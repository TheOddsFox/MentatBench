"""Command-line entry point: python -m mentat_bench {build,seal,evaluate,score,fixtures}.

Exit codes: 0 ok; 2 usage or I/O (bad arguments, missing inputs); 3 build incomplete or aborted (adapter failure in
the group or the relation phase, nothing sealed; base theory not verified, with the work dir kept for a later
finalize); 4 seal refused (an incomplete build, marked by INCOMPLETE.json, unless --allow-incomplete; or a post-seal
change detected by evaluate); 5 mb-engine refused the submission, or the base theory is inconsistent (invalid submission),
or a budget overrun; 6 profile or usage validation refused before the engine ran (evaluate); 7 infrastructure
failure (engine missing, not executable, crashed, or produced output that does not match the queries).

evaluate discards any earlier answers.jsonl and verdicts.jsonl in --out before it does anything else, and writes
new ones only when the whole run succeeds, so a refused run leaves no outputs for score to pick up. evaluate warns on
stderr when the submission was built with the oracle or gold_view adapter (it read gold; the score is a diagnostic),
and when the sealed submission is an incomplete build (INCOMPLETE.json); its output then has "incomplete": true.

build --gold DIR is read only by the oracle and gold_view adapters, and only for recall metrics (gold_pairs,
gold_groups): the summary's blocking_recall and group_recall. With another adapter --gold is ignored with a warning.
The group phase has a budget of its own (--group-budget-frac, --max-group-calls): when it is reached the group phase
stops (summary group_budget_stopped) and the relation phase runs; the global budget stops are unchanged.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

from . import engine, fixtures_gen, ir, scoring
from .adapters.base import AdapterFatal
from .adapters.local_jev import LocalJSONLAdapter
from .adapters.mock import EmptyAdapter, GoldViewAdapter, OracleAdapter
from .gateway import UsageError, usage_totals, validate_usage
from .harness import fixed
from .manifest import ProfileError, load_profile
from .seal import INCOMPLETE_FILE, SealError, ensure_build_id, incomplete_reason, seal, verify_ledger, verify_seal

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPO_ROOT / "fixtures" / "toy10k"
EXIT_USAGE = 2
EXIT_BUILD_INCOMPLETE = 3
EXIT_SEAL = 4
EXIT_ENGINE = 5
EXIT_VALIDATE = 6
EXIT_INFRA = 7
ANSWERS, VERDICTS = "answers.jsonl", "verdicts.jsonl"
# The build CLI defaults to K=64; the library default fixed.TOP_K is 128.
BUILD_TOP_K = 64


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {text!r}")
    return value


def _group_members(text: str) -> int:
    value = _positive_int(text)
    if value > fixed.GROUP_MAX_MEMBERS_LIMIT:
        raise argparse.ArgumentTypeError(f"at most {fixed.GROUP_MAX_MEMBERS_LIMIT} outcomes per group, got {text!r}")
    return value


def _fraction(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and 0 < value <= 1):
        raise argparse.ArgumentTypeError(f"expected a number in (0, 1], got {text!r}")
    return value


def _adapter_warning(submission: Path) -> str | None:
    """A warning for a submission built with the oracle or gold_view adapter, which read gold."""
    try:
        policy = json.loads((submission / "build_policy.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(policy, dict) and policy.get("adapter") in ("oracle", "gold_view"):
        return (f"warning: {submission} was built with the {policy['adapter']} adapter, which reads gold; "
                "its score is a diagnostic, not a model result")
    return None


def _group_sources(text: str) -> tuple[str, ...]:
    names = tuple(part.strip() for part in text.split(","))
    if any(name not in fixed.GROUP_SOURCES for name in names):
        raise argparse.ArgumentTypeError(f"expected a comma list of {', '.join(fixed.GROUP_SOURCES)}, got {text!r}")
    return names


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError(f"expected a finite positive number, got {text!r}")
    return value


def _remove_tree(path: Path) -> None:
    """Best-effort removal, tolerant of OSError. A failed build can still hold its log handles in a reference
    cycle, which Windows will not delete through, so a second attempt follows a garbage collection. Read-only
    files (sealed copies) are made writable first."""
    def writable(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=writable)
    if path.exists():
        gc.collect()
        shutil.rmtree(path, onerror=writable)


def _is_within(path: Path, root: Path) -> bool:
    p, r = path.resolve(), root.resolve()
    return p == r or r in p.parents


def _checkpoint_top_k(work: Path) -> int | None:
    """The top_k the checkpoint in `work` was built with (its policy), or None when there is no such value."""
    try:
        policy = json.loads((work / "checkpoint.json").read_text(encoding="utf-8")).get("policy")
    except (OSError, ValueError, AttributeError):
        return None
    value = policy.get("top_k") if isinstance(policy, dict) else None  # a malformed policy: the build reports it
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _has_checkpoint(work: Path) -> bool:
    # existence only: an unreadable or corrupt checkpoint still holds paid decisions, so the work dir is never removed for it
    return (work / "checkpoint.json").is_file()


def _discard_build_output(out: Path, created: bool) -> None:
    """Removes what a failed build wrote to `out`: the directory itself when this build created it, otherwise its
    contents (an existing directory was empty when the build started), so that a later finalize can write there."""
    if created:
        _remove_tree(out)
    elif out.is_dir():
        for child in out.iterdir():
            if child.is_dir() and not child.is_symlink():
                _remove_tree(child)
            else:
                child.unlink(missing_ok=True)


def _mark_incomplete(out: Path, summary: dict[str, Any]) -> None:
    """Writes the INCOMPLETE.json marker of an adapter_fatal build into its out dir: seal refuses the directory."""
    phase = "group" if summary.get("groups_fatal") else "relation"
    reason = f"adapter_fatal: the adapter failed in the {phase} phase"
    keys = ("adapter_fatal", "groups_fatal", "budget_stopped", "outcomes", "candidate_pairs", "relations_committed",
            "groups_committed", "constraints", "calls", "spend_usd")
    ir.write_json(out / INCOMPLETE_FILE, {"reason": reason, "summary": {k: summary[k] for k in keys if k in summary}})


def _corpus_records(args: argparse.Namespace) -> Iterator[dict]:
    """Streams corpus records from JSONL; nothing is materialised here."""
    for r in ir.read_jsonl(Path(args.corpus)):
        rec = {"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r.get("event_title")}
        if r.get("context") is not None:  # optional rule card: left out of the record (and the corpus digest) when absent
            rec["context"] = r["context"]
        yield rec


def _make_adapter(args: argparse.Namespace) -> Any:
    if args.adapter == "oracle":
        return OracleAdapter(args.gold)
    if args.adapter == "gold_view":
        return GoldViewAdapter(args.gold)
    if args.adapter == "empty":
        return EmptyAdapter()
    return LocalJSONLAdapter(args.local_arg, timeout_s=args.local_timeout, protocol=args.local_protocol,
                             hello_timeout_s=args.hello_timeout)


HELLO_HINTS = {  # what a user can do about each way the protocol 2 hello can fail (groups_disabled_reason)
    "hello_timeout": "the child did not answer the hello in time; raise --hello-timeout",
    "hello_error": "the child answered the hello with an error; fix the child (a longer --hello-timeout does not help)",
    "hello_failed": "the child's hello reply was malformed or the child died; fix the child (a longer --hello-timeout does not help)",
    "no_decide_group": "the child's hello does not advertise decide_group; use a child that makes group decisions "
                       "(a longer --hello-timeout does not help)",
}


def _partial_reasons_of(submission: Path) -> list[str]:
    """Reasons from the submission's PARTIAL.json (a complete build that stopped at a cap or budget); [] when absent."""
    path = submission / fixed.PARTIAL_FILE
    if not path.is_file():
        return []
    try:
        return list(ir.read_json(path)["reasons"])
    except (OSError, ValueError, KeyError, TypeError):
        return ["unreadable_partial_marker"]


def _resume_command(args: argparse.Namespace) -> str:
    """The build command that resumes this one, with the drop caps doubled (replacing any given values)."""
    caps = {"--max-relation-drops": str(args.max_relation_drops * 2), "--max-group-drops": str(args.max_group_drops * 2)}
    tokens, raw, i = [], list(getattr(args, "raw_argv", [])), 0
    while i < len(raw):
        name = raw[i].split("=", 1)[0]
        if name == "--resume":
            i += 1
            continue
        if name in caps:
            i += 1 if "=" in raw[i] else 2
            continue
        tokens.append(raw[i])
        i += 1
    for name, value in caps.items():
        tokens += [name, value]
    return "python -m mentat_bench " + " ".join(shlex.quote(t) for t in [*tokens, "--resume"])


def cmd_build(args: argparse.Namespace) -> int:
    out = Path(args.out)
    work = out.with_name(out.name + ".build")
    if out.exists() and not out.is_dir():
        print(f"{out} is a file; --out must be a new or empty directory", file=sys.stderr)
        return EXIT_USAGE
    if out.is_dir() and any(out.iterdir()):
        print(f"{out} is not empty; builds are write-once, choose a new output directory", file=sys.stderr)
        return EXIT_USAGE
    if work.exists() and (not work.is_dir() or (any(work.iterdir()) and not args.resume)):
        print(f"{work} is a leftover build directory; --resume continues it, or remove it or choose another --out",
              file=sys.stderr)
        return EXIT_USAGE
    kept_top_k = _checkpoint_top_k(work) if args.resume else None  # a resume continues the checkpoint's blocking
    if args.top_k is not None and kept_top_k is not None and args.top_k != kept_top_k:
        print(f"build refused: --top-k {args.top_k} differs from the top_k {kept_top_k} of the checkpoint in {work}; "
              f"resume with --top-k {kept_top_k}, or without --top-k to use {kept_top_k}", file=sys.stderr)
        return EXIT_USAGE
    if args.top_k is None:  # a fresh build takes the default; a resume without --top-k adopts the checkpoint's value
        args.top_k = kept_top_k if kept_top_k is not None else BUILD_TOP_K
    if args.adapter in ("oracle", "gold_view") and not args.gold:
        print(f"--adapter {args.adapter} needs --gold DIR", file=sys.stderr)
        return EXIT_USAGE
    if args.adapter in ("oracle", "gold_view") and not Path(args.gold).is_dir():
        print(f"--gold {args.gold} is not a directory", file=sys.stderr)
        return EXIT_USAGE
    if args.adapter == "local" and not args.local_arg:
        print("--adapter local needs one or more --local-arg values (use --local-arg=-x for dash-leading values)", file=sys.stderr)
        return EXIT_USAGE
    if not Path(args.corpus).is_file():
        print(f"corpus file not found: {args.corpus}", file=sys.stderr)
        return EXIT_USAGE
    try:
        profile = load_profile(args.profile)
    except ProfileError as exc:
        print(f"invalid profile: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.candidates:  # checked in full (schema, corpus ids, not under gold/) before any child starts
        if args.adapter == "oracle":
            print("build refused: --candidates does not apply to the oracle (a full replay asks no question)", file=sys.stderr)
            return EXIT_USAGE
        if args.complement_dedup:
            print("build refused: --complement-dedup does not apply to --candidates (the curated pairs are asked as they "
                  "are)", file=sys.stderr)
            return EXIT_USAGE
        try:
            fixed.load_candidates(args.candidates, {r["id"] for r in _corpus_records(args)})
        except (OSError, ValueError) as exc:
            print(f"build refused: --candidates: {exc}", file=sys.stderr)
            return EXIT_USAGE
    outcomes = None  # a fresh build's binding calls (one per outcome) must fit under --max-calls: refused before any child starts
    if args.max_calls is not None and not args.resume:
        try:
            outcomes = sum(1 for _ in _corpus_records(args))
            fixed.check_max_calls(args.max_calls, outcomes)
        except (OSError, ValueError) as exc:
            print(f"build refused: {exc}", file=sys.stderr)
            return EXIT_USAGE
    try:
        adapter = _make_adapter(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot start the adapter: {exc}", file=sys.stderr)
        return EXIT_USAGE
    # A protocol 2 child whose hello did not succeed builds without groups: an error unless --allow-groups-off.
    reason = getattr(adapter, "groups_disabled_reason", None)
    if isinstance(adapter, LocalJSONLAdapter) and args.local_protocol == 2 and not args.no_groups and reason:
        hint = HELLO_HINTS.get(reason, "fix the child")
        message = f"groups are off because the child's hello failed ({reason}): {hint}"
        if not args.allow_groups_off:
            adapter.close()
            print(f"build refused: {message}. Pass --allow-groups-off to build without groups.", file=sys.stderr)
            return EXIT_BUILD_INCOMPLETE
        print(f"warning: {message}", file=sys.stderr)

    gold_pairs = gold_groups = None  # recall metrics only, and only for the adapters that are given gold
    if args.gold and args.adapter in ("oracle", "gold_view"):
        try:
            theory = ir.read_json(Path(args.gold) / "theory.json")
        except (OSError, ValueError) as exc:
            print(f"cannot read the gold theory for the recall metrics: {exc}", file=sys.stderr)
            return EXIT_USAGE
        gold_pairs, gold_groups = fixed.gold_pairs_from_theory(theory), fixed.gold_groups_from_theory(theory)
    elif args.gold:
        print(f"warning: --gold is read only by the oracle and gold_view adapters; ignored for --adapter {args.adapter}",
              file=sys.stderr)
    created = not out.exists()
    work_created = not work.exists() or not any(work.iterdir())  # an empty dir that was there counts as created here
    code = EXIT_USAGE
    keep_out = False  # adapter_fatal: the finalized latest checkpoint is kept in --out, not sealed
    keep_work = False  # base check incomplete, or OSError after a checkpoint: the work dir keeps it for a later finalize
    try:
        summary = fixed.build(
            _corpus_records(args), adapter, out, profile, checkpoint_every=args.checkpoint_every, max_df=args.max_df,
            max_consecutive_errors=args.max_consecutive_errors, max_relation_drops=args.max_relation_drops,
            use_groups=not args.no_groups, group_sources=args.group_sources, group_max_members=args.group_max_members,
            group_budget_frac=args.group_budget_frac, max_group_calls=args.max_group_calls,
            gold_pairs=gold_pairs, gold_groups=gold_groups, batch_size=args.batch_size, top_k=args.top_k,
            complement_dedup=args.complement_dedup is not False, complement_fallback=args.complement_fallback,
            pair_order=args.pair_order, max_pairs=args.max_pairs, max_calls=args.max_calls, outcomes=outcomes,
            max_relation_drop_frac=args.max_relation_drop_frac, resume=args.resume,
            max_group_drops=args.max_group_drops, max_group_drop_frac=args.max_group_drop_frac,
            keep_work=args.keep_work, admit=not args.no_admit, admit_max_literals=args.admit_max_literals,
            relation_floor=args.relation_floor, repair_budget_s=args.repair_budget_s, candidates=args.candidates,
        )
        if summary.get("adapter_fatal"):
            # build() already removed its work dir; the finalized checkpoint in `out` is kept for inspection.
            _mark_incomplete(out, summary)
            print(json.dumps(summary, indent=2, allow_nan=False, default=str))
            print(f"build aborted: the adapter failed in the group or the relation phase. The latest checkpoint is in "
                  f"{out} but was not sealed; seal refuses it ({INCOMPLETE_FILE}).", file=sys.stderr)
            code, keep_out = EXIT_BUILD_INCOMPLETE, True
        else:
            ensure_build_id(out)  # a capped or budget-stopped build is marked partial by finalize (PARTIAL.json, hashed into the seal)
            code = 0
    except FileExistsError as exc:  # another build holds the out or work dir: this one wrote nothing, so it removes nothing
        print(str(exc), file=sys.stderr)
        keep_out = keep_work = True
    except fixed.BuildIncomplete as exc:
        print(f"build incomplete, no checkpoint to seal: {exc}", file=sys.stderr)
        code = EXIT_BUILD_INCOMPLETE
    except fixed.RepairBudgetStopped as exc:  # the repair budget ran out; the deadline also, when deadline_passed says so
        later = ""
        if exc.deadline_passed:  # the build deadline has passed too: the profile's deadline must grow as well
            later = " The build deadline has also passed: finalize with a profile whose build_deadline_s is larger."
        print(f"build incomplete, the post-hoc repair ran past its budget: {exc}. Nothing was written; the checkpoint is kept "
              f"in {work}. Finalize it with the Python call fixed.finalize_checkpoint({str(work)!r}, {str(out)!r}, profile, "
              f"repair_budget_s=<more seconds>); build --resume refuses a changed repair_budget_s.{later}", file=sys.stderr)
        code, keep_work = EXIT_BUILD_INCOMPLETE, True
    except fixed.BaseCheckIncomplete as exc:
        print(f"build incomplete, base theory not verified: {exc}. Nothing was written; the checkpoint is kept in "
              f"{work}. Finalize it later with fixed.finalize_checkpoint({str(work)!r}, {str(out)!r}, profile) and a "
              "profile whose build_deadline_s is larger.", file=sys.stderr)
        code, keep_work = EXIT_BUILD_INCOMPLETE, True
    except fixed.BaseInconsistentError as exc:
        print(f"build refused, the base theory is inconsistent (invalid submission): {exc}", file=sys.stderr)
        code = EXIT_ENGINE
        if _has_checkpoint(work):  # the model's decisions are kept: a refused repair is resumed with higher caps
            keep_work = True
            print(f"the decisions are kept in {work}. Resume with higher caps:\n  {_resume_command(args)}", file=sys.stderr)
    except AdapterFatal as exc:
        print(f"build aborted, the adapter failed repeatedly: {exc}", file=sys.stderr)
        code = EXIT_BUILD_INCOMPLETE
    except engine.EngineInfraError as exc:
        print(f"build aborted, mb-engine unavailable: {exc}", file=sys.stderr)
        code = EXIT_INFRA
    except engine.EngineError as exc:
        print(f"build aborted, mb-engine refused an input: {exc}", file=sys.stderr)
        code = EXIT_ENGINE
    except fixed.BindingCountMismatch as exc:  # paid calls were made and the build is incomplete: its work dir is kept
        print(f"build failed: {exc}; the work dir {work} is kept with {fixed.paid_calls(work)} paid calls", file=sys.stderr)
        code, keep_work = EXIT_BUILD_INCOMPLETE, True
    except ValueError as exc:
        print(f"build refused: {exc}", file=sys.stderr)
        code = EXIT_USAGE
    except fixed.CorruptCheckpoint as exc:  # the checkpoint of a resume is unreadable: a failed build, the work dir is kept
        print(f"build failed: {exc}; the work dir {work} is kept", file=sys.stderr)
        code, keep_work = EXIT_BUILD_INCOMPLETE, True
    except RuntimeError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        code = EXIT_BUILD_INCOMPLETE
    except OSError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        code = EXIT_BUILD_INCOMPLETE
        if _has_checkpoint(work):  # e.g. a checkpoint write that another process blocked: resume, do not restart
            keep_work = True
            print(f"the checkpoint in {work} is kept: rerun the same command with --resume to continue from it (a batch "
                  "that was not committed is asked again, and paid again)", file=sys.stderr)
    except BaseException:  # interrupted (Ctrl-C, a crash): the checkpoint is kept so that --resume can continue
        if _has_checkpoint(work):
            keep_work = True
            print(f"build interrupted; the checkpoint in {work} is kept: rerun the same command with --resume",
                  file=sys.stderr)
        raise
    finally:
        if isinstance(adapter, LocalJSONLAdapter):
            adapter.close()
            if adapter.exit_error is not None:  # a child that fails at its end (a digest refusal, a crash) fails the build
                print(f"build failed: the local child {adapter.exit_error}", file=sys.stderr)
                code = code or EXIT_BUILD_INCOMPLETE
        if code != 0:
            # a checkpoint holds model decisions that cost money: it is kept on every failure, and --resume continues it.
            # A work dir without one is removed only when this invocation created it (an empty dir that was already there counts as
            # created: H2's rule); a resume's dir may hold decisions
            if not keep_work and not _has_checkpoint(work) and work_created and fixed.paid_calls(work) == 0:
                _remove_tree(work)  # a work dir with paid calls is kept (its usage log is the record of what was paid)
            if not keep_out:
                _discard_build_output(out, created)
    if code != 0:
        return code
    print(json.dumps(summary, indent=2, allow_nan=False, default=str))
    return 0


def cmd_seal(args: argparse.Namespace) -> int:
    try:
        record = seal(args.submission, allow_incomplete=args.allow_incomplete)
    except (SealError, OSError) as exc:  # an OS error (a ledger or directory that cannot be used) is a seal failure too
        print(str(exc), file=sys.stderr)
        return EXIT_SEAL
    print(json.dumps({"digest": record["digest"], "files": sorted(record["files"])}, indent=2, allow_nan=False))
    return 0


def _discard_outputs(out: Path) -> None:
    """Removes this command's outputs from --out, tolerant of OSError: a failed or refused run leaves none."""
    for name in (ANSWERS, VERDICTS):
        try:
            (out / name).unlink(missing_ok=True)
        except OSError as exc:
            print(f"warning: could not remove stale {out / name}: {exc}", file=sys.stderr)


def cmd_evaluate(args: argparse.Namespace) -> int:
    submission, out = Path(args.submission), Path(args.out)
    gold = Path(args.gold)
    if _is_within(out, submission) or _is_within(out, gold):
        print("--out must not be inside --submission or --gold", file=sys.stderr)
        return EXIT_USAGE
    if out.exists() and not out.is_dir():
        print(f"--out {out} is a file", file=sys.stderr)
        return EXIT_USAGE
    _discard_outputs(out)  # from here on, every path that does not publish leaves --out without outputs
    try:
        code = _evaluate(args, submission, out, gold)
    except BaseException:
        _discard_outputs(out)
        raise
    if code != 0:
        _discard_outputs(out)
    return code


def _evaluate(args: argparse.Namespace, submission: Path, out: Path, gold: Path) -> int:
    missing = [flag for flag, value in (("--profile", args.profile), ("--expected-digest", args.expected_digest)) if not value]
    if missing:
        print(f"evaluate needs {' and '.join(missing)} (required: the pinned profile and the seal digest printed by `seal`)",
              file=sys.stderr)
        return EXIT_USAGE
    queries, profile_path = Path(args.queries), Path(args.profile)
    warning = _adapter_warning(submission)
    if warning:
        print(warning, file=sys.stderr)
    if not gold.is_dir():
        print(f"--gold {gold} is not a directory", file=sys.stderr)
        return EXIT_USAGE
    if not queries.is_file():
        print(f"--queries {queries} is not a file", file=sys.stderr)
        return EXIT_USAGE
    if not profile_path.is_file():
        print(f"--profile {profile_path} is not a file", file=sys.stderr)
        return EXIT_USAGE
    try:
        pinned = load_profile(profile_path)
    except ProfileError as exc:
        print(f"refusing to evaluate: invalid profile: {exc}", file=sys.stderr)
        return EXIT_VALIDATE
    # The certificate cap is part of the pinned profile: check gets it by flag, and a gold profile that sets a
    # different cap (or one, against a pinned profile that omits it) is refused rather than silently obeyed.
    cap = pinned.get("max_certificate_refs")
    if (gold / "profile.json").is_file():
        try:
            gold_cap = load_profile(gold / "profile.json").get("max_certificate_refs")
        except ProfileError as exc:
            print(f"refusing to evaluate: invalid gold profile: {exc}", file=sys.stderr)
            return EXIT_VALIDATE
        if gold_cap is not None and gold_cap != cap:
            print(f"refusing to evaluate: the pinned max_certificate_refs ({cap}) differs from {gold / 'profile.json'} "
                  f"({gold_cap})", file=sys.stderr)
            return EXIT_VALIDATE
    try:
        record = verify_seal(submission, expected_digest=args.expected_digest, action="evaluate")
    except SealError as exc:
        print(f"refusing to evaluate: {exc}", file=sys.stderr)
        return EXIT_SEAL
    if getattr(args, "ledger", None):
        try:
            verify_ledger(submission, args.ledger, record["digest"])
        except SealError as exc:
            print(f"refusing to evaluate: {exc}", file=sys.stderr)
            return EXIT_SEAL
    if not (submission / "profile.json").is_file():
        print(f"refusing to evaluate: {submission} has no profile.json", file=sys.stderr)
        return EXIT_VALIDATE
    incomplete = incomplete_reason(submission)  # sealed with the submission, so verified by verify_seal
    if incomplete is not None:
        print(f"warning: {submission} is an incomplete build ({incomplete}); its score is for a partial theory",
              file=sys.stderr)
    partial = _partial_reasons_of(submission)  # sealed too: a capped or budget-stopped theory
    if partial:
        print(f"warning: {submission} is a partial build ({', '.join(partial)}): its theory stopped at a cap or budget",
              file=sys.stderr)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        # Everything below works on a copy that is verified against the pinned digest, so the engine never reads
        # a directory that changes under it; the original must still verify after the run (see post-run check).
        copy = tmp / "submission"
        try:
            shutil.copytree(submission, copy)
        except OSError as exc:
            print(f"cannot copy the submission for evaluation: {exc}", file=sys.stderr)
            return EXIT_INFRA
        try:
            verify_seal(copy, expected_digest=record["digest"], action="evaluate")
        except SealError as exc:
            print(f"refusing to evaluate: the submission changed while it was copied: {exc}", file=sys.stderr)
            return EXIT_SEAL
        copy_profile = copy / "profile.json"
        try:
            if load_profile(copy_profile) != pinned:
                print(f"refusing to evaluate: {submission / 'profile.json'} differs from --profile {profile_path}", file=sys.stderr)
                return EXIT_VALIDATE
            validate_usage(ir.read_json(copy / "usage.json"), pinned)
        except (ProfileError, UsageError, OSError, ValueError) as exc:
            print(f"refusing to evaluate: {exc}", file=sys.stderr)
            return EXIT_VALIDATE
        try:
            engine.validate(copy, copy_profile)
            budget = engine.engine_budget_s(queries, pinned["query_timeout_s"])
        except engine.EngineInfraError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_INFRA
        except engine.EngineError as exc:
            print(f"refusing to evaluate: mb-engine validate refused the submission:\n{exc}", file=sys.stderr)
            return EXIT_ENGINE
        except (OSError, ValueError) as exc:
            print(f"cannot start evaluation: {exc}", file=sys.stderr)
            return EXIT_USAGE

        answers, verdicts = tmp / ANSWERS, tmp / VERDICTS
        engine.eval_submission(copy, queries, answers, timeout_s=budget)
        engine.check(gold, queries, answers, verdicts, timeout_s=budget, max_certificate_refs=cap)
        try:
            verify_seal(submission, expected_digest=record["digest"], action="evaluate")
        except SealError as exc:
            print(f"refusing to publish: the submission changed during the run: {exc}", file=sys.stderr)
            return EXIT_SEAL
        out.mkdir(parents=True, exist_ok=True)
        os.replace(answers, out / ANSWERS)
        try:
            os.replace(verdicts, out / VERDICTS)
        except OSError:
            (out / ANSWERS).unlink(missing_ok=True)  # never leave answers without verdicts
            raise
    except engine.EngineError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INFRA if isinstance(exc, engine.EngineInfraError) else EXIT_ENGINE
    except OSError as exc:
        print(f"cannot write the run directory {out}: {exc}", file=sys.stderr)
        return EXIT_INFRA
    finally:
        _remove_tree(tmp)
    result = {"digest": record["digest"], "answers": str(out / ANSWERS), "verdicts": str(out / VERDICTS)}
    result["certificate_cap"] = cap  # null: the pinned profile sets no cap (unlimited)
    if incomplete is not None:
        result["incomplete"] = True
    if partial:
        result["partial"] = partial
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    """Streams: queries are read one at a time; answers and gold are looked up by byte offset, so only
    one answer is parsed at once. Verdicts are one boolean per query. A missing answers file scores every query
    0 (no answers); a missing verdicts file means no certificates. --gold-bindings adds class weights (the
    `weighted` block); --design adds the anchor and active blocks. Both are opt-in."""
    if args.baseline and (args.answers or args.verdicts):
        print("--baseline computes its own answers; do not pass --answers or --verdicts", file=sys.stderr)
        return EXIT_USAGE
    if not args.baseline and not (args.answers and args.verdicts):
        print("score needs --answers and --verdicts, or --baseline NAME", file=sys.stderr)
        return EXIT_USAGE
    required = [(args.queries, "queries"), (args.gold_answers, "gold answers")]
    if args.gold_bindings:
        required.append((args.gold_bindings, "gold bindings"))
    if args.design:
        required.append((args.design, "design"))
    try:
        for path, what in required:
            if not Path(path).is_file():
                print(f"{what} file not found: {path}", file=sys.stderr)
                return EXIT_USAGE
        gold = ir.JsonlIndex(Path(args.gold_answers))
        weights = scoring.ClassWeights.from_bindings(args.gold_bindings) if args.gold_bindings else None
        design = ir.read_jsonl(Path(args.design)) if args.design else None
        if args.baseline:
            queries = list(ir.read_jsonl(Path(args.queries)))  # baselines need their counts up front
            answers = scoring.BaselineAnswers(args.baseline, queries, gold)  # baselines' answers stay lazy
            verdicts: dict[str, bool] = {}
        else:
            queries = ir.read_jsonl(Path(args.queries))  # streamed
            answers = ir.JsonlIndex(Path(args.answers)) if Path(args.answers).is_file() else {}
            if not Path(args.answers).is_file():
                print(f"no answers file at {args.answers}: every query scores 0", file=sys.stderr)
            verdicts = {}
            if Path(args.verdicts).is_file():
                verdicts = scoring.read_verdicts(Path(args.verdicts))
            else:
                print(f"no verdicts file at {args.verdicts}: no certificates", file=sys.stderr)
        report = scoring.score_run(queries, answers, gold, verdicts, weights=weights, design=design)
        report["baseline"] = args.baseline
        report["baseline_gold_informed"] = args.baseline in scoring.GOLD_INFORMED_BASELINES
        if args.usage:
            report["usage"] = usage_totals(ir.read_json(Path(args.usage)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot score: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


def cmd_fixtures_gen(args: argparse.Namespace) -> int:
    summary = fixtures_gen.generate(Path(args.out), seed=args.seed, seasons=args.seasons, name=args.name)
    print(json.dumps(summary, indent=2, allow_nan=False, default=str))
    return 0


def cmd_fixtures_gold(args: argparse.Namespace) -> int:
    """Full gold step (validate, eval, check, verdicts and premises) through fixtures_gen.finalize, which writes
    gold/answers.jsonl and gold/verdicts.jsonl. Eval alone would leave verdicts and premises stale."""
    fixture = Path(args.fixture)
    if not (fixture / "queries.jsonl").is_file():
        print(f"{fixture} has no queries.jsonl; generate the fixture first", file=sys.stderr)
        return EXIT_USAGE
    try:
        summary = fixtures_gen.finalize(fixture)
    except engine.EngineError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INFRA if isinstance(exc, engine.EngineInfraError) else EXIT_ENGINE
    except OSError as exc:
        print(f"cannot read the fixture: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps(summary, indent=2, allow_nan=False, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mentat_bench",
        epilog="exit codes: 0 ok; 2 usage/IO; 3 build incomplete or aborted; 4 seal refused; 5 engine refused "
               "the submission or overran its budget; 6 profile or usage validation refused; 7 infrastructure failure",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a submission directory (metered; output must be new or empty)")
    b.add_argument("--adapter", choices=["oracle", "gold_view", "empty", "local"], required=True)
    b.add_argument("--corpus", required=True, help="corpus.jsonl with id,text,label,event_title (and context, optional)")
    b.add_argument("--gold", help="gold submission directory: read by the oracle and gold_view adapters, and by the "
                                  "recall metrics (blocking_recall, group_recall) of those builds only")
    b.add_argument(
        "--local-arg", action="append", default=[],
        help="argv element for the local adapter (repeatable; write --local-arg=-x for values starting with a dash)",
    )
    b.add_argument("--local-timeout", type=_positive_float, default=60.0, help="seconds per local request before the child is killed")
    b.add_argument("--hello-timeout", type=_positive_float, default=900.0,
                   help="seconds for the protocol 2 hello (a child may load its model before it answers)")
    b.add_argument("--allow-groups-off", action="store_true",
                   help="with --local-protocol 2, build without groups when the hello fails (otherwise that is exit 3)")
    b.add_argument("--out", required=True)
    b.add_argument("--profile", help="resource profile JSON; defaults to manifest.DEFAULT_PROFILE")
    b.add_argument("--checkpoint-every", type=_positive_int, default=fixed.CHECKPOINT_EVERY)
    b.add_argument("--max-df", type=_positive_int, default=fixed.MAX_DF, help="skip blocking tokens with more postings than this")
    b.add_argument("--max-consecutive-errors", type=_positive_int, default=fixed.MAX_CONSECUTIVE_ERRORS,
                   help="adapter failures in a row before the build is aborted")
    b.add_argument("--max-relation-drop-frac", type=_fraction, default=fixed.MAX_RELATION_DROP_FRAC,
                   help="the most relations the base repair may drop, as a share of the relations asked (the contradiction "
                        "rate); above it the build is refused (exit 5)")
    b.add_argument("--resume", action="store_true",
                   help="continue a killed or interrupted build from its kept work dir (<out>.build); the policy flags must match")
    b.add_argument("--keep-work", action="store_true",
                   help="keep <out>.build after a successful build, with checkpoints.jsonl (every checkpoint), so that "
                        "python -m mentat_bench.ceilings can derive lower-ceiling cells")
    b.add_argument("--max-group-drops", type=_positive_int, default=fixed.MAX_GROUP_DROPS_DEFAULT,
                   help="group drops the base repair may make; above it the build is refused (exit 5). Not part of the "
                        "resume policy: it may change on --resume")
    b.add_argument("--max-group-drop-frac", type=_fraction, default=fixed.MAX_GROUP_DROP_FRAC,
                   help="the most group drops as a share of the committed groups (the group contradiction rate); above it "
                        "the build is refused (exit 5)")
    b.add_argument("--max-relation-drops", type=_positive_int, default=fixed.MAX_RELATION_DROPS,
                   help="relation constraints the build may drop to restore a consistent base")
    b.add_argument("--local-protocol", type=int, choices=[1, 2], default=1,
                   help="local child protocol; 2 asks the hello handshake and enables group decisions")
    b.add_argument("--no-groups", action="store_true", help="make no group decisions (pairwise only)")
    b.add_argument("--group-budget-frac", type=_fraction, default=fixed.GROUP_BUDGET_FRAC,
                   help="share (0, 1] of the spend ceiling and of the deadline left at the start of the group phase; "
                        "reaching it stops the group phase only (group_budget_stopped)")
    b.add_argument("--max-group-calls", type=_positive_int, default=fixed.MAX_GROUP_CALLS,
                   help="candidate calls the group phase may make (the group phase stops there)")
    b.add_argument("--group-max-members", type=_group_members, default=fixed.GROUP_MAX_MEMBERS,
                   help="largest candidate group (outcomes) a group decision may name")
    b.add_argument("--group-sources", type=_group_sources, default=fixed.DEFAULT_GROUP_SOURCES,
                   help=f"comma list of candidate sources from {', '.join(fixed.GROUP_SOURCES)} (s4 is opt-in)")
    b.add_argument("--batch-size", type=_positive_int, default=fixed.BATCH_SIZE,
                   help="decisions per batch call, capped by the child's advertised max_batch (protocol 2 children only)")
    b.add_argument("--top-k", type=_positive_int, default=None,
                   help=f"blocking partner markets per market (default {BUILD_TOP_K}; with --resume, the checkpoint's value; "
                        "python -m mentat_bench.harness.topk_sweep reports recall by K)")
    b.add_argument("--complement-dedup", action=argparse.BooleanOptionalAction, default=None,
                   help="ask one representative pair of each complement pair of binary markets; derive the other three (default "
                        "on; off with --candidates)")
    b.add_argument("--candidates", help="a curated candidates.jsonl (public, not under gold/): its relation questions are asked "
                                        "in file order as (a, b) instead of blocking, its group questions are the only group "
                                        "candidates; gold-informed, recorded in build_policy.json")
    b.add_argument("--complement-fallback", choices=fixed.COMPLEMENT_FALLBACKS, default="all",
                   help="when a complement representative has no named answer: ask all three other pairs (all, the default), "
                        "only the both-flipped one (flipped), or none; the pairs not asked are counted (dedup_fallback_skipped)")
    b.add_argument("--pair-order", choices=fixed.PAIR_ORDERS, default="index",
                   help="order the candidate pairs are asked in: market index (default) or blocking score, highest first, so a "
                        "budget, deadline or --max-pairs stop cuts the weakest pairs")
    b.add_argument("--max-pairs", type=_positive_int, default=None, help="candidate pairs the relation phase may cover (pairs_capped)")
    b.add_argument("--max-calls", type=_positive_int, default=None,
                   help="hard cap on the decision items the build makes, bindings included: a fresh build is refused below its outcome "
                        "count, retries never exceed the cap (binding retries leave the items of the bindings not yet asked), "
                        "and resumed calls made after the last checkpoint count; the group and relation phases stop at it (calls_capped)")
    b.add_argument("--no-admit", action="store_true",
                   help="repair the base after the build (post-hoc) instead of admitting relations in confidence order")
    b.add_argument("--admit-max-literals", type=_positive_int, default=fixed.ADMIT_MAX_LITERALS,
                   help="admission runs when 2 x outcomes is at most this (its bit matrix: 2 GB at the default 131072, about "
                        "3x that at peak); above it the post-hoc repair runs")
    b.add_argument("--relation-floor", type=float, default=None,
                   help="admission drops a relation whose child-sent conf is below this (relations without conf are not floored)")
    b.add_argument("--repair-budget-s", type=float, default=None,
                   help="seconds the post-hoc repair may run, checked between repair passes; when it runs out the build fails "
                        "(exit 3, nothing written, the checkpoint kept). The repair runs only when admission is refused or does not apply, or in the engine fallback")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("seal", help="seal a submission directory (write-once; prints the digest)")
    s.add_argument("--submission", required=True)
    s.add_argument("--allow-incomplete", action="store_true",
                   help=f"seal a build marked incomplete ({INCOMPLETE_FILE}); evaluate then warns and reports it")
    s.set_defaults(func=cmd_seal)

    e = sub.add_parser("evaluate", help="verify seal, validate, run mb-engine eval then check against gold", allow_abbrev=False)
    # Not argparse-required, so that a refusal can discard earlier outputs first (see cmd_evaluate). Both are
    # required: evaluate refuses with exit 2 when either is missing.
    e.add_argument("--submission", required=True)
    e.add_argument("--expected-digest", help="required: the seal digest printed by `seal`; refuse unless it matches")
    e.add_argument("--profile", help="required: pinned resource profile; submission/profile.json must equal it")
    e.add_argument("--ledger", help="central seal ledger: the sealed digest must also match this build's entry there")
    e.add_argument("--queries", required=True)
    e.add_argument("--gold", required=True, help="gold submission directory")
    e.add_argument(
        "--out", required=True,
        help="run directory. Every evaluate run first deletes any earlier answers.jsonl and verdicts.jsonl here; "
             "new ones appear only when the whole run succeeds. A refused or failed run leaves none, so its "
             "universe scores 0 (a missing answers file scores 0 in score)",
    )
    e.set_defaults(func=cmd_evaluate)

    sc = sub.add_parser("score", help="print the JSON score report")
    sc.add_argument("--queries", required=True)
    sc.add_argument("--gold-answers", required=True)
    sc.add_argument("--answers", help="answers.jsonl; a missing file scores every query 0")
    sc.add_argument("--verdicts", help="verdicts.jsonl; a missing file means no certificates")
    sc.add_argument("--baseline", choices=scoring.BASELINES)
    sc.add_argument("--usage", help="usage.json of the submission, for usage totals")
    sc.add_argument("--gold-bindings", help="gold/bindings.jsonl: adds the weighted block (class weights, global)")
    sc.add_argument("--design", help="gold/query_design.jsonl: adds the anchor and active blocks")
    sc.set_defaults(func=cmd_score)

    f = sub.add_parser("fixtures", help="controlled fixtures")
    fs = f.add_subparsers(dest="fixture_cmd", required=True)
    fg = fs.add_parser("gen", help="generate the toy10k fixture (no engine needed)")
    fg.add_argument("--out", default=str(DEFAULT_FIXTURE))
    fg.add_argument("--seed", type=int, default=fixtures_gen.SEED)
    fg.add_argument("--seasons", type=_positive_int, default=fixtures_gen.BASE_SEASONS, help="4 = toy10k, 40 = toy100k, 400 = toy1m")
    fg.add_argument("--name", default="toy10k")
    fg.set_defaults(func=cmd_fixtures_gen)
    fgold = fs.add_parser("gold", help="full gold step with mb-engine: answers, verdicts and premises (needs the engine)")
    fgold.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    fgold.set_defaults(func=cmd_fixtures_gold)
    return parser


def _option_value(argv: list[str], name: str) -> str | None:
    """Value of `name` (`--name VALUE` or `--name=VALUE`, last one wins), as argparse reads it; None when absent."""
    value = None
    for i, token in enumerate(argv):
        if token == name and i + 1 < len(argv):
            value = argv[i + 1]
        elif token.startswith(name + "="):
            value = token[len(name) + 1:]
    return value


def _discard_before_parsing(argv: list[str]) -> None:
    """Best effort, before argparse runs: an `evaluate` invocation that argparse refuses must not leave an earlier
    run's answers and verdicts in --out. Skipped when --out is inside --submission or --gold (never touch those)."""
    if not argv or argv[0] != "evaluate":
        return
    out = _option_value(argv, "--out")
    if not out:
        return
    try:
        for root in (_option_value(argv, "--submission"), _option_value(argv, "--gold")):
            if root and _is_within(Path(out), Path(root)):
                return
        _discard_outputs(Path(out))
    except Exception:  # noqa: BLE001 - parsing must still report its own error
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    _discard_before_parsing(argv)
    args = build_parser().parse_args(argv)
    args.raw_argv = list(argv)  # the command as given, for the resume hint of a refused build
    return args.func(args)

"""One matrix cell: build -> seal -> evaluate -> score, into a new run directory.

  python -m mentat_bench.runcell --fixture fixtures/mentat_hard_dev --adapter local --local-arg python --local-arg child.py \
      --out runs/2026-10-18-child --profile profiles/toy-local-v0.json

A dash-leading --local-arg value needs the form --local-arg=VALUE (for example --local-arg=-m).

Layout of --out (created by this command; it must be new or empty):
  submission/          the build (sealed): theory, bindings, provenance, usage.json, build_policy.json, profile.json
  submission.build/    the build's work dir, kept (build --keep-work) with checkpoints.jsonl, the logs and pairs.bin (about
                       8 bytes a pair). python -m mentat_bench.ceilings --work <out>/submission.build derives lower-ceiling
                       cells from it. --resume continues it.
  fixture_stamp.json   the fixture's stamp when the cell started: {name, version, status, content_hash} for a registered
                       version, else {error, content_hash} (the fingerprint). --resume refuses a fixture whose content_hash
                       differs or is missing on either side. A cell that predates this file resumes with a warning. A fixture
                       that cannot be hashed (links, unreadable files) is refused before any build.
  build_summary.json   the build's summary (blocking_recall, group_recall, items, pairs_derived, ...); absent when the build failed
  answers.jsonl, verdicts.jsonl   evaluate's outputs
  score.json           the score report (Q_N, families, weighted and anchor/active blocks, usage totals)
  cell.json            what ran: fixture, adapter, arguments, profile, the steps' exit codes, status (complete,
                       budget_stopped, pairs_capped, calls_capped, group_budget_stopped, or incomplete when any step
                       failed), failed_step (the first step with a non-zero exit code, else null; build_exit then holds the
                       build's exit code), step_seconds (the wall seconds of each step that ran), partial and incomplete
                       (from the build and from evaluate; incomplete is true when a step failed), groups_disabled_reason, top_k and
                       batch_size (the resolved build knobs, from build_policy.json), evaluate (the JSON evaluate printed,
                       or null), and runtime (the run-time facts a run card needs: engine, profile and fixture hashes, repo
                       commit and dirty flag, GPUs with index, UUID and CUDA_VISIBLE_DEVICES, versions; see
                       runcard.capture_facts)
--gold-bindings and --design come from the fixture's gold directory, so the weighted and anchor/active blocks are present.
The fixture's gold is read only for oracle and gold_view builds (and by evaluate and score, as they always do). The
exit code is that of the first step that fails (2 usage, 3 build, 4 seal, 5 engine, 6 validation, 7 infrastructure).

A committed fixture needs corpus.jsonl, queries.jsonl and gold/answers.jsonl, checked before the build. The score step
also reads gold/bindings.jsonl and gold/query_design.jsonl (always passed), so a fixture without them fails at score and its
cell is incomplete. The registry
(fixtures/VERSIONS.json) is read once, before any build: a missing, unreadable or malformed registry is refused with exit 2,
so no cell records an unregistered stamp for a registered fixture.

--extra-queries (opt-in) also evaluates queries_extra.jsonl on the same sealed submission, into extra/ (answers.jsonl,
verdicts.jsonl), and scores it against gold/answers_extra.jsonl into score_extra.json. cell.json records it under
extra_queries and the steps evaluate_extra and score_extra; extra_status is ok, failed (its exit code is non-zero and
report shows it) or not_run (the main score failed first). query_files lists queries_extra.jsonl once the extra pass
starts: answers.jsonl covers queries.jsonl and extra/answers.jsonl covers the extra file, and `corrections rescore`
re-scores every file named there. Cells written before that list only queries.jsonl; rescore still finds extra/.

--candidates FILE (a curated candidates.jsonl, build flag) builds from the file's questions; after the score step,
python -m mentat_bench.decision_scoring scores submission.build/decisions.jsonl against gold/suite_labels.jsonl (and
--fixture, the fixture directory whose version picks the headline) into score_decisions.json (step score_decisions). cell.json records candidates and decision_scoring: ok, failed, or
not_importable (the module is not installed; the step is skipped). A cell whose decisions were not scored (failed or
not_importable) is incomplete.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import cli, corrections, runcard
from .harness import fixed
from .seal import seal

REPO = Path(__file__).resolve().parents[2]
EXIT_USAGE = 2
GOLD_READING = ("oracle", "gold_view")
EXTRA_QUERIES = corrections.EXTRA_QUERIES  # the second query file of a fixture (--extra-queries)
FIXTURE_STAMP_FILE = "fixture_stamp.json"  # written at the start of a cell; --resume checks the fixture against it
DECISION_SCORING = "mentat_bench.decision_scoring"  # scores decisions.jsonl of a --candidates cell (score_decisions.json)


def _captured(argv: list[str]) -> tuple[int, str]:
    """Runs one CLI command, returning (exit code, its stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def _timed(seconds: dict[str, float], step: str, argv: list[str]) -> tuple[int, str]:
    """Runs one CLI command like _captured and records its wall seconds under `step`."""
    t0 = time.monotonic()
    code, text = _captured(argv)
    seconds[step] = round(time.monotonic() - t0, 3)
    return code, text


def _json_or_none(text: str) -> dict | None:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _fixture_stamp(fixture: Path, hashes: dict[str, str], registry: list[dict]) -> dict:
    """The registered version the fixture's files match ({name, version, status, content_hash}). A fixture with none
    gets {error, content_hash}: the reason, and its content hash (the fingerprint --resume compares, registered or not)."""
    try:
        return corrections.stamp_for(fixture, registry, hashes=hashes)
    except corrections.CorrectionError as exc:
        return {"error": str(exc), "content_hash": corrections.content_hash(hashes)}


def _refusal(out: Path, resume: bool, kept: Path) -> str:
    """Why an existing run directory cannot take this cell, named by its state."""
    sub = out / "submission"
    if not out.is_dir():
        return f"{out} is not a directory; a cell needs a new run directory"
    if sub.exists() and (out / "cell.json").is_file():
        return f"{out} is a finished cell (cell.json and {sub} exist): a cell needs a new run directory"
    if sub.exists():  # published by build, then killed before cell.json: build is never resumed after publishing
        sealed = (sub / "seal.json").is_file()
        return (f"{out} holds a build that was published but not finished ({sub}, {'sealed' if sealed else 'not sealed'}): "
                + ("evaluate and score it by hand, or " if sealed else
                   f"seal it (python -m mentat_bench seal --submission {sub}) and evaluate and score it by hand, or ")
                + "choose a new run directory")
    if resume:
        return (f"nothing to resume in {out}: no checkpoint in {kept.parent} (the build stopped before its first checkpoint); "
                "remove it or choose a new run directory")
    message = f"{out} exists and is not empty; a cell needs a new run directory"
    if kept.is_file():
        message += f" (a killed build's checkpoint is in {kept.parent}: rerun with --resume)"
    return message


def run_cell(
    fixture: Path, adapter: str, local_args: list[str], out: Path, profile: Path, build_flags: list[str],
    resume: bool = False, extra_queries: bool = False, candidates: Path | None = None,
) -> int:
    missing = [rel for rel in corrections.REQUIRED if not (fixture / rel).is_file()]
    if missing:
        print(f"{fixture} needs {', '.join(corrections.REQUIRED)} (missing: {', '.join(missing)})", file=sys.stderr)
        return EXIT_USAGE
    if extra_queries and not ((fixture / EXTRA_QUERIES).is_file() and (fixture / "gold" / "answers_extra.jsonl").is_file()):
        print(f"--extra-queries needs {EXTRA_QUERIES} and gold/answers_extra.jsonl in {fixture}", file=sys.stderr)
        return EXIT_USAGE
    try:  # read once per cell: every stamp of this cell is checked against this snapshot of the registry
        registry = corrections.read_registry(corrections.REGISTRY)
    except corrections.CorrectionError as exc:  # refused before any build: no stamp is recorded against a registry it cannot read
        print(f"no cell is run, the registry cannot be used: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:  # hashed once per cell: the stamp, the fingerprint and the run card's file hashes all come from this
        hashes = corrections.content_hashes(fixture, registry)
    except (corrections.CorrectionError, OSError) as exc:  # refused before any build: no fingerprint, no resume
        print(f"{fixture} cannot be fingerprinted, so no cell is run on it: {exc}", file=sys.stderr)
        return EXIT_USAGE
    kept = out / "submission.build" / "checkpoint.json"  # --resume continues the build kept in this run directory
    # a finished build (submission/ exists) is not resumed: its work dir is kept now, and build would refuse the non-empty out
    resumable = resume and kept.is_file() and not (out / "submission").exists()
    if out.exists() and (not out.is_dir() or (any(out.iterdir()) and not resumable)):
        print(_refusal(out, resume, kept), file=sys.stderr)
        return EXIT_USAGE
    if resume:
        build_flags = [*build_flags, "--resume"]
    if candidates is not None:
        build_flags = [*build_flags, "--candidates", str(candidates)]
    gold, queries, sub = fixture / "gold", fixture / "queries.jsonl", out / "submission"
    out.mkdir(parents=True, exist_ok=True)
    current = _fixture_stamp(fixture, hashes, registry)
    if resume and (out / FIXTURE_STAMP_FILE).is_file():  # the interrupted cell's fixture is the one it started on
        recorded = _json_or_none((out / FIXTURE_STAMP_FILE).read_text(encoding="utf-8")) or {}
        if recorded.get("content_hash") != current["content_hash"]:  # a recorded stamp without a fingerprint is refused too
            print(f"--resume refused: {fixture} is not the fixture {out} started on (recorded {recorded}, now {current})",
                  file=sys.stderr)
            return EXIT_USAGE
    elif resume:
        print(f"warning: {out} records no fixture stamp (started before one was recorded): the fixture is not checked",
              file=sys.stderr)
    else:
        (out / FIXTURE_STAMP_FILE).write_text(json.dumps(current, sort_keys=True) + "\n", encoding="utf-8")
    steps: dict[str, int] = {}
    step_seconds: dict[str, float] = {}  # timing only: the wall seconds of each step that ran
    cell = {"fixture": fixture.name, "fixture_path": str(fixture), "adapter": adapter, "local_arg": local_args,
            "profile": str(profile), "build_flags": build_flags, "steps": steps, "step_seconds": step_seconds, "evaluate": None,
            "runtime": runcard.capture_facts(fixture, profile, files=hashes),  # run-time facts for the run card (before the build)
            "fixture_stamp": current, "query_files": ["queries.jsonl"],  # the files evaluate and score read
            "extra_queries": {"query_file": EXTRA_QUERIES, "evaluate": None} if extra_queries else None,
            "extra_status": "not_run" if extra_queries else None}  # ok | failed | not_run (set when the extra pass ends)

    def finish(code: int) -> int:
        cell["failed_step"] = next((step for step, rc in steps.items() if rc != 0), None)  # the first step that failed
        if cell["failed_step"] is not None:  # a failed step, whatever the build's status, makes the cell incomplete
            cell.update(status="incomplete", incomplete=True)
        (out / "cell.json").write_text(json.dumps(cell, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return code

    build = ["build", "--adapter", adapter, "--corpus", str(fixture / "corpus.jsonl"), "--out", str(sub),
             "--profile", str(profile), "--keep-work", *build_flags]  # <out>/submission.build keeps the checkpoints (ceilings)
    if adapter in GOLD_READING:
        build += ["--gold", str(gold)]
    build += [f"--local-arg={arg}" for arg in local_args]
    steps["build"], text = _timed(step_seconds, "build", build)
    if steps["build"] != 0:  # a failed build has no summary: the cell is incomplete
        cell.update(status="incomplete", build_exit=steps["build"], partial=[], incomplete=True)
        return finish(steps["build"])
    summary = json.loads(text)
    # status: complete, budget_stopped, pairs_capped, calls_capped, group_budget_stopped or incomplete (report.py shows it)
    cell.update(status=fixed.build_status(summary), partial=fixed.partial_reasons(summary),
                groups_disabled_reason=summary.get("groups_disabled_reason"), incomplete=False,
                relations_asked=summary.get("relations_asked"), relations_dropped=summary.get("relations_dropped"),
                contradiction_rate=summary.get("contradiction_rate"), resumed=summary.get("resumed", False),
                base_repaired=summary.get("base_repaired"), groups_dropped=summary.get("groups_dropped"),
                groups_dropped_pct=summary.get("groups_dropped_pct"),
                prior_calls=summary.get("prior_calls", 0))
    policy = _build_policy(sub)  # the resolved knobs: cells built under different defaults stay comparable
    cell.update(top_k=policy.get("top_k"), batch_size=policy.get("batch_size"))
    (out / "build_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")

    t0 = time.monotonic()
    try:
        digest = seal(sub)["digest"]
    except Exception as exc:  # noqa: BLE001 - reported as the seal step's failure
        step_seconds["seal"] = round(time.monotonic() - t0, 3)
        print(f"seal failed: {exc}", file=sys.stderr)
        steps["seal"] = cli.EXIT_SEAL
        return finish(steps["seal"])
    step_seconds["seal"] = round(time.monotonic() - t0, 3)
    steps["seal"] = 0

    steps["evaluate"], text = _timed(step_seconds, "evaluate", [
        "evaluate", "--submission", str(sub), "--expected-digest", digest, "--profile", str(profile),
        "--queries", str(queries), "--gold", str(gold), "--out", str(out),
    ])
    evaluated = _json_or_none(text)  # evaluate prints its JSON only on success
    cell["evaluate"] = evaluated
    if evaluated is not None:  # surfaced here so a run directory shows them without the evaluate output
        if "partial" in evaluated:
            cell["partial"] = evaluated["partial"]
        cell["incomplete"] = cell["incomplete"] or bool(evaluated.get("incomplete"))
    if steps["evaluate"] != 0:
        return finish(steps["evaluate"])

    steps["score"], text = _timed(step_seconds, "score", [
        "score", "--queries", str(queries), "--gold-answers", str(gold / "answers.jsonl"),
        "--answers", str(out / "answers.jsonl"), "--verdicts", str(out / "verdicts.jsonl"),
        "--usage", str(sub / "usage.json"), "--gold-bindings", str(gold / "bindings.jsonl"),
        "--design", str(gold / "query_design.jsonl"),
    ])
    if steps["score"] == 0:
        scored = _json_or_none(text)  # the fixture stamp travels with the score (rescore checks it against cell.json)
        if scored is not None:
            scored["fixture_stamp"] = cell["fixture_stamp"]
        text = json.dumps(scored, indent=2, sort_keys=True) + "\n" if scored is not None else text
        (out / "score.json").write_text(text, encoding="utf-8")
    code = steps["score"]
    if candidates is not None:
        cell["candidates"] = str(candidates)
        rc = _score_decisions(fixture, candidates, out, cell, steps, step_seconds)
        code = code or rc
    if extra_queries and code == 0:
        code = _extra_pass(fixture, gold, sub, out, profile, digest, cell, steps, step_seconds)
        cell["extra_status"] = "ok" if code == 0 else "failed"  # a failed pass is shown in report and exits non-zero
    return finish(code)


def _score_decisions(fixture: Path, candidates: Path, out: Path, cell: dict, steps: dict[str, int],
                     step_seconds: dict[str, float]) -> int:
    """Scores the build's raw decision log against gold/suite_labels.jsonl into score_decisions.json, when
    mentat_bench.decision_scoring is importable (else the step is skipped and cell.json says so)."""
    if importlib.util.find_spec(DECISION_SCORING) is None:
        cell.update(decision_scoring="not_importable", status="incomplete", incomplete=True)  # the suite was not scored
        return 0
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1]),
                                                                      os.environ.get("PYTHONPATH")]))}
    t0 = time.monotonic()
    steps["score_decisions"] = subprocess.run(
        [sys.executable, "-m", DECISION_SCORING, "--decisions", str(out / "submission.build" / "decisions.jsonl"),
         "--labels", str(fixture / "gold" / "suite_labels.jsonl"), "--candidates", str(candidates), "--fixture", str(fixture),
         "--out", str(out / "score_decisions.json")], env=env, check=False).returncode
    step_seconds["score_decisions"] = round(time.monotonic() - t0, 3)
    cell["decision_scoring"] = "ok" if steps["score_decisions"] == 0 else "failed"
    return steps["score_decisions"]


def _build_policy(sub: Path) -> dict:
    """The build's policy (build_policy.json, the knobs it ran with); empty when the build wrote none."""
    try:
        policy = json.loads((sub / "build_policy.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return policy if isinstance(policy, dict) else {}


def _extra_pass(fixture: Path, gold: Path, sub: Path, out: Path, profile: Path, digest: str, cell: dict,
                steps: dict[str, int], step_seconds: dict[str, float]) -> int:
    """Evaluates queries_extra.jsonl on the sealed submission (into out/extra) and scores it against
    gold/answers_extra.jsonl into score_extra.json, with the same fixture stamp as score.json."""
    extra_dir = out / "extra"
    cell["query_files"].append(EXTRA_QUERIES)  # the file this cell used, from here on (also when the pass fails)
    steps["evaluate_extra"], text = _timed(step_seconds, "evaluate_extra", [
        "evaluate", "--submission", str(sub), "--expected-digest", digest, "--profile", str(profile),
        "--queries", str(fixture / EXTRA_QUERIES), "--gold", str(gold), "--out", str(extra_dir),
    ])
    cell["extra_queries"]["evaluate"] = _json_or_none(text)
    if steps["evaluate_extra"] != 0:
        return steps["evaluate_extra"]
    steps["score_extra"], text = _timed(step_seconds, "score_extra", [
        "score", "--queries", str(fixture / EXTRA_QUERIES), "--gold-answers", str(gold / "answers_extra.jsonl"),
        "--answers", str(extra_dir / "answers.jsonl"), "--verdicts", str(extra_dir / "verdicts.jsonl"),
        "--gold-bindings", str(gold / "bindings.jsonl"),
    ])
    if steps["score_extra"] == 0:
        scored = _json_or_none(text)
        if scored is not None:
            scored["fixture_stamp"] = cell["fixture_stamp"]
            (out / "score_extra.json").write_text(json.dumps(scored, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return steps["score_extra"]


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    for k, token in enumerate(raw[:-1]):  # argparse reads "--local-arg -m" as an option: say how to pass it
        if token == "--local-arg" and raw[k + 1].startswith("-"):
            print(f"runcell: a dash-leading --local-arg value needs the form --local-arg={raw[k + 1]}", file=sys.stderr)
            return EXIT_USAGE
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.runcell", description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", required=True, type=Path, help="fixture directory (corpus.jsonl, queries.jsonl, gold/)")
    parser.add_argument("--adapter", required=True, choices=["oracle", "gold_view", "empty", "local"])
    parser.add_argument("--local-arg", action="append", default=[],
                        help="argv element of the local child (repeatable; write --local-arg=-m for a dash-leading value)")
    parser.add_argument("--out", required=True, type=Path, help="new run directory")
    parser.add_argument("--profile", type=Path, default=REPO / "profiles" / "toy-local-v0.json")
    parser.add_argument("--local-protocol", type=int, choices=[1, 2], default=2, help="local child protocol (default 2)")
    parser.add_argument("--batch-size", type=int, help="decisions per batch call (build flag)")
    parser.add_argument("--top-k", type=int, help="blocking partner markets per market (build flag)")
    parser.add_argument("--max-pairs", type=int, help="candidate pairs the relation phase may cover (build flag)")
    parser.add_argument("--max-calls", type=int, help="decision items of the group and relation phases (build flag)")
    parser.add_argument("--no-groups", action="store_true", help="make no group decisions (build flag)")
    parser.add_argument("--no-complement-dedup", action="store_true", help="ask every complement pair (build flag)")
    parser.add_argument("--complement-fallback", choices=fixed.COMPLEMENT_FALLBACKS,
                        help="what a complement representative with no named answer asks: all, flipped or none (build flag)")
    parser.add_argument("--pair-order", choices=fixed.PAIR_ORDERS, help="candidate pair order: index or score (build flag)")
    parser.add_argument("--local-timeout", type=float, help="seconds per local request (build flag)")
    parser.add_argument("--hello-timeout", type=float, help="seconds for the protocol 2 hello (build flag)")
    parser.add_argument("--allow-groups-off", action="store_true",
                        help="build without groups when the protocol 2 hello fails (build flag)")
    parser.add_argument("--max-relation-drops", type=int, help="relation drops the base repair may make (build flag)")
    parser.add_argument("--max-relation-drop-frac", type=float, help="contradiction-rate cap of the base repair (build flag)")
    parser.add_argument("--max-group-drops", type=int, help="group drops the base repair may make (build flag)")
    parser.add_argument("--max-group-drop-frac", type=float, help="group contradiction-rate cap of the base repair (build flag)")
    parser.add_argument("--no-admit", action="store_true",
                        help="repair the base after the build instead of admitting relations (build flag)")
    parser.add_argument("--admit-max-literals", type=cli._positive_int, help="admission's literal cap (build flag)")
    parser.add_argument("--relation-floor", type=float, help="admission drops relations with a conf below this (build flag)")
    parser.add_argument("--repair-budget-s", type=float, help="seconds the post-hoc repair may run (build flag)")
    parser.add_argument("--resume", action="store_true",
                        help="continue the build a killed run left in <out>/submission.build (build flag)")
    parser.add_argument("--candidates", type=Path,
                        help="curated candidates.jsonl: build from its questions and score the decision log (build flag)")
    parser.add_argument("--extra-queries", action="store_true",
                        help=f"also evaluate and score {EXTRA_QUERIES} (gold/answers_extra.jsonl) into score_extra.json")
    args = parser.parse_args(argv)
    flags = ["--local-protocol", str(args.local_protocol)]
    for name, value in (("--batch-size", args.batch_size), ("--top-k", args.top_k), ("--max-pairs", args.max_pairs),
                        ("--max-calls", args.max_calls), ("--local-timeout", args.local_timeout),
                        ("--hello-timeout", args.hello_timeout), ("--max-relation-drops", args.max_relation_drops),
                        ("--max-relation-drop-frac", args.max_relation_drop_frac),
                        ("--max-group-drops", args.max_group_drops), ("--max-group-drop-frac", args.max_group_drop_frac),
                        ("--admit-max-literals", args.admit_max_literals), ("--relation-floor", args.relation_floor),
                        ("--repair-budget-s", args.repair_budget_s), ("--complement-fallback", args.complement_fallback),
                        ("--pair-order", args.pair_order)):
        if value is not None:
            flags += [name, str(value)]
    if args.no_groups:
        flags.append("--no-groups")
    if args.allow_groups_off:
        flags.append("--allow-groups-off")
    if args.no_complement_dedup:
        flags.append("--no-complement-dedup")
    if args.no_admit:
        flags.append("--no-admit")
    return run_cell(args.fixture, args.adapter, args.local_arg, args.out, args.profile, flags, resume=args.resume,
                    extra_queries=args.extra_queries, candidates=args.candidates)


if __name__ == "__main__":
    raise SystemExit(main())

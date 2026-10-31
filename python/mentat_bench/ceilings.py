"""Lower-ceiling submissions from one kept build, without asking the model again.

  python -m mentat_bench.ceilings --work DIR --metric gpu_seconds --ceilings 60,300,1200 \
      --fixture DIR --profile PROFILE --out OUTDIR [--build-profile PROFILE]

DIR is the work dir of a build made with `build --keep-work` (<out>.build). Each checkpoint of that build is a line of
checkpoints.jsonl (its state: the byte lengths of the logs it commits, and the wall time). The cumulative usage at each
checkpoint is read from usage_calls.jsonl up to its usage_bytes (committed usage). For each ceiling the latest checkpoint
whose cumulative metric is <= the ceiling is finalized (on a COPY of the work dir cut to that checkpoint), sealed,
evaluated and scored as the cell OUTDIR/<metric>_<ceiling>/. Each cell is partial (PARTIAL.json and cell.json name the
ceiling). A ceiling below the first checkpoint has no submission: its run scores zero (spec s9). The cell also records
what was BILLED: the usage after the checkpoint, up to and including the first call that takes the metric past the
ceiling (its decision is discarded, its cost is charged). Metrics: spend_usd, gpu_seconds, wall_seconds, items. No
quality-per-cost composite is computed.

The build's profile must be matched: the profile.json beside its submission (the work dir is <out>.build), or
--build-profile when that file is absent. Builds of the oracle (full replay) are refused: their theory is not in the
checkpoints. Exit codes: 0 every cell scored; 2 usage (arguments, not a kept build, a profile that differs or cannot be
found, a fixture that differs from the one the build ran on, duplicate ceilings, a full-replay build); 3 at least one
cell could not be finalized, sealed, evaluated or scored (its cell.json says incomplete).
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

from . import cli, report, runcell
from .harness import fixed
from .ir import write_json
from .manifest import load_profile
from .runcard import sha256_file
from .seal import seal

EXIT_USAGE = 2
EXIT_INCOMPLETE = 3
METRICS = ("spend_usd", "gpu_seconds", "wall_seconds", "items")
SCORE_FILE = "score.json"
REPAIR_CAPS = ("max_relation_drops", "max_relation_drop_frac", "max_group_drops", "max_group_drop_frac")
FULL_REPLAY = "full_replay"  # the groups_disabled_reason of an adapter with extra_theory (the oracle)


def history(work: Path) -> list[dict[str, Any]]:
    """Every checkpoint of a kept build, in order. ValueError when the work dir was not kept."""
    path = work / fixed.HISTORY_FILE
    if not path.is_file():
        raise ValueError(f"{work} has no {fixed.HISTORY_FILE}: only a build made with --keep-work can be derived")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError(f"{path} records no checkpoint")
    return rows


def cumulative(work: Path, checkpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Committed usage at each checkpoint: calls, items, spend_usd, gpu_seconds and wall_seconds. Calls are summed in
    their log order, as the gateway sums its spend. ValueError when a checkpoint does not end on a usage line."""
    totals, calls, items, spend, gpu, pos = [], 0, 0, 0.0, 0.0, 0
    with open(work / "usage_calls.jsonl", "rb") as fh:
        for ckpt in checkpoints:
            target = int(ckpt["usage_bytes"])
            if target < pos:
                raise ValueError("checkpoints are out of order: usage_bytes decreases")
            while pos < target:
                line = fh.readline()
                if not line:
                    raise ValueError("usage_calls.jsonl is shorter than the checkpoints say")
                pos += len(line)
                if pos > target:
                    raise ValueError("a checkpoint does not end on a usage line")
                call = json.loads(line)
                calls += 1
                items += call.get("items", 1)
                spend += call["cost_usd"]
                gpu += call.get("gpu_seconds") or 0
            totals.append({"calls": calls, "items": items, "spend_usd": spend, "gpu_seconds": gpu,
                           "wall_seconds": float(ckpt["wall_seconds"])})
    return totals


def billed(work: Path, ckpt: dict[str, Any], base: dict[str, Any], metric: str, ceiling: float) -> dict[str, Any]:
    """What the build was charged up to its stop at this ceiling: the committed usage (`base`), then the usage lines after
    the checkpoint up to and including the first one that takes `metric` past the ceiling. Wall time is the checkpoint's
    plus the call seconds of those lines (the gateway's own clock is not in usage.json)."""
    calls, items, spend, gpu, seconds = base["calls"], base["items"], base["spend_usd"], base["gpu_seconds"], 0.0
    with open(work / "usage_calls.jsonl", "rb") as fh:
        fh.seek(int(ckpt["usage_bytes"]))
        for line in fh:
            call = json.loads(line)
            calls += 1
            items += call.get("items", 1)
            spend += call["cost_usd"]
            gpu += call.get("gpu_seconds") or 0
            seconds += call.get("seconds") or 0
            current = {"spend_usd": spend, "gpu_seconds": gpu, "wall_seconds": base["wall_seconds"] + seconds,
                       "items": items}[metric]
            if current > ceiling:
                break
    return {"billed_calls": calls, "billed_items": items, "billed_spend_usd": round(spend, 6),
            "billed_gpu_seconds": round(gpu, 6), "billed_wall_seconds": round(base["wall_seconds"] + seconds, 3)}


def pick(totals: list[dict[str, Any]], metric: str, ceiling: float) -> int | None:
    """Index of the latest checkpoint whose cumulative metric is <= ceiling (None: no checkpoint is within it)."""
    chosen = None
    for index, row in enumerate(totals):
        if row[metric] <= ceiling:
            chosen = index
    return chosen


def _label(text: str) -> str:
    return text.replace(".", "p")


def _cell_dir(out: Path, metric: str, text: str) -> Path:
    return out / f"{metric}_{_label(text)}"


def _profile_check(work: Path, profile: dict[str, Any], build_profile: Path | None) -> str:
    """Matches --profile with the build's profile: the profile.json beside its submission (the work dir is <out>.build),
    else --build-profile. Returns which one matched; ValueError when neither can be found or they differ."""
    sibling = work.with_name(work.name[: -len(".build")]) / "profile.json" if work.name.endswith(".build") else None
    if sibling is not None and sibling.is_file():
        path, how = sibling, "profile.json beside the build"
    elif build_profile is not None:
        path, how = build_profile, "--build-profile"
    else:
        raise ValueError("the build's profile cannot be found: the work dir has no profile.json beside its submission "
                         "(it must be named <out>.build) and no --build-profile was given")
    if load_profile(path) != profile:
        raise ValueError(f"--profile differs from the build's profile ({how}: {path})")
    if build_profile is not None and load_profile(build_profile) != profile:
        raise ValueError(f"--profile differs from --build-profile ({build_profile})")
    return how


def _optional_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _corpus_check(work: Path, fixture: Path) -> str:
    """Compares the fixture's corpus.jsonl with the one the build ran on, when cell.json beside the build records it.
    Returns "matched" or "not recorded" (a warning); ValueError when they differ."""
    cell = _optional_json(work.parent / "cell.json")
    recorded = (((cell or {}).get("runtime") or {}).get("fixture") or {}).get("files", {}).get("corpus.jsonl")
    if recorded is None:
        print("warning: no corpus.jsonl hash is recorded for this build (cell.json beside it): not compared",
              file=sys.stderr)
        return "not recorded"
    if recorded != sha256_file(fixture / "corpus.jsonl"):
        raise ValueError("the fixture's corpus.jsonl differs from the one this build ran on (cell.json)")
    return "matched"


def _repair_caps(ckpt: dict[str, Any]) -> dict[str, Any]:
    """The base-repair caps and admission knobs the build ran with (its policy, resolved as finalize resolves them): the
    cut must repair exactly as the build did."""
    policy = ckpt.get("policy") or {}
    return {**{key: policy[key] for key in REPAIR_CAPS if key in policy}, **fixed._admission_knobs(policy)}


def _cut_copy(work: Path, scratch: Path, ckpt: dict[str, Any]) -> Path:
    """A copy of the work dir whose logs end at the checkpoint, with the checkpoint as its checkpoint.json."""
    copy = scratch / "work"
    shutil.copytree(work, copy, ignore=shutil.ignore_patterns(fixed.HISTORY_FILE))
    fixed._truncate_logs(copy, ckpt)
    write_json(copy / "checkpoint.json", ckpt, indent=None)
    return copy


def _finish(cell: dict[str, Any], cell_dir: Path, scratch: Path, code: int) -> dict[str, Any]:
    shutil.rmtree(scratch, ignore_errors=True)
    cell_dir.mkdir(parents=True, exist_ok=True)
    cell["exit"] = code
    write_json(cell_dir / "cell.json", cell)
    return cell


def derive_cell(work: Path, checkpoints: list[dict[str, Any]], totals: list[dict[str, Any]], metric: str, text: str,
                fixture: Path, profile_path: Path, profile: dict[str, Any], out: Path, how: str) -> dict[str, Any]:
    """Finalize, seal, evaluate and score the cell of one ceiling. Returns its cell.json content (with `exit`)."""
    cell_dir = _cell_dir(out, metric, text)
    sub = cell_dir / "submission"
    scratch = out / f".scratch_{metric}_{_label(text)}"
    cell: dict[str, Any] = {
        "fixture": fixture.name, "fixture_path": str(fixture), "profile": str(profile_path),
        "profile_matched_by": how, "derived_from": str(work), "partial": [f"ceiling_{metric}"],
        "ceiling": {"metric": metric, "limit": float(text), "text": text}, "steps": {}, "evaluate": None,
        "incomplete": False,
    }
    index = pick(totals, metric, float(text))
    if index is None:
        cell.update(status="no_checkpoint_within_ceiling", score_policy="zero: no checkpoint within the ceiling (spec s9)")
        return _finish(cell, cell_dir, scratch, 0)
    ckpt, total = checkpoints[index], totals[index]
    reached_end = index == len(checkpoints) - 1
    cell["ceiling"].update(checkpoint=index, of=len(checkpoints), value=total[metric], usage=total,
                           reached_end=reached_end, **billed(work, ckpt, total, metric, float(text)))
    cell["adapter"] = (ckpt.get("policy") or {}).get("adapter")
    cell["status"] = "ceiling_reached_end" if reached_end else "ceiling_stopped"
    try:
        summary = fixed.finalize_checkpoint(_cut_copy(work, scratch, ckpt), sub, profile, **_repair_caps(ckpt))
    except Exception as exc:  # noqa: BLE001 - the cell records why it has no submission
        print(f"{metric} <= {text}: cannot finalize checkpoint {index}: {exc}", file=sys.stderr)
        shutil.rmtree(sub, ignore_errors=True)  # no submission from a failed finalize (finalize removes its own writes too)
        cell.update(status="incomplete", incomplete=True, error=str(exc), steps={"derive": 3})
        return _finish(cell, cell_dir, scratch, EXIT_INCOMPLETE)
    shutil.rmtree(scratch, ignore_errors=True)
    reasons = [f"ceiling_{metric}"] + [r for r in fixed.partial_reasons(summary) if r != f"ceiling_{metric}"]
    write_json(sub / fixed.PARTIAL_FILE, {"partial": True, "reasons": reasons})
    # not build_summary.json: report.py would show the build's own status ("complete") for a partial cell
    write_json(cell_dir / "ceiling_summary.json", {**summary, "ceiling": cell["ceiling"]})
    if fixed.build_status(summary) == "incomplete":
        cli._mark_incomplete(sub, summary)  # as build does: an adapter-fatal checkpoint is not sealed (seal refuses it)
    cell.update(partial=reasons, build_status=fixed.build_status(summary), base_repaired=summary.get("base_repaired"),
                incomplete=fixed.build_status(summary) == "incomplete", steps={"derive": 0})
    try:
        digest = seal(sub)["digest"]
    except Exception as exc:  # noqa: BLE001 - reported as the seal step's failure
        print(f"{metric} <= {text}: seal failed: {exc}", file=sys.stderr)
        cell.update(status="incomplete", incomplete=True, error=str(exc))
        cell["steps"]["seal"] = cli.EXIT_SEAL
        return _finish(cell, cell_dir, scratch, EXIT_INCOMPLETE)
    cell["steps"]["seal"] = 0
    queries, gold = fixture / "queries.jsonl", fixture / "gold"
    code, text_out = runcell._captured([  # the same evaluate and score steps as runcell
        "evaluate", "--submission", str(sub), "--expected-digest", digest, "--profile", str(profile_path),
        "--queries", str(queries), "--gold", str(gold), "--out", str(cell_dir),
    ])
    evaluated = runcell._json_or_none(text_out)
    cell["evaluate"] = evaluated
    cell["steps"]["evaluate"] = code
    if evaluated is not None and evaluated.get("incomplete"):
        cell["incomplete"] = True
    if code == 0:
        code, text_out = runcell._captured([
            "score", "--queries", str(queries), "--gold-answers", str(gold / "answers.jsonl"),
            "--answers", str(cell_dir / "answers.jsonl"), "--verdicts", str(cell_dir / "verdicts.jsonl"),
            "--usage", str(sub / "usage.json"), "--gold-bindings", str(gold / "bindings.jsonl"),
            "--design", str(gold / "query_design.jsonl"),
        ])
        if code == 0:
            (cell_dir / SCORE_FILE).write_text(text_out, encoding="utf-8")
    cell["steps"]["score"] = code
    if code != 0:
        cell.update(status="incomplete", incomplete=True)
    return _finish(cell, cell_dir, scratch, code)


def _cell_row(cell_dir: Path, cell: dict[str, Any]) -> dict[str, Any]:
    """The table row of a cell: its scores and usage (report.run_row), with the billed usage of its ceiling. A ceiling
    below every checkpoint scores zero."""
    row = report.run_row(cell_dir)
    ceiling = cell["ceiling"]
    row["ceiling"] = f"{ceiling['metric']} <= {ceiling['text']}"
    row["status"] = cell["status"]
    row["checkpoint"] = f"{ceiling['checkpoint'] + 1} of {ceiling['of']}" if "checkpoint" in ceiling else "-"
    row["billed_cost_usd"] = ceiling.get("billed_spend_usd")
    row["billed_gpu_seconds"] = ceiling.get("billed_gpu_seconds")
    if cell["status"] == "no_checkpoint_within_ceiling":
        row["Q_N"], row["strict_Q_N"] = 0.0, 0.0
    return row


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _table(rows: list[dict[str, Any]]) -> str:
    head = ("| ceiling | status | checkpoint | Q_N | strict Q_N | exact | false_ent | false_ref | committed USD | "
            "committed gpu s | billed USD | billed gpu s | wall s | items | calls |")
    lines = [head, "|" + "---|" * 16]
    for row in rows:
        keys = ("ceiling", "status", "checkpoint", "Q_N", "strict_Q_N", "exact_query_success", "false_entailment",
                "false_refutation", "cost_usd", "gpu_seconds", "billed_cost_usd", "billed_gpu_seconds", "wall_seconds",
                "items", "calls")
        lines.append("| " + " | ".join(_fmt(row[key]) for key in keys) + " |")
    legend = ("\nEach row is a partial cell: the latest checkpoint whose cumulative metric is within the ceiling. Committed "
              "columns are that checkpoint's usage; billed columns add the calls after it up to the first one past the "
              "ceiling (charged, decision discarded). A ceiling below the first checkpoint scores zero. No "
              "quality-per-cost composite is computed.\n")
    return "\n".join(lines) + "\n" + legend


def _ceiling_value(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"a ceiling must be a finite non-negative number, got {text!r}")
    return value


def run(work: Path, metric: str, ceilings: list[str], fixture: Path, profile_path: Path, out: Path,
        build_profile: Path | None = None) -> int:
    if not work.is_dir() or not (fixture / "queries.jsonl").is_file() or not (fixture / "gold").is_dir():
        print(f"{work} must be a kept work dir and {fixture} a fixture (queries.jsonl, gold/)", file=sys.stderr)
        return EXIT_USAGE
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        print(f"{out} exists and is not empty; ceilings write new cells into a new directory", file=sys.stderr)
        return EXIT_USAGE
    try:
        profile = load_profile(profile_path)
        checkpoints = history(work)
        policy = checkpoints[-1].get("policy") or {}
        if not policy.get("adapter"):
            # without the recorded adapter a full-replay build cannot be ruled out (its theory is not in the checkpoints)
            raise ValueError("the checkpoint's policy does not record the adapter, so the build cannot be shown not to be a "
                             "full replay; lower ceilings are not derived from it")
        if policy.get("groups_disabled_reason") == FULL_REPLAY:
            raise ValueError("this is a full-replay build (the oracle): its theory is not in the checkpoints, so lower "
                             "ceilings cannot be derived from it")
        totals = cumulative(work, checkpoints)
        how = _profile_check(work, profile, build_profile)
        _corpus_check(work, fixture)
    except (OSError, ValueError, KeyError) as exc:
        print(f"ceilings: {exc}", file=sys.stderr)
        return EXIT_USAGE
    out.mkdir(parents=True, exist_ok=True)
    cells = [derive_cell(work, checkpoints, totals, metric, text, fixture, profile_path, profile, out, how)
             for text in ceilings]
    rows = [_cell_row(_cell_dir(out, metric, cell["ceiling"]["text"]), cell) for cell in cells]
    table = _table(rows)
    (out / "ceilings.md").write_text(table, encoding="utf-8")
    write_json(out / "ceilings.json", {"metric": metric, "fixture": fixture.name, "rows": rows})
    print(table, end="")
    return EXIT_INCOMPLETE if any(cell["exit"] != 0 for cell in cells) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.ceilings", description=__doc__.splitlines()[0])
    parser.add_argument("--work", required=True, type=Path, help="kept work dir (<out>.build of a build with --keep-work)")
    parser.add_argument("--metric", required=True, choices=METRICS)
    parser.add_argument("--ceilings", required=True, help="comma-separated ceilings in the metric's unit (no duplicates)")
    parser.add_argument("--fixture", required=True, type=Path, help="fixture directory (queries.jsonl, gold/)")
    parser.add_argument("--profile", required=True, type=Path, help="the resource profile the build used")
    parser.add_argument("--build-profile", type=Path,
                        help="the build's profile, when its profile.json is not beside the work dir")
    parser.add_argument("--out", required=True, type=Path, help="new directory: one cell per ceiling, plus ceilings.md/json")
    args = parser.parse_args(argv)
    ceilings = [text.strip() for text in args.ceilings.split(",") if text.strip()]
    if not ceilings:
        print("ceilings: --ceilings needs at least one value", file=sys.stderr)
        return EXIT_USAGE
    try:
        values = [_ceiling_value(text) for text in ceilings]
    except argparse.ArgumentTypeError as exc:
        print(f"ceilings: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if len(set(values)) != len(values):
        print(f"ceilings: duplicate ceiling values in {args.ceilings!r}", file=sys.stderr)
        return EXIT_USAGE
    return run(args.work, args.metric, ceilings, args.fixture, args.profile, args.out, args.build_profile)


if __name__ == "__main__":
    raise SystemExit(main())

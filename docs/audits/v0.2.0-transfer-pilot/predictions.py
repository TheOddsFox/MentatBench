"""Maps each transfer-pilot run's decisions to the pilot's classes: predictions.json and mapping_summary.json (PREREG.md, Model runs;
the approved plan, Step 2).

A run's decisions (mb-runs/transfer-pilot/runs/<folder>/submission.build/decisions.jsonl, local) are mapped item by item over the published
selection, with the published candidates. decision_scoring.load cannot read the pilot's labels, because it refuses their compound group
labels, so the mapping calls decision_scoring's own helpers on each item: a relation takes _predict, re-oriented when the logged (a, b)
is the reverse of the candidate's; a group takes _terms_answer, the class of its bounds and chain.

An item is __missing__ (a wrong answer) when it has no decision, its decision has an error, its conf is not a number, its relation is
UNRESOLVED or not a relation, its group answer is malformed, its bounds are invalid (lo > hi), or its class is not a label of the item
(analyse.valid_labels). The mapping_summary.json counts each reason.

write refuses unless every run's latest attempt is done: exit 0, status complete, a readable decision log with no torn final line, and a
decisions file whose sha256 and size are the ones its record holds. It also refuses unless the published fixture and the selection are the
ones fixture_manifest.json records.

usage, from the repository root:
  python docs/audits/v0.2.0-transfer-pilot/predictions.py one ID    (one run's counts; nothing is written)
  python docs/audits/v0.2.0-transfer-pilot/predictions.py write     (all 19 runs: predictions.json, mapping_summary.json, runs.json)
Exit codes: 0 written or printed; 1 refused, or an error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import analyse as AN
import run_models as RM
from mentat_bench import decision_scoring as DS

HERE = Path(__file__).resolve().parent
SUMMARY_SCHEMA = "mentat-bench-transfer-mapping/1"


def read_items(base: Path = HERE) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """The selected item ids, in selection order, and the published candidate of each. Refused when they differ."""
    selected = [row["id"] for row in AN.read_lines(base / "selection.jsonl")]
    cands = {row["qid"]: row for row in AN.read_lines(base / "fixture" / "candidates.jsonl")}
    if sorted(cands) != sorted(selected):
        raise AN.AnalyseError("the published candidates are not the selected items")
    return selected, cands


def verify_published(base: Path = HERE) -> None:
    """The published fixture and the selection are the ones fixture_manifest.json records (analyse.py fixture)."""
    manifest = json.loads((base / "fixture_manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        path = base / "fixture" / name
        if not path.is_file() or AN.sha_file(path) != digest:
            raise AN.AnalyseError(f"{path} does not match fixture_manifest.json")
    if AN.sha_file(base / "selection.jsonl") != manifest["inputs"]["selection.jsonl"]:
        raise AN.AnalyseError("selection.jsonl does not match fixture_manifest.json")


def classify(qid: str, cand: dict[str, Any], row: dict[str, Any] | None, cands: dict[str, dict[str, Any]]) -> tuple[str, str | None]:
    """(class, reason) of one item from its decision row. The reason is None for an answer; otherwise the class is __missing__ and the
    reason says why."""
    op = cand["op"]
    if row is None:
        return DS.MISSING, "absent"
    if row.get("op") != op:
        raise ValueError(f"decision {qid!r} is op {row.get('op')!r}, its candidate is {op!r}")
    flipped = op == "decide_relation" and DS._flipped(qid, cands, row)  # ValueError: the logged (a, b) is neither order
    if row.get("error") is not None:
        return DS.MISSING, "error"
    if DS._answer(row)[1]:  # the conf is not a finite number
        return DS.MISSING, "invalid_conf"
    if op == "decide_relation":
        decision = row.get("decision")
        if (decision.get("relation") if isinstance(decision, dict) else decision) == "UNRESOLVED":
            return DS.MISSING, "unresolved"
        pred = DS._predict(op, decision)
        if pred == DS.MISSING:
            return DS.MISSING, "malformed"
        if flipped:
            pred = DS.FLIP.get(pred, pred)
        n = None
    else:
        pred, _ = DS._terms_answer(row.get("decision"), cand["members"])
        if pred == "invalid":
            return DS.MISSING, "invalid_bounds"
        if pred == DS.MISSING:
            return DS.MISSING, "malformed"
        n = len(cand["members"])
    if pred not in AN.valid_labels(op, n):
        return DS.MISSING, "outside_vocabulary"
    return pred, None


def map_decisions(decisions: Path, selected: list[str], cands: dict[str, dict[str, Any]]) -> tuple[dict[str, str], Counter, dict[str, int]]:
    """(classes by item, __missing__ count by reason, the log's torn and duplicate counts) of one decisions.jsonl. A torn final line is
    dropped and an identical repeat is counted once (DS.read_decisions); two different rows for one qid raise ValueError."""
    rows, read = DS.read_decisions(decisions)
    decided = {row["qid"]: row for row in rows if row.get("qid") is not None}
    classes: dict[str, str] = {}
    reasons: Counter = Counter()
    for qid in selected:
        classes[qid], reason = classify(qid, cands[qid], decided.get(qid), cands)
        if reason is not None:
            reasons[reason] += 1
    return classes, reasons, read


def map_run_dir(run_dir: Path, selected: list[str], cands: dict[str, dict[str, Any]]) -> tuple[dict[str, str], dict[str, Any]]:
    """The classes of one run folder, and its summary: the __missing__ total and by reason, and the log's torn and duplicate counts."""
    classes, reasons, read = map_decisions(Path(run_dir) / "submission.build" / "decisions.jsonl", selected, cands)
    summary = {"items": len(selected), "missing": sum(reasons.values()), "by_reason": dict(sorted(reasons.items())), **read}
    return classes, summary


def not_done(rec: dict[str, Any] | None, runs_root: Path) -> str | None:
    """Why a run's latest attempt does not count, or None when it does (see the module docstring)."""
    if rec is None:
        return "no record"
    if rec["exit_code"] != 0:
        return f"exit code {rec['exit_code']}"
    if rec["status"] != "complete":
        return f"status {rec['status']}"
    if rec.get("log_error"):
        return f"its decision log could not be read ({rec['log_error']})"
    log = rec.get("decision_log")
    if log is None:
        return "no decision log"
    if log["torn_final_line"]:
        return "a torn final line in the decision log"
    path = Path(runs_root) / rec["folder"] / "submission.build" / "decisions.jsonl"
    if not path.is_file():
        return "decisions.jsonl is missing"
    recorded = rec.get("decisions_file")
    if recorded is None or AN.sha_file(path) != recorded["sha256"] or path.stat().st_size != recorded["bytes"]:
        return "decisions.jsonl changed since the run"
    return None


def write(records_dir: Path, runs_root: Path, runs_json: Path, base: Path = HERE, out_dir: Path = HERE) -> None:
    """Writes predictions.json ({run: {item: class}}, for the 17 models and the two baselines), mapping_summary.json, and runs.json (merged
    from the attempt records). Refused, and nothing is written, unless every run is done and the published inputs are the recorded ones."""
    AN.SC.check_frozen()
    verify_published(base)
    ids = RM.run_ids()
    latest = RM.latest_records(records_dir)
    problems = {run_id: why for run_id in ids if (why := not_done(latest.get(run_id), runs_root)) is not None}
    if problems:
        raise AN.AnalyseError("the runs are not all done, so no predictions are written: "
                              + "; ".join(f"{run_id}: {why}" for run_id, why in problems.items()))
    selected, cands = read_items(base)
    predictions: dict[str, dict[str, str]] = {}
    models: dict[str, dict[str, Any]] = {}
    for run_id in ids:
        predictions[run_id], models[run_id] = map_run_dir(Path(runs_root) / latest[run_id]["folder"], selected, cands)
    AN.write_json(Path(out_dir) / "predictions.json", predictions)
    AN.write_json(Path(out_dir) / "mapping_summary.json", {"schema": SUMMARY_SCHEMA, "items": len(selected), "models": models})
    RM.collect(records_dir, runs_json)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    one = sub.add_parser("one", help="map one run and print its counts (nothing is written)")
    one.add_argument("id")
    sub.add_parser("write", help="map all 19 runs into predictions.json and mapping_summary.json")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "one":
            selected, cands = read_items(HERE)
            rec = RM.latest_records(RM.RUNS_DIR).get(a.id)
            _, summary = map_run_dir(RM.LOCAL_RUNS / (a.id if rec is None else rec["folder"]), selected, cands)
            print(json.dumps(summary, indent=1, sort_keys=True))
        else:
            write(RM.RUNS_DIR, RM.LOCAL_RUNS, RM.RUNS_JSON, base=HERE, out_dir=HERE)
            print("wrote predictions.json, mapping_summary.json and runs.json")
    except (AN.AnalyseError, AN.SC.Refused, OSError, ValueError) as exc:
        print(f"predictions: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

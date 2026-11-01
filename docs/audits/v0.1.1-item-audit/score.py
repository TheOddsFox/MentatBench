"""Scores the two judges' answers to the v0.1.1 item-audit sample and writes verdicts.json (the rule is in PREREG.md).

usage: python -B score.py

Reads key.jsonl, judge1_answers.jsonl and judge2_answers.jsonl (the answer files copied into this directory by the main agent)
and sources.json. Each answers line is {"id": "A001", "answer": {...}}, the answer in the answer_format of sample.jsonl. A
missing or invalid answer normalises to null. Writes verdicts.json, deterministically. Read-only on the repo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
WORKTREE = Path(__file__).resolve().parents[3]  # the repository root
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKTREE / "python"))  # the canonical group classes

from mentat_bench.adapters.base import group_terms, terms_class  # noqa: E402

SCHEMA = "mentat-bench-item-verdicts/1"
SAMPLE_RULE = ("PREREG.md: all 423 all_wrong items; 127 inverted (not all_wrong) items, stratified by op x gold class x stratum; "
               "50 unflagged controls, stratified the same way over the unflagged items")
RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")
KINDS = ("none", "exactly_one", "at_most_one", "at_least_k", "at_most_k", "ordered_chain")
VERDICTS = ("suspect_label", "hard_valid", "unclear")
GROUPS = ("all_wrong", "inverted", "control")
ANSWER_FILES = ("judge1_answers.jsonl", "judge2_answers.jsonl")


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) else None


def _count(value: Any, n: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= n else None


def normalised(row: dict[str, Any], answer: Any) -> str | None:
    """The answer as a class: a relation label, or a group's canonical class (chain order is not scored). None when invalid."""
    if isinstance(answer, str):  # a judge that wrote its JSON as a string
        try:
            answer = json.loads(answer)
        except ValueError:
            return None
    if not isinstance(answer, dict):
        return None
    if row["op"] == "decide_relation":
        text = _text(answer.get("relation"))
        return text if text in RELATIONS else None
    kind = _text(answer.get("kind"))
    kind = kind.lower() if kind else None
    if kind == "none":
        return "none"
    n = row["n"]
    lower, upper = _count(answer.get("lower"), n), _count(answer.get("upper"), n)
    if kind not in KINDS or lower is None or upper is None:
        return None
    try:
        return terms_class(group_terms(lower, upper, n, kind == "ordered_chain"))
    except ValueError:  # bounds the canonical terms cannot hold (upper < 1, or lower > upper)
        return None


def verdict(gold: str, judge1: str | None, judge2: str | None) -> str:
    """hard_valid: both judges give the gold. suspect_label: both give the same answer and it is not the gold (the first case
    failed, so an agreed answer here is a non-gold one). unclear: everything else."""
    if judge1 == gold and judge2 == gold:
        return "hard_valid"
    if judge1 is not None and judge1 == judge2:
        return "suspect_label"
    return "unclear"


def answers_by_id(path: Path, key_ids: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            oid = row.get("id")
            if oid not in key_ids:
                raise ValueError(f"{path.name}: an answer for an id that is not in the key: {oid!r}")
            if oid in out:
                raise ValueError(f"{path.name}: two answers for id {oid!r}")
            out[oid] = row.get("answer")
    return out


def tally(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {v: sum(r["verdict"] == v for r in rows) for v in VERDICTS}


def main() -> None:
    with open(OUT / "key.jsonl", encoding="utf-8") as fh:
        key = [json.loads(line) for line in fh if line.strip()]
    sources = json.loads((OUT / "sources.json").read_text(encoding="utf-8"))
    ids = {row["id"] for row in key}
    answers = []
    for name in ANSWER_FILES:
        got = answers_by_id(OUT / name, ids)
        print(f"{name}: {len(got)} answers, {len(ids) - len(got)} missing (counted as no answer)")
        answers.append(got)

    items = []
    for row in key:
        judge1, judge2 = (normalised(row, got.get(row["id"])) for got in answers)
        items.append({"qid": row["qid"], "op": row["op"], "gold": row["gold_class"], "group": row["group"],
                      "judge1": judge1, "judge2": judge2, "verdict": verdict(row["gold_class"], judge1, judge2)})
    items.sort(key=lambda r: r["qid"])

    flagged = [r for r in items if r["group"] != "control"]
    controls = [r for r in items if r["group"] == "control"]
    control_counts = tally(controls)
    doc = {
        "schema": SCHEMA,
        "sample_rule": SAMPLE_RULE,
        "n_judged": len(flagged),  # flagged items judged; the controls are counted under "controls"
        "n_judged_total": len(items),
        "n_flagged": sources["flagged"]["rows"],
        "sampled": True,
        "counts": tally(flagged),
        "controls": {"n": len(controls), **control_counts,
                     "false_alarm_rate": round(control_counts["suspect_label"] / len(controls), 4)},
        "by_group": {g: {"n": sum(r["group"] == g for r in items), **tally([r for r in items if r["group"] == g])}
                     for g in GROUPS},
        "items": items,
    }
    assert {r["group"] for r in items} == set(GROUPS) and len(items) == len({r["qid"] for r in items})
    (OUT / "verdicts.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("flagged items", len(flagged), dict(doc["counts"]))
    print("controls", len(controls), dict(control_counts), "false_alarm_rate", doc["controls"]["false_alarm_rate"])


if __name__ == "__main__":
    main()

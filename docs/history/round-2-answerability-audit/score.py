"""Scores judge answers to the Mentat-Hard v2 answerability sample against key.jsonl.

usage: python -B score.py --key key.jsonl --answers <judge answers jsonl>

Each answers line is {"id": "A001", "answer": {...}}, the answer in the answer_format of sample.jsonl. Every key id needs one
line; a missing or invalid answer counts as wrong (and is tallied). Metrics:
- relation: the relation (EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, NONE) against the gold class.
- binding: the status (bound, unresolved).
- group, class: the judge's constraint as the canonical class. kind none is class "none"; otherwise
  adapters.base.group_terms(lower, upper, n, kind == ordered_chain), compared by terms_class with the gold terms.
  Chain order is collected but not scored.
- group, kind: the judge's kind against the canonical kind of the gold terms (the suite label).
- reading: the base and the alt option letter (A-D, or none), each scored on its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
REPO_PYTHON = str(Path(__file__).resolve().parents[3] / "python")  # the adapters the canonical class uses
sys.path.insert(0, REPO_PYTHON)

from mentat_bench.adapters.base import group_terms, terms_class  # noqa: E402

RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")
STATUSES = ("bound", "unresolved")
KINDS = ("none", "exactly_one", "at_most_one", "at_least_k", "at_most_k", "ordered_chain")
LETTERS = ("A", "B", "C", "D")


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) else None


def _count(value: Any, n: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= n else None


def _letter(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if text.lower() == "none":
        return "none"
    return text.upper() if text.upper() in LETTERS else None


def score_one(row: dict[str, Any], answer: Any) -> dict[str, bool]:
    """The metric -> correct map of one key row and its answer (a dict in the answer_format, or None when missing)."""
    if not isinstance(answer, dict):
        answer = {}
    kind = row["type"]
    if kind == "relation":
        return {"relation": _text(answer.get("relation")) == row["gold"]
                and _text(answer.get("relation")) in RELATIONS}
    if kind == "binding":
        status = _text(answer.get("status"))
        return {"status": status == row["gold"] and status in STATUSES}
    if kind == "group":
        n = row["n"]
        judged = _text(answer.get("kind"))
        judged = judged.lower() if judged else None
        kind_ok = judged == row["kind"] and judged in KINDS
        lower, upper = _count(answer.get("lower"), n), _count(answer.get("upper"), n)
        if judged == "none":
            klass = "none"
        elif judged in KINDS and lower is not None and upper is not None:
            try:
                klass = terms_class(group_terms(lower, upper, n, judged == "ordered_chain"))
            except ValueError:  # bounds the canonical terms cannot hold (hi < 1, lo > hi)
                klass = None
        else:
            klass = None
        gold_class = terms_class([tuple(t) for t in row["gold"]])
        return {"class": klass == gold_class, "kind": kind_ok}
    if kind == "reading":
        base, alt = _letter(answer.get("base")), _letter(answer.get("alt"))
        return {"base": base == row["gold"]["base"], "alt": alt == row["gold"]["alt"]}
    raise ValueError(f"unknown type {kind!r}")


def _answers_by_id(rows: list[dict[str, Any]], key_ids: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in rows:
        oid = row.get("id")
        if oid not in key_ids:
            raise ValueError(f"answer for an id that is not in the key: {oid!r}")
        if oid in out:
            raise ValueError(f"two answers for id {oid!r}")
        answer = row.get("answer")
        if isinstance(answer, str):  # a judge that wrote its JSON as a string
            try:
                answer = json.loads(answer)
            except ValueError:
                answer = None
        out[oid] = answer
    return out


def score(key: list[dict[str, Any]], answers: list[dict[str, Any]]) -> dict[str, Any]:
    """Tallies: {(type, stratum, metric): [n, correct, missing]} and the same for the relation suite strata."""
    by_id = _answers_by_id(answers, {row["id"] for row in key})
    tally: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])
    for row in key:
        answer = by_id.get(row["id"], None)
        missing = row["id"] not in by_id
        for metric, ok in score_one(row, answer).items():
            for stratum in (row["stratum"], "all"):
                cell = tally[(row["type"], stratum, metric)]
                cell[0] += 1
                cell[1] += int(ok)
                cell[2] += int(missing)
            if row["type"] == "relation":
                cell = tally[("relation", f"suite:{row['suite_stratum']}", metric)]
                cell[0] += 1
                cell[1] += int(ok)
                cell[2] += int(missing)
    return dict(tally)


def _rows(tally: dict[tuple[str, str, str], list[int]], keys: list[tuple[str, str, str]]) -> list[str]:
    lines = [f"  {'type':<9} {'stratum':<22} {'metric':<9} {'n':>4} {'correct':>8} {'acc':>7} {'missing':>8}"]
    for key in keys:
        n, correct, missing = tally[key]
        acc = f"{correct / n:.3f}" if n else "n/a"
        lines.append(f"  {key[0]:<9} {key[1]:<22} {key[2]:<9} {n:>4} {correct:>8} {acc:>7} {missing:>8}")
    return lines


def report(tally: dict[tuple[str, str, str], list[int]]) -> str:
    keys = sorted(tally, key=lambda k: (k[0], k[1] != "all", k[1], k[2]))
    overall = [k for k in keys if k[1] == "all"]
    by_stratum = [k for k in keys if k[1] not in ("all",) and not k[1].startswith("suite:")]
    by_suite = [k for k in keys if k[1].startswith("suite:")]
    return "\n".join(["By type (all strata):", *_rows(tally, overall), "", "By type and stratum:", *_rows(tally, by_stratum),
                      "", "Relation by suite stratum (hard_distractor, trivial, real):", *_rows(tally, by_suite)])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--answers", required=True, type=Path)
    args = parser.parse_args(argv)
    with open(args.key, encoding="utf-8") as fh:
        key = [json.loads(line) for line in fh if line.strip()]
    with open(args.answers, encoding="utf-8") as fh:
        answers = [json.loads(line) for line in fh if line.strip()]
    print(report(score(key, answers)))


if __name__ == "__main__":
    main()

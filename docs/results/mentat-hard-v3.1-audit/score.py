"""Scores judge answers to the Mentat-Hard v3 answerability sample against key.jsonl, against the pre-registered bar.

usage: python -B score.py --key key.jsonl --answers <judge answers jsonl> [--answers <judge 2 answers jsonl>]
                          [--adjudication adjudication.jsonl] [--summary summary.json]

Each answers line is {"id": "A001", "answer": {...}}, the answer in the answer_format of sample.jsonl. Every key id needs one
line; a missing or invalid answer counts as wrong (and is tallied). A file's name is its judge's name.

Bar (fixed in notes.md before any judging): a part passes when every judge scores at least BAR on it. Parts (30 items each,
readings: base and alt over the 30 ambiguous rows, decoys: both questions of the 30 decoy rows):
  bindings, relations, groups (full canonical class), readings_base, readings_alt, reading_decoys, heldout_relations,
  deep_relations (v3.1: derived links at depth 3 or more).
Report only (no bar): relations_real (the non-NONE dev relation rows, v2-comparable: 24), groups_real (the real sets, class:
v2-comparable: 15), groups_kind (kind only, all groups, and groups_kind_real / groups_kind_decoy), relations_<class>.
Both-missed: with two judges, the items both judges got wrong in a part. They stay in the score. adjudication.jsonl (optional,
one line per id: {"id": "A001", "derivable": true|false, "derivation": "..."}) records whether each such item's answer is
derivable from the prompt text alone; the summary counts them and never removes an item.

Metrics per item:
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
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
REPO_PYTHON = str(Path(__file__).resolve().parents[3] / "python")  # the adapters the canonical class uses
sys.path.insert(0, REPO_PYTHON)

from mentat_bench.adapters.base import group_terms, terms_class  # noqa: E402

BAR = 0.8  # pre-registered: a part passes when every judge scores at least this
PASS_PARTS = ("bindings", "relations", "groups", "readings_base", "readings_alt", "reading_decoys", "heldout_relations",
              "deep_relations")
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


def parts_of(row: dict[str, Any], metrics: dict[str, bool]) -> list[tuple[str, bool]]:
    """The (part, correct) results one key row contributes. A reading row counts for three parts (base, alt, or decoy)."""
    part = row["part"]
    if part == "bindings":
        return [("bindings", metrics["status"])]
    if part in ("heldout_relations", "deep_relations"):
        return [(part, metrics["relation"])]
    if part == "relations":
        out = [("relations", metrics["relation"]), (f"relations_{row['gold']}", metrics["relation"])]
        if row["gold"] != "NONE":
            out.append(("relations_real", metrics["relation"]))
        return out
    if part == "groups":
        out = [("groups", metrics["class"]), ("groups_kind", metrics["kind"]),
               (f"groups_kind_{row['stratum']}", metrics["kind"])]
        if row["stratum"] == "real":
            out.append(("groups_real", metrics["class"]))
        return out
    if part == "readings_ambiguous":
        return [("readings_base", metrics["base"]), ("readings_alt", metrics["alt"])]
    if part == "readings_decoy":
        return [("reading_decoys", metrics["base"] and metrics["alt"])]
    raise ValueError(f"unknown part {part!r}")


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


def results(key: list[dict[str, Any]], answers: list[dict[str, Any]]) -> dict[str, dict[str, list[tuple[str, bool]]]]:
    """part -> [(id, correct)] for one judge. A missing answer is wrong for every part the row counts for."""
    by_id = _answers_by_id(answers, {row["id"] for row in key})
    out: dict[str, list[tuple[str, bool]]] = {}
    for row in key:
        for part, ok in parts_of(row, score_one(row, by_id.get(row["id"], None))):
            out.setdefault(part, []).append((row["id"], bool(ok)))
    return out


def summarise(judges: dict[str, dict[str, list[tuple[str, bool]]]], missing: dict[str, int],
              adjudication: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The summary: per part, each judge's correct count and accuracy, the pass of each part against BAR, and (with two or
    more judges) the ids every judge got wrong, with their adjudication when recorded."""
    names = list(judges)
    parts = sorted({p for per in judges.values() for p in per})
    summary: dict[str, Any] = {"bar": BAR, "judges": names, "parts": {}}
    for part in parts:
        judged: dict[str, Any] = {}
        for name in names:
            rows = judges[name].get(part, [])
            n = len(rows)
            correct = sum(ok for _, ok in rows)
            judged[name] = {"n": n, "correct": correct, "acc": round(correct / n, 4) if n else None,
                            "missing": missing[name].get(part, 0)}
        entry: dict[str, Any] = {"n": next(iter(judged.values()))["n"], "judges": judged}
        if part in PASS_PARTS:
            entry["pass"] = all(j["correct"] * 5 >= j["n"] * 4 for j in judged.values())  # acc >= BAR, exact
        if len(names) >= 2:
            per_id = [dict(judges[name].get(part, [])) for name in names]
            both = [oid for oid in per_id[0] if not any(p[oid] for p in per_id)]
            entry["both_missed"] = both
            entry["adjudicated"] = {
                "derivable": sum(1 for oid in both if adjudication.get(oid, {}).get("derivable") is True),
                "not_derivable": sum(1 for oid in both if adjudication.get(oid, {}).get("derivable") is False),
                "unadjudicated": sum(1 for oid in both if oid not in adjudication)}
        summary["parts"][part] = entry
    summary["all_pass"] = all(summary["parts"].get(p, {}).get("pass", False) for p in PASS_PARTS)
    return summary


def table(summary: dict[str, Any]) -> str:
    names = summary["judges"]
    head = f"  {'part':<20} {'n':>3} " + " ".join(f"{name[:16]:>16}" for name in names) + f" {'result':>8}"
    lines = [f"Bar: every judge >= {BAR:.0%} on a part. Pass or fail:", head]

    def row(part: str, entry: dict[str, Any], result: str) -> str:
        cells = " ".join(f"{entry['judges'][name]['correct']:>2}/{entry['n']:<2} {entry['judges'][name]['acc']:.3f}".rjust(16)
                         for name in names)
        return f"  {part:<20} {entry['n']:>3} {cells} {result:>8}"

    for part in PASS_PARTS:
        entry = summary["parts"].get(part)
        if entry is not None:
            lines.append(row(part, entry, "PASS" if entry["pass"] else "FAIL"))
    lines += ["", "Report only (no bar):", head]
    for part in sorted(p for p in summary["parts"] if p not in PASS_PARTS):
        lines.append(row(part, summary["parts"][part], ""))
    if len(names) >= 2:
        lines += ["", "Both judges missed (kept in the score; adjudicated as derivable from the prompt or not):"]
        for part in PASS_PARTS:
            entry = summary["parts"].get(part, {})
            if "both_missed" in entry:
                a = entry["adjudicated"]
                lines.append(f"  {part:<20} {len(entry['both_missed']):>3} missed by both: derivable {a['derivable']}, "
                             f"not derivable {a['not_derivable']}, unadjudicated {a['unadjudicated']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--answers", required=True, type=Path, action="append", help="one per judge (one or two)")
    parser.add_argument("--adjudication", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=here / "summary.json")
    args = parser.parse_args(argv)
    if len(args.answers) not in (1, 2):
        parser.error("give one or two --answers files")
    with open(args.key, encoding="utf-8") as fh:
        key = [json.loads(line) for line in fh if line.strip()]
    judges: dict[str, dict[str, list[tuple[str, bool]]]] = {}
    missing: dict[str, dict[str, int]] = {}
    for path in args.answers:
        with open(path, encoding="utf-8") as fh:
            answers = [json.loads(line) for line in fh if line.strip()]
        name = path.stem
        judges[name] = results(key, answers)
        answered = {row.get("id") for row in answers}
        missing[name] = {}
        for row in key:
            if row["id"] not in answered:
                for part, _ in parts_of(row, {m: False for m in score_one(row, None)}):
                    missing[name][part] = missing[name].get(part, 0) + 1
    adjudication: dict[str, dict[str, Any]] = {}
    if args.adjudication is not None:
        with open(args.adjudication, encoding="utf-8") as fh:
            adjudication = {row["id"]: row for row in (json.loads(line) for line in fh if line.strip())}
    summary = summarise(judges, missing, adjudication)
    print(table(summary))
    print(f"\nall pass-parts pass: {summary['all_pass']}")
    args.summary.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

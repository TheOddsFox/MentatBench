"""Robustness of the v0.1.1 item-audit result. EXPLORATORY: not pre-registered, changes no verdict in verdicts.json.

usage: python -B exploratory.py        (writes exploratory.json, deterministically; read-only on the judging files)

(a) gold agreement of each judge set per slice of 100 consecutive ids (A001-A100 is slice 1, and so on), and the discarded
    first answers of judge set 1, slice 3 (deviations/judge1_slice3_delegated_answers.jsonl).
(b) the suspect labels, each judge's raw answer, and whether a judge gave the gold bounds under the kind label `none`.
(c) sensitivity: the pre-registered verdicts with the discarded delegated answers in place of the re-run of slice 3.
(d) EXPLORATORY bound-based reading: a group answer matches gold when its lower and upper bounds equal the gold class's bounds,
    whatever its kind label. A gold or an answer with an ordered_chain is compared by class only, because the chain is a
    constraint that the bounds do not carry. Verdict rule as in PREREG.md with this match: hard_valid when both judges match;
    suspect_label when the two normalised answers are the same, not null, and neither matches.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score as SC  # noqa: E402  (the scorer: the same normalisation and verdict rule as verdicts.json)
from mentat_bench.adapters.base import group_terms, terms_class  # noqa: E402

SCHEMA = "mentat-bench-item-exploratory/1"
OUT = HERE / "exploratory.json"
KEY, JUDGE1, JUDGE2 = HERE / "key.jsonl", HERE / "judge1_answers.jsonl", HERE / "judge2_answers.jsonl"
DELEGATED, VERDICTS = HERE / "deviations" / "judge1_slice3_delegated_answers.jsonl", HERE / "verdicts.json"
SLICE, SLICES = 100, 6
DISCARDED_SLICE = 3  # judge set 1, slice 3 (A201-A300) was re-run; its first answers are in DELEGATED
VERDICT_ORDER = ("hard_valid", "unclear", "suspect_label")


def _rows(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slice_of(item_id: str) -> int:
    return (int(item_id[1:]) - 1) // SLICE + 1


def ids_of(s: int) -> list[str]:
    return [f"A{i:03d}" for i in range((s - 1) * SLICE + 1, s * SLICE + 1)]


def _obj(answer: Any) -> dict | None:
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError:
            return None
    return answer if isinstance(answer, dict) else None


def gold_bounds(row: dict[str, Any]) -> tuple[int, int] | None:
    """The (lower, upper) bounds of a group's gold class, from its terms. The self-check: the bounds rebuild the gold class."""
    if row["op"] != "decide_group":
        return None
    terms = [tuple(t) for t in row["gold"]]
    kinds, n = dict(terms), row["n"]
    lo = 1 if "exactly_one" in kinds else kinds.get("at_least_k", 0)
    hi = 1 if "exactly_one" in kinds else kinds["at_most_k"] if "at_most_k" in kinds else 1 if "at_most_one" in kinds else n
    chain = "ordered_chain" in kinds
    assert terms_class(group_terms(lo, hi, n, chain)) == row["gold_class"], row["id"]
    return lo, hi


def answer_bounds(row: dict[str, Any], answer: Any) -> tuple[int, int] | None:
    """The raw (lower, upper) of a group answer when both are counts within 0..n, whatever the kind; None otherwise."""
    obj = _obj(answer)
    if obj is None:
        return None
    lo, hi = obj.get("lower"), obj.get("upper")
    ok = all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= row["n"] for v in (lo, hi))
    return (lo, hi) if ok else None


def matches_gold(row: dict[str, Any], answer: Any, cls: str | None) -> bool:
    """Exploratory match: the normalised class is the gold class, or (groups without a chain) the bounds are the gold bounds."""
    if cls is None:
        return False
    if cls == row["gold_class"]:
        return True
    gb = gold_bounds(row)
    if gb is None or "ordered_chain" in row["gold_class"] or "ordered_chain" in cls:
        return False
    return answer_bounds(row, answer) == gb


def verdict_bounds(row: dict[str, Any], a1: Any, a2: Any) -> str:
    c1, c2 = SC.normalised(row, a1), SC.normalised(row, a2)
    m1, m2 = matches_gold(row, a1, c1), matches_gold(row, a2, c2)
    if m1 and m2:
        return "hard_valid"
    if c1 is not None and c1 == c2 and not m1 and not m2:
        return "suspect_label"
    return "unclear"


def _tally(verdicts: list[str]) -> dict[str, int]:
    return {v: sum(x == v for x in verdicts) for v in VERDICT_ORDER}


def _raw(answer: Any) -> dict[str, Any] | None:
    obj = _obj(answer)
    if obj is None:
        return None
    return {k: obj.get(k) for k in ("kind", "lower", "upper", "relation") if k in obj}


def build() -> dict[str, Any]:
    key = _rows(KEY)
    ids = {row["id"] for row in key}
    j1, j2 = SC.answers_by_id(JUDGE1, ids), SC.answers_by_id(JUDGE2, ids)
    delegated = SC.answers_by_id(DELEGATED, ids)
    assert set(delegated) == set(ids_of(DISCARDED_SLICE)), "the discarded answers are not slice 3"
    assert len(j1) == len(j2) == len(key) == SLICE * SLICES

    # the pre-registered verdicts, recomputed and checked against verdicts.json
    verdicts = json.loads(VERDICTS.read_text(encoding="utf-8"))
    by_qid = {r["qid"]: r for r in verdicts["items"]}
    rows = []
    for row in key:
        c1, c2 = SC.normalised(row, j1.get(row["id"])), SC.normalised(row, j2.get(row["id"]))
        v = SC.verdict(row["gold_class"], c1, c2)
        want = by_qid[row["qid"]]
        assert (want["judge1"], want["judge2"], want["verdict"]) == (c1, c2, v), row["qid"]
        rows.append({"row": row, "c1": c1, "c2": c2, "verdict": v})
    assert len(by_qid) == len(key)

    # (a) per slice
    slices = []
    for s in range(1, SLICES + 1):
        part = [r for r in rows if slice_of(r["row"]["id"]) == s]
        slices.append({"slice": s, "ids": f"{ids_of(s)[0]}-{ids_of(s)[-1]}", "n": len(part),
                       "judge1_gold_agree": sum(r["c1"] == r["row"]["gold_class"] for r in part),
                       "judge2_gold_agree": sum(r["c2"] == r["row"]["gold_class"] for r in part)})
    discarded_agree = sum(SC.normalised(r, delegated[r["id"]]) == r["gold_class"] for r in key if r["id"] in delegated)

    # (b) the suspect labels
    suspects = [r for r in rows if r["verdict"] == "suspect_label"]
    listing = []
    for r in sorted(suspects, key=lambda x: x["row"]["qid"]):
        row = r["row"]
        gb = gold_bounds(row)
        kind_none_gold_bounds = []
        if gb is not None:
            for name, a in (("judge1", j1[row["id"]]), ("judge2", j2[row["id"]])):
                obj = _obj(a)
                if obj is not None and str(obj.get("kind")).lower() == "none" and answer_bounds(row, a) == gb:
                    kind_none_gold_bounds.append(name)
        listing.append({"qid": row["qid"], "id": row["id"], "slice": slice_of(row["id"]), "op": row["op"],
                        "group": row["group"], "gold": row["gold_class"], "n": row.get("n"),
                        "judge1": {"class": r["c1"], "answer": _raw(j1[row["id"]])},
                        "judge2": {"class": r["c2"], "answer": _raw(j2[row["id"]])},
                        "judges_giving_gold_bounds_with_kind_none": kind_none_gold_bounds})
    atmost_none = [x for x in listing if x["op"] == "decide_group" and x["gold"].startswith("at_most_k")
                   and x["judge1"]["class"] == "none" and x["judge2"]["class"] == "none"]

    # (c) sensitivity: the discarded first answers of slice 3 in place of the re-run
    j1_disc = {**j1, **delegated}
    sens = []
    for row in key:
        c1, c2 = SC.normalised(row, j1_disc[row["id"]]), SC.normalised(row, j2[row["id"]])
        sens.append({"qid": row["qid"], "id": row["id"], "group": row["group"],
                     "verdict": SC.verdict(row["gold_class"], c1, c2)})
    flagged_s = [r for r in sens if r["group"] != "control"]
    controls_s = [r for r in sens if r["group"] == "control"]

    # (d) exploratory bound-based reading
    bounds = [{"qid": r["row"]["qid"], "id": r["row"]["id"], "group": r["row"]["group"],
               "verdict": verdict_bounds(r["row"], j1[r["row"]["id"]], j2[r["row"]["id"]])} for r in rows]
    flagged_b = [r for r in bounds if r["group"] != "control"]
    controls_b = [r for r in bounds if r["group"] == "control"]
    remaining = sorted(r["qid"] for r in bounds if r["verdict"] == "suspect_label" and r["group"] != "control")
    suspect_qids = [x["qid"] for x in listing]
    resolved = sorted(set(suspect_qids) - set(remaining))

    return {
        "schema": SCHEMA,
        "status": "exploratory: not pre-registered; verdicts.json is unchanged and remains the pre-registered result",
        "inputs": {"key.jsonl": _sha(KEY), "judge1_answers.jsonl": _sha(JUDGE1), "judge2_answers.jsonl": _sha(JUDGE2),
                   "deviations/judge1_slice3_delegated_answers.jsonl": _sha(DELEGATED), "verdicts.json": _sha(VERDICTS)},
        "a_gold_agreement_by_slice": {
            "slices": slices,
            "discarded_judge1_slice3": {"n": SLICE, "gold_agree": discarded_agree},
        },
        "b_suspect_labels": {
            "counts": _tally([r["verdict"] for r in rows if r["row"]["group"] != "control"]),
            "suspects": listing,
            "group_suspects": sum(x["op"] == "decide_group" for x in listing),
            "atmost_k_none": {"n": len(atmost_none), "qids": [x["qid"] for x in atmost_none],
                              "slices": dict(sorted(Counter(str(x["slice"]) for x in atmost_none).items())),
                              "judge2_gives_gold_bounds_with_kind_none": sum(
                                  "judge2" in x["judges_giving_gold_bounds_with_kind_none"] for x in atmost_none),
                              "judge1_gives_gold_bounds_with_kind_none": sum(
                                  "judge1" in x["judges_giving_gold_bounds_with_kind_none"] for x in atmost_none)},
        },
        "c_sensitivity_discarded_first_answers": {
            "flagged": _tally([r["verdict"] for r in flagged_s]),
            "controls": _tally([r["verdict"] for r in controls_s]),
            "suspects": sorted(r["qid"] for r in sens if r["verdict"] == "suspect_label" and r["group"] != "control"),
        },
        "d_exploratory_bound_reading": {
            "flagged": _tally([r["verdict"] for r in flagged_b]),
            "controls": _tally([r["verdict"] for r in controls_b]),
            "remaining_suspects": remaining,
            "resolved_suspects": resolved,
        },
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps(build()))
    print("wrote", OUT.name)


if __name__ == "__main__":
    main()

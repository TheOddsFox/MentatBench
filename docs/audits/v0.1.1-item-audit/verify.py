"""Self-check of sample.jsonl, key.jsonl, sources.json and the scorer (run after build_sample.py).

1. No gold-revealing fields: sample rows have exactly id, type, prompt, answer_format; ids are opaque and in key order; no qid,
   outcome id or flag name appears in sample.jsonl.
2. Each prompt equals the text rebuilt from the child's own builders (systemone_child._rules, _relation_question, _choice,
   _frame, _listing and the templates), a path separate from the wire capture in build_sample.py.
3. Rule cards: every relation and group prompt contains the rule card of each outcome it asks about (under "League rules:").
4. Sources and design: the fixture, flagged.jsonl and the child still match sources.json; the sample is re-derived from the
   population and matches the key (ids, groups, order); the group counts are 423, 127 and 50; each drawn cell is within one of
   its exact quota.
5. Scorer: the gold, given as the answer, is hard_valid for both judges on every item; the verdict rule and the answer
   normalisation hold on synthetic cases.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import build_sample as B  # noqa: E402  (the builder: the same child module and the same helpers)
import score as SC  # noqa: E402

S1, F = B.S1, B.F


def read(path: str) -> list[dict]:
    with open(HERE / path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def menu(criteria: dict) -> str:
    return "\n".join(f"{key}: {text}" for key, text in criteria.items())


def prompt_of(state: str, question: dict) -> str:
    return f"{state}\n\n{question['instructions']}\nOptions:\n{menu(question['criteria'])}"


def gold_answer(key_row: dict) -> dict:
    """The gold as a judge's answer, in the answer_format of the type."""
    if key_row["op"] == "decide_relation":
        return {"relation": key_row["gold"]}
    terms = [tuple(t) for t in key_row["gold"]]
    kinds, n = dict(terms), key_row["n"]
    if not terms:
        return {"kind": "none", "lower": 0, "upper": n, "chain": []}
    lo = 1 if "exactly_one" in kinds else kinds.get("at_least_k", 0)
    hi = 1 if "exactly_one" in kinds else (kinds["at_most_k"] if "at_most_k" in kinds
                                           else 1 if "at_most_one" in kinds else n)
    return {"kind": key_row["kind"], "lower": lo, "upper": hi, "chain": []}


def self_check_scorer() -> None:
    """The verdict rule and the normalisation on synthetic cases (the rule is in PREREG.md)."""
    v = SC.verdict
    assert v("EQUIV", "EQUIV", "EQUIV") == "hard_valid"
    assert v("EQUIV", "IMPLIES", "IMPLIES") == "suspect_label"
    assert v("EQUIV", "EQUIV", "IMPLIES") == "unclear"
    assert v("EQUIV", "IMPLIES", "NONE") == "unclear"
    assert v("EQUIV", None, None) == "unclear" and v("EQUIV", None, "NONE") == "unclear"
    rel = {"op": "decide_relation"}
    assert SC.normalised(rel, {"relation": "EQUIV"}) == "EQUIV"
    assert SC.normalised(rel, {"relation": "equiv"}) is None and SC.normalised(rel, "junk") is None
    grp = {"op": "decide_group", "n": 3}
    assert SC.normalised(grp, {"kind": "none", "lower": 0, "upper": 3, "chain": []}) == "none"
    assert SC.normalised(grp, {"kind": "exactly_one", "lower": 1, "upper": 1, "chain": []}) == "exactly_one"
    assert SC.normalised(grp, {"kind": "at_least_k", "lower": 1, "upper": 3, "chain": []}) == "at_least_k:1"
    assert SC.normalised(grp, {"kind": "at_most_k", "lower": 0, "upper": 0, "chain": []}) is None  # upper < 1
    assert SC.normalised(grp, {"kind": "at_least_k", "lower": 4, "upper": 3, "chain": []}) is None  # lower > n
    assert SC.normalised(grp, {"kind": "ordered_chain", "lower": 0, "upper": 3, "chain": [1, 2, 3]}) == "ordered_chain"
    assert SC.normalised(grp, {"kind": "bogus", "lower": 0, "upper": 3}) is None
    print("5a. scorer: verdict rule and normalisation hold on synthetic cases")


def main() -> None:
    sources = json.loads((HERE / "sources.json").read_text(encoding="utf-8"))
    sample, key = read("sample.jsonl"), read("key.jsonl")
    text = (HERE / "sample.jsonl").read_text(encoding="utf-8")

    # 1. fields, opaque ids, no leaks
    assert all(set(row) == {"id", "type", "prompt", "answer_format"} for row in sample)
    assert [row["id"] for row in sample] == [f"A{i:03d}" for i in range(1, len(sample) + 1)]
    assert [row["id"] for row in key] == [row["id"] for row in sample] and len(sample) == 600
    for word in ("qid", "gold_class", "stratum", "all_wrong", "inverted", "flags", "residual", "gold"):
        assert not re.search(rf"\b{word}\b", text), word
    assert not re.search(r"\b[rgb]\d{5}\b", text), "a qid in sample.jsonl"
    assert "mh:" not in text, "an outcome id in sample.jsonl"
    print("1. fields: ok; ids A001-A600 in key order; sample.jsonl has no qid, outcome id, stratum, flag or gold field")

    # 4a. sources: the fixture, the flag list and the child are the ones the sample was built from
    src = B.F.source(B.FIXTURE)
    src["path"] = "fixtures/mentat_hard_test"
    assert src == sources["fixture"], "the fixture changed since build_sample.py"
    flagged_sha = hashlib.sha256((HERE / "flagged.jsonl").read_bytes()).hexdigest()
    assert flagged_sha == sources["flagged"]["sha256"], "flagged.jsonl changed since build_sample.py"
    assert S1.PROMPT_SHA256 == B.PROMPT_SHA256 == sources["child"]["prompt_sha256"] == \
        "d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44", "the child's prompts are not the pre-registered ones"
    print("4a. sources: fixture", src["content_hash"][:12], "seed", src["seed"], "| flagged.jsonl", flagged_sha[:12],
          "| child prompt sha", S1.PROMPT_SHA256[:12])

    # 2 and 3. prompts rebuilt from the child's builders; rule cards
    fx = F.load(B.FIXTURE)
    cands = {r["qid"]: r for r in fx["cands"]}
    corpus = fx["corpus"]
    by_id = {row["id"]: row for row in sample}
    checked, cards = collections.Counter(), collections.Counter()
    for k in key:
        s, cand = by_id[k["id"]], cands[k["qid"]]
        if k["op"] == "decide_relation":
            a, b = F.ctx_of(corpus, cand["a"]), F.ctx_of(corpus, cand["b"])
            want = prompt_of(S1._frame(S1._rules([a, b]) + S1._prop("A", a)), S1._relation_question(a, b))
            ctxs, kind = [a, b], "relation"
        else:
            members = [F.ctx_of(corpus, m) for m in cand["members"]]
            state = S1._frame(S1._rules(members) + S1.T["group_state"].format(listing=S1._listing(members)))
            want = prompt_of(state, S1._choice("kind")) + "\n\n" + F.NEUTRAL.format(n=len(members))
            ctxs, kind = members, "group"
        assert s["type"] == kind and s["prompt"] == want, k["id"]
        checked[kind] += 1
        for ctx in ctxs:
            card = (ctx.get("context") or "").strip()
            assert card and "League rules:\n" in want and card in want, (k["id"], "rule card missing")
            cards[kind] += 1
    print("2. prompts equal the child rebuild:", dict(checked))
    print("3. rule cards: each asked outcome's card is in its prompt:", dict(cards), "outcomes checked")

    # 4b. the sample re-derived from the population; group counts; drawn cells within one of the exact quota
    items = B.population(fx)
    groups = B.groups_of(items, B.flagged_rows(HERE / "flagged.jsonl"))
    assert {g: len(v) for g, v in groups.items()} == {"all_wrong": 423, "inverted": 2539, "control": 4038}
    chosen, alloc = B.select(groups)
    assert [(it["qid"], g) for g, it in chosen] == [(k["qid"], k["group"]) for k in key], "the sample is not the re-derived one"
    assert alloc == sources["allocation"]
    counts = collections.Counter(k["group"] for k in key)
    assert counts == {"all_wrong": 423, "inverted": 127, "control": 50}, counts
    assert {k["qid"] for k in key if k["group"] == "all_wrong"} == {it["qid"] for it in groups["all_wrong"]}
    for g, k_draw in (("inverted", 127), ("control", 50)):
        pool = groups[g]
        size = collections.Counter(B.cell(it) for it in pool)
        drawn = collections.Counter((k["op"], k["gold_class"], k["stratum"]) for k in key if k["group"] == g)
        assert sum(drawn.values()) == k_draw and set(drawn) <= set(size)
        for c, n in size.items():
            lo, hi = k_draw * n // len(pool), -(-k_draw * n // len(pool))  # floor and ceiling of the exact quota
            assert lo <= drawn[c] <= hi, (g, c, drawn[c], lo, hi)
    print("4b. sample re-derived from the population: 423 all_wrong, 127 inverted, 50 controls; every drawn cell within one "
          "of its exact quota")

    # 5. gold round trip through the scorer, and the scorer's own self-check
    results = [SC.verdict(k["gold_class"], SC.normalised(k, gold_answer(k)), SC.normalised(k, gold_answer(k))) for k in key]
    bad = [k["id"] for k, v in zip(key, results) if v != "hard_valid"]
    assert not bad, bad[:10]
    self_check_scorer()
    print("5b. gold round trip: every item is hard_valid when both judges give the gold")


if __name__ == "__main__":
    main()

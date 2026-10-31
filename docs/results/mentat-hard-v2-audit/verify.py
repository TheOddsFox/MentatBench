"""Self-check of sample.jsonl and key.jsonl (run after build_sample.py).

1. No gold-revealing fields: sample rows have exactly id, type, prompt, answer_format; ids are opaque; no qid, outcome id,
   stratum, terms or gold text appears in sample.jsonl.
2. Each prompt equals the text rebuilt from the child's own builders (systemone_child._relation_question, _choice, _frame,
   _listing and the templates), a path separate from the wire capture in build_sample.py.
3. The scorer, given the gold as the answer, scores 1.0 on every metric (round trip through key and score.py).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_sample as B  # noqa: E402  (the builder: the same dev paths and the same child module)
import score as SC  # noqa: E402

S1 = B.S1


def read(path: str) -> list[dict]:
    with open(HERE / path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def menu(criteria: dict) -> str:
    return "\n".join(f"{key}: {text}" for key, text in criteria.items())


def prompt_of(state: str, question: dict) -> str:
    return f"{state}\n\n{question['instructions']}\nOptions:\n{menu(question['criteria'])}"


def main() -> None:
    sample, key = read("sample.jsonl"), read("key.jsonl")
    text = (HERE / "sample.jsonl").read_text(encoding="utf-8")

    # 1. fields and opaque ids
    assert all(set(row) == {"id", "type", "prompt", "answer_format"} for row in sample)
    assert all(re.fullmatch(r"A\d{3}", row["id"]) for row in sample)
    assert len({row["id"] for row in sample}) == len(sample) == len(key)
    assert [row["id"] for row in sample] == [row["id"] for row in key]
    for token in ("qid", "stratum", "terms", "gold", "readings", "mh:", "r0", "g0", "b0", "\"label\":"):
        assert token not in text, token
    assert not re.search(r"\b[rgb]\d{5}\b", text)  # no qid such as r00001, g01001, b00001
    print("1. fields: ok; ids opaque; sample.jsonl has no qid, outcome id, stratum, terms or gold text")

    # 2. prompts rebuilt from the child's builders
    corpus = {r["id"]: r for r in B.read_jsonl(B.DEV / "corpus.jsonl")}
    labels = {r["qid"]: r for r in B.read_jsonl(B.DEV / "gold" / "suite_labels.jsonl")}
    cands = {r["qid"]: r for r in B.read_jsonl(B.DEV / "candidates.jsonl")}
    by_id = {row["id"]: row for row in sample}
    checked = dict.fromkeys(("relation", "binding", "group", "reading"), 0)
    for k in key:
        s, cand = by_id[k["id"]], cands[k["qid"]]
        assert s["type"] == k["type"]
        if k["type"] == "relation":
            a, b = B.ctx_of(corpus, cand["a"]), B.ctx_of(corpus, cand["b"])
            q = S1._relation_question(a, b)
            want = prompt_of(S1._frame(S1._prop("A", a)), q)
        elif k["type"] == "binding":
            ctx = B.ctx_of(corpus, cand["id"])
            want = prompt_of(S1._frame(S1._record(ctx)), S1._choice("status"))
        elif k["type"] == "group" and cand["source"] == "curated":
            members = [B.ctx_of(corpus, m) for m in cand["members"]]
            state = S1._frame(S1.T["group_state"].format(listing=S1._listing(members)))
            want = prompt_of(state, S1._choice("kind")) + "\n\n" + B.NEUTRAL.format(n=len(members))
        else:  # reading
            members = cand["members"]
            u = B.ctx_of(corpus, members[0])
            options = [B.ctx_of(corpus, m) for m in members[1:]]
            letters = S1.ORDER_LETTERS[:len(options)]
            listing = "\n".join(S1.T["letter"].format(letter=lt, question=m["question"], label=m["label"])
                                for lt, m in zip(letters, options))
            state = S1._frame(S1.T["reading_state"].format(proposition=S1._prop("U", u), listing=listing))
            menu_ = {lt: S1.PROMPTS["reading"]["option"].format(letter=lt) for lt in letters}
            menu_["none"] = S1.PROMPTS["reading"]["none"]
            want = {"base": prompt_of(state, S1._choice("base", criteria=menu_)),
                    "alt": prompt_of(state, S1._choice("alt", criteria=menu_))}
        assert s["prompt"] == want, k["id"]
        checked[k["type"]] += 1
    print("2. prompts equal the child-builder rebuild:", checked)

    # 3. gold round trip through the scorer
    answers = []
    for k in key:
        if k["type"] == "relation":
            ans = {"relation": k["gold"]}
        elif k["type"] == "binding":
            ans = {"status": k["gold"]}
        elif k["type"] == "group":
            terms = [tuple(t) for t in k["gold"]]
            kinds = dict(terms)
            if not terms:
                ans = {"kind": "none", "lower": 0, "upper": k["n"], "chain": []}
            else:
                lo = 1 if "exactly_one" in kinds else kinds.get("at_least_k", 0)
                hi = 1 if "exactly_one" in kinds else (kinds["at_most_k"] if "at_most_k" in kinds
                                                       else 1 if "at_most_one" in kinds else k["n"])
                ans = {"kind": k["kind"], "lower": lo, "upper": hi, "chain": []}
        else:
            ans = {"base": k["gold"]["base"].upper() if k["gold"]["base"] != "none" else "none",
                   "alt": k["gold"]["alt"].upper() if k["gold"]["alt"] != "none" else "none"}
        answers.append({"id": k["id"], "answer": ans})
    tally = SC.score(key, answers)
    bad = {key_: cell for key_, cell in tally.items() if cell[0] and cell[1] != cell[0]}
    assert not bad, bad
    print("3. gold round trip: every metric 100%:", len(tally), "cells")


if __name__ == "__main__":
    main()

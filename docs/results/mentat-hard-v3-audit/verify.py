"""Self-check of sample.jsonl, key.jsonl and sources.json (run after build_sample.py).

1. No gold-revealing fields: sample rows have exactly id, type, prompt, answer_format; ids are opaque; no qid, stratum,
   terms, gold, family, build or outcome id appears in sample.jsonl.
2. Each prompt equals the text rebuilt from the child v6's own builders (systemone_child._rules, _relation_question, _choice,
   _frame, _listing, _record and the templates), a path separate from the wire capture in build_sample.py.
3. Rule cards: every relation, group and reading prompt contains the rule card of each outcome it asks about (under
   "League rules:"); no binding prompt contains a rule card.
4. Sources and design: the fixtures still hash to the hashes in sources.json (dev: split dev, the dev seed; held-out:
   split test, not the registered test seed); each part has its designed counts; held-out relations each have an outcome
   of a held-out family (21-30), and dev relations have none.
5. The scorer, given the gold as the answer, scores 100% in every part (round trip through key and score.py).
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_sample as B  # noqa: E402  (the builder: the same child module and the same helpers)
import score as SC  # noqa: E402

S1 = B.S1
DEV_FAMILIES = B.DEV_FAMILIES


def read(path: str) -> list[dict]:
    with open(HERE / path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def menu(criteria: dict) -> str:
    return "\n".join(f"{key}: {text}" for key, text in criteria.items())


def prompt_of(state: str, question: dict) -> str:
    return f"{state}\n\n{question['instructions']}\nOptions:\n{menu(question['criteria'])}"


def gold_answer(key_row: dict) -> dict:
    """The gold as a judge's answer, in the answer_format of the type."""
    if key_row["type"] == "relation":
        return {"relation": key_row["gold"]}
    if key_row["type"] == "binding":
        return {"status": key_row["gold"]}
    if key_row["type"] == "group":
        terms = [tuple(t) for t in key_row["gold"]]
        kinds = dict(terms)
        if not terms:
            return {"kind": "none", "lower": 0, "upper": key_row["n"], "chain": []}
        lo = 1 if "exactly_one" in kinds else kinds.get("at_least_k", 0)
        hi = 1 if "exactly_one" in kinds else (kinds["at_most_k"] if "at_most_k" in kinds
                                               else 1 if "at_most_one" in kinds else key_row["n"])
        return {"kind": key_row["kind"], "lower": lo, "upper": hi, "chain": []}
    gold = key_row["gold"]
    return {"base": gold["base"].upper() if gold["base"] != "none" else "none",
            "alt": gold["alt"].upper() if gold["alt"] != "none" else "none"}


def main() -> None:
    sources = json.loads((HERE / "sources.json").read_text(encoding="utf-8"))
    sample, key = read("sample.jsonl"), read("key.jsonl")
    text = (HERE / "sample.jsonl").read_text(encoding="utf-8")

    # 1. fields and opaque ids
    assert all(set(row) == {"id", "type", "prompt", "answer_format"} for row in sample)
    assert all(re.fullmatch(r"A\d{3}", row["id"]) for row in sample)
    assert len({row["id"] for row in sample}) == len(sample) == len(key)
    assert [row["id"] for row in sample] == [row["id"] for row in key]
    for word in ("qid", "stratum", "terms", "families", "suite_stratum", "gold", "build", "held_out"):
        assert not re.search(rf"\b{word}\b", text), word
    assert "mh:" not in text and '"label":' not in text
    assert not re.search(r"\b[rgb]\d{5}\b", text)  # no qid such as r00001, g01001, b00001
    print("1. fields: ok; ids opaque; sample.jsonl has no qid, outcome id, stratum, terms, family, build or gold text")

    # 4a. sources: the fixtures are the ones the sample was built from
    dev_path, held_path = Path(sources["dev"]["path"]), Path(sources["heldout"]["path"])
    assert B.source(dev_path) == sources["dev"], "dev fixture changed since build_sample.py"
    assert B.source(held_path) == sources["heldout"], "held-out fixture changed since build_sample.py"
    assert sources["dev"]["split"] == "dev" and sources["dev"]["seed"] == B.DEV_SEED
    assert sources["heldout"]["split"] == "test" and sources["heldout"]["seed"] != B.REGISTERED_TEST_SEED
    print("4a. sources: dev", sources["dev"]["content_hash"][:12], "seed", sources["dev"]["seed"],
          "| held-out", sources["heldout"]["content_hash"][:12], "seed", sources["heldout"]["seed"])

    # 2 and 3. prompts rebuilt from the child's builders; rule cards
    fx = {"dev": B.load(dev_path), "heldout": B.load(held_path)}
    cands = {name: {r["qid"]: r for r in f["cands"]} for name, f in fx.items()}
    by_id = {row["id"]: row for row in sample}
    checked = collections.Counter()
    cards = collections.Counter()
    for k in key:
        s, f = by_id[k["id"]], fx[k["build"]]
        corpus, cand = f["corpus"], cands[k["build"]][k["qid"]]
        assert s["type"] == k["type"]
        if k["type"] == "relation":
            a, b = B.ctx_of(corpus, cand["a"]), B.ctx_of(corpus, cand["b"])
            want = prompt_of(S1._frame(S1._rules([a, b]) + S1._prop("A", a)), S1._relation_question(a, b))
            ctxs = [a, b]
        elif k["type"] == "binding":
            ctx = B.ctx_of(corpus, cand["id"])
            want = prompt_of(S1._frame(S1._record(ctx)), S1._choice("status"))
            ctxs = [ctx]
        elif k["type"] == "group" and cand["source"] == "curated":
            members = [B.ctx_of(corpus, m) for m in cand["members"]]
            state = S1._frame(S1._rules(members) + S1.T["group_state"].format(listing=S1._listing(members)))
            want = prompt_of(state, S1._choice("kind")) + "\n\n" + B.NEUTRAL.format(n=len(members))
            ctxs = members
        else:  # reading
            members = cand["members"]
            u = B.ctx_of(corpus, members[0])
            options = [B.ctx_of(corpus, m) for m in members[1:]]
            letters = S1.ORDER_LETTERS[:len(options)]
            listing = "\n".join(S1.T["letter"].format(letter=lt, question=m["question"], label=m["label"])
                                for lt, m in zip(letters, options))
            state = S1._frame(S1._rules([u]) + S1.T["reading_state"].format(proposition=S1._prop("U", u), listing=listing))
            menu_ = {lt: S1.PROMPTS["reading"]["option"].format(letter=lt) for lt in letters}
            menu_["none"] = S1.PROMPTS["reading"]["none"]
            want = {"base": prompt_of(state, S1._choice("base_rules", criteria=menu_)),
                    "alt": prompt_of(state, S1._choice("alt_rules", criteria=menu_))}
            ctxs = [u]
        assert s["prompt"] == want, k["id"]
        checked[k["type"]] += 1

        # 3. rule cards: the outcome(s) asked about carry their card in every prompt text of the row (a binding: none)
        texts = list(want.values()) if isinstance(want, dict) else [want]
        for ctx in ctxs:
            card = (ctx.get("context") or "").strip()
            assert card, (k["id"], "a league without a rule card")
            for t in texts:
                if k["type"] == "binding":
                    assert card not in t and "League rules:" not in t, k["id"]
                else:
                    assert "League rules:\n" in t and card in t, (k["id"], "card missing")
            cards[k["type"]] += 1
    print("2. prompts equal the child-v6 rebuild:", dict(checked))
    print("3. rule cards: each asked outcome's card is in its relation, group and reading prompts, and not in binding "
          "prompts:", dict(cards), "outcomes checked")

    # 4b. designed counts, and the held-out families
    count = collections.Counter(k["part"] for k in key)
    want_parts = {"bindings": 30, "relations": 30, "groups": 30, "readings_ambiguous": 30, "readings_decoy": 30,
                  "heldout_relations": 30}
    assert dict(count) == want_parts, dict(count)
    assert all(max(k["families"]) > DEV_FAMILIES for k in key if k["part"] == "heldout_relations")
    assert all(max(k["families"]) <= DEV_FAMILIES for k in key if k["part"] == "relations")
    for part in ("relations", "heldout_relations"):
        assert collections.Counter(k["gold"] for k in key if k["part"] == part) == {c: 6 for c in B.CLASSES}, part
    assert collections.Counter(k["stratum"] for k in key if k["part"] == "bindings") == {"bound": 15, "unresolved": 15}
    assert collections.Counter(k["stratum"] for k in key if k["part"] == "groups") == {"real": 15, "decoy": 15}
    assert collections.Counter(k["stratum"] for k in key if k["part"].startswith("readings")) == \
        {"reading": 30, "reading_decoy": 30}
    assert len({(k["build"], k["qid"]) for k in key}) == len(key)  # no item sampled twice
    print("4b. counts:", dict(count), "| held-out families:",
          sorted({f for k in key if k["part"] == "heldout_relations" for f in k["families"]}))

    # 5. gold round trip through the scorer
    answers = [{"id": k["id"], "answer": gold_answer(k)} for k in key]
    results = SC.results(key, answers)
    bad = {part: sum(1 for _, ok in rows if not ok) for part, rows in results.items() if not all(ok for _, ok in rows)}
    assert not bad, bad
    print("5. gold round trip: every item correct in", len(results), "parts:", sorted(results))


if __name__ == "__main__":
    main()

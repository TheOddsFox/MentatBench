"""Blind answerability sample of Mentat-Hard v2, dev split: writes sample.jsonl and key.jsonl into this directory.

The prompt of each item is the wire text of the System One child v5. Each sampled item goes through the real child
(children.protocol.handle, which validates it, then SystemOneChild.decide) to the FakeOllama of tests/test_systemone_child.py
on 127.0.0.1. The state and question of each request the child sends are rendered as the prompt. No real server is contacted.
Read-only on the repo: bytecode is not written, and only this directory is written.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[3]  # the repository root
OUT = Path(__file__).resolve().parent
DEV = WORKTREE / "fixtures" / "mentat_hard_dev"
SEED = "audit:2026-10-29"
CLASSES = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")
PER_CLASS = 6
GROUP_PER_STRATUM = 15
BINDING_PER_STATUS = 15
READING_PER_STRATUM = 30
CANONICAL_KIND_ORDER = ("exactly_one", "ordered_chain", "at_least_k", "at_most_k", "at_most_one")

sys.dont_write_bytecode = True
sys.path[:0] = [str(WORKTREE / "python"), str(WORKTREE / "tests")]

from test_systemone_child import DIGEST, TAG, VERSION, FakeOllama  # noqa: E402

from mentat_bench.adapters.base import group_terms, terms_class  # noqa: E402
from mentat_bench.children import systemone_child as S1  # noqa: E402
from mentat_bench.children.protocol import handle  # noqa: E402

NEUTRAL = ("Answer all of these together: the kind (one of the options above); the lower bound, the fewest of the {n} outcomes "
           "that must hold (0 to {n}); the upper bound, the most of them that can hold (0 to {n}, where {n} means no limit); "
           "and, if the kind is ordered_chain, the outcome numbers in chain order, strongest first.")


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:  # LF on Windows too: the prompts stay byte-exact
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def canonical_kind(terms: list) -> str:
    """The suite's canonical label from the terms (base.group_terms order): exactly_one > ordered_chain > at_least_k >
    at_most_k > at_most_one; 'none' for no terms. Checked against gold/suite_labels.jsonl for every curated group."""
    kinds = [kind for kind, _ in terms]
    if not kinds:
        return "none"
    return next(k for k in CANONICAL_KIND_ORDER if k in kinds)


def ctx_of(corpus: dict[str, dict], oid: str) -> dict:
    """The child's outcome context, as harness.fixed._context builds it from a corpus record (cli: question = text)."""
    r = corpus[oid]
    return {"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r.get("event_title")}


def render(request: dict, name: str) -> str:
    """The prompt text of one question: the state the child sent, the question's instructions, then its options menu."""
    assert list(request["questions"]) == [name], (name, list(request["questions"]))
    q = request["questions"][name]
    menu = "\n".join(f"{key}: {text}" for key, text in q["criteria"].items())
    return f"{request['state']}\n\n{q['instructions']}\nOptions:\n{menu}"


def expect_state(text: str, want: str) -> None:
    assert text == want, "wire state differs from the state the child builds from the context"


def main() -> None:
    corpus = {r["id"]: r for r in read_jsonl(DEV / "corpus.jsonl")}
    labels = {r["qid"]: r for r in read_jsonl(DEV / "gold" / "suite_labels.jsonl")}
    cands = read_jsonl(DEV / "candidates.jsonl")

    # Pools (file order, so the seeded draws are deterministic).
    rel_pool: dict[str, list[dict]] = {c: [] for c in CLASSES}
    rel_excluded = 0
    group_pool: dict[str, list[dict]] = {"real": [], "decoy": []}
    bind_pool: dict[str, list[dict]] = {"bound": [], "unresolved": []}
    read_pool: dict[str, list[dict]] = {"reading": [], "reading_decoy": []}
    for row in cands:
        lab = labels[row["qid"]]
        if row["op"] == "decide_relation":
            a, b = ctx_of(corpus, row["a"]), ctx_of(corpus, row["b"])
            if len(S1._relation_question(a, b)["instructions"]) > S1.MAX_INSTRUCTION_CHARS:
                rel_excluded += 1  # the child refuses these as an item error: never sent, so no prompt exists
                continue
            rel_pool[lab["label"]].append({"qid": row["qid"], "a": a, "b": b, "suite": lab["stratum"]})
        elif row["op"] == "decide_binding":
            bind_pool[lab["label"]].append({"qid": row["qid"], "ctx": ctx_of(corpus, row["id"])})
        elif row["op"] == "decide_group" and row["source"] == "curated":
            terms = [list(t) for t in lab["terms"]]
            assert (lab["stratum"] == "real") == bool(terms), row["qid"]  # real: terms non-empty; decoy: terms []
            assert canonical_kind(terms) == lab["label"], row["qid"]
            members = [ctx_of(corpus, m) for m in row["members"]]
            group_pool[lab["stratum"]].append({"qid": row["qid"], "members": members, "terms": terms, "kind": lab["label"]})
        elif row["op"] == "decide_group" and row["source"] == "reading":
            assert len(row["members"]) == 5, row["qid"]
            members = row["members"]
            picks = {}
            for reading in ("base", "alt"):
                oid = lab["readings"][reading]
                picks[reading] = "none" if oid is None else S1.ORDER_LETTERS[members[1:].index(oid)]  # ValueError if absent
            read_pool[lab["stratum"]].append({"qid": row["qid"], "u": ctx_of(corpus, members[0]),
                                              "options": [ctx_of(corpus, m) for m in members[1:]], "gold": picks})

    counts_pool = {"relation": {c: len(v) for c, v in rel_pool.items()}, "group": {k: len(v) for k, v in group_pool.items()},
                   "binding": {k: len(v) for k, v in bind_pool.items()}, "reading": {k: len(v) for k, v in read_pool.items()}}
    print("pools", counts_pool, "relation rows excluded (instructions over the limit):", rel_excluded)

    # Sampling: one generator, seeded with SEED; then one shuffle across the types.
    rng = random.Random(SEED)
    picked: list[tuple[str, str, dict]] = []  # (type, stratum, item)
    for cls in CLASSES:
        picked += [("relation", cls, x) for x in rng.sample(rel_pool[cls], PER_CLASS)]
    for stratum in ("real", "decoy"):
        picked += [("group", stratum, x) for x in rng.sample(group_pool[stratum], GROUP_PER_STRATUM)]
    for status in ("bound", "unresolved"):
        picked += [("binding", status, x) for x in rng.sample(bind_pool[status], BINDING_PER_STATUS)]
    for stratum in ("reading", "reading_decoy"):
        picked += [("reading", stratum, x) for x in rng.sample(read_pool[stratum], READING_PER_STRATUM)]
    rng.shuffle(picked)

    # The real child, over the fake server: one request per question, the requests recorded by the fake.
    fake = FakeOllama()
    child = S1.SystemOneChild(TAG, fake.url, DIGEST, VERSION, parallel=1, timeout=10.0)
    sample: list[dict] = []
    key: list[dict] = []
    try:
        for index, (kind, stratum, x) in enumerate(picked, start=1):
            item_id = f"A{index:03d}"
            fake.requests.clear()
            if kind == "relation":
                a, b = x["a"], x["b"]
                reply = handle(child, json.dumps({"op": "decide_relation", "a": a, "b": b}))
                reqs = list(fake.requests)
                assert "error" not in reply and [list(r["questions"]) for r in reqs] == [["relation"]], reqs
                q = reqs[0]["questions"]["relation"]
                expect_state(reqs[0]["state"], S1._frame(S1._prop("A", a)))
                assert q["instructions"] == S1.T["relation_lead"].format(proposition=S1._prop("B", b)) + \
                    S1.PROMPTS["relation"]["instructions"]
                assert list(q["criteria"]) == list(S1.relation_order(a, b)) and set(q["criteria"]) == set(S1.RELATION_CHOICES)
                prompt = render(reqs[0], "relation")
                sample.append({"id": item_id, "type": "relation", "prompt": prompt,
                               "answer_format": '{"relation": "EQUIV" | "IMPLIES" | "IMPLIED_BY" | "EXCLUDES" | "NONE"}'})
                key.append({"id": item_id, "qid": x["qid"], "type": "relation", "stratum": stratum, "suite_stratum": x["suite"],
                            "gold": stratum})
            elif kind == "binding":
                ctx = x["ctx"]
                reply = handle(child, json.dumps({"op": "decide_binding", "ctx": ctx}))
                reqs = list(fake.requests)
                assert "error" not in reply and [list(r["questions"]) for r in reqs][:1] == [["status"]], reqs
                expect_state(reqs[0]["state"], S1._frame(S1._record(ctx)))
                assert reqs[0]["questions"]["status"]["instructions"] == S1.PROMPTS["status"]["instructions"]
                sample.append({"id": item_id, "type": "binding", "prompt": render(reqs[0], "status"),
                               "answer_format": '{"status": "bound" | "unresolved"}'})
                key.append({"id": item_id, "qid": x["qid"], "type": "binding", "stratum": stratum, "gold": stratum})
            elif kind == "group":
                members = x["members"]
                n = len(members)
                ctx = {"group_id": x["qid"], "source": "curated", "members": members}
                reply = handle(child, json.dumps({"op": "decide_group", "ctx": ctx}))
                reqs = list(fake.requests)
                assert "error" not in reply and [list(r["questions"]) for r in reqs][:1] == [["kind"]], reqs
                expect_state(reqs[0]["state"], S1._frame(S1.T["group_state"].format(listing=S1._listing(members))))
                assert list(reqs[0]["questions"]["kind"]["criteria"]) == list(S1.GROUP_CHOICES)
                prompt = render(reqs[0], "kind") + "\n\n" + NEUTRAL.format(n=n)
                fmt = ('{"kind": "none" | "exactly_one" | "at_most_one" | "at_least_k" | "at_most_k" | "ordered_chain", '
                       f'"lower": integer 0 to {n}, "upper": integer 0 to {n}, '
                       '"chain": [outcome numbers, strongest first; [] unless the kind is ordered_chain]}')
                sample.append({"id": item_id, "type": "group", "prompt": prompt, "answer_format": fmt})
                key.append({"id": item_id, "qid": x["qid"], "type": "group", "stratum": stratum, "gold": x["terms"],
                            "kind": x["kind"], "n": n})
            else:  # reading: the base and the alt question of one row
                u, options = x["u"], x["options"]
                ctx = {"group_id": x["qid"], "source": "reading", "members": [u] + options}
                reply = handle(child, json.dumps({"op": "decide_group", "ctx": ctx}))
                reqs = list(fake.requests)
                assert "error" not in reply and [list(r["questions"]) for r in reqs] == [["base"], ["alt"]], reqs
                letters = S1.ORDER_LETTERS[:len(options)]
                listing = "\n".join(S1.T["letter"].format(letter=lt, question=m["question"], label=m["label"])
                                    for lt, m in zip(letters, options))
                expect_state(reqs[0]["state"], S1._frame(S1.T["reading_state"].format(proposition=S1._prop("U", u),
                                                                                       listing=listing)))
                expect_state(reqs[1]["state"], reqs[0]["state"])
                prompt = {"base": render(reqs[0], "base"), "alt": render(reqs[1], "alt")}
                sample.append({"id": item_id, "type": "reading", "prompt": prompt,
                               "answer_format": '{"base": "A" | "B" | "C" | "D" | "none", "alt": "A" | "B" | "C" | "D" | "none"}'})
                key.append({"id": item_id, "qid": x["qid"], "type": "reading", "stratum": stratum, "gold": x["gold"]})
    finally:
        fake.close()

    write_jsonl(OUT / "sample.jsonl", sample)
    write_jsonl(OUT / "key.jsonl", key)
    tally: dict[str, int] = {}
    for k in key:
        tag = f"{k['type']}/{k['stratum']}"
        tally[tag] = tally.get(tag, 0) + 1
    print("sample rows", len(sample), "key rows", len(key))
    print(json.dumps(tally, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()

"""Blind item audit of the flagged relation and group items of the v0.1.1 analysis (fixtures/mentat_hard_test): writes
sample.jsonl, key.jsonl and sources.json into this directory. The sample rule is PREREG.md.

usage: python -B build_sample.py

The prompt of each item is the wire text of the unseeded System One child. Each sampled item goes through the real child
(children.protocol.handle, which validates it, then SystemOneChild.decide) to the FakeOllama of tests/test_systemone_child.py
on 127.0.0.1. The state (with the league rule card of each outcome asked about), the instructions and the options of the
request the child sends are rendered as the prompt, as in the frozen test-set-relations audit. No real server is contacted.
Read-only on the repo: bytecode is not written, and only this directory is written.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WORKTREE = Path(__file__).resolve().parents[3]  # the repository root
OUT = Path(__file__).resolve().parent
FIXTURE = WORKTREE / "fixtures" / "mentat_hard_test"
FROZEN = WORKTREE / "docs" / "audits" / "test-set-relations" / "build_sample.py"  # the frozen audit's helpers
SEED = "item-audit-v0.1.1"
PROMPT_SHA256 = "d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44"  # the pre-registered child
K_INVERTED, K_CONTROL = 127, 50
ALL_WRONG, INVERTED, CONTROL = "all_wrong", "inverted", "control"
GROUPS = (ALL_WRONG, INVERTED, CONTROL)
REL_ANSWER = '{"relation": "EQUIV" | "IMPLIES" | "IMPLIED_BY" | "EXCLUDES" | "NONE"}'

sys.dont_write_bytecode = True
sys.path[:0] = [str(WORKTREE / "python"), str(WORKTREE / "tests")]

from test_systemone_child import DIGEST, TAG, VERSION, FakeOllama  # noqa: E402

from mentat_bench.adapters.base import terms_class  # noqa: E402
from mentat_bench.children import systemone_child as S1  # noqa: E402
from mentat_bench.children.protocol import handle  # noqa: E402

# The frozen audit's helpers: the JSON lines I/O, the outcome context, the fixture loader, the wire render and the neutral
# group instruction. Loaded by path under its own name, so it is not confused with the frozen audit's build_sample module.
_spec = importlib.util.spec_from_file_location("test_set_relations_build_sample", FROZEN)
F = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(F)


def cell(item: dict[str, Any]) -> tuple[str, str, str]:
    """The stratification cell: op, gold class and stratum."""
    return (item["op"], item["gold_class"], item["stratum"])


def cell_name(c: tuple[str, str, str]) -> str:
    return "|".join(c)


def population(fx: dict[str, Any]) -> list[dict[str, Any]]:
    """The analysis's items: every decide_relation candidate and every curated decide_group candidate (readings are not items).
    gold is the key's gold (a relation label; a group's terms); gold_class is the release analysis's gold string."""
    corpus, labels = fx["corpus"], fx["labels"]
    items = []
    for row in fx["cands"]:
        lab = labels[row["qid"]]
        if row["op"] == "decide_relation":
            a, b = F.ctx_of(corpus, row["a"]), F.ctx_of(corpus, row["b"])
            assert len(S1._relation_question(a, b)["instructions"]) <= S1.MAX_INSTRUCTION_CHARS, row["qid"]  # never refused
            items.append({"qid": row["qid"], "op": row["op"], "gold": lab["label"], "gold_class": lab["label"],
                          "stratum": lab["stratum"], "a": a, "b": b})
        elif row["op"] == "decide_group" and row["source"] == "curated":
            terms = [tuple(t) for t in lab["terms"]]
            assert F.canonical_kind(terms) == lab["label"], row["qid"]
            assert (lab["stratum"] == "real") == bool(terms), row["qid"]  # real: terms non-empty; decoy: terms []
            members = [F.ctx_of(corpus, m) for m in row["members"]]
            items.append({"qid": row["qid"], "op": row["op"], "gold": [list(t) for t in terms], "gold_class": terms_class(terms),
                          "kind": lab["label"], "n": len(members), "stratum": lab["stratum"], "members": members})
    return items


def flagged_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows = F.read_jsonl(path)
    by_qid = {r["qid"]: r for r in rows}
    assert len(by_qid) == len(rows), "flagged.jsonl has a duplicate qid"
    return by_qid


def groups_of(items: list[dict[str, Any]], flagged: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """The population of each group: all_wrong (every flagged item that is all wrong), inverted (flagged inverted, not all
    wrong) and control (not flagged). Each flagged row must match its item on op, gold class and stratum."""
    assert set(flagged) <= {it["qid"] for it in items}, "a flagged qid is not an item"
    out: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUPS}
    for it in items:
        f = flagged.get(it["qid"])
        if f is None:
            out[CONTROL].append(it)
            continue
        assert (f["op"], f["gold"], f["stratum"]) == (it["op"], it["gold_class"], it["stratum"]), it["qid"]
        assert set(f["flags"]) <= {ALL_WRONG, INVERTED} and f["flags"], it["qid"]
        out[ALL_WRONG if ALL_WRONG in f["flags"] else INVERTED].append(it)
    return out


def allocate(pool: list[dict[str, Any]], k: int) -> dict[tuple[str, str, str], int]:
    """Proportional allocation of k over the cells of pool, by largest remainder: each cell gets the floor of its exact quota
    (k * N_c / M, in integers), then the seats left over go to the largest remainders, ties to the smaller cell."""
    size = Counter(cell(it) for it in pool)
    total = len(pool)
    base = {c: k * n // total for c, n in size.items()}
    left = k - sum(base.values())
    for c in sorted(size, key=lambda c: (-(k * size[c] % total), c))[:left]:
        base[c] += 1
    assert sum(base.values()) == k and all(base[c] <= size[c] for c in size)
    return dict(sorted(base.items()))


def draw(rng: random.Random, pool: list[dict[str, Any]], alloc: dict[tuple[str, str, str], int]) -> list[dict[str, Any]]:
    """A simple random sample without replacement within each cell, cells in key order, candidates in qid order."""
    by_cell: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for it in sorted(pool, key=lambda it: it["qid"]):
        by_cell[cell(it)].append(it)
    out: list[dict[str, Any]] = []
    for c in sorted(alloc):
        if alloc[c]:
            out += rng.sample(by_cell[c], alloc[c])
    return out


def select(groups: dict[str, list[dict[str, Any]]]) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, dict[str, int]]]:
    """The 600 sampled items in shuffled order, with the group of each, and the allocation of the two drawn groups."""
    rng = random.Random(SEED)
    alloc = {INVERTED: allocate(groups[INVERTED], K_INVERTED), CONTROL: allocate(groups[CONTROL], K_CONTROL)}
    chosen = [(ALL_WRONG, it) for it in sorted(groups[ALL_WRONG], key=lambda it: it["qid"])]
    chosen += [(INVERTED, it) for it in draw(rng, groups[INVERTED], alloc[INVERTED])]
    chosen += [(CONTROL, it) for it in draw(rng, groups[CONTROL], alloc[CONTROL])]
    rng.shuffle(chosen)
    return chosen, {g: {cell_name(c): n for c, n in a.items()} for g, a in alloc.items()}


def render_relation(child: S1.SystemOneChild, fake: FakeOllama, it: dict[str, Any]) -> str:
    a, b = it["a"], it["b"]
    fake.requests.clear()
    reply = handle(child, json.dumps({"op": "decide_relation", "a": a, "b": b}))
    reqs = list(fake.requests)
    assert "error" not in reply and [list(r["questions"]) for r in reqs] == [["relation"]], reqs
    F.expect_state(reqs[0]["state"], S1._frame(S1._rules([a, b]) + S1._prop("A", a)))
    q = reqs[0]["questions"]["relation"]
    assert q["instructions"] == S1.T["relation_lead"].format(proposition=S1._prop("B", b)) + S1.PROMPTS["relation"]["instructions"]
    assert list(q["criteria"]) == list(S1.relation_order(a, b)) and set(q["criteria"]) == set(S1.RELATION_CHOICES)
    return F.render(reqs[0], "relation")


def render_group(child: S1.SystemOneChild, fake: FakeOllama, it: dict[str, Any]) -> str:
    members, n = it["members"], it["n"]
    ctx = {"group_id": it["qid"], "source": "curated", "members": members}
    fake.requests.clear()
    reply = handle(child, json.dumps({"op": "decide_group", "ctx": ctx}))
    reqs = list(fake.requests)
    assert "error" not in reply and [list(r["questions"]) for r in reqs][:1] == [["kind"]], reqs
    F.expect_state(reqs[0]["state"], S1._frame(S1._rules(members) + S1.T["group_state"].format(listing=S1._listing(members))))
    assert list(reqs[0]["questions"]["kind"]["criteria"]) == list(S1.GROUP_CHOICES)
    return F.render(reqs[0], "kind") + "\n\n" + F.NEUTRAL.format(n=n)


def group_answer_format(n: int) -> str:
    return ('{"kind": "none" | "exactly_one" | "at_most_one" | "at_least_k" | "at_most_k" | "ordered_chain", '
            f'"lower": integer 0 to {n}, "upper": integer 0 to {n}, '
            '"chain": [outcome numbers, strongest first; [] unless the kind is ordered_chain]}')


def main() -> None:
    assert S1.PROMPT_SHA256 == PROMPT_SHA256, "the child's prompts are not the pre-registered ones"
    src = F.source(FIXTURE)
    assert (src["name"], src["split"], src["seed"], src["version"]) == ("mentat_hard_test", "test", 20261028, "3.1"), src
    src["path"] = "fixtures/mentat_hard_test"  # relative, so sources.json is the same on every checkout

    flagged_path = OUT / "flagged.jsonl"
    flagged_sha = hashlib.sha256(flagged_path.read_bytes()).hexdigest()
    flagged = flagged_rows(flagged_path)
    items = population(F.load(FIXTURE))
    assert len(items) == 7000 and sum(it["op"] == "decide_relation" for it in items) == 6000
    groups = groups_of(items, flagged)
    counts = {g: len(v) for g, v in groups.items()}
    assert counts == {ALL_WRONG: 423, INVERTED: 2539, CONTROL: 4038}, counts  # the pre-registered population sizes
    assert len(flagged) == 2962

    chosen, alloc = select(groups)
    assert Counter(g for g, _ in chosen) == {ALL_WRONG: 423, INVERTED: K_INVERTED, CONTROL: K_CONTROL}

    fake = FakeOllama()
    child = S1.SystemOneChild(TAG, fake.url, DIGEST, VERSION, parallel=1, timeout=10.0)  # unseeded: option_seed None
    sample: list[dict[str, Any]] = []
    key: list[dict[str, Any]] = []
    try:
        for index, (group, it) in enumerate(chosen, start=1):
            item_id = f"A{index:03d}"
            if it["op"] == "decide_relation":
                prompt = render_relation(child, fake, it)
                sample.append({"id": item_id, "type": "relation", "prompt": prompt, "answer_format": REL_ANSWER})
                key.append({"id": item_id, "qid": it["qid"], "op": it["op"], "group": group, "stratum": it["stratum"],
                            "gold": it["gold"], "gold_class": it["gold_class"]})
            else:
                prompt = render_group(child, fake, it)
                sample.append({"id": item_id, "type": "group", "prompt": prompt, "answer_format": group_answer_format(it["n"])})
                key.append({"id": item_id, "qid": it["qid"], "op": it["op"], "group": group, "stratum": it["stratum"],
                            "gold": it["gold"], "gold_class": it["gold_class"], "kind": it["kind"], "n": it["n"]})
    finally:
        fake.close()

    F.write_jsonl(OUT / "sample.jsonl", sample)
    F.write_jsonl(OUT / "key.jsonl", key)
    sources = {"sample_seed": SEED,
               "fixture": src,
               "flagged": {"path": "docs/audits/v0.1.1-item-audit/flagged.jsonl", "sha256": flagged_sha, "rows": len(flagged)},
               "child": {"module": "python/mentat_bench/children/systemone_child.py", "prompt_sha256": PROMPT_SHA256,
                         "option_seed": None},
               "population": {"items": len(items), "relation": 6000, "group": 1000, **counts, "flagged": len(flagged)},
               "allocation": alloc}
    (OUT / "sources.json").write_text(json.dumps(sources, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("sample rows", len(sample), "key rows", len(key), "by group", dict(Counter(r["group"] for r in key)))


if __name__ == "__main__":
    main()

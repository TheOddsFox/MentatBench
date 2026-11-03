"""Reasoning reference sample of MentatBench v0.2.0 (fixtures/mentat_hard_test, version 3.1): writes sample.jsonl, key.jsonl and
slices.json into this directory. The sample rule is PREREG.md.

usage: python -B build_sample.py

The prompt of each item is the wire text of the unseeded System One child, captured by the v0.1.1 item audit's render functions
(docs/audits/v0.1.1-item-audit/build_sample.py): each sampled item goes through the real child (children.protocol.handle) to the
FakeOllama of tests/test_systemone_child.py on 127.0.0.1. No real server is contacted. Read-only on the repo: bytecode is not
written, and only this directory is written. Re-running gives byte-identical files.
"""

from __future__ import annotations

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
ITEM_AUDIT = WORKTREE / "docs" / "audits" / "v0.1.1-item-audit" / "build_sample.py"  # the prompt capture and rendering
SEED = "reasoning-reference-v0.2.0"  # random.Random(SEED): the one generator of the draws and the shuffle
FIXTURE_HASH = "7b408459538d7420e808d5d81e71916c57aee78f459a6b8c0905e48e43c8e6ab"
PROMPT_SHA256 = "d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44"  # the child's, as every v0.2.0 cell records
N_RELATIONS, N_GROUPS = 300, 200
RELATION_FLOOR, GROUP_FLOOR = 5, 4  # at least this many per cell (or the whole cell when smaller)
POOLED = "relation|pooled|4+"  # the depth-4+ relations: one cell, all taken
BUDGET_CHARS = 120_000  # a slice's prompt characters (the sum of its prompts' lengths) stay under this
MAX_ITEMS = 50  # a slice holds at most this many items
POPULATION = {"decide_relation": 6000, "decide_group": 1000}

sys.dont_write_bytecode = True
sys.path[:0] = [str(WORKTREE / "python"), str(WORKTREE / "tools" / "eval")]

from mentat_bench.adapters.base import terms_class  # noqa: E402

import league_ci as LC  # noqa: E402

_spec = importlib.util.spec_from_file_location("item_audit_v0_1_1_build_sample", ITEM_AUDIT)
V1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V1)  # the v0.1.1 audit's population and render functions, loaded by path
F, S1 = V1.F, V1.S1


def bucket(it: dict[str, Any]) -> str:
    """The depth bucket of a relation (0, 1, 2, 3, 4+, or NONE for a gold NONE) and the set-size bucket of a group (2, 3, 4-8)."""
    if it["op"] == "decide_relation":
        d = it["depth"]
        return "NONE" if d is None else "4+" if d >= 4 else str(d)
    return "2" if it["n"] == 2 else "3" if it["n"] == 3 else "4-8"


def cell_of(it: dict[str, Any]) -> str:
    """Relations: stratum x gold label x depth bucket, the depth-4+ items pooled. Groups: gold kind with k dropped x size bucket
    (decoys have kind none)."""
    if it["op"] == "decide_relation":
        return POOLED if bucket(it) == "4+" else f"relation|{it['stratum']}|{it['gold']}|{bucket(it)}"
    kind = "+".join(term[0] for term in it["gold"]) or "none"
    return f"group|{kind}|{bucket(it)}"


def allocate(sizes: dict[str, int], k: int, floor: int) -> dict[str, int]:
    """Each cell gets min(N_c, floor). The k - sum seats left are shared over the cells in proportion to their population left
    (N_c minus the floor), by largest remainder in integer arithmetic, ties to the smaller cell key."""
    base = {c: min(n, floor) for c, n in sizes.items()}
    seats = k - sum(base.values())
    rest = {c: sizes[c] - base[c] for c in sizes}
    total = sum(rest.values())
    assert 0 <= seats <= total, (seats, total)
    extra = {c: seats * rest[c] // total for c in sizes}
    for c in sorted(sizes, key=lambda c: (-(seats * rest[c] % total), c))[:seats - sum(extra.values())]:
        extra[c] += 1
    alloc = {c: base[c] + extra[c] for c in sorted(sizes)}
    assert sum(alloc.values()) == k and all(alloc[c] <= sizes[c] for c in sizes), alloc
    return alloc


def draw(rng: random.Random, pool: list[dict[str, Any]], alloc: dict[str, int]) -> list[dict[str, Any]]:
    """A simple random sample without replacement within each cell, cells in key order, candidates in qid order."""
    by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in sorted(pool, key=lambda it: it["qid"]):
        by_cell[cell_of(it)].append(it)
    out: list[dict[str, Any]] = []
    for c in sorted(alloc):
        if alloc[c]:
            out += rng.sample(by_cell[c], alloc[c])
    return out


def select(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """The 500 sampled items in shuffled order, and each cell's population, sample size and weight."""
    rels = [it for it in items if it["op"] == "decide_relation"]
    grps = [it for it in items if it["op"] == "decide_group"]
    pooled = [it for it in rels if cell_of(it) == POOLED]
    rel_sizes = Counter(cell_of(it) for it in rels if cell_of(it) != POOLED)
    grp_sizes = Counter(cell_of(it) for it in grps)
    rel_alloc = allocate(rel_sizes, N_RELATIONS - len(pooled), RELATION_FLOOR)
    grp_alloc = allocate(grp_sizes, N_GROUPS, GROUP_FLOOR)
    rng = random.Random(SEED)
    chosen = sorted(pooled, key=lambda it: it["qid"])
    chosen += draw(rng, [it for it in rels if cell_of(it) != POOLED], rel_alloc)
    chosen += draw(rng, grps, grp_alloc)
    rng.shuffle(chosen)
    sizes = {**dict(rel_sizes), **dict(grp_sizes), POOLED: len(pooled)}
    alloc = {**rel_alloc, **grp_alloc, POOLED: len(pooled)}
    cells = {c: {"op": "decide_relation" if c.startswith("relation") else "decide_group", "population": sizes[c],
                 "sample": alloc[c], "weight": sizes[c] / alloc[c]} for c in sorted(sizes)}
    return chosen, cells


def cut(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Consecutive slices in the sample's order: a slice closes at MAX_ITEMS items or before the item that would take its
    prompt characters over BUDGET_CHARS."""
    slices: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for row in rows:
        chars = len(row["prompt"])
        assert chars <= BUDGET_CHARS, row["id"]
        if current and (len(current) == MAX_ITEMS or size + chars > BUDGET_CHARS):
            slices.append(current)
            current, size = [], 0
        current.append(row)
        size += chars
    return slices + ([current] if current else [])


def main(out: Path = OUT) -> None:
    assert S1.PROMPT_SHA256 == PROMPT_SHA256, "the child's prompts are not the pre-registered ones"
    src = F.source(FIXTURE)
    assert (src["name"], src["split"], src["seed"], src["version"]) == ("mentat_hard_test", "test", 20261028, "3.1"), src
    assert src["content_hash"] == FIXTURE_HASH, src["content_hash"]
    src["path"] = "fixtures/mentat_hard_test"  # relative, so the file is the same on every checkout

    fx = F.load(FIXTURE)
    items = V1.population(fx)
    for it in items:
        it["depth"] = fx["labels"][it["qid"]].get("depth")
    assert Counter(it["op"] for it in items) == POPULATION, Counter(it["op"] for it in items)
    leagues = LC.read_fixture(FIXTURE)
    for it in items:
        it["league"] = leagues["leagues"][leagues["item_league"][it["qid"]]]

    chosen, cells = select(items)
    assert len(chosen) == N_RELATIONS + N_GROUPS
    assert sum(c["sample"] for c in cells.values() if c["op"] == "decide_relation") == N_RELATIONS
    assert sum(c["sample"] for c in cells.values() if c["op"] == "decide_group") == N_GROUPS

    fake = V1.FakeOllama()
    child = S1.SystemOneChild(V1.TAG, fake.url, V1.DIGEST, V1.VERSION, parallel=1, timeout=10.0)  # unseeded: option_seed None
    sample: list[dict[str, Any]] = []
    key: list[dict[str, Any]] = []
    try:
        for index, it in enumerate(chosen, start=1):
            item_id = f"H{index:03d}"
            cell = cell_of(it)
            if it["op"] == "decide_relation":
                prompt = V1.render_relation(child, fake, it)
                sample.append({"id": item_id, "type": "relation", "prompt": prompt, "answer_format": V1.REL_ANSWER})
            else:
                prompt = V1.render_group(child, fake, it)
                sample.append({"id": item_id, "type": "group", "prompt": prompt,
                               "answer_format": V1.group_answer_format(it["n"])})
            gold = it["gold"] if it["op"] == "decide_relation" else terms_class([tuple(t) for t in it["gold"]])
            key.append({"id": item_id, "qid": it["qid"], "op": it["op"], "gold": gold, "weight": cells[cell]["weight"],
                        "cell": cell, "league": it["league"], "n_members": None if it["op"] == "decide_relation" else it["n"],
                        "stratum": it["stratum"], "bucket": bucket(it)})
    finally:
        fake.close()

    slices = cut(sample)
    by_id = {row["id"]: row for row in sample}
    slice_rows = []
    for j, rows in enumerate(slices, start=1):
        slice_rows.append({"slice": j, "ids": [r["id"] for r in rows], "items": len(rows),
                           "relations": sum(r["type"] == "relation" for r in rows),
                           "groups": sum(r["type"] == "group" for r in rows),
                           "prompt_chars": sum(len(by_id[r["id"]]["prompt"]) for r in rows)})
    assert sorted(i for s in slice_rows for i in s["ids"]) == sorted(by_id)

    write_jsonl(out / "sample.jsonl", sample)
    write_jsonl(out / "key.jsonl", key)
    doc = {"schema": "mentat-bench-reasoning-slices/1", "sample_seed": SEED, "budget_chars": BUDGET_CHARS,
           "max_items": MAX_ITEMS, "slices": slice_rows, "cells": cells,
           "population": POPULATION, "fixture": src,
           "child": {"module": "python/mentat_bench/children/systemone_child.py", "prompt_sha256": PROMPT_SHA256,
                     "option_seed": None}}
    (out / "slices.json").write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8",
                                     newline="\n")
    print("sample rows", len(sample), "key rows", len(key), "relations", sum(r["type"] == "relation" for r in sample),
          "groups", sum(r["type"] == "group" for r in sample))
    print("slices", len(slice_rows), [(s["items"], s["prompt_chars"]) for s in slice_rows])


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:  # LF on Windows too: the prompts stay byte-exact
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

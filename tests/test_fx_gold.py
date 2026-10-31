"""Semantic tests for the toy10k generator and its gold theory. No engine and no catalogue are needed.

The formal world of fixtures_gen (score thresholds, one winner per group, cardinality per group) is compared
with the gold constraints, both on sampled worlds and on single-bit perturbations of them.
"""

import itertools
import os
import random
from collections import Counter
from pathlib import Path

import pytest

from mentat_bench import fixtures_gen as fg
from mentat_bench import ir

REPO = Path(__file__).resolve().parents[1]
TOY10K = Path(os.environ.get("MB_FIXTURE_TOY10K", REPO / "fixtures" / "toy10k"))


@pytest.fixture(scope="module")
def base_records():
    return fg._records(fg.SEED, fg.BASE_SEASONS)


@pytest.mark.parametrize("n1, n2, sx, sy, rel", [
    (False, False, False, False, "IMPLIES"),  # cx => cy
    (True, False, False, False, "OR"),        # not cx or cy
    (False, True, False, False, "EXCLUDES"),  # not (cx and not-cy) with the second record negated
    (True, True, False, False, "IMPLIED_BY"),  # cy => cx
    (False, False, True, True, "IMPLIED_BY"),  # not-cx => not-cy, i.e. cy => cx
])
def test_clause_relation_table(n1, n2, sx, sy, rel):
    assert fg._clause_relation(n1, n2, sx, sy) == rel


def test_pair_expressions_match_their_relation():
    assert fg._pair_expr("IMPLIES", "a", "b") == {"op": "implies", "a": ir.var("a"), "b": ir.var("b")}
    assert fg._pair_expr("IMPLIED_BY", "a", "b") == {"op": "implies", "a": ir.var("b"), "b": ir.var("a")}
    assert fg._pair_expr("EXCLUDES", "a", "b") == {"op": "atmost", "k": 1, "args": [ir.var("a"), ir.var("b")]}
    assert fg._pair_expr("EQUIV", "a", "b") == {"op": "iff", "a": ir.var("a"), "b": ir.var("b")}


def test_gold_constraint_structure(base_records):
    constraints = fg._gold_constraints(base_records)
    ids = [c["id"] for c in constraints]
    assert ids == [f"g{n:06d}" for n in range(len(ids))], "constraint IDs are sequential"
    cards = [c for c in constraints if c["expr"]["op"] in ("exactly", "atleast", "atmost") and len(c["expr"]["args"]) == 4]
    # Per (season, group): exactly 1 winner, atleast 2 with T5, atmost 2 with T6.
    assert Counter(c["expr"]["op"] + str(c["expr"]["k"]) for c in cards) == {"exactly1": 48, "atleast2": 48, "atmost2": 48}
    scoped = [c for c in constraints if c["interps"] is not None]
    assert {tuple(c["interps"]) for c in scoped} == {("base",), ("alt",)}
    assert len(scoped) == 200  # two scoped iffs per dated Yes
    for c in constraints:
        assert set(c) == {"id", "expr", "interps"}


def _eval(expr, w):
    op = expr["op"]
    if op == "true":
        return True
    if op == "false":
        return False
    if op == "var":
        return w[expr["id"]]
    if op == "not":
        return not _eval(expr["a"], w)
    if op == "and":
        return all(_eval(a, w) for a in expr["args"])
    if op == "or":
        return any(_eval(a, w) for a in expr["args"])
    if op == "implies":
        return (not _eval(expr["a"], w)) or _eval(expr["b"], w)
    if op == "iff":
        return _eval(expr["a"], w) == _eval(expr["b"], w)
    if op in ("atmost", "atleast", "exactly"):
        k = sum(1 for a in expr["args"] if _eval(a, w))
        return {"atmost": k <= expr["k"], "atleast": k >= expr["k"], "exactly": k == expr["k"]}[op]
    raise ValueError(op)


def _formal_group(T, W):
    """The formal world of one (season, group): chain axiom, win axiom, exactly one winner, cardinalities."""
    chain = all(not T[i, t + 1] or T[i, t] for i in range(4) for t in range(1, 8))
    wins = all(not W[i] or T[i, 5] for i in range(4))
    one = sum(W.values()) == 1
    at_least = sum(T[i, 5] for i in range(4)) >= 2
    at_most = sum(T[i, 6] for i in range(4)) <= 2
    return chain and wins and one and at_least and at_most


def test_gold_group_matches_the_formal_world_on_sampled_and_perturbed_worlds(base_records):
    """Season 0, group A (four entities). Gold accepts exactly the formal worlds, including after one bit flip."""
    group = [r for r in base_records if r["si"] == 0 and r["i"] < 4 and r["kind"] in ("thr", "win")]
    group_ids = {r["id"] for r in group}
    constraints = [c for c in fg._gold_constraints(base_records)
                   if c["interps"] is None and ir.expr_vars(c["expr"]) <= group_ids]
    assert len(constraints) > 100
    rng = random.Random(7)
    accepted = rejected = 0
    for trial in range(1500):
        scores = [rng.randint(0, 8) for _ in range(4)]
        T = {(i, t): scores[i] >= t for i in range(4) for t in range(1, 9)}
        winner = rng.randrange(4)
        W = {i: i == winner for i in range(4)}
        if trial % 2:  # perturb one class bit: the formal verdict must still be reproduced exactly
            if rng.random() < 0.5:
                key = rng.choice(list(T))
                T[key] = not T[key]
            else:
                key = rng.randrange(4)
                W[key] = not W[key]
        cls_value = {("thr", i, t): T[i, t] for (i, t) in T}
        cls_value.update({("win", i): W[i] for i in W})
        w = {}
        for r in group:
            key = ("thr", r["i"], r["t"]) if r["kind"] == "thr" else ("win", r["i"])
            w[r["id"]] = cls_value[key] != r["neg"]
        gold_ok = all(_eval(c["expr"], w) for c in constraints)
        formal_ok = _formal_group(T, W)
        assert gold_ok == formal_ok, (trial, scores, winner)
        accepted += gold_ok
        rejected += not gold_ok
    assert accepted > 0 and rejected > 0


def test_threshold_classes_are_nested_per_entity_in_gold(base_records):
    """T(k,t+1) => T(k,t) is present for every entity and threshold as a pairwise implication."""
    constraints = fg._gold_constraints(base_records)
    pairs = set()
    for c in constraints:
        e = c["expr"]
        if e["op"] == "implies" and c["interps"] is None:
            pairs.add((e["a"]["id"], e["b"]["id"]))
    by_cls = {}
    for r in base_records:
        if r["cls"] is not None:
            by_cls.setdefault(r["cls"], []).append(r["id"])
    for si in range(fg.BASE_SEASONS):
        for i in range(len(fg.TEAMS)):
            for t in range(1, 8):
                hi, lo = fg.thr_class(si, i, t + 1), fg.thr_class(si, i, t)
                assert any((a, b) in pairs or (b, a) in pairs for a in by_cls[hi] for b in by_cls[lo]), (si, i, t)


def _formal_worlds():
    """Every valid formal world of one group, over its four entities (relative index 0..3)."""
    for scores in itertools.product(range(9), repeat=4):
        T = {(i, t): scores[i] >= t for i in range(4) for t in range(1, 9)}
        for winner in range(4):
            W = {i: i == winner for i in range(4)}
            if _formal_group(T, W):
                yield T, W


def _literal(expr):
    if expr["op"] == "var":
        return expr["id"], True
    assert expr["op"] == "not" and expr["a"]["op"] == "var", expr
    return expr["a"]["id"], False


@pytest.mark.skipif(not (TOY10K / "queries.jsonl").exists(), reason="toy10k fixture not generated")
def test_closure_hypotheses_are_satisfiable_in_every_interpretation(base_records):
    """Spec s6A: a closure hypothesis must hold in every reference interpretation (base and alt). Literals of
    different (season, group) pairs are independent, so it is enough to check each group's literals against all
    formal worlds of that group, with dated propositions read under each interpretation."""
    by_id = {r["id"]: r for r in base_records}
    worlds = list(_formal_worlds())
    assert len(worlds) > 1000
    checked = 0
    for q in ir.read_jsonl(TOY10K / "queries.jsonl"):
        if q["family"] != "closure":
            continue
        lits = [_literal(h) for h in q["hyp"] if h["op"] != "true"]
        groups = {}
        for oid, positive in lits:
            r = by_id[oid]
            groups.setdefault((r["si"], r["i"] // 4), []).append((r, positive))
        for key, group_lits in groups.items():
            for interp in fg.GOLD_INTERPRETATIONS:
                ok = False
                for T, W in worlds:
                    if all(_record_value(r, T, W, interp) == positive for r, positive in group_lits):
                        ok = True
                        break
                assert ok, (q["qid"], key, interp)
        checked += 1
    assert checked == 10


def _record_value(r, T, W, interp):
    """Truth value of one outcome in a formal world (relative entity index i % 4)."""
    i = r["i"] % 4
    if r["kind"] == "thr":
        return T[i, r["t"]] != r["neg"]
    if r["kind"] == "win":
        return W[i] != r["neg"]
    base = T[i, r["t"]]  # dated: "base" ties it to T(k,t), "alt" to not T(k,t)
    value = base if interp == "base" else not base
    return value if r["label"] == "Yes" else not value

"""PB4-PB7 (harness side): market-level blocking, streamed usage, class-parity checks, the engine base-consistency
probe, blocking recall reporting and positive-int validation. Engine-dependent tests skip without mb-engine."""

import itertools
import json
import random

import pytest

from mentat_bench import engine, ir
from mentat_bench.adapters.base import usage
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import _ClassParity, candidate_pairs
from mentat_bench.manifest import DEFAULT_PROFILE

HAVE_ENGINE = engine.engine_available()


class Scripted:
    """Binding decisions from a dict; relation decisions from a dict keyed by (a_id, b_id) in the order the harness asks
    (lower record index first); unknown pairs are NONE. Costs are configurable."""

    name = "scripted"

    def __init__(self, bindings, relations=None, cost_relation=0.0):
        self.bindings, self.relations, self.cost_relation = bindings, relations or {}, cost_relation

    def decide_binding(self, ctx):
        return self.bindings[ctx["id"]], usage("scripted", "local")

    def decide_relation(self, a, b):
        return self.relations.get((a["id"], b["id"]), "NONE"), usage("scripted", "local", cost_usd=self.cost_relation)


def _unresolved(oid):
    return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}


def _bound(cls, negated=False):
    return {"status": "bound", "class": cls, "negated": negated, "missing": []}


def _recs(spec):
    """spec: list of (outcome_id, question)."""
    return [{"id": oid, "question": q, "label": "Yes", "event_title": None} for oid, q in spec]


# PB4: blocking


def test_sibling_outcomes_are_candidates_and_do_not_inflate_market_frequencies():
    # Market m1 has two outcomes with the same question; market m2 shares its question tokens.
    recs = _recs([("v:m1:1", "alpha beta gamma"), ("v:m1:2", "alpha beta gamma"), ("v:m2:1", "alpha beta delta")])
    pairs = candidate_pairs(recs)
    assert (0, 1) in pairs  # siblings
    assert (0, 2) in pairs and (1, 2) in pairs  # both outcomes of m1 against m2
    assert pairs == sorted(set(pairs)) and all(i < j for i, j in pairs)


def test_relative_cutoff_keeps_a_token_that_an_absolute_cap_of_1000_would_drop():
    # 5000 markets; "teamx" appears in every fourth one (1250 markets): above the old absolute cap of 1000,
    # below 0.3 * 5000. Markets that share it must link.
    recs = _recs([(f"v:m{i}:1", ("teamx " if i % 4 == 0 else "") + f"unique{i} word") for i in range(5000)])
    pairs = set(candidate_pairs(recs, max_df=4000))
    assert (0, 4) in pairs and (4, 8) in pairs
    assert (0, 4) not in set(candidate_pairs(recs, max_df=1000))


def test_tiny_corpora_keep_shared_tokens_under_the_floor():
    recs = _recs([("v:m1:1", "p1 alpha"), ("v:m2:1", "p1 beta"), ("v:m3:1", "solo zeta")])
    assert candidate_pairs(recs) == [(0, 1)]


def test_candidate_pairs_are_deterministic_for_records_without_ids():
    recs = [{"question": f"shared tok{i % 3}"} for i in range(9)]
    assert candidate_pairs(recs) == candidate_pairs(recs)
    assert candidate_pairs(recs)  # no ID: each record is its own unit


def test_positive_int_arguments_are_validated(tmp_path):
    recs = _recs([("v:m1:1", "ab cd")])
    for kwargs in ({"checkpoint_every": 0}, {"max_df": 0}, {"checkpoint_every": True}, {"max_df": -4}):
        with pytest.raises(ValueError):
            fixed.build(recs, Scripted({"v:m1:1": _unresolved("v:m1:1")}), tmp_path / "sub", dict(DEFAULT_PROFILE), **kwargs)
    assert not (tmp_path / "sub").exists()


def test_duplicate_or_malformed_outcome_id_fails_before_any_model_call(tmp_path):
    calls = []

    class Counting(Scripted):
        def decide_binding(self, ctx):
            calls.append(ctx["id"])
            return super().decide_binding(ctx)

    recs = _recs([("v:m1:1", "ab cd"), ("v:m1:1", "ab cd")])
    with pytest.raises(ValueError, match="duplicate"):
        fixed.build(recs, Counting({"v:m1:1": _bound("X")}), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert calls == ["v:m1:1"]  # the second record is rejected at ingest; nothing is written
    assert not (tmp_path / "sub").exists() and (tmp_path / "sub.build" / "usage_calls.jsonl").exists()  # charged calls: kept
    with pytest.raises(ValueError, match="malformed"):
        fixed.build(_recs([("nocolon", "ab cd")]), Counting({}), tmp_path / "sub2", dict(DEFAULT_PROFILE))


def test_blocking_recall_is_reported_against_supplied_gold_pairs(tmp_path):
    recs = _recs([("v:m1:1", "alpha team x"), ("v:m2:1", "alpha team y"), ("v:m3:1", "zeta only")])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    relations = {("v:m1:1", "v:m2:1"): "IMPLIES"}
    # m3 is unreachable; m9 is unknown. The first pair is given in reverse order: the match must not depend on it.
    gold = [("v:m2:1", "v:m1:1"), ("v:m1:1", "v:m3:1"), ("v:m1:1", "v:m9:1")]
    summary = fixed.build(recs, Scripted(bindings, relations), tmp_path / "sub", dict(DEFAULT_PROFILE), gold_pairs=gold)
    assert summary["gold_pairs"] == 2  # the unknown outcome is ignored
    assert summary["blocking_recall"] == pytest.approx(0.5)
    assert fixed.build(recs, Scripted(bindings), tmp_path / "sub2", dict(DEFAULT_PROFILE))["blocking_recall"] is None


# PB5: usage is streamed, the gateway never holds the call list


def test_usage_calls_are_written_as_they_happen_and_the_gateway_list_stays_empty(tmp_path, monkeypatch):
    n = 2500  # more than USAGE_FLUSH_EVERY, so flushes happen mid-build
    recs = _recs([(f"v:m{i}:1", f"filler{i} w{i}") for i in range(n)])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    holder, seen = {}, []
    real_gateway = fixed.Gateway

    class Probe(real_gateway):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            holder["gw"] = self

    class Watching(Scripted):
        def decide_binding(self, ctx):
            seen.append(len(holder["gw"].calls))  # calls the gateway still holds when the next decision starts
            return super().decide_binding(ctx)

    monkeypatch.setattr(fixed, "Gateway", Probe)
    summary = fixed.build(recs, Watching(bindings), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert max(seen) == 0
    assert summary["calls"] == n
    usage_doc = json.loads((tmp_path / "sub" / "usage.json").read_text(encoding="utf-8"))
    assert len(usage_doc["calls"]) == n
    assert not (tmp_path / "sub.build").exists()


# PB6: class parity checked up front, and the engine base-consistency probe


def _single_class_accepts(state, rel, i_neg, j_neg):
    """Brute force over one class variable X: is the accepted set plus this relation still satisfiable?
    Outcome meanings are X XOR negation flag."""
    def holds(x, r, negi, negj):
        a, b = x ^ negi, x ^ negj
        if r == "EQUIV":
            return a == b
        if r == "IMPLIES":
            return (not a) or b
        if r == "IMPLIED_BY":
            return (not b) or a
        return not (a and b)  # EXCLUDES

    cons = state + [(i_neg, j_neg, rel)]
    return any(all(holds(x, r, ni, nj) for ni, nj, r in cons) for x in (False, True))


def test_class_parity_rejects_exactly_the_relations_that_make_the_class_unsatisfiable():
    rng = random.Random(3)
    for _ in range(3000):
        classes = _ClassParity()
        state = []
        for _ in range(rng.randint(1, 6)):
            rel = rng.choice(["EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES"])
            ni, nj = rng.random() < 0.5, rng.random() < 0.5
            expected = _single_class_accepts(state, rel, ni, nj)
            if rel == "EQUIV":
                got = classes.equiv("X", "X", ni != nj)
            else:
                got = classes.one_sided("X", ni, "X", nj, rel)
            assert got == expected, (rel, ni, nj, state)
            if got:
                state.append((ni, nj, rel))


def test_class_parity_forced_values_follow_a_merge():
    classes = _ClassParity()
    assert classes.one_sided("A", False, "A", True, "IMPLIES")  # A (positive) => not-A forces A false
    assert classes.equiv("B", "A", False)  # B == A: the forced value moves to the merged root
    assert not classes.one_sided("B", False, "B", True, "IMPLIED_BY")  # not-B => B forces B true: conflicts
    assert classes.equiv("C", "A", True)  # merging a class without a forced value still works


def test_engine_probe_is_skipped_with_a_warning_when_mb_engine_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: False)
    recs = _recs([("v:m1:1", "ab cd"), ("v:m2:1", "ab cd")])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    with pytest.warns(UserWarning, match="skipped"):
        summary = fixed.build(recs, Scripted(bindings, {("v:m1:1", "v:m2:1"): "EXCLUDES"}), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["base_probe"] == "skipped" and summary["relations_dropped"] == 0 and summary["constraints"] == 1


@pytest.mark.skipif(not HAVE_ENGINE, reason="mb-engine not built")
def test_engine_probe_drops_the_relation_that_makes_the_base_inconsistent(tmp_path):
    # X => Y, Y => not X and not-X => X: each relation alone is satisfiable, the three together are not.
    # The same-class pair (A, E) is checked up front; the cross-class cycle is found by the engine probe.
    recs = _recs([("v:m1:1", "same"), ("v:m2:1", "same"), ("v:m3:1", "same")])
    bindings = {"v:m1:1": _bound("X"), "v:m2:1": _bound("X", negated=True), "v:m3:1": _bound("Y")}
    relations = {
        ("v:m1:1", "v:m2:1"): "IMPLIED_BY",  # m2 (not X) => m1 (X): forces X true
        ("v:m1:1", "v:m3:1"): "IMPLIES",  # X => Y
        ("v:m2:1", "v:m3:1"): "IMPLIED_BY",  # Y => not X
    }
    summary = fixed.build(recs, Scripted(bindings, relations), tmp_path / "sub", dict(DEFAULT_PROFILE), max_relation_drop_frac=1.0)
    assert summary["base_probe"] == "engine"  # the engine confirms the base the in-process check repaired
    assert summary["relations_dropped"] == 1 and summary["constraints"] == 2
    digest = engine.eval_submission(tmp_path / "sub", _empty(tmp_path), tmp_path / "answers.jsonl")
    # The five required digests; profile.json is listed too when the submission carries one.
    assert {"theory.json", "bindings.jsonl", "provenance.jsonl", "queries", "answers"} <= set(digest["mb_engine_digests"])


def _empty(tmp_path):
    p = tmp_path / "empty_queries.jsonl"
    p.write_text("", encoding="utf-8")
    return p


def test_recovered_build_is_finalized_without_probe_when_asked(tmp_path):
    recs = _recs([("v:m1:1", "ab cd"), ("v:m2:1", "ab cd"), ("v:m3:1", "ab cd")])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    work = tmp_path / "sub.build"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(recs, _InterruptSecond(bindings), tmp_path / "sub", dict(DEFAULT_PROFILE), checkpoint_every=1)
    summary = fixed.finalize_checkpoint(work, tmp_path / "recovered", dict(DEFAULT_PROFILE), probe=False)
    assert summary["base_probe"] == "not_probed" and summary["constraints"] == 1  # the first decision is checkpointed


class _InterruptSecond(Scripted):
    def __init__(self, bindings):
        super().__init__(bindings, {("v:m1:1", "v:m2:1"): "EXCLUDES"})
        self.asked = 0

    def decide_relation(self, a, b):
        self.asked += 1
        if self.asked == 2:
            raise KeyboardInterrupt  # hard kill after one committed batch
        return super().decide_relation(a, b)


def test_siblings_are_candidates_on_random_markets():
    rng = random.Random(5)
    spec = []
    for m in range(12):
        for k in range(rng.randint(1, 3)):
            spec.append((f"v:m{m}:{k + 1}", f"tok{rng.randint(0, 4)} word{m % 3}"))
    recs = _recs(spec)
    pairs = set(candidate_pairs(recs, top_k=10_000))
    market = [r["id"].rsplit(":", 1)[0] for r in recs]
    for i, j in itertools.combinations(range(len(recs)), 2):
        if market[i] == market[j]:
            assert (i, j) in pairs  # siblings are always candidates
    assert all(i < j for i, j in pairs)


def test_gold_pairs_from_theory_reads_pairwise_relations_only():
    a, b, c = ir.var("v:m1:1"), ir.var("v:m2:1"), ir.var("v:m3:1")
    theory = {"constraints": [
        {"id": "g1", "expr": {"op": "implies", "a": a, "b": b}, "interps": None},
        {"id": "g2", "expr": {"op": "iff", "a": a, "b": c}, "interps": None},
        {"id": "g3", "expr": {"op": "atmost", "k": 1, "args": [a, b]}, "interps": None},
        {"id": "g4", "expr": {"op": "atmost", "k": 2, "args": [a, b, c]}, "interps": None},
        {"id": "g5", "expr": {"op": "or", "args": [a, b]}, "interps": None},
        {"id": "g6", "expr": {"op": "implies", "a": {"op": "ref", "name": "X"}, "b": b}, "interps": None},
    ]}
    assert fixed.gold_pairs_from_theory(theory) == [("v:m1:1", "v:m2:1"), ("v:m1:1", "v:m3:1"), ("v:m1:1", "v:m2:1")]

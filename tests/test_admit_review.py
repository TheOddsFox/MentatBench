"""Review follow-ups of the admission stage: multi-word closure against the exact 2-CNF check and the transitive closure, the
refused at-least undo, the tie-break order, tier A for at_most_k(1), group drops per row, check_conf overflow, ceilings
forwarding, the admission fallbacks (named), floor drops on every path, and an engine-localized deliberate conflict."""

import random

import numpy as np
import pytest

from mentat_bench import engine
from mentat_bench.adapters.base import DecisionError, check_conf, usage
from mentat_bench.harness import fixed
from mentat_bench.harness.admit import Closure, admit_units
from mentat_bench.manifest import DEFAULT_PROFILE
from test_admit import _binary_markets, _Child, _cross, _unit
from irval import check_submission


def _random_units(rng, n, count, max_clauses=3):
    return [[(rng.randrange(2 * n), rng.randrange(2 * n)) for _ in range(rng.randint(1, max_clauses))] for _ in range(count)]


@pytest.mark.parametrize("seed", range(12))
def test_multiword_closure_agrees_with_the_2cnf_check(seed):
    """n between 33 and 200 variables (several uint64 words per row): every admission flag equals the exact 2-CNF check."""
    rng = random.Random(1000 + seed)
    n = rng.randint(33, 200)
    binding = [(2 * v, 2 * v) for v in rng.sample(range(n), n // 4)]
    units = _random_units(rng, n, 60)
    flags = admit_units(n, binding, units)
    admitted = list(binding)
    for clauses, flag in zip(units, flags):
        assert flag == (not fixed._unsat_2cnf(n, admitted + clauses)), (seed, clauses)
        if flag:
            admitted += clauses
    assert not fixed._unsat_2cnf(n, admitted)


@pytest.mark.parametrize("seed", range(20))
def test_closure_rows_equal_the_transitive_closure_of_all_added_edges(seed):
    """Every clause adds its two implication edges; after each clause the closure equals the transitive closure of all
    edges so far (computed by search). Clauses are added without the admissibility check."""
    rng = random.Random(2000 + seed)
    n = rng.randint(2, 40)
    closure = Closure(n)
    edges: list[tuple[int, int]] = []
    for _ in range(25):
        x, y = rng.randrange(2 * n), rng.randrange(2 * n)
        closure.add_clause(x, y)
        edges += [(x ^ 1, y), (y ^ 1, x)]
        adjacency = [[] for _ in range(2 * n)]
        for u, v in edges:
            adjacency[u].append(v)
        for u in range(2 * n):
            seen, stack = {u}, [u]
            while stack:
                w = stack.pop()
                for v in adjacency[w]:
                    if v not in seen:
                        seen.add(v)
                        stack.append(v)
            for v in range(2 * n):
                assert closure.reach(u, v) == (v in seen), (seed, u, v)


def test_a_refused_at_least_part_undoes_the_unit_clauses_too():
    closure = Closure(2)
    before = closure.R.copy()
    # the clause forces x0 true (literal 0); literal 1 (not x0) is then forced false, literal 2 (x1) is not: only one of
    # the two literals can still be true, so an at-least-2 part refuses the unit and its clause is undone
    assert closure.try_unit([(0, 0)], (2, [1, 2])) is False
    assert np.array_equal(closure.R, before)
    assert closure.try_unit([(0, 0)], (1, [1, 2])) is True


def test_tie_break_order_for_relations_without_conf_is_the_study_order():
    recs = [
        {"question": "Will alpha reach 1 points?", "event_title": "E"},  # 0: market m1
        {"question": "Will alpha reach 1 points in 2026?", "event_title": "E"},  # 1: market m2, same event
        {"question": "Will beta win the cup?", "event_title": None},  # 2: market m3, unrelated
        {"question": "Will alpha reach 1 points?", "event_title": None},  # 3: market m1 (same market as 0)
    ]
    ids = ["toy:m1:1", "toy:m2:1", "toy:m3:1", "toy:m1:2"]
    key = fixed._pair_tiebreak(ids, recs)
    assert key(0, 3)[0] == 0  # one market
    assert key(0, 1)[0] == 1  # one event title
    assert key(0, 2)[0] == 2  # neither
    units = [_unit("p0", "relation", [(0, 2)]), _unit("p1", "relation", [(2, 4)]), _unit("p2", "relation", [(4, 6)])]
    for unit, pair in zip(units, [(0, 2), (1, 2), (0, 3)]):
        unit.pair = pair
    order = fixed._admission_order(units, lambda n: key(*units[n].pair))
    assert order == [2, 0, 1]  # the same market first, then the same event, then the rest


def test_at_most_k_with_k_one_is_admitted_in_tier_a_with_the_siblings():
    amo = _unit("g0", "at_most_k", [(1, 3)])
    amo.expr = {"op": "atmost", "k": 1, "args": []}
    other = _unit("g1", "ordered_chain", [(0, 0)])
    rel = _unit("p0", "relation", [(0, 2)], conf=0.9)
    sibling = _unit("g2", "exactly_one", [(1, 1)])
    assert fixed._admission_order([other, rel, amo, sibling]) == [2, 3, 1, 0]  # tier A (amo, sibling), tier B, tier C


def test_group_drops_count_once_per_row():
    ledger = fixed._DropLedger(9, 9, None, 1, None, 5)
    ledger.drop_group(7)
    ledger.drop_group(7)  # another unit of the same group row
    ledger.drop_group(8)
    assert ledger.group == 2


def test_conf_overflow_and_non_finite_values_are_decision_errors():
    for bad in (10 ** 400, -(10 ** 400), float("nan")):
        with pytest.raises(DecisionError):
            check_conf(bad)
    assert check_conf(7) == 7.0


def test_ceilings_forward_the_admission_knobs():
    from mentat_bench import ceilings

    policy = {"admit": False, "admit_max_literals": 9, "relation_floor": 0.3, "repair_budget_s": 60.0, "max_relation_drops": 5}
    assert ceilings._repair_caps({"policy": policy}) == policy


def test_base_is_marked_repaired_after_an_admission_rejection(tmp_path):
    # four outcomes in four markets, complements X and Y, IMPLIES on every pair: the implications contradict the bindings
    recs = [{"id": f"toy:{m}:1", "question": f"Will alpha team reach {m} points in 2026?", "label": "Yes", "event_title": "e"}
            for m in ("a1", "a2", "b1", "b2")]
    bindings = {"toy:a1:1": {"status": "bound", "class": "X", "negated": False, "missing": []},
                "toy:a2:1": {"status": "bound", "class": "X", "negated": True, "missing": []},
                "toy:b1:1": {"status": "bound", "class": "Y", "negated": False, "missing": []},
                "toy:b2:1": {"status": "bound", "class": "Y", "negated": True, "missing": []}}
    summary = fixed.build(recs, _Child(bindings, lambda a, b: "IMPLIES"), tmp_path / "sub", dict(DEFAULT_PROFILE),
                          max_relation_drop_frac=1.0)
    assert summary["admit_mode"] == "admit" and summary["admit_rejected_relations"] >= 1
    assert summary["relations_dropped"] >= summary["admit_rejected_relations"]
    assert summary["base_repaired"] is True


def test_floor_drops_are_not_contradictions_on_the_repair_path_either(tmp_path, monkeypatch):
    recs, bindings = _binary_markets()
    child = _Child(bindings, _cross, with_conf=lambda a, b: 0.1)  # every relation is below the floor
    monkeypatch.setattr(fixed, "_admit_build", lambda *args, **kwargs: None)  # admission unverified: the repair runs
    summary = fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE), relation_floor=0.5)
    assert summary["admit_mode"] == "repair_unverified" and summary["floor_dropped_relations"] == 1
    assert summary["relations_dropped"] == 0 and summary["contradiction_rate"] == 0.0
    assert summary["base_repaired"] is False


@pytest.mark.parametrize("mode, error", [("repair_unverified", None), ("repair_no_numpy", ImportError),
                                         ("repair_no_memory", MemoryError)])
def test_fallbacks_to_the_repair_are_named(tmp_path, monkeypatch, mode, error):
    recs, bindings = _binary_markets()

    def admission(*args, **kwargs):
        if error is not None:
            raise error("simulated")
        return None

    monkeypatch.setattr(fixed, "_admit_build", admission)
    summary = fixed.build(recs, _Child(bindings, _cross), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == mode and summary["base_check"] == "in_process"


def test_an_engine_refused_admitted_base_falls_back_to_the_repair(tmp_path, monkeypatch):
    recs, bindings = _binary_markets()
    real_settle, calls = fixed._settle_with_engine, []

    def refuse_first(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise fixed._EngineRejected("simulated refusal of the admitted base")
        return real_settle(*args, **kwargs)

    monkeypatch.setattr(fixed, "_settle_with_engine", refuse_first)
    summary = fixed.build(recs, _Child(bindings, _cross), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "repair_after_engine" and len(calls) == 2
    assert check_submission(tmp_path / "sub") == []


class _GroupChild(_Child):
    """Pairwise EXCLUDES and a group decision 'at least 2 of the three' for the candidate of three outcomes. Admission sees
    no contradiction (none of the three is forced false), so the engine must localize the at-least part."""

    def decide_group(self, ctx):
        ids = [m["id"] for m in ctx["members"]]
        if len(ids) == 3:
            return [{"kind": "at_least_k", "members": ids, "k": 2}], usage(self.name)
        return [], usage(self.name)


def test_engine_localizes_a_deliberate_at_least_conflict(tmp_path):
    if not engine.engine_available():
        pytest.skip("mb-engine not found (set MB_ENGINE)")
    recs = [{"id": f"toy:a:{k}", "question": f"Will alpha team reach {k} points in 2026?", "label": "Yes", "event_title": "e"}
            for k in (1, 2, 3)]
    bindings = {f"toy:a:{k}": {"status": "bound", "class": f"C{k}", "negated": False, "missing": []} for k in (1, 2, 3)}
    # one group committed and dropped: the group-fraction cap is the share of committed groups, so it is raised here
    summary = fixed.build(recs, _GroupChild(bindings, lambda a, b: "EXCLUDES"), tmp_path / "sub", dict(DEFAULT_PROFILE),
                          max_group_drop_frac=1.0)
    assert summary["admit_mode"] == "admit" and summary["base_probe"] == "engine"
    assert summary["engine_localized_drops"] == 1 and summary["groups_dropped"] == 1
    assert summary["engine_rounds"] >= 1 and summary["engine_probes"] >= summary["engine_rounds"]
    assert check_submission(tmp_path / "sub") == []

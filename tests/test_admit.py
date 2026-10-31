"""Admit-time base consistency (harness/admit.py and fixed._admit_units): the admitted set is satisfiable and every refusal is
forced (checked against the 2-CNF check and brute force), a refused unit leaves the closure unchanged, the admission order
is tiered, the relation floor and the caps hold, and the wire field conf is validated, logged and inherited."""

import json
import random
import time

import numpy as np
import pytest

from mentat_bench import engine, ir
from mentat_bench.adapters.base import DecisionError, check_conf, check_group, check_relation_decision, usage
from mentat_bench.harness import fixed
from mentat_bench.harness.admit import Closure, admit_units, at_least_possible, at_most_possible
from mentat_bench.harness.fixed import (
    BaseCheckIncomplete, BaseInconsistentError, RepairBudgetStopped, _admission_order, _admit_units, _amo, _BaseCheck, _DropLedger,
    _relation_clauses,
    _Unit,
)
from mentat_bench.manifest import DEFAULT_PROFILE
from irval import check_submission


class _NoRows:
    def __init__(self, n):
        self.ids = [f"v:x{i}:1" for i in range(n)]

    def rows(self):
        return iter(())


def _brute_sat(n, clauses):
    """Literal 2v is variable v, 2v + 1 its negation; a clause holds when one of its literals does."""
    for bits in range(1 << n):
        a = [(bits >> v) & 1 for v in range(n)]
        if all(a[x >> 1] ^ (x & 1) or a[y >> 1] ^ (y & 1) for x, y in clauses):
            return True
    return False


@pytest.mark.parametrize("seed", range(150))
def test_admitted_set_is_satisfiable_and_every_refusal_is_forced(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 6)
    truth = [rng.randint(0, 1) for _ in range(n)]
    binding = [(2 * v + 1 - truth[v],) * 2 for v in range(n) if rng.random() < 0.5]  # satisfied by `truth`
    units = [[(rng.randrange(2 * n), rng.randrange(2 * n)) for _ in range(rng.choice([1, 1, 2, 3]))]
             for _ in range(rng.randint(1, 25))]
    flags = admit_units(n, binding, units)
    admitted = list(binding)
    for clauses, flag in zip(units, flags):
        sat = _brute_sat(n, admitted + clauses)
        assert sat == flag == (not fixed._unsat_2cnf(n, admitted + clauses)), (seed, admitted, clauses)
        if flag:
            admitted += clauses
    assert _brute_sat(n, admitted) and not fixed._unsat_2cnf(n, admitted)


@pytest.mark.parametrize("seed", range(60))
def test_a_refused_unit_leaves_the_closure_exactly_as_it_was(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 6)
    closure = Closure(n)
    for _ in range(rng.randint(0, 6)):
        unit = [(rng.randrange(2 * n), rng.randrange(2 * n)) for _ in range(rng.choice([1, 2, 3]))]
        before = closure.R.copy()
        if not closure.try_unit(unit):
            assert np.array_equal(closure.R, before), (seed, unit)


def _unit(cid, kind, clauses=(), conf=None, rep=-1):
    unit = _Unit(cid, None, -1, list(clauses), False, kind, rep=rep)
    unit.conf = conf
    return unit


def test_admission_order_is_tiered_and_deterministic():
    units = [
        _unit("g0", "at_least_k", conf=0.9),
        _unit("p0", "relation", [(0, 2)], conf=0.2),
        _unit("g1", "exactly_one", [(0, 2)]),
        _unit("p1", "relation", [(4, 6)]),  # no conf: after the confident relations
        _unit("p2", "relation", [(2, 4)], conf=0.7),
        _unit("d0", "derived", rep=2),
        _unit("g2", "ordered_chain"),  # no conf
        _unit("g3", "at_most_one", [(1, 3)]),
    ]
    # tier A: sibling groups (log order); tier B: relations by conf, then no conf; tier C: other groups by conf, then none
    assert _admission_order(units) == [2, 7, 4, 1, 3, 0, 6]
    units[1].dropped = True
    assert _admission_order(units) == [2, 7, 4, 3, 0, 6]


def test_relation_floor_rejects_low_conf_relations_and_counts_them():
    n = 3
    units = [
        _unit("p0", "relation", _relation_clauses(0, 1, "IMPLIES"), conf=0.9),
        _unit("p1", "relation", _relation_clauses(1, 2, "IMPLIES"), conf=0.1),  # below the floor
        _unit("p2", "relation", _relation_clauses(0, 2, "EXCLUDES")),  # no conf: never floored
    ]
    ledger = _DropLedger(100, 100, None, 3)
    res = _admit_units(_BaseCheck(_NoRows(n), units, 1), units, ledger, 0.5)
    assert res["dropped"] == {1} and res["floored"] == {1} and res["rejected_relations"] == 0
    assert res["floored_relations"] == 1 and ledger.relation == 0  # a floor drop is not a contradiction


def test_admission_refuses_when_the_relation_cap_is_exceeded():
    n = 2
    units = [_unit("p0", "relation", _relation_clauses(0, 1, "IMPLIES"), conf=0.9),
             _unit("p1", "relation", _relation_clauses(0, 1, "EXCLUDES"), conf=0.8),
             _unit("p2", "relation", [(0, 0)], conf=0.7)]  # forces x0, which the admitted units make false
    with pytest.raises(BaseInconsistentError, match="relation drops"):
        _admit_units(_BaseCheck(_NoRows(n), units, 1), units, _DropLedger(0, 100, None, 3), None)


def test_admission_keeps_the_highest_conf_unit_of_a_conflict():
    # a unit that forces x0 false and a unit that forces it true: the one with the higher conf is admitted
    units = [_unit("low", "relation", [(1, 1)], conf=0.2), _unit("high", "relation", [(0, 0)], conf=0.8)]
    res = _admit_units(_BaseCheck(_NoRows(1), units, 1), units, _DropLedger(9, 9, None, 2), None)
    assert res["dropped"] == {0} and res["rejected_relations"] == 1


def test_a_deferred_at_least_part_needs_k_members_still_possibly_true():
    # x0 and x1 are forced false by the first two units; of x0, x1, x2 only x2 can be true, so at least 2 is refused
    units = [_unit("p0", "relation", [(1, 1), (3, 3)], conf=0.9),  # not x0 and not x1
             _unit("g0", "at_least_k", [], conf=0.5)]
    units[1].atleast = (2, [0, 1, 2])  # record indices: members x0, x1, x2 (their literals are 0, 2, 4)
    res = _admit_units(_BaseCheck(_NoRows(3), units, 1), units, _DropLedger(9, 9, None, 2), None)
    assert res["dropped"] == {1} and res["rejected_groups"] == 1
    units[1].atleast = (1, [0, 1, 2])  # one of them can still be true: admitted
    res = _admit_units(_BaseCheck(_NoRows(3), units, 1), units, _DropLedger(9, 9, None, 2), None)
    assert res["dropped"] == set()


def test_bindings_alone_inconsistent_is_refused_like_the_repair():
    units = [_unit("p0", "relation", [(0, 0)], conf=0.5)]
    check = _BaseCheck(_NoRows(1), units, 1)
    check.binding = [(0, 0), (1, 1)]  # x0 and not x0
    with pytest.raises(BaseInconsistentError, match="bindings alone"):
        _admit_units(check, units, _DropLedger(9, 9, None, 1), None)


def test_check_conf_and_relation_decisions_validate_the_optional_field():
    assert check_relation_decision("IMPLIES") == ("IMPLIES", None)
    assert check_relation_decision({"relation": "EXCLUDES", "conf": 0.25}) == ("EXCLUDES", 0.25)
    assert check_relation_decision({"relation": "NONE"}) == ("NONE", None)
    assert check_relation_decision({"relation": "NONE", "conf": None}) == ("NONE", None)
    for bad in (float("nan"), float("inf"), "0.5", True):
        with pytest.raises(DecisionError):
            check_relation_decision({"relation": "IMPLIES", "conf": bad})
    with pytest.raises(DecisionError):
        check_relation_decision({"relation": "MAYBE", "conf": 0.5})
    assert check_conf(None) is None and check_conf(3) == 3.0


def test_check_group_keeps_a_valid_conf_only():
    base = {"kind": "at_most_one", "members": ["a", "b"]}
    assert "conf" not in check_group(base, {"a", "b"})
    assert check_group({**base, "conf": 0.4}, {"a", "b"})["conf"] == 0.4
    with pytest.raises(DecisionError):
        check_group({**base, "conf": float("nan")}, {"a", "b"})


class _Child:
    """A scripted child. relation(a, b) names the relation; with_conf(a, b) (when given) adds the conf field to the reply."""

    name = "conf-child"

    def __init__(self, bindings, relation, with_conf=None):
        self.bindings, self.relation, self.with_conf = bindings, relation, with_conf

    def decide_binding(self, ctx):
        return self.bindings[ctx["id"]], usage(self.name)

    def decide_relation(self, a, b):
        rel = self.relation(a["id"], b["id"])
        if self.with_conf is None:
            return rel, usage(self.name)
        return {"relation": rel, "conf": self.with_conf(a["id"], b["id"])}, usage(self.name)


def _cross(a, b):
    """IMPLIES between outcomes of different markets; NONE between siblings (a sibling pair is complementary: IMPLIES would
    contradict the bindings)."""
    return "IMPLIES" if a.split(":")[1][0] != b.split(":")[1][0] else "NONE"


def _binary_markets():
    """Two binary markets (toy:a and toy:b) whose outcomes are complements (a:1 = not a:2, b:1 = not b:2): the complement
    dedup applies."""
    recs = [
        {"id": f"toy:{m}:{k}", "question": f"Will alpha team reach {m}{k} points in 2026?", "label": "Yes", "event_title": "e"}
        for m in ("a", "b") for k in (1, 2)
    ]
    bindings = {
        "toy:a:1": {"status": "bound", "class": "X", "negated": False, "missing": []},
        "toy:a:2": {"status": "bound", "class": "X", "negated": True, "missing": []},
        "toy:b:1": {"status": "bound", "class": "Y", "negated": False, "missing": []},
        "toy:b:2": {"status": "bound", "class": "Y", "negated": True, "missing": []},
    }
    return recs, bindings


def test_derived_rows_inherit_the_representatives_conf(tmp_path):
    recs, bindings = _binary_markets()
    child = _Child(bindings, _cross, with_conf=lambda a, b: 0.5)
    fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True)
    rows = [json.loads(line) for line in (tmp_path / "sub.build" / "relations.jsonl").read_text().splitlines()]
    # the representative (a1, b1) is asked; its complement pairs derive EXCLUDES and IMPLIED_BY (IMPLIES flipping a is NONE)
    assert [row[2] for row in rows] == ["IMPLIES", "EXCLUDES", "IMPLIED_BY"]
    assert all(len(row) == 5 and row[4] == 0.5 for row in rows)
    assert [row[3] for row in rows] == [-1, 0, 0]  # the derived rows follow the representative's line


def test_child_without_conf_writes_the_old_row_format(tmp_path):
    recs, bindings = _binary_markets()
    child = _Child(bindings, _cross)
    fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True)
    lines =(tmp_path / "sub.build" / "relations.jsonl").read_text().splitlines()
    assert lines == ['[0, 2, "IMPLIES"]', '[0, 3, "EXCLUDES", 0]', '[1, 3, "IMPLIED_BY", 0]']  # no conf field: the old format


def test_admitted_build_with_conf_is_a_consistent_submission(tmp_path):
    recs, bindings = _binary_markets()
    child = _Child(bindings, _cross, with_conf=lambda a, b: 0.5)
    summary = fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "admit" and summary["admit_rejected_relations"] == 0
    assert summary["base_probe"] in ("engine", "skipped") and check_submission(tmp_path / "sub") == []
    policy = json.loads((tmp_path / "sub" / "build_policy.json").read_text())
    assert policy["admit"] is True and policy["relation_floor"] is None and policy["repair_budget_s"] is None


def test_memory_cap_falls_back_to_the_repair(tmp_path):
    recs, bindings = _binary_markets()
    child = _Child(bindings, _cross, with_conf=lambda a, b: 0.5)
    fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True)
    work = tmp_path / "sub.build"
    capped = fixed.finalize_checkpoint(work, tmp_path / "capped", dict(DEFAULT_PROFILE), admit_max_literals=4)
    plain = fixed.finalize_checkpoint(work, tmp_path / "plain", dict(DEFAULT_PROFILE), admit=False)
    assert capped["admit_mode"] == "repair_size" and plain["admit_mode"] == "repair"
    assert capped["relations_dropped"] == plain["relations_dropped"]


def test_scoped_theories_fall_back_to_the_repair(tmp_path):
    recs, bindings = _binary_markets()
    fixed.build(recs, _Child(bindings, _cross), tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True)
    work = tmp_path / "sub.build"
    ckpt = json.loads((work / "checkpoint.json").read_text())
    ckpt["axes"] = {"venue": ["a", "b"]}  # two interpretations, no scoped unit: the repair runs per interpretation
    (work / "checkpoint.json").write_text(json.dumps(ckpt, sort_keys=True))
    summary = fixed.finalize_checkpoint(work, tmp_path / "scoped", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "repair_scoped" and summary["interpretations"] == ["venue=a", "venue=b"]


def test_repair_budget_past_stops_the_post_hoc_repair_with_its_own_error(tmp_path, monkeypatch):
    recs, bindings = _binary_markets()
    fixed.build(recs, _Child(bindings, _cross), tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True)
    work = tmp_path / "sub.build"

    def slow_repair(*args, **kwargs):  # the repair runs past its budget before it finishes (its time is the only stub)
        time.sleep(0.02)
        raise BaseCheckIncomplete("budget")

    monkeypatch.setattr(fixed, "_prune_in_process", slow_repair)
    # admit_max_literals 4: the theory is too large for admission, so the post-hoc repair is the base path
    with pytest.raises(RepairBudgetStopped, match="repair budget"):
        fixed.finalize_checkpoint(work, tmp_path / "partial", dict(DEFAULT_PROFILE), admit_max_literals=4, repair_budget_s=0.005)
    assert not (tmp_path / "partial" / "PARTIAL.json").exists() and (work / "checkpoint.json").is_file()


def test_no_admit_takes_the_budget_and_the_floor_on_the_post_hoc_repair(tmp_path):
    recs, bindings = _binary_markets()
    child = _Child(bindings, _cross, with_conf=lambda a, b: 0.1)  # below the floor: the representative is floored
    summary = fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE), admit=False, relation_floor=0.5, repair_budget_s=60.0)
    assert summary["admit_mode"] == "repair" and summary["floor_dropped_relations"] == 1
    policy = json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["admit"] is False and policy["relation_floor"] == 0.5 and policy["repair_budget_s"] == 60.0


def test_repair_without_a_budget_still_stops_at_the_deadline(tmp_path, monkeypatch):
    recs, bindings = _binary_markets()
    fixed.build(recs, _Child(bindings, _cross), tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True)

    def out_of_time(*args, **kwargs):
        raise BaseCheckIncomplete("deadline")

    monkeypatch.setattr(fixed, "_prune_in_process", out_of_time)
    with pytest.raises(BaseCheckIncomplete) as exc:
        fixed.finalize_checkpoint(tmp_path / "sub.build", tmp_path / "late", dict(DEFAULT_PROFILE), admit=False)
    assert type(exc.value) is BaseCheckIncomplete  # not the budget's own error: the deadline stopped it


def test_bad_admission_knobs_are_refused_before_any_model_call(tmp_path):
    recs, bindings = _binary_markets()
    with pytest.raises(ValueError, match="admit_max_literals"):
        fixed.build(recs, _Child(bindings, lambda a, b: "IMPLIES"), tmp_path / "sub", dict(DEFAULT_PROFILE), admit_max_literals=0)
    with pytest.raises(ValueError, match="repair_budget_s"):
        fixed.build(recs, _Child(bindings, lambda a, b: "IMPLIES"), tmp_path / "sub", dict(DEFAULT_PROFILE), repair_budget_s=0.0)
    assert not (tmp_path / "sub.build").exists()


def test_build_with_an_engine_confirmed_admitted_base(tmp_path):
    """Engine-gated: an admitted base is confirmed by mb-engine (base_probe engine) on a small slice with conflicts."""
    if not engine.engine_available():
        pytest.skip("mb-engine not found (set MB_ENGINE)")
    recs, bindings = _binary_markets()
    rel = {("toy:a:1", "toy:b:1"): "EXCLUDES", ("toy:a:1", "toy:b:2"): "IMPLIES", ("toy:a:2", "toy:b:1"): "IMPLIED_BY"}
    child = _Child(bindings, lambda a, b: rel.get((a, b), "NONE"), with_conf=lambda a, b: 0.3)
    summary = fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["base_probe"] == "engine" and summary["admit_mode"] == "admit"
    assert check_submission(tmp_path / "sub") == []


def test_full_replay_bindings_are_sorted_by_id_and_pairwise_keeps_record_order(tmp_path):
    recs = [{"id": f"toy:m{i}:1", "question": f"Will alpha team reach {i} points?", "label": "Yes", "event_title": "e"}
            for i in (3, 1, 2)]

    class Replay:
        name = "gold_replay"
        supplies_gold_refs = True  # a stand-in for the oracle: a full replay is for gold-backed adapters only

        def decide_binding(self, ctx):
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}, usage("replay")

        def extra_theory(self):
            return {"interpretations": ["base"], "constraints": []}

    fixed.build(recs, Replay(), tmp_path / "replay", dict(DEFAULT_PROFILE))
    replay_ids = [row["id"] for row in ir.read_jsonl(tmp_path / "replay" / "bindings.jsonl")]
    assert replay_ids == ["toy:m1:1", "toy:m2:1", "toy:m3:1"]
    unresolved = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}
    fixed.build(recs, _Child({r["id"]: unresolved for r in recs}, _cross), tmp_path / "pairwise", dict(DEFAULT_PROFILE))
    pairwise_ids = [row["id"] for row in ir.read_jsonl(tmp_path / "pairwise" / "bindings.jsonl")]
    assert pairwise_ids == ["toy:m3:1", "toy:m1:1", "toy:m2:1"]  # the record order, as before


def test_amo_units_keep_the_pairwise_clauses_under_admission():
    members = [0, 1, 2]
    unit = _Unit("g0", None, -1, _amo(members), False, "at_most_one", members)
    unit.conf = None
    res = _admit_units(_BaseCheck(_NoRows(3), [unit], 1), [unit], _DropLedger(9, 9, None, 0), None)
    assert res["dropped"] == set() and res["rejected_relations"] == 0 and res["rejected_groups"] == 0


# ---- BC2 (per-unit check of the deferred parts) and BC1 (the seal of a stopped localization) ------------------------

def _at_most_part(cid, members, k):
    """A deferred at most k part over record indices (its members are checked by the per-unit check)."""
    unit = _Unit(cid, {"op": "atmost", "k": k, "args": [ir.var(f"v:x{i}:1") for i in members]}, -1, [], True, "at_most_k")
    unit.atmost = (k, members)
    return unit


def _at_least_part(cid, members, k):
    unit = _Unit(cid, {"op": "atleast", "k": k, "args": [ir.var(f"v:x{i}:1") for i in members]}, -1, [], True, "at_least_k")
    unit.atleast = (k, members)
    return unit


def _exactly_one_part(cid, members):
    """A deferred exactly_one over 3+ members: its at most one is 2-CNF (kept in the unit's clauses), its at least one is not."""
    unit = _Unit(cid, {"op": "exactly", "k": 1, "args": [ir.var(f"v:x{i}:1") for i in members]}, -1, _amo(members), True,
                 "exactly_one", members)
    unit.atleast = (1, members)
    return unit


def _models(n, clauses):
    """Every assignment (0/1 per variable) that satisfies the clauses (literal 2v is variable v true)."""
    return [a for bits in range(1 << n) for a in [[(bits >> v) & 1 for v in range(n)]]
            if all(a[x >> 1] ^ (x & 1) or a[y >> 1] ^ (y & 1) for x, y in clauses)]


def test_closure_bounds_count_forced_values_and_disjoint_pairs():
    closure = Closure(5)
    closure.add_clause(0, 2)  # x0 or x1
    closure.add_clause(4, 6)  # x2 or x3: two disjoint pairs, so at least two of x0..x3 are true
    assert at_most_possible(closure, [0, 2, 4, 6], 1) is False
    assert at_most_possible(closure, [0, 2, 4, 6], 2) is True
    closure.add_clause(9, 9)  # not x4: x4 is out of the count
    assert at_least_possible(closure, [0, 2, 4, 6, 8], 4) is True and at_least_possible(closure, [0, 2, 4, 6, 8], 5) is False
    forced = Closure(2)
    forced.add_clause(0, 0)  # x0 true in every model
    assert at_most_possible(forced, [0, 2], 0) is False and at_most_possible(forced, [0, 2], 1) is True


def test_a_deferred_at_most_part_impossible_alone_is_dropped_and_a_feasible_one_is_kept():
    units = [_unit("p0", "relation", [(0, 2)]), _unit("p1", "relation", [(4, 6)]),  # x0 or x1; x2 or x3
             _at_most_part("g1", [0, 1, 2, 3], 1),  # at most 1 of 4: the two pairs force 2 true, so impossible alone
             _at_most_part("g2", [0, 1, 2, 3], 2)]  # at most 2 of 4: possible
    ledger = _DropLedger(9, 9, None, 4)
    assert fixed._drop_infeasible_deferred(_BaseCheck(_NoRows(4), units, 1), units, ledger) == ([2], [])
    assert units[2].dropped and not units[3].dropped and units[3].deferred
    assert ledger.group == 1  # a group drop, counted against the cap


def test_an_exactly_one_with_an_impossible_at_least_one_is_downgraded_and_a_feasible_one_is_kept():
    units = [_unit("p0", "relation", [(1, 1)]), _unit("p1", "relation", [(3, 3)]), _unit("p2", "relation", [(5, 5)]),  # not x0, x1, x2
             _exactly_one_part("g0", [0, 1, 2]),  # every member false: the at least one is impossible
             _exactly_one_part("g1", [0, 1, 3])]  # x3 can be true
    ledger = _DropLedger(9, 9, None, 3)
    assert fixed._drop_infeasible_deferred(_BaseCheck(_NoRows(4), units, 1), units, ledger) == ([], [3])
    assert not units[3].dropped and not units[3].deferred and units[3].down  # the at most one stays, as the engine keeps it
    assert units[3].expr["op"] == "atmost" and units[3].expr["k"] == 1 and ledger.group == 0
    assert units[4].deferred and not units[4].down


def _noise_clause(rng, n):
    roll = rng.random()
    if roll < 0.4:
        return (2 * rng.randrange(n), 2 * rng.randrange(n))  # x or y over positive literals
    if roll < 0.6:
        x = rng.randrange(2 * n)
        return (x, x)  # a forced literal
    return (rng.randrange(2 * n), rng.randrange(2 * n))


@pytest.mark.parametrize("seed", range(300))
def test_a_part_refused_alone_is_refused_by_every_model_of_the_base(seed):
    """The argument of BC2: a part impossible against N0 (the non-deferred base, the bindings included) is refused by every model
    of N0, so every probe of the localization (whose base contains N0) refuses it. The reference is N0 alone: a deferred
    exactly_one's at most one is not in it. Brute force on small random bases and deferred parts; many of them have disjoint
    pairs over the members of their part, so that the at most and at least bounds are exercised."""
    rng = random.Random(seed)
    n = rng.randint(3, 6)
    units = [_unit(f"p{i}", "relation", [_noise_clause(rng, n)]) for i in range(rng.randint(0, 4))]
    for j in range(rng.randint(1, 4)):
        members = sorted(rng.sample(range(n), rng.randint(2, n)))
        if rng.random() < 0.7:  # disjoint positive pairs over the members
            shuffled = members[:]
            rng.shuffle(shuffled)
            for a in range(0, len(members) - 1, 2):
                units.append(_unit(f"p{len(units)}", "relation", [(2 * shuffled[a], 2 * shuffled[a + 1])]))
        if rng.random() < 0.3:  # a member forced false
            x = rng.choice(members)
            units.append(_unit(f"p{len(units)}", "relation", [(2 * x + 1, 2 * x + 1)]))
        kind = rng.choice(["at_most", "at_least", "exactly"])
        if kind == "at_most":
            units.append(_at_most_part(f"g{j}", members, rng.randint(1, len(members) - 1)))
        elif kind == "at_least":
            units.append(_at_least_part(f"g{j}", members, rng.randint(1, len(members) - 1)))
        elif len(members) >= 3:
            units.append(_exactly_one_part(f"g{j}", members))
    n0 = [clause for unit in units if not unit.deferred for clause in unit.clauses]
    models = _models(n, n0)
    if not models:  # N0 must be satisfiable (finalize checks the admitted base before the per-unit check)
        return
    parts = [unit for unit in units if unit.deferred]
    fixed._drop_infeasible_deferred(_BaseCheck(_NoRows(n), units, 1), units, _DropLedger(99, 99, None, 1))
    for unit in parts:
        if unit.deferred:  # kept: nothing to check
            continue
        members = unit.atmost[1] if unit.atmost is not None else unit.atleast[1]
        for a in models:
            count = sum(a[i] for i in members)
            if unit.atmost is not None:
                assert count > unit.atmost[0], (seed, unit.id)
            elif unit.kind == "at_least_k":
                assert count < unit.atleast[0], (seed, unit.id)
            else:  # the at least one of an exactly_one
                assert count == 0, (seed, unit.id)


class _Outcomes:
    """The replay of the settle tests: outcome ids with unresolved bindings and no decisions."""

    def __init__(self, n):
        self.ids = [f"v:x{i}:1" for i in range(n)]

    def rows(self):
        return iter([{"id": oid, "status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}
                     for oid in self.ids])


def _settle(tmp_path, units, n, verdict, monkeypatch):
    """One engine settle over `units` with a scripted mb-engine verdict; returns (status, stats, ledger)."""
    monkeypatch.setattr(fixed, "_engine_verdict", verdict)
    stats, ledger = fixed._engine_stats(), _DropLedger(99, 99, None, 1)
    work = tmp_path / "work"
    work.mkdir()
    usage = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}
    status = fixed._settle_with_engine(units, _Outcomes(n), work, tmp_path / "sub", ["base"], dict(DEFAULT_PROFILE),
                                       lambda: usage, lambda: 1e9, ledger, stats)
    return status, stats, ledger


def _group_units(count):
    return [_at_most_part(f"g{i}", [0, 1, 2], 2) for i in range(count)]  # each at most 2 of 3: deferred, not impossible


def test_seal_at_the_round_cap_drops_the_rest_and_seals_when_the_engine_accepts_the_base(tmp_path, monkeypatch):
    """The stub engine never finishes localizing: it rejects every base that still has a group constraint. At the round cap
    the three parts left are dropped, and the base is sealed on one probe."""
    monkeypatch.setattr(fixed, "MAX_GROUP_DROPS", 2)

    def never_localizes(out_dir, work, budget_s):
        theory = json.loads((out_dir / "theory.json").read_text(encoding="utf-8"))
        if any(con["id"].startswith("g") for con in theory["constraints"]):
            return "rejected", "scripted: a group constraint is still present"
        return "engine", ""

    units = _group_units(5)
    status, stats, ledger = _settle(tmp_path, units, 3, never_localizes, monkeypatch)
    assert status == "engine"
    assert stats["localized_drops"] == 2 and stats["deferred_unresolved"] == 3
    assert all(unit.dropped for unit in units) and ledger.group == 5  # the sealed drops are group drops, counted


def test_seal_at_the_round_cap_keeps_the_failure_when_the_engine_refuses_the_sealed_base(tmp_path, monkeypatch):
    monkeypatch.setattr(fixed, "MAX_GROUP_DROPS", 2)

    def refuses_the_sealed_base(out_dir, work, budget_s):
        if out_dir.name == "probe":  # the localization probes: a group constraint is still present
            theory = json.loads((out_dir / "theory.json").read_text(encoding="utf-8"))
            return ("rejected", "scripted") if any(con["id"].startswith("g") for con in theory["constraints"]) else ("engine", "")
        return "rejected", "scripted: the sealed base is inconsistent too"

    with pytest.raises(fixed._EngineRejected, match="inconsistent"):
        _settle(tmp_path, _group_units(5), 3, refuses_the_sealed_base, monkeypatch)


@pytest.mark.parametrize("sealed_verdict, expected", [("engine", "sealed"), ("rejected", BaseCheckIncomplete)])
def test_seal_at_the_deadline_is_sealed_only_when_the_engine_accepts_the_base(tmp_path, monkeypatch, sealed_verdict, expected):
    """The deadline cuts the localization (its probes are skipped); the seal drops the two parts left and probes once."""
    calls = []

    def deadline(out_dir, work, budget_s):
        if out_dir.name == "probe":
            return "skipped_deadline", ""
        calls.append(1)
        return ("rejected", "scripted") if len(calls) == 1 else (sealed_verdict, "")

    units = _group_units(2)
    if expected is BaseCheckIncomplete:
        with pytest.raises(BaseCheckIncomplete):
            _settle(tmp_path, units, 3, deadline, monkeypatch)
        return
    status, stats, _ = _settle(tmp_path, units, 3, deadline, monkeypatch)
    assert status == "engine" and stats["deferred_unresolved"] == 2 and stats["localized_drops"] == 0
    assert all(unit.dropped for unit in units)


def test_the_build_downgrades_an_infeasible_exactly_one_before_its_one_engine_probe(tmp_path, monkeypatch):
    from test_groups import Scripted, _anchored_case, _exactly_one_over  # the anchored exactly_one of the engine tests

    monkeypatch.setattr(engine, "engine_available", lambda: True)
    asked = []

    def accepts(out_dir, work, budget_s):
        asked.append(out_dir.name)
        return "engine", ""

    monkeypatch.setattr(fixed, "_engine_verdict", accepts)
    records, bindings, relations = _anchored_case()
    adapter = Scripted(bindings, relations=relations, decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]))
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["groups_downgraded"] == 1 and summary["deferred_infeasible_dropped"] == 0  # downgraded, not dropped
    assert summary["base_probe"] == "engine" and summary["engine_probes"] == 1 and asked == ["sub"]
    assert summary["deferred_unresolved"] == 0 and check_submission(tmp_path / "sub") == []


@pytest.mark.parametrize("seed", range(20))
def test_the_admitted_closure_is_the_closure_of_the_admitted_clauses(seed):
    """The per-unit check reads the closure admission built: it must equal the closure of the bindings and the admitted
    clauses, built again in another order (refused units are undone, so nothing else is in it)."""
    rng = random.Random(seed)
    n = 5
    units = [_unit(f"p{i}", "relation", [(rng.randrange(2 * n), rng.randrange(2 * n)) for _ in range(rng.choice([1, 2]))])
             for i in range(rng.randint(1, 14))]
    check = _BaseCheck(_NoRows(n), units, 1)
    res = _admit_units(check, units, _DropLedger(99, 99, None, len(units)), None)
    if res is None:  # the admitted set failed the 2-CNF check (never on these inputs)
        return
    admitted = [clause for i, unit in enumerate(units) if i not in res["dropped"] for clause in unit.clauses]
    rebuilt = Closure(n)
    for x, y in list(reversed(admitted)):
        rebuilt.add_clause(x, y)
    assert np.array_equal(res["closure"].R, rebuilt.R), seed


def test_the_repair_path_checks_the_deferred_parts_before_its_engine_probe(tmp_path, monkeypatch):
    """Without admission the base is repaired in process; the per-unit check still runs before the engine is asked."""
    from test_groups import Scripted, _anchored_case, _exactly_one_over  # the anchored exactly_one of the engine tests

    monkeypatch.setattr(engine, "engine_available", lambda: True)
    asked = []

    def accepts(out_dir, work, budget_s):
        asked.append(out_dir.name)
        return "engine", ""

    monkeypatch.setattr(fixed, "_engine_verdict", accepts)
    records, bindings, relations = _anchored_case()
    adapter = Scripted(bindings, relations=relations, decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]))
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), admit=False)
    assert summary["admit_mode"] == "repair" and summary["groups_downgraded"] == 1
    assert summary["base_probe"] == "engine" and asked == ["sub"] and check_submission(tmp_path / "sub") == []


# ---- BC2 against the localization: the same final base with and without the per-unit check -----------------------------

def _clause_expr(clauses):
    """The IR of a 2-CNF part (literal 2v is variable v true, 2v + 1 its negation)."""
    def lit(x):
        var = ir.var(f"v:x{x >> 1}:1")
        return var if x & 1 == 0 else {"op": "not", "a": var}

    if not clauses:
        return {"op": "true"}
    return {"op": "and", "args": [{"op": "or", "args": [lit(x), lit(y)]} for x, y in clauses]}


def _holds(expr, value):
    op = expr["op"]
    if op == "true":
        return True
    if op == "var":
        return bool(value[expr["id"]])
    if op == "not":
        return not _holds(expr["a"], value)
    if op == "or":
        return any(_holds(e, value) for e in expr["args"])
    if op == "and":
        return all(_holds(e, value) for e in expr["args"])
    count = sum(_holds(e, value) for e in expr["args"])
    return {"atmost": count <= expr["k"], "atleast": count >= expr["k"], "exactly": count == expr["k"]}[op]


def _oracle(n):
    """A mb-engine stand-in: it accepts the written theory iff some assignment of the n outcomes satisfies every constraint."""
    ids = [f"v:x{i}:1" for i in range(n)]

    def verdict(out_dir, work, budget_s):
        theory = json.loads((out_dir / "theory.json").read_text(encoding="utf-8"))
        for bits in range(1 << n):
            value = {ids[i]: (bits >> i) & 1 for i in range(n)}
            if all(_holds(con["expr"], value) for con in theory["constraints"]):
                return "engine", ""
        return "rejected", "scripted: no assignment satisfies the theory"

    return verdict


def _final(units):
    return [(unit.id, unit.dropped, unit.down) for unit in units]


def _random_theory(seed, with_bindings=False):
    """A small random theory: a non-deferred 2-CNF part, and 1 to 4 deferred parts over random members (the bounds interact
    through the base, so the two checks are compared on real overlaps). With `with_bindings`, one or two outcomes are fixed
    (forced literals, as bindings fix them): they are the check's bindings and also units of the theory. Returns (n, units,
    binding clauses)."""
    rng = random.Random(seed)
    n = rng.randint(3, 6)
    units = []
    for i in range(rng.randint(0, 4)):
        clauses = [_noise_clause(rng, n) for _ in range(rng.choice([1, 2]))]
        unit = _unit(f"p{i}", "relation", clauses)
        unit.expr = _clause_expr(clauses)
        units.append(unit)
    for j in range(rng.randint(1, 4)):
        members = sorted(rng.sample(range(n), rng.randint(2, n)))
        roll = rng.random()
        if roll < 0.4:
            units.append(_at_most_part(f"g{j}", members, rng.randint(1, len(members) - 1)))
        elif roll < 0.7:
            units.append(_at_least_part(f"g{j}", members, rng.randint(1, len(members) - 1)))
        elif len(members) >= 3:
            units.append(_exactly_one_part(f"g{j}", members))
            if rng.random() < 0.4:  # every member forced false: the at least one is impossible against N0 (the check's downgrade)
                for m in members:
                    unit = _unit(f"f{j}_{m}", "relation", [(2 * m + 1, 2 * m + 1)])
                    unit.expr = _clause_expr([(2 * m + 1, 2 * m + 1)])
                    units.append(unit)
        else:
            units.append(_at_most_part(f"g{j}", members, 1))
    binding = []
    if with_bindings:
        for i in range(rng.randint(1, 2)):
            literal = 2 * rng.randrange(n) + rng.randint(0, 1)
            binding.append((literal, literal))
            unit = _unit(f"b{i}", "relation", [(literal, literal)])
            unit.expr = _clause_expr([(literal, literal)])
            units.insert(i, unit)
    return n, units, binding


@pytest.mark.parametrize("with_bindings", [False, True])
def test_the_per_unit_check_leaves_the_localization_the_same_final_base(tmp_path, monkeypatch, with_bindings):
    """BC2 on and off: the same final units, and every unit the check drops or downgrades is one the localization also drops
    or downgrades. The check's bindings are passed as the check's binding clauses too (the closure starts from them)."""
    covered = {"drops": 0, "downgrades": 0}
    for seed in range(200):
        n, units, binding = _random_theory(seed, with_bindings)
        clauses_all = [c for unit in units for c in unit.clauses]
        clauses_base = [c for unit in units if not unit.deferred for c in unit.clauses]
        if not _models(n, clauses_all) or not _models(n, clauses_base):  # the admitted base is satisfiable, as in finalize
            continue
        _, off, _ = _random_theory(seed, with_bindings)
        (tmp_path / f"off{seed}").mkdir()
        status_off, _, _ = _settle(tmp_path / f"off{seed}", off, n, _oracle(n), monkeypatch)
        _, on, _ = _random_theory(seed, with_bindings)
        check = _BaseCheck(_NoRows(n), on, 1)
        check.binding = list(binding)
        bc2_dropped, bc2_downgraded = fixed._drop_infeasible_deferred(check, on, _DropLedger(99, 99, None, 1))
        (tmp_path / f"on{seed}").mkdir()
        status_on, _, _ = _settle(tmp_path / f"on{seed}", on, n, _oracle(n), monkeypatch)
        assert status_off == status_on == "engine", seed
        assert _final(on) == _final(off), seed
        off_dropped = {unit.id for unit in off if unit.dropped}
        off_downgraded = {unit.id for unit in off if unit.down}
        assert {on[k].id for k in bc2_dropped} <= off_dropped, seed
        assert {on[k].id for k in bc2_downgraded} <= off_downgraded, seed
        covered["drops"] += len(bc2_dropped) > 0
        covered["downgrades"] += len(bc2_downgraded) > 0
    assert covered["drops"] and covered["downgrades"], covered  # the seeds do exercise both checks


def test_the_admitted_closure_gives_the_same_check_as_a_rebuilt_one_when_no_deferred_part_has_clauses():
    """The reuse branch: no live deferred part has clauses, so the closure admission built is read as it is. The rebuilt
    closure (N0, from the units) gives the same drops."""
    def theory():
        return [_unit("p0", "relation", [(0, 2)]), _unit("p1", "relation", [(4, 6)]), _unit("p2", "relation", [(1, 1)]),
                _at_most_part("g1", [0, 1, 2, 3], 1),  # impossible: x1 is forced true and the pair x2 or x3 adds one more
                _at_most_part("g2", [0, 1, 2, 3], 2)]  # possible
    units = theory()
    check = _BaseCheck(_NoRows(4), units, 1)
    res = _admit_units(check, units, _DropLedger(99, 99, None, len(units)), None)
    assert res is not None and res["closure"] is not None and not res["dropped"]
    reused = fixed._drop_infeasible_deferred(check, units, _DropLedger(99, 99, None, 1), res["closure"])
    rebuilt_units = theory()
    rebuilt = fixed._drop_infeasible_deferred(_BaseCheck(_NoRows(4), rebuilt_units, 1), rebuilt_units, _DropLedger(99, 99, None, 1))
    assert reused == rebuilt == ([3], [])


def test_two_exactly_ones_that_interact_through_the_base_settle_the_same_with_and_without_the_check(tmp_path, monkeypatch):
    """d1 = exactly_one{p, q, r} and d2 = exactly_one{s, t, u}; the base makes each of p, q, r imply s and t. The at least one of
    d1 is impossible only with d2's at most one in the base, so the check must leave d1 to the localization: both end as
    at_most_one, as the localization gives (the check's own base holds N0 only: the at most ones of the deferred parts are not in it)."""
    def theory():
        clauses = [(1, 6), (8, 1), (3, 6), (8, 3), (5, 6), (8, 5)]  # not p or s, t or not p, and the same for q and r
        units = []
        for i, clause in enumerate(clauses):
            unit = _unit(f"p{i}", "relation", [clause])
            unit.expr = _clause_expr([clause])
            units.append(unit)
        return units + [_exactly_one_part("d1", [0, 1, 2]), _exactly_one_part("d2", [3, 4, 5])]

    off = theory()
    (tmp_path / "off").mkdir()
    assert _settle(tmp_path / "off", off, 6, _oracle(6), monkeypatch)[0] == "engine"
    on = theory()
    assert fixed._drop_infeasible_deferred(_BaseCheck(_NoRows(6), on, 1), on, _DropLedger(99, 99, None, 1)) == ([], [])
    (tmp_path / "on").mkdir()
    assert _settle(tmp_path / "on", on, 6, _oracle(6), monkeypatch)[0] == "engine"
    assert _final(on) == _final(off)
    assert [down for name, _, down in _final(on) if name in ("d1", "d2")] == [True, True]


def test_a_seal_over_the_group_cap_is_refused_and_the_failure_stands(tmp_path, monkeypatch):
    """The localization drops two parts (the cap of two rounds); the seal would drop three more, over a group cap of three.
    The probe accepts the sealed base, but its drops do not fit the group cap, so the seal is refused and the round cap's
    failure (_EngineRejected) stands, which runs the post-hoc repair."""
    monkeypatch.setattr(fixed, "MAX_GROUP_DROPS", 2)

    def never_localizes(out_dir, work, budget_s):
        theory = json.loads((out_dir / "theory.json").read_text(encoding="utf-8"))
        if any(con["id"].startswith("g") for con in theory["constraints"]):
            return "rejected", "scripted: a group constraint is still present"
        return "engine", ""

    monkeypatch.setattr(fixed, "_engine_verdict", never_localizes)
    work = tmp_path / "work"
    work.mkdir()
    usage = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}
    with pytest.raises(fixed._EngineRejected, match="scripted"):
        fixed._settle_with_engine(_group_units(5), _Outcomes(3), work, tmp_path / "sub", ["base"], dict(DEFAULT_PROFILE),
                                  lambda: usage, lambda: 1e9, _DropLedger(99, 3, None, 1), fixed._engine_stats())


def test_a_seal_probe_the_deadline_skips_is_the_deadline_failure(tmp_path, monkeypatch):
    """Past the round cap the seal probe is skipped by the deadline: the failure is BaseCheckIncomplete, so the post-hoc
    repair (which _EngineRejected would start) does not run past the deadline."""
    monkeypatch.setattr(fixed, "MAX_GROUP_DROPS", 2)

    def deadline_at_the_seal(out_dir, work, budget_s):
        theory = json.loads((out_dir / "theory.json").read_text(encoding="utf-8"))
        has_group = any(con["id"].startswith("g") for con in theory["constraints"])
        if out_dir.name == "probe":
            return ("rejected", "scripted") if has_group else ("engine", "")
        return ("rejected", "scripted") if has_group else ("skipped_deadline", "")

    monkeypatch.setattr(fixed, "_engine_verdict", deadline_at_the_seal)
    work = tmp_path / "work"
    work.mkdir()
    usage = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}
    with pytest.raises(BaseCheckIncomplete):
        fixed._settle_with_engine(_group_units(5), _Outcomes(3), work, tmp_path / "sub", ["base"], dict(DEFAULT_PROFILE),
                                  lambda: usage, lambda: 1e9, _DropLedger(99, 99, None, 1), fixed._engine_stats())


def _cycle_adapter():
    """The implication-cycle fixture of test_groups (the at least one of its exactly_one is impossible only with the AMO)."""
    from test_groups import Scripted, _exactly_one_over, _records, bound

    records = _records(3)
    bindings = {f"v:m{k}:1": bound(f"M{k}") for k in (1, 2, 3)}
    relations = {("v:m1:1", "v:m2:1"): "IMPLIES", ("v:m2:1", "v:m3:1"): "IMPLIES", ("v:m1:1", "v:m3:1"): "IMPLIED_BY"}
    return records, Scripted(bindings, relations=relations, decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]))


@pytest.mark.parametrize("error, reason", [(MemoryError, "memory"), (ImportError, "numpy")])
def test_a_per_unit_check_that_cannot_build_its_closure_is_recorded_and_the_engine_localizes(tmp_path, monkeypatch, capsys,
                                                                                            error, reason):
    monkeypatch.setattr(engine, "engine_available", lambda: True)

    def cannot_build(*args, **kwargs):
        raise error("scripted")

    monkeypatch.setattr(fixed, "_drop_infeasible_deferred", cannot_build)
    monkeypatch.setattr(fixed, "_engine_verdict", lambda out_dir, work, budget_s: ("engine", ""))
    records, adapter = _cycle_adapter()
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["deferred_check_skipped"] == reason and summary["deferred_infeasible_dropped"] == 0
    assert "per-unit check of the deferred parts is skipped" in capsys.readouterr().err
    assert summary["base_probe"] == "engine"


def test_the_per_unit_check_does_not_run_past_the_deadline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(engine, "engine_available", lambda: True)
    real_verdict = fixed._engine_verdict
    monkeypatch.setattr(fixed, "_engine_verdict", lambda out_dir, work, budget_s: ("engine", ""))
    records, adapter = _cycle_adapter()
    fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True)
    monkeypatch.setattr(fixed, "_engine_verdict", real_verdict)  # the deadline has passed: the real verdict skips the probe
    summary = fixed.finalize_checkpoint(tmp_path / "sub.build", tmp_path / "late", dict(DEFAULT_PROFILE, build_deadline_s=1e-9))
    assert summary["deferred_check_skipped"] == "deadline" and summary["deferred_infeasible_dropped"] == 0
    assert summary["base_probe"] == "skipped_deadline" and summary["admit_mode"] == "admit"
    assert "skipped (deadline)" in capsys.readouterr().err


def test_the_admitted_closure_is_released_before_a_rebuild_of_n0(tmp_path, monkeypatch):
    """A rebuild of N0 is only needed when a live deferred part has clauses: the caller then hands over no closure, so the
    admitted one is not alive beside the new one (checked by a weak reference to the admitted closure)."""
    import gc
    import weakref

    seen = {}
    real = fixed._drop_infeasible_deferred

    def watch(check, units, ledger, closure=None):
        seen["passed"] = closure is not None
        seen["admitted_alive_before_rebuild"] = seen.get("ref") is not None and seen["ref"]() is not None
        return real(check, units, ledger, closure)

    real_admit = fixed._admit_units

    def admit_and_remember(*args, **kwargs):
        res = real_admit(*args, **kwargs)
        if res is not None:
            seen["ref"] = weakref.ref(res["closure"])
        return res

    monkeypatch.setattr(fixed, "_drop_infeasible_deferred", watch)
    monkeypatch.setattr(fixed, "_admit_units", admit_and_remember)
    monkeypatch.setattr(engine, "engine_available", lambda: True)
    monkeypatch.setattr(fixed, "_engine_verdict", lambda out_dir, work, budget_s: ("engine", ""))
    records, adapter = _cycle_adapter()  # its exactly_one has clauses: the closure is rebuilt
    fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    gc.collect()
    assert seen["passed"] is False and seen["admitted_alive_before_rebuild"] is False

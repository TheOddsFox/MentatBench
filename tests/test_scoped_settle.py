"""The scoped settle of a candidates build (fixed._settle_scoped): per interpretation, the base with that interpretation's
scoped units is checked, and when it is unsatisfiable (or an unscoped deferred at-least part becomes impossible) the scoped
units are walked in log order; a reading pair is one item, and an item that conflicts loses that interpretation's bit (a
unit with no bit left is dropped). A scoped build without candidates keeps the post-hoc repair (admit_mode repair_scoped)."""

import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentat_bench.adapters.base import reading_picks, usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_candidates import (  # noqa: E402
    READING_ROWS, Reading, Rec, _build, _questions, _records, _satisfiable_per_interpretation, _write,
)


def _unit(cid, mask, clauses, kind="at_most_one", expr=None):
    return fixed._Unit(cid, expr, mask, clauses, False, kind)


def _pair_unit(cid, mask, a, b, clauses, cand="c1"):
    unit = _unit(cid, mask, clauses, "ordered_chain", {"op": "implies", "a": {"op": "var", "id": a}, "b": {"op": "var", "id": b}})
    unit.cand = cand  # the candidate call the link came from (a reading answer's two links share it)
    return unit


def _settle(units, n_interps=3, time_left=lambda: 1e9, admitted_dropped=()):
    replay = SimpleNamespace(ids=["a", "b", "c"], rows=lambda: [])  # three outcomes, no bindings
    ledger = fixed._DropLedger(10, 10, None, 0)
    settled, cleared = fixed._settle_scoped(fixed._BaseCheck(replay, units, n_interps), units, ledger, time_left,
                                            set(admitted_dropped))
    return ledger, settled, cleared


def test_a_conflict_clears_only_the_conflicting_interpretation_bit():
    # literals: 2i is outcome i true, 2i + 1 false. Interpretation 1 forces a and b, and the later unit (bits 0 and 1)
    # says not both: it conflicts in interpretation 1 only, so it keeps interpretation 0
    forced = _unit("g0", 0b010, [(0, 0), (2, 2)])
    not_both = _unit("g1", 0b011, [(1, 3)])
    units = [forced, not_both]
    ledger, settled, cleared = _settle(units)
    assert not_both.mask == 0b001 and not not_both.dropped
    assert forced.mask == 0b010 and not forced.dropped
    assert settled == set() and cleared == 1
    assert ledger.group == 1  # a cleared bit is a group drop of its row, as in the post-hoc repair


def test_a_unit_with_no_bit_left_is_a_group_drop():
    forced = _unit("g0", 0b010, [(0, 0), (2, 2)])
    not_both = _unit("g1", 0b010, [(1, 3)])
    units = [forced, not_both]
    ledger, settled, cleared = _settle(units)
    assert settled == {1} and not forced.dropped and cleared == 0
    assert ledger.group == 1


def test_a_consistent_theory_keeps_every_bit():
    units = [_unit("g0", 0b011, [(0, 0)]), _unit("g1", 0b011, [(3, 3)])]  # a and not b: consistent in every interpretation
    ledger, settled, cleared = _settle(units)
    assert [u.mask for u in units] == [0b011, 0b011] and ledger.group == 0 and settled == set() and cleared == 0


def test_a_reading_pair_loses_its_bit_as_a_whole():
    # interpretation 1: a -> b and b -> a (the pair, as two links) against "a and not b" (the unit before them)
    unit_a = _unit("g0", 0b010, [(0, 0), (3, 3)], kind="at_most_one")
    link_ab = _pair_unit("g1.0", 0b010, "a", "b", [(1, 2)])
    link_ba = _pair_unit("g2.0", 0b010, "b", "a", [(3, 0)])
    units = [unit_a, link_ab, link_ba]
    ledger, settled, cleared = _settle(units, n_interps=2)
    assert settled == {1, 2} and not unit_a.dropped  # the pair is dropped whole; the unit that conflicts with it stays
    assert ledger.group == 2 and cleared == 0


def test_a_pair_with_two_bits_that_loses_one_bit_walks_without_a_key_error():
    # interpretation 1 has a forced unit, so the pair (masked 0b110: interpretations 1 and 2) conflicts there and clears
    # its bit 1 on both links; the walk must not look the pair up again under its changed mask
    forced = _unit("g0", 0b010, [(0, 0), (2, 2)])
    link_ab = _pair_unit("g1.0", 0b110, "a", "b", [(1, 3)])
    link_ba = _pair_unit("g2.0", 0b110, "b", "a", [(3, 1)])
    units = [forced, link_ab, link_ba]
    ledger, settled, cleared = _settle(units, n_interps=3)
    assert settled == set() and cleared == 2 and ledger.group == 2
    assert link_ab.mask == 0b100 and link_ba.mask == 0b100 and not link_ab.dropped and not link_ba.dropped


def test_links_of_different_candidates_are_not_a_pair():
    # A (a -> b) and B (a -> not b) share outcomes and bits but come from two candidate calls: each is its own item, so only
    # B, which conflicts with the forced a and A's b, is dropped
    forced = _unit("g0", 0b010, [(0, 0)])
    link_a = _pair_unit("g1.0", 0b010, "a", "b", [(1, 2)], cand="c1")
    link_b = _pair_unit("g2.0", 0b010, "a", "b", [(1, 3)], cand="c2")
    units = [forced, link_a, link_b]
    ledger, settled, cleared = _settle(units, n_interps=2)
    assert settled == {2} and not link_a.dropped and ledger.group == 1


def test_an_at_least_part_blocks_the_scoped_unit_that_makes_it_impossible():
    # an unscoped exactly_one over a, b, c (its at most one as 2-CNF, its at least one deferred). The scoped unit forces
    # all three false: the 2-CNF is satisfiable, but the at least one is impossible, so the scoped unit is the culprit
    one_of = _unit("p0", -1, [(1, 3), (1, 5), (3, 5)], kind="exactly_one")
    one_of.deferred, one_of.atleast = True, (1, [0, 1, 2])
    all_false = _unit("g1", 0b010, [(1, 1), (3, 3), (5, 5)])
    units = [one_of, all_false]
    ledger, settled, cleared = _settle(units, n_interps=2)
    assert settled == {1} and not one_of.dropped and ledger.group == 1 and cleared == 0


def test_the_deadline_stops_the_walk():
    forced = _unit("g0", 0b010, [(0, 0), (2, 2)])
    not_both = _unit("g1", 0b010, [(1, 3)])
    with pytest.raises(fixed.BaseCheckIncomplete, match="deadline"):
        _settle([forced, not_both], time_left=lambda: 0.0)


class Scoped(Rec):
    """Every group is a scoped at_most_one over its first two members (scope venue=a, of venue a and b)."""

    def decide_group(self, ctx):
        self.groups.append(ctx)
        ids = [m["id"] for m in ctx["members"]]
        scope = {"axis": "venue", "values": ["a"], "of": ["a", "b"]}
        return [{"kind": "at_most_one", "members": ids[:2], "scope": scope}], usage(self.name)


def test_a_scoped_build_without_candidates_keeps_the_post_hoc_repair(tmp_path):
    summary = fixed.build(_records(), Scoped(), tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True,
                          max_relation_drop_frac=1.0, max_group_drop_frac=1.0)
    assert summary["admit_mode"] == "repair_scoped" and summary["interpretations"] == ["venue=a", "venue=b"]
    assert "settle_dropped" not in summary
    policy = json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["candidates_gold_informed"] is False and policy["candidates_sha256"] is None
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text(encoding="utf-8"))
    scoped = [c for c in theory["constraints"] if c["id"].startswith("g")]
    assert scoped and all(c["interps"] == ["venue=a"] for c in scoped)


# ---- finalize of a reading build: the settle's failure paths ---------------------------------------------------------

def _reading_build(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions(pairs=[], groups=[]) + READING_ROWS)
    _build(tmp_path, Reading(), candidates=path)
    return tmp_path / "sub.build"


def test_the_settle_hitting_the_group_cap_stops_the_finalize_and_writes_nothing(tmp_path):
    work = _reading_build(tmp_path)
    with pytest.raises(fixed.BaseInconsistentError, match="max-group-drops 0"):  # the settle drops two rows
        fixed.finalize_checkpoint(work, tmp_path / "capped", dict(DEFAULT_PROFILE), max_group_drops=0)
    assert not (tmp_path / "capped").exists()


def test_a_memory_error_in_the_settle_falls_back_to_the_post_hoc_repair_from_the_snapshot(tmp_path, monkeypatch):
    work = _reading_build(tmp_path)
    seen = []
    real_prune = fixed._prune_in_process

    def failing_settle(*args, **kwargs):
        raise MemoryError("the closure does not fit")

    def spy_prune(check, *args, **kwargs):  # the units the repair starts from
        seen.append({unit.id: (unit.mask, unit.dropped) for unit in check.units})
        return real_prune(check, *args, **kwargs)

    monkeypatch.setattr(fixed, "_settle_scoped", failing_settle)
    monkeypatch.setattr(fixed, "_prune_in_process", spy_prune)
    summary = fixed.finalize_checkpoint(work, tmp_path / "fallback", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "repair_no_memory" and summary["interpretations"] == ["reading=alt", "reading=base"]
    assert "settle_dropped" not in summary  # the settle did not run
    assert seen and seen[0]["g0000005.0"] == (1, False)  # the snapshot's alt bit, not dropped


def test_an_interrupted_admission_restores_the_held_scoped_units(tmp_path, monkeypatch):
    work = _reading_build(tmp_path)
    captured = []

    def interrupted(check, units, *args, **kwargs):  # the held scoped units are out of the admitted base during admission
        captured.append(units)
        assert all(unit.dropped for unit in units if unit.mask != -1)
        raise fixed.BaseCheckIncomplete("the deadline passed during the base admission")

    monkeypatch.setattr(fixed, "_admit_build", interrupted)
    with pytest.raises(fixed.BaseCheckIncomplete):
        fixed.finalize_checkpoint(work, tmp_path / "interrupted", dict(DEFAULT_PROFILE))
    assert captured and not any(unit.dropped for unit in captured[0])


def test_an_engine_refusal_of_the_settled_base_restores_the_masks_for_the_repair(tmp_path, monkeypatch):
    work = _reading_build(tmp_path)
    real_settle_engine, real_prune = fixed._settle_with_engine, fixed._prune_in_process
    calls, seen = [], []

    def refuse_once(*args, **kwargs):  # the engine rejects the settled base once; the repaired base is accepted
        if not calls:
            calls.append(1)
            raise fixed._EngineRejected("mb-engine finds the base inconsistent (test)")
        return real_settle_engine(*args, **kwargs)

    def spy_prune(check, *args, **kwargs):
        seen.append({unit.id: (unit.mask, unit.dropped) for unit in check.units})
        return real_prune(check, *args, **kwargs)

    monkeypatch.setattr(fixed, "_settle_with_engine", refuse_once)
    monkeypatch.setattr(fixed, "_prune_in_process", spy_prune)
    summary = fixed.finalize_checkpoint(work, tmp_path / "engine", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "repair_after_engine" and "settle_dropped" not in summary
    assert seen[0]["g0000005.0"] == (1, False)  # the settle had dropped it; the restore brings the unit back for the repair


# ---- a reading answer: the harness compiles exactly what reading_picks reads ------------------------------------------

U, C1, C2, C9 = "v:m0:1", "v:m1:1", "v:m2:1", "v:m9:1"
READING_CTX = {"source": "reading", "members": [{"id": x} for x in (U, C1, C2, "v:m3:1", "v:m4:1")]}
ALT = {"axis": "reading", "values": ["alt"], "of": ["alt", "base"]}
BASE = {"axis": "reading", "values": ["base"], "of": ["alt", "base"]}
BOTH = {"axis": "reading", "values": ["alt", "base"], "of": ["alt", "base"]}
TENSE = {"axis": "tense", "values": ["now"], "of": ["later", "now"]}


def _chain(members, scope, direction=None):
    decision = {"kind": "ordered_chain", "members": list(members), "scope": scope}
    if direction is not None:
        decision["direction"] = direction
    return decision


def _pair(option, scope, first=U):
    return [_chain([first, option], scope), _chain([option, first], scope)]


# (name, raw answer, the picks reading_picks gives, the links compiled per reading, the rejections)
CASES = [
    ("both-readings scope", _pair(C1, BOTH), {"base": C1, "alt": C1}, {"base": C1, "alt": C1}, 0),
    ("unscoped pair", _pair(C1, None), {"base": C1, "alt": C1}, {"base": C1, "alt": C1}, 0),
    ("reverse direction", [_chain([C1, U], ALT, "reverse"), _chain([C1, U], ALT)], {"base": "none", "alt": C1},
     {"alt": C1}, 0),
    ("mixed-direction pair", [_chain([U, C1], ALT), _chain([U, C1], ALT, "reverse")], {"base": "none", "alt": C1},
     {"alt": C1}, 0),
    ("same direction twice", [_chain([U, C1], ALT), _chain([U, C1], ALT)], {"base": "none", "alt": "invalid"}, {}, 1),
    ("non-chain sibling", _pair(C1, ALT) + [{"kind": "exactly_one", "members": [U, C1]}], {"base": "none", "alt": C1},
     {"alt": C1}, 0),
    ("split buckets", [_chain([U, C1], ALT), _chain([C1, U], None)], {"base": "invalid", "alt": C1},
     {"alt": C1}, 1),
    ("two options across readings", _pair(C1, ALT) + _pair(C2, BASE), {"base": C2, "alt": C1},
     {"base": C2, "alt": C1}, 0),
    ("two options in one reading", _pair(C1, ALT) + _pair(C2, ALT), {"base": "none", "alt": "invalid"}, {}, 1),
    ("scope of mismatch", _pair(C1, {"axis": "reading", "values": ["alt"], "of": ["alt"]}), {"base": "none", "alt": "none"},
     {}, 0),
    ("half pair", [_chain([U, C1], ALT)], {"base": "none", "alt": "invalid"}, {}, 1),
    ("option outside the members", _pair(C9, ALT), {"base": "none", "alt": "none"}, {}, 1),  # malformed only: the scorer's missing
    ("another axis", _pair(C1, TENSE), {"base": "none", "alt": "none"}, {}, 0),
    ("three members", [{"kind": "ordered_chain", "members": [U, C1, C2], "scope": ALT}], {"base": "none", "alt": "none"}, {}, 0),
    ("only a malformed decision", [{"kind": "bogus", "members": [U, C1]}], {"base": "none", "alt": "none"}, {}, 1),
    ("malformed beside a valid pair", _pair(C1, ALT) + [{"kind": "bogus", "members": [U, C1]}], {"base": "none", "alt": C1},
     {"alt": C1}, 0),
    ("empty answer", [], {"base": "none", "alt": "none"}, {}, 0),
]


def _compiled(raw):
    """The links the settle is given, per reading: {reading: {(first, second), ...}} and the scope of each reading's links."""
    tally = Counter()
    items, failed = fixed._settle_group(raw, READING_CTX, fixed._AdapterHealth(20), tally)
    assert not failed
    links = {}
    for item in items:
        reading = item["scope"]["values"][0]
        assert item["scope"] == {"axis": "reading", "values": [reading], "of": ["alt", "base"]}  # the canonical scope
        links.setdefault(reading, set()).add(tuple(item["members"]))
    return links, tally["groups_rejected"]


@pytest.mark.parametrize("name, raw, picks, expected, rejected", CASES, ids=[case[0] for case in CASES])
def test_the_compiled_links_are_exactly_reading_picks(name, raw, picks, expected, rejected):
    assert reading_picks(raw, READING_CTX_MEMBERS) == picks
    links, rejections = _compiled(raw)
    assert rejections == rejected
    for reading in ("base", "alt"):
        if reading in expected:
            assert links[reading] == {(U, expected[reading]), (expected[reading], U)}  # one pair, both directions
        else:
            assert reading not in links  # "none" and "invalid" compile nothing


READING_CTX_MEMBERS = [m["id"] for m in READING_CTX["members"]]


def test_mutual_reading_rows_are_both_kept_with_their_own_pairs(tmp_path):
    # A asks u = m0 with pick m1; B asks u = m1 with pick m0. Each keeps its pair: the dedup key carries the candidate call.
    path = _write(tmp_path / "candidates.jsonl", _questions(pairs=[], groups=[]) + MUTUAL_ROWS)
    summary = _build(tmp_path, Mutual(), candidates=path)
    assert summary["admit_mode"] == "admit" and summary["groups_committed"] == 4 and summary["groups_dropped"] == 0
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text(encoding="utf-8"))
    assert sorted(c["id"] for c in theory["constraints"]) == ["g0000000.0", "g0000001.0", "g0000002.0", "g0000003.0"]
    assert all(c["interps"] == ["reading=alt"] for c in theory["constraints"])


MUTUAL = {"v:m0:1": {"base": None, "alt": "v:m1:1"}, "v:m1:1": {"base": None, "alt": "v:m0:1"}}
MUTUAL_ROWS = [  # A: u = m0, pick m1; B: u = m1, pick m0
    {"qid": "g00000", "op": "decide_group", "members": ["v:m0:1", "v:m1:1", "v:m2:1", "v:m3:1", "v:m4:1"], "source": "reading"},
    {"qid": "g00001", "op": "decide_group", "members": ["v:m1:1", "v:m0:1", "v:m2:1", "v:m3:1", "v:m4:1"], "source": "reading"},
]


class Mutual(Reading):
    picks = MUTUAL


# ---- two scope axes: the settle over interpretations of the cross product, and a fallback after a changed mask -----------

TENSE_ROW = {"qid": "g00000", "op": "decide_group", "members": ["v:m5:1", "v:m6:1"], "source": "curated"}
TWO_AXIS_ROWS = [TENSE_ROW] + [{**row, "qid": f"g{n + 1:05d}"} for n, row in enumerate(READING_ROWS)]


class TwoAxes(Reading):
    """The reading answers, and the curated row answered as an at most one scoped to tense=now (a second scope axis)."""

    def decide_group(self, ctx):
        if ctx["source"] != "file":
            return super().decide_group(ctx)
        self.groups.append(ctx)
        ids = [m["id"] for m in ctx["members"]]
        scope = {"axis": "tense", "values": ["now"], "of": ["later", "now"]}
        return [{"kind": "at_most_one", "members": ids, "scope": scope}], usage(self.name)


def _two_axis_build(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions(pairs=[], groups=[]) + TWO_AXIS_ROWS)
    return _build(tmp_path, TwoAxes(), candidates=path)


def test_two_scope_axes_settle_over_the_cross_product_and_clear_bits(tmp_path):
    summary = _two_axis_build(tmp_path)
    assert summary["admit_mode"] == "admit"
    assert summary["settle_cleared_bits"] == 2 and summary["settle_dropped"] == 2 and summary["groups_dropped"] == 2
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text(encoding="utf-8"))
    assert theory["interpretations"] == ["reading=alt|tense=later", "reading=alt|tense=now",
                                         "reading=base|tense=later", "reading=base|tense=now"]
    interps = {c["id"]: c["interps"] for c in theory["constraints"]}
    assert sorted(interps) == ["g0000000", "g0000001.0", "g0000002.0", "g0000003.0", "g0000004.0"]
    assert interps["g0000000"] == ["reading=alt|tense=now", "reading=base|tense=now"]  # the tense unit: two bits
    assert interps["g0000003.0"] == ["reading=alt|tense=later", "reading=alt|tense=now"]  # the surviving alt pair
    assert all(_satisfiable_per_interpretation(tmp_path / "sub").values())


def test_a_fallback_after_a_two_bit_unit_lost_one_bit_restores_its_mask(tmp_path, monkeypatch):
    _two_axis_build(tmp_path)
    work = tmp_path / "sub.build"
    real_drop, real_prune = fixed._drop_culprit, fixed._prune_in_process
    calls, seen = [], []

    def drop_then_no_memory(*args, **kwargs):  # the first refused link clears its bit; the second refused link fails
        calls.append(1)
        if len(calls) == 2:
            raise MemoryError("the second drop does not fit")
        return real_drop(*args, **kwargs)

    def spy_prune(check, *args, **kwargs):
        seen.append({unit.id: (unit.mask, unit.dropped) for unit in check.units})
        return real_prune(check, *args, **kwargs)

    monkeypatch.setattr(fixed, "_drop_culprit", drop_then_no_memory)
    monkeypatch.setattr(fixed, "_prune_in_process", spy_prune)
    summary = fixed.finalize_checkpoint(work, tmp_path / "fallback", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "repair_no_memory" and "settle_dropped" not in summary
    assert seen[0]["g0000005.0"] == (3, False) and seen[0]["g0000006.0"] == (3, False)  # both bits back, not dropped

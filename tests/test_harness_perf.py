"""Harness timing (the build's phase and finalize seconds, the engine settle's write and engine seconds, a cell's step
seconds). Timing lives in the summary and the cell only: the theory and the bindings never carry it, and two builds of the
same inputs write the same theory and bindings bytes."""

import json
import random

import pytest

from mentat_bench import runcell
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import _DropLedger, _Unit
from mentat_bench.manifest import DEFAULT_PROFILE
from test_admit import _binary_markets, _Child, _cross
from test_runcell import PROFILE, _fixture, _with_gold
from helpers_pa_engine import make_fake_engine

PHASES = {"bindings", "blocking", "groups", "relations"}
THEORY_KEYS = {"aliases", "constraints", "interpretations"}
CONSTRAINT_KEYS = {"id", "expr", "interps", "provenance"}
BINDING_KEYS = {"id", "status", "class", "negated", "missing", "evidence", "event_title"}


def _build(out):
    recs, bindings = _binary_markets()
    return fixed.build(recs, _Child(bindings, _cross, with_conf=lambda a, b: 0.5), out, dict(DEFAULT_PROFILE))


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_the_build_summary_carries_the_phase_and_finalize_seconds(tmp_path):
    summary = _build(tmp_path / "sub")
    assert set(summary["phase_seconds"]) == PHASES
    assert set(summary["phase_call_seconds"]) <= PHASES and "bindings" in summary["phase_call_seconds"]
    assert all(v >= 0 for v in summary["phase_seconds"].values())
    for key in ("finalize_seconds", "engine_seconds", "write_seconds"):
        assert isinstance(summary[key], float) and summary[key] >= 0


def test_timing_stays_out_of_the_theory_and_the_bindings(tmp_path):
    _build(tmp_path / "sub")
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text(encoding="utf-8"))
    assert set(theory) == THEORY_KEYS
    assert all(set(row) <= CONSTRAINT_KEYS for row in theory["constraints"])
    assert all(set(row) <= BINDING_KEYS for row in _lines(tmp_path / "sub" / "bindings.jsonl"))


def test_two_builds_of_the_same_inputs_write_the_same_theory_and_bindings(tmp_path):
    _build(tmp_path / "a")
    _build(tmp_path / "b")
    for name in ("theory.json", "bindings.jsonl", "provenance.jsonl"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_the_cell_records_the_seconds_of_each_step(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    runcell.run_cell(fixture, "empty", [], out, PROFILE, [])
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["steps"]["score"] == 0
    assert set(cell["step_seconds"]) == {"build", "seal", "evaluate", "score"}
    assert all(isinstance(v, float) and v >= 0 for v in cell["step_seconds"].values())


def _settle(monkeypatch, tmp_path, units, rejects, stats=None):
    """_settle_with_engine on hand-made units, with the write and the engine stubbed: the submission holds the units not
    dropped (or the probe's include), as {id: op}, and mb-engine rejects it when rejects(present) is true (monotone).
    `stats` is filled in even when the settle raises."""
    seen = {}

    def write(dest, replay, units_, names, usage, profile, include=None):
        seen["present"] = {u.id: u.expr["op"] for n, u in enumerate(units_) if (not u.dropped if include is None else include(n, u))}

    def verdict(out_dir, work, budget_s):
        return ("rejected" if rejects(seen["present"]) else "engine"), "stub"

    monkeypatch.setattr(fixed, "_write_units", write)
    monkeypatch.setattr(fixed, "_engine_verdict", verdict)
    stats = fixed._engine_stats() if stats is None else stats
    status = fixed._settle_with_engine(units, None, tmp_path, tmp_path / "out", [], {}, dict, lambda: 1e9,
                                       _DropLedger(9, 9, None, 1), stats)
    return status, stats


def _group(gid, row):
    unit = _Unit(gid, {"op": "atleast", "k": 2, "args": []}, -1, [], True, "at_least_k")
    unit.row = row
    return unit


def _exactly(cid):
    return _Unit(cid, {"op": "exactly", "k": 1, "args": []}, -1, [], True, "exactly_one")


def test_two_localized_drops_keep_the_bound_and_skip_the_base_probe_after_the_first(tmp_path, monkeypatch):
    units = [_group(f"g{k}", k) for k in range(1, 5)]
    status, stats = _settle(monkeypatch, tmp_path, units, lambda p: "g2" in p or "g3" in p)
    assert status == "engine"
    assert [u.id for u in units if u.dropped] == ["g2", "g3"] and stats["localized_drops"] == 2
    assert stats["rounds"] == 2 and stats["probes"] == 8  # the old settle probed 9 (the base each round, bisection from 0)


def test_a_downgrade_restarts_and_the_next_culprit_is_dropped(tmp_path, monkeypatch):
    units = [_exactly("e1"), _group("g2", 2)]
    status, stats = _settle(monkeypatch, tmp_path, units, lambda p: p.get("e1") == "exactly" or "g2" in p)
    assert status == "engine" and stats["localized_drops"] == 1 and stats["rounds"] == 2 and stats["probes"] == 6
    assert units[0].down and units[0].expr["op"] == "atmost" and not units[0].dropped
    assert units[1].dropped


def test_a_downgrade_resets_the_bound_so_the_units_before_the_culprit_are_checked_again(tmp_path, monkeypatch):
    units = [_group("g1", 1), _exactly("e2"), _group("g3", 3)]  # the engine rejects only with g1 and e2 both present
    status, stats = _settle(monkeypatch, tmp_path, units, lambda p: "g1" in p and "e2" in p)
    assert status == "engine"
    assert [u.id for u in units if u.dropped] == ["g1"] and [u.id for u in units if u.down] == ["e2"]
    assert stats["localized_drops"] == 1  # a bound kept across the downgrade would drop g3, then g1


def test_the_base_is_checked_again_after_a_downgrade(tmp_path, monkeypatch):
    units = [_exactly("e1"), _group("g2", 2)]  # the downgraded at_most_one is now part of the base, and it rejects there
    stats = fixed._engine_stats()
    with pytest.raises(fixed._EngineRejected):
        _settle(monkeypatch, tmp_path, units, lambda p: p.get("e1") in ("exactly", "atmost"), stats)
    assert stats["rounds"] == 2 and stats["localized_drops"] == 0  # refused at once: g2 is never dropped first


def _present(units, kept=None):
    """The submission the engine sees: the units not dropped, and the deferred ones only when their index is in `kept`."""
    return {u.id: u.expr["op"] for n, u in enumerate(units) if not u.dropped and (kept is None or not u.deferred or n in kept)}


def _conflict_engine(conflicts):
    """A monotone stub engine: it rejects when the submission holds every (id, op) of one conflict (op None: any op)."""
    return lambda present: any(all(i in present and op in (None, present[i]) for i, op in conflict) for conflict in conflicts)


def _random_case(rng):
    """Unit kinds (about 30% exactly_one) and conflicts over them. An exactly_one conflicts only while it is still exactly."""
    kinds = ["exactly_one" if rng.random() < 0.3 else "at_least_k" for _ in range(rng.randint(2, 7))]
    conflicts = []
    for _ in range(rng.randint(1, 4)):
        members = rng.sample(range(len(kinds)), rng.randint(1, min(3, len(kinds))))
        conflicts.append([(f"u{i}", "exactly" if kinds[i] == "exactly_one" else None) for i in members])
    return kinds, conflicts


def _units_from(kinds):
    return [_exactly(f"u{i}") if kind == "exactly_one" else _group(f"u{i}", i) for i, kind in enumerate(kinds)]


def _old_settle(units, rejects, cap):
    """Reference: the settle before the bound was carried. Every round checks the base again and bisects from 0. Past `cap`
    rounds the rest are dropped (an exactly_one downgraded) and the base is accepted or refused as it stands (the seal of
    a stopped localization). Returns (rounds, refused, sealed)."""
    rounds = 0
    while True:
        if not rejects(_present(units)):
            return rounds, False, False
        deferred = [n for n, unit in enumerate(units) if unit.deferred and not unit.dropped]
        rounds += 1
        if not deferred:
            return rounds, True, False
        if rounds > cap:
            for n in deferred:
                _settle_unit(units[n])
            refused = rejects(_present(units))
            return rounds, refused, not refused
        if rejects(_present(units, set())):
            return rounds, True, False
        lo, hi = 0, len(deferred)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if rejects(_present(units, set(deferred[:mid]))):
                hi = mid
            else:
                lo = mid
        _settle_unit(units[deferred[lo]])


def _settle_unit(unit):
    unit.deferred = False
    if unit.kind == "exactly_one":
        unit.expr = {"op": "atmost", "k": 1, "args": unit.expr["args"]}
        unit.down = True
    else:
        unit.dropped = True


def test_the_settle_agrees_with_the_reference_on_seeded_engines_at_the_cap_and_without(tmp_path, monkeypatch):
    seen = {"drops": 0, "downgrades": 0, "refused": 0, "sealed": 0}
    for cap in (fixed.MAX_GROUP_DROPS, 2):
        monkeypatch.setattr(fixed, "MAX_GROUP_DROPS", cap)
        for seed in range(300):
            kinds, conflicts = _random_case(random.Random(seed))
            rejects = _conflict_engine(conflicts)
            old, new, stats = _units_from(kinds), _units_from(kinds), fixed._engine_stats()
            old_rounds, old_refused, old_sealed = _old_settle(old, rejects, cap)
            try:
                _settle(monkeypatch, tmp_path, new, rejects, stats)
                new_refused = False
            except fixed._EngineRejected:
                new_refused = True
            assert (new_refused, stats["rounds"]) == (old_refused, old_rounds), (cap, seed)
            assert (stats["deferred_unresolved"] > 0) == old_sealed, (cap, seed)
            assert [u.id for u in new if u.dropped] == [u.id for u in old if u.dropped], (cap, seed)
            assert [u.id for u in new if u.down] == [u.id for u in old if u.down], (cap, seed)
            seen["drops"] += stats["localized_drops"] > 0
            seen["downgrades"] += any(u.down for u in new)
            seen["refused"] += new_refused
            seen["sealed"] += old_sealed
    # the seeds reach drops, downgrades and seals; a refused seal needs a conflict over a non-deferred unit, which this family
    # has none of (test_admit.py tests it): refusals are counted, not required
    assert seen["drops"] and seen["downgrades"] and seen["sealed"], seen

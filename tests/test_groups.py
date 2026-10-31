"""Typed group decisions (decide_group): decision checks, candidate sources, compile shapes and interpretation scopes,
the extended 2-CNF base check against brute force, scripted and fake-child builds, kill/resume and legacy checkpoints,
engine-gated localization, and the gold views. Engine-dependent tests skip without mb-engine."""

import itertools
import json
import random
import sys
from pathlib import Path

import pytest

from mentat_bench import engine, ir
from mentat_bench.adapters.base import AdapterError, DecisionError, ScopeError, check_group, usage
from mentat_bench.adapters.local_jev import LocalJSONLAdapter
from mentat_bench.adapters.mock import EmptyAdapter, GoldViewAdapter, OracleAdapter
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import (
    _BaseCheck,
    _Unit,
    _group_units,
    _interp_combos,
    _interp_names,
    _relation_clauses,
    _scope_mask,
    group_candidates,
)
from mentat_bench.manifest import DEFAULT_PROFILE

REPO = Path(__file__).resolve().parents[1]
HAVE_ENGINE = engine.engine_available()
IDS = {"a", "b", "c", "d"}


def bound(cls, negated=False):
    return {"status": "bound", "class": cls, "negated": negated, "missing": []}


UNRESOLVED = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}


class Scripted:
    """Bindings from a dict (unlisted outcomes are unresolved); relations from a dict, else `relation_default`; groups
    from `decide`. Counts relation and group calls. group_error makes every group call fail. kill_after=N makes the
    (N+1)-th relation call raise, to simulate a killed build."""

    name = "scripted"

    def __init__(self, bindings=None, decide=None, relations=None, group_error=False, kill_after=None,
                 relation_default="NONE"):
        self.bindings = bindings or {}
        self.decide = decide
        self.relations = relations or {}
        self.group_error = group_error
        self.kill_after = kill_after
        self.relation_default = relation_default
        self.relation_calls = 0
        self.group_calls = 0

    def decide_binding(self, ctx):
        return self.bindings.get(ctx["id"], UNRESOLVED), usage(self.name)

    def decide_relation(self, a, b):
        self.relation_calls += 1
        if self.kill_after is not None and self.relation_calls > self.kill_after:
            raise RuntimeError("killed during the relation phase")
        return self.relations.get((a["id"], b["id"]), self.relation_default), usage(self.name)

    def decide_group(self, ctx):
        self.group_calls += 1
        if self.group_error:
            raise AdapterError("group backend down")
        return (self.decide(ctx) if self.decide else []), usage(self.name)


def _records(n, event="E", template="Will X{i} win the cup?", start=1):
    return [{"id": f"v:m{i}:1", "question": template.format(i=i), "label": "Yes", "event_title": event}
            for i in range(start, start + n)]


def _exactly_one_over(ids_wanted):
    def decide(ctx):
        ids = [m["id"] for m in ctx["members"]]
        return [{"kind": "exactly_one", "members": ids}] if set(ids_wanted) <= set(ids) else []

    return decide


def _theory(out):
    return json.loads((Path(out) / "theory.json").read_text(encoding="utf-8"))


# ---- decision checks -------------------------------------------------------------------------------------------

def test_check_group_normalises_each_kind():
    assert check_group({"kind": "exactly_one", "members": ["a", "b", "c"]}, IDS) == {
        "kind": "exactly_one", "members": ["a", "b", "c"], "k": 1, "direction": None, "scope": None}
    assert check_group({"kind": "at_least_k", "members": ["a", "b"], "k": 2}, IDS)["k"] == 2
    assert check_group({"kind": "at_most_k", "members": ["a", "b", "c"], "k": 2}, IDS)["k"] == 2
    assert check_group({"kind": "ordered_chain", "members": ["c", "a"]}, IDS)["direction"] == "forward"
    assert check_group({"kind": "ordered_chain", "members": ["c", "a"], "direction": "reverse"}, IDS)["direction"] == "reverse"
    scoped = check_group({"kind": "at_most_one", "members": ["a", "b"],
                          "scope": {"axis": "tie", "values": ["one"], "of": ["none", "one"]}}, IDS)
    assert scoped["scope"] == {"axis": "tie", "values": ["one"], "of": ["none", "one"]}


@pytest.mark.parametrize("decision", [
    {"kind": "nope", "members": ["a", "b"]},
    {"kind": "exactly_one", "members": ["a"]},
    {"kind": "exactly_one", "members": ["a", "a"]},
    {"kind": "exactly_one", "members": ["a", "z"]},
    {"kind": "exactly_one", "members": ["a", "b"], "k": 2},
    {"kind": "at_least_k", "members": ["a", "b"], "k": 3},
    {"kind": "at_least_k", "members": ["a", "b"], "k": 0},
    {"kind": "at_least_k", "members": ["a", "b"], "k": True},
    {"kind": "at_least_k", "members": ["a", "b"]},
    {"kind": "ordered_chain", "members": ["a", "b"], "k": 1},
    {"kind": "ordered_chain", "members": ["a", "b"], "direction": "sideways"},
    {"kind": "at_most_one", "members": ["a", "b"], "direction": "forward"},
    {"kind": "at_most_one", "members": ["a", "b"], "scope": {"axis": "t", "values": ["x"]}},
    "not an object",
])
def test_check_group_rejects_malformed_decisions(decision):
    with pytest.raises(DecisionError):
        check_group(decision, IDS)


@pytest.mark.parametrize("scope", [
    {"axis": "Tie", "values": ["x"], "of": ["x"]},  # axis name must be [a-z0-9_]
    {"axis": "tie", "values": ["x"], "of": ["y"]},  # values must be a subset of of
    {"axis": "tie", "values": [], "of": ["x"]},
    {"axis": "tie", "values": ["a", "b", "c", "d", "e"], "of": ["a", "b", "c", "d", "e"]},  # more than 4 values
])
def test_check_group_rejects_bad_scopes_as_scope_errors(scope):
    with pytest.raises(ScopeError):
        check_group({"kind": "at_most_one", "members": ["a", "b"], "scope": scope}, IDS)


# ---- candidate sources --------------------------------------------------------------------------------------------

def _candidate_records():
    rows = [("Will Ardent Falcons win the cup?", "v:m0:1", "E"), ("Will Ardent Harbor win the cup?", "v:m1:1", "E"),
            ("Will Brisk Falcons win the cup?", "v:m2:1", "E"), ("Will Brisk Harbor win the cup?", "v:m3:1", "E"),
            ("Will Cobalt Falcons win the cup?", "v:m4:1", "F"), ("Will Cobalt Falcons win the cup?", "v:m4:2", "F")]
    return [{"id": oid, "question": q, "label": "Yes", "event_title": e} for q, oid, e in rows]


def _sets(recs, sources, **kwargs):
    return {tuple(m): src for src, m in group_candidates(recs, sources, **kwargs)}


def test_group_candidate_sources_s1_s2_s3_and_dedupe():
    recs = _candidate_records()
    found = _sets(recs, ("s1", "s2", "s3"))
    assert found[(0, 1, 2, 3)] == "s2"  # the whole event, first source wins over the s3 family with the same members
    assert found[(4, 5)] == "s1"  # two outcomes of one market
    assert found[(0, 2)] == "s3"  # Ardent Falcons / Brisk Falcons: the slot is the first team word
    assert found[(1, 3)] == "s3"
    assert len(list(group_candidates(recs, ("s1", "s2", "s3")))) == len(found)  # each set once
    assert all(len(m) <= 3 for _, m in group_candidates(recs, ("s2",), max_members=3))  # E (4 outcomes) is over the cap


def test_group_candidate_s4_agglomerates_pairs_within_the_bound():
    recs = _candidate_records()
    pairs = [(0, 1), (1, 2)]
    assert (0, 1, 2) in _sets(recs, ("s4",), pairs=pairs)
    bounded = _sets(recs, ("s4",), pairs=pairs, max_members=2)  # merging 0,1 then 2 would exceed the bound
    assert (0, 1) in bounded and (0, 1, 2) not in bounded and (2,) not in bounded


# ---- compile shapes and scopes -------------------------------------------------------------------------------------

def _row(kind, members, k=None, direction=None, scope=None):
    return {"n": 3, "kind": kind, "k": k, "direction": direction, "members": members, "scope": scope, "source": "s2"}


def test_compile_shapes_and_deferred_parts():
    ids = [f"v:m{i}:1" for i in range(6)]
    (one,) = _group_units(_row("exactly_one", [0, 1, 2, 3]), ids, [{}])
    assert one.id == "g0000003" and one.expr["op"] == "exactly" and one.expr["k"] == 1 and one.deferred
    (pair,) = _group_units(_row("exactly_one", [0, 1]), ids, [{}])
    assert pair.expr["op"] == "exactly" and not pair.deferred  # ALO over two is a clause: fully 2-CNF
    (amo,) = _group_units(_row("at_most_one", [0, 1, 2]), ids, [{}])
    assert amo.expr == {"op": "atmost", "k": 1, "args": [ir.var(i) for i in ids[:3]]} and not amo.deferred
    chain = _group_units(_row("ordered_chain", [0, 1, 2], direction="reverse"), ids, [{}])
    assert [u.id for u in chain] == ["g0000003.0", "g0000003.1"]
    assert chain[0].expr == {"op": "implies", "a": ir.var(ids[1]), "b": ir.var(ids[0])} and not chain[0].deferred
    (alo2,) = _group_units(_row("at_least_k", [0, 1], k=1), ids, [{}])
    assert alo2.expr["op"] == "atleast" and not alo2.deferred
    (alo3,) = _group_units(_row("at_least_k", [0, 1, 2], k=2), ids, [{}])
    assert alo3.deferred
    (atmost2,) = _group_units(_row("at_most_k", [0, 1, 2], k=2), ids, [{}])
    assert atmost2.expr["k"] == 2 and atmost2.deferred
    (trivial,) = _group_units(_row("at_most_k", [0, 1, 2], k=3), ids, [{}])
    assert not trivial.deferred and trivial.clauses == []


def test_interpretations_are_the_axis_cross_product():
    combos = _interp_combos({"tie": ["none", "one"], "boundary": ["excl", "incl"]})
    assert _interp_names(combos) == ["boundary=excl|tie=none", "boundary=excl|tie=one",
                                     "boundary=incl|tie=none", "boundary=incl|tie=one"]
    assert _interp_names(_interp_combos({})) == ["base"]
    assert _scope_mask({"axis": "tie", "values": ["one"], "of": ["none", "one"]}, combos) == 0b1010
    assert _scope_mask(None, combos) == -1


# ---- the extended 2-CNF base check against brute force ----------------------------------------------------------

class _Stub:
    """A replay-shaped theory with n outcomes and no bindings."""

    def __init__(self, n):
        self.ids = [f"v:m{i}:1" for i in range(n)]

    def rows(self):
        return iter([{"id": i, "status": "unresolved", "class": None, "negated": False, "missing": ["m"]} for i in self.ids])


def _holds(spec, x, n):
    kind = spec[0]
    if kind == "rel":
        _, i, j, rel = spec
        return {"IMPLIES": (not x[i]) or x[j], "IMPLIED_BY": (not x[j]) or x[i],
                "EXCLUDES": not (x[i] and x[j]), "EQUIV": x[i] == x[j]}[rel]
    if kind == "chain":
        return all((not x[a]) or x[b] for a, b in spec[1])
    _, gkind, k, members = spec
    total = sum(x[m] for m in members)
    return {"exactly_one": total == 1, "at_most_one": total <= 1, "at_most_k": total <= k, "at_least_k": total >= k}[gkind]


def _random_spec(rng, n):
    if rng.random() < 0.3:
        i, j = rng.sample(range(n), 2)
        return ("rel", i, j, rng.choice(["IMPLIES", "IMPLIED_BY", "EXCLUDES", "EQUIV"]))
    if rng.random() < 0.2:
        members = rng.sample(range(n), rng.randint(2, n))
        return ("chain", list(zip(members, members[1:])), members)
    members = rng.sample(range(n), rng.randint(2, n))
    gkind = rng.choice(["exactly_one", "at_most_one", "at_most_k", "at_least_k"])
    k = 1 if gkind in ("exactly_one", "at_most_one") else rng.randint(1, len(members))
    return ("group", gkind, k, members)


def _units_of(spec, ids):
    if spec[0] == "rel":
        _, i, j, rel = spec
        return [_Unit(f"p{i}{j}", None, -1, _relation_clauses(i, j, rel), False, "relation")]
    if spec[0] == "chain":
        row = {"n": 0, "kind": "ordered_chain", "k": None, "direction": "forward", "members": spec[2], "scope": None}
        return _group_units(row, ids, [{}])
    _, gkind, k, members = spec
    row = {"n": 0, "kind": gkind, "k": k if gkind not in ("exactly_one", "at_most_one") else None,
           "direction": None, "members": members, "scope": None}
    return _group_units(row, ids, [{}])


@pytest.mark.parametrize("seed", range(3))
def test_two_cnf_base_check_agrees_with_brute_force(seed):
    """Random small theories, each unit scoped to a random subset of two interpretations. Exact units (fully 2-CNF)
    must agree with brute force; deferred units only bound it: an in-process conflict implies a real conflict."""
    rng = random.Random(seed)
    checked = exact_cases = 0
    for _ in range(120):
        n = rng.randint(2, 5)
        stub = _Stub(n)
        units = []
        for spec in (_random_spec(rng, n) for _ in range(rng.randint(1, 4))):
            mask = rng.choice([-1, 0b01, 0b10, 0b11])
            for unit in _units_of(spec, stub.ids):
                unit.mask = mask
                units.append((unit, spec))
        check = _BaseCheck(stub, [u for u, _ in units], n_interps=2)
        exact = not any(u.deferred for u, _ in units)
        for interp in range(2):
            sat = False
            for bits in itertools.product([False, True], repeat=n):
                if all(_holds(spec, bits, n) for unit, spec in units if (unit.mask >> interp) & 1):
                    sat = True
                    break
            in_process_unsat = check.unsat(len(units), set(), interp)
            if exact:
                assert in_process_unsat == (not sat), (seed, interp)
                exact_cases += 1
            elif in_process_unsat:
                assert not sat, (seed, interp)
            checked += 1
    assert checked > 0 and exact_cases > 0


# ---- builds with scripted adapters --------------------------------------------------------------------------------

def test_group_decision_compiles_and_every_pair_is_still_asked(tmp_path):
    records = _records(4)
    adapter = Scripted(decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1", "v:m4:1"]))
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), gold_groups=[
        {"kind": "exactly_one", "k": 1, "members": ["v:m1:1", "v:m2:1", "v:m3:1", "v:m4:1"], "interps": None}])
    theory = _theory(tmp_path / "sub")
    assert [c["id"] for c in theory["constraints"]] == ["g0000000"]
    assert theory["constraints"][0]["expr"]["op"] == "exactly"
    assert summary["groups_committed"] == 1 and summary["group_candidates"] >= 1
    # no pair is skipped because a group covers it (the key stays for compatibility, at 0)
    assert summary["pairs_skipped_by_groups"] == 0
    assert adapter.relation_calls == summary["candidate_pairs"] > 0
    assert summary["group_recall"] == 1.0 and summary["gold_groups"] == 1
    assert summary["interpretations"] == ["base"] and summary["group_errors"] == 0
    policy = json.loads((tmp_path / "sub" / "build_policy.json").read_text())
    assert policy["groups"] is True and policy["adapter"] == "scripted"
    assert policy["group_budget_frac"] == 0.25 and policy["max_group_calls"] == 20000


def test_relations_inside_accepted_groups_are_kept(tmp_path):
    """H1: a pair inside an accepted at_most_one group that answers EQUIV, and a pair inside an accepted chain that
    answers IMPLIED_BY or EXCLUDES, must all appear in theory.json (no covered-pair skip)."""
    records = _records(3)

    def decide(ctx):
        ids = [m["id"] for m in ctx["members"]]
        if len(ids) != 3:
            return []
        return [{"kind": "at_most_one", "members": ids[:2]},
                {"kind": "ordered_chain", "members": ids[1:]}]

    relations = {("v:m1:1", "v:m2:1"): "EQUIV", ("v:m2:1", "v:m3:1"): "IMPLIED_BY", ("v:m1:1", "v:m3:1"): "EXCLUDES"}
    adapter = Scripted(decide=decide, relations=relations)
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["groups_committed"] == 2 and summary["pairs_skipped_by_groups"] == 0
    assert adapter.relation_calls == summary["candidate_pairs"] == 3
    constraints = _theory(tmp_path / "sub")["constraints"]
    relation_ops = sorted((c["id"], c["expr"]["op"]) for c in constraints if c["id"].startswith("p"))
    assert sorted(op for _, op in relation_ops) == ["atmost", "iff", "implies"]
    implies = next(c for c in constraints if c["expr"]["op"] == "implies")["expr"]
    assert implies["a"]["id"] == "v:m3:1" and implies["b"]["id"] == "v:m2:1"  # IMPLIED_BY(m2, m3): m3 implies m2
    iff = next(c for c in constraints if c["expr"]["op"] == "iff")["expr"]
    assert {iff["a"]["id"], iff["b"]["id"]} == {"v:m1:1", "v:m2:1"}


def test_scoped_decision_gives_interpretations_and_scoped_constraints(tmp_path):
    scope = {"axis": "tie", "values": ["one"], "of": ["none", "one"]}
    records = _records(3)

    def decide(ctx):
        ids = [m["id"] for m in ctx["members"]]
        return [{"kind": "at_most_one", "members": ids[:2], "scope": scope}] if len(ids) == 3 else []

    summary = fixed.build(records, Scripted(decide=decide), tmp_path / "sub", dict(DEFAULT_PROFILE))
    theory = _theory(tmp_path / "sub")
    assert theory["interpretations"] == ["tie=none", "tie=one"]
    assert theory["constraints"][0]["interps"] == ["tie=one"]
    assert summary["axes"] == {"tie": ["none", "one"]} and summary["interpretations"] == ["tie=none", "tie=one"]


def test_inadmissible_decisions_are_rejected_and_counted(tmp_path):
    records = _records(3)
    bindings = {"v:m1:1": bound("c"), "v:m2:1": bound("c")}  # same class, same polarity: one literal twice

    # AMO over one literal (the two bound members) is rejected, whatever else the candidate proposes
    decision = {"kind": "at_most_one", "members": ["v:m1:1", "v:m2:1"]}
    adapter = Scripted(bindings, decide=lambda ctx: [decision] if len(ctx["members"]) == 3 else [])
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["groups_rejected"] >= 1 and summary["groups_committed"] == 0
    assert _theory(tmp_path / "sub")["constraints"] == []


def test_scope_caps_are_counted_as_scope_rejections(tmp_path):
    scope_x = {"axis": "a", "values": ["x", "y", "z", "w"], "of": ["x", "y", "z", "w"]}
    scope_y = {"axis": "b", "values": ["p", "q", "r"], "of": ["p", "q", "r"]}  # 4 x 3 = 12 interpretations > 8

    def decide(ctx):
        ids = [m["id"] for m in ctx["members"]]
        return [{"kind": "at_most_one", "members": ids[:2], "scope": scope_x},
                {"kind": "at_most_one", "members": ids[1:], "scope": scope_y}] if len(ids) == 3 else []

    summary = fixed.build(_records(3), Scripted(decide=decide), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["group_scope_rejected"] >= 1 and summary["groups_committed"] == 1
    assert summary["interpretations"] == ["a=w", "a=x", "a=y", "a=z"]


def test_group_failures_do_not_trip_relation_health(tmp_path):
    records = _records(4)
    adapter = Scripted(group_error=True, relations={("v:m1:1", "v:m2:1"): "EXCLUDES"})
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["group_errors"] >= 1 and summary["groups_committed"] == 0
    assert summary["relations_committed"] > 0 and not summary["adapter_fatal"]
    assert all(c["id"].startswith("p") for c in _theory(tmp_path / "sub")["constraints"])


def test_adapters_without_decide_group_make_no_group_decisions(tmp_path):
    class NoGroups:
        name = "nogroups"

        def decide_binding(self, ctx):
            return UNRESOLVED, usage(self.name)

        def decide_relation(self, a, b):
            return "NONE", usage(self.name)

    summary = fixed.build(_records(3), NoGroups(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["group_candidates"] == 0 and summary["groups_committed"] == 0
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text())["groups"] is False


def test_use_groups_false_skips_the_group_phase(tmp_path):
    adapter = Scripted(decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]))
    summary = fixed.build(_records(3), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), use_groups=False)
    assert adapter.group_calls == 0 and summary["groups_committed"] == 0 and summary["relations_committed"] > 0


def test_kill_in_relation_phase_then_finalize_from_group_checkpoint(tmp_path):
    records = _records(3) + _records(1, event="F", template="Will Q{i} win the match?", start=4)
    adapter = Scripted(decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]), relation_default="IMPLIES", kill_after=1)
    out = tmp_path / "sub"
    with pytest.raises(RuntimeError, match="killed"):
        fixed.build(records, adapter, out, dict(DEFAULT_PROFILE), checkpoint_every=1)
    work = out.with_name(out.name + ".build")
    ckpt = json.loads((work / "checkpoint.json").read_text())
    assert ckpt["groups_committed"] == 1 and ckpt["groups_bytes"] > 0
    assert ckpt["relations_committed"] == 1  # the first relation batch was committed before the kill
    summary = fixed.finalize_checkpoint(work, tmp_path / "recovered", dict(DEFAULT_PROFILE), probe=False)
    assert summary["groups_committed"] == 1 and summary["constraints"] == 2
    ids = [c["id"] for c in _theory(tmp_path / "recovered")["constraints"]]
    assert "g0000000" in ids and "p0000000" in ids


def test_checkpoint_without_group_keys_still_finalizes(tmp_path):
    records = _records(3) + _records(1, event="F", template="Will Q{i} win the match?", start=4)
    adapter = Scripted(relations={("v:m1:1", "v:m4:1"): "IMPLIES"}, kill_after=0)
    out = tmp_path / "sub"
    with pytest.raises(RuntimeError, match="killed"):
        fixed.build(records, adapter, out, dict(DEFAULT_PROFILE), checkpoint_every=1)
    work = out.with_name(out.name + ".build")
    ckpt = json.loads((work / "checkpoint.json").read_text())
    for key in ("groups_bytes", "group_candidates", "groups_committed", "groups_rejected", "group_errors",
                "group_scope_rejected", "group_recall", "gold_groups", "axes", "pairs_skipped_by_groups",
                "groups_fatal", "policy"):
        ckpt.pop(key, None)
    (work / "checkpoint.json").write_text(json.dumps(ckpt, sort_keys=True))
    (work / "groups.jsonl").unlink(missing_ok=True)
    summary = fixed.finalize_checkpoint(work, tmp_path / "old", dict(DEFAULT_PROFILE), probe=False)
    assert summary["groups_committed"] == 0 and summary["group_candidates"] == 0
    assert not (tmp_path / "old" / "build_policy.json").exists()
    assert all(c["id"].startswith("p") for c in _theory(tmp_path / "old")["constraints"])


def _anchored_case():
    """Three outcomes of one event, each bound to a class that an anchor pair forces false (the anchors' IMPLIES
    makes class F_k false). The pairwise AMO of exactly_one is consistent; its ALO is violated, and only the
    engine checks the ALO (a deferred part)."""
    members = _records(3)
    anchors, bindings, relations = [], {}, {}
    for k in (1, 2, 3):
        anchors += [{"id": f"v:f{k}:1", "question": f"Anchor {k} yes", "label": "Yes", "event_title": "A"},
                    {"id": f"v:f{k}:2", "question": f"Anchor {k} no", "label": "No", "event_title": "A"}]
        bindings[f"v:m{k}:1"] = bound(f"F{k}")
        bindings[f"v:f{k}:1"] = bound(f"F{k}")
        bindings[f"v:f{k}:2"] = bound(f"F{k}", negated=True)
        relations[(f"v:f{k}:1", f"v:f{k}:2")] = "IMPLIES"
    return members + anchors, bindings, relations


@pytest.mark.skipif(not HAVE_ENGINE, reason="mb-engine not built")
def test_engine_localizes_an_alo_conflict_and_downgrades_it(tmp_path):
    """The three members form an implication cycle (m1 -> m2 -> m3 -> m1), so the relations alone allow all of them true.
    The exactly_one's pairwise AMO makes them all false, so its at least one is impossible, but only with the AMO: the
    non-deferred base (N0) leaves it possible, so the per-unit check does not take it. The engine localizes the exactly_one
    and downgrades it to at_most_one (the AMO stays), and then accepts the submission."""
    records = _records(3)
    bindings = {f"v:m{k}:1": bound(f"M{k}") for k in (1, 2, 3)}
    relations = {("v:m1:1", "v:m2:1"): "IMPLIES", ("v:m2:1", "v:m3:1"): "IMPLIES", ("v:m1:1", "v:m3:1"): "IMPLIED_BY"}  # the pair is asked in index order: m3 -> m1
    adapter = Scripted(bindings, relations=relations, decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]))
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["base_probe"] == "engine" and summary["groups_downgraded"] == 1 and summary["groups_dropped"] == 0
    assert summary["deferred_infeasible_downgraded"] == 0 and summary["engine_rounds"] >= 1  # the localization did it
    (con,) = [c for c in _theory(tmp_path / "sub")["constraints"] if c["id"].startswith("g")]
    assert con["expr"]["op"] == "atmost" and con["expr"]["k"] == 1


def test_engine_free_run_keeps_deferred_constraints_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: False)
    records, bindings, relations = _anchored_case()
    adapter = Scripted(bindings, relations=relations, decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]))
    with pytest.warns(UserWarning, match="mb-engine not found"):
        summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["base_probe"] == "skipped" and summary["groups_downgraded"] == 0 and summary["groups_committed"] == 1


# ---- local protocol v2 with fake children --------------------------------------------------------------------------

CHILD = r'''
import json, sys
mode = sys.argv[1]
for line in sys.stdin:
    req = json.loads(line)
    op = req["op"]
    if op == "hello" and mode == "badops":
        out = {"protocol": 2, "ops": []}
    elif op == "hello":
        cap = 2 if mode == "v2small" else 64
        out = ({"protocol": 2, "name": "fake", "version": "0", "ops": {"decide_binding": {}, "decide_relation": {}, "decide_group": {"max_members": cap}}}
               if mode.startswith("v2") else {"error": "unknown op hello"})
    elif op == "decide_binding":
        out = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
    elif op == "decide_relation":
        out = {"relation": "NONE"}
    else:
        ids = [m["id"] for m in req["ctx"]["members"]]
        out = {"groups": [{"kind": "at_most_one", "members": ids[:2]}] if len(ids) >= 2 else []}
    out["id"] = req["id"]
    print(json.dumps(out), flush=True)
'''


def _child(tmp_path, mode):
    script = tmp_path / f"child_{mode}.py"
    script.write_text(CHILD, encoding="utf-8")
    return [sys.executable, str(script), mode]


def test_protocol_2_handshake_enables_groups(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "v2"), name="v2", timeout_s=20, protocol=2) as adapter:
        assert adapter.supports_groups is True
        summary = fixed.build(_records(3), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["groups_committed"] == 1 and summary["group_errors"] == 0


@pytest.mark.parametrize("protocol", [1, 2])
def test_protocol_1_or_failed_hello_keeps_groups_off(tmp_path, protocol):
    with LocalJSONLAdapter(_child(tmp_path, "v1"), name="v1", timeout_s=20, protocol=protocol) as adapter:
        assert adapter.supports_groups is False
        summary = fixed.build(_records(3), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["group_candidates"] == 0 and summary["groups_committed"] == 0
    assert summary["relations_committed"] > 0


def test_protocol_must_be_1_or_2(tmp_path):
    with pytest.raises(ValueError, match="protocol"):
        LocalJSONLAdapter(_child(tmp_path, "v2"), protocol=3)


# ---- gold views -----------------------------------------------------------------------------------------------------

def _scoped_gold(tmp_path):
    gold = tmp_path / "gold"
    gold.mkdir()
    ids = ["v:m1:1", "v:m2:1"]
    bindings = [{"id": oid, "status": "bound", "class": f"C{i}", "negated": False, "missing": []}
                for i, oid in enumerate(ids)]
    expr = lambda op: {"op": op, "k": 1, "args": [ir.var(oid) for oid in ids]}
    theory = {"interpretations": ["base", "alt"], "aliases": {}, "constraints": [
        {"id": "scoped", "expr": expr("exactly"), "interps": ["base"]},
        {"id": "unscoped", "expr": expr("atmost"), "interps": None},
    ]}
    ir.write_jsonl(gold / "bindings.jsonl", bindings)
    ir.write_json(gold / "theory.json", theory)
    return gold


def _first_scoped_gold_group(gold):
    theory = json.loads((gold / "theory.json").read_text(encoding="utf-8"))
    return next(g for g in fixed.gold_groups_from_theory(theory) if g["interps"] is not None and len(g["members"]) == 2)


@pytest.mark.parametrize("adapter_cls", [OracleAdapter, GoldViewAdapter])
def test_gold_views_answer_gold_groups_inside_the_context(adapter_cls, tmp_path):
    gold_dir = _scoped_gold(tmp_path)
    group = _first_scoped_gold_group(gold_dir)
    adapter = adapter_cls(gold_dir)
    ctx = {"group_id": "c0", "source": "s2", "members": [{"id": m} for m in group["members"]]}
    decisions, _ = adapter.decide_group(ctx)
    # gold also has an at_most_one over the same pair (unscoped); the scoped exactly_one is the one asked for
    match = [d for d in decisions if sorted(d["members"]) == sorted(group["members"]) and d["kind"] == group["kind"]]
    assert len(match) == 1
    assert match[0]["scope"]["axis"] == "gold" and sorted(match[0]["scope"]["values"]) == sorted(group["interps"])
    partial = {"group_id": "c1", "source": "s2", "members": [{"id": group["members"][0]}]}
    assert adapter.decide_group(partial)[0] == []


def test_empty_adapter_decides_no_groups():
    assert EmptyAdapter().decide_group({"group_id": "c", "source": "s2", "members": [{"id": "v:m1:1"}]})[0] == []


def test_group_recall_is_the_share_of_gold_groups_inside_some_candidate():
    position = {"a": 0, "b": 1, "c": 2, "d": 3}
    gold = [{"members": ["a", "b"]}, {"members": ["a", "c"]}, {"members": ["a", "z"]}]
    recall = fixed._GroupRecall(gold, position)
    recall.feed([0, 1, 2])
    assert recall.value() == round(2 / 3, 6)  # a-b and a-c inside; a-z has a member outside the corpus
    recall.feed([0, 3])
    assert recall.value() == round(2 / 3, 6)


# ---- fix round 1: budgets, engine probes, scoped pruning, admission, caps, gold views, exit codes ------------------

def test_group_phase_budget_stops_only_the_group_phase(tmp_path):
    """H2: reaching max_group_calls ends the group phase (group_budget_stopped); the global budget is untouched and
    the relation phase still runs."""
    records = _records(3) + _records(2, event="F", template="Will Q{i} win the match?", start=4)
    adapter = Scripted(decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]), relation_default="IMPLIES")
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), max_group_calls=1)
    assert summary["group_budget_stopped"] is True and summary["budget_stopped"] is False
    assert summary["group_candidates"] == 1 and summary["relations_committed"] > 0
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text())["max_group_calls"] == 1


def test_undecided_engine_probe_is_incomplete_not_accepted(tmp_path, monkeypatch):
    """M1: a localization probe that the deadline cuts short is BaseCheckIncomplete, never an acceptance."""
    records, bindings, relations = _anchored_case()

    def verdict(out_dir, work, budget_s):
        return ("rejected", "conflict") if out_dir.name == "sub" else ("skipped_deadline", "")

    monkeypatch.setattr(fixed, "_engine_verdict", verdict)
    # BC2 would downgrade this exactly_one before any probe (its ALO is impossible alone): this test is about the localization probe
    monkeypatch.setattr(fixed, "_drop_infeasible_deferred", lambda *args: ([], []))
    adapter = Scripted(bindings, relations=relations, decide=_exactly_one_over(["v:m1:1", "v:m2:1", "v:m3:1"]))
    with pytest.raises(fixed.BaseCheckIncomplete):
        fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))


def test_engine_rejection_with_no_deferred_unit_is_final(tmp_path, monkeypatch):
    """M1: the engine rejecting the base with no deferred unit to blame is an invalid submission, at once."""
    monkeypatch.setattr(fixed, "_engine_verdict", lambda out_dir, work, budget_s: ("rejected", "boom"))
    with pytest.raises(fixed.BaseInconsistentError, match="boom"):
        fixed.build(_records(2), Scripted(), tmp_path / "sub", dict(DEFAULT_PROFILE))


def test_scoped_conflict_loses_only_the_conflicting_interpretation(tmp_path):
    """M3: two scoped groups that contradict each other only where both hold. at_most_one over m1 and m2 (scope window=x)
    and at_least_k with k 2 over the same two (scope tie=one) cannot both hold: they are inconsistent exactly in
    tie=one|window=x. Pruning clears that interpretation from the at_least_k group only: it keeps tie=one|window=y, and
    the at_most_one group is untouched. Without scopes the same pair is a contradiction of the whole theory, and one group
    is dropped (groups_dropped 1), so groups_dropped 0 is what the scoping is checked by."""
    scope_a = {"axis": "window", "values": ["x"], "of": ["x", "y"]}
    scope_b = {"axis": "tie", "values": ["one"], "of": ["none", "one"]}
    bindings = {"v:m1:1": bound("c"), "v:m2:1": bound("d")}

    def decide(ctx):
        if len(ctx["members"]) != 2:
            return []
        return [{"kind": "at_most_one", "members": ["v:m1:1", "v:m2:1"], "scope": scope_a},
                {"kind": "at_least_k", "k": 2, "members": ["v:m1:1", "v:m2:1"], "scope": scope_b}]

    summary = fixed.build(_records(2), Scripted(bindings, decide=decide), tmp_path / "sub", dict(DEFAULT_PROFILE))
    constraints = {c["id"]: c for c in _theory(tmp_path / "sub")["constraints"]}
    assert set(constraints) == {"g0000000", "g0000001"}
    assert constraints["g0000000"]["interps"] == ["tie=none|window=x", "tie=one|window=x"]
    assert constraints["g0000001"]["interps"] == ["tie=one|window=y"]
    assert summary["groups_dropped"] == 0 and summary["interpretations"] == [
        "tie=none|window=x", "tie=none|window=y", "tie=one|window=x", "tie=one|window=y"]


def test_amo_members_that_merge_into_one_literal_are_dropped_at_finalize(tmp_path):
    """L2: at_most_k with k=1 over two classes that an EQUIV merges into one literal is degenerate: dropped."""
    bindings = {"v:m1:1": bound("c"), "v:m2:1": bound("d")}

    def decide(ctx):
        ids = [m["id"] for m in ctx["members"]]
        return [{"kind": "at_most_k", "members": ids, "k": 1}] if len(ids) == 2 else []

    summary = fixed.build(_records(2), Scripted(bindings, decide=decide, relations={("v:m1:1", "v:m2:1"): "EQUIV"}),
                          tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["groups_committed"] == 1 and summary["groups_dropped"] == 1
    ops = [c["expr"]["op"] for c in _theory(tmp_path / "sub")["constraints"]]
    assert ops == ["iff"]


def test_at_most_k_with_k_one_rejects_one_literal_twice_at_admission(tmp_path):
    """L2: the duplicate-literal check covers at_most_k with k=1 too."""
    bindings = {"v:m1:1": bound("c"), "v:m2:1": bound("c")}

    def decide(ctx):
        ids = [m["id"] for m in ctx["members"]]
        return [{"kind": "at_most_k", "members": ids, "k": 1}] if len(ids) == 2 else []

    summary = fixed.build(_records(2), Scripted(bindings, decide=decide), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["groups_rejected"] >= 1 and summary["groups_committed"] == 0


def test_group_size_is_capped_by_the_flag_limit(tmp_path):
    """L3: group_max_members above 256 is refused by build and by the flag parser."""
    with pytest.raises(ValueError, match="at most 256"):
        fixed.build(_records(2), Scripted(), tmp_path / "a", dict(DEFAULT_PROFILE), group_max_members=257)
    from mentat_bench import cli

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["build", "--adapter", "empty", "--corpus", "x", "--out", "y",
                                       "--group-max-members", "300"])


def test_advertised_max_members_caps_the_group_size(tmp_path):
    """L4: a child that advertises max_members 2 never receives a larger candidate."""
    script = tmp_path / "child_small.py"
    script.write_text(CHILD, encoding="utf-8")
    records = _records(3) + _records(2, event="F", template="Will Q{i} win the match?", start=4)
    with LocalJSONLAdapter([sys.executable, str(script), "v2small"], name="small", timeout_s=20, protocol=2) as adapter:
        assert adapter.advertised_group_max_members == 2
        summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text())["group_max_members"] == 2
    assert summary["groups_committed"] >= 1
    for c in _theory(tmp_path / "sub")["constraints"]:
        if "args" in c["expr"]:
            assert len(c["expr"]["args"]) <= 2


def test_malformed_hello_falls_back_to_protocol_1(tmp_path):
    """L1: a hello whose ops is not an object kills the child and leaves the adapter at protocol 1."""
    script = tmp_path / "child_badops.py"
    script.write_text(CHILD, encoding="utf-8")
    with LocalJSONLAdapter([sys.executable, str(script), "badops"], name="bad", timeout_s=20, protocol=2) as adapter:
        assert adapter.supports_groups is False and adapter.protocol == 1
        assert adapter.decide_binding({"id": "v:m1:1", "question": "q", "label": "Yes", "event_title": None})[0]["status"] == "unresolved"


def test_empty_adapter_skips_the_group_phase(tmp_path):
    """L8: EmptyAdapter's decide_group is a stub; it declares no group support, so no candidate is made."""
    summary = fixed.build(_records(3), EmptyAdapter(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["group_candidates"] == 0 and summary["groups_committed"] == 0
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text())["groups"] is False


def test_group_phase_fatal_exits_3_and_seals_nothing(tmp_path, monkeypatch):
    """M2: a group-phase adapter failure is an adapter_fatal: exit 3, nothing sealed."""
    from mentat_bench import cli
    from mentat_bench.seal import INCOMPLETE_FILE

    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(json.dumps({"id": f"v:m{i}:1", "text": "Will X win?", "label": "Yes", "event_title": "E"}) + "\n"
                              for i in (1, 2)), encoding="utf-8")
    def fatal(records, adapter, out_dir, *args, **kwargs):
        Path(out_dir).mkdir()  # the finalized checkpoint is kept in out_dir
        return {"adapter_fatal": True, "groups_fatal": True}

    monkeypatch.setattr(cli.fixed, "build", fatal)
    out = tmp_path / "out"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(out)]) == 3
    assert not (out / "build_id.json").exists() and not (out / "seal.json").exists()
    assert "group phase" in json.loads((out / INCOMPLETE_FILE).read_text())["reason"]
    assert cli.main(["seal", "--submission", str(out)]) == cli.EXIT_SEAL


def _synthetic_gold(tmp_path):
    """A small corpus and gold directory: an implies, an exactly-one over three outcomes (its ALO is deferred), and an
    `or` (at least one) over two outcomes; m3 is unresolved and only the `or` touches it."""
    ids = {"A": "v:m1:1", "B": "v:m2:1", "C": "v:m4:1"}
    corpus = [{"id": f"v:m{i}:1", "text": f"Will X{i} win the cup?", "label": "Yes", "event_title": "E"} for i in (1, 2, 3, 4)]
    corpus.append({"id": "v:m5:1", "text": "Will Q5 win the match?", "label": "Yes", "event_title": "F"})
    gold = tmp_path / "gold"
    gold.mkdir()
    bindings = [
        {"id": "v:m1:1", "status": "bound", "class": "A", "negated": False, "missing": []},
        {"id": "v:m2:1", "status": "bound", "class": "B", "negated": False, "missing": []},
        {"id": "v:m3:1", "status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]},
        {"id": "v:m4:1", "status": "bound", "class": "C", "negated": False, "missing": []},
        {"id": "v:m5:1", "status": "bound", "class": "D", "negated": False, "missing": []},
    ]
    constraints = [
        {"id": "c1", "expr": {"op": "implies", "a": ir.var(ids["A"]), "b": ir.var(ids["B"])}, "interps": None},
        {"id": "c2", "expr": {"op": "exactly", "k": 1, "args": [ir.var(ids[k]) for k in "ABC"]}, "interps": None},
        {"id": "c3", "expr": {"op": "or", "args": [ir.var(ids["B"]), ir.var("v:m3:1")]}, "interps": None},
    ]
    ir.write_jsonl(gold / "bindings.jsonl", bindings)
    ir.write_json(gold / "theory.json", {"interpretations": ["base"], "aliases": {}, "constraints": constraints})
    return corpus, gold


def test_gold_view_pipeline_on_a_synthetic_fixture(tmp_path):
    """The GoldView adapter through the whole build: gold bindings, gold pairs and gold groups, no theory replay."""
    corpus, gold = _synthetic_gold(tmp_path)
    records = [{"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r["event_title"]} for r in corpus]
    theory = json.loads((gold / "theory.json").read_text(encoding="utf-8"))
    summary = fixed.build(records, GoldViewAdapter(gold), tmp_path / "sub", dict(DEFAULT_PROFILE),
                          gold_pairs=fixed.gold_pairs_from_theory(theory), gold_groups=fixed.gold_groups_from_theory(theory))
    assert summary["mode"] == "pairwise" and summary["groups_committed"] == 2 and summary["group_recall"] == 1.0
    assert summary["gold_pairs"] == 1 and summary["gold_groups"] == 2
    ops = sorted(c["expr"]["op"] for c in _theory(tmp_path / "sub")["constraints"])
    assert ops == ["atleast", "exactly", "implies"]


def test_cli_build_reports_gold_recall_for_gold_view(tmp_path, capsys):
    """The CLI passes gold metrics to a gold_view build, so group_recall is a number, not null."""
    from mentat_bench import cli

    corpus, gold = _synthetic_gold(tmp_path)
    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text("".join(json.dumps(r) + "\n" for r in corpus), encoding="utf-8")
    out = tmp_path / "sub"
    assert cli.main(["build", "--adapter", "gold_view", "--gold", str(gold), "--corpus", str(corpus_path),
                     "--out", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["group_recall"] == 1.0 and summary["gold_groups"] == 2
    assert summary["gold_pairs"] == 1 and summary["blocking_recall"] == 1.0


def test_evaluate_warning_names_gold_reading_adapters(tmp_path):
    """L7: a submission built by the oracle or gold_view adapter gets a warning; other adapters do not."""
    from mentat_bench import cli

    (tmp_path / "build_policy.json").write_text(json.dumps({"adapter": "gold_view"}), encoding="utf-8")
    assert "gold_view adapter, which reads gold" in cli._adapter_warning(tmp_path)
    (tmp_path / "build_policy.json").write_text(json.dumps({"adapter": "local"}), encoding="utf-8")
    assert cli._adapter_warning(tmp_path) is None
    assert cli._adapter_warning(tmp_path / "missing") is None


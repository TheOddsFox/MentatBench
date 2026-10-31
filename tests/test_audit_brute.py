"""Tests for the independent gold checker python/mentat_bench/audit_brute.py.

Hand-made theories with answers worked out by hand; DPLL against exhaustive enumeration on random theories; a
cross-check against mb-engine (MB_ENGINE, skipped without it); and the committed fixtures (engine-free, slow).
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest

from mentat_bench import audit_brute as ab

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- helpers

def _var(oid):
    return {"op": "var", "id": oid}


def _not(e):
    return {"op": "not", "a": e}


def _or(*args):
    return {"op": "or", "args": list(args)}


def _implies(a, b):
    return {"op": "implies", "a": a, "b": b}


def _iff(a, b):
    return {"op": "iff", "a": a, "b": b}


def _card(op, k, *args):
    return {"op": op, "k": k, "args": list(args)}


def _bound(oid, negated=False):
    return {"id": oid, "status": "bound", "class": f"K_{oid}", "negated": negated, "missing": [], "evidence": []}


def _unresolved(oid, missing=("no_date",)):
    return {"id": oid, "status": "unresolved", "class": None, "negated": False, "missing": list(missing),
            "evidence": []}


def _theory(interps, bound_ids, constraints):
    """constraints: list of (expr, interps-or-None). Each bound outcome gets the class alias K_<id> = var(id)."""
    return {
        "interpretations": list(interps),
        "aliases": {f"K_{o}": _var(o) for o in bound_ids},
        "constraints": [{"id": f"c{n:07d}", "expr": e, "interps": ii, "provenance": []}
                        for n, (e, ii) in enumerate(constraints)],
    }


def _checker(theory, bindings, queries=()):
    th = ab.Theory(theory, bindings, list(queries))
    ck = ab.Checker(th, budget=10**6)
    ck.check_base()
    return ck


def _q(family, qid="q", **kw):
    return {"qid": qid, "family": family, **kw}


def _sorted_labels(labels):
    return {k: sorted(v) for k, v in labels.items()}


# ---------------------------------------------------------------- hand-computed theories

def test_exactly_one_closure_and_feasibility():
    th = _theory(["base", "alt"], "abc", [(_card("exactly", 1, _var("a"), _var("b"), _var("c")), None)])
    binds = [_bound("a"), _bound("b"), _bound("c")]
    ck = _checker(th, binds)
    labels, survivors = ck.closure(_q("closure", hyp=[_var("a")]))
    assert survivors == [0, 1]
    assert _sorted_labels(labels) == {"ENTAILED": ["a"], "REFUTED": ["b", "c"], "CONTINGENT": [],
                                      "UNRESOLVED": []}
    labels, _ = ck.closure(_q("closure", hyp=[]))
    assert _sorted_labels(labels)["CONTINGENT"] == ["a", "b", "c"]

    ours, mism = ab._check_feasibility(ck, _q("feasibility", assume=[_var("a"), _var("b")]),
                                       {"decision": "UNSAT", "witness": None, "core": [0, 1], "missing": []})
    assert ours["decision"] == "UNSAT" and mism == []
    # a single assumption is not a core: a alone is satisfiable
    _, mism = ab._check_feasibility(ck, _q("feasibility", assume=[_var("a"), _var("b")]),
                                    {"decision": "UNSAT", "witness": None, "core": [0], "missing": []})
    assert mism

    q = _q("feasibility", assume=[_var("a")])
    _, mism = ab._check_feasibility(ck, q, {"decision": "SAT", "witness": {"a": True}, "core": None, "missing": []})
    assert mism == []
    # b is outside the support of the query: the witness is wrong
    _, mism = ab._check_feasibility(ck, q, {"decision": "SAT", "witness": {"a": True, "b": True}, "core": None,
                                            "missing": []})
    assert any("outside the query support" in m for m in mism)
    _, mism = ab._check_feasibility(ck, q, {"decision": "UNSAT", "witness": None, "core": [0], "missing": []})
    assert mism


def test_sequential_counter_and_inconsistent_hypothesis():
    # exactly two of a, b, c: atleast 2 and atmost 2 both go through the sequential counter
    th = _theory(["base"], "abc", [(_card("atleast", 2, _var("a"), _var("b"), _var("c")), None),
                                   (_card("atmost", 2, _var("a"), _var("b"), _var("c")), None)])
    binds = [_bound("a"), _bound("b"), _bound("c")]
    ck = _checker(th, binds)
    labels, survivors = ck.closure(_q("closure", hyp=[_not(_var("a"))]))
    assert survivors == [0]
    assert _sorted_labels(labels) == {"ENTAILED": ["b", "c"], "REFUTED": ["a"], "CONTINGENT": [],
                                      "UNRESOLVED": []}
    hyp = [_not(_var("a")), _not(_var("b"))]
    labels, survivors = ck.closure(_q("closure", hyp=hyp))
    assert labels is None and survivors == []
    _, mism = ab._check_closure(ck, _q("closure", hyp=hyp), {"labels": None, "error": "hyp_inconsistent"})
    assert mism == []
    _, mism = ab._check_closure(ck, _q("closure", hyp=hyp), {"labels": {"CONTINGENT": ["a", "b", "c"]}})
    assert mism


def test_selection_relations_and_decomposed_components():
    # atmost one of a, b, c; e is forced true; d is a free outcome in its own component
    th = _theory(["base"], "abcde", [(_card("atmost", 1, _var("a"), _var("b"), _var("c")), None),
                                     (_var("e"), None)])
    binds = [_bound(x) for x in "abcde"]
    ck = _checker(th, binds)

    def run(rel, target):
        return ck.selection(_q("selection", relation=rel, target=target))

    # b and c exclude a; d and e (free, or forced true) do not
    assert run("excludes", _var("a")) == ["b", "c"]
    # everything implies the forced-true target; only forced-true outcomes are implied by a free target
    assert run("implies", _var("e")) == ["a", "b", "c", "d", "e"]
    assert run("implied_by", _var("e")) == ["e"]
    assert run("excludes", _var("e")) == []
    assert run("equiv", _var("a")) == ["a"]


def test_scoped_interpretations_change_labels_and_selection():
    # a implies b holds only in the base interpretation
    th = _theory(["base", "alt"], "ab", [(_implies(_var("a"), _var("b")), ["base"])])
    binds = [_bound("a"), _bound("b")]
    ck = _checker(th, binds)
    labels, survivors = ck.closure(_q("closure", hyp=[_var("a")]))
    assert survivors == [0, 1]
    assert _sorted_labels(labels) == {"ENTAILED": ["a"], "REFUTED": [], "CONTINGENT": [],
                                      "UNRESOLVED": ["b"]}
    # alt does not entail "a implies b", so b is not implied by a in every interpretation
    assert ck.selection(_q("selection", relation="implied_by", target=_var("a"))) == ["a"]
    unscoped = _checker(_theory(["base", "alt"], "ab", [(_implies(_var("a"), _var("b")), None)]), binds)
    assert unscoped.selection(_q("selection", relation="implied_by", target=_var("a"))) == ["a", "b"]
    # base is UNSAT for a and not b, alt is SAT: UNRESOLVED, with no unresolved codes in the support
    ours, mism = ab._check_feasibility(ck, _q("feasibility", assume=[_var("a"), _not(_var("b"))]),
                                       {"decision": "UNRESOLVED", "witness": None, "core": None, "missing": []})
    assert ours["per_interpretation"] == [False, True] and mism == []


def test_unresolved_atoms_keep_identity_and_missing_codes():
    # u is unresolved and u implies a in base only
    th = _theory(["base", "alt"], "a", [(_implies(_var("u"), _var("a")), ["base"])])
    binds = [_bound("a"), _unresolved("u")]
    ck = _checker(th, binds)
    # u is forced by the hypothesis, so it is ENTAILED although unresolved; a follows only in base (UNRESOLVED)
    labels, _ = ck.closure(_q("closure", hyp=[_var("u")]))
    assert _sorted_labels(labels)["ENTAILED"] == ["u"] and _sorted_labels(labels)["UNRESOLVED"] == ["a"]
    labels, _ = ck.closure(_q("closure", hyp=[]))
    assert _sorted_labels(labels) == {"ENTAILED": [], "REFUTED": [], "CONTINGENT": ["a"],
                                      "UNRESOLVED": ["u"]}
    # asserting u and not u is inconsistent by identity
    ours, mism = ab._check_feasibility(ck, _q("feasibility", assume=[_var("u"), _not(_var("u"))]),
                                       {"decision": "UNSAT", "witness": None, "core": [0, 1], "missing": []})
    assert ours["decision"] == "UNSAT" and mism == []
    # u and not a is SAT in alt only: UNRESOLVED, and the codes of the unresolved support must be reported
    q = _q("feasibility", assume=[_var("u"), _not(_var("a"))])
    ours, mism = ab._check_feasibility(ck, q, {"decision": "UNRESOLVED", "witness": None, "core": None,
                                               "missing": ["no_date"]})
    assert ours["decision"] == "UNRESOLVED" and mism == []
    _, mism = ab._check_feasibility(ck, q, {"decision": "UNRESOLVED", "witness": None, "core": None, "missing": []})
    assert mism


def test_partition_claims_and_counterexample_certificates():
    # no constraints: a or b is not exhaustive, and (a, b) = (false, false) is a counterexample
    ck = _checker(_theory(["base"], "ab", []), [_bound("a"), _bound("b")])
    q = _q("partition", members=[_var("a"), _var("b")], claim="exhaustive", target=None)
    ours, mism = ab._check_partition(ck, q, {"decision": "false", "counterexample": {"a": False, "b": False},
                                             "missing": []})
    assert ours["decision"] == "false" and mism == []
    _, mism = ab._check_partition(ck, q, {"decision": "false", "counterexample": {"a": True}, "missing": []})
    assert mism  # a true member is never a counterexample to "a or b covers everything"

    # c <-> (a or b) and a, b exclusive: exclusive, exhaustive into c and each member inside c: a true partition
    th = _theory(["base", "alt"], "abc", [(_card("atmost", 1, _var("a"), _var("b")), None),
                                          (_iff(_var("c"), _or(_var("a"), _var("b"))), None)])
    ck = _checker(th, [_bound("a"), _bound("b"), _bound("c")])
    q = _q("partition", members=[_var("a"), _var("b")], claim="partition", target=_var("c"))
    ours, mism = ab._check_partition(ck, q, {"decision": "true", "counterexample": None, "missing": []})
    assert ours["decision"] == "true" and mism == []
    # exclusive is entailed by atmost one in both interpretations: true, and a wrong false is caught
    q = _q("partition", members=[_var("a"), _var("b")], claim="exclusive", target=None)
    ours, mism = ab._check_partition(ck, q, {"decision": "true", "counterexample": None, "missing": []})
    assert ours["fails_per_interpretation"] == [False, False] and mism == []
    _, mism = ab._check_partition(ck, q, {"decision": "false", "counterexample": {"a": True, "b": True},
                                          "missing": []})
    assert mism


def test_wsum_is_exhaustive():
    expr = {"op": "wsum", "cmp": "le", "k": 3, "terms": [{"w": 2, "e": _var("x")}, {"w": 3, "e": _var("y")}]}
    ck = _checker(_theory(["base"], "", [(expr, None)]), [])
    assert ck.satisfiable(ck.support_roots([_var("x")]), [_var("y")]) == [True]
    assert ck.satisfiable(ck.support_roots([_var("x")]), [_var("x"), _var("y")]) == [False]


# ---------------------------------------------------------------- solver against exhaustive enumeration

def _rand_expr(rng, names, depth):
    if depth == 0 or rng.random() < 0.3:
        v = _var(rng.choice(names))
        return _not(v) if rng.random() < 0.4 else v
    op = rng.choice(["and", "or", "not", "implies", "iff", "atmost", "atleast", "exactly"])
    if op == "not":
        return _not(_rand_expr(rng, names, depth - 1))
    if op in ("implies", "iff"):
        return {"op": op, "a": _rand_expr(rng, names, depth - 1), "b": _rand_expr(rng, names, depth - 1)}
    args = [_rand_expr(rng, names, depth - 1) for _ in range(rng.randint(1, 4))]
    if op in ("and", "or"):
        return {"op": op, "args": args}
    return {"op": op, "k": rng.randint(0, len(args)), "args": args}


def test_dpll_matches_exhaustive_enumeration_on_random_theories():
    rng = random.Random(20261018)
    for _ in range(150):
        names = [f"x{k}" for k in range(rng.randint(3, 7))]
        cons = [(_rand_expr(rng, names, 3), None) for _ in range(rng.randint(1, 5))]
        th = ab.Theory(_theory(["base"], "", cons), [], [])
        ck = ab.Checker(th, budget=10**6)
        g = ck.group(frozenset(th.component_roots))
        brute, dpll = ab._Brute(th, g, 0), ab._Dpll(th, g, 0, 10**6)
        for _ in range(4):
            assume = [_rand_expr(rng, list(g.vars), 2) for _ in range(rng.randint(1, 3))] if g.vars else []
            mb, md = brute.solve(assume), dpll.solve(assume)
            assert (mb is None) == (md is None), (cons, assume)
            if md is not None:
                # the solver's model is a world of the exhaustive semantics
                assert brute.solve([ab._lit(v, val) for v, val in md.items()]) is not None
        forced_b = ab._probe(brute, [], list(brute.vars))
        forced_d = ab._probe(dpll, [], list(dpll.vars))
        assert forced_b == forced_d, cons


# ---------------------------------------------------------------- cross-check against mb-engine

def _engine_available():
    exe = os.environ.get("MB_ENGINE")
    return bool(exe) and Path(exe).is_file()


def _eval_world(e, world):
    """Truth of a ref-free expression in one world (test-side evaluator, used only to plant consistent theories)."""
    op = e["op"]
    if op == "var":
        return world[e["id"]]
    if op == "not":
        return not _eval_world(e["a"], world)
    if op == "and":
        return all(_eval_world(x, world) for x in e["args"])
    if op == "or":
        return any(_eval_world(x, world) for x in e["args"])
    if op == "implies":
        return (not _eval_world(e["a"], world)) or _eval_world(e["b"], world)
    if op == "iff":
        return _eval_world(e["a"], world) == _eval_world(e["b"], world)
    count = sum(_eval_world(x, world) for x in e["args"])
    return count <= e["k"] if op == "atmost" else count >= e["k"] if op == "atleast" else count == e["k"]


def _random_submission(rng, directory: Path):
    """A small random submission and queries in the wire format of docs/ir.md; returns (theory, binds, queries).

    Two representative outcomes carry class aliases (var of the representative). Other bound outcomes are bound to
    one of them, possibly negated, so the binding equivalences are satisfiable and the planted world respects them.
    """
    n = rng.randint(3, 6)
    ids = [f"x{k}" for k in range(n)]
    reps = ids[:2]
    aliases = {f"K_{r}": _var(r) for r in reps}
    binds, world = [], {}
    for oid in ids:
        ev = [f"src:{oid}:question"]
        if oid in reps:
            world[oid] = rng.random() < 0.5
            binds.append({"id": oid, "status": "bound", "class": f"K_{oid}", "negated": False, "missing": [],
                          "evidence": ev})
        elif rng.random() < 0.8:
            rep, neg = rng.choice(reps), rng.random() < 0.3
            world[oid] = world.setdefault(rep, rng.random() < 0.5) != neg
            binds.append({"id": oid, "status": "bound", "class": f"K_{rep}", "negated": neg, "missing": [],
                          "evidence": ev})
        else:
            world[oid] = rng.random() < 0.5
            binds.append({"id": oid, "status": "unresolved", "class": None, "negated": False,
                          "missing": ["no_date"], "evidence": ev})
    # most theories are planted: a constraint is kept only when the planted world satisfies it
    cons = []
    while len(cons) < rng.randint(1, 4):
        expr = _rand_expr(rng, ids, 2)
        if rng.random() < 0.8 and not _eval_world(expr, world):
            continue
        scope = None if rng.random() < 0.7 else (["base"] if rng.random() < 0.5 else ["alt"])
        cons.append({"id": f"c{len(cons):07d}", "expr": expr, "interps": scope,
                     "provenance": [f"src:{oid}:question" for oid in ids]})
    theory = {"interpretations": ["base", "alt"], "aliases": aliases, "constraints": cons}
    queries = []
    for k in range(6):
        fam = rng.choice(["closure", "selection", "feasibility", "partition"])
        qid = f"q{k}"
        if fam == "closure":
            queries.append({"qid": qid, "family": fam,
                            "hyp": [_rand_expr(rng, ids, 1) for _ in range(rng.randint(1, 2))]})
        elif fam == "selection":
            queries.append({"qid": qid, "family": fam,
                            "relation": rng.choice(["equiv", "implies", "implied_by", "excludes"]),
                            "target": _rand_expr(rng, ids, 1)})
        elif fam == "feasibility":
            queries.append({"qid": qid, "family": fam,
                            "assume": [_rand_expr(rng, ids, 1) for _ in range(rng.randint(1, 3))]})
        else:
            target = None if rng.random() < 0.5 else _rand_expr(rng, ids, 1)
            queries.append({"qid": qid, "family": fam,
                            "members": [_rand_expr(rng, ids, 1) for _ in range(rng.randint(2, 3))],
                            "claim": rng.choice(["exclusive", "exhaustive", "partition"]), "target": target})
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "bindings.jsonl").open("w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(b) + "\n" for b in binds)
    (directory / "theory.json").write_text(json.dumps(theory), encoding="utf-8")
    with (directory / "provenance.jsonl").open("w", encoding="utf-8") as fh:
        for oid in ids:
            fh.write(json.dumps({"ref": f"src:{oid}:question", "kind": "source", "record": oid,
                                 "field": "question"}) + "\n")
    (directory / "profile.json").write_text(json.dumps({
        "max_expr_nodes": 100000, "max_bytes": 10000000, "max_interpretations": 8, "max_weight_bits": 32,
        "query_timeout_s": 60.0, "build_deadline_s": 600.0, "spend_ceiling_usd": 1.0}), encoding="utf-8")
    return theory, binds, queries


@pytest.mark.skipif(not _engine_available(), reason="mb-engine not available: set MB_ENGINE to a copy of the binary")
def test_random_theories_agree_with_mb_engine(tmp_path):
    from mentat_bench import engine

    rng = random.Random(7)
    checked = refused = 0
    for case in range(40):
        sub = tmp_path / f"case{case}"
        theory, binds, queries = _random_submission(rng, sub)
        queries_path = sub / "queries.jsonl"
        with queries_path.open("w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(q) + "\n" for q in queries)
        answers_path = tmp_path / f"answers{case}.jsonl"  # the engine refuses outputs that name an input
        ck = ab.Checker(ab.Theory(theory, binds, queries), budget=10**6)
        try:
            engine.validate(sub)
            engine.eval_submission(sub, queries_path, answers_path)
        except engine.EngineError:
            # the engine refuses an inconsistent base; so must the checker
            with pytest.raises(ab.BaseInconsistent):
                ck.check_base()
            refused += 1
            continue
        ck.check_base()
        answers = {a["qid"]: a for a in map(json.loads, answers_path.read_text(encoding="utf-8").splitlines())}
        for q in queries:
            _, mism = ab._HANDLERS[q["family"]](ck, q, answers[q["qid"]])
            assert mism == [], (case, q, answers[q["qid"]], mism)
            checked += 1
    assert checked >= 100 and refused <= 10, (checked, refused)


# ---------------------------------------------------------------- committed fixtures (engine-free)

@pytest.mark.slow
def test_committed_toy10k_gold_is_independently_verified(tmp_path):
    path = REPO / "fixtures" / "toy10k"
    out = tmp_path / "report.json"
    code = ab.main(["--fixture", str(path), "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0, report["summary"]
    s = report["summary"]
    assert s["mismatch"] == 0 and s["skipped"] == 0 and s["sampled"] == 0
    assert s["verified_exhaustive"] + s["verified_independent_solver"] == 40
    assert report["base"] == "consistent"


# ---------------------------------------------------------------- witness and counterexample maximality

def test_witness_maximality_single_interpretation():
    ck = _checker(_theory(["base"], "ab", []), [_bound("a"), _bound("b")])
    q = _q("feasibility", assume=[_or(_var("a"), _var("b"))])
    # {a}: b can be fixed to either value, so the witness is not maximal
    _, mism = ab._check_feasibility(ck, q, {"decision": "SAT", "witness": {"a": True}, "core": None, "missing": []})
    assert any("not maximal: b=True" in m for m in mism) and any("not maximal: b=False" in m for m in mism)
    # {}: a can be fixed, so not maximal
    _, mism = ab._check_feasibility(ck, q, {"decision": "SAT", "witness": {}, "core": None, "missing": []})
    assert mism
    # total over the support: maximal, and the check ran
    ours, mism = ab._check_feasibility(ck, q, {"decision": "SAT", "witness": {"a": True, "b": False}, "core": None,
                                               "missing": []})
    assert mism == [] and ours["maximality"] == "checked"


def test_witness_maximality_blocked_by_interpretations():
    # base forces b, alt forbids b. With a true, b=True is blocked in alt and b=False is blocked in base, so
    # {a: True} is maximal although b is not fixed; {} is not, because a=True extends in every interpretation.
    th = _theory(["base", "alt"], "ab", [(_var("b"), ["base"]), (_not(_var("b")), ["alt"])])
    ck = _checker(th, [_bound("a"), _bound("b")])
    q = _q("feasibility", assume=[_or(_var("a"), _var("b"))])
    ours, mism = ab._check_feasibility(ck, q, {"decision": "SAT", "witness": {"a": True}, "core": None, "missing": []})
    assert mism == [] and ours["maximality"] == "checked"
    _, mism = ab._check_feasibility(ck, q, {"decision": "SAT", "witness": {}, "core": None, "missing": []})
    assert any("a=True extends" in m for m in mism)


def test_counterexample_maximality():
    ck = _checker(_theory(["base"], "ab", []), [_bound("a"), _bound("b")])
    q = _q("partition", members=[_var("a"), _var("b")], claim="exhaustive", target=None)
    ours, mism = ab._check_partition(ck, q, {"decision": "false", "counterexample": {"a": False, "b": False},
                                             "missing": []})
    assert mism == [] and ours["maximality"] == "checked"
    _, mism = ab._check_partition(ck, q, {"decision": "false", "counterexample": {"a": False}, "missing": []})
    assert any("b=False extends" in m for m in mism)


def test_maximality_budget_overrun_is_never_verified():
    assert ab._status([], set(), True) == "verified_except_maximality"
    assert ab._status(["witness not maximal: x=True"], set(), True) == "mismatch"
    assert ab._status([], {"solver"}, False) == "verified_independent_solver"


# ---------------------------------------------------------------- driver: files, timeouts, evidence scope

def _write_fixture(root, theory, bindings, provenance, files):
    """files: {queries file name: (queries, answers file name, answers)}; writes a fixture directory."""
    gold = root / "gold"
    gold.mkdir(parents=True)
    (gold / "theory.json").write_text(json.dumps(theory), encoding="utf-8")
    (gold / "bindings.jsonl").write_text("".join(json.dumps(b) + "\n" for b in bindings), encoding="utf-8")
    (gold / "provenance.jsonl").write_text("".join(
        json.dumps({"ref": r, "kind": "axiom" if r.startswith("ax:") else "source", "record": r, "field": "rule"})
        + "\n" for r in provenance), encoding="utf-8")
    for qname, (queries, aname, answers) in files.items():
        (root / qname).write_text("".join(json.dumps(q) + "\n" for q in queries), encoding="utf-8")
        (gold / aname).write_text("".join(json.dumps(a) + "\n" for a in answers), encoding="utf-8")
    return root


def test_timeout_is_reported_and_extra_query_file_is_counted(tmp_path):
    th = _theory(["base"], "ab", [])
    binds = [_bound("a"), _bound("b")]
    queries = [_q("feasibility", "f1", assume=[_var("a")]),
               _q("selection", "s1", relation="implies", target=_var("a"))]
    answers = [{"qid": "f1", "decision": "SAT", "witness": {"a": True}, "core": None, "missing": []},
               {"qid": "s1", "ids": ["a"]}]
    extra = [_q("feasibility", "x1", assume=[_var("b")])]
    extra_answers = [{"qid": "x1", "decision": "SAT", "witness": {"b": True}, "core": None, "missing": []}]
    fx = _write_fixture(tmp_path / "fx", th, binds, ["ref:unused"],
                        {"queries.jsonl": (queries, "answers.jsonl", answers),
                         "queries_extra.jsonl": (extra, "answers_extra.jsonl", extra_answers)})
    # an expired budget: every query that reaches the solver is reported as a timeout, never as verified
    report = ab.audit_fixture(fx, query_seconds=-1.0)
    assert report["query_files"] == {"queries.jsonl": 2, "queries_extra.jsonl": 1}
    assert all(r["status"].startswith("skipped(timeout") for r in report["results"])
    assert report["files"]["queries_extra.jsonl"]["summary"]["skipped"] == 1
    report = ab.audit_fixture(fx, query_seconds=120.0)
    assert report["summary"]["verified_exhaustive"] == 3 and report["summary"]["mismatch"] == 0


def test_scope_reach_rule_hand_computed():
    # a in A; b in B (forced true); c unresolved in C; d bound to b but evidenced by A.
    theory = {"interpretations": ["base"], "aliases": {"K_a": _var("a"), "K_b": _var("b")},
              "constraints": [
                  {"id": "k1", "expr": _implies(_var("a"), _var("b")), "interps": None,
                   "provenance": ["src:A:q", "src:B:q", "ax:R"]},
                  {"id": "k2", "expr": _implies(_var("c"), _var("a")), "interps": None, "provenance": ["src:C:q"]},
                  {"id": "k3", "expr": _var("b"), "interps": None, "provenance": ["src:B:q"]}]}
    binds = [{"id": "a", "status": "bound", "class": "K_a", "negated": False, "missing": [], "evidence": ["src:A:q"]},
             {"id": "b", "status": "bound", "class": "K_b", "negated": False, "missing": [], "evidence": ["src:B:q"]},
             {"id": "c", "status": "unresolved", "class": None, "negated": False, "missing": ["no_date"],
              "evidence": ["src:C:q"]},
             {"id": "d", "status": "bound", "class": "K_b", "negated": False, "missing": [], "evidence": ["src:A:q"]}]
    full = ab.Theory(theory, binds, [])
    # scope {A, R}: k1 under-cites B, k2 and k3 reach outside, so no constraint survives; d reaches b, so it demotes
    sub, sub_b, in_scope = ab._scope(full, theory, binds, {"src:A:q", "ax:R"})
    assert in_scope == {"a", "d"} and sub["constraints"] == []
    d = next(b for b in sub_b if b["id"] == "d")
    assert d["status"] == "unresolved" and d["class"] is None and d["missing"] == ["out_of_scope"]
    # scope {A, B, C, R}: everything survives
    sub, sub_b, in_scope = ab._scope(full, theory, binds, {"src:A:q", "src:B:q", "src:C:q", "ax:R"})
    assert in_scope == {"a", "b", "c", "d"} and [c["id"] for c in sub["constraints"]] == ["k1", "k2", "k3"]


def _scope_fixture(root):
    theory = {"interpretations": ["base"], "aliases": {"K_a": _var("a"), "K_b": _var("b")},
              "constraints": [
                  {"id": "k1", "expr": _implies(_var("a"), _var("b")), "interps": None,
                   "provenance": ["src:A:q", "src:B:q", "ax:R"]},
                  {"id": "k2", "expr": _implies(_var("c"), _var("a")), "interps": None, "provenance": ["src:C:q"]},
                  {"id": "k3", "expr": _var("b"), "interps": None, "provenance": ["src:B:q"]}]}
    binds = [{"id": "a", "status": "bound", "class": "K_a", "negated": False, "missing": [], "evidence": ["src:A:q"]},
             {"id": "b", "status": "bound", "class": "K_b", "negated": False, "missing": [], "evidence": ["src:B:q"]},
             {"id": "c", "status": "unresolved", "class": None, "negated": False, "missing": ["no_date"],
              "evidence": ["src:C:q"]},
             {"id": "d", "status": "bound", "class": "K_b", "negated": False, "missing": [], "evidence": ["src:A:q"]}]
    narrow = ["src:A:q", "ax:R"]
    wide = ["src:A:q", "src:B:q", "src:C:q", "ax:R"]
    tautology = [_var("b"), _not(_var("b"))]
    queries = [
        {"qid": "s1", "family": "closure", "hyp": [], "evidence": narrow},
        {"qid": "s2", "family": "feasibility", "assume": [_var("b")], "evidence": narrow},
        {"qid": "s3", "family": "closure", "hyp": [], "evidence": wide},
        {"qid": "s4", "family": "partition", "members": tautology, "claim": "exhaustive", "target": None,
         "evidence": wide},
        {"qid": "s5", "family": "partition", "members": tautology, "claim": "exhaustive", "target": None,
         "evidence": wide},
    ]
    answers = [
        {"qid": "s1", "labels": {"ENTAILED": [], "REFUTED": [], "CONTINGENT": ["a"], "UNRESOLVED": ["d"]}},
        {"qid": "s2", "error": "out_of_scope"},
        {"qid": "s3", "labels": {"ENTAILED": ["b", "d"], "REFUTED": [], "CONTINGENT": ["a"], "UNRESOLVED": ["c"]}},
        # the certificate {B} suffices for the tautology (b is in scope under B alone)
        {"qid": "s4", "decision": "true", "counterexample": None, "missing": [], "evidence": ["src:B:q"]},
        # the certificate {A} cannot cover b, so it does not establish the claim
        {"qid": "s5", "decision": "true", "counterexample": None, "missing": [], "evidence": ["src:A:q"]},
    ]
    extra = [{"qid": "u1", "family": "closure", "hyp": []}]
    extra_answers = [{"qid": "u1", "labels": {"ENTAILED": ["b", "d"], "REFUTED": [], "CONTINGENT": ["a"],
                                              "UNRESOLVED": ["c"]}}]
    return _write_fixture(root, theory, binds, ["src:A:q", "src:B:q", "src:C:q", "ax:R"],
                          {"queries.jsonl": (queries, "answers.jsonl", answers),
                           "queries_extra.jsonl": (extra, "answers_extra.jsonl", extra_answers)})


def test_evidence_scope_fixture_end_to_end(tmp_path):
    report = ab.audit_fixture(_scope_fixture(tmp_path / "scope"), query_seconds=120.0)
    status = {r["qid"]: r["status"] for r in report["results"]}
    assert status["s1"].startswith("verified") and status["s2"].startswith("verified")
    assert status["s3"].startswith("verified") and status["s4"].startswith("verified")
    assert status["s5"] == "mismatch"
    bad = next(r for r in report["results"] if r["qid"] == "s5")
    assert any("certificate scope does not cover" in d for d in bad["details"])
    assert status["u1"].startswith("verified")
    assert report["query_files"] == {"queries.jsonl": 5, "queries_extra.jsonl": 1}

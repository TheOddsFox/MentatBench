"""Tests for python/mentat_bench/hard_suite.py: exact labels on hand-made Mentat-Hard theories (contract shape of
gold/meta.jsonl), the world.json cross-check on a generated split, orientation, base/alt, determinism, the mix, the
contract schema and the audit_brute cross-check. The engine-dependent tests are marked needs_engine (MB_ENGINE)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from mentat_bench import audit_brute as ab, engine, hard_suite as hs, ir

needs_engine = pytest.mark.skipif(not engine.engine_available(), reason="mb-engine binary not built")


def _ref(c):
    return {"op": "ref", "name": c}


def _not(e):
    return {"op": "not", "a": e}


def _imp(a, b):
    return {"op": "implies", "a": a, "b": b}


def _card(op, k, classes):
    return {"op": op, "k": k, "args": [_ref(c) for c in classes]}


class _World:
    """Accumulates markets (Yes/No outcomes), meta rows and constraints for a fixture."""

    def __init__(self):
        self.corpus, self.binds, self.meta, self.cons = [], [], [], []

    def market(self, lg, mid, cls, text, kind, team=None, t=None, subgroup=None, family=1, fail=False,
               paraphrase_of=None, missing=None):
        for idx, label in ((1, "Yes"), (2, "No")):
            oid = f"hard:{lg}{mid}:{idx}"
            self.corpus.append({"id": oid, "text": text, "label": label, "event_title": f"{lg} season",
                                "controlled": True})
            negated = (idx == 2) != fail
            if missing:
                self.binds.append({"id": oid, "status": "unresolved", "class": None, "negated": False,
                                   "missing": list(missing)})
            else:
                self.binds.append({"id": oid, "status": "bound", "class": cls, "negated": negated, "missing": []})
            self.meta.append({"id": oid, "market": f"{lg}{mid}", "league": lg, "kind": kind, "team": team, "t": t,
                              "subgroup": subgroup, "class": cls, "family": family, "test_only": family > 20,
                              "paraphrase_of": paraphrase_of})
        return f"hard:{lg}{mid}:1", f"hard:{lg}{mid}:2"

    def add(self, expr, interps=None):
        self.cons.append({"id": f"g{len(self.cons):07d}", "expr": expr, "interps": interps})

    def write(self, root: Path, interps=("base", "alt"), seed=7):
        gold = root / "gold"
        ir.write_submission(gold, self.binds, self.cons, list(interps), {"calls": [], "wall_seconds": 0, "peak_rss_mb": 0})
        ir.write_jsonl(root / "corpus.jsonl", self.corpus)
        ir.write_jsonl(gold / "meta.jsonl", self.meta)
        ir.write_json(root / "fixture.json", {"name": root.name, "split": "test", "seed": seed})
        return root


def _league(w: _World, lg: str, teams=("Ash", "Birch", "Cedar", "Dune"), fam=lambda n: 1, paraphrases=1):
    """One league: ladders, exactly-one win and runner-up with a placement rule, exactly-2 qualify, a comparison,
    a win -> threshold chain, at-most-2 at the top, two overlapping exactly-one subgroups, paraphrases and two
    base/alt ambiguous propositions, and one proposition that is impossible in alt only. Returns {name: (yes_id, no_id)}."""
    o, n = {}, iter(range(10_000))

    def mk(name, cls, text, kind, **kw):
        o[name] = w.market(lg, f"m{next(n)}", cls, text, kind, family=fam(len(o)), **kw)

    A, B, C, D = teams
    for x in teams:
        for t in (1, 2, 3):
            mk(f"thr{x}{t}", f"{lg}_thr_{x}_{t}", f"Will {x} score at least {t} points in the {lg} season?",
               "thr", team=x, t=t)
        mk(f"win{x}", f"{lg}_win_{x}", f"Will {x} win the {lg} league?", "win", team=x)
        mk(f"run{x}", f"{lg}_run_{x}", f"Will {x} finish runner-up in the {lg} league?", "runner", team=x)
        mk(f"qual{x}", f"{lg}_qual_{x}", f"Will {x} qualify from the {lg} league?", "qual", team=x)
    for sg, members in (("north", (A, B, C)), ("coast", (C, D))):
        for x in members:
            mk(f"seed{sg}{x}", f"{lg}_seed_{sg}_{x}", f"Will {x} finish first among the {sg} sides?", "seed",
               team=x, subgroup=sg)
    para = [(f"thr{C}2", "thr", C, 2, f"Will {C} score more than 1 points in the {lg} season?"),
            (f"win{B}", "win", B, None, f"Will {B} take the {lg} title?"),
            (f"thr{D}1", "thr", D, 1, f"Will {D} score 1 or more points in the {lg} season?")][:paraphrases]
    for base, kind, x, t, text in para:
        cls = f"{lg}_{kind}_{x}" + (f"_{t}" if t else "")
        mk(base + "p", cls, text, kind, team=x, t=t, paraphrase_of=o[base][0].split(":")[1])
    # the break propositions state threshold 1 (their base reading is thr(C, 1), as in the generator's meta)
    mk("u1", None, f"Will {C} score by the {lg} break?", "dated", team=C, t=1, missing=["no_date"])
    mk("u2", None, f"Will {C} be on the board by the {lg} break?", "dated", team=C, t=1, missing=["no_date"])
    mk("u3", None, f"Will {C} have scored by the {lg} deadline?", "dated", team=C, missing=["no_date"])
    cls = lambda name: _ref(f"{lg}_{name}")  # noqa: E731
    for x in teams:
        for t in (1, 2):
            w.add(_imp(cls(f"thr_{x}_{t + 1}"), cls(f"thr_{x}_{t}")))
        w.add(_imp(cls(f"win_{x}"), _not(cls(f"run_{x}"))))
        w.add(_imp(cls(f"win_{x}"), cls(f"thr_{x}_2")))  # chain: win -> score >= 2 -> score >= 1
        w.add(_imp(cls(f"thr_{x}_3"), cls(f"qual_{x}")))
    w.add(_card("exactly", 1, [f"{lg}_win_{x}" for x in teams]))
    w.add(_card("exactly", 1, [f"{lg}_run_{x}" for x in teams]))
    w.add(_imp(cls(f"win_{A}"), cls(f"run_{B}")))  # placement
    w.add(_card("exactly", 2, [f"{lg}_qual_{x}" for x in teams]))
    w.add(_card("atmost", 2, [f"{lg}_thr_{x}_3" for x in teams]))
    for t in (1, 2):  # A outscores B (no T(A,1) axiom here, so nothing is constant)
        w.add(_imp(cls(f"thr_{B}_{t}"), cls(f"thr_{A}_{t + 1}")))
    for sg, members in (("north", (A, B, C)), ("coast", (C, D))):
        w.add(_card("exactly", 1, [f"{lg}_seed_{sg}_{x}" for x in members]))
    for u in ("u1", "u2", "u3"):
        y, no = o[u]
        w.add({"op": "iff", "a": ir.var(y), "b": _not(ir.var(no))})
    w.add({"op": "iff", "a": ir.var(o["u1"][0]), "b": cls(f"thr_{C}_1")}, ["base"])
    w.add({"op": "iff", "a": ir.var(o["u1"][0]), "b": cls(f"thr_{C}_2")}, ["alt"])
    w.add({"op": "iff", "a": ir.var(o["u2"][0]), "b": cls(f"thr_{C}_1")}, ["base"])
    w.add({"op": "iff", "a": ir.var(o["u2"][0]), "b": cls(f"thr_{D}_1")}, ["alt"])
    w.add({"op": "iff", "a": ir.var(o["u3"][0]), "b": cls(f"thr_{C}_1")}, ["base"])
    w.add(_not(ir.var(o["u3"][0])), ["alt"])  # impossible in alt only: not a constant
    return o


def _small(root):
    w = _World()
    one = _league(w, "L1")
    two = _league(w, "L2", teams=("Ash", "Birch", "Cedar", "Elm"))
    fx = hs.Fixture(w.write(root))
    return fx, hs._Engine(fx), one, two


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    return _small(tmp_path_factory.mktemp("small") / "fx")


Y, N = 0, 1


def _relation_cases(o, two):
    return {
        (o["thrAsh3"][Y], o["thrAsh2"][Y]): "IMPLIES",  # ladder
        (o["thrAsh2"][Y], o["thrAsh3"][Y]): "IMPLIED_BY",  # orientation
        (o["thrAsh2"][N], o["thrAsh3"][N]): "IMPLIES",  # contrapositive on the No side
        (o["winAsh"][Y], o["winBirch"][Y]): "EXCLUDES",  # exactly-one
        (o["winAsh"][Y], o["runBirch"][Y]): "IMPLIES",  # placement
        (o["winAsh"][Y], o["runAsh"][Y]): "EXCLUDES",
        (o["thrBirch2"][Y], o["thrAsh3"][Y]): "IMPLIES",  # comparison
        (o["thrAsh2"][Y], o["thrBirch1"][Y]): "IMPLIED_BY",  # the comparison read backwards
        (o["thrAsh3"][Y], o["thrBirch1"][Y]): "NONE",  # near-miss threshold under the comparison
        (o["winCedar"][Y], o["thrCedar1"][Y]): "IMPLIES",  # multi-hop chain
        (o["thrCedar2"][Y], o["thrCedar2p"][Y]): "EQUIV",  # paraphrase
        (o["thrCedar2"][N], o["thrCedar2p"][Y]): "EXCLUDES",
        (o["thrCedar2"][Y], o["thrDune2"][Y]): "NONE",  # same threshold, other team
        (o["seednorthCedar"][Y], o["seednorthAsh"][Y]): "EXCLUDES",  # overlapping subgroups
        (o["seednorthCedar"][Y], o["seedcoastCedar"][Y]): "NONE",
        (o["winAsh"][Y], two["winAsh"][Y]): "NONE",  # same team name in another league
        (o["u1"][Y], o["thrCedar1"][Y]): "IMPLIES",  # EQUIV in base, IMPLIES in alt: only IMPLIES holds in both
        (o["u1"][Y], o["thrCedar2"][Y]): "IMPLIED_BY",
        (o["u2"][Y], o["thrCedar1"][Y]): "NONE",  # EQUIV in base only
        (o["u1"][Y], o["u2"][Y]): "NONE",
        (o["u1"][Y], o["u1"][N]): "EXCLUDES",  # same market: complementary in every interpretation
        (o["u3"][Y], o["thrCedar1"][Y]): "IMPLIES",  # EQUIV in base; u3 is impossible in alt, so only u3 => holds
        (o["u3"][Y], o["thrCedar1"][N]): "EXCLUDES",
        (o["u3"][Y], o["thrCedar2"][Y]): "NONE",
    }


@needs_engine
def test_relation_labels_exact(small):
    fx, eng, o, two = small
    pairs = _relation_cases(o, two)
    assert not {*o["u3"], *o["u1"]} & hs.constants(fx, eng)
    got = hs.label_pairs(fx, eng, list(pairs))
    assert got == pairs
    flipped = hs.label_pairs(fx, eng, [(b, a) for a, b in pairs])
    swap = {"IMPLIES": "IMPLIED_BY", "IMPLIED_BY": "IMPLIES"}
    assert {(b, a): swap.get(v, v) for (a, b), v in pairs.items()} == flipped


@needs_engine
def test_group_labels_exact(small):
    fx, eng, o, _ = small
    teams = ("Ash", "Birch", "Cedar", "Dune")
    sets = {  # the exact terms: the bounds of the set, and ordered_chain first when it is a chain
        "win": ([o[f"win{x}"][Y] for x in teams], [("exactly_one", None)]),
        "win_pair": ([o["winAsh"][Y], o["winBirch"][Y]], [("at_most_one", None)]),
        "qualify": ([o[f"qual{x}"][Y] for x in teams], [("at_least_k", 2), ("at_most_k", 2)]),  # exactly 2
        "qualify3": ([o[f"qual{x}"][Y] for x in teams[:3]], [("at_least_k", 1), ("at_most_k", 2)]),
        "top": ([o[f"thr{x}3"][Y] for x in ("Ash", "Cedar", "Dune")], [("at_most_k", 2)]),
        "ladder": ([o["thrCedar2"][Y], o["thrCedar1"][Y], o["thrCedar3"][Y]], [("ordered_chain", None)]),
        "cross_chain": ([o["thrBirch2"][Y], o["thrAsh3"][Y], o["thrAsh1"][Y]], [("ordered_chain", None)]),
        "north": ([o[f"seednorth{x}"][Y] for x in teams[:3]], [("exactly_one", None)]),
        "decoy": ([o["thrCedar2"][Y], o["winDune"][Y], o["qualAsh"][Y]], []),
        "base_only": ([o["u2"][Y], o["thrCedar1"][N]], []),  # exactly one in base only
        "both": ([o["u1"][Y], o["thrCedar1"][N]], [("at_most_one", None)]),  # exactly one in base, at most in alt
    }
    got = hs.label_groups(fx, eng, [m for m, _ in sets.values()])
    assert {k: g[0] for k, g in zip(sets, got)} == {k: v for k, (_, v) in sets.items()}
    orders = {k: g[1] for k, g in zip(sets, got)}
    assert orders["ladder"] == [o["thrCedar3"][Y], o["thrCedar2"][Y], o["thrCedar1"][Y]]  # strongest first
    assert orders["cross_chain"] == [o["thrBirch2"][Y], o["thrAsh3"][Y], o["thrAsh1"][Y]]
    assert all(orders[k] is None for k in sets if k not in ("ladder", "cross_chain"))


@pytest.mark.parametrize("i,j,x,want", [
    (True, True, False, "EQUIV"), (True, False, False, "IMPLIES"), (False, True, False, "IMPLIED_BY"),
    (False, False, True, "EXCLUDES"), (False, False, False, "NONE"),
])
def test_relation_label_rules(i, j, x, want):
    assert hs.relation_label(i, j, x) == want


def test_relation_label_refuses_unsatisfiable():
    with pytest.raises(ValueError):
        hs.relation_label(True, False, True)


def _write_suite(root, rows):
    ir.write_jsonl(root / "candidates.jsonl", [c for c, _ in rows])
    ir.write_jsonl(root / "gold" / "suite_labels.jsonl", [lab for _, lab in rows])


def _rel_row(n, a, b, label):
    return ({"qid": f"r{n:05d}", "op": "decide_relation", "a": a, "b": b},
            {"qid": f"r{n:05d}", "op": "decide_relation", "label": label, "k": None, "missing": None, "order": None,
             "held_out": False, "stratum": "hard_distractor" if label == "NONE" else "real"})


def test_audit_engine_free_and_detects_tampering(tmp_path):
    """Hand-written labels audit clean without the engine; a flipped label, a wrong chain order and a constant
    outcome are each reported."""
    fx, _, o, two = _small(tmp_path / "fx")
    rows = [_rel_row(n, a, b, lab) for n, ((a, b), lab) in enumerate(_relation_cases(o, two).items())]
    ladder = [o["thrCedar3"][Y], o["thrCedar2"][Y], o["thrCedar1"][Y]]
    rows.append(({"qid": "g00001", "op": "decide_group", "members": ladder[::-1], "source": "curated"},
                 {"qid": "g00001", "op": "decide_group", "label": "ordered_chain", "k": None, "missing": None,
                  "order": ladder, "held_out": False, "stratum": "real", "terms": [["ordered_chain", None]]}))
    _write_suite(fx.root, rows)
    assert hs.audit(fx.root)["mismatches"] == []
    rows[0][1]["label"] = "EQUIV"
    rows[-1][1]["order"] = ladder[::-1]
    _write_suite(fx.root, rows)
    bad = {m["qid"]: m["problems"] for m in hs.audit(fx.root)["mismatches"]}
    assert bad == {"r00000": ["label"], "g00001": ["order is not a valid chain over the members"]}

    w = _World()
    a = w.market("K", "m0", "K_a", "Will Ash score?", "thr")
    b = w.market("K", "m1", "K_b", "Will Birch score?", "thr")
    w.add(_ref("K_a"))
    root = w.write(tmp_path / "const")
    _write_suite(root, [_rel_row(0, a[Y], b[Y], "IMPLIED_BY")])
    (m,) = hs.audit(root)["mismatches"]
    assert m["problems"] == [f"constant outcome {a[Y]}"]


@needs_engine
def test_constants_excluded(tmp_path):
    w = _World()
    a = w.market("K", "m0", "K_a", "Will Ash score?", "thr")
    b = w.market("K", "m1", "K_b", "Will Birch score?", "thr")
    c = w.market("K", "m2", "K_c", "Will Cedar score?", "thr")
    w.add(_ref("K_a"))  # a is always true: it would be IMPLIED_BY everything and its No would imply everything
    w.add(_imp(_ref("K_b"), _ref("K_c")))
    fx = hs.Fixture(w.write(tmp_path / "fx"))
    assert hs.constants(fx, hs._Engine(fx)) == set(a)
    hs.build(fx.root, relations=6, groups=2)
    cands = list(ir.read_jsonl(fx.root / "candidates.jsonl"))
    used = {x for c in cands if c["op"] != "decide_binding" for x in (c.get("a"), c.get("b"), *c.get("members", []))}
    assert not used & set(a) and used & set(b + c)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Ten leagues, families 1..30 (21..30 test-only), three paraphrases per league. The budgets leave room for every
    generator edge (about 1,000 on ten leagues) and for the reading rows (four per ambiguous proposition)."""
    w = _World()
    names = ("Ash", "Birch", "Cedar", "Dune", "Elm", "Fir", "Gorse", "Heath")
    for n in range(10):
        _league(w, f"L{n}", teams=tuple(names[(n + i) % 8] for i in range(4)),
                fam=lambda k, n=n: 1 + (7 * k + n) % 30, paraphrases=3)
    root = w.write(tmp_path_factory.mktemp("built") / "fx")
    summary = hs.build(root, relations=BUILT_RELATIONS, groups=BUILT_GROUPS)
    return root, summary


BUILT_RELATIONS, BUILT_GROUPS = 3_000, 400


def _rows(root):
    return list(ir.read_jsonl(root / "candidates.jsonl")), list(ir.read_jsonl(root / "gold" / "suite_labels.jsonl"))


@needs_engine
def test_contract_schema(built):
    root, _ = built
    cands, labels = _rows(root)
    ids = {r["id"] for r in ir.read_jsonl(root / "corpus.jsonl")}
    assert [c["qid"] for c in cands] == [r["qid"] for r in labels]
    assert len({c["qid"] for c in cands}) == len(cands)
    order = [c["op"] for c in cands]
    assert order == sorted(order, key=["decide_binding", "decide_relation", "decide_group"].index)
    keys = {"decide_binding": {"qid", "op", "id"}, "decide_relation": {"qid", "op", "a", "b"},
            "decide_group": {"qid", "op", "members", "source"}}
    allowed = {"decide_binding": {"bound", "unresolved"},
               "decide_relation": {"EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE"},
               "decide_group": {"none", "reading", *hs.GROUP_KINDS}}
    strata = {"decide_binding": {"real"}, "decide_relation": {"real", "hard_distractor", "trivial"},
              "decide_group": {"real", "decoy", "reading", "reading_decoy"}}
    prefix = {"decide_binding": "b", "decide_relation": "r", "decide_group": "g"}
    for c, lab in zip(cands, labels):
        assert set(c) == keys[c["op"]] and c["qid"][0] == prefix[c["op"]] and len(c["qid"]) == 6
        assert lab["op"] == c["op"] and lab["stratum"] in strata[c["op"]] and isinstance(lab["held_out"], bool)
        if c["op"] == "decide_group" and c["source"] == "reading":
            ms = c["members"]
            assert len(ms) == 5 and len(set(ms)) == 5 and set(ms) <= ids
            assert set(lab) == {"qid", "op", "stratum", "readings", "label", "k", "held_out", "missing", "order"}
            assert lab["label"] == "reading" and lab["k"] is None and lab["missing"] is None and lab["order"] is None
            assert set(lab["readings"]) == {"base", "alt"} and set(lab["readings"].values()) <= set(ms[1:])
            assert (lab["stratum"] == "reading_decoy") == (lab["readings"]["base"] == lab["readings"]["alt"])
            continue
        if c["op"] == "decide_group":
            assert set(lab) == {"qid", "op", "label", "k", "missing", "order", "held_out", "stratum", "terms"}
            terms = lab["terms"]
            assert (lab["label"], lab["k"]) == (tuple(terms[0]) if terms else ("none", None))
            assert (lab["order"] is not None) == any(kind == "ordered_chain" for kind, _ in terms)
            assert (lab["stratum"] == "decoy") == (lab["label"] == "none")
            ms = c["members"]
            assert set(ms) <= ids and 2 <= len(ms) <= 8 and len(set(ms)) == len(ms) and c["source"] == "curated"
            if lab["order"] is not None:
                assert sorted(lab["order"]) == sorted(ms)
        else:
            label_keys = {"qid", "op", "label", "k", "missing", "order", "held_out", "stratum"}
            assert set(lab) == label_keys | ({"depth"} if c["op"] == "decide_relation" else set())
        assert lab["label"] in allowed[c["op"]]
        assert (lab["k"] is not None) == (lab["label"] in ("at_least_k", "at_most_k"))
        assert (lab["missing"] is not None) == (lab["label"] == "unresolved")
        if c["op"] == "decide_binding":
            assert c["id"] in ids
        elif c["op"] == "decide_relation":
            assert {c["a"], c["b"]} <= ids and c["a"] != c["b"] and lab["order"] is None
            assert "depth" not in c  # the depth tag is gold-side only
    assert sum(c["op"] == "decide_binding" for c in cands) == len(ids)
    member_sets = [frozenset(c["members"]) for c in cands if c["op"] == "decide_group"]
    assert len(set(member_sets)) == len(member_sets)  # no two group or reading questions share a member set


@needs_engine
def test_mix_and_held_out(built):
    root, summary = built
    cands, labels = _rows(root)
    rel = [r for r in labels if r["op"] == "decide_relation"]
    assert len(rel) == BUILT_RELATIONS and set(summary["relations"]["shortfall"]) <= {"EQUIV", "trivial"}  # few pairs of each here
    assert summary["relations"]["uncovered"] == 0 and summary["relations"]["generator_edges"] > 0
    short = summary["relations"]["shortfall"]  # trivial and EQUIV are absolute counts (owner decision 2026-10-30)
    assert sum(r["stratum"] == "trivial" for r in rel) == hs.TRIVIAL_N - short.get("trivial", 0)
    assert sum(r["label"] == "EQUIV" for r in rel) == hs.EQUIV_N - short.get("EQUIV", 0)
    # NONE is NONE_SHARE of the budget (v3.1), HARD_NONE_SHARE of it rule-vocabulary pairs, the rest profile-matched
    none = summary["relations"]["none"]
    n_none = round(hs.NONE_SHARE * BUILT_RELATIONS)
    assert sum(r["stratum"] == "hard_distractor" for r in rel) == n_none - short.get("NONE_hard", 0) - short.get("NONE", 0)
    assert none["hard_vocabulary"] + none["profile_matched"] == sum(r["label"] == "NONE" for r in rel)
    assert none["hard_vocabulary"] == round(hs.HARD_NONE_SHARE * n_none) - short.get("NONE_hard", 0)
    assert all(r["label"] == "NONE" for r in rel if r["stratum"] == "hard_distractor")
    # the derived links are drawn deepest tier first (4+, 3, 2, then 1 and 0). Per class slot: a slot that left some of a
    # tier undrawn drew nothing from any shallower tier. The depth-3-or-more supply is drawn whenever the budget allows
    tiers = summary["relations"]["tiers"]
    for slot in ("IMPLIES", "IMPLIED_BY", "EXCLUDES"):
        for i, tier in enumerate(hs.DEPTH_TIERS):
            if tiers[tier]["drawn"][slot] < tiers[tier]["caps"][slot]:
                assert all(tiers[t]["drawn"][slot] == 0 for t in hs.DEPTH_TIERS[i + 1:])
    edges = summary["relations"]["generator_edges"]
    budget = len(rel) - edges - sum(r["stratum"] == "trivial" for r in rel) - sum(
        r["label"] == "EQUIV" for r in rel) - sum(r["label"] == "NONE" for r in rel)
    deep3 = sum(r["stratum"] == "real" and isinstance(r["depth"], int) and r["depth"] >= 3 for r in rel)
    supply3 = sum(n for lab in summary["relations"]["depth_supply"].values() for b, n in lab.items() if b in ("3", "4+"))
    assert deep3 == min(supply3, budget)  # every generator edge is depth 1 or 0, so no real row of depth 3 is one
    assert all(r["depth"] != "count" for r in rel)  # a counting-only entailment is never drawn
    sym = [c for c, lab in zip(cands, labels) if lab["op"] == "decide_relation" and lab["label"] in ("EQUIV", "EXCLUDES", "NONE")]
    forward = sum(c["a"] < c["b"] for c in sym)  # no direction: the orientation is random, not the sorted one
    assert 0.4 * len(sym) < forward < 0.6 * len(sym)
    implies, implied_by = (sum(r["label"] == lab for r in rel) for lab in ("IMPLIES", "IMPLIED_BY"))
    assert abs(implies - implied_by) <= 0.05 * len(rel)  # the generator edges are oriented 50/50
    assert summary["relations"]["cross_league_none_share"] <= hs.CROSS_CAP
    asked: Counter = Counter()
    for hist in summary["relations"]["depth_asked"].values():
        asked.update(hist)
    assert asked == Counter(hs._bucket(r["depth"]) for r in rel)
    base = summary["relations"]["public_baselines"]
    assert set(base) == {"related_share", "same_event_title_acc", "top_overlap_acc"}
    grp = [r for r in labels if r["op"] == "decide_group" and r["label"] != "reading"]
    assert len(grp) == BUILT_GROUPS and summary["groups"]["decoy_shortfall"] == 0
    assert summary["groups"]["uncovered"] == 0
    assert sum(r["stratum"] == "decoy" for r in grp) / len(grp) == pytest.approx(0.30, abs=0.05)
    assert {r["label"] for r in grp} == {"none", *hs.GROUP_KINDS}
    reading = [r for r in labels if r["label"] == "reading"]
    # Synthetic fixture: each league's u3 proposition is impossible in alt only, so it has no alt class and is not a
    # reading; its u1 has its alt at base + 1 (the retired coin-flip convention), so its near miss is the alt and no
    # reading row is built. Their four outcomes are the 4 * 10 uncovered rows (generated worlds have both under v3).
    assert summary["readings"]["uncovered"] == 4 * 10
    assert summary["readings"]["unresolved"] - summary["readings"]["ambiguous"] == summary["readings"]["uncovered"]
    assert summary["readings"]["complement_incomplete"] == 0  # both outcomes of each ambiguous proposition have a row
    # the synthetic u2 proposition of each league has its alt on another team at the base's threshold 1, so no decoy can
    # mirror it (the generator never produces that); exactly those ten propositions have no decoy
    assert summary["readings"]["decoy_shortfall"] == 10
    assert len(reading) == summary["readings"]["ambiguous"] + summary["readings"]["decoys"]
    assert sum(r["stratum"] == "reading_decoy" for r in reading) == summary["readings"]["decoys"]


@needs_engine
def test_reading_stats_match_the_build_report(built):
    """The stats in the build report are the stats of the reading rows (the synthetic world's u2 is not compared across
    strata: its alt is another team's threshold, which the generator never produces; see the generated-split test)."""
    root, summary = built
    cands, labels = _rows(root)
    fx = hs.Fixture(root)
    rows = [(c, lab) for c, lab in zip(cands, labels) if lab["label"] == "reading"]
    assert hs.reading_stats(fx, rows) == summary["readings"]["stats"]
    assert summary["readings"]["uncovered"] == 4 * 10  # u1 and u3 of each league: see test_mix_and_held_out


def test_depth_is_the_fewest_constraint_steps(small):
    """Depth on the hand-made league (v3.1): a ladder step is a constraint edge (depth 1); two ladder steps are free (0);
    an implication through one outscores edge and one edge is 2; a paraphrase is 0; a placement or an exclusion of one
    constraint is 1; NONE has no depth and no chain."""
    fx, _, one, _ = small
    d = hs.Depths(fx)
    yes = lambda name: one[name][Y]  # noqa: E731
    assert d.of(yes("thrAsh3"), yes("thrAsh2"), "IMPLIES") == 0  # a ladder step costs 0, a generator edge or not
    assert d.of(yes("thrAsh3"), yes("thrAsh1"), "IMPLIES") == 0
    assert d.of(yes("winAsh"), yes("thrAsh1"), "IMPLIES") == 1
    assert d.of(yes("thrBirch2"), yes("qualAsh"), "IMPLIES") == 2  # thrBirch2 => thrAsh3 (outscores) => qualAsh
    assert d.of(yes("qualAsh"), yes("thrBirch2"), "IMPLIED_BY") == 2
    assert d.of(yes("winAsh"), yes("runBirch"), "IMPLIES") == 1
    assert d.of(yes("winAsh"), yes("runAsh"), "EXCLUDES") == 1
    assert d.of(yes("thrCedar2"), yes("thrCedar2p"), "EQUIV") == 0  # the paraphrase: the same class
    assert d.of(yes("thrBirch1"), yes("thrAsh1"), "IMPLIES") == 1  # thrBirch1 => thrAsh2 (outscores), then the ladder
    assert d.of(yes("thrAsh1"), yes("thrCedar3"), "NONE") is None
    assert not d.chain(yes("thrAsh1"), yes("thrCedar3"))


def test_exactly_one_of_two_gives_both_exclusion_and_at_least_edges():
    """exactly(1, [x, y]): x => not y and not x => y, each with its contrapositive, all at cost 1."""
    from types import SimpleNamespace

    cons = [{"expr": {"op": "exactly", "k": 1, "args": [_ref("x"), _ref("y")]}, "interps": None}]
    d = hs.Depths(SimpleNamespace(theory={"constraints": cons}, bind={}, meta=None, ids=[]))
    assert d._dd(("x", True), ("y", False)) == 1 and d._dd(("y", True), ("x", False)) == 1  # exclusion
    assert d._dd(("x", False), ("y", True)) == 1 and d._dd(("y", False), ("x", True)) == 1  # at least one
    assert d._dd(("x", True), ("y", True)) is None  # both may be true or both false: nothing entailed


def test_depth_helpers():
    assert [hs._bucket(d) for d in (0, 1, 2, 3, 4, 9, hs.COUNT, None)] == ["0", "1", "2", "3", "4+", "4+", "count", "none"]
    assert [hs._tier(d) for d in (0, 1, 2, 3, 4, 5, hs.COUNT, None)] == ["0", "1", "2", "3", "4+", "4+", None, None]


@needs_engine
def test_none_rows_are_not_entailed_and_most_share_rule_vocabulary(built):
    """Every NONE row is entailed under no interpretation (audit_brute, which shares no code with the engine: neither
    a => b, b => a nor the exclusion of a and b is valid), no candidate row carries a depth, and at least the stated
    share of the NONE rows share rule vocabulary (a team, a threshold, a draw group or a constraint)."""
    root, summary = built
    cands, labels = _rows(root)
    assert all("depth" not in c for c in cands)
    th = ab.Theory(ab._json(root / "gold" / "theory.json"), ab._jsonl(root / "gold" / "bindings.jsonl"), [])
    ck = ab.Checker(th, hs.AUDIT_CONFLICT_BUDGET)

    def valid(*exprs):  # the conjunction is unsatisfiable in every interpretation
        return not any(ck.satisfiable(ck.support_roots(list(exprs)), list(exprs)))

    def neg(oid):
        return {"op": "not", "a": hs._var(oid)}

    fx = hs.Fixture(root)
    co = hs._co_occurring(fx)
    none = [(c["a"], c["b"]) for c, lab in zip(cands, labels) if lab["op"] == "decide_relation" and lab["label"] == "NONE"]
    assert len(none) == sum(r["label"] == "NONE" for r in labels if r["op"] == "decide_relation")
    for a, b in none:
        assert not valid(hs._var(a), neg(b)) and not valid(hs._var(b), neg(a)) and not valid(hs._var(a), hs._var(b))
    shared = sum(hs._shares_vocabulary(fx, p, co) for p in none)
    assert shared >= summary["relations"]["none"]["hard_vocabulary"]


@pytest.fixture(scope="module")
def generated_dev(tmp_path_factory):
    """The full generated dev split with its suite (the generator's own worlds: no synthetic exceptions)."""
    from mentat_bench import fixtures_hard

    root = tmp_path_factory.mktemp("dev") / "fx"
    fixtures_hard.gen(root, "dev")
    summary = hs.build(root)
    return root, summary


@pytest.mark.slow
def test_relation_group_and_reading_rows_come_in_league_blocks(generated_dev):
    """Each league's rows of an op form one block (consecutive requests share a rule card): a relation is in its side a's league."""
    root, _ = generated_dev
    cands, _ = _rows(root)
    fx = hs.Fixture(root)
    for op, key in (("decide_relation", lambda c: fx.league[c["a"]]),
                    ("decide_group", lambda c: fx.league[c["members"][0]])):
        for source in ((None,) if op == "decide_relation" else ("curated", "reading")):
            seq = [key(c) for c in cands if c["op"] == op and c.get("source") == source]
            blocks = [k for i, k in enumerate(seq) if i == 0 or seq[i - 1] != k]
            assert len(blocks) == len(set(blocks)), (op, source)


@pytest.mark.slow
@needs_engine
def test_generated_dev_reading_strata_are_structurally_matched(generated_dev):
    """On the generated dev split the ambiguous and decoy strata match: two options on u's team, equal option kinds,
    number-match rate, u's complement twins, held-out rows, polarity, and threshold distributions (base and other
    same-team option), and the option wording families (exactly)."""
    root, summary = generated_dev
    cands, labels = _rows(root)
    fx = hs.Fixture(root)
    rows = [(c, lab) for c, lab in zip(cands, labels) if lab["label"] == "reading"]
    stats = hs.reading_stats(fx, rows)
    assert stats == summary["readings"]["stats"]
    amb, dec = stats["reading"], stats["reading_decoy"]
    assert summary["readings"]["decoy_shortfall"] == 0
    assert amb["rows"] == dec["rows"] == summary["readings"]["ambiguous"] == summary["readings"]["decoys"]
    for key in ("same_team", "option_kinds", "u_twin", "option_twin", "held_rows", "u_negated", "option_negated",
                "number_match", "base_t", "other_t"):
        assert amb[key] == dec[key], key
    assert amb["same_team"] == {3: amb["rows"]} and dec["same_team"] == {3: dec["rows"]}  # v3: two readings or gold and mirror, and a near miss
    assert amb["u_twin"] == dec["u_twin"] == amb["rows"]  # every u's complement is named by another reading row
    assert amb["families"] == dec["families"]  # the option wording families match exactly (decoy repair)
    assert summary["readings"]["family_residual"] == 0
    # the audit (the generation gate) checks every reading row: each reading stratum is audited in full
    report = hs.audit(root)
    assert report["mismatches"] == []
    assert report["cells"]["decide_group|reading|reading"] == amb["rows"]
    assert report["cells"]["decide_group|reading|reading_decoy"] == dec["rows"]
    assert summary["readings"]["complement_incomplete"] == 0


def test_audit_checks_each_reading_option_in_its_interpretation(tmp_path):
    """The audit checks u against each reading's option under that reading: thrCedar2 is u's alt option (and u is
    not equivalent to thrCedar1 under alt), so a row with thrDune1 as its alt option is reported, and the same row
    with the right alt option is clean."""
    fx, _, o, two = _small(tmp_path / "fx")
    u = o["u1"][Y]  # base: thrCedar1, alt: thrCedar2 (see _league)
    others = [o["thrAsh1"][Y], o["thrAsh2"][Y]]  # other-team distractors, not equivalent to u in any interpretation

    def row(alt: str, team_mate: str) -> tuple[dict, dict]:
        members = [u, o["thrCedar1"][Y], team_mate, *others]
        cand = {"qid": "g00001", "op": "decide_group", "members": members, "source": "reading"}
        lab = {"qid": "g00001", "op": "decide_group", "stratum": "reading", "readings": {
            "base": o["thrCedar1"][Y], "alt": alt}, "label": "reading", "k": None, "held_out": False, "missing": None,
            "order": None}
        return cand, lab

    _write_suite(fx.root, [row(o["thrCedar2"][Y], o["thrCedar2"][Y])])
    assert hs.audit(fx.root)["mismatches"] == []
    _write_suite(fx.root, [row(o["thrDune1"][Y], o["thrDune1"][Y])])  # u is not equivalent to thrDune1 under alt
    (m,) = hs.audit(fx.root)["mismatches"]
    assert m["problems"] == ["u is not equivalent to its alt option under alt"]


@needs_engine
def test_deterministic(built, tmp_path):
    root, _ = built
    copy = tmp_path / "fx"
    (copy / "gold").mkdir(parents=True)
    for rel in ("corpus.jsonl", "fixture.json", *(f"gold/{f}" for f in ("bindings.jsonl", "theory.json",
                "provenance.jsonl", "usage.json", "meta.jsonl"))):
        (copy / rel).write_bytes((root / rel).read_bytes())
    hs.build(copy, relations=BUILT_RELATIONS, groups=BUILT_GROUPS)
    for rel in ("candidates.jsonl", "gold/suite_labels.jsonl"):
        assert (copy / rel).read_bytes() == (root / rel).read_bytes()


@needs_engine
def test_audit_agrees(built):
    root, _ = built
    _, labels = _rows(root)
    report = hs.audit(root, sample=10_000)
    assert report["mismatches"] == []
    assert report["depth"]["checked"] == BUILT_RELATIONS and report["depth"]["mismatches"] == []  # every row, sampled or not
    assert report["checked"]["decide_relation"] == BUILT_RELATIONS
    assert report["checked"]["decide_group"] == sum(r["op"] == "decide_group" for r in labels)


@needs_engine
def test_degraded_without_meta(small, tmp_path):
    fx, _, _, _ = small
    copy = tmp_path / "fx"
    (copy / "gold").mkdir(parents=True)
    for rel in ("corpus.jsonl", *(f"gold/{f}" for f in ("bindings.jsonl", "theory.json", "provenance.jsonl"))):
        (copy / rel).write_bytes((fx.root / rel).read_bytes())
    summary = hs.build(copy, relations=100, groups=10)
    _, labels = _rows(copy)
    assert not any(r["held_out"] for r in labels)
    assert summary["balance"]["decide_relation"]["n"] == 100
    assert hs.audit(copy, sample=50)["mismatches"] == []


@needs_engine
def test_cli(built, capsys):
    root, _ = built
    assert hs.main(["audit", "--fixture", str(root), "--sample", "5"]) == 0
    assert json.loads(capsys.readouterr().out)["mismatches"] == []


# ---------------------------------------------------------------- world.json cross-check on a generated split

@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    from mentat_bench import fixtures_hard

    root = tmp_path_factory.mktemp("generated") / "fx"
    fixtures_hard.gen(root, "test", leagues=2)
    return root


def _positive(root):
    """{(league, kind, team, t, subgroup): an outcome meaning the class (bound, not negated)}."""
    bind = {b["id"]: b for b in ir.read_jsonl(root / "gold" / "bindings.jsonl")}
    out = {}
    for m in ir.read_jsonl(root / "gold" / "meta.jsonl"):
        b = bind[m["id"]]
        if b["status"] == "bound" and not b["negated"]:
            out.setdefault((m["league"], m["kind"], m["team"], m["t"], m["subgroup"]), m["id"])
    return bind, out


def test_world_check_flags_labels_against_declared_rules(generated):
    bind, pos = _positive(generated)
    lg = json.loads((generated / "gold" / "world.json").read_text(encoding="utf-8"))["leagues"][0]
    lid, x, other = lg["id"], lg["winner"], next(t for t in lg["teams"] if t != lg["winner"])
    wins = [pos[(lid, "win", t, None, None)] for t in lg["teams"]]
    t2, t1 = pos[(lid, "thr", x, 2, None)], pos[(lid, "thr", x, 1, None)]
    w_x, w_o = pos[(lid, "win", x, None, None)], pos[(lid, "win", other, None, None)]
    cands = {"g1": {"members": wins}, "r1": {"a": t2, "b": t1}, "r2": {"a": w_x, "b": w_o}}

    def rows(terms, r1, r2):
        return [{"qid": "g1", "op": "decide_group", "label": terms[0][0], "k": None, "order": None, "terms": terms},
                {"qid": "r1", "op": "decide_relation", "label": r1}, {"qid": "r2", "op": "decide_relation", "label": r2}]

    clean = hs.world_check(generated, cands, rows([["exactly_one", None]], "IMPLIES", "EXCLUDES"), bind)
    assert clean["disagreements"] == [] and clean["relations_checked"] == 2 and clean["groups_checked"] == 1
    bad = hs.world_check(generated, cands, rows([["at_most_one", None]], "NONE", "IMPLIES"), bind)["disagreements"]
    assert {d["qid"]: d["problems"] for d in bad} == {
        "g1": ["declared bounds [1, 1] are not in the label"],
        "r1": ["declared (implies, implied_by, excludes) (True, False, False) not in the label"],
        "r2": ["label fails in the gold world",
               "declared (implies, implied_by, excludes) (False, False, True) not in the label"]}


@needs_engine
def test_a_chain_of_equivalent_members_is_not_asked(generated):
    """Two outcomes with the same meaning (a class's paraphrase markets) imply each other: as a group they would be an
    ordered_chain without a unique order, so label_groups drops the set."""
    fx = hs.Fixture(generated)
    by_meaning: dict = {}
    for o in fx.ids:
        b = fx.bind[o]
        if b["status"] == "bound":
            by_meaning.setdefault((b["class"], b["negated"]), []).append(o)
    pair = next(v[:2] for v in by_meaning.values() if len(v) >= 2)
    assert hs.label_groups(fx, hs._Engine(fx), [pair]) == [(None, None)]


@needs_engine
def test_generated_split_suite_agrees_with_world(generated, tmp_path):
    info = hs.build(generated, relations=800, groups=80)
    assert info["uncovered"] == 0 and info["constants_excluded"] == 0
    assert set(info["groups"]["public_baselines"]) == {"real_share", "same_event_title_acc", "top_overlap_acc",
                                                       "shared_specific_word_acc"}
    report = hs.audit(generated)
    assert report["mismatches"] == [] and report["world"]["disagreements"] == []
    assert report["world"]["relations_checked"] > 500 and report["world"]["groups_checked"] > 50
    assert report["world"]["readings_checked"] > 0

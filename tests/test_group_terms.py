"""Development round 2, stream 2: group_terms and terms_class (adapters/base.py); scoring of group labels with terms and of reading
questions (decision_scoring.py); gold_view's answers to them (adapters/mock.py); a gold_view round trip that scores MCC 1.0 on
every component; and the v1 behaviour that must not change. The contract is docs/mentat_hard_contracts.md."""

import json

import pytest

from mentat_bench import decision_scoring as DS
from mentat_bench.adapters import mock
from mentat_bench.adapters.base import group_terms, reading_picks, terms_class

MISSING = DS.MISSING
M4 = ["o1", "o2", "o3", "o4"]


def _g(kind, members=M4, k=None, direction=None, scope=None):
    group = {"kind": kind, "members": list(members)}
    if k is not None:
        group["k"] = k
    if direction is not None:
        group["direction"] = direction
    if scope is not None:
        group["scope"] = scope
    return group


# ---------------------------------------------------------------- group_terms and terms_class

@pytest.mark.parametrize("lo, hi, n, chain, want", [
    (0, 5, 5, False, []),  # no constraint
    (0, 5, 5, True, [("ordered_chain", None)]),
    (1, 1, 5, False, [("exactly_one", None)]),
    (1, 1, 5, True, [("ordered_chain", None), ("exactly_one", None)]),
    (2, 2, 5, False, [("at_least_k", 2), ("at_most_k", 2)]),  # exactly-k
    (5, 5, 5, False, [("at_least_k", 5)]),  # hi == n: no upper bound
    (0, 1, 5, False, [("at_most_one", None)]),
    (0, 3, 5, False, [("at_most_k", 3)]),
    (3, 5, 5, False, [("at_least_k", 3)]),
    (1, 3, 5, False, [("at_least_k", 1), ("at_most_k", 3)]),
    (2, 4, 5, True, [("ordered_chain", None), ("at_least_k", 2), ("at_most_k", 4)]),  # chain plus bounds, fixed order
])
def test_group_terms_is_the_canonical_order_fixed_list(lo, hi, n, chain, want):
    assert group_terms(lo, hi, n, chain) == want


@pytest.mark.parametrize("lo, hi, n", [(3, 2, 5), (0, 0, 5), (-1, 1, 5), (0, 6, 5), (0, 1, 0)])
def test_group_terms_refuses_bounds_outside_0_le_lo_le_hi_le_n_with_hi_at_least_1(lo, hi, n):
    with pytest.raises(ValueError):
        group_terms(lo, hi, n, False)


def test_terms_class_joins_the_class_keys_and_none_is_no_terms():
    assert terms_class([]) == "none"
    assert terms_class([("at_least_k", 2), ("at_most_k", 2)]) == "at_least_k:2+at_most_k:2"
    assert terms_class([("ordered_chain", None), ("at_least_k", 2)]) == "ordered_chain+at_least_k:2"
    assert terms_class([["exactly_one", None]]) == "exactly_one"  # JSON lists work too


# ---------------------------------------------------------------- group answers with terms

def test_a_group_answer_is_scored_as_the_class_of_its_bounds():
    terms = DS._terms_answer
    assert terms({"groups": [_g("at_least_k", k=2), _g("at_most_k", k=2)]}, M4)[0] == "at_least_k:2+at_most_k:2"  # exactly-2
    assert terms({"groups": [_g("at_least_k", k=2)]}, M4)[0] == "at_least_k:2"  # the upper bound was not asked
    assert terms({"groups": [_g("at_most_k", k=3), _g("at_least_k", k=1)]}, M4)[0] == "at_least_k:1+at_most_k:3"
    assert terms({"groups": [_g("at_most_k", k=1)]}, M4)[0] == "at_most_one"  # at_most_k 1 is at_most_one
    assert terms({"groups": [_g("at_most_one")]}, M4)[0] == "at_most_one"


def test_at_least_one_with_at_most_one_is_the_canonical_exactly_one():
    # [at_least_k 1, at_most_one] has lo = hi = 1, which group_terms names exactly_one (the single canonical form)
    assert DS._terms_answer({"groups": [_g("at_least_k", k=1), _g("at_most_one")]}, M4)[0] == "exactly_one"
    assert DS._terms_answer({"groups": [_g("exactly_one")]}, M4)[0] == "exactly_one"


def test_a_chain_with_bounds_is_scored_with_its_implied_order():
    decision = {"groups": [_g("ordered_chain", direction="reverse"), _g("at_least_k", k=2)]}
    cls, chain = DS._terms_answer(decision, M4)
    assert cls == "ordered_chain+at_least_k:2"
    assert chain == list(reversed(M4))  # reverse: the members run from the end


def test_contradictory_bounds_are_invalid_and_no_decision_is_none():
    assert DS._terms_answer({"groups": [_g("at_least_k", k=3), _g("at_most_one")]}, M4)[0] == "invalid"
    assert DS._terms_answer({"groups": []}, M4)[0] == "none"
    assert DS._terms_answer([], M4)[0] == "none"


def test_scoped_and_subset_decisions_are_not_the_groups_bounds():
    scoped = _g("at_most_one", scope={"axis": "reading", "values": ["base"], "of": ["alt", "base"]})
    subset = _g("at_most_one", members=["o1", "o2"])
    assert DS._terms_answer({"groups": [scoped]}, M4)[0] == "none"
    assert DS._terms_answer({"groups": [subset, scoped]}, M4)[0] == "none"


def test_a_malformed_sibling_is_skipped_when_a_decision_is_valid():
    decision = {"groups": [{"kind": "bogus", "members": M4}, _g("at_least_k", k=2), _g("at_most_k", k=2)]}
    assert DS._terms_answer(decision, M4)[0] == "at_least_k:2+at_most_k:2"
    assert DS._terms_answer({"groups": [{"kind": "bogus", "members": M4}]}, M4)[0] == MISSING  # no valid decision


def test_a_malformed_group_decision_is_missing():
    assert DS._terms_answer({"groups": [{"kind": "bogus", "members": M4}]}, M4)[0] == MISSING
    assert DS._terms_answer({"groups": [_g("at_least_k", members=["o1", "zz"], k=1)]}, M4)[0] == MISSING  # outside the context
    assert DS._terms_answer({"groups": None}, M4)[0] == MISSING


def _group_item(label_terms, label, decision, members=M4, order=None, qid="g1", probs=None):
    label_row = {"op": "decide_group", "label": label, "k": None, "terms": label_terms, "held_out": False,
                 "stratum": "real", "order": order}
    row = {"qid": qid, "op": "decide_group", "decision": decision, "conf": None, "probs": probs, "error": None}
    return DS._item(qid, label_row, {qid: {"members": list(members)}}, row)


def test_the_truth_of_a_group_with_terms_is_the_class_of_its_terms_and_its_pred_is_checked():
    exactly_two = [["at_least_k", 2], ["at_most_k", 2]]
    item = _group_item(exactly_two, "at_least_k", {"groups": [_g("at_least_k", k=2), _g("at_most_k", k=2)]})
    assert item["truth"] == "at_least_k:2+at_most_k:2" and item["pred"] == item["truth"] and item["option"] == "at_least_k"
    decoy = _group_item([], "none", {"groups": []}, members=["o1", "o2"])
    assert decoy["truth"] == "none" and decoy["pred"] == "none"
    assert _group_item(exactly_two, "at_least_k", None)["pred"] == MISSING  # no answer
    wrong = _group_item(exactly_two, "at_least_k", {"groups": [_g("at_least_k", k=3), _g("at_most_k", k=2)]})
    assert wrong["pred"] == "invalid"  # lo 3 > hi 2


def test_chain_order_counts_a_chain_with_bounds_by_its_implied_order():
    order = ["o1", "o2", "o3"]
    terms = [["ordered_chain", None], ["at_least_k", 2]]
    right = _group_item(terms, "ordered_chain", {"groups": [_g("ordered_chain", members=order, direction="forward"),
                                                            _g("at_least_k", members=order, k=2)]}, members=order, order=order,
                        qid="c1")
    wrong = _group_item(terms, "ordered_chain", {"groups": [_g("ordered_chain", members=list(reversed(order))),
                                                           _g("at_least_k", members=order, k=2)]}, members=order, order=order,
                        qid="c2")
    assert right["pred"] == "ordered_chain+at_least_k:2" and right["truth"] == right["pred"]
    assert DS.chain_order([right, wrong]) == {"n": 2, "correct": 1, "accuracy": 0.5}


def test_a_group_with_terms_is_not_scored_by_probs_but_a_v1_group_is():
    probs = {"none": 0.1, "exactly_one": 0.1, "ordered_chain": 0.1, "at_least_k": 0.5, "at_most_k": 0.1, "at_most_one": 0.1}
    item = _group_item([["at_least_k", 2], ["at_most_k", 2]], "at_least_k", {"groups": [_g("at_least_k", k=2)]}, probs=probs)
    assert item["pred"] == "at_least_k:2" and item["probs"] is None
    assert DS.brier([item]) == {"value": None, "n": 0, "excluded": 1}
    v1 = {"op": "decide_group", "label": "at_least_k", "k": 2, "held_out": False, "stratum": "real", "order": None}
    row = {"qid": "g1", "op": "decide_group", "decision": {"groups": [_g("at_least_k", k=2)]}, "conf": None, "probs": probs,
           "error": None}
    assert DS._item("g1", v1, {"g1": {"members": M4}}, row)["probs"] == probs


def test_the_group_kind_diagnostic_ignores_k_in_compound_classes():
    assert DS._kind("at_least_k:2+at_most_k:2") == "at_least_k+at_most_k"
    assert DS._kind("at_least_k:3") == "at_least_k" and DS._kind("none") == "none" and DS._kind(MISSING) == MISSING


# ---------------------------------------------------------------- reading questions

RMEM = ["x", "p", "q", "r", "s"]  # u, then the four options
RCAND = {"qid": "g9", "op": "decide_group", "members": RMEM, "source": "reading"}


def _label(readings, stratum="reading"):
    return {"op": "decide_group", "qid": "g9", "stratum": stratum, "readings": readings, "label": "reading", "k": None,
            "held_out": False, "missing": None, "order": None}


def _pair(u, c, reading):
    """The two chains of a pick c for `reading`; None makes them unscoped, which answers both readings."""
    scope = None if reading is None else {"axis": "reading", "values": [reading], "of": ["alt", "base"]}
    return [_g("ordered_chain", members=[u, c], direction="forward", scope=scope),
            _g("ordered_chain", members=[c, u], direction="forward", scope=scope)]


def _reading(readings, groups, stratum="reading"):
    row = {"qid": "g9", "op": "decide_group", "decision": {"groups": groups}, "conf": None, "probs": None, "error": None}
    return DS._reading_items("g9", _label(readings, stratum), {"g9": RCAND}, row)


def _by_reading(items):
    return {"base": items[0], "alt": items[1]}


def test_an_ambiguous_row_with_different_readings_gives_the_option_of_each():
    items = _reading({"base": "q", "alt": "r"}, _pair("x", "q", "base") + _pair("x", "r", "alt"))
    assert [it["op"] for it in items] == ["decide_reading", "decide_reading"]
    assert (items[0]["truth"], items[0]["pred"]) == ("opt2", "opt2")  # q is members[2]
    assert (items[1]["truth"], items[1]["pred"]) == ("opt3", "opt3")  # r is members[3]


def test_a_decoy_with_the_same_option_for_both_readings_is_that_option_twice():
    items = _reading({"base": "p", "alt": "p"}, _pair("x", "p", "base") + _pair("x", "p", "alt"), stratum="reading_decoy")
    assert [(it["truth"], it["pred"], it["stratum"]) for it in items] == [("opt1", "opt1", "reading_decoy")] * 2


def test_a_none_reading_is_none_and_emits_nothing():
    items = _reading({"base": None, "alt": "s"}, _pair("x", "s", "alt"))
    base, alt = _by_reading(items)["base"], _by_reading(items)["alt"]
    assert (base["truth"], base["pred"]) == ("none", "none")
    assert (alt["truth"], alt["pred"]) == ("opt4", "opt4")
    assert [(it["truth"], it["pred"]) for it in _reading({"base": None, "alt": None}, [])] == [("none", "none")] * 2


def test_two_options_for_one_reading_are_invalid_and_so_is_a_half_pair():
    two = _reading({"base": "q", "alt": None}, _pair("x", "q", "base") + _pair("x", "p", "base"))
    assert two[0]["pred"] == "invalid" and two[0]["truth"] == "opt2"
    half = _reading({"base": "q", "alt": None}, _pair("x", "q", "base")[:1])
    assert half[0]["pred"] == "invalid"


def test_a_pair_scoped_to_both_readings_answers_each_of_them():
    both = [_g("ordered_chain", members=["x", "q"], direction="forward",
               scope={"axis": "reading", "values": ["alt", "base"], "of": ["alt", "base"]}),
            _g("ordered_chain", members=["q", "x"], direction="forward",
               scope={"axis": "reading", "values": ["alt", "base"], "of": ["alt", "base"]})]
    items = _reading({"base": "q", "alt": "q"}, both)
    assert [(it["truth"], it["pred"]) for it in items] == [("opt2", "opt2"), ("opt2", "opt2")]


def test_an_unscoped_pair_answers_both_readings_so_a_none_truth_scores_wrong():
    items = _reading({"base": None, "alt": None}, _pair("x", "q", None))
    assert [(it["truth"], it["pred"]) for it in items] == [("none", "opt2"), ("none", "opt2")]
    ambiguous = _reading({"base": "q", "alt": "r"}, _pair("x", "q", None))  # the same option is wrong for alt
    assert [(it["truth"], it["pred"]) for it in ambiguous] == [("opt2", "opt2"), ("opt3", "opt2")]


def test_an_unscoped_pair_with_a_scoped_pair_to_another_option_is_invalid():
    items = _reading({"base": "q", "alt": None}, _pair("x", "q", None) + _pair("x", "p", "base"))
    assert items[0]["pred"] == "invalid"  # base: the unscoped pair and the base pair to p
    assert items[1]["pred"] == "opt2"  # alt: only the unscoped pair counts


def test_a_malformed_sibling_does_not_spoil_a_reading_with_a_valid_pair():
    items = _reading({"base": "q", "alt": None}, _pair("x", "q", "base") + [{"kind": "bogus"}])
    assert (items[0]["pred"], items[1]["pred"]) == ("opt2", "none")
    assert _reading({"base": "q", "alt": None}, [{"kind": "bogus"}])[0]["pred"] == MISSING


def test_an_unanswered_or_errored_reading_is_missing():
    cand = {"g9": RCAND}
    label = _label({"base": "q", "alt": "r"})
    assert [it["pred"] for it in DS._reading_items("g9", label, cand, None)] == [MISSING, MISSING]
    row = {"qid": "g9", "op": "decide_group", "decision": None, "error": "timeout"}
    assert [it["pred"] for it in DS._reading_items("g9", label, cand, row)] == [MISSING, MISSING]


def test_load_splits_a_reading_row_into_base_and_alt_in_label_order(tmp_path):
    labels = [{"qid": "b1", "op": "decide_binding", "label": "bound", "k": None, "missing": None, "held_out": False,
               "stratum": "real", "order": None}, _label({"base": "q", "alt": None})]
    candidates = [{"qid": "b1", "op": "decide_binding", "id": "x"}, RCAND]
    decisions = [{"qid": "g9", "op": "decide_group", "members": RMEM, "decision": {"groups": _pair("x", "q", "base")},
                  "conf": None, "probs": None, "error": None}]
    paths = {}
    for name, rows in (("labels", labels), ("candidates", candidates), ("decisions", decisions)):
        paths[name] = tmp_path / f"{name}.jsonl"
        paths[name].write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    items, counts = DS.load(paths["labels"], paths["candidates"], paths["decisions"])
    assert [(it["qid"], it["op"], it["truth"], it["pred"]) for it in items] == [
        ("b1", "decide_binding", "bound", MISSING), ("g9", "decide_reading", "opt2", "opt2"),
        ("g9", "decide_reading", "none", "none")]
    assert counts["labels"] == 2


# ---------------------------------------------------------------- gold_view round trip

def _world():
    """A tiny suite: relations, bindings, groups with terms (exactly-one, chain plus bounds, exactly-k, decoy, at_most_one,
    at_least_k) and reading questions (different readings, a decoy, a none reading)."""
    groups = [
        ("g1", ["a", "b", "c", "d"], [["exactly_one", None]], "exactly_one", None, None),
        ("g2", ["a", "b", "c"], [["ordered_chain", None], ["at_least_k", 2]], "ordered_chain", None, ["a", "b", "c"]),
        ("g3", ["a", "b", "d", "e"], [["at_least_k", 2], ["at_most_k", 2]], "at_least_k", 2, None),
        ("g4", ["b", "c"], [], "none", None, None),
        ("g5", ["a", "c"], [["at_most_one", None]], "at_most_one", None, None),
        ("g6", ["a", "c", "d", "e"], [["at_least_k", 3]], "at_least_k", 3, None),
    ]
    cands = [{"qid": "b1", "op": "decide_binding", "id": "a"}, {"qid": "b2", "op": "decide_binding", "id": "b"},
             {"qid": "r1", "op": "decide_relation", "a": "a", "b": "b"}, {"qid": "r2", "op": "decide_relation", "a": "c", "b": "d"},
             {"qid": "r3", "op": "decide_relation", "a": "d", "b": "c"}]
    labels = [{"qid": "b1", "op": "decide_binding", "label": "bound", "k": None, "missing": None, "held_out": False,
               "stratum": "real", "order": None},
              {"qid": "b2", "op": "decide_binding", "label": "unresolved", "k": None, "missing": ["out_of_scope"], "held_out": False,
               "stratum": "real", "order": None},
              {"qid": "r1", "op": "decide_relation", "label": "IMPLIES", "k": None, "missing": None, "held_out": False,
               "stratum": "real", "order": None},
              {"qid": "r2", "op": "decide_relation", "label": "NONE", "k": None, "missing": None, "held_out": False,
               "stratum": "decoy", "order": None},
              {"qid": "r3", "op": "decide_relation", "label": "EXCLUDES", "k": None, "missing": None, "held_out": True,
               "stratum": "real", "order": None}]
    for qid, members, terms, label, k, order in groups:
        cands.append({"qid": qid, "op": "decide_group", "members": members, "source": "curated"})
        labels.append({"qid": qid, "op": "decide_group", "label": label, "k": k, "terms": terms, "missing": None,
                       "held_out": False, "stratum": "decoy" if not terms else "real", "order": order})
    readings = [("rd1", ["x", "p", "q", "r", "s"], {"base": "q", "alt": "r"}, "reading"),
                ("rd2", ["y", "p", "q", "r", "s"], {"base": "p", "alt": "p"}, "reading_decoy"),
                ("rd3", ["z", "p", "q", "r", "s"], {"base": None, "alt": "s"}, "reading")]
    for qid, members, pick, stratum in readings:
        cands.append({"qid": qid, "op": "decide_group", "members": members, "source": "reading"})
        labels.append({"qid": qid, "op": "decide_group", "stratum": stratum, "readings": pick, "label": "reading", "k": None,
                       "held_out": False, "missing": None, "order": None})
    return cands, labels


def test_gold_view_answers_round_trip_to_mcc_one_on_every_component(tmp_path):
    fx = tmp_path / "fixture"
    gold = fx / "gold"
    gold.mkdir(parents=True)
    cands, labels = _world()
    (fx / "candidates.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cands), encoding="utf-8")
    (gold / "suite_labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in labels), encoding="utf-8")
    (gold / "bindings.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"id": "a", "status": "bound", "class": "k1", "negated": False, "missing": []},
        {"id": "b", "status": "unresolved", "class": None, "negated": False, "missing": ["out_of_scope"]}]), encoding="utf-8")
    (gold / "theory.json").write_text(json.dumps({"interpretations": [], "constraints": []}), encoding="utf-8")
    adapter = mock.GoldViewAdapter(gold)
    rows = []
    for c in cands:
        base = {"qid": c["qid"], "op": c["op"], "conf": None, "probs": None, "error": None}
        if c["op"] == "decide_binding":
            decision, _ = adapter.decide_binding({"id": c["id"]})
            rows.append({**base, "id": c["id"], "decision": decision})
        elif c["op"] == "decide_relation":
            decision, _ = adapter.decide_relation({"id": c["a"]}, {"id": c["b"]})
            rows.append({**base, "a": c["a"], "b": c["b"], "decision": decision})
        else:
            raw, _ = adapter.decide_group({"group_id": c["qid"], "source": "file",
                                           "members": [{"id": m} for m in c["members"]]})
            rows.append({**base, "members": c["members"], "decision": {"groups": raw}})
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    items, counts = DS.load(gold / "suite_labels.jsonl", fx / "candidates.jsonl", decisions)
    assert len(items) == len(labels) + 3  # each of the three reading questions gives two items
    assert all(it["pred"] == it["truth"] for it in items), [(it["qid"], it["truth"], it["pred"]) for it in items
                                                             if it["pred"] != it["truth"]]
    out = DS.score(items, counts, 2)
    head = out["headline"]
    assert head["relation_mcc"] == 1.0 and head["secondary"]["binding_mcc"] == 1.0
    assert head["diagnostics"] == {"group_mcc": 1.0, "reading_mcc": 1.0}
    assert out["ops"]["decide_group"]["chain_order"] == {"n": 1, "correct": 1, "accuracy": 1.0}


def test_the_gold_view_group_answer_has_one_decision_per_term():
    ids = list(M4)
    exactly_two = {"label": "at_least_k", "k": 2, "terms": [["at_least_k", 2], ["at_most_k", 2]], "order": None}
    assert mock._labelled_group(exactly_two, ids) == [
        {"kind": "at_least_k", "members": ids, "k": 2}, {"kind": "at_most_k", "members": ids, "k": 2}]
    chain = {"label": "ordered_chain", "k": None, "terms": [["ordered_chain", None], ["at_most_one", None]],
             "order": ["o2", "o1", "o3", "o4"]}
    assert mock._labelled_group(chain, ids) == [
        {"kind": "ordered_chain", "members": ["o2", "o1", "o3", "o4"], "direction": "forward"},
        {"kind": "at_most_one", "members": ids}]


def test_a_v1_label_without_terms_keeps_its_one_canonical_decision():
    v1 = {"label": "at_least_k", "k": 2}
    assert mock._labelled_group(v1, M4) == [{"kind": "at_least_k", "members": M4, "k": 2}]
    assert mock._labelled_group({"label": "at_most_one", "k": None}, M4) == [{"kind": "at_most_one", "members": M4}]
    assert mock._labelled_group({"label": "none", "k": None}, M4) == []
    chain = {"label": "ordered_chain", "k": None, "order": ["o2", "o1"]}
    assert mock._labelled_group(chain, ["o1", "o2"]) == [{"kind": "ordered_chain", "members": ["o2", "o1"], "direction": "forward"}]


def test_v1_group_scoring_takes_the_first_group_only():
    # a v1 label (no terms): the first group's kind decides, and the second group is not read
    label = {"op": "decide_group", "label": "at_least_k", "k": 2, "held_out": False, "stratum": "real", "order": None}
    row = {"qid": "g1", "op": "decide_group", "decision": {"groups": [_g("at_least_k", k=3), _g("at_most_one")]},
           "conf": None, "probs": None, "error": None}
    item = DS._item("g1", label, {"g1": {"members": M4}}, row)
    assert item["truth"] == "at_least_k:2" and item["pred"] == "at_least_k:3"


def _write_gold(tmp_path, cands, labels):
    fx = tmp_path / "fixture"
    gold = fx / "gold"
    gold.mkdir(parents=True)
    (fx / "candidates.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cands), encoding="utf-8")
    (gold / "suite_labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in labels), encoding="utf-8")
    (gold / "bindings.jsonl").write_text("", encoding="utf-8")
    (gold / "theory.json").write_text(json.dumps({"interpretations": [], "constraints": []}), encoding="utf-8")
    return gold


def _reading_label(qid, readings):
    return {"qid": qid, "op": "decide_group", "stratum": "reading", "readings": readings, "label": "reading", "k": None,
            "held_out": False, "missing": None, "order": None}


def test_gold_view_keys_group_answers_by_members_in_file_order_and_refuses_only_an_exact_duplicate(tmp_path):
    rd_a = ["x", "p", "q", "r", "s"]
    rd_b = ["x", "s", "r", "q", "p"]  # the same member set in another order: a question of its own
    cands = [{"qid": "g1", "op": "decide_group", "members": rd_a, "source": "reading"},
             {"qid": "g2", "op": "decide_group", "members": rd_b, "source": "reading"}]
    labels = [_reading_label("g1", {"base": "q", "alt": None}), _reading_label("g2", {"base": "s", "alt": None})]
    adapter = mock.GoldViewAdapter(_write_gold(tmp_path / "ok", cands, labels))
    got_a, _ = adapter.decide_group({"group_id": "a", "source": "file", "members": [{"id": m} for m in rd_a]})
    got_b, _ = adapter.decide_group({"group_id": "b", "source": "file", "members": [{"id": m} for m in rd_b]})
    assert [g["members"] for g in got_a] == [["x", "q"], ["q", "x"]]
    assert [g["members"] for g in got_b] == [["x", "s"], ["s", "x"]]
    with pytest.raises(ValueError, match="same members in the same order"):
        mock._suite_answers(_write_gold(tmp_path / "dup", cands + [dict(cands[0], qid="g3")], labels + [_reading_label("g3", {"base": None, "alt": None})]))


def test_two_group_questions_with_the_same_members_in_the_same_order_are_refused(tmp_path):
    cands = [{"qid": "g1", "op": "decide_group", "members": ["a", "b"], "source": "curated"},
             {"qid": "g2", "op": "decide_group", "members": ["a", "b"], "source": "curated"}]
    labels = [{"qid": q, "op": "decide_group", "label": "none", "k": None, "held_out": False, "stratum": "decoy"}
              for q in ("g1", "g2")]
    with pytest.raises(ValueError, match="same members"):
        mock._suite_answers(_write_gold(tmp_path, cands, labels))


def test_gold_view_answers_chain_plus_exactly_one_as_both_terms(tmp_path):
    members = ["a", "b", "c"]
    cands = [{"qid": "g1", "op": "decide_group", "members": members, "source": "curated"}]
    labels = [{"qid": "g1", "op": "decide_group", "label": "ordered_chain", "k": None, "held_out": False, "stratum": "real",
               "terms": [["ordered_chain", None], ["exactly_one", None]], "order": ["b", "a", "c"]}]
    adapter = mock.GoldViewAdapter(_write_gold(tmp_path, cands, labels))
    raw, _ = adapter.decide_group({"group_id": "g1", "source": "file", "members": [{"id": m} for m in members]})
    assert raw == [{"kind": "ordered_chain", "members": ["b", "a", "c"], "direction": "forward"},
                   {"kind": "exactly_one", "members": members}]
    row = {"qid": "g1", "op": "decide_group", "members": members, "decision": {"groups": raw}, "conf": None, "probs": None,
           "error": None}
    item = DS._item("g1", labels[0], {"g1": cands[0]}, row)
    assert item["truth"] == item["pred"] == "ordered_chain+exactly_one"
    assert DS.chain_order([item]) == {"n": 1, "correct": 1, "accuracy": 1.0}


def test_a_v1_score_has_only_the_v1_keys():
    rel = {"op": "decide_relation", "label": "IMPLIES", "k": None, "held_out": False, "stratum": "real", "order": None}
    grp = {"op": "decide_group", "label": "at_least_k", "k": 2, "held_out": False, "stratum": "real", "order": None}
    rel_row = {"qid": "r1", "op": "decide_relation", "a": "x", "b": "y", "decision": "IMPLIES", "conf": None, "probs": None,
               "error": None}
    grp_row = {"qid": "g1", "op": "decide_group", "decision": {"groups": [_g("at_least_k", k=2)]}, "conf": None,
               "probs": None, "error": None}
    items = [DS._item("r1", rel, {"r1": {"a": "x", "b": "y"}}, rel_row), DS._item("g1", grp, {"g1": {"members": M4}}, grp_row)]
    out = DS.score(items, {}, 1)
    assert set(out["headline"]) == {"metric", "relation_mcc", "secondary", "diagnostics"}
    assert set(out["ops"]) == {"decide_relation", "decide_group", "decide_binding"}
    assert out["headline"]["relation_mcc"] is not None and out["headline"]["diagnostics"]["group_mcc"] is not None
    assert out["headline"]["secondary"]["binding_mcc"] is None  # no binding items in this run


def test_the_v1_cli_output_has_no_v2_keys_in_its_bytes(tmp_path):
    rel = {"op": "decide_relation", "label": "IMPLIES", "k": None, "held_out": False, "stratum": "real", "order": None}
    grp = {"op": "decide_group", "label": "at_least_k", "k": 2, "held_out": False, "stratum": "real", "order": None}
    labels = [dict(rel, qid="r1"), dict(grp, qid="g1")]
    candidates = [{"qid": "r1", "op": "decide_relation", "a": "x", "b": "y"},
                  {"qid": "g1", "op": "decide_group", "members": M4, "source": "curated"}]
    decisions = [{"qid": "r1", "op": "decide_relation", "a": "x", "b": "y", "decision": "IMPLIES", "conf": None, "probs": None,
                  "error": None},
                 {"qid": "g1", "op": "decide_group", "decision": {"groups": [_g("at_least_k", k=2)]}, "conf": None,
                  "probs": None, "error": None}]
    paths = {}
    for name, rows in (("labels", labels), ("candidates", candidates), ("decisions", decisions)):
        paths[name] = tmp_path / f"{name}.jsonl"
        paths[name].write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (tmp_path / "fixture.json").write_text(json.dumps({"version": "1"}), encoding="utf-8")
    out = tmp_path / "score_decisions.json"
    assert DS.main(["--decisions", str(paths["decisions"]), "--labels", str(paths["labels"]),
                    "--candidates", str(paths["candidates"]), "--fixture", str(tmp_path), "--out", str(out)]) == 0
    text = out.read_bytes()
    for key in (b"readings", b"decide_reading", b"mcc_mean", b"ops_counted", b"components"):
        assert key not in text


def test_the_console_line_prints_the_readings_value_when_it_lists_readings(tmp_path, capsys):
    labels = [_reading_label("g9", {"base": "q", "alt": None})]
    candidates = [RCAND]
    decisions = [{"qid": "g9", "op": "decide_group", "members": RMEM, "decision": {"groups": _pair("x", "q", "base")},
                  "conf": None, "probs": None, "error": None}]
    paths = {}
    for name, rows in (("labels", labels), ("candidates", candidates), ("decisions", decisions)):
        paths[name] = tmp_path / f"{name}.jsonl"
        paths[name].write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (tmp_path / "fixture.json").write_text(json.dumps({"version": "2"}), encoding="utf-8")
    out = tmp_path / "score_decisions.json"
    assert DS.main(["--decisions", str(paths["decisions"]), "--labels", str(paths["labels"]),
                    "--candidates", str(paths["candidates"]), "--fixture", str(tmp_path), "--out", str(out)]) == 0
    line = capsys.readouterr().out
    assert line.startswith("headline relation_mcc n/a; secondary binding MCC n/a; diagnostics readings 1.0000")


# ---------------------------------------------------------------- reading_picks (the shared rule of the scorer and the harness)

RP = ["x", "p", "q", "r", "s"]  # u, then the four options


def _chain(members, direction=None, scope=None):
    chain = {"kind": "ordered_chain", "members": list(members)}
    if direction is not None:
        chain["direction"] = direction
    if scope is not None:
        chain["scope"] = scope
    return chain


def _on(reading, of=("alt", "base"), axis="reading"):
    return {"axis": axis, "values": [reading], "of": list(of)}


def test_reading_picks_gives_each_readings_option():
    groups = [_chain(["x", "q"], scope=_on("base")), _chain(["q", "x"], scope=_on("base")),
              _chain(["x", "r"], scope=_on("alt")), _chain(["r", "x"], scope=_on("alt"))]
    assert reading_picks({"groups": groups}, RP) == {"base": "q", "alt": "r"}
    assert reading_picks(groups, RP) == {"base": "q", "alt": "r"}  # the bare list form


def test_reading_picks_an_unscoped_pair_is_the_pick_of_both_readings_and_none_is_none():
    assert reading_picks({"groups": [_chain(["x", "q"]), _chain(["q", "x"])]}, RP) == {"base": "q", "alt": "q"}
    assert reading_picks({"groups": []}, RP) == {"base": "none", "alt": "none"}
    assert reading_picks([], RP) == {"base": "none", "alt": "none"}
    only_alt = [_chain(["x", "s"], scope=_on("alt")), _chain(["s", "x"], scope=_on("alt"))]
    assert reading_picks(only_alt, RP) == {"base": "none", "alt": "s"}


def test_reading_picks_two_options_or_a_half_pair_or_a_stray_chain_is_invalid():
    two = [_chain(["x", "q"], scope=_on("base")), _chain(["q", "x"], scope=_on("base")),
           _chain(["x", "p"], scope=_on("base")), _chain(["p", "x"], scope=_on("base"))]
    assert reading_picks(two, RP)["base"] == "invalid"
    assert reading_picks([_chain(["x", "q"], scope=_on("base"))], RP) == {"base": "invalid", "alt": "none"}
    stray = [_chain(["x", "q"]), _chain(["q", "x"]), _chain(["x", "p"])]  # the pair, and an unpaired chain
    assert reading_picks(stray, RP) == {"base": "invalid", "alt": "invalid"}


def test_reading_picks_reads_the_implied_order_of_a_reverse_chain():
    reverse = [_chain(["x", "q"], direction="reverse", scope=_on("base")), _chain(["x", "q"], scope=_on("base"))]
    assert reading_picks(reverse, RP)["base"] == "q"  # implied q => x and x => q
    same = [_chain(["x", "q"], scope=_on("base")), _chain(["x", "q"], scope=_on("base"))]
    assert reading_picks(same, RP)["base"] == "invalid"  # the same direction twice is not a pair


def test_reading_picks_counts_only_reading_chains_of_exactly_alt_and_base():
    gold_axis = [_chain(["x", "q"], scope=_on("base", axis="gold")), _chain(["q", "x"], scope=_on("base", axis="gold"))]
    assert reading_picks(gold_axis, RP) == {"base": "none", "alt": "none"}
    wider_of = [_chain(["x", "q"], scope=_on("base", of=("alt", "base", "gold"))),
                _chain(["q", "x"], scope=_on("base", of=("alt", "base", "gold")))]
    assert reading_picks(wider_of, RP) == {"base": "none", "alt": "none"}


def test_reading_picks_ignores_malformed_non_chain_and_off_u_siblings_and_three_member_chains():
    pair = [_chain(["x", "q"], scope=_on("base")), _chain(["q", "x"], scope=_on("base"))]
    noisy = pair + [{"kind": "bogus"}, {"kind": "at_most_one", "members": ["x", "p"]}, _chain(["q", "r"]),
                    _chain(["x", "q", "r"])]
    assert reading_picks({"groups": noisy}, RP) == {"base": "q", "alt": "none"}


def test_reading_picks_of_an_answer_that_is_not_a_list_is_invalid_for_both():
    assert reading_picks("garbage", RP) == {"base": "invalid", "alt": "invalid"}
    assert reading_picks({"groups": None}, RP) == {"base": "invalid", "alt": "invalid"}


def test_the_scorer_reads_a_chain_on_another_axis_as_no_pick():
    other = [_g("ordered_chain", members=["x", "q"], direction="forward", scope={"axis": "gold", "values": ["base"],
                                                                                 "of": ["alt", "base"]}),
             _g("ordered_chain", members=["q", "x"], direction="forward", scope={"axis": "gold", "values": ["base"],
                                                                                 "of": ["alt", "base"]})]
    items = _reading({"base": "q", "alt": None}, other)
    assert [it["pred"] for it in items] == ["none", "none"]  # base: wrong, as the pick is not counted


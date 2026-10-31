"""python/mentat_bench/decision_scoring.py (WS5a): the multiclass MCC against known values, the metrics on hand-built items, the
orientation of a reversed relation (labels and probs), the missing and error items, the join and its validation, Brier and ECE
by hand, per-stratum and seen/held-out reports, and the CLI output."""

import json
import math

import pytest

from mentat_bench import decision_scoring as DS

MISSING = DS.MISSING


def _classic_mcc(tp, tn, fp, fn):
    den = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    return (tp * tn - fp * fn) / den if den else 0.0


def _binary(tp, tn, fp, fn):
    return [(1, 1)] * tp + [(0, 0)] * tn + [(0, 1)] * fp + [(1, 0)] * fn


def test_mcc_of_a_binary_problem_is_the_classic_mcc():
    for counts in [(20, 50, 5, 10), (3, 4, 1, 2), (7, 0, 3, 5)]:
        assert DS.mcc(_binary(*counts)) == pytest.approx(_classic_mcc(*counts))


def test_mcc_multiclass_known_value_and_the_edge_cases():
    # truths A A B B C C, predictions A B B B C A: c = 4, sum_k t_k p_k = 2*2 + 2*3 + 2*1 = 12, so R_K = 12 / sqrt(24 * 22)
    assert DS.mcc([("A", "A"), ("A", "B"), ("B", "B"), ("B", "B"), ("C", "C"), ("C", "A")]) == pytest.approx(12 / math.sqrt(528))
    assert DS.mcc([("A", "A"), ("B", "B"), ("C", "C")]) == 1.0  # perfect
    assert DS.mcc([("A", "X"), ("B", "X"), ("A", "X")]) == 0.0  # constant predictor
    assert DS.mcc([("A", "A"), ("A", "A")]) == 0.0  # constant truth
    assert DS.mcc([]) == 0.0


def _item(truth, pred, stratum="real", held_out=False, probs=None, option=None):
    return {"qid": "q", "op": "decide_relation", "stratum": stratum, "held_out": held_out, "truth": truth,
            "option": truth if option is None else option, "pred": pred, "flipped": False, "conf": None, "probs": probs}


def test_metrics_per_class_confusion_coverage_and_macro_f1_by_hand():
    items = [_item("IMPLIES", "IMPLIES"), _item("NONE", MISSING), _item("IMPLIES", "NONE")]
    m = DS.metrics(items)
    assert (m["n"], m["answered"], m["coverage"], m["accuracy"]) == (3, 2, 2 / 3, 1 / 3)
    assert m["confusion"] == {"IMPLIES": {"IMPLIES": 1, "NONE": 1}, "NONE": {MISSING: 1}}
    assert m["per_class"]["IMPLIES"] == {"support": 2, "predicted": 1, "precision": 1.0, "recall": 0.5, "f1": pytest.approx(2 / 3)}
    assert m["per_class"]["NONE"] == {"support": 1, "predicted": 1, "precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert m["per_class"][MISSING] == {"support": 0, "predicted": 1, "precision": 0.0, "recall": None, "f1": 0.0}
    assert m["macro_f1"] == pytest.approx((2 / 3) / 2)  # IMPLIES and NONE: __missing__ is not averaged (it is still wrong)
    assert m["mcc"] == 0.0
    assert DS.metrics([])["mcc"] is None and DS.metrics([])["coverage"] is None  # no items: undefined, not 0


def test_brier_and_ece_by_hand_with_unscored_items_counted():
    a = _item("IMPLIES", "IMPLIES", probs={"EQUIV": 0.1, "IMPLIES": 0.6, "IMPLIED_BY": 0.1, "EXCLUDES": 0.1, "NONE": 0.1})
    b = _item("EQUIV", "EQUIV", probs={"EQUIV": 1.0, "IMPLIES": 0.0, "IMPLIED_BY": 0.0, "EXCLUDES": 0.0, "NONE": 0.0})
    c = _item("NONE", "NONE")  # no probs: excluded
    br = DS.brier([a, b, c])
    assert br == {"value": pytest.approx(0.1), "n": 2, "excluded": 1}  # (0.01 + 0.16 + 3 * 0.01 + 0) / 2
    # a: top IMPLIES 0.6, correct; d: top NONE 0.9, wrong. ECE = 1/2 |1 - 0.6| + 1/2 |0 - 0.9|
    d = _item("IMPLIES", "NONE", probs={"EQUIV": 0.025, "IMPLIES": 0.025, "IMPLIED_BY": 0.025, "EXCLUDES": 0.025, "NONE": 0.9})
    # with b (top 1.0, correct) also in the 0.9 bin: bin 6 has 1 item (gap 0.4), bin 9 has 2 (mean conf 0.95, accuracy 0.5)
    assert DS.ece([a, b, c, d])["value"] == pytest.approx(0.4 / 3 + 0.45 * 2 / 3)
    assert DS.ece([a, d])["value"] == pytest.approx(0.65)
    assert DS.ece([a, d])["bins"][6] == {"lo": 0.6, "n": 1, "confidence": 0.6, "accuracy": 1.0}
    assert DS.ece([c])["value"] is None


def test_ece_ties_go_to_the_first_option_in_sorted_order():
    tie = _item("EQUIV", "EQUIV", probs={"EQUIV": 0.5, "IMPLIES": 0.5, "IMPLIED_BY": 0.0, "EXCLUDES": 0.0, "NONE": 0.0})
    assert DS.ece([tie])["bins"][5]["accuracy"] == 1.0  # EQUIV is first in sorted order, so the top label is EQUIV: correct
    assert DS.ece([tie])["value"] == pytest.approx(0.5)  # |1 - 0.5|


def test_per_stratum_and_seen_vs_held_out_reports():
    items = [_item("IMPLIES", "IMPLIES", stratum="real"), _item("NONE", "IMPLIES", stratum="decoy", held_out=True),
             _item("NONE", "NONE", stratum="decoy")]
    rep = DS.op_report(items)
    assert sorted(rep["by_stratum"]) == ["decoy", "real"]
    assert rep["by_stratum"]["real"]["accuracy"] == 1.0 and rep["by_stratum"]["decoy"]["accuracy"] == 0.5
    assert rep["by_split"]["seen"]["n"] == 2 and rep["by_split"]["seen"]["accuracy"] == 1.0
    assert rep["by_split"]["held_out"] == DS.metrics([items[1]])


SUITE_LABELS = [
    {"qid": "r1", "op": "decide_relation", "label": "IMPLIES", "k": None, "missing": None, "held_out": False, "stratum": "real"},
    {"qid": "r2", "op": "decide_relation", "label": "EXCLUDES", "k": None, "missing": None, "held_out": True, "stratum": "decoy"},
    {"qid": "b1", "op": "decide_binding", "label": "bound", "k": None, "missing": None, "held_out": False, "stratum": "real"},
    {"qid": "g1", "op": "decide_group", "label": "at_least_k", "k": 2, "missing": None, "held_out": False, "stratum": "real"},
    {"qid": "g2", "op": "decide_group", "label": "none", "k": None, "missing": None, "held_out": False, "stratum": "decoy"},
]
SUITE_CANDIDATES = [
    {"qid": "r1", "op": "decide_relation", "a": "x", "b": "y"},
    {"qid": "r2", "op": "decide_relation", "a": "x", "b": "z"},
    {"qid": "b1", "op": "decide_binding", "id": "o1"},
    {"qid": "g1", "op": "decide_group", "members": ["o1", "o2", "o3"], "source": "curated"},
    {"qid": "g2", "op": "decide_group", "members": ["o1", "o2"], "source": "curated"},
]
PROBS_IMPLIED_BY = {"EQUIV": 0.05, "IMPLIES": 0.05, "IMPLIED_BY": 0.8, "EXCLUDES": 0.05, "NONE": 0.05}


def _suite_decisions():
    return [
        # asked (y, x): IMPLIED_BY on the reversed pair is IMPLIES on (x, y), the label's order
        {"qid": "r1", "op": "decide_relation", "a": "y", "b": "x", "decision": "IMPLIED_BY", "conf": 0.8,
         "probs": PROBS_IMPLIED_BY, "error": None},
        {"qid": "r2", "op": "decide_relation", "a": "x", "b": "z", "decision": {"relation": "EXCLUDES", "conf": 0.5},
         "conf": 0.5, "probs": None, "error": None},
        {"qid": "b1", "op": "decide_binding", "a": None, "b": None,
         "decision": {"status": "unresolved", "class": None, "negated": False, "missing": ["out_of_scope"]},
         "conf": None, "probs": {"bound": 0.3, "unresolved": 0.7}, "error": None},
        # a k of 3 against the label's 2 is a wrong class (its kind is right, so kind_accuracy counts it)
        {"qid": "g1", "op": "decide_group", "members": ["o1", "o2"], "decision": {"groups": [{"kind": "at_least_k", "k": 3,
         "members": ["o1", "o2", "o3"]}]}, "conf": None,
         "probs": {"none": 0.1, "exactly_one": 0.1, "ordered_chain": 0.1, "at_least_k": 0.5, "at_most_k": 0.1, "at_most_one": 0.1},
         "error": None},
        {"qid": "g2", "op": "decide_group", "members": ["o1", "o2"], "decision": {"groups": []}, "conf": None, "probs": None,
         "error": None},
        {"qid": "zz", "op": "decide_relation", "a": "x", "b": "y", "decision": "NONE", "conf": None, "probs": None, "error": None},
        {"qid": None, "op": "decide_binding", "decision": None, "error": "timeout"},
    ]


def _suite(tmp_path, decisions=None):
    paths = {}
    for name, rows in (("labels", SUITE_LABELS), ("candidates", SUITE_CANDIDATES),
                       ("decisions", _suite_decisions() if decisions is None else decisions)):
        paths[name] = tmp_path / f"{name}.jsonl"
        paths[name].write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return paths["labels"], paths["candidates"], paths["decisions"]


def test_load_orients_a_reversed_relation_and_maps_missing_and_wrong_k(tmp_path):
    items, counts = DS.load(*_suite(tmp_path))
    by = {it["qid"]: it for it in items}
    assert [it["qid"] for it in items] == ["r1", "r2", "b1", "g1", "g2"]  # label order
    assert by["r1"]["pred"] == "IMPLIES" and by["r1"]["flipped"] and by["r1"]["truth"] == "IMPLIES"
    assert by["r1"]["probs"] == {"EQUIV": 0.05, "IMPLIES": 0.8, "IMPLIED_BY": 0.05, "EXCLUDES": 0.05, "NONE": 0.05}
    assert by["r2"]["pred"] == "EXCLUDES" and not by["r2"]["flipped"]  # a dict decision with conf
    assert by["b1"]["pred"] == "unresolved" and by["b1"]["truth"] == "bound"
    assert by["g1"]["pred"] == "at_least_k:3" and by["g1"]["truth"] == "at_least_k:2"
    assert by["g2"]["pred"] == "none"  # groups == []
    assert by["b1"]["probs"] == {"bound": 0.3, "unresolved": 0.7}
    assert by["g1"]["probs"]["at_least_k"] == 0.5 and by["g1"]["truth"] == "at_least_k:2"
    assert counts == {"labels": 5, "decisions": 7, "decisions_without_qid": 1, "decisions_unlabelled": 1, "invalid_conf": 0,
                      "oriented": 1, "torn_final_line": 0, "duplicate_identical": 0}


def test_the_headline_is_the_relation_mcc_with_binding_secondary_and_group_diagnostic(tmp_path):
    """Owner decision 2026-10-29 (fixture version 2): no mean. The headline is the relation MCC; groups are a diagnostic."""
    out = DS.score(*DS.load(*_suite(tmp_path)), 2)
    head = out["headline"]
    assert head["metric"] == "relation_mcc"
    assert head["relation_mcc"] == pytest.approx(1.0)  # both relations correct after orientation
    # groups: (at_least_k:2 -> at_least_k:3) and (none -> none): the pairs agree on one of two items
    assert head["diagnostics"]["group_mcc"] == pytest.approx(0.5)
    assert head["secondary"]["binding_mcc"] == 0.0  # one item: a constant marginal
    assert head["diagnostics"]["reading_mcc"] is None  # no reading items in this suite
    assert set(head) == {"metric", "relation_mcc", "secondary", "diagnostics"}  # no aggregate key
    assert out["ops"]["decide_relation"]["coverage"] == 1.0 and out["ops"]["decide_group"]["coverage"] == 1.0
    assert out["ops"]["decide_group"]["per_class"]["at_least_k:3"]["predicted"] == 1
    assert out["ops"]["decide_group"]["kind_accuracy"] == 1.0  # k ignored: the wrong k is still a kind match


def test_the_version_3_headline_is_the_mean_of_relation_and_group_mcc_with_its_parts(tmp_path):
    """Owner decision 2026-10-30: from fixture version 3, the headline is (relation MCC + group MCC) / 2; the parts are kept."""
    head = DS.score(*DS.load(*_suite(tmp_path)), 3)["headline"]
    assert head == {"metric": "relation_group_mcc", "relation_group_mcc": pytest.approx(0.75),  # (1.0 + 0.5) / 2
                    "parts": {"relation_mcc": pytest.approx(1.0), "group_mcc": pytest.approx(0.5)},
                    "secondary": {"binding_mcc": 0.0}, "diagnostics": {"reading_mcc": None}}


def test_the_headline_rule_is_chosen_by_version_and_is_null_when_a_part_is_missing(tmp_path):
    mcc = {"decide_relation": 0.8, "decide_group": 0.4, "decide_binding": 0.3, "decide_reading": None}
    for version in (None, 1, 2):  # the relation MCC alone, groups a diagnostic
        head = DS.headline_for(version, mcc)
        assert head["metric"] == "relation_mcc" and head["relation_mcc"] == 0.8 and "relation_group_mcc" not in head
        assert head["diagnostics"] == {"group_mcc": 0.4, "reading_mcc": None}
    assert DS.headline_for(3, mcc)["relation_group_mcc"] == pytest.approx(0.6)
    no_groups = DS.headline_for(3, dict(mcc, decide_group=None))  # a run without group items
    assert no_groups["relation_group_mcc"] is None and no_groups["parts"] == {"relation_mcc": 0.8, "group_mcc": None}
    assert DS.headline_for(3, dict(mcc, decide_relation=None))["relation_group_mcc"] is None
    assert DS.headline_for(2, dict(mcc, decide_relation=None))["relation_mcc"] is None  # v2 is null the same way
    rel = [it for it in DS.load(*_suite(tmp_path))[0] if it["op"] == "decide_relation"]
    assert DS.score(rel, {}, 3)["headline"]["relation_group_mcc"] is None  # no group items: the mean is null, not the relation MCC


def _fixture_json(tmp_path, doc):
    (tmp_path / "fixture.json").write_text(json.dumps(doc), encoding="utf-8")


def test_the_fixture_version_is_the_major_of_fixture_json_and_anything_else_is_refused(tmp_path):
    for text, major in (("1", 1), ("2", 2), ("2.9", 2), ("3", 3), ("3.1", 3)):
        _fixture_json(tmp_path, {"name": "mentat_hard_test", "version": text})
        assert DS.fixture_version(tmp_path) == major
    _fixture_json(tmp_path, {"name": "toy_native"})  # no version (the toy fixtures): the legacy headline, not an error
    assert DS.fixture_version(tmp_path) is None
    assert not DS.relation_group(None) and not DS.relation_group(2) and DS.relation_group(3) and DS.relation_group(4)
    for bad in ("v3", "3.", "3.x", "\u00b3", "", " 3", "3.1.", "3-1", "3 "):
        _fixture_json(tmp_path, {"version": bad})
        with pytest.raises(ValueError):
            DS.fixture_version(tmp_path)
    for bad in (3, None, ["3"]):  # a number, null or a list is not a version string
        _fixture_json(tmp_path, {"version": bad})
        with pytest.raises(ValueError):
            DS.fixture_version(tmp_path)
    (tmp_path / "fixture.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        DS.fixture_version(tmp_path)
    with pytest.raises(OSError):  # no fixture.json at all
        DS.fixture_version(tmp_path / "missing")


def test_a_version_3_1_fixture_gets_the_version_3_headline_and_no_version_the_legacy_one(tmp_path):
    mcc = {"decide_relation": 0.8, "decide_group": 0.4, "decide_binding": 0.3, "decide_reading": None}
    _fixture_json(tmp_path, {"version": "3.1"})
    assert DS.headline_for(DS.fixture_version(tmp_path), mcc)["metric"] == "relation_group_mcc"
    _fixture_json(tmp_path, {"version": "2.9"})
    assert DS.headline_for(DS.fixture_version(tmp_path), mcc)["metric"] == "relation_mcc"
    assert DS.headline_for(None, mcc)["metric"] == "relation_mcc"


def test_an_error_row_is_missing_even_when_it_carries_a_decision(tmp_path):
    rows = _suite_decisions()
    rows[0]["error"] = "malformed"
    items, _ = DS.load(*_suite(tmp_path, rows))
    assert [it["pred"] for it in items if it["qid"] == "r1"] == [MISSING]


def test_probs_are_validated_and_orientation_errors_raise(tmp_path):
    rows = _suite_decisions()
    rows[0]["probs"] = {"EQUIV": 0.5, "IMPLIES": 0.5, "IMPLIED_BY": 0.5, "EXCLUDES": 0.0, "NONE": 0.0}
    with pytest.raises(ValueError, match="sum"):
        DS.load(*_suite(tmp_path, rows))
    rows = _suite_decisions()
    rows[0]["a"], rows[0]["b"] = "x", "w"  # neither order of (x, y)
    with pytest.raises(ValueError, match="neither order"):
        DS.load(*_suite(tmp_path, rows))
    conflicting = dict(_suite_decisions()[1], decision="IMPLIES")  # a second, different row for r2
    with pytest.raises(ValueError, match="conflicting decisions for qid 'r2'"):
        DS.load(*_suite(tmp_path, _suite_decisions() + [conflicting]))


def test_an_unanswered_row_has_no_probs_even_when_it_carries_some(tmp_path):
    rows = _suite_decisions()
    rows[0]["error"] = "malformed"
    rows[0]["probs"] = {"EQUIV": 0.5, "IMPLIES": 0.5, "IMPLIED_BY": 0.5, "EXCLUDES": 0.0, "NONE": 0.0}  # bad sum: not read
    items, _ = DS.load(*_suite(tmp_path, rows))
    r1 = next(it for it in items if it["qid"] == "r1")
    assert r1["pred"] == MISSING and r1["probs"] is None


def test_conf_falls_back_to_the_decision_and_a_non_finite_conf_is_missing(tmp_path):
    rows = _suite_decisions()
    rows[0]["conf"] = float("nan")  # JSON NaN: not a finite number
    rows[1]["conf"] = None  # the decision {"relation": "EXCLUDES", "conf": 0.5} carries it
    items, counts = DS.load(*_suite(tmp_path, rows))
    by = {it["qid"]: it for it in items}
    assert by["r1"]["pred"] == MISSING and by["r1"]["invalid_conf"] and by["r1"]["probs"] is None and by["r1"]["conf"] is None
    assert by["r2"]["conf"] == 0.5 and by["r2"]["pred"] == "EXCLUDES"
    assert counts["invalid_conf"] == 1


def test_identical_duplicates_are_dropped_and_a_torn_final_line_is_ignored(tmp_path):
    labels, candidates, decisions = _suite(tmp_path, _suite_decisions() + [dict(_suite_decisions()[0])])
    with decisions.open("a", encoding="utf-8") as fh:
        fh.write('{"qid": "r2", "op": "decide_rel')  # a torn append: no trailing newline, not JSON
    items, counts = DS.load(labels, candidates, decisions)
    assert counts["duplicate_identical"] == 1 and counts["torn_final_line"] == 1
    assert counts["decisions"] == 7 and len(items) == 5
    assert next(it for it in items if it["qid"] == "r2")["pred"] == "EXCLUDES"


def test_a_bad_line_before_the_last_one_is_an_error(tmp_path):
    labels, candidates, decisions = _suite(tmp_path)
    decisions.write_text("not json\n" + decisions.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="line 1 is not JSON"):
        DS.load(labels, candidates, decisions)


def test_a_predicted_at_most_k_with_k_one_is_at_most_one():
    at_most = [{"kind": "at_most_k", "k": 1, "members": ["o1", "o2"]}]
    assert DS._predict("decide_group", {"groups": at_most}) == "at_most_one"
    assert DS._predict("decide_group", {"groups": [{"kind": "at_most_k", "k": 2}]}) == "at_most_k:2"
    assert DS._predict("decide_group", {"groups": [{"kind": "at_most_one", "k": None}]}) == "at_most_one"


def _chain_item(qid, order, decision):
    label = {"op": "decide_group", "label": "ordered_chain", "k": None, "held_out": False, "stratum": "real", "order": order}
    row = {"qid": qid, "op": "decide_group", "decision": decision, "conf": None, "probs": None, "error": None}
    return DS._item(qid, label, {}, row)


def _chain(members, direction=None):
    group = {"kind": "ordered_chain", "members": members}
    if direction is not None:
        group["direction"] = direction
    return {"groups": [group]}


def test_the_implied_order_of_a_chain_follows_its_direction():
    assert DS._implied_chain(_chain(["x", "y", "z"])) == ["x", "y", "z"]  # forward by default
    assert DS._implied_chain(_chain(["x", "y", "z"], "forward")) == ["x", "y", "z"]
    assert DS._implied_chain(_chain(["z", "y", "x"], "reverse")) == ["x", "y", "z"]  # each link runs member i+1 => i
    assert DS._implied_chain({"groups": []}) is None and DS._implied_chain({"groups": [{"kind": "ordered_chain"}]}) is None


def test_chain_order_counts_labelled_chains_by_their_implied_order():
    xyz = ["x", "y", "z"]
    items = [
        _chain_item("forward", xyz, _chain(["x", "y", "z"], "forward")),  # correct
        _chain_item("reverse", xyz, _chain(["z", "y", "x"], "reverse")),  # correct: the same implied order
        _chain_item("wrong", xyz, _chain(["z", "y", "x"], "forward")),  # ordered_chain, implied z => y => x: wrong
        _chain_item("kind", xyz, {"groups": [{"kind": "at_least_k", "k": 2, "members": ["x", "y", "z"]}]}),  # not a chain
        _chain_item("unordered", None, _chain(["x", "y"])),  # no label order: not counted
    ]
    assert DS.chain_order(items) == {"n": 4, "correct": 2, "accuracy": 0.5}
    assert DS.chain_order([]) == {"n": 0, "correct": 0, "accuracy": None}
    out = DS.score(items, {}, 2)
    assert out["ops"]["decide_group"]["chain_order"] == {"n": 4, "correct": 2, "accuracy": 0.5}


def test_a_group_decision_with_no_groups_is_none_and_no_decision_is_missing(tmp_path):
    assert DS._predict("decide_group", {"groups": []}) == "none" and DS._predict("decide_group", []) == "none"
    rows = [r for r in _suite_decisions() if r.get("qid") != "g2"]
    items, counts = DS.load(*_suite(tmp_path, rows))
    assert next(it for it in items if it["qid"] == "g2")["pred"] == MISSING
    assert DS.score(items, counts, 2)["ops"]["decide_group"]["coverage"] == 0.5


def test_group_and_binding_probs_are_over_kinds_and_statuses(tmp_path):
    items, _ = DS.load(*_suite(tmp_path))
    g1 = next(it for it in items if it["qid"] == "g1")
    b1 = next(it for it in items if it["qid"] == "b1")
    # g1 option is the kind at_least_k (k is not scored): (0.5 - 1)^2 + 5 * 0.1^2 = 0.30; top kind at_least_k is correct
    assert DS.brier([g1]) == {"value": pytest.approx(0.30), "n": 1, "excluded": 0}
    assert DS.ece([g1])["value"] == pytest.approx(0.5)
    # b1 option is bound: (0.3 - 1)^2 + 0.7^2 = 0.98; top unresolved (0.7) is wrong
    assert DS.brier([b1])["value"] == pytest.approx(0.98)
    assert DS.ece([b1])["value"] == pytest.approx(0.7)


def test_an_op_without_items_is_null_in_the_headline(tmp_path):
    items, counts = DS.load(*_suite(tmp_path))
    rel = [it for it in items if it["op"] == "decide_relation"]
    out = DS.score(rel, counts, 2)
    assert out["headline"]["relation_mcc"] == pytest.approx(1.0)
    assert out["ops"]["decide_group"]["mcc"] is None and out["headline"]["diagnostics"]["group_mcc"] is None
    assert out["headline"]["secondary"]["binding_mcc"] is None


def test_cli_writes_deterministic_sorted_json(tmp_path, capsys):
    labels, candidates, decisions = _suite(tmp_path)
    (tmp_path / "fixture.json").write_text(json.dumps({"version": "2"}), encoding="utf-8")
    out = tmp_path / "score_decisions.json"
    argv = ["--decisions", str(decisions), "--labels", str(labels), "--candidates", str(candidates), "--fixture", str(tmp_path),
            "--out", str(out)]
    assert DS.main(argv) == 0
    first = out.read_bytes()
    assert DS.main(argv) == 0
    assert out.read_bytes() == first
    data = json.loads(first)
    assert list(data) == sorted(data) and data["counts"]["oriented"] == 1
    assert "headline relation_mcc 1.0000; secondary binding MCC 0.0000" in capsys.readouterr().out

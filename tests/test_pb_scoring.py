"""PB1-PB3: lenient answer indexes, answer and gold validity in scoring, gold-informed baselines.
The spec section 8 formulas are checked against an independent reference on random cases."""

import random

import pytest

from mentat_bench import ir, scoring
from mentat_bench.scoring import BaselineAnswers, closure_utility, query_utility, score_run, selection_utility

GOLD = {"ENTAILED": ["a"], "REFUTED": ["b"], "CONTINGENT": ["c", "d"], "UNRESOLVED": []}


def _labels(**kw):
    return {"labels": {**{label: [] for label in scoring.LABELS}, **kw}}


def _ref_closure(pred, gold):
    scores = []
    for label in scoring.LABELS:
        p, g = pred[label], gold[label]
        if not p and not g:
            continue
        tp, fp, fn = len(p & g), len(p - g), len(g - p)
        scores.append(1.0 if 2 * tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(scores) / len(scores) if scores else 1.0


def test_closure_formula_matches_independent_reference_on_random_partitions():
    rng = random.Random(7)
    for _ in range(5000):
        universe = [f"o{i}" for i in range(rng.randint(0, 8))]
        gold = {label: [] for label in scoring.LABELS}
        pred = {label: [] for label in scoring.LABELS}
        for oid in universe:
            gold[rng.choice(scoring.LABELS)].append(oid)
            pred[rng.choice(scoring.LABELS)].append(oid)
        expected = _ref_closure({k: set(v) for k, v in pred.items()}, {k: set(v) for k, v in gold.items()})
        assert closure_utility({"labels": pred}, gold) == pytest.approx(expected, abs=1e-12)


def test_selection_formula_matches_independent_reference_on_random_sets():
    rng = random.Random(11)
    universe = [f"o{i}" for i in range(6)]
    for _ in range(3000):
        gold = rng.sample(universe, rng.randint(0, 6))
        pred = rng.sample(universe, rng.randint(0, 6))
        tp, fp, fn = len(set(gold) & set(pred)), len(set(pred) - set(gold)), len(set(gold) - set(pred))
        expected = 1.0 if tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)
        assert selection_utility({"ids": pred}, gold) == pytest.approx(expected, abs=1e-12)


# PB1: lenient streaming of answer files


def test_jsonl_index_skips_malformed_lines_and_warns(tmp_path):
    p = tmp_path / "answers.jsonl"
    p.write_bytes(
        b'{"qid": "ok1", "ids": []}\n'
        b"not json\n"
        b"[1, 2]\n"
        b'{"ids": []}\n'
        b'{"qid": ["list"], "ids": []}\n'
        b'{"qid": "bad\xff", "ids": []}\n'
        b'{"qid": "ok2", "ids": ["x"]}\n'
    )
    with pytest.warns(UserWarning, match="skipped 5"):
        idx = ir.JsonlIndex(p)
    assert len(idx) == 2 and "ok1" in idx and "ok2" in idx
    assert idx["ok2"]["ids"] == ["x"]
    assert idx.skipped == 5
    idx.close()


def test_duplicated_answer_is_absent_and_scores_zero(tmp_path):
    ir.write_jsonl(tmp_path / "q.jsonl", [{"qid": "s1", "family": "selection"}])
    ir.write_jsonl(tmp_path / "g.jsonl", [{"qid": "s1", "ids": ["x"]}])
    (tmp_path / "a.jsonl").write_text('{"qid": "s1", "ids": ["x"]}\n{"qid": "s1", "ids": []}\n', encoding="utf-8")
    gold = ir.JsonlIndex(tmp_path / "g.jsonl")
    with pytest.warns(UserWarning, match="duplicated"):
        answers = ir.JsonlIndex(tmp_path / "a.jsonl")
    report = score_run(ir.read_jsonl(tmp_path / "q.jsonl"), answers, gold, {})
    assert report["per_query"] == {"s1": 0.0}  # neither copy is used: no last-wins
    with pytest.raises(ValueError, match="more than once"):
        answers["s1"]
    gold.close()
    answers.close()


def test_duplicate_gold_is_a_broken_fixture(tmp_path):
    ir.write_jsonl(tmp_path / "q.jsonl", [{"qid": "s1", "family": "selection"}])
    (tmp_path / "g.jsonl").write_text('{"qid": "s1", "ids": []}\n{"qid": "s1", "ids": []}\n', encoding="utf-8")
    with pytest.warns(UserWarning):
        gold = ir.JsonlIndex(tmp_path / "g.jsonl")
    with pytest.raises(ValueError, match="s1"):
        score_run([{"qid": "s1", "family": "selection"}], {}, gold, {})


def test_gold_error_shape_raises_naming_qid_and_family():
    cases = [
        ({"qid": "c9", "family": "closure"}, {"qid": "c9", "labels": None, "error": "hyp_inconsistent"}),
        ({"qid": "s9", "family": "selection"}, {"qid": "s9", "error": "timeout"}),
        ({"qid": "f9", "family": "feasibility"}, {"qid": "f9"}),
    ]
    for query, gold in cases:
        with pytest.raises(ValueError) as info:
            query_utility(query, {"qid": query["qid"]}, gold, False)
        assert query["qid"] in str(info.value) and query["family"] in str(info.value)


def test_duplicate_query_qid_is_an_invalid_workload():
    queries = [{"qid": "s1", "family": "selection"}, {"qid": "s1", "family": "selection"}]
    with pytest.raises(ValueError, match="duplicate query qid"):
        score_run(queries, {"s1": {"ids": []}}, {"s1": {"ids": []}}, {})
    with pytest.raises(ValueError, match="duplicate query qid"):
        BaselineAnswers("all-contingent", queries, {"s1": {"ids": []}})


# PB2: validity of answers


def test_missing_codes_must_be_unique_nonempty_strings_within_the_cap():
    gold = {"decision": "UNRESOLVED", "missing": ["no_date"]}
    q = {"qid": "f", "family": "feasibility"}
    good = {"decision": "UNRESOLVED", "missing": ["no_date"]}
    assert query_utility(q, good, gold, False) == 1.0
    bad_values = [
        "no_date", 0, False, "", ["", "no_date"], ["no_date", "no_date"], [1], [None],
        ["no_date"] + [f"c{i}" for i in range(8)],  # nine codes: over the cap of 8
    ]
    for value in bad_values:
        assert query_utility(q, {"decision": "UNRESOLVED", "missing": value}, gold, True) == 0.0, value
    # absent or null means [], which does not match a non-empty gold set
    assert query_utility(q, {"decision": "UNRESOLVED"}, gold, True) == 0.0
    assert query_utility(q, {"decision": "UNRESOLVED", "missing": None}, gold, True) == 0.0
    empty_gold = {"decision": "UNRESOLVED", "missing": []}
    assert query_utility(q, {"decision": "UNRESOLVED"}, empty_gold, False) == 1.0
    assert query_utility(q, {"decision": "UNRESOLVED", "missing": ["x"]}, empty_gold, False) == 0.0


def test_answers_with_a_truthy_error_score_zero_even_when_the_body_is_valid():
    assert selection_utility({"ids": ["a"], "error": "timeout"}, ["a"]) == 0.0
    assert query_utility({"qid": "f", "family": "partition"}, {"decision": "true", "error": "timeout"}, {"decision": "true"}, True) == 0.0
    closure = _labels(ENTAILED=["a"], REFUTED=["b"], CONTINGENT=["c", "d"])
    assert closure_utility({**closure, "error": "timeout"}, GOLD) == 0.0
    assert closure_utility({**closure, "error": ""}, GOLD) == 1.0  # a falsy error is not an error


def test_false_assertions_count_only_valid_closure_answers_inside_the_universe():
    queries = [{"qid": "c1", "family": "closure"}]
    gold = {"c1": {"qid": "c1", "labels": GOLD}}
    valid = _labels(ENTAILED=["a", "c"], REFUTED=["b"], CONTINGENT=["d"])
    assert score_run(queries, {"c1": {"qid": "c1", **valid}}, gold, {})["false_entailment"] == 1
    uncovered = _labels(ENTAILED=["a", "c"], REFUTED=["b"], CONTINGENT=[])  # d missing: invalid answer
    assert score_run(queries, {"c1": {"qid": "c1", **uncovered}}, gold, {})["false_entailment"] == 0
    errored = {"qid": "c1", **valid, "error": "timeout"}
    assert score_run(queries, {"c1": errored}, gold, {})["false_entailment"] == 0
    outside = _labels(ENTAILED=["a", "c", "zz"], REFUTED=["b"], CONTINGENT=["d"])  # zz is not in the universe
    assert score_run(queries, {"c1": {"qid": "c1", **outside}}, gold, {})["false_entailment"] == 0


# PB3: baselines


def test_baselines_are_gold_informed_only_where_declared_and_certificate_free_decisions_score_zero():
    queries = [{"qid": "f1", "family": "feasibility"}, {"qid": "f2", "family": "feasibility"}, {"qid": "p1", "family": "partition"}]
    gold = {
        "f1": {"qid": "f1", "decision": "UNRESOLVED", "missing": ["no_date", "no_date_x"]},
        "f2": {"qid": "f2", "decision": "UNRESOLVED", "missing": ["no_date"]},
        "p1": {"qid": "p1", "decision": "false"},
    }
    unresolved = BaselineAnswers("all-unresolved", queries, gold)
    assert unresolved.gold_informed is True
    assert unresolved.get("f1")["missing"] == ["no_date"]  # most frequent gold code of the family
    assert scoring.baseline_answers("all-contingent", queries, gold)["f1"]["decision"] == "SAT"
    assert BaselineAnswers("all-contingent", queries, gold).gold_informed is False
    report = score_run(queries, BaselineAnswers("all-contingent", queries, gold), gold, {})
    assert report["families"]["feasibility"]["mean_utility"] == 0.0  # SAT/false need a certificate: the floor
    assert report["families"]["partition"]["mean_utility"] == 0.0
    report = score_run(queries, unresolved, gold, {})
    assert report["per_query"] == {"f1": 1.0, "f2": 1.0, "p1": 0.0}  # "no_date" is one of f1's gold codes

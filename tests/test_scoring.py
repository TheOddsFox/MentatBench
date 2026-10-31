import json
import math

import pytest

from mentat_bench import scoring
from mentat_bench.scoring import (
    baseline_answers,
    closure_utility,
    f1,
    feasibility_utility,
    partition_utility,
    q_n,
    score_run,
    selection_utility,
)

GOLD_LABELS = {
    "ENTAILED": ["a"],
    "REFUTED": ["b"],
    "CONTINGENT": ["c", "d"],
    "UNRESOLVED": [],
}


def labels(**kw):
    out = {k: [] for k in scoring.LABELS}
    out.update(kw)
    return {"qid": "q", "labels": out}


def test_f1_formula_and_empty_denominator():
    assert f1(0, 0, 0) == 1.0
    assert f1(1, 1, 1) == pytest.approx(0.5)
    assert f1(0, 3, 0) == 0.0


def test_closure_perfect_scores_one_and_absent_classes_are_omitted():
    # UNRESOLVED is absent from both gold and prediction, so it must not drag the macro average.
    assert closure_utility(labels(ENTAILED=["a"], REFUTED=["b"], CONTINGENT=["c", "d"]), GOLD_LABELS) == 1.0


def test_closure_predicted_only_class_scores_zero():
    pred = labels(ENTAILED=["a"], REFUTED=["b"], CONTINGENT=["c"], UNRESOLVED=["d"])
    # UNRESOLVED is predicted-only (gold has no UNRESOLVED) and scores 0; CONTINGENT has tp=1, fn=1 -> 2/3.
    expected = (1.0 + 1.0 + (2 / 3) + 0.0) / 4
    assert closure_utility(pred, GOLD_LABELS) == pytest.approx(expected)


def test_closure_malformed_answers_score_zero():
    assert closure_utility(None, GOLD_LABELS) == 0.0
    assert closure_utility({"qid": "q", "labels": None, "error": "hyp_inconsistent"}, GOLD_LABELS) == 0.0
    missing_key = {"qid": "q", "labels": {"ENTAILED": ["a"]}}
    assert closure_utility(missing_key, GOLD_LABELS) == 0.0
    duplicated = labels(ENTAILED=["a"], REFUTED=["b"], CONTINGENT=["c", "c"])
    assert closure_utility(duplicated, GOLD_LABELS) == 0.0
    uncovered = labels(ENTAILED=["a"], REFUTED=["b"], CONTINGENT=["c"])
    assert closure_utility(uncovered, GOLD_LABELS) == 0.0
    not_strings = labels(ENTAILED=[1], REFUTED=["b"], CONTINGENT=["c", "d"])
    assert closure_utility(not_strings, GOLD_LABELS) == 0.0


def test_selection_correctly_empty_is_one_and_empty_when_gold_nonempty_is_zero():
    assert selection_utility({"qid": "q", "ids": []}, []) == 1.0
    assert selection_utility({"qid": "q", "ids": []}, ["x"]) == 0.0
    assert selection_utility({"qid": "q", "ids": ["x"]}, []) == 0.0


def test_selection_set_f1_and_malformed():
    assert selection_utility({"qid": "q", "ids": ["a", "b"]}, ["a", "c"]) == pytest.approx(0.5)
    assert selection_utility({"qid": "q", "ids": ["a", "a"]}, ["a"]) == 0.0
    assert selection_utility({"qid": "q", "ids": "a"}, ["a"]) == 0.0
    assert selection_utility(None, ["a"]) == 0.0


def test_feasibility_requires_decision_and_valid_certificate():
    gold = {"decision": "UNSAT", "core": [0, 1]}
    assert feasibility_utility({"decision": "UNSAT", "core": [0]}, gold, cert_valid=True) == 1.0
    assert feasibility_utility({"decision": "UNSAT", "core": [0]}, gold, cert_valid=False) == 0.0
    assert feasibility_utility({"decision": "SAT"}, gold, cert_valid=True) == 0.0
    assert feasibility_utility({"decision": "bogus"}, gold, cert_valid=True) == 0.0
    assert feasibility_utility(None, gold, cert_valid=True) == 0.0


def test_unresolved_needs_gold_accepted_missing_code():
    gold = {"decision": "UNRESOLVED", "missing": ["no_date"]}
    assert feasibility_utility({"decision": "UNRESOLVED", "missing": ["no_date"]}, gold, cert_valid=False) == 1.0
    assert feasibility_utility({"decision": "UNRESOLVED", "missing": ["no_source"]}, gold, cert_valid=True) == 0.0
    assert feasibility_utility({"decision": "UNRESOLVED", "missing": []}, gold, cert_valid=True) == 0.0
    assert feasibility_utility({"decision": "UNRESOLVED", "missing": "no_date"}, gold, cert_valid=True) == 0.0


def test_partition_decisions():
    gold = {"decision": "true"}
    assert partition_utility({"decision": "true"}, gold, cert_valid=True) == 1.0
    assert partition_utility({"decision": "true"}, gold, cert_valid=False) == 0.0
    assert partition_utility({"decision": "false"}, gold, cert_valid=True) == 0.0
    assert partition_utility({"decision": "SAT"}, gold, cert_valid=True) == 0.0


def test_q_n_arithmetic():
    means = {"closure": 1.0, "selection": 0.5, "feasibility": 0.0, "partition": 0.5}
    assert q_n(means) == pytest.approx(50.0)
    assert q_n({f: 1.0 for f in scoring.FAMILIES}) == pytest.approx(100.0)


def test_score_run_absent_answer_scores_zero_and_aggregates():
    queries = [
        {"qid": "s1", "family": "selection"},
        {"qid": "s2", "family": "selection"},
        {"qid": "f1", "family": "feasibility"},
        {"qid": "p1", "family": "partition"},
        {"qid": "c1", "family": "closure"},
    ]
    gold = {
        "s1": {"qid": "s1", "ids": ["x"]},
        "s2": {"qid": "s2", "ids": []},
        "f1": {"qid": "f1", "decision": "SAT"},
        "p1": {"qid": "p1", "decision": "false"},
        "c1": {"qid": "c1", "labels": GOLD_LABELS},
    }
    answers = {
        "s1": {"qid": "s1", "ids": ["x"]},
        # s2 absent: scores 0 even though the gold set is empty
        "f1": {"qid": "f1", "decision": "SAT"},
        "p1": {"qid": "p1", "decision": "true"},
        "c1": {"qid": "c1", "labels": {**labels()["labels"], "ENTAILED": ["a"], "REFUTED": ["b"], "CONTINGENT": ["c", "d"]}},
    }
    verdicts = {"f1": True}
    report = score_run(queries, answers, gold, verdicts)
    assert report["families"]["selection"]["mean_utility"] == pytest.approx(0.5)
    assert report["families"]["feasibility"]["mean_utility"] == 1.0
    assert report["families"]["partition"]["mean_utility"] == 0.0
    assert report["families"]["closure"]["mean_utility"] == 1.0
    assert report["Q_N"] == pytest.approx(100 * (0.5 + 1.0 + 0.0 + 1.0) / 4)
    assert report["exact_query_success"] == pytest.approx(3 / 5)
    assert report["per_query"]["s2"] == 0.0


def test_false_entailment_counts():
    queries = [{"qid": "c1", "family": "closure"}]
    gold = {"c1": {"qid": "c1", "labels": GOLD_LABELS}}
    answers = {"c1": labels(ENTAILED=["a", "c"], REFUTED=["b"], CONTINGENT=["d"])}
    report = score_run(queries, answers, gold, {})
    assert report["false_entailment"] == 1
    assert report["false_refutation"] == 0


def test_baselines_produce_expected_shapes():
    queries = [
        {"qid": "c1", "family": "closure"},
        {"qid": "s1", "family": "selection"},
        {"qid": "f1", "family": "feasibility"},
        {"qid": "p1", "family": "partition"},
    ]
    gold = {
        "c1": {"qid": "c1", "labels": GOLD_LABELS},
        "s1": {"qid": "s1", "ids": ["x"]},
        "f1": {"qid": "f1", "decision": "UNRESOLVED", "missing": ["no_date"]},
        "p1": {"qid": "p1", "decision": "false"},
    }
    contingent = baseline_answers("all-contingent", queries, gold)
    assert contingent["c1"]["labels"]["CONTINGENT"] == ["a", "b", "c", "d"]
    assert contingent["f1"]["decision"] == "SAT"
    unresolved = baseline_answers("all-unresolved", queries, gold)
    assert unresolved["c1"]["labels"]["UNRESOLVED"] == ["a", "b", "c", "d"]
    assert unresolved["f1"]["missing"] == ["no_date"]  # gold-informed: the family's most frequent gold code
    constant = baseline_answers("constant-answer", queries, gold)
    assert constant["f1"]["decision"] == "UNRESOLVED" and constant["f1"]["missing"] == ["no_date"]
    assert constant["p1"]["decision"] == "false" and constant["p1"]["missing"] == []
    report = score_run(queries, unresolved, gold, {})
    assert report["families"]["closure"]["mean_utility"] < 1.0
    assert report["families"]["feasibility"]["mean_utility"] == 1.0  # the code is accepted; no certificate needed
    assert score_run(queries, contingent, gold, {})["families"]["feasibility"]["mean_utility"] == 0.0  # certificate-free SAT floor
    with pytest.raises(ValueError):
        baseline_answers("nope", queries, gold)


def test_nan_free_mean_for_empty_family():
    report = score_run([{"qid": "s1", "family": "selection"}], {}, {"s1": {"qid": "s1", "ids": []}}, {})
    assert not math.isnan(report["Q_N"])
    assert report["families"]["closure"]["queries"] == 0


def test_score_cli_baseline_and_answer_files(tmp_path, capsys):
    from mentat_bench import cli, ir

    queries = [{"qid": "c1", "family": "closure"}, {"qid": "s1", "family": "selection"}]
    gold = [{"qid": "c1", "labels": GOLD_LABELS}, {"qid": "s1", "ids": ["x"]}]
    ir.write_jsonl(tmp_path / "q.jsonl", queries)
    ir.write_jsonl(tmp_path / "g.jsonl", gold)
    assert cli.main(["score", "--queries", str(tmp_path / "q.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl"),
                     "--baseline", "all-contingent"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["baseline"] == "all-contingent" and report["families"]["selection"]["mean_utility"] == 0.0

    ir.write_jsonl(tmp_path / "a.jsonl", [{"qid": "s1", "ids": ["x"]}])
    ir.write_jsonl(tmp_path / "v.jsonl", [{"qid": "s1", "cert_valid": True, "reason": "ok"}])
    assert cli.main(["score", "--queries", str(tmp_path / "q.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl"),
                     "--answers", str(tmp_path / "a.jsonl"), "--verdicts", str(tmp_path / "v.jsonl")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["per_query"] == {"c1": 0.0, "s1": 1.0}
    assert cli.main(["score", "--queries", str(tmp_path / "q.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl")]) == cli.EXIT_USAGE


def test_families_missing_and_gold_informed_flag(tmp_path, capsys):
    from mentat_bench import cli, ir

    queries = [{"qid": "s1", "family": "selection"}]
    ir.write_jsonl(tmp_path / "q.jsonl", queries)
    ir.write_jsonl(tmp_path / "g.jsonl", [{"qid": "s1", "ids": []}])
    base = ["score", "--queries", str(tmp_path / "q.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl")]
    assert cli.main(base + ["--baseline", "constant-answer"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["families_missing"] == ["closure", "feasibility", "partition"]
    assert report["baseline_gold_informed"] is True
    assert report["Q_N"] == pytest.approx(100 * 1.0 / 4)  # absent families contribute 0
    # cli.py must set the flag from scoring.GOLD_INFORMED_BASELINES (all-unresolved is gold-informed too).
    assert cli.main(base + ["--baseline", "all-unresolved"]) == 0
    assert json.loads(capsys.readouterr().out)["baseline_gold_informed"] is True


def test_jsonl_index_gives_random_access_without_loading_rows(tmp_path):
    from mentat_bench import ir

    p = tmp_path / "a.jsonl"
    ir.write_jsonl(p, [{"qid": "b", "x": 1}, {"qid": "a", "x": 2}])
    idx = ir.JsonlIndex(p)
    assert len(idx) == 2 and "a" in idx and "z" not in idx
    assert idx["a"]["x"] == 2 and idx.get("b")["x"] == 1 and idx.get("z", 7) == 7
    with pytest.raises(KeyError):
        idx["z"]
    idx.close()


def test_unresolved_with_empty_gold_missing_matches_empty_answer_missing():
    gold = {"decision": "UNRESOLVED", "missing": []}
    assert feasibility_utility({"decision": "UNRESOLVED", "missing": []}, gold, False) == 1.0
    assert feasibility_utility({"decision": "UNRESOLVED", "missing": ["no_date"]}, gold, False) == 0.0
    # Non-empty gold missing keeps the intersection rule.
    gold = {"decision": "UNRESOLVED", "missing": ["no_date"]}
    assert feasibility_utility({"decision": "UNRESOLVED", "missing": []}, gold, False) == 0.0
    assert feasibility_utility({"decision": "UNRESOLVED", "missing": ["no_date"]}, gold, False) == 1.0


def _closure_labels(entailed, refuted, contingent=(), unresolved=()):
    return {"ENTAILED": list(entailed), "REFUTED": list(refuted), "CONTINGENT": list(contingent), "UNRESOLVED": list(unresolved)}


QSHA = "f" * 64  # the digest of the scored queries the measurement was taken on
RSHA = "e" * 64  # the digest of the registry the measurement was taken on


def _measured(design, per_row, **over):
    """A workload measurement (the shape the removed workload_exact wrote), for the given design rows, queries and registry digests."""
    out = {"N_active": sum(per_row.values()), "per_row": per_row, "design_sha256": scoring.design_digest(design),
           "queries_sha256": QSHA, "registry_sha256": RSHA}
    out.update(over)
    return out


_DESIGN = [
    {"qid": "c1", "family": "closure", "anchor": True, "stratum": "wide", "active_units": 5},
    {"qid": "c2", "family": "closure", "anchor": False, "stratum": None, "active_units": 7},
    {"qid": "s1", "family": "selection", "anchor": False, "stratum": "wide", "active_units": 0},
]


def _closure_labels(entailed, refuted, contingent=(), unresolved=()):
    return {"ENTAILED": list(entailed), "REFUTED": list(refuted), "CONTINGENT": list(contingent), "UNRESOLVED": list(unresolved)}


def _runs():
    queries = [{"qid": "c1", "family": "closure"}, {"qid": "c2", "family": "closure"}, {"qid": "s1", "family": "selection"}]
    gold = {
        "c1": {"qid": "c1", "labels": _closure_labels(["a", "x"], ["b"], ["c"])},  # decisive: a, x, b
        "c2": {"qid": "c2", "labels": _closure_labels(["p"], ["q"], unresolved=["r"])},  # decisive: p, q
        "s1": {"qid": "s1", "ids": ["x"]},
    }
    answers = {
        "c1": {"qid": "c1", "labels": _closure_labels(["a"], ["b", "x"], ["c"])},  # a and b correct, x wrong
        "c2": {"qid": "c2", "error": "engine failed"},  # an invalid answer is correct on none
        "s1": {"qid": "s1", "ids": ["x"]},
    }
    return queries, answers, gold


def test_additive_keys_decisive_accuracy_strata_and_measured_n_active():
    queries, answers, gold = _runs()
    measured = _measured(_DESIGN, {"c1": 4, "c2": 6, "s1": 0})
    report = score_run(queries, answers, gold, {}, design=_DESIGN, measured=measured, queries_sha256=QSHA, registry_sha256=RSHA)
    assert report["N_active"] == 10  # the measured count, not the design's 12
    assert report["closure_decisive_accuracy"] == pytest.approx(2 / 5)
    assert report["strata"]["wide"]["queries"] == 2
    assert report["strata"]["wide"]["mean_utility"] == pytest.approx(
        (report["per_query"]["c1"] + report["per_query"]["s1"]) / 2)
    assert report["anchor"]["queries"] == 1 and report["active"]["queries"] == 2  # existing blocks still split by anchor

    # existing keys do not change: the same run without a design gives the same Q_N and per-query utilities
    bare = score_run(queries, answers, gold, {})
    assert bare["Q_N"] == report["Q_N"]
    assert bare["per_query"] == report["per_query"]
    assert bare["N_active"] is None and bare["strata"] == {}
    assert bare["closure_decisive_accuracy"] == pytest.approx(2 / 5)  # decisive accuracy needs no design


def test_n_active_is_omitted_without_a_measurement_and_never_the_design_sum():
    queries, answers, gold = _runs()
    report = score_run(queries, answers, gold, {}, design=_DESIGN)
    assert report["N_active"] is None  # the design declares 12 active units; without a measurement none is reported
    assert score_run(queries, answers, gold, {}, design=_DESIGN, measured=None)["N_active"] is None


def test_measured_n_active_must_match_the_design_the_queries_and_the_registry():
    queries, answers, gold = _runs()
    good = _measured(_DESIGN, {"c1": 4, "c2": 6, "s1": 0})

    def run(measured, sha=QSHA, reg=RSHA, design=_DESIGN):
        return score_run(queries, answers, gold, {}, design=design, measured=measured, queries_sha256=sha, registry_sha256=reg)

    with pytest.raises(ValueError, match="different query design"):
        run({**good, "design_sha256": "0" * 64})
    with pytest.raises(ValueError, match="exceeds"):
        run(_measured(_DESIGN, {"c1": 6, "c2": 6, "s1": 0}))
    with pytest.raises(ValueError, match="does not cover"):
        run(_measured(_DESIGN, {"c1": 4, "c2": 6}))
    with pytest.raises(ValueError, match="disagrees"):
        run({**good, "N_active": 11})
    with pytest.raises(ValueError, match="needs the design"):
        score_run(queries, answers, gold, {}, measured=good, queries_sha256=QSHA, registry_sha256=RSHA)
    with pytest.raises(ValueError, match="different queries"):
        run(good, sha="b" * 64)
    with pytest.raises(ValueError, match="needs queries_sha256"):  # required whenever a measurement is given
        score_run(queries, answers, gold, {}, design=_DESIGN, measured=good, registry_sha256=RSHA)
    with pytest.raises(ValueError, match="needs registry_sha256"):  # required whenever a measurement is given
        score_run(queries, answers, gold, {}, design=_DESIGN, measured=good, queries_sha256=QSHA)
    with pytest.raises(ValueError, match="different registry"):
        run(good, reg="d" * 64)


def test_design_without_active_units_gives_no_n_active_even_with_a_measurement():
    queries = [{"qid": "s1", "family": "selection"}]
    gold = {"s1": {"qid": "s1", "ids": ["x"]}}
    answers = {"s1": {"qid": "s1", "ids": ["x"]}}
    design = [{"qid": "s1", "anchor": False}]
    report = score_run(queries, answers, gold, {}, design=iter(design))  # a generator is read once for flags and keys
    assert report["N_active"] is None
    assert report["closure_decisive_accuracy"] is None  # no closure query
    assert report["strata"] == {}
    assert report["active"]["queries"] == 1
    measured = {"N_active": 0, "per_row": {}, "design_sha256": scoring.design_digest(design), "queries_sha256": QSHA,
                "registry_sha256": RSHA}
    assert score_run(queries, answers, gold, {}, design=design, measured=measured, queries_sha256=QSHA,
                     registry_sha256=RSHA)["N_active"] is None

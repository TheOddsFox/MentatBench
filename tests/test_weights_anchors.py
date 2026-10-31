"""Class-weighted scoring (ClassWeights, score_run(weights=...)) and anchor/active reporting (score_run(design=...)).

Hand-computed weighted F1 on a small bindings table, determinism under shuffled order and PYTHONHASHSEED, the
engine-backed toy10k fixture effect, and the anchor flag of the committed design file. The CLI opt-ins
(`score --gold-bindings`, `score --design`) are exercised end to end.
"""

import json
import os
import random
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import pytest

from mentat_bench import engine, ir, scoring
from mentat_bench.cli import main as cli_main
from mentat_bench.scoring import ClassWeights, closure_utility, score_run, selection_utility

REPO = Path(__file__).resolve().parents[1]
TOY10K = REPO / "fixtures" / "toy10k"
HAVE_ENGINE = engine.engine_available()

# Bound classes A (4 outcomes, weight 1/4), B (2 outcomes, weight 1/2); unresolved u1, u2 (weight 1).
BINDINGS = [
    {"id": "a1", "status": "bound", "class": "A", "negated": False},
    {"id": "a2", "status": "bound", "class": "A", "negated": False},
    {"id": "a3", "status": "bound", "class": "A", "negated": True},
    {"id": "a4", "status": "bound", "class": "A", "negated": True},
    {"id": "b1", "status": "bound", "class": "B", "negated": False},
    {"id": "b2", "status": "bound", "class": "B", "negated": True},
    {"id": "u1", "status": "unresolved", "class": None, "negated": False},
    {"id": "u2", "status": "unresolved", "class": None, "negated": False},
]



def _core(block):
    """A block without the uncovered and families_missing fields (checked separately)."""
    return {k: v for k, v in block.items() if k not in ("uncovered", "families_missing")}

def _weights(tmp_path, rows=BINDINGS):
    path = tmp_path / "bindings.jsonl"
    ir.write_jsonl(path, rows)
    return ClassWeights.from_bindings(path)


def _labels(**kw):
    return {"labels": {**{label: [] for label in scoring.LABELS}, **kw}}


# Weighted F1 by hand (weights: a* 1/4, b* 1/2, u* 1)

GOLD_LABELS = {"ENTAILED": ["a1", "a2"], "REFUTED": ["b1"], "CONTINGENT": ["a3", "a4", "b2", "u1"], "UNRESOLVED": ["u2"]}
PRED_LABELS = {"ENTAILED": ["a1", "a3"], "REFUTED": ["b1", "b2"], "CONTINGENT": ["a2", "a4", "u1"], "UNRESOLVED": ["u2"]}


def test_class_weights_are_one_over_class_size_and_unresolved_is_one(tmp_path):
    w = _weights(tmp_path)
    assert [w.weight(x) for x in ("a1", "a4")] == [0.25, 0.25]
    assert [w.weight(x) for x in ("b1", "b2")] == [0.5, 0.5]
    assert [w.weight(x) for x in ("u1", "u2")] == [1.0, 1.0]
    assert w.weight("not-in-bindings") == 1.0


def test_closure_weighted_macro_f1_matches_hand_computation(tmp_path):
    w = _weights(tmp_path)
    # ENTAILED: TP a1=1/4, FP a3=1/4, FN a2=1/4 -> 2(1/4)/(1/2+1/4+1/4) = 1/2
    # REFUTED: TP b1=1/2, FP b2=1/2, FN 0 -> 1/(1+1/2) = 2/3
    # CONTINGENT: TP a4+u1=5/4, FP a2=1/4, FN a3+b2=3/4 -> (5/2)/(7/2) = 5/7
    # UNRESOLVED: TP u2=1 -> 1
    expected = (Fraction(1, 2) + Fraction(2, 3) + Fraction(5, 7) + 1) / 4
    answer = _labels(**PRED_LABELS)
    assert closure_utility(answer, GOLD_LABELS, w) == pytest.approx(float(expected), abs=1e-12)
    unweighted = (Fraction(1, 2) + Fraction(2, 3) + Fraction(4, 7) + 1) / 4  # CONTINGENT: TP 2, FP 1, FN 2
    assert closure_utility(answer, GOLD_LABELS) == pytest.approx(float(unweighted), abs=1e-12)


def test_selection_weighted_f1_matches_hand_computation(tmp_path):
    w = _weights(tmp_path)
    # gold {a1, b1, u1}, pred {a1, a2, b1}: TP a1+b1 = 3/4, FP a2 = 1/4, FN u1 = 1 -> (3/2)/(11/4) = 6/11
    assert selection_utility({"ids": ["a1", "a2", "b1"]}, ["a1", "b1", "u1"], w) == pytest.approx(6 / 11, abs=1e-12)
    # unweighted: TP 2, FP 1, FN 1 -> 4/6
    assert selection_utility({"ids": ["a1", "a2", "b1"]}, ["a1", "b1", "u1"]) == pytest.approx(2 / 3)


def test_weights_none_is_the_unweighted_formula(tmp_path):
    w = _weights(tmp_path)
    gold = {"qid": "c", "labels": GOLD_LABELS}
    queries = [{"qid": "c", "family": "closure"}, {"qid": "s", "family": "selection"}]
    answers = {"c": {"qid": "c", **_labels(**PRED_LABELS)}, "s": {"qid": "s", "ids": ["a1", "a2"]}}
    gold_map = {"c": gold, "s": {"qid": "s", "ids": ["a1"]}}
    plain = score_run(queries, answers, gold_map, {})
    assert "weighted" not in plain
    weighted = score_run(queries, answers, gold_map, {}, weights=w)
    assert weighted["per_query"] == plain["per_query"]  # exact/per_query stay unweighted
    assert weighted["families"] == plain["families"]
    assert weighted["Q_N"] == plain["Q_N"]  # top-level headline unchanged
    assert weighted["weighted"]["Q_N"] != plain["Q_N"]


def test_negated_complement_members_share_their_class_weight(tmp_path):
    # Yes and No of one binary question: same class, opposite polarity, so each weighs 1/2.
    w = _weights(tmp_path, [
        {"id": "yes", "status": "bound", "class": "X", "negated": False},
        {"id": "no", "status": "bound", "class": "X", "negated": True},
    ])
    assert w.weight("yes") == w.weight("no") == 0.5
    # a swap of the pair is weighted by the class, not by polarity: TP 0, FP 1/2, FN 1/2 for both sides
    gold = {"ENTAILED": ["yes"], "REFUTED": ["no"], "CONTINGENT": [], "UNRESOLVED": []}
    pred = {"ENTAILED": ["no"], "REFUTED": ["yes"], "CONTINGENT": [], "UNRESOLVED": []}
    assert closure_utility({"labels": pred}, gold, w) == 0.0


def test_false_assertions_are_weighted_only_when_weights_are_given(tmp_path):
    w = _weights(tmp_path)
    queries = [{"qid": "c", "family": "closure"}]
    gold = {"c": {"qid": "c", "labels": {"ENTAILED": ["a1"], "REFUTED": [], "CONTINGENT": ["a2", "b1", "u1", "a3", "a4", "b2", "u2"], "UNRESOLVED": []}}}
    answer = {"c": {"qid": "c", **_labels(ENTAILED=["a1", "a2", "b2"], CONTINGENT=["b1", "u1", "a3", "a4", "u2"])}}
    plain = score_run(queries, answer, gold, {})
    assert (plain["false_entailment"], plain["false_refutation"]) == (2, 0)
    weighted = score_run(queries, answer, gold, {}, weights=w)
    assert weighted["weighted"]["false_entailment"] == pytest.approx(0.25 + 0.5)  # a2 and b2
    assert weighted["weighted"]["false_refutation"] == 0.0


def test_weighted_score_is_identical_under_shuffled_construction_order(tmp_path):
    """fsum is exact, so the sum of weights does not depend on set order. Plain sum() does depend on it."""
    sizes = {"K3": 3, "K5": 5, "K6": 6, "K7": 7}
    rows, ids_by_class = [], {}
    for name, n in sizes.items():
        ids_by_class[name] = [f"{name}_{i}" for i in range(n)]
        rows += [{"id": x, "status": "bound", "class": name, "negated": i % 2 == 1} for i, x in enumerate(ids_by_class[name])]
    rows += [{"id": f"u{i}", "status": "unresolved", "class": None} for i in range(40)]
    w = _weights(tmp_path, rows)
    universe = [r["id"] for r in rows]
    gold_ids = universe[::2]
    pred_ids = [x for i, x in enumerate(universe) if i % 2 == 1] + [universe[0]]  # unique, overlaps gold
    rng = random.Random(3)
    values = set()
    for _ in range(40):
        rng.shuffle(gold_ids)
        rng.shuffle(pred_ids)
        values.add(selection_utility({"ids": list(pred_ids)}, list(gold_ids), w))
    assert len(values) == 1, values
    assert 0.0 < values.pop() < 1.0  # a non-trivial score, so the check bites


def test_weighted_report_is_identical_across_hash_seeds(tmp_path):
    """Runs the CLI in subprocesses with different PYTHONHASHSEED values (set order changes between them)."""
    args = [sys.executable, "-m", "mentat_bench", "score",
            "--queries", str(TOY10K / "queries.jsonl"), "--gold-answers", str(TOY10K / "gold" / "answers.jsonl"),
            "--baseline", "all-contingent", "--gold-bindings", str(TOY10K / "gold" / "bindings.jsonl"),
            "--design", str(TOY10K / "gold" / "query_design.jsonl")]
    outputs = []
    for seed in ("1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO / "python")}
        done = subprocess.run(args, env=env, capture_output=True, text=True, timeout=600, cwd=REPO)
        assert done.returncode == 0, done.stderr
        report = json.loads(done.stdout)
        outputs.append(json.dumps({"weighted": report["weighted"], "anchor": report["anchor"]}, sort_keys=True))
    assert len(set(outputs)) == 1


def test_class_weights_refuse_duplicate_ids_and_non_string_ids(tmp_path):
    with pytest.raises(ValueError, match="occurs twice"):
        _weights(tmp_path, [{"id": "x", "status": "unresolved", "class": None}, {"id": "x", "status": "unresolved", "class": None}])
    with pytest.raises(ValueError, match="not a string"):
        _weights(tmp_path, [{"id": 7, "status": "unresolved", "class": None}])


def test_cli_gold_bindings_and_design_are_opt_in_and_refuse_missing_files(tmp_path, capsys):
    base = ["score", "--queries", str(TOY10K / "queries.jsonl"), "--gold-answers", str(TOY10K / "gold" / "answers.jsonl"),
            "--baseline", "all-unresolved"]
    assert cli_main(base + ["--gold-bindings", str(tmp_path / "missing.jsonl")]) == 2
    assert cli_main(base + ["--design", str(tmp_path / "missing.jsonl")]) == 2
    capsys.readouterr()
    assert cli_main(base) == 0
    plain = json.loads(capsys.readouterr().out)
    assert "weighted" not in plain
    assert plain["anchor"]["status"] == plain["active"]["status"] == "no_design"
    assert cli_main(base + ["--gold-bindings", str(TOY10K / "gold" / "bindings.jsonl"),
                            "--design", str(TOY10K / "gold" / "query_design.jsonl")]) == 0
    full = json.loads(capsys.readouterr().out)
    assert full["Q_N"] == plain["Q_N"]
    assert full["weighted"]["Q_N"] != plain["Q_N"]
    assert full["anchor"]["queries"] == 40 and full["active"]["status"] == "not_applicable"


# Anchor and active reporting

def _selection_fixture():
    queries = [{"qid": f"s{i}", "family": "selection"} for i in (1, 2, 3)]
    gold = {"s1": {"qid": "s1", "ids": ["x"]}, "s2": {"qid": "s2", "ids": ["y"]}, "s3": {"qid": "s3", "ids": ["z"]}}
    answers = {"s1": {"qid": "s1", "ids": ["x"]}, "s2": {"qid": "s2", "ids": []}, "s3": {"qid": "s3", "ids": ["z"]}}
    return queries, answers, gold


def test_anchor_and_active_blocks_split_by_flag_and_exclude_rows_without_the_key():
    queries, answers, gold = _selection_fixture()
    design = [{"qid": "s1", "anchor": True}, {"qid": "s2", "anchor": False}, {"qid": "s3", "tags": []}]
    report = score_run(queries, answers, gold, {}, design=design)
    assert _core(report["anchor"]) == {"queries": 1, "Q_N": 25.0, "exact_query_success": 1.0, "status": "ok"}
    assert _core(report["active"]) == {"queries": 1, "Q_N": 0.0, "exact_query_success": 0.0, "status": "ok"}
    assert report["queries"] == 3  # the headline still counts every query


def test_all_anchor_design_makes_the_anchor_block_equal_the_headline_and_active_not_applicable():
    queries, answers, gold = _selection_fixture()
    design = [{"qid": q["qid"], "anchor": True} for q in queries]
    report = score_run(queries, answers, gold, {}, design=design)
    assert report["anchor"]["Q_N"] == pytest.approx(report["Q_N"])
    assert _core(report["active"]) == {"queries": 0, "Q_N": None, "status": "not_applicable"}


def test_design_without_any_anchor_key_is_not_applicable_and_no_design_is_no_design():
    queries, answers, gold = _selection_fixture()
    report = score_run(queries, answers, gold, {}, design=[{"qid": "s1"}])
    assert _core(report["anchor"]) == {"queries": 0, "Q_N": None, "status": "not_applicable"}
    assert _core(report["active"]) == {"queries": 0, "Q_N": None, "status": "not_applicable"}
    report = score_run(queries, answers, gold, {})
    assert _core(report["anchor"]) == {"queries": 0, "Q_N": None, "status": "no_design"}
    assert _core(report["active"]) == {"queries": 0, "Q_N": None, "status": "no_design"}


def test_anchor_flag_must_be_a_boolean_and_qids_unique():
    queries, answers, gold = _selection_fixture()
    with pytest.raises(ValueError, match="true or false"):
        score_run(queries, answers, gold, {}, design=[{"qid": "s1", "anchor": "yes"}])
    with pytest.raises(ValueError, match="duplicate design row"):
        score_run(queries, answers, gold, {}, design=[{"qid": "s1", "anchor": True}, {"qid": "s1", "anchor": False}])


def test_committed_design_file_flags_every_query_as_an_anchor():
    fixture = TOY10K
    queries = [q["qid"] for q in ir.read_jsonl(fixture / "queries.jsonl")]
    design = list(ir.read_jsonl(fixture / "gold" / "query_design.jsonl"))
    assert [d["qid"] for d in design] == queries
    assert all(d["anchor"] is True for d in design)


def test_anchor_block_of_the_committed_gold_equals_the_headline_q_n():
    queries = list(ir.read_jsonl(TOY10K / "queries.jsonl"))
    gold = ir.JsonlIndex(TOY10K / "gold" / "answers.jsonl")
    answers = gold  # the gold answers are the oracle answers
    report = score_run(queries, answers, gold, {q["qid"]: True for q in queries},
                       design=ir.read_jsonl(TOY10K / "gold" / "query_design.jsonl"))
    gold.close()
    assert report["Q_N"] == 100.0
    assert _core(report["anchor"]) == {"queries": 40, "Q_N": 100.0, "exact_query_success": 1.0, "status": "ok"}


# Engine-backed fixture effects (skipped without mb-engine)


def _empty_answers(fixture, tmp_path):
    out = tmp_path / "empty_sub"
    rows = [{"id": r["id"], "status": "unresolved", "class": None, "negated": False, "missing": ["unspecified"]}
            for r in ir.read_jsonl(fixture / "corpus.jsonl")]
    ir.write_submission(out, rows, [], ["base", "alt"], {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0})
    run = tmp_path / "empty_run"
    run.mkdir()
    engine.eval_submission(out, fixture / "queries.jsonl", run / "answers.jsonl")
    engine.check(out, fixture / "queries.jsonl", run / "answers.jsonl", run / "verdicts.jsonl")
    answers = {a["qid"]: a for a in ir.read_jsonl(run / "answers.jsonl")}
    verdicts = {v["qid"]: v["cert_valid"] is True for v in ir.read_jsonl(run / "verdicts.jsonl")}
    return answers, verdicts


@pytest.mark.skipif(not HAVE_ENGINE, reason="mb-engine absent")
def test_toy10k_weighting_changes_the_empty_score_and_keeps_the_oracle_at_100(tmp_path):
    queries = list(ir.read_jsonl(TOY10K / "queries.jsonl"))
    gold = ir.JsonlIndex(TOY10K / "gold" / "answers.jsonl")
    weights = ClassWeights.from_bindings(TOY10K / "gold" / "bindings.jsonl")
    oracle_verdicts = {v["qid"]: v["cert_valid"] is True for v in ir.read_jsonl(TOY10K / "gold" / "verdicts.jsonl")}
    oracle = score_run(queries, gold, gold, oracle_verdicts, weights=weights)
    assert oracle["Q_N"] == 100.0 and oracle["weighted"]["Q_N"] == 100.0
    answers, verdicts = _empty_answers(TOY10K, tmp_path)
    empty = score_run(queries, answers, gold, verdicts, weights=weights)
    assert empty["Q_N"] == pytest.approx(22.401972, abs=1e-5)
    assert empty["weighted"]["Q_N"] == pytest.approx(23.350492, abs=1e-5)
    assert empty["weighted"]["Q_N"] != pytest.approx(empty["Q_N"], abs=1e-3)
    gold.close()


def test_equal_class_sizes_leave_selection_f1_unchanged_by_weights(tmp_path):
    bindings = tmp_path / "bindings.jsonl"
    ir.write_jsonl(bindings, [
        {"id": "a1", "status": "bound", "class": "A", "negated": False},
        {"id": "a2", "status": "bound", "class": "A", "negated": True},
        {"id": "b1", "status": "bound", "class": "B", "negated": False},
        {"id": "b2", "status": "bound", "class": "B", "negated": True},
    ])
    weights = ClassWeights.from_bindings(bindings)
    queries = [{"qid": "s1", "family": "selection"}, {"qid": "s2", "family": "selection"}]
    gold = {"s1": {"qid": "s1", "ids": ["a1", "b1"]}, "s2": {"qid": "s2", "ids": ["a2", "b2"]}}
    answers = {qid: {"qid": qid, "ids": row["ids"][:1]} for qid, row in gold.items()}
    weighted = score_run(queries, answers, gold, {}, weights=weights)
    plain = score_run(queries, answers, gold, {})
    plain_mean = plain["families"]["selection"]["mean_utility"]
    assert 0.0 < plain_mean < 1.0
    assert weighted["weighted"]["families"]["selection"]["mean_utility"] == pytest.approx(plain_mean, abs=1e-12)


def test_anchor_block_reports_uncovered_design_rows_and_missing_families():
    """L5: a design row with no query in the workload is `uncovered` in its block and listed in design_absent; a
    block's families with no query are listed in families_missing."""
    from mentat_bench import scoring

    queries = [{"qid": "s1", "family": "selection"}]
    answers = {"s1": {"qid": "s1", "ids": ["a"]}}
    gold = {"s1": {"qid": "s1", "ids": ["a"]}}
    design = [{"qid": "s1", "anchor": True}, {"qid": "s2", "anchor": True}, {"qid": "s3", "anchor": False}]
    report = scoring.score_run(queries, answers, gold, {}, design=design)
    assert report["anchor"]["uncovered"] == 1 and report["anchor"]["queries"] == 1
    assert report["anchor"]["families_missing"] == ["closure", "feasibility", "partition"]
    assert report["active"]["uncovered"] == 1 and report["active"]["status"] == "not_applicable"
    assert report["design_absent"] == ["s2", "s3"]


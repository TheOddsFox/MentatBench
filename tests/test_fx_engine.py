"""Engine-backed fixture tests: gold answers reproduce under mb-engine, premises match ablation, the oracle
scores 100 and a consistent empty theory scores low, and regeneration reproduces every fixture file.

These need the mb-engine binary (MB_ENGINE or target/release); they skip cleanly otherwise. The toy10k fixture
directory can be overridden with MB_FIXTURE_TOY10K.
"""

import os
import shutil
from pathlib import Path

import pytest

from mentat_bench import engine, fixtures_gen, ir, scoring

REPO = Path(__file__).resolve().parents[1]
TOY10K = Path(os.environ.get("MB_FIXTURE_TOY10K", REPO / "fixtures" / "toy10k"))
HAVE_ENGINE = engine.engine_available()
GOLD_FILES = ("bindings.jsonl", "theory.json", "provenance.jsonl", "usage.json")
GENERATED_TOY10K = ("corpus.jsonl", "queries.jsonl", "fixture.json", "gold/bindings.jsonl",
                    "gold/provenance.jsonl", "gold/theory.json", "gold/usage.json", "gold/answers.jsonl",
                    "gold/verdicts.jsonl", "gold/query_design.jsonl", "gold/summary.json")
def _have(fixture: Path) -> bool:
    return HAVE_ENGINE and (fixture / "gold" / "theory.json").exists() and (fixture / "queries.jsonl").exists()


def _run_gold(gold_dir: Path, queries: Path, tmp: Path) -> tuple[dict, dict]:
    """Engine answers and verdicts for one submission, keyed by qid."""
    tmp.mkdir(parents=True, exist_ok=True)
    answers_path, verdicts_path = tmp / "answers.jsonl", tmp / "verdicts.jsonl"
    engine.eval_submission(gold_dir, queries, answers_path)
    engine.check(gold_dir, queries, answers_path, verdicts_path)
    answers = {a["qid"]: a for a in ir.read_jsonl(answers_path)}
    verdicts = {v["qid"]: v for v in ir.read_jsonl(verdicts_path)}
    return answers, verdicts


@pytest.mark.skipif(not _have(TOY10K), reason="toy10k fixture or mb-engine absent")
def test_toy10k_committed_answers_and_verdicts_reproduce(tmp_path):
    answers, verdicts = _run_gold(TOY10K / "gold", TOY10K / "queries.jsonl", tmp_path)
    committed = {a["qid"]: a for a in ir.read_jsonl(TOY10K / "gold" / "answers.jsonl")}
    assert set(answers) == set(committed)
    for qid in committed:
        family = next(q["family"] for q in ir.read_jsonl(TOY10K / "queries.jsonl") if q["qid"] == qid)
        assert fixtures_gen.canonical_answer(family, answers[qid]) == fixtures_gen.canonical_answer(family, committed[qid]), qid
    if (TOY10K / "gold" / "verdicts.jsonl").exists():
        for v in ir.read_jsonl(TOY10K / "gold" / "verdicts.jsonl"):
            assert (verdicts[v["qid"]]["reason"], verdicts[v["qid"]]["cert_valid"]) == (v["reason"], v["cert_valid"]), v["qid"]


@pytest.mark.skipif(not _have(TOY10K), reason="toy10k fixture or mb-engine absent")
def test_toy10k_premises_equal_a_fresh_ablation(tmp_path):
    queries = list(ir.read_jsonl(TOY10K / "queries.jsonl"))
    answers = {a["qid"]: a for a in ir.read_jsonl(TOY10K / "gold" / "answers.jsonl")}
    counts = fixtures_gen.material_premises(TOY10K / "gold", queries, answers, tmp_path)
    design = {d["qid"]: d["premises"] for d in ir.read_jsonl(TOY10K / "gold" / "query_design.jsonl")}
    assert design == counts


def _submission_copy(src_gold: Path, dst: Path) -> Path:
    dst.mkdir(parents=True)
    for name in GOLD_FILES:
        shutil.copyfile(src_gold / name, dst / name)
    return dst


@pytest.mark.skipif(not _have(TOY10K), reason="toy10k fixture or mb-engine absent")
def test_oracle_scores_100_and_a_consistent_empty_theory_scores_low(tmp_path):
    queries = list(ir.read_jsonl(TOY10K / "queries.jsonl"))
    gold_answers, gold_verdicts = _run_gold(TOY10K / "gold", TOY10K / "queries.jsonl", tmp_path / "gold")
    oracle_answers, oracle_verdicts = _run_gold(
        _submission_copy(TOY10K / "gold", tmp_path / "oracle"), TOY10K / "queries.jsonl", tmp_path / "oracle_run")
    oracle = scoring.score_run(queries, oracle_answers, gold_answers,
                               {q: v["cert_valid"] for q, v in oracle_verdicts.items()})
    assert oracle["Q_N"] == 100.0 and oracle["exact_query_success"] == 1.0

    empty_dir = tmp_path / "empty"
    corpus_ids = [r["id"] for r in ir.read_jsonl(TOY10K / "corpus.jsonl")]
    rows = [{"id": i, "status": "unresolved", "class": None, "negated": False, "missing": ["unspecified"]} for i in corpus_ids]
    ir.write_submission(empty_dir, rows, [], ["base", "alt"], {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0})
    empty_answers, empty_verdicts = _run_gold(empty_dir, TOY10K / "queries.jsonl", tmp_path / "empty_run")
    empty = scoring.score_run(queries, empty_answers, gold_answers, {q: v["cert_valid"] for q, v in empty_verdicts.items()})
    assert empty["Q_N"] < 50.0, empty["families"]


@pytest.mark.skipif(not _have(TOY10K), reason="toy10k fixture or mb-engine absent")
@pytest.mark.slow
def test_toy10k_regeneration_reproduces_every_fixture_file(tmp_path):
    fixtures_gen.generate(tmp_path)
    fixtures_gen.finalize(tmp_path)
    for rel in GENERATED_TOY10K:
        assert (tmp_path / rel).read_bytes() == (TOY10K / rel).read_bytes(), f"{rel} is stale"



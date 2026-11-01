"""Development round 3 acceptance (stream 6, docs/mentat_hard.md section 5): on the committed dev and test fixtures (registry
version 3, fixture version 3.1), gold_view with the curated suite (--candidates) and the oracle both score Q_N 100 on the exam.
Every verdict is ok or family_not_certified, and the curated decision suite scores MCC 1.0 on its headline (the mean of the
relation and group MCCs), its secondary (binding) and its reading diagnostic. The v2 acceptance test is in git history at 928df14.

Slow: each cell runs the full build and evaluates the exam with mb-engine. Skipped without the engine, as other slow engine
tests are (MB_REQUIRE_ENGINE=1 turns the skip into a failure)."""

import json
from pathlib import Path

import pytest

from mentat_bench import engine, runcell

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"
PROFILE = REPO / "profiles" / "mentat-hard-v1.json"
SPLITS = ("mentat_hard_dev", "mentat_hard_test")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not engine.engine_available(), reason="mb-engine not built (set MB_ENGINE)"),
]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("split", SPLITS)
def test_gold_view_with_the_curated_suite_scores_q_n_100_and_mcc_1(split, tmp_path):
    fixture = FIXTURES / split
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "gold_view", [], out, PROFILE, [], candidates=fixture / "candidates.jsonl") == 0
    assert _json(out / "score.json")["Q_N"] == 100.0
    verdicts = [json.loads(line) for line in (out / "verdicts.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert verdicts and {v["reason"] for v in verdicts} <= {"ok", "family_not_certified"}
    headline = _json(out / "score_decisions.json")["headline"]
    assert headline["metric"] == "relation_group_mcc"  # fixture version 3.1: the mean of the relation and group MCCs
    assert headline["relation_group_mcc"] == 1.0
    assert headline["parts"] == {"relation_mcc": 1.0, "group_mcc": 1.0}
    assert headline["secondary"]["binding_mcc"] == 1.0
    assert headline["diagnostics"] == {"reading_mcc": 1.0}


@pytest.mark.parametrize("split", SPLITS)
def test_oracle_scores_q_n_100(split, tmp_path):
    out = tmp_path / "cell"
    assert runcell.run_cell(FIXTURES / split, "oracle", [], out, PROFILE, []) == 0
    assert _json(out / "score.json")["Q_N"] == 100.0

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from mentat_bench import fixtures_gen, ir
from irval import check_expr, check_submission

REPO = Path(__file__).resolve().parents[1]
# MB_FIXTURE_TOY10K overrides the fixture directory (used to test a regenerated copy before it is committed).
FIXTURE = Path(os.environ.get("MB_FIXTURE_TOY10K", REPO / "fixtures" / "toy10k"))
HAVE_FIXTURE = (FIXTURE / "queries.jsonl").exists() and (FIXTURE / "gold" / "theory.json").exists()


@pytest.mark.skipif(not HAVE_FIXTURE, reason="toy10k fixture not generated")
def test_fixture_corpus_is_controlled_and_matches_gold_bindings():
    records = list(ir.read_jsonl(FIXTURE / "corpus.jsonl"))
    ids = [r["id"] for r in records]
    assert len(ids) == len(set(ids)) >= 10_000
    assert all(r["controlled"] is True for r in records)
    assert all(set(r) == {"id", "text", "label", "controlled", "event_title"} for r in records)
    assert all(r["text"] and r["label"] for r in records)
    gold_ids = {b["id"] for b in ir.read_jsonl(FIXTURE / "gold" / "bindings.jsonl")}
    assert gold_ids == set(ids)
    assert check_submission(FIXTURE / "gold") == []


@pytest.mark.skipif(not HAVE_FIXTURE, reason="toy10k fixture not generated")
def test_fixture_queries_are_wellformed_and_balanced():
    from mentat_bench.ir import expr_vars

    queries = list(ir.read_jsonl(FIXTURE / "queries.jsonl"))
    design = list(ir.read_jsonl(FIXTURE / "gold" / "query_design.jsonl"))
    ids = {r["id"] for r in ir.read_jsonl(FIXTURE / "corpus.jsonl")}
    assert len(queries) == 40
    assert Counter(q["family"] for q in queries) == {"closure": 10, "selection": 10, "feasibility": 10, "partition": 10}
    assert [d["qid"] for d in design] == [q["qid"] for q in queries]
    for q in queries:
        exprs = []
        for key in ("hyp", "assume", "members"):
            exprs += q.get(key, [])
        if q.get("target") is not None:
            exprs.append(q["target"])
        for e in exprs:
            assert check_expr(e) == [], (q["qid"], e)
        used = set()
        for e in exprs:
            used |= expr_vars(e)
        assert used <= ids, q["qid"]
        assert set(q.get("universe", ids)) <= ids
    unresolved = {d["qid"] for d in design if any("unresolved" in t or "missing" in t for t in d["tags"])}
    assert len(unresolved) >= 5
    # Premises are measured by ablation (finalize), so the design must carry the measured counts.
    assert all(isinstance(d["premises"], int) for d in design), "run fixtures_gen finalize"
    assert sum(1 for d in design if d["premises"] >= 8) >= 6


@pytest.mark.skipif(not (HAVE_FIXTURE and (FIXTURE / "gold" / "answers.jsonl").exists()), reason="toy10k answers not generated")
def test_fixture_answer_mixtures_match_the_design():
    queries = list(ir.read_jsonl(FIXTURE / "queries.jsonl"))
    answers = {a["qid"]: a for a in ir.read_jsonl(FIXTURE / "gold" / "answers.jsonl")}
    feas = Counter(answers[q["qid"]]["decision"] for q in queries if q["family"] == "feasibility")
    part = Counter(answers[q["qid"]]["decision"] for q in queries if q["family"] == "partition")
    assert feas == {"SAT": 4, "UNSAT": 4, "UNRESOLVED": 2}
    assert part == {"true": 4, "false": 4, "UNRESOLVED": 2}
    nonempty = sum(1 for q in queries if q["family"] == "selection" and answers[q["qid"]]["ids"])
    assert nonempty >= 9
    assert all(answers[q["qid"]].get("error") is None for q in queries if q["family"] == "closure")


def test_generation_is_deterministic(tmp_path):
    summary_a = fixtures_gen.generate(tmp_path / "a")
    summary_b = fixtures_gen.generate(tmp_path / "b")
    assert summary_a == summary_b
    for rel in ("corpus.jsonl", "queries.jsonl", "fixture.json", "gold/bindings.jsonl", "gold/query_design.jsonl",
                "gold/provenance.jsonl", "gold/theory.json", "gold/usage.json"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel


@pytest.mark.skipif(not HAVE_FIXTURE, reason="toy10k fixture not generated")
def test_checked_in_fixture_matches_generator(tmp_path):
    """The pure generation step (no engine). The engine step is checked in test_fx_engine.py."""
    fixtures_gen.generate(tmp_path / "fresh")
    for rel in ("corpus.jsonl", "queries.jsonl", "gold/bindings.jsonl", "gold/provenance.jsonl", "gold/theory.json"):
        assert (FIXTURE / rel).read_bytes() == (tmp_path / "fresh" / rel).read_bytes(), f"{rel} is stale; regenerate with fixtures_gen"


@pytest.mark.slow
def test_anchor_queries_and_base_outcomes_are_identical_at_larger_scale(tmp_path):
    """Scaling adds seasons: the 40 queries (all anchored to seasons 2025-2026) and every base-season outcome ID
    keep their content, so matched queries mean the same thing at every scale."""
    small = fixtures_gen.generate(tmp_path / "s4")
    large = fixtures_gen.generate(tmp_path / "s8", seasons=8)
    assert large["outcomes"] > small["outcomes"]
    assert (tmp_path / "s4" / "queries.jsonl").read_bytes() == (tmp_path / "s8" / "queries.jsonl").read_bytes()
    base = {r["id"] for r in ir.read_jsonl(tmp_path / "s4" / "corpus.jsonl")}
    scaled = {r["id"] for r in ir.read_jsonl(tmp_path / "s8" / "corpus.jsonl")}
    assert base <= scaled


@pytest.mark.skipif(not HAVE_FIXTURE, reason="toy10k fixture not generated")
def test_gold_theory_interpretations_and_scoped_constraints():
    theory = json.loads((FIXTURE / "gold" / "theory.json").read_text(encoding="utf-8"))
    assert theory["interpretations"] == ["base", "alt"]
    scoped = [c for c in theory["constraints"] if c["interps"] is not None]
    assert scoped and {tuple(c["interps"]) for c in scoped} == {("base",), ("alt",)}
    assert all(c["id"].startswith("g") for c in theory["constraints"])


def test_dated_yes_and_no_are_complementary_in_every_interpretation():
    recs = fixtures_gen._records(fixtures_gen.SEED, fixtures_gen.BASE_SEASONS)
    constraints = fixtures_gen._gold_constraints(recs)
    by_id = {r["id"]: r for r in recs}
    dated_yes = [r for r in recs if r["kind"] == "dated" and r["label"] == "Yes"]
    assert len(dated_yes) == 100
    complements = set()
    for c in constraints:
        e = c["expr"]
        if e["op"] == "iff" and c["interps"] is None and e["b"]["op"] == "not":
            complements.add((e["a"]["id"], e["b"]["a"]["id"]))
    for r in dated_yes:
        no = next(x for x in recs if x["kind"] == "dated" and x["si"] == r["si"] and x["i"] == r["i"]
                  and x["t"] == r["t"] and x["label"] == "No")
        assert (r["id"], no["id"]) in complements
        assert by_id[r["id"]]["cls"] is None


GOLD_ONLY_KEYS = {"tags", "premises", "answers", "verdict_reasons", "decisions"}


@pytest.mark.skipif(not HAVE_FIXTURE, reason="toy10k fixture not generated")
def test_top_level_carries_only_public_inputs():
    """Gold-bearing data (answers, verdicts, decision tags, premise counts) lives under gold/."""
    assert {p.name for p in FIXTURE.iterdir()} <= {"corpus.jsonl", "queries.jsonl", "fixture.json", "gold"}
    assert all(GOLD_ONLY_KEYS.isdisjoint(q) for q in ir.read_jsonl(FIXTURE / "queries.jsonl"))
    assert GOLD_ONLY_KEYS.isdisjoint(ir.read_json(FIXTURE / "fixture.json"))

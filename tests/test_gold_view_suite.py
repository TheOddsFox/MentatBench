"""gold_view answers the curated decision suite from its labels (gold/suite_labels.jsonl, joined to the fixture's
candidates.jsonl): relations as asked (IMPLIES and IMPLIED_BY swap when the pair is reversed), groups as the canonical kind
(members in label order for an ordered_chain). Unlabelled questions keep the gold theory's answers."""

import json
from pathlib import Path

import pytest

from mentat_bench.adapters.mock import GoldViewAdapter, _GoldAdapter, _suite_answers

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mentat_hard_dev"
GOLD = FIXTURE / "gold"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def suite():
    if not (GOLD / "suite_labels.jsonl").is_file() or not (FIXTURE / "candidates.jsonl").is_file():
        pytest.skip("mentat_hard_dev fixture with its suite labels is not in this checkout")
    labels = {row["qid"]: row for row in _rows(GOLD / "suite_labels.jsonl")}
    candidates = _rows(FIXTURE / "candidates.jsonl")
    return GoldViewAdapter(GOLD), labels, candidates


def test_every_labelled_relation_and_group_is_answered_with_its_label(suite) -> None:
    adapter, labels, candidates = suite
    relations = groups = 0
    for cand in candidates:
        label = labels[cand["qid"]]
        if cand["op"] == "decide_relation":
            decision, _ = adapter.decide_relation({"id": cand["a"]}, {"id": cand["b"]})
            assert decision == {"relation": label["label"], "conf": 10.0}
            relations += 1
        elif cand["op"] == "decide_group" and cand.get("source") != "reading":  # reading rows: test_mentat_hard_v2 and the acceptance test (test_mentat_hard_v3_gold_view)
            decisions, _ = adapter.decide_group({"members": [{"id": m} for m in cand["members"]]})
            terms = label.get("terms", [] if label["label"] == "none" else [[label["label"], label["k"]]])  # one decision per term
            assert len(decisions) == len(terms), cand["qid"]
            for decision, (kind, k) in zip(decisions, terms):
                assert decision["kind"] == kind and set(decision["members"]) == set(cand["members"])
                if kind == "ordered_chain":
                    assert decision["members"] == label["order"] and decision["direction"] == "forward"
                else:
                    assert "direction" not in decision and decision.get("k") == k
            groups += 1
    assert relations == 6000 and groups == 1000


def test_a_reversed_pair_swaps_implies_and_implied_by(suite) -> None:
    adapter, labels, candidates = suite
    seen: set[str] = set()
    for cand in candidates:
        if cand["op"] != "decide_relation" or labels[cand["qid"]]["label"] not in ("IMPLIES", "IMPLIED_BY", "EQUIV"):
            continue
        label = labels[cand["qid"]]["label"]
        reversed_label = {"IMPLIES": "IMPLIED_BY", "IMPLIED_BY": "IMPLIES", "EQUIV": "EQUIV"}[label]
        decision, _ = adapter.decide_relation({"id": cand["b"]}, {"id": cand["a"]})
        assert decision["relation"] == reversed_label
        seen.add(label)
        if seen == {"IMPLIES", "IMPLIED_BY", "EQUIV"}:
            break
    assert seen == {"IMPLIES", "IMPLIED_BY", "EQUIV"}


def cand_group(candidates: list[dict]) -> list[str]:
    return next(c["members"] for c in candidates if c["op"] == "decide_group")


def test_unlabelled_questions_keep_the_gold_theory_answers(suite) -> None:
    adapter, labels, candidates = suite
    cand = next(c for c in candidates if c["op"] == "decide_relation")
    saved = (adapter._labelled_relations, adapter._labelled_groups)
    adapter._labelled_relations, adapter._labelled_groups = {}, {}
    try:
        decision, _ = adapter.decide_relation({"id": cand["a"]}, {"id": cand["b"]})
        assert decision == _GoldAdapter.decide_relation(adapter, {"id": cand["a"]}, {"id": cand["b"]})[0]  # a plain string
        groups, _ = adapter.decide_group({"members": [{"id": m} for m in cand_group(candidates)]})
        assert groups == _GoldAdapter.decide_group(adapter, {"members": [{"id": m} for m in cand_group(candidates)]})[0]
    finally:
        adapter._labelled_relations, adapter._labelled_groups = saved


def test_no_labels_without_both_the_suite_labels_and_the_candidates(tmp_path: Path) -> None:
    assert _suite_answers(tmp_path) == ({}, {})
    gold = tmp_path / "fixture" / "gold"
    gold.mkdir(parents=True)
    (gold / "suite_labels.jsonl").write_text('{"qid": "r1", "op": "decide_relation", "label": "EQUIV"}\n', encoding="utf-8")
    assert _suite_answers(gold) == ({}, {})  # no candidates.jsonl above gold/

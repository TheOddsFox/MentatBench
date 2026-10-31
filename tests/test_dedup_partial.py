"""Complement dedup coverage (fallback when the representative is not a named answer), derived rows that never outlive
their representative in the base-check pruning, and the PARTIAL marker of a complete build that stopped at a cap."""

import json
import sys
from pathlib import Path

import pytest

from mentat_bench import cli, report
from mentat_bench.adapters.base import AdapterError, usage
from mentat_bench.adapters.mock import GoldViewAdapter
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import _BaseCheck, _prune_in_process, _Unit
from mentat_bench.manifest import DEFAULT_PROFILE
from mentat_bench.seal import seal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers_pa_engine import make_fake_engine  # noqa: E402
from test_cost_controls import _complement_gold  # noqa: E402


class _Answers:
    """Gold bindings; the relation of the representative pair (alpha 1, beta 1) is `answer` (an exception is raised);
    every other pair is NONE. Records the pairs asked."""

    name = "answers"

    def __init__(self, gold, answer):
        self.gold, self.answer = GoldViewAdapter(gold), answer
        self.asked: list[tuple[str, str]] = []

    def decide_binding(self, ctx):
        return self.gold.decide_binding(ctx)

    def decide_relation(self, a, b):
        self.asked.append((a["id"], b["id"]))
        if {a["id"], b["id"]} == {"v:alpha:1", "v:beta:1"}:
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer, usage(self.name)
        return "NONE", usage(self.name)


DERIVED = {("v:alpha:2", "v:beta:1"), ("v:alpha:1", "v:beta:2"), ("v:alpha:2", "v:beta:2")}


@pytest.mark.parametrize("answer", ["NONE", "UNRESOLVED", AdapterError("gave up")])
def test_an_undecided_representative_asks_the_three_complement_pairs(tmp_path, answer):
    """M1: NONE, UNRESOLVED and an AdapterError answer give no entailment, so the complement pairs are asked."""
    records, gold = _complement_gold(tmp_path)
    adapter = _Answers(gold, answer)
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert DERIVED <= set(adapter.asked)  # records are in (alpha1, alpha2, beta1, beta2) order: pairs are i < j
    assert summary["dedup_fallback_asked"] == 3 and summary["pairs_derived"] == 0
    assert summary["dedup_representatives"] == 1 and summary["relations_committed"] == summary["candidate_pairs"]
    assert summary["dedup_undecided"] == (1 if answer != "NONE" else 0)
    assert summary["dedup_none"] == (1 if answer == "NONE" else 0)


def test_a_named_representative_derives_without_asking(tmp_path):
    records, gold = _complement_gold(tmp_path)
    adapter = _Answers(gold, "IMPLIES")
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["dedup_fallback_asked"] == 0 and summary["dedup_named"] == 1
    assert summary["pairs_derived"] == 2 and summary["pairs_derived_none"] == 1
    assert summary["relations_committed"] + summary["pairs_derived"] + summary["pairs_derived_none"] == summary["candidate_pairs"]
    assert not (set(adapter.asked) & DERIVED)  # the derived pairs were never asked


def test_derived_rows_carry_their_representative_line(tmp_path, monkeypatch):
    """M2: a derived row of relations.jsonl is [i, j, relation, line of its representative]."""
    records, gold = _complement_gold(tmp_path)
    real_rmtree = fixed.shutil.rmtree
    monkeypatch.setattr(fixed.shutil, "rmtree", lambda path, **kw: None if str(path).endswith(".build") else real_rmtree(path, **kw))
    fixed.build(records, _Answers(gold, "IMPLIES"), tmp_path / "sub", dict(DEFAULT_PROFILE))
    rows = [json.loads(line) for line in (tmp_path / "sub.build" / "relations.jsonl").read_text(encoding="utf-8").splitlines()]
    rep = [row for row in rows if len(row) == 3]
    derived = [row for row in rows if len(row) == 4]
    assert len(rep) == 1 and rep[0][2] == "IMPLIES"
    assert len(derived) == 2 and all(row[3] == 0 for row in derived)  # the representative is line 0


class _Replay:
    ids = ["v:x0:1", "v:x1:1", "v:x2:1", "v:x3:1"]

    def rows(self):
        return iter(())


def _rel(n, clauses, kind="relation", rep=-1):
    return _Unit(f"p{n:07d}", None, -1, clauses, False, kind, rep=rep)


def test_a_derived_row_culprit_drops_its_representative_and_its_derived_rows():
    """M2: the culprit is the derived row (x0 forced true), but it is consistent only with its representative, so the
    representative and the derived row go together; the plain relation (not x0) stays."""
    units = [_rel(0, [(1, 1)]), _rel(1, [(2, 2)]), _rel(2, [(0, 0)], "derived", rep=1)]
    check = _BaseCheck(_Replay(), units, 1)
    dropped = _prune_in_process(check, relation_cap=10, group_cap=10, time_left=lambda: 1e9)
    assert dropped == {1, 2}
    assert not check.unsat(len(units), dropped, 0)


def test_a_representative_culprit_takes_its_derived_rows_along():
    units = [_rel(0, [(1, 1)]), _rel(1, [(0, 0)]), _rel(2, [(4, 4)], "derived", rep=1)]
    dropped = _prune_in_process(_BaseCheck(_Replay(), units, 1), relation_cap=10, group_cap=10, time_left=lambda: 1e9)
    assert dropped == {1, 2}


# ---- PARTIAL marker: a complete build that stopped at a cap is sealed and says so -------------------------------------

def _tiny_corpus(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(json.dumps({"id": f"v:m{i}:1", "text": f"Will X{i} win the cup?", "label": "Yes",
                                          "event_title": "E"}) + "\n" for i in range(1, 5)), encoding="utf-8")
    return corpus


def test_a_pairs_capped_build_is_sealed_with_a_partial_marker(tmp_path, capsys):
    out = tmp_path / "out"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out),
                     "--max-pairs", "2"]) == 0
    marker = json.loads((out / fixed.PARTIAL_FILE).read_text(encoding="utf-8"))
    assert marker == {"partial": True, "reasons": ["pairs_capped"]}
    summary = json.loads(capsys.readouterr().out)
    assert summary["pairs_asked_cap"] == 2 and summary["pairs_considered"] == 2 and summary["pairs_capped"] is True
    assert cli.main(["seal", "--submission", str(out)]) == 0  # budget-capped builds still seal
    assert fixed.PARTIAL_FILE in json.loads((out / "seal.json").read_text(encoding="utf-8"))["files"]


def test_a_complete_build_has_no_partial_marker(tmp_path, capsys):
    out = tmp_path / "out"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == 0
    assert not (out / fixed.PARTIAL_FILE).exists()
    assert json.loads(capsys.readouterr().out)["pairs_asked_cap"] is None


def test_partial_reasons_and_status_follow_the_summary():
    assert fixed.partial_reasons({"pairs_capped": True, "budget_stopped": True}) == ["budget_stopped", "pairs_capped"]
    assert fixed.build_status({"pairs_capped": True}) == "pairs_capped"
    assert fixed.build_status({"calls_capped": True, "group_budget_stopped": True}) == "calls_capped"
    assert fixed.build_status({"group_budget_stopped": True}) == "group_budget_stopped"
    assert fixed.build_status({"adapter_fatal": True, "pairs_capped": True}) == "incomplete"
    assert fixed.build_status({}) == "complete"


def test_evaluate_warns_and_reports_a_partial_submission(tmp_path, monkeypatch, capsys):
    """evaluate prints a stderr warning and adds "partial" to its stdout JSON."""
    corpus = _tiny_corpus(tmp_path)
    sub = tmp_path / "run" / "submission"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(sub), "--max-pairs", "2"]) == 0
    digest = seal(sub)["digest"]
    capsys.readouterr()
    gold = Path(__file__).resolve().parents[1] / "fixtures" / "toy10k" / "gold"
    queries = tmp_path / "queries.jsonl"
    queries.write_text(json.dumps({"family": "closure", "qid": "c1", "hyp": []}) + "\n", encoding="utf-8")
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "eval"
    code = cli.main(["evaluate", "--submission", str(sub), "--expected-digest", digest,
                     "--profile", str(Path(__file__).resolve().parents[1] / "profiles" / "default.json"),
                     "--queries", str(queries), "--gold", str(gold), "--out", str(out)])
    captured = capsys.readouterr()
    assert code == 0
    assert "partial build (pairs_capped)" in captured.err
    assert json.loads(captured.out)["partial"] == ["pairs_capped"]


def test_report_shows_the_status_and_the_model_identity(tmp_path):
    def run(name, summary, policy=None):
        r = tmp_path / name
        r.mkdir()
        (r / "score.json").write_text(json.dumps({"Q_N": 1.0, "families": {}}), encoding="utf-8")
        (r / "build_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        if policy is not None:
            (r / "submission").mkdir()
            (r / "submission" / "build_policy.json").write_text(json.dumps(policy), encoding="utf-8")
        return r

    rows = [report.run_row(run("a", {"pairs_capped": True, "pairs_asked_cap": 2, "dedup_fallback_asked": 3},
                               {"model": {"id": "Qwen2.5-0.5B-Instruct", "revision": "abc"}})),
            report.run_row(run("b", {"adapter_fatal": True})),
            report.run_row(run("c", {}))]
    assert [r["status"] for r in rows] == ["pairs_capped", "incomplete", "complete"]
    assert rows[0]["model_id"] == "Qwen2.5-0.5B-Instruct" and rows[0]["model_revision"] == "abc"
    assert rows[0]["pairs_asked_cap"] == 2 and rows[0]["dedup_fallback_asked"] == 3
    assert "| pairs_capped |" in report.markdown(rows)

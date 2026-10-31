"""PB round 2: adapter failure handling (AdapterFatal), in-process base-consistency check with bounded drops, metered
engine confirmation, symmetric blocking partners, and verdict reading. Engine-dependent tests skip without mb-engine."""

import itertools
import json
import math
import random

import pytest

from mentat_bench import engine, ir, scoring
from mentat_bench.adapters.base import AdapterError, AdapterFatal, usage
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import BaseCheckIncomplete, BaseInconsistentError, candidate_pairs, iter_candidate_pairs
from mentat_bench.manifest import DEFAULT_PROFILE
from test_pb_harness import Scripted, _bound, _recs, _unresolved

HAVE_ENGINE = engine.engine_available()


def _profile(**kw):
    return dict(DEFAULT_PROFILE, **kw)


class _Failing:
    """Every decision is an AdapterError (the gateway retries and then reports one failure per decision)."""

    name = "failing"

    def decide_binding(self, ctx):
        raise AdapterError("no reply")

    def decide_relation(self, a, b):
        raise AdapterError("no reply")


# Item 1 and 2: AdapterFatal and the failure counters


def test_bindings_that_always_fail_abort_the_build_and_write_nothing(tmp_path):
    recs = _recs([(f"v:m{i}:1", f"topic{i} word{i}") for i in range(50)])
    with pytest.raises(AdapterFatal, match="adapter failures in a row"):
        fixed.build(recs, _Failing(), tmp_path / "sub", _profile(), max_retries=0)
    assert not (tmp_path / "sub").exists() and (tmp_path / "sub.build" / "usage_calls.jsonl").exists()  # charged calls: kept


def test_relation_failures_seal_the_latest_checkpoint_and_report_adapter_fatal(tmp_path):
    recs = _recs([(f"v:m{i}:1", "shared topic") for i in range(30)])  # every pair is a candidate: 435 relation calls
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    calls = []

    class FailAfterTwo(Scripted):
        def decide_relation(self, a, b):
            calls.append((a["id"], b["id"]))
            if len(calls) <= 2:
                return "EXCLUDES", usage("scripted")
            raise AdapterError("no reply")

    summary = fixed.build(recs, FailAfterTwo(bindings), tmp_path / "sub", _profile(), max_retries=0, checkpoint_every=1)
    assert summary["adapter_fatal"] is True
    assert summary["constraints"] == 2  # the two good decisions survive
    assert len(calls) == 2 + fixed.MAX_CONSECUTIVE_ERRORS  # stopped at the 20th failure in a row
    assert summary["relations_committed"] == 1 + fixed.MAX_CONSECUTIVE_ERRORS  # the 22nd call's batch is discarded
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text(encoding="utf-8"))
    assert len(theory["constraints"]) == 2
    assert not (tmp_path / "sub.build").exists()


def test_consecutive_failures_are_counted_per_run_and_a_success_resets_them(tmp_path):
    recs = _recs([(f"v:m{i}:1", f"topic{i} word{i}") for i in range(60)])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    seen = []

    class NineteenThenOne(Scripted):
        def decide_binding(self, ctx):
            seen.append(ctx["id"])
            if len(seen) % 20 == 0:
                return super().decide_binding(ctx)  # one success every 20 decisions: runs of 19 failures
            raise AdapterError("no reply")

    # 60 decisions, 95% failing: under the 100-decision fraction rule, and no run reaches 20.
    summary = fixed.build(recs, NineteenThenOne(bindings), tmp_path / "sub", _profile(), max_retries=0)
    assert summary["outcomes"] == 60 and summary["adapter_fatal"] is False
    assert sum(1 for r in ir.read_jsonl(tmp_path / "sub" / "bindings.jsonl") if r["missing"] == ["adapter_error"]) == 57


def test_failing_more_than_half_of_at_least_100_decisions_is_fatal(tmp_path):
    recs = _recs([(f"v:m{i}:1", f"topic{i} word{i}") for i in range(300)])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    seen = []

    class TwoOfThreeFail(Scripted):
        def decide_binding(self, ctx):
            seen.append(ctx["id"])
            if len(seen) % 3 == 0:
                return super().decide_binding(ctx)
            raise AdapterError("no reply")

    with pytest.raises(AdapterFatal, match="decisions failed"):
        fixed.build(recs, TwoOfThreeFail(bindings), tmp_path / "sub", _profile(), max_retries=0)
    assert len(seen) >= 100 and len(seen) < 300


def test_exactly_half_failing_is_not_fatal(tmp_path):
    recs = _recs([(f"v:m{i}:1", f"topic{i} word{i}") for i in range(200)])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    seen = []

    class EveryOtherFails(Scripted):
        def decide_binding(self, ctx):
            seen.append(ctx["id"])
            if len(seen) % 2 == 0:
                raise AdapterError("no reply")
            return super().decide_binding(ctx)

    summary = fixed.build(recs, EveryOtherFails(bindings), tmp_path / "sub", _profile(), max_retries=0)
    assert summary["outcomes"] == 200 and summary["budget_stopped"] is False and summary["adapter_fatal"] is False


def test_malformed_decisions_count_as_failures(tmp_path):
    recs = _recs([(f"v:m{i}:1", f"topic{i} word{i}") for i in range(30)])

    class Malformed(Scripted):
        def decide_binding(self, ctx):
            return {"status": "bound", "class": None}, usage("scripted")

    with pytest.raises(AdapterFatal):
        fixed.build(recs, Malformed({}), tmp_path / "sub", _profile(), max_retries=0)


def test_negative_cost_is_a_failed_call_not_a_successful_one(tmp_path):
    recs = _recs([(f"v:m{i}:1", f"topic{i} word{i}") for i in range(30)])

    class NegativeCost(Scripted):
        def decide_binding(self, ctx):
            return _unresolved(ctx["id"]), {"model": "x", "kind": "local", "tokens_in": 0, "tokens_out": 0, "cost_usd": -1.0}

    with pytest.raises(AdapterFatal):
        fixed.build(recs, NegativeCost({}), tmp_path / "sub", _profile(), max_retries=0)


def test_failure_limits_are_validated(tmp_path):
    recs = _recs([("v:m1:1", "ab cd")])
    with pytest.raises(ValueError, match="max_consecutive_errors"):
        fixed.build(recs, Scripted({"v:m1:1": _unresolved("v:m1:1")}), tmp_path / "sub", _profile(), max_consecutive_errors=0)
    with pytest.raises(ValueError, match="max_relation_drops"):
        fixed.build(recs, Scripted({"v:m1:1": _unresolved("v:m1:1")}), tmp_path / "sub", _profile(), max_relation_drops=0)


# Item 3: in-process base-consistency check


def _brute_unsat(n, clauses):
    for bits in itertools.product((False, True), repeat=n):
        if all(bits[x >> 1] != bool(x & 1) or bits[y >> 1] != bool(y & 1) for x, y in clauses):
            return False
    return True


def test_scc_2cnf_check_matches_brute_force_on_random_formulas():
    rng = random.Random(13)
    for _ in range(1500):
        n = rng.randint(1, 6)
        clauses = [(rng.randrange(2 * n), rng.randrange(2 * n)) for _ in range(rng.randint(0, 12))]
        assert fixed._unsat_2cnf(n, clauses) == _brute_unsat(n, clauses), clauses


def _contradiction(prefix, classes):
    """m1 = X, m2 = not X, m3 = Y with m2 => m1 (forces X), m1 => m3, m3 => m2 (Y => not X): unsatisfiable together."""
    bindings = {f"v:{prefix}1:1": _bound(classes[0]), f"v:{prefix}2:1": _bound(classes[0], negated=True), f"v:{prefix}3:1": _bound(classes[1])}
    relations = {(f"v:{prefix}1:1", f"v:{prefix}2:1"): "IMPLIED_BY", (f"v:{prefix}1:1", f"v:{prefix}3:1"): "IMPLIES",
                 (f"v:{prefix}2:1", f"v:{prefix}3:1"): "IMPLIED_BY"}
    return bindings, relations


def _two_groups():
    b1, r1 = _contradiction("a", ("X", "Y"))
    b2, r2 = _contradiction("b", ("Z", "W"))
    recs = _recs([(oid, "alpha" if oid.startswith("v:a") else "beta") for oid in list(b1) + list(b2)])
    return recs, {**b1, **b2}, {**r1, **r2}


def test_in_process_check_repairs_the_base_without_any_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: False)
    recs, bindings, relations = _two_groups()
    with pytest.warns(UserWarning, match="skipped"):
        summary = fixed.build(recs, Scripted(bindings, relations), tmp_path / "sub", _profile(), max_relation_drop_frac=1.0)
    assert summary["relations_dropped"] == 2  # one per contradictory group
    assert summary["base_check"] == "in_process" and summary["base_probe"] == "skipped"


def test_drop_cap_turns_an_unrepairable_base_into_a_typed_error(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: False)
    recs, bindings, relations = _two_groups()
    with pytest.raises(BaseInconsistentError, match="over --max-relation-drops 1"):
        fixed.build(recs, Scripted(bindings, relations), tmp_path / "sub", _profile(), max_relation_drops=1)
    assert not (tmp_path / "sub").exists()


def test_consistent_theory_needs_no_drops_and_no_engine_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: False)
    recs = _recs([("v:m1:1", "ab cd"), ("v:m2:1", "ab cd")])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    with pytest.warns(UserWarning, match="skipped"):
        summary = fixed.build(recs, Scripted(bindings, {("v:m1:1", "v:m2:1"): "EXCLUDES"}), tmp_path / "sub", _profile())
    assert summary["relations_dropped"] == 0 and summary["constraints"] == 1


def test_deadline_before_verification_is_a_loud_failure_and_keeps_the_checkpoint(tmp_path):
    # Four records: the contradiction is committed by the first three decisions; the fourth call is killed.
    recs = _recs([("v:m1:1", "alpha"), ("v:m2:1", "alpha"), ("v:m3:1", "alpha gamma"), ("v:m4:1", "gamma")])
    bindings = {"v:m1:1": _bound("X"), "v:m2:1": _bound("X", negated=True), "v:m3:1": _bound("Y"), "v:m4:1": _bound("Z")}
    relations = {("v:m1:1", "v:m2:1"): "IMPLIED_BY", ("v:m1:1", "v:m3:1"): "IMPLIES", ("v:m2:1", "v:m3:1"): "IMPLIED_BY"}
    asked = []

    class KillFourth(Scripted):
        def decide_relation(self, a, b):
            asked.append((a["id"], b["id"]))
            if len(asked) == 4:
                raise KeyboardInterrupt
            return super().decide_relation(a, b)

    with pytest.raises(KeyboardInterrupt):
        fixed.build(recs, KillFourth(bindings, relations), tmp_path / "sub", _profile(), checkpoint_every=1,
                    max_relation_drop_frac=1.0)
    work = tmp_path / "sub.build"
    assert work.is_dir()
    with pytest.raises(BaseCheckIncomplete, match="deadline"):
        fixed.finalize_checkpoint(work, tmp_path / "late", _profile(build_deadline_s=1e-9), max_relation_drop_frac=1.0)
    assert not (tmp_path / "late").exists()
    summary = fixed.finalize_checkpoint(work, tmp_path / "ok", _profile(), max_relation_drop_frac=1.0)
    assert summary["relations_dropped"] == 1 and summary["base_check"] == "in_process"


def test_engine_timeout_at_the_deadline_is_skipped_deadline_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: True)

    def timed_out(*args, **kwargs):
        raise engine.EngineError("mb-engine eval exceeded its 1s budget and was killed")

    monkeypatch.setattr(engine, "eval_submission", timed_out)
    recs = _recs([("v:m1:1", "ab cd"), ("v:m2:1", "ab cd")])
    bindings = {r["id"]: _unresolved(r["id"]) for r in recs}
    summary = fixed.build(recs, Scripted(bindings, {("v:m1:1", "v:m2:1"): "EXCLUDES"}), tmp_path / "sub", _profile())
    assert summary["base_probe"] == "skipped_deadline" and summary["constraints"] == 1


def test_engine_budget_is_the_time_left_in_the_build_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: True)
    budgets = []

    def record(submission, queries, answers, timeout_s=None):
        budgets.append(timeout_s)

    monkeypatch.setattr(engine, "eval_submission", record)
    recs = _recs([("v:m1:1", "ab cd")])
    summary = fixed.build(recs, Scripted({"v:m1:1": _unresolved("v:m1:1")}), tmp_path / "sub", _profile(build_deadline_s=500.0))
    assert summary["base_probe"] == "engine" and len(budgets) == 1 and 0 < budgets[0] <= 500.0


def test_engine_disagreeing_with_the_in_process_check_is_a_typed_error(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: True)

    def says_inconsistent(*args, **kwargs):
        raise engine.EngineError("mb-engine validate: inconsistent_base")

    monkeypatch.setattr(engine, "eval_submission", says_inconsistent)
    recs = _recs([("v:m1:1", "ab cd")])
    with pytest.raises(BaseInconsistentError, match="inconsistent_base"):
        fixed.build(recs, Scripted({"v:m1:1": _unresolved("v:m1:1")}), tmp_path / "sub", _profile())


def test_engine_infrastructure_errors_keep_their_type(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "engine_available", lambda: True)

    def broken(*args, **kwargs):
        raise engine.EngineInfraError("mb-engine exited 2: usage")

    monkeypatch.setattr(engine, "eval_submission", broken)
    recs = _recs([("v:m1:1", "ab cd")])
    with pytest.raises(engine.EngineInfraError):
        fixed.build(recs, Scripted({"v:m1:1": _unresolved("v:m1:1")}), tmp_path / "sub", _profile())


class _Theory:
    """The rows and relation log of a theory, shaped like a checkpoint replay for _BaseCheck."""

    def __init__(self, ids, rows, relations):
        self.ids, self._rows, self._relations = ids, rows, relations

    def rows(self):
        return iter(self._rows)

    def relations(self):
        return iter((i, j, rel, -1) for i, j, rel in self._relations)  # (i, j, relation, representative line)


def test_exact_two_sat_agrees_with_mb_engine_on_random_theories(tmp_path):
    if not HAVE_ENGINE:
        pytest.skip("mb-engine not built")
    rng = random.Random(21)
    queries = tmp_path / "empty.jsonl"
    queries.write_text("", encoding="utf-8")
    disagreements = 0
    unsat_seen = 0
    for case in range(60):
        n = rng.randint(2, 5)
        ids = [f"v:m{i}:1" for i in range(1, n + 1)]
        rows = []
        for oid in ids:
            if rng.random() < 0.7:
                rows.append({"id": oid, "status": "bound", "class": rng.choice(["X", "Y"]), "negated": rng.random() < 0.5, "missing": []})
            else:
                rows.append({"id": oid, "status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]})
        relations = []
        for i, j in itertools.combinations(range(n), 2):
            if rng.random() < 0.6:
                relations.append((i, j, rng.choice(["IMPLIES", "IMPLIED_BY", "EXCLUDES", "EQUIV"])))
        exact_unsat = fixed._BaseCheck(_Theory(ids, rows, relations)).unsat(len(relations), set())
        out = tmp_path / f"case{case}"
        cons = [{"id": f"p{k}", "expr": fixed._relation_expr(rel, ids[i], ids[j]), "interps": None} for k, (i, j, rel) in enumerate(relations)]
        ir.write_submission(out, rows, cons, ["base"], {"calls": []})
        try:
            engine.eval_submission(out, queries, tmp_path / f"answers{case}.jsonl")
            engine_unsat = False
        except engine.EngineError as exc:
            assert "inconsistent_base" in str(exc), exc
            engine_unsat = True
        unsat_seen += exact_unsat
        disagreements += exact_unsat != engine_unsat
    assert disagreements == 0
    assert unsat_seen >= 5  # the random theories exercise both verdicts


# Item 4: symmetric blocking


def test_every_units_best_partner_is_a_candidate_even_when_it_is_earlier():
    rng = random.Random(4)
    pool = [f"tok{i}" for i in range(40)]
    texts = [" ".join(rng.sample(pool, 2)) for _ in range(60)]
    recs = [{"question": t} for t in texts]
    n = len(texts)
    df = {}
    for t in texts:
        for tok in set(t.split()):
            df[tok] = df.get(tok, 0) + 1
    cands = {frozenset(p) for p in candidate_pairs(recs, top_k=1)}
    for i in range(n):
        best, best_score = None, 0.0
        for j in range(n):
            if j == i:
                continue
            shared = set(texts[i].split()) & set(texts[j].split())
            score = sum(math.log(n / df[tok]) for tok in shared)
            if score > best_score:  # ties: the lower index wins, as in the ranking
                best, best_score = j, score
        if best is not None:
            assert frozenset((i, best)) in cands, (i, best)


def test_each_candidate_pair_is_emitted_once():
    rng = random.Random(8)
    pool = [f"w{i}" for i in range(15)]
    texts = [" ".join(rng.sample(pool, 3)) for _ in range(80)]
    pairs = list(iter_candidate_pairs(lambda: iter(texts), len(texts), top_k=20))
    assert len(pairs) == len(set(pairs)) and all(i < j for i, j in pairs)


# Item 5: verdict reading


def test_verdicts_require_the_json_boolean_and_a_unique_qid(tmp_path):
    p = tmp_path / "v.jsonl"
    p.write_text(
        '{"qid": "a", "cert_valid": true, "reason": "ok"}\n'
        '{"qid": "b", "cert_valid": "false", "reason": "ok"}\n'
        '{"qid": "c", "cert_valid": 1}\n'
        '{"qid": "d", "cert_valid": false}\n'
        '{"qid": "d", "cert_valid": true}\n'
        '{"qid": "e", "cert_valid": true}\n'
        '{"qid": "e", "cert_valid": true}\n'
        "not json\n"
        '{"cert_valid": true}\n',
        encoding="utf-8",
    )
    assert scoring.read_verdicts(p) == {"a": True, "b": False, "c": False, "d": False, "e": False}


def test_verdicts_feed_scoring_as_absent_or_invalid_certificates(tmp_path):
    p = tmp_path / "v.jsonl"
    p.write_text('{"qid": "f1", "cert_valid": "true"}\n{"qid": "f1", "cert_valid": true}\n', encoding="utf-8")
    verdicts = scoring.read_verdicts(p)
    queries = [{"qid": "f1", "family": "feasibility"}]
    gold = {"f1": {"qid": "f1", "decision": "SAT"}}
    answers = {"f1": {"qid": "f1", "decision": "SAT"}}
    assert score_run_utility(queries, answers, gold, verdicts) == 0.0


def score_run_utility(queries, answers, gold, verdicts):
    return scoring.score_run(queries, answers, gold, verdicts)["families"]["feasibility"]["mean_utility"]


def test_unresolved_with_gold_listing_no_code_scores_zero_for_any_reason():
    gold = {"decision": "UNRESOLVED", "missing": []}
    for reason in (["adapter_error"], ["malformed_decision"], ["no_date"]):
        assert scoring.feasibility_utility({"decision": "UNRESOLVED", "missing": reason}, gold, True) == 0.0
    assert scoring.feasibility_utility({"decision": "UNRESOLVED", "missing": []}, gold, True) == 1.0


def test_engine_confirmation_clamps_a_huge_deadline_to_a_valid_timeout(tmp_path, monkeypatch):
    import threading

    monkeypatch.setattr(engine, "engine_available", lambda: True)
    budgets = []
    monkeypatch.setattr(engine, "eval_submission", lambda *a, **k: budgets.append(k["timeout_s"]))
    recs = _recs([("v:m1:1", "ab cd")])
    summary = fixed.build(recs, Scripted({"v:m1:1": _unresolved("v:m1:1")}), tmp_path / "sub",
                          _profile(build_deadline_s=2 * threading.TIMEOUT_MAX + 1))
    assert summary["base_probe"] == "engine" and budgets == [threading.TIMEOUT_MAX]

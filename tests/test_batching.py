"""Protocol v2 batching: the batch envelope of a fake child, hello with and without batch support, per-item errors that
are retried alone, count mismatches as protocol errors, batch overshoot discarded as a whole, usage items and
gpu_seconds, and batched builds equal to single-call builds (theory, usage totals)."""

import json
import sys
import time
from pathlib import Path

import pytest

from mentat_bench.adapters.base import AdapterError, check_usage, usage
from mentat_bench.adapters.local_jev import LocalJSONLAdapter
from mentat_bench.gateway import BudgetExceeded, Gateway, UsageError, usage_totals, validate_usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from irval import check_submission  # noqa: E402

# One child for every mode. Per item it reports tokens 5/2, cost 0.01 and gpu 0.1 (costly: cost 0.5). Binding: bound to
# the market of "Will <market> ..." questions, negated for label No; relation: IMPLIES by id order; group: the first two.
CHILD = r'''
import json, sys
mode = sys.argv[1]
seen = set()
COST = 0.5 if mode == "costly" else 0.01

def binding(ctx):
    parts = ctx["question"].split()
    if parts[0] == "Will" and ctx.get("label") in ("Yes", "No"):
        return {"status": "bound", "class": "c_" + parts[1], "negated": ctx["label"] == "No", "missing": []}
    return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}

def item(op, body):
    if op == "decide_binding":
        return binding(body["ctx"])
    if op == "decide_relation":
        return {"relation": "IMPLIES" if body["a"]["id"] < body["b"]["id"] else "NONE"}
    ids = [m["id"] for m in body["ctx"]["members"]]
    return {"groups": [{"kind": "at_most_one", "members": ids[:2]}] if len(ids) >= 2 else []}

def flaky(body):
    key = json.dumps(body, sort_keys=True)
    if "flaky" in key and key not in seen:
        seen.add(key)
        return True
    return False

def hello():
    ops = {"decide_binding": {"max_batch": 4}, "decide_relation": {"max_batch": 8},
           "decide_group": {"max_members": 64, "max_batch": 2}}
    if mode in ("batch", "flaky", "mismatch", "costly", "badresult"):
        ops["batch"] = {"max_calls": 16}
    return {"protocol": 2, "name": "fake", "version": "0", "ops": ops}

for line in sys.stdin:
    req = json.loads(line)
    op = req["op"]
    if op == "hello":
        out = hello()
    elif op == "batch":
        results, cost_total, n = [], 0.0, len(req["calls"])
        for call in req["calls"]:
            if mode == "flaky" and flaky(call):
                results.append({"error": "transient"})
                continue
            results.append(item(call["op"], call))
            cost_total += COST
        if mode == "mismatch":
            results = results[:-1]
        if mode == "badresult":
            results[0] = "not an object"
        out = {"results": results, "tokens_in": 5 * n, "tokens_out": 2 * n, "cost_usd": round(cost_total, 6),
               "gpu_seconds": 0.1 * n}
    else:
        body = {k: v for k, v in req.items() if k not in ("op", "id")}
        if op == "decide_binding" and mode == "single_fail":
            out = {"error": "unused"}
        else:
            out = item(op, body)
        out.update({"tokens_in": 5, "tokens_out": 2, "cost_usd": COST, "gpu_seconds": 0.1})
    out["id"] = req["id"]
    print(json.dumps(out), flush=True)
'''


def _child(tmp_path, mode):
    script = tmp_path / f"batch_child_{mode}.py"
    script.write_text(CHILD, encoding="utf-8")
    return [sys.executable, str(script), mode]


def _ctx(oid, question, label="Yes"):
    return {"id": oid, "question": question, "label": label, "event_title": "E"}


def _records():
    return [_ctx("v:a1:1", "Will A1 win?"), _ctx("v:a1:2", "Will A1 win?", "No"),
            _ctx("v:b2:1", "Will B2 win?"), _ctx("v:b2:2", "Will B2 win?", "No"),
            _ctx("v:c3:1", "Will C3 win?"), _ctx("v:flaky4:1", "Will flaky4 win?")]


def _theory(out):
    return json.loads((Path(out) / "theory.json").read_text(encoding="utf-8"))


# ---- hello -----------------------------------------------------------------------------------------------------

def test_hello_advertises_batch_and_per_op_max_batch(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "batch"), timeout_s=20, protocol=2) as adapter:
        assert adapter.supports_batch is True and adapter.supports_groups is True
        # per-op max_batch, capped by the batch envelope's max_calls (16 here, so nothing is capped)
        assert adapter.advertised_max_batch == {"decide_binding": 4, "decide_relation": 8, "decide_group": 2}


def test_hello_without_batch_keeps_single_calls(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "single"), timeout_s=20, protocol=2) as adapter:
        assert adapter.supports_batch is False and adapter.supports_groups is True
        assert adapter.advertised_max_batch == {}


def test_protocol_1_never_batches(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "batch"), timeout_s=20, protocol=1) as adapter:
        assert adapter.supports_batch is False


# ---- decide_batch and the gateway -------------------------------------------------------------------------------

def test_batch_answers_each_item_and_charges_one_aggregate_record(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "batch"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=60.0)
        results = gw.decide_batch("decide_binding", [(c,) for c in _records()[:3]])
    assert [r["status"] for r in results] == ["bound", "bound", "bound"]
    assert results[1]["negated"] is True
    (call,) = gw.calls  # one usage entry for the three items
    assert call["items"] == 3 and call["tokens_in"] == 15 and call["tokens_out"] == 6
    assert call["cost_usd"] == pytest.approx(0.03) and call["gpu_seconds"] == pytest.approx(0.3)
    assert call["retry"] is False and call["model"] == "local"


def test_per_item_error_is_retried_alone(tmp_path):
    """Only the flaky item is sent again: the second entry carries one item and is a retry."""
    with LocalJSONLAdapter(_child(tmp_path, "flaky"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=60.0)
        results = gw.decide_batch("decide_binding", [(c,) for c in _records()])
    assert all(isinstance(r, dict) and r.get("status") in ("bound", "unresolved") for r in results)
    first, second = gw.calls
    assert first["items"] == 6 and first["retry"] is False
    assert second["items"] == 1 and second["retry"] is True


def test_item_errors_left_after_the_retries_are_returned_per_item(tmp_path):
    """With no retries left, an item that failed is returned as an AdapterError for that item alone."""
    records = [_ctx("v:flaky1:1", "Will flaky1 win?")]
    with LocalJSONLAdapter(_child(tmp_path, "flaky"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=60.0, max_retries=0)
        (outcome,) = gw.decide_batch("decide_binding", [(records[0],)])
    assert isinstance(outcome, AdapterError) and "transient" in str(outcome)
    assert gw.calls[0]["items"] == 1


def test_count_mismatch_is_a_protocol_error_that_kills_the_child(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "mismatch"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=60.0, max_retries=1)
        outcomes = gw.decide_batch("decide_binding", [(c,) for c in _records()[:3]])
    assert len(outcomes) == 3 and all(isinstance(o, AdapterError) for o in outcomes)
    assert "results" in str(outcomes[0])
    assert len(gw.calls) == 2 and all(c["items"] == 3 for c in gw.calls)  # both attempts charged for their items


def test_non_object_batch_result_is_a_protocol_error(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "badresult"), timeout_s=20, protocol=2) as adapter:
        with pytest.raises(AdapterError, match="not an object"):
            adapter.decide_batch([("decide_binding", (_records()[0],)), ("decide_binding", (_records()[1],))])


def test_batch_overshooting_the_spend_ceiling_is_discarded_whole(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "costly"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=0.8, build_deadline_s=60.0)
        with pytest.raises(BudgetExceeded, match="discarded"):
            gw.decide_batch("decide_binding", [(c,) for c in _records()[:3]])  # 3 x 0.5 USD overshoots 0.8
    (call,) = gw.calls
    assert call["items"] == 3 and call["cost_usd"] == pytest.approx(1.5)


def test_batch_with_empty_items_is_a_value_error(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "batch"), timeout_s=20, protocol=2) as adapter:
        with pytest.raises(ValueError, match="at least one"):
            adapter.decide_batch([])


# ---- usage fields -----------------------------------------------------------------------------------------------

def test_usage_items_and_gpu_seconds_are_optional_and_checked():
    base = {"model": "m", "kind": "local", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0}
    assert "items" not in check_usage(base)
    assert check_usage({**base, "items": 3, "gpu_seconds": 0.5})["items"] == 3
    for bad in ({"items": 0}, {"items": 1.5}, {"items": None}, {"gpu_seconds": -1.0}, {"gpu_seconds": None}):
        with pytest.raises(AdapterError):
            check_usage({**base, **bad})


def test_validate_usage_and_totals_count_items_and_gpu_seconds():
    profile = dict(DEFAULT_PROFILE)
    calls = [
        {"model": "m", "kind": "local", "tokens_in": 4, "tokens_out": 1, "cost_usd": 0.1, "seconds": 0.2,
         "items": 4, "gpu_seconds": 0.4, "retry": False},
        {"model": "m", "kind": "local", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "seconds": 0.1,
         "retry": True},
    ]
    doc = {"calls": calls, "wall_seconds": 1.0, "peak_rss_mb": 1.0}
    totals = validate_usage(doc, profile)
    assert totals["calls"] == 2 and totals["items"] == 5 and totals["gpu_seconds"] == pytest.approx(0.4)
    assert usage_totals(doc)["items"] == 5
    with pytest.raises(Exception):
        validate_usage({"calls": [{**calls[0], "items": 0}], "wall_seconds": 1.0, "peak_rss_mb": 1.0}, profile)


def test_irval_accepts_the_optional_usage_keys():
    usage_doc = {"calls": [{"model": "m", "kind": "local", "tokens_in": 1, "tokens_out": 1, "seconds": 0.1,
                            "cost_usd": 0.0, "retry": False, "items": 2, "gpu_seconds": 0.2}],
                 "wall_seconds": 1.0, "peak_rss_mb": 1.0}
    # check_submission reads a directory, so a minimal submission is written around the usage document
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "bindings.jsonl").write_text("", encoding="utf-8")
        (d / "provenance.jsonl").write_text("", encoding="utf-8")
        (d / "theory.json").write_text(json.dumps({"interpretations": ["base"], "aliases": {}, "constraints": []}), encoding="utf-8")
        (d / "usage.json").write_text(json.dumps(usage_doc), encoding="utf-8")
        assert check_submission(d) == []


# ---- batched builds equal single-call builds ---------------------------------------------------------------------

def test_batched_build_equals_single_call_build(tmp_path):
    """Same answers, same theory, same usage items, tokens, cost and gpu seconds; fewer usage entries when batched."""
    summaries, theories, totals = {}, {}, {}
    for mode in ("single", "batch"):
        out = tmp_path / mode
        with LocalJSONLAdapter(_child(tmp_path, mode), timeout_s=20, protocol=2) as adapter:
            summaries[mode] = fixed.build(_records(), adapter, out, dict(DEFAULT_PROFILE), checkpoint_every=4)
        theories[mode] = _theory(out)
        totals[mode] = usage_totals(json.loads((out / "usage.json").read_text(encoding="utf-8")))
    assert theories["single"] == theories["batch"]
    assert summaries["single"]["relations_committed"] == summaries["batch"]["relations_committed"] > 0
    s, b = totals["single"], totals["batch"]
    assert s["items"] == b["items"] and s["tokens_in"] == b["tokens_in"] and s["tokens_out"] == b["tokens_out"]
    assert s["cost_usd"] == pytest.approx(b["cost_usd"]) and s["gpu_seconds"] == pytest.approx(b["gpu_seconds"])
    assert b["calls"] < s["calls"] == s["items"]
    assert summaries["batch"]["items"] == summaries["single"]["items"] == s["items"]
    assert summaries["batch"]["calls"] < summaries["single"]["calls"]
    assert check_submission(tmp_path / "batch") == []


def test_batch_size_flag_of_one_uses_single_calls(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "batch"), timeout_s=20, protocol=2) as adapter:
        summary = fixed.build(_records(), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), batch_size=1)
    assert summary["calls"] == summary["items"]
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text())["batch_size"] == 1


def test_build_under_a_batch_child_keeps_the_health_counters(tmp_path):
    """A failed item is retried alone, and every outcome is still decided by the binding phase."""
    with LocalJSONLAdapter(_child(tmp_path, "flaky"), timeout_s=20, protocol=2) as adapter:
        summary = fixed.build(_records(), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["adapter_fatal"] is False
    decided = [json.loads(line) for line in (tmp_path / "sub" / "bindings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(decided) == len(_records())  # every outcome decided once the retry succeeded


# ---- --max-calls: items_left counts every charged item, retries included -----------------------------------------------

class _FailOnceSingle:
    """The first decide_binding call fails with a transient error; the next ones answer."""

    name = "once"

    def __init__(self):
        self.calls = 0

    def decide_binding(self, ctx):
        self.calls += 1
        if self.calls == 1:
            raise AdapterError("transient", usage=usage(self.name))
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}, usage(self.name)


def test_a_single_call_retry_runs_only_if_it_fits_in_items_left(tmp_path):
    gw = Gateway(_FailOnceSingle(), spend_ceiling_usd=10.0, build_deadline_s=60.0, items_left=1)
    with pytest.raises(AdapterError, match="failed after 1 attempts"):
        gw.decide_binding(_records()[0])  # the first attempt takes the one item; the retry does not fit
    assert gw.items_left == 0 and len(gw.calls) == 1

    gw = Gateway(_FailOnceSingle(), spend_ceiling_usd=10.0, build_deadline_s=60.0, items_left=2)
    assert gw.decide_binding(_records()[0])["status"] == "unresolved"
    assert gw.items_left == 0 and [c["retry"] for c in gw.calls] == [False, True]


def test_a_batch_retry_runs_only_if_all_its_items_fit(tmp_path):
    """Two items, one of which fails once: with 2 items left the retry (one item) does not fit; with 3 it does."""
    records = [_ctx("v:flaky1:1", "Will flaky1 win?"), _ctx("v:a2:1", "Will A2 win?")]
    with LocalJSONLAdapter(_child(tmp_path, "flaky"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=60.0, items_left=2)
        first, second = gw.decide_batch("decide_binding", [(c,) for c in records])
        assert isinstance(first, AdapterError) and second["status"] == "bound"
        assert len(gw.calls) == 1 and gw.items_left == 0

    with LocalJSONLAdapter(_child(tmp_path, "flaky"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=60.0, items_left=3)
        outcomes = gw.decide_batch("decide_binding", [(c,) for c in records])
        assert all(isinstance(o, dict) for o in outcomes)
        assert [c["items"] for c in gw.calls] == [2, 1] and gw.items_left == 0


def test_the_first_attempt_is_not_refused_by_items_left(tmp_path):
    """Bindings are never cut: a call is always made once, whatever is left (items_left may go negative)."""
    with LocalJSONLAdapter(_child(tmp_path, "batch"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=60.0, items_left=1)
        results = gw.decide_batch("decide_binding", [(c,) for c in _records()[:3]])
    assert [r["status"] for r in results] == ["bound", "bound", "bound"]
    assert gw.items_left == -2


def test_a_batch_retry_leaves_the_reserve_for_the_bindings_not_yet_asked(tmp_path):
    """Two items, one failing once, 3 items left, and a reserve of 2 for bindings not yet asked: the retry (one item) would
    take an item a later binding needs, so it is not made and the failed item keeps its error."""
    records = [_ctx("v:flaky1:1", "Will flaky1 win?"), _ctx("v:a2:1", "Will A2 win?")]
    with LocalJSONLAdapter(_child(tmp_path, "flaky"), timeout_s=20, protocol=2) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=60.0, items_left=3, reserve=2)
        first, second = gw.decide_batch("decide_binding", [(c,) for c in records])
    assert isinstance(first, AdapterError) and second["status"] == "bound"
    assert len(gw.calls) == 1 and gw.items_left == 1 and [c["retry"] for c in gw.calls] == [False]


class _OpStub:
    """A fake adapter answering every decision with one item and one usage entry of its own."""

    name = "stub"

    def decide_binding(self, outcome_context):
        return {"status": "unresolved", "missing": ["x"]}, usage("stub")

    def decide_relation(self, a_context, b_context):
        raise AdapterError("transient", usage("stub"))

    def decide_batch(self, calls):
        return [{"status": "unresolved", "missing": ["x"]} for _ in calls], usage("stub")


def test_each_usage_entry_names_the_decision_op_it_charges():
    """Single calls, a batch and a failed call (its entry too) each record the decision method as `op`."""
    gw = Gateway(_OpStub(), spend_ceiling_usd=10.0, build_deadline_s=60.0, max_retries=0)
    gw.decide_binding({})
    with pytest.raises(AdapterError):
        gw.decide_relation({}, {})
    gw.decide_batch("decide_group", [({},), ({},)])
    assert [c["op"] for c in gw.calls] == ["decide_binding", "decide_relation", "decide_group"]
    assert gw.calls[2]["items"] == 2


def test_validate_usage_accepts_a_known_op_or_none_and_refuses_another(tmp_path):
    base = {"model": "m", "kind": "local", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "seconds": 0.1, "retry": False}
    doc = {"wall_seconds": 1.0, "peak_rss_mb": 1.0}
    assert validate_usage({**doc, "calls": [base, {**base, "op": "decide_relation"}]}, dict(DEFAULT_PROFILE))["calls"] == 2
    with pytest.raises(UsageError, match="unknown op"):
        validate_usage({**doc, "calls": [{**base, "op": "decide_nope"}]}, dict(DEFAULT_PROFILE))


class _SleepyStub(_OpStub):
    def decide_binding(self, outcome_context):
        time.sleep(0.002)  # well under the 15.6 ms tick of time.monotonic on Windows
        return super().decide_binding(outcome_context)


def test_a_call_shorter_than_a_clock_tick_still_records_its_seconds():
    gw = Gateway(_SleepyStub(), spend_ceiling_usd=10.0, build_deadline_s=60.0)
    gw.decide_binding({})
    assert gw.calls[0]["seconds"] > 0

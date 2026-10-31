"""Batch failures in the health counters, the protocol 2 hello (its own timeout, the recorded reason, the model identity),
the groups-off refusal of the build, and the envelope usage of a batch whose answers do not match its items."""

import json
import sys
from collections import Counter

import pytest

from mentat_bench import cli
from mentat_bench.adapters.base import AdapterError, AdapterFatal, usage
from mentat_bench.adapters.local_jev import LocalJSONLAdapter
from mentat_bench.gateway import Gateway
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

# A protocol 2 child whose hello misbehaves on request: slowhello (answers after 2 s), helloerror (an error answer),
# nogroups (no decide_group), modelhello (a model block). Everything else answers single calls at once.
CHILD = r'''
import json, sys, time
mode = sys.argv[1]
ops = {"decide_binding": {"max_batch": 4}, "decide_relation": {"max_batch": 8},
       "decide_group": {"max_members": 64}}
for line in sys.stdin:
    req = json.loads(line)
    op = req["op"]
    if op == "hello":
        if mode == "slowhello":
            time.sleep(2)
        if mode == "helloerror":
            out = {"error": "not a protocol 2 child"}
        else:
            hello_ops = dict(ops)
            if mode == "nogroups":
                del hello_ops["decide_group"]
            out = {"protocol": 2, "name": "fake", "version": "0", "ops": hello_ops}
            if mode == "modelhello":
                out["model"] = {"id": "tiny-test", "revision": "r1", "device": "cpu"}
    elif op == "decide_binding":
        out = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
    elif op == "decide_relation":
        out = {"relation": "NONE"}
    else:
        out = {"groups": []}
    out.update({"tokens_in": 5, "tokens_out": 2, "cost_usd": 0.01})
    out["id"] = req["id"]
    print(json.dumps(out), flush=True)
'''


def _child(tmp_path, mode):
    script = tmp_path / f"hello_child_{mode}.py"
    script.write_text(CHILD, encoding="utf-8")
    return [sys.executable, str(script), mode]


def _ctx(oid, question, label="Yes"):
    return {"id": oid, "question": question, "label": label, "event_title": "E"}


def _records(n=4):
    return [_ctx(f"v:m{i}:1", f"Will X{i} win?") for i in range(1, n + 1)]


def _corpus(tmp_path, n=4):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(json.dumps({"id": f"v:m{i}:1", "text": f"Will X{i} win?", "label": "Yes", "event_title": "E"})
                              + "\n" for i in range(1, n + 1)), encoding="utf-8")
    return corpus


def _local_build(tmp_path, mode, *extra):
    return ["build", "--adapter", "local", "--local-arg", sys.executable, "--local-arg", str(tmp_path / f"hello_child_{mode}.py"),
            "--local-arg", mode, "--local-protocol", "2", "--corpus", str(_corpus(tmp_path)),
            "--out", str(tmp_path / "out"), *extra]


def _write_child(tmp_path, mode):
    (tmp_path / f"hello_child_{mode}.py").write_text(CHILD, encoding="utf-8")


# ---- hello: its own timeout, its recorded reason, the model identity -----------------------------------------------

def test_a_slow_hello_times_out_and_records_hello_timeout(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "slowhello"), timeout_s=20, protocol=2, hello_timeout_s=0.5) as adapter:
        assert adapter.supports_groups is False and adapter.protocol == 1
        assert adapter.groups_disabled_reason == "hello_timeout"
        # the next request starts a fresh child, which answers the single call
        assert adapter.decide_binding(_records()[0])[0]["status"] == "unresolved"


def test_a_hello_error_keeps_the_child_and_records_hello_error(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "helloerror"), timeout_s=20, protocol=2) as adapter:
        assert adapter.supports_groups is False and adapter.groups_disabled_reason == "hello_error"
        assert adapter.decide_binding(_records()[0])[0]["status"] == "unresolved"


def test_a_hello_without_decide_group_records_no_decide_group(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "nogroups"), timeout_s=20, protocol=2) as adapter:
        assert adapter.groups_disabled_reason == "no_decide_group"


def test_a_successful_hello_clears_the_reason_and_names_the_model(tmp_path):
    with LocalJSONLAdapter(_child(tmp_path, "modelhello"), timeout_s=20, protocol=2) as adapter:
        assert adapter.groups_disabled_reason is None and adapter.supports_groups is True
        summary = fixed.build(_records(), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    policy = json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["model"] == {"id": "tiny-test", "revision": "r1", "device": "cpu"}
    assert summary["groups_disabled_reason"] is None
    models = {c["model"] for c in json.loads((tmp_path / "sub" / "usage.json").read_text(encoding="utf-8"))["calls"]}
    assert models == {"local:tiny-test"}


def test_protocol_2_build_whose_hello_fails_is_refused_with_exit_3(tmp_path, capsys):
    _write_child(tmp_path, "slowhello")
    assert cli.main(_local_build(tmp_path, "slowhello", "--hello-timeout", "0.5")) == cli.EXIT_BUILD_INCOMPLETE
    assert "hello failed (hello_timeout)" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_allow_groups_off_builds_without_groups_and_records_the_reason(tmp_path, capsys):
    _write_child(tmp_path, "slowhello")
    assert cli.main(_local_build(tmp_path, "slowhello", "--hello-timeout", "0.5", "--allow-groups-off")) == 0
    assert "warning: groups are off" in capsys.readouterr().err
    policy = json.loads((tmp_path / "out" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["groups_disabled_reason"] == "hello_timeout" and policy["groups"] is False


def test_no_groups_flag_is_the_reason_even_when_the_hello_fails(tmp_path):
    _write_child(tmp_path, "slowhello")
    assert cli.main(_local_build(tmp_path, "slowhello", "--hello-timeout", "0.5", "--no-groups")) == 0
    policy = json.loads((tmp_path / "out" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["groups_disabled_reason"] == "flag"


# ---- batch failures: one failed call is one failure in a row --------------------------------------------------------

class _FirstBatchFails:
    """A batch adapter whose first batch of each op fails as a whole (every attempt of the gateway): its items share one
    AdapterError. Later batches answer: unresolved bindings, NONE relations, no groups."""

    name = "first-fails"
    supports_batch = True
    supports_groups = True
    advertised_max_batch = {"decide_binding": 64, "decide_relation": 64, "decide_group": 64}

    def __init__(self):
        self.batches = Counter()

    def decide_batch(self, calls, timeout_s=None):
        op = calls[0][0]
        self.batches[op] += 1
        if self.batches[op] <= 3:  # the gateway's max_retries=2 gives up on this batch
            raise AdapterError(f"{op} batch failed")
        answers = {"decide_binding": {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]},
                   "decide_relation": "NONE", "decide_group": []}
        return [answers[op] for _ in calls], usage(self.name, "local", 1, 1, 0.0)

    def decide_group(self, ctx, timeout_s=None):
        return [], usage(self.name)


def test_a_failed_batch_is_one_failed_call_for_the_health_counter():
    health = fixed._AdapterHealth(20)
    shared = AdapterError("batch gone")
    fixed._book_errors([shared] * 64, health)
    assert (health.run, health.failures, health.decisions) == (1, 64, 64)
    fixed._book_errors([AdapterError("a"), AdapterError("b")], health)  # two single failures
    assert health.run == 3
    with pytest.raises(AdapterFatal):
        for _ in range(20):
            health.record_failed_call(1)


def test_one_failed_25_item_batch_does_not_trip_the_consecutive_limit(tmp_path):
    """H2: a 25-item binding batch that fails as a whole, then relation and group batches that fail once, must not abort
    the build (per-item counting would have been 25 failures in a row, over MAX_CONSECUTIVE_ERRORS=20)."""
    records = [_ctx(f"v:q{k}:1", f"Will Q{k} win?") for k in range(25)]  # one event of 25 outcomes
    summary = fixed.build(records, _FirstBatchFails(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["adapter_fatal"] is False and summary["groups_fatal"] is False
    decided = [json.loads(line) for line in (tmp_path / "sub" / "bindings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(decided) == 25
    assert all(row["missing"] == ["adapter_error"] for row in decided)  # the one batch that failed as a whole
    assert summary["relations_committed"] == summary["candidate_pairs"] > 0  # later relation batches were answered


def test_a_count_mismatch_is_charged_with_the_envelope_usage():
    """L2: the usage of an envelope whose answers do not match its items is charged with the failure."""

    class Short:
        name = "short"

        def decide_batch(self, calls, timeout_s=None):
            answer = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
            return [answer], usage(self.name, "local", 7, 2, 0.25)

    gw = Gateway(Short(), spend_ceiling_usd=10.0, build_deadline_s=60.0, max_retries=0)
    results = gw.decide_batch("decide_binding", [(_ctx("v:a:1", "Will A win?"),), (_ctx("v:b:1", "Will B win?"),)])
    assert all(isinstance(r, AdapterError) for r in results)
    (call,) = gw.calls
    assert call["tokens_in"] == 7 and call["cost_usd"] == pytest.approx(0.25) and call["items"] == 2


@pytest.mark.parametrize("mode, reason", [("helloerror", "hello_error"), ("nogroups", "no_decide_group")])
def test_the_refusal_only_suggests_a_timeout_for_a_timeout(tmp_path, capsys, mode, reason):
    """A longer --hello-timeout cannot help an error answer or a missing decide_group, so the message says so."""
    _write_child(tmp_path, mode)
    assert cli.main(_local_build(tmp_path, mode, "--hello-timeout", "5")) == cli.EXIT_BUILD_INCOMPLETE
    err = capsys.readouterr().err
    assert f"hello failed ({reason})" in err and "--allow-groups-off" in err
    assert "raise --hello-timeout" not in err and "does not help" in err

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers_pa_engine import make_fake_engine
from mentat_bench import cli, engine, ir
from mentat_bench.adapters.base import AdapterError, AdapterFatal, check_binding, check_relation, usage
from mentat_bench.adapters import local_jev
from mentat_bench.adapters.local_jev import LocalJSONLAdapter
from mentat_bench.gateway import Gateway
from mentat_bench.manifest import DEFAULT_PROFILE, ProfileError, load_profile, validate_profile
from mentat_bench.seal import INCOMPLETE_FILE, seal

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "toy10k"
CTX = {"id": "toy:m1:1", "question": "q", "label": "Yes", "event_title": None}

ECHO_PROCESS = r'''
import json, sys
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "decide_binding":
        if req["ctx"]["id"].endswith(":bad"):
            print(json.dumps({"error": "boom"}), flush=True)
            continue
        out = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"],
               "tokens_in": 3, "tokens_out": 1, "cost_usd": 0.01}
    else:
        out = {"relation": "EXCLUDES" if req["a"]["id"] < req["b"]["id"] else "NONE"}
    print(json.dumps(out), flush=True)
'''

HANG_PROCESS = r'''
import json, sys, time
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "decide_binding" and req["ctx"]["id"].endswith(":hang"):
        time.sleep(60)
    if req["op"] == "decide_binding" and req["ctx"]["id"].endswith(":err"):
        print(json.dumps({"error": "rate limited", "tokens_in": 11, "tokens_out": 2, "cost_usd": 0.05}), flush=True)
        continue
    print(json.dumps({"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}), flush=True)
'''

# Each mode misbehaves in one protocol way. The request id is echoed unless the mode says otherwise.
PROTOCOL_CHILD = r'''
import json, sys
mode = sys.argv[1]
for line in sys.stdin:
    req = json.loads(line)
    ok = {"status": "unresolved", "class": None, "negated": False, "missing": ["m"], "id": req["id"]}
    if mode == "nan":
        sys.stdout.write('{"status":"unresolved","class":null,"negated":false,"missing":["m"],"cost_usd":NaN}\n')
    elif mode == "wrongid":
        sys.stdout.write(json.dumps(dict(ok, id=req["id"] + 1)) + "\n")
    elif mode == "list":
        sys.stdout.write("[1, 2]\n")
    elif mode == "badutf8":
        sys.stdout.buffer.write(b"\xff\xfe\n")
    elif mode == "extra":
        sys.stdout.write(json.dumps(ok) + "\n" + json.dumps(dict(ok, id=0)) + "\n")
    elif mode == "partial":
        sys.stdout.write(json.dumps(dict(ok, tokens_in=7, cost_usd=-2.0)) + "\n")
    elif mode == "negcost":
        sys.stdout.write(json.dumps(dict(ok, cost_usd=-5.0)) + "\n")
    elif mode == "surrogate":
        sys.stdout.write(json.dumps(dict(ok, missing=[req["ctx"]["question"]])) + "\n")
    elif mode == "env":
        sys.stdout.write(json.dumps(dict(ok, missing=[__import__("os").environ.get("PYTHONUTF8", ""), __import__("os").environ.get("PYTHONIOENCODING", "")])) + "\n")
    sys.stdout.flush()
'''

GRANDCHILD = r'''
import os, subprocess, sys, time
subprocess.Popen([sys.executable, "-c", "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(120)", sys.argv[1]])
for line in sys.stdin:
    time.sleep(120)
'''


def _argv(tmp_path, source, name="child.py", *args):
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    return [sys.executable, str(script), *args]


def _alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_local_adapter_json_lines_roundtrip(tmp_path):
    with LocalJSONLAdapter(_argv(tmp_path, ECHO_PROCESS, "echo.py"), name="echo") as adapter:
        decision, use = adapter.decide_binding({"id": "toy:m1:1", "question": "q", "label": "Yes", "event_title": None})
        assert check_binding(decision)["status"] == "unresolved"
        assert use["tokens_in"] == 3 and use["cost_usd"] == pytest.approx(0.01) and use["kind"] == "local"
        rel, _ = adapter.decide_relation({"id": "toy:a:1"}, {"id": "toy:b:1"})
        assert check_relation(rel) == "EXCLUDES"
        gw = Gateway(adapter, spend_ceiling_usd=1.0, build_deadline_s=1e9)
        assert gw.decide_relation({"id": "toy:b:1"}, {"id": "toy:a:1"}) == "NONE"
        assert len(gw.calls) == 1


def test_local_adapter_times_out_kills_child_and_restarts_on_next_request(tmp_path):
    adapter = LocalJSONLAdapter(_argv(tmp_path, HANG_PROCESS, "hang.py"), name="hang", timeout_s=0.5)
    started = time.monotonic()
    with pytest.raises(AdapterError, match="no response within 0.5s"):
        adapter.decide_binding({**CTX, "id": "toy:m1:hang"})
    assert time.monotonic() - started < 20
    assert adapter._proc.poll() is not None  # child was killed
    decision, _ = adapter.decide_binding({**CTX, "id": "toy:m2:1"})  # a fresh child answers
    assert decision["missing"] == ["no_source"]
    assert adapter._restarts == 0  # the valid response reset the consecutive-failure budget
    adapter.close()


def test_per_call_timeout_is_capped_by_the_remaining_budget(tmp_path):
    adapter = LocalJSONLAdapter(_argv(tmp_path, HANG_PROCESS, "hang.py"), name="hang", timeout_s=60)
    with pytest.raises(AdapterError, match="no response within 0.3s"):
        adapter.decide_binding({**CTX, "id": "toy:m1:hang"}, timeout_s=0.3)
    adapter.close()


def test_local_adapter_error_response_charges_its_usage(tmp_path):
    with LocalJSONLAdapter(_argv(tmp_path, HANG_PROCESS, "hang.py"), name="err", timeout_s=10) as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=1.0, build_deadline_s=1e9, max_retries=1)
        with pytest.raises(AdapterError):
            gw.decide_binding({"id": "toy:m1:err", "question": "q", "label": "Yes", "event_title": None})
        assert [c["tokens_in"] for c in gw.calls] == [11, 11]
        assert gw.spend_usd == pytest.approx(0.10)


def test_local_adapter_error_is_retried_then_raised(tmp_path):
    with LocalJSONLAdapter(_argv(tmp_path, ECHO_PROCESS, "echo.py"), name="echo") as adapter:
        gw = Gateway(adapter, spend_ceiling_usd=1.0, build_deadline_s=1e9, max_retries=1)
        with pytest.raises(AdapterError):
            gw.decide_binding({"id": "toy:bad", "question": "q", "label": "Yes", "event_title": None})
        assert [c["retry"] for c in gw.calls] == [False, True]


@pytest.mark.parametrize("mode", ["nan", "wrongid", "list", "badutf8"])
def test_protocol_violations_are_adapter_errors_and_kill_the_child(tmp_path, mode):
    adapter = LocalJSONLAdapter(_argv(tmp_path, PROTOCOL_CHILD, "proto.py", mode), name=mode, timeout_s=10)
    with pytest.raises(AdapterError, match="protocol error"):
        adapter.decide_binding(CTX)
    assert adapter._proc.poll() is not None
    adapter.close()


def test_extra_response_line_is_a_protocol_error(tmp_path):
    adapter = LocalJSONLAdapter(_argv(tmp_path, PROTOCOL_CHILD, "proto.py", "extra"), name="extra", timeout_s=10)
    with pytest.raises(AdapterError, match="protocol error"):
        adapter.decide_binding(CTX)
        adapter.decide_binding(CTX)  # if the first check missed the extra line, the second sees its stale id
    adapter.close()


def test_negative_usage_is_charged_nothing_and_the_child_survives(tmp_path):
    adapter = LocalJSONLAdapter(_argv(tmp_path, PROTOCOL_CHILD, "proto.py", "negcost"), name="neg", timeout_s=10)
    with pytest.raises(AdapterError, match="cost_usd"):
        adapter.decide_binding(CTX)
    assert adapter._proc.poll() is None
    gw = Gateway(adapter, spend_ceiling_usd=1.0, build_deadline_s=1e9, max_retries=0)
    with pytest.raises(AdapterError):
        gw.decide_binding(CTX)
    assert gw.spend_usd == 0.0
    adapter.close()


def test_child_sees_utf8_environment_and_lone_surrogates_round_trip(tmp_path):
    with LocalJSONLAdapter(_argv(tmp_path, PROTOCOL_CHILD, "proto.py", "env"), name="env", timeout_s=10) as adapter:
        decision, _ = adapter.decide_binding(CTX)
        assert decision["missing"] == ["1", "utf-8"]
    with LocalJSONLAdapter(_argv(tmp_path, PROTOCOL_CHILD, "proto.py", "surrogate"), name="sur", timeout_s=10) as adapter:
        decision, _ = adapter.decide_binding({**CTX, "question": "bad \ud83d surrogate"})
        assert decision["missing"] == ["bad \ud83d surrogate"]


def test_oversized_request_cannot_block_past_the_timeout(tmp_path):
    import threading

    adapter = LocalJSONLAdapter(_argv(tmp_path, "import time\ntime.sleep(60)\n", "noread.py"), name="noread", timeout_s=1)
    result = {}

    def call():
        try:
            adapter.decide_binding({**CTX, "question": "q" * (2 * 2**20)})
            result["r"] = "returned"
        except Exception as exc:  # noqa: BLE001 - the assertion below reports the type
            result["r"] = exc

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(20)
    assert not thread.is_alive()
    assert isinstance(result["r"], AdapterError) and "did not read the request" in str(result["r"])


def test_restart_limit_turns_a_dying_child_into_a_fatal_error(tmp_path):
    adapter = LocalJSONLAdapter(_argv(tmp_path, "import sys\nsys.exit(0)\n", "dies.py"), name="dies", timeout_s=5, max_restarts=2)
    errors = 0
    with pytest.raises(AdapterFatal, match="failed 3 times"):
        for _ in range(10):
            try:
                adapter.decide_binding(CTX)
            except AdapterError:
                errors += 1
    assert errors == 3  # the first process, then two restarts
    adapter.close()


def test_close_joins_the_reader_and_refuses_later_use(tmp_path):
    adapter = LocalJSONLAdapter(_argv(tmp_path, ECHO_PROCESS, "echo.py"), name="echo")
    adapter.decide_binding({"id": "toy:m1:1", "question": "q", "label": "Yes", "event_title": None})
    adapter.close()
    assert not adapter._reader.is_alive() and adapter._proc.poll() is not None
    with pytest.raises(AdapterFatal, match="closed"):
        adapter.decide_binding(CTX)


def test_timeout_kills_the_whole_process_tree(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    adapter = LocalJSONLAdapter(_argv(tmp_path, GRANDCHILD, "tree.py", str(pidfile)), name="tree", timeout_s=3)
    with pytest.raises(AdapterError, match="no response"):
        adapter.decide_binding({**CTX, "id": "toy:m1:1"})
    deadline = time.monotonic() + 10
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(grandchild)


def test_bad_adapter_arguments_are_value_errors(tmp_path):
    with pytest.raises(ValueError):
        LocalJSONLAdapter([], name="x")
    for bad in (float("nan"), float("inf"), 0.0, -1.0):
        with pytest.raises(ValueError):
            LocalJSONLAdapter(_argv(tmp_path, ECHO_PROCESS, "echo.py"), timeout_s=bad)


def test_profile_validation():
    assert validate_profile(dict(DEFAULT_PROFILE)) == DEFAULT_PROFILE
    assert load_profile(None) == DEFAULT_PROFILE
    with pytest.raises(ProfileError):
        validate_profile({k: v for k, v in DEFAULT_PROFILE.items() if k != "spend_ceiling_usd"})
    with pytest.raises(ProfileError):
        validate_profile(dict(DEFAULT_PROFILE, max_expr_nodes=True))
    with pytest.raises(ProfileError):
        validate_profile(dict(DEFAULT_PROFILE, build_deadline_s=0))


def test_check_binding_and_relation_reject_malformed():
    from mentat_bench.adapters.base import DecisionError

    with pytest.raises(DecisionError):
        check_binding({"status": "bound", "class": "C", "negated": "no"})
    with pytest.raises(DecisionError):
        check_binding({"status": "unresolved", "class": None, "missing": []})
    with pytest.raises(DecisionError):
        check_relation("SOMETIMES")


def test_check_probs_takes_a_distribution_over_exactly_the_options():
    from mentat_bench.adapters.base import PROB_OPTIONS, DecisionError, check_probs

    options = PROB_OPTIONS["relation"]
    good = dict.fromkeys(options, 0.2)
    assert check_probs(good, options) == good and check_probs(None, options) is None
    assert check_probs({**good, "EQUIV": 1, "IMPLIES": 0, "IMPLIED_BY": 0, "EXCLUDES": 0, "NONE": 0}, options)["EQUIV"] == 1.0
    assert check_probs({**good, "EQUIV": 0.205}, options)  # sum 1.005
    assert check_probs({**good, "EQUIV": 0.195}, options)  # sum 0.995
    bad = [
        {k: v for k, v in good.items() if k != "NONE"},  # a missing option
        {**good, "UNRESOLVED": 0.0},  # an extra key (UNRESOLVED is a relation, not a probs option)
        [0.2] * 5,
        {**good, "EQUIV": True},
        {**good, "EQUIV": "0.2"},
        {**good, "EQUIV": float("nan")},
        {**good, "EQUIV": float("inf")},
        {**good, "EQUIV": 10 ** 400},  # a JSON integer too large for a float
        {**good, "EQUIV": -0.2, "IMPLIES": 0.6},  # a value below 0 (the sum is 1)
        {"EQUIV": 1.5, "IMPLIES": -0.5, **{k: 0.0 for k in ("IMPLIED_BY", "EXCLUDES", "NONE")}},  # a value above 1
        {k: 0.196 for k in options},  # sum 0.98
        {k: 0.204 for k in options},  # sum 1.02
    ]
    for value in bad:
        with pytest.raises(DecisionError):
            check_probs(value, options)


def test_probs_never_change_the_checked_binding_or_relation_decision():
    from mentat_bench.adapters.base import check_binding, check_relation_decision

    base = {"status": "bound", "class": "C", "negated": False, "missing": []}
    assert check_binding({**base, "probs": {"bound": 0.9, "unresolved": 0.1}}) == check_binding(base)
    assert check_binding({**base, "probs": {"bound": 0.5}}) == check_binding(base)  # not read here: the adapter drops it
    assert check_relation_decision({"relation": "EQUIV", "probs": {"EQUIV": 9}}) == ("EQUIV", None)
    assert check_relation_decision({"relation": "EQUIV", "conf": 1.5, "probs": None}) == ("EQUIV", 1.5)


DROP_CHILD = r'''
import json, sys
BAD = {"EQUIV": 0.9, "IMPLIES": 0.9, "IMPLIED_BY": 0.0, "EXCLUDES": 0.0, "NONE": 0.0}  # sums to 1.8
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "decide_binding":
        reply = {"status": "bound", "class": "C", "negated": False, "missing": [], "probs": {"bound": 0.5}}
    elif req["op"] == "decide_relation":
        reply = {"relation": "IMPLIES", "conf": 2.0, "probs": BAD}
    else:
        reply = {"groups": [{"kind": "at_most_one", "members": ["x", "y"]}], "probs": "not an object"}
    print(json.dumps({"id": req["id"], **reply}), flush=True)
'''


def test_malformed_probs_are_dropped_with_a_warning_and_the_decision_is_kept(tmp_path, capsys):
    script = tmp_path / "child.py"
    script.write_text(DROP_CHILD, encoding="utf-8")
    adapter = LocalJSONLAdapter([sys.executable, str(script)], name="drop")
    x = {"id": "x", "question": "Will X?", "label": "Yes", "event_title": None}
    y = dict(x, id="y")
    try:
        binding, _ = adapter.decide_binding(x)
        relation, _ = adapter.decide_relation(x, y)
        groups, _ = adapter.decide_group({"group_id": "g", "source": "s", "members": [x, y]})
    finally:
        adapter.close()
    assert binding == {"status": "bound", "class": "C", "negated": False, "missing": []}  # the probs are gone
    assert relation == {"relation": "IMPLIES", "conf": 2.0}
    assert isinstance(groups, list) and groups == [{"kind": "at_most_one", "members": ["x", "y"]}] and groups.probs is None
    assert adapter.probs_dropped == 3
    assert capsys.readouterr().err.count("dropped malformed probs, kept the decision") == 3


def test_local_decision_keeps_valid_probs_and_drops_malformed_ones():
    from mentat_bench.adapters.base import PROB_OPTIONS
    from mentat_bench.adapters.local_jev import _decision, _Groups

    dropped: list[Exception] = []

    def keep(exc: Exception) -> None:
        dropped.append(exc)

    relation_probs = dict.fromkeys(PROB_OPTIONS["relation"], 0.2)
    group_probs = dict.fromkeys(PROB_OPTIONS["group"], 1 / 6)
    assert _decision("decide_relation", {"relation": "IMPLIES"}, keep) == "IMPLIES"  # unchanged shape without conf or probs
    assert _decision("decide_relation", {"relation": "IMPLIES", "conf": 0.5}, keep) == {"relation": "IMPLIES", "conf": 0.5}
    assert _decision("decide_relation", {"relation": "EQUIV", "probs": relation_probs}, keep) == {
        "relation": "EQUIV", "probs": relation_probs}
    assert _decision("decide_relation", {"relation": "EQUIV", "probs": {"EQUIV": 1}}, keep) == "EQUIV"
    assert len(dropped) == 1  # the malformed relation probs
    binding = {"status": "bound", "class": "C", "negated": False, "missing": [], "probs": {"bound": 1.0, "unresolved": 0.0}}
    assert _decision("decide_binding", binding, keep) == binding
    stripped = _decision("decide_binding", {**binding, "probs": {"bound": 9}}, keep)
    assert stripped == {k: v for k, v in binding.items() if k != "probs"} and len(dropped) == 2
    plain = _decision("decide_group", {"groups": []}, keep)
    assert isinstance(plain, _Groups) and plain == [] and plain.probs is None
    assert _decision("decide_group", {"groups": [], "probs": group_probs}, keep).probs == group_probs
    bad = _decision("decide_group", {"groups": [{"kind": "at_most_one"}], "probs": {"none": 1.0}}, keep)
    assert bad == [{"kind": "at_most_one"}] and bad.probs is None and len(dropped) == 3


# ---------------------------------------------------------------- evaluate (seal, profile, usage, engine)


EMPTY_USAGE = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}


def _sealed_submission(tmp_path, usage_doc=EMPTY_USAGE, incomplete=None):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "bindings.jsonl").write_text("", encoding="utf-8")
    ir.write_json(sub / "profile.json", dict(DEFAULT_PROFILE))
    ir.write_json(sub / "usage.json", usage_doc)
    if incomplete is not None:
        ir.write_json(sub / INCOMPLETE_FILE, {"reason": incomplete})
    return sub, seal(sub, allow_incomplete=incomplete is not None)["digest"]


class _Run:
    """Paths for one evaluate invocation under tmp_path."""

    def __init__(self, tmp_path, usage_doc=EMPTY_USAGE, incomplete=None):
        self.tmp = tmp_path
        self.sub, self.digest = _sealed_submission(tmp_path, usage_doc, incomplete)
        self.pinned = tmp_path / "pinned_profile.json"
        ir.write_json(self.pinned, dict(DEFAULT_PROFILE))
        self.queries = tmp_path / "queries.jsonl"
        ir.write_jsonl(self.queries, [{"qid": "a", "family": "closure"}, {"qid": "b", "family": "selection"}])
        self.gold = tmp_path / "gold"
        self.gold.mkdir()
        self.out = tmp_path / "run"

    def argv(self, **overrides):
        args = {
            "--submission": str(self.sub), "--expected-digest": self.digest, "--profile": str(self.pinned),
            "--queries": str(self.queries), "--gold": str(self.gold), "--out": str(self.out),
        }
        args.update({k: str(v) for k, v in overrides.items()})
        return ["evaluate", *[x for kv in args.items() for x in kv]]


def test_evaluate_requires_profile_and_expected_digest(capsys):
    assert cli.main(["evaluate", "--submission", "s", "--queries", "q", "--gold", "g", "--out", "o"]) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "--profile" in err and "--expected-digest" in err


def test_seal_refusal_happens_before_engine(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    (run.sub / "extra.txt").write_text("tampered\n", encoding="utf-8")  # adding a file needs no unlock
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(run.argv()) == cli.EXIT_SEAL
    assert not run.out.exists()


def test_evaluate_refuses_unexpected_digest(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(run.argv(**{"--expected-digest": "0" * 64})) == cli.EXIT_SEAL and not run.out.exists()


def test_evaluate_accepts_digest_in_any_case_and_whitespace(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    monkeypatch.setattr(engine, "validate", lambda *a, **k: None)
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    code = cli.main(run.argv(**{"--expected-digest": " " + run.digest.upper() + " "}))
    assert code == cli.EXIT_INFRA  # got past the seal check to the engine


def test_evaluate_refuses_when_engine_validate_fails_and_writes_no_run_dir(tmp_path, monkeypatch):
    run = _Run(tmp_path)

    def failing_validate(submission, profile=None):
        raise engine.EngineError('[{"error": "alias cycle"}]', returncode=1)

    monkeypatch.setattr(engine, "validate", failing_validate)
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(run.argv()) == cli.EXIT_ENGINE and not run.out.exists()


def test_evaluate_refuses_profile_mismatch(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    ir.write_json(run.pinned, dict(DEFAULT_PROFILE, spend_ceiling_usd=1.0))
    called = []
    monkeypatch.setattr(engine, "validate", lambda *a, **k: called.append(a))
    assert cli.main(run.argv()) == cli.EXIT_VALIDATE and not called and not run.out.exists()


def test_evaluate_refuses_usage_over_the_pinned_caps(tmp_path, monkeypatch):
    over = {"model": "m", "kind": "llm", "tokens_in": 1, "tokens_out": 1, "seconds": 0.1, "cost_usd": 99.0}
    doc = {"calls": [over, dict(over, cost_usd=0.1)], "wall_seconds": 1.0, "peak_rss_mb": 1.0}  # the first call overshoots and is not last
    run = _Run(tmp_path, usage_doc=doc)
    called = []
    monkeypatch.setattr(engine, "validate", lambda *a, **k: called.append(a))
    assert cli.main(run.argv()) == cli.EXIT_VALIDATE
    assert not called and not run.out.exists()


def test_evaluate_refuses_nonfinite_profile_file(tmp_path):
    run = _Run(tmp_path)
    run.pinned.write_text(json.dumps(dict(DEFAULT_PROFILE)).replace("60.0", "NaN"), encoding="utf-8")
    assert cli.main(run.argv()) == cli.EXIT_VALIDATE


def test_evaluate_reaches_engine_only_after_validate_passes(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    validated = []
    monkeypatch.setattr(engine, "validate", lambda submission, profile=None: validated.append(profile))
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(run.argv()) == cli.EXIT_INFRA  # engine missing: infrastructure failure
    assert len(validated) == 1 and validated[0].name == "profile.json"
    assert validated[0].parent != run.sub  # the engine sees the verified copy, not the original
    assert not run.out.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".run-")]


def test_evaluate_bad_inputs_are_usage_errors(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(run.argv(**{"--out": run.sub / "inside"})) == cli.EXIT_USAGE
    assert cli.main(run.argv(**{"--gold": tmp_path / "no-gold"})) == cli.EXIT_USAGE
    assert cli.main(run.argv(**{"--queries": tmp_path / "no-queries.jsonl"})) == cli.EXIT_USAGE
    assert cli.main(run.argv(**{"--profile": tmp_path / "no-profile.json"})) == cli.EXIT_USAGE
    assert not run.out.exists()


def test_evaluate_with_fake_engine_publishes_both_outputs_atomically(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    assert cli.main(run.argv()) == 0
    assert len(list(ir.read_jsonl(run.out / "answers.jsonl"))) == 2
    assert len(list(ir.read_jsonl(run.out / "verdicts.jsonl"))) == 2
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".run-")]


def test_engine_refusal_writes_nothing_and_discards_stale_outputs(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    run.out.mkdir()
    (run.out / "answers.jsonl").write_text('{"qid": "old"}\n', encoding="utf-8")
    (run.out / "verdicts.jsonl").write_text('{"qid": "old"}\n', encoding="utf-8")
    monkeypatch.setattr(engine, "validate", lambda *a, **k: None)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "invalid")
    assert cli.main(run.argv()) == cli.EXIT_ENGINE
    assert not (run.out / "answers.jsonl").exists() and not (run.out / "verdicts.jsonl").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".run-")]


@pytest.mark.parametrize("mode, code", [("usage", cli.EXIT_INFRA), ("short", cli.EXIT_INFRA)])
def test_engine_infrastructure_failures_exit_7(tmp_path, monkeypatch, mode, code):
    run = _Run(tmp_path)
    monkeypatch.setattr(engine, "validate", lambda *a, **k: None)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", mode)
    assert cli.main(run.argv()) == code
    assert not run.out.exists()


def test_engine_overall_budget_kills_a_hung_engine(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    monkeypatch.setattr(engine, "validate", lambda *a, **k: None)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "hang")
    monkeypatch.setenv("MB_ENGINE_TIMEOUT", "2")
    started = time.monotonic()
    assert cli.main(run.argv()) == cli.EXIT_ENGINE
    assert time.monotonic() - started < 60


def test_engine_budget_env_must_be_positive(monkeypatch, tmp_path):
    monkeypatch.setenv("MB_ENGINE_TIMEOUT", "nan")
    with pytest.raises(ValueError):
        engine.engine_budget_s(tmp_path / "q.jsonl")


def test_missing_engine_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert not engine.engine_available()
    with pytest.raises(engine.EngineInfraError, match="not built"):
        engine.eval_submission(tmp_path, tmp_path / "q.jsonl", tmp_path / "a.jsonl")
    assert cli.main(["fixtures", "gold", "--fixture", str(tmp_path)]) == cli.EXIT_USAGE  # no queries.jsonl yet
    ir.write_jsonl(tmp_path / "queries.jsonl", [{"qid": "f1", "family": "feasibility", "assume": []}])
    assert cli.main(["fixtures", "gold", "--fixture", str(tmp_path)]) == cli.EXIT_INFRA


def test_non_executable_engine_is_an_infrastructure_error(tmp_path, monkeypatch):
    bogus = tmp_path / "mb-engine"
    bogus.write_text("not a program\n", encoding="utf-8")
    monkeypatch.setenv("MB_ENGINE", str(bogus))
    with pytest.raises(engine.EngineInfraError, match="cannot run"):
        engine.validate(tmp_path)


# ---------------------------------------------------------------- build and score arguments


def test_build_refuses_nonempty_output_directory(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.txt").write_text("x", encoding="utf-8")
    code = cli.main(["build", "--adapter", "empty", "--corpus", str(tmp_path / "none.jsonl"), "--out", str(out)])
    assert code == cli.EXIT_USAGE


def test_build_requires_gold_for_oracle(capsys):
    assert cli.main(["build", "--adapter", "oracle", "--corpus", "x.jsonl", "--out", "y"]) == cli.EXIT_USAGE


def test_build_friendly_usage_errors(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"id":"toy:m1:1","text":"q","label":"Yes","event_title":null}\n', encoding="utf-8")
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(tmp_path / "missing.jsonl"), "--out", str(tmp_path / "o")]) == cli.EXIT_USAGE
    as_file = tmp_path / "a_file"
    as_file.write_text("x", encoding="utf-8")
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(as_file)]) == cli.EXIT_USAGE
    missing_exe = ["build", "--adapter", "local", "--corpus", str(corpus), "--out", str(tmp_path / "o2"),
                   f"--local-arg={tmp_path / 'no-such-program'}"]
    assert cli.main(missing_exe) == cli.EXIT_USAGE
    assert not (tmp_path / "o2").exists() and not (tmp_path / "o2.build").exists()


def test_build_aborts_when_the_adapter_keeps_dying_and_leaves_no_work_dir(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        '{"id":"toy:m1:1","text":"q one","label":"Yes","event_title":null}\n'
        '{"id":"toy:m2:1","text":"q two","label":"Yes","event_title":null}\n',
        encoding="utf-8",
    )
    argv = ["build", "--adapter", "local", "--corpus", str(corpus), "--out", str(tmp_path / "out"),
            f"--local-arg={sys.executable}", "--local-arg=-c", "--local-arg=import sys; sys.exit(0)", "--local-timeout", "5"]
    assert cli.main(argv) == cli.EXIT_BUILD_INCOMPLETE
    assert not (tmp_path / "out").exists() and (tmp_path / "out.build" / "usage_calls.jsonl").exists()  # charged calls: kept


def test_local_arg_accepts_dash_leading_values():
    args = cli.build_parser().parse_args(["build", "--adapter", "local", "--corpus", "c", "--out", "o",
                                          "--local-arg=-m", "--local-arg=jev"])
    assert args.local_arg == ["-m", "jev"]


@pytest.mark.parametrize("argv", [
    ["build", "--adapter", "empty", "--corpus", "c", "--out", "o", "--checkpoint-every", "0"],
    ["build", "--adapter", "empty", "--corpus", "c", "--out", "o", "--max-df", "-3"],
    ["build", "--adapter", "empty", "--corpus", "c", "--out", "o", "--limit-markets", "0"],
    ["build", "--adapter", "local", "--corpus", "c", "--out", "o", "--local-timeout", "nan"],
    ["build", "--adapter", "local", "--corpus", "c", "--out", "o", "--local-timeout", "0"],
    ["fixtures", "gen", "--seasons", "0"],
])
def test_positive_arguments_are_validated_by_the_parser(argv, capsys):
    with pytest.raises(SystemExit) as info:
        cli.build_parser().parse_args(argv)
    assert info.value.code == 2


def test_score_baseline_with_answers_is_a_usage_error(tmp_path):
    ir.write_jsonl(tmp_path / "q.jsonl", [{"qid": "s1", "family": "selection"}])
    base = ["score", "--queries", str(tmp_path / "q.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl")]
    assert cli.main([*base, "--baseline", "all-unresolved", "--answers", str(tmp_path / "a.jsonl")]) == cli.EXIT_USAGE


def test_score_usage_file_problems_are_usage_errors(tmp_path):
    ir.write_jsonl(tmp_path / "q.jsonl", [{"qid": "s1", "family": "selection"}])
    ir.write_jsonl(tmp_path / "g.jsonl", [{"qid": "s1", "ids": []}])
    bad = tmp_path / "usage.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    argv = ["score", "--queries", str(tmp_path / "q.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl"),
            "--baseline", "all-unresolved", "--usage", str(bad)]
    assert cli.main(argv) == cli.EXIT_USAGE


@pytest.mark.skipif(not engine.engine_available(), reason="mb-engine binary not built")
def test_evaluate_roundtrip_with_engine(tmp_path):
    if not (FIXTURE / "queries.jsonl").exists():
        pytest.skip("toy10k fixture not generated")
    sub = tmp_path / "gold_copy"
    sub.mkdir()
    for name in ("bindings.jsonl", "theory.json", "provenance.jsonl", "usage.json"):
        (sub / name).write_bytes((FIXTURE / "gold" / name).read_bytes())
    ir.write_json(sub / "profile.json", dict(DEFAULT_PROFILE))
    digest = seal(sub)["digest"]
    pinned = tmp_path / "pinned.json"
    ir.write_json(pinned, dict(DEFAULT_PROFILE))
    out = tmp_path / "run"
    assert cli.main(["evaluate", "--submission", str(sub), "--expected-digest", digest, "--profile", str(pinned),
                     "--queries", str(FIXTURE / "queries.jsonl"), "--gold", str(FIXTURE / "gold"), "--out", str(out)]) == 0
    answers = list(ir.read_jsonl(out / "answers.jsonl"))
    verdicts = list(ir.read_jsonl(out / "verdicts.jsonl"))
    assert len(answers) == len(verdicts) == sum(1 for _ in ir.read_jsonl(FIXTURE / "queries.jsonl"))


@pytest.mark.skipif(not (FIXTURE / "gold" / "theory.json").exists(), reason="toy10k fixture not generated")
@pytest.mark.slow
def test_oracle_build_on_fixture_seals_and_verifies(tmp_path):
    from irval import check_submission
    from mentat_bench.adapters.mock import OracleAdapter
    from mentat_bench.harness import fixed
    from mentat_bench.manifest import DEFAULT_PROFILE
    from mentat_bench.seal import verify_seal

    records = [
        {"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r["event_title"]}
        for r in ir.read_jsonl(FIXTURE / "corpus.jsonl")
    ]
    summary = fixed.build(records, OracleAdapter(FIXTURE / "gold"), tmp_path / "sub", dict(DEFAULT_PROFILE))
    gold_theory = json.loads((FIXTURE / "gold" / "theory.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "full_replay" and summary["budget_stopped"] is False
    assert summary["outcomes"] == len(records) and summary["calls"] == len(records)  # bindings only
    assert summary["constraints"] == len(gold_theory["constraints"])
    assert summary["interpretations"] == ["base", "alt"]
    seal(tmp_path / "sub")
    verify_seal(tmp_path / "sub")
    assert check_submission(tmp_path / "sub") == []


# ---------------------------------------------------------------- round 2: discard outputs, copy, publish, score


def _stale_outputs(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "answers.jsonl").write_text('{"qid": "old", "ids": []}\n', encoding="utf-8")
    (out / "verdicts.jsonl").write_text('{"qid": "old", "cert_valid": true}\n', encoding="utf-8")


def _no_outputs(out: Path) -> bool:
    return not (out / "answers.jsonl").exists() and not (out / "verdicts.jsonl").exists()


@pytest.mark.parametrize("refusal", ["seal", "profile", "usage", "validate", "overrun", "missing_profile"])
def test_every_refused_run_discards_earlier_outputs(tmp_path, monkeypatch, refusal):
    over = {"model": "m", "kind": "llm", "tokens_in": 1, "tokens_out": 1, "seconds": 0.1, "cost_usd": 99.0}
    run = _Run(tmp_path, usage_doc={"calls": [over, dict(over, cost_usd=0.1)], "wall_seconds": 1.0, "peak_rss_mb": 1.0}
               if refusal == "usage" else EMPTY_USAGE)
    _stale_outputs(run.out)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "hang" if refusal == "overrun" else ("invalid" if refusal == "validate" else "ok"))
    monkeypatch.setenv("MB_ENGINE_TIMEOUT", "1")
    argv = run.argv()
    if refusal == "seal":
        (run.sub / "extra.txt").write_text("tampered\n", encoding="utf-8")
    elif refusal == "profile":
        ir.write_json(run.pinned, dict(DEFAULT_PROFILE, spend_ceiling_usd=1.0))
    elif refusal == "missing_profile":
        argv = run.argv(**{"--profile": tmp_path / "no-such-profile.json"})
    expected = {"seal": cli.EXIT_SEAL, "profile": cli.EXIT_VALIDATE, "usage": cli.EXIT_VALIDATE,
                "validate": cli.EXIT_ENGINE, "overrun": cli.EXIT_ENGINE, "missing_profile": cli.EXIT_USAGE}[refusal]
    assert cli.main(argv) == expected
    assert _no_outputs(run.out)
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".run-")]


def test_outputs_inside_the_submission_are_never_touched(tmp_path):
    run = _Run(tmp_path)
    inside = run.sub / "answers.jsonl"
    inside.write_text("not ours\n", encoding="utf-8")  # a sealed-tree file would make seal fail; it must survive a refused run
    assert cli.main(run.argv(**{"--out": run.sub})) == cli.EXIT_USAGE
    assert inside.read_text(encoding="utf-8") == "not ours\n"


def test_submission_modified_during_the_run_is_refused_and_engine_reads_a_copy(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    _stale_outputs(run.out)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    real_eval = engine.eval_submission
    seen = []

    def tampering_eval(submission, queries, answers_out, timeout_s=None):
        seen.append(Path(submission))
        real_eval(submission, queries, answers_out, timeout_s=timeout_s)
        target = run.sub / "bindings.jsonl"
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        target.write_text("tampered\n", encoding="utf-8")

    monkeypatch.setattr(engine, "eval_submission", tampering_eval)
    assert cli.main(run.argv()) == cli.EXIT_SEAL
    assert seen and seen[0] != run.sub
    assert _no_outputs(run.out)


def test_failed_publish_leaves_no_partial_outputs(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    real_replace = os.replace

    def flaky_replace(src, dst):
        if Path(dst).name == "verdicts.jsonl":
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    assert cli.main(run.argv()) == cli.EXIT_INFRA
    assert _no_outputs(run.out)


def test_successful_run_replaces_stale_outputs(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    _stale_outputs(run.out)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    assert cli.main(run.argv()) == 0
    assert [json.loads(x)["qid"] for x in (run.out / "answers.jsonl").read_text().splitlines()] == ["q0", "q1"]


def test_evaluate_warns_and_flags_a_sealed_incomplete_submission(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    partial, complete = tmp_path / "partial", tmp_path / "complete"
    partial.mkdir()
    complete.mkdir()
    run = _Run(partial, incomplete="adapter_fatal: the adapter failed in the relation phase")
    assert cli.main(run.argv()) == 0
    captured = capsys.readouterr()
    assert "incomplete build (adapter_fatal: the adapter failed in the relation phase)" in captured.err
    assert json.loads(captured.out)["incomplete"] is True
    assert cli.main(_Run(complete).argv()) == 0
    captured = capsys.readouterr()
    assert "incomplete build" not in captured.err and "incomplete" not in json.loads(captured.out)


def test_engine_overrun_message_shows_fractional_budget(tmp_path, monkeypatch, capsys):
    run = _Run(tmp_path)
    monkeypatch.setattr(engine, "validate", lambda *a, **k: None)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "hang")
    monkeypatch.setenv("MB_ENGINE_TIMEOUT", "0.5")
    assert cli.main(run.argv()) == cli.EXIT_ENGINE
    assert "0.5s budget" in capsys.readouterr().err


# ---------------------------------------------------------------- round 2: score with missing files


def _score_inputs(tmp_path):
    ir.write_jsonl(tmp_path / "q.jsonl", [{"qid": "s1", "family": "selection"}])
    ir.write_jsonl(tmp_path / "g.jsonl", [{"qid": "s1", "ids": ["x"]}])
    return ["score", "--queries", str(tmp_path / "q.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl")]


def test_score_with_missing_answers_scores_every_query_zero(tmp_path, capsys):
    base = _score_inputs(tmp_path)
    assert cli.main([*base, "--answers", str(tmp_path / "none.jsonl"), "--verdicts", str(tmp_path / "none-v.jsonl")]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["per_query"] == {"s1": 0.0}
    assert "every query scores 0" in captured.err and "no certificates" in captured.err


def test_score_missing_verdicts_means_no_certificates(tmp_path, capsys):
    base = _score_inputs(tmp_path)
    ir.write_jsonl(tmp_path / "a.jsonl", [{"qid": "s1", "ids": ["x"]}])
    ir.write_jsonl(tmp_path / "v.jsonl", [{"qid": "s1", "cert_valid": True, "reason": "ok"}])
    assert cli.main([*base, "--answers", str(tmp_path / "a.jsonl"), "--verdicts", str(tmp_path / "v.jsonl")]) == 0
    capsys.readouterr()
    assert cli.main([*base, "--answers", str(tmp_path / "a.jsonl"), "--verdicts", str(tmp_path / "none-v.jsonl")]) == 0
    captured = capsys.readouterr()
    assert "no certificates" in captured.err and "per_query" in captured.out


def test_score_unreadable_inputs_are_usage_errors(tmp_path):
    base = _score_inputs(tmp_path)
    assert cli.main(["score", "--queries", str(tmp_path / "missing.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl"),
                     "--baseline", "all-unresolved"]) == cli.EXIT_USAGE
    ir.write_jsonl(tmp_path / "a.jsonl", [{"qid": "s1", "ids": ["x"]}])
    (tmp_path / "bad_v.jsonl").write_text('{"qid": "s1"}\n', encoding="utf-8")  # no cert_valid: read as no certificate
    assert cli.main([*base, "--answers", str(tmp_path / "a.jsonl"), "--verdicts", str(tmp_path / "bad_v.jsonl")]) == 0
    assert cli.main(["score", "--queries", str(tmp_path / "q.jsonl"), "--gold-answers", str(tmp_path / "g.jsonl"),
                     "--answers", str(tmp_path / "a.jsonl"), "--verdicts", str(tmp_path / "missing_v.jsonl")]) == 0


# ---------------------------------------------------------------- round 2: build exit codes and wiring


def _tiny_corpus(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"id":"toy:m1:1","text":"q one","label":"Yes","event_title":null}\n', encoding="utf-8")
    return corpus


@pytest.mark.parametrize("exc, code", [
    (engine.EngineInfraError("no engine"), cli.EXIT_INFRA),
    (engine.EngineError("refused", 1), cli.EXIT_ENGINE),
    (ValueError("bad checkpoint"), cli.EXIT_USAGE),
    (RuntimeError("boom"), cli.EXIT_BUILD_INCOMPLETE),
])
def test_build_maps_failures_to_documented_exit_codes(tmp_path, monkeypatch, exc, code, capsys):
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(cli.fixed, "build", boom)
    out = tmp_path / "o"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == code
    assert not out.exists() and not (tmp_path / "o.build").exists()
    assert "Traceback" not in capsys.readouterr().err


def test_build_writes_build_id_and_seals(tmp_path):
    out = tmp_path / "o"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == 0
    assert json.loads((out / "build_id.json").read_text())["build_id"]
    assert cli.main(["seal", "--submission", str(out)]) == 0


def test_build_policy_names_the_local_adapter_kind_not_the_program(tmp_path):
    argv_child = _argv(tmp_path, ECHO_PROCESS, "echo.py")
    with LocalJSONLAdapter(argv_child, timeout_s=10) as adapter:
        assert adapter.name == "local"
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"id":"toy:m1:1","text":"q one","label":"Yes","event_title":null}\n'
                      '{"id":"toy:m2:1","text":"q one","label":"Yes","event_title":null}\n', encoding="utf-8")
    out = tmp_path / "o"
    argv = ["build", "--adapter", "local", "--corpus", str(corpus), "--out", str(out), "--local-timeout", "10",
            *(f"--local-arg={part}" for part in argv_child)]
    assert cli.main(argv) == 0
    assert json.loads((out / "build_policy.json").read_text())["adapter"] == "local"


def test_finalized_checkpoint_after_adapter_abort_is_kept(tmp_path, monkeypatch):
    def finalized(records, adapter, out_dir, *args, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True)
        (out_dir / "bindings.jsonl").write_text("", encoding="utf-8")
        return {"adapter_fatal": "local process kept failing", "outcomes": 0}

    monkeypatch.setattr(cli.fixed, "build", finalized)
    out = tmp_path / "o"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == cli.EXIT_BUILD_INCOMPLETE
    assert (out / "bindings.jsonl").exists()
    assert json.loads((out / INCOMPLETE_FILE).read_text())["reason"].startswith("adapter_fatal")


def test_leftover_build_dir_is_a_usage_error(tmp_path):
    out = tmp_path / "o"
    (tmp_path / "o.build").mkdir()
    (tmp_path / "o.build" / "x").write_text("x", encoding="utf-8")
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == cli.EXIT_USAGE


# ---------------------------------------------------------------- round 2: adapter protocol


def test_restart_budget_counts_consecutive_failures_only(tmp_path):
    source = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req['ctx']['id'].endswith(':die'):\n"
        "        sys.exit(0)\n"
        "    print(json.dumps({'id': req['id'], 'status': 'unresolved', 'class': None, 'negated': False, 'missing': ['m']}), flush=True)\n"
    )
    adapter = LocalJSONLAdapter(_argv(tmp_path, source, "die.py"), name="die", timeout_s=5, max_restarts=1)
    for _ in range(4):  # each death is followed by a healthy response: the budget never runs out
        with pytest.raises(AdapterError):
            adapter.decide_binding({**CTX, "id": "toy:m1:die"})
        assert adapter.decide_binding({**CTX, "id": "toy:m2:1"})[0]["missing"] == ["m"]
    with pytest.raises(AdapterError):
        adapter.decide_binding({**CTX, "id": "toy:m1:die"})
    with pytest.raises(AdapterError):
        adapter.decide_binding({**CTX, "id": "toy:m1:die"})
    with pytest.raises(AdapterFatal):
        adapter.decide_binding({**CTX, "id": "toy:m1:die"})  # two deaths in a row exhaust max_restarts=1
    adapter.close()


def test_malformed_usage_is_still_charged_its_valid_fields(tmp_path):
    adapter = LocalJSONLAdapter(_argv(tmp_path, PROTOCOL_CHILD, "proto.py", "partial"), name="partial", timeout_s=10)
    gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=1e9, max_retries=0)
    with pytest.raises(AdapterError):
        gw.decide_binding(CTX)
    assert gw.calls[0]["tokens_in"] == 7 and gw.calls[0]["cost_usd"] == 0.0
    adapter.close()


def test_drain_gives_up_on_pipes_held_by_surviving_descendants():
    import subprocess as sp

    class Pipe:
        closed = False

        def close(self):
            self.closed = True

    class Stuck:
        def __init__(self):
            self.stdout, self.stderr = Pipe(), Pipe()

        def communicate(self, timeout=None):
            raise sp.TimeoutExpired("mb-engine", timeout)

    proc = Stuck()
    engine._drain(proc)  # returns instead of waiting forever
    assert proc.stdout.closed and proc.stderr.closed


# ---------------------------------------------------------------- round 2: capped builds evaluate (engine)


class _Spender:
    """Free unresolved bindings; EXCLUDES relations that cost `cost` and take `sleep` seconds each."""

    name = "spender"

    def __init__(self, cost=0.0, sleep=0.0):
        self.cost, self.sleep = cost, sleep

    def decide_binding(self, ctx):
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}, usage("spender")

    def decide_relation(self, a, b):
        if self.sleep:
            time.sleep(self.sleep)
        return "EXCLUDES", usage("spender", "llm", 1, 1, self.cost)


def _capped_records():
    return [{"id": f"toy:m{i}:1", "question": f"will alpha team {i // 2} win", "label": "Yes", "event_title": None}
            for i in range(1, 61)]


@pytest.mark.parametrize("cap", ["spend", "deadline"])
def test_capped_build_seals_and_evaluates(tmp_path, monkeypatch, cap):
    from mentat_bench.adapters.mock import EmptyAdapter
    from mentat_bench.harness import fixed

    if not engine.engine_available():
        pytest.skip("mb-engine binary not built")
    if cap == "spend":
        adapter, profile = _Spender(cost=1.0), dict(DEFAULT_PROFILE, spend_ceiling_usd=3.5)
    else:
        adapter, profile = _Spender(sleep=0.05), dict(DEFAULT_PROFILE, build_deadline_s=0.3)
    sub = tmp_path / "sub"
    summary = fixed.build(_capped_records(), adapter, sub, profile, checkpoint_every=1)
    assert summary["budget_stopped"] is True
    usage_doc = json.loads((sub / "usage.json").read_text())
    assert usage_doc["calls"][-1]["cost_usd"] > 0 or cap == "deadline"  # the overshooting call is recorded
    gold = tmp_path / "gold"
    fixed.build(_capped_records(), EmptyAdapter(), gold, dict(DEFAULT_PROFILE))
    queries = tmp_path / "queries.jsonl"
    ir.write_jsonl(queries, [{"qid": "f1", "family": "feasibility", "assume": []}])
    digest = seal(sub)["digest"]
    pinned = tmp_path / "pinned.json"
    ir.write_json(pinned, profile)
    out = tmp_path / "run"
    assert cli.main(["evaluate", "--submission", str(sub), "--expected-digest", digest, "--profile", str(pinned),
                     "--queries", str(queries), "--gold", str(gold), "--out", str(out)]) == 0
    assert len(list(ir.read_jsonl(out / "answers.jsonl"))) == 1


# ---------------------------------------------------------------- round 3: harness outcomes and new build wiring


def test_adapter_fatal_summary_keeps_the_finalized_checkpoint_and_exits_3(tmp_path, monkeypatch, capsys):
    def finalized(records, adapter, out_dir, *args, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True)
        (out_dir / "bindings.jsonl").write_text("", encoding="utf-8")
        return {"adapter_fatal": True, "base_check": "not_run", "base_probe": "not_applicable",
                "relations_dropped": 0, "outcomes": 0}

    monkeypatch.setattr(cli.fixed, "build", finalized)
    out = tmp_path / "o"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == cli.EXIT_BUILD_INCOMPLETE
    assert (out / "bindings.jsonl").exists()  # kept
    assert not (out / "build_id.json").exists()  # and not sealed
    assert json.loads((out / INCOMPLETE_FILE).read_text())["summary"]["adapter_fatal"] is True
    assert cli.main(["seal", "--submission", str(out)]) == cli.EXIT_SEAL  # the marker refuses the seal
    captured = capsys.readouterr()
    assert json.loads(captured.out)["adapter_fatal"] is True
    assert "not sealed" in captured.err


def test_base_check_incomplete_keeps_the_work_dir_for_finalize(tmp_path, monkeypatch, capsys):
    def incomplete(records, adapter, out_dir, *args, **kwargs):
        work = Path(out_dir).with_name(Path(out_dir).name + ".build")
        work.mkdir()
        (work / "checkpoint.json").write_text("{}", encoding="utf-8")
        raise cli.fixed.BaseCheckIncomplete("the build deadline passed")

    monkeypatch.setattr(cli.fixed, "build", incomplete)
    out = tmp_path / "o"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == cli.EXIT_BUILD_INCOMPLETE
    assert (tmp_path / "o.build" / "checkpoint.json").exists()
    assert not out.exists()
    err = capsys.readouterr().err
    assert "finalize_checkpoint" in err and "build_deadline_s" in err


def test_base_inconsistent_build_is_exit_5_and_cleans_up(tmp_path, monkeypatch, capsys):
    def inconsistent(records, adapter, out_dir, *args, **kwargs):
        work = Path(out_dir).with_name(Path(out_dir).name + ".build")
        work.mkdir()
        (Path(out_dir)).mkdir()
        raise cli.fixed.BaseInconsistentError("more than 1000 relation constraints contradict the bindings")

    monkeypatch.setattr(cli.fixed, "build", inconsistent)
    out = tmp_path / "o"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == cli.EXIT_ENGINE
    assert not out.exists() and not (tmp_path / "o.build").exists()
    assert "inconsistent" in capsys.readouterr().err


def test_adapter_fatal_exception_cleans_up_and_exits_3(tmp_path, monkeypatch):
    def fatal(records, adapter, out_dir, *args, **kwargs):
        Path(out_dir).with_name(Path(out_dir).name + ".build").mkdir()
        raise AdapterFatal("kept failing")

    monkeypatch.setattr(cli.fixed, "build", fatal)
    out = tmp_path / "o"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == cli.EXIT_BUILD_INCOMPLETE
    assert not out.exists() and not (tmp_path / "o.build").exists()


def test_build_passes_the_error_and_drop_limits_with_defaults(tmp_path, monkeypatch):
    seen = {}

    def record(records, adapter, out_dir, *args, **kwargs):
        seen.update(kwargs)
        raise cli.fixed.BuildIncomplete("stop")

    monkeypatch.setattr(cli.fixed, "build", record)
    base = ["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path))]
    assert cli.main([*base, "--out", str(tmp_path / "a")]) == cli.EXIT_BUILD_INCOMPLETE
    assert seen["max_consecutive_errors"] == 20 and seen["max_relation_drops"] == 20000
    assert cli.main([*base, "--out", str(tmp_path / "b"), "--max-consecutive-errors", "5", "--max-relation-drops", "7"]) == cli.EXIT_BUILD_INCOMPLETE
    assert seen["max_consecutive_errors"] == 5 and seen["max_relation_drops"] == 7
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([*base, "--out", "c", "--max-relation-drops", "0"])


def test_build_output_reports_the_base_check_fields(tmp_path, capsys):
    out = tmp_path / "o"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_tiny_corpus(tmp_path)), "--out", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    for key in ("base_check", "base_probe", "relations_dropped", "adapter_fatal"):
        assert key in summary
    assert summary["adapter_fatal"] is False


def test_score_reads_verdicts_through_scoring_read_verdicts(tmp_path, monkeypatch, capsys):
    base = _score_inputs(tmp_path)
    ir.write_jsonl(tmp_path / "a.jsonl", [{"qid": "s1", "ids": ["x"]}])
    ir.write_jsonl(tmp_path / "v.jsonl", [{"qid": "s1", "cert_valid": True, "reason": "ok"}])
    seen = []
    real = cli.scoring.read_verdicts

    def spy(path):
        seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(cli.scoring, "read_verdicts", spy)
    assert cli.main([*base, "--answers", str(tmp_path / "a.jsonl"), "--verdicts", str(tmp_path / "v.jsonl")]) == 0
    assert seen == [tmp_path / "v.jsonl"]
    assert json.loads(capsys.readouterr().out)["per_query"] == {"s1": 1.0}


# ---------------------------------------------------------------- round 4: refusals never leave a prior run's outputs


def _argv_without(run, *flags):
    argv, skip = [], False
    for token in run.argv():
        if skip:
            skip = False
            continue
        if token in flags:
            skip = True
            continue
        argv.append(token)
    return argv


def test_evaluate_missing_profile_refuses_and_discards_prior_outputs(tmp_path, monkeypatch, capsys):
    run = _Run(tmp_path)
    _stale_outputs(run.out)
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(_argv_without(run, "--profile")) == cli.EXIT_USAGE
    assert _no_outputs(run.out)
    assert "--profile" in capsys.readouterr().err


def test_evaluate_missing_expected_digest_refuses_and_discards_prior_outputs(tmp_path, monkeypatch, capsys):
    run = _Run(tmp_path)
    _stale_outputs(run.out)
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(_argv_without(run, "--expected-digest")) == cli.EXIT_USAGE
    assert _no_outputs(run.out)
    assert "--expected-digest" in capsys.readouterr().err


def test_invalid_queries_path_with_a_prior_run_discards_its_outputs(tmp_path, monkeypatch):
    run = _Run(tmp_path)
    _stale_outputs(run.out)
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(run.argv(**{"--queries": tmp_path / "no-such-queries.jsonl"})) == cli.EXIT_USAGE
    assert _no_outputs(run.out)


def test_argparse_refusal_discards_a_prior_run_before_parsing(tmp_path):
    run = _Run(tmp_path)
    _stale_outputs(run.out)
    with pytest.raises(SystemExit) as info:
        cli.main([*run.argv(), "--bogus"])
    assert info.value.code == 2
    assert _no_outputs(run.out)


def test_argparse_refusal_discards_when_out_is_given_as_equals_form(tmp_path):
    run = _Run(tmp_path)
    _stale_outputs(run.out)
    argv = _argv_without(run, "--out") + [f"--out={run.out}", "--bogus"]
    with pytest.raises(SystemExit):
        cli.main(argv)
    assert _no_outputs(run.out)


def test_argparse_refusal_never_touches_outputs_inside_the_submission(tmp_path):
    run = _Run(tmp_path)
    inside = run.sub / "answers.jsonl"
    inside.write_text("not ours\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main([*run.argv(**{"--out": run.sub}), "--bogus"])
    assert inside.read_text(encoding="utf-8") == "not ours\n"


def test_evaluate_refuses_a_junction_in_the_submission_with_evaluate_wording(tmp_path, monkeypatch, capsys):
    run = _Run(tmp_path)
    _stale_outputs(run.out)
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = run.sub / "linked"
    if os.name == "nt":
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if proc.returncode != 0:
            pytest.skip(f"cannot create a junction: {proc.stderr.strip()}")
    else:
        os.symlink(target, link)
    monkeypatch.setenv("MB_ENGINE", str(tmp_path / "no-such-engine"))
    assert cli.main(run.argv()) == cli.EXIT_SEAL
    err = capsys.readouterr().err
    assert "refusing to evaluate" in err and "refusing to seal" not in err
    assert _no_outputs(run.out)


# ---- child metering (proctree.TreeMeter, usage fields child_cpu_seconds and child_peak_rss_mb) -------------------

# Each decision burns 0.3 s of CPU and keeps 50 MB resident. The first decision starts a grandchild that burns 0.5 s
# of CPU and keeps 100 MB until its stdin closes (the child exits): the metering must reach descendants too.
METERED_CHILD = r'''
import json, subprocess, sys, time
GRANDCHILD = """
import sys, time
held = b"x" * (100 * 2**20)
end = time.process_time() + 0.5
while time.process_time() < end:
    pass
sys.stdin.read()
"""
held, grand = [], None
for line in sys.stdin:
    req = json.loads(line)
    if grand is None:
        grand = subprocess.Popen([sys.executable, "-c", GRANDCHILD], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    end = time.process_time() + 0.3
    while time.process_time() < end:
        pass
    held.append(b"x" * (50 * 2**20))
    print(json.dumps({"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"], "id": req["id"]}),
          flush=True)
'''


@pytest.mark.skipif(not (sys.platform.startswith("linux") or sys.platform == "win32"),
                    reason="requires Linux /proc or the Windows process API")
def test_child_metering_sums_the_process_tree_and_reports_peak_memory(tmp_path, monkeypatch):
    from mentat_bench.adapters import proctree
    from mentat_bench.adapters.base import check_usage
    from mentat_bench.gateway import usage_totals

    monkeypatch.setattr(proctree, "SAMPLE_EVERY_S", 0.0)  # every request is sampled
    script = tmp_path / "metered_child.py"
    script.write_text(METERED_CHILD, encoding="utf-8")
    records = []
    with LocalJSONLAdapter([sys.executable, str(script)], timeout_s=60) as adapter:
        for i in range(5):
            _msg, record = adapter.decide_binding({**CTX, "id": f"toy:m{i}:1"})
            records.append(record)
    cpu = [r["child_cpu_seconds"] for r in records]
    peak = [r["child_peak_rss_mb"] for r in records]
    assert all(c >= 0 for c in cpu) and peak == sorted(peak)  # deltas never go negative, the peak never falls
    assert sum(cpu) >= 1.5, cpu  # the child burnt 5 x 0.3 s, the grandchild 0.5 s more
    assert peak[-1] >= 200, peak  # 5 x 50 MB held by the child, 100 MB by the grandchild
    assert all(check_usage(r)["child_peak_rss_mb"] == r["child_peak_rss_mb"] for r in records)
    totals = usage_totals({"calls": records})
    assert totals["child_cpu_seconds"] == pytest.approx(sum(cpu), abs=1e-5)
    assert totals["child_peak_rss_mb"] == peak[-1]


@pytest.mark.skipif(not (sys.platform.startswith("linux") or sys.platform == "win32"),
                    reason="requires Linux /proc or the Windows process API")
def test_metering_is_sampled_at_most_once_per_interval(tmp_path, monkeypatch):
    from mentat_bench.adapters import proctree

    monkeypatch.setattr(proctree, "SAMPLE_EVERY_S", 3600.0)  # the second request is always inside the interval
    script = tmp_path / "metered_child.py"
    script.write_text(METERED_CHILD, encoding="utf-8")
    with LocalJSONLAdapter([sys.executable, str(script)], timeout_s=60) as adapter:
        _msg, first = adapter.decide_binding(CTX)
        _msg, second = adapter.decide_binding({**CTX, "id": "toy:m2:1"})
    assert "child_cpu_seconds" in first and "child_cpu_seconds" not in second


def test_failed_metering_omits_the_child_fields_and_changes_nothing_else(tmp_path, monkeypatch):
    from mentat_bench.gateway import usage_totals
    from mentat_bench.adapters import proctree

    def unreadable(pid):
        raise OSError("process table unreadable")

    script = tmp_path / "metered_child.py"
    script.write_text(METERED_CHILD, encoding="utf-8")
    monkeypatch.setattr(proctree, "_tree", unreadable)
    with LocalJSONLAdapter([sys.executable, str(script)], timeout_s=60) as adapter:
        _msg, record = adapter.decide_binding(CTX)
    assert "child_cpu_seconds" not in record and "child_peak_rss_mb" not in record
    assert record["kind"] == "local" and record["cost_usd"] == 0.0
    assert "child_cpu_seconds" not in usage_totals({"calls": [record]})


def test_close_waits_thirty_seconds_for_the_child_to_exit(monkeypatch):
    assert local_jev.CLOSE_WAIT_S == 30.0
    monkeypatch.setattr(local_jev, "CLOSE_WAIT_S", 1.0)  # the test does not wait 30 s


def test_close_refuses_a_child_that_does_not_exit_after_its_input_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(local_jev, "CLOSE_WAIT_S", 1.0)
    adapter = LocalJSONLAdapter(_argv(tmp_path, ECHO_PROCESS + "\nimport time\ntime.sleep(60)\n", "slow_exit.py"),
                                name="slow", timeout_s=10)
    adapter.decide_binding(CTX)
    adapter.close()  # waits, then kills: the end was not clean, so the build fails on it
    assert adapter.exit_error == "did not exit within 1 s after its input closed; it was killed"


def test_build_fails_when_the_local_child_exits_nonzero_after_its_input_closes(tmp_path, capsys):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text('{"id":"toy:m1:1","text":"q one","label":"Yes","event_title":null}\n'
                      '{"id":"toy:m2:1","text":"q one","label":"Yes","event_title":null}\n', encoding="utf-8")
    out = tmp_path / "o"
    child = _argv(tmp_path, ECHO_PROCESS + "\nsys.exit(3)\n", "fails_at_eof.py")
    argv = ["build", "--adapter", "local", "--corpus", str(corpus), "--out", str(out), "--local-timeout", "10",
            *(f"--local-arg={part}" for part in child)]
    assert cli.main(argv) == cli.EXIT_BUILD_INCOMPLETE
    assert "build failed: the local child exited with status 3 after its input closed" in capsys.readouterr().err
    assert not out.exists()

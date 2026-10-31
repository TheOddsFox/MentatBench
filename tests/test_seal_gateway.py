import errno
import json
import math
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mentat_bench.adapters.base import AdapterError, usage
from mentat_bench import cli
from mentat_bench.gateway import BudgetExceeded, Gateway, UsageError, usage_totals, validate_usage
from mentat_bench.manifest import DEFAULT_PROFILE, ProfileError, validate_profile
from mentat_bench import seal as seal_mod

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers_seal import case_insensitive_paths, fake_clock, is_seal_temp, scoped_os  # noqa: E402
from mentat_bench.seal import BUILD_ID_FILE, INCOMPLETE_FILE, LEDGER_FILE, SEAL_FILE, SealError, seal, verify_ledger, verify_seal


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch):
    """No test reads a ledger that the environment names unless the test sets one itself."""
    monkeypatch.delenv("MB_SEAL_LEDGER", raising=False)


def _submission(tmp_path):
    d = tmp_path / "sub"
    d.mkdir()
    (d / "bindings.jsonl").write_text('{"id":"toy:m1:1"}\n', encoding="utf-8")
    (d / "theory.json").write_text('{"interpretations":["base"]}\n', encoding="utf-8")
    (d / "usage.json").write_text("{}\n", encoding="utf-8")
    return d


def _unlock(path):
    """Simulate an adversary who clears the read-only flag."""
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_seal_roundtrip_and_tamper_detection(tmp_path):
    d = _submission(tmp_path)
    record = seal(d)
    assert json.loads((d / "seal.json").read_text())["digest"] == record["digest"]
    verify_seal(d)
    verify_seal(d, expected_digest=record["digest"])
    with pytest.raises(SealError, match="does not match expected"):
        verify_seal(d, expected_digest="0" * 64)

    _unlock(d / "theory.json")
    (d / "theory.json").write_text('{"interpretations":["base"],"x":1}\n', encoding="utf-8")
    with pytest.raises(SealError, match="modified: theory.json"):
        verify_seal(d)

    (d / "theory.json").write_text('{"interpretations":["base"]}\n', encoding="utf-8")
    verify_seal(d)

    (d / "extra.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(SealError, match="added: extra.jsonl"):
        verify_seal(d)
    (d / "extra.jsonl").unlink()

    _unlock(d / "usage.json")
    (d / "usage.json").unlink()
    with pytest.raises(SealError, match="removed: usage.json"):
        verify_seal(d)


def test_edit_then_reseal_is_refused(tmp_path):
    d = _submission(tmp_path)
    seal(d)
    _unlock(d / "theory.json")
    (d / "theory.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(SealError, match="write-once"):
        seal(d)
    with pytest.raises(SealError, match="modified: theory.json"):
        verify_seal(d)


def test_sealed_files_are_read_only(tmp_path):
    d = _submission(tmp_path)
    seal(d)
    with pytest.raises(PermissionError):
        (d / "bindings.jsonl").write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError):
        (d / "seal.json").write_text("x", encoding="utf-8")


def test_seal_detects_forged_digest_and_unsealed(tmp_path):
    d = _submission(tmp_path)
    with pytest.raises(SealError, match="not sealed"):
        verify_seal(d)
    seal(d)
    _unlock(d / "seal.json")
    record = json.loads((d / "seal.json").read_text())
    record["digest"] = "0" * 64
    (d / "seal.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SealError, match="digest"):
        verify_seal(d)


def test_seal_hashes_subdirectories_recursively(tmp_path):
    d = _submission(tmp_path)
    (d / "parts").mkdir()
    (d / "parts" / "more.jsonl").write_text("{}\n", encoding="utf-8")
    record = seal(d)
    assert "parts/more.jsonl" in record["files"]
    verify_seal(d)
    _unlock(d / "parts" / "more.jsonl")
    (d / "parts" / "more.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    with pytest.raises(SealError, match="modified: parts/more.jsonl"):
        verify_seal(d)


def test_seal_refuses_symlinks(tmp_path):
    d = _submission(tmp_path)
    try:
        os.symlink(d / "theory.json", d / "link.json")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links need privileges on this platform")
    with pytest.raises(SealError, match="link.json is not a regular file"):
        seal(d)


def test_missing_directory_is_a_seal_error(tmp_path):
    with pytest.raises(SealError, match="not a directory"):
        seal(tmp_path / "nope")
    with pytest.raises(SealError, match="not a directory"):
        verify_seal(tmp_path / "nope")


def test_malformed_seal_json_is_a_seal_error(tmp_path):
    d = _submission(tmp_path)
    seal(d)
    _unlock(d / "seal.json")
    for bad in ("not json", '{"digest": "%s"}' % ("0" * 64), '{"digest":"%s","files":{},"extra":1}' % ("0" * 64),
                '{"digest":"%s","files":{"../x":"%s"}}' % ("0" * 64, "0" * 64), '{"digest":"xyz","files":{}}',
                '{"digest":"%s","files":{"a.jsonl":"NaN"}}' % ("0" * 64)):
        (d / "seal.json").write_text(bad, encoding="utf-8")
        with pytest.raises(SealError, match="malformed seal.json"):
            verify_seal(d)


def test_expected_digest_is_normalised(tmp_path):
    d = _submission(tmp_path)
    record = seal(d)
    verify_seal(d, expected_digest="  " + record["digest"].upper() + "\n")
    with pytest.raises(SealError, match="SHA-256 hex"):
        verify_seal(d, expected_digest="not-a-digest")


def test_a_deleted_seal_json_is_written_back_with_the_ledgered_digest(tmp_path):
    d = _submission(tmp_path)
    record = seal(d)
    ledger = (tmp_path / LEDGER_FILE).read_text(encoding="utf-8").splitlines()
    assert len(ledger) == 1
    entry = json.loads(ledger[0])
    assert entry["digest"] == record["digest"] and entry["sealed_at_utc"].endswith("+00:00")
    _unlock(d / "seal.json")
    (d / "seal.json").unlink()
    seal(d)  # unchanged: the ledgered digest is written back, and only that digest
    assert json.loads((d / "seal.json").read_text(encoding="utf-8"))["digest"] == record["digest"]


def test_seal_ledger_is_per_directory_and_refuses_malformed_lines(tmp_path):
    a = _submission(tmp_path)
    b_parent = tmp_path / "b"
    b_parent.mkdir()
    b = b_parent / "sub"
    b.mkdir()
    (b / "x.jsonl").write_text("{}\n", encoding="utf-8")
    seal(a)
    seal(b)  # different parent, so its own ledger
    assert len((tmp_path / LEDGER_FILE).read_text().splitlines()) == 1
    assert len((b_parent / LEDGER_FILE).read_text().splitlines()) == 1
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "y.jsonl").write_text("{}\n", encoding="utf-8")
    with open(tmp_path / LEDGER_FILE, "a", encoding="utf-8") as fh:
        fh.write("garbage\n")
    with pytest.raises(SealError, match="malformed"):
        seal(tmp_path / "c")


def test_peak_rss_is_positive():
    from mentat_bench.gateway import peak_rss_mb

    assert peak_rss_mb() > 0


def test_adapter_error_usage_is_charged():
    class Costly:
        name = "costly"

        def decide_binding(self, ctx):
            raise AdapterError("rate limited", usage=usage("costly", "llm", 7, 0, 0.25))

        def decide_relation(self, a, b):
            raise AdapterError("x")

    gw = Gateway(Costly(), spend_ceiling_usd=1.0, build_deadline_s=1e9, max_retries=1)
    with pytest.raises(AdapterError, match="failed after 2 attempts"):
        gw.decide_binding({"id": "x"})
    assert [c["tokens_in"] for c in gw.calls] == [7, 7]
    assert gw.spend_usd == pytest.approx(0.5)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _FixedCost:
    name = "fixed"

    def __init__(self, cost):
        self.cost = cost
        self.calls = 0

    def decide_binding(self, ctx):
        self.calls += 1
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["x"]}, usage("m", "llm", 10, 5, self.cost)

    def decide_relation(self, a, b):
        self.calls += 1
        return "NONE", usage("m", "llm", 1, 1, self.cost)


def test_gateway_spend_ceiling_discards_the_overshooting_call():
    adapter = _FixedCost(0.4)
    gw = Gateway(adapter, spend_ceiling_usd=1.0, build_deadline_s=1e9)
    for _ in range(2):  # spend goes 0.4, 0.8
        gw.decide_binding({"id": "x"})
    with pytest.raises(BudgetExceeded, match="spend"):  # third call: 1.2 > 1.0, decision discarded
        gw.decide_binding({"id": "x"})
    assert adapter.calls == 3
    report = {"calls": list(gw.calls)}  # the gateway's charged calls (no sink)
    assert len(report["calls"]) == 3  # the overshooting call is still charged
    assert report["calls"][0]["tokens_in"] == 10 and report["calls"][0]["kind"] == "llm"
    totals = usage_totals(report)
    assert totals["calls"] == 3 and totals["cost_usd"] == pytest.approx(1.2)


def test_gateway_deadline_discards_call_that_runs_past_it():
    clock = _Clock()

    class Slow(_FixedCost):
        def decide_relation(self, a, b):
            clock.now += 10.0
            return super().decide_relation(a, b)

    adapter = Slow(0.0)
    gw = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=25.0, clock=clock)
    for _ in range(2):  # calls end at 10 and 20
        gw.decide_relation({"id": "a"}, {"id": "b"})
    with pytest.raises(BudgetExceeded, match="model-call stop"):  # this one ends at 30 > 25
        gw.decide_relation({"id": "a"}, {"id": "b"})
    assert adapter.calls == 3 and len(gw.calls) == 3


def test_gateway_passes_remaining_time_to_adapters_that_accept_it():
    seen = []

    class Timed(_FixedCost):
        supports_timeout = True

        def decide_binding(self, ctx, timeout_s=None):
            seen.append(timeout_s)
            return super().decide_binding(ctx)

    clock = _Clock()
    gw = Gateway(Timed(0.0), spend_ceiling_usd=1.0, build_deadline_s=100.0, clock=clock)
    clock.now = 40.0
    gw.decide_binding({"id": "x"})
    assert seen == [pytest.approx(60.0)]


def test_gateway_discards_nonfinite_or_negative_costs_and_retries():
    class Bad:
        name = "bad"

        def __init__(self, costs):
            self.costs = list(costs)

        def decide_binding(self, ctx):
            cost = self.costs.pop(0)
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["m"]}, usage("bad", "llm", 1, 1, cost)

        def decide_relation(self, a, b):
            raise AdapterError("unused")

    for cost in (float("nan"), float("inf"), -1.0):
        gw = Gateway(Bad([cost, 0.5]), spend_ceiling_usd=10.0, build_deadline_s=1e9, max_retries=1)
        assert gw.decide_binding({"id": "x"})["missing"] == ["m"]
        assert [c["cost_usd"] for c in gw.calls] == [0.0, 0.5]  # the malformed attempt charges nothing
        assert gw.calls[1]["retry"] is True

    gw = Gateway(Bad([float("nan")]), spend_ceiling_usd=10.0, build_deadline_s=1e9, max_retries=0)
    with pytest.raises(AdapterError, match="failed after 1 attempts"):
        gw.decide_binding({"id": "x"})
    assert gw.spend_usd == 0.0


def test_gateway_rejects_fractional_and_null_tokens():
    class Tokens:
        name = "t"

        def decide_binding(self, ctx):
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["m"]}, usage("t", "llm", None, 1.5, 0.1)

        def decide_relation(self, a, b):
            raise AdapterError("unused")

    gw = Gateway(Tokens(), spend_ceiling_usd=10.0, build_deadline_s=1e9, max_retries=0)
    with pytest.raises(AdapterError):
        gw.decide_binding({"id": "x"})
    assert gw.calls[0]["cost_usd"] == 0.0 and gw.calls[0]["tokens_in"] == 0


def test_gateway_rejects_nonfinite_caps():
    with pytest.raises(ValueError):
        Gateway(_FixedCost(0.0), spend_ceiling_usd=float("nan"), build_deadline_s=1.0)
    with pytest.raises(ValueError):
        Gateway(_FixedCost(0.0), spend_ceiling_usd=1.0, build_deadline_s=float("inf"))


def test_gateway_retries_transient_errors_and_records_them():
    class Flaky:
        name = "flaky"

        def __init__(self):
            self.attempts = 0

        def decide_binding(self, ctx):
            self.attempts += 1
            if self.attempts == 1:
                raise AdapterError("transient")
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["m"]}, usage("flaky")

        def decide_relation(self, a, b):
            raise AdapterError("always")

    gw = Gateway(Flaky(), spend_ceiling_usd=1.0, build_deadline_s=1e9, max_retries=2)
    decision = gw.decide_binding({"id": "x"})
    assert decision["missing"] == ["m"]
    calls = gw.calls
    assert [c["retry"] for c in calls] == [False, True]
    assert calls[0]["cost_usd"] == 0.0

    with pytest.raises(AdapterError, match="failed after 3 attempts"):
        gw.decide_relation({"id": "a"}, {"id": "b"})
    assert len(gw.calls) == 2 + 3


def test_gateway_rejects_unknown_usage_kind():
    class Bad:
        name = "bad"

        def decide_binding(self, ctx):
            return {}, {"model": "m", "kind": "gpu", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}

    gw = Gateway(Bad(), spend_ceiling_usd=1.0, build_deadline_s=1e9)
    with pytest.raises(ValueError, match="usage kind"):
        gw.decide_binding({"id": "x"})


def test_usage_totals_tolerates_missing_and_null_fields():
    totals = usage_totals({"calls": [{"kind": "llm", "cost_usd": None, "retry": True}, {"tokens_in": 4, "cost_usd": 0.5}]})
    assert totals["calls"] == 2 and totals["retries"] == 1
    assert totals["tokens_in"] == 4 and totals["cost_usd"] == pytest.approx(0.5)
    assert totals["wall_seconds"] is None
    with pytest.raises(ValueError):
        usage_totals([])
    with pytest.raises(ValueError):
        usage_totals({"calls": ["x"]})


def _usage_doc(**call_overrides):
    call = {"model": "m", "kind": "llm", "tokens_in": 3, "tokens_out": 2, "seconds": 0.5, "cost_usd": 0.25}
    call.update(call_overrides)
    return {"calls": [call], "wall_seconds": 1.0, "peak_rss_mb": 10.0}


def test_child_metering_fields_are_optional_finite_and_nonnegative():
    from mentat_bench.adapters.base import check_usage

    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=1.0, build_deadline_s=10.0)
    base = {"model": "m", "kind": "local", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0}
    assert "child_cpu_seconds" not in check_usage(base)
    assert check_usage({**base, "child_cpu_seconds": 1.5, "child_peak_rss_mb": 2.0})["child_peak_rss_mb"] == 2.0
    for bad in ({"child_cpu_seconds": -1.0}, {"child_peak_rss_mb": float("inf")}, {"child_peak_rss_mb": None}):
        with pytest.raises(AdapterError):
            check_usage({**base, **bad})
    doc = _usage_doc(kind="local", child_cpu_seconds=0.5, child_peak_rss_mb=3.0)
    totals = validate_usage(doc, profile)
    assert totals["child_cpu_seconds"] == pytest.approx(0.5) and totals["child_peak_rss_mb"] == pytest.approx(3.0)
    assert "child_cpu_seconds" not in usage_totals(_usage_doc())  # absent when no call carries it


def test_validate_usage_schema_and_caps():
    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=1.0, build_deadline_s=10.0)
    assert validate_usage(_usage_doc(), profile)["cost_usd"] == pytest.approx(0.25)
    assert validate_usage({"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}, profile)["calls"] == 0
    bad_docs = [
        [],
        {"wall_seconds": 0.0, "peak_rss_mb": 0.0},
        _usage_doc(cost_usd=float("nan")),
        _usage_doc(cost_usd=-1.0),
        _usage_doc(tokens_in=None),
        _usage_doc(kind="gpu"),
        _usage_doc(retry="yes"),
        _usage_doc(seconds=math.inf),
        dict(_usage_doc(), wall_seconds=None),
        _over_then_ok(cost_usd=2.0),  # accepted calls exceed spend_ceiling_usd before the last call
        _over_then_ok(seconds=11.0),  # accepted calls exceed build_deadline_s before the last call
    ]
    for doc in bad_docs:
        with pytest.raises(UsageError):
            validate_usage(doc, profile)
    assert "retry" not in _usage_doc()["calls"][0]  # retry is optional and defaults to false
    validate_usage(_usage_doc(retry=True), profile)


def _over_then_ok(**overrides):
    """Two calls: a call that overshoots (`overrides`) and is NOT last, then a call under the caps."""
    doc = _usage_doc(cost_usd=0.5, seconds=0.5)
    doc["calls"].insert(0, {**doc["calls"][0], **overrides})
    return doc


def test_overshooting_last_call_is_the_discarded_one(tmp_path):
    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=1.0, build_deadline_s=10.0)
    # One call that overshoots and is last: it was discarded by the gateway, so the caps are not compared.
    totals = validate_usage(_usage_doc(cost_usd=5.0, seconds=30.0), profile)
    assert totals["cost_usd"] == pytest.approx(5.0)
    # Only the final call may overshoot: an overshooting call followed by another call is not a discard.
    with pytest.raises(UsageError):
        validate_usage(_over_then_ok(cost_usd=5.0), profile)


def test_deadline_rule_start_refused_at_deadline_finish_at_deadline_accepted():
    clock = _Clock()

    class Steady(_FixedCost):
        def decide_binding(self, ctx):
            clock.now += 10.0  # each call takes exactly 10 seconds
            return super().decide_binding(ctx)

    gw = Gateway(Steady(0.0), spend_ceiling_usd=10.0, build_deadline_s=20.0, clock=clock)
    gw.decide_binding({"id": "x"})  # starts at 0, ends at 10
    gw.decide_binding({"id": "x"})  # starts at 10, ends exactly at 20: accepted
    with pytest.raises(BudgetExceeded, match="model-call stop reached"):  # starts at 20: refused, nothing charged
        gw.decide_binding({"id": "x"})
    assert len(gw.calls) == 2 and all(not c.get("discarded") for c in gw.calls)


def test_stop_message_names_the_profile_deadline_when_the_stop_is_a_reserve_before_it():
    clock = _Clock()

    class Steady(_FixedCost):
        def decide_binding(self, ctx):
            clock.now += 10.0
            return super().decide_binding(ctx)

    gw = Gateway(Steady(0.0), spend_ceiling_usd=10.0, build_deadline_s=19.0, clock=clock, profile_deadline_s=20.0)
    gw.decide_binding({"id": "x"})  # starts at 0, ends at 10
    with pytest.raises(BudgetExceeded, match=r"ran past the model-call stop: 20.000s > 19.0s \(build_deadline_s 20.0s; the stop is 0.95 of it\)"):
        gw.decide_binding({"id": "x"})  # starts at 10, ends at 20 > 19: discarded and charged


def test_seal_refuses_junctions_and_symlinks_at_depth(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "x.jsonl").write_text("{}\n", encoding="utf-8")

    def make_link(link):
        if os.name == "nt":
            import subprocess

            proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
            if proc.returncode != 0:
                pytest.skip(f"cannot create a junction: {proc.stderr.strip()}")
        else:
            os.symlink(target, link)

    fresh = _submission(tmp_path)
    make_link(fresh / "linked")
    with pytest.raises(SealError, match="symbolic link or junction"):
        seal(fresh)  # a link at depth is refused while sealing

    d = tmp_path / "sealed_then_linked"
    d.mkdir()
    (d / "bindings.jsonl").write_text('{"id":"toy:m1:1"}\n', encoding="utf-8")
    seal(d)
    make_link(d / "linked")
    with pytest.raises(SealError, match="symbolic link or junction"):
        verify_seal(d)  # and so is a link added after sealing


def test_renaming_a_sealed_directory_does_not_allow_resealing(tmp_path):
    d = _submission(tmp_path)
    seal(d)
    _unlock(d / "seal.json")
    (d / "seal.json").unlink()
    moved = tmp_path / "renamed"
    os.rename(d, moved)
    with pytest.raises(SealError, match="seal ledger entry"):  # same build_id, new path
        seal(moved)
    _unlock(moved / "seal.json") if (moved / "seal.json").exists() else None
    with pytest.raises(SealError, match="seal ledger entry"):
        seal(moved)


def test_deleting_seal_and_build_id_in_place_does_not_allow_resealing(tmp_path):
    d = _submission(tmp_path)
    seal(d)
    _unlock(d / "seal.json")
    (d / "seal.json").unlink()
    _unlock(d / "build_id.json")
    (d / "build_id.json").unlink()
    with pytest.raises(SealError, match="seal ledger entry"):  # same path, new build_id
        seal(d)


def test_build_id_is_sealed_and_stable(tmp_path):
    from mentat_bench.seal import BUILD_ID_FILE, ensure_build_id

    d = _submission(tmp_path)
    build_id = ensure_build_id(d)
    assert ensure_build_id(d) == build_id  # idempotent
    record = seal(d)
    assert BUILD_ID_FILE in record["files"]
    assert json.loads((d / BUILD_ID_FILE).read_text())["build_id"] == build_id
    _unlock(d / BUILD_ID_FILE)
    (d / BUILD_ID_FILE).write_text('{"build_id": "%s"}\n' % ("0" * 32), encoding="utf-8")
    with pytest.raises(SealError, match="modified: build_id.json"):
        verify_seal(d)


def test_seal_ledger_lock_is_respected(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    monkeypatch.setattr(seal_mod, "LOCK_WAIT_S", 0.1)
    with seal_mod._ledger_lock(tmp_path / LEDGER_FILE):  # another seal holds the OS lock
        with pytest.raises(SealError, match="held by another seal"):
            seal(d)
    assert not (d / "seal.json").exists() and not (d / BUILD_ID_FILE).exists()
    seal(d)  # released: the seal goes through


def test_a_failed_seal_json_publish_after_the_ledger_append_is_finished_by_the_next_seal(tmp_path, monkeypatch):
    """Ledger first: the entry is appended, then seal.json cannot be written. The entry and the build_id stay (the entry
    names the build); the next seal publishes seal.json with the ledgered digest, without a second ledger entry."""
    d = _submission(tmp_path)
    real = seal_mod._write_exclusive

    def disk_full(path, text):
        if is_seal_temp(path):
            raise OSError("disk full")
        return real(path, text)

    monkeypatch.setattr(seal_mod, "_write_exclusive", disk_full)
    with pytest.raises(SealError, match="cannot write seal.json"):
        seal(d)
    lines = (tmp_path / LEDGER_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and not (d / SEAL_FILE).exists() and (d / BUILD_ID_FILE).exists()
    monkeypatch.setattr(seal_mod, "_write_exclusive", real)
    record = seal(d)
    assert json.loads(lines[0])["digest"] == record["digest"]
    assert (tmp_path / LEDGER_FILE).read_text(encoding="utf-8").splitlines() == lines
    verify_seal(d)


def test_an_interrupt_during_the_seal_json_write_leaves_no_temp_file_and_the_next_seal_finishes(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    real = seal_mod._write_exclusive

    def interrupted(path, text):
        if is_seal_temp(path):
            with open(path, "x", encoding="utf-8") as fh:  # a partly written temp file, then the interrupt
                fh.write(text[:10])
            raise KeyboardInterrupt
        return real(path, text)

    monkeypatch.setattr(seal_mod, "_write_exclusive", interrupted)
    with pytest.raises(KeyboardInterrupt):
        seal(d)
    assert not (d / SEAL_FILE).exists() and (d / BUILD_ID_FILE).exists()
    assert not [p for p in tmp_path.iterdir() if is_seal_temp(p)]  # the partial temp file is removed
    assert len((tmp_path / LEDGER_FILE).read_text(encoding="utf-8").splitlines()) == 1
    monkeypatch.setattr(seal_mod, "_write_exclusive", real)
    seal(d)
    verify_seal(d)


def test_a_seal_json_publish_that_meets_another_sealers_file_never_removes_that_file(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    other = '{"digest": "not ours", "files": {}}\n'
    real_link = os.link

    def another_sealer_first(src, dst):
        if Path(dst).name == SEAL_FILE:
            Path(dst).write_text(other, encoding="utf-8")  # the other sealer's file, created an instant earlier
            raise FileExistsError(str(dst))
        return real_link(src, dst)

    scoped_os(monkeypatch, link=another_sealer_first)
    with pytest.raises(SealError, match="already sealed"):
        seal(d)
    assert (d / SEAL_FILE).read_text(encoding="utf-8") == other  # not deleted by this seal


def test_a_build_id_another_sealer_wrote_is_kept_when_this_seal_fails(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    build_id = '{"build_id": "%s"}\n' % ("ab" * 16)
    (d / BUILD_ID_FILE).write_text(build_id, encoding="utf-8")  # written by the other sealer before this one started
    real = seal_mod._write_exclusive

    def disk_full(path, text):
        if is_seal_temp(path):
            raise OSError("disk full")
        return real(path, text)

    monkeypatch.setattr(seal_mod, "_write_exclusive", disk_full)
    with pytest.raises(SealError, match="cannot write seal.json"):
        seal(d)
    assert (d / BUILD_ID_FILE).read_text(encoding="utf-8") == build_id


@pytest.mark.parametrize("failure", ["enospc", "interrupt"])
def test_a_failed_build_id_write_removes_its_partial_file(tmp_path, monkeypatch, failure):
    d = _submission(tmp_path)
    real = seal_mod._write_exclusive

    def partial_then_fail(path, text):
        if path.name == BUILD_ID_FILE:
            with open(path, "x", encoding="utf-8") as fh:  # the partly written file, then the failure
                fh.write(text[:5])
            if failure == "enospc":
                raise OSError(errno.ENOSPC, "No space left on device")
            raise KeyboardInterrupt
        return real(path, text)

    monkeypatch.setattr(seal_mod, "_write_exclusive", partial_then_fail)
    with pytest.raises(KeyboardInterrupt if failure == "interrupt" else SealError,
                       match=None if failure == "interrupt" else "cannot write build_id.json"):
        seal(d)
    assert not (d / BUILD_ID_FILE).exists() and not (d / SEAL_FILE).exists() and not (tmp_path / LEDGER_FILE).exists()
    monkeypatch.setattr(seal_mod, "_write_exclusive", real)
    seal(d)
    verify_seal(d)


def test_a_failed_ledger_append_leaves_no_entry_and_the_directory_is_sealable(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    real = seal_mod._ledger_append

    def disk_full(ledger, line):
        raise OSError("disk full")

    monkeypatch.setattr(seal_mod, "_ledger_append", disk_full)
    with pytest.raises(SealError, match="cannot record the seal"):
        seal(d)
    assert not (d / SEAL_FILE).exists() and not (d / BUILD_ID_FILE).exists() and not (tmp_path / LEDGER_FILE).exists()
    monkeypatch.setattr(seal_mod, "_ledger_append", real)
    seal(d)
    verify_seal(d)


def test_a_ledger_append_that_persisted_then_raised_is_finished_by_the_next_seal(tmp_path, monkeypatch):
    """The entry reached the ledger before the error: this seal fails, the entry stays, and the next seal of the directory
    publishes seal.json with the ledgered digest."""
    d = _submission(tmp_path)
    real = seal_mod._ledger_append

    def persist_then_fail(ledger, line):
        real(ledger, line)
        raise OSError("fsync failed after the write")

    monkeypatch.setattr(seal_mod, "_ledger_append", persist_then_fail)
    with pytest.raises(SealError, match="cannot record the seal"):
        seal(d)
    lines = (tmp_path / LEDGER_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and not (d / SEAL_FILE).exists() and (d / BUILD_ID_FILE).exists()
    monkeypatch.setattr(seal_mod, "_ledger_append", real)
    record = seal(d)
    assert json.loads(lines[0])["digest"] == record["digest"]
    verify_seal(d)


def test_a_partial_seal_json_temp_file_held_open_elsewhere_is_removed_with_retry(tmp_path, monkeypatch):
    real_unlink, real_write = os.unlink, seal_mod._write_exclusive
    d = _submission(tmp_path)

    def partial_then_disk_full(path, text):
        if is_seal_temp(path):
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(text[:10])
            raise OSError("disk full")
        return real_write(path, text)

    monkeypatch.setattr(seal_mod, "_write_exclusive", partial_then_disk_full)
    held = [2]  # the first two removals meet a sharing violation (Windows: another process has the file open)

    def sharing_violation(path, *args, **kwargs):
        if is_seal_temp(path) and held[0]:
            held[0] -= 1
            raise PermissionError("the file is open in another process")
        return real_unlink(path, *args, **kwargs)

    clock = fake_clock(monkeypatch)
    scoped_os(monkeypatch, unlink=sharing_violation)
    with pytest.raises(SealError, match="cannot write seal.json"):
        seal(d)
    assert held[0] == 0 and not [p for p in tmp_path.iterdir() if is_seal_temp(p)]
    assert len(clock.sleeps) >= 2  # the retries waited, on the fake clock


def test_a_published_seal_json_that_cannot_be_discarded_says_to_delete_it(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    real_fsync_dir = seal_mod._fsync_dir

    def directory_fsync_fails_after_the_link(directory):
        if Path(directory) == d.resolve() and (d / SEAL_FILE).exists():
            raise OSError("directory fsync failed")
        return real_fsync_dir(directory)

    real_unlink = os.unlink

    def stuck(path, *args, **kwargs):
        if Path(path).name == SEAL_FILE:
            raise PermissionError("the file is held open")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(seal_mod, "_fsync_dir", directory_fsync_fails_after_the_link)
    fake_clock(monkeypatch)
    scoped_os(monkeypatch, unlink=stuck)
    with pytest.raises(SealError, match="cannot remove .*delete it"):
        seal(d)
    assert (d / SEAL_FILE).exists()  # the published file stays; the message names it for deletion


def test_a_ledger_named_like_the_lock_file_is_refused_in_any_case(tmp_path, monkeypatch):
    case_insensitive_paths(monkeypatch)  # Windows comparison: central.LOCK is the lock's own name
    d = _submission(tmp_path)
    for name in ("central.lock", "central.LOCK"):
        monkeypatch.setenv("MB_SEAL_LEDGER", str(tmp_path / name))
        with pytest.raises(SealError, match="lock file"):
            seal(d)
    assert not (tmp_path / "central.lock").exists()


def test_the_seal_command_reports_an_unusable_ledger_as_exit_4(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MB_SEAL_LEDGER", str(tmp_path / "central.lock"))
    assert cli.main(["seal", "--submission", str(_submission(tmp_path))]) == cli.EXIT_SEAL
    assert "lock file" in capsys.readouterr().err


def test_a_relative_ledger_is_resolved_to_an_absolute_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MB_SEAL_LEDGER", os.path.join("rel", "ledger.jsonl"))
    resolved = seal_mod.ledger_path(tmp_path / "sub")
    assert resolved.is_absolute() and resolved.resolve() == (tmp_path / "rel" / "ledger.jsonl").resolve()


def test_a_ledgered_path_that_differs_only_in_case_is_the_same_directory(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    record = seal(d)
    ledger = tmp_path / LEDGER_FILE
    entry = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    entry["submission"] = entry["submission"].upper()
    ledger.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    _unlock(d / SEAL_FILE)
    (d / SEAL_FILE).unlink()
    case_insensitive_paths(monkeypatch)
    assert seal(d)["digest"] == record["digest"]  # the kill-window recovery matches the path under Windows rules


def test_a_build_id_changed_under_the_second_lock_is_refused(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    real = seal_mod._read_build_id
    calls = []

    def changed_at_the_second_lock(submission):
        calls.append(1)
        return "f" * 32 if len(calls) == 2 else real(submission)

    monkeypatch.setattr(seal_mod, "_read_build_id", changed_at_the_second_lock)
    with pytest.raises(SealError, match="changed while it was being sealed"):
        seal(d)
    assert not (d / SEAL_FILE).exists()


def test_a_volume_without_hard_links_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    d = _submission(tmp_path)

    def no_links(src, dst):
        raise OSError(errno.EPERM, "hard links are not supported on this volume")

    scoped_os(monkeypatch, link=no_links)
    with pytest.raises(SealError, match="does not support hard links"):
        seal(d)
    assert not (tmp_path / LEDGER_FILE).exists() and not (d / BUILD_ID_FILE).exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".mentat-probe-")]


def test_the_seal_json_temp_file_is_never_part_of_a_listing(tmp_path):
    d = _submission(tmp_path)
    seal(d)
    assert not [p for p in tmp_path.iterdir() if is_seal_temp(p)]  # the temp file is removed after the publish
    expected = {"bindings.jsonl", "theory.json", "usage.json", BUILD_ID_FILE}
    assert set(seal_mod._listing(d)) == expected
    (tmp_path / ".seal.json.deadbeef.tmp").write_text("left by a killed publish", encoding="utf-8")  # beside, not inside
    assert set(seal_mod._listing(d)) == expected
    verify_seal(d)


def test_a_torn_seal_json_with_a_matching_ledger_entry_is_replaced_with_the_same_digest(tmp_path):
    d = _submission(tmp_path)
    record = seal(d)
    _unlock(d / SEAL_FILE)
    (d / SEAL_FILE).write_bytes(b"")  # zero-length: a torn write
    assert seal(d) == record
    verify_seal(d)


def test_a_torn_seal_json_without_a_ledger_entry_is_refused_and_names_the_file(tmp_path):
    d = _submission(tmp_path)
    (d / SEAL_FILE).write_text('{"digest": ', encoding="utf-8")
    with pytest.raises(SealError, match="seal.json is malformed and has no ledger entry"):
        seal(d)
    assert (d / SEAL_FILE).read_text(encoding="utf-8") == '{"digest": '  # not touched


def test_a_symbolic_link_named_seal_json_is_refused_by_seal_and_verify(tmp_path):
    d = _submission(tmp_path)
    target = tmp_path / "elsewhere.json"
    target.write_text('{"digest": "0", "files": {}}\n', encoding="utf-8")
    try:
        os.symlink(target, d / SEAL_FILE)
    except (OSError, NotImplementedError):
        pytest.skip("creating a symbolic link needs a privilege this session does not have")
    with pytest.raises(SealError, match="symbolic link"):
        seal(d)
    with pytest.raises(SealError, match="symbolic link"):
        verify_seal(d)
    with pytest.raises(SealError, match="symbolic link"):
        seal_mod._listing(d)


def test_a_link_named_seal_json_is_refused_by_listing_seal_and_verify_on_any_platform(tmp_path, monkeypatch):
    """The link check on seal.json, with the link test mocked (creating a real link needs a privilege)."""
    d = _submission(tmp_path)
    seal(d)
    real_is_link = seal_mod._is_link
    monkeypatch.setattr(seal_mod, "_is_link", lambda path: Path(path).name == SEAL_FILE or real_is_link(path))
    with pytest.raises(SealError, match="symbolic link"):
        seal_mod._listing(d)
    with pytest.raises(SealError, match="symbolic link"):
        verify_seal(d)
    with pytest.raises(SealError, match="symbolic link"):
        seal(d)


_KILL_AFTER_THE_LEDGER_APPEND = (
    "import os, sys\n"
    "from mentat_bench import seal as s\n"
    "real = s._write_exclusive\n"
    "def kill(path, text):\n"
    "    if path.name.startswith('.seal.json.'):\n"  # the publish's temp file: the kill comes before seal.json exists
    "        os._exit(9)\n"
    "    return real(path, text)\n"
    "s._write_exclusive = kill\n"
    "s.seal(sys.argv[1])\n"
)


def _killed_seal(tmp_path, monkeypatch):
    """A child process killed (os._exit, no cleanup) after the ledger append and before seal.json is published. Returns
    the directory, the ledger path and the ledger entry the kill left."""
    d = _submission(tmp_path)
    monkeypatch.delenv("MB_SEAL_LEDGER", raising=False)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "python")}
    proc = subprocess.run([sys.executable, "-c", _KILL_AFTER_THE_LEDGER_APPEND, str(d)], env=env, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 9, proc.stderr
    ledger = tmp_path / LEDGER_FILE
    assert not (d / SEAL_FILE).exists()
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    return d, ledger, json.loads(lines[0])


def test_a_hard_kill_before_seal_json_is_finished_by_the_next_seal_with_the_ledgered_digest(tmp_path, monkeypatch):
    """A real hard kill after the ledger append: one ledger entry, no seal.json. evaluate (verify_seal) refuses the
    directory, verify_ledger finds the entry, and sealing again publishes seal.json with the ledgered digest. A further
    seal is refused."""
    d, ledger, entry = _killed_seal(tmp_path, monkeypatch)
    with pytest.raises(SealError, match="is not sealed"):
        verify_seal(d)
    record = seal(d)  # the killed process released its OS lock when it died
    assert record["digest"] == entry["digest"]
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    verify_seal(d, entry["digest"])
    verify_ledger(d, ledger, entry["digest"])
    with pytest.raises(SealError, match="already sealed"):
        seal(d)


def test_a_hard_kill_before_seal_json_is_not_finished_when_the_files_changed(tmp_path, monkeypatch):
    d, ledger, entry = _killed_seal(tmp_path, monkeypatch)
    _unlock(d / "bindings.jsonl")  # the killed seal left the marks: a change needs them cleared first
    (d / "bindings.jsonl").write_text('{"id":"toy:m1:1","changed":true}\n', encoding="utf-8")
    with pytest.raises(SealError, match="changed since it was ledgered"):
        seal(d)
    assert not (d / SEAL_FILE).exists() and len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_a_failed_ledger_write_is_truncated_back_to_its_size(tmp_path, monkeypatch):
    ledger = tmp_path / LEDGER_FILE
    ledger.write_bytes(b'{"a": 1}\n')
    real_write = os.write
    calls = []

    def short_then_disk_full(fd, data):
        calls.append(1)
        if len(calls) == 1:
            return real_write(fd, bytes(data[:5]))  # a short write leaves a partial line behind ...
        raise OSError(errno.ENOSPC, "No space left on device")  # ... the next write fails

    scoped_os(monkeypatch, write=short_then_disk_full)
    with pytest.raises(OSError):
        seal_mod._ledger_append(ledger, '{"b": 2}')
    assert ledger.read_bytes() == b'{"a": 1}\n'  # truncated back: no partial line


def test_a_zero_byte_ledger_write_is_a_short_write_and_leaves_no_line(tmp_path, monkeypatch):
    ledger = tmp_path / LEDGER_FILE
    scoped_os(monkeypatch, write=lambda fd, data: 0)
    with pytest.raises(OSError, match="short write"):
        seal_mod._ledger_append(ledger, '{"b": 2}')
    assert ledger.read_bytes() == b""


def test_a_directory_sealed_under_one_digest_is_never_sealed_under_another(tmp_path):
    d = _submission(tmp_path)
    seal(d)
    _unlock(d / SEAL_FILE)
    (d / SEAL_FILE).unlink()
    _unlock(d / "bindings.jsonl")
    (d / "bindings.jsonl").write_text('{"id":"toy:m1:1","changed":true}\n', encoding="utf-8")
    with pytest.raises(SealError, match="changed since it was ledgered"):
        seal(d)
    assert not (d / SEAL_FILE).exists() and len((tmp_path / LEDGER_FILE).read_text(encoding="utf-8").splitlines()) == 1


def test_validate_profile_rejects_out_of_range_knobs():
    assert validate_profile(dict(DEFAULT_PROFILE)) == DEFAULT_PROFILE
    assert validate_profile(dict(DEFAULT_PROFILE, query_timeout_s=10_000_000))["query_timeout_s"] == 1e7
    assert validate_profile(dict(DEFAULT_PROFILE, max_expr_depth=100000))["max_expr_depth"] == 100000  # optional key
    with pytest.raises(ProfileError):
        validate_profile(dict(DEFAULT_PROFILE, max_expr_depth=0))
    with pytest.raises(ProfileError):
        validate_profile(dict(DEFAULT_PROFILE, unknown_key=1))
    bad = [
        dict(DEFAULT_PROFILE, query_timeout_s=float("nan")),
        dict(DEFAULT_PROFILE, query_timeout_s=float("inf")),
        dict(DEFAULT_PROFILE, query_timeout_s=10_000_001),
        dict(DEFAULT_PROFILE, query_timeout_s=0),
        dict(DEFAULT_PROFILE, build_deadline_s=float("nan")),
        dict(DEFAULT_PROFILE, spend_ceiling_usd=-0.5),
        dict(DEFAULT_PROFILE, spend_ceiling_usd=float("inf")),
        dict(DEFAULT_PROFILE, max_expr_nodes=2.0),
        dict(DEFAULT_PROFILE, max_expr_nodes=0),
        dict(DEFAULT_PROFILE, max_expr_nodes=2**64),
        dict(DEFAULT_PROFILE, max_weight_bits=2**32),
        dict(DEFAULT_PROFILE, max_bytes=10**400),
        dict(DEFAULT_PROFILE, max_bytes=True),
        dict(DEFAULT_PROFILE, build_deadline_s="60"),
    ]
    for profile in bad:
        with pytest.raises(ProfileError):
            validate_profile(profile)


def test_load_profile_rejects_nan_constants_and_missing_files(tmp_path):
    from mentat_bench.manifest import load_profile

    path = tmp_path / "p.json"
    path.write_text(json.dumps(dict(DEFAULT_PROFILE)).replace("60.0", "NaN"), encoding="utf-8")
    with pytest.raises(ProfileError, match="non-standard JSON constant"):
        load_profile(path)
    with pytest.raises(ProfileError, match="cannot read profile"):
        load_profile(tmp_path / "missing.json")


def test_seal_refuses_an_incomplete_build_until_allow_incomplete(tmp_path, capsys):
    d = _submission(tmp_path)
    (d / INCOMPLETE_FILE).write_text('{"reason": "adapter_fatal: the adapter failed in the relation phase"}', encoding="utf-8")
    with pytest.raises(SealError, match="incomplete build .*relation phase"):
        seal(d)
    assert not (d / SEAL_FILE).exists() and not (d / BUILD_ID_FILE).exists() and not (tmp_path / LEDGER_FILE).exists()
    assert cli.main(["seal", "--submission", str(d)]) == cli.EXIT_SEAL
    assert "relation phase" in capsys.readouterr().err
    assert cli.main(["seal", "--submission", str(d), "--allow-incomplete"]) == 0
    record = json.loads((d / SEAL_FILE).read_text())
    assert INCOMPLETE_FILE in record["files"] and set(record) == {"digest", "files"}
    verify_seal(d, expected_digest=record["digest"])


def test_seal_marker_without_a_readable_reason_still_refuses(tmp_path):
    d = _submission(tmp_path)
    (d / INCOMPLETE_FILE).write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(SealError, match="malformed marker"):
        seal(d)


def test_default_ledger_does_not_catch_a_directory_moved_and_resealed_under_another_parent(tmp_path, monkeypatch):
    # The documented residual limit: each parent has its own ledger, so a moved and re-sealed directory passes.
    monkeypatch.delenv("MB_SEAL_LEDGER", raising=False)
    d = _submission(tmp_path)
    seal(d)
    _unlock(d / SEAL_FILE)
    (d / SEAL_FILE).unlink()
    moved = tmp_path / "other" / "sub"
    moved.parent.mkdir()
    d.rename(moved)
    seal(moved)
    assert (moved / SEAL_FILE).is_file()


def test_central_ledger_refuses_a_directory_moved_and_resealed_under_another_parent(tmp_path, monkeypatch):
    central = tmp_path / "central" / "ledger.jsonl"
    central.parent.mkdir()
    monkeypatch.setenv("MB_SEAL_LEDGER", str(central))
    d = _submission(tmp_path)
    seal(d)
    assert central.is_file() and not (tmp_path / LEDGER_FILE).exists()
    _unlock(d / SEAL_FILE)
    (d / SEAL_FILE).unlink()
    moved = tmp_path / "other" / "sub"
    moved.parent.mkdir()
    d.rename(moved)
    with pytest.raises(SealError, match="seal ledger entry"):
        seal(moved)
    assert not (moved / SEAL_FILE).exists()


def test_verify_ledger_matches_the_build_and_its_digest(tmp_path, monkeypatch):
    central = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("MB_SEAL_LEDGER", str(central))
    d = _submission(tmp_path)
    record = seal(d)
    verify_ledger(d, central, record["digest"])
    with pytest.raises(SealError, match="different digest"):
        verify_ledger(d, central, "0" * 64)
    with pytest.raises(SealError, match="no entry"):
        verify_ledger(d, tmp_path / "missing.jsonl", record["digest"])

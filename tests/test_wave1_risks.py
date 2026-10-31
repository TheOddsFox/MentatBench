"""Wave 1 risks: an adapter_fatal build is marked incomplete and seal refuses it; a checkpoint write that another
process blocks (Windows sharing violation, PermissionError on os.replace) is retried, and a build that fails after its
first checkpoint keeps the work dir so it can be finalized."""

import json
import os
from pathlib import Path

import pytest

from mentat_bench import cli
from mentat_bench import seal as sealing
from mentat_bench.adapters.base import AdapterError, usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE
from mentat_bench.seal import INCOMPLETE_FILE

SHARING_VIOLATION = "The process cannot access the file because it is being used by another process"


class _Unresolved:
    """Every binding is unresolved; every relation fails, so the relation phase runs out of consecutive errors."""

    name = "unresolved"

    def decide_binding(self, ctx):
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["none"]}, usage("unresolved")

    def decide_relation(self, a, b):
        raise AdapterError("no reply")


@pytest.fixture
def sleeps(monkeypatch):
    """Records the backoff sleeps of replace_with_retry instead of sleeping."""
    recorded = []
    monkeypatch.setattr(sealing.time, "sleep", recorded.append)
    return recorded


def _block_replaces(monkeypatch, name, blocked):
    """os.replace onto a file called `name` raises a sharing violation on each attempt for which blocked(attempt)
    is true; attempts count from 1. Returns the attempt counter (a one-item list)."""
    real, attempts = os.replace, [0]

    def replace(src, dst):
        if Path(dst).name == name:
            attempts[0] += 1
            if blocked(attempts[0]):
                raise PermissionError(32, SHARING_VIOLATION)
        return real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    return attempts


def _corpus(tmp_path, n=30):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(json.dumps({"id": f"toy:m{i}:1", "text": "shared topic", "label": "Yes", "event_title": None})
                              + "\n" for i in range(1, n + 1)), encoding="utf-8")
    return corpus


def _build_argv(tmp_path, *extra):
    return ["build", "--adapter", "empty", "--corpus", str(_corpus(tmp_path)), "--out", str(tmp_path / "out"), *extra]


# ---------------------------------------------------------------- risk 1: adapter_fatal builds are marked and refused


def test_relation_phase_fatal_build_is_marked_and_seal_refuses_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_make_adapter", lambda args: _Unresolved())
    out = tmp_path / "out"
    assert cli.main(_build_argv(tmp_path, "--max-consecutive-errors", "3")) == cli.EXIT_BUILD_INCOMPLETE
    marker = json.loads((out / INCOMPLETE_FILE).read_text(encoding="utf-8"))
    assert marker["reason"] == "adapter_fatal: the adapter failed in the relation phase"
    assert marker["summary"]["adapter_fatal"] is True and marker["summary"]["outcomes"] == 30
    assert not (out / "build_id.json").exists() and not (tmp_path / "seal_ledger.jsonl").exists()
    assert cli.main(["seal", "--submission", str(out)]) == cli.EXIT_SEAL
    assert "relation phase" in capsys.readouterr().err
    assert cli.main(["seal", "--submission", str(out), "--allow-incomplete"]) == 0
    assert INCOMPLETE_FILE in json.loads((out / "seal.json").read_text(encoding="utf-8"))["files"]


# ---------------------------------------------------------------- risk 2: transient sharing violations


def test_replace_with_retry_backs_off_and_then_succeeds(tmp_path, monkeypatch, sleeps):
    src, dst = tmp_path / "x.tmp", tmp_path / "x.json"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    attempts = _block_replaces(monkeypatch, "x.json", lambda n: n <= 3)
    sealing.replace_with_retry(src, dst)
    assert attempts[0] == 4 and dst.read_text(encoding="utf-8") == "new" and not src.exists()
    assert sleeps == [0.05, 0.1, 0.2]


def test_replace_with_retry_gives_up_after_its_tries(tmp_path, monkeypatch, sleeps):
    src, dst = tmp_path / "x.tmp", tmp_path / "x.json"
    src.write_text("new", encoding="utf-8")
    attempts = _block_replaces(monkeypatch, "x.json", lambda n: True)
    with pytest.raises(PermissionError):
        sealing.replace_with_retry(src, dst)
    assert attempts[0] == sealing.REPLACE_TRIES == 8
    assert len(sleeps) == 7 and max(sleeps) <= 0.5


def test_checkpoint_write_rides_out_transient_sharing_violations(tmp_path, monkeypatch, sleeps):
    target = tmp_path / "checkpoint.json"
    target.write_text("{}", encoding="utf-8")
    attempts = _block_replaces(monkeypatch, "checkpoint.json", lambda n: n <= 3)
    fixed._atomic_write(target, '{"ok": 1}')
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": 1} and attempts[0] == 4


def test_build_survives_blocked_checkpoint_writes(tmp_path, monkeypatch, sleeps):
    attempts = _block_replaces(monkeypatch, "checkpoint.json", lambda n: n in (1, 4))  # the first try of two writes
    assert cli.main(_build_argv(tmp_path)) == 0
    assert attempts[0] > 2
    assert cli.main(["seal", "--submission", str(tmp_path / "out")]) == 0


# ---------------------------------------------------------------- risk 2: a failed build keeps its checkpoint


def test_build_failing_after_a_checkpoint_keeps_the_work_dir_for_finalize(tmp_path, monkeypatch, sleeps, capsys):
    _block_replaces(monkeypatch, "checkpoint.json", lambda n: n >= 2)  # the first checkpoint (after bindings) lands
    out, work = tmp_path / "out", tmp_path / "out.build"
    assert cli.main(_build_argv(tmp_path)) == cli.EXIT_BUILD_INCOMPLETE
    assert not out.exists() and (work / "checkpoint.json").is_file()
    err = capsys.readouterr().err
    assert "--resume" in err and "fixed.finalize_checkpoint" not in err  # the hint names --resume, the way to continue
    monkeypatch.undo()  # the other process is gone
    summary = fixed.finalize_checkpoint(work, out, dict(DEFAULT_PROFILE))
    assert summary["outcomes"] == 30
    assert cli.main(["seal", "--submission", str(out)]) == 0


def test_failure_before_any_checkpoint_keeps_the_work_dir_that_holds_charged_calls(tmp_path, monkeypatch, sleeps):
    """The bindings were charged (their usage rows are on disk) and no checkpoint landed: the work dir is the only record of
    those calls, so it is kept. A work dir with no charged call is still removed (test_resume)."""
    _block_replaces(monkeypatch, "checkpoint.json", lambda n: True)
    assert cli.main(_build_argv(tmp_path)) == cli.EXIT_BUILD_INCOMPLETE
    assert not (tmp_path / "out").exists() and (tmp_path / "out.build" / "usage_calls.jsonl").stat().st_size > 0


def test_failed_build_leaves_a_preexisting_empty_out_dir_empty(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    out.mkdir()

    def partial_then_incomplete(records, adapter, out_dir, *args, **kwargs):
        Path(out_dir).joinpath("bindings.jsonl").write_text("", encoding="utf-8")  # written before the base check stops
        raise fixed.BaseCheckIncomplete("the build deadline passed")

    monkeypatch.setattr(cli.fixed, "build", partial_then_incomplete)
    assert cli.main(_build_argv(tmp_path)) == cli.EXIT_BUILD_INCOMPLETE
    assert out.is_dir() and list(out.iterdir()) == []  # the directory is as it was, so a later finalize can write there

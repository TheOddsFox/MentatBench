"""The base repair's progress lines, the contradiction fields in cell.json and the report's dropped% column."""

import json
import sys
from pathlib import Path

import pytest

from mentat_bench import report, runcell
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import _BaseCheck, _prune_in_process, _Unit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers_pa_engine import make_fake_engine  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "toy-local-v0.json"


class _NoRows:
    ids = ["v:x0:1"]

    def rows(self):
        return iter(())


def test_a_slow_repair_prints_progress_lines_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(fixed, "PRUNE_PROGRESS_EVERY", 0.0)  # a line on every pass, for the test
    units = [_Unit("p0000000", None, -1, [(1, 1)], False, "relation"), _Unit("p0000001", None, -1, [(0, 0)], False, "relation")]
    _prune_in_process(_BaseCheck(_NoRows(), units, 1), 10, 10, lambda: 1e9, method="scc")
    err = capsys.readouterr().err
    assert "finalize: repairing the base" in err and "unsatisfied variables" in err


def _fixture(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "gold").mkdir(parents=True)
    (fixture / "corpus.jsonl").write_text("".join(
        json.dumps({"id": f"v:m{i}:1", "text": f"Will X{i} win the cup?", "label": "Yes", "event_title": "E"}) + "\n"
        for i in range(1, 5)), encoding="utf-8")
    (fixture / "queries.jsonl").write_text(json.dumps({"family": "closure", "qid": "c1", "hyp": []}) + "\n", encoding="utf-8")
    return fixture


def test_the_cell_carries_the_contradiction_fields_and_the_report_shows_dropped_pct(tmp_path, monkeypatch):
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    fixture = _fixture(tmp_path)
    (fixture / "gold" / "answers.jsonl").write_text("", encoding="utf-8")  # a required file, so it can be fingerprinted
    runcell.run_cell(fixture, "empty", [], out, PROFILE, [])
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["relations_asked"] > 0 and cell["relations_dropped"] == 0 and cell["contradiction_rate"] == 0.0
    assert cell["resumed"] is False and cell["prior_calls"] == 0
    row = report.run_row(out)
    assert row["dropped_pct"] == 0.0 and row["relations_asked"] == cell["relations_asked"] and row["resumed"] is False
    assert "dropped%" in report.markdown([row])


def test_a_stalled_build_leaves_a_stack_trace_in_its_work_dir(tmp_path, monkeypatch):
    """A build that stalls leaves the stack of its threads in <work>/stall_trace.txt (repeated while it stalls)."""
    import time as _time

    from mentat_bench.adapters.base import usage
    from mentat_bench.manifest import DEFAULT_PROFILE

    class _Slow:
        name = "slow"

        def decide_binding(self, ctx):
            _time.sleep(0.6)  # longer than the watch interval below
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["x"]}, usage(self.name)

    class _Stop(Exception):
        pass

    def stop(*args, **kwargs):
        raise _Stop()

    monkeypatch.setattr(fixed, "STALL_TRACE_EVERY", 0.2)
    monkeypatch.setattr(fixed, "finalize_checkpoint", stop)
    recs = [{"id": "v:m1:1", "question": "Will X win?", "label": "Yes", "event_title": "E"}]
    with pytest.raises(_Stop):
        fixed.build(recs, _Slow(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    trace = (tmp_path / "sub.build" / "stall_trace.txt").read_text(encoding="utf-8")
    assert "Thread" in trace or "File" in trace  # faulthandler's dump of the stack

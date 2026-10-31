"""Round 4: group-drop caps (count and rate), base_repaired and the strict score, runcell --resume straight to finalize on a
kept work dir whose decision phases are complete, and the heartbeat watchdog (silent while a long build progresses)."""

import json
import sys
import time
from pathlib import Path

import pytest

from mentat_bench import cli, report, runcell
from mentat_bench.adapters.base import usage
from mentat_bench.adapters.mock import EmptyAdapter
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import BaseInconsistentError, _BaseCheck, _prune_in_process, _Unit
from mentat_bench.manifest import DEFAULT_PROFILE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers_pa_engine import make_fake_engine  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "toy-local-v0.json"


class _NoRows:
    ids = ["v:x0:1"]

    def rows(self):
        return iter(())


def _group(n, clauses):
    return _Unit(f"g{n:07d}", None, -1, clauses, False, "at_most_one", members=[0])


def test_group_drops_are_capped_by_count_and_by_rate():
    """Two groups that contradict each other: each repair is a group drop. With a count cap of 1 the second is refused;
    with a rate cap of 25% of the 2 committed groups (50%) it is refused with the rate."""
    with pytest.raises(BaseInconsistentError, match="over --max-group-drops 0"):
        _prune_in_process(_BaseCheck(_NoRows(), [_group(0, [(1, 1)]), _group(1, [(0, 0)])], 1), 10, 0, lambda: 1e9,
                          method="scc", groups_asked=2)
    with pytest.raises(BaseInconsistentError, match="over the limit of 25% \\(--max-group-drop-frac\\)"):
        _prune_in_process(_BaseCheck(_NoRows(), [_group(0, [(1, 1)]), _group(1, [(0, 0)])], 1), 10, 10, lambda: 1e9,
                          method="scc", group_frac=0.25, groups_asked=2)
    dropped = _prune_in_process(_BaseCheck(_NoRows(), [_group(0, [(1, 1)]), _group(1, [(0, 0)])], 1), 10, 10, lambda: 1e9,
                                method="scc", group_frac=0.5, groups_asked=2)
    assert len(dropped) == 1  # within both caps: one group repaired


def test_the_build_and_cli_default_for_group_drops_is_20000_rows():
    # raised with admission (group drops are counted per row; toy10k-0.5B drops 6,881 of 9,322 rows by admission)
    from mentat_bench import cli

    assert fixed.MAX_GROUP_DROPS_DEFAULT == 20000 and fixed.MAX_GROUP_DROP_FRAC == 0.9
    args = cli.build_parser().parse_args(["build", "--adapter", "empty", "--corpus", "x", "--out", "y"])
    assert args.max_group_drops == 20000 and args.max_group_drop_frac == 0.9


def test_a_repaired_cell_shows_its_strict_score_as_zero_and_a_clean_cell_its_own(tmp_path):
    def cell(name, base_repaired):
        r = tmp_path / name
        r.mkdir()
        (r / "score.json").write_text(json.dumps({"Q_N": 60.0, "families": {}}), encoding="utf-8")
        (r / "build_summary.json").write_text(json.dumps({"base_repaired": base_repaired, "groups_dropped_pct": 3.5}),
                                              encoding="utf-8")
        return report.run_row(r)

    repaired, clean = cell("repaired", True), cell("clean", False)
    assert repaired["Q_N"] == 60.0 and repaired["strict_Q_N"] == 0.0 and repaired["base_repaired"] is True
    assert clean["strict_Q_N"] == 60.0 and clean["base_repaired"] is False
    text = report.markdown([repaired, clean])
    assert "Q_N (repaired, diagnostic)" in text and "strict Q_N (diagnostic)" in text and "strict Q_N is 0.0 for a repaired base" in text
    assert "Headline: the relation MCC for fixture versions 1 and 2" in text and "never a ranking" in text


def _corpus_records(fixture):
    return [{"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r.get("event_title")}
            for r in (json.loads(line) for line in (fixture / "corpus.jsonl").read_text(encoding="utf-8").splitlines())]


def _fixture(tmp_path, n=4):
    fixture = tmp_path / "fixture"
    (fixture / "gold").mkdir(parents=True)
    (fixture / "corpus.jsonl").write_text("".join(
        json.dumps({"id": f"v:m{i}:1", "text": f"Will X{i} win the cup?", "label": "Yes", "event_title": "E"}) + "\n"
        for i in range(1, n + 1)), encoding="utf-8")
    (fixture / "queries.jsonl").write_text(json.dumps({"family": "closure", "qid": "c1", "hyp": []}) + "\n", encoding="utf-8")
    return fixture


def _kept_checkpoint(tmp_path, monkeypatch, top_k):
    """A build with the given top_k, stopped before finalize: its checkpoint is kept in <out>.build (the work dir)."""
    fixture = _fixture(tmp_path)
    out = tmp_path / "run" / "submission"
    real_finalize = fixed.finalize_checkpoint

    class _Stop(Exception):
        pass

    def stop(*args, **kwargs):
        raise _Stop()

    monkeypatch.setattr(fixed, "finalize_checkpoint", stop)
    with pytest.raises(_Stop):
        fixed.build(_corpus_records(fixture), EmptyAdapter(), out, dict(DEFAULT_PROFILE), top_k=top_k)
    monkeypatch.setattr(fixed, "finalize_checkpoint", real_finalize)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    return fixture, out


def _build_argv(fixture, out, *extra):
    return ["build", "--adapter", "empty", "--corpus", str(fixture / "corpus.jsonl"), "--out", str(out),
            "--profile", str(PROFILE), *extra]


def test_a_fresh_build_takes_the_cli_default_top_k(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "submission"
    assert cli.main(_build_argv(fixture, out)) == 0
    assert json.loads((out / "build_policy.json").read_text(encoding="utf-8"))["top_k"] == cli.BUILD_TOP_K


def test_resume_without_top_k_adopts_the_checkpoints_top_k(tmp_path, monkeypatch):
    fixture, out = _kept_checkpoint(tmp_path, monkeypatch, top_k=128)
    assert cli.main(_build_argv(fixture, out, "--resume")) == 0  # the default (64) is not imposed on the checkpoint
    assert json.loads((out / "build_policy.json").read_text(encoding="utf-8"))["top_k"] == 128


def test_resume_with_a_different_top_k_names_the_flag_and_the_checkpoint_value(tmp_path, monkeypatch, capsys):
    fixture, out = _kept_checkpoint(tmp_path, monkeypatch, top_k=128)
    assert cli.main(_build_argv(fixture, out, "--resume", "--top-k", str(cli.BUILD_TOP_K))) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert f"--top-k {cli.BUILD_TOP_K} differs from the top_k 128" in err
    assert "resume with --top-k 128" in err
    assert not (out / "build_policy.json").exists()  # refused before anything was written
    assert cli.main(_build_argv(fixture, out, "--resume", "--top-k", "128")) == 0  # the explicit checkpoint value


def test_runcell_resume_on_a_kept_dir_goes_straight_to_finalize_and_evaluate(tmp_path, monkeypatch):
    """A build stopped after its decision phases (before finalize) leaves a kept work dir; runcell --resume finishes it:
    no decision is asked again, the base is finalized, sealed and evaluated."""
    fixture = _fixture(tmp_path)
    (fixture / "gold" / "answers.jsonl").write_text("", encoding="utf-8")  # a required file, so it can be fingerprinted
    run = tmp_path / "run"
    real_finalize = fixed.finalize_checkpoint

    class _Stop(Exception):
        pass

    def stop(*args, **kwargs):
        raise _Stop()

    monkeypatch.setattr(fixed, "finalize_checkpoint", stop)
    with pytest.raises(_Stop):  # top_k as the CLI builds it: runcell --resume refuses a different top_k
        fixed.build(_corpus_records(fixture), EmptyAdapter(), run / "submission", dict(DEFAULT_PROFILE), top_k=cli.BUILD_TOP_K)
    monkeypatch.setattr(fixed, "finalize_checkpoint", real_finalize)
    assert (run / "submission.build" / "checkpoint.json").exists()

    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    runcell.run_cell(fixture, "empty", [], run, PROFILE, [], resume=True)
    cell = json.loads((run / "cell.json").read_text(encoding="utf-8"))
    assert cell["steps"]["build"] == 0 and cell["steps"]["seal"] == 0 and cell["steps"]["evaluate"] == 0
    assert cell["resumed"] is True and cell["base_repaired"] is False
    summary = json.loads((run / "build_summary.json").read_text(encoding="utf-8"))
    assert summary["resumed"] is True and summary["prior_calls"] > 0  # the stopped build had made its usage entries
    # runcell builds with --keep-work: the work dir stays (checkpoints, logs and pairs.bin)
    assert (run / "submission" / "seal.json").exists() and (run / "submission.build" / "checkpoint.json").exists()
    assert (run / "submission.build" / "pairs.bin").is_file()


def test_a_long_progressing_build_writes_no_stall_trace(tmp_path, monkeypatch):
    """The watch fires only after STALL_TRACE_EVERY seconds without a heartbeat: a build that keeps deciding leaves an
    empty stall_trace.txt, however long it runs."""

    class _Steady:
        name = "steady"

        def decide_binding(self, ctx):
            time.sleep(0.03)  # progress every ~0.03 s, longer in total than the watch interval below
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["x"]}, usage(self.name)

        def decide_relation(self, a, b):
            return "NONE", usage(self.name)

    class _Stop(Exception):
        pass

    def stop(*args, **kwargs):
        raise _Stop()

    monkeypatch.setattr(fixed, "STALL_TRACE_EVERY", 0.2)
    monkeypatch.setattr(fixed, "finalize_checkpoint", stop)
    recs = [{"id": f"v:m{i}:1", "question": f"Will X{i} win?", "label": "Yes", "event_title": "E"} for i in range(1, 25)]
    with pytest.raises(_Stop):
        fixed.build(recs, _Steady(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert (tmp_path / "sub.build" / "stall_trace.txt").read_text(encoding="utf-8") == ""


def test_a_stall_after_progress_is_traced_again(tmp_path, monkeypatch):
    """Progress first, then a silence longer than the interval: the trace appears (the heartbeat is what resets it)."""

    class _Pause:
        name = "pause"
        calls = 0

        def decide_binding(self, ctx):
            _Pause.calls += 1
            time.sleep(0.5 if _Pause.calls == 3 else 0.01)  # one long silence after progress
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["x"]}, usage(self.name)

        def decide_relation(self, a, b):
            return "NONE", usage(self.name)

    class _Stop(Exception):
        pass

    def stop(*args, **kwargs):
        raise _Stop()

    monkeypatch.setattr(fixed, "STALL_TRACE_EVERY", 0.2)
    monkeypatch.setattr(fixed, "finalize_checkpoint", stop)
    recs = [{"id": f"v:m{i}:1", "question": f"Will X{i} win?", "label": "Yes", "event_title": "E"} for i in range(1, 8)]
    with pytest.raises(_Stop):
        fixed.build(recs, _Pause(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert (tmp_path / "sub.build" / "stall_trace.txt").read_text(encoding="utf-8").strip() != ""

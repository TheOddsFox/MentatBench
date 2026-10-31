"""Round 5: a drop-cap refusal keeps the model's decisions (the work dir survives) and prints the resume command with higher
caps, which then succeeds; runcell forwards every drop cap and --resume to the build; report.py derives the repaired state
(and so the strict Q_N) from the summary, its drop counts, or cell.json."""

import json
import sys
from pathlib import Path

from mentat_bench import cli, engine, report, runcell

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pb_harness import Scripted  # noqa: E402
from test_pb_round2 import _two_groups  # noqa: E402


def test_a_drop_cap_refusal_keeps_the_decisions_and_resumes_with_raised_caps(tmp_path, monkeypatch, capsys):
    recs, bindings, relations = _two_groups()  # two contradictory groups: two relation drops are needed
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(json.dumps({"id": r["id"], "text": r["question"], "label": r["label"],
                                          "event_title": r["event_title"]}) + "\n" for r in recs), encoding="utf-8")
    monkeypatch.setattr(engine, "engine_available", lambda: False)
    monkeypatch.setattr(cli, "_make_adapter", lambda args: Scripted(bindings, relations))
    out, work = tmp_path / "out", tmp_path / "out.build"
    base = ["build", "--adapter", "local", "--local-arg", "unused", "--corpus", str(corpus), "--out", str(out),
            "--max-relation-drop-frac", "1.0"]

    assert cli.main([*base, "--max-relation-drops", "1"]) == cli.EXIT_ENGINE  # the cap refuses the base
    assert (work / "checkpoint.json").is_file() and not out.exists()  # the decisions survive; nothing is written
    err = capsys.readouterr().err
    assert "over --max-relation-drops 1" in err
    resume_lines = [line.strip() for line in err.splitlines() if "--resume" in line]
    assert resume_lines and "--max-relation-drops 2" in resume_lines[-1] and "--max-group-drops 4000" in resume_lines[-1]

    assert cli.main([*base, "--max-relation-drops", "2", "--resume"]) == 0  # the resume with raised caps succeeds
    assert (out / "theory.json").is_file() and not work.exists()


def test_a_non_cap_failure_after_a_checkpoint_keeps_the_work_dir_too(tmp_path, monkeypatch, capsys):
    """Any failure with a checkpoint keeps it (the decisions cost money): here a RuntimeError from the build."""
    monkeypatch.setattr(cli, "_make_adapter", lambda args: Scripted({}, {}))
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"id": "v:m1:1", "text": "Will X win?", "label": "Yes", "event_title": "E"}) + "\n",
                      encoding="utf-8")
    work = tmp_path / "out.build"

    def failing(*args, **kwargs):  # a checkpoint is written, then the build fails
        work.mkdir()
        (work / "checkpoint.json").write_text("{}", encoding="utf-8")
        raise RuntimeError("boom after the checkpoint")

    monkeypatch.setattr(cli.fixed, "build", failing)
    assert cli.main(["build", "--adapter", "local", "--local-arg", "x", "--corpus", str(corpus),
                     "--out", str(tmp_path / "out")]) == cli.EXIT_BUILD_INCOMPLETE
    assert (work / "checkpoint.json").is_file()


def _fixture(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "gold").mkdir(parents=True)
    (fixture / "corpus.jsonl").write_text(json.dumps(
        {"id": "v:m1:1", "text": "Will X win?", "label": "Yes", "event_title": "E"}) + "\n", encoding="utf-8")
    (fixture / "queries.jsonl").write_text("", encoding="utf-8")
    return fixture


def test_runcell_forwards_every_drop_cap_and_resume_to_the_build(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    (fixture / "gold" / "answers.jsonl").write_text("", encoding="utf-8")  # a required file, so it can be fingerprinted
    out = tmp_path / "run"
    (out / "submission.build").mkdir(parents=True)
    (out / "submission.build" / "checkpoint.json").write_text("{}", encoding="utf-8")  # a kept build
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return 1, ""  # stop after the build's argv is known

    monkeypatch.setattr(runcell, "_captured", fake)
    runcell.main(["--fixture", str(fixture), "--adapter", "empty", "--out", str(out),
                  "--max-relation-drops", "7", "--max-relation-drop-frac", "0.3",
                  "--max-group-drops", "9", "--max-group-drop-frac", "0.4", "--resume"])
    argv = seen["argv"]
    assert argv[0] == "build"
    for flag, value in (("--max-relation-drops", "7"), ("--max-relation-drop-frac", "0.3"),
                        ("--max-group-drops", "9"), ("--max-group-drop-frac", "0.4")):
        assert argv[argv.index(flag) + 1] == value, flag
    assert "--resume" in argv


def _cell(tmp_path, name, summary=None, cell=None):
    run = tmp_path / name
    run.mkdir()
    (run / "score.json").write_text(json.dumps({"Q_N": 40.0, "families": {}}), encoding="utf-8")
    if summary is not None:
        (run / "build_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    if cell is not None:
        (run / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    return report.run_row(run)


def test_strict_q_n_is_zero_for_every_recorded_repair(tmp_path):
    by_cell = _cell(tmp_path, "by_cell", summary={"relations_dropped": 3}, cell={"base_repaired": True})
    by_counts = _cell(tmp_path, "by_counts", summary={"relations_dropped": 3, "groups_dropped": 0})
    by_flag = _cell(tmp_path, "by_flag", summary={"base_repaired": True})
    for row in (by_cell, by_counts, by_flag):
        assert row["strict_Q_N"] == 0.0 and row["base_repaired"] is True
        assert row["Q_N"] == 40.0  # the repaired score is still shown
    clean = _cell(tmp_path, "clean", summary={"relations_dropped": 0, "base_repaired": False})
    assert clean["strict_Q_N"] == 40.0 and clean["base_repaired"] is False
    unknown = _cell(tmp_path, "unknown", summary={"pairs_capped": True})
    assert unknown["strict_Q_N"] is None and unknown["base_repaired"] is None

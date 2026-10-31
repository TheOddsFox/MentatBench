"""runcell: the dash-value hint, a failed build's cell (status incomplete, no summary, still reported), evaluate's stdout
stored in cell.json with its partial marker, the per-reason hint of a refused hello, a failed step (seal, evaluate, score)
making the cell incomplete and naming it, and the refusal of an unreadable or malformed registry before any build."""

import json
import sys
from pathlib import Path

import pytest

from mentat_bench import ceilings, cli, report, runcell
from mentat_bench.harness import fixed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers_pa_engine import make_fake_engine  # noqa: E402
from test_hello_health import CHILD  # noqa: E402

PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "toy-local-v0.json"


def _fixture(tmp_path, n=4):
    fixture = tmp_path / "fixture"
    (fixture / "gold").mkdir(parents=True)
    (fixture / "corpus.jsonl").write_text("".join(
        json.dumps({"id": f"v:m{i}:1", "text": f"Will X{i} win the cup?", "label": "Yes", "event_title": "E"}) + "\n"
        for i in range(1, n + 1)), encoding="utf-8")
    (fixture / "queries.jsonl").write_text(json.dumps({"family": "closure", "qid": "c1", "hyp": []}) + "\n", encoding="utf-8")
    return fixture


def test_a_dash_leading_local_arg_gets_the_equals_hint(tmp_path, capsys):
    code = runcell.main(["--fixture", str(_fixture(tmp_path)), "--adapter", "local", "--local-arg", "-m",
                         "--out", str(tmp_path / "cell")])
    assert code == runcell.EXIT_USAGE
    assert "--local-arg=-m" in capsys.readouterr().err


def test_a_failed_build_is_an_incomplete_cell_that_the_report_shows(tmp_path, capsys):
    script = tmp_path / "hello_child.py"
    script.write_text(CHILD, encoding="utf-8")
    out = tmp_path / "cell"
    fixture = _fixture(tmp_path)
    (fixture / "gold" / "answers.jsonl").write_text("", encoding="utf-8")  # a required file, so the cell can be fingerprinted
    code = runcell.run_cell(fixture, "local", [sys.executable, str(script), "slowhello"], out, PROFILE,
                            ["--local-protocol", "2", "--hello-timeout", "0.5"])
    assert code == cli.EXIT_BUILD_INCOMPLETE
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["status"] == "incomplete" and cell["build_exit"] == cli.EXIT_BUILD_INCOMPLETE
    assert cell["steps"] == {"build": cli.EXIT_BUILD_INCOMPLETE} and not (out / "build_summary.json").exists()
    assert cell["failed_step"] == "build" and cell["incomplete"] is True
    row = report.run_row(out)  # no score.json, no build summary: the row still appears, with status incomplete
    assert row["status"] == "incomplete" and row["Q_N"] is None


def _with_gold(fixture, extra=True):
    """Gold files for the score step; with extra, a second query file and its gold answers (one closure query each)."""
    gold = fixture / "gold"
    closure = {"ENTAILED": [], "REFUTED": [], "CONTINGENT": [], "UNRESOLVED": []}
    (gold / "answers.jsonl").write_text(json.dumps({"qid": "c1", "labels": closure}) + "\n", encoding="utf-8")
    (gold / "bindings.jsonl").write_text("", encoding="utf-8")
    (gold / "query_design.jsonl").write_text("", encoding="utf-8")
    if extra:
        (fixture / "queries_extra.jsonl").write_text(json.dumps({"family": "closure", "qid": "x1", "hyp": []}) + "\n", encoding="utf-8")
        (gold / "answers_extra.jsonl").write_text(json.dumps({"qid": "x1", "labels": closure}) + "\n", encoding="utf-8")
    return fixture


def test_extra_queries_are_evaluated_and_scored_into_score_extra(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture(tmp_path))
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, [], extra_queries=True) == 0
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["steps"]["evaluate_extra"] == 0 and cell["steps"]["score_extra"] == 0 and cell["extra_status"] == "ok"
    assert set(cell["step_seconds"]) == set(cell["steps"]) and all(isinstance(v, float) for v in cell["step_seconds"].values())
    assert cell["extra_queries"]["query_file"] == "queries_extra.jsonl" and cell["extra_queries"]["evaluate"]["digest"]
    assert cell["query_files"] == ["queries.jsonl", "queries_extra.jsonl"]  # rescore re-scores every file listed here
    assert (out / "extra" / "answers.jsonl").is_file() and (out / "extra" / "verdicts.jsonl").is_file()
    extra = json.loads((out / "score_extra.json").read_text(encoding="utf-8"))
    assert extra["queries"] == 1 and extra["fixture_stamp"] == cell["fixture_stamp"]
    assert json.loads((out / "score.json").read_text(encoding="utf-8"))["queries"] == 1


def test_a_failed_extra_pass_is_visible_in_the_cell_the_report_and_the_exit_code(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture(tmp_path))
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    real_main = cli.main

    def evaluate_fails_on_the_extra_file(argv):  # the extra evaluate step fails; the main steps run for real
        if argv and argv[0] == "evaluate" and any(str(a).endswith("queries_extra.jsonl") for a in argv):
            return cli.EXIT_INFRA
        return real_main(argv)

    monkeypatch.setattr(runcell.cli, "main", evaluate_fails_on_the_extra_file)
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, [], extra_queries=True) == cli.EXIT_INFRA
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["extra_status"] == "failed" and cell["steps"]["evaluate_extra"] == cli.EXIT_INFRA
    assert cell["query_files"] == ["queries.jsonl", "queries_extra.jsonl"]  # the pass started, so the file was used
    assert "score_extra" not in cell["steps"] and not (out / "score_extra.json").exists()
    assert cell["failed_step"] == "evaluate_extra" and cell["status"] == "incomplete" and cell["incomplete"] is True
    assert (out / "score.json").exists()  # the main score still stands
    assert report.run_row(out)["extra_Q_N"] == "failed"


def test_a_main_score_failure_leaves_the_extra_pass_not_run(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture(tmp_path))
    (fixture / "gold" / "answers.jsonl").write_text("{not json\n", encoding="utf-8")  # the main score step cannot read it
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, [], extra_queries=True) == cli.EXIT_USAGE
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["steps"].get("score") not in (None, 0) and cell["extra_status"] == "not_run"
    assert cell["status"] == "incomplete" and cell["failed_step"] == "score" and cell["incomplete"] is True
    assert "evaluate_extra" not in cell["steps"]
    assert cell["query_files"] == ["queries.jsonl"]  # the extra file was never used by this cell
    assert report.run_row(out)["extra_Q_N"] == "not_run"


def test_the_cell_records_the_resolved_top_k_and_batch_size(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    runcell.run_cell(fixture, "empty", [], out, PROFILE, [])
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    policy = json.loads((out / "submission" / "build_policy.json").read_text(encoding="utf-8"))
    assert cell["top_k"] == policy["top_k"] == cli.BUILD_TOP_K
    assert cell["batch_size"] == policy["batch_size"]
    assert report.run_row(out)["top_k"] == cli.BUILD_TOP_K


def test_without_the_flag_a_cell_has_no_extra_pass(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture(tmp_path))
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, []) == 0
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["extra_queries"] is None and "evaluate_extra" not in cell["steps"]
    assert cell["query_files"] == ["queries.jsonl"]
    assert not (out / "score_extra.json").exists() and not (out / "extra").exists()


def test_a_cell_keeps_its_work_dir_and_ceilings_derive_from_it(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, []) == 0
    work = out / "submission.build"  # kept by the build's --keep-work
    assert (work / fixed.HISTORY_FILE).is_file() and (work / "checkpoint.json").is_file()
    assert (work / "pairs.bin").is_file()  # kept: a resume of a partial build and the hf child read it
    cells = tmp_path / "ceilings"
    assert ceilings.run(work, "wall_seconds", ["1000000"], fixture, PROFILE, cells) == 0
    cell = json.loads((cells / "wall_seconds_1000000" / "cell.json").read_text(encoding="utf-8"))
    assert cell["status"] == "ceiling_reached_end" and cell["steps"] == {"derive": 0, "seal": 0, "evaluate": 0, "score": 0}
    assert (cells / "wall_seconds_1000000" / "score.json").is_file()


def _stamp_by_content(monkeypatch):
    """A fake registry: the stamp of a fixture is a hash of its corpus, so two fixtures with different corpora differ."""
    import hashlib

    def stamp(fixture, registry=None, hashes=None):
        return {"name": "fake", "version": "1", "status": "active",
                "content_hash": hashlib.sha256((fixture / "corpus.jsonl").read_bytes()).hexdigest()}

    monkeypatch.setattr(runcell.corrections, "stamp_for", stamp)


def test_resume_refuses_a_fixture_other_than_the_one_the_interrupted_cell_started_on(tmp_path, monkeypatch, capsys):
    import shutil
    _stamp_by_content(monkeypatch)
    started = _with_gold(_fixture(tmp_path), extra=False)
    other = tmp_path / "other"
    shutil.copytree(started, other)
    (other / "corpus.jsonl").write_text((other / "corpus.jsonl").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    real_finalize = fixed.finalize_checkpoint
    monkeypatch.setattr(fixed, "finalize_checkpoint", _raise_stop)  # the build stops after its decisions
    assert runcell.run_cell(started, "empty", [], out, PROFILE, []) == cli.EXIT_BUILD_INCOMPLETE
    assert (out / "fixture_stamp.json").is_file() and (out / "submission.build" / fixed.HISTORY_FILE).is_file()
    cell_before = (out / "cell.json").read_bytes()
    monkeypatch.setattr(fixed, "finalize_checkpoint", real_finalize)
    assert runcell.run_cell(other, "empty", [], out, PROFILE, [], resume=True) == cli.EXIT_USAGE
    assert "--resume refused" in capsys.readouterr().err
    assert (out / "cell.json").read_bytes() == cell_before  # nothing was written
    assert runcell.run_cell(started, "empty", [], out, PROFILE, [], resume=True) == 0  # the same fixture resumes


def test_resume_checks_the_fingerprint_of_an_unregistered_fixture(tmp_path, monkeypatch, capsys):
    """Neither fixture is registered: the recorded content hash still tells them apart, and a stamp with none is refused."""
    import shutil
    started = _with_gold(_fixture(tmp_path), extra=False)
    other = tmp_path / "other"
    shutil.copytree(started, other)
    (other / "corpus.jsonl").write_text((other / "corpus.jsonl").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    real_finalize = fixed.finalize_checkpoint
    monkeypatch.setattr(fixed, "finalize_checkpoint", _raise_stop)
    assert runcell.run_cell(started, "empty", [], out, PROFILE, []) == cli.EXIT_BUILD_INCOMPLETE
    recorded = (out / "fixture_stamp.json").read_bytes()
    assert json.loads(recorded)["content_hash"]  # an unregistered fixture is fingerprinted too
    monkeypatch.setattr(fixed, "finalize_checkpoint", real_finalize)
    assert runcell.run_cell(other, "empty", [], out, PROFILE, [], resume=True) == cli.EXIT_USAGE
    assert "--resume refused" in capsys.readouterr().err
    (out / "fixture_stamp.json").write_text(json.dumps({"error": "no fingerprint"}), encoding="utf-8")
    assert runcell.run_cell(started, "empty", [], out, PROFILE, [], resume=True) == cli.EXIT_USAGE  # either side lacking one
    assert "--resume refused" in capsys.readouterr().err
    (out / "fixture_stamp.json").write_bytes(recorded)
    assert runcell.run_cell(started, "empty", [], out, PROFILE, [], resume=True) == 0  # the same fixture resumes


def test_a_cell_hashes_each_fixture_file_once_and_the_run_card_reuses_the_hashes(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    hashed, listed = [], []
    real_sha, real_files = runcell.corrections._sha256, runcell.runcard.fixture_files
    monkeypatch.setattr(runcell.corrections, "_sha256", lambda path: hashed.append(Path(path)) or real_sha(path))
    monkeypatch.setattr(runcell.runcard, "fixture_files", lambda root: listed.append(root) or real_files(root))
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    monkeypatch.setattr(fixed, "finalize_checkpoint", _raise_stop)  # the stamp is written before the build stops
    out = tmp_path / "cell"
    runcell.run_cell(fixture, "empty", [], out, PROFILE, [])
    assert sorted(hashed) == sorted(p for p in fixture.rglob("*") if p.is_file())  # one pass per file
    assert listed == []  # the run card takes the hashes of the cell, it does not hash the fixture again
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["runtime"]["fixture"]["files"] == runcell.corrections.content_hashes(fixture)


def test_a_fixture_missing_a_required_file_is_refused_before_any_build(tmp_path, monkeypatch, capsys):
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    (fixture / "gold" / "answers.jsonl").unlink()  # a required file: no fingerprint, so no cell

    def no_build(*args, **kwargs):
        raise AssertionError("a build started")

    monkeypatch.setattr(runcell.cli, "main", no_build)
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, []) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "needs corpus.jsonl, queries.jsonl, gold/answers.jsonl (missing: gold/answers.jsonl)" in err and not out.exists()


@pytest.mark.parametrize("registry_text", [None, "{not json", json.dumps({"entries": [{"name": "x"}]})],
                         ids=["missing", "unparseable", "malformed"])
def test_runcell_refuses_an_unreadable_or_malformed_registry_before_any_build(tmp_path, monkeypatch, capsys, registry_text):
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    registry = tmp_path / "VERSIONS.json"
    if registry_text is not None:
        registry.write_text(registry_text, encoding="utf-8")
    monkeypatch.setattr(runcell.corrections, "REGISTRY", registry)

    def no_build(*args, **kwargs):
        raise AssertionError("a build started")

    monkeypatch.setattr(runcell.cli, "main", no_build)
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, []) == cli.EXIT_USAGE
    assert "the registry cannot be used" in capsys.readouterr().err and not out.exists()


@pytest.mark.parametrize("step", ["seal", "evaluate", "score"])
def test_a_failed_step_makes_the_cell_incomplete_and_names_it(tmp_path, monkeypatch, step):
    """The build succeeded, so its status is complete; the failing step replaces it and is named in failed_step."""
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    if step == "seal":
        def seal_fails(submission):
            raise RuntimeError("seal boom (test)")

        monkeypatch.setattr(runcell, "seal", seal_fails)
    elif step == "evaluate":
        real_main = cli.main

        def evaluate_fails(argv):  # evaluate exits with an infrastructure error; the other commands run for real
            return cli.EXIT_INFRA if argv and argv[0] == "evaluate" else real_main(argv)

        monkeypatch.setattr(runcell.cli, "main", evaluate_fails)
    else:
        (fixture / "gold" / "answers.jsonl").write_text("{not json\n", encoding="utf-8")  # the score step cannot read it
    out = tmp_path / "cell"
    code = runcell.run_cell(fixture, "empty", [], out, PROFILE, [])
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["steps"]["build"] == 0 and code != 0 and code == cell["steps"][step]
    assert cell["failed_step"] == step and cell["status"] == "incomplete" and cell["incomplete"] is True


def _raise_stop(*args, **kwargs):
    raise RuntimeError("stopped before finalize (test)")


def test_the_refusal_names_the_state_of_the_run_directory(tmp_path, capsys):
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    out = tmp_path / "unstarted"
    out.mkdir()
    (out / "note.txt").write_text("x", encoding="utf-8")
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, [], resume=True) == cli.EXIT_USAGE
    assert "nothing to resume" in capsys.readouterr().err
    published = tmp_path / "published"
    (published / "submission").mkdir(parents=True)
    assert runcell.run_cell(fixture, "empty", [], published, PROFILE, [], resume=True) == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "published but not finished" in err and "not sealed" in err and "seal --submission" in err
    finished = tmp_path / "finished"
    (finished / "submission").mkdir(parents=True)
    (finished / "cell.json").write_text("{}", encoding="utf-8")
    assert runcell.run_cell(fixture, "empty", [], finished, PROFILE, []) == cli.EXIT_USAGE
    assert "is a finished cell" in capsys.readouterr().err


def test_a_finished_cell_is_not_resumed_and_its_cell_json_is_kept(tmp_path, monkeypatch):
    """The kept work dir of a finished cell has a checkpoint, but its submission/ exists: --resume refuses before anything
    is written, so the cell's cell.json is not overwritten."""
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, []) == 0
    before = (out / "cell.json").read_bytes()
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, [], resume=True) == cli.EXIT_USAGE
    assert (out / "cell.json").read_bytes() == before


def test_extra_queries_need_the_extra_files_before_anything_is_built(tmp_path, capsys):
    fixture = _with_gold(_fixture(tmp_path), extra=False)
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, [], extra_queries=True) == runcell.EXIT_USAGE
    assert not out.exists() and "--extra-queries needs queries_extra.jsonl" in capsys.readouterr().err


def test_evaluate_stdout_is_stored_in_the_cell_with_its_partial_marker(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path)
    (fixture / "gold" / "answers.jsonl").write_text("", encoding="utf-8")  # a required file, so the cell is not refused
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "cell"
    code = runcell.run_cell(fixture, "empty", [], out, PROFILE, ["--max-pairs", "2"])
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["steps"]["evaluate"] == 0
    assert cell["evaluate"]["partial"] == ["pairs_capped"]  # evaluate's own stdout, kept in the cell
    assert cell["partial"] == ["pairs_capped"]
    # The score step passes gold/bindings.jsonl and gold/query_design.jsonl, which this fixture lacks: score refuses (usage)
    assert cell["steps"]["score"] == cli.EXIT_USAGE and code == cli.EXIT_USAGE
    assert cell["failed_step"] == "score" and cell["status"] == "incomplete" and cell["incomplete"] is True

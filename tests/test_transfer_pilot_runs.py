"""The transfer pilot's model runs and their mapping (docs/audits/v0.2.0-transfer-pilot/run_models.py and predictions.py), on synthetic data:
no network, no model, no question or outcome text, and no engine binary or local fixture. The cells, the pins, the engine, the fixture check
and the subprocess are made up (the synthetic_runs fixture); the candidates and decisions use made-up ids, and the fixture's files are
empty. Every output goes to a temporary folder: nothing is written into the repository or into mb-runs.

The two places that read the repository are the list of the 19 runs (the leaderboard's ids and the two baselines) and the 19 real cell
records, read to compare argv_for with runcell's own build argv (skipped when a cell file is absent). Neither holds text.

Covered: the build argv equals the one runcell builds, for synthetic cells and for each real cell; the floor carried over; the environment
with no inherited MB_S1_ setting and no MB_S1_ALLOW_UNPINNED; the local paths named in a record and in the plan; the run guards (repository
path, a done run without --retry, a non-empty folder, a model run without a URL, unknown and repeated ids, the local fixture's hashes); a
run's folder, its records and their provenance, the engine sha256 and its comparison with the cell, the build status, a failed log read, and
collect; the decision log's counts; duplicate and torn rows; the mapping of each decision shape; write's refusals (a failed or missing run, a
status other than complete, a torn log, a changed decisions file, a changed fixture or selection) and its outputs; and the exit codes of the
two commands."""

import copy
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "audits" / "v0.2.0-transfer-pilot"
# Other audits ship modules with the same names: import ours fresh and leave neither the modules nor the folder behind.
_SHARED = ("score", "run_slice", "build_pool", "rehydrate", "fetch", "analyse", "run_models", "predictions")
for _name in _SHARED:
    sys.modules.pop(_name, None)
sys.path.insert(0, str(AUDIT))
import analyse as AN  # noqa: E402
import predictions as PR  # noqa: E402
import run_models as RM  # noqa: E402

sys.path[:] = [p for p in sys.path if not p or Path(p).resolve() != AUDIT]
for _name in _SHARED:
    sys.modules.pop(_name, None)

from mentat_bench import decision_scoring as DS  # noqa: E402
from mentat_bench import runcell  # noqa: E402

PILOT_FLOOR = "4.498940449603714"
FLAGS = ["--local-protocol", "2", "--batch-size", "128", "--top-k", "24", "--local-timeout", "600.0", "--hello-timeout", "900.0",
         "--relation-floor", PILOT_FLOOR, "--candidates", "fixtures/mentat_hard_test/candidates.jsonl"]
SYSTEM_ONE = ["python.exe", "-m", "mentat_bench.children.systemone_child"]
LEXICAL = ["python.exe", "-m", "mentat_bench.children.lexical_child"]
DIGEST = "d" * 64  # the made-up pin of laya:latest, and the revision of its made-up cell
ENGINE_BYTES = b"made-up engine\n"
ENGINE_SHA = hashlib.sha256(ENGINE_BYTES).hexdigest()


def model_doc(run_id="laya_latest", tag="laya:latest", flags=FLAGS, revision=DIGEST):
    """A made-up model cell, shaped like results/v0.2.0/cells/<id>.json."""
    return {"id": run_id, "kind": "system_one_model", "model": {"revision": revision, "id": tag},
            "child_stats": {"model": tag},
            "cell": {"adapter": "local", "fixture_path": "fixtures/mentat_hard_test", "local_arg": list(SYSTEM_ONE),
                     "profile": "profiles/mentat-hard-v1.json", "build_flags": list(flags)}}


def baseline_doc(flags=("--local-protocol", "2", "--top-k", "24", "--no-groups", "--candidates", "fixtures/mentat_hard_test/candidates.jsonl")):
    """A made-up baseline cell, shaped like results/v0.1.0/cells/lexical.json (its groups are off)."""
    return {"id": "lexical", "kind": "baseline", "model": None, "child_stats": None,
            "cell": {"adapter": "local", "fixture_path": "fixtures/mentat_hard_test", "local_arg": list(LEXICAL),
                     "profile": "profiles/mentat-hard-v1.json", "build_flags": list(flags)}}


def fake_load_cell(run_id):
    """The made-up cell of a run: the model's for laya_latest, the lexical baseline's (under its own id) for any other."""
    if run_id == "laya_latest":
        return model_doc()
    return {**baseline_doc(), "id": run_id}


def make_fixture(path: Path) -> Path:
    """A fixture folder runcell accepts, with empty files (its build is never run)."""
    (path / "gold").mkdir(parents=True)
    for name in ("corpus.jsonl", "queries.jsonl", "gold/answers.jsonl", "candidates.jsonl"):
        (path / name).write_text("", encoding="utf-8")
    return path


def runcell_build_argv(monkeypatch, doc, fixture: Path, run_dir: Path, candidates: Path) -> list[str]:
    """The build argv runcell issues for this cell with these candidates: the cell's runcell command (tools/reproduce.py's) through
    runcell.main, with cli.main recorded instead of run (it returns 3, so run_cell stops after its build step)."""
    seen = []
    monkeypatch.setattr(runcell.cli, "main", lambda argv: seen.append(list(argv)) or 3)
    cell = copy.deepcopy(doc["cell"])
    cell["fixture_path"] = str(fixture)
    cell["build_flags"] = RM.without_candidates(cell["build_flags"]) + ["--candidates", str(candidates)]
    assert runcell.main(RM.R.runcell_argv(cell, run_dir)[3:]) == 3
    assert len(seen) == 1
    return seen[0]


def _git_only(argv, **kwargs):
    """Answers git with a made-up head and a clean tree, and refuses anything else: a build is faked explicitly by fake_build."""
    if argv[0] == "git":
        return subprocess.CompletedProcess(argv, 0, stdout="abc123\n" if argv[1] == "rev-parse" else "", stderr="")
    raise AssertionError("a run started")


def fake_build(monkeypatch, *, code=0, summary=None, decisions=None):
    """Replaces the build's subprocess: the build writes a made-up summary to its stdout, and (when given) a made-up decision log, then
    exits with code. Git is answered as _git_only answers it. Returns the list of the build commands that ran."""
    calls = []

    def fake_run(argv, **kwargs):
        if argv[0] == "git":
            return _git_only(argv, **kwargs)
        calls.append({"argv": list(argv), "cwd": kwargs["cwd"], "env": kwargs["env"]})
        run_dir = Path(kwargs["stdout"].name).parent
        kwargs["stdout"].write((json.dumps({} if summary is None else summary) + "\n").encode("utf-8"))
        if decisions is not None:
            (run_dir / "submission.build").mkdir(parents=True, exist_ok=True)
            (run_dir / "submission.build" / "decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in decisions), encoding="utf-8")
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr(RM.subprocess, "run", fake_run)
    return calls


@pytest.fixture
def synthetic_runs(tmp_path, monkeypatch):
    """Everything a run touches, made up: an engine binary (so a CI without one passes), the cells, the pins, the local fixture's check,
    the local and published folders, and a subprocess that answers git only. Returns the paths the run functions take."""
    engine = tmp_path / "bin" / ("mb-engine.exe" if os.name == "nt" else "mb-engine")
    engine.parent.mkdir()
    engine.write_bytes(ENGINE_BYTES)
    monkeypatch.setattr(RM, "ENGINE", engine)
    monkeypatch.setattr(RM, "load_cell", fake_load_cell)
    monkeypatch.setattr(RM, "pins", lambda: {"laya:latest": {"digest": DIGEST}})
    monkeypatch.setattr(RM, "verify_fixture", lambda fixture_dir: None)
    monkeypatch.setattr(RM, "LOCAL_RUNS", tmp_path / "local" / "runs")
    monkeypatch.setattr(RM, "LOCAL_FIXTURE", make_fixture(tmp_path / "local" / "fixture"))
    monkeypatch.setattr(RM, "RUNS_DIR", tmp_path / "records")
    monkeypatch.setattr(RM, "RUNS_JSON", tmp_path / "runs.json")
    monkeypatch.setattr(RM.subprocess, "run", _git_only)
    return {"runs_root": RM.LOCAL_RUNS, "fixture_dir": RM.LOCAL_FIXTURE, "records_dir": RM.RUNS_DIR, "runs_json": RM.RUNS_JSON}


# ---- the run list, the argv and the environment


def test_the_19_runs_are_the_17_models_then_the_two_baselines():
    ids = RM.run_ids()
    assert len(ids) == len(set(ids)) == 19
    assert ids[-2:] == ["lexical", "heuristic"]


def test_the_build_argv_is_the_one_runcell_issues_for_a_model_cell(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)  # the profile is named relative to the repository, as tools/reproduce.py runs it
    fixture = make_fixture(tmp_path / "fixture")
    doc, run_dir = model_doc(), tmp_path / "runs" / "laya_latest"
    issued = runcell_build_argv(monkeypatch, doc, fixture, run_dir, fixture / "candidates.jsonl")
    assert issued == RM.argv_for(doc, run_dir, fixture)[3:]


def test_the_build_argv_is_the_one_runcell_issues_for_a_baseline_cell(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    fixture = make_fixture(tmp_path / "fixture")
    doc, run_dir = baseline_doc(), tmp_path / "runs" / "lexical"
    issued = runcell_build_argv(monkeypatch, doc, fixture, run_dir, fixture / "candidates.jsonl")
    assert issued == RM.argv_for(doc, run_dir, fixture)[3:]
    assert "--no-groups" in issued


@pytest.mark.parametrize("run_id", RM.run_ids() if RM.cell_path("laya_latest").is_file() else [])
def test_the_argv_of_each_real_cell_is_the_build_runcell_issues(run_id, tmp_path, monkeypatch):
    path = RM.cell_path(run_id)
    if not path.is_file():
        pytest.skip(f"{path.name} is absent")
    monkeypatch.chdir(ROOT)
    fixture = make_fixture(tmp_path / "fixture")
    doc = json.loads(path.read_text(encoding="utf-8"))
    run_dir = tmp_path / "runs" / run_id
    issued = runcell_build_argv(monkeypatch, doc, fixture, run_dir, fixture / "candidates.jsonl")
    assert issued == RM.argv_for(doc, run_dir, fixture)[3:]


def test_only_the_candidates_fixture_and_out_paths_differ_from_the_cell(tmp_path):
    fixture, run_dir = tmp_path / "fixture", tmp_path / "runs" / "laya_latest"
    argv = RM.argv_for(model_doc(), run_dir, fixture)
    assert argv[argv.index("--candidates") + 1] == str(fixture / "candidates.jsonl")
    assert argv[argv.index("--corpus") + 1] == str(fixture / "corpus.jsonl")
    assert argv[argv.index("--out") + 1] == str(run_dir / "submission")
    assert argv.count("--candidates") == 1  # the cell's own pair is replaced, not kept beside the pilot's
    assert argv[:4] == [sys.executable, "-m", "mentat_bench", "build"]


def test_the_floor_is_carried_over_as_recorded(tmp_path):
    fixture, run_dir = tmp_path / "fixture", tmp_path / "runs" / "x"
    argv = RM.argv_for(model_doc(), run_dir, fixture)
    assert argv[argv.index("--relation-floor") + 1] == PILOT_FLOOR
    assert RM.floor_of(model_doc()) == float(PILOT_FLOOR)
    assert RM.floor_of(baseline_doc()) is None
    assert "--relation-floor" not in RM.argv_for(baseline_doc(), run_dir, fixture)
    big = model_doc(flags=[*FLAGS[:10], "--relation-floor", "1.7976931348623157e+308", *FLAGS[12:]])  # no relation named: unchanged
    big_argv = RM.argv_for(big, run_dir, fixture)
    assert big_argv[big_argv.index("--relation-floor") + 1] == "1.7976931348623157e+308"
    assert RM.floor_of(big) == 1.7976931348623157e308


def test_the_environment_has_no_inherited_mb_s1_setting_and_never_allows_unpinned(tmp_path, monkeypatch):
    monkeypatch.setenv("MB_S1_ALLOW_UNPINNED", "1")
    monkeypatch.setenv("MB_S1_URL", "http://203.0.113.9:1")
    monkeypatch.setenv("MB_S1_MODEL", "other:tag")
    monkeypatch.setenv("MB_S1_PARALLEL", "9")
    monkeypatch.setenv("MB_ENGINE", "C:/elsewhere/mb-engine.exe")
    stats = tmp_path / "child_stats.json"
    env = RM.env_for(model_doc(), "http://127.0.0.1:11435", stats)
    assert {k: v for k, v in env.items() if k.startswith("MB_S1_")} == {
        "MB_S1_MODEL": "laya:latest", "MB_S1_URL": "http://127.0.0.1:11435", "MB_S1_PARALLEL": "2", "MB_S1_STATS": str(stats)}
    assert "MB_S1_ALLOW_UNPINNED" not in env
    assert env["MB_ENGINE"] == str(RM.ENGINE)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(ROOT / "python")
    base = RM.env_for(baseline_doc(), None, stats)
    assert not [k for k in base if k.startswith("MB_S1_")]
    assert base["MB_ENGINE"] == str(RM.ENGINE)


def test_the_engine_binary_is_named_for_the_platform():
    assert RM.ENGINE.name == ("mb-engine.exe" if os.name == "nt" else "mb-engine")


def test_the_published_record_names_its_local_paths(tmp_path):
    fixture, run_dir = tmp_path / "fixture", tmp_path / "runs" / "laya_latest"
    red = RM.redact(RM.argv_for(model_doc(), run_dir, fixture), fixture, run_dir)
    assert red[0] == "<python>" and "--local-arg=<python>" in red
    assert f"<fixture>{os.sep}corpus.jsonl" in red and f"<fixture>{os.sep}candidates.jsonl" in red
    assert f"<run>{os.sep}submission" in red
    assert str(tmp_path) not in " ".join(red)


# ---- the run guards: every refusal comes before the first run, and nothing starts


def test_a_run_is_refused_inside_the_repository_before_anything_starts(tmp_path, synthetic_runs):
    with pytest.raises(AN.AnalyseError, match="inside the repository"):
        RM.run(["lexical"], runs_root=AUDIT / "runs-here", fixture_dir=synthetic_runs["fixture_dir"],
               records_dir=synthetic_runs["records_dir"])


def test_a_model_run_needs_the_url_of_its_ollama_server(synthetic_runs):
    with pytest.raises(AN.AnalyseError, match="needs --url"):
        RM.run(["laya_latest"], url=None, runs_root=synthetic_runs["runs_root"], fixture_dir=synthetic_runs["fixture_dir"],
               records_dir=synthetic_runs["records_dir"])


def test_a_run_folder_that_is_not_empty_is_refused(synthetic_runs):
    (synthetic_runs["runs_root"] / "lexical").mkdir(parents=True)
    (synthetic_runs["runs_root"] / "lexical" / "build.stdout.txt").write_text("x", encoding="utf-8")
    with pytest.raises(AN.AnalyseError, match="not empty"):
        RM.run(["lexical"], runs_root=synthetic_runs["runs_root"], fixture_dir=synthetic_runs["fixture_dir"],
               records_dir=synthetic_runs["records_dir"])


def test_unknown_and_repeated_run_ids_are_refused(synthetic_runs):
    args = {"runs_root": synthetic_runs["runs_root"], "fixture_dir": synthetic_runs["fixture_dir"], "records_dir": synthetic_runs["records_dir"]}
    with pytest.raises(AN.AnalyseError, match="not one of the 19 runs"):
        RM.run(["no-such-run"], **args)
    with pytest.raises(AN.AnalyseError, match="more than once"):
        RM.run(["lexical", "lexical"], **args)


def test_the_local_fixture_must_be_the_published_one(tmp_path):
    with pytest.raises(AN.AnalyseError, match="does not match fixture_manifest.json"):
        RM.verify_fixture(make_fixture(tmp_path / "fixture"))


def test_a_done_run_is_refused_without_retry_and_a_retry_takes_a_new_folder(synthetic_runs):
    args = {"runs_root": synthetic_runs["runs_root"], "fixture_dir": synthetic_runs["fixture_dir"], "records_dir": synthetic_runs["records_dir"]}
    AN.write_json(synthetic_runs["records_dir"] / "lexical.attempt1.json",
                  {"id": "lexical", "attempt": 1, "exit_code": 0, "status": "complete", "folder": "lexical"})
    with pytest.raises(AN.AnalyseError, match="--retry"):
        RM.prepare(["lexical"], None, False, **args)
    plan = RM.prepare(["lexical"], None, True, **args)["plans"][0]
    assert plan["attempt"] == 2 and plan["folder"] == synthetic_runs["runs_root"] / "lexical.attempt2"


def test_a_failed_run_is_run_again_as_the_next_attempt_without_retry(synthetic_runs):
    AN.write_json(synthetic_runs["records_dir"] / "lexical.attempt1.json",
                  {"id": "lexical", "attempt": 1, "exit_code": 1, "status": "incomplete", "folder": "lexical"})
    plan = RM.prepare(["lexical"], None, False, runs_root=synthetic_runs["runs_root"], fixture_dir=synthetic_runs["fixture_dir"],
                      records_dir=synthetic_runs["records_dir"])["plans"][0]
    assert plan["attempt"] == 2


# ---- a run's folder, its records, and what they hold


def test_a_run_writes_its_folder_and_one_record_each(synthetic_runs, monkeypatch, capsys):
    calls = fake_build(monkeypatch)
    records = RM.run(["lexical", "laya_latest"], "http://127.0.0.1:11435", runs_root=synthetic_runs["runs_root"],
                     fixture_dir=synthetic_runs["fixture_dir"], records_dir=synthetic_runs["records_dir"])
    assert f"sha256 {ENGINE_SHA}" in capsys.readouterr().out  # the engine is printed before any run
    assert [c["argv"][3] for c in calls] == ["build", "build"]
    assert Path(calls[0]["cwd"]) == ROOT
    assert calls[0]["env"].keys().isdisjoint({"MB_S1_MODEL", "MB_S1_URL"})
    assert calls[1]["env"]["MB_S1_MODEL"] == "laya:latest" and calls[1]["env"]["MB_S1_URL"] == "http://127.0.0.1:11435"
    assert "MB_S1_ALLOW_UNPINNED" not in calls[1]["env"]
    assert calls[1]["env"]["MB_ENGINE"] == str(RM.ENGINE)
    assert (synthetic_runs["runs_root"] / "laya_latest" / "build.stdout.txt").is_file()
    lexical, laya = records
    assert lexical["id"] == "lexical" and lexical["model"] is None and lexical["floor"] is None and lexical["exit_code"] == 0
    assert laya["model"] == {"tag": "laya:latest", "digest": DIGEST} and laya["floor"] == float(PILOT_FLOOR)
    assert laya["decision_log"] is None and laya["status"] == "complete" and laya["decisions_file"] is None
    assert laya["engine"] == {"name": RM.ENGINE.name, "sha256": ENGINE_SHA, "matches_cell": None}
    assert laya["provenance"]["git_head"] == "abc123"
    assert laya["provenance"]["git_dirty"] is False
    files = laya["provenance"]["files"]
    assert {"python/mentat_bench/children/systemone_child.py", "python/mentat_bench/harness/fixed.py", "python/mentat_bench/cli.py",
            "profiles/mentat-hard-v1.json", "python/mentat_bench/adapters/base.py"} <= set(files)
    assert all(len(digest) == 64 for digest in files.values())
    assert str(synthetic_runs["runs_root"].parent) not in json.dumps(records)
    assert sorted(p.name for p in synthetic_runs["records_dir"].iterdir()) == ["laya_latest.attempt1.json", "lexical.attempt1.json"]
    doc = RM.collect(synthetic_runs["records_dir"], synthetic_runs["runs_json"])
    assert [r["id"] for r in doc["runs"]] == ["laya_latest", "lexical"]
    assert json.loads(synthetic_runs["runs_json"].read_text(encoding="utf-8"))["schema"] == RM.RUNS_SCHEMA


@pytest.mark.parametrize("recorded, matches", [(ENGINE_SHA, True), ("0" * 64, False)])
def test_the_engine_sha_is_compared_with_the_cell_and_not_refused_on_mismatch(synthetic_runs, monkeypatch, recorded, matches):
    def with_runtime(run_id):
        doc = fake_load_cell(run_id)
        doc["cell"]["runtime"] = {"engine": {"sha256": recorded}}
        return doc

    monkeypatch.setattr(RM, "load_cell", with_runtime)
    fake_build(monkeypatch)
    (rec,) = RM.run(["lexical"], runs_root=synthetic_runs["runs_root"], fixture_dir=synthetic_runs["fixture_dir"],
                    records_dir=synthetic_runs["records_dir"])
    assert rec["engine"]["matches_cell"] is matches and rec["exit_code"] == 0


def test_a_failed_build_is_recorded_as_incomplete_with_its_exit_code(synthetic_runs, monkeypatch):
    fake_build(monkeypatch, code=1)
    (rec,) = RM.run(["heuristic"], runs_root=synthetic_runs["runs_root"], fixture_dir=synthetic_runs["fixture_dir"],
                    records_dir=synthetic_runs["records_dir"])
    assert rec["exit_code"] == 1 and rec["status"] == "incomplete" and rec["partial"] == []
    assert (synthetic_runs["records_dir"] / "heuristic.attempt1.json").is_file()


def test_the_build_status_is_read_as_runcell_reads_it(synthetic_runs, monkeypatch):
    fake_build(monkeypatch, summary={"adapter_fatal": True})  # the adapter failed: runcell's status is incomplete
    (rec,) = RM.run(["lexical"], runs_root=synthetic_runs["runs_root"], fixture_dir=synthetic_runs["fixture_dir"],
                    records_dir=synthetic_runs["records_dir"])
    assert rec["exit_code"] == 0 and rec["status"] == "incomplete"


def test_an_unreadable_decision_log_is_recorded_by_its_type_and_the_record_is_still_written(synthetic_runs, monkeypatch):
    row = {"qid": "R1", "op": "decide_relation", "a": "A1", "b": "B1", "decision": {"relation": "EQUIV"}, "error": None}
    fake_build(monkeypatch, decisions=[row, {**row, "decision": {"relation": "NONE"}}])  # two different rows for one qid
    (rec,) = RM.run(["lexical"], runs_root=synthetic_runs["runs_root"], fixture_dir=synthetic_runs["fixture_dir"],
                    records_dir=synthetic_runs["records_dir"])
    assert rec["log_error"] == "ValueError" and rec["decision_log"] is None and rec["status"] == "complete"
    assert (synthetic_runs["records_dir"] / "lexical.attempt1.json").is_file()


def test_the_decision_log_counts_rows_and_error_rows_by_op(tmp_path):
    run = tmp_path / "run"
    (run / "submission.build").mkdir(parents=True)
    rows = [{"qid": "R1", "op": "decide_relation", "a": "A", "b": "B", "decision": {"relation": "EQUIV"}, "error": None},
            {"qid": "G1", "op": "decide_group", "decision": {"groups": []}, "error": None},
            {"qid": None, "op": "decide_binding", "decision": None, "error": "AdapterError: gave up"},
            {"qid": "R2", "op": "decide_relation", "decision": None, "error": "AdapterError: gave up"}]
    text = "".join(json.dumps(row) + "\n" for row in rows) + '{"qid": "R3", "op": '  # a torn last line
    (run / "submission.build" / "decisions.jsonl").write_text(text, encoding="utf-8")
    assert RM.decision_log(run) == {"decisions": {"decide_relation": 2, "decide_group": 1, "decide_binding": 1},
                                    "error_rows": {"decide_relation": 1, "decide_binding": 1}, "torn_final_line": 1,
                                    "duplicate_identical": 0}
    assert RM.decision_log(tmp_path / "no-run") is None


# ---- the commands: plan and run exit codes


def test_plan_prints_the_19_argvs_with_their_local_paths_named(synthetic_runs, capsys):
    assert RM.main(["plan"]) == 0
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 19 and all("<fixture>" in line and "<run>" in line for line in lines)
    assert str(synthetic_runs["fixture_dir"]) not in out and str(synthetic_runs["runs_root"]) not in out


def test_run_exits_2_on_a_refusal_and_starts_nothing(synthetic_runs, monkeypatch):
    fake_build(monkeypatch)
    assert RM.main(["run", "laya_latest"]) == 2  # no --url
    assert RM.main(["run", "no-such-run"]) == 2
    assert not (synthetic_runs["runs_root"] / "laya_latest").exists()
    assert not synthetic_runs["records_dir"].exists()


def test_run_exits_0_when_every_build_exits_0(synthetic_runs, monkeypatch):
    fake_build(monkeypatch)
    assert RM.main(["run", "lexical"]) == 0


def test_run_exits_1_when_a_build_exits_non_zero(synthetic_runs, monkeypatch):
    fake_build(monkeypatch, code=1)
    assert RM.main(["run", "heuristic"]) == 1


def test_run_exits_3_on_an_error_after_the_runs_started(synthetic_runs, monkeypatch):
    fake_build(monkeypatch)

    def broken(*args, **kwargs):
        raise OSError("made-up failure after the start")

    monkeypatch.setattr(RM, "execute", broken)
    assert RM.main(["run", "lexical"]) == 3


# ---- the mapping of decisions to classes


REL = {"qid": "R1", "op": "decide_relation", "a": "A1", "b": "B1"}
GRP = {"qid": "G1", "op": "decide_group", "members": ["m1", "m2", "m3"]}
CANDS = {"R1": REL, "G1": GRP}


def decided(qid, op, decision=None, a=None, b=None, error=None, conf=None):
    return {"qid": qid, "op": op, "a": a, "b": b, "id": None, "members": None, "decision": decision, "conf": conf, "probs": None,
            "error": error}


def group(kind, members=("m1", "m2", "m3"), k=None, direction=None, scope=None):
    return {"kind": kind, "members": list(members), "k": k, "direction": direction, "scope": scope}


def classify_group(*items):
    return PR.classify("G1", GRP, decided("G1", "decide_group", {"groups": list(items)}), CANDS)


@pytest.mark.parametrize("relation, mapped", [("IMPLIES", "IMPLIES"), ("IMPLIED_BY", "IMPLIED_BY"), ("EQUIV", "EQUIV"),
                                              ("EXCLUDES", "EXCLUDES"), ("NONE", "NONE")])
def test_a_relation_in_the_candidate_order_is_kept(relation, mapped):
    row = decided("R1", "decide_relation", {"relation": relation}, a="A1", b="B1")
    assert PR.classify("R1", REL, row, CANDS) == (mapped, None)


@pytest.mark.parametrize("relation, mapped", [("IMPLIES", "IMPLIED_BY"), ("IMPLIED_BY", "IMPLIES"), ("EQUIV", "EQUIV"),
                                              ("EXCLUDES", "EXCLUDES"), ("NONE", "NONE")])
def test_a_relation_asked_in_the_reverse_order_is_reoriented(relation, mapped):
    row = decided("R1", "decide_relation", {"relation": relation}, a="B1", b="A1")
    assert PR.classify("R1", REL, row, CANDS) == (mapped, None)


def test_a_bare_relation_name_is_read_as_the_relation():
    assert PR.classify("R1", REL, decided("R1", "decide_relation", "EXCLUDES", a="A1", b="B1"), CANDS) == ("EXCLUDES", None)


def test_group_bounds_give_the_class_of_their_terms():
    assert classify_group(group("at_least_k", k=1), group("at_most_k", k=2)) == ("at_least_k:1+at_most_k:2", None)
    assert classify_group(group("at_least_k", k=2)) == ("at_least_k:2", None)
    assert classify_group(group("exactly_one")) == ("exactly_one", None)
    assert classify_group(group("at_most_k", k=1)) == ("at_most_one", None)
    assert classify_group(group("at_most_k", k=2)) == ("at_most_k:2", None)


def test_a_group_chain_adds_ordered_chain_to_the_class():
    assert classify_group(group("ordered_chain", direction="forward")) == ("ordered_chain", None)
    assert classify_group(group("ordered_chain", direction="reverse"), group("at_least_k", k=1)) == ("ordered_chain+at_least_k:1", None)


def test_no_group_is_the_class_none():
    assert classify_group() == ("none", None)


def test_a_scoped_group_does_not_set_the_bounds():
    scope = {"axis": "year", "values": ["2025"], "of": ["2025", "2026"]}
    assert classify_group(group("at_least_k", k=3, scope=scope), group("at_most_k", k=2)) == ("at_most_k:2", None)


def test_bounds_with_lo_above_hi_are_invalid_and_missing():
    assert classify_group(group("at_least_k", k=3), group("at_most_k", k=2)) == (DS.MISSING, "invalid_bounds")


def test_a_malformed_group_answer_is_missing():
    assert classify_group(group("bogus_kind")) == (DS.MISSING, "malformed")
    assert PR.classify("G1", GRP, decided("G1", "decide_group", {"groups": "x"}), CANDS) == (DS.MISSING, "malformed")


def test_an_error_row_is_missing_whatever_its_answer():
    assert PR.classify("G1", GRP, decided("G1", "decide_group", None, error="AdapterError: gave up"), CANDS) == (DS.MISSING, "error")
    assert PR.classify("R1", REL, decided("R1", "decide_relation", {"relation": "IMPLIES"}, a="A1", b="B1", error="x"),
                       CANDS) == (DS.MISSING, "error")


def test_unresolved_is_missing_and_not_credited_as_none():
    row = decided("R1", "decide_relation", {"relation": "UNRESOLVED"}, a="A1", b="B1")
    assert PR.classify("R1", REL, row, CANDS) == (DS.MISSING, "unresolved")


def test_an_absent_decision_is_missing():
    assert PR.classify("R1", REL, None, CANDS) == (DS.MISSING, "absent")
    assert PR.classify("G1", GRP, None, CANDS) == (DS.MISSING, "absent")


def test_an_unknown_relation_name_is_missing_as_malformed():
    row = decided("R1", "decide_relation", {"relation": "MAYBE"}, a="A1", b="B1")
    assert PR.classify("R1", REL, row, CANDS) == (DS.MISSING, "malformed")


def test_a_conf_that_is_not_a_number_is_missing():
    row = decided("R1", "decide_relation", {"relation": "EQUIV"}, a="A1", b="B1", conf="high")
    assert PR.classify("R1", REL, row, CANDS) == (DS.MISSING, "invalid_conf")


def test_a_class_outside_the_vocabulary_of_its_item_is_missing(monkeypatch):
    monkeypatch.setattr(DS, "_terms_answer", lambda decision, members: ("at_least_k:9", None))  # not a class of three members
    assert PR.classify("G1", GRP, decided("G1", "decide_group", {"groups": []}), CANDS) == (DS.MISSING, "outside_vocabulary")


def test_a_decision_of_another_op_than_its_candidate_is_refused():
    with pytest.raises(ValueError, match="is op"):
        PR.classify("R1", REL, decided("R1", "decide_group", {"groups": []}), CANDS)


LAYA_ROWS = [{"qid": "R1", "op": "decide_relation", "a": "A1", "b": "B1", "decision": {"relation": "IMPLIES"}, "error": None},
             {"qid": "G1", "op": "decide_group", "decision": {"groups": [{"kind": "at_least_k", "members": ["m1", "m2", "m3"],
                                                                         "k": 1}]}, "error": None}]


def test_duplicate_and_torn_rows_are_read_as_the_decision_log_reads_them(tmp_path):
    path = tmp_path / "decisions.jsonl"
    path.write_text(json.dumps(LAYA_ROWS[0]) + "\n" + json.dumps(LAYA_ROWS[0]) + "\n" + '{"qid": "G1", "op":', encoding="utf-8")
    classes, reasons, read = PR.map_decisions(path, ["R1", "G1"], CANDS)
    assert classes == {"R1": "IMPLIES", "G1": DS.MISSING}  # the identical repeat counts once; the torn last line is dropped
    assert reasons == Counter({"absent": 1}) and read == {"torn_final_line": 1, "duplicate_identical": 1}


def test_two_different_rows_for_one_qid_are_refused(tmp_path):
    path = tmp_path / "decisions.jsonl"
    path.write_text(json.dumps(LAYA_ROWS[0]) + "\n" + json.dumps({**LAYA_ROWS[0], "decision": {"relation": "NONE"}}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting"):
        PR.map_decisions(path, ["R1", "G1"], CANDS)


# ---- write: every run done, the published inputs as recorded, the outputs


def synthetic_base(base: Path) -> Path:
    """A made-up selection and published candidates (two items, one relation and one group of three members), and the manifest that
    records their hashes, as analyse.py fixture writes it."""
    AN.write_lines(base / "selection.jsonl", [{"id": "R1", "op": "decide_relation", "cell": "x", "final": "EXCLUDES"},
                                              {"id": "G1", "op": "decide_group", "cell": "y", "final": "at_least_k:1"}])
    AN.write_lines(base / "fixture" / "candidates.jsonl", [{"qid": "R1", "op": "decide_relation", "a": "A1", "b": "B1"},
                                                           {"qid": "G1", "op": "decide_group", "members": ["m1", "m2", "m3"]}])
    AN.write_json(base / "fixture_manifest.json", {
        "schema": "mentat-bench-transfer-fixture/1", "files": {"candidates.jsonl": AN.sha_file(base / "fixture" / "candidates.jsonl")},
        "inputs": {"selection.jsonl": AN.sha_file(base / "selection.jsonl")}})
    return base


def record_for(runs_root: Path, run_id: str, rows: list[dict], *, attempt: int = 1, folder: str | None = None, exit_code: int = 0,
               status: str = "complete", torn: int = 0) -> dict:
    """A made-up record of a run whose decisions file holds these rows, as run_one writes it."""
    name = folder or run_id
    path = runs_root / name / "submission.build" / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return {"schema": RM.RECORD_SCHEMA, "id": run_id, "attempt": attempt, "folder": name, "exit_code": exit_code, "status": status,
            "partial": [], "log_error": None,
            "decision_log": {"decisions": {}, "error_rows": {}, "torn_final_line": torn, "duplicate_identical": 0},
            "decisions_file": {"sha256": AN.sha_file(path), "bytes": path.stat().st_size}}


def set_up(tmp_path: Path) -> dict[str, Path]:
    """All 19 runs done: the laya run maps two items, and every other run has no decisions."""
    paths = {"base": synthetic_base(tmp_path / "base"), "runs_root": tmp_path / "runs", "records_dir": tmp_path / "records",
             "runs_json": tmp_path / "runs.json", "out": tmp_path / "out"}
    for run_id in RM.run_ids():
        rows = LAYA_ROWS if run_id == "laya_latest" else []
        AN.write_json(paths["records_dir"] / f"{run_id}.attempt1.json", record_for(paths["runs_root"], run_id, rows))
    return paths


def write_args(paths: dict[str, Path]) -> dict[str, Path]:
    return {"records_dir": paths["records_dir"], "runs_root": paths["runs_root"], "runs_json": paths["runs_json"],
            "base": paths["base"], "out_dir": paths["out"]}


def test_write_maps_all_19_runs_and_counts_what_is_missing(tmp_path):
    paths = set_up(tmp_path)
    PR.write(**write_args(paths))
    predictions = json.loads((paths["out"] / "predictions.json").read_text(encoding="utf-8"))
    assert sorted(predictions) == sorted(RM.run_ids())
    assert predictions["laya_latest"] == {"R1": "IMPLIES", "G1": "at_least_k:1"}
    assert predictions["lexical"] == {"R1": DS.MISSING, "G1": DS.MISSING}
    summary = json.loads((paths["out"] / "mapping_summary.json").read_text(encoding="utf-8"))
    assert summary["items"] == 2 and summary["models"]["laya_latest"]["missing"] == 0
    assert summary["models"]["lexical"]["by_reason"] == {"absent": 2} and summary["models"]["lexical"]["missing"] == 2
    assert len(json.loads(paths["runs_json"].read_text(encoding="utf-8"))["runs"]) == 19  # write also merges runs.json


def test_write_refuses_a_run_whose_latest_attempt_exited_non_zero(tmp_path):
    paths = set_up(tmp_path)
    AN.write_json(paths["records_dir"] / "d3_27b-q8_0.attempt2.json",
                  record_for(paths["runs_root"], "d3_27b-q8_0", [], attempt=2, folder="d3_27b-q8_0.attempt2", exit_code=1))
    with pytest.raises(AN.AnalyseError, match="d3_27b-q8_0: exit code 1"):
        PR.write(**write_args(paths))
    assert not (paths["out"] / "predictions.json").exists()


def test_write_refuses_a_run_with_no_record(tmp_path):
    paths = set_up(tmp_path)
    (paths["records_dir"] / "heuristic.attempt1.json").unlink()
    with pytest.raises(AN.AnalyseError, match="heuristic: no record"):
        PR.write(**write_args(paths))
    assert not (paths["out"] / "predictions.json").exists()


def test_write_refuses_a_run_whose_status_is_not_complete(tmp_path):
    paths = set_up(tmp_path)
    AN.write_json(paths["records_dir"] / "heuristic.attempt1.json", record_for(paths["runs_root"], "heuristic", [], status="incomplete"))
    with pytest.raises(AN.AnalyseError, match="heuristic: status incomplete"):
        PR.write(**write_args(paths))


def test_write_refuses_a_decision_log_with_a_torn_final_line(tmp_path):
    paths = set_up(tmp_path)
    AN.write_json(paths["records_dir"] / "lexical.attempt1.json", record_for(paths["runs_root"], "lexical", [], torn=1))
    with pytest.raises(AN.AnalyseError, match="lexical: a torn final line"):
        PR.write(**write_args(paths))


def test_write_refuses_a_run_whose_decision_log_could_not_be_read(tmp_path):
    paths = set_up(tmp_path)
    rec = json.loads((paths["records_dir"] / "lexical.attempt1.json").read_text(encoding="utf-8"))
    rec["log_error"] = "ValueError"
    AN.write_json(paths["records_dir"] / "lexical.attempt1.json", rec)
    with pytest.raises(AN.AnalyseError, match="lexical: its decision log could not be read"):
        PR.write(**write_args(paths))


def test_write_refuses_a_decisions_file_that_changed_after_its_run(tmp_path):
    paths = set_up(tmp_path)
    path = paths["runs_root"] / "laya_latest" / "submission.build" / "decisions.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(AN.AnalyseError, match="laya_latest: decisions.jsonl changed since the run"):
        PR.write(**write_args(paths))
    assert not (paths["out"] / "predictions.json").exists()


def test_write_refuses_a_missing_decisions_file(tmp_path):
    paths = set_up(tmp_path)
    (paths["runs_root"] / "heuristic" / "submission.build" / "decisions.jsonl").unlink()
    with pytest.raises(AN.AnalyseError, match="heuristic: decisions.jsonl is missing"):
        PR.write(**write_args(paths))


def test_write_refuses_a_fixture_that_is_not_the_recorded_one(tmp_path):
    paths = set_up(tmp_path)
    AN.write_lines(paths["base"] / "fixture" / "candidates.jsonl", [{"qid": "R1", "op": "decide_relation", "a": "A9", "b": "B1"},
                                                                    {"qid": "G1", "op": "decide_group", "members": ["m1", "m2", "m3"]}])
    with pytest.raises(AN.AnalyseError, match="does not match fixture_manifest.json"):
        PR.write(**write_args(paths))


def test_write_refuses_a_selection_that_is_not_the_recorded_one(tmp_path):
    paths = set_up(tmp_path)
    AN.write_lines(paths["base"] / "selection.jsonl", [{"id": "R1", "op": "decide_relation", "cell": "x", "final": "NONE"},
                                                       {"id": "G1", "op": "decide_group", "cell": "y", "final": "at_least_k:1"}])
    with pytest.raises(AN.AnalyseError, match="selection.jsonl does not match fixture_manifest.json"):
        PR.write(**write_args(paths))


def test_the_published_candidates_must_be_the_selected_items(tmp_path):
    base = synthetic_base(tmp_path / "base")
    AN.write_lines(base / "fixture" / "candidates.jsonl", [{"qid": "R1", "op": "decide_relation", "a": "A1", "b": "B1"}])
    with pytest.raises(AN.AnalyseError, match="not the selected items"):
        PR.read_items(base)


def test_predictions_main_exits_0_when_written_and_1_when_refused(tmp_path, monkeypatch):
    paths = set_up(tmp_path)
    monkeypatch.setattr(PR, "HERE", paths["base"])
    monkeypatch.setattr(RM, "RUNS_DIR", paths["records_dir"])
    monkeypatch.setattr(RM, "LOCAL_RUNS", paths["runs_root"])
    monkeypatch.setattr(RM, "RUNS_JSON", paths["runs_json"])
    assert PR.main(["write"]) == 0
    assert (paths["base"] / "predictions.json").is_file()
    (paths["records_dir"] / "heuristic.attempt1.json").unlink()
    assert PR.main(["write"]) == 1
    assert PR.main(["one", "laya_latest"]) == 0

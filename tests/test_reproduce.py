"""tools/reproduce.py: the pure comparison and its checks on synthetic rows, a compare of a published cell's own predictions through a
run directory built from the published records, the rows read from a real decisions file, the command built from a published
cell's recorded flags, the refusals and exit codes of run (with subprocess replaced), and the corrupt-record cases. Fast: no engine,
no Ollama, no model. The one real subprocess is the committed build --check, which reads committed files only."""

import copy
import gzip
import importlib.util
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mentat_bench import runcell

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "reproduce.py"
_spec = importlib.util.spec_from_file_location("reproduce_tool", TOOL)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")
GROUPS = ("exactly_one", "at_most_one", "at_least_k:2", "ordered_chain")
BINDINGS = ("bound", "unresolved")
READINGS = ("base", "alt")


def synthetic_rows(seed: int = 0) -> list[dict]:
    """Prediction rows of a made-up run, shaped like predictions.jsonl.gz: 6000 relations, 1000 groups, 3000 bindings and 184
    reading questions (368 items). About 80% of the answers are right; each item's probabilities sum to one."""
    rng = random.Random(seed)
    rows = []

    def add(qid, op, options, reading=None):
        gold = rng.choice(options)
        pred = gold if rng.random() < 0.8 else rng.choice(options)
        raw = {o: rng.random() + (3.0 if o == pred else 0.0) for o in options}
        total = sum(raw.values())
        row = {"qid": qid, "op": op, "stratum": "synthetic", "held_out": rng.random() < 0.4, "gold": gold, "pred": pred,
               "correct": gold == pred, "probs": {o: round(v / total, 4) for o, v in raw.items()}}
        if reading is not None:
            row["reading"] = reading
        rows.append(row)

    for i in range(6000):
        add(f"r{i:05d}", "decide_relation", RELATIONS)
    for i in range(1000):
        add(f"g{i:05d}", "decide_group", GROUPS)
    for i in range(3000):
        add(f"b{i:05d}", "decide_binding", BINDINGS)
    for i in range(184):
        for reading in READINGS:
            add(f"x{i:05d}", "decide_reading", ("A", "B", "C", "D"), reading)
    return rows


def flipped(rows: list[dict], op: str, count: int) -> list[dict]:
    """A copy in which the first `count` items of one op name another option."""
    out = copy.deepcopy(rows)
    for row in [r for r in out if r["op"] == op][:count]:
        row["pred"] = next(o for o in row["probs"] if o != row["pred"])
    return out


def drifted(rows: list[dict], amount: float) -> list[dict]:
    """A copy in which every item's probability of its predicted option is raised by `amount` (and another option lowered by it):
    the predictions are unchanged, only the probabilities move."""
    out = copy.deepcopy(rows)
    for row in out:
        other = next(o for o in row["probs"] if o != row["pred"])
        row["probs"][row["pred"]] = round(row["probs"][row["pred"]] + amount, 4)
        row["probs"][other] = round(row["probs"][other] - amount, 4)
    return out


def compare(published, reproduced):
    """The comparison with the committed headline taken as the rescored one (so only the rows decide)."""
    c = R.compare_rows(published, reproduced, 3)
    return c, R.checks_for(c, c["metrics"]["headline"]["published"])


def verdict(published, reproduced, bytes_equal):
    c, checks = compare(published, reproduced)
    return R.verdict_for(checks, bytes_equal)


# ---------------------------------------------------------------- pure comparison on synthetic rows

def test_identical_rows_are_exact_at_the_metric_level():
    rows = synthetic_rows()
    c, checks = compare(rows, copy.deepcopy(rows))
    assert c["same_items"] is True
    assert c["decisions"]["items"] == 10368 and c["decisions"]["flips"] == 0 and c["decisions"]["flip_rate"] == 0
    assert c["max_prob_diff"] == 0
    assert all(m["delta"] == 0 for m in c["metrics"].values())
    assert all(check["ok"] for check in checks.values())
    assert R.verdict_for(checks, bytes_equal=True) == ("exact", [])


def test_a_few_flips_are_within_tolerance_and_name_every_check():
    rows = synthetic_rows()
    c, checks = compare(rows, flipped(rows, "decide_relation", 3))
    assert c["decisions"]["flips"] == 3 and c["decisions"]["per_op"]["decide_relation"]["flips"] == 3
    assert c["decisions"]["per_op"]["decide_group"]["flips"] == 0
    assert abs(c["metrics"]["relation"]["delta"]) <= R.METRIC_TOL
    assert abs(c["metrics"]["headline"]["delta"]) <= R.METRIC_TOL
    verdict_, reasons = R.verdict_for(checks, bytes_equal=False)
    assert verdict_ == "within_tolerance"
    for name in ("same_items", "headline", "relation", "group", "binding", "reading", "flip_rate", "flip_rate:decide_relation",
                 "flip_rate:decide_binding", "flip_rate:decide_group", "flip_rate:decide_reading", "max_prob_diff",
                 "published_headline"):
        assert name in checks and name in reasons[0], name


def test_flips_over_tolerance_differ():
    rows = synthetic_rows()
    verdict_, reasons = verdict(rows, flipped(rows, "decide_relation", 400), bytes_equal=False)
    assert verdict_ == "differs"
    assert any(r.startswith("flip_rate:") for r in reasons) and any(r.startswith("relation:") for r in reasons)


def test_probability_only_drift_is_within_tolerance_up_to_the_limit():
    rows = synthetic_rows()
    small, checks = compare(rows, drifted(rows, 0.01))
    assert small["decisions"]["flips"] == 0 and small["metrics"]["headline"]["delta"] == 0
    assert abs(small["max_prob_diff"] - 0.01) < 1e-9
    assert R.verdict_for(checks, bytes_equal=False)[0] == "within_tolerance"
    verdict_, reasons = verdict(rows, drifted(rows, 0.05), bytes_equal=False)
    assert verdict_ == "differs" and any(r.startswith("max_prob_diff:") for r in reasons)


def test_a_flip_on_a_small_op_is_judged_per_op():
    rows = synthetic_rows()
    two, checks = compare(rows, flipped(rows, "decide_reading", 2))
    assert two["decisions"]["per_op"]["decide_reading"]["items"] == 368
    assert checks["flip_rate:decide_reading"]["ok"] and checks["reading"]["ok"]  # two of 368 is the per-op allowance
    assert R.verdict_for(checks, bytes_equal=False)[0] == "within_tolerance"
    three = verdict(rows, flipped(rows, "decide_reading", 3), bytes_equal=False)
    assert three[0] == "differs" and any(r.startswith("flip_rate:decide_reading:") for r in three[1])


def test_different_item_sets_differ():
    rows = synthetic_rows()
    c, checks = compare(rows, rows[:-1])
    assert c["same_items"] is False and checks["same_items"]["ok"] is False
    assert R.verdict_for(checks, bytes_equal=False)[0] == "differs"


def test_a_missing_answer_is_a_flip_not_a_label_change():
    rows = synthetic_rows()
    unanswered = copy.deepcopy(rows)
    unanswered[0]["pred"], unanswered[0]["probs"] = "__missing__", None
    c, _ = compare(rows, unanswered)
    assert c["same_items"] is True  # the labels are the same
    assert c["decisions"]["per_op"]["decide_relation"]["flips"] == 1
    assert c["max_prob_diff"] == 0  # no probability is compared with an absent one


def test_the_committed_headline_is_checked_against_the_rescored_one():
    rows = synthetic_rows()
    c = R.compare_rows(rows, rows, 3)
    checks = R.checks_for(c, published_headline=0.9)
    assert checks["published_headline"]["ok"] is False
    assert R.verdict_for(checks, bytes_equal=True)[0] == "differs"
    assert R.checks_for(c, published_headline=None)["published_headline"]["ok"] is False  # a missing headline fails too


def test_the_headline_needs_a_version_3_fixture():
    with pytest.raises(ValueError):
        R.compare_rows(synthetic_rows(), synthetic_rows(), None)


def test_the_gzip_container_does_not_decide_exact():
    rows = synthetic_rows()
    _, checks = compare(rows, rows)
    assert R.verdict_for(checks, bytes_equal=True) == ("exact", [])  # bytes_equal is the decompressed comparison


# ---------------------------------------------------------------- a run directory built from the published records

def write_fake_run(run: Path, cell_id: str, status: str = "complete", release: str = R.RELEASE) -> None:
    """A run directory with the files compare reads, built from the published records (not from a run). Its decisions file is
    empty: the rows are supplied by the test (run_rows is replaced)."""
    doc = R.published_doc(cell_id, release)
    card = R.runcard(release, cell_id)
    (run / "submission.build").mkdir(parents=True, exist_ok=True)
    (run / "submission").mkdir(exist_ok=True)
    (run / "submission.build" / "decisions.jsonl").write_text("", encoding="utf-8")
    (run / "cell.json").write_text(json.dumps({**doc["cell"], "status": status}), encoding="utf-8")
    (run / "score.json").write_text(json.dumps(doc["score"]), encoding="utf-8")
    (run / "score_decisions.json").write_text(json.dumps(doc["score_decisions"]), encoding="utf-8")
    (run / "submission" / "build_policy.json").write_text(json.dumps(card["build_policy"]), encoding="utf-8")


def fake_run(tmp_path: Path, cell_id: str, status: str = "complete", release: str = R.RELEASE) -> Path:
    run = tmp_path / cell_id
    write_fake_run(run, cell_id, status, release)
    return run


def published_rows(cell_id: str, release: str = R.RELEASE) -> list[dict]:
    return R.decode((ROOT / R.published_doc(cell_id, release)["predictions"]).read_bytes())


def test_compare_of_a_published_cells_own_predictions_is_exact(tmp_path, monkeypatch):
    cell_id = "lexical"
    published_bytes = (ROOT / R.published_doc(cell_id, R.RELEASE)["predictions"]).read_bytes()
    monkeypatch.setattr(R, "run_rows", lambda run_dir: R.decode(published_bytes))
    run = fake_run(tmp_path, cell_id)
    report, path = R.compare_run(run, cell_id)
    assert report["verdict"] == "exact" and report["reasons"] == []
    # The gzip container depends on the zlib build (it differs on Linux CI), so only the content decides exact.
    assert report["predictions_bytes_equal"] is True and report["same_items"] is True
    assert report["environment"]["differences"] == []
    assert report["environment"]["child_interpreter"] == {"published": "python.exe", "reproduced": "python.exe"}
    assert report["metrics"]["headline"]["delta"] == 0 and report["decisions"]["flips"] == 0
    assert all(check["ok"] for check in report["checks"].values())
    assert path == run.parent / f"{cell_id}.reproduce" / "report.json"
    assert json.loads(path.read_text(encoding="utf-8"))["verdict"] == "exact"
    written = (run.parent / f"{cell_id}.reproduce" / "predictions.jsonl.gz").read_bytes()
    assert gzip.decompress(written) == gzip.decompress(published_bytes)
    text = path.read_text(encoding="utf-8")
    assert report["run"] == cell_id and str(tmp_path) not in text and str(R.ROOT) not in text  # no local path in the report


def test_a_run_whose_cell_is_not_complete_is_incomplete(tmp_path, monkeypatch):
    cell_id = "lexical"
    rows = published_rows(cell_id)
    monkeypatch.setattr(R, "run_rows", lambda run_dir: rows)
    run = fake_run(tmp_path, cell_id, status="incomplete")
    report, _ = R.compare_run(run, cell_id)
    assert report["verdict"] == "incomplete"
    assert any("not complete" in reason for reason in report["reasons"])
    assert report["metrics"] is not None  # the numbers are still shown; the verdict is not exact
    assert R.main(["compare", str(run), "--id", cell_id]) == 1


@pytest.mark.parametrize("victim", ["cell.json", "score.json", "submission/build_policy.json"])
@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])
def test_a_corrupt_record_is_incomplete_not_a_traceback(tmp_path, monkeypatch, victim, content):
    cell_id = "lexical"
    monkeypatch.setattr(R, "run_rows", lambda run_dir: published_rows(cell_id))
    run = fake_run(tmp_path, cell_id)
    (run / victim).write_text(content, encoding="utf-8")
    report, path = R.compare_run(run, cell_id)
    assert report["verdict"] == "incomplete"
    assert any(reason.startswith(victim) for reason in report["reasons"]), report["reasons"]
    assert json.loads(path.read_text(encoding="utf-8"))["verdict"] == "incomplete"


def test_a_decisions_line_that_is_not_an_object_is_incomplete(tmp_path):
    cell_id = "lexical"
    run = fake_run(tmp_path, cell_id)
    (run / "submission.build" / "decisions.jsonl").write_text("[1, 2]\n", encoding="utf-8")
    report, _ = R.compare_run(run, cell_id)
    assert report["verdict"] == "incomplete"
    assert any(reason == "the run's decisions cannot be read: decisions.jsonl line 1 is not a JSON object"
               for reason in report["reasons"]), report["reasons"]
    assert str(tmp_path) not in json.dumps(report)


def test_run_rows_reads_a_real_decisions_file(tmp_path):
    """The rows of a run built from a small decisions file on the committed test fixture: two bindings are answered, every other
    item is unanswered, and the rows carry the published labels."""
    run = tmp_path / "run"
    (run / "submission.build").mkdir(parents=True)
    (run / "submission.build" / "decisions.jsonl").write_text(
        json.dumps({"qid": "b00001", "op": "decide_binding", "decision": {"status": "bound"}}) + "\n"
        + json.dumps({"qid": "b00002", "op": "decide_binding", "decision": {"status": "unresolved"}}) + "\n", encoding="utf-8")
    rows = R.run_rows(run)
    assert len(rows) == 10716
    by_qid = {r["qid"]: r for r in rows if r["op"] == "decide_binding"}
    assert by_qid["b00001"]["pred"] == "bound" and by_qid["b00001"]["gold"] == "bound" and by_qid["b00001"]["correct"]
    assert by_qid["b00002"]["pred"] == "unresolved" and by_qid["b00002"]["gold"] == "bound" and not by_qid["b00002"]["correct"]
    assert by_qid["b00003"]["pred"] == "__missing__"
    assert sum(r["pred"] != "__missing__" for r in rows) == 2  # only the two decisions were answered


def test_environment_lists_only_the_facts_that_differ():
    doc = R.published_doc("d3_27b-q8_0", R.RELEASE)
    card = R.read_json(ROOT / "release" / R.RELEASE / "runcards" / "d3_27b-q8_0.json")
    published = R.facts(doc["cell"], card["build_policy"])
    assert R.compare_environment(published, published)["differences"] == []
    other = json.loads(json.dumps(doc["cell"]))
    other["runtime"]["engine"]["sha256"] = "0" * 64
    diff = R.compare_environment(published, R.facts(other, card["build_policy"]))
    assert diff["differences"] == ["engine_sha256"]
    assert diff["compared"]["engine_sha256"]["published"] == doc["cell"]["runtime"]["engine"]["sha256"]
    windows = json.loads(json.dumps(doc["cell"]))
    windows["build_flags"][-1] = r"fixtures\mentat_hard_test\candidates.jsonl"  # runcell records a Windows path with backslashes
    assert R.compare_environment(published, R.facts(windows, card["build_policy"]))["differences"] == []


def test_binding_and_reading_have_their_own_limits():
    rows = synthetic_rows()
    _, checks = compare(rows, rows)
    assert checks["binding"]["limit"] == R.BINDING_TOL == 0.01
    assert checks["reading"]["limit"] == R.READING_TOL == 0.01
    assert checks["headline"]["limit"] == R.METRIC_TOL == 1e-3


# ---------------------------------------------------------------- run-defining facts, report-only facts, one-sided packages

def edit_run(run: Path, name: str, mutate) -> None:
    """Rewrites one JSON file of a fake run directory after mutate(document) has changed it."""
    path = run / name
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document), encoding="utf-8")


RUN_DEFINING_EDITS = {  # the field -> (the file of the fake run, the change); each one differs from the published lexical run
    "build_flags": ("cell.json", lambda d: d.__setitem__("build_flags", d["build_flags"] + ["--batch-size", "64"])),
    "adapter": ("cell.json", lambda d: d.__setitem__("adapter", "empty")),
    "child_arguments": ("cell.json", lambda d: d.__setitem__("local_arg", ["python.exe", "-m", "mentat_bench.children.other"])),
    "fixture_path": ("cell.json", lambda d: d.__setitem__("fixture_path", "fixtures/mentat_hard_dev")),
    "fixture_files": ("cell.json", lambda d: d["runtime"]["fixture"]["files"].__setitem__("queries.jsonl", "0" * 64)),
    "profile_path": ("cell.json", lambda d: d.__setitem__("profile", "profiles/other.json")),
    "profile_sha256": ("cell.json", lambda d: d["runtime"].__setitem__("profile_sha256", "0" * 64)),
    "model_digest": ("submission/build_policy.json", lambda d: d["model"].__setitem__("revision", "a" * 64)),
    "prompt_sha256": ("submission/build_policy.json", lambda d: d["model"].__setitem__("prompt_sha256", "b" * 64)),
}


@pytest.mark.parametrize("field", sorted(RUN_DEFINING_EDITS))
def test_a_run_defining_difference_is_never_exact(tmp_path, monkeypatch, field):
    cell_id = "lexical"
    rows = published_rows(cell_id)
    monkeypatch.setattr(R, "run_rows", lambda run_dir: rows)  # the predictions are the published ones: only the record differs
    run = fake_run(tmp_path, cell_id)
    name, mutate = RUN_DEFINING_EDITS[field]
    edit_run(run, name, mutate)
    report, _ = R.compare_run(run, cell_id)
    assert report["verdict"] == "differs"
    assert report["checks"][f"run_defining:{field}"]["ok"] is False
    assert any(reason.startswith(f"run_defining:{field}:") for reason in report["reasons"]), report["reasons"]
    assert R.main(["compare", str(run), "--id", cell_id]) == 1


def test_the_report_only_facts_do_not_decide_the_verdict(tmp_path, monkeypatch):
    cell_id = "lexical"
    rows = published_rows(cell_id)
    monkeypatch.setattr(R, "run_rows", lambda run_dir: rows)
    run = fake_run(tmp_path, cell_id)
    edit_run(run, "cell.json", lambda d: d["runtime"]["engine"].__setitem__("sha256", "0" * 64))
    edit_run(run, "cell.json", lambda d: d["runtime"].__setitem__("gpus", [{"name": "Other GPU", "driver": "1"}]))
    edit_run(run, "submission/build_policy.json", lambda d: d["model"].__setitem__("ollama_version", "9.9.9"))
    report, _ = R.compare_run(run, cell_id)
    assert set(report["environment"]["differences"]) == {"engine_sha256", "gpus", "ollama_version"}
    assert not any(name.startswith("run_defining:") for name in report["checks"])
    assert report["verdict"] == "exact"


def test_a_package_on_one_side_only_is_listed_apart_from_the_differences(tmp_path, monkeypatch):
    cell_id = "lexical"
    rows = published_rows(cell_id)
    monkeypatch.setattr(R, "run_rows", lambda run_dir: rows)
    run = fake_run(tmp_path, cell_id)
    edit_run(run, "cell.json", lambda d: d["runtime"]["versions"].pop("duckdb"))  # the published record has duckdb
    report, _ = R.compare_run(run, cell_id)
    assert report["environment"]["differences"] == []
    assert report["environment"]["one_sided"]["versions"] == {"published_only": ["duckdb"], "reproduced_only": []}
    assert report["verdict"] == "exact"
    edit_run(run, "cell.json", lambda d: d["runtime"]["versions"].__setitem__("torch", "0.0"))  # a shared package differs
    report, _ = R.compare_run(run, cell_id)
    assert report["environment"]["differences"] == ["versions"] and report["verdict"] == "exact"


def test_run_refuses_a_non_empty_reproduce_dir_before_running(tmp_path, monkeypatch):
    (tmp_path / "lexical.reproduce").mkdir()
    (tmp_path / "lexical.reproduce" / "report.json").write_text("{}", encoding="utf-8")
    no_subprocess(monkeypatch)
    assert R.main(["run", "lexical", "--out-root", str(tmp_path)]) == 2


# ---------------------------------------------------------------- the child interpreter and the command

def test_a_published_python_child_runs_under_this_interpreter():
    assert R.child_interpreter("python.exe") == sys.executable
    assert R.child_interpreter("python3.11") == sys.executable
    assert R.child_interpreter("/opt/bin/mychild") == "/opt/bin/mychild"
    assert R.resolve_interpreter("python.exe") == sys.executable


def captured_run_cell(monkeypatch) -> dict:
    """Replaces runcell.run_cell so that runcell.main's parsed arguments are recorded and nothing runs."""
    seen = {}

    def fake(fixture, adapter, local_args, out, profile, build_flags, resume=False, extra_queries=False, candidates=None):
        seen.update(fixture=fixture, adapter=adapter, local_args=local_args, out=out, profile=profile, build_flags=build_flags,
                    candidates=candidates, resume=resume, extra_queries=extra_queries)
        return 0

    monkeypatch.setattr(runcell, "run_cell", fake)
    return seen


@pytest.mark.parametrize("cell_id", ["d3_27b-q8_0", "lexical", "heuristic"])
def test_the_command_reproduces_the_recorded_flags(cell_id, tmp_path, monkeypatch):
    cell = R.published_doc(cell_id, R.RELEASE)["cell"]
    argv = R.runcell_argv(cell, tmp_path / cell_id)
    assert argv[1:3] == ["-m", "mentat_bench.runcell"]
    seen = captured_run_cell(monkeypatch)
    assert runcell.main(argv[3:]) == 0
    norm = lambda text: str(text).replace("\\", "/")  # noqa: E731 - a path as runcell records it on Windows or elsewhere
    assert seen["adapter"] == cell["adapter"]
    assert seen["local_args"] == [R.child_interpreter(cell["local_arg"][0]), *cell["local_arg"][1:]]
    assert norm(seen["fixture"]) == cell["fixture_path"]
    assert norm(seen["profile"]) == cell["profile"]
    assert seen["out"] == tmp_path / cell_id
    flags = [*seen["build_flags"], *(["--candidates", norm(seen["candidates"])] if seen["candidates"] else [])]
    assert [norm(f) for f in flags] == cell["build_flags"]
    assert seen["resume"] is False and seen["extra_queries"] is False


def test_a_model_cell_runs_with_the_published_ollama_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MB_S1_OPTION_SEED", "7")  # an inherited option-order setting must not reach a published run
    stats = tmp_path / "d3_27b-q8_0.meta" / "child_stats.json"
    env = R.run_env("d3:27b-q8_0", R.DEFAULT_URL, stats)
    assert env["MB_S1_MODEL"] == "d3:27b-q8_0" and env["MB_S1_URL"] == "http://127.0.0.1:11434"
    assert env["MB_S1_PARALLEL"] == "2" and env["MB_S1_STATS"] == str(stats)
    assert "MB_S1_OPTION_SEED" not in env
    assert str(R.ROOT / "python") in env["PYTHONPATH"].split(R.os.pathsep)
    assert "MB_S1_MODEL" not in R.run_env(None, R.DEFAULT_URL, stats)


# ---------------------------------------------------------------- run: refusals, exit codes and the run path

def no_subprocess(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("runcell must not start")

    monkeypatch.setattr(R.subprocess, "run", refuse)


def fake_subprocess(monkeypatch, cell_id: str, returncode: int = 0, status: str = "complete", release: str = R.RELEASE) -> dict:
    """Replaces subprocess.run: the fake runcell writes a complete run directory (or one with the given status) and returns the
    given exit code; the arguments and the environment it was started with are recorded."""
    seen = {}

    def run(argv, cwd, env):
        seen.update(argv=argv, cwd=cwd, env=env)
        write_fake_run(Path(argv[argv.index("--out") + 1]), cell_id, status, release)
        return subprocess.CompletedProcess(argv, returncode)

    monkeypatch.setattr(R.subprocess, "run", run)
    return seen


def test_run_refuses_a_non_empty_out_dir_before_running_anything(tmp_path, monkeypatch, capsys):
    (tmp_path / "lexical").mkdir()
    (tmp_path / "lexical" / "cell.json").write_text("{}", encoding="utf-8")
    no_subprocess(monkeypatch)
    assert R.main(["run", "lexical", "--out-root", str(tmp_path)]) == 2
    assert "not empty" in capsys.readouterr().err


def test_a_second_cell_with_a_non_empty_dir_refuses_the_whole_run(tmp_path, monkeypatch):
    (tmp_path / "heuristic").mkdir()
    (tmp_path / "heuristic" / "cell.json").write_text("{}", encoding="utf-8")
    no_subprocess(monkeypatch)
    assert R.main(["run", "lexical", "heuristic", "--out-root", str(tmp_path)]) == 2
    assert not (tmp_path / "lexical").exists()  # the first cell was not started either


def test_run_refuses_a_stats_file_left_by_an_earlier_run(tmp_path, monkeypatch):
    (tmp_path / "lexical.meta").mkdir()
    (tmp_path / "lexical.meta" / "child_stats.json").write_text("{}", encoding="utf-8")
    no_subprocess(monkeypatch)
    assert R.main(["run", "lexical", "--out-root", str(tmp_path)]) == 2


def test_run_refuses_a_cell_without_published_predictions(tmp_path, capsys):
    assert R.main(["run", "empty", "--out-root", str(tmp_path / "out")]) == 2
    assert "no published predictions" in capsys.readouterr().err


def test_run_refuses_a_duplicate_id_before_running(tmp_path, monkeypatch):
    no_subprocess(monkeypatch)
    assert R.main(["run", "lexical", "lexical", "--out-root", str(tmp_path)]) == 2


def test_run_refuses_a_model_cell_without_its_model_tag_before_running(tmp_path, monkeypatch):
    doc = R.published_doc("d3_27b-q8_0", R.RELEASE)
    doc["child_stats"] = None
    monkeypatch.setattr(R, "published_doc", lambda cell_id, release: doc)
    no_subprocess(monkeypatch)
    assert R.main(["run", "lexical", "d3_27b-q8_0", "--out-root", str(tmp_path)]) == 2
    assert not (tmp_path / "lexical").exists()


def test_run_refuses_a_child_interpreter_that_cannot_be_found(tmp_path, monkeypatch, capsys):
    doc = R.published_doc("lexical", R.RELEASE)
    doc["cell"]["local_arg"] = ["no-such-child-binary-xyz", "-m", "mentat_bench.children.lexical_child"]
    monkeypatch.setattr(R, "published_doc", lambda cell_id, release: doc)
    no_subprocess(monkeypatch)
    assert R.main(["run", "lexical", "--out-root", str(tmp_path)]) == 2
    assert "cannot be found" in capsys.readouterr().err


def test_run_success_path_exits_0_and_starts_the_recorded_command(tmp_path, monkeypatch):
    rows = published_rows("lexical")
    monkeypatch.setattr(R, "run_rows", lambda run_dir: rows)
    seen = fake_subprocess(monkeypatch, "lexical")
    assert R.main(["run", "lexical", "--out-root", str(tmp_path)]) == 0
    assert f"--local-arg={sys.executable}" in seen["argv"]
    assert "--local-arg=python.exe" not in seen["argv"]
    assert seen["cwd"] == R.ROOT
    assert not any(k.startswith("MB_S1_") for k in seen["env"])  # a non-model cell gets no Ollama settings
    report = json.loads((tmp_path / "lexical.reproduce" / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "exact"


def test_run_exit_code_follows_the_runcell_code_and_the_verdict(tmp_path, monkeypatch):
    rows = published_rows("lexical")
    monkeypatch.setattr(R, "run_rows", lambda run_dir: rows)
    fake_subprocess(monkeypatch, "lexical", returncode=1)  # runcell failed although the predictions match
    assert R.main(["run", "lexical", "--out-root", str(tmp_path / "a")]) == 1
    fake_subprocess(monkeypatch, "lexical", returncode=0, status="incomplete")  # runcell ran but the cell did not complete
    assert R.main(["run", "lexical", "--out-root", str(tmp_path / "b")]) == 1


def test_run_passes_the_model_settings_to_a_model_cell(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "run_rows", lambda run_dir: published_rows("d3_27b-q8_0"))
    seen = fake_subprocess(monkeypatch, "d3_27b-q8_0")
    assert R.main(["run", "d3_27b-q8_0", "--out-root", str(tmp_path), "--url", "http://127.0.0.1:9999"]) == 0
    env = seen["env"]
    assert env["MB_S1_MODEL"] == "d3:27b-q8_0" and env["MB_S1_URL"] == "http://127.0.0.1:9999"
    assert env["MB_S1_PARALLEL"] == "2" and env["MB_S1_STATS"] == str(tmp_path / "d3_27b-q8_0.meta" / "child_stats.json")


def test_compare_returns_0_for_an_exact_run(tmp_path, monkeypatch, capsys):
    rows = published_rows("lexical")
    monkeypatch.setattr(R, "run_rows", lambda run_dir: rows)
    run = fake_run(tmp_path, "lexical")
    assert R.main(["compare", str(run), "--id", "lexical"]) == 0
    assert capsys.readouterr().out.startswith("lexical: exact")


# ---------------------------------------------------------------- releases: carried baselines, analysis releases, unknown ones

def index_with(tmp_path: Path, monkeypatch, *entries: dict) -> None:
    """A copy of results/index.json with extra release entries. R.INDEX points at the copy for this test only."""
    index = json.loads(R.INDEX.read_text(encoding="utf-8"))
    index["versions"].extend(entries)
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(R, "INDEX", path)


def test_a_v020_baseline_resolves_to_the_v010_cell_and_run_card():
    assert R.holding_release("v0.2.0", "lexical") == "v0.1.0"
    assert R.holding_release("v0.2.0", "d3_27b-q8_0") == "v0.2.0"  # a model measured in v0.2.0 holds its own cell
    assert R.published_doc("lexical", "v0.2.0") == R.published_doc("lexical", "v0.1.0")
    assert R.runcard("v0.2.0", "lexical") == R.runcard("v0.1.0", "lexical")
    assert R.holding_release("v0.1.1", "lexical") == "v0.1.0"  # v0.1.1's cells_dir is v0.1.0's: it holds no cells of its own


def test_v022_resolves_a_model_to_its_v020_cell_through_v021():
    """v0.2.2 is based on v0.2.1, which is based on v0.2.0: the two analysis releases hold no cells, so the model is v0.2.0's cell."""
    assert R.holding_release("v0.2.2", "d3_27b-q8_0") == "v0.2.0"
    assert R.published_doc("d3_27b-q8_0", "v0.2.2") == R.published_doc("d3_27b-q8_0", "v0.2.0")
    assert R.runcard("v0.2.2", "d3_27b-q8_0") == R.runcard("v0.2.0", "d3_27b-q8_0")


def test_a_v020_model_cell_runs_its_recorded_command_and_settings(tmp_path, monkeypatch):
    cell_id = "d1-omni_600m-q8_0"
    doc = R.published_doc(cell_id, "v0.2.0")
    assert doc["predictions"] == "results/v0.2.0/predictions/d1-omni_600m-q8_0.jsonl.gz"
    assert R.model_tag(cell_id, doc) == "d1-omni:600m-q8_0"
    monkeypatch.setattr(R, "run_rows", lambda run_dir: published_rows(cell_id, "v0.2.0"))
    seen = fake_subprocess(monkeypatch, cell_id, release="v0.2.0")
    assert R.main(["run", cell_id, "--release", "v0.2.0", "--out-root", str(tmp_path)]) == 0
    argv = seen["argv"]
    assert argv[argv.index("--fixture") + 1] == doc["cell"]["fixture_path"] == "fixtures/mentat_hard_test"
    assert argv[argv.index("--adapter") + 1] == doc["cell"]["adapter"]
    assert argv[argv.index("--profile") + 1] == doc["cell"]["profile"]
    assert seen["env"]["MB_S1_MODEL"] == "d1-omni:600m-q8_0" and seen["env"]["MB_S1_PARALLEL"] == "2"
    report = json.loads((tmp_path / f"{cell_id}.reproduce" / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "exact" and report["release"] == "v0.2.0" and report["measured_in"] == "v0.2.0"
    assert report["builder"] == "results/v0.2.0/build_bundle.py"
    assert report["rescoring_builder"] == "results/v0.1.0/build_bundle.py"


def test_a_v020_baseline_is_run_and_compared_with_the_v010_measurement(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "run_rows", lambda run_dir: published_rows("lexical", "v0.2.0"))
    seen = fake_subprocess(monkeypatch, "lexical", release="v0.2.0")
    assert R.main(["run", "lexical", "--release", "v0.2.0", "--out-root", str(tmp_path)]) == 0
    assert not any(k.startswith("MB_S1_") for k in seen["env"])  # a baseline is not a model cell
    report = json.loads((tmp_path / "lexical.reproduce" / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "exact" and report["release"] == "v0.2.0" and report["measured_in"] == "v0.1.0"


def test_an_unknown_release_or_cell_is_refused_before_anything_runs(tmp_path, monkeypatch, capsys):
    no_subprocess(monkeypatch)
    assert R.main(["run", "lexical", "--release", "v9.9.9", "--out-root", str(tmp_path)]) == 2
    assert "no release 'v9.9.9' in results/index.json" in capsys.readouterr().err
    assert R.main(["compare", str(tmp_path), "--id", "lexical", "--release", "v9.9.9"]) == 2
    assert R.main(["run", "no-such-cell", "--release", "v0.2.0", "--out-root", str(tmp_path)]) == 2
    assert "no published cell 'no-such-cell'" in capsys.readouterr().err
    assert not (tmp_path / "lexical").exists() and not (tmp_path / "no-such-cell").exists()


def test_an_analysis_release_resolves_through_its_base_to_the_cell(tmp_path, monkeypatch):
    index_with(tmp_path, monkeypatch, {"version": "v0.9.1", "based_on": "v0.2.0"},
               {"version": "v0.9.2", "based_on": "v0.9.1"})  # a synthetic analysis chain: v0.9.2 -> v0.9.1 -> v0.2.0
    assert R.holding_release("v0.9.1", "d3_27b-q8_0") == "v0.2.0"
    assert R.holding_release("v0.9.2", "d3_27b-q8_0") == "v0.2.0"
    assert R.holding_release("v0.9.2", "lexical") == "v0.1.0"  # v0.2.0 carries the baseline from v0.1.0
    assert R.published_doc("d3_27b-q8_0", "v0.9.1") == R.published_doc("d3_27b-q8_0", "v0.2.0")
    monkeypatch.setattr(R, "run_rows", lambda run_dir: published_rows("d3_27b-q8_0", "v0.2.0"))
    run = fake_run(tmp_path / "runs", "d3_27b-q8_0", release="v0.9.1")
    report, _ = R.compare_run(run, "d3_27b-q8_0", "v0.9.1")
    assert report["verdict"] == "exact" and report["release"] == "v0.9.1" and report["measured_in"] == "v0.2.0"


def fake_results(tmp_path: Path, monkeypatch, entries: list[dict], own: dict[str, list[str]]) -> None:
    """A temporary repository: copies of the v0.1.0 and v0.2.0 cells, an index with extra entries, and own cells (copies of a model
    cell, one per id) in the cells directory of each release named in own. R.ROOT and R.INDEX point at it for this test only."""
    root = tmp_path / "repo"
    for release in ("v0.1.0", "v0.2.0"):
        shutil.copytree(ROOT / "results" / release / "cells", root / "results" / release / "cells")
    index = json.loads(R.INDEX.read_text(encoding="utf-8"))
    index["versions"].extend(entries)
    for release, ids in own.items():
        entry = next(e for e in index["versions"] if e["version"] == release)
        cells_dir = root / entry.get("cells_dir", f"results/{release}/cells")
        cells_dir.mkdir(parents=True, exist_ok=True)
        for cell_id in ids:
            shutil.copy(ROOT / "results" / "v0.2.0" / "cells" / "d3_27b-q8_0.json", cells_dir / f"{cell_id}.json")
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(R, "ROOT", root)
    monkeypatch.setattr(R, "INDEX", root / "index.json")


def test_an_analysis_release_may_not_hold_a_cell_its_base_holds(tmp_path, monkeypatch):
    fake_results(tmp_path, monkeypatch, [{"version": "v0.9.3", "based_on": "v0.2.0"}],
                 {"v0.9.3": ["d3_27b-q8_0", "synthetic_new"]})
    with pytest.raises(R.UsageError, match="may not hold a cell that its base holds"):
        R.holding_release("v0.9.3", "d3_27b-q8_0")
    assert R.holding_release("v0.9.3", "synthetic_new") == "v0.9.3"  # a cell the base does not hold is the analysis release's own
    no_subprocess(monkeypatch)
    assert R.main(["run", "d3_27b-q8_0", "--release", "v0.9.3", "--out-root", str(tmp_path / "out")]) == 2


def test_a_record_from_release_keeps_its_own_cell_over_the_carried_one(tmp_path, monkeypatch):
    fake_results(tmp_path, monkeypatch, [{"version": "v0.9.5", "record_from": "v0.2.0"}], {"v0.9.5": ["d3_27b-q8_0"]})
    assert R.holding_release("v0.9.5", "d3_27b-q8_0") == "v0.9.5"


def test_the_cells_dir_of_an_index_entry_is_read(tmp_path, monkeypatch):
    fake_results(tmp_path, monkeypatch, [{"version": "v0.9.8", "cells_dir": "results/v0.9.8/alt-cells"}],
                 {"v0.9.8": ["synthetic_new"]})
    assert R.holding_release("v0.9.8", "synthetic_new") == "v0.9.8"
    assert R.published_doc("synthetic_new", "v0.9.8")["kind"] == "system_one_model"


def test_a_model_absent_from_a_record_from_release_is_not_carried(tmp_path, monkeypatch):
    index_with(tmp_path, monkeypatch, {"version": "v0.9.6", "record_from": "v0.1.0"})
    assert R.holding_release("v0.9.6", "lexical") == "v0.1.0"  # a baseline is carried
    with pytest.raises(R.UsageError, match="no published cell 'd3_27b-q8_0'"):
        R.holding_release("v0.9.6", "d3_27b-q8_0")  # a model is not


def test_a_model_added_after_v010_is_refused_under_v010(tmp_path, monkeypatch, capsys):
    no_subprocess(monkeypatch)
    assert R.main(["run", "kev_4b-q8_0", "--out-root", str(tmp_path)]) == 2
    assert "no published cell 'kev_4b-q8_0' in results/v0.1.0/cells" in capsys.readouterr().err


def test_an_unknown_release_in_the_middle_of_a_chain_is_refused(tmp_path, monkeypatch):
    index_with(tmp_path, monkeypatch, {"version": "v0.9.7", "record_from": "v9.9.9"})
    with pytest.raises(R.UsageError) as err:
        R.holding_release("v0.9.7", "lexical")
    assert str(err.value) == "no release 'v9.9.9' in results/index.json (reached through v0.9.7 -> v9.9.9)"


def test_a_cycle_of_release_entries_is_refused_not_looped(tmp_path, monkeypatch):
    index_with(tmp_path, monkeypatch, {"version": "vA", "based_on": "vB"}, {"version": "vB", "based_on": "vA"})
    with pytest.raises(R.UsageError) as err:
        R.holding_release("vA", "lexical")
    assert str(err.value) == "no published cell 'lexical' in results/vA/cells or results/vB/cells (followed vA -> vB)"


def test_a_run_whose_rows_cannot_be_read_drops_an_earlier_predictions_file(tmp_path, monkeypatch):
    def unreadable(run_dir):
        raise ValueError("unreadable decisions")

    monkeypatch.setattr(R, "run_rows", unreadable)
    run = fake_run(tmp_path, "lexical")
    stale = tmp_path / "lexical.reproduce" / "predictions.jsonl.gz"
    stale.parent.mkdir()
    stale.write_bytes(b"stale")
    report, _ = R.compare_run(run, "lexical")
    assert report["verdict"] == "incomplete" and not stale.exists()


def test_compare_names_the_release_that_measured_a_carried_cell(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(R, "run_rows", lambda run_dir: published_rows("lexical", "v0.2.0"))
    run = fake_run(tmp_path, "lexical", release="v0.2.0")
    assert R.main(["compare", str(run), "--id", "lexical", "--release", "v0.2.0"]) == 0
    assert capsys.readouterr().out.startswith("lexical: exact (measured in v0.1.0)")


# ---------------------------------------------------------------- the committed bundle still builds

def test_bundle_build_check_passes():
    result = subprocess.run([sys.executable, "results/v0.1.1/build_bundle.py", "build", "--check"], cwd=ROOT,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr

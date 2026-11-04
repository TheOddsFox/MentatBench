"""Builds the transfer pilot's 19 runs: the 17 models and the two baselines (PREREG.md, Model runs; the approved plan, Step 2).

runcell refuses the pilot fixture, which has no queries and no gold answers, so a run is runcell's build step alone, with the argv
runcell issues for the same cell: the cell's build flags, with --candidates set to the pilot's candidates.jsonl. The environment is
tools/reproduce.py's run_env, plus MB_ENGINE. The Ollama server is started by the operator, who gives its URL here.

usage, from the repository root:
  python docs/audits/v0.2.0-transfer-pilot/run_models.py plan
  python docs/audits/v0.2.0-transfer-pilot/run_models.py run ID [ID ...] [--url http://127.0.0.1:PORT] [--retry]
  python docs/audits/v0.2.0-transfer-pilot/run_models.py collect

plan prints the argv of the 19 runs, with local paths named. run builds each ID in turn. The first attempt of an ID goes into a new folder
mb-runs/transfer-pilot/runs/<ID>, and a later attempt into runs/<ID>.attempt<k>. A run whose latest record exited 0 with status complete
is refused unless --retry is given. Each attempt writes one published record, runs/<ID>.attempt<k>.json, holding ids, placeholders for
local paths, hashes, the model tag and digest, the floor, the exit code, the status, and counts: no text. collect merges the records
into runs.json.
Exit codes: 0 every run exited 0; 1 a build exited non-zero (the others still run); 2 a refusal before any run started; 3 an error
after the runs started (the records written so far stay).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import analyse as AN  # the audit's helpers; importing it puts the repository's python package and tools on sys.path
from mentat_bench import decision_scoring as DS
from mentat_bench.harness import fixed

HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "runs"  # one published record per attempt: runs/<id>.attempt<k>.json
RUNS_JSON = HERE / "runs.json"  # every record, merged by collect
RECORD_SCHEMA = "mentat-bench-transfer-run/1"
RUNS_SCHEMA = "mentat-bench-transfer-runs/1"
LOCAL_RUNS = AN.LOCAL / "runs"  # outside the repository: the decisions stay local
LOCAL_FIXTURE = AN.LOCAL / "fixture"
ENGINE = AN.ROOT / "target" / "release" / ("mb-engine.exe" if os.name == "nt" else "mb-engine")  # as python/mentat_bench/engine.py
PINS = AN.ROOT / "python" / "mentat_bench" / "children" / "ollama_models.json"
MODEL_CELLS = AN.ROOT / "results" / "v0.2.0" / "cells"
BASELINE_CELLS = AN.ROOT / "results" / "v0.1.0" / "cells"
# The code a build runs through, hashed into each record besides the cell, the child and the profile (and adapters/*.py).
PROVENANCE_FILES = ("python/mentat_bench/harness/fixed.py", "python/mentat_bench/cli.py")
REFUSALS = (AN.AnalyseError, AN.SC.Refused, OSError, ValueError)  # R.UsageError is named in main (reproduce.py's own error)

# tools/reproduce.py is not a package: its run_env, child_interpreter and model_tag are the ones the published cells were run with.
_spec = importlib.util.spec_from_file_location("reproduce_tool", AN.ROOT / "tools" / "reproduce.py")
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


def model_ids() -> list[str]:
    ids = [m["id"] for m in json.loads(AN.LEADERBOARD.read_text(encoding="utf-8"))["models"]]
    if len(ids) != AN.MODELS_REQUIRED:
        raise AN.AnalyseError(f"the leaderboard has {len(ids)} models, not {AN.MODELS_REQUIRED}")
    return ids


def run_ids() -> list[str]:
    """The 19 runs: the 17 models in leaderboard order, then the two baselines."""
    return [*model_ids(), *AN.BASELINES]


def cell_path(run_id: str) -> Path:
    return (BASELINE_CELLS if run_id in AN.BASELINES else MODEL_CELLS) / f"{run_id}.json"


def load_cell(run_id: str) -> dict[str, Any]:
    return json.loads(cell_path(run_id).read_text(encoding="utf-8"))


def pins() -> dict[str, dict[str, Any]]:
    return json.loads(PINS.read_text(encoding="utf-8"))


def without_candidates(flags: list[str]) -> list[str]:
    """The build flags without their --candidates pair: runcell appends the candidates file itself, after them."""
    kept, rest = [], iter(flags)
    for flag in rest:
        if flag == "--candidates":
            next(rest)
        else:
            kept.append(flag)
    return kept


def argv_for(cell: dict[str, Any], run_dir: Path, fixture_dir: Path) -> list[str]:
    """The command of one run: the build step runcell.run_cell issues for this cell (its argument order and str(Path) forms), with
    the pilot's corpus and candidates and the run's own --out. Pure: nothing is run or written."""
    doc = cell["cell"]
    local = [R.child_interpreter(doc["local_arg"][0]), *doc["local_arg"][1:]]
    return [sys.executable, "-m", "mentat_bench", "build", "--adapter", doc["adapter"],
            "--corpus", str(Path(fixture_dir) / "corpus.jsonl"), "--out", str(Path(run_dir) / "submission"),
            "--profile", str(Path(doc["profile"])), "--keep-work", *without_candidates(doc["build_flags"]),
            "--candidates", str(Path(fixture_dir) / "candidates.jsonl"), *[f"--local-arg={arg}" for arg in local]]


def env_for(cell: dict[str, Any], url: str | None, stats: Path) -> dict[str, str]:
    """The environment of one run: reproduce's run_env (every inherited MB_S1_ setting removed, the model's own when it is a model) and
    the engine binary. MB_S1_ALLOW_UNPINNED is never set."""
    env = R.run_env(R.model_tag(cell["id"], cell), url, Path(stats))
    env["MB_ENGINE"] = str(ENGINE)
    return env


def floor_of(cell: dict[str, Any]) -> float | None:
    flags = cell["cell"]["build_flags"]
    return float(flags[flags.index("--relation-floor") + 1]) if "--relation-floor" in flags else None


def redact(argv: list[str], fixture_dir: Path, run_dir: Path) -> list[str]:
    """The argv with its local paths named, so that a printed or published argv holds no local path."""
    names = ((sys.executable, "<python>"), (str(Path(fixture_dir)), "<fixture>"), (str(Path(run_dir)), "<run>"))
    out = []
    for arg in argv:
        for path, name in names:
            arg = arg.replace(path, name)
        out.append(arg)
    return out


def engine_sha() -> str:
    return AN.sha_file(ENGINE)


def cell_engine_sha(cell: dict[str, Any]) -> str | None:
    """The engine sha256 the cell recorded when it ran (its runtime block), or None."""
    return ((cell["cell"].get("runtime") or {}).get("engine") or {}).get("sha256")


def git_state() -> dict[str, Any]:
    """The repository's HEAD and whether its work tree is dirty (None when git cannot tell)."""
    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], cwd=AN.ROOT, capture_output=True, text=True, check=False)
        except OSError:
            return None
        return done.stdout if done.returncode == 0 else None
    head, status = git("rev-parse", "HEAD"), git("status", "--porcelain")
    return {"git_head": head.strip() if head else None, "git_dirty": None if status is None else bool(status.strip())}


def provenance(cell: dict[str, Any], run_id: str, git: dict[str, Any]) -> dict[str, Any]:
    """The sha256 of each file a run's build depends on: its cell, its child module, the harness and cli, every adapter, and the profile."""
    local = cell["cell"]["local_arg"]
    module = local[local.index("-m") + 1]  # mentat_bench.children.systemone_child
    names = [cell_path(run_id).relative_to(AN.ROOT).as_posix(), f"python/{module.replace('.', '/')}.py", *PROVENANCE_FILES,
             cell["cell"]["profile"]]
    names += sorted(p.relative_to(AN.ROOT).as_posix() for p in (AN.ROOT / "python" / "mentat_bench" / "adapters").glob("*.py"))
    return {**git, "python": platform.python_version(), "files": {name: AN.sha_file(AN.ROOT / name) for name in names}}


def floor_and_model(cell: dict[str, Any]) -> tuple[float | None, dict[str, str] | None]:
    tag = R.model_tag(cell["id"], cell)
    return floor_of(cell), None if tag is None else {"tag": tag, "digest": pins()[tag]["digest"]}


def decision_log(run_dir: Path) -> dict[str, Any] | None:
    """Decision rows by op, error rows by op, and the log's torn and identical-duplicate counts; None when there is no log.
    A log that cannot be read raises (its exception is recorded by build_outcome)."""
    path = Path(run_dir) / "submission.build" / "decisions.jsonl"
    if not path.is_file():
        return None
    rows, read = DS.read_decisions(path)
    return {"decisions": dict(Counter(row.get("op") for row in rows)),
            "error_rows": dict(Counter(row.get("op") for row in rows if row.get("error") is not None)), **read}


def build_outcome(run_dir: Path, code: int) -> dict[str, Any]:
    """The status and partial reasons of the build (runcell's own reading of its summary), the decisions file's sha256 and size, and
    the decision log's counts. A read that fails is recorded by its exception type alone, never its message, so a record is always written."""
    out: dict[str, Any] = {"status": "incomplete", "partial": [], "decisions_file": None, "decision_log": None, "log_error": None}
    try:
        if code == 0:  # a failed build has no summary: its cell is incomplete, as in runcell
            summary = json.loads((Path(run_dir) / "build.stdout.txt").read_text(encoding="utf-8"))
            out["status"], out["partial"] = fixed.build_status(summary), fixed.partial_reasons(summary)
        path = Path(run_dir) / "submission.build" / "decisions.jsonl"
        if path.is_file():
            out["decisions_file"] = {"sha256": AN.sha_file(path), "bytes": path.stat().st_size}
        out["decision_log"] = decision_log(run_dir)
    except Exception as exc:  # noqa: BLE001 - recorded by type; the record is written whatever the log holds
        out["log_error"] = type(exc).__name__
    return out


def verify_fixture(fixture_dir: Path) -> None:
    """The fixture the runs read is the published one: its corpus and candidates match fixture_manifest.json."""
    manifest = json.loads((HERE / "fixture_manifest.json").read_text(encoding="utf-8"))
    expected = {"corpus.jsonl": manifest["corpus_sha256"], "candidates.jsonl": manifest["files"]["candidates.jsonl"]}
    for name, digest in expected.items():
        path = Path(fixture_dir) / name
        if not path.is_file() or AN.sha_file(path) != digest:
            raise AN.AnalyseError(f"{path} does not match fixture_manifest.json")


def records(records_dir: Path) -> list[dict[str, Any]]:
    """Every attempt record, by run id and then attempt."""
    if not Path(records_dir).is_dir():
        return []
    found = [json.loads(path.read_text(encoding="utf-8")) for path in Path(records_dir).glob("*.attempt*.json")]
    return sorted(found, key=lambda rec: (rec["id"], rec["attempt"]))


def latest_records(records_dir: Path) -> dict[str, dict[str, Any]]:
    """The record of each run's latest attempt."""
    latest: dict[str, dict[str, Any]] = {}
    for rec in records(records_dir):
        latest[rec["id"]] = rec  # sorted by attempt, so the last one wins
    return latest


def collect(records_dir: Path, runs_json: Path) -> dict[str, Any]:
    """Merges every attempt record into runs.json (the published list of runs)."""
    doc = {"schema": RUNS_SCHEMA, "runs": records(records_dir)}
    AN.write_json(Path(runs_json), doc)
    return doc


def prepare(ids: list[str], url: str | None, retry: bool, *, runs_root: Path, fixture_dir: Path, records_dir: Path) -> dict[str, Any]:
    """Every refusal, checked before the first run starts: the plan of each run (its cell, attempt and folder), the engine's sha256 (printed),
    and the repository's git state. Nothing is started, and nothing is written, on a refusal."""
    AN.SC.check_frozen()
    if not ids:
        raise AN.AnalyseError("no run is named")
    unknown = sorted(set(ids) - set(run_ids()))
    if unknown:
        raise AN.AnalyseError(f"not one of the 19 runs: {', '.join(unknown)}")
    if len(set(ids)) != len(ids):
        raise AN.AnalyseError("a run is named more than once")
    runs_root = Path(runs_root)
    AN.refuse_repo_path(runs_root, "the run folder")
    if not ENGINE.is_file():
        raise AN.AnalyseError(f"the engine {ENGINE} is not built")
    verify_fixture(fixture_dir)
    latest = latest_records(records_dir)
    plans = []
    for run_id in ids:
        cell = load_cell(run_id)
        tag = R.model_tag(run_id, cell)
        if tag is not None:
            if not url:
                raise AN.AnalyseError(f"{run_id} is a model run and needs --url (its Ollama server)")
            pin = pins().get(tag)
            if pin is None or pin["digest"] != cell["model"]["revision"]:
                raise AN.AnalyseError(f"{run_id}: the pinned digest of {tag} differs from its published cell")
        prev = latest.get(run_id)
        if prev is not None and prev["exit_code"] == 0 and prev["status"] == "complete" and not retry:
            raise AN.AnalyseError(f"{run_id} is already done (exit 0, status complete): another run of it needs --retry")
        attempt = prev["attempt"] + 1 if prev is not None else 1
        folder = runs_root / (run_id if attempt == 1 else f"{run_id}.attempt{attempt}")
        AN.refuse_repo_path(folder, "a run folder")
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise AN.AnalyseError(f"{folder} exists and is not empty; a run needs a new folder")
        plans.append({"id": run_id, "cell": cell, "attempt": attempt, "folder": folder})
    sha = engine_sha()
    print(f"engine {ENGINE.name} sha256 {sha}")
    return {"plans": plans, "engine_sha256": sha, **git_state()}


def run_one(plan: dict[str, Any], url: str | None, fixture_dir: Path, records_dir: Path, prepared: dict[str, Any]) -> dict[str, Any]:
    """One attempt: the build's command, its output in its folder, and its record (written even when the build or its log fails)."""
    run_id, cell, attempt, run_dir = plan["id"], plan["cell"], plan["attempt"], plan["folder"]
    run_dir.mkdir(parents=True, exist_ok=True)
    argv = argv_for(cell, run_dir, fixture_dir)
    git = {"git_head": prepared["git_head"], "git_dirty": prepared["git_dirty"]}
    prov = provenance(cell, run_id, git)
    floor, model = floor_and_model(cell)
    t0 = time.monotonic()
    with open(run_dir / "build.stdout.txt", "wb") as out, open(run_dir / "build.stderr.txt", "wb") as err:
        code = subprocess.run(argv, cwd=AN.ROOT, env=env_for(cell, url, run_dir / "child_stats.json"), stdout=out, stderr=err,
                              check=False).returncode
    recorded = cell_engine_sha(cell)
    engine = {"name": ENGINE.name, "sha256": prepared["engine_sha256"],
              "matches_cell": None if recorded is None else prepared["engine_sha256"] == recorded}
    record = {"schema": RECORD_SCHEMA, "id": run_id, "attempt": attempt, "folder": run_dir.name, "kind": cell["kind"],
              "argv": redact(argv, fixture_dir, run_dir), "model": model, "floor": floor, "exit_code": code,
              "duration_s": round(time.monotonic() - t0, 3), "engine": engine, "provenance": prov,
              **build_outcome(run_dir, code)}
    AN.write_json(Path(records_dir) / f"{run_id}.attempt{attempt}.json", record)
    return record


def execute(prepared: dict[str, Any], url: str | None, fixture_dir: Path, records_dir: Path) -> list[dict[str, Any]]:
    """Runs the prepared plans in turn; a failed build does not stop the others."""
    return [run_one(plan, url, fixture_dir, records_dir, prepared) for plan in prepared["plans"]]


def run(ids: list[str], url: str | None = None, retry: bool = False, *, runs_root: Path, fixture_dir: Path,
        records_dir: Path) -> list[dict[str, Any]]:
    """prepare, then execute: the refusals come first, and nothing starts on one."""
    return execute(prepare(ids, url, retry, runs_root=runs_root, fixture_dir=fixture_dir, records_dir=records_dir), url, fixture_dir,
                   records_dir)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan", help="print the argv of the 19 runs, with local paths named (nothing is run)")
    r = sub.add_parser("run", help="build the named runs, each into a new folder")
    r.add_argument("ids", nargs="+", metavar="ID")
    r.add_argument("--url", help="the Ollama server of a model run, e.g. http://127.0.0.1:11435 (the baselines need none)")
    r.add_argument("--retry", action="store_true", help="run again an ID whose latest attempt is already done (exit 0, complete)")
    sub.add_parser("collect", help="merge the attempt records into runs.json")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        print(f"wrote {len(collect(RUNS_DIR, RUNS_JSON)['runs'])} records to {RUNS_JSON.name}")
        return 0
    if a.cmd == "plan":
        try:
            for run_id in run_ids():
                folder = LOCAL_RUNS / run_id
                print(f"{run_id}: {subprocess.list2cmdline(redact(argv_for(load_cell(run_id), folder, LOCAL_FIXTURE), LOCAL_FIXTURE, folder))}")
        except REFUSALS as exc:
            print(f"run_models: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        return 0
    try:
        prepared = prepare(a.ids, a.url, a.retry, runs_root=LOCAL_RUNS, fixture_dir=LOCAL_FIXTURE, records_dir=RUNS_DIR)
    except (*REFUSALS, R.UsageError) as exc:
        print(f"run_models: refused, nothing ran: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    try:
        done = execute(prepared, a.url, LOCAL_FIXTURE, RUNS_DIR)
    except Exception as exc:  # noqa: BLE001 - an error after the runs started: the records written so far stay
        print(f"run_models: error after the runs started: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    for rec in done:
        print(f"{rec['id']} attempt {rec['attempt']}: exit {rec['exit_code']}, status {rec['status']}, {rec['duration_s']} s")
    return 0 if all(rec["exit_code"] == 0 for rec in done) else 1


if __name__ == "__main__":
    sys.exit(main())

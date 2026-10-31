"""Run card for one scored cell: python -m mentat_bench.runcard --cell DIR [--out PATH]

Writes runcard.json beside the cell's cell.json (or to --out). The card records what a reader needs to reproduce or
audit the cell: hardware, package versions, the engine binary (sha256, mtime), the git commit and dirty flag of the
repository, the resource profile (content and sha256), the build policy and model block, the fixture (name, version,
and the sha256 of every regular file under its root), the submission digest and seal.json hash, usage totals, the score
blocks (repaired and strict Q_N, unweighted, weighted, anchor, active), the cell status, repair statistics and timestamps.

Run-time facts. runcell stores the facts that exist only at run time in cell.json ("runtime", written by capture_facts
before the build starts): the engine sha256, the profile sha256, the fixture file hashes, the repo commit and dirty flag,
the package versions, each GPU's index, name, UUID and memory, and CUDA_VISIBLE_DEVICES and CUDA_DEVICE_ORDER. A card takes
these from the cell when they are there (captured_at: run_time) and otherwise from the live machine (captured_at:
runcard_time). nvidia-smi lists the physical GPUs, whatever CUDA_VISIBLE_DEVICES says: the environment is recorded
beside the list, and it is what decides which of them a child process can use. A missing input is null; nothing is
estimated. Paths are repository-relative when inside the repository, else "<external>/NAME": a card never holds a
local directory. Nothing here runs the engine.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import engine, report, seal
from .gateway import usage_totals
from .scoring import FAMILIES

REPO = Path(__file__).resolve().parents[2]
EXIT_USAGE = 2
SCHEMA = "mentat-bench-runcard/1"
EXTERNAL = "<external>"
MODEL_KEYS = ("id", "revision", "dtype", "calibrate", "prefix_cache")
PACKAGES = ("torch", "transformers")  # reported when importable (found without importing them)
RUNCARD_FILE = "runcard.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path | str) -> str:
    """The repository-relative POSIX path of a file inside the repository; "<external>/NAME" for any other file. Both sides are
    resolved (realpath), so a junction or symlink counts by where it points."""
    try:
        return Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(REPO))).as_posix()
    except ValueError:
        return f"{EXTERNAL}/{Path(os.path.abspath(path)).name}"


def cell_ref(run: Path) -> str:
    """The name of a cell in the cards and the tables: its repository path, or <external>/NAME#HASH outside the repository,
    where HASH is the first 8 hex digits of the sha256 of its realpath (two cells named alike in different directories are
    then different names, and never a silent match)."""
    text = rel(run)
    if not text.startswith(EXTERNAL + "/"):
        return text
    return f"{text}#{hashlib.sha256(os.path.realpath(run).encode('utf-8')).hexdigest()[:8]}"


def fixture_files(root: Path) -> dict[str, str]:
    """sha256 of every regular file under the fixture root, keyed by POSIX relative path (nothing is left out)."""
    return {path.relative_to(root).as_posix(): sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _optional(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _run(args: list[str], cwd: Path | None = None) -> str | None:
    """stdout of a command, stripped; None when it cannot run or exits non-zero."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=20, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended", ctypes.c_ulonglong)]


def _ram_gb() -> float | None:
    try:
        if os.name == "nt":
            status = _MemoryStatus()
            status.length = ctypes.sizeof(_MemoryStatus)
            if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return round(status.total_phys / 2**30, 2)
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 2**20, 2)
    except (OSError, ValueError):
        return None
    return None


def _cpu_model() -> str | None:
    if os.name == "nt":
        return os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or None
    if sys.platform == "darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def gpus() -> list[dict[str, Any]]:
    """Every physical GPU nvidia-smi reports: index, name, UUID, memory (MB) and driver. [] when nvidia-smi is absent or
    fails. Visibility to a child process is CUDA_VISIBLE_DEVICES (see cuda_environment), not this list."""
    exe = shutil.which("nvidia-smi")
    query = "--query-gpu=index,name,uuid,memory.total,driver_version"
    out = _run([exe, query, "--format=csv,noheader,nounits"]) if exe else None
    found = []
    for line in (out or "").splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) == 5:
            index = _number(parts[0])
            found.append({"index": int(index) if index is not None else None, "name": parts[1], "uuid": parts[2],
                          "memory_mb": _number(parts[3]), "driver": parts[4]})
    return found


def cuda_environment() -> dict[str, str | None]:
    return {"CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "CUDA_DEVICE_ORDER": os.environ.get("CUDA_DEVICE_ORDER")}


def hardware() -> dict[str, Any]:
    """The machine as it is now (CPU, RAM, OS, GPUs and the CUDA environment)."""
    found = gpus()
    return {"os": platform.platform(), "machine": platform.machine(), "cpu_model": _cpu_model(),
            "cpu_count": os.cpu_count(), "ram_gb": _ram_gb(), "gpus": found, "gpu_count": len(found),
            "cuda": cuda_environment()}


def _package_version(name: str) -> str | None:
    if importlib.util.find_spec(name) is None:  # found, not imported: torch is heavy
        return None
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def versions() -> dict[str, Any]:
    return {"python": platform.python_version(), **{name: _package_version(name) for name in PACKAGES}}


def engine_identity() -> dict[str, Any]:
    path = engine.engine_path()
    if not path.is_file():
        return {"path": rel(path), "sha256": None, "mtime_utc": None}
    return {"path": rel(path), "sha256": sha256_file(path), "mtime_utc": _iso(path.stat().st_mtime)}


def repo_identity(repo: Path = REPO) -> dict[str, Any]:
    status = _run(["git", "-C", str(repo), "status", "--porcelain"])
    return {"commit": _run(["git", "-C", str(repo), "rev-parse", "HEAD"]),
            "dirty": None if status is None else bool(status)}


def profile_identity(path: Path | None) -> dict[str, Any] | None:
    if path is None or not Path(path).is_file():
        return None
    raw = Path(path).read_bytes()
    return {"path": rel(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "content": json.loads(raw.decode("utf-8-sig"))}


def capture_facts(fixture: Path, profile: Path | None, files: dict[str, str] | None = None) -> dict[str, Any]:
    """The run-time facts of a cell: runcell stores them in cell.json before the build starts (see the module docstring).
    `files` is fixture_files(fixture) when the caller has it already (the fixture is not hashed again)."""
    profile_block = profile_identity(profile)
    return {
        "captured_at": "run_time", "captured_utc": _now(), "engine": engine_identity(),
        "profile_sha256": profile_block["sha256"] if profile_block else None,
        "fixture": {"path": rel(fixture), "files": files if files is not None else fixture_files(fixture) if fixture.is_dir() else None},
        "repo": repo_identity(), "versions": versions(), "gpus": gpus(), "cuda": cuda_environment(),
    }


def model_block(policy: dict[str, Any]) -> dict[str, Any]:
    model = policy.get("model") or {}
    return {"adapter": policy.get("adapter"), **{key: model.get(key) for key in MODEL_KEYS}}


def _sealed_at(submission: Path, digest: str) -> str | None:
    """The seal ledger's time for a digest. The ledger is seal.ledger_path (MB_SEAL_LEDGER, or beside the submission)."""
    try:
        lines = seal.ledger_path(submission).read_text(encoding="utf-8-sig").splitlines()
    except (OSError, seal.SealError):  # no ledger, or a MB_SEAL_LEDGER that cannot be a ledger: no time, nothing estimated
        return None
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("digest") == digest:
            return entry.get("sealed_at_utc")
    return None


def submission_block(run: Path) -> dict[str, Any]:
    sealed = _optional(run / "submission" / "seal.json")
    if not isinstance(sealed, dict):
        return {"digest": None, "seal_sha256": None, "sealed_at_utc": None, "sealed_files": None}
    digest = sealed.get("digest")
    return {"digest": digest, "seal_sha256": sha256_file(run / "submission" / "seal.json"),
            "sealed_at_utc": _sealed_at(run / "submission", digest) if isinstance(digest, str) else None,
            "sealed_files": len(sealed.get("files", {}))}


def _stamped(recorded: Any, live: Callable[[], Any]) -> dict[str, Any]:
    """A run-time block as recorded by runcell (captured_at run_time), else the live one (captured_at runcard_time)."""
    if isinstance(recorded, dict):
        return {**recorded, "captured_at": "run_time"}
    return {**live(), "captured_at": "runcard_time"}


def _fixture_block(cell: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    name = cell.get("fixture")
    path = Path(cell["fixture_path"]) if cell.get("fixture_path") else REPO / "fixtures" / str(name)
    if not path.is_dir():
        return {"name": name, "path": rel(path), "version": None, "files": None, "captured_at": "runcard_time"}
    meta = _optional(path / "fixture.json") or {}
    fixture_facts = runtime.get("fixture")
    recorded = fixture_facts.get("files") if isinstance(fixture_facts, dict) else None
    files = recorded if isinstance(recorded, dict) else fixture_files(path)
    return {"name": name, "path": rel(path), "version": meta.get("version"), "files": files,
            "captured_at": "run_time" if isinstance(recorded, dict) else "runcard_time"}


def _profile_block(cell: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any] | None:
    block = profile_identity(Path(cell["profile"]) if cell.get("profile") else None)
    if block is None:
        return None
    recorded = runtime.get("profile_sha256")
    if recorded is None:
        return {**block, "captured_at": "runcard_time"}
    return {**block, "sha256": recorded, "sha256_now": block["sha256"], "changed_since_run": recorded != block["sha256"],
            "captured_at": "run_time"}


def _hardware_block(runtime: dict[str, Any]) -> dict[str, Any]:
    live = hardware()
    if "gpus" not in runtime:
        return {**live, "gpus_captured_at": "runcard_time"}
    return {**live, "gpus": runtime["gpus"], "gpu_count": len(runtime["gpus"]), "cuda": runtime.get("cuda"),
            "gpus_captured_at": "run_time"}


def _scores(row: dict[str, Any]) -> dict[str, Any]:
    scores = {
        "Q_N_repaired": row["Q_N"], "Q_N_strict": row["strict_Q_N"], "base_repaired": row["base_repaired"],
        "families": {family: row[family] for family in FAMILIES},
        "exact_query_success": row["exact_query_success"], "false_entailment": row["false_entailment"],
        "false_refutation": row["false_refutation"], "weighted_Q_N": row["weighted_Q_N"],
        "anchor_Q_N": row["anchor_Q_N"], "active_Q_N": row["active_Q_N"],
        # the decision MCCs (owner decision 2026-10-29): the relation MCC is the headline of versions 1 and 2, the binding MCC
        # is secondary, and the group and reading MCCs are diagnostics; None when the cell has no decision log
        "decision_rel_mcc": row["decision_rel_mcc"], "decision_bind_mcc": row["decision_bind_mcc"],
        "decision_grp_mcc": row["decision_grp_mcc"], "decision_read_mcc": row["decision_read_mcc"],
    }
    if row["decision_headline_metric"] == "relation_group_mcc":  # version 3 and later (owner decision 2026-10-30); v1 and v2 cards keep their bytes
        scores.update(decision_headline_metric=row["decision_headline_metric"], decision_headline=row["decision_headline"])
    return scores


def runcard(run: Path) -> dict[str, Any]:
    """The run card of a cell directory. ValueError when the directory has no cell.json."""
    run = Path(run)
    cell = _optional(run / "cell.json")
    if not isinstance(cell, dict):
        raise ValueError(f"{run} has no readable cell.json: a run card describes a cell written by runcell or ceilings")
    runtime = cell.get("runtime") if isinstance(cell.get("runtime"), dict) else {}
    policy = _optional(run / "submission" / "build_policy.json") or {}
    build = _optional(run / "build_summary.json") or _optional(run / "ceiling_summary.json") or {}
    row = report.run_row(run)  # score, usage and status columns, as the report shows them
    usage = _optional(run / "submission" / "usage.json")
    submission = submission_block(run)
    return {
        "schema": SCHEMA,
        "cell": cell_ref(run),
        "generated_utc": _now(),
        "hardware": _hardware_block(runtime),
        "versions": _stamped(runtime.get("versions"), versions),
        "engine": _stamped(runtime.get("engine"), engine_identity),
        "repo": _stamped(runtime.get("repo"), repo_identity),
        "profile": _profile_block(cell, runtime),
        "build_policy": policy or None,
        "model": model_block(policy),
        "fixture": _fixture_block(cell, runtime),
        "submission": submission,
        "usage": usage_totals(usage) if isinstance(usage, dict) else None,
        "scores": _scores(row),
        "status": {"status": cell.get("status"), "partial": cell.get("partial"), "incomplete": cell.get("incomplete"),
                   "ceiling": cell.get("ceiling")},
        "repairs": {key: build.get(key) for key in (
            "base_repaired", "contradiction_rate", "relations_asked", "relations_dropped", "derived_dropped", "groups_dropped",
            "groups_dropped_pct", "groups_downgraded", "equiv_rejected", "relation_rejected", "group_errors",
            "group_scope_rejected", "resumed", "prior_calls")},
        "timestamps": {"cell_json_utc": _mtime(run / "cell.json"), "score_json_utc": _mtime(run / "score.json"),
                       "captured_utc": runtime.get("captured_utc"), "sealed_at_utc": submission["sealed_at_utc"]},
    }


def _mtime(path: Path) -> str | None:
    return _iso(path.stat().st_mtime) if path.is_file() else None


def write_runcard(run: Path, out: Path | None = None) -> Path:
    path = Path(out) if out else Path(run) / RUNCARD_FILE
    path.write_text(json.dumps(runcard(run), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.runcard", description=__doc__.splitlines()[0])
    parser.add_argument("--cell", required=True, type=Path, help="cell directory (holds cell.json)")
    parser.add_argument("--out", type=Path, help="where to write the card (default: CELL/runcard.json)")
    args = parser.parse_args(argv)
    try:
        path = write_runcard(args.cell, args.out)
    except (ValueError, OSError, KeyError) as exc:
        print(f"runcard: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

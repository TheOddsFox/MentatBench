"""Wrapper over the Rust `mb-engine` CLI (validate, eval, check). Binary: $MB_ENGINE, else target/release/mb-engine.

Engine exit status: 0 ok; 1 invalid submission or inconsistent base (JSON error list on stderr), raised as
EngineError; 2 usage/IO, any other status or a signal, raised as EngineInfraError (the engine did not reach a
verdict). Every run has a wall-clock budget: MB_ENGINE_TIMEOUT seconds when set, else
600 + queries * query_timeout_s. An overrun kills the engine's process tree and raises EngineError.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

from .adapters.proctree import kill_tree, start_kwargs
from .manifest import DEFAULT_PROFILE

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_TIMEOUT_ENV = "MB_ENGINE_TIMEOUT"
BASE_TIMEOUT_S = 600.0


class EngineError(RuntimeError):
    """The engine refused the input (exit 1), or the run overran its budget (returncode None). `codes` are the error
    codes of the engine's JSON error list: on stderr (eval, check), or on stdout (validate). Empty when there is none."""

    def __init__(self, message: str, returncode: int | None = None, codes: list[str] | None = None):
        super().__init__(message)
        self.returncode = returncode
        self.codes = list(codes or [])


class EngineInfraError(EngineError):
    """The engine did not reach a verdict: missing or non-executable binary, exit 2, a crash or a signal, or
    output that does not match the queries."""


def engine_path() -> Path:
    override = os.environ.get("MB_ENGINE")
    if override:
        return Path(override)
    exe = "mb-engine.exe" if os.name == "nt" else "mb-engine"
    return REPO_ROOT / "target" / "release" / exe


def engine_available() -> bool:
    return engine_path().is_file()


def _count_lines(path: str | Path) -> int:
    with open(path, encoding="utf-8-sig") as fh:
        return sum(1 for line in fh if line.strip())


def _env_budget_s() -> float | None:
    """MB_ENGINE_TIMEOUT in seconds, or None when unset. ValueError when set to anything but a positive number."""
    override = os.environ.get(ENGINE_TIMEOUT_ENV)
    if override is None:
        return None
    try:
        value = float(override)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{ENGINE_TIMEOUT_ENV} must be a finite positive number of seconds, got {override!r}")
    return value


def engine_budget_s(queries: str | Path, query_timeout_s: float = DEFAULT_PROFILE["query_timeout_s"]) -> float:
    """Wall-clock budget for one engine run over `queries`."""
    env = _env_budget_s()
    return env if env is not None else BASE_TIMEOUT_S + _count_lines(queries) * query_timeout_s


def _default_budget(queries: str | Path) -> float:
    """Budget for a run over `queries`. The queries are read only when the engine exists, so a missing engine is reported first."""
    return engine_budget_s(queries) if engine_available() else BASE_TIMEOUT_S


def _error_codes(text: str) -> list[str]:
    """Codes of the engine's JSON error list: the last line that parses as a list. [] when none does."""
    for line in reversed(text.split("\n")):
        try:
            items = json.loads(line)
        except ValueError:
            continue
        if isinstance(items, list):
            return [item["code"] for item in items if isinstance(item, dict) and isinstance(item.get("code"), str)]
    return []


_DIGEST_KEYS = ("theory.json", "bindings.jsonl", "provenance.jsonl", "queries", "answers")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _digest_line(stdout: str) -> dict | None:
    """The engine's digest line, parsed. The last line carrying `mb_engine_digests` is the one used. It counts only
    when the five required digests are present and every digest is 64 lowercase hex characters. None otherwise."""
    for line in reversed(stdout.split("\n")):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "mb_engine_digests" in obj:
            digests = obj["mb_engine_digests"]
            ok = (isinstance(digests, dict) and all(k in digests for k in _DIGEST_KEYS)
                  and all(isinstance(v, str) and _SHA256_HEX.fullmatch(v) for v in digests.values()))
            return obj if ok else None
    return None


def _run(args: list[str | Path], budget_s: float) -> tuple[str, str]:
    """Runs mb-engine and returns its (stdout, stderr)."""
    exe = engine_path()
    if not exe.is_file():
        raise EngineInfraError(f"mb-engine not built at {exe}; build it or set MB_ENGINE")
    try:
        proc = subprocess.Popen(
            [str(exe), *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", **start_kwargs(),
        )
    except OSError as exc:
        raise EngineInfraError(f"cannot run mb-engine at {exe}: {exc}") from exc
    try:
        out, err = proc.communicate(timeout=budget_s)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        _drain(proc)
        raise EngineError(f"mb-engine {args[0]} exceeded its {budget_s:g}s budget and was killed") from None
    except BaseException:
        kill_tree(proc)
        raise
    if proc.returncode != 0:
        detail = "\n".join(s for s in (out.strip(), err.strip()) if s)
        cls = EngineError if proc.returncode == 1 else EngineInfraError
        raise cls(f"mb-engine {args[0]} exited {proc.returncode}: {detail}", proc.returncode,
                  codes=_error_codes(err) or _error_codes(out))
    return out, err


def _drain(proc: subprocess.Popen) -> None:
    """After a kill: collect the pipes, but never wait on them forever (a surviving descendant may hold them)."""
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def validate(submission: str | Path, profile: str | Path | None = None) -> None:
    """`mb-engine validate`: raises EngineError (with the engine's error list) on any problem."""
    args: list[str | Path] = ["validate", submission]
    if profile is not None:
        args += ["--profile", profile]
    env = _env_budget_s()
    _run(args, BASE_TIMEOUT_S if env is None else env)


def eval_submission(submission: str | Path, queries: str | Path, answers_out: str | Path,
                    timeout_s: float | None = None) -> dict | None:
    """`mb-engine eval`, then checks that exactly one answer line was written per query line. Returns the engine's
    digest line, parsed (`{"mb_engine_digests": {...}, "version": ...}`), or None when the line is missing or malformed."""
    out, _ = _run(["eval", submission, queries, answers_out],
                  _default_budget(queries) if timeout_s is None else timeout_s)
    _expect_lines(answers_out, queries, "answers")
    return _digest_line(out)


def check(gold: str | Path, queries: str | Path, answers: str | Path, verdicts_out: str | Path, timeout_s: float | None = None,
          max_certificate_refs: int | None = None) -> None:
    """`mb-engine check`, then checks that exactly one verdict line was written per query line. The cap overrides
    the gold profile's max_certificate_refs (the evaluator passes the pinned profile's value)."""
    args: list[str | Path] = ["check", gold, queries, answers, verdicts_out]
    if max_certificate_refs is not None:
        args += ["--max-certificate-refs", str(max_certificate_refs)]
    _run(args, _default_budget(queries) if timeout_s is None else timeout_s)
    _expect_lines(verdicts_out, queries, "verdicts")


def _expect_lines(out: str | Path, queries: str | Path, what: str) -> None:
    if not Path(out).is_file():
        raise EngineInfraError(f"mb-engine wrote no {what} file")
    got, want = _count_lines(out), _count_lines(queries)
    if got != want:
        raise EngineInfraError(f"mb-engine wrote {got} {what} for {want} queries")

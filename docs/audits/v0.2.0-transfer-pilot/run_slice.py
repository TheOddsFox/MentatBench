"""Makes one judge call for one slice of the transfer pilot: runs `claude -p` (claude-haiku-5-5, no tools, no session persistence, JSON
output) from an empty working folder outside the repository, with the slice's stdin (score.py input). The stdin is saved and a calling line is
written before the call; the stdout is saved to LOCAL judge/raw/inst<i>/slice<j>/attempt<k>.json, and a called line records it after the
call returns. The stderr is saved beside it. Nothing is parsed here: `score.py extract` closes the call. Adapted from
docs/audits/v0.2.0-reasoning-reference/run_slice.py.

usage: python -B run_slice.py INST SLICE ATTEMPT --workdir EMPTY_FOLDER_OUTSIDE_THE_REPOSITORY

The attempt is checked against the ledger before the call (score.check_allowed): a pending call blocks a new call; until `score.py preflight`
has passed, only instance 1 is called, one slice at a time in order; and an interrupted or timed-out call stays pending for extract. The subprocess gets
the stdin as bytes, not through a shell redirection, and the environment variable CLAUDE_CODE_DISABLE_AUTO_MEMORY=1.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import score as SC  # noqa: E402

MODEL = "claude-haiku-5-5"
CLAUDE_ARGS = ["-p", "--safe-mode", "--model", MODEL, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
               "--output-format", "json"]
ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}


def check_workdir(workdir: Path) -> Path:
    """The working folder: an existing empty folder outside the repository (SC.Refused otherwise)."""
    wd = workdir.resolve()
    if not wd.is_dir():
        raise SC.Refused(f"{wd} is not a folder")
    if wd == SC.ROOT or SC.ROOT in wd.parents:
        raise SC.Refused(f"{wd} is inside the repository; the working folder must be outside it")
    if any(wd.iterdir()):
        raise SC.Refused(f"{wd} is not empty")
    return wd


def capture(inst: int, slice_no: int, attempt: int, workdir: Path) -> tuple[Path, int]:
    """Runs one attempt and saves its raw output. Returns the raw file's path and the process's exit code. Refused (before any call)
    when a frozen file differs, or when the ledger does not allow the attempt. The stdin and a calling line are written before the
    subprocess starts; the raw output and a called line after it returns. A call that is interrupted, or times out, stays pending and
    score.py extract closes it."""
    SC.check_frozen()
    ledger = SC.read_ledger()
    SC.verify_ledger(ledger)
    try:
        SC.check_allowed(ledger, inst, slice_no, attempt)
    except SC.Refused:
        SC.record_stop()  # a stopped pilot gets its stop line the first time a call is refused
        raise
    out = SC.raw_path(inst, slice_no, attempt)
    if out.exists():
        raise SC.Refused(f"{out} exists: a raw output with no closing line; extract it, or move it aside by hand")
    wd = check_workdir(workdir)
    claude = shutil.which("claude")
    if claude is None:
        raise SC.Refused("claude is not on PATH")
    stdin = SC.stdin_of(slice_no)
    SC.record_calling(inst, slice_no, attempt, stdin)
    try:
        proc = subprocess.run([claude, *CLAUDE_ARGS], input=stdin, cwd=wd, env={**os.environ, **ENV}, capture_output=True,
                              check=False, timeout=SC.TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise SC.Refused(f"the call timed out after {SC.TIMEOUT_S} s: it stays pending; score.py extract closes it") from exc
    SC.record_called(inst, slice_no, attempt, proc.stdout)
    out.with_suffix(".stderr").write_bytes(proc.stderr)
    return out, proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("instance", "slice", "attempt"):
        ap.add_argument(name, type=int)
    ap.add_argument("--workdir", type=Path, required=True, help="an empty folder outside the repository")
    a = ap.parse_args(argv)
    try:
        out, code = capture(a.instance, a.slice, a.attempt, a.workdir)
    except SC.Refused as exc:
        print(f"run_slice.py: refused: {exc}", file=sys.stderr)
        return 1
    print(f"called and saved {out} (exit code {code}); next: python -B score.py extract {a.instance} {a.slice} {a.attempt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

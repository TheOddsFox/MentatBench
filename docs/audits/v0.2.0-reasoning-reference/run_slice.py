"""Captures one raw output of one slice of the reasoning reference: runs `claude -p` (claude-haiku-5-5, no tools, no session
persistence, JSON output) from an empty working folder outside the repository, with the slice's stdin (score.py input), and saves
its stdout bytes exactly to raw/inst<i>/slice<j>/attempt<k>.json. Its stderr goes beside it (attempt<k>.stderr). Nothing is
parsed here: `score.py extract` checks the raw output and records it in raw/ledger.jsonl.

usage: python -B run_slice.py INST SLICE ATTEMPT --workdir EMPTY_FOLDER_OUTSIDE_THE_REPOSITORY

The attempt is checked against the ledger before the call (score.check_allowed): a second attempt needs a void attempt 1, and a
raw file that already exists is never overwritten. The subprocess gets the stdin as bytes, not through a shell redirection, and the
environment variable CLAUDE_CODE_DISABLE_AUTO_MEMORY=1.
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


def capture(inst: int, slice_no: int, attempt: int, workdir: Path, base: Path = SC.HERE) -> tuple[Path, int]:
    """Runs one attempt and saves its raw output. Returns the raw file's path and the process's exit code. Refused (before any call)
    when a frozen file differs, or when the ledger does not allow the attempt."""
    SC.check_frozen()
    ledger = SC.read_ledger(base)
    SC.verify_ledger(base, ledger)
    SC.check_allowed(ledger, inst, slice_no, attempt)
    out = SC.raw_path(base, inst, slice_no, attempt)
    if out.exists():
        raise SC.Refused(f"{out} exists with no ledger entry: move it aside (a schema error keeps its file) and repeat the call")
    wd = check_workdir(workdir)
    claude = shutil.which("claude")
    if claude is None:
        raise SC.Refused("claude is not on PATH")
    proc = subprocess.run([claude, *CLAUDE_ARGS], input=SC.stdin_of(slice_no), cwd=wd, env={**os.environ, **ENV},
                          capture_output=True, check=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(proc.stdout)
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
    print(f"saved {out} (exit code {code}); next: python -B score.py extract {a.instance} {a.slice} {a.attempt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

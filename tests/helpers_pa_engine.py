"""Fake mb-engine for the protocol tests: a launcher (.cmd on Windows, sh elsewhere) that runs a Python script.

The behaviour is chosen by $FAKE_ENGINE_MODE (inherited by the child):
  ok       validate passes; eval and check write one line per query line
  invalid  every command prints an error list and exits 1 (submission refused)
  usage    every command exits 2 (usage/IO inside the engine)
  short    eval writes one answer line too few
  hang     every command sleeps far past any test budget
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPT = r'''
import os, sys, time
mode = os.environ.get("FAKE_ENGINE_MODE", "ok")
cmd = sys.argv[1]
if mode == "hang":
    time.sleep(600)
if mode == "usage":
    sys.stderr.write("bad usage\n")
    sys.exit(2)
if mode == "invalid":
    sys.stderr.write('[{"code": "inconsistent_base"}]\n')
    sys.exit(1)
if cmd == "validate":
    sys.exit(0)

def lines(path):
    with open(path, encoding="utf-8-sig") as fh:
        return sum(1 for line in fh if line.strip())

if cmd == "eval":
    queries, out = sys.argv[3], sys.argv[4]
    n = lines(queries) - (1 if mode == "short" else 0)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for i in range(n):
            fh.write('{"qid": "q%d", "error": "fake"}\n' % i)
    sys.exit(0)
if cmd == "check":
    queries, out = sys.argv[3], sys.argv[5]
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for i in range(lines(queries)):
            fh.write('{"qid": "q%d", "cert_valid": false, "reason": "fake"}\n' % i)
    sys.exit(0)
sys.exit(2)
'''


def make_fake_engine(directory: Path) -> Path:
    """Writes the script and a launcher into `directory`; returns the launcher path (for MB_ENGINE)."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "fake_engine.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    if os.name == "nt":
        launcher = directory / "fake_engine.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = directory / "fake_engine"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher

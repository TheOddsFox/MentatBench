"""Shared helpers for the seal tests: a scoped view of os and time as seal.py sees them, and a fake clock.

Patches made through these helpers reach mentat_bench.seal only; the global os and time modules are left alone."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

from mentat_bench import seal as seal_mod


def scoped_os(monkeypatch, **overrides):
    """seal.py's view of os, with some functions replaced. Patches compose (each builds on the previous view)."""
    base = seal_mod.os
    proxy = SimpleNamespace(**{name: getattr(base, name) for name in dir(base) if not name.startswith("__")})
    for name, value in overrides.items():
        setattr(proxy, name, value)
    monkeypatch.setattr(seal_mod, "os", proxy)


def case_insensitive_paths(monkeypatch):
    """seal.py compares paths as Windows does: os.path.normcase lowercases on both sides."""
    ns = {name: getattr(os.path, name) for name in dir(os.path) if not name.startswith("__")}
    ns["normcase"] = str.lower
    scoped_os(monkeypatch, path=SimpleNamespace(**ns))


def is_seal_temp(path) -> bool:
    """The temp file that seal.json is written to before it is hard-linked into place."""
    name = Path(path).name
    return name.startswith(".seal.json.") and name.endswith(".tmp")


class FakeClock:
    """Replaces the time seal.py sleeps and measures its deadlines with: sleeps are counted and advance the clock."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def fake_clock(monkeypatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(seal_mod, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep, time=time.time))
    return clock

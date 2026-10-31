"""Shared pytest hooks: make engine- and fixture-dependent skips visible, and optionally make them failures.

With MB_REQUIRE_ENGINE=1, an engine or fixture skip fails the run so a machine expected to have the test inputs
cannot pass by silently skipping them.
"""

import os
from collections import Counter

import pytest

# xdist workers share the machine. Set before any test module imports torch or numpy (conftest loads first); a serial
# run keeps the library defaults. One BLAS/OpenMP thread per worker avoids N x 32 thread oversubscription.
if "PYTEST_XDIST_WORKER" in os.environ:
    for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(_var, "1")

INPUT_WORDS = ("mb-engine", "engine", "fixture")
SLOW_REASON = "slow test: set MB_RUN_SLOW=1 or pass -m slow"


def _reason(report) -> str:
    longrepr = report.longrepr
    text = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
    return text.removeprefix("Skipped: ")


def _input_skip(reason: str) -> bool:
    return any(word in reason for word in INPUT_WORDS)


def _slow_skip(reason: str) -> bool:
    return reason == SLOW_REASON


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running test, runs only with MB_RUN_SLOW=1 or -m slow")


def pytest_collection_modifyitems(config, items):
    expr = (getattr(config.option, "markexpr", "") or "").strip()
    if os.environ.get("MB_RUN_SLOW") == "1" or expr:  # any -m expression: pytest's own selection decides
        return
    skip = pytest.mark.skip(reason=SLOW_REASON)
    for item in items:
        if item.get_closest_marker("slow"):
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when == "setup" and report.skipped and _input_skip(_reason(report)):
        rule = _required_by(_reason(report))
        if rule:
            report.outcome = "failed"
            report.longrepr = f"required input missing ({rule}): {_reason(report)}"


def _required_by(reason: str) -> str | None:
    """Why this input skip becomes a failure, or None (it stays a skip)."""
    if _require_engine():
        return "MB_REQUIRE_ENGINE=1"
    return None


def _require_engine() -> bool:
    import os

    return os.environ.get("MB_REQUIRE_ENGINE") == "1"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    skipped = terminalreporter.stats.get("skipped", [])
    slow = Counter(_reason(r) for r in skipped if _slow_skip(_reason(r)))
    if slow:
        terminalreporter.write_sep("-", f"slow tests skipped (MB_RUN_SLOW=1 or -m slow to run): {sum(slow.values())}")
    reasons = Counter(_reason(r) for r in skipped if _input_skip(_reason(r)))
    if not reasons:
        return
    mode = "FAILED (MB_REQUIRE_ENGINE=1)" if _require_engine() else "skipped"
    terminalreporter.write_sep("-", f"engine/fixture-dependent tests {mode}: {sum(reasons.values())}")
    for reason, count in reasons.most_common():
        terminalreporter.write_line(f"  {count} x {reason}")

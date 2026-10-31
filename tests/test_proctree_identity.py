"""Process identity for the tree walk and the kill of a verification's engine processes (python/mentat_bench/adapters/proctree.py).
A process is named by its PID and its start time: an orphan whose parent PID was reused is not a descendant, and a process
whose PID was reused between the snapshot and the kill is not killed.
"""

import subprocess
import sys
import time

import pytest

from mentat_bench.adapters import proctree

PROCESS_TREE_API = sys.platform.startswith("linux") or sys.platform == "win32"


def _alive(child):
    return child.poll() is None


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])


def test_an_orphan_whose_parent_pid_was_reused_is_not_a_descendant(monkeypatch):
    """Root 1 (started at 100) has child 10 (started at 200). Process 20 is listed under 10, but started at 150, before 10:
    it belongs to an earlier process whose PID 10 was reused, so it is not in the tree (nor are its children)."""
    children = {1: [10], 10: [20], 20: [30]}
    started = {1: 100, 10: 200, 20: 150, 30: 300}
    monkeypatch.setattr(proctree, "_WINDOWS", True)
    monkeypatch.setattr(proctree, "_win_children", lambda: children)
    monkeypatch.setattr(proctree, "_win_stats", lambda pid: (0.0, 0.0, started[pid]) if pid in started else None)
    assert set(proctree._tree(1)) == {(1, 100), (10, 200)}


def test_a_child_started_before_its_parent_is_not_a_descendant(monkeypatch):
    monkeypatch.setattr(proctree, "_WINDOWS", True)
    monkeypatch.setattr(proctree, "_win_children", lambda: {1: [2]})
    monkeypatch.setattr(proctree, "_win_stats", lambda pid: {1: (0.0, 0.0, 500), 2: (0.0, 0.0, 400)}.get(pid))
    assert set(proctree._tree(1)) == {(1, 500)}


@pytest.mark.skipif(not PROCESS_TREE_API, reason="requires Linux /proc or the Windows process API")
def test_a_pid_reused_between_the_snapshot_and_the_kill_is_not_killed():
    """The snapshot recorded the process with start time `started`. The PID now belongs to a process with another start time
    (here: the same PID with a different start), so nothing is killed. With the recorded start the kill goes through."""
    child = _sleeper()
    try:
        (pid, started), = [key for key in proctree._tree(child.pid) if key[0] == child.pid]
        assert not proctree.terminate_verified(pid, started + 1)  # a reused PID: another process, left alone
        time.sleep(0.2)
        assert _alive(child)
        assert proctree.terminate_verified(pid, started)  # the recorded process is killed
        child.wait(timeout=15)
        assert not _alive(child)
    finally:
        if child.poll() is None:
            child.kill()  # this test's own process, held by its handle
            child.wait()


@pytest.mark.skipif(not PROCESS_TREE_API, reason="requires Linux /proc or the Windows process API")
def test_a_process_that_is_gone_is_not_reported_as_killed():
    child = _sleeper()
    key = [k for k in proctree._tree(child.pid) if k[0] == child.pid]
    child.kill()
    child.wait()
    if not key:
        pytest.skip("the process could not be read before it exited")
    pid, started = key[0]
    assert proctree.terminate_verified(pid, started) is False

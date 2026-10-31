"""TreeMeter bookkeeping: processes are keyed by (PID, start time), a process that leaves the tree is retired with its CPU
kept in the total, and a retired key is never counted again. The process table is replaced by scripted samples."""

import pytest

from mentat_bench.adapters import proctree


def _scripted(monkeypatch, samples):
    queue = list(samples)
    monkeypatch.setattr(proctree, "SAMPLE_EVERY_S", 0.0)
    monkeypatch.setattr(proctree, "_tree", lambda pid: queue.pop(0))


def test_a_reused_pid_is_a_new_process(monkeypatch):
    _scripted(monkeypatch, [{(100, 1): (2.0, 10.0)}, {(100, 2): (0.5, 5.0)}])
    meter = proctree.TreeMeter()
    assert meter.sample(100) == pytest.approx((2.0, 10.0))
    # the old process (start 1) is gone; the new one (start 2) burnt 0.5 s; the peak keeps the old 10 MB
    assert meter.sample(100) == pytest.approx((0.5, 10.0))


def test_dead_processes_are_pruned_and_their_cpu_is_kept(monkeypatch):
    _scripted(monkeypatch, [
        {(1, 1): (1.0, 1.0), (2, 1): (3.0, 1.0)},
        {(1, 1): (1.5, 1.0)},  # pid 2 has exited
        {(1, 1): (1.5, 1.0)},
    ])
    meter = proctree.TreeMeter()
    assert meter.sample(1) == pytest.approx((4.0, 2.0))  # the peak is the tree's summed memory: 2 MB
    assert meter.sample(1) == pytest.approx((0.5, 2.0))  # 3.0 s of the dead process stay in the total
    assert set(meter._live) == {(1, 1)}  # its entry is dropped
    assert meter.sample(1) == pytest.approx((0.0, 2.0))


def test_a_retired_process_that_reappears_is_not_counted_again(monkeypatch):
    _scripted(monkeypatch, [
        {(5, 1): (2.0, 1.0), (6, 1): (1.0, 1.0)},
        {(6, 1): (1.0, 1.0)},  # pid 5 is unreadable for a moment and retired
        {(5, 1): (2.5, 1.0), (6, 1): (1.0, 1.0)},  # ...then it is back: its CPU is not added a second time
    ])
    meter = proctree.TreeMeter()
    meter.sample(5)
    meter.sample(5)
    assert meter.sample(5) == pytest.approx((0.0, 2.0))


def test_sampling_failure_is_not_a_sample(monkeypatch):
    def broken(pid):
        raise OSError("process table unreadable")

    monkeypatch.setattr(proctree, "SAMPLE_EVERY_S", 0.0)
    monkeypatch.setattr(proctree, "_tree", broken)
    assert proctree.TreeMeter().sample(1) is None

"""Process-tree termination and metering for child processes (local adapters and mb-engine).

Children must be started with `start_kwargs()` so that a POSIX process group exists. On Windows the tree
is killed with `taskkill /T`, which reaches descendants while their parent is still alive. Processes that
detach from the tree (double-forked daemons, breakaway jobs) are not reached: a documented limitation.

Metering. `TreeMeter.sample(pid)` reads the CPU seconds and resident memory of a process tree (Windows: Toolhelp
snapshot, GetProcessTimes, GetProcessMemoryInfo; POSIX: /proc). A sample returns None when the tree cannot be read or
the request falls within SAMPLE_EVERY_S of the previous sample, so metering never fails a request. Limits: a descendant that exits between two samples is counted from the last
sample that saw it, and memory released between samples is missed.
"""

from __future__ import annotations

import ctypes
import functools
import os
import signal
import subprocess
import time
from ctypes import wintypes
from typing import Any

from ..gateway import _ProcessMemoryCounters  # the Windows working-set structure, shared with peak_rss_mb

_WINDOWS = os.name == "nt"
_TH32CS_SNAPPROCESS = 0x2
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _ProcessEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _FileTime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


def start_kwargs() -> dict[str, Any]:
    return {} if _WINDOWS else {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill proc and its descendants, then reap proc. Safe on an exited process."""
    if proc.poll() is None:
        if _WINDOWS:
            try:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


@functools.lru_cache(maxsize=1)
def _win_api() -> tuple[Any, Any]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FileTime)] * 4
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]
    return kernel32, psapi


def _win_children() -> dict[int, list[int]]:
    """Parent pid -> child pids of every process in the system (one Toolhelp snapshot)."""
    kernel32, _psapi = _win_api()
    snap = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap is None or snap == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    try:
        entry = _ProcessEntry32()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32)
        children: dict[int, list[int]] = {}
        more = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            more = kernel32.Process32NextW(snap, ctypes.byref(entry))
        return children
    finally:
        kernel32.CloseHandle(snap)


def _filetime_ticks(t: _FileTime) -> int:
    return (t.high << 32) | t.low  # 100 ns units


def _win_stats(pid: int) -> tuple[float, float, int] | None:
    """(CPU seconds, working set MB, start time) of one process; None when it cannot be opened or has exited. The start
    time (creation FILETIME) tells a reused PID apart from the process that held it."""
    kernel32, psapi = _win_api()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        cpu = (_filetime_ticks(kernel) + _filetime_ticks(user)) / 1e7
        return cpu, counters.WorkingSetSize / 2**20, _filetime_ticks(created)
    finally:
        kernel32.CloseHandle(handle)


def _proc_fields(pid: int) -> list[str] | None:
    """The fields of /proc/<pid>/stat after the command name (state, ppid, ..., utime is index 11, stime 12,
    starttime 19)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            return fh.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None


def _posix_children() -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for name in os.listdir("/proc"):
        fields = _proc_fields(int(name)) if name.isdigit() else None
        if fields is not None:
            children.setdefault(int(fields[1]), []).append(int(name))
    return children


def _posix_stats(pid: int) -> tuple[float, float, int] | None:
    """(CPU seconds, resident MB, start time in clock ticks) of one process from /proc; None when it has exited."""
    fields = _proc_fields(pid)
    if fields is None:
        return None
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as fh:
            resident_pages = int(fh.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None
    cpu = (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    return cpu, resident_pages * os.sysconf("SC_PAGE_SIZE") / 2**20, int(fields[19])


def _tree(pid: int) -> dict[tuple[int, int], tuple[float, float]]:
    """(CPU seconds, resident MB) of `pid` and its descendants that can be read now, keyed by (pid, start time). A child is
    accepted only if it started no earlier than its parent: a child whose parent PID now belongs to a newer process is an
    orphan of an earlier process, not a descendant, and it is left out together with its own children."""
    children = _win_children() if _WINDOWS else _posix_children()
    stats = _win_stats if _WINDOWS else _posix_stats
    out: dict[tuple[int, int], tuple[float, float]] = {}
    seen: set[int] = set()
    todo: list[tuple[int, int | None]] = [(pid, None)]  # (pid, start time of its parent)
    while todo:
        p, parent_start = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        found = stats(p)
        if found is None:
            continue
        started = found[2]
        if parent_start is not None and started < parent_start:
            continue
        out[(p, started)] = (found[0], found[1])
        todo.extend((c, started) for c in children.get(p, ()))
    return out


def terminate_verified(pid: int, started: int) -> bool:
    """Kills `pid` only if it is still the process recorded with start time `started`; a PID that now belongs to another
    process is left alone. Windows: the process is opened, its creation time is checked on that handle, and TerminateProcess is
    called on the same handle, so the check and the kill see one process. POSIX: the start time is read from /proc, then
    SIGKILL is sent (a small race remains between the two). Returns True when the process was killed."""
    if _WINDOWS:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FileTime)] * 4
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            created, exited, kernel, user = _FileTime(), _FileTime(), _FileTime(), _FileTime()
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel),
                                            ctypes.byref(user)):
                return False
            if _filetime_ticks(created) != started:
                return False
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    fields = _posix_stats(pid)
    if fields is None or fields[2] != started:
        return False
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


SAMPLE_EVERY_S = 0.5  # a process-table snapshot costs ~5 ms on Windows, so requests closer than this carry no child fields


class TreeMeter:
    """CPU seconds and peak resident memory of one child's process tree, sampled at most every SAMPLE_EVERY_S.

    sample(pid) returns (CPU seconds used since the previous sample, peak resident MB over all samples), or None when
    the request falls inside the interval or the tree cannot be read (the caller then omits the fields). Processes are
    keyed by (PID, start time), so a reused PID is a new process. A process that leaves the tree is retired: its last
    CPU stays in the total, its entry is dropped, and it is never counted again, so the total never decreases.
    shortcut: CPU spent after the last sample of a run is not counted (at most SAMPLE_EVERY_S of child time), memory held
    only between two samples is missed, and a process that is briefly unreadable is retired early (its later CPU is
    not counted); upgrade to a continuous sampler or per-job accounting if the tail matters."""

    def __init__(self) -> None:
        self._live: dict[tuple[int, int], float] = {}  # (pid, start time) -> CPU seconds at its last sample
        self._retired: set[tuple[int, int]] = set()  # processes that left the tree: their CPU is in _retired_cpu
        self._retired_cpu = 0.0
        self._reported = 0.0
        self._peak_mb = 0.0
        self._sampled_at: float | None = None  # monotonic time of the last sample attempt

    def sample(self, pid: int) -> tuple[float, float] | None:
        now = time.monotonic()
        if self._sampled_at is not None and now - self._sampled_at < SAMPLE_EVERY_S:
            return None
        self._sampled_at = now
        try:
            tree = _tree(pid)
        except Exception:  # noqa: BLE001 - metering must never fail a request
            return None
        if not tree:
            return None
        for key in [k for k in self._live if k not in tree]:
            self._retired_cpu += self._live.pop(key)
            self._retired.add(key)
        for key, (cpu, _rss) in tree.items():
            if key not in self._retired:
                self._live[key] = cpu
        total = self._retired_cpu + sum(self._live.values())
        self._peak_mb = max(self._peak_mb, sum(rss for _cpu, rss in tree.values()))
        delta, self._reported = total - self._reported, total
        return max(delta, 0.0), self._peak_mb

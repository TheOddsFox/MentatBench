"""Metered model gateway: wraps any ModelAdapter, records every call, enforces spend and deadline caps."""

from __future__ import annotations

import ctypes
import math
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Callable

from .adapters.base import USAGE_KINDS, USAGE_OPS, AdapterError, check_usage, finite_number


class BudgetExceeded(RuntimeError):
    """A spend or deadline cap was reached or overshot. The caller seals its latest valid checkpoint.
    A call that overshoots is charged to usage, but its decision is not returned."""


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_rss_mb() -> float:
    """Peak resident memory of this process in MB (POSIX getrusage, Windows GetProcessMemoryInfo)."""
    try:
        import resource
    except ImportError:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]
        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return counters.PeakWorkingSetSize / 2**20
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maxrss / 2**20 if sys.platform == "darwin" else maxrss / 1024


class UsageError(ValueError):
    """A submission's usage.json fails the schema or exceeds the pinned profile caps."""


def _tolerant_number(value: Any) -> float:
    return 0 if value is None else value


def usage_totals(usage: Any) -> dict[str, Any]:
    """Totals of a usage.json for reporting. Missing or null fields count as zero; a non-object or
    non-object call raises ValueError. The strict schema check for evaluate is validate_usage."""
    if not isinstance(usage, dict) or not isinstance(usage.get("calls") or [], list):
        raise ValueError("usage must be an object with a calls list")
    calls = usage.get("calls") or []
    if not all(isinstance(c, dict) for c in calls):
        raise ValueError("every usage call must be an object")
    totals = {
        "calls": len(calls),
        "items": sum(c["items"] if isinstance(c.get("items"), int) else 1 for c in calls),
        "retries": sum(1 for c in calls if c.get("retry")),
        "tokens_in": sum(_tolerant_number(c.get("tokens_in")) for c in calls),
        "tokens_out": sum(_tolerant_number(c.get("tokens_out")) for c in calls),
        "cost_usd": round(sum(_tolerant_number(c.get("cost_usd")) for c in calls), 6),
        "call_seconds": round(sum(_tolerant_number(c.get("seconds")) for c in calls), 6),
        "gpu_seconds": round(sum(_tolerant_number(c.get("gpu_seconds")) for c in calls), 6),
        "wall_seconds": usage.get("wall_seconds"),
        "peak_rss_mb": usage.get("peak_rss_mb"),
    }
    if any("child_cpu_seconds" in c or "child_peak_rss_mb" in c for c in calls):  # local children; absent otherwise
        totals["child_cpu_seconds"] = round(sum(_tolerant_number(c.get("child_cpu_seconds")) for c in calls), 6)
        totals["child_peak_rss_mb"] = max((_tolerant_number(c.get("child_peak_rss_mb")) for c in calls), default=0)
    return totals


def validate_usage(usage: Any, profile: dict[str, Any]) -> dict[str, Any]:
    """Strict check of a submission's usage.json (evaluate): schema for every call, then the caps.

    A call that overshoots a cap is discarded by the gateway and the build stops there, so such a call is
    always the LAST entry of usage.json (at most one). The caps are therefore compared on every call but the
    last, and the last call's own cost and seconds are not compared (they are schema-checked only). Every
    accepted call starts under the caps, so a violation before the last entry means the file was not written
    by the gateway. Returns usage_totals of all calls."""
    if not isinstance(usage, dict) or not isinstance(usage.get("calls"), list):
        raise UsageError('usage.json must be an object with a "calls" list')
    try:
        for i, call in enumerate(usage["calls"]):
            if not isinstance(call, dict):
                raise AdapterError("call is not an object")
            fields = check_usage(call)
            if fields["kind"] not in USAGE_KINDS:
                raise AdapterError(f"unknown kind {fields['kind']!r}")
            if "op" in call and call["op"] not in USAGE_OPS:  # optional: usage.json from before the op has none
                raise AdapterError(f"unknown op {call['op']!r}")
            finite_number(call, "seconds")
            if not isinstance(call.get("retry", False), bool):
                raise AdapterError("retry must be a boolean")
        finite_number(usage, "wall_seconds")
        finite_number(usage, "peak_rss_mb")
    except AdapterError as exc:
        raise UsageError(f"usage.json: {exc}") from exc
    accepted = usage_totals({**usage, "calls": usage["calls"][:-1]})
    if accepted["cost_usd"] > profile["spend_ceiling_usd"] + 1e-9:
        raise UsageError(f"accepted calls cost {accepted['cost_usd']} USD, over spend_ceiling_usd {profile['spend_ceiling_usd']}")
    if accepted["call_seconds"] > profile["build_deadline_s"] + 1e-6:
        raise UsageError(f"accepted calls took {accepted['call_seconds']}s, over build_deadline_s {profile['build_deadline_s']}")
    return usage_totals(usage)


@dataclass
class Gateway:
    """Every call is charged when it returns or fails. Deadline rule: a call may not start once elapsed >=
    build_deadline_s, and a call that finishes exactly at the deadline is accepted; a call that finishes
    after it is discarded. Spend rule: a call may not start once spend >= spend_ceiling_usd; a call that
    leaves spend > spend_ceiling_usd is discarded. A discarded call is charged to usage, raises
    BudgetExceeded and its decision is not returned; the build stops, so it is the last usage entry.
    Failed calls are retried (max_retries) while under the caps. items_left (None: no cap) is the decision items the
    build may still charge (--max-calls): every charged entry counts its items, retries included, and a retry runs only
    if it fits in what is left after `reserve` (the binding items not yet asked, which a retry must leave); the first
    attempt of a call always runs."""

    adapter: Any
    spend_ceiling_usd: float
    build_deadline_s: float
    max_retries: int = 2
    clock: Callable[[], float] = time.perf_counter  # high resolution: time.monotonic ticks every 15.6 ms on Windows
    calls: list[dict[str, Any]] = field(default_factory=list)
    spend_usd: float = 0.0
    _t0: float = 0.0
    elapsed_offset_s: float = 0.0  # time of earlier runs of a resumed build
    profile_deadline_s: float | None = None  # the profile's build_deadline_s, when build_deadline_s is a stop before it (messages only)
    items_left: int | None = None  # decision items the build may still charge (None: no cap)
    reserve: int = 0  # items kept for first attempts not yet asked (the bindings of phase 1)
    sink: Callable[[dict[str, Any]], None] | None = None  # receives each charged entry at its commit (a build's usage log)

    def __post_init__(self) -> None:
        if not (math.isfinite(self.spend_ceiling_usd) and self.spend_ceiling_usd >= 0):
            raise ValueError("spend_ceiling_usd must be a finite non-negative number")
        if not (math.isfinite(self.build_deadline_s) and self.build_deadline_s > 0):
            raise ValueError("build_deadline_s must be a finite positive number of seconds")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._t0 = self.clock()

    def _fits_retry(self, items: int) -> bool:
        """Can `items` more be charged as a retry: within items_left, past the reserve (None: no cap)?"""
        return self.items_left is None or self.items_left - self.reserve >= items

    def elapsed_s(self) -> float:
        return self.clock() - self._t0 + self.elapsed_offset_s

    def _check_caps(self) -> None:
        if self.spend_usd >= self.spend_ceiling_usd:
            raise BudgetExceeded(f"spend ceiling reached: {self.spend_usd:.6f} >= {self.spend_ceiling_usd} USD")
        if self.elapsed_s() >= self.build_deadline_s:
            raise BudgetExceeded(f"model-call stop reached: {self.elapsed_s():.3f}s >= {self.build_deadline_s}s{self._of_profile()}")

    def _of_profile(self) -> str:
        if self.profile_deadline_s is None:
            return ""
        return f" (build_deadline_s {self.profile_deadline_s}s; the stop is {self.build_deadline_s / self.profile_deadline_s:.2f} of it)"

    def _entry(self, usage: Any, seconds: float, retry: bool, op: str) -> dict[str, Any]:
        """One usage record for usage.json, charged to the decision method `op`. AdapterError when the adapter's usage is
        malformed."""
        fields = check_usage(usage)
        if fields["kind"] not in USAGE_KINDS:
            raise ValueError(f"unknown usage kind {fields['kind']!r}")
        return {**fields, "seconds": seconds, "retry": retry, "op": op}

    def _failed_entry(self, usage: Any, seconds: float, retry: bool, op: str, items: int | None = None) -> dict[str, Any]:
        """Charge for a failed call: the usage it reported, or nothing when that usage is missing or malformed.
        `items` (a batch's size) is set on the entry whatever the reported usage says."""
        zero = {"model": str(getattr(self.adapter, "name", "adapter")), "kind": "local", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        extra = {} if items is None else {"items": items}
        try:
            return self._entry({**(usage or zero), **extra}, seconds, retry, op)
        except AdapterError:
            return self._entry({**zero, **extra}, seconds, retry, op)

    def _call(self, method: str, *args: Any) -> Any:
        last_error: AdapterError | None = None
        attempts = 0
        for attempt in range(self.max_retries + 1):
            if attempt and not self._fits_retry(1):
                break  # the retry would pass --max-calls (or the reserve): the last error stands
            attempts += 1
            self._check_caps()
            start = self.clock()
            extra = {"timeout_s": max(self.build_deadline_s - self.elapsed_s(), 1e-3)} if getattr(self.adapter, "supports_timeout", False) else {}
            try:
                decision, usage = getattr(self.adapter, method)(*args, **extra)
                entry = self._entry(usage, self.clock() - start, attempt > 0, method)
            except AdapterError as exc:
                last_error = exc
                self._commit(self._failed_entry(exc.usage, self.clock() - start, attempt > 0, method))
                self._check_overshoot("the failed call is charged and not retried")
                continue
            self._commit(entry)
            self._check_overshoot("its decision is discarded")
            return decision
        raise AdapterError(f"{method} failed after {attempts} attempts: {last_error}")

    def _commit(self, entry: dict[str, Any]) -> None:
        self.spend_usd += entry["cost_usd"]  # counted before the sink: a failed write (disk full) still counts in memory
        if self.items_left is not None:
            self.items_left -= entry.get("items", 1)
        if self.sink is None:
            self.calls.append(entry)
        else:
            self.sink(entry)  # written at once: a kill in a retry loop keeps the paid entries before it

    def _check_overshoot(self, note: str) -> None:
        if self.spend_usd > self.spend_ceiling_usd:
            raise BudgetExceeded(f"call overshot the spend ceiling: {self.spend_usd:.6f} > {self.spend_ceiling_usd} USD; {note}")
        if self.elapsed_s() > self.build_deadline_s:
            raise BudgetExceeded(f"call ran past the model-call stop: {self.elapsed_s():.3f}s > {self.build_deadline_s}s{self._of_profile()}; {note}")

    def decide_batch(self, op: str, items: list[tuple[Any, ...]]) -> list[Any]:
        """Decisions of one batch of `op` calls; items are the argument tuples of the single-call method. Returns one
        entry per item: its decision, or the AdapterError it was left with after the retries. Only the items that
        failed are sent again (each retry is one more usage entry, with retry true). A batch that overshoots a cap is
        charged and its decisions are discarded: BudgetExceeded, as for a single call. A retry runs only if its items fit in
        items_left (see the class docstring)."""
        results: list[Any] = [None] * len(items)
        errors: dict[int, AdapterError] = {}
        pending = list(range(len(items)))
        for attempt in range(self.max_retries + 1):
            if not pending:
                break
            if attempt and not self._fits_retry(len(pending)):
                break  # the retry would pass --max-calls (or the reserve): these items keep their errors
            self._check_caps()
            start = self.clock()
            extra = {"timeout_s": max(self.build_deadline_s - self.elapsed_s(), 1e-3)} if getattr(self.adapter, "supports_timeout", False) else {}
            try:
                outcomes, usage = self.adapter.decide_batch([(op, items[i]) for i in pending], **extra)
                if len(outcomes) != len(pending):  # the envelope was spent: its usage is charged with the failure
                    raise AdapterError(f"{op} batch answered {len(outcomes)} decisions for {len(pending)} items", usage=usage)
                entry = self._entry({**usage, "items": len(pending)}, self.clock() - start, attempt > 0, op)
            except AdapterError as exc:
                self._commit(self._failed_entry(exc.usage, self.clock() - start, attempt > 0, op, items=len(pending)))
                self._check_overshoot("the failed batch is charged and its items are retried")
                errors.update((i, exc) for i in pending)
                continue
            self._commit(entry)
            self._check_overshoot("its decisions are discarded")
            still = []
            for i, outcome in zip(pending, outcomes):
                if isinstance(outcome, AdapterError):
                    errors[i] = outcome
                    still.append(i)
                else:
                    results[i] = outcome
                    errors.pop(i, None)
            pending = still
        for i in pending:
            results[i] = errors[i]
        return results

    def decide_binding(self, outcome_context: dict[str, Any]) -> dict[str, Any]:
        return self._call("decide_binding", outcome_context)

    def decide_relation(self, a_context: dict[str, Any], b_context: dict[str, Any]) -> Any:
        """A relation name, or {relation, conf} when the child sent a confidence (passed through unchanged)."""
        return self._call("decide_relation", a_context, b_context)

    def decide_group(self, group_ctx: dict[str, Any]) -> list[dict[str, Any]]:
        return self._call("decide_group", group_ctx)

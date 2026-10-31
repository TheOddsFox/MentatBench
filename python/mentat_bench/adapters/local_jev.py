"""Adapter that drives a local decision process (for example a Jev build) over JSON lines.

Protocol. The process is started with the configured argv, with PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 in
its environment. Each request is one JSON object on one line of stdin, answered by exactly one JSON object
on one line of stdout, in order (UTF-8). Every request carries a sequence number "id"; a response may echo it
(then it must match). Requests are ASCII-only JSON, so lone surrogates in a question cannot break the pipe.

  request  {"op": "decide_binding", "id": N, "ctx": {"id": ID, "question": str, "label": str, "event_title": str|null, "context": str?}}
  response {"status": "bound"|"unresolved", "class": str|null, "negated": bool, "missing": [str..], "probs": {..}?,
            "tokens_in": int?, "tokens_out": int?, "cost_usd": float?}  (probs: optional, see PROB_OPTIONS in base.py)

  request  {"op": "decide_relation", "id": N, "a": {ctx}, "b": {ctx}}
  response {"relation": "EQUIV"|"IMPLIES"|"IMPLIED_BY"|"EXCLUDES"|"NONE"|"UNRESOLVED", "conf": float?, "probs": {..}?,
            "tokens_in": int?, "tokens_out": int?, "cost_usd": float?}  (conf and probs: optional, see docs/local_protocol.md)

  error    {"error": str, "tokens_in": int?, "tokens_out": int?, "cost_usd": float?}
           is an AdapterError (retried by the gateway); any usage fields are still charged. The child
           stays alive. A response with valid JSON but invalid usage fields is an AdapterError that
           carries the valid usage fields (invalid ones count as 0), so the spend is not under-recorded.

  request  {"op": "decide_group", "id": N, "ctx": {"group_id": str, "source": str, "members": [{ctx}..]}}
  response {"groups": [group decision..], "probs": {..}?, "tokens_in": int?, "tokens_out": int?, "cost_usd": float?}
           (protocol 2 only, see docs/local_protocol.md; a group decision is {"kind", "members", "k"?,
           "direction"?, "scope"?} and is validated by adapters.base.check_group)

  request  {"op": "hello", "id": N, "protocol": 2}  (protocol 2 only; sent once when the adapter is built with
           protocol=2) answered by {"protocol": 2, "ops": {"decide_group": {"max_members": int?}}, ...}. An error
           answer leaves the adapter on protocol 1 with supports_groups False. Any other failure of the hello (a
           malformed answer, ops that are not an object, no decide_group) kills the child and falls back to protocol 1.
           An "ops" entry "batch" sets supports_batch; each op's "max_batch" (capped by batch.max_calls) goes to
           advertised_max_batch.

  request  {"op": "batch", "id": N, "calls": [{"op": ..., request fields}, ...]} (protocol 2, batch advertised)
  response {"results": [response fields or {"error": str}, ...], "tokens_in": int?, ...}
           decide_batch(calls) answers one entry per call: its decision, or an AdapterError for an item the child
           answered with an error. The whole call has ONE usage record with items = len(calls). A result count that
           differs from the calls, or a result that is not an object, is a protocol error (the whole batch fails).

Protocol failures kill the child's whole process tree and raise AdapterError: a closed stdout, a line that
is not a JSON object (NaN and Infinity are not JSON), a mismatched "id", a second line after a response,
no response within the timeout, or a stdin write that does not finish within the timeout. The next request
restarts the child. The restart budget counts consecutive failures: a valid response resets it. A child that
keeps failing (more than `max_restarts` restarts in a row) raises AdapterFatal, which stops the build. Children that do not echo "id" are checked only for extra lines that are already buffered
when the response arrives, so an extra line sent after the response is detected by the next request's
id check only when the child echoes ids. Stderr is inherited for diagnostics. Each usage record carries the
child's CPU seconds since the previous record (child_cpu_seconds) and its peak resident MB so far
(child_peak_rss_mb). The child is sampled at most once per proctree.SAMPLE_EVERY_S = 0.5 s: a response inside that
interval carries no child fields, and the totals cover the sampled intervals. The build's peak_rss_mb is still the parent
process only.
"""

from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from .base import PROB_OPTIONS, AdapterError, AdapterFatal, DecisionError, check_probs, check_usage, finite_number, usage
from .proctree import TreeMeter, kill_tree, start_kwargs

CLOSE_WAIT_S = 30.0  # how long close() waits for a child to exit after its input closed (a child's exit check may be a request)
_CTX_KEYS = ("id", "question", "label", "event_title")
_BATCH_OPS = ("decide_binding", "decide_relation", "decide_group")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _ctx(ctx: dict[str, Any]) -> dict[str, Any]:
    out = {k: ctx.get(k) for k in _CTX_KEYS}
    if ctx.get("context") is not None:  # optional rule card: omitted, not null, when the outcome has none
        out["context"] = ctx["context"]
    return out


def _body(op: str, args: tuple) -> dict[str, Any]:
    """Request fields of one single-call operation, from the arguments its adapter method takes."""
    if op == "decide_binding":
        return {"ctx": _ctx(args[0])}
    if op == "decide_relation":
        return {"a": _ctx(args[0]), "b": _ctx(args[1])}
    if op == "decide_group":
        group = args[0]
        return {"ctx": {"group_id": group["group_id"], "source": group["source"], "members": [_ctx(m) for m in group["members"]]}}
    raise ValueError(f"no request for op {op!r}")


class _Groups(list):
    """The groups of a decide_group answer: a list, as the harness reads it, with the answer's validated per-option
    probabilities (PROB_OPTIONS["group"]) as `probs`, None when the answer has none (or they were malformed)."""

    def __init__(self, groups: list[Any], probs: dict[str, float] | None):
        super().__init__(groups)
        self.probs = probs


def _silent(exc: DecisionError) -> None:
    """The default `dropped`: a malformed probs is dropped without a report."""


def _valid_probs(msg: dict[str, Any], options: tuple[str, ...], dropped: Callable[[DecisionError], None]) -> dict[str, float] | None:
    """The validated probs of a response, or None. Malformed probs are dropped, not an error: the decision is kept, and
    `dropped` is told why."""
    try:
        return check_probs(msg.get("probs"), options)
    except DecisionError as exc:
        dropped(exc)
        return None


def _decision(op: str, msg: dict[str, Any], dropped: Callable[[DecisionError], None] = _silent) -> Any:
    """The decision a response carries for its op: the relation (with its optional conf and probs), the groups (a
    _Groups), or the response itself (a binding, without its malformed probs). See check_relation_decision."""
    if op == "decide_relation":
        probs = _valid_probs(msg, PROB_OPTIONS["relation"], dropped)
        fields = {"conf": msg["conf"]} if "conf" in msg else {}
        if probs is not None:
            fields["probs"] = probs
        return {"relation": msg.get("relation"), **fields} if fields else msg.get("relation")
    if op == "decide_group":
        groups = msg.get("groups")
        if not isinstance(groups, list):
            return groups
        return _Groups(groups, _valid_probs(msg, PROB_OPTIONS["group"], dropped))
    if op == "decide_binding" and msg.get("probs") is not None:
        if _valid_probs(msg, PROB_OPTIONS["binding"], dropped) is None:
            return {k: v for k, v in msg.items() if k != "probs"}
    return msg


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token}")


def _pump(proc: subprocess.Popen, lines: queue.Queue) -> None:
    """Reader thread: queues each stdout line, then None (EOF) however the loop ends."""
    try:
        for line in proc.stdout:
            lines.put(line)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)


def _write(stdin: Any, data: str, errors: list[BaseException]) -> None:
    try:
        stdin.write(data)
        stdin.flush()
    except (OSError, ValueError) as exc:
        errors.append(exc)


class _ErrorResponse(AdapterError):
    """An {"error": ...} answer. The child is alive."""


class _Timeout(AdapterError):
    """No answer, or no read, within the timeout. The child was killed."""


class LocalJSONLAdapter:
    supports_timeout = True  # gateway passes the remaining build time as timeout_s per call

    def __init__(
        self, argv: list[str], name: str | None = None, timeout_s: float = 60.0, max_restarts: int = 3, protocol: int = 1,
        hello_timeout_s: float = 900.0,
    ):
        argv = list(argv)
        if not argv or not all(isinstance(a, str) and a for a in argv):
            raise ValueError("argv must be a non-empty list of strings naming the local program")
        if not (math.isfinite(timeout_s) and timeout_s > 0):
            raise ValueError("timeout_s must be a finite positive number of seconds")
        if not (math.isfinite(hello_timeout_s) and hello_timeout_s > 0):
            raise ValueError("hello_timeout_s must be a finite positive number of seconds")
        if max_restarts < 0:
            raise ValueError("max_restarts must be non-negative")
        if protocol not in (1, 2):
            raise ValueError("protocol must be 1 or 2")
        self.protocol = protocol
        self.hello_timeout_s = float(hello_timeout_s)
        self.supports_groups = False  # set by the hello handshake of protocol 2
        self.supplies_gold_refs = False  # a child is never gold-backed (its evidence is stripped of gold-only REFs)
        self.advertised_group_max_members: int | None = None  # from the hello handshake
        self.supports_batch = False  # set by the hello handshake of protocol 2 when it advertises the batch op
        self.advertised_max_batch: dict[str, int] = {}  # op -> largest batch the child takes (hello)
        self.probs_dropped = 0  # malformed probs dropped (the decision was kept); each one is printed on stderr
        # why groups are off: None while on; "flag" (protocol 1), or a hello outcome (hello_timeout, hello_error,
        # hello_failed, no_decide_group). Recorded in build_policy.json and the summary.
        self.groups_disabled_reason: str | None = "flag" if protocol == 1 else None
        self.hello_model: dict[str, Any] | None = None  # the child's hello "model" block (model id, revision, ...)
        self._argv = argv
        self.name = name or "local"  # the adapter kind, not the child program (build_policy.json, usage model)
        self.timeout_s = float(timeout_s)
        self._max_restarts = max_restarts
        self._restarts = 0
        self._seq = 0
        self._closed = False
        self._killed = False  # the current child was killed by a protocol failure (close does not judge its exit)
        self.exit_error: str | None = None  # set by close(): why the child's end fails the build (None: it ended cleanly)
        self._meter = TreeMeter()
        self._metered: tuple[float, float] | None = None  # (CPU seconds, peak MB) sampled after the last response
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._start()
        if protocol == 2:
            self._hello()

    def _hello(self) -> None:
        """Protocol 2 handshake, with its own timeout (hello_timeout_s: a child may load weights before it answers). An
        error answer leaves the child alive at protocol 1 with groups off (reason hello_error). A timeout (reason
        hello_timeout), any other failure (hello_failed), or a hello that is malformed or does not advertise
        decide_group (no_decide_group) kills the child and falls back to protocol 1 (the next request starts a fresh
        child). The reason is recorded; the build decides whether groups off is an error (see cli)."""
        try:
            msg = self._request({"op": "hello", "protocol": 2}, hello=True)
        except _ErrorResponse:
            self.groups_disabled_reason = "hello_error"
            return
        except _Timeout:
            self.protocol = 1
            self.groups_disabled_reason = "hello_timeout"
            return
        except Exception:  # noqa: BLE001 - any other handshake failure is a fallback, never a build crash
            self._kill()
            self.protocol = 1
            self.groups_disabled_reason = "hello_failed"
            return
        ops = msg.get("ops")
        spec = ops.get("decide_group") if isinstance(ops, dict) else None
        cap = spec.get("max_members") if isinstance(spec, dict) else None
        cap_ok = cap is None or (isinstance(cap, int) and not isinstance(cap, bool) and cap >= 2)
        if msg.get("protocol") == 2 and isinstance(spec, dict) and cap_ok:
            self.supports_groups = True
            self.groups_disabled_reason = None
            self.advertised_group_max_members = cap
            model = msg.get("model")
            self.hello_model = model if isinstance(model, dict) else None
            batch = ops.get("batch")
            if isinstance(batch, dict):
                self.supports_batch = True
                limit = batch.get("max_calls")
                for op in _BATCH_OPS:
                    spec_op = ops.get(op)
                    size = spec_op.get("max_batch") if isinstance(spec_op, dict) else None
                    if _positive_int(size):
                        self.advertised_max_batch[op] = min(size, limit) if _positive_int(limit) else size
            return
        self._kill()
        self.protocol = 1
        self.groups_disabled_reason = "no_decide_group"

    def _start(self) -> None:
        # no proxy variables reach the child (it must not use the network through a parent-configured proxy)
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        self._killed = False
        self._proc = subprocess.Popen(
            self._argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True,
            encoding="utf-8", errors="replace", bufsize=1, **start_kwargs(),
        )
        self._lines = queue.Queue()
        self._reader = threading.Thread(target=_pump, args=(self._proc, self._lines), daemon=True)
        self._reader.start()

    def _ensure_running(self) -> None:
        if self._closed:
            raise AdapterFatal(f"{self.name}: adapter is closed")
        if self._proc.poll() is None:
            return
        if self._restarts >= self._max_restarts:
            raise AdapterFatal(
                f"{self.name}: local process failed {self._restarts + 1} times (limit {self._max_restarts}); aborting"
            )
        self._restarts += 1
        try:
            self._start()
        except OSError as exc:
            raise AdapterFatal(f"{self.name}: cannot restart local process: {exc}") from exc

    def _kill(self) -> None:
        kill_tree(self._proc)
        self._killed = True
        self._reader.join(timeout=5)

    def _has_extra_line(self) -> bool:
        """True when a line beyond the response is buffered (the EOF sentinel does not count)."""
        with self._lines.mutex:
            return bool(self._lines.queue) and self._lines.queue[0] is not None

    def _usage(self, msg: dict[str, Any], items: int | None = None) -> dict[str, Any]:
        """Usage of a response (`items` is set for a batch). Invalid usage is an AdapterError carrying the valid fields."""
        record = usage(self._model(msg), "local", msg.get("tokens_in", 0), msg.get("tokens_out", 0), msg.get("cost_usd", 0.0))
        if items is not None:
            record["items"] = items
        if "gpu_seconds" in msg:
            record["gpu_seconds"] = msg["gpu_seconds"]
        self._add_metering(record)
        try:
            return check_usage(record)
        except AdapterError as exc:
            raise AdapterError(str(exc), usage=self._valid_usage(msg, items)) from None

    def _add_metering(self, record: dict[str, Any]) -> None:
        """The child's CPU and peak RSS sampled after the last response (optional usage fields, absent when sampling failed)."""
        if self._metered is not None:
            record["child_cpu_seconds"], record["child_peak_rss_mb"] = self._metered

    def _valid_usage(self, msg: dict[str, Any], items: int | None = None) -> dict[str, Any]:
        """The usage fields of `msg` that are valid numbers; invalid or missing ones count as 0."""
        def field(key: str, integer: bool) -> int | float:
            try:
                return finite_number(msg, key, integer)
            except AdapterError:
                return 0

        record = usage(self._model(msg), "local", field("tokens_in", True), field("tokens_out", True), field("cost_usd", False))
        if items is not None:
            record["items"] = items
        if "gpu_seconds" in msg:
            record["gpu_seconds"] = field("gpu_seconds", False)
        self._add_metering(record)
        return record

    def _model(self, msg: dict[str, Any]) -> str:
        """The usage model of a response: the child's optional "model" string, else local:<hello model id> when the
        hello named one, else the adapter kind."""
        model = msg.get("model")
        if isinstance(model, str) and model:
            return model
        hello_id = self.hello_model.get("id") if self.hello_model else None
        return f"{self.name}:{hello_id}" if isinstance(hello_id, str) and hello_id else self.name

    def _request(self, payload: dict[str, Any], timeout_s: float | None = None, hello: bool = False) -> dict[str, Any]:
        self._metered = None  # set only when this request gets a response (a killed child is not metered)
        self._ensure_running()
        base = self.hello_timeout_s if hello else self.timeout_s
        timeout = base if timeout_s is None else min(base, timeout_s)
        self._seq += 1
        try:
            wire = json.dumps({**payload, "id": self._seq}, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise AdapterError(f"request is not JSON-encodable: {exc}") from exc
        end = time.monotonic() + timeout
        errors: list[BaseException] = []
        writer = threading.Thread(target=_write, args=(self._proc.stdin, wire + "\n", errors), daemon=True)
        writer.start()
        writer.join(timeout)
        if writer.is_alive():
            self._kill()
            raise _Timeout(f"local process did not read the request within {timeout}s; killed")
        if errors:
            self._kill()
            raise AdapterError(f"writing to the local process failed: {errors[0]}")
        try:
            line = self._lines.get(timeout=max(end - time.monotonic(), 0.0))
        except queue.Empty:
            self._kill()
            raise _Timeout(f"local process gave no response within {timeout}s; killed") from None
        if line is None:
            self._kill()
            raise AdapterError("local process closed stdout")
        self._metered = self._meter.sample(self._proc.pid)
        try:
            msg = json.loads(line, parse_constant=_reject_constant)
            if not isinstance(msg, dict):
                raise ValueError("response is not a JSON object")
            if "id" in msg and msg.pop("id") != self._seq:
                raise ValueError(f"response id does not match request {self._seq}")
            if self._has_extra_line():
                raise ValueError("more than one line in response to a single request")
        except (ValueError, RecursionError) as exc:
            self._kill()
            raise AdapterError(f"protocol error from local process: {exc}") from exc
        self._restarts = 0  # a valid response: the child is healthy, so the consecutive-failure budget resets
        if "error" in msg:
            raise _ErrorResponse(str(msg["error"]), usage=self._valid_usage(msg))
        return msg

    def _drop_probs(self, exc: DecisionError) -> None:
        self.probs_dropped += 1
        print(f"{self.name}: dropped malformed probs, kept the decision: {exc}", file=sys.stderr)

    def decide_binding(self, outcome_context: dict[str, Any], timeout_s: float | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        msg = self._request({"op": "decide_binding", **_body("decide_binding", (outcome_context,))}, timeout_s)
        return _decision("decide_binding", msg, self._drop_probs), self._usage(msg)

    def decide_relation(
        self, a_context: dict[str, Any], b_context: dict[str, Any], timeout_s: float | None = None
    ) -> tuple[Any, dict[str, Any]]:
        msg = self._request({"op": "decide_relation", **_body("decide_relation", (a_context, b_context))}, timeout_s)
        return _decision("decide_relation", msg, self._drop_probs), self._usage(msg)

    def decide_group(self, group_ctx: dict[str, Any], timeout_s: float | None = None) -> tuple[Any, dict[str, Any]]:
        """Protocol 2 only: the child's list of group decisions (validated by the harness, not here), as a _Groups."""
        msg = self._request({"op": "decide_group", **_body("decide_group", (group_ctx,))}, timeout_s)
        return _decision("decide_group", msg, self._drop_probs), self._usage(msg)

    def decide_batch(self, calls: list[tuple[str, tuple]], timeout_s: float | None = None) -> tuple[list[Any], dict[str, Any]]:
        """Protocol 2 batch. `calls` are (op, args) with args as the single-call method takes them. Returns (outcomes,
        usage): outcomes[i] is the decision of calls[i] (see _decision) or an AdapterError for an item the child
        answered with an error, and usage is the one record of the whole call (items = len(calls)). Protocol errors
        (a result count that differs, a result that is not an object) kill the child and fail the whole batch."""
        if not calls:
            raise ValueError("a batch needs at least one call")
        ops = [op for op, _ in calls]
        msg = self._request({"op": "batch", "calls": [{"op": op, **_body(op, args)} for op, args in calls]}, timeout_s)
        results = msg.get("results")
        if not isinstance(results, list) or len(results) != len(calls):
            self._kill()
            got = len(results) if isinstance(results, list) else "no list"
            raise AdapterError(
                f"protocol error from local process: batch of {len(calls)} calls answered with {got} results",
                usage=self._valid_usage(msg, len(calls)),
            )
        if not all(isinstance(r, dict) for r in results):
            self._kill()
            raise AdapterError("protocol error from local process: batch result is not an object",
                               usage=self._valid_usage(msg, len(calls)))
        outcomes = [AdapterError(str(r["error"])) if "error" in r else _decision(op, r, self._drop_probs)
                    for op, r in zip(ops, results)]
        return outcomes, self._usage(msg, items=len(calls))

    def close(self) -> None:
        """Ends the child (stdin closed, then waited for up to CLOSE_WAIT_S; killed if it does not exit) and joins the reader.
        A child that exits nonzero after its input closed, or that had to be killed, sets exit_error (the build fails on
        it). A child killed by a protocol failure is not judged: that failure is already its error."""
        if self._closed:
            return
        self._closed = True
        if self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=CLOSE_WAIT_S)
            except subprocess.TimeoutExpired:
                kill_tree(self._proc)
                self.exit_error = f"did not exit within {CLOSE_WAIT_S:g} s after its input closed; it was killed"
            except (OSError, ValueError) as exc:
                kill_tree(self._proc)
                self.exit_error = f"could not be ended cleanly ({exc}); it was killed"
        if self.exit_error is None and not self._killed and self._proc.returncode != 0:
            self.exit_error = f"exited with status {self._proc.returncode} after its input closed"
        self._reader.join(timeout=5)

    def __enter__(self) -> "LocalJSONLAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

"""JSON-lines loop shared by the reference children (docs/local_protocol.md, version 2).

A child implements two methods:
  describe() -> {"name", "version", "model"}   the hello body; the loop adds protocol and ops
  decide(op, items) -> (results, usage)         one result per item: a response-fields dict or a ChildError.
                                                usage is {"tokens_in", "tokens_out", "cost_usd", optional
                                                "gpu_seconds", "model", "kind"} for the whole call.
`items` are validated request bodies: {"ctx"} for decide_binding and decide_group, {"a", "b"} for decide_relation.
An exception out of decide() fails the whole call; a ChildError fails one item and is isolated in a batch.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any, Iterable, TextIO

OPS = ("decide_binding", "decide_relation", "decide_group")
MAX_CALLS = 256
MAX_MEMBERS = 64
MAX_BATCH = {"decide_binding": 64, "decide_relation": 128, "decide_group": 16}
ADVERTISED_OPS = {
    "decide_binding": {"max_batch": MAX_BATCH["decide_binding"]},
    "decide_relation": {"max_batch": MAX_BATCH["decide_relation"]},
    "decide_group": {"max_batch": MAX_BATCH["decide_group"], "max_members": MAX_MEMBERS},
    "batch": {"max_calls": MAX_CALLS},
}


class ChildError(Exception):
    """One item failed: answered as {"error": message}; the other items of a batch are unaffected."""


def outcome(ctx: Any) -> dict[str, Any]:
    if not isinstance(ctx, dict):
        raise ChildError("ctx must be an object")
    oid, question, label, title = ctx.get("id"), ctx.get("question"), ctx.get("label"), ctx.get("event_title")
    context = ctx.get("context")  # optional rule card: absent from the outcome unless the request carries one
    if not isinstance(oid, str) or not oid:
        raise ChildError("ctx.id must be a non-empty string")
    if not isinstance(question, str) or not question.strip():
        raise ChildError("ctx.question must be a non-empty string")
    for name, value in (("label", label), ("event_title", title), ("context", context)):
        if value is not None and not isinstance(value, str):
            raise ChildError(f"ctx.{name} must be a string or null")
    out = {"id": oid, "question": question, "label": label, "event_title": title}
    if context is not None:
        out["context"] = context
    return out


def group_ctx(ctx: Any) -> dict[str, Any]:
    if not isinstance(ctx, dict):
        raise ChildError("group ctx must be an object")
    gid, source, members = ctx.get("group_id"), ctx.get("source"), ctx.get("members")
    if not isinstance(gid, str) or not isinstance(source, str):
        raise ChildError("group ctx needs string group_id and source")
    if not isinstance(members, list) or not 2 <= len(members) <= MAX_MEMBERS:
        raise ChildError(f"a group needs between 2 and {MAX_MEMBERS} members")
    rows = [outcome(m) for m in members]
    if len({r["id"] for r in rows}) != len(rows):
        raise ChildError("group members must have distinct ids")
    return {"group_id": gid, "source": source, "members": rows}


def _args(op: str, body: dict[str, Any]) -> dict[str, Any]:
    if op == "decide_binding":
        return {"ctx": outcome(body.get("ctx"))}
    if op == "decide_relation":
        return {"a": outcome(body.get("a")), "b": outcome(body.get("b"))}
    if op == "decide_group":
        return {"ctx": group_ctx(body.get("ctx"))}
    raise ChildError(f"unknown op {op!r}")


def _sum_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    total: dict[str, Any] = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "gpu_seconds": 0.0}
    for u in usages:
        total["tokens_in"] += u.get("tokens_in", 0)
        total["tokens_out"] += u.get("tokens_out", 0)
        total["cost_usd"] += u.get("cost_usd", 0.0)
        total["gpu_seconds"] += u.get("gpu_seconds", 0.0)
        for key in ("model", "kind"):
            if key in u:
                total.setdefault(key, u[key])
    return total


def _usage_fields(usage: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "tokens_in": usage.get("tokens_in", 0),
        "tokens_out": usage.get("tokens_out", 0),
        "cost_usd": usage.get("cost_usd", 0.0),
    }
    for key in ("gpu_seconds", "model", "kind"):
        if key in usage:
            fields[key] = usage[key]
    return fields


def _run(child: Any, op: str, items: list[dict[str, Any]]) -> tuple[list[Any], dict[str, Any]]:
    results, usage = child.decide(op, items)
    if len(results) != len(items):
        raise RuntimeError(f"{op} returned {len(results)} results for {len(items)} items")
    return results, usage


def _single(child: Any, op: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        args = _args(op, body)
    except ChildError as exc:
        return {"error": str(exc)}
    try:
        results, usage = _run(child, op, [args])
    except Exception as exc:  # noqa: BLE001 - a failed call is an error reply; the child stays alive
        return {"error": f"{type(exc).__name__}: {exc}"}
    result = results[0]
    if isinstance(result, ChildError):
        return {"error": str(result), **_usage_fields(usage)}
    return {**result, **_usage_fields(usage)}


def _batch(child: Any, calls: Any) -> dict[str, Any]:
    if not isinstance(calls, list):
        return {"error": "batch calls must be a list"}
    if len(calls) > MAX_CALLS:
        return {"error": f"batch has {len(calls)} calls; max_calls is {MAX_CALLS}"}
    prepared: list[tuple[str | None, Any]] = []
    for call in calls:
        op = call.get("op") if isinstance(call, dict) else None
        if op not in OPS:
            prepared.append((None, ChildError(f"unknown batch op {op!r}")))
            continue
        try:
            prepared.append((op, _args(op, call)))
        except ChildError as exc:
            prepared.append((op, exc))
    over = [op for op, n in Counter(op for op, _ in prepared if op).items() if n > MAX_BATCH[op]]
    if over:
        return {"error": f"batch has more calls of {over[0]} than max_batch {MAX_BATCH[over[0]]}"}
    results: list[Any] = [None] * len(calls)
    usages: list[dict[str, Any]] = []
    for op in OPS:
        idx = [i for i, (o, a) in enumerate(prepared) if o == op and not isinstance(a, ChildError)]
        if not idx:
            continue
        try:
            got, usage = _run(child, op, [prepared[i][1] for i in idx])
        except Exception as exc:  # noqa: BLE001 - a failed call fails the whole batch envelope
            # the op groups that completed before it were spent: their usage is still reported
            return {"error": f"{type(exc).__name__}: {exc}", **_usage_fields(_sum_usage(usages))}
        usages.append(usage)
        for i, result in zip(idx, got):
            results[i] = result
    for i, (_, item) in enumerate(prepared):
        if isinstance(item, ChildError):
            results[i] = item
    rows = [{"error": str(r)} if isinstance(r, ChildError) else r for r in results]
    return {"results": rows, **_usage_fields(_sum_usage(usages))}


def _hello(child: Any, body: dict[str, Any]) -> dict[str, Any]:
    if body.get("protocol") != 2:
        return {"error": "unsupported protocol; this child speaks protocol 2"}
    return {"protocol": 2, **child.describe(), "ops": ADVERTISED_OPS}


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token}")


def handle(child: Any, line: str) -> dict[str, Any]:
    """The reply to one request line. The request id, when present, is echoed first."""
    try:
        body = json.loads(line, parse_constant=_reject_constant)
    except ValueError as exc:
        return {"error": f"request is not JSON: {exc}"}
    if not isinstance(body, dict):
        return {"error": "request is not a JSON object"}
    op = body.get("op")
    if op == "hello":
        reply = _hello(child, body)
    elif op == "batch":
        reply = _batch(child, body.get("calls"))
    elif op in OPS:
        reply = _single(child, op, body)
    else:
        reply = {"error": f"unknown op {op!r}"}
    if "id" in body:
        reply = {"id": body["id"], **reply}
    return reply


def serve(child: Any, stdin: Iterable[str] | None = None, stdout: TextIO | None = None) -> None:
    """Answer requests line by line until stdin closes."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    for line in stdin:
        if not line.strip():
            continue
        reply = handle(child, line)
        try:
            wire = json.dumps(reply, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            wire = json.dumps({"error": f"reply is not JSON-encodable: {exc}"})
        stdout.write(wire + "\n")
        stdout.flush()

"""Conformance runner for version-2 children: python -m mentat_bench.children.conformance <child argv...>

Spawns the child (PYTHONUTF8=1), then sends hello, golden requests for the three decision ops, an unknown op and one
batch whose item 3 is malformed. Every answer is checked with adapters.base (check_binding, check_relation,
check_group), the batch envelope rules and the usage fields. Prints one line per check and a summary.
Exit status: 0 when every check passes, 1 otherwise, 2 for a usage error.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from typing import Any

from ..adapters.base import PROB_OPTIONS, AdapterError, DecisionError, check_binding, check_group, check_probs, check_relation, finite_number
from .protocol import ADVERTISED_OPS

TIMEOUT_S = 900.0  # a model child loads its weights before it answers hello


def _ctx(oid: str, question: str, label: str | None, title: str | None, context: str | None = None) -> dict[str, Any]:
    return {"id": oid, "question": question, "label": label, "event_title": title,
            **({"context": context} if context is not None else {})}


BINDINGS = [
    _ctx("g:bin:1", "Will Example Coin close above 100 on June 1, 12PM ET?", "Yes", "Example Coin above ___ on June 1",
         "Rule card: a coin closes above a threshold when its June 1 noon ET price is above it."),
    _ctx("g:bin:2", "Will Example Coin close above 100 on June 1, 12PM ET?", "No", "Example Coin above ___ on June 1"),
    _ctx("g:bin:3", "Will Example Coin rise soon?", "Yes", "Example Coin"),
]
RELATION_PAIRS = [
    (BINDINGS[0], BINDINGS[1]),
    (_ctx("g:rel:1", "Will Example Coin close above 120 on June 1, 12PM ET?", "Yes", "Example Coin above ___ on June 1"),
     _ctx("g:rel:2", "Will Example Coin close above 90 on June 1, 12PM ET?", "Yes", "Example Coin above ___ on June 1")),
]
GROUP = {
    "group_id": "g:c1",
    "source": "s1",
    "members": [
        _ctx("g:m:1", "Who wins the Example Cup?", "Alpha", "Example Cup",
             "Rule card: the Example Cup goes to the team that wins the most matches in June."),
        _ctx("g:m:2", "Who wins the Example Cup?", "Beta", "Example Cup"),
        _ctx("g:m:3", "Who wins the Example Cup?", "Gamma", "Example Cup"),
    ],
}


class _Child:
    """One child process speaking JSON lines; a reader thread queues its stdout lines."""

    def __init__(self, argv: list[str]):
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1)
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.seq = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def ask(self, body: dict[str, Any]) -> dict[str, Any]:
        self.seq += 1
        self.proc.stdin.write(json.dumps({**body, "id": self.seq}, ensure_ascii=True, sort_keys=True) + "\n")
        self.proc.stdin.flush()
        line = self.lines.get(timeout=TIMEOUT_S)
        if line is None:
            raise AdapterError("child closed stdout")
        reply = json.loads(line)
        if not isinstance(reply, dict):
            raise AdapterError("reply is not a JSON object")
        if reply.get("id") != self.seq:
            raise AdapterError(f"reply id {reply.get('id')!r} does not echo request id {self.seq}")
        return reply

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


class _Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn: Any) -> Any:
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 - any failure of a check is a FAIL line, not a crash
            self.results.append((name, False, f"{type(exc).__name__}: {exc}"))
            return None
        self.results.append((name, True, ""))
        return value

    def print(self) -> int:
        for name, ok, detail in self.results:
            print(f"{'PASS' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail else ""))
        passed = sum(1 for _, ok, _ in self.results if ok)
        print(f"conformance: {passed}/{len(self.results)} checks passed")
        return 0 if passed == len(self.results) else 1


def _no_error(reply: dict[str, Any]) -> dict[str, Any]:
    if "error" in reply:
        raise AdapterError(f"child returned error: {reply['error']}")
    return reply


def _usage(reply: dict[str, Any]) -> None:
    """Token counts are non-negative integers and cost is finite and non-negative, when present."""
    for key in ("tokens_in", "tokens_out"):
        if key in reply:
            finite_number(reply, key, integer=True)
    for key in ("cost_usd", "gpu_seconds"):
        if key in reply:
            finite_number(reply, key)


def _binding_ok(reply: dict[str, Any]) -> None:
    _usage(reply)
    check_binding(reply)
    check_probs(reply.get("probs"), PROB_OPTIONS["binding"])


def _relation_ok(reply: dict[str, Any]) -> None:
    _usage(reply)
    check_relation(reply.get("relation"))
    check_probs(reply.get("probs"), PROB_OPTIONS["relation"])


def _group_ok(reply: dict[str, Any]) -> None:
    _usage(reply)
    groups = reply.get("groups")
    if not isinstance(groups, list):
        raise DecisionError("groups must be a list")
    check_probs(reply.get("probs"), PROB_OPTIONS["group"])
    member_ids = {m["id"] for m in GROUP["members"]}
    for decision in groups:
        check_group(decision, member_ids)


def run(argv: list[str]) -> int:
    report = _Report()
    child = _Child(argv)
    try:
        hello = report.check("hello (protocol 2)", lambda: _no_error(child.ask({"op": "hello", "protocol": 2})))
        if hello is not None:
            def hello_ok() -> None:
                if hello.get("protocol") != 2 or not isinstance(hello.get("name"), str) or not hello["name"]:
                    raise DecisionError("hello needs protocol 2 and a name")
                ops = hello.get("ops")
                if not isinstance(ops, dict) or not set(ADVERTISED_OPS) <= set(ops):
                    raise DecisionError(f"hello ops must include {sorted(ADVERTISED_OPS)}")
                cap = ops["decide_group"].get("max_members") if isinstance(ops["decide_group"], dict) else None
                if not isinstance(cap, int) or isinstance(cap, bool) or cap < 2:
                    raise DecisionError("decide_group needs max_members >= 2")
            report.check("hello advertises ops", hello_ok)
        for n, ctx in enumerate(BINDINGS, 1):
            reply = report.check(f"decide_binding golden {n}", lambda ctx=ctx: _no_error(child.ask(
                {"op": "decide_binding", "ctx": ctx})))
            if reply is not None:
                report.check(f"decide_binding golden {n} is a valid decision", lambda reply=reply: _binding_ok(reply))
        for n, (a, b) in enumerate(RELATION_PAIRS, 1):
            reply = report.check(f"decide_relation golden {n}", lambda a=a, b=b: _no_error(child.ask(
                {"op": "decide_relation", "a": a, "b": b})))
            if reply is not None:
                report.check(f"decide_relation golden {n} is a valid decision", lambda reply=reply: _relation_ok(reply))
        reply = report.check("decide_group golden", lambda: _no_error(child.ask({"op": "decide_group", "ctx": GROUP})))
        if reply is not None:
            report.check("decide_group golden is a valid decision", lambda: _group_ok(reply))
        report.check("unknown op is an error reply", lambda: _is_error(child.ask({"op": "decide_nothing"})))
        calls = [
            {"op": "decide_binding", "ctx": BINDINGS[0]},
            {"op": "decide_relation", "a": RELATION_PAIRS[0][0], "b": RELATION_PAIRS[0][1]},
            {"op": "decide_group", "ctx": GROUP},
            {"op": "decide_binding", "ctx": {"id": "g:bad", "question": "", "label": "Yes", "event_title": None}},
            {"op": "decide_binding", "ctx": BINDINGS[1]},
        ]
        batch = report.check("batch of 5 with one malformed item", lambda: _no_error(child.ask({"op": "batch", "calls": calls})))
        if batch is not None:
            report.check("batch envelope: results count equals calls count", lambda: _batch_count(batch, len(calls)))
            report.check("batch envelope: usage fields", lambda: _usage(batch))
            report.check("batch item 3 is an isolated error", lambda: _is_error(batch["results"][3]))
            report.check("batch item 1 is a valid binding", lambda: _binding_ok(batch["results"][0]))
            report.check("batch item 2 is a valid relation", lambda: _relation_ok(batch["results"][1]))
            report.check("batch item 3 group is a valid decision", lambda: _group_ok(batch["results"][2]))
            report.check("batch item 5 is a valid binding", lambda: _binding_ok(batch["results"][4]))
    finally:
        child.close()
    return report.print()


def _is_error(reply: Any) -> None:
    if not isinstance(reply, dict) or not isinstance(reply.get("error"), str):
        raise DecisionError(f"expected an error reply, got {reply!r}")


def _batch_count(batch: dict[str, Any], calls: int) -> None:
    results = batch.get("results")
    if not isinstance(results, list) or len(results) != calls:
        raise DecisionError(f"results must be a list of {calls} entries")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m mentat_bench.children.conformance <child argv...>", file=sys.stderr)
        return 2
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())

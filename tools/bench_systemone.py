#!/usr/bin/env python
"""Throughput of the System One child on one Ollama server, from a fixture's public corpus. Not part of the test suite.

    PYTHONPATH=python MB_S1_MODEL=<tag> [MB_S1_URL=...] python tools/bench_systemone.py --fixture fixtures/toy10k --n 200

The samples are seeded and read from corpus.jsonl only (outcome text, label and event title, the fields the adapter
sends; no gold is read): N bindings, N relations (anchors with 16 partners each, one request per pair) and N group candidate
sets (2 to 8 outcomes of one event). Each op runs once through SystemOneChild.decide, timed, and every request is timed
too. Prints one JSON object: the child's describe(), then per op the items, items/s, requests, request p50 and p95 seconds,
item errors, the 400s the server refused, and the token counts.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from mentat_bench.children.protocol import ChildError
from mentat_bench.children.systemone_child import SystemOneChild

PARTNERS = 16
GROUP_MAX = 8


def load_outcomes(corpus: Path) -> list[dict[str, Any]]:
    rows = []
    with corpus.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                row = {"id": rec["id"], "question": rec["text"], "label": rec.get("label"),
                       "event_title": rec.get("event_title")}
                if rec.get("context") is not None:  # the optional rule card, left out (not null) when the row has none
                    row["context"] = rec["context"]
                rows.append(row)
    return rows


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[min(len(ordered), math.ceil(q * len(ordered))) - 1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bench the System One child on one Ollama server.")
    parser.add_argument("--fixture", type=Path, default=Path("fixtures/toy10k"))
    parser.add_argument("--n", type=int, default=200, help="items per op")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    outcomes = load_outcomes(args.fixture / "corpus.jsonl")
    child = SystemOneChild.from_env()  # the pin check runs here, as in the child

    requests: list[float] = []
    post = child._post

    def timed_post(body: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return post(body)
        finally:
            requests.append(time.perf_counter() - started)

    child._post = timed_post  # on this instance only

    bindings = [{"ctx": c} for c in rng.sample(outcomes, min(args.n, len(outcomes)))]
    anchors = rng.sample(outcomes, min(len(outcomes), math.ceil(args.n / PARTNERS)))
    relations = [{"a": a, "b": b} for a in anchors for b in rng.sample(outcomes, PARTNERS)][:args.n]
    by_event: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        by_event[outcome["event_title"]].append(outcome)
    events = [members for members in by_event.values() if len(members) >= 2]
    groups = [{"ctx": {"group_id": f"g{i}", "source": "s2", "members": members[:GROUP_MAX]}}
              for i, members in enumerate(rng.sample(events, min(args.n, len(events))))]

    report: dict[str, Any] = {"describe": child.describe(), "fixture": args.fixture.name, "seed": args.seed,
                              "parallel": child.parallel, "ops": {}}
    for op, items in (("decide_binding", bindings), ("decide_relation", relations), ("decide_group", groups)):
        if not items:
            continue
        requests.clear()
        refused_before = child.rejected_400
        started = time.perf_counter()
        results, usage = child.decide(op, items)
        elapsed = time.perf_counter() - started
        report["ops"][op] = {
            "items": len(items), "seconds": round(elapsed, 3), "items_per_s": round(len(items) / elapsed, 3),
            "requests": len(requests), "request_p50_s": round(percentile(requests, 0.5), 4),
            "request_p95_s": round(percentile(requests, 0.95), 4),
            "item_errors": sum(isinstance(r, ChildError) for r in results),
            "rejected_400": child.rejected_400 - refused_before,
            "tokens_in": usage["tokens_in"], "tokens_out": usage["tokens_out"],
        }
    report["abstentions"] = dict(child.abstentions)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

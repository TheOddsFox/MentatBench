#!/usr/bin/env python
"""Bench and decision diff of the HF child on a fixed sample of a kept build. Not part of the test suite.

Bench (run against any checkout with PYTHONPATH=<checkout>/python; the model comes from MB_HF_* as for the child):
    python tools/bench_hf_child.py --work <copy of a submission.build> --out <dir> [--batch 64]
Diff of two bench outputs (sample and header must match):
    python tools/bench_hf_child.py --compare <A/decisions.jsonl> <B/decisions.jsonl>

The sample is seeded and read from the build copy: relation pairs from pairs.bin (record indices into records.jsonl),
binding items from records.jsonl, and group items from groups.jsonl. The group items are the member sets of admitted
group decisions of the build, not the candidate population (distinct sets of 2 to 64 distinct ids), so they check
decisions, not throughput: decide_group is timed for completeness and is marked throughput_claim false. Every item is
the harness's own outcome context (fixed._context, the fields the adapter sends), so the bench feeds the child what the
adapter sends. No gold is read. Each op runs in chunks of --batch items through HFChild.decide after one untimed
warm-up chunk. Timed per op: items/s (relations are the pairs), scoring chunks per call and per item (a chunk is one head pass and
one tail pass), synchronised gpu seconds, tokenizer seconds (the tokenizer's __call__ and apply_chat_template), prep
seconds (call wall time minus gpu seconds), and CUDA peak allocated and reserved memory.
decisions.jsonl: a header line (sample sha256, the child's describe() with its batch budgets and halvings), then one
line per item: op, key, result, and the option scores per stage (the rounds of an order stage are
order:0, order:1, ...) (after calibration, the values the decision is the
argmax of). --compare prints both headers' budgets next to the flips.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from array import array
from collections import Counter
from pathlib import Path
from typing import Any

# torch's allocator default, as in the child (hf_child sets it on the same condition): it must be set before torch.
import os

if sys.platform != "win32":
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch  # noqa: E402

from mentat_bench.children import hf_child as HF  # noqa: E402
from mentat_bench.children.common import chain_members  # noqa: E402
from mentat_bench.harness.fixed import _context  # noqa: E402 - the outcome context the adapter sends

OPS = ("decide_relation", "decide_binding", "decide_group")


def read_jsonl(path: Path) -> list[Any]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def payload_key(payload: Any) -> str:
    """Key of an item from its decision payload: a binding ctx, a relation (a, b) pair or a candidate set's members."""
    if isinstance(payload, dict):
        return payload["id"]
    if isinstance(payload, tuple):
        return f"{payload[0]['id']}|{payload[1]['id']}"
    return "+".join(m["id"] for m in payload)


def item_payload(op: str, item: dict[str, Any]) -> Any:
    if op == "decide_relation":
        return (item["a"], item["b"])
    if op == "decide_binding":
        return item["ctx"]
    return item["ctx"]["members"]


def sample(work: Path, seed: int, n_pairs: int, n_bindings: int, n_groups: int) -> tuple[dict[str, list], str]:
    store = read_jsonl(work / "records.jsonl")
    raw = array("I")
    raw.frombytes((work / "pairs.bin").read_bytes())
    total = len(raw) // 2
    picked = sorted(random.Random(seed).sample(range(total), min(n_pairs, total)))
    relations = [{"a": _context(store[raw[2 * k]]), "b": _context(store[raw[2 * k + 1]])} for k in picked]
    chosen = sorted(random.Random(seed + 1).sample(range(len(store)), min(n_bindings, len(store))))
    bindings = [{"ctx": _context(store[i])} for i in chosen]
    # Admitted group decisions: distinct member sets of 2 to 64 distinct ids (the adapter's candidate limit).
    rows = [row for row in {tuple(r["members"]): r for r in read_jsonl(work / "groups.jsonl")}.values()
            if 2 <= len(row["members"]) <= 64 and len({store[m]["id"] for m in row["members"]}) == len(row["members"])]
    chosen = sorted(random.Random(seed + 2).sample(range(len(rows)), min(n_groups, len(rows))))
    groups = [{"ctx": {"group_id": f"c{k:07d}", "source": rows[k]["source"],
                       "members": [_context(store[m]) for m in rows[k]["members"]]}} for k in chosen]
    blob = json.dumps({"relation": relations, "binding": bindings, "group": groups}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True)
    return {"decide_relation": relations, "decide_binding": bindings, "decide_group": groups}, \
        hashlib.sha256(blob.encode("utf-8")).hexdigest()


class TimedTokenizer:
    """The child's tokenizer with its __call__ and apply_chat_template timed (everything else is forwarded)."""

    def __init__(self, tokenizer: Any, probe: "Probe") -> None:
        self._tokenizer = tokenizer
        self._probe = probe

    def _timed(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self._probe.tokenizer_seconds += time.perf_counter() - started

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._timed(self._tokenizer, *args, **kwargs)

    def apply_chat_template(self, *args: Any, **kwargs: Any) -> Any:
        return self._timed(self._tokenizer.apply_chat_template, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tokenizer, name)


class Probe:
    """Counts scoring chunks (one head pass and one tail pass each) and tokenizer time, and keeps the option scores of every
    stage, keyed by (op, item)."""

    def __init__(self, child: HF.HFChild) -> None:
        self.forwards = 0
        self.tokenizer_seconds = 0.0
        self.op = ""
        self.batch: list[Any] = []  # the items of the decide call in progress: a stage's index i is an index into them
        self.scores: dict[tuple[str, str], dict[str, list[float]]] = {}
        self.rounds: Counter = Counter()  # order-stage rounds per (op, item): each round is its own stage ("order:<round>")
        forward_tails, scores = child._forward_tails, child._scores

        def counted(fn: Any) -> Any:
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                self.forwards += 1
                return fn(*args, **kwargs)
            return wrapped

        def stage_scores(results: Any, idx: Any, stage: str, payload_of: Any, options_of: Any,
                         size_of: Any = None) -> Any:
            out = scores(results, idx, stage, payload_of, options_of, size_of)
            for i, vals in out.items():  # i indexes the batch of the call, for every stage (a chain's rounds too)
                key = (self.op, payload_key(item_payload(self.op, self.batch[i])))
                name = stage
                if stage == "order":  # the rounds of one chain are separate stages, so none overwrites another
                    name = f"order:{self.rounds[key]}"
                    self.rounds[key] += 1
                self.scores.setdefault(key, {})[name] = list(vals)
            return out

        child._forward_tails = counted(forward_tails)
        child._scores = stage_scores
        child.tokenizer = TimedTokenizer(child.tokenizer, self)

    def reset(self) -> None:
        self.forwards = 0
        self.tokenizer_seconds = 0.0
        self.scores.clear()
        self.rounds.clear()


def run_bench(child: HF.HFChild, probe: Probe, samples: dict[str, list], batch: int) -> tuple[dict, list[dict]]:
    dev = child.device
    cuda = dev.startswith("cuda")
    probe.op = OPS[0]
    probe.batch = samples[OPS[0]][:batch]
    child.decide(OPS[0], probe.batch)  # warm-up, untimed
    probe.reset()
    if cuda:
        torch.cuda.synchronize(dev)
        torch.cuda.reset_peak_memory_stats(dev)
    report: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    for op in OPS:
        items = samples[op]
        probe.op = op
        probe.forwards = 0
        probe.tokenizer_seconds = 0.0
        gpu = call_seconds = 0.0
        results: list[Any] = []
        calls = 0
        for start in range(0, len(items), batch):
            probe.batch = items[start:start + batch]
            started = time.perf_counter()
            got, usage = child.decide(op, probe.batch)
            call_seconds += time.perf_counter() - started
            gpu += usage["gpu_seconds"]
            results.extend(got)
            calls += 1
        report[op] = {
            "throughput_claim": op != "decide_group",
            "items": len(items), "calls": calls, "seconds": round(call_seconds, 4),
            "items_per_s": round(len(items) / call_seconds, 3), "forwards": probe.forwards,
            "forwards_per_call": round(probe.forwards / calls, 3), "forwards_per_item": round(probe.forwards / len(items), 4),
            "gpu_seconds": round(gpu, 4), "tokenizer_seconds": round(probe.tokenizer_seconds, 4),
            "prep_seconds": round(call_seconds - gpu, 4),
            "prep_fraction": round((call_seconds - gpu) / call_seconds, 4),
            "errors": sum(1 for r in results if isinstance(r, Exception)),
        }
        for item, result in zip(items, results):
            key = payload_key(item_payload(op, item))
            if isinstance(result, Exception):
                result = {"error": str(result)}
            records.append({"op": op, "key": key, "result": result, "scores": probe.scores.get((op, key), {})})
    if cuda:
        torch.cuda.synchronize(dev)
        allocated, reserved = torch.cuda.max_memory_allocated(dev), torch.cuda.max_memory_reserved(dev)
        report["memory"] = {"max_allocated_bytes": allocated, "max_reserved_bytes": reserved,
                            "reserved_over_allocated": round(reserved / allocated, 4)}
    return report, records


SPLIT_STAGES = {
    "decide_relation": [("relation", lambda it: (it["a"], it["b"], HF.relation_order(it["a"], it["b"])))],
    "decide_binding": [(stage, lambda it: it["ctx"]) for stage in ("status", "negated", "missing")],
    "decide_group": [("kind", lambda it: it["ctx"]["members"]), ("k", lambda it: it["ctx"]["members"]),
                     ("order", lambda it: chain_members(it["ctx"]["members"])[:len(HF.ORDER_LETTERS)])],  # the menu size
}


def split_check(child: HF.HFChild, samples: dict[str, list]) -> dict[str, Any]:
    """Untimed: every item's prompt ids from the child's (split) path against the full-chat ids, for each stage its op
    runs. A mismatch is a bug in the split path, not a bench number."""
    report: dict[str, Any] = {}
    for op, items in samples.items():
        for stage, payload_of in SPLIT_STAGES[op]:
            payloads = [payload_of(item) for item in items]
            mismatches = 0
            for payload, got in zip(payloads, child._prompts(stage, payloads)):
                try:
                    want: list[int] | None = child._prompt_ids(HF._messages(stage, payload))
                except HF.ChildError:
                    want = None
                mismatches += (None if isinstance(got, HF.ChildError) else got) != want
            report[stage] = {"items": len(payloads), "mismatches": mismatches, "split": child._split[stage]}
    return report


def decision_of(result: dict[str, Any]) -> dict[str, Any]:
    """The decision without its confidence (the margin is compared through the scores)."""
    if "groups" in result:
        return {"groups": [{k: v for k, v in g.items() if k != "conf"} for g in result["groups"]]}
    return {k: v for k, v in result.items() if k != "conf"}


def margin(vals: list[float]) -> float | None:
    top = sorted(vals, reverse=True)
    return top[0] - top[1] if len(top) > 1 else None


def centre(vals: list[float]) -> list[float]:
    mean = sum(vals) / len(vals)
    return [v - mean for v in vals]


def compare(a_path: Path, b_path: Path) -> dict[str, Any]:
    a, b = read_jsonl(a_path), read_jsonl(b_path)
    if a[0]["sample_sha256"] != b[0]["sample_sha256"]:
        raise SystemExit(f"different samples: {a[0]['sample_sha256']} vs {b[0]['sample_sha256']}")
    rows_a = {(r["op"], r["key"]): r for r in a[1:]}
    rows_b = {(r["op"], r["key"]): r for r in b[1:]}
    flips, argmax_flips, max_diff, stage_gaps = [], 0, 0.0, 0
    for k in rows_a.keys() & rows_b.keys():
        ra, rb = rows_a[k], rows_b[k]
        for stage in ra["scores"].keys() & rb["scores"].keys():
            va, vb = ra["scores"][stage], rb["scores"][stage]
            if stage == "relation":  # raw logits (new) and log-probabilities (old) differ by one constant per item
                va, vb = centre(va), centre(vb)
            diff = max(abs(x - y) if math.isfinite(x) and math.isfinite(y) else math.inf for x, y in zip(va, vb))
            max_diff = max(max_diff, diff)
            if va.index(max(va)) != vb.index(max(vb)):
                argmax_flips += 1
        stage_gaps += len(ra["scores"].keys() ^ rb["scores"].keys())
        if decision_of(ra["result"]) != decision_of(rb["result"]):
            flips.append({"op": k[0], "key": k[1], "A": ra["result"], "B": rb["result"],
                          "margins_A": {s: margin(v) for s, v in ra["scores"].items()},
                          "margins_B": {s: margin(v) for s, v in rb["scores"].items()}})
    budgets = {name: h.get("child", {}).get("limits") for name, h in (("A", a[0]), ("B", b[0]))}  # None before v7
    return {"sample_sha256": a[0]["sample_sha256"], "budgets": budgets, "compared": len(rows_a.keys() & rows_b.keys()),
            "only_in_A": len(rows_a.keys() - rows_b.keys()), "only_in_B": len(rows_b.keys() - rows_a.keys()),
            "decision_flips": len(flips), "stage_argmax_flips": argmax_flips,
            "stage_scores_missing_on_one_side": stage_gaps, "max_abs_score_diff": max_diff, "flips": flips}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work", type=Path, help="a copy of a submission.build (pairs.bin, records.jsonl, groups.jsonl)")
    parser.add_argument("--out", type=Path, help="directory for decisions.jsonl and summary.json")
    parser.add_argument("--batch", type=int, default=64, help="items per decide call (default 64)")
    parser.add_argument("--pairs", type=int, default=2000, help="relation pairs in the sample (default 2000)")
    parser.add_argument("--bindings", type=int, default=200, help="binding items in the sample (default 200)")
    parser.add_argument("--groups", type=int, default=50, help="group candidate sets in the sample (default 50)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("A", "B"), help="diff two decisions.jsonl files")
    args = parser.parse_args(argv)
    if args.compare:
        print(json.dumps(compare(*args.compare), indent=2, sort_keys=True))
        return
    if args.work is None or args.out is None:
        parser.error("--work and --out are required unless --compare is given")
    samples, sha = sample(args.work, args.seed, args.pairs, args.bindings, args.groups)
    child = HF.HFChild.from_env()
    probe = Probe(child)
    report, records = run_bench(child, probe, samples, args.batch)
    if hasattr(child, "_prompts"):  # checkouts before the split tokenization have no split path to check
        report["split_check"] = split_check(child, samples)
    args.out.mkdir(parents=True, exist_ok=True)
    child_info = child.describe()  # after the run: the budgets in effect and the halvings that happened
    header = {"sample_sha256": sha, "batch": args.batch, "seed": args.seed, "code": HF.__file__,
              "child": child_info, "model": child_info["model"],
              "counts": {op: len(items) for op, items in samples.items()}}
    with (args.out / "decisions.jsonl").open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(header, sort_keys=True) + "\n")
        for record in records:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    summary = {**header, **report}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True), file=sys.stdout)


if __name__ == "__main__":
    main()

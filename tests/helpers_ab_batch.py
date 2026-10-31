"""Batched reference build for the default flags: the call sequence, the usage entries and the theory of a build with a
batching adapter whose checkpoint windows (checkpoint_every 7) do not match its batch sizes (4 and 3), with complement
blocks that straddle a window. The body uses only the public build API, so the same run on an export of main pins the
reference (tests/ab_batch_reference.json). Run: python helpers_ab_batch.py OUT_JSON WORK_DIR (PYTHONPATH selects the tree)."""

import hashlib
import json
import sys
from pathlib import Path

from mentat_bench.adapters.base import AdapterError, usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

RELATIONS = ("IMPLIES", "NONE", "EQUIV", "EXCLUDES", "UNRESOLVED", "NONE")
MARKETS = 6  # binary markets bound as complements (two records, one class, opposite negation); one more is unresolved


def _h(*parts):
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest(), 16)


def records():
    out = []
    for k in range(MARKETS + 1):
        for n, label in ((1, "Yes"), (2, "No")):
            out.append({"id": f"v:m{k}:{n}", "question": "Will Ember win the cup?", "label": label, "event_title": "E"})
    return out


class BatchChild:
    """Decides in batches only (the single methods answer the same way); records every batch call as (op, item ids)."""

    name = "batch-child"
    supports_batch = True
    advertised_max_batch = {"decide_binding": 4, "decide_relation": 3}

    def __init__(self):
        self.calls = []

    def _binding(self, ctx):
        k = int(ctx["id"].split(":")[1][1:])
        if k == MARKETS:
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
        return {"status": "bound", "class": f"c{k}", "negated": ctx["label"] == "No", "missing": []}

    def _relation(self, a, b):
        return RELATIONS[_h(a["id"], b["id"]) % len(RELATIONS)]

    def decide_binding(self, ctx):
        return self._binding(ctx), usage(self.name)

    def decide_relation(self, a, b):
        return self._relation(a, b), usage(self.name)

    def decide_batch(self, calls):
        outcomes, ids = [], []
        for op, args in calls:
            if op == "decide_binding":
                outcomes.append(self._binding(args[0]))
                ids.append([args[0]["id"]])
            else:
                outcomes.append(self._relation(args[0], args[1]))
                ids.append([args[0]["id"], args[1]["id"]])
        self.calls.append([[op, item] for (op, _), item in zip(calls, ids)])
        return outcomes, usage(self.name, "local", len(calls), 1, 0.0)


class FailOnce(BatchChild):
    """A batch that holds the given relation pair fails once with a transient AdapterError (the gateway retries it alone)."""

    def __init__(self, pair):
        super().__init__()
        self.pair, self.failed = pair, False

    def decide_batch(self, calls):
        if not self.failed and any(op == "decide_relation" and {args[0]["id"], args[1]["id"]} == set(self.pair)
                                   for op, args in calls):
            self.failed = True
            raise AdapterError("transient", usage=usage(self.name, "local", len(calls), 1, 0.0))
        return super().decide_batch(calls)


def run(out: Path) -> dict:
    """The reference of one build: the batch calls, the usage entries, the summary counts and the theory's digests."""
    adapter = BatchChild()
    summary = fixed.build(records(), adapter, out / "sub", dict(DEFAULT_PROFILE), checkpoint_every=7, max_relation_drop_frac=1.0)
    doc = json.loads((out / "sub" / "usage.json").read_text(encoding="utf-8"))
    return {
        "sequence": adapter.calls,
        "usage": [[call.get("items", 1), call.get("retry", False)] for call in doc["calls"]],
        "summary": {key: summary[key] for key in ("candidate_pairs", "relations_committed", "pairs_derived",
                                                  "pairs_derived_none", "dedup_fallback_asked", "items", "calls",
                                                  "constraints", "relations_dropped")},
        "files": {name: hashlib.sha256((out / "sub" / name).read_bytes()).hexdigest()
                  for name in ("theory.json", "bindings.jsonl", "provenance.jsonl")},
    }


if __name__ == "__main__":
    result = run(Path(sys.argv[2]))
    Path(sys.argv[1]).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("wrote", sys.argv[1], "batches", len(result["sequence"]), "usage entries", len(result["usage"]))

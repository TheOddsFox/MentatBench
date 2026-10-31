"""Relation-phase throughput does not degrade as relations accumulate. The synthetic adapter has the 1.5B pattern: complement
markets whose representatives get named answers (so most complement pairs are derived, not asked), and it is cheap, so
the harness's own bookkeeping is what is timed. Stops before finalize: only phases 1-3 run."""

import time
import zlib

import pytest

from mentat_bench.adapters.base import usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

ANSWERS = ("IMPLIES", "IMPLIES", "EXCLUDES", "NONE")


class _Stop(Exception):
    pass


class _Cheap:
    """Binary markets (Yes/No of one question) bound as complements; a named answer for most representatives. Times
    every relation decision by index."""

    name = "cheap"
    supports_batch = True
    advertised_max_batch = {"decide_binding": 64, "decide_relation": 64}

    def __init__(self):
        self.calls = 0
        self.stamps = []  # (decision index, seconds since the first decision)
        self.t0 = None

    def decide_binding(self, ctx):
        bound = {"status": "bound", "class": "c" + ctx["question"], "negated": ctx["label"] == "No", "missing": []}
        return bound, usage(self.name)

    def decide_relation(self, a, b):
        self.calls += 1
        if self.t0 is None:
            self.t0 = time.process_time()
        if self.calls % 1000 == 0:
            self.stamps.append((self.calls, time.process_time() - self.t0))
        return ANSWERS[zlib.crc32((a["id"] + "|" + b["id"]).encode("utf-8")) % len(ANSWERS)], usage(self.name)

    def decide_batch(self, calls, timeout_s=None):
        out = []
        for op, args in calls:
            out.append(self.decide_binding(*args)[0] if op == "decide_binding" else self.decide_relation(*args)[0])
        return out, usage(self.name, "local", 0, 0, 0.0) | {"items": len(calls)}


def _records(markets):
    recs = []
    for k in range(markets):
        question = f"Will Team{k} win the {k % 40} cup?"
        for label in ("Yes", "No"):
            recs.append({"id": f"v:m{k}:{1 if label == 'Yes' else 2}", "question": question, "label": label,
                         "event_title": f"E{k % 50}"})
    return recs


@pytest.mark.slow
def test_relation_decisions_do_not_slow_down_as_relations_accumulate(tmp_path, monkeypatch):
    def stop(*args, **kwargs):
        raise _Stop()

    monkeypatch.setattr(fixed, "finalize_checkpoint", stop)
    adapter = _Cheap()
    with pytest.raises(_Stop):
        fixed.build(_records(4000), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), checkpoint_every=5000, top_k=48,
                    complement_dedup=True, batch_size=64, use_groups=False)
    assert adapter.calls >= 110_000, adapter.calls  # the 100k-110k window exists
    stamps = dict(adapter.stamps)
    early_rate = stamps[10_000] / 10  # seconds per 1000 decisions, the first 10k (from the first decision)
    late_rate = (stamps[110_000] - stamps[100_000]) / 10  # seconds per 1000 decisions, 100k-110k
    # 2.5x, not 2x: identical runs on this shared machine differ by up to ~2x in speed (measured), and memory and live
    # objects stay flat. Growth in proportion to the relations accumulated (the failure this guards) is an order of
    # magnitude at 100k against 10k decisions, so 2.5x still catches it.
    assert late_rate <= 2.5 * early_rate + 0.05, (early_rate, late_rate)

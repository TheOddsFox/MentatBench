"""Embeddings-without-semantic-reasoning baseline (hello name embed-cosine, kind local).

Bindings and groups are those of lexical_child (no model is used for them). Relations: EQUIV only when both sides pass the
lexical gate (lexical_child.may_be_equiv: one claim, no polarity-bearing word differing, equal numbers) and the cosine of
their embeddings is at least the threshold; NONE otherwise.
IMPLIES, IMPLIED_BY, EXCLUDES and UNRESOLVED are never emitted, and groups are none.

Model: Qwen/Qwen2.5-0.5B-Instruct at its pin in models.json, read from the local Hugging Face cache only
(HF_HUB_OFFLINE=1, local_files_only; nothing is downloaded). --model may name a local model directory instead (no pin;
the tests use a tiny random-weight model). MB_HF_DEVICE selects the device (default cpu). Float32 throughout. A text is
the normalised question (a named outcome adds its label), embedded without a chat template: the attention-masked mean
of the last hidden state, L2-normalised. Texts are embedded in length-sorted micro-batches of BATCH.

Threshold (gold-free, computed once at startup from the --corpus file: only the text and label of each row). PAIRS
pairs of distinct propositions are drawn with a fixed seed (SEED); the threshold is the nearest-rank PERCENTILE of their
cosines. Random pairs are almost always unrelated, so the threshold is the similarity that unrelated questions reach by
chance. It is recorded in the hello model block.

Usage: tokens_in counts the tokens of every text of a call (cache independent); cost 0. Startup calibration is not
metered, as the hf child's priors are not. Known weakness: the threshold comes from random pairs, but the build compares
blocked candidates, which are far more alike than random pairs (shared templates), so many of them pass. The lexical
gate removes polarity and numeric clashes only; there is no word order and no reasoning.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # read by huggingface_hub at import: this child never downloads

import huggingface_hub  # noqa: E402
import torch  # noqa: E402
from transformers import AutoModel, AutoTokenizer  # noqa: E402

from .common import binary_label, normalise  # noqa: E402
from .hf_child import PINS, resolve_revision, verify_snapshot  # noqa: E402
from .lexical_child import binding, may_be_equiv  # noqa: E402
from .protocol import serve  # noqa: E402

NAME = "embed-cosine"
VERSION = "1"
DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
SEED = 0
PAIRS = 1000
PERCENTILE = 0.995
BATCH = 32
MAX_TOKENS = 512  # shortcut: longer questions are truncated before pooling, upgrade if a corpus has longer ones


def proposition_text(ctx: dict[str, Any]) -> str:
    text = normalise(ctx["question"])
    return text if binary_label(ctx["label"])[0] else normalise(f"{ctx['question']} {ctx['label'] or ''}")


class EmbedChild:
    def __init__(self, model_id: str, revision: str | None, tokenizer: Any, model: Any, device: str):
        self.model_id, self.revision, self.device = model_id, revision, device
        self.tokenizer, self.model = tokenizer, model.eval()
        self.tokenizer.padding_side = "right"  # real tokens start at position 0 whatever the batch
        # shortcut: one unbounded vector cache per distinct text (about 3.6 KB each), upgrade to an LRU for corpora of
        # more than about 200k outcomes
        self._vectors: dict[str, tuple[torch.Tensor, int]] = {}  # text -> (unit vector, token count)
        self.threshold: float | None = None
        self.calibration: dict[str, Any] = {}

    @classmethod
    def load(cls, model: str, device: str, env: Any) -> "EmbedChild":
        if os.path.isdir(model):
            path, model_id, revision = model, Path(model).name, None
        else:
            revision = resolve_revision(model, env.get("MB_HF_REVISION") or None, env.get("MB_HF_ALLOW_UNPINNED") == "1")
            path, model_id = huggingface_hub.snapshot_download(model, revision=revision, local_files_only=True), model
            pin = json.loads(PINS.read_text(encoding="utf-8"))["models"].get(model)
            if pin is not None and revision == pin["revision"]:
                verify_snapshot(pin, Path(path), hash_contents=env.get("MB_HF_VERIFY") == "1")
        tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        net = AutoModel.from_pretrained(path, local_files_only=True, torch_dtype=torch.float32).to(device)
        return cls(model_id, revision, tokenizer, net, device)

    def describe(self) -> dict[str, Any]:
        return {
            "name": NAME,
            "version": VERSION,
            "model": {"id": self.model_id, "revision": self.revision, "kind": "local", "dtype": "float32",
                      "device": self.device, "pooling": "mean", "threshold": self.threshold,
                      "threshold_calibration": self.calibration},
        }

    def _embed(self, texts: list[str]) -> None:
        """Vectors of the texts not yet cached, in length-sorted micro-batches."""
        missing = sorted({t for t in texts if t not in self._vectors}, key=lambda t: (len(t), t))
        for start in range(0, len(missing), BATCH):
            chunk = missing[start:start + BATCH]
            enc = self.tokenizer(chunk, padding=True, truncation=True, max_length=MAX_TOKENS,
                                 return_tensors="pt").to(self.device)
            with torch.inference_mode():
                hidden = self.model(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1.0)
                vectors = torch.nn.functional.normalize(pooled.float(), dim=-1).cpu()
            for text, vector, n in zip(chunk, vectors, enc["attention_mask"].sum(1).tolist()):
                self._vectors[text] = (vector, int(n))

    def calibrate(self, corpus: Path) -> None:
        texts = set()
        with open(corpus, encoding="utf-8-sig") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    texts.add(proposition_text({"question": row["text"], "label": row.get("label")}))
        ordered = sorted(texts)  # a set's order is not stable across processes; sorting makes the sample reproducible
        if len(ordered) < 2:
            raise ValueError(f"{corpus}: calibration needs at least two distinct propositions")
        rng = random.Random(SEED)
        pairs = [tuple(ordered[i] for i in rng.sample(range(len(ordered)), 2)) for _ in range(PAIRS)]
        self._embed([t for pair in pairs for t in pair])
        sims = sorted(float(self._vectors[a][0] @ self._vectors[b][0]) for a, b in pairs)
        self.threshold = sims[min(len(sims), math.ceil(PERCENTILE * len(sims))) - 1]  # nearest rank
        self.calibration = {"pairs": PAIRS, "seed": SEED, "percentile": PERCENTILE, "distinct_propositions": len(ordered)}

    def _relation(self, a: dict[str, Any], b: dict[str, Any]) -> str:
        if not may_be_equiv(a, b):
            return "NONE"
        va, vb = self._vectors[proposition_text(a)][0], self._vectors[proposition_text(b)][0]
        return "EQUIV" if float(va @ vb) >= self.threshold else "NONE"

    def decide(self, op: str, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        usage = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "model": self.model_id, "kind": "local"}
        if op == "decide_binding":
            return [binding(item["ctx"]) for item in items], usage
        if op == "decide_group":
            return [{"groups": []} for _ in items], usage
        texts = [proposition_text(side) for item in items for side in (item["a"], item["b"])]
        self._embed(texts)
        usage["tokens_in"] = sum(self._vectors[t][1] for t in texts)
        return [{"relation": self._relation(item["a"], item["b"])} for item in items], usage


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.children.embed_child", description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", required=True, type=Path,
                        help="the fixture's corpus.jsonl (text and label only), sampled at startup for the threshold")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="a pinned Hugging Face model id (models.json) or a local model directory")
    args = parser.parse_args(argv)
    started = time.monotonic()
    child = EmbedChild.load(args.model, os.environ.get("MB_HF_DEVICE", "cpu"), os.environ)
    child.calibrate(args.corpus)
    print(f"embed_child: threshold {child.threshold:.6f} from {PAIRS} pairs; startup {time.monotonic() - started:.1f} s",
          file=sys.stderr)
    serve(child)


if __name__ == "__main__":
    main()

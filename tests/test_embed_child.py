"""Embedding child: masked mean pooling on a local model directory, the gold-free startup threshold (nearest-rank 99.5th
percentile of seeded random pairs), the EQUIV gate (same claim and cosine >= threshold), batch usage and conformance.

The model is a tiny random-weight Qwen2 saved to a temporary directory, so nothing is downloaded. The real cached
Qwen2.5-0.5B-Instruct is exercised only with MB_HF_MODEL_TESTS=1 (it loads from the local Hugging Face cache on
MB_HF_DEVICE, default cpu).
"""

from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tokenizers  # noqa: F401 - availability check
    import torch
    import transformers  # noqa: F401 - availability check
except ImportError as exc:
    pytest.skip(f"optional torch/transformers/tokenizers dependency unavailable: {exc}", allow_module_level=True)

from mentat_bench.adapters.base import check_binding, check_relation  # noqa: E402
from mentat_bench.children import embed_child as E  # noqa: E402
from mentat_bench.children.protocol import handle  # noqa: E402

PYTHON_DIR = Path(__file__).resolve().parents[1] / "python"
ROWS = [
    {"id": "v:m1:1", "text": "Will Ada close above 100 on June 1, 12PM ET?", "label": "Yes", "event_title": "E"},
    {"id": "v:m1:2", "text": "Will Ada close above 100 on June 1, 12PM ET?", "label": "No", "event_title": "E"},
    {"id": "v:m2:1", "text": "Will Ben win the Example Cup?", "label": "Yes", "event_title": "F"},
    {"id": "v:m3:1", "text": "Who wins the Example Cup this year?", "label": "Alpha", "event_title": "G"},
    {"id": "v:m3:2", "text": "Who wins the Example Cup this year?", "label": "Beta", "event_title": "G"},
    {"id": "v:m4:1", "text": "Will Cy finish with 3 or more points?", "label": "Yes", "event_title": "H"},
]
QUESTION = "Will Ada close above 100 on June 1, 12PM ET?"
TEMPLATE = (
    "{% for m in messages %}<|{{ m['role'] }}|>{{ m['content'] }}\n{% endfor %}"
    "{% if add_generation_prompt %}<|assistant|>{% endif %}"
)


def write_tiny_model(directory: Path) -> Path:
    """A byte-level tokenizer and a random-weight Qwen2, saved as a local model directory (seeded)."""
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM

    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(vocab_size=400, special_tokens=["<pad>", "<eos>", "<unk>"],
                                  initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), show_progress=False)
    tok.train_from_iterator([row["text"] for row in ROWS] * 4, trainer)
    fast = PreTrainedTokenizerFast(tokenizer_object=tok, pad_token="<pad>", eos_token="<eos>", unk_token="<unk>")
    fast.chat_template = TEMPLATE
    torch.manual_seed(0)
    config = Qwen2Config(vocab_size=len(fast), hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                         num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=8192)
    directory.mkdir(parents=True, exist_ok=True)
    fast.save_pretrained(directory)
    Qwen2ForCausalLM(config).save_pretrained(directory)
    return directory


def write_corpus(path: Path) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in ROWS), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory) -> Path:
    return write_tiny_model(tmp_path_factory.mktemp("tiny-qwen2"))


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    return write_corpus(tmp_path_factory.mktemp("corpus") / "corpus.jsonl")


@pytest.fixture(scope="module")
def child(model_dir, corpus) -> E.EmbedChild:
    child = E.EmbedChild.load(str(model_dir), "cpu", {})
    child.calibrate(corpus)
    return child


def ctx(oid: str, question: str, label: str | None) -> dict:
    return {"id": oid, "question": question, "label": label, "event_title": None}


def test_embeddings_are_unit_length_and_have_the_model_width(child) -> None:
    child._embed([E.proposition_text(ctx("a", QUESTION, "Yes"))])
    vector, tokens = child._vectors[E.proposition_text(ctx("a", QUESTION, "Yes"))]
    assert vector.shape == (child.model.config.hidden_size,)
    assert abs(float(vector.norm()) - 1.0) < 1e-5 and tokens > 0


def test_the_threshold_is_the_nearest_rank_percentile_of_seeded_pairs(child, corpus) -> None:
    texts = sorted({E.proposition_text({"question": r["text"], "label": r["label"]}) for r in ROWS})
    rng = random.Random(E.SEED)
    pairs = [tuple(texts[i] for i in rng.sample(range(len(texts)), 2)) for _ in range(E.PAIRS)]
    sims = sorted(float(child._vectors[a][0] @ child._vectors[b][0]) for a, b in pairs)
    assert child.threshold == sims[math.ceil(E.PERCENTILE * E.PAIRS) - 1]
    assert child.calibration == {"pairs": E.PAIRS, "seed": E.SEED, "percentile": E.PERCENTILE,
                                 "distinct_propositions": len(texts)}


def test_calibration_is_reproducible(model_dir, corpus, child) -> None:
    again = E.EmbedChild.load(str(model_dir), "cpu", {})
    again.calibrate(corpus)
    assert again.threshold == child.threshold


def test_relation_needs_one_claim_and_a_cosine_at_the_threshold(model_dir, corpus) -> None:
    permissive = E.EmbedChild.load(str(model_dir), "cpu", {})
    permissive.calibrate(corpus)
    permissive.threshold = -1.0  # every cosine passes: only the claim test can say NONE
    yes, no = ctx("a", QUESTION, "Yes"), ctx("b", QUESTION, "No")
    assert permissive.decide("decide_relation", [{"a": yes, "b": yes}])[0] == [{"relation": "EQUIV"}]
    assert permissive.decide("decide_relation", [{"a": yes, "b": no}])[0] == [{"relation": "NONE"}]  # opposite sides
    alpha, beta = ctx("c", "Who wins?", "Alpha"), ctx("d", "Who wins?", "Beta")
    assert permissive.decide("decide_relation", [{"a": alpha, "b": beta}])[0] == [{"relation": "NONE"}]
    strict = E.EmbedChild.load(str(model_dir), "cpu", {})
    strict.calibrate(corpus)
    strict.threshold = 1.5  # no cosine reaches it
    assert strict.decide("decide_relation", [{"a": yes, "b": yes}])[0] == [{"relation": "NONE"}]


def test_usage_counts_the_tokens_of_every_text_whatever_the_cache(child) -> None:
    items = [{"a": ctx("a", QUESTION, "Yes"), "b": ctx("b", "Will Ben win the Example Cup?", "Yes")}]
    _, first = child.decide("decide_relation", items)
    _, second = child.decide("decide_relation", items)  # every text is cached now
    assert first["tokens_in"] == second["tokens_in"] > 0
    assert first["model"] == child.model_id and first["kind"] == "local" and first["cost_usd"] == 0.0


def test_bindings_and_groups_do_not_touch_the_model(child) -> None:
    results, usage = child.decide("decide_binding", [{"ctx": ctx("a", QUESTION, "Yes")}])
    assert check_binding(results[0])["status"] == "bound" and usage["tokens_in"] == 0
    group, _ = child.decide("decide_group", [{"ctx": {"group_id": "g", "source": "s1", "members": []}}])
    assert group == [{"groups": []}]


def test_hello_reports_the_model_and_the_threshold(child) -> None:
    body = json.loads(json.dumps(handle(child, json.dumps({"op": "hello", "protocol": 2, "id": 3}))))
    assert body["name"] == E.NAME and body["id"] == 3
    assert body["model"]["threshold"] == child.threshold and body["model"]["pooling"] == "mean"
    assert body["model"]["id"] == child.model_id and body["model"]["revision"] is None


@pytest.mark.slow
def test_conformance_runner_passes_on_the_tiny_model(model_dir, corpus) -> None:
    env = {**os.environ, "PYTHONPATH": str(PYTHON_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    done = subprocess.run([sys.executable, "-m", "mentat_bench.children.conformance", sys.executable, "-m",
                           "mentat_bench.children.embed_child", "--corpus", str(corpus), "--model", str(model_dir)],
                          env=env, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "conformance:" in done.stdout


@pytest.mark.skipif(os.environ.get("MB_HF_MODEL_TESTS") != "1", reason="real-model test: set MB_HF_MODEL_TESTS=1")
def test_real_pinned_model_from_the_local_cache(corpus) -> None:
    child = E.EmbedChild.load(E.DEFAULT_MODEL, os.environ.get("MB_HF_DEVICE", "cpu"), os.environ)
    child.calibrate(corpus)
    pinned = json.loads(E.PINS.read_text(encoding="utf-8"))["models"][E.DEFAULT_MODEL]["revision"]
    assert child.model.config.hidden_size == 896 and child.revision == pinned
    assert 0.0 < child.threshold <= 1.0
    yes = ctx("a", QUESTION, "Yes")
    same = child.decide("decide_relation", [{"a": yes, "b": yes}])[0][0]
    check_relation(same["relation"])
    assert same["relation"] == "EQUIV"  # a claim is EQUIV to itself: cosine 1 >= threshold


# The lexical gate (polarity words, numbers) applies before the cosine (review M9). With threshold -1 every cosine
# passes, so NONE below can only come from the gate.
SUFFIX = " according to the official schedule published by the committee for the quarter"
BASE = "Will the Fed announce a cut to its policy rate by 25 basis points at the March meeting in Washington this year"
ABOVE = "Will the policy rate of the Fed be above 4 percent at the March meeting in Washington this year" + SUFFIX
OVER = "Will the total goals in the Example Cup final be over 2 at the stadium in Washington this year" + SUFFIX
BEFORE = "Will the Example Cup final be decided before the second half of the match in Washington this year" + SUFFIX
WIN = "Will the home team win the Example Cup final match held at the stadium in Washington this year" + SUFFIX
POLARITY_PAIRS = [
    ("not", BASE, BASE.replace("Will the Fed announce", "Will the Fed not announce")),
    ("n't", BASE, BASE.replace("Will the Fed announce", "Will the Fed don't announce")),
    ("above/below", ABOVE, ABOVE.replace("above", "below")),
    ("over/under", OVER, OVER.replace(" over ", " under ")),
    ("before/after", BEFORE, BEFORE.replace("before", "after")),
    ("win/lose", WIN, WIN.replace(" win ", " lose ")),
]


@pytest.fixture(scope="module")
def permissive(model_dir, corpus) -> E.EmbedChild:
    child = E.EmbedChild.load(str(model_dir), "cpu", {})
    child.calibrate(corpus)
    child.threshold = -1.0
    return child


@pytest.mark.parametrize("name,a,b", POLARITY_PAIRS, ids=[p[0] for p in POLARITY_PAIRS])
def test_polarity_words_block_equiv_whatever_the_cosine(permissive, name: str, a: str, b: str) -> None:
    for x, y in ((a, b), (b, a)):
        decision = permissive.decide("decide_relation", [{"a": ctx("a", x, "Yes"), "b": ctx("b", y, "Yes")}])[0][0]
        assert decision == {"relation": "NONE"}


def test_numbers_block_equiv_whatever_the_cosine(permissive) -> None:
    decision = permissive.decide("decide_relation", [{"a": ctx("a", "Will Ada close above 100?", "Yes"),
                                                       "b": ctx("b", "Will Ada close above 120?", "Yes")}])[0][0]
    assert decision == {"relation": "NONE"}


def test_a_difference_without_polarity_words_passes_the_gate(permissive) -> None:
    decision = permissive.decide("decide_relation", [{"a": ctx("a", BASE, "Yes"),
                                                       "b": ctx("b", BASE.replace("announce", "signal"), "Yes")}])[0][0]
    assert decision == {"relation": "EQUIV"}

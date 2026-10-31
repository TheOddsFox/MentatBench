"""HF child: option scoring, contextual calibration, few-shot prompts, the batch envelope, error isolation, OOM halving
and revision pinning.

The model is a tiny random-weight Qwen2 built from a config, with a byte-level BPE tokenizer trained on a few
sentences, so nothing is downloaded. The real-model smoke test is opt-in: MB_HF_MODEL_TESTS=1 loads the pinned
Qwen2.5-0.5B-Instruct from the Hugging Face cache on MB_HF_DEVICE (default cuda:0).
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    import tokenizers  # noqa: F401 - availability check
    import torch
    import transformers  # noqa: F401 - availability check
except ImportError as exc:
    pytest.skip(f"optional torch/transformers/tokenizers dependency unavailable: {exc}", allow_module_level=True)

from mentat_bench.adapters.base import RELATIONS, check_binding, check_group, check_relation  # noqa: E402
from mentat_bench.adapters.local_jev import _decision  # noqa: E402
from mentat_bench.children import hf_child as HF  # noqa: E402
from mentat_bench.children.common import chain_members  # noqa: E402
from mentat_bench.children.protocol import ChildError, handle  # noqa: E402

CORPUS = [
    "Will Bitcoin close above 75,000 on May 29, 10AM ET?",
    "Will Ethereum close above 2,780 on September 21, 11PM ET?",
    "Yes No Over Under Up Down Label Event Question Options bound unresolved",
    "exactly_one at_most_one at_least_k at_most_k ordered_chain forward reverse none",
]
TEMPLATE = (
    "{% for m in messages %}<|{{ m['role'] }}|>{{ m['content'] }}\n{% endfor %}"
    "{% if add_generation_prompt %}<|assistant|>{% endif %}"
)


def tiny_pair():
    """A byte-level tokenizer and a random-weight Qwen2 built from a config (no download). Seeded, so reproducible."""
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast, Qwen2Config, Qwen2ForCausalLM

    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=400, special_tokens=["<pad>", "<eos>", "<unk>"],
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), show_progress=False,  # stdout stays protocol-clean
    )
    tok.train_from_iterator(CORPUS * 4, trainer)
    fast = PreTrainedTokenizerFast(tokenizer_object=tok, pad_token="<pad>", eos_token="<eos>", unk_token="<unk>")
    fast.chat_template = TEMPLATE
    torch.manual_seed(0)
    config = Qwen2Config(vocab_size=len(fast), hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                         num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=8192)
    return fast, Qwen2ForCausalLM(config).eval()


@pytest.fixture(scope="module")
def tiny():
    return tiny_pair()


@pytest.fixture(scope="module", autouse=True)
def long_prompts():
    """The byte-level test tokenizer spends about one token per character, and the relation few-shot turns are about
    4,500 characters, so they need more room than the 4096 tokens of a real tokenizer. The oversized-prompt tests still
    exceed this limit. Module scope: the shared calibrated child is constructed under the same limit."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(HF, "MAX_PROMPT_TOKENS", 8192)
        yield


@pytest.fixture(scope="module")
def calibrated(tiny) -> HF.HFChild:
    """One construction (priors and prefix caches computed at startup), shared read-only: each test gets a copy. Relation
    calibration is on here (it is off by default): the relation tests below need its prior."""
    tokenizer, model = tiny
    return HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu", relation_calibration=True)


@pytest.fixture(scope="module")
def uncalibrated(tiny) -> HF.HFChild:
    tokenizer, model = tiny
    return HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu", calibrate=False)


def fresh(prototype: HF.HFChild) -> HF.HFChild:
    """A copy of a constructed child with its own mutable state: the priors, prefix caches and splits are new dicts that
    start from the prototype's values, and the meter is empty. The model and tokenizer are shared."""
    child = copy.copy(prototype)
    child._priors = dict(prototype._priors)
    child._prefixes = dict(prototype._prefixes)
    child._prefix_text = dict(prototype._prefix_text)
    child._split = dict(prototype._split)
    child._meter = {"tokens_in": 0, "tokens_out": 0, "gpu_seconds": 0.0}
    return child


@pytest.fixture()
def child(calibrated) -> HF.HFChild:
    return fresh(calibrated)


@pytest.fixture()
def raw_child(uncalibrated) -> HF.HFChild:
    return fresh(uncalibrated)


def ctx(oid: str, question: str, label: str | None, title: str | None = "Event") -> dict:
    return {"id": oid, "question": question, "label": label, "event_title": title}


def naive_score(child: HF.HFChild, prompt: list[int], option: list[int]) -> float:
    """Reference: one unbatched forward pass with full logits; mean log-probability of the option tokens."""
    logits = child.model(input_ids=torch.tensor([prompt + option], device=child.device)).logits[0].float()
    logp = torch.log_softmax(logits, dim=-1)
    values = [logp[len(prompt) - 1 + t, token].item() for t, token in enumerate(option)]
    return sum(values) / len(values)


def naive_raw(child: HF.HFChild, stage: str, payload, options: tuple[str, ...]) -> list[float]:
    prompt = child._prompt_ids(HF._messages(stage, payload))
    return [naive_score(child, prompt, child._option_ids(o)) for o in options]


def test_batched_option_scores_match_unbatched_reference(child) -> None:
    payloads = [ctx(str(i), q, "Yes") for i, q in enumerate(CORPUS)]
    prompts = [child._prompt_ids(HF._messages("status", p)) for p in payloads]
    pairs = [(p, child._option_ids(opt)) for p in prompts for opt in HF.STATUS + HF.NEGATED_OPTIONS]
    batched = child._score(pairs)
    for (prompt, option), score in zip(pairs, batched):
        assert score == pytest.approx(naive_score(child, prompt, option), abs=1e-4)


def test_prior_is_the_mean_raw_score_over_content_free_inputs(child) -> None:
    prior = child._prior("negated", HF.NEGATED_OPTIONS)
    per_input = [naive_raw(child, "negated", p, HF.NEGATED_OPTIONS) for p in HF.CALIBRATION["negated"](2)]
    expected = [sum(column) / len(per_input) for column in zip(*per_input)]
    assert prior == pytest.approx(expected, abs=1e-4)


def test_startup_computes_every_prior_once(tiny) -> None:
    tokenizer, model = tiny
    child = HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu", relation_calibration=True)  # the startup
    keys = set(child._priors)
    assert {("status", HF.STATUS, 0), ("kind", HF.GROUP_CHOICES, 0)} <= keys
    assert ("relation", HF.RELATION_LETTERS, 0) in keys
    assert {("k", HF.k_options(n), n) for n in range(2, HF.MAX_K + 1)} <= keys
    assert {("order", tuple(HF.ORDER_LETTERS[:n]), n) for n in range(2, len(HF.ORDER_LETTERS) + 1)} <= keys
    before = dict(child._priors)
    child._prior("negated", HF.NEGATED_OPTIONS)  # a second request reads the cache, it does not recompute
    assert child._priors == before


def test_k_prior_uses_the_real_member_count(child) -> None:
    """A candidate set of 12 members (more than MAX_K) gets the prior of 12 content-free members, not of 8."""
    prior = child._prior("k", HF.k_options(12), 12)
    per_input = [naive_raw(child, "k", payload, HF.k_options(12)) for payload in HF.CALIBRATION["k"](12)]
    expected = [sum(column) / len(per_input) for column in zip(*per_input)]
    assert prior == pytest.approx(expected, abs=1e-4)
    assert len(HF.CALIBRATION["k"](12)[0]) == 12


def test_stage_subtracts_the_prior_before_the_argmax(child) -> None:
    payload = ctx("a", "Will Bitcoin close above 75,000 on May 29, 10AM ET?", "Yes")
    raw = naive_raw(child, "negated", payload, HF.NEGATED_OPTIONS)
    prior = child._prior("negated", HF.NEGATED_OPTIONS)
    adjusted = [r - p for r, p in zip(raw, prior)]
    results: list = [None]
    choice = child._stage(results, [0], "negated", lambda i: payload, lambda i: HF.NEGATED_OPTIONS)
    assert choice[0] == max(range(len(adjusted)), key=lambda k: adjusted[k])


def test_flag_off_is_the_raw_argmax(raw_child) -> None:
    payload = ctx("a", "Will Bitcoin close above 75,000 on May 29, 10AM ET?", "No")
    raw = naive_raw(raw_child, "negated", payload, HF.NEGATED_OPTIONS)
    results: list = [None]
    choice = raw_child._stage(results, [0], "negated", lambda i: payload, lambda i: HF.NEGATED_OPTIONS)
    assert raw_child._priors == {}
    assert choice[0] == max(range(len(raw)), key=lambda k: raw[k])
    assert raw_child.describe()["model"]["calibrate"] is False


@pytest.mark.slow
def test_calibrated_decisions_are_deterministic(tiny) -> None:
    tokenizer, model = tiny
    first = HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu")
    second = HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu")
    for key in first._priors:
        assert first._priors[key] == pytest.approx(second._priors[key], abs=1e-6)
    items = [{"ctx": ctx(str(i), q, "Yes")} for i, q in enumerate(CORPUS)]
    a, _ = first.decide("decide_binding", items)
    b, _ = second.decide("decide_binding", items)
    c, _ = first.decide("decide_binding", items)
    assert a == b == c


def test_flag_parsing() -> None:
    assert HF.parse_calibrate("1") is True and HF.parse_calibrate("0") is False
    with pytest.raises(ValueError, match="MB_HF_CALIBRATE"):
        HF.parse_calibrate("yes")


def test_few_shot_examples_cover_every_option() -> None:
    for stage, options in (("status", HF.STATUS), ("negated", HF.NEGATED_OPTIONS), ("missing", HF.MISSING),
                           ("kind", HF.GROUP_CHOICES), ("order", tuple(HF.ORDER_LETTERS[:3]))):
        answers = [answer for _, answer in HF.FEW_SHOT[stage]]
        assert set(options) <= set(answers), stage
    assert {answer for _, answer in HF.FEW_SHOT["k"]} <= set(HF.k_options(3))
    assert "direction" not in HF.FEW_SHOT and "direction" not in HF.USER  # the order stage replaces the direction stage


def test_relation_few_shot_is_balanced_and_each_answer_names_its_relation() -> None:
    names = Counter(name for _, name in HF._RELATION_SHOTS)
    assert names == {name: 6 if name == "NONE" else 3 for name in HF.RELATION_CHOICES}  # no block of one answer
    letters = Counter(answer for _, answer in HF.FEW_SHOT["relation"])
    assert set(letters) == set(HF.RELATION_LETTERS)
    assert max(letters.values()) - min(letters.values()) <= 1  # the answer letters are balanced over the shots
    for (_, name), (payload, letter) in zip(HF._RELATION_SHOTS, HF.FEW_SHOT["relation"]):
        assert payload[2][HF.RELATION_LETTERS.index(letter)] == name  # the letter is the relation's place on the menu
    labels = {shot["label"] for pair, _ in HF._RELATION_SHOTS for shot in pair}
    assert {"No", "Under"} <= labels  # negative labels are exemplified, as decisions show them


def test_relation_few_shot_has_no_run_of_one_answer_longer_than_two() -> None:
    for answers in ([name for _, name in HF._RELATION_SHOTS], [answer for _, answer in HF.FEW_SHOT["relation"]]):
        assert max(len(list(run)) for _, run in itertools.groupby(answers)) <= 2
    assert HF._RELATION_SHOTS[-1][1] != "NONE"  # the last shot is not the NONE the decision turn follows


def test_relation_letters_are_single_tokens(child) -> None:
    assert all(len(child._option_ids(letter)) == 1 for letter in HF.RELATION_LETTERS)


def test_one_token_options_need_no_tail_and_match_one_row_each(child, monkeypatch) -> None:
    """One-token options are read at the head's last position: the four letters of a prompt are scored from one head pass
    and no tail, with the scores of one unbatched forward per letter (within tolerance) and the same argmax."""
    a, b = _pair("a", "b")
    prompt = child._prompt_ids(HF._messages("relation", (a, b, HF.relation_order(a, b))))
    letters = [child._option_ids(letter) for letter in HF.RELATION_LETTERS]
    seen: list[tuple[int, int]] = []
    original = child._forward_tails

    def spy(feeds, prefix, *args):
        seen.append((len(feeds), sum(len(o) > 1 for _, options in feeds for o in options)))  # (prompts, multi-token options)
        return original(feeds, prefix, *args)

    monkeypatch.setattr(child, "_forward_tails", spy)
    one_row = child._score([(prompt, letter) for letter in letters], "relation")
    assert seen == [(1, 0)]  # one prompt, one head pass, no tail for the four one-token options
    naive = [naive_score(child, prompt, letter) for letter in letters]
    assert one_row == pytest.approx(naive, abs=1e-3)


def test_multi_token_options_share_one_head_and_one_tail_each(child, monkeypatch) -> None:
    """The kind options of one candidate set cost one head pass (one feed) and one tail per multi-token option, not one full
    row each."""
    members = [ctx("a", "Will Q1 be above 30?", "Yes"), ctx("b", "Will Q1 be above 40?", "Yes")]
    prompt = child._prompt_ids(HF._messages("kind", members))
    options = [child._option_ids(o) for o in HF.GROUP_CHOICES]
    assert max(len(o) for o in options) > 1  # some kind options are multi-token
    seen: list[tuple[int, int]] = []
    original = child._forward_tails

    def spy(feeds, prefix, *args):
        seen.append((len(feeds), sum(len(o) > 1 for _, options in feeds for o in options)))
        return original(feeds, prefix, *args)

    monkeypatch.setattr(child, "_forward_tails", spy)
    scores = child._score([(prompt, o) for o in options], "kind")
    assert seen == [(1, sum(len(o) > 1 for o in options))]
    assert scores == pytest.approx([naive_score(child, prompt, o) for o in options], abs=1e-3)


def test_relation_few_shot_prefix_is_the_same_for_every_pair() -> None:
    prefix = HF._shot_turns("relation")
    for a_id, b_id in (("a", "b"), ("c", "d")):
        a, b = _pair(a_id, b_id)
        assert HF._messages("relation", (a, b, HF.relation_order(a, b)))[:-1] == prefix  # the prefix KV cache holds


def _exemplar_questions(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return [payload["question"]]
    if isinstance(payload, (list, tuple)):
        return [q for item in payload for q in _exemplar_questions(item)]
    return []  # option strings and menu orders


def test_exemplar_questions_do_not_occur_in_the_fixture_corpora() -> None:
    corpora = sorted((Path(__file__).resolve().parents[1] / "fixtures").glob("*/corpus.jsonl"))
    if not corpora:
        pytest.skip("no fixture corpus present")
    questions = {q for shots in HF.FEW_SHOT.values() for payload, _ in shots for q in _exemplar_questions(payload)}
    assert questions
    for path in corpora:
        text = path.read_text(encoding="utf-8")
        assert sorted(q for q in questions if q in text) == [], path.parent.name


def test_relation_prompt_lists_the_four_lettered_choices() -> None:
    pair = (ctx("a", "Will Oak Town get 10 mm of rain?", "Yes"), ctx("b", "Will Oak Town get 20 mm of rain?", "Yes"))
    prompt = HF._messages("relation", pair)[-1]["content"]
    assert "A. IMPLIES (A implies B)\nB. IMPLIED_BY (B implies A)\nC. EXCLUDES (not both)\nD. NONE" in prompt
    assert prompt.endswith("Answer with the letter.")
    assert set(HF.RELATION_CHOICES) == set(RELATIONS) - {"EQUIV", "UNRESOLVED"}


def _pair(a_id: str, b_id: str) -> tuple[dict, dict]:
    return ctx(a_id, f"Will {a_id} win?", "Yes"), ctx(b_id, f"Will {b_id} win?", "Yes")


RELATION_FLOOR_NATS = 4.0  # the tie test needs the exact floor value
assert HF.RELATION_FLOOR == RELATION_FLOOR_NATS


def _letters(order: tuple[str, ...], values: dict[str, float]) -> list[float]:
    """Scores in letter order A-D for a menu in `order`, from relation-name values."""
    return [values[name] for name in order]


def test_relation_order_is_a_fixed_shuffle_of_the_four_choices() -> None:
    a, b = _pair("p1", "p2")
    assert HF.relation_order(a, b) == HF.relation_order(a, b)  # deterministic
    assert sorted(HF.relation_order(a, b)) == sorted(HF.RELATION_CHOICES)
    orders = {HF.relation_order(*_pair(f"x{i}", f"y{i}")) for i in range(40)}
    assert len(orders) > 1  # the letter a relation gets varies across pairs


def test_relation_decides_the_best_named_relation_above_the_floor(child, monkeypatch) -> None:
    pairs = [_pair("a", "b"), _pair("c", "d"), _pair("e", "f")]
    orders = [HF.relation_order(a, b) for a, b in pairs]
    sure = _letters(orders[0], {"IMPLIES": 0.0, "IMPLIED_BY": -9.0, "EXCLUDES": -9.0, "NONE": -9.0})
    weak = _letters(orders[1], {"IMPLIES": -1.0, "IMPLIED_BY": -3.0, "EXCLUDES": -3.0, "NONE": -0.5})
    none = _letters(orders[2], {"IMPLIES": -9.0, "IMPLIED_BY": -9.0, "EXCLUDES": -9.0, "NONE": 0.0})
    monkeypatch.setattr(child, "_scores",
                        lambda results, idx, stage, payload_of, options_of, size_of=None: {0: sure, 1: weak, 2: none})
    out = child._relations(pairs)
    assert out[0] == {"relation": "IMPLIES", "conf": pytest.approx(9.0)}
    assert out[1]["relation"] == "NONE" and out[1]["conf"] == pytest.approx(-0.5)  # NONE beats the best named relation
    assert out[2] == {"relation": "NONE", "conf": pytest.approx(-9.0)}


def test_relation_floor_turns_a_weak_named_relation_into_none(child, monkeypatch) -> None:
    pairs = [_pair("a", "b")]
    order = HF.relation_order(*pairs[0])
    scores = _letters(order, {"IMPLIES": 2.0, "IMPLIED_BY": 0.0, "EXCLUDES": 0.0, "NONE": 0.0})
    monkeypatch.setattr(child, "_scores",
                        lambda results, idx, stage, payload_of, options_of, size_of=None: {0: scores})
    child.relation_floor = 3.0
    assert child._relations(pairs) == [{"relation": "NONE", "conf": pytest.approx(2.0)}]
    child.relation_floor = 1.0
    assert child._relations(pairs) == [{"relation": "IMPLIES", "conf": pytest.approx(2.0)}]


def test_relation_margin_is_the_difference_of_the_calibrated_projected_logits(child) -> None:
    """Relation scores are the projected logits of the letters (no normaliser), less the letter prior: the conf is the
    exact difference of the calibrated logits of the best named relation and NONE."""
    a, b = _pair("a", "b")
    order = HF.relation_order(a, b)
    prompt = child._prompt_ids(HF._messages("relation", (a, b, order)))
    logits = child.model(input_ids=torch.tensor([prompt])).logits[0, -1].float()
    letters = [child._option_ids(letter)[0] for letter in HF.RELATION_LETTERS]
    values = {name: logits[token].item() for name, token in zip(order, letters)}
    got = child._score([(prompt, [token]) for token in letters], "relation", raw=True)
    assert got == pytest.approx([values[name] for name in order], abs=1e-4)
    prior = child._prior("relation", HF.RELATION_LETTERS)
    calibrated = {name: values[name] - prior[k] for k, name in enumerate(order)}
    best = max((n for n in order if n != "NONE"), key=lambda n: calibrated[n])
    assert child._relations([(a, b)])[0]["conf"] == pytest.approx(calibrated[best] - calibrated["NONE"], abs=1e-4)


def test_a_margin_of_exactly_the_floor_is_named(child, monkeypatch) -> None:
    """A margin of exactly RELATION_FLOOR (one bf16 logit step on the grid) is decided by the >= rule, not by rounding."""
    pairs = [_pair("a", "b")]
    scores = _letters(HF.relation_order(*pairs[0]), {"IMPLIES": RELATION_FLOOR_NATS, "IMPLIED_BY": 0.0,
                                                     "EXCLUDES": 0.0, "NONE": 0.0})
    monkeypatch.setattr(child, "_scores",
                        lambda results, idx, stage, payload_of, options_of, size_of=None: {0: scores})
    assert child._relations(pairs) == [{"relation": "IMPLIES", "conf": pytest.approx(RELATION_FLOOR_NATS)}]


def test_relation_prompt_uses_the_pair_order_and_the_fixed_order_without_one() -> None:
    a, b = _pair("a", "b")
    order = HF.relation_order(a, b)
    prompt = HF._messages("relation", (a, b, order))[-1]["content"]
    for letter, name in zip(HF.RELATION_LETTERS, order):
        assert f"{letter}. {name} (" in prompt
    decision_turn = HF._messages("relation", (a, b))[-1]["content"]
    assert "A. IMPLIES (A implies B)\nB. IMPLIED_BY (B implies A)" in decision_turn


def test_relation_prior_is_the_mean_raw_logit_per_letter_over_rotated_content_free_pairs(child) -> None:
    """Calibration math: each content-free pair under each rotation of the relation names, so every relation is on every
    letter once per content-free pair, and the prior is the mean raw logit of each letter over those prompts."""
    payloads = HF.CALIBRATION["relation"](0)
    assert len(payloads) == 3 * len(HF.RELATION_CHOICES)
    for s in range(3):
        block = [order for _, _, order in payloads[4 * s:4 * s + 4]]
        for letter in range(4):
            assert {order[letter] for order in block} == set(HF.RELATION_CHOICES)
    per_payload = []
    for a, b, order in payloads:
        prompt = child._prompt_ids(HF._messages("relation", (a, b, order)))
        logits = child.model(input_ids=torch.tensor([prompt])).logits[0, -1].float()
        per_payload.append([logits[child._option_ids(letter)[0]].item() for letter in HF.RELATION_LETTERS])
    expected = [sum(column) / len(per_payload) for column in zip(*per_payload)]
    assert child._prior("relation", HF.RELATION_LETTERS) == pytest.approx(expected, abs=1e-3)


def test_relation_decision_is_on_the_calibrated_logits(child, monkeypatch) -> None:
    """The letter prior is subtracted before the margin: IMPLIES is 6.0 raw, 3.0 after its letter's prior of 3.0, so
    under the floor of 4.0 the pair is NONE (its margin over NONE is 3.0)."""
    a, b = _pair("a", "b")
    order = HF.relation_order(a, b)
    values = {"IMPLIES": 6.0, "IMPLIED_BY": 1.0, "EXCLUDES": 0.0, "NONE": 0.0}
    prior = [0.0] * 4
    prior[order.index("IMPLIES")] = 3.0
    child._priors[("relation", HF.RELATION_LETTERS, 0)] = prior
    monkeypatch.setattr(child, "_score", lambda pairs, stage=None, raw=False: [values[n] for n in order])
    assert child._relations([(a, b)]) == [{"relation": "NONE", "conf": pytest.approx(3.0)}]
    child._priors[("relation", HF.RELATION_LETTERS, 0)] = [0.0] * 4
    assert child._relations([(a, b)]) == [{"relation": "IMPLIES", "conf": pytest.approx(6.0)}]


def test_relation_floor_resolution_order(tmp_path, monkeypatch) -> None:
    """The model's models.json relation_floor is the default; MB_HF_RELATION_FLOOR may only raise it; a model without
    one gets RELATION_FLOOR."""
    pins = tmp_path / "models.json"
    pins.write_text(json.dumps({"models": {"x/pinned": {"relation_floor": 2.5, "revision": "a" * 40},
                                           "x/plain": {"revision": "a" * 40}}}), encoding="utf-8")
    monkeypatch.setattr(HF, "PINS", pins)
    assert HF.relation_floor_of("x/plain", None) == (HF.RELATION_FLOOR, "default")
    assert HF.relation_floor_of("x/pinned", None) == (2.5, "models.json")
    assert HF.relation_floor_of("x/pinned", 2.5) == (2.5, "models.json")
    assert HF.relation_floor_of("x/pinned", 3.0) == (3.0, "env")
    assert HF.relation_floor_of("x/plain", 5.0) == (5.0, "env")
    with pytest.raises(ValueError, match="only tighten"):
        HF.relation_floor_of("x/pinned", 2.0)
    with pytest.raises(ValueError, match="only tighten"):
        HF.relation_floor_of("x/plain", 3.0)
    with pytest.raises(ValueError):
        HF.relation_floor_of("x/plain", float("nan"))


def test_from_env_refuses_a_loosened_floor_before_loading_weights(monkeypatch) -> None:
    monkeypatch.setattr(HF.AutoModelForCausalLM, "from_pretrained", lambda *a, **k: pytest.fail("weights loaded"))
    with pytest.raises(ValueError, match="only tighten"):
        HF.HFChild.from_env({"MB_HF_MODEL": "Qwen/Qwen2.5-0.5B-Instruct", "MB_HF_DEVICE": "cpu",
                             "MB_HF_RELATION_FLOOR": "2.0"})


def test_describe_reports_version_floor_calibration_and_their_sources(tiny, raw_child) -> None:
    tokenizer, model = tiny
    assert raw_child.describe()["version"] == "9"
    assert raw_child.describe()["relation_floor"] == {"value": HF.RELATION_FLOOR, "source": "default"}
    assert raw_child.describe()["relation_calibration"] == {"value": False, "source": "default"}
    raised = HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu", relation_floor=5.0,
                        relation_calibration=True)
    assert raised.describe()["relation_floor"] == {"value": 5.0, "source": "env"}
    assert raised.describe()["relation_calibration"] == {"value": True, "source": "env"}
    gated = HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu", calibrate=False, relation_calibration=True)
    assert gated.describe()["relation_calibration"] == {"value": False,
                                                        "source": "env, disabled by MB_HF_CALIBRATE=0"}
    assert gated.describe()["model"]["calibrate"] is False


def test_relation_calibration_resolution_order(tmp_path, monkeypatch) -> None:
    """models.json relation_calibration is the default (off when absent); MB_HF_RELATION_CALIBRATION overrides it."""
    pins = tmp_path / "models.json"
    pins.write_text(json.dumps({"models": {"x/on": {"relation_calibration": True, "revision": "a" * 40},
                                           "x/off": {"relation_calibration": False, "revision": "a" * 40},
                                           "x/plain": {"revision": "a" * 40},
                                           "x/bad": {"relation_calibration": "yes", "revision": "a" * 40}}}),
                    encoding="utf-8")
    monkeypatch.setattr(HF, "PINS", pins)
    assert HF.relation_calibration_of("x/plain", None) == (False, "default")
    assert HF.relation_calibration_of("x/on", None) == (True, "models.json")
    assert HF.relation_calibration_of("x/off", None) == (False, "models.json")
    assert HF.relation_calibration_of("x/on", False) == (False, "env")
    assert HF.relation_calibration_of("x/off", True) == (True, "env")
    with pytest.raises(ValueError, match="true or false"):
        HF.relation_calibration_of("x/bad", None)


@pytest.mark.parametrize("value", ["yes", "2", "true", "-1"])
def test_from_env_refuses_a_relation_calibration_other_than_0_or_1_before_loading_weights(monkeypatch, value) -> None:
    monkeypatch.setattr(HF.AutoModelForCausalLM, "from_pretrained", lambda *a, **k: pytest.fail("weights loaded"))
    with pytest.raises(ValueError):
        HF.HFChild.from_env({"MB_HF_MODEL": "Qwen/Qwen2.5-0.5B-Instruct", "MB_HF_DEVICE": "cpu",
                             "MB_HF_RELATION_CALIBRATION": value})


def naive_logit(child: HF.HFChild, prompt: list[int], token: int) -> float:
    """Reference: the raw logit of one token at the last prompt position, from an unbatched forward pass."""
    logits = child.model(input_ids=torch.tensor([prompt], device=child.device)).logits[0, -1].float()
    return logits[token].item()


def test_calibration_off_relation_margins_equal_the_naive_raw_margins(tiny) -> None:
    """With relation calibration off (the default) every margin is the raw logit difference of the best named relation
    and NONE, from the same prompt the child scores; nothing is mocked."""
    tokenizer, model = tiny
    child = HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu")
    assert child.relation_calibration is False
    pairs = [_pair(f"p{i}", f"q{i}") for i in range(6)]
    for (a, b), result in zip(pairs, child._relations(pairs)):
        order = HF.relation_order(a, b)
        prompt = child._prompt_ids(HF._messages("relation", (a, b, order)))
        raw = {name: naive_logit(child, prompt, child._option_ids(letter)[0])
               for letter, name in zip(HF.RELATION_LETTERS, order)}
        best = max((n for n in order if n != "NONE"), key=lambda n: raw[n])
        conf = raw[best] - raw["NONE"]
        assert result["conf"] == pytest.approx(conf, abs=1e-3)
        assert result["relation"] == (best if conf >= child.relation_floor else "NONE")


@pytest.mark.parametrize("env, expected", [
    ({"MB_HF_RELATION_FLOOR": "4.0"}, ((4.0, "default"), (False, "default"))),
    ({"MB_HF_RELATION_FLOOR": "6.5"}, ((6.5, "env"), (False, "default"))),
    ({"MB_HF_RELATION_CALIBRATION": "0"}, ((4.0, "default"), (False, "env"))),
    ({"MB_HF_RELATION_CALIBRATION": "1"}, ((4.0, "default"), (True, "env"))),
    ({"MB_HF_RELATION_CALIBRATION": "1", "MB_HF_CALIBRATE": "0"},
     ((4.0, "default"), (False, "env, disabled by MB_HF_CALIBRATE=0"))),
])
def test_from_env_accepts_the_relation_floor_and_calibration_settings(tiny, monkeypatch, env, expected) -> None:
    """The env paths the child accepts (an unpinned id, so no weights are read: the tiny model stands in for it)."""
    tokenizer, model = tiny
    monkeypatch.setattr(HF, "AutoTokenizer", SimpleNamespace(from_pretrained=lambda *a, **k: tokenizer))
    monkeypatch.setattr(HF, "AutoModelForCausalLM",
                        SimpleNamespace(from_pretrained=lambda *a, **k: SimpleNamespace(to=lambda device: model)))
    child = HF.HFChild.from_env({"MB_HF_MODEL": "tiny/random-qwen2", "MB_HF_DEVICE": "cpu", "MB_HF_ALLOW_UNPINNED": "1",
                                 **env})
    (floor, floor_source), (calibration, calibration_source) = expected
    assert child.describe()["relation_floor"] == {"value": floor, "source": floor_source}
    assert child.describe()["relation_calibration"] == {"value": calibration, "source": calibration_source}


def test_models_json_relation_settings_must_be_a_finite_number_and_a_boolean(tmp_path, monkeypatch) -> None:
    pins = tmp_path / "models.json"
    monkeypatch.setattr(HF, "PINS", pins)
    for bad in (True, "4.0", float("inf"), None):
        pins.write_text(json.dumps({"models": {"x/bad": {"relation_floor": bad, "revision": "a" * 40}}}), encoding="utf-8")
        with pytest.raises(ValueError, match="finite number"):
            HF.relation_floor_of("x/bad", None)
    pins.write_text(json.dumps({"models": {"x/bad": {"relation_floor": 4.0, "revision": "a" * 40}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="finite number"):
        HF.relation_floor_of("x/bad", True)  # an env value that is a bool is refused the same way


def test_relation_is_scored_raw_by_default(tiny, monkeypatch) -> None:
    """Relation calibration is off by default: no relation prior is computed, and the margin is the raw logit difference
    (version 8's decision, floor included)."""
    tokenizer, model = tiny
    child = HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu")
    assert child.relation_calibration is False
    assert not any(key[0] == "relation" for key in child._priors)
    a, b = _pair("a", "b")
    order = HF.relation_order(a, b)
    values = {"IMPLIES": 6.0, "IMPLIED_BY": 1.0, "EXCLUDES": 0.0, "NONE": 0.0}
    monkeypatch.setattr(child, "_score", lambda pairs, stage=None, raw=False: [values[n] for n in order])
    assert child._relations([(a, b)]) == [{"relation": "IMPLIES", "conf": pytest.approx(6.0)}]
    assert not any(key[0] == "relation" for key in child._priors)


def test_binary_label_sets_negated_without_the_model(child, monkeypatch) -> None:
    """A binary label (Yes, Over, Up: positive; No, Under, Down: negative) sets negated; the model decides others."""
    asked: list[str] = []
    real_stage = child._stage

    def spy(results, idx, stage, payload_of, options_of, size_of=None):
        if stage == "status":
            return {i: 0 for i in idx}  # every outcome is bound (STATUS index 0), so the negation stage is reached
        if stage == "negated":
            asked.extend(payload_of(i)["label"] for i in idx)
        return real_stage(results, idx, stage, payload_of, options_of, size_of)

    monkeypatch.setattr(child, "_stage", spy)
    labels = ["Yes", "No", "Over", "Under", "Lions"]
    ctxs = [ctx(f"o{k}", "Will the Lions win the Cup?", label) for k, label in enumerate(labels)]
    out = child._bindings(ctxs)
    assert [r["status"] for r in out] == ["bound"] * len(labels)
    assert [r["negated"] for r in out[:4]] == [False, True, False, True]
    assert asked == ["Lions"]  # the model is asked only about the label that is not binary


def test_few_shot_turns_precede_the_decision(child) -> None:
    turns = HF._messages("status", ctx("a", "Will the weather be nice soon?", "Yes"))
    assert turns[0]["role"] == "system" and turns[-1]["role"] == "user"
    assert [t["role"] for t in turns[1:-1]] == ["user", "assistant"] * len(HF.FEW_SHOT["status"])


def test_batch_envelope_decides_every_op_and_isolates_an_overlong_item(child) -> None:
    good = ctx("a", "Will Bitcoin close above 75,000 on May 29, 10AM ET?", "Yes")
    huge = ctx("big", "".join(str(i) for i in range(6000)), "Yes")  # about 22k tokens, over MAX_PROMPT_TOKENS
    group = {"group_id": "c1", "source": "s1", "members": [
        ctx("m1", "Who wins the Cup?", "Alpha"), ctx("m2", "Who wins the Cup?", "Beta"),
        ctx("m3", "Who wins the Cup?", "Gamma")]}
    calls = [
        {"op": "decide_binding", "ctx": good},
        {"op": "decide_relation", "a": good, "b": ctx("b", "Will Bitcoin close above 80,000 on May 29, 10AM ET?", "No")},
        {"op": "decide_binding", "ctx": huge},
        {"op": "decide_group", "ctx": group},
    ]
    reply = handle(child, json.dumps({"op": "batch", "id": 4, "calls": calls}))
    assert reply["id"] == 4 and len(reply["results"]) == len(calls)
    assert check_binding(reply["results"][0])["status"] in ("bound", "unresolved")
    assert check_relation(reply["results"][1]["relation"]) in RELATIONS
    assert "error" in reply["results"][2] and "tokens" in reply["results"][2]["error"]
    decision = reply["results"][3]["groups"]
    if decision:  # the child either states one decision for the candidate set or none (kind none)
        assert len(decision) == 1 and decision[0]["kind"] in HF.GROUP_KINDS
        assert decision[0]["scope"] is None
        check_group(decision[0], {"m1", "m2", "m3"})
    else:
        assert decision == []
    assert reply["tokens_in"] > 0 and reply["tokens_out"] > 0 and reply["cost_usd"] == 0.0
    assert reply["gpu_seconds"] >= 0.0 and reply["model"] == "tiny/random-qwen2"


def test_single_binding_is_deterministic_and_class_follows_question(child) -> None:
    question = "Will Bitcoin close above 75,000 on May 29, 10AM ET?"
    first = handle(child, json.dumps({"op": "decide_binding", "id": 1, "ctx": ctx("a", question, "Yes")}))
    again = handle(child, json.dumps({"op": "decide_binding", "id": 2, "ctx": ctx("b", question, "No")}))
    assert first["id"] == 1 and again["id"] == 2
    if first["status"] == "bound" and again["status"] == "bound":
        assert first["class"] == again["class"]  # equal questions share a class whatever the label


def test_oom_halves_the_batch_and_keeps_the_scores(child, monkeypatch) -> None:
    prompts = [child._prompt_ids(HF._messages("status", ctx(str(i), q, "Yes"))) for i, q in enumerate(CORPUS * 2)]
    pairs = [(p, child._option_ids(opt)) for p in prompts for opt in HF.STATUS]
    reference = child._score(pairs)
    original = child._forward_tails
    seen: list[int] = []

    def flaky(batch, *args):
        seen.append(len(batch))
        if len(batch) > 2:
            raise torch.cuda.OutOfMemoryError("simulated out of memory")
        return original(batch, *args)

    monkeypatch.setattr(child, "_forward_tails", flaky)
    scores = child._score(pairs)
    assert child._max_seqs == 2 and max(seen) > 2
    for got, want in zip(scores, reference):
        assert got == pytest.approx(want, abs=1e-4)


def random_pairs(child: HF.HFChild, stage: str, n: int, seed: int) -> list[tuple[list[int], list[int]]]:
    """n (prompt, option) pairs whose prompts start with the stage prefix followed by random suffixes of varied length
    (so the batches are right-padded), random option tokens."""
    gen = torch.Generator().manual_seed(seed)
    prefix = child._stage_prefix(stage)[0]
    vocab = child.model.config.vocab_size
    pairs = []
    for _ in range(n):
        suffix = torch.randint(0, vocab, (int(torch.randint(1, 13, (1,), generator=gen)),), generator=gen).tolist()
        option = torch.randint(0, vocab, (int(torch.randint(1, 4, (1,), generator=gen)),), generator=gen).tolist()
        pairs.append((prefix + suffix, option))
    return pairs


@pytest.fixture(scope="module")
def whole_scores(calibrated):
    """Full-row scores of the random pairs of a stage (one unbatched forward of prompt plus option per pair), computed once
    per module: the reference of every batch size and of the option-tail path."""
    cache: dict[str, tuple[list, list[float]]] = {}

    def get(stage: str) -> tuple[list, list[float]]:
        if stage not in cache:
            pairs = random_pairs(calibrated, stage, 20, seed=1)
            cache[stage] = (pairs, [naive_score(calibrated, p, o) for p, o in pairs])
        return cache[stage]

    return get


@pytest.mark.parametrize("batch", [1, 3, 17])
@pytest.mark.parametrize("stage", ["negated", "relation"])
def test_prefix_cache_scores_equal_uncached(child, whole_scores, stage, batch) -> None:
    pairs, uncached = whole_scores(stage)
    child._max_seqs = batch  # force micro-batches of exactly `batch` feeds
    cached = child._score(pairs, stage)
    assert cached == pytest.approx(uncached, abs=1e-3)


def test_option_tails_of_shared_prompts_equal_full_rows(child) -> None:
    """Options of one prompt share its head: every option of every stage's prompt scores as its own full row would, with
    one-token and multi-token options mixed, in any batch size."""
    for stage in ("kind", "missing", "order"):
        payloads = HF.CALIBRATION[stage](3) if stage in HF.CALIBRATION else []
        options = HF.GROUP_CHOICES if stage == "kind" else HF.MISSING if stage == "missing" else HF.ORDER_LETTERS[:3]
        pairs = []
        for payload in payloads:
            prompt = child._prompt_ids(HF._messages(stage, payload))
            pairs += [(prompt, child._option_ids(o)) for o in options]
        full = [naive_score(child, p, o) for p, o in pairs]
        for batch in (1, 4):
            child._max_seqs = batch
            assert child._score(pairs, stage) == pytest.approx(full, abs=1e-3), (stage, batch)


def test_prefix_cache_ignores_the_padding_token(child) -> None:
    pairs = random_pairs(child, "relation", 9, seed=5)
    child._max_seqs = 9
    first = child._score(pairs, "relation")
    child._pad = (child._pad + 1) % child.model.config.vocab_size
    second = child._score(pairs, "relation")
    assert second == pytest.approx(first, abs=1e-5)  # the mask, not the pad value, decides what attends


def test_prefix_cache_positions_follow_the_prefix(child) -> None:
    """A position-id mistake (positions from 0 instead of the prefix length) changes the scores; equality here shows
    the suffix is positioned after the cached prefix."""
    pairs = random_pairs(child, "negated", 4, seed=9)
    cached = child._score(pairs, "negated")
    uncached = child._score(pairs)
    assert cached == pytest.approx(uncached, abs=1e-3)
    prefix_len = len(child._stage_prefix("negated")[0])
    assert prefix_len > 50  # the few-shot prefix is long enough for the positions to matter


def test_prefix_cache_is_not_mutated_by_scoring(child) -> None:
    ids, legacy = child._stage_prefix("relation")
    before = [(k.clone(), v.clone()) for k, v in legacy]
    child._max_seqs = 8
    child._score(random_pairs(child, "relation", 16, seed=2), "relation")
    child._score(random_pairs(child, "relation", 16, seed=3), "relation")
    for (k, v), (k0, v0) in zip(legacy, before):
        assert torch.equal(k, k0) and torch.equal(v, v0)


def test_pairs_outside_the_prefix_fall_back_to_whole_sequences(child) -> None:
    pairs = random_pairs(child, "negated", 6, seed=4)
    other = [(prompt[1:], option) for prompt, option in pairs]  # no longer starts with the stage prefix
    mixed = pairs[:3] + other[3:]
    child._max_seqs = 6
    got = child._score(mixed, "negated")
    child._max_seqs = 6
    assert got == pytest.approx(child._score(mixed), abs=1e-3)


def test_stage_prefix_matches_the_start_of_every_real_prompt(child) -> None:
    for stage in HF.FEW_SHOT:
        ids, _ = child._stage_prefix(stage)
        payload = HF.CALIBRATION[stage](2)[0] if stage in HF.CALIBRATION else (ctx("a", "Will Q1?", "Yes"), ctx("b", "Will Q2?", "Yes"))
        prompt = child._prompt_ids(HF._messages(stage, payload))
        assert prompt[: len(ids)] == ids and len(prompt) > len(ids), stage


def _decision_payloads(stage: str) -> list:
    if stage in HF.CALIBRATION:
        return HF.CALIBRATION[stage](2)
    return [(a, b, HF.relation_order(a, b)) for a, b in (_pair("p", "q"), _pair("r", "s"))]


def test_split_ids_equal_the_full_chat_for_every_calibration_payload(child) -> None:
    for stage in HF.FEW_SHOT:
        payloads = _decision_payloads(stage)
        assert child._split[stage], stage
        assert child._prompts(stage, payloads) == [child._prompt_ids(HF._messages(stage, p)) for p in payloads], stage


def test_split_tokenizes_the_suffixes_in_one_batched_call(child, monkeypatch) -> None:
    calls: list[Any] = []
    tokenizer = child.tokenizer

    class Recording:
        def __getattr__(self, name: str) -> Any:
            return getattr(tokenizer, name)

        def __call__(self, text: Any, **kwargs: Any) -> Any:
            calls.append(text)
            return tokenizer(text, **kwargs)

    monkeypatch.setattr(child, "tokenizer", Recording())
    payloads = _decision_payloads("relation")
    got = child._prompts("relation", payloads)
    assert len(calls) == 1 and len(calls[0]) == len(payloads)  # one batched call for the stage's suffixes
    assert got == [child._prompt_ids(HF._messages("relation", p)) for p in payloads]


def test_a_stage_whose_split_ids_differ_is_tokenized_whole(child, monkeypatch) -> None:
    ids, legacy = child._stage_prefix("negated")
    monkeypatch.setitem(child._prefixes, "negated", ([ids[0] + 1] + ids[1:], legacy))  # not the start of the chat
    assert child._split_matches("negated") is False  # the setup check catches it
    payloads = _decision_payloads("negated")
    monkeypatch.setitem(child._split, "negated", False)
    assert child._prompts("negated", payloads) == [child._prompt_ids(HF._messages("negated", p)) for p in payloads]


def test_prefix_cache_flag_off_scores_whole_sequences(tiny) -> None:
    tokenizer, model = tiny
    off = HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu", calibrate=False, prefix_cache=False)
    assert off._prefixes == {}
    assert off.describe()["model"]["prefix_cache"] is False
    pairs = random_pairs(off, "negated", 5, seed=6)
    assert off._score(pairs, "negated") == off._score(pairs)


def test_prefix_cache_flag_parsing() -> None:
    assert HF.parse_prefix_cache("1") is True and HF.parse_prefix_cache("0") is False
    with pytest.raises(ValueError, match="MB_HF_PREFIX_CACHE"):
        HF.parse_prefix_cache("on")


def test_oom_halves_the_cached_batch(child, monkeypatch) -> None:
    pairs = random_pairs(child, "relation", 8, seed=11)
    reference = child._score(pairs)
    original = child._forward_tails
    seen: list[int] = []

    def flaky(batch, prefix, *args):
        seen.append(len(batch))
        if len(batch) > 2:
            raise torch.cuda.OutOfMemoryError("simulated out of memory")
        return original(batch, prefix, *args)

    monkeypatch.setattr(child, "_forward_tails", flaky)
    scores = child._score(pairs, "relation")
    assert child._max_seqs == 2 and max(seen) > 2
    assert scores == pytest.approx(reference, abs=1e-3)


def test_oom_is_logged_and_the_limits_regrow_after_clean_forwards(child, monkeypatch, capsys) -> None:
    pairs = random_pairs(child, "relation", 8, seed=12)
    reference = child._score(pairs)
    original = child._forward_tails
    failed: list[bool] = []

    def flaky(batch, prefix, *args):
        if len(batch) > 2 and not failed:  # one out-of-memory, then clean forward passes
            failed.append(True)
            raise torch.cuda.OutOfMemoryError("simulated out of memory")
        return original(batch, prefix, *args)

    monkeypatch.setattr(child, "_forward_tails", flaky)
    monkeypatch.setattr(HF, "REGROW_AFTER", 2)
    child._max_seqs = 8
    scores = child._score(pairs, "relation")
    assert "out of memory" in capsys.readouterr().err  # the halving is logged
    assert scores == pytest.approx(reference, abs=1e-3)
    assert child._max_seqs > 4  # halved to 4 by the failure, doubled back after two clean forward passes
    limits = child.describe()["limits"]
    assert limits["oom_halvings"] == 1 and limits["startup"]["max_seqs"] == child._full[0]  # recorded in describe()


def test_kv_budget_is_half_the_free_device_memory_capped_at_kv_bytes(monkeypatch) -> None:
    assert HF.kv_bytes("cpu") == HF.KV_BYTES
    monkeypatch.setattr(HF.torch.cuda, "mem_get_info", lambda device: (2 * 2**30, 49 * 2**30))
    assert HF.kv_bytes("cuda:0") == 2**30
    monkeypatch.setattr(HF.torch.cuda, "mem_get_info", lambda device: (200 * 2**30, 0))
    assert HF.kv_bytes("cuda:0") == HF.KV_BYTES


def test_oversized_candidate_set_gets_no_decision_not_an_error(child) -> None:
    """A group whose prompt is over MAX_PROMPT_TOKENS is answered with kind none, never with an item error."""
    members = [ctx(f"m{i}", "".join(str((i * 7919 + k) % 10) for k in range(200)), "Yes") for i in range(64)]
    reply = handle(child, json.dumps({"op": "decide_group", "id": 8, "ctx": {
        "group_id": "big", "source": "s2", "members": members}}))
    assert "error" not in reply
    assert reply["groups"] == []


def test_group_call_with_mixed_sizes_keeps_small_candidates(child) -> None:
    small = {"group_id": "s", "source": "s1", "members": [ctx("a", "Who wins the Cup?", "Alpha"),
                                                          ctx("b", "Who wins the Cup?", "Beta")]}
    members = [ctx(f"m{i}", "".join(str((i * 7919 + k) % 10) for k in range(200)), "Yes") for i in range(64)]
    big = {"group_id": "b", "source": "s2", "members": members}
    reply = handle(child, json.dumps({"op": "batch", "id": 9, "calls": [
        {"op": "decide_group", "ctx": small}, {"op": "decide_group", "ctx": big}]}))
    assert "error" not in reply["results"][0] and "error" not in reply["results"][1]
    assert reply["results"][1]["groups"] == []


def test_group_conf_is_inside_the_decision_and_reaches_check_group(child, monkeypatch) -> None:
    """The margin of the chosen kind rides inside the group decision, the object the adapter and check_group read."""
    kinds = HF.GROUP_CHOICES
    margins = [0.0] * len(kinds)
    margins[kinds.index("exactly_one")] = 2.5
    real_scores = child._scores

    def scores(results, idx, stage, payload_of, options_of, size_of=None):
        if stage == "kind":
            return {0: margins}
        return real_scores(results, idx, stage, payload_of, options_of, size_of)

    monkeypatch.setattr(child, "_scores", scores)
    group ={"group_id": "g", "source": "s1", "members": [ctx("m1", "Who wins the Cup?", "Alpha"),
                                                          ctx("m2", "Who wins the Cup?", "Beta")]}
    reply = handle(child, json.dumps({"op": "decide_group", "id": 1, "ctx": group}))
    assert "conf" not in reply
    decisions = _decision("decide_group", reply)
    assert check_group(decisions[0], {"m1", "m2"})["conf"] == pytest.approx(2.5)


def test_non_finite_score_fails_only_its_item(child, monkeypatch) -> None:
    ctxs = [ctx(str(i), q, "Yes") for i, q in enumerate(CORPUS[:3])]
    real_score = child._score

    def scorer(pairs, stage=None, raw=False):
        values = real_score(pairs, stage)
        if stage == "status":
            values[2:4] = [math.nan, math.nan]  # the two status options of item 1 (pairs are in item order)
        return values

    monkeypatch.setattr(child, "_score", scorer)
    results: list = [None] * len(ctxs)
    choice = child._stage(results, [0, 1, 2], "status", lambda i: ctxs[i], lambda i: HF.STATUS)
    assert isinstance(results[1], ChildError) and 1 not in choice
    assert results[0] is None and results[2] is None and set(choice) == {0, 2}


def test_non_finite_group_score_is_an_error_not_no_constraint(child, monkeypatch) -> None:
    real_score = child._score

    def scorer(pairs, stage=None, raw=False):
        values = real_score(pairs, stage)
        return [math.nan] * len(values) if stage == "kind" else values

    monkeypatch.setattr(child, "_score", scorer)
    group = {"group_id": "g", "source": "s1", "members": [ctx("m1", "Who wins the Cup?", "Alpha"),
                                                          ctx("m2", "Who wins the Cup?", "Beta")]}
    reply = handle(child, json.dumps({"op": "decide_group", "id": 1, "ctx": group}))
    assert "error" in reply and "groups" not in reply


CHAIN_MARKETS = [  # a threshold ladder with both sides of each market, a named outcome and a negative-only market
    ("a35", "Will the high temperature exceed 35 C on 3 March?", "Yes"),
    ("a35n", "Will the high temperature exceed 35 C on 3 March?", "No"),
    ("a30", "Will the high temperature exceed 30 C on 3 March?", "Yes"),
    ("a30n", "Will the high temperature exceed 30 C on 3 March?", "No"),
    ("a25", "Will the high temperature exceed 25 C on 3 March?", "Yes"),
    ("team", "Who wins the Cup?", "Alpha"),
    ("a20n", "Will the high temperature exceed 20 C on 3 March?", "No"),
]


def _force_kind(child, monkeypatch, kind: str) -> None:
    """The kind stage's margins favour `kind`; every other stage is the model's."""
    real_scores = child._scores

    def scores(results, idx, stage, payload_of, options_of, size_of=None):
        if stage == "kind":
            margins = [0.0] * len(HF.GROUP_CHOICES)
            margins[HF.GROUP_CHOICES.index(kind)] = 2.5
            return {i: margins for i in idx if results[i] is None}
        return real_scores(results, idx, stage, payload_of, options_of, size_of)

    monkeypatch.setattr(child, "_scores", scores)


def test_chain_members_keep_one_outcome_per_binary_market_and_its_positive_side() -> None:
    members = [ctx(*spec) for spec in CHAIN_MARKETS]
    assert [m["id"] for m in chain_members(members)] == ["a35", "a30", "a25", "team", "a20n"]


def test_ordered_chain_names_positive_outcomes_only_and_checks_as_a_group(child, monkeypatch) -> None:
    _force_kind(child, monkeypatch, "ordered_chain")
    group = {"group_id": "g", "source": "s2", "members": [ctx(*spec) for spec in CHAIN_MARKETS]}
    reply = handle(child, json.dumps({"op": "decide_group", "id": 1, "ctx": group}))
    assert "error" not in reply
    decision = check_group(_decision("decide_group", reply)[0], {m["id"] for m in group["members"]})
    assert decision["kind"] == "ordered_chain" and decision["direction"] == "forward"
    assert sorted(decision["members"]) == ["a20n", "a25", "a30", "a35", "team"]  # no No side of a market with a Yes


def test_order_stage_places_the_strongest_each_round_and_asks_no_question_for_the_last(child, monkeypatch) -> None:
    """With a menu that favours its last letter, each round places the last member left: the chain is the listing reversed.
    Four members take three questions, with menus of 4, 3 and 2 letters (the last member needs none)."""
    _force_kind(child, monkeypatch, "ordered_chain")
    menus: list[int] = []
    real_scores = child._scores

    def scores(results, idx, stage, payload_of, options_of, size_of=None):
        if stage != "order":
            return real_scores(results, idx, stage, payload_of, options_of, size_of)
        menus.append(len(options_of(idx[0])))
        return {i: [float(j) for j in range(len(options_of(i)))] for i in idx if results[i] is None}

    monkeypatch.setattr(child, "_scores", scores)
    members = [ctx(*CHAIN_MARKETS[i]) for i in (0, 2, 4, 5)]  # a35, a30, a25, team: one side each
    group = {"group_id": "g", "source": "s2", "members": members}
    first = _decision("decide_group", handle(child, json.dumps({"op": "decide_group", "id": 1, "ctx": group})))
    second = _decision("decide_group", handle(child, json.dumps({"op": "decide_group", "id": 1, "ctx": group})))
    assert menus == [4, 3, 2, 4, 3, 2]  # one call per round, twice (the second run is the same)
    assert first == second
    assert first[0]["members"] == ["team", "a25", "a30", "a35"]


def test_chain_with_fewer_than_two_or_more_than_26_members_is_no_group(child, monkeypatch) -> None:
    _force_kind(child, monkeypatch, "ordered_chain")
    one = {"group_id": "g", "source": "s1", "members": [ctx("a35", *CHAIN_MARKETS[0][1:]), ctx("a35n", *CHAIN_MARKETS[1][1:])]}
    assert handle(child, json.dumps({"op": "decide_group", "id": 1, "ctx": one}))["groups"] == []
    wide = {"group_id": "w", "source": "s2",
            "members": [ctx(f"w{i}", f"Will Team {i} win?", "Yes") for i in range(27)]}
    assert handle(child, json.dumps({"op": "decide_group", "id": 2, "ctx": wide}))["groups"] == []


def test_non_finite_prior_fails_loudly_and_is_not_cached(tiny, child, monkeypatch) -> None:
    tokenizer, model = tiny
    monkeypatch.setattr(child, "_score", lambda pairs, stage=None, raw=False: [math.nan] * len(pairs))
    with pytest.raises(RuntimeError, match="non-finite"):
        child._prior("k", HF.k_options(9), 9)  # a lazily computed prior
    assert ("k", HF.k_options(9), 9) not in child._priors
    monkeypatch.setattr(HF.HFChild, "_score", lambda self, pairs, stage=None, raw=False: [math.nan] * len(pairs))
    with pytest.raises(RuntimeError, match="non-finite"):
        HF.HFChild("tiny/random-qwen2", None, tokenizer, model, "cpu")  # a warmed prior at startup


def test_snapshot_verification_checks_size_always_and_hash_on_request(tiny, tmp_path) -> None:
    import hashlib

    tokenizer, model = tiny
    tokenizer.save_pretrained(tmp_path)
    model.save_pretrained(tmp_path)
    files = [p for p in sorted(tmp_path.iterdir()) if p.is_file()]
    assert files, "the tiny model wrote no files"

    def pin_for(directory) -> dict:
        entries = []
        for path in sorted(directory.iterdir()):
            if path.is_file():
                data = path.read_bytes()
                entries.append({"path": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        return {"files": entries}

    pin = pin_for(tmp_path)
    HF.verify_snapshot(pin, tmp_path, hash_contents=True)  # the files as written match their pin

    weights = tmp_path / "model.safetensors"
    original = weights.read_bytes()
    flipped = bytearray(original)
    flipped[-10] ^= 0xFF  # same size, different content
    weights.write_bytes(bytes(flipped))
    HF.verify_snapshot(pin, tmp_path, hash_contents=False)  # size check only: passes
    with pytest.raises(RuntimeError, match="sha256"):
        HF.verify_snapshot(pin, tmp_path, hash_contents=True)

    weights.write_bytes(original + b"x")  # size changed: caught without hashing
    with pytest.raises(RuntimeError, match="bytes"):
        HF.verify_snapshot(pin, tmp_path, hash_contents=False)

    weights.unlink()
    with pytest.raises(RuntimeError, match="missing"):
        HF.verify_snapshot(pin, tmp_path, hash_contents=False)


def test_revision_pins_and_overrides() -> None:
    pinned = json.loads(HF.PINS.read_text(encoding="utf-8"))["models"]["Qwen/Qwen2.5-1.5B-Instruct"]["revision"]
    assert HF.resolve_revision("Qwen/Qwen2.5-1.5B-Instruct", None, False) == pinned
    assert HF.resolve_revision("Qwen/Qwen2.5-1.5B-Instruct", pinned, False) == pinned
    with pytest.raises(RuntimeError, match="is not the pin"):
        HF.resolve_revision("Qwen/Qwen2.5-1.5B-Instruct", "0" * 40, False)
    assert HF.resolve_revision("Qwen/Qwen2.5-1.5B-Instruct", "0" * 40, True) == "0" * 40
    with pytest.raises(RuntimeError, match="no pin"):
        HF.resolve_revision("example/unpinned", None, False)
    assert HF.resolve_revision("example/unpinned", None, True) is None


@pytest.mark.skipif(os.environ.get("MB_HF_MODEL_TESTS") != "1",
                    reason="real-model smoke test is opt-in: set MB_HF_MODEL_TESTS=1 (needs the pinned weights cached)")
def test_real_pinned_model_smoke_through_the_protocol(monkeypatch) -> None:
    monkeypatch.setattr(HF, "MAX_PROMPT_TOKENS", 4096)  # the production limit: the real relation prompt must fit it
    real = HF.HFChild.from_env({"MB_HF_MODEL": "Qwen/Qwen2.5-0.5B-Instruct",
                                "MB_HF_DEVICE": os.environ.get("MB_HF_DEVICE", "cuda:0")})
    assert all(len(real._option_ids(letter)) == 1 for letter in HF.RELATION_LETTERS)
    binding = ctx("a", "Will Bitcoin close above 75,000 on May 29, 10AM ET?", "Yes")
    reply = handle(real, json.dumps({"op": "decide_binding", "id": 1, "ctx": binding}))
    assert check_binding(reply)["status"] in ("bound", "unresolved")
    assert reply["model"] == "Qwen/Qwen2.5-0.5B-Instruct" and reply["gpu_seconds"] >= 0.0
    rel = handle(real, json.dumps({"op": "decide_relation", "id": 2, "a": binding,
                                   "b": ctx("b", "Will Bitcoin close above 80,000 on May 29, 10AM ET?", "Yes")}))
    assert "error" not in rel
    assert check_relation(rel["relation"]) in RELATIONS



@pytest.mark.skipif(os.environ.get("MB_HF_MODEL_TESTS") != "1",
                    reason="real-model test is opt-in: set MB_HF_MODEL_TESTS=1 (needs the pinned weights cached)")
def test_real_1p5b_chain_order_is_positive_outcomes_and_option_tails_match_full_rows(monkeypatch) -> None:
    monkeypatch.setattr(HF, "MAX_PROMPT_TOKENS", 4096)
    real = HF.HFChild.from_env({"MB_HF_MODEL": "Qwen/Qwen2.5-1.5B-Instruct",
                                "MB_HF_DEVICE": os.environ.get("MB_HF_DEVICE", "cuda:0")})
    members = [ctx(*spec) for spec in CHAIN_MARKETS]
    placed = real._order([None], [0], lambda i: members)[0]
    assert sorted(m["id"] for m in placed) == ["a20n", "a25", "a30", "a35", "team"]  # the positive side of each market
    # The tail check runs in float32: bf16 full rows differ from any other batch shape by up to 0.2 nats on this prompt.
    real.model.float()
    real.dtype = torch.float32
    real._prefixes.clear()
    prompt = real._prompt_ids(HF._messages("kind", members))
    kind_pairs = [(prompt, real._option_ids(option)) for option in HF.GROUP_CHOICES]
    full = [naive_score(real, p, o) for p, o in kind_pairs]
    assert real._score(kind_pairs, "kind") == pytest.approx(full, abs=1e-3)


TOKENIZER_PINS = ("Qwen/Qwen2.5-0.5B-Instruct", "HuggingFaceTB/SmolLM2-1.7B-Instruct")


@pytest.mark.slow
@pytest.mark.parametrize("model_id", TOKENIZER_PINS)
def test_pinned_tokenizer_split_ids_equal_the_full_chat(model_id) -> None:
    """The real pinned tokenizer and chat template (cached files only, no weights): every stage's split is in use and its
    ids equal the full-chat ids, for the calibration payloads and one real relation pair. The model is random with the
    real vocabulary and a tiny width, since the split is a property of the tokenizer and the template. Skipped when the
    pinned files are not cached."""
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    pin = HF.resolve_revision(model_id, None, False)
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_id, revision=pin, local_files_only=True)
        config = AutoConfig.from_pretrained(model_id, revision=pin, local_files_only=True)
    except OSError as exc:
        pytest.skip(f"pinned tokenizer files of {model_id} are not cached: {exc}")
    config.update({"vocab_size": len(tokenizer), "hidden_size": 32, "intermediate_size": 64, "num_hidden_layers": 2,
                   "num_attention_heads": 4, "num_key_value_heads": 2, "head_dim": 8})
    torch.manual_seed(0)
    child = HF.HFChild(model_id, pin, tokenizer, AutoModelForCausalLM.from_config(config), "cpu", calibrate=False)
    assert all(child._split.values()), child._split
    real = ctx("a", "Will Bitcoin close above 75,000 on May 29, 10AM ET?", "Yes")
    real_pair = (real, ctx("b", "Will Bitcoin close above 80,000 on May 29, 10AM ET?", "No"))
    for stage in HF.FEW_SHOT:
        payloads = _decision_payloads(stage) + ([(*real_pair, HF.relation_order(*real_pair))] if stage == "relation" else [])
        assert child._prompts(stage, payloads) == [child._prompt_ids(HF._messages(stage, p)) for p in payloads], stage


@pytest.mark.slow
def test_conformance_runner_passes_on_the_hf_child_with_the_tiny_model(tmp_path) -> None:
    """The HF child's JSON-lines protocol is checked by the conformance runner without any download: a launcher loads
    the tiny random-weight model on CPU and runs the child's own serve loop (hf_child is not modified for this)."""
    import subprocess
    import sys

    tests_dir = Path(__file__).resolve().parent
    python_dir = tests_dir.parent / "python"
    launcher = tmp_path / "tiny_hf_child.py"
    launcher.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(tests_dir)!r})\n"
        "from test_hf_child import tiny_pair\n"
        "from mentat_bench.children import hf_child as HF\n"
        "from mentat_bench.children.protocol import serve\n"
        "HF.MAX_PROMPT_TOKENS = 8192\n"  # the byte-level tokenizer needs it for the relation few-shot turns
        "tokenizer, model = tiny_pair()\n"
        "serve(HF.HFChild('tiny/random-qwen2', None, tokenizer, model, 'cpu'))\n",
        encoding="utf-8",
    )
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTHONPATH": str(python_dir)}
    proc = subprocess.run(
        [sys.executable, "-m", "mentat_bench.children.conformance", sys.executable, str(launcher)],
        env=env, capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "conformance:" in proc.stdout and "FAIL" not in proc.stdout

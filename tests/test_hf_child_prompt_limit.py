"""The order stage's largest menu under the production prompt limit. This module does not patch MAX_PROMPT_TOKENS (the
byte-level test tokenizer of test_hf_child.py needs a larger limit, so that module raises it): the pinned tokenizer and
chat template decide the length. Cached files only; the test is skipped when they are not cached. The model is a tiny
random-weight one with the pinned vocabulary, since only the child's startup and its scoring need one."""

from __future__ import annotations

import pytest

try:
    import torch  # noqa: F401
    import transformers  # noqa: F401
except ImportError as exc:
    pytest.skip(f"optional torch/transformers dependency unavailable: {exc}", allow_module_level=True)

from mentat_bench.children import hf_child as HF  # noqa: E402
from mentat_bench.children.protocol import ChildError  # noqa: E402

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"  # the pinned tokenizer and chat template


def _members(n: int) -> list[dict[str, str]]:
    """n positive threshold questions of about 200 characters each, one event: a candidate set of n chain members."""
    return [{"id": f"m{k:02d}",
             "question": (f"Will the high temperature in coastal district {k:02d} exceed {20 + k} degrees C on 3 March, "
                          "as published in the national weather service bulletin and in its regional summaries, "
                          "after the final correction?"),
             "label": "Yes", "event_title": "Coastal temperatures"} for k in range(n)]


@pytest.fixture(scope="module")
def child():
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    pin = HF.resolve_revision(MODEL, None, False)
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=pin, local_files_only=True)
        config = AutoConfig.from_pretrained(MODEL, revision=pin, local_files_only=True)
    except OSError as exc:
        pytest.skip(f"pinned tokenizer files of {MODEL} are not cached: {exc}")
    config.update({"vocab_size": len(tokenizer), "hidden_size": 32, "intermediate_size": 64, "num_hidden_layers": 2,
                   "num_attention_heads": 4, "num_key_value_heads": 2, "head_dim": 8})
    torch.manual_seed(0)
    return HF.HFChild(MODEL, pin, tokenizer, AutoModelForCausalLM.from_config(config), "cpu", calibrate=False)


def test_the_limit_under_test_is_the_production_value() -> None:
    assert HF.MAX_PROMPT_TOKENS == 4096


@pytest.mark.slow
def test_a_26_member_order_menu_fits_the_production_limit_and_is_accepted(child) -> None:
    members = _members(26)
    assert all(175 <= len(m["question"]) <= 225 for m in members)  # about 200 characters each
    prompt = child._prompts("order", [members])[0]
    assert not isinstance(prompt, ChildError) and len(prompt) <= HF.MAX_PROMPT_TOKENS
    assert len(prompt) > 1000  # the menu is large: the test would be weak for a short prompt
    results: list = [None]
    placed = child._order(results, [0], lambda i: members)  # 26 members: 25 rounds, none refused
    assert results[0] is None and sorted(m["id"] for m in placed[0]) == [m["id"] for m in members]

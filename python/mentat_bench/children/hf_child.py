"""Jev-like reference child: a pinned open-weights causal LM answering typed decisions by constrained choice.

Every decision is a closed option set. Each option is scored by the mean log-probability of its tokens after a chat
prompt; the scores come from forward passes over the prompt+option sequences; the argmax is the decision. Nothing is
generated freely, so every response is schema-valid. No sampling: eval mode, fixed seed, length-sorted micro-batches
under a token budget (halved on CUDA out-of-memory, doubled back after clean forward passes). Determinism is per batch composition: the same request batch gives
the same decisions on the same device. A near-tie in bf16 can flip when the composition of the batch changes, because
padding and batch shape change the floating-point reduction order. Two single-item calls are not guaranteed to match a
batched call bit for bit.

Prompts. Each stage has a fixed list of hand-written few-shot examples (invented topics: weather, sports, prices; no
fixture or Polymarket text), in a fixed order, covering every fixed option. The order stage's letter menus grow with the
member count, and its examples answer A to C only: widening them is a prompt revision that needs its own measurement.

Batch shape and bf16 rounding. Scores come from bf16 forward passes, so their last bits depend on the batch shape. The
shape follows the startup KV budget (half the free device memory at startup, at most KV_BYTES: another device or free
memory gives another budget) and the batch limits carried across calls (an out-of-memory halves them, and REGROW_AFTER
clean forward passes double them back). A decision within a bf16 step of its threshold can therefore differ between
runs on other hardware or after an out-of-memory. describe() reports the budgets and the halvings; the bench header
records them.

Shared-prefix KV cache (MB_HF_PREFIX_CACHE=1, default). The few-shot turns of a stage are the same for every decision of
that stage, so their key/value cache is computed once per stage (batch 1, lazily or at startup). A batch then runs only
the variable suffix, attending to the prefix cache expanded across the batch. The expanded cache is a fresh copy per
forward pass, so the stored prefix is never mutated. A prompt whose token prefix does not match the stage prefix is
scored without the cache, so the cache never changes what is scored.

Option tails. Each prompt runs once, as its head (the prompt from the prefix on, its last token included). Every
option's first token is read at the head's last position, so one-token options (the letters, the k and the negation
words) need nothing more. A multi-token option's remaining tokens are read from a tail: the option's tokens but the last,
run against a copy of the head's cache. A six-option kind decision therefore costs one head pass and five short tails,
not six full prompts. The scores equal the full-row scores up to floating-point noise (tests/test_hf_child.py checks
this against unbatched full sequences on the tiny model; in bf16 the rounding of a full row is up to about 0.2 nats).

Contextual calibration (MB_HF_CALIBRATE=1, default). Raw option log-probabilities favour frequent option strings, so
each stage subtracts a prior: the mean option score over three content-free inputs ("N/A", "", "the thing") run through
the same stage prompt. The relation prior is the mean raw logit of each letter over the content-free pairs, each under
the four rotations of the relation names, so every relation sits on every letter once and only the letter's own bias
is left (the per-pair order still shuffles the menu). The relation prior applies only when relation calibration is on
(relation_calibration in models.json, off by default; MB_HF_RELATION_CALIBRATION overrides it). Otherwise relations are
the raw argmax, as in version 8. The prior is computed once per (stage, option set, member count)
and kept in memory (per model revision, for the life of the process). kind is calibrated on a fixed three-member listing
whatever the candidate set's size (its prior is keyed with member count 0); k and order are keyed by their member count
(order by the members not yet placed), the count their options depend on. Startup computes the priors of the relation and
other stages, of the k stage for 2 to MAX_K members and of the order stage for 2 to 26 members; a k prior for more members
is computed on its first use, inside the call that needs it (its forward passes count in that call's gpu_seconds). The
decision is the argmax of raw minus prior. With MB_HF_CALIBRATE=0 the decision is the argmax of the raw scores.

Configuration (environment):
- MB_HF_MODEL: Hugging Face model id (default Qwen/Qwen2.5-1.5B-Instruct).
- MB_HF_REVISION: commit sha. Default: the pin in models.json. Any other revision needs MB_HF_ALLOW_UNPINNED=1.
- MB_HF_DEVICE: torch device (default cuda:0). bfloat16 on CUDA, float32 elsewhere.
- MB_HF_CALIBRATE: 1 (default) or 0.
- MB_HF_PREFIX_CACHE: 1 (default) or 0.
- MB_HF_RELATION_FLOOR: a number of nats that may only raise the relation floor of the model (see relation_floor_of).
- MB_HF_RELATION_CALIBRATION: 1 or 0, overrides the model's relation_calibration (see relation_calibration_of). It has
  no effect with MB_HF_CALIBRATE=0.

Decisions (see docs/local_protocol.md):
- bindings: status (bound|unresolved); negated (false|true) when bound; a missing code when unresolved. The class key
  is not generated: it is common.class_key(question, label), so equal questions share a class. negated of a binary
  label (Yes, Over, Up, No, Under, Down) is read from the label; the model decides it only for other labels.
- relations: four letter options (IMPLIES, IMPLIED_BY, EXCLUDES, NONE) in a per-pair order (relation_order). The
  decision is the best named relation when its margin over NONE is at least the model's relation floor, else NONE
  (relation_floor_of: models.json, else RELATION_FLOOR). The margin (the response's optional conf, nats) is the
  difference of the two projected logits, less the letter prior when relation calibration is on, so the normaliser
  cancels. The
  logits are bf16 values: with calibration off a margin lies on the bf16 grid, and a decision within two bf16 steps of the floor is
  rounding-sensitive (a margin of exactly the floor is named, by the >= rule, but another batch shape can round each of the
  two logits one step, moving the margin by up to two steps; other stages' near-ties are likewise rounding-sensitive).
  See docs/local_protocol.md. EQUIV and UNRESOLVED are never emitted.
- groups: kind (none or a GROUP_KINDS value) for the whole candidate set, then k (at_least_k, at_most_k). The kind and
  k decisions see the whole candidate set. An ordered_chain (version 8) is decided on fewer members: common.chain_members keeps
  one outcome per binary market (its positive side, or its negative side when the set holds no positive one) and every
  named outcome. The order stage then places those members strongest first by repeated letter menus: "which is the
  strongest" over the members not yet placed, the last member left is placed without a question. The chain is
  {"direction": "forward"} with the members in that order (each outcome implies the next). Fewer than two members, or
  more than 26 (one letter each: shortcut, a larger chain is not decided), give {"groups": []}. The scope is always
  null: this child never emits scopes.

Usage: tokens_in counts the prompt tokens of every stage (the cached prefix included, so usage does not depend on the
cache); tokens_out the tokens of the chosen options; cost_usd is 0.0; gpu_seconds is the synchronised wall time of the
forward passes of the call. Prefix caches and the priors of the fixed stages are computed at startup, outside any call.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sys
import time
import zlib
from pathlib import Path
from typing import Any, Callable

# torch's CUDA caching allocator reads this when it starts, so it is set before torch is imported. Expandable segments
# return freed blocks instead of holding fragments. Windows builds do not support them (a warning, no effect), so the
# default is set on the other platforms only.
if sys.platform != "win32":
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import huggingface_hub  # noqa: E402
import torch  # noqa: E402
import transformers  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache  # noqa: E402

from ..adapters.base import GROUP_KINDS  # noqa: E402
from .common import binary_label, chain_members, class_key  # noqa: E402
from .protocol import ChildError, serve  # noqa: E402

DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
PINS = Path(__file__).with_name("models.json")
MAX_PROMPT_TOKENS = 4096
TOKEN_BUDGET = 32768  # padded suffix tokens per forward pass
KV_BYTES = 6 * 2**30  # cap on the key/value memory of the rows of one cached forward pass (prefix copies and suffixes)
REGROW_AFTER = 50  # clean forward passes after an out-of-memory halving before the batch limits double back
MAX_SEQS = 128
MAX_K = 8

STATUS = ("bound", "unresolved")
NEGATED_OPTIONS = ("no", "yes")  # the words the model chooses between: option 0 positive side, option 1 negative side
MISSING = ("no_date", "no_source", "no_threshold", "ambiguous")
# The relations this child decides. Option letters A-D are single tokens, so each option is one log-probability.
# EQUIV and UNRESOLVED are left out: on a 6-way menu the 0.5B and 1.5B models chose the first letter for most pairs.
RELATION_CHOICES = ("IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")
RELATION_LETTERS = ("A", "B", "C", "D")
RELATION_MEANING = {"IMPLIES": "A implies B", "IMPLIED_BY": "B implies A", "EXCLUDES": "not both",
                    "NONE": "no such relation"}
# A named relation is decided only when its calibrated logit beats NONE's by at least the model's relation floor, in nats
# (the conf of the response). RELATION_FLOOR is the default for a model without one in models.json (relation_floor_of).
RELATION_FLOOR = 4.0
GROUP_CHOICES = ("none",) + GROUP_KINDS
ORDER_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # the order stage's menu: one letter per chain member (at most 26)
CONTENT_FREE = ("N/A", "", "the thing")
SYSTEM = (
    "You label one or two outcomes of a prediction market for a benchmark. "
    "Answer with exactly one of the listed options."
)


def _finite_number(value: Any, what: str) -> float:
    """value as a float, or ValueError: a finite number, not a bool."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} must be a finite number, got {value!r}")
    return float(value)


def relation_floor_of(model_id: str, requested: float | None) -> tuple[float, str]:
    """The relation floor in use and its source. The model's default is its models.json relation_floor (source
    "models.json"), else RELATION_FLOOR ("default"). A requested floor (MB_HF_RELATION_FLOOR) may only tighten it: a
    higher value is used (source "env"), a lower one raises. An equal one keeps the default's source."""
    pin = json.loads(PINS.read_text(encoding="utf-8"))["models"].get(model_id, {})
    if "relation_floor" in pin:
        default = _finite_number(pin["relation_floor"], f"relation_floor of {model_id} in models.json")
        source = "models.json"
    else:
        default, source = RELATION_FLOOR, "default"
    if requested is None:
        return default, source
    requested = _finite_number(requested, "MB_HF_RELATION_FLOOR")
    if requested < default:
        raise ValueError(f"MB_HF_RELATION_FLOOR must be a number of at least {default} (the floor of {model_id}), "
                         f"got {requested!r}: the floor may only tighten")
    return (requested, "env") if requested > default else (default, source)


def relation_calibration_of(model_id: str, requested: bool | None) -> tuple[bool, str]:
    """Whether relation letters are calibrated, and the source: MB_HF_RELATION_CALIBRATION (requested, source "env"),
    else the model's models.json relation_calibration ("models.json"), else off ("default")."""
    pin = json.loads(PINS.read_text(encoding="utf-8"))["models"].get(model_id, {})
    if "relation_calibration" in pin:
        if not isinstance(pin["relation_calibration"], bool):
            raise ValueError(f"relation_calibration of {model_id} in models.json must be true or false")
        default, source = pin["relation_calibration"], "models.json"
    else:
        default, source = False, "default"
    return (default, source) if requested is None else (requested, "env")


class NonFiniteScore(ChildError):
    """An item whose option score is NaN or infinite: an error for that item, never a decision."""


def k_options(n: int) -> tuple[str, ...]:
    """The k values a group of n members may state: 1 to min(n, MAX_K)."""
    return tuple(str(k) for k in range(1, min(n, MAX_K) + 1))


def resolve_revision(model_id: str, env_revision: str | None, allow_unpinned: bool) -> str | None:
    """The revision to load. A revision other than the pin in models.json needs allow_unpinned."""
    pinned = json.loads(PINS.read_text(encoding="utf-8"))["models"].get(model_id, {}).get("revision")
    revision = env_revision or pinned
    if allow_unpinned:
        return revision
    if pinned is None:
        raise RuntimeError(f"{model_id} has no pin in models.json; set MB_HF_ALLOW_UNPINNED=1 to load it")
    if revision != pinned:
        raise RuntimeError(f"revision {revision} is not the pin {pinned} of {model_id}; "
                           "set MB_HF_ALLOW_UNPINNED=1 to override")
    return pinned


def verify_snapshot(pin: dict[str, Any], directory: Path, hash_contents: bool) -> None:
    """Every file of a pin is present with its recorded size. With hash_contents (MB_HF_VERIFY=1) each file's sha256
    is checked as well. Raises RuntimeError on the first mismatch."""
    for entry in pin["files"]:
        path = directory / entry["path"]
        if not path.is_file():
            raise RuntimeError(f"pinned model file missing: {path}")
        size = path.stat().st_size
        if size != entry["bytes"]:
            raise RuntimeError(f"{entry['path']} has {size} bytes, the pin says {entry['bytes']}")
        if hash_contents:
            digest = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 24), b""):
                    digest.update(chunk)
            if digest.hexdigest() != entry["sha256"]:
                raise RuntimeError(f"sha256 of {entry['path']} differs from the pin")


def _flag(name: str, value: str) -> bool:
    if value not in ("0", "1"):
        raise ValueError(f"{name} must be 0 or 1, got {value!r}")
    return value == "1"


def parse_calibrate(value: str) -> bool:
    """MB_HF_CALIBRATE: "1" (contextual calibration on) or "0" (raw option scores)."""
    return _flag("MB_HF_CALIBRATE", value)


def parse_prefix_cache(value: str) -> bool:
    """MB_HF_PREFIX_CACHE: "1" (shared-prefix KV cache on) or "0" (every prompt scored whole)."""
    return _flag("MB_HF_PREFIX_CACHE", value)


def _shot(question: str, label: str, event: str = "Example event") -> dict[str, str]:
    return {"question": question, "label": label, "event_title": event}


def _q(ctx: dict[str, Any]) -> str:
    return f"Question: {ctx['question']}\nLabel: {ctx['label']}\nEvent: {ctx.get('event_title')}"


def _listing(members: list[dict[str, Any]]) -> str:
    return "\n".join(f"{i + 1}. {m['question']} [label: {m['label']}]" for i, m in enumerate(members))


def _user_status(ctx: dict[str, Any]) -> str:
    return (_q(ctx) + "\nIs the question fully specified (a date or time, a threshold or target, and a way to resolve "
            "it)? Options: " + ", ".join(STATUS) + ".")


def _user_negated(ctx: dict[str, Any]) -> str:
    return (_q(ctx) + "\nIs the label the negative side of the question (the side that says the event did NOT happen)? "
            "Options: no (the label is the positive side, such as Yes, Over or Up), yes (the label is the negative side, "
            "such as No, Under or Down).")


def _user_missing(ctx: dict[str, Any]) -> str:
    return (_q(ctx) + "\nWhat information is missing? Options: no_date (no date or time), no_source (no resolution "
            "source), no_threshold (no threshold or target), ambiguous (unclear wording).")


def relation_order(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, ...]:
    """The menu order of the relations for one pair: a shuffle keyed on the pair's ids (a fixed crc32 seed), so no
    letter is the same relation for every pair and the letter position bias averages out. The few-shot turns use their
    own fixed menus (_shot_menu)."""
    order = list(RELATION_CHOICES)
    random.Random(zlib.crc32(f"{a['id']}\x1f{b['id']}".encode("utf-8"))).shuffle(order)
    return tuple(order)


def _shot_menu(index: int, name: str) -> tuple[str, ...]:
    """The fixed menu of relation shot `index`: `name` at letter index % 4 (so the answers are balanced over the shots),
    the other relations in a crc32-keyed order."""
    others = [n for n in RELATION_CHOICES if n != name]
    random.Random(zlib.crc32(f"shot{index}".encode("utf-8"))).shuffle(others)
    others.insert(index % 4, name)
    return tuple(others)


def _user_relation(pair: tuple[Any, ...]) -> str:
    a, b = pair[0], pair[1]
    order = pair[2] if len(pair) > 2 else RELATION_CHOICES
    menu = "\n".join(f"{letter}. {name} ({RELATION_MEANING[name]})"
                     for letter, name in zip(RELATION_LETTERS, order))
    return (f"Proposition A: {a['question']} [label: {a['label']}]\n"
            f"Proposition B: {b['question']} [label: {b['label']}]\n"
            f"Which relation holds between A and B?\n{menu}\nAnswer with the letter.")


def _user_kind(members: list[dict[str, Any]]) -> str:
    return (f"Outcomes of one candidate set:\n{_listing(members)}\nWhich constraint holds across these outcomes? "
            "Options: none (no constraint), exactly_one (exactly one holds), at_most_one (at most one holds), "
            "at_least_k (at least k hold), at_most_k (at most k hold), ordered_chain (each outcome implies the next).")


def _user_k(members: list[dict[str, Any]]) -> str:
    return (f"Outcomes of one candidate set:\n{_listing(members)}\nHow many of these outcomes is the constraint about "
            "(k)? Options: " + ", ".join(k_options(len(members))) + ".")


def _user_order(members: list[dict[str, Any]]) -> str:
    letters = ", ".join(ORDER_LETTERS[:len(members)])
    listing = "\n".join(f"{ORDER_LETTERS[i]}. {m['question']} [label: {m['label']}]" for i, m in enumerate(members))
    return (f"Outcomes of one chain:\n{listing}\nWhich outcome is the strongest, the one that implies all the others? "
            f"Options: {letters}.")


USER: dict[str, Callable[[Any], str]] = {
    "status": _user_status,
    "negated": _user_negated,
    "missing": _user_missing,
    "relation": _user_relation,
    "kind": _user_kind,
    "k": _user_k,
    "order": _user_order,
}

_SPRING = [_shot("Will the Lions win the Spring Cup?", "Yes", "Spring Cup winner"),
           _shot("Will the Hawks win the Spring Cup?", "Yes", "Spring Cup winner"),
           _shot("Will the Owls win the Spring Cup?", "Yes", "Spring Cup winner")]
_STORMS = [_shot("Will Storm Ada make landfall?", "Yes", "Storm landfall"),
           _shot("Will Storm Bo make landfall?", "Yes", "Storm landfall"),
           _shot("Will Storm Cy make landfall?", "Yes", "Storm landfall")]
_EMBER = [_shot("Will Ember Rovers finish in the top 2?", "Yes", "Ember league"),
          _shot("Will Ember Hawks finish in the top 2?", "Yes", "Ember league"),
          _shot("Will Ember Owls finish in the top 2?", "Yes", "Ember league")]
_CHAIN = [_shot("Will the high temperature exceed 35 C on 3 March?", "Yes", "Temperature"),
          _shot("Will the high temperature exceed 30 C on 3 March?", "Yes", "Temperature"),
          _shot("Will the high temperature exceed 25 C on 3 March?", "Yes", "Temperature")]
_RISE = list(reversed(_CHAIN))
_MIXED = [_CHAIN[1], _CHAIN[0], _CHAIN[2]]
_MILLS = [_shot("Will Ridge Mill open in May?", "Yes", "Mill openings"),
          _shot("Will Harbor Mill open in May?", "Yes", "Mill openings"),
          _shot("Will Lake Mill open in May?", "Yes", "Mill openings")]
_UNRELATED = [_shot("Will it snow in Oslo on 2 January?", "Yes", "Oslo weather"),
              _shot("Will Widget Pro cost more than 40 dollars on 1 June?", "Yes", "Widget Pro price"),
              _shot("Will the river flood by 5 May?", "Yes", "River flood")]
# Relation exemplars: (proposition A, proposition B), relation of A to B about the labelled outcomes (a No or Under label
# negates the question). Three shots per named relation and six NONE (NONE is most of every candidate set). The order
# has no run of one answer longer than two, and the last shot is not NONE. Invented topics only.
_RELATION_SHOTS: list[tuple[tuple[dict[str, str], dict[str, str]], str]] = [
    ((_shot("Will the Ferris Bridge be closed to traffic on 9 May?", "Yes", "Ferris Bridge"),
      _shot("Will Lark Corp report revenue above 400 million for Q3?", "Yes", "Lark Corp Q3")), "NONE"),
    ((_shot("Will Lark Corp report revenue above 500 million for Q3?", "Yes", "Lark Corp Q3"),
      _shot("Will Lark Corp report revenue above 400 million for Q3?", "Yes", "Lark Corp Q3")), "IMPLIES"),
    ((_shot("Will the Oak Town river flood by 2 July?", "Yes", "Oak Town river"),
      _shot("Will Oak Town get between 10 and 19 mm of rain on 2 July?", "Yes", "Oak Town rain")), "NONE"),
    ((_shot("Will Team Nova score at least 1 goal in the semifinal?", "Yes", "Semifinal"),
      _shot("Will Team Nova score at least 3 goals in the semifinal?", "Yes", "Semifinal")), "IMPLIED_BY"),
    ((_shot("Will Team Nova score at least 3 goals in the semifinal?", "Yes", "Semifinal"),
      _shot("Will the Ferris Bridge be closed to traffic on 9 May?", "Yes", "Ferris Bridge")), "NONE"),
    ((_shot("Will Oak Town get between 10 and 19 mm of rain on 2 July?", "Yes", "Oak Town rain"),
      _shot("Will Oak Town get between 20 and 29 mm of rain on 2 July?", "Yes", "Oak Town rain")), "EXCLUDES"),
    ((_shot("Will the Pine River rise above 6 m by 10 June?", "Yes", "Pine River level"),
      _shot("Will the Pine River rise above 4 m by 10 June?", "Yes", "Pine River level")), "IMPLIES"),
    ((_shot("Will Quill Index close above 4,120 on 14 May?", "No", "Quill Index close"),
      _shot("Will Vale Index close above 2,310 on 14 May?", "Yes", "Vale Index close")), "NONE"),
    ((_shot("Will the Pine River rise above 4 m by 10 June?", "Yes", "Pine River level"),
      _shot("Will the Pine River rise above 6 m by 10 June?", "Yes", "Pine River level")), "IMPLIED_BY"),
    ((_shot("Will Team Rio win the semifinal?", "Yes", "Semifinal"),
      _shot("Will Team Nova win the semifinal?", "Yes", "Semifinal")), "EXCLUDES"),
    ((_shot("Will Harbor Mill open in May?", "Yes", "Mill openings"),
      _shot("Will Ridge Mill open in June?", "Yes", "Mill openings")), "NONE"),
    ((_shot("Will at least 300 people attend the Lark Corp event?", "No", "Lark Corp event"),
      _shot("Will at least 500 people attend the Lark Corp event?", "No", "Lark Corp event")), "IMPLIES"),
    ((_shot("Will Lark Corp report revenue above 500 million for Q3?", "Yes", "Lark Corp Q3"),
      _shot("Will Lark Corp report revenue above 500 million for Q4?", "Yes", "Lark Corp Q4")), "NONE"),
    ((_shot("Will Lark Corp report revenue above 500 million for Q3?", "No", "Lark Corp Q3"),
      _shot("Will Lark Corp report revenue above 400 million for Q3?", "No", "Lark Corp Q3")), "IMPLIED_BY"),
    ((_shot("O/U 2.5 total goals in the Harbor vs Lake match", "Over", "Harbor vs Lake goals"),
      _shot("O/U 1.5 total goals in the Harbor vs Lake match", "Under", "Harbor vs Lake goals")), "EXCLUDES"),
]


def _relation_few_shot() -> list[tuple[Any, str]]:
    """Each relation shot with its own fixed menu; the answer is the letter its relation has on that menu."""
    shots = []
    for index, ((a, b), name) in enumerate(_RELATION_SHOTS):
        order = _shot_menu(index, name)
        shots.append(((a, b, order), RELATION_LETTERS[order.index(name)]))
    return shots


# Few-shot examples per stage, in a fixed order: (payload, answer). Each option set is covered at least once.
FEW_SHOT: dict[str, list[tuple[Any, str]]] = {
    "status": [
        (_shot("Will the high temperature in Springfield exceed 30 C at 2PM on 3 March?", "Yes",
               "Springfield temperature"), "bound"),
        (_shot("Will the weather be nice soon?", "Yes", "Weather"), "unresolved"),
    ],
    "negated": [
        (_shot("Will the price of Widget Pro be below 40 dollars on 1 June at 9AM UTC?", "Yes",
               "Widget Pro price"), "no"),
        (_shot("Will the price of Widget Pro be below 40 dollars on 1 June at 9AM UTC?", "No",
               "Widget Pro price"), "yes"),
        (_shot("O/U 30.5 total goals in the Atlas vs Borealis match", "Over", "Atlas vs Borealis goals"), "no"),
        (_shot("O/U 30.5 total goals in the Atlas vs Borealis match", "Under", "Atlas vs Borealis goals"), "yes"),
    ],
    "missing": [
        (_shot("Will the rain fall on the day in question?", "Yes", "Rain"), "no_date"),
        (_shot("Will a news outlet report that the river flooded?", "Yes", "River flood"), "no_source"),
        (_shot("Will the storm be strong when it arrives on 4 July?", "Yes", "Storm strength"), "no_threshold"),
        (_shot("Will the big one win on 9 July?", "Yes", "Big one"), "ambiguous"),
    ],
    "relation": _relation_few_shot(),
    "kind": [
        (_UNRELATED, "none"),
        (_SPRING, "exactly_one"),
        (_MILLS[:2], "at_most_one"),
        (_STORMS, "at_least_k"),
        (_EMBER, "at_most_k"),
        (_CHAIN, "ordered_chain"),
    ],
    "k": [
        (_STORMS, "2"),
        (_EMBER, "2"),
        (_MILLS, "1"),
    ],
    "order": [  # the strongest outcome of each listing: the 35 C threshold, at letter A, B or C
        (_CHAIN, "A"),
        (_MIXED, "B"),
        (_RISE, "C"),
    ],
}


def _content(text: str) -> dict[str, str]:
    return {"question": text, "label": text, "event_title": text}


def _content_members(n: int, shift: int) -> list[dict[str, str]]:
    return [_content(CONTENT_FREE[(shift + j) % len(CONTENT_FREE)]) for j in range(n)]


def _relation_calibration() -> list[tuple[dict[str, str], dict[str, str], tuple[str, ...]]]:
    """Content-free pairs for the relation letter prior: each content-free pair under every rotation of the relation
    names, so each relation is on each letter once and the prior keeps only the letter's own bias."""
    rotations = [RELATION_CHOICES[r:] + RELATION_CHOICES[:r] for r in range(len(RELATION_CHOICES))]
    return [(_content(CONTENT_FREE[s]), _content(CONTENT_FREE[(s + 1) % len(CONTENT_FREE)]), order)
            for s in range(len(CONTENT_FREE)) for order in rotations]


# Content-free inputs for each stage's calibration prior, one per content-free shift.
CALIBRATION: dict[str, Callable[[int], list[Any]]] = {
    "status": lambda n: [_content(text) for text in CONTENT_FREE],
    "negated": lambda n: [_content(text) for text in CONTENT_FREE],
    "missing": lambda n: [_content(text) for text in CONTENT_FREE],
    "relation": lambda n: _relation_calibration(),
    "kind": lambda n: [_content_members(3, i) for i in range(3)],
    "k": lambda n: [_content_members(n, i) for i in range(3)],
    "order": lambda n: [_content_members(n, i) for i in range(3)],
}


def _shot_turns(stage: str) -> list[dict[str, str]]:
    """The constant part of a stage's chat: the system turn and the few-shot turns."""
    turns = [{"role": "system", "content": SYSTEM}]
    for shot, answer in FEW_SHOT[stage]:
        turns.append({"role": "user", "content": USER[stage](shot)})
        turns.append({"role": "assistant", "content": answer})
    return turns


def _messages(stage: str, payload: Any) -> list[dict[str, str]]:
    """The chat for one decision: the stage's constant turns, then the decision itself."""
    return _shot_turns(stage) + [{"role": "user", "content": USER[stage](payload)}]


DECISION_MARK = "MB_DECISION_CONTENT"  # stands for a decision's user content when the template is read


def _decision_parts(tokenizer: Any, stage: str, prefix_text: str) -> tuple[str, str] | None:
    """The chat text around a decision's user content: the chat is the stage's prefix text, then pre, the content, then
    post. Read once from the template with a marker as the content; None when the marker is not where it should be."""
    full = tokenizer.apply_chat_template(_shot_turns(stage) + [{"role": "user", "content": DECISION_MARK}],
                                         tokenize=False, add_generation_prompt=True)
    if not full.startswith(prefix_text) or full.count(DECISION_MARK) != 1:
        return None
    pre, post = full[len(prefix_text):].split(DECISION_MARK)
    return pre, post


def kv_bytes(device: str) -> int:
    """The prefix-cache memory of one forward pass: half the free memory of a CUDA device at startup, at most KV_BYTES.
    On other devices, KV_BYTES."""
    if not device.startswith("cuda"):
        return KV_BYTES
    free, _ = torch.cuda.mem_get_info(device)
    return min(KV_BYTES, free // 2)


class HFChild:
    def __init__(self, model_id: str, revision: str | None, tokenizer: Any, model: Any, device: str,
                 calibrate: bool = True, prefix_cache: bool = True, relation_floor: float | None = None,
                 relation_calibration: bool | None = None):
        self.model_id = model_id
        self.revision = revision
        self.tokenizer = tokenizer
        self.model = model.eval()
        self.device = device
        self.calibrate = calibrate
        self.prefix_cache = prefix_cache
        self.relation_floor, self.relation_floor_source = relation_floor_of(model_id, relation_floor)
        resolved, source = relation_calibration_of(model_id, relation_calibration)
        # on only with the global calibration on (MB_HF_CALIBRATE); a disabled setting says so in its source
        self.relation_calibration = resolved and calibrate
        self.relation_calibration_source = f"{source}, disabled by MB_HF_CALIBRATE=0" if resolved and not calibrate else source
        self.dtype = next(model.parameters()).dtype
        self._pad = tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
        self._max_seqs = MAX_SEQS
        self._token_budget = TOKEN_BUDGET
        cfg = model.config
        heads = getattr(cfg, "num_key_value_heads", None) or cfg.num_attention_heads
        head_dim = getattr(cfg, "head_dim", None) or cfg.hidden_size // cfg.num_attention_heads
        bytes_per_token = cfg.num_hidden_layers * 2 * heads * head_dim * torch.empty((), dtype=self.dtype).element_size()
        self._kv_bytes = kv_bytes(device)
        self._kv_budget = max(1, self._kv_bytes // bytes_per_token)  # cached tokens (prefix and suffix) of a pass
        self._full = (self._max_seqs, self._token_budget, self._kv_budget)  # the limits that regrowth returns to
        self._clean = 0  # clean forward passes since the last out-of-memory halving
        self._oom_halvings = 0
        self._meter = {"tokens_in": 0, "tokens_out": 0, "gpu_seconds": 0.0}
        self._priors: dict[tuple[str, tuple[str, ...]], list[float]] = {}
        self._prefixes: dict[str, tuple[list[int], tuple[tuple[torch.Tensor, torch.Tensor], ...]]] = {}
        self._prefix_text: dict[str, str] = {}
        self._decision_parts: dict[str, tuple[str, str] | None] = {}
        self._split: dict[str, bool] = dict.fromkeys(FEW_SHOT, False)  # a stage is tokenized as a split chat when it passes
        self._option_cache: dict[str, list[int]] = {}
        torch.manual_seed(0)
        if prefix_cache:
            for stage in FEW_SHOT:
                self._stage_prefix(stage)
                self._split[stage] = self._split_matches(stage)
        if calibrate:
            self._warm_priors()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "HFChild":
        env = os.environ if env is None else env
        model_id = env.get("MB_HF_MODEL", DEFAULT_MODEL)
        revision = resolve_revision(model_id, env.get("MB_HF_REVISION") or None, env.get("MB_HF_ALLOW_UNPINNED") == "1")
        device = env.get("MB_HF_DEVICE", "cuda:0")
        calibrate = parse_calibrate(env.get("MB_HF_CALIBRATE", "1"))
        prefix_cache = parse_prefix_cache(env.get("MB_HF_PREFIX_CACHE", "1"))
        floor = env.get("MB_HF_RELATION_FLOOR") or None
        floor = None if floor is None else float(floor)
        relation_floor_of(model_id, floor)  # refuses a loosening before any weights load
        calibration = env.get("MB_HF_RELATION_CALIBRATION") or None
        calibration = None if calibration is None else _flag("MB_HF_RELATION_CALIBRATION", calibration)
        relation_calibration_of(model_id, calibration)  # refuses a value other than 0 or 1 before any weights load
        pin = json.loads(PINS.read_text(encoding="utf-8"))["models"].get(model_id)
        if pin is not None and revision == pin["revision"]:
            snapshot = Path(huggingface_hub.snapshot_download(model_id, revision=revision, local_files_only=True))
            verify_snapshot(pin, snapshot, hash_contents=env.get("MB_HF_VERIFY") == "1")
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
        model = AutoModelForCausalLM.from_pretrained(model_id, revision=revision, torch_dtype=dtype).to(device)
        return cls(model_id, revision, tokenizer, model, device, calibrate=calibrate, prefix_cache=prefix_cache,
                   relation_floor=floor, relation_calibration=calibration)

    def describe(self) -> dict[str, Any]:
        return {
            "name": "hf-constrained-choice",
            "version": "9",
            "relation_floor": {"value": self.relation_floor, "source": self.relation_floor_source},
            "relation_calibration": {"value": self.relation_calibration, "source": self.relation_calibration_source},
            "limits": {  # the batch budgets now in effect (after any halving) and at startup, and the halvings so far
                "kv_bytes": self._kv_bytes, "max_seqs": self._max_seqs, "token_budget": self._token_budget,
                "kv_budget_tokens": self._kv_budget, "startup": {
                    "max_seqs": self._full[0], "token_budget": self._full[1], "kv_budget_tokens": self._full[2]},
                "oom_halvings": self._oom_halvings,
            },
            "model": {
                "id": self.model_id,
                "revision": self.revision,
                "kind": "local",
                "dtype": str(self.dtype).removeprefix("torch."),
                "device": self.device,
                "torch": torch.__version__,
                "transformers": transformers.__version__,
                "calibrate": self.calibrate,
                "prefix_cache": self.prefix_cache,
            },
        }

    # Scoring. A stage scores one option set per item in one or more batched forward passes.

    def _prompt_ids(self, messages: list[dict[str, str]]) -> list[int]:
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        ids = self.tokenizer(text, add_special_tokens=False)["input_ids"]
        if len(ids) > MAX_PROMPT_TOKENS:
            raise ChildError(f"prompt of {len(ids)} tokens exceeds {MAX_PROMPT_TOKENS}")
        return ids

    def _option_ids(self, option: str) -> list[int]:
        if option not in self._option_cache:
            ids = self.tokenizer(option, add_special_tokens=False)["input_ids"]
            if not ids:
                raise RuntimeError(f"option {option!r} has no tokens")
            self._option_cache[option] = ids
        return self._option_cache[option]

    def _prompts(self, stage: str, payloads: list[Any], split: bool | None = None) -> list[list[int] | ChildError]:
        """Token ids of the chat of each payload, in order; a ChildError in place of the ids of a chat over
        MAX_PROMPT_TOKENS. A split stage tokenizes only what follows its few-shot prefix text: the prefix ids are cached,
        and the remainders go through the tokenizer in one batched call. A chat that does not start with the prefix text
        makes the whole batch whole-chat tokenized, so the ids are always those of _prompt_ids."""
        if not payloads:
            return []
        split = self._split[stage] if split is None else split
        if split:
            head_ids = self._stage_prefix(stage)[0]
            pre, post = self._decision_parts[stage]
            tails = self.tokenizer([pre + USER[stage](p) + post for p in payloads], add_special_tokens=False)["input_ids"]
            ids = [head_ids + tail for tail in tails]
        else:
            rendered = [self.tokenizer.apply_chat_template(_messages(stage, p), tokenize=False, add_generation_prompt=True)
                        for p in payloads]
            ids = self.tokenizer(rendered, add_special_tokens=False)["input_ids"]
        return [v if len(v) <= MAX_PROMPT_TOKENS else ChildError(f"prompt of {len(v)} tokens exceeds {MAX_PROMPT_TOKENS}")
                for v in ids]

    def _split_matches(self, stage: str) -> bool:
        """Setup check of a stage: the split ids equal the full-chat ids for its calibration payloads (the relation stage
        has none, so one content-free pair). A stage that fails is tokenized whole."""
        payloads = (CALIBRATION[stage](2) if stage in CALIBRATION
                    else [(_content("N/A"), _content("the thing"), RELATION_CHOICES)])
        ok = self._decision_parts.get(stage) is not None and (
            self._prompts(stage, payloads, split=True) == [self._prompt_ids(_messages(stage, p)) for p in payloads])
        if not ok:
            print(f"hf_child: stage {stage} is tokenized as whole chats (split ids differ)", file=sys.stderr)
        return ok

    def _stage_prefix(self, stage: str) -> tuple[list[int], tuple[tuple[torch.Tensor, torch.Tensor], ...]]:
        """Token ids and key/value cache (batch 1, legacy per-layer tuples) of a stage's constant few-shot turns."""
        if stage not in self._prefixes:
            text = self.tokenizer.apply_chat_template(_shot_turns(stage), tokenize=False, add_generation_prompt=False)
            ids = self.tokenizer(text, add_special_tokens=False)["input_ids"]
            with torch.inference_mode():
                out = self.model.base_model(input_ids=torch.tensor([ids], device=self.device), use_cache=True)
            self._prefixes[stage] = (ids, out.past_key_values.to_legacy_cache())
            self._prefix_text[stage] = text
            self._decision_parts[stage] = _decision_parts(self.tokenizer, stage, text)
        return self._prefixes[stage]

    def _prior(self, stage: str, options: tuple[str, ...], size: int = 0) -> list[float]:
        """Mean raw option score over the content-free inputs of a stage (computed once per (stage, option set, member
        count) and kept in memory). The score is a log-probability, or for relation the raw logit it is decided on. `size`
        is the real member count of a k decision (0 otherwise)."""
        key = (stage, options, size)
        if key not in self._priors:
            payloads = CALIBRATION[stage](size)
            pairs, owner = [], []
            for payload in payloads:
                prompt = self._prompt_ids(_messages(stage, payload))
                for j, option in enumerate(options):
                    pairs.append((prompt, self._option_ids(option)))
                    owner.append(j)
            sums = [0.0] * len(options)
            for j, score in zip(owner, self._score(pairs, stage, raw=stage == "relation")):
                sums[j] += score
            prior = [s / len(payloads) for s in sums]
            if not all(math.isfinite(p) for p in prior):  # not cached: the next call computes it again
                raise RuntimeError(f"non-finite calibration prior for stage {stage}")
            self._priors[key] = prior
        return self._priors[key]

    def _warm_priors(self) -> None:
        if self.relation_calibration:
            self._prior("relation", RELATION_LETTERS)
        for stage, options in (("status", STATUS), ("negated", NEGATED_OPTIONS), ("missing", MISSING),
                               ("kind", GROUP_CHOICES)):
            self._prior(stage, options)
        for n in range(2, MAX_K + 1):
            self._prior("k", k_options(n), n)
        for n in range(2, len(ORDER_LETTERS) + 1):  # an order menu has one letter per member still to place
            self._prior("order", tuple(ORDER_LETTERS[:n]), n)

    def _stage(self, results: list[Any], idx: list[int], stage: str, payload_of: Callable[[int], Any],
               options_of: Callable[[int], tuple[str, ...]],
               size_of: Callable[[int], int] | None = None) -> dict[int, int]:
        """Argmax option index (after calibration, when on) for each live item of `idx`. Items whose prompt is too
        long get a ChildError in `results` and are left out of the stage."""
        adjusted = self._scores(results, idx, stage, payload_of, options_of, size_of)
        choice: dict[int, int] = {}
        for i in adjusted:
            j = max(range(len(adjusted[i])), key=lambda k: adjusted[i][k])  # first maximum: deterministic
            choice[i] = j
            self._meter["tokens_out"] += len(self._option_ids(options_of(i)[j]))
        return choice

    def _scores(self, results: list[Any], idx: list[int], stage: str, payload_of: Callable[[int], Any],
                options_of: Callable[[int], tuple[str, ...]],
                size_of: Callable[[int], int] | None = None) -> dict[int, list[float]]:
        """Option scores (after calibration, when on) of each live item of `idx`, keyed by item. Items whose prompt is
        too long get a ChildError in `results` and are left out."""
        live = [i for i in idx if results[i] is None]
        prompts: dict[int, list[int]] = {}
        for i, got in zip(live, self._prompts(stage, [payload_of(i) for i in live])):
            if isinstance(got, ChildError):
                results[i] = got
            else:
                prompts[i] = got
        options = {i: tuple(options_of(i)) for i in prompts}
        pairs, owner = [], []
        for i, prompt in prompts.items():
            for j, option in enumerate(options[i]):
                pairs.append((prompt, self._option_ids(option)))
                owner.append((i, j))
            self._meter["tokens_in"] += len(prompt)
        adjusted = {i: [0.0] * len(options[i]) for i in prompts}
        for (i, j), score in zip(owner, self._score(pairs, stage, raw=stage == "relation")):
            adjusted[i][j] = score
        if self.calibrate and (stage != "relation" or self.relation_calibration):
            for i in prompts:
                size = size_of(i) if size_of is not None else 0
                adjusted[i] = [s - p for s, p in zip(adjusted[i], self._prior(stage, options[i], size))]
        for i in [i for i in adjusted if not all(math.isfinite(s) for s in adjusted[i])]:
            results[i] = NonFiniteScore("non-finite option score")  # an item error: no argmax over NaN or infinity
            del adjusted[i]
        return adjusted

    def _score(self, pairs: list[tuple[list[int], list[int]]], stage: str | None = None,
               raw: bool = False) -> list[float]:
        """Mean log-probability of each option (prompt, option) pair, in input order. The pairs of one prompt object form a
        feed: its head (the prompt from the stage's cached prefix on, when the prompt starts with it) runs once, and its
        options are read from the head and from tails of it (_forward_tails). Chunks are bounded by the batch limits, and an
        out-of-memory halves them."""
        prefix = self._stage_prefix(stage) if stage is not None and self.prefix_cache else None
        plen = len(prefix[0]) if prefix is not None else 0
        feeds: list[tuple[list[int], list[int]]] = []  # (prompt, pair indices of its options)
        where: dict[int, int] = {}
        for k, (prompt, _) in enumerate(pairs):
            if id(prompt) not in where:
                where[id(prompt)] = len(feeds)
                feeds.append((prompt, []))
            feeds[where[id(prompt)]][1].append(k)
        cached = [prefix is not None and len(p) > plen and p[:plen] == prefix[0] for p, _ in feeds]
        scores = [0.0] * len(pairs)
        for use_prefix in (True, False):  # a chunk is either all cached or all whole
            base = plen if use_prefix else 0
            todo = sorted((f for f in range(len(feeds)) if cached[f] == use_prefix), key=lambda f: len(feeds[f][0]))
            start = 0
            while start < len(todo):
                end, cost = start, 0  # cost: cache copies of the chunk, in prompt-length units
                while end < len(todo) and end - start < self._max_seqs:
                    f = todo[end]
                    # the head's own cache, plus one copy per multi-token option (its tail reads a copy of the head cache)
                    weight = 1 + sum(len(pairs[k][1]) > 1 for k in feeds[f][1])
                    if end > start and (end - start + 1) * (len(feeds[f][0]) - base) > self._token_budget:
                        break
                    if end > start and (cost + weight) * len(feeds[f][0]) > self._kv_budget:
                        break
                    cost += weight
                    end += 1
                chunk = todo[start:end]
                batch = [(feeds[f][0][base:], [pairs[k][1] for k in feeds[f][1]]) for f in chunk]
                owner = [k for f in chunk for k in feeds[f][1]]
                try:
                    values = self._forward_tails(batch, prefix if use_prefix else None, raw)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    if len(chunk) == 1:
                        raise
                    self._max_seqs = max(1, len(chunk) // 2)
                    self._token_budget = max(1, self._token_budget // 2)
                    self._kv_budget = max(1, self._kv_budget // 2)
                    self._clean = 0
                    self._oom_halvings += 1
                    print(f"hf_child: CUDA out of memory on {len(chunk)} rows; batch limits halved to {self._max_seqs} rows",
                          file=sys.stderr)
                    continue
                for k, value in zip(owner, values):
                    scores[k] = value
                self._regrow()
                start = end
        return scores

    def _regrow(self) -> None:
        """After REGROW_AFTER clean forward passes, the batch limits double back, up to their starting values."""
        self._clean += 1
        if self._clean >= REGROW_AFTER:
            self._clean = 0
            self._max_seqs = min(self._full[0], 2 * self._max_seqs)
            self._token_budget = min(self._full[1], 2 * self._token_budget)
            self._kv_budget = min(self._full[2], 2 * self._kv_budget)

    def _forward_tails(self, feeds: list[tuple[list[int], list[list[int]]]], prefix: Any, raw: bool = False) -> list[float]:
        """Option scores of one chunk. A feed is (head, options): the head is the prompt from the cached prefix on, up to
        and including its last token. The heads run once, right-padded, over the prefix cache when prefix is given, and keep
        their key/value cache. Every option's first token is read at its head's last position. A multi-token option's later
        tokens are read from a tail: the option's tokens but the last, run against a copy of its head's cache (one copy per
        multi-token option, with its own positions and mask). One-token options need no tail, so a chunk of them is one
        forward pass. Returns the mean log-probability of every option, in feed and option order (raw: the projected
        logits of one-token options)."""
        plen = len(prefix[0]) if prefix is not None else 0
        n = len(feeds)
        width = max(len(head) for head, _ in feeds)
        ids = torch.full((n, width), self._pad, dtype=torch.long)
        mask = torch.zeros((n, plen + width), dtype=torch.long)
        mask[:, :plen] = 1
        for f, (head, _) in enumerate(feeds):
            ids[f, :len(head)] = torch.tensor(head, dtype=torch.long)
            mask[f, plen: plen + len(head)] = 1
        positions = torch.arange(plen, plen + width, device=self.device).unsqueeze(0).expand(n, -1)
        started = time.perf_counter()
        with torch.inference_mode():
            past = None
            if prefix is not None:  # the prefix cache, expanded to the feeds (the stored tensors are never written)
                past = DynamicCache.from_legacy_cache(
                    tuple((k.expand(n, -1, -1, -1), v.expand(n, -1, -1, -1)) for k, v in prefix[1]))
            heads = self.model.base_model(input_ids=ids.to(self.device), attention_mask=mask.to(self.device),
                                          position_ids=positions, past_key_values=past, use_cache=True)
        rows, cols, targets, owners, tails = [], [], [], [], []  # first tokens, read at each head's last position
        g = 0
        for f, (head, options) in enumerate(feeds):
            for option in options:
                rows.append(f)
                cols.append(len(head) - 1)
                targets.append(option[0])
                owners.append(g)
                if len(option) > 1:
                    tails.append((f, g, option))
                g += 1
        out = self._option_logprobs(heads.last_hidden_state, rows, cols, targets, owners, g, started, raw)
        if not tails:
            return out
        started = time.perf_counter()
        index = torch.tensor([f for f, _, _ in tails], dtype=torch.long, device=self.device)
        tlen = max(len(option) - 1 for _, _, option in tails)
        m = len(tails)
        tids = torch.full((m, tlen), self._pad, dtype=torch.long)
        tmask = torch.zeros((m, plen + width + tlen), dtype=torch.long)
        tpos = torch.zeros((m, tlen), dtype=torch.long)
        trows, tcols, ttargets, towners = [], [], [], []
        for q, (f, _, option) in enumerate(tails):
            head_len = len(feeds[f][0])
            inputs = option[:-1]
            tids[q, :len(inputs)] = torch.tensor(inputs, dtype=torch.long)
            tmask[q, :plen + head_len] = 1  # the prefix and the real head tokens; head padding stays masked
            tmask[q, plen + width: plen + width + len(inputs)] = 1
            tpos[q] = torch.arange(tlen) + plen + head_len  # the first option token follows the head's last token
            trows += [q] * len(inputs)
            tcols += list(range(len(inputs)))  # the logits at tail position t predict option token t + 1
            ttargets += option[1:]
            towners += [q] * len(inputs)
        with torch.inference_mode():
            cache = DynamicCache.from_legacy_cache(tuple(
                (k.index_select(0, index), v.index_select(0, index)) for k, v in heads.past_key_values.to_legacy_cache()))
            hidden = self.model.base_model(
                input_ids=tids.to(self.device), attention_mask=tmask.to(self.device), position_ids=tpos.to(self.device),
                past_key_values=cache, use_cache=True,
            ).last_hidden_state
        rest = self._option_logprobs(hidden, trows, tcols, ttargets, towners, m, started, raw)
        for q, (_, g, option) in enumerate(tails):  # the mean over all tokens: the first token, then the tail's tokens
            out[g] = (out[g] + (len(option) - 1) * rest[q]) / len(option)
        return out

    def _option_logprobs(self, hidden: torch.Tensor, rows: list[int], cols: list[int], targets: list[int],
                         owners: list[int], n: int, started: float, raw: bool = False) -> list[float]:
        """Mean option-token log-probability per option (owners[k] is the option of token k), from the hidden states at
        the predicting positions. Options of one row share a position, so each position is projected once. With raw, the
        projected logits themselves (bf16 values, exact in float32) instead of log-probabilities: a difference of two
        of them has no normaliser rounding, so a margin that is exactly one bf16 step apart is exact."""
        dev = self.device
        with torch.inference_mode():
            at: dict[tuple[int, int], int] = {}
            for r, c in zip(rows, cols):
                at.setdefault((r, c), len(at))
            slots = [at[(r, c)] for r, c in zip(rows, cols)]
            picked = hidden[torch.tensor([r for r, _ in at], device=dev), torch.tensor([c for _, c in at], device=dev)]
            logits = self.model.lm_head(picked).float()
            if not raw:
                logits = torch.log_softmax(logits, dim=-1)
            token_logp = logits[torch.tensor(slots, device=dev), torch.tensor(targets, device=dev)].tolist()
        if dev.startswith("cuda"):
            torch.cuda.synchronize(dev)
        self._meter["gpu_seconds"] += time.perf_counter() - started
        sums, counts = [0.0] * n, [0] * n
        for g, value in zip(owners, token_logp):
            sums[g] += value
            counts[g] += 1
        return [s / c for s, c in zip(sums, counts)]

    # Decisions

    def _bindings(self, ctxs: list[dict[str, Any]]) -> list[Any]:
        results: list[Any] = [None] * len(ctxs)
        idx = list(range(len(ctxs)))
        status = self._stage(results, idx, "status", lambda i: ctxs[i], lambda i: STATUS)
        bound = [i for i in status if status[i] == 0]
        unresolved = [i for i in status if status[i] == 1]
        # A binary label names its own side (Yes, Over, Up: positive; No, Under, Down: negative), so the model decides
        # negation only for the other labels.
        sides = {i: binary_label(ctxs[i]["label"]) for i in bound}
        negated = {i: sides[i][1] for i in bound if sides[i][0]}
        negation = self._stage(results, [i for i in bound if not sides[i][0]], "negated", lambda i: ctxs[i],
                               lambda i: NEGATED_OPTIONS)
        negated.update({i: j == 1 for i, j in negation.items()})
        missing = self._stage(results, unresolved, "missing", lambda i: ctxs[i], lambda i: MISSING)
        for i, neg in negated.items():
            ctx = ctxs[i]
            results[i] = {"status": "bound", "class": class_key(ctx["question"], ctx["label"]),
                          "negated": neg, "missing": []}
        for i, j in missing.items():
            results[i] = {"status": "unresolved", "class": None, "negated": False, "missing": [MISSING[j]]}
        return [r if r is not None else ChildError("no decision") for r in results]

    def _relations(self, pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[Any]:
        results: list[Any] = [None] * len(pairs)
        orders = [relation_order(a, b) for a, b in pairs]
        scores = self._scores(results, list(range(len(pairs))), "relation",
                              lambda i: (pairs[i][0], pairs[i][1], orders[i]), lambda i: RELATION_LETTERS)
        for i, vals in scores.items():
            by_name = dict(zip(orders[i], vals))
            best = max((n for n in orders[i] if n != "NONE"), key=lambda n: by_name[n])  # first maximum: deterministic
            conf = by_name[best] - by_name["NONE"]  # the margin of the best named relation over NONE, in nats (calibrated when relation calibration is on)
            name = best if conf >= self.relation_floor else "NONE"
            results[i] = {"relation": name, "conf": conf}
            top = max(orders[i], key=lambda n: by_name[n])
            self._meter["tokens_out"] += len(self._option_ids(RELATION_LETTERS[orders[i].index(top)]))
        return [r if r is not None else ChildError("no decision") for r in results]

    def _order(self, results: list[Any], idx: list[int],
               members_of: Callable[[int], list[dict[str, Any]]]) -> dict[int, list[dict[str, Any]]]:
        """The chain members of each item of `idx` (see common.chain_members), strongest first. Each round the order stage picks
        the strongest of the members not yet placed (a letter menu over them); the last member left needs no question.
        An item with fewer than two chain members, or more than 26 (one letter each), gets {"groups": []}."""
        left: dict[int, list[dict[str, Any]]] = {}
        for i in idx:
            left[i] = chain_members(members_of(i))
            if not 2 <= len(left[i]) <= len(ORDER_LETTERS):
                results[i] = {"groups": []}
        placed: dict[int, list[dict[str, Any]]] = {i: [] for i in left if results[i] is None}
        while True:
            active = [i for i in placed if results[i] is None and len(left[i]) >= 2]
            if not active:
                break
            choice = self._stage(results, active, "order", lambda i: left[i],
                                 lambda i: tuple(ORDER_LETTERS[:len(left[i])]), size_of=lambda i: len(left[i]))
            for i, j in choice.items():
                placed[i].append(left[i].pop(j))
        return {i: placed[i] + left[i] for i in placed if results[i] is None}

    def _groups(self, ctxs: list[dict[str, Any]]) -> list[Any]:
        results: list[Any] = [None] * len(ctxs)
        members_of = lambda i: ctxs[i]["members"]  # noqa: E731
        kind_scores = self._scores(results, list(range(len(ctxs))), "kind", members_of, lambda i: GROUP_CHOICES)
        kind_conf: dict[int, float] = {}  # the margin of the chosen kind over "none", in nats (0 for none)
        kind: dict[int, str] = {}
        for i, vals in kind_scores.items():
            j = max(range(len(vals)), key=lambda k: vals[k])  # first maximum: deterministic
            kind[i] = GROUP_CHOICES[j]
            kind_conf[i] = vals[j] - vals[0]
            self._meter["tokens_out"] += len(self._option_ids(GROUP_CHOICES[j]))
        counted = [i for i, name in kind.items() if name in ("at_least_k", "at_most_k")]
        ks = self._stage(results, counted, "k", members_of, lambda i: k_options(len(members_of(i))),
                         size_of=lambda i: len(members_of(i)))
        chains = self._order(results, [i for i, name in kind.items() if name == "ordered_chain"], members_of)
        for i, name in kind.items():
            if results[i] is not None:
                continue
            if name == "none":
                results[i] = {"groups": []}
                continue
            if name == "ordered_chain":
                chain = {"kind": name, "members": [m["id"] for m in chains[i]], "direction": "forward", "scope": None,
                         "conf": kind_conf[i]}
                results[i] = {"groups": [chain]}
                continue
            decision: dict[str, Any] = {"kind": name, "members": [m["id"] for m in members_of(i)], "scope": None,
                                        "conf": kind_conf[i]}
            if i in ks:
                decision["k"] = int(k_options(len(members_of(i)))[ks[i]])
            results[i] = {"groups": [decision]}
        # A candidate set whose prompt is over MAX_PROMPT_TOKENS gets no decision: {"groups": []}. A group call never
        # fails on size, so the adapter does not retry or restart for it. A non-finite score is an error, not a "none".
        out = []
        for r in results:
            oversized = isinstance(r, ChildError) and not isinstance(r, NonFiniteScore)
            out.append({"groups": []} if r is None or oversized else r)
        return out

    def decide(self, op: str, items: list[dict[str, Any]]) -> tuple[list[Any], dict[str, Any]]:
        self._meter = {"tokens_in": 0, "tokens_out": 0, "gpu_seconds": 0.0}
        if op == "decide_binding":
            results = self._bindings([item["ctx"] for item in items])
        elif op == "decide_relation":
            results = self._relations([(item["a"], item["b"]) for item in items])
        else:
            results = self._groups([item["ctx"] for item in items])
        usage = {
            "tokens_in": self._meter["tokens_in"],
            "tokens_out": self._meter["tokens_out"],
            "cost_usd": 0.0,
            "gpu_seconds": round(self._meter["gpu_seconds"], 6),
            "model": self.model_id,
            "kind": "local",
        }
        return results, usage


def main() -> None:
    serve(HFChild.from_env())


if __name__ == "__main__":
    main()

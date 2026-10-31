"""System One child: typed decisions answered by a server's System One endpoint (torch-free).

The decisions are the typed questions of hf_child (bindings, relations, groups), asked of one model through POST
{MB_S1_URL}/v1/systemone (Ollama's endpoint, docs.ollama.com/api/systemone; any server with the same wire format, see
docs/local_protocol.md). A choice question returns a probability for each option, and the
decision is the argmax. The prompts are not hf_child's: they are zero-shot (no few-shot turns), uncalibrated (no
content-free prior), and read the server's own probabilities. Relation menus are shuffled per pair (a crc32 key, as in
hf_child), so no option sits at the same place for every pair.

Requests. One question per request, for every op (MAX_QUESTIONS is 1): packing questions into one request changed relation
decisions on nimble:9b-bf16 (31 of 240 argmaxes, conf up to 6.2, against the same question asked alone) and gave no speedup.
Bindings: a request for status, one for negated (a label that is not binary, when bound), one for missing when the status is
unresolved. Relations: one request per pair. Groups: one request for kind, one for k when the kind counts, and one request per
round of an ordered_chain (the strongest of the members not yet placed). Then, for a set of at most MAX_K members, one
request for each bound the kind leaves open: "lower" (at_most_one, at_most_k and ordered_chain) and "upper" (at_least_k and
ordered_chain). A group's bounds are canonicalized by adapters.base.group_terms (exactly_one when lo == hi == 1, at_most_one
rather than at_most_k 1, no bound at its trivial value), and the kind's own term is emitted first. A set whose
ordered_chain would drop a member (both sides of a binary market) asks no chain or bound questions and gets no decision. A reading row
(source "reading": the proposition, then its options: four in Mentat-Hard, at most 26) gets two requests, "base" and "alt". Up to MB_S1_PARALLEL requests are in
flight per call; results come back in item order and do not depend on timing.

Conf. A relation's conf is ln p(best named relation) - ln p(NONE), and a group's is ln p(kind) - ln p(none), with each
probability clamped at P_FLOOR so the value is finite. A reading's conf is ln p(pick) - ln p(none). A distribution is refused
(an item error) unless every option has a probability in [0, 1] and the probabilities sum to 1 within 1e-2.

Probs. An answer from the server also carries its probabilities as "probs": a relation over its five options (EQUIV
included), a group over none and the five kinds, a binding's status over bound and unresolved. An abstention has none.

Rule cards. An outcome may carry its league's rule card (ctx.context). The state of a relation (the cards of A and B, once each),
of a group (the cards of its members) and of a reading (U's card) starts with those cards under "League rules:", before the
body; a binding has none, and a blank card is no card. A reading with a card asks base_rules and alt_rules (under reading (1)
and (2) of the rules). A request with no card keeps its version-5 bytes.

Limits. The server's context is per question (the state plus that question's instructions). An over-long state is HTTP 400
for its request. A single question refused for its input length (413, or a 400 saying "longer than the context
length" or "exceeds the available context size") is an abstention: a relation is NONE without conf, a group is no decision. Other refusals are item errors. Instructions
past the server's limit are not refused: the server truncates them silently (about 160 tokens, the head kept). So the child
refuses a relation whose instructions exceed MAX_INSTRUCTION_CHARS, as an item error. HTTP 500 is retried once; a second one
is an item error for its question. A connection refused, a timeout, a 3xx or a 404 fails the whole call.

Floor. The child applies no relation floor: describe() reports relation_floor null. A run passes the calibrated
--relation-floor to the harness (dev builds run without one, to get the full conf curve).

Pins. children/ollama_models.json maps each tag to its digest and its server kind (`server`: "ollama" when absent, "openai"
for a digest in /v1/models, "reply" for a digest in the /v1/systemone reply). `pin <tag> [--license X]` writes an entry from
the live server; `conform <tag> [--url]` checks a live server and prints a JSON report (exit 1 on any failure). A server other
than Ollama is pinned only when conform passes, and its entry keeps that conform record; the child refuses it without one. The
child refuses a tag that is unpinned, whose digest or server kind differs, or that reports no digest, unless
MB_S1_ALLOW_UNPINNED=1 (which never allows a missing digest). At exit the child reads the digest again and refuses the run if
it changed or cannot be read. Each refused request is printed on stderr, and the 400s are counted in rejected_400, which is
also printed on stderr at exit.

Configuration (environment): MB_S1_MODEL (the tag, required; a bare name is "<name>:latest"), MB_S1_URL (default
http://127.0.0.1:11434),
MB_S1_PARALLEL (default 4), MB_S1_TIMEOUT (seconds per request, default 300), MB_S1_ALLOW_UNPINNED (1 to allow),
MB_S1_PINS (the pin file, default children/ollama_models.json), MB_S1_STATS (a JSON file of the cumulative counts, optional).
MB_S1_URL must be http(s) to localhost, 127.0.0.1 or
::1: the child talks to a local server only. It follows no redirects and uses no proxy.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import http.client
import json
import math
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable

from ..adapters.base import GROUP_KINDS, group_terms
from ..seal import replace_with_retry
from .common import binary_label, chain_members, class_key
from .protocol import ChildError, serve

DEFAULT_URL = "http://127.0.0.1:11434"
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
DEFAULT_PARALLEL = 4
DEFAULT_TIMEOUT_S = 300.0
PINS = Path(__file__).with_name("ollama_models.json")
MAX_QUESTIONS = 1  # questions per request: one, never packed. Packing changed relation decisions on nimble:9b-bf16
MAX_INSTRUCTION_CHARS = 300  # the server cuts instructions past about 160 tokens (~780 characters of question text)
MAX_K = 8
MAX_CHAIN = 26  # one letter per member of an ordered_chain menu
# The terms a kind asks for (group_terms): emitted first, so the decision the kind states leads the answer.
OWN_TERMS = {"ordered_chain": ("ordered_chain",), "exactly_one": ("exactly_one",), "at_least_k": ("at_least_k",),
             "at_most_k": ("at_most_one", "at_most_k"), "at_most_one": ("at_most_one", "at_most_k")}
P_FLOOR = 1e-12  # a probability of 0 is read as this, so every conf is finite
PROB_SUM_TOLERANCE = 1e-2
ORDER_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
STATUS = ("bound", "unresolved")
NEGATED = ("no", "yes")
MISSING = ("no_date", "no_source", "no_threshold", "ambiguous")
GROUP_CHOICES = ("none",) + GROUP_KINDS

# Every prompt, option text and format string of the child. PROMPT_SHA256 pins all of them in describe().
PROMPTS: dict[str, Any] = {
    "system": ("You label one or two outcomes of a prediction market for a benchmark. "
               "Answer with exactly one of the listed options."),
    "text": {
        "record": "Question: {question}\nLabel: {label}\nEvent: {event_title}",
        "proposition": "Proposition {name}: {question} [label: {label}]",
        "relation_lead": "{proposition}\n",  # the line of B, above the relation instructions
        "listing": "{number}. {question} [label: {label}]",
        "letter": "{letter}. {question} [label: {label}]",
        "group_state": "Outcomes of one candidate set:\n{listing}",
        "chain_state": "Outcomes of one chain, not yet ranked:\n{listing}",
        "reading_state": "{proposition}\nOptions (each label in brackets is part of that option's meaning):\n{listing}",
        "state": "{system}\n\n{body}",
        "rules": "League rules:\n{cards}\n\n",  # the rule cards of the outcomes asked about, before the body (see _rules)
        "card_join": "\n\n",  # between two cards of one state
    },
    "status": {
        "instructions": "Is the meaning of this proposition fully determined by its wording? (Ignore whether it has happened or will happen.)",
        "criteria": {"bound": "the wording pins down exactly what would make it true (who, which quantity, which threshold, which period)",
                     "unresolved": ("the wording leaves its meaning ambiguous, such as an unclear date like 'by the break' or an "
                                    "unclear threshold or scope like 'top half', so it could be read in more than one way")},
    },
    "negated": {
        "instructions": ("Is the label the negative side of the question (the side that says the event did NOT "
                         "happen)?"),
        "criteria": {"no": "the label is the positive side, such as Yes, Over or Up",
                     "yes": "the label is the negative side, such as No, Under or Down"},
    },
    "missing": {
        "instructions": "What makes the meaning of this proposition unclear?",
        "criteria": {"no_date": "the date or time is unclear", "no_source": "no resolution source is named",
                     "no_threshold": "the threshold or target is unclear",
                     "ambiguous": "the wording could be read in more than one way"},
    },
    "relation": {
        "instructions": "Which relation holds between A and B?",
        "choices": ["EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE"],
        # the menu of a pair: the choices shuffled by random.Random(crc32(f"{a_id}\x1f{b_id}")), as hf_child's relation_order
        "menu_order": "crc32-shuffle-v1",
        "menu_seed": "{a_id}\x1f{b_id}",  # the crc32 input of the shuffle
        "ties": "menu order",  # an exact tie between relations goes to the one the pair's menu lists first
        "criteria": {"EQUIV": "A and B mean the same: each holds exactly when the other does",
                     "IMPLIES": "A implies B: if A holds, B holds", "IMPLIED_BY": "B implies A: if B holds, A holds",
                     "EXCLUDES": "not both: A and B cannot both hold", "NONE": "no such relation"},
    },
    "kind": {
        "instructions": "Which constraint holds across these outcomes?",
        "criteria": {"none": "no constraint", "exactly_one": "exactly one holds", "at_most_one": "at most one holds",
                     "at_least_k": "at least k hold", "at_most_k": "at most k hold",
                     "ordered_chain": "each outcome implies the next"},
    },
    "k": {
        "instructions": "How many of these outcomes is the constraint about (k)?",
        "criteria": {str(k): f"k is {k}" for k in range(1, MAX_K + 1)},
    },
    "order": {"instructions": "Which outcome is the strongest, the one that implies all the others?",
              "option": "outcome {letter}", "letters": ORDER_LETTERS},
    "lower": {"instructions": "At least how many of these outcomes must hold?",
              "criteria": {str(j): ("none required" if j == 0 else f"at least {j}") for j in range(MAX_K + 1)}},
    "upper": {"instructions": "At most how many of these outcomes can hold?",
              "criteria": {**{str(j): f"at most {j}" for j in range(1, MAX_K)}, "no_limit": "no limit"}},
    "base": {"instructions": ("Which option has the same meaning as the proposition, read most literally? "
                              "Answer none if no option does.")},
    "alt": {"instructions": ("Which option matches a second, different reading of the proposition? "
                             "If it has no second reading, give the option that matches its literal reading. "
                             "Answer none if no option matches.")},
    # the reading questions when the state carries a rule card (the numbered readings of the card)
    "base_rules": {"instructions": ("Under reading (1) of the league rules, which option means the same as U? "
                                    "Answer none if no option does.")},
    "alt_rules": {"instructions": ("Under reading (2) of the league rules, which option means the same as U? "
                                   "If U's wording fixes one meaning (it uses no term with two readings, or it says "
                                   "which reading it means), give the option for that meaning. Answer none if no option does.")},
    "reading": {"option": "option {letter}", "none": "none of the options"},
}
PROMPT_SHA256 = hashlib.sha256(json.dumps(PROMPTS, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
T = PROMPTS["text"]
RELATION_CHOICES = tuple(PROMPTS["relation"]["choices"])
CONTEXT_TEXTS = ("longer than the context length", "exceeds the available context size")  # the 400 texts of an input that does
# not fit the context: laya says the first, Ollama 0.40.11 the second. Matched in any letter case (is_over_context_text).


def is_over_context_text(message: str) -> bool:
    """True when a 400's message says the input does not fit the context (any letter case)."""
    text = message.lower()
    return any(phrase in text for phrase in CONTEXT_TEXTS)


OPS = ("decide_binding", "decide_relation", "decide_group")
STAT_FIELDS = ("units", "requests", "abstained_overlength", "item_errors", "retries_500", "refused_instructions")
RETRY_PAUSE_S = 0.5  # before the one retry of a 500


def _load_stats(path: str | None) -> dict[str, dict[str, int]]:
    """The counts of an existing MB_S1_STATS file (so a restarted child continues the count), else zeros. A malformed file
    is a RuntimeError: the count is not reset silently, and the file must be moved aside to start a new one."""
    stats = {op: dict.fromkeys(STAT_FIELDS, 0) for op in OPS}
    if path is None or not os.path.exists(path):
        return stats
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        ops = doc["ops"]
        for op in OPS:
            for field in STAT_FIELDS:
                value = ops[op][field]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{op}.{field} is not a count")
                stats[op][field] = value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"MB_S1_STATS {path} is malformed ({exc}); move it aside to start a new count") from None
    return stats


class _Refused(ChildError):
    """The server refused a request with HTTP 400, 413 or 500 (a 500 only after its one retry): an item error for the items
    of that request. over_context: the input did not fit the context (or the size limit), so the model abstains on that
    question instead."""

    def __init__(self, status: int, message: str):
        super().__init__(f"systemone HTTP {status}: {message}")
        self.status = status
        self.over_context = status == 413 or (status == 400 and is_over_context_text(message))


class _RefusedInstructions(ChildError):
    """A relation whose instructions are over MAX_INSTRUCTION_CHARS: not sent, as an item error."""


class _Abstained:
    """A question the model abstains on (its input does not fit the context). decide() answers it: a relation is NONE with
    no conf, a group has no decision."""

    def __init__(self, op: str):
        self.op = op


def _choice(stage: str, criteria: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = PROMPTS[stage]
    return {"type": "choice", "instructions": spec["instructions"],
            "criteria": spec["criteria"] if criteria is None else criteria}


def relation_order(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, ...]:
    """The menu order of the relations for one pair (PROMPTS["relation"]["menu_order"]): a shuffle keyed on the pair's ids.
    It is hf_child.relation_order applied to the five RELATION_CHOICES (hf_child has four, without EQUIV): the same
    random.Random(crc32) shuffle of the list, so the order is not hf_child's order with EQUIV inserted."""
    order = list(RELATION_CHOICES)
    random.Random(zlib.crc32(f"{a['id']}\x1f{b['id']}".encode("utf-8"))).shuffle(order)
    return tuple(order)


def _relation_question(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    spec = PROMPTS["relation"]
    order = relation_order(a, b)
    return {"type": "choice", "instructions": T["relation_lead"].format(proposition=_prop("B", b)) + spec["instructions"],
            "criteria": {k: spec["criteria"][k] for k in order}}


def _frame(body: str) -> str:
    return T["state"].format(system=PROMPTS["system"], body=body)


def _rules(members: list[dict[str, Any]]) -> str:
    """The lead of a state: the distinct rule cards of the members, stripped, in member order, under "League rules:" ("" when
    no member has a non-blank card, so the request keeps its version-5 bytes). The card goes first so a server can reuse the
    prefix of a league."""
    cards = list(dict.fromkeys(card for card in ((m.get("context") or "").strip() for m in members) if card))
    return T["rules"].format(cards=T["card_join"].join(cards)) if cards else ""


def _record(ctx: dict[str, Any]) -> str:
    return T["record"].format(question=ctx["question"], label=ctx["label"], event_title=ctx.get("event_title"))


def _prop(name: str, ctx: dict[str, Any]) -> str:
    return T["proposition"].format(name=name, question=ctx["question"], label=ctx["label"])


def _listing(members: list[dict[str, Any]]) -> str:
    return "\n".join(T["listing"].format(number=i + 1, question=m["question"], label=m["label"])
                     for i, m in enumerate(members))


def _count(value: Any) -> int:
    """A usage count: a non-negative integer that a float holds exactly, else 0 (invalid fields count as 0)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**53 else 0


def _error_text(exc: urllib.error.HTTPError) -> str:
    raw = exc.read().decode("utf-8", "replace")
    try:
        return str(json.loads(raw).get("error", raw))
    except (ValueError, AttributeError):
        return raw[:200]


def _probs(answer: Any, keys: Iterable[str]) -> dict[str, float]:
    """The probability of each option of a choice answer. ChildError (the answer's own, or a new one) when the answer is
    not a choice over keys, a probability is not in [0, 1], or the probabilities do not sum to 1."""
    probs = answer.get("probabilities") if isinstance(answer, dict) and answer.get("type") == "choice" else None
    if not isinstance(probs, dict):
        raise ChildError("answer is not a choice with probabilities")
    keys = list(keys)
    values: dict[str, float] = {}
    for key in keys:
        p = probs.get(key)
        try:
            valid = not isinstance(p, bool) and isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1
        except OverflowError:  # an integer too large for a float
            valid = False
        if not valid:
            raise ChildError(f"probability of {key} is missing or not in [0, 1]")
        values[key] = float(p)
    total = sum(values.values())
    if not total > 0 or abs(total - 1) > PROB_SUM_TOLERANCE:
        raise ChildError(f"probabilities sum to {total}, not 1")
    return values


def _margin(probs: dict[str, float], best: str, base: str) -> float:
    """ln p(best) - ln p(base), each probability clamped at P_FLOOR so the value is finite."""
    return math.log(max(probs[best], P_FLOOR)) - math.log(max(probs[base], P_FLOOR))


def _argmax(scores: dict[str, float], order: Iterable[str] | None = None) -> str:
    """The option with the largest score; an exact tie goes to the first of `order` (default: the keys of scores)."""
    return max(scores if order is None else order, key=lambda k: scores[k])


def _relation_decision(answer: Any, order: tuple[str, ...]) -> dict[str, Any]:
    """The relation decision of one answer over the menu `order` of its pair: the argmax over the five options (an exact
    tie goes to the one the menu lists first), conf, the margin of the best named relation over NONE (negative when NONE
    is the argmax, as hf_child reports it), and the probs of the five options."""
    probs = _probs(answer, RELATION_CHOICES)
    named = tuple(k for k in order if k != "NONE")
    return {"relation": _argmax(probs, order), "conf": _margin(probs, _argmax(probs, named), "NONE"), "probs": probs}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a 3xx is an error reply, so a redirect cannot send the request off the loopback


# No proxy either: the trust boundary is the loopback server named by MB_S1_URL.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _loopback_url(raw: str) -> str:
    """The server URL, which must be http or https to a loopback host (localhost, 127.0.0.1 or ::1), with no userinfo and no
    backslash. RuntimeError otherwise: the child talks to a local model server and to nothing else."""
    url = raw.rstrip("/")
    try:
        parts = urllib.parse.urlsplit(url)
        parts.port  # raises ValueError for a bad port
    except ValueError:
        parts = None
    if (parts is None or "\\" in url or "@" in parts.netloc or parts.scheme not in ("http", "https")
            or parts.hostname not in LOOPBACK_HOSTS):
        raise RuntimeError(f"MB_S1_URL must be an http(s) URL on localhost, 127.0.0.1 or ::1 with no userinfo, got {raw!r}")
    return url


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    with _OPENER.open(url, timeout=timeout) as response:
        return json.loads(response.read())


def _get_optional(url: str, path: str, timeout: float) -> dict[str, Any] | None:
    """The JSON object of GET url+path, or None when the server does not have the path: an HTTP 404, 405 or 501, or a 200 that
    is not a JSON object (noted on stderr). Other HTTP errors are raised."""
    try:
        reply = _get_json(url + path, timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 405, 501):
            return None
        raise
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        print(f"systemone_child: GET {path} did not return a JSON object; treated as absent", file=sys.stderr)
        return None
    return reply


def _bare_digest(digest: Any) -> Any:
    """A digest without its optional "sha256:" prefix: the pins hold the bare hex digest."""
    return digest[len("sha256:"):] if isinstance(digest, str) and digest.startswith("sha256:") else digest


# What a refused, unreachable or malformed server raises: reported as one line (pin, startup, exit) or as a failed check.
FAILURES = (RuntimeError, OSError, ValueError, http.client.HTTPException)


def _request_body(model: str, state: str, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"model": model, "state": _frame(state), "questions": questions, "keep_alive": -1}


def _raw_post(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST body to url's /v1/systemone and return the reply. A refused request raises urllib.error.HTTPError."""
    request = urllib.request.Request(url + "/v1/systemone", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    with _OPENER.open(request, timeout=timeout) as response:
        return json.loads(response.read())


# The question of the probe that reads a digest from the reply of a server with no model list.
PROBE_RECORD = {"id": "probe", "question": "Will Example Coin close above 100 on June 1?", "label": "Yes", "event_title": None}


def _probe(url: str, model: str, timeout: float) -> dict[str, Any] | None:
    """The reply to one status question (its "digest" field, if any); None when the server does not serve the model (404).
    A refusal other than 404, a reply that is not a JSON object, or one that names another model is a RuntimeError."""
    try:
        reply = _raw_post(url, _request_body(model, _record(PROBE_RECORD), {"status": _choice("status")}), timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"the probe of {model} was refused: HTTP {exc.code}: {_error_text(exc)}") from None
    except ValueError as exc:
        raise RuntimeError(f"the probe of {model} did not return JSON: {exc}") from None
    if not isinstance(reply, dict):
        raise RuntimeError(f"the probe of {model} did not return a JSON object")
    if "model" in reply and resolve_tag(str(reply["model"])) != resolve_tag(model):
        raise RuntimeError(f"the probe of {model} was answered for model {reply['model']!r}")
    return reply


def _version(url: str, timeout: float) -> str | None:
    """The Ollama version of the server (/api/version). Optional: None for any error (HTTP, socket, a malformed status line)
    or a reply that is not an object."""
    try:
        reply = _get_json(url + "/api/version", timeout)
    except (OSError, http.client.HTTPException, ValueError):
        return None
    version = reply.get("version") if isinstance(reply, dict) else None
    return version if isinstance(version, str) else None


def _live(url: str, model: str, timeout: float) -> dict[str, Any] | None:
    """The server kind, digest and size of `model`, or None when the server does not serve it. The digest is taken from the
    first source that gives one: the entry for the model in /api/tags (server "ollama"), then its entry in /v1/models (server
    "openai"), then the "digest" field of the reply to a probe (server "reply"). A source that lists the model without a digest,
    or does not list it, falls through to the next. The model is None only when the probe finds no model; a server where no
    source gives a digest is refused."""
    tags = _get_optional(url, "/api/tags", timeout)
    entry = None if tags is None else next((e for e in tags.get("models", []) if model in (e.get("name"), e.get("model"))), None)
    if entry is not None and isinstance(entry.get("digest"), str) and entry["digest"]:
        return {"server": "ollama", "digest": _bare_digest(entry["digest"]), "size": entry.get("size")}
    models = _get_optional(url, "/v1/models", timeout)
    entry = None if models is None else next((e for e in models.get("data", []) if e.get("id") == model), None)
    if entry is not None and isinstance(entry.get("digest"), str) and entry["digest"]:
        return {"server": "openai", "digest": _bare_digest(entry["digest"]), "size": entry.get("size")}
    reply = _probe(url, model, timeout)
    if reply is None:
        return None
    digest = reply.get("digest")
    if not isinstance(digest, str) or not digest:
        raise RuntimeError(f"the server gives no digest for {model} (in /api/tags, /v1/models or the reply to a probe); "
                           "a server that reports none is refused")
    return {"server": "reply", "digest": _bare_digest(digest), "size": None}


def resolve_tag(tag: str) -> str:
    """The name Ollama gives a tag: a bare name is "<name>:latest" (a name with a ":" is already a tag)."""
    return tag if ":" in tag else f"{tag}:latest"


def pins_path(env: dict[str, str]) -> Path:
    return Path(env.get("MB_S1_PINS") or PINS)


def load_pins(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_pin(model: str, live: dict[str, Any] | None, pins: dict[str, Any], allow_unpinned: bool) -> str:
    """The live digest of `model`, once it is verified: the tag must be on the server, and pinned with that digest and server
    kind unless allow_unpinned. Raises RuntimeError otherwise."""
    if live is None:
        raise RuntimeError(f"{model} is not on the Ollama server (GET /api/tags) nor in the /v1/models list; pull it first")
    digest = live["digest"]
    if not allow_unpinned:
        pin = pins.get(model)
        if pin is None:
            raise RuntimeError(f"{model} has no pin in {PINS.name}; run `python -m mentat_bench.children.systemone_child "
                               f"pin {model}` or set MB_S1_ALLOW_UNPINNED=1")
        if _bare_digest(pin["digest"]) != digest:
            raise RuntimeError(f"digest of {model} is {digest}, the pin says {pin['digest']}; "
                               "set MB_S1_ALLOW_UNPINNED=1 to override")
        if pin.get("server", "ollama") != live["server"]:
            raise RuntimeError(f"{model} is served by {live['server']}, the pin says {pin.get('server', 'ollama')}")
        if live["server"] != "ollama" and not (isinstance(pin.get("conform"), dict) and pin["conform"].get("ok") is True):
            raise RuntimeError(f"{model} is served by {live['server']} and its pin has no passing conform record; "
                               "run the pin command again against the server")
    return digest


def pin_model(model: str, license_: str | None, env: dict[str, str]) -> dict[str, Any]:
    """Write or update the pin of `model` (resolved to its tag) from the live server. Other entries of the pin file are
    kept. The file is replaced atomically, so an interrupted write leaves the old pins."""
    model = resolve_tag(model)
    url = _loopback_url(env.get("MB_S1_URL", DEFAULT_URL))
    timeout = float(env.get("MB_S1_TIMEOUT", DEFAULT_TIMEOUT_S))
    live = _live(url, model, timeout)
    if live is None:
        raise RuntimeError(f"{model} is not on the Ollama server (GET /api/tags) nor in the /v1/models list; pull it first")
    entry = {"digest": live["digest"], "license": license_, "ollama_version": _version(url, timeout), "size": live["size"]}
    if live["server"] != "ollama":  # absent for Ollama, so the existing entries are valid as they are
        report = conform(model, url, env, pin_file=False)  # a server that is not Ollama is pinned only when it conforms
        if not report["ok"]:
            failed = ", ".join(c["check"] for c in report["checks"] if not c["ok"])
            raise RuntimeError(f"{model} does not conform ({failed}); it is not pinned")
        entry["server"] = live["server"]
        entry["conform"] = {"ok": True, "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "checks": report["checks"]}
    path = pins_path(env)
    pins = load_pins(path)
    pins[model] = entry
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return pins[model]


class SystemOneChild:
    def __init__(self, model_id: str, url: str, digest: str, ollama_version: str, parallel: int = DEFAULT_PARALLEL,
                 timeout: float = DEFAULT_TIMEOUT_S, stats_path: str | None = None):
        self.model_id = model_id
        self.url = url
        self.digest = digest
        self.ollama_version = ollama_version
        self.parallel = parallel
        self.timeout = timeout
        self.rejected_400 = 0  # requests the server refused as invalid or over-length, in all calls
        self.stats_path = stats_path  # MB_S1_STATS: the cumulative counts, rewritten after every decide()
        self.stats = _load_stats(stats_path)  # over the calls that returned, continued from MB_S1_STATS
        self._lock = threading.Lock()
        self._meter = {"tokens_in": 0, "tokens_out": 0}
        self._call = {"requests": 0, "retries_500": 0}  # the current call's requests and 500 retries

    @property
    def abstentions(self) -> dict[str, int]:
        return {op: self.stats[op]["abstained_overlength"] for op in ("decide_relation", "decide_group")}

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "SystemOneChild":
        env = os.environ if env is None else env
        model = env.get("MB_S1_MODEL")
        if not model:
            raise RuntimeError("MB_S1_MODEL must name the Ollama model tag to serve")
        model = resolve_tag(model)
        url = _loopback_url(env.get("MB_S1_URL", DEFAULT_URL))
        parallel = int(env.get("MB_S1_PARALLEL", DEFAULT_PARALLEL))
        if parallel < 1:
            raise ValueError("MB_S1_PARALLEL must be at least 1")
        timeout = float(env.get("MB_S1_TIMEOUT", DEFAULT_TIMEOUT_S))
        # the digest is read before the first decision request (a reply probe, for a server with no model list, is one request)
        digest = check_pin(model, _live(url, model, timeout), load_pins(pins_path(env)),
                           allow_unpinned=env.get("MB_S1_ALLOW_UNPINNED") == "1")
        version = _version(url, timeout)
        stats_path = env.get("MB_S1_STATS") or None
        if stats_path is not None and not Path(stats_path).parent.is_dir():
            raise RuntimeError(f"MB_S1_STATS {stats_path}: its directory does not exist")
        return cls(model, url, digest, version, parallel, timeout, stats_path=stats_path)

    def verify_digest(self) -> None:
        """Refuses when the digest of the model is not the one checked at startup (the pin was checked then). A server that
        cannot be asked fails closed with its own message: the digest was not verified."""
        try:
            live = _live(self.url, self.model_id, self.timeout)
        except FAILURES as exc:
            raise RuntimeError(f"digest_at_exit not verified: {exc}") from None
        if live is None:
            raise RuntimeError(f"digest of {self.model_id} is not listed at exit; the run is refused")
        if live["digest"] != self.digest:
            raise RuntimeError(f"digest of {self.model_id} is {live['digest']} at exit, {self.digest} at start; "
                               "the run is refused")

    def describe(self) -> dict[str, Any]:
        return {
            "name": "systemone-choice",
            "version": "7",  # version 7: alt_rules says a gloss that names its reading fixes it (6: rule cards; 5: bounds)
            "model": {"id": self.model_id, "revision": self.digest, "kind": "local", "ollama_version": self.ollama_version,
                      "child_version": "7", "prompt_sha256": PROMPT_SHA256},  # the hello model block: a resume checks it
            "prompt_sha256": PROMPT_SHA256,
            "parallel": self.parallel,
            "relation_floor": None,  # the child applies none: the harness's --relation-floor does
        }

    # Requests. A refused 400, 413 or 500 raises _Refused (a ChildError, an item error); a 500 is retried once first. Any
    # other failure raises the error itself, which fails the whole call.

    def _count_call(self, key: str) -> None:
        with self._lock:
            self._call[key] += 1

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in (1, 2):
            self._count_call("requests")
            try:
                reply = _raw_post(self.url, body, self.timeout)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in (400, 413, 500):
                    raise  # 404, 3xx and the rest concern the server, and fail the call
                message = _error_text(exc)
                print(f"systemone_child: HTTP {exc.code}: {message}", file=sys.stderr)
                if exc.code == 500 and attempt == 1:  # one retry, after a pause; a second 500 is an item error
                    self._count_call("retries_500")
                    time.sleep(RETRY_PAUSE_S)
                    continue
                if exc.code == 400:
                    with self._lock:
                        self.rejected_400 += 1
                raise _Refused(exc.code, message) from None
            except ValueError as exc:
                raise ChildError(f"systemone reply is not JSON: {exc}") from None
        answers = reply.get("answers") if isinstance(reply, dict) else None
        if not isinstance(answers, dict):
            raise ChildError("systemone reply has no answers")
        usage = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
        with self._lock:
            self._meter["tokens_in"] += _count(usage.get("input_tokens"))
            self._meter["tokens_out"] += _count(usage.get("output_tokens"))
        return answers

    def _ask(self, state: str, questions: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """One request. It carries at most MAX_QUESTIONS questions, so a refusal is always for that one question."""
        if len(questions) > MAX_QUESTIONS:
            raise ValueError(f"{len(questions)} questions in one request; the limit is {MAX_QUESTIONS}")
        return self._post(_request_body(self.model_id, state, questions))

    def _one(self, state: str, name: str, question: dict[str, Any], keys: Iterable[str]) -> dict[str, float]:
        """The probabilities of one question, asked alone. A refused request raises _Refused."""
        return _probs(self._ask(state, {name: question}).get(name), keys)

    # Decisions

    def _binding(self, ctx: dict[str, Any]) -> dict[str, Any]:
        binary, negated = binary_label(ctx["label"])
        status = self._one(_record(ctx), "status", _choice("status"), STATUS)
        if _argmax(status) == "bound":
            if not binary:  # a binary label names its own side, as in hf_child
                negated = _argmax(self._one(_record(ctx), "negated", _choice("negated"), NEGATED)) == "yes"
            return {"status": "bound", "class": class_key(ctx["question"], ctx["label"]), "negated": negated, "missing": [],
                    "probs": status}
        missing = _argmax(self._one(_record(ctx), "missing", _choice("missing"), MISSING))
        return {"status": "unresolved", "class": None, "negated": False, "missing": [missing], "probs": status}

    def _relation(self, a: dict[str, Any], b: dict[str, Any]) -> Any:
        """The relation decision of one pair, asked alone. Input too long for the context is an abstention; any other
        refusal, or a degenerate answer, is an item error."""
        question = _relation_question(a, b)
        if len(question["instructions"]) > MAX_INSTRUCTION_CHARS:
            return _RefusedInstructions(f"relation instructions of {len(question['instructions'])} characters exceed "
                                        f"{MAX_INSTRUCTION_CHARS}: the server would truncate them")
        try:
            answer = self._ask(_rules([a, b]) + _prop("A", a), {"relation": question}).get("relation")
        except _Refused as exc:
            return _Abstained("decide_relation") if exc.over_context else exc
        try:
            return _relation_decision(answer, relation_order(a, b))
        except ChildError as exc:
            return exc

    def _relations(self, pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[Any]:
        """One request per pair: each pair is its own unit, so no answer depends on another pair's question."""
        units = [([i], lambda a=a, b=b: [self._relation(a, b)]) for i, (a, b) in enumerate(pairs)]
        return self._map(units, len(pairs))

    def _chain(self, members: list[dict[str, Any]], rules: str) -> list[dict[str, Any]]:
        """The members strongest first: each round asks for the strongest of those not yet placed; the last one left
        needs no question (as hf_child's order stage). rules is the lead of the group's state (see _rules)."""
        left: list[dict[str, Any]] = list(members)
        placed: list[dict[str, Any]] = []
        while len(left) >= 2:
            letters = ORDER_LETTERS[:len(left)]
            listing = "\n".join(T["letter"].format(letter=letter, question=m["question"], label=m["label"])
                                for letter, m in zip(letters, left))
            state = rules + T["chain_state"].format(listing=listing)
            options = {letter: PROMPTS["order"]["option"].format(letter=letter) for letter in letters}
            scores = self._one(state, "order", _choice("order", criteria=options), letters)
            placed.append(left.pop(letters.index(_argmax(scores))))
        return placed + left

    def _group(self, ctx: dict[str, Any]) -> Any:
        try:
            return self._group_decision(ctx)
        except _Refused as exc:
            if not exc.over_context:
                raise
            return _Abstained("decide_group")  # a candidate set that does not fit the context: no decision

    def _group_decision(self, ctx: dict[str, Any]) -> dict[str, Any]:
        if ctx["source"] == "reading":
            return self._reading(ctx)
        members = ctx["members"]
        ids = [m["id"] for m in members]
        n = len(ids)
        state = _rules(members) + T["group_state"].format(listing=_listing(members))
        probs = self._one(state, "kind", _choice("kind"), GROUP_CHOICES)
        kind = _argmax(probs)
        if kind == "none":
            return {"groups": [], "probs": probs}
        conf = _margin(probs, kind, "none")
        chain_ids: list[str] | None = None
        lo, hi = 0, n  # the bounds: those the kind states here, the rest are asked by _bounds
        if kind == "ordered_chain":
            chain = chain_members(members)
            # chain_members drops one side of a binary market: no chain or bound questions for such a set
            if len(chain) < n or not 2 <= n <= MAX_CHAIN:
                return {"groups": [], "probs": probs}
            chain_ids = [m["id"] for m in self._chain(chain, _rules(members))]
        elif kind == "exactly_one":
            lo = hi = 1
        elif kind == "at_most_one":
            hi = 1
        elif kind in ("at_least_k", "at_most_k"):
            ks = tuple(str(k) for k in range(1, min(n, MAX_K) + 1))
            k_criteria = {k: PROMPTS["k"]["criteria"][k] for k in ks}
            k = int(_argmax(self._one(state, "k", _choice("k", criteria=k_criteria), ks)))
            lo, hi = (k, n) if kind == "at_least_k" else (0, k)
        lo, hi = self._bounds(state, kind, lo, hi, n)
        terms = group_terms(lo, hi, n, chain_ids is not None)  # the canonical terms (adapters.base.group_terms)
        terms.sort(key=lambda term: term[0] not in OWN_TERMS[kind])  # stable: the kind's own term first, then the others
        groups: list[dict[str, Any]] = []
        for term_kind, k in terms:
            if term_kind == "ordered_chain":
                groups.append({"kind": term_kind, "members": chain_ids, "direction": "forward", "scope": None, "conf": conf})
            else:
                decision: dict[str, Any] = {"kind": term_kind, "members": ids, "scope": None, "conf": conf}
                if k is not None:
                    decision["k"] = k
                groups.append(decision)
        return {"groups": groups, "probs": probs}

    def _bounds(self, state: str, kind: str, lo: int, hi: int, n: int) -> tuple[int, int]:
        """The bounds of a group: each side the kind leaves open is one choice question. "lower" (the fewest that must hold)
        follows at_most_one, at_most_k and ordered_chain; "upper" (the most that can hold, or "no limit") follows at_least_k
        and ordered_chain. The menus keep lo <= hi and hi >= 1, and an upper menu is asked only when it has a choice. A set
        over MAX_K members is not asked: it keeps the kind's own bounds, as v4 did."""
        if n > MAX_K:
            return lo, hi
        if kind in ("at_most_one", "at_most_k", "ordered_chain"):
            menu = {str(j): PROMPTS["lower"]["criteria"][str(j)] for j in range(hi + 1)}
            lo = int(_argmax(self._one(state, "lower", _choice("lower", criteria=menu), menu)))
        if kind in ("at_least_k", "ordered_chain") and max(lo, 1) < n:
            menu = {str(j): PROMPTS["upper"]["criteria"][str(j)] for j in range(max(lo, 1), n)}
            menu["no_limit"] = PROMPTS["upper"]["criteria"]["no_limit"]
            pick = _argmax(self._one(state, "upper", _choice("upper", criteria=menu), menu))
            hi = n if pick == "no_limit" else int(pick)
        return lo, hi

    def _reading(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """A reading row: members[0] is the proposition u, the rest its options (lettered: four in Mentat-Hard, at most 26). Two choice questions over
        the options and none: base (the most literal reading of u) and alt (another plausible reading, which may be the same
        option). For each reading that picks an option c, the pair [u, c] and [c, u] under that reading's scope."""
        u, options = ctx["members"][0], ctx["members"][1:]
        if not options:
            return {"groups": []}
        if len(options) > len(ORDER_LETTERS):
            raise ChildError(f"a reading row has {len(options)} options; the limit is {len(ORDER_LETTERS)}")
        letters = ORDER_LETTERS[:len(options)]
        listing = "\n".join(T["letter"].format(letter=letter, question=m["question"], label=m["label"])
                            for letter, m in zip(letters, options))
        rules = _rules([u])
        state = rules + T["reading_state"].format(proposition=_prop("U", u), listing=listing)
        menu = {letter: PROMPTS["reading"]["option"].format(letter=letter) for letter in letters}
        menu["none"] = PROMPTS["reading"]["none"]
        groups: list[dict[str, Any]] = []
        for reading in ("base", "alt"):
            stage = f"{reading}_rules" if rules else reading  # the readings of the rule card, when U has one
            probs = self._one(state, reading, _choice(stage, criteria=menu), menu)
            pick = _argmax(probs)
            if pick == "none":
                continue
            scope = {"axis": "reading", "values": [reading], "of": ["alt", "base"]}
            conf = _margin(probs, pick, "none")
            option = options[letters.index(pick)]["id"]
            for pair in ([u["id"], option], [option, u["id"]]):
                groups.append({"kind": "ordered_chain", "members": pair, "direction": "forward", "scope": scope,
                               "conf": conf})
        return {"groups": groups}

    def _map(self, units: list[tuple[list[int], Callable[[], list[Any]]]], count: int) -> list[Any]:
        """Runs each unit (its item indices and a thunk giving one result per index, a ChildError for a failed item) in
        the thread pool. A fatal error propagates out of the call, and the requests still queued are cancelled."""
        results: list[Any] = [None] * count

        def run(unit: tuple[list[int], Callable[[], list[Any]]]) -> None:
            indices, thunk = unit
            try:
                values = thunk()
            except ChildError as exc:  # the request failed: its items fail, the rest of the call goes on
                values = [exc] * len(indices)
            for i, value in zip(indices, values):
                results[i] = value

        pool = ThreadPoolExecutor(max_workers=self.parallel)
        try:
            list(pool.map(run, units))  # a fatal error is raised here
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        return results

    def _answers(self, op: str, results: list[Any]) -> list[Any]:
        """The answers decide() returns, and the stats of the call. The counts are taken from the results returned: each
        item is a unit; an abstention and an item error are counted when their answer is returned. The call's requests and
        500 retries are counted when it returns. A call that raises counts nothing."""
        stats = self.stats[op]
        out: list[Any] = []
        for result in results:
            stats["units"] += 1
            if isinstance(result, _Abstained):
                stats["abstained_overlength"] += 1
                out.append({"relation": "NONE"} if op == "decide_relation" else {"groups": []})
                continue
            if isinstance(result, ChildError):
                stats["item_errors"] += 1
                if isinstance(result, _RefusedInstructions):
                    stats["refused_instructions"] += 1
            out.append(result)
        with self._lock:
            stats["requests"] += self._call["requests"]
            stats["retries_500"] += self._call["retries_500"]
        return out

    def _write_stats(self) -> None:
        """Rewrites MB_S1_STATS with the cumulative counts: a temporary file, then a rename that rides out a sharing
        violation. A failure is reported on stderr and never fails the decide() call."""
        if self.stats_path is None:
            return
        path = Path(self.stats_path)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps({"model": self.model_id, "ops": self.stats}, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
            replace_with_retry(tmp, path)
        except OSError as exc:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            print(f"systemone_child: could not write MB_S1_STATS {path}: {exc}", file=sys.stderr)

    def decide(self, op: str, items: list[dict[str, Any]]) -> tuple[list[Any], dict[str, Any]]:
        self._meter = {"tokens_in": 0, "tokens_out": 0}
        self._call = {"requests": 0, "retries_500": 0}
        if op == "decide_relation":
            results = self._relations([(item["a"], item["b"]) for item in items])
        else:
            decide_one = self._binding if op == "decide_binding" else self._group
            units = [([i], lambda ctx=item["ctx"]: [decide_one(ctx)]) for i, item in enumerate(items)]
            results = self._map(units, len(items))
        answers = self._answers(op, results)
        usage = {"tokens_in": self._meter["tokens_in"], "tokens_out": self._meter["tokens_out"], "cost_usd": 0.0,
                 "model": self.model_id, "kind": "local"}
        self._write_stats()  # after a call that returns; a call that raises changes no count
        return answers, usage


OVERSIZE_STATE = "word " * 200_000  # about 1 MB of text: over any context, so the server must refuse it
CONFORM_CARD = ("Rules of the Example League. Teams: Ann, Bo.\n"
                "'By the break' has two readings: (1) final points; (2) at least 2 points.")


def _conform_questions() -> list[tuple[str, str, dict[str, Any], tuple[str, ...]]]:
    """One fixed question per name the child asks, built by the child's own builders: (name, state, question, option keys)."""
    def item(oid: str, question: str, label: str) -> dict[str, Any]:
        return {"id": oid, "question": question, "label": label, "event_title": "Example Coin"}

    def lettered(members: list[dict[str, Any]]) -> str:
        return "\n".join(T["letter"].format(letter=letter, question=m["question"], label=m["label"])
                         for letter, m in zip(ORDER_LETTERS, members))

    a = item("conform:a", "Will Example Coin close above 100 on June 1?", "Yes")
    b = item("conform:b", "Will Example Coin close above 120 on June 1?", "No")
    members = [item(f"conform:m{i}", f"Will Example Coin close above {100 + 10 * i} on June 1?", "Yes") for i in range(3)]
    chain_members = [item(f"conform:c{i}", f"Will Example Coin close above {100 + 10 * i} on June 1?", "Yes")
                     for i in range(MAX_CHAIN)]
    options = [item(f"conform:o{i}", f"Will Example Coin close above {200 + 10 * i} on June 1?", "Yes") for i in range(4)]
    group = T["group_state"].format(listing=_listing(members))
    chain = T["chain_state"].format(listing=lettered(chain_members))
    reading = T["reading_state"].format(proposition=_prop("U", a), listing=lettered(options))
    # The largest menus of the decision code, built with its formulas (the menus are inline there): a k of up to MAX_K, a lower
    # bound of up to MAX_K + 1 options, an upper bound of up to MAX_K options, a chain of up to MAX_CHAIN letters, four readings.
    ks = tuple(str(k) for k in range(1, MAX_K + 1))
    lowers = tuple(str(j) for j in range(MAX_K + 1))
    uppers = tuple(str(j) for j in range(1, MAX_K)) + ("no_limit",)
    letters = ORDER_LETTERS[:MAX_CHAIN]
    readings = {letter: PROMPTS["reading"]["option"].format(letter=letter) for letter in ORDER_LETTERS[:4]}
    readings["none"] = PROMPTS["reading"]["none"]
    carded_reading = _rules([{**a, "context": CONFORM_CARD}]) + reading  # the reading state of a card-bearing U
    return [
        ("status", _record(a), _choice("status"), STATUS),
        ("negated", _record(a), _choice("negated"), NEGATED),
        ("missing", _record(a), _choice("missing"), MISSING),
        ("relation", _prop("A", a), _relation_question(a, b), RELATION_CHOICES),
        ("kind", group, _choice("kind"), GROUP_CHOICES),
        ("k", group, _choice("k", criteria={k: PROMPTS["k"]["criteria"][k] for k in ks}), ks),
        ("order", chain, _choice("order", criteria={letter: PROMPTS["order"]["option"].format(letter=letter)
                                                    for letter in letters}), letters),
        ("lower", group, _choice("lower", criteria={j: PROMPTS["lower"]["criteria"][j] for j in lowers}), lowers),
        ("upper", group, _choice("upper", criteria={j: PROMPTS["upper"]["criteria"][j] for j in uppers}), uppers),
        ("base", reading, _choice("base", criteria=readings), tuple(readings)),
        ("alt", reading, _choice("alt", criteria=readings), tuple(readings)),
        ("base_rules", carded_reading, _choice("base_rules", criteria=readings), tuple(readings)),
        ("alt_rules", carded_reading, _choice("alt_rules", criteria=readings), tuple(readings)),
    ]


def conform(model: str, url: str, env: dict[str, str], pin_file: bool = True) -> dict[str, Any]:
    """The conformance report of the server at `url` for `model`. Checks: (pin) the digest and server kind are the pin's (not
    run when pin_file is False, as `pin` runs it before the entry exists); (probabilities) each question of _conform_questions
    answers probabilities in [0, 1] summing to 1 within the child's tolerance; (determinism) the same question twice gives the
    same probabilities (absolute tolerance 1e-6, the same argmax); (over_context) an oversized state is refused as
    over-context, the refusal the child abstains on; (usage) the reply's usage counts are non-negative integers;
    (digest_at_exit) the digest is the same at exit as at start. ok is true when every check passes."""
    model = resolve_tag(model)
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        url = _loopback_url(url)
        timeout = float(env.get("MB_S1_TIMEOUT", DEFAULT_TIMEOUT_S))
        live = _live(url, model, timeout)
        if live is None:
            raise RuntimeError(f"{model} is not on the server")
        version = _version(url, timeout)
    except FAILURES as exc:
        return {"model": model, "url": url, "server": None, "digest": None, "ok": False,
                "checks": [{"check": "server", "ok": False, "detail": str(exc)}]}
    child = SystemOneChild(model, url, live["digest"], version, parallel=1, timeout=timeout)
    if pin_file:
        try:
            check_pin(model, live, load_pins(pins_path(env)), allow_unpinned=False)
            check("pin", True, live["digest"])
        except FAILURES as exc:
            check("pin", False, str(exc))

    def probe(name: str, state: str, question: dict[str, Any], keys: Iterable[str]) -> dict[str, float] | None:
        """The probabilities of one question, or None when the child abstains on it (an over-context refusal, as in production)."""
        try:
            return child._one(state, name, question, keys)
        except _Refused as exc:
            if exc.over_context:
                return None
            raise

    questions = _conform_questions()
    failed: dict[str, str] = {}
    unstable: dict[str, str] = {}
    abstained: list[str] = []
    compared = 0  # questions whose two answers were compared
    for name, state, question, keys in questions:
        try:
            first = probe(name, state, question, keys)
            second = probe(name, state, question, keys)
        except (ChildError, OSError, ValueError) as exc:
            failed[name] = str(exc)
            continue
        if first is None and second is None:
            abstained.append(name)
            continue
        compared += 1
        if first is None or second is None or not (
                first.keys() == second.keys() and all(abs(first[k] - second[k]) <= 1e-6 for k in first)
                and _argmax(first) == _argmax(second)):
            unstable[name] = f"two requests gave {first} and {second}"
    answered = len(questions) - len(failed) - len(abstained)
    note = f"; abstains (over context): {', '.join(abstained)}" if abstained else ""
    check("probabilities", not failed, failed or f"{answered} questions answered, probabilities in [0, 1] summing to 1{note}")
    check("determinism", not unstable, unstable or f"{compared} questions compared, the same answer twice")

    try:
        child._ask(OVERSIZE_STATE, {"status": _choice("status")})
        check("over_context", False, "an oversized state was answered, not refused")
    except _Refused as exc:
        check("over_context", exc.over_context, str(exc))
    except FAILURES as exc:
        check("over_context", False, str(exc))

    try:
        reply = _probe(url, model, timeout)
        usage = None if reply is None else reply.get("usage")
        counts = isinstance(usage, dict) and all(type(usage.get(key)) is int and usage[key] >= 0
                                                 for key in ("input_tokens", "output_tokens"))
        check("usage", counts, usage)
    except FAILURES as exc:
        check("usage", False, str(exc))

    try:
        child.verify_digest()
        check("digest_at_exit", True, live["digest"])
    except RuntimeError as exc:  # verify_digest's message names the failure: a mismatch, or "digest_at_exit not verified"
        check("digest_at_exit", False, str(exc))
    return {"model": model, "url": url, "server": live["server"], "digest": live["digest"],
            "ok": all(c["ok"] for c in checks), "checks": checks}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["pin"]:
        parser = argparse.ArgumentParser(prog="python -m mentat_bench.children.systemone_child pin")
        parser.add_argument("tag")
        parser.add_argument("--license", default=None)
        args = parser.parse_args(argv[1:])
        try:
            entry = pin_model(args.tag, args.license, dict(os.environ))
        except FAILURES as exc:
            _refuse(exc)
        print(json.dumps({args.tag: entry}, sort_keys=True))
        return
    if argv[:1] == ["conform"]:
        parser = argparse.ArgumentParser(prog="python -m mentat_bench.children.systemone_child conform")
        parser.add_argument("tag")
        parser.add_argument("--url", default=None, help="the server URL (default MB_S1_URL, else http://127.0.0.1:11434)")
        args = parser.parse_args(argv[1:])
        env = dict(os.environ)
        report = conform(args.tag, args.url or env.get("MB_S1_URL", DEFAULT_URL), env)
        print(json.dumps(report, indent=2, sort_keys=True))
        if not report["ok"]:
            raise SystemExit(1)
        return
    try:
        child = SystemOneChild.from_env()  # a refused pin or an unreachable server: one line, no traceback
    except FAILURES as exc:
        _refuse(exc)
    try:
        serve(child)
    finally:
        print(f"systemone_child: abstentions decide_relation={child.abstentions['decide_relation']} "
              f"decide_group={child.abstentions['decide_group']}; {child.rejected_400} requests refused as invalid or "
              "over-length (HTTP 400)", file=sys.stderr)
    try:
        child.verify_digest()  # a digest changed during the run, or was not verified: the run is refused, after its output
    except RuntimeError as exc:
        _refuse(exc)


def _refuse(exc: BaseException) -> None:
    """One line on stderr, then exit 1: a refused pin or startup, or an exit check that failed."""
    print(f"systemone_child: {exc}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()

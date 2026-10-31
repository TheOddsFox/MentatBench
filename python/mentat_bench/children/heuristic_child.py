"""Deterministic parsing-and-rules baseline (hello name heuristic-rules, kind local).

Generic rules written from the decision protocol alone. The child never reads fixtures or gold, and it does not
use the fixture parsers (fixtures_native, fixtures_gen). Rules:

- bindings: class = normalised question (plus the label for a named outcome). Yes, Over and Up are positive; No,
  Under and Down are negated. A question with a vague time word gives unresolved no_date; a vague source phrase gives
  no_source.
- relations: a bound question with exactly one numeric slot (dates, times and years are not slots) and exactly one
  comparator phrase (or an Over/Under label, which is "above" for the positive side) is a half-line on the real line,
  keyed by its template (the question with the slot and the comparator masked). Comparators: above, over, greater than,
  exceed, at least, or more, at or above, greater than or equal to (and the lower-side counterparts), with strict or
  inclusive endpoints. A question with negation (not, n't, no, never, ...), exactly/between/equal wording other than
  the comparators above, or a second comparator gets no relation: it is an atomic binding only. Two half-lines of one
  template are compared by inclusion and disjointness (sound over the reals). Two propositions of one atomic class are
  EQUIV when they have the same polarity and EXCLUDES when not. Anything else is NONE. The template key is coarse on
  purpose (the comparator is masked); soundness is checked by a property test, not by the key.
- groups: named outcomes of one question form at_most_one only when the question is single-winner ("who will win",
  "which team wins") and not a top-k, qualifying or multiple-outcome question. Half-lines of one template with one
  direction and distinct thresholds form ordered_chain (the stronger proposition first).
- reading rows (source "reading": the proposition, then four options): base is the option sharing the most words with the
  proposition, not counting READING_STOP words, and skipping the option on the other side of a binary market; alt is the
  runner-up, or base when base leads by READING_LEAD words. Each pick gives a scoped pair.
Honest scope: a baseline with modest coverage. It makes no exactly_one claim and infers no bound beyond at_most_one. Its only
scope is the reading axis of reading rows.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .common import binary_label, chain_members, class_key, normalise, word_tokens
from .protocol import serve

NAME = "heuristic-rules"
VERSION = "3"  # version 3: reading rows (version 2: group rules only)
READING_LEAD = 2  # shared words by which the best option leads the runner-up, so that alt repeats base
READING_STOP = frozenset({"will", "the", "a", "an", "of", "in", "by", "be", "to"})  # dropped before the overlap count

VAGUE_TIME = re.compile(
    r"\b(soon|later|eventually|upcoming|recently|currently|for now|this week|next week|this month|next month"
    r"|this year|next year|in the near future|by the end of (?:the )?(?:year|month|week))\b"
)
VAGUE_SOURCE = re.compile(
    r"\b(?:(?:credible|reputable|reliable) (?:sources?|media|outlets?|reports?)|news reports?"
    r"|according to (?:the )?(?:media|news))\b"
)
# Any of these makes the proposition atomic (no numeric relation).
NEGATION = re.compile(r"\b(?:not|no|never|none|neither|nor|without|exactly|between|either)\b|n't\b")
# Comparator phrases as (phrase, direction, strict). Longest first so "no more than" is not read as "more than".
_COMPARATORS = [
    ("no less than", "up", False), ("at least", "up", False), ("at or above", "up", False),
    ("greater than or equal to", "up", False), ("higher than or equal to", "up", False),
    ("or more", "up", False), ("or higher", "up", False), ("minimum of", "up", False),
    ("at most", "down", False), ("at or below", "down", False), ("no more than", "down", False),
    ("less than or equal to", "down", False), ("lower than or equal to", "down", False),
    ("or fewer", "down", False), ("or less", "down", False), ("maximum of", "down", False),
    ("above", "up", True), ("over", "up", True), ("more than", "up", True), ("greater than", "up", True),
    ("higher than", "up", True), ("exceeds", "up", True), ("exceed", "up", True),
    ("below", "down", True), ("under", "down", True), ("less than", "down", True), ("fewer than", "down", True),
    ("lower than", "down", True), ("beneath", "down", True),
]
COMPARATOR = re.compile(
    r"\b(" + "|".join(re.escape(p) for p, _, _ in sorted(_COMPARATORS, key=lambda d: -len(d[0]))) + r")\b"
)
COMPARATOR_OF = {phrase: (direction, strict) for phrase, direction, strict in _COMPARATORS}
MONTH = re.compile(r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
                   r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s*$")
# A number token: 1,234 / 8.5 / 11:40pm / 3am. Times and years are removed below.
NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?(?::\d{2})?(?:\s?(?:am|pm)\b)?")
PLAIN_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
YEAR = re.compile(r"(?:19|20)\d{2}")
OVER_UNDER = ("over", "under")
# Single-winner questions only: a named-outcome set is at most one winner when it asks who wins.
WINNER = re.compile(r"\b(?:win|wins|winner|winners|champion|champions|elected)\b")
NOT_SINGLE_WINNER = re.compile(
    r"\b(?:top|qualif\w*|finish\w*|among|multiple|two|advance\w*|reach\w*|make the|places?|ranks?|ranking|any of)\b"
)


def _binding(ctx: dict[str, Any]) -> dict[str, Any]:
    text = normalise(ctx["question"])
    missing = []
    if VAGUE_TIME.search(text):
        missing.append("no_date")
    if VAGUE_SOURCE.search(text):
        missing.append("no_source")
    if missing:
        return {"status": "unresolved", "class": None, "negated": False, "missing": missing}
    negated = binary_label(ctx["label"])[1]
    return {"status": "bound", "class": class_key(ctx["question"], ctx["label"]), "negated": negated, "missing": []}


def _slot_spans(text: str) -> list[re.Match[str]]:
    """Number tokens that can be a threshold: not a time, a year or a day of a month."""
    slots = []
    for m in NUMBER.finditer(text):
        token = m.group()
        if ":" in token or token.endswith(("am", "pm")) or YEAR.fullmatch(token) or MONTH.search(text[:m.start()]):
            continue
        slots.append(m)
    return slots


def _numeric(ctx: dict[str, Any]) -> tuple[str, tuple[str, float, bool]] | None:
    """(template key, positive half-line) of a binary question with one threshold and one comparator, else None.
    A half-line is (direction, threshold, inclusive): up is x > t or x >= t, down is x < t or x <= t."""
    text = normalise(ctx["question"])
    label = normalise(ctx["label"] or "")
    if not binary_label(ctx["label"])[0]:
        return None
    if NEGATION.search(text):
        return None
    slots = _slot_spans(text)
    phrases = list(COMPARATOR.finditer(text))
    if len(slots) != 1 or len(phrases) > 1:
        return None
    if "equal" in text and not (phrases and "equal" in phrases[0].group(1)):
        return None  # equality wording outside the inclusive comparators: not modelled
    spans: list[tuple[int, int, str]] = [(slots[0].start(), slots[0].end(), "#")]
    if phrases:
        if label in OVER_UNDER:
            return None  # an Over/Under label with a comparator word in the question is ambiguous
        direction, strict = COMPARATOR_OF[phrases[0].group(1)]
        spans.append((phrases[0].start(), phrases[0].end(), "@"))
    elif label in OVER_UNDER:
        direction, strict = "up", True  # Over is the positive side of an Over/Under line; Under is its negation
    else:
        return None
    if not PLAIN_NUMBER.fullmatch(slots[0].group()):
        return None
    template, last = [], 0
    for start, end, mark in sorted(spans):
        template.append(text[last:start])
        template.append(mark)
        last = end
    template.append(text[last:])
    value = float(slots[0].group().replace(",", ""))
    return "t:" + "".join(template), (direction, value, not strict)


def _proposition(ctx: dict[str, Any]) -> tuple[str, tuple[str, float, bool] | None, bool] | None:
    """(key, positive half-line or None for an atomic class, negated) of a bound outcome; None if unresolved."""
    decision = _binding(ctx)
    if decision["status"] != "bound":
        return None
    numeric = _numeric(ctx)
    if numeric is None:
        return "a:" + decision["class"], None, decision["negated"]
    return numeric[0], numeric[1], decision["negated"]


def _complement(line: tuple[str, float, bool]) -> tuple[str, float, bool]:
    direction, t, inclusive = line
    return ("down" if direction == "up" else "up"), t, not inclusive


def _subset(a: tuple[str, float, bool], b: tuple[str, float, bool]) -> bool:
    """Every point of half-line a is in half-line b."""
    (da, ta, ia), (db, tb, ib) = a, b
    if da != db:
        return False
    if da == "up":
        return ta > tb or (ta == tb and (ib or not ia))
    return ta < tb or (ta == tb and (ib or not ia))


def _disjoint(a: tuple[str, float, bool], b: tuple[str, float, bool]) -> bool:
    """The two half-lines share no point (an up and a down line; two lines of one direction always overlap)."""
    if a[0] == b[0]:
        return False
    (_, tu, iu), (_, td, idn) = (a, b) if a[0] == "up" else (b, a)
    return tu > td or (tu == td and not (iu and idn))


def relation_of(a: dict[str, Any], b: dict[str, Any]) -> str:
    """The relation of proposition a to proposition b (IMPLIES: a => b), or NONE/UNRESOLVED when no rule applies."""
    pa, pb = _proposition(a), _proposition(b)
    if pa is None or pb is None:
        return "UNRESOLVED"
    (ka, la, na), (kb, lb, nb) = pa, pb
    if ka != kb:
        return "NONE"
    if la is None:
        return "EQUIV" if na == nb else "EXCLUDES"
    line_a = _complement(la) if na else la
    line_b = _complement(lb) if nb else lb
    if line_a == line_b:
        return "EQUIV"
    if _subset(line_a, line_b):
        return "IMPLIES"
    if _subset(line_b, line_a):
        return "IMPLIED_BY"
    if _disjoint(line_a, line_b):
        return "EXCLUDES"
    return "NONE"


def _single_winner(question: str) -> bool:
    text = normalise(question)
    return bool(WINNER.search(text)) and not NOT_SINGLE_WINNER.search(text)


def _content_words(text: str) -> set[str]:
    return set(word_tokens(text)) - READING_STOP


def _opposite_sides(u: dict[str, Any], o: dict[str, Any]) -> bool:
    """True when u and the option are the two sides of one binary market (Yes against No): the option says the negation of u,
    so it is skipped, whatever its words."""
    (binary_u, negated_u), (binary_o, negated_o) = binary_label(u["label"]), binary_label(o["label"])
    return binary_u and binary_o and negated_u != negated_o


def _reading(ctx: dict[str, Any]) -> dict[str, Any]:
    """A reading row (members: u, then its options). base is the option sharing the most words with u (the question texts);
    alt is the runner-up, or base again when base leads by READING_LEAD words. The overlap counts content words (READING_STOP
    is dropped), and an option on the other side of u's binary market is skipped. No shared word is no pick, and no option
    left to pick is no group. A pick c gives the pair [u, c] and [c, u] under that reading's scope."""
    u, options = ctx["members"][0], ctx["members"][1:]
    pool = [i for i, o in enumerate(options) if not _opposite_sides(u, o)]
    if not pool:
        return {"groups": []}
    words = _content_words(u["question"])
    shared = {i: len(words & _content_words(options[i]["question"])) for i in pool}
    order = sorted(pool, key=lambda i: (-shared[i], i))  # a tie keeps the listed order
    best = order[0]
    runner = order[1] if len(order) > 1 else best
    picks: dict[str, int] = {}
    if shared[best] > 0:
        picks["base"] = best
        if shared[best] - shared[runner] >= READING_LEAD:
            picks["alt"] = best
        elif shared[runner] > 0:
            picks["alt"] = runner
    groups: list[dict[str, Any]] = []
    for reading, i in picks.items():
        option = options[i]["id"]
        scope = {"axis": "reading", "values": [reading], "of": ["alt", "base"]}
        for pair in ([u["id"], option], [option, u["id"]]):
            groups.append({"kind": "ordered_chain", "members": pair, "direction": "forward", "scope": scope})
    return {"groups": groups}


def _group(ctx: dict[str, Any]) -> dict[str, Any]:
    if ctx["source"] == "reading":
        return _reading(ctx)
    members = chain_members(ctx["members"])  # one outcome per binary market, the positive side first (common.chain_members)
    props = {m["id"]: _proposition(m) for m in members}
    groups: list[dict[str, Any]] = []
    named: dict[str, list[str]] = defaultdict(list)
    for m in members:
        if props[m["id"]] is not None and not binary_label(m["label"])[0] and _single_winner(m["question"]):
            named[normalise(m["question"])].append(m["id"])
    for ids in named.values():
        if len(ids) >= 2:
            groups.append({"kind": "at_most_one", "members": ids, "scope": None})
    ladders: dict[str, list[tuple[str, tuple[str, float, bool]]]] = defaultdict(list)
    for mid, prop in props.items():
        if prop is None or prop[1] is None:
            continue
        key, line, negated = prop
        ladders[key].append((mid, _complement(line) if negated else line))
    for rows in ladders.values():
        directions = {line[0] for _, line in rows}
        thresholds = [line[1] for _, line in rows]
        if len(rows) < 2 or len(directions) != 1 or len(set(thresholds)) != len(thresholds):
            continue
        rows.sort(key=lambda row: row[1][1], reverse=directions == {"up"})
        groups.append({"kind": "ordered_chain", "members": [mid for mid, _ in rows], "direction": "forward",
                       "scope": None})
    return {"groups": groups}


class HeuristicChild:
    def describe(self) -> dict[str, Any]:
        return {"name": NAME, "version": VERSION, "model": {"id": NAME, "kind": "local"}}

    def decide(self, op: str, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if op == "decide_binding":
            results = [_binding(item["ctx"]) for item in items]
        elif op == "decide_relation":
            results = [{"relation": relation_of(item["a"], item["b"])} for item in items]
        else:
            results = [_group(item["ctx"]) for item in items]
        return results, {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "model": NAME, "kind": "local"}


def main() -> None:
    serve(HeuristicChild())


if __name__ == "__main__":
    main()

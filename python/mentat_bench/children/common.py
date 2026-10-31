"""Text helpers shared by the reference children: normalisation, label semantics and stable class keys."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from typing import Any

POSITIVE_LABELS = frozenset({"yes", "over", "up"})
NEGATIVE_LABELS = frozenset({"no", "under", "down"})
# Words that can flip or bound a proposition. The text helpers below use them for the conservative polarity guard.
POLARITY_TOKENS = frozenset({
    "not", "no", "never", "without", "neither", "nor", "before", "after", "above", "below", "over", "under", "more",
    "less", "higher", "lower", "win", "wins", "won", "lose", "loses", "lost", "yes", "up", "down", "greater", "fewer",
    "exceed", "exceeds", "first", "last",
})
POLARITY_PHRASES = frozenset({("at", "least"), ("at", "most"), ("or", "more"), ("or", "less")})
WORD = re.compile(r"\w+")


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def binary_label(label: str | None) -> tuple[bool, bool]:
    """(is a binary label, negated). Yes, Over and Up are the positive side of a market; No, Under and Down the
    negative side. Any other label is a named outcome of a multi-outcome market."""
    key = normalise(label or "")
    if key in POSITIVE_LABELS:
        return True, False
    if key in NEGATIVE_LABELS:
        return True, True
    return False, False


def class_key(question: str, label: str | None) -> str:
    """Stable class of a proposition: the normalised question, so both sides of a binary market share it. A named
    outcome also keys on its label. A SHA-256 prefix, equal in every process."""
    text = normalise(question)
    if not binary_label(label)[0]:
        text += "\x1f" + normalise(label or "")
    return "c" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def word_tokens(text: str) -> list[str]:
    """Word tokens of the normalised text, in order. The contraction n't reads as "not" (don't -> do not)."""
    text = normalise(text).replace("n't", " not").replace("n’t", " not")
    return WORD.findall(text)


def number_tokens(tokens: list[str]) -> frozenset[str]:
    """The tokens that contain a digit (1, 25, 2026, 12pm)."""
    return frozenset(t for t in tokens if any(c.isdigit() for c in t))


def polarity_clash(a: list[str], b: list[str]) -> bool:
    """True when the two token lists differ by a polarity-bearing token or phrase: the symmetric difference of their
    token multisets (or of their adjacent-pair multisets, for the phrases) contains one. Conservative on purpose: any
    such difference blocks an equivalence claim, so "will X" and "will X not" never count as equal."""
    diff = (Counter(a) - Counter(b)) + (Counter(b) - Counter(a))
    if any(t in POLARITY_TOKENS for t in diff):
        return True
    pairs_a, pairs_b = Counter(zip(a, a[1:])), Counter(zip(b, b[1:]))
    return any(p in POLARITY_PHRASES for p in (pairs_a - pairs_b) + (pairs_b - pairs_a))


def chain_members(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The outcomes an ordered_chain may name: one per binary market (its positive side, Yes, Over or Up, when the set
    holds it, else the side that is there; both sides of a market share their class), and every named outcome. Corpus
    order is kept, so a chain never holds both sides of one market."""
    best: dict[str, dict[str, Any]] = {}
    for m in members:
        is_binary, negated = binary_label(m["label"])
        if is_binary:
            key = class_key(m["question"], m["label"])
            if key not in best or (not negated and binary_label(best[key]["label"])[1]):
                best[key] = m
    keep = {m["id"] for m in best.values()}
    return [m for m in members if not binary_label(m["label"])[0] or m["id"] in keep]

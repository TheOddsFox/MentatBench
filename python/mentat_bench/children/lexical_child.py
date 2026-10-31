"""Lexical-retrieval baseline (hello name lexical-retrieval, kind local): text overlap and nothing else.

Deliberately weak. Generic rules, no fixture parsing and no gold:

- bindings: every outcome is bound. class = common.class_key(question, label) and negated from the label
  (common.binary_label: Yes/Over/Up positive, No/Under/Down negated). Vague-time and vague-source codes are not used.
- relations: EQUIV only when both sides name the same claim (lexical_child.same_claim: the same polarity of one binary
  market, or the same normalised label of a named outcome), no polarity-bearing word or phrase differs between the
  questions (common.polarity_clash: not, above/below, over/under, before/after, win/lose, ...; conservative), their
  numeric tokens are equal (may_be_equiv), and the token-set Jaccard of their questions is at least JACCARD. NONE
  otherwise. Token overlap carries no word order, so "A beats B"
  and "B beats A" are EQUIV here (a known false equivalence, left in on purpose). No IDF or BM25 term weights:
  the child answers one pair at a time and sees no corpus statistics.
- groups: none. decide_group answers [] (the build also runs with --no-groups for this child).

IMPLIES, IMPLIED_BY, EXCLUDES and UNRESOLVED are never emitted: lexical overlap cannot derive them. Its gain over the empty
baseline comes from binding every outcome and from EQUIV on identical claims (the build derives the complement relations
of those EQUIV pairs itself). JACCARD is a fixed constant chosen before any run; it is not fitted to gold.
"""

from __future__ import annotations

from typing import Any

from .common import binary_label, class_key, normalise, number_tokens, polarity_clash, word_tokens
from .protocol import serve

NAME = "lexical-retrieval"
VERSION = "1"
JACCARD = 0.9
USAGE = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "model": NAME, "kind": "local"}


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def claim_key(ctx: dict[str, Any]) -> tuple[str, bool, str]:
    """A binary market side keys on its polarity; a named outcome keys on its normalised label. Two contexts can be
    EQUIV only with equal keys, so opposite sides of one market (Yes and No) are never EQUIV."""
    binary, negated = binary_label(ctx["label"])
    return ("side", negated, "") if binary else ("named", False, normalise(ctx["label"] or ""))


def same_claim(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return claim_key(a) == claim_key(b)


def binding(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"status": "bound", "class": class_key(ctx["question"], ctx["label"]),
            "negated": binary_label(ctx["label"])[1], "missing": []}


def may_be_equiv(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """The gate both children apply before any similarity: one claim, no polarity-bearing word or phrase that differs
    between the questions (common.polarity_clash), and equal numbers."""
    if not same_claim(a, b):
        return False
    wa, wb = word_tokens(a["question"]), word_tokens(b["question"])
    return not polarity_clash(wa, wb) and number_tokens(wa) == number_tokens(wb)


def relation(a: dict[str, Any], b: dict[str, Any]) -> str:
    if not may_be_equiv(a, b):
        return "NONE"
    overlap = jaccard(frozenset(word_tokens(a["question"])), frozenset(word_tokens(b["question"])))
    return "EQUIV" if overlap >= JACCARD else "NONE"


class LexicalChild:
    def describe(self) -> dict[str, Any]:
        return {"name": NAME, "version": VERSION, "model": {"id": NAME, "kind": "local"}}

    def decide(self, op: str, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if op == "decide_binding":
            results = [binding(item["ctx"]) for item in items]
        elif op == "decide_relation":
            results = [{"relation": relation(item["a"], item["b"])} for item in items]
        else:
            results = [{"groups": []} for _ in items]
        return results, dict(USAGE)


def main() -> None:
    serve(LexicalChild())


if __name__ == "__main__":
    main()

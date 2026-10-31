"""Lexical-retrieval child: bindings by normalised text, EQUIV only for one claim with token overlap >= 0.9 and equal
numbers, never IMPLIES, EXCLUDES or UNRESOLVED, no groups. Also the hello/batch protocol and the conformance runner."""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from mentat_bench.adapters.base import check_binding, check_group, check_relation
from mentat_bench.children import lexical_child as L
from mentat_bench.children.common import number_tokens, word_tokens
from mentat_bench.children.protocol import MAX_CALLS, handle

PYTHON_DIR = Path(__file__).resolve().parents[1] / "python"
QUESTION = "Will Example Coin close above 100 on June 1, 12PM ET?"


def ctx(oid: str, question: str, label: str | None, title: str | None = None) -> dict:
    return {"id": oid, "question": question, "label": label, "event_title": title}


def test_binary_sides_share_a_class_and_differ_in_negation() -> None:
    yes = L.binding(ctx("a", QUESTION, "Yes"))
    no = L.binding(ctx("b", QUESTION, "No"))
    assert check_binding(yes) == {"status": "bound", "class": yes["class"], "negated": False, "missing": []}
    assert no["class"] == yes["class"] and no["negated"] is True


def test_complement_labels_follow_the_shared_label_semantics() -> None:
    up = L.binding(ctx("a", "Solana Up or Down - August 21, 11:40PM ET", "Up"))
    down = L.binding(ctx("b", "Solana Up or Down - August 21, 11:40PM ET", "Down"))
    over = L.binding(ctx("c", "O/U 8.5 Total Corners", "Over"))
    under = L.binding(ctx("d", "O/U 8.5 Total Corners", "Under"))
    assert (up["negated"], down["negated"], up["class"]) == (False, True, down["class"])
    assert (over["negated"], under["negated"], over["class"]) == (False, True, under["class"])


def test_named_outcomes_get_distinct_classes_and_no_vague_codes() -> None:
    a = L.binding(ctx("a", "Who wins the Example Cup?", "Alpha"))
    b = L.binding(ctx("b", "Who wins the Example Cup?", "Beta"))
    assert a["class"] != b["class"] and a["negated"] is False and b["negated"] is False
    assert L.binding(ctx("c", "Will Example Coin rise soon?", "Yes"))["status"] == "bound"  # no vague-word rules


def test_identical_claims_are_equiv_and_opposite_sides_are_not() -> None:
    assert L.relation(ctx("a", QUESTION, "Yes"), ctx("b", QUESTION, "Yes")) == "EQUIV"
    assert L.relation(ctx("a", QUESTION, "No"), ctx("b", QUESTION, "No")) == "EQUIV"
    assert L.relation(ctx("a", QUESTION, "Yes"), ctx("b", QUESTION, "No")) == "NONE"  # never EXCLUDES


def test_numeric_tokens_must_be_equal() -> None:
    a = ctx("a", "Will Example Coin close above 100 on June 1?", "Yes")
    b = ctx("b", "Will Example Coin close above 120 on June 1?", "Yes")
    assert L.relation(a, b) == "NONE"  # the token sets differ only by a number: Jaccard is high, the numbers differ


def test_jaccard_threshold_is_inclusive_at_point_nine() -> None:
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
    full = ctx("a", " ".join(words), "Yes")
    assert L.jaccard(frozenset(words), frozenset(words[:9])) == 0.9
    assert L.relation(full, ctx("b", " ".join(words[:9]), "Yes")) == "EQUIV"
    assert L.relation(full, ctx("c", " ".join(words[:8]), "Yes")) == "NONE"


def test_named_outcomes_need_the_same_label() -> None:
    question = "Who wins the Example Cup this year in the long running tournament series?"
    assert L.relation(ctx("a", question, "Alpha"), ctx("b", question, "Alpha")) == "EQUIV"
    assert L.relation(ctx("a", question, "Alpha"), ctx("b", question, "Beta")) == "NONE"
    assert L.relation(ctx("a", question, "Alpha"), ctx("b", question, "Yes")) == "NONE"


def test_only_equiv_or_none_is_ever_emitted() -> None:
    rng = random.Random(7)
    words = ["alpha", "beta", "gamma", "100", "120", "above", "win", "soon", "not"]
    labels = ["Yes", "No", "Up", "Down", "Over", "Under", "Alpha", "Beta", None]

    def random_ctx(i: int) -> dict:
        return ctx(f"x{i}", " ".join(rng.choice(words) for _ in range(rng.randint(2, 7))), rng.choice(labels))

    for i in range(500):
        relation = L.relation(random_ctx(2 * i), random_ctx(2 * i + 1))
        assert relation in ("EQUIV", "NONE")
        check_relation(relation)


def test_protocol_replies_are_valid_and_groups_are_empty() -> None:
    child = L.LexicalChild()
    hello = json.loads(json.dumps(handle(child, json.dumps({"op": "hello", "protocol": 2, "id": 1}))))
    assert hello["name"] == L.NAME and hello["model"]["kind"] == "local" and hello["id"] == 1
    group = {"group_id": "g1", "source": "s1", "members": [ctx("m1", "Who wins?", "Alpha"), ctx("m2", "Who wins?", "Beta")]}
    reply = json.loads(json.dumps(handle(child, json.dumps({"op": "decide_group", "ctx": group}))))
    assert reply["groups"] == [] and check_group({"kind": "at_most_one", "members": ["m1", "m2"]}, {"m1", "m2"})["kind"]
    calls = [{"op": "decide_relation", "a": ctx("a", QUESTION, "Yes"), "b": ctx("b", QUESTION, "Yes")},
             {"op": "decide_binding", "ctx": ctx("c", QUESTION, "No")}, {"op": "decide_binding", "ctx": {"id": "", "question": "x"}}]
    batch = json.loads(json.dumps(handle(child, json.dumps({"op": "batch", "calls": calls}))))
    assert len(batch["results"]) == len(calls) <= MAX_CALLS
    assert batch["results"][0]["relation"] == "EQUIV" and batch["results"][1]["status"] == "bound"
    assert "error" in batch["results"][2]  # a malformed item is an isolated error


def test_conformance_runner_passes_on_the_lexical_child() -> None:
    env = {**os.environ, "PYTHONPATH": str(PYTHON_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    done = subprocess.run([sys.executable, "-m", "mentat_bench.children.conformance", sys.executable, "-m",
                           "mentat_bench.children.lexical_child"], env=env, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stdout + done.stderr


# Polarity guard (review M9): each pair shares nearly every word, so token overlap alone would reach the threshold.
BASE = "Will the Fed announce a cut to its policy rate by 25 basis points at the March meeting in Washington this year"
SUFFIX = " according to the official schedule published by the committee for the quarter"
ABOVE = "Will the policy rate of the Fed be above 4 percent at the March meeting in Washington this year" + SUFFIX
OVER = "Will the total goals in the Example Cup final be over 2 at the stadium in Washington this year" + SUFFIX
BEFORE = "Will the Example Cup final be decided before the second half of the match in Washington this year" + SUFFIX
WIN = "Will the home team win the Example Cup final match held at the stadium in Washington this year" + SUFFIX
AT_LEAST = "Will the home side finish with at least three points in the group stage at the stadium in Washington" + SUFFIX
POLARITY_PAIRS = [
    ("not", BASE, BASE.replace("Will the Fed announce", "Will the Fed not announce")),
    ("n't", BASE, BASE.replace("Will the Fed announce", "Will the Fed don't announce")),
    ("never", BASE, BASE.replace("Will the Fed announce", "Will the Fed never announce")),
    ("above/below", ABOVE, ABOVE.replace("above", "below")),
    ("over/under", OVER, OVER.replace(" over ", " under ")),
    ("before/after", BEFORE, BEFORE.replace("before", "after")),
    ("win/lose", WIN, WIN.replace(" win ", " lose ")),
    ("at least/at most", AT_LEAST, AT_LEAST.replace("at least", "at most")),
]


@pytest.mark.parametrize("name,a,b", POLARITY_PAIRS, ids=[p[0] for p in POLARITY_PAIRS])
def test_polarity_words_block_equiv_even_when_overlap_passes(name: str, a: str, b: str) -> None:
    ta, tb = word_tokens(a), word_tokens(b)
    assert L.jaccard(frozenset(ta), frozenset(tb)) >= L.JACCARD  # precondition: overlap alone would say EQUIV
    assert number_tokens(ta) == number_tokens(tb)
    assert L.relation(ctx("a", a, "Yes"), ctx("b", b, "Yes")) == "NONE"
    assert L.relation(ctx("b", b, "Yes"), ctx("a", a, "Yes")) == "NONE"


def test_a_difference_without_polarity_words_is_still_equiv() -> None:
    assert L.relation(ctx("a", BASE, "Yes"), ctx("b", BASE.lower(), "Yes")) == "EQUIV"  # normalisation only
    assert L.relation(ctx("a", BASE, "Yes"), ctx("b", BASE.replace("announce", "signal"), "Yes")) == "EQUIV"

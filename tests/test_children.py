"""Reference children: heuristic rules, the JSON-lines protocol (hello, single ops, batch envelope), the conformance
runner and the local adapter talking to the heuristic child. No model, no download, no gold."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mentat_bench.adapters.base import check_binding, check_group, check_relation
from mentat_bench.adapters.local_jev import LocalJSONLAdapter
from mentat_bench.children import heuristic_child as H
from mentat_bench.children.common import chain_members
from mentat_bench.children.protocol import MAX_BATCH, MAX_CALLS, handle

PYTHON_DIR = Path(__file__).resolve().parents[1] / "python"


def ctx(oid: str, question: str, label: str | None, title: str | None = None) -> dict:
    return {"id": oid, "question": question, "label": label, "event_title": title}


def child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(PYTHON_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")}


@pytest.fixture()
def child() -> H.HeuristicChild:
    return H.HeuristicChild()


def call(child, body: dict) -> dict:
    return json.loads(json.dumps(handle(child, json.dumps(body))))


# Heuristic rules

def test_binary_pair_shares_class_and_negation_by_label() -> None:
    yes = H._binding(ctx("a", "Bitcoin above 75,000 on May 29, 10AM ET?", "Yes"))
    no = H._binding(ctx("b", "Bitcoin above 75,000 on May 29, 10AM ET?", "No"))
    assert check_binding(yes)["status"] == "bound" and yes["negated"] is False
    assert no["negated"] is True and no["class"] == yes["class"]


def test_up_down_and_over_under_are_complementary_sides() -> None:
    up = H._binding(ctx("a", "Solana Up or Down - August 21, 11:40PM-11:45PM ET", "Up"))
    down = H._binding(ctx("b", "Solana Up or Down - August 21, 11:40PM-11:45PM ET", "Down"))
    over = H._binding(ctx("c", "O/U 8.5 Total Corners", "Over"))
    under = H._binding(ctx("d", "O/U 8.5 Total Corners", "Under"))
    assert (up["negated"], down["negated"], up["class"]) == (False, True, down["class"])
    assert (over["negated"], under["negated"], over["class"]) == (False, True, under["class"])


def test_named_outcomes_get_distinct_classes() -> None:
    a = H._binding(ctx("a", "Who wins the Example Cup?", "Alpha"))
    b = H._binding(ctx("b", "Who wins the Example Cup?", "Beta"))
    assert a["class"] != b["class"] and a["negated"] is False and b["negated"] is False


def test_vague_time_and_source_are_unresolved_with_codes() -> None:
    soon = H._binding(ctx("a", "Will Example Coin rise soon?", "Yes"))
    assert soon == {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}
    both = H._binding(ctx("b", "Will the credible media report a win this week?", "Yes"))
    assert both["missing"] == ["no_date", "no_source"]
    assert check_binding(both)["status"] == "unresolved"


def test_threshold_relations_follow_half_lines() -> None:
    def rel(q1: str, l1: str, q2: str, l2: str) -> str:
        return H.relation_of(ctx("a", q1, l1), ctx("b", q2, l2))

    q = "Bitcoin above {} on July 8, 3AM ET?"
    assert rel(q.format("64,000"), "Yes", q.format("62,800"), "Yes") == "IMPLIES"
    assert rel(q.format("62,800"), "Yes", q.format("64,000"), "Yes") == "IMPLIED_BY"
    assert rel(q.format("62,800"), "Yes", q.format("62,800"), "No") == "EXCLUDES"
    assert rel(q.format("62,800"), "No", q.format("62,800"), "No") == "EQUIV"
    below = "Bitcoin below 62,800 on July 8, 3AM ET?"
    assert rel(q.format("62,800"), "Yes", below, "Yes") == "EXCLUDES"
    assert rel(q.format("62,800"), "No", below, "No") == "NONE"


def test_direction_words_and_over_under_relations() -> None:
    assert H.relation_of(ctx("a", "Will X score at least 8 points in the 2026 season?", "Yes"),
                       ctx("b", "Will X score at least 3 points in the 2026 season?", "Yes")) == "IMPLIES"
    assert H.relation_of(ctx("a", "Will X finish with 7 or more points in the 2026 season?", "Yes"),
                       ctx("b", "Will X finish with 7 or more points in the 2026 season?", "No")) == "EXCLUDES"
    assert H.relation_of(ctx("a", "Will X finish with 2 or fewer points in the 2026 season?", "Yes"),
                       ctx("b", "Will X finish with 3 or more points in the 2026 season?", "Yes")) == "EXCLUDES"
    assert H.relation_of(ctx("a", "O/U 8.5 Total Corners", "Over"), ctx("b", "O/U 7.5 Total Corners", "Over")) == "IMPLIES"
    assert H.relation_of(ctx("a", "O/U 8.5 Total Corners", "Under"), ctx("b", "O/U 8.5 Total Corners", "Over")) == "EXCLUDES"


def test_relation_unresolved_and_atomic_cases() -> None:
    assert H.relation_of(ctx("a", "Will Ember win soon?", "Yes"), ctx("b", "Will Ember win?", "Yes")) == "UNRESOLVED"
    assert H.relation_of(ctx("a", "Will Ember win?", "Yes"), ctx("b", "Will Ember win?", "No")) == "EXCLUDES"
    assert H.relation_of(ctx("a", "Will Ember win?", "Yes"), ctx("b", "Will Ash win?", "Yes")) == "NONE"


def test_half_line_comparisons_are_sound_over_reals() -> None:
    assert H._subset(("up", 5.0, False), ("up", 5.0, True))  # x > 5 implies x >= 5
    assert not H._subset(("up", 5.0, True), ("up", 5.0, False))
    assert H._disjoint(("up", 5.0, False), ("down", 5.0, True))  # x > 5 and x <= 5
    assert not H._disjoint(("up", 5.0, True), ("down", 5.0, True))  # both hold at 5


def test_groups_named_outcomes_and_ladders() -> None:
    members = [
        ctx("m1", "Who wins the Example Cup?", "Alpha"),
        ctx("m2", "Who wins the Example Cup?", "Beta"),
        ctx("m3", "Will X score at least 8 points in the 2026 season?", "Yes"),
        ctx("m4", "Will X score at least 3 points in the 2026 season?", "Yes"),
        ctx("m5", "Will X score at least 5 points in the 2026 season?", "Yes"),
    ]
    decisions = H._group({"group_id": "c1", "source": "s2", "members": members})["groups"]
    kinds = {d["kind"]: d for d in decisions}
    assert kinds["at_most_one"]["members"] == ["m1", "m2"]
    assert kinds["ordered_chain"]["members"] == ["m3", "m5", "m4"]
    member_ids = {m["id"] for m in members}
    for decision in decisions:
        check_group(decision, member_ids)


# Negation, equality and comparator soundness (review findings H1)

def test_negated_question_gives_no_numeric_relation() -> None:
    """(b) "not exceed 30" must not be read as x > 30: the two negated questions are atomic, so NONE."""
    assert H.relation_of(ctx("a", "Will X not exceed 30?", "Yes"), ctx("b", "Will X not exceed 25?", "Yes")) == "NONE"
    assert H._numeric(ctx("a", "Will X not exceed 30?", "Yes")) is None


def test_inclusive_and_strict_comparators_do_not_overlap_wrongly() -> None:
    """(a) "greater than or equal to 30" and "less than or equal to 30" overlap at 30: not EXCLUDES."""
    up = ctx("a", "Will X be greater than or equal to 30?", "Yes")
    down = ctx("b", "Will X be less than or equal to 30?", "Yes")
    assert H.relation_of(up, down) == "NONE"
    assert H.relation_of(up, ctx("c", "Will X be greater than 30?", "Yes")) == "IMPLIED_BY"
    assert H.relation_of(ctx("d", "Will X be less than 30?", "Yes"), down) == "IMPLIES"


def test_equality_or_second_comparator_outside_the_table_gets_no_relation() -> None:
    assert H._numeric(ctx("a", "Will X equal 30?", "Yes")) is None
    assert H._numeric(ctx("a", "Will X be above 30 and below 40?", "Yes")) is None
    assert H._numeric(ctx("a", "Will X be exactly 30?", "Yes")) is None


COMPARATORS = {
    "above": lambda x, t: x > t, "greater than": lambda x, t: x > t, "exceed": lambda x, t: x > t,
    "at least": lambda x, t: x >= t, "at or above": lambda x, t: x >= t,
    "greater than or equal to": lambda x, t: x >= t,
    "below": lambda x, t: x < t, "less than": lambda x, t: x < t,
    "at most": lambda x, t: x <= t, "at or below": lambda x, t: x <= t,
    "less than or equal to": lambda x, t: x <= t,
}


def test_property_claimed_relations_are_sound_on_a_dense_grid() -> None:
    """For generated single-template pairs (comparators > >= < <= in words, integer thresholds, Yes/No labels), every
    relation the rules claim holds on a dense real grid: IMPLIES, IMPLIED_BY, EQUIV and EXCLUDES are checked directly."""
    import random

    rng = random.Random(20260518)
    grid = [k / 8 for k in range(-8, 8 * 22)]  # -1 .. 22 in steps of 1/8: thresholds and their neighbours included
    phrases = list(COMPARATORS)
    claimed: dict[str, int] = {}
    for n in range(400):
        pa, pb = rng.choice(phrases), rng.choice(phrases)
        ta, tb = rng.randint(0, 20), rng.randint(0, 20)
        la, lb = rng.choice(["Yes", "No"]), rng.choice(["Yes", "No"])
        a = ctx(f"a{n}", f"Will Bitcoin be {pa} {ta} on July 8, 3AM ET?", la)
        b = ctx(f"b{n}", f"Will Bitcoin be {pb} {tb} on July 8, 3AM ET?", lb)
        rel = H.relation_of(a, b)
        claimed[rel] = claimed.get(rel, 0) + 1
        fa = [COMPARATORS[pa](x, ta) == (la == "Yes") for x in grid]
        fb = [COMPARATORS[pb](x, tb) == (lb == "Yes") for x in grid]
        if rel == "IMPLIES":
            assert all(not x or y for x, y in zip(fa, fb)), (a, b)
        elif rel == "IMPLIED_BY":
            assert all(not y or x for x, y in zip(fa, fb)), (a, b)
        elif rel == "EQUIV":
            assert fa == fb, (a, b)
        elif rel == "EXCLUDES":
            assert not any(x and y for x, y in zip(fa, fb)), (a, b)
    assert claimed.get("EXCLUDES", 0) > 0 and claimed.get("IMPLIES", 0) + claimed.get("IMPLIED_BY", 0) > 0


# Groups: single-winner named sets only (review finding M3)

def test_at_most_one_only_for_single_winner_questions() -> None:
    def groups_for(question: str) -> list[str]:
        members = [ctx("m1", question, "Alpha"), ctx("m2", question, "Beta")]
        return [g["kind"] for g in H._group({"group_id": "c", "source": "s1", "members": members})["groups"]]

    assert groups_for("Who will win the Example Cup?") == ["at_most_one"]
    assert groups_for("Which team wins the Example Cup?") == ["at_most_one"]
    assert groups_for("Who will finish in the top 2?") == []
    assert groups_for("Which two teams will qualify?") == []
    assert groups_for("Will any of these teams win?") == []
    assert groups_for("Which city hosts the fair?") == []


# Reading rows (source "reading": the proposition, then its options)

def reading_row(question: str, options: list[str]) -> dict:
    members = [ctx("u", question, "Yes")] + [ctx(f"o{i}", q, "Yes") for i, q in enumerate(options)]
    return {"group_id": "r", "source": "reading", "members": members}


def reading_pairs(option: str, reading: str) -> list[dict]:
    scope = {"axis": "reading", "values": [reading], "of": ["alt", "base"]}
    return [{"kind": "ordered_chain", "members": pair, "direction": "forward", "scope": scope}
            for pair in (["u", option], [option, "u"])]


def test_reading_base_is_the_most_shared_words_and_alt_the_runner_up(child) -> None:
    row = reading_row("Will Alpha win the cup?", ["Will Alpha win the league?", "Will Beta win the cup?", "Will Gamma lose?",
                                                  "Will Delta sign?"])
    reply = call(child, {"op": "decide_group", "ctx": row})  # a tie of 4 shared words: the listed order picks base
    assert reply["groups"] == reading_pairs("o0", "base") + reading_pairs("o1", "alt")
    member_ids = {m["id"] for m in row["members"]}
    for decision in reply["groups"]:
        check_group(decision, member_ids)


def test_reading_base_leading_by_a_margin_repeats_as_alt(child) -> None:
    row = reading_row("Will Alpha win the cup?", ["Will Alpha win the cup outright?", "Will Beta lose?", "Will Gamma sign?",
                                                  "Delta?"])
    assert child.decide("decide_group", [{"ctx": row}])[0][0]["groups"] == (
        reading_pairs("o0", "base") + reading_pairs("o0", "alt"))


def test_reading_runner_up_sharing_no_word_gives_no_alt(child) -> None:
    row = reading_row("Will Alpha win?", ["Alpha wins", "Beta sign", "Gamma lose"])
    assert child.decide("decide_group", [{"ctx": row}])[0][0]["groups"] == reading_pairs("o0", "base")


def test_reading_with_no_shared_word_has_no_pick(child) -> None:
    row = reading_row("Will Alpha win?", ["Beta sign", "Gamma lose", "Delta fall", "Epsilon rise"])
    assert child.decide("decide_group", [{"ctx": row}])[0][0]["groups"] == []


def test_reading_stop_words_do_not_count_as_overlap(child) -> None:
    row = reading_row("Will the cat be in a box?", ["Will a dog be in the house?", "Will the bird fly?"])
    assert child.decide("decide_group", [{"ctx": row}])[0][0]["groups"] == []  # only "will", "the", "be", "in", "a" are shared


def test_reading_skips_the_option_on_the_other_side_of_the_market(child) -> None:
    row = reading_row("Will Alpha win the cup?", ["Will Alpha win the cup?", "Will Beta lose?", "Will Gamma sign?", "Delta?"])
    row["members"][1] = ctx("o0", "Will Alpha win the cup?", "No")  # the No side of u's own market: skipped, whatever its words
    row["members"][2] = ctx("o1", "Will Alpha win the cup later?", "Yes")  # the same side: picked
    assert child.decide("decide_group", [{"ctx": row}])[0][0]["groups"] == reading_pairs("o1", "base") + reading_pairs("o1", "alt")


def test_reading_without_options_gives_no_group() -> None:
    assert H._reading({"members": [ctx("u", "Will Alpha win?", "Yes")]}) == {"groups": []}


# Usage and protocol envelopes (review findings M4, L1)

def test_heuristic_usage_names_the_model(child) -> None:
    _, usage = child.decide("decide_binding", [{"ctx": ctx("a", "Will Ember win?", "Yes")}])
    assert usage["model"] == "heuristic-rules" and usage["kind"] == "local"
    reply = call(child, {"op": "batch", "id": 1, "calls": [{"op": "decide_binding", "ctx": ctx("a", "Will Ember win?", "Yes")}]})
    assert reply["model"] == "heuristic-rules"


class _HalfFailingChild:
    """Answers binding calls and fails relation calls: the batch error envelope must still carry the binding usage."""

    def describe(self) -> dict:
        return {"name": "half", "version": "1", "model": {"id": "half", "kind": "local"}}

    def decide(self, op: str, items: list[dict]) -> tuple[list[dict], dict]:
        if op == "decide_relation":
            raise RuntimeError("relation backend down")
        results = [{"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]} for _ in items]
        return results, {"tokens_in": 7, "tokens_out": 1, "cost_usd": 0.5, "model": "half", "kind": "local"}


def test_batch_error_envelope_reports_the_usage_of_completed_groups() -> None:
    good = ctx("a", "Will X win?", "Yes")
    reply = call(_HalfFailingChild(), {"op": "batch", "id": 3, "calls": [
        {"op": "decide_binding", "ctx": good}, {"op": "decide_relation", "a": good, "b": good}]})
    assert "error" in reply and "relation backend down" in reply["error"]
    assert reply["tokens_in"] == 7 and reply["cost_usd"] == 0.5 and reply["model"] == "half"


# Candidate sets that hold both sides of each binary market (verifier finding: no group was ever emitted)

def test_bitcoin_ladder_event_yields_one_chain_over_the_yes_rungs() -> None:
    """20 members: 10 rungs, each with its Yes and No outcome. One ordered_chain over the 10 Yes outcomes, strongest
    threshold first (each rung implies the next lower one): the decision names representative ids only."""
    import random

    rungs = [60000 + 2000 * k for k in range(10)]
    title = "Bitcoin above ___ on June 1, 12PM ET?"
    members = [ctx(f"r{t}:{label}", f"Bitcoin above {t:,} on June 1, 12PM ET?", label, title)
               for t in rungs for label in ("No", "Yes")]
    random.Random(3).shuffle(members)
    groups = H._group({"group_id": "c1", "source": "s2", "members": members})["groups"]
    assert len(groups) == 1
    chain = groups[0]
    assert chain["kind"] == "ordered_chain" and chain["direction"] == "forward"
    assert chain["members"] == [f"r{t}:Yes" for t in reversed(rungs)]
    check_group(chain, {m["id"] for m in members})


def test_two_outcome_up_down_market_yields_no_group() -> None:
    """Up and Down of one market are one representative (Up): nothing to group, and no win wording to ask for an
    at_most_one."""
    title = "Solana Up or Down - August 21, 11:40PM-11:45PM ET"
    members = [ctx("u", title, "Up", title), ctx("d", title, "Down", title)]
    assert H._group({"group_id": "c2", "source": "s1", "members": members})["groups"] == []


def test_chain_members_keep_the_positive_side_and_named_outcomes() -> None:
    members = [ctx("y", "Will X exceed 5?", "Yes"), ctx("n", "Will X exceed 5?", "No"),
               ctx("a", "Who will win the Cup?", "Alpha"), ctx("b", "Who will win the Cup?", "Beta"),
               ctx("o", "Will Z stay under 9?", "No")]
    kept = [m["id"] for m in chain_members(members)]
    assert kept == ["y", "a", "b", "o"]  # the only No side of Z stays: it is the market's one outcome in the set


# Protocol

def test_hello_advertises_ops_and_rejects_other_protocols(child) -> None:
    reply = call(child, {"op": "hello", "id": 1, "protocol": 2})
    assert reply["id"] == 1 and reply["protocol"] == 2
    assert reply["name"] == "heuristic-rules" and reply["model"]["kind"] == "local"
    assert reply["ops"]["decide_group"] == {"max_batch": 16, "max_members": 64}
    assert reply["ops"]["batch"] == {"max_calls": MAX_CALLS}
    assert "error" in call(child, {"op": "hello", "id": 2, "protocol": 1})


def test_single_ops_echo_id_and_report_usage_fields(child) -> None:
    binding = ctx("a", "Bitcoin above 75,000 on May 29, 10AM ET?", "Yes")
    reply = call(child, {"op": "decide_binding", "id": 3, "ctx": binding})
    assert reply["id"] == 3 and check_binding(reply)["status"] == "bound"
    assert reply["tokens_in"] == 0 and reply["tokens_out"] == 0 and reply["cost_usd"] == 0.0
    assert "gpu_seconds" not in reply
    rel = call(child, {"op": "decide_relation", "id": 4, "a": binding, "b": binding})
    assert check_relation(rel["relation"]) == "EQUIV"  # same question, same label


def test_batch_envelope_counts_and_isolates_item_errors(child) -> None:
    good = ctx("a", "Bitcoin above 75,000 on May 29, 10AM ET?", "Yes")
    calls = [
        {"op": "decide_binding", "ctx": good},
        {"op": "decide_relation", "a": good, "b": good},
        {"op": "decide_binding", "ctx": ctx("bad", "", "Yes")},
        {"op": "decide_group", "ctx": {"group_id": "c1", "source": "s1", "members": [
            ctx("m1", "Who will win the Example Cup?", "Alpha"), ctx("m2", "Who will win the Example Cup?", "Beta")]}},
        {"op": "decide_nothing"},
    ]
    reply = call(child, {"op": "batch", "id": 9, "calls": calls})
    assert reply["id"] == 9 and len(reply["results"]) == len(calls)
    assert check_binding(reply["results"][0])["status"] == "bound"
    assert check_relation(reply["results"][1]["relation"]) == "EQUIV"
    assert "error" in reply["results"][2] and "error" in reply["results"][4]
    assert check_group(reply["results"][3]["groups"][0], {"m1", "m2"})["kind"] == "at_most_one"
    assert reply["tokens_in"] == 0 and reply["gpu_seconds"] == 0.0


def test_batch_limits_are_envelope_errors(child) -> None:
    binding = {"op": "decide_binding", "ctx": ctx("a", "Will X win?", "Yes")}
    too_many_of_one = [binding] * (MAX_BATCH["decide_binding"] + 1)
    assert "max_batch" in call(child, {"op": "batch", "id": 1, "calls": too_many_of_one})["error"]
    assert "max_calls" in call(child, {"op": "batch", "id": 2, "calls": [binding] * (MAX_CALLS + 1)})["error"]
    assert "must be a list" in call(child, {"op": "batch", "id": 3, "calls": "x"})["error"]


def test_malformed_requests_get_error_replies(child) -> None:
    assert "not JSON" in handle(child, "{bad")["error"]
    assert "not a JSON object" in handle(child, "[1, 2]")["error"]
    assert "unknown op" in handle(child, json.dumps({"op": "decide_nothing", "id": 5}))["error"]
    single = call(child, {"op": "decide_relation", "id": 6, "a": ctx("a", "Q?", "Yes")})
    assert single["id"] == 6 and "error" in single


# Conformance and the local adapter

def test_conformance_runner_passes_on_heuristic_child() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mentat_bench.children.conformance", sys.executable, "-m",
         "mentat_bench.children.heuristic_child"],
        env=child_env(), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "conformance:" in proc.stdout and "FAIL" not in proc.stdout


def test_local_adapter_protocol_2_drives_heuristic_child(monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(PYTHON_DIR))
    adapter = LocalJSONLAdapter([sys.executable, "-m", "mentat_bench.children.heuristic_child"], name="local",
                                timeout_s=120, protocol=2)
    try:
        assert adapter.supports_groups is True and adapter.advertised_group_max_members == 64
        decision, usage = adapter.decide_binding(ctx("a", "Bitcoin above 75,000 on May 29, 10AM ET?", "No"))
        assert check_binding(decision)["negated"] is True and usage["cost_usd"] == 0.0
        groups, _ = adapter.decide_group({"group_id": "c1", "source": "s1", "members": [
            ctx("m1", "Who will win the Example Cup?", "Alpha"), ctx("m2", "Who will win the Example Cup?", "Beta")]})
        assert [check_group(g, {"m1", "m2"})["kind"] for g in groups] == ["at_most_one"]
    finally:
        adapter.close()


def test_heuristic_child_does_not_reuse_fixture_parsers() -> None:
    source = (PYTHON_DIR / "mentat_bench" / "children" / "heuristic_child.py").read_text(encoding="utf-8")
    imports = [ln for ln in source.splitlines() if ln.startswith(("import ", "from "))]
    assert not [ln for ln in imports if "fixtures" in ln or "gold" in ln]

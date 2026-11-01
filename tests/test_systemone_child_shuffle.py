"""System One child under MB_S1_OPTION_SEED (the option-order diagnostic). Covered: the unseeded identity (the pinned prompt hash,
no option_order, the relation menus of the parent commit, the request bytes of every stage as eef88eb sends them); the seeded
menus (permutations of the fixed ones, the letter listings permuted and their letters kept, a pinned permutation, the same bytes
for the same seed, a question's menu alone or in a batch); the answer mapping (a server that answers by option content gives the
unseeded decisions under any seed, and the workload's known truth is decided); ties (the first option shown); describe()'s
option_order in the top level and the model block, and the prompt identity; MB_S1_STATS naming the order and refusing another;
from_env. Built on the fake Ollama server of tests/test_systemone_child.py. No network beyond 127.0.0.1, no clock, no randomness."""

from __future__ import annotations

import functools
import hashlib
import json
import random
import re
import zlib
from typing import Any

import pytest

import test_systemone_child as T
from mentat_bench.children import systemone_child as S1
from mentat_bench.children.protocol import ChildError
from test_systemone_child import (DIGEST, TAG, VERSION, FakeOllama, child_env, ctx, fake, group_of,  # noqa: F401 (fake: a fixture)
                                  make_child, pinned)

SEED = 20261101
SCHEME = "crc32-shuffle-seeded-v1"
PINNED_PROMPT_SHA256 = "d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44"
PINNED_OPTION_SHA256 = "3192c4e9526e13721b4ad6bd43081f49936ad76acf0a8068f906f8a5cf59fabf"  # option_prompt_sha256(SEED)
LETTER_STAGES = {"order", "base", "alt", "base_rules", "alt_rules"}  # the menus whose letters name a listed row
LETTER_ROW = re.compile(r"^([A-Z])\. (.*)$", re.M)  # a listing row of a chain or a reading: its letter and its text

# The unseeded relation menus (the parent commit's rule, checked against eef88eb): (a id, b id) and the menu.
UNSEEDED_RELATION_ORDERS = [
    (("event-17", "event-18"), ("EXCLUDES", "IMPLIES", "NONE", "EQUIV", "IMPLIED_BY")),
    (("m:1", "m:2"), ("NONE", "EXCLUDES", "EQUIV", "IMPLIED_BY", "IMPLIES")),
    (("", ""), ("IMPLIES", "EXCLUDES", "EQUIV", "IMPLIED_BY", "NONE")),
    (("toy:002aad3252abe6c2:1", "toy:00392d459f77e88c:2"), ("NONE", "EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES")),
]
# The seeded relation menus, pinned so that a change to the seeded scheme shows here.
SEEDED_RELATION_ORDERS = [
    (("a1", "b1"), SEED, ("IMPLIES", "IMPLIED_BY", "EQUIV", "NONE", "EXCLUDES")),
    (("x", "y"), SEED, ("EXCLUDES", "IMPLIES", "NONE", "IMPLIED_BY", "EQUIV")),
    (("event-17", "event-18"), SEED, ("IMPLIES", "EXCLUDES", "EQUIV", "IMPLIED_BY", "NONE")),
    (("a1", "b1"), 0, ("EQUIV", "EXCLUDES", "IMPLIES", "NONE", "IMPLIED_BY")),
]


# The workload: every op a run decides, with content that exercises each menu (bindings of both statuses and both label
# sides, relations over a grid of propositions, group sets of 2 to 9 members, a binary market, reading rows with and without a
# rule card).

def bindings() -> list[dict[str, Any]]:
    labels = ["Yes", "No", "Alpha", "Over", "Up", "Down"]
    return [{"ctx": ctx(f"b{i}", f"Will Example Coin close above {100 + i} on June {i + 1}?", label)}
            for i, label in enumerate(labels * 2)]


def relations() -> list[dict[str, Any]]:
    return [{"a": ctx(f"a{i}", f"Will Example {i} win?", "Yes"),
             "b": ctx(f"b{j}", f"Will Example {j} lose?", "No" if j % 2 else "Yes")} for i in range(3) for j in range(6)]


def groups() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [{"ctx": group_of(s, 2 + s % 7)} for s in range(24)]  # sizes 2 to 8
    big = [ctx(f"big:{j}", f"Will team big-{j} win cup?", "Yes") for j in range(S1.MAX_K + 1)]
    items.append({"ctx": {"group_id": "big", "source": "s1", "members": big}})  # over MAX_K: no bound questions
    market = [ctx("m1", "Will X win?", "Yes"), ctx("m2", "Will X win?", "No"), ctx("m3", "Will Y win?", "Yes")]
    items.append({"ctx": {"group_id": "m", "source": "s1", "members": market}})  # one side of a binary market is dropped
    return items


def readings() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for r in range(6):
        u = ctx(f"r{r}:u", f"Will Team {r} win the cup?", "Yes")
        if r % 2:
            u["context"] = S1.CONFORM_CARD  # a rule card: base_rules and alt_rules
        options = [ctx(f"r{r}:o{k}", f"Will Team {r}-{k} win?", "Yes") for k in range(2 + r % 3)]
        items.append({"ctx": {"group_id": f"r{r}", "source": "reading", "members": [u, *options]}})
    return items


def workload() -> list[tuple[str, list[dict[str, Any]]]]:
    return [("decide_binding", bindings()), ("decide_relation", relations()), ("decide_group", groups()),
            ("decide_group", readings())]


# The answers. A content answer weighs each option by a hash of its own text, so it never depends on the option's letter or
# position: a named option by its name (with the state and the instructions), a letter by the text of the row it names (with
# the instructions only, so a listing in another order gives the same weights).

def content_answer(state: str, question: dict[str, Any]) -> dict[str, Any]:
    keys = list(question["criteria"])
    rows = dict(LETTER_ROW.findall(state)) if question["instructions"] in LETTER_INSTRUCTIONS else None
    weights = {}
    for key in keys:
        if rows is None:
            basis = f"{state}\x1f{question['instructions']}\x1f{key}"
        else:
            basis = f"{question['instructions']}\x1f{rows.get(key, key)}"
        weights[key] = int(hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8], 16) + 1
    total = sum(weights.values())
    probs = {k: w / total for k, w in weights.items()}
    return {"type": "choice", "choice": max(probs, key=probs.get), "probabilities": probs, "confidence": 0.5}


def flat_answer(state: str, question: dict[str, Any]) -> dict[str, Any]:
    """Uniform probabilities: every argmax is an exact tie, so each decision is the first option shown."""
    keys = list(question["criteria"])
    return {"type": "choice", "choice": keys[0], "probabilities": {k: 1 / len(keys) for k in keys}, "confidence": 0.5}


RELATION_TEXT = S1.PROMPTS["relation"]["instructions"]
LETTER_INSTRUCTIONS = {S1.PROMPTS[name]["instructions"] for name in LETTER_STAGES}
READING_INSTRUCTIONS = {S1.PROMPTS[name]["instructions"] for name in ("base", "alt", "base_rules", "alt_rules")}


def team_number(text: str) -> int:
    """The j of "Will team <seed>-<j> win ..." (the group sets) or "Will Team <r>-<k> win?" (the reading options)."""
    return int(re.search(r"-(\d+) win", text).group(1))


def truth_of(state: str, question: dict[str, Any]) -> str | None:
    """The option the workload's truth names for this question, read from its content; None when the workload has no truth
    for it. Relations: RELATION_CHOICES[(i + j) % 5]. Chains: the outcome with the smallest number is the strongest. Sets: the
    kind is ordered_chain. Readings: the option numbered 1, and none for a U whose number is a multiple of 3."""
    text = question["instructions"]
    if text.endswith(RELATION_TEXT):
        i = int(re.search(r"Proposition A: Will Example (\d+) win", state).group(1))
        j = int(re.search(r"Proposition B: Will Example (\d+) lose", text).group(1))
        return S1.RELATION_CHOICES[(i + j) % 5]
    if text == S1.PROMPTS["kind"]["instructions"]:
        return "ordered_chain"
    rows = LETTER_ROW.findall(state)
    if text == S1.PROMPTS["order"]["instructions"]:
        return min(rows, key=lambda row: team_number(row[1]))[0]
    if text in READING_INSTRUCTIONS:
        u = int(re.search(r"Proposition U: Will Team (\d+) win", state).group(1))
        if u % 3 == 0:
            return "none"
        return next(letter for letter, content in rows if content.startswith("Will Team") and "-1 win?" in content)
    return None


def truth_answer(state: str, question: dict[str, Any]) -> dict[str, Any]:
    pick = truth_of(state, question)
    if pick is None or pick not in question["criteria"]:
        return content_answer(state, question)
    keys = list(question["criteria"])
    probs = {k: (0.8 if k == pick else 0.2 / (len(keys) - 1)) for k in keys}
    return {"type": "choice", "choice": pick, "probabilities": probs, "confidence": 0.8}


@pytest.fixture(autouse=True)
def content_answers(monkeypatch):
    """The fake server answers by option content (the handler looks answer_of up at call time)."""
    monkeypatch.setattr(T, "answer_of", content_answer)


def seeded_child(server: FakeOllama, seed: int | None, **kwargs: Any) -> S1.SystemOneChild:
    """The child under test. Unset, it is built exactly as before the option seed existed (no option_seed argument), so the
    unseeded golden can also be generated from the parent commit's module."""
    if seed is not None:
        kwargs["option_seed"] = seed
    return S1.SystemOneChild(TAG, server.url, DIGEST, VERSION, parallel=1, timeout=10.0, **kwargs)


def run_workload(seed: int | None) -> tuple[list[list[Any]], list[dict[str, Any]], list[bytes]]:
    """Every op of the workload through one fresh server: the decisions per op, the requests as parsed, the raw bodies."""
    server = FakeOllama()
    try:
        child = seeded_child(server, seed)
        results = [child.decide(op, items)[0] for op, items in workload()]
        requests, raw = list(server.requests), list(server.raw_requests)
    finally:
        server.close()
    for op_results in results:
        assert not any(isinstance(result, ChildError) for result in op_results)
    return results, requests, raw


@functools.lru_cache(maxsize=None)
def cached_run(seed: int | None) -> tuple[list[list[Any]], list[dict[str, Any]], list[bytes]]:
    """run_workload under the content answers (every test that uses it has them): each seed is decided once."""
    return run_workload(seed)


def canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


RULE_READING_TEXTS = {S1.PROMPTS["base_rules"]["instructions"], S1.PROMPTS["alt_rules"]["instructions"]}


def stage_digests(raw: list[bytes]) -> dict[str, str]:
    """The SHA-256 of the request bodies of each stage, as sent and in the order sent, with "all" for every body. A stage is the
    name of its question, with "+rules" for a reading question of a rule card (base or alt under the card's instructions)."""
    by_stage: dict[str, list[bytes]] = {"all": list(raw)}
    for body_bytes in raw:
        [(name, question)] = json.loads(body_bytes)["questions"].items()
        stage = name + ("+rules" if question["instructions"] in RULE_READING_TEXTS else "")
        by_stage.setdefault(stage, []).append(body_bytes)
    return {stage: hashlib.sha256(b"".join(body + b"\n" for body in bodies)).hexdigest()
            for stage, bodies in sorted(by_stage.items())}


def paired(seed: int | None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """The requests of the unseeded run and of the run under seed, one pair per request (the same questions, in the same order)."""
    _, base, _ = cached_run(None)
    _, seeded, _ = cached_run(seed)
    assert len(seeded) == len(base)
    return list(zip(base, seeded))


# 1. Unset: the identity of the pre-registered child is unchanged.

def test_prompt_sha256_is_the_pinned_literal() -> None:
    assert S1.PROMPT_SHA256 == PINNED_PROMPT_SHA256


def test_unseeded_describe_has_no_option_order(fake) -> None:
    child = make_child(fake)
    desc = child.describe()
    assert "option_order" not in desc
    assert desc["prompt_sha256"] == PINNED_PROMPT_SHA256 == desc["model"]["prompt_sha256"]
    assert desc["version"] == "7" and desc["model"]["child_version"] == "7"


def original_relation_order(a_id: str, b_id: str) -> tuple[str, ...]:
    """The parent commit's rule (eef88eb), spelled out: a random.Random seeded by crc32 of "a\\x1fb" shuffles the five choices."""
    order = ["EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE"]
    random.Random(zlib.crc32(f"{a_id}\x1f{b_id}".encode("utf-8"))).shuffle(order)
    return tuple(order)


@pytest.mark.parametrize("ids, expected", UNSEEDED_RELATION_ORDERS)
def test_unseeded_relation_order_is_the_golden_list(ids: tuple[str, str], expected: tuple[str, ...]) -> None:
    a, b = {"id": ids[0]}, {"id": ids[1]}
    assert S1.relation_order(a, b) == expected
    assert S1.relation_order(a, b, None) == expected


def test_unseeded_relation_order_is_the_parent_commits_crc32_rule() -> None:
    for i in range(12):
        for j in range(12):
            a_id, b_id = f"pair-{i}", f"pair-{j}"
            assert S1.relation_order({"id": a_id}, {"id": b_id}) == original_relation_order(a_id, b_id)


# The unseeded request bytes, per stage (stage_digests), as the parent commit eef88eb sends them for the workload. Generated by
# stage_digests(run_workload(None)[2]) on eef88eb's systemone_child.py (git archive eef88eb python, first on PYTHONPATH, with the
# content answers of this file): 129 requests. Any change to an unseeded request fails here, including a reorder that the
# fake server's name-keyed answers would not show.
UNSEEDED_STAGE_DIGESTS = {
    "all": "033c04a54648dcd2ed37d55201a87f51c8a62f25628abc2eb9b7aa0957beb7dd",
    "alt": "5be49a3eeb787cd0ec7987184eead816fa162d368ddd2716e44e13f6109e5caa",
    "alt+rules": "d3794e467eae9d952cfbc7b115497f5cbba8226d5c7c79ea3447233b8b8bed1d",
    "base": "02bd50f3ad83dd7a9d5899393f36a8e4f3b2d6f7e7866ad9319a3f2c35330a71",
    "base+rules": "c3f7b1f5dfb3b3f8db773b7e28ba2329f4f25e582f145078ca085a12a89e85f6",
    "k": "879c1a7e86c05913dbf83757a01126ac78794e7e143eac46a1106c469dbd38ee",
    "kind": "825adb681bbbadaace800866610834f983db8c6ddb4777e10595529d97bb0e0d",
    "lower": "15129f0cdbdd20ae5c998588877befca1e6dd1f9de32126939a8db69b2727184",
    "missing": "4839eecd8f4afbba574cf2496008f242cf414370b002ca1118d7f3b167014a98",
    "negated": "8fa77c9dd45b5fc8b074c598a6e8fe3a7080bff50975b67ff13e9cd93736da35",
    "order": "28e3fb71f6a632f80253580191ed4b3b13ebce1da057745d3f51a3c37786c040",
    "relation": "98a63da9ae44d9e740f8f90283bd853abd70eb98bebc9906d00261a402009d2f",
    "status": "23af1a26e4230e969980a1ad1d83ccf63f61473ec0f4ae102e9236c028443422",
    "upper": "a735001820bf374d3cd6f3b9d11ec4e603a247eecc51182807aa103483d7acc4",
}


def test_unseeded_request_bytes_are_the_parent_commits() -> None:
    assert stage_digests(cached_run(None)[2]) == UNSEEDED_STAGE_DIGESTS


# The seeded listing of reading row 1 (a rule card, three options) under SEED: the order of its options, and the rows the
# request shows (letter A first).
SEEDED_READING_ORDER = [1, 2, 0]
SEEDED_READING_LISTING = ["Will Team 1-1 win? [label: Yes]", "Will Team 1-2 win? [label: Yes]", "Will Team 1-0 win? [label: Yes]"]


def test_a_seeded_reading_listing_is_pinned() -> None:
    row = readings()[1]["ctx"]["members"]
    assert S1._menu_order(SEED, "reading", [m["id"] for m in row], range(len(row) - 1)) == SEEDED_READING_ORDER
    _, requests, _ = cached_run(SEED)
    state = next(r["state"] for r in requests if "Proposition U: Will Team 1 win the cup?" in r["state"]
                 and "base" in r["questions"])
    assert [content for _, content in LETTER_ROW.findall(state)] == SEEDED_READING_LISTING


def test_unseeded_menus_keep_the_fixed_order(fake) -> None:
    child = make_child(fake)
    members = group_of(3, 4)["members"]
    child.decide("decide_group", [{"ctx": group_of(3, 4)}])
    kind = next(r["questions"]["kind"]["criteria"] for r in fake.requests if "kind" in r["questions"])
    assert list(kind) == list(S1.PROMPTS["kind"]["criteria"])
    fake.requests.clear()
    child._chain(members, "")
    first_round = next(r for r in fake.requests if "order" in r["questions"])
    assert dict(LETTER_ROW.findall(first_round["state"]))["A"] == f"{members[0]['question']} [label: {members[0]['label']}]"


# 2. Set: the menus are permutations of the fixed ones, and keyed on the seed and the question.

def test_seeded_named_menus_are_permutations_of_the_fixed_menus() -> None:
    reordered: set[str] = set()
    for base, seeded in paired(SEED):
        assert list(base["questions"]) == list(seeded["questions"])
        for name, qa in base["questions"].items():
            if name in LETTER_STAGES:
                continue
            qb = seeded["questions"][name]
            assert seeded["state"] == base["state"]  # a named menu's state is not listed, so it is the same
            assert qb["instructions"] == qa["instructions"]
            assert sorted(qb["criteria"].items()) == sorted(qa["criteria"].items()), name  # the same options and texts
            if list(qb["criteria"]) != list(qa["criteria"]):
                reordered.add(name)
    assert reordered >= {"relation", "status", "kind", "k", "lower", "upper"}


def test_seeded_letter_listings_are_permutations_that_keep_their_letters() -> None:
    moved = {"order": 0, "reading": 0}
    for base, seeded in paired(SEED):
        for name, qa in base["questions"].items():
            if name not in LETTER_STAGES:
                continue
            qb = seeded["questions"][name]
            assert list(qb["criteria"].items()) == list(qa["criteria"].items())  # the letters and their texts, in order
            rows_a, rows_b = LETTER_ROW.findall(base["state"]), LETTER_ROW.findall(seeded["state"])
            assert [letter for letter, _ in rows_a] == [letter for letter, _ in rows_b]  # letters name the same positions
            assert sorted(text for _, text in rows_a) == sorted(text for _, text in rows_b)  # the same outcomes, reordered
            if rows_a != rows_b:
                moved["order" if name == "order" else "reading"] += 1
    assert moved["order"] > 0 and moved["reading"] > 0


def test_seeded_relation_and_kind_menus_are_pinned() -> None:
    for ids, seed, expected in SEEDED_RELATION_ORDERS:
        assert S1.relation_order({"id": ids[0]}, {"id": ids[1]}, seed) == expected
    assert S1._menu_order(SEED, "kind", ["g:1", "g:2", "g:3"], S1.PROMPTS["kind"]["criteria"]) == [
        "at_least_k", "at_most_k", "at_most_one", "exactly_one", "none", "ordered_chain"]


def test_the_same_seed_gives_byte_identical_requests() -> None:
    _, _, first = run_workload(SEED)
    _, _, second = run_workload(SEED)
    assert first == second
    assert first != cached_run(None)[2]  # the seed changes the bytes sent


def reordered_named_menus(seed: int) -> int:
    """How many named menus of the workload under seed are in another order than unseeded."""
    count = 0
    for base, seeded in paired(seed):
        for name, qa in base["questions"].items():
            if name not in LETTER_STAGES and list(seeded["questions"][name]["criteria"]) != list(qa["criteria"]):
                count += 1
    return count


def test_a_different_seed_reorders_some_menus() -> None:
    assert reordered_named_menus(SEED + 1) > 0


def test_seed_zero_is_a_seed_that_reorders_the_menus() -> None:
    """0 is an integer seed like any other: it is not unset, and it shuffles the menus."""
    assert reordered_named_menus(0) > 0
    assert S1._menu_order(0, "kind", ["g:1", "g:2", "g:3"], S1.PROMPTS["kind"]["criteria"]) != list(S1.PROMPTS["kind"]["criteria"])


BATCHES = {"binding": ("decide_binding", bindings), "relation": ("decide_relation", relations),
           "group": ("decide_group", groups), "reading": ("decide_group", readings)}


@pytest.mark.parametrize("name", list(BATCHES))
def test_a_question_is_asked_the_same_alone_or_in_a_batch(name: str) -> None:
    op, make = BATCHES[name]
    items = make()
    server = FakeOllama()
    try:
        child = seeded_child(server, SEED)
        batch_results, _ = child.decide(op, items)
        batch_raw = list(server.raw_requests)
        server.raw_requests.clear()
        alone_results: list[Any] = []
        alone_raw: list[bytes] = []
        for item in items:
            alone_results += child.decide(op, [item])[0]
            alone_raw += server.raw_requests
            server.raw_requests.clear()
    finally:
        server.close()
    assert alone_raw == batch_raw
    assert canon(alone_results) == canon(batch_results)


# 3. Answer mapping: a server that answers by content gets the same decisions with and without the seed.

@pytest.mark.parametrize("seed", [0, 7, SEED])
def test_content_answers_give_the_unseeded_decisions(seed: int) -> None:
    base_results, _, _ = cached_run(None)
    seeded_results, _, _ = cached_run(seed)
    for op_base, op_seeded in zip(base_results, seeded_results):
        assert canon(op_seeded) == canon(op_base)


def test_the_workload_reaches_each_decision_path() -> None:
    """The equality above is not vacuous: the decisions cover each path (both statuses, several relations, chains of three or
    more, a reading's pick under each reading, and the rule-card readings)."""
    binding, relation, group, reading = cached_run(SEED)[0]
    assert {r["status"] for r in binding} == {"bound", "unresolved"}
    assert len({r["relation"] for r in relation}) >= 3
    chains = [g for r in group for g in r["groups"] if g["kind"] == "ordered_chain" and g["scope"] is None]
    assert any(len(g["members"]) >= 3 for g in chains)
    assert any(g["scope"] is not None for r in reading for g in r["groups"])
    _, requests, _ = cached_run(SEED)
    asked = {name for req in requests for name in req["questions"]}
    assert {"lower", "upper", "k", "order", "relation", "status", "negated", "missing", "base", "alt"} <= asked
    rule_card_readings = [q for req in requests for q in req["questions"].values() if "league rules" in q["instructions"]]
    assert {q["instructions"] for q in rule_card_readings} == {S1.PROMPTS["base_rules"]["instructions"],
                                                               S1.PROMPTS["alt_rules"]["instructions"]}


@pytest.mark.parametrize("seed", [None, SEED])
def test_the_known_truth_is_decided_under_the_seed(seed: int | None, monkeypatch) -> None:
    monkeypatch.setattr(T, "answer_of", truth_answer)
    binding, relation, group, reading = run_workload(seed)[0]
    assert [r["relation"] for r in relation] == [S1.RELATION_CHOICES[(i + j) % 5] for i in range(3) for j in range(6)]
    expected_chains: list[list[str] | None] = [[f"g{s}:{j}" for j in range(2 + s % 7)] for s in range(24)]
    expected_chains += [[f"big:{j}" for j in range(S1.MAX_K + 1)], None]  # the binary market drops a side: no decision
    for decision, want in zip(group, expected_chains):
        if want is None:
            assert decision["groups"] == []
        else:
            assert [g["members"] for g in decision["groups"] if g["kind"] == "ordered_chain" and g["scope"] is None] == [want]
    for r, decision in enumerate(reading):
        u = f"r{r}:u"
        if r % 3 == 0:
            assert decision["groups"] == []  # both readings pick none
        else:
            pairs = {tuple(g["members"]) for g in decision["groups"]}
            assert pairs == {(u, f"r{r}:o1"), (f"r{r}:o1", u)}
            assert {tuple(g["scope"]["values"]) for g in decision["groups"]} == {("base",), ("alt",)}


# 4. Ties: an exact tie goes to the first option shown, which under the seed is not the fixed first option.

def test_flat_named_menus_decide_the_first_option_shown(monkeypatch) -> None:
    monkeypatch.setattr(T, "answer_of", flat_answer)
    server = FakeOllama()
    try:
        child = seeded_child(server, SEED)
        k_menu = {str(k): S1.PROMPTS["k"]["criteria"][str(k)] for k in range(1, 5)}
        lower_menu = {str(j): S1.PROMPTS["lower"]["criteria"][str(j)] for j in range(4)}
        upper_menu = {str(j): S1.PROMPTS["upper"]["criteria"][str(j)] for j in range(1, 4)}
        upper_menu["no_limit"] = S1.PROMPTS["upper"]["criteria"]["no_limit"]
        cases = [("status", None, ["b1"]), ("negated", None, ["b1"]), ("missing", None, ["b1"]),
                 ("kind", None, ["g:1", "g:2", "g:3"]), ("k", k_menu, ["g:1", "g:2", "g:3", "g:4"]),
                 ("lower", lower_menu, ["g:1", "g:2", "g:3"]), ("upper", upper_menu, ["g:1", "g:2", "g:3"])]
        moved = 0
        for stage, criteria, ids in cases:
            probs = child._pick(S1._frame(stage), stage, ids, criteria)
            shown = list(server.requests[-1]["questions"][stage]["criteria"])
            fixed_first = list(criteria if criteria is not None else S1.PROMPTS[stage]["criteria"])[0]
            assert S1._argmax(probs) == shown[0], stage
            moved += shown[0] != fixed_first
        assert moved > 0
    finally:
        server.close()


def test_flat_relation_tie_goes_to_the_first_option_of_the_seeded_menu(monkeypatch) -> None:
    monkeypatch.setattr(T, "answer_of", flat_answer)
    pairs = [{"a": ctx(f"a{i}", f"Will A{i} win?", "Yes"), "b": ctx(f"b{i}", f"Will B{i} win?", "Yes")} for i in range(8)]
    server = FakeOllama()
    try:
        results, _ = seeded_child(server, SEED).decide("decide_relation", pairs)
    finally:
        server.close()
    for pair, result in zip(pairs, results):
        order = S1.relation_order(pair["a"], pair["b"], SEED)
        assert result["relation"] == order[0]
        assert result["conf"] == pytest.approx(0.0)  # equal probabilities: ln p(best named) - ln p(NONE) is zero
    assert any(S1.relation_order(p["a"], p["b"], SEED)[0] != S1.RELATION_CHOICES[0] for p in pairs)


def test_flat_chain_and_reading_pick_the_first_listed_option(monkeypatch) -> None:
    monkeypatch.setattr(T, "answer_of", flat_answer)
    server = FakeOllama()
    try:
        child = seeded_child(server, SEED)
        members = [ctx(f"c{i}", f"Will team 9-{i} win cup?", "Yes") for i in range(5)]
        placed = child._chain(members, "")
        rounds = [r for r in server.requests if "order" in r["questions"]]
        assert len(rounds) == 4
        for k, round_request in enumerate(rounds):
            row_a = dict(LETTER_ROW.findall(round_request["state"]))["A"]
            assert row_a == f"{placed[k]['question']} [label: {placed[k]['label']}]"  # letter A is the member placed
        assert placed[0]["id"] != members[0]["id"]  # the seeded listing, not the fixed one

        picks = []
        for r in range(4):  # several rows: the seeded listing puts a different option first in some of them
            u = ctx(f"r{r}:u", f"Will Team {r} win the cup?", "Yes")
            options = [ctx(f"r{r}:o{i}", f"Will Team {r}-{i} win?", "Yes") for i in range(4)]
            server.requests.clear()
            result = child._reading({"group_id": f"r{r}", "source": "reading", "members": [u, *options]})
            base_request = next(q for q in server.requests if "base" in q["questions"])
            row_a = dict(LETTER_ROW.findall(base_request["state"]))["A"]
            by_content = {f"{o['question']} [label: {o['label']}]": o["id"] for o in options}
            picked = by_content[row_a]  # the tie went to letter A, the first option listed
            assert {tuple(g["members"]) for g in result["groups"]} == {(u["id"], picked), (picked, u["id"])}
            picks.append(picked != options[0]["id"])
        assert any(picks)
    finally:
        server.close()


# 5. describe() and the prompt identity of a seeded run.

@pytest.mark.parametrize("seed", [0, SEED])
def test_describe_with_a_seed_names_the_seed_and_its_prompt(seed: int, fake) -> None:
    desc = seeded_child(fake, seed).describe()
    assert desc["option_order"] == {"seed": seed, "scheme": SCHEME}
    want = hashlib.sha256(json.dumps({"prompts": S1.PROMPTS, "option_seed": seed}, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
    assert desc["prompt_sha256"] == want
    assert desc["model"]["prompt_sha256"] == want
    assert want != PINNED_PROMPT_SHA256
    assert desc["version"] == "7" and desc["model"]["child_version"] == "7"


def test_option_prompt_sha256_is_pinned_and_depends_on_the_seed() -> None:
    assert S1.option_prompt_sha256(SEED) == PINNED_OPTION_SHA256
    assert S1.option_prompt_sha256(SEED) != S1.option_prompt_sha256(SEED + 1)
    assert S1.option_prompt_sha256(SEED) != S1.option_prompt_sha256(0)


# 6. from_env reads MB_S1_OPTION_SEED: unset is no seed; an integer is the seed; anything else is refused.

def test_from_env_without_the_variable_has_no_seed(fake, tmp_path) -> None:
    env = child_env(fake, pinned(tmp_path))
    env.pop("MB_S1_OPTION_SEED", None)
    child = S1.SystemOneChild.from_env(env)
    assert child.option_seed is None
    assert "option_order" not in child.describe()


@pytest.mark.parametrize("value, seed", [("0", 0), ("20261101", SEED)])
def test_from_env_reads_the_seed_as_an_integer(fake, tmp_path, value: str, seed: int) -> None:
    child = S1.SystemOneChild.from_env({**child_env(fake, pinned(tmp_path)), "MB_S1_OPTION_SEED": value})
    assert type(child.option_seed) is int and child.option_seed == seed
    assert child.describe()["option_order"] == {"seed": seed, "scheme": SCHEME}


@pytest.mark.parametrize("value", ["", "abc", "1.5"])
def test_from_env_refuses_a_seed_that_is_not_an_integer(fake, tmp_path, value: str) -> None:
    with pytest.raises(ValueError):
        S1.SystemOneChild.from_env({**child_env(fake, pinned(tmp_path)), "MB_S1_OPTION_SEED": value})


# 7. The model block (the harness persists it) and MB_S1_STATS name the option order; a stats file of another order is refused.

@pytest.mark.parametrize("seed", [0, SEED])
def test_the_model_block_names_the_seed(seed: int, fake) -> None:
    assert seeded_child(fake, seed).describe()["model"]["option_order"] == {"seed": seed, "scheme": SCHEME}


def test_the_model_block_of_an_unseeded_run_has_no_option_order(fake) -> None:
    assert "option_order" not in make_child(fake).describe()["model"]


def write_stats_file(path, seed: int | None) -> None:
    """One binding decided by a child of `seed` (None: unseeded) with MB_S1_STATS at path: the file is written after the call."""
    server = FakeOllama()
    try:
        child = seeded_child(server, seed, stats_path=str(path))
        child.decide("decide_binding", [{"ctx": ctx("sb0", "Will Example Coin close above 100?", "Yes")}])
    finally:
        server.close()


def test_a_shuffled_run_writes_its_option_order_to_the_stats_file(tmp_path) -> None:
    path = tmp_path / "stats.json"
    write_stats_file(path, SEED)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["option_order"] == {"seed": SEED, "scheme": SCHEME}
    assert doc["ops"]["decide_binding"]["units"] == 1


def test_an_unseeded_run_writes_no_option_order_to_the_stats_file(tmp_path) -> None:
    path = tmp_path / "stats.json"
    write_stats_file(path, None)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert "option_order" not in doc
    assert doc["ops"]["decide_binding"]["units"] == 1


@pytest.mark.parametrize("written, reading", [(None, SEED), (SEED, None), (SEED, SEED + 1)])
def test_a_stats_file_of_another_option_order_is_refused_and_not_rewritten(tmp_path, written, reading) -> None:
    path = tmp_path / "stats.json"
    write_stats_file(path, written)
    before = path.read_bytes()
    server = FakeOllama()
    try:
        with pytest.raises(RuntimeError, match="option order"):
            seeded_child(server, reading, stats_path=str(path))
    finally:
        server.close()
    assert path.read_bytes() == before


def test_the_same_option_order_continues_the_stats_file(tmp_path) -> None:
    path = tmp_path / "stats.json"
    write_stats_file(path, SEED)
    server = FakeOllama()
    try:
        child = seeded_child(server, SEED, stats_path=str(path))
    finally:
        server.close()
    assert child.stats["decide_binding"]["units"] == 1

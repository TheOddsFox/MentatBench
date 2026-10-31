"""System One child against a fake Ollama server (stdlib http.server on an ephemeral port): one question per request, conf,
groups, refusals, abstentions over the context, item-level 500s, degenerate answers, the loopback URL rules, usage, pins,
determinism, conformance and the local adapter. No model, no network beyond 127.0.0.1, no gold."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import socket
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from mentat_bench import decision_scoring as DS
from mentat_bench.adapters.base import check_binding, check_group, check_relation_decision, group_terms, terms_class
from mentat_bench.adapters.base import DecisionError
from mentat_bench.adapters.local_jev import LocalJSONLAdapter
from mentat_bench.children import conformance as CONF
from mentat_bench.gateway import Gateway
from mentat_bench.children import systemone_child as S1
from mentat_bench.children.common import chain_members, class_key
from mentat_bench.children.protocol import ChildError, handle

PYTHON_DIR = Path(__file__).resolve().parents[1] / "python"
WORKTREE = Path(__file__).resolve().parents[1]
TAG = "fake-s1:1b"
DIGEST = "ab" * 32
VERSION = "0.40.11"
CONTEXT_400 = "the prompt is longer than the context length currently available to the model; shorten the prompt"
CONTEXT_SIZE_400 = "request (9280 tokens) exceeds the available context size (8192 tokens), try increasing it"  # Ollama 0.40.11


def ctx(oid: str, question: str, label: str | None, title: str | None = "Example event") -> dict:
    return {"id": oid, "question": question, "label": label, "event_title": title}


def fake_probs(state: str, question: dict[str, Any]) -> dict[str, float]:
    """The fake server's probabilities: a fixed function of the state, the instructions and each option."""
    keys = list(question["criteria"])
    raw = [int(hashlib.sha256(f"{state}\x1f{question['instructions']}\x1f{k}".encode()).hexdigest()[:8], 16) + 1
           for k in keys]
    total = sum(raw)
    return {k: w / total for k, w in zip(keys, raw)}


def answer_of(state: str, question: dict[str, Any]) -> dict[str, Any]:
    """A choice answer. Markers in the state or the instructions make the answer degenerate, as the tests need."""
    text = state + question["instructions"]
    probs = fake_probs(state, question)
    if "ZEROP" in state:  # all probability on the first option, none on the rest
        probs = {k: (1.0 if i == 0 else 0.0) for i, k in enumerate(probs)}
    if "ONEQUIV" in text:  # all probability on EQUIV
        probs = {k: float(k == "EQUIV") for k in probs}
    if "BADZERO" in text:
        probs = {k: 0.0 for k in probs}
    if "BADSUM" in text:
        probs = {k: 2 * v for k, v in probs.items()}
    if "BADKEY" in text:
        probs.pop(list(probs)[-1])
    if "BADNAN" in text:
        probs[next(iter(probs))] = float("nan")
    if "BIGINT" in text:
        probs[next(iter(probs))] = 10 ** 400  # a valid JSON integer, too large for a float
    return {"type": "choice", "choice": max(probs, key=lambda k: probs[k]), "probabilities": probs, "confidence": 0.5}


# The kinds whose /api/tags and /api/version answer: ollama lists the digest; the tags_* kinds are the fall-through cases (see FakeOllama).
TAGS_KINDS = ("ollama", "tags_nodigest", "tags_bare", "tags_unlisted")


class FakeOllama:
    """/api/tags, /api/version and /v1/systemone. A marker in the state or in any question's instructions decides the
    reply to the whole request: CTX400 (400, input longer than the context), CTXSIZE (400, the "exceeds the available context
    size" text; CTXSIZEUPPER in upper case), BAD400 (400, other), FAIL413, FAIL500,
    FAIL404, REDIRECT301/302/307/308 (a redirect), BADJSON (a non-JSON 200). NOANSWER in one question's instructions
    leaves that question out of the answers. kind selects the server: "ollama" (/api/tags with the digest, /api/version),
    "openai" (no /api/tags; /v1/models lists the model with its digest), "bare" (no /api/tags; /v1/models lists it with no
    digest) or "reply" (no model list; the digest is a field of each reply). "reply405" and "reply_html" are "reply" servers
    whose model list and version are a 405, or an HTML 200. "tags500": /api/tags is a 500. "tags_unlisted": /api/tags lists no
    model and /v1/models lists it with its digest. "tags_nodigest": /api/tags lists it without a digest and the reply gives it.
    "tags_bare": /api/tags lists it without a digest and no source gives one. context_chars: a state over it is a 400 with
    the context text (None: no limit). transform(answers) edits the answers of a 200 reply; usage_override replaces its usage."""

    def __init__(self, tag: str = TAG, digest: str = DIGEST, kind: str = "ollama"):
        self.tag, self.digest, self.kind = tag, digest, kind
        self.context_chars: int | None = 200_000
        self.context_text = CONTEXT_400  # the 400 text of an over-long state (CTX400 and context_chars)
        self.transform: Any = None
        self.usage_override: Any = None
        self.reply_model: str | None = None  # the "model" field of the replies (None: the tag)
        self.requests: list[dict[str, Any]] = []
        self.raw_requests: list[bytes] = []  # the request bodies as sent, before parsing (the golden digests hash these)
        self._lock = threading.Lock()
        self.flaky_seen: set[str] = set()  # the request texts that already got their one FLAKY500
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, body: dict[str, Any]) -> None:
                self._raw(code, json.dumps(body).encode("utf-8"))

            def _raw(self, code: int, data: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if owner.kind in ("reply405", "reply_html") and self.path in ("/api/tags", "/api/version", "/v1/models"):
                    if owner.kind == "reply405":
                        self._send(405, {"error": "method not allowed"})
                    else:
                        self._raw(200, b"<!doctype html><html><body>Not an API</body></html>")
                elif owner.kind == "tags500" and self.path == "/api/tags":
                    self._send(500, {"error": "the model list is unavailable"})
                elif self.path == "/api/tags" and owner.kind in TAGS_KINDS:
                    entry = {"name": owner.tag, "model": owner.tag, "size": 1234,
                             **({"digest": owner.digest} if owner.kind == "ollama" else {})}
                    self._send(200, {"models": [] if owner.kind == "tags_unlisted" else [entry]})
                elif self.path == "/api/version" and owner.kind in TAGS_KINDS:
                    self._send(200, {"version": VERSION})
                elif self.path == "/v1/models" and owner.kind in ("openai", "bare", "tags_unlisted"):
                    entry = {"id": owner.tag, "object": "model",
                             **({"digest": owner.digest} if owner.kind in ("openai", "tags_unlisted") else {})}
                    self._send(200, {"object": "list", "data": [entry]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                body = json.loads(raw)
                with owner._lock:
                    owner.raw_requests.append(raw)
                    owner.requests.append(body)
                text = body.get("state", "") + "".join(q["instructions"] for q in body.get("questions", {}).values())
                redirect = next((code for code in (301, 302, 307, 308) if f"REDIRECT{code}" in text), None)
                if self.path != "/v1/systemone":
                    self._send(404, {"error": "not found"})
                elif body["model"] != owner.tag:
                    self._send(404, {"error": f"model {body['model']!r} not found"})
                elif "FAIL404" in text:
                    self._send(404, {"error": "model not found"})
                elif redirect is not None:
                    self.send_response(redirect)
                    self.send_header("Location", "http://example.invalid/v1/systemone")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif "CTX400UPPER" in text:
                    self._send(400, {"error": "The Prompt Is LONGER Than The Context Length currently available"})
                elif "CTXSIZEUPPER" in text:  # checked first: it contains CTXSIZE
                    self._send(400, {"error": CONTEXT_SIZE_400.upper()})
                elif "CTXSIZE" in text:
                    self._send(400, {"error": CONTEXT_SIZE_400})
                elif "CTX400" in text or (owner.context_chars and len(body.get("state", "")) > owner.context_chars):
                    self._send(400, {"error": owner.context_text})
                elif "FLAKY500" in text and text not in owner.flaky_seen:
                    owner.flaky_seen.add(text)  # a 500 once; the retry of the same request is answered
                    self._send(500, {"error": "scoring failed once"})
                elif "BAD400" in text:
                    self._send(400, {"error": "invalid request: question text is not allowed"})
                elif "FAIL413" in text:
                    self._send(413, {"error": "text and schema must not exceed 64 KiB"})
                elif "FAIL500" in text:
                    self._send(500, {"error": "scoring failed"})
                elif "BADJSON" in text:
                    self._raw(200, b"<html>not json</html>")
                else:
                    answers = {name: answer_of(body["state"], q) for name, q in body["questions"].items()
                               if "NOANSWER" not in q["instructions"]}
                    if owner.transform is not None:
                        answers = owner.transform(answers)
                    usage = {"input_tokens": 5 * len(body["questions"]), "output_tokens": len(body["questions"])}
                    reply = {"model": owner.reply_model or owner.tag, "answers": answers,
                             "usage": usage if owner.usage_override is None else owner.usage_override}
                    if owner.kind in ("reply", "reply405", "reply_html", "tags_nodigest"):
                        reply["digest"] = owner.digest
                    self._send(200, reply)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake():
    server = FakeOllama()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    """The pause before a 500 retry is 0.5 s; the tests run without it (one test checks the pause itself)."""
    monkeypatch.setattr(S1, "RETRY_PAUSE_S", 0.0)


def make_child(fake: FakeOllama, parallel: int = 4) -> S1.SystemOneChild:
    return S1.SystemOneChild(TAG, fake.url, DIGEST, VERSION, parallel=parallel, timeout=10.0)


def write_pins(path: Path, pins: dict[str, Any]) -> Path:
    path.write_text(json.dumps(pins), encoding="utf-8")
    return path


def pinned(tmp_path: Path) -> Path:
    return write_pins(tmp_path / "pins.json", {TAG: {"digest": DIGEST, "license": None, "ollama_version": VERSION,
                                                       "size": 1234}})


def child_env(fake: FakeOllama, pins: Path) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(PYTHON_DIR) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            "MB_S1_MODEL": TAG, "MB_S1_URL": fake.url, "MB_S1_PINS": str(pins)}


# Bindings

def test_binding_questions_and_conditional_use(fake) -> None:
    child = make_child(fake)
    statuses = set()
    for i, label in enumerate(["Yes", "No", "Alpha", "Over"] * 4):
        item = {"ctx": ctx(f"b{i}", f"Will Example Coin close above {100 + i} on June {i + 1}?", label)}
        fake.requests.clear()
        result, _ = child.decide("decide_binding", [item])
        result = result[0]
        assert not isinstance(result, ChildError)
        check_binding(result)
        names = [sorted(r["questions"]) for r in fake.requests]
        binary = label in ("Yes", "No", "Over")
        assert names[0] == ["status"]  # every question is asked alone
        statuses.add(result["status"])
        if result["status"] == "unresolved":
            assert names == [["status"], ["missing"]] and result["class"] is None and len(result["missing"]) == 1
            assert result["missing"][0] in S1.MISSING
        else:
            # a binary label's negation is read from the label, never asked; another label's is asked, alone, when bound
            assert names == ([["status"]] if binary else [["status"], ["negated"]])
            assert result["class"] == class_key(item["ctx"]["question"], label)
            if binary:
                assert result["negated"] == (label == "No")
    assert statuses == {"bound", "unresolved"}


def test_keep_alive_and_model_are_sent_on_every_request(fake) -> None:
    child = make_child(fake)
    child.decide("decide_binding", [{"ctx": ctx("b", "Will X win?", "Alpha")}])
    assert fake.requests and all(r["keep_alive"] == -1 and r["model"] == TAG for r in fake.requests)


# Relations

def test_relations_send_one_question_per_request_and_keep_the_order(fake) -> None:
    child = make_child(fake)
    a1, a2 = ctx("a1", "Will A1 win?", "Yes"), ctx("a2", "Will A2 win?", "Yes")
    items: list[dict[str, Any]] = []
    for i in range(70):  # a1 gets 70 pairs, a2 gets 3, interleaved
        items.append({"a": a1, "b": ctx(f"b1-{i}", f"Will B1 {i} win?", "Yes")})
        if i < 3:
            items.append({"a": a2, "b": ctx(f"b2-{i}", f"Will B2 {i} win?", "Yes")})
    fake.requests.clear()
    results, _ = child.decide("decide_relation", items)
    assert len(fake.requests) == len(items)  # one request per pair
    assert all(len(r["questions"]) == 1 for r in fake.requests)
    assert all(r["state"].split("\n\n")[1].startswith("Proposition A: ") for r in fake.requests)
    for item, result in zip(items, results):
        assert result == child.decide("decide_relation", [item])[0][0]  # the answer does not depend on the others


def test_every_op_sends_one_question_per_request(fake) -> None:
    child = make_child(fake)
    fake.requests.clear()
    child.decide("decide_binding", [{"ctx": ctx(f"b{i}", f"Will X{i} win?", ["Yes", "Alpha", "No"][i % 3])}
                                    for i in range(6)])
    child.decide("decide_relation", [{"a": ctx("a", "Will A win?", "Yes"), "b": ctx(f"r{i}", f"Will R{i}?", "No")}
                                     for i in range(6)])
    child.decide("decide_group", [{"ctx": group_of(s, 3 + s % 3)} for s in range(12)])
    assert fake.requests
    assert all(len(r["questions"]) == 1 for r in fake.requests)


def test_the_server_request_never_carries_two_questions(fake) -> None:
    child = make_child(fake)
    two = {"q1": {"type": "choice", "instructions": "Is it?", "criteria": {"yes": "y", "no": "n"}}, "q2": {"type": "choice", "instructions": "Is it?", "criteria": {"yes": "y", "no": "n"}}}
    with pytest.raises(ValueError, match="limit is 1"):
        child._ask("state", two)
    assert S1.MAX_QUESTIONS == 1


def expected_relation(a: dict, b: dict) -> dict[str, Any]:
    question = S1._relation_question(a, b)
    lp = {k: math.log(max(p, S1.P_FLOOR)) for k, p in fake_probs(S1._frame(S1._prop("A", a)), question).items()}
    named = {k: v for k, v in lp.items() if k != "NONE"}
    best = max(named, key=lambda k: named[k])
    return {"relation": max(lp, key=lambda k: lp[k]), "conf": named[best] - lp["NONE"]}


def test_relation_conf_is_best_named_margin_over_none_and_none_is_argmax(fake) -> None:
    child = make_child(fake)
    a_items = [ctx(f"a{i}", f"Will Example {i} win?", "Yes") for i in range(4)]
    b_items = [ctx(f"b{j}", f"Will Example {j} lose?", "No" if j % 2 else "Yes") for j in range(10)]
    items = [{"a": a, "b": b} for a in a_items for b in b_items]
    results, _ = child.decide("decide_relation", items)
    names = set()
    for item, result in zip(items, results):
        want = expected_relation(item["a"], item["b"])
        assert {"relation": result["relation"], "conf": result["conf"]} == want
        assert math.isfinite(result["conf"])
        names.add(result["relation"])
    assert "NONE" in names and names & {"IMPLIES", "IMPLIED_BY", "EXCLUDES"}


def test_zero_probability_is_clamped_so_conf_is_finite(fake) -> None:
    child = make_child(fake)
    a, b = ctx("a", "ZEROP Will A win?", "Yes"), ctx("b", "Will B win?", "Yes")
    results, _ = child.decide("decide_relation", [{"a": a, "b": b}])
    assert results[0]["relation"] == S1.relation_order(a, b)[0]  # the one option with probability 1
    assert abs(results[0]["conf"]) == pytest.approx(-math.log(S1.P_FLOOR))


def test_an_exact_relation_tie_goes_to_the_first_of_the_pairs_menu() -> None:
    a, b = {"id": "a1"}, {"id": "b1"}
    order = S1.relation_order(a, b)
    tie = {"type": "choice", "probabilities": dict.fromkeys(S1.RELATION_CHOICES, 0.2)}
    decision = S1._relation_decision(tie, order)
    assert decision["relation"] == order[0]
    assert decision["conf"] == pytest.approx(0.0)  # ln p(best named) - ln p(NONE) with equal probabilities


def test_prompt_sha256_is_pinned() -> None:
    # A change to any prompt, option, menu rule or format string changes this digest: update it with the change.
    # Version 6 added the rule-card lead ("rules", with its "card_join" separator), the base_rules and alt_rules instructions
    # and the card probes of conform (version 5: 15c9c1ce...).
    assert S1.PROMPT_SHA256 == "d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44"  # v7: alt_rules wording


def test_relation_menu_order_is_a_per_pair_permutation() -> None:
    a = ctx("a", "Will A win?", "Yes")
    orders = {S1.relation_order(a, ctx(f"b{i}", "Will B win?", "Yes")) for i in range(30)}
    assert all(sorted(order) == sorted(S1.RELATION_CHOICES) for order in orders)
    assert len(orders) > 1  # the menu is not the same for every pair


def test_relation_menu_offers_equiv_with_its_criteria_text(fake) -> None:
    child = make_child(fake)
    a, b = ctx("a", "Will A win?", "Yes"), ctx("b", "Will B win?", "Yes")
    child.decide("decide_relation", [{"a": a, "b": b}])
    criteria = fake.requests[-1]["questions"]["relation"]["criteria"]
    assert set(criteria) == {"EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE"}
    assert list(criteria) == list(S1.relation_order(a, b))  # the wire order is the pair's menu order
    assert criteria["EQUIV"] == "A and B mean the same: each holds exactly when the other does"


def test_relation_answers_carry_five_probs_and_map_equiv(fake) -> None:
    child = make_child(fake)
    a, b = ctx("a", "ONEQUIV Will A win?", "Yes"), ctx("b", "Will B win?", "Yes")
    (result,), _ = child.decide("decide_relation", [{"a": a, "b": b}])
    assert result["relation"] == "EQUIV"
    assert result["probs"] == {k: float(k == "EQUIV") for k in S1.RELATION_CHOICES}
    assert result["conf"] == pytest.approx(-math.log(S1.P_FLOOR))  # ln p(EQUIV) - ln p(NONE), NONE clamped
    assert check_relation_decision(result) == ("EQUIV", result["conf"])


def test_binding_and_group_answers_carry_the_probs_of_their_question(fake) -> None:
    child = make_child(fake)
    item = ctx("b", "Will Example Coin close above 100?", "Alpha")
    (binding,), _ = child.decide("decide_binding", [{"ctx": item}])
    assert binding["probs"] == fake_probs(S1._frame(S1._record(item)), S1._choice("status"))
    assert set(binding["probs"]) == {"bound", "unresolved"} and check_binding(binding)["status"] in ("bound", "unresolved")
    for seed in range(12):
        group = group_of(seed, 3 + seed % 3)
        (answer,), _ = child.decide("decide_group", [{"ctx": group}])
        state = S1._frame(S1.T["group_state"].format(listing=S1._listing(group["members"])))
        assert answer["probs"] == fake_probs(state, S1._choice("kind"))
        assert set(answer["probs"]) == set(S1.GROUP_CHOICES)
        if max(answer["probs"], key=answer["probs"].get) == "none":
            assert answer["groups"] == []


# Reference orders: hf_child.relation_order's exact body (crc32-seeded shuffle of its list, with the pair ids as literals;
# hf_child itself imports torch, so the test does not import it) applied to the five v3 choices, EQUIV first. Each entry:
# (a id, b id, expected menu order).
HF_RELATION_ORDERS = [
    ("a1", "b1", ("IMPLIES", "NONE", "EXCLUDES", "EQUIV", "IMPLIED_BY")),
    ("g:rel:1", "g:rel:2", ("NONE", "IMPLIES", "EXCLUDES", "IMPLIED_BY", "EQUIV")),
    ("toy:002aad3252abe6c2:1", "toy:00392d459f77e88c:2", ("NONE", "EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES")),
    ("x", "y", ("EQUIV", "EXCLUDES", "IMPLIES", "NONE", "IMPLIED_BY")),
    ("b", "a", ("IMPLIES", "IMPLIED_BY", "EQUIV", "NONE", "EXCLUDES")),
]


@pytest.mark.parametrize("a_id, b_id, expected", HF_RELATION_ORDERS)
def test_relation_order_is_hf_child_shuffle_of_the_five_choices(a_id: str, b_id: str, expected: tuple) -> None:
    assert S1.relation_order({"id": a_id}, {"id": b_id}) == expected


def test_over_long_relation_instructions_are_refused_not_sent(fake) -> None:
    """The server cuts instructions past about 160 tokens and answers 200. The child refuses such an item instead, and the
    other items are answered."""
    child = make_child(fake)
    a = ctx("a", "Will A win?", "Yes")
    long_b = ctx("long", "Will " + "very " * 80 + "win?", "Yes")
    items = [{"a": a, "b": ctx("b1", "Will b1 win?", "Yes")}, {"a": a, "b": long_b}, {"a": a, "b": ctx("b2", "Will b2?", "Yes")}]
    fake.requests.clear()
    results, _ = child.decide("decide_relation", items)
    assert isinstance(results[1], ChildError) and "exceed" in str(results[1])
    assert isinstance(results[0], dict) and isinstance(results[2], dict)
    assert sum(len(r["questions"]) for r in fake.requests) == 2
    assert all(len(q["instructions"]) <= S1.MAX_INSTRUCTION_CHARS for r in fake.requests for q in r["questions"].values())


# Groups

def group_of(seed: int, size: int) -> dict[str, Any]:
    return {"group_id": f"g{seed}", "source": "s1", "members": [
        ctx(f"g{seed}:{j}", f"Will team {seed}-{j} win cup?", "Yes") for j in range(size)]}


def test_group_kinds_k_and_chain_order_are_valid_decisions(fake) -> None:
    child = make_child(fake, parallel=1)
    kinds: dict[str, int] = {}
    for seed in range(40):
        group = group_of(seed, 2 + seed % 5)
        fake.requests.clear()
        results, _ = child.decide("decide_group", [{"ctx": group}])
        member_ids = {m["id"] for m in group["members"]}
        decisions = results[0]["groups"]
        assert len(decisions) <= 3  # a chain, and up to two bounds
        for decision in decisions:
            check_group(decision, member_ids)
            kinds[decision["kind"]] = kinds.get(decision["kind"], 0) + 1
            if decision["kind"] in ("at_least_k", "at_most_k"):
                assert 1 <= decision["k"] <= len(group["members"])
            if decision["kind"] == "ordered_chain":
                assert sorted(decision["members"]) == sorted(m["id"] for m in chain_members(group["members"]))
                assert sum("order" in r["questions"] for r in fake.requests) == len(decision["members"]) - 1
        assert all(not isinstance(r, ChildError) for r in results)
    assert {"ordered_chain", "at_least_k"} <= set(kinds)


def test_group_decision_shape_matches_hf_child(fake) -> None:
    child = make_child(fake)
    results, _ = child.decide("decide_group", [{"ctx": group_of(1, 3)}])
    for decision in results[0]["groups"]:
        assert set(decision) <= {"kind", "members", "k", "direction", "scope", "conf"}
        assert decision["scope"] is None
        assert math.isfinite(decision["conf"])


def test_every_criteria_value_sent_is_a_non_empty_string(fake) -> None:
    child = make_child(fake)
    fake.requests.clear()
    child.decide("decide_binding", [{"ctx": ctx(f"b{i}", f"Will X{i} win?", ["Yes", "Alpha"][i % 2])} for i in range(4)])
    child.decide("decide_relation", [{"a": ctx("a", "Will A win?", "Yes"), "b": ctx(f"r{i}", f"Will R{i}?", "No")}
                                     for i in range(4)])
    child.decide("decide_group", [{"ctx": group_of(s, 3 + s % 3)} for s in range(12)])
    sent = [q for r in fake.requests for q in r["questions"].values()]
    assert sent
    for question in sent:
        for value in question["criteria"].values():
            assert isinstance(value, str) and value.strip(), question


# Version 5: group bounds and reading rows. script[instructions] is the option the fake server favours (0.8) for the question
# with those instructions; the other questions keep the fake's own probabilities.

@pytest.fixture()
def script(monkeypatch) -> dict[str, str]:
    favoured: dict[str, str] = {}
    fake_answer = answer_of

    def scripted(state: str, question: dict[str, Any]) -> dict[str, Any]:
        pick = favoured.get(question["instructions"])
        if pick is None:
            return fake_answer(state, question)
        keys = list(question["criteria"])
        probs = {k: 0.8 if k == pick else 0.2 / (len(keys) - 1) for k in keys}
        return {"type": "choice", "choice": pick, "probabilities": probs, "confidence": 0.8}

    monkeypatch.setattr(sys.modules[__name__], "answer_of", scripted)
    return favoured


def group_decisions(child: S1.SystemOneChild, favoured: dict[str, str], group: dict[str, Any], **picks: str) -> list[Any]:
    """The group decisions of one candidate set, with the answers of the questions named in picks (kind, k, lower, upper)."""
    favoured.clear()
    for name, pick in picks.items():
        favoured[S1.PROMPTS[name]["instructions"]] = pick
    (answer,), _ = child.decide("decide_group", [{"ctx": group}])
    member_ids = {m["id"] for m in group["members"]}
    for decision in answer["groups"]:
        check_group(decision, member_ids)
    return answer["groups"]


def shape(decisions: list[dict[str, Any]]) -> list[tuple[str, int | None]]:
    return [(d["kind"], d.get("k")) for d in decisions]


def test_at_least_k_asks_the_upper_bound(fake, script) -> None:
    child = make_child(fake)
    group = group_of(10, 4)
    fake.requests.clear()
    assert shape(group_decisions(child, script, group, kind="at_least_k", k="2", upper="no_limit")) == [("at_least_k", 2)]
    assert [list(r["questions"]) for r in fake.requests] == [["kind"], ["k"], ["upper"]]
    assert shape(group_decisions(child, script, group, kind="at_least_k", k="2", upper="3")) == [("at_least_k", 2),
                                                                                                  ("at_most_k", 3)]
    assert shape(group_decisions(child, script, group, kind="at_least_k", k="2", upper="2")) == [("at_least_k", 2),
                                                                                                 ("at_most_k", 2)]
    assert shape(group_decisions(child, script, group, kind="at_least_k", k="1", upper="1")) == [("exactly_one", None)]


def test_at_most_k_asks_the_lower_bound(fake, script) -> None:
    child = make_child(fake)
    group = group_of(11, 4)
    fake.requests.clear()
    assert shape(group_decisions(child, script, group, kind="at_most_k", k="2", lower="0")) == [("at_most_k", 2)]
    assert [list(r["questions"]) for r in fake.requests] == [["kind"], ["k"], ["lower"]]
    # the kind's own bound (at_most_k) comes first, then the bound the follow-up asked
    assert shape(group_decisions(child, script, group, kind="at_most_k", k="2", lower="1")) == [("at_most_k", 2),
                                                                                               ("at_least_k", 1)]
    assert shape(group_decisions(child, script, group, kind="at_most_k", k="2", lower="2")) == [("at_most_k", 2),
                                                                                               ("at_least_k", 2)]
    assert shape(group_decisions(child, script, group, kind="at_most_k", k="1", lower="1")) == [("exactly_one", None)]
    assert shape(group_decisions(child, script, group, kind="at_most_k", k="1", lower="0")) == [("at_most_one", None)]


def test_at_most_one_asks_the_lower_bound(fake, script) -> None:
    child = make_child(fake)
    group = group_of(12, 4)
    fake.requests.clear()
    assert shape(group_decisions(child, script, group, kind="at_most_one", lower="0")) == [("at_most_one", None)]
    assert [list(r["questions"]) for r in fake.requests] == [["kind"], ["lower"]]
    assert shape(group_decisions(child, script, group, kind="at_most_one", lower="1")) == [("exactly_one", None)]


def test_exactly_one_asks_no_bound(fake, script) -> None:
    child = make_child(fake)
    fake.requests.clear()
    assert shape(group_decisions(child, script, group_of(13, 4), kind="exactly_one")) == [("exactly_one", None)]
    assert [list(r["questions"]) for r in fake.requests] == [["kind"]]


def test_an_ordered_chain_asks_both_bounds(fake, script) -> None:
    child = make_child(fake)
    group = group_of(14, 4)  # four named outcomes: a chain of four, so three order rounds
    fake.requests.clear()
    decisions = group_decisions(child, script, group, kind="ordered_chain", lower="2", upper="3")
    assert shape(decisions) == [("ordered_chain", None), ("at_least_k", 2), ("at_most_k", 3)]
    assert len(decisions[0]["members"]) == 4 and decisions[0]["direction"] == "forward"
    assert [list(r["questions"]) for r in fake.requests] == [["kind"], ["order"], ["order"], ["order"], ["lower"], ["upper"]]
    assert shape(group_decisions(child, script, group, kind="ordered_chain", lower="0", upper="no_limit")) == [
        ("ordered_chain", None)]


@pytest.mark.parametrize("kind, picks, requests", [
    ("exactly_one", {}, 1), ("at_most_one", {"lower": "0"}, 2), ("at_least_k", {"k": "2", "upper": "no_limit"}, 3),
    ("at_most_k", {"k": "2", "lower": "0"}, 3), ("ordered_chain", {"lower": "0", "upper": "no_limit"}, 1 + 3 + 2),
])
def test_request_count_per_group_question(fake, script, kind: str, picks: dict[str, str], requests: int) -> None:
    child = make_child(fake)
    fake.requests.clear()
    group_decisions(child, script, group_of(15, 4), kind=kind, **picks)  # four members: a chain of four
    assert len(fake.requests) == requests
    assert all(len(r["questions"]) == 1 for r in fake.requests)


def test_a_set_over_max_k_members_asks_no_bound(fake, script) -> None:
    child = make_child(fake)
    fake.requests.clear()
    decisions = group_decisions(child, script, group_of(16, S1.MAX_K + 1), kind="at_least_k", k="2", upper="3")
    assert shape(decisions) == [("at_least_k", 2)]
    assert [list(r["questions"]) for r in fake.requests] == [["kind"], ["k"]]


def test_a_chain_that_drops_a_market_side_asks_no_chain_or_bound_questions(fake, script) -> None:
    child = make_child(fake)
    group = {"group_id": "m", "source": "s1", "members": [ctx("m1", "Will X win?", "Yes"), ctx("m2", "Will X win?", "No"),
                                                          ctx("m3", "Will Y win?", "Yes")]}  # m2 is the other side of m1
    fake.requests.clear()
    assert group_decisions(child, script, group, kind="ordered_chain", lower="1", upper="2") == []
    assert [list(r["questions"]) for r in fake.requests] == [["kind"]]


def test_hard_fixture_group_rows_have_at_most_max_k_members() -> None:
    """A set over MAX_K members gets no bound questions, so a fixture group row that large would be asked without its bounds."""
    sizes = []
    for split in ("dev", "test"):
        path = WORKTREE / "fixtures" / f"mentat_hard_{split}" / "candidates.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("op") == "decide_group":
                sizes.append(len(row["members"]))
    assert sizes  # the fixtures have group rows
    assert max(sizes) <= S1.MAX_K, max(sizes)


# The hello model block of the version-5 child (df8b9de) as a checkpoint records it, with the same server and model.
V5_HELLO_MODEL = {"id": TAG, "revision": DIGEST, "kind": "local", "ollama_version": VERSION, "child_version": "5",
                  "prompt_sha256": "15c9c1ce78e6faae14a17dc750f7bc0ec9b10b948f548a2c17afc2f1975f6833"}
# The version-6 block (1fb6e3d): rule cards, the old alt_rules wording.
V6_HELLO_MODEL = {**V5_HELLO_MODEL, "child_version": "6",
                  "prompt_sha256": "8cdfc8516032f40aa16cfcf5ba9ec092763551adc84a26d260ae10bf1c6d0000"}


def test_checkpoints_of_older_child_versions_do_not_resume_on_version_seven(fake) -> None:
    """The checkpoint policy records the hello model block. The current block differs from a version-4 block (no child version
    or prompt hash) and from the literal version-5 and version-6 blocks (their child versions and prompt hashes), so a build
    started on any of them refuses to resume on version 7."""
    from mentat_bench.harness import fixed
    v7 = make_child(fake).describe()["model"]
    v4 = {k: v for k, v in v7.items() if k not in ("child_version", "prompt_sha256")}
    base = {key: f"value of {key}" for key in fixed.RESUME_POLICY_KEYS if key != "model"}
    for checkpoint in (v4, V5_HELLO_MODEL, V6_HELLO_MODEL):
        with pytest.raises(ValueError, match="model block differs"):
            fixed._check_resume_policy({**base, "model": checkpoint}, {**base, "model": v7}, None)
    fixed._check_resume_policy({**base, "model": v7}, {**base, "model": v7}, None)  # the same child: resumes


def test_describe_records_version_seven_and_the_prompt_hash(fake) -> None:
    desc = make_child(fake).describe()
    assert desc["version"] == "7"
    assert desc["prompt_sha256"] == S1.PROMPT_SHA256


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# Digests of the version-4 questions and states, computed from the version-4 module: a version-5 change to any of them fails here.
V4_DIGESTS = {
    "status": "15ad8b10e3c1b3310855d730e82e989e0da3b12e9d4ee731ff712a8f93c275b3",
    "negated": "96e5f73c538e81cceb66272f6ac4ed540f092233f133bbd3a6195b5ab1460346",
    "missing": "0e3a1d471e72208abb7e1412d381c8f78fdceaf3232d92ee1d11fe7381f66a63",
    "kind": "9eca7510bfad02c7e754bc213ed9248fa7fab682821367dbcd321bbc1308ed38",
    "k": "62d2bd141c9b7b3bdb683c050ed856d516dafb2c8f9940dddca0eba2b4dacc6f",
    "order": "ae02322ff6f8f5c8f6e01157dbabbd0f249bf0f75720db1f7956e0801f7d1e88",
    "relation": "526a21713026bf2d62a1d0d8b31d7280ed7768e407b58930877c4a969a0dd38d",
    "binding_state": "c4eb553a179c0924d3c22e690e0da948c8ff68bf5a5652f16fbb5c5df31ebf07",
    "group_state": "284db4036f99e0ead006ee202b65910c76630ca3304462360998c24fec721820",
    "chain_state": "e9a1791390451e7358b5be9fe8ebb36fd88aecd4b9684e0e87ffcc2fbec6883e",
}


def test_version_four_question_types_are_unchanged() -> None:
    a, b = ctx("a1", "Will A win?", "Yes"), ctx("b1", "Will B win?", "No")
    sent = {
        "status": S1._choice("status"), "negated": S1._choice("negated"), "missing": S1._choice("missing"),
        "kind": S1._choice("kind"),
        "k": S1._choice("k", criteria={str(k): S1.PROMPTS["k"]["criteria"][str(k)] for k in range(1, 4)}),
        "order": S1._choice("order", criteria={letter: S1.PROMPTS["order"]["option"].format(letter=letter) for letter in "ABC"}),
        "relation": S1._relation_question(a, b),
        "binding_state": S1._frame(S1._record(a)),
        "group_state": S1._frame(S1.T["group_state"].format(listing=S1._listing([a, b]))),
        "chain_state": S1._frame(S1.T["chain_state"].format(listing="A. x [label: Yes]")),
    }
    assert {name: digest(value) for name, value in sent.items()} == V4_DIGESTS


def no_card_run(fake: FakeOllama) -> list[bytes]:
    """The request bodies, as sent, of a fixed set of outcomes with no rule card: bindings (both status paths), relations,
    groups (every kind) and a reading row."""
    child = make_child(fake, parallel=1)
    labels = ["Yes", "Alpha", "No", "Beta", "Over", "Gamma"]
    bindings = [{"ctx": ctx(f"b{i}", f"Will X{i} win?", labels[i % len(labels)])} for i in range(12)]
    relations = [{"a": ctx("a", "Will A win?", "Yes"), "b": ctx(f"r{i}", f"Will R{i} win?", "No")} for i in range(6)]
    groups = [{"ctx": group_of(seed, 3 + seed % 3)} for seed in range(40)] + [{"ctx": reading()}]
    fake.requests.clear()
    fake.raw_requests.clear()
    for op, items in (("decide_binding", bindings), ("decide_relation", relations), ("decide_group", groups)):
        child.decide(op, items)
    return fake.raw_requests


def raw_digest(raw: list[bytes]) -> str:
    """The SHA-256 of the request bodies as sent, one after another, each followed by a newline (a body has none)."""
    return hashlib.sha256(b"".join(body + b"\n" for body in raw)).hexdigest()


# The digest of no_card_run's request bodies from the version-5 child (df8b9de): a request with no rule card keeps its
# version-5 bytes.
V5_NO_CARD_DIGEST = "141d9aaf9b85c77fa58296b34a924d46db3d17a9967105845f06a779981b6037"

NO_CARD_QUESTIONS = {"status", "negated", "missing", "relation", "kind", "k", "order", "lower", "upper", "base", "alt"}


def test_no_card_requests_are_byte_identical_to_version_five(fake) -> None:
    raw = no_card_run(fake)
    assert {name for body in raw for name in json.loads(body)["questions"]} == NO_CARD_QUESTIONS
    assert raw_digest(raw) == V5_NO_CARD_DIGEST


ALT_INSTRUCTIONS = ("Which option matches a second, different reading of the proposition? "
                    "If it has no second reading, give the option that matches its literal reading. "
                    "Answer none if no option matches.")


def test_reading_and_bound_instructions_fit_the_limit() -> None:
    for name in ("base", "alt", "base_rules", "alt_rules", "lower", "upper"):
        assert len(S1.PROMPTS[name]["instructions"]) <= S1.MAX_INSTRUCTION_CHARS


def test_the_alt_prompt_and_the_reading_state_are_sent_as_written(fake, script) -> None:
    assert S1.PROMPTS["alt"]["instructions"] == ALT_INSTRUCTIONS
    assert S1.PROMPTS["base"]["instructions"] == ("Which option has the same meaning as the proposition, read most literally? "
                                                  "Answer none if no option does.")
    child = make_child(fake)
    fake.requests.clear()
    child.decide("decide_group", [{"ctx": reading()}])
    sent = {name: q["instructions"] for r in fake.requests for name, q in r["questions"].items()}
    assert sent == {"base": S1.PROMPTS["base"]["instructions"], "alt": ALT_INSTRUCTIONS}
    body = ("Proposition U: Will Team X win the cup? [label: Yes]\n"
            "Options (each label in brackets is part of that option's meaning):\n"
            + "\n".join(f"{letter}. Will option {i} happen? [label: Yes]" for i, letter in enumerate("ABCD")))
    assert all(r["state"] == S1._frame(body) for r in fake.requests)


# Reading rows: members[0] is the proposition, the rest its options.

def reading(options: int = 4) -> dict[str, Any]:
    return {"group_id": "r1", "source": "reading", "members": [ctx("r1:u", "Will Team X win the cup?", "Yes")]
            + [ctx(f"r1:o{i}", f"Will option {i} happen?", "Yes") for i in range(options)]}


def pair(u: str, c: str, reading_name: str) -> list[dict[str, Any]]:
    scope = {"axis": "reading", "values": [reading_name], "of": ["alt", "base"]}
    return [{"kind": "ordered_chain", "members": [u, c], "direction": "forward", "scope": scope},
            {"kind": "ordered_chain", "members": [c, u], "direction": "forward", "scope": scope}]


def with_conf(decisions: list[dict[str, Any]], conf: float) -> list[dict[str, Any]]:
    return [{**d, "conf": conf} for d in decisions]


def test_reading_different_base_and_alt_picks_give_one_scoped_pair_each(fake, script) -> None:
    child = make_child(fake)
    script[S1.PROMPTS["base"]["instructions"]] = "B"
    script[S1.PROMPTS["alt"]["instructions"]] = "D"
    fake.requests.clear()
    (answer,), _ = child.decide("decide_group", [{"ctx": reading()}])
    conf = math.log(0.8) - math.log(0.2 / 4)  # ln p(pick) - ln p(none), the other four options share 0.2 of it
    assert answer["groups"] == (with_conf(pair("r1:u", "r1:o1", "base"), conf) + with_conf(pair("r1:u", "r1:o3", "alt"), conf))
    assert [list(r["questions"]) for r in fake.requests] == [["base"], ["alt"]]
    check_group(answer["groups"][0], {m["id"] for m in reading()["members"]})


def test_reading_the_same_pick_for_both_readings(fake, script) -> None:
    child = make_child(fake)
    script[S1.PROMPTS["base"]["instructions"]] = "A"
    script[S1.PROMPTS["alt"]["instructions"]] = "A"
    (answer,), _ = child.decide("decide_group", [{"ctx": reading()}])
    conf = math.log(0.8) - math.log(0.2 / 4)
    assert answer["groups"] == with_conf(pair("r1:u", "r1:o0", "base") + pair("r1:u", "r1:o0", "alt"), conf)


def test_reading_none_emits_nothing_for_that_reading(fake, script) -> None:
    child = make_child(fake)
    script[S1.PROMPTS["base"]["instructions"]] = "none"
    script[S1.PROMPTS["alt"]["instructions"]] = "C"
    (answer,), _ = child.decide("decide_group", [{"ctx": reading()}])
    conf = math.log(0.8) - math.log(0.2 / 4)
    assert answer["groups"] == with_conf(pair("r1:u", "r1:o2", "alt"), conf)
    script[S1.PROMPTS["alt"]["instructions"]] = "none"
    (answer,), _ = child.decide("decide_group", [{"ctx": reading()}])
    assert answer["groups"] == []  # both readings none: an empty answer is valid


# League rule cards: the cards lead the state of a relation, a group (every question of it) and a reading; a binding has none.

CARD_A = ("Rules of the Alpha League and Alpha Cup. Teams: Ann, Bo.\nExactly one team wins the Cup.\n"
          "'By the break' has two readings: (1) final points; (2) final points plus the 1 point every team gets in the Shield "
          "round.")
CARD_B = "Rules of the Beta League. Teams: Cy, Di.\nAt most one team is relegated."
BASE_RULES = ("Under reading (1) of the league rules, which option means the same as U? "
              "Answer none if no option does.")
ALT_RULES = ("Under reading (2) of the league rules, which option means the same as U? If U's wording fixes one meaning "
             "(it uses no term with two readings, or it says which reading it means), give the option for that meaning. "
             "Answer none if no option does.")


def carded(oid: str, question: str, label: str, card: str) -> dict[str, Any]:
    return {**ctx(oid, question, label), "context": card}


def layout(*cards: str, body: str) -> str:
    """The state of a request with these rule cards: the system text, then the cards under "League rules:", then the body."""
    return S1._frame("League rules:\n" + "\n\n".join(cards) + "\n\n" + body)


def test_the_reading_instructions_with_a_card_are_as_written() -> None:
    assert S1.PROMPTS["base_rules"]["instructions"] == BASE_RULES
    assert S1.PROMPTS["alt_rules"]["instructions"] == ALT_RULES


def test_relation_state_leads_with_the_cards_of_a_and_of_b_once_each(fake) -> None:
    child = make_child(fake, parallel=1)
    a = carded("a", "Will A win?", "Yes", CARD_A)
    same, other = carded("b1", "Will B1 win?", "No", CARD_A), carded("b2", "Will B2 win?", "No", CARD_B)
    fake.requests.clear()
    child.decide("decide_relation", [{"a": a, "b": same}, {"a": a, "b": other}, {"a": a, "b": ctx("b3", "Will B3?", "No")}])
    states = [r["state"] for r in fake.requests]
    assert states == [layout(CARD_A, body=S1._prop("A", a)), layout(CARD_A, CARD_B, body=S1._prop("A", a)),
                      layout(CARD_A, body=S1._prop("A", a))]  # B without a card adds none
    assert all("League rules" not in q["instructions"] for r in fake.requests for q in r["questions"].values())


def test_every_group_question_leads_with_the_members_cards(fake, script) -> None:
    child = make_child(fake, parallel=1)
    members = [carded(f"g{j}", f"Will team {j} win cup?", "Yes", CARD_A) for j in range(4)]
    fake.requests.clear()
    group_decisions(child, script, {"group_id": "g", "source": "s1", "members": members}, kind="ordered_chain", lower="2",
                    upper="3")
    assert [list(r["questions"]) for r in fake.requests] == [["kind"], ["order"], ["order"], ["order"], ["lower"], ["upper"]]
    assert all(r["state"].startswith(S1._frame("League rules:\n" + CARD_A + "\n\n")) for r in fake.requests)
    assert fake.requests[0]["state"] == layout(CARD_A, body=S1.T["group_state"].format(listing=S1._listing(members)))


def test_a_group_across_leagues_shows_each_card_once_in_member_order(fake, script) -> None:
    child = make_child(fake, parallel=1)
    members = [carded("m0", "Will X win?", "Yes", CARD_A), carded("m1", "Will Y win?", "Yes", CARD_B),
               carded("m2", "Will Z win?", "Yes", CARD_A)]
    fake.requests.clear()
    group_decisions(child, script, {"group_id": "m", "source": "s1", "members": members}, kind="exactly_one")
    assert [r["state"] for r in fake.requests] == [layout(CARD_A, CARD_B, body=S1.T["group_state"].format(
        listing=S1._listing(members)))]


def test_bindings_never_carry_a_card(fake) -> None:
    child = make_child(fake, parallel=1)
    item = carded("b", "Will Ann win?", "Alpha", CARD_A)
    fake.requests.clear()
    child.decide("decide_binding", [{"ctx": item}])
    assert fake.requests and {r["state"] for r in fake.requests} == {S1._frame(S1._record(item))}


def test_a_reading_with_a_card_leads_with_it_and_asks_the_rule_readings(fake, script) -> None:
    child = make_child(fake, parallel=1)
    row = reading()
    row["members"][0]["context"] = CARD_A  # the card of U
    body = ("Proposition U: Will Team X win the cup? [label: Yes]\n"
            "Options (each label in brackets is part of that option's meaning):\n"
            + "\n".join(f"{letter}. Will option {i} happen? [label: Yes]" for i, letter in enumerate("ABCD")))
    fake.requests.clear()
    child.decide("decide_group", [{"ctx": row}])
    assert {name: q["instructions"] for r in fake.requests for name, q in r["questions"].items()} == {"base": BASE_RULES,
                                                                                                     "alt": ALT_RULES}
    assert all(r["state"] == layout(CARD_A, body=body) for r in fake.requests)


def test_a_card_too_long_for_the_context_is_an_abstention(fake) -> None:
    """The fake refuses a state with CTX400 as over the context: each question with that card abstains, and the rest are
    answered."""
    child = make_child(fake, parallel=1)
    big = CARD_A + " CTX400"
    relation = {"a": carded("a", "Will A win?", "Yes", big), "b": ctx("b", "Will b win?", "Yes")}
    group = {"ctx": {"group_id": "big", "source": "s1", "members": [carded("big:0", "Will team 0 win cup?", "Yes", big),
                                                                   ctx("big:1", "Will team 1 win cup?", "Yes")]}}
    reading_row = reading()
    reading_row["members"][0]["context"] = big
    results, _ = child.decide("decide_relation", [relation, {"a": ctx("a2", "Will A2 win?", "Yes"),
                                                             "b": ctx("b2", "Will b2?", "Yes")}])
    assert results[0] == {"relation": "NONE"} and isinstance(results[1], dict) and results[1]["relation"] in S1.RELATION_CHOICES
    results, _ = child.decide("decide_group", [group, {"ctx": reading_row}])
    assert results == [{"groups": []}, {"groups": []}]
    assert child.abstentions == {"decide_relation": 1, "decide_group": 2}


def test_a_blank_card_is_no_card_and_a_padded_card_is_sent_stripped(fake) -> None:
    child = make_child(fake, parallel=1)
    blank = carded("a", "Will A win?", "Yes", " \n ")
    padded = carded("p", "Will P win?", "Yes", f"\n{CARD_A}  \n")
    fake.requests.clear()
    child.decide("decide_relation", [{"a": blank, "b": ctx("b1", "Will B1 win?", "No")},
                                     {"a": padded, "b": ctx("b2", "Will B2 win?", "No")}])
    assert fake.requests[0]["state"] == S1._frame(S1._prop("A", blank))  # the v5 state
    assert fake.requests[1]["state"] == layout(CARD_A, body=S1._prop("A", padded))


# Scored by decision_scoring: the child's answers go through the real join, _terms_answer and reading_picks.

def child_answer(child: S1.SystemOneChild, favoured: dict[str, str], group: dict[str, Any], **picks: str) -> dict[str, Any]:
    """The answer of the child to one group or reading ctx, with the questions named in picks answered as given."""
    favoured.clear()
    for name, pick in picks.items():
        favoured[S1.PROMPTS[name]["instructions"]] = pick
    (answer,), _ = child.decide("decide_group", [{"ctx": group}])
    return answer


def scorer_load(tmp_path: Path, labels: list[dict], candidates: list[dict], decisions: list[dict]) -> list[dict]:
    """The items decision_scoring.load builds for these label, candidate and decision rows."""
    paths = []
    for name, rows in (("labels", labels), ("candidates", candidates), ("decisions", decisions)):
        paths.append(tmp_path / f"{name}.jsonl")
        paths[-1].write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    items, _ = DS.load(*paths)
    return items


def score_group(tmp_path: Path, terms: list[tuple[str, int | None]], members: list[str], answer: dict[str, Any]) -> dict:
    """The item of one group label with the given canonical terms, scored from the child's answer."""
    label = {"qid": "g", "op": "decide_group", "label": terms[0][0] if terms else "none", "k": terms[0][1] if terms else None,
             "terms": [list(t) for t in terms], "missing": None, "held_out": False, "stratum": "real"}
    candidate = {"qid": "g", "op": "decide_group", "members": members, "source": "curated"}
    decision = {"qid": "g", "op": "decide_group", "decision": answer, "conf": None, "probs": None, "error": None}
    (item,) = scorer_load(tmp_path, [label], [candidate], [decision])
    return item


def test_exactly_k_is_scored_right(fake, script, tmp_path) -> None:
    child = make_child(fake)
    group = group_of(20, 4)
    answer = child_answer(child, script, group, kind="at_least_k", k="2", upper="2")
    assert shape(answer["groups"]) == [("at_least_k", 2), ("at_most_k", 2)]
    item = score_group(tmp_path, group_terms(2, 2, 4, False), [m["id"] for m in group["members"]], answer)
    assert item["truth"] == item["pred"] == "at_least_k:2+at_most_k:2"


def test_at_most_k_with_a_lower_bound_is_scored_as_the_canonical_class(fake, script, tmp_path) -> None:
    child = make_child(fake)
    group = group_of(21, 4)
    answer = child_answer(child, script, group, kind="at_most_k", k="3", lower="1")
    assert shape(answer["groups"]) == [("at_most_k", 3), ("at_least_k", 1)]  # the kind's own term first
    members = [m["id"] for m in group["members"]]
    item = score_group(tmp_path, group_terms(1, 3, 4, False), members, answer)
    assert item["truth"] == item["pred"] == "at_least_k:1+at_most_k:3"
    wrong = score_group(tmp_path, group_terms(0, 3, 4, False), members, answer)  # the label without the lower bound
    assert wrong["truth"] == "at_most_k:3" and wrong["pred"] == "at_least_k:1+at_most_k:3"


def reading_items(tmp_path: Path, u: str, options: list[str], readings: dict[str, str | None], answer: dict[str, Any],
                  stratum: str) -> list[dict]:
    label = {"qid": "r", "op": "decide_group", "label": "reading", "k": None, "missing": None, "held_out": False,
             "stratum": stratum, "readings": readings, "order": None}
    candidate = {"qid": "r", "op": "decide_group", "members": [u] + options, "source": "reading"}
    decision = {"qid": "r", "op": "decide_group", "decision": answer, "conf": None, "probs": None, "error": None}
    return scorer_load(tmp_path, [label], [candidate], [decision])


def test_a_reading_with_the_same_pick_for_both_readings_is_right_on_a_decoy(fake, script, tmp_path) -> None:
    child = make_child(fake)
    row = reading()
    answer = child_answer(child, script, row, base="B", alt="B")
    u, options = row["members"][0]["id"], [m["id"] for m in row["members"][1:]]
    items = reading_items(tmp_path, u, options, {"base": options[1], "alt": options[1]}, answer, "reading_decoy")
    assert [(it["option"], it["pred"]) for it in items] == [("opt2", "opt2"), ("opt2", "opt2")]


def test_reading_picks_that_differ_and_a_none_are_scored_per_reading(fake, script, tmp_path) -> None:
    child = make_child(fake)
    row = reading()
    u, options = row["members"][0]["id"], [m["id"] for m in row["members"][1:]]
    answer = child_answer(child, script, row, base="B", alt="D")
    items = reading_items(tmp_path, u, options, {"base": options[1], "alt": options[3]}, answer, "reading")
    assert [(it["option"], it["pred"]) for it in items] == [("opt2", "opt2"), ("opt4", "opt4")]
    answer = child_answer(child, script, row, base="A", alt="none")
    items = reading_items(tmp_path, u, options, {"base": options[0], "alt": None}, answer, "reading")
    assert [(it["option"], it["pred"]) for it in items] == [("opt1", "opt1"), ("none", "none")]


# Round trip: the child's group answer, scored by decision_scoring's _terms_answer, is the intended class. Four members.
ROUND_TRIPS = [
    ("exactly_one", {}, (1, 1, False)),
    ("at_most_one", {"lower": "0"}, (0, 1, False)),
    ("at_most_one", {"lower": "1"}, (1, 1, False)),
    ("at_least_k", {"k": "2", "upper": "no_limit"}, (2, 4, False)),
    ("at_least_k", {"k": "2", "upper": "3"}, (2, 3, False)),
    ("at_least_k", {"k": "2", "upper": "2"}, (2, 2, False)),
    ("at_most_k", {"k": "3", "lower": "0"}, (0, 3, False)),
    ("at_most_k", {"k": "3", "lower": "2"}, (2, 3, False)),
    ("at_most_k", {"k": "2", "lower": "2"}, (2, 2, False)),
    ("at_most_k", {"k": "1", "lower": "1"}, (1, 1, False)),
    ("ordered_chain", {"lower": "0", "upper": "no_limit"}, (0, 4, True)),
    ("ordered_chain", {"lower": "2", "upper": "3"}, (2, 3, True)),
    ("ordered_chain", {"lower": "1", "upper": "1"}, (1, 1, True)),
    ("ordered_chain", {"lower": "4"}, (4, 4, True)),  # all hold: no upper question
]


@pytest.mark.parametrize("kind, picks, bounds", ROUND_TRIPS)
def test_the_child_answer_scores_as_its_intended_class(fake, script, kind: str, picks: dict[str, str], bounds: tuple) -> None:
    child = make_child(fake)
    group = group_of(30, 4)
    members = [m["id"] for m in group["members"]]
    lo, hi, chain = bounds
    answer = child_answer(child, script, group, kind=kind, **picks)
    scored, _ = DS._terms_answer(answer, members)
    assert scored == terms_class(group_terms(lo, hi, 4, chain))


def test_an_empty_reading_row_gives_no_group_and_no_question(fake) -> None:
    child = make_child(fake)
    fake.requests.clear()
    assert child._reading({"group_id": "r", "source": "reading", "members": [ctx("u", "Will Team X win?", "Yes")]}) == {
        "groups": []}
    assert fake.requests == []


# Refusals, abstentions and call-level failures

@pytest.mark.parametrize("marker, rejected", [("CTX400", 2), ("CTXSIZE", 2), ("FAIL413", 0)])
def test_an_over_context_question_is_abstained(fake, marker: str, rejected: int) -> None:
    """A question the server refuses for its input length is an abstention (NONE, no conf), not an item error, so it is
    not a failure in a row for the harness."""
    child = make_child(fake)
    bad_a = ctx("bad", f"{marker} Will bad win?", "Yes")
    good_a = ctx("good", "Will good win?", "Yes")
    items = [{"a": bad_a, "b": ctx("b1", "Will b1 win?", "Yes")}, {"a": good_a, "b": ctx("b2", "Will b2 win?", "Yes")},
             {"a": bad_a, "b": ctx("b3", "Will b3 win?", "Yes")}]
    results, _ = child.decide("decide_relation", items)
    assert results[0] == {"relation": "NONE"} and results[2] == {"relation": "NONE"}  # no conf: the model abstains
    assert isinstance(results[1], dict) and results[1]["relation"] in S1.RELATION_CHOICES
    assert child.rejected_400 == rejected
    assert child.abstentions == {"decide_relation": 2, "decide_group": 0}


def test_a_plain_400_is_an_item_error_not_an_abstention(fake) -> None:
    child = make_child(fake)
    bad_a = ctx("bad", "BAD400 Will bad win?", "Yes")
    good_a = ctx("good", "Will good win?", "Yes")
    items = [{"a": bad_a, "b": ctx("b1", "Will b1 win?", "Yes")}, {"a": good_a, "b": ctx("b2", "Will b2 win?", "Yes")},
             {"a": bad_a, "b": ctx("b3", "Will b3 win?", "Yes")}]
    results, _ = child.decide("decide_relation", items)
    assert isinstance(results[0], ChildError) and isinstance(results[2], ChildError)
    assert "HTTP 400" in str(results[0])
    assert isinstance(results[1], dict)
    assert child.rejected_400 == 2 and child.abstentions["decide_relation"] == 0


@pytest.mark.parametrize("marker, expected", [("CTX400", "NONE"), ("BAD400", "error"), ("FAIL413", "NONE")])
def test_a_refused_question_affects_only_that_item(fake, marker: str, expected: str) -> None:
    child = make_child(fake)
    a = ctx("a", "Will A win?", "Yes")
    bs = [ctx(f"b{i}", f"Will b{i} win?", "Yes") for i in range(8)]
    bs[5] = ctx("b5", f"{marker} will b5 win?", "Yes")
    results, _ = child.decide("decide_relation", [{"a": a, "b": b} for b in bs])
    for i, result in enumerate(results):
        if i != 5:
            assert isinstance(result, dict) and result["relation"] in S1.RELATION_CHOICES
    if expected == "error":
        assert isinstance(results[5], ChildError) and "HTTP 400" in str(results[5])
    else:
        assert results[5] == {"relation": "NONE"}


def test_a_flaky_500_is_retried_once_and_answered(fake) -> None:
    child = make_child(fake)
    results, _ = child.decide("decide_binding", [{"ctx": ctx("f", "FLAKY500 Will X win?", "Yes")}])
    assert check_binding(results[0])["status"] in ("bound", "unresolved")
    assert child.stats["decide_binding"]["retries_500"] == len(fake.flaky_seen) >= 1  # each request flakes once


def test_a_persistent_500_is_an_item_error_for_its_question_only(fake) -> None:
    """A 500 is retried once; a second 500 is an item error for its question. The others are answered."""
    child = make_child(fake)
    a = ctx("a", "Will A win?", "Yes")
    bs = [ctx(f"b{i}", f"Will b{i} win?", "Yes") for i in range(8)]
    bs[5] = ctx("b5", "FAIL500 will b5 win?", "Yes")
    results, _ = child.decide("decide_relation", [{"a": a, "b": b} for b in bs])
    assert [isinstance(r, ChildError) for r in results] == [i == 5 for i in range(8)]
    assert "HTTP 500" in str(results[5])
    assert child.stats["decide_relation"]["item_errors"] == 1
    assert child.stats["decide_relation"]["retries_500"] == 1


def test_a_500_through_handle_is_an_error_envelope_and_the_next_line_is_answered(fake) -> None:
    child = make_child(fake)
    bad = json.dumps({"op": "decide_binding", "id": 1, "ctx": ctx("bad", "FAIL500 Will bad win?", "Yes")})
    good = json.dumps({"op": "decide_binding", "id": 2, "ctx": ctx("ok", "Will ok win?", "Yes")})
    reply = handle(child, bad)
    assert reply["id"] == 1 and "HTTP 500" in reply["error"]
    answered = handle(child, good)
    assert answered["id"] == 2 and "error" not in answered and check_binding(answered)["status"] in ("bound", "unresolved")


def test_a_500_in_a_binding_and_a_group_batch_is_an_item_error(fake) -> None:
    child = make_child(fake)
    group = group_of(5, 3)
    group["members"][0] = ctx("g5:0", "FAIL500 Will team 5 win cup?", "Yes")
    line = json.dumps({"op": "batch", "id": 9, "calls": [
        {"op": "decide_binding", "ctx": ctx("bad", "FAIL500 Will bad win?", "Yes")},
        {"op": "decide_binding", "ctx": ctx("ok", "Will ok win?", "Yes")},
        {"op": "decide_group", "ctx": group},
        {"op": "decide_group", "ctx": group_of(6, 3)},
    ]})
    reply = handle(child, line)
    assert "error" not in reply and len(reply["results"]) == 4
    rows = reply["results"]
    assert "HTTP 500" in rows[0]["error"] and "error" not in rows[1]
    assert "HTTP 500" in rows[2]["error"] and "error" not in rows[3]


@pytest.mark.parametrize("marker", ["CTX400UPPER", "CTXSIZEUPPER"])
def test_the_context_text_is_matched_in_any_case(fake, marker: str) -> None:
    child = make_child(fake)
    results, _ = child.decide("decide_relation", [{"a": ctx("a", f"{marker} Will A win?", "Yes"),
                                                   "b": ctx("b", "Will b win?", "Yes")}])
    assert results == [{"relation": "NONE"}]  # an abstention: the upper-case text is the context error
    assert child.abstentions["decide_relation"] == 1


@pytest.mark.parametrize("message, over", [
    ("the prompt is longer than the context length currently available to the model", True),
    ("The Prompt Is LONGER Than The Context Length", True),
    ("request (9280 tokens) exceeds the available context size (8192 tokens), try increasing it", True),
    ("REQUEST (9280 TOKENS) EXCEEDS THE AVAILABLE CONTEXT SIZE (8192 TOKENS)", True),
    ("invalid request: question text is not allowed", False),
    ("the context of this model is 8192 tokens", False),
])
def test_the_two_over_context_texts_are_recognized_in_any_case(message: str, over: bool) -> None:
    assert S1.is_over_context_text(message) is over
    assert S1._Refused(400, message).over_context is over
    assert S1._Refused(413, message).over_context is True  # a 413 is over the size limit whatever its text


def test_mb_s1_stats_file_holds_the_cumulative_counts_of_the_calls_that_returned(fake, tmp_path: Path) -> None:
    stats_file = tmp_path / "child_stats.json"
    child = S1.SystemOneChild(TAG, fake.url, DIGEST, VERSION, timeout=10.0, stats_path=str(stats_file))
    a = ctx("a", "Will A win?", "Yes")
    items = [{"a": a, "b": ctx("b1", "CTX400 Will b1 win?", "Yes")}, {"a": a, "b": ctx("b2", "Will b2 win?", "Yes")},
             {"a": a, "b": ctx("b3", "Will " + "very " * 80 + "win?", "Yes")}]
    fake.requests.clear()
    child.decide("decide_relation", items)
    doc = json.loads(stats_file.read_text(encoding="utf-8"))
    relation = doc["ops"]["decide_relation"]
    assert doc["model"] == TAG
    assert relation == {"units": 3, "requests": len(fake.requests), "abstained_overlength": 1, "item_errors": 1,
                        "retries_500": 0, "refused_instructions": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["child_stats.json"]
    child.decide("decide_binding", [{"ctx": ctx("ok", "Will ok win?", "Yes")}])
    before = json.loads(stats_file.read_text(encoding="utf-8"))
    with pytest.raises(urllib.error.HTTPError):  # a call that raises counts nothing
        child.decide("decide_binding", [{"ctx": ctx("bad", "FAIL404 Will bad win?", "Yes")}])
    after = json.loads(stats_file.read_text(encoding="utf-8"))
    assert after["ops"] == before["ops"] and after["ops"]["decide_relation"] == relation
    assert after["ops"]["decide_binding"]["units"] == 1


def test_an_over_context_binding_is_an_item_error(fake) -> None:
    child = make_child(fake)
    results, _ = child.decide("decide_binding", [{"ctx": ctx("x", "CTX400 Will x happen?", "Yes")},
                                                 {"ctx": ctx("y", "Will y happen?", "Yes")}])
    assert isinstance(results[0], ChildError) and "HTTP 400" in str(results[0])
    assert check_binding(results[1])["status"] in ("bound", "unresolved")
    assert child.abstentions == {"decide_relation": 0, "decide_group": 0}


def test_an_over_context_group_has_no_decision_and_is_counted(fake) -> None:
    child = make_child(fake)
    big = group_of(2, 3)
    big["members"][0] = ctx("big:0", "CTX400 Will team big win cup?", "Yes")
    results, _ = child.decide("decide_group", [{"ctx": big}, {"ctx": group_of(3, 3)}])
    assert results[0] == {"groups": []}
    assert all(not isinstance(r, ChildError) for r in results)
    assert child.abstentions["decide_group"] == 1


def test_a_plain_400_in_a_group_is_an_item_error(fake) -> None:
    child = make_child(fake)
    bad = group_of(4, 3)
    bad["members"][0] = ctx("bad:0", "BAD400 Will team bad win cup?", "Yes")
    results, _ = child.decide("decide_group", [{"ctx": bad}])
    assert isinstance(results[0], ChildError) and child.abstentions["decide_group"] == 0


@pytest.mark.parametrize("marker", ["BADZERO", "BADSUM", "BADKEY", "BADNAN", "NOANSWER", "BIGINT"])
def test_a_degenerate_answer_fails_only_its_item(fake, marker: str) -> None:
    child = make_child(fake)
    a = ctx("a", "Will A win?", "Yes")
    bs = [ctx(f"b{i}", f"Will b{i} win?", "Yes") for i in range(4)]
    bs[2] = ctx("b2", f"{marker} will b2 win?", "Yes")
    results, _ = child.decide("decide_relation", [{"a": a, "b": b} for b in bs])
    assert [isinstance(r, ChildError) for r in results] == [False, False, True, False]
    assert all(isinstance(r, dict) and math.isfinite(r["conf"]) for i, r in enumerate(results) if i != 2)


def test_a_non_json_reply_fails_the_items_of_its_request(fake) -> None:
    child = make_child(fake)
    bad_a, good_a = ctx("bad", "BADJSON Will A win?", "Yes"), ctx("good", "Will good win?", "Yes")
    results, _ = child.decide("decide_relation", [{"a": bad_a, "b": ctx("b1", "Will b1?", "Yes")},
                                                  {"a": good_a, "b": ctx("b2", "Will b2?", "Yes")}])
    assert isinstance(results[0], ChildError) and "not JSON" in str(results[0])
    assert isinstance(results[1], dict)


def test_probabilities_must_sum_to_one_within_a_hundredth() -> None:
    def scores(values: list[float]) -> dict[str, float]:
        answer = {"type": "choice", "probabilities": dict(zip(("A", "B", "C", "D"), values))}
        return S1._probs(answer, ("A", "B", "C", "D"))

    assert set(scores([0.25, 0.25, 0.25, 0.255])) == {"A", "B", "C", "D"}  # sum 1.005
    assert set(scores([0.25, 0.25, 0.25, 0.245])) == {"A", "B", "C", "D"}  # sum 0.995
    for values in ([0.25, 0.25, 0.25, 0.27], [0.25, 0.25, 0.25, 0.22]):  # sums 1.02 and 0.97
        with pytest.raises(ChildError):
            scores(values)


def test_a_fatal_error_cancels_the_queued_requests(fake) -> None:
    child = make_child(fake, parallel=1)
    items = [{"ctx": ctx("bad", "FAIL404 Will bad win?", "Yes")}]
    items += [{"ctx": ctx(f"b{i}", f"Will b{i} win?", "Yes")} for i in range(49)]
    fake.requests.clear()
    with pytest.raises(urllib.error.HTTPError):
        child.decide("decide_binding", items)
    assert len(fake.requests) < len(items)  # the queued requests were cancelled, not sent


def test_connection_refused_fails_the_call(fake) -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # nothing listens on this port now
    child = S1.SystemOneChild(TAG, f"http://127.0.0.1:{port}", DIGEST, VERSION, timeout=5.0)
    with pytest.raises(urllib.error.URLError):
        child.decide("decide_binding", [{"ctx": ctx("a", "Will X win?", "Yes")}])


def test_unknown_model_404_fails_the_call(fake) -> None:
    child = S1.SystemOneChild("other:tag", fake.url, DIGEST, VERSION, timeout=5.0)
    with pytest.raises(urllib.error.HTTPError):
        child.decide("decide_binding", [{"ctx": ctx("a", "Will X win?", "Yes")}])


@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_a_redirect_is_an_error_and_is_not_followed(fake, code: int) -> None:
    child = make_child(fake)
    with pytest.raises(urllib.error.HTTPError) as caught:
        child.decide("decide_binding", [{"ctx": ctx("r", f"REDIRECT{code} Will X win?", "Yes")}])
    assert caught.value.code == code


def test_usage_sums_the_server_token_counts(fake) -> None:
    child = make_child(fake)
    items = [{"a": ctx("a", "Will A win?", "Yes"), "b": ctx(f"b{i}", f"Will B{i} win?", "Yes")} for i in range(5)]
    fake.requests.clear()
    _, usage = child.decide("decide_relation", items)
    questions = sum(len(r["questions"]) for r in fake.requests)
    assert usage == {"tokens_in": 5 * questions, "tokens_out": questions, "cost_usd": 0.0, "model": TAG, "kind": "local"}


def test_usage_counts_must_be_exact_floats() -> None:
    assert S1._count(5) == 5 and S1._count(2 ** 53) == 2 ** 53
    assert S1._count(2 ** 60) == 0 and S1._count(-1) == 0 and S1._count(True) == 0 and S1._count(1.5) == 0


# Determinism

def test_results_do_not_depend_on_parallelism(fake) -> None:
    items_b = [{"ctx": ctx(f"b{i}", f"Will Example {i} win?", ["Yes", "Alpha"][i % 2])} for i in range(12)]
    items_r = [{"a": ctx(f"a{i % 3}", f"Will A{i % 3} win?", "Yes"), "b": ctx(f"r{i}", f"Will R{i} win?", "No")}
               for i in range(12)]
    items_g = [{"ctx": group_of(s, 3)} for s in range(6)]
    for op, items in (("decide_binding", items_b), ("decide_relation", items_r), ("decide_group", items_g)):
        serial, _ = make_child(fake, parallel=1).decide(op, items)
        parallel, _ = make_child(fake, parallel=4).decide(op, items)
        assert parallel == serial and parallel == make_child(fake, parallel=4).decide(op, items)[0]


# Pins and the server URL

def test_from_env_refuses_an_unpinned_tag_or_a_digest_mismatch(fake, tmp_path: Path) -> None:
    empty = write_pins(tmp_path / "empty.json", {})
    with pytest.raises(RuntimeError, match="has no pin"):
        S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": fake.url, "MB_S1_PINS": str(empty)})
    wrong = write_pins(tmp_path / "wrong.json", {TAG: {"digest": "cd" * 32}})
    with pytest.raises(RuntimeError, match="the pin says"):
        S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": fake.url, "MB_S1_PINS": str(wrong)})


def test_from_env_refuses_a_tag_the_server_does_not_have(fake, tmp_path: Path) -> None:
    pins = write_pins(tmp_path / "p.json", {"absent:tag": {"digest": DIGEST}})
    with pytest.raises(RuntimeError, match="not on the Ollama server"):
        S1.SystemOneChild.from_env({"MB_S1_MODEL": "absent:tag", "MB_S1_URL": fake.url, "MB_S1_PINS": str(pins)})


def test_allow_unpinned_serves_the_live_digest(fake, tmp_path: Path) -> None:
    empty = write_pins(tmp_path / "empty.json", {})
    child = S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": fake.url, "MB_S1_PINS": str(empty),
                                        "MB_S1_ALLOW_UNPINNED": "1"})
    desc = child.describe()
    assert desc["model"] == {"id": TAG, "revision": DIGEST, "kind": "local", "ollama_version": VERSION,
                             "child_version": "7", "prompt_sha256": S1.PROMPT_SHA256}
    assert desc["name"] == "systemone-choice" and desc["version"] == "7" and desc["parallel"] == 4
    assert len(desc["prompt_sha256"]) == 64
    assert desc["relation_floor"] is None


@pytest.mark.parametrize("url", [
    "http://example.com:11434", "http://10.0.0.5:11434", "http://127.0.0.1.example.com", "ftp://127.0.0.1:11434",
    "http://[2001:db8::1]:11434", "not a url", "http://localhost.:11434", "http://127.0.0.1.:11434",
    "http://user:pw@127.0.0.1:11434", "http://user@localhost:11434", "http://@127.0.0.1:11434",
    "http://127.0.0.1\\@example.com:11434", "http://127.0.0.1:99999",
])
def test_the_server_url_must_be_loopback(url: str, tmp_path: Path) -> None:
    pins = write_pins(tmp_path / "p.json", {TAG: {"digest": DIGEST}})
    with pytest.raises(RuntimeError, match="MB_S1_URL must be"):
        S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": url, "MB_S1_PINS": str(pins)})


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://127.0.0.1:11434/", "http://[::1]:11434",
                                 "https://127.0.0.1:443", "HTTP://LOCALHOST:11434", "http://Localhost:11434"])
def test_loopback_server_urls_are_accepted(url: str) -> None:
    assert S1._loopback_url(url) == url.rstrip("/")


def test_pin_cli_writes_and_updates_the_entry(fake, tmp_path: Path) -> None:
    pins = write_pins(tmp_path / "pins.json", {"other:tag": {"digest": "ee" * 32, "license": "mit"}})
    proc = subprocess.run([sys.executable, "-m", "mentat_bench.children.systemone_child", "pin", TAG, "--license",
                           "apache-2.0"], env=child_env(fake, pins), cwd=PYTHON_DIR, capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, proc.stderr
    text = pins.read_text(encoding="utf-8")
    assert text.endswith("\n") and text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    entry = json.loads(text)
    assert entry[TAG] == {"digest": DIGEST, "license": "apache-2.0", "ollama_version": VERSION, "size": 1234}
    assert entry["other:tag"] == {"digest": "ee" * 32, "license": "mit"}


def test_a_bare_tag_resolves_to_latest_for_the_pin_and_the_server(tmp_path: Path) -> None:
    server = FakeOllama("fakebare:latest", DIGEST)
    try:
        pins = write_pins(tmp_path / "p.json", {"fakebare:latest": {"digest": DIGEST}})
        child = S1.SystemOneChild.from_env({"MB_S1_MODEL": "fakebare", "MB_S1_URL": server.url, "MB_S1_PINS": str(pins)})
        assert child.model_id == "fakebare:latest" and child.describe()["model"]["id"] == "fakebare:latest"
        result, _ = child.decide("decide_binding", [{"ctx": ctx("b", "Will X win?", "Yes")}])
        assert check_binding(result[0])["status"] in ("bound", "unresolved")
        assert {r["model"] for r in server.requests} == {"fakebare:latest"}
        bare_key = write_pins(tmp_path / "bare.json", {"fakebare": {"digest": DIGEST}})  # a pin keyed by the bare name
        with pytest.raises(RuntimeError, match="has no pin"):
            S1.SystemOneChild.from_env({"MB_S1_MODEL": "fakebare", "MB_S1_URL": server.url, "MB_S1_PINS": str(bare_key)})
    finally:
        server.close()


def test_resolve_tag_follows_ollamas_naming() -> None:
    assert S1.resolve_tag("laya") == "laya:latest"
    assert S1.resolve_tag("laya:9b") == "laya:9b"
    assert S1.resolve_tag("user/model") == "user/model:latest"


def test_pin_cli_resolves_a_bare_tag_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    server = FakeOllama("fakebare:latest", DIGEST)
    try:
        pins = write_pins(tmp_path / "pins.json", {})
        proc = subprocess.run([sys.executable, "-m", "mentat_bench.children.systemone_child", "pin", "fakebare"],
                              env=child_env(server, pins), cwd=PYTHON_DIR, capture_output=True,
                              text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        assert list(json.loads(pins.read_text(encoding="utf-8"))) == ["fakebare:latest"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["pins.json"]
    finally:
        server.close()


def test_a_failed_pin_write_keeps_the_old_pin_file(fake, tmp_path: Path, monkeypatch) -> None:
    pins = write_pins(tmp_path / "pins.json", {"other:tag": {"digest": "ee" * 32}})
    before = pins.read_bytes()

    def fail(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(S1.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        S1.pin_model(TAG, None, child_env(fake, pins))
    assert pins.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pins.json"]


def test_a_retry_after_500_waits_half_a_second(fake, monkeypatch) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(S1, "RETRY_PAUSE_S", 0.5)
    monkeypatch.setattr(S1.time, "sleep", pauses.append)
    child = make_child(fake)
    results, _ = child.decide("decide_binding", [{"ctx": ctx("f", "FLAKY500 Will X win?", "Yes")}])
    assert check_binding(results[0])["status"] in ("bound", "unresolved")
    assert pauses == [0.5] * len(fake.flaky_seen)  # one pause per retried request


def test_the_stats_parent_directory_must_exist_at_startup(fake, tmp_path: Path) -> None:
    pins = write_pins(tmp_path / "p.json", {TAG: {"digest": DIGEST}})
    with pytest.raises(RuntimeError, match="directory does not exist"):
        S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": fake.url, "MB_S1_PINS": str(pins),
                                    "MB_S1_STATS": str(tmp_path / "missing" / "child_stats.json")})


def test_a_failed_stats_write_is_reported_and_does_not_fail_the_decide(fake, tmp_path: Path, monkeypatch, capsys) -> None:
    def refuse(src: Any, dst: Any) -> None:
        raise PermissionError("sharing violation")

    child = S1.SystemOneChild(TAG, fake.url, DIGEST, VERSION, timeout=10.0, stats_path=str(tmp_path / "s.json"))
    monkeypatch.setattr(S1, "replace_with_retry", refuse)
    results, usage = child.decide("decide_binding", [{"ctx": ctx("ok", "Will ok win?", "Yes")}])
    assert check_binding(results[0])["status"] in ("bound", "unresolved") and usage["model"] == TAG
    assert "could not write MB_S1_STATS" in capsys.readouterr().err
    assert child.stats["decide_binding"]["units"] == 1
    assert not (tmp_path / "s.json").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_the_count_continues_from_an_existing_stats_file(fake, tmp_path: Path) -> None:
    stats_file = tmp_path / "child_stats.json"
    first = S1.SystemOneChild(TAG, fake.url, DIGEST, VERSION, timeout=10.0, stats_path=str(stats_file))
    first.decide("decide_binding", [{"ctx": ctx("a", "Will A win?", "Yes")}, {"ctx": ctx("b", "Will B win?", "Yes")}])
    restarted = S1.SystemOneChild(TAG, fake.url, DIGEST, VERSION, timeout=10.0, stats_path=str(stats_file))
    assert restarted.stats["decide_binding"]["units"] == 2  # a restarted child starts from the file, not from zero
    restarted.decide("decide_binding", [{"ctx": ctx("c", "Will C win?", "Yes")}])
    assert json.loads(stats_file.read_text(encoding="utf-8"))["ops"]["decide_binding"]["units"] == 3


@pytest.mark.parametrize("content", [
    "not json",
    json.dumps({"ops": {"decide_binding": {}}}),
    json.dumps({"ops": {op: {**dict.fromkeys(S1.STAT_FIELDS, 0), "units": -1} for op in S1.OPS}}),
    json.dumps({"ops": {op: {**dict.fromkeys(S1.STAT_FIELDS, 0), "units": True} for op in S1.OPS}}),
])
def test_a_malformed_stats_file_refuses_startup(fake, tmp_path: Path, content: str) -> None:
    stats_file = tmp_path / "child_stats.json"
    stats_file.write_text(content, encoding="utf-8")
    pins = write_pins(tmp_path / "p.json", {TAG: {"digest": DIGEST}})
    with pytest.raises(RuntimeError, match="malformed"):
        S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": fake.url, "MB_S1_PINS": str(pins),
                                    "MB_S1_STATS": str(stats_file)})


def test_exit_prints_the_abstention_and_refusal_counts_on_stderr(fake, tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, "-m", "mentat_bench.children.systemone_child"],
                          env=child_env(fake, pinned(tmp_path)), cwd=PYTHON_DIR, input="", capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "abstentions decide_relation=0 decide_group=0; 0 requests refused" in proc.stderr


# Conformance and the local adapter

def test_child_module_is_the_one_of_this_worktree(fake, tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, "-c", "import mentat_bench.children.systemone_child as m; print(m.__file__)"],
                          env=child_env(fake, pinned(tmp_path)), cwd=PYTHON_DIR, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert Path(proc.stdout.strip()).resolve().is_relative_to(WORKTREE.resolve())


def test_conformance_runner_passes_on_systemone_child(fake, tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, "-m", "mentat_bench.children.conformance", sys.executable, "-m",
                           "mentat_bench.children.systemone_child"], env=child_env(fake, pinned(tmp_path)),
                          cwd=PYTHON_DIR, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "conformance:" in proc.stdout and "FAIL" not in proc.stdout


def test_local_adapter_protocol_2_drives_systemone_child(fake, tmp_path: Path, monkeypatch) -> None:
    for key, value in child_env(fake, pinned(tmp_path)).items():
        if key.startswith("MB_S1_") or key == "PYTHONPATH":
            monkeypatch.setenv(key, value)
    adapter = LocalJSONLAdapter([sys.executable, "-m", "mentat_bench.children.systemone_child"], name="local",
                                timeout_s=120, protocol=2)
    try:
        assert adapter.supports_groups is True and adapter.advertised_group_max_members == 64
        decision, usage = adapter.decide_binding(ctx("a", "Will Example Coin close above 100?", "No"))
        assert check_binding(decision)["status"] in ("bound", "unresolved") and usage["cost_usd"] == 0.0
        groups, _ = adapter.decide_group(group_of(3, 3))
        for group in groups:
            check_group(group, {m["id"] for m in group_of(3, 3)["members"]})
        relation, _ = adapter.decide_relation(ctx("a", "Will A win?", "Yes"), ctx("b", "Will B win?", "Yes"))
        assert check_relation_decision(relation)[0] in S1.RELATION_CHOICES
    finally:
        adapter.close()


def test_local_adapter_passes_probs_and_equiv_through(fake, tmp_path: Path, monkeypatch) -> None:
    for key, value in child_env(fake, pinned(tmp_path)).items():
        if key.startswith("MB_S1_") or key == "PYTHONPATH":
            monkeypatch.setenv(key, value)
    adapter = LocalJSONLAdapter([sys.executable, "-m", "mentat_bench.children.systemone_child"], name="local",
                                timeout_s=120, protocol=2)
    a, b = ctx("a", "ONEQUIV Will A win?", "Yes"), ctx("b", "Will B win?", "Yes")
    group = group_of(5, 3)
    try:
        relation, _ = adapter.decide_relation(a, b)
        assert check_relation_decision(relation) == ("EQUIV", relation["conf"])
        assert relation["probs"] == {k: float(k == "EQUIV") for k in S1.RELATION_CHOICES}
        groups, _ = adapter.decide_group(group)
        state = S1._frame(S1.T["group_state"].format(listing=S1._listing(group["members"])))
        assert groups.probs == fake_probs(state, S1._choice("kind")) and isinstance(groups, list)
        binding, _ = adapter.decide_binding(ctx("c", "Will Example Coin close above 100?", "No"))
        assert check_binding(binding)["status"] in ("bound", "unresolved")
        assert binding["probs"] == fake_probs(S1._frame(S1._record(ctx("c", "Will Example Coin close above 100?", "No"))),
                                              S1._choice("status"))
        outcomes, _ = adapter.decide_batch([("decide_relation", (a, b)), ("decide_group", (group,))])
        assert outcomes[0] == relation and outcomes[1].probs == groups.probs
    finally:
        adapter.close()


def test_gateway_keeps_the_probs_of_decide_group_and_decide_batch(fake, tmp_path: Path, monkeypatch) -> None:
    for key, value in child_env(fake, pinned(tmp_path)).items():
        if key.startswith("MB_S1_") or key == "PYTHONPATH":
            monkeypatch.setenv(key, value)
    adapter = LocalJSONLAdapter([sys.executable, "-m", "mentat_bench.children.systemone_child"], name="local",
                                timeout_s=120, protocol=2)
    gateway = Gateway(adapter, spend_ceiling_usd=10.0, build_deadline_s=600.0)
    a, b = ctx("a", "ONEQUIV Will A win?", "Yes"), ctx("b", "Will B win?", "Yes")
    group = group_of(7, 3)
    state = S1._frame(S1.T["group_state"].format(listing=S1._listing(group["members"])))
    want = fake_probs(state, S1._choice("kind"))
    try:
        groups = gateway.decide_group(group)
        assert isinstance(groups, list) and groups.probs == want
        (batched,) = gateway.decide_batch("decide_group", [(group,)])
        assert batched.probs == want and list(batched) == list(groups)
        (relation,) = gateway.decide_batch("decide_relation", [(a, b)])
        assert relation["probs"] == {k: float(k == "EQUIV") for k in S1.RELATION_CHOICES}
    finally:
        adapter.close()


def test_conformance_checks_the_probs_of_each_answer() -> None:
    bad = {"EQUIV": 0.9, "IMPLIES": 0.9, "IMPLIED_BY": 0.0, "EXCLUDES": 0.0, "NONE": 0.0}
    with pytest.raises(DecisionError):
        CONF._relation_ok({"relation": "EQUIV", "probs": bad})
    with pytest.raises(DecisionError):
        CONF._group_ok({"groups": [], "probs": {"none": 1.0}})
    with pytest.raises(DecisionError):
        CONF._binding_ok({"status": "bound", "class": "C", "negated": False, "missing": [], "probs": {"bound": 0.5}})
    CONF._relation_ok({"relation": "EQUIV", "probs": dict.fromkeys(S1.RELATION_CHOICES, 0.2)})


def test_handle_answers_a_batch_with_isolated_item_errors(fake) -> None:
    child = make_child(fake)
    line = json.dumps({"op": "batch", "id": 4, "calls": [
        {"op": "decide_binding", "ctx": ctx("ok", "Will Example win?", "Yes")},
        {"op": "decide_binding", "ctx": ctx("bad", "CTX400 Will Example win?", "Yes")},
    ]})
    reply = handle(child, line)
    assert reply["id"] == 4 and len(reply["results"]) == 2
    assert check_binding(reply["results"][0])["status"] in ("bound", "unresolved")
    assert "error" in reply["results"][1] and "HTTP 400" in reply["results"][1]["error"]


# Discovery without /api/tags, the digest at exit, and conform

def server_pins(tmp_path: Path, server: str | None, digest: str = DIGEST, name: str = "pins.json") -> Path:
    """A pin of a server kind; a kind other than ollama carries the passing conform record that pin writes."""
    entry: dict[str, Any] = {"digest": digest, "license": None, "ollama_version": None, "size": None}
    if server is not None:
        entry["server"] = server
        if server != "ollama":
            entry["conform"] = {"ok": True, "date": "2026-10-29T00:00:00Z", "checks": []}
    return write_pins(tmp_path / name, {TAG: entry})


def test_a_server_without_api_tags_is_pinned_by_its_v1_models_digest(tmp_path: Path) -> None:
    server = FakeOllama(kind="openai")
    try:
        child = S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url,
                                            "MB_S1_PINS": str(server_pins(tmp_path, "openai"))})
        assert child.digest == DIGEST and child.ollama_version is None
        result, _ = child.decide("decide_binding", [{"ctx": ctx("b", "Will X win?", "Yes")}])
        assert check_binding(result[0])["status"] in ("bound", "unresolved")
        with pytest.raises(RuntimeError, match="is served by openai, the pin says ollama"):
            S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url,
                                        "MB_S1_PINS": str(server_pins(tmp_path, None, name="o.json"))})
    finally:
        server.close()


@pytest.mark.parametrize("kind", ["openai", "reply"])
def test_pin_records_the_server_kind_of_a_server_that_is_not_ollama(kind: str, tmp_path: Path) -> None:
    server = FakeOllama(kind=kind)
    try:
        pins = write_pins(tmp_path / "pins.json", {"other:tag": {"digest": "ee" * 32}})
        entry = S1.pin_model(TAG, None, child_env(server, pins))
        conform_record = entry.pop("conform")  # the pin ran conform first, and keeps its passing record
        assert conform_record["ok"] is True and conform_record["date"].endswith("Z")
        assert [c["check"] for c in conform_record["checks"]] == ["probabilities", "determinism", "over_context", "usage",
                                                                   "digest_at_exit"]
        assert entry == {"digest": DIGEST, "license": None, "ollama_version": None, "size": None, "server": kind}
        saved = json.loads(pins.read_text(encoding="utf-8"))
        assert saved["other:tag"] == {"digest": "ee" * 32} and saved[TAG]["conform"] == conform_record
        assert S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url, "MB_S1_PINS": str(pins)}).digest == DIGEST
    finally:
        server.close()


def test_a_digest_in_the_reply_is_pinned_and_checked_like_the_others(tmp_path: Path) -> None:
    server = FakeOllama(kind="reply")
    try:
        env = {"MB_S1_MODEL": TAG, "MB_S1_URL": server.url}
        child = S1.SystemOneChild.from_env({**env, "MB_S1_PINS": str(server_pins(tmp_path, "reply"))})
        assert child.digest == DIGEST and child.ollama_version is None
        with pytest.raises(RuntimeError, match="the pin says"):
            S1.SystemOneChild.from_env({**env, "MB_S1_PINS": str(server_pins(tmp_path, "reply", "cd" * 32, "w.json"))})
        with pytest.raises(RuntimeError, match="is served by reply, the pin says ollama"):
            S1.SystemOneChild.from_env({**env, "MB_S1_PINS": str(server_pins(tmp_path, None, name="o.json"))})
    finally:
        server.close()


@pytest.mark.parametrize("call", ["from_env", "pin"])
def test_a_server_that_reports_no_digest_anywhere_is_refused(call: str, tmp_path: Path) -> None:
    server = FakeOllama(kind="bare")
    try:
        pins = write_pins(tmp_path / "pins.json", {})
        env = {**child_env(server, pins), "MB_S1_ALLOW_UNPINNED": "1"}  # not even an unpinned run takes a missing digest
        with pytest.raises(RuntimeError, match="gives no digest"):
            if call == "pin":
                S1.pin_model(TAG, None, env)
            else:
                S1.SystemOneChild.from_env(env)
        assert pins.read_text(encoding="utf-8") == "{}"
    finally:
        server.close()


def test_a_digest_that_changes_during_the_run_is_refused_at_exit(fake, tmp_path: Path) -> None:
    child = make_child(fake)
    child.verify_digest()  # unchanged: passes
    fake.digest = "cd" * 32
    with pytest.raises(RuntimeError, match="at exit"):
        child.verify_digest()


def test_main_refuses_the_run_after_its_output_when_the_digest_changed(fake, tmp_path: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-m", "mentat_bench.children.systemone_child"],
                            env=child_env(fake, pinned(tmp_path)), cwd=PYTHON_DIR, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        proc.stdin.write(json.dumps({"op": "decide_binding", "id": 1, "ctx": ctx("a", "Will X win?", "Yes")}) + "\n")
        proc.stdin.flush()
        reply = json.loads(proc.stdout.readline())
        assert reply["id"] == 1 and "error" not in reply
        fake.digest = "cd" * 32
        proc.stdin.close()
        rest = proc.stdout.read()
        err = proc.stderr.read()
        code = proc.wait(timeout=120)
    finally:
        proc.kill()
    assert code != 0 and rest == "" and f"digest of {TAG} is {'cd' * 32} at exit" in err


def conform_check(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in report["checks"] if c["check"] == name)


def test_conform_takes_the_size_refusal_of_an_oversized_state_as_over_context(fake, tmp_path: Path) -> None:
    fake.context_text = CONTEXT_SIZE_400  # the Ollama 0.40.11 wording of the over-long state's refusal
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert conform_check(report, "over_context")["ok"] is True, report
    assert report["ok"] is True


def test_conform_probes_the_reading_instructions_with_a_rule_card(fake, tmp_path: Path) -> None:
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is True
    probes = {name: q for r in fake.requests for name, q in r["questions"].items() if name in ("base_rules", "alt_rules")}
    assert {name: q["instructions"] for name, q in probes.items()} == {"base_rules": BASE_RULES, "alt_rules": ALT_RULES}
    assert all("League rules:\nRules of the Example League" in r["state"] for r in fake.requests
               if set(r["questions"]) & {"base_rules", "alt_rules"})


def test_conform_passes_on_a_conforming_server(fake, tmp_path: Path) -> None:
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is True and report["digest"] == DIGEST and report["server"] == "ollama"
    assert [c["check"] for c in report["checks"]] == ["pin", "probabilities", "determinism", "over_context", "usage",
                                                       "digest_at_exit"]
    assert all(c["ok"] for c in report["checks"])
    assert all(len(r["questions"]) == 1 for r in fake.requests)  # one question per request, as the child sends


def test_conform_cli_prints_the_report_and_exits_nonzero_on_failure(fake, tmp_path: Path) -> None:
    args = [sys.executable, "-m", "mentat_bench.children.systemone_child", "conform", TAG, "--url", fake.url]
    passed = subprocess.run(args, env=child_env(fake, pinned(tmp_path)), cwd=PYTHON_DIR, capture_output=True, text=True,
                            timeout=300)
    assert passed.returncode == 0, passed.stderr
    assert json.loads(passed.stdout)["ok"] is True
    unpinned = subprocess.run(args, env=child_env(fake, write_pins(tmp_path / "none.json", {})), cwd=PYTHON_DIR,
                              capture_output=True, text=True, timeout=300)
    assert unpinned.returncode == 1
    assert conform_check(json.loads(unpinned.stdout), "pin")["ok"] is False


def test_conform_refuses_a_digest_that_is_not_the_pin(fake, tmp_path: Path) -> None:
    pins = write_pins(tmp_path / "p.json", {TAG: {"digest": "cd" * 32}})
    report = S1.conform(TAG, fake.url, child_env(fake, pins))
    assert report["ok"] is False and conform_check(report, "pin")["ok"] is False
    assert "the pin says" in conform_check(report, "pin")["detail"]


def test_conform_refuses_bad_probabilities(fake, tmp_path: Path) -> None:
    def doubled(answers: dict[str, Any]) -> dict[str, Any]:
        return {name: {**a, "probabilities": {k: 2 * p for k, p in a["probabilities"].items()}} for name, a in answers.items()}

    fake.transform = doubled
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is False
    assert conform_check(report, "probabilities")["ok"] is False and conform_check(report, "determinism")["ok"] is True


def test_conform_refuses_nondeterministic_answers(fake, tmp_path: Path) -> None:
    calls = itertools.count()

    def jitter(answers: dict[str, Any]) -> dict[str, Any]:
        shift = 0.001 if next(calls) % 2 else -0.001  # the second request of a question gets the other shift
        out = {}
        for name, a in answers.items():
            probs = dict(a["probabilities"])
            keys = list(probs)
            probs[keys[0]] += shift
            probs[keys[1]] -= shift
            out[name] = {**a, "probabilities": probs}
        return out

    fake.transform = jitter
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is False
    assert conform_check(report, "determinism")["ok"] is False and conform_check(report, "probabilities")["ok"] is True


def test_conform_refuses_a_server_that_answers_an_oversized_state(fake, tmp_path: Path) -> None:
    fake.context_chars = None  # no context limit: the oversized state is answered
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is False and conform_check(report, "over_context")["ok"] is False


def test_conform_refuses_usage_that_is_not_counts(fake, tmp_path: Path) -> None:
    fake.usage_override = {"input_tokens": "5", "output_tokens": 1}
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is False and conform_check(report, "usage")["ok"] is False


def test_conform_refuses_a_digest_that_changes_during_the_check(fake, tmp_path: Path) -> None:
    def flip(answers: dict[str, Any]) -> dict[str, Any]:
        fake.digest = "cd" * 32
        return answers

    fake.transform = flip
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is False
    assert conform_check(report, "pin")["ok"] is True and conform_check(report, "digest_at_exit")["ok"] is False


def test_conform_reports_a_server_that_gives_no_digest(tmp_path: Path) -> None:
    server = FakeOllama(kind="bare")
    try:
        report = S1.conform(TAG, server.url, child_env(server, pinned(tmp_path)))
    finally:
        server.close()
    assert report["ok"] is False and [c["check"] for c in report["checks"]] == ["server"]
    assert "gives no digest" in report["checks"][0]["detail"]


# Review fixes: pin gating, discovery statuses, probe checks, exit line, adapter refusal, bad URL

def test_pin_refuses_a_non_ollama_server_that_does_not_conform(tmp_path: Path) -> None:
    server = FakeOllama(kind="openai")
    server.context_chars = None  # answers the oversized state: fails over_context
    try:
        pins = write_pins(tmp_path / "pins.json", {})
        with pytest.raises(RuntimeError, match="does not conform .*over_context"):
            S1.pin_model(TAG, None, child_env(server, pins))
        assert pins.read_text(encoding="utf-8") == "{}"
    finally:
        server.close()


@pytest.mark.parametrize("record", [None, {"ok": False, "date": "x", "checks": []}, {"ok": "yes"}])
def test_from_env_refuses_a_non_ollama_pin_without_a_passing_conform_record(record: Any, tmp_path: Path) -> None:
    server = FakeOllama(kind="openai")
    try:
        entry: dict[str, Any] = {"digest": DIGEST, "server": "openai"}
        if record is not None:
            entry["conform"] = record
        pins = write_pins(tmp_path / "pins.json", {TAG: entry})
        with pytest.raises(RuntimeError, match="no passing conform record"):
            S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url, "MB_S1_PINS": str(pins)})
    finally:
        server.close()


@pytest.mark.parametrize("kind", ["openai", "reply"])
def test_conform_passes_on_the_openai_and_reply_kinds(kind: str, tmp_path: Path) -> None:
    server = FakeOllama(kind=kind)
    try:
        report = S1.conform(TAG, server.url, child_env(server, server_pins(tmp_path, kind)))
    finally:
        server.close()
    assert report["ok"] is True and report["server"] == kind and report["digest"] == DIGEST
    assert conform_check(report, "pin")["ok"] is True and conform_check(report, "digest_at_exit")["ok"] is True


def test_a_digest_with_the_sha256_prefix_is_the_bare_digest(tmp_path: Path) -> None:
    server = FakeOllama(digest=f"sha256:{DIGEST}")
    try:
        child = S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url, "MB_S1_PINS": str(pinned(tmp_path))})
        assert child.digest == DIGEST
        prefixed = write_pins(tmp_path / "prefixed.json", {TAG: {"digest": f"sha256:{DIGEST}"}})
        assert S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url, "MB_S1_PINS": str(prefixed)}).digest == DIGEST
    finally:
        server.close()


@pytest.mark.parametrize("kind", ["reply405", "reply_html"])
def test_a_model_list_that_is_405_or_html_is_absent_and_the_reply_gives_the_digest(kind: str, tmp_path: Path, capsys) -> None:
    server = FakeOllama(kind=kind)
    try:
        child = S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url,
                                            "MB_S1_PINS": str(server_pins(tmp_path, "reply"))})
    finally:
        server.close()
    assert child.digest == DIGEST and child.ollama_version is None  # the version is optional: a 405 or HTML is null
    if kind == "reply_html":
        assert "did not return a JSON object; treated as absent" in capsys.readouterr().err


def test_a_model_list_that_fails_with_500_is_fatal(tmp_path: Path) -> None:
    server = FakeOllama(kind="tags500")
    try:
        with pytest.raises(urllib.error.HTTPError, match="500"):
            S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url, "MB_S1_PINS": str(pinned(tmp_path))})
    finally:
        server.close()


def test_a_probe_answered_for_another_model_is_refused(tmp_path: Path) -> None:
    server = FakeOllama(kind="reply")
    server.reply_model = "other:tag"
    try:
        with pytest.raises(RuntimeError, match="answered for model 'other:tag'"):
            S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url,
                                        "MB_S1_PINS": str(server_pins(tmp_path, "reply"))})
    finally:
        server.close()


def test_a_probe_refused_with_an_error_is_a_clear_runtime_error(tmp_path: Path) -> None:
    server = FakeOllama(kind="reply")
    server.context_chars = 10  # the probe's own state is over this: refused with 400
    try:
        with pytest.raises(RuntimeError, match="the probe of fake-s1:1b was refused: HTTP 400"):
            S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": server.url,
                                        "MB_S1_PINS": str(server_pins(tmp_path, "reply"))})
    finally:
        server.close()


def test_the_exit_check_names_a_server_that_cannot_be_reached(tmp_path: Path) -> None:
    server = FakeOllama()
    child = make_child(server)
    server.close()
    with pytest.raises(RuntimeError, match="digest_at_exit not verified"):
        child.verify_digest()


def test_main_prints_one_line_and_exits_nonzero_when_the_exit_check_cannot_reach_the_server(tmp_path: Path) -> None:
    server = FakeOllama()
    proc = subprocess.Popen([sys.executable, "-m", "mentat_bench.children.systemone_child"],
                            env=child_env(server, pinned(tmp_path)), cwd=PYTHON_DIR, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        proc.stdin.write(json.dumps({"op": "decide_binding", "id": 1, "ctx": ctx("a", "Will X win?", "Yes")}) + "\n")
        proc.stdin.flush()
        assert json.loads(proc.stdout.readline())["id"] == 1
        server.close()  # the server is gone before the child ends
        proc.stdin.close()
        err = proc.stderr.read()
        code = proc.wait(timeout=120)
    finally:
        proc.kill()
    lines = [line for line in err.splitlines() if "digest_at_exit" in line]
    assert code == 1 and len(lines) == 1 and lines[0].startswith("systemone_child: digest_at_exit not verified: ")
    assert "Traceback" not in err


def test_the_adapter_fails_the_build_when_the_digest_changes_during_the_run(fake, tmp_path: Path, monkeypatch) -> None:
    for key, value in child_env(fake, pinned(tmp_path)).items():
        if key.startswith("MB_S1_") or key == "PYTHONPATH":
            monkeypatch.setenv(key, value)
    adapter = LocalJSONLAdapter([sys.executable, "-m", "mentat_bench.children.systemone_child"], name="local",
                                timeout_s=120, protocol=2)
    adapter.decide_binding(ctx("a", "Will X win?", "Yes"))
    fake.digest = "cd" * 32  # the model changed after its first answer
    adapter.close()
    assert adapter.exit_error == "exited with status 1 after its input closed"


def test_the_adapter_judges_a_clean_end_as_clean(fake, tmp_path: Path, monkeypatch) -> None:
    for key, value in child_env(fake, pinned(tmp_path)).items():
        if key.startswith("MB_S1_") or key == "PYTHONPATH":
            monkeypatch.setenv(key, value)
    adapter = LocalJSONLAdapter([sys.executable, "-m", "mentat_bench.children.systemone_child"], name="local",
                                timeout_s=120, protocol=2)
    adapter.decide_binding(ctx("a", "Will X win?", "Yes"))
    adapter.close()
    assert adapter.exit_error is None


def test_conform_accepts_a_difference_within_the_tolerance(fake, tmp_path: Path) -> None:
    calls = itertools.count()

    def tiny(answers: dict[str, Any]) -> dict[str, Any]:
        shift = 1e-7 if next(calls) % 2 else -1e-7  # far below the 1e-6 tolerance, and the same argmax
        out = {}
        for name, a in answers.items():
            probs = dict(a["probabilities"])
            keys = list(probs)
            probs[keys[0]] += shift
            probs[keys[1]] -= shift
            out[name] = {**a, "probabilities": probs}
        return out

    fake.transform = tiny
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert conform_check(report, "determinism")["ok"] is True


def test_conform_probes_the_largest_menus_of_the_decision_code(fake, tmp_path: Path) -> None:
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is True
    sizes = {}
    for r in fake.requests:
        for name, q in r["questions"].items():
            sizes.setdefault(name, set()).add(len(q["criteria"]))
    assert sizes["order"] == {26} and sizes["lower"] == {9} and sizes["k"] == {8} and sizes["upper"] == {8}
    assert sizes["base"] == {5} and sizes["alt"] == {5}  # four readings and none


def test_conform_with_a_bad_url_is_a_report_not_a_traceback(tmp_path: Path) -> None:
    report = S1.conform(TAG, "http://example.com:11434", {"MB_S1_PINS": str(pinned(tmp_path))})
    assert report["ok"] is False and report["server"] is None and report["digest"] is None
    assert [c["check"] for c in report["checks"]] == ["server"] and "MB_S1_URL must be" in report["checks"][0]["detail"]


def test_conform_cli_with_a_bad_url_prints_the_report_and_exits_one(tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, "-m", "mentat_bench.children.systemone_child", "conform", TAG, "--url",
                           "http://example.com:11434"], env={**os.environ, "MB_S1_PINS": str(pinned(tmp_path))},
                          cwd=PYTHON_DIR, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    assert json.loads(proc.stdout)["ok"] is False


def test_conform_reports_a_refused_probe_as_a_server_failure(tmp_path: Path) -> None:
    server = FakeOllama(kind="reply")
    server.context_chars = 10  # the discovery probe is refused: conform reports the server, not a traceback
    try:
        report = S1.conform(TAG, server.url, child_env(server, server_pins(tmp_path, "reply")))
    finally:
        server.close()
    assert report["ok"] is False and [c["check"] for c in report["checks"]] == ["server"]
    assert "was refused: HTTP 400" in report["checks"][0]["detail"]


# Review round 2: discovery falls through, malformed replies, menu abstentions, one-line refusals

class MalformedStatusServer:
    """Every connection gets a reply whose status line is not HTTP: http.client raises BadStatusLine, which is no HTTPError."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.1)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                continue
            conn.settimeout(5)
            with conn:
                try:
                    conn.recv(65536)
                    conn.sendall(b"NOT AN HTTP STATUS LINE\r\n\r\n")
                except OSError:
                    pass

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.sock.close()


@pytest.mark.parametrize("kind, server", [("tags_unlisted", "openai"), ("tags_nodigest", "reply")])
def test_discovery_falls_through_when_tags_lists_no_digest(kind: str, server: str, tmp_path: Path) -> None:
    """/api/tags that does not list the model, or lists it without a digest, goes on to /v1/models, then to the reply digest."""
    fake_server = FakeOllama(kind=kind)
    try:
        child = S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": fake_server.url,
                                            "MB_S1_PINS": str(server_pins(tmp_path, server))})
    finally:
        fake_server.close()
    assert child.digest == DIGEST


def test_discovery_refuses_only_when_no_source_gives_a_digest(tmp_path: Path) -> None:
    fake_server = FakeOllama(kind="tags_bare")  # listed without a digest, and neither /v1/models nor the reply gives one
    try:
        with pytest.raises(RuntimeError, match="gives no digest for fake-s1:1b"):
            S1.SystemOneChild.from_env({"MB_S1_MODEL": TAG, "MB_S1_URL": fake_server.url, "MB_S1_ALLOW_UNPINNED": "1",
                                        "MB_S1_PINS": str(write_pins(tmp_path / "pins.json", {}))})
    finally:
        fake_server.close()


def test_version_is_null_for_a_malformed_status_line() -> None:
    server = MalformedStatusServer()
    try:
        assert S1._version(server.url, 5.0) is None
    finally:
        server.close()


def test_conform_reports_a_malformed_status_line_as_a_server_failure(tmp_path: Path) -> None:
    server = MalformedStatusServer()
    try:
        report = S1.conform(TAG, server.url, {"MB_S1_PINS": str(pinned(tmp_path))})
    finally:
        server.close()
    assert report["ok"] is False and [c["check"] for c in report["checks"]] == ["server"]
    assert report["checks"][0]["detail"]  # a failed check, not a traceback


def test_conform_records_a_menu_the_child_abstains_on_and_passes_the_other_checks(fake, tmp_path: Path) -> None:
    fake.context_chars = 1000  # the 26-letter order menu's state is over this, so the child abstains on it; the rest fit
    report = S1.conform(TAG, fake.url, child_env(fake, pinned(tmp_path)))
    assert report["ok"] is True
    assert conform_check(report, "probabilities")["ok"] is True
    assert "abstains (over context): order" in conform_check(report, "probabilities")["detail"]
    assert conform_check(report, "determinism")["ok"] is True
    # the 13 questions less the abstained order menu (the two card-reading probes are compared too)
    assert conform_check(report, "determinism")["detail"].startswith("12 questions compared")
    assert conform_check(report, "over_context")["ok"] is True  # the separate check is unchanged


def test_a_refused_pin_prints_one_line_and_no_traceback(fake, tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, "-m", "mentat_bench.children.systemone_child", "pin", "absent:tag"],
                          env=child_env(fake, pinned(tmp_path)), cwd=PYTHON_DIR, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1 and proc.stderr.startswith("systemone_child: absent:tag is not")


def test_a_refused_startup_prints_one_line_and_no_traceback(fake, tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, "-m", "mentat_bench.children.systemone_child"],
                          env=child_env(fake, write_pins(tmp_path / "none.json", {})), cwd=PYTHON_DIR, input="",
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    assert len(proc.stderr.strip().splitlines()) == 1 and "has no pin" in proc.stderr

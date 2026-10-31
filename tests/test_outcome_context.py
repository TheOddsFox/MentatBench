"""The optional corpus field `context` (a league rule card): a row's context reaches the child on the binding, relation and
group requests that name its outcome, and a row without one (absent or null) sends the same request bytes as before. Also
the resume rules for cards, the 2,000-character cap, and a golden of a build without cards (taken from before the field)."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest

from mentat_bench import cli, ir
from mentat_bench.adapters.local_jev import LocalJSONLAdapter
from mentat_bench.adapters.mock import EmptyAdapter
from mentat_bench.children.protocol import ChildError, outcome
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

CARD = "Rule card: a coin closes above a threshold when its June 1 noon ET price is above it."

# Logs every request line it reads (the bytes the adapter wrote) and answers each op with a valid decision.
LOGGER = r'''
import json, sys
log = open(sys.argv[1], "a", encoding="utf-8")
for line in sys.stdin:
    log.write(line)
    log.flush()
    req = json.loads(line)
    op = req["op"]
    if op == "hello":
        out = {"protocol": 2, "name": "logger", "version": "0",
               "ops": {"decide_binding": {}, "decide_relation": {}, "decide_group": {"max_members": 64}}}
    elif op == "decide_binding":
        out = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
    elif op == "decide_relation":
        out = {"relation": "NONE"}
    else:
        ids = [m["id"] for m in req["ctx"]["members"]]
        out = {"groups": [{"kind": "at_most_one", "members": ids[:2]}]}
    out["id"] = req["id"]
    print(json.dumps(out), flush=True)
'''


def _rows(extra, n=3):
    """n outcome records of one event (a binding, relations and a group each); `extra` is added to every row."""
    return [{"id": f"v:m{i}:1", "question": f"Will X{i} win the cup?", "label": "Yes", "event_title": "E", **extra}
            for i in range(1, n + 1)]


def _corpus(rows):
    """The corpus.jsonl rows of build records (text instead of question, as a corpus file has them)."""
    return [{**{k: v for k, v in row.items() if k != "question"}, "text": row["question"]} for row in rows]


def _script(tmp_path):
    script = tmp_path / "logger.py"
    script.write_text(LOGGER, encoding="utf-8")
    return script


def _build(tmp_path, name, rows):
    """A protocol 2 local build over the rows through the CLI; returns the request lines the child read, as bytes."""
    corpus = tmp_path / f"{name}.jsonl"
    ir.write_jsonl(corpus, _corpus(rows))
    log = tmp_path / f"{name}.log"
    argv = ["build", "--adapter", "local", "--local-protocol", "2", "--corpus", str(corpus), "--out", str(tmp_path / name),
            "--local-arg", sys.executable, "--local-arg", str(_script(tmp_path)), "--local-arg", str(log)]
    assert cli.main(argv) == 0
    return log.read_bytes()


def _requests(raw: bytes) -> list[dict]:
    return [json.loads(line) for line in raw.decode("utf-8").splitlines()]


def _lines(path: Path) -> list[str]:
    """The request lines a logger child read, without the hello of each adapter start and without the sequence number
    "id" (a resumed run numbers its requests from 1 again), in canonical form."""
    requests = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return [json.dumps({k: v for k, v in req.items() if k != "id"}, sort_keys=True)
            for req in requests if req["op"] != "hello"]


class _Killable(LocalJSONLAdapter):
    """Raises KeyboardInterrupt on the relation call after `kill_after` of them, before anything is sent: a killed build."""

    def __init__(self, *args, kill_after=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.kill_after, self.relation_calls = kill_after, 0

    def decide_relation(self, *args, **kwargs):
        self.relation_calls += 1
        if self.kill_after is not None and self.relation_calls > self.kill_after:
            raise KeyboardInterrupt
        return super().decide_relation(*args, **kwargs)


def _adapter(tmp_path, log, kill_after=None):
    return _Killable([sys.executable, str(_script(tmp_path)), str(log)], name="local", timeout_s=20, protocol=2,
                     kill_after=kill_after)


def _killed_build(tmp_path, rows, out, log):
    """A build killed in its relation phase after two relation calls; its work dir (with a checkpoint) is kept."""
    with _adapter(tmp_path, log, kill_after=2) as adapter:
        with pytest.raises(KeyboardInterrupt):
            fixed.build(rows, adapter, out, dict(DEFAULT_PROFILE), checkpoint_every=1)
    return out.with_name(out.name + ".build")


def _resume(tmp_path, rows, out, log):
    with _adapter(tmp_path, log) as adapter:
        return fixed.build(rows, adapter, out, dict(DEFAULT_PROFILE), checkpoint_every=1, resume=True)


def test_context_reaches_the_binding_relation_and_group_requests(tmp_path):
    requests = _requests(_build(tmp_path, "card", _rows({"context": CARD})))
    assert {"decide_binding", "decide_relation", "decide_group"} <= {req["op"] for req in requests}
    for req in requests:
        if req["op"] == "decide_binding":
            contexts = [req["ctx"]]
        elif req["op"] == "decide_relation":
            contexts = [req["a"], req["b"]]
        elif req["op"] == "decide_group":
            contexts = req["ctx"]["members"]
        else:
            continue
        assert contexts and all(ctx["context"] == CARD for ctx in contexts), req["op"]


def test_rows_without_a_context_send_the_same_bytes_as_null(tmp_path):
    absent = _build(tmp_path, "absent", _rows({}))
    null = _build(tmp_path, "null", _rows({"context": None}))
    assert absent == null
    assert b'"context"' not in absent
    # the binding of the first outcome, as the request was written before the field existed (Windows text stdin: CRLF)
    golden = b'{"ctx":{"event_title":"E","id":"v:m1:1","label":"Yes","question":"Will X1 win the cup?"},"id":2,"op":"decide_binding"}'
    assert golden in absent.replace(b"\r\n", b"\n").split(b"\n")


def test_the_corpus_loader_leaves_context_out_of_a_record_without_one(tmp_path):
    def records(rows):
        path = tmp_path / "corpus.jsonl"
        ir.write_jsonl(path, _corpus(rows))
        return list(cli._corpus_records(argparse.Namespace(corpus=str(path), data_dir=None, limit_markets=None)))

    absent, null = records(_rows({})), records(_rows({"context": None}))
    assert absent == null and all("context" not in rec for rec in absent)
    assert records(_rows({"context": CARD}))[0]["context"] == CARD


def test_a_build_refuses_a_context_that_is_not_text(tmp_path):
    rows = [{"id": "v:m1:1", "question": "Will X1 win?", "label": "Yes", "event_title": "E", "context": 5}]
    with pytest.raises(ValueError, match="context"):
        fixed.build(rows, EmptyAdapter(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert not (tmp_path / "sub").exists()


@pytest.mark.parametrize("size, accepted", [(2000, True), (2001, False)])
def test_a_card_is_capped_at_2000_characters_and_counted_in_the_policy(tmp_path, size, accepted):
    rows = [{"id": "v:m1:1", "question": "Will X1 win?", "label": "Yes", "event_title": "E", "context": "c" * size}]
    if not accepted:
        with pytest.raises(ValueError, match="outcome v:m1:1 has a context of 2001 characters, above the 2000 allowed"):
            fixed.build(rows, EmptyAdapter(), tmp_path / "sub", dict(DEFAULT_PROFILE))
        assert not (tmp_path / "sub").exists()
        return
    fixed.build(rows, EmptyAdapter(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))["context_rows"] == 1


def test_a_build_without_cards_has_no_context_rows_key_in_its_policy(tmp_path):
    fixed.build(_rows({})[:1], EmptyAdapter(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert "context_rows" not in json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))


def test_the_child_side_outcome_keeps_a_context_only_when_it_is_sent():
    base = {"id": "v:m1:1", "question": "Will X1 win?", "label": "Yes", "event_title": "E"}
    assert outcome({**base, "context": CARD}) == {**base, "context": CARD}
    assert outcome(base) == base
    with pytest.raises(ChildError, match="ctx.context"):
        outcome({**base, "context": ["not", "text"]})


def test_a_resumed_build_with_cards_sends_the_requests_of_an_uninterrupted_build(tmp_path):
    rows = _rows({"context": CARD}, n=6)
    full_log = tmp_path / "full.log"
    with _adapter(tmp_path, full_log) as adapter:
        fixed.build(rows, adapter, tmp_path / "full", dict(DEFAULT_PROFILE), checkpoint_every=1)
    out = tmp_path / "killed"
    killed_log, resumed_log = tmp_path / "killed.log", tmp_path / "resumed.log"
    work = _killed_build(tmp_path, rows, out, killed_log)
    checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["relations_committed"] == 2 and checkpoint["phase"] == "relations"
    summary = _resume(tmp_path, rows, out, resumed_log)
    assert summary["resumed"] is True
    full, killed, resumed = _lines(full_log), _lines(killed_log), _lines(resumed_log)
    assert killed and resumed and len(killed) < len(full)
    assert full[:len(killed)] == killed  # the killed run asked the same requests, in the same order, up to the kill
    assert full[-len(resumed):] == resumed  # the resumed run asks the rest, byte for byte
    assert all(CARD in line for line in full), "every request of the build names its card"


def test_adding_a_card_to_the_corpus_refuses_the_resume(tmp_path):
    rows = _rows({}, n=6)
    out = tmp_path / "killed"
    _killed_build(tmp_path, rows, out, tmp_path / "killed.log")
    carded = [dict(rows[0], context=CARD)] + rows[1:]
    with _adapter(tmp_path, tmp_path / "resumed.log") as adapter:
        with pytest.raises(ValueError, match="cannot resume: the corpus differs"):
            fixed.build(carded, adapter, out, dict(DEFAULT_PROFILE), checkpoint_every=1, resume=True)
    assert not out.exists()


def test_a_checkpoint_without_a_corpus_record_refuses_cards_and_resumes_without(tmp_path):
    rows = _rows({}, n=6)
    out = tmp_path / "killed"
    work = _killed_build(tmp_path, rows, out, tmp_path / "killed.log")
    checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    del checkpoint["policy"]["corpus"]  # a checkpoint from before the corpus digest was recorded
    (work / "checkpoint.json").write_text(json.dumps(checkpoint, sort_keys=True), encoding="utf-8")
    carded = [dict(rows[0], context=CARD)] + rows[1:]
    with _adapter(tmp_path, tmp_path / "carded.log") as adapter:
        with pytest.raises(ValueError, match="predates the corpus record"):
            fixed.build(carded, adapter, out, dict(DEFAULT_PROFILE), checkpoint_every=1, resume=True)
    assert _resume(tmp_path, rows, out, tmp_path / "resumed.log")["resumed"] is True


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# the checkpoint fields that differ between two runs of the same build (wall-clock, memory, and the byte count of the usage
# log, whose entries carry seconds), left out of its digest
VOLATILE = ("wall_seconds", "peak_rss_mb", "phase_seconds", "phase_call_seconds", "group_t0",
            "usage_bytes")


def _golden(tmp_path: Path) -> dict:
    """Digests of a build without cards: the corpus digest the checkpoint records, the work dir's records, the checkpoint
    (without VOLATILE) and the build policy of an uninterrupted run of the same build."""
    rows = _rows({}, n=6)
    work = _killed_build(tmp_path, rows, tmp_path / "killed", tmp_path / "killed.log")
    checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    full = tmp_path / "full"
    with _adapter(tmp_path, tmp_path / "full.log") as adapter:
        fixed.build(rows, adapter, full, dict(DEFAULT_PROFILE), checkpoint_every=1)
    stable = {k: v for k, v in checkpoint.items() if k not in VOLATILE}
    return {
        "corpus": checkpoint["policy"]["corpus"],
        "records_sha256": _sha((work / "records.jsonl").read_bytes()),
        "checkpoint_sha256": _sha(json.dumps(stable, sort_keys=True).encode("utf-8")),
        "build_policy_sha256": _sha((full / "build_policy.json").read_bytes()),
    }


# taken from a git archive of 5e65729 (the commit before the field existed), by _golden run from that tree
GOLDEN_BEFORE_CONTEXT = {
    "corpus": "294bebb4ef15642f38291c5beb7d515683a7c1facc5fa85b29c55990502cd363",
    "records_sha256": "294bebb4ef15642f38291c5beb7d515683a7c1facc5fa85b29c55990502cd363",
    "checkpoint_sha256": "88c965929c62d0234c665b612d9e3742bd87f19c5a26a81fc1ee132f059bedcd",
    "build_policy_sha256": "24d3873a80ae35a8589e8fb721ebee375ad60691ce54dce24f51478dfcbe5f36",
}


def test_a_build_without_cards_matches_the_golden_taken_before_the_field(tmp_path):
    assert _golden(tmp_path) == GOLDEN_BEFORE_CONTEXT

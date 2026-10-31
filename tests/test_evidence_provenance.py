"""Evidence and provenance of submissions (wave 3, verifier round): the optional binding `evidence` and the
constraint `provenance` that a gold-backed adapter supplies, and the byte baseline that proves non-oracle submissions
are unchanged.

Model-built submissions cannot cite gold's event-id or axiom REFs (no public vocabulary); only the gold-backed
adapters (oracle, gold_view) supply them.
"""

import hashlib
import tempfile
from pathlib import Path

import pytest

from mentat_bench import ir
from mentat_bench.adapters.base import DecisionError, check_binding, usage
from mentat_bench.adapters.mock import EmptyAdapter, GoldViewAdapter
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

REPO = Path(__file__).resolve().parents[1]
TOY10K = REPO / "fixtures" / "toy10k"
# Pinned submission-file hashes for non-oracle adapters on the first 300 toy10k outcomes.
BASELINE = {
    "empty": {
        "bindings.jsonl": "25eb637700af220f135bc7ec6f4389a8c63d9d7ada214db0f8beffff96a8b6e5",
        "provenance.jsonl": "12c2ad694d07783ff28b3f89bd44d431c5add6dfbbcaf45c9df5eca2bf2bfbfa",
        "theory.json": "7963163841c3440b61e582845748d6e9a85a2c6793c70d82abeb45936d184ff2"
    },
    "scripted": {
        "bindings.jsonl": "ad42d10601ef6a3b1374749ab6ea48137cdbe39c42d670997f306389e0ccc9e8",
        "provenance.jsonl": "12c2ad694d07783ff28b3f89bd44d431c5add6dfbbcaf45c9df5eca2bf2bfbfa",
        "theory.json": "6de205c8c7e2935441746087a01dff469e7bf9538f010947b9051ea5e79befd4"
    }
}
NO_USAGE = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}


def _records(n):
    rows = sorted(ir.read_jsonl(TOY10K / "corpus.jsonl"), key=lambda r: r["id"])[:n]
    for r in rows:
        yield {"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r.get("event_title")}


class Scripted:
    """A pairwise, non-gold adapter: bound classes from the market and outcome index, and a few relations."""

    name = "scripted"

    def decide_binding(self, ctx):
        mid, idx = ctx["id"].split(":")[1], ctx["id"].rsplit(":", 1)[1]
        if idx in ("1", "2"):
            return {"status": "bound", "class": f"C{mid}", "negated": idx == "2", "missing": []}, usage(self.name)
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}, usage(self.name)

    def decide_relation(self, a, b):
        both_yes = a["id"].endswith(":1") and b["id"].endswith(":1")
        return ("EXCLUDES" if both_yes else "NONE"), usage(self.name)


def _digests(adapter):
    out = Path(tempfile.mkdtemp()) / "sub"
    fixed.build(_records(300), adapter, out, DEFAULT_PROFILE)
    return {f: hashlib.sha256((out / f).read_bytes()).hexdigest() for f in ("bindings.jsonl", "theory.json", "provenance.jsonl")}


def test_non_oracle_submissions_are_byte_identical_to_the_baseline():
    assert _digests(Scripted()) == BASELINE["scripted"]
    assert _digests(EmptyAdapter()) == BASELINE["empty"]


def test_check_binding_keeps_known_evidence_and_is_unchanged_without_it():
    plain = {"status": "bound", "class": "A", "negated": False, "missing": []}
    assert check_binding(plain) == {"status": "bound", "class": "A", "negated": False, "missing": []}
    refs = ["src:m1:question", "src:m1:outcome_label:1", "src:event:e1:event_title", "ax:binary_complement"]
    assert check_binding(dict(plain, evidence=refs))["evidence"] == refs
    unresolved = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"], "evidence": refs[:2]}
    assert check_binding(unresolved)["evidence"] == refs[:2]
    for bad in (["bogus:ref"], "src:m1:question", [1], None):
        with pytest.raises(DecisionError):
            check_binding(dict(plain, evidence=bad))


def _rows(evidence):
    base = {"status": "bound", "class": "A", "missing": [], "event_title": "Event"}
    rows = [dict(base, id="polymarket:m1:1", negated=False), dict(base, id="polymarket:m1:2", negated=True)]
    if evidence is not None:
        rows = [dict(r, evidence=evidence(r["id"])) for r in rows]
    return rows


def _write(rows, constraints, out):
    ir.write_submission(out, rows, constraints, ["base"], NO_USAGE)
    return {r["id"]: set(r["evidence"]) for r in ir.read_jsonl(out / "bindings.jsonl")}, {
        p["ref"]: p for p in ir.read_jsonl(out / "provenance.jsonl")}


def test_supplied_binding_evidence_is_written_verbatim_and_suppresses_the_derived_event_ref(tmp_path):
    refs = lambda oid: [f"src:{oid.split(':')[1]}:question", f"src:{oid.split(':')[1]}:outcome_label:{oid[-1]}",
                        "src:event:e1:event_title", "ax:binary_complement"]
    evidence, prov = _write(_rows(refs), [], tmp_path)
    assert evidence == {oid: set(refs(oid)) for oid in ("polymarket:m1:1", "polymarket:m1:2")}  # no src:m1:event_title
    assert prov["ax:binary_complement"] == {"ref": "ax:binary_complement", "kind": "axiom",
                                            "record": "ax:binary_complement", "field": "rule"}
    assert prov["src:event:e1:event_title"]["field"] == "event_title"


def test_without_supplied_evidence_the_derived_event_ref_is_unchanged(tmp_path):
    evidence, _ = _write(_rows(None), [], tmp_path)
    assert evidence["polymarket:m1:1"] == {"src:m1:question", "src:m1:outcome_label:1", "src:m1:event_title"}


def test_supplied_constraint_provenance_is_unioned_and_unknown_shapes_are_refused(tmp_path):
    refs = lambda oid: [f"src:{oid.split(':')[1]}:question", f"src:{oid.split(':')[1]}:outcome_label:{oid[-1]}", "ax:x"]
    con = {"id": "c1", "expr": {"op": "implies", "a": {"op": "var", "id": "polymarket:m1:1"},
                                "b": {"op": "var", "id": "polymarket:m1:2"}}, "interps": None,
           "provenance": ["ax:x", "src:event:e1:event_title"]}
    _, prov = _write(_rows(refs), [con], tmp_path)
    theory = ir.read_json(tmp_path / "theory.json")
    assert set(theory["constraints"][0]["provenance"]) == {"src:m1:question", "src:event:e1:event_title", "ax:x"}
    assert "src:m1:outcome_label:1" not in theory["constraints"][0]["provenance"]  # outcome labels are not cited by constraints
    with pytest.raises(ValueError):
        _write(_rows(lambda oid: ["bogus:ref"]), [], tmp_path / "bad")


def _gold_ref_fixture(tmp_path):
    gold = tmp_path / "gold"
    ids = ("toy:m1:1", "toy:m1:2")
    evidence = ["src:m1:question", "ax:binary_complement"]
    bindings = [
        {"id": ids[0], "status": "bound", "class": "A", "negated": False, "missing": [], "evidence": evidence},
        {"id": ids[1], "status": "bound", "class": "A", "negated": True, "missing": [], "evidence": evidence},
    ]
    constraint = {"id": "c1", "expr": {"op": "iff", "a": ir.var(ids[0]),
                                           "b": {"op": "not", "a": ir.var(ids[1])}},
                  "interps": None, "provenance": ["ax:binary_complement"]}
    ir.write_submission(gold, bindings, [constraint], ["base"], NO_USAGE)
    return gold


def test_gold_view_returns_gold_binding_evidence_and_empty_returns_none(tmp_path):
    gold = _gold_ref_fixture(tmp_path)
    gv = GoldViewAdapter(gold)
    rows = {r["id"]: r for r in ir.read_jsonl(gold / "bindings.jsonl")}
    oid = next(iter(rows))
    decision, _ = gv.decide_binding({"id": oid})
    assert set(decision["evidence"]) == set(rows[oid]["evidence"])
    decision, _ = EmptyAdapter().decide_binding({"id": oid})
    assert "evidence" not in decision

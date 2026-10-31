"""PB7 (ir side): supplied and derived provenance, event-title evidence, atomic submission writes,
streamed usage, outcome-ID validation and iterative expression walks."""

import json
import math

import pytest

from mentat_bench import ir


def _row(oid, cls="X", negated=False, event_title=None, status="bound"):
    row = {"id": oid, "status": status, "class": cls if status == "bound" else None, "negated": negated,
           "missing": [] if status == "bound" else ["no_date"]}
    if event_title is not None:
        row["event_title"] = event_title
    return row


def _usage(calls=()):
    return {"calls": list(calls), "wall_seconds": 1.5, "peak_rss_mb": 2.0}


def _read(out, name):
    return [json.loads(line) for line in (out / name).read_text(encoding="utf-8").splitlines()]


def test_binding_evidence_includes_event_title_only_when_present(tmp_path):
    rows = [_row("polymarket:m1:1", event_title="Ethereum above?"), _row("polymarket:m2:1")]
    ir.write_submission(tmp_path / "s", rows, [], ["base"], _usage())
    bindings = {r["id"]: r for r in _read(tmp_path / "s", "bindings.jsonl")}
    assert bindings["polymarket:m1:1"]["evidence"] == [
        "src:m1:question", "src:m1:outcome_label:1", "src:m1:event_title"]
    assert bindings["polymarket:m2:1"]["evidence"] == ["src:m2:question", "src:m2:outcome_label:1"]
    provenance = {r["ref"]: r for r in _read(tmp_path / "s", "provenance.jsonl")}
    assert provenance["src:m1:event_title"] == {"ref": "src:m1:event_title", "kind": "source", "record": "m1", "field": "event_title"}


def test_supplied_constraint_provenance_is_written_and_unioned_with_derived_refs(tmp_path):
    rows = [_row("polymarket:m1:1"), _row("polymarket:m2:1", cls="Y")]
    con = {
        "id": "c1",
        "expr": {"op": "implies", "a": ir.var("polymarket:m1:1"), "b": ir.var("polymarket:m2:1")},
        "interps": None,
        "provenance": ["ax:mutex_rule", "src:event:e42:event_title", "src:m2:outcome_label:1"],
    }
    ir.write_submission(tmp_path / "s", rows, [con], ["base"], _usage())
    theory = json.loads((tmp_path / "s" / "theory.json").read_text(encoding="utf-8"))
    (constraint,) = theory["constraints"]
    assert constraint["provenance"] == sorted([
        "src:m1:question", "src:m2:question", "ax:mutex_rule", "src:event:e42:event_title", "src:m2:outcome_label:1"])
    provenance = {r["ref"]: r for r in _read(tmp_path / "s", "provenance.jsonl")}
    assert set(constraint["provenance"]) <= set(provenance)  # every cited REF has a row
    assert provenance["ax:mutex_rule"]["kind"] == "axiom"
    assert provenance["src:event:e42:event_title"] == {"ref": "src:event:e42:event_title", "kind": "source",
                                                       "record": "e42", "field": "event_title"}


def test_constraint_made_only_of_alias_refs_cites_the_alias_source(tmp_path):
    rows = [_row("polymarket:m1:1", cls="X"), _row("polymarket:m2:1", cls="Y")]
    con = {"id": "c1", "expr": {"op": "or", "args": [{"op": "ref", "name": "X"}, {"op": "ref", "name": "Y"}]}, "interps": None}
    ir.write_submission(tmp_path / "s", rows, [con], ["base"], _usage())
    (constraint,) = json.loads((tmp_path / "s" / "theory.json").read_text(encoding="utf-8"))["constraints"]
    assert constraint["provenance"] == ["src:m1:question", "src:m2:question"]


def test_constraint_referencing_an_undefined_alias_is_rejected(tmp_path):
    con = {"id": "c1", "expr": {"op": "ref", "name": "Nope"}, "interps": None}
    with pytest.raises(ValueError, match="unknown aliases"):
        ir.write_submission(tmp_path / "s", [_row("polymarket:m1:1")], [con], ["base"], _usage())


def test_undefined_alias_is_named_among_many_defined_aliases(tmp_path):
    rows = [_row(f"polymarket:m{i}:1", cls=f"C{i}") for i in range(500)]
    defined = [{"id": f"d{i}", "expr": {"op": "ref", "name": f"C{i}"}, "interps": None} for i in range(500)]
    ir.write_submission(tmp_path / "ok", rows, defined, ["base"], _usage())  # every alias defined: accepted
    con = {"id": "c1", "expr": {"op": "or", "args": [{"op": "ref", "name": "C7"}, {"op": "ref", "name": "Missing"}]},
           "interps": None}
    with pytest.raises(ValueError, match=r"unknown aliases: \['Missing'\]"):
        ir.write_submission(tmp_path / "s", rows, [con], ["base"], _usage())


def test_unsupported_provenance_ref_is_rejected(tmp_path):
    con = {"id": "c1", "expr": ir.var("polymarket:m1:1"), "interps": None, "provenance": ["weird:ref"]}
    with pytest.raises(ValueError, match="unsupported provenance ref"):
        ir.write_submission(tmp_path / "s", [_row("polymarket:m1:1")], [con], ["base"], _usage())


def test_duplicate_and_malformed_outcome_ids_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        ir.write_submission(tmp_path / "d", [_row("polymarket:m1:1"), _row("polymarket:m1:1")], [], ["base"], _usage())
    for bad in ("m1:1", "a:b:c", "a::1", 7):
        with pytest.raises(ValueError, match="malformed"):
            ir.validate_outcome_id(bad)


def test_failed_write_leaves_no_truncated_submission_files(tmp_path):
    bad = {"id": "c1", "expr": ir.var("polymarket:zz:9"), "interps": None}  # outcome not in the bindings
    out = tmp_path / "s"
    with pytest.raises(ValueError, match="unknown outcomes"):
        ir.write_submission(out, [_row("polymarket:m1:1")], [bad], ["base"], _usage())
    assert not out.exists() or not any(out.iterdir())


def test_non_finite_usage_is_refused_and_leaves_nothing_behind(tmp_path):
    out = tmp_path / "s"
    with pytest.raises(ValueError):
        ir.write_submission(out, [_row("polymarket:m1:1")], [], ["base"], {"calls": [], "wall_seconds": math.nan})
    assert not any(out.iterdir())


def test_usage_calls_are_streamed_from_an_iterable(tmp_path):
    calls = [{"model": "m", "kind": "local", "tokens_in": i, "tokens_out": 0, "seconds": 0.0, "cost_usd": 0.5, "retry": False}
             for i in range(3)]
    consumed = []

    def gen():
        for call in calls:
            consumed.append(call["tokens_in"])
            yield call

    ir.write_submission(tmp_path / "s", [_row("polymarket:m1:1")], [], ["base"], {"calls": gen(), "wall_seconds": 1.0, "peak_rss_mb": 3.0})
    usage = json.loads((tmp_path / "s" / "usage.json").read_text(encoding="utf-8"))
    assert usage == {"calls": calls, "wall_seconds": 1.0, "peak_rss_mb": 3.0}
    assert consumed == [0, 1, 2]


def test_expr_walks_are_iterative_on_very_deep_expressions():
    expr = ir.var("polymarket:m1:1")
    for _ in range(50_000):
        expr = {"op": "not", "a": expr}
    assert ir.expr_vars(expr) == {"polymarket:m1:1"}
    assert ir.expr_refs({"op": "not", "a": {"op": "ref", "name": "A"}}) == {"A"}

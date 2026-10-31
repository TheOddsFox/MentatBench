"""tools/eval/floor_calibrate.py (MEASUREMENT ONLY): the precision curve over the confs, the lowest-threshold rule and its
no-floor case, the population and gold orientation on a hand-written work dir (extra gold ids, derived rows, rejected
counts), the exit status and --json, and on a real build (complement dedup on): the floor the tool predicts keeps exactly
the relation rows the harness keeps, by id, read from the finalized theory."""

import itertools
import json
import sys
from pathlib import Path

import pytest

from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE
from test_admit import _Child, _cross

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "eval"))
import floor_calibrate as FC  # noqa: E402

NONE_FLOOR = "--relation-floor 1.7976931348623157e+308"  # sys.float_info.max


def _row(conf, rel, entailed, index=0):
    return FC.Row(index, conf, rel, entailed, ("x", "y"))


def test_the_floor_is_the_lowest_threshold_that_reaches_the_target_even_when_precision_is_not_monotone():
    # (conf, relation, gold-entailed): precision by t is 0.8, 0.75, 1.0, 1.0 (t = 4, 5, 6, 7)
    rows = [_row(4.0, "IMPLIES", 1), _row(5.0, "IMPLIES", 0), _row(6.0, "EXCLUDES", 1), _row(7.0, "IMPLIES", 1),
            _row(7.0, "IMPLIED_BY", 1)]
    curve, no_floor = FC.sweep(rows)
    assert [(p["t"], p["kept"], p["entailed"]) for p in curve] == [(4.0, 5, 4), (5.0, 4, 3), (6.0, 3, 3), (7.0, 2, 2)]
    assert [p["precision"] for p in curve] == [0.8, 0.75, 1.0, 1.0]
    assert curve[0]["per_op"]["IMPLIES"] == {"kept": 3, "entailed": 2, "precision": 0.6667}
    assert no_floor["t"] is None and (no_floor["kept"], no_floor["entailed"]) == (5, 4)
    assert FC.choose(curve, 0.8)["t"] == 4.0  # the lowest t meets 0.8 (4 of 5 is exactly 0.8), though 7 also does
    assert FC.choose(curve, 0.81)["t"] == 6.0


def test_no_threshold_reaching_the_target_names_no_relations_and_conf_less_rows_are_always_kept():
    rows = [_row(4.0, "IMPLIES", 0), _row(5.0, "EXCLUDES", 0), _row(6.0, "IMPLIES", 1), _row(None, "IMPLIES", 0)]
    curve, _ = FC.sweep(rows)
    assert [(p["t"], p["kept"], p["entailed"]) for p in curve] == [(4.0, 4, 1), (5.0, 3, 1), (6.0, 2, 1)]
    assert FC.choose(curve, 0.8) is None
    assert FC.sweep([]) == ([], {"t": None, "kept": 0, "entailed": 0, "precision": None,
                                 "per_op": {op: {"kept": 0, "entailed": 0, "precision": None} for op in FC.OPS}})


def _gold_fixture(tmp_path, ids, constraints):
    fx = tmp_path / "fx"
    (fx / "gold").mkdir(parents=True)
    (fx / "corpus.jsonl").write_text("".join(json.dumps({"id": x, "question": "q", "label": "Yes"}) + "\n" for x in ids))
    (fx / "gold" / "theory.json").write_text(json.dumps({"interpretations": ["I0"], "constraints": [{"expr": c} for c in constraints]}))
    (fx / "gold" / "bindings.jsonl").write_text("".join(json.dumps({"id": x, "status": "unresolved"}) + "\n" for x in ids))
    return fx


def _var(oid):
    return {"op": "var", "id": oid}


A1, A2, B1, B2 = "toy:a:1", "toy:a:2", "toy:b:1", "toy:b:2"
Z = "toy:z:9"  # a gold id outside the corpus: the closure a1 -> z -> b1 needs it
GOLD = [
    {"op": "implies", "a": _var(A1), "b": _var(Z)},
    {"op": "implies", "a": _var(Z), "b": _var(B1)},
    {"op": "atmost", "k": 1, "args": [_var(A1), _var(B2)]},
    {"op": "atleast", "k": 2, "args": [_var(A1), _var(A2), _var(B1)]},  # outside 2-CNF: ignored
]


def _work(tmp_path, rows, name="work"):
    """A hand-written kept work dir over the four corpus ids (index 0..3 = a1, a2, b1, b2) with the given relation rows."""
    work = tmp_path / name
    work.mkdir(parents=True)
    (work / "decided.jsonl").write_text("".join(json.dumps({"id": x, "status": "unresolved"}) + "\n" for x in (A1, A2, B1, B2)))
    (work / "relations.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    ckpt = {"decided_bytes": (work / "decided.jsonl").stat().st_size, "relations_bytes": (work / "relations.jsonl").stat().st_size,
            "equiv_rejected": 2, "relation_rejected": 1}
    (work / "checkpoint.json").write_text(json.dumps(ckpt))
    return work


def _fixture4(tmp_path):
    return _gold_fixture(tmp_path, [A1, A2, B1, B2], GOLD)


# relations.jsonl rows as the build writes them (i, j, rel[, rep line, conf]): the orientation is as written; IMPLIED_BY(x, y)
# means y implies x. The last row is derived from decision 0 (its conf inherited) and is not gold-entailed.
ROWS = [
    [0, 2, "IMPLIES", -1, 5.0],
    [2, 0, "IMPLIED_BY", -1, 6.0],
    [0, 2, "IMPLIED_BY", -1, 4.0],
    [0, 3, "EXCLUDES", -1],  # no conf
    [1, 3, "EXCLUDES", 0, 5.0],  # derived
]


def test_population_orientation_extra_gold_ids_and_derived_rows(tmp_path):
    work, fx = _work(tmp_path, ROWS), _fixture4(tmp_path)
    decisions, derived, gold, _ = FC.population(work, fx)
    assert [(r.rel, r.entailed, r.ids) for r in decisions] == [
        ("IMPLIES", 1, (A1, B1)), ("IMPLIED_BY", 1, (B1, A1)), ("IMPLIED_BY", 0, (A1, B1)), ("EXCLUDES", 1, (A1, B2))]
    assert [(r.rel, r.entailed, r.conf) for r in derived] == [("EXCLUDES", 0, 5.0)]
    assert gold.ignored == {"atleast": 1}  # only the atleast is outside 2-CNF; the closure through Z is in


def test_the_curve_and_the_chosen_floor_keep_the_same_rows_by_id(tmp_path):
    work, fx = _work(tmp_path, ROWS), _fixture4(tmp_path)
    out = FC.calibrate(work, fx, 0.8)
    decisions = FC.population(work, fx)[0]
    assert [(p["t"], p["kept"], p["entailed"]) for p in out["curve"]] == [(4.0, 4, 3), (5.0, 3, 3), (6.0, 2, 2)]
    assert out["floor"] == 5.0 and out["harness_args"] == "--relation-floor 5.0" and out["names_relations"]
    kept_ids = {(r.rel, r.ids) for r in FC.kept(decisions, out["floor"])}
    assert kept_ids == {("IMPLIES", (A1, B1)), ("IMPLIED_BY", (B1, A1)), ("EXCLUDES", (A1, B2))}  # conf 4.0 row dropped
    assert out["derived_at_floor"] == {"floor": 5.0, "rows": 1, "kept": 1, "entailed_kept": 0, "precision": 0.0}
    assert out["population"] == {"named_decisions": 4, "with_conf": 3, "without_conf": 1, "derived_rows": 1,
                                 "equiv_rejected": 2, "relation_rejected": 1}
    assert out["gold_ignored_ops"] == {"atleast": 1} and out["partial_reasons"] == []
    assert out["fixture_stamp"] is None and set(out["fixture_sha256"]) == {"corpus.jsonl", "gold/theory.json"}


def test_partial_reasons_come_from_the_checkpoint_and_the_sibling_partial_file(tmp_path):
    work = _work(tmp_path, ROWS, name="sub.build")
    ckpt = json.loads((work / "checkpoint.json").read_text())
    ckpt["pairs_capped"] = True
    (work / "checkpoint.json").write_text(json.dumps(ckpt))
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "PARTIAL.json").write_text(json.dumps({"partial": True, "reasons": ["pairs_capped", "budget_stopped"]}))
    assert FC.calibrate(work, _fixture4(tmp_path), 0.8)["partial_reasons"] == ["pairs_capped", "budget_stopped"]


def test_no_hit_with_conf_less_rows_has_no_harness_argument_and_exits_nonzero(tmp_path, capsys):
    work, fx = _work(tmp_path, [[0, 2, "IMPLIED_BY", -1, 4.0], [0, 3, "EXCLUDES", -1]]), _fixture4(tmp_path)
    out_json = tmp_path / "out.json"
    assert FC.main([str(work), "--fixture-dir", str(fx), "--json", str(out_json)]) == 1
    text = capsys.readouterr().out
    assert "no threshold reaches it" in text and "harness:" not in text and "no harness argument" in text
    assert json.loads(out_json.read_text())["harness_args"] is None


def test_zero_named_and_all_conf_less_cases(tmp_path, capsys):
    fx = _fixture4(tmp_path)
    assert FC.main([str(_work(tmp_path / "zero", [])), "--fixture-dir", str(fx)]) == 0  # nothing named: names none
    assert NONE_FLOOR in capsys.readouterr().out
    assert FC.main([str(_work(tmp_path / "less", [[0, 3, "EXCLUDES", -1]])), "--fixture-dir", str(fx)]) == 1
    assert "harness:" not in capsys.readouterr().out


def _three_markets():
    recs = [{"id": f"toy:{m}:{k}", "question": f"Will alpha team reach {m}{k} points in 2026?", "label": "Yes", "event_title": "e"}
            for m in "abc" for k in (1, 2)]
    bindings = {}
    for m in "abc":
        bindings[f"toy:{m}:1"] = {"status": "bound", "class": m.upper(), "negated": False, "missing": []}
        bindings[f"toy:{m}:2"] = {"status": "bound", "class": m.upper(), "negated": True, "missing": []}
    return recs, bindings


def test_the_floor_the_tool_predicts_keeps_the_same_rows_as_the_harness(tmp_path):
    recs, bindings = _three_markets()
    ids = [r["id"] for r in recs]
    conf = {pair: 4.0 + 0.5 * k for k, pair in enumerate(itertools.combinations(ids, 2))}
    conf[("toy:b:1", "toy:c:1")] = 30.0  # a toy10k-style conf above every other dev conf (the pair is asked: a representative)
    child = _Child(bindings, _cross, with_conf=lambda a, b: conf.get((a, b), conf.get((b, a))))
    fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE), keep_work=True)  # complement dedup on
    work = tmp_path / "sub.build"
    fx = _gold_fixture(tmp_path, ids, [{"op": "implies", "a": _var("toy:a:1"), "b": _var("toy:b:1")}])
    decisions, derived, _, _ = FC.population(work, fx)
    assert len(derived) == 6 and len({r.conf for r in decisions}) == 3 and max(r.conf for r in decisions) == 30.0
    out = FC.calibrate(work, fx, 0.8)  # only a1 -> b1 is gold: no threshold reaches 0.8, so the model names no relations
    assert out["names_relations"] is False and out["harness_args"] == NONE_FLOOR
    floors = [p["t"] for p in out["curve"]] + [FC.NO_RELATION_FLOOR]
    for k, t in enumerate(floors):  # the harness drops a row iff its conf is below the floor (equal is kept)
        rows = FC.kept(decisions + derived, t)
        summary = fixed.finalize_checkpoint(work, tmp_path / f"fin{k}", dict(DEFAULT_PROFILE), relation_floor=t)
        assert summary["floor_dropped_relations"] == sum(1 for r in decisions if r.conf is not None and r.conf < t), t
        theory = json.loads((tmp_path / f"fin{k}" / "theory.json").read_text(encoding="utf-8"))
        harness_kept = {int(c["id"][1:]) for c in theory["constraints"] if c["id"].startswith("p")}
        assert harness_kept == {r.index for r in rows}, t  # the same relation rows, by id, derived rows with their rep


# The label mode (--labels): named relation decisions judged by suite labels; the candidates give the order each pair is labelled in.
LABEL_CANDIDATES = [{"qid": f"r{i}", "op": "decide_relation", "a": "x", "b": "y"} for i in range(1, 8)]
LABELS = [{"qid": f"r{i}", "op": "decide_relation", "label": lab, "k": None, "missing": None, "held_out": False, "stratum": "real"}
          for i, lab in enumerate(["EXCLUDES", "IMPLIES", "EXCLUDES", "IMPLIES", "IMPLIES", "EQUIV", "IMPLIES"], start=1)]


def _decision(qid, relation, conf, asked=("x", "y")):
    return {"qid": qid, "op": "decide_relation", "a": asked[0], "b": asked[1], "decision": relation, "conf": conf, "probs": None,
            "error": None}


# named decisions: (conf, correct) = (4, no), (5, yes), (6, yes), (7, yes: IMPLIED_BY asked (y, x) is IMPLIES), (7, no: exact),
# (8, yes); r7 names NONE, so it is not in the population
LABEL_DECISIONS = [_decision("r1", "IMPLIES", 4.0), _decision("r2", "IMPLIES", 5.0), _decision("r3", "EXCLUDES", 6.0),
                   _decision("r4", "IMPLIED_BY", 7.0, asked=("y", "x")), _decision("r5", "EQUIV", 7.0),
                   _decision("r6", "EQUIV", 8.0), _decision("r7", "NONE", 9.0)]


def _label_files(tmp_path, decisions, labels=LABELS):
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, rows in (("labels", labels), ("candidates", LABEL_CANDIDATES), ("decisions", decisions)):
        paths[name] = tmp_path / f"{name}.jsonl"
        paths[name].write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return paths["labels"], paths["candidates"], paths["decisions"]


def test_label_mode_judges_named_decisions_exactly_in_the_candidate_order(tmp_path):
    labels, cands, decs = _label_files(tmp_path, LABEL_DECISIONS)
    out = FC.calibrate_labels(labels, decs, cands, 0.8)
    assert out["population"] == {"named_decisions": 6, "with_conf": 6, "without_conf": 0}
    assert out["counts"]["oriented"] == 1
    # precision by t over the distinct confs: 4 -> 4/6, 5 -> 4/5 (the lowest reaching 0.8), 6 -> 3/4, 7 -> 2/3, 8 -> 1/1
    assert [(p["t"], p["kept"], p["entailed"]) for p in out["curve"]] == [(4.0, 6, 4), (5.0, 5, 4), (6.0, 4, 3), (7.0, 3, 2), (8.0, 1, 1)]
    assert out["floor"] == 5.0 and out["names_relations"] and out["harness_args"] == "--relation-floor 5.0"


def test_label_mode_is_the_same_rule_as_the_2cnf_mode_and_its_exit_codes(tmp_path, capsys):
    labels, cands, decs = _label_files(tmp_path, LABEL_DECISIONS)
    out_json = tmp_path / "out.json"
    assert FC.main(["--labels", str(labels), "--decisions", str(decs), "--candidates", str(cands), "--json", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "harness: --relation-floor 5.0" in text and "floor 5.0" in text
    assert json.loads(out_json.read_text())["floor"] == 5.0
    # no named decision is correct enough and the named ones are conf-less: no harness argument, exit 1
    conf_less = [_decision("r1", "IMPLIES", None), _decision("r2", "EXCLUDES", None)]
    labels, cands, decs = _label_files(tmp_path / "less", conf_less)
    assert FC.main(["--labels", str(labels), "--decisions", str(decs), "--candidates", str(cands)]) == 1
    assert "no harness argument" in capsys.readouterr().out
    # nothing named (only NONE): the model names no relations, the argument is the finite maximum, exit 0
    labels, cands, decs = _label_files(tmp_path / "none", [_decision("r7", "NONE", 9.0)])
    assert FC.main(["--labels", str(labels), "--decisions", str(decs), "--candidates", str(cands)]) == 0
    assert NONE_FLOOR in capsys.readouterr().out


def test_label_mode_keeps_conf_less_named_decisions_at_every_threshold(tmp_path):
    rows = [_decision("r1", "IMPLIES", None), _decision("r2", "IMPLIES", 4.0), _decision("r3", "IMPLIES", 5.0)]
    labels, cands, decs = _label_files(tmp_path, rows)  # labels: r1 EXCLUDES (wrong), r2 IMPLIES, r3 EXCLUDES (wrong)
    out = FC.calibrate_labels(labels, decs, cands, 0.8)
    assert out["population"]["without_conf"] == 1
    assert [(p["t"], p["kept"], p["entailed"]) for p in out["curve"]] == [(4.0, 3, 1), (5.0, 2, 0)]  # r1 kept at 4 and 5
    assert out["none_floor"]["conf_less_kept"] == 1 and out["harness_args"] is None  # no hit, and r1 is conf-less: exit 1


def test_label_mode_requires_decisions_and_candidates_and_no_work_dir(tmp_path):
    labels, cands, decs = _label_files(tmp_path, LABEL_DECISIONS)
    with pytest.raises(SystemExit):
        FC.main(["--labels", str(labels), "--decisions", str(decs)])
    with pytest.raises(SystemExit):
        FC.main([str(tmp_path), "--labels", str(labels), "--decisions", str(decs), "--candidates", str(cands)])


def test_label_mode_leaves_out_a_named_decision_with_a_non_finite_conf(tmp_path):
    rows = [_decision("r1", "IMPLIES", float("nan")), _decision("r2", "IMPLIES", 5.0)]  # r1 is a NaN conf: unanswered, not named
    labels, cands, decs = _label_files(tmp_path, rows)
    out = FC.calibrate_labels(labels, decs, cands, 0.8)
    assert out["counts"]["invalid_conf"] == 1
    assert out["population"] == {"named_decisions": 1, "with_conf": 1, "without_conf": 0}
    assert [(p["t"], p["kept"], p["entailed"]) for p in out["curve"]] == [(5.0, 1, 1)]  # r2 has label IMPLIES: correct

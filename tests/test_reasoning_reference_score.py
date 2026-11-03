"""The reasoning reference scorer and capture (docs/audits/v0.2.0-reasoning-reference/): the answer normalisation (the v0.1.1 audit's
edge cases, __missing__ for invalid), the gold round trip, the weighted MCC against decision_scoring.mcc on the expanded items, the
scorer fed a model's own predictions through the extract path (it reproduces that model's weighted MCC), the protocol (void versus
schema error), the ledger (overwrite, second void, sha change), double void scoring, the capture runner (with a patched subprocess;
the audit never calls claude), the pinned results.json and representativeness.json, and the instruction wording."""

import json
import random
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "audits" / "v0.2.0-reasoning-reference"
sys.path.insert(0, str(AUDIT))
import representativeness as RP  # noqa: E402
import run_slice as RS  # noqa: E402
import score as SC  # noqa: E402
from mentat_bench import decision_scoring as DS  # noqa: E402

sys.path.insert(0, str(ROOT / "tools" / "eval"))
import league_ci as LC  # noqa: E402

KEY, SLICES = SC.load_key()
BY_ID = {row["id"]: row for row in KEY}


def answer_for(cls: str, n: int) -> dict:
    """An answer object whose normalisation is the class (the inverse of score.normalised, for the tests)."""
    if cls == "none":
        return {"kind": "none", "lower": 0, "upper": n, "chain": []}
    chain, lo, hi, first = False, 0, n, None
    for term in cls.split("+"):
        kind, _, k = term.partition(":")
        first = first or kind
        if kind == "ordered_chain":
            chain = True
        elif kind == "exactly_one":
            lo, hi = 1, 1
        elif kind == "at_most_one":
            hi = 1
        elif kind == "at_least_k":
            lo = int(k)
        elif kind == "at_most_k":
            hi = int(k)
    return {"kind": "ordered_chain" if chain else first, "lower": lo, "upper": hi, "chain": []}


def gold_answer(row: dict) -> dict:
    return {"relation": row["gold"]} if row["op"] == "decide_relation" else answer_for(row["gold"], row["n_members"])


def norm(op: str, n, answer):
    return SC.normalised({"op": op, "n_members": n}, answer)


def slice_ids(j: int) -> list[str]:
    return next(s["ids"] for s in SLICES["slices"] if s["slice"] == j)


def answer_line(row: dict, pred: str) -> str:
    answer = {"relation": pred} if row["op"] == "decide_relation" else answer_for(pred, row["n_members"])
    return json.dumps({"id": row["id"], "answer": answer})


def result_doc(result: str, **fields) -> dict:
    """A claude --output-format json result object (the fields the protocol reads, and permission_denials)."""
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "permission_denials": [],
            "result": result, **fields}


def fenced(lines: list[str]) -> str:
    return "Reasoning in prose.\n```jsonl\n" + "\n".join(lines) + "\n```"


def put_raw(base: Path, inst: int, j: int, attempt: int, data: bytes) -> Path:
    path = SC.raw_path(base, inst, j, attempt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def valid_raw(preds: dict[str, str], ids: list[str], **fields) -> bytes:
    lines = [answer_line(BY_ID[i], preds[i]) for i in ids]
    return json.dumps(result_doc(fenced(lines), **fields)).encode("utf-8")


def write_instance(base: Path, inst: int, preds: list[str], skip: tuple = ()) -> None:
    """Every slice of one instance (but those in skip) answered with the given class predictions (key order), through extract."""
    by_id = {row["id"]: p for row, p in zip(KEY, preds)}
    for s in SLICES["slices"]:
        if s["slice"] in skip:
            continue
        put_raw(base, inst, s["slice"], 1, valid_raw(by_id, s["ids"]))
        SC.extract(inst, s["slice"], 1, base=base)


# ---- normalisation


def test_relation_answers_normalise_as_in_the_audit():
    rel = "decide_relation"
    assert norm(rel, None, {"relation": "EQUIV"}) == "EQUIV"
    assert norm(rel, None, {"relation": " IMPLIES "}) == "IMPLIES"  # the label is stripped
    assert norm(rel, None, {"relation": "equiv"}) == DS.MISSING  # labels are case-sensitive
    assert norm(rel, None, {"relation": "EQUIVALENT"}) == DS.MISSING
    assert norm(rel, None, {"relation": 3}) == DS.MISSING
    assert norm(rel, None, {}) == DS.MISSING
    assert norm(rel, None, None) == DS.MISSING and norm(rel, None, "junk") == DS.MISSING
    assert norm(rel, None, ["EQUIV"]) == DS.MISSING
    assert norm(rel, None, '{"relation": "NONE"}') == "NONE"  # an answer written as a JSON string


def test_group_answers_normalise_as_in_the_audit():
    grp, n = "decide_group", 3
    assert norm(grp, n, {"kind": "none", "lower": 2, "upper": 1, "chain": []}) == "none"  # the bounds are not read for none
    assert norm(grp, n, {"kind": "EXACTLY_ONE", "lower": 1, "upper": 1, "chain": []}) == "exactly_one"  # kind is lower-cased
    assert norm(grp, n, {"kind": "at_most_one", "lower": 0, "upper": 1, "chain": []}) == "at_most_one"
    assert norm(grp, n, {"kind": "at_least_k", "lower": 1, "upper": 3, "chain": []}) == "at_least_k:1"
    assert norm(grp, n, {"kind": "at_least_k", "lower": 2, "upper": 3, "chain": []}) == "at_least_k:2"
    assert norm(grp, n, {"kind": "at_most_k", "lower": 0, "upper": 2, "chain": []}) == "at_most_k:2"
    assert norm(grp, n, {"kind": "ordered_chain", "lower": 0, "upper": 3, "chain": [1, 2, 3]}) == "ordered_chain"
    assert norm(grp, n, {"kind": "ordered_chain", "lower": 1, "upper": 3, "chain": []}) == "ordered_chain+at_least_k:1"
    assert norm(grp, n, '{"kind": "none", "lower": 0, "upper": 3}') == "none"  # a JSON-string group answer
    assert norm(grp, n, {"kind": "at_least_k", "lower": 1, "upper": 3, "chain": [2, 1]}) == "at_least_k:1"  # chain not scored
    # invalid: a missing kind or an unknown one, bounds that are not integers in 0..n, bools, bounds the terms cannot hold
    assert norm(grp, n, {"lower": 0, "upper": 3}) == DS.MISSING
    assert norm(grp, n, {"kind": "bogus", "lower": 0, "upper": 3}) == DS.MISSING
    assert norm(grp, n, {"kind": "at_least_k", "lower": 4, "upper": 3}) == DS.MISSING  # lower above n
    assert norm(grp, n, {"kind": "at_least_k", "lower": -1, "upper": 3}) == DS.MISSING
    assert norm(grp, n, {"kind": "at_least_k", "lower": 1.0, "upper": 3}) == DS.MISSING  # a float is not an integer here
    assert norm(grp, n, {"kind": "at_least_k", "lower": True, "upper": 3}) == DS.MISSING  # a bool is not a count
    assert norm(grp, n, {"kind": "at_most_k", "lower": 0, "upper": 0}) == DS.MISSING  # upper below 1
    assert norm(grp, n, {"kind": "at_least_k", "lower": 3, "upper": 2}) == DS.MISSING  # lower above upper
    assert norm(grp, n, None) == DS.MISSING


def test_every_gold_answers_back_to_its_gold_class():
    wrong = [r["id"] for r in KEY if SC.normalised(r, gold_answer(r)) != r["gold"]]
    assert not wrong, wrong[:10]


def test_weighted_mcc_is_decision_scoring_mcc_on_the_expanded_items():
    rng = random.Random("reasoning-reference-test")
    classes = {"decide_relation": ["EQUIV", "IMPLIES", "NONE", DS.MISSING], "decide_group": ["none", "exactly_one", "at_most_k:2"]}
    key, preds = [], []
    for i in range(80):
        op = "decide_relation" if i % 3 else "decide_group"
        gold = rng.choice(classes[op])
        key.append({"op": op, "gold": gold, "weight": rng.randint(1, 4)})
        preds.append(rng.choice(classes[op]))
    leagues_of = np.array([rng.randrange(3) for _ in key])
    L = 3
    W = LC.draws(50, 1, L)
    ones = np.ones((1, L), dtype=np.int64)
    weighted = SC.mcc_parts(SC.confusion_parts(key, preds, classes, leagues_of, L, True), ones, W)
    unit = SC.mcc_parts(SC.confusion_parts(key, preds, classes, leagues_of, L, False), ones, W)
    for op in ("decide_relation", "decide_group"):
        rows = [(r["gold"], p) for r, p in zip(key, preds) if r["op"] == op]
        expanded = [(r["gold"], p) for r, p in zip(key, preds) if r["op"] == op for _ in range(r["weight"])]
        assert unit[op][0] == pytest.approx(DS.mcc(rows), abs=1e-12)
        assert weighted[op][0] == pytest.approx(DS.mcc(expanded), abs=1e-12)
        for b in range(W.shape[0]):  # a league drawn k times adds its items k times, each at its weight
            picked = [(r["gold"], p) for r, p, lg in zip(key, preds, leagues_of)
                      if r["op"] == op for _ in range(int(W[b][lg])) for _ in range(r["weight"])]
            if picked:
                assert weighted[op][1][b] == pytest.approx(DS.mcc(picked), abs=1e-9)
            else:
                assert np.isnan(weighted[op][1][b])


def test_spearman_averages_ties():
    assert SC._ranks([3.0, 1.0, 2.0, 2.0]) == [4.0, 1.0, 2.5, 2.5]
    assert SC.spearman([1.0, 2.0, 3.0], [3.0, 5.0, 9.0]) == pytest.approx(1.0)
    assert SC.spearman([1.0, 1.0], [1.0, 2.0]) is None


def test_fleiss_kappa_and_the_full_agreement_case():
    assert SC.fleiss_kappa([["a", "a", "a"], ["b", "b", "b"], ["a", "a", "a"], ["b", "b", "b"]]) == pytest.approx(1.0)
    assert SC.fleiss_kappa([["a", "a", "a"], ["a", "a", "a"]]) is None  # one category: undefined
    # 4 items, 3 raters each, split 2-1 in every item: P_bar 1/3, chance 1/2, kappa -1/3
    assert SC.fleiss_kappa([["a", "b", "a"], ["b", "a", "b"], ["a", "b", "b"], ["b", "a", "a"]]) == pytest.approx(-1 / 3)


def test_agreement_counts_two_missing_and_also_reports_items_both_answered():
    key = [{"op": "decide_relation"}, {"op": "decide_relation"}, {"op": "decide_relation"}]
    per = {"inst1": ["EQUIV", DS.MISSING, "NONE"], "inst2": ["EQUIV", DS.MISSING, "IMPLIES"], "inst3": ["EQUIV", "NONE", "NONE"]}
    out = SC.agreement(key, per)["decide_relation"]
    assert out["pairwise"]["inst1-inst2"] == pytest.approx(2 / 3)  # the two __missing__ answers agree
    # both answered: items 0 (EQUIV, EQUIV) and 2 (NONE, IMPLIES); item 1 is __missing__ for both and is left out
    assert out["pairwise_both_answered"]["inst1-inst2"] == pytest.approx(0.5)
    assert out["both_answered_items"]["inst1-inst2"] == 2
    assert out["pairwise"]["inst2-inst3"] == pytest.approx(1 / 3)


# ---- the scorer, fed through the extract path


def test_fed_its_own_predictions_the_scorer_reproduces_a_models_weighted_mcc(tmp_path):
    model = "d3_27b-q8_0"
    write_instance(tmp_path, 1, SC.model_predictions(KEY)[model])
    doc = SC.run(tmp_path)
    assert doc["status"] == "scored" and doc["instances"]["1"]["status"] == "complete"
    assert doc["instances"]["2"]["status"] == "absent" and doc["three_instances"]["status"] == "incomplete"
    assert doc["instances"]["1"]["weighted"] == doc["models"][model]["weighted"]
    assert doc["instances"]["1"]["unweighted"] == doc["models"][model]["unweighted"]
    assert doc["instances"]["1"]["by_depth"] == doc["models"][model]["by_depth"]
    assert all(entry["verdict"] == "valid" for entry in SC.read_ledger(tmp_path))


def test_three_instances_are_summarised_per_draw_and_paired(tmp_path):
    models = SC.model_predictions(KEY)
    picks = {1: "d3_27b-q8_0", 2: "clef_27b-q8_0", 3: "kev_9b-bf16"}
    for i, m in picks.items():
        write_instance(tmp_path, i, models[m])
    doc = SC.run(tmp_path)
    three = doc["three_instances"]
    assert three["status"] == "complete" and three["complete_instances"] == [1, 2, 3]
    for name in ("relation", "group", "headline"):  # the main point is the mean of the three instances' points
        mean = sum(doc["instances"][str(i)]["weighted"][name]["point"] for i in picks) / 3
        assert three["weighted"][name]["point"] == pytest.approx(mean, abs=2e-6), name
        lo, hi = three["weighted"][name]["lo"], three["weighted"][name]["hi"]
        assert lo is None or lo <= three["weighted"][name]["point"] <= hi, name
    rng = three["instance_range"]["relation"]
    assert rng["min"] <= rng["max"]
    assert set(three["agreement"]["decide_relation"]["pairwise"]) == {"inst1-inst2", "inst1-inst3", "inst2-inst3"}
    assert doc["instances"]["1"]["slices"][0]["items"] == 50 and len(doc["instances"]["1"]["slices"]) == 10


def test_a_double_void_slice_is_missing_for_its_instance_and_the_instance_is_still_scored(tmp_path):
    models = SC.model_predictions(KEY)
    write_instance(tmp_path, 1, models["d3_27b-q8_0"])
    write_instance(tmp_path, 2, models["clef_27b-q8_0"], skip=(1,))
    # instance 2, slice 1: a void attempt 1 (an error result), then a void attempt 2 (a second turn)
    put_raw(tmp_path, 2, 1, 1, json.dumps(result_doc("x", is_error=True)).encode())
    assert SC.extract(2, 1, 1, base=tmp_path)["verdict"] == "void"
    put_raw(tmp_path, 2, 1, 2, json.dumps(result_doc("x", num_turns=2)).encode())
    assert SC.extract(2, 1, 2, base=tmp_path)["verdict"] == "void"
    doc = SC.run(tmp_path)
    entry = doc["instances"]["2"]
    assert entry["status"] == "complete" and entry["protocol"]["slices"]["1"] == "double_void"
    assert [(v["slice"], v["attempt"]) for v in entry["protocol"]["voids"]] == [(1, 1), (1, 2)]
    assert entry["slices"][0]["all"]["correct"] == 0  # the slice's 50 items are __missing__ (wrong)
    assert entry["slices"][0]["all"]["n"] == 50
    assert doc["instances"]["1"]["status"] == "complete"
    assert doc["three_instances"]["status"] == "incomplete"  # instance 3 has not been run


def test_a_void_attempt_1_is_pending_until_its_one_re_run(tmp_path):
    preds = SC.model_predictions(KEY)["d3_27b-q8_0"]
    write_instance(tmp_path, 1, preds, skip=(1,))
    put_raw(tmp_path, 1, 1, 1, json.dumps(result_doc("x", is_error=True)).encode())  # slice 1 void, attempt 1
    SC.extract(1, 1, 1, base=tmp_path)
    doc = SC.run(tmp_path)
    assert doc["instances"]["1"]["status"] == "incomplete"
    assert doc["instances"]["1"]["protocol"]["slices"]["1"] == "pending"
    put_raw(tmp_path, 1, 1, 2, valid_raw({r["id"]: p for r, p in zip(KEY, preds)}, slice_ids(1)))
    assert SC.extract(1, 1, 2, base=tmp_path)["verdict"] == "valid"
    assert SC.run(tmp_path)["instances"]["1"]["status"] == "complete"


def test_without_answers_the_run_is_models_only(tmp_path):
    doc = SC.run(tmp_path)
    assert doc["status"] == "models_only" and len(doc["models"]) == 17
    assert all(entry["status"] == "absent" for entry in doc["instances"].values())
    assert doc["three_instances"]["status"] == "incomplete" and doc["three_instances"]["complete_instances"] == []


def test_an_accepted_answer_file_missing_an_id_is_a_file_error(tmp_path):
    write_instance(tmp_path, 1, SC.model_predictions(KEY)["d3_27b-q8_0"])
    path = tmp_path / "answers" / "inst1" / "slice1.jsonl"
    path.write_text("\n".join(path.read_text(encoding="utf-8").splitlines()[1:]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        SC.run(tmp_path)


def test_an_answer_for_an_id_outside_its_slice_is_a_file_error(tmp_path):
    path = tmp_path / "slice.jsonl"
    path.write_text('{"id": "H001", "answer": {"relation": "NONE"}}\n{"id": "H001", "answer": {"relation": "NONE"}}\n',
                    encoding="utf-8")
    with pytest.raises(ValueError):
        SC.read_answers(path, {"H002"})
    with pytest.raises(ValueError):
        SC.read_answers(path, {"H001"})  # given twice


# ---- the protocol: void versus schema error


def test_permission_denials_void_the_attempt_and_a_non_list_is_a_schema_error():
    ids = slice_ids(1)
    lines = [answer_line(BY_ID[i], "NONE") for i in ids]
    denied = json.dumps(result_doc(fenced(lines), permission_denials=[{"tool_name": "Bash"}])).encode()
    with pytest.raises(SC.Void, match="permission denial"):
        SC.answer_lines(denied, ids)
    with pytest.raises(SC.SchemaError):
        SC.answer_lines(json.dumps(result_doc(fenced(lines), permission_denials="none")).encode(), ids)
    doc = result_doc(fenced(lines))
    del doc["permission_denials"]  # absent: no denials
    assert len(SC.answer_lines(json.dumps(doc).encode(), ids)) == 50


def test_missing_schema_keys_and_undecodable_bytes_are_schema_errors_that_use_no_attempt(tmp_path):
    ids = slice_ids(1)
    preds = {i: "NONE" for i in ids}
    for key_name in ("is_error", "subtype", "num_turns", "result"):
        doc = result_doc(fenced([answer_line(BY_ID[i], "NONE") for i in ids]))
        del doc[key_name]
        put_raw(tmp_path, 1, 1, 1, json.dumps(doc).encode())
        with pytest.raises(SC.SchemaError, match=key_name):
            SC.extract(1, 1, 1, base=tmp_path)
    put_raw(tmp_path, 1, 1, 1, b"\xff\xfe\x00 not utf-8 \xc3(")
    with pytest.raises(SC.SchemaError):
        SC.extract(1, 1, 1, base=tmp_path)
    assert SC.read_ledger(tmp_path) == []  # nothing was recorded: the attempt is not used up
    put_raw(tmp_path, 1, 1, 1, valid_raw(preds, ids))  # the same attempt, now readable
    assert SC.extract(1, 1, 1, base=tmp_path)["verdict"] == "valid"


def test_a_utf8_byte_order_mark_is_read_and_a_bad_slice_is_void(tmp_path):
    ids = slice_ids(1)
    lines = [answer_line(BY_ID[i], "NONE") for i in ids]
    put_raw(tmp_path, 1, 1, 1, b"\xef\xbb\xbf" + json.dumps(result_doc(fenced(lines))).encode())
    assert SC.extract(1, 1, 1, base=tmp_path)["verdict"] == "valid"


def test_each_malformed_answer_block_is_void_not_a_crash():
    ids = slice_ids(1)
    good = [answer_line(BY_ID[i], "NONE") for i in ids]

    def verdict_of(doc: dict) -> str:
        try:
            SC.answer_lines(json.dumps(doc).encode(), ids)
        except SC.Void as exc:
            return str(exc)
        return "valid"

    assert verdict_of(result_doc(fenced(good))) == "valid"
    assert "error result" in verdict_of(result_doc(fenced(good), is_error=True))
    assert "num_turns" in verdict_of(result_doc(fenced(good), num_turns=2))
    assert "0 fenced" in verdict_of(result_doc("no block here"))
    assert "2 fenced" in verdict_of(result_doc(fenced(good) + "\n" + fenced(good)))
    assert "not a string" in verdict_of(result_doc(fenced([json.dumps({"id": 5, "answer": {"relation": "NONE"}})] + good[1:])))
    assert "missing" in verdict_of(result_doc(fenced(good[:-1])))  # an id of the slice is missing
    assert "extra" in verdict_of(result_doc(fenced(good + [json.dumps({"id": "H999", "answer": {"relation": "NONE"}})])))
    assert "repeated" in verdict_of(result_doc(fenced(good + [good[0]])))
    assert "not JSON" in verdict_of(result_doc(fenced(good[:-1] + ["not json"])))
    assert "{id, answer}" in verdict_of(result_doc(fenced(good[:-1] + [json.dumps({"id": ids[-1]})])))


# ---- the ledger: overwrite, second void, attempt rules, a changed raw file


def test_an_attempt_with_a_ledger_entry_is_never_overwritten(tmp_path):
    ids = slice_ids(1)
    preds = {i: "NONE" for i in ids}
    put_raw(tmp_path, 1, 1, 1, valid_raw(preds, ids))
    SC.extract(1, 1, 1, base=tmp_path)
    answers = (tmp_path / "answers" / "inst1" / "slice1.jsonl").read_bytes()
    with pytest.raises(SC.Refused, match="already"):
        SC.extract(1, 1, 1, base=tmp_path)
    assert (tmp_path / "answers" / "inst1" / "slice1.jsonl").read_bytes() == answers
    assert len(SC.read_ledger(tmp_path)) == 1


def test_a_second_void_ends_the_slice_and_no_third_attempt_is_allowed(tmp_path):
    put_raw(tmp_path, 1, 1, 1, json.dumps(result_doc("x", is_error=True)).encode())
    assert SC.extract(1, 1, 1, base=tmp_path)["verdict"] == "void"
    put_raw(tmp_path, 1, 1, 2, json.dumps(result_doc("x", is_error=True)).encode())
    assert SC.extract(1, 1, 2, base=tmp_path)["verdict"] == "void"
    put_raw(tmp_path, 1, 1, 3, json.dumps(result_doc("x")).encode())
    with pytest.raises(SC.Refused, match="attempt"):
        SC.extract(1, 1, 3, base=tmp_path)
    with pytest.raises(SC.Refused, match="already"):
        SC.extract(1, 1, 2, base=tmp_path)
    assert [e["verdict"] for e in SC.read_ledger(tmp_path)] == ["void", "void"]
    assert not (tmp_path / "answers" / "inst1" / "slice1.jsonl").exists()


def test_attempt_2_needs_a_void_attempt_1(tmp_path):
    ids = slice_ids(1)
    put_raw(tmp_path, 1, 1, 2, valid_raw({i: "NONE" for i in ids}, ids))
    with pytest.raises(SC.Refused, match="void attempt 1"):
        SC.extract(1, 1, 2, base=tmp_path)  # no attempt 1
    put_raw(tmp_path, 1, 1, 1, valid_raw({i: "NONE" for i in ids}, ids))
    SC.extract(1, 1, 1, base=tmp_path)
    with pytest.raises(SC.Refused, match="already accepted"):
        SC.extract(1, 1, 2, base=tmp_path)  # attempt 1 is valid: no re-run


def test_a_changed_raw_file_is_refused_everywhere(tmp_path):
    ids = slice_ids(1)
    path = put_raw(tmp_path, 1, 1, 1, valid_raw({i: "NONE" for i in ids}, ids))
    SC.extract(1, 1, 1, base=tmp_path)
    path.write_bytes(path.read_bytes() + b" ")  # the raw file is edited after it was checked
    with pytest.raises(SC.Refused, match="changed"):
        SC.verify_ledger(tmp_path, SC.read_ledger(tmp_path))
    with pytest.raises(SC.Refused, match="changed"):
        SC.extract(1, 1, 2, base=tmp_path)
    with pytest.raises(SC.Refused, match="changed"):
        SC.run(tmp_path)


def test_preflight_reports_the_shape_and_writes_nothing(tmp_path):
    ids = slice_ids(1)
    ok = tmp_path / "ok.json"
    ok.write_bytes(json.dumps(result_doc(fenced([answer_line(BY_ID[i], "NONE") for i in ids[:2]]))).encode())
    report = SC.preflight(ok)
    assert report["verdict"] == "valid" and report["answer_lines"] == 2 and report["fenced_blocks"] == 1
    assert report["num_turns"] == 1 and report["subtype"] == "success" and len(report["sha256"]) == 64
    bad = tmp_path / "bad.json"
    bad.write_bytes(json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 1}).encode())
    with pytest.raises(SC.SchemaError, match="result"):
        SC.preflight(bad)
    assert not (tmp_path / "raw").exists()


# ---- the capture runner (subprocess patched: the audit never calls claude)


def test_run_slice_runs_claude_from_the_folder_with_the_stdin_and_saves_stdout_exactly(tmp_path, monkeypatch):
    calls = {}
    stdout = b"\xef\xbb\xbf{\r\n\"result\": \"caf\xc3\xa9\"}\r\n"

    def fake_run(cmd, **kw):
        calls.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=b"warn")

    monkeypatch.setattr(RS.subprocess, "run", fake_run)
    monkeypatch.setattr(RS.shutil, "which", lambda name: "/bin/claude")
    outside = tmp_path / "work"
    outside.mkdir()
    out, code = RS.capture(1, 1, 1, outside, base=tmp_path)
    assert out == SC.raw_path(tmp_path, 1, 1, 1) and out.read_bytes() == stdout and code == 0
    assert out.with_suffix(".stderr").read_bytes() == b"warn"
    assert calls["cmd"] == ["/bin/claude", "-p", "--safe-mode", "--model", "claude-haiku-5-5", "--tools", "",
                            "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"]
    assert calls["input"] == SC.stdin_of(1) and calls["cwd"] == outside.resolve()
    assert calls["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert "shell" not in calls  # the list form, no shell
    with pytest.raises(SC.Refused, match="exists"):
        RS.capture(1, 1, 1, outside, base=tmp_path)  # never overwritten


def test_run_slice_refuses_a_folder_inside_the_repository_or_a_non_empty_one(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "empty").mkdir(parents=True)
    (repo / "full").mkdir()
    (repo / "full" / "x.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(SC, "ROOT", repo)
    with pytest.raises(SC.Refused, match="inside the repository"):
        RS.check_workdir(repo / "empty")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "y.txt").write_text("y", encoding="utf-8")
    with pytest.raises(SC.Refused, match="not empty"):
        RS.check_workdir(outside)


def test_run_slice_applies_the_ledger_rules_before_any_call(tmp_path, monkeypatch):
    def no_call(*args, **kw):
        raise AssertionError("claude must not be called when the attempt is refused")

    monkeypatch.setattr(RS.subprocess, "run", no_call)
    monkeypatch.setattr(RS.shutil, "which", lambda name: "/bin/claude")
    ids = slice_ids(1)
    put_raw(tmp_path, 1, 1, 1, valid_raw({i: "NONE" for i in ids}, ids))
    SC.extract(1, 1, 1, base=tmp_path)
    outside = tmp_path / "work"
    outside.mkdir()
    with pytest.raises(SC.Refused, match="already accepted"):
        RS.capture(1, 1, 2, outside, base=tmp_path)


# ---- the frozen files, CRLF and line splitting, missing raw files, and the grammar


def test_frozen_json_matches_the_files_and_lists_exactly_the_frozen_files():
    frozen = json.loads((AUDIT / "frozen.json").read_text(encoding="utf-8"))
    assert sorted(frozen["files"]) == sorted(SC.FROZEN_FILES)
    for name, sha in frozen["files"].items():
        assert SC.file_sha(AUDIT / name) == sha, f"{name} differs from frozen.json: run score.py freeze before the commit"
    SC.check_frozen()


def test_prereg_hash_lines_match_frozen_json():
    frozen = json.loads((AUDIT / "frozen.json").read_text(encoding="utf-8"))["files"]
    lines = re.findall(r"^- `([^`]+)`: `([0-9a-f]{64})`$", (AUDIT / "PREREG.md").read_text(encoding="utf-8"), re.M)
    assert dict(lines) == frozen


def test_freeze_records_the_current_hashes_and_check_frozen_refuses_a_change(tmp_path, monkeypatch):
    target = tmp_path / "frozen.json"
    monkeypatch.setattr(SC, "FROZEN", target)
    assert not target.exists()
    with pytest.raises(SC.Refused, match="missing"):
        SC.check_frozen()
    SC.freeze()
    SC.check_frozen()  # the files are as frozen
    doc = json.loads(target.read_text(encoding="utf-8"))
    doc["files"]["sample.jsonl"] = "0" * 64
    target.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SC.Refused, match="sample.jsonl"):
        SC.check_frozen()
    doc["files"] = {"sample.jsonl": "0" * 64}
    target.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SC.Refused, match="not the frozen files"):
        SC.check_frozen()


def test_a_frozen_file_that_differs_refuses_extract_run_and_the_capture_before_any_write_or_call(tmp_path, monkeypatch):
    ids = slice_ids(1)
    put_raw(tmp_path, 1, 1, 1, valid_raw({i: "NONE" for i in ids}, ids))
    bad = tmp_path / "frozen.json"
    doc = json.loads((AUDIT / "frozen.json").read_text(encoding="utf-8"))
    doc["files"]["key.jsonl"] = "f" * 64
    bad.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(SC, "FROZEN", bad)
    with pytest.raises(SC.Refused, match="key.jsonl"):
        SC.extract(1, 1, 1, base=tmp_path)
    with pytest.raises(SC.Refused, match="key.jsonl"):
        SC.run(tmp_path)
    assert SC.read_ledger(tmp_path) == [] and not (tmp_path / "answers").exists()

    def no_call(*args, **kw):
        raise AssertionError("claude must not be called when a frozen file differs")

    monkeypatch.setattr(RS.subprocess, "run", no_call)
    monkeypatch.setattr(RS.shutil, "which", lambda name: "/bin/claude")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SC.Refused, match="key.jsonl"):
        RS.capture(1, 1, 1, work, base=tmp_path)


def test_crlf_line_endings_in_the_result_text_are_the_same_newline():
    ids = slice_ids(1)
    lines = [answer_line(BY_ID[i], "NONE") for i in ids]
    text = fenced(lines).replace("\n", "\r\n")
    assert SC.answer_lines(json.dumps(result_doc(text)).encode(), ids) == lines


def test_u2028_inside_a_json_string_does_not_split_a_line():
    ids = slice_ids(1)
    lines = [answer_line(BY_ID[i], "NONE") for i in ids]
    lines[0] = json.dumps({"id": ids[0], "answer": {"relation": "NONE", "note": "a\u2028b\u2029c"}})
    assert SC.answer_lines(json.dumps(result_doc(fenced(lines))).encode(), ids) == lines


def test_a_missing_or_unreadable_raw_file_is_refused_with_no_ledger_entry_and_no_attempt_used(tmp_path, monkeypatch):
    ids = slice_ids(1)
    with pytest.raises(SC.Refused, match="no readable raw output"):
        SC.extract(1, 1, 1, base=tmp_path)  # missing
    assert SC.read_ledger(tmp_path) == []
    put_raw(tmp_path, 1, 1, 1, valid_raw({i: "NONE" for i in ids}, ids))
    real_read = Path.read_bytes

    def unreadable(self):
        if self == SC.raw_path(tmp_path, 1, 1, 1):
            raise PermissionError("denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    with pytest.raises(SC.Refused, match="denied"):
        SC.extract(1, 1, 1, base=tmp_path)
    monkeypatch.setattr(Path, "read_bytes", real_read)
    assert SC.read_ledger(tmp_path) == []
    assert SC.extract(1, 1, 1, base=tmp_path)["verdict"] == "valid"  # the attempt was never used


def test_the_schema_error_names_the_type_in_plain_words():
    doc = result_doc("x")
    doc["num_turns"] = "one"
    with pytest.raises(SC.SchemaError, match="an integer"):
        SC.parse_raw(json.dumps(doc).encode())
    doc = result_doc("x")
    doc["is_error"] = "no"
    with pytest.raises(SC.SchemaError, match="a boolean"):
        SC.parse_raw(json.dumps(doc).encode())


# ---- the pinned files and the wording


def test_results_json_and_representativeness_json_are_the_models_only_run(tmp_path):
    doc = SC.run(tmp_path)  # no answers: the models-only run
    assert SC.render(doc).encode("utf-8") == (AUDIT / "results.json").read_bytes()
    leaderboard = json.loads(SC.LEADERBOARD.read_text(encoding="utf-8"))
    assert RP.document(doc, leaderboard).encode("utf-8") == (AUDIT / "representativeness.json").read_bytes()


def test_the_instructions_do_not_name_the_gold_the_key_the_benchmark_or_the_repository_and_block_once():
    text = (AUDIT / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    for word in ("gold", "key", "benchmark", "repository", "mentat", "github", "fixture", "answer key"):
        assert word not in text.lower(), word
    assert text.count("```jsonl") == 1
    assert "exactly once, at the end" in text
    stdin = SC.stdin_of(1).decode("utf-8")
    assert stdin.startswith(text.rstrip("\n"))
    assert stdin.count('"id": "H') == 50

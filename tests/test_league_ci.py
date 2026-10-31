"""tools/eval/league_ci.py (MEASUREMENT ONLY): the vectorized MCC against decision_scoring.mcc (random, and constant prediction),
each draw's values against decision_scoring and scoring.q_n on the expanded items, the point values against decision_scoring
and score.json, the draws (fixed by the seed, the same for every run, so identical runs give gaps of exactly zero), the
refusals (a stamp that is not the fixture's, an unregistered fixture, a failed self-check, a group that spans leagues, missing
or unscorable inputs, B < 1), the default reference (never a run that reads gold or has no decisions), the NaN convention for
a family a draw lacks, a perfect run (gold_view-like) at [1, 1], runs without decisions, and the CLI's written outputs."""

import json
import math
import random
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from mentat_bench import corrections
from mentat_bench import decision_scoring as DS
from mentat_bench import scoring

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "eval"))
import league_ci as LC  # noqa: E402

LEAGUES = ("L1", "L2", "L3")
STAMP = {"name": "toy", "version": "1", "status": "active", "content_hash": "ab" * 32}
COMPONENTS = ("relations", "groups", "bindings")
# a few wrong answers: the imperfect run's decisions (the rest are the gold answers)
WRONG = {"r00": {"relation": "NONE"}, "r11": {"relation": "EXCLUDES"}, "b01": {"status": "bound"}, "g00": {"groups": []}}


def _fixture(root: Path, version: str = "1") -> Path:
    """Three leagues, each with both classes of every op (bindings, relations: held-out too, groups), and one query per family."""
    fx = root / "fixture"
    (fx / "gold").mkdir(parents=True)
    meta, cands, labels, design = [], [], [], []
    for i, lg in enumerate(LEAGUES):
        ids = [f"{lg}:{k}" for k in range(4)]
        meta += [{"id": x, "league": lg} for x in ids]
        for k in range(2):
            cands.append({"id": ids[k], "op": "decide_binding", "qid": f"b{i}{k}"})
            labels.append({"qid": f"b{i}{k}", "op": "decide_binding", "label": "bound" if k == 0 else "unresolved",
                           "k": None, "held_out": k == 1, "missing": None, "order": None, "stratum": "real"})
        for k, (rel, held) in enumerate([("IMPLIES", False), ("NONE", True), ("EXCLUDES", False), ("IMPLIES", True)]):
            other = LEAGUES[(i + 1) % len(LEAGUES)]  # b sits in the next league: the item is on side a
            cands.append({"a": ids[k], "b": f"{other}:0", "op": "decide_relation", "qid": f"r{i}{k}"})
            labels.append({"qid": f"r{i}{k}", "op": "decide_relation", "label": rel, "k": None, "held_out": held,
                           "missing": None, "order": None, "stratum": "real"})
        for k, kind in enumerate(["exactly_one", "none"]):
            cands.append({"members": [ids[1], ids[2]], "op": "decide_group", "qid": f"g{i}{k}"})
            labels.append({"qid": f"g{i}{k}", "op": "decide_group", "label": kind, "k": None, "held_out": False,
                           "missing": None, "order": None, "stratum": "real"})
        for family in scoring.FAMILIES:
            design.append({"qid": f"{family}-{i}", "family": family, "league": lg, "anchor": True})
    _write(fx / "candidates.jsonl", cands)
    _write(fx / "gold" / "suite_labels.jsonl", labels)
    _write(fx / "gold" / "meta.jsonl", meta)
    _write(fx / "gold" / "query_design.jsonl", design)
    (fx / "fixture.json").write_text(json.dumps({"name": "toy", "version": version}), encoding="utf-8")
    return fx


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def _gold(decision_label: dict, cand: dict) -> dict:
    """The gold answer of a candidate, as a decision row's payload."""
    if cand["op"] == "decide_binding":
        return {"status": decision_label["label"]}
    if cand["op"] == "decide_relation":
        return {"relation": decision_label["label"]}
    kind = decision_label["label"]
    return {"groups": [] if kind == "none" else [{"kind": kind, "members": cand["members"]}]}


def _write_run(root: Path, name: str, fx: Path, overrides: dict | None = None, utility: float = 1.0,
               stamp: str = STAMP["content_hash"], decisions: bool = True, adapter: str | None = None,
               utilities: dict | None = None) -> Path:
    """A run dir in the runcell layout: fixture_stamp.json, score.json, cell.json (when an adapter is given), and with decisions
    the decisions.jsonl of the build and its score_decisions.json (decision_scoring.score). overrides: qid -> the decision
    payload, else the gold answer. utilities: qid -> the query utility, else utility."""
    run = root / name
    (run / "submission.build").mkdir(parents=True)
    cands = [json.loads(line) for line in (fx / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    labels = {json.loads(line)["qid"]: json.loads(line) for line in
              (fx / "gold" / "suite_labels.jsonl").read_text(encoding="utf-8").splitlines()}
    overrides = overrides or {}
    (run / "fixture_stamp.json").write_text(json.dumps({"content_hash": stamp, "name": "toy", "status": "active",
                                                        "version": "1"}), encoding="utf-8")
    if adapter is not None:
        (run / "cell.json").write_text(json.dumps({"adapter": adapter}), encoding="utf-8")
    per_query = {row["qid"]: (utilities or {}).get(row["qid"], utility) for row in map(json.loads, (
        fx / "gold" / "query_design.jsonl").read_text(encoding="utf-8").splitlines())}
    fam = {f: [per_query[q] for q in per_query if q.startswith(f + "-")] for f in scoring.FAMILIES}
    q_n = scoring.q_n({f: sum(v) / len(v) for f, v in fam.items()})
    (run / "score.json").write_text(json.dumps({"Q_N": q_n, "per_query": per_query}), encoding="utf-8")
    if decisions:
        rows = []
        for cand in cands:
            qid = cand["qid"]
            payload = overrides.get(qid, _gold(labels[qid], cand))
            rows.append({"qid": qid, "op": cand["op"], "a": cand.get("a"), "b": cand.get("b"), "decision": payload,
                         "conf": 0.9, "probs": None, "error": None})
        _write(run / "submission.build" / "decisions.jsonl", rows)
        items, counts = DS.load(fx / "gold" / "suite_labels.jsonl", fx / "candidates.jsonl",
                                run / "submission.build" / "decisions.jsonl")
        (run / "score_decisions.json").write_text(json.dumps(DS.score(items, counts, DS.fixture_version(fx))), encoding="utf-8")
    return run


def _copy_run(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def test_vectorized_mcc_equals_decision_scoring_mcc_including_constant_predictions_and_truths():
    rng = random.Random(7)
    L, K, B = 3, 4, 6
    for _ in range(25):
        cube = np.array([[[rng.randint(1, 5) for _ in range(K)] for _ in range(K)] for _ in range(L)], dtype=np.int64)
        W = np.array([np.bincount([rng.randrange(L) for _ in range(L)], minlength=L) for _ in range(B)], dtype=np.int64)
        got = LC._mcc(cube, W)
        for b in range(B):
            pairs = [(str(t), str(p)) for lg in range(L) for t in range(K) for p in range(K)
                     for _ in range(int(cube[lg, t, p] * W[b, lg]))]
            assert got[b] == pytest.approx(DS.mcc(pairs), abs=1e-12)
    ones = np.ones((1, 1), dtype=np.int64)
    constant_truth = np.zeros((1, 2, 2), dtype=np.int64)  # one truth class: a constant marginal, 0 as decision_scoring
    constant_truth[0, 0, 0], constant_truth[0, 0, 1] = 1, 2
    assert LC._mcc(constant_truth, ones)[0] == 0.0 == DS.mcc([("a", "a"), ("a", "b"), ("a", "b")])
    constant_prediction = np.zeros((1, 2, 2), dtype=np.int64)  # every prediction the same class: the binding case of a real run
    constant_prediction[0, 0, 0], constant_prediction[0, 1, 0] = 3, 2
    assert LC._mcc(constant_prediction, ones)[0] == 0.0 == DS.mcc([("a", "x")] * 3 + [("b", "x")] * 2)


def test_each_draw_equals_decision_scoring_and_scoring_q_n_on_the_expanded_items(tmp_path):
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "imperfect", fx, overrides=WRONG)
    fixture = LC.read_fixture(fx)
    data = LC.read_run(run, fx, fixture)
    cubes = LC._decision_cubes(data["items"], LC._classes([data]), fixture)
    W = LC.draws(4, 3, len(fixture["leagues"]))
    values = LC._draw_values(data, cubes, W)
    per_query = json.loads((run / "score.json").read_text(encoding="utf-8"))["per_query"]
    for b in range(len(W)):
        # each draw holds W[b, league] copies of every item and query of that league
        items = [it for it in data["items"] for _ in range(int(W[b, fixture["item_league"][it["qid"]]]))]
        scored = DS.score(items, {}, 1)
        assert values["relations"][b] == pytest.approx(scored["headline"]["relation_mcc"], abs=1e-9)
        for name in COMPONENTS:
            assert values[name][b] == pytest.approx(scored["ops"][LC.COMPONENT_OP[name]]["mcc"], abs=1e-9)
        assert values["held_out_relations"][b] == pytest.approx(
            scored["ops"]["decide_relation"]["by_split"]["held_out"]["mcc"], abs=1e-9)
        fam = {f: [] for f in scoring.FAMILIES}
        for qid, utility in per_query.items():
            family, league = fixture["design"][qid]
            fam[scoring.FAMILIES[family]] += [utility] * int(W[b, league])
        expanded = scoring.q_n({f: sum(v) / len(v) for f, v in fam.items()})
        assert values["Q_N"][b] == pytest.approx(expanded, abs=1e-9)


def test_point_values_are_the_decision_scoring_headline_and_the_score_json_q_n(tmp_path):
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "imperfect", fx, overrides=WRONG)
    doc = LC.run_ci(fx, [run], STAMP, B=50)
    scored = json.loads((run / "score_decisions.json").read_text(encoding="utf-8"))
    metrics = doc["runs"]["imperfect"]["metrics"]
    assert 0 < metrics["relations"]["point"] < 1
    assert metrics["relations"]["point"] == pytest.approx(scored["headline"]["relation_mcc"], abs=1e-6)
    assert "headline" not in metrics and "mcc_mean_3" not in metrics  # no aggregate in the default layout
    for name in COMPONENTS:
        assert metrics[name]["point"] == pytest.approx(scored["ops"][LC.COMPONENT_OP[name]]["mcc"], abs=1e-6)
    assert metrics["held_out_relations"]["point"] == pytest.approx(
        scored["ops"]["decide_relation"]["by_split"]["held_out"]["mcc"], abs=1e-6)
    assert metrics["Q_N"]["point"] == pytest.approx(json.loads((run / "score.json").read_text())["Q_N"], abs=1e-6)
    for name, m in metrics.items():  # the point sits inside its interval
        assert m["lo"] <= m["point"] <= m["hi"], name


def test_the_gap_has_the_sign_of_the_gap_between_the_decision_scoring_headlines(tmp_path):
    fx = _fixture(tmp_path)
    better = _write_run(tmp_path, "better", fx, overrides=WRONG)
    worse = _write_run(tmp_path, "worse", fx, overrides={**WRONG, "r02": {"relation": "NONE"}, "r12": {"relation": "NONE"}})
    doc = LC.run_ci(fx, [better, worse], STAMP, reference="better", B=200)
    head = {r.name: json.loads((r / "score_decisions.json").read_text(encoding="utf-8"))["headline"]["relation_mcc"]
            for r in (better, worse)}
    gap = doc["gaps"]["worse"]["relations"]
    assert gap["point"] == pytest.approx(head["better"] - head["worse"], abs=1e-6) and gap["point"] > 0
    assert math.copysign(1, gap["point"]) == math.copysign(1, head["better"] - head["worse"])
    assert gap["lo"] <= gap["point"] <= gap["hi"]


def test_draws_repeat_for_a_seed_and_change_with_it(tmp_path):
    assert np.array_equal(LC.draws(50, 0, 3), LC.draws(50, 0, 3))
    assert not np.array_equal(LC.draws(50, 0, 3), LC.draws(50, 1, 3))
    assert (LC.draws(50, 0, 3).sum(axis=1) == 3).all()
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "imperfect", fx, overrides=WRONG)
    first = json.dumps(LC.run_ci(fx, [run], STAMP, B=200, seed=0), sort_keys=True)
    assert first == json.dumps(LC.run_ci(fx, [run], STAMP, B=200, seed=0), sort_keys=True)
    assert first != json.dumps(LC.run_ci(fx, [run], STAMP, B=200, seed=1), sort_keys=True)


def test_identical_runs_have_gaps_of_exactly_zero_because_the_draws_are_shared(tmp_path):
    fx = _fixture(tmp_path)
    a = _write_run(tmp_path, "a", fx, overrides=WRONG)
    b = _copy_run(a, tmp_path / "b")
    doc = LC.run_ci(fx, [a, b], STAMP, reference="a", B=200)
    gaps = doc["gaps"]["b"]
    assert set(gaps) == {*COMPONENTS, "held_out_relations", "Q_N"}
    assert all(g == {"point": 0.0, "lo": 0.0, "hi": 0.0} for g in gaps.values())


def test_a_run_from_another_fixture_is_refused(tmp_path):
    fx = _fixture(tmp_path)
    old = _write_run(tmp_path, "old", fx, stamp="cd" * 32)
    with pytest.raises(LC.CIError, match="not run on this fixture"):
        LC.run_ci(fx, [old], STAMP, B=10)


def test_the_cli_refuses_a_fixture_that_is_not_a_registered_version(tmp_path, capsys):
    fx = _fixture(tmp_path)
    for rel in corrections.REQUIRED:  # the real stamp_for path: the files are there, the fixture is not in the registry
        (fx / rel).parent.mkdir(parents=True, exist_ok=True)
        (fx / rel).write_text("", encoding="utf-8")
    run = _write_run(tmp_path, "perfect", fx)
    code = LC.main(["--fixture", str(fx), "--runs", str(run), "--out", str(tmp_path / "out.json")])
    assert code == 1 and "is not a registered fixture version" in capsys.readouterr().err
    assert not (tmp_path / "out.json").exists()


def test_the_cli_accepts_a_sealed_split_only_at_its_committed_hash(tmp_path, capsys):
    fx = _fixture(tmp_path)
    for rel in corrections.REQUIRED:
        (fx / rel).parent.mkdir(parents=True, exist_ok=True)
        (fx / rel).touch()
    sealed = corrections.content_hash(corrections.content_hashes(fx))
    run = _write_run(tmp_path, "perfect", fx, stamp=sealed)
    out = tmp_path / "out.json"
    assert LC.main(["--fixture", str(fx), "--runs", str(run), "--B", "20", "--sealed", "ab" * 32, "--out", str(out)]) == 1
    assert "not the sealed commitment" in capsys.readouterr().err and not out.exists()
    assert LC.main(["--fixture", str(fx), "--runs", str(run), "--B", "20", "--sealed", sealed, "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["stamp"] == {"name": fx.name, "sealed": True, "content_hash": sealed}


def test_the_cli_writes_the_json_and_the_markdown(tmp_path, monkeypatch, capsys):
    fx = _fixture(tmp_path)
    a = _write_run(tmp_path, "a", fx, overrides=WRONG)
    b = _write_run(tmp_path, "b", fx)
    monkeypatch.setattr(LC.corrections, "stamp_for", lambda fixture, *args, **kwargs: STAMP)  # the fixture is not registered
    out, md = tmp_path / "out.json", tmp_path / "out.md"
    code = LC.main(["--fixture", str(fx), "--runs", str(a), str(b), "--B", "50", "--out", str(out), "--md", str(md)])
    assert code == 0 and "reference b" in capsys.readouterr().out
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["q_n_kind"] == "repaired" and set(doc["runs"]) == {"a", "b"} and doc["reference"] == "b"
    table = md.read_text(encoding="utf-8")
    assert table.startswith("Point [95%") and "| Run |" in table and "Q_N (repaired base)" in table and "b (reference)" in table


def test_a_perfect_run_has_intervals_of_exactly_one_and_q_n_of_one_hundred(tmp_path):
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "perfect", fx)  # every answer and every query utility is the gold one
    doc = LC.run_ci(fx, [run], STAMP, B=500)
    metrics = doc["runs"]["perfect"]["metrics"]
    for name in ("relations", *COMPONENTS, "held_out_relations"):
        assert (metrics[name]["point"], metrics[name]["lo"], metrics[name]["hi"]) == (1.0, 1.0, 1.0)
    assert (metrics["Q_N"]["point"], metrics["Q_N"]["lo"], metrics["Q_N"]["hi"]) == (100.0, 100.0, 100.0)
    assert doc["reference"] == "perfect"


def test_the_default_reference_is_never_a_run_that_reads_gold_or_has_no_decisions(tmp_path):
    fx = _fixture(tmp_path)
    ceiling = _write_run(tmp_path, "gold_view", fx, adapter="gold_view")  # perfect, and it reads gold
    model = _write_run(tmp_path, "model", fx, overrides=WRONG, adapter="local")
    doc = LC.run_ci(fx, [ceiling, model], STAMP, B=50)
    assert doc["reference"] == "model" and doc["runs"]["gold_view"]["diagnostic"] is True
    assert doc["runs"]["model"]["diagnostic"] is False
    with pytest.raises(LC.CIError, match="give --reference"):
        LC.run_ci(fx, [ceiling], STAMP, B=50)
    assert LC.run_ci(fx, [ceiling, model], STAMP, reference="gold_view", B=50)["reference"] == "gold_view"


def test_a_run_without_decisions_gets_q_n_only_and_the_best_headline_is_the_reference(tmp_path):
    fx = _fixture(tmp_path)
    perfect = _write_run(tmp_path, "perfect", fx)
    empty = _write_run(tmp_path, "empty", fx, decisions=False, utility=0.5)
    doc = LC.run_ci(fx, [empty, perfect], STAMP, B=50)
    assert doc["reference"] == "perfect"
    assert doc["runs"]["empty"]["decisions"] is False and set(doc["runs"]["empty"]["metrics"]) == {"Q_N"}
    assert doc["runs"]["empty"]["diagnostic"] is True and set(doc["gaps"]["empty"]) == {"Q_N"}
    assert doc["gaps"]["empty"]["Q_N"]["point"] == pytest.approx(100.0 - 50.0, abs=1e-6)


def test_a_family_a_draw_lacks_is_nan_and_a_family_absent_from_the_workload_counts_as_zero():
    # two leagues; closure has a query only in league 0, partition has none; draw [0, 2] holds no closure query
    run = {"fam_sum": np.array([[1.0, 1.0, 1.0, 0.0], [0.0, 1.0, 1.0, 0.0]]),
           "fam_n": np.array([[1, 1, 1, 0], [0, 1, 1, 0]], dtype=np.int64)}
    q = LC._q_n(run, np.array([[1, 0], [0, 2]], dtype=np.int64))
    assert q[0] == pytest.approx(100.0 * (1 + 1 + 1 + 0) / 4)  # partition: absent from the workload, so 0 in every draw
    assert math.isnan(q[1])


def test_a_q_n_that_does_not_match_score_json_fails_the_self_check(tmp_path):
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "bad", fx, overrides=WRONG)
    score = json.loads((run / "score.json").read_text(encoding="utf-8"))
    score["Q_N"] += 1.0
    (run / "score.json").write_text(json.dumps(score), encoding="utf-8")
    with pytest.raises(LC.CIError, match="Q_N"):
        LC.run_ci(fx, [run], STAMP, B=10)


def test_items_take_the_league_of_their_first_outcome_and_a_group_must_stay_in_one_league(tmp_path):
    fx = _fixture(tmp_path)
    item_league = LC.read_fixture(fx)["item_league"]
    assert item_league["r00"] == 0 and item_league["g10"] == 1  # r00's b is in L2, its a in L1
    rows = [json.loads(line) for line in (fx / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    next(r for r in rows if r["qid"] == "g00")["members"][1] = "L2:2"  # the second member moves to another league
    _write(fx / "candidates.jsonl", rows)
    with pytest.raises(LC.CIError, match="spans leagues"):
        LC.read_fixture(fx)


def test_missing_or_unscorable_inputs_are_refused_with_a_ci_error(tmp_path):
    fx = _fixture(tmp_path / "a")
    run = _write_run(tmp_path / "a", "run", fx, overrides=WRONG)
    (run / "fixture_stamp.json").unlink()
    with pytest.raises(LC.CIError, match="fixture_stamp.json is missing"):
        LC.run_ci(fx, [run], STAMP, B=10)

    fx = _fixture(tmp_path / "b")
    run = _write_run(tmp_path / "b", "run", fx, overrides=WRONG)
    (run / "submission.build" / "decisions.jsonl").unlink()
    with pytest.raises(LC.CIError, match="cannot be scored"):
        LC.run_ci(fx, [run], STAMP, B=10)

    fx = _fixture(tmp_path / "c")
    run = _write_run(tmp_path / "c", "run", fx, overrides=WRONG)
    rows = [json.loads(line) for line in (fx / "gold" / "meta.jsonl").read_text(encoding="utf-8").splitlines()]
    _write(fx / "gold" / "meta.jsonl", [r for r in rows if r["id"] != "L1:2"])  # candidates name an id meta does not have
    with pytest.raises(LC.CIError, match="does not have"):
        LC.run_ci(fx, [run], STAMP, B=10)

    fx = _fixture(tmp_path / "d")
    run = _write_run(tmp_path / "d", "run", fx, overrides=WRONG)
    rows = [json.loads(line) for line in (run / "submission.build" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    rows.append({**rows[0], "decision": {"status": "unresolved"}})  # a second, different answer for one qid
    _write(run / "submission.build" / "decisions.jsonl", rows)
    with pytest.raises(LC.CIError, match="cannot be scored"):
        LC.run_ci(fx, [run], STAMP, B=10)

    fx = _fixture(tmp_path / "e")
    run = _write_run(tmp_path / "e", "run", fx)
    with pytest.raises(LC.CIError, match="B must be at least 1"):
        LC.run_ci(fx, [run], STAMP, B=0)


def test_each_draws_q_n_weights_every_query_by_its_league_multiplicity(tmp_path):
    fx = _fixture(tmp_path)
    utilities = {f"{family}-{i}": 0.1 + 0.3 * i + 0.05 * k for i in range(3) for k, family in enumerate(scoring.FAMILIES)}
    run = _write_run(tmp_path, "varied", fx, overrides=WRONG, utilities=utilities)
    fixture = LC.read_fixture(fx)
    W = LC.draws(6, 5, len(fixture["leagues"]))
    values = LC._q_n(LC.read_run(run, fx, fixture), W)
    for b in range(len(W)):  # each draw holds W[b, league] copies of every query of that league
        fam = {f: [] for f in scoring.FAMILIES}
        for qid, utility in utilities.items():
            family, league = fixture["design"][qid]
            fam[scoring.FAMILIES[family]] += [utility] * int(W[b, league])
        assert values[b] == pytest.approx(scoring.q_n({f: sum(v) / len(v) for f, v in fam.items()}), abs=1e-9)
    assert len({round(float(v), 9) for v in values}) > 1  # the draws differ, so ignoring W could not pass


def test_intervals_are_the_2_5_and_97_5_percentiles_of_the_non_nan_draws():
    assert LC._interval(np.arange(101, dtype=float)) == (pytest.approx(2.5), pytest.approx(97.5))
    assert LC._interval(np.array([np.nan, *np.arange(101, dtype=float)])) == (pytest.approx(2.5), pytest.approx(97.5))
    assert LC._interval(np.array([np.nan])) == (None, None)


def _rewrite_json(path: Path, mutate) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _rewrite_first_candidate(fx: Path, mutate) -> None:
    rows = [json.loads(line) for line in (fx / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    mutate(rows[0])
    _write(fx / "candidates.jsonl", rows)


MALFORMED = {
    "score.json without per_query": lambda fx, run: _rewrite_json(run / "score.json", lambda d: d.pop("per_query")),
    "score.json without Q_N": lambda fx, run: _rewrite_json(run / "score.json", lambda d: d.pop("Q_N")),
    "non-numeric utility": lambda fx, run: _rewrite_json(run / "score.json", lambda d: d["per_query"].update({"closure-0": "high"})),
    "candidate without qid": lambda fx, run: _rewrite_first_candidate(fx, lambda r: r.pop("qid")),
    "unknown op with no id": lambda fx, run: _rewrite_first_candidate(fx, lambda r: (r.update(op="decide_xyz"), r.pop("id"))),
    "score_decisions.json without ops": lambda fx, run: _rewrite_json(run / "score_decisions.json",
                                                                     lambda d: d.pop("ops")),
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
def test_malformed_inputs_raise_a_ci_error_not_a_traceback(case, tmp_path):
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "run", fx, overrides=WRONG)
    MALFORMED[case](fx, run)
    with pytest.raises(LC.CIError, match=r"\S"):
        LC.run_ci(fx, [run], STAMP, B=10)


def _fixture_v2(root: Path, version: str = "2") -> Path:
    """Like _fixture, with five outcomes per league: compound group labels (terms, with a decoy), and reading questions (one
    ambiguous row with two options, and a decoy with the same option for both readings)."""
    fx = root / "fixture"
    (fx / "gold").mkdir(parents=True)
    meta, cands, labels, design = [], [], [], []
    for i, lg in enumerate(LEAGUES):
        ids = [f"{lg}:{k}" for k in range(5)]
        meta += [{"id": x, "league": lg} for x in ids]
        for k in range(2):
            cands.append({"id": ids[k], "op": "decide_binding", "qid": f"b{i}{k}"})
            labels.append({"qid": f"b{i}{k}", "op": "decide_binding", "label": "bound" if k == 0 else "unresolved",
                           "k": None, "held_out": k == 1, "missing": None, "order": None, "stratum": "real"})
        for k, (rel, held) in enumerate([("IMPLIES", False), ("NONE", True), ("EXCLUDES", False), ("IMPLIES", True)]):
            other = LEAGUES[(i + 1) % len(LEAGUES)]
            cands.append({"a": ids[k], "b": f"{other}:0", "op": "decide_relation", "qid": f"r{i}{k}"})
            labels.append({"qid": f"r{i}{k}", "op": "decide_relation", "label": rel, "k": None, "held_out": held,
                           "missing": None, "order": None, "stratum": "real"})
        group_specs = [([0, 1, 2], [["at_least_k", 1], ["at_most_k", 2]], "at_least_k", 1),
                       ([0, 2, 3], [["at_least_k", 2], ["at_most_k", 2]], "at_least_k", 2),
                       ([1, 2], [], "none", None)]
        for k, (members, terms, label, kk) in enumerate(group_specs):
            cands.append({"members": [ids[m] for m in members], "op": "decide_group", "qid": f"g{i}{k}", "source": "curated"})
            labels.append({"qid": f"g{i}{k}", "op": "decide_group", "label": label, "k": kk, "terms": terms, "held_out": False,
                           "missing": None, "order": None, "stratum": "decoy" if not terms else "real"})
        reading_specs = [([4, 0, 1, 2, 3], {"base": ids[0], "alt": ids[2]}, "reading"),
                         ([3, 0, 1, 2, 4], {"base": ids[1], "alt": ids[1]}, "reading_decoy")]
        for k, (members, pick, stratum) in enumerate(reading_specs):
            # the last option is another league's outcome, as the generator's other-team distractors can be
            options = [ids[m] for m in members[:-1]] + [f"{LEAGUES[(i + 1) % len(LEAGUES)]}:4"]
            cands.append({"members": options, "op": "decide_group", "qid": f"rd{i}{k}", "source": "reading"})
            labels.append({"qid": f"rd{i}{k}", "op": "decide_group", "stratum": stratum, "readings": pick, "label": "reading",
                           "k": None, "held_out": False, "missing": None, "order": None})
        for family in scoring.FAMILIES:
            design.append({"qid": f"{family}-{i}", "family": family, "league": lg, "anchor": True})
    _write(fx / "candidates.jsonl", cands)
    _write(fx / "gold" / "suite_labels.jsonl", labels)
    _write(fx / "gold" / "meta.jsonl", meta)
    _write(fx / "gold" / "query_design.jsonl", design)
    (fx / "fixture.json").write_text(json.dumps({"name": "toy", "version": version}), encoding="utf-8")
    return fx


def _gold_v2(label: dict, cand: dict) -> dict:
    """The gold answer of a v2 candidate: one group decision per term, or a reading's scoped chain pairs (none emits nothing)."""
    if cand["op"] == "decide_binding":
        return {"status": label["label"]}
    if cand["op"] == "decide_relation":
        return {"relation": label["label"]}
    if "readings" in label:
        u, groups = cand["members"][0], []
        for reading in ("base", "alt"):
            pick = label["readings"][reading]
            if pick is None:
                continue
            scope = {"axis": "reading", "values": [reading], "of": ["alt", "base"]}
            groups += [{"kind": "ordered_chain", "members": pair, "direction": "forward", "scope": scope}
                       for pair in ([u, pick], [pick, u])]
        return {"groups": groups}
    groups = []
    for kind, k in label["terms"]:
        group = {"kind": kind, "members": cand["members"]}
        if k is not None:
            group["k"] = k
        groups.append(group)
    return {"groups": groups}


def _v2_payloads(fx: Path) -> dict:
    cands = [json.loads(line) for line in (fx / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    labels = {json.loads(line)["qid"]: json.loads(line) for line in
              (fx / "gold" / "suite_labels.jsonl").read_text(encoding="utf-8").splitlines()}
    return {cand["qid"]: _gold_v2(labels[cand["qid"]], cand) for cand in cands}


def test_a_v2_run_is_scored_per_component_and_its_point_values_are_the_decision_scoring_ones(tmp_path):
    fx = _fixture_v2(tmp_path)
    payloads = _v2_payloads(fx)
    # the compound group read as at_least_k 2 alone (truth at_least_k:1+at_most_k:2): wrong
    payloads["g00"] = {"groups": [{"kind": "at_least_k", "k": 2, "members": ["L1:0", "L1:1", "L1:2"]}]}
    # the decoy answered with an unscoped pair to another option: it answers both readings, so both are wrong
    payloads["rd01"] = {"groups": [{"kind": "ordered_chain", "members": ["L1:3", "L1:0"], "direction": "forward"},
                                   {"kind": "ordered_chain", "members": ["L1:0", "L1:3"], "direction": "forward"}]}
    run = _write_run(tmp_path, "v2", fx, overrides=payloads)
    doc = LC.run_ci(fx, [run], STAMP, B=50)  # the self-check raises when a point value is not decision_scoring's
    metrics = doc["runs"]["v2"]["metrics"]
    assert doc["layout"] == "relation-headline/1" and "relation_group_mcc" not in metrics
    scored = json.loads((run / "score_decisions.json").read_text(encoding="utf-8"))
    assert {"readings", "relations", "groups", "bindings"} <= set(metrics)
    assert "headline" not in metrics and "mcc_mean_3" not in metrics
    assert metrics["relations"]["point"] == pytest.approx(scored["ops"]["decide_relation"]["mcc"], abs=1e-6)
    assert metrics["readings"]["point"] == pytest.approx(scored["ops"]["decide_reading"]["mcc"], abs=1e-6)
    assert metrics["groups"]["point"] == pytest.approx(scored["ops"]["decide_group"]["mcc"], abs=1e-6)
    assert doc["roles"]["readings"] == "diagnostic" and doc["roles"]["relations"] == "primary"
    assert metrics["readings"]["point"] < 1 and metrics["groups"]["point"] < 1
    assert doc["runs"]["v2"]["items"]["readings"] == 12  # two reading questions, two items each, per league
    items, _ = DS.load(fx / "gold" / "suite_labels.jsonl", fx / "candidates.jsonl", run / "submission.build" / "decisions.jsonl")
    classes = LC._classes([{"items": items}])
    assert "at_least_k:1+at_most_k:2" in classes["decide_group"] and "at_least_k:2" in classes["decide_group"]
    assert {"opt1", "opt2", "opt3"} <= set(classes["decide_reading"])
    text = LC.markdown(doc)
    assert "Relation MCC (headline)" in text.splitlines()[2] and "Binding MCC (secondary)" in text.splitlines()[2]
    assert "## Diagnostics (not scores" in text and "| Run | Group MCC | Reading MCC |" in text and "3-op" not in text


def _old_layout(path: Path) -> None:
    """Rewrites a score_decisions.json in the pre-2026-10-29 layout (headline mcc_mean, components, ops_counted, mcc_mean_3),
    with the values the old scorer gave: the same per-op MCCs, and the means of the components present."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    comp = {COMP: doc["ops"][op]["mcc"] for op, COMP in DS.COMPONENT.items() if op in doc["ops"]}
    counted = [c for c in ("relations", "groups", "bindings", "readings") if c in comp]
    mean = lambda names: sum(comp[n] for n in names) / len(names)  # noqa: E731
    doc["headline"] = {"mcc_mean": mean(counted), "components": comp, "ops_counted": counted,
                       "mcc_mean_3": mean([c for c in counted if c != "readings"])}
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_the_old_layout_is_still_read_and_legacy_means_reproduce_its_aggregates(tmp_path):
    fx = _fixture_v2(tmp_path)
    payloads = _v2_payloads(fx)
    payloads["g00"] = {"groups": [{"kind": "at_least_k", "k": 2, "members": ["L1:0", "L1:1", "L1:2"]}]}
    run = _write_run(tmp_path, "old", fx, overrides=payloads)
    _old_layout(run / "score_decisions.json")
    doc = LC.run_ci(fx, [run], STAMP, B=50)  # reads ops.<op>.mcc: both layouts give the same per-op metrics
    metrics = doc["runs"]["old"]["metrics"]
    assert metrics["relations"]["point"] == pytest.approx(json.loads((run / "score_decisions.json").read_text())["ops"][
        "decide_relation"]["mcc"], abs=1e-6)
    legacy = LC.run_ci(fx, [run], STAMP, B=50, legacy=True)
    old = json.loads((run / "score_decisions.json").read_text(encoding="utf-8"))["headline"]
    assert legacy["runs"]["old"]["metrics"]["headline"]["point"] == pytest.approx(old["mcc_mean"], abs=1e-6)
    assert legacy["runs"]["old"]["metrics"]["mcc_mean_3"]["point"] == pytest.approx(old["mcc_mean_3"], abs=1e-6)
    assert legacy["roles"]["headline"] == "legacy" and legacy["legacy_means"] is True
    assert "Legacy aggregates" in LC.markdown(legacy)


def test_a_new_layout_file_cannot_give_legacy_means(tmp_path):
    fx = _fixture_v2(tmp_path)
    run = _write_run(tmp_path, "new", fx, overrides=_v2_payloads(fx))
    with pytest.raises(LC.CIError, match="lacks"):
        LC.run_ci(fx, [run], STAMP, B=10, legacy=True)


def test_a_v1_run_has_no_reading_or_3_op_metrics(tmp_path):
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "v1", fx, overrides=WRONG)
    doc = LC.run_ci(fx, [run], STAMP, B=50)
    metrics = doc["runs"]["v1"]["metrics"]
    assert "readings" not in metrics and "headline" not in metrics and "mcc_mean_3" not in metrics
    assert "readings" not in doc["runs"]["v1"]["items"]
    assert "Legacy aggregates" not in LC.markdown(doc)


def test_a_perturbed_per_op_mcc_in_the_stored_file_fails_the_self_check(tmp_path):
    """The self-check reads ops.<op>.mcc from the stored score file: a value that the items do not reproduce is a CIError."""
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "perturbed", fx, overrides=WRONG)
    _rewrite_json(run / "score_decisions.json", lambda d: d["ops"]["decide_relation"].update(mcc=d["ops"]["decide_relation"]["mcc"] + 0.1))
    with pytest.raises(LC.CIError, match="relations is"):
        LC.run_ci(fx, [run], STAMP, B=10)


def test_a_v3_run_has_the_relation_group_headline_whose_point_is_decision_scorings(tmp_path):
    fx = _fixture(tmp_path, version="3")
    run = _write_run(tmp_path, "v3", fx, overrides=WRONG)
    doc = LC.run_ci(fx, [run], STAMP, B=50)  # the self-check raises when a point value is not decision_scoring's
    metrics = doc["runs"]["v3"]["metrics"]
    scored = json.loads((run / "score_decisions.json").read_text(encoding="utf-8"))
    assert doc["layout"] == "relation-group-headline/1" and doc["legacy_means"] is False
    assert doc["roles"]["relation_group_mcc"] == "primary" and doc["roles"]["relations"] == "part"
    assert doc["roles"]["groups"] == "part" and doc["roles"]["bindings"] == "secondary" and doc["roles"]["readings"] == "diagnostic"
    assert "headline" not in metrics and "mcc_mean_3" not in metrics
    head = metrics["relation_group_mcc"]
    assert head["point"] == pytest.approx(scored["headline"]["relation_group_mcc"], abs=1e-6)
    assert head["point"] == pytest.approx((metrics["relations"]["point"] + metrics["groups"]["point"]) / 2, abs=1e-6)
    assert head["lo"] is not None and head["hi"] is not None
    text = LC.markdown(doc)
    assert "Relation and group MCC (headline)" in text.splitlines()[2] and "Group MCC (part)" in text.splitlines()[2]
    assert "| Run | Reading MCC | Held-out relation MCC | Q_N (repaired base) | Q_N gap (reference minus run) |" in text


def test_a_v3_interval_is_deterministic_per_seed_and_its_gaps_are_paired_on_the_headline(tmp_path):
    fx = _fixture(tmp_path, version="3")
    good = _write_run(tmp_path, "good", fx)
    bad = _write_run(tmp_path, "bad", fx, overrides=WRONG)
    first = LC.run_ci(fx, [good, bad], STAMP, B=200, seed=7)
    assert LC.run_ci(fx, [good, bad], STAMP, B=200, seed=7) == first
    assert LC.run_ci(fx, [good, bad], STAMP, B=200, seed=8)["runs"]["bad"]["metrics"]["relation_group_mcc"]["point"] ==         first["runs"]["bad"]["metrics"]["relation_group_mcc"]["point"]  # the point does not depend on the seed
    assert first["reference"] == "good"
    gap = first["gaps"]["bad"]["relation_group_mcc"]
    assert gap["point"] == pytest.approx(first["runs"]["good"]["metrics"]["relation_group_mcc"]["point"]
                                         - first["runs"]["bad"]["metrics"]["relation_group_mcc"]["point"], abs=1e-6)
    assert gap["point"] > 0 and gap["lo"] is not None and gap["hi"] is not None


def test_legacy_means_are_refused_for_version_3(tmp_path):
    fx = _fixture(tmp_path, version="3")
    run = _write_run(tmp_path, "v3", fx)
    with pytest.raises(LC.CIError, match="version 1 and 2"):
        LC.run_ci(fx, [run], STAMP, B=10, legacy=True)


def test_a_fixture_json_without_a_version_gets_the_legacy_layout_and_other_versions_are_refused(tmp_path):
    fx = _fixture(tmp_path)
    run = _write_run(tmp_path, "run", fx, overrides=WRONG)
    _rewrite_json(fx / "fixture.json", lambda d: d.pop("version"))
    doc = LC.run_ci(fx, [run], STAMP, B=50)  # no version: the relation-headline layout, as versions 1 and 2
    assert doc["layout"] == "relation-headline/1" and "relation_group_mcc" not in doc["runs"]["run"]["metrics"]
    _rewrite_json(fx / "fixture.json", lambda d: d.update(version="v3"))
    with pytest.raises(LC.CIError, match="fixture version"):
        LC.run_ci(fx, [run], STAMP, B=10)
    (fx / "fixture.json").unlink()
    with pytest.raises(LC.CIError, match="fixture version"):
        LC.run_ci(fx, [run], STAMP, B=10)


def test_the_layout_follows_the_major_version_so_3_1_is_version_3_and_2_9_is_version_2(tmp_path):
    for version, layout in (("3.1", "relation-group-headline/1"), ("2.9", "relation-headline/1")):
        fx = _fixture(tmp_path / version, version=version)
        run = _write_run(tmp_path / version, "run", fx, overrides=WRONG)
        assert LC.run_ci(fx, [run], STAMP, B=20)["layout"] == layout

def test_a_group_whose_members_span_leagues_is_refused_in_version_3_too(tmp_path):
    fx = _fixture(tmp_path, version="3")
    rows = [json.loads(line) for line in (fx / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    next(r for r in rows if r["qid"] == "g00")["members"][1] = "L2:2"  # the second member moves to another league
    _write(fx / "candidates.jsonl", rows)
    with pytest.raises(LC.CIError, match="spans leagues"):
        LC.read_fixture(fx)


def _drop_group_rows(fx):
    """Removes the group candidates and labels: a version 3 run with no group items."""
    for name in ("candidates.jsonl", "gold/suite_labels.jsonl"):
        path = fx / name
        _write(path, [r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if r.get("op") != "decide_group"])


def test_the_version_3_interval_is_the_percentiles_of_the_per_draw_mean_of_the_two_mccs(tmp_path):
    fx = _fixture(tmp_path, version="3")
    run = _write_run(tmp_path, "v3", fx, overrides=WRONG)
    B, seed = 300, 4
    doc = LC.run_ci(fx, [run], STAMP, B=B, seed=seed)
    fixture = LC.read_fixture(fx)
    data = LC.read_run(run, fx, fixture)
    cubes = LC._decision_cubes(data["items"], LC._classes([data]), fixture)
    W = LC.draws(B, seed, len(fixture["leagues"]))
    per_draw = (LC._mcc(cubes["decide_relation"], W) + LC._mcc(cubes["decide_group"], W)) / 2  # NaN draws leave the interval
    lo, hi = np.percentile(per_draw[~np.isnan(per_draw)], [2.5, 97.5])
    metrics = doc["runs"]["v3"]["metrics"]
    assert metrics["relation_group_mcc"]["lo"] == pytest.approx(lo, abs=1e-6)
    assert metrics["relation_group_mcc"]["hi"] == pytest.approx(hi, abs=1e-6)
    assert metrics["relation_group_mcc"]["point"] == pytest.approx(
        (metrics["relations"]["point"] + metrics["groups"]["point"]) / 2, abs=1e-6)


def test_a_version_3_run_without_group_items_has_a_null_headline_and_interval(tmp_path):
    fx = _fixture(tmp_path, version="3")
    _drop_group_rows(fx)
    run = _write_run(tmp_path, "nogroups", fx)
    doc = LC.run_ci(fx, [run], STAMP, B=50, reference="nogroups")
    metrics = doc["runs"]["nogroups"]["metrics"]
    assert metrics["relation_group_mcc"] == {"point": None, "lo": None, "hi": None}
    assert metrics["relations"]["point"] is not None and metrics["groups"]["point"] is None
    assert "n/a" in LC.markdown(doc).splitlines()[4]  # the headline cell renders as n/a, not as a number

"""Native open-discovery adjudication tooling (mentat-bench-v0.1.md sections 4, 7, 8; python/mentat_bench/adjudication.py)."""

from __future__ import annotations

import csv
import itertools
import json
import random
from pathlib import Path

import pytest

from mentat_bench import adjudication as adj

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"


# --------------------------------------------------------------------------------------------- synthetic submission


def _var(i: str) -> dict:
    return {"op": "var", "id": i}


def _not(e: dict) -> dict:
    return {"op": "not", "a": e}


def _write_submission(root: Path) -> tuple[Path, Path]:
    """Four binary/ternary markets with one binding per kind, plus one constraint per exclusion rule."""
    sub = root / "sub"
    sub.mkdir()
    o = lambda m, i: f"polymarket:{m}:{i}"  # noqa: E731
    bindings = [
        {"id": o(1, 1), "status": "bound", "class": "A1", "negated": False, "missing": [], "evidence": [
            "src:1:question", "src:1:outcome_label:1", "src:event:9:event_title"]},  # identity: excluded
        {"id": o(1, 2), "status": "bound", "class": "A1", "negated": True, "missing": [], "evidence": [
            "src:1:question", "src:1:outcome_label:2", "src:event:9:event_title"]},
        {"id": o(2, 1), "status": "bound", "class": "A2", "negated": False, "missing": [], "evidence": [
            "src:2:question", "src:2:outcome_label:1"]},  # identity: excluded
        {"id": o(2, 2), "status": "bound", "class": "A2", "negated": True, "missing": [], "evidence": [
            "src:2:question", "src:2:outcome_label:2"]},
        {"id": o(3, 1), "status": "bound", "class": "R3", "negated": False, "missing": [], "evidence": [
            "src:3:question", "src:3:outcome_label:1"]},
        {"id": o(4, 1), "status": "unresolved", "class": None, "negated": False, "missing": ["no_source"], "evidence": [
            "src:4:question", "src:4:outcome_label:1"]},
    ]
    aliases = {"A1": _var(o(1, 1)), "A2": _var(o(2, 1)), "R3": {"op": "ref", "name": "A1"}}
    constraints = [
        {"id": "c1", "expr": {"op": "implies", "a": _var(o(1, 1)), "b": _var(o(2, 1))},
         "interps": None, "provenance": ["src:1:question", "src:2:question"]},
        {"id": "c2", "expr": {"op": "implies", "a": _var(o(1, 1)), "b": _var(o(2, 1))},
         "interps": None, "provenance": ["src:1:question", "src:2:question"]},  # duplicate of c1
        {"id": "c3", "expr": {"op": "or", "args": [_var(o(2, 1)), _not(_var(o(2, 1)))]},
         "interps": None, "provenance": ["src:2:question"]},  # tautology
        {"id": "c4", "expr": {"op": "iff", "a": _var(o(1, 1)), "b": _not(_var(o(1, 2)))},
         "interps": None, "provenance": ["ax:binary_complement", "src:1:question"]},  # axiom restatement
        {"id": "c5", "expr": {"op": "iff", "a": _var(o(2, 1)), "b": _not(_var(o(2, 2)))},
         "interps": None, "provenance": ["src:2:question"]},  # definition: complementary pair of one market
        {"id": "c6", "expr": {"op": "exactly", "k": 1, "args": [_var(o(1, 1)), _var(o(2, 1)), _var(o(3, 1))]},
         "interps": None, "provenance": ["ax:disjoint_integer_ranges", "src:1:question", "src:2:question",
                                         "src:3:question"]},  # group, cross-market
        {"id": "c7", "expr": {"op": "implies", "a": _var(o(2, 1)), "b": _var(o(1, 1))},
         "interps": None, "provenance": ["src:1:question", "src:2:question"]},  # reverse of c1: merges to iff
        {"id": "c8", "expr": {"op": "iff", "a": {"op": "ref", "name": "R3"}, "b": _var(o(4, 1))},
         "interps": None, "provenance": ["src:3:question", "src:4:question"]},  # alias expansion
        {"id": "c9", "expr": {"op": "atmost", "k": 1, "args": [_var(o(1, 1)), _var(o(4, 1))]},
         "interps": None, "provenance": ["src:1:question", "src:4:question"]},  # pairwise AMO
    ]
    provenance = [{"ref": "ax:binary_complement", "kind": "axiom", "record": "ax:binary_complement", "field": "rule"}]
    (sub / "bindings.jsonl").write_text("\n".join(json.dumps(b) for b in bindings) + "\n", encoding="utf-8")
    (sub / "theory.json").write_text(json.dumps({"interpretations": ["base"], "aliases": aliases,
                                                 "constraints": constraints}), encoding="utf-8")
    (sub / "provenance.jsonl").write_text("\n".join(json.dumps(p) for p in provenance) + "\n", encoding="utf-8")
    corpus = root / "corpus.jsonl"
    records = []
    for m, (question, n) in {1: ("Will Alpha win?", 2), 2: ("Will Beta win?", 2), 3: ("Will Gamma win?", 3),
                             4: ("Will Delta win?", 1)}.items():
        for i in range(1, n + 1):
            records.append({"id": o(m, i), "label": "Yes" if i == 1 else ("No" if i == 2 else "Other"),
                            "text": question, "event_title": "Race of 2026", "controlled": True})
    corpus.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return sub, corpus


def _synthetic_frame(n_rows: int, seed: int) -> list[dict]:
    """Frame-shaped rows: kind x tercile strata; clusters of five rows that share one tercile."""
    kinds = ("binding", "pairwise", "group")
    rows = []
    for i in range(n_rows):
        rows.append({"assertion_id": f"{seed:04d}-{i:06d}", "stratum": kinds[i % 3], "kind": kinds[i % 3],
                     "cond": False, "interps": None, "tercile": f"T{(i // 5) % 3 + 1}", "cluster_id": f"c{i // 5:05d}"})
    return rows


def _attach(sample: list[dict], population: list[dict]) -> list[dict]:
    """Copy the truth verdicts of the frame onto the sampled rows (the sampler emits no verdicts)."""
    verdicts = {r["assertion_id"]: r["verdict"] for r in population}
    return [dict(s, verdict=verdicts[s["assertion_id"]]) for s in sample]


def _judged_population(frame: list[dict], seed: int) -> list[dict]:
    """Attach truth verdicts with kind-specific precision (about 0.9 overall)."""
    rng = random.Random(seed)
    p_correct = {"binding": 0.97, "pairwise": 0.92, "group": 0.85}
    out = []
    for row in frame:
        u = rng.random()
        verdict = "correct" if u < p_correct[row["stratum"]] else "incorrect"
        out.append(dict(row, verdict=verdict))
    return out


# --------------------------------------------------------------------------------------------- canonical form


def _canon_key(expr: dict, aliases: dict | None = None) -> str:
    canon = adj._Canon(aliases or {})
    clauses = adj._cnf(canon.nnf(expr))
    return adj._key(adj._lits(clauses))


def test_canonical_equivalent_shapes_share_one_key():
    a, b = _var("polymarket:1:1"), _var("polymarket:2:1")
    implies = {"op": "implies", "a": a, "b": b}
    as_or = {"op": "or", "args": [_not(a), b]}
    contrapositive = {"op": "implies", "a": _not(b), "b": _not(a)}
    assert _canon_key(implies) == _canon_key(as_or) == _canon_key(contrapositive)
    amo2 = {"op": "atmost", "k": 1, "args": [a, b]}
    assert _canon_key(amo2) == _canon_key({"op": "or", "args": [_not(a), _not(b)]})
    xor = {"op": "exactly", "k": 1, "args": [a, b]}
    assert _canon_key(xor) == _canon_key({"op": "iff", "a": a, "b": _not(b)})
    assert _canon_key(implies) != _canon_key({"op": "implies", "a": b, "b": a})


def test_tautologies_and_contradictions_are_detected():
    a = _var("polymarket:1:1")
    assert adj._cnf(adj._Canon({}).nnf({"op": "or", "args": [a, _not(a)]})) == []
    assert adj._cnf(adj._Canon({}).nnf({"op": "implies", "a": a, "b": a})) == []
    assert adj._cnf(adj._Canon({}).nnf({"op": "false"})) == [frozenset()]


def test_wide_cardinality_stays_a_group_atom():
    args = [_var(f"polymarket:{i}:1") for i in range(4)]
    clauses = adj._lits(adj._cnf(adj._Canon({}).nnf({"op": "atmost", "k": 2, "args": args})))
    assert len(clauses) == 1 and clauses[0][0].startswith("+atmost:2:")
    assert adj._stratum(clauses) == "group"


# --------------------------------------------------------------------------------------------- registry


def test_registry_exclusions_and_merges(tmp_path):
    sub, corpus = _write_submission(tmp_path)
    rows, summary = adj.build_frame(sub, corpus)
    counts = summary["counts"]
    assert counts["excluded_identity_binding"] == 2  # outcome bound to its own class representative
    assert counts["excluded_axiom_restatement"] == 1  # single-market constraint citing a supplied axiom
    assert counts["excluded_definition"] == 3  # c5, plus the binary complements p:1:2 and p:2:2
    assert counts["excluded_tautology"] == 1
    assert counts["duplicates_merged"] == 1  # c2 repeats c1
    assert counts["merged_implication_pairs"] == 1  # c1 and c7 form one equivalence
    assert counts["bindings_bound"] == 5
    assert counts["rows_binding"] == 1  # only the cross-market binding of p:3:1 to p:1:1 survives
    assert counts["rows"] == len(rows) == 5
    assert counts["rows_group"] == 1
    ids = [r["assertion_id"] for r in rows]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    for r in rows:
        assert r["assertion_id"] == adj._sha(r["canonical"])
        assert r["sources"], "every row carries source texts"
        assert all(s["ref"].startswith("src:") for s in r["sources"]), "axiom refs never reach packets"
        assert r["claim"]


def test_registry_rows_carry_provenance_and_clusters(tmp_path):
    sub, corpus = _write_submission(tmp_path)
    rows, _ = adj.build_frame(sub, corpus)
    iff_rows = [r for r in rows if "if and only if" in r["claim"] or "exactly one" in r["claim"]]
    assert iff_rows, "the merged equivalence renders as one sentence"
    assert all(r["cluster_size"] >= 1 for r in rows)
    texts = " ".join(s["text"] or "" for r in rows for s in r["sources"])
    assert "Will Alpha win?" in texts and "Will Gamma win?" in texts


# --------------------------------------------------------------------------------------------- sampling


def test_sampling_is_seeded_and_reproducible():
    frame = _synthetic_frame(600, seed=1)
    rows_a, info_a = adj.draw_sample(frame, "units", 60, seed=11)
    rows_b, info_b = adj.draw_sample(frame, "units", 60, seed=11)
    rows_c, _ = adj.draw_sample(frame, "units", 60, seed=12)
    assert json.dumps(rows_a, sort_keys=True) == json.dumps(rows_b, sort_keys=True)
    assert json.dumps(info_a, sort_keys=True) == json.dumps(info_b, sort_keys=True)
    assert {r["assertion_id"] for r in rows_a} != {r["assertion_id"] for r in rows_c}


def test_inclusion_probabilities_are_consistent_with_frame_sizes():
    frame = _synthetic_frame(600, seed=2)
    rows, info = adj.draw_sample(frame, "units", 60, seed=3)
    total_n = 0
    for label, st in info["strata"].items():
        in_stratum = [r for r in rows if r["stratum"] == label]
        assert len(in_stratum) == st["n"]
        assert st["n"] / st["N"] == pytest.approx(in_stratum[0]["pi"], rel=0, abs=1e-12)
        # Horvitz-Thompson consistency: the weights of a stratum sum to its frame size
        assert sum(1.0 / r["pi"] for r in in_stratum) == pytest.approx(st["N"], rel=1e-9)
        total_n += st["n"]
    assert total_n == len(rows) == 60
    assert sum(st["N"] for st in info["strata"].values()) == len(frame)


def test_two_stage_cluster_sampling_records_both_stages():
    frame = _synthetic_frame(600, seed=4)
    rows, info = adj.draw_sample(frame, "clusters", 8, seed=5, within=3)
    assert info["psu"] == "cluster"
    clusters = {}
    for r in rows:
        clusters.setdefault(r["cluster_id"], []).append(r)
    assert len(clusters) == sum(st["m"] for st in info["strata"].values())
    for members in clusters.values():
        assert len(members) == 3  # within-cluster SRS of 3 rows out of 5
        assert members[0]["pi_second"] == pytest.approx(3 / 5)
        assert members[0]["pi"] == pytest.approx(members[0]["pi_first"] * members[0]["pi_second"])


# --------------------------------------------------------------------------------------------- intervals


def test_clopper_pearson_and_wilson_reference_values():
    lo, hi = adj.clopper_pearson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.3084971, abs=1e-6)
    assert adj.clopper_pearson_upper(0, 10) == pytest.approx(1 - 0.05 ** 0.1, abs=1e-9)
    lo, hi = adj.clopper_pearson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.025 ** 0.1, abs=1e-6)
    assert adj.clopper_pearson_upper(0, 300) == pytest.approx(3 / 300, rel=0.01)  # rule of three
    assert adj.wilson(0.5, 100, 1.959964) == pytest.approx((0.40383, 0.59617), abs=1e-4)


def test_pure_python_incomplete_beta_matches_scipy(monkeypatch):
    pytest.importorskip("scipy")
    reference = adj.clopper_pearson(7, 40, 0.05)
    monkeypatch.setattr(adj, "_scipy_betainc", None)
    monkeypatch.setattr(adj, "_scipy_betaincinv", None)
    pure = adj.clopper_pearson(7, 40, 0.05)
    assert pure[0] == pytest.approx(reference[0], abs=1e-9)
    assert pure[1] == pytest.approx(reference[1], abs=1e-9)


def test_cohen_kappa_reference_values():
    result = adj.cohen_kappa([("correct", "correct"), ("correct", "incorrect"),
                              ("incorrect", "incorrect"), ("incorrect", "incorrect")])
    assert result["raw_agreement"] == pytest.approx(0.75)
    assert result["kappa"] == pytest.approx(0.5)


# --------------------------------------------------------------------------------------------- estimator


def _simulate(reps: int) -> tuple[float, float, float]:
    """Mean estimate error and 95% linearised coverage over `reps` seeded samples of a synthetic registry."""
    frame = _synthetic_frame(1200, seed=9)
    population = _judged_population(frame, seed=10)
    truth = sum(r["verdict"] == "correct" for r in population) / len(population)
    estimates, covered = [], 0
    for rep in range(reps):
        sample, info = adj.draw_sample(population, "units", 120, seed=rep)
        result = adj.estimate_domain(_attach(sample, population), info, lambda r: True)
        estimates.append(result["precision"])
        lo, hi = result["linearised_interval"]
        covered += lo <= truth <= hi
    return sum(estimates) / len(estimates) - truth, covered / reps, truth


def test_estimator_is_unbiased_and_covers_fast():
    bias, coverage, _ = _simulate(300)
    assert abs(bias) < 0.01
    assert 0.85 <= coverage <= 1.0


@pytest.mark.slow  # 2000 replicates
def test_estimator_is_unbiased_and_covers_on_synthetic_registry():
    bias, coverage, _ = _simulate(2000)
    assert abs(bias) < 0.005
    assert 0.90 <= coverage <= 0.99


def test_zero_errors_never_print_perfect_precision():
    frame = _synthetic_frame(60, seed=3)
    perfect = [dict(r, verdict="correct") for r in frame]
    sample, info = adj.draw_sample(perfect, "units", 30, seed=1)
    result = adj.estimate(_attach(sample, perfect), info)
    dom = result["domains"]["combined"]
    assert dom["incorrect"] == 0
    assert dom["precision"] is None
    assert "Zero observed errors do not establish perfect precision" in dom["statement"]
    assert dom["error_rate_upper_cp_sample_only"] == pytest.approx(1 - 0.05 ** (1 / dom["n_judged"]), abs=1e-9)
    assert "Sample-only" in dom["statement"]
    text = adj.render_text(result)
    assert "100%" not in text and "100.00%" not in text
    assert "precision (Hajek" not in text.split("bindings:")[0]


def test_finite_population_correction_edges():
    frame = _synthetic_frame(60, seed=5)
    population = _judged_population(frame, seed=6)
    population = [dict(r, verdict="incorrect") if i % 2 == 0 else r for i, r in enumerate(population)]
    census, info = adj.draw_sample(population, "units", len(population), seed=1)
    assert all(st["n"] == st["N"] for st in info["strata"].values())
    res = adj.estimate_domain(_attach(census, population), info, lambda r: True)
    assert res["variance"] == 0.0 and res["se"] == 0.0  # n == N: no sampling variance
    assert res["clopper_pearson_unweighted"][0] <= res["precision"] <= res["clopper_pearson_unweighted"][1]
    # one unit per stratum: the variance of a stratum with N_h > 1 is not estimable
    n_strata = len({r["stratum"] + "|" + r["tercile"] for r in population})
    one, info1 = adj.draw_sample(population, "units", n_strata, seed=2)
    res1 = adj.estimate_domain(_attach(one, population), info1, lambda r: True)
    assert res1["variance"] is None and res1["linearised_interval"] is None
    assert res1["precision"] is not None


def test_unjudgeable_rows_stay_out_of_the_denominator():
    frame = _synthetic_frame(300, seed=7)
    population = [dict(r, verdict=("unjudgeable" if i % 4 == 0 else r["verdict"]))
                  for i, r in enumerate(_judged_population(frame, seed=8))]
    sample, info = adj.draw_sample(population, "units", 150, seed=3)
    res = adj.estimate_domain(_attach(sample, population), info, lambda r: True)
    assert res["unjudgeable"] > 0
    assert res["n_judged"] == res["correct"] + res["incorrect"]
    sens = res["unjudgeable_sensitivity"]
    assert sens["all_incorrect"] <= res["precision"] <= sens["all_correct"]
    assert res["frame_unjudgeable_estimate"] > 0


def test_domains_and_strata_are_reported_separately():
    frame = _synthetic_frame(300, seed=12)
    population = _judged_population(frame, seed=13)
    sample, info = adj.draw_sample(population, "units", 90, seed=4)
    result = adj.estimate(_attach(sample, population), info)
    assert set(result["domains"]) == {"combined", "bindings", "constraints", "pairwise", "group", "conditional"}
    assert result["domains"]["bindings"]["n_sampled"] + result["domains"]["constraints"]["n_sampled"] == 90
    assert set(result["strata"]) == set(info["strata"])


# --------------------------------------------------------------------------------------------- export and import


def test_packets_are_blinded(tmp_path):
    sub, corpus = _write_submission(tmp_path)
    frame, _ = adj.build_frame(sub, corpus)
    sample, _ = adj.draw_sample(frame, "units", len(frame), seed=1)
    out = tmp_path / "export"
    adj.export_packets(frame, sample, ["ann_a", "ann_b"], 0.5, seed=2, out=out)
    banned = ["gold", "model", "Qwen", "stratum", "cluster", "canonical", "verdict", "ax:", "claim_id", "constraint",
              "binding", "pairwise", "group", "+v:", "-v:", "polymarket:"]
    for name in ("ann_a", "ann_b"):
        raw = (out / "packets" / f"{name}.jsonl").read_text(encoding="utf-8")
        for line in raw.splitlines():
            item = json.loads(line)
            assert set(item) == {"assertion_id", "claim", "sources"}
            assert all(set(s) == {"ref", "text"} for s in item["sources"])
        lowered = raw.lower()
        for word in banned:
            assert word.lower() not in lowered, word
        with open(out / "packets" / f"{name}.csv", encoding="utf-8", newline="") as fh:
            assert next(csv.reader(fh)) == ["assertion_id", "claim", "sources"]
    assignments = adj._read_jsonl(out / "assignments.jsonl")
    assert any(a["double"] for a in assignments), "overlap yields double-annotated items"
    assert set(adj._read_jsonl(out / "assignments.jsonl")[0]) == {"assertion_id", "annotator", "double"}


def test_import_validates_and_resolves(tmp_path):
    sub, corpus = _write_submission(tmp_path)
    frame, _ = adj.build_frame(sub, corpus)
    sample, _ = adj.draw_sample(frame, "units", len(frame), seed=1)
    out = tmp_path / "export"
    adj.export_packets(frame, sample, ["ann_a", "ann_b"], 1.0, seed=2, out=out)  # every item double-annotated
    ids = [s["assertion_id"] for s in sample]
    ann = tmp_path / "ann"
    ann.mkdir()
    # disagreement on the first item, agreement elsewhere
    adj._write_jsonl(ann / "ann_a.jsonl", [{"assertion_id": i, "verdict": "correct", "rationale": "",
                                            "annotator": "ann_a"} for i in ids])
    adj._write_jsonl(ann / "ann_b.jsonl", [{"assertion_id": i, "verdict": "incorrect" if k == 0 else "correct",
                                            "rationale": "x", "annotator": "ann_b"} for k, i in enumerate(ids)])
    res_dir = tmp_path / "resolved"
    agreement = adj.import_annotations(out, [ann / "ann_a.jsonl", ann / "ann_b.jsonl"], None, res_dir)
    assert agreement["items"]["pending"] == 1 and agreement["items"]["agreed"] == len(ids) - 1
    adj._write_jsonl(ann / "adj.jsonl", [{"assertion_id": ids[0], "verdict": "correct", "rationale": "",
                                          "annotator": "adjudicator"}])
    agreement = adj.import_annotations(out, [ann / "ann_a.jsonl", ann / "ann_b.jsonl"], ann / "adj.jsonl", res_dir)
    assert agreement["items"]["adjudicated"] == 1
    resolved = {r["assertion_id"]: r for r in adj._read_jsonl(res_dir / "resolved.jsonl")}
    assert resolved[ids[0]]["final_verdict"] == "correct" and resolved[ids[0]]["status"] == "adjudicated"


def test_import_rejects_malformed_annotations(tmp_path):
    sub, corpus = _write_submission(tmp_path)
    frame, _ = adj.build_frame(sub, corpus)
    sample, _ = adj.draw_sample(frame, "units", len(frame), seed=1)
    out = tmp_path / "export"
    adj.export_packets(frame, sample, ["ann_a", "ann_b"], 0.0, seed=2, out=out)
    ident = sample[0]["assertion_id"]
    cases = {
        "bad verdict": {"assertion_id": ident, "verdict": "maybe", "rationale": "", "annotator": "ann_a"},
        "no rationale for incorrect": {"assertion_id": ident, "verdict": "incorrect", "rationale": "  ",
                                       "annotator": "ann_a"},
        "unknown id": {"assertion_id": "0" * 64, "verdict": "correct", "rationale": "", "annotator": "ann_a"},
    }
    for label, row in cases.items():
        path = tmp_path / f"{label.replace(' ', '_')}.jsonl"
        adj._write_jsonl(path, [row])
        with pytest.raises(adj.AdjudicationError):
            adj.import_annotations(out, [path], None, tmp_path / "r")
    unassigned = [a for a in adj._read_jsonl(out / "assignments.jsonl") if a["annotator"] == "ann_b"][0]
    path = tmp_path / "wrong_annotator.jsonl"
    adj._write_jsonl(path, [{"assertion_id": unassigned["assertion_id"], "verdict": "correct", "rationale": "",
                             "annotator": "ann_a"}])
    with pytest.raises(adj.AdjudicationError, match="not assigned"):
        adj.import_annotations(out, [path], None, tmp_path / "r")


def test_cli_round_trip_on_synthetic_submission(tmp_path, capsys):
    sub, corpus = _write_submission(tmp_path)
    frames = tmp_path / "frame"
    assert adj.main(["frame", "--submission", str(sub), "--corpus", str(corpus), "--out", str(frames)]) == 0
    samp = tmp_path / "sample"
    assert adj.main(["sample", "--frame", str(frames / "frame.jsonl"), "--design", "units", "--n", "7",
                     "--seed", "3", "--out", str(samp)]) == 0
    exp = tmp_path / "export"
    assert adj.main(["export", "--frame", str(frames / "frame.jsonl"), "--sample-dir", str(samp),
                     "--annotators", "ann_a,ann_b", "--overlap", "0.3", "--seed", "4", "--out", str(exp)]) == 0
    ann = tmp_path / "ann"
    ann.mkdir()
    for name in ("ann_a", "ann_b"):
        rows = adj._read_jsonl(exp / "packets" / f"{name}.jsonl")
        adj._write_jsonl(ann / f"{name}.jsonl", [{"assertion_id": r["assertion_id"], "verdict": "correct",
                                                  "rationale": "", "annotator": name} for r in rows])
    res = tmp_path / "resolved"
    assert adj.main(["import", "--export", str(exp), "--annotations", str(ann / "ann_a.jsonl"),
                     str(ann / "ann_b.jsonl"), "--out", str(res)]) == 0
    assert adj.main(["estimate", "--sample-dir", str(samp), "--resolved", str(res / "resolved.jsonl"),
                     "--out", str(tmp_path / "est.json")]) == 0
    text = capsys.readouterr().out
    assert "Zero observed errors do not establish perfect precision" in text
    assert "100%" not in text


# --------------------------------------------------------------------------------------------- committed fixtures


def test_committed_toy10k_gold_registry(tmp_path):
    name = "toy10k"
    expected = {"rows": 60222, "rows_binding": 6598, "rows_pairwise": 53480, "rows_group": 144,
                "rows_conditional": 200, "excluded_identity_binding": 1728, "excluded_axiom_restatement": 0,
                "excluded_definition": 1806, "excluded_tautology": 0, "duplicates_merged": 0,
                "unresolved_bindings": 200}
    gold = FIXTURES / name / "gold"
    corpus = FIXTURES / name / "corpus.jsonl"
    rows, summary = adj.build_frame(gold, corpus)
    counts = summary["counts"]
    for key, value in expected.items():
        assert counts[key] == value, key
    assert counts["missing_source_texts"] == 0
    assert counts["rows"] == len(rows) == counts["rows_binding"] + counts["rows_pairwise"] + counts["rows_group"]
    sample, info = adj.draw_sample(rows, "units", 200, seed=20261018)
    assert sum(st["n"] for st in info["strata"].values()) == 200
    assert sum(st["N"] for st in info["strata"].values()) == len(rows)


def test_estimator_shape_on_toy10k_units_sample():
    gold = FIXTURES / "toy10k" / "gold"
    corpus = FIXTURES / "toy10k" / "corpus.jsonl"
    rows, _ = adj.build_frame(gold, corpus)
    sample, info = adj.draw_sample(rows, "units", 200, seed=20261018)
    for i, r in enumerate(sample):
        r["verdict"] = "incorrect" if i % 25 == 0 else "correct"  # SIMULATED verdicts, not judgements
    res = adj.estimate(sample, info)
    dom = res["domains"]["combined"]
    assert dom["n_judged"] == 200
    assert dom["precision"] == pytest.approx(1 - dom["incorrect"] / dom["n_judged"], abs=0.05)
    assert dom["linearised_interval"][0] <= dom["precision"] <= dom["linearised_interval"][1]


def test_frame_is_deterministic(tmp_path):
    sub, corpus = _write_submission(tmp_path)
    first, _ = adj.build_frame(sub, corpus)
    second, _ = adj.build_frame(sub, corpus)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# --------------------------------------------------------------------------------------------- fix round (H2, M5, L2-L6)


def _eval_expr(e: dict, env: dict) -> bool:
    op = e["op"]
    if op == "var":
        return env[e["id"]]
    if op == "true":
        return True
    if op == "false":
        return False
    if op == "not":
        return not _eval_expr(e["a"], env)
    if op == "and":
        return all(_eval_expr(a, env) for a in e["args"])
    if op == "or":
        return any(_eval_expr(a, env) for a in e["args"])
    if op == "implies":
        return (not _eval_expr(e["a"], env)) or _eval_expr(e["b"], env)
    if op == "iff":
        return _eval_expr(e["a"], env) == _eval_expr(e["b"], env)
    count = sum(_eval_expr(a, env) for a in e["args"])
    return {"atmost": count <= e["k"], "atleast": count >= e["k"], "exactly": count == e["k"]}[op]


def _eval_atom(atom: str, env: dict) -> bool:
    if atom.startswith("v:"):
        return env[atom[2:]]
    op, k, args = atom.split(":", 2)
    count = sum((env[m[3:]] if m[0] == "+" else not env[m[3:]]) for m in args.split(","))
    return {"atmost": count <= int(k), "atleast": count >= int(k), "exactly": count == int(k)}[op]


def _eval_clauses(clauses: list[list[str]], env: dict) -> bool:
    def lit_value(lit: str) -> bool:
        value = _eval_atom(lit[1:], env)
        return value if lit[0] == "+" else not value

    return all(any(lit_value(lit) for lit in clause) for clause in clauses)


def _small_submission(root: Path, bindings: list[dict], constraints: list[dict],
                      aliases: dict | None = None) -> tuple[Path, Path]:
    sub = root / "small"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "bindings.jsonl").write_text("".join(json.dumps(b) + "\n" for b in bindings), encoding="utf-8")
    (sub / "theory.json").write_text(json.dumps({"interpretations": ["base", "alt"], "aliases": aliases or {},
                                                 "constraints": constraints}), encoding="utf-8")
    (sub / "provenance.jsonl").write_text("", encoding="utf-8")
    corpus = root / "corpus_small.jsonl"
    corpus.write_text("", encoding="utf-8")
    return sub, corpus


def _unresolved(*ids: str) -> list[dict]:
    return [{"id": i, "status": "unresolved", "class": None, "negated": False, "missing": ["no_source"],
             "evidence": []} for i in ids]


def _constraint(cid: str, expr: dict, provenance: list[str], interps: list[str] | None = None) -> dict:
    return {"id": cid, "expr": expr, "interps": interps, "provenance": provenance}


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5])
def test_cardinality_negation_truth_tables(n):
    """Every atmost/atleast/exactly form, negated or not, canonicalises to a CNF with the same truth table."""
    ids = [f"polymarket:{i}:1" for i in range(1, n + 1)]
    args = [_var(i) for i in ids]
    assignments = [dict(zip(ids, bits)) for bits in itertools.product([False, True], repeat=n)]
    classes: dict[str, set[tuple]] = {}
    for op in ("atmost", "atleast", "exactly"):
        for k in range(-1, n + 2):
            for neg in (False, True):
                expr = {"op": op, "k": k, "args": args}
                if neg:
                    expr = _not(expr)
                table = tuple(_eval_expr(expr, env) for env in assignments)
                clauses = adj._lits(adj._cnf(adj._Canon({}).nnf(expr)))
                cnf_table = tuple(_eval_clauses(clauses, env) for env in assignments)
                assert cnf_table == table, (op, k, neg, clauses)
                classes.setdefault(adj._key(clauses), set()).add(table)
    # soundness: one canonical key never stands for two different truth tables
    assert all(len(tables) == 1 for tables in classes.values())


def test_cardinality_negation_keys_match_their_positive_rewrites():
    a, b, c = (_var(f"polymarket:{i}:1") for i in (1, 2, 3))
    # not exactly(1, a, b) is (a and b) or (not a and not b), which is iff(a, b)
    assert _canon_key(_not({"op": "exactly", "k": 1, "args": [a, b]})) == _canon_key({"op": "iff", "a": a, "b": b})
    # not exactly(0, S) is at least one of S
    assert _canon_key(_not({"op": "exactly", "k": 0, "args": [a, b, c]})) == _canon_key(
        {"op": "or", "args": [a, b, c]})
    # not exactly(3, S) on three arguments is at most two of them
    assert _canon_key(_not({"op": "exactly", "k": 3, "args": [a, b, c]})) == _canon_key(
        {"op": "atmost", "k": 2, "args": [a, b, c]})


def test_cnf_cleanup_is_equivalence_preserving():
    rng = random.Random(20261018)
    atoms = ["v:a", "v:b", "v:c"]
    assignments = [dict(zip(("a", "b", "c"), bits)) for bits in itertools.product([False, True], repeat=3)]
    for _ in range(400):
        clauses = []
        for _ in range(rng.randint(1, 5)):
            size = rng.randint(0, 3)
            clauses.append(frozenset((rng.choice(atoms), rng.random() < 0.5) for _ in range(size)))
        cleaned = adj._simplify(clauses)
        for env in assignments:
            before = all(any(_eval_atom(atom, env) == sign for atom, sign in c) for c in clauses)
            after = all(any(_eval_atom(atom, env) == sign for atom, sign in c) for c in cleaned)
            assert before == after
    # absorption, duplicates and unit resolution
    a, b = ("v:a", True), ("v:b", True)
    assert adj._simplify([frozenset([a]), frozenset([a, b])]) == [frozenset([a])]
    assert adj._simplify([frozenset([a]), frozenset([("v:a", False), b])]) == [frozenset([a]), frozenset([b])]
    assert adj._simplify([frozenset([a]), frozenset([("v:a", False)])]) == [frozenset()]
    # equivalent CNFs now share one key
    x = _var("polymarket:1:1")
    y = _var("polymarket:2:1")
    absorbed = {"op": "or", "args": [x, {"op": "and", "args": [x, y]}]}
    assert _canon_key(absorbed) == _canon_key(x)


def test_conditional_rows_are_never_merged_with_unconditional_ones(tmp_path):
    a, b = _var("polymarket:7:1"), _var("polymarket:8:1")
    implies = {"op": "implies", "a": a, "b": b}
    bindings = _unresolved("polymarket:7:1", "polymarket:8:1")
    constraints = [
        _constraint("u", implies, ["src:7:question", "src:8:question"]),
        _constraint("c", implies, ["src:7:question", "src:8:question"], ["alt"]),
        _constraint("c_dup", implies, ["src:7:question", "src:8:question"], ["alt"]),
        _constraint("both", implies, ["src:7:question", "src:8:question"], ["base", "alt"]),
    ]
    sub, corpus = _small_submission(tmp_path, bindings, constraints)
    rows, summary = adj.build_frame(sub, corpus)
    assert len(rows) == 3, "unconditional, alt-only and base+alt rows stay apart"
    assert summary["counts"]["duplicates_merged"] == 1  # c_dup repeats c
    by_scope = {json.dumps(r["interps"]): r for r in rows}
    cond = by_scope['["alt"]']
    assert cond["stratum"] == "pairwise|cond" and cond["cond"] is True
    assert cond["claim"].startswith("Under the interpretation 'alt': ")
    plain = by_scope["null"]
    assert plain["stratum"] == "pairwise" and not plain["claim"].startswith("Under")
    assert by_scope['["alt", "base"]']["claim"].startswith("Under each of these interpretations ('alt'; 'base'): ")
    assert summary["counts"]["rows_conditional"] == 2


def test_own_event_title_is_own_but_another_events_title_is_not(tmp_path):
    implication = {"op": "implies", "a": _var("polymarket:7:1"), "b": _var("polymarket:7:2")}
    bindings = _unresolved("polymarket:7:1", "polymarket:7:2")
    for binding in bindings:  # the market's bindings cite its own event (9)
        binding["evidence"] = ["src:7:question", "src:event:9:event_title"]
    own = ["ax:tie_resolves_one_side", "src:7:question", "src:event:9:event_title"]
    rows, summary = adj.build_frame(*_small_submission(tmp_path / "own", bindings, [_constraint("r", implication, own)]))
    assert rows == [] and summary["counts"]["excluded_axiom_restatement"] == 1
    other = ["ax:tie_resolves_one_side", "src:7:question", "src:event:10:event_title"]
    rows, summary = adj.build_frame(*_small_submission(tmp_path / "other", bindings,
                                                       [_constraint("r", implication, other)]))
    assert len(rows) == 1 and summary["counts"]["excluded_axiom_restatement"] == 0


def test_known_interpretation_labels_render_in_plain_english():
    assert adj._scope_text(["incl_tie_one"]) == (
        "Under the interpretation in which interval endpoints are inclusive and ties resolve to one side: ")
    assert adj._scope_text(["excl_tie_none"]) == (
        "Under the interpretation in which interval endpoints are exclusive and ties resolve to neither side: ")
    assert adj._scope_text(["base"]) == "Under the interpretation 'base': "
    assert adj._scope_text(None) == ""


def test_binary_complement_binding_is_a_definition(tmp_path):
    bindings = [
        {"id": "polymarket:9:1", "status": "bound", "class": "A9", "negated": False, "missing": [], "evidence": []},
        {"id": "polymarket:9:2", "status": "bound", "class": "A9", "negated": True, "missing": [], "evidence": []},
    ]
    rows, summary = adj.build_frame(*_small_submission(tmp_path, bindings, [], {"A9": _var("polymarket:9:1")}))
    assert rows == []
    assert summary["counts"]["excluded_identity_binding"] == 1
    assert summary["counts"]["excluded_definition"] == 1


def test_binding_across_two_markets_is_kept(tmp_path):
    bindings = [
        {"id": "polymarket:8:1", "status": "bound", "class": "A8", "negated": False, "missing": [], "evidence": []},
        {"id": "polymarket:8:2", "status": "unresolved", "class": None, "negated": False, "missing": ["no_source"],
         "evidence": []},
        {"id": "polymarket:9:1", "status": "bound", "class": "A8", "negated": False, "missing": [], "evidence": []},
        {"id": "polymarket:9:2", "status": "bound", "class": "A8", "negated": True, "missing": [], "evidence": []},
    ]
    rows, summary = adj.build_frame(*_small_submission(tmp_path, bindings, [], {"A8": _var("polymarket:8:1")}))
    assert len(rows) == 2 and summary["counts"]["rows_binding"] == 2
    assert summary["counts"]["excluded_definition"] == 0


def test_unresolved_bindings_are_reported_not_asserted(tmp_path):
    rows, summary = adj.build_frame(*_small_submission(tmp_path, _unresolved("polymarket:9:1", "polymarket:9:2"), []))
    assert rows == []  # an unresolved outcome makes no claim, so it is no row and no definition
    assert summary["counts"]["excluded_definition"] == 0
    assert summary["counts"]["unresolved_bindings"] == 2


def test_definitions_need_exactly_two_outcomes_in_the_market(tmp_path):
    complement = _constraint("d", {"op": "iff", "a": _var("polymarket:9:1"), "b": _not(_var("polymarket:9:2"))},
                             ["src:9:question"])
    two = _small_submission(tmp_path / "two", _unresolved("polymarket:9:1", "polymarket:9:2"), [complement])
    rows, summary = adj.build_frame(*two)
    assert rows == [] and summary["counts"]["excluded_definition"] == 1
    three = _small_submission(tmp_path / "three", _unresolved("polymarket:9:1", "polymarket:9:2", "polymarket:9:3"),
                              [complement])
    rows, summary = adj.build_frame(*three)
    assert len(rows) == 1 and summary["counts"]["excluded_definition"] == 0


def test_axiom_restatements_need_own_market_refs_only(tmp_path):
    own = ["ax:tie_resolves_one_side", "src:7:question", "src:7:outcome_label:1", "src:7:outcome_label:2"]
    rows, summary = adj.build_frame(*_small_submission(tmp_path / "own", _unresolved("polymarket:7:1", "polymarket:7:2"),
                                                       [_constraint("r", {"op": "implies", "a": _var("polymarket:7:1"),
                                                                          "b": _var("polymarket:7:2")}, own)]))
    assert rows == [] and summary["counts"]["excluded_axiom_restatement"] == 1
    # an implication (not a complement pair, so no definition exclusion applies) citing a foreign source
    implication = {"op": "implies", "a": _var("polymarket:7:1"), "b": _var("polymarket:7:2")}
    foreign = own + ["src:event:9:event_title"]  # cites a source beyond the market's own question and labels
    rows, summary = adj.build_frame(*_small_submission(tmp_path / "foreign", _unresolved("polymarket:7:1", "polymarket:7:2"),
                                                       [_constraint("r", implication, foreign)]))
    assert len(rows) == 1 and summary["counts"]["excluded_axiom_restatement"] == 0


def test_constant_constraints_are_dropped_only_when_true(tmp_path):
    ax = ["ax:strict_threshold_monotone"]
    rows, summary = adj.build_frame(*_small_submission(tmp_path / "false", [], [_constraint("f", {"op": "false"}, ax)]))
    assert len(rows) == 1 and summary["counts"]["excluded_tautology"] == 0  # a contradiction stays visible
    rows, summary = adj.build_frame(*_small_submission(tmp_path / "true", [], [_constraint("t", {"op": "true"}, ax)]))
    assert rows == [] and summary["counts"]["excluded_tautology"] == 1


def test_double_assigned_item_with_one_answer_stays_pending(tmp_path):
    sub, corpus = _write_submission(tmp_path)
    frame, _ = adj.build_frame(sub, corpus)
    sample, _ = adj.draw_sample(frame, "units", len(frame), seed=1)
    out = tmp_path / "export"
    adj.export_packets(frame, sample, ["ann_a", "ann_b"], 1.0, seed=2, out=out)  # every item double-assigned
    ann = tmp_path / "ann"
    ann.mkdir()
    adj._write_jsonl(ann / "ann_a.jsonl", [{"assertion_id": s["assertion_id"], "verdict": "correct", "rationale": "",
                                            "annotator": "ann_a"} for s in sample])
    res = tmp_path / "res"
    agreement = adj.import_annotations(out, [ann / "ann_a.jsonl"], None, res)
    assert agreement["items"]["pending"] == len(sample)
    assert agreement["items"].get("single", 0) == 0
    resolved = adj._read_jsonl(res / "resolved.jsonl")
    assert all(r["final_verdict"] is None for r in resolved)
    # the adjudicator answering settles them
    adj._write_jsonl(ann / "adj.jsonl", [{"assertion_id": s["assertion_id"], "verdict": "correct", "rationale": "",
                                          "annotator": "adjudicator"} for s in sample])
    agreement = adj.import_annotations(out, [ann / "ann_a.jsonl"], ann / "adj.jsonl", res)
    assert agreement["items"]["adjudicated"] == len(sample)


@pytest.mark.parametrize("bad", ["../x", "a b", "adjudicator", "", "x/y"])
def test_annotator_names_cannot_escape_the_packets(tmp_path, bad):
    sub, corpus = _write_submission(tmp_path)
    frame, _ = adj.build_frame(sub, corpus)
    sample, _ = adj.draw_sample(frame, "units", len(frame), seed=1)
    with pytest.raises(adj.AdjudicationError):
        adj.export_packets(frame, sample, ["ann_a", bad], 0.0, seed=2, out=tmp_path / "export")
    if bad != "adjudicator":  # the reserved name is valid on adjudication rows only
        row = {"assertion_id": sample[0]["assertion_id"], "verdict": "correct", "rationale": "", "annotator": bad}
        assert adj._check_annotation(row, "t") is not None


def test_allocation_keeps_two_units_per_stratum_when_budget_allows():
    alloc = adj._allocate(10, {"a": 100, "b": 100, "c": 3, "d": 1})
    assert sum(alloc.values()) == 10
    assert alloc["a"] >= 2 and alloc["b"] >= 2 and alloc["c"] >= 2 and alloc["d"] == 1  # d is a census of one
    assert adj._allocate(3, {"a": 5, "b": 5, "c": 5}) == {"a": 1, "b": 1, "c": 1}  # too tight for two each

"""results/v0.2.1/build_bundle.py: the analysis release based on v0.2.0 (fast). The rows are v0.2.0's; the analysis differs only in its
release field; the profiles are recomputed independently for two models and checked for their invariants; the example rule; the
reference builder on a synthetic scored results.json (and its refusal while unscored); the index hand-over."""

import gzip
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools" / "eval"))

FIXTURE = ROOT / "fixtures" / "mentat_hard_test"
V021 = ROOT / "results" / "v0.2.1"
V020 = ROOT / "results" / "v0.2.0"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B = _load("build_bundle_v021", V021 / "build_bundle.py")


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def predictions(model: str) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress((V020 / "predictions" / f"{model}.jsonl.gz").read_bytes())
            .decode("utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- rows and analysis

def test_leaderboard_rows_equal_v020_exactly():
    v20, v21 = load("results/v0.2.0/leaderboard.json"), load("results/v0.2.1/leaderboard.json")
    assert v21["models"] == v20["models"] and v21["baselines"] == v20["baselines"]
    assert v21["ranking"] == v20["ranking"] and v21["resolved_comparisons"] == v20["resolved_comparisons"]
    assert v21["depth_buckets"] == v20["depth_buckets"] and v21["depth_n"] == v20["depth_n"]
    assert (V021 / "leaderboard.csv").read_bytes() == (V020 / "leaderboard.csv").read_bytes()


def test_leaderboard_provenance_is_based_on_v020_without_record_from():
    lb = load("results/v0.2.1/leaderboard.json")
    assert lb["release"] == "v0.2.1" and lb["date"] == "2026-11-03"
    prov = lb["provenance"]
    assert "record_from" not in prov
    assert prov["based_on_release"] == "v0.2.0" and prov["bundle_builder"] == "results/v0.2.1/build_bundle.py"
    analysis = (V021 / "analysis.json").read_bytes()
    assert prov["analysis"] == {"path": "results/v0.2.1/analysis.json", "sha256": B.sha256(analysis)}


def test_analysis_differs_from_v020_only_in_its_release_field():
    a20, a21 = load("results/v0.2.0/analysis.json"), load("results/v0.2.1/analysis.json")
    assert a20["release"] == "v0.2.0" and a21["release"] == "v0.2.1"
    a20.pop("release")
    a21.pop("release")
    assert a21 == a20
    assert a21["based_on"]["release"] == "v0.2.0"
    assert a21["based_on"]["leaderboard"]["path"] == "results/v0.2.0/leaderboard.json"


def test_analysis_keeps_version_shift_and_both_prediction_sources():
    an = load("results/v0.2.1/analysis.json")
    assert an["method"]["version_shift"] and an["robustness"]["summary"]["version_shift"]["n_models"] == 15
    preds = an["based_on"]["predictions"]
    assert any(k.startswith("results/v0.1.0/predictions/") for k in preds)
    assert any(k.startswith("results/v0.2.0/predictions/") for k in preds)


# ---------------------------------------------------------------- profiles

def _independent(model: str) -> dict:
    """The profile numbers of one model, from its raw predictions and the candidates only (no builder code)."""
    cands = {r["qid"]: r for r in jsonl(FIXTURE / "candidates.jsonl")}
    rows = predictions(model)
    rel = [r for r in rows if r["op"] == "decide_relation"]
    grp = [r for r in rows if r["op"] == "decide_group" and cands[r["qid"]].get("source") == "curated"]
    assert len(rel) == 6000 and len(grp) == 1000
    rel_correct = sum(1 for r in rel if r["pred"] == r["gold"])  # an abstention is its recorded NONE answer, as published
    rel_missing = sum(1 for r in rel if r.get("abstained"))
    grp_correct = sum(1 for r in grp if r["pred"] == r["gold"])
    sizes, kinds = Counter(), Counter()
    right_size, right_kind = Counter(), Counter()
    for r in grp:
        n = len(cands[r["qid"]]["members"])
        bucket = "2" if n == 2 else "3" if n == 3 else "4-8"
        k = "+".join(t.split(":")[0] for t in r["gold"].split("+"))
        sizes[bucket] += 1
        kinds[k] += 1
        if r["pred"] == r["gold"]:
            right_size[bucket] += 1
            right_kind[k] += 1
    return {"relation": (rel_correct, rel_missing), "group": (grp_correct, sizes, right_size, kinds, right_kind)}


@pytest.mark.parametrize("model", ["d3_27b-q8_0", "laya_latest"])
def test_profiles_recomputed_independently_for_two_models(model):
    profile = load("results/v0.2.1/profiles.json")["models"][model]
    want = _independent(model)
    rel_correct, rel_missing = want["relation"]
    assert profile["relation"] == {"accuracy": round(rel_correct / 6000, 6), "correct": rel_correct, "n": 6000}
    assert profile["missing"]["relation"] == rel_missing
    grp_correct, sizes, right_size, kinds, right_kind = want["group"]
    assert profile["group"]["correct"] == grp_correct and profile["group"]["n"] == 1000
    for bucket in ("2", "3", "4-8"):
        got = profile["group"]["by_set_size"][bucket]
        assert (got["n"], got["correct"]) == (sizes[bucket], right_size[bucket]), bucket
        assert got["accuracy"] == (round(right_size[bucket] / sizes[bucket], 6) if sizes[bucket] else None)
    for kind in B.GROUP_KINDS:
        got = profile["group"]["by_kind"][kind]
        assert (got["n"], got["correct"]) == (kinds[kind], right_kind[kind]), kind
    assert profile["missing"]["group"] == 0


def test_d3_27b_relation_and_group_numbers_are_the_checked_ones():
    profile = load("results/v0.2.1/profiles.json")["models"]["d3_27b-q8_0"]
    assert profile["relation"] == {"accuracy": 0.827167, "correct": 4963, "n": 6000}  # the leaderboard's depth buckets sum to 4,963
    assert profile["group"]["accuracy"] == 0.372 and profile["group"]["correct"] == 372
    assert {b: (v["correct"], v["n"]) for b, v in profile["group"]["by_set_size"].items()} == {
        "2": (250, 389), "3": (90, 352), "4-8": (32, 259)}


def test_relation_correct_equals_the_leaderboard_depth_buckets_for_every_model():
    """Relations are scored as the published scores score them: the profile's relation correct count is the sum of the
    leaderboard's depth buckets (NONE included), for every one of the 17 models."""
    doc = load("results/v0.2.1/profiles.json")
    lb = load("results/v0.2.0/leaderboard.json")
    assert {e["id"] for e in lb["models"]} == set(doc["models"])
    for e in lb["models"]:
        buckets = e["depth_accuracy"].values()
        assert sum(b["n"] for b in buckets) == 6000, e["id"]
        assert doc["models"][e["id"]]["relation"]["correct"] == sum(b["correct"] for b in buckets), e["id"]
    assert doc["models"]["laya_latest"]["relation"]["correct"] == 1407  # its 4,981 abstentions are NONE answers, 1,198 of them right
    assert doc["models"]["laya_latest"]["missing"]["relation"] == 4981


def test_profiles_invariants_for_all_seventeen_models():
    doc = load("results/v0.2.1/profiles.json")
    lb = load("results/v0.2.0/leaderboard.json")
    ranking = [e["id"] for e in sorted(lb["models"], key=lambda e: e["rank"])]
    assert doc["schema"] == "mentat-bench-profiles/1" and doc["release"] == "v0.2.1" and doc["based_on"] == "v0.2.0"
    assert list(doc["models"]) == ranking and len(ranking) == 17
    assert doc["set_size_buckets"] == ["2", "3", "4-8"] and doc["group_kinds"] == B.GROUP_KINDS
    assert len(doc["leagues"]) == 18
    assert all(v["name"] and v["card"] for v in doc["leagues"].values())
    corpus = {r["id"]: r for r in jsonl(FIXTURE / "corpus.jsonl")}
    cands = {r["qid"]: r for r in jsonl(FIXTURE / "candidates.jsonl")}
    suspect = {v["qid"] for v in load("docs/audits/v0.1.1-item-audit/verdicts.json")["items"] if v["verdict"] in B.SUSPECT}
    for model, d in doc["models"].items():
        rel, grp = d["relation"], d["group"]
        assert rel["n"] == 6000 and grp["n"] == 1000, model
        assert rel["accuracy"] == round(rel["correct"] / 6000, 6) and grp["accuracy"] == round(grp["correct"] / 1000, 6)
        assert sum(v["n"] for v in grp["by_set_size"].values()) == 1000
        assert sum(v["correct"] for v in grp["by_set_size"].values()) == grp["correct"]
        assert list(grp["by_set_size"]) == ["2", "3", "4-8"]
        assert sum(v["n"] for v in grp["by_kind"].values()) == 1000
        assert sum(v["correct"] for v in grp["by_kind"].values()) == grp["correct"]
        assert list(grp["by_kind"]) == B.GROUP_KINDS
        for block in list(grp["by_set_size"].values()) + list(grp["by_kind"].values()):
            assert block["accuracy"] == (round(block["correct"] / block["n"], 6) if block["n"] else None)
        abstained = sum(1 for r in predictions(model) if r["op"] == "decide_relation" and r["abstained"])
        assert d["missing"] == {"relation": abstained, "group": 0}, model
        assert len(d["examples"]) <= 2
        for e in d["examples"]:
            assert e["gold"] != e["pred"] and e["op"] in ("decide_relation", "decide_group")
            assert e["qid"] not in suspect and e["league"] in doc["leagues"]
            if e["op"] == "decide_relation":
                assert e["qid"].startswith("r") and len(e["outcomes"]) == 2
                assert [o["text"] for o in e["outcomes"]] == [corpus[i]["text"] for i in (cands[e["qid"]]["a"], cands[e["qid"]]["b"])]
            else:
                assert cands[e["qid"]]["source"] == "curated" and len(e["outcomes"]) == len(cands[e["qid"]]["members"])
            assert all(o["label"] in ("Yes", "No") for o in e["outcomes"])
    assert [e["op"] for e in doc["models"]["d3_27b-q8_0"]["examples"]] == ["decide_relation", "decide_group"]


def test_example_for_d3_27b_is_its_modal_cell_with_the_most_others_and_the_lowest_qid():
    """Independent check of the rule for one model: the chosen cell, its count, and the chosen item."""
    model = "d3_27b-q8_0"
    doc = load("results/v0.2.1/profiles.json")
    suspect = {v["qid"] for v in load("docs/audits/v0.1.1-item-audit/verdicts.json")["items"] if v["verdict"] in B.SUSPECT}
    by_model = {m: {r["qid"]: r for r in predictions(m)} for m in doc["models"]}
    others_of = [m for m in doc["models"] if m != model]
    for op in ("decide_relation", "decide_group"):
        qids = [q for q, r in by_model[model].items() if r["op"] == op and (op == "decide_relation" or
                                                                           r["stratum"] in ("real", "decoy"))]
        eligible = [q for q in qids if q not in suspect and not by_model[model][q].get("abstained")
                    and by_model[model][q]["pred"] != by_model[model][q]["gold"]]
        cells = Counter((by_model[model][q]["gold"], by_model[model][q]["pred"]) for q in eligible)
        top = max(cells.values())
        cell = min(c for c, n in cells.items() if n == top)  # ties by class name: the smaller (gold, pred)
        in_cell = [q for q in eligible if (by_model[model][q]["gold"], by_model[model][q]["pred"]) == cell]

        def others(q):
            return sum(1 for m in others_of if by_model[m][q]["pred"] == by_model[m][q]["gold"])

        pick = min(in_cell, key=lambda q: (-others(q), q))
        example = next(e for e in doc["models"][model]["examples"] if e["op"] == op)
        assert (example["gold"], example["pred"]) == cell and example["cell_count"] == top, op
        assert example["qid"] == pick and example["others_correct"] == others(pick), op


def test_choose_example_takes_the_modal_cell_then_the_most_others_then_the_lowest_qid():
    wrong = {"r2": ("IMPLIES", "NONE"), "r1": ("EXCLUDES", "NONE"), "r3": ("IMPLIES", "NONE"), "r4": ("EQUIV", "NONE"),
             "r5": ("EQUIV", "NONE")}
    others = {"r1": 16, "r2": 5, "r3": 9, "r4": 3, "r5": 3}
    # IMPLIES and EQUIV both hold 2 items: the tie goes to the smaller class name (EQUIV); within it r4 and r5 tie on 3 others
    assert B.choose_example(wrong, others) == (("EQUIV", "NONE"), 2, "r4")
    others["r5"] = 4
    assert B.choose_example(wrong, others) == (("EQUIV", "NONE"), 2, "r5")
    assert B.choose_example({}, {}) is None


# ---------------------------------------------------------------- reference

LEADER = load("results/v0.2.0/leaderboard.json")
MODELS = LEADER["ranking"]
BANNED = ("beats", "better than", "outperform", "smarter", "general reasoning")


def _weighted(rel: float, grp: float) -> dict:
    head = (rel + grp) / 2
    return {"relation": {"point": rel, "lo": rel - 0.05, "hi": rel + 0.05},
            "group": {"point": grp, "lo": grp - 0.02, "hi": grp + 0.02},
            "headline": {"point": head, "lo": head - 0.03, "hi": head + 0.03}}


def synthetic_scored() -> dict:
    """A results.json as score.py writes it once the three instances are complete (the same keys; the numbers are made up)."""
    points = {"1": (0.30, 0.20), "2": (0.40, 0.10), "3": (0.35, 0.15)}
    instances = {k: {"status": "complete", "protocol": {"slices": {}, "voids": []}, "weighted": _weighted(r, g),
                     "unweighted": _weighted(r, g), "by_depth": {}, "by_set_size": {}, "slices": []}
                 for k, (r, g) in points.items()}
    mean_r = sum(r for r, _ in points.values()) / 3
    mean_g = sum(g for _, g in points.values()) / 3
    agree = {"pairwise": {"inst1-inst2": 0.6, "inst1-inst3": 0.7, "inst2-inst3": 0.8}, "pairwise_both_answered": {},
             "both_answered_items": {}, "fleiss_kappa": 0.5}
    return {
        "schema": "mentat-bench-reasoning-reference/1", "status": "scored",
        "design": {"items": 500, "relations": 300, "groups": 200, "sample_seed": "reasoning-reference-v0.2.0", "slices": 10,
                   "fixture": "7b408459538d7420e808d5d81e71916c57aee78f459a6b8c0905e48e43c8e6ab", "weights": "w"},
        "bootstrap": {"B": 2000, "seed": 0, "leagues": 18, "draws": "tools/eval/league_ci.py draws", "interval": "i"},
        "models": {m: {"weighted": _weighted(0.1 + 0.01 * i, 0.05 + 0.01 * i), "unweighted": {}, "by_depth": {}, "by_set_size": {}}
                   for i, m in enumerate(MODELS)},
        "instances": instances,
        "three_instances": {"status": "complete", "complete_instances": [1, 2, 3], "weighted": _weighted(mean_r, mean_g),
                            "unweighted": _weighted(mean_r, mean_g), "instance_range": {},
                            "agreement": {"decide_relation": agree, "decide_group": dict(agree, fleiss_kappa=0.4)}},
    }


def walk_strings(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)
    elif isinstance(obj, str):
        yield obj


def test_reference_from_a_synthetic_scored_results_json():
    doc = json.loads(B.reference_bytes(synthetic_scored(), LEADER).decode("utf-8"))
    assert doc["schema"] == "mentat-bench-reference/1" and doc["release"] == "v0.2.1"
    assert len(doc["references"]) == 1
    ref = doc["references"][0]
    assert ref["id"] == "haiku-5-5-reasoning" and ref["ranked"] is False and ref["kind"] == "reasoning_reference"
    assert ref["model"] == "claude-haiku-5-5" and ref["note"] == "docs/audits/v0.2.0-reasoning-reference/notes.md"
    assert ref["sample"] == {"items": 500, "relations": 300, "groups": 200, "weighted": True, "leagues": 18, "instances": 3}
    assert ref["id"] not in {e["id"] for e in LEADER["models"] + LEADER["baselines"]}
    metrics = ref["metrics"]
    assert metrics["headline"]["point"] == pytest.approx(0.25, abs=1e-6)  # the mean of the three instances' headlines
    assert metrics["relation"]["point"] == pytest.approx(0.35, abs=1e-6)
    assert metrics["relation"]["ci95"] == pytest.approx([0.30, 0.40])
    assert [i["id"] for i in ref["instances"]] == ["1", "2", "3"]
    assert ref["instances"][0]["relation"] == pytest.approx(0.30) and ref["instances"][0]["group"] == pytest.approx(0.20)
    assert ref["agreement"]["relation"]["pairwise"] == pytest.approx(0.7) and ref["agreement"]["relation"]["fleiss_kappa"] == 0.5
    assert ref["agreement"]["group"]["fleiss_kappa"] == 0.4
    assert [m["id"] for m in doc["models_on_sample"]] == MODELS and len(doc["models_on_sample"]) == 17
    first = doc["models_on_sample"][0]["headline"]  # model 0: relation 0.10, group 0.05, headline 0.075
    assert first["point"] == pytest.approx(0.075) and first["ci95"] == pytest.approx([0.045, 0.105])
    assert not B.has_key(doc, "rank")
    text = " ".join(walk_strings(doc)).lower()
    assert not any(word in text for word in BANNED)


def test_reference_is_deterministic():
    results = synthetic_scored()
    assert B.reference_bytes(results, LEADER) == B.reference_bytes(json.loads(json.dumps(results)), LEADER)


def test_reference_refuses_while_the_instances_are_absent():
    results = synthetic_scored()
    results["three_instances"] = {"status": "incomplete", "complete_instances": []}
    results["instances"] = {k: {"status": "absent"} for k in ("1", "2", "3")}
    with pytest.raises(B.NotScored, match="reference results not scored yet"):
        B.reference_bytes(results, LEADER)
    results["instances"]["1"] = {"status": "complete", "weighted": _weighted(0.3, 0.2)}
    with pytest.raises(B.NotScored):
        B.reference_bytes(results, LEADER)  # two instances still absent


def test_reference_fails_loudly_on_a_null_fleiss_kappa():
    """A kappa is null only when every rating is in one category; the contract has no null, so the build fails instead."""
    results = synthetic_scored()
    results["three_instances"]["agreement"]["decide_group"]["fleiss_kappa"] = None
    with pytest.raises(B.CheckError, match="fleiss_kappa"):
        B.reference_bytes(results, LEADER)


def test_reference_refuses_a_point_that_is_not_the_mean_of_the_instances():
    results = synthetic_scored()
    results["three_instances"]["weighted"]["relation"]["point"] = 0.9
    with pytest.raises(B.CheckError, match="not the mean"):
        B.reference_bytes(results, LEADER)


# ---------------------------------------------------------------- outputs and the index

def test_build_outputs_are_deterministic_and_are_the_committed_files():
    first = B.build_outputs()
    assert first == B.build_outputs()
    assert set(first) == {"leaderboard.json", "leaderboard.csv", "analysis.json", "profiles.json"}  # no index: v0.2.2 is latest
    for name, data in first.items():
        assert (V021 / name).read_bytes() == data, name
    assert B.index_doc() is None


def test_build_refuses_then_writes_and_checks_a_scored_reference_in_a_scratch_tree(tmp_path, monkeypatch, capsys):
    """build writes the other files and refuses the reference until it is scored; once the (synthetic) results are scored it writes
    reference.json, and --check then passes. Every path is a scratch path: no committed file is touched."""
    (tmp_path / "v0.2.1").mkdir()
    (tmp_path / "index.json").write_bytes((ROOT / "results" / "index.json").read_bytes())
    scored = tmp_path / "results.json"
    scored.write_text(json.dumps(synthetic_scored()), encoding="utf-8")
    monkeypatch.setattr(B, "RESULTS", tmp_path)
    monkeypatch.setattr(B, "V021", tmp_path / "v0.2.1")
    monkeypatch.setattr(B, "REFERENCE_RESULTS", tmp_path / "unscored.json")
    (tmp_path / "unscored.json").write_text(json.dumps({"schema": B.SCORED_SCHEMA, "status": "models_only", "three_instances":
                                                         {"status": "incomplete"}, "instances": {}}), encoding="utf-8")
    assert B.build(check_only=True) == 1
    assert "reference results not scored yet" in capsys.readouterr().err
    assert B.build(check_only=False) == 1  # the other files are written; the reference is refused
    assert not (tmp_path / "v0.2.1" / "reference.json").exists()
    assert (tmp_path / "v0.2.1" / "profiles.json").read_bytes() == (ROOT / "results" / "v0.2.1" / "profiles.json").read_bytes()
    monkeypatch.setattr(B, "REFERENCE_RESULTS", scored)
    assert B.build(check_only=False) == 0
    assert (tmp_path / "v0.2.1" / "reference.json").read_bytes() == B.reference_bytes(synthetic_scored(), LEADER)
    assert B.build(check_only=True) == 0


def test_committed_index_hands_v021_over_to_v022_and_keeps_its_entry():
    index = load("results/index.json")
    assert index["latest"] == "v0.2.2" and index["versions"][0]["version"] == "v0.2.2"
    entry = next(v for v in index["versions"] if v["version"] == "v0.2.1")
    assert entry["status"] == "superseded" and entry["superseded_by"] == "v0.2.2" and "v0.2.2" in entry["note"]
    assert entry["based_on"] == "v0.2.0" and "record_from" not in entry
    for key in ("analysis_json", "leaderboard_json", "leaderboard_csv", "profiles_json", "reference_json", "readme", "builder"):
        assert entry[key].startswith("results/v0.2.1/"), key
    assert entry["predictions_dir"] == "results/v0.2.0/predictions" and entry["cells_dir"] == "results/v0.2.0/cells"
    assert entry["runcards_dir"] == "release/v0.2.0/runcards"
    v020 = next(v for v in index["versions"] if v["version"] == "v0.2.0")
    assert v020["status"] == "superseded" and v020["superseded_by"] == "v0.2.1" and "v0.2.1" in v020["note"]
    assert B.index_doc() is None  # the v0.2.2 builder owns the index while v0.2.2 is its latest release


def test_index_builder_leaves_the_index_alone_once_a_later_release_is_latest(tmp_path, monkeypatch):
    index = load("results/index.json")
    (tmp_path / "index.json").write_text(json.dumps(dict(index, latest="v1.0.0")), encoding="utf-8")
    monkeypatch.setattr(B, "RESULTS", tmp_path)
    assert B.index_doc() is None

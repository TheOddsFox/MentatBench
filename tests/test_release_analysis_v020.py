"""tools/eval/release_analysis_v020.py and the v0.1.1 helpers it reuses, on synthetic rows (fast). The full build over the committed
v0.2.0 results is a slow test (MB_RUN_SLOW=1 or -m slow) and runs only once those results are committed."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "eval"))
sys.path.insert(0, str(ROOT / "python"))
import release_analysis as RA  # noqa: E402

spec = importlib.util.spec_from_file_location("release_analysis_v020", ROOT / "tools" / "eval" / "release_analysis_v020.py")
RA20 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(RA20)


def test_family_size_of_seventeen_models_is_136():
    assert RA20._family(17) == 136
    assert RA20._family(15) == 105  # the v0.1.1 family size, as the v0.1.1 analysis has it


def test_v020_repeat_notes_are_the_three_named_ones():
    assert set(RA20.REPEAT_NOTES) == {"note_protocol", "note_identical", "note_batching"}


def test_v020_cost_hardware_names_the_engine_and_the_parallelism():
    assert RA20.HARDWARE.endswith("OLLAMA_NUM_PARALLEL=2") and "0.40.13" in RA20.HARDWARE


def test_robustness_reads_the_repeat_and_the_version_shift_and_nulls_the_rest():
    repeats = {"m1": {"r2": {"identical": 0.99, "delta_vs_published": -0.002, "flip_rate": 0.01}}, "note_protocol": "p"}
    shift = {"m1": {"delta": 0.01, "flip_rate": 0.05}}
    rob = RA20._robustness(repeats, ["m1", "kev_4b-q8_0"], shift)
    m1 = rob["models"]["m1"]
    assert m1["run_to_run"] == {"max_abs_delta": 0.002, "max_flip_rate": 0.01}
    assert m1["shuffle"] is None and m1["vulkan"] is None and m1["version_shift"] == {"delta": 0.01, "flip_rate": 0.05}
    kev = rob["models"]["kev_4b-q8_0"]
    assert kev["run_to_run"] is None and kev["version_shift"] is None
    summary = rob["summary"]
    assert summary["presentation"] is None and summary["presentation_matched"] is None and summary["backend"] is None
    assert summary["run_to_run"] == {"n_models": 1, "max_abs_delta": 0.002}
    assert summary["version_shift"]["n_models"] == 1 and summary["version_shift"]["max_abs_delta"] == 0.01
    assert summary["version_shift"]["from"] == {"release": "v0.1.0", "ollama": "0.40.11"}
    assert summary["version_shift"]["to"] == {"ollama": "0.40.13"}


def test_repeats_take_the_notes_they_are_given(tmp_path):
    (tmp_path / "runs.json").write_text(json.dumps({"schema": RA.REPEAT_SCHEMA, "runs": []}), encoding="utf-8")
    assert RA._repeats(tmp_path, {}, {}, None, 0, notes=RA20.REPEAT_NOTES) == RA20.REPEAT_NOTES
    assert RA._repeats(tmp_path, {}, {}, None, 0) == RA.REPEAT_NOTES  # the v0.1.1 default is unchanged


def test_cost_takes_the_hardware_it_is_given():
    entries = {"m": {"runtime": {"build_wall_s": 60.0}, "counts": {"decisions": 120}, "size_bytes": 10, "params": "4B",
                     "quant": "q8_0"}}
    assert RA._cost(["m"], entries, "box")["hardware"] == "box"
    assert RA._cost(["m"], entries)["hardware"] == RA.HARDWARE


def test_weighting_prose_takes_the_family_size_from_the_document():
    weighting = {"schemes": [{"name": "published", "relation": 0.5, "group": 0.5}], "ranks": {"published": {"a": {
        "rank": 1, "ci95": [1, 1], "median": 1}}}, "resolved": {"published": []}, "robust_pairs": [],
        "weight_dependent_pairs": []}
    doc = {"method": {"family_size": 136}, "weighting": weighting, "models": ["a"]}
    text = "\n".join(RA._weighting_lines(doc, {"a": "A"}))
    assert "own Holm family of 136 pairs" in text and "(of 136)" in text and "105" not in text


def test_robustness_lines_state_each_model_and_the_summary():
    doc = {"models": ["a", "b"], "repeats": {"note_protocol": "P"}, "cost": None, "robustness": {
        "models": {"a": {"shuffle": None, "vulkan": None, "run_to_run": {"max_abs_delta": 0.001, "max_flip_rate": 0.01},
                         "version_shift": {"delta": 0.002, "flip_rate": 0.05}},
                   "b": {"shuffle": None, "vulkan": None, "run_to_run": None, "version_shift": None}},
        "summary": {"presentation": None, "presentation_matched": None, "backend": None,
                    "run_to_run": {"n_models": 1, "max_abs_delta": 0.001},
                    "version_shift": {"n_models": 1, "median_abs_delta": 0.002, "max_abs_delta": 0.002}}}}
    text = "\n".join(RA20._robustness_lines(doc, {}))
    assert "| a | 0.0010 | 1.0% | +0.0020 | 5.0% |" in text
    assert "| b | n/a | n/a | n/a | n/a |" in text
    assert "Version shift: 1 models, median absolute headline change 0.0020, largest 0.0020." in text


@pytest.mark.slow
def test_v020_analysis_json_is_reproduced_by_the_script():
    analysis = ROOT / "results" / "v0.2.0" / "analysis.json"
    if not analysis.is_file():
        pytest.skip("results/v0.2.0/analysis.json is not committed yet")
    doc, _ = RA20.build()
    assert RA.dump(doc) == analysis.read_bytes()


# ---------------------------------------------------------------- repeat identity (v0.2.0: bytes imply labels)

class _Rescore:
    """The v0.1.0 bundle's rescore for the synthetic blobs: each blob's headline is set by the test."""

    def __init__(self, by_blob: dict):
        self.by_blob = by_blob

    def rescore(self, blob: bytes, version: int) -> dict:
        return {"headline": {"relation_group_mcc": self.by_blob[blob]}}


def _repeat_case(tmp_path, flipped_rows: int, identical_bytes: bool, identical_labels: bool):
    """One model with one r2 run whose rows differ from the published rows in flipped_rows items."""
    import gzip as _gzip
    qids = [f"q{i}" for i in range(RA20.ITEMS)]
    rows = [{"qid": q, "op": "decide_relation", "reading": None, "pred": "A"} for q in qids]
    rows_of = {"m00": rows}
    pred = ["B"] * flipped_rows + ["A"] * (len(qids) - flipped_rows)
    blob = _gzip.compress("\n".join(json.dumps({"qid": q, "op": "decide_relation", "reading": None, "pred": p})
                                    for q, p in zip(qids, pred)).encode("utf-8"))
    (tmp_path / "predictions").mkdir(exist_ok=True)
    path = tmp_path / "predictions" / "m00.r2.jsonl.gz"
    path.write_bytes(blob)
    rec = {"id": "m00", "tag": "r2", "predictions": str(path), "status": "complete", "rc": 0,
           "identical_to_published_decisions": identical_bytes, "identical_labels": identical_labels,
           "headline_relation_group_mcc": 0.5}
    (tmp_path / "runs.json").write_text(json.dumps({"schema": RA.REPEAT_SCHEMA, "runs": [rec]}), encoding="utf-8")
    return rows_of, _Rescore({blob: 0.5})


def test_v020_refuses_byte_identical_decisions_with_changed_labels(tmp_path):
    rows_of, bundle = _repeat_case(tmp_path, flipped_rows=1, identical_bytes=True, identical_labels=False)
    with pytest.raises(RA.CheckError, match="byte-identical"):
        RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3, notes=RA20.REPEAT_NOTES, bytes_imply_labels=True)


def test_v020_accepts_probability_only_differences_and_records_both_fields(tmp_path):
    rows_of, bundle = _repeat_case(tmp_path, flipped_rows=0, identical_bytes=False, identical_labels=True)
    out = RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3, notes=RA20.REPEAT_NOTES, bytes_imply_labels=True)
    assert out["m00"]["r2"] == pytest.approx({"identical": 1.0, "delta_vs_published": 0.0, "flip_rate": 0.0,
                                              "identical_decisions": False, "identical_labels": True})
    assert set(out) == {"m00"} | set(RA20.REPEAT_NOTES)


def test_v020_accepts_changed_labels_with_the_bytes_flag_false(tmp_path):
    rows_of, bundle = _repeat_case(tmp_path, flipped_rows=3, identical_bytes=False, identical_labels=False)
    out = RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3, notes=RA20.REPEAT_NOTES, bytes_imply_labels=True)
    assert out["m00"]["r2"]["identical_labels"] is False and out["m00"]["r2"]["identical_decisions"] is False
    assert out["m00"]["r2"]["flip_rate"] == pytest.approx(3 / RA20.ITEMS)


def test_v020_refuses_an_identical_labels_field_that_disagrees_with_the_rows(tmp_path):
    rows_of, bundle = _repeat_case(tmp_path, flipped_rows=0, identical_bytes=False, identical_labels=False)
    with pytest.raises(RA.CheckError, match="identical_labels"):
        RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3, notes=RA20.REPEAT_NOTES, bytes_imply_labels=True)


def test_v011_default_rule_is_unchanged(tmp_path):
    """Without the v0.2.0 flag the old rule holds: identical (labels) must equal the byte flag, and no new field is written."""
    rows_of, bundle = _repeat_case(tmp_path, flipped_rows=0, identical_bytes=False, identical_labels=True)
    with pytest.raises(RA.CheckError, match="disagrees"):
        RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3)
    rows_of, bundle = _repeat_case(tmp_path, flipped_rows=0, identical_bytes=True, identical_labels=True)
    out = RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3)
    assert set(out["m00"]["r2"]) == {"identical", "delta_vs_published", "flip_rate"}


def _hash_case(tmp_path, recorded):
    """One r2 run whose runs.json records the given predictions_sha256 (or none, when recorded is None)."""
    import hashlib
    rows_of, bundle = _repeat_case(tmp_path, flipped_rows=0, identical_bytes=False, identical_labels=True)
    path = tmp_path / "predictions" / "m00.r2.jsonl.gz"
    rec = {"id": "m00", "tag": "r2", "predictions": str(path), "status": "complete", "rc": 0,
           "identical_to_published_decisions": False, "identical_labels": True, "headline_relation_group_mcc": 0.5}
    if recorded is not None:
        rec["predictions_sha256"] = recorded(path.read_bytes()) if callable(recorded) else recorded
    (tmp_path / "runs.json").write_text(json.dumps({"schema": RA.REPEAT_SCHEMA, "runs": [rec]}), encoding="utf-8")
    return rows_of, bundle


def test_v020_accepts_a_predictions_file_with_its_recorded_hash(tmp_path):
    import hashlib
    rows_of, bundle = _hash_case(tmp_path, recorded=lambda data: hashlib.sha256(data).hexdigest())
    out = RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3, notes=RA20.REPEAT_NOTES, bytes_imply_labels=True,
                      verify_prediction_hashes=True)
    assert out["m00"]["r2"]["identical_labels"] is True


def test_v020_refuses_a_predictions_file_that_differs_from_its_recorded_hash(tmp_path):
    rows_of, bundle = _hash_case(tmp_path, recorded="0" * 64)
    with pytest.raises(RA.CheckError, match="predictions_sha256"):
        RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3, notes=RA20.REPEAT_NOTES, bytes_imply_labels=True,
                    verify_prediction_hashes=True)


def test_v020_refuses_a_run_with_no_recorded_hash_when_the_check_is_on(tmp_path):
    rows_of, bundle = _hash_case(tmp_path, recorded=None)
    with pytest.raises(RA.CheckError, match="predictions_sha256"):
        RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3, notes=RA20.REPEAT_NOTES, bytes_imply_labels=True,
                    verify_prediction_hashes=True)
    # without the flag (the v0.1.1 rule) the same run is read as before
    RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3, notes=RA20.REPEAT_NOTES, bytes_imply_labels=True)

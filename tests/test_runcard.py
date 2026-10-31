"""Run cards (mentat_bench.runcard): the identities, usage and scores of one cell, from its run directory alone."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from mentat_bench import engine, runcard

HARDWARE_KEYS = {"os", "machine", "cpu_model", "cpu_count", "ram_gb", "gpus", "gpu_count", "cuda"}


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))  # bytes: the hashes are of these bytes on every platform


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cell(tmp_path):
    """A complete cell directory with a fixture, a profile and the run files a cell has (no engine needed)."""
    fixture = tmp_path / "fixtures" / "toy"
    _write(fixture / "corpus.jsonl", '{"id": "a"}\n')
    _write(fixture / "queries.jsonl", '{"qid": "q1"}\n')
    _write(fixture / "fixture.json", json.dumps({"name": "toy", "version": "t1"}))
    _write(fixture / "gold" / "answers.jsonl", '{"qid": "q1"}\n')
    profile = tmp_path / "profile.json"
    _write(profile, json.dumps({"spend_ceiling_usd": 25.0}))
    run = tmp_path / "runs" / "cell"
    _write(run / "cell.json", json.dumps({"fixture": "toy", "fixture_path": str(fixture), "profile": str(profile),
                                          "adapter": "local", "status": "complete", "partial": [], "incomplete": False}))
    _write(run / "score.json", json.dumps({"Q_N": 91.0, "families": {f: {"mean_utility": 0.9} for f in
                                                                     ("closure", "selection", "feasibility", "partition")},
                                           "exact_query_success": 0.5, "false_entailment": 1, "false_refutation": 0,
                                           "weighted": {"Q_N": 90.0}, "anchor": {"status": "not_applicable"},
                                           "active": {"Q_N": 80.0}}))
    _write(run / "build_summary.json", json.dumps({"contradiction_rate": 0.1, "relations_asked": 10,
                                                   "relations_dropped": 1, "base_repaired": True}))
    _write(run / "submission" / "usage.json", json.dumps({"calls": [
        {"model": "m", "kind": "local", "tokens_in": 3, "tokens_out": 1, "cost_usd": 0.0, "gpu_seconds": 1.0,
         "seconds": 0.5, "items": 4},
        {"model": "m", "kind": "local", "tokens_in": 2, "tokens_out": 1, "cost_usd": 0.0, "gpu_seconds": 0.5,
         "seconds": 0.25}], "wall_seconds": 2.0, "peak_rss_mb": 100.0}))
    _write(run / "submission" / "build_policy.json", json.dumps({"adapter": "local", "model": {
        "id": "org/model", "revision": "abc", "kind": "local", "dtype": "bfloat16", "device": "cuda",
        "calibrate": True, "prefix_cache": False}}))
    seal_text = json.dumps({"digest": "d" * 64, "files": {"theory.json": "x"}})
    _write(run / "submission" / "seal.json", seal_text)
    _write(run / "seal_ledger.jsonl", json.dumps({"build_id": "b", "digest": "d" * 64, "sealed_at_utc": "2026-10-18T10:00:00+00:00",
                                                  "submission": str(run / "submission")}) + "\n")
    return run, fixture, profile


def test_the_card_records_the_identities_usage_and_scores(tmp_path):
    run, fixture, profile = _cell(tmp_path)
    card = runcard.runcard(run)
    assert card["schema"] == runcard.SCHEMA
    assert card["fixture"]["name"] == "toy" and card["fixture"]["version"] == "t1"
    assert card["fixture"]["files"]["corpus.jsonl"] == _sha('{"id": "a"}\n')
    assert card["fixture"]["files"]["gold/answers.jsonl"] == _sha('{"qid": "q1"}\n')
    assert card["profile"]["sha256"] == hashlib.sha256(profile.read_bytes()).hexdigest()
    assert card["profile"]["content"] == {"spend_ceiling_usd": 25.0}
    assert card["model"] == {"adapter": "local", "id": "org/model", "revision": "abc", "dtype": "bfloat16",
                             "calibrate": True, "prefix_cache": False}
    assert card["submission"]["digest"] == "d" * 64
    assert card["submission"]["sealed_at_utc"] == "2026-10-18T10:00:00+00:00"
    assert card["usage"]["calls"] == 2 and card["usage"]["items"] == 5
    assert card["usage"]["gpu_seconds"] == pytest.approx(1.5) and card["usage"]["wall_seconds"] == 2.0
    assert card["scores"]["Q_N_repaired"] == 91.0
    assert card["scores"]["Q_N_strict"] == 0.0  # the base was repaired: the strict score is 0 (spec s9)
    assert card["scores"]["weighted_Q_N"] == 90.0 and card["scores"]["active_Q_N"] == 80.0
    assert card["scores"]["anchor_Q_N"] == "not_applicable"
    assert card["status"]["status"] == "complete" and card["status"]["partial"] == []
    assert card["repairs"]["relations_dropped"] == 1 and card["repairs"]["base_repaired"] is True
    assert card["timestamps"]["cell_json_utc"] and card["timestamps"]["score_json_utc"]


V2_HEADLINE = {"metric": "relation_mcc", "relation_mcc": 0.8, "secondary": {"binding_mcc": 0.3},
               "diagnostics": {"group_mcc": 0.4, "reading_mcc": None}}
V3_HEADLINE = {"metric": "relation_group_mcc", "relation_group_mcc": 0.6, "parts": {"relation_mcc": 0.8, "group_mcc": 0.4},
               "secondary": {"binding_mcc": 0.3}, "diagnostics": {"reading_mcc": None}}
SCORE_KEYS = {"Q_N_repaired", "Q_N_strict", "base_repaired", "families", "exact_query_success", "false_entailment",
              "false_refutation", "weighted_Q_N", "anchor_Q_N", "active_Q_N", "decision_rel_mcc", "decision_bind_mcc",
              "decision_grp_mcc", "decision_read_mcc"}  # the keys of the pinned v0.2 run cards


def _decisions(run, headline):
    """A score_decisions.json with the given headline and the per-op MCCs the headline's parts are read from."""
    _write(run / "score_decisions.json", json.dumps({"headline": headline, "counts": {}, "ops": {
        "decide_relation": {"mcc": 0.8}, "decide_binding": {"mcc": 0.3}, "decide_group": {"mcc": 0.4},
        "decide_reading": {"mcc": None}}}))


def test_a_version_2_card_keeps_the_pinned_score_keys(tmp_path):
    run, _, _ = _cell(tmp_path)
    _decisions(run, V2_HEADLINE)
    assert set(runcard.runcard(run)["scores"]) == SCORE_KEYS  # no headline fields: the card bytes are the pinned ones
    run_none, _, _ = _cell(tmp_path / "no_decisions")
    assert set(runcard.runcard(run_none)["scores"]) == SCORE_KEYS


def test_a_version_3_card_carries_the_relation_group_headline(tmp_path):
    run, _, _ = _cell(tmp_path)
    _decisions(run, V3_HEADLINE)
    scores = runcard.runcard(run)["scores"]
    assert set(scores) == SCORE_KEYS | {"decision_headline_metric", "decision_headline"}
    assert scores["decision_headline_metric"] == "relation_group_mcc" and scores["decision_headline"] == pytest.approx(0.6)
    assert scores["decision_rel_mcc"] == 0.8 and scores["decision_grp_mcc"] == 0.4  # the parts stay on the card


def test_the_card_has_the_hardware_and_version_blocks(tmp_path):
    run, _, _ = _cell(tmp_path)
    card = runcard.runcard(run)
    assert HARDWARE_KEYS <= set(card["hardware"])
    assert card["hardware"]["gpu_count"] == len(card["hardware"]["gpus"])
    assert card["versions"]["python"]
    assert set(card["versions"]) >= {"python", "torch", "transformers"}


def test_the_engine_block_hashes_the_engine_binary(tmp_path, monkeypatch):
    run, _, _ = _cell(tmp_path)
    binary = tmp_path / "mb-engine.exe"
    binary.write_bytes(b"engine bytes")
    monkeypatch.setattr(engine, "engine_path", lambda: binary)
    block = runcard.runcard(run)["engine"]
    assert block["sha256"] == hashlib.sha256(b"engine bytes").hexdigest()
    assert block["mtime_utc"].endswith("+00:00")


def test_a_missing_seal_and_a_missing_engine_are_null(tmp_path, monkeypatch):
    run, _, _ = _cell(tmp_path)
    (run / "submission" / "seal.json").unlink()
    monkeypatch.setattr(engine, "engine_path", lambda: tmp_path / "absent.exe")
    card = runcard.runcard(run)
    assert card["submission"]["digest"] is None and card["submission"]["sealed_at_utc"] is None
    assert card["engine"]["sha256"] is None


def test_repo_identity_outside_a_repository_is_null(tmp_path):
    assert runcard.repo_identity(tmp_path) == {"commit": None, "dirty": None}


@pytest.mark.skipif(not engine.engine_available(), reason="mb-engine not found (set MB_ENGINE)")
def test_the_real_engine_binary_is_hashed(tmp_path):
    run, _, _ = _cell(tmp_path)
    assert runcard.runcard(run)["engine"]["sha256"] == runcard.sha256_file(engine.engine_path())


def test_main_writes_runcard_json_next_to_cell_json(tmp_path):
    run, _, _ = _cell(tmp_path)
    assert runcard.main(["--cell", str(run)]) == 0
    written = json.loads((run / "runcard.json").read_text(encoding="utf-8"))
    assert written["cell"].startswith("<external>/cell#")  # outside the repository: no local directory is written


def test_main_refuses_a_directory_without_a_cell(tmp_path):
    assert runcard.main(["--cell", str(tmp_path / "missing")]) == 2


def test_run_time_facts_are_used_when_runcell_recorded_them(tmp_path):
    run, _, _ = _cell(tmp_path)
    cell = json.loads((run / "cell.json").read_text(encoding="utf-8"))
    cell["runtime"] = {
        "captured_at": "run_time", "captured_utc": "2026-10-18T09:00:00+00:00",
        "engine": {"path": "target/release/mb-engine.exe", "sha256": "f" * 64, "mtime_utc": "2026-10-17T00:00:00+00:00"},
        "profile_sha256": "p" * 64, "fixture": {"files": {"corpus.jsonl": "c" * 64}},
        "repo": {"commit": "abc", "dirty": False}, "versions": {"python": "3.11.9"},
        "gpus": [{"index": 0, "name": "X", "uuid": "GPU-1", "memory_mb": 1.0, "driver": "d"}],
        "cuda": {"CUDA_VISIBLE_DEVICES": "0", "CUDA_DEVICE_ORDER": None},
    }
    _write(run / "cell.json", json.dumps(cell))
    card = runcard.runcard(run)
    assert card["engine"]["sha256"] == "f" * 64 and card["engine"]["captured_at"] == "run_time"
    assert card["repo"] == {"commit": "abc", "dirty": False, "captured_at": "run_time"}
    assert card["fixture"]["files"] == {"corpus.jsonl": "c" * 64} and card["fixture"]["captured_at"] == "run_time"
    assert card["profile"]["sha256"] == "p" * 64 and card["profile"]["changed_since_run"] is True
    assert card["hardware"]["gpus"][0]["uuid"] == "GPU-1" and card["hardware"]["cuda"]["CUDA_VISIBLE_DEVICES"] == "0"
    assert card["hardware"]["gpus_captured_at"] == "run_time"
    assert card["timestamps"]["captured_utc"] == "2026-10-18T09:00:00+00:00"


def test_without_run_time_facts_the_card_says_they_are_live(tmp_path, monkeypatch):
    run, _, _ = _cell(tmp_path)
    binary = tmp_path / "mb-engine.exe"
    binary.write_bytes(b"x")
    monkeypatch.setattr(engine, "engine_path", lambda: binary)
    card = runcard.runcard(run)
    assert card["engine"]["captured_at"] == "runcard_time" and card["engine"]["sha256"]
    assert card["fixture"]["captured_at"] == "runcard_time"
    assert card["hardware"]["gpus_captured_at"] == "runcard_time"
    assert card["profile"]["captured_at"] == "runcard_time"


def test_the_card_holds_no_local_directory(tmp_path):
    run, _, _ = _cell(tmp_path)
    card = runcard.runcard(run)
    text = json.dumps(card)
    assert str(tmp_path) not in text and str(Path.home()) not in text
    assert card["cell"].startswith("<external>/") and card["fixture"]["path"].startswith("<external>/")


def test_every_regular_file_under_the_fixture_is_pinned(tmp_path):
    run, fixture, _ = _cell(tmp_path)
    _write(fixture / "notes" / "extra.txt", "x")
    files = runcard.runcard(run)["fixture"]["files"]
    assert "notes/extra.txt" in files and "gold/answers.jsonl" in files and "fixture.json" in files


def test_the_seal_time_comes_from_the_central_ledger(tmp_path, monkeypatch):
    run, _, _ = _cell(tmp_path)
    ledger = tmp_path / "central.jsonl"
    ledger.write_text("not json\n" + json.dumps({"digest": "d" * 64, "sealed_at_utc": "2026-10-18T11:00:00+00:00"}) + "\n",
                      encoding="utf-8")
    monkeypatch.setenv("MB_SEAL_LEDGER", str(ledger))
    assert runcard.runcard(run)["submission"]["sealed_at_utc"] == "2026-10-18T11:00:00+00:00"


def test_gpus_are_listed_with_index_and_uuid(monkeypatch):
    csv = "0, NVIDIA A100, GPU-aaa, 40960, 535.1\n1, NVIDIA A100, GPU-bbb, 40960, 535.1"
    query = "--query-gpu=index,name,uuid,memory.total,driver_version"
    monkeypatch.setattr(runcard.shutil, "which", lambda name: "nvidia-smi")
    monkeypatch.setattr(runcard, "_run", lambda args, cwd=None: csv if query in args else None)
    found = runcard.gpus()
    assert [g["index"] for g in found] == [0, 1] and found[1]["uuid"] == "GPU-bbb" and found[0]["memory_mb"] == 40960.0


def test_the_cuda_environment_is_recorded(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    assert runcard.cuda_environment() == {"CUDA_VISIBLE_DEVICES": "", "CUDA_DEVICE_ORDER": "PCI_BUS_ID"}


def test_runcell_stores_the_run_time_facts_before_the_build(tmp_path, monkeypatch):
    """runcell writes the run-time facts into cell.json before it builds: the build here is stubbed to fail at once, and the
    cell still records the fixture's corpus hash and the captured_at marker."""
    from mentat_bench import runcell

    fixture = tmp_path / "fx"
    (fixture / "gold").mkdir(parents=True)
    _write(fixture / "corpus.jsonl", '{"id": "a"}\n')
    _write(fixture / "queries.jsonl", "")
    _write(fixture / "gold" / "answers.jsonl", "")  # a required file, so the cell can be fingerprinted
    profile = tmp_path / "profile.json"
    _write(profile, json.dumps({"spend_ceiling_usd": 25.0}))
    monkeypatch.setattr(runcell, "_captured", lambda argv: (3, ""))
    code = runcell.run_cell(fixture, "empty", [], tmp_path / "cell", profile, [])
    cell = json.loads((tmp_path / "cell" / "cell.json").read_text(encoding="utf-8"))
    assert code == 3
    assert cell["runtime"]["captured_at"] == "run_time"
    assert cell["runtime"]["fixture"]["files"]["corpus.jsonl"] == _sha('{"id": "a"}\n')
    assert cell["runtime"]["profile_sha256"] == hashlib.sha256(profile.read_bytes()).hexdigest()
    assert set(cell["runtime"]) >= {"engine", "repo", "versions", "gpus", "cuda", "captured_utc"}
    assert cell["query_files"] == ["queries.jsonl"]  # the file runcell passes to evaluate and score
    assert "fixture_stamp" in cell  # {name, version, ...} of a registered fixture, or {"error": ...} when none matches


def test_a_link_inside_the_repository_is_recorded_by_its_target(tmp_path, monkeypatch):
    """rel() resolves both sides: a symlink or junction inside the repository counts by where it points, so a link to a
    directory outside the repository is external; a path outside keeps "<external>/NAME"."""
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "events.parquet").write_bytes(b"x")
    monkeypatch.setattr(runcard, "REPO", repo)
    try:
        os.symlink(other, repo / "data", target_is_directory=True)
    except OSError:
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(repo / "data"), str(other)], capture_output=True, text=True)             if os.name == "nt" else None
        if done is None or done.returncode != 0:
            pytest.skip("cannot create a symlink or a junction here (needs privilege, or Windows mklink)")
    assert runcard.rel(repo / "data" / "events.parquet") == "<external>/events.parquet"
    assert runcard.rel(other / "events.parquet") == "<external>/events.parquet"


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows feature")
def test_a_junction_to_the_repository_is_inside_it_by_its_target(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "data" / "file.txt").write_text("x", encoding="utf-8")
    alias = tmp_path / "alias"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(repo)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("mklink /J is not available here")
    try:
        monkeypatch.setattr(runcard, "REPO", alias)  # the repository is named through the junction
        assert runcard.rel(repo / "data" / "file.txt") == "data/file.txt"
        assert runcard.rel(alias / "data" / "file.txt") == "data/file.txt"
        assert runcard.rel(tmp_path / "elsewhere.txt") == "<external>/elsewhere.txt"
    finally:
        os.rmdir(alias)  # removes the junction only; the target is untouched


def test_external_cell_names_carry_a_hash_of_their_directory(tmp_path, monkeypatch):
    """Two cells named alike in different directories outside the repository are different names (a short hash of the
    realpath), so no baseline row can match the other's card by mistake."""
    monkeypatch.setattr(runcard, "REPO", tmp_path / "repo")
    first = runcard.cell_ref(tmp_path / "a" / "empty")
    second = runcard.cell_ref(tmp_path / "b" / "empty")
    assert first.startswith("<external>/empty#") and second.startswith("<external>/empty#") and first != second
    assert runcard.cell_ref(tmp_path / "a" / "empty") == first  # stable for one directory
    assert runcard.cell_ref(tmp_path / "repo" / "runs" / "x") == "runs/x"  # inside the repository: the path, no hash

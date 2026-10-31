"""The public correction process (docs/corrections.md): the committed registry matches every file of the fixtures, a
drifted directory is refused, a corrected version supersedes the previous one with a change log, and a rescore applies
a corrected gold to a saved run (the query files and the recorded version of cell.json) without any model."""

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mentat_bench import cli, ir
from mentat_bench import corrections as c
from mentat_bench import engine, runcell
from mentat_bench import report as report_mod
from mentat_bench.manifest import DEFAULT_PROFILE

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"
needs_engine = pytest.mark.skipif(not engine.engine_available(), reason="mb-engine not built: set MB_ENGINE (rescore needs it)")


def _new_registry(tmp_path: Path, name: str = "VERSIONS.json") -> Path:
    """An empty registry file: register needs the file to exist (a missing registry is refused)."""
    path = tmp_path / name
    path.write_text('{"entries": []}\n', encoding="utf-8")
    return path


def _synthetic_fixture(dst: Path) -> Path:
    """A tiny valid fixture for registry and rescore tests; no released corpus payload is needed."""
    dst.mkdir(parents=True)
    gold = dst / "gold"
    gold.mkdir()
    main = [
        {"qid": "closure-01", "family": "closure", "hyp": [], "universe": []},
        {"qid": "selection-01", "family": "selection", "relation": "equiv", "target": {"op": "true"}, "universe": []},
        {"qid": "feasibility-01", "family": "feasibility", "assume": []},
        {"qid": "feasibility-02", "family": "feasibility", "assume": []},
        {"qid": "partition-01", "family": "partition", "members": [], "claim": "exclusive", "target": None},
    ]
    extra = [dict(q, qid=f"ext-{q['qid']}") for q in main]
    for filename, queries in (("queries.jsonl", main), ("queries_extra.jsonl", extra)):
        (dst / filename).write_text("".join(json.dumps(q) + "\n" for q in queries), encoding="utf-8")
    (dst / "corpus.jsonl").write_text("", encoding="utf-8")
    (dst / "fixture.json").write_text(json.dumps({"name": dst.name}) + "\n", encoding="utf-8")
    for suffix, queries in (("", main), ("_extra", extra)):
        answers = []
        for q in queries:
            answer = {"qid": q["qid"]}
            if q["family"] == "closure":
                answer["labels"] = {label: [] for label in ("ENTAILED", "REFUTED", "CONTINGENT", "UNRESOLVED")}
            elif q["family"] == "selection":
                answer["ids"] = []
            elif q["family"] == "partition":
                answer.update(decision="true", counterexample=None, missing=[])
            else:
                answer.update(decision="SAT", witness={})
            answers.append(answer)
        verdicts = [{"qid": q["qid"], "cert_valid": True, "reason": "ok"} for q in queries]
        (gold / f"answers{suffix}.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in answers), encoding="utf-8")
        (gold / f"verdicts{suffix}.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in verdicts), encoding="utf-8")
        (gold / f"query_design{suffix}.jsonl").write_text(
            "".join(json.dumps({"qid": q["qid"], "anchor": False}) + "\n" for q in queries), encoding="utf-8")
    ir.write_submission(gold, [], [], ["base"], {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0})
    return dst


def _set_decision(fixture: Path, qid: str, decision: str, witness=None, core=None, name: str = "answers.jsonl") -> None:
    """Rewrites the gold answer of one query (the correction)."""
    path = fixture / "gold" / name
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["qid"] == qid:
            row.update({"decision": decision, "witness": witness, "core": core})
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def _profile(tmp_path: Path) -> Path:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(DEFAULT_PROFILE, sort_keys=True), encoding="utf-8")
    return path


def _versions(tmp_path: Path):
    """Two tiny versions: v2 corrects feasibility-01 (SAT -> UNSAT)."""
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    _set_decision(v2, "feasibility-01", "UNSAT", core=[0, 1])
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    c.register(v2, "toy_synthetic", "2", "feasibility-01 gold corrected", ["feasibility-01"], registry=registry)
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy(v1 / "gold" / "answers.jsonl", run / "answers.jsonl")  # the run answered as the old gold did
    shutil.copy(v1 / "gold" / "verdicts.jsonl", run / "verdicts.jsonl")
    return v1, v2, registry, run


# ---- the registry and the directories it describes -------------------------------------------------------------------

EXPECTED_VERSIONS = sorted([
    ("toy10k", "1", "active"),
    ("mentat_hard_dev", "1", "superseded"), ("mentat_hard_dev", "2", "superseded"), ("mentat_hard_dev", "3", "active"),
    ("mentat_hard_test", "1", "superseded"), ("mentat_hard_test", "2", "superseded"), ("mentat_hard_test", "3", "active"),
])
# Registered content hashes that must never change: the committed and generated Mentat-Hard history.
FROZEN_CONTENT_HASHES = {
    ("toy10k", "1"): "140a0144e11f7050596b563b22992eb2de98e3d5834c96dfd75ce3546a2ad873",
    ("mentat_hard_dev", "1"): "7d23bcbdce729933934b26ceef9ba86a81422ec1827c7244ba0ca57bc44f804f",
    ("mentat_hard_test", "1"): "0907b4f88b90d9f6e5cd0c94ee7f542ee5219162bd70b5e95e61d49a1133520e",
    ("mentat_hard_dev", "2"): "09da49370c1f3830c39bcd0e9f47193766b2446c22a03190b144a26b7fccad55",
    ("mentat_hard_test", "2"): "1f4613bec957dd4de9b261b48241dbeeb3faf127a1a7686a6cf938b9a6aefc2e",
    ("mentat_hard_dev", "3"): "218003c63135ace753a1b2449ee002dc2813c7e99bed8c0956fd5a52187c3964",
    ("mentat_hard_test", "3"): "7b408459538d7420e808d5d81e71916c57aee78f459a6b8c0905e48e43c8e6ab",
}


def test_the_registered_content_hashes_never_change():
    by_key = {(e["name"], e["version"]): e for e in c.list_entries(c.REGISTRY)}
    assert set(FROZEN_CONTENT_HASHES) == set(by_key)
    for key, content_hash in FROZEN_CONTENT_HASHES.items():
        assert by_key[key]["content_hash"] == content_hash == c.content_hash(by_key[key]["hashes"]), key


def test_committed_registry_matches_every_file_of_each_active_fixture():
    entries = c.list_entries(c.REGISTRY)
    assert sorted((e["name"], e["version"], e["status"]) for e in entries) == EXPECTED_VERSIONS
    for entry in entries:
        # a generated version has a committed directory only when it was also built into fixtures/ (Mentat-Hard)
        if entry["status"] != "active" or ("generator" in entry and not (FIXTURES / entry["name"]).is_dir()):
            continue
        fixture = FIXTURES / entry["name"]
        assert c.content_hashes(fixture) == entry["hashes"], entry["name"]  # every regular file, not a fixed list
        assert c.stamp_for(fixture)["content_hash"] == entry["content_hash"]
    for name in ("mentat_hard_dev", "mentat_hard_test"):
        history = {e["version"]: e for e in entries if e["name"] == name}
        assert history["1"]["superseded_by"] == "2"
        assert history["2"]["superseded_by"] == "3"
        assert history["3"]["status"] == "active" and history["3"]["superseded_by"] is None


def test_stamp_refuses_a_directory_that_differs_from_its_version(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    assert c.stamp_for(v1, registry)["version"] == "1"
    theory = v1 / "gold" / "theory.json"
    original = theory.read_bytes()
    theory.write_bytes(original + b" ")  # a gold file the old hash covers, one byte changed
    with pytest.raises(c.CorrectionError, match=r"changed \['gold/theory.json'\]"):
        c.stamp_for(v1, registry)
    theory.write_bytes(original)
    assert c.stamp_for(v1, registry)["version"] == "1"  # back to the registered bytes
    (v1 / "gold" / "unexpected.json").write_text("{}", encoding="utf-8")
    with pytest.raises(c.CorrectionError, match=r"extra \['gold/unexpected.json'\]"):
        c.stamp_for(v1, registry)
    (v1 / "gold" / "unexpected.json").unlink()
    (v1 / "gold" / "answers.jsonl").unlink()
    with pytest.raises(c.CorrectionError, match=r"has no \['gold/answers.jsonl'\]"):
        c.stamp_for(v1, registry)


def test_register_supersedes_the_active_version_and_logs_the_change(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    _set_decision(v2, "feasibility-01", "UNSAT", core=[0, 1])
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry, date="2026-01-01")
    c.register(v2, "toy_synthetic", "2", "feasibility-01 was SAT but its witness breaks a premise", ["feasibility-01"],
               registry=registry, date="2026-02-01")
    first, second = c.list_entries(registry)
    assert (first["status"], first["superseded_by"]) == ("superseded", "2")
    assert (second["status"], second["superseded_by"]) == ("active", None)
    assert second["change_log"] == [{"date": "2026-02-01", "reason": "feasibility-01 was SAT but its witness breaks a premise",
                                     "affected_queries": ["feasibility-01"]}]
    assert c.stamp_for(v2, registry)["version"] == "2" and c.stamp_for(v1, registry)["status"] == "superseded"


def test_register_refuses_repeats_unknown_queries_and_unregistered_content(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    with pytest.raises(c.CorrectionError, match="already registered"):
        c.register(v1, "toy_synthetic", "1", "again", registry=registry)
    with pytest.raises(c.CorrectionError, match="already registered under another version"):
        c.register(v1, "toy_synthetic", "9", "same content", registry=registry)
    v2 = _synthetic_fixture(tmp_path / "v2")
    _set_decision(v2, "feasibility-01", "UNSAT", core=[0, 1])
    with pytest.raises(c.CorrectionError, match="not in"):
        c.register(v2, "toy_synthetic", "2", "fix", ["no-such-query"], registry=registry)
    with pytest.raises(c.CorrectionError, match="not a registered"):
        c.stamp_for(v2, registry)


def test_rehash_widens_the_hash_scope_but_refuses_content_changes(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    registry = _new_registry(tmp_path)
    entry = c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    doc = json.loads(registry.read_text(encoding="utf-8"))
    doc["entries"][0]["hashes"] = {k: v for k, v in entry["hashes"].items() if k in ("corpus.jsonl", "queries.jsonl")}
    doc["entries"][0]["content_hash"] = c.content_hash(doc["entries"][0]["hashes"])  # an old, narrower scope
    registry.write_text(json.dumps(doc), encoding="utf-8")
    widened = c.rehash(v1, "toy_synthetic", "1", "scope now covers every file", registry=registry, date="2026-03-01")
    assert widened["hashes"] == c.content_hashes(v1) and len(widened["change_log"]) == 2
    assert c.stamp_for(v1, registry)["content_hash"] == widened["content_hash"]
    _set_decision(v1, "feasibility-01", "UNSAT", core=[0, 1])
    with pytest.raises(c.CorrectionError, match="rehash changes the hash scope only"):
        c.rehash(v1, "toy_synthetic", "1", "content edit", registry=registry)


def test_list_prints_each_entry(tmp_path, capsys):
    registry = _new_registry(tmp_path)
    c.register(_synthetic_fixture(tmp_path / "v1"), "toy_synthetic", "1", "initial", registry=registry)
    assert c.main(["list", "--registry", str(registry)]) == 0
    assert capsys.readouterr().out.startswith("toy_synthetic v1 active ")


def test_committed_jsonl_files_end_with_a_newline():
    for path in sorted(FIXTURES.glob("*/**/*.jsonl")):
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            if fh.tell():
                fh.seek(-1, 2)
                assert fh.read(1) == b"\n", f"{path} has no final newline"


def test_concatenated_parts_stay_separate_lines(tmp_path):
    parts = [tmp_path / "a.jsonl", tmp_path / "b.jsonl", tmp_path / "c.jsonl"]
    parts[0].write_text('{"qid": "1"}\n', encoding="utf-8")
    parts[1].write_text('{"qid": "2"}', encoding="utf-8")  # no final newline
    parts[2].write_text('{"qid": "3"}\n', encoding="utf-8")
    out = c._concat(parts, tmp_path / "out.jsonl")
    assert [json.loads(line)["qid"] for line in out.read_text(encoding="utf-8").splitlines()] == ["1", "2", "3"]


# ---- rescore ----------------------------------------------------------------------------------------------------------

@needs_engine
def test_rescore_applies_the_corrected_gold_without_rerunning_a_model(tmp_path):
    v1, v2, registry, run = _versions(tmp_path)
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["fixture"]["to"]["version"] == "2" and report["fixture"]["from"]["version"] == "1"
    assert report["queries"] == ["queries.jsonl"]  # no cell.json: the default query file
    assert report["old"]["Q_N"] == pytest.approx(100.0)  # the run is the old gold itself: a perfect score under v1
    assert report["delta"]["Q_N"] < 0, report["delta"]
    assert [row["qid"] for row in report["changed_queries"]] == ["feasibility-01"]
    assert report["changed_queries"][0]["old"] == 1.0 and report["changed_queries"][0]["new"] < 1.0
    written = json.loads((run / "rescore_toy_synthetic_v2.json").read_text(encoding="utf-8"))
    assert written["delta"] == report["delta"]
    assert not (run / "rescore_work").exists()  # the scratch files are removed


@needs_engine
def test_rescore_old_score_of_toy_synthetic_equals_the_run_score_json(tmp_path, capsys):
    """A v2 run evaluated on queries.jsonl only (cell.json lists it): the old Q_N is the run's own score.json."""
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    _set_decision(v2, "feasibility-02", "UNSAT", core=[0, 1])  # SAT in v2 today
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    c.register(v2, "toy_synthetic", "2", "feasibility-02 gold corrected", ["feasibility-02"], registry=registry)
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy(v1 / "gold" / "answers.jsonl", run / "answers.jsonl")
    shutil.copy(v1 / "gold" / "verdicts.jsonl", run / "verdicts.jsonl")
    assert cli.main(["score", "--queries", str(v1 / "queries.jsonl"), "--gold-answers", str(v1 / "gold" / "answers.jsonl"),
                     "--answers", str(run / "answers.jsonl"), "--verdicts", str(run / "verdicts.jsonl")]) == 0
    score = json.loads(capsys.readouterr().out)
    (run / "score.json").write_text(json.dumps(score), encoding="utf-8")
    (run / "cell.json").write_text(json.dumps({"fixture_stamp": c.stamp_for(v1, registry), "query_files": ["queries.jsonl"]}),
                                   encoding="utf-8")
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["queries"] == ["queries.jsonl"]
    assert report["old"]["Q_N"] == pytest.approx(score["Q_N"]) and report["old"]["Q_N"] == pytest.approx(100.0)
    assert report["new"]["Q_N"] < report["old"]["Q_N"]


def _two_versions_with_a_corrected_main_query(tmp_path):
    """v1 and v2 of toy_synthetic (v2 corrects feasibility-02 in the main gold) and their registry."""
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    _set_decision(v2, "feasibility-02", "UNSAT", core=[0, 1])
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    c.register(v2, "toy_synthetic", "2", "feasibility-02 gold corrected", ["feasibility-02"], registry=registry)
    return v1, v2, registry


@needs_engine
def test_rescore_covers_every_query_file_the_cell_used(tmp_path):
    """cell.json lists queries.jsonl and queries_extra.jsonl: the main file is re-scored from answers.jsonl and the extra
    file from extra/, and both are reported."""
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    (run / "cell.json").write_text(json.dumps({"fixture_stamp": c.stamp_for(v1, registry),
                                               "query_files": ["queries.jsonl", "queries_extra.jsonl"]}), encoding="utf-8")
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["queries"] == ["queries.jsonl", "queries_extra.jsonl"]
    assert report["old"]["Q_N"] == pytest.approx(100.0) and report["new"]["Q_N"] < 100.0
    assert [row["qid"] for row in report["changed_queries"]] == ["feasibility-02"]
    assert report["extra"]["status"] == "rescored" and report["extra"]["new"]["Q_N"] == pytest.approx(100.0)


@needs_engine
def test_rescore_finds_the_extra_pass_of_a_cell_written_before_query_files_listed_it(tmp_path):
    """A cell.json that lists only queries.jsonl (written before the extra file was recorded) still re-scores extra/."""
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)  # cell.json lists queries.jsonl only
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["queries"] == ["queries.jsonl", "queries_extra.jsonl"]
    assert report["extra"]["status"] == "rescored" and report["extra"]["old"]["Q_N"] == pytest.approx(100.0)
    assert (run / "score_extra.json").is_file()


def _extra_run(tmp_path, v1, registry):
    """A run that answered the extra file as v1's gold does: extra/ answers and verdicts, a score_extra.json recorded
    under v1, and a cell.json with the recorded version."""
    run = tmp_path / "run"
    (run / "extra").mkdir(parents=True)
    for src, dst in (("answers.jsonl", "answers.jsonl"), ("verdicts.jsonl", "verdicts.jsonl")):
        shutil.copy(v1 / "gold" / src, run / dst)
    shutil.copy(v1 / "gold" / "answers_extra.jsonl", run / "extra" / "answers.jsonl")
    shutil.copy(v1 / "gold" / "verdicts_extra.jsonl", run / "extra" / "verdicts.jsonl")
    stamp = c.stamp_for(v1, registry)
    score = c._score(v1 / "queries_extra.jsonl", run / "extra" / "answers.jsonl", v1 / "gold" / "answers_extra.jsonl",
                     run / "extra" / "verdicts.jsonl")
    (run / "score_extra.json").write_text(json.dumps({**score, "fixture_stamp": stamp}), encoding="utf-8")
    (run / "cell.json").write_text(json.dumps({"fixture_stamp": stamp, "query_files": ["queries.jsonl"],
                                               "extra_status": "ok", "status": "complete"}), encoding="utf-8")
    return run


@needs_engine
def test_rescore_also_re_scores_the_extra_pass_into_score_extra(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    qid = next(a["qid"] for a in ir.read_jsonl(v1 / "gold" / "answers_extra.jsonl")
               if a["qid"].startswith("ext-feasibility") and a.get("decision") == "SAT")
    _set_decision(v2, qid, "UNSAT", core=[0, 1], name="answers_extra.jsonl")
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    c.register(v2, "toy_synthetic", "2", "extra feasibility gold corrected", [qid], registry=registry)
    run = _extra_run(tmp_path, v1, registry)
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    extra = report["extra"]
    assert extra["status"] == "rescored" and extra["queries"] == ["queries_extra.jsonl"]
    assert extra["old"]["Q_N"] == pytest.approx(100.0) and extra["new"]["Q_N"] < 100.0
    assert [row["qid"] for row in extra["changed_queries"]] == [qid]
    written = json.loads((run / "score_extra.json").read_text(encoding="utf-8"))
    assert written["fixture_stamp"]["version"] == "2" and written["Q_N"] == pytest.approx(extra["new"]["Q_N"])
    kept = json.loads((run / "score_extra_toy_synthetic_v1.json").read_text(encoding="utf-8"))  # the run's own copy
    assert kept["fixture_stamp"]["version"] == "1" and kept["Q_N"] == pytest.approx(100.0)
    row = report_mod.run_row(run, registry)  # the re-scored extra score is the active version: fresh, not stale
    assert row["extra_status"] == "ok" and row["extra_Q_N"] == pytest.approx(written["Q_N"])


@needs_engine
def test_rescore_refuses_only_the_extra_pass_when_queries_extra_changed(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    extra_queries = v2 / "queries_extra.jsonl"
    extra_queries.write_text(extra_queries.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # a different byte stream
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    c.register(v2, "toy_synthetic", "2", "queries_extra reformatted", registry=registry)
    run = _extra_run(tmp_path, v1, registry)
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["old"]["Q_N"] == pytest.approx(100.0)  # the main file is still re-scored
    assert report["extra"]["status"] == "refused" and "re-run the extra pass" in report["extra"]["reason"]
    assert json.loads((run / "score_extra.json").read_text(encoding="utf-8"))["fixture_stamp"]["version"] == "1"
    assert report_mod.run_row(run, registry)["extra_Q_N"] == "stale"  # the score the run recorded is not the active one


def test_rescore_refuses_when_queries_changed_and_unregistered_versions(tmp_path):
    v1, v2, registry, run = _versions(tmp_path)
    v3 = _synthetic_fixture(tmp_path / "v3")
    queries = v3 / "queries.jsonl"
    queries.write_text(queries.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # a different byte stream
    c.register(v3, "toy_synthetic", "3", "queries reformatted", registry=registry)
    with pytest.raises(c.CorrectionError, match="saved answers do not apply"):
        c.rescore(run, v3, v1, _profile(tmp_path), registry=registry)
    unregistered = _synthetic_fixture(tmp_path / "unregistered")
    _set_decision(unregistered, "feasibility-02", "SAT", witness={})
    assert c.main(["rescore", "--run", str(run), "--fixture", str(unregistered), "--from", str(v1),
                   "--profile", str(_profile(tmp_path)), "--registry", str(registry)]) == 2


def test_rescore_refuses_a_from_directory_other_than_the_recorded_version(tmp_path):
    v1, v2, registry, run = _versions(tmp_path)
    (run / "cell.json").write_text(json.dumps({"fixture_stamp": c.stamp_for(v2, registry)}), encoding="utf-8")
    with pytest.raises(c.CorrectionError, match="not the version the run recorded"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)


def test_rescore_refuses_query_file_names_that_are_paths(tmp_path):
    v1, v2, registry, run = _versions(tmp_path)
    (run / "cell.json").write_text(json.dumps({"query_files": ["../queries.jsonl"]}), encoding="utf-8")
    with pytest.raises(c.CorrectionError, match="plain file names"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)


def _set_cell(run, **fields):
    cell = json.loads((run / "cell.json").read_text(encoding="utf-8"))
    cell.update(fields)
    (run / "cell.json").write_text(json.dumps(cell), encoding="utf-8")


@needs_engine
def test_rescore_refuses_an_extra_pass_that_did_not_finish_ok_and_leaves_its_score(tmp_path):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    _set_cell(run, extra_status="failed")
    before = (run / "score_extra.json").read_bytes()
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["extra"]["status"] == "refused" and "did not finish ok" in report["extra"]["reason"]
    assert (run / "score_extra.json").read_bytes() == before and not (run / "score_extra_toy_synthetic_v1.json").exists()


@needs_engine
def test_rescore_refuses_an_unreadable_score_extra_and_never_overwrites_it(tmp_path):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    (run / "score_extra.json").write_text("{not json", encoding="utf-8")
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["extra"]["status"] == "refused" and "not a readable JSON object" in report["extra"]["reason"]
    assert (run / "score_extra.json").read_text(encoding="utf-8") == "{not json"


@needs_engine
def test_rescore_takes_a_cell_without_extra_status_as_ok_when_its_score_is_there(tmp_path):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    cell = json.loads((run / "cell.json").read_text(encoding="utf-8"))
    del cell["extra_status"]  # written before extra_status was recorded
    (run / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["extra"]["status"] == "rescored"


@needs_engine
def test_rescore_refuses_a_legacy_extra_pass_whose_score_is_missing(tmp_path):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    cell = json.loads((run / "cell.json").read_text(encoding="utf-8"))
    del cell["extra_status"]
    (run / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    (run / "score_extra.json").unlink()
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["extra"]["status"] == "refused" and not (run / "score_extra.json").exists()


@needs_engine
def test_rescore_drops_the_extra_file_from_the_names_when_the_run_has_no_extra_answers(tmp_path):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    for name in ("answers", "verdicts"):
        shutil.copy(v1 / "gold" / f"{name}.jsonl", run / f"{name}.jsonl")
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry,
                       queries_files=["queries.jsonl", "queries_extra.jsonl"])
    assert report["queries"] == ["queries.jsonl"] and report["extra"] is None
    assert report["old"]["Q_N"] == pytest.approx(100.0)


@needs_engine
def test_an_extra_engine_failure_is_a_refused_extra_block_and_the_main_rescore_still_completes(tmp_path, monkeypatch):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    real_pass = c._score_pass

    def extra_check_fails(work, tag, *args):
        if tag == "extra_":
            raise c.engine.EngineError("mb-engine check failed on the extra file")
        return real_pass(work, tag, *args)

    monkeypatch.setattr(c, "_score_pass", extra_check_fails)
    before = (run / "score_extra.json").read_bytes()
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["extra"]["status"] == "refused" and "extra re-score failed" in report["extra"]["reason"]
    assert report["new"]["Q_N"] < report["old"]["Q_N"]  # the main file was re-scored
    assert (run / "score_extra.json").read_bytes() == before
    assert not (run / "rescore_work").exists()


def test_rescore_refuses_queries_extra_named_alone(tmp_path):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    with pytest.raises(c.CorrectionError, match="cannot be re-scored alone"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry=registry, queries_files=["queries_extra.jsonl"])


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@needs_engine
def test_rerun_extra_scores_the_corrected_extra_gold_into_a_new_directory_only(tmp_path, capsys, monkeypatch):
    """Re-run a saved extra pass against corrected gold into a new directory only."""
    run = tmp_path / "run"
    profile = _profile(tmp_path)
    old = _synthetic_fixture(tmp_path / "v3")
    new = _synthetic_fixture(tmp_path / "v4")
    qid = next(a["qid"] for a in ir.read_jsonl(new / "gold" / "answers_extra.jsonl")
               if a["qid"].startswith("ext-feasibility") and a.get("decision") == "SAT")
    _set_decision(new, qid, "UNSAT", core=[0, 1], name="answers_extra.jsonl")
    registry = _new_registry(tmp_path)
    c.register(old, "toy_synthetic", "3", "content of the committed v3", registry=registry)
    c.register(new, "toy_synthetic", "4", "extra feasibility gold corrected", [qid], registry=registry)
    monkeypatch.setattr(c, "REGISTRY", registry)
    assert runcell.run_cell(old, "oracle", [], run, profile, [], extra_queries=True) == 0
    before = _tree(run)
    assert json.loads((run / "score_extra.json").read_text(encoding="utf-8"))["Q_N"] == pytest.approx(100.0)

    report = c.rerun_extra(run, new, old, profile, registry=registry)
    target = run / "extra_toy_synthetic_v4"
    assert report["dir"] == str(target) and report["new"]["Q_N"] < 100.0
    scored = json.loads((target / "score_extra.json").read_text(encoding="utf-8"))
    assert scored["fixture_stamp"]["version"] == "4" and scored["per_query"][qid] < 1.0
    assert (target / "answers.jsonl").is_file() and (target / "verdicts.jsonl").is_file()
    after = _tree(run)
    assert {k: v for k, v in after.items() if not k.startswith("extra_toy_synthetic_v4/")} == before  # nothing else touched
    assert set(after) - set(before) == {f"extra_toy_synthetic_v4/{name}" for name in
                                        ("answers.jsonl", "verdicts.jsonl", "score_extra.json")}

    with pytest.raises(c.CorrectionError, match="never replaces one"):
        c.rerun_extra(run, new, old, profile, registry=registry)
    assert c.main(["rescore", "--run", str(run), "--fixture", str(new), "--from", str(old), "--profile", str(profile),
                   "--registry", str(registry), "--rerun-extra"]) == 2
    assert "never replaces one" in capsys.readouterr().err
    assert _tree(run) == after  # the refused second run wrote nothing


@needs_engine
def test_rerun_extra_refuses_a_corpus_change_and_an_unregistered_run_version(tmp_path, monkeypatch):
    run = tmp_path / "run"
    profile = _profile(tmp_path)
    old = _synthetic_fixture(tmp_path / "v3")
    new = _synthetic_fixture(tmp_path / "v4")
    corpus = new / "corpus.jsonl"
    corpus.write_text(corpus.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # a different corpus byte stream
    registry = _new_registry(tmp_path)
    c.register(old, "toy_synthetic", "3", "content of the committed v3", registry=registry)
    c.register(new, "toy_synthetic", "4", "corpus reformatted", registry=registry)
    monkeypatch.setattr(c, "REGISTRY", registry)
    assert runcell.run_cell(old, "oracle", [], run, profile, [], extra_queries=True) == 0
    with pytest.raises(c.CorrectionError, match="corpus.jsonl differs"):
        c.rerun_extra(run, new, old, profile, registry=registry)
    assert not (run / "extra_toy_synthetic_v4").exists()


def test_a_name_and_a_version_must_be_plain_directory_name_parts(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    registry = _new_registry(tmp_path)
    for name, version in (("toy/native", "1"), ("toy_synthetic", "../1"), ("toy native", "1"), ("toy_synthetic", "1 2")):
        with pytest.raises(c.CorrectionError, match="use only"):
            c.register(v1, name, version, "reason", registry=registry)
    assert c.list_entries(registry) == []  # nothing was written


def test_rerun_extra_needs_a_cell_json_and_an_extra_pass_that_finished_ok(tmp_path):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    _set_cell(run, extra_status="failed")
    with pytest.raises(c.CorrectionError, match="did not finish ok"):
        c.rerun_extra(run, v2, v1, _profile(tmp_path), registry=registry)
    (run / "cell.json").unlink()
    with pytest.raises(c.CorrectionError, match="has no cell.json"):
        c.rerun_extra(run, v2, v1, _profile(tmp_path), registry=registry)


def test_rerun_extra_cannot_be_combined_with_queries_file(tmp_path, capsys):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    assert c.main(["rescore", "--run", str(run), "--fixture", str(v2), "--from", str(v1), "--profile",
                   str(_profile(tmp_path)), "--registry", str(registry), "--rerun-extra", "--queries-file",
                   "queries.jsonl"]) == 2
    assert "cannot be combined with --rerun-extra" in capsys.readouterr().err
    assert not (run / "extra_toy_synthetic_v2").exists()


def test_a_failed_rerun_leaves_no_trace_and_evaluate_writes_inside_the_new_directory(tmp_path, monkeypatch):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)
    (run / "submission").mkdir()  # a real run has its sealed submission here; verify_seal is faked below
    before = _tree(run)
    seen = {}

    def fake_cli(argv, step):  # evaluate writes its answers and verdicts; score then fails
        if step == "evaluate":
            out = Path(argv[argv.index("--out") + 1])
            seen["out"] = out
            out.mkdir()
            (out / "answers.jsonl").write_text("", encoding="utf-8")
            (out / "verdicts.jsonl").write_text("", encoding="utf-8")
            return ""
        raise c.CorrectionError("score boom")

    monkeypatch.setattr(c, "_run_cli", fake_cli)
    monkeypatch.setattr(c, "verify_seal", lambda *args, **kwargs: {"digest": "0" * 64})
    with pytest.raises(c.CorrectionError, match="score boom"):
        c.rerun_extra(run, v2, v1, _profile(tmp_path), registry=registry)
    assert seen["out"].parent == run / "extra_toy_synthetic_v2"  # evaluate's temp dir is inside the new directory
    assert not (run / "extra_toy_synthetic_v2").exists()  # the failed call removed its directory
    assert _tree(run) == before


def test_rescore_refuses_to_replace_an_existing_report(tmp_path):
    v1, v2, registry, run = _versions(tmp_path)
    report = run / "rescore_toy_synthetic_v2.json"
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(c.CorrectionError, match="rescore_toy_synthetic_v2.json exists"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report.read_text(encoding="utf-8") == "{}"  # nothing was written


def test_rescore_never_removes_a_rescore_work_dir_it_did_not_make(tmp_path, monkeypatch):
    v1, v2, registry, run = _versions(tmp_path)
    kept = run / "rescore_work"
    kept.mkdir()
    (kept / "keep.txt").write_text("x", encoding="utf-8")

    def score_fails(*args, **kwargs):
        raise c.CorrectionError("score boom")

    monkeypatch.setattr(c, "_score_pass", score_fails)
    with pytest.raises(c.CorrectionError, match="score boom"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert (kept / "keep.txt").read_text(encoding="utf-8") == "x"  # the existing dir is untouched
    assert not list(run.glob("rescore_work_*"))  # the scratch dir this call made is removed


def test_rescore_refuses_a_registered_name_that_is_not_a_plain_file_name_part(tmp_path):
    v1, v2, registry, run = _versions(tmp_path)
    registry.write_text(registry.read_text(encoding="utf-8").replace('"name": "toy_synthetic"', '"name": "toy native"'),
                        encoding="utf-8")
    with pytest.raises(c.CorrectionError, match="use only"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)


@needs_engine
def test_rescore_with_queries_file_queries_only_leaves_score_extra_alone(tmp_path):
    v1, v2, registry = _two_versions_with_a_corrected_main_query(tmp_path)
    run = _extra_run(tmp_path, v1, registry)  # the run has an extra pass, which the names below do not ask for
    before = (run / "score_extra.json").read_bytes()
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry, queries_files=["queries.jsonl"])
    assert report["queries"] == ["queries.jsonl"] and report["extra"] is None
    assert report["new"]["Q_N"] < 100.0
    assert (run / "score_extra.json").read_bytes() == before and not (run / "score_extra_toy_synthetic_v1.json").exists()


def test_rerun_extra_refuses_a_fixture_name_other_than_the_recorded_one(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    _set_decision(v2, "feasibility-02", "UNSAT", core=[0, 1])
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    c.register(v2, "other_fixture", "1", "registered under another name", ["feasibility-02"], registry=registry)
    run = _extra_run(tmp_path, v1, registry)  # recorded as toy_synthetic v1
    before = _tree(run)
    with pytest.raises(c.CorrectionError, match="recorded fixture 'toy_synthetic'"):
        c.rerun_extra(run, v2, v1, _profile(tmp_path), registry=registry)
    assert _tree(run) == before and not list(run.glob("extra_*"))


def test_an_unreadable_directory_is_a_correction_error_not_a_traceback(tmp_path, monkeypatch):
    fixture = _synthetic_fixture(tmp_path / "v1")

    def walk(top, onerror=None, **kwargs):
        onerror(PermissionError(13, "Permission denied", str(top)))
        return iter(())

    monkeypatch.setattr(c.os, "walk", walk)
    with pytest.raises(c.CorrectionError, match="cannot read"):
        c.content_hashes(fixture)


# ---- generated versions: the manifest, the generator whitelist and the regenerate check ---------------------------------
# These tests whitelist a tiny test-only generator (the toygen fixture).

GEN = "mb_test_toygen"
TOYGEN_SOURCE = """import json
from pathlib import Path

SEED = 7
REQUIRED_FILES = ("corpus.jsonl", "queries.jsonl", "fixture.json", "gold/answers.jsonl", "gold/extra.json")


def generate(out, *, name, seed):
    out = Path(out)
    (out / "gold").mkdir(parents=True)
    (out / "fixture.json").write_text(json.dumps({"name": name, "seed": seed}) + "\\n", encoding="utf-8")
    (out / "corpus.jsonl").write_text("".join(json.dumps({"id": f"x:{seed}:{i}"}) + "\\n" for i in range(3)), encoding="utf-8")
    (out / "queries.jsonl").write_text(json.dumps({"qid": "q1"}) + "\\n", encoding="utf-8")
    (out / "gold" / "answers.jsonl").write_text(json.dumps({"qid": "q1", "seed": seed}) + "\\n", encoding="utf-8")
    (out / "gold" / "extra.json").write_text("{}\\n", encoding="utf-8")
"""


@pytest.fixture
def toygen(tmp_path_factory, monkeypatch):
    """A deterministic generator module, whitelisted for the test (its source dir is the code identity's CODE_DIR)."""
    code = tmp_path_factory.mktemp("toygen")
    (code / f"{GEN}.py").write_text(TOYGEN_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(code))
    monkeypatch.delitem(sys.modules, GEN, raising=False)
    monkeypatch.setitem(c.GENERATORS, GEN, (GEN,))
    monkeypatch.setitem(c.GENERATOR_PARAMS, GEN, ("seed",))
    monkeypatch.setattr(c, "CODE_DIR", code)
    return importlib.import_module(GEN)


def _generated(tmp_path: Path, name: str = "toy_gen") -> Path:
    """Registers a generated version in a temporary registry (its manifest goes beside it); returns the registry."""
    registry = _new_registry(tmp_path)
    c.register_generated(GEN, None, name, "1", "test registration", registry=registry, date="2026-10-27")
    return registry


def _edit_registry(registry: Path, name: str, edit) -> None:
    doc = json.loads(registry.read_text(encoding="utf-8"))
    edit(next(e for e in doc["entries"] if e["name"] == name))
    registry.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _changed_output(toygen, monkeypatch) -> None:
    """Makes the generator write output that differs from the registered bytes."""
    real = toygen.generate

    def changed(out, **kwargs):
        real(out, **kwargs)
        with open(Path(out) / "corpus.jsonl", "a", encoding="utf-8") as fh:
            fh.write('{"id": "extra"}\n')

    monkeypatch.setattr(toygen, "generate", changed)


def test_register_writes_only_the_registry_and_a_manifest(tmp_path, toygen):
    registry = _generated(tmp_path)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()) == [
        "VERSIONS.json", "manifests/toy_gen_v1.json"]
    entry = c.list_entries(registry)[0]
    assert entry["generator"] == {"module": GEN, "params": {"seed": 7}, "code_sha256": c._code_sha256(GEN),
                                  "manifest": "manifests/toy_gen_v1.json"}


def test_register_then_regenerate_round_trips_identical_bytes_and_stamps_the_version(tmp_path, toygen):
    registry = _generated(tmp_path)
    entry = c.list_entries(registry)[0]
    first = Path(c.regenerate("toy_gen", "1", tmp_path / "a", registry)["dir"])
    second = Path(c.regenerate("toy_gen", "1", tmp_path / "b", registry)["dir"])
    assert _tree(first) == _tree(second)
    assert c.content_hashes(first, registry) == entry["hashes"]
    stamp = c.stamp_for(first, registry)
    assert stamp == {"name": "toy_gen", "version": "1", "status": "active", "content_hash": entry["content_hash"]}


def test_a_generated_directory_needs_its_generators_required_files(tmp_path, toygen):
    registry = _generated(tmp_path)
    out = Path(c.regenerate("toy_gen", "1", tmp_path / "out", registry)["dir"])
    (out / "gold" / "extra.json").unlink()  # not in the generic REQUIRED: the generator's REQUIRED_FILES name it
    with pytest.raises(c.CorrectionError, match="extra.json"):
        c.content_hashes(out, registry)


def test_a_tampered_manifest_refuses_and_leaves_no_output(tmp_path, toygen):
    registry = _generated(tmp_path)
    manifest = tmp_path / "manifests" / "toy_gen_v1.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    doc["files"]["corpus.jsonl"] = "0" * 64
    doc["content_hash"] = c.content_hash(doc["files"])
    manifest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(c.CorrectionError, match="does not match the registry entry"):  # the registry still has the true hashes
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)
    assert not (tmp_path / "out").exists()

    def follow(entry: dict) -> None:  # the registry follows the tampered manifest: the regenerated bytes still differ
        entry["hashes"]["corpus.jsonl"] = "0" * 64
        entry["content_hash"] = c.content_hash(entry["hashes"])

    _edit_registry(registry, "toy_gen", follow)
    with pytest.raises(c.CorrectionError, match=r"differs from its manifest: changed \['corpus.jsonl'\]"):
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)
    assert not (tmp_path / "out").exists()


def test_a_registry_content_hash_that_does_not_match_its_files_refuses(tmp_path, toygen):
    registry = _generated(tmp_path)
    _edit_registry(registry, "toy_gen", lambda e: e.__setitem__("content_hash", "1" * 64))
    with pytest.raises(c.CorrectionError, match="does not match the registry entry"):
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)
    assert not (tmp_path / "out").exists()


def test_a_non_whitelisted_module_refuses_to_register_or_regenerate(tmp_path, toygen):
    with pytest.raises(c.CorrectionError, match="not whitelisted"):
        c.register_generated("os", None, "toy_x", "1", "r", registry=tmp_path / "VERSIONS.json")
    assert not (tmp_path / "manifests").exists() and not (tmp_path / "VERSIONS.json").exists()
    registry = _generated(tmp_path)
    _edit_registry(registry, "toy_gen", lambda e: e["generator"].__setitem__("module", "os"))
    with pytest.raises(c.CorrectionError, match="not whitelisted"):
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)
    assert not (tmp_path / "out").exists()


def test_regenerate_refuses_params_outside_the_generator_contract(tmp_path, toygen):
    registry = _generated(tmp_path)
    _edit_registry(registry, "toy_gen", lambda e: e["generator"]["params"].__setitem__("extra", "x"))
    with pytest.raises(c.CorrectionError, match="generator params"):
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)


@pytest.mark.parametrize("params", [
    {"seed": True}, {"seed": 1, "split": False}, {"seed": 1, "split": 1.5}, {"split": "dev"},
    {"seed": 1, "split": "dev", "extra": 1}, None,
], ids=["bool-seed", "bool-value", "float-value", "no-seed", "extra-key", "not-an-object"])
def test_checked_params_rejects_bools_floats_and_keys_outside_the_contract(params, monkeypatch):
    monkeypatch.setitem(c.GENERATOR_PARAMS, GEN, ("seed", "split"))
    assert c._checked_params({"seed": 1, "split": "dev"}, GEN) == {"seed": 1, "split": "dev"}
    with pytest.raises(c.CorrectionError, match="generator params"):
        c._checked_params(params, GEN)


def test_a_changed_generator_source_with_identical_output_regenerates_with_a_warning(tmp_path, toygen):
    registry = _generated(tmp_path)
    source = c.CODE_DIR / f"{GEN}.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    assert c._code_sha256(GEN) != c.list_entries(registry)[0]["generator"]["code_sha256"]
    result = c.regenerate("toy_gen", "1", tmp_path / "out", registry)  # the output is still the manifest's
    assert len(result["warnings"]) == 1 and "differs from the code" in result["warnings"][0]
    assert result["stamp"]["status"] == "active"


def test_a_generator_that_changes_the_output_refuses_regeneration(tmp_path, toygen, monkeypatch):
    registry = _generated(tmp_path)
    _changed_output(toygen, monkeypatch)
    with pytest.raises(c.CorrectionError, match=r"differs from its manifest: changed \['corpus.jsonl'\]"):
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)
    assert not (tmp_path / "out").exists()


def test_a_failed_removal_is_reported_and_the_output_is_left_in_place(tmp_path, toygen, monkeypatch):
    registry = _generated(tmp_path)
    _changed_output(toygen, monkeypatch)
    monkeypatch.setattr(cli, "_remove_tree", lambda path: None)  # the removal fails, as on a locked directory
    with pytest.raises(c.CorrectionError, match="could not be removed and is left in place"):
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)
    assert (tmp_path / "out").is_dir()


def test_a_generator_runtime_error_is_refused_and_leaves_no_output(tmp_path, toygen, monkeypatch):
    registry = _generated(tmp_path)

    def generate_fails(out, **kwargs):
        Path(out).mkdir(parents=True)
        raise RuntimeError("generator boom")

    monkeypatch.setattr(toygen, "generate", generate_fails)
    with pytest.raises(c.CorrectionError, match="generator boom"):
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)
    assert not (tmp_path / "out").exists()
    with pytest.raises(c.CorrectionError, match="generator boom"):
        c.register_generated(GEN, None, "toy_other", "1", "r", registry=registry)
    assert [e["name"] for e in c.list_entries(registry)] == ["toy_gen"]
    assert not (tmp_path / "manifests" / "toy_other_v1.json").exists()


def test_generated_registration_and_regeneration_through_the_cli(tmp_path, toygen, capsys):
    registry = str(_new_registry(tmp_path))
    base = ["--registry", registry, "--name", "toy_cli", "--version", "1", "--reason", "cli"]
    assert c.main(["register", "--generator", GEN, "--seed", "3", *base]) == 0
    assert "generated" in capsys.readouterr().out
    assert c.list_entries(registry)[0]["generator"]["params"] == {"seed": 3}
    assert c.main(["register", "--generator", GEN, "--fixture", str(tmp_path), *base]) == 2
    assert c.main(["register", "--generator", GEN, "--param", "split=dev", "--name", "toy_cli", "--version", "2",
                   "--reason", "cli", "--registry", registry]) == 2  # outside the contract
    assert "generator params" in capsys.readouterr().err
    out = tmp_path / "regen"
    assert c.main(["regenerate", "toy_cli", "--version", "1", "--out", str(out), "--registry", registry]) == 0
    assert "regenerated toy_cli v1" in capsys.readouterr().out
    assert c.main(["regenerate", "toy_cli", "--version", "1", "--out", str(out), "--registry", registry]) == 2
    assert "exists" in capsys.readouterr().err


def test_a_failed_registry_write_leaves_no_manifest(tmp_path, toygen, monkeypatch):
    registry = _new_registry(tmp_path)

    def failing_save(path, doc):  # the registry is written last: its failure must not leave a manifest behind
        raise OSError("registry write failed")

    monkeypatch.setattr(c, "_save", failing_save)
    with pytest.raises(OSError, match="registry write failed"):
        c.register_generated(GEN, None, "toy_b", "1", "reason", registry=registry)
    assert not (tmp_path / "manifests" / "toy_b_v1.json").exists()
    assert c.list_entries(registry) == []


def test_the_manifest_is_in_place_before_the_registry_entry_is_written(tmp_path, toygen, monkeypatch):
    seen = {}
    real_register = c.register

    def spy(*args, **kwargs):
        seen["manifest_in_place"] = (tmp_path / "manifests" / "toy_gen_v1.json").is_file()
        return real_register(*args, **kwargs)

    monkeypatch.setattr(c, "register", spy)
    _generated(tmp_path)
    assert seen == {"manifest_in_place": True}


@pytest.mark.parametrize("mutate", [
    lambda doc: doc.__setitem__("name", "other"),
    lambda doc: doc.__setitem__("version", "2"),
    lambda doc: doc["generator"]["params"].__setitem__("seed", 8),
    lambda doc: doc["generator"].__setitem__("module", "os"),
], ids=["name", "version", "seed", "module"])
def test_regenerate_refuses_a_manifest_whose_fields_differ_from_the_registry(tmp_path, toygen, mutate):
    registry = _generated(tmp_path)
    manifest = tmp_path / "manifests" / "toy_gen_v1.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    mutate(doc)
    manifest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(c.CorrectionError, match="does not match the registry entry"):
        c.regenerate("toy_gen", "1", tmp_path / "out", registry)
    assert not (tmp_path / "out").exists()


def test_regenerate_refuses_an_output_inside_the_repository_fixtures(tmp_path, toygen):
    registry = _generated(tmp_path)
    target = c.REGISTRY.parent / "toy_gen_scratch_refused"
    with pytest.raises(c.CorrectionError, match="is inside"):
        c.regenerate("toy_gen", "1", target, registry)
    assert not target.exists()


def test_rehash_refuses_a_generated_version(tmp_path, toygen):
    registry = _generated(tmp_path)
    with pytest.raises(c.CorrectionError, match="is generated"):
        c.rehash(tmp_path, "toy_gen", "1", "a reason", registry=registry)


def test_a_name_holds_committed_or_generated_versions_but_never_both(tmp_path, toygen):
    registry = _generated(tmp_path)  # toy_gen v1 is generated
    committed = _synthetic_fixture(tmp_path / "committed")
    with pytest.raises(c.CorrectionError, match="is a generated fixture name"):
        c.register(committed, "toy_gen", "2", "a committed version", registry=registry)
    c.register(committed, "toy_synthetic", "1", "initial", registry=registry)
    with pytest.raises(c.CorrectionError, match="is a committed fixture name"):
        c.register_generated(GEN, None, "toy_synthetic", "2", "a generated version", registry=registry)
    assert not (tmp_path / "manifests" / "toy_synthetic.json").exists()  # refused before anything is written
    assert [(e["name"], e["version"]) for e in c.list_entries(registry)] == [("toy_gen", "1"), ("toy_synthetic", "1")]


def test_a_generated_name_keeps_one_module_across_its_versions(tmp_path, toygen):
    registry = _generated(tmp_path)
    _edit_registry(registry, "toy_gen", lambda e: e["generator"].__setitem__("module", "mb_other_gen"))
    with pytest.raises(c.CorrectionError, match="must use it"):
        c.register_generated(GEN, {"seed": 8}, "toy_gen", "2", "another module", registry=registry)
    assert not (tmp_path / "manifests" / "toy_gen_v2.json").exists()


def test_an_orphan_manifest_is_replaced_but_a_registered_manifest_never_is(tmp_path, toygen):
    registry = _new_registry(tmp_path)
    orphan = tmp_path / "manifests" / "toy_orphan_v1.json"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("{crash leftover", encoding="utf-8")  # written before a crash, with no registry entry
    c.register_generated(GEN, None, "toy_orphan", "1", "after a crash", registry=registry)
    assert json.loads(orphan.read_text(encoding="utf-8"))["name"] == "toy_orphan"
    registered = orphan.read_bytes()
    with pytest.raises(c.CorrectionError, match="never replaced"):
        c.register_generated(GEN, {"seed": 8}, "toy_orphan", "1", "again", registry=registry)
    assert orphan.read_bytes() == registered and len(c.list_entries(registry)) == 1


def test_rescore_refuses_a_query_file_with_no_rows(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    for fixture in (v1, v2):
        (fixture / "queries.jsonl").write_text("", encoding="utf-8")
    _set_decision(v2, "feasibility-01", "UNSAT", core=[0, 1])
    # register refuses an empty queries.jsonl, so the entries are written as they would be without that check
    entries = []
    for version, fixture, status, superseded in (("1", v1, "superseded", "2"), ("2", v2, "active", None)):
        hashes = c.content_hashes(fixture, [])
        entries.append({"name": "toy_empty", "version": version, "status": status, "superseded_by": superseded,
                        "content_hash": c.content_hash(hashes), "hashes": hashes,
                        "change_log": [{"date": "2026-10-23", "reason": "test", "affected_queries": []}]})
    registry = tmp_path / "VERSIONS.json"
    registry.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    (run / "answers.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(c.CorrectionError, match="has no rows"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry)


def _set_entry(doc: dict, **fields) -> None:
    doc["entries"][0].update(fields)


# Each malformation of the first entry (toy_synthetic v1) and the field or entry the refusal must name.
MALFORMATIONS = {
    "not-an-object": (lambda doc: doc["entries"].__setitem__(0, "x"), "entry 0 is not an object"),
    "no-status": (lambda doc: doc["entries"][0].pop("status"), "field 'status'"),
    "bad-status": (lambda doc: _set_entry(doc, status="deleted"), "field 'status'"),
    "version-not-a-string": (lambda doc: _set_entry(doc, version=1), "field 'version'"),
    "hashes-not-an-object": (lambda doc: _set_entry(doc, hashes=[]), "field 'hashes'"),
    "hash-not-a-string": (lambda doc: _set_entry(doc, hashes={"corpus.jsonl": 3}), "field 'hashes'"),
    "no-change-log": (lambda doc: doc["entries"][0].pop("change_log"), "field 'change_log'"),
    "content-hash-not-hex": (lambda doc: _set_entry(doc, content_hash="not-a-hex-digest"), "field 'content_hash'"),
    "hash-not-hex": (lambda doc: _set_entry(doc, hashes={"corpus.jsonl": "not-a-hex-digest"}), "field 'hashes'"),
    "hash-uppercase": (lambda doc: _set_entry(doc, hashes={"corpus.jsonl": "A" * 64}), "field 'hashes'"),
    "generator-without-manifest": (lambda doc: _set_entry(doc, generator={"module": "removed_generator", "params": {}, "code_sha256": "x"}),
                                  "field 'generator'"),
}


@pytest.mark.parametrize("command", ["list", "register", "rehash", "regenerate", "rescore"])
@pytest.mark.parametrize("malformation", list(MALFORMATIONS), ids=list(MALFORMATIONS))
def test_a_malformed_registry_is_refused_by_every_command(tmp_path, capsys, command, malformation):
    v1, v2, registry, run = _versions(tmp_path)
    doc = json.loads(registry.read_text(encoding="utf-8"))
    edit, named = MALFORMATIONS[malformation]
    edit(doc)
    registry.write_text(json.dumps(doc), encoding="utf-8")
    before = registry.read_bytes()
    reg = str(registry)
    argv = {
        "list": ["list", "--registry", reg],
        "register": ["register", "--fixture", str(v2), "--name", "toy_synthetic", "--version", "9", "--reason", "r", "--registry", reg],
        "rehash": ["rehash", "--fixture", str(v1), "--name", "toy_synthetic", "--version", "1", "--reason", "r", "--registry", reg],
        "regenerate": ["regenerate", "toy_synthetic", "--version", "1", "--out", str(tmp_path / "out"), "--registry", reg],
        "rescore": ["rescore", "--run", str(run), "--fixture", str(v2), "--from", str(v1), "--profile", str(_profile(tmp_path)),
                    "--registry", reg],
    }[command]
    assert c.main(argv) == 2
    err = capsys.readouterr().err
    assert "refused: registry is malformed" in err and named in err
    assert registry.read_bytes() == before and not (tmp_path / "out").exists()  # nothing was written


def test_a_missing_registry_is_refused_by_read_registry_and_the_list_command(tmp_path, capsys):
    with pytest.raises(c.CorrectionError, match="file is missing"):
        c.read_registry(tmp_path / "none.json")
    assert c.list_entries(tmp_path / "none.json") == []  # the library read stays lenient (report uses it)
    assert c.main(["list", "--registry", str(tmp_path / "none.json")]) == 2
    assert "file is missing" in capsys.readouterr().err


def test_a_missing_registry_is_refused_by_list_and_register_and_leaves_the_manifest_untouched(tmp_path, toygen, capsys):
    registry = tmp_path / "VERSIONS.json"  # does not exist
    manifest = tmp_path / "manifests" / "toy_none_v1.json"
    manifest.parent.mkdir()
    manifest.write_bytes(b"kept")  # a manifest of this name and version is not replaced either
    assert c.main(["list", "--registry", str(registry)]) == 2
    assert c.main(["register", "--generator", GEN, "--name", "toy_none", "--version", "1", "--reason", "r",
                   "--registry", str(registry)]) == 2
    assert "file is missing" in capsys.readouterr().err
    assert not registry.exists() and manifest.read_bytes() == b"kept"
    with pytest.raises(c.CorrectionError, match="file is missing"):
        c.register(_synthetic_fixture(tmp_path / "v1"), "toy_synthetic", "1", "initial", registry=registry)
    assert not registry.exists()


def test_a_missing_registry_is_refused_by_rescore_with_the_register_message(tmp_path, capsys):
    run = tmp_path / "run"
    run.mkdir()
    (run / "answers.jsonl").write_text("", encoding="utf-8")
    toy, profile, missing = _synthetic_fixture(tmp_path / "fixture"), _profile(tmp_path), tmp_path / "none.json"
    with pytest.raises(c.CorrectionError, match=r"cannot read registry .*none\.json: the file is missing"):
        c.rescore(run, toy, toy, profile, missing)
    assert c.main(["rescore", "--run", str(run), "--fixture", str(toy), "--from", str(toy), "--profile", str(profile),
                   "--registry", str(missing)]) == 2
    assert "refused: cannot read registry" in capsys.readouterr().err and not any(run.glob("rescore_work_*"))


def test_rerun_extra_refuses_a_missing_registry_as_register_does(tmp_path, capsys):
    run = tmp_path / "run"
    run.mkdir()
    toy, profile, missing = _synthetic_fixture(tmp_path / "fixture"), _profile(tmp_path), tmp_path / "none.json"
    with pytest.raises(c.CorrectionError, match=r"cannot read registry .*none\.json: the file is missing"):
        c.rerun_extra(run, toy, toy, profile, missing)
    assert c.main(["rescore", "--run", str(run), "--fixture", str(toy), "--from", str(toy), "--rerun-extra",
                   "--profile", str(profile), "--registry", str(missing)]) == 2
    assert "refused: cannot read registry" in capsys.readouterr().err and not any(run.iterdir())


def test_each_committed_generated_manifest_equals_its_registry_entry():
    """JSON only: a generated version's manifest carries the same name, version, files, content hash and generator as its entry."""
    entries = [e for e in c.list_entries(c.REGISTRY) if "generator" in e]
    assert sorted((e["name"], e["version"]) for e in entries) == [
        ("mentat_hard_dev", "1"), ("mentat_hard_dev", "2"), ("mentat_hard_dev", "3"),
        ("mentat_hard_test", "1"), ("mentat_hard_test", "2"), ("mentat_hard_test", "3")]
    for e in entries:
        manifest = json.loads((FIXTURES / e["generator"]["manifest"]).read_text(encoding="utf-8"))
        assert manifest == {"name": e["name"], "version": e["version"], "content_hash": e["content_hash"], "files": e["hashes"],
                            "generator": {k: e["generator"][k] for k in ("module", "params", "code_sha256")}}, e["name"]


def test_register_refuses_a_committed_fixture_whose_queries_have_no_rows(tmp_path):
    fixture = _synthetic_fixture(tmp_path / "empty")
    (fixture / "queries.jsonl").write_text("", encoding="utf-8")
    registry = _new_registry(tmp_path)
    with pytest.raises(c.CorrectionError, match=r"queries\.jsonl has no rows"):
        c.register(fixture, "toy_empty", "1", "initial", registry=registry)
    assert c.list_entries(registry) == []  # nothing is registered


def test_an_empty_queries_extra_refuses_the_extra_re_score_and_the_extra_re_run(tmp_path):
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    for fixture in (v1, v2):
        (fixture / "queries_extra.jsonl").write_text("", encoding="utf-8")
    _set_decision(v2, "feasibility-02", "UNSAT", core=[0, 1])  # v2 differs in its gold, so both versions register
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    c.register(v2, "toy_synthetic", "2", "queries_extra emptied", ["feasibility-02"], registry=registry)
    run = tmp_path / "run"
    (run / "extra").mkdir(parents=True)
    shutil.copy(v1 / "gold" / "answers.jsonl", run / "answers.jsonl")
    (run / "extra" / "answers.jsonl").write_text("", encoding="utf-8")
    cell = {"fixture_stamp": c.stamp_for(v1, registry), "query_files": ["queries.jsonl", "queries_extra.jsonl"], "extra_status": "ok"}
    (run / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    with pytest.raises(c.CorrectionError, match="queries_extra.jsonl is missing or has no rows"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert not (run / "rescore_toy_synthetic_v2.json").exists()  # the guard refused before anything was written
    with pytest.raises(c.CorrectionError, match="queries_extra.jsonl has no rows: there are no queries to re-run"):
        c.rerun_extra(run, v2, v1, _profile(tmp_path), registry=registry)
    assert not (run / "extra_toy_synthetic_v2").exists()


def test_a_junction_in_a_fixture_is_refused(tmp_path):
    if not shutil.which("cmd"):
        pytest.skip("cmd is not available: no mklink /J")
    fixture = _synthetic_fixture(tmp_path / "v1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "answers.jsonl").write_text("{}\n", encoding="utf-8")
    link = fixture / "linked"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
    if made.returncode != 0 or not link.exists():
        pytest.skip(f"mklink /J is unavailable: {made.stderr.decode(errors='replace')[:200]}")
    try:
        with pytest.raises(c.CorrectionError, match="is a link"):
            c.content_hashes(fixture, tmp_path / "VERSIONS.json")
    finally:
        os.rmdir(link)  # removes the junction only; its target is outside the fixture and is not touched


@needs_engine
def test_a_new_version_without_queries_extra_refuses_only_the_extra_pass(tmp_path):
    """The new version has no queries_extra.jsonl: the extra pass is refused alone; the main file is still re-scored."""
    v1 = _synthetic_fixture(tmp_path / "v1")
    v2 = _synthetic_fixture(tmp_path / "v2")
    _set_decision(v2, "feasibility-02", "UNSAT", core=[0, 1])
    (v2 / "queries_extra.jsonl").unlink()
    registry = _new_registry(tmp_path)
    c.register(v1, "toy_synthetic", "1", "initial", registry=registry)
    c.register(v2, "toy_synthetic", "2", "feasibility-02 gold corrected; queries_extra dropped", ["feasibility-02"],
               registry=registry)
    run = _extra_run(tmp_path, v1, registry)
    before = (run / "score_extra.json").read_bytes()
    report = c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)
    assert report["old"]["Q_N"] == pytest.approx(100.0) and report["new"]["Q_N"] < 100.0  # the main file is re-scored
    assert report["extra"]["status"] == "refused" and "re-run the extra pass" in report["extra"]["reason"]
    assert (run / "score_extra.json").read_bytes() == before  # the run's extra score is left as it was


def test_a_missing_main_query_file_in_the_new_version_refuses_the_whole_rescore(tmp_path):
    v1, v2, registry, run = _versions(tmp_path)
    (v2 / "queries.jsonl").unlink()  # a required file: named by the fingerprint, before the rescore starts
    with pytest.raises(c.CorrectionError, match=r"has no \['queries.jsonl'\]"):
        c.rescore(run, v2, v1, _profile(tmp_path), registry=registry)

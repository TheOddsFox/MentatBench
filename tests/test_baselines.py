"""Baseline runner: cell arguments, usage errors, and an end-to-end run on a small synthetic fixture through runcell with
the fake engine. The synthetic baselines and the oracle-retrieval diagnostic (gold_view) are labelled apart."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
import pytest

from mentat_bench import baselines, ir, scoring

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers_pa_engine import make_fake_engine  # noqa: E402

ROWS = [
    ("v:m1:1", "Will Ada close above 100 on June 1, 12PM ET?", "Yes"),
    ("v:m1:2", "Will Ada close above 100 on June 1, 12PM ET?", "No"),
    ("v:m2:1", "Will Ben win the Example Cup?", "Yes"),
    ("v:m2:2", "Will Ben win the Example Cup?", "No"),
]


def make_fixture(root: Path) -> Path:
    fixture = root / "fixture"
    gold = fixture / "gold"
    gold.mkdir(parents=True)
    ir.write_jsonl(fixture / "corpus.jsonl", [
        {"controlled": True, "event_title": "E", "id": oid, "label": label, "text": text} for oid, text, label in ROWS])
    ir.write_jsonl(fixture / "queries.jsonl", [{"family": "closure", "qid": "c1", "hyp": []}])
    ir.write_jsonl(gold / "bindings.jsonl", [
        {"class": f"k{i}", "evidence": [], "id": oid, "missing": [], "negated": label == "No", "status": "bound"}
        for i, (oid, _, label) in enumerate(ROWS)])
    ir.write_jsonl(gold / "answers.jsonl", [{"qid": "c1", "labels": {
        "ENTAILED": ["v:m1:1"], "REFUTED": ["v:m1:2"], "CONTINGENT": ["v:m2:1", "v:m2:2"], "UNRESOLVED": []}}])
    ir.write_jsonl(gold / "query_design.jsonl", [{"anchor": True, "family": "closure", "qid": "c1"}])
    (gold / "theory.json").write_text(json.dumps({"interpretations": ["base"], "constraints": []}), encoding="utf-8")
    return fixture


def test_cell_arguments_name_the_child_and_its_flags(tmp_path) -> None:
    fx, out = tmp_path / "f", tmp_path / "o"
    lexical = baselines.cell_argv("lexical", fx, out, 24)
    assert "--local-arg=mentat_bench.children.lexical_child" in lexical and "--no-groups" in lexical
    assert lexical[lexical.index("--top-k") + 1] == "24"
    embedding = baselines.cell_argv("embedding", fx, out, 24)
    assert "--local-arg=mentat_bench.children.embed_child" in embedding and "--no-groups" in embedding
    assert f"--local-arg={fx / 'corpus.jsonl'}" in embedding and "--local-timeout" in embedding
    assert "--no-groups" not in baselines.cell_argv("heuristic", fx, out, 24)
    assert baselines.cell_argv("gold_view", fx, out, 24)[:2] == ["--adapter", "gold_view"]
    assert baselines.cell_argv("oracle", fx, out, 24)[:2] == ["--adapter", "oracle"]


def test_usage_errors_exit_2_before_anything_runs(tmp_path, monkeypatch) -> None:
    fx = make_fixture(tmp_path)
    monkeypatch.delenv("MB_HF_MODEL", raising=False)
    monkeypatch.delenv("MB_HF_DEVICE", raising=False)
    base = ["run", "--fixture", str(fx), "--out", str(tmp_path / "out")]
    assert baselines.main([*base, "--cells", "bogus"]) == baselines.EXIT_USAGE
    assert baselines.main([*base, "--cells", "hf"]) == baselines.EXIT_USAGE  # needs --include-hf
    assert baselines.main([*base, "--cells", "hf", "--include-hf"]) == baselines.EXIT_USAGE  # needs MB_HF_* env
    monkeypatch.setenv("MB_HF_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
    assert baselines.main([*base, "--cells", "hf", "--include-hf"]) == baselines.EXIT_USAGE  # MB_HF_DEVICE unset
    assert baselines.main([*base, "--top-k", "0"]) == baselines.EXIT_USAGE
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "old.txt").write_text("x", encoding="utf-8")
    assert baselines.main(["run", "--fixture", str(fx), "--out", str(busy)]) == baselines.EXIT_USAGE
    assert not (tmp_path / "out").exists()


def test_end_to_end_table_labels_synthetic_and_diagnostic_rows(tmp_path, monkeypatch) -> None:
    launcher = make_fake_engine(tmp_path / "bin")
    monkeypatch.setenv("MB_ENGINE", str(launcher))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    fx = make_fixture(tmp_path)
    out = tmp_path / "table"
    cells = "empty,lexical,heuristic,gold_view,oracle"
    code = baselines.main(["run", "--fixture", str(fx), "--out", str(out), "--cells", cells, "--top-k", "3"])
    doc = json.loads((out / "baselines.json").read_text(encoding="utf-8"))
    assert code == 0, doc["exit_codes"]
    assert doc["top_k"] == 3 and set(doc["cells"]) == set(cells.split(","))
    assert all(row["status"] == "complete" for row in doc["cells"].values()), doc["cells"]
    assert doc["cells"]["lexical"]["calls"] > 0 and doc["cells"]["empty"]["calls"] > 0
    assert set(doc["synthetic"]) == set(scoring.BASELINES)
    assert doc["synthetic"]["constant-answer"]["gold_informed"] is True
    assert doc["synthetic"]["all-contingent"]["gold_informed"] is False
    assert doc["synthetic"]["all-contingent"]["strict_Q_N"] is None and doc["synthetic"]["all-contingent"]["calls"] == 0
    md = (out / "baselines.md").read_text(encoding="utf-8")
    main_part, diagnostic_part = md.split("## Oracle-retrieval diagnostic (not a corpus score)")
    assert "oracle-retrieval DIAGNOSTIC" not in main_part and "oracle-retrieval DIAGNOSTIC" in diagnostic_part
    assert "synthetic: all-contingent" in main_part and "synthetic: constant-answer" in main_part
    assert "oracle meanings through the common engine" in main_part


def test_gold_reading_rows_are_marked_and_model_rows_are_not(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "table"
    assert baselines.main(["run", "--fixture", str(make_fixture(tmp_path)), "--out", str(out),
                           "--cells", "lexical,oracle,gold_view", "--top-k", "3"]) == 0
    cells = json.loads((out / "baselines.json").read_text(encoding="utf-8"))["cells"]
    assert cells["oracle"]["gold_informed"] is True and cells["gold_view"]["gold_informed"] is True
    assert cells["lexical"]["gold_informed"] is False  # False, never null: the column says yes or no


def test_engine_sha256_and_each_rows_top_k_are_recorded(tmp_path, monkeypatch) -> None:
    launcher = make_fake_engine(tmp_path / "bin")
    monkeypatch.setenv("MB_ENGINE", str(launcher))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    out = tmp_path / "table"
    assert baselines.main(["run", "--fixture", str(make_fixture(tmp_path)), "--out", str(out), "--cells", "lexical",
                           "--top-k", "3"]) == 0
    doc = json.loads((out / "baselines.json").read_text(encoding="utf-8"))
    assert doc["engine"]["sha256"] == hashlib.sha256(launcher.read_bytes()).hexdigest()
    assert doc["cells"]["lexical"]["top_k"] == 3
    assert "sha256" in (out / "baselines.md").read_text(encoding="utf-8")
    assert any(note.startswith("note: the build's own default") for note in doc["notes"])


def test_rows_with_another_k_produce_a_warning() -> None:
    notes = baselines.topk_notes([{"top_k": 24}, {"top_k": 128}, {"top_k": None}], 24)
    assert any(note.startswith("warning: rows used top-k [24, 128]") for note in notes)
    assert baselines.topk_notes([{"top_k": 24}], 24) == [
        f"note: the build's own default is --top-k {baselines.BUILD_TOP_K}; every row here uses 24"]


def test_a_synthetic_score_error_is_recorded_and_the_table_is_still_written(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    real = baselines.synthetic_row

    def broken(name, fixture):
        if name == "constant-answer":
            raise ValueError("gold answer is malformed")
        return real(name, fixture)

    monkeypatch.setattr(baselines, "synthetic_row", broken)
    out = tmp_path / "table"
    code = baselines.main(["run", "--fixture", str(make_fixture(tmp_path)), "--out", str(out), "--cells", "lexical",
                           "--top-k", "3"])
    assert code == baselines.EXIT_FAIL
    doc = json.loads((out / "baselines.json").read_text(encoding="utf-8"))  # written despite the error
    assert doc["synthetic"]["constant-answer"]["status"] == "failed"
    assert "gold answer is malformed" in doc["synthetic"]["constant-answer"]["error"]
    assert doc["synthetic"]["all-contingent"]["status"] == "synthetic"
    assert doc["cells"]["lexical"]["status"] == "complete"
    assert any("failed rows: synthetic constant-answer" in note for note in doc["notes"])
    assert "| synthetic: constant-answer | failed |" in (out / "baselines.md").read_text(encoding="utf-8")
    assert capsys.readouterr().out  # the table still goes to stdout


def _cell_dir(root, name, *, adapter="local", module=None, top_k=24, fixture="mentat_hard_test", q_n=27.0, mcc=0.3):
    """A baseline cell directory as runcell writes it (cell.json, score.json, build_summary.json, score_decisions.json)."""
    run = root / name
    cell = {"adapter": adapter, "fixture": fixture, "status": "complete", "top_k": top_k, "partial": [], "incomplete": False,
            "runtime": {"engine": {"path": "<external>/mb-engine-mh.exe", "sha256": "e" * 64}}}
    if module is not None:
        cell["local_arg"] = [sys.executable, "-m", module]
    run.mkdir(parents=True, exist_ok=True)
    (run / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    (run / "score.json").write_text(json.dumps({"Q_N": q_n, "families": {}}), encoding="utf-8")
    (run / "build_summary.json").write_text(json.dumps({"base_repaired": False}), encoding="utf-8")
    if mcc is not None:
        (run / "score_decisions.json").write_text(json.dumps({"headline": {"metric": "relation_mcc", "relation_mcc": mcc},
            "ops": {"decide_relation": {"mcc": mcc}}}), encoding="utf-8")
    return run


def test_collect_names_the_rules_cell_heuristic_and_states_its_k(tmp_path) -> None:
    _cell_dir(tmp_path, "rules", module="mentat_bench.children.heuristic_child", top_k=24)
    _cell_dir(tmp_path, "empty", adapter="empty", top_k=24, mcc=None)
    doc = baselines.collect([tmp_path], 24)
    assert set(doc["cells"]) == {"heuristic", "empty"}
    assert doc["cells"]["heuristic"]["run"].startswith("<external>/rules#") and doc["cells"]["heuristic"]["top_k"] == 24
    assert doc["cells"]["heuristic"]["decision_rel_mcc"] == 0.3 and doc["cells"]["empty"]["decision_rel_mcc"] is None
    assert doc["engine"] == {"path": "<external>/mb-engine-mh.exe", "sha256": "e" * 64}
    assert doc["fixture"] == "mentat_hard_test" and not any(n.startswith("warning") for n in doc["notes"])
    assert str(tmp_path) not in json.dumps(doc)  # no absolute paths in the table


def test_mixed_k_is_flagged_in_the_table_notes(tmp_path) -> None:
    _cell_dir(tmp_path, "rules", module="mentat_bench.children.heuristic_child", top_k=24)
    _cell_dir(tmp_path, "oracle", adapter="oracle", top_k=64, mcc=None)
    doc = baselines.collect([tmp_path], 24)
    assert doc["cells"]["oracle"]["top_k"] == 64
    assert any(note.startswith("warning: rows used top-k [24, 64]") for note in doc["notes"]), doc["notes"]


def test_a_name_in_two_directories_is_refused(tmp_path) -> None:
    _cell_dir(tmp_path / "a", "empty", adapter="empty")
    _cell_dir(tmp_path / "b", "empty", adapter="empty")
    with pytest.raises(ValueError, match="empty is in two directories"):
        baselines.collect([tmp_path / "a", tmp_path / "b"], 24)


def test_a_directory_that_is_not_a_baseline_cell_is_refused(tmp_path) -> None:
    _cell_dir(tmp_path, "mystery", module="mentat_bench.children.not_a_child")
    with pytest.raises(ValueError, match="is not a baseline cell"):
        baselines.collect([tmp_path], 24)


def test_cells_on_two_fixtures_are_refused(tmp_path) -> None:
    _cell_dir(tmp_path, "empty", adapter="empty", fixture="mentat_hard_test")
    _cell_dir(tmp_path, "oracle", adapter="oracle", fixture="mentat_hard_dev")
    with pytest.raises(ValueError, match="over one fixture"):
        baselines.collect([tmp_path], 24)


def test_collect_writes_the_table_and_prints_its_headline(tmp_path, capsys) -> None:
    _cell_dir(tmp_path, "rules", module="mentat_bench.children.heuristic_child", top_k=24)
    out = tmp_path / "table.json"
    assert baselines.main(["collect", "--runs", str(tmp_path), "--top-k", "24", "--out", str(out)]) == 0
    assert set(json.loads(out.read_text(encoding="utf-8"))["cells"]) == {"heuristic"}
    text = capsys.readouterr().out
    assert "headline MCC" in text and "Headline: the relation MCC for fixture versions 1 and 2" in text


def test_the_table_json_of_a_version_1_or_2_row_has_no_headline_keys(tmp_path) -> None:
    _cell_dir(tmp_path, "rules", module="mentat_bench.children.heuristic_child", top_k=24)  # its headline is the relation MCC
    out = tmp_path / "table.json"
    assert baselines.main(["collect", "--runs", str(tmp_path), "--top-k", "24", "--out", str(out)]) == 0
    row = json.loads(out.read_text(encoding="utf-8"))["cells"]["heuristic"]
    assert not {"decision_headline", "decision_headline_metric"} & set(row)
    assert row["decision_rel_mcc"] == 0.3  # the MCC columns are unchanged


def test_a_version_3_cell_gives_the_headline_and_its_parts_in_the_table(tmp_path, capsys) -> None:
    _cell_dir(tmp_path, "rules", module="mentat_bench.children.heuristic_child", top_k=24)
    _cell_dir(tmp_path, "lexical", module="mentat_bench.children.lexical_child", top_k=24, mcc=0.5)  # a version 1 or 2 row: its headline is its relation MCC
    v3 = {"headline": {"metric": "relation_group_mcc", "relation_group_mcc": 0.25, "parts": {"relation_mcc": 0.3, "group_mcc": 0.2},
                       "secondary": {"binding_mcc": 0.3}, "diagnostics": {"reading_mcc": None}},
          "ops": {"decide_relation": {"mcc": 0.3}, "decide_binding": {"mcc": 0.3}, "decide_group": {"mcc": 0.2}}}
    (tmp_path / "rules" / "score_decisions.json").write_text(json.dumps(v3), encoding="utf-8")
    out = tmp_path / "table.json"
    assert baselines.main(["collect", "--runs", str(tmp_path), "--top-k", "24", "--out", str(out)]) == 0
    cells = json.loads(out.read_text(encoding="utf-8"))["cells"]
    assert (cells["heuristic"]["decision_headline"], cells["heuristic"]["decision_headline_metric"]) == (0.25, "relation_group_mcc")
    assert (cells["heuristic"]["decision_rel_mcc"], cells["heuristic"]["decision_grp_mcc"]) == (0.3, 0.2)
    assert "decision_headline" not in cells["lexical"] and "decision_headline_metric" not in cells["lexical"]  # v1/v2 JSON bytes
    assert cells["lexical"]["decision_rel_mcc"] == 0.5
    text = capsys.readouterr().out
    assert "| headline MCC | headline metric |" in text and "relation_group_mcc" in text and "| 0.25 |" in text
    assert "the mean of the relation and group MCCs for version 3 and later" in text


def test_the_candidates_flag_reaches_every_cell(tmp_path) -> None:
    cand = tmp_path / "candidates.jsonl"
    cand.write_text("", encoding="utf-8")
    assert "--candidates" in baselines.cell_argv("lexical", tmp_path, tmp_path / "out", 24, cand)
    assert "--candidates" in baselines.cell_argv("gold_view", tmp_path, tmp_path / "out", 24, cand)
    assert "--candidates" not in baselines.cell_argv("oracle", tmp_path, tmp_path / "out", 24, cand)  # refused by runcell
    assert "--candidates" not in baselines.cell_argv("empty", tmp_path, tmp_path / "out", 24, cand)  # no decisions
    assert "--candidates" not in baselines.cell_argv("lexical", tmp_path, tmp_path / "out", 24)
    prof = tmp_path / "mentat-hard-v1.json"
    argv = baselines.cell_argv("heuristic", tmp_path, tmp_path / "out", 24, profile=prof)
    assert argv[argv.index("--profile") + 1] == str(prof)
    assert "--profile" not in baselines.cell_argv("heuristic", tmp_path, tmp_path / "out", 24)


def test_a_failed_row_names_its_cell_without_an_absolute_path(tmp_path) -> None:
    """A cell with no score.json: its error text names the cell repository-relatively (or <external>), never the absolute
    directory (a table holds no absolute path)."""
    run = _cell_dir(tmp_path, "empty", adapter="empty")
    (run / "score.json").unlink()
    cell = json.loads((run / "cell.json").read_text(encoding="utf-8"))
    cell.pop("status")  # a cell that recorded no status and has no score is a row error (report.run_row raises)
    (run / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    doc = baselines.collect([tmp_path], 24)
    row = doc["cells"]["empty"]
    assert row["status"] == "failed" and "no readable score.json" in row["error"]
    assert str(tmp_path) not in json.dumps(doc) and "<external>" in row["error"]

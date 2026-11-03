"""results/v0.2.0/build_bundle.py: the pieces that need no run directory (fast). Synthetic inputs where a fixture would be needed, and the
committed v0.1.0 predictions and cells for the interval primitives: the builder's intervals must reproduce the v0.1.0 leaderboard
(the same league bootstrap, B 2000, seed 0). The run-directory collection is checked by hand on the real r1 runs (not here)."""

import gzip
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools" / "eval"))

import league_ci as LC  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


B2 = _load("build_bundle_v020", ROOT / "results" / "v0.2.0" / "build_bundle.py")
B1 = _load("build_bundle_v011_t", ROOT / "results" / "v0.1.1" / "build_bundle.py")


def test_flag_map_pairs_each_flag_with_its_value_and_normalises_paths():
    flags = ["--local-protocol", "2", "--candidates", "fixtures\\mentat_hard_test\\candidates.jsonl", "--groups"]
    assert B2.flag_map(flags) == {"--local-protocol": "2", "--candidates": "fixtures/mentat_hard_test/candidates.jsonl",
                                  "--groups": None}


def test_floor_is_read_once_and_removed_from_the_comparison():
    flags = ["--top-k", "24", "--relation-floor", "0.5", "--batch-size", "128"]
    assert B2.floor_of(flags) == 0.5
    assert B2.without_floor(flags) == {"--top-k": "24", "--batch-size": "128"}
    with pytest.raises(B2.CheckError):
        B2.floor_of(["--top-k", "24"])
    with pytest.raises(B2.CheckError):
        B2.floor_of(["--relation-floor", "1", "--relation-floor", "2"])


def test_repo_commits_accepted_are_the_two_named_ones():
    assert B2.ACCEPTED_COMMITS == {"29ce00d72f81f779b1e5a472287ba30f73ea8eac": True,
                                   "f1652ad60bf701da86bbedfe94319b5b21c7923b": False}
    assert B2.ENGINE_SHA256 == "38fc6fcc4b862634fbcc6e6de0ec654aaef1d0960202c76988f46fe28c0ce094"


def test_models_are_the_fifteen_plus_two_kev_and_the_baselines_are_seven():
    assert len(B2.MODELS) == 17 and len(set(B2.MODELS)) == 17
    assert B2.MODELS[-2:] == ("kev_4b-q8_0", "kev_0.8b-q8_0")
    assert B2.tag_of("kev_4b-q8_0") == "kev:4b-q8_0" and B2.tag_of("d3_27b-q8_0") == "d3:27b-q8_0"
    assert B2.BASELINES == ("heuristic", "lexical", "embedding", "empty", "gold_view", "oracle", "hf")


def test_statistic_and_gap_are_rounded_and_paired():
    draws = np.array([0.1, 0.2, 0.3, np.nan])
    block = B2.statistic(0.2, draws)
    assert block["point"] == 0.2 and block["ci95"] == [round(float(np.percentile([0.1, 0.2, 0.3], 2.5)), 6),
                                                       round(float(np.percentile([0.1, 0.2, 0.3], 97.5)), 6)]
    assert B2.statistic(None, draws) == {"point": None, "ci95": None}
    assert B2.statistic(0.2, draws, ci=False) == {"point": 0.2, "ci95": None}
    ref = {"x": (0.5, np.array([0.5, 0.6, 0.7]))}
    run = {"x": (0.2, np.array([0.1, 0.2, 0.3]))}
    gap = B2.gap_statistic(ref, run, "x")  # reference minus run, draw by draw: 0.4 in every draw, and 0.5 - 0.2 at the point
    assert gap == {"point": 0.3, "ci95": [0.4, 0.4]}
    assert B2.gap_statistic(ref, {}, "x") is None


def test_flags_are_data_derived_and_name_the_provenance():
    rows = [{"op": "decide_binding", "pred": "bound"}]
    cell = {"cell": {"base_repaired": False}}
    flags = B2.flags_of("kev_4b-q8_0", cell, rows, {}, True, {"relation_abstained": 3}, {})
    assert flags == ["not_preregistered", "base_not_repaired", "names_no_relations_under_floor", "abstains_for_length",
                     "never_flags_ambiguous"]
    assert B2.flags_of("empty", {"cell": {}}, None, {}, None, None, {}) == ["q_n_only"]
    assert "reference" in B2.flags_of(B2.REFERENCE, {"cell": {}}, rows, {}, False, None, {})


def test_carried_baseline_rows_are_v010_rows_except_the_gaps():
    """The committed v0.2.0 baseline rows equal v0.1.0's, field for field, except the gap (against this release's reference)
    and private, which is null (v0.2.0 publishes no private-split results). The check is the builder's own."""
    v010 = {r["id"]: r for r in json.loads((ROOT / "results" / "v0.1.0" / "leaderboard.json").read_text(encoding="utf-8"))["baselines"]}
    v020 = json.loads((ROOT / "results" / "v0.2.0" / "leaderboard.json").read_text(encoding="utf-8"))["baselines"]
    assert sorted(r["id"] for r in v020) == sorted(v010) and len(v010) == 7
    for r in v020:
        old = v010[r["id"]]
        assert B2.carry_problems(r, old) == [], r["id"]
        assert (r["gap_to_reference"] is None) == (old["gap_to_reference"] is None), r["id"]  # no gap where v0.1.0 has none
        assert B2.carried_metric_problems(r["metrics"], old["metrics"], r["predictions"] is not None) == [], r["id"]
        assert r["flags"] == old["flags"] and r["notes"] == old["notes"] and r["runcard"] == old["runcard"], r["id"]
    hf = next(r for r in v020 if r["id"] == "hf")
    assert hf["build_flags"] == v010["hf"]["build_flags"]
    # a drifted row is named: a changed note, a private block, a changed metric point or an interval that has rows
    assert B2.carry_problems({**hf, "notes": []}, v010["hf"]) == ["notes"]
    gold = next(r for r in v020 if r["id"] == "gold_view")
    assert gold["private"] is None
    assert B2.carry_problems({**gold, "private": v010["gold_view"]["private"]}, v010["gold_view"]) == ["private"]
    old_metrics = v010["heuristic"]["metrics"]
    assert B2.carried_metric_problems({**old_metrics, "headline": {"point": 0.5, "ci95": None}}, old_metrics, True) == ["headline"]
    q_n = {**old_metrics["q_n_repaired"], "ci95": [1.0, 2.0]}  # a run with no rows gets no interval, so only a run with rows is compared
    assert B2.carried_metric_problems({**old_metrics, "q_n_repaired": q_n}, old_metrics, False) == []
    assert B2.carried_metric_problems({**old_metrics, "q_n_repaired": q_n}, old_metrics, True) == ["q_n_repaired"]


def test_scrubbed_refuses_a_path_an_address_and_the_user_name():
    B2.scrubbed(b"fine text")
    for bad in (b"C:\\Users\\x", b"/home/me/run", b"someone@example.com", Path.home().name.encode()):
        with pytest.raises(B2.CheckError):
            B2.scrubbed(bad)
    with pytest.raises(B2.CheckError):
        B2.scrubbed(gzip.compress(b"/Users/me/x"))


def test_csv_has_one_row_per_entry_and_the_columns_of_the_json(tmp_path):
    entry = {"id": "m", "display_name": "M", "kind": "system_one_model", "family": "d3", "rank": 1, "ollama_tag": "d3:4b-q8_0",
             "digest": "ab", "params": "4B", "quant": "q8_0", "license": "Apache-2.0", "size_bytes": 5,
             "runtime": {"build_wall_min": 2.5}, "dev": {"headline": 0.2}, "relation_floor": 1.5,
             "metrics": {k: None for k in ("headline", "relation", "group", "binding", "reading", "held_out_relation",
                                           "seen_relation", "q_n_repaired")} | {"q_n_strict": {"point": 0.0, "ci95": None}},
             "gap_to_reference": None, "rank_interval": {"lo": 1, "hi": 2, "median": 1}}
    entry["metrics"]["headline"] = {"point": 0.25, "ci95": [0.2, 0.3]}
    text = B2.leaderboard_csv({"models": [entry], "baselines": []}).decode("utf-8").splitlines()
    assert text[0].split(",") == B2.CSV_COLUMNS
    row = dict(zip(B2.CSV_COLUMNS, text[1].split(",")))
    assert row["headline_point"] == "0.25" and row["headline_lo"] == "0.2" and row["rank_lo"] == "1" and row["q_n_strict"] == "0.0"


def test_committed_index_hands_over_to_v021_and_v020_keeps_its_entry():
    """The committed results/index.json (no skip) is owned by v0.2.1 now: v0.2.0 is superseded by it, keeps its record_from and
    files, and the v0.2.0 builder reports no index to write (index_doc is None) while v0.2.1 is the latest."""
    doc = json.loads((ROOT / "results" / "index.json").read_bytes().decode("utf-8"))
    assert doc["latest"] == "v0.2.1" and doc["versions"][0]["version"] == "v0.2.1"
    v020 = next(v for v in doc["versions"] if v["version"] == "v0.2.0")
    assert v020["status"] == "superseded" and v020["superseded_by"] == "v0.2.1" and v020["record_from"] == "v0.1.0"
    assert [v["version"] for v in doc["versions"]].count("v0.2.0") == 1
    assert B2.index_doc() is None


def test_v011_builder_leaves_the_index_alone_once_a_later_release_is_latest(tmp_path, monkeypatch):
    """The v0.1.1 builder writes the index only while v0.1.1 is its latest release (results/index.json belongs to the later builder
    after that), so its --check cannot fail on the index of v0.2.0."""
    index = json.loads((ROOT / "results" / "index.json").read_text(encoding="utf-8"))
    later = dict(index, latest="v0.2.0")
    (tmp_path / "v0.1.0").mkdir()
    (tmp_path / "v0.1.0" / "leaderboard.json").write_bytes((ROOT / "results" / "v0.1.0" / "leaderboard.json").read_bytes())
    (tmp_path / "v0.1.0" / "leaderboard.csv").write_bytes((ROOT / "results" / "v0.1.0" / "leaderboard.csv").read_bytes())
    (tmp_path / "index.json").write_text(json.dumps(later), encoding="utf-8")
    monkeypatch.setattr(B1, "RESULTS", tmp_path)
    outputs = B1.build_outputs()
    assert sorted(outputs) == ["leaderboard.csv", "leaderboard.json"]


def test_intervals_of_the_v010_predictions_reproduce_the_v010_leaderboard():
    """The primitives the release builds with, run on committed v0.1.0 rows and cells: every metric's point and interval equal the
    v0.1.0 leaderboard's (within 1e-9; the leaderboard is rounded to 6 decimals and so is the builder)."""
    fx = LC.read_fixture(B2.TEST_FIXTURE)
    L = len(fx["leagues"])
    ones = np.ones((1, L), dtype=np.int64)
    W = LC.draws(B2.B_RANK, B2.SEED, L)
    lb = json.loads((ROOT / "results" / "v0.1.0" / "leaderboard.json").read_text(encoding="utf-8"))
    entries = {e["id"]: e for e in lb["models"] + lb["baselines"]}
    names = {"headline": "headline", "relation": "relations", "group": "groups", "binding": "bindings", "reading": "readings",
             "held_out_relation": "held_out_relations", "q_n_repaired": "q_n"}
    for mid in ("d3_27b-q8_0", "tev1_0.8b-q8_0", "laya_latest", "lexical", "empty"):
        cell_doc = json.loads((ROOT / "results" / "v0.1.0" / "cells" / f"{mid}.json").read_text(encoding="utf-8"))
        pred = ROOT / "results" / "v0.1.0" / "predictions" / f"{mid}.jsonl.gz"
        rows = [json.loads(x) for x in gzip.decompress(pred.read_bytes()).decode("utf-8").splitlines()] if pred.is_file() else None
        draws = B2.run_draws(rows, cell_doc["score"], mid, fx, W, ones)
        for metric, source in names.items():
            ref = entries[mid]["metrics"][metric]
            if ref is None or source not in draws:
                continue
            block = B2.statistic(*draws[source])
            assert block["point"] == pytest.approx(ref["point"], abs=1e-9), (mid, metric)
            if ref["ci95"] is not None:
                assert block["ci95"][0] == pytest.approx(ref["ci95"][0], abs=1e-9), (mid, metric)
                assert block["ci95"][1] == pytest.approx(ref["ci95"][1], abs=1e-9), (mid, metric)


def test_baseline_status_must_name_all_seven(tmp_path, monkeypatch):
    path = tmp_path / "baselines.json"
    doc = {"schema": B2.BASELINES_SCHEMA, "release": "v0.2.0", "baselines": {"heuristic": {"status": "carried"}}}
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(B2, "V020", tmp_path)
    with pytest.raises(B2.CheckError):
        B2.baseline_status()
    doc["baselines"] = {b: {"status": "carried"} for b in B2.BASELINES}
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert set(B2.baseline_status()) == set(B2.BASELINES)


def test_added_record_lists_the_two_kev_pins_and_their_floors():
    docs = {mid: {"floor": {"value": 1.5, "names_relations": True}, "dev": {"headline": 0.1}}
            for mid in ("kev_4b-q8_0", "kev_0.8b-q8_0")}
    rows = json.loads(B2.added_record(docs).decode("utf-8"))
    assert [r["id"] for r in rows] == ["kev_4b-q8_0", "kev_0.8b-q8_0"]
    assert all(r["added_after_preregistration"] and r["relation_floor"] == 1.5 for r in rows)
    assert rows[0]["digest"] == "46203beabf65d133ff2291c5482f37c9ce210175d162b2d49fc3c5f2ae3a83f8"


@pytest.mark.slow  # about 17 s: the whole build over the committed v0.1.0 data
def test_build_on_the_v010_data_reproduces_its_metrics_gaps_and_the_v011_rank_intervals(tmp_path, monkeypatch):
    """The whole build (build_outputs) on the committed v0.1.0 data, presented as a v0.2.0 tree: the 15 pre-registered models with
    their v0.1.0 cells and predictions (the cell documents carry the blocks the build reads from a collection), and the seven
    baselines carried. Every metric, gap and rank must equal the v0.1.0 leaderboard's, and the rank intervals the v0.1.1 analysis's."""
    prereg_ids = tuple(B2.TAG_OF)
    lb = json.loads((ROOT / "results" / "v0.1.0" / "leaderboard.json").read_text(encoding="utf-8"))
    v011 = json.loads((ROOT / "results" / "v0.1.1" / "leaderboard.json").read_text(encoding="utf-8"))
    entries = {e["id"]: e for e in lb["models"] + lb["baselines"]}
    ranks = {e["id"]: e["rank_interval"] for e in v011["models"]}
    monkeypatch.setattr(B2, "MODELS", prereg_ids)
    monkeypatch.setattr(B2, "ADDED", {})
    monkeypatch.setattr(B2, "V020", tmp_path)
    (tmp_path / "cells").mkdir()
    (tmp_path / "predictions").mkdir()
    for mid in prereg_ids:
        cell = json.loads((ROOT / "results" / "v0.1.0" / "cells" / f"{mid}.json").read_text(encoding="utf-8"))
        e = entries[mid]
        cell.update({
            "floor": {"value": sys.float_info.max if e["relation_floor_disabled"] else e["relation_floor"],
                      "names_relations": e["names_relations_under_floor"]},
            "model": {"child_version": e["child"]["version"], "prompt_sha256": e["child"]["prompt_sha256"]},
            "depth": e["depth_accuracy"], "counts": e["counts"], "coverage": e["coverage"], "repair": e["repair"],
            "abstention": e["abstention"], "dev": None})
        (tmp_path / "cells" / f"{mid}.json").write_text(json.dumps(cell), encoding="utf-8")
        (tmp_path / "predictions" / f"{mid}.jsonl.gz").write_bytes(
            (ROOT / "results" / "v0.1.0" / "predictions" / f"{mid}.jsonl.gz").read_bytes())
    (tmp_path / "baselines.json").write_text(json.dumps({
        "schema": B2.BASELINES_SCHEMA, "release": "v0.2.0", "based_on": "v0.1.0",
        "baselines": {b: {"status": "carried", "from": "v0.1.0"} for b in B2.BASELINES}}), encoding="utf-8")
    out = B2.build_outputs()
    lb2 = json.loads(out["leaderboard.json"].decode("utf-8"))
    assert [e["id"] for e in lb2["models"]] == lb2["ranking"] and len(lb2["models"]) == 15
    for e in lb2["models"]:
        old = entries[e["id"]]
        assert e["rank"] == old["rank"], e["id"]
        for metric in ("headline", "relation", "group", "binding", "reading", "held_out_relation", "q_n_repaired", "seen_relation"):
            assert e["metrics"][metric] == old["metrics"][metric], (e["id"], metric)
        assert e["metrics"]["q_n_strict"]["point"] == old["metrics"]["q_n_strict"]["point"], e["id"]
        assert e["rank_interval"] == {"lo": ranks[e["id"]]["lo"], "hi": ranks[e["id"]]["hi"], "median": ranks[e["id"]]["median"]}
        if old["gap_to_reference"] is None:
            assert e["gap_to_reference"] is None
        else:
            for stat in ("headline", "relation", "group", "binding", "q_n_repaired"):
                assert e["gap_to_reference"][stat] == old["gap_to_reference"][stat], (e["id"], stat)
    for e in lb2["baselines"]:
        assert B2.carry_problems(e, entries[e["id"]]) == [] and e["cell"].startswith("results/v0.1.0/cells/"), e["id"]
        assert e["metrics"]["headline"] == entries[e["id"]]["metrics"]["headline"], e["id"]
    assert out["leaderboard.csv"].decode("utf-8").count("\n") == 1 + len(lb2["models"]) + len(lb2["baselines"])


def test_baseline_carry_compares_the_score_without_usage():
    """empty and oracle have no predictions: their score.json must equal v0.1.0's once usage (timings, memory, call counts) is
    removed. The seal digest is not compared (it hashes the submission directory, timings included)."""
    assert B2.without_usage({"usage": {"calls": 3}, "Q_N": 16.8}) == {"Q_N": 16.8}
    old = json.loads((ROOT / "results" / "v0.1.0" / "cells" / "empty.json").read_text(encoding="utf-8"))["score"]
    assert B2.without_usage(old) == {k: v for k, v in old.items() if k != "usage"}
    assert "usage" in old  # the v0.1.0 score carries usage, so the strip is what makes a re-run comparable


@pytest.mark.slow  # about 15 s: runs the release builder's own check in a subprocess
def test_build_check_subprocess_passes_on_the_committed_files():
    result = subprocess.run([sys.executable, "results/v0.2.0/build_bundle.py", "build", "--check"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _tables(md: str) -> list[tuple[str, list[list[str]]]]:
    """(header line, body rows as cell lists) of every markdown table in a document."""
    out, current = [], []
    for line in md.splitlines() + [""]:
        if line.startswith("|"):
            current.append(line)
            continue
        if current:
            out.append((current[0], [[c.strip() for c in row.strip().strip("|").split("|")] for row in current[2:]]))
            current = []
    return out


def _table(md: str, header_prefix: str) -> list[list[str]]:
    found = [body for header, body in _tables(md) if header.startswith(header_prefix)]
    assert len(found) == 1, f"{len(found)} tables start with {header_prefix!r}"
    return found[0]


def _ci3(block) -> str:
    if block is None or block.get("point") is None:
        return "n/a"
    if block.get("ci95") is None:
        return f"{block['point']:.3f}"
    return f"{block['point']:.3f} [{block['ci95'][0]:.3f}, {block['ci95'][1]:.3f}]"


def test_v020_readme_tables_match_the_committed_json_cell_by_cell():
    """Every cell of the four v0.2.0 README tables (leaderboard, run to run, version shift, baselines) against leaderboard.json,
    analysis.json and baselines.json, formatted as the README states its numbers."""
    md = (ROOT / "results" / "v0.2.0" / "README.md").read_text(encoding="utf-8")
    lb = json.loads((ROOT / "results" / "v0.2.0" / "leaderboard.json").read_text(encoding="utf-8"))
    an = json.loads((ROOT / "results" / "v0.2.0" / "analysis.json").read_text(encoding="utf-8"))
    bl = json.loads((ROOT / "results" / "v0.2.0" / "baselines.json").read_text(encoding="utf-8"))
    rows = _table(md, "| Rank | Run id | Model")
    assert len(rows) == len(lb["models"]) == 17
    for row, e in zip(rows, lb["models"]):
        ri, m = e["rank_interval"], e["metrics"]
        model = e["display_name"] + (" (not pre-registered)" if "not_preregistered" in e["flags"] else "")
        gap = "reference" if e["gap_to_reference"] is None else _ci3(e["gap_to_reference"]["headline"])
        expected = [str(e["rank"]), f"`{e['id']}`", model, e["params"] or "—", e["quant"] or "—", _ci3(m["headline"]),
                    _ci3(m["relation"]), _ci3(m["group"]), _ci3(m["binding"]), gap,
                    f"{ri['lo']}–{ri['hi']} (median {ri['median']})", str(e["runtime"]["build_wall_min"]), e["license"] or "—"]
        assert row == expected, e["id"]
    rep = {m: an["repeats"][m]["r2"] for m in an["models"] if "r2" in an["repeats"].get(m, {})}
    rr = _table(md, "| Model | Decisions byte-identical")
    names = {e["id"]: e["display_name"] for e in lb["models"]}
    assert len(rr) == len(rep) == 17
    for row, m in zip(rr, [e["id"] for e in lb["models"]]):
        r = rep[m]
        assert row == [names[m], "yes" if r["identical_decisions"] else "no", f"{r['identical']:.4f}",
                       str(round(r["flip_rate"] * 10716)), f"{r['delta_vs_published']:+.4f}",
                       f"{100 * r['flip_rate']:.2f}%"], m
    shifted = [m for m in an["models"] if an["robustness"]["models"][m]["version_shift"] is not None]
    vs = _table(md, "| Model | Headline change")
    assert len(vs) == len(shifted) == 15
    for row, m in zip(vs, [e["id"] for e in lb["models"] if e["id"] in shifted]):
        b = an["robustness"]["models"][m]["version_shift"]
        assert row == [names[m], f"{b['delta']:+.4f}", str(round(b["flip_rate"] * 10716)),
                       f"{100 * b['flip_rate']:.2f}%"], m
    base = _table(md, "| Baseline | Status")
    assert len(base) == len(lb["baselines"]) == 7
    for row, e in zip(base, lb["baselines"]):
        status = bl["baselines"][e["id"]]["status"]
        head = (_ci3(e["metrics"]["headline"]) if e["metrics"]["headline"] and e["metrics"]["headline"]["point"] is not None
                else f"Q_N {e['metrics']['q_n_repaired']['point']:.2f} (no headline)")
        source = "v0.1.0 files (exact, see the record)" if status == "carried" else "published in results/v0.2.0"
        assert row == [e["display_name"], status, head, source], e["id"]

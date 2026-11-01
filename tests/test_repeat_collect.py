"""results/v0.1.1/build_bundle.py: the repeat collector's checks, without any run folder (the run folders live on the machine that ran
them). Covers the dotted model ids in the tag parsing, the progress-log pairing, the command comparison against the published run
(on the committed repeat cells, so the check is tested on real records), the reasons a run is left out, and the refusal without a
published run."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_bundle_v011", ROOT / "results" / "v0.1.1" / "build_bundle.py")
BB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BB)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_tag_part_takes_the_tag_after_a_dotted_id():
    assert BB.tag_part("tev1_0.8b-q8_0", "tev1_0.8b-q8_0.r2") == "r2"
    assert BB.tag_part("tev1_0.8b-q8_0", "tev1_0.8b-q8_0.shuffle") == "shuffle"
    assert BB.tag_part("tev1_0.8b-q8_0", "tev1_0.8b-q8_0") is None  # the published run's folder is the bare id
    assert BB.tag_part("d3_9b-q8_0", "d3_9b-q8_0.vulkan_r2") == "vulkan_r2"
    with pytest.raises(ValueError):
        BB.tag_part("d3_9b-q8_0", "other-model.r2")


def test_last_rc_pairs_the_last_attempt_with_its_own_done(tmp_path):
    """The lines read are '== <key> <tag> start' and '== <key> <tag> done ... rc=<n>' (the format this collector reads; the
    progress log itself is written on the machine that ran the builds). An unfinished later attempt leaves no exit code."""
    log = tmp_path / "progress.log"
    log.write_text("\n".join([
        "== tev1_0.8b-q8_0 r2 start",
        "== tev1_0.8b-q8_0 r2 done rc=0",
        "== tev1_0.8b-q8_0 r2 start",  # a later attempt that never finished
        "== tev1_0.8b-q8_0 r3 start",
        "== tev1_0.8b-q8_0 r3 done rc=3",
        "== tev1_0.8b-q8_0 start",     # the published run
        "== tev1_0.8b-q8_0 done rc=0",
    ]) + "\n", encoding="utf-8")
    assert BB.last_rc(log, "tev1_0.8b-q8_0", "r2") is None
    assert BB.last_rc(log, "tev1_0.8b-q8_0", "r3") == 3
    assert BB.last_rc(log, "tev1_0.8b-q8_0", None) == 0
    assert BB.last_rc(tmp_path / "absent.log", "tev1_0.8b-q8_0", None) is None


def test_without_floor_removes_the_floor_option_and_its_value():
    flags = ["--top-k", "24", "--relation-floor", "0.7", "--batch-size", "128"]
    assert BB.without_floor(flags) == ["--top-k", "24", "--batch-size", "128"]


def test_committed_repeat_cells_match_the_published_command_and_the_prereg_floor():
    """Every committed repeat (r2, r3, shuffle, Vulkan) records the published run's engine, command and profile, and the relation
    floor the pre-registration gives its model."""
    prereg = _json(ROOT / "release" / "v0.1.0" / "record" / "prereg.json")
    floor_of = {key.replace(":", "_"): float(entry["relation_floor"]) for key, entry in prereg["models"].items()}
    runs = _json(ROOT / "results" / "v0.1.1" / "repeats" / "runs.json")
    assert runs["runs"]
    for rec in runs["runs"]:
        if rec["tag"] == "r1":
            continue
        cell = _json(ROOT / rec["cell"])["cell"]
        published = _json(ROOT / "results" / "v0.1.0" / "cells" / f"{rec['id']}.json")["cell"]
        assert BB.command_mismatches(cell, published, floor_of[rec["id"]]) == [], rec["cell"]


def test_engine_constant_is_the_one_the_cells_record():
    cell = _json(ROOT / "results" / "v0.1.1" / "repeats" / "cells" / "d3_9b-q8_0.r2.json")["cell"]
    assert BB.ENGINE_SHA256 == cell["runtime"]["engine"]["sha256"]


def test_command_mismatches_name_each_difference():
    published = _json(ROOT / "results" / "v0.1.0" / "cells" / "d3_9b-q8_0.json")["cell"]
    floor = float(next(v["relation_floor"] for k, v in _json(ROOT / "release" / "v0.1.0" / "record" / "prereg.json")["models"].items()
                       if k == "d3:9b-q8_0"))
    assert BB.command_mismatches(json.loads(json.dumps(published)), published, floor) == []
    wrong_floor = BB.command_mismatches(json.loads(json.dumps(published)), published, floor + 0.1)
    assert any("relation floor" in m for m in wrong_floor)
    engine = json.loads(json.dumps(published))
    engine["runtime"]["engine"]["sha256"] = "00"
    assert any("engine" in m for m in BB.command_mismatches(engine, published, floor))
    command = json.loads(json.dumps(published))
    command["build_flags"] = [("25" if w == "24" else w) for w in command["build_flags"]]
    assert any("build flags" in m for m in BB.command_mismatches(command, published, floor))
    profile = json.loads(json.dumps(published))
    profile["profile"] = "profiles/other.json"
    assert any("profile" in m for m in BB.command_mismatches(profile, published, floor))


def test_path_separators_alone_are_the_same_command():
    """The shuffle cells on Windows record the same files with backslashes (fixtures\\mentat_hard_test\\candidates.jsonl, profiles\\...);
    their profile hash, engine, top_k, batch and floor are the published run's, so the command matches."""
    published = _json(ROOT / "results" / "v0.1.0" / "cells" / "d3_27b-q8_0.json")["cell"]
    windows = json.loads(json.dumps(published))
    windows["build_flags"] = [w.replace("/", "\\") if "/" in w else w for w in published["build_flags"]]
    windows["profile"] = published["profile"].replace("/", "\\")
    assert BB.command_mismatches(windows, published, float(published["build_flags"][published["build_flags"].index("--relation-floor") + 1])) == []
    changed = json.loads(json.dumps(windows))
    changed["runtime"]["profile_sha256"] = "00"  # a different profile content is still refused
    assert any("profile" in m for m in BB.command_mismatches(changed, published, 0.0010576248168946423))


def test_run_problem_names_each_reason(tmp_path):
    cdir = tmp_path / "runs" / "x" / "m.shuffle"
    assert BB.run_problem(cdir, 0) == "no run directory"
    cdir.mkdir(parents=True)
    assert BB.run_problem(cdir, 0).startswith("missing cell.json")
    (cdir / "submission" ).mkdir()
    (cdir / "submission.build").mkdir()
    (cdir / "cell.json").write_text(json.dumps({"status": "complete"}), encoding="utf-8")
    (cdir / "build_summary.json").write_text("{}", encoding="utf-8")
    (cdir / "submission" / "build_policy.json").write_text("{}", encoding="utf-8")
    (cdir / "submission.build" / "decisions.jsonl").write_text("", encoding="utf-8")
    assert BB.run_problem(cdir, 0) == "missing m.shuffle.meta/child_stats.json"
    meta = cdir.parent / "m.shuffle.meta"
    meta.mkdir()
    (meta / "child_stats.json").write_text("{}", encoding="utf-8")
    assert BB.run_problem(cdir, 0) is None
    assert BB.run_problem(cdir, None).startswith("no exit code")
    assert BB.run_problem(cdir, 1) == "exit code 1 (progress.log)"
    (cdir / "cell.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    assert BB.run_problem(cdir, 0).startswith("cell not complete")


def test_collect_refuses_without_the_published_run(tmp_path):
    """No published run in the run folders: the collection stops with nothing written (the published run anchors every check)."""
    # load_v010() executes a fresh copy of the v0.1.0 builder each time, so match the exception by its class name
    with pytest.raises(Exception) as caught:
        BB.collect_repeats(tmp_path)
    assert type(caught.value).__name__ == "CheckError" and "published run" in str(caught.value)
    assert json.loads((ROOT / "results" / "v0.1.1" / "repeats" / "runs.json").read_text(encoding="utf-8"))["runs"]

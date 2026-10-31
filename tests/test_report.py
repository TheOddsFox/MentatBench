"""report.py on synthetic run directories: the rows, the JSON and markdown outputs, optional parts, and the usage error."""

import hashlib
import json

import pytest

from mentat_bench import report
from mentat_bench.scoring import FAMILIES


def _score(q_n, weighted=None, anchor=None, active=None):
    score = {
        "Q_N": q_n,
        "families": {f: {"queries": 10, "mean_utility": q_n / 100, "exact": 3} for f in FAMILIES},
        "families_missing": [],
        "queries": 40,
        "exact_query_success": 0.25,
        "false_entailment": 2,
        "false_refutation": 1,
        "per_query": {},
        "anchor": anchor or {"queries": 0, "Q_N": None, "status": "not_applicable"},
        "active": active or {"queries": 0, "Q_N": None, "status": "not_applicable"},
    }
    if weighted is not None:
        score["weighted"] = {"Q_N": weighted, "families": {}, "false_entailment": 1.5, "false_refutation": 0.5}
    return score


def _run(root, name, score, build=None, cell=None, usage=None, policy=None):
    run = root / name
    run.mkdir(parents=True)
    (run / "score.json").write_text(json.dumps(score), encoding="utf-8")
    if build is not None:
        (run / "build_summary.json").write_text(json.dumps(build), encoding="utf-8")
    if cell is not None:
        (run / "cell.json").write_text(json.dumps(cell), encoding="utf-8")
    if usage is not None or policy is not None:
        (run / "submission").mkdir(exist_ok=True)
    if usage is not None:
        (run / "submission" / "usage.json").write_text(json.dumps(usage), encoding="utf-8")
    if policy is not None:
        (run / "submission" / "build_policy.json").write_text(json.dumps(policy), encoding="utf-8")
    return run


def _usage():
    calls = [
        {"model": "qwen-0.5b", "kind": "local", "tokens_in": 100, "tokens_out": 10, "cost_usd": 0.0, "seconds": 1.5,
         "items": 64, "gpu_seconds": 0.8, "retry": False},
        {"model": "qwen-0.5b", "kind": "local", "tokens_in": 20, "tokens_out": 2, "cost_usd": 0.0, "seconds": 0.5,
         "items": 2, "gpu_seconds": 0.2, "retry": True},
    ]
    return {"calls": calls, "wall_seconds": 12.25, "peak_rss_mb": 512.5}


def test_rows_collect_score_build_usage_and_policy(tmp_path):
    run = _run(tmp_path, "2026-10-18-qwen", _score(42.5, weighted=40.0, anchor=_block(55.0), active={
        "queries": 0, "Q_N": None, "status": "not_applicable"}),
        build={"blocking_recall": 0.985, "group_recall": 0.5, "candidate_pairs": 900, "pairs_derived": 30,
               "relations_committed": 870},
        cell={"fixture": "toy_native", "adapter": "local"}, usage=_usage(),
        policy={"adapter": "local", "complement_dedup": True, "top_k": 64, "batch_size": 64})
    row = report.run_row(run)
    assert row["run"] == "2026-10-18-qwen" and row["fixture"] == "toy_native" and row["adapter"] == "local"
    assert row["model_id"] == "qwen-0.5b"  # no hello model in the policy: the usage model names
    assert row["Q_N"] == 42.5 and row["closure"] == pytest.approx(0.425) and row["partition"] == pytest.approx(0.425)
    assert row["false_entailment"] == 2 and row["false_refutation"] == 1 and row["exact_query_success"] == 0.25
    assert row["weighted_Q_N"] == 40.0 and row["anchor_Q_N"] == 55.0 and row["active_Q_N"] == "not_applicable"
    assert row["blocking_recall"] == 0.985 and row["group_recall"] == 0.5
    assert row["pairs_derived"] == 30 and row["relations_committed"] == 870 and row["candidate_pairs"] == 900
    assert row["items"] == 66 and row["calls"] == 2 and row["gpu_seconds"] == 1.0
    assert row["wall_seconds"] == 12.25 and row["peak_rss_mb"] == 512.5 and row["cost_usd"] == 0.0
    assert row["complement_dedup"] is True and row["top_k"] == 64 and row["batch_size"] == 64


def test_a_cell_whose_later_step_failed_shows_incomplete_though_its_build_completed(tmp_path):
    """The build summary says complete; the cell's status (a failed score) is the record, so the row shows incomplete."""
    build = {"blocking_recall": 0.985, "group_recall": 0.5}
    failed = _run(tmp_path, "failed-score", _score(40.0), build=build, cell={
        "status": "incomplete", "incomplete": True, "failed_step": "score", "steps": {"build": 0, "evaluate": 0, "score": 2}})
    done = _run(tmp_path, "done", _score(40.0), build=build, cell={"status": "complete", "incomplete": False, "steps": {}})
    assert report.run_row(failed)["status"] == "incomplete"
    assert report.run_row(done)["status"] == "complete"


def test_a_malformed_registry_refuses_the_report_with_its_reason(tmp_path, monkeypatch, capsys):
    """Not every extra row is taken as stale: the registry itself is refused, whatever the rows hold."""
    registry = tmp_path / "VERSIONS.json"
    registry.write_text(json.dumps({"entries": [{"name": "toy_native_v2", "version": "2", "status": "active",
                                                 "content_hash": "h2"}]}), encoding="utf-8")  # no hashes, change_log, superseded_by
    run = _extra_run(tmp_path, "extra", "h2")
    with pytest.raises(ValueError, match="registry is malformed: entry 0 .*field 'hashes'"):
        report.run_row(run, registry)
    real_run_row = report.run_row
    monkeypatch.setattr(report, "run_row", lambda run_dir: real_run_row(run_dir, registry))
    assert report.main(["--runs", str(run)]) == 2
    err = capsys.readouterr().err
    assert "registry is malformed" in err and "field 'hashes'" in err


def _block(q_n):
    return {"queries": 10, "Q_N": q_n, "status": "ok"}


def test_main_writes_json_and_markdown_and_prints_nothing_else(tmp_path, capsys):
    run_a = _run(tmp_path, "a", _score(10.0))
    run_b = _run(tmp_path, "b", _score(20.0), usage=_usage())
    assert report.main(["--runs", str(run_a), str(run_b), "--json", str(tmp_path / "out.json"),
                        "--md", str(tmp_path / "out.md")]) == 0
    assert capsys.readouterr().out == ""
    doc = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert [row["run"] for row in doc["runs"]] == ["a", "b"]
    assert doc["runs"][0]["items"] is None and doc["runs"][1]["items"] == 66  # no usage: empty columns
    markdown = (tmp_path / "out.md").read_text(encoding="utf-8")
    lines = markdown.strip().splitlines()
    assert lines[0].startswith("| run |") and set(lines[1]) <= set("|-")
    table = [line for line in lines if line.startswith("| ")]  # the header and the rows (the legend follows)
    assert len(table) == 3 and table[1].startswith("| a |") and "| - |" in table[1]
    assert "strict Q_N is 0.0 for a repaired base" in markdown


def _hex(tag):
    """A SHA-256 hex digest standing for a test's content hash (the registry requires lowercase hex digests)."""
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _registry(tmp_path, content_hash, status="active", name="toy_native_v2"):
    """A registry file with one entry (version 2) of `name` whose content hash is `content_hash`."""
    path = tmp_path / f"VERSIONS_{status}.json"
    entry = {"name": name, "version": "2", "status": status, "content_hash": _hex(content_hash), "hashes": {}, "change_log": [],
             "superseded_by": None}
    path.write_text(json.dumps({"entries": [entry]}), encoding="utf-8")
    return path


def _extra_run(root, name, stamp_hash, cell=None):
    """A run whose score_extra.json (Q_N 12.5) was scored under the version with `stamp_hash`."""
    run = _run(root, name, _score(40.0), cell=cell or {"fixture": "toy_native_v2"})
    if stamp_hash is not None:
        stamp = {"name": "toy_native_v2", "version": "2", "status": "active", "content_hash": _hex(stamp_hash)}
        (run / "score_extra.json").write_text(json.dumps({"Q_N": 12.5, "fixture_stamp": stamp}), encoding="utf-8")
    return run


def test_extra_q_n_comes_from_score_extra_when_it_is_the_active_version(tmp_path):
    registry = _registry(tmp_path, "h2")
    with_extra = _extra_run(tmp_path, "extra", "h2")
    plain = _run(tmp_path, "plain", _score(40.0))
    rows = [report.run_row(with_extra, registry), report.run_row(plain, registry)]
    assert rows[0]["extra_Q_N"] == 12.5 and rows[0]["extra_status"] == "ok"
    assert rows[1]["extra_Q_N"] is None and rows[1]["extra_status"] is None
    assert "| extra Q_N |" in report.markdown(rows).splitlines()[0]
    assert "| 12.50 |" in report.markdown(rows[:1])


def test_extra_q_n_is_stale_when_scored_under_a_version_that_is_not_active(tmp_path):
    registry = _registry(tmp_path, "h2")  # version 2 is active; the run's score_extra.json holds an older hash
    row = report.run_row(_extra_run(tmp_path, "old", "h1"), registry)
    assert row["extra_Q_N"] == "stale" and row["extra_status"] == "stale" and row["extra_Q_N_recorded"] == 12.5
    assert "| stale |" in report.markdown([row])
    withdrawn = _registry(tmp_path, "h2", status="withdrawn")  # no active version of the name at all
    assert report.run_row(_extra_run(tmp_path, "gone", "h2"), withdrawn)["extra_Q_N"] == "stale"


def test_extra_q_n_is_stale_when_the_score_has_no_fixture_stamp(tmp_path):
    registry = _registry(tmp_path, "h2")
    run = _run(tmp_path, "nostamp", _score(40.0))
    (run / "score_extra.json").write_text(json.dumps({"Q_N": 12.5}), encoding="utf-8")
    assert report.run_row(run, registry)["extra_Q_N"] == "stale"


def test_a_failed_or_unfinished_extra_pass_is_shown_in_its_column(tmp_path):
    registry = _registry(tmp_path, "h2")
    failed = _extra_run(tmp_path, "failed", "h2", cell={"fixture": "toy_native_v2", "extra_status": "failed"})
    not_run = _run(tmp_path, "not_run", _score(40.0), cell={"fixture": "toy_native_v2", "extra_status": "not_run"})
    ran_no_score = _run(tmp_path, "missing", _score(40.0), cell={"fixture": "toy_native_v2", "extra_status": "ok"})
    rows = [report.run_row(run, registry) for run in (failed, not_run, ran_no_score)]
    assert [row["extra_Q_N"] for row in rows] == ["failed", "not_run", "missing"]
    assert [row["extra_status"] for row in rows] == ["failed", "not_run", "missing"]


def test_top_k_and_batch_size_come_from_the_policy_or_else_the_cell(tmp_path):
    registry = _registry(tmp_path, "h2")
    run = _run(tmp_path, "cell_only", _score(40.0), cell={"fixture": "toy_native", "top_k": 54, "batch_size": 1})
    row = report.run_row(run, registry)
    assert row["top_k"] == 54 and row["batch_size"] == 1
    policed = _run(tmp_path, "policy", _score(40.0), cell={"top_k": 128},
                   policy={"adapter": "local", "top_k": 24, "batch_size": 64})
    assert report.run_row(policed, registry)["top_k"] == 24


def test_stdout_markdown_when_no_md_path(tmp_path, capsys):
    run = _run(tmp_path, "solo", _score(33.333))
    assert report.main(["--runs", str(run)]) == 0
    out = capsys.readouterr().out
    assert "| solo |" in out and "33.33" in out


def test_missing_score_is_a_usage_error(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert report.main(["--runs", str(tmp_path / "empty")]) == report.EXIT_USAGE
    assert "no readable score.json" in capsys.readouterr().err


def test_malformed_usage_is_a_usage_error(tmp_path, capsys):
    run = _run(tmp_path, "bad", _score(1.0), usage={"calls": ["not an object"]})
    assert report.main(["--runs", str(run)]) == report.EXIT_USAGE
    assert capsys.readouterr().err


def test_a_re_run_under_the_active_version_is_preferred_to_a_stale_score_extra(tmp_path):
    registry = _registry(tmp_path, "h2")  # version 2 is active
    run = _extra_run(tmp_path, "rerun", "h1")  # score_extra.json: the superseded version 1 (Q_N 12.5)
    rerun = run / "extra_toy_native_v2_v2"
    rerun.mkdir()
    stamp = {"name": "toy_native_v2", "version": "2", "status": "active", "content_hash": _hex("h2")}
    (rerun / "score_extra.json").write_text(json.dumps({"Q_N": 88.0, "fixture_stamp": stamp}), encoding="utf-8")
    row = report.run_row(run, registry)
    assert row["extra_Q_N"] == 88.0 and row["extra_status"] == "ok"
    assert row["extra_Q_N_recorded"] == 12.5  # the run's own score_extra.json, not the re-run's 88.0
    assert row["extra_from"] == "extra_toy_native_v2_v2/score_extra.json"  # the row names the file it reads
    old = report.run_row(_extra_run(tmp_path, "plain_stale", "h1"), registry)
    assert old["extra_Q_N"] == "stale" and old["extra_from"] == "score_extra.json"


def _speed_usage():
    """Six calls in build order: bindings (one retried), groups, relations (one retried)."""
    def call(items, seconds, retry=False):
        return {"model": "qwen-0.5b", "kind": "local", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "seconds": seconds,
                "items": items, "gpu_seconds": 0.1, "retry": retry}
    return {"calls": [call(64, 1.5), call(2, 0.5, retry=True), call(10, 0.25), call(100, 2.0), call(50, 1.0),
                      call(30, 0.5, retry=True)], "wall_seconds": 9.0, "peak_rss_mb": 100.0}


SPEED_BUILD = {"phase_call_seconds": {"bindings": 2.0, "groups": 0.25, "relations": 3.5},
               "phase_seconds": {"bindings": 2.5, "blocking": 0.1, "groups": 0.3, "relations": 4.0}}


def test_speed_is_items_per_second_per_phase_and_call_latency(tmp_path):
    run = _run(tmp_path, "speed", _score(40.0), build=SPEED_BUILD, usage=_speed_usage())
    row = report.run_row(run)
    assert row["bindings_items_per_s"] == 33.0 and row["groups_items_per_s"] == 40.0
    assert row["relations_items_per_s"] == 51.43  # 180 charged items over 3.5 s
    assert row["relations_wall_seconds"] == 4.0
    assert row["call_p50_s"] == 0.5 and row["call_p95_s"] == 2.0 and row["items_per_call"] == 42.67
    assert row["phases"]["bindings"] == {"calls": 2, "items": 66, "seconds": 2.0, "items_per_s": 33.0,
                                         "items_per_call": 33.0, "p50_s": 0.5, "p95_s": 1.5}
    assert row["phases"]["groups"] == {"calls": 1, "items": 10, "seconds": 0.25, "items_per_s": 40.0,
                                       "items_per_call": 10.0, "p50_s": 0.25, "p95_s": 0.25}
    assert row["phases"]["relations"]["items"] == 180 and row["phases"]["relations"]["p50_s"] == 1.0
    assert row["phases"]["relations"]["p95_s"] == 2.0 and row["phases"]["relations"]["items_per_call"] == 60.0
    markdown = report.markdown([row]).splitlines()
    header, values = ([c.strip() for c in line.strip("|").split("|")] for line in (markdown[0], markdown[2]))
    assert header[-17:-10] == ["bind items/s", "grp items/s", "rel items/s", "call p50 s", "call p95 s", "items/call",
                           "rel wall s"]
    assert values[-17:-10] == ["33.00", "40.00", "51.43", "0.50", "2.00", "42.67", "4.00"]


def test_speed_is_empty_where_its_data_is_missing(tmp_path):
    no_usage = report.run_row(_run(tmp_path, "no_usage", _score(40.0), build=SPEED_BUILD))
    assert no_usage["bindings_items_per_s"] is None and no_usage["call_p50_s"] is None
    assert no_usage["phases"] is None and no_usage["relations_wall_seconds"] == 4.0
    no_phases = report.run_row(_run(tmp_path, "no_phases", _score(40.0), build={}, usage=_speed_usage()))
    assert no_phases["relations_items_per_s"] is None and no_phases["phases"] is None
    assert no_phases["call_p50_s"] == 0.5 and no_phases["items_per_call"] == 42.67  # per call, no phase split needed
    unmatched = dict(SPEED_BUILD, phase_call_seconds={"bindings": 2.0, "groups": 0.25, "relations": 9.0})
    split = report.run_row(_run(tmp_path, "unmatched", _score(40.0), build=unmatched, usage=_speed_usage()))
    assert split["bindings_items_per_s"] is None and split["phases"] is None and split["call_p95_s"] == 2.0
    markdown = report.markdown([no_usage]).splitlines()
    assert [c.strip() for c in markdown[2].strip("|").split("|")][-17:-10] == ["-"] * 6 + ["4.00"]  # the build's relation wall (the ten decision columns follow)


def test_speed_assigns_phases_by_op_when_every_call_names_one(tmp_path):
    """The ops are interleaved, which no time order could split; no phase_call_seconds are needed for it."""
    def call(op, items, seconds):
        return {"model": "qwen-0.5b", "kind": "local", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "seconds": seconds,
                "items": items, "retry": False, "op": op}
    usage = {"calls": [call("decide_relation", 10, 1.0), call("decide_binding", 4, 0.5), call("decide_relation", 20, 2.0),
                       call("decide_group", 6, 0.25), call("decide_binding", 2, 0.5)], "wall_seconds": 5.0, "peak_rss_mb": 1.0}
    row = report.run_row(_run(tmp_path, "by_op", _score(40.0), usage=usage))
    assert row["relations_items_per_s"] == 10.0 and row["bindings_items_per_s"] == 6.0
    assert row["groups_items_per_s"] == 24.0
    assert row["phases"]["relations"]["calls"] == 2 and row["phases"]["bindings"]["items"] == 6
    assert row["call_p50_s"] == 0.5 and row["call_p95_s"] == 2.0 and row["items_per_call"] == 8.4


def test_speed_falls_back_to_time_when_one_call_has_no_op(tmp_path):
    """One call without an op (usage from before the op) makes the whole run split by time, as the older runs do."""
    usage = _speed_usage()
    usage["calls"][0]["op"] = "decide_binding"  # the other five calls name none
    row = report.run_row(_run(tmp_path, "mixed", _score(40.0), build=SPEED_BUILD, usage=usage))
    assert row["bindings_items_per_s"] == 33.0 and row["groups_items_per_s"] == 40.0
    assert row["relations_items_per_s"] == 51.43 and row["phases"]["relations"]["calls"] == 3


def _call(seconds, items, op=None, **extra):
    call = {"model": "qwen-0.5b", "kind": "local", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0, "seconds": seconds,
            "items": items, "retry": False, **extra}
    return {**call, "op": op} if op else call


def test_torn_placeholder_rows_are_left_out_and_need_no_op_of_their_own(tmp_path):
    usage = {"calls": [_call(0.5, 4, "decide_binding"), _call(0.0, 64, torn=True), _call(1.0, 10, "decide_relation")],
             "wall_seconds": 5.0, "peak_rss_mb": 1.0}
    row = report.run_row(_run(tmp_path, "torn", _score(40.0), usage=usage))
    assert row["bindings_items_per_s"] == 8.0 and row["relations_items_per_s"] == 10.0
    assert row["items_per_call"] == 7.0 and row["phases"]["bindings"]["calls"] == 1 and row["phases"]["relations"]["calls"] == 1


def test_speed_is_not_reported_for_calls_too_fast_to_time(tmp_path):
    """A mean call under 1 ms gives no rate and no percentile, by op or by time; the counts stay."""
    by_op = {"calls": [_call(0.0002, 64, "decide_binding"), _call(0.0002, 64, "decide_relation")],
             "wall_seconds": 1.0, "peak_rss_mb": 1.0}
    row = report.run_row(_run(tmp_path, "fast_op", _score(40.0), build={"phase_seconds": {"relations": 0.0002}}, usage=by_op))
    assert row["bindings_items_per_s"] is None and row["relations_items_per_s"] is None
    assert row["call_p50_s"] is None and row["call_p95_s"] is None and row["items_per_call"] == 64.0
    assert row["phases"]["bindings"] == {"calls": 1, "items": 64, "seconds": 0.0, "items_per_s": None,
                                         "items_per_call": 64.0, "p50_s": None, "p95_s": None}
    by_time = {"calls": [_call(0.0002, 64), _call(0.0002, 64)], "wall_seconds": 1.0, "peak_rss_mb": 1.0}
    timed_out = report.run_row(_run(tmp_path, "fast_time", _score(40.0), build={"phase_call_seconds": {"bindings": 0.0004}},
                                    usage=by_time))
    assert timed_out["phases"] is None and timed_out["bindings_items_per_s"] is None and timed_out["call_p50_s"] is None


# score_decisions.json (decision_scoring.score): the decision columns, appended after the existing ones. The headline is the
# relation MCC (owner decision 2026-10-29). Each MCC is read from its op's block, so a file in the old layout gives the same
# columns (test_an_old_layout_file_gives_the_same_columns).
DECISIONS = {
    "headline": {"metric": "relation_mcc", "relation_mcc": 0.8, "secondary": {"binding_mcc": 0.3},
                 "diagnostics": {"group_mcc": 0.4, "reading_mcc": None}},
    "ops": {
        "decide_relation": {"mcc": 0.8, "macro_f1": 0.7, "brier": {"value": 0.2, "n": 5, "excluded": 0},
                            "ece": {"value": 0.05, "n": 5, "bins": []},
                            "by_split": {"seen": {"mcc": 0.9}, "held_out": {"mcc": 0.6, "n": 3}}},
        "decide_group": {"mcc": 0.4, "macro_f1": 0.5, "chain_order": {"n": 0, "correct": 0, "accuracy": None}},
        "decide_binding": {"mcc": 0.3, "macro_f1": 0.6},
    },
    "counts": {"labels": 9},
}
OLD_LAYOUT = {"headline": {"mcc_mean": 0.5, "components": {"relations": 0.8, "groups": 0.4, "bindings": 0.3},
                           "ops_counted": ["relations", "groups", "bindings"]},
              "ops": DECISIONS["ops"], "counts": {"labels": 9}}
DECISION_HEADERS = ("| headline MCC | headline metric | rel MCC (headline v1-v2, part v3) | bind MCC (secondary) | "
                    "grp MCC (diagnostic v1-v2, part v3) | read MCC (diagnostic) | rel macro-F1 (diagnostic) | rel Brier (diagnostic) | "
                    "rel ECE (diagnostic) | held-out rel MCC (diagnostic) |")


def test_decision_columns_come_from_score_decisions_and_the_json_keeps_every_block(tmp_path, capsys):
    run = _run(tmp_path, "decided", _score(40.0))
    (run / "score_decisions.json").write_text(json.dumps(DECISIONS), encoding="utf-8")
    row = report.run_row(run)
    assert (row["decision_rel_mcc"], row["decision_bind_mcc"], row["decision_grp_mcc"], row["decision_read_mcc"]) == (
        0.8, 0.3, 0.4, None)
    assert row["decision_rel_macro_f1"] == 0.7  # the relation op's own macro-F1: no mean over groups or readings
    assert (row["decision_rel_brier"], row["decision_rel_ece"], row["decision_held_out_rel_mcc"]) == (0.2, 0.05, 0.6)
    assert row["decision_scoring"] == DECISIONS  # the JSON rows keep the full per-op blocks
    table = report.markdown([row]).splitlines()
    assert table[0].endswith(DECISION_HEADERS)
    assert table[2].endswith("| 0.80 | relation_mcc | 0.80 | 0.30 | 0.40 | - | 0.70 | 0.20 | 0.05 | 0.60 |")
    assert report.main(["--runs", str(run), "--json", str(tmp_path / "out.json")]) == 0
    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["runs"][0]
    assert written["decision_scoring"]["ops"]["decide_relation"]["by_split"]["held_out"]["mcc"] == 0.6
    capsys.readouterr()


def test_the_headline_column_is_the_relation_mcc_for_v1_v2_and_the_mean_for_v3(tmp_path):
    v3 = {**DECISIONS, "headline": {"metric": "relation_group_mcc", "relation_group_mcc": 0.6,
                                    "parts": {"relation_mcc": 0.8, "group_mcc": 0.4},
                                    "secondary": {"binding_mcc": 0.3}, "diagnostics": {"reading_mcc": None}}}
    run = _run(tmp_path, "v3", _score(40.0))
    (run / "score_decisions.json").write_text(json.dumps(v3), encoding="utf-8")
    row = report.run_row(run)
    assert (row["decision_headline"], row["decision_headline_metric"], row["decision_rel_mcc"]) == (0.6, "relation_group_mcc", 0.8)
    for layout in (DECISIONS, OLD_LAYOUT):  # version 2, and the old layout: the relation MCC is the headline
        (run / "score_decisions.json").write_text(json.dumps(layout), encoding="utf-8")
        assert report.run_row(run)["decision_headline"] == 0.8 and report.run_row(run)["decision_headline_metric"] == "relation_mcc"
    plain = report.run_row(_run(tmp_path, "plain", _score(40.0)))
    assert plain["decision_headline"] is None and plain["decision_headline_metric"] is None


def test_an_old_layout_file_gives_the_same_columns(tmp_path):
    run = _run(tmp_path, "old_layout", _score(40.0))
    (run / "score_decisions.json").write_text(json.dumps(OLD_LAYOUT), encoding="utf-8")
    row = report.run_row(run)
    assert (row["decision_rel_mcc"], row["decision_bind_mcc"], row["decision_grp_mcc"]) == (0.8, 0.3, 0.4)
    assert row["decision_rel_macro_f1"] == 0.7 and row["decision_held_out_rel_mcc"] == 0.6


def test_decision_columns_are_dashes_and_null_without_the_file_or_a_held_out_split(tmp_path):
    plain = report.run_row(_run(tmp_path, "plain", _score(40.0)))
    assert all(plain[key] is None for key in ("decision_rel_mcc", "decision_rel_macro_f1", "decision_held_out_rel_mcc"))
    assert plain["decision_scoring"] is None
    assert report.markdown([plain]).splitlines()[2].endswith("| - | - | - | - | - | - | - | - |")
    run = _run(tmp_path, "no_split", _score(40.0))
    no_split = {**DECISIONS, "ops": {**DECISIONS["ops"], "decide_relation": {
        **DECISIONS["ops"]["decide_relation"], "by_split": {"seen": {"mcc": 0.9}, "held_out": {"mcc": None, "n": 0}}}}}
    (run / "score_decisions.json").write_text(json.dumps(no_split), encoding="utf-8")
    assert report.run_row(run)["decision_held_out_rel_mcc"] is None  # an empty held-out split: null, not 0


def test_existing_columns_come_first_and_the_decision_columns_follow_them(tmp_path):
    header = report.markdown([report.run_row(_run(tmp_path, "head", _score(40.0)))]).splitlines()[0]
    assert header.startswith("| run | status | fixture | adapter | model | revision | Q_N (repaired, diagnostic) | strict Q_N (diagnostic) |")
    assert header.endswith(DECISION_HEADERS)
    assert [key for _, key in report.COLUMNS[-10:]] == [
        "decision_headline", "decision_headline_metric", "decision_rel_mcc", "decision_bind_mcc", "decision_grp_mcc",
        "decision_read_mcc", "decision_rel_macro_f1", "decision_rel_brier", "decision_rel_ece", "decision_held_out_rel_mcc"]



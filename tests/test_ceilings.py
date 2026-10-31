"""Lower-ceiling cells from a kept build (mentat_bench.ceilings) and the --keep-work hook of build.

The synthetic builds use a metered adapter (no model, no engine needed): they check the checkpoint history, the cumulative
usage and the finalized prefixes. The tiny synthetic scored cell (seal, evaluate, score) needs mb-engine and skips
without it; MB_REQUIRE_ENGINE=1 turns that skip into a failure.
"""

import hashlib
import json
from pathlib import Path

import pytest

from mentat_bench import ceilings, cli, engine, ir
from mentat_bench.adapters.base import AdapterError, usage
from mentat_bench.adapters.mock import EmptyAdapter
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

RELATIONS = ["NONE", "NONE", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "EQUIV", "UNRESOLVED"]
COST, GPU = 0.01, 0.5


def _h(*parts):
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest(), 16)


class Metered:
    """Deterministic decisions (as the fixed-harness tests use), each call charged COST USD and GPU seconds."""

    name = "metered"
    supports_groups = False

    def _charge(self):
        return {**usage(self.name), "cost_usd": COST, "gpu_seconds": GPU}

    def decide_binding(self, ctx):
        h = _h(ctx["id"])
        if h % 3 == 0:
            decision = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
        else:
            decision = {"status": "bound", "class": f"c{h % 4}", "negated": (h // 4) % 2 == 0, "missing": []}
        return decision, self._charge()

    def decide_relation(self, a, b):
        return RELATIONS[_h(a["id"], b["id"]) % len(RELATIONS)], self._charge()


def _records():
    recs = []
    for k in range(7):
        question = f"Will M{k} win the cup?"
        event = "E" if k < 4 else "F"
        recs.append({"id": f"v:m{k}:1", "question": question, "label": "Yes", "event_title": event})
        recs.append({"id": f"v:m{k}:2", "question": question, "label": "No", "event_title": event})
    return recs


def _build(tmp_path, keep=True):
    out = tmp_path / "out"
    fixed.build(_records(), Metered(), out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                keep_work=keep)
    return out, out.with_name("out.build")


def _profile_file(tmp_path, **changes):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({**DEFAULT_PROFILE, **changes}), encoding="utf-8")
    return path


def _fixture(tmp_path):
    """A fixture with no queries: enough for the ceilings CLI to find its gold directory."""
    root = tmp_path / "fixture"
    (root / "gold").mkdir(parents=True, exist_ok=True)
    (root / "queries.jsonl").write_text("", encoding="utf-8")
    (root / "corpus.jsonl").write_text("", encoding="utf-8")
    return root


def _scored_fixture(tmp_path):
    """A tiny complete fixture for the engine-backed ceiling path."""
    root = _fixture(tmp_path)
    records = _records()
    (root / "corpus.jsonl").write_text("".join(json.dumps({
        "id": row["id"], "text": row["question"], "label": row["label"], "event_title": row["event_title"]
    }) + "\n" for row in records), encoding="utf-8")
    ir.write_jsonl(root / "queries.jsonl", [{"qid": "c1", "family": "closure", "hyp": []}])
    ir.write_submission(root / "gold", [
        {"id": row["id"], "status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
        for row in records
    ], [], ["base", "alt"], {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0})
    ir.write_jsonl(root / "gold" / "answers.jsonl", [{
        "qid": "c1", "labels": {"ENTAILED": [], "REFUTED": [], "CONTINGENT": [],
                                  "UNRESOLVED": [row["id"] for row in records]}
    }])
    ir.write_jsonl(root / "gold" / "query_design.jsonl", [{"qid": "c1", "anchor": True}])
    return root


def test_keep_work_keeps_one_history_line_per_checkpoint(tmp_path):
    out, work = _build(tmp_path)
    lines = (work / fixed.HISTORY_FILE).read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 3
    assert json.loads(lines[-1]) == json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))


def test_without_keep_work_the_work_dir_is_removed_and_no_history_is_written(tmp_path):
    out, work = _build(tmp_path, keep=False)
    assert not work.exists()
    assert out.is_dir()


def test_cumulative_usage_follows_the_usage_log(tmp_path):
    out, work = _build(tmp_path)
    checkpoints = ceilings.history(work)
    totals = ceilings.cumulative(work, checkpoints)
    calls = json.loads((out / "usage.json").read_text(encoding="utf-8"))["calls"]
    assert totals[-1]["calls"] == len(calls)
    assert totals[-1]["spend_usd"] == pytest.approx(COST * len(calls))
    assert totals[-1]["gpu_seconds"] == pytest.approx(GPU * len(calls))
    for before, after in zip(totals, totals[1:]):
        assert before["calls"] <= after["calls"] and before["spend_usd"] <= after["spend_usd"]


def test_pick_takes_the_latest_checkpoint_within_the_ceiling():
    totals = [{"gpu_seconds": g} for g in (1.0, 1.0, 2.0, 3.0)]
    assert ceilings.pick(totals, "gpu_seconds", 2.5) == 2
    assert ceilings.pick(totals, "gpu_seconds", 3.0) == 3
    assert ceilings.pick(totals, "gpu_seconds", 0.5) is None


def test_a_checkpoint_cut_finalizes_to_the_build_prefix(tmp_path):
    out, work = _build(tmp_path)
    checkpoints = ceilings.history(work)
    totals = ceilings.cumulative(work, checkpoints)
    middle = len(checkpoints) // 2
    cut = tmp_path / "cut"
    fixed.finalize_checkpoint(ceilings._cut_copy(work, tmp_path / "scratch", checkpoints[middle]), cut,
                              dict(DEFAULT_PROFILE), max_relation_drop_frac=1.0)
    full_calls = json.loads((out / "usage.json").read_text(encoding="utf-8"))["calls"]
    cut_calls = json.loads((cut / "usage.json").read_text(encoding="utf-8"))["calls"]
    assert cut_calls == full_calls[: len(cut_calls)]
    assert len(cut_calls) == totals[middle]["calls"]
    last = tmp_path / "last"
    fixed.finalize_checkpoint(ceilings._cut_copy(work, tmp_path / "scratch2", checkpoints[-1]), last,
                              dict(DEFAULT_PROFILE), max_relation_drop_frac=1.0)
    for name in ("theory.json", "bindings.jsonl", "provenance.jsonl"):
        assert (last / name).read_bytes() == (out / name).read_bytes()


def test_a_ceiling_below_the_first_checkpoint_scores_zero(tmp_path, capsys):
    _, work = _build(tmp_path)
    cells = tmp_path / "cells"
    code = ceilings.run(work, "spend_usd", ["0"], _fixture(tmp_path), _profile_file(tmp_path), cells)
    assert code == 0
    cell = json.loads((cells / "spend_usd_0" / "cell.json").read_text(encoding="utf-8"))
    assert cell["status"] == "no_checkpoint_within_ceiling"
    assert not (cells / "spend_usd_0" / "submission").exists()
    assert "| 0.00 |" in (cells / "ceilings.md").read_text(encoding="utf-8")


def test_a_build_without_history_is_refused(tmp_path):
    _, work = _build(tmp_path, keep=False)
    assert ceilings.run(work, "spend_usd", ["1"], _fixture(tmp_path), _profile_file(tmp_path), tmp_path / "cells") == 2


def test_a_profile_that_differs_from_the_build_is_refused(tmp_path):
    out, work = _build(tmp_path)
    other = _profile_file(tmp_path, spend_ceiling_usd=5.0)
    assert ceilings.run(work, "spend_usd", ["1"], _fixture(tmp_path), other, tmp_path / "cells") == 2
    assert not (tmp_path / "cells").exists()


def test_the_cli_build_keeps_its_work_dir_only_with_keep_work(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(json.dumps({"id": r["id"], "text": r["question"], "label": r["label"],
                                          "event_title": r["event_title"]}) + "\n" for r in _records()), encoding="utf-8")
    kept, dropped = tmp_path / "kept", tmp_path / "dropped"
    base = ["build", "--adapter", "empty", "--corpus", str(corpus), "--checkpoint-every", "4"]
    assert cli.main([*base, "--out", str(kept), "--keep-work"]) == 0
    assert (kept.with_name("kept.build") / fixed.HISTORY_FILE).is_file()
    assert cli.main([*base, "--out", str(dropped)]) == 0
    assert not dropped.with_name("dropped.build").exists()


def test_the_cli_parser_takes_keep_work():
    args = cli.build_parser().parse_args(["build", "--adapter", "empty", "--corpus", "x", "--out", "y", "--keep-work"])
    assert args.keep_work is True
    assert cli.build_parser().parse_args(["build", "--adapter", "empty", "--corpus", "x", "--out", "y"]).keep_work is False


class EmptyMetered(EmptyAdapter):
    """An empty adapter (every binding unresolved), each call charged COST USD and GPU seconds."""

    name = "empty-metered"

    def _charge(self):
        return {**usage(self.name), "cost_usd": COST, "gpu_seconds": GPU}

    def decide_binding(self, ctx):
        decision, _ = super().decide_binding(ctx)
        return decision, self._charge()

    def decide_relation(self, a, b):
        relation, _ = super().decide_relation(a, b)
        return relation, self._charge()


@pytest.mark.skipif(not engine.engine_available(), reason="mb-engine not found (set MB_ENGINE)")
@pytest.mark.slow
def test_scored_cells_at_several_ceilings_on_a_tiny_fixture(tmp_path):
    from mentat_bench.report import run_row

    fixture = _scored_fixture(tmp_path)
    records = _records()
    out = tmp_path / "build"
    profile = _profile_file(tmp_path, spend_ceiling_usd=1000.0)  # well above the tiny build's spend
    fixed.build(records, EmptyMetered(), out, json.loads(profile.read_text(encoding="utf-8")), checkpoint_every=4,
                keep_work=True)
    work = out.with_name("build.build")
    total = ceilings.cumulative(work, ceilings.history(work))[-1]["gpu_seconds"]
    cells = tmp_path / "cells"
    code = ceilings.run(work, "gpu_seconds", [str(total / 2), str(total)], fixture, profile, cells)
    assert code == 0
    stopped = json.loads((cells / f"gpu_seconds_{str(total / 2).replace('.', 'p')}" / "cell.json").read_text(encoding="utf-8"))
    full = json.loads((cells / f"gpu_seconds_{str(total).replace('.', 'p')}" / "cell.json").read_text(encoding="utf-8"))
    assert stopped["status"] == "ceiling_stopped" and stopped["partial"] == ["ceiling_gpu_seconds"]
    assert full["partial"][0] == "ceiling_gpu_seconds"  # the build itself stopped at its spend ceiling
    assert full["status"] == "ceiling_reached_end"
    assert stopped["ceiling"]["usage"]["gpu_seconds"] <= total / 2
    assert stopped["steps"] == {"derive": 0, "seal": 0, "evaluate": 0, "score": 0}
    assert run_row(cells / f"gpu_seconds_{str(total / 2).replace('.', 'p')}")["status"] == "ceiling_stopped"
    assert run_row(cells / f"gpu_seconds_{str(total).replace('.', 'p')}")["status"] == "ceiling_reached_end"
    # the cell at the full ceiling is the build: the same theory, bindings and provenance bytes
    for name in ("theory.json", "bindings.jsonl", "provenance.jsonl"):
        assert (cells / f"gpu_seconds_{str(total).replace('.', 'p')}" / "submission" / name).read_bytes() == (out / name).read_bytes()
    assert (cells / "ceilings.md").is_file() and json.loads((cells / "ceilings.json").read_text(encoding="utf-8"))["rows"]


class FailingRelations(Metered):
    """Answers the first few relation calls, then fails every call (an adapter that gives up)."""

    def __init__(self):
        self.relation_calls = 0

    def decide_relation(self, a, b):
        self.relation_calls += 1
        if self.relation_calls > 6:
            raise AdapterError("child died")
        return super().decide_relation(a, b)


def test_a_checkpoint_of_an_adapter_fatal_build_is_not_sealed(tmp_path):
    out = tmp_path / "out"
    summary = fixed.build(_records(), FailingRelations(), out, dict(DEFAULT_PROFILE), checkpoint_every=2,
                          max_consecutive_errors=2, max_relation_drop_frac=1.0, keep_work=True)
    assert summary["adapter_fatal"] is True
    work = out.with_name("out.build")
    cells = tmp_path / "cells"
    code = ceilings.run(work, "spend_usd", ["1000"], _fixture(tmp_path), _profile_file(tmp_path), cells)
    assert code == 3
    cell = json.loads((cells / "spend_usd_1000" / "cell.json").read_text(encoding="utf-8"))
    assert cell["status"] == "incomplete" and cell["incomplete"] is True
    sub = cells / "spend_usd_1000" / "submission"
    assert (sub / cli.INCOMPLETE_FILE).is_file() and not (sub / "seal.json").exists()


class GroupedMetered(Metered):
    """Metered, with group decisions: an at_most_one over the first two members of each candidate."""

    supports_groups = True

    def decide_group(self, ctx):
        ids = [m["id"] for m in ctx["members"]]
        return ([{"kind": "at_most_one", "members": ids[:2]}] if len(ids) >= 2 else []), self._charge()


class KilledMetered(Metered):
    """Metered, and killed (KeyboardInterrupt, as a Ctrl-C) on the relation call after kill_after."""

    def __init__(self, kill_after):
        self.kill_after, self.relation_calls = kill_after, 0

    def decide_relation(self, a, b):
        self.relation_calls += 1
        if self.relation_calls > self.kill_after:
            raise KeyboardInterrupt("killed")
        return super().decide_relation(a, b)


class UnresolvedMetered(Metered):
    """Metered, with every relation UNRESOLVED: a batch of these appends no line to relations.jsonl. Same policy name,
    so a build can resume with it."""

    def decide_relation(self, a, b):
        return "UNRESOLVED", self._charge()


def test_resumed_build_cursors_are_exact_after_a_torn_tail(tmp_path):
    out, work = tmp_path / "out", tmp_path / "out.build"
    profile = dict(DEFAULT_PROFILE)
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), KilledMetered(kill_after=20), out, profile, checkpoint_every=4,
                    max_relation_drop_frac=1.0, keep_work=True)
    # a crash between a log write and its checkpoint leaves rows past the last checkpoint: a relation row (cut by the
    # resume), a complete paid call (a copy of the last usage row, which the resume charges), and a torn final usage row
    # (the first half of a row, no newline: cut, and charged as one item)
    with open(work / "relations.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"torn": true}\n')
    last_call = (work / "usage_calls.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    with open(work / "usage_calls.jsonl", "a", encoding="utf-8") as fh:
        fh.write(last_call + "\n")
        fh.write(last_call[: len(last_call) // 2])
    resumed = fixed.build(_records(), UnresolvedMetered(), out, profile, checkpoint_every=4, max_relation_drop_frac=1.0,
                          keep_work=True, resume=True)
    assert resumed["torn_usage_rows"] == 1
    last = ceilings.history(work)[-1]
    assert last["relations_bytes"] == (work / "relations.jsonl").stat().st_size
    assert last["usage_bytes"] == (work / "usage_calls.jsonl").stat().st_size
    ceilings.cumulative(work, ceilings.history(work))  # every checkpoint ends on a usage line


def test_a_full_replay_build_is_refused(tmp_path):
    """The oracle replays the theory from its extra_theory, not from checkpoints: its checkpoints carry no theory, so a
    lower ceiling would be an empty submission. Refused (exit 2) with the reason."""
    from mentat_bench.adapters.mock import OracleAdapter

    fixture = _scored_fixture(tmp_path)
    records = _records()
    out = tmp_path / "oracle"
    fixed.build(records, OracleAdapter(fixture / "gold"), out, dict(DEFAULT_PROFILE), checkpoint_every=4, keep_work=True)
    work = out.with_name("oracle.build")
    assert ceilings.history(work)[-1]["policy"]["groups_disabled_reason"] == "full_replay"
    cells = tmp_path / "cells"
    assert ceilings.run(work, "spend_usd", ["1"], fixture, _profile_file(tmp_path), cells) == 2
    assert not cells.exists()


def test_a_checkpoint_whose_policy_does_not_record_the_adapter_is_refused(tmp_path, monkeypatch):
    """Without the recorded adapter a full-replay build cannot be ruled out: refused (exit 2), whatever the policy holds."""
    fixture = _fixture(tmp_path)
    for policy in (None, {}, {"groups_disabled_reason": None}):
        monkeypatch.setattr(ceilings, "history", lambda work, policy=policy: [{"policy": policy}])
        cells = tmp_path / f"cells{len(list(tmp_path.iterdir()))}"
        assert ceilings.run(tmp_path, "spend_usd", ["1"], fixture, _profile_file(tmp_path), cells) == 2
        assert not cells.exists()


def test_a_profile_that_cannot_be_matched_is_refused_unless_given(tmp_path):
    """A work dir not named <out>.build has no profile.json beside it: --build-profile is then required, and must match."""
    _, work = _build(tmp_path)
    moved = work.with_name("renamed-work")
    work.rename(moved)
    profile = _profile_file(tmp_path)
    assert ceilings.run(moved, "spend_usd", ["0"], _fixture(tmp_path), profile, tmp_path / "refused") == 2
    other = tmp_path / "other.json"
    other.write_text(json.dumps({**DEFAULT_PROFILE, "spend_ceiling_usd": 5.0}), encoding="utf-8")
    assert ceilings.run(moved, "spend_usd", ["0"], _fixture(tmp_path), profile, tmp_path / "differs",
                        build_profile=other) == 2
    assert ceilings.run(moved, "spend_usd", ["0"], _fixture(tmp_path), profile, tmp_path / "matched",
                        build_profile=profile) == 0
    assert ceilings._profile_check(moved, dict(DEFAULT_PROFILE), profile) == "--build-profile"


def test_a_fixture_that_differs_from_the_build_is_refused(tmp_path):
    """cell.json beside a build records the corpus it ran on; a different corpus.jsonl is refused, a missing record is a
    warning."""
    _, work = _build(tmp_path)
    fixture = _fixture(tmp_path)
    cell = tmp_path / "cell.json"
    cell.write_text(json.dumps({"runtime": {"fixture": {"files": {"corpus.jsonl": "0" * 64}}}}), encoding="utf-8")
    assert ceilings.run(work, "spend_usd", ["0"], fixture, _profile_file(tmp_path), tmp_path / "differs") == 2
    cell.unlink()
    assert ceilings.run(work, "spend_usd", ["0"], fixture, _profile_file(tmp_path), tmp_path / "unrecorded") == 0


def test_duplicate_ceilings_are_refused(tmp_path, capsys):
    _, work = _build(tmp_path)
    code = ceilings.main(["--work", str(work), "--metric", "spend_usd", "--ceilings", "1,1.0", "--fixture",
                          str(_fixture(tmp_path)), "--profile", str(_profile_file(tmp_path)), "--out",
                          str(tmp_path / "dup")])
    assert code == 2 and "duplicate" in capsys.readouterr().err


def test_the_billed_usage_includes_the_call_that_crosses_the_ceiling(tmp_path):
    """A ceiling between two checkpoints: the committed usage stops at the earlier one, the billed usage adds the first call
    after it that takes spend past the ceiling (its cost is charged, its decision discarded)."""
    out, work = _build(tmp_path)
    checkpoints = ceilings.history(work)
    totals = ceilings.cumulative(work, checkpoints)
    index = next(i for i in range(len(totals) - 1) if totals[i + 1]["spend_usd"] > totals[i]["spend_usd"])
    ceiling = totals[index]["spend_usd"] + COST / 2
    cells = tmp_path / "cells"
    ceilings.run(work, "spend_usd", [repr(ceiling)], _fixture(tmp_path), _profile_file(tmp_path), cells)
    cell = json.loads((cells / f"spend_usd_{_label(repr(ceiling))}" / "cell.json").read_text(encoding="utf-8"))
    info = cell["ceiling"]
    assert info["checkpoint"] == index
    assert info["usage"]["calls"] == totals[index]["calls"]
    assert info["billed_calls"] == totals[index]["calls"] + 1
    assert info["billed_spend_usd"] == pytest.approx(totals[index]["spend_usd"] + COST)
    assert info["billed_spend_usd"] > ceiling


def _label(text):
    return text.replace(".", "p")


def test_a_derived_cut_of_a_group_checkpoint_matches_the_build(tmp_path):
    """Group decisions are checkpointed too: the cut at a group checkpoint finalizes, and the last cut is the build."""
    out = tmp_path / "out"
    fixed.build(_records(), GroupedMetered(), out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                keep_work=True)
    work = out.with_name("out.build")
    checkpoints = ceilings.history(work)
    grouped = next(i for i, c in enumerate(checkpoints) if c.get("groups_committed", 0) > 0)
    cut = tmp_path / "group-cut"
    fixed.finalize_checkpoint(ceilings._cut_copy(work, tmp_path / "scratch", checkpoints[grouped]), cut,
                              dict(DEFAULT_PROFILE), max_relation_drop_frac=1.0)
    theory = json.loads((cut / "theory.json").read_text(encoding="utf-8"))
    assert any(c["id"].startswith("g") for c in theory["constraints"])
    last = tmp_path / "group-last"
    fixed.finalize_checkpoint(ceilings._cut_copy(work, tmp_path / "scratch2", checkpoints[-1]), last,
                              dict(DEFAULT_PROFILE), max_relation_drop_frac=1.0)
    for name in ("theory.json", "bindings.jsonl", "provenance.jsonl"):
        assert (last / name).read_bytes() == (out / name).read_bytes()


def test_the_derived_theory_is_not_empty_and_its_middle_is_a_prefix(tmp_path):
    """The metered adapter emits real constraints: the full theory is not empty, and a mid-run cut holds fewer of them
    with the same bindings (every binding is decided before the first relation checkpoint)."""
    out, work = _build(tmp_path)
    full = json.loads((out / "theory.json").read_text(encoding="utf-8"))["constraints"]
    assert full
    checkpoints = ceilings.history(work)
    middle = len(checkpoints) // 2
    cut = tmp_path / "middle"
    fixed.finalize_checkpoint(ceilings._cut_copy(work, tmp_path / "scratch", checkpoints[middle]), cut,
                              dict(DEFAULT_PROFILE), max_relation_drop_frac=1.0)
    cut_constraints = json.loads((cut / "theory.json").read_text(encoding="utf-8"))["constraints"]
    assert 0 < len(cut_constraints) < len(full)
    def decisions(path):  # class names are canonical names of the merges so far: compare the decisions themselves
        return [(r["id"], r["status"], r["negated"]) for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())]

    assert decisions(cut / "bindings.jsonl") == decisions(out / "bindings.jsonl")

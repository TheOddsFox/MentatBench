"""Second review follow-ups of the admission stage: the policy-resolved knobs of finalize (legacy and --no-admit checkpoints),
the admission flags of runcell, the resume policy of the admission knobs, the floor count on the repair-budget path, one
drop ledger for the repair and the engine stage, and the tie-break's records."""

import json
import time

import pytest

from mentat_bench import ceilings, cli, engine, runcell
from mentat_bench.adapters.base import usage
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import BaseCheckIncomplete, BaseInconsistentError, RepairBudgetStopped, _BaseCheck, _DropLedger
from mentat_bench.manifest import DEFAULT_PROFILE
from test_admit import _binary_markets, _Child, _cross, _NoRows, _unit
from irval import check_submission

ADMISSION_KEYS = ("admit", "admit_max_literals", "relation_floor", "repair_budget_s")


def _built(tmp_path, **kwargs):
    recs, bindings = _binary_markets()
    fixed.build(recs, _Child(bindings, _cross, with_conf=lambda a, b: 0.5), tmp_path / "sub", dict(DEFAULT_PROFILE),
                keep_work=True, **kwargs)
    return tmp_path / "sub.build"


def _policy(work):
    path = work / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    return path, ckpt


def test_a_legacy_checkpoint_without_admit_in_its_policy_finalizes_with_the_repair(tmp_path):
    work = _built(tmp_path)
    path, ckpt = _policy(work)
    for key in ADMISSION_KEYS:  # a checkpoint of a build from before admission
        ckpt["policy"].pop(key, None)
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    assert ceilings._repair_caps(ckpt)["admit"] is False
    summary = fixed.finalize_checkpoint(work, tmp_path / "legacy", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "repair"
    assert check_submission(tmp_path / "legacy") == []


def test_a_no_admit_checkpoint_finalized_without_arguments_stays_repair(tmp_path):
    work = _built(tmp_path, admit=False)
    assert _policy(work)[1]["policy"]["admit"] is False
    summary = fixed.finalize_checkpoint(work, tmp_path / "plain", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "repair"
    policy = json.loads((tmp_path / "plain" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["admit"] is False and policy["relation_floor"] is None


def test_the_admission_flags_of_runcell_are_forwarded_to_the_build(tmp_path, monkeypatch):
    seen = []

    def fake_run_cell(fixture, adapter, local_args, out, profile, build_flags, resume=False, extra_queries=False, candidates=None):
        seen.append(build_flags)
        return 0

    monkeypatch.setattr(runcell, "run_cell", fake_run_cell)
    args = ["--fixture", str(tmp_path), "--adapter", "empty", "--out", str(tmp_path / "run")]
    assert runcell.main([*args, "--admit-max-literals", "4096", "--relation-floor", "0.3", "--repair-budget-s", "60"]) == 0
    flags = seen[-1]
    assert flags[flags.index("--admit-max-literals") + 1] == "4096"
    assert flags[flags.index("--relation-floor") + 1] == "0.3"
    assert flags[flags.index("--repair-budget-s") + 1] == "60.0"
    assert "--no-admit" not in flags
    assert runcell.main([*args, "--no-admit"]) == 0 and "--no-admit" in seen[-1]
    with pytest.raises(SystemExit):  # the cli's validator for the literal cap
        runcell.main([*args, "--admit-max-literals", "0"])


def test_a_resumed_build_refuses_a_changed_admission_knob_and_reads_a_legacy_checkpoint_as_no_admit():
    old = {key: f"value of {key}" for key in fixed.RESUME_POLICY_KEYS if key not in ADMISSION_KEYS}  # a legacy policy
    requested = {**old, "admit": False, "admit_max_literals": fixed.ADMIT_MAX_LITERALS, "relation_floor": None,
                 "repair_budget_s": None}
    fixed._check_resume_policy(old, requested, None)  # the same knobs (no admission): resumes
    with pytest.raises(ValueError, match="admit=False.*resumes with --no-admit"):  # a pre-admission checkpoint's refusal names it
        fixed._check_resume_policy(old, {**requested, "admit": True}, None)
    with pytest.raises(ValueError, match="admit=True.*without --no-admit"):
        fixed._check_resume_policy({**old, "admit": True}, requested, None)
    with pytest.raises(ValueError, match="relation_floor"):
        fixed._check_resume_policy(old, {**requested, "relation_floor": 0.5}, None)


def test_the_floor_count_on_the_repair_path_counts_the_floored_relations(tmp_path):
    work = _built(tmp_path)  # the checkpoint's relation confs are 0.5; the floor below drops it on finalize
    # admit_max_literals 4 makes the theory too large for admission: the repair runs (no stubs), and the floor holds on it
    summary = fixed.finalize_checkpoint(work, tmp_path / "floored", dict(DEFAULT_PROFILE), relation_floor=0.9,
                                        admit_max_literals=4)
    assert summary["admit_mode"] == "repair_size" and summary["floor_dropped_relations"] == 1
    assert summary["relations_dropped"] == 0 and summary["base_repaired"] is False


class _ConflictChild(_Child):
    """Group decisions by outcome set. Market {a1, a2} is 'at least 2 of 2' (both true) and event {a1, a2, b1} is 'at most
    one': a contradiction whose cycle holds two group units and no relation, so the repair drops one of the two rows. Market
    {u1, u2, u3} is 'at least 2 of 3' over pairwise EXCLUDES, which only mb-engine localizes."""

    def decide_group(self, ctx):
        ids = [m["id"] for m in ctx["members"]]
        members = set(ids)
        if members == {"toy:a:1", "toy:a:2"}:
            return [{"kind": "at_least_k", "members": ids, "k": 2}], usage(self.name)
        if members == {"toy:a:1", "toy:a:2", "toy:b:1"}:
            return [{"kind": "at_most_one", "members": ids}], usage(self.name)
        if members == {"toy:u:1", "toy:u:2", "toy:u:3"}:
            return [{"kind": "at_least_k", "members": ids, "k": 2}], usage(self.name)
        return [], usage(self.name)


def _conflict_build(tmp_path, **kwargs):
    rows = [("toy:a:1", "E"), ("toy:a:2", "E"), ("toy:b:1", "E"), ("toy:u:1", None), ("toy:u:2", None), ("toy:u:3", None)]
    recs = [{"id": oid, "question": f"Will alpha team reach {n} points in 2026?", "label": "Yes", "event_title": title}
            for n, (oid, title) in enumerate(rows, 1)]
    bindings = {r["id"]: {"status": "bound", "class": f"C{n}", "negated": False, "missing": []} for n, r in enumerate(recs, 1)}
    child = _ConflictChild(bindings, lambda a, b: "EXCLUDES" if a.startswith("toy:u") and b.startswith("toy:u") else "NONE")
    return fixed.build(recs, child, tmp_path / "sub", dict(DEFAULT_PROFILE), admit=False, max_group_drop_frac=1.0, **kwargs)


def test_repair_and_engine_group_drops_are_one_count_against_the_group_cap(tmp_path):
    if not engine.engine_available():
        pytest.skip("mb-engine not found (set MB_ENGINE)")
    # the repair drops one group row of the contradiction, the engine localizes the other: two group drops in all
    summary = _conflict_build(tmp_path, max_group_drops=2)
    assert summary["admit_mode"] == "repair" and summary["groups_dropped"] == 2
    assert summary["engine_localized_drops"] == 1 and check_submission(tmp_path / "sub") == []
    with pytest.raises(BaseInconsistentError, match="max-group-drops 1"):  # the engine's drop is the second: over a cap of 1
        _conflict_build(tmp_path / "again", max_group_drops=1)


@pytest.mark.parametrize("cap, refused", [(2, False), (1, True)])
def test_repair_and_engine_group_drops_share_the_cap_without_mb_engine(tmp_path, monkeypatch, cap, refused):
    """The same contradiction as the engine test, with a scripted mb-engine: it rejects the base once (the deferred market
    {u1, u2, u3}), then accepts it. Runs without the engine binary."""
    rejected = []

    def scripted_engine(out_dir, work, budget_s):
        if out_dir.name == "probe" or rejected:
            return "engine", ""
        rejected.append(1)
        return "rejected", "scripted: at least 2 of {u1, u2, u3} with pairwise EXCLUDES"

    monkeypatch.setattr(fixed, "_engine_verdict", scripted_engine)
    if refused:
        with pytest.raises(BaseInconsistentError, match="max-group-drops 1"):  # the engine's drop is the second one
            _conflict_build(tmp_path, max_group_drops=cap)
    else:
        summary = _conflict_build(tmp_path, max_group_drops=cap)
        assert summary["groups_dropped"] == 2 and summary["engine_localized_drops"] == 1 and summary["base_probe"] == "engine"


def test_the_admission_tie_break_reads_only_the_checkpointed_records(tmp_path, monkeypatch):
    ids = ["toy:m1:1", "toy:m2:1"]
    rows = [{"id": oid, "question": f"Will alpha reach {k} points?", "event_title": None} for k, oid in enumerate(ids)]
    rows.append({"id": "toy:m3:1", "question": "Will beta win?", "event_title": None})  # past the checkpoint: not truncated
    work = tmp_path / "work"
    work.mkdir()
    (work / "records.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    counts, real = [], fixed._pair_tiebreak

    def spy(ids_, records):
        records = list(records)
        counts.append(len(records))
        return real(ids_, records)

    monkeypatch.setattr(fixed, "_pair_tiebreak", spy)
    units = [_unit("p0", "relation", [(0, 2)])]
    fixed._admit_build(_BaseCheck(_NoRows(2), units, 1), units, _DropLedger(9, 9, None, 1), None, lambda: 1e9, work, ids)
    assert counts == [len(ids)]


def test_an_exhausted_repair_budget_without_admission_fails_with_its_own_message(tmp_path, monkeypatch):
    work = _built(tmp_path)  # admission applies, but a theory too large for it (admit_max_literals 4) is repaired, not admitted

    def slow_repair(*args, **kwargs):  # the repair runs past its budget before it finishes
        time.sleep(0.02)
        raise BaseCheckIncomplete("the build deadline passed before the base theory was verified consistent")

    monkeypatch.setattr(fixed, "_prune_in_process", slow_repair)
    with pytest.raises(RepairBudgetStopped, match="repair budget of 0.005 s") as caught:
        fixed.finalize_checkpoint(work, tmp_path / "late", dict(DEFAULT_PROFILE), admit_max_literals=4, repair_budget_s=0.005)
    assert isinstance(caught.value, BaseCheckIncomplete) and caught.value.deadline_passed is False
    assert not (tmp_path / "late" / "PARTIAL.json").exists() and (work / "checkpoint.json").is_file()  # kept for a larger budget
    tiny = dict(DEFAULT_PROFILE, build_deadline_s=1e-9)  # the deadline has passed too: the stop says so
    with pytest.raises(RepairBudgetStopped) as passed:
        fixed.finalize_checkpoint(work, tmp_path / "late2", tiny, admit_max_literals=4, repair_budget_s=0.005)
    assert passed.value.deadline_passed is True


def test_the_cli_advises_a_larger_repair_budget_when_the_budget_stopped_the_build(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("", encoding="utf-8")

    def budget_stopped(*args, **kwargs):
        raise RepairBudgetStopped("the repair budget of 60.0 s passed before the base theory was verified consistent")

    monkeypatch.setattr(cli.fixed, "build", budget_stopped)
    code = cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(tmp_path / "out"), "--no-admit",
                     "--repair-budget-s", "60"])
    err = capsys.readouterr().err
    assert code == cli.EXIT_BUILD_INCOMPLETE and "ran past its budget" in err and "repair_budget_s=" in err
    assert "build_deadline_s" not in err


@pytest.mark.parametrize("deadline_passed, advice", [(False, "repair_budget_s="), (True, "build_deadline_s")])
def test_the_cli_names_the_deadline_only_when_it_has_passed(tmp_path, monkeypatch, capsys, deadline_passed, advice):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("", encoding="utf-8")

    def budget_stopped(*args, **kwargs):
        raise RepairBudgetStopped("the repair budget of 60.0 s passed", deadline_passed=deadline_passed)

    monkeypatch.setattr(cli.fixed, "build", budget_stopped)
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(tmp_path / "out")]) == cli.EXIT_BUILD_INCOMPLETE
    err = capsys.readouterr().err
    assert advice in err
    assert ("build_deadline_s" in err) is deadline_passed


def test_the_repair_budget_applies_to_the_engine_fallback_repair(tmp_path, monkeypatch):
    work = _built(tmp_path)  # admission applies; the engine refuses the admitted base, so the repair runs after it
    real_prune, real_settle, calls = fixed._prune_in_process, fixed._settle_with_engine, []

    def refuse_first(*args, **kwargs):
        if not calls:
            calls.append(1)
            raise fixed._EngineRejected("simulated refusal of the admitted base")
        return real_settle(*args, **kwargs)

    def slow_fallback(*args, **kwargs):  # the repair is cut by its budget, which reaches it as the time_left it is given
        time.sleep(0.02)
        if args[3]() <= 0:
            raise BaseCheckIncomplete("the repair budget passed")
        return real_prune(*args, **kwargs)

    monkeypatch.setattr(fixed, "_settle_with_engine", refuse_first)
    monkeypatch.setattr(fixed, "_prune_in_process", slow_fallback)
    with pytest.raises(RepairBudgetStopped):
        fixed.finalize_checkpoint(work, tmp_path / "fallback", dict(DEFAULT_PROFILE), repair_budget_s=0.005)


def test_the_engine_counts_of_the_summary_are_those_of_the_fallback_settle(tmp_path, monkeypatch):
    work = _built(tmp_path)
    real_settle, calls = fixed._settle_with_engine, []

    def refuse_after_probes(*args, **kwargs):  # the first settle's probes are counted, then the admitted base is refused
        if not calls:
            calls.append(1)
            args[9]["probes"] += 100
            raise fixed._EngineRejected("simulated refusal of the admitted base")
        return real_settle(*args, **kwargs)

    monkeypatch.setattr(fixed, "_settle_with_engine", refuse_after_probes)
    summary = fixed.finalize_checkpoint(work, tmp_path / "fallback", dict(DEFAULT_PROFILE))
    assert summary["admit_mode"] == "repair_after_engine" and 0 < summary["engine_probes"] < 100


def test_build_policy_records_the_admission_knobs_applied_by_the_finalize(tmp_path):
    work = _built(tmp_path)
    fixed.finalize_checkpoint(work, tmp_path / "pairwise", dict(DEFAULT_PROFILE))
    applied = json.loads((tmp_path / "pairwise" / "build_policy.json").read_text(encoding="utf-8"))
    assert applied["admit"] is True and applied["relation_floor"] is None and applied["repair_budget_s"] is None
    fixed.finalize_checkpoint(work, tmp_path / "unprobed", dict(DEFAULT_PROFILE), probe=False)  # admission did not run
    unprobed = json.loads((tmp_path / "unprobed" / "build_policy.json").read_text(encoding="utf-8"))
    assert unprobed["admit"] is None and unprobed["relation_floor"] is None and unprobed["repair_budget_s"] is None
    assert _policy(work)[1]["policy"]["admit"] is True  # the checkpoint's own policy (resume, ceilings) is untouched


def test_the_build_parser_accepts_the_four_admission_flags():
    args = cli.build_parser().parse_args([
        "build", "--adapter", "empty", "--corpus", "c.jsonl", "--out", "o", "--no-admit", "--admit-max-literals", "4096",
        "--relation-floor", "0.3", "--repair-budget-s", "60",
    ])
    assert args.no_admit is True and args.admit_max_literals == 4096
    assert args.relation_floor == 0.3 and args.repair_budget_s == 60.0
    default = cli.build_parser().parse_args(["build", "--adapter", "empty", "--corpus", "c.jsonl", "--out", "o"])
    assert default.no_admit is False and default.admit_max_literals == fixed.ADMIT_MAX_LITERALS


@pytest.mark.parametrize("method", ["scc", "bisect"])
def test_the_real_repair_checks_the_deadline_between_passes(tmp_path, monkeypatch, method):
    """The real _prune_scc / _prune_bisect check, with no stub: a checkpoint with a conflict needs repair passes, and a passed
    deadline stops them with the plain BaseCheckIncomplete (no repair budget is set). A deadline of 0 is passed whatever the
    clock says. A repair budget cannot be tested this way on this host: time.monotonic has a 15.6 ms resolution, so a fast
    repair finishes within one tick of any tiny budget."""
    monkeypatch.setenv("MB_PRUNE", method)
    _conflict_build(tmp_path, keep_work=True)  # admission off; the checkpoint is kept in sub.build
    with pytest.raises(BaseCheckIncomplete) as exc:
        fixed.finalize_checkpoint(tmp_path / "sub.build", tmp_path / "late", dict(DEFAULT_PROFILE, build_deadline_s=0.0),
                                  admit=False)
    assert type(exc.value) is BaseCheckIncomplete and not (tmp_path / "late").exists()


def test_a_fallback_repair_cut_by_its_budget_leaves_no_submission(tmp_path, monkeypatch):
    """The admitted base is written and refused by the engine; the fallback repair runs out of budget. Nothing is left in
    out_dir: finalize removes what it wrote when it raises."""
    work = _built(tmp_path)

    def refuse_admitted(out_dir, work_dir, budget_s):  # the probe is accepted; the admitted submission is rejected
        return ("engine", "") if out_dir.name == "probe" else ("rejected", "simulated refusal of the admitted base")

    def slow_fallback(*args, **kwargs):  # the fallback repair runs past its budget (its time is the only stub)
        time.sleep(0.02)
        raise BaseCheckIncomplete("budget")

    monkeypatch.setattr(fixed, "_engine_verdict", refuse_admitted)
    monkeypatch.setattr(fixed, "_prune_in_process", slow_fallback)
    with pytest.raises(RepairBudgetStopped):
        fixed.finalize_checkpoint(work, tmp_path / "fallback", dict(DEFAULT_PROFILE), repair_budget_s=0.005)
    assert not (tmp_path / "fallback").exists()  # the admitted submission the engine refused was removed
    assert (work / "checkpoint.json").is_file()  # the checkpoint is kept for a later finalize

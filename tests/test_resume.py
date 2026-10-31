"""build --resume: a build killed mid-relation-phase and resumed from its kept work dir gives the same theory, bindings,
provenance, usage (timing aside) and summary as an uninterrupted build with the same decisions; the policy must match;
the summary says it resumed and how many calls came before."""

import hashlib
import json
import shutil

import pytest

from mentat_bench import cli, report
from mentat_bench.adapters.base import AdapterError, usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE
from mentat_bench.report import OP_PHASES

RELATIONS = ["NONE", "NONE", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "EQUIV", "UNRESOLVED"]


def _h(*parts):
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest(), 16)


class Det:
    """A deterministic adapter: the same answer for the same question, in any process. kill_after=N raises
    KeyboardInterrupt on the (N+1)-th relation call, as a Ctrl-C would."""

    name = "det"
    supports_groups = True

    def __init__(self, kill_after=None):
        self.kill_after, self.relation_calls = kill_after, 0

    def decide_binding(self, ctx):
        h = _h(ctx["id"])
        if h % 3 == 0:
            decision = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
        else:
            decision = {"status": "bound", "class": f"c{h % 4}", "negated": (h // 4) % 2 == 0, "missing": []}
        return decision, usage(self.name)

    def decide_relation(self, a, b):
        self.relation_calls += 1
        if self.kill_after is not None and self.relation_calls > self.kill_after:
            raise KeyboardInterrupt("killed")
        return RELATIONS[_h(a["id"], b["id"]) % len(RELATIONS)], usage(self.name)

    def decide_group(self, ctx):
        ids = [m["id"] for m in ctx["members"]]
        return ([{"kind": "at_most_one", "members": ids[:2]}] if len(ids) >= 2 else []), usage(self.name)


def _records():
    recs = []
    for k in range(7):
        question = f"Will M{k} win the cup?"
        event = "E" if k < 4 else "F"
        recs.append({"id": f"v:m{k}:1", "question": question, "label": "Yes", "event_title": event})
        recs.append({"id": f"v:m{k}:2", "question": question, "label": "No", "event_title": event})
    return recs


def _files(out):
    return {name: (out / name).read_bytes() for name in ("theory.json", "bindings.jsonl", "provenance.jsonl")}


def _calls(out):
    return [{k: v for k, v in call.items() if k != "seconds"}
            for call in json.loads((out / "usage.json").read_text(encoding="utf-8"))["calls"]]


TIMING = {"wall_seconds", "peak_rss_mb", "out_dir", "resumed", "prior_calls", "prune_seconds", "admit_seconds",
          "finalize_seconds", "engine_seconds", "write_seconds", "phase_seconds", "phase_call_seconds"}


def test_a_resumed_build_equals_the_uninterrupted_build(tmp_path):
    profile = dict(DEFAULT_PROFILE)
    full = tmp_path / "full"
    full_summary = fixed.build(_records(), Det(), full, profile, checkpoint_every=4, max_relation_drop_frac=1.0)

    killed = tmp_path / "killed"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Det(kill_after=20), killed, profile, checkpoint_every=4, max_relation_drop_frac=1.0)
    assert not killed.exists()  # nothing sealed or written to the output dir
    checkpoint = json.loads((tmp_path / "killed.build" / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["phase"] == "relations" and 0 < checkpoint["pairs_considered"] < checkpoint["pairs_total"]

    resumed_summary = fixed.build(_records(), Det(), killed, profile, checkpoint_every=4, resume=True, max_relation_drop_frac=1.0)
    assert _files(killed) == _files(full)  # byte-identical theory, bindings and provenance
    assert _calls(killed) == _calls(full)  # the same usage entries (the killed chunk was discarded and re-asked)
    assert resumed_summary["resumed"] is True and resumed_summary["prior_calls"] > 0
    assert full_summary["resumed"] is False
    same = {k: v for k, v in resumed_summary.items() if k not in TIMING}
    assert same == {k: v for k, v in full_summary.items() if k not in TIMING}
    assert not (tmp_path / "killed.build").exists()  # the finished build removes its work dir


def test_a_resume_with_different_policy_flags_is_refused(tmp_path):
    profile = dict(DEFAULT_PROFILE)
    killed = tmp_path / "killed"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Det(kill_after=20), killed, profile, checkpoint_every=4, top_k=128, max_relation_drop_frac=1.0)
    with pytest.raises(ValueError, match="cannot resume: the checkpoint was built with top_k=128"):
        fixed.build(_records(), Det(), killed, profile, checkpoint_every=4, top_k=64, resume=True, max_relation_drop_frac=1.0)
    assert not killed.exists() and (tmp_path / "killed.build").exists()  # the checkpoint is kept for a later resume


def test_resume_with_nothing_to_resume_is_a_usage_error(tmp_path, capsys):
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(tmp_path / "missing.jsonl"), "--out",
                     str(tmp_path / "out"), "--resume"]) == cli.EXIT_USAGE
    (tmp_path / "x.build").mkdir()
    with pytest.raises(ValueError, match="nothing to resume"):
        fixed.build(_records(), Det(), tmp_path / "x", dict(DEFAULT_PROFILE), resume=True)


def test_an_interrupted_cli_build_keeps_its_checkpoint_for_resume(tmp_path, monkeypatch, capsys):
    def interrupted(records, adapter, out_dir, profile, **kwargs):
        work = out_dir.with_name(out_dir.name + ".build")
        work.mkdir(parents=True)
        (work / "checkpoint.json").write_text("{}", encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.fixed, "build", interrupted)
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"id": "v:m1:1", "text": "Will X win?", "label": "Yes", "event_title": "E"}) + "\n",
                      encoding="utf-8")
    with pytest.raises(KeyboardInterrupt):
        cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(tmp_path / "out")])
    assert (tmp_path / "out.build" / "checkpoint.json").exists()
    assert "rerun the same command with --resume" in capsys.readouterr().err


# ---- a budget stop resumed with larger limits (budget_extended) -------------------------------------------------------

class Priced(Det):
    """Det with a price per call, so that a spend ceiling stops it."""

    def __init__(self, cost=0.01):
        super().__init__()
        self.cost = cost

    def decide_binding(self, ctx):
        decision, _ = super().decide_binding(ctx)
        return decision, usage(self.name, cost_usd=self.cost)

    def decide_relation(self, a, b):
        relation, _ = super().decide_relation(a, b)
        return relation, usage(self.name, cost_usd=self.cost)

    def decide_group(self, ctx):
        groups, _ = super().decide_group(ctx)
        return groups, usage(self.name, cost_usd=self.cost)


class Clock:
    """The gateway's clock: the test sets it, so that the deadline stop is reached at a known point."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Overdue(Det):
    """Sets the clock to the model-call stop after the last binding, so that blocking starts past the stop."""

    def __init__(self, clock, limit):
        super().__init__()
        self.clock, self.limit = clock, limit

    def decide_binding(self, ctx):
        decision, call = super().decide_binding(ctx)
        if ctx["id"] == _records()[-1]["id"]:
            self.clock.t = self.limit
        return decision, call


def _is_subsequence(small, big):
    it = iter(big)
    return all(any(item == other for other in it) for item in small)


@pytest.mark.parametrize("ceiling,stopped_in", [(0.5, "relations"), (0.15, "groups")])
def test_a_spend_stop_resumes_with_a_larger_ceiling_as_an_uninterrupted_build(tmp_path, ceiling, stopped_in):
    """The stop keeps its work dir (keep_work); a resume with a larger spend ceiling continues from the stopped phase, is
    labelled budget_extended, and decides what an uninterrupted build with the larger ceiling decides. The paid calls of a
    discarded batch stay in usage.json and are asked again, so the usage is a superset of the uninterrupted usage."""
    big = dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0)
    full = tmp_path / "full"
    full_summary = fixed.build(_records(), Priced(), full, big, checkpoint_every=1, max_relation_drop_frac=1.0)

    out = tmp_path / "out"
    killed = fixed.build(_records(), Priced(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=ceiling), checkpoint_every=1,
                         max_relation_drop_frac=1.0, keep_work=True)
    assert killed["budget_stopped"] is True and killed["stopped_in"] == stopped_in
    checkpoint = json.loads((tmp_path / "out.build" / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["stopped_in"] == stopped_in
    killed_calls = _calls(out)
    shutil.rmtree(out)  # the stopped build wrote its partial submission to out; a resume needs a new out

    resumed = fixed.build(_records(), Priced(), out, big, checkpoint_every=1, resume=True, max_relation_drop_frac=1.0)
    assert _files(out) == _files(full)
    assert resumed["budget_stopped"] is False and "stopped_in" not in resumed
    assert resumed["budget_extended"] == [{"old": {"spend_ceiling_usd": ceiling, "build_deadline_s": 3600.0},
                                           "new": {"spend_ceiling_usd": 10.0, "build_deadline_s": 3600.0}, "stopped_in": stopped_in}]
    policy = json.loads((out / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["budget_extended"] == resumed["budget_extended"] and "profile" not in policy
    resumed_calls = _calls(out)
    assert resumed_calls[:len(killed_calls)] == killed_calls and _is_subsequence(_calls(full), resumed_calls)
    assert resumed["pairs_considered"] == full_summary["pairs_considered"]
    assert resumed["groups_committed"] == full_summary["groups_committed"]


def test_a_stop_in_the_blocking_phase_reruns_blocking_on_resume(tmp_path, monkeypatch):
    """The clock passes the model-call stop before blocking: pairs.bin is cut short. A resume with a larger deadline runs
    blocking again, so the theory is the uninterrupted build's; the finalize reserve follows the new deadline."""
    clock = Clock()
    gateway = fixed.Gateway
    monkeypatch.setattr(fixed, "Gateway", lambda *args, **kwargs: gateway(*args, clock=clock, **kwargs))
    limit = float(DEFAULT_PROFILE["build_deadline_s"]) * (1 - fixed.FINALIZE_RESERVE_FRAC)
    big = dict(DEFAULT_PROFILE, build_deadline_s=7200.0)
    full = tmp_path / "full"
    full_summary = fixed.build(_records(), Det(), full, big, checkpoint_every=4, max_relation_drop_frac=1.0)

    out = tmp_path / "out"
    killed = fixed.build(_records(), Overdue(clock, limit), out, dict(DEFAULT_PROFILE), checkpoint_every=4,
                         max_relation_drop_frac=1.0, keep_work=True)
    checkpoint = json.loads((tmp_path / "out.build" / "checkpoint.json").read_text(encoding="utf-8"))
    assert killed["budget_stopped"] is True and checkpoint["stopped_in"] == "blocking"
    shutil.rmtree(out)

    resumed = fixed.build(_records(), Det(), out, big, checkpoint_every=4, resume=True, max_relation_drop_frac=1.0)
    assert _files(out) == _files(full)
    assert _calls(out) == _calls(full)
    assert resumed["budget_stopped"] is False and checkpoint["pairs_total"] < resumed["candidate_pairs"]  # cut short
    assert resumed["candidate_pairs"] == full_summary["candidate_pairs"] > 0
    assert resumed["finalize_reserve_s"] == round(fixed.FINALIZE_RESERVE_FRAC * 7200.0, 3)


def test_a_resume_that_changes_a_profile_key_other_than_the_limits_is_refused(tmp_path):
    profile = dict(DEFAULT_PROFILE)
    killed = tmp_path / "killed"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Det(kill_after=20), killed, profile, checkpoint_every=4, max_relation_drop_frac=1.0)
    with pytest.raises(ValueError, match="cannot resume: the checkpoint was built with profile max_interpretations=8"):
        fixed.build(_records(), Det(), killed, dict(profile, max_interpretations=4), checkpoint_every=4, resume=True,
                    max_relation_drop_frac=1.0)
    assert not killed.exists() and (tmp_path / "killed.build").exists()


def test_a_budget_stop_without_headroom_stays_final(tmp_path):
    """Resumed with the same ceiling, the spend is still at the stop: no call is made and the build stays partial."""
    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=0.5)
    out = tmp_path / "out"
    fixed.build(_records(), Priced(), out, profile, checkpoint_every=1, max_relation_drop_frac=1.0, keep_work=True)
    before = _calls(out)
    shutil.rmtree(out)
    again = fixed.build(_records(), Priced(), out, profile, checkpoint_every=1, resume=True, max_relation_drop_frac=1.0)
    assert again["budget_stopped"] is True and again["stopped_in"] == "relations" and "budget_extended" not in again
    assert _calls(out) == before


def test_a_resume_with_a_different_corpus_is_refused(tmp_path):
    """The checkpoint's corpus digest covers every record in order: one record fewer, or one question changed, is another
    corpus, and the resume is refused before any decision is made or any log is touched."""
    profile = dict(DEFAULT_PROFILE)
    killed = tmp_path / "killed"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Det(kill_after=20), killed, profile, checkpoint_every=4, max_relation_drop_frac=1.0)
    changed = [dict(r, question=r["question"] + "?") if n == 0 else r for n, r in enumerate(_records())]
    for other in (_records()[:-1], changed):
        with pytest.raises(ValueError, match="cannot resume: the corpus differs"):
            fixed.build(other, Det(), killed, profile, checkpoint_every=4, resume=True, max_relation_drop_frac=1.0)
    assert not killed.exists() and (tmp_path / "killed.build").exists()


def test_a_checkpoint_from_before_the_profile_was_recorded_resumes(tmp_path):
    profile = dict(DEFAULT_PROFILE)
    killed = tmp_path / "killed"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Det(kill_after=20), killed, profile, checkpoint_every=4, max_relation_drop_frac=1.0)
    path = tmp_path / "killed.build" / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    del ckpt["policy"]["profile"]
    del ckpt["policy"]["corpus"]
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    fixed.build(_records(), Det(), killed, profile, checkpoint_every=4, resume=True, max_relation_drop_frac=1.0)
    full = tmp_path / "full"
    fixed.build(_records(), Det(), full, profile, checkpoint_every=4, max_relation_drop_frac=1.0)
    assert _files(killed) == _files(full)
    assert "profile" not in json.loads((killed / "build_policy.json").read_text(encoding="utf-8"))


class Staged(Det):
    """Det priced by phase: a binding 0.25, a group decision 1.0, a relation nothing. A spend ceiling then stops the group
    phase, and the group share is the only budget that binds there."""

    def decide_binding(self, ctx):
        decision, _ = super().decide_binding(ctx)
        return decision, usage(self.name, cost_usd=0.25)

    def decide_group(self, ctx):
        groups, _ = super().decide_group(ctx)
        return groups, usage(self.name, cost_usd=1.0)


def test_a_resumed_group_phase_keeps_the_share_of_its_first_entry(tmp_path):
    """Bindings cost 3.5. The first run stops on a ceiling of 5.5 after two group decisions (the share, 0.5 of 5.5, is not
    reached). Resumed with 8.0 the group share is 4.0 of the groups' spend since the phase began, as in an uninterrupted
    build with 8.0. Re-anchored at the resume (spend 5.5), the share would allow four more decisions."""
    flags = dict(checkpoint_every=1, max_relation_drop_frac=1.0, group_budget_frac=0.5)
    full = tmp_path / "full"
    full_summary = fixed.build(_records(), Staged(), full, dict(DEFAULT_PROFILE, spend_ceiling_usd=8.0), **flags)
    assert full_summary["group_budget_stopped"] is True and full_summary["budget_stopped"] is False

    out = tmp_path / "out"
    killed = fixed.build(_records(), Staged(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=5.5), keep_work=True, **flags)
    assert killed["budget_stopped"] is True and killed["stopped_in"] == "groups"
    checkpoint = json.loads((tmp_path / "out.build" / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["group_spend0"] == 3.5 and checkpoint["group_cursor"] == 2  # two decided, the third refused
    shutil.rmtree(out)

    resumed = fixed.build(_records(), Staged(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=8.0), resume=True, **flags)
    assert resumed["group_budget_stopped"] is True and resumed["budget_stopped"] is False
    assert resumed["group_candidates"] == full_summary["group_candidates"] == 4
    assert resumed["groups_committed"] == full_summary["groups_committed"]
    assert _files(out) == _files(full)  # the same group decisions, and so the same theory
    assert _calls(out) == _calls(full)


def test_each_cleared_stop_appends_a_budget_extended_entry(tmp_path):
    out = tmp_path / "out"
    flags = dict(checkpoint_every=1, max_relation_drop_frac=1.0, keep_work=True)
    first = fixed.build(_records(), Priced(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=0.5), **flags)
    assert first["stopped_in"] == "relations"
    shutil.rmtree(out)
    second = fixed.build(_records(), Priced(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=0.6), resume=True, **flags)
    assert second["budget_stopped"] is True and second["stopped_in"] == "relations"
    assert len(second["budget_extended"]) == 1
    shutil.rmtree(out)
    final = fixed.build(_records(), Priced(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), resume=True,
                        checkpoint_every=1, max_relation_drop_frac=1.0)
    assert final["budget_stopped"] is False
    assert [entry["new"]["spend_ceiling_usd"] for entry in final["budget_extended"]] == [0.6, 10.0]
    assert [entry["stopped_in"] for entry in final["budget_extended"]] == ["relations", "relations"]
    assert final["budget_extended"][0] == second["budget_extended"][0]
    assert final["budget_extended"][1]["old"] == {"spend_ceiling_usd": 0.6, "build_deadline_s": 3600.0}
    policy = json.loads((out / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["budget_extended"] == final["budget_extended"]
    fixed.build(_records(), Priced(), tmp_path / "full", dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), checkpoint_every=1,
                max_relation_drop_frac=1.0)
    assert _files(out) == _files(tmp_path / "full")


def test_a_changed_limit_without_headroom_writes_no_entry_and_the_stop_stays(tmp_path):
    """The spend is still at the ceiling, so the stop stands: the deadline (changed) does not clear it, and no
    budget_extended entry is written for the change."""
    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=0.5)
    out = tmp_path / "out"
    fixed.build(_records(), Priced(), out, profile, checkpoint_every=1, max_relation_drop_frac=1.0, keep_work=True)
    before = _calls(out)
    shutil.rmtree(out)
    again = fixed.build(_records(), Priced(), out, dict(profile, build_deadline_s=7200.0), checkpoint_every=1, resume=True,
                        max_relation_drop_frac=1.0)
    assert again["budget_stopped"] is True and again["stopped_in"] == "relations" and "budget_extended" not in again
    assert _calls(out) == before
    assert "budget_extended" not in json.loads((out / "build_policy.json").read_text(encoding="utf-8"))


def test_a_max_calls_resume_counts_the_items_already_charged(tmp_path):
    """The cap (44 items: 14 bindings, then 30 decisions) is set on both runs. The crash after 20 relation calls leaves 34
    items charged; the resume's items_left is 10, and it asks what the uninterrupted build asks under the same cap."""
    flags = dict(checkpoint_every=4, max_relation_drop_frac=1.0, max_calls=44)
    full = tmp_path / "full"
    full_summary = fixed.build(_records(), Det(), full, dict(DEFAULT_PROFILE), outcomes=len(_records()), **flags)
    assert full_summary["items"] == 44 and full_summary["calls_capped"] is True

    killed = tmp_path / "killed"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Det(kill_after=20), killed, dict(DEFAULT_PROFILE), outcomes=len(_records()), **flags)
    resumed = fixed.build(_records(), Det(), killed, dict(DEFAULT_PROFILE), resume=True, **flags)
    assert resumed["items"] == 44 and resumed["calls_capped"] is True and resumed["prior_calls"] > 0
    assert _files(killed) == _files(full) and _calls(killed) == _calls(full)


def test_a_resume_that_changes_the_drop_caps_records_the_applied_ones(tmp_path):
    caps = dict(max_relation_drops=50, max_relation_drop_frac=0.5, max_group_drops=90, max_group_drop_frac=0.8)
    profile = dict(DEFAULT_PROFILE)
    killed = tmp_path / "killed"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Det(kill_after=20), killed, profile, checkpoint_every=4, max_relation_drop_frac=1.0)
    fixed.build(_records(), Det(), killed, profile, checkpoint_every=4, resume=True, **caps)
    full = tmp_path / "full"
    fixed.build(_records(), Det(), full, profile, checkpoint_every=4, **caps)
    policy = json.loads((killed / "build_policy.json").read_text(encoding="utf-8"))
    assert {key: policy[key] for key in caps} == caps
    assert (killed / "build_policy.json").read_bytes() == (full / "build_policy.json").read_bytes()


def test_a_resume_charges_the_paid_calls_made_after_the_last_checkpoint(tmp_path):
    """A crash between checkpoints leaves paid calls in usage_calls.jsonl that no checkpoint commits. The resume charges them:
    its items and spend count every row of usage.json and stay within the cap and the ceiling, and the decisions are asked
    again (the theory is not compared: the discarded calls count against the cap)."""
    flags = dict(checkpoint_every=4, max_relation_drop_frac=1.0, max_calls=44)
    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=50.0)
    killed = tmp_path / "killed"
    crash = Priced()
    crash.kill_after = 10  # the 11th relation call raises; the calls after the last checkpoint are paid and uncommitted
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), crash, killed, profile, outcomes=len(_records()), **flags)
    work = tmp_path / "killed.build"
    checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    assert (work / "usage_calls.jsonl").stat().st_size > checkpoint["usage_bytes"]  # uncommitted paid rows on disk
    killed_rows = [json.loads(line) for line in (work / "usage_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    paid_before = len(killed_rows)

    resumed = fixed.build(_records(), Priced(), killed, profile, resume=True, **flags)
    calls = json.loads((killed / "usage.json").read_text(encoding="utf-8"))["calls"]
    assert resumed["calls"] == len(calls) and resumed["prior_calls"] >= paid_before
    assert resumed["items"] == sum(call.get("items", 1) for call in calls) <= 44
    assert resumed["spend_usd"] == round(sum(call["cost_usd"] for call in calls), 6) <= 50.0
    assert calls[:paid_before] == killed_rows  # the uncommitted paid rows are kept in usage.json, not cut


def test_a_resume_that_lowers_a_limit_is_refused_before_anything_is_touched(tmp_path):
    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=0.5)
    out = tmp_path / "out"
    fixed.build(_records(), Priced(), out, profile, checkpoint_every=1, max_relation_drop_frac=1.0, keep_work=True)
    shutil.rmtree(out)
    work = tmp_path / "out.build"
    before = {name: (work / name).read_bytes() for name in ("usage_calls.jsonl", "checkpoint.json", "decided.jsonl")}
    with pytest.raises(ValueError, match="cannot resume: spend_ceiling_usd is 0.3, below the 0.5"):
        fixed.build(_records(), Priced(), out, dict(profile, spend_ceiling_usd=0.3), checkpoint_every=1, resume=True,
                    max_relation_drop_frac=1.0)
    with pytest.raises(ValueError, match="cannot resume: build_deadline_s is 60.0, below the 3600.0"):
        fixed.build(_records(), Priced(), out, dict(profile, build_deadline_s=60.0), checkpoint_every=1, resume=True,
                    max_relation_drop_frac=1.0)
    assert {name: (work / name).read_bytes() for name in before} == before and not out.exists()


def test_a_checkpoint_whose_max_calls_is_below_its_bindings_is_refused(tmp_path):
    """A checkpoint from before the cap was enforced can hold more binding items than its max_calls: refused on resume."""
    profile = dict(DEFAULT_PROFILE)
    killed = tmp_path / "killed"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Det(kill_after=20), killed, profile, checkpoint_every=4, max_relation_drop_frac=1.0,
                    max_calls=44, outcomes=len(_records()))
    path = tmp_path / "killed.build" / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    ckpt["policy"]["max_calls"] = 5
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot resume: the checkpoint was built with max_calls 5, below its 14 binding calls"):
        fixed.build(_records(), Det(), killed, profile, checkpoint_every=4, resume=True, max_relation_drop_frac=1.0, max_calls=5)
    assert not killed.exists()


def _crashed(tmp_path, name, **flags):
    """A build killed by Ctrl-C after 10 relation calls, between checkpoints: its usage log holds paid rows no checkpoint
    commits. Returns the output dir (its work dir is <out>.build)."""
    out = tmp_path / name
    crash = Priced()
    crash.kill_after = 10
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), crash, out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0, **flags)
    return out


def _tear_usage_tail(out):
    """What a kill mid-write leaves: the first half of one more row, with no newline."""
    row = json.dumps({"model": "det", "kind": "local", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.01, "seconds": 0.0,
                      "retry": False})
    with open(out.with_name(out.name + ".build") / "usage_calls.jsonl", "ab") as fh:
        fh.write(row[: len(row) // 2].encode("utf-8"))


def test_a_torn_usage_tail_is_cut_and_charged_as_one_item_of_unknown_cost(tmp_path):
    clean, torn = _crashed(tmp_path, "clean"), _crashed(tmp_path, "torn")
    _tear_usage_tail(torn)
    resumed_clean = fixed.build(_records(), Priced(), clean, dict(DEFAULT_PROFILE), checkpoint_every=4,
                                max_relation_drop_frac=1.0, resume=True)
    resumed_torn = fixed.build(_records(), Priced(), torn, dict(DEFAULT_PROFILE), checkpoint_every=4,
                               max_relation_drop_frac=1.0, resume=True)
    assert resumed_torn["torn_usage_rows"] == 1 and "torn_usage_rows" not in resumed_clean
    assert resumed_torn["items"] == resumed_clean["items"] + 1 and resumed_torn["calls"] == resumed_clean["calls"] + 1
    assert resumed_torn["spend_usd"] == resumed_clean["spend_usd"]  # the placeholder adds no spend
    assert _files(torn) == _files(clean)  # the decisions are the same
    usage_doc = json.loads((torn / "usage.json").read_text(encoding="utf-8"))
    assert usage_doc["torn_usage_rows"] == 1
    placeholders = [call for call in usage_doc["calls"] if call.get("torn")]
    assert len(placeholders) == 1 and placeholders[0]["items"] == 1 and placeholders[0]["cost_usd"] == 0.0


def test_a_torn_resume_names_the_op_on_every_row_and_splits_the_phases_by_op(tmp_path):
    """The placeholder takes the op of the row before its cut; the report splits the resumed run's phases by op."""
    torn = _crashed(tmp_path, "torn")
    _tear_usage_tail(torn)
    fixed.build(_records(), Priced(), torn, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0, resume=True)
    calls = json.loads((torn / "usage.json").read_text(encoding="utf-8"))["calls"]
    assert all(call.get("op") in OP_PHASES for call in calls)
    (cut,) = [i for i, call in enumerate(calls) if call.get("torn")]
    assert calls[cut]["op"] == calls[cut - 1]["op"]
    speed = report.speed({"calls": calls}, {})
    real = [call for call in calls if not call.get("torn")]  # the placeholder is left out of the speed metrics
    expected = {phase: sum(OP_PHASES[call["op"]] == phase for call in real) for phase in report.PHASES}
    assert {phase: row["calls"] for phase, row in speed["phases"].items()} == {p: n for p, n in expected.items() if n}


def test_a_torn_first_row_takes_the_op_of_the_checkpoint_phase_or_none(tmp_path):
    """No row precedes the cut: the placeholder names the op of the checkpoint's phase (none for a phase without calls)."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "usage_calls.jsonl").write_text('{"model": "det", "kin', encoding="utf-8")
    plan = fixed._plan_resume_logs(work, {"phase": "relations", "usage_bytes": 0}, "det", 4)
    assert plan["placeholder"]["op"] == "decide_relation" and plan["placeholder"]["torn"] is True
    plan = fixed._plan_resume_logs(work, {"phase": "blocking", "usage_bytes": 0}, "det", 4)
    assert "op" not in plan["placeholder"]


def test_a_torn_usage_tail_counts_toward_max_calls(tmp_path):
    flags = dict(max_calls=44, outcomes=len(_records()))
    torn = _crashed(tmp_path, "torn", **flags)
    _tear_usage_tail(torn)
    resumed = fixed.build(_records(), Priced(), torn, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                          max_calls=44, resume=True)
    assert resumed["torn_usage_rows"] == 1 and resumed["items"] == 44 and resumed["calls_capped"] is True
    usage_doc = json.loads((torn / "usage.json").read_text(encoding="utf-8"))
    assert resumed["items"] == sum(call.get("items", 1) for call in usage_doc["calls"])


def test_a_corrupt_usage_row_before_the_last_one_refuses_the_resume_and_changes_nothing(tmp_path):
    out = _crashed(tmp_path, "bad")
    work = tmp_path / "bad.build"
    lines = (work / "usage_calls.jsonl").read_bytes().splitlines(keepends=True)
    assert len(lines) >= 3
    lines[1] = b"not json\n"
    (work / "usage_calls.jsonl").write_bytes(b"".join(lines))
    before = {name: (work / name).read_bytes() for name in ("usage_calls.jsonl", "decided.jsonl", "relations.jsonl")}
    with pytest.raises(ValueError, match="cannot resume: usage_calls.jsonl row 2 is corrupt inside the checkpointed rows"):
        fixed.build(_records(), Priced(), out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0, resume=True)
    assert {name: (work / name).read_bytes() for name in before} == before and not out.exists()


def test_a_resumed_group_share_ignores_the_discarded_batch_and_equals_the_uninterrupted_build(tmp_path):
    """The group share measures the group decisions committed, not the paid calls of a discarded batch. Bindings cost 3.5,
    a group decision 1.0, and a relation nothing; checkpoints every 2 decisions. A ceiling of 6.25 stops on the third
    group call, which overshoots: its batch is discarded and paid (usage 6.5, committed 5.5). Resumed with 10.0 the share
    (0.5 of 10.0, 5.0 decisions) leaves 3 decisions for the resume, as an uninterrupted build with 10.0 does. Counting the
    discarded call would leave 2 and a different theory."""
    flags = dict(checkpoint_every=2, max_relation_drop_frac=1.0, group_budget_frac=0.5)
    full = tmp_path / "full"
    full_summary = fixed.build(_records(), Staged(), full, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), **flags)
    assert full_summary["group_budget_stopped"] is True and full_summary["budget_stopped"] is False
    assert full_summary["group_candidates"] == 5

    out = tmp_path / "out"
    killed = fixed.build(_records(), Staged(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=6.25), keep_work=True, **flags)
    assert killed["budget_stopped"] is True and killed["stopped_in"] == "groups" and killed["spend_usd"] == 6.5
    checkpoint = json.loads((tmp_path / "out.build" / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["group_committed_spend"] == 5.5  # the spend of the committed decisions: the discarded call is after it
    shutil.rmtree(out)

    resumed = fixed.build(_records(), Staged(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), resume=True, **flags)
    assert resumed["group_budget_stopped"] is True and resumed["budget_stopped"] is False
    assert resumed["group_candidates"] == full_summary["group_candidates"]
    assert resumed["groups_committed"] == full_summary["groups_committed"]
    assert _files(out) == _files(full)  # the same group decisions, and so the same theory


def test_the_overall_spend_ceiling_counts_the_discarded_call_and_can_stop_a_resume_earlier(tmp_path):
    """The same resume under an overall ceiling of 8.0 (share 4.0): the discarded call is real money. The resume reaches
    the ceiling before its share, so it commits fewer group decisions than the uninterrupted build with 8.0 (documented)."""
    flags = dict(checkpoint_every=2, max_relation_drop_frac=1.0, group_budget_frac=0.5)
    full_summary = fixed.build(_records(), Staged(), tmp_path / "full", dict(DEFAULT_PROFILE, spend_ceiling_usd=8.0), **flags)
    assert full_summary["group_budget_stopped"] is True and full_summary["budget_stopped"] is False

    out = tmp_path / "out"
    fixed.build(_records(), Staged(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=6.25), keep_work=True, **flags)
    shutil.rmtree(out)
    resumed = fixed.build(_records(), Staged(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=8.0), resume=True, **flags)
    assert resumed["budget_stopped"] is True and resumed["stopped_in"] == "groups"
    assert resumed["groups_committed"] < full_summary["groups_committed"]


class Batched(Det):
    """Det answering in batches: one envelope per batch call, one usage row for it (cost 0.01 an item)."""

    supports_batch = True
    advertised_max_batch = {"decide_binding": 4, "decide_relation": 4, "decide_group": 4}

    def decide_batch(self, items, **kwargs):
        outcomes = [getattr(self, op)(*args)[0] for op, args in items]
        return outcomes, usage(self.name, cost_usd=0.01 * len(items))


def _crashed_batched(tmp_path, name):
    out = tmp_path / name
    crash = Batched(kill_after=10)
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), crash, out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                    batch_size=4)
    return out


def test_a_torn_batch_row_is_charged_as_a_batch_of_the_requested_size(tmp_path):
    """A torn final row of a batched build is a batch: the placeholder charges batch_size items, not one."""
    clean, torn = _crashed_batched(tmp_path, "clean"), _crashed_batched(tmp_path, "torn")
    _tear_usage_tail(torn)
    resumed_clean = fixed.build(_records(), Batched(), clean, dict(DEFAULT_PROFILE), checkpoint_every=4,
                                max_relation_drop_frac=1.0, batch_size=4, resume=True)
    resumed_torn = fixed.build(_records(), Batched(), torn, dict(DEFAULT_PROFILE), checkpoint_every=4,
                               max_relation_drop_frac=1.0, batch_size=4, resume=True)
    assert resumed_torn["torn_usage_rows"] == 1
    assert resumed_torn["items"] == resumed_clean["items"] + 4 and resumed_torn["calls"] == resumed_clean["calls"] + 1
    assert resumed_torn["spend_usd"] == resumed_clean["spend_usd"]
    usage_doc = json.loads((torn / "usage.json").read_text(encoding="utf-8"))
    placeholder = [call for call in usage_doc["calls"] if call.get("torn")]
    assert len(placeholder) == 1 and placeholder[0]["items"] == 4 and placeholder[0]["cost_usd"] == 0.0


def test_the_discarded_calls_seconds_start_the_resumed_clock(tmp_path, monkeypatch):
    """Each call takes one second of the gateway's clock. The rows a crash left past its checkpoint are paid and their
    seconds are real time: the resumed clock starts from the checkpoint's wall time plus those seconds."""
    real_gateway = fixed.Gateway
    clock = Clock()
    monkeypatch.setattr(fixed, "Gateway", lambda *args, **kwargs: real_gateway(*args, clock=clock, **kwargs))

    class Timed(Priced):
        def decide_relation(self, a, b):
            clock.t += 1.0
            return super().decide_relation(a, b)

    out = tmp_path / "out"
    crash = Timed()
    crash.kill_after = 10
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), crash, out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0)
    work = tmp_path / "out.build"
    checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    rows = (work / "usage_calls.jsonl").read_bytes().splitlines(keepends=True)
    offset, discarded = 0, 0.0
    for raw in rows:
        if offset >= checkpoint["usage_bytes"]:
            discarded += json.loads(raw)["seconds"]
        offset += len(raw)
    assert discarded > 0  # the crash left paid calls past the checkpoint

    clock.t = 0.0  # the resumed process starts its own clock
    resumed = fixed.build(_records(), Priced(), out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                          resume=True)
    assert resumed["wall_seconds"] == pytest.approx(checkpoint["wall_seconds"] + discarded, abs=1e-3)


class RetryKilled(Priced):
    """The third relation call fails once (a paid failure), and its retry is killed (Ctrl-C) before it is charged."""

    def __init__(self):
        super().__init__()
        self.relation_calls = 0

    def decide_relation(self, a, b):
        self.relation_calls += 1
        if self.relation_calls == 3:
            raise AdapterError("transient", usage=usage(self.name, cost_usd=0.02))
        if self.relation_calls == 4:
            raise KeyboardInterrupt("killed in the retry")
        return super().decide_relation(a, b)


def test_a_paid_call_is_on_disk_at_its_commit_even_if_the_build_is_killed_in_its_retry(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), RetryKilled(), out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0)
    rows = [json.loads(line) for line in (tmp_path / "out.build" / "usage_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    failed = [row for row in rows if row["cost_usd"] == 0.02]
    assert len(failed) == 1 and failed[0]["retry"] is False  # the paid failure is in the log, though its decision was never made


def test_a_resume_that_finds_a_short_log_changes_no_log(tmp_path):
    """Every check runs before any log is cut or appended: a relations log shorter than its checkpoint refuses the resume,
    and the decided log (which a check-by-check cut would have cut first) is left as it was."""
    killed = _crashed(tmp_path, "short")
    work = tmp_path / "short.build"
    (work / "decided.jsonl").write_bytes((work / "decided.jsonl").read_bytes() + b'{"junk": 1}\n')
    relations = work / "relations.jsonl"
    relations.write_bytes(relations.read_bytes()[: len(relations.read_bytes()) // 2])
    before = {path.name: path.read_bytes() for path in work.iterdir() if path.is_file()}
    with pytest.raises(ValueError, match="relations.jsonl is shorter than the checkpoint says"):
        fixed.build(_records(), Priced(), killed, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                    resume=True)
    assert {path.name: path.read_bytes() for path in work.iterdir() if path.is_file()} == before
    assert not killed.exists()


@pytest.mark.parametrize("bad_row,match", [
    ({"model": "det", "kind": "local", "tokens_in": 0, "tokens_out": 0, "cost_usd": "x", "seconds": 0.0, "retry": False},
     "row .* is not a valid usage entry"),
    ({"model": "det", "kind": "local", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "seconds": 0.0, "retry": False,
      "items": 0}, "row .* is not a valid usage entry"),
    ({"model": "det", "kind": "nope", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "seconds": 0.0, "retry": False},
     "row .* is not a valid usage entry"),
    ({"model": "det", "kind": "local", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "seconds": 0.0, "retry": False,
      "op": "decide_nope"}, "row .* is not a valid usage entry"),
])
def test_an_invalid_usage_row_refuses_the_resume_as_a_refusal_not_a_crash(tmp_path, bad_row, match):
    killed = _crashed(tmp_path, "bad")
    work = tmp_path / "bad.build"
    with open(work / "usage_calls.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(bad_row) + "\n")
    before = (work / "usage_calls.jsonl").read_bytes()
    with pytest.raises(ValueError, match=match):
        fixed.build(_records(), Priced(), killed, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                    resume=True)
    assert (work / "usage_calls.jsonl").read_bytes() == before

def test_a_resume_that_fails_keeps_the_work_dir_even_with_an_unreadable_checkpoint(tmp_path, monkeypatch, capsys):
    """An unreadable (corrupt or locked) checkpoint.json still stands for paid decisions: a failed resume keeps the work dir."""
    work = tmp_path / "out.build"
    work.mkdir()
    (work / "checkpoint.json").write_text("{not json", encoding="utf-8")
    (work / "decided.jsonl").write_text('{"id": "v:m1:1"}\n', encoding="utf-8")

    def locked(records, adapter, out_dir, profile, **kwargs):
        raise OSError("checkpoint.json is locked by another process")

    monkeypatch.setattr(cli.fixed, "build", locked)
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"id": "v:m1:1", "text": "Will X win?", "label": "Yes", "event_title": "E"}) + "\n",
                      encoding="utf-8")
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(tmp_path / "out"),
                     "--resume"]) == cli.EXIT_BUILD_INCOMPLETE
    assert (work / "decided.jsonl").read_text(encoding="utf-8") == '{"id": "v:m1:1"}\n'  # the decisions are still there
    assert (work / "checkpoint.json").exists() and not (tmp_path / "out").exists()
    assert "the checkpoint in" in capsys.readouterr().err


def _one_record_corpus(tmp_path):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(json.dumps({"id": "v:m1:1", "text": "Will X win?", "label": "Yes", "event_title": "E"}) + "\n",
                      encoding="utf-8")
    return corpus


def test_a_resume_with_nothing_to_resume_keeps_the_work_dir_it_found(tmp_path, capsys):
    """A kill before the first checkpoint can leave paid decisions in the work dir: a refused --resume keeps them."""
    work = tmp_path / "out.build"
    work.mkdir()
    (work / "decided.jsonl").write_text('{"id": "v:m1:1"}\n', encoding="utf-8")
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out",
                     str(tmp_path / "out"), "--resume"]) == cli.EXIT_USAGE
    assert (work / "decided.jsonl").read_text(encoding="utf-8") == '{"id": "v:m1:1"}\n'
    assert "nothing to resume" in capsys.readouterr().err


@pytest.mark.parametrize("content", ["{not json", "[1, 2]"])  # unreadable JSON, and JSON that is not an object
def test_a_corrupt_checkpoint_is_a_failed_build_that_names_the_file_and_keeps_the_work_dir(tmp_path, capsys, content):
    work = tmp_path / "out.build"
    work.mkdir()
    (work / "checkpoint.json").write_text(content, encoding="utf-8")
    (work / "decided.jsonl").write_text('{"id": "v:m1:1"}\n', encoding="utf-8")
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out",
                     str(tmp_path / "out"), "--resume"]) == cli.EXIT_BUILD_INCOMPLETE
    err = capsys.readouterr().err
    assert "build failed" in err and "checkpoint.json" in err and "refused" not in err
    assert (work / "checkpoint.json").read_text(encoding="utf-8") == content
    assert (work / "decided.jsonl").is_file() and not (tmp_path / "out").exists()


def test_a_build_that_finds_the_directories_held_leaves_the_other_builds_files(tmp_path, monkeypatch):
    out, work = tmp_path / "out", tmp_path / "out.build"

    def other_build_wins(records, adapter, out_dir, profile, **kwargs):
        # the first build got both directories and wrote into them; this one is refused and must touch nothing
        out_dir.mkdir()
        (out_dir / "theory.json").write_text("first", encoding="utf-8")
        work.mkdir()
        (work / "decided.jsonl").write_text("first", encoding="utf-8")
        raise FileExistsError(f"{work} is a leftover build")

    monkeypatch.setattr(cli.fixed, "build", other_build_wins)
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out",
                     str(out)]) == cli.EXIT_USAGE
    assert (out / "theory.json").read_text(encoding="utf-8") == "first"
    assert (work / "decided.jsonl").read_text(encoding="utf-8") == "first"


def test_a_failed_build_removes_an_empty_work_dir_that_was_already_there(tmp_path, monkeypatch):
    work = tmp_path / "out.build"
    work.mkdir()  # empty: it holds no decisions, so this build may remove it

    def fails_before_any_checkpoint(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(cli.fixed, "build", fails_before_any_checkpoint)
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out",
                     str(tmp_path / "out")]) == cli.EXIT_USAGE
    assert not work.exists()


def _logs(work):
    return {path.name: path.read_bytes() for path in work.iterdir() if path.is_file()}


def test_a_missing_usage_log_with_paid_calls_in_the_checkpoint_refuses_the_resume_and_changes_nothing(tmp_path):
    killed = _crashed(tmp_path, "gone")
    work = tmp_path / "gone.build"
    (work / "usage_calls.jsonl").unlink()
    before = _logs(work)
    with pytest.raises(ValueError, match="usage_calls.jsonl is missing, but the checkpoint counts"):
        fixed.build(_records(), Priced(), killed, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                    resume=True)
    assert _logs(work) == before and not killed.exists()


@pytest.mark.parametrize("key", ["decided_bytes", "usage_bytes", "wall_seconds", "budget_stopped"])
def test_a_checkpoint_missing_a_required_key_is_corrupt_and_changes_no_log(tmp_path, key):
    killed = _crashed(tmp_path, "malformed")
    work = tmp_path / "malformed.build"
    path = work / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    del ckpt[key]
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    before = _logs(work)
    with pytest.raises(fixed.CorruptCheckpoint, match=f"no {key}"):
        fixed.build(_records(), Priced(), killed, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                    resume=True)
    assert _logs(work) == before and work.exists() and not killed.exists()


def test_the_cli_reports_a_checkpoint_missing_decided_bytes_as_exit_3_and_keeps_the_work_dir(tmp_path, capsys):
    out = _crashed(tmp_path, "cli")
    work = tmp_path / "cli.build"
    path = work / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    del ckpt["decided_bytes"]
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    before = _logs(work)
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out", str(out),
                     "--resume"]) == cli.EXIT_BUILD_INCOMPLETE
    assert "no decided_bytes" in capsys.readouterr().err
    assert _logs(work) == before and work.exists()


def test_a_corrupt_checkpointed_row_refuses_the_resume_before_any_log_is_cut(tmp_path):
    killed = _crashed(tmp_path, "prefix")
    work = tmp_path / "prefix.build"
    relations = work / "relations.jsonl"
    data = bytearray(relations.read_bytes())
    data[0:1] = b"#"  # the first byte of the first row: the same length, no longer JSON
    relations.write_bytes(bytes(data))
    (work / "decided.jsonl").write_bytes((work / "decided.jsonl").read_bytes() + b'{"junk": 1}\n')  # past its checkpoint
    before = _logs(work)
    with pytest.raises(ValueError, match="relations.jsonl row 1 is corrupt"):
        fixed.build(_records(), Priced(), killed, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                    resume=True)
    assert _logs(work) == before


def test_a_checkpoint_whose_policy_is_not_an_object_exits_3_not_a_traceback(tmp_path, capsys):
    out = _crashed(tmp_path, "policy")
    work = tmp_path / "policy.build"
    path = work / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    ckpt["policy"] = ["not", "an", "object"]
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out", str(out),
                     "--resume"]) == cli.EXIT_BUILD_INCOMPLETE
    assert "policy is not an object" in capsys.readouterr().err and work.exists()


def test_a_failed_usage_write_still_counts_in_the_spend_and_the_cli_hint_points_at_resume(tmp_path, monkeypatch, capsys):
    from mentat_bench.gateway import Gateway

    class Paid:
        name = "paid"

        def decide_binding(self, ctx):
            decision = {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}
            return decision, usage(self.name, cost_usd=0.4)

    gw = Gateway(Paid(), spend_ceiling_usd=10.0, build_deadline_s=60.0)

    def disk_full(entry):
        raise OSError("disk full")

    gw.sink = disk_full
    with pytest.raises(OSError, match="disk full"):
        gw.decide_binding({"id": "x"})
    assert gw.spend_usd == pytest.approx(0.4)  # in memory, though in neither file

    def interrupted_by_a_full_disk(records, adapter, out_dir, profile, **kwargs):
        work = out_dir.with_name(out_dir.name + ".build")
        work.mkdir(parents=True)
        (work / "checkpoint.json").write_text("{}", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(cli.fixed, "build", interrupted_by_a_full_disk)
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out",
                     str(tmp_path / "out")]) == cli.EXIT_BUILD_INCOMPLETE
    err = capsys.readouterr().err
    assert "rerun the same command with --resume" in err and "finalize_checkpoint" not in err


def test_a_group_phase_interrupted_before_its_first_commit_resumes_with_its_discarded_spend(tmp_path):
    """The group phase persists its keys at its entry. A crash on the second group decision leaves one paid decision
    (1.0) uncommitted: the resume reports it as discarded_spend, and its group decisions and theory equal the uninterrupted
    build's (the share is 0.5 of 10.0, five decisions)."""
    flags = dict(checkpoint_every=2, max_relation_drop_frac=1.0, group_budget_frac=0.5)
    full = tmp_path / "full"
    fixed.build(_records(), Staged(), full, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), **flags)

    class KilledInTheSecondGroupDecision(Staged):
        def __init__(self):
            super().__init__()
            self.group_calls = 0

        def decide_group(self, ctx):
            self.group_calls += 1
            if self.group_calls == 2:
                raise KeyboardInterrupt("killed")
            return super().decide_group(ctx)

    out = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), KilledInTheSecondGroupDecision(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0),
                    keep_work=True, **flags)
    work = tmp_path / "out.build"
    checkpoint = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["phase"] == "groups" and checkpoint["group_committed_spend"] == 3.5  # persisted at the group's entry

    fixed.build(_records(), Staged(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), resume=True, keep_work=True, **flags)
    resumed_ckpt = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    assert resumed_ckpt["discarded_spend"] == pytest.approx(1.0)
    assert _files(out) == _files(full)


def test_a_bindings_phase_fault_with_paid_calls_keeps_the_work_dir(tmp_path, monkeypatch, capsys):
    """A sink fault (disk full) on the third paid binding: the two paid rows before it are on disk, so the work dir is kept
    with its usage log and the path and paid count are reported; nothing is removed."""
    real_dumps = fixed.ir.dumps
    seen = {"paid": 0}

    def faulty_dumps(obj, *args, **kwargs):
        if isinstance(obj, dict) and "retry" in obj and "cost_usd" in obj:
            seen["paid"] += 1
            if seen["paid"] == 3:
                raise OSError("disk full")
        return real_dumps(obj, *args, **kwargs)

    monkeypatch.setattr(fixed.ir, "dumps", faulty_dumps)
    out, work = tmp_path / "out", tmp_path / "out.build"
    with pytest.raises(OSError, match="disk full"):
        fixed.build(_records(), Priced(), out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0)
    assert work.is_dir() and not out.exists()
    assert len((work / "usage_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 2  # the paid rows before the fault
    assert "2 paid calls" in capsys.readouterr().err


def test_a_keyboard_interrupt_in_the_bindings_phase_with_paid_calls_keeps_the_work_dir(tmp_path, capsys):
    class KilledAtThirdBinding(Priced):
        def __init__(self):
            super().__init__()
            self.bindings = 0

        def decide_binding(self, ctx):
            self.bindings += 1
            if self.bindings == 3:
                raise KeyboardInterrupt("killed")
            return super().decide_binding(ctx)

    out, work = tmp_path / "out", tmp_path / "out.build"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), KilledAtThirdBinding(), out, dict(DEFAULT_PROFILE), checkpoint_every=4)
    assert work.is_dir() and not out.exists()
    assert len((work / "usage_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert "2 paid calls" in capsys.readouterr().err


def test_a_bindings_phase_failure_before_any_paid_call_removes_the_work_dir(tmp_path):
    class KilledBeforeAnyCharge(Priced):
        def decide_binding(self, ctx):
            raise KeyboardInterrupt("killed")

    out, work = tmp_path / "out", tmp_path / "out.build"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), KilledBeforeAnyCharge(), out, dict(DEFAULT_PROFILE), checkpoint_every=4)
    assert not work.exists() and not out.exists()


def test_a_corrupt_usage_row_inside_the_checkpointed_rows_is_damage_not_a_torn_tail(tmp_path):
    killed = _crashed(tmp_path, "inside")
    work = tmp_path / "inside.build"
    lines = (work / "usage_calls.jsonl").read_bytes().splitlines(keepends=True)
    lines[0] = b"#" + lines[0][1:]  # the first row, inside the checkpoint's rows
    (work / "usage_calls.jsonl").write_bytes(b"".join(lines))
    before = _logs(work)
    with pytest.raises(ValueError, match="row 1 is corrupt inside the checkpointed rows"):
        fixed.build(_records(), Priced(), killed, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0,
                    resume=True)
    assert _logs(work) == before


def test_a_checkpoint_with_an_unknown_phase_is_corrupt(tmp_path, capsys):
    out = _crashed(tmp_path, "phase")
    work = tmp_path / "phase.build"
    path = work / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    ckpt["phase"] = "warp"
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out", str(out),
                     "--resume"]) == cli.EXIT_BUILD_INCOMPLETE
    assert "'warp' is not a build phase" in capsys.readouterr().err and work.exists()


@pytest.mark.parametrize("key,value,problem", [
    ("pairs_considered", None, "pairs_considered is not a int"),
    ("relation_rejected", "x", "relation_rejected is not a int"),
    ("dedup_named", -1, "dedup_named is not a int"),
    ("pairs_ahead", "x", "pairs_ahead is not a int"),
    ("adapter_fatal", "no", "adapter_fatal is not a bool"),
])
def test_a_malformed_counter_is_corrupt_and_never_a_key_error(tmp_path, key, value, problem):
    out = _crashed(tmp_path, "counter")
    work = tmp_path / "counter.build"
    path = work / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    if value is None:
        del ckpt[key]
        problem = f"no {key}"
    else:
        ckpt[key] = value
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    before = _logs(work)
    with pytest.raises(fixed.CorruptCheckpoint, match=problem):
        fixed.build(_records(), Priced(), out, dict(DEFAULT_PROFILE), checkpoint_every=4, max_relation_drop_frac=1.0, resume=True)
    assert _logs(work) == before


def test_a_group_phase_checkpoint_without_its_group_keys_is_corrupt(tmp_path, capsys):
    flags = dict(checkpoint_every=2, max_relation_drop_frac=1.0, group_budget_frac=0.5)

    class KilledInGroups(Staged):
        def __init__(self):
            super().__init__()
            self.group_calls = 0

        def decide_group(self, ctx):
            self.group_calls += 1
            if self.group_calls == 2:
                raise KeyboardInterrupt("killed")
            return super().decide_group(ctx)

    out = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), KilledInGroups(), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), keep_work=True, **flags)
    work = tmp_path / "out.build"
    path = work / "checkpoint.json"
    ckpt = json.loads(path.read_text(encoding="utf-8"))
    assert ckpt["phase"] == "groups"
    del ckpt["group_candidates"]
    path.write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    before = _logs(work)
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out", str(out),
                     "--resume"]) == cli.EXIT_BUILD_INCOMPLETE
    assert "no group_candidates" in capsys.readouterr().err and _logs(work) == before


def test_a_group_share_stop_stays_final_when_the_relation_phase_is_interrupted(tmp_path):
    """The group phase ends with a checkpoint of phase groups_done before the relations, so a relation-phase crash resumes
    past the group phase: the share stop is final, and the group decisions and theory are the uninterrupted build's."""
    flags = dict(checkpoint_every=1000, max_relation_drop_frac=1.0, group_budget_frac=0.5)
    full = tmp_path / "full"
    full_summary = fixed.build(_records(), Staged(), full, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), **flags)
    assert full_summary["group_budget_stopped"] is True

    class CountingGroups(Staged):
        def __init__(self):
            super().__init__()
            self.group_calls = 0

        def decide_group(self, ctx):
            self.group_calls += 1
            return super().decide_group(ctx)

    out = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_records(), Staged(kill_after=1), out, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), keep_work=True, **flags)
    work = tmp_path / "out.build"
    assert json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))["phase"] == "groups_done"
    shutil.rmtree(out, ignore_errors=True)

    adapter = CountingGroups()
    resumed = fixed.build(_records(), adapter, out, dict(DEFAULT_PROFILE, spend_ceiling_usd=10.0), resume=True, **flags)
    assert adapter.group_calls == 0  # the group phase is not entered again
    assert resumed["group_budget_stopped"] is True and resumed["groups_committed"] == full_summary["groups_committed"]
    assert _files(out) == _files(full)


def test_the_cli_reports_a_binding_count_mismatch_as_a_failed_build_with_its_paid_calls(tmp_path, monkeypatch, capsys):
    work = tmp_path / "out.build"

    def mismatch(records, adapter, out_dir, profile, **kwargs):
        work.mkdir(parents=True)
        (work / "usage_calls.jsonl").write_text("{}\n", encoding="utf-8")
        raise fixed.BindingCountMismatch("max_calls was checked against 5 outcomes, but the bindings read 4")

    monkeypatch.setattr(cli.fixed, "build", mismatch)
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(_one_record_corpus(tmp_path)), "--out",
                     str(tmp_path / "out")]) == cli.EXIT_BUILD_INCOMPLETE
    err = capsys.readouterr().err
    assert "build failed: max_calls was checked against 5 outcomes" in err and "1 paid calls" in err and work.is_dir()

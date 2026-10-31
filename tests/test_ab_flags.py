"""The two opt-in A/B flags: --complement-fallback {all,flipped,none} (what a complement representative with no named answer
asks of its other three pairs; the skipped pairs are counted) and --pair-order {index,score} (the candidate pairs by blocking
score, highest first). Defaults keep the build of before the flags; both flags are resume policy."""

import hashlib
import json
import math
import re
import sys
from pathlib import Path

import pytest

from mentat_bench import cli, runcell
from mentat_bench.adapters.base import usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dedup_partial import DERIVED, _Answers  # noqa: E402
from test_cost_controls import _complement_gold  # noqa: E402
from test_resume import TIMING, Det as ResumeDet  # noqa: E402

REP_PAIR = ("v:alpha:1", "v:beta:1")  # the representative of _complement_gold (its first outcomes)
FALLBACK_ASKED = {"all": DERIVED, "flipped": {("v:alpha:2", "v:beta:2")}, "none": set()}
FALLBACK_SKIPPED = {"all": 0, "flipped": 2, "none": 3}


def _identity(summary):
    """Every pair the cursor covers is committed, derived, or a skipped fallback (under caps: pairs_considered)."""
    return summary["relations_committed"] + summary["pairs_derived"] + summary["pairs_derived_none"] + \
        summary["dedup_fallback_skipped"] == summary["pairs_considered"]


# ---- --complement-fallback --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("answer", ["NONE", "UNRESOLVED"])
@pytest.mark.parametrize("mode", ["all", "flipped", "none"])
def test_a_fallback_mode_asks_exactly_its_pairs(tmp_path, mode, answer):
    records, gold = _complement_gold(tmp_path)
    adapter = _Answers(gold, answer)
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), complement_fallback=mode)
    assert set(adapter.asked) & DERIVED == FALLBACK_ASKED[mode]
    assert summary["dedup_fallback_asked"] == len(FALLBACK_ASKED[mode])
    assert summary["dedup_fallback_skipped"] == FALLBACK_SKIPPED[mode]
    assert summary["dedup_representatives"] == 1 and summary["pairs_derived"] == 0
    assert summary["pairs_considered"] == summary["candidate_pairs"] and _identity(summary)
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))["complement_fallback"] == mode


@pytest.mark.parametrize("mode", ["all", "flipped", "none"])
def test_a_named_representative_is_the_same_in_every_fallback_mode(tmp_path, mode):
    """A named answer derives the three pairs without asking, so the fallback mode changes nothing."""
    records, gold = _complement_gold(tmp_path)
    adapter = _Answers(gold, "IMPLIES")
    summary = fixed.build(records, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), complement_fallback=mode)
    assert not (set(adapter.asked) & DERIVED)
    assert (summary["pairs_derived"], summary["pairs_derived_none"], summary["dedup_fallback_asked"],
            summary["dedup_fallback_skipped"]) == (2, 1, 0, 0)
    assert summary["pairs_considered"] == summary["candidate_pairs"] and _identity(summary)


# ---- --pair-order ------------------------------------------------------------------------------------------------------

def _brute_score(questions, n, i, j):
    """The blocking score of records i and j by the definition: the idf-weighted sum over their shared tokens (every
    token is kept on a corpus this small: DF_FLOOR is at least the market count)."""
    tokens = [fixed.token_set(q) for q in questions]
    df = {}
    for t in tokens:
        for w in t:
            df[w] = df.get(w, 0) + 1
    return sum(math.log(n / df[w]) for w in sorted(tokens[i] & tokens[j]))


def test_score_order_breaks_ties_by_the_index_order():
    scored = [(0.5, 0, 1), (0.9, 2, 3), (0.5, 4, 5), (0.1, 0, 2)]
    assert list(fixed._score_order(scored)) == [(2, 3), (0, 1), (4, 5), (0, 2)]


def test_blocking_scores_are_the_shared_token_weights_and_the_pairs_are_unchanged():
    questions = ["Will Oak win the league title?", "Will Ember win the cup final?", "Will Ember win the cup final?",
                 "Will Pine win the league?", "Will Ash win the cup?"]
    plain = list(fixed.iter_candidate_pairs(lambda: iter(questions), len(questions)))
    scored = list(fixed._scored_candidate_pairs(lambda: iter(questions), len(questions), scored=True))
    assert [(i, j) for _, i, j in scored] == plain
    for score, i, j in scored:
        assert score == pytest.approx(_brute_score(questions, len(questions), i, j), abs=1e-12)
    assert all(s == 0.0 for s, _, _ in fixed._scored_candidate_pairs(lambda: iter(questions), 5, scored=False))


FOUR = ["Will Oak win the league title?", "Will Ember win the cup final?", "Will Ember win the cup final?",
        "Will Pine win the league?"]  # index order of the pairs: (0,1) (0,2) (0,3) (1,2) (1,3) (2,3); by score (1,2) is first


def _four_records():
    return [{"id": f"v:m{k}:1", "question": q, "label": "Yes", "event_title": "E"} for k, q in enumerate(FOUR, start=1)]


class _Silent:
    """Unresolved bindings and NONE relations; records the pairs asked, in order."""

    name = "silent"

    def __init__(self):
        self.asked = []

    def decide_binding(self, ctx):
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}, usage(self.name)

    def decide_relation(self, a, b):
        self.asked.append((a["id"], b["id"]))
        return "NONE", usage(self.name)


def test_score_order_asks_the_highest_scoring_pair_first_and_the_cut_keeps_the_top(tmp_path):
    index = _Silent()
    fixed.build(_four_records(), index, tmp_path / "index", dict(DEFAULT_PROFILE), max_pairs=1)
    assert index.asked == [("v:m1:1", "v:m2:1")]  # the market index order starts with (0, 1)

    by_score = _Silent()
    fixed.build(_four_records(), by_score, tmp_path / "score", dict(DEFAULT_PROFILE), pair_order="score", max_pairs=2)
    assert by_score.asked == [("v:m2:1", "v:m3:1"), ("v:m1:1", "v:m4:1")]  # the top two by blocking score

    full = _Silent()
    summary = fixed.build(_four_records(), full, tmp_path / "all", dict(DEFAULT_PROFILE), pair_order="score")
    index = {rec["id"]: k for k, rec in enumerate(_four_records())}
    scores = [_brute_score(FOUR, len(FOUR), index[a], index[b]) for a, b in full.asked]
    assert scores == sorted(scores, reverse=True)  # every pair is asked in score order
    assert summary["candidate_pairs"] == len(full.asked) == 6 and summary["pairs_considered"] == 6 and _identity(summary)


def test_score_order_keeps_a_representative_before_its_complement_pairs(tmp_path):
    """Gamma is the first market, so the index order asks it first; alpha and beta score highest, so the score order asks
    their pair first, representative first, and the derived pairs are still never asked."""
    records, gold = _complement_gold(tmp_path)
    alpha_beta, gamma = records[:4], records[4:]
    for rec in alpha_beta:
        rec["question"] = "Will Ember win the cup final?"
    for rec in gamma:
        rec["question"] = "Will Oak win the league?"
    reordered = gamma + alpha_beta
    results = {}
    for order in ("index", "score"):
        adapter = _Answers(gold, "IMPLIES")
        summary = fixed.build(reordered, adapter, tmp_path / order, dict(DEFAULT_PROFILE), pair_order=order)
        assert not (set(adapter.asked) & DERIVED)
        results[order] = (adapter, summary)
    def market_pairs(adapter):  # the siblings of one market come first in both orders; the market pairs follow
        return [pair for pair in adapter.asked if pair[0].rsplit(":", 1)[0] != pair[1].rsplit(":", 1)[0]]

    assert market_pairs(results["index"][0])[0][0] == "v:gamma:1"
    assert market_pairs(results["score"][0])[0] == REP_PAIR
    for key in ("pairs_derived", "pairs_derived_none", "dedup_fallback_asked", "candidate_pairs", "relations_committed"):
        assert results["index"][1][key] == results["score"][1][key]


# ---- defaults and wiring -----------------------------------------------------------------------------------------------

DET_RECORDS = [{"id": f"v:m{k}:{n}", "question": f"Will M{k} win the cup?", "label": "Yes" if n == 1 else "No",
                "event_title": "E" if k < 4 else "F"} for k in range(12) for n in (1, 2)]


class _Det:
    """The deterministic adapter of the cross-tree check of the defaults (hash-chosen answers in every phase)."""

    name = "det"
    supports_groups = True

    def decide_binding(self, ctx):
        h = int(hashlib.sha256(ctx["id"].encode()).hexdigest(), 16)
        if h % 3 == 0:
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}, usage(self.name)
        return {"status": "bound", "class": f"c{h % 4}", "negated": (h // 4) % 2 == 0, "missing": []}, usage(self.name)

    def decide_relation(self, a, b):
        rels = ["NONE", "NONE", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "EQUIV", "UNRESOLVED"]
        return rels[int(hashlib.sha256((a["id"] + "|" + b["id"]).encode()).hexdigest(), 16) % len(rels)], usage(self.name)

    def decide_group(self, ctx):
        ids = [m["id"] for m in ctx["members"]]
        return ([{"kind": "at_most_one", "members": ids[:2]}] if len(ids) >= 2 else []), usage(self.name)


def test_the_defaults_build_the_same_theory_as_before_the_flags(tmp_path):
    """Pinned from a build of the code before these flags (main 5f43906): the same candidate pairs, decisions, derived
    and fallback counts, constraints and bound bindings. The explicit defaults write the same bytes as no flags."""
    summary = fixed.build(DET_RECORDS, _Det(), tmp_path / "default", dict(DEFAULT_PROFILE), checkpoint_every=4,
                          max_relation_drop_frac=1.0)
    assert {key: summary[key] for key in ("candidate_pairs", "relations_committed", "pairs_derived", "pairs_derived_none",
                                          "dedup_fallback_asked", "dedup_representatives", "items", "calls",
                                          "groups_committed", "constraints")} == {
        "candidate_pairs": 276, "relations_committed": 270, "pairs_derived": 4, "pairs_derived_none": 2,
        "dedup_fallback_asked": 3, "dedup_representatives": 3, "items": 308, "calls": 308, "groups_committed": 12,
        "constraints": 126}
    assert summary["dedup_fallback_skipped"] == 0
    rows = [json.loads(line) for line in (tmp_path / "default" / "bindings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 24 and sum(row["status"] == "bound" for row in rows) == 18
    explicit = fixed.build(DET_RECORDS, _Det(), tmp_path / "explicit", dict(DEFAULT_PROFILE), checkpoint_every=4,
                           max_relation_drop_frac=1.0, complement_fallback="all", pair_order="index")
    for name in ("theory.json", "bindings.jsonl", "provenance.jsonl"):
        assert (tmp_path / "explicit" / name).read_bytes() == (tmp_path / "default" / name).read_bytes()
    assert explicit["candidate_pairs"] == summary["candidate_pairs"] and explicit["items"] == summary["items"]
    policy = json.loads((tmp_path / "default" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["complement_fallback"] == "all" and policy["pair_order"] == "index"


@pytest.mark.parametrize("kwargs", [{"complement_fallback": "some"}, {"pair_order": "random"}])
def test_an_unknown_value_is_refused_before_anything_is_written(tmp_path, kwargs):
    records, gold = _complement_gold(tmp_path)
    with pytest.raises(ValueError, match="must be one of"):
        fixed.build(records, _Answers(gold, "NONE"), tmp_path / "sub", dict(DEFAULT_PROFILE), **kwargs)
    assert not (tmp_path / "sub").exists() and not (tmp_path / "sub.build").exists()


def test_the_cli_takes_both_flags_and_records_them(tmp_path, capsys):
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("".join(json.dumps({"id": f"v:m{i}:1", "text": f"Will X{i} win the cup?", "label": "Yes",
                                          "event_title": "E"}) + "\n" for i in range(1, 5)), encoding="utf-8")
    out = tmp_path / "out"
    assert cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(out),
                     "--complement-fallback", "flipped", "--pair-order", "score"]) == 0
    policy = json.loads((out / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["complement_fallback"] == "flipped" and policy["pair_order"] == "score"
    summary = json.loads(capsys.readouterr().out)
    assert summary["dedup_fallback_skipped"] == 0
    parser = cli.build_parser()
    for flag, bad in (("--complement-fallback", "some"), ("--pair-order", "random")):
        with pytest.raises(SystemExit):
            parser.parse_args(["build", "--adapter", "empty", "--corpus", "c", "--out", "o", flag, bad])


def test_runcell_forwards_the_flags_to_the_build_only_when_given(tmp_path, monkeypatch):
    seen = []

    def fake_run_cell(fixture, adapter, local_args, out, profile, build_flags, resume=False, extra_queries=False, candidates=None):
        seen.append(build_flags)
        return 0

    monkeypatch.setattr(runcell, "run_cell", fake_run_cell)
    base = ["--fixture", str(tmp_path), "--adapter", "empty", "--out", str(tmp_path / "cell")]
    assert runcell.main(base + ["--complement-fallback", "none", "--pair-order", "score"]) == 0
    flags = seen[-1]
    assert flags[flags.index("--complement-fallback") + 1] == "none" and flags[flags.index("--pair-order") + 1] == "score"
    assert runcell.main(base) == 0
    assert "--complement-fallback" not in seen[-1] and "--pair-order" not in seen[-1]


# ---- resume ------------------------------------------------------------------------------------------------------------

def _files(out):
    return {name: (out / name).read_bytes() for name in ("theory.json", "bindings.jsonl", "provenance.jsonl")}


class _ComplementDet(ResumeDet):
    """ResumeDet on complement markets: m1 to m4 are binary, each bound to one class with opposite negation (their first
    pairs are complement representatives); m5 is unresolved."""

    def decide_binding(self, ctx):
        _, market, n = ctx["id"].split(":")
        if market == "m5":
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}, usage(self.name)
        return {"status": "bound", "class": f"c{market[1:]}", "negated": n == "2", "missing": []}, usage(self.name)


def _complement_records():
    """Five markets on one question, so every pair is a candidate and the complement blocks straddle the windows."""
    return [{"id": f"v:m{k}:{n}", "question": "Will Ember win the cup?", "label": label, "event_title": "E"}
            for k in range(1, 6) for n, label in ((1, "Yes"), (2, "No"))]


KILL_WINDOW = 3  # checkpoint_every: the window that is killed ends inside a complement block (pairs_ahead > 0)


def _kill(path, **flags):
    """A build killed on its 8th relation call; returns the checkpoint it kept, which ends inside a complement block."""
    with pytest.raises(KeyboardInterrupt):
        fixed.build(_complement_records(), _ComplementDet(kill_after=7), path, dict(DEFAULT_PROFILE),
                    checkpoint_every=KILL_WINDOW, max_relation_drop_frac=1.0, **flags)
    checkpoint = json.loads((path.with_name(path.name + ".build") / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["pairs_ahead"] > 0
    return checkpoint


@pytest.mark.parametrize("key, value", [("complement_fallback", "flipped"), ("pair_order", "score")])
def test_a_resume_with_a_changed_flag_is_refused(tmp_path, key, value):
    killed = tmp_path / "killed"
    _kill(killed, **{key: value})
    with pytest.raises(ValueError, match=re.escape(f"cannot resume: the checkpoint was built with {key}={value!r}")):
        fixed.build(_complement_records(), _ComplementDet(), killed, dict(DEFAULT_PROFILE), checkpoint_every=KILL_WINDOW,
                    resume=True, max_relation_drop_frac=1.0)
    assert not killed.exists() and (tmp_path / "killed.build").exists()  # the checkpoint is kept for the right flag
    resumed = fixed.build(_complement_records(), _ComplementDet(), killed, dict(DEFAULT_PROFILE), checkpoint_every=KILL_WINDOW,
                          resume=True, max_relation_drop_frac=1.0, **{key: value})
    assert resumed["resumed"] is True


def test_a_checkpoint_without_the_new_keys_resumes_as_the_defaults(tmp_path):
    full = tmp_path / "full"
    fixed.build(_complement_records(), _ComplementDet(), full, dict(DEFAULT_PROFILE), checkpoint_every=KILL_WINDOW,
                max_relation_drop_frac=1.0)
    killed = tmp_path / "killed"
    _kill(killed)
    work = tmp_path / "killed.build"
    ckpt = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    for key in ("complement_fallback", "pair_order"):
        del ckpt["policy"][key]
    ckpt.pop("dedup_fallback_skipped", None)
    ckpt["pairs_considered"] -= ckpt.pop("pairs_ahead")  # a checkpoint from before pairs_ahead: pairs_considered is the cursor
    (work / "checkpoint.json").write_text(json.dumps(ckpt, sort_keys=True), encoding="utf-8")
    resumed = fixed.build(_complement_records(), _ComplementDet(), killed, dict(DEFAULT_PROFILE), checkpoint_every=KILL_WINDOW,
                          resume=True, max_relation_drop_frac=1.0)
    assert resumed["resumed"] is True and resumed["dedup_fallback_skipped"] == 0
    assert _identity(resumed) and resumed["pairs_considered"] == resumed["candidate_pairs"]
    assert _files(killed) == _files(full)
    policy = json.loads((killed / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["complement_fallback"] == "all" and policy["pair_order"] == "index"  # the defaults, written at finalize
    assert not (tmp_path / "killed.build").exists()


# ---- every kind of stop: the cursor and the identity -------------------------------------------------------------------

class _Priced(_Answers):
    """_Answers that answers NONE for the representative too (so the fallback runs), charges `cost` per relation call, and
    moves a fake clock past the deadline after `jump_after` calls."""

    def __init__(self, gold, *, cost=0.0, jump_after=None, now=None):
        super().__init__(gold, "NONE")
        self.cost, self.jump_after, self.now = cost, jump_after, now

    def decide_relation(self, a, b):
        self.asked.append((a["id"], b["id"]))
        if self.jump_after is not None and len(self.asked) > self.jump_after:
            self.now[0] = 10_000.0
        return "NONE", usage(self.name, cost_usd=self.cost)


@pytest.mark.parametrize("stop", ["spend", "deadline", "calls", "pairs"])
@pytest.mark.parametrize("mode", ["all", "flipped", "none"])
def test_the_cursor_and_the_identity_hold_at_every_kind_of_stop(tmp_path, monkeypatch, mode, stop):
    records, gold = _complement_gold(tmp_path)
    profile, kwargs, now = dict(DEFAULT_PROFILE), {}, [0.0]
    adapter = _Priced(gold, cost=0.01 if stop == "spend" else 0.0, jump_after=3 if stop == "deadline" else None, now=now)
    if stop == "spend":
        profile["spend_ceiling_usd"] = 0.035  # four priced calls, then the ceiling refuses the fifth
    if stop == "deadline":
        real_gateway = fixed.Gateway
        monkeypatch.setattr(fixed, "Gateway", lambda *a, **k: real_gateway(*a, clock=lambda: now[0], **k))
        profile["build_deadline_s"] = 1000.0
    if stop == "calls":
        kwargs["max_calls"], kwargs["outcomes"] = 10, len(records)  # six bindings, then four decisions
    if stop == "pairs":
        kwargs["max_pairs"] = 5
    summary = fixed.build(records, adapter, tmp_path / "sub", profile, checkpoint_every=2, complement_fallback=mode, **kwargs)
    assert _identity(summary), summary
    assert summary["pairs_considered"] < summary["candidate_pairs"]
    assert {"spend": summary["budget_stopped"], "deadline": summary["budget_stopped"],
            "calls": summary["calls_capped"], "pairs": summary["pairs_capped"]}[stop] is True
    if stop == "calls":
        assert summary["items"] <= 10  # never past the cap; a representative reserves its fallbacks, so some may go unused


# ---- a score-order build interrupted mid-relations resumes to the same bytes ---------------------------------------------

@pytest.mark.parametrize("order", ["index", "score"])
@pytest.mark.parametrize("mode", ["all", "flipped"])
def test_a_resume_inside_a_complement_block_equals_the_uninterrupted_build(tmp_path, order, mode):
    flags = {"pair_order": order, "complement_fallback": mode}
    profile = dict(DEFAULT_PROFILE)
    full = tmp_path / "full"
    full_summary = fixed.build(_complement_records(), _ComplementDet(), full, profile, checkpoint_every=KILL_WINDOW,
                               max_relation_drop_frac=1.0, **flags)
    killed = tmp_path / "killed"
    checkpoint = _kill(killed, **flags)
    assert checkpoint["phase"] == "relations" and 0 < checkpoint["pairs_considered"] < checkpoint["pairs_total"]
    assert checkpoint.get("budget_stopped") is False  # a crash, not a stop: the resume continues the relation phase
    resumed = fixed.build(_complement_records(), _ComplementDet(), killed, profile, checkpoint_every=KILL_WINDOW, resume=True,
                          max_relation_drop_frac=1.0, **flags)
    for name in ("theory.json", "bindings.jsonl", "provenance.jsonl"):
        assert (killed / name).read_bytes() == (full / name).read_bytes()
    assert resumed["resumed"] is True
    same = {k: v for k, v in resumed.items() if k not in TIMING}
    assert same == {k: v for k, v in full_summary.items() if k not in TIMING}
    assert json.loads((killed / "build_policy.json").read_text(encoding="utf-8"))["pair_order"] == order


def test_the_report_shows_the_flags_and_the_skipped_count_and_fills_the_defaults(tmp_path):
    from mentat_bench import report

    def run(name, policy, build):
        path = tmp_path / name
        (path / "submission").mkdir(parents=True)
        (path / "score.json").write_text("{}", encoding="utf-8")
        (path / "submission" / "build_policy.json").write_text(json.dumps(policy), encoding="utf-8")
        (path / "build_summary.json").write_text(json.dumps(build), encoding="utf-8")
        return report.run_row(path)

    new = run("new", {"complement_fallback": "flipped", "pair_order": "score"}, {"dedup_fallback_skipped": 2})
    old = run("old", {"top_k": 64}, {})  # a build from before the flags
    assert (new["complement_fallback"], new["pair_order"], new["dedup_fallback_skipped"]) == ("flipped", "score", 2)
    assert (old["complement_fallback"], old["pair_order"], old["dedup_fallback_skipped"]) == ("all", "index", None)
    keys = {key for _, key in report.COLUMNS}
    assert {"complement_fallback", "pair_order", "dedup_fallback_skipped"} <= keys


# ---- defaults: the batched call sequence of main, and the identity under retries and window sizes -----------------------

REFERENCE = json.loads((Path(__file__).resolve().parent / "ab_batch_reference.json").read_text(encoding="utf-8"))


def test_the_default_build_asks_and_bills_the_batches_exactly_as_main_did(tmp_path):
    """tests/ab_batch_reference.json is the same run on an export of main (5f43906): every batch call (op and items), every
    usage entry's item count, the summary counts and the theory's digests. checkpoint_every 7 does not match the batch
    sizes (4 and 3), and the complement blocks straddle the windows."""
    from helpers_ab_batch import run

    assert run(tmp_path) == REFERENCE


def test_a_retry_inside_a_capped_relation_batch_keeps_the_identity(tmp_path):
    from helpers_ab_batch import FailOnce, records

    for cap in (14 + 4, 14 + 9, 14 + 20, 14 + 40):
        out = tmp_path / f"cap{cap}"
        summary = fixed.build(records(), FailOnce(("v:m0:1", "v:m0:2")), out, dict(DEFAULT_PROFILE), checkpoint_every=7,
                              max_relation_drop_frac=1.0, max_calls=cap, outcomes=len(records()))
        assert _identity(summary), (cap, summary)
        calls = json.loads((out / "usage.json").read_text(encoding="utf-8"))["calls"]
        if cap == 14 + 4:  # the 14 bindings and the batch's first attempts leave no item for the retry: no retry
            assert not any(call.get("retry") for call in calls)
            assert summary["items"] == cap
        else:  # the failed batch was retried inside the relation phase
            assert any(call.get("retry") for call in calls), cap


@pytest.mark.parametrize("mode", ["all", "flipped", "none"])
@pytest.mark.parametrize("window", [1, 2, 3, 5])
def test_the_identity_holds_for_every_pair_cap_and_window(tmp_path, mode, window):
    records, gold = _complement_gold(tmp_path)
    for cap in range(1, 16):
        summary = fixed.build(records, _Answers(gold, "NONE"), tmp_path / f"c{cap}", dict(DEFAULT_PROFILE),
                              checkpoint_every=window, complement_fallback=mode, max_pairs=cap)
        assert _identity(summary), (cap, summary)
        assert summary["pairs_considered"] <= cap + 3  # at most a representative's three derived pairs past the cap


@pytest.mark.parametrize("window", [1, 2, 3, 5, 7])
def test_default_decisions_do_not_depend_on_the_checkpoint_window(tmp_path, window):
    """The window moves the checkpoints and the batch boundaries only: the default counts and theory are the same at any
    checkpoint_every, including the windows that end inside a complement block."""
    reference = fixed.build(_complement_records(), _ComplementDet(), tmp_path / "ref", dict(DEFAULT_PROFILE),
                            checkpoint_every=KILL_WINDOW, max_relation_drop_frac=1.0)
    summary = fixed.build(_complement_records(), _ComplementDet(), tmp_path / "w", dict(DEFAULT_PROFILE),
                          checkpoint_every=window, max_relation_drop_frac=1.0)
    assert {k: v for k, v in summary.items() if k not in TIMING} == {k: v for k, v in reference.items() if k not in TIMING}
    assert _identity(summary) and summary["pairs_derived"] > 0  # the complement blocks were derived, not only asked
    assert (tmp_path / "w" / "theory.json").read_bytes() == (tmp_path / "ref" / "theory.json").read_bytes()

"""Cost controls: complement dedup (the transformation table against a brute-force truth table, the rows a representative
derives, the entailment equivalence with and without dedup on mb-engine), the max_pairs and max_calls caps, the
top-k sweep on tiny data, and the call counters of a build."""

import json
from array import array

import pytest

from mentat_bench import cli, engine, ir
from mentat_bench.adapters.base import AdapterError
from mentat_bench.adapters.mock import GoldViewAdapter
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import _COMPLEMENT, _complement_mates, _complement_rows, _dedup_role
from mentat_bench.harness.topk_sweep import recall_by_k, smallest_k, sweep
from mentat_bench.manifest import DEFAULT_PROFILE

HAVE_ENGINE = engine.engine_available()
NAMED = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")


def _holds(rel, a, b):
    """The truth of relation `rel` between propositions a and b (0/1), read as the formulas of docs/ir.md."""
    return {"EQUIV": a == b, "IMPLIES": (not a) or bool(b), "IMPLIED_BY": (not b) or bool(a),
            "EXCLUDES": not (a and b), "NONE": True}[rel]


def _allowed(rel):
    return {(a, b) for a in (0, 1) for b in (0, 1) if _holds(rel, a, b)}


# ---- complement dedup: derivation tables ------------------------------------------------------------------------

@pytest.mark.parametrize("fa,fb", [(1, 0), (0, 1), (1, 1)])
@pytest.mark.parametrize("rel", NAMED + ("UNRESOLVED",))
def test_complement_table_matches_the_truth_table(rel, fa, fb):
    """Brute force over the four outcome values consistent with the complements (a2 = not a1, b2 = not b1): the
    representative (a1, b1) holds in some worlds; the derived relation of (a2, b1), (a1, b2) or (a2, b2) must hold in
    every one of them (sound), and it is the tightest named relation that does (exact when the image is named)."""
    derived = _COMPLEMENT[rel, fa, fb]
    if rel == "UNRESOLVED":
        assert derived == "UNRESOLVED"
        return
    worlds = [(a, b) for a in (0, 1) for b in (0, 1)]
    satisfied = [(a, b) for a, b in worlds if _holds(rel, a, b)]
    image = {(a ^ fa, b ^ fb) for a, b in satisfied}
    assert all(_holds(derived, *pair) for pair in image)
    sound = [name for name in NAMED if image <= _allowed(name)]
    assert derived == min(sound, key=lambda name: len(_allowed(name)))
    exact = [name for name in NAMED if _allowed(name) == image]
    if exact:
        assert derived == exact[0]


def test_inexact_cases_are_the_documented_weakenings():
    assert _COMPLEMENT["EQUIV", 1, 0] == "EXCLUDES"  # XOR has no name: the tightest sound one
    assert _COMPLEMENT["IMPLIES", 1, 0] == "NONE"  # a2 or b: no named relation
    assert _COMPLEMENT["EXCLUDES", 1, 1] == "NONE"  # a2 or b2
    assert _COMPLEMENT["IMPLIES", 0, 1] == "EXCLUDES" and _COMPLEMENT["IMPLIES", 1, 1] == "IMPLIED_BY"


def test_derived_rows_are_sound_for_the_representative_and_normalised():
    """Records 0 and 1 are one market (complements), 2 and 3 another. The rows of a representative (0, 2) are checked
    against a truth table over the four values with v1 = not v0 and v3 = not v2, and come out with x < y."""
    mate = array("i", [1, 0, 3, 2])
    worlds = [(v0, v2) for v0 in (0, 1) for v2 in (0, 1)]
    for rel in NAMED:
        rows = _complement_rows(0, 2, rel, mate)
        assert [(x, y) for x, y, _ in rows] == [(1, 2), (0, 3), (1, 3)]
        assert all(x < y for x, y, _ in rows)
        for v0, v2 in worlds:
            if not _holds(rel, v0, v2):
                continue
            values = {0: v0, 1: 1 - v0, 2: v2, 3: 1 - v2}
            for x, y, r in rows:
                assert _holds(r, values[x], values[y]), (rel, x, y, r)


def test_rows_that_point_backwards_are_swapped_into_order():
    """Markets {0, 2} and {1, 3}: the representative (0, 1) is EXCLUDES, so b1 implies a2, and the row (2, 1) with
    IMPLIED_BY is written as (1, 2) with IMPLIES. Checked against the truth table with v2 = not v0 and v3 = not v1."""
    mate = array("i", [2, 3, 0, 1])
    rows = _complement_rows(0, 1, "EXCLUDES", mate)
    assert (1, 2, "IMPLIES") in rows
    for v0 in (0, 1):
        for v1 in (0, 1):
            if not _holds("EXCLUDES", v0, v1):
                continue
            values = {0: v0, 1: v1, 2: 1 - v0, 3: 1 - v1}
            for x, y, r in rows:
                assert _holds(r, values[x], values[y]), (x, y, r)


def test_mates_need_two_records_bound_to_one_class_with_opposite_negation():
    unit_of = array("I", [0, 0, 1, 1, 2, 2, 3])
    cls_id = array("i", [5, 5, 6, 6, 7, 7, -1])
    neg = bytearray([0, 1, 0, 0, 0, 1, 0])  # unit 0 complements; unit 1 same polarity; unit 2 complements; 3 unbound
    mate = _complement_mates(unit_of, 4, cls_id, neg)
    assert list(mate) == [1, 0, -1, -1, 5, 4, -1]  # unit 1 is not complements; unit 3 has one record


def test_dedup_role_marks_representatives_and_derived_pairs():
    unit_of = array("I", [0, 0, 1, 1])
    mate = array("i", [1, 0, 3, 2])
    assert _dedup_role(0, 2, mate, unit_of) == "representative"
    assert _dedup_role(1, 2, mate, unit_of) == "derived"
    assert _dedup_role(1, 3, mate, unit_of) == "derived"
    assert _dedup_role(0, 1, mate, unit_of) == "plain"  # siblings of one market
    assert _dedup_role(0, 2, None, unit_of) == "plain"  # dedup off


# ---- complement dedup: builds ----------------------------------------------------------------------------------

def _complement_gold(tmp_path):
    """Markets alpha and beta are binary and bound as complements; gamma is unresolved. Gold: Alpha implies Beta, and
    its contrapositive (complement-symmetric, so the gold answers of the derived pairs are the derived relations)."""
    rows = [
        {"id": "v:alpha:1", "status": "bound", "class": "CA", "negated": False, "missing": []},
        {"id": "v:alpha:2", "status": "bound", "class": "CA", "negated": True, "missing": []},
        {"id": "v:beta:1", "status": "bound", "class": "CB", "negated": False, "missing": []},
        {"id": "v:beta:2", "status": "bound", "class": "CB", "negated": True, "missing": []},
        {"id": "v:gamma:1", "status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]},
        {"id": "v:gamma:2", "status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]},
    ]
    constraints = [
        {"id": "c1", "expr": {"op": "implies", "a": ir.var("v:alpha:1"), "b": ir.var("v:beta:1")}, "interps": None},
        {"id": "c2", "expr": {"op": "implies", "a": ir.var("v:beta:2"), "b": ir.var("v:alpha:2")}, "interps": None},
    ]
    gold = tmp_path / "gold"
    gold.mkdir()
    ir.write_jsonl(gold / "bindings.jsonl", rows)
    ir.write_json(gold / "theory.json", {"interpretations": ["base"], "aliases": {}, "constraints": constraints})
    records = [{"id": r["id"], "question": q, "label": lab, "event_title": "E"} for r, q, lab in (
        (rows[0], "Will Alpha win?", "Yes"), (rows[1], "Will Alpha win?", "No"),
        (rows[2], "Will Beta win?", "Yes"), (rows[3], "Will Beta win?", "No"),
        (rows[4], "Will Gamma win?", "Yes"), (rows[5], "Will Gamma win?", "No"))]
    return records, gold


QUERIES = [
    {"family": "closure", "qid": "closure-01", "hyp": []},
    {"family": "selection", "qid": "selection-01", "relation": "implies", "target": ir.var("v:beta:1")},
    {"family": "selection", "qid": "selection-02", "relation": "excludes", "target": ir.var("v:alpha:2")},
    {"family": "feasibility", "qid": "feasibility-01", "assume": [ir.var("v:alpha:1"), ir.var("v:beta:2")]},
    {"family": "feasibility", "qid": "feasibility-02", "assume": [ir.var("v:alpha:2")]},
    {"family": "partition", "qid": "partition-01", "claim": "exclusive", "target": None,
     "members": [ir.var("v:alpha:1"), ir.var("v:alpha:2")]},
]


@pytest.mark.skipif(not HAVE_ENGINE, reason="mb-engine not built")
def test_dedup_on_and_off_give_byte_identical_engine_answers(tmp_path):
    """Entailment equivalence: the representative's derived rows follow from the bindings and the representative, so
    the theory with dedup entails the same answers as the theory with every pair asked (here the exact gold relations)."""
    records, gold = _complement_gold(tmp_path)
    queries = tmp_path / "queries.jsonl"
    queries.write_text("".join(json.dumps(q, sort_keys=True) + "\n" for q in QUERIES), encoding="utf-8")
    answers = {}
    for dedup in (True, False):
        out = tmp_path / ("on" if dedup else "off")
        summary = fixed.build(records, GoldViewAdapter(gold), out, dict(DEFAULT_PROFILE), complement_dedup=dedup)
        assert summary["pairs_derived"] == (2 if dedup else 0)  # IMPLIES flipping a derives NONE, not counted
        answers[dedup] = tmp_path / f"answers_{dedup}.jsonl"
        engine.eval_submission(out, queries, answers[dedup])
    assert answers[True].read_bytes() == answers[False].read_bytes()
    assert answers[True].read_bytes().strip()  # there are answers to compare


def test_dedup_derives_three_pairs_and_asks_the_rest(tmp_path):
    """Candidates = sibling pairs + record pairs of the unit pairs. The alpha-beta unit pair asks one representative
    and derives three pairs; every other candidate is asked. Blocking recall is measured on all candidates."""
    records, gold = _complement_gold(tmp_path)
    summary = fixed.build(records, GoldViewAdapter(gold), tmp_path / "on", dict(DEFAULT_PROFILE))
    assert summary["pairs_derived"] == 2 and summary["pairs_derived_none"] == 1  # IMPLIES: a-flip NONE, b-flip and both named
    assert summary["dedup_named"] == 1 and summary["dedup_fallback_asked"] == 0
    assert summary["relations_committed"] + summary["pairs_derived"] + summary["pairs_derived_none"] == summary["candidate_pairs"]
    off = fixed.build(records, GoldViewAdapter(gold), tmp_path / "off", dict(DEFAULT_PROFILE), complement_dedup=False)
    assert off["pairs_derived"] == 0 and off["relations_committed"] == off["candidate_pairs"] == summary["candidate_pairs"]
    assert json.loads((tmp_path / "on" / "build_policy.json").read_text())["complement_dedup"] is True


class _NoAnswers:
    """Unresolved bindings and NONE relations: no dedup applies, every candidate is asked."""

    name = "none-answers"

    def __init__(self):
        self.relation_calls = 0

    def decide_binding(self, ctx):
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_source"]}, _usage(self.name)

    def decide_relation(self, a, b):
        self.relation_calls += 1
        return "NONE", _usage(self.name)


def _usage(model):
    return {"model": model, "kind": "local", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.0}


def _records(n):
    return [{"id": f"v:m{i}:1", "question": f"Will X{i} win the cup?", "label": "Yes", "event_title": "E"} for i in range(1, n + 1)]


def test_max_calls_caps_the_relation_phase_and_counts_decisions(tmp_path):
    summary = fixed.build(_records(4), _NoAnswers(), tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=6, outcomes=4)
    assert summary["items"] == 6 and summary["calls_capped"] is True
    assert summary["relations_committed"] == 2  # 4 bindings, then the 2 remaining decisions
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text())["max_calls"] == 6


def test_a_fresh_build_with_a_cap_below_its_outcome_count_is_refused_before_any_call(tmp_path):
    adapter = _NoAnswers()
    with pytest.raises(ValueError, match="max_calls 2 is below the 4 binding calls this build makes"):
        fixed.build(_records(4), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=2, outcomes=4)
    assert adapter.relation_calls == 0 and not (tmp_path / "sub").exists() and not (tmp_path / "sub.build").exists()
    with pytest.raises(ValueError, match="max_calls needs outcomes"):
        fixed.build(_records(4), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=6)


def test_the_cli_refuses_a_max_calls_below_the_outcome_count_with_the_usage_exit(tmp_path, capsys):
    corpus = tmp_path / "corpus.jsonl"
    lines = [json.dumps({"id": r["id"], "text": r["question"], "label": r["label"], "event_title": r["event_title"]})
             for r in _records(4)]
    corpus.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code = cli.main(["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(tmp_path / "out"), "--max-calls", "3"])
    assert code == cli.EXIT_USAGE
    assert "max_calls 3 is below the 4 binding calls this build makes" in capsys.readouterr().err
    assert not (tmp_path / "out").exists() and not (tmp_path / "out.build").exists()


class _BindingFailsOnce(_NoAnswers):
    """The first binding call fails once with a transient error; the others answer."""

    def __init__(self):
        super().__init__()
        self.failed = False

    def decide_binding(self, ctx):
        if not self.failed:
            self.failed = True
            raise AdapterError("transient", usage=_usage(self.name))
        return super().decide_binding(ctx)


def test_a_binding_retry_never_takes_an_item_a_later_binding_needs(tmp_path):
    """Four outcomes under a cap of four: the first binding fails once. Its retry would take the fourth binding's item, so it
    is not made: the failed outcome is unresolved, every binding has its first attempt, and the cap is met exactly."""
    summary = fixed.build(_records(4), _BindingFailsOnce(), tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=4, outcomes=4)
    assert summary["items"] == summary["calls"] == 4 and summary["calls_capped"] is True
    usage_doc = json.loads((tmp_path / "sub" / "usage.json").read_text(encoding="utf-8"))
    assert not any(call.get("retry") for call in usage_doc["calls"])
    bindings = [json.loads(line) for line in (tmp_path / "sub" / "bindings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(bindings) == 4 and bindings[0]["status"] == "unresolved" and bindings[0]["missing"] == ["adapter_error"]


def test_the_cli_refuses_a_max_calls_before_the_adapter_starts(tmp_path, monkeypatch, capsys):
    started = []

    def start(args):
        started.append(args)
        raise AssertionError("the adapter must not start for a refused build")

    monkeypatch.setattr(cli, "_make_adapter", start)
    corpus = tmp_path / "corpus.jsonl"
    lines = [json.dumps({"id": r["id"], "text": r["question"], "label": r["label"], "event_title": r["event_title"]})
             for r in _records(4)]
    corpus.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code = cli.main(["build", "--adapter", "local", "--local-arg", "x", "--corpus", str(corpus), "--out", str(tmp_path / "out"),
                     "--max-calls", "3"])
    assert code == cli.EXIT_USAGE and started == []
    assert "max_calls 3 is below the 4 binding calls" in capsys.readouterr().err


def test_a_cap_equal_to_the_outcome_count_leaves_no_item_for_the_relations(tmp_path):
    summary = fixed.build(_records(4), _NoAnswers(), tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=4, outcomes=4)
    assert summary["items"] == 4 and summary["calls_capped"] is True and summary["relations_committed"] == 0


def test_max_pairs_caps_the_candidates_the_relation_phase_covers(tmp_path):
    adapter = _NoAnswers()
    summary = fixed.build(_records(4), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), max_pairs=2)
    assert summary["pairs_capped"] is True and summary["relations_committed"] == adapter.relation_calls == 2
    assert summary["candidate_pairs"] > 2  # blocking, and so blocking recall, is not capped


def test_uncapped_build_reports_no_caps_and_all_items(tmp_path):
    adapter = _NoAnswers()
    summary = fixed.build(_records(3), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["pairs_capped"] is False and summary["calls_capped"] is False
    assert summary["items"] == summary["calls"] == 3 + summary["relations_committed"]


# ---- top-k sweep -----------------------------------------------------------------------------------------------

def _tiny_fixture(tmp_path):
    """Six outcomes of two events; gold pairs link questions that share words, so recall grows with K."""
    fixture = tmp_path / "tiny"
    (fixture / "gold").mkdir(parents=True)
    questions = ["Will Ember win the cup?", "Will Ember win the final?", "Will Ash win the cup?",
                 "Will Ash win the final?", "Will Oak win the league?", "Will Oak win the title?"]
    with open(fixture / "corpus.jsonl", "w", encoding="utf-8") as fh:
        for n, text in enumerate(questions, start=1):
            fh.write(json.dumps({"id": f"toy:m{n}:1", "text": text, "label": "Yes", "event_title": "E"}) + "\n")
    constraints = [{"id": "c1", "expr": {"op": "implies", "a": ir.var("toy:m1:1"), "b": ir.var("toy:m2:1")}, "interps": None},
                   {"id": "c2", "expr": {"op": "iff", "a": ir.var("toy:m3:1"), "b": ir.var("toy:m4:1")}, "interps": None},
                   {"id": "c3", "expr": {"op": "atmost", "k": 1, "args": [ir.var("toy:m5:1"), ir.var("toy:m6:1")]},
                    "interps": None}]
    ir.write_json(fixture / "gold" / "theory.json", {"interpretations": ["base"], "aliases": {}, "constraints": constraints})
    return fixture


def test_topk_sweep_recall_grows_with_k_and_reports_the_smallest_k(tmp_path, capsys):
    from mentat_bench.harness import topk_sweep

    fixture = _tiny_fixture(tmp_path)
    result = sweep([fixture], [1, 2, 3, 8], 0.98)
    rows = result["fixtures"][fixture.name]["rows"]
    recalls = [row["blocking_recall"] for row in rows]
    assert recalls == sorted(recalls)  # monotone in K
    assert recalls[-1] == 1.0  # every partner fits in the top 8 of a six-market corpus
    assert result["fixtures"][fixture.name]["smallest_k"] == smallest_k(rows, 0.98)
    assert smallest_k(rows, 0.98) is not None and rows[0]["candidates"] <= rows[-1]["candidates"]
    assert result["smallest_k_all"] == smallest_k(rows, 0.98)

    assert topk_sweep.main(["--fixture", str(fixture), "--k", "2", "--k", "8"]) == 0
    text = capsys.readouterr().out
    assert "smallest top_k with recall >= 0.98 on every fixture" in text


@pytest.mark.slow
def test_the_build_default_top_k_reaches_documented_target_on_toy10k():
    """The default K reaches the 0.98 target on the retained fixture at its pinned measured recall."""
    from pathlib import Path

    from mentat_bench import cli
    from mentat_bench.harness import topk_sweep

    args = cli.build_parser().parse_args(["build", "--adapter", "empty", "--corpus", "c", "--out", "o", "--profile", "p"])
    assert args.top_k is None  # cmd_build applies cli.BUILD_TOP_K (or a resumed checkpoint's value)
    assert cli.BUILD_TOP_K == 64
    repo = Path(__file__).resolve().parents[1]
    records, gold_pairs = topk_sweep.load_fixture(repo / "fixtures" / "toy10k")
    (row,) = recall_by_k(records, gold_pairs, [cli.BUILD_TOP_K])
    assert row["blocking_recall"] >= topk_sweep.TARGET, row
    assert row["blocking_recall"] == pytest.approx(0.9928, abs=5e-5), row


def test_topk_rows_match_candidate_pairs(tmp_path):
    fixture = _tiny_fixture(tmp_path)
    records = [{"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r["event_title"]}
               for r in ir.read_jsonl(fixture / "corpus.jsonl")]
    (row,) = recall_by_k(records, [("toy:m1:1", "toy:m2:1")], [4])
    assert row["candidates"] == len(fixed.candidate_pairs(records, top_k=4))
    assert row["blocking_recall"] == 1.0


# ---- call counters of a build ----------------------------------------------------------------------------------

def test_items_and_calls_are_counted_by_the_build(tmp_path):
    summary = fixed.build(_records(3), _NoAnswers(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    usage = json.loads((tmp_path / "sub" / "usage.json").read_text(encoding="utf-8"))
    assert summary["calls"] == len(usage["calls"]) and summary["items"] == sum(c.get("items", 1) for c in usage["calls"])
    assert summary["items"] == 3 + summary["relations_committed"]


# ---- --max-calls is a hard cap: retries count, a cut keeps the complete units --------------------------------------------



class _FailFirst(_NoAnswers):
    """Every relation is NONE, except that the first call of the pair (x, y) fails once with a transient error."""

    def __init__(self, pair):
        super().__init__()
        self.pair, self.failed = pair, False

    def decide_relation(self, a, b):
        if not self.failed and {a["id"], b["id"]} == set(self.pair):
            self.failed = True
            raise AdapterError("transient", usage=_usage(self.name))
        return ("IMPLIES" if {a["id"], b["id"]} == set(self.pair) else "NONE"), _usage(self.name)


def _identity_holds(summary):
    return (summary["relations_committed"] + summary["pairs_derived"] + summary["pairs_derived_none"]
            + summary["dedup_fallback_skipped"] == summary["pairs_considered"])


def test_a_retry_that_fits_under_the_cap_is_made_and_its_answer_kept(tmp_path):
    """Four bindings, then six pairs: the first pair fails once. Its retry fits (the cap is 4 + 6 + 1), so every pair is
    answered and the cap is met exactly."""
    records = _records(4)
    summary = fixed.build(records, _FailFirst(("v:m1:1", "v:m2:1")), tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=11, outcomes=4)
    assert summary["items"] == 11 and summary["calls"] == 11 and summary["calls_capped"] is False
    assert summary["relations_committed"] == 6 and _identity_holds(summary)
    assert summary["constraints"] == 1  # the retried pair's IMPLIES reached the theory
    usage_doc = json.loads((tmp_path / "sub" / "usage.json").read_text(encoding="utf-8"))
    assert sum(1 for call in usage_doc["calls"] if call.get("retry")) == 1


def test_a_cap_hit_by_a_retry_keeps_the_complete_units_and_stops(tmp_path):
    """The same build with a cap of 4 + 6: the retry takes the item the last pair needed. The five pairs answered before it
    are kept (paid answers are not discarded), the sixth is not asked, and the identity holds."""
    records = _records(4)
    summary = fixed.build(records, _FailFirst(("v:m1:1", "v:m2:1")), tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=10, outcomes=4)
    assert summary["items"] == 10 and summary["calls_capped"] is True
    assert summary["relations_committed"] == 5 and _identity_holds(summary)
    assert summary["constraints"] == 1


def test_a_retry_that_does_not_fit_leaves_its_item_unresolved(tmp_path):
    """A cap of 4 + 1: the one pair asked fails, and there is no item left for its retry. It is decided UNRESOLVED (no
    constraint) and the build never passes the cap."""
    summary = fixed.build(_records(4), _FailFirst(("v:m1:1", "v:m2:1")), tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=5, outcomes=4)
    assert summary["items"] == 5 and summary["calls_capped"] is True
    assert summary["relations_committed"] == 1 and summary["constraints"] == 0 and _identity_holds(summary)


def test_complete_units_of_a_batch_under_the_cap():
    """_complete_prefix: a plain pair is complete once answered; a representative is complete when its answer is named,
    or when its fallbacks are answered too; derived pairs ride with their representative."""
    plain, rep, der = "plain", "representative", "derived"
    roles = [plain, rep, der, der, der, plain]
    assert fixed._complete_prefix(roles, [("NONE", None), ("NONE", None)], 3, 3) == (5, 2, 3)  # the rep's fallbacks in
    assert fixed._complete_prefix(roles, [("NONE", None), ("NONE", None)], 1, 3) == (1, 1, 0)  # rep's fallbacks cut
    assert fixed._complete_prefix(roles, [("NONE", None)], 0, 3) == (1, 1, 0)  # the rep is not asked
    assert fixed._complete_prefix(roles, [("NONE", None), ("IMPLIES", None), ("NONE", None)], 0, 3) == (6, 3, 0)
    assert fixed._complete_prefix([der, plain], [("NONE", None)], 0, 3) == (2, 1, 0)  # a derived run left by a batch


def test_a_max_calls_checked_against_another_outcome_count_is_refused_and_keeps_the_work_dir(tmp_path):
    """The bindings read four outcomes, not the five the cap was checked against: the build refuses, names the mismatch,
    and keeps its usage log and work dir (the paid calls are on disk), so the reserve is never wrong."""
    with pytest.raises(fixed.BindingCountMismatch, match="max_calls was checked against 5 outcomes, but the bindings read 4"):
        fixed.build(_records(4), _NoAnswers(), tmp_path / "sub", dict(DEFAULT_PROFILE), max_calls=6, outcomes=5)
    assert not (tmp_path / "sub").exists() and (tmp_path / "sub.build" / "usage_calls.jsonl").exists()

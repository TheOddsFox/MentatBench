"""tools/eval/release_analysis.py (MEASUREMENT ONLY): the v0.1.1 analysis of the committed v0.1.0 results.

Hand cases on the module's helpers: the Holm step-down (the spec's four p-values, the cap at 1), the pairwise p-value
(mixed signs, ties on both sides, zero differences, all-positive, NaN draws), the rank intervals (a swap, ties, integers),
the depth buckets (one league, empty, the n < 30 and 99% rules, the league-weighted ratio), the item audit (all wrong,
inverted, the rank-8 model left out of both halves, r_pb null, the modal prediction, binding and reading rows ignored), the
binding detection (weighted AUROC and average precision against sklearn on the repeated items), the weighting schemes
(robust and weight-dependent pairs), the robustness summary (presentation over every shuffle model, presentation_matched over
the run-to-run models, the Vulkan folding), the cost ratio, the judged block and the small helpers (the repeats folder, the
README splice, the 6-decimal rounding and the NaN refusal).

Contract tests read the committed results/v0.1.1/analysis.json and check its shape and cross-block consistency against the
leaderboard, the flagged list, the committed predictions and the committed hashes. Slow tests (MB_RUN_SLOW=1 or -m slow)
rebuild the analysis in memory: the consistency pass makes each build about 11 minutes.
"""

import gzip
import hashlib
import json
import math
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pytest

from mentat_bench import corrections

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "eval"))
import release_analysis as RA  # noqa: E402

MODELS15 = [f"m{i:02d}" for i in range(15)]  # rank order: m00 is the highest point
POINTS15 = {m: 15.0 - i for i, m in enumerate(MODELS15)}
OP_POINTS = {"decide_relation": POINTS15, "decide_group": POINTS15}  # the item audit ranks the models by each op's MCC
FAM_OF = {m: ("d3", "d1", "tev1")[i % 3] for i, m in enumerate(MODELS15)}
FAMS = ("headline", "relation", "group")
SCHEMES = ["relation_only", "r75", "published", "r25", "group_only"]
BINDING_RUNS_WITHOUT_PROBABILITIES = {"heuristic", "lexical", "embedding", "gold_view", "hf"}


def _reject_constant(token):
    raise ValueError(f"non-finite JSON constant {token}")


def _committed():
    return json.loads(RA.ANALYSIS.read_text(encoding="utf-8"), parse_constant=_reject_constant)


def _leaderboard():
    return json.loads(RA.LEADERBOARD.read_text(encoding="utf-8"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _predictions(rel_path: str) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress((RA.ROOT / rel_path).read_bytes()).decode("utf-8").splitlines()]


# ---------------------------------------------------------------- Holm and the pairwise p-value

def _matrix(groups, n: int) -> np.ndarray:
    """Draws as value matrices (models x draws). A group is (count, ascending order of the models): the models take the
    values 0, 1, ... in that order, so the pairs are ordered by the group's order and no two values tie."""
    cols = []
    for count, order in groups:
        pos = np.empty(n)
        for k, m in enumerate(order):
            pos[m] = k
        cols.append(np.repeat(pos[:, None], count, axis=1))
    return np.hstack(cols)


def _p_two_models(D) -> float:
    """The raw p of the single pair of two models whose per-draw difference is D (a - b)."""
    D = np.asarray(D, dtype=float)
    V = np.vstack([D, np.zeros_like(D)])
    return RA._pairwise(["a", "b"], V, [0.0, 0.0])[0]["p"]


def test_holm_hand_case_from_spec():
    """Six pairs of four models, with raw p = [.001, .002, .005, .01, .03, .04] (pairs in triu order: 0-1 .04, 0-2 .005,
    0-3 .001, 1-2 .03, 1-3 .002, 2-3 .01). The four p-values of the spec, [.01, .04, .03, .005], sit under step-down multipliers
    4, 3, 2, 1 and so adjust to [.03, .06, .06, .02]; the two smaller p-values are the family's first two steps."""
    # B = 1999 draws, so p = 2(1 + k)/2000 with k the count of draws where a pair is inverted (the minority side)
    groups = [(1922, (0, 1, 2, 3)), (4, (1, 2, 0, 3)), (35, (1, 0, 2, 3)), (28, (0, 2, 1, 3)), (1, (0, 2, 3, 1)),
              (9, (0, 1, 3, 2))]
    V = _matrix(groups, 4)
    assert V.shape == (4, 1999)
    pairs = RA._pairwise(MODELS15[:4], V, [4.0, 3.0, 2.0, 1.0])
    got = {(pr["a"], pr["b"]): (pr["p"], pr["p_holm"], pr["resolved"]) for pr in pairs}
    raw = {("m00", "m01"): .04, ("m00", "m02"): .005, ("m00", "m03"): .001, ("m01", "m02"): .03,
           ("m01", "m03"): .002, ("m02", "m03"): .01}
    for key, p in raw.items():
        assert got[key][0] == pytest.approx(p, abs=1e-12), key
    assert got[("m02", "m03")][1] == pytest.approx(.03)  # raw .01 -> .03 (multiplier 3)
    assert got[("m01", "m02")][1] == pytest.approx(.06)  # raw .03 -> .06 (multiplier 2)
    assert got[("m00", "m01")][1] == pytest.approx(.06)  # raw .04 -> .06 (multiplier 1, kept at the running max)
    assert got[("m00", "m02")][1] == pytest.approx(.02)  # raw .005 -> .02 (multiplier 4)
    # the adjusted values never fall as the raw p rises, and never fall below the raw p
    ordered = sorted(got.values(), key=lambda v: v[0])
    assert all(a[1] <= b[1] for a, b in zip(ordered, ordered[1:]))
    assert all(v[1] >= v[0] for v in got.values())
    # resolved is adjusted p <= .05, and higher is the model with the larger point (here the first of each pair)
    assert {k for k, v in got.items() if v[2]} == {("m00", "m02"), ("m00", "m03"), ("m01", "m03"), ("m02", "m03")}
    by_pair = {(pr["a"], pr["b"]): pr for pr in pairs}
    assert by_pair[("m00", "m02")]["higher"] == "m00"
    assert by_pair[("m01", "m02")]["higher"] is None


def test_holm_is_capped_at_one():
    """Six pairs with p = .5 each (299 draws of 1199 invert every pair): the step-down multipliers reach 6, so every
    adjusted value is capped at 1 and none is above it."""
    V = _matrix([(299, (3, 2, 1, 0)), (900, (0, 1, 2, 3))], 4)
    pairs = RA._pairwise(MODELS15[:4], V, [4.0, 3.0, 2.0, 1.0])
    assert all(pr["p"] == pytest.approx(.5) for pr in pairs)
    assert all(pr["p_holm"] == 1.0 for pr in pairs)
    assert all(not pr["resolved"] and pr["higher"] is None for pr in pairs)


def test_p_value_formula_with_mixed_signs():
    """B = 10 with two negative and eight positive differences: p = 2 (1 + min(2, 8)) / 11 = 6/11, and flipping the sign
    of every difference gives the same p."""
    D = [-1, -1, 1, 1, 1, 1, 1, 1, 1, 1]
    assert _p_two_models(D) == pytest.approx(6 / 11)
    assert _p_two_models([-x for x in D]) == pytest.approx(6 / 11)


def test_p_value_counts_ties_on_both_sides():
    """Four zero differences and six positive ones: the zeros count in #(<= 0), so p = 2 (1 + 4) / 11 = 10/11. Counting them
    on one side only would give 2/11."""
    assert _p_two_models([0, 0, 0, 0, 1, 1, 1, 1, 1, 1]) == pytest.approx(10 / 11)
    assert _p_two_models([0, 0, 0, 0, -1, -1, -1, -1, -1, -1]) == pytest.approx(10 / 11)


def test_p_value_zero_difference_is_one():
    assert _p_two_models([0.0] * 7) == 1.0


def test_p_value_all_positive_is_the_smallest_p():
    """No draw at or below zero: p = 2 / (1 + B), the smallest value the formula can give. Same for all negative."""
    assert _p_two_models([1.0] * 9) == pytest.approx(2 / 10)
    assert _p_two_models([-1.0] * 9) == pytest.approx(2 / 10)


def test_p_value_denominator_counts_only_valid_draws():
    """Two NaN draws are not valid: B_valid = 5, the minority count is 1 (one negative), so p = 2 (1 + 1) / 6 = 2/3.
    Using B = 7 in the denominator would give 4/8."""
    assert _p_two_models([np.nan, np.nan, 1, 1, 1, 1, -1]) == pytest.approx(2 / 3)


def test_p_value_with_no_valid_draw_is_one():
    assert _p_two_models([np.nan] * 5) == 1.0


def test_single_pair_holm_is_the_raw_p():
    """One pair is its own family: the adjusted p equals the raw p."""
    pr = RA._pairwise(["a", "b"], np.vstack([[1.0, 2.0, 3.0, -1.0], np.zeros(4)]), [1.0, 0.0])[0]
    assert pr["p_holm"] == pytest.approx(pr["p"])


# ---------------------------------------------------------------- rank intervals

def test_rank_intervals_with_a_swap_and_a_fixed_leader():
    """A is always first. B and C swap: B is above C in 60 of 100 draws. B's rank is 2 in 60 draws and 3 in 40; C's is the
    reverse. The 2.5% and 97.5% ranges are [2, 3] for both; the medians follow the majority (2 for B, 3 for C)."""
    draws = np.arange(100)
    V = np.vstack([np.full(100, 3.0), np.where(draws < 60, 2.0, 1.0), np.where(draws < 60, 1.0, 2.0)])
    r = RA._ranks(["A", "B", "C"], V, [3.0, 1.5, 1.4])
    assert r["A"] == {"rank": 1, "ci95": [1, 1], "median": 1}
    assert r["B"] == {"rank": 2, "ci95": [2, 3], "median": 2}
    assert r["C"] == {"rank": 3, "ci95": [2, 3], "median": 3}


def test_rank_ties_share_a_rank_and_outputs_are_integers():
    """A and B equal in every draw: neither has a model strictly above it, so both rank 1; C ranks 3 (two above it)."""
    V = np.array([[3.0] * 5, [3.0] * 5, [1.0] * 5])
    r = RA._ranks(["A", "B", "C"], V, [2.0, 2.0, 1.0])
    assert r["A"] == {"rank": 1, "ci95": [1, 1], "median": 1}
    assert r["B"] == {"rank": 1, "ci95": [1, 1], "median": 1}
    assert r["C"] == {"rank": 3, "ci95": [3, 3], "median": 3}
    for entry in r.values():
        assert type(entry["rank"]) is int and all(type(x) is int for x in entry["ci95"]) and type(entry["median"]) is int


# ---------------------------------------------------------------- depth

def _depth(cells: dict, W: np.ndarray) -> dict:
    """The depth blocks of one run. cells maps (league index, bucket index) to (n, correct); the arrays are (leagues, buckets)
    as _depth_counts builds them. Returns the block of the run, keyed by bucket name."""
    n = np.zeros((2, len(RA.DEPTH_BUCKETS)), dtype=np.int64)
    corr = np.zeros_like(n)
    for (league, bucket), (n_cell, c_cell) in cells.items():
        n[league, bucket], corr[league, bucket] = n_cell, c_cell
    return RA._depth_block({"run": (corr, n)}, W)["run"]


def test_depth_bucket_present_in_one_league_only():
    """Bucket '0' has items in league 0 only. A draw with no league-0 pick has no denominator: valid_draws counts the rest,
    and the point is the plain ratio (not a draw)."""
    W = RA.LC.draws(RA.B, RA.SEED, 2)
    bucket = _depth({(0, 0): (40, 30)}, W)["0"]
    assert bucket["valid_draws"] == int((W[:, 0] > 0).sum())
    assert 0 < bucket["valid_draws"] < RA.B
    assert bucket["point"] == pytest.approx(30 / 40)
    assert bucket["ci95"] == pytest.approx([0.75, 0.75])  # every valid draw has the ratio 30/40
    assert bucket["degenerate"] is True


def test_depth_empty_bucket_has_no_interval_and_is_degenerate():
    W = RA.LC.draws(RA.B, RA.SEED, 2)
    block = _depth({(0, 0): (10, 5), (1, 0): (10, 5)}, W)
    assert block["4+"] == {"n": 0, "correct": 0, "point": None, "ci95": None, "valid_draws": 0, "degenerate": True}
    assert block["0"]["point"] == pytest.approx(0.5) and block["0"]["valid_draws"] == RA.B


def test_depth_n_below_30_is_degenerate_and_30_is_not():
    """Both leagues hold items, so every draw is valid. n = 29 is degenerate; n = 30 is not (the 99% rule passes)."""
    W = RA.LC.draws(RA.B, RA.SEED, 2)
    at_29 = _depth({(0, 0): (14, 10), (1, 0): (15, 11)}, W)["0"]
    at_30 = _depth({(0, 0): (15, 10), (1, 0): (15, 11)}, W)["0"]
    assert at_29["n"] == 29 and at_29["valid_draws"] == RA.B and at_29["degenerate"] is True
    assert at_30["n"] == 30 and at_30["valid_draws"] == RA.B and at_30["degenerate"] is False


def test_depth_99_percent_valid_rule_at_the_boundary():
    """The module's 99% rule divides by the module's B (20000), so the draws here are 20000 rows. With 200 draws with no
    denominator, valid = 19800 = 99%: not degenerate. With 201, valid is below 99%: degenerate."""
    base = np.ones((RA.B, 2), dtype=np.int64)
    n = np.zeros((2, 6), dtype=np.int64)
    corr = np.zeros_like(n)
    n[0, 0], corr[0, 0] = 40, 30  # league 0 holds the bucket; a draw with no league-0 pick has no denominator
    for no_denominator, degenerate in ((200, False), (201, True)):
        W = base.copy()
        W[:no_denominator, 0] = 0
        bucket = RA._depth_block({"run": (corr, n)}, W)["run"]["0"]
        assert bucket["valid_draws"] == RA.B - no_denominator
        assert bucket["degenerate"] is degenerate


def test_depth_ratio_is_league_weighted_and_its_interval_comes_from_the_draws():
    """League 0: 7 of 10 correct; league 1: 5 of 20. Half the draws take league 0 only (ratio 0.7), half league 1 only
    (ratio 0.25). The point is the plain ratio 12/30 = 0.4; the interval is the draws' 2.5 and 97.5 percentiles, [0.25, 0.7]."""
    W = np.vstack([np.tile([[2, 0]], (RA.B // 2, 1)), np.tile([[0, 2]], (RA.B // 2, 1))])
    bucket = _depth({(0, 0): (10, 7), (1, 0): (20, 5)}, W)["0"]
    assert bucket["point"] == pytest.approx(12 / 30)
    assert bucket["ci95"] == pytest.approx([0.25, 0.7])
    assert bucket["valid_draws"] == RA.B
    # a draw that takes both leagues once is the pooled ratio 12/30, not the mean of the two league ratios (0.475)
    both = _depth({(0, 0): (10, 7), (1, 0): (20, 5)}, np.ones((RA.B, 2), dtype=np.int64))["0"]
    assert both["ci95"] == pytest.approx([0.4, 0.4])


def test_depth_counts_map_depth_4_to_4plus_and_none_to_none():
    fx = {"leagues": ["L1", "L2"], "item_league": {"q1": 0, "q2": 1, "q3": 1}}
    rows = [
        {"qid": "q1", "op": "decide_relation", "gold": "IMPLIES", "pred": "IMPLIES", "depth": 4},
        {"qid": "q2", "op": "decide_relation", "gold": "NONE", "pred": "NONE", "depth": None},
        {"qid": "q3", "op": "decide_relation", "gold": "EXCLUDES", "pred": "NONE", "depth": 3},
        {"qid": "q3", "op": "decide_group", "gold": {}, "pred": {}, "depth": None},
    ]
    corr, n = RA._depth_counts(rows, fx)
    b = RA.DEPTH_BUCKETS.index
    assert (n[0, b("4+")], corr[0, b("4+")]) == (1, 1)
    assert (n[1, b("NONE")], corr[1, b("NONE")]) == (1, 1)
    assert (n[1, b("3")], corr[1, b("3")]) == (1, 0)
    assert n.sum() == 3  # the group row is not a relation


def test_depth_counts_refuse_a_depth_that_does_not_match_the_gold():
    fx = {"leagues": ["L1"], "item_league": {"q1": 0}}
    with pytest.raises(RA.CheckError):
        RA._depth_counts([{"qid": "q1", "op": "decide_relation", "gold": "NONE", "pred": "NONE", "depth": 2}], fx)
    with pytest.raises(RA.CheckError):
        RA._depth_counts([{"qid": "q1", "op": "decide_relation", "gold": "IMPLIES", "pred": "IMPLIES", "depth": None}], fx)


# ---------------------------------------------------------------- binding detection

def test_binding_draws_equal_sklearn_on_the_repeated_items():
    """Each draw weights an item by its league's count, so the weighted AUROC and AP equal sklearn's on the items repeated that
    many times. Scores are rounded to two decimals on purpose, so ties are common."""
    metrics = pytest.importorskip("sklearn.metrics")
    rng = np.random.default_rng(1)
    for trial in range(5):
        n = 300
        scores = np.round(rng.random(n), 2)
        positive = rng.random(n) < 0.1
        league = rng.integers(0, 6, size=n)
        W = RA.LC.draws(40, trial, 6)
        auc, ap = RA._binding_draws(scores, positive, league, W)
        for d in range(len(W)):
            rep = np.repeat(np.arange(n), W[d, league])
            y, s = positive[rep], scores[rep]
            if y.sum() == 0 or (~y).sum() == 0:
                assert np.isnan(auc[d])
                continue
            assert auc[d] == pytest.approx(metrics.roc_auc_score(y, s), abs=1e-12)
            assert ap[d] == pytest.approx(metrics.average_precision_score(y, s), abs=1e-12)
        a1, p1 = RA._binding_draws(scores, positive, league, np.ones((1, 6), dtype=np.int64))
        assert a1[0] == pytest.approx(metrics.roc_auc_score(positive, scores), abs=1e-12)
        assert p1[0] == pytest.approx(metrics.average_precision_score(positive, scores), abs=1e-12)


def test_binding_draws_nan_without_a_positive_or_a_negative():
    """Only positives: AUROC is undefined in every draw. A draw whose league holds no positive has NaN AP, and the rest give 1."""
    scores = np.array([0.1, 0.2, 0.3])
    positive = np.array([True, True, True])
    league = np.array([0, 1, 0])
    W = np.array([[1, 1], [0, 1], [1, 0]])
    auc, ap = RA._binding_draws(scores, positive, league, W)
    assert np.isnan(auc).all()
    assert np.allclose(ap, 1.0)
    # one positive (item 0, league 0) and one negative (item 2, league 0): a draw without league 0 has no positive, NaN AP;
    # with league 0 the negative (.3) ranks above the positive (.1): precision 1/2 at the positive's recall 1
    mixed = np.array([True, False, False])
    _, ap_mixed = RA._binding_draws(scores, mixed, league, np.array([[0, 1], [1, 0]]))
    assert np.isnan(ap_mixed[0]) and ap_mixed[1] == pytest.approx(0.5)


def test_binding_run_flags_by_argmax_and_copies_the_mcc():
    fx = {"item_league": {"b1": 0, "b2": 1, "b3": 0}}
    rows = [
        {"qid": "b1", "op": "decide_binding", "gold": "unresolved", "pred": "bound",
         "probs": {"bound": 0.6, "unresolved": 0.4}},
        {"qid": "b2", "op": "decide_binding", "gold": "bound", "pred": "unresolved",
         "probs": {"bound": 0.3, "unresolved": 0.7}},
        {"qid": "b3", "op": "decide_binding", "gold": "bound", "pred": "bound",
         "probs": {"bound": 0.5, "unresolved": 0.5}},  # a tie is not flagged
    ]
    entry = {"id": "x", "metrics": {"binding": {"point": 0.25, "ci95": None}}}
    block = RA._binding_run(rows, entry, fx, np.ones((1, 2), dtype=np.int64))
    assert block["n"] == 3 and block["n_unresolved"] == 1 and block["prevalence"] == pytest.approx(1 / 3)
    assert block["flagged"] == 1 and block["mcc"] == 0.25
    # the one unresolved item scores .4, below both bound items (.7 and .5): no pair ranks it higher, so AUROC is 0
    assert block["auroc"]["point"] == pytest.approx(0.0)
    # sorted by score, the unresolved item comes last, at precision 1/3 over recall 1
    assert block["average_precision"]["point"] == pytest.approx(1 / 3)


def test_binding_run_without_probabilities_has_null_scores():
    fx = {"item_league": {"b1": 0}}
    rows = [{"qid": "b1", "op": "decide_binding", "gold": "unresolved", "pred": "unresolved"}]
    block = RA._binding_run(rows, {"id": "h", "metrics": {"binding": None}}, fx, np.ones((1, 1), dtype=np.int64))
    assert block == {"n": 1, "n_unresolved": 1, "prevalence": 1.0, "flagged": 1, "mcc": None, "auroc": None,
                     "average_precision": None}


# ---------------------------------------------------------------- weighting

def test_weighting_classifies_robust_and_weight_dependent_pairs():
    """Three models, 200 constant draws, so every nonzero difference has p = 2/201 and the three-pair family resolves at
    alpha. A beats C on both parts (robust). A beats B on relation by .8 and ties on group: resolved only where relation has
    weight (weight dependent). B and C swap with the weight: C above B on relation, B above C on group (weight dependent)."""
    B = 200
    models = ["A", "B", "C"]
    rel = np.array([0.9, 0.1, 0.2])
    grp = np.array([0.5, 0.5, 0.1])
    V = {"relation": np.repeat(rel[:, None], B, axis=1), "group": np.repeat(grp[:, None], B, axis=1)}
    head = (rel + grp) / 2
    P = {"relation": list(rel), "group": list(grp)}
    headline_pairs = RA._pairwise(models, np.repeat(head[:, None], B, axis=1), list(head))
    headline_ranks = RA._ranks(models, np.repeat(head[:, None], B, axis=1), list(head))
    out = RA._weighting(models, V, P, headline_pairs, headline_ranks)
    assert [s["name"] for s in out["schemes"]] == SCHEMES
    assert out["robust_pairs"] == [{"a": "A", "b": "C", "higher": "A"}]
    weight_dependent = {(p["a"], p["b"]): p["higher_by_scheme"] for p in out["weight_dependent_pairs"]}
    assert set(weight_dependent) == {("A", "B"), ("B", "C")}
    assert weight_dependent[("A", "B")] == {"relation_only": "A", "r75": "A", "published": "A", "r25": "A", "group_only": None}
    assert weight_dependent[("B", "C")] == {"relation_only": "C", "r75": "B", "published": "B", "r25": "B", "group_only": "B"}
    assert [(r["a"], r["b"]) for r in out["resolved"]["group_only"]] == [("A", "C"), ("B", "C")]
    assert out["ranks"]["published"] == headline_ranks


def test_weighting_refuses_a_published_scheme_that_differs_from_the_headline():
    B = 200
    models = ["A", "B", "C"]
    V = {"relation": np.repeat(np.array([0.9, 0.1, 0.2])[:, None], B, axis=1),
         "group": np.repeat(np.array([0.5, 0.5, 0.1])[:, None], B, axis=1)}
    P = {"relation": [0.9, 0.1, 0.2], "group": [0.5, 0.5, 0.1]}
    wrong_pairs = RA._pairwise(models, np.repeat(np.array([0.1, 0.9, 0.2])[:, None], B, axis=1), [0.1, 0.9, 0.2])
    with pytest.raises(RA.CheckError):
        RA._weighting(models, V, P, wrong_pairs, RA._ranks(models, np.repeat(np.array([0.1, 0.9, 0.2])[:, None], B, axis=1),
                                                           [0.1, 0.9, 0.2]))


# ---------------------------------------------------------------- robustness and cost

def test_robustness_summary_covers_the_right_models():
    repeats = {
        "m00": {"shuffle": {"identical": .9, "delta_vs_published": -0.01, "flip_rate": .1},
                "r2": {"identical": 1.0, "delta_vs_published": 0.0, "flip_rate": 0.0},
                "r3": {"identical": .99, "delta_vs_published": 0.002, "flip_rate": .01}},
        "m01": {"shuffle": {"identical": .95, "delta_vs_published": 0.004, "flip_rate": .05}},
        "m02": {"shuffle": {"identical": .97, "delta_vs_published": 0.001, "flip_rate": .03},
                "vulkan": {"identical": .98, "delta_vs_published": 0.0005, "flip_rate": .02}},
        "note_protocol": "a note",
    }
    rob = RA._robustness(repeats, ["m00", "m01", "m02"])
    assert "note" not in rob
    assert rob["models"]["m00"]["run_to_run"] == pytest.approx({"max_abs_delta": 0.002, "max_flip_rate": 0.01})
    assert rob["models"]["m01"]["run_to_run"] is None and rob["models"]["m01"]["vulkan"] is None
    assert rob["models"]["m02"]["vulkan"] == pytest.approx({"delta": 0.0005, "flip_rate": 0.02})
    s = rob["summary"]
    assert s["presentation"] == pytest.approx({"n_models": 3, "median_abs_delta": 0.004, "max_abs_delta": 0.01})
    assert s["presentation_matched"] == pytest.approx({"n_models": 1, "max_abs_delta": 0.01})  # m00 only: run-to-run
    assert s["run_to_run"] == pytest.approx({"n_models": 1, "max_abs_delta": 0.002})
    assert s["backend"] == pytest.approx({"n_models": 1, "max_abs_delta": 0.0005})


def test_robustness_refuses_a_run_to_run_model_without_a_shuffle():
    repeats = {"m00": {"r2": {"identical": 1.0, "delta_vs_published": 0.0, "flip_rate": 0.0},
                       "r3": {"identical": .9, "delta_vs_published": 0.001, "flip_rate": .1}}}
    with pytest.raises(RA.CheckError):
        RA._robustness(repeats, ["m00"])


def test_robustness_without_repeats_is_empty_and_null():
    rob = RA._robustness({}, MODELS15)
    assert rob == {"models": {}, "summary": {"presentation": {"n_models": 0, "median_abs_delta": 0.0, "max_abs_delta": 0.0},
                                             "presentation_matched": {"n_models": 0, "max_abs_delta": 0.0},
                                             "run_to_run": {"n_models": 0, "max_abs_delta": 0.0}, "backend": None}}


def test_cost_is_decisions_over_build_seconds_and_refuses_zero_time():
    entries = {"m": {"runtime": {"build_wall_s": 200.0}, "counts": {"decisions": 10532}, "size_bytes": None, "params": "27B",
                     "quant": None}}
    run = RA._cost(["m"], entries)["runs"]["m"]
    assert run == {"build_wall_s": 200.0, "decisions": 10532, "decisions_per_s": pytest.approx(52.66), "size_bytes": None,
                   "params": "27B", "quant": None}
    entries["m"]["runtime"]["build_wall_s"] = 0
    with pytest.raises(RA.CheckError):
        RA._cost(["m"], entries)


def _write_repeat(tmp_path: Path, name: str, preds: list[str], qids: list[str]) -> tuple[Path, bytes]:
    blob = gzip.compress("\n".join(json.dumps({"qid": q, "op": "decide_relation", "reading": None, "pred": p})
                                   for q, p in zip(qids, preds)).encode("utf-8"))
    path = tmp_path / f"{name}.jsonl.gz"
    path.write_bytes(blob)
    return path, blob


class _Bundle:
    """Stands in for the v0.1.0 bundle's rescore: the headline it reports for each predictions blob is set by the test."""

    def __init__(self, by_blob: dict):
        self.by_blob = by_blob

    def rescore(self, blob: bytes, version: int) -> dict:
        return {"headline": {"relation_group_mcc": self.by_blob[blob]}}


def test_repeats_fold_the_two_vulkan_passes_into_one_entry(tmp_path):
    """Two Vulkan passes with the same decisions are one 'vulkan' entry; the shuffle and r2 keep their own tags. The
    predictions are fake (10,716 relation rows), which the module compares with the published rows."""
    qids = [f"q{i}" for i in range(10716)]
    published = ["A"] * len(qids)
    rows_of = {"m00": [{"qid": q, "op": "decide_relation", "reading": None, "pred": "A"} for q in qids]}
    flipped = ["B"] + ["A"] * (len(qids) - 1)
    r2, blob_r2 = _write_repeat(tmp_path, "r2", published, qids)
    v2, blob_v2 = _write_repeat(tmp_path, "v2", flipped, qids)
    v3, blob_v3 = _write_repeat(tmp_path, "v3", flipped, qids)
    _, blob_r1 = _write_repeat(tmp_path, "r1", published, qids)
    runs = [
        {"id": "m00", "tag": "r1", "predictions": str(tmp_path / "r1.jsonl.gz"), "status": "complete", "rc": 0,
         "identical_to_published_decisions": True, "headline_relation_group_mcc": 0.5},
        {"id": "m00", "tag": "r2", "predictions": str(r2), "status": "complete", "rc": 0,
         "identical_to_published_decisions": True, "headline_relation_group_mcc": 0.5},
        {"id": "m00", "tag": "vulkan_r2", "predictions": str(v2), "status": "complete", "rc": 0,
         "identical_to_published_decisions": False, "headline_relation_group_mcc": 0.48},
        {"id": "m00", "tag": "vulkan_r3", "predictions": str(v3), "status": "complete", "rc": 0,
         "identical_to_published_decisions": False, "headline_relation_group_mcc": 0.48},
    ]
    (tmp_path / "runs.json").write_text(json.dumps({"schema": RA.REPEAT_SCHEMA, "runs": runs}), encoding="utf-8")
    bundle = _Bundle({blob_r1: 0.5, blob_r2: 0.5, blob_v2: 0.48, blob_v3: 0.48})
    out = RA._repeats(tmp_path, rows_of, {"m00": 0.5}, bundle, 3)
    assert set(out["m00"]) == {"r2", "vulkan"}
    assert out["m00"]["r2"] == pytest.approx({"identical": 1.0, "delta_vs_published": 0.0, "flip_rate": 0.0})
    assert out["m00"]["vulkan"]["identical"] == pytest.approx(1 - 1 / 10716)
    assert out["m00"]["vulkan"]["flip_rate"] == pytest.approx(1 / 10716)
    assert out["m00"]["vulkan"]["delta_vs_published"] == pytest.approx(-0.02)
    assert set(out) == {"m00"} | set(RA.REPEAT_NOTES)
    # two Vulkan passes that disagree (two flipped rows against one) are an error, not a silent choice
    v3_other, blob_other = _write_repeat(tmp_path, "v3b", ["B", "B"] + ["A"] * (len(qids) - 2), qids)
    runs[3]["predictions"] = str(v3_other)
    (tmp_path / "runs.json").write_text(json.dumps({"schema": RA.REPEAT_SCHEMA, "runs": runs}), encoding="utf-8")
    with pytest.raises(RA.CheckError):
        RA._repeats(tmp_path, rows_of, {"m00": 0.5}, _Bundle({blob_r1: 0.5, blob_r2: 0.5, blob_v2: 0.48, blob_other: 0.48}), 3)


# ---------------------------------------------------------------- item audit

def _item(qid, correct, *, op="decide_relation", gold="EQUIV", wrong="IMPLIES", wrong_preds=None, stratum="real"):
    """One item of the 15 models: the models at the positions in `correct` answer the gold; the others answer `wrong`, or
    wrong_preds[i] when given."""
    preds = [gold if i in correct else (wrong_preds[i] if wrong_preds else wrong) for i in range(15)]
    depth = None if op == "decide_group" else 1
    return qid, op, gold, preds, depth, stratum


def _rows_of(items) -> dict:
    rows = {m: [] for m in MODELS15}
    for qid, op, gold, preds, depth, stratum in items:
        for m, pred in zip(MODELS15, preds):
            rows[m].append({"qid": qid, "op": op, "gold": gold, "pred": pred, "depth": depth, "stratum": stratum,
                            "held_out": False})
    return rows


def _audit_items():
    """Positions are 0-based rank order (position 7 is rank 8). Relations: an all-wrong item (its wrong answers are 7 'zeta',
    7 'alpha', 1 'beta': the modal is 'alpha', the tie goes to the smaller string); an all-right one; an inverted one (correct
    at positions 12-14); one correct at positions 5, 6 (strong), 7 (rank 8) and 13, 14 (weak): strong 2, weak 2, so not
    inverted, although counting rank 8 into the weak half would flip it; and one correct at 0-2 (strong only). A group item
    correct at positions 13-14 is inverted. Residuals are centred within (op, gold, stratum) cells, so g1 needs a cell peer: g2
    (correct at positions 0-1) makes g1 inverted and g2 not. A binding and a reading item, all wrong, are never counted."""
    zeta_alpha = ["zeta"] * 7 + ["alpha"] * 7 + ["beta"]
    return [
        _item("r1", set(), wrong_preds=zeta_alpha),
        _item("r2", set(range(15))),
        _item("r3", {12, 13, 14}),
        _item("r4", {5, 6, 7, 13, 14}),
        _item("r5", {0, 1, 2}),
        _item("g1", {13, 14}, op="decide_group", gold="exactly_one", wrong="none"),
        _item("g2", {0, 1}, op="decide_group", gold="exactly_one", wrong="none"),
        _item("b1", set(), op="decide_binding", gold="bound", wrong="unresolved"),
        _item("x1", set(), op="decide_reading", gold="yes", wrong="no"),
    ]


def _audit(rows, tmp_path):
    return RA._item_audit(MODELS15, rows, OP_POINTS, FAM_OF, tmp_path / "absent-verdicts.json")


def test_item_audit_flags_and_halves(tmp_path):
    block, data = _audit(_rows_of(_audit_items()), tmp_path)
    recs = {r["qid"]: r for r in map(json.loads, data.decode("utf-8").splitlines())}
    assert set(recs) == {"g1", "r1", "r3"}  # the all-wrong and the two inverted items; r4 and r5 are not flagged
    assert recs["r1"]["flags"] == ["all_wrong"] and recs["r1"]["n_correct"] == 0
    assert recs["r1"]["r_pb"] is not None  # residuals are centred within the cell, so they vary even when no model is right
    assert recs["r1"]["modal_pred"] == "alpha"
    assert recs["r3"]["flags"] == ["inverted"] and recs["r3"]["n_correct"] == 3
    assert recs["r3"]["strong_correct"] == 0 and recs["r3"]["weak_correct"] == 3
    assert recs["r3"]["r_pb"] < 0 and recs["r3"]["modal_pred"] == "IMPLIES"
    assert recs["g1"]["flags"] == ["inverted"] and recs["g1"]["depth"] is None
    assert block["strong_half"] == MODELS15[0:7] and block["rank_8"] == MODELS15[7]
    assert block["weak_half"] == MODELS15[8:15]


def test_item_audit_totals_and_sorting(tmp_path):
    block, data = _audit(_rows_of(_audit_items()), tmp_path)
    assert block["items"] == 7  # five relations and two groups: the binding and the reading are not items
    assert block["items_by_op"] == {"decide_group": 2, "decide_relation": 5}
    assert (block["flagged"], block["all_wrong"], block["inverted"]) == (3, 1, 2)
    assert block["by_op"]["decide_relation"] == {"items": 5, "flagged": 2, "all_wrong": 1, "inverted": 1, "inverted_not_all_wrong": 1}
    assert block["by_op"]["decide_group"] == {"items": 2, "flagged": 1, "all_wrong": 0, "inverted": 1, "inverted_not_all_wrong": 1}
    assert block["by_stratum"]["decide_relation"] == {"real": {"flagged": 2, "all_wrong": 1, "inverted": 1}}
    # sorted by n_correct, then qid: the all-wrong item (0), the group item (2), then the relation item (3)
    assert [r["qid"] for r in block["top_flagged"]] == ["r1", "g1", "r3"]
    assert block["judged"] is None
    qids = [json.loads(line)["qid"] for line in data.decode("utf-8").splitlines()]
    assert qids == sorted(qids)  # the flagged list is sorted by qid


def test_item_audit_flagged_list_is_canonical_json(tmp_path):
    _, data = _audit(_rows_of(_audit_items()), tmp_path)
    assert data.endswith(b"\n")
    for line in data.decode("utf-8").splitlines():
        rec = json.loads(line, parse_constant=_reject_constant)
        assert line == json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_item_audit_reads_the_verdicts_file_when_present(tmp_path):
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"counts": {"suspect_label": 2, "hard_valid": 1, "unclear": 0}, "n_judged": 3,
                                    "sampled": False, "sample_rule": "every flagged item"}), encoding="utf-8")
    block, _ = RA._item_audit(MODELS15, _rows_of(_audit_items()), OP_POINTS, FAM_OF, verdicts)
    assert block["judged"] == {"path": str(verdicts), "sha256": _sha(verdicts.read_bytes()),
                               "counts": {"hard_valid": 1, "suspect_label": 2, "unclear": 0}, "n_judged": 3, "n_flagged": 3,
                               "sampled": False, "sample_rule": "every flagged item"}


def test_judged_shape_and_refusals(tmp_path):
    verdicts = tmp_path / "verdicts.json"
    good = {"counts": {"suspect_label": 1, "hard_valid": 1, "unclear": 0}, "n_judged": 2, "sampled": True,
            "sample_rule": "a seeded sample of 2"}
    verdicts.write_text(json.dumps(good), encoding="utf-8")
    assert RA._judged(verdicts, 3)["n_judged"] == 2 and RA._judged(verdicts, 3)["sampled"] is True
    assert RA._judged(tmp_path / "absent.json", 3) is None
    bad = [
        dict(good, n_judged=4),                                   # more judged than flagged
        dict(good, sampled=False),                                # not a sample, yet fewer judged than flagged
        dict(good, counts={"suspect_label": 1, "hard_valid": 0, "unclear": 0}),  # counts do not sum to n_judged
        {k: v for k, v in good.items() if k != "sample_rule"},   # missing field
    ]
    for doc in bad:
        verdicts.write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(RA.CheckError):
            RA._judged(verdicts, 3)


@pytest.mark.parametrize("break_rows", ["gold", "missing", "duplicate"])
def test_item_audit_refuses_inconsistent_rows(tmp_path, break_rows):
    rows = _rows_of(_audit_items())
    if break_rows == "gold":
        rows[MODELS15[3]][0]["gold"] = "IMPLIES"  # one model's gold label differs from the others'
    elif break_rows == "missing":
        rows[MODELS15[3]] = [r for r in rows[MODELS15[3]] if r["qid"] != "r2"]  # one model lacks an item
    else:
        rows[MODELS15[3]].append(dict(rows[MODELS15[3]][0]))  # one model answers an item twice
    with pytest.raises(RA.CheckError):
        _audit(rows, tmp_path)


# ---------------------------------------------------------------- small helpers

def test_repeats_folder_is_empty_until_populated(tmp_path):
    assert RA._repeats(tmp_path / "absent", {}, {}, None, 0) == {}
    assert RA._repeats(tmp_path, {}, {}, None, 0) == {}  # an empty folder
    (tmp_path / "r2.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RA.CheckError):  # files without runs.json are not silently dropped
        RA._repeats(tmp_path, {}, {}, None, 0)


def test_splice_inserts_before_the_next_section_then_replaces_between_markers():
    readme = "# T\n\n## Which differences are resolved\n\ntext\n\n## Next\n\nlast\n"
    once = RA.splice(readme, "BLOCK")
    assert once == "# T\n\n## Which differences are resolved\n\ntext\n\nBLOCK\n\n## Next\n\nlast\n"
    marked = "A\n" + RA.START + "old" + RA.END + "\nB\n"
    assert RA.splice(marked, "BLOCK") == "A\nBLOCK\nB\n"
    with pytest.raises(RA.CheckError):
        RA.splice(RA.END + RA.START, "BLOCK")


def test_rounding_and_the_nan_refusal():
    assert RA._rounded({"a": 0.1234567, "b": [1.00000049, "x", None, 3]}) == {"a": 0.123457, "b": [1.0, "x", None, 3]}
    assert RA.dump({"x": 0.5}) == b'{\n "x": 0.5\n}\n'
    with pytest.raises(ValueError):
        RA.dump({"x": float("nan")})
    with pytest.raises(ValueError):
        RA.dump({"x": float("inf")})


# ---------------------------------------------------------------- committed analysis.json: contract

@pytest.fixture(scope="module")
def doc():
    return _committed()


@pytest.fixture(scope="module")
def lb():
    return _leaderboard()


@pytest.fixture(scope="module")
def flagged_records():
    return [json.loads(line) for line in RA.FLAGGED.read_text(encoding="utf-8").splitlines()]


def _floats(x):
    if isinstance(x, bool):
        return
    if isinstance(x, float):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _floats(v)
    elif isinstance(x, list):
        for v in x:
            yield from _floats(v)


SECTIONS = {"schema", "release", "based_on", "method", "models", "runs", "pairwise", "rank_intervals", "depth", "confusion",
            "calibration", "consistency", "item_audit", "repeats", "binding_detection", "weighting", "robustness", "cost"}


def test_contract_top_level_and_no_non_finite_numbers(doc):
    assert set(doc) == SECTIONS
    assert doc["schema"] == "mentat-bench-analysis/1" and doc["release"] == "v0.1.1"
    for x in _floats(doc):
        assert math.isfinite(x)
        assert round(x, 6) == x, f"{x} has more than 6 decimals"


def test_contract_based_on_hashes_match_the_committed_files(doc, lb):
    based = doc["based_on"]
    assert based["release"] == "v0.1.0"
    assert based["leaderboard"] == {"path": "results/v0.1.0/leaderboard.json",
                                    "sha256": _sha(RA.LEADERBOARD.read_bytes())}
    assert based["fixture"]["name"] == "mentat_hard_test" and based["fixture"]["revision"] == "3.1"
    assert based["fixture"]["content_hash"] == corrections.content_hash(corrections.content_hashes(RA.FIXTURE))
    runs_with_predictions = [e["predictions"] for e in lb["models"] + lb["baselines"]
                             if e["id"] in doc["runs"] and e["predictions"]]
    assert set(based["predictions"]) == set(runs_with_predictions)
    for path, digest in based["predictions"].items():
        assert (RA.ROOT / path).is_file(), path
        assert _sha((RA.ROOT / path).read_bytes()) == digest, path
    assert doc["method"]["flagged_list"] == {"path": "docs/audits/v0.1.1-item-audit/flagged.jsonl",
                                             "sha256": _sha(RA.FLAGGED.read_bytes())}


def test_contract_models_and_runs_come_from_the_leaderboard(doc, lb):
    system_one = [e["id"] for e in lb["models"] if e["kind"] == "system_one_model"]
    assert len(system_one) == 15
    assert doc["models"] == system_one == lb["ranking"]
    baselines = [e["id"] for e in lb["baselines"] if e["predictions"]]
    assert doc["runs"] == lb["ranking"] + baselines
    assert doc["method"]["bootstrap"] == {"unit": "league", "leagues": 18, "B": 20000, "seed": 0, "rng": "league_ci:0"}
    assert doc["method"]["family_size"] == 105 and doc["method"]["alpha"] == 0.05 and doc["method"]["correction"] == "holm"


def test_contract_method_pins(doc):
    m = doc["method"]
    assert m["bootstrap_draws"] == 20000 and m["published_draws"] == 2000 and m["leagues"] == 18 and m["seed"] == 0
    assert m["alpha"] == 0.05 and m["correction"] == "holm" and m["family_size"] == 105
    assert m["degenerate"] == {"min_n": 30, "min_valid_share": 0.99}
    assert m["halves"] == {"strong_ranks": [1, 7], "weak_ranks": [9, 15]}
    assert m["judges"] == {"model": "Haiku 5.5", "count": 2}
    assert m["first_2000_equal_published"] is True


def test_contract_pairwise_pairs_p_values_and_holm(doc, lb):
    models = doc["models"]
    entries = {e["id"]: e for e in lb["models"]}
    assert set(doc["pairwise"]) == set(FAMS)
    for fam in FAMS:
        pairs = doc["pairwise"][fam]
        assert len(pairs) == 105
        assert [(pr["a"], pr["b"]) for pr in pairs] == list(combinations(models, 2))  # each pair once, a ranked higher
        for pr in pairs:
            assert set(pr) == {"a", "b", "diff", "p", "p_holm", "resolved", "higher"}
            assert set(pr["diff"]) == {"point", "ci95"} and pr["diff"]["ci95"][0] <= pr["diff"]["ci95"][1]
            expected = entries[pr["a"]]["metrics"][fam]["point"] - entries[pr["b"]]["metrics"][fam]["point"]
            assert abs(pr["diff"]["point"] - expected) <= 2e-6  # the leaderboard's points
            assert 0.0001 <= pr["p"] <= 1 and 0 <= pr["p_holm"] <= 1  # 0.0001 = 2/(1+20000), the smallest attainable
            assert pr["p_holm"] >= pr["p"]
            assert pr["resolved"] == (pr["p_holm"] <= 0.05)
            if not pr["resolved"]:
                assert pr["higher"] is None
            elif pr["diff"]["point"] > 0:
                assert pr["higher"] == pr["a"]
            elif pr["diff"]["point"] < 0:
                assert pr["higher"] == pr["b"]
        ordered = sorted(pairs, key=lambda pr: pr["p"])
        assert all(x["p_holm"] <= y["p_holm"] for x, y in zip(ordered, ordered[1:]))  # Holm adjusted values never fall
        assert sum(pr["resolved"] for pr in pairs) == doc["method"]["resolved_pairs"][fam]


def test_contract_rank_intervals(doc, lb):
    entries = {e["id"]: e for e in lb["models"]}
    for fam in FAMS:
        block = doc["rank_intervals"][fam]
        assert set(block) == set(doc["models"])
        for m, r in block.items():
            assert set(r) == {"rank", "ci95", "median"}
            assert type(r["rank"]) is int and all(type(x) is int for x in r["ci95"]) and type(r["median"]) is int
            lo, hi = r["ci95"]
            assert 1 <= r["rank"] <= 15 and 1 <= lo <= hi <= 15
            assert lo <= r["median"] <= hi
            point = entries[m]["metrics"][fam]["point"]
            assert r["rank"] == 1 + sum(entries[o]["metrics"][fam]["point"] > point for o in doc["models"])
    v11 = json.loads((RA.ROOT / "results" / "v0.1.1" / "leaderboard.json").read_text(encoding="utf-8"))
    rows = {e["id"]: e for e in v11["models"]}
    for m in doc["models"]:  # the v0.1.1 leaderboard's rank interval is the headline one
        interval = doc["rank_intervals"]["headline"][m]
        assert rows[m]["rank_interval"] == {"lo": interval["ci95"][0], "hi": interval["ci95"][1], "median": interval["median"]}
        assert rows[m]["rank"] == interval["rank"]


def test_contract_depth_matches_the_leaderboard_and_counts(doc, lb):
    depth = doc["depth"]
    assert depth["buckets"] == ["0", "1", "2", "3", "4+", "NONE"]
    assert set(depth["runs"]) == set(doc["runs"])
    assert set(depth["leagues_by_bucket"]) == set(depth["buckets"])
    assert depth["leagues_by_bucket"]["4+"] == 1  # the six depth-4+ items are all in one league (test-L10)
    assert all(0 <= v <= 18 for v in depth["leagues_by_bucket"].values())
    entries = {e["id"]: e for e in lb["models"] + lb["baselines"]}
    for rid, buckets in depth["runs"].items():
        assert list(buckets) == depth["buckets"]
        total = sum(c["n"] for c in buckets.values())
        assert total == 6000  # every relation item, the NONE items included
        assert buckets["4+"]["n"] == 6  # as the spec says: the 4+ bucket has n = 6
        published = entries[rid].get("depth_accuracy")
        for bucket, c in buckets.items():
            assert set(c) == {"n", "correct", "point", "ci95", "valid_draws", "degenerate"}
            assert type(c["n"]) is int and 0 <= c["correct"] <= c["n"]
            assert c["point"] == (round(c["correct"] / c["n"], 6) if c["n"] else None)
            assert (c["ci95"] is None) == (c["valid_draws"] == 0)
            assert 0 <= c["valid_draws"] <= 20000
            if c["n"] < 30:
                assert c["degenerate"] is True
            if c["valid_draws"] == 20000 and c["n"] >= 30:
                assert c["degenerate"] is False
            if published is not None:
                assert (c["n"], c["correct"]) == (published[bucket]["n"], published[bucket]["correct"])


def test_contract_confusion_matches_the_depth_and_group_counts(doc):
    rel = doc["confusion"]["relation"]
    assert rel["classes"] == ["EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE"]
    assert set(rel["runs"]) == set(doc["runs"])
    for rid, c in rel["runs"].items():
        assert len(c["matrix"]) == 5 and all(len(row) == 5 for row in c["matrix"])
        assert all(type(x) is int and x >= 0 for row in c["matrix"] for x in row)
        assert type(c["missing"]) is int and type(c["abstained"]) is int and c["abstained"] >= 0
        assert sum(map(sum, c["matrix"])) + c["missing"] == 6000  # a missing answer is not in the matrix
        gold_none = sum(c["matrix"][4])
        assert gold_none <= doc["depth"]["runs"][rid]["NONE"]["n"]
    grp = doc["confusion"]["group"]
    assert grp["classes"] == sorted(grp["classes"])
    for rid, c in grp["runs"].items():
        k = len(grp["classes"])
        assert len(c["matrix"]) == k and all(len(row) == k for row in c["matrix"])
        assert sum(map(sum, c["matrix"])) == doc["item_audit"]["items_by_op"]["decide_group"] == 1000


def test_contract_calibration_bins_and_probability_flags(doc):
    assert set(doc["calibration"]) == set(doc["runs"])
    for rid, c in doc["calibration"].items():
        assert c["decide_group"] == {"probabilities": False} and c["decide_reading"] == {"probabilities": False}
        for op in ("decide_relation", "decide_binding"):
            block = c[op]
            assert set(block) == {"brier", "ece", "bins"}
            # the Brier is the cell's multiclass sum over the options (0 to 2 here); the ECE is a probability-scale gap
            assert block["brier"] is None or 0 <= block["brier"] <= 2
            assert block["ece"] is None or 0 <= block["ece"] <= 1
            bins = block["bins"]
            assert [b["lo"] for b in bins] == [round(i / 10, 1) for i in range(10)]
            for b in bins:
                assert set(b) == {"lo", "n", "confidence", "accuracy"} and type(b["n"]) is int and b["n"] >= 0
                for key in ("confidence", "accuracy"):
                    assert (b[key] is None) == (b["n"] == 0)
                    assert b[key] is None or 0 <= b[key] <= 1


def test_contract_consistency_shape(doc):
    cons = doc["consistency"]
    assert isinstance(cons["definition"], str) and cons["max_witness"] == 3
    assert set(cons["runs"]) == set(doc["runs"])
    for rid, c in cons["runs"].items():
        assert c["conflicting"] <= c["claim_bearing"] and c["conflicting_unsat"] <= c["conflicting"]
        assert sum(c["by_size"].values()) <= c["conflicting"]  # by_size counts the forced-outcome answers only
        assert set(c["by_op"]) == {"decide_relation", "decide_group"}
        assert sum(v["conflicting"] for v in c["by_op"].values()) == c["conflicting"]
        assert c["share"] is None or c["share"] == pytest.approx(c["conflicting"] / c["claim_bearing"], abs=1e-6)
        assert isinstance(c["contradiction_detected"], bool)
    gold_view = cons["runs"]["gold_view"]
    assert gold_view["conflicting"] == 0 and gold_view["conflicting_unsat"] == 0 and gold_view["contradiction_detected"] is False


def test_contract_consistency_unsat_within_conflicts_per_op(doc):
    """conflicting is the union of the two predicates' minimal conflicts, so it contains conflicting_unsat in total and per op
    (the site reader's rule, analysis-read.mjs consistencyOp)."""
    for rid, c in doc["consistency"]["runs"].items():
        assert c["conflicting_unsat"] <= c["conflicting"] <= c["claim_bearing"], rid
        for op, v in c["by_op"].items():
            assert v["conflicting_unsat"] <= v["conflicting"] <= v["claim_bearing"], (rid, op)


def test_contract_binding_detection_matches_the_leaderboard_and_the_predictions(doc, lb):
    bd = doc["binding_detection"]
    assert set(bd) == {"definition", "runs"} and set(bd["runs"]) == set(doc["runs"])
    entries = {e["id"]: e for e in lb["models"] + lb["baselines"]}
    for rid, b in bd["runs"].items():
        assert set(b) == {"n", "n_unresolved", "prevalence", "flagged", "mcc", "auroc", "average_precision"}
        assert b["n"] == 3348 and b["n_unresolved"] == 92  # the binding items of the fixture, and the unresolved ones
        assert b["prevalence"] == pytest.approx(92 / 3348, abs=1e-6) and 0 <= b["flagged"] <= b["n"]
        binding = entries[rid]["metrics"]["binding"]
        assert (b["mcc"] is None) == (binding is None)
        assert b["mcc"] is None or abs(b["mcc"] - binding["point"]) <= 5e-7
        for key in ("auroc", "average_precision"):
            assert b[key] is None or 0 <= b[key]["point"] <= 1
    assert {rid for rid, b in bd["runs"].items() if b["auroc"] is None} == BINDING_RUNS_WITHOUT_PROBABILITIES
    for m in doc["models"]:
        assert bd["runs"][m]["auroc"] is not None and bd["runs"][m]["average_precision"] is not None


def test_contract_binding_auroc_equals_sklearn_on_the_committed_predictions(doc, lb):
    """The point AUROC and the flagged count of the reference model, from its committed binding rows."""
    metrics = pytest.importorskip("sklearn.metrics")
    entry = next(e for e in lb["models"] if e["id"] == lb["benchmark"]["reference"])
    rows = [r for r in _predictions(entry["predictions"]) if r["op"] == "decide_binding"]
    y = [r["gold"] == "unresolved" for r in rows]
    s = [r["probs"]["unresolved"] for r in rows]
    block = doc["binding_detection"]["runs"][entry["id"]]
    assert block["auroc"]["point"] == pytest.approx(metrics.roc_auc_score(y, s), abs=1e-6)
    assert block["average_precision"]["point"] == pytest.approx(metrics.average_precision_score(y, s), abs=1e-6)
    assert block["flagged"] == sum(r["probs"]["unresolved"] > r["probs"]["bound"] for r in rows)


def test_contract_weighting(doc):
    w = doc["weighting"]
    assert [s["name"] for s in w["schemes"]] == SCHEMES
    assert [(s["relation"], s["group"]) for s in w["schemes"]] == pytest.approx([(1, 0), (.75, .25), (.5, .5), (.25, .75), (0, 1)])
    for name in SCHEMES:
        assert set(w["ranks"][name]) == set(doc["models"])
        for m, r in w["ranks"][name].items():
            lo, hi = r["ci95"]
            assert all(type(x) is int for x in (r["rank"], r["median"], lo, hi)) and 1 <= lo <= r["median"] <= hi <= 15
        for pr in w["resolved"][name]:
            assert set(pr) == {"a", "b", "higher", "p_holm"} and pr["higher"] in (pr["a"], pr["b"]) and pr["p_holm"] <= 0.05
    # the published scheme is the headline: its ranks and its resolved pairs
    assert w["ranks"]["published"] == doc["rank_intervals"]["headline"]
    assert w["resolved"]["published"] == [
        {"a": p["a"], "b": p["b"], "higher": p["higher"], "p_holm": p["p_holm"]} for p in doc["pairwise"]["headline"] if p["resolved"]]
    assert w["resolved"]["relation_only"] == [
        {"a": p["a"], "b": p["b"], "higher": p["higher"], "p_holm": p["p_holm"]} for p in doc["pairwise"]["relation"] if p["resolved"]]
    assert w["resolved"]["group_only"] == [
        {"a": p["a"], "b": p["b"], "higher": p["higher"], "p_holm": p["p_holm"]} for p in doc["pairwise"]["group"] if p["resolved"]]
    # robust pairs are resolved under every scheme with the winner they share; weight-dependent pairs are the rest that resolve
    resolved_by = {name: {(r["a"], r["b"]): r["higher"] for r in w["resolved"][name]} for name in SCHEMES}
    robust = {(r["a"], r["b"]): r["higher"] for r in w["robust_pairs"]}
    for key, winner in robust.items():
        assert all(resolved_by[name].get(key) == winner for name in SCHEMES)
    dependent = {(r["a"], r["b"]): r["higher_by_scheme"] for r in w["weight_dependent_pairs"]}
    assert not set(robust) & set(dependent)
    for key, by_scheme in dependent.items():
        assert set(by_scheme) == set(SCHEMES)
        assert any(by_scheme[name] is not None for name in SCHEMES)
        assert not (all(by_scheme[name] is not None for name in SCHEMES) and len({by_scheme[name] for name in SCHEMES}) == 1)
        assert all(by_scheme[name] == resolved_by[name].get(key) for name in SCHEMES)
    resolved_somewhere = set().union(*(set(resolved_by[name]) for name in SCHEMES))
    assert resolved_somewhere == set(robust) | set(dependent)


def test_contract_robustness_and_cost(doc, lb):
    rb = doc["robustness"]
    assert "note" not in rb
    models = rb["models"]
    assert set(models) <= set(doc["models"])
    for m, e in models.items():
        assert set(e) == {"shuffle", "vulkan", "run_to_run"}
        for key in ("shuffle", "vulkan"):
            if e[key] is not None:
                assert 0 <= e[key]["flip_rate"] <= 1 and isinstance(e[key]["delta"], float)
        if e["run_to_run"] is not None:
            assert e["run_to_run"]["max_abs_delta"] >= 0 and 0 <= e["run_to_run"]["max_flip_rate"] <= 1
    rep = doc["repeats"]
    for m, e in models.items():  # the robustness block is the repeats block, per model
        if e["shuffle"] is not None:
            assert e["shuffle"]["delta"] == rep[m]["shuffle"]["delta_vs_published"]
    shuffled = {m: e["shuffle"]["delta"] for m, e in models.items() if e["shuffle"] is not None}
    matched = [m for m, e in models.items() if e["run_to_run"] is not None]
    s = rb["summary"]
    assert s["presentation"]["n_models"] == len(shuffled)
    assert s["presentation"]["max_abs_delta"] == pytest.approx(max(abs(d) for d in shuffled.values()), abs=5e-7)
    assert s["presentation"]["median_abs_delta"] == pytest.approx(float(np.median([abs(d) for d in shuffled.values()])), abs=5e-7)
    assert s["presentation_matched"]["n_models"] == len(matched) == s["run_to_run"]["n_models"]
    assert s["presentation_matched"]["max_abs_delta"] == pytest.approx(max(abs(shuffled[m]) for m in matched), abs=5e-7)
    assert s["run_to_run"]["max_abs_delta"] == pytest.approx(max(models[m]["run_to_run"]["max_abs_delta"] for m in matched), abs=5e-7)
    vulkan = [m for m, e in models.items() if e["vulkan"] is not None]
    assert vulkan == ["d3_9b-q8_0"]  # the Vulkan pass is the d3:9b one, the only model run on that backend
    assert s["backend"]["n_models"] == 1 and s["backend"]["max_abs_delta"] == pytest.approx(abs(models[vulkan[0]]["vulkan"]["delta"]), abs=5e-7)
    cost = doc["cost"]
    assert cost["hardware"] == RA.HARDWARE and cost["note"] == RA.COST_NOTE
    entries = {e["id"]: e for e in lb["models"]}
    assert set(cost["runs"]) == set(doc["models"])
    for m, r in cost["runs"].items():
        e = entries[m]
        assert r["build_wall_s"] == e["runtime"]["build_wall_s"]
        assert r["decisions"] == e["counts"]["decisions"] == 10532
        assert r["decisions_per_s"] == pytest.approx(r["decisions"] / r["build_wall_s"], rel=1e-6)
        assert r["size_bytes"] == e["size_bytes"] and r["params"] == e["params"] and r["quant"] == e["quant"]


def test_contract_repeats_are_shares_and_only_the_named_tags(doc):
    rep = doc["repeats"]
    for key, value in rep.items():
        if key.startswith("note_"):
            assert isinstance(value, str)
            continue
        assert set(value) <= {"r2", "r3", "shuffle", "vulkan"}
        for tag, c in value.items():
            assert 0 <= c["identical"] <= 1 and 0 <= c["flip_rate"] <= 1
            assert c["flip_rate"] == pytest.approx(1 - c["identical"], abs=1e-6)


def test_contract_item_audit_counts_and_top_list(doc, lb, flagged_records):
    audit = doc["item_audit"]
    assert audit["items"] == 7000 and audit["items_by_op"] == {"decide_group": 1000, "decide_relation": 6000}
    entries = {e["id"]: e for e in lb["models"]}
    for op, fam in (("decide_relation", "relation"), ("decide_group", "group")):  # halves rank by the op's own MCC
        ranked = sorted(doc["models"], key=lambda m: -entries[m]["metrics"][fam]["point"])  # ties keep the headline order
        assert audit["halves"][op] == {"strong": ranked[0:7], "rank_8": ranked[7], "weak": ranked[8:15]}
    assert audit["strong_half"] == audit["halves"]["decide_relation"]["strong"]
    assert audit["rank_8"] == audit["halves"]["decide_relation"]["rank_8"]
    assert audit["weak_half"] == audit["halves"]["decide_relation"]["weak"]
    # the totals are the flagged list's, and the per-op and per-stratum blocks add up to them
    assert audit["flagged"] == len(flagged_records)
    assert audit["all_wrong"] == sum("all_wrong" in r["flags"] for r in flagged_records)
    assert audit["inverted"] == sum("inverted" in r["flags"] for r in flagged_records)
    assert max(audit["all_wrong"], audit["inverted"]) <= audit["flagged"] <= audit["all_wrong"] + audit["inverted"]
    for op, c in audit["by_op"].items():
        recs = [r for r in flagged_records if r["op"] == op]
        assert (c["flagged"], c["all_wrong"], c["inverted"]) == (
            len(recs), sum("all_wrong" in r["flags"] for r in recs), sum("inverted" in r["flags"] for r in recs))
        assert sum(s["flagged"] for s in audit["by_stratum"][op].values()) == c["flagged"]
    # each flagged record obeys the rule: all_wrong is exactly 0 correct; inverted has the weak half's residual mean above the
    # strong half's (residuals are correctness minus the cell mean, so raw counts need not order the same way), and r_pb < 0
    for r in flagged_records:
        assert ("all_wrong" in r["flags"]) == (r["n_correct"] == 0)
        if "inverted" in r["flags"]:
            assert r["residual_weak_minus_strong"] > 0 and r["r_pb"] is not None and r["r_pb"] < 0
        assert r["r_pb"] is None or -1 <= r["r_pb"] <= 1  # undefined only when the residuals are constant
        assert r["flags"] and set(r["flags"]) <= {"all_wrong", "inverted"}
        assert (r["depth"] is None) == (r["op"] == "decide_group" or r["gold"] == "NONE")  # a NONE gold has no depth
    qids = [r["qid"] for r in flagged_records]
    assert qids == sorted(qids) and len(set(qids)) == len(qids)
    expected_top = sorted(flagged_records, key=lambda r: (r["n_correct"], r["qid"]))[:100]
    keys = ("qid", "op", "gold", "flags", "n_correct", "modal_pred", "depth", "stratum")
    assert audit["top_flagged"] == [{k: r[k] for k in keys} for r in expected_top]
    if not RA.VERDICTS.is_file():  # the judged block appears with the verdicts file
        assert audit["judged"] is None
    else:
        assert audit["judged"]["path"] == "docs/audits/v0.1.1-item-audit/verdicts.json"
        assert audit["judged"]["n_flagged"] == audit["flagged"]


# ---------------------------------------------------------------- binding detection, by hand (no sklearn needed)

def test_binding_auroc_and_ap_by_hand():
    """Scores .1 (negative), .2 (positive), .3 (negative), .4 (positive). The positive-negative pairs: .2 > .1, .4 > .1, .4 > .3 and
    .2 < .3, so AUROC is 3/4. Sorted from the top: .4 is positive at precision 1 (recall 1/2), .3 is negative, .2 is positive at
    precision 2/3 (recall 1), so AP is 1/2 + (1/2)(2/3) = 5/6."""
    scores = np.array([0.1, 0.2, 0.3, 0.4])
    positive = np.array([False, True, False, True])
    auc, ap = RA._binding_draws(scores, positive, np.zeros(4, dtype=int), np.ones((1, 1), dtype=np.int64))
    assert auc[0] == pytest.approx(3 / 4)
    assert ap[0] == pytest.approx(5 / 6)


def test_binding_weighted_by_hand_equals_the_repeated_items():
    """League 0 has weight 2 and league 1 weight 1. Repeating the items gives negatives .1 x2 and .3 x2, positives .2 x1 and .4 x2.
    Positive-negative pairs: 12, of which 10 are won (the .2 beats .1 twice, the two .4s beat both negatives), so AUROC is 5/6.
    Sorted from the top: .4 x2 at precision 1 (recall 2/3), then .2 x1 at precision 3/5 (recall 1), so AP is 2/3 + (1/3)(3/5) = 13/15."""
    scores = np.array([0.1, 0.2, 0.3, 0.4])
    positive = np.array([False, True, False, True])
    league = np.array([0, 1, 0, 0])
    auc, ap = RA._binding_draws(scores, positive, league, np.array([[2, 1]], dtype=np.int64))
    assert auc[0] == pytest.approx(10 / 12)
    assert ap[0] == pytest.approx(13 / 15)


def test_higher_on_a_zero_point_difference_is_the_second_model():
    """A resolved pair whose point difference is 0 (every draw favours a): the contract's rule is higher = diff > 0 ? a : b, so b."""
    V = np.vstack([np.ones(200), np.zeros(200)])
    pr = RA._pairwise(["a", "b"], V, [0.0, 0.0])[0]
    assert pr["resolved"] is True and pr["diff"]["point"] == 0.0
    assert pr["higher"] == "b"


# ---------------------------------------------------------------- consistency: union, conflicting_main and by_size, with a stub

def _stub_consistency(monkeypatch, answers, res_extra=None, rid_rows=None):
    """Installs a score_run stub for release_analysis._consistency. answers: qid -> (main, unsat, size)."""
    import types
    stub = types.ModuleType("consistency")

    def score_run(rows, fixture_dir):
        claim = len(answers)
        main = sum(1 for m, _, _ in answers.values() if m)
        unsat = sum(1 for _, u, _ in answers.values() if u)
        by_size = {str(s): sum(1 for m, _, z in answers.values() if m and z == s) for s in (1, 2, 3)}
        return {"answers": {q: {"main": m, "unsat": u, "size": z} for q, (m, u, z) in answers.items()},
                "claim_bearing": claim, "conflicting": main, "conflicting_unsat": unsat,
                "contradiction_detected": bool(main), "by_size": by_size,
                "by_op": {"decide_relation": {"claim_bearing": claim, "conflicting": main, "conflicting_unsat": unsat},
                          "decide_group": {"claim_bearing": 0, "conflicting": 0, "conflicting_unsat": 0}},
                "definition": "stub definition", "max_witness": 3}

    stub.score_run = score_run
    monkeypatch.setitem(sys.modules, "consistency", stub)


def _consistency_fixture(qids):
    fx = {"leagues": ["L1", "L2"], "item_league": {q: i % 2 for i, q in enumerate(qids)}}
    rows = [{"qid": q, "op": "decide_relation", "reading": None} for q in qids]
    return fx, rows


def test_consistency_union_conflicting_main_and_the_by_size_identity(monkeypatch):
    """q1: main, size 1. q2: UNSAT only (no main witness). q3: main (size 2) and UNSAT. q4: neither. So the union is 3, the main
    count 2, the UNSAT count 2; by_size counts the main answers (one of size 1, one of size 2) and sums to the main count."""
    qids = ["q1", "q2", "q3", "q4"]
    answers = {"q1": (True, False, 1), "q2": (False, True, None), "q3": (True, True, 2), "q4": (False, False, None)}
    _stub_consistency(monkeypatch, answers)
    fx, rows = _consistency_fixture(qids)
    res = RA._consistency({"run": rows}, fx, RA.LC.draws(20, 0, 2))
    run = res["runs"]["run"]
    assert run["conflicting"] == 3 and run["conflicting_main"] == 2 and run["conflicting_unsat"] == 2
    assert run["by_size"] == {"1": 1, "2": 1, "3": 0} and sum(run["by_size"].values()) == run["conflicting_main"]
    assert run["share"] == pytest.approx(3 / 4)
    assert run["by_op"]["decide_relation"]["conflicting"] == 3 and run["by_op"]["decide_relation"]["conflicting_main"] == 2
    assert run["by_op"]["decide_relation"]["conflicting_unsat"] <= run["by_op"]["decide_relation"]["conflicting"]
    assert "either predicate" in res["definition"] and "conflicting_main" in res["definition"]


def test_consistency_refuses_a_main_count_that_differs_from_score_run(monkeypatch):
    """score_run reports a main count that its own answers do not give: the module refuses rather than writes."""
    qids = ["q1", "q2"]
    _stub_consistency(monkeypatch, {"q1": (True, False, 1), "q2": (False, False, None)})
    import types
    base = sys.modules["consistency"].score_run

    def wrong(rows, fixture_dir):
        res = base(rows, fixture_dir)
        res["conflicting"] = 2  # its answers give 1
        return res
    sys.modules["consistency"].score_run = wrong
    fx, rows = _consistency_fixture(qids)
    with pytest.raises(RA.CheckError):
        RA._consistency({"run": rows}, fx, RA.LC.draws(5, 0, 2))


def test_consistency_stop_rule_on_gold_view(monkeypatch):
    qids = ["q1"]
    fx, rows = _consistency_fixture(qids)
    _stub_consistency(monkeypatch, {"q1": (True, False, 1)})
    with pytest.raises(RA.CheckError):
        RA._consistency({"gold_view": rows}, fx, RA.LC.draws(5, 0, 2))
    _stub_consistency(monkeypatch, {"q1": (False, False, None)})
    res = RA._consistency({"gold_view": rows}, fx, RA.LC.draws(5, 0, 2))
    assert res["runs"]["gold_view"]["conflicting"] == 0


def test_verdict_facts_count_the_prose_numbers():
    """The item-audit prose quotes these counts; the helper derives them from verdicts.json items (controls apart)."""
    items = [
        {"group": "all_wrong", "op": "decide_group", "gold": "at_most_k:2", "judge1": "none", "judge2": "none", "verdict": "suspect_label"},
        {"group": "inverted", "op": "decide_relation", "gold": "EQUIV", "judge1": "IMPLIES", "judge2": "IMPLIES", "verdict": "suspect_label"},
        {"group": "inverted", "op": "decide_relation", "gold": "EQUIV", "judge1": "EQUIV", "judge2": "EQUIV", "verdict": "hard_valid"},
        {"group": "control", "op": "decide_relation", "gold": "EQUIV", "judge1": "EQUIV", "judge2": "EQUIV", "verdict": "hard_valid"},
    ]
    facts = RA.verdict_facts({"items": items})
    assert facts == {"n_flagged_judged": 3, "n_suspect": 2, "n_atmost_none": 1, "n_controls": 1, "controls_suspect": 0,
                     "controls_groups": 0, "both_agree_nongold": 2}
    assert RA.upper_bound_zero_of(50) == pytest.approx(0.0711, abs=1e-4)  # the exact two-sided 95% upper limit: 7.1%
    assert RA.upper_bound_zero_one_sided_of(50) == pytest.approx(0.0582, abs=1e-4)  # the one-sided 95% upper bound: 5.8%


def test_contract_consistency_main_union_and_by_size_identity(doc):
    """conflicting is the union of the two predicates; conflicting_main is the main-predicate count; by_size sums to it exactly."""
    for rid, c in doc["consistency"]["runs"].items():
        assert c["conflicting_unsat"] <= c["conflicting"] <= c["claim_bearing"], rid
        assert c["conflicting_main"] <= c["conflicting"], rid
        assert sum(c["by_size"].values()) == c["conflicting_main"], rid
        assert sum(v["conflicting_main"] for v in c["by_op"].values()) == c["conflicting_main"], rid
        for op, v in c["by_op"].items():
            assert v["conflicting_unsat"] <= v["conflicting"] <= v["claim_bearing"], (rid, op)


def test_contract_inverted_split_and_the_all_wrong_overlap(doc, flagged_records):
    """The inverted flag is pre-registered and unchanged; the report adds the counts without the all-wrong items."""
    audit = doc["item_audit"]
    inverted = [r for r in flagged_records if "inverted" in r["flags"]]
    both = [r for r in flagged_records if "inverted" in r["flags"] and "all_wrong" in r["flags"]]
    assert audit["inverted"] == len(inverted) and audit["all_wrong_and_inverted"] == len(both)
    assert audit["inverted_not_all_wrong"] == len(inverted) - len(both)
    for op, c in audit["by_op"].items():
        assert c["inverted_not_all_wrong"] == sum(1 for r in inverted if r["op"] == op and "all_wrong" not in r["flags"])


def test_contract_judged_matches_the_verdicts_file_and_the_index(doc):
    judged = doc["item_audit"]["judged"]
    assert judged is not None and judged["path"] == "docs/audits/v0.1.1-item-audit/verdicts.json"
    assert judged["sha256"] == _sha(RA.VERDICTS.read_bytes())
    index = json.loads((RA.ROOT / "results" / "index.json").read_text(encoding="utf-8"))
    entry = next(v for v in index["versions"] if v["version"] == "v0.1.1")
    assert entry["item_audit_verdicts"] == judged["path"]
    assert judged["n_judged"] == sum(judged["counts"].values()) and judged["n_judged"] <= judged["n_flagged"]
    assert judged["sampled"] is True and judged["n_flagged"] == doc["item_audit"]["flagged"]


def test_contract_repeats_point_at_the_published_run_and_keep_no_copies(doc):
    """The published run is referenced in results/v0.1.0 (no copy under repeats/); runs.json records any left-out run."""
    runs = json.loads((RA.REPEATS / "runs.json").read_text(encoding="utf-8"))
    assert isinstance(runs["left_out"], list)
    for rec in runs["runs"]:
        if rec["tag"] == "r1":
            assert rec["predictions"] == f"results/v0.1.0/predictions/{rec['id']}.jsonl.gz"
            assert rec["cell"] == f"results/v0.1.0/cells/{rec['id']}.json"
            assert (RA.ROOT / rec["predictions"]).is_file() and (RA.ROOT / rec["cell"]).is_file()
    assert not list(RA.REPEATS.glob("predictions/*.r1.*")) and not list(RA.REPEATS.glob("cells/*.r1.*"))


# ---------------------------------------------------------------- slow: rebuild from the committed inputs

@pytest.fixture(scope="module")
def rebuilt():
    """The rebuild as the module makes it: no import of the consistency module is blocked, so a null or failing consistency
    block shows up as a difference, not as a masked one."""
    return RA.build(RA.REPEATS)


@pytest.mark.slow
def test_build_reproduces_the_committed_bytes(rebuilt):
    doc, flagged = rebuilt
    assert RA.dump(doc) == RA.ANALYSIS.read_bytes()
    assert flagged == RA.FLAGGED.read_bytes()


@pytest.mark.slow
def test_two_builds_are_byte_identical(rebuilt):
    second_doc, second_flagged = RA.build(RA.REPEATS)
    assert RA.dump(second_doc) == RA.dump(rebuilt[0])
    assert second_flagged == rebuilt[1]


@pytest.mark.slow
def test_readme_section_matches_markdown(rebuilt):
    doc, _ = rebuilt
    text = (RA.ROOT / "results" / "README.md").read_text(encoding="utf-8")
    start = text.index(RA.START)
    section = text[start:text.index(RA.END) + len(RA.END)]
    assert section == RA.markdown(doc)
    assert RA.splice(text, RA.markdown(doc)) == text  # splicing the section into the committed README changes nothing

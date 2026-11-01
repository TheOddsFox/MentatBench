"""tools/eval/consistency.py (MEASUREMENT ONLY): the local-conflict score of one run's raw answers.

Hand cases on score_run, with tiny prediction rows over a synthetic fixture of five markets (outcomes mh:<market>:1 and
mh:<market>:2): a pair that conflicts under MAIN only (a forced outcome), a single forced answer, a group that forces its
outcomes, a group contradicted by a market's complement facts (UNSAT), a 3-answer cycle that is UNSAT-minimal with no UNSAT
pair, an ordered chain with exactly_one, an invalid group, a 4-answer conflict (not counted), the non-claims (NONE, none,
MISSING, None, abstained, bindings, readings), a consistent set, and order independence.

The oracles: for relation rows, a truth table over the outcome variables gives the predicates (MAIN and UNSAT-only), and
every subset of at most three answers is enumerated for the minimal sets. For mixed rows, the module's own predicate
(_Theory.flags) is enumerated the same way. Both are compared with score_run's per-answer main, unsat and size.

Slow: the gold_view stop rule on the committed predictions (MB_RUN_SLOW=1). The full 20-run analysis takes about 11 minutes,
so it runs only with MB_RUN_ANALYSIS=1.
"""

import gzip
import itertools
import json
import os
import random
import sys
from functools import lru_cache
from itertools import combinations
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "eval"))
import consistency as C  # noqa: E402

FIXTURE = ROOT / "fixtures" / "mentat_hard_test"
MARKETS = ("A", "B", "C", "D", "E")
RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES")
RUN_ANALYSIS = os.environ.get("MB_RUN_ANALYSIS") == "1"
full_analysis = pytest.mark.skipif(not RUN_ANALYSIS,
                                   reason="the full 20-run analysis takes about 11 minutes: set MB_RUN_ANALYSIS=1")


def o(market: str, side: int | str) -> str:
    """The outcome id of a market: side 1 or 2 (the two complementary outcomes)."""
    return f"mh:{market}:{side}"


A1, A2, B1, B2, C1, C2, D1, D2, E1, E2 = (o(m, s) for m in MARKETS for s in (1, 2))


def rel(qid, a, b, pred, **extra):
    return {"op": "decide_relation", "qid": qid, "a": a, "b": b, "pred": pred, **extra}


def grp(qid, members, pred, chain=None):
    row = {"op": "decide_group", "qid": qid, "members": list(members), "pred": pred}
    if chain is not None:
        row["chain"] = list(chain)
    return row


def _jsonl(path: Path, rows) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture(scope="module")
def fx(tmp_path_factory) -> Path:
    """A synthetic fixture: the five markets, each with its two outcomes, as gold/meta.jsonl and gold/bindings.jsonl."""
    root = tmp_path_factory.mktemp("fixture")
    (root / "gold").mkdir()
    meta = [{"id": o(m, s), "market": m} for m in MARKETS for s in (1, 2)]
    _jsonl(root / "gold" / "meta.jsonl", meta)
    _jsonl(root / "gold" / "bindings.jsonl", [{"id": row["id"]} for row in meta])
    return root


# ---------------------------------------------------------------- oracles

def _market(outcome: str) -> str:
    return outcome.rsplit(":", 1)[0].split(":", 1)[1]


@lru_cache(maxsize=None)
def _truth(relations: tuple) -> tuple[bool, bool]:
    """(MAIN, UNSAT-only) of relation answers (a, b, pred) by a truth table over the outcome variables of their markets,
    written from the semantics of the predicates (not from the module): IMPLIES(a, b) is a -> b, IMPLIED_BY(a, b) is b -> a,
    EXCLUDES is not both, EQUIV is both implications. Each market has exactly one of its two outcomes. UNSAT: no assignment
    satisfies it. MAIN: UNSAT, or some outcome has the same value in every satisfying assignment (a forced constant)."""
    markets = sorted({_market(x) for a, b, _ in relations for x in (a, b)})
    if not markets:
        return False, False
    names = [o(m, s) for m in markets for s in (1, 2)]
    index = {name: i for i, name in enumerate(names)}

    def lit(name: str, value: bool) -> tuple[int, bool]:
        return index[name], value

    clauses = []
    for m in markets:
        clauses.append([lit(o(m, 1), True), lit(o(m, 2), True)])  # one of the two is true
        clauses.append([lit(o(m, 1), False), lit(o(m, 2), False)])  # not both
    for a, b, pred in relations:
        if pred == "IMPLIES":
            clauses.append([lit(a, False), lit(b, True)])
        elif pred == "IMPLIED_BY":
            clauses.append([lit(b, False), lit(a, True)])
        elif pred == "EXCLUDES":
            clauses.append([lit(a, False), lit(b, False)])
        elif pred == "EQUIV":
            clauses += [[lit(a, False), lit(b, True)], [lit(a, True), lit(b, False)]]
        else:
            raise ValueError(pred)
    models = [bits for bits in itertools.product((False, True), repeat=len(names))
              if all(any(bits[i] == v for i, v in clause) for clause in clauses)]
    if not models:
        return True, True
    forced = any(len({bits[i] for bits in models}) == 1 for i in range(len(names)))
    return forced, False


def _relations(rows) -> tuple:
    return tuple((r["a"], r["b"], r["pred"]) for r in rows)


def _minimal_sets(n: int, flag) -> tuple[list, list]:
    """(MAIN, UNSAT) minimal sets among the subsets of at most three of range(n): flagged, and no proper subset flagged."""
    flagged = {S: flag(S) for k in (1, 2, 3) for S in combinations(range(n), k)}
    mains, unsats = [], []
    for S, (main, unsat) in flagged.items():
        proper = [T for k in range(1, len(S)) for T in combinations(S, k)]
        if main and not any(flagged[T][0] for T in proper):
            mains.append(S)
        if unsat and not any(flagged[T][1] for T in proper):
            unsats.append(S)
    return mains, unsats


def _check_against_oracle(res: dict, qids: list[str], flag) -> None:
    """score_run's answers against the oracle: each answer's main, unsat and smallest-witness size, the totals, and the
    contradiction of the whole set."""
    mains, unsats = _minimal_sets(len(qids), flag)
    size: dict[int, int] = {}
    for S in mains:
        for i in S:
            size[i] = min(size.get(i, 4), len(S))
    unsat_members = {i for S in unsats for i in S}
    for i, qid in enumerate(qids):
        assert res["answers"][qid] == {"main": i in size, "unsat": i in unsat_members, "size": size.get(i)}, qid
    assert res["conflicting"] == len(size)
    assert res["conflicting_unsat"] == len(unsat_members)
    assert res["by_size"] == {str(s): sum(1 for v in size.values() if v == s) for s in (1, 2, 3)}
    assert res["contradiction_detected"] == flag(tuple(range(len(qids))))[0]


def _module_flag(rows, fx: Path):
    """The module's own predicate over the claims of rows (claim i is row i here), built as score_run builds it."""
    ids, pairs = C._outcomes(fx)
    market_of = [0] * len(ids)
    for m, (a, b) in enumerate(pairs):
        market_of[a] = market_of[b] = m
    var = {oid: v for v, oid in enumerate(ids)}
    return C._Theory(pairs, market_of, C._claims(rows, var, ids)).flags


def _truth_flag(rows):
    return lambda S: _truth(tuple(_relations([rows[i] for i in S])))


# ---------------------------------------------------------------- hand cases: relations

def test_pair_conflicts_only_through_the_forced_outcome(fx):
    rows = [rel("q1", A1, B1, "IMPLIES"), rel("q2", A1, B1, "EXCLUDES")]
    assert _truth(_relations(rows)) == (True, False)  # A:1 must be false, and nothing else is forced
    res = C.score_run(rows, fx)
    for qid in ("q1", "q2"):
        assert res["answers"][qid] == {"main": True, "unsat": False, "size": 2}
    assert res["conflicting"] == 2 and res["conflicting_unsat"] == 0
    assert res["by_size"] == {"1": 0, "2": 2, "3": 0}  # each answer once, at its smallest witness
    assert res["contradiction_detected"] is True
    assert res["by_op"]["decide_relation"] == {"claim_bearing": 2, "conflicting": 2, "conflicting_unsat": 0}


def test_single_implies_between_the_two_outcomes_of_one_market(fx):
    rows = [rel("q1", A1, A2, "IMPLIES")]  # A:1 implies A:2, which is the negation of A:1: A:1 is forced false
    assert _truth(_relations(rows)) == (True, False)
    res = C.score_run(rows, fx)
    assert res["answers"]["q1"] == {"main": True, "unsat": False, "size": 1}
    assert res["conflicting"] == 1 and res["conflicting_unsat"] == 0
    assert res["by_size"] == {"1": 1, "2": 0, "3": 0}
    assert res["contradiction_detected"] is True


def test_three_answer_cycle_is_unsat_minimal_with_no_unsat_pair(fx):
    """C:1 implies A:1; C:2 excludes A:1; A:2 is equivalent to C:1. The triple is contradictory only as a whole (UNSAT),
    while two of its pairs are forced-outcome conflicts under MAIN. Every answer is in the UNSAT witness of size 3, and
    under MAIN each is in a size-2 witness."""
    rows = [rel("q1", C1, A1, "IMPLIES"), rel("q2", C2, A1, "EXCLUDES"), rel("q3", A2, C1, "EQUIV")]
    assert _truth(_relations(rows)) == (True, True)
    assert _truth(_relations([rows[0], rows[1]])) == (False, False)
    assert _truth(_relations([rows[0], rows[2]])) == (True, False)
    assert _truth(_relations([rows[1], rows[2]])) == (True, False)
    res = C.score_run(rows, fx)
    for qid in ("q1", "q2", "q3"):
        assert res["answers"][qid] == {"main": True, "unsat": True, "size": 2}
    assert res["conflicting"] == 3 and res["conflicting_unsat"] == 3
    assert res["by_size"] == {"1": 0, "2": 3, "3": 0}
    assert res["by_op"]["decide_relation"]["conflicting_unsat"] == 3


def test_four_answer_conflict_is_not_counted(fx):
    """A 4-answer cycle through four markets: contradictory as a whole under MAIN, but no subset of at most three answers
    is. Its answers are not counted; the whole set still shows contradiction_detected."""
    rows = [rel("q1", D2, A1, "EQUIV"), rel("q2", A1, B1, "IMPLIES"), rel("q3", D1, C1, "EXCLUDES"),
            rel("q4", C1, B1, "EXCLUDES")]
    assert _truth(_relations(rows)) == (True, False)
    for k in (1, 2, 3):
        for S in combinations(rows, k):
            assert _truth(_relations(S)) == (False, False), [r["qid"] for r in S]
    res = C.score_run(rows, fx)
    for qid in ("q1", "q2", "q3", "q4"):
        assert res["answers"][qid] == {"main": False, "unsat": False, "size": None}
    assert res["conflicting"] == 0 and res["conflicting_unsat"] == 0
    assert res["by_size"] == {"1": 0, "2": 0, "3": 0}
    assert res["contradiction_detected"] is True


def test_consistent_set_has_no_conflicts(fx):
    rows = [rel("q1", A1, B1, "IMPLIES"), rel("q2", B1, C1, "IMPLIES"), rel("q3", A1, D1, "EXCLUDES")]
    assert _truth(_relations(rows)) == (False, False)
    res = C.score_run(rows, fx)
    assert all(v == {"main": False, "unsat": False, "size": None} for v in res["answers"].values())
    assert res["conflicting"] == 0 and res["conflicting_unsat"] == 0
    assert res["by_size"] == {"1": 0, "2": 0, "3": 0}
    assert res["contradiction_detected"] is False
    assert res["claim_bearing"] == 3


# ---------------------------------------------------------------- hand cases: groups

def test_group_that_forces_its_outcomes_is_a_main_conflict_alone(fx):
    res = C.score_run([grp("g1", [A1, C1], "at_least_k:2")], fx)  # both must hold: unit clauses
    assert res["answers"]["g1"] == {"main": True, "unsat": False, "size": 1}
    assert res["conflicting"] == 1 and res["conflicting_unsat"] == 0
    assert res["contradiction_detected"] is True


def test_group_forced_outcome_contradicted_by_an_implication_is_an_unsat_pair(fx):
    """At least two of {A:1, C:1} forces both true. IMPLIES(A:1, A:2) forces A:1 false under MAIN. Together they are
    contradictory under UNSAT as well: A:1 implies A:2, which is the negation of A:1, and A:1 is forced true."""
    rows = [grp("g1", [A1, C1], "at_least_k:2"), rel("q2", A1, A2, "IMPLIES")]
    res = C.score_run(rows, fx)
    assert res["answers"]["g1"] == {"main": True, "unsat": True, "size": 1}
    assert res["answers"]["q2"] == {"main": True, "unsat": True, "size": 1}
    assert res["conflicting"] == 2 and res["conflicting_unsat"] == 2
    assert res["by_size"] == {"1": 2, "2": 0, "3": 0}


def test_group_contradicted_by_its_own_market_complements(fx):
    """At least two of {A:1, A:2} needs both outcomes true, which the complement facts of the market forbid: a single
    answer that is contradictory alone under UNSAT (and so under MAIN)."""
    res = C.score_run([grp("g1", [A1, A2], "at_least_k:2")], fx)
    assert res["answers"]["g1"] == {"main": True, "unsat": True, "size": 1}
    assert res["conflicting_unsat"] == 1


def test_at_least_k_over_three_with_two_forced_false_is_a_cardinality_conflict(fx):
    """At least two of {A:1, B:1, C:1}, where A:1 and B:1 each imply their negation (each forced false). The deferred
    cardinality check finds only one possible outcome: a violation. The triple is UNSAT-minimal (no UNSAT pair: each pair
    keeps two possible outcomes), so all three answers are UNSAT-conflicting. The group alone is not contradictory, and the
    two implications are size-1 MAIN conflicts."""
    rows = [grp("g1", [A1, B1, C1], "at_least_k:2"), rel("q2", A1, A2, "IMPLIES"), rel("q3", B1, B2, "IMPLIES")]
    res = C.score_run(rows, fx)
    assert res["answers"]["g1"] == {"main": False, "unsat": True, "size": None}
    assert res["answers"]["q2"] == {"main": True, "unsat": True, "size": 1}
    assert res["answers"]["q3"] == {"main": True, "unsat": True, "size": 1}
    assert res["conflicting"] == 2 and res["conflicting_unsat"] == 3
    assert res["by_size"] == {"1": 2, "2": 0, "3": 0}


def test_ordered_chain_with_exactly_one_over_three_outcomes(fx):
    """Chain A:1 -> B:1 -> C:1 with at most one of them: A:1 and B:1 are forced false (each reaches its negation), so the
    group is a MAIN conflict of size 1; exactly one of the three is then C:1, which is possible, so it is not UNSAT. The
    implication D:1 -> A:1 is not in any minimal set: it is consistent with D:1 false."""
    group = grp("g1", [A1, B1, C1], "ordered_chain+exactly_one", chain=[A1, B1, C1])
    implies = rel("q2", D1, A1, "IMPLIES")
    res = C.score_run([group, implies], fx)
    assert res["answers"]["g1"] == {"main": True, "unsat": False, "size": 1}
    assert res["answers"]["q2"] == {"main": False, "unsat": False, "size": None}
    assert res["conflicting"] == 1 and res["conflicting_unsat"] == 0
    assert res["contradiction_detected"] is True
    assert res["by_op"]["decide_group"]["claim_bearing"] == 1


def test_invalid_group_is_a_conflict_alone(fx):
    res = C.score_run([grp("g1", [A1, B1], "invalid")], fx)
    assert res["answers"]["g1"] == {"main": True, "unsat": True, "size": 1}
    assert res["conflicting"] == 1 and res["conflicting_unsat"] == 1
    assert res["by_size"] == {"1": 1, "2": 0, "3": 0}


# ---------------------------------------------------------------- claims: what is and is not counted

def test_none_missing_abstained_and_non_relation_rows_are_never_claims(fx):
    rows = [
        rel("n1", A1, B1, "NONE"),
        rel("n2", A1, A2, C.MISSING),  # unanswered or errored
        rel("n3", A1, A2, None),
        rel("n4", A1, A2, "IMPLIES", abstained=True),  # would be a size-1 conflict if it were counted
        grp("n5", [A1, B1], "none"),
        grp("n6", [A1, B1], C.MISSING),
        grp("n7", [A1, B1], None),
        {"op": "decide_binding", "qid": "n8", "pred": "bound", "gold": "bound"},
        {"op": "decide_reading", "qid": "n9", "pred": "opt1", "gold": "opt1"},
    ]
    res = C.score_run(rows, fx)
    assert res["claim_bearing"] == 0 and res["answers"] == {}
    assert res["conflicting"] == 0 and res["contradiction_detected"] is False
    assert res["by_op"] == {"decide_relation": {"claim_bearing": 0, "conflicting": 0, "conflicting_unsat": 0},
                            "decide_group": {"claim_bearing": 0, "conflicting": 0, "conflicting_unsat": 0}}
    # the denominator is the claims alone: one real answer among the non-claims is the whole count
    res = C.score_run(rows + [rel("r1", A1, B1, "IMPLIES")], fx)
    assert res["claim_bearing"] == 1 and list(res["answers"]) == ["r1"]
    assert res["answers"]["r1"]["main"] is False


def test_duplicate_claim_qids_are_refused(fx):
    with pytest.raises(ValueError):
        C.score_run([rel("q1", A1, B1, "IMPLIES"), rel("q1", A1, B1, "EXCLUDES")], fx)


# ---------------------------------------------------------------- order independence

def test_order_of_rows_does_not_change_the_result(fx):
    rows = [rel("q1", C1, A1, "IMPLIES"), rel("q2", C2, A1, "EXCLUDES"), rel("q3", A2, C1, "EQUIV"),
            rel("q4", D2, A1, "EQUIV"), rel("q5", A1, B1, "IMPLIES"), rel("q6", D1, C1, "EXCLUDES"),
            rel("q7", C1, B1, "EXCLUDES"), grp("g8", [A1, B1, C1], "ordered_chain+exactly_one", chain=[A1, B1, C1]),
            grp("g9", [A1, B1], "invalid"), rel("q10", E1, E2, "IMPLIES")]
    base = C.score_run(rows, fx)
    for seed in range(5):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        assert C.score_run(shuffled, fx) == base, seed


# ---------------------------------------------------------------- oracle properties

def _random_relations(rng: random.Random, n: int) -> list[dict]:
    outcomes = [o(m, s) for m in MARKETS for s in (1, 2)]
    rows = []
    for i in range(n):
        a, b = rng.sample(outcomes, 2)
        rows.append(rel(f"q{i}", a, b, rng.choice(RELATIONS)))
    return rows


def _random_mixed(rng: random.Random, n: int) -> list[dict]:
    outcomes = [o(m, s) for m in MARKETS for s in (1, 2)]
    rows = []
    for i in range(n):
        if rng.random() < 0.7:
            a, b = rng.sample(outcomes, 2)
            rows.append(rel(f"q{i}", a, b, rng.choice(RELATIONS)))
            continue
        members = rng.sample(outcomes, rng.choice((2, 3)))
        m = len(members)
        pick = rng.random()
        if pick < 0.05:
            pred = "invalid"
        elif pick < 0.25:
            pred = "ordered_chain"
        elif pick < 0.45:
            pred = "exactly_one"
        elif pick < 0.6:
            pred = "at_most_one"
        elif pick < 0.8:
            pred = f"at_least_k:{rng.randint(1, m)}"
        else:
            pred = f"at_most_k:{rng.randint(1, m)}"
        rows.append(grp(f"q{i}", members, pred, chain=members if pred == "ordered_chain" else None))
    return rows


@pytest.mark.parametrize("seed", range(120))
def test_random_relation_theories_match_the_truth_table(fx, seed):
    """Relation-only theories: score_run's answers, totals and contradiction equal the enumeration of every subset of at most
    three answers under the truth-table predicate."""
    rng = random.Random(seed)
    rows = _random_relations(rng, rng.randint(1, 8))
    res = C.score_run(rows, fx)
    _check_against_oracle(res, [r["qid"] for r in rows], _truth_flag(rows))


@pytest.mark.parametrize("seed", range(120))
def test_random_mixed_theories_match_the_module_predicate_enumeration(fx, seed):
    """Relations and groups (ordered chains, exactly_one, at most one, at least and at most k, invalid): the same
    enumeration, under the module's own predicate. Checks the enumeration and the connected-set pruning."""
    rng = random.Random(1000 + seed)
    rows = _random_mixed(rng, rng.randint(1, 8))
    res = C.score_run(rows, fx)
    _check_against_oracle(res, [r["qid"] for r in rows], _module_flag(rows, fx))


@pytest.mark.parametrize("seed", range(60))
def test_module_predicate_matches_the_truth_table_on_random_subsets(fx, seed):
    rng = random.Random(2000 + seed)
    rows = _random_relations(rng, 6)
    flag = _module_flag(rows, fx)
    for k in (1, 2, 3, 4):
        for S in combinations(range(len(rows)), k):
            assert flag(S) == _truth_flag(rows)(S), S


# ---------------------------------------------------------------- slow: the committed predictions

@pytest.mark.slow
def test_gold_view_has_no_contradiction_on_the_committed_predictions():
    """The stop rule of the analysis: gold's own answers must score zero conflicts. If they do not, the forced-constant rule
    is dropped (UNSAT only) and that is documented."""
    rows = [json.loads(line) for line in
            gzip.open(ROOT / "results" / "v0.1.0" / "predictions" / "gold_view.jsonl.gz", "rt", encoding="utf-8")]
    res = C.score_run(rows, FIXTURE)
    assert res["claim_bearing"] > 0
    assert res["conflicting"] == 0 and res["conflicting_unsat"] == 0
    assert res["contradiction_detected"] is False


@full_analysis
def test_all_twenty_runs_have_consistent_totals_and_gold_view_is_clean():
    """Every run of the leaderboard (15 models and the 5 baselines with predictions) scores, and its totals add up. Runs only
    with MB_RUN_ANALYSIS=1 (about 11 minutes)."""
    lb = json.loads((ROOT / "results" / "v0.1.0" / "leaderboard.json").read_text(encoding="utf-8"))
    runs = [e for e in lb["models"] + lb["baselines"] if e["predictions"]]
    assert len(runs) == 20
    for entry in runs:
        rows = [json.loads(line) for line in
                gzip.open(ROOT / entry["predictions"], "rt", encoding="utf-8")]
        res = C.score_run(rows, FIXTURE)
        assert res["claim_bearing"] == sum(c["claim_bearing"] for c in res["by_op"].values()), entry["id"]
        assert res["conflicting"] <= res["claim_bearing"], entry["id"]
        assert sum(res["by_size"].values()) == res["conflicting"], entry["id"]
        assert isinstance(res["contradiction_detected"], bool), entry["id"]
        if entry["id"] == "gold_view":
            assert res["conflicting"] == 0 and res["contradiction_detected"] is False

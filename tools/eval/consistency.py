"""MEASUREMENT ONLY (reads <fixture>/gold for the market structure; product code never does). Local-conflict rate of one run's
claim-bearing answers (v0.1.1 analysis): an answer is conflicting when it is in a minimal contradictory set of at most 3 answers,
under the MAIN predicate (headline) and the UNSAT-ONLY predicate (logical contradictions only).

usage: python tools/eval/consistency.py PREDICTIONS.jsonl[.gz] [--fixture fixtures/mentat_hard_test]

score_run(rows, fixture_dir) returns {"answers", "claim_bearing", "conflicting", "conflicting_unsat", "contradiction_detected",
"by_size", "by_op", "definition", "max_witness"}; the definition string is the method. Literal 2v is outcome variable v, 2v + 1
its negation (harness/fixed.py).
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python"))

from mentat_bench import ir  # noqa: E402
from mentat_bench.decision_scoring import MISSING  # noqa: E402
from mentat_bench.harness import fixed  # noqa: E402
from mentat_bench.harness.admit import at_least_possible, at_most_possible  # noqa: E402

OPS = ("decide_relation", "decide_group")
RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES")
GROUP_KINDS = ("ordered_chain", "exactly_one", "at_most_one", "at_least_k", "at_most_k")
K_KINDS = ("at_least_k", "at_most_k")  # the terms that carry a k
MAX_WITNESS = 3  # the largest minimal contradictory set counted

DEFINITION = (
    "Local-conflict rate of one run's raw answers, with no gold and no floor. Two predicates are reported separately. MAIN: a set "
    "of answers is contradictory when (a) its clauses and the facts are unsatisfiable, or (b) some outcome literal is forced "
    "false (it reaches its negation), or (c) a deferred cardinality part is violated, or (d) it contains an invalid group. Rule "
    "(b) is justified by the fixture's world: hard_suite.constants() excludes gold-constant outcomes from relations and groups, "
    "so no listed outcome is constant in the world the suite describes, and an answer set that forces an outcome constant "
    "contradicts that world. UNSAT-ONLY is the same predicate without (b): (a), (c) and (d). Outcome variables are the outcome "
    "ids. Each market has the outcomes :1 and :2, and the complement facts (x1 or x2) and (not x1 or not x2) hold for every "
    "market (market membership from gold/meta.jsonl, checked against gold/bindings.jsonl). The facts are never counted as "
    "answers. Markets are treated independently: paraphrase identities between markets (shared candidate classes) are not used, "
    "so a conflict that arises only through two markets that state the same proposition is not counted (an under-count). A "
    "relation answer (pred not NONE, MISSING or None, and not abstained) contributes the clauses of harness/fixed.py "
    "_relation_clauses. A group answer contributes the scorer's canonical class (decision_scoring: the lower bound is the largest "
    "exactly_one or at_least_k over the unscoped decisions on its members, the upper bound the smallest exactly_one, at_most_one "
    "or at_most_k, and the first ordered_chain), not every raw decision. Its terms (ordered_chain, exactly_one, at_most_one, "
    "at_least_k:K, at_most_k:K) are compiled by fixed._group_units (scope None, direction forward; an ordered_chain uses the "
    "row's chain, member i implies member i+1). An invalid group (lower bound above upper bound) is contradictory alone. Its "
    "non-2-CNF parts (at least k of m with 1 <= k < m and m > 2, including exactly_one's at-least-one for m > 2; at most k with "
    "1 < k < m) are deferred cardinality checks with harness/admit.py at_least_possible and at_most_possible on the closure. "
    "These checks are sound but not complete: a violation is a real contradiction, a missed one is not counted. A minimal "
    "contradictory set (for either predicate) is a contradictory set of at most 3 answers none of whose proper subsets is "
    "contradictory under that predicate; a single answer that is contradictory alone is a minimal set of size 1. An answer is "
    "conflicting under a predicate when it belongs to at least one minimal set of that predicate; its size is the smallest such "
    "set under MAIN. The enumeration tests every connected set of at most 3 answers (connected through shared markets: a "
    "contradiction of a set with two market-disjoint parts lies in one part, so minimal sets are connected), with each set "
    "tested against its singles and its pairs. A pair that shares no market is not tested, because it is contradictory only if "
    "one of its answers is, and that answer is excluded already. No cycle test or literal-order heuristic is used. "
    "contradiction_detected is the MAIN predicate applied to all claim-bearing answers together (sound, not complete). NONE "
    "answers, none groups, abstained answers, MISSING or None preds (unanswered or errored, so no claim), readings and bindings "
    "are not claim-bearing and are not scored. Readings are not used: the group class already drops scoped decisions, and "
    "relations have no scope."
)


def _outcomes(fixture: Path) -> tuple[list[str], list[tuple[int, int]]]:
    """(outcome ids in variable order, the (:1, :2) variable pair of every market). Asserts the two-outcome structure."""
    meta = {row["id"]: row["market"] for row in ir.read_jsonl(fixture / "gold" / "meta.jsonl")}
    bound = {row["id"] for row in ir.read_jsonl(fixture / "gold" / "bindings.jsonl")}
    if bound != set(meta):
        raise ValueError("gold/bindings.jsonl and gold/meta.jsonl name different outcomes")
    ids = sorted(meta)
    var = {oid: v for v, oid in enumerate(ids)}
    by_market: dict[str, dict[str, int]] = {}
    for oid in ids:
        prefix, n = oid.rsplit(":", 1)
        market = meta[oid]
        if prefix != f"mh:{market}" or n not in ("1", "2"):
            raise ValueError(f"outcome {oid} does not belong to market {market} as :1 or :2")
        by_market.setdefault(market, {})[n] = var[oid]
    pairs = []
    for market, outs in sorted(by_market.items()):
        if set(outs) != {"1", "2"}:
            raise ValueError(f"market {market} has outcomes {sorted(outs)}, not :1 and :2")
        pairs.append((outs["1"], outs["2"]))
    return ids, pairs


def _claims(rows: list[dict], var: dict[str, int], ids: list[str]) -> list[dict]:
    """The claim-bearing answers, in row order: {qid, op, clauses (literal pairs), cards ((k, literals, at_least)), invalid}."""
    out = []
    for n, row in enumerate(rows):
        op, qid = row.get("op"), row.get("qid")
        invalid = False
        clauses, cards = [], []
        if op == "decide_relation":
            pred = row.get("pred")
            if pred in ("NONE", MISSING, None) or row.get("abstained"):  # MISSING: unanswered or errored, no claim
                continue
            if pred not in RELATIONS:
                raise ValueError(f"relation {qid!r} has pred {pred!r}")
            clauses = fixed._relation_clauses(var[row["a"]], var[row["b"]], pred)
        elif op == "decide_group":
            pred = row.get("pred")
            if pred in ("none", MISSING, None):  # MISSING: unanswered or errored, no claim
                continue
            if pred == "invalid":
                invalid = True
            else:
                for term in pred.split("+"):
                    kind, _, k = term.partition(":")
                    if kind not in GROUP_KINDS:
                        raise ValueError(f"group {qid!r} has term {term!r}")
                    if kind in K_KINDS and not k:
                        raise ValueError(f"group {qid!r} has term {term!r} without its k")
                    if kind == "ordered_chain" and not row.get("chain"):
                        raise ValueError(f"group {qid!r} predicts ordered_chain with no chain")
                    members = row["chain"] if kind == "ordered_chain" else row["members"]
                    group = {"n": n, "kind": kind, "k": int(k) if k else None, "scope": None, "direction": "forward",
                             "members": [var[x] for x in members]}
                    for unit in fixed._group_units(group, ids, [{}]):
                        clauses += unit.clauses
                        if unit.atleast:
                            cards.append((unit.atleast[0], [2 * v for v in unit.atleast[1]], True))
                        if unit.atmost:
                            cards.append((unit.atmost[0], [2 * v for v in unit.atmost[1]], False))
        else:
            continue
        out.append({"qid": qid, "op": op, "clauses": clauses, "cards": cards, "invalid": invalid})
    qids = [c["qid"] for c in out]
    if len(set(qids)) != len(qids):
        raise ValueError("a claim-bearing qid appears twice")
    return out


class _Reach:
    """Reachability of an implication graph (succ[v] = the literals v implies), read from its strongly connected components.
    reach and forced_false are the Closure methods of harness/admit.py, so at_least_possible and at_most_possible take this
    unchanged."""

    def __init__(self, succ: list[list[int]]):
        self.comp = comp = fixed._scc(succ)
        count = max(comp) + 1 if comp else 0  # Tarjan numbers components in reverse topological order: successors are lower
        own = [0] * count
        for v, c in enumerate(comp):
            own[c] |= 1 << v
        out: list[set[int]] = [set() for _ in range(count)]
        for v, row in enumerate(succ):
            for w in row:
                if comp[w] != comp[v]:
                    out[comp[v]].add(comp[w])
        self.desc = desc = [0] * count
        for c in range(count):
            acc = own[c]
            for d in out[c]:
                acc |= desc[d]
            desc[c] = acc

    def reach(self, u: int, v: int) -> bool:
        return bool((self.desc[self.comp[u]] >> v) & 1)

    def forced_false(self, x: int) -> bool:
        return self.reach(x, x ^ 1)


def _violated(reach: _Reach, lits: list[int], k: int, at_least: bool) -> bool:
    if at_least:
        return not at_least_possible(reach, lits, k)
    return not at_most_possible(reach, lits, k)


class _Theory:
    """The claim-bearing answers over the outcome variables. flags(members) = (MAIN, UNSAT-ONLY) of DEFINITION."""

    def __init__(self, market_pairs: list[tuple[int, int]], market_of: list[int], claims: list[dict]):
        self.market_pairs = market_pairs
        self.clauses = [c["clauses"] for c in claims]
        self.cards = [c["cards"] for c in claims]
        self.invalid = [c["invalid"] for c in claims]
        self.markets = [frozenset([market_of[x >> 1] for x, _ in cl] + [market_of[y >> 1] for _, y in cl]
                                  + [market_of[lit >> 1] for _, lits, _ in cd for lit in lits])
                        for cl, cd in zip(self.clauses, self.cards)]

    def flags(self, members) -> tuple[bool, bool]:
        """(MAIN, UNSAT-ONLY) of the answers in members. A set with no market is not contradictory unless it has an invalid
        group. Markets no member touches are left out: they cannot take part in a contradiction."""
        if any(self.invalid[i] for i in members):
            return True, True
        markets = set().union(*(self.markets[i] for i in members)) if members else set()
        if not markets:
            return False, False
        variables = sorted(v for m in markets for v in self.market_pairs[m])
        local = {v: j for j, v in enumerate(variables)}
        n = 2 * len(variables)

        def lit(x: int) -> int:
            return 2 * local[x >> 1] + (x & 1)

        succ: list[list[int]] = [[] for _ in range(n)]
        for m in markets:
            a, b = self.market_pairs[m]
            la, lb = 2 * local[a], 2 * local[b]
            succ[la + 1].append(lb)  # the complement facts (x1 or x2), (not x1 or not x2) as edges
            succ[lb + 1].append(la)
            succ[la].append(lb + 1)
            succ[lb].append(la + 1)
        for i in members:
            for x, y in self.clauses[i]:
                succ[lit(x) ^ 1].append(lit(y))
                succ[lit(y) ^ 1].append(lit(x))
        reach = _Reach(succ)
        comp, desc = reach.comp, reach.desc
        if any(comp[x] == comp[x + 1] for x in range(0, n, 2)):  # unsatisfiable: a variable reaches its negation and back
            return True, True
        if any(self.cards[i] for i in members) and any(
                _violated(reach, [lit(x) for x in lits], k, at_least)
                for i in members for k, lits, at_least in self.cards[i]):
            return True, True
        return any((desc[comp[x]] >> (x ^ 1)) & 1 for x in range(n)), False  # some literal reaches its negation


def _first_adjacent(t: tuple[int, int, int], nbr: dict[int, set[int]]) -> tuple[int, int] | None:
    """The lexicographically first adjacent pair of a sorted triple (adjacent: they share a market)."""
    for p, q in combinations(t, 2):
        if q in nbr[p]:
            return p, q
    return None


def _minimal_sets(theory: _Theory, family) -> tuple[set[tuple[int, ...]], set[tuple[int, ...]]]:
    """(MAIN, UNSAT-ONLY) minimal contradictory sets of at most MAX_WITNESS answers of family, as sorted tuples (DEFINITION)."""
    assert MAX_WITNESS == 3, "the enumeration covers sets of size 1 to 3"
    single = {a: theory.flags((a,)) for a in family}
    main_sets = {(a,) for a in family if single[a][0]}
    unsat_sets = {(a,) for a in family if single[a][1]}
    universe = sorted(a for a in family if not single[a][1])  # an UNSAT-ONLY set has no UNSAT-ONLY single member
    by_market: dict[int, list[int]] = defaultdict(list)
    for a in universe:
        for m in theory.markets[a]:
            by_market[m].append(a)
    nbr: dict[int, set[int]] = {a: set() for a in universe}
    for lst in by_market.values():
        for a in lst:
            nbr[a].update(lst)
    for a in universe:
        nbr[a].discard(a)
    pair_flags: dict[tuple[int, int], tuple[bool, bool]] = {}
    for a in universe:
        for b in nbr[a]:
            if a < b:
                f = theory.flags((a, b))
                pair_flags[(a, b)] = f
                if f[0] and not single[a][0] and not single[b][0]:
                    main_sets.add((a, b))
                if f[1]:
                    unsat_sets.add((a, b))
    for a in universe:
        for b in nbr[a]:
            if b <= a:
                continue
            for c in nbr[a] | nbr[b]:
                if c == a or c == b:
                    continue
                t = tuple(sorted((a, b, c)))
                if _first_adjacent(t, nbr) != (a, b):  # each connected triple is tested once
                    continue
                pairs = [pair_flags.get(p, (False, False)) for p in combinations(t, 2)]  # market-disjoint pairs: not flagged
                cand_main = not any(single[x][0] for x in t) and not any(p[0] for p in pairs)
                cand_unsat = not any(p[1] for p in pairs)
                if not (cand_main or cand_unsat):
                    continue
                f_main, f_unsat = theory.flags(t)
                if cand_main and f_main:
                    main_sets.add(t)
                if cand_unsat and f_unsat:
                    unsat_sets.add(t)
    return main_sets, unsat_sets


def score_run(rows: list[dict], fixture_dir: Path) -> dict:
    """The local-conflict rate of one run's prediction rows (see DEFINITION). rows: the rows of predictions/<id>.jsonl.gz."""
    ids, market_pairs = _outcomes(Path(fixture_dir))
    market_of = [0] * len(ids)
    for m, (a, b) in enumerate(market_pairs):
        market_of[a] = market_of[b] = m
    var = {oid: v for v, oid in enumerate(ids)}
    claims = _claims(rows, var, ids)
    theory = _Theory(market_pairs, market_of, claims)
    main_sets, unsat_sets = _minimal_sets(theory, range(len(claims)))
    smallest: dict[int, int] = {}
    for witness in main_sets:
        for a in witness:
            smallest[a] = min(smallest.get(a, MAX_WITNESS + 1), len(witness))
    unsat_members = {a for witness in unsat_sets for a in witness}
    by_op = {op: {"claim_bearing": 0, "conflicting": 0, "conflicting_unsat": 0} for op in OPS}
    answers = {}
    for a, claim in enumerate(claims):
        block = by_op[claim["op"]]
        block["claim_bearing"] += 1
        block["conflicting"] += int(a in smallest)
        block["conflicting_unsat"] += int(a in unsat_members)
        answers[claim["qid"]] = {"main": a in smallest, "unsat": a in unsat_members, "size": smallest.get(a)}
    return {"answers": answers, "claim_bearing": len(claims), "conflicting": len(smallest),
            "conflicting_unsat": len(unsat_members), "contradiction_detected": theory.flags(range(len(claims)))[0],
            "by_size": {str(s): sum(1 for v in smallest.values() if v == s) for s in range(1, MAX_WITNESS + 1)},
            "by_op": by_op, "definition": DEFINITION, "max_witness": MAX_WITNESS}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("predictions", type=Path, help="predictions/<id>.jsonl.gz of one run")
    ap.add_argument("--fixture", type=Path, default=REPO / "fixtures" / "mentat_hard_test")
    args = ap.parse_args(argv)
    opener = gzip.open if args.predictions.suffix == ".gz" else open
    with opener(args.predictions, "rt", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    started = time.perf_counter()
    result = score_run(rows, args.fixture)
    seconds = time.perf_counter() - started
    claim = result["claim_bearing"]
    summary = {"claim_bearing": claim, "conflicting": result["conflicting"],
               "share": round(result["conflicting"] / claim, 6) if claim else None,
               "conflicting_unsat": result["conflicting_unsat"],
               "share_unsat": round(result["conflicting_unsat"] / claim, 6) if claim else None,
               "contradiction_detected": result["contradiction_detected"], "by_size": result["by_size"],
               "by_op": result["by_op"], "seconds": round(seconds, 2)}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

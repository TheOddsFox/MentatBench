"""Mentat-Hard curated decision suite (docs/mentat_hard.md s3; formats in docs/mentat_hard_contracts.md).

A fixture-building tool on the eval side: it reads gold. `build` writes `candidates.jsonl` (public) and
`gold/suite_labels.jsonl` into a fixture directory; `audit` re-derives a sample of the labels with audit_brute,
which shares no code with the engine.

Label semantics (all exact, from mb-engine over every interpretation of the gold theory):
- Binding: the gold binding's status; `missing` = its codes when unresolved.
- Relation (a, b), as ordered: I = a => b valid in every interpretation (closure, hyp [a]: b ENTAILED);
  J = b => a (closure, hyp [b]: a ENTAILED); X = not (a and b) valid (b REFUTED under hyp [a]).
  EQUIV if I and J, else IMPLIES if I, IMPLIED_BY if J, EXCLUDES if X, else NONE. Closure keeps only the
  interpretations where the hypothesis is satisfiable, and in the others the implication holds vacuously, so
  "ENTAILED in the closure" equals "valid in every interpretation". A relation that holds in base only is NONE.
- I together with X means a is unsatisfiable in every interpretation. Outcomes that are constant in the gold
  theory (ENTAILED or REFUTED with no hypothesis) are left out of relations and groups, so that never happens:
  a constant implies or excludes everything, and its labels would be vacuous.
- Group M (n members): the exact bounds lo..hi on its true count (lo = the largest k with atleast(k, M) valid, hi = the
  smallest k with atmost(k, M) valid), and the members' order when every two are comparable by a valid implication
  (ordered_chain, one implies per link; harness/fixed.py; members are shuffled and the decision picks the order). Its
  terms are base.group_terms(lo, hi, n, chain): ordered_chain first, then the bounds (exactly_one when lo = hi = 1,
  at_least_k lo, at_most_one or at_most_k hi). label and k are terms[0]; a set with no terms is "none" (a decoy).
  `order` lists the members strongest first when the terms name an ordered_chain (each implies the next), and is null
  otherwise. A chain with equivalent members has no unique order, so the set is not asked.
- Same-market Yes/No pairs (stratum trivial) must come out EXCLUDES; build fails otherwise. Build also fails when
  audit_brute finds (or cannot decide) an inconsistent base.
Strata: bindings `real`; relations `trivial` (same market), `hard_distractor` (NONE) or `real`; groups `decoy`
(no terms) or `real`; reading questions `reading` (the proposition u of an unresolved outcome) or `reading_decoy`.
held_out: any outcome of the question has a test-only family (gold/meta.jsonl).

Selection (spec s3, revision 3.1 mix): relations are, in order, every unscoped generator implies edge (as a class-literal
outcome pair, oriented by the seeded rng) and every two-argument atmost-1 (EXCLUDES); then 500 trivial and 800 EQUIV;
then the derived IMPLIES, IMPLIED_BY and EXCLUDES links, deepest inference depth tier first (Depths: 4+, 3, 2; 1 and 0
only when the supply runs out; counting-only entailments are never drawn); then NONE (25% of the budget), two thirds of
it same-league pairs that share rule vocabulary (a team, a threshold, a draw group or a constraint) and the rest
profile-matched to the word overlap of every drawn positive. Each step is capped by the budget left. Relation pools are every pair inside a league plus cross-league pairs that share a rare word
(the same team name in another league); about 30% of each class is held out when there are any. Profile-matched NONE
pairs follow the word-overlap profile (Jaccard deciles) of the non-trivial pairs picked before them, so overlap does not
separate them: within a league that makes them near misses (same team, near-miss thresholds, comparisons read backwards).
Cross-league pairs are NONE by construction and event titles can reveal the league, so they are at most CROSS_CAP of the
profile-matched NONE pairs (target CROSS_SHARE per overlap decile); the rest are within-league. The build report gives
the NONE mix, the depth histograms and two public-field baselines (same event title => related, top half of word
overlap => related). Relation labels carry the gold-side `depth` (int, "count" or null for NONE); candidates never do.
Groups: every unscoped cardinality constraint is one set (no cap; uncovered when it has no row). Then decoys (no terms)
follow the word-overlap profile of the real sets, up to 30% of --groups; the rest are subsets, chains (walks on the
implications) and random or swapped sets, water-filled over the kinds. A set's terms carry the exact bounds.
Reading questions (docs/mentat_hard_contracts.md and docs/mentat_hard.md): every reading row has two options on u's team
and two other-team distractors. For each unresolved outcome u, an ambiguous row has the base and alt options (the bound
outcome of each reading's class with u's polarity). Each ambiguous row is paired with a decoy row of the same base kind:
a bound outcome u, its same-class paraphrase (another market, the answer for both readings), and one same-team option that
is not equivalent to u (the neighbouring threshold, or a threshold of u's team). Both strata share the option structure
(see reading_stats). Reading rows are shuffled among themselves and placed after the group rows.

Degraded mode: without gold/meta.jsonl (toy10k), a league is a connected component of the gold theory and
nothing is held out.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict, deque
from itertools import combinations
from pathlib import Path
from typing import Any

from . import audit_brute as ab
from . import engine, ir
from .adapters.base import group_terms
from .children.systemone_child import MAX_K as CHILD_MAX_K

# Relations (owner decisions 2026-10-30; test set revision 3.1). The trivial and EQUIV counts are absolute (their values at the v2 budget of
# 10,000). NONE is NONE_SHARE of the budget (25%: 1,500 of 6,000); HARD_NONE_SHARE of those are rule-vocabulary pairs, the
# rest profile-matched as in v3. The derived links fill the rest, deepest tier first (DEPTH_TIERS).
TRIVIAL_N = 500  # same-market Yes/No pairs
EQUIV_N = 800  # EQUIV pairs
NONE_SHARE = 0.25  # NONE distractors, as a share of the relation budget
HARD_NONE_SHARE = 2 / 3  # of the NONE distractors: pairs of one league sharing rule vocabulary
DERIVED = ("IMPLIES", "IMPLIED_BY", "EXCLUDES")
DEPTH_TIERS = ("4+", "3", "2", "1", "0")  # derived links are drawn deepest tier first; counting-only never
COUNT = "count"  # the depth of an entailment that no chain of constraint steps reaches (a counting argument)
HELD_OUT_SHARE = 0.30
DECOY_SHARE = 0.30  # groups: decoys (no terms) as a share of the groups asked
GROUP_KINDS = ("exactly_one", "ordered_chain", "at_least_k", "at_most_k", "at_most_one")
READINGS = ("base", "alt")
DECOY_ROW_ATTEMPTS = 40  # draws of a decoy row's mirror and distractors before its candidate is dropped
DECOY_CANDIDATES = 40  # feasible decoy propositions compared per ambiguous proposition (least family overspend)
MAX_MEMBERS = 8
CHAIN_MAX = 4  # members of a walk on the implications (chains): shorter chains than the cardinality subsets
POOL_FACTOR = 4  # group candidates generated per group asked
CROSS_PER_OUTCOME = 3  # cross-league partners per outcome
OVERLAP_BINS = 10  # word-overlap (Jaccard) deciles matched between NONE and real pairs
CROSS_SHARE, CROSS_CAP = 0.15, 0.25  # cross-league share of the NONE stratum: target, and cap when within-league runs short
AUDIT_MIN_CELL = 30
AUDIT_CONFLICT_BUDGET = 5_000_000  # DPLL conflicts per solver call in the suite audit (6,000-relation builds need it)
WORD = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------- fixture

class Fixture:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        gold = self.root / "gold"
        self.gold = gold
        self.corpus = list(ir.read_jsonl(self.root / "corpus.jsonl"))
        self.ids = [r["id"] for r in self.corpus]
        self.text = {r["id"]: r["text"] for r in self.corpus}
        self.title = {r["id"]: r.get("event_title") for r in self.corpus}
        self.bind = {b["id"]: b for b in ir.read_jsonl(gold / "bindings.jsonl")}
        missing = [i for i in self.ids if i not in self.bind]
        if missing:
            raise ValueError(f"{len(missing)} corpus outcomes have no gold binding, e.g. {missing[0]}")
        self.theory = ir.read_json(gold / "theory.json")
        meta_path = gold / "meta.jsonl"
        self.meta = {m["id"]: m for m in ir.read_jsonl(meta_path)} if meta_path.is_file() else None
        self.th = ab.Theory(self.theory, list(self.bind.values()), [])
        if self.meta is None:
            self.league = {i: self.th.root(i) for i in self.ids}
        else:
            self.league = {i: self.meta[i]["league"] for i in self.ids}
        fx = self.root / "fixture.json"
        self.seed = ir.read_json(fx).get("seed", 0) if fx.is_file() else 0

    def market(self, oid: str) -> str:
        return ir.parse_outcome_id(oid)[1]

    def held(self, *oids: str) -> bool:
        return self.meta is not None and any(self.meta[o].get("test_only") for o in oids)


def _rng(fx: Fixture, seed: int | None, purpose: str) -> random.Random:
    return random.Random(f"hard_suite:{fx.seed if seed is None else seed}:{purpose}")


def _var(oid: str) -> dict[str, Any]:
    return {"op": "var", "id": oid}


class _Engine:
    """Batches queries into one `mb-engine eval` run over the gold submission and keeps the elapsed seconds."""

    def __init__(self, fx: Fixture):
        self.fx = fx
        self.seconds = 0.0
        self.queries = 0

    def run(self, queries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if not queries:
            return {}
        with tempfile.TemporaryDirectory(prefix="hard_suite_") as tmp:
            qp, ap = Path(tmp) / "queries.jsonl", Path(tmp) / "answers.jsonl"
            ir.write_jsonl(qp, queries)
            t0 = time.perf_counter()
            engine.eval_submission(self.fx.gold, qp, ap)
            self.seconds += time.perf_counter() - t0
            self.queries += len(queries)
            return {a["qid"]: a for a in ir.read_jsonl(ap)}


def constants(fx: Fixture, eng: _Engine) -> set[str]:
    """Outcomes the gold theory forces (ENTAILED or REFUTED with no hypothesis)."""
    ans = eng.run([{"qid": "base", "family": "closure", "hyp": [], "universe": fx.ids}])["base"]
    return set(ans["labels"]["ENTAILED"]) | set(ans["labels"]["REFUTED"])


def _closures(eng: _Engine, universes: dict[str, set[str]]) -> dict[str, dict[str, str] | None]:
    """{anchor: {id: closure label under hyp [anchor]}}; None when the anchor is unsatisfiable everywhere."""
    anchors = sorted(universes)
    ans = eng.run([{"qid": f"c{n}", "family": "closure", "hyp": [_var(a)], "universe": sorted(universes[a])}
                   for n, a in enumerate(anchors)])
    out: dict[str, dict[str, str] | None] = {}
    for n, a in enumerate(anchors):
        labels = ans[f"c{n}"].get("labels")
        out[a] = None if labels is None else {i: lab for lab, ids in labels.items() for i in ids}
    return out


# ---------------------------------------------------------------- labels

def relation_label(implies: bool, implied_by: bool, excludes: bool) -> str:
    if excludes and (implies or implied_by):
        raise ValueError("an outcome that implies and excludes its partner is unsatisfiable")
    if implies and implied_by:
        return "EQUIV"
    return "IMPLIES" if implies else "IMPLIED_BY" if implied_by else "EXCLUDES" if excludes else "NONE"


def label_pairs(fx: Fixture, eng: _Engine, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Exact labels of ordered pairs, by one closure per anchor (universe = its partners). Pairs with an anchor that
    is unsatisfiable in every interpretation are left out."""
    uni: dict[str, set[str]] = defaultdict(set)
    for a, b in pairs:
        uni[a].add(b)
        uni[b].add(a)
    cl = _closures(eng, uni)
    out = {}
    for a, b in pairs:
        if cl[a] is None or cl[b] is None:
            continue
        excludes = cl[a][b] == "REFUTED"
        assert excludes == (cl[b][a] == "REFUTED"), (a, b)
        out[(a, b)] = relation_label(cl[a][b] == "ENTAILED", cl[b][a] == "ENTAILED", excludes)
    return out


def _card(op: str, k: int, members: list[str]) -> dict[str, Any]:
    return {"op": op, "k": k, "args": [_var(m) for m in members]}


def label_groups(fx: Fixture, eng: _Engine, sets: list[list[str]]) -> list[tuple[list | None, list[str] | None]]:
    """Exact (terms, order) of member sets: terms = group_terms of the exact bounds (feasibility of atmost(j)/atleast(j)
    per set), order from closures when the set is a chain. `order` lists the members strongest first: each implies the
    next, and no two are equivalent, so the order is unique. A chain with an equivalent member pair is (None, None): not
    asked."""
    uni: dict[str, set[str]] = defaultdict(set)
    queries = []
    for s, members in enumerate(sets):
        for m in members:
            uni[m].update(x for x in members if x != m)
        n = len(members)
        queries += [{"qid": f"g{s}:le{j}", "family": "feasibility", "assume": [_card("atmost", j, members)]}
                    for j in range(n)]
        queries += [{"qid": f"g{s}:ge{j}", "family": "feasibility", "assume": [_card("atleast", j, members)]}
                    for j in range(2, n + 1)]
    ans = eng.run(queries)
    cl = _closures(eng, uni)
    out = []
    for s, members in enumerate(sets):
        n = len(members)
        unsat = {q: ans[f"g{s}:{q}"]["decision"] == "UNSAT" for q in [f"le{j}" for j in range(n)]
                 + [f"ge{j}" for j in range(2, n + 1)]}
        k_lo = max((k for k in range(1, n + 1) if unsat[f"le{k - 1}"]), default=0)
        k_hi = min((k for k in range(1, n) if unsat[f"ge{k + 1}"]), default=n)
        chain = all(cl[a] is not None and cl[b] is not None and "ENTAILED" in (cl[a][b], cl[b][a])
                    for a, b in combinations(members, 2))
        if chain and any(cl[a][b] == cl[b][a] == "ENTAILED" for a, b in combinations(members, 2)):
            out.append((None, None))  # equivalent members: the order is not unique, so the set is not asked
            continue
        order = None
        if chain:
            order = sorted(members, key=lambda m: (-sum(cl[m][x] == "ENTAILED" for x in members if x != m), m))  # strongest first
        out.append((group_terms(k_lo, k_hi, n, chain), order))
    return out


# ---------------------------------------------------------------- selection helpers

def _words(text: str) -> frozenset[str]:
    return frozenset(WORD.findall(text.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _pick(order: list[Any], q: int, used: set[Any], key, held) -> list[Any]:
    """Up to q unused items, about HELD_OUT_SHARE of them held out (when any are), in `order` preference."""
    out: list[Any] = []

    def take(items: list[Any], cap: int) -> None:
        for x in items:
            if len(out) >= cap:
                return
            if key(x) not in used:
                used.add(key(x))
                out.append(x)

    hl = [x for x in order if held(x)]
    take(hl, round(q * HELD_OUT_SHARE))
    take([x for x in order if not held(x)], q)
    take(hl, q)
    return out


def _water_fill(avail: dict[str, int], total: int) -> dict[str, int]:
    """Splits `total` as evenly as availability allows."""
    quota, left = {}, total
    for n, kind in enumerate(sorted(avail, key=lambda k: (avail[k], k))):
        quota[kind] = min(avail[kind], left // (len(avail) - n))
        left -= quota[kind]
    return quota


# ---------------------------------------------------------------- relations

def _relation_pool(fx: Fixture, live: list[str]) -> list[tuple[str, str]]:
    """Unordered pairs (sorted): every pair inside a league, plus cross-league pairs sharing a rare word."""
    by_league: dict[Any, list[str]] = defaultdict(list)
    for o in live:
        by_league[fx.league[o]].append(o)
    pairs = {p for group in by_league.values() for p in combinations(group, 2)}
    words = {o: _words(fx.text[o]) for o in live}
    index: dict[str, list[str]] = defaultdict(list)
    for o in live:
        for w in words[o]:
            index[w].append(o)
    for o in live:
        rare = sorted(words[o], key=lambda w: (len(index[w]), w))[:2]
        cands = {c for w in rare for c in index[w][:200] if fx.league[c] != fx.league[o]}
        best = sorted(cands, key=lambda c: (-_jaccard(words[o], words[c]), c))[:CROSS_PER_OUTCOME]
        pairs.update(tuple(sorted((o, c))) for c in best)
    return sorted(pairs)


def _by_class(fx: Fixture, live: set[str]) -> dict[tuple[str, bool], list[str]]:
    """Live bound outcomes by (class, negated)."""
    by_class: dict[tuple[str, bool], list[str]] = defaultdict(list)
    for o in fx.ids:
        b = fx.bind[o]
        if b["status"] == "bound" and o in live:
            by_class[(b["class"], bool(b["negated"]))].append(o)
    return by_class


def _generator_edges(fx: Fixture, by_class: dict, rng: random.Random) -> list[tuple[str | None, str | None]]:
    """The unscoped generator constraints the suite must ask, as outcome pairs: each implies (a => b), and each
    two-argument atmost-1 (a and b exclusive). A literal with no live outcome is None (uncovered)."""
    out = []
    for c in fx.theory["constraints"]:
        e = c["expr"]
        if c.get("interps"):
            continue
        if e["op"] == "implies":
            out.append(tuple(_literal_outcome(fx, e[k], by_class, rng) for k in ("a", "b")))
        elif e["op"] == "atmost" and e["k"] == 1 and len(e["args"]) == 2:
            out.append(tuple(_literal_outcome(fx, x, by_class, rng) for x in e["args"]))
    return out


class Depths:
    """Inference depth of an entailed relation (docs/mentat_hard.md, section 3). The graph has one node per class literal
    (a class, or its negation) and one edge per unscoped constraint of the gold theory, with its contrapositive: each
    implication a => b; each pairwise exclusion of an atmost-1 or exactly-1 constraint (a => not b); and, for an exactly-1
    constraint of two arguments, the at-least-one edge (not x => y). An edge costs 1, a rule constraint a reader chains;
    a score-ladder step (T(x,t+1) => T(x,t) for one team) costs 0, wherever it comes from (rule 1 has no card sentence).
    A relation's depth is the fewest cost on a chain: IMPLIES a => b: the cost from a to b; IMPLIED_BY: from b to a;
    EXCLUDES: from a to not b; EQUIV: the larger of the two directions. Paraphrases (one class), negations and readings
    cost 0; an unresolved outcome is its reading's class under each interpretation, and the entailment must hold in both,
    so it takes the larger of the two. An entailment that no chain reaches (it needs a counting argument) has depth COUNT.
    Without meta.jsonl (a degraded fixture) no ladder step is recognized, so every edge costs 1."""

    def __init__(self, fx: Fixture):
        self.fx = fx
        self.readings = _readings(fx)
        info: dict[Any, tuple[Any, Any, Any]] = {}  # class -> (kind, team identity, t), for the ladder
        if fx.meta is not None:
            for o in fx.ids:
                b = fx.bind[o]
                if b["status"] == "bound":
                    m = fx.meta[o]
                    info.setdefault(b["class"], (m["kind"], (m["league"], m["team"]), m["t"]))
        self.edges: dict[tuple, list[tuple[tuple, int]]] = defaultdict(list)
        self._dist: dict[tuple, dict[tuple, int]] = {}
        for c in fx.theory["constraints"]:
            e = c["expr"]
            if c.get("interps"):
                continue
            if e["op"] == "implies":
                a, b = self._lit(e["a"]), self._lit(e["b"])
                if a[0] is None or b[0] is None:
                    continue
                ka, kb = info.get(a[0]), info.get(b[0])
                ladder = (ka is not None and kb is not None and a[1] and b[1] and ka[0] == "thr" and kb[0] == "thr"
                          and ka[1] == kb[1] and ka[2] is not None and kb[2] is not None and ka[2] == kb[2] + 1)
                self._add(a, b, 0 if ladder else 1)
            elif e["op"] in ("atmost", "exactly") and e["k"] == 1:
                lits = [self._lit(x) for x in e["args"]]
                if any(x[0] is None for x in lits):
                    continue
                for x, y in combinations(lits, 2):
                    self._add(x, (y[0], not y[1]), 1)
                if e["op"] == "exactly" and len(lits) == 2:  # exactly one of two: not x => y (and not y => x)
                    x, y = lits
                    self._add((x[0], not x[1]), y, 1)

    def _lit(self, e: dict[str, Any]) -> tuple[Any, bool]:
        neg = False
        while e["op"] == "not":
            e, neg = e["a"], not neg
        if e["op"] == "ref":
            return e["name"], not neg
        b = self.fx.bind[e["id"]]
        return b["class"], (not b["negated"]) != neg

    def _add(self, u: tuple, v: tuple, cost: int) -> None:
        for x, y in ((u, v), ((v[0], not v[1]), (u[0], not u[1]))):  # the implication and its contrapositive
            self.edges[x].append((y, cost))

    def _dd(self, u: tuple | None, v: tuple | None) -> int | None:
        if u is None or v is None:
            return None
        if u == v:
            return 0
        if u not in self._dist:  # 0-1 BFS from u
            dist = {u: 0}
            queue = deque([u])
            while queue:
                x = queue.popleft()
                for y, w in self.edges.get(x, ()):
                    if y not in dist or dist[x] + w < dist[y]:
                        dist[y] = dist[x] + w
                        (queue.appendleft if w == 0 else queue.append)(y)
            self._dist[u] = dist
        return self._dist[u].get(v)

    def _interp(self, o: str) -> tuple[tuple | None, tuple | None]:
        """The literal of outcome o under each interpretation (base, alt); None where it has no class."""
        b = self.fx.bind[o]
        if b["status"] == "bound":
            lit = (b["class"], not b["negated"])
            return lit, lit
        r = self.readings.get(o)
        if r is None:
            return None, None
        return tuple((r[k], not r["negated"]) for k in READINGS)

    def _implies(self, a: str, b: str) -> int | None:
        ds = [self._dd(x, y) for x, y in zip(self._interp(a), self._interp(b))]
        return None if None in ds else max(ds)

    def _excludes(self, a: str, b: str) -> int | None:
        ds = [None if x is None or y is None else self._dd(x, (y[0], not y[1]))
              for x, y in zip(self._interp(a), self._interp(b))]
        return None if None in ds else max(ds)

    def chain(self, a: str, b: str) -> bool:
        """Whether a chain of constraints entails a => b, b => a or a and b unsatisfiable (the soundness check of NONE)."""
        return self._implies(a, b) is not None or self._implies(b, a) is not None or self._excludes(a, b) is not None

    def of(self, a: str, b: str, label: str) -> int | str | None:
        """The depth of the ordered pair (a, b) with its label: an int, COUNT, or None for NONE."""
        if label == "NONE":
            return None
        if label == "EXCLUDES":
            d = self._excludes(a, b)
        elif label == "IMPLIES":
            d = self._implies(a, b)
        elif label == "IMPLIED_BY":
            d = self._implies(b, a)
        else:  # EQUIV
            x, y = self._implies(a, b), self._implies(b, a)
            d = None if x is None or y is None else max(x, y)
        return COUNT if d is None else d


def _ladder_pair(fx: Fixture, a: str, b: str) -> bool:
    """Whether a and b are one team's thresholds one point apart (a score-ladder pair, whatever the orientation)."""
    if fx.meta is None:
        return False
    ma, mb = fx.meta[a], fx.meta[b]
    return (ma["kind"] == mb["kind"] == "thr" and (ma["league"], ma["team"]) == (mb["league"], mb["team"])
            and ma["t"] is not None and mb["t"] is not None and abs(ma["t"] - mb["t"]) == 1)


def _bucket(d: int | str | None) -> str:
    """The depth stratum of a tag: 0, 1, 2, 3, 4+ (4 or more), count, or none (NONE)."""
    if d is None:
        return "none"
    return d if isinstance(d, str) else str(d) if d < 4 else "4+"


def _co_occurring(fx: Fixture) -> set[frozenset]:
    """Pairs of classes that one unscoped constraint of the gold theory states together (one card sentence's constraint)."""
    out: set[frozenset] = set()

    def walk(e: dict[str, Any], acc: set) -> None:
        if e["op"] == "ref":
            acc.add(e["name"])
        elif e["op"] == "var":
            acc.add(fx.bind[e["id"]]["class"])
        for k in ("a", "b"):
            if isinstance(e.get(k), dict):
                walk(e[k], acc)
        for x in e.get("args", ()):
            walk(x, acc)

    for c in fx.theory["constraints"]:
        if not c.get("interps"):
            acc: set = set()
            walk(c["expr"], acc)
            acc.discard(None)
            out.update(frozenset(p) for p in combinations(sorted(acc), 2))
    return out


def _shares_vocabulary(fx: Fixture, p: tuple[str, str], co: set[frozenset]) -> bool:
    """A same-league pair whose outcomes share rule vocabulary: a team, a threshold, a draw group (subgroup), or a
    constraint of the theory (both classes in one card sentence's constraint)."""
    a, b = p
    if frozenset((fx.bind[a]["class"], fx.bind[b]["class"])) in co:
        return True
    if fx.meta is None:
        return False
    ma, mb = fx.meta[a], fx.meta[b]
    return (ma["team"] == mb["team"] or (ma["t"] is not None and ma["t"] == mb["t"])
            or (ma["subgroup"] is not None and ma["subgroup"] == mb["subgroup"]))


def _tier(d: int | str | None) -> str | None:
    """The DEPTH_TIERS entry of a depth; None for a counting-only entailment (never drawn) or NONE."""
    return None if not isinstance(d, int) else "4+" if d >= 4 else str(d)


def build_relations(fx: Fixture, eng: _Engine, live: list[str], n: int, rng: random.Random) -> tuple[list, dict]:
    """n relation rows (revision 3.1), in this order: every generator edge; TRIVIAL_N trivial and EQUIV_N EQUIV pairs (absolute
    counts); the derived links, deepest tier first (DEPTH_TIERS: 4+, 3, 2, and 1 or 0 only when the supply runs out), each
    tier water-filled over IMPLIES, IMPLIED_BY and EXCLUDES; then NONE, NONE_SHARE of n, of which HARD_NONE_SHARE are
    same-league pairs sharing rule vocabulary and the rest profile-matched (the word overlap of every drawn positive,
    cross-league capped). Each step is capped by the budget left, so the total is n unless the pool runs short (reported).
    A pair whose depth is COUNT is never drawn. Every row's depth is Depths.of; a generator edge that is not a ladder step
    and not EQUIV must be depth 1 (a build error otherwise). EQUIV, EXCLUDES and NONE have no direction: their rows are
    oriented at random."""
    pool = _relation_pool(fx, live)
    labels = label_pairs(fx, eng, pool + [(b, a) for a, b in pool])
    depth = Depths(fx)
    words = {o: _words(fx.text[o]) for o in live}
    same = lambda p: fx.market(p[0]) == fx.market(p[1])  # noqa: E731
    by_class: dict[str, list] = defaultdict(list)
    for p, lab in labels.items():
        if same(p) and lab != "EXCLUDES":
            raise ValueError(f"same-market pair {p} is {lab}, not EXCLUDES: Yes and No are not complementary")
        if lab == "NONE":
            if depth.chain(*p):
                raise ValueError(f"pair {p} is NONE but a chain of constraints entails it")
            by_class["NONE_cross" if fx.league[p[0]] != fx.league[p[1]] else "NONE"].append(p)
        elif _tier(depth.of(p[0], p[1], lab)) is not None:  # a COUNT entailment is never drawn
            by_class["trivial" if same(p) else lab].append(p)
    for v in by_class.values():
        v.sort()
        rng.shuffle(v)
    bin_of = lambda p: min(int(_jaccard(words[p[0]], words[p[1]]) * OVERLAP_BINS), OVERLAP_BINS - 1)  # noqa: E731
    for c in ("NONE", "NONE_cross"):
        for p in by_class[c]:
            by_class[f"{c}:{bin_of(p)}"].append(p)
    co = _co_occurring(fx)
    for p in by_class["NONE"]:
        if _shares_vocabulary(fx, p, co):
            by_class["HARD"].append(p)
            by_class[f"HARD:{bin_of(p)}"].append(p)
    # the derived links by tier, each unordered pair once: IMPLIES holds every implication pair in its implying direction
    # (IMPLIED_BY holds the reverse of the same pairs), and EXCLUDES is listed both ways, so only its sorted direction counts
    for c in ("IMPLIES", "EXCLUDES"):
        for p in by_class[c]:
            if c == "EXCLUDES" and p[0] > p[1]:
                continue
            tier = _tier(depth.of(p[0], p[1], labels[p]))
            by_class[f"{'EXCLUDES' if c == 'EXCLUDES' else 'IMPL'}@{tier}"].append(p)
    for tier in DEPTH_TIERS:
        rng.shuffle(by_class[f"IMPL@{tier}"])
        rng.shuffle(by_class[f"EXCLUDES@{tier}"])
    used: set = set()
    chosen: list[tuple[tuple[str, str], str]] = []
    shortfall: dict[str, int] = {}
    uncovered = dropped = 0
    # 1. every generator edge, oriented by the rng (IMPLIES and IMPLIED_BY stay balanced)
    edges = _generator_edges(fx, _by_class(fx, set(live)), rng)
    for pair in edges:
        if None in pair or pair[0] == pair[1] or pair not in labels:  # labels hold both orientations
            uncovered += 1
            continue
        a, b = pair if rng.random() < 0.5 else pair[::-1]
        if frozenset((a, b)) in used:
            dropped += 1
            continue
        used.add(frozenset((a, b)))
        chosen.append(((a, b), "real"))
    n_edges = len(chosen)

    def pick(c: str, q: int) -> int:
        got = _pick(by_class[c], min(q, n - len(chosen)), used, lambda p: frozenset(p), lambda p: fx.held(*p))
        chosen.extend((p, c) for p in got)
        return len(got)

    def pick_implication(c: str, tier: str, q: int) -> int:
        """IMPLIES or IMPLIED_BY at a tier: the pair is oriented so that its label is c, so the two stay balanced."""
        got = _pick(by_class[f"IMPL@{tier}"], min(q, n - len(chosen)), used, lambda p: frozenset(p),
                    lambda p: fx.held(*p))
        chosen.extend(((p if labels[p] == c else p[::-1]), f"{c}@{tier}") for p in got)
        return len(got)

    for c, q in (("trivial", TRIVIAL_N), ("EQUIV", EQUIV_N)):
        got = pick(c, q)
        if got < q:
            shortfall[c] = q - got
    # 2. the derived links, deepest tier first. The derived budget is what the NONE count leaves. IMPLIES and IMPLIED_BY
    # share one pool of implication pairs, so each takes half of it (the odd pair to a random side); the slots are the caps
    q_none = round(NONE_SHARE * n)
    q_hard = round(HARD_NONE_SHARE * q_none)
    tiers: dict[str, dict[str, dict[str, int]]] = {}
    for tier in DEPTH_TIERS:
        impl = sum(frozenset(p) not in used for p in by_class[f"IMPL@{tier}"])
        excl = sum(frozenset(p) not in used for p in by_class[f"EXCLUDES@{tier}"])
        big, small = ("IMPLIES", "IMPLIED_BY") if rng.random() < 0.5 else ("IMPLIED_BY", "IMPLIES")
        caps = {"EXCLUDES": excl, big: (impl + 1) // 2, small: impl // 2}
        drawn = {}
        for c, q in _water_fill(caps, n - q_none - len(chosen)).items():
            drawn[c] = pick(f"EXCLUDES@{tier}", q) if c == "EXCLUDES" else pick_implication(c, tier, q)
        tiers[tier] = {"caps": dict(sorted(caps.items())), "drawn": dict(sorted(drawn.items()))}
    if len(chosen) < n - q_none:
        shortfall["derived"] = n - q_none - len(chosen)
    # 3. NONE: the word-overlap profile of every non-trivial pair picked so far (edges, EQUIV and the derived links) is the
    # reference for both parts. The rule-vocabulary pairs come first, then the profile-matched pairs (cross-league capped)
    profile = Counter(bin_of(p) for p, c in chosen if c != "trivial")
    total = sum(profile.values())
    n_hard = 0
    for b, cnt in sorted(profile.items()):
        n_hard += pick(f"HARD:{b}", min(round(q_hard * cnt / total), q_hard - n_hard))
    n_hard += pick("HARD", q_hard - n_hard)  # bins that ran short
    if n_hard < q_hard:
        shortfall["NONE_hard"] = q_hard - n_hard
    q_prof = q_none - n_hard
    n_cross = n_within = 0
    for b, cnt in sorted(profile.items()):
        t = min(round(q_prof * cnt / total), q_prof - n_cross - n_within)
        c = pick(f"NONE_cross:{b}", round(CROSS_SHARE * t))
        w = pick(f"NONE:{b}", t - c)
        c += pick(f"NONE_cross:{b}", min(int(CROSS_CAP * t) - c, t - c - w))
        n_cross, n_within = n_cross + c, n_within + w
    n_within += pick("NONE", q_prof - n_cross - n_within)  # bins that ran short
    n_cross += pick("NONE_cross", min(int(CROSS_CAP * q_prof) - n_cross, q_prof - n_cross - n_within))
    if n_cross + n_within < q_prof:
        shortfall["NONE"] = q_prof - n_cross - n_within
    rng.shuffle(chosen)
    rows = []
    for (a, b), c in chosen:
        lab = labels[(a, b)]
        if lab in ("EQUIV", "EXCLUDES", "NONE") and rng.random() < 0.5:  # no direction: a < b is no cue
            a, b = b, a
        d = depth.of(a, b, lab)
        if c == "real" and lab != "EQUIV":  # a generator edge: depth 1 unless it is a score-ladder step (depth 0)
            want = 0 if _ladder_pair(fx, a, b) else 1
            if d != want:
                raise ValueError(f"generator edge ({a}, {b}) is {lab} at depth {d}, not {want}")
        stratum = "trivial" if c == "trivial" else "hard_distractor" if c.startswith(("NONE", "HARD")) else "real"
        rows.append(({"op": "decide_relation", "a": a, "b": b},
                     {"label": lab, "k": None, "missing": None, "order": None, "held_out": fx.held(a, b),
                      "stratum": stratum, "depth": d}))
    # supply: the derived pool (one entry per unordered pair, in pool orientation) by label and depth, without the generator
    # edges (asked) and the trivial pairs
    edge_sets = {frozenset(p) for p, c in chosen if c == "real"}
    supply: dict[str, Counter] = defaultdict(Counter)
    for p in pool:
        if labels[p] in DERIVED and not same(p) and frozenset(p) not in edge_sets:
            supply[labels[p]][_bucket(depth.of(p[0], p[1], labels[p]))] += 1
    asked: dict[str, Counter] = defaultdict(Counter)
    overlap: dict[str, list[int]] = defaultdict(lambda: [0] * OVERLAP_BINS)
    for row, lab in rows:
        asked[lab["label"]][_bucket(lab["depth"])] += 1
        overlap[lab["label"]][bin_of((row["a"], row["b"]))] += 1
    share = round(n_cross / (n_hard + n_cross + n_within), 4) if n_hard + n_cross + n_within else None
    return rows, {"pool_pairs": len(labels), "generator_edges": n_edges, "generator_dropped_duplicates": dropped,
                  "uncovered": uncovered, "shortfall": shortfall, "cross_league_none_share": share,
                  "none": {"hard_vocabulary": n_hard, "profile_matched": n_cross + n_within, "cross_league": n_cross},
                  "tiers": tiers, "overlap_deciles": {k: v for k, v in sorted(overlap.items())},
                  "depth_asked": {k: dict(sorted(v.items())) for k, v in sorted(asked.items())},
                  "depth_supply": {k: dict(sorted(v.items())) for k, v in sorted(supply.items())},
                  "public_baselines": public_baselines(fx, rows)}


def public_baselines(fx: Fixture, rows: list) -> dict[str, Any]:
    """Related (label not NONE) vs NONE, predicted from public fields only: the same event title, or word overlap
    in the top half. Accuracies near the related share mean the distractors are not separable by these cues."""
    if not rows:
        return {}
    truth = [lab["label"] != "NONE" for _, lab in rows]
    jac = [_jaccard(_words(fx.text[q["a"]]), _words(fx.text[q["b"]])) for q, _ in rows]
    cut = sorted(jac)[len(jac) // 2]

    def acc(pred: list[bool]) -> float:
        return round(sum(p == t for p, t in zip(pred, truth)) / len(truth), 4)

    return {"related_share": round(sum(truth) / len(truth), 4),
            "same_event_title_acc": acc([fx.title[q["a"]] == fx.title[q["b"]] for q, _ in rows]),
            "top_overlap_acc": acc([j >= cut for j in jac])}


# ---------------------------------------------------------------- groups

def _literal_outcome(fx: Fixture, e: dict[str, Any], by_class: dict, rng: random.Random) -> str | None:
    """An outcome whose meaning is the literal `e` (var, ref or their negation), or None."""
    neg = False
    while e["op"] == "not":
        e, neg = e["a"], not neg
    if e["op"] == "var":
        b = fx.bind.get(e["id"])
        if not neg:
            return e["id"]
        if b is None or b["status"] != "bound":
            return None
        key = (b["class"], not b["negated"])
    elif e["op"] == "ref":
        key = (e["name"], neg)
    else:
        return None
    cands = by_class.get(key)
    return rng.choice(cands) if cands else None


def _group_pool(fx: Fixture, live: set[str], target: int, rng: random.Random) -> tuple[list[list[str]], int, list]:
    """(pool, n_full, cards): the member sets of every unscoped cardinality constraint come first (n_full of them, no cap;
    `cards` is their member list per constraint, None where a literal has no live outcome), then subsets of those,
    walks on the implications (chains) and random or swapped sets (decoys and the rest)."""
    by_class = _by_class(fx, live)
    cards, edges = [], defaultdict(set)
    for c in fx.theory["constraints"]:
        e = c["expr"]
        if c.get("interps"):
            continue
        if e["op"] in ("exactly", "atleast", "atmost"):
            ms = [_literal_outcome(fx, x, by_class, rng) for x in e["args"]]
            cards.append(ms if all(m in live for m in ms) else None)
        elif e["op"] == "implies":
            a, b = (_literal_outcome(fx, e[k], by_class, rng) for k in ("a", "b"))
            if a in live and b in live and a != b:
                edges[a].add(b)
    seen: set[frozenset[str]] = set()
    pool: list[list[str]] = []

    def add(ms: list[str]) -> None:  # one member per market and per class: no complement or paraphrase twins
        keys = [k for m in ms for k in (fx.market(m), fx.bind[m]["class"] or m)]
        if 2 <= len(ms) <= MAX_MEMBERS and len(set(keys)) == len(keys) and frozenset(ms) not in seen:
            seen.add(frozenset(ms))
            pool.append(ms)

    full = [ms for ms in cards if ms is not None and len(set(ms)) == len(ms)]
    for ms in full:
        add(ms)
    n_full = len(pool)
    third = POOL_FACTOR * target // 3
    for ms in full:  # subsets of the cardinality sets, while the pool is short
        if len(pool) >= third:
            break
        for _ in range(2):
            add(rng.sample(ms, rng.randint(2, min(MAX_MEMBERS, len(ms)))))
    starts = sorted(edges)
    for _ in range(20 * third):
        if len(pool) >= 2 * third or not starts:
            break
        walk, length = [rng.choice(starts)], rng.randint(2, CHAIN_MAX)
        classes = {fx.bind[walk[0]]["class"]}
        while len(walk) < length:
            nxt = [x for x in sorted(edges[walk[-1]]) if x not in walk and fx.bind[x]["class"] not in classes]
            if not nxt:
                break
            walk.append(rng.choice(nxt))
            classes.add(fx.bind[walk[-1]]["class"])
        if len(walk) >= 2:
            add(walk)
    by_league: dict[Any, list[str]] = defaultdict(list)
    for o in sorted(live):
        by_league[fx.league[o]].append(o)
    leagues = [v for _, v in sorted(by_league.items(), key=lambda kv: str(kv[0])) if len(v) >= 2]
    real = list(pool)
    for _ in range(20 * third):
        if len(pool) >= POOL_FACTOR * target or not leagues:
            break
        r = rng.random()
        if full and r < 1 / 3:  # a cardinality set's members, some read negated: the same scope and wording
            card = rng.choice(full)
            ms = rng.sample(card, rng.randint(2, min(MAX_MEMBERS, len(card))))
            flip = set(rng.sample(range(len(ms)), rng.randint(1, max(1, len(ms) - 1))))
            out = []
            for j, m in enumerate(ms):
                b = fx.bind[m]
                other = by_class.get((b["class"], not b["negated"])) if j in flip else [m]
                out.append(rng.choice(other) if other else m)
            add(out)
        elif real and r < 2 / 3:  # a real set with one member swapped for another of its league
            ms = list(rng.choice(real))
            ms[rng.randrange(len(ms))] = rng.choice(by_league[fx.league[ms[0]]])
            add(ms)
        else:
            group = rng.choice(leagues)
            add(rng.sample(group, rng.randint(2, min(MAX_MEMBERS, len(group)))))
    return pool, n_full, cards


def build_groups(fx: Fixture, eng: _Engine, live: set[str], n: int, rng: random.Random) -> tuple[list, dict]:
    """n group rows: every cardinality set (no cap), then decoys (no terms) up to DECOY_SHARE of n, matched to the
    word-overlap profile of the real sets; the rest of the budget is water-filled over the kinds of the other sets."""
    pool, n_full, cards = _group_pool(fx, live, n, rng)
    labels = label_groups(fx, eng, pool)  # (terms, order) per set; terms None: not asked
    by_kind: dict[str, list[int]] = defaultdict(list)
    none: list[int] = []
    for s in range(n_full, len(pool)):
        terms = labels[s][0]
        if terms is None:
            continue
        if terms:
            by_kind[terms[0][0]].append(s)
        else:
            none.append(s)
    full = [s for s in range(n_full) if labels[s][0] is not None]
    full_none = sum(not labels[s][0] for s in full)
    q_none = max(0, min(len(none), round(DECOY_SHARE * n) - full_none))
    quota = _water_fill({k: len(by_kind[k]) for k in GROUP_KINDS}, max(0, n - len(full) - q_none))
    chosen = full + [s for kind, q in quota.items() for s in rng.sample(by_kind[kind], q)]
    # decoys follow the word-overlap profile (deciles of mean pairwise Jaccard) of the real sets, so wording does not
    # separate them; a decile short of decoys is filled from the rest
    overlap = {s: _set_overlap(fx, pool[s]) for s in range(len(pool))}
    bin_of = lambda s: min(int(overlap[s] * OVERLAP_BINS), OVERLAP_BINS - 1)  # noqa: E731
    none_by_bin: dict[int, list[int]] = defaultdict(list)
    for s in rng.sample(none, len(none)):
        none_by_bin[bin_of(s)].append(s)
    profile = Counter(bin_of(s) for s in chosen if labels[s][0])
    decoys: list[int] = []
    for b, cnt in sorted(profile.items()):
        want = min(round(q_none * cnt / sum(profile.values())), q_none - len(decoys))
        decoys += none_by_bin[b][:want]
    taken = set(decoys)
    decoys += [s for s in none if s not in taken][:q_none - len(decoys)]
    chosen += decoys
    rng.shuffle(chosen)
    rows = []
    for s in chosen:
        members = list(pool[s])
        if len(members) > CHILD_MAX_K:  # a larger set would be asked without its bounds (the child asks none over MAX_K)
            raise ValueError(f"group of {len(members)} members exceeds the child's MAX_K {CHILD_MAX_K}")
        rng.shuffle(members)
        terms, order = labels[s]
        kind, k = terms[0] if terms else ("none", None)
        rows.append(({"op": "decide_group", "members": members, "source": "curated"},
                     {"label": kind, "k": k, "missing": None, "order": order, "held_out": fx.held(*members),
                      "stratum": "decoy" if not terms else "real", "terms": [list(t) for t in terms]}))
    covered = {frozenset(pool[s]) for s in chosen}
    uncovered = sum(ms is None or frozenset(ms) not in covered for ms in cards)
    return rows, {"pool_sets": len(pool), "cardinality_sets": n_full, "uncovered": uncovered,
                  "available": {k: len(v) for k, v in sorted(by_kind.items())},
                  "not_unique_chains": sum(1 for terms, _ in labels if terms is None),
                  "decoy_shortfall": q_none - len(decoys), "shortfall": n - len(rows),
                  "public_baselines": group_baselines(fx, rows)}


def _readings(fx: Fixture) -> dict[str, dict[str, Any]]:
    """{unresolved outcome: {"base": class, "alt": class, "negated": flag, "partner": the other Yes/No outcome}}. Under
    reading r the outcome is equivalent to the bound outcome of class[r] whose negated flag is `flag`. Read from the scoped
    iffs (interps base and alt: the proposition's meaning against a class) and the complementary Yes/No iff; an outcome
    without both readings is left out."""
    reading: dict[str, dict[str, tuple[str, bool]]] = defaultdict(dict)
    partner: dict[str, str] = {}
    for c in fx.theory["constraints"]:
        e = c["expr"]
        if e["op"] != "iff":
            continue
        if c.get("interps"):
            neg = e["a"]["op"] == "not"
            var = e["a"]["a"] if neg else e["a"]
            if var["op"] == "var" and e["b"]["op"] == "ref":
                (r,) = c["interps"]
                reading[var["id"]][r] = (e["b"]["name"], neg)
        elif e["a"]["op"] == "var" and e["b"]["op"] == "not" and e["b"]["a"]["op"] == "var":
            partner[e["a"]["id"]], partner[e["b"]["a"]["id"]] = e["b"]["a"]["id"], e["a"]["id"]
    out: dict[str, dict[str, Any]] = {}
    for y, rd in reading.items():
        if set(rd) != set(READINGS) or y not in partner:
            continue
        (cb, nb), (ca, na) = rd["base"], rd["alt"]
        if nb != na:
            raise ValueError(f"{y}: the readings state different polarities")
        n = partner[y]
        out[y] = {"base": cb, "alt": ca, "negated": nb, "partner": n}
        out[n] = {"base": cb, "alt": ca, "negated": not nb, "partner": y}
    return out


def tid(fx: Fixture, oid: str) -> tuple[Any, Any]:
    """A team's identity: its league and name (a team name can recur in another league of a split)."""
    return fx.meta[oid]["league"], fx.meta[oid]["team"]


def reading_stats(fx: Fixture, rows: list[tuple[dict, dict]]) -> dict[str, dict[str, Any]]:
    """Per reading stratum, the counts a file-level classifier could use. rows: (candidate, label) pairs of the reading rows.
    rows: the row count; same_team: a histogram of the options on u's team per row; option_kinds; u_kind; number_match: rows
    where a same-team option has u's threshold; u_twin: rows whose u's complement (the other outcome of u's market) is named
    by another reading row; option_twin: rows where an option's complement is named by another reading row; held_rows: rows
    with a test-only member; u_negated and option_negated: negated flags; families: option wording families; base_t and
    other_t: the thresholds of the base option and of the two other same-team options (the alt or mirror, and the near miss). Ambiguity is detectable from u's own wording by design; these counts compare the rest."""
    markets: dict[str, list[str]] = defaultdict(list)
    for m in fx.meta.values():
        markets[m["market"]].append(m["id"])

    def complement(o: str) -> str | None:
        return next((x for x in markets[fx.meta[o]["market"]] if x != o), None)

    where: dict[str, set[int]] = defaultdict(set)  # outcome -> indices of the reading rows that name it
    for i, (cand, _) in enumerate(rows):
        for m in cand["members"]:
            where[m].add(i)

    def twin(o: str, i: int) -> bool:
        c = complement(o)
        return c is not None and any(j != i for j in where[c])

    counters = ("same_team", "option_kinds", "u_kind", "families", "base_t", "other_t")
    readings = _readings(fx)  # an unresolved u's polarity is its wording's, read from its readings
    out: dict[str, dict[str, Any]] = {}
    for i, (cand, lab) in enumerate(rows):
        u, opts = cand["members"][0], cand["members"][1:]
        mu = fx.meta[u]
        s = out.setdefault(lab["stratum"], {"rows": 0, **{c: Counter() for c in counters}, "number_match": 0,
                                            "u_twin": 0, "option_twin": 0, "held_rows": 0, "u_negated": 0,
                                            "option_negated": 0, "u_gloss": 0})
        same = [o for o in opts if tid(fx, o) == tid(fx, u)]
        base = lab["readings"]["base"]
        other = [o for o in same if o != base]
        s["rows"] += 1
        s["same_team"][len(same)] += 1
        s["option_kinds"].update(fx.meta[o]["kind"] for o in opts)
        s["u_kind"][mu["kind"]] += 1
        s["number_match"] += any(mu["t"] is not None and fx.meta[o]["t"] == mu["t"] for o in same)
        s["u_twin"] += twin(u, i)
        s["option_twin"] += any(twin(o, i) for o in opts)
        s["held_rows"] += fx.held(*cand["members"])
        s["u_gloss"] += bool(mu.get("gloss"))  # v2 fixtures have no gloss field
        s["u_negated"] += bool(readings[u]["negated"] if u in readings else fx.bind[u]["negated"])
        s["option_negated"] += sum(bool(fx.bind[o]["negated"]) for o in opts)
        s["families"].update(str(fx.meta[o]["family"]) for o in opts)
        s["base_t"][str(fx.meta[base]["t"])] += 1
        for o in other:  # the other same-team options: the alt (or mirror) and the near miss
            s["other_t"][str(fx.meta[o]["t"])] += 1
    return {k: {**v, **{c: dict(sorted(v[c].items())) for c in counters}} for k, v in sorted(out.items())}


def near_threshold(base_kind: str, base_t: int | None, alt_t: int, top: int) -> int:
    """The same-team near miss of a reading row: the threshold one step from the wording's threshold, on the side away from
    the alt. Break (base kind thr, wording threshold base_t, alt base_t - 1): base_t + 1, or base_t - 2 at the top. Top half
    (base kind qual, wording threshold alt_t = M): M + 1, or M - 1 at the top."""
    if base_kind == "thr":
        return base_t + 1 if base_t + 1 <= top else base_t - 2
    return alt_t + 1 if alt_t + 1 <= top else alt_t - 1


def build_readings(fx: Fixture, live: set[str], taken: set[frozenset[str]], rng: random.Random) -> tuple[list, dict]:
    """The reading rows (docs/mentat_hard_contracts.md). Every row has five members: u and four options, shuffled, three of
    them on u's team.
    - Ambiguous rows (u unresolved; both outcomes of each proposition): the base and alt options (the bound outcome of each
      reading's class with u's polarity; the base is the literal reading), the same-team near miss (near_threshold), and one
      other-team distractor of the base's kind from the same league.
    - One decoy proposition per ambiguous proposition, as a complement pair: the two outcomes of a glossed market m1 of a
      paraphrase class (flipped polarity; the phrase with its reading, so it is bound) are the two decoy u's; each row's gold
      option is the same-polarity outcome of another market m2
      of that class (the answer for both readings); the same-team mirror is thr(team, alt threshold) (the ambiguous alt's
      threshold distribution), the same-team near miss is near_threshold, and one same-league distractor completes the row.
      A decoy proposition copies its ambiguous proposition's base kind, base threshold and test-only status (the row held
      flags, by rejection), so the strata match where the world allows.
    The option wording families are equalized last: a repair swaps a decoy's distractor or same-team option (never its u or
    gold) for a same-kind, same-polarity alternative of a family the decoys still lack, keeping the row's test-only status.
    Returns (rows, info); rows are (candidate, label) pairs, shuffled. Ambiguity cues other than u's wording are equalized;
    see reading_stats. decoy_shortfall counts ambiguous propositions without a decoy proposition (0 on generated splits)."""
    read = _readings(fx)
    by_class = _by_class(fx, live)
    by_cm: dict[tuple[str, str], dict[bool, str]] = defaultdict(dict)  # (class, market) -> {negated: outcome}
    for o in fx.ids:
        b = fx.bind[o]
        if b["status"] == "bound" and o in live:
            by_cm[(b["class"], fx.market(o))][bool(b["negated"])] = o
    cm_index: dict[str, list[str]] = defaultdict(list)
    for c, m in by_cm:
        cm_index[c].append(m)
    cls_info: dict[str, tuple[Any, Any, Any]] = {}  # class -> (kind, (league, team), t), from meta
    if fx.meta is not None:  # without meta (a degraded fixture) there are no kinds, teams, thresholds or decoys
        for o in fx.ids:
            b = fx.bind[o]
            if b["status"] == "bound":
                m = fx.meta[o]
                cls_info.setdefault(b["class"], (m["kind"], (m["league"], m["team"]), m["t"]))
    thr_of = {(team, t): c for c, (kind, team, t) in cls_info.items() if kind == "thr"}
    top: dict[Any, int] = {}  # league -> L, its highest threshold
    for kind, (league, _), t in cls_info.values():
        if kind == "thr" and t is not None:
            top[league] = max(top.get(league, 0), t)

    def other_team(ref: str, kind: Any, flag: bool, used: set[str], need: Counter | None = None) -> tuple[str, str] | None:
        """(class, outcome): a bound outcome with the flag of a class of `kind` of another team than ref's, of ref's league
        (any class without meta), its class not in used. With `need` (the wording families still needed), the outcome of a
        sampled candidate whose family is most needed. None when there is none."""
        keys = [k for k in by_class if k[1] == flag and k[0] not in used and (
            not cls_info or (cls_info[k[0]][0] == kind and cls_info[k[0]][1] != cls_info[ref][1]
                             and cls_info[k[0]][1][0] == cls_info[ref][1][0]))]
        if not keys:
            return None
        if need is None:
            key = rng.choice(keys)
            return key[0], rng.choice(by_class[key])
        cands = [(k[0], o) for k in keys for o in by_class[k]]
        return max(cands, key=lambda co: (need[str(fx.meta[co[1]]["family"])], rng.random()))

    rows: list[tuple[dict, dict]] = []
    row_sets: set[frozenset[str]] = set()

    def add_row(members: list[str], stratum: str, gold: dict[str, str]) -> None:
        if len(members) > CHILD_MAX_K:
            raise ValueError(f"reading question {members[0]!r} has {len(members)} members, over the child's MAX_K {CHILD_MAX_K}")
        ms = frozenset(members)
        if len(ms) != len(members) or ms in taken or ms in row_sets:
            raise ValueError(f"reading question {members[0]!r} repeats a member")
        row_sets.add(ms)
        rows.append(({"op": "decide_group", "members": members, "source": "reading"},
                     {"op": "decide_group", "stratum": stratum, "readings": gold, "label": "reading", "k": None,
                      "held_out": fx.held(*members), "missing": None, "order": None}))

    # 1. the ambiguous rows
    amb: dict[str, list[str]] = {}  # unresolved outcome -> its row's members
    for u in sorted(read):
        info = read[u]
        flag = info["negated"]
        if u not in live or not cls_info or any(not by_class.get((info[r], flag)) for r in READINGS):
            continue
        if info["base"] == info["alt"]:
            raise ValueError(f"{u}: both readings name the class {info['base']}")
        base_kind, base_t, alt_t = cls_info[info["base"]][0], cls_info[info["base"]][2], cls_info[info["alt"]][2]
        team = cls_info[info["base"]][1]
        near = thr_of.get((team, near_threshold(base_kind, base_t, alt_t, top[team[0]])))
        if near is None or near in (info["base"], info["alt"]) or not by_class.get((near, flag)):  # no distinct near miss
            continue
        d = other_team(info["base"], base_kind, flag, {info["base"], info["alt"], near})
        if d is None:
            continue
        gold = {r: rng.choice(by_class[(info[r], flag)]) for r in READINGS}
        opts = [gold["base"], gold["alt"], rng.choice(by_class[(near, flag)]), d[1]]
        rng.shuffle(opts)
        amb[u] = [u, *opts]
        add_row(amb[u], "reading", gold)

    # the wording families of the ambiguous options are the target for the decoys' options (families are equalized in
    # distribution: the distractors are chosen from the families still needed)
    need = Counter(str(fx.meta[o]["family"]) for members in amb.values() for o in members[1:]) if fx.meta else Counter()
    # 2. one decoy proposition per ambiguous proposition, as a complement pair (no decoys without meta)
    props: dict[str, list[str]] = defaultdict(list)
    for u in amb:
        props[fx.market(u)].append(u)
    paraphrase = sorted({c for c, _ in by_cm if len(cm_index[c]) >= 2})
    used_m1: set[tuple[str, str]] = set()
    prop_count = decoy_props = retries = fallback_u = 0
    for mk in sorted(props) if fx.meta is not None else []:
        if len(props[mk]) != 2:
            continue
        prop_count += 1
        o_t = next(u for u in props[mk] if read[u]["negated"])  # the outcome whose negated flag is True
        o_f = next(u for u in props[mk] if not read[u]["negated"])
        info = read[o_t]
        kind, base_t, alt_t = cls_info[info["base"]][0], cls_info[info["base"]][2], cls_info[info["alt"]][2]
        prop_top = top[cls_info[info["base"]][1][0]]  # the decoy's near miss follows its ambiguous partner's league
        held = fx.meta[o_t]["test_only"]
        targets = {True: fx.held(*amb[o_t]), False: fx.held(*amb[o_f])}
        found = None  # (class, m1, m2, decoy, overspend): the feasible decoy overspending its families least
        fallback = False  # True when the decoy's u must differ in test-only status from the ambiguous u (no strict match)
        cands = [c for c in paraphrase if cls_info[c][0] == kind and (kind != "thr" or cls_info[c][2] == base_t)]
        rng.shuffle(cands)
        for strict in (True, False):
            feasible = 0
            for cls in cands:
                team = cls_info[cls][1]
                mirror = thr_of.get((team, alt_t))
                near = thr_of.get((team, near_threshold(kind, base_t, alt_t, prop_top)))
                if mirror is None or near is None or cls in (mirror, near):  # (a synthetic alt may share the base's threshold)
                    continue
                m1s = [m for m in cm_index[cls] if (cls, m) not in used_m1 and fx.meta[by_cm[(cls, m)][True]].get("gloss")
                       and (not strict or fx.meta[by_cm[(cls, m)][True]]["test_only"] == held)]  # u: a glossed market
                rng.shuffle(m1s)
                for m1 in m1s:
                    m2s = [m for m in cm_index[cls] if m != m1]
                    rng.shuffle(m2s)
                    for m2 in m2s:
                        decoy = _decoy_rows(fx, by_class, by_cm, cls, m1, m2, mirror, near, kind, targets, rng, other_team, need)
                        if decoy is None:
                            retries += 1
                            continue
                        spent = decoy[1]
                        over = sum(max(0, spent[f] - max(need[f], 0)) for f in spent)  # slots beyond the families still needed
                        feasible += 1
                        if found is None or over < found[4]:
                            found = (cls, m1, m2, decoy, over)
                        if over == 0 or feasible >= DECOY_CANDIDATES:
                            break
                    if found and (found[4] == 0 or feasible >= DECOY_CANDIDATES):
                        break
                if found and (found[4] == 0 or feasible >= DECOY_CANDIDATES):
                    break
            if found is not None:
                fallback = not strict
                break
        if found is None:
            continue
        cls, m1, m2, (decoy, spent), _ = found
        used_m1.add((cls, m1))
        fallback_u += fallback
        need.subtract(spent)
        for flag in (True, False):
            members, gold_o = decoy[flag]
            add_row(members, "reading_decoy", {r: gold_o for r in READINGS})
        decoy_props += 1
    # 3. repair: until the option families of the two strata match, swap an option of a row (never its u, its readings
    # or the decoy's gold) for a same-kind, same-polarity alternative (another market of its class for a same-team option,
    # a same-league distractor of another team), keeping the row's test-only status and its member set unique
    if fx.meta is not None:
        fam = lambda o: str(fx.meta[o]["family"])  # noqa: E731
        label_of = {id(c): lab for c, lab in rows}
        amb_rows = [c for c, lab in rows if lab["stratum"] == "reading"]
        dec_rows = [c for c, lab in rows if lab["stratum"] == "reading_decoy"]

        def counts(rs: list[dict]) -> Counter:
            return Counter(fam(o) for c in rs for o in c["members"][1:])

        def swap(c: dict, keep: set[str], over: set[str], under: set[str]) -> bool:
            u_team = tid(fx, c["members"][0])
            for idx in range(1, 5):
                o = c["members"][idx]
                if o in keep or fam(o) not in over:
                    continue
                flag = bool(fx.bind[o]["negated"])
                row_classes = {fx.bind[m]["class"] for m in c["members"]}
                if tid(fx, o) == u_team:  # a same-team option: another market of the same class and polarity
                    pool = iter(by_class.get((fx.bind[o]["class"], flag), []))
                else:  # a distractor: another class of the same kind, of another team in the same league
                    pool = (x for k in by_class if k[1] == flag and k[0] not in row_classes
                            and cls_info[k[0]][0] == cls_info[fx.bind[o]["class"]][0]
                            and cls_info[k[0]][1] != u_team and cls_info[k[0]][1][0] == u_team[0] for x in by_class[k])
                for x in pool:
                    if fam(x) not in under or x == o:
                        continue
                    new = c["members"][:idx] + [x] + c["members"][idx + 1:]
                    if fx.held(*new) != fx.held(*c["members"]) or frozenset(new) in taken or frozenset(new) in row_sets:
                        continue
                    row_sets.discard(frozenset(c["members"]))
                    row_sets.add(frozenset(new))
                    c["members"] = new
                    return True
            return False

        for _ in range(8 * (len(amb_rows) + len(dec_rows))):
            have_a, have_d = counts(amb_rows), counts(dec_rows)
            fams = set(have_a) | set(have_d)
            moved = False
            for rs, mine, other in ((amb_rows, have_a, have_d), (dec_rows, have_d, have_a)):
                over = {f for f in fams if mine[f] > other[f]}
                under = {f for f in fams if mine[f] < other[f]}
                if not over or not under:
                    continue
                for c in rs:
                    lab = label_of[id(c)]
                    keep = set(lab["readings"].values())  # the readings' options (amb) or the gold (decoy)
                    if swap(c, keep, over, under):
                        moved = True
                        break
                if moved:
                    break
            if not moved:
                break
    rng.shuffle(rows)
    unresolved = [o for o in fx.ids if fx.bind[o]["status"] == "unresolved"]
    stats = reading_stats(fx, rows) if fx.meta is not None else {}
    return rows, {"ambiguous": len(amb), "decoys": sum(lab["stratum"] == "reading_decoy" for _, lab in rows),
                  "decoy_propositions": decoy_props, "decoy_shortfall": prop_count - decoy_props,
                  "decoy_retries": retries, "decoy_u_held_fallback": fallback_u, "family_residual": _family_residual(fx, rows),
                  "unresolved": len(unresolved),
                  "uncovered": len(unresolved) - len(amb),
                  "complement_incomplete": sum(read[u]["partner"] not in amb for u in amb),
                  "paraphrase_classes": len(paraphrase), "stats": stats}


def _family_residual(fx: Fixture, rows: list[tuple[dict, dict]]) -> int:
    """Option slots of the decoy rows whose wording family the ambiguous rows' options lack, from the final rows (0 when the
    families match; 0 without meta, which has no families)."""
    if fx.meta is None:
        return 0
    target = Counter(str(fx.meta[o]["family"]) for c, lab in rows if lab["stratum"] == "reading" for o in c["members"][1:])
    have = Counter(str(fx.meta[o]["family"]) for c, lab in rows if lab["stratum"] == "reading_decoy" for o in c["members"][1:])
    return sum(max(0, target[f] - have[f]) for f in target)


def _decoy_rows(fx: Fixture, by_class: dict, by_cm: dict, cls: str, m1: str, m2: str, mirror: str, near: str, kind: Any,
                targets: dict[bool, bool], rng: random.Random, other_team: Any,
                need: Counter) -> tuple[dict[bool, tuple[list[str], str]], Counter] | None:
    """The two rows of a decoy proposition on class cls: u = m1's outcome with each polarity, the gold = m2's outcome with the
    same polarity, the same-team mirror and near miss of that polarity, and one same-league distractor of kind `kind`, chosen
    to spend the wording families `need` still lacks. Each row's test-only status must equal its ambiguous partner's
    (targets), by rejection. Returns (rows by polarity, the families spent), or None when a row cannot be made."""
    rows: dict[bool, tuple[list[str], str]] = {}
    spent: Counter = Counter()
    for flag in (True, False):
        for _ in range(DECOY_ROW_ATTEMPTS):
            if not by_class.get((mirror, flag)) or not by_class.get((near, flag)):
                return None
            gold = by_cm[(cls, m2)][flag]
            left = need - spent
            left.subtract([str(fx.meta[gold]["family"])])
            # the same-team options are the markets of their classes whose wording family is still needed (random ties)
            mir = max(by_class[(mirror, flag)], key=lambda o: (left[str(fx.meta[o]["family"])], rng.random()))
            left.subtract([str(fx.meta[mir]["family"])])
            nm = max(by_class[(near, flag)], key=lambda o: (left[str(fx.meta[o]["family"])], rng.random()))
            left.subtract([str(fx.meta[nm]["family"])])
            d = other_team(cls, kind, flag, {cls, mirror, near}, left)
            if d is None:
                return None
            opts = [gold, mir, nm, d[1]]
            rng.shuffle(opts)
            members = [by_cm[(cls, m1)][flag], *opts]
            if fx.held(*members) == targets[flag]:
                rows[flag] = (members, gold)
                spent.update(str(fx.meta[o]["family"]) for o in opts)
                break
        else:
            return None
    return rows, spent


STOP = frozenset("will the a an of in to for be is it by at on its with from and or than not no".split())


def _set_overlap(fx: Fixture, members: list[str]) -> float:
    """Mean pairwise word Jaccard of the members' questions."""
    pairs = list(combinations(members, 2))
    return sum(_jaccard(_words(fx.text[a]), _words(fx.text[b])) for a, b in pairs) / len(pairs)


def group_baselines(fx: Fixture, rows: list) -> dict[str, Any]:
    """Real (label not none) vs decoy, predicted from public fields only: all members share the event title; mean word
    overlap in the top half; all members share a specific word (not a stop word, not a word of most questions of their
    league, e.g. its name): the group or scope phrase. Accuracies near the real share mean the decoys are not separable
    by these cues."""
    if not rows:
        return {}
    league_words: dict[Any, Counter] = defaultdict(Counter)
    league_n: Counter = Counter()
    for o in fx.ids:
        league_words[fx.league[o]].update(_words(fx.text[o]))
        league_n[fx.league[o]] += 1
    truth = [lab["label"] != "none" for _, lab in rows]
    overlap = [_set_overlap(fx, q["members"]) for q, _ in rows]
    cut = sorted(overlap)[len(overlap) // 2]

    def specific(members: list[str]) -> bool:
        lg = fx.league[members[0]]
        common = {w for w, c in league_words[lg].items() if c >= league_n[lg] / 2}
        shared = frozenset.intersection(*(_words(fx.text[m]) for m in members)) - STOP - common
        return bool(shared)

    def acc(pred: list[bool]) -> float:
        return round(sum(p == t for p, t in zip(pred, truth)) / len(truth), 4)

    return {"real_share": round(sum(truth) / len(truth), 4),
            "same_event_title_acc": acc([len({fx.title[m] for m in q["members"]}) == 1 for q, _ in rows]),
            "top_overlap_acc": acc([o >= cut for o in overlap]),
            "shared_specific_word_acc": acc([specific(q["members"]) for q, _ in rows])}


# ---------------------------------------------------------------- build

def _league_blocked(rows: list, league_of: Any, rng: random.Random) -> list:
    """The rows in league blocks: the leagues in a seeded order, each block's rows shuffled, so consecutive requests share a
    league's rule card (the corpus `context` of its outcomes)."""
    blocks: dict[Any, list] = defaultdict(list)
    for r in rows:
        blocks[league_of(r)].append(r)
    keys = sorted(blocks, key=str)
    rng.shuffle(keys)
    out: list = []
    for k in keys:
        rng.shuffle(blocks[k])
        out += blocks[k]
    return out


def build(fixture: str | Path, relations: int = 6_000, groups: int = 1_000, seed: int | None = None) -> dict[str, Any]:
    """Writes candidates.jsonl and gold/suite_labels.jsonl. `uncovered` counts the generator rows with no suite row: unscoped
    implies and two-argument atmost-1 edges, unscoped cardinality constraints, and unresolved outcomes without a reading row
    (0 on a generated split; generate refuses otherwise). constants_excluded is the constant outcome count (0 on a split)."""
    fx = Fixture(fixture)
    if ab.Checker(fx.th, ab.CONFLICT_BUDGET).check_base():  # raises BaseInconsistent on an unsatisfiable base
        raise ValueError("audit_brute could not decide the base consistency of every component")
    eng = _Engine(fx)
    const = constants(fx, eng)
    live = [o for o in fx.ids if o not in const]
    rows = []
    for o in fx.ids:
        b = fx.bind[o]
        rows.append(({"op": "decide_binding", "id": o},
                     {"label": b["status"], "k": None,
                      "missing": list(b["missing"]) if b["status"] == "unresolved" else None, "order": None,
                      "held_out": fx.held(o), "stratum": "real"}))
    _rng(fx, seed, "bindings").shuffle(rows)  # Yes and No of a market are not adjacent in the numbering
    rel_rows, rel_info = build_relations(fx, eng, live, relations, _rng(fx, seed, "relations"))
    grp_rows, grp_info = build_groups(fx, eng, set(live), groups, _rng(fx, seed, "groups"))
    read_rows, read_info = build_readings(fx, set(live), {frozenset(q["members"]) for q, _ in grp_rows},
                                          _rng(fx, seed, "readings"))
    # league blocks: consecutive requests share a league's rule card (cross-league pairs sit in side a's league)
    order = _rng(fx, seed, "order")
    rel_rows = _league_blocked(rel_rows, lambda r: fx.league[r[0]["a"]], order)
    grp_rows = _league_blocked(grp_rows, lambda r: fx.league[r[0]["members"][0]], order)
    read_rows = _league_blocked(read_rows, lambda r: fx.league[r[0]["members"][0]], order)
    uncovered =rel_info["uncovered"] + grp_info["uncovered"] + read_info["uncovered"]
    cands, labels = [], []
    counters: Counter = Counter()
    for (q, lab), prefix in [(r, "b") for r in rows] + [(r, "r") for r in rel_rows] + [(r, "g") for r in grp_rows + read_rows]:
        counters[prefix] += 1
        qid = f"{prefix}{counters[prefix]:05d}"
        cands.append({"qid": qid, **q})
        labels.append({"qid": qid, "op": q["op"], **lab})
    ir.write_jsonl(fx.root / "candidates.jsonl", cands)
    ir.write_jsonl(fx.gold / "suite_labels.jsonl", labels)
    return {"constants_excluded": len(const), "uncovered": uncovered, "relations": rel_info, "groups": grp_info,
            "readings": read_info, "engine_seconds": round(eng.seconds, 2), "engine_queries": eng.queries,
            "balance": balance(labels)}


def balance(labels: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for op in ("decide_binding", "decide_relation", "decide_group"):
        rows = [r for r in labels if r["op"] == op]
        out[op] = {"n": len(rows),
                   "label": dict(sorted(Counter(r["label"] for r in rows).items())),
                   "stratum": dict(sorted(Counter(r["stratum"] for r in rows).items())),
                   "held_out": dict(sorted(Counter(str(r["held_out"]).lower() for r in rows).items())),
                   "label_held_out": dict(sorted(Counter(f"{r['label']}|{str(r['held_out']).lower()}"
                                                         for r in rows).items()))}
        if op == "decide_relation":  # gold-side depth tags (_bucket): none for NONE
            out[op]["depth"] = dict(sorted(Counter(_bucket(r["depth"]) for r in rows).items()))
            out[op]["label_depth"] = dict(sorted(Counter(f"{r['label']}|{_bucket(r['depth'])}" for r in rows).items()))
    return out


# ---------------------------------------------------------------- audit (independent of the engine)

def depth_check(root: Path, cands: dict[str, dict], labels: list[dict]) -> dict[str, Any]:
    """Every relation row's depth against audit_brute.DepthGraph, an independent 0-1 breadth-first search over the theory
    JSON, the bindings and the meta rows (it shares no code with Depths). Every row is checked, sampled or not."""
    theory = ab._json(root / "gold" / "theory.json")
    bindings = ab._jsonl(root / "gold" / "bindings.jsonl")
    meta_path = root / "gold" / "meta.jsonl"
    graph = ab.DepthGraph(theory, bindings, ab._jsonl(meta_path) if meta_path.is_file() else None)
    checked, mismatches = 0, []
    for row in labels:
        if row["op"] != "decide_relation":
            continue
        q = cands[row["qid"]]
        want = graph.depth(q["a"], q["b"], row["label"])
        checked += 1
        if want != row.get("depth"):
            mismatches.append({"qid": row["qid"], "label": row["label"], "depth": row.get("depth"), "audit": want})
    return {"checked": checked, "mismatches": mismatches}


def audit(fixture: str | Path, sample: int = AUDIT_MIN_CELL, seed: int = 0) -> dict[str, Any]:
    """Re-derives a sample of the labels with audit_brute (exhaustive or DPLL, no engine code), reading the gold files
    itself. The sample is stratified by (op, label, stratum): max(sample, AUDIT_MIN_CELL) per cell, or all of a smaller
    cell. Group terms are recomputed from the exact bounds; each ordered_chain `order` is a valid chain. A reading row is
    checked for five distinct members, distinct options, bound options, and for decoys (one option for both readings)
    that the option is equivalent to the outcome in every interpretation. With gold/world.json and gold/meta.jsonl
    (generated splits) every label is also checked against the world (world_check)."""
    root = Path(fixture)
    cands = {c["qid"]: c for c in ab._jsonl(root / "candidates.jsonl")}
    labels = ab._jsonl(root / "gold" / "suite_labels.jsonl")
    th = ab.Theory(ab._json(root / "gold" / "theory.json"), ab._jsonl(root / "gold" / "bindings.jsonl"), [])
    ck = ab.Checker(th, AUDIT_CONFLICT_BUDGET)  # the fixture audit's budget: a few relation checks need more than the default

    def valid(*exprs: dict[str, Any]) -> bool:  # the conjunction is unsatisfiable in every interpretation
        return not any(ck.satisfiable(ck.support_roots(list(exprs)), list(exprs)))

    def neg(oid: str) -> dict[str, Any]:
        return {"op": "not", "a": _var(oid)}

    def equal_in(a: str, b: str, i: int) -> bool:  # a and b have the same truth value in interpretation i
        return all(not ck.satisfiable(ck.support_roots(e), e)[i] for e in ([_var(a), neg(b)], [neg(a), _var(b)]))

    def equal_everywhere(a: str, b: str) -> bool:
        return all(equal_in(a, b, i) for i in range(len(th.interps)))

    rng = random.Random(f"hard_suite:audit:{seed}")
    cells = defaultdict(list)
    for row in labels:
        cells[(row["op"], str(row["label"]), row["stratum"])].append(row)
    report: dict[str, Any] = {"checked": Counter(), "cells": {}, "mismatches": []}
    constant: dict[str, bool] = {}
    t0 = time.perf_counter()
    for cell, rows in sorted(cells.items()):
        # every reading row is audited (the generation gate depends on it); other cells are sampled
        take = len(rows) if cell[2].startswith("reading") else min(max(sample, AUDIT_MIN_CELL), len(rows))
        rows = rng.sample(rows, take)
        report["checked"][cell[0]] += len(rows)
        report["cells"]["|".join(cell)] = len(rows)
        for row in rows:
            q, op = cands[row["qid"]], row["op"]
            bad = []
            ours: Any = None
            theirs: Any = None
            if op == "decide_binding":
                b = th.bind_by_id[q["id"]]
                ours = (b["status"], b["missing"] if b["status"] == "unresolved" else None)
                theirs = (row["label"], row["missing"])
            elif op == "decide_relation":
                a, b = q["a"], q["b"]
                ours = relation_label(valid(_var(a), neg(b)), valid(neg(a), _var(b)), valid(_var(a), _var(b)))
                theirs = row["label"]
            elif "readings" in row:  # a reading question: each option checked in each interpretation (u = option under r)
                members = q["members"]
                u, opts = members[0], members[1:]
                gold = [row["readings"][r] for r in READINGS]
                ambiguous = row["stratum"] == "reading"
                if len(set(members)) != 5 or row["label"] != "reading" or \
                        (th.bind_by_id[u]["status"] == "unresolved") != ambiguous:
                    bad.append("reading question: five distinct members; u is unresolved exactly when it is ambiguous")
                if any(g not in opts for g in gold) or any(th.bind_by_id[g]["status"] != "bound" for g in gold):
                    bad.append("reading options are not bound options of the question")
                if ambiguous:
                    if gold[0] == gold[1]:
                        bad.append("reading: the two readings name one option")
                    for r, g, other in zip(READINGS, gold, gold[::-1]):
                        i = th.interps.index(r)
                        if not equal_in(u, g, i):
                            bad.append(f"u is not equivalent to its {r} option under {r}")
                        if equal_in(u, other, i):  # the reading picks one option: the other one is not u under r
                            bad.append(f"the other reading's option is equivalent to u under {r}")
                elif gold[0] != gold[1] or not equal_everywhere(u, gold[0]):
                    bad.append("decoy: one option for both readings, equivalent to u in every interpretation")
                for o in opts:
                    if o not in gold and any(equal_in(u, o, i) for i in range(len(th.interps))):
                        bad.append(f"distractor {o} is equivalent to u in some interpretation")
            else:
                ms, n = q["members"], len(q["members"])
                k_lo = max((k for k in range(1, n + 1) if valid(_card("atmost", k - 1, ms))), default=0)
                k_hi = min((k for k in range(1, n) if valid(_card("atleast", k + 1, ms))), default=n)
                chain = all(valid(_var(x), neg(y)) or valid(_var(y), neg(x)) for x, y in combinations(ms, 2))
                ours = group_terms(k_lo, k_hi, n, chain)
                theirs = [tuple(t) for t in row["terms"]]
                order = row.get("order")
                if (order is not None) != any(kind == "ordered_chain" for kind, _ in theirs):
                    bad.append("order present iff ordered_chain")
                elif order is not None and (sorted(order) != sorted(ms) or not all(
                        valid(_var(x), neg(y)) for x, y in zip(order, order[1:]))):
                    bad.append("order is not a valid chain over the members")
                elif order is not None and any(valid(_var(y), neg(x)) for x, y in zip(order, order[1:])):
                    bad.append("order is not unique: two consecutive members are equivalent")
                if (row["label"], row["k"]) != (theirs[0] if theirs else ("none", None)):
                    bad.append("label and k are not terms[0]")
            for oid in [q[k] for k in ("a", "b") if k in q] + q.get("members", []):
                if oid not in constant:
                    constant[oid] = valid(_var(oid)) or valid(neg(oid))
                if constant[oid]:
                    bad.append(f"constant outcome {oid}")
            if ours is not None and ours != theirs:
                bad.append("label")
            if bad:
                report["mismatches"].append({"qid": row["qid"], "suite": theirs, "audit": ours, "problems": bad})
    if (root / "gold" / "world.json").is_file() and (root / "gold" / "meta.jsonl").is_file():
        report["world"] = world_check(root, cands, labels, th.bind_by_id)
    report["depth"] = depth_check(root, cands, labels)
    report["seconds"] = round(time.perf_counter() - t0, 2)
    return report


# ---------------------------------------------------------------- world.json cross-check (generator's declared rules)

_REL_FACTS = {"EQUIV": (True, True, False), "IMPLIES": (True, False, False), "IMPLIED_BY": (False, True, False),
              "EXCLUDES": (False, False, True), "NONE": (False, False, False)}
_PRECEDENCE = ("exactly_one", "ordered_chain", "at_least_k", "at_most_k", "at_most_one", "none")


def _declared_edges(lg: dict[str, Any]) -> list[tuple[tuple, tuple]]:
    """Literal implications (key, polarity) => (key, polarity) the league's declared rules state directly, keys being
    (kind, team, t, subgroup) as in gold/meta.jsonl; exclusions are a => not b. Contrapositives are added by the caller."""
    teams, L = lg["teams"], lg["L"]
    T = lambda x, t: (("thr", x, t, None), True)  # noqa: E731
    one = lambda kind, x, g=None: ((kind, x, None, g), True)  # noqa: E731
    neg = lambda lit: (lit[0], not lit[1])  # noqa: E731
    imp, excl = [], []
    for r in lg["rules"]:
        if r["type"] == "ladder":
            imp += [(T(x, t + 1), T(x, t)) for x in teams for t in range(1, L)]
        elif r["type"] == "qualify":
            imp += [(one("qual", x), T(x, r["min_points"])) for x in teams]
        elif r["type"] == "placement":
            imp += [(one(k, x), one("qual", x)) for x in teams for k in ("win", "runner")]
            imp += [(one("win", x), one("runner", y)) for x, y in r["conditionals"]]
            excl += [(one("win", x), one("runner", x)) for x in teams]
            excl += [(one(k, x), one(k, y)) for k in ("win", "runner") for x, y in combinations(teams, 2)]
        elif r["type"] == "outscores":
            imp += [(T(y, t), T(x, t + 1)) for x, y in r["pairs"] for t in range(1, L)]
        elif r["type"] == "chain":
            ch = r["teams"]
            imp += [(T(y, t), T(x, t)) for x, y in zip(ch, ch[1:]) for t in range(1, L + 1)]
            imp += [(one("win", x), T(x, r["a"])) for x in teams]
        elif r["type"] == "overlap":
            groups = {g["id"]: g["members"] for g in lg["subgroups"]}
            for gid in r["groups"]:
                excl += [(one("seed", x, gid), one("seed", y, gid)) for x, y in combinations(groups[gid], 2)]
                imp += [(one("seed", x, gid), T(x, r["seed_min_points"][gid])) for x in groups[gid]]
            g1, g2 = r["groups"]
            excl += [(one("seed", x, g1), one("seed", x, g2)) for x in r["shared"]]
    return imp + [(a, neg(b)) for a, b in excl]


def world_check(root: Path, cands: dict[str, dict], labels: list[dict], bind: dict[str, dict]) -> dict[str, Any]:
    """Every label against the generator's gold/world.json, on outcomes bound to a class: (a) the label holds in the gold
    world (which satisfies every constraint); (b) every consequence of the declared rules is in the label: declared a => b,
    b => a or exclusion (transitively, with contrapositives) must be in a relation's label; a group's declared bounds
    (exactly one for the cup and subgroup sets, at least k for the qualify set and the counting rule's sets, at most k
    for the counting rule's other set, at most one for pairwise exclusive sets) must contain the label's bounds; a declared
    chain is an ordered_chain term in the declared order. A reading row (an unresolved proposition, rule 8) is checked
    against the world's props: each option is the reading's class, with the polarity of u (the proposition's negated flag
    is the Yes outcome's; a No outcome flips it); a decoy's option is a same-class outcome of another market."""
    world = ab._json(root / "gold" / "world.json")
    meta = {m["id"]: m for m in ab._jsonl(root / "gold" / "meta.jsonl")}
    yes = {c["id"]: c["label"] == "Yes" for c in ab._jsonl(root / "corpus.jsonl")}
    leagues = {lg["id"]: lg for lg in world["leagues"]}
    props = {p["market"]: p for lg in world["leagues"] for r in lg["rules"] if r["rule"] == 8 for p in r["props"]}
    reach: dict[Any, dict[tuple, set[tuple]]] = {}
    for lid, lg in leagues.items():
        graph: dict[tuple, set[tuple]] = defaultdict(set)
        for a, b in _declared_edges(lg):
            graph[a].add(b)
            graph[(b[0], not b[1])].add((a[0], not a[1]))
        closure = {}
        for start in list(graph):
            seen, stack = {start}, [start]
            while stack:
                for nxt in graph[stack.pop()]:
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            closure[start] = seen
        reach[lid] = closure

    def lit(oid: str) -> tuple | None:
        b, m = bind[oid], meta[oid]
        if b["status"] != "bound":
            return None
        return m["league"], (m["kind"], m["team"], m["t"], m["subgroup"]), not b["negated"]

    def gold(lg: dict, key: tuple, pol: bool) -> bool:
        kind, x, t, g = key
        v = (lg["scores"][x] >= t if kind == "thr" else lg["winner"] == x if kind == "win" else lg["runner_up"] == x
             if kind == "runner" else x in lg["qualifiers"] if kind == "qual" else lg["seeds"].get(g) == x)
        return v == pol

    def implies(lid: Any, a: tuple, b: tuple) -> bool:
        return a == b or b in reach[lid].get(a, ())

    def tid(x: str) -> tuple[Any, Any]:
        return meta[x]["league"], meta[x]["team"]

    # the top-half thresholds M of the world (a decoy's class may come from another league, so its mirror's M is any league's)
    top_half_ms = {p["readings"]["alt"]["t"] for lg in world["leagues"] for r in lg["rules"] if r["rule"] == 8
                   for p in r["props"] if p["kind"] == "tophalf"}
    tops = {lg["L"] for lg in world["leagues"]}

    def reading_problems(row: dict, q: dict) -> list[str]:
        u, opts = q["members"][0], q["members"][1:]
        bad = []
        mu = meta[u]
        top = leagues[mu["league"]]["L"]
        same = [o for o in opts if tid(o) == tid(u)]  # the three options on u's team
        if row["stratum"] == "reading":
            p = props.get(mu["market"])
            if p is None or bind[u]["status"] != "unresolved":
                return ["the unresolved outcome is not a proposition of the world"]
            flag = p["negated"] == yes[u]  # the negated flag of the option that u means under each reading
            for r in READINGS:
                o, w = row["readings"][r], p["readings"][r]
                same_class = o in opts and bind[o]["status"] == "bound" and bind[o]["class"] == w["class"]
                if not same_class or bind[o]["negated"] != flag or \
                        (meta[o]["kind"], meta[o]["team"], meta[o]["t"], meta[o]["subgroup"]) != \
                        (w["kind"], w["team"], w["t"], w["subgroup"]):
                    bad.append(f"{r}: the option is not the reading's class with the proposition's polarity")
            near = [o for o in same if o not in row["readings"].values()]
            if len(same) != 3 or len(set(row["readings"].values())) != 2 or len(near) != 1:
                bad.append("the options on u's team are not the two readings and one near miss")
            elif meta[near[0]]["kind"] != "thr" or meta[near[0]]["t"] != near_threshold(
                    p["readings"]["base"]["kind"], p["readings"]["base"]["t"], p["readings"]["alt"]["t"], top):
                bad.append("the near miss is not the threshold one step past the wording")
            if mu["t"] is not None and meta[row["readings"]["base"]]["t"] != mu["t"]:
                bad.append("the literal base is not the threshold of u's wording")
            if mu["t"] is not None and abs(meta[row["readings"]["alt"]]["t"] - mu["t"]) != 1:
                bad.append("the alt threshold is not next to the wording's threshold")
        else:
            o = row["readings"]["base"]
            if o not in opts or row["readings"]["alt"] != o or bind[u]["status"] != "bound" or \
                    bind[o]["class"] != bind[u]["class"] or bind[o]["negated"] != bind[u]["negated"] or \
                    meta[o]["market"] == mu["market"]:
                bad.append("decoy: the option is not a same-class outcome of another market")
            others = [x for x in same if x != o]
            if len(same) != 3 or len(others) != 2 or any(bind[x]["class"] == bind[o]["class"] for x in others):
                bad.append("decoy: three options on u's team: the gold option and two other classes")
            elif any(meta[x]["kind"] != "thr" for x in others):
                bad.append("decoy: the same-team options are not thresholds")
            else:
                ts = [meta[x]["t"] for x in others]  # the near miss follows the partner proposition's league: any league's top
                if mu["t"] is not None:  # break-type: the mirror is the alt (t - 1), the near miss one step past the wording
                    ok = any(sorted(ts) == sorted([mu["t"] - 1, near_threshold("thr", mu["t"], mu["t"] - 1, top_l)]) for top_l in tops)
                else:  # top-half-type: the mirror is a top-half M of the world, the near miss one step from it
                    ok = any(m in top_half_ms and sorted(ts) == sorted([m, near_threshold("qual", None, m, top_l)])
                             for m in ts for top_l in tops)
                if not ok:
                    bad.append("decoy: the same-team options are not the mirror and the near miss of the reading")
        return bad

    out: dict[str, Any] = {"relations_checked": 0, "groups_checked": 0, "readings_checked": 0, "disagreements": []}
    for row in labels:
        q = cands[row["qid"]]
        if row["op"] == "decide_binding":
            continue
        if "readings" in row:
            out["readings_checked"] += 1
            problems = reading_problems(row, q)
            if problems:
                out["disagreements"].append({"qid": row["qid"], "label": row["label"], "k": None, "problems": problems})
            continue
        ids = [q["a"], q["b"]] if row["op"] == "decide_relation" else q["members"]
        lits = [lit(o) for o in ids]
        if None in lits or len({x[0] for x in lits}) != 1:
            continue  # ambiguous outcome, or a cross-league pair: no declared rule spans leagues
        lid, lg = lits[0][0], leagues[lits[0][0]]
        nodes = [(x[1], x[2]) for x in lits]
        vals = [gold(lg, *n) for n in nodes]
        problems = []
        if row["op"] == "decide_relation":
            out["relations_checked"] += 1
            (a, va), (b, vb) = zip(nodes, vals)
            i, j, x = _REL_FACTS[row["label"]]
            if (i and va and not vb) or (j and vb and not va) or (x and va and vb):
                problems.append("label fails in the gold world")
            declared = (implies(lid, a, b), implies(lid, b, a), implies(lid, a, (b[0], not b[1])))
            if any(d and not got for d, got in zip(declared, (i, j, x))):
                problems.append(f"declared (implies, implied_by, excludes) {declared} not in the label")
        else:
            out["groups_checked"] += 1
            terms = [tuple(t) for t in row["terms"]]
            n, true = len(nodes), sum(vals)
            lo = max([k for kd, k in terms if kd == "at_least_k"] + [1 for kd, _ in terms if kd == "exactly_one"] + [0])
            hi = min([k for kd, k in terms if kd == "at_most_k"]
                     + [1 for kd, _ in terms if kd in ("exactly_one", "at_most_one")] + [n])
            if not lo <= true <= hi:
                problems.append("label fails in the gold world")
            chain_t = any(kd == "ordered_chain" for kd, _ in terms)
            if chain_t and not all(gold(lg, *nodes[ids.index(u)]) <= gold(lg, *nodes[ids.index(v)])
                                   for u, v in zip(row["order"], row["order"][1:])):
                problems.append("chain order fails in the gold world")
            keys, pols = {nd[0] for nd in nodes}, {nd[1] for nd in nodes}
            kinds, teams = {kk[0] for kk in keys}, {kk[1] for kk in keys}
            full = pols == {True} and len(keys) == n and teams == set(lg["teams"]) and len(kinds) == 1
            subgroups = {g["id"]: set(g["members"]) for g in lg["subgroups"]}
            dlo, dhi = 0, n  # the bounds the declared rules state for this set
            if full and kinds <= {"win", "runner"}:
                dlo, dhi = 1, 1
            elif pols == {True} and kinds == {"seed"} and any(
                    {kk[3] for kk in keys} == {gid} and teams == mem for gid, mem in subgroups.items()):
                dlo, dhi = 1, 1
            elif full and kinds == {"qual"}:
                k = next(r["k"] for r in lg["rules"] if r["type"] == "qualify")
                dlo, dhi = k, k
            for r in lg["rules"]:
                if r["type"] == "count" and full and kinds == {"thr"}:
                    ts = {kk[2] for kk in keys}
                    if ts == {r["atleast"]["t"]}:
                        dlo = max(dlo, r["atleast"]["k"])
                    if ts == {r["atmost"]["t"]}:
                        dhi = min(dhi, r["atmost"]["k"])
            pairs = list(combinations(range(n), 2))
            if pairs and all(implies(lid, nodes[u], (nodes[v][0], not nodes[v][1])) for u, v in pairs):
                dhi = min(dhi, 1)  # pairwise exclusive: at most one
            if lo < dlo or hi > dhi:
                problems.append(f"declared bounds [{dlo}, {dhi}] are not in the label")
            if pairs and all(implies(lid, nodes[u], nodes[v]) or implies(lid, nodes[v], nodes[u]) for u, v in pairs):
                pos = {o: ids.index(o) for o in ids}
                backwards = chain_t and any(  # orders are unique (no equivalent members)
                    implies(lid, nodes[pos[v]], nodes[pos[u]]) and not implies(lid, nodes[pos[u]], nodes[pos[v]])
                    for u, v in combinations(row["order"] or [], 2))
                if not chain_t or backwards:
                    problems.append("declared chain: ordered_chain in the declared order")
        if problems:
            out["disagreements"].append({"qid": row["qid"], "label": row["label"], "k": row.get("k"),
                                         "problems": problems})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mentat_bench.hard_suite", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write candidates.jsonl and gold/suite_labels.jsonl")
    b.add_argument("--fixture", required=True)
    b.add_argument("--relations", type=int, default=6_000)
    b.add_argument("--groups", type=int, default=1_000)
    b.add_argument("--seed", type=int, default=None, help="default: fixture.json seed")
    a = sub.add_parser("audit", help="cross-check a sample of labels with audit_brute")
    a.add_argument("--fixture", required=True)
    a.add_argument("--sample", type=int, default=AUDIT_MIN_CELL, help="labels per (op, label, stratum) cell")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        print(json.dumps(build(args.fixture, args.relations, args.groups, args.seed), indent=2))
        return 0
    report = audit(args.fixture, args.sample)
    print(json.dumps(report, indent=2))
    return 1 if report["mismatches"] or report["depth"]["mismatches"] or report.get("world", {}).get("disagreements") else 0


if __name__ == "__main__":
    sys.exit(main())

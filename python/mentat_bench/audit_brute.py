"""Independent checker of the gold answers of a toy fixture (plan step 4b).

The checker reads only a fixture's gold submission (gold/theory.json, gold/bindings.jsonl), queries.jsonl and
gold/answers.jsonl. It imports neither the Rust engine nor the generators (fixtures_gen / fixtures_native), and it
implements the docs/ir.md semantics from scratch. It does not establish that the formalisation is faithful to the
language (mentat-bench-v0.1.md s7); it establishes that the gold answers follow from the gold theory.

Method
  1. Connected components: union-find over outcome IDs, linked by constraints (after alias expansion) and by
     bindings (var(id) <-> class alias).
  2. A query is answered per declared interpretation, on the groups of components it touches. A group is the set of
     components linked by one hypothesis expression (closure) or by the query as a whole.
  3. Groups of at most EXHAUSTIVE_MAX_VARS outcomes are enumerated exhaustively (numpy masks over all worlds).
     Larger groups go to an independent second encoding: Tseitin clauses with sequential counters for cardinality,
     answered by a small DPLL solver (two watched literals, no learning) with a conflict budget per call. A query
     over the budget is reported as skipped(BudgetExceeded); no random-world sampling tier is implemented.
  4. Model reuse: a probe that finds a world also tells every other outcome's value in that world, so a literal
     needs a refutation call only when its opposite value was not yet seen.

Semantics (docs/ir.md)
  closure     per interpretation: ENTAILED/REFUTED when K and H force the literal (priority over unresolved), else
              CONTINGENT if bound, UNRESOLVED if unresolved. Across the interpretations where K and H is satisfiable:
              a label survives only if all agree, otherwise UNRESOLVED. No such interpretation: hyp_inconsistent.
  selection   bound p qualifies iff in every interpretation K entails p<->T, p->T, T->p or not(p and T), per relation.
  feasibility SAT if every interpretation admits a world of K and the assumptions, UNSAT if none does, else UNRESOLVED.
  partition   true if every interpretation entails the claim, false if none does, else UNRESOLVED.
  Certificates: a SAT witness and a partition counterexample must extend to a world of every interpretation; an UNSAT
  core must be unsatisfiable in every interpretation. Maximality of witnesses is not checked (docs/ir.md does not
  define it precisely enough to test; see the report).
"""

from __future__ import annotations

import argparse
import functools
import json
import random
import sys
import time
from collections import Counter, defaultdict, deque
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

EXHAUSTIVE_MAX_VARS = 20
CONFLICT_BUDGET = 200_000
REPO_ROOT = Path(__file__).resolve().parents[2]
LABELS = ("ENTAILED", "REFUTED", "CONTINGENT", "UNRESOLVED")


class Unsupported(Exception):
    """Expression outside the solver encoding (wsum); the query is skipped with this reason."""


class BudgetExceeded(Exception):
    """A solver call used more conflicts than the budget."""


class BaseInconsistent(Exception):
    """A declared interpretation's base theory is unsatisfiable, so the gold is invalid."""


class QueryTimeout(Exception):
    """A query exceeded its wall-clock budget (--query-seconds)."""


def _check_deadline(deadline):
    if deadline is not None and time.perf_counter() > deadline:
        raise QueryTimeout("wall-clock budget exceeded")


# ---------------------------------------------------------------- expressions

def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _json(path: Path) -> Any:
    with path.open(encoding="utf-8-sig") as fh:
        return json.load(fh)


def _kids(e: dict[str, Any]) -> list[dict[str, Any]]:
    op = e["op"]
    if op in ("var", "ref", "true", "false"):
        return []
    if op == "not":
        return [e["a"]]
    if op in ("and", "or", "atmost", "atleast", "exactly"):
        return list(e["args"])
    if op in ("implies", "iff"):
        return [e["a"], e["b"]]
    if op == "wsum":
        return [t["e"] for t in e["terms"]]
    raise ValueError(f"unknown op {op!r}")


@functools.lru_cache(maxsize=None)
def _lit(vid: str, positive: bool) -> dict[str, Any]:
    v = {"op": "var", "id": vid}
    return v if positive else {"op": "not", "a": v}


def _not(e: dict[str, Any]) -> dict[str, Any]:
    return {"op": "not", "a": e}


def _and(args: list[dict[str, Any]]) -> dict[str, Any]:
    return {"op": "and", "args": args}


def _or(args: list[dict[str, Any]]) -> dict[str, Any]:
    return {"op": "or", "args": args}


def _query_exprs(q: dict[str, Any]) -> list[dict[str, Any]]:
    out = list(q.get("hyp", [])) + list(q.get("assume", [])) + list(q.get("members", []))
    if q.get("target") is not None:
        out.append(q["target"])
    return out


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[Any, Any] = {}

    def find(self, x: Any) -> Any:
        p = self.parent
        if x not in p:
            p[x] = x
            return x
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a: Any, b: Any) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


class Theory:
    """The gold theory indexed by connected component (root = a representative outcome ID)."""

    def __init__(self, theory: dict[str, Any], bindings: list[dict[str, Any]], queries: list[dict[str, Any]]):
        self.interps: list[str] = list(theory["interpretations"])
        self.aliases: dict[str, Any] = theory["aliases"]
        self.bindings = bindings
        self.bind_by_id: dict[str, dict[str, Any]] = {}
        self._alias_vars: dict[str, frozenset[str] | None] = {}
        uf = _UnionFind()
        bind_exprs: dict[str, dict[str, Any]] = {}
        for b in bindings:
            if b["id"] in self.bind_by_id:
                raise ValueError(f"duplicate binding {b['id']}")
            self.bind_by_id[b["id"]] = b
            uf.find(b["id"])
            if b["status"] == "bound":
                cls: dict[str, Any] = {"op": "ref", "name": b["class"]}
                if b.get("negated"):
                    cls = _not(cls)
                bind_exprs[b["id"]] = {"op": "iff", "a": _lit(b["id"], True), "b": cls}
                for v in self.alias_vars(b["class"]):
                    uf.union(b["id"], v)
            elif b["status"] != "unresolved":
                raise ValueError(f"binding {b['id']} has status {b['status']!r}")
        const: list[dict[str, Any]] = []
        linked: list[tuple[str, dict[str, Any]]] = []
        for c in theory["constraints"]:
            vs = self.expanded_vars(c["expr"])
            if not vs:
                const.append(c)
                continue
            first = min(vs)
            for v in vs:
                uf.union(first, v)
            linked.append((first, c))
        for q in queries:
            for e in _query_exprs(q):
                for v in self.expanded_vars(e):
                    uf.find(v)
        self.root = uf.find
        self.members: dict[str, list[str]] = defaultdict(list)
        for node in uf.parent:
            self.members[uf.find(node)].append(node)
        self.cons: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for first, c in linked:
            self.cons[uf.find(first)].append(c)
        self.bind_exprs_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for vid, e in bind_exprs.items():
            self.bind_exprs_by_root[uf.find(vid)].append(e)
        self.const_cons = const
        self.component_roots = sorted(self.members)

    def expanded_vars(self, e: dict[str, Any]) -> frozenset[str]:
        out: set[str] = set()
        stack = [e]
        while stack:
            x = stack.pop()
            if x["op"] == "var":
                out.add(x["id"])
            elif x["op"] == "ref":
                out |= self.alias_vars(x["name"])
            else:
                stack.extend(_kids(x))
        return frozenset(out)

    def alias_vars(self, name: str) -> frozenset[str]:
        if name not in self._alias_vars:
            if name not in self.aliases:
                raise ValueError(f"unknown alias {name!r}")
            self._alias_vars[name] = None  # cycle guard
            self._alias_vars[name] = self.expanded_vars(self.aliases[name])
        out = self._alias_vars[name]
        if out is None:
            raise ValueError(f"alias cycle through {name!r}")
        return out


class _Group:
    """The components in `roots`: their outcomes, the constraints and bindings inside them."""

    def __init__(self, th: Theory, roots: frozenset[str]):
        self.roots = roots
        self.vars = sorted(v for r in roots for v in th.members[r])
        if roots:
            self.cons = [c for r in sorted(roots) for c in th.cons[r]]
            self.binds = [e for r in sorted(roots) for e in th.bind_exprs_by_root[r]]
        else:
            self.cons = list(th.const_cons)
            self.binds = []

    def exprs(self, th: Theory, i: int) -> list[dict[str, Any]]:
        name = th.interps[i]
        active = [c["expr"] for c in self.cons if c.get("interps") is None or name in c["interps"]]
        return active + self.binds


# ---------------------------------------------------------------- exhaustive backend

class _Brute:
    """Every world of the group as one row of a 2^n table; masks are evaluated with numpy."""

    kind = "exhaustive"

    def __init__(self, th: Theory, group: _Group, i: int, deadline: float | None = None):
        self.deadline = deadline
        self.aliases = th.aliases
        self.vars = group.vars
        self.index = {v: j for j, v in enumerate(group.vars)}
        self.rows = np.arange(1 << len(group.vars), dtype=np.int64)
        self._cols: dict[str, np.ndarray] = {}
        self._alias: dict[str, np.ndarray] = {}
        self.K = np.ones(self.rows.shape[0], dtype=bool)
        for e in group.exprs(th, i):
            self.K &= self._ev(e)

    def _col(self, vid: str) -> np.ndarray:
        if vid not in self._cols:
            if vid not in self.index:
                raise ValueError(f"outcome {vid!r} outside its component")
            self._cols[vid] = ((self.rows >> self.index[vid]) & 1).astype(bool)
        return self._cols[vid]

    def _ev(self, e: dict[str, Any]) -> np.ndarray:
        op = e["op"]
        n = self.rows.shape[0]
        if op == "var":
            return self._col(e["id"])
        if op == "ref":
            if e["name"] not in self._alias:
                self._alias[e["name"]] = self._ev(self.aliases[e["name"]])
            return self._alias[e["name"]]
        if op == "true":
            return np.ones(n, dtype=bool)
        if op == "false":
            return np.zeros(n, dtype=bool)
        if op == "not":
            return ~self._ev(e["a"])
        if op == "and":
            out = np.ones(n, dtype=bool)
            for x in e["args"]:
                out &= self._ev(x)
            return out
        if op == "or":
            out = np.zeros(n, dtype=bool)
            for x in e["args"]:
                out |= self._ev(x)
            return out
        if op == "implies":
            return ~self._ev(e["a"]) | self._ev(e["b"])
        if op == "iff":
            return self._ev(e["a"]) == self._ev(e["b"])
        if op in ("atmost", "atleast", "exactly"):
            cnt = np.zeros(n, dtype=np.int64)
            for x in e["args"]:
                cnt += self._ev(x)
            k = e["k"]
            return cnt <= k if op == "atmost" else cnt >= k if op == "atleast" else cnt == k
        if op == "wsum":
            tot = np.zeros(n, dtype=np.int64)
            for t in e["terms"]:
                tot += int(t["w"]) * self._ev(t["e"]).astype(np.int64)
            k = int(e["k"])
            return tot <= k if e["cmp"] == "le" else tot >= k if e["cmp"] == "ge" else tot == k
        raise ValueError(f"unknown op {op!r}")

    def solve(self, exprs: list[dict[str, Any]], rng: random.Random | None = None) -> dict[str, bool] | None:
        _check_deadline(self.deadline)
        m = self.K
        for e in exprs:
            m = m & self._ev(e)
        hit = np.flatnonzero(m)
        if hit.size == 0:
            return None
        row = int(hit[rng.randrange(hit.size)]) if rng is not None else int(hit[0])
        return {v: bool((row >> j) & 1) for v, j in self.index.items()}


# ---------------------------------------------------------------- second encoding: CNF + DPLL

class _Solver:
    """DPLL over clauses with two watched literals and chronological backtracking (no learning).

    Literals are nonzero ints (+v / -v). `val[v]` is 1 (true), -1 (false) or 0 (unassigned).
    """

    def __init__(self, budget: int, deadline: float | None = None):
        self.budget = budget
        self.deadline = deadline
        self.nvars = 0
        self.val: list[int] = [0]
        self.watch: list[list[int]] = [[], []]
        self.clauses: list[list[int]] = []
        self.trail: list[int] = []
        self.base = 0
        self.unsat = False

    @staticmethod
    def _code(lit: int) -> int:
        return 2 * lit if lit > 0 else -2 * lit + 1

    def new_var(self) -> int:
        self.nvars += 1
        self.val.append(0)
        self.watch.append([])
        self.watch.append([])
        return self.nvars

    def _lv(self, lit: int) -> int:
        return self.val[lit] if lit > 0 else -self.val[-lit]

    def _assign(self, lit: int) -> None:
        self.val[abs(lit)] = 1 if lit > 0 else -1
        self.trail.append(lit)

    def _undo(self, n: int) -> None:
        while len(self.trail) > n:
            self.val[abs(self.trail.pop())] = 0

    def add_clause(self, lits: list[int]) -> None:
        """Add a clause at the base level (no search is running)."""
        if self.unsat:
            return
        out: list[int] = []
        for lit in lits:
            v = self._lv(lit)
            if v == 1:
                return
            if v == 0 and lit not in out:
                if -lit in out:
                    return
                out.append(lit)
        if not out:
            self.unsat = True
            return
        if len(out) == 1:
            qh = len(self.trail)
            self._assign(out[0])
            if not self._propagate(qh):
                self.unsat = True
        else:
            ci = len(self.clauses)
            self.clauses.append(out)
            self.watch[self._code(out[0])].append(ci)
            self.watch[self._code(out[1])].append(ci)
        self.base = len(self.trail)

    def _propagate(self, qh: int) -> bool:
        trail, val, clauses, watch = self.trail, self.val, self.clauses, self.watch
        while qh < len(trail):
            p = trail[qh]
            qh += 1
            fl = -p
            wl = watch[self._code(fl)]
            i = j = 0
            n = len(wl)
            while i < n:
                ci = wl[i]
                i += 1
                c = clauses[ci]
                if c[0] == fl:
                    c[0], c[1] = c[1], c[0]
                first = c[0]
                fv = val[first] if first > 0 else -val[-first]
                if fv == 1:
                    wl[j] = ci
                    j += 1
                    continue
                moved = False
                for k in range(2, len(c)):
                    lit = c[k]
                    if (val[lit] if lit > 0 else -val[-lit]) != -1:
                        c[1], c[k] = lit, fl
                        watch[self._code(lit)].append(ci)
                        moved = True
                        break
                if moved:
                    continue
                wl[j] = ci
                j += 1
                if fv == -1:
                    while i < n:
                        wl[j] = wl[i]
                        j += 1
                        i += 1
                    del wl[j:]
                    return False
                self._assign(first)
            del wl[j:]
        return True

    def solve(self, assume: list[int], rng: random.Random | None = None) -> list[bool] | None:
        """A model as a list indexed by variable (index 0 unused), or None when unsatisfiable under `assume`."""
        self._undo(self.base)
        if self.unsat:
            return None
        try:
            for lit in assume:
                v = self._lv(lit)
                if v == -1:
                    return None
                if v == 0:
                    qh = len(self.trail)
                    self._assign(lit)
                    if not self._propagate(qh):
                        return None
            return self._search(rng)
        finally:
            self._undo(self.base)

    def _search(self, rng: random.Random | None) -> list[bool] | None:
        # Watermark: every variable below `pos` is assigned. A decision at order position p0 is taken only when all
        # variables below p0 are assigned, and undoing to that decision leaves them assigned, so after a flip of the
        # decision at p0 the watermark is p0 + 1 and no rescan from 1 is needed.
        stack: list[tuple[int, int, bool, int]] = []
        conflicts = 0
        pos = 1
        val = self.val
        while True:
            _check_deadline(self.deadline)
            while pos <= self.nvars and val[pos] != 0:
                pos += 1
            if pos > self.nvars:
                return [False] + [val[v] == 1 for v in range(1, self.nvars + 1)]
            lit = pos if rng is None or rng.random() < 0.5 else -pos
            stack.append((len(self.trail), lit, False, pos))
            self._assign(lit)
            qh = len(self.trail) - 1
            while not self._propagate(qh):
                conflicts += 1
                if conflicts > self.budget:
                    raise BudgetExceeded(f"{conflicts} conflicts")
                while True:
                    if not stack:
                        return None
                    ti, lit0, flipped, p0 = stack.pop()
                    self._undo(ti)
                    if not flipped:
                        stack.append((ti, -lit0, True, p0))
                        self._assign(-lit0)
                        qh = ti
                        pos = p0 + 1
                        break


class _Encoder:
    """Tseitin encoding of the expression language; cardinality by a full sequential counter."""

    def __init__(self, solver: _Solver, var_of: dict[str, int], aliases: dict[str, Any]):
        self.s = solver
        self.var_of = var_of
        self.aliases = aliases
        self.T = solver.new_var()
        solver.add_clause([self.T])
        self._memo: dict[int, tuple[dict[str, Any], int]] = {}
        self._alias: dict[str, int | None] = {}

    def lit(self, e: dict[str, Any]) -> int:
        hit = self._memo.get(id(e))
        if hit is not None and hit[0] is e:
            return hit[1]
        out = self._lit(e)
        self._memo[id(e)] = (e, out)
        return out

    def _lit(self, e: dict[str, Any]) -> int:
        op = e["op"]
        if op == "var":
            if e["id"] not in self.var_of:
                raise ValueError(f"outcome {e['id']!r} outside its component")
            return self.var_of[e["id"]]
        if op == "true":
            return self.T
        if op == "false":
            return -self.T
        if op == "ref":
            name = e["name"]
            if name in self._alias:
                if self._alias[name] is None:
                    raise ValueError(f"alias cycle through {name!r}")
                return self._alias[name]
            self._alias[name] = None
            out = self.lit(self.aliases[name])
            self._alias[name] = out
            return out
        if op == "not":
            return -self.lit(e["a"])
        if op == "and":
            return self._and([self.lit(x) for x in e["args"]])
        if op == "or":
            return -self._and([-self.lit(x) for x in e["args"]])
        if op == "implies":
            return -self._and([self.lit(e["a"]), -self.lit(e["b"])])
        if op == "iff":
            return self._iff(self.lit(e["a"]), self.lit(e["b"]))
        if op in ("atmost", "atleast", "exactly"):
            lits = [self.lit(x) for x in e["args"]]
            k = int(e["k"])
            if op == "atmost":
                return -self._ge(lits, k + 1)
            if op == "atleast":
                return self._ge(lits, k)
            return self._and([self._ge(lits, k), -self._ge(lits, k + 1)])
        raise Unsupported(f"op {op!r} is not in the solver encoding")

    def _and(self, lits: list[int]) -> int:
        if not lits:
            return self.T
        if len(lits) == 1:
            return lits[0]
        g = self.s.new_var()
        for lit in lits:
            self.s.add_clause([-g, lit])
        self.s.add_clause([g] + [-lit for lit in lits])
        return g

    def _iff(self, a: int, b: int) -> int:
        g = self.s.new_var()
        self.s.add_clause([-g, -a, b])
        self.s.add_clause([-g, a, -b])
        self.s.add_clause([g, a, b])
        self.s.add_clause([g, -a, -b])
        return g

    def _ge(self, lits: list[int], t: int) -> int:
        """Literal for `count of true lits >= t` (sequential counter, both directions)."""
        if t <= 0:
            return self.T
        if t > len(lits):
            return -self.T
        prev = [self.T] + [-self.T] * t
        for x in lits:
            cur = [self.T]
            for j in range(1, t + 1):
                a, b = prev[j], prev[j - 1]  # count>=j and count>=j-1 before x
                g = self.s.new_var()  # g <-> a or (b and x)
                self.s.add_clause([-a, g])
                self.s.add_clause([-b, -x, g])
                self.s.add_clause([-g, a, b])
                self.s.add_clause([-g, a, x])
                cur.append(g)
            prev = cur
        return prev[t]


class _Dpll:
    kind = "solver"

    def __init__(self, th: Theory, group: _Group, i: int, budget: int, deadline: float | None = None):
        self.vars = group.vars
        self.s = _Solver(budget, deadline)
        self.var_of = {v: self.s.new_var() for v in group.vars}
        self.enc = _Encoder(self.s, self.var_of, th.aliases)
        for e in group.exprs(th, i):
            self.s.add_clause([self.enc.lit(e)])

    def solve(self, exprs: list[dict[str, Any]], rng: random.Random | None = None) -> dict[str, bool] | None:
        model = self.s.solve([self.enc.lit(e) for e in exprs], rng)
        if model is None:
            return None
        return {v: model[x] for v, x in self.var_of.items()}


# ---------------------------------------------------------------- per-query analysis

def _probe(be: Any, base: list[dict[str, Any]], vars_: list[str]) -> dict[str, bool] | None:
    """None when `base` is unsatisfiable, else the outcomes forced to one value (absent = both values occur)."""
    m0 = be.solve(base)
    if m0 is None:
        return None
    seen = {v: {m0[v]} for v in vars_}
    forced: dict[str, bool] = {}
    for v in vars_:
        if len(seen[v]) == 2:
            continue
        (val,) = seen[v]
        m = be.solve(base + [_lit(v, not val)])
        if m is None:
            forced[v] = val
        else:
            for u in vars_:
                seen[u].add(m[u])
    return forced


def _merge_hyp(th: Theory, hyp: list[dict[str, Any]]) -> list[tuple[frozenset[str], list[dict[str, Any]]]]:
    """Hypothesis expressions grouped by the components they link (one group per connected set)."""
    uf = _UnionFind()
    roots_of = []
    for k, e in enumerate(hyp):
        roots = frozenset(th.root(v) for v in th.expanded_vars(e))
        roots_of.append(roots)
        uf.find(("e", k))
        for r in roots:
            uf.union(("e", k), ("r", r))
    groups: dict[Any, tuple[set[str], list[dict[str, Any]]]] = {}
    for k, e in enumerate(hyp):
        g = groups.setdefault(uf.find(("e", k)), (set(), []))
        g[0].update(roots_of[k])
        g[1].append(e)
    return [(frozenset(r), exprs) for r, exprs in groups.values()]


def _claim_expr(claim: str, members: list[dict[str, Any]], target: dict[str, Any] | None) -> dict[str, Any]:
    t = target if target is not None else {"op": "true"}
    pairs = [_not(_and([members[a], members[b]])) for a in range(len(members)) for b in range(a + 1, len(members))]
    excl = _and(pairs)
    exh = {"op": "implies", "a": t, "b": _or(members)}
    if claim == "exclusive":
        return excl
    if claim == "exhaustive":
        return exh
    if claim == "partition":
        return _and([excl, exh] + [{"op": "implies", "a": m, "b": t} for m in members])
    raise ValueError(f"unknown claim {claim!r}")


def _relation_violated(rel: str, p_can: tuple[bool, bool], t_can: tuple[bool, bool]) -> bool:
    """Violation of a selection relation given which values of p and of T occur (components disjoint)."""
    p1, p0 = p_can
    t1, t0 = t_can
    if rel == "equiv":
        return (p1 and t0) or (p0 and t1)
    if rel == "implies":
        return p1 and t0
    if rel == "implied_by":
        return t1 and p0
    if rel == "excludes":
        return p1 and t1
    raise ValueError(f"unknown relation {rel!r}")


class Checker:
    def __init__(self, th: Theory, budget: int):
        self.th = th
        self.budget = budget
        self._groups: dict[frozenset[str], _Group] = {}
        self._base: dict[tuple[str, int], tuple[dict[str, bool], str]] = {}
        self.reset_query()

    def reset_query(self, deadline: float | None = None) -> None:
        self._live: dict[tuple[frozenset[str], int], Any] = {}
        self.used: set[str] = set()
        self.max_vars = 0
        self.deadline = deadline

    def group(self, roots: frozenset[str]) -> _Group:
        if roots not in self._groups:
            self._groups[roots] = _Group(self.th, roots)
        return self._groups[roots]

    def _make(self, roots: frozenset[str], i: int) -> Any:
        g = self.group(roots)
        if len(g.vars) <= EXHAUSTIVE_MAX_VARS:
            return _Brute(self.th, g, i, self.deadline)
        return _Dpll(self.th, g, i, self.budget, self.deadline)

    def backend(self, roots: frozenset[str], i: int) -> Any:
        key = (roots, i)
        if key not in self._live:
            self._live[key] = self._make(roots, i)
        be = self._live[key]
        self.used.add(be.kind)
        self.max_vars = max(self.max_vars, len(be.vars))
        return be

    def base(self, root: str, i: int) -> dict[str, bool]:
        """Forced values of one component's base theory in interpretation i (cached across queries)."""
        key = (root, i)
        if key not in self._base:
            roots = frozenset([root])
            be = self._make(roots, i)
            forced = _probe(be, [], list(be.vars))
            if forced is None:
                raise BaseInconsistent(f"interpretation {self.th.interps[i]!r}, component {root!r}")
            self._base[key] = (forced, be.kind)
        forced, kind = self._base[key]
        self.used.add(kind)
        return forced

    def check_base(self) -> int:
        """Base consistency of every component and interpretation; returns how many could not be decided."""
        undecided = 0
        for r in [None] + self.th.component_roots:
            for i in range(len(self.th.interps)):
                try:
                    if r is None:
                        if self._make(frozenset(), i).solve([]) is None:
                            raise BaseInconsistent(f"interpretation {self.th.interps[i]!r}, constant constraints")
                    else:
                        self.base(r, i)
                except (Unsupported, BudgetExceeded):
                    undecided += 1
        self.reset_query()
        return undecided

    # -- closure
    def closure(self, q: dict[str, Any]) -> tuple[dict[str, list[str]] | None, list[str]]:
        th = self.th
        universe = q.get("universe") or [b["id"] for b in th.bindings]
        hgroups = _merge_hyp(th, q["hyp"])
        hroot_group: dict[str, int] = {}
        for gi, (roots, _) in enumerate(hgroups):
            for r in roots:
                hroot_group[r] = gi
        survivors: list[int] = []
        hforced: dict[int, list[dict[str, bool]]] = {}
        for i in range(len(th.interps)):
            per_group = []
            alive = True
            for roots, exprs in hgroups:
                be = self.backend(roots, i)
                res = _probe(be, exprs, list(be.vars))
                if res is None:
                    alive = False
                    break
                per_group.append(res)
            if alive:
                survivors.append(i)
                hforced[i] = per_group
        if not survivors:
            return None, []
        labels: dict[str, list[str]] = {lab: [] for lab in LABELS}
        for u in universe:
            bound = self.th.bind_by_id.get(u, {}).get("status") == "bound"
            seen: set[str] = set()
            for i in survivors:
                root = th.root(u)
                if root in hroot_group:
                    f = hforced[i][hroot_group[root]].get(u)
                else:
                    f = self.base(root, i).get(u)
                if f is True:
                    seen.add("ENTAILED")
                elif f is False:
                    seen.add("REFUTED")
                else:
                    seen.add("CONTINGENT" if bound else "UNRESOLVED")
            label = seen.pop() if len(seen) == 1 else "UNRESOLVED"
            labels[label].append(u)
        return labels, survivors

    # -- selection
    def selection(self, q: dict[str, Any]) -> list[str]:
        th = self.th
        target, rel = q["target"], q["relation"]
        troots = frozenset(th.root(v) for v in th.expanded_vars(target))
        universe = q.get("universe") or [b["id"] for b in th.bindings]
        cand = [u for u in universe if th.bind_by_id.get(u, {}).get("status") == "bound"]
        ok = {u: True for u in cand}
        not_target = _not(target)
        for i in range(len(th.interps)):
            tb = self.backend(troots, i)
            t_can = (tb.solve([target]) is not None, tb.solve([not_target]) is not None)
            for u in cand:
                if not ok[u]:
                    continue
                root = th.root(u)
                if root in troots:
                    violated = self._violated_joint(tb, target, not_target, u, rel)
                else:
                    forced = self.base(root, i)
                    p_can = (forced.get(u) is not False, forced.get(u) is not True)
                    violated = _relation_violated(rel, p_can, t_can)
                if violated:
                    ok[u] = False
        return sorted(u for u in cand if ok[u])

    @staticmethod
    def _violated_joint(tb: Any, target: dict[str, Any], not_target: dict[str, Any], u: str, rel: str) -> bool:
        """Violation of the relation when p and the target share components: direct solver calls."""
        p, not_p = _lit(u, True), _lit(u, False)
        if rel == "equiv":
            return tb.solve([p, not_target]) is not None or tb.solve([not_p, target]) is not None
        if rel == "implies":
            return tb.solve([p, not_target]) is not None
        if rel == "implied_by":
            return tb.solve([target, not_p]) is not None
        if rel == "excludes":
            return tb.solve([p, target]) is not None
        raise ValueError(f"unknown relation {rel!r}")

    # -- feasibility / partition
    def satisfiable(self, roots: frozenset[str], exprs: list[dict[str, Any]]) -> list[bool]:
        return [self.backend(roots, i).solve(exprs) is not None for i in range(len(self.th.interps))]

    def support_roots(self, exprs: list[dict[str, Any]]) -> frozenset[str]:
        return frozenset(self.th.root(v) for e in exprs for v in self.th.expanded_vars(e))


def _maximality(ck: Checker, roots: frozenset[str], base: list[dict[str, Any]], w: dict[str, bool],
                support: frozenset[str]) -> tuple[list[str], str]:
    """Witness maximality. W (over the support) is maximal when, for every support ID x not in W and each polarity v,
    W + {x=v} does NOT extend to a world of `base` in EVERY interpretation: some interpretation blocks it.
    Consequence: in a single-interpretation theory a maximal W is total over the support (any world extending W
    gives every other support ID a value that extends W). Returns (mismatches, 'checked' | 'unverified'); a budget
    overrun leaves the query 'unverified' and is never reported as verified."""
    wl = [_lit(k, bool(v)) for k, v in w.items()]
    gaps: list[str] = []
    try:
        for x in sorted(support - set(w)):
            for v in (True, False):
                ext = [ck.backend(roots, i).solve(base + wl + [_lit(x, v)]) is not None
                       for i in range(len(ck.th.interps))]
                if all(ext):
                    gaps.append(f"witness not maximal: {x}={v} extends to a world in every interpretation")
    except BudgetExceeded:
        return gaps, "unverified"
    return gaps, "checked"


def _unresolved_codes(th: Theory, support: frozenset[str]) -> set[str]:
    codes: set[str] = set()
    for v in support:
        b = th.bind_by_id.get(v)
        if b is not None and b["status"] == "unresolved":
            codes |= set(b.get("missing") or [])
    return codes


def _missing_ok(gold_missing: list[str] | None, codes: set[str]) -> bool:
    gm = set(gold_missing or [])
    return bool(gm & codes) if codes else not gm


# ---------------------------------------------------------------- query handlers

def _check_closure(ck: Checker, q: dict[str, Any], gold: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    labels, survivors = ck.closure(q)
    ours = {"survivors": [ck.th.interps[i] for i in survivors],
            "labels": None if labels is None else {k: len(v) for k, v in labels.items()}}
    mism: list[str] = []
    if labels is None:
        if gold.get("labels") is not None or gold.get("error") != "hyp_inconsistent":
            mism.append("hypothesis is inconsistent in every interpretation but gold does not say hyp_inconsistent")
        return ours, mism
    if gold.get("error") is not None:
        return ours, [f"gold error {gold['error']!r} but the hypothesis is consistent"]
    glabels = gold.get("labels") or {}
    for lab in LABELS:
        mine, theirs = sorted(labels[lab]), sorted(glabels.get(lab) or [])
        if mine != theirs:
            extra = sorted(set(theirs) - set(mine))[:3]
            missing = sorted(set(mine) - set(theirs))[:3]
            mism.append(f"{lab}: {len(mine)} ours vs {len(theirs)} gold (gold-only {extra}, ours-only {missing})")
    return ours, mism


def _check_selection(ck: Checker, q: dict[str, Any], gold: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    ids = ck.selection(q)
    ours = {"count": len(ids)}
    if gold.get("error") is not None:
        return ours, [f"gold error {gold['error']!r}"]
    gids = sorted(gold.get("ids") or [])
    if gids == ids:
        return ours, []
    return ours, [f"ids differ: {len(ids)} ours vs {len(gids)} gold (gold-only {sorted(set(gids) - set(ids))[:3]}, "
                  f"ours-only {sorted(set(ids) - set(gids))[:3]})"]


def _check_feasibility(ck: Checker, q: dict[str, Any], gold: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    th = ck.th
    assume = q["assume"]
    roots = ck.support_roots(assume)
    sat = ck.satisfiable(roots, assume)
    decision = "SAT" if all(sat) else "UNSAT" if not any(sat) else "UNRESOLVED"
    ours = {"decision": decision, "per_interpretation": sat}
    mism: list[str] = []
    if gold.get("decision") != decision:
        mism.append(f"decision {decision} ours vs {gold.get('decision')} gold")
        return ours, mism
    support = frozenset(v for e in assume for v in th.expanded_vars(e))
    if decision == "SAT":
        w = gold.get("witness")
        if w is None:
            mism.append("SAT without witness")
        elif set(w) - support:
            mism.append(f"witness keys outside the query support: {sorted(set(w) - support)[:3]}")
        else:
            wl = [_lit(k, bool(v)) for k, v in w.items()]
            for i, name in enumerate(th.interps):
                if ck.backend(roots, i).solve(assume + wl) is None:
                    mism.append(f"witness does not extend to a world in interpretation {name!r}")
            if not mism:
                gaps, ours["maximality"] = _maximality(ck, roots, assume, w, support)
                mism += gaps
    elif decision == "UNSAT":
        core = gold.get("core")
        if core is None:
            mism.append("UNSAT without core")
        elif not all(isinstance(k, int) and 0 <= k < len(assume) for k in core):
            mism.append(f"core indices out of range: {core}")
        else:
            sub = [assume[k] for k in core]
            for i, name in enumerate(th.interps):
                if ck.backend(roots, i).solve(sub) is not None:
                    mism.append(f"core is satisfiable in interpretation {name!r}")
    else:
        codes = _unresolved_codes(th, support)
        if not _missing_ok(gold.get("missing"), codes):
            mism.append(f"missing {gold.get('missing')} does not match unresolved codes {sorted(codes)}")
    return ours, mism


def _check_partition(ck: Checker, q: dict[str, Any], gold: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    th = ck.th
    members, target = q["members"], q.get("target")
    claim = _claim_expr(q["claim"], members, target)
    support = frozenset(v for e in members + ([target] if target is not None else []) for v in th.expanded_vars(e))
    roots = ck.support_roots(members + ([target] if target is not None else []))
    fails = [ck.backend(roots, i).solve([_not(claim)]) is not None for i in range(len(th.interps))]
    decision = "true" if not any(fails) else "false" if all(fails) else "UNRESOLVED"
    ours = {"decision": decision, "fails_per_interpretation": fails}
    mism: list[str] = []
    if gold.get("decision") != decision:
        mism.append(f"decision {decision} ours vs {gold.get('decision')} gold")
        return ours, mism
    if decision == "false":
        w = gold.get("counterexample")
        if w is None:
            mism.append("false without counterexample")
        elif set(w) - support:
            mism.append(f"counterexample keys outside the query support: {sorted(set(w) - support)[:3]}")
        else:
            wl = [_lit(k, bool(v)) for k, v in w.items()]
            for i, name in enumerate(th.interps):
                if ck.backend(roots, i).solve([_not(claim)] + wl) is None:
                    mism.append(f"counterexample does not extend to a violating world in {name!r}")
            if not mism:
                gaps, ours["maximality"] = _maximality(ck, roots, [_not(claim)], w, support)
                mism += gaps
    elif decision == "UNRESOLVED":
        codes = _unresolved_codes(th, support)
        if not _missing_ok(gold.get("missing"), codes):
            mism.append(f"missing {gold.get('missing')} does not match unresolved codes {sorted(codes)}")
    return ours, mism


_HANDLERS = {"closure": _check_closure, "selection": _check_selection,
             "feasibility": _check_feasibility, "partition": _check_partition}


# ---------------------------------------------------------------- fixture driver

def _status(mism: list[str], methods: set[str], max_unverified: bool) -> str:
    if mism:
        return "mismatch"
    if max_unverified:
        return "verified_except_maximality"
    return "verified_independent_solver" if "solver" in methods else "verified_exhaustive"


class _Skip(Exception):
    def __init__(self, msg: str, mismatch: bool = False):
        super().__init__(msg)
        self.mismatch = mismatch


_QUERY_FILES = (("queries.jsonl", "answers.jsonl"), ("queries_extra.jsonl", "answers_extra.jsonl"))


def _scope(full: Theory, theory: dict[str, Any], bindings: list[dict[str, Any]],
           evidence: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    """Evidence scope, docs/ir.md reach rule. Returns the scoped theory, the scoped bindings (file order) and the
    in-scope outcome IDs. An outcome is in scope when all its binding evidence is in the evidence set. A constraint is
    kept when all its provenance refs are in the evidence set and every outcome it reaches (through aliases) is in
    scope. A bound in-scope outcome whose class reaches an out-of-scope outcome becomes unresolved, with
    out_of_scope appended to its missing codes."""
    in_scope = {b["id"] for b in bindings if set(b.get("evidence") or []) <= evidence}
    cons = [c for c in theory["constraints"]
            if set(c.get("provenance") or []) <= evidence and full.expanded_vars(c["expr"]) <= in_scope]
    out: list[dict[str, Any]] = []
    for b in bindings:
        if b["id"] not in in_scope:
            continue
        if b["status"] == "bound" and not full.alias_vars(b["class"]) <= in_scope:
            nb = dict(b)
            nb["status"], nb["class"], nb["negated"] = "unresolved", None, False
            nb["missing"] = list(dict.fromkeys(list(b.get("missing") or []) + ["out_of_scope"]))
            b = nb
        out.append(b)
    sub = {"interpretations": theory["interpretations"], "aliases": theory["aliases"], "constraints": cons}
    return sub, out, in_scope


def _certificate_check(full: Theory, theory: dict[str, Any], bindings: list[dict[str, Any]], prov_refs: set[str],
                       q: dict[str, Any], refs: Any, query_scope: set[str], max_cert: int | None,
                       budget: int, deadline: float | None) -> list[str]:
    """A true partition certificate: its refs must lie in the query's scope, and under the scope they induce the claim
    must be entailed in every interpretation (docs/ir.md, certificates)."""
    if not isinstance(refs, list):
        return ["certificate evidence is not a list"]
    cert = set(refs)
    mism: list[str] = []
    if not cert <= query_scope:
        mism.append(f"certificate refs outside the query scope (evidence_out_of_scope): "
                    f"{sorted(cert - query_scope)[:3]}")
    if cert - prov_refs:
        mism.append(f"certificate cites unknown refs: {sorted(cert - prov_refs)[:3]}")
    if max_cert is not None and len(refs) > max_cert:
        mism.append(f"certificate_too_large: {len(refs)} > {max_cert}")
    parts = q["members"] + ([q["target"]] if q.get("target") is not None else [])
    sub, sub_b, in_scope = _scope(full, theory, bindings, cert)
    if not set().union(*(full.expanded_vars(e) for e in parts)) <= in_scope:
        return mism + ["certificate scope does not cover the claim's outcomes"]
    th = Theory(sub, sub_b, [])
    ck = Checker(th, budget)
    ck.check_base()
    ck.reset_query(deadline)
    claim = _claim_expr(q["claim"], q["members"], q.get("target"))
    roots = ck.support_roots(parts)
    if any(ck.backend(roots, i).solve([_not(claim)]) is not None for i in range(len(th.interps))):
        mism.append("certificate does not establish the claim in every interpretation")
    return mism


def _scoped_query(full: Theory, theory: dict[str, Any], bindings: list[dict[str, Any]], prov_refs: set[str],
                  q: dict[str, Any], gold: dict[str, Any], budget: int, deadline: float | None,
                  max_cert: int | None) -> tuple[dict[str, Any], list[str], Checker | None]:
    evidence = q["evidence"]
    if not isinstance(evidence, list):
        raise _Skip("evidence is not a list")
    scope = set(evidence)
    expected = "out_of_scope" if not evidence or any(r not in prov_refs for r in evidence) else None
    sub, sub_b, in_scope = _scope(full, theory, bindings, scope)
    exprs = _query_exprs(q)
    qvars = set().union(*(full.expanded_vars(e) for e in exprs)) if exprs else set()
    if expected is None and not qvars <= in_scope:
        expected = "out_of_scope"
    if expected is not None:
        ok = gold.get("error") == expected
        return {"expected_error": expected}, ([] if ok else [f"gold must answer {expected} (evidence scope)"]), None
    by_id = {b["id"]: b for b in bindings}
    if q.get("universe"):
        universe = list(q["universe"])
        known = {b["id"] for b in sub_b}
        for u in universe:
            if u not in known:
                # explicit entry out of scope: kept as unresolved, with out_of_scope appended to its missing codes
                base = by_id.get(u, {})
                sub_b.append({"id": u, "status": "unresolved", "class": None, "negated": False,
                              "missing": list(dict.fromkeys(list(base.get("missing") or []) + ["out_of_scope"])),
                              "evidence": []})
                known.add(u)
    else:
        universe = [b["id"] for b in sub_b]
    q2 = dict(q, universe=universe)
    th = Theory(sub, sub_b, [q2])
    ck = Checker(th, budget)
    try:
        ck.check_base()
    except BaseInconsistent as exc:
        return {"scoped_base": str(exc)}, [f"scoped base inconsistent: {exc}"], ck
    ck.reset_query(deadline)
    ours, mism = _HANDLERS[q["family"]](ck, q2, gold)
    if q["family"] == "partition" and gold.get("decision") == "true" and "evidence" in gold:
        mism += _certificate_check(full, theory, bindings, prov_refs, q, gold["evidence"], scope, max_cert,
                                   budget, deadline)
    return ours, mism, ck


def _audit_one(full: Theory, ck: Checker, theory: dict[str, Any], bindings: list[dict[str, Any]],
               prov_refs: set[str], fname: str, q: dict[str, Any], gold: dict[str, Any] | None, budget: int,
               query_seconds: float, max_cert: int | None) -> dict[str, Any]:
    rec: dict[str, Any] = {"qid": q["qid"], "family": q["family"], "file": fname, "scoped": "evidence" in q}
    deadline = time.perf_counter() + query_seconds
    t1 = time.perf_counter()
    used: Checker | None = None
    try:
        if gold is None:
            raise _Skip("no gold answer", mismatch=True)
        if "evidence" in q:
            ours, mism, used = _scoped_query(full, theory, bindings, prov_refs, q, gold, budget, deadline, max_cert)
        else:
            ck.reset_query(deadline)
            ours, mism = _HANDLERS[q["family"]](ck, q, gold)
            used = ck
        rec["ours"] = ours
        rec["details"] = mism
        methods = used.used if used is not None else set()
        rec["status"] = _status(mism, methods, ours.get("maximality") == "unverified")
    except _Skip as exc:
        rec["details"] = [str(exc)] if exc.mismatch else []
        rec["status"] = "mismatch" if exc.mismatch else f"skipped({exc})"
    except QueryTimeout:
        rec["details"] = []
        rec["status"] = f"skipped(timeout after {query_seconds:g} s)"
    except (Unsupported, BudgetExceeded, ValueError) as exc:
        rec["details"] = []
        rec["status"] = f"skipped({type(exc).__name__}: {exc})"
    rec["methods"] = sorted(used.used if used is not None else [])
    rec["max_group_vars"] = used.max_vars if used is not None else 0
    rec["seconds"] = round(time.perf_counter() - t1, 3)
    return rec


def _count(records: list[dict[str, Any]]) -> dict[str, int]:
    keys = ("verified_exhaustive", "verified_independent_solver", "verified_except_maximality", "sampled",
            "mismatch", "skipped")
    counts = Counter(("skipped" if r["status"].startswith("skipped") else r["status"]) for r in records)
    return {k: counts.get(k, 0) for k in keys}


def audit_fixture(fixture: Path, budget: int = CONFLICT_BUDGET, query_seconds: float = 120.0) -> dict[str, Any]:
    t0 = time.perf_counter()
    gold = fixture / "gold"
    theory = _json(gold / "theory.json")
    bindings = _jsonl(gold / "bindings.jsonl")
    files = []
    for qname, aname in _QUERY_FILES:
        if (fixture / qname).exists():
            answers = {a["qid"]: a for a in _jsonl(gold / aname)} if (gold / aname).exists() else {}
            files.append((qname, _jsonl(fixture / qname), answers))
    all_queries = [q for _, qs, _ in files for q in qs]
    full = Theory(theory, bindings, all_queries)
    ck = Checker(full, budget)
    report: dict[str, Any] = {
        "fixture": str(fixture), "interpretations": full.interps, "bindings": len(bindings),
        "queries": len(all_queries), "query_files": {qn: len(qs) for qn, qs, _ in files},
        "components": len(full.component_roots),
        "component_outcomes_max": max((len(full.members[r]) for r in full.component_roots), default=0),
        "exhaustive_max_vars": EXHAUSTIVE_MAX_VARS, "conflict_budget": budget, "query_seconds": query_seconds,
    }
    try:
        undecided = ck.check_base()
        report["base"] = "consistent" if not undecided else f"consistent except {undecided} undecided groups"
    except BaseInconsistent as exc:
        report.update(base=f"inconsistent: {exc}", fatal=True, results=[], files={}, by_family={},
                      summary=_count([]), runtime_s=round(time.perf_counter() - t0, 3))
        return report
    prov_refs: set[str] = set()
    if any("evidence" in q for _, qs, _ in files for q in qs):
        prov_refs = {r["ref"] for r in _jsonl(gold / "provenance.jsonl")}
    profile = gold / "profile.json"
    max_cert = _json(profile).get("max_certificate_refs") if profile.exists() else None
    results: list[dict[str, Any]] = []
    per_file: dict[str, Any] = {}
    for fname, queries, answers in files:
        recs = [_audit_one(full, ck, theory, bindings, prov_refs, fname, q, answers.get(q["qid"]), budget,
                           query_seconds, max_cert) for q in queries]
        per_file[fname] = {"queries": len(queries), "scoped": sum(1 for q in queries if "evidence" in q),
                           "summary": _count(recs)}
        results.extend(recs)
    by_family: dict[str, Counter[str]] = defaultdict(Counter)
    for rec in results:
        key = "skipped" if rec["status"].startswith("skipped") else rec["status"]
        by_family[rec["family"]][key] += 1
    report.update(results=results, files=per_file, summary=_count(results),
                  by_family={f: dict(c) for f, c in sorted(by_family.items())}, fatal=False,
                  runtime_s=round(time.perf_counter() - t0, 3))
    return report


# ---------------------------------------------------------------- inference depth of relation labels (Mentat-Hard v3.1)

DEPTH_COUNT = "count"  # an entailment that no chain of constraints reaches


class DepthGraph:
    """The inference depth of a relation label, written independently of hard_suite.Depths: a 0-1 breadth-first search
    over the unscoped constraints of the gold theory, read here from the theory JSON, the bindings and the meta rows.
    Nodes are class literals (a class, or its negation). An implication a => b and each exclusion of an exactly-1 or
    atmost-1 constraint (a => not b) are edges with their contrapositives; an exactly-1 constraint with two arguments
    also gives not x => y. An edge costs 0 when it is a score-ladder step (one team's thresholds, one point apart), else
    1. An unresolved outcome is its reading's class under each interpretation (the scoped iffs), and an entailment must
    hold in every interpretation, so it takes the larger cost. The depth of IMPLIES (a, b) is the cost from a to b;
    IMPLIED_BY, from b to a; EXCLUDES, from a to not b; EQUIV, the larger of the two directions. Without meta rows no
    ladder step is recognized, so every edge costs 1 (a degraded fixture)."""

    def __init__(self, theory: dict[str, Any], bindings: list[dict[str, Any]], meta: list[dict[str, Any]] | None):
        self.bind = {b["id"]: b for b in bindings}
        self.meta = {m["id"]: m for m in meta} if meta is not None else None
        self.interps = list(theory.get("interpretations") or ["base"])
        self.class_team: dict[str, tuple[Any, Any, Any]] = {}  # class -> (kind, (league, team), t)
        if self.meta is not None:
            for oid, b in self.bind.items():
                if b["status"] == "bound":
                    m = self.meta[oid]
                    self.class_team.setdefault(b["class"], (m["kind"], (m["league"], m["team"]), m["t"]))
        self.readings: dict[str, dict[str, tuple[str, bool]]] = defaultdict(dict)  # outcome -> interp -> (class, neg)
        self.adj: dict[tuple, list[tuple[tuple, int]]] = defaultdict(list)
        self._cache: dict[tuple, dict[tuple, int]] = {}
        partner: dict[str, str] = {}  # the Yes and No outcomes of one market: an unscoped iff(yes, not no)
        for c in theory["constraints"]:
            e = c["expr"]
            if e["op"] == "iff" and not c.get("interps") and e["a"]["op"] == "var" and e["b"]["op"] == "not" \
                    and e["b"]["a"]["op"] == "var":
                partner[e["a"]["id"]], partner[e["b"]["a"]["id"]] = e["b"]["a"]["id"], e["a"]["id"]
        for c in theory["constraints"]:
            e = c["expr"]
            if c.get("interps"):
                if e["op"] == "iff" and len(c["interps"]) == 1:
                    neg = e["a"]["op"] == "not"
                    var = e["a"]["a"] if neg else e["a"]
                    if var["op"] == "var" and e["b"]["op"] == "ref":
                        self.readings[var["id"]][c["interps"][0]] = (e["b"]["name"], neg)
                continue
        for yes, no in partner.items():  # the No outcome of a market reads the same class with the flag flipped
            for r, (cls, neg) in list(self.readings.get(yes, {}).items()):
                self.readings[no][r] = (cls, not neg)
        for c in theory["constraints"]:
            e = c["expr"]
            if c.get("interps"):
                continue
            if e["op"] == "implies":
                a, b = self._lit(e["a"]), self._lit(e["b"])
                if a and b:
                    self._edge(a, b, 0 if self._ladder(a, b) else 1)
            elif e["op"] in ("exactly", "atmost") and e["k"] == 1 and len(e["args"]) >= 2:
                lits = [self._lit(x) for x in e["args"]]
                if any(x is None for x in lits):
                    continue
                for x, y in combinations(lits, 2):
                    self._edge(x, (y[0], not y[1]), 1)  # at most one: x => not y (and y => not x)
                if e["op"] == "exactly" and len(lits) == 2:  # exactly one of two: not x => y
                    x, y = lits
                    self._edge((x[0], not x[1]), y, 1)

    def _lit(self, e: dict[str, Any]) -> tuple[str, bool] | None:
        neg = False
        while e["op"] == "not":
            e, neg = e["a"], not neg
        if e["op"] == "ref":
            return e["name"], not neg
        if e["op"] == "var":
            b = self.bind[e["id"]]
            return (b["class"], (not b["negated"]) != neg) if b["class"] is not None else None
        return None

    def _ladder(self, a: tuple[str, bool], b: tuple[str, bool]) -> bool:
        """A score-ladder step: T(x,t+1) => T(x,t) (positive literals of one team's thresholds one point apart)."""
        if not (a[1] and b[1]) or a[0] not in self.class_team or b[0] not in self.class_team:
            return False
        (ka, ta, va), (kb, tb, vb) = self.class_team[a[0]], self.class_team[b[0]]
        return ka == kb == "thr" and ta == tb and va is not None and vb is not None and va == vb + 1

    def _edge(self, u: tuple[str, bool], v: tuple[str, bool], cost: int) -> None:
        self.adj[u].append((v, cost))  # the implication u => v and its contrapositive not v => not u
        self.adj[(v[0], not v[1])].append(((u[0], not u[1]), cost))

    def _dist(self, u: tuple[str, bool], v: tuple[str, bool]) -> int | None:
        if u == v:
            return 0
        if u not in self._cache:
            dist = {u: 0}
            queue = deque([u])
            while queue:
                x = queue.popleft()
                for y, w in self.adj.get(x, ()):
                    if y not in dist or dist[x] + w < dist[y]:
                        dist[y] = dist[x] + w
                        (queue.appendleft if w == 0 else queue.append)(y)
            self._cache[u] = dist
        return self._cache[u].get(v)

    def _literals(self, oid: str) -> list[tuple[str, bool] | None]:
        """The literal of an outcome under each interpretation (None where it has no class)."""
        b = self.bind[oid]
        if b["status"] == "bound":
            return [(b["class"], not b["negated"])] * len(self.interps)
        out = []
        for r in self.interps:
            hit = self.readings.get(oid, {}).get(r)
            out.append(None if hit is None else (hit[0], not hit[1]))
        return out

    def depth(self, a: str, b: str, label: str) -> int | str | None:
        """The depth of the ordered pair (a, b) with its label: an int, DEPTH_COUNT, or None for NONE."""
        if label == "NONE":
            return None
        la, lb = self._literals(a), self._literals(b)
        if label == "IMPLIES":
            pairs = [(x, y) for x, y in zip(la, lb)]
        elif label == "IMPLIED_BY":
            pairs = [(y, x) for x, y in zip(la, lb)]
        elif label == "EXCLUDES":
            pairs = [(x, None if y is None else (y[0], not y[1])) for x, y in zip(la, lb)]
        else:  # EQUIV: both directions, the larger cost
            ds = [self._worst(x, y) for x, y in zip(la, lb)] + [self._worst(y, x) for x, y in zip(la, lb)]
            return DEPTH_COUNT if None in ds else max(ds)
        ds = [self._worst(x, y) for x, y in pairs]
        return DEPTH_COUNT if None in ds else max(ds)

    def _worst(self, x: tuple[str, bool] | None, y: tuple[str, bool] | None) -> int | None:
        if x is None or y is None:
            return None
        return self._dist(x, y)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mentat_bench.audit_brute",
                                 description="Independent exhaustive / second-encoding check of gold answers.")
    ap.add_argument("--fixture", required=True, help="fixture directory, e.g. fixtures/toy10k")
    ap.add_argument("--budget", type=int, default=CONFLICT_BUDGET, help="DPLL conflicts per solver call")
    ap.add_argument("--query-seconds", type=float, default=120.0, help="wall-clock budget per query")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    fixture = Path(args.fixture)
    if not fixture.exists():
        fixture = REPO_ROOT / args.fixture
    if not (fixture / "gold" / "theory.json").exists():
        print(f"not a fixture with gold/: {args.fixture}", file=sys.stderr)
        return 2
    report = audit_fixture(fixture, budget=args.budget, query_seconds=args.query_seconds)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    s = report["summary"]
    print(f"{fixture.name}: base {report['base']}; exhaustive {s['verified_exhaustive']}, "
          f"independent_solver {s['verified_independent_solver']}, except_maximality "
          f"{s['verified_except_maximality']}, sampled {s['sampled']}, mismatch {s['mismatch']}, "
          f"skipped {s['skipped']}; {report['runtime_s']} s")
    for fname, info in report.get("files", {}).items():
        print(f"  {fname}: {info['queries']} queries ({info['scoped']} scoped) {info['summary']}")
    for rec in report["results"]:
        if rec["status"] == "mismatch" or rec["status"].startswith("skipped"):
            print(f"  {rec['file']} {rec['qid']}: {rec['status']} {rec['details'][:3]}")
    return 1 if report.get("fatal") or s["mismatch"] else 0


if __name__ == "__main__":
    sys.exit(main())

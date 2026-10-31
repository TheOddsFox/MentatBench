"""Greedy admission of 2-CNF units against a dynamic transitive closure of the implication graph (the admit-time base
consistency stage of harness/fixed.py, finalize_checkpoint).

Invariant: the admitted clauses are satisfiable. For a satisfiable 2-CNF, F |= not l exactly when l reaches not l in its
implication graph, so a clause (x or y) is admissible iff not (F |= not x and F |= not y). The closure is an N x N bit
matrix R over the N = 2 x variables literals: bit v of row u is set when u reaches v (about N^2/8 bytes).

Skew symmetry: the closure of a clause set is invariant under w reaches u <=> not-u reaches not-w, because each clause
adds both of its implication edges. So the literals that reach u are the bits of row not-u with each bit moved to its
neighbour (bit 2k <-> 2k+1), and no column scan of the matrix is needed.

A clause (x or y) adds the edges a = not-x -> b = y and c = not-y -> d = x. The second edge is applied to the closure
after the first one: its sources are sources(c) plus sources(a) when b reaches c, and its targets are row d plus row b
when d reaches a. Both updates are applied to rows of the pre-clause closure.

numpy is imported here, so the module is loaded only when admission runs (fixed.py imports it lazily)."""

from __future__ import annotations

import numpy as np

_ONE = np.uint64(1)
_EVEN_BITS = np.uint64(0x5555555555555555)  # bits 0, 2, 4, ... of every word


class Closure:
    def __init__(self, n_vars: int):
        self.N = 2 * n_vars
        self.W = (self.N + 63) // 64
        self.R = np.zeros((self.N, self.W), dtype=np.uint64)  # MemoryError propagates to the caller (repair instead)
        idx = np.arange(self.N)
        self.R[idx, idx >> 6] |= _ONE << (idx & 63).astype(np.uint64)
        self._saved: list | None = None  # pre-unit contents of every row a unit touched (once each), for a refused unit
        self._touched = np.zeros(self.N, dtype=bool)
        self._touched_list: list[np.ndarray] = []

    def reach(self, u: int, v: int) -> bool:
        return bool((int(self.R[u, v >> 6]) >> (v & 63)) & 1)

    def forced_false(self, x: int) -> bool:  # F |= not x
        return self.reach(x, x ^ 1)

    def sources(self, u: int) -> np.ndarray:
        """Every literal that reaches u (u included), from the row of not-u (skew symmetry, see the module docstring)."""
        row = self.R[u ^ 1]
        swapped = ((row & _EVEN_BITS) << _ONE) | ((row >> _ONE) & _EVEN_BITS)
        return np.nonzero(np.unpackbits(swapped.view(np.uint8), bitorder="little")[: self.N])[0]

    def _update(self, rows: np.ndarray, bits: np.ndarray) -> None:
        if self._saved is not None:
            new = rows[~self._touched[rows]]
            if new.size:
                self._saved.append((new, self.R[new].copy()))
                self._touched[new] = True
                self._touched_list.append(new)
        self.R[rows] |= bits

    def add_clause(self, x: int, y: int) -> None:
        """Adds (x or y) to the closure. The caller has checked that it is admissible."""
        a, b, c, d = x ^ 1, y, y ^ 1, x
        first = None if self.reach(a, b) else (self.sources(a), self.R[b].copy())  # edge a -> b
        via_first = first is not None and self.reach(c, a) and self.reach(b, d)
        if self.reach(c, d) or via_first:  # edge c -> d is implied by the closure after edge a -> b
            second = None
        else:
            sources = self.sources(c)
            if first is not None and self.reach(b, c):
                sources = np.union1d(sources, first[0])
            targets = self.R[d].copy()
            if first is not None and self.reach(d, a):
                targets |= first[1]
            second = (sources, targets)
        if first is not None:
            self._update(*first)
        if second is not None:
            self._update(*second)

    def _begin(self, undo: bool) -> None:
        self._saved = [] if undo else None

    def _end(self, keep: bool) -> None:
        if self._saved is not None and not keep:
            for rows, old in self._saved:
                self.R[rows] = old
        if self._saved is not None:
            for rows in self._touched_list:
                self._touched[rows] = False
        self._saved, self._touched_list = None, []

    def try_unit(self, clauses: list[tuple[int, int]], need: tuple[int, list[int]] | None = None) -> bool:
        """Admits one unit all or nothing: its clauses one by one (each checked against the closure with the earlier ones
        added), then `need` = (k, literals): at least k of the literals must still be possibly true, a necessary condition
        of the unit's cardinality part (outside 2-CNF). A refusal at any step undoes every row the unit touched."""
        self._begin(undo=len(clauses) > 1 or need is not None)
        admitted = True
        for x, y in clauses:
            if self.forced_false(x) and self.forced_false(y):
                admitted = False
                break
            self.add_clause(x, y)
        if admitted and need is not None:
            k, literals = need
            admitted = at_least_possible(self, literals, k)
        self._end(keep=admitted)
        return admitted


def at_least_possible(closure: Closure, lits: list[int], k: int) -> bool:
    """False when every model of the closure has fewer than k of `lits` true: fewer than k are not forced false."""
    return sum(not closure.forced_false(lit) for lit in lits) >= k


def at_most_possible(closure: Closure, lits: list[int], k: int) -> bool:
    """False when every model of the closure has more than k of `lits` true. A lower bound on the true literals: the forced
    true ones, plus a greedy set of disjoint pairs (x or y) that the closure implies (each pair holds a true literal). Sound,
    not complete: a True answer is no proof of possibility."""
    forced = sum(closure.forced_false(lit ^ 1) for lit in lits)
    rest = [lit for lit in lits if not closure.forced_false(lit ^ 1) and not closure.forced_false(lit)]
    used, pairs = set(), 0
    for a, x in enumerate(rest):
        if x in used:
            continue
        for y in rest[a + 1:]:
            if y not in used and closure.reach(x ^ 1, y):
                used.update((x, y))
                pairs += 1
                break
    return forced + pairs <= k


def admit_units(n_vars: int, binding: list[tuple[int, int]], units: list[list[tuple[int, int]]],
                at_least: list[tuple[int, list[int]] | None] | None = None, tick=None) -> list[bool]:
    """Admission flag of each unit (its clause list), in order, on top of the binding clauses. The caller checks that the
    bindings are satisfiable: a unit is admitted iff it keeps the admitted set satisfiable (and, when `at_least[i]` =
    (k, literals) is given, iff at least k of its literals stay possibly true, see Closure.try_unit). `tick()` is called
    every TICK_EVERY units (the caller may raise to stop: the deadline)."""
    return admit_closure(n_vars, binding, units, at_least, tick)[0]


def admit_closure(n_vars: int, binding: list[tuple[int, int]], units: list[list[tuple[int, int]]],
                  at_least: list[tuple[int, list[int]] | None] | None = None, tick=None) -> tuple[list[bool], Closure]:
    """admit_units, and the closure of the bindings and the admitted units (refused units are undone, so it holds exactly
    the admitted clauses): the base that a per-unit check of the deferred parts reads."""
    closure = Closure(n_vars)
    for x, y in binding:
        closure.add_clause(x, y)
    flags = []
    for i, clauses in enumerate(units):
        if tick is not None and i % TICK_EVERY == 0:
            tick(flags)
        need = at_least[i] if at_least is not None else None
        flags.append(closure.try_unit(clauses, need))
    return flags, closure


TICK_EVERY = 256  # units between two calls of the caller's tick (deadline check)

"""Base repair by conflict-cycle extraction (the default) against the prefix bisection (MB_PRUNE=bisect): on random small
theories both leave every interpretation consistent, every repair is the highest-indexed unit on a conflict cycle of the
current pass, the choice is deterministic, and the caps raise with the count and rate."""

import random

import pytest

from mentat_bench.harness.fixed import (
    BaseInconsistentError, _amo, _BaseCheck, _conflict_cycle, _prune_in_process, _relation_clauses, _Unit,
)


class _NoRows:
    def __init__(self, n):
        self.ids = [f"v:x{i}:1" for i in range(n)]

    def rows(self):
        return iter(())


def _theory(seed):
    """Random relation units (2-CNF) and scoped at-most-one groups (a bit per interpretation): a fresh copy per call."""
    rng = random.Random(seed)
    n = rng.randint(3, 6)
    units = []
    for k in range(rng.randint(3, 12)):
        if rng.random() < 0.75:
            i, j = rng.sample(range(n), 2)
            rel = rng.choice(["IMPLIES", "IMPLIED_BY", "EXCLUDES", "EQUIV"])
            units.append(_Unit(f"p{k:07d}", None, -1, _relation_clauses(i, j, rel), False, "relation"))
        else:
            members = sorted(rng.sample(range(n), 3))
            mask = rng.choice([1, 2, 3])  # holds in interpretation 0, 1 or both
            units.append(_Unit(f"g{k:07d}", None, mask, _amo(members), False, "at_most_one", members))
    return n, units


def _run(seed, method):
    n, units = _theory(seed)
    check = _BaseCheck(_NoRows(n), units, 2)
    trace = []
    dropped = _prune_in_process(check, 10**6, 10**6, lambda: 1e9, method=method, trace=trace)
    return check, units, dropped, trace


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize("method", ["scc", "bisect"])
def test_both_repairs_leave_every_interpretation_consistent(seed, method):
    check, units, dropped, _ = _run(seed, method)
    for interp in range(check.n_interps):
        assert not check.unsat(len(units), dropped, interp), (seed, method, interp)


@pytest.mark.parametrize("seed", range(60))
def test_scc_repairs_the_highest_unit_of_a_conflict_cycle_and_nothing_else(seed):
    check, units, dropped, trace = _run(seed, "scc")
    for interp, cycle, culprit in trace:
        relations = [n for n in cycle if units[n].kind == "relation"]
        assert cycle and culprit == (max(relations) if relations else max(cycle))  # the highest relation on the cycle
    culprits = {culprit for _, _, culprit in trace}
    # every repaired unit was a culprit (relation units here have no representative)
    assert {n for n in dropped} <= culprits
    scoped_cleared = {culprit for _, _, culprit in trace if culprit not in dropped}
    assert all(units[n].mask != -1 for n in scoped_cleared)  # a culprit that is not dropped only lost its bit


@pytest.mark.parametrize("seed", range(20))
def test_repairs_are_deterministic(seed):
    first = _run(seed, "scc")
    second = _run(seed, "scc")
    assert first[2] == second[2] and first[3] == second[3]
    assert [u.mask for u in first[1]] == [u.mask for u in second[1]]


def test_conflict_cycle_names_the_units_that_contradict_each_other():
    clauses = [(1, 1, 1), (0, 0, 2)]  # unit 1 says not x0, unit 2 says x0
    labels, conflicts = _conflict_cycle(1, clauses)
    assert set(labels) == {1, 2} and conflicts == 1
    assert _conflict_cycle(1, [(0, 0, 3)]) == (None, 0)  # consistent: no cycle


def test_the_relation_cap_and_the_rate_cap_raise_with_the_count_and_rate():
    def theory():  # "not x0" and "x0" are two relation units that contradict each other
        return [_Unit("p0000000", None, -1, [(1, 1)], False, "relation"), _Unit("p0000001", None, -1, [(0, 0)], False, "relation")]

    with pytest.raises(BaseInconsistentError, match="needed 1 relation drops of 2 asked relations .*50.0%.*limit of 25%"):
        _prune_in_process(_BaseCheck(_NoRows(1), theory(), 2), 10**6, 10**6, lambda: 1e9, method="scc",
                          relation_frac=0.25, asked=2)
    with pytest.raises(BaseInconsistentError, match="over --max-relation-drops 0"):
        _prune_in_process(_BaseCheck(_NoRows(1), theory(), 2), 0, 10**6, lambda: 1e9, method="scc")
    dropped = _prune_in_process(_BaseCheck(_NoRows(1), theory(), 2), 10, 10**6, lambda: 1e9, method="scc",
                                relation_frac=0.5, asked=2)
    assert dropped == {1}  # the later of the two units is repaired


def test_an_unknown_method_is_refused():
    n, units = _theory(0)
    with pytest.raises(ValueError, match="MB_PRUNE"):
        _prune_in_process(_BaseCheck(_NoRows(n), units, 2), 10, 10, lambda: 1e9, method="nope")


def _noisy(seed, n=40, units=150):
    """A larger noisy theory: random 2-clauses over n outcomes with random polarities (the named relations are all
    satisfied by "everything false", so they would never conflict), a giant component at this density."""
    rng = random.Random(seed)
    out = []
    for k in range(units):
        i, j = rng.sample(range(n), 2)
        clause = (2 * i + rng.randint(0, 1), 2 * j + rng.randint(0, 1))
        out.append(_Unit(f"p{k:07d}", None, -1, [clause], False, "relation"))
    return n, out


def _noisy_scoped(seed):
    """Small noisy theory with scoped at-most-one groups (a bit per interpretation), so that repairs clear bits too."""
    rng = random.Random(seed)
    n = 8
    _, units = _noisy(seed, n=n, units=25)
    for k in range(4):
        members = sorted(rng.sample(range(n), 3))
        units.append(_Unit(f"g{k:07d}", None, rng.choice([1, 2, 3]), _amo(members), False, "at_most_one", members))
    return n, units


@pytest.mark.parametrize("seed", range(6))
def test_noisy_repairs_are_consistent_deterministic_and_highest_on_their_walk(seed):
    results = []
    for _ in range(2):
        n, units = _noisy(seed)
        check = _BaseCheck(_NoRows(n), units, 1)
        trace = []
        dropped = _prune_in_process(check, 10**6, 10**6, lambda: 1e9, method="scc", trace=trace)
        assert not check.unsat(len(units), dropped, 0), seed  # the base is consistent
        for _interp, cycle, culprit in trace:
            relations = [m for m in cycle if units[m].kind == "relation"]
            assert culprit == max(relations)
        results.append((dropped, trace))
    assert results[0] == results[1]
    n, units = _noisy(seed)
    bisect = _prune_in_process(_BaseCheck(_NoRows(n), units, 1), 10**6, 10**6, lambda: 1e9, method="bisect")
    assert not _BaseCheck(_NoRows(n), units, 1).unsat(len(units), bisect, 0)  # the other repair is consistent too


def test_one_pass_runs_two_searches_per_walk_and_one_component_pass(monkeypatch):
    """Work counter (no timing): a pass makes one strongly-connected-component computation and, for each of the first
    PRUNE_CYCLES_PER_PASS unsatisfied variables, two breadth-first searches (x to not x and back), no more."""
    from mentat_bench.harness import fixed

    n = 300
    clauses = [(2 * i + 1, 2 * (i + 1)) for i in range(n - 1)]  # x_i -> x_{i+1}
    clauses += [(2 * (i + 1) + 1, 2 * i) for i in range(n - 1)]  # x_{i+1} -> x_i: one equivalence chain
    clauses += [(0, 0), (1, 1)]  # x0 and not x0: every variable is unsatisfied
    counts = {"search": 0, "scc": 0}
    real_search, real_scc = fixed._path_labels, fixed._scc

    def counting_search(*args):
        counts["search"] += 1
        return real_search(*args)

    def counting_scc(*args):
        counts["scc"] += 1
        return real_scc(*args)

    monkeypatch.setattr(fixed, "_path_labels", counting_search)
    monkeypatch.setattr(fixed, "_scc", counting_scc)
    walks, bad = fixed._conflict_cycles(n, [(x, y, k) for k, (x, y) in enumerate(clauses)])
    assert bad == n and len(walks) == fixed.PRUNE_CYCLES_PER_PASS
    assert counts == {"search": 2 * fixed.PRUNE_CYCLES_PER_PASS, "scc": 1}
    assert all(walk for walk in walks), "a walk without a unit would be a bindings-only conflict"


def _clauses_of(check, units, dropped, interp):
    """(x, y, label) of the bindings and of every unit live in `interp`, as the repair sees them."""
    return check.clauses(interp, dropped, len(units))


@pytest.mark.parametrize("seed", range(12))
def test_every_walk_is_an_unsatisfiable_subset_of_the_clauses(seed):
    """Trace T1: each walk the repair reads is a conflict on its own: the clauses carrying its labels (and the bindings)
    are unsatisfiable."""
    from mentat_bench.harness import fixed

    n, units = _noisy(seed)
    check = _BaseCheck(_NoRows(n), units, 1)
    clauses = _clauses_of(check, units, set(), 0)
    walks, _ = fixed._conflict_cycles(check.n, clauses)
    assert walks, "the noisy theory has no conflict"
    for walk in walks:
        labels = set(walk)
        subset = [(x, y) for x, y, label in clauses if label < 0 or label in labels]
        assert fixed._unsat_2cnf(check.n, subset), (seed, sorted(labels))


@pytest.mark.parametrize("seed", range(12))
def test_each_applied_repair_had_all_its_walk_units_live(seed, monkeypatch):
    """Trace T1: when a culprit is applied, every unit on its cycle still holds in that interpretation (an earlier repair
    of the same pass may have broken the cycle; such a culprit is skipped). Liveness is checked here from the drop state
    directly, not by the repair's own helper; after the repair, every interpretation is satisfiable."""
    from mentat_bench.harness import fixed

    n, units = _noisy_scoped(seed)
    check = _BaseCheck(_NoRows(n), units, 2)
    trace: list = []
    real_drop = fixed._drop_culprit

    def checked_drop(chk, dropped, children, culprit, bit, ledger):
        interp, cycle, applied = trace[-1]
        assert applied == culprit
        for n_unit in cycle:
            if n_unit < 0:
                continue
            unit = chk.units[n_unit]
            assert n_unit not in dropped and not unit.dropped, (seed, n_unit)
            assert (unit.mask >> interp) & 1, (seed, interp, n_unit)
        return real_drop(chk, dropped, children, culprit, bit, ledger)

    monkeypatch.setattr(fixed, "_drop_culprit", checked_drop)
    dropped = fixed._prune_in_process(check, 10**6, 10**6, lambda: 1e9, method="scc", trace=trace)
    assert trace, "no repair was applied"
    for interp in range(check.n_interps):
        assert not check.unsat(len(units), dropped, interp), (seed, interp)  # unsat-after-repair, checked directly


def test_the_liveness_check_skips_culprits_on_noisy_theories(monkeypatch):
    """The skip is exercised: some culprit of a pass is found broken by an earlier repair of that pass (else the check
    would be dead code on these theories)."""
    from mentat_bench.harness import fixed

    outcomes: list[bool] = []
    real_live = fixed._cycle_live

    def counting(*args):
        ok = real_live(*args)
        outcomes.append(ok)
        return ok

    monkeypatch.setattr(fixed, "_cycle_live", counting)
    for seed in range(12):
        n, units = _noisy_scoped(seed)
        fixed._prune_in_process(_BaseCheck(_NoRows(n), units, 2), 10**6, 10**6, lambda: 1e9, method="scc")
    assert outcomes.count(False) > 0, "no culprit was skipped: the liveness check never decided anything here"


@pytest.mark.parametrize("seed", range(12))
def test_ledger_counts_equal_the_distinct_repairs(seed):
    """Trace T5: a scoped group that loses one interpretation is one repair, counted once; the ledger equals the number
    of distinct (interpretation, unit) repairs in the trace."""
    from mentat_bench.harness import fixed

    n, units = _noisy_scoped(seed)
    check = _BaseCheck(_NoRows(n), units, 2)
    ledger = fixed._DropLedger(10**6, 10**6, None, 0)
    trace: list = []
    fixed._prune_scc(check, ledger, lambda: 1e9, fixed._PruneProgress(), trace)
    keys = [(interp, culprit) for interp, _, culprit in trace]
    assert len(set(keys)) == len(keys), "a unit was repaired twice in one interpretation"
    assert ledger.relation + ledger.group == len(keys)


def _reference_path(adj_t, adj_l, comp, src, dst):
    """Labels of a shortest path src -> dst inside src's component, by a breadth-first search that runs to the end."""
    parent = {src: None}
    queue = [src]
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        for target, label in zip(adj_t[node], adj_l[node]):
            if target not in parent and comp[target] == comp[src]:
                parent[target] = (node, label)
                queue.append(target)
    labels, node = [], dst
    while parent[node] is not None:
        node, label = parent[node]
        labels.append(label)
    return labels


@pytest.mark.parametrize("seed", range(40))
def test_the_early_exit_search_equals_a_full_breadth_first_search(seed):
    """The shortest-path search stops when its target is found; on random digraphs it must give the path a full search
    gives, for every pair inside a component."""
    from mentat_bench.harness import fixed

    rng = random.Random(seed)
    nodes = rng.randint(4, 30)
    adj_t = [[] for _ in range(nodes)]
    adj_l = [[] for _ in range(nodes)]
    for _ in range(rng.randint(nodes, 4 * nodes)):
        a, b = rng.randrange(nodes), rng.randrange(nodes)
        adj_t[a].append(b)
        adj_l[a].append(rng.randint(-1, 50))
    comp = fixed._scc(adj_t)
    pairs = [(a, b) for a in range(nodes) for b in range(nodes) if a != b and comp[a] == comp[b]]
    for a, b in pairs[:200]:
        assert fixed._path_labels(adj_t, adj_l, comp, a, b) == _reference_path(adj_t, adj_l, comp, a, b), (seed, a, b)

"""Offline gold entailment oracle (MEASUREMENT ONLY), the admission study's (b0/gold.py): per-interpretation 2-CNF closure
of <fixture>/gold/theory.json plus the gold bindings. Constraint ops outside 2-CNF are ignored (counted in `ignored`), so
`entailed` is a sound lower bound of gold entailment."""
import json
from pathlib import Path

from mentat_bench.harness import fixed as F


def _lit(e, idx):
    if e["op"] == "var":
        return 2 * idx[e["id"]]
    if e["op"] == "not":
        return _lit(e["a"], idx) ^ 1
    raise ValueError(e)


def _clauses(e, idx):
    op = e["op"]
    if op == "implies":
        a, b = _lit(e["a"], idx), _lit(e["b"], idx)
        return [(a ^ 1, b)]
    if op == "iff":
        a, b = _lit(e["a"], idx), _lit(e["b"], idx)
        return [(a ^ 1, b), (a, b ^ 1)]
    if op == "or":
        ms = [_lit(x, idx) for x in e["args"]]
        if len(ms) > 2:
            raise ValueError(op)
        return [tuple(ms) if len(ms) == 2 else (ms[0], ms[0])]
    if op in ("atmost", "exactly") and e["k"] == 1:
        ms = [_lit(x, idx) for x in e["args"]]
        cl = [(ms[p] ^ 1, ms[q] ^ 1) for p in range(len(ms)) for q in range(p + 1, len(ms))]
        if op == "exactly" and len(ms) == 2:
            cl.append((ms[0], ms[1]))
        return cl
    raise ValueError(op)


class Gold:
    def __init__(self, fixture_dir: Path, ids):
        theory = json.loads((fixture_dir / "gold" / "theory.json").read_text(encoding="utf-8"))
        interps = theory["interpretations"]
        idx = {o: i for i, o in enumerate(ids)}
        n = len(ids)
        self.ignored = {}
        per = [[] for _ in interps]
        for c in theory["constraints"]:
            try:
                cl = _clauses(c["expr"], idx)
            except (ValueError, KeyError):
                self.ignored[c["expr"]["op"]] = self.ignored.get(c["expr"]["op"], 0) + 1
                continue
            for k in (range(len(interps)) if c.get("interps") is None else [interps.index(x) for x in c["interps"]]):
                per[k].extend(cl)
        defining, bcl = {}, []
        for line in open(fixture_dir / "gold" / "bindings.jsonl", encoding="utf-8"):
            b = json.loads(line)
            if b["status"] != "bound" or b["id"] not in idx:
                continue
            k = idx[b["id"]]
            f, nf = defining.setdefault(b["class"], (k, bool(b["negated"])))
            m = 2 * f + (nf != bool(b["negated"]))
            bcl += [(2 * k + 1, m), (2 * k, m ^ 1)]
        for cls in per:
            cls.extend(bcl)
        self.reach, self.comp = [], []
        for cls in per:
            adj = [[] for _ in range(2 * n)]
            for x, y in cls:
                adj[x ^ 1].append(y)
                adj[y ^ 1].append(x)
            comp = F._scc(adj)
            nc = max(comp) + 1
            succ = [set() for _ in range(nc)]
            for u in range(2 * n):
                for v in adj[u]:
                    if comp[u] != comp[v]:
                        succ[comp[u]].add(comp[v])
            reach = [0] * nc
            for c in range(nc):  # Tarjan ids: sinks first
                r = 1 << c
                for d in succ[c]:
                    r |= reach[d]
                reach[c] = r
            self.reach.append(reach)
            self.comp.append(comp)

    def r(self, k, u, v):
        return (self.reach[k][self.comp[k][u]] >> self.comp[k][v]) & 1

    def clause_entailed(self, x, y):  # gold |= (x or y) in every interpretation
        return all(self.r(k, x ^ 1, y) for k in range(len(self.reach)))

    def entailed(self, i, j, rel):
        a, b = 2 * i, 2 * j
        qs = {"IMPLIES": [(a, b)], "IMPLIED_BY": [(b, a)], "EXCLUDES": [(a, b ^ 1)], "EQUIV": [(a, b), (b, a)]}[rel]
        return all(self.r(k, u, v) for k in range(len(self.reach)) for u, v in qs)

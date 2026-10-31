"""Native open-discovery adjudication (mentat-bench-v0.1.md sections 4, 7 and 8).

Subcommands (python -m mentat_bench.adjudication):
  frame     submission dir -> normalized assertion registry (frame.jsonl, frame_summary.json)
  sample    registry -> stratified probability sample with recorded inclusion probabilities
  export    sample -> blinded annotator packets, coordinator assignments, import schema
  import    annotations (+ adjudicator file) -> resolved verdicts and agreement statistics
  estimate  sample + resolved verdicts -> Hajek precision with design-based and exact intervals

Precision is a diagnostic over emitted claims, never a gate. Gold is never read here: an omitted gold edge
cannot invalidate an emitted claim, and packets carry no gold labels, model names or provenance beyond source refs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

try:  # scipy is optional; without it the regularised incomplete beta is computed here
    from scipy.special import betainc as _scipy_betainc
    from scipy.special import betaincinv as _scipy_betaincinv
except ImportError:  # pragma: no cover - depends on the environment
    _scipy_betainc = None
    _scipy_betaincinv = None

VERDICTS = ("correct", "incorrect", "unjudgeable")
JUDGED = ("correct", "incorrect")
MAX_CLAUSES = 512  # CNF blow-up cap; larger composites are hashed as opaque atoms (shortcut, see docs)
ANNOTATOR_ADJUDICATOR = "adjudicator"
DOMAINS: dict[str, Callable[[dict], bool]] = {
    "combined": lambda r: True,
    "bindings": lambda r: r["kind"] == "binding",
    "constraints": lambda r: r["kind"] != "binding",
    "pairwise": lambda r: r["kind"] == "pairwise",
    "group": lambda r: r["kind"] == "group",
    "conditional": lambda r: r["cond"],
}


class AdjudicationError(ValueError):
    """Invalid input or an inconsistent design; the CLI reports it and exits 2."""


# --------------------------------------------------------------------------------------------- file helpers


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------------------------- formulas
# NNF formula nodes: ("T",), ("F",), ("L", atom, sign), ("A", [kids]), ("O", [kids]).
# Atoms: "v:<outcome id>" for a Boolean variable, "atmost:k:<+/-lits>" / "atleast:..." / "exactly:..." for a
# cardinality over literal arguments, "x:<sha256>" for an opaque composite (weighted sums, wide CNFs).


def _mk(kind: str, kids: list) -> tuple:
    ident, absorb = (("T",), ("F",)) if kind == "A" else (("F",), ("T",))
    out: list = []
    for kid in kids:
        if kid == ident:
            continue
        if kid == absorb:
            return absorb
        if kid[0] == kind:
            out.extend(kid[1])
        else:
            out.append(kid)
    if not out:
        return ident
    return out[0] if len(out) == 1 else (kind, out)


def _literal_of(aliases: dict, name: str) -> tuple[str, bool] | None:
    """(outcome id, sign) when alias `name` is a single literal (ref/not/var chain), else None."""
    sign, seen = True, set()
    expr: dict = {"op": "ref", "name": name}
    while True:
        op = expr.get("op")
        if op == "ref":
            if expr["name"] in seen or expr["name"] not in aliases:
                return None
            seen.add(expr["name"])
            expr = aliases[expr["name"]]
        elif op == "not":
            sign = not sign
            expr = expr["a"]
        elif op == "var":
            return expr["id"], sign
        else:
            return None


class _Canon:
    """Expr -> NNF formula. Collects the outcome ids reached (through aliases) in `self.ids`."""

    def __init__(self, aliases: dict):
        self.aliases = aliases
        self.ids: set[str] = set()

    def nnf(self, e: dict, neg: bool = False, stack: tuple = ()) -> tuple:
        op = e.get("op")
        if op == "true":
            return ("F",) if neg else ("T",)
        if op == "false":
            return ("T",) if neg else ("F",)
        if op == "var":
            self.ids.add(e["id"])
            return ("L", "v:" + e["id"], not neg)
        if op == "ref":
            name = e["name"]
            if name in stack:
                raise AdjudicationError(f"alias_cycle:{name}")
            if name not in self.aliases:
                raise AdjudicationError(f"unknown_alias:{name}")
            return self.nnf(self.aliases[name], neg, stack + (name,))
        if op == "not":
            return self.nnf(e["a"], not neg, stack)
        if op in ("and", "or"):
            kids = [self.nnf(a, neg, stack) for a in e["args"]]
            return _mk("A" if (op == "and") != neg else "O", kids)
        if op == "implies":
            a, b = e["a"], e["b"]
            if neg:
                return _mk("A", [self.nnf(a, False, stack), self.nnf(b, True, stack)])
            return _mk("O", [self.nnf(a, True, stack), self.nnf(b, False, stack)])
        if op == "iff":
            a, b = e["a"], e["b"]
            if neg:
                return _mk("O", [_mk("A", [self.nnf(a, False, stack), self.nnf(b, True, stack)]),
                                 _mk("A", [self.nnf(a, True, stack), self.nnf(b, False, stack)])])
            return _mk("A", [_mk("O", [self.nnf(a, True, stack), self.nnf(b, False, stack)]),
                             _mk("O", [self.nnf(a, False, stack), self.nnf(b, True, stack)])])
        if op in ("atmost", "atleast", "exactly"):
            return self._card(op, int(e["k"]), e["args"], neg, stack)
        if op == "wsum":  # shortcut: weighted sums are hashed as opaque atoms, not canonicalised
            self.ids.update(_var_ids(e))
            return ("L", "x:" + _sha(json.dumps(e, sort_keys=True)), not neg)
        raise AdjudicationError(f"unknown_op:{op}")

    def _card(self, op: str, k: int, args: list, neg: bool, stack: tuple) -> tuple:
        if op == "exactly" and neg:  # not exactly k  ==  at least k+1, or at most k-1
            return _mk("O", [self._card("atleast", k + 1, args, False, stack),
                             self._card("atmost", k - 1, args, False, stack)])
        if op == "atmost" and neg:
            op, k = "atleast", k + 1
        elif op == "atleast" and neg:
            op, k = "atmost", k - 1
        lits = [self.nnf(a, False, stack) for a in args]
        n = len(args)
        if not all(f[0] == "L" for f in lits):  # composite arguments: opaque atom over the normalised shape
            return ("L", "x:" + _sha(json.dumps([op, k, lits], sort_keys=True)), True)
        L = [(f[1], f[2]) for f in lits]
        pos = [("L", a, s) for a, s in L]
        negs = [("L", a, not s) for a, s in L]
        if op == "atmost":
            if k < 0:
                return ("F",)
            if k >= n:
                return ("T",)
            if k == 0:
                return _mk("A", negs)
            if n == 2 and k == 1:  # pairwise form of an at-most-one over two literals
                return _mk("O", negs)
        elif op == "atleast":
            if k <= 0:
                return ("T",)
            if k > n:
                return ("F",)
            if k == n:
                return _mk("A", pos)
            if k == 1:
                return _mk("O", pos)
        else:  # exactly, not negated here
            if k < 0 or k > n:
                return ("F",)
            if k == 0:
                return _mk("A", negs)
            if k == n:
                return _mk("A", pos)
            if n == 2 and k == 1:  # exactly one of two literals
                return _mk("A", [_mk("O", pos), _mk("O", negs)])
        key = f"{op}:{k}:" + ",".join(sorted(("+" if s else "-") + a for a, s in L))
        return ("L", key, True)


def _var_ids(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        if obj.get("op") == "var":
            found.add(obj["id"])
        for value in obj.values():
            found |= _var_ids(value)
    elif isinstance(obj, list):
        for value in obj:
            found |= _var_ids(value)
    return found


def _simplify(clauses: list[frozenset]) -> list[frozenset]:
    """Equivalence-preserving CNF cleanup: tautologies, unit propagation, subsumption (A or (A and B) = A)."""
    work: list[frozenset] = []
    for clause in clauses:
        if not clause:
            return [frozenset()]  # a false conjunct makes the whole conjunction false
        if {a for a, s in clause if s} & {a for a, s in clause if not s}:
            continue  # tautological clause
        work.append(clause)
    changed = True
    while changed:
        changed = False
        units = {next(iter(c)) for c in work if len(c) == 1}
        if any((a, not s) in units for a, s in units):
            return [frozenset()]  # two complementary units
        nxt: list[frozenset] = []
        for c in work:
            if len(c) > 1 and c & units:
                changed = True  # satisfied by a unit
                continue
            reduced = frozenset(lit for lit in c if (lit[0], not lit[1]) not in units) if len(c) > 1 else c
            if reduced != c:
                changed = True
            if not reduced:
                return [frozenset()]
            nxt.append(reduced)
        work = nxt
    kept: list[frozenset] = []
    for c in sorted(set(work), key=lambda c: (len(c), sorted(c))):
        if not any(k <= c for k in kept):
            kept.append(c)
    return kept


def _cnf(f: tuple) -> list[frozenset] | None:
    """Clause set of a formula: [] is true, [frozenset()] is false; None when the CNF exceeds MAX_CLAUSES."""
    kind = f[0]
    if kind == "T":
        return []
    if kind == "F":
        return [frozenset()]
    if kind == "L":
        return [frozenset([(f[1], f[2])])]
    if kind == "A":
        parts: list[frozenset] = []
        for kid in f[1]:
            sub = _cnf(kid)
            if sub is None:
                return None
            parts.extend(sub)
            if len(parts) > MAX_CLAUSES:
                return None
        return _simplify(parts)
    acc = [frozenset()]  # the empty disjunction is false
    for kid in f[1]:
        sub = _cnf(kid)
        if sub is None:
            return None
        if not sub:
            return []  # a true disjunct makes the disjunction true
        acc = [x | y for x in acc for y in sub]
        if len(acc) > MAX_CLAUSES:
            return None
    return _simplify(acc)


def _lits(clauses: list[frozenset]) -> list[list[str]]:
    """Canonical clause set: literals are '+atom' / '-atom', sorted within and across clauses."""
    return sorted(sorted(("+" if s else "-") + a for a, s in clause) for clause in clauses)


def _key(clauses: list[list[str]], interps: list[str] | None = None) -> str:
    """Canonical form: the interpretation scope (null = every interpretation) and the sorted clause set."""
    return json.dumps([interps, clauses], ensure_ascii=False, separators=(",", ":"))


def _flip(clause: list[str]) -> list[str]:
    return sorted(("-" if lit[0] == "+" else "+") + lit[1:] for lit in clause)


def _stratum(clauses: list[list[str]]) -> str:
    pairwise = all(len(c) <= 2 and all(lit[1:].startswith("v:") for lit in c) for c in clauses)
    return "pairwise" if pairwise else "group"


# --------------------------------------------------------------------------------------------- source texts


class _Texts:
    """Source texts from corpus.jsonl (question, outcome label, event title) addressed by REF."""

    def __init__(self, corpus: Path):
        self.question: dict[str, str] = {}
        self.label: dict[tuple[str, str], str] = {}
        self.event: dict[str, str] = {}
        self.event_refs: dict[str, str] = {}
        for rec in _read_jsonl(corpus):
            _, market, idx = rec["id"].split(":")[:3]
            self.question.setdefault(market, rec.get("text"))
            self.label[(market, idx)] = rec.get("label")
            self.event.setdefault(market, rec.get("event_title"))

    def index_bindings(self, bindings: list[dict]) -> None:
        """Map event REFs (which name no text in provenance) to the event title of the market that cites them."""
        for b in bindings:
            market = b["id"].split(":")[1]
            for ref in b.get("evidence", []):
                if ref.startswith("src:event:"):
                    self.event_refs.setdefault(ref, self.event.get(market))

    def ref_text(self, ref: str) -> str | None:
        if ref.startswith("src:event:"):
            return self.event_refs.get(ref)
        parts = ref.split(":")
        market = parts[1]
        if parts[2:] == ["question"]:
            return self.question.get(market)
        if parts[2] == "outcome_label" and len(parts) == 4:
            return self.label.get((market, parts[3]))
        if parts[2:] == ["event_title"]:
            return self.event.get(market)
        return None

    def outcome(self, oid: str) -> str:
        market, idx = oid.split(":")[1], oid.split(":")[2]
        question, label = self.question.get(market), self.label.get((market, idx))
        return f"[{question} -> {label}]" if question and label else "[source text missing]"


def _src_refs(refs: list[str]) -> list[str]:
    return [r for r in refs if r.startswith("src:")]  # axiom REFs are never shown to annotators


def _outcome_refs(oid: str) -> list[str]:
    market, idx = oid.split(":")[1], oid.split(":")[2]
    return [f"src:{market}:question", f"src:{market}:outcome_label:{idx}"]


INTERP_PATTERN = re.compile(r"^(incl|excl)_tie_(one|none)$")


def _scope_phrase(label: str) -> str:
    """Plain English for the interpretation labels the theories use; any other label is quoted as it is."""
    match = INTERP_PATTERN.match(label)
    if not match:
        return f"'{label}'"
    endpoints = "interval endpoints are inclusive" if match.group(1) == "incl" else "interval endpoints are exclusive"
    ties = "ties resolve to one side" if match.group(2) == "one" else "ties resolve to neither side"
    return f"in which {endpoints} and {ties}"


def _scope_text(interps: list[str] | None) -> str:
    if not interps:
        return ""
    if len(interps) == 1:
        return f"Under the interpretation {_scope_phrase(interps[0])}: "
    return f"Under each of these interpretations ({'; '.join(_scope_phrase(i) for i in interps)}): "


def _claim(clauses: list[list[str]], texts: _Texts, interps: list[str] | None = None) -> str:
    """Plain-English rendering of the canonical clause set (no class names, no gold, no model names)."""
    prefix = _scope_text(interps)
    if not clauses:
        return prefix + "always true"
    pair = _pair_text(clauses, texts)
    if pair is not None:
        return prefix + pair
    return prefix + "; and ".join(_clause_text(clause, texts) for clause in clauses)


def _pair_text(clauses: list[list[str]], texts: _Texts) -> str | None:
    """Two-clause equivalences: exactly-one (xor) and if-and-only-if read as one sentence."""
    if len(clauses) != 2 or any(len(c) != 2 for c in clauses):
        return None
    if any(not lit[1:].startswith("v:") for c in clauses for lit in c):
        return None
    first, second = clauses
    if second != _flip(first):
        return None
    if first[0][0] == first[1][0]:
        return f"exactly one of {texts.outcome(_outcome_id(first[0]))} and {texts.outcome(_outcome_id(first[1]))} holds"
    neg = next(lit for lit in first if lit[0] == "-")
    pos = next(lit for lit in first if lit[0] == "+")
    return f"{texts.outcome(_outcome_id(neg))} holds if and only if {texts.outcome(_outcome_id(pos))} holds"


def _clause_text(clause: list[str], texts: _Texts) -> str:
    if not clause:
        return "impossible (contradiction)"
    if any(not lit[1:].startswith("v:") for lit in clause):
        if len(clause) == 1:
            return _card_or_lit_text(clause[0], texts)
        return "at least one of: " + "; or ".join(_card_or_lit_text(lit, texts) for lit in clause)
    if len(clause) == 1:
        lit = clause[0]
        body = texts.outcome(_outcome_id(lit))
        return f"{body} holds" if lit[0] == "+" else f"{body} does not hold"
    if len(clause) == 2:
        (sa, la), (sb, lb) = (clause[0][0], clause[0]), (clause[1][0], clause[1])
        a, b = texts.outcome(_outcome_id(la)), texts.outcome(_outcome_id(lb))
        if sa == "-" and sb == "+":
            return f"if {a} holds then {b} holds"
        if sa == "+" and sb == "-":
            return f"if {b} holds then {a} holds"
        if sa == "+":
            return f"at least one of {a} and {b} holds"
        return f"{a} and {b} do not both hold"
    return "at least one of: " + ", ".join(_lit_body(lit, texts) for lit in clause) + " holds"


def _outcome_id(lit: str) -> str:
    return lit[3:]  # '+v:<outcome id>'


def _lit_body(lit: str, texts: _Texts) -> str:
    body = texts.outcome(_outcome_id(lit))
    return body if lit[0] == "+" else f"NOT {body}"


def _card_or_lit_text(lit: str, texts: _Texts) -> str:
    sign, atom = lit[0] == "+", lit[1:]
    if atom.startswith("v:"):
        return _lit_body(lit, texts) + (" holds" if sign else " does not hold")
    if atom.startswith("x:"):
        return "a composite condition stated in the sources" if sign else "the negation of a composite condition"
    op, k, args = atom.split(":", 2)
    words = {"atmost": "at most", "atleast": "at least", "exactly": "exactly"}
    text = f"{words[op]} {k} of these hold: " + "; ".join(_lit_body(arg, texts) for arg in args.split(","))
    return text if sign else f"NOT ({text})"


# --------------------------------------------------------------------------------------------- registry


def _components(row_outcomes: list[list[str]]) -> dict[str, str]:
    """Connected components over outcome ids: outcome -> cluster id (smallest outcome id in the component)."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for ids in row_outcomes:
        ids = sorted(ids)
        for other in ids:
            root_a, root_b = find(ids[0]), find(other)
            if root_a != root_b:
                parent[root_b] = root_a
    smallest: dict[str, str] = {}
    for oid in sorted(parent):
        root = find(oid)
        smallest.setdefault(root, oid)
    return {oid: smallest[find(oid)] for oid in parent}


def _complement_pair(ids: list[str]) -> list[list[str]]:
    """Canonical form of 'exactly one of two outcomes of one market holds'."""
    a, b = "v:" + ids[0], "v:" + ids[1]
    return _lits([frozenset({(a, True), (b, True)}), frozenset({(a, False), (b, False)})])


def _own_market_only(prov: list[str], market: str, events: dict[str, set[str]]) -> bool:
    """True when every source REF belongs to the market: its question, its outcome labels, its event title and the
    event titles its bindings cite. Axiom REFs are ignored. Another market's or event's source is not own."""
    own = {f"src:{market}:question", f"src:{market}:event_title"} | events.get(market, set())
    return all(r.startswith("ax:") or r in own or r.startswith(f"src:{market}:outcome_label:") for r in prov)


def _merge_complement_pairs(rows: dict[str, dict], counts: Counter) -> None:
    """Merge the two implications of an equivalence (C and flip(C)) into one iff row (equivalent shapes).

    Only rows with the same interpretation scope merge: the scope is part of the key.
    """
    for key in sorted(rows):
        row = rows.get(key)
        if row is None or row["kind"] != "pairwise" or len(row["clauses"]) != 1 or len(row["clauses"][0]) != 2:
            continue
        clause = row["clauses"][0]
        flipped = _flip(clause)
        fkey = _key([flipped], row["interps"])
        other = rows.get(fkey)
        if other is None or other is row:
            continue
        new_clauses = sorted([clause, flipped])
        nkey = _key(new_clauses, row["interps"])
        rows.pop(key)
        rows.pop(fkey)
        target = rows.get(nkey)
        if target is None:
            target = row
            target.update(canonical=nkey, clauses=new_clauses)
            rows[nkey] = target
        else:
            counts["duplicates_merged"] += 1
        target["source_ids"] = sorted(set(target["source_ids"]) | set(other["source_ids"]) | set(row["source_ids"]))
        target["outcomes"] |= other["outcomes"]
        target["refs"] |= other["refs"]
        counts["merged_implication_pairs"] += 1


def build_frame(submission: Path, corpus: Path) -> tuple[list[dict], dict]:
    """Normalized assertion registry for a submission directory. Returns (rows sorted by id, summary)."""
    submission = Path(submission)
    bindings = _read_jsonl(submission / "bindings.jsonl")
    theory = json.loads((submission / "theory.json").read_text(encoding="utf-8-sig"))
    aliases = theory.get("aliases", {})
    texts = _Texts(Path(corpus))
    texts.index_bindings(bindings)
    market_size = Counter(b["id"].split(":")[1] for b in bindings)  # one binding per eligible outcome
    market_events: dict[str, set[str]] = defaultdict(set)
    for b in bindings:
        market_events[b["id"].split(":")[1]].update(r for r in b.get("evidence", []) if r.startswith("src:event:"))
    canon = _Canon(aliases)
    rows: dict[str, dict] = {}
    counts: Counter = Counter()

    def add(clauses: list[list[str]], kind: str, interps: list[str] | None, ids: set[str], refs: list[str],
            source_id: str) -> None:
        key = _key(clauses, interps)
        row = rows.get(key)
        if row is None:
            rows[key] = {"canonical": key, "clauses": clauses, "kind": kind, "interps": interps,
                         "outcomes": set(ids), "refs": set(refs), "source_ids": [source_id]}
            return
        counts["duplicates_merged"] += 1
        row["source_ids"].append(source_id)
        row["outcomes"] |= ids
        row["refs"] |= set(refs)

    for b in bindings:  # bindings first: a constraint that restates a binding merges into the binding row
        if b.get("status") != "bound":
            counts["unresolved_bindings"] += 1  # no claim: the outcome stays UNRESOLVED with its missing codes
            continue
        if not b.get("class"):
            continue
        counts["bindings_bound"] += 1
        x, neg = b["id"], bool(b.get("negated"))
        lit = _literal_of(aliases, b["class"])
        if lit is not None and lit[0] == x and lit[1] != neg:
            counts["excluded_identity_binding"] += 1
            continue
        class_ref: dict = {"op": "ref", "name": b["class"]}
        expr = {"op": "iff", "a": {"op": "var", "id": x}, "b": {"op": "not", "a": class_ref} if neg else class_ref}
        canon.ids = set()
        clauses = _cnf(canon.nnf(expr))
        if clauses is None:
            raise AdjudicationError(f"binding_too_large:{x}")
        if not clauses:
            counts["excluded_tautology"] += 1
            continue
        clauses_lits = _lits(clauses)
        other = lit[0] if lit is not None else None
        if (other is not None and canon.ids == {x, other} and x.split(":")[1] == other.split(":")[1]
                and market_size[x.split(":")[1]] == 2 and clauses_lits == _complement_pair(sorted(canon.ids))):
            counts["excluded_definition"] += 1  # the binary complement of the market's own two outcomes
            continue
        add(clauses_lits, "binding", None, canon.ids, _src_refs(b.get("evidence", [])), f"bind:{x}")

    for c in theory.get("constraints", []):
        counts["constraints_in"] += 1
        canon.ids = set()
        formula = canon.nnf(c["expr"])
        ids = set(canon.ids)
        prov = c.get("provenance", [])
        interps = sorted(c["interps"]) if c.get("interps") else None  # null = every interpretation
        markets = {i.split(":")[1] for i in ids}
        clauses = _cnf(formula)
        if clauses == []:  # true in every world: a tautology, whatever its provenance
            counts["excluded_tautology"] += 1
            continue
        if (len(markets) == 1 and interps is None and any(r.startswith("ax:") for r in prov)
                and _own_market_only(prov, next(iter(markets)), market_events)):
            counts["excluded_axiom_restatement"] += 1
            continue
        if clauses is None:
            counts["opaque_composites"] += 1
            clauses_lits = [["+x:" + _sha(json.dumps(formula, sort_keys=True))]]
        else:
            clauses_lits = _lits(clauses)
            if (len(markets) == 1 and len(ids) == 2 and interps is None
                    and market_size[next(iter(markets))] == 2 and clauses_lits == _complement_pair(sorted(ids))):
                counts["excluded_definition"] += 1  # the two outcomes of one binary market are complementary
                continue
        add(clauses_lits, _stratum(clauses_lits), interps, ids, _src_refs(prov), f"c:{c['id']}")

    _merge_complement_pairs(rows, counts)

    ordered = list(rows.values())
    cluster_of = _components([sorted(r["outcomes"]) for r in ordered])
    sizes: Counter = Counter()
    row_cluster: list[str] = []
    for r in ordered:
        cid = cluster_of[min(r["outcomes"])] if r["outcomes"] else "const:" + _sha(r["canonical"])
        row_cluster.append(cid)
        sizes[cid] += 1
    ranked = sorted(sizes, key=lambda c: (sizes[c], c))
    tercile_of = {c: f"T{min(2, 3 * i // len(ranked)) + 1}" for i, c in enumerate(ranked)}
    bounds: dict[str, list[int]] = {}
    for c in ranked:
        lo_hi = bounds.setdefault(tercile_of[c], [sizes[c], sizes[c]])
        lo_hi[0], lo_hi[1] = min(lo_hi[0], sizes[c]), max(lo_hi[1], sizes[c])

    out: list[dict] = []
    for r, cid in zip(ordered, row_cluster):
        refs = set(r["refs"])
        for oid in r["outcomes"]:
            refs.update(_outcome_refs(oid))
        sources = [{"ref": ref, "text": texts.ref_text(ref)} for ref in sorted(refs)]
        counts["missing_source_texts"] += sum(1 for s in sources if s["text"] is None)
        cond = r["interps"] is not None
        out.append({
            "assertion_id": _sha(r["canonical"]),
            "stratum": r["kind"] + ("|cond" if cond else ""),
            "kind": r["kind"],
            "cond": cond,
            "interps": r["interps"],
            "canonical": r["canonical"],
            "claim": _claim(r["clauses"], texts, r["interps"]),
            "sources": sources,
            "outcomes": sorted(r["outcomes"]),
            "source_ids": sorted(r["source_ids"]),
            "cluster_id": cid,
            "cluster_size": sizes[cid],
            "tercile": tercile_of[cid],
        })
    out.sort(key=lambda row: row["assertion_id"])
    by_kind = Counter(r["kind"] for r in out)
    summary = {
        "inputs": {name: _sha_file(submission / name) for name in ("bindings.jsonl", "theory.json", "provenance.jsonl")},
        "corpus_sha256": _sha_file(Path(corpus)),
        "counts": {
            "bindings_bound": counts["bindings_bound"],
            "constraints_in": counts["constraints_in"],
            "excluded_identity_binding": counts["excluded_identity_binding"],
            "excluded_axiom_restatement": counts["excluded_axiom_restatement"],
            "excluded_definition": counts["excluded_definition"],
            "excluded_tautology": counts["excluded_tautology"],
            "unresolved_bindings": counts["unresolved_bindings"],
            "duplicates_merged": counts["duplicates_merged"],
            "merged_implication_pairs": counts["merged_implication_pairs"],
            "opaque_composites": counts["opaque_composites"],
            "missing_source_texts": counts["missing_source_texts"],
            "rows": len(out),
            "rows_binding": by_kind["binding"],
            "rows_pairwise": by_kind["pairwise"],
            "rows_group": by_kind["group"],
            "rows_conditional": sum(1 for r in out if r["cond"]),
            "clusters": len(ranked),
        },
        "cluster_size_terciles": bounds,
    }
    return out, summary


# --------------------------------------------------------------------------------------------- sampling


def _allocate(total: int, sizes: dict[str, int]) -> dict[str, int]:
    """Proportional allocation n_h (largest remainder), 1 <= n_h <= N_h when total < N, census when total >= N."""
    labels = sorted(sizes)
    big_n = sum(sizes.values())
    if not labels:
        return {}
    if total < len(labels):
        raise AdjudicationError(f"n={total} is smaller than the number of strata ({len(labels)})")
    if total >= big_n:
        return dict(sizes)
    raw = {h: total * sizes[h] / big_n for h in labels}
    # a stratum needs two sampled units for a variance; fall back to one per stratum when the budget is tight
    floor = 2 if total >= sum(min(2, sizes[h]) for h in labels) else 1
    lo = {h: min(floor, sizes[h]) for h in labels}
    alloc = {h: min(sizes[h], max(lo[h], math.floor(raw[h]))) for h in labels}
    order = sorted(labels, key=lambda h: (-(raw[h] - math.floor(raw[h])), h))
    diff = total - sum(alloc.values())
    while diff != 0:
        moved = False
        for h in order:
            if diff > 0 and alloc[h] < sizes[h]:
                alloc[h] += 1
                diff -= 1
                moved = True
            elif diff < 0 and alloc[h] > lo[h]:
                alloc[h] -= 1
                diff += 1
                moved = True
            if diff == 0:
                break
        if not moved:
            raise AdjudicationError("allocation failed")
    return alloc


def draw_sample(frame: list[dict], design: str, n: int, seed: int, within: int | None = None) -> tuple[list[dict], dict]:
    """Seeded stratified SRS without replacement (design 'units') or two-stage cluster sampling ('clusters').

    units: strata = kind x cluster-size tercile; n = number of assertions. Inclusion pi = n_h / N_h.
    clusters: strata = cluster-size tercile; n = number of clusters (SRS within strata); within = rows per
    sampled cluster (None = all rows). pi = (m_h / M_h) * (k_c / M_c).
    """
    if design not in ("units", "clusters"):
        raise AdjudicationError(f"unknown design {design}")
    out: list[dict] = []
    strata_info: dict[str, dict] = {}
    if design == "units":
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in frame:
            groups[f"{row['stratum']}|{row['tercile']}"].append(row)
        alloc = _allocate(n, {h: len(v) for h, v in groups.items()})
        for label in sorted(groups):
            members = sorted(groups[label], key=lambda r: r["assertion_id"])
            k = alloc[label]
            picked = random.Random(f"{seed}|{label}").sample(members, k)
            pi = k / len(members)
            strata_info[label] = {"N": len(members), "M": len(members), "n": k, "m": k}
            for row in picked:
                out.append(_sample_row(row, label, pi, 1.0))
    else:
        clusters: dict[str, list[dict]] = defaultdict(list)
        for row in frame:
            clusters[row["cluster_id"]].append(row)
        by_tercile: dict[str, list[str]] = defaultdict(list)
        for cid in clusters:
            by_tercile[clusters[cid][0]["tercile"]].append(cid)
        alloc = _allocate(n, {t: len(v) for t, v in by_tercile.items()})
        for tercile in sorted(by_tercile):
            cids = sorted(by_tercile[tercile])
            m_h = alloc[tercile]
            chosen = random.Random(f"{seed}|{tercile}").sample(cids, m_h)
            pi_first = m_h / len(cids)
            rows_in = sum(len(clusters[c]) for c in cids)
            strata_info[tercile] = {"N": rows_in, "M": len(cids), "n": 0, "m": m_h}
            for cid in sorted(chosen):
                members = sorted(clusters[cid], key=lambda r: r["assertion_id"])
                if within is None or within >= len(members):
                    picked, pi_second = members, 1.0
                else:
                    picked = random.Random(f"{seed}|{tercile}|{cid}").sample(members, within)
                    pi_second = within / len(members)
                strata_info[tercile]["n"] += len(picked)
                for row in picked:
                    out.append(_sample_row(row, tercile, pi_first, pi_second))
    out.sort(key=lambda r: r["assertion_id"])
    info = {"design": design, "psu": "unit" if design == "units" else "cluster", "seed": seed,
            "requested_n": n, "within": within, "sampled": len(out), "strata": strata_info}
    return out, info


def _sample_row(row: dict, label: str, pi_first: float, pi_second: float) -> dict:
    return {"assertion_id": row["assertion_id"], "stratum": label, "kind": row["kind"], "cond": row["cond"],
            "tercile": row["tercile"], "cluster_id": row["cluster_id"], "pi_first": pi_first,
            "pi_second": pi_second, "pi": pi_first * pi_second}


# --------------------------------------------------------------------------------------------- beta and intervals


def _betainc(a: float, b: float, x: float) -> float:
    if _scipy_betainc is not None:
        return float(_scipy_betainc(a, b, x))
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (modified Lentz), Numerical Recipes 6.4."""
    tiny, eps = 1e-300, 3e-16
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / (c if abs(c) > tiny else tiny)
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / (c if abs(c) > tiny else tiny)
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _beta_ppf(q: float, a: float, b: float) -> float:
    """Quantile of Beta(a, b): scipy when importable, else bisection on the regularised incomplete beta."""
    if _scipy_betaincinv is not None:
        return float(_scipy_betaincinv(a, b, q))
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _betainc(a, b, mid) < q:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-13:
            break
    return 0.5 * (lo + hi)


def clopper_pearson(x: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact two-sided (1 - alpha) interval for a binomial proportion x/n."""
    if n <= 0:
        return 0.0, 1.0
    lo = 0.0 if x <= 0 else _beta_ppf(alpha / 2, x, n - x + 1)
    hi = 1.0 if x >= n else _beta_ppf(1 - alpha / 2, x + 1, n - x)
    return lo, hi


def clopper_pearson_upper(x: int, n: int, alpha: float = 0.05) -> float:
    """Exact one-sided (1 - alpha) upper bound on x/n."""
    if n <= 0:
        return 1.0
    return 1.0 if x >= n else _beta_ppf(1 - alpha, x + 1, n - x)


def wilson(p: float, n: float, z: float) -> tuple[float, float]:
    """Wilson score interval for a proportion p with (effective) sample size n."""
    if n <= 0:
        return 0.0, 1.0
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def cohen_kappa(pairs: list[tuple[str, str]]) -> dict:
    """Raw agreement and Cohen's kappa for two raters over the verdict categories."""
    n = len(pairs)
    if n == 0:
        return {"n": 0, "raw_agreement": None, "kappa": None}
    po = sum(a == b for a, b in pairs) / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in VERDICTS)
    kappa = None if pe >= 1.0 else (po - pe) / (1.0 - pe)
    return {"n": n, "raw_agreement": po, "kappa": kappa, "expected_agreement": pe}


# --------------------------------------------------------------------------------------------- estimation


def _psu(row: dict, psu: str) -> str:
    return row["assertion_id"] if psu == "unit" else row["cluster_id"]


def estimate_domain(rows: list[dict], design: dict, pred: Callable[[dict], bool], alpha: float = 0.05,
                    name: str = "") -> dict:
    """Hajek ratio precision over the rows selected by `pred`, with design-based variance.

    Precision = sum(w*y) / sum(w*z) over judged rows (w = 1/pi, y = correct, z = judged). Unjudgeable rows are
    outside the denominator and only enter the sensitivity bounds. Variance: linearisation of the ratio, residual
    t_i = w_i * z_i * (y_i - p), summed per PSU (unit or cluster) inside strata with the FPC (1 - m_h/M_h).
    """
    z_crit = statistics.NormalDist().inv_cdf(1 - alpha / 2)
    inside = [pred(r) for r in rows]
    judged = [f and r["verdict"] in JUDGED for r, f in zip(rows, inside)]
    correct = [j and r["verdict"] == "correct" for r, j in zip(rows, judged)]
    unjudgeable = [f and r["verdict"] == "unjudgeable" for r, f in zip(rows, inside)]
    n_j, n_c = sum(judged), sum(correct)
    res: dict[str, Any] = {
        "name": name, "n_sampled": sum(inside), "n_judged": n_j, "correct": n_c, "incorrect": n_j - n_c,
        "unjudgeable": sum(unjudgeable), "alpha": alpha,
    }
    if n_j == 0:
        res["status"] = "no_judged_assertions"
        return res
    w_judged = sum(1.0 / r["pi"] for r, j in zip(rows, judged) if j)
    y_total = sum(1.0 / r["pi"] for r, c in zip(rows, correct) if c)
    u_total = sum(1.0 / r["pi"] for r, u in zip(rows, unjudgeable) if u)
    p = y_total / w_judged
    psu_total: Counter = Counter()
    psus: dict[str, set[str]] = defaultdict(set)
    in_domain: set[str] = set()  # strata holding judged rows of this domain
    for r, j, c in zip(rows, judged, correct):
        key = (r["stratum"], _psu(r, design["psu"]))
        psus[r["stratum"]].add(key[1])
        if j:
            in_domain.add(r["stratum"])
            psu_total[key] += ((1.0 if c else 0.0) - p) / r["pi"]
    variance: float | None = 0.0
    for label, info in design["strata"].items():
        ids = psus.get(label, set())
        m = len(ids)
        if m == 0:
            continue
        if m != info["m"]:
            raise AdjudicationError(f"stratum {label}: {m} sampled PSUs but the design records {info['m']}")
        f = m / info["M"]
        if f >= 1.0 or label not in in_domain:
            continue  # census of the stratum's PSUs, or no domain rows there: zero contribution
        if m < 2:
            variance = None  # a single PSU carries domain residuals: its variance is not estimable
            continue
        vals = [psu_total.get((label, c), 0.0) for c in ids]
        mu = sum(vals) / m
        if variance is not None:
            variance += (1.0 - f) * m / (m - 1) * sum((v - mu) ** 2 for v in vals)
    var = None if variance is None else variance / (w_judged ** 2)
    res.update({"frame_judged_estimate": w_judged, "frame_unjudgeable_estimate": u_total,
                "variance": var, "se": None if var is None else math.sqrt(var)})
    res["error_rate_upper_cp_sample_only"] = clopper_pearson_upper(n_j - n_c, n_j, alpha)
    res["clopper_pearson_unweighted"] = list(clopper_pearson(n_c, n_j, alpha))
    if n_c == n_j:
        res["precision"] = None  # zero observed errors: precision is not established as 100%
        res["linearised_interval"] = res["deff"] = res["wilson_deff_adjusted"] = None
        res["statement"] = (f"No errors observed in {n_j} judged sampled assertions. Sample-only one-sided "
                            f"{100 * (1 - alpha):.0f}% Clopper-Pearson upper bound on the error rate (treats the judged "
                            f"assertions as a simple random sample; not a design-based bound): "
                            f"{100 * res['error_rate_upper_cp_sample_only']:.2f}%. "
                            "Zero observed errors do not establish perfect precision.")
    else:
        res["precision"] = p
        res["linearised_interval"] = None if var is None else [max(0.0, p - z_crit * math.sqrt(var)),
                                                                min(1.0, p + z_crit * math.sqrt(var))]
        deff = var / (p * (1 - p) / n_j) if var and 0 < p < 1 else None
        res["deff"] = deff
        res["wilson_deff_adjusted"] = list(wilson(p, n_j / deff if deff else n_j, z_crit))
        res["statement"] = None
    if w_judged + u_total > 0:
        res["unjudgeable_sensitivity"] = {
            "all_incorrect": y_total / (w_judged + u_total),
            "all_correct": (y_total + u_total) / (w_judged + u_total),
        }
    return res


def estimate(rows: list[dict], design: dict, alpha: float = 0.05) -> dict:
    out: dict[str, Any] = {"alpha": alpha, "design": design["design"], "psu": design["psu"],
                           "n_sampled": len(rows), "frame": design.get("frame", {}), "domains": {}, "strata": {}}
    for name, pred in DOMAINS.items():
        out["domains"][name] = estimate_domain(rows, design, pred, alpha, name)
    for label in sorted(design["strata"]):
        out["strata"][label] = estimate_domain(rows, design, lambda r, h=label: r["stratum"] == h, alpha, label)
    return out


def render_text(result: dict) -> str:
    lines = [f"design={result['design']} psu={result['psu']} n_sampled={result['n_sampled']} alpha={result['alpha']}"]
    for name, d in result["domains"].items():
        lines.append(_domain_line(name, d))
    lines.append("per stratum:")
    for name, d in result["strata"].items():
        lines.append(_domain_line("  " + name, d))
    return "\n".join(lines)


def _domain_line(name: str, d: dict) -> str:
    head = f"{name}: judged={d['n_judged']} correct={d['correct']} incorrect={d['incorrect']} unjudgeable={d['unjudgeable']}"
    if d.get("status"):
        return f"{head} status={d['status']}"
    if d["precision"] is None:
        return f"{head}\n    {d['statement']}"
    lo, hi = d["wilson_deff_adjusted"]
    cp_lo, cp_hi = d["clopper_pearson_unweighted"]
    lin = d["linearised_interval"]
    lin_txt = "n/a (variance not estimable)" if lin is None else f"[{lin[0]:.4f}, {lin[1]:.4f}]"
    sens = d.get("unjudgeable_sensitivity")
    sens_txt = "" if not sens else f" unjudgeable bounds [{sens['all_incorrect']:.4f}, {sens['all_correct']:.4f}]"
    return (f"{head}\n    precision (Hajek, HT-weighted)={d['precision']:.4f} linearised 95% CI={lin_txt} "
            f"deff={'n/a' if d['deff'] is None else format(d['deff'], '.3f')} "
            f"Wilson(deff)=[{lo:.4f}, {hi:.4f}] CP(unweighted)=[{cp_lo:.4f}, {cp_hi:.4f}]{sens_txt}")


# --------------------------------------------------------------------------------------------- export and import


SCHEMA = {
    "description": "Adjudication import schema: one JSON object per line per judged assertion.",
    "fields": {
        "assertion_id": "string, id from the packet (sha256 of the canonical form)",
        "verdict": "one of correct | incorrect | unjudgeable",
        "rationale": "string; required and non-empty for incorrect and unjudgeable; optional for correct",
        "annotator": "string; the annotator id from the packet filename (or 'adjudicator')",
    },
    "verdicts": {
        "correct": "every real-world outcome consistent with the stated source propositions satisfies the claim",
        "incorrect": "some real-world outcome consistent with the sources violates the claim",
        "unjudgeable": "the sources do not settle the claim (ambiguous, missing or contradictory information)",
    },
}


def export_packets(frame: list[dict], sample: list[dict], annotators: list[str], overlap: float, seed: int,
                   out: Path) -> dict:
    if not annotators or len(set(annotators)) != len(annotators):
        raise AdjudicationError("annotators must be a non-empty list of distinct names")
    for name in annotators:
        _check_annotator_name(name)
    if not 0.0 <= overlap <= 1.0:
        raise AdjudicationError("overlap must be in [0, 1]")
    by_id = {r["assertion_id"]: r for r in frame}
    ids = sorted({s["assertion_id"] for s in sample}, key=lambda i: _sha(f"{seed}|order|{i}"))
    assignments: list[dict] = []
    for idx, aid in enumerate(ids):
        names = [annotators[idx % len(annotators)]]
        draw = int(_sha(f"{seed}|overlap|{aid}")[:12], 16) / 16 ** 12
        if len(annotators) >= 2 and draw < overlap:
            names.append(annotators[(idx + 1) % len(annotators)])
        assignments.extend({"assertion_id": aid, "annotator": a, "double": len(names) > 1} for a in names)
    packets_dir = out / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name in annotators:
        items = [a["assertion_id"] for a in assignments if a["annotator"] == name]
        rows = []
        for aid in items:
            row = by_id[aid]
            rows.append({"assertion_id": aid, "claim": row["claim"],
                         "sources": [{"ref": s["ref"], "text": s["text"]} for s in row["sources"]]})
        _write_jsonl(packets_dir / f"{name}.jsonl", rows)
        with open(packets_dir / f"{name}.csv", "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["assertion_id", "claim", "sources"])
            for row in rows:
                writer.writerow([row["assertion_id"], row["claim"],
                                 " | ".join(f"{s['ref']}: {s['text']}" for s in row["sources"])])
        counts[name] = len(rows)
    _write_jsonl(out / "assignments.jsonl", assignments)  # coordinator only: never shipped to annotators
    _write_json(out / "adjudication_schema.json", SCHEMA)
    per_item = Counter(a["assertion_id"] for a in assignments)
    return {"annotators": counts, "assertions": len(ids), "double_annotated": sum(1 for c in per_item.values() if c > 1)}


ANNOTATOR_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _check_annotator_name(name: str) -> None:
    if not ANNOTATOR_NAME.match(name) or name == ANNOTATOR_ADJUDICATOR:
        raise AdjudicationError(f"annotator name must be 1-64 letters, digits, '-' or '_' (not '{ANNOTATOR_ADJUDICATOR}'): {name!r}")


def _check_annotation(row: dict, where: str) -> str | None:
    if not isinstance(row, dict):
        return f"{where}: not an object"
    if not isinstance(row.get("assertion_id"), str):
        return f"{where}: assertion_id missing"
    if row.get("verdict") not in VERDICTS:
        return f"{where}: verdict must be one of {', '.join(VERDICTS)}"
    rationale = row.get("rationale", "")
    if not isinstance(rationale, str):
        return f"{where}: rationale must be a string"
    if row["verdict"] != "correct" and not rationale.strip():
        return f"{where}: rationale is required for {row['verdict']}"
    if not isinstance(row.get("annotator"), str) or not ANNOTATOR_NAME.match(row["annotator"]):
        return f"{where}: annotator missing or not a valid name"
    return None


def import_annotations(export_dir: Path, annotation_files: list[Path], adjudication: Path | None, out: Path) -> dict:
    assigned: dict[str, set[str]] = defaultdict(set)
    for row in _read_jsonl(export_dir / "assignments.jsonl"):
        assigned[row["assertion_id"]].add(row["annotator"])
    errors: list[str] = []
    labels: dict[str, dict[str, dict]] = defaultdict(dict)
    for path in annotation_files:
        for lineno, row in enumerate(_read_jsonl(path), 1):
            where = f"{path.name}:{lineno}"
            problem = _check_annotation(row, where)
            if problem is None and row["annotator"] not in assigned.get(row["assertion_id"], set()):
                problem = f"{where}: {row['annotator']} was not assigned {row['assertion_id'][:12]}"
            if problem is None and row["annotator"] in labels[row["assertion_id"]]:
                problem = f"{where}: duplicate annotation"
            if problem:
                errors.append(problem)
            else:
                labels[row["assertion_id"]][row["annotator"]] = row
    adj: dict[str, dict] = {}
    if adjudication is not None:
        for lineno, row in enumerate(_read_jsonl(adjudication), 1):
            where = f"{adjudication.name}:{lineno}"
            problem = _check_annotation(row, where)
            if problem is None and row["assertion_id"] not in assigned:
                problem = f"{where}: unknown assertion_id"
            if problem is None and row["annotator"] != ANNOTATOR_ADJUDICATOR:
                problem = f"{where}: adjudication rows must have annotator '{ANNOTATOR_ADJUDICATOR}'"
            if problem is None and row["assertion_id"] in adj:
                problem = f"{where}: duplicate adjudication"
            if problem:
                errors.append(problem)
            else:
                adj[row["assertion_id"]] = row
    if errors:
        raise AdjudicationError("; ".join(errors[:20]) + (f" (and {len(errors) - 20} more)" if len(errors) > 20 else ""))

    resolved: list[dict] = []
    status_counts: Counter = Counter()
    for aid in sorted(assigned):
        anns = labels.get(aid, {})
        verdicts = {a: r["verdict"] for a, r in sorted(anns.items())}
        complete = len(anns) == len(assigned[aid])
        if not anns:
            status, final = ("adjudicated", adj[aid]["verdict"]) if aid in adj else ("missing", None)
        elif complete and len(set(verdicts.values())) == 1:
            status, final = ("single" if len(verdicts) == 1 else "agreed"), next(iter(verdicts.values()))
        elif aid in adj:
            status, final = "adjudicated", adj[aid]["verdict"]
        else:
            status, final = "pending", None  # a missing second annotation or a disagreement, no adjudicator
        status_counts[status] += 1
        row = {"assertion_id": aid, "final_verdict": final, "status": status, "verdicts": verdicts}
        if status == "adjudicated":
            row["adjudicator_rationale"] = adj[aid]["rationale"]
        resolved.append(row)

    pairs: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for aid, anns in labels.items():
        for a in sorted(anns):
            for b in sorted(anns):
                if a < b:
                    pairs[(a, b)].append((anns[a]["verdict"], anns[b]["verdict"]))
    agreement = {"pairs": {f"{a}|{b}": cohen_kappa(v) for (a, b), v in sorted(pairs.items())},
                 "items": dict(status_counts), "adjudication_rows": len(adj)}
    _write_jsonl(out / "resolved.jsonl", resolved)
    _write_json(out / "agreement.json", agreement)
    return agreement


# --------------------------------------------------------------------------------------------- CLI


def _load_sample(sample_dir: Path) -> tuple[list[dict], dict]:
    rows = _read_jsonl(sample_dir / "sample.jsonl")
    design = json.loads((sample_dir / "sample_design.json").read_text(encoding="utf-8-sig"))
    return rows, design


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.adjudication", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("frame", help="build the normalized assertion registry")
    p.add_argument("--submission", required=True, type=Path)
    p.add_argument("--corpus", required=True, type=Path, help="corpus.jsonl (source texts only)")
    p.add_argument("--out", required=True, type=Path)

    p = sub.add_parser("sample", help="stratified probability sample of the registry")
    p.add_argument("--frame", required=True, type=Path, help="frame.jsonl")
    p.add_argument("--design", required=True, choices=("units", "clusters"))
    p.add_argument("--n", required=True, type=int, help="assertions (units) or clusters (clusters)")
    p.add_argument("--seed", required=True, type=int)
    p.add_argument("--within", type=int, default=None, help="clusters design: rows per sampled cluster")
    p.add_argument("--out", required=True, type=Path)

    p = sub.add_parser("export", help="blinded annotator packets")
    p.add_argument("--frame", required=True, type=Path)
    p.add_argument("--sample-dir", required=True, type=Path)
    p.add_argument("--annotators", required=True, help="comma-separated annotator ids")
    p.add_argument("--overlap", type=float, default=0.2, help="fraction double-annotated (default 0.2)")
    p.add_argument("--seed", required=True, type=int)
    p.add_argument("--out", required=True, type=Path)

    p = sub.add_parser("import", help="validate annotations, agreement, resolution")
    p.add_argument("--export", required=True, type=Path, help="export directory (assignments.jsonl)")
    p.add_argument("--annotations", required=True, nargs="+", type=Path)
    p.add_argument("--adjudication", type=Path, default=None)
    p.add_argument("--out", required=True, type=Path)

    p = sub.add_parser("estimate", help="weighted precision with design-based intervals")
    p.add_argument("--sample-dir", required=True, type=Path)
    p.add_argument("--resolved", required=True, type=Path, help="resolved.jsonl from import")
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--out", type=Path, default=None, help="optional JSON output path")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "frame":
            rows, summary = build_frame(args.submission, args.corpus)
            _write_jsonl(args.out / "frame.jsonl", rows)
            _write_json(args.out / "frame_summary.json", summary)
            print(json.dumps(summary, sort_keys=True, indent=2))
        elif args.cmd == "sample":
            frame = _read_jsonl(args.frame)
            rows, design = draw_sample(frame, args.design, args.n, args.seed, args.within)
            summary_path = args.frame.parent / "frame_summary.json"
            design["frame"] = json.loads(summary_path.read_text(encoding="utf-8"))["counts"] if summary_path.exists() else {}
            design["frame_rows"] = len(frame)
            _write_jsonl(args.out / "sample.jsonl", rows)
            _write_json(args.out / "sample_design.json", design)
            print(json.dumps({k: v for k, v in design.items() if k != "frame"}, sort_keys=True, indent=2))
        elif args.cmd == "export":
            frame = _read_jsonl(args.frame)
            sample, _ = _load_sample(args.sample_dir)
            names = [a.strip() for a in args.annotators.split(",") if a.strip()]
            print(json.dumps(export_packets(frame, sample, names, args.overlap, args.seed, args.out), indent=2))
        elif args.cmd == "import":
            agreement = import_annotations(args.export, args.annotations, args.adjudication, args.out)
            print(json.dumps(agreement, sort_keys=True, indent=2))
        elif args.cmd == "estimate":
            sample, design = _load_sample(args.sample_dir)
            resolved = {r["assertion_id"]: r for r in _read_jsonl(args.resolved)}
            missing = [r["assertion_id"] for r in sample if resolved.get(r["assertion_id"], {}).get("final_verdict") is None]
            if missing:
                raise AdjudicationError(f"{len(missing)} sampled assertions have no final verdict (estimate refuses)")
            joined = [dict(r, verdict=resolved[r["assertion_id"]]["final_verdict"]) for r in sample]
            result = estimate(joined, design, args.alpha)
            print(render_text(result))
            if args.out is not None:
                _write_json(args.out, result)
    except (AdjudicationError, OSError, KeyError, ValueError) as exc:
        print(f"adjudication: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Mock adapters: `oracle` replays gold from a submission-format directory (full theory replay, groups skipped);
`gold_view` answers gold bindings, pairwise relations and gold groups without replaying the theory (and the curated
suite's labels, when present: one decision per term of a labelled group, and the scoped chain pairs of a reading question);
`empty` decides nothing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..harness.fixed import gold_groups_from_theory
from ..ir import read_jsonl
from .base import usage

_RANK = {"EQUIV": 0, "IMPLIES": 1, "IMPLIED_BY": 1, "EXCLUDES": 2}


def _is_var(expr: dict[str, Any]) -> bool:
    return expr.get("op") == "var"


def _pairwise(expr: dict[str, Any]) -> list[tuple[str, str, str]] | None:
    """Pairwise relations (x, y, relation from x's side) a constraint expresses, or None if not pairwise."""
    op = expr.get("op")
    if op == "implies" and _is_var(expr["a"]) and _is_var(expr["b"]):
        a, b = expr["a"]["id"], expr["b"]["id"]
        return [(a, b, "IMPLIES"), (b, a, "IMPLIED_BY")]
    if op == "iff" and _is_var(expr["a"]) and _is_var(expr["b"]):
        a, b = expr["a"]["id"], expr["b"]["id"]
        return [(a, b, "EQUIV"), (b, a, "EQUIV")]
    if op == "atmost" and expr["k"] == 1 and all(_is_var(x) for x in expr["args"]):
        ids = [x["id"] for x in expr["args"]]
        return [(x, y, "EXCLUDES") for x in ids for y in ids if x != y]
    return None


class _GoldAdapter:
    """Gold bindings from bindings.jsonl; pairwise relations between candidate pairs; decide_group returns every
    gold cardinality group whose members all lie in the group context, with gold's interpretation scope (axis
    `gold`) when the gold constraint is scoped."""

    name = "gold"
    supplies_gold_refs = False  # set True per gold-backed class: only those may write gold-only REFs (axioms, event ids)

    def __init__(self, gold_dir: str | Path):
        gold_dir = Path(gold_dir)
        self._bindings = {row["id"]: row for row in read_jsonl(gold_dir / "bindings.jsonl")}
        theory = json.loads((gold_dir / "theory.json").read_text(encoding="utf-8-sig"))
        self._interpretations = list(theory["interpretations"])
        # Provenance (axiom and source REFs) travels with each constraint: the full-replay submission must cite what gold cites.
        self._constraints = [{"id": c["id"], "expr": c["expr"], "interps": c.get("interps"), "provenance": c.get("provenance")}
                             for c in theory["constraints"]]
        self._pairs: dict[tuple[str, str], str] = {}
        for con in self._constraints:
            pairs = _pairwise(con["expr"]) if con["interps"] is None else None
            for x, y, rel in pairs or []:
                if (x, y) not in self._pairs or _RANK[rel] < _RANK[self._pairs[(x, y)]]:
                    self._pairs[(x, y)] = rel
        self._groups = gold_groups_from_theory(theory)
        self._group_of: dict[str, list[int]] = {}
        for n, group in enumerate(self._groups):
            for member in group["members"]:
                self._group_of.setdefault(member, []).append(n)

    def _row(self, outcome_id: str) -> dict[str, Any]:
        if outcome_id not in self._bindings:
            raise ValueError(f"outcome {outcome_id} is not covered by the gold directory; the oracle only fits its corpus")
        return self._bindings[outcome_id]

    def decide_binding(self, outcome_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        row = self._row(outcome_context["id"])
        decision = {k: row[k] for k in ("status", "class", "negated", "missing")}
        if "evidence" in row:
            decision["evidence"] = list(row["evidence"])  # gold's own REFs: the axioms and the event REF
        return decision, usage(self.name)

    def decide_relation(self, a_context: dict[str, Any], b_context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        a, b = a_context["id"], b_context["id"]
        if self._row(a)["status"] != "bound" or self._row(b)["status"] != "bound":
            return "UNRESOLVED", usage(self.name)
        return self._pairs.get((a, b), "NONE"), usage(self.name)

    def decide_group(self, group_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        ids = {m["id"] for m in group_ctx["members"]}
        found = []
        for n in sorted({n for i in ids for n in self._group_of.get(i, [])}):
            gold = self._groups[n]
            if set(gold["members"]) <= ids:
                found.append(self._decision(gold))
        return found, usage(self.name)

    def _decision(self, gold: dict[str, Any]) -> dict[str, Any]:
        decision: dict[str, Any] = {"kind": gold["kind"], "members": list(gold["members"])}
        if gold["kind"] in ("at_least_k", "at_most_k"):
            decision["k"] = gold["k"]
        if gold["interps"] is not None:
            decision["scope"] = {"axis": "gold", "values": list(gold["interps"]), "of": list(self._interpretations)}
        return decision


class OracleAdapter(_GoldAdapter):
    """Full replay of gold. Bindings come from gold bindings.jsonl. extra_theory() returns every gold
    constraint verbatim (interpretation scopes included), so the harness skips candidate decisions for this
    adapter and the submission's theory equals gold's. The pairwise and group views keep the typed protocol."""

    name = "oracle"
    supplies_gold_refs = True

    def extra_theory(self) -> dict[str, Any]:
        """Full theory replay: every gold constraint, unchanged."""
        return {"interpretations": list(self._interpretations), "constraints": [dict(c) for c in self._constraints]}


GOLD_VIEW_CONF = 10.0  # a labelled relation is certain: its conf is above any calibrated relation floor
_FLIP = {"IMPLIES": "IMPLIED_BY", "IMPLIED_BY": "IMPLIES"}


def _suite_answers(gold_dir: Path) -> tuple[dict[tuple[str, str], str], dict[tuple[str, ...], dict[str, Any]]]:
    """The curated suite's labels joined to their questions: the relation label of each candidate pair (a, b), as
    candidates.jsonl orders it, and the label row of each group or reading question, by its members in file order. The labels
    (gold/suite_labels.jsonl) carry no outcome ids, so the join reads candidates.jsonl from the fixture directory above
    gold/. Both are empty when either file is absent."""
    labels_path, candidates_path = gold_dir / "suite_labels.jsonl", gold_dir.parent / "candidates.jsonl"
    if not (labels_path.is_file() and candidates_path.is_file()):
        return {}, {}
    labels = {row["qid"]: row for row in read_jsonl(labels_path)}
    relations: dict[tuple[str, str], str] = {}
    groups: dict[tuple[str, ...], dict[str, Any]] = {}  # group and reading questions, by their members in file order
    for cand in read_jsonl(candidates_path):
        label = labels.get(cand["qid"])
        if label is None:
            continue
        if cand["op"] == "decide_relation":
            relations[(cand["a"], cand["b"])] = label["label"]
        elif cand["op"] == "decide_group":
            key = tuple(cand["members"])
            if key in groups:
                raise ValueError(f"two group questions name the same members in the same order (at {cand['qid']!r})")
            groups[key] = label
    return relations, groups


def _labelled_group(row: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    """The group decisions of a labelled group: one per term of its terms (the label's canonical kind for a row without
    terms, none for a decoy). An ordered_chain is the label's order (forward); the other kinds name all of ids."""
    if "terms" in row:
        terms = row["terms"]
    else:
        terms = [] if row["label"] == "none" else [[row["label"], row["k"]]]
    out = []
    for kind, k in terms:
        if kind == "ordered_chain":
            out.append({"kind": kind, "members": list(row["order"]), "direction": "forward"})
            continue
        decision: dict[str, Any] = {"kind": kind, "members": list(ids)}
        if kind in ("at_least_k", "at_most_k"):
            decision["k"] = k
        out.append(decision)
    return out


def _reading_answer(row: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    """The scoped chain pairs of a reading question: for each reading with a pick c (readings[r], not null), the chains
    [u, c] and [c, u] scoped to exactly that reading. ids is the candidate's members: u first, then its four options."""
    u = ids[0]
    out = []
    for reading in ("base", "alt"):
        pick = row["readings"][reading]
        if pick is None:
            continue
        scope = {"axis": "reading", "values": [reading], "of": ["alt", "base"]}
        out += [{"kind": "ordered_chain", "members": pair, "direction": "forward", "scope": scope}
                for pair in ([u, pick], [pick, u])]
    return out


class GoldViewAdapter(_GoldAdapter):
    """Gold bindings, gold pairwise relations and gold groups, but no theory replay: what the pipeline can express
    when every decision is right. A diagnostic (oracle retrieval), not a baseline. When the gold directory holds the
    curated suite's labels (gold/suite_labels.jsonl, with the fixture's candidates.jsonl), a labelled relation is
    answered in the order asked (IMPLIES and IMPLIED_BY swap when reversed) with conf GOLD_VIEW_CONF, a labelled group with
    one decision per term of its label, and a reading question with its scoped chain pairs; other questions use the gold
    theory as before."""

    name = "gold_view"
    supplies_gold_refs = True

    def __init__(self, gold_dir: str | Path):
        super().__init__(gold_dir)
        self._labelled_relations, self._labelled_groups = _suite_answers(Path(gold_dir))

    def decide_relation(self, a_context: dict[str, Any], b_context: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        a, b = a_context["id"], b_context["id"]
        if (a, b) in self._labelled_relations:
            return {"relation": self._labelled_relations[(a, b)], "conf": GOLD_VIEW_CONF}, usage(self.name)
        if (b, a) in self._labelled_relations:
            label = self._labelled_relations[(b, a)]
            return {"relation": _FLIP.get(label, label), "conf": GOLD_VIEW_CONF}, usage(self.name)
        return super().decide_relation(a_context, b_context)

    def decide_group(self, group_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        ids = [m["id"] for m in group_ctx["members"]]
        row = self._labelled_groups.get(tuple(ids))
        if row is None:
            return super().decide_group(group_ctx)
        if "readings" in row:
            return _reading_answer(row, ids), usage(self.name)
        return _labelled_group(row, ids), usage(self.name)


class EmptyAdapter:
    """Consistent but empty: every binding unresolved, every relation UNRESOLVED, no groups, no constraints. Its
    decide_group is a stub, so it declares no group support and the build skips the group phase for it."""

    name = "empty"
    supports_groups = False
    supplies_gold_refs = False

    def decide_binding(self, outcome_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        return {"status": "unresolved", "class": None, "negated": False, "missing": ["unspecified"]}, usage(self.name)

    def decide_relation(self, a_context: dict[str, Any], b_context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return "UNRESOLVED", usage(self.name)

    def decide_group(self, group_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return [], usage(self.name)

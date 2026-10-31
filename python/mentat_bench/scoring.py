"""Per-family utilities (mentat-bench-v0.1.md section 8) and the Q_N aggregate.

Gold inputs are reference answers produced by `mb-engine eval` on the gold directory. Verdicts are
`mb-engine check` results keyed by qid (cert_valid).

Headline numbers count each outcome ID once. The optional weighted block (score_run with `weights`) uses
ClassWeights, a global table built from the gold bindings: a bound outcome weighs 1/|class| and an unresolved
outcome weighs 1, so each semantic class has unit total weight. Weights only change closure and selection
masses; `exact`, `per_query` and the decision families stay unweighted. The weights are global, not per query: a
class that the query universe cuts (only some of its outcomes are in the universe) still weighs 1/|class| per outcome,
so it contributes less than unit mass to that query. This is deliberate (the mass of a class is its share of the whole
corpus); it is not a bug, and it is why the weighted block is read beside the unweighted headline.
Anchor and active blocks split the queries by the `anchor` flag of the design rows (gold/query_design.jsonl). Each
block reports `uncovered` (design rows of that block with no query in the workload) and `families_missing`; the
report's `design_absent` lists every design qid that is not in the workload.
"""

from __future__ import annotations

import hashlib
import json
import math
from array import array
from collections import Counter
from collections.abc import Set
from pathlib import Path
from typing import Any, Iterable

from . import ir

LABELS = ("ENTAILED", "REFUTED", "CONTINGENT", "UNRESOLVED")
FAMILIES = ("closure", "selection", "feasibility", "partition")
FEAS_DECISIONS = ("SAT", "UNSAT", "UNRESOLVED")
PART_DECISIONS = ("true", "false", "UNRESOLVED")
BASELINES = ("all-contingent", "all-unresolved", "constant-answer")
GOLD_INFORMED_BASELINES = ("constant-answer", "all-unresolved")
MAX_MISSING = 8  # qualifier codes per UNRESOLVED answer
UNRESOLVED_CLASS = 0  # class index of an outcome with no class
DECISIVE = ("ENTAILED", "REFUTED")  # closure labels that the closure_decisive_accuracy key measures


class ClassWeights:
    """Global outcome weights, streamed from a bindings file (gold/bindings.jsonl) in one pass. A bound outcome
    (status "bound" with a class) weighs 1/|class|, where |class| counts the bound outcomes of that class; both
    polarities count, so a complement pair shares the class weight. An unresolved outcome, or an ID missing from
    the bindings, weighs 1. Memory: one dict entry and one array('I') slot per outcome; class names are interned."""

    def __init__(self) -> None:
        self._row: dict[str, int] = {}  # outcome id -> row
        self._cls = array("I")  # row -> class index
        self._class_index: dict[str, int] = {}  # class name -> class index (1-based)
        self._size = array("I", [0])  # class index -> number of bound outcomes; index 0 is unresolved

    @classmethod
    def from_bindings(cls, path: str | Path) -> "ClassWeights":
        weights = cls()
        for row in ir.read_jsonl(Path(path)):
            oid = row["id"]
            if not isinstance(oid, str) or oid in weights._row:
                raise ValueError(f"outcome id {oid!r} is not a string or occurs twice in {path}")
            index = UNRESOLVED_CLASS
            name = row.get("class")
            if row.get("status") == "bound" and isinstance(name, str):
                index = weights._class_index.get(name)
                if index is None:
                    index = weights._class_index[name] = len(weights._class_index) + 1
                    weights._size.append(0)
                weights._size[index] += 1
            weights._row[oid] = len(weights._cls)
            weights._cls.append(index)
        return weights

    def weight(self, oid: str) -> float:
        row = self._row.get(oid)
        if row is None or self._cls[row] == UNRESOLVED_CLASS:
            return 1.0
        return 1.0 / self._size[self._cls[row]]


def _mass(ids: Set[str], weights: ClassWeights | None) -> float:
    """Count of IDs (no weights) or their total weight. math.fsum is exact and order independent, so the result
    does not depend on set iteration order (PYTHONHASHSEED)."""
    if weights is None:
        return len(ids)
    return math.fsum(weights.weight(x) for x in ids)


def f1(tp: float, fp: float, fn: float) -> float:
    denom = 2 * tp + fp + fn
    return 1.0 if denom == 0 else 2 * tp / denom


def _str_list(value: Any) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return value
    return None


def _missing_codes(value: Any) -> list[str] | None:
    """Qualifier codes: a list of unique non-empty strings, at most MAX_MISSING. Absent or null means []."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_MISSING:
        return None
    if not all(isinstance(x, str) and x for x in value) or len(set(value)) != len(value):
        return None
    return value


def _label_lists(labels: Any) -> dict[str, list[str]] | None:
    if not isinstance(labels, dict) or set(labels) != set(LABELS):
        return None
    lists = {label: _str_list(labels[label]) for label in LABELS}
    return None if any(v is None for v in lists.values()) else lists


def _closure_lists(answer: Any, gold_labels: dict[str, list[str]]) -> dict[str, list[str]] | None:
    """Label lists of a valid closure answer: no error, all four labels, and every universe ID exactly once."""
    if not isinstance(answer, dict) or answer.get("error"):
        return None
    lists = _label_lists(answer.get("labels"))
    if lists is None:
        return None
    pred_ids = [x for label in LABELS for x in lists[label]]
    gold_ids = [x for label in LABELS for x in gold_labels[label]]
    if len(pred_ids) != len(set(pred_ids)) or set(pred_ids) != set(gold_ids):
        return None
    return lists


def closure_utility(answer: Any, gold_labels: dict[str, list[str]], weights: ClassWeights | None = None) -> float:
    """Macro F1 over the four labels. Labels absent from gold and prediction are omitted;
    predicted-only labels score 0. Malformed labels, or a universe not covered exactly once, score 0."""
    lists = _closure_lists(answer, gold_labels)
    if lists is None:
        return 0.0
    scores = []
    for label in LABELS:
        pred, gold = set(lists[label]), set(gold_labels[label])
        if not pred and not gold:
            continue
        scores.append(f1(_mass(pred & gold, weights), _mass(pred - gold, weights), _mass(gold - pred, weights)))
    return sum(scores) / len(scores) if scores else 1.0


def selection_utility(answer: Any, gold_ids: list[str], weights: ClassWeights | None = None) -> float:
    """Set F1; a correctly empty answer scores 1; malformed, errored or duplicated IDs score 0."""
    if not isinstance(answer, dict) or answer.get("error"):
        return 0.0
    ids = _str_list(answer.get("ids"))
    if ids is None or len(set(ids)) != len(ids):
        return 0.0
    pred, gold = set(ids), set(gold_ids)
    return f1(_mass(pred & gold, weights), _mass(pred - gold, weights), _mass(gold - pred, weights))


def _decision_utility(answer: Any, gold: dict[str, Any], decisions: tuple[str, ...], cert_valid: bool) -> float:
    """An UNRESOLVED decision scores 1 when its missing list intersects gold's codes. When gold lists no code
    (interpretations disagree over bound atoms), only an empty missing list scores: any reason code scores 0.
    Other decisions score 1 only with a valid certificate (cert_valid)."""
    if not isinstance(answer, dict) or answer.get("error") or answer.get("decision") not in decisions:
        return 0.0
    if answer["decision"] != gold["decision"]:
        return 0.0
    if gold["decision"] == "UNRESOLVED":
        missing = _missing_codes(answer.get("missing"))
        if missing is None:
            return 0.0
        gold_missing = set(gold.get("missing") or [])
        if not gold_missing:  # interpretations disagree over bound atoms: no qualifier code applies
            return 1.0 if not missing else 0.0
        return 1.0 if set(missing) & gold_missing else 0.0
    return 1.0 if cert_valid else 0.0


def read_verdicts(path: str | Path) -> dict[str, bool]:
    """qid -> certificate validity from `mb-engine check` output. Lenient like JsonlIndex: a line that is not an
    object with a string qid is skipped (its query then counts as absent). cert_valid counts only as the JSON
    boolean true, so "false" or 1 is not valid. A qid that appears more than once is invalid."""
    first: dict[str, bool] = {}
    duplicated: set[str] = set()
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not isinstance(row.get("qid"), str):
                continue
            qid = row["qid"]
            if qid in first:
                duplicated.add(qid)
            else:
                first[qid] = row.get("cert_valid") is True
    return {qid: ok and qid not in duplicated for qid, ok in first.items()}


def feasibility_utility(answer: Any, gold: dict[str, Any], cert_valid: bool) -> float:
    """Correct decision plus a valid certificate. UNRESOLVED needs a gold-accepted missing code."""
    return _decision_utility(answer, gold, FEAS_DECISIONS, cert_valid)


def partition_utility(answer: Any, gold: dict[str, Any], cert_valid: bool) -> float:
    return _decision_utility(answer, gold, PART_DECISIONS, cert_valid)


def _check_gold(qid: str, family: str, gold: Any) -> None:
    """Gold answers must be well-formed for their family; an error shape is a broken fixture."""
    if isinstance(gold, dict) and not gold.get("error"):
        if family == "closure" and _label_lists(gold.get("labels")) is not None:
            return
        if family == "selection" and _str_list(gold.get("ids")) is not None:
            return
        if family == "feasibility" and gold.get("decision") in FEAS_DECISIONS:
            return
        if family == "partition" and gold.get("decision") in PART_DECISIONS:
            return
    raise ValueError(f"gold {family} answer for {qid} is missing, malformed or an error; fix the fixture")


def query_utility(
    query: dict[str, Any], answer: Any, gold: dict[str, Any], cert_valid: bool, weights: ClassWeights | None = None,
) -> float:
    """Absent (None), malformed or error answers score 0."""
    family = query["family"]
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r} for {query['qid']}")
    _check_gold(query["qid"], family, gold)
    if answer is None:
        return 0.0
    if family == "closure":
        return closure_utility(answer, gold["labels"], weights)
    if family == "selection":
        return selection_utility(answer, gold["ids"], weights)
    if family == "feasibility":
        return feasibility_utility(answer, gold, cert_valid)
    return partition_utility(answer, gold, cert_valid)


def q_n(family_means: dict[str, float]) -> float:
    """Q_N = 100 * mean over the four families of the mean query utility."""
    return 100.0 * sum(family_means[f] for f in FAMILIES) / len(FAMILIES)


def _query_false_assertions(
    q: dict[str, Any], pred: Any, gold: dict[str, Any], weights: ClassWeights | None = None,
) -> dict[str, float]:
    """Outcome IDs a VALID closure answer asserts ENTAILED / REFUTED where gold disagrees (their weight when
    `weights` is given). Valid answers cover the gold universe exactly, so every asserted ID is inside it."""
    counts = {"false_entailment": 0, "false_refutation": 0}
    gold_labels = gold.get("labels") if isinstance(gold, dict) else None
    if q["family"] != "closure" or _label_lists(gold_labels) is None:
        return counts
    lists = _closure_lists(pred, gold_labels)
    if lists is None:
        return counts
    for key, label in (("false_entailment", "ENTAILED"), ("false_refutation", "REFUTED")):
        counts[key] += _mass(set(lists[label]) - set(gold_labels[label]), weights)
    return counts


def false_assertions(
    queries: Iterable[dict[str, Any]], answers: Any, gold: Any, weights: ClassWeights | None = None,
) -> dict[str, float]:
    """Outcome IDs asserted ENTAILED (or REFUTED) by a closure answer where gold disagrees."""
    counts: dict[str, float] = {"false_entailment": 0, "false_refutation": 0}
    for q in queries:
        for key, n in _query_false_assertions(q, answers.get(q["qid"]), gold[q["qid"]], weights).items():
            counts[key] += n
    return counts


def _anchor_flags(design: Iterable[dict[str, Any]] | None) -> dict[str, bool] | None:
    """qid -> anchor flag from design rows. Rows without an `anchor` key are left out; None means no design."""
    if design is None:
        return None
    flags: dict[str, bool] = {}
    for row in design:
        if "anchor" not in row:
            continue
        qid, anchor = row["qid"], row["anchor"]
        if not isinstance(anchor, bool):
            raise ValueError(f"anchor of {qid!r} must be true or false")
        if qid in flags:
            raise ValueError(f"duplicate design row for {qid!r}")
        flags[qid] = anchor
    return flags


def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def design_digest(rows: Iterable[dict[str, Any]]) -> str:
    """SHA-256 of the design rows in canonical JSON, one per line. A measurement records it, so that score_run can refuse a
    measured N_active taken on a different design."""
    text = "".join(ir.dumps(row) + chr(10) for row in rows)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _measured_n_active(design: list[dict[str, Any]] | None, measured: dict[str, Any] | None,
                       queries_sha256: str | None = None, registry_sha256: str | None = None) -> int | None:
    """The measured N_active (a workload_measured.json; its writer, workload_exact, was removed on 2026-10-27 with the
    exact-scale track, so no caller passes one now), checked against the design:
    None when no measurement is given; ValueError when it was taken on another design or disagrees with the design's
    per-row bounds. The design's own active_units sum is never reported as N_active."""
    if measured is None:
        return None
    if design is None:
        raise ValueError("a measured N_active needs the design it was taken on")
    if queries_sha256 is None:
        raise ValueError("a measured N_active needs queries_sha256: the SHA-256 of the scored queries file it was taken on")
    if measured.get("queries_sha256") != queries_sha256:
        raise ValueError("measured N_active was taken on different queries (queries_sha256 differs)")
    if registry_sha256 is None:
        raise ValueError("a measured N_active needs registry_sha256: the registry digest it was taken on")
    if measured.get("registry_sha256") != registry_sha256:
        raise ValueError("measured N_active was taken on a different registry (registry_sha256 differs)")
    if measured.get("design_sha256") != design_digest(design):
        raise ValueError("measured N_active was taken on a different query design")
    declared = {row["qid"]: row["active_units"] for row in design if "active_units" in row}
    if not declared:  # a design that declares no active units has no N_active to measure
        return None
    per_row = measured.get("per_row")
    if not isinstance(per_row, dict) or set(per_row) != set(declared):
        raise ValueError("measured N_active does not cover the design rows that carry active_units")
    if any(type(n) is not int or not 0 <= n <= declared[q] for q, n in per_row.items()):
        raise ValueError("measured N_active exceeds the units its design row declares")
    if measured.get("N_active") != sum(per_row.values()):
        raise ValueError("measured N_active disagrees with its per-row counts")
    return measured["N_active"]


def _decisive(pred: Any, gold_labels: Any) -> tuple[int, int]:
    """(correct, total) over the gold ENTAILED and REFUTED outcomes of one closure query. An outcome is correct when a valid
    predicted answer gives it the same label; an invalid answer is correct on none of them."""
    lists = _label_lists(gold_labels)
    if lists is None:
        return 0, 0
    total = len(lists["ENTAILED"]) + len(lists["REFUTED"])
    got = _closure_lists(pred, lists)
    if got is None:
        return 0, total
    picked = {label: set(got[label]) for label in DECISIVE}
    correct = sum(1 for label in DECISIVE for x in lists[label] if x in picked[label])
    return correct, total


def _subset_block(per_query: dict[str, tuple[str, float]], flags: dict[str, bool] | None, want: bool) -> dict[str, Any]:
    """Q_N and exact success over the queries whose design row has anchor == want. `uncovered` counts the design rows of
    this block that have no query in the workload; `families_missing` lists the families with no query in the block."""
    if flags is None:
        return {"queries": 0, "Q_N": None, "status": "no_design"}
    uncovered = sum(1 for qid, flag in flags.items() if flag is want and qid not in per_query)
    picked = [(family, u) for qid, (family, u) in per_query.items() if flags.get(qid) is want]
    if not picked:
        return {"queries": 0, "Q_N": None, "status": "not_applicable", "uncovered": uncovered,
                "families_missing": list(FAMILIES)}
    means = {f: _mean([u for family, u in picked if family == f]) for f in FAMILIES}
    return {
        "queries": len(picked),
        "Q_N": q_n(means),
        "exact_query_success": sum(1 for _, u in picked if u == 1.0) / len(picked),
        "status": "ok",
        "uncovered": uncovered,
        "families_missing": [f for f in FAMILIES if not any(family == f for family, _ in picked)],
    }


def score_run(
    queries: Iterable[dict[str, Any]],
    answers: Any,
    gold: Any,
    verdicts: Any,
    weights: ClassWeights | None = None,
    design: Iterable[dict[str, Any]] | None = None,
    measured: dict[str, Any] | None = None,
    queries_sha256: str | None = None,
    registry_sha256: str | None = None,
) -> dict[str, Any]:
    """One pass over `queries`, holding one answer and one gold answer at a time.
    answers, gold: qid -> answer object (any mapping with `get` / `in` / `[]`, e.g. a `JsonlIndex`
    that keeps only byte offsets). verdicts: qid -> cert_valid. Duplicate query IDs raise ValueError.
    weights: ClassWeights; adds the `weighted` block. design: query_design rows; their `anchor` flags split the
    `anchor` and `active` blocks (both always present; status says why one is empty). measured: a workload
    measurement (workload_measured.json; its writer was removed with the exact-scale track on 2026-10-27); its N_active is
    reported only when it matches the design, and N_active is None without it. queries_sha256 (the SHA-256 of the scored queries file, hashlib.sha256 of its bytes) is
    required whenever measured is given, and must equal the measurement's. API only: the `score` command does not pass
    measured."""
    design = None if design is None else list(design)  # read twice: anchor flags, then the per-row keys
    flags = _anchor_flags(design)
    per_query: dict[str, tuple[str, float]] = {}
    decisive = [0, 0]  # closure: correct and total gold ENTAILED/REFUTED outcomes
    stratum_of = {row["qid"]: row["stratum"] for row in design or () if row.get("stratum")}
    weighted: dict[str, list[float]] = {family: [] for family in FAMILIES}
    false_counts = {"false_entailment": 0, "false_refutation": 0}
    weighted_false: dict[str, list[float]] = {"false_entailment": [], "false_refutation": []}
    for q in queries:
        qid, family = q["qid"], q["family"]
        if family not in FAMILIES:
            raise ValueError(f"unknown family {family!r} for {qid}")
        if qid in per_query:
            raise ValueError(f"duplicate query qid {qid!r}; the workload is invalid")
        if qid not in gold:
            raise ValueError(f"no gold answer for {qid}")
        pred, gold_answer = answers.get(qid), gold[qid]
        cert_valid = bool(verdicts.get(qid, False))
        utility = query_utility(q, pred, gold_answer, cert_valid)
        per_query[qid] = (family, utility)
        if family == "closure":
            correct, total = _decisive(pred, gold_answer.get("labels") if isinstance(gold_answer, dict) else None)
            decisive[0] += correct
            decisive[1] += total
        for key, n in _query_false_assertions(q, pred, gold_answer).items():
            false_counts[key] += n
        if weights is not None:
            weighted[family].append(query_utility(q, pred, gold_answer, cert_valid, weights))
            for key, mass in _query_false_assertions(q, pred, gold_answer, weights).items():
                weighted_false[key].append(mass)
    families = {}
    for family in FAMILIES:
        vals = [u for f, u in per_query.values() if f == family]
        families[family] = {
            "queries": len(vals),
            "mean_utility": _mean(vals),
            "exact": sum(1 for v in vals if v == 1.0),
        }
    exact = sum(1 for _, u in per_query.values() if u == 1.0)
    report: dict[str, Any] = {
        "Q_N": q_n({f: families[f]["mean_utility"] for f in FAMILIES}),
        "families": families,
        # A family with no queries contributes 0 to Q_N; this list makes that visible.
        "families_missing": [f for f in FAMILIES if families[f]["queries"] == 0],
        "queries": len(per_query),
        "exact_query_success": exact / len(per_query) if per_query else 0.0,
        **false_counts,
        "per_query": {qid: u for qid, (_, u) in per_query.items()},
        "anchor": _subset_block(per_query, flags, True),
        "active": _subset_block(per_query, flags, False),
        "design_absent": sorted(qid for qid in (flags or {}) if qid not in per_query),
        # additive keys of the exact workload: N_active is the measured value (see measured), None without one;
        # strata groups the utilities of the queries whose design row names a stratum
        "N_active": _measured_n_active(design, measured, queries_sha256, registry_sha256),
        "closure_decisive_accuracy": decisive[0] / decisive[1] if decisive[1] else None,
        "strata": {name: {"queries": len(vals), "mean_utility": _mean(vals)} for name, vals in sorted(
            _strata_utilities(per_query, stratum_of).items())},
    }
    if weights is not None:
        weighted_families = {
            family: {"queries": len(vals), "mean_utility": math.fsum(vals) / len(vals) if vals else 0.0}
            for family, vals in weighted.items()
        }
        report["weighted"] = {
            "Q_N": q_n({f: weighted_families[f]["mean_utility"] for f in FAMILIES}),
            "families": weighted_families,
            **{key: math.fsum(vals) for key, vals in weighted_false.items()},
        }
    return report


def _strata_utilities(per_query: dict[str, tuple[str, float]], stratum_of: dict[str, str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for qid, name in stratum_of.items():
        if qid in per_query:
            out.setdefault(name, []).append(per_query[qid][1])
    return out


class BaselineAnswers:
    """Synthetic answers for the non-engine baselines. Certificates are absent, so SAT/UNSAT/true/false
    decisions cannot score (verdicts are empty): these baselines are the floor for those families. The
    empty-theory baseline is the `empty` adapter run through build/seal/evaluate.

    Gold-informed baselines (GOLD_INFORMED_BASELINES) read the gold answers:
    - `constant-answer` uses the most frequent gold closure label and gold decision per family.
    - `all-unresolved` and `constant-answer` put the most frequent gold missing code of each family into
      UNRESOLVED answers, so they can score on the qualifier check. They are upper bounds for
      constant strategies, not gold-blind baselines. The CLI marks reports with baseline_gold_informed."""

    def __init__(self, name: str, queries: Iterable[dict[str, Any]], gold: Any):
        if name not in BASELINES:
            raise ValueError(f"unknown baseline {name!r}")
        self.name, self.gold = name, gold
        self.gold_informed = name in GOLD_INFORMED_BASELINES
        self.queries: dict[str, dict[str, Any]] = {}
        label_counts: Counter[str] = Counter()
        self.decision_counts: dict[str, Counter[str]] = {"feasibility": Counter(), "partition": Counter()}
        self.missing_counts: dict[str, Counter[str]] = {"feasibility": Counter(), "partition": Counter()}
        for q in queries:
            if q["qid"] in self.queries:
                raise ValueError(f"duplicate query qid {q['qid']!r}; the workload is invalid")
            self.queries[q["qid"]] = q
            family = q["family"]
            if family not in FAMILIES:
                raise ValueError(f"unknown family {family!r} for {q['qid']}")
            g = gold[q["qid"]]
            _check_gold(q["qid"], family, g)
            if family == "closure":
                label_counts.update({label: len(g["labels"][label]) for label in LABELS})
            elif family in self.decision_counts:
                self.decision_counts[family][g["decision"]] += 1
                if g["decision"] == "UNRESOLVED":
                    self.missing_counts[family].update(g.get("missing") or [])
        self.constant_label = label_counts.most_common(1)[0][0] if label_counts else "CONTINGENT"

    def _missing(self, family: str, decision: str) -> list[str]:
        counts = self.missing_counts[family]
        if decision != "UNRESOLVED" or not counts:
            return []
        return [counts.most_common(1)[0][0]]

    def get(self, qid: str, default: Any = None) -> Any:
        """Built on demand, so a closure answer over a huge universe is never held with the others."""
        q = self.queries.get(qid)
        if q is None:
            return default
        name, family = self.name, q["family"]
        if family == "closure":
            label = {"all-contingent": "CONTINGENT", "all-unresolved": "UNRESOLVED"}.get(name, self.constant_label)
            g = self.gold[qid]
            ids = [x for lab in LABELS for x in g["labels"][lab]]
            labels: dict[str, list[str]] = {lab: [] for lab in LABELS}
            labels[label] = ids
            return {"qid": qid, "labels": labels}
        if family == "selection":
            return {"qid": qid, "ids": []}
        if family == "feasibility":
            decision = {"all-contingent": "SAT", "all-unresolved": "UNRESOLVED"}.get(name)
            if decision is None:
                decision = self.decision_counts["feasibility"].most_common(1)[0][0]
            return {"qid": qid, "decision": decision, "witness": None, "core": None,
                    "missing": self._missing("feasibility", decision)}
        if family == "partition":
            decision = {"all-contingent": "false", "all-unresolved": "UNRESOLVED"}.get(name)
            if decision is None:
                decision = self.decision_counts["partition"].most_common(1)[0][0]
            return {"qid": qid, "decision": decision, "counterexample": None,
                    "missing": self._missing("partition", decision)}
        return default


def baseline_answers(name: str, queries: list[dict[str, Any]], gold: dict[str, Any]) -> dict[str, Any]:
    """All baseline answers at once (small inputs); `BaselineAnswers` builds them lazily."""
    lazy = BaselineAnswers(name, queries, gold)
    return {q["qid"]: lazy.get(q["qid"]) for q in queries}

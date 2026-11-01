"""Scores the raw decisions of one curated decision suite against its gold suite labels (Mentat-Hard spec section 5).

Inputs follow docs/mentat_hard_contracts.md: candidates.jsonl (the (a, b) order each relation question is asked in),
gold/suite_labels.jsonl (one label per qid) and decisions.jsonl (raw answers, scored as answered: no floor is applied).
Items are joined by qid; a label without a decision is unanswered.

decisions.jsonl is read robustly (a resumed build may append to it). A final line with no trailing newline, or that is not
JSON, is a torn append and is ignored (counted as torn_final_line). A row that repeats an earlier row of the same qid exactly
is dropped (duplicate_identical); two different rows for one qid raise.

Conf: the top-level conf, else the decision's own conf. A conf that is not a finite number (check_conf) makes the row
unanswered ("__missing__", counted as invalid_conf), so it is wrong and it is not named by the floor calibration.

Orientation: a relation decision whose logged (a, b) is the reverse of the candidate's is oriented to the candidate order.
IMPLIES and IMPLIED_BY swap, in the relation and in its probs.

Predicted classes. Unanswered or errored items are "__missing__": wrong, and they lower coverage. Their probs are ignored.
- relation: the decision's relation when it is EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES or NONE. Any other value, UNRESOLVED
  included, is "__missing__": such an answer is not credited as NONE. A bare {"relation": "NONE"} is credited as NONE, so the
  child's abstention for length (written in that form) is scored as a NONE answer (pre-registered; see the results README).
- group: "none" when groups == [] (or the decision is an empty list), else the first group's kind. at_least_k and at_most_k
  carry their k ("at_least_k:3"), so a wrong k is a wrong class, as the canonical label carries k. A predicted at_most_k
  with k = 1 is at_most_one (the same constraint, which the canonical label never names as at_most_k). Group direction is
  not compared. The diagnostic group kind_accuracy ignores k.
- group with "terms" (the v2 labels): the class is the canonical terms of the answer's bounds (_terms_answer): lo is the
  largest lower bound and hi the smallest upper bound over the unscoped decisions on the row's members, chain is whether an
  ordered_chain is among them, and the class is group_terms(lo, hi, n, chain) joined by "+" ("invalid" when lo > hi;
  "none" with no decisions). The truth is the same join of the label's terms. Malformed siblings are skipped, and the row
  is "__missing__" only when no decision is valid and one is malformed (_checked_groups).
- reading (the v2 reading questions, one item per reading): the counting chains of reading r are the valid ordered_chain
  decisions that are unscoped or whose scope includes r. Two of them that are the two directions of the proposition u with
  members[i] give "opt<i>" (base.reading_picks); none gives "none"; any other set gives "invalid". An unscoped pair therefore
  answers both readings.
- binding: the status, bound or unresolved (anything else is "__missing__").

Chain order (secondary, groups): for ordered_chain labels with an order (strongest first), chain_order counts the labelled
chains predicted with an ordered_chain term whose implied order equals the label's. The implied order of a decision's first
group is its members as listed for direction forward (the harness links member i to member i+1) and reversed for direction
reverse. For a group with terms it is the first unscoped chain over the row's members.

Reading questions (a label with "readings") are split into two items, base and alt, each scored as decide_reading.
Their probs are not read: a reading answer is a list, with no per-option probabilities.

Probs are used only for answered items whose prediction is not "__missing__". Brier and ECE score the probs over the option
keys of their op. For groups the option keys are the kinds, so group Brier and ECE are over kind probabilities only (k is not
scored there), and the top label of a group is its kind.

Metrics per op: multiclass MCC (Gorodkin R_K, 0 when the marginals are constant, None when the op has no items), macro-F1
over the classes that occur (true or predicted), "__missing__" excluded, accuracy (missing counted wrong), per-class
precision/recall/F1 (None when undefined), the confusion matrix, Brier (items without probs are excluded and counted) and
ECE (10 equal-width bins on the top label's probability). Each op is also reported per stratum and per seen/held-out split.
The headline is chosen by the major fixture version (fixture.json "version", fixture_version; headline_for). A fixture.json with
no "version" (the toy fixtures) gets the legacy headline, as versions 1 and 2 do:
- versions 1 and 2 (owner decision 2026-10-29): the relation MCC alone. `headline` = {"metric": "relation_mcc", "relation_mcc": x,
  "secondary": {"binding_mcc": y}, "diagnostics": {"group_mcc": g, "reading_mcc": r}}. The group MCC is a diagnostic there
  because the v2 groups were invalid (docs/history/round-2/headline-change-disclosure.md).
- version 3 and later (owner decision 2026-10-30): the mean of the relation and group MCCs. `headline` = {"metric":
  "relation_group_mcc", "relation_group_mcc": (x + g) / 2, "parts": {"relation_mcc": x, "group_mcc": g}, "secondary":
  {"binding_mcc": y}, "diagnostics": {"reading_mcc": r}}. The mean is null when either part is null (its op has no items).
A value is null when its op has no items. Readings and Q_N stay diagnostics. The per-op `ops` blocks are the same as before,
and a v1 or v2 run's output keeps its keys and bytes. Readers that predate this layout (the old headline with mcc_mean,
mcc_mean_3 and components) read the per-op `ops.<op>.mcc` instead.
The decide_reading block appears only when the run has reading items.
Group rows with terms are not scored by probs (the child's probs are over kinds, which do not name a compound class), so they
are excluded from the group Brier and ECE and counted as excluded.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import ir
from .adapters.base import READINGS, DecisionError, check_conf, check_group, group_terms, reading_picks, terms_class
from .scoring import f1

MISSING = "__missing__"
OPTIONS = {
    "decide_relation": ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE"),
    "decide_group": ("none", "exactly_one", "ordered_chain", "at_least_k", "at_most_k", "at_most_one"),
    "decide_binding": ("bound", "unresolved"),
    "decide_reading": ("opt1", "opt2", "opt3", "opt4", "none", "invalid"),  # opt<i>: members[i] of the reading question
}
OPS = tuple(OPTIONS)
COMPONENT = {"decide_relation": "relations", "decide_group": "groups", "decide_binding": "bindings", "decide_reading": "readings"}
NAMED = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES")  # the relations a decision can name (NONE names nothing)
GROUP_KINDS = ("exactly_one", "ordered_chain", "at_least_k", "at_most_k", "at_most_one")
K_KINDS = ("at_least_k", "at_most_k")
FLIP = {"IMPLIES": "IMPLIED_BY", "IMPLIED_BY": "IMPLIES"}
BINS = 10
PROB_TOLERANCE = 1e-2
RELATION_GROUP_VERSION = 3  # the first fixture major version whose headline is the relation and group mean (owner decision 2026-10-30)
VERSION = re.compile(r"[0-9]+(\.[0-9]+)*")  # fixture.json "version": "3", "3.1" (ASCII digits only)


def class_key(kind: str, k: Any) -> str:
    """The predicted or label class of a group kind: the kind, with k for at_least_k and at_most_k."""
    return f"{kind}:{k}" if kind in K_KINDS and k is not None else kind


def read_decisions(path: Path) -> tuple[list[dict], dict]:
    """The decision rows of a checkpointed decisions.jsonl, and counts of the torn final line and identical duplicates."""
    lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines(keepends=True) if line.strip()]
    torn = 0
    if lines and not lines[-1].endswith("\n"):
        lines.pop()
        torn = 1
    rows: list[dict] = []
    for i, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines) - 1:
                raise ValueError(f"{path}: line {i + 1} is not JSON") from None
            torn = 1
    kept: list[dict] = []
    by_qid: dict[str, dict] = {}
    duplicates = 0
    for row in rows:
        qid = row.get("qid")
        if qid is None:
            kept.append(row)
        elif qid in by_qid:
            if by_qid[qid] != row:
                raise ValueError(f"conflicting decisions for qid {qid!r}")
            duplicates += 1
        else:
            by_qid[qid] = row
            kept.append(row)
    return kept, {"torn_final_line": torn, "duplicate_identical": duplicates}


def _by_qid(path: Path, what: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for row in ir.read_jsonl(path):
        if row["qid"] in rows:
            raise ValueError(f"duplicate qid {row['qid']!r} in {what}")
        rows[row["qid"]] = row
    return rows


def _flipped(qid: str, cands: dict[str, dict], row: dict | None) -> bool:
    """True when the logged (a, b) is the reverse of the candidate's; ValueError when it is neither order."""
    if row is None or row.get("a") is None or row.get("b") is None:
        return False
    if qid not in cands:
        raise ValueError(f"relation decision {qid!r} has no candidate")
    ca, cb = cands[qid]["a"], cands[qid]["b"]
    if (row["a"], row["b"]) == (ca, cb):
        return False
    if (row["a"], row["b"]) == (cb, ca):
        return True
    raise ValueError(f"relation decision {qid!r} asks ({row['a']!r}, {row['b']!r}), neither order of its candidate")


def _conf(row: dict) -> float | None:
    """The row's conf: top level, else the decision's own. DecisionError when it is not a finite number."""
    value = row.get("conf")
    if value is None and isinstance(row.get("decision"), dict):
        value = row["decision"].get("conf")
    return check_conf(value)


def _predict(op: str, decision: Any) -> str:
    if op == "decide_relation":
        rel = decision.get("relation") if isinstance(decision, dict) else decision
        return rel if rel in OPTIONS[op] else MISSING
    if op == "decide_group":
        groups = decision.get("groups") if isinstance(decision, dict) else decision
        if not isinstance(groups, list):
            return MISSING
        if not groups:
            return "none"
        first = groups[0]
        if not isinstance(first, dict) or first.get("kind") not in GROUP_KINDS:
            return MISSING
        if first["kind"] == "at_most_k" and first.get("k") == 1:  # the same constraint as at_most_one
            return "at_most_one"
        return class_key(first["kind"], first.get("k"))
    status = decision.get("status") if isinstance(decision, dict) else None
    return status if status in OPTIONS[op] else MISSING


def _implied_chain(decision: Any) -> list | None:
    """The implied order, strongest first, of a decision's first group: members as listed (forward), reversed (reverse)."""
    groups = decision.get("groups") if isinstance(decision, dict) else decision
    first = groups[0] if isinstance(groups, list) and groups and isinstance(groups[0], dict) else None
    members = first.get("members") if first else None
    if not isinstance(members, list):
        return None
    return list(reversed(members)) if first.get("direction") == "reverse" else list(members)


def _probs(op: str, probs: Any, flipped: bool, qid: str) -> dict[str, float] | None:
    """The probs of one answered decision, validated and oriented to the candidate order (None when absent)."""
    if probs is None:
        return None
    options = OPTIONS[op]
    if not isinstance(probs, dict) or set(probs) != set(options):
        raise ValueError(f"probs of {qid!r} must have exactly the keys {list(options)}")
    for p in probs.values():
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError(f"probs of {qid!r} must be finite numbers in [0, 1], got {p!r}")
    if abs(sum(probs.values()) - 1) > PROB_TOLERANCE:
        raise ValueError(f"probs of {qid!r} sum to {sum(probs.values())!r}, not 1")
    return {FLIP.get(k, k) if flipped else k: float(p) for k, p in probs.items()}


def _answer(row: dict | None) -> tuple[float | None, bool, bool]:
    """(conf, invalid_conf, answered) of a decision row: an absent row is unanswered, and so is an errored or badly confed one."""
    conf, invalid = None, False
    if row is not None:
        try:
            conf = _conf(row)
        except DecisionError:
            invalid = True
    return conf, invalid, row is not None and row.get("error") is None and not invalid


def _checked_groups(decision: Any, members: list[str]) -> list[dict] | None:
    """The valid group decisions of an answer (check_group), normalised. A malformed sibling is skipped. None (MISSING) when
    the answer is not a list of decisions, or when it has a malformed decision and no valid one."""
    groups = decision.get("groups") if isinstance(decision, dict) else decision
    if not isinstance(groups, list):
        return None
    valid, malformed = [], 0
    for g in groups:
        try:
            valid.append(check_group(g, members))
        except DecisionError:
            malformed += 1
    return None if malformed and not valid else valid


def _terms_answer(decision: Any, members: list[str]) -> tuple[str, list | None]:
    """(class, implied chain) of a group with terms: over the unscoped valid decisions on its members, lo is the largest lower
    bound (exactly_one, at_least_k), hi the smallest upper bound (exactly_one, at_most_one, at_most_k), and chain says whether
    an ordered_chain is asked. The class is that of group_terms(lo, hi, n, chain); lo > hi is invalid, and no decisions is
    none. MISSING only when no decision is valid and one is malformed."""
    checked = _checked_groups(decision, members)
    if checked is None:
        return MISSING, None
    own = [g for g in checked if g["scope"] is None and set(g["members"]) == set(members)]
    lo, hi = 0, len(members)
    for g in own:
        if g["kind"] in ("exactly_one", "at_least_k"):
            lo = max(lo, g["k"])
        if g["kind"] in ("exactly_one", "at_most_one", "at_most_k"):
            hi = min(hi, g["k"])
    chains = [g for g in own if g["kind"] == "ordered_chain"]
    chain = _implied_chain({"groups": chains[:1]})
    if lo > hi:
        return "invalid", chain
    return terms_class(group_terms(lo, hi, len(members), bool(chains))), chain


def _reading_picks(decision: Any, members: list[str]) -> dict[str, str] | None:
    """The picks of a reading answer (base.reading_picks). None (MISSING) when no decision is valid and one is malformed, as
    _checked_groups decides for every group answer."""
    if _checked_groups(decision, members) is None:
        return None
    return reading_picks(decision, members)


def _item(qid: str, label: dict, cands: dict[str, dict], row: dict | None) -> dict:
    op = label["op"]
    if op not in OPTIONS:
        raise ValueError(f"suite label {qid!r} has unknown op {op!r}")
    if row is not None and row.get("op") != op:
        raise ValueError(f"decision {qid!r} is op {row.get('op')!r}, its label is {op!r}")
    flipped = op == "decide_relation" and _flipped(qid, cands, row)
    conf, invalid, answered = _answer(row)
    terms = label.get("terms")
    chain = None
    if not answered:
        pred = MISSING
    elif terms is not None:
        if qid not in cands:
            raise ValueError(f"group decision {qid!r} has no candidate")
        pred, chain = _terms_answer(row.get("decision"), cands[qid]["members"])
    else:
        pred = _predict(op, row.get("decision"))
        chain = _implied_chain(row.get("decision")) if pred == "ordered_chain" else None
    if flipped:
        pred = FLIP.get(pred, pred)
    option = label["label"]
    if option not in OPTIONS[op]:
        raise ValueError(f"suite label {qid!r} is {option!r}, not one of {list(OPTIONS[op])}")
    if terms is not None:
        truth = terms_class(terms)
    else:
        truth = class_key(option, label.get("k")) if op == "decide_group" else option
    # A group with terms is not scored by probs: the child's probs are over kinds, which do not name a compound class.
    probs = _probs(op, row.get("probs"), flipped, qid) if answered and pred != MISSING and terms is None else None
    return {"qid": qid, "op": op, "stratum": label["stratum"], "held_out": bool(label["held_out"]), "truth": truth,
            "option": option, "pred": pred, "flipped": flipped, "conf": conf, "invalid_conf": invalid, "probs": probs,
            "chain": chain, "order": label.get("order")}


def _reading_items(qid: str, label: dict, cands: dict[str, dict], row: dict | None) -> list[dict]:
    """The two decide_reading items of a reading question, base then alt. Its candidate's members are u and four options:
    the truth of a reading is opt<i> for readings[reading] = members[i], or none. Probs are not read (see the docstring)."""
    if row is not None and row.get("op") != label["op"]:
        raise ValueError(f"decision {qid!r} is op {row.get('op')!r}, its label is {label['op']!r}")
    if qid not in cands:
        raise ValueError(f"reading question {qid!r} has no candidate")
    members = cands[qid]["members"]
    options = members[1:]
    conf, invalid, answered = _answer(row)
    picks = _reading_picks(row.get("decision"), members) if answered else None
    items = []
    for reading in READINGS:
        oid = label["readings"][reading]
        if oid is not None and oid not in options:
            raise ValueError(f"reading question {qid!r} names {oid!r} for {reading}, not one of its options")
        truth = "none" if oid is None else f"opt{options.index(oid) + 1}"
        if picks is None:
            pred = MISSING
        elif picks[reading] in ("none", "invalid"):
            pred = picks[reading]
        else:
            pred = f"opt{options.index(picks[reading]) + 1}"
        items.append({"qid": qid, "op": "decide_reading", "stratum": label["stratum"], "held_out": bool(label["held_out"]),
                      "truth": truth, "option": truth, "pred": pred, "flipped": False, "conf": conf, "invalid_conf": invalid,
                      "probs": None, "chain": None, "order": None})
    return items


def load(labels_path: Path, candidates_path: Path, decisions_path: Path) -> tuple[list[dict], dict]:
    """(items, counts). Items are in label order: one per label, and two (base, alt) per reading question. A decision with
    no qid, or a qid with no label, is not scored. invalid_conf counts items (a reading question has two)."""
    labels = _by_qid(labels_path, "suite labels")
    cands = _by_qid(candidates_path, "candidates")
    rows, read = read_decisions(decisions_path)
    decided = {row["qid"]: row for row in rows if row.get("qid") is not None}
    items = []
    for qid, label in labels.items():
        if "readings" in label:
            items += _reading_items(qid, label, cands, decided.get(qid))
        else:
            items.append(_item(qid, label, cands, decided.get(qid)))
    counts = {"labels": len(labels), "decisions": len(rows),
              "decisions_without_qid": sum(row.get("qid") is None for row in rows),
              "decisions_unlabelled": len(set(decided) - set(labels)),
              "invalid_conf": sum(it["invalid_conf"] for it in items),
              "oriented": sum(it["flipped"] for it in items), **read}
    return items, counts


def _kind(group_class: str) -> str:
    """The kinds of a group class, k ignored (each term's kind, joined as in the class)."""
    return "+".join(term.split(":")[0] for term in group_class.split("+"))


def _ratio(k: int, n: int) -> float | None:
    return k / n if n else None


def mcc(pairs: list[tuple[str, str]]) -> float:
    """Multiclass MCC (Gorodkin's R_K) over (truth, predicted) pairs. 0 for no pairs or a constant marginal."""
    n = len(pairs)
    truth, pred = Counter(t for t, _ in pairs), Counter(p for _, p in pairs)
    hits = sum(t == p for t, p in pairs)
    cov = hits * n - sum(truth[c] * pred[c] for c in truth.keys() | pred.keys())
    var_t, var_p = n * n - sum(v * v for v in truth.values()), n * n - sum(v * v for v in pred.values())
    return cov / math.sqrt(var_t * var_p) if var_t and var_p else 0.0


def brier(items: list[dict]) -> dict:
    scored = [it for it in items if it["probs"] is not None]
    total = sum(sum((p - float(o == it["option"])) ** 2 for o, p in it["probs"].items()) for it in scored)
    return {"value": _ratio(total, len(scored)), "n": len(scored), "excluded": len(items) - len(scored)}


def ece(items: list[dict]) -> dict:
    scored = [it for it in items if it["probs"] is not None]
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(BINS)]
    for it in scored:
        top = max(sorted(it["probs"]), key=it["probs"].__getitem__)  # ties go to the first option in sorted order
        conf = it["probs"][top]
        bins[min(int(conf * BINS), BINS - 1)].append((conf, top == it["option"]))
    table = [{"lo": i / BINS, "n": len(m), "confidence": _ratio(sum(c for c, _ in m), len(m)),
              "accuracy": _ratio(sum(ok for _, ok in m), len(m))} for i, m in enumerate(bins)]
    n = len(scored)
    value = sum(r["n"] / n * abs(r["accuracy"] - r["confidence"]) for r in table if r["n"]) if n else None
    return {"value": value, "n": n, "bins": table}


def metrics(items: list[dict]) -> dict:
    pairs = [(it["truth"], it["pred"]) for it in items]
    n = len(items)
    truth, pred = Counter(t for t, _ in pairs), Counter(p for _, p in pairs)
    classes = sorted(truth.keys() | pred.keys())
    per_class = {}
    for c in classes:
        tp = sum(t == c and p == c for t, p in pairs)
        per_class[c] = {"support": truth[c], "predicted": pred[c], "precision": _ratio(tp, pred[c]),
                        "recall": _ratio(tp, truth[c]), "f1": f1(tp, pred[c] - tp, truth[c] - tp)}
    occurring = [c for c in classes if c != MISSING]
    answered = sum(p != MISSING for _, p in pairs)
    return {
        "n": n,
        "answered": answered,
        "coverage": _ratio(answered, n),
        "accuracy": _ratio(sum(t == p for t, p in pairs), n),
        "mcc": mcc(pairs) if pairs else None,
        "macro_f1": _ratio(sum(per_class[c]["f1"] for c in occurring), len(occurring)),
        "per_class": per_class,
        "confusion": {t: dict(sorted(Counter(p for tt, p in pairs if tt == t).items())) for t in sorted(truth)},
        "brier": brier(items),
        "ece": ece(items),
    }


def op_report(items: list[dict]) -> dict:
    report = metrics(items)
    report["by_stratum"] = {s: metrics([it for it in items if it["stratum"] == s])
                            for s in sorted({it["stratum"] for it in items})}
    report["by_split"] = {name: metrics([it for it in items if it["held_out"] == held])
                          for name, held in (("seen", False), ("held_out", True))}
    return report


def chain_order(items: list[dict]) -> dict:
    """Of the group items whose label is ordered_chain with an order: how many are predicted with an ordered_chain term and the
    implied order equal to the label's (strongest first). n counts the labelled chains; accuracy is correct / n."""
    labelled = [it for it in items if it["option"] == "ordered_chain" and it["order"] is not None]
    correct = sum("ordered_chain" in it["pred"].split("+") and it["chain"] == it["order"] for it in labelled)
    return {"n": len(labelled), "correct": correct, "accuracy": _ratio(correct, len(labelled))}


def fixture_version(fixture: Path) -> int | None:
    """The major version of a fixture directory, from fixture.json's "version" ("3" and "3.1" are 3; "2.9" is 2). None when
    fixture.json has no "version" (the toy fixtures): the legacy headline. ValueError for any other value (a "v3", "3.", "3.x",
    a superscript digit, a number), and OSError when there is no fixture.json. The switch reads this file, not the registry
    number (fixtures/VERSIONS.json)."""
    doc = json.loads((fixture / "fixture.json").read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{fixture}/fixture.json is not a JSON object")
    if "version" not in doc:
        return None
    version = doc["version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError(f"{fixture}/fixture.json has version {version!r}, not a version such as \"3\" or \"3.1\"")
    return int(version.split(".")[0])


def relation_group(version: int | None) -> bool:
    """True for a fixture version whose headline is the relation and group mean (version 3 and later); None is legacy."""
    return version is not None and version >= RELATION_GROUP_VERSION


def headline_for(version: int | None, mcc: dict[str, float | None]) -> dict:
    """The headline block for a fixture version (None: no version, the legacy headline), from the per-op MCCs (keyed by op;
    None when the op has no items)."""
    rel, grp = mcc["decide_relation"], mcc["decide_group"]
    secondary, reading = {"binding_mcc": mcc["decide_binding"]}, mcc["decide_reading"]
    if not relation_group(version):
        return {"metric": "relation_mcc", "relation_mcc": rel, "secondary": secondary,
                "diagnostics": {"group_mcc": grp, "reading_mcc": reading}}
    mean = None if rel is None or grp is None else (rel + grp) / 2
    return {"metric": "relation_group_mcc", "relation_group_mcc": mean, "parts": {"relation_mcc": rel, "group_mcc": grp},
            "secondary": secondary, "diagnostics": {"reading_mcc": reading}}


def score(items: list[dict], counts: dict, version: int | None) -> dict:
    readings = any(it["op"] == "decide_reading" for it in items)  # v1 output has no reading block, so its bytes do not change
    ops = {op: op_report([it for it in items if it["op"] == op]) for op in OPS if readings or op != "decide_reading"}
    groups = [it for it in items if it["op"] == "decide_group"]
    ops["decide_group"]["kind_accuracy"] = _ratio(sum(_kind(it["truth"]) == _kind(it["pred"]) for it in groups), len(groups))
    ops["decide_group"]["chain_order"] = chain_order(groups)
    mcc = {op: ops[op]["mcc"] if op in ops else None for op in OPS}  # None for an op with no items (or not in this run)
    return {"headline": headline_for(version, mcc), "ops": ops, "counts": counts}


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.4f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decisions", type=Path, required=True)
    ap.add_argument("--labels", type=Path, required=True)
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--fixture", type=Path, required=True, help="the fixture directory: its fixture.json version picks the headline")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    items, counts = load(a.labels, a.candidates, a.decisions)
    out = score(items, counts, fixture_version(a.fixture))
    a.out.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    h = out["headline"]
    print(f"headline {h['metric']} {_fmt(h[h['metric']])}; secondary binding MCC {_fmt(h['secondary']['binding_mcc'])}; "
          f"diagnostics readings {_fmt(h['diagnostics']['reading_mcc'])}; "
          f"labels {counts['labels']}, decisions {counts['decisions']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Consensus, review, selection, fixture and statistics of the transfer pilot (PREREG.md, "Labels", "Review", "Selection",
"Fixture" and "Statistics"). Every rule the PREREG states is pinned here.

usage: python -B analyse.py consensus    the judges' canonical answers -> consensus.jsonl and consensus_summary.json (published) and the
                                         review sheet (LOCAL). Refused while a sheet has any your_label filled.
       python -B analyse.py review       apply the review sheets (strictly) -> review_labels.jsonl and final_labels.jsonl (published).
                                         The sheet's ids and reasons must equal the ones the consensus and the seeded spot draw give.
                                         When more than two spot-check items changed, it writes the escalation sheet and stops until
                                         that sheet is complete.
       python -B analyse.py select       the seeded, cell-allocated sample -> selection.jsonl and selection_report.json (published)
       python -B analyse.py fixture      the fixture of the selected items: corpus.jsonl is LOCAL (outside the repository); the rest is
                                         published with hashes
       python -B analyse.py score PREDICTIONS   the statistics -> results.json, for PREDICTIONS = {model: {item id: class}} with the 17
                                         models and the two baselines (lexical, heuristic), each with every selected item

Every step checks frozen.json (score.check_frozen), and every published output records the sha256 of the inputs it read. The review
sheets and the corpus hold text, so they live outside the repository: a run directory or a corpus inside the repository is refused.
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]  # the repository root
LOCAL = ROOT.parent / "mb-runs" / "transfer-pilot"
REVIEW_DIR = LOCAL / "review"
SHEET = REVIEW_DIR / "review.csv"
ESCALATION_SHEET = REVIEW_DIR / "review_escalation.csv"
LEADERBOARD = ROOT / "results" / "v0.2.0" / "leaderboard.json"
ANALYSIS = ROOT / "results" / "v0.2.0" / "analysis.json"
sys.dont_write_bytecode = True
sys.path[:0] = [str(HERE), str(ROOT / "python"), str(ROOT / "tools" / "eval")]

import build_pool as BP  # noqa: E402
import league_ci as LC  # noqa: E402
import score as SC  # noqa: E402
from mentat_bench import decision_scoring as DS  # noqa: E402
from mentat_bench.adapters.base import group_terms, terms_class  # noqa: E402

REVIEW_SEED = "transfer-pilot-review:0"  # the spot-check draw
SELECT_SEED = "transfer-pilot-select:0"  # the selection draw
SPOT_FRACTION = 0.10  # of the labelled items outside the review set, rounded up
MAX_CHANGED = 2  # more spot-check items than this changed by the reviewer: the escalation sheet is required
SELECT_RELATIONS, SELECT_GROUPS, TRIVIAL_CAP = 120, 80, 12  # trivial items are at most 10% of the relations
MIN_PER_CLASS = 5  # a relation label or group kind with fewer drawn items gets no per-class statement
SCORE_B, SCORE_SEED = 2000, 0  # league_ci.draws(B, seed, L): random.Random("league_ci:0")
MODELS_REQUIRED = 17  # the v0.2.0 models; every one answers every selected item
BASELINES = ("lexical", "heuristic")  # scale anchors, reported in results.json; not in the correlation
RELATION_LABELS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE", "UNCLEAR")
SHEET_COLUMNS = ["id", "op", "reason", "propositions", "judge1", "judge2", "judge3", "consensus", "your_label"]
REASONS = ("all_different", "two_disagree", "fewer_than_two", "spot_check", "escalation")
SHEET_HEADER = [
    "# Review sheet, transfer pilot. LOCAL: it holds question text. Fill your_label on every row.",
    "# your_label vocabulary: a relation row takes EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, NONE or UNCLEAR.",
    "# A group row takes a class in the exact terms_class form: none, exactly_one, at_most_one, at_least_k:K, at_most_k:K, ordered_chain,",
    "# or joined by + (for example at_least_k:2+at_most_k:3), as the bounds of the group allow; or UNCLEAR.",
    "# UNCLEAR drops the item. A label that is not in the vocabulary stops the run.",
    "# reason: all_different, two_disagree, fewer_than_two (review: no consensus); spot_check (the seeded sample); escalation (every",
    "# remaining 2-1 item). Only spot_check rows can count as changed.",
]
STATUS_REVIEW = "review"
STATUS_LABELLED = ("unanimous", "majority", "two_agree")


class AnalyseError(Exception):
    """A step that cannot run: an incomplete input, a sheet that does not match, a label outside the vocabulary, or a path inside the
    repository that must stay outside it."""


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refuse_repo_path(path: Path, what: str) -> None:
    """Text and run directories live outside the repository (PREREG.md, "Terms"): refuse one inside it."""
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise AnalyseError(f"{what} {resolved} is inside the repository; it must stay outside (mb-runs/transfer-pilot)")


def write_lines(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def read_lines(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def load_pool_rows() -> dict[str, dict[str, Any]]:
    """Each pool item by id: op, member count (n, None for a relation), structural type, cluster, and its outcome ids."""
    out = {}
    for row in read_lines(SC.POOL):
        outcomes = [row["a"], row["b"]] if row["op"] == "decide_relation" else row["members"]
        out[row["id"]] = {"op": row["op"], "n": None if row["op"] == "decide_relation" else len(row["members"]),
                          "structural_type": row["structural_type"], "cluster": row["cluster"], "outcomes": outcomes}
    return out


def valid_labels(op: str, n: int | None) -> set[str]:
    """The labels a reviewer may give: the relation labels, or every canonical group class for n members (group_terms over every
    bound pair, with and without the chain), and UNCLEAR. Anything else is refused when the sheet is read."""
    if op == "decide_relation":
        return set(RELATION_LABELS)
    labels = {"UNCLEAR", "none"}
    for lower in range(n + 1):
        for upper in range(lower, n + 1):
            for chain in (False, True):
                try:
                    labels.add(terms_class(group_terms(lower, upper, n, chain)))
                except ValueError:
                    pass
    return labels


# ---- consensus


def consensus_of(classes: list[str]) -> tuple[str, str | None]:
    """(status, label) from the classes of the judges. A class of __missing__ is an answer that did not count (an out-of-format
    answer, or a slice that was double void), so it leaves the valid answers to decide. With three valid answers: all equal is
    unanimous; two equal is a majority (2-1); all different is review. With two valid answers: equal is two_agree; different is
    review. With fewer than two, review. A majority or agreement on UNCLEAR is dropped, and is not reviewed."""
    valid = [c for c in classes if c != DS.MISSING]
    if len(valid) == 3:
        label, count = Counter(valid).most_common(1)[0]
        if count == 1:
            return STATUS_REVIEW, None
        status = "unanimous" if count == 3 else "majority"
    elif len(valid) == 2:
        if valid[0] != valid[1]:
            return STATUS_REVIEW, None
        label, status = valid[0], "two_agree"
    else:
        return STATUS_REVIEW, None
    if label == "UNCLEAR":
        return "dropped", None
    return status, label


def review_reason(judges: list[str]) -> str:
    """Why an item needs review: all three answers differ (all_different), two valid answers differ (two_disagree), or fewer than two
    answers are valid (fewer_than_two)."""
    valid = sum(c != DS.MISSING for c in judges)
    return "all_different" if valid == 3 else "two_disagree" if valid == 2 else "fewer_than_two"


def judged_classes(pool: dict[str, dict[str, Any]], base: Path = HERE) -> dict[str, list[str]]:
    """Each item's normalised class from each judge instance (instance 1, 2, 3). An item with no accepted answer from an
    instance has __missing__ for it."""
    out = {oid: [DS.MISSING] * len(SC.INSTANCES) for oid in pool}
    for inst in SC.INSTANCES:
        for slice_no in SC.load_slices():
            path = SC.answers_path(inst, slice_no, base)
            if not path.is_file():
                continue
            for row in read_lines(path):
                item = pool[row["id"]]
                out[row["id"]][inst - 1] = SC.normalised(item["op"], item["n"], row["answer"])
    return out


def check_complete(ledger: list[dict[str, Any]]) -> None:
    """Every instance and slice must be settled, by score.settled: an accepted attempt, or two closed attempts (double void)."""
    for inst in SC.INSTANCES:
        for slice_no in SC.load_slices():
            if not SC.settled(ledger, inst, slice_no):
                raise AnalyseError(f"inst{inst} slice{slice_no} is not settled (no accepted attempt and not double void)")


def check_answer_files(ledger: list[dict[str, Any]], base: Path) -> None:
    """Exactly one answers file for each valid ledger line, none for any other, and each file's sha256 is the one its line records."""
    valid = {(e["inst"], e["slice"]): e for e in ledger if e["verdict"] == "valid"}
    found = {(int(Path(p).parent.name[len("inst"):]), int(Path(p).stem[len("slice"):]))
             for p in glob.glob(str(base / "judge" / "answers" / "inst*" / "slice*.jsonl"))}
    if found != set(valid):
        raise AnalyseError(f"the answers files {sorted(found)} do not match the valid ledger lines {sorted(valid)}")
    for (inst, slice_no), e in valid.items():
        if sha_file(SC.answers_path(inst, slice_no, base)) != e["answers_sha256"]:
            raise AnalyseError(f"inst{inst} slice{slice_no}: the answers file differs from the sha256 its ledger line records")


def recompute_consensus(base: Path, local: Path = SC.JUDGE_LOCAL) -> dict[str, dict[str, Any]]:
    """The consensus rows recomputed from the ledger, its raw outputs and the answers files (the same rows run_consensus writes)."""
    pool = load_pool_rows()
    ledger = SC.read_ledger(base)
    SC.verify_ledger(ledger, local, base)
    check_complete(ledger)
    check_answer_files(ledger, base)
    classes = judged_classes(pool, base)
    rows = {}
    for oid in sorted(pool):
        status, label = consensus_of(classes[oid])
        rows[oid] = {"id": oid, "op": pool[oid]["op"], "status": status, "label": label, "judges": classes[oid]}
    return rows


def check_recorded_consensus(base: Path, local: Path = SC.JUDGE_LOCAL) -> dict[str, dict[str, Any]]:
    """Refuses when consensus.jsonl is not the consensus the ledger and the answers give now. Returns the recorded rows."""
    recomputed = recompute_consensus(base, local)
    recorded = {r["id"]: r for r in read_lines(base / "consensus.jsonl")}
    if recorded != recomputed:
        raise AnalyseError("consensus.jsonl differs from the consensus recomputed from the ledger and the answers")
    return recorded


def load_texts() -> dict[str, dict[str, Any]]:
    """The local texts, checked against pool.jsonl: each question, label, event title and event id must have the hash the pool
    publishes. A mismatch stops the run."""
    texts = {r["outcome"]: r for r in read_lines(LOCAL / "pool_texts.jsonl")}
    problems = BP.text_problems(read_lines(SC.POOL), texts)
    if problems:
        raise AnalyseError(f"pool_texts.jsonl does not match pool.jsonl ({len(problems)} problems, e.g. {problems[0]})")
    return texts


def proposition_text(oid_list: list[str], texts: dict[str, dict[str, Any]], op: str) -> str:
    if op == "decide_relation":
        return " | ".join(f"{'AB'[i]}: {texts[o]['question']} [{texts[o]['label']}]" for i, o in enumerate(oid_list))
    return " | ".join(f"{i + 1}: {texts[o]['question']} [{texts[o]['label']}]" for i, o in enumerate(oid_list))


def expected_sheet(consensus: dict[str, dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """What the first review sheet must hold, from the consensus alone: the review items with their reason, and the spot-check sample
    (the labelled items, sorted by id; ceil(SPOT_FRACTION * n) drawn with random.Random(REVIEW_SEED)), with reason spot_check."""
    review = {oid: review_reason(row["judges"]) for oid, row in consensus.items() if row["status"] == STATUS_REVIEW}
    labelled = sorted(oid for oid, row in consensus.items() if row["status"] in STATUS_LABELLED)
    rng = random.Random(REVIEW_SEED)
    spot = {oid: "spot_check" for oid in sorted(rng.sample(labelled, math.ceil(SPOT_FRACTION * len(labelled))))}
    return review, spot


def expected_escalation(consensus: dict[str, dict[str, Any]], spot: dict[str, str]) -> set[str]:
    """The escalation sheet holds every remaining 2-1 item: the majority items outside the spot-check sample."""
    return {oid for oid, row in consensus.items() if row["status"] == "majority" and oid not in spot}


def sheet_has_labels(path: Path) -> bool:
    with open(path, encoding="utf-8", newline="") as fh:
        body = [line for line in fh if not line.startswith("#")]
    return any(row["your_label"] for row in csv.DictReader(body))


def write_sheet(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for line in SHEET_HEADER:
            fh.write(line + "\n")
        writer = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({**{c: "" for c in SHEET_COLUMNS}, **row})


def read_sheet(path: Path, pool: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The rows of a review sheet, checked strictly: the header, a known id, a reason, and a your_label in the vocabulary of the
    item (None for UNCLEAR, which drops the item). A blank label, or one outside the vocabulary, stops the run."""
    with open(path, encoding="utf-8", newline="") as fh:
        body = [line for line in fh if not line.startswith("#")]
    reader = csv.DictReader(body)
    if reader.fieldnames != SHEET_COLUMNS:
        raise AnalyseError(f"{path.name}: the header is not {SHEET_COLUMNS}")
    out: dict[str, dict[str, Any]] = {}
    for row in reader:
        oid = row["id"]
        if oid not in pool:
            raise AnalyseError(f"{path.name}: unknown id {oid}")
        if oid in out:
            raise AnalyseError(f"{path.name}: id {oid} appears twice")
        if row["op"] != pool[oid]["op"]:
            raise AnalyseError(f"{path.name}: the op of {oid} does not match the pool")
        if row["reason"] not in REASONS:
            raise AnalyseError(f"{path.name}: {oid} has an unknown reason")
        label = row["your_label"]
        if label == "":
            raise AnalyseError(f"{path.name}: {oid} has no your_label")
        if label not in valid_labels(pool[oid]["op"], pool[oid]["n"]):
            raise AnalyseError(f"{path.name}: {oid} has a label outside the vocabulary")
        out[oid] = {"reason": row["reason"], "consensus": row["consensus"], "label": None if label == "UNCLEAR" else label}
    return out


def run_consensus(base: Path = HERE, sheet: Path = SHEET, local: Path = SC.JUDGE_LOCAL) -> dict[str, Any]:
    """Writes consensus.jsonl, consensus_summary.json and the review sheet. Refused when the ledger does not verify, when the answers files
    are not exactly those of the valid ledger lines, when a slice is not settled, or when the sheet already has a label."""
    refuse_repo_path(sheet, "the review sheet")
    pool = load_pool_rows()
    ledger = SC.read_ledger(base)
    SC.verify_ledger(ledger, local, base)
    check_complete(ledger)
    check_answer_files(ledger, base)
    if sheet.exists() and sheet_has_labels(sheet):
        raise AnalyseError(f"{sheet.name} already has labels: a sheet with labels is never overwritten")
    classes = judged_classes(pool, base)
    consensus = {}
    for oid in sorted(pool):
        status, label = consensus_of(classes[oid])
        consensus[oid] = {"id": oid, "op": pool[oid]["op"], "status": status, "label": label, "judges": classes[oid]}
    write_lines(base / "consensus.jsonl", [consensus[oid] for oid in sorted(consensus)])
    agreement = {}
    for op in ("decide_relation", "decide_group"):
        rated = [classes[oid] for oid in sorted(consensus) if consensus[oid]["op"] == op]
        agreement[op] = {"items": len(rated), "fleiss_kappa": fleiss_kappa(rated) if rated else None,
                         "statuses": dict(Counter(consensus[oid]["status"] for oid in consensus if consensus[oid]["op"] == op))}
    write_json(base / "consensus_summary.json", {"agreement": agreement, "inputs": {"ledger.jsonl": sha_file(base / "judge" / "ledger.jsonl")}})
    review, spot = expected_sheet(consensus)
    texts = load_texts()
    rows = []
    for oid in sorted(set(review) | set(spot)):
        row = consensus[oid]
        rows.append({"id": oid, "op": row["op"], "reason": review.get(oid) or spot[oid],
                     "propositions": proposition_text(pool[oid]["outcomes"], texts, row["op"]),
                     "judge1": row["judges"][0], "judge2": row["judges"][1], "judge3": row["judges"][2],
                     "consensus": row["label"] if row["label"] is not None else "review"})
    write_sheet(sheet, rows)
    return {"items": len(consensus), "statuses": dict(Counter(r["status"] for r in consensus.values())),
            "review_rows": len(review), "spot_check_rows": len(spot)}


def fleiss_kappa(ratings: list[list[str]]) -> float | None:
    """Fleiss' kappa of items each rated by the same number of raters (ratings[i] holds the raters' labels of item i, __missing__
    included as a category). None when every rating falls in one category (chance agreement is then 1)."""
    items, raters = len(ratings), len(ratings[0])
    cats = sorted({c for r in ratings for c in r})
    count = [[sum(c == k for c in r) for k in cats] for r in ratings]
    p_item = [(sum(x * x for x in row) - raters) / (raters * (raters - 1)) for row in count]
    p_bar = sum(p_item) / items
    p_exp = sum((sum(row[j] for row in count) / (items * raters)) ** 2 for j in range(len(cats)))
    return None if p_exp == 1 else (p_bar - p_exp) / (1 - p_exp)


def run_review(base: Path = HERE, review_dir: Path = REVIEW_DIR, local: Path = SC.JUDGE_LOCAL) -> dict[str, Any]:
    """Applies the review sheet(s). The first sheet must hold exactly the review items and the spot-check sample that the consensus and the
    seeded draw give, with their reasons. Changed means a spot-check item whose label differs from its consensus label (a review item
    has no consensus, so it is reviewed, not changed). The escalation sheet is written when more than MAX_CHANGED spot-check items changed,
    and the run stops until it holds exactly the remaining 2-1 items. Writes review_labels.jsonl (each row with the sha256 of its sheet)
    and final_labels.jsonl (ids and labels only). A re-run is allowed only if it reproduces the recorded labels: labels that changed after the
    review are refused."""
    refuse_repo_path(review_dir, "the review sheets")
    consensus = check_recorded_consensus(base, local)
    pool = load_pool_rows()
    first_path = review_dir / SHEET.name
    first = read_sheet(first_path, pool)
    review, spot = expected_sheet(consensus)
    expected_first = {**review, **spot}
    if set(first) != set(expected_first):
        missing, extra = sorted(set(expected_first) - set(first)), sorted(set(first) - set(expected_first))
        raise AnalyseError(f"{first_path.name} does not hold exactly the review and spot-check items: missing {missing[:5]}, "
                           f"extra {extra[:5]}")
    for oid, reason in expected_first.items():
        if first[oid]["reason"] != reason:
            raise AnalyseError(f"{first_path.name}: {oid} has reason {first[oid]['reason']}, expected {reason}")
    changed = [oid for oid in spot if first[oid]["label"] != consensus[oid]["label"]]
    sheet_of = {oid: (first_path.name, sha_file(first_path)) for oid in first}
    reviewed = dict(first)
    escalation = review_dir / ESCALATION_SHEET.name
    if len(changed) > MAX_CHANGED:
        expected = expected_escalation(consensus, spot)
        if not escalation.exists():
            texts = load_texts()
            write_sheet(escalation, [{"id": oid, "op": consensus[oid]["op"], "reason": "escalation",
                                      "propositions": proposition_text(pool[oid]["outcomes"], texts, consensus[oid]["op"]),
                                      "judge1": "", "judge2": "", "judge3": "", "consensus": consensus[oid]["label"]}
                                     for oid in sorted(expected)])
            raise AnalyseError(f"{len(changed)} spot-check items changed: the escalation sheet {escalation.name} is written; "
                               "complete it before review or selection")
        second = read_sheet(escalation, pool)
        if set(second) != expected or any(r["reason"] != "escalation" for r in second.values()):
            raise AnalyseError(f"{escalation.name} does not hold exactly the remaining 2-1 items, all with reason escalation")
        reviewed.update(second)
        sheet_of.update({oid: (escalation.name, sha_file(escalation)) for oid in second})
    elif escalation.exists():
        raise AnalyseError(f"{escalation.name} exists but is not required: remove it or check the spot-check sheet")
    final = {}
    review_rows = []
    for oid in sorted(consensus):
        row = consensus[oid]
        if oid in reviewed:
            label = reviewed[oid]["label"]
            review_rows.append({"id": oid, "op": row["op"], "reason": reviewed[oid]["reason"], "consensus": row["label"],
                                "final": label, "changed": oid in changed, "sheet": sheet_of[oid][0],
                                "sheet_sha256": sheet_of[oid][1]})
            source = "reviewed"
        elif row["status"] in STATUS_LABELLED:
            label, source = row["label"], row["status"]
        elif row["status"] == "dropped":
            label, source = None, "dropped"
        else:
            raise AnalyseError(f"{oid} needs review and has no label")
        final[oid] = {"id": oid, "op": row["op"], "final": label, "source": source}
    final_rows = [final[oid] for oid in sorted(final)]
    for path, rows in ((base / "review_labels.jsonl", review_rows), (base / "final_labels.jsonl", final_rows)):
        if path.exists() and read_lines(path) != rows:
            raise AnalyseError(f"{path.name} already records different labels: labels do not change after the review")
    write_lines(base / "review_labels.jsonl", review_rows)
    write_lines(base / "final_labels.jsonl", final_rows)
    return {"reviewed": len(review_rows), "changed_spot_check": len(changed), "dropped": sum(f["final"] is None for f in final.values()),
            "labelled": sum(f["final"] is not None for f in final.values())}


# ---- selection


def relation_cell(structural_type: str, label: str) -> str:
    if structural_type == "trivial":
        return "trivial"
    return {"EQUIV": "EQUIV", "IMPLIES": "IMPLIES|IMPLIED_BY", "IMPLIED_BY": "IMPLIES|IMPLIED_BY", "EXCLUDES": "EXCLUDES",
            "NONE": "NONE"}[label]


def group_kind(label: str) -> str:
    """A group class with k dropped: the kinds of its terms joined by +."""
    return "+".join(term.split(":")[0] for term in label.split("+"))


def group_cell(label: str, n: int) -> str:
    return f"group|{group_kind(label)}|{'2' if n == 2 else '3' if n == 3 else '4-8'}"


def allocate(sizes: dict[str, int], k: int) -> dict[str, int]:
    """The reference's rule (docs/audits/v0.2.0-reasoning-reference/build_sample.py, allocate): every non-empty cell takes
    min(its size, floor) with floor = k // (number of non-empty cells); the seats left are shared in proportion to each cell's size
    left after the floor, by largest remainder (integer arithmetic; ties to the smaller cell name). A cell smaller than its share
    takes all of it. If the cells hold fewer than k items, every item is taken."""
    cells = {c: s for c, s in sizes.items() if s > 0}
    alloc = {c: 0 for c in sizes}
    if not cells:
        return alloc
    floor = k // len(cells)
    base = {c: min(s, floor) for c, s in cells.items()}
    seats = min(k, sum(cells.values())) - sum(base.values())
    rest = {c: cells[c] - base[c] for c in cells}
    total = sum(rest.values())
    extra = {c: seats * rest[c] // total if total else 0 for c in cells}
    leftover = seats - sum(extra.values())
    order = sorted((c for c in cells if rest[c] > 0), key=lambda c: (-(seats * rest[c] % total), c))
    for c in order[:leftover]:
        extra[c] += 1
    for c in cells:
        alloc[c] = base[c] + extra[c]
    return alloc


def run_select(base: Path = HERE) -> dict[str, Any]:
    """The sample. Trivial relations take min(TRIVIAL_CAP, their number) seats. The other relations share the rest of SELECT_RELATIONS
    (120 minus the trivial seats drawn) by final label, with IMPLIES and IMPLIED_BY in one cell. Groups take SELECT_GROUPS seats by kind with
    k dropped, crossed with the set-size bucket. Each cell's draw is a simple random sample, cells in name order, items in id order."""
    pool = load_pool_rows()
    final_path = base / "final_labels.jsonl"
    final = {r["id"]: r for r in read_lines(final_path)}
    report_path = base / "selection_report.json"
    if (base / "selection.jsonl").exists() and report_path.exists() \
            and json.loads(report_path.read_text(encoding="utf-8"))["inputs"]["final_labels.jsonl"] != sha_file(final_path):
        raise AnalyseError("the selection was drawn from other labels: final_labels.jsonl changed after it (labels do not change after the review)")
    cells: dict[str, list[str]] = {}
    for oid, row in final.items():
        if row["final"] is None:
            continue
        p = pool[oid]
        cell = relation_cell(p["structural_type"], row["final"]) if p["op"] == "decide_relation" else group_cell(row["final"], p["n"])
        cells.setdefault(cell, []).append(oid)
    rel_cells = {c: sorted(v) for c, v in cells.items() if not c.startswith("group|")}
    grp_cells = {c: sorted(v) for c, v in cells.items() if c.startswith("group|")}
    trivial = len(rel_cells.get("trivial", []))
    trivial_alloc = min(TRIVIAL_CAP, trivial)
    label_alloc = allocate({c: len(v) for c, v in rel_cells.items() if c != "trivial"}, SELECT_RELATIONS - trivial_alloc)
    rel_alloc = {"trivial": trivial_alloc, **label_alloc}
    grp_alloc = allocate({c: len(v) for c, v in grp_cells.items()}, SELECT_GROUPS)
    rng = random.Random(SELECT_SEED)
    chosen: dict[str, str] = {}
    report: dict[str, Any] = {"relations": {}, "groups": {}}
    for block, pools, alloc in (("relations", rel_cells, rel_alloc), ("groups", grp_cells, grp_alloc)):
        for c in sorted(pools):
            drawn = sorted(rng.sample(pools[c], alloc[c])) if alloc.get(c, 0) else []
            for oid in drawn:
                chosen[oid] = c
            report[block][c] = {"pool": len(pools[c]), "allocated": alloc.get(c, 0), "drawn": len(drawn)}
    per_class = Counter(final[oid]["final"] for oid in chosen if pool[oid]["op"] == "decide_relation")
    per_kind = Counter(group_kind(final[oid]["final"]) for oid in chosen if pool[oid]["op"] == "decide_group")
    report["relation_seats"] = {"requested": SELECT_RELATIONS, "drawn": sum(v["drawn"] for k, v in report["relations"].items())}
    report["group_seats"] = {"requested": SELECT_GROUPS, "drawn": sum(v["drawn"] for v in report["groups"].values())}
    report["trivial_share_of_relations"] = round(
        sum(oid in chosen and pool[oid]["structural_type"] == "trivial" for oid in final) / max(1, report["relation_seats"]["drawn"]), 4)
    report["per_class_drawn"] = dict(sorted(per_class.items()))
    report["per_kind_drawn"] = dict(sorted(per_kind.items()))
    report["no_per_class_statement"] = sorted([k for k, v in per_class.items() if v < MIN_PER_CLASS]
                                              + [f"kind {k}" for k, v in per_kind.items() if v < MIN_PER_CLASS])
    report["inputs"] = {"final_labels.jsonl": sha_file(final_path)}
    write_lines(base / "selection.jsonl", [{"id": oid, "op": pool[oid]["op"], "cell": chosen[oid], "final": final[oid]["final"]}
                                          for oid in sorted(chosen)])
    write_json(base / "selection_report.json", report)
    return report


# ---- fixture


def terms_from_class(label: str) -> list[list[Any]]:
    if label == "none":
        return []
    return [[t.split(":")[0], int(t.split(":")[1]) if ":" in t else None] for t in label.split("+")]


def run_fixture(base: Path = HERE, local: Path = LOCAL / "fixture") -> dict[str, Any]:
    """The fixture of the selected items, as the fixtures/ contracts describe it. corpus.jsonl (outcome text and event titles) is written
    LOCAL and never published. candidates.jsonl, gold/suite_labels.jsonl, gold/meta.jsonl (the clusters) and fixture.json are written both
    locally (the runnable fixture) and to the published folder, which holds no corpus. fixture_manifest.json holds the sha256 of every
    published file, of the corpus, and of the selection the fixture was built from. A run folder or a corpus inside the repository is
    refused: runcell's --out and .build directories live under mb-runs/transfer-pilot as well."""
    refuse_repo_path(local, "the fixture")
    pool = load_pool_rows()
    texts = load_texts()
    selection_path = base / "selection.jsonl"
    selected = read_lines(selection_path)
    rows_by_id = {r["id"]: r for r in read_lines(SC.POOL)}
    corpus, candidates, suite, meta, seen = [], [], [], [], set()
    for sel in selected:
        oid, p = sel["id"], pool[sel["id"]]
        row = rows_by_id[oid]
        if p["op"] == "decide_relation":
            candidates.append({"a": row["a"], "b": row["b"], "op": "decide_relation", "qid": oid})
            suite.append({"depth": None, "held_out": False, "k": None, "label": sel["final"], "missing": None,
                          "op": "decide_relation", "order": None, "qid": oid,
                          "stratum": "trivial" if p["structural_type"] == "trivial" else "real"})
        else:
            candidates.append({"members": row["members"], "op": "decide_group", "qid": oid, "source": "curated"})
            suite.append({"depth": None, "held_out": False, "k": None, "label": sel["final"], "missing": None,
                          "op": "decide_group", "order": None, "qid": oid,
                          "stratum": "decoy" if p["structural_type"] == "decoy" else "real",
                          "terms": terms_from_class(sel["final"])})
        for o in p["outcomes"]:
            if o in seen:
                continue
            seen.add(o)
            t = texts[o]
            corpus.append({"id": o, "text": t["question"], "label": t["label"], "event_title": t["event_title"], "controlled": True})
            meta.append({"id": o, "league": t["cluster"], "market": t["market"], "event": t["event"]})
    fixture = {"name": "transfer_pilot", "split": "pilot", "version": "3.1", "generator": "polymarket-gamma-snapshot",
               "controlled": False, "items": len(selected), "relations": sum(c["op"] == "decide_relation" for c in candidates),
               "groups": sum(c["op"] == "decide_group" for c in candidates), "sample_seed": SELECT_SEED}
    files = {"candidates.jsonl": candidates, "gold/suite_labels.jsonl": suite, "gold/meta.jsonl": meta}
    for name, rows in files.items():
        write_lines(local / name, rows)
    write_lines(local / "corpus.jsonl", corpus)
    write_json(local / "fixture.json", fixture)
    published = base / "fixture"
    for name, rows in files.items():
        write_lines(published / name, rows)
    write_json(published / "fixture.json", fixture)
    if (published / "corpus.jsonl").exists():
        raise AnalyseError("the published fixture folder must not hold corpus.jsonl")
    manifest = {"schema": "mentat-bench-transfer-fixture/1", "corpus_sha256": sha_file(local / "corpus.jsonl"),
                "files": {name: sha_file(published / name) for name in [*files, "fixture.json"]},
                "inputs": {"selection.jsonl": sha_file(selection_path)}}
    write_json(base / "fixture_manifest.json", manifest)
    return manifest


# ---- statistics


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (ties share the mean of their ranks)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in order[i:j + 1]:
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float:
    """Spearman's rho with average ranks for ties. A vector with no variation (a constant) gives 0, for the point and for every
    draw."""
    if len(set(a)) < 2 or len(set(b)) < 2:
        return 0.0
    return float(np.corrcoef(_ranks(a), _ranks(b))[0, 1])


def kendall_tau_b(a: list[float], b: list[float]) -> float:
    """Kendall's tau-b. Pairs tied in a, in b, or in both are excluded from the concordant and discordant counts, and each tie
    adjusts the denominator. A zero denominator gives 0."""
    n = len(a)
    n0 = n * (n - 1) / 2
    conc = disc = ta = tb = 0
    for i in range(n):
        for j in range(i + 1, n):
            sa = int(a[i] > a[j]) - int(a[i] < a[j])
            sb = int(b[i] > b[j]) - int(b[i] < b[j])
            ta += sa == 0
            tb += sb == 0
            if sa and sb:
                conc += sa == sb
                disc += sa != sb
    denom = math.sqrt((n0 - ta) * (n0 - tb))
    return (conc - disc) / denom if denom else 0.0


def percentile_interval(values: np.ndarray) -> tuple[float, float]:
    lo, hi = np.percentile(values, [2.5, 97.5])
    return float(lo), float(hi)


def reading(lower: float, upper: float) -> str:
    """The pre-registered reading of the primary statistic's 95% interval."""
    if lower >= 0.6:
        return "carries over"
    if upper < 0.6:
        return "does not carry over"
    return "inconclusive"


def _mcc(items: list[dict[str, Any]], weights: list[int], preds: dict[str, str]) -> float:
    pairs: list[tuple[str, str]] = []
    for item, w in zip(items, weights):
        pairs.extend([(item["gold"], preds.get(item["id"], DS.MISSING))] * w)
    return DS.mcc(pairs)  # 0 for no pairs or a constant marginal (decision_scoring.mcc)


def model_draws(items: list[dict[str, Any]], preds: dict[str, str], W: np.ndarray, cidx: dict[str, int]) -> dict[str, Any]:
    """Each op's point MCC (every item once) and its MCC on each league draw (an item weighs its cluster's multiplicity)."""
    out = {}
    for op, name in (("decide_relation", "relation"), ("decide_group", "group")):
        sub = [it for it in items if it["op"] == op]
        point = _mcc(sub, [1] * len(sub), preds)
        draws = np.array([_mcc(sub, [int(w[cidx[it["cluster"]]]) for it in sub], preds) for w in W])
        out[name] = (point, draws)
    return out


def _block(per: dict[str, Any], head_draws: dict[str, np.ndarray], items: list[dict[str, Any]], preds: dict[str, str],
           m: str) -> dict[str, Any]:
    head = per[m]["relation"][0] + per[m]["group"][0]
    return {
        "relation_mcc": {"point": per[m]["relation"][0], "lo": percentile_interval(per[m]["relation"][1])[0],
                         "hi": percentile_interval(per[m]["relation"][1])[1]},
        "group_mcc": {"point": per[m]["group"][0], "lo": percentile_interval(per[m]["group"][1])[0],
                      "hi": percentile_interval(per[m]["group"][1])[1]},
        "headline": {"point": head / 2, "lo": percentile_interval(head_draws[m])[0], "hi": percentile_interval(head_draws[m])[1]},
        "accuracy": {op: (sum(preds.get(it["id"], DS.MISSING) == it["gold"] for it in items if it["op"] == op)
                          / max(1, sum(it["op"] == op for it in items)))
                     for op in ("decide_relation", "decide_group")},
    }


def statistics(items: list[dict[str, Any]], predictions: dict[str, dict[str, str]], v020: dict[str, dict[str, float]],
               resolved: list[dict[str, str]], B: int = SCORE_B, seed: int = SCORE_SEED,
               anchors: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """The primary statistic, the secondaries and the per-model table for one set of items. items: id, op, gold, cluster. The draws are
    league_ci.draws(B, seed, L) over the clusters of these items, the same for every model (paired). The anchors (the baselines) get the
    same draws and are reported beside the models, outside the correlation."""
    clusters = sorted({it["cluster"] for it in items})
    cidx = {c: i for i, c in enumerate(clusters)}
    W = LC.draws(B, seed, len(clusters))
    models = sorted(predictions)
    per = {m: model_draws(items, predictions[m], W, cidx) for m in models}
    head_point = {m: (per[m]["relation"][0] + per[m]["group"][0]) / 2 for m in models}
    head_draws = {m: (per[m]["relation"][1] + per[m]["group"][1]) / 2 for m in models}
    vh = [v020[m]["headline"] for m in models]
    vr = [v020[m]["relation"] for m in models]
    vg = [v020[m]["group"] for m in models]
    rho_h = np.array([spearman([head_draws[m][b] for m in models], vh) for b in range(B)])
    rho_r = np.array([spearman([per[m]["relation"][1][b] for m in models], vr) for b in range(B)])
    rho_g = np.array([spearman([per[m]["group"][1][b] for m in models], vg) for b in range(B)])
    tau = np.array([kendall_tau_b([head_draws[m][b] for m in models], vh) for b in range(B)])
    lo, hi = percentile_interval(rho_h)
    point = spearman([head_point[m] for m in models], vh)
    kept = 0
    for pair in resolved:
        other = pair["b"] if pair["higher"] == pair["a"] else pair["a"]
        kept += head_point[pair["higher"]] > head_point[other]  # a difference of exactly 0 is not kept
    anchor_blocks = {}
    for name, preds in (anchors or {}).items():
        anchored = model_draws(items, preds, W, cidx)
        anchor_blocks[name] = _block({name: anchored}, {name: (anchored["relation"][1] + anchored["group"][1]) / 2}, items, preds, name)
    return {
        "items": {"relation": sum(it["op"] == "decide_relation" for it in items), "group": sum(it["op"] == "decide_group" for it in items),
                  "clusters": len(clusters)},
        "primary": {"statistic": "spearman rho, pilot headline vs v0.2.0 headline", "point": point,
                    "lo": lo, "hi": hi, "reading": reading(lo, hi), "draws": B},
        "secondary": {
            "kendall_tau_b": {"point": kendall_tau_b([head_point[m] for m in models], vh), "lo": percentile_interval(tau)[0],
                              "hi": percentile_interval(tau)[1]},
            "rho_relation": {"point": spearman([per[m]["relation"][0] for m in models], vr),
                             "lo": percentile_interval(rho_r)[0], "hi": percentile_interval(rho_r)[1]},
            "rho_group": {"point": spearman([per[m]["group"][0] for m in models], vg),
                          "lo": percentile_interval(rho_g)[0], "hi": percentile_interval(rho_g)[1]},
            "resolved_pairs": {"pairs": len(resolved), "kept_order": kept, "share": kept / len(resolved) if resolved else None},
        },
        "models": {m: _block(per, head_draws, items, predictions[m], m) for m in models},
        "anchors": anchor_blocks,
    }


def run_score(predictions_path: Path, base: Path = HERE, B: int = SCORE_B, local: Path = SC.JUDGE_LOCAL) -> dict[str, Any]:
    """The results for the pilot. Items are the selected items with their final labels. The predictions must hold the 17 models and the two
    baselines, each with every selected item (a missing item is refused, not scored as wrong). Sensitivity: the unanimous items only,
    excluding the spot-check items the reviewer changed, and excluding trivial relations. The double-void count comes from the ledger."""
    pool = load_pool_rows()
    final = {r["id"]: r for r in read_lines(base / "final_labels.jsonl")}
    consensus = check_recorded_consensus(base, local)
    review = {r["id"]: r for r in read_lines(base / "review_labels.jsonl")}
    selection = read_lines(base / "selection.jsonl")
    if not (base / "fixture_manifest.json").exists():
        raise AnalyseError("the fixture is not built: analyse.py fixture comes before the scores")
    suite = {r["qid"]: r for r in read_lines(base / "fixture" / "gold" / "suite_labels.jsonl")}
    items = []
    for sel in selection:
        oid = sel["id"]
        if not sel["final"] == final[oid]["final"] == suite[oid]["label"]:
            raise AnalyseError(f"{oid}: the selection, final_labels.jsonl and the fixture's suite labels disagree")
        items.append({"id": oid, "op": pool[oid]["op"], "gold": final[oid]["final"], "cluster": pool[oid]["cluster"],
                      "trivial": pool[oid]["structural_type"] == "trivial",
                      "unanimous": consensus[oid]["status"] == "unanimous",
                      "changed": oid in review and review[oid]["reason"] == "spot_check" and review[oid]["final"] != review[oid]["consensus"]})
    predictions = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
    lb = json.loads(LEADERBOARD.read_text(encoding="utf-8"))
    v020 = {m["id"]: {"relation": m["metrics"]["relation"]["point"], "group": m["metrics"]["group"]["point"],
                      "headline": m["metrics"]["headline"]["point"]} for m in lb["models"]}
    if len(v020) != MODELS_REQUIRED:
        raise AnalyseError(f"the leaderboard has {len(v020)} models, not {MODELS_REQUIRED}")
    required = sorted(set(v020) | set(BASELINES))
    absent = sorted(set(required) - set(predictions))
    if absent:
        raise AnalyseError(f"the predictions lack {absent}: every one of the 17 models and the two baselines is required")
    selected_ids = {it["id"] for it in items}
    for name in required:
        missing = sorted(selected_ids - set(predictions[name]))
        if missing:
            raise AnalyseError(f"the predictions for {name} lack {len(missing)} selected items: a missing item is refused, not scored")
        for it in items:
            if predictions[name][it["id"]] not in valid_labels(it["op"], pool[it["id"]]["n"]) | {DS.MISSING}:
                raise AnalyseError(f"the prediction for {name} on {it['id']} is not a label of that item (or __missing__)")
    analysis = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    resolved = [{"a": p["a"], "b": p["b"], "higher": p["higher"]} for p in analysis["pairwise"]["headline"] if p["resolved"]]
    preds = {m: predictions[m] for m in sorted(v020)}
    anchors = {b: predictions[b] for b in BASELINES}
    main = statistics(items, preds, v020, resolved, B, anchors=anchors)
    sensitivity = {}
    for name, subset in (("unanimous_only", [it for it in items if it["unanimous"]]),
                         ("excluding_changed", [it for it in items if not it["changed"]]),
                         ("excluding_trivial", [it for it in items if not it["trivial"]])):
        block = statistics(subset, preds, v020, resolved, B)
        sensitivity[name] = {"items": block["items"], **block["primary"]}
    ledger = SC.read_ledger(base)
    slices = SC.load_slices()
    double = sorted((i, s) for i in SC.INSTANCES for s in slices if len(SC.closed_attempts(ledger, i, s)) >= 2)
    inputs = {"predictions": sha_file(Path(predictions_path)), "final_labels.jsonl": sha_file(base / "final_labels.jsonl"),
              "selection.jsonl": sha_file(base / "selection.jsonl"), "review_labels.jsonl": sha_file(base / "review_labels.jsonl"),
              "consensus.jsonl": sha_file(base / "consensus.jsonl")}
    fixture_manifest = base / "fixture_manifest.json"
    if fixture_manifest.exists():
        inputs["fixture_manifest.json"] = sha_file(fixture_manifest)
    return {"schema": "mentat-bench-transfer-results/1", "models": sorted(preds), "baselines": list(BASELINES),
            "inputs": inputs, "main": main, "sensitivity": sensitivity,
            "double_void": {"sessions": len(double), "slices": [{"inst": i, "slice": s, "items": len(slices[s]["ids"])}
                                                               for i, s in double],
                            "items_without_answer": sum(len(slices[s]["ids"]) for _, s in double)}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    for name in ("consensus", "review", "select", "fixture"):
        sub.add_parser(name)
    p = sub.add_parser("score")
    p.add_argument("predictions", type=Path)
    a = ap.parse_args(argv)
    try:
        SC.check_frozen()
        if a.cmd == "consensus":
            print(json.dumps(run_consensus(), indent=1, sort_keys=True))
        elif a.cmd == "review":
            print(json.dumps(run_review(), indent=1, sort_keys=True))
        elif a.cmd == "select":
            print(json.dumps(run_select(), indent=1, sort_keys=True))
        elif a.cmd == "fixture":
            print(json.dumps(run_fixture(), indent=1, sort_keys=True))
        elif a.cmd == "score":
            doc = run_score(a.predictions)
            write_json(HERE / "results.json", doc)
            print(json.dumps(doc["main"]["primary"], indent=1, sort_keys=True))
        else:
            ap.print_help()
            return 1
    except (AnalyseError, SC.Refused, OSError) as exc:
        print(f"analyse.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

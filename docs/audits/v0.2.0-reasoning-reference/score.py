"""Scores the reasoning reference sample: the three test-taker instances' answers and the 17 v0.2.0 models on the same 500 items,
with sample-weighted and unweighted MCC and league-bootstrap intervals. The rule is PREREG.md.

usage: python -B score.py                             score, and write results.json (models only while no instance has answers)
       python -B score.py input SLICE                 the stdin of one slice to stdout: INSTRUCTIONS.md, then the slice's item lines
       python -B score.py extract INST SLICE ATTEMPT  check one raw output (raw/inst<i>/slice<j>/attempt<k>.json, written by
                                                      run_slice.py) and record the attempt in raw/ledger.jsonl. A valid attempt
                                                      writes answers/inst<i>/slice<j>.jsonl; a void one writes nothing but the ledger.
       python -B score.py preflight RAW               check the shape and fence form of one raw output file (no ledger entry)
       python -B score.py freeze                      write frozen.json: the sha256 of the frozen files (before the commit only)

Before any CLI call, ledger read or write, extract and run check frozen.json (check_frozen): a frozen file that differs from its
recorded hash is refused.

Protocol outcomes. A schema error (the raw bytes are not UTF-8 JSON, or a field the protocol reads is missing or of the wrong
type) raises SchemaError: no ledger entry, the attempt is not used up. A void attempt is recorded in the ledger. Each void is
re-run exactly once (attempt 2); a void attempt 2 leaves the slice's items __missing__ for that instance (PREREG.md).

Inputs: sample.jsonl, key.jsonl and slices.json (build_sample.py); the v0.2.0 predictions and leaderboard (results/v0.2.0); the test
fixture's candidates and meta (through league_ci.read_fixture). Answers are normalised as in the v0.1.1 item audit: an invalid or
missing answer is __missing__, which is wrong. Read-only on the repo (no bytecode is written).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]  # the repository root
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "python"), str(ROOT / "tools" / "eval")]

from mentat_bench import decision_scoring as DS  # noqa: E402
from mentat_bench.adapters.base import group_terms, terms_class  # noqa: E402

import league_ci as LC  # noqa: E402

SCHEMA = "mentat-bench-reasoning-reference/1"
OPS = ("decide_relation", "decide_group")
RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")
KINDS = ("none", "exactly_one", "at_most_one", "at_least_k", "at_most_k", "ordered_chain")
INSTANCES = (1, 2, 3)
ATTEMPTS = (1, 2)  # a void attempt 1 is re-run exactly once as attempt 2
B, BOOT_SEED = 2000, 0  # league draws: league_ci.draws(B, BOOT_SEED, leagues), the same draws for every source
FIXTURE = ROOT / "fixtures" / "mentat_hard_test"
LEADERBOARD = ROOT / "results" / "v0.2.0" / "leaderboard.json"
FENCE = re.compile(r"^```jsonl[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
PUBLISHED_TOL = 5e-6  # the published MCCs are rounded to 6 places
FROZEN = HERE / "frozen.json"  # the sha256 of the frozen files (written by `score.py freeze`, checked before any call or write)
FROZEN_FILES = ("score.py", "run_slice.py", "INSTRUCTIONS.md", "sample.jsonl", "key.jsonl", "slices.json")


class Void(Exception):
    """A protocol failure of a raw output: the attempt is void and is recorded in the ledger (PREREG.md)."""


class SchemaError(Exception):
    """A raw output the protocol cannot read: no ledger entry, the attempt is not used up."""


class Refused(Exception):
    """An action the protocol does not allow: an overwrite, a third attempt, a changed raw file, and so on."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) else None


def _count(value: Any, n: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= n else None


def normalised(row: dict[str, Any], answer: Any) -> str:
    """The answer as a class, as the v0.1.1 item audit normalises it. A relation answer is its label (whitespace stripped, case
    kept). A group answer is read as a JSON object (a string is parsed first). Its kind is lower-cased and read only to tell
    `none` and `ordered_chain` apart; any other kind must still be one of the six. The class is then
    terms_class(group_terms(lower, upper, n, chain)), the canonical class of decision_scoring, so the bounds decide it. The chain
    is collected and not scored. An invalid or absent answer is __missing__, which counts wrong. decision_scoring writes
    'invalid' for a group whose lower bound is above its upper bound; here that case is __missing__ too."""
    if isinstance(answer, str):  # an answer written as a JSON string
        try:
            answer = json.loads(answer)
        except ValueError:
            return DS.MISSING
    if not isinstance(answer, dict):
        return DS.MISSING
    if row["op"] == "decide_relation":
        text = _text(answer.get("relation"))
        return text if text in RELATIONS else DS.MISSING
    kind = _text(answer.get("kind"))
    kind = kind.lower() if kind else None
    if kind == "none":
        return "none"
    n = row["n_members"]
    lower, upper = _count(answer.get("lower"), n), _count(answer.get("upper"), n)
    if kind not in KINDS or lower is None or upper is None:
        return DS.MISSING
    try:
        return terms_class(group_terms(lower, upper, n, kind == "ordered_chain"))
    except ValueError:  # bounds the canonical terms cannot hold (upper < 1, or lower > upper)
        return DS.MISSING


def read_answers(path: Path, ids: set[str]) -> dict[str, Any]:
    """The answer of each id in one slice file. An id outside the slice, or given twice, is a file error (ValueError)."""
    out: dict[str, Any] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            oid = row.get("id")
            if oid not in ids:
                raise ValueError(f"{path}: an answer for an id outside its slice: {oid!r}")
            if oid in out:
                raise ValueError(f"{path}: two answers for id {oid!r}")
            out[oid] = row.get("answer")
    return out


def load_key() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    key = read_jsonl(HERE / "key.jsonl")
    slices = json.loads((HERE / "slices.json").read_text(encoding="utf-8"))
    return key, slices


def league_of(key: list[dict[str, Any]]) -> tuple[list[str], np.ndarray]:
    """The test leagues (sorted) and each key row's league index, from the fixture (league_ci's grouping), checked against key."""
    fx = LC.read_fixture(FIXTURE)
    index = np.array([fx["item_league"][row["qid"]] for row in key])
    assert all(fx["leagues"][i] == row["league"] for i, row in zip(index, key)), "a key row's league differs from the fixture's"
    return fx["leagues"], index


def model_rows(path: Path) -> dict[str, dict[str, Any]]:
    """The headline predictions of one model, by qid: every relation, and the curated groups (real and decoy)."""
    rows: dict[str, dict[str, Any]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["op"] == "decide_relation" or (row["op"] == "decide_group" and row.get("stratum") in ("real", "decoy")):
                assert row["qid"] not in rows, (path, row["qid"])
                rows[row["qid"]] = row
    return rows


def model_predictions(key: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Each model's prediction on the sample's items (key order), checked against the gold of the key and against the
    published full-set MCCs (every relation and curated group, as the leaderboard scores them)."""
    lb = json.loads(LEADERBOARD.read_text(encoding="utf-8"))
    out = {}
    for model in sorted(lb["models"], key=lambda m: m["rank"]):
        rows = model_rows(ROOT / model["predictions"])
        for op, name in (("decide_relation", "relation"), ("decide_group", "group")):
            full = [(r["gold"], r["pred"]) for r in rows.values() if r["op"] == op]
            assert len(full) == (6000 if op == "decide_relation" else 1000), (model["id"], op, len(full))
            published = model["metrics"][name]["point"]
            assert abs(DS.mcc(full) - published) < PUBLISHED_TOL, (model["id"], name, DS.mcc(full), published)
        preds = []
        for row in key:
            got = rows[row["qid"]]
            assert got["op"] == row["op"] and got["gold"] == row["gold"], (model["id"], row["id"])
            preds.append(got["pred"])
        out[model["id"]] = preds
    return out


# ---- raw outputs, the protocol, and the ledger


def raw_path(base: Path, inst: int, slice_no: int, attempt: int) -> Path:
    return base / "raw" / f"inst{inst}" / f"slice{slice_no}" / f"attempt{attempt}.json"


def ledger_path(base: Path) -> Path:
    return base / "raw" / "ledger.jsonl"


def read_ledger(base: Path) -> list[dict[str, Any]]:
    path = ledger_path(base)
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def append_ledger(base: Path, entry: dict[str, Any]) -> None:
    path = ledger_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze() -> dict[str, Any]:
    """Writes frozen.json: the sha256 of each frozen file as it is now. Run only when a frozen file is final (before the commit)."""
    doc = {"schema": "mentat-bench-reasoning-frozen/1", "files": {name: file_sha(HERE / name) for name in FROZEN_FILES}}
    FROZEN.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return doc


def check_frozen() -> None:
    """Refuses (Refused) when frozen.json is missing, does not list exactly the frozen files, or lists a file whose sha256 differs.
    extract, run and run_slice.py call it before any CLI call, ledger read or write."""
    if not FROZEN.is_file():
        raise Refused("frozen.json is missing: the frozen files have no recorded hashes (score.py freeze)")
    try:
        files = json.loads(FROZEN.read_text(encoding="utf-8"))["files"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Refused(f"frozen.json is unreadable: {exc}") from exc
    if sorted(files) != sorted(FROZEN_FILES):
        raise Refused(f"frozen.json lists {sorted(files)}, not the frozen files {sorted(FROZEN_FILES)}")
    changed = [name for name in FROZEN_FILES if not (HERE / name).is_file() or file_sha(HERE / name) != files[name]]
    if changed:
        raise Refused(f"frozen files differ from frozen.json: {', '.join(changed)} (the freeze must be regenerated only before the commit)")


def verify_ledger(base: Path, ledger: list[dict[str, Any]]) -> None:
    """Every ledger entry's raw file must exist and still have the sha256 recorded when it was checked (Refused otherwise)."""
    for e in ledger:
        path = raw_path(base, e["inst"], e["slice"], e["attempt"])
        if not path.is_file():
            raise Refused(f"{path} is in the ledger but missing")
        if hashlib.sha256(path.read_bytes()).hexdigest() != e["raw_sha256"]:
            raise Refused(f"{path} changed after it was checked (its sha256 differs from the ledger)")


def check_allowed(ledger: list[dict[str, Any]], inst: int, slice_no: int, attempt: int) -> None:
    """Refuses an attempt the protocol does not allow: an attempt that already has an entry, an attempt in a slice that is
    already accepted, a second attempt unless attempt 1 is void, and any attempt after the second."""
    if attempt not in ATTEMPTS:
        raise Refused("attempt must be 1 or 2: a void attempt 1 is re-run once, and nothing is re-run after that")
    mine = [e for e in ledger if e["inst"] == inst and e["slice"] == slice_no]
    if any(e["attempt"] == attempt for e in mine):
        raise Refused(f"inst{inst} slice{slice_no} attempt {attempt} already has a ledger entry")
    if any(e["verdict"] == "valid" for e in mine):
        raise Refused(f"inst{inst} slice{slice_no} is already accepted")
    if attempt == 1 and mine:
        raise Refused(f"inst{inst} slice{slice_no} has a ledger entry that is not attempt 1")
    if attempt == 2:
        first = [e for e in mine if e["attempt"] == 1]
        if not first or first[0]["verdict"] != "void":
            raise Refused(f"inst{inst} slice{slice_no}: attempt 2 is only allowed after a void attempt 1")


def parse_raw(raw: bytes) -> dict[str, Any]:
    """The result object of one raw output, read as UTF-8 (a BOM is accepted). SchemaError when the bytes are not a JSON object
    with the fields the protocol reads: is_error (bool), subtype (str), num_turns (int), result (str); permission_denials, when
    present, must be a list."""
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SchemaError(f"the raw output is not UTF-8 JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise SchemaError("the raw output is not a JSON object")
    for key, kind in (("is_error", bool), ("subtype", str), ("num_turns", int), ("result", str)):
        if key not in doc:
            raise SchemaError(f"the result object has no {key!r} field")
        value = doc[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            noun = {bool: "a boolean", str: "a string", int: "an integer"}[kind]
            raise SchemaError(f"{key!r} is {value!r}, not {noun}")
    if "permission_denials" in doc and not isinstance(doc["permission_denials"], list):
        raise SchemaError("permission_denials is not a list")
    return doc


def answer_lines(raw: bytes, ids: list[str] | None = None) -> list[str]:
    """The answer lines of one raw output. Void when the attempt fails the protocol: an error result, num_turns not 1, a
    non-empty permission_denials, not exactly one fenced jsonl block, a line that is not an {id, answer} object, a non-string or
    repeated id, or (with ids) ids that are not exactly the slice's ids. SchemaError from parse_raw when the object cannot be read."""
    doc = parse_raw(raw)
    if doc["is_error"] or doc["subtype"] != "success":
        raise Void(f"an error result: subtype {doc['subtype']!r}, is_error {doc['is_error']!r}")
    if doc["num_turns"] != 1:
        raise Void(f"num_turns is {doc['num_turns']}, not 1")
    if doc.get("permission_denials"):
        raise Void(f"{len(doc['permission_denials'])} permission denial(s)")
    text = doc["result"].replace("\r\n", "\n")  # CRLF is the same newline; the lines are split on "\n" only (below)
    blocks = FENCE.findall(text)
    if len(blocks) != 1:
        raise Void(f"{len(blocks)} fenced jsonl blocks, not one")
    lines = [line for line in blocks[0].split("\n") if line.strip()]  # not splitlines: U+2028 inside a JSON string is not a break
    got = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise Void(f"a line is not JSON: {line[:80]!r}") from exc
        if not isinstance(row, dict) or set(row) != {"id", "answer"}:
            raise Void(f"a line is not an {{id, answer}} object: {line[:80]!r}")
        if not isinstance(row["id"], str):
            raise Void(f"an id that is not a string: {row['id']!r}")
        got.append(row["id"])
    if len(set(got)) != len(got):
        raise Void("an id is repeated")
    if ids is not None and set(got) != set(ids):
        raise Void(f"the ids are not the slice's ids: missing {sorted(set(ids) - set(got))[:5]}, "
                   f"extra {sorted(set(got) - set(ids))[:5]}")
    return lines


def slice_ids(slice_no: int) -> list[str]:
    slices = json.loads((HERE / "slices.json").read_text(encoding="utf-8"))
    return next(s["ids"] for s in slices["slices"] if s["slice"] == slice_no)


def extract(inst: int, slice_no: int, attempt: int, base: Path = HERE) -> dict[str, Any]:
    """Checks one raw output and records the attempt in the ledger. Refused: an attempt the protocol does not allow, or a ledger
    whose raw files changed. SchemaError: nothing is recorded. Returns the ledger entry."""
    check_frozen()
    ledger = read_ledger(base)
    verify_ledger(base, ledger)
    check_allowed(ledger, inst, slice_no, attempt)
    path = raw_path(base, inst, slice_no, attempt)
    try:
        raw = path.read_bytes()  # a missing or unreadable raw file is refused: no ledger entry, no attempt used
    except OSError as exc:
        raise Refused(f"there is no readable raw output at {path}: {exc}") from exc
    entry: dict[str, Any] = {"inst": inst, "slice": slice_no, "attempt": attempt,
                             "raw_sha256": hashlib.sha256(raw).hexdigest()}
    try:
        lines = answer_lines(raw, slice_ids(slice_no))
    except Void as exc:
        entry.update({"verdict": "void", "reason": str(exc)})
    else:
        out = base / "answers" / f"inst{inst}" / f"slice{slice_no}.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        entry.update({"verdict": "valid", "reason": None})
    append_ledger(base, entry)
    return entry


def preflight(raw_file: Path) -> dict[str, Any]:
    """The shape and fence form of one raw output, for the first call. Writes nothing. SchemaError when the object cannot be read;
    otherwise the protocol verdict the attempt would get (ids are not checked, since the preflight call is not a slice)."""
    raw = raw_file.read_bytes()
    doc = parse_raw(raw)
    try:
        lines = answer_lines(raw, None)
        verdict, reason = "valid", None
    except Void as exc:
        lines, verdict, reason = [], "void", str(exc)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "is_error": doc["is_error"], "subtype": doc["subtype"],
            "num_turns": doc["num_turns"], "permission_denials": len(doc.get("permission_denials", [])),
            "fenced_blocks": len(FENCE.findall(doc["result"])), "answer_lines": len(lines), "verdict": verdict, "reason": reason}


# ---- the instances


def instance_predictions(base: Path, inst: int, key: list[dict[str, Any]], slices: dict[str, Any],
                         ledger: list[dict[str, Any]]) -> tuple[str, list[str] | None, dict[str, Any]]:
    """One instance's normalised predictions in key order. Each slice is accepted (its valid attempt's answers), double void (its
    items are __missing__), or pending (a void attempt 1 with no attempt 2 yet). The instance is complete when every slice is
    accepted or double void; absent when the ledger has nothing for it; otherwise incomplete (and not scored)."""
    mine = [e for e in ledger if e["inst"] == inst]
    protocol: dict[str, Any] = {"slices": {}, "voids": []}
    if not mine:
        return "absent", None, protocol
    by_id = {row["id"]: row for row in key}
    preds: dict[str, str] = {}
    for s in slices["slices"]:
        j = s["slice"]
        entries = sorted((e for e in mine if e["slice"] == j), key=lambda e: e["attempt"])
        protocol["voids"] += [{"slice": j, "attempt": e["attempt"], "reason": e["reason"]} for e in entries if e["verdict"] == "void"]
        if any(e["verdict"] == "valid" for e in entries):
            path = base / "answers" / f"inst{inst}" / f"slice{j}.jsonl"
            answers = read_answers(path, set(s["ids"]))
            missing = [i for i in s["ids"] if i not in answers]
            if missing:
                raise ValueError(f"{path} has no answer for {len(missing)} id(s) of its slice, e.g. {missing[0]}")
            for i in s["ids"]:
                preds[i] = normalised(by_id[i], answers[i])
            protocol["slices"][str(j)] = "accepted"
        elif any(e["attempt"] == 2 for e in entries):
            for i in s["ids"]:
                preds[i] = DS.MISSING
            protocol["slices"][str(j)] = "double_void"
        else:
            protocol["slices"][str(j)] = "pending" if entries else "none"
    if any(state not in ("accepted", "double_void") for state in protocol["slices"].values()):
        return "incomplete", None, protocol
    return "complete", [preds[row["id"]] for row in key], protocol


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


def spearman(a: list[float], b: list[float]) -> float | None:
    ra, rb = _ranks(a), _ranks(b)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _round(x: float | None) -> float | None:
    return None if x is None else round(float(x), 6)


def confusion_parts(key: list[dict[str, Any]], preds: list[str], classes: dict[str, list[str]], leagues_of: np.ndarray,
                    L: int, weighted: bool) -> dict[str, np.ndarray]:
    """Per op, the (league, gold, predicted) confusion of the items: each item adds its weight, or 1 when unweighted."""
    parts = {}
    for op in OPS:
        index = {c: i for i, c in enumerate(classes[op])}
        cube = np.zeros((L, len(index), len(index)))
        for i, row in enumerate(key):
            if row["op"] == op:
                cube[leagues_of[i], index[row["gold"]], index[preds[i]]] += row["weight"] if weighted else 1.0
        parts[op] = cube
    return parts


def mcc_parts(cubes: dict[str, np.ndarray], ones: np.ndarray, W: np.ndarray) -> dict[str, tuple[float, np.ndarray]]:
    """Per op, the MCC of the whole sample (point) and of each league draw (league_ci._mcc, the decision_scoring formula)."""
    return {op: (float(LC._mcc(cube, ones)[0]), LC._mcc(cube, W)) for op, cube in cubes.items()}


def summary(parts: dict[str, tuple[float, np.ndarray]]) -> dict[str, dict[str, float | None]]:
    """Relation, group and headline (their mean), each with its point and the 95% percentile interval of the draws."""
    rel, grp = parts["decide_relation"], parts["decide_group"]
    series = {"relation": rel, "group": grp, "headline": ((rel[0] + grp[0]) / 2, (rel[1] + grp[1]) / 2)}
    out = {}
    for name, (point, draws) in series.items():
        lo, hi = LC._interval(draws)
        out[name] = {"point": _round(point), "lo": _round(lo), "hi": _round(hi)}
    return out


def by_bucket(key: list[dict[str, Any]], preds: list[str], op: str) -> dict[str, dict[str, Any]]:
    """Weighted accuracy (each item at its weight) and the item count, by depth bucket (relations) or set size (groups)."""
    acc: dict[str, list[float]] = {}
    for i, row in enumerate(key):
        if row["op"] == op:
            cell = acc.setdefault(row["bucket"], [0, 0.0, 0.0])
            cell[0] += 1
            cell[1] += row["weight"]
            cell[2] += row["weight"] * (preds[i] == row["gold"])
    return {b: {"n": int(n), "accuracy": _round(c / w)} for b, (n, w, c) in sorted(acc.items())}


def slice_rates(key: list[dict[str, Any]], preds: list[str], slices: dict[str, Any]) -> list[dict[str, Any]]:
    """Unweighted correct rates of one instance on each slice, per op and overall."""
    pos = {row["id"]: i for i, row in enumerate(key)}
    table = []
    for s in slices["slices"]:
        idx = [pos[x] for x in s["ids"]]
        entry: dict[str, Any] = {"slice": s["slice"], "items": len(idx)}
        for name, op in (("relation", "decide_relation"), ("group", "decide_group")):
            sel = [i for i in idx if key[i]["op"] == op]
            right = sum(preds[i] == key[i]["gold"] for i in sel)
            entry[name] = {"n": len(sel), "correct": right, "rate": _round(right / len(sel)) if sel else None}
        right = sum(preds[i] == key[i]["gold"] for i in idx)
        entry["all"] = {"n": len(idx), "correct": right, "rate": _round(right / len(idx))}
        table.append(entry)
    return table


def fleiss_kappa(ratings: list[list[str]]) -> float | None:
    """Fleiss' kappa of items each rated by the same number of raters (ratings[i] holds the raters' labels of item i). None when
    every rating falls in one category (chance agreement is then 1)."""
    items, raters = len(ratings), len(ratings[0])
    cats = sorted({c for r in ratings for c in r})
    count = [[sum(c == k for c in r) for k in cats] for r in ratings]
    p_item = [(sum(x * x for x in row) - raters) / (raters * (raters - 1)) for row in count]
    p_bar = sum(p_item) / items
    p_cat = [sum(row[j] for row in count) / (items * raters) for j in range(len(cats))]
    p_exp = sum(p * p for p in p_cat)
    return None if p_exp == 1 else _round((p_bar - p_exp) / (1 - p_exp))


def agreement(key: list[dict[str, Any]], per_instance: dict[str, list[str]]) -> dict[str, Any]:
    """Per op, for each pair of instances: the share of items on which they give the same normalised answer, counting two
    __missing__ answers as agreement (pairwise), and the same share over the items both answered (pairwise_both_answered, with
    its item count). Fleiss' kappa over the three instances (unweighted, __missing__ as a category)."""
    names = sorted(per_instance)
    out = {}
    for op in OPS:
        idx = [i for i, row in enumerate(key) if row["op"] == op]
        if not idx:
            continue
        pairs, both, counts = {}, {}, {}
        for n, a in enumerate(names):
            for b in names[n + 1:]:
                name = f"{a}-{b}"
                pairs[name] = _round(sum(per_instance[a][i] == per_instance[b][i] for i in idx) / len(idx))
                answered = [i for i in idx if DS.MISSING not in (per_instance[a][i], per_instance[b][i])]
                counts[name] = len(answered)
                both[name] = _round(sum(per_instance[a][i] == per_instance[b][i] for i in answered) / len(answered)) \
                    if answered else None
        kappa = fleiss_kappa([[per_instance[name][i] for name in names] for i in idx])
        out[op] = {"pairwise": pairs, "pairwise_both_answered": both, "both_answered_items": counts, "fleiss_kappa": kappa}
    return out


def run(base: Path = HERE) -> dict[str, Any]:
    """The results document: the 17 models, the instances (complete or not, with their protocol record), and the three-instance
    summary. base holds answers/ and raw/ (HERE for the repository). The frozen files and the ledger are checked first, before the
    key, the fixture, the predictions, or any raw output or answer file is read."""
    check_frozen()
    ledger = read_ledger(base)
    verify_ledger(base, ledger)
    key, slices = load_key()
    leagues, leagues_of = league_of(key)
    L = len(leagues)
    W = LC.draws(B, BOOT_SEED, L)
    ones = np.ones((1, L), dtype=np.int64)
    models = model_predictions(key)
    instances = {i: instance_predictions(base, i, key, slices, ledger) for i in INSTANCES}
    complete = {i: preds for i, (status, preds, _) in instances.items() if status == "complete"}
    sources = {**models, **{f"inst{i}": preds for i, preds in complete.items()}}
    classes = {op: sorted({p for preds in sources.values() for p, row in zip(preds, key) if row["op"] == op}
                          | {row["gold"] for row in key if row["op"] == op}) for op in OPS}

    def evaluate(preds: list[str]) -> tuple[dict[str, Any], dict[str, dict[str, tuple[float, np.ndarray]]]]:
        """One source's block, and its per-draw MCC parts (for the three-instance mean)."""
        parts = {}
        for tag, weighted in (("weighted", True), ("unweighted", False)):
            parts[tag] = mcc_parts(confusion_parts(key, preds, classes, leagues_of, L, weighted), ones, W)
        out = {tag: summary(parts[tag]) for tag in parts}
        out.update({"by_depth": by_bucket(key, preds, "decide_relation"), "by_set_size": by_bucket(key, preds, "decide_group")})
        return out, parts

    models_out = {m: evaluate(preds)[0] for m, preds in models.items()}
    inst_out: dict[str, Any] = {}
    inst_parts: dict[int, dict[str, dict[str, tuple[float, np.ndarray]]]] = {}
    for i, (status, preds, protocol) in instances.items():
        entry: dict[str, Any] = {"status": status, "protocol": protocol}
        if status == "complete":
            out, inst_parts[i] = evaluate(preds)
            entry.update(out)
            entry["slices"] = slice_rates(key, preds, slices)
        inst_out[str(i)] = entry

    three: dict[str, Any] = {"status": "complete" if len(complete) == len(INSTANCES) else "incomplete",
                             "complete_instances": sorted(complete)}
    if len(complete) == len(INSTANCES):
        for tag in ("weighted", "unweighted"):
            mean = {op: (float(np.mean([inst_parts[i][tag][op][0] for i in INSTANCES])),
                         np.mean([inst_parts[i][tag][op][1] for i in INSTANCES], axis=0)) for op in OPS}
            three[tag] = summary(mean)
        three["instance_range"] = {name: {"min": _round(min(inst_out[str(i)]["weighted"][name]["point"] for i in INSTANCES)),
                                          "max": _round(max(inst_out[str(i)]["weighted"][name]["point"] for i in INSTANCES))}
                                   for name in ("relation", "group", "headline")}
        three["agreement"] = agreement(key, {f"inst{i}": complete[i] for i in INSTANCES})

    status = "scored" if any(s != "absent" for s, _, _ in instances.values()) else "models_only"
    return {"schema": SCHEMA, "status": status,
            "design": {"items": len(key), "relations": sum(r["op"] == "decide_relation" for r in key),
                       "groups": sum(r["op"] == "decide_group" for r in key), "sample_seed": slices["sample_seed"],
                       "slices": len(slices["slices"]), "fixture": slices["fixture"]["content_hash"],
                       "weights": "cell population / cell sample size (slices.json cells); weighted = sample-weighted, "
                                  "unweighted = each item counts once"},
            "bootstrap": {"B": B, "seed": BOOT_SEED, "leagues": L, "draws": "tools/eval/league_ci.py draws",
                          "interval": "95% percentile (numpy, linear) of the draws; the same draws for every source; conditional "
                                      "on the 500 sampled items"},
            "models": models_out, "instances": inst_out, "three_instances": three}


def render(doc: dict[str, Any]) -> str:
    """The bytes of results.json for a results document."""
    return json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n"


def stdin_of(slice_no: int) -> bytes:
    """The stdin of one slice: INSTRUCTIONS.md, then the slice's item lines of sample.jsonl in slice order."""
    by_id = {row["id"]: row for row in read_jsonl(HERE / "sample.jsonl")}
    instructions = (HERE / "INSTRUCTIONS.md").read_text(encoding="utf-8").rstrip("\n")
    items = "".join(json.dumps(by_id[i], ensure_ascii=False) + "\n" for i in slice_ids(slice_no))
    return (instructions + "\n\n" + items).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("input")
    p.add_argument("slice", type=int)
    p = sub.add_parser("extract")
    for name in ("instance", "slice", "attempt"):
        p.add_argument(name, type=int)
    p = sub.add_parser("preflight")
    p.add_argument("raw", type=Path)
    sub.add_parser("freeze")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "freeze":
            doc = freeze()
            print(f"wrote {FROZEN.name} for {len(doc['files'])} files")
            return 0
        if a.cmd == "input":
            sys.stdout.buffer.write(stdin_of(a.slice))
            return 0
        if a.cmd == "extract":
            entry = extract(a.instance, a.slice, a.attempt)
            print(f"inst{a.instance} slice{a.slice} attempt {a.attempt}: {entry['verdict']}"
                  + (f" ({entry['reason']})" if entry["verdict"] == "void" else ""))
            return 0
        if a.cmd == "preflight":
            print(json.dumps(preflight(a.raw), indent=1, sort_keys=True))
            return 0
    except (SchemaError, Refused, OSError) as exc:
        print(f"score.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    doc = run()
    (HERE / "results.json").write_text(render(doc), encoding="utf-8", newline="\n")
    print(f"status {doc['status']}; {len(doc['models'])} models; instances "
          + ", ".join(f"{i}: {e['status']}" for i, e in doc["instances"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

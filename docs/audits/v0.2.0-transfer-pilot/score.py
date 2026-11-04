"""The transfer pilot's judge protocol: the stdin of each judge slice, the preflight gate, the call lines, the check of each raw output,
the ledger, the canonical answers, and the frozen-file check. Adapted from docs/audits/v0.2.0-reasoning-reference/score.py (the reference's
own files are not changed). The rule is PREREG.md; the judge brief is JUDGE.md.

usage: python -B score.py input SLICE                 the stdin of one slice to stdout: JUDGE.md, then the slice's item lines
       python -B score.py preflight                   check the latest called raw output of instance 1 and record a preflight line;
                                                      until one passes, only instance 1 is called, one slice at a time, in order
       python -B score.py extract INST SLICE ATTEMPT  close the pending call of that attempt in judge/ledger.jsonl
       python -B score.py freeze                      write frozen.json (refused once the ledger, the answers or a raw output exists)

The ledger lines. A calling line is written before a call (with the stdin's sha256); a called line is written after the raw output is
saved (with the raw output's sha256). A call with no closing line is pending. extract closes a pending call: as valid, or void with a fixed
reason code (raw_missing, raw_unrecorded, raw_mismatch, stdin_mismatch, schema_error_repeats, or a protocol code). A schema error is a
closing line of its own that does not use up the attempt; the second one is written with its void (schema_error_repeats) in one append.
A calling line younger than TIMEOUT_S + CALL_GRACE_S may be a call in flight, and extract refuses to close it. A preflight line is
informational for the gate and is never a closing line. A stop line (a reason code) stops the pilot: every call is refused until a
deviation is recorded (PREREG.md, Stop).

Two roots. The ledger and the canonical answers are published, in this folder: judge/ledger.jsonl and judge/answers/. The raw outputs and
the stdin are local, outside the repository (mb-runs/transfer-pilot/judge/raw). Nothing a judge writes is copied into the published files
except canonical answers. Every other answer is recorded as invalid, and every void or schema error has a fixed reason code.

Before any CLI call, ledger read or write, and stdin, check frozen.json (check_frozen): a frozen file, or a listed input, that differs from
its recorded hash is refused.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]  # the repository root
LOCAL = ROOT.parent / "mb-runs" / "transfer-pilot"  # outside the repository
JUDGE_LOCAL = LOCAL / "judge"
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / "python")]

from mentat_bench import decision_scoring as DS  # noqa: E402
from mentat_bench.adapters.base import group_terms, terms_class  # noqa: E402

SCHEMA = "mentat-bench-transfer-judge/1"
POOL = HERE / "pool.jsonl"
SLICES = HERE / "slices.json"
RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE", "UNCLEAR")
KINDS = ("none", "exactly_one", "at_most_one", "at_least_k", "at_most_k", "ordered_chain")
INSTANCES = (1, 2, 3)
ATTEMPTS = (1, 2)  # a void attempt 1 is re-run exactly once as attempt 2
MAX_SCHEMA_ERRORS = 2  # per attempt; the second closes the attempt as void (schema_error_repeats)
TIMEOUT_S = 1800  # a call that does not return in this time stays pending, and extract closes it
FENCE = re.compile(r"^```jsonl[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
FROZEN = HERE / "frozen.json"
FROZEN_FILES = ("JUDGE.md", "PREREG.md", "pool.jsonl", "pool_summary.json", "slices.json", "snapshot_manifest.json", "fetch.py",
                "rehydrate.py", "build_pool.py", "run_slice.py", "score.py", "analyse.py")
MODELS = ("clef-flash_9b", "clef_27b-q8_0", "d1-omni_600m-q8_0", "d1_3b-q8_0", "d3_27b-q8_0", "d3_2b-q8_0", "d3_4b-q8_0",
          "d3_9b-q8_0", "kev_0.8b-q8_0", "kev_4b-q8_0", "kev_9b-bf16", "laya_latest", "nimble-v3_9b-q8_0", "nimble_9b-bf16",
          "pplx-decider_27b", "tev1_0.8b-q8_0", "tev1_4b-q8_0")  # the 17 v0.2.0 models (leaderboard.json ids)
# The repository files the pilot's rules, runs and analysis read. Their hashes are recorded in frozen.json under "inputs". The baselines have
# no cell of their own: lexical and heuristic are recorded in leaderboard.json and baselines.json.
FROZEN_INPUTS = ("python/mentat_bench/decision_scoring.py", "tools/eval/league_ci.py", "python/mentat_bench/adapters/base.py",
                 "python/mentat_bench/children/systemone_child.py", "results/v0.2.0/leaderboard.json", "results/v0.2.0/analysis.json",
                 "results/v0.2.0/baselines.json", "tools/reproduce.py", "profiles/mentat-hard-v1.json",
                 "python/mentat_bench/children/ollama_models.json",
                 *[f"results/v0.2.0/cells/{m}.json" for m in MODELS])
CLOSING = ("valid", "void", "schema_error")
PREFLIGHT_INSTANCE = 1  # until a preflight passes, only this instance is called, one slice at a time in slice order
CALL_GRACE_S = 120  # a calling line younger than TIMEOUT_S + CALL_GRACE_S may be a call in flight: extract refuses it


class Void(Exception):
    """A protocol failure of a raw output: the attempt is void and is recorded in the ledger (PREREG.md). The message is a fixed
    reason code with positions or counts, never text from the judge."""


class SchemaError(Exception):
    """A raw output the protocol cannot read: a schema error. It is recorded without using up the attempt. The message is a code."""


class Refused(Exception):
    """An action the protocol does not allow: an overwrite, a third attempt, a changed file, a call before the preflight gate, and so on."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pool() -> dict[str, dict[str, Any]]:
    """Each pool item's op and member count, by id (the published pool.jsonl)."""
    out = {}
    for row in read_jsonl(POOL):
        out[row["id"]] = {"op": row["op"], "n": None if row["op"] == "decide_relation" else len(row["members"])}
    return out


def load_slices() -> dict[int, dict[str, Any]]:
    """Each slice's ids and the sha256 of its judge item file (the published slices.json)."""
    doc = json.loads(SLICES.read_text(encoding="utf-8"))
    return {s["slice"]: s for s in doc["slices"]}


def slice_ids(slice_no: int) -> list[str]:
    if slice_no not in load_slices():
        raise Refused(f"slice {slice_no} is not in slices.json")
    return load_slices()[slice_no]["ids"]


# ---- answers: the canonical form


def _count(value: Any, n: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= n else None


def canonical_answer(op: str, n: int | None, answer: Any) -> dict[str, Any]:
    """The answer as recorded: a relation {"relation": label} with a label from RELATIONS; a group {"kind", "lower", "upper", "chain"}
    with a kind from KINDS, integer bounds in 0..n, and a chain of distinct integer positions 1..n (for ordered_chain; [] for the other
    kinds); a group {"kind": "UNCLEAR"} when the judge says so. Anything else is {"invalid": true}: the judge's text is not kept."""
    invalid = {"invalid": True}
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except ValueError:
            return invalid
    if not isinstance(answer, dict):
        return invalid
    if op == "decide_relation":
        return {"relation": answer["relation"]} if answer.get("relation") in RELATIONS else invalid
    kind = answer.get("kind")
    if isinstance(kind, str) and kind.strip().upper() == "UNCLEAR":
        return {"kind": "UNCLEAR"}
    kind = kind.strip().lower() if isinstance(kind, str) else None
    if kind not in KINDS:
        return invalid
    lower, upper = _count(answer.get("lower"), n), _count(answer.get("upper"), n)
    if lower is None or upper is None:
        return invalid
    chain: list[int] = []
    if kind == "ordered_chain":
        raw = answer.get("chain")
        if not isinstance(raw, list) or len(set(map(str, raw))) != len(raw) \
                or not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= n for x in raw):
            return invalid
        chain = list(raw)
    return {"kind": kind, "lower": lower, "upper": upper, "chain": chain}


def normalised(op: str, n: int | None, canonical: dict[str, Any]) -> str:
    """The class of a canonical answer: a relation label (EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, NONE or UNCLEAR); a group UNCLEAR, or
    "none" for kind none, or terms_class(group_terms(lower, upper, n, chain)) (the canonical class of decision_scoring). An invalid answer
    is __missing__."""
    if "invalid" in canonical:
        return DS.MISSING
    if op == "decide_relation":
        return canonical["relation"]
    if canonical["kind"] == "UNCLEAR":
        return "UNCLEAR"
    if canonical["kind"] == "none":
        return "none"
    try:
        return terms_class(group_terms(canonical["lower"], canonical["upper"], n, canonical["kind"] == "ordered_chain"))
    except ValueError:  # bounds the canonical terms cannot hold (upper < 1, or lower > upper)
        return DS.MISSING


# ---- raw outputs, the ledger


def raw_path(inst: int, slice_no: int, attempt: int, local: Path = JUDGE_LOCAL, name: str | None = None) -> Path:
    return local / "raw" / f"inst{inst}" / f"slice{slice_no}" / (name or f"attempt{attempt}.json")


def answers_path(inst: int, slice_no: int, base: Path = HERE) -> Path:
    return base / "judge" / "answers" / f"inst{inst}" / f"slice{slice_no}.jsonl"


def ledger_path(base: Path = HERE) -> Path:
    return base / "judge" / "ledger.jsonl"


def read_ledger(base: Path = HERE) -> list[dict[str, Any]]:
    """The ledger's lines. A line that is not JSON stops the run and names its line number: only a torn last line may be removed by the
    operator, and the removal is recorded in deviations.md (PREREG.md, Repairs)."""
    path = ledger_path(base)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise Refused(f"{path} line {number} is not JSON (only a torn last line may be removed; PREREG.md, Repairs): {exc}") from exc
    return rows


def append_ledger(entry: dict[str, Any], base: Path = HERE) -> None:
    append_ledger_many([entry], base)


def append_ledger_many(entries: list[dict[str, Any]], base: Path = HERE) -> None:
    """The lines in one write, synced to disk: a schema error and the void that closes its attempt are written together."""
    path = ledger_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(json.dumps(e, sort_keys=True) + "\n" for e in entries))
        fh.flush()
        os.fsync(fh.fileno())


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def inputs_hashes() -> dict[str, str]:
    return {name: file_sha(ROOT / name) for name in FROZEN_INPUTS}


def freeze() -> dict[str, Any]:
    """Writes frozen.json: the sha256 of each frozen file and each recorded input, as they are now. Refused once the ledger, the answers or a
    raw output exists: the frozen files are fixed after the first judge call (the local judge items are an input written before)."""
    if (HERE / "judge").exists() or (JUDGE_LOCAL / "raw").exists():
        raise Refused("the ledger, the answers or a raw output exists: the frozen files are fixed after the first judge call")
    doc = {"schema": "mentat-bench-transfer-frozen/1", "files": {name: file_sha(HERE / name) for name in FROZEN_FILES},
           "inputs": inputs_hashes()}
    FROZEN.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return doc


def check_frozen() -> None:
    """Refuses when frozen.json is missing, does not list exactly the frozen files and inputs, or lists a file or input whose sha256 differs."""
    if not FROZEN.is_file():
        raise Refused("frozen.json is missing: the frozen files have no recorded hashes (score.py freeze)")
    try:
        doc = json.loads(FROZEN.read_text(encoding="utf-8"))
        files, inputs = doc["files"], doc["inputs"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Refused(f"frozen.json is unreadable: {exc}") from exc
    if sorted(files) != sorted(FROZEN_FILES) or sorted(inputs) != sorted(FROZEN_INPUTS):
        raise Refused("frozen.json does not list exactly the frozen files and inputs")
    changed = [name for name in FROZEN_FILES if not (HERE / name).is_file() or file_sha(HERE / name) != files[name]]
    changed += [name for name in FROZEN_INPUTS if not (ROOT / name).is_file() or file_sha(ROOT / name) != inputs[name]]
    if changed:
        raise Refused(f"frozen files or inputs differ from frozen.json: {', '.join(changed)}")


def judge_entries(ledger: list[dict[str, Any]], inst: int, slice_no: int) -> list[dict[str, Any]]:
    return [e for e in ledger if e.get("inst") == inst and e.get("slice") == slice_no]


def pending_call(ledger: list[dict[str, Any]], inst: int, slice_no: int) -> dict[str, Any] | None:
    """The calling or called line of this slice with no closing line after it, or None."""
    open_call = None
    for e in judge_entries(ledger, inst, slice_no):
        if e["verdict"] in ("calling", "called"):
            open_call = e
        elif e["verdict"] in CLOSING:
            open_call = None
    return open_call


def preflight_passed(ledger: list[dict[str, Any]]) -> bool:
    return any(e["verdict"] == "preflight" and e["result"] == "pass" for e in ledger)


def closed_attempts(ledger: list[dict[str, Any]], inst: int, slice_no: int) -> set[int]:
    """The attempts closed as void: each with a void line, or with two schema-error lines (a torn append that lost the void line)."""
    mine = judge_entries(ledger, inst, slice_no)
    closed = {e["attempt"] for e in mine if e["verdict"] == "void"}
    for attempt in {e["attempt"] for e in mine if e["verdict"] == "schema_error"}:
        if sum(e["verdict"] == "schema_error" and e["attempt"] == attempt for e in mine) >= MAX_SCHEMA_ERRORS:
            closed.add(attempt)
    return closed


def settled(ledger: list[dict[str, Any]], inst: int, slice_no: int) -> bool:
    """A slice is settled when it is accepted or double void, by the normal rules, whether or not the gate was closed."""
    return any(e["verdict"] == "valid" for e in judge_entries(ledger, inst, slice_no)) or len(closed_attempts(ledger, inst, slice_no)) >= 2


def late_call(ledger: list[dict[str, Any]]) -> bool:
    """A called line after its attempt was closed: the subprocess returned after extract had closed the call (as raw_missing, say)."""
    return any(e["attempt"] in closed_attempts(ledger[:i], e["inst"], e["slice"]) for i, e in enumerate(ledger) if e["verdict"] == "called")


def stop_reason(ledger: list[dict[str, Any]]) -> str | None:
    """The reason code the pilot is stopped for, or None. Stopped by a stop line on the record, by a late call, or by an instance-1 slice
    that ends double void while the preflight gate is closed. Every call is refused while stopped (PREREG.md, Stop)."""
    stops = [e["reason"] for e in ledger if e["verdict"] == "stop"]
    if stops:
        return stops[0]
    if late_call(ledger):
        return "late_call"
    if not preflight_passed(ledger) and any(len(closed_attempts(ledger, PREFLIGHT_INSTANCE, s)) >= 2
                                            for s in {e["slice"] for e in ledger if "slice" in e}):
        return "double_void_gated"
    return None


def record_stop(base: Path = HERE) -> None:
    """Writes a stop line when the ledger is stopped and has none yet. The stop itself is the same whether or not a line is written."""
    ledger = read_ledger(base)
    reason = stop_reason(ledger)
    if reason and not any(e["verdict"] == "stop" for e in ledger):
        append_ledger({"verdict": "stop", "reason": reason, "timestamp": now()}, base)


def check_allowed(ledger: list[dict[str, Any]], inst: int, slice_no: int, attempt: int) -> None:
    """Refuses a call the protocol does not allow: a stopped pilot (stop_reason); an unknown instance or slice; before a preflight passes,
    any call but the next instance-1 slice in slice order (a slice starts only after the previous one is settled); a pending call; an
    accepted slice; a double void; an attempt that is not the next one (the lowest attempt not closed)."""
    if inst not in INSTANCES:
        raise Refused(f"instance must be one of {INSTANCES}")
    slice_ids(slice_no)  # Refused when the slice is not in slices.json
    reason = stop_reason(ledger)
    if reason:
        raise Refused(f"stopped: {reason} (PREREG.md, Stop): no call is made until a deviation is recorded")
    if not preflight_passed(ledger):
        if inst != PREFLIGHT_INSTANCE:
            raise Refused(f"the preflight gate: until a preflight passes, only instance {PREFLIGHT_INSTANCE} may be called")
        unsettled = next((s for s in sorted(load_slices()) if not settled(ledger, PREFLIGHT_INSTANCE, s)), None)
        if slice_no != unsettled:
            raise Refused(f"the preflight gate: until a preflight passes, instance {PREFLIGHT_INSTANCE} is called one slice at a time, "
                          f"in order: the next slice is {unsettled}")
    if pending_call(ledger, inst, slice_no):
        raise Refused(f"inst{inst} slice{slice_no} has a call with no closing line: extract it before calling again")
    if any(e["verdict"] == "valid" for e in judge_entries(ledger, inst, slice_no)):
        raise Refused(f"inst{inst} slice{slice_no} is already accepted")
    closed = closed_attempts(ledger, inst, slice_no)
    if len(closed) >= 2:
        raise Refused(f"inst{inst} slice{slice_no} is double void")
    nxt = min(a for a in ATTEMPTS if a not in closed)  # the lowest attempt not closed
    if attempt != nxt:
        raise Refused(f"inst{inst} slice{slice_no}: the next attempt is {nxt}, not {attempt}")


def record_calling(inst: int, slice_no: int, attempt: int, stdin: bytes, local: Path = JUDGE_LOCAL, base: Path = HERE) -> dict[str, Any]:
    """Before a call: the stdin is saved beside the raw output's name, and a calling line records its sha256. The call is pending until a
    called or a closing line follows."""
    path = raw_path(inst, slice_no, attempt, local)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".stdin.txt").write_bytes(stdin)
    entry = {"inst": inst, "slice": slice_no, "attempt": attempt, "raw": path.name, "stdin_sha256": sha_bytes(stdin), "timestamp": now(),
             "verdict": "calling"}
    append_ledger(entry, base)
    return entry


def record_called(inst: int, slice_no: int, attempt: int, raw: bytes, local: Path = JUDGE_LOCAL, base: Path = HERE) -> dict[str, Any]:
    """After a call returns: the raw output is saved, and then the called line records its sha256. If the save fails the call stays
    pending with no raw output, and extract closes it as raw_missing."""
    path = raw_path(inst, slice_no, attempt, local)
    path.write_bytes(raw)
    entry = {"inst": inst, "slice": slice_no, "attempt": attempt, "raw": path.name, "raw_sha256": sha_bytes(raw), "timestamp": now(),
             "verdict": "called"}
    append_ledger(entry, base)
    return entry


def block_rows(raw: bytes) -> dict[str, Any]:
    """The rows of the one fenced block of one raw output, by id (in line order). Void, with a fixed reason code, when the attempt fails the
    protocol before the ids are compared. An error result (is_error, or a subtype other than success) is a void even without a result field."""
    doc = parse_raw(raw)
    if doc["is_error"] or doc.get("subtype") != "success":
        raise Void("error_result")
    if doc["num_turns"] != 1:
        raise Void(f"num_turns:{doc['num_turns']}")
    if doc.get("permission_denials"):
        raise Void(f"permission_denials:{len(doc['permission_denials'])}")
    text = doc["result"].replace("\r\n", "\n")  # CRLF is the same newline; the lines are split on "\n" only (below)
    blocks = FENCE.findall(text)
    if len(blocks) != 1:
        raise Void(f"fenced_blocks:{len(blocks)}")
    lines = [line for line in blocks[0].split("\n") if line.strip()]  # not splitlines: U+2028 inside a JSON string is not a break
    got: dict[str, Any] = {}
    for position, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise Void(f"line_not_json:{position}") from exc
        if not isinstance(row, dict) or set(row) != {"id", "answer"}:
            raise Void(f"line_not_object:{position}")
        if not isinstance(row["id"], str):
            raise Void(f"id_not_string:{position}")
        if row["id"] in got:
            raise Void("id_repeated")
        got[row["id"]] = row["answer"]
    return got


def parse_raw(raw: bytes) -> dict[str, Any]:
    """The result object of one raw output, read as UTF-8 (a BOM is accepted). The error flag is read first: an object with is_error true is
    returned as it is, whatever else it holds, for block_rows to void as error_result. A subtype other than success is returned the same
    way. Otherwise the object must have the fields the protocol reads. SchemaError (a code) when it cannot be read."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SchemaError("not_utf8") from exc
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise SchemaError("not_json") from exc
    if not isinstance(doc, dict):
        raise SchemaError("not_object")
    if "is_error" not in doc:
        raise SchemaError("missing:is_error")
    if not isinstance(doc["is_error"], bool):
        raise SchemaError("type:is_error")
    if doc["is_error"]:
        return doc
    if "subtype" not in doc:
        raise SchemaError("missing:subtype")
    if not isinstance(doc["subtype"], str):
        raise SchemaError("type:subtype")
    if doc["subtype"] != "success":
        return doc
    for key, kind in (("num_turns", int), ("result", str)):
        if key not in doc:
            raise SchemaError(f"missing:{key}")
        if not isinstance(doc[key], kind) or (kind is int and isinstance(doc[key], bool)):
            raise SchemaError(f"type:{key}")
    if "permission_denials" in doc and not isinstance(doc["permission_denials"], list):
        raise SchemaError("type:permission_denials")
    return doc


def canonical_lines(raw: bytes, ids: list[str]) -> list[str]:
    """The canonical answer lines of one raw output, in the slice's id order. Void (a fixed reason code) when the attempt fails the
    protocol. SchemaError from parse_raw when the object cannot be read."""
    got = block_rows(raw)
    if set(got) != set(ids):
        raise Void(f"ids_mismatch:missing={len(set(ids) - set(got))},extra={len(set(got) - set(ids))}")
    pool = load_pool()
    return [json.dumps({"id": oid, "answer": canonical_answer(pool[oid]["op"], pool[oid]["n"], got[oid])}, sort_keys=True)
            for oid in ids]


def stdin_of(slice_no: int, local: Path = JUDGE_LOCAL) -> bytes:
    """The stdin of one slice: JUDGE.md, a blank line, then the slice's item lines (local judge/slices). Refused for an unknown slice, when
    the item file does not have the sha256 published in slices.json, or when it does not hold the slice's ids in order."""
    slice_ids(slice_no)
    instructions = (HERE / "JUDGE.md").read_text(encoding="utf-8").rstrip("\n")
    items = (local / "slices" / f"slice{slice_no}.jsonl").read_bytes()
    slice_entry = load_slices()[slice_no]
    if sha_bytes(items) != slice_entry["sha256"]:
        raise Refused(f"the item file of slice {slice_no} differs from the sha256 published in slices.json")
    ids = [json.loads(line)["id"] for line in items.decode("utf-8").split("\n") if line]
    if ids != slice_entry["ids"]:
        raise Refused(f"the item file of slice {slice_no} does not hold the slice's ids in order")
    return (instructions + "\n\n").encode("utf-8") + items


def verify_ledger(ledger: list[dict[str, Any]], local: Path = JUDGE_LOCAL, base: Path = HERE) -> None:
    """Every closing line's raw output (under its name, or the schema-error name it was moved to) has the sha256 the line records, except a
    raw_missing void, and its saved stdin has the recorded sha256. A valid line's canonical answers are re-derived from the raw output and
    compared with the answers file. Calling, called and preflight lines are checked when their call is closed (extract), not here."""
    for e in ledger:
        if e["verdict"] in ("calling", "called", "preflight", "stop"):
            continue
        if e["verdict"] not in CLOSING:
            raise Refused(f"the ledger has an unknown verdict: {e['verdict']}")
        if e.get("reason") == "raw_missing":
            continue
        path = raw_path(e["inst"], e["slice"], e["attempt"], local, e["raw"])
        if not path.is_file():
            raise Refused(f"{path} is in the ledger but missing")
        if sha_bytes(path.read_bytes()) != e["raw_sha256"]:
            raise Refused(f"{path} changed after it was checked (its sha256 differs from the ledger)")
        stdin_file = raw_path(e["inst"], e["slice"], e["attempt"], local).with_suffix(".stdin.txt")
        if stdin_file.is_file() and sha_bytes(stdin_file.read_bytes()) != e["stdin_sha256"]:
            raise Refused(f"{stdin_file} changed after the call (its sha256 differs from the ledger)")
        if e["verdict"] == "valid":
            answers = answers_path(e["inst"], e["slice"], base)
            if not answers.is_file():
                raise Refused(f"{answers} is in the ledger but missing")
            expected = ("\n".join(canonical_lines(path.read_bytes(), slice_ids(e["slice"]))) + "\n").encode("utf-8")
            if answers.read_bytes() != expected or sha_bytes(expected) != e["answers_sha256"]:
                raise Refused(f"{answers} does not match the answers re-derived from its raw output")


def _close(entry: dict[str, Any], verdict: str, reason: str | None, base: Path) -> dict[str, Any]:
    entry.update({"verdict": verdict, "reason": reason})
    append_ledger(entry, base)
    record_stop(base)
    return entry


def extract(inst: int, slice_no: int, attempt: int, base: Path = HERE, local: Path = JUDGE_LOCAL) -> dict[str, Any]:
    """Closes the pending call of one attempt. Every mismatch closes it as a void with a fixed code: a call with no saved raw output
    (raw_missing, or raw_unrecorded when the file exists but no called line followed), a raw output that is not the one recorded
    (raw_mismatch), a stdin that is not the one sent (stdin_mismatch), and an attempt whose second schema error is in (schema_error_repeats).
    A schema error that is not the second is a closing line of its own and does not use up the attempt; the second and its void are written
    in one append. A calling line younger than TIMEOUT_S + CALL_GRACE_S is refused: it may be a call still in flight. Refused, too, when
    there is no pending call for the attempt, or a closed line no longer verifies."""
    check_frozen()
    ledger = read_ledger(base)
    verify_ledger(ledger, local, base)
    reason = stop_reason(ledger)
    if reason:
        record_stop(base)
        raise Refused(f"stopped: {reason} (PREREG.md, Stop): no extract is made until a deviation is recorded")
    call = pending_call(ledger, inst, slice_no)
    if call is None or call["attempt"] != attempt:
        raise Refused(f"inst{inst} slice{slice_no} attempt {attempt} has no pending call (run_slice.py first)")
    path = raw_path(inst, slice_no, attempt, local)
    sent = path.with_suffix(".stdin.txt")
    # the stdin's sha256 is on the calling line of this attempt (the called line records the raw output only)
    stdin_sha = [e for e in judge_entries(ledger, inst, slice_no) if e["verdict"] == "calling" and e["attempt"] == attempt][-1]["stdin_sha256"]
    entry: dict[str, Any] = {"inst": inst, "slice": slice_no, "attempt": attempt, "raw": path.name,
                             "raw_sha256": call.get("raw_sha256"), "stdin_sha256": stdin_sha}
    if call["verdict"] == "calling":  # the call never recorded its raw output
        started = datetime.datetime.fromisoformat(call["timestamp"])
        if datetime.datetime.now(datetime.timezone.utc) - started < datetime.timedelta(seconds=TIMEOUT_S + CALL_GRACE_S):
            raise Refused(f"inst{inst} slice{slice_no} attempt {attempt}: the call may be in flight (started {call['timestamp']}); "
                          f"extract it {TIMEOUT_S + CALL_GRACE_S} seconds after it started")
        if path.is_file():
            entry["raw_sha256"] = file_sha(path)
            return _close(entry, "void", "raw_unrecorded", base)
        return _close(entry, "void", "raw_missing", base)
    repeats = sum(e["verdict"] == "schema_error" and e["attempt"] == attempt for e in judge_entries(ledger, inst, slice_no)) + 1
    aside = path.with_name(f"attempt{attempt}.schema-error-{repeats}.json")
    if not path.is_file():
        # a schema error moved the output aside and stopped before its line was written: that close is finished below
        if not (aside.is_file() and sha_bytes(aside.read_bytes()) == call["raw_sha256"]):
            return _close(entry, "void", "raw_missing", base)
        path = aside
    raw = path.read_bytes()
    if sha_bytes(raw) != call["raw_sha256"]:
        entry["raw_sha256"] = sha_bytes(raw)  # the closing line records what is on disk, so the ledger still verifies
        return _close(entry, "void", "raw_mismatch", base)
    try:
        now_stdin = stdin_of(slice_no, local)
    except Refused:
        now_stdin = None
    if not sent.is_file() or sha_bytes(sent.read_bytes()) != stdin_sha or sent.read_bytes() != now_stdin:
        entry["stdin_sha256"] = sha_bytes(sent.read_bytes()) if sent.is_file() else None
        return _close(entry, "void", "stdin_mismatch", base)
    try:
        lines = canonical_lines(raw, slice_ids(slice_no))
    except SchemaError as exc:
        if path != aside:
            path.rename(aside)
        se = {**entry, "raw": aside.name, "repeat": repeats, "verdict": "schema_error", "reason": str(exc)}
        if repeats >= MAX_SCHEMA_ERRORS:  # the budget is spent: the second schema error and its void are one append
            void = {**entry, "raw": aside.name, "verdict": "void", "reason": "schema_error_repeats"}
            append_ledger_many([se, void], base)
            record_stop(base)
            return void
        append_ledger(se, base)
        return se
    except Void as exc:
        return _close(entry, "void", str(exc), base)
    text = "\n".join(lines) + "\n"
    out = answers_path(inst, slice_no, base)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return _close({**entry, "answers_sha256": sha_bytes(text.encode("utf-8"))}, "valid", None, base)


def preflight(base: Path = HERE, local: Path = JUDGE_LOCAL) -> dict[str, Any]:
    """The preflight gate: the raw output of the latest called line of instance 1 (any slice) is checked for its error flags, its shape, its
    single fenced block and its ids, and a preflight line records the result. A failed check can be repeated after the next call; once a
    check passes, nothing more is recorded and the gate is open. Refused when there is no call of instance 1 on the record, when the raw
    output is missing, or when it differs from the one its called line recorded."""
    check_frozen()
    ledger = read_ledger(base)
    if preflight_passed(ledger):
        raise Refused("the preflight already passed")
    called = [e for e in ledger if e["verdict"] == "called" and e["inst"] == PREFLIGHT_INSTANCE]
    if not called:
        raise Refused(f"no call of instance {PREFLIGHT_INSTANCE} is on the record: call it first")
    last = called[-1]
    inst, slice_no = PREFLIGHT_INSTANCE, last["slice"]
    # a schema error on this call moved its raw output aside: the check reads it under the name its schema_error line records
    moved = [e["raw"] for e in ledger[max(i for i, e in enumerate(ledger) if e is last):] if e["verdict"] == "schema_error"]
    path = raw_path(inst, slice_no, last["attempt"], local, moved[-1] if moved else last["raw"])
    if not path.is_file():
        raise Refused(f"{path} is missing: the raw output cannot be checked")
    raw = path.read_bytes()
    if sha_bytes(raw) != last["raw_sha256"]:
        raise Refused(f"{path} differs from the raw output its called line recorded")
    try:
        canonical_lines(raw, slice_ids(slice_no))
        result, reason = "pass", None
    except (SchemaError, Void) as exc:
        result, reason = "fail", str(exc)
    entry = {"inst": inst, "slice": slice_no, "attempt": last["attempt"], "verdict": "preflight", "result": result, "reason": reason,
             "raw_sha256": sha_bytes(raw), "timestamp": now()}
    append_ledger(entry, base)
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("input")
    p.add_argument("slice", type=int)
    p = sub.add_parser("extract")
    for name in ("instance", "slice", "attempt"):
        p.add_argument(name, type=int)
    sub.add_parser("preflight")
    sub.add_parser("freeze")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "freeze":
            doc = freeze()
            print(f"wrote {FROZEN.name} for {len(doc['files'])} files and {len(doc['inputs'])} inputs")
            return 0
        if a.cmd == "input":
            check_frozen()
            sys.stdout.buffer.write(stdin_of(a.slice))
            return 0
        if a.cmd == "extract":
            entry = extract(a.instance, a.slice, a.attempt)
            print(f"inst{a.instance} slice{a.slice} attempt {a.attempt}: {entry['verdict']} ({entry['reason']})")
            return 0 if entry["verdict"] in ("valid", "void") else 1
        if a.cmd == "preflight":
            entry = preflight()
            print(f"preflight: {entry['result']} ({entry['reason']})")
            return 0 if entry["result"] == "pass" else 1
        ap.print_help()
        return 1
    except (SchemaError, Void, Refused, OSError) as exc:
        print(f"score.py: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

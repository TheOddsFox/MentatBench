"""Wire-format helpers for docs/ir.md. JSON shapes only; no logical inference lives here."""

from __future__ import annotations

import json
import os
import re
import warnings
from pathlib import Path
from typing import Any, Iterable, Iterator

TRUE = {"op": "true"}
FALSE = {"op": "false"}
_OUTCOME_ID = re.compile(r"[^:]+:[^:]+:[0-9]+")
# Provenance REF shapes the harness writes (also used by the native fixtures): (pattern, kind, field).
_PROVENANCE_SHAPES = (
    (re.compile(r"src:([^:]+):question"), "source", "question"),
    (re.compile(r"src:([^:]+):outcome_label:[0-9]+"), "source", "outcome_label"),
    (re.compile(r"src:([^:]+):event_title"), "source", "event_title"),
    (re.compile(r"src:event:([^:]+):event_title"), "source", "event_title"),
    (re.compile(r"ax:([^:]+)"), "axiom", "rule"),
)


def var(outcome_id: str) -> dict[str, Any]:
    return {"op": "var", "id": outcome_id}


def neg(expr: dict[str, Any]) -> dict[str, Any]:
    return {"op": "not", "a": expr}


def conj(*args: dict[str, Any]) -> dict[str, Any]:
    return {"op": "and", "args": list(args)}


def disj(*args: dict[str, Any]) -> dict[str, Any]:
    return {"op": "or", "args": list(args)}


def validate_outcome_id(outcome_id: Any) -> str:
    """Outcome IDs are `venue:market_id:outcome_index`; anything else is rejected."""
    if not isinstance(outcome_id, str) or not _OUTCOME_ID.fullmatch(outcome_id):
        raise ValueError(f"malformed outcome id {outcome_id!r}; expected venue:market_id:index")
    return outcome_id


def parse_outcome_id(outcome_id: str) -> tuple[str, str, int]:
    venue, market_id, index = outcome_id.split(":")
    return venue, market_id, int(index)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, obj: Any, indent: int | None = 2) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(dumps(row) + "\n")


def read_jsonl(path: Path) -> Iterator[Any]:
    """Strict reader (workloads, verdicts, submissions): a malformed line raises."""
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _walk(expr: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every node of an Expr, iteratively (alias refs are not followed)."""
    stack = [expr]
    while stack:
        node = stack.pop()
        yield node
        op = node["op"]
        if op == "not":
            stack.append(node["a"])
        elif op in ("implies", "iff"):
            stack.extend((node["a"], node["b"]))
        elif op in ("and", "or", "atmost", "atleast", "exactly"):
            stack.extend(node["args"])
        elif op == "wsum":
            stack.extend(term["e"] for term in node["terms"])


def expr_vars(expr: dict[str, Any], out: set[str] | None = None) -> set[str]:
    """Outcome IDs referenced by an Expr (alias refs are not followed)."""
    out = set() if out is None else out
    out.update(node["id"] for node in _walk(expr) if node["op"] == "var")
    return out


def expr_refs(expr: dict[str, Any]) -> set[str]:
    """Alias names referenced by an Expr."""
    return {node["name"] for node in _walk(expr) if node["op"] == "ref"}


def provenance_row(ref: str) -> dict[str, Any]:
    """Provenance row for a REF of a shape the harness knows; ValueError otherwise. An axiom's record is its whole REF,
    as gold's provenance writes it."""
    for pattern, kind, field in _PROVENANCE_SHAPES:
        match = pattern.fullmatch(ref)
        if match:
            record = ref if kind == "axiom" else match.group(1)
            return {"ref": ref, "kind": kind, "record": record, "field": field}
    raise ValueError(f"unsupported provenance ref {ref!r}")


def alias_definitions(binding_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Class alias = meaning of its first bound outcome (var, negated when that binding is negated)."""
    aliases: dict[str, dict[str, Any]] = {}
    for row in binding_rows:
        if row["status"] == "bound" and row["class"] not in aliases:
            expr = var(row["id"])
            aliases[row["class"]] = neg(expr) if row["negated"] else expr
    return aliases


def source_refs(outcome_id: str) -> tuple[str, str, str]:
    """(question REF, outcome_label REF, market_id) for an outcome ID."""
    _, market_id, index = parse_outcome_id(outcome_id)
    return f"src:{market_id}:question", f"src:{market_id}:outcome_label:{index}", market_id


def _dump_strict(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _dump_compact(obj: Any) -> str:
    """The bytes of `dumps`, but NaN and infinity are refused."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _write_usage(path: Path, usage: dict[str, Any]) -> None:
    """usage.json with `calls` streamed: the iterable is consumed once and never held as a list."""
    meta = {k: v for k, v in usage.items() if k != "calls"}
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write('{"calls": [')
        for n, call in enumerate(usage.get("calls", [])):
            fh.write(("" if n == 0 else ", ") + _dump_strict(call))
        fh.write("]")
        for key in sorted(meta):
            fh.write(", " + _dump_strict(key) + ": " + _dump_strict(meta[key]))
        fh.write("}\n")


def write_submission(
    out_dir: Path,
    binding_rows: Iterable[dict[str, Any]],
    constraints: Iterable[dict[str, Any]],
    interpretations: list[str],
    usage: dict[str, Any],
) -> None:
    """Write bindings/theory/provenance/usage, streaming: each iterable is consumed once and rows go
    straight to disk (memory is O(outcomes) for the ID set, not O(theory)).
    Binding rows: id,status,class,negated,missing, plus optional event_title, or optional evidence (supplied REFs:
    when present they replace the derived event title, so a gold binding's own REFs are written verbatim).
    Constraint rows: id,expr,interps, plus optional provenance (extra REFs, e.g. ax:* axioms). usage["calls"] may be an iterable.

    A binding cites its question and outcome_label REFs, plus the market's event_title REF when the row has
    an event title. A constraint cites the question REF of each outcome it mentions, the defining question REF
    of each alias it references, and its supplied provenance. Every cited REF gets a provenance row.
    Duplicate or malformed outcome IDs and unknown outcomes or aliases raise ValueError.

    Atomic: every file is written under a temporary name and moved into place only after all four are
    written, so a failure leaves no truncated bindings, theory, provenance or usage file behind."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = ("bindings.jsonl", "provenance.jsonl", "theory.json", "usage.json")
    tmp = {name: out_dir / (name + ".tmp") for name in names}
    try:
        _write_parts(tmp, binding_rows, constraints, interpretations, usage)
        for name in names:
            os.replace(tmp[name], out_dir / name)
    except BaseException:
        for path in tmp.values():
            path.unlink(missing_ok=True)
        raise


def _with_supplied(base: list[str], supplied: Any) -> list[str]:
    """`base` followed by the supplied REFs, in order and without duplicates; every REF must have a known shape."""
    if not isinstance(supplied, list) or not all(isinstance(r, str) for r in supplied):
        raise ValueError("evidence must be a list of REF strings")
    out = list(dict.fromkeys(base + supplied))
    for ref in out:
        provenance_row(ref)
    return out


def _write_parts(
    tmp: dict[str, Path],
    binding_rows: Iterable[dict[str, Any]],
    constraints: Iterable[dict[str, Any]],
    interpretations: list[str],
    usage: dict[str, Any],
) -> None:
    known: set[str] = set()
    written: set[str] = set()  # REFs that already have a provenance row
    aliases: dict[str, dict[str, Any]] = {}
    alias_ref: dict[str, str] = {}  # class -> question REF of the outcome that defines it
    with open(tmp["bindings.jsonl"], "w", encoding="utf-8", newline="\n") as bf, open(
        tmp["provenance.jsonl"], "w", encoding="utf-8", newline="\n"
    ) as pf, open(tmp["theory.json"], "w", encoding="utf-8", newline="\n") as tf:

        def cite(ref: str) -> None:
            if ref not in written:
                written.add(ref)
                pf.write(_dump_compact(provenance_row(ref)) + "\n")

        for row in binding_rows:
            oid = validate_outcome_id(row["id"])
            if oid in known:
                raise ValueError(f"duplicate outcome id {oid}")
            q_ref, l_ref, market_id = source_refs(oid)
            if "evidence" in row:
                # Supplied evidence (a gold binding's REFs, from an oracle) is authoritative: it carries the event REF
                # itself, so no market-keyed event title is added on top.
                refs = _with_supplied([q_ref, l_ref], row["evidence"])
            else:
                refs = [q_ref, l_ref]
                if row.get("event_title"):
                    refs.append(f"src:{market_id}:event_title")
            for ref in refs:
                cite(ref)
            known.add(oid)
            out = {
                "id": oid,
                "status": row["status"],
                "class": row["class"],
                "negated": bool(row["negated"]),
                "missing": list(row["missing"]),
                "evidence": refs,
            }
            bf.write(_dump_compact(out) + "\n")
            if out["status"] == "bound" and out["class"] not in aliases:
                expr = var(oid)
                aliases[out["class"]] = neg(expr) if out["negated"] else expr
                alias_ref[out["class"]] = q_ref

        tf.write('{"aliases": ' + _dump_strict(aliases) + ', "constraints": [')
        for n, con in enumerate(constraints):
            ids = expr_vars(con["expr"])
            unknown = ids - known
            if unknown:
                raise ValueError(f"constraint {con['id']} references unknown outcomes: {sorted(unknown)[:3]}")
            names = expr_refs(con["expr"])
            # a comprehension: `names - aliases.keys()` walks every alias per constraint (O(constraints x classes)).
            undefined = {n for n in names if n not in aliases}
            if undefined:
                raise ValueError(f"constraint {con['id']} references unknown aliases: {sorted(undefined)[:3]}")
            refs = {source_refs(i)[0] for i in ids} | {alias_ref[name] for name in names} | set(con.get("provenance") or [])
            for ref in sorted(refs):
                cite(ref)
            row = {"id": con["id"], "expr": con["expr"], "interps": con.get("interps"), "provenance": sorted(refs)}
            tf.write((", " if n else "") + _dump_strict(row))
        tf.write('], "interpretations": ' + _dump_strict(list(interpretations)) + "}\n")
    _write_usage(tmp["usage.json"], usage)


class JsonlIndex:
    """Random access to a JSONL file of objects keyed by `qid` without holding the objects in memory:
    one pass records byte offsets; `get` parses a single line on demand.

    Lenient: a line that is not a JSON object with a string `key` (bad JSON, bad UTF-8, non-object, missing
    or non-string key) is skipped and counted in `skipped`, with a warning. A key that occurs more than once
    is listed in `duplicates`: `get` returns the default for it and `[]` raises ValueError. Scoring thus
    treats a duplicated answer as absent (score 0) and a duplicated gold answer as a broken fixture."""

    def __init__(self, path: Path, key: str = "qid"):
        self.path = Path(path)
        self.key = key
        self.offsets: dict[str, int] = {}
        self.duplicates: set[str] = set()
        self.skipped = 0
        with open(self.path, "rb") as fh:
            offset = 0
            for line in fh:
                if line.strip():
                    qid = _line_key(line, key)
                    if qid is None:
                        self.skipped += 1
                    elif qid in self.offsets:
                        self.duplicates.add(qid)
                    else:
                        self.offsets[qid] = offset
                offset += len(line)
        if self.skipped:
            warnings.warn(f"{self.path}: skipped {self.skipped} line(s) without a usable {key!r}", stacklevel=2)
        if self.duplicates:
            warnings.warn(f"{self.path}: {len(self.duplicates)} duplicated {key!r} value(s) ignored", stacklevel=2)
        self._fh = open(self.path, "rb")

    def __contains__(self, qid: object) -> bool:
        return qid in self.offsets

    def __len__(self) -> int:
        return len(self.offsets)

    def get(self, qid: str, default: Any = None) -> Any:
        offset = self.offsets.get(qid)
        if offset is None or qid in self.duplicates:
            return default
        self._fh.seek(offset)
        return json.loads(self._fh.readline().decode("utf-8-sig"))

    def __getitem__(self, qid: str) -> Any:
        if qid not in self.offsets:
            raise KeyError(qid)
        if qid in self.duplicates:
            raise ValueError(f"{self.key} {qid!r} occurs more than once in {self.path}")
        return self.get(qid)

    def close(self) -> None:
        self._fh.close()


def _line_key(line: bytes, key: str) -> str | None:
    try:
        obj = json.loads(line.decode("utf-8-sig"))  # bad JSON and bad UTF-8 are both ValueError
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    value = obj.get(key)
    return value if isinstance(value, str) else None

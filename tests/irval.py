"""Well-formedness checks against the JSON shapes in docs/ir.md (test helper, not part of the package)."""

import json
from pathlib import Path

LOGIC_OPS = {"and", "or", "not", "implies", "iff"}
CARD_OPS = {"atmost", "atleast", "exactly"}
# Source fields docs/ir.md allows in provenance (question, outcome_label, event_title, ...).
SOURCE_FIELDS = {"question", "outcome_label", "event_title"}


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def check_expr(expr, refs=frozenset()):
    """Return a list of problems with an Expr (docs/ir.md, Expr section)."""
    problems = []

    def walk(x):
        if not isinstance(x, dict) or not isinstance(x.get("op"), str):
            problems.append(f"not an Expr object: {x!r}"[:200])
            return
        op = x["op"]
        if op in ("true", "false"):
            return
        if op == "var":
            if not isinstance(x.get("id"), str):
                problems.append("var without string id")
        elif op == "ref":
            if x.get("name") not in refs:
                problems.append(f"ref to unknown alias {x.get('name')!r}")
        elif op == "not":
            walk(x.get("a"))
        elif op in ("and", "or"):
            args = x.get("args")
            if not isinstance(args, list):
                problems.append(f"{op} needs args list")
            else:
                for a in args:
                    walk(a)
        elif op in ("implies", "iff"):
            walk(x.get("a"))
            walk(x.get("b"))
        elif op in CARD_OPS:
            if not _is_int(x.get("k")):
                problems.append(f"{op} needs integer k")
            if not isinstance(x.get("args"), list):
                problems.append(f"{op} needs args list")
            else:
                for a in x["args"]:
                    walk(a)
        elif op == "wsum":
            if x.get("cmp") not in ("le", "ge", "eq") or not _is_int(x.get("k")):
                problems.append("wsum needs cmp in le/ge/eq and integer k")
            terms = x.get("terms")
            if not isinstance(terms, list):
                problems.append("wsum needs terms list")
            else:
                for t in terms:
                    if not isinstance(t, dict) or not _is_int(t.get("w")):
                        problems.append("wsum term needs integer w")
                    else:
                        walk(t.get("e"))
        else:
            problems.append(f"unknown op {op!r}")

    walk(expr)
    return problems


def _rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def check_submission(directory):
    """Return a list of problems with a submission directory (bindings, theory, provenance, usage)."""
    d = Path(directory)
    problems = []
    bindings = _rows(d / "bindings.jsonl")
    theory = json.loads((d / "theory.json").read_text(encoding="utf-8"))
    provenance = _rows(d / "provenance.jsonl")
    usage = json.loads((d / "usage.json").read_text(encoding="utf-8"))

    ids = [b["id"] for b in bindings]
    if len(ids) != len(set(ids)):
        problems.append("duplicate binding ids")
    refs = {p["ref"] for p in provenance}
    if len(refs) != len(provenance):
        problems.append("duplicate provenance refs")
    for p in provenance:
        if set(p) != {"ref", "kind", "record", "field"} or p["kind"] not in ("source", "axiom"):
            problems.append(f"bad provenance row {p}")
        elif p["kind"] == "source" and p["field"] not in SOURCE_FIELDS:
            problems.append(f"source provenance with unknown field {p}")
        elif p["kind"] == "axiom" and p["field"] != "rule":
            problems.append(f"axiom provenance field must be 'rule' {p}")
    for b in bindings:
        if set(b) != {"id", "status", "class", "negated", "missing", "evidence"}:
            problems.append(f"bad binding keys {sorted(b)}")
            continue
        if b["status"] == "bound":
            if not isinstance(b["class"], str) or b["missing"] != []:
                problems.append(f"bound binding malformed {b['id']}")
        elif b["status"] == "unresolved":
            if b["class"] is not None or not b["missing"]:
                problems.append(f"unresolved binding malformed {b['id']}")
        else:
            problems.append(f"bad status {b['status']!r}")
        if not b["evidence"] or not set(b["evidence"]) <= refs:
            problems.append(f"binding evidence missing or dangling {b['id']}")

    interps = theory.get("interpretations")
    if not isinstance(interps, list) or not interps:
        problems.append("theory.interpretations must be a non-empty list")
        interps = []
    aliases = theory.get("aliases", {})
    for name, expr in aliases.items():
        for p in check_expr(expr, refs=frozenset(aliases)):
            problems.append(f"alias {name}: {p}")
    constraint_ids = set()
    bound_ids = set(ids)
    for c in theory.get("constraints", []):
        if set(c) != {"id", "expr", "interps", "provenance"}:
            problems.append(f"bad constraint keys {sorted(c)}")
            continue
        if c["id"] in constraint_ids:
            problems.append(f"duplicate constraint id {c['id']}")
        constraint_ids.add(c["id"])
        for p in check_expr(c["expr"], refs=frozenset(aliases)):
            problems.append(f"constraint {c['id']}: {p}")
        if c["interps"] is not None and not set(c["interps"]) <= set(interps):
            problems.append(f"constraint {c['id']} uses unknown interpretation")
        if not c["provenance"] or not set(c["provenance"]) <= refs:
            problems.append(f"constraint {c['id']} provenance missing or dangling")
    for c in theory.get("constraints", []):
        if "expr" in c:
            from mentat_bench.ir import expr_vars

            unknown = expr_vars(c["expr"]) - bound_ids
            if unknown:
                problems.append(f"constraint {c.get('id')} mentions unbound outcomes {sorted(unknown)[:3]}")

    if not {"calls", "wall_seconds", "peak_rss_mb"} <= set(usage) <= {"calls", "wall_seconds", "peak_rss_mb", "torn_usage_rows"}:
        problems.append(f"usage keys {sorted(usage)}")
    for call in usage.get("calls", []):
        required = {"model", "kind", "tokens_in", "tokens_out", "seconds", "cost_usd", "retry"}
        optional = {"items", "gpu_seconds", "child_cpu_seconds", "child_peak_rss_mb", "op", "torn"}  # all optional
        if not required <= set(call) <= required | optional:
            problems.append(f"bad call keys {sorted(call)}")
        if call.get("kind") not in ("llm", "embedding", "local"):
            problems.append(f"bad call kind {call.get('kind')!r}")
        if "op" in call and call["op"] not in ("decide_binding", "decide_relation", "decide_group"):
            problems.append(f"bad call op {call['op']!r}")
    return problems

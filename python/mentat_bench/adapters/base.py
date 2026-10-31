"""Typed decision protocol shared by all model adapters (docs/ir.md; spec section 9).

An adapter method returns (decision, usage). Decisions:
- decide_binding(outcome_context) -> {"status": "bound"|"unresolved", "class": str|None,
  "negated": bool, "missing": [code..]}; unresolved decisions need >= 1 missing code.
- decide_relation(a_context, b_context) -> one of RELATIONS, read as a relation of proposition a
  to proposition b: IMPLIES means a => b, IMPLIED_BY means b => a, EXCLUDES means not (a and b).
- decide_group(group_ctx) -> list of group decisions (optional; see check_group). group_ctx is
  {"group_id", "source", "members": [outcome context..]}; every decision names members of that context only.
Outcome contexts are dicts with keys id, question, label, event_title, and context when the corpus row has one.
usage is {"model", "kind", "tokens_in", "tokens_out", "cost_usd"} plus optional "items" (decisions the call carried,
at least 1, default 1), "gpu_seconds" (finite, non-negative) and the child metering "child_cpu_seconds" and
"child_peak_rss_mb" (finite, non-negative; local children only); the gateway adds seconds, retry and op.
"""

from __future__ import annotations

import math
import re
from typing import Any, Collection, Iterable, Protocol

from ..ir import provenance_row

RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE", "UNRESOLVED")
USAGE_KINDS = ("llm", "embedding", "local")
USAGE_OPS = ("decide_binding", "decide_relation", "decide_group")  # the decision method a gateway usage entry charges
GROUP_KINDS = ("exactly_one", "at_most_one", "at_least_k", "at_most_k", "ordered_chain")
PROB_OPTIONS = {  # the option keys of a decision's optional "probs" (docs/local_protocol.md)
    "relation": ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE"),
    "group": ("none",) + GROUP_KINDS,
    "binding": ("bound", "unresolved"),
}
PROB_SUM_TOLERANCE = 1e-2
DIRECTIONS = ("forward", "reverse")
AXIS_NAME = re.compile(r"[a-z0-9_]{1,32}")
MAX_GROUP_AXES = 3  # distinct scope axes over the whole theory
MAX_SCOPE_VALUES = 4  # per axis, in a decision's values and in its of


class AdapterError(RuntimeError):
    """Transient adapter failure; the gateway retries it. `usage` is the spend already incurred, if known."""

    def __init__(self, message: str, usage: dict[str, Any] | None = None):
        super().__init__(message)
        self.usage = usage


class AdapterFatal(RuntimeError):
    """The adapter cannot continue (its process keeps dying). Not retried and not turned into adapter_error
    decisions: the build stops with a non-zero exit."""


class DecisionError(ValueError):
    """An adapter returned a malformed decision; the harness treats it as unresolved."""


class ScopeError(DecisionError):
    """A group decision whose scope is malformed or over the per-axis caps. Counted as group_scope_rejected."""


class ModelAdapter(Protocol):
    name: str

    def decide_binding(self, outcome_context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def decide_relation(self, a_context: dict[str, Any], b_context: dict[str, Any]) -> tuple[str, dict[str, Any]]: ...


def usage(model: str, kind: str = "local", tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0) -> dict[str, Any]:
    return {"model": model, "kind": kind, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd}


def finite_number(record: dict[str, Any], key: str, integer: bool = False) -> int | float:
    """record[key] as a finite, non-negative number (an integer when `integer`); AdapterError otherwise."""
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise AdapterError(f"usage {key} must be a finite non-negative number, got {value!r}")
    if integer:
        if value != int(value):
            raise AdapterError(f"usage {key} must be an integer, got {value!r}")
        return int(value)
    return float(value)


def check_usage(record: Any) -> dict[str, Any]:
    """Validated accounting fields of one usage record. Missing, null, non-finite, negative or fractional
    token counts and non-finite or negative costs are rejected, so they never reach the spend total. The optional
    items (at least 1), gpu_seconds and the child metering fields are validated when present and kept only then."""
    if not isinstance(record, dict):
        raise AdapterError("usage is not an object")
    model = record.get("model")
    if not isinstance(model, str) or not model:
        raise AdapterError("usage model must be a non-empty string")
    fields = {
        "model": model,
        "kind": record.get("kind"),
        "tokens_in": finite_number(record, "tokens_in", integer=True),
        "tokens_out": finite_number(record, "tokens_out", integer=True),
        "cost_usd": finite_number(record, "cost_usd"),
    }
    if "items" in record:
        if finite_number(record, "items", integer=True) < 1:
            raise AdapterError(f"usage items must be at least 1, got {record['items']!r}")
        fields["items"] = int(record["items"])
    if "gpu_seconds" in record:
        fields["gpu_seconds"] = finite_number(record, "gpu_seconds")
    for key in ("child_cpu_seconds", "child_peak_rss_mb"):
        if key in record:
            fields[key] = finite_number(record, key)
    return fields


def check_binding(decision: Any) -> dict[str, Any]:
    """Normalise a binding decision to {status, class, negated, missing}; raises DecisionError."""
    if not isinstance(decision, dict):
        raise DecisionError("binding decision is not an object")
    status = decision.get("status")
    missing = decision.get("missing", [])
    if not isinstance(missing, list) or not all(isinstance(m, str) for m in missing):
        raise DecisionError("missing must be a list of strings")
    if status == "bound":
        cls, negated = decision.get("class"), decision.get("negated")
        if not isinstance(cls, str) or not cls or not isinstance(negated, bool):
            raise DecisionError("bound decision needs a class name and boolean negated")
        return _with_evidence({"status": "bound", "class": cls, "negated": negated, "missing": []}, decision)
    if status == "unresolved":
        if not missing:
            raise DecisionError("unresolved decision needs at least one missing code")
        return _with_evidence({"status": "unresolved", "class": None, "negated": False, "missing": list(missing)}, decision)
    raise DecisionError(f"bad binding status {status!r}")


def _with_evidence(result: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Adds the optional `evidence` (provenance REFs of a known shape) when the decision carries it; absent is unchanged."""
    if "evidence" in decision:
        refs = decision["evidence"]
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            raise DecisionError("evidence must be a list of REF strings")
        for ref in refs:
            try:
                provenance_row(ref)
            except ValueError as exc:
                raise DecisionError(str(exc)) from None
        result["evidence"] = list(refs)
    return result


def check_relation(decision: Any) -> str:
    if decision not in RELATIONS:
        raise DecisionError(f"bad relation {decision!r}")
    return decision


def _finite(value: Any, name: str) -> float:
    """value as a finite float; DecisionError otherwise (a bool, a non-number, NaN or infinity, or an integer too large for a float)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionError(f"{name} must be a finite number, got {type(value).__name__}")
    try:
        number = float(value)  # a JSON integer too large for a float is an OverflowError here
    except OverflowError:
        raise DecisionError(f"{name} is too large to be a finite number") from None
    if not math.isfinite(number):
        raise DecisionError(f"{name} must be a finite number, got {value!r}")
    return number


def check_conf(value: Any) -> float | None:
    """The optional confidence of a decision: None when absent (or null), else a finite number; DecisionError otherwise."""
    if value is None:
        return None
    return _finite(value, "conf")


def check_probs(value: Any, options: tuple[str, ...]) -> dict[str, float] | None:
    """The optional per-option probabilities of a decision (PROB_OPTIONS): None when absent (or null), else an object with
    exactly `options` as its keys, each a finite number in [0, 1], summing to 1 within PROB_SUM_TOLERANCE. DecisionError otherwise."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(options):
        raise DecisionError(f"probs must be an object with exactly the keys {list(options)}")
    probs = {option: _finite(value[option], f"probs {option}") for option in options}
    if not all(0.0 <= p <= 1.0 for p in probs.values()):
        raise DecisionError("probs must each be in [0, 1]")
    total = sum(probs.values())
    if abs(total - 1.0) > PROB_SUM_TOLERANCE:
        raise DecisionError(f"probs must sum to 1, got {total!r}")
    return probs


def check_relation_decision(decision: Any) -> tuple[str, float | None]:
    """(relation, conf) of a relation decision: a relation name, or {"relation": name, "conf": number} where conf is optional
    (see docs/local_protocol.md). DecisionError when the relation or the conf is malformed. probs are not read here: the
    adapter checks them (check_probs) and drops a malformed set."""
    if isinstance(decision, dict):
        return check_relation(decision.get("relation")), check_conf(decision.get("conf"))
    return check_relation(decision), None


def check_group(decision: Any, member_ids: Collection[str]) -> dict[str, Any]:
    """Normalise one group decision to {kind, members, k, direction, scope} (and conf, when the decision carries one);
    DecisionError (ScopeError for scope). Kinds: exactly_one and at_most_one (k is 1), at_least_k and at_most_k
    (1 <= k <= members), ordered_chain (members in chain order; direction forward or reverse, default forward).
    Members are distinct and must be among member_ids, at least two. scope is null or {axis, values, of}: axis matches
    [a-z0-9_]{1,32}, values are a non-empty subset of of, at most MAX_SCOPE_VALUES each. The interpretation cap is the
    harness's."""
    if not isinstance(decision, dict):
        raise DecisionError("group decision is not an object")
    kind = decision.get("kind")
    if kind not in GROUP_KINDS:
        raise DecisionError(f"bad group kind {kind!r}")
    conf = check_conf(decision.get("conf"))
    members = decision.get("members")
    if not isinstance(members, list) or len(members) < 2 or not all(isinstance(m, str) for m in members):
        raise DecisionError("group members must be a list of at least two outcome ids")
    if len(set(members)) != len(members):
        raise DecisionError("group members must be distinct")
    unknown = [m for m in members if m not in member_ids]
    if unknown:
        raise DecisionError(f"group names members outside its context: {unknown[0]!r}")
    n, k, direction = len(members), decision.get("k"), decision.get("direction")
    if kind in ("exactly_one", "at_most_one"):
        if k not in (None, 1) or isinstance(k, bool):
            raise DecisionError(f"{kind} has k 1")
        k = 1
    elif kind in ("at_least_k", "at_most_k"):
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= n:
            raise DecisionError(f"{kind} needs an integer k with 1 <= k <= {n}")
    else:
        if k is not None:
            raise DecisionError("ordered_chain takes no k")
    if kind == "ordered_chain":
        direction = "forward" if direction is None else direction
        if direction not in DIRECTIONS:
            raise DecisionError(f"bad direction {direction!r}")
    elif direction is not None:
        raise DecisionError(f"{kind} takes no direction")
    item = {"kind": kind, "members": list(members), "k": k, "direction": direction, "scope": _check_scope(decision.get("scope"))}
    if conf is not None:
        item["conf"] = conf
    return item


def _check_scope(scope: Any) -> dict[str, Any] | None:
    if scope is None:
        return None
    if not isinstance(scope, dict) or set(scope) != {"axis", "values", "of"}:
        raise DecisionError("scope must be null or an object with exactly axis, values and of")
    axis, values, of = scope["axis"], scope["values"], scope["of"]
    if not isinstance(axis, str) or not AXIS_NAME.fullmatch(axis):
        raise ScopeError(f"bad scope axis {axis!r}")
    for name, group in (("values", values), ("of", of)):
        if not isinstance(group, list) or not all(isinstance(v, str) and AXIS_NAME.fullmatch(v) for v in group):
            raise ScopeError(f"scope {name} must be a list of [a-z0-9_] names")
        if len(set(group)) != len(group) or len(group) > MAX_SCOPE_VALUES:
            raise ScopeError(f"scope {name} needs distinct values, at most {MAX_SCOPE_VALUES} of them")
    if not values or not set(values) <= set(of):
        raise ScopeError("scope values must be non-empty and a subset of of")
    return {"axis": axis, "values": sorted(values), "of": sorted(of)}



def group_terms(lo: int, hi: int, n: int, chain: bool) -> list[tuple[str, int | None]]:
    """Canonical constraint terms [(kind, k), ...] for a member set of size n whose true-count bounds are lo..hi
    (0 <= lo <= hi <= n, hi >= 1; ValueError otherwise), optionally an ordered chain. Order is fixed: ordered_chain first, then
    the bounds. lo == hi == 1 is exactly_one alone, and hi == 1 is at_most_one; both carry k None. No terms is no constraint."""
    if not 0 <= lo <= hi <= n or hi < 1:
        raise ValueError(f"group bounds need 0 <= lo <= hi <= n and hi >= 1, got lo {lo}, hi {hi}, n {n}")
    t: list[tuple[str, int | None]] = [("ordered_chain", None)] if chain else []
    if lo == hi == 1:
        return t + [("exactly_one", None)]
    if lo >= 1:
        t.append(("at_least_k", lo))
    if hi < n:
        t.append(("at_most_one", None) if hi == 1 else ("at_most_k", hi))
    return t


def terms_class(terms: Iterable[tuple[str, int | None]]) -> str:
    """The scoring class of a term list: each term's class key (kind, or kind:k for at_least_k and at_most_k), joined by '+'.
    'none' for no terms."""
    return "+".join(f"{kind}:{k}" if k is not None else kind for kind, k in terms) or "none"


READINGS = ("base", "alt")  # the two readings of an ambiguous proposition (a reading question)


def reading_picks(decisions: Any, members: Collection[str]) -> dict[str, str]:
    """The picks of a reading question's answer: {"base": p, "alt": p}, where p is an option outcome id from members[1:], "none"
    (no counted chain for that reading) or "invalid" (anything else). members[0] is u, the proposition asked about. The chains
    read are the valid two-member ordered_chain decisions that contain u; the implied order of a reverse chain is reversed. A
    chain counts for reading r when its scope is None, or when its axis is "reading", its of is exactly ["alt", "base"] and r is
    in its values. A pick c needs both implied directions, [u, c] and [c, u], and those two must be the only chains counted for
    r. Non-chain and malformed decisions are ignored. An answer that is not a list of decisions gives "invalid" for both."""
    groups = decisions.get("groups") if isinstance(decisions, dict) else decisions
    if not isinstance(groups, list):
        return {reading: "invalid" for reading in READINGS}
    u = members[0]
    chains = []  # (scope, implied order) of the valid two-member chains that contain u
    for group in groups:
        try:
            d = check_group(group, members)
        except DecisionError:
            continue
        if d["kind"] != "ordered_chain" or len(d["members"]) != 2 or u not in d["members"]:
            continue
        order = d["members"][::-1] if d["direction"] == "reverse" else d["members"]
        chains.append((d["scope"], tuple(order)))
    picks = {}
    for reading in READINGS:
        counted = [order for scope, order in chains if scope is None
                   or (scope["axis"] == "reading" and scope["of"] == ["alt", "base"] and reading in scope["values"])]
        if not counted:
            picks[reading] = "none"
        elif len(counted) == 2 and counted[1] == counted[0][::-1]:
            x, y = counted[0]
            picks[reading] = y if x == u else x
        else:
            picks[reading] = "invalid"
    return picks

"""Results table over run directories: python -m mentat_bench.report --runs DIR [DIR ...] [--json PATH] [--md PATH].

Each run directory is what `runcell` writes: score.json (the score report), build_summary.json (the build summary),
cell.json (what ran), and submission/usage.json, submission/build_policy.json. score_extra.json (runcell
--extra-queries) gives the extra Q_N. score_decisions.json (decision_scoring, the curated suite) gives the decision columns. A missing optional part leaves its columns empty ("-" in markdown, null in JSON); a missing
score.json is exit 2, and so is a missing or malformed fixtures/VERSIONS.json (the reason is printed). STATUS is complete,
budget_stopped, pairs_capped, calls_capped, group_budget_stopped or incomplete: cell.json's status when it has one (a failed
step makes it incomplete), else build_summary.json's. The model column is the
child's hello model id from build_policy.json (usage model names when absent), with its revision. The markdown table
goes to stdout (or --md); --json writes the same rows as JSON. The speed columns (decisions per second per phase, call
latency) are computed by speed(); the JSON rows also carry each phase's breakdown.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from . import corrections
from .gateway import usage_totals
from .harness.fixed import FLAG_DEFAULTS, build_status
from .scoring import FAMILIES

EXIT_USAGE = 2
COLUMNS = (
    ("run", "run"), ("status", "status"), ("fixture", "fixture"), ("adapter", "adapter"), ("model", "model_id"),
    ("revision", "model_revision"), ("Q_N (repaired, diagnostic)", "Q_N"), ("strict Q_N (diagnostic)", "strict_Q_N"),
    ("base repaired", "base_repaired"), ("groups dropped%", "groups_dropped_pct"),
    ("closure", "closure"), ("selection", "selection"),
    ("feasibility", "feasibility"), ("partition", "partition"), ("exact", "exact_query_success"),
    ("false_ent", "false_entailment"), ("false_ref", "false_refutation"), ("weighted Q_N", "weighted_Q_N"),
    ("anchor Q_N", "anchor_Q_N"), ("active Q_N", "active_Q_N"), ("extra Q_N", "extra_Q_N"),
    ("blocking_recall", "blocking_recall"),
    ("group_recall", "group_recall"), ("groups off", "groups_disabled_reason"),
    ("relations asked", "relations_asked"), ("relations dropped", "relations_dropped"), ("dropped%", "dropped_pct"),
    ("resumed", "resumed"), ("pairs", "candidate_pairs"),
    ("pairs asked cap", "pairs_asked_cap"), ("derived", "pairs_derived"), ("derived dropped", "derived_dropped"),
    ("dedup reps", "dedup_representatives"), ("dedup undecided", "dedup_undecided"),
    ("dedup fallback asked", "dedup_fallback_asked"), ("dedup fallback skipped", "dedup_fallback_skipped"),
    ("complement fallback", "complement_fallback"), ("pair order", "pair_order"), ("relation calls", "relations_committed"),
    ("items", "items"), ("usage entries", "calls"), ("wall s", "wall_seconds"), ("gpu s", "gpu_seconds"),
    ("peak RSS MB", "peak_rss_mb"), ("cost USD", "cost_usd"), ("complement dedup", "complement_dedup"),
    ("top_k", "top_k"), ("batch", "batch_size"),
    ("bind items/s", "bindings_items_per_s"), ("grp items/s", "groups_items_per_s"),
    ("rel items/s", "relations_items_per_s"), ("call p50 s", "call_p50_s"), ("call p95 s", "call_p95_s"),
    ("items/call", "items_per_call"), ("rel wall s", "relations_wall_seconds"),
    ("headline MCC", "decision_headline"), ("headline metric", "decision_headline_metric"),
    ("rel MCC (headline v1-v2, part v3)", "decision_rel_mcc"), ("bind MCC (secondary)", "decision_bind_mcc"),
    ("grp MCC (diagnostic v1-v2, part v3)", "decision_grp_mcc"), ("read MCC (diagnostic)", "decision_read_mcc"),
    ("rel macro-F1 (diagnostic)", "decision_rel_macro_f1"), ("rel Brier (diagnostic)", "decision_rel_brier"),
    ("rel ECE (diagnostic)", "decision_rel_ece"), ("held-out rel MCC (diagnostic)", "decision_held_out_rel_mcc"),
)
PHASES = ("bindings", "groups", "relations")  # the build's model-call phases in run order (blocking makes no call)
OP_PHASES = {"decide_binding": "bindings", "decide_relation": "relations", "decide_group": "groups"}  # a usage entry's op


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _optional(path: Path) -> Any:
    try:
        return _read(path)
    except (OSError, ValueError):
        return None


def _block(score: dict[str, Any], name: str) -> Any:
    """Q_N of an anchor or active block, or its status when it has no value."""
    block = score.get(name) or {}
    return block.get("Q_N") if block.get("Q_N") is not None else block.get("status")


def _repaired(build: dict[str, Any], cell: dict[str, Any]) -> bool | None:
    """Did the base need a repair? True when the build summary or cell.json says so (base_repaired, or a relation, derived
    or group drop), False when a summary or cell records none, None when neither records anything."""
    if build.get("base_repaired") is True or cell.get("base_repaired") is True:
        return True
    if any((build.get(key) or 0) > 0 for key in ("relations_dropped", "derived_dropped", "groups_dropped")):
        return True
    if "base_repaired" in build or "relations_dropped" in build or cell.get("base_repaired") is False:
        return False
    return None


def _stale(stamp: Any, registry: Path) -> bool:
    """True when a fixture stamp is not the active registered version of its name. A registry that cannot be read is a
    CorrectionError (run_row refuses the report with its reason); it is never taken as stale."""
    if not isinstance(stamp, dict):
        return True
    active = [e for e in corrections.list_entries(registry)
              if e.get("name") == stamp.get("name") and e.get("status") == "active"]
    return not active or active[0].get("content_hash") != stamp.get("content_hash")


def _rerun_scores(run: Path) -> list[Path]:
    """The score_extra.json files of the extra re-runs (corrections.rerun_extra): <run>/extra_<name>_v<version>/."""
    return [path for path in sorted(run.glob("extra_*_v*/score_extra.json")) if isinstance(_optional(path), dict)]


def _extra(run: Path, cell: dict[str, Any], registry: Path) -> tuple[Any, Any, Any, str | None]:
    """(extra Q_N column, extra status, recorded Q_N, the file the column comes from) of the --extra-queries pass. The
    column shows the Q_N, or failed / not_run when the pass did not finish, missing when the pass ran but wrote no score, and
    stale when score_extra.json was scored under a version that is not the registry's active one. A re-run whose score is
    under the active version is preferred: the newest one of the extra_<name>_v<version>/ directories, else score_extra.json.
    The recorded Q_N is always the run's own score_extra.json, which can differ from a re-run's."""
    state = cell.get("extra_status")
    if state in ("failed", "not_run"):
        return state, state, None, None
    own = _optional(run / "score_extra.json")
    recorded = own.get("Q_N") if isinstance(own, dict) else None
    active = [path for path in _rerun_scores(run)
              if not _stale(_optional(path).get("fixture_stamp"), registry)]
    if active:
        path = max(active, key=lambda p: p.stat().st_mtime)
        return _optional(path).get("Q_N"), "ok", recorded, f"{path.parent.name}/score_extra.json"
    if not isinstance(own, dict):
        return ("missing", "missing", None, None) if state == "ok" else (None, None, None, None)
    if _stale(own.get("fixture_stamp"), registry):
        return "stale", "stale", recorded, "score_extra.json"
    return recorded, "ok", recorded, "score_extra.json"


MIN_CALL_S = 0.001  # a mean call under this many seconds is too fast to time: its rate and percentiles are not reported


def _stats(secs: list[float], items: list[int]) -> dict[str, Any]:
    """Calls, charged items, call seconds, items per second, items per call, and the p50 and p95 call seconds (nearest
    rank) of a non-empty list of calls. The rate and the percentiles are None when the mean call is under MIN_CALL_S."""
    total, ordered = sum(secs), sorted(secs)
    timed = total >= MIN_CALL_S * len(secs)
    return {"calls": len(secs), "items": sum(items), "seconds": round(total, 3),
            "items_per_s": round(sum(items) / total, 2) if timed else None,
            "items_per_call": round(sum(items) / len(secs), 2),
            "p50_s": round(ordered[math.ceil(0.5 * len(secs)) - 1], 3) if timed else None,
            "p95_s": round(ordered[math.ceil(0.95 * len(secs)) - 1], 3) if timed else None}


def _phase_of_calls(calls: list[dict[str, Any]], secs: list[float], build: dict[str, Any]) -> list[str] | None:
    """The phase of each call. When every call names a decision op (the gateway writes it), the phase is OP_PHASES[op].
    Otherwise (usage.json from before the op, or a pre-op build resumed by a newer harness) the phase is by time: the
    build's calls are contiguous per phase in PHASES order, so a call belongs to the phase whose span of build_summary's
    phase_call_seconds holds its midpoint. None when those seconds do not add up to the calls' seconds (each is rounded per
    call), or when the mean call is under MIN_CALL_S (near-zero calls do not place their midpoints)."""
    if all(isinstance(c.get("op"), str) and c["op"] in OP_PHASES for c in calls):
        return [OP_PHASES[c["op"]] for c in calls]
    phase_seconds = build.get("phase_call_seconds") or {}
    if sum(secs) < MIN_CALL_S * len(secs) or abs(sum(phase_seconds.values()) - sum(secs)) > 0.0005 * len(secs) + 0.001:
        return None
    spans, end = [], 0.0
    for name in PHASES:
        if name in phase_seconds:
            end += phase_seconds[name]
            spans.append((name, end))
    if not spans:
        return None
    phases, before, k = [], 0.0, 0
    for sec in secs:
        middle = before + sec / 2
        while k < len(spans) - 1 and middle > spans[k][1]:
            k += 1
        phases.append(spans[k][0])
        before += sec
    return phases


def speed(usage: dict[str, Any], build: dict[str, Any]) -> dict[str, Any]:
    """Speed of a run's model calls (None where the data is missing or too fast to time). Torn placeholder rows (a resume's
    cut usage row) are left out. Items are charged as in usage_totals: a retried call's items count too, and a call without
    an items count is one item. Each call's phase is its op when every call names one, else by time (_phase_of_calls).
    Per phase: `<phase>_items_per_s` is the charged items over the phase's call seconds; `phases` (JSON) has each phase's
    calls, items, seconds, items_per_s, items_per_call, p50_s and p95_s. Over all calls: call_p50_s, call_p95_s (per call,
    nearest rank), items_per_call, and relations_wall_seconds (build_summary's phase_seconds, the relation phase)."""
    calls = [c for c in usage.get("calls") or [] if not c.get("torn")]
    secs = [c.get("seconds") or 0 for c in calls]
    items = [c["items"] if isinstance(c.get("items"), int) else 1 for c in calls]
    names = _phase_of_calls(calls, secs, build) if calls else None
    phases = None if names is None else {
        name: _stats([s for s, p in zip(secs, names) if p == name], [i for i, p in zip(items, names) if p == name])
        for name in PHASES if name in names}
    overall = _stats(secs, items) if calls else {}
    return {**{f"{name}_items_per_s": (phases or {}).get(name, {}).get("items_per_s") for name in PHASES},
            "call_p50_s": overall.get("p50_s"), "call_p95_s": overall.get("p95_s"),
            "items_per_call": overall.get("items_per_call"),
            "relations_wall_seconds": (build.get("phase_seconds") or {}).get("relations"), "phases": phases}


def decision_metrics(scoring: Any) -> dict[str, Any]:
    """The decision columns of a run's score_decisions.json. Each MCC is read from its op's block (ops.<op>.mcc), so a file
    in the old layout (headline mcc_mean, components) and one in the new (headline relation_mcc) give the same columns. The
    headline is the file's headline block when it names the relation and group mean (fixture version 3 and later, owner
    decision 2026-10-30); otherwise the relation MCC (versions 1 and 2, owner decision 2026-10-29). The binding MCC is
    secondary; the group and reading MCCs are diagnostics, and the group MCC is a part of the version 3 headline. Relation
    macro-F1, Brier and ECE are the relation op's; each is None when absent."""
    scoring = scoring if isinstance(scoring, dict) else {}
    ops = scoring.get("ops") or {}

    def block(op: str) -> dict[str, Any]:
        found = ops.get(op)
        return found if isinstance(found, dict) else {}

    head = scoring.get("headline") if isinstance(scoring.get("headline"), dict) else {}
    if head.get("metric") == "relation_group_mcc":
        headline_metric, headline = "relation_group_mcc", head.get("relation_group_mcc")
    else:
        headline_metric, headline = ("relation_mcc" if ops else None), block("decide_relation").get("mcc")
    rel = block("decide_relation")
    return {
        "decision_headline": headline, "decision_headline_metric": headline_metric,
        "decision_rel_mcc": block("decide_relation").get("mcc"),
        "decision_bind_mcc": block("decide_binding").get("mcc"),
        "decision_grp_mcc": block("decide_group").get("mcc"),
        "decision_read_mcc": block("decide_reading").get("mcc"),
        "decision_rel_macro_f1": rel.get("macro_f1"),
        "decision_rel_brier": (rel.get("brier") or {}).get("value"),
        "decision_rel_ece": (rel.get("ece") or {}).get("value"),
        "decision_held_out_rel_mcc": ((rel.get("by_split") or {}).get("held_out") or {}).get("mcc"),
    }


def run_row(run: Path, registry: Path = corrections.REGISTRY) -> dict[str, Any]:
    """One row of the report for a run directory; ValueError when score.json is missing or unreadable. The extra
    column is checked against `registry` (the active fixture versions)."""
    corrections.read_registry(registry)  # a malformed or missing registry refuses the report, whatever the run holds
    cell = _optional(run / "cell.json") or {}
    try:
        score = _read(run / "score.json")
    except (OSError, ValueError) as exc:
        if "status" not in cell:  # only a cell that ran (and recorded its status, e.g. a failed build) may lack a score
            raise ValueError(f"{run}: no readable score.json ({exc})") from exc
        score = {}
    build_doc = _optional(run / "build_summary.json")
    build = build_doc or {}
    usage_doc = _optional(run / "submission" / "usage.json")
    policy = _optional(run / "submission" / "build_policy.json") or {}
    totals = usage_totals(usage_doc) if usage_doc else {}
    families = score.get("families", {})
    extra_column, extra_state, extra_recorded, extra_from = _extra(run, cell, registry)  # --extra-queries runs only
    decision_scoring = _optional(run / "score_decisions.json")
    repaired = _repaired(build, cell)
    model = policy.get("model") or {}
    usage_models = sorted({c.get("model") for c in usage_doc.get("calls", []) if c.get("model")}) if usage_doc else []
    return {
        "run": run.name,
        "status": cell["status"] if "status" in cell else (build_status(build) if build_doc is not None else None),
        "fixture": cell.get("fixture"),
        "adapter": policy.get("adapter") or cell.get("adapter"),
        "model_id": model.get("id") or ", ".join(usage_models) or None,
        "model_revision": model.get("revision"),
        "Q_N": score.get("Q_N"),
        **{family: families.get(family, {}).get("mean_utility") for family in FAMILIES},
        "exact_query_success": score.get("exact_query_success"),
        "false_entailment": score.get("false_entailment"),
        "false_refutation": score.get("false_refutation"),
        "weighted_Q_N": (score.get("weighted") or {}).get("Q_N"),
        "anchor_Q_N": _block(score, "anchor"),
        "active_Q_N": _block(score, "active"),
        "extra_Q_N": extra_column,
        "extra_status": extra_state,
        "extra_Q_N_recorded": extra_recorded,
        "extra_from": extra_from,  # the file the extra Q_N is read from (a re-run directory or score_extra.json)
        "blocking_recall": build.get("blocking_recall"),
        "group_recall": build.get("group_recall"),
        "groups_disabled_reason": build.get("groups_disabled_reason"),
        "base_repaired": repaired,
        "groups_dropped_pct": build.get("groups_dropped_pct"),
        # spec s9: an inconsistent base fails its universe, so a repaired base scores 0 in the strict column
        "strict_Q_N": None if repaired is None else (0.0 if repaired else score.get("Q_N")),
        "relations_asked": build.get("relations_asked"),
        "relations_dropped": build.get("relations_dropped"),
        # the contradiction rate: how often the model's relations had to be repaired (a penalty signal; the theory is
        # scored after the repair)
        "dropped_pct": round(100 * build["contradiction_rate"], 2) if build.get("contradiction_rate") is not None else None,
        "resumed": build.get("resumed") if build_doc is not None else None,
        "candidate_pairs": build.get("candidate_pairs"),
        "pairs_asked_cap": build.get("pairs_asked_cap"),
        "pairs_derived": build.get("pairs_derived"),
        "derived_dropped": build.get("derived_dropped"),
        "dedup_representatives": build.get("dedup_representatives"),
        "dedup_undecided": build.get("dedup_undecided"),
        "dedup_fallback_asked": build.get("dedup_fallback_asked"),
        "dedup_fallback_skipped": build.get("dedup_fallback_skipped"),
        "relations_committed": build.get("relations_committed"),
        "items": totals.get("items"),
        "calls": totals.get("calls"),
        "wall_seconds": totals.get("wall_seconds"),
        "gpu_seconds": totals.get("gpu_seconds"),
        "peak_rss_mb": totals.get("peak_rss_mb"),
        "cost_usd": totals.get("cost_usd"),
        "complement_dedup": policy.get("complement_dedup"),
        # A/B flags: a policy without them is a build from before the flags, which ran the defaults
        "complement_fallback": policy.get("complement_fallback", FLAG_DEFAULTS["complement_fallback"]) if policy else None,
        "pair_order": policy.get("pair_order", FLAG_DEFAULTS["pair_order"]) if policy else None,
        "top_k": policy.get("top_k", cell.get("top_k")),  # the resolved knobs (build_policy.json, else cell.json)
        "batch_size": policy.get("batch_size", cell.get("batch_size")),
        **speed(usage_doc or {}, build),
        **decision_metrics(decision_scoring),
        "decision_scoring": decision_scoring,  # the whole score_decisions.json (per-op blocks), in the JSON rows
    }


def _cell(value: Any) -> str:
    if value is None or value == []:
        return "-"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}" if abs(value) >= 0.01 or value == 0 else f"{value:.4g}"
    return str(value)


def markdown(rows: list[dict[str, Any]]) -> str:
    head = "| " + " | ".join(name for name, _ in COLUMNS) + " |"
    rule = "|" + "|".join("---" for _ in COLUMNS) + "|"
    body = ["| " + " | ".join(_cell(row.get(key)) for _, key in COLUMNS) + " |" for row in rows]
    legend = (
        "\nHeadline: the relation MCC for fixture versions 1 and 2 (owner decision 2026-10-29), and the mean of the relation and "
        "group MCCs for version 3 and later (owner decision 2026-10-30); headline metric says which. Bind MCC is secondary; "
        "read MCC is a diagnostic; grp MCC is a part of the version 3 headline and a diagnostic in versions 1 and 2. Q_N "
        "(repaired), strict Q_N and every other Q_N column are diagnostics: they are "
        "reported beside the headline and are never a ranking or a headline score. "
        "Q_N (repaired) is the score of the theory as scored after the base repair (drops made to restore consistency). "
        "strict Q_N is 0.0 for a repaired base (base repaired = yes): an inconsistent base theory fails the universe it "
        "describes (spec s9), and the repaired score is shown beside it. Otherwise strict Q_N equals Q_N. "
        "groups dropped% = group drops over the committed groups. A dash (-) means unknown or not applicable. "
        "bind/grp/rel items/s = charged items (retried ones included, torn placeholder rows left out) per second of that "
        "phase's call seconds; call p50/p95 s = nearest-rank percentile of the per-call seconds over all charged calls; "
        "items/call = mean items per call; rel wall s = the relation phase's seconds in build_summary. A call's phase is its "
        "op when every call names one, else its time (midpoint in phase_call_seconds). Speed is - when the mean call takes "
        "under 1 ms. The relation, binding, group and reading MCCs come from the run's score_decisions.json (a dash when it has "
        "none). Relation macro-F1, Brier, ECE and held-out relation MCC are relation-op diagnostics. Each MCC is read from its "
        "op's block, so an old score_decisions.json (headline mcc_mean) and a new one give the same columns.\n"
    )
    return "\n".join([head, rule, *body]) + "\n" + legend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.report", description=__doc__.splitlines()[0])
    parser.add_argument("--runs", nargs="+", required=True, type=Path, help="run directories written by runcell")
    parser.add_argument("--json", type=Path, help="also write the rows as JSON to this path")
    parser.add_argument("--md", type=Path, help="write the markdown table to this path instead of stdout")
    args = parser.parse_args(argv)
    try:
        rows = [run_row(run) for run in args.runs]
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    text = markdown(rows)
    if args.md:
        args.md.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.json:
        args.json.write_text(json.dumps({"runs": rows}, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

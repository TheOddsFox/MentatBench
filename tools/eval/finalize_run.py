"""Finalize a fresh copy of a kept work dir with the code under test, as `build` would, and record the outcome.
MEASUREMENT ONLY: product code is not changed; three functions are wrapped to observe their arguments. With --admit 0 and
--repair-budget-s, the knob check is told to accept the budget (the CLI refuses that combination).

python tools/eval/finalize_run.py SRC_WORK RUN_DIR --fixture-dir FIXTURE_DIR --profile PROFILE --admit 0|1
[--repair-budget-s X] [--caps default|original]
Writes RUN_DIR/{work,submission,finalize_result.json,gold_metrics.json}; stderr (progress lines) goes to the caller's log."""
import argparse
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

from mentat_bench.harness import fixed as F
from mentat_bench.manifest import load_profile
from mentat_bench.seal import ensure_build_id

# (also writes units.json, per-unit facts for analyze.py)
ORIGINAL_CAPS = dict(max_relation_drops=100000, max_relation_drop_frac=0.5, max_group_drops=100000, max_group_drop_frac=1.0)

ap = argparse.ArgumentParser()
ap.add_argument("src")
ap.add_argument("run")
ap.add_argument("--fixture-dir", type=Path, required=True)
ap.add_argument("--admit", type=int, required=True)
ap.add_argument("--repair-budget-s", type=float, default=None)
ap.add_argument("--caps", choices=["default", "original"], default="default")
ap.add_argument("--profile", required=True)
a = ap.parse_args()
run = Path(a.run)
assert not run.exists(), f"{run} exists: one fresh copy per run"
run.mkdir(parents=True)
work, out = run / "work", run / "submission"
shutil.copytree(a.src, work)

seen = {"settle_calls": 0, "admit_units_calls": [], "tiebreak_calls": 0}
_settle, _admit_units, _tiebreak, _check = F._settle_with_engine, F._admit_units, F._pair_tiebreak, F._check_admit_knobs


def settle(units, replay, *args, **kw):  # the final units and replay, for the gold metrics
    seen["settle_calls"] += 1
    seen["units"], seen["replay"] = units, replay
    return _settle(units, replay, *args, **kw)


def admit_units(check, units, ledger, floor, time_left=None, pair_key=None):
    seen["admit_units_calls"].append({"pair_key_given": pair_key is not None,
                                      "relations_with_conf": sum(1 for u in units if u.kind == "relation" and u.conf is not None),
                                      "relations": sum(1 for u in units if u.kind == "relation")})
    return _admit_units(check, units, ledger, floor, time_left, pair_key)


def tiebreak(ids, records):
    seen["tiebreak_calls"] += 1
    return _tiebreak(ids, records)


F._settle_with_engine, F._admit_units, F._pair_tiebreak = settle, admit_units, tiebreak
if not a.admit and a.repair_budget_s is not None:  # measurement override: the CLI refuses --no-admit with a repair budget
    F._check_admit_knobs = lambda admit, lits, floor, budget: _check(admit, lits, floor, None)

caps = ORIGINAL_CAPS if a.caps == "original" else {}
res = {"profile": a.profile, "src": a.src, "fixture": str(a.fixture_dir), "admit": bool(a.admit), "repair_budget_s": a.repair_budget_s, "caps": a.caps,
       "caps_values": caps or "finalize_checkpoint defaults"}
t0 = time.monotonic()
try:
    summary = F.finalize_checkpoint(work, out, load_profile(a.profile), admit=bool(a.admit),
                                    repair_budget_s=a.repair_budget_s, **caps)
    res["finalize_wall_s"] = round(time.monotonic() - t0, 3)
    ensure_build_id(out)
    res.update(exit=0, summary=summary)
except Exception as exc:  # noqa: BLE001 - the outcome is the measurement
    res["finalize_wall_s"] = round(time.monotonic() - t0, 3)
    code = 3 if isinstance(exc, (F.BaseCheckIncomplete, F.BuildIncomplete)) else 5 if isinstance(exc, F.BaseInconsistentError) else 2 if isinstance(exc, ValueError) else 1
    res.update(exit=code, error=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()[-3000:])
res["observed"] = {k: v for k, v in seen.items() if k not in ("units", "replay")}
(run / "finalize_result.json").write_text(json.dumps(res, indent=2, default=str))
print(json.dumps({k: v for k, v in res.items() if k not in ("summary", "traceback")}, default=str), flush=True)

# gold metrics of the retained base (after timing; reads <fixture>/gold, which product code never does)
if res["exit"] == 0 and "units" in seen:
    sys.path.insert(0, str(Path(__file__).parent))
    from gold_oracle import Gold
    units, replay = seen["units"], seen["replay"]
    g = Gold(a.fixture_dir, replay.ids)
    rows = list(replay.relations())
    assert all(units[k].kind in ("relation", "derived") for k in range(len(rows)))
    def kept(u):
        return not u.dropped

    m = {"decisions": 0, "decisions_entailed": 0, "derived": 0, "derived_entailed": 0,
         "asked_decisions_entailed": 0, "asked_derived_entailed": 0}
    for k, row in enumerate(rows):
        u, ent = units[k], g.entailed(row[0], row[1], row[2])
        key = "decisions" if u.kind == "relation" else "derived"
        m[f"asked_{key}_entailed"] += ent
        if kept(u):
            m[key] += 1
            m[f"{key}_entailed"] += ent
    m["decision_precision"] = round(m["decisions_entailed"] / max(1, m["decisions"]), 4)
    m["rows_retained"] = m["decisions"] + m["derived"]
    m["rows_entailed"] = m["decisions_entailed"] + m["derived_entailed"]
    m["row_precision"] = round(m["rows_entailed"] / max(1, m["rows_retained"]), 4)
    grp = [u for u in units[len(rows):] if u.kind not in ("relation", "derived")]
    def gent(u):
        return bool(u.clauses) and all(g.clause_entailed(x, y) for x, y in u.clauses)

    rows_all = {u.row for u in grp}
    rows_kept = {u.row for u in grp} - {u.row for u in grp if u.dropped}
    m.update(group_units=len(grp), group_units_retained=sum(kept(u) for u in grp),
             group_units_retained_2cnf=sum(kept(u) and bool(u.clauses) for u in grp),
             group_units_retained_entailed=sum(kept(u) and gent(u) for u in grp),
             group_units_asked_entailed=sum(gent(u) for u in grp),
             group_rows=len(rows_all), group_rows_retained=len(rows_kept),
             gold_ignored_ops=g.ignored)
    m["group_unit_precision_2cnf"] = round(m["group_units_retained_entailed"] / max(1, m["group_units_retained_2cnf"]), 4)
    (run / "gold_metrics.json").write_text(json.dumps(m, indent=2))
    (run / "units.json").write_text(json.dumps({  # relation rows in log order; group units [row, kind, dropped, 2cnf, entailed]
        "rel_dropped": [int(units[k].dropped) for k in range(len(rows))],
        "rel_floored": [int(units[k].floored) for k in range(len(rows))],
        "groups": [[u.row, u.kind, int(u.dropped), int(bool(u.clauses)), int(gent(u))] for u in grp]}))
    print(json.dumps(m), flush=True)

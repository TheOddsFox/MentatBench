"""MEASUREMENT ONLY (reads <fixture>/gold; product code never does). League-bootstrap 95% intervals for the decision MCCs, Q_N
and the paired gaps of finalized runs of one fixture.

usage: python tools/eval/league_ci.py --fixture DIR --runs RUN [RUN...] [--reference RUN] [--B 2000] [--seed 0] --out JSON [--md MD]
                                      [--legacy-means]
DIR: a fixtures/<name> directory whose stamp is a registered version. RUN: a cell directory (the runcell layout).
A run is refused when its fixture_stamp.json is not the fixture's stamp. A run without score_decisions.json has no decisions
(no --candidates cell: empty, oracle) and gets Q_N only.

Layout by fixture version (the major version of fixture.json; a fixture.json with no version gets the legacy layout). Versions 1 and 2 (owner decision 2026-10-29, "relation-headline/1"):
the headline is the relation MCC, the binding MCC is secondary, and the group MCC, the reading MCC, the held-out relation MCC
and Q_N are diagnostics (groups and readings have known validity problems, docs/history/round-2/headline-change-disclosure.md).
Version 3 and later (owner decision 2026-10-30, "relation-group-headline/1"): the headline is relation_group_mcc, the mean of the
relation and group MCCs over the same draws; the relation and group MCCs are its parts and the binding MCC is secondary, each
with its own interval and gaps; the reading MCC, the held-out relation MCC and Q_N are diagnostics. No layout has a mean of
its diagnostics. --legacy-means (versions 1 and 2 only) also computes the old aggregates (the 4-part mean "headline" and the 3-op mean "mcc_mean_3") so that the
published pre-2026-10-29 CI JSON can be reproduced; they are not scores. What --legacy-means reproduces: the per-run and gap values of docs/history/round-2/ci.json (runs[*].metrics and gaps). Its top-level keys (no roles, layout or legacy_means) are the older shape, so compare values, not the whole file. To check (needs the v2 run directories): python tools/eval/league_ci.py --fixture fixtures/mentat_hard_test --reference d3_27b-q8_0 --legacy-means --out OUT.json --runs followed by all twelve cell directories in one call (runs/20261029-v2-test/<cell> for clef-flash_9b, clef_27b-q8_0, d3_27b-q8_0, d3_9b-q8_0, kev_9b-bf16, laya_latest, nimble_9b-bf16, pplx-decider_27b, empty, gold_view, oracle, rules); then compare every runs[name].metrics[m] and gaps[name][m] (point, lo, hi) with the published file. The check on 2026-10-29 agreed on all 468 values: 156 run and gap metric entries, point, lo and hi each (maximum absolute difference 0).

Items are scored by decision_scoring.load, unchanged. An item's league is the league of its candidate's first outcome id
(gold/meta.jsonl): a binding's id, a relation's a (so the 384 cross-league NONE distractors count on side a; their b-league
dependence is ignored), a group's members[0]; all members of a curated group must share that league, while a reading question takes its proposition's league
(members[0]) only, since its distractors may come from other leagues.
Q_N is score.json's value (scoring.q_n of the family means of per_query, the repaired base; strict Q_N is 0 for a repaired base),
each query's family and league from gold/query_design.jsonl. A family a draw holds no query of gives NaN for that draw's Q_N.

Resampling: B draws of the L leagues with replacement, from random.Random(f"league_ci:{seed}"), the same draws for every run, so
the gaps are paired. The draws sample items and leagues only: the runs are deterministic. A component a draw holds no items for
is NaN for that draw. The components are relations, bindings, groups, and readings only for a run with reading items. Class sets
come from the items. Intervals are the 2.5 and 97.5 percentiles (numpy, linear) of the non-NaN draws. A gap is reference minus
run, per draw.

Self-check. Every unweighted value must equal the run's own score: Q_N from score.json, and each MCC from the per-op blocks
ops.<op>.mcc of score_decisions.json (the same keys in the old and the new layout), and the held-out relation MCC. With
--legacy-means the old headline is checked too; version 3 checks its headline's relation_group_mcc.

Diagnostic runs (gold_view and oracle read gold, runcell.GOLD_READING, and runs without decisions) are never the default reference.
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np

from mentat_bench import corrections, decision_scoring, ir, scoring
from mentat_bench.runcell import GOLD_READING

FAMILIES = scoring.FAMILIES
RELATION, GROUP, BINDING, READING = "decide_relation", "decide_group", "decide_binding", "decide_reading"
OPS = decision_scoring.OPS
COMPONENTS = tuple(decision_scoring.COMPONENT[op] for op in OPS)  # relations, groups, bindings, readings
COMPONENT_OP = {decision_scoring.COMPONENT[op]: op for op in OPS}
LAYOUT = "relation-headline/1"  # fixture versions 1 and 2
LAYOUT_V3 = "relation-group-headline/1"  # fixture version 3 and later
ROLE = {"relations": "primary", "bindings": "secondary", "groups": "diagnostic", "readings": "diagnostic",
        "held_out_relations": "diagnostic", "Q_N": "diagnostic"}
ROLE_V3 = {"relation_group_mcc": "primary", "relations": "part", "groups": "part", "bindings": "secondary",
           "readings": "diagnostic", "held_out_relations": "diagnostic", "Q_N": "diagnostic"}
HEADLINE = {LAYOUT: ("relations", "relation MCC"), LAYOUT_V3: ("relation_group_mcc", "relation and group MCC")}  # (metric, name)
LEGACY = ("headline", "mcc_mean_3")  # the pre-2026-10-29 aggregates: computed only with --legacy-means, never scores
TOL = 1e-9  # the self-check: the unweighted values against the run's own score files


class CIError(ValueError):
    """A refused run or input, or a failed self-check: the CLI prints it and exits 1."""


def _json_file(path: Path) -> dict:
    """A JSON object file of a run: CIError when it is missing, not JSON or not an object."""
    if not path.is_file():
        raise CIError(f"{path} is missing")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CIError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise CIError(f"{path} is not a JSON object")
    return doc


def _number(x, what: str) -> float:
    """x as a float; CIError unless it is a finite number (a bool is not one)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise CIError(f"{what} is {x!r}, not a finite number")
    return float(x)


def read_fixture(fixture: Path) -> dict:
    """The leagues (sorted), the league index of every candidate's qid, and the (family, league) index of every design qid."""
    try:
        meta = {row["id"]: row["league"] for row in ir.read_jsonl(fixture / "gold" / "meta.jsonl")}
        candidates = list(ir.read_jsonl(fixture / "candidates.jsonl"))
        design_rows = list(ir.read_jsonl(fixture / "gold" / "query_design.jsonl"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CIError(f"cannot read the fixture {fixture}: {exc!r}") from exc
    leagues = sorted(set(meta.values()))
    index = {name: i for i, name in enumerate(leagues)}
    item_league = {}
    for cand in candidates:
        if not isinstance(cand, dict) or cand.get("qid") is None:
            raise CIError(f"a candidate row of {fixture} has no qid: {cand!r}")
        qid, op = cand["qid"], cand.get("op")
        if op == RELATION:
            ids = [cand.get("a")]
        elif op == GROUP:
            ids = cand.get("members")
            if cand.get("source") == "reading" and isinstance(ids, list):
                ids = ids[:1]  # a reading question belongs to its proposition's league; its distractors may come from others
        elif op == BINDING:
            ids = [cand.get("id")]
        else:
            raise CIError(f"candidate {qid} has op {op!r}, not one of {list(OPS)}")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise CIError(f"candidate {qid} ({op}) names no outcome id: {ids!r}")
        unknown = [i for i in ids if i not in meta]
        if unknown:
            raise CIError(f"candidate {qid} names outcome ids that gold/meta.jsonl does not have: {unknown}")
        spans = {meta[i] for i in ids}
        if len(spans) != 1:
            raise CIError(f"candidate {qid} spans leagues {sorted(spans)}")
        item_league[qid] = index[spans.pop()]
    design = {}
    for row in design_rows:
        try:
            qid, family, league = row["qid"], row["family"], row["league"]
        except (KeyError, TypeError) as exc:
            raise CIError(f"a query_design row lacks {exc}: {row!r}") from exc
        if family not in FAMILIES or league not in index:
            raise CIError(f"query {qid} has family {family!r} and league {league!r}: not a family, or a "
                          "league gold/meta.jsonl does not have")
        design[qid] = (FAMILIES.index(family), index[league])
    return {"leagues": leagues, "item_league": item_league, "design": design}


def read_run(run: Path, fixture: Path, fx: dict) -> dict:
    """One run's score.json per-league family sums and counts, and its decision items when it has score_decisions.json."""
    score = _json_file(run / "score.json")
    if not isinstance(score.get("per_query"), dict):
        raise CIError(f"{run}/score.json has no per_query object")
    if "Q_N" not in score:
        raise CIError(f"{run}/score.json has no Q_N")
    _number(score["Q_N"], f"{run}/score.json Q_N")
    shape = (len(fx["leagues"]), len(FAMILIES))
    fam_sum, fam_n = np.zeros(shape), np.zeros(shape, dtype=np.int64)
    for qid, utility in score["per_query"].items():
        if qid not in fx["design"]:
            raise CIError(f"{qid} is scored in {run}/score.json but has no row in gold/query_design.jsonl")
        family, league = fx["design"][qid]
        fam_sum[league, family] += _number(utility, f"{run}/score.json utility of {qid}")
        fam_n[league, family] += 1
    adapter = _json_file(run / "cell.json").get("adapter") if (run / "cell.json").is_file() else None
    data = {"name": run.name, "score": score, "fam_sum": fam_sum, "fam_n": fam_n, "items": None, "scored": None,
            "adapter": adapter, "diagnostic": adapter in GOLD_READING}
    if (run / "score_decisions.json").is_file():
        data["scored"] = _json_file(run / "score_decisions.json")
        if not isinstance(data["scored"].get("ops"), dict):
            raise CIError(f"{run}/score_decisions.json has no ops")
        try:
            data["items"], _ = decision_scoring.load(fixture / "gold" / "suite_labels.jsonl", fixture / "candidates.jsonl",
                                                     run / "submission.build" / "decisions.jsonl")
        except (OSError, ValueError) as exc:
            raise CIError(f"{run}: its decisions cannot be scored: {exc}") from exc
        unknown = sorted({it["qid"] for it in data["items"]} - fx["item_league"].keys())
        if unknown:
            raise CIError(f"{run}: decisions for qids with no candidate: {unknown[:5]}")
    else:
        data["diagnostic"] = True  # no decisions: Q_N only, never the reference
    return data


def draws(B: int, seed: int, L: int) -> np.ndarray:
    """B draws of the L leagues with replacement, as league multiplicities (B, L)."""
    rng = random.Random(f"league_ci:{seed}")
    picks = [[rng.randrange(L) for _ in range(L)] for _ in range(B)]
    return np.array([np.bincount(pick, minlength=L) for pick in picks], dtype=np.int64)


def _classes(runs: list[dict]) -> dict[str, list[str]]:
    """Per op, the sorted truth and predicted classes of every run's items (so one confusion shape serves all runs)."""
    out = {op: set() for op in OPS}
    for run in runs:
        for it in run["items"] or ():
            out[it["op"]].update((it["truth"], it["pred"]))
    return {op: sorted(classes) for op, classes in out.items()}


def _cube(items: list[dict], classes: list[str], fx: dict) -> np.ndarray:
    """Confusion counts (league, truth, predicted) of the items."""
    index = {c: i for i, c in enumerate(classes)}
    cube = np.zeros((len(fx["leagues"]), len(classes), len(classes)), dtype=np.int64)
    for it in items:
        cube[fx["item_league"][it["qid"]], index[it["truth"]], index[it["pred"]]] += 1
    return cube


def _decision_cubes(items: list[dict], classes: dict[str, list[str]], fx: dict) -> dict[str, np.ndarray]:
    cubes = {op: _cube([it for it in items if it["op"] == op], classes[op], fx) for op in OPS}
    held_out = [it for it in items if it["op"] == RELATION and it["held_out"]]
    cubes["held_out"] = _cube(held_out, classes[RELATION], fx)
    return cubes


def _mcc(cube: np.ndarray, W: np.ndarray) -> np.ndarray:
    """decision_scoring.mcc of the summed confusion of each draw (0 for a constant marginal, NaN when the draw has no items)."""
    L, K = cube.shape[0], cube.shape[1]
    S = (W @ cube.reshape(L, K * K)).reshape(len(W), K, K)
    n = S.sum(axis=(1, 2))
    hits = np.einsum("bkk->b", S)
    truth, pred = S.sum(axis=2), S.sum(axis=1)
    cov = hits * n - (truth * pred).sum(axis=1)
    var_t = n * n - (truth * truth).sum(axis=1)
    var_p = n * n - (pred * pred).sum(axis=1)
    ok = (var_t > 0) & (var_p > 0)
    root = np.sqrt(np.where(ok, var_t.astype(float) * var_p, 1.0))
    return np.where(n > 0, np.where(ok, cov / root, 0.0), np.nan)


def _q_n(run: dict, W: np.ndarray) -> np.ndarray:
    """scoring.q_n of each draw's family means. A family with no query in the workload is 0, as score_run scores it; a family
    the draw holds no query of is NaN for that draw."""
    total, count = W @ run["fam_sum"], W @ run["fam_n"]
    means = {}
    for i, family in enumerate(FAMILIES):
        if run["fam_n"][:, i].sum() == 0:
            means[family] = np.zeros(len(W))
        else:
            means[family] = np.divide(total[:, i], count[:, i], out=np.full(len(W), np.nan), where=count[:, i] > 0)
    return scoring.q_n(means)


def _headline(components: list[np.ndarray]) -> np.ndarray:
    """decision_scoring.score's headline: the mean of the MCCs of the ops that have items in the draw."""
    stack = np.vstack(components)
    present = ~np.isnan(stack)
    count = present.sum(axis=0)
    total = np.where(present, stack, 0.0).sum(axis=0)
    return np.where(count > 0, total / np.maximum(count, 1), np.nan)


def _components(run: dict) -> list[str]:
    """The components of a run's decisions: relations, groups and bindings, and readings only with reading items."""
    readings = run["items"] is not None and any(it["op"] == READING for it in run["items"])
    return [name for name in COMPONENTS if name != "readings" or readings]


def _draw_values(run: dict, cubes: dict | None, W: np.ndarray, legacy: bool = False, v3: bool = False) -> dict[str, np.ndarray]:
    """Per-draw values of one run: Q_N, and with decisions each component MCC and the held-out relation MCC. Version 3 adds
    the relation_group_mcc headline (the mean of the relation and group MCCs; NaN when either is). With legacy, the old
    aggregates too (the 4-part mean, and the 3-op mean with readings)."""
    values = {"Q_N": _q_n(run, W)}
    if cubes is not None:
        components = _components(run)
        for name in components:
            values[name] = _mcc(cubes[COMPONENT_OP[name]], W)
        values["held_out_relations"] = _mcc(cubes["held_out"], W)
        if v3:
            values["relation_group_mcc"] = (values["relations"] + values["groups"]) / 2  # NaN propagates: null when a part is
        if legacy:
            values["headline"] = _headline([values[name] for name in components])
            if "readings" in components:
                values["mcc_mean_3"] = _headline([values[name] for name in components if name != "readings"])
    return values


def _point(x: np.ndarray) -> float | None:
    return None if np.isnan(x[0]) else float(x[0])


def _interval(values: np.ndarray) -> tuple[float | None, float | None]:
    ok = values[~np.isnan(values)]
    if ok.size == 0:
        return None, None
    lo, hi = np.percentile(ok, [2.5, 97.5])
    return float(lo), float(hi)


def _same(ours: float | None, theirs: float | None) -> bool:
    if ours is None or theirs is None:
        return ours is None and theirs is None
    return abs(ours - theirs) <= TOL


def _self_check(name: str, point: dict, data: dict, legacy: bool = False, v3: bool = False) -> None:
    """The unweighted values must equal the run's own score.json Q_N and its per-op MCCs (ops.<op>.mcc, the same in both
    layouts), and the held-out relation MCC. With legacy, the old headline too; with v3, the headline's relation_group_mcc."""
    expect = {"Q_N": data["score"]["Q_N"]}
    if data["scored"] is not None:
        scored = data["scored"]
        try:
            ops = scored["ops"]
            expect.update({component: ops[COMPONENT_OP[component]]["mcc"] for component in _components(data)})
            expect["held_out_relations"] = ops[RELATION]["by_split"]["held_out"]["mcc"]
            if v3:
                expect["relation_group_mcc"] = scored["headline"]["relation_group_mcc"]
            if legacy:
                expect["headline"] = scored["headline"]["mcc_mean"]
                if "mcc_mean_3" in scored["headline"]:
                    expect["mcc_mean_3"] = scored["headline"]["mcc_mean_3"]
        except (KeyError, TypeError) as exc:
            raise CIError(f"{name}: score_decisions.json lacks {exc}") from exc
    for key, want in expect.items():
        if not _same(point.get(key), want):
            raise CIError(f"{name}: {key} is {point.get(key)} from the items and {want} in its score file")


def _round(x):
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    return round(x, 6) if isinstance(x, float) else x


def run_ci(fixture: Path, runs: list[Path], stamp: dict, reference: str | None = None, B: int = 2000, seed: int = 0,
            legacy: bool = False) -> dict:
    """The intervals of every run, and the gaps (reference minus run) of every other run, as a rounded dict."""
    if B < 1:
        raise CIError(f"B must be at least 1, got {B}")
    try:
        version = decision_scoring.fixture_version(fixture)
    except (OSError, ValueError) as exc:
        raise CIError(f"cannot read the fixture version of {fixture}: {exc}") from exc
    v3 = decision_scoring.relation_group(version)
    if v3 and legacy:
        raise CIError(f"--legacy-means reproduces the version 1 and 2 aggregates; {fixture} is version {version}")
    fx = read_fixture(fixture)
    names = [run.name for run in runs]
    if len(set(names)) != len(names):
        raise CIError(f"run names must be distinct: {names}")
    for run in runs:
        recorded = _json_file(run / "fixture_stamp.json")
        if recorded.get("content_hash") != stamp["content_hash"]:
            raise CIError(f"{run} was not run on this fixture: its fixture_stamp.json is {recorded}, the fixture is {stamp}")
    data = [read_run(run, fixture, fx) for run in runs]
    classes = _classes(data)
    for run in data:
        run["cubes"] = None if run["items"] is None else _decision_cubes(run["items"], classes, fx)
    L = len(fx["leagues"])
    ones, W = np.ones((1, L), dtype=np.int64), draws(B, seed, L)
    full = {run["name"]: _draw_values(run, run["cubes"], ones, legacy, v3) for run in data}
    boot = {run["name"]: _draw_values(run, run["cubes"], W, legacy, v3) for run in data}
    point = {}
    for run in data:
        point[run["name"]] = {m: _point(v) for m, v in full[run["name"]].items()}
        _self_check(run["name"], point[run["name"]], run, legacy, v3)
    summary = {}
    for run in data:
        name, cubes = run["name"], run["cubes"]
        metrics = {}
        for m in boot[name]:
            lo, hi = _interval(boot[name][m])
            metrics[m] = {"point": point[name][m], "lo": lo, "hi": hi}
        items = None
        if cubes is not None:
            items = {name_: int(cubes[COMPONENT_OP[name_]].sum()) for name_ in _components(run)}
            items["held_out_relations"] = int(cubes["held_out"].sum())
        summary[name] = {"decisions": cubes is not None, "adapter": run["adapter"], "diagnostic": run["diagnostic"],
                         "items": items, "metrics": metrics}
    head_key = HEADLINE[LAYOUT_V3 if v3 else LAYOUT][0]
    eligible = [name for name in names if not summary[name]["diagnostic"] and point[name].get(head_key) is not None]
    if reference is None:
        if not eligible:
            raise CIError("no run can be the default reference (a run with decisions that does not read gold): give --reference")
        reference = max(eligible, key=lambda name: point[name][head_key])
    else:
        reference = Path(reference).name
        if reference not in names:
            raise CIError(f"reference {reference} is not one of the runs {names}")
    gaps = {}
    for name in names:
        if name == reference:
            continue
        gaps[name] = {}
        for m in boot[reference]:
            if m in boot[name]:
                diff = boot[reference][m] - boot[name][m]
                ref_point, run_point = point[reference][m], point[name][m]
                lo, hi = _interval(diff)
                gaps[name][m] = {"point": None if ref_point is None or run_point is None else ref_point - run_point,
                                 "lo": lo, "hi": hi}
    roles = dict(ROLE_V3 if v3 else ROLE)
    if legacy:
        roles.update({m: "legacy" for m in LEGACY})
    doc = {"fixture": fixture.name, "stamp": stamp, "leagues": L, "B": B, "seed": seed, "q_n_kind": "repaired",
           "interval": "95% percentile (numpy, linear) of the draws", "layout": LAYOUT_V3 if v3 else LAYOUT, "legacy_means": legacy,
           "roles": roles, "reference": reference, "runs": summary, "gaps": gaps}
    return _round(doc)


def _cell(m: dict | None, digits: int) -> str:
    if m is None:
        return "-"
    if m["point"] is None:
        return "n/a"
    return f"{m['point']:.{digits}f} [{m['lo']:.{digits}f}, {m['hi']:.{digits}f}]"


def _label(name: str, run: dict, reference: str) -> str:
    label = name if name != reference else f"{name} (reference)"
    return label + (" (diagnostic)" if run["diagnostic"] else "")


def markdown(doc: dict) -> str:
    """Headline table (the headline, its parts in version 3, the binding MCC, with gaps), then the diagnostics (never
    aggregated), then, with --legacy-means, the old aggregates."""
    ref = doc["reference"]
    runs = doc["runs"]
    head_key = HEADLINE[doc["layout"]][0]
    if doc["layout"] == LAYOUT_V3:
        intro = "Headline: the mean of relation and group MCC. Secondary: binding MCC. The diagnostics below are not scores."
        columns = [("Relation and group MCC (headline)", head_key, 3), ("Relation MCC (part)", "relations", 3),
                   ("Group MCC (part)", "groups", 3), ("Binding MCC (secondary)", "bindings", 3)]
        gap_name = "Headline gap"
        diagnostics = [("Reading MCC", "readings", 3), ("Held-out relation MCC", "held_out_relations", 3)]
    else:
        intro = "Headline: relation MCC. Secondary: binding MCC. The diagnostics below are not scores."
        columns = [("Relation MCC (headline)", "relations", 3), ("Binding MCC (secondary)", "bindings", 3)]
        gap_name = "Relation gap"
        diagnostics = [("Group MCC", "groups", 3), ("Reading MCC", "readings", 3), ("Held-out relation MCC", "held_out_relations", 3)]
    diagnostics.append(("Q_N (repaired base)", "Q_N", 2))
    lines = [f"Point [95% lower, upper] over B={doc['B']} league draws (seed {doc['seed']}), {doc['leagues']} leagues. "
             f"{intro}", "",
             "| " + " | ".join(["Run", *(name for name, _, _ in columns), f"{gap_name} (reference minus run)",
                                "Binding gap (reference minus run)"]) + " |", "|" + "---|" * (len(columns) + 3)]
    for name, run in runs.items():
        m, g = run["metrics"], doc["gaps"].get(name, {})
        gaps = ["reference" if name == ref else _cell(g.get(key), 3) for key in (head_key, "bindings")]
        lines.append("| " + " | ".join([_label(name, run, ref), *(_cell(m.get(key), digits) for _, key, digits in columns),
                                          *gaps]) + " |")
    lines += ["", "## Diagnostics (not scores: no aggregate includes these)", "",
              "| " + " | ".join(["Run", *(name for name, _, _ in diagnostics), "Q_N gap (reference minus run)"]) + " |",
              "|" + "---|" * (len(diagnostics) + 2)]
    for name, run in runs.items():
        m, g = run["metrics"], doc["gaps"].get(name, {})
        q_gap = "reference" if name == ref else _cell(g.get("Q_N"), 2)
        lines.append("| " + " | ".join([_label(name, run, ref), *(_cell(m.get(key), digits) for _, key, digits in diagnostics),
                                          q_gap]) + " |")
    if doc.get("legacy_means"):
        lines += ["", "## Legacy aggregates (old layout: not scores; they reproduce the pre-2026-10-29 CI JSONs)", "",
                  "| Run | 4-part mean (legacy) | 3-op mean (legacy) |", "|---|---|---|"]
        for name, run in runs.items():
            m = run["metrics"]
            lines.append("| " + " | ".join([_label(name, run, ref), _cell(m.get("headline"), 3),
                                              _cell(m.get("mcc_mean_3"), 3)]) + " |")
    return "\n".join(lines) + "\n"


def _stamp(fixture: Path, sealed: str | None) -> dict:
    """The registry stamp of a registered fixture, or, for a sealed split, its name and content hash once that hash matches the
    commitment (a sealed split has no registry entry; its commitment is published in docs/release.md)."""
    if sealed is None:
        return corrections.stamp_for(fixture)
    content_hash = corrections.content_hash(corrections.content_hashes(fixture))
    if content_hash != sealed:
        raise CIError(f"{fixture} has content hash {content_hash}, not the sealed commitment {sealed}")
    return {"name": fixture.name, "sealed": True, "content_hash": content_hash}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", type=Path, required=True)
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--reference", help="the run the gaps are taken from (reference minus run); default the best point relation "
                                        "MCC among runs that do not read gold")
    ap.add_argument("--B", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--legacy-means", action="store_true",
                    help="also compute the old 4-part and 3-op means (not scores; for the pre-2026-10-29 CI JSONs)")
    ap.add_argument("--sealed", metavar="CONTENT_HASH",
                    help="the fixture is a sealed split (not in the registry, e.g. the private test split): accept it only if "
                         "its content hash equals this published commitment")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--md", type=Path)
    a = ap.parse_args(argv)
    try:
        doc = run_ci(a.fixture, a.runs, _stamp(a.fixture, a.sealed), a.reference, a.B, a.seed, a.legacy_means)
    except (CIError, corrections.CorrectionError) as exc:
        print(f"league_ci: {exc}", file=sys.stderr)
        return 1
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    if a.md:
        a.md.parent.mkdir(parents=True, exist_ok=True)
        a.md.write_text(markdown(doc), encoding="utf-8", newline="\n")
    head_key, head_name = HEADLINE[doc["layout"]]
    print(f"reference {doc['reference']}; " + "; ".join(
        f"{name} {head_name} {_cell(run['metrics'].get(head_key), 3)}" for name, run in doc["runs"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

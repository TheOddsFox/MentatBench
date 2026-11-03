"""MEASUREMENT ONLY (reads committed v0.2.0 results, the v0.1.0 predictions and fixtures/mentat_hard_test; never runs/). The v0.2.0
analysis of the Mentat-Hard test results: a new measurement on Ollama 0.40.13. It changes no score.

It reuses the v0.1.1 analysis helpers of tools/eval/release_analysis.py (RA: the cubes, the bootstrap draws, the pairwise and
Holm comparisons, the rank intervals, the leave-one-league-out check, the weighting, the binding detection, the consistency
score, the depth table, the cost table and the repeat-run comparison). The item audit is not re-run for v0.2.0 (it is the
v0.1.1 audit: results/v0.1.1/analysis.json), so item_audit is null here.

Every interval is the league bootstrap of tools/eval/league_ci.py, B = 20000 draws from random.Random("league_ci:0"). The first
2000 are the draws the leaderboard's intervals use, and the self-check requires them to reproduce those intervals and the rank
intervals in results/v0.2.0/leaderboard.json (which build_bundle.py computes with the same functions).

usage, from the repo root:
  python tools/eval/release_analysis_v020.py [--out JSON] [--readme MD] [--check]
    --out      results/v0.2.0/analysis.json (default)
    --readme   the README whose analysis section (between the analysis markers) is replaced; not written unless given
    --check    rebuild in memory and compare the bytes with the committed file; exit 1 on any difference
A failed self-check or input stops the script with exit 1 and a message, and writes nothing.
"""

import argparse
import gzip
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools" / "eval"))

from mentat_bench import corrections, decision_scoring as DS  # noqa: E402
import league_ci as LC  # noqa: E402
import release_analysis as RA  # noqa: E402

RELEASE, BASED_ON = "v0.2.0", "v0.1.0"
V020 = ROOT / "results" / "v0.2.0"
V010 = ROOT / "results" / "v0.1.0"
LEADERBOARD = V020 / "leaderboard.json"
ANALYSIS = V020 / "analysis.json"
REPEATS = V020 / "repeats"
FIXTURE = ROOT / "fixtures" / "mentat_hard_test"
OLLAMA_OLD, OLLAMA_NEW = "0.40.11", "0.40.13"
HARDWARE = "2× NVIDIA RTX A6000 (48 GB), Ollama 0.40.13 (CUDA), one server per GPU, OLLAMA_NUM_PARALLEL=2"
ITEMS = 10716  # the scored items of the fixture (every prediction file has one row per item)
REPEAT_NOTES = {
    "note_protocol": "Diagnostic only: the leaderboard headline is the r1 run; r2 is a run-to-run repeat of r1 on the same machine, "
                     "engine and command (Ollama 0.40.13, both A6000s).",
    "note_identical": "identical is the share of the 10,716 prediction rows whose answer equals r1's; 1 means every answer is the same. "
                      "identical_decisions is true only when the decisions file is byte-identical, which also needs the same "
                      "probabilities. Equal answers with different probabilities are probability-only differences: the answers agree, "
                      "the probabilities move by a small amount, and the decisions file is not byte-identical. identical_decisions "
                      "implies identical_labels.",
    "note_batching": "The r1-versus-r2 differences may come from batching (OLLAMA_NUM_PARALLEL=2). This is a hypothesis; it is not tested.",
}


def _family(n: int) -> int:
    return n * (n - 1) // 2


def _version_shift(models: list[str], data: dict, bundle, version: int) -> tuple[dict, dict]:
    """Per model with a v0.1.0 run (the 15 pre-registered models): the decision flips against the v0.1.0 predictions (the share of
    the 10,716 rows whose label differs) and the headline change. Returns ({model: block}, {v0.1.0 file: sha256})."""
    out, inputs = {}, {}
    for m in models:
        path = V010 / "predictions" / f"{m}.jsonl.gz"
        if not path.is_file():
            continue
        blob = path.read_bytes()
        inputs[RA._rel(path)] = RA._sha(blob)
        old = {RA._pred_key(r): r["pred"] for r in (json.loads(x) for x in gzip.decompress(blob).decode("utf-8").splitlines())}
        new = {RA._pred_key(r): r["pred"] for r in data[m]["rows"]}
        RA.check(old.keys() == new.keys() and len(new) == ITEMS, f"{m}: its rows do not match the v0.1.0 rows")
        flips = sum(old[k] != new[k] for k in new)
        head_old = bundle.rescore(blob, version)["headline"]["relation_group_mcc"]
        head_new = data[m]["scored"]["headline"]["relation_group_mcc"]
        RA.check(data[m]["cell"].get("version_shift") is None or data[m]["cell"]["version_shift"]["flips"] == flips,
                 f"{m}: the cell's version-shift flips differ from the predictions")
        out[m] = {"delta": head_new - head_old, "flip_rate": flips / ITEMS}
    return out, inputs


def _robustness(repeats: dict, models: list[str], shift: dict) -> dict:
    """The robustness block of v0.2.0: no shuffle or Vulkan pass (not run for this release); the run-to-run repeat (r2) of each
    model that has one; and the version shift from v0.1.0 (Ollama 0.40.11) to this run (0.40.13) for the 15 pre-registered models."""
    per = {}
    for m in models:
        run_to_run = None
        if "r2" in repeats.get(m, {}):
            r2 = repeats[m]["r2"]
            run_to_run = {"max_abs_delta": abs(r2["delta_vs_published"]), "max_flip_rate": r2["flip_rate"]}
        per[m] = {"shuffle": None, "vulkan": None, "run_to_run": run_to_run, "version_shift": shift.get(m)}
    runs = [e["run_to_run"] for e in per.values() if e["run_to_run"] is not None]
    deltas = [abs(e["version_shift"]["delta"]) for e in per.values() if e["version_shift"] is not None]
    summary = {
        "presentation": None, "presentation_matched": None, "backend": None,
        "run_to_run": {"n_models": len(runs), "max_abs_delta": max((r["max_abs_delta"] for r in runs), default=0.0)},
        "version_shift": {"n_models": len(deltas), "median_abs_delta": float(np.median(deltas)) if deltas else 0.0,
                          "max_abs_delta": max(deltas, default=0.0), "from": {"release": BASED_ON, "ollama": OLLAMA_OLD},
                          "to": {"ollama": OLLAMA_NEW}},
    }
    return {"models": per, "summary": summary}


def build() -> tuple[dict, bytes]:
    """Every check and every output, in memory: (analysis document, the leaderboard bytes it pins)."""
    lb_bytes = LEADERBOARD.read_bytes()
    lb = json.loads(lb_bytes.decode("utf-8"))
    check = RA.check
    fx = LC.read_fixture(FIXTURE)
    leagues = fx["leagues"]
    L = len(leagues)
    check(L == 18, f"the fixture has {L} leagues, the analysis is for 18")
    check(lb["release"] == RELEASE, f"the leaderboard is {lb['release']}, not {RELEASE}")
    content_hash = corrections.content_hash(corrections.content_hashes(FIXTURE))
    check(lb["provenance"]["fixture"]["content_hash"] == content_hash, "the leaderboard is for another fixture")
    runs = RA._runs(lb)
    models = list(lb["ranking"])
    check(len(models) == 17, f"{len(models)} models in the leaderboard, expected 17")
    bundle = RA._bundle()
    version = DS.fixture_version(FIXTURE)
    W = LC.draws(RA.B, RA.SEED, L)
    ones = np.ones((1, L), dtype=np.int64)
    WLOO = np.ones((L, L), dtype=np.int64) - np.eye(L, dtype=np.int64)
    data, rows_of, predictions = {}, {}, {}
    for rid, entry in runs.items():
        path = ROOT / entry["predictions"]
        blob = path.read_bytes()
        predictions[RA._rel(path)] = RA._sha(blob)
        rows = [json.loads(line) for line in gzip.decompress(blob).decode("utf-8").splitlines()]
        rows_of[rid] = rows
        cubes = {fam: RA._op_cube(rows, RA.OP_OF[fam], fx) for fam in ("relation", "group")}
        mcc = {fam: (float(LC._mcc(cubes[fam], ones)[0]), RA._mcc_draws(cubes[fam], W)) for fam in cubes}
        mcc["headline"] = ((mcc["relation"][0] + mcc["group"][0]) / 2, (mcc["relation"][1] + mcc["group"][1]) / 2)
        loo = {fam: LC._mcc(cubes[fam], WLOO) for fam in cubes}
        loo["headline"] = (loo["relation"] + loo["group"]) / 2
        corr, n = RA._depth_counts(rows, fx)
        cell = json.loads((ROOT / entry["cell"]).read_text(encoding="utf-8"))
        data[rid] = {"entry": entry, "rows": rows, "mcc": mcc, "loo": loo, "corr": corr, "n": n,
                     "scored": bundle.rescore(blob, version), "cell": cell}
    first_2000 = RA._self_check(data, None, W, L, models)
    V = {fam: np.vstack([data[m]["mcc"][fam][1] for m in models]) for fam in RA.FAMILIES}
    P = {fam: [data[m]["mcc"][fam][0] for m in models] for fam in RA.FAMILIES}
    pairwise = {fam: RA._pairwise(models, V[fam], P[fam]) for fam in RA.FAMILIES}
    ranks = {fam: RA._ranks(models, V[fam], P[fam]) for fam in RA.FAMILIES}
    for m in models:  # the rank intervals of build_bundle.py (the leaderboard) are these, computed the same way
        iv = next(e for e in lb["models"] if e["id"] == m)["rank_interval"]
        got = ranks["headline"][m]
        check([got["ci95"][0], got["ci95"][1], got["median"]] == [iv["lo"], iv["hi"], iv["median"]],
              f"{m}: rank interval {got} differs from the leaderboard {iv}")
    lolo = RA._lolo(models, data, P, pairwise, leagues)
    consistency = RA._consistency(rows_of, fx, W)
    headline = {m: data[m]["mcc"]["headline"][0] for m in models}
    repeats = RA._repeats(REPEATS, rows_of, headline, bundle, version, notes=REPEAT_NOTES, bytes_imply_labels=True,
                          verify_prediction_hashes=True)
    shift, v010_inputs = _version_shift(models, data, bundle, version)
    robustness = _robustness(repeats, models, shift)
    weighting = RA._weighting(models, V, P, pairwise["headline"], ranks["headline"])
    binding = {rid: RA._binding_run(data[rid]["rows"], data[rid]["entry"], fx, W) for rid in runs}
    cost = RA._cost(models, {m: data[m]["entry"] for m in models}, HARDWARE)
    kinds = sorted({DS._kind(r[k]) for rows in rows_of.values() for r in rows if r["op"] == "decide_group" for k in ("gold", "pred")})
    relation, group, calibration, counts = {}, {}, {}, {}
    for rid in runs:
        rel = [r for r in data[rid]["rows"] if r["op"] == "decide_relation"]
        matrix = [[0] * 5 for _ in RA.RELATIONS]
        for r in rel:
            if r["pred"] != RA.MISSING:
                check(r["pred"] in RA.RELATIONS, f"{rid}: relation prediction {r['pred']!r}")
                matrix[RA.RELATIONS.index(r["gold"])][RA.RELATIONS.index(r["pred"])] += 1
        relation[rid] = {"matrix": matrix, "missing": sum(r["pred"] == RA.MISSING for r in rel),
                         "abstained": sum(bool(r.get("abstained")) for r in rel)}
        gmatrix = [[0] * len(kinds) for _ in kinds]
        for r in data[rid]["rows"]:
            if r["op"] == "decide_group":
                gmatrix[kinds.index(DS._kind(r["gold"]))][kinds.index(DS._kind(r["pred"]))] += 1
        group[rid] = {"matrix": gmatrix}
        ops = data[rid]["cell"]["score_decisions"]["ops"]
        calibration[rid] = {op: {"brier": ops[op]["brier"]["value"], "ece": ops[op]["ece"]["value"],
                                 "bins": ops[op]["ece"]["bins"]} for op in ("decide_relation", "decide_binding")}
        calibration[rid].update({"decide_group": {"probabilities": False}, "decide_reading": {"probabilities": False}})
        counts[rid] = (data[rid]["corr"], data[rid]["n"])
    n_items = data[models[0]]["n"]
    depth = {"buckets": list(RA.DEPTH_BUCKETS), "runs": RA._depth_block(counts, W),
             "leagues_by_bucket": {b: int((n_items[:, i] > 0).sum()) for i, b in enumerate(RA.DEPTH_BUCKETS)}}
    n4 = n_items[:, RA.DEPTH_BUCKETS.index("4+")]
    family = _family(len(models))
    doc = {
        "schema": RA.SCHEMA,
        "release": RELEASE,
        "based_on": {"release": RELEASE, "leaderboard": {"path": RA._rel(LEADERBOARD), "sha256": RA._sha(lb_bytes)},
                     # the site's predictions rule names results/<release>/predictions and results/<record_from>/predictions only;
                     # the r2 repeat predictions are read by the repeat block (hashes checked there), not listed here
                     "predictions": dict(sorted({**predictions, **v010_inputs}.items())),
                     "fixture": {"name": "mentat_hard_test", "revision": "3.1", "content_hash": content_hash}},
        "method": {
            "bootstrap": {"unit": "league", "leagues": L, "B": RA.B, "seed": RA.SEED, "rng": f"league_ci:{RA.SEED}"},
            "first_2000_equal_published": first_2000,
            "b_comparison": "the leaderboard's ci95 are B = 2000 intervals (their draws are the first 2000 of this module's B = 20000); "
                            "the rank, pairwise and depth intervals and the p-values are the B = 20000 paired draws",
            "uncertainty": "league resampling of the one fixture is the only uncertainty source; one deterministic run per model",
            "interval": "95% percentile interval (numpy, linear) of the valid draws",
            "p_value": "per pair, delta = a - b per draw: p = min(1, 2 * (1 + min(#delta<=0, #delta>=0)) / (1 + B_valid))",
            "correction": "holm",
            "family": "per metric: headline, relation, group; each family is corrected separately, not against the others",
            "family_size": family,
            "bootstrap_draws": RA.B,
            "published_draws": RA.PUBLISHED_B,
            "leagues": L,
            "seed": RA.SEED,
            "degenerate": {"min_n": RA.MIN_N, "min_valid_share": RA.MIN_VALID_SHARE},
            "cost": "decisions is the cell's decision count (one per decision item; a reading's base and alt rows count once); "
                    "decisions_per_s is decisions over the leaderboard's build_wall_s",
            "resolved_pairs": {fam: sum(pr["resolved"] for pr in pairwise[fam]) for fam in RA.FAMILIES},
            "alpha": RA.ALPHA,
            "resolved": "Holm-adjusted p <= alpha",
            "diff": "diff = a minus b (point and draws); the README shows higher minus lower",
            "higher": "the model with the larger point estimate when resolved; null when not resolved",
            "lolo": lolo,
            "rank": "rank is the point rank among the 17 models; ci95 is the 2.5% (method lower) to 97.5% (method higher) range "
                    "of the per-draw rank; median by method nearest",
            "headline": "relation_group_mcc: the mean of the relation and group MCC over the same draws",
            "item_audit": "not re-run for v0.2.0: the v0.1.1 item audit (results/v0.1.1/analysis.json) is the audit of the same "
                          "test set and the same fixture",
            "depth_4plus_items": int(n4.sum()),
            "depth_4plus_leagues": int((n4 > 0).sum()),
            "runs": "the 17 models in headline rank order, then the baselines with decisions (heuristic, lexical, embedding, "
                    "gold_view, hf)",
            "repeats": "r2 is a run-to-run repeat of r1 (the headline run); identical, delta_vs_published and flip_rate are computed "
                       "over the 10,716 prediction rows and the r1 headline",
            "version_shift": f"the 15 pre-registered models against their v0.1.0 runs on Ollama {OLLAMA_OLD}: the headline change and "
                             "the share of the 10,716 rows whose label differs; the Kev models have no v0.1.0 run",
        },
        "models": models,
        "runs": list(runs),
        "pairwise": pairwise,
        "rank_intervals": ranks,
        "depth": depth,
        "confusion": {"relation": {"classes": list(RA.RELATIONS), "rows": "gold", "columns": "predicted", "runs": relation},
                      "group": {"classes": kinds, "rows": "gold kind", "columns": "predicted kind", "runs": group}},
        "calibration": calibration,
        "consistency": consistency,
        "item_audit": None,
        "repeats": repeats,
        "binding_detection": {"definition": RA.BINDING_DEFINITION, "runs": binding},
        "weighting": weighting,
        "robustness": robustness,
        "cost": cost,
    }
    return RA._rounded(doc), lb_bytes


def markdown(doc: dict, names: dict) -> str:
    """The analysis section of results/v0.2.0/README.md, with its markers, from the analysis document."""
    lines = [RA.START, "", "### Analysis (v0.2.0, a new measurement on Ollama 0.40.13)", "",
             "Every table in this section is computed by `tools/eval/release_analysis_v020.py` from the committed v0.2.0 predictions, "
             "cells and leaderboard, and the committed v0.1.0 predictions for the version shift. The numbers are in "
             "[analysis.json](analysis.json). No score is changed by the analysis.", "",
             "The leaderboard's intervals are league-bootstrap intervals (B = 2000). The rank, pairwise and depth intervals and the "
             "p-values here are the paired draws of the same resampling at B = 20000 (the first 2000 are the leaderboard's draws). "
             "Uncertainty comes only from resampling the 18 leagues of one fixture: each model has one deterministic run per "
             "measurement, and the three metric families are corrected separately.", ""]
    lines += RA._rank_lines(doc) + [""] + RA._pair_lines(doc)
    lines += RA._weighting_lines(doc, names)
    lines += RA._depth_lines(doc) + [""] + RA._consistency_lines(doc) + [""] + RA._calibration_lines(doc)
    lines += RA._binding_lines(doc)
    lines += _robustness_lines(doc, names) + RA._cost_lines(doc) + [RA.END]
    return "\n".join(lines)


def _robustness_lines(doc: dict, names: dict) -> list[str]:
    rob = doc["robustness"]
    s = rob["summary"]
    lines = ["#### Robustness: run to run and version shift", "",
             "Run to run is the r2 repeat against the r1 headline run (one repeat per model). The version shift is the change "
             f"from the v0.1.0 run (Ollama {OLLAMA_OLD}) to this run (Ollama {OLLAMA_NEW}) for the 15 pre-registered models; the Kev "
             "models have no v0.1.0 run. Flip rate is the share of the 10,716 decision rows whose label differs.", "",
             "| Model | Run to run max abs delta | Run to run flip rate | Version shift delta | Version shift flip rate |",
             "|---|---|---|---|---|"]
    for m in doc["models"]:
        e = rob["models"][m]
        run, shift = e["run_to_run"], e["version_shift"]
        run_delta = "n/a" if run is None else f"{run['max_abs_delta']:.4f}"
        lines.append(f"| {RA._label(m, doc)} | {run_delta} | "
                     f"{'n/a' if run is None else RA._pct(run['max_flip_rate'])} | "
                     f"{'n/a' if shift is None else RA._signed(shift['delta'])} | "
                     f"{'n/a' if shift is None else RA._pct(shift['flip_rate'])} |")
    v = s["version_shift"]
    lines += ["", f"Version shift: {v['n_models']} models, median absolute headline change {v['median_abs_delta']:.4f}, largest "
              f"{v['max_abs_delta']:.4f}.",
              f"Run to run: {s['run_to_run']['n_models']} models with an r2 repeat, largest absolute headline change "
              f"{s['run_to_run']['max_abs_delta']:.4f}.", ""]
    return lines + [f"- {text}" for key, text in doc["repeats"].items() if key.startswith("note_")] + [""]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ANALYSIS)
    ap.add_argument("--readme", type=Path)
    ap.add_argument("--check", action="store_true", help="compare the rebuild with the committed file; exit 1 on a difference")
    a = ap.parse_args(argv)
    try:
        doc, _ = build()
        out = RA.dump(doc)
        readme = None
        if a.readme is not None:
            lb = json.loads(LEADERBOARD.read_text(encoding="utf-8"))
            names = {e["id"]: e["display_name"] for e in lb["models"] + lb["baselines"]}
            readme = RA.splice(a.readme.read_bytes().decode("utf-8"), markdown(doc, names)).encode("utf-8")
    except (RA.CheckError, OSError, ValueError, KeyError, IndexError, TypeError, StopIteration) as exc:
        print(f"release_analysis_v020: {exc}", file=sys.stderr)
        return 1
    if a.check:
        ok = RA._same(a.out, out) and (readme is None or RA._same(a.readme, readme))
        print(f"release_analysis_v020: check {'passed' if ok else 'FAILED'}", file=sys.stderr)
        return 0 if ok else 1
    for path, data in ((a.out, out), (a.readme, readme)):
        if path is not None and data is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            print(f"release_analysis_v020: wrote {RA._rel(path)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

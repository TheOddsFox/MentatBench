"""MEASUREMENT ONLY (reads committed v0.1.0 results and fixtures/mentat_hard_test; never runs/). The v0.1.1 analysis of the
Mentat-Hard test results. It changes no score. It adds pairwise comparisons with Holm correction per metric (with a
leave-one-league-out check), rank intervals, depth intervals, confusion matrices, calibration copied from the cells, the
consistency score (tools/eval/consistency.py; required, a failure exits 1), the item audit's candidate list, and the
diagnostic sections: binding detection, the weighting schemes, robustness (the repeat runs) and cost.

Every interval is the league bootstrap of tools/eval/league_ci.py: B = 20000 draws from random.Random("league_ci:0"). The first
2000 draws are the draws the published CI files used, and the self-check requires them to reproduce the published intervals.

usage, from the repo root:
  python tools/eval/release_analysis.py [--out JSON] [--flagged JSONL] [--readme MD] [--repeats DIR] [--check]
    --out      results/v0.1.1/analysis.json (default)
    --flagged  docs/audits/v0.1.1-item-audit/flagged.jsonl (default)
    --readme   the README whose analysis section (between the analysis:start and analysis:end markers) is replaced; not written
               unless given
    --repeats  results/v0.1.1/repeats (an absent or empty folder gives repeats {}; files without runs.json are an error)
    --check    rebuild in memory and compare the bytes with the committed files; exit 1 on any difference
A failed self-check or input, or a consistency import or run error, stops the script with exit 1 and a message, and writes nothing.
"""

import argparse
import gzip
import hashlib
import importlib.util
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools" / "eval"))

from mentat_bench import corrections, decision_scoring as DS  # noqa: E402
import league_ci as LC  # noqa: E402

SCHEMA = "mentat-bench-analysis/1"
RELEASE, BASED_ON = "v0.1.1", "v0.1.0"
V0 = ROOT / "results" / "v0.1.0"
FIXTURE = ROOT / "fixtures" / "mentat_hard_test"
LEADERBOARD = V0 / "leaderboard.json"
CI_FILE = ROOT / "docs" / "results" / "mentat-hard-20261030-ci.json"
BUNDLE = V0 / "build_bundle.py"
ANALYSIS = ROOT / "results" / "v0.1.1" / "analysis.json"
REPEATS = ROOT / "results" / "v0.1.1" / "repeats"
AUDIT = ROOT / "docs" / "audits" / "v0.1.1-item-audit"
VERDICTS = AUDIT / "verdicts.json"
FLAGGED = AUDIT / "flagged.jsonl"
START, END = "<!-- analysis:start -->", "<!-- analysis:end -->"
B = 20000
SEED = 0
PUBLISHED_B = 2000  # the published CI files used B = 2000; their draws are the first 2000 of the B draws
CHUNK = 2000  # draws per MCC batch: bounds the memory of the per-draw confusion sums
ALPHA = 0.05
TOL = 1e-6  # the published values are rounded to 6 decimals
NULL_SPLITS, NULL_SEED = 1000, 20261101  # random 7-vs-7 splits of the 15 models for the item audit's null rate
HALF = 7  # models per half in the item audit: ranks 1-7 (strong) and 9-15 (weak); rank 8 is in neither
RELATIONS = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")
DEPTH_BUCKETS = ("0", "1", "2", "3", "4+", "NONE")
FAMILIES = ("headline", "relation", "group")  # the metric families; leaderboard keys are the same names
CI_KEY = {"headline": "relation_group_mcc", "relation": "relations", "group": "groups"}  # keys of the CI file's runs
OP_OF = {"relation": "decide_relation", "group": "decide_group"}
REL_OP, GRP_OP = OP_OF["relation"], OP_OF["group"]
BIND_OP, UNRESOLVED = "decide_binding", "unresolved"
MISSING = DS.MISSING
MIN_N, MIN_VALID_SHARE = 30, 0.99  # a depth cell is degenerate below either (the method's degenerate pin)
FAMILY_SIZE = 105  # the pairs of the 15 models; each metric family is one Holm family
WEIGHT_SCHEMES = (("relation_only", 1.0), ("r75", 0.75), ("published", 0.5), ("r25", 0.25), ("group_only", 0.0))  # (name, w)
JUDGES = {"model": "Haiku 5.5", "count": 2}  # the item audit's judges (a pin; the verdicts come from them)
HARDWARE = "2× NVIDIA RTX A6000 (48 GB), Ollama 0.40.11 (CUDA), one server per GPU, OLLAMA_NUM_PARALLEL=2"
COST_NOTE = "From the single published run per model; build-phase wall clock, includes harness overhead."
BINDING_DEFINITION = ("Threshold-free detection of unresolved bindings from each run's own P(unresolved) over its binding items; "
                      "the positive class is gold unresolved. auroc is the Mann-Whitney AUROC (a tied pair counts one half); "
                      "average_precision is the step-wise area under the precision-recall curve (sklearn's definition). Both are "
                      "league bootstraps over the same draws. flagged counts items predicted unresolved: the argmax of the two "
                      "probabilities, or the run's decision when it has none. mcc is a copy of the leaderboard's binding MCC point. "
                      "Runs without probabilities (the baselines and the gold_view ceiling) have null auroc and average_precision.")


class CheckError(Exception):
    """A failed self-check or an input that does not match its source: the script prints it and exits 1."""


def check(ok: bool, msg: str) -> None:
    if not ok:
        raise CheckError(msg)


def _json(path: Path):
    check(path.is_file(), f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _rounded(x):
    """Floats to 6 decimals, recursively. NaN and infinity stay, and the JSON encoder (allow_nan=False) refuses them."""
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    if isinstance(x, float):
        return round(x, 6)
    return x


def _close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= TOL


def _bundle():
    """results/v0.1.0/build_bundle.py, imported unchanged (its folder is not a package)."""
    spec = importlib.util.spec_from_file_location("build_bundle", BUNDLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _op_cube(rows: list[dict], op: str, fx: dict) -> np.ndarray:
    """The confusion cube of one op's prediction rows. The class set is the run's own: an empty class adds nothing to the MCC,
    so the values equal the league_ci values of the same items."""
    items = [{"qid": r["qid"], "op": op, "truth": r["gold"], "pred": r["pred"]} for r in rows if r["op"] == op]
    classes = LC._classes([{"items": items}])[op]
    return LC._cube(items, classes, fx)


def _mcc_draws(cube: np.ndarray, W: np.ndarray) -> np.ndarray:
    """league_ci._mcc over the draws in batches (the same per-draw arithmetic, so the same values)."""
    return np.concatenate([LC._mcc(cube, W[i:i + CHUNK]) for i in range(0, len(W), CHUNK)])


def _depth_counts(rows: list[dict], fx: dict) -> tuple[np.ndarray, np.ndarray]:
    """Per league and depth bucket: correct and total relation answers. A missing answer is wrong (as build_results)."""
    corr = np.zeros((len(fx["leagues"]), len(DEPTH_BUCKETS)), dtype=np.int64)
    n = np.zeros_like(corr)
    for r in rows:
        if r["op"] != "decide_relation":
            continue
        if r["gold"] == "NONE":
            check(r.get("depth") is None, f"{r['qid']}: a NONE label with depth {r.get('depth')!r}")
            bucket = "NONE"
        else:
            check(r.get("depth") in (0, 1, 2, 3, 4), f"{r['qid']}: a named relation with depth {r.get('depth')!r}")
            bucket = "4+" if r["depth"] >= 4 else str(r["depth"])
        b, league = DEPTH_BUCKETS.index(bucket), fx["item_league"][r["qid"]]
        n[league, b] += 1
        corr[league, b] += int(r["gold"] == r["pred"])
    return corr, n


def _ratio_draws(num_l: np.ndarray, den_l: np.ndarray, W: np.ndarray) -> np.ndarray:
    """Per draw: the league-weighted numerator over the league-weighted denominator; NaN where the draw has no denominator."""
    num, den = W @ num_l, W @ den_l
    return np.divide(num.astype(float), den.astype(float), out=np.full(num.shape, np.nan),
                     where=den > 0)


def _interval(values: np.ndarray) -> list | None:
    lo, hi = LC._interval(values)
    return None if lo is None else [lo, hi]


def _runs(lb: dict) -> dict:
    """The 15 models in headline rank order, then the baselines that have predictions (the ones with decisions)."""
    entries = {e["id"]: e for e in lb["models"] + lb["baselines"]}
    ids = list(lb["ranking"]) + [e["id"] for e in lb["baselines"] if e["predictions"]]
    return {rid: entries[rid] for rid in ids}


def _same_interval(a: list | None, b: list | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return _close(a[0], b[0]) and _close(a[1], b[1])


def _self_check(data: dict, ci: dict, W: np.ndarray, L: int, models: list[str]) -> bool:
    """Every check of the module against the committed numbers. Failures are collected and reported together. Returns whether the
    first 2000 draws equal the published draws (and so reproduce the published intervals)."""
    bad = []
    first_2000 = bool(np.array_equal(W[:PUBLISHED_B], LC.draws(PUBLISHED_B, SEED, L)))
    if not first_2000:
        bad.append("the first 2000 bootstrap draws differ from the published draws")
    for m_prev, m_next in zip(models, models[1:]):
        if not data[m_prev]["mcc"]["headline"][0] >= data[m_next]["mcc"]["headline"][0]:
            bad.append(f"{m_prev} does not rank above {m_next} by headline point")
    for rid, d in data.items():
        sc = d["scored"]
        expect = {"headline": sc["headline"]["relation_group_mcc"], "relation": sc["ops"]["decide_relation"]["mcc"],
                  "group": sc["ops"]["decide_group"]["mcc"]}
        for fam in FAMILIES:
            point, boot = d["mcc"][fam]
            if not _close(point, expect[fam]):
                bad.append(f"{rid} {fam}: {point} from the rows, {expect[fam]} from decision_scoring (rescore)")
            ours = _interval(boot[:PUBLISHED_B])
            block = d["entry"].get("metrics", {}).get(fam)
            if block is not None:
                if not _close(round(point, 6), block["point"]):
                    bad.append(f"{rid} {fam}: point {point} differs from the leaderboard {block['point']}")
                if not _same_interval(ours, block["ci95"]):
                    bad.append(f"{rid} {fam}: the first 2000 draws give {ours}, the leaderboard {block['ci95']}")
            published = ci["runs"][rid]["metrics"][CI_KEY[fam]]
            if not (_close(round(point, 6), published["point"])
                    and _same_interval(ours, [published["lo"], published["hi"]])):
                bad.append(f"{rid} {fam}: the CI file has {published}, ours {point} {ours}")
        acc = d["entry"].get("depth_accuracy")
        if acc is not None:
            for i, bucket in enumerate(DEPTH_BUCKETS):
                n_b, c_b = int(d["n"][:, i].sum()), int(d["corr"][:, i].sum())
                want = acc[bucket]
                if (n_b, c_b) != (want["n"], want["correct"]) or not _close(c_b / n_b if n_b else None, want["accuracy"]):
                    bad.append(f"{rid} depth {bucket}: n {n_b} correct {c_b}, leaderboard {want}")
        cell = d["cell"]["score_decisions"]["ops"]
        rel_rows = Counter((r["gold"], r["pred"]) for r in d["rows"] if r["op"] == "decide_relation")
        cell_rel = {(t, p): c for t, row in cell["decide_relation"]["confusion"].items() for p, c in row.items()}
        if dict(rel_rows) != cell_rel:
            bad.append(f"{rid}: relation confusion differs from the cell's")
        grp_rows = Counter((DS._kind(r["gold"]), DS._kind(r["pred"])) for r in d["rows"] if r["op"] == "decide_group")
        cell_grp = Counter()
        for t, row in cell["decide_group"]["confusion"].items():
            for p, c in row.items():
                cell_grp[(DS._kind(t), DS._kind(p))] += c
        if dict(grp_rows) != dict(cell_grp):
            bad.append(f"{rid}: group kind confusion differs from the cell's")
    if bad:
        more = f"\n  ... {len(bad) - 25} more" if len(bad) > 25 else ""
        raise CheckError("self-check failed:\n  " + "\n  ".join(bad[:25]) + more)
    return first_2000


def _pairwise(models: list[str], V: np.ndarray, P: list[float]) -> list[dict]:
    """The 105 pairs of the models in rank order (a is the higher-ranked). delta = a - b per draw; p = min(1, 2 (1 + min(#<=0,
    #>=0)) / (1 + B_valid)); Holm within the family."""
    left, right = np.triu_indices(len(models), 1)
    D = V[left] - V[right]
    valid = ~np.isnan(D)
    le = ((D <= 0) & valid).sum(axis=1)
    ge = ((D >= 0) & valid).sum(axis=1)
    p = np.minimum(1.0, 2.0 * (1 + np.minimum(le, ge)) / (1 + valid.sum(axis=1)))
    m = len(p)
    order = np.argsort(p, kind="stable")
    adj = np.empty(m)
    adj[order] = np.minimum(1.0, np.maximum.accumulate((m - np.arange(m)) * p[order]))  # Holm step-down
    pairs = []
    for k, (i, j) in enumerate(zip(left, right)):
        diff = P[i] - P[j]
        resolved = bool(adj[k] <= ALPHA)
        higher = models[i] if resolved and diff > 0 else (models[j] if resolved else None)  # the contract: diff > 0 ? a : b
        pairs.append({"a": models[i], "b": models[j], "diff": {"point": diff, "ci95": _interval(D[k])}, "p": float(p[k]),
                      "p_holm": float(adj[k]), "resolved": resolved, "higher": higher})
    return pairs


def _lolo(models: list[str], data: dict, P: dict, pairwise: dict, leagues: list[str]) -> dict:
    """Leave-one-league-out: each Holm-resolved pair's point difference recomputed with each league left out. Survive = the sign
    holds in all of them; flips list the pairs that do not, with the leagues whose removal flips the sign."""
    index = {m: i for i, m in enumerate(models)}
    out = {}
    for fam in FAMILIES:
        loo = np.vstack([data[m]["loo"][fam] for m in models])  # (15, leagues)
        resolved = [pr for pr in pairwise[fam] if pr["resolved"]]
        flips = []
        for pr in resolved:
            i, j = index[pr["a"]], index[pr["b"]]
            sign = np.sign(P[fam][i] - P[fam][j])
            d = loo[i] - loo[j]
            bad = [leagues[k] for k in range(len(leagues)) if not np.sign(d[k]) == sign]
            if bad:
                flips.append({"a": pr["a"], "b": pr["b"], "leagues": bad})
        out[fam] = {"resolved": len(resolved), "survive": len(resolved) - len(flips), "flips": flips}
    return out


def _ranks(models: list[str], V: np.ndarray, P: list[float]) -> dict:
    """Per model: rank (its point rank: 1 + the number of models with a higher point), ci95 (per draw, rank = 1 + the number of
    models strictly greater, ties share a rank; 2.5% by method lower and 97.5% by method higher) and median (numpy's method
    nearest, so a rank)."""
    rank = 1.0 + (V[None, :, :] > V[:, None, :]).sum(axis=1)  # rank[i, b]: models j with V[j, b] > V[i, b]
    rank[np.isnan(V)] = np.nan
    out = {}
    for i, m in enumerate(models):
        ok = rank[i][~np.isnan(rank[i])]
        lo, hi = np.percentile(ok, 2.5, method="lower"), np.percentile(ok, 97.5, method="higher")
        out[m] = {"rank": 1 + sum(P[j] > P[i] for j in range(len(models))), "ci95": [int(lo), int(hi)],
                  "median": int(np.percentile(ok, 50, method="nearest"))}
    return out


def _depth_block(counts: dict, W: np.ndarray) -> dict:
    """Per bucket: n, correct, the point ratio, the draw interval, valid_draws (the draws with an item) and degenerate. The
    degenerate share is taken over the draws passed in (len(W))."""
    out = {}
    for run_id, (corr, n) in counts.items():
        ratio = _ratio_draws(corr, n, W)
        block = {}
        for i, bucket in enumerate(DEPTH_BUCKETS):
            n_b, c_b = int(n[:, i].sum()), int(corr[:, i].sum())
            valid = int((~np.isnan(ratio[:, i])).sum())
            block[bucket] = {"n": n_b, "correct": c_b, "point": c_b / n_b if n_b else None,
                             "ci95": _interval(ratio[:, i]), "valid_draws": valid,
                             "degenerate": bool(n_b < MIN_N or valid / len(W) < MIN_VALID_SHARE)}
        out[run_id] = block
    return out


CONSISTENCY_NOTE = (" Release analysis: an answer is conflicting if it belongs to a minimal conflict under either predicate (MAIN or "
                    "UNSAT-only), so conflicting_unsat is part of conflicting in total and in every op. conflicting_main counts the "
                    "answers in a minimal MAIN conflict (UNSAT, forced outcome, cardinality or invalid group). by_size is the smallest "
                    "witness under the main predicate (UNSAT, forced outcome, cardinality or invalid group) for each conflicting_main "
                    "answer, so sum(by_size) == conflicting_main; an answer only in a minimal UNSAT-only conflict has no main witness "
                    "and is not in by_size.")


def _consistency(rows_of: dict, fx: dict, W: np.ndarray) -> dict:
    """The consistency module's score of every run, per answer: main (in a minimal MAIN conflict of at most MAX_WITNESS answers)
    and unsat (in a minimal UNSAT-only conflict). conflicting is the union, so conflicting_unsat is part of it in total and in every
    op (the site's rule). by_size counts the MAIN answers by their smallest witness; an UNSAT-only answer has no witness size. The
    shares are league bootstraps over the same draws. Any import or run error stops the script (CheckError); nothing is written as
    null."""
    try:
        from consistency import score_run
    except Exception as exc:  # the module must import: a missing or broken consistency.py is an error, not a null block
        raise CheckError(f"tools/eval/consistency.py cannot be imported: {exc!r}") from exc
    L = len(fx["leagues"])
    runs, definition, max_witness = {}, None, None

    def blank() -> dict:
        return {op: {"claim_bearing": 0, "conflicting": 0, "conflicting_unsat": 0} for op in OP_OF.values()}

    for number, (rid, rows) in enumerate(rows_of.items(), start=1):
        started = time.time()
        try:
            res = score_run(rows, FIXTURE)
        except Exception as exc:
            raise CheckError(f"consistency.score_run raised on {rid}: {exc!r}") from exc
        print(f"release_analysis: consistency {number}/{len(rows_of)} {rid}: {time.time() - started:.1f} s", file=sys.stderr)
        answers = res["answers"]
        check(isinstance(answers, dict) and all(
            isinstance(a, dict) and isinstance(a.get("main"), bool) and isinstance(a.get("unsat"), bool)
            for a in answers.values()), f"{rid}: score_run answers must map qid to {{main, unsat, size}} with bool main and unsat")
        check(isinstance(res["contradiction_detected"], bool), f"{rid}: contradiction_detected must be a bool")
        check(sorted(res["by_size"]) == ["1", "2", "3"], f"{rid}: by_size keys are {sorted(res['by_size'])}")
        if definition is None:
            definition, max_witness = res["definition"], res["max_witness"]
        check(res["definition"] == definition and res["max_witness"] == max_witness,
              f"{rid}: the definition or max_witness differs from the other runs")
        op_of = {r["qid"]: r["op"] for r in rows if r["op"] in OP_OF.values()}
        total = np.zeros(L, dtype=np.int64)
        main_l, unsat_l, union_l = (np.zeros(L, dtype=np.int64) for _ in range(3))
        main_by_op, union_by_op = blank(), blank()  # the module's MAIN counts (checked against score_run) and the reported union
        by_size = {"1": 0, "2": 0, "3": 0}
        for qid, a in answers.items():
            check(qid in op_of, f"{rid}: answer {qid} is not a relation or group item of this run")
            league, op = fx["item_league"][qid], op_of[qid]
            total[league] += 1
            main_by_op[op]["claim_bearing"] += 1
            union_by_op[op]["claim_bearing"] += 1
            if a["main"]:
                check(a["size"] in (1, 2, 3), f"{rid}: conflicting answer {qid} has witness size {a['size']!r}")
                main_l[league] += 1
                main_by_op[op]["conflicting"] += 1
                by_size[str(a["size"])] += 1
            else:
                check(a["size"] is None, f"{rid}: non-conflicting answer {qid} has witness size {a['size']!r}")
            if a["main"] or a["unsat"]:
                union_l[league] += 1
                union_by_op[op]["conflicting"] += 1
            if a["unsat"]:
                unsat_l[league] += 1
                main_by_op[op]["conflicting_unsat"] += 1
                union_by_op[op]["conflicting_unsat"] += 1
        cb, k_main, ku = int(total.sum()), int(main_l.sum()), int(unsat_l.sum())
        k_union = int(union_l.sum())
        for op in OP_OF.values():
            union_by_op[op]["conflicting_main"] = main_by_op[op]["conflicting"]
        check(sum(by_size.values()) == k_main, f"{rid}: by_size sums to {sum(by_size.values())}, conflicting_main is {k_main}")
        check(res["claim_bearing"] == cb and res["conflicting"] == k_main and res["conflicting_unsat"] == ku,
              f"{rid}: score_run reports claim_bearing {res['claim_bearing']}, conflicting {res['conflicting']}, "
              f"conflicting_unsat {res['conflicting_unsat']}; its answers give {cb}, {k_main}, {ku}")
        check(res["by_op"] == main_by_op, f"{rid}: by_op {res['by_op']} differs from its answers {main_by_op}")
        check({s: int(v) for s, v in res["by_size"].items()} == by_size and sum(by_size.values()) == k_main,
              f"{rid}: by_size {res['by_size']} differs from its answers {by_size}")
        check(all(v["conflicting_unsat"] <= v["conflicting"] <= v["claim_bearing"] for v in union_by_op.values())
              and ku <= k_union <= cb, f"{rid}: the union breaks conflicting_unsat <= conflicting <= claim_bearing")
        runs[rid] = {"claim_bearing": cb, "conflicting": k_union, "conflicting_main": k_main,
                     "share": k_union / cb if cb else None,
                     "ci95": _interval(_ratio_draws(union_l, total, W)),
                     "conflicting_unsat": ku, "share_unsat": ku / cb if cb else None,
                     "ci95_unsat": _interval(_ratio_draws(unsat_l, total, W)),
                     "contradiction_detected": res["contradiction_detected"], "by_size": by_size, "by_op": union_by_op}
        if rid == "gold_view":
            check(k_union == 0 and ku == 0 and not res["contradiction_detected"],
                  f"gold_view has {k_union} conflicting answers ({ku} UNSAT), contradiction_detected="
                  f"{res['contradiction_detected']}: the stop rule needs 0, 0 and false (keep the forced-constant rule out and "
                  "document it)")
    return {"definition": definition + CONSISTENCY_NOTE, "max_witness": max_witness, "runs": runs}


def _binding_draws(scores: np.ndarray, positive: np.ndarray, league: np.ndarray, W: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per draw, the league-weighted ROC AUC (Mann-Whitney: a tied pair counts one half) and average precision (step-wise over
    the distinct scores, as sklearn's average_precision_score). Item i has weight W[draw, league[i]], the same as repeating it that
    many times, so the values equal sklearn's on the repeated items. NaN where a draw lacks a positive or a negative (AUC) or a
    positive (AP)."""
    order = np.argsort(scores, kind="stable")
    s, pos, lg = scores[order], positive[order], league[order]
    starts = np.r_[0, np.flatnonzero(np.diff(s)) + 1]  # the first item of each distinct score, low to high
    auc, ap = np.empty(len(W)), np.empty(len(W))
    for lo in range(0, len(W), CHUNK):
        w = W[lo:lo + CHUNK][:, lg].astype(float)  # (draws, items)
        wp = np.add.reduceat(w * pos, starts, axis=1)  # positive weight at each distinct score
        wn = np.add.reduceat(w * ~pos, starts, axis=1)
        P, N = wp.sum(axis=1), wn.sum(axis=1)
        below = np.cumsum(wn, axis=1) - wn  # negative weight strictly below each score
        auc[lo:lo + CHUNK] = np.divide((wp * (below + wn / 2)).sum(axis=1), P * N, out=np.full(len(w), np.nan), where=P * N > 0)
        tp, fp = np.cumsum(wp[:, ::-1], axis=1), np.cumsum(wn[:, ::-1], axis=1)  # from the highest score down
        precision = np.divide(tp, tp + fp, out=np.zeros_like(tp), where=tp + fp > 0)
        ap[lo:lo + CHUNK] = np.divide((wp[:, ::-1] * precision).sum(axis=1), P, out=np.full(len(w), np.nan), where=P > 0)
    return auc, ap


def _estimate(point: np.ndarray, draws: np.ndarray) -> dict | None:
    """A point with its league-bootstrap 95% interval, or None when the point is undefined."""
    return None if np.isnan(point[0]) else {"point": float(point[0]), "ci95": _interval(draws)}


def _binding_run(rows: list[dict], entry: dict, fx: dict, W: np.ndarray) -> dict:
    """One run's binding detection (binding_detection.runs). A run has probabilities when every binding row carries them."""
    items = sorted((r for r in rows if r["op"] == BIND_OP), key=lambda r: r["qid"])
    check(items, f"{entry['id']}: no binding rows")
    positive = np.array([r["gold"] == UNRESOLVED for r in items])
    has = [("probs" in r) for r in items]
    check(all(has) or not any(has), f"{entry['id']}: some binding rows have probabilities and some do not")
    binding = entry["metrics"].get("binding")
    block = {"n": len(items), "n_unresolved": int(positive.sum()), "prevalence": float(positive.mean()),
             "mcc": None if binding is None else binding["point"], "auroc": None, "average_precision": None}
    if not all(has):
        block["flagged"] = sum(r["pred"] == UNRESOLVED for r in items)
        return block
    scores = np.array([r["probs"][UNRESOLVED] for r in items], dtype=float)
    block["flagged"] = sum(r["probs"][UNRESOLVED] > r["probs"]["bound"] for r in items)
    league = np.array([fx["item_league"][r["qid"]] for r in items])
    auc, ap = _binding_draws(scores, positive, league, W)
    auc0, ap0 = _binding_draws(scores, positive, league, np.ones((1, W.shape[1]), dtype=np.int64))
    block["auroc"] = _estimate(auc0, auc)
    block["average_precision"] = _estimate(ap0, ap)
    return block


def _item_audit(models: list[str], rows_of: dict, points: dict, fam_of: dict, verdicts: Path) -> tuple[dict, bytes]:
    """Candidate flags over the 6000 relations and 1000 groups (readings excluded). Per op, the halves are the models ranked by
    the op's own MCC (top 7 strong, bottom 7 weak, rank 8 in neither). Flags:
      all_wrong: every one of the 15 models is wrong on the item;
      inverted: within each (op, gold class, stratum) cell each model's correctness is centred on its cell mean; the weak half's
        mean residual is above the strong half's, and the residuals' point-biserial r_pb with the op's MCC over the 15 models
        is below 0.
    The null rate of inverted uses NULL_SPLITS seeded random 7-vs-7 splits. Returns the block and the flagged.jsonl bytes."""
    per = []
    for m in models:
        rs = {}
        for r in rows_of[m]:
            if r["op"] in OP_OF.values():
                check(r["qid"] not in rs, f"{m}: a duplicate item {r['qid']}")
                rs[r["qid"]] = r
        per.append(rs)
    qids_all = sorted(per[0])
    for m, rs in zip(models, per):
        check(set(rs) == set(qids_all), f"{m}: its items differ from the other models'")
    fams = sorted({fam_of[m] for m in models})
    members = {f: [i for i, m in enumerate(models) if fam_of[m] == f] for f in fams}
    rng = np.random.default_rng(NULL_SEED)
    Ms, Mw = np.zeros((len(models), NULL_SPLITS)), np.zeros((len(models), NULL_SPLITS))
    for s in range(NULL_SPLITS):
        perm = rng.permutation(len(models))
        Ms[perm[:HALF], s] = 1 / HALF
        Mw[perm[HALF + 1:], s] = 1 / HALF
    flagged, halves, by_op = [], {}, {}
    null_counts = np.zeros(NULL_SPLITS)  # inverted items per random split, over both ops
    for op in OP_OF.values():
        qids = [q for q in qids_all if per[0][q]["op"] == op]
        for q in qids:
            check(all(per[i][q]["gold"] == per[0][q]["gold"] for i in range(len(models))),
                  f"{q}: the gold label differs between runs")
        C = np.array([[per[i][q]["gold"] == per[i][q]["pred"] for i in range(len(models))] for q in qids], dtype=float)
        keys = [(per[0][q]["gold"], per[0][q]["stratum"]) for q in qids]
        index = {}
        cell = np.array([index.setdefault(k, len(index)) for k in keys])
        sums = np.zeros((len(index), len(models)))
        np.add.at(sums, cell, C)
        counts = np.bincount(cell, minlength=len(index)).astype(float)
        R = C - (sums / counts[:, None])[cell]  # residuals: correctness minus the model's mean in the item's cell
        tot = np.array([points[op][m] for m in models], dtype=float)
        order = sorted(range(len(models)), key=lambda i: (-tot[i], i))
        strong, rank8, weak = order[:HALF], order[HALF], order[HALF + 1:]
        halves[op] = {"strong": [models[i] for i in strong], "rank_8": models[rank8],
                      "weak": [models[i] for i in weak]}
        n_correct = C.sum(axis=1)
        strong_c, weak_c = C[:, strong].sum(axis=1), C[:, weak].sum(axis=1)
        diff = R[:, weak].mean(axis=1) - R[:, strong].mean(axis=1)
        Rc, tc = R - R.mean(axis=1, keepdims=True), tot - tot.mean()
        denom = np.sqrt((Rc ** 2).sum(axis=1)) * np.sqrt((tc ** 2).sum())
        r_pb = np.where(denom > 1e-9, (Rc @ tc) / np.where(denom > 1e-9, denom, 1.0), np.nan)
        all_wrong = n_correct == 0
        inverted = (diff > 1e-9) & (r_pb < 0)
        families_wrong = sum((C[:, members[f]].sum(axis=1) == 0).astype(int) for f in fams)
        null = ((R @ Mw - R @ Ms) > 1e-9) & (r_pb < 0)[:, None]  # inverted under each random split
        null_counts += null.sum(axis=0)
        n_op = len(qids)
        for k in np.flatnonzero(all_wrong | inverted):
            rs = [per[i][qids[k]] for i in range(len(models))]
            flags = (["all_wrong"] if all_wrong[k] else []) + (["inverted"] if inverted[k] else [])
            modal, count = sorted(Counter(r["pred"] for r in rs).items(), key=lambda kv: (-kv[1], kv[0]))[0]
            flagged.append({"qid": qids[k], "op": op, "gold": rs[0]["gold"], "flags": flags, "n_correct": int(n_correct[k]),
                            "strong_correct": int(strong_c[k]), "weak_correct": int(weak_c[k]),
                            "r_pb": None if np.isnan(r_pb[k]) else float(r_pb[k]),
                            "residual_weak_minus_strong": float(diff[k]), "families_wrong": int(families_wrong[k]),
                            "modal_pred": modal, "modal_count": int(count), "depth": rs[0].get("depth"),
                            "stratum": rs[0]["stratum"], "held_out": rs[0]["held_out"]})
        by_op[op] = {"items": n_op, "flagged": sum(r["op"] == op for r in flagged),
                     "all_wrong": sum(r["op"] == op and "all_wrong" in r["flags"] for r in flagged),
                     "inverted": sum(r["op"] == op and "inverted" in r["flags"] for r in flagged)}
    flagged.sort(key=lambda r: r["qid"])
    text = "".join(json.dumps(_rounded(rec), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
                   for rec in flagged)
    data = text.encode("utf-8")

    def tally(recs):
        return {"flagged": len(recs), "all_wrong": sum("all_wrong" in r["flags"] for r in recs),
                "inverted": sum("inverted" in r["flags"] for r in recs)}

    by_stratum = {op: {s: tally([r for r in flagged if r["op"] == op and r["stratum"] == s])
                       for s in sorted({r["stratum"] for r in flagged if r["op"] == op})} for op in OP_OF.values()}
    top = sorted(flagged, key=lambda r: (r["n_correct"], r["qid"]))[:100]
    top_flagged = [{"qid": r["qid"], "op": r["op"], "gold": r["gold"], "flags": r["flags"], "n_correct": r["n_correct"],
                    "modal_pred": r["modal_pred"], "depth": r["depth"], "stratum": r["stratum"]} for r in top]
    # the inverted flag without the all-wrong items: an all-wrong item tends to satisfy the inverted rule mechanically (its cell
    # means include it), so the flags overlap by construction and the overlap is reported apart from the totals
    inverted_only = (lambda recs: sum("inverted" in r["flags"] and "all_wrong" not in r["flags"] for r in recs))
    for op in OP_OF.values():
        by_op[op]["inverted_not_all_wrong"] = inverted_only([r for r in flagged if r["op"] == op])
    n_items = sum(by_op[op]["items"] for op in OP_OF.values())
    null_shares = null_counts / n_items  # shares of the same 7000-item population as item_audit.inverted / items
    block = {"items": n_items,
             "items_by_op": {op: by_op[op]["items"] for op in OP_OF.values()},
             "strong_half": halves[REL_OP]["strong"], "rank_8": halves[REL_OP]["rank_8"],
             "weak_half": halves[REL_OP]["weak"], "halves": halves, **tally(flagged),
             "inverted_not_all_wrong": inverted_only(flagged),
             "all_wrong_and_inverted": sum("all_wrong" in r["flags"] and "inverted" in r["flags"] for r in flagged),
             "by_op": by_op, "by_stratum": by_stratum,
             "null_inverted_rate": {"mean": float(null_shares.mean()), "p95": float(np.percentile(null_shares, 95))},
             "top_flagged": top_flagged,
             "judged": _judged(verdicts, len(flagged))}
    return block, data


def _judged(verdicts: Path, flagged: int) -> dict | None:
    """The judging step's verdicts file, or None until it exists. The file holds counts (suspect_label, hard_valid, unclear),
    n_judged (the flagged items the judges read), sampled (a bool) and sample_rule (text). n_flagged is the audit's flagged
    total; a file that does not sample reads every flagged item."""
    if not verdicts.is_file():
        return None
    data = verdicts.read_bytes()
    doc = json.loads(data.decode("utf-8"))
    counts = doc.get("counts", {})
    check(sorted(counts) == ["hard_valid", "suspect_label", "unclear"], f"{_rel(verdicts)} counts are {sorted(counts)}")
    check(all(k in doc for k in ("n_judged", "sampled", "sample_rule")) and isinstance(doc["sampled"], bool)
          and isinstance(doc["sample_rule"], str), f"{_rel(verdicts)} needs n_judged, sampled (bool) and sample_rule (text)")
    n_judged, sampled = int(doc["n_judged"]), doc["sampled"]
    check(0 <= n_judged <= flagged and (sampled or n_judged == flagged),
          f"{_rel(verdicts)}: n_judged {n_judged} of {flagged} flagged items")
    counts = {k: int(counts[k]) for k in sorted(counts)}
    check(sum(counts.values()) == n_judged, f"{_rel(verdicts)}: counts sum to {sum(counts.values())}, n_judged is {n_judged}")
    return {"path": _rel(verdicts), "sha256": _sha(data), "counts": counts, "n_judged": n_judged, "n_flagged": flagged,
            "sampled": sampled, "sample_rule": doc["sample_rule"]}


REPEAT_SCHEMA = "mentat-bench-repeats/1"
REPEAT_NOTES = {
    "note_protocol": "Diagnostic only: the leaderboard headline remains the single pre-registered run; repeats use the pre-registered "
                     "command on CUDA (Ollama 0.40.11, both A6000s).",
    "note_shuffle": "shuffle = one pass with answer options presented in a seeded random order (MB_S1_OPTION_SEED=20261101); same "
                    "model, weights and prompts otherwise.",
    "note_vulkan": "vulkan = the d3:9b repeats run on Ollama's Vulkan backend instead of CUDA (two passes, r2 and r3, which must "
                   "agree); reported as a backend-sensitivity diagnostic.",
}
# runs.json tag -> the key in repeats (the site accepts r2, r3, shuffle, vulkan). r1 is the published run and is not listed.
REPEAT_KEYS = {"r1": "r1", "r2": "r2", "r3": "r3", "shuffle": "shuffle", "vulkan_r2": "vulkan", "vulkan_r3": "vulkan"}


def _pred_key(r: dict) -> tuple:
    return (r["qid"], r["op"], r.get("reading"))


def _repeats(folder: Path, rows_of: dict, headline: dict, bundle, version: int) -> dict:
    """The repeat runs of the models (plan D1-D3). For each repeat (not r1, the published run): identical, the share of the 10,716
    prediction rows whose label equals the published run's; delta_vs_published, its headline minus the published headline;
    flip_rate, the share whose label differs. Empty when the folder is absent or empty; a folder with files and no runs.json is
    an error. Each repeat's headline is rescored from its predictions and checked against the index, and identical is checked
    against the index's identical_to_published_decisions."""
    index = folder / "runs.json"
    if not index.is_file():
        check(not folder.is_dir() or not any(folder.iterdir()), f"{_rel(folder)} holds files but no runs.json")
        return {}
    doc = _json(index)
    check(doc.get("schema") == REPEAT_SCHEMA, f"{_rel(index)} has schema {doc.get('schema')!r}")
    out = {}
    for rec in doc["runs"]:
        rid, tag = rec["id"], rec["tag"]
        check(tag in REPEAT_KEYS, f"{rid}: unknown repeat tag {tag!r}")
        check(rec.get("status") == "complete" and rec.get("rc") == 0, f"{rid} {tag}: not a complete run")
        check(rid in rows_of and rid in headline,
              f"{rid} {tag}: not one of the 15 models or baselines of this analysis")
        blob = (ROOT / rec["predictions"]).read_bytes()
        rep = {_pred_key(r): r["pred"] for r in (json.loads(line) for line in gzip.decompress(blob).decode("utf-8").splitlines())}
        pub = {_pred_key(r): r["pred"] for r in rows_of[rid]}
        check(len(rep) == 10716 and set(rep) == set(pub), f"{rid} {tag}: its rows do not match the published rows")
        same = sum(rep[k] == pub[k] for k in pub) / len(pub)
        check((same == 1.0) == rec["identical_to_published_decisions"],
              f"{rid} {tag}: identical {same} disagrees with identical_to_published_decisions "
              f"{rec['identical_to_published_decisions']}")
        scored = bundle.rescore(blob, version)["headline"]["relation_group_mcc"]
        check(_close(scored, rec["headline_relation_group_mcc"]),
              f"{rid} {tag}: headline {rec['headline_relation_group_mcc']} in the index, {scored} rescored")
        if tag == "r1":
            check(same == 1.0, f"{rid} r1 is not identical to the published run")
            continue
        key = REPEAT_KEYS[tag]
        entry = {"identical": same, "delta_vs_published": rec["headline_relation_group_mcc"] - headline[rid], "flip_rate": 1.0 - same}
        check(key not in out.get(rid, {}) or out[rid][key] == entry, f"{rid} {tag}: differs from the other {key} pass")
        out.setdefault(rid, {})[key] = entry
    return {**out, **REPEAT_NOTES}


def _robustness(repeats: dict, models: list[str]) -> dict:
    """The robustness section, from the repeats block. shuffle and vulkan: the headline change against the published run.
    run_to_run: the largest change of the r2 and r3 repeats. The summary's presentation covers every model with a shuffle run;
    presentation_matched covers exactly the models with run_to_run (each of them must have a shuffle run)."""
    per = {}
    for m, tags in repeats.items():
        if m.startswith("note_"):
            continue
        check(m in models, f"repeats name {m}, which is not one of the models")
        entry = {"shuffle": None, "vulkan": None, "run_to_run": None}
        for key in ("shuffle", "vulkan"):
            if key in tags:
                entry[key] = {"delta": tags[key]["delta_vs_published"], "flip_rate": tags[key]["flip_rate"]}
        reps = [tags[t] for t in ("r2", "r3") if t in tags]
        if reps:
            entry["run_to_run"] = {"max_abs_delta": max(abs(r["delta_vs_published"]) for r in reps),
                                   "max_flip_rate": max(r["flip_rate"] for r in reps)}
        per[m] = entry
    matched = [e for e in per.values() if e["run_to_run"] is not None]
    check(all(e["shuffle"] is not None for e in matched), "a model with run-to-run repeats has no shuffle run")
    shuffles = [abs(e["shuffle"]["delta"]) for e in per.values() if e["shuffle"] is not None]
    backend = [abs(e["vulkan"]["delta"]) for e in per.values() if e["vulkan"] is not None]
    summary = {
        "presentation": {"n_models": len(shuffles), "median_abs_delta": float(np.median(shuffles)) if shuffles else 0.0,
                         "max_abs_delta": max(shuffles, default=0.0)},
        "presentation_matched": {"n_models": len(matched),
                                 "max_abs_delta": max((abs(e["shuffle"]["delta"]) for e in matched), default=0.0)},
        "run_to_run": {"n_models": len(matched), "max_abs_delta": max((e["run_to_run"]["max_abs_delta"] for e in matched), default=0.0)},
        "backend": None if not backend else {"n_models": len(backend), "max_abs_delta": max(backend)},
    }
    return {"models": per, "summary": summary}


def _weighting(models: list[str], V: dict, P: dict, headline_pairs: list[dict], headline_ranks: dict) -> dict:
    """The headline under five weightings of the relation MCC (w) and the group MCC (1 - w), per draw. Each scheme has its own rank
    intervals and its own Holm family of FAMILY_SIZE pairs. The published scheme (w = 0.5) is the headline and must reproduce its
    ranks and pairs. A pair is robust when it is resolved under every scheme with the same winner; weight-dependent when it is
    resolved under some schemes only, or its winner differs."""
    out = {"schemes": [], "ranks": {}, "resolved": {}, "robust_pairs": [], "weight_dependent_pairs": []}
    pairs_of = {}
    for name, w in WEIGHT_SCHEMES:
        Vw = w * V["relation"] + (1 - w) * V["group"]
        Pw = [w * a + (1 - w) * b for a, b in zip(P["relation"], P["group"])]
        pairs_of[name] = _pairwise(models, Vw, Pw)
        out["schemes"].append({"name": name, "relation": w, "group": 1 - w})
        out["ranks"][name] = _ranks(models, Vw, Pw)
        out["resolved"][name] = [{"a": pr["a"], "b": pr["b"], "higher": pr["higher"], "p_holm": pr["p_holm"]}
                                 for pr in pairs_of[name] if pr["resolved"]]
    published = pairs_of["published"]
    check([(pr["a"], pr["b"], pr["resolved"], pr["higher"]) for pr in published]
          == [(pr["a"], pr["b"], pr["resolved"], pr["higher"]) for pr in headline_pairs]
          and all(abs(x["p_holm"] - y["p_holm"]) <= 1e-12 for x, y in zip(published, headline_pairs)),
          "the published weighting does not reproduce the headline pairs")
    check(out["ranks"]["published"] == headline_ranks, "the published weighting does not reproduce the headline ranks")
    by = {name: {(pr["a"], pr["b"]): pr for pr in pairs_of[name]} for name, _ in WEIGHT_SCHEMES}
    for pr in published:
        key = (pr["a"], pr["b"])
        winners = {name: by[name][key]["higher"] for name, _ in WEIGHT_SCHEMES}
        resolved = [by[name][key]["resolved"] for name, _ in WEIGHT_SCHEMES]
        if all(resolved) and len(set(winners.values())) == 1:
            out["robust_pairs"].append({"a": key[0], "b": key[1], "higher": winners["published"]})
        elif any(resolved):
            out["weight_dependent_pairs"].append({"a": key[0], "b": key[1], "higher_by_scheme": winners})
    return out


def _cost(models: list[str], entries: dict) -> dict:
    """Per model, from the single published run: the leaderboard's build wall time, its decision count (the cell's, one per
    decision item: a reading's base and alt rows count once) and the ratio of the two."""
    runs = {}
    for m in models:
        e = entries[m]
        seconds, decisions = e["runtime"]["build_wall_s"], e["counts"]["decisions"]
        check(seconds > 0, f"{m}: build_wall_s is {seconds}")
        runs[m] = {"build_wall_s": seconds, "decisions": decisions, "decisions_per_s": decisions / seconds,
                   "size_bytes": e["size_bytes"], "params": e["params"], "quant": e["quant"]}
    return {"hardware": HARDWARE, "note": COST_NOTE, "runs": runs}


def build(repeats: Path, verdicts: Path = VERDICTS) -> tuple[dict, bytes]:
    """Every check and every output, in memory: (analysis document, flagged.jsonl bytes)."""
    lb_bytes = LEADERBOARD.read_bytes()
    lb, ci = json.loads(lb_bytes), _json(CI_FILE)
    fx = LC.read_fixture(FIXTURE)
    leagues = fx["leagues"]
    L = len(leagues)
    check(L == 18, f"the fixture has {L} leagues, the analysis is for 18")
    content_hash = corrections.content_hash(corrections.content_hashes(FIXTURE))
    fixture_doc = _json(FIXTURE / "fixture.json")
    check(ci["stamp"]["content_hash"] == content_hash, "the CI file is for another fixture")
    check(ci["B"] == PUBLISHED_B and ci["seed"] == SEED, "the CI file is not the B 2000, seed 0 output")
    runs = _runs(lb)
    models = list(lb["ranking"])
    check(len(models) == 15, f"{len(models)} models in the leaderboard, expected 15")
    fam_of = {e["id"]: e.get("family") for e in lb["models"]}
    check(all(fam_of[m] for m in models), "a model in the leaderboard has no family")
    bundle = _bundle()
    version = DS.fixture_version(FIXTURE)
    W = LC.draws(B, SEED, L)
    ones = np.ones((1, L), dtype=np.int64)
    WLOO = np.ones((L, L), dtype=np.int64) - np.eye(L, dtype=np.int64)  # row l leaves league l out
    data, rows_of, predictions = {}, {}, {}
    for rid, entry in runs.items():
        blob = (ROOT / entry["predictions"]).read_bytes()
        predictions[_rel(ROOT / entry["predictions"])] = _sha(blob)
        rows = [json.loads(line) for line in gzip.decompress(blob).decode("utf-8").splitlines()]
        rows_of[rid] = rows
        cubes = {fam: _op_cube(rows, OP_OF[fam], fx) for fam in ("relation", "group")}
        mcc = {fam: (float(LC._mcc(cubes[fam], ones)[0]), _mcc_draws(cubes[fam], W)) for fam in cubes}
        mcc["headline"] = ((mcc["relation"][0] + mcc["group"][0]) / 2,
                           (mcc["relation"][1] + mcc["group"][1]) / 2)  # NaN propagates: null when a part is
        loo = {fam: LC._mcc(cubes[fam], WLOO) for fam in cubes}
        loo["headline"] = (loo["relation"] + loo["group"]) / 2
        corr, n = _depth_counts(rows, fx)
        data[rid] = {"entry": entry, "rows": rows, "mcc": mcc, "loo": loo, "corr": corr, "n": n,
                     "scored": bundle.rescore(blob, version), "cell": _json(ROOT / entry["cell"])}
    first_2000 = _self_check(data, ci, W, L, models)
    V = {fam: np.vstack([data[m]["mcc"][fam][1] for m in models]) for fam in FAMILIES}
    P = {fam: [data[m]["mcc"][fam][0] for m in models] for fam in FAMILIES}
    op_points = {REL_OP: {m: data[m]["mcc"]["relation"][0] for m in models},
                 GRP_OP: {m: data[m]["mcc"]["group"][0] for m in models}}

    audit, flagged_bytes = _item_audit(models, rows_of, op_points, fam_of, verdicts)
    pairwise = {fam: _pairwise(models, V[fam], P[fam]) for fam in FAMILIES}
    ranks = {fam: _ranks(models, V[fam], P[fam]) for fam in FAMILIES}
    lolo = _lolo(models, data, P, pairwise, leagues)
    consistency = _consistency(rows_of, fx, W)
    repeats_doc = _repeats(repeats, rows_of, {m: data[m]["mcc"]["headline"][0] for m in models}, bundle, version)
    robustness = _robustness(repeats_doc, models)
    weighting = _weighting(models, V, P, pairwise["headline"], ranks["headline"])
    binding = {rid: _binding_run(data[rid]["rows"], data[rid]["entry"], fx, W) for rid in runs}
    cost = _cost(models, {m: data[m]["entry"] for m in models})
    kinds = sorted({DS._kind(r[k]) for rows in rows_of.values() for r in rows if r["op"] == "decide_group"
                    for k in ("gold", "pred")})
    relation = {}
    for rid in runs:
        rel = [r for r in data[rid]["rows"] if r["op"] == "decide_relation"]
        matrix = [[0] * 5 for _ in RELATIONS]
        for r in rel:
            if r["pred"] != MISSING:
                check(r["pred"] in RELATIONS, f"{rid}: relation prediction {r['pred']!r}")
                matrix[RELATIONS.index(r["gold"])][RELATIONS.index(r["pred"])] += 1
        relation[rid] = {"matrix": matrix, "missing": sum(r["pred"] == MISSING for r in rel),
                         "abstained": sum(bool(r.get("abstained")) for r in rel)}
    group = {}
    for rid in runs:
        matrix = [[0] * len(kinds) for _ in kinds]
        for r in data[rid]["rows"]:
            if r["op"] == "decide_group":
                matrix[kinds.index(DS._kind(r["gold"]))][kinds.index(DS._kind(r["pred"]))] += 1
        group[rid] = {"matrix": matrix}
    calibration = {}
    for rid in runs:
        ops = data[rid]["cell"]["score_decisions"]["ops"]
        calibration[rid] = {op: {"brier": ops[op]["brier"]["value"], "ece": ops[op]["ece"]["value"],
                                 "bins": ops[op]["ece"]["bins"]} for op in ("decide_relation", "decide_binding")}
        calibration[rid].update({"decide_group": {"probabilities": False}, "decide_reading": {"probabilities": False}})
    counts = {rid: (data[rid]["corr"], data[rid]["n"]) for rid in runs}
    n_items = data[models[0]]["n"]  # the same relation items in every run
    depth = {"buckets": list(DEPTH_BUCKETS), "runs": _depth_block(counts, W),
             "leagues_by_bucket": {b: int((n_items[:, i] > 0).sum()) for i, b in enumerate(DEPTH_BUCKETS)}}
    n4 = n_items[:, DEPTH_BUCKETS.index("4+")]
    doc = {
        "schema": SCHEMA,
        "release": RELEASE,
        "based_on": {"release": BASED_ON, "leaderboard": {"path": _rel(LEADERBOARD), "sha256": _sha(lb_bytes)},
                     "predictions": dict(sorted(predictions.items())),
                     "fixture": {"name": fixture_doc["name"], "revision": fixture_doc["version"], "content_hash": content_hash}},
        "method": {
            "bootstrap": {"unit": "league", "leagues": L, "B": B, "seed": SEED, "rng": f"league_ci:{SEED}"},
            "first_2000_equal_published": first_2000,
            "b_comparison": "the leaderboard's ci95 and the CI file are B = 2000 intervals (their draws are the first 2000 of this "
                            "module's B = 20000); the rank intervals, pairwise intervals, p-values and depth intervals here are "
                            "the B = 20000 paired draws",
            "uncertainty": "league resampling of the one fixture is the only uncertainty source; one deterministic run per model",
            "interval": "95% percentile interval (numpy, linear) of the valid draws",
            "p_value": "per pair, delta = a - b per draw: p = min(1, 2 * (1 + min(#delta<=0, #delta>=0)) / (1 + B_valid))",
            "correction": "holm",
            "family": "per metric: headline, relation, group; each family is corrected separately, not against the others",
            "family_size": FAMILY_SIZE,
            "bootstrap_draws": B,
            "published_draws": PUBLISHED_B,
            "leagues": L,
            "seed": SEED,
            "degenerate": {"min_n": MIN_N, "min_valid_share": MIN_VALID_SHARE},
            "halves": {"strong_ranks": [1, HALF], "weak_ranks": [HALF + 2, 2 * HALF + 1]},
            "judges": JUDGES,
            "cost": "decisions is the cell's decision count (one per decision item; a reading's base and alt rows count once); "
                    "decisions_per_s is decisions over the leaderboard's build_wall_s",
            "resolved_pairs": {fam: sum(pr["resolved"] for pr in pairwise[fam]) for fam in FAMILIES},
            "alpha": ALPHA,
            "resolved": "Holm-adjusted p <= alpha",
            "diff": "diff = a minus b (point and draws); the README shows higher minus lower",
            "higher": "the model with the larger point estimate when resolved; null when not resolved",
            "lolo": lolo,
            "rank": "rank is the point rank among the 15 models; ci95 is the 2.5% (method lower) to 97.5% (method higher) range "
                    "of the per-draw rank; median by method nearest",
            "headline": "relation_group_mcc: the mean of the relation and group MCC over the same draws",
            "families": {f: [m for m in models if fam_of[m] == f] for f in sorted(set(fam_of.values()))},
            "item_audit": "per op the halves are the models ranked by the op's own MCC; all_wrong is over the 15 models; "
                          "inverted is computed on residuals within (op, gold class, stratum) cells; see item_audit.null_inverted_rate",
            "null_inverted_rate": {"draws": NULL_SPLITS, "seed": NULL_SEED,
                                   "population": "all item_audit items; shares of inverted items under random 7-vs-7 splits"},
            "calibration": "Brier is summed over the options (range 0 to 2); calibration is copied from each cell's "
                           "score_decisions, not rescored",
            "depth_4plus_items": int(n4.sum()),
            "depth_4plus_leagues": int((n4 > 0).sum()),
            "runs": "the 15 models in headline rank order, then the baselines with decisions (heuristic, lexical, embedding, "
                    "gold_view, hf)",
            "flagged_list": {"path": _rel(FLAGGED), "sha256": _sha(flagged_bytes)},
            "repeats": "the published run r1 is the reference and is not listed; identical, delta_vs_published and flip_rate are "
                       "computed over the 10,716 prediction rows and the published headline",
        },
        "models": models,
        "runs": list(runs),
        "pairwise": pairwise,
        "rank_intervals": ranks,
        "depth": depth,
        "confusion": {"relation": {"classes": list(RELATIONS), "rows": "gold", "columns": "predicted", "runs": relation},
                      "group": {"classes": kinds, "rows": "gold kind", "columns": "predicted kind", "runs": group}},
        "calibration": calibration,
        "consistency": consistency,
        "item_audit": audit,
        "repeats": repeats_doc,
        "binding_detection": {"definition": BINDING_DEFINITION, "runs": binding},
        "weighting": weighting,
        "robustness": robustness,
        "cost": cost,
    }
    return _rounded(doc), flagged_bytes


def dump(doc: dict) -> bytes:
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _fmt(x, digits: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def _interval_text(block, digits: int = 3) -> str:
    if block is None or block.get("point") is None:
        return "n/a"
    text = _fmt(block["point"], digits)
    return text if block.get("ci95") is None else f"{text} [{_fmt(block['ci95'][0], digits)}, {_fmt(block['ci95'][1], digits)}]"


def _pair_line(pr: dict, B_draws: int) -> str:
    """One resolved pair, with the difference oriented as higher minus lower and the Holm p (a floor when no draw lies on the
    other side)."""
    hi_is_a = pr["higher"] == pr["a"]
    sign = 1 if hi_is_a else -1
    lo, hi = (pr["diff"]["ci95"][0], pr["diff"]["ci95"][1]) if hi_is_a else (-pr["diff"]["ci95"][1], -pr["diff"]["ci95"][0])
    lower = pr["b"] if hi_is_a else pr["a"]
    other = round(pr["p"] * (B_draws + 1) / 2 - 1)  # draws on the other side: p = 2 (1 + k) / (1 + B)
    if other == 0:
        p_text = f"p_holm < {math.ceil(pr['p_holm'] * 1e4) / 1e4:.4f} (0 of {B_draws:,} draws on the other side)"
    else:
        p_text = f"p_holm {pr['p_holm']:.4f} ({other:,} of {B_draws:,} draws on the other side)"
    return (f"- {pr['higher']} over {lower}: higher minus lower {_fmt(sign * pr['diff']['point'])} "
            f"[{_fmt(lo)}, {_fmt(hi)}], {p_text}")


def _names() -> dict:
    """The display name of every leaderboard row (the analysis keys rows by id; prose uses the names)."""
    lb = _json(LEADERBOARD)
    return {e["id"]: e["display_name"] for e in lb["models"] + lb["baselines"]}


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _signed(x) -> str:
    return "n/a" if x is None else f"{x:+.4f}"


def _weighting_lines(doc: dict, names: dict) -> list[str]:
    w = doc["weighting"]
    schemes = [s["name"] for s in w["schemes"]]
    lines = ["#### Weighting of relation and group MCC", "",
             "The headline weights the relation and group MCC equally (the published scheme, w = 0.5). Each scheme weights the "
             "relation MCC by w and the group MCC by 1 - w, with its own rank intervals and its own Holm family of 105 pairs. The "
             "published column reproduces the headline ranks and pairs.", "",
             "| Model | " + " | ".join(f"{s['name']} ({s['relation']:g} / {s['group']:g})" for s in w["schemes"]) + " |",
             "|---|" + "---|" * len(schemes)]
    for m in doc["models"]:
        cells = [f"{w['ranks'][s][m]['rank']} [{w['ranks'][s][m]['ci95'][0]}, {w['ranks'][s][m]['ci95'][1]}]" for s in schemes]
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    lines += ["", "Holm-resolved pairs per scheme (of 105): " + ", ".join(f"{s} {len(w['resolved'][s])}" for s in schemes) + ".", "",
              f"Robust pairs: {len(w['robust_pairs'])} are resolved under every scheme with the same winner (weighting.robust_pairs "
              "in analysis.json).", "",
              f"Weight-dependent pairs ({len(w['weight_dependent_pairs'])}): the winner differs across schemes, or the pair is "
              "resolved under some schemes only.", ""]
    for pr in w["weight_dependent_pairs"]:
        by = ", ".join(f"{s} {names[h] if h else 'not resolved'}" for s, h in pr["higher_by_scheme"].items())
        lines.append(f"- {names[pr['a']]} vs {names[pr['b']]}, higher by scheme: {by}.")
    return lines + [""]


def _label(rid: str, doc: dict) -> str:
    """A run id in a table: the system_one models bare; the baselines and the gold_view ceiling labelled as such."""
    if rid in doc["models"]:
        return rid
    return f"{rid} (gold-backed ceiling)" if rid == "gold_view" else f"{rid} (baseline)"


def verdict_facts(verdicts: dict) -> dict:
    """Counts the item-audit prose quotes, from verdicts.json (the flagged items judged; the controls apart). Pure."""
    items = verdicts["items"]
    flagged = [r for r in items if r["group"] != "control"]
    controls = [r for r in items if r["group"] == "control"]
    suspects = [r for r in flagged if r["verdict"] == "suspect_label"]
    atmost_none = [r for r in suspects if r["op"] == "decide_group" and r["gold"].startswith("at_most_k")
                   and r["judge1"] == "none" and r["judge2"] == "none"]
    return {"n_flagged_judged": len(flagged), "n_suspect": len(suspects), "n_atmost_none": len(atmost_none),
            "n_controls": len(controls), "controls_suspect": sum(r["verdict"] == "suspect_label" for r in controls),
            "controls_groups": sum(r["op"] == "decide_group" for r in controls),
            "both_agree_nongold": sum(r["judge1"] == r["judge2"] and r["judge1"] != r["gold"] for r in suspects)}


def upper_bound_zero_of(n: int, alpha: float = 0.05) -> float:
    """The upper limit of the exact (Clopper-Pearson) two-sided 95% interval for a rate when 0 of n are positive: 1 - (alpha / 2) ** (1 / n).
    (The one-sided 95% bound would be 1 - alpha ** (1 / n), about 6% for n = 50.)"""
    return 1 - (alpha / 2) ** (1 / n)


def _binding_lines(doc: dict) -> list[str]:
    runs = doc["binding_detection"]["runs"]
    lines = ["#### Binding detection", "",
             "Does each run's own probability of an unresolved binding separate the unresolved items from the bound ones? AUROC is "
             "the Mann-Whitney statistic (a tied pair counts one half); average precision is the step-wise area under the "
             "precision-recall curve. Both have league-bootstrap 95% intervals over the same draws. Flagged counts items predicted "
             "unresolved (argmax of the two probabilities). MCC is the leaderboard's binding point. Runs without probabilities "
             "(the baselines and the gold_view ceiling) have no AUROC.", ""]
    ref = runs["d3_27b-q8_0"]
    lo, hi = ref["auroc"]["ci95"]
    same = [m for m in doc["models"] if m != "d3_27b-q8_0" and runs[m]["auroc"] and runs[m]["auroc"]["ci95"]
            and runs[m]["auroc"]["ci95"][0] > 0.5 and runs[m]["flagged"] == 0]
    lines += [f"D3 27B's probabilities rank unresolved bindings above bound ones (AUROC {ref['auroc']['point']:.3f} "
              f"[{lo:.3f}, {hi:.3f}]), but its decisions flag {ref['flagged']} (MCC {ref['mcc']:.3f}): the ranking signal is not used "
              "at its decision threshold."]
    if same:
        lines.append("The same pattern (AUROC interval above 0.5, no flagged items) holds for " + ", ".join(same) + ".")
    lines += ["", "| Run | Items | Unresolved (prevalence) | Flagged | Binding MCC | AUROC (95%) | Average precision (95%) |",
              "|---|---|---|---|---|---|---|"]
    for rid, b in runs.items():
        lines.append(f"| {_label(rid, doc)} | {b['n']} | {b['n_unresolved']} ({b['prevalence']:.4f}) | {b['flagged']} | "
                     f"{_fmt(b['mcc'])} | {_interval_text(b['auroc'])} | {_interval_text(b['average_precision'])} |")
    return lines + [""]


def _robustness_lines(doc: dict, names: dict) -> list[str]:
    rob = doc["robustness"]
    s = rob["summary"]
    p, pm, rr, vk = s["presentation"], s["presentation_matched"], s["run_to_run"], s["backend"]
    lines = ["#### Robustness: option order, run to run and backend", "",
             "Deltas are headline changes against the published run. The option-shuffle delta is one pass with the answer options in a "
             "seeded random order (one seed, 20261101). Run to run is the largest change of the r2 and r3 repeats. Backend is the "
             "Vulkan pass of d3:9b. Flip rate is the share of the 10,716 decision rows whose label differs. The published run is "
             "the r1 reference in results/v0.1.0 (its predictions and cell are not copied here).", "",
             "| Model | Option shuffle delta | Shuffle flip rate | Run to run max abs delta | Run to run max flip rate | Vulkan delta | "
             "Vulkan flip rate |", "|---|---|---|---|---|---|---|"]
    for m in doc["models"]:
        e = rob["models"].get(m)
        if e is None:
            continue
        sh, run, vkr = e["shuffle"], e["run_to_run"], e["vulkan"]
        lines.append(f"| {_label(m, doc)} | {_signed(sh and sh['delta'])} | {_pct(sh and sh['flip_rate'])} | "
                     f"{_fmt(run and run['max_abs_delta'], 4)} | {_pct(run and run['max_flip_rate'])} | "
                     f"{_signed(vkr and vkr['delta'])} | {_pct(vkr and vkr['flip_rate'])} |")
    # the one-pass sentence, its counts and numbers taken from the data
    lines += ["", f"One option-shuffle pass per model ({p['n_models']} models) moves the headline by up to ±{p['max_abs_delta']:.4f} "
              f"(median {p['median_abs_delta']:.4f})."]
    repeated = [m for m in doc["models"] if rob["models"].get(m, {}).get("run_to_run")]
    identical = [m for m in repeated if rob["models"][m]["run_to_run"]["max_flip_rate"] == 0]
    differed = [m for m in repeated if m not in identical]
    if repeated:
        parts = [f"for the {len(repeated)} repeated models ({', '.join(repeated)}), the r2 and r3 repeats were bit-identical for "
                 f"{len(identical)}" + (f" ({', '.join(identical)})" if identical else "")]
        if differed:
            parts.append(f"and differed by at most ±{rr['max_abs_delta']:.4f} for {', '.join(differed)}")
        lines.append("Run to run, " + ", ".join(parts[:1]) + (" " + parts[1] if len(parts) > 1 else "") + ".")
    comparison = "larger than run noise" if pm["max_abs_delta"] > rr["max_abs_delta"] else "not larger than run noise"
    lines.append(f"Over the {pm['n_models']} models with run-to-run repeats (presentation_matched), the shuffle moves the headline by "
                 f"at most ±{pm['max_abs_delta']:.4f}, against a run-to-run change of at most ±{rr['max_abs_delta']:.4f}: "
                 f"{comparison}.")
    if vk is not None:
        lines.append(f"Backend: the Vulkan pass moves the headline by ±{vk['max_abs_delta']:.4f} ({vk['n_models']} model).")
    disclosure = ("Disclosure: the first d3:9b repeat attempt ran on Ollama's Vulkan backend by mistake (runs/20261101-repeat); the "
                  "CUDA repeats r2 and r3 were run afterwards in a separate folder (runs/20261101-cuda-repeat). The Vulkan passes are "
                  "kept as a backend diagnostic, and the headline is the CUDA run.")
    return lines + ["", disclosure, ""] + [f"- {text}" for key, text in doc["repeats"].items() if key.startswith("note_")] + [""]


def _judging_text(doc: dict, judged: dict) -> str:
    """The judging paragraph, from the verdicts file (the counts the prose quotes). The controls are reported apart."""
    facts = verdict_facts(json.loads((ROOT / judged["path"]).read_text(encoding="utf-8")))
    if facts["controls_suspect"] == 0:
        controls = (f"0 of {facts['n_controls']} controls were labelled suspect (exact 95% confidence interval 0% to "
                    f"{100 * upper_bound_zero_of(facts['n_controls']):.0f}%; only {facts['controls_groups']} controls are groups)")
    else:
        controls = f"{facts['controls_suspect']} of {facts['n_controls']} controls were labelled suspect"
    return (f"the item audit used model judges ({JUDGES['count']} {JUDGES['model']} judge sets), not people. {judged['n_judged']} of "
            f"{judged['n_flagged']} flagged items were judged ({judged['sample_rule']}): suspect label "
            f"{judged['counts']['suspect_label']}, hard but valid {judged['counts']['hard_valid']}, "
            f"unclear {judged['counts']['unclear']}. Of the {facts['n_suspect']} suspect labels, {facts['n_atmost_none']} are group "
            "items with gold at_most_k where both judges answered none: an untested hypothesis, a shared misreading of the group-kind "
            f"taxonomy or gold errors. {controls}. Flags and suspect labels are candidates for review, not confirmed label errors.")


def _cost_lines(doc: dict) -> list[str]:
    cost = doc["cost"]
    lines = ["#### Cost", "", f"Hardware: {cost['hardware']}. {cost['note']}", "",
             "Decisions are the cell's decision count, one per decision item (a reading's base and alt rows count once). Decisions "
             "per second is decisions over the build wall seconds; build minutes are the same time over 60.", "",
             "| Model | Params | Quant | Size (GB) | Build (min) | Decisions | Decisions/s |", "|---|---|---|---|---|---|---|"]
    for m in doc["models"]:
        r = cost["runs"][m]
        size = "n/a" if r["size_bytes"] is None else f"{r['size_bytes'] / 1e9:.2f}"
        lines.append(f"| {m} | {r['params'] or 'n/a'} | {r['quant'] or 'n/a'} | {size} | {r['build_wall_s'] / 60:.1f} | "
                     f"{r['decisions']} | {r['decisions_per_s']:.2f} |")
    return lines + [""]


def markdown(doc: dict) -> str:
    """The analysis section of results/README.md, with its markers. Its numbers are the analysis.json numbers."""
    method, ranks, pairs = doc["method"], doc["rank_intervals"], doc["pairwise"]
    names = _names()
    audit, cons, B_draws = doc["item_audit"], doc["consistency"], method["bootstrap"]["B"]
    lines = [START, "", "### Analysis (v0.1.1, same measurements)", "",
             "Every table in this section is computed by `tools/eval/release_analysis.py` from the committed v0.1.0 predictions, "
             "cells and fixture; the numbers are in [v0.1.1/analysis.json](v0.1.1/analysis.json). No score changes.", "",
             "The leaderboard's intervals are the published league-bootstrap intervals (B = 2000). The rank, pairwise and depth "
             "intervals and the p-values in this section are the paired draws of the same resampling at B = 20000 (the first 2000 "
             "are the published draws). Uncertainty comes only from resampling the 18 leagues of one fixture: each model has one "
             "deterministic run, and the three metric families (headline, relation, group) are corrected separately, not against "
             "each other.", "",
             "#### Rank intervals", "",
             "Rank 1 is the highest point. The interval is the 2.5% to 97.5% range of the rank over the draws.", "",
             "| Model | Headline rank (95%) | Relation rank (95%) | Group rank (95%) |", "|---|---|---|---|"]
    for m in doc["models"]:
        cells = [f"{ranks[f][m]['rank']} [{ranks[f][m]['ci95'][0]}, {ranks[f][m]['ci95'][1]}] (median {ranks[f][m]['median']})"
                 for f in FAMILIES]
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    lines += ["", "#### Holm-resolved pairs", "",
              "Differences are higher minus lower. The Holm p-value is adjusted within each family; when no draw lies on the other "
              "side it is a floor. Leave-one-league-out: each resolved pair's point difference is recomputed with each of the "
              f"{method['bootstrap']['leagues']} leagues left out.", ""]
    for fam in FAMILIES:
        lolo = method["lolo"][fam]
        lines.append(f"**{fam.capitalize()}**: {method['resolved_pairs'][fam]} of {method['family_size']} pairs are resolved "
                     f"(Holm, alpha 0.05). Leave-one-league-out: {lolo['survive']} of {lolo['resolved']} keep their sign in all "
                     f"{method['bootstrap']['leagues']} recomputations.")
        for flip in lolo["flips"]:
            lines.append(f"- sign flips when a league is left out, {flip['a']} vs {flip['b']}: {', '.join(flip['leagues'])}")
        for pr in pairs[fam]:
            if pr["resolved"]:
                lines.append(_pair_line(pr, B_draws))
        lines.append("")
    lines += _weighting_lines(doc, names)
    n4, k4 = method["depth_4plus_items"], method["depth_4plus_leagues"]
    lines += ["#### Depth (accuracy by gold depth, with intervals)", "",
              "A `*` marks a degenerate cell (n < 30 or valid draws below 99%); its interval is shown as n/a. The 4+ bucket holds "
              f"{n4} items in {k4} league{'' if k4 == 1 else 's'}.", "",
              "| Run (depth, 95% interval) | " + " | ".join(doc["depth"]["buckets"]) + " |", "|" + "---|" * (len(DEPTH_BUCKETS) + 1)]
    for rid, cells in doc["depth"]["runs"].items():
        out = []
        for c in cells.values():
            if c["degenerate"]:
                out.append("n/a" if c["point"] is None else f"{_fmt(c['point'])}* (n/a)")
            else:
                out.append(_interval_text(c))
        lines.append(f"| {_label(rid, doc)} | " + " | ".join(out) + " |")
    lines += ["", "#### Consistency", "",
              f"An answer is conflicting if it belongs to a minimal conflict of at most {cons['max_witness']} answers under either "
              "predicate: the main predicate (logical UNSAT, a forced suite outcome, a cardinality or an invalid group) or the UNSAT-only "
              "predicate. The conflicting share is that union, so the UNSAT share is part of it in total and in every op. The main-predicate "
              "count is shown on its own. The smallest-witness columns count the main-predicate answers by the size of their smallest "
              "witness under the main predicate, so they sum to the main-predicate count; an answer only in a minimal UNSAT-only conflict "
              "has no main witness. Each share is over claim-bearing answers, with a league-bootstrap 95% interval. Contradiction "
              "detected: the detector finds a conflict in the whole answer set (sound, not complete).", "",
              "| Run (consistency) | Claim-bearing | Conflicting share (95%) | UNSAT share (95%) | Main-predicate conflicts | "
              "Contradiction detected | Smallest witness 1 / 2 / 3 (main predicate) |", "|---|---|---|---|---|---|---|"]
    for rid, c in cons["runs"].items():
        sizes = c["by_size"]
        lines.append(f"| {_label(rid, doc)} | {c['claim_bearing']} | "
                     f"{_interval_text({'point': c['share'], 'ci95': c['ci95']})} | "
                     f"{_interval_text({'point': c['share_unsat'], 'ci95': c['ci95_unsat']})} | {c['conflicting_main']} | "
                     f"{'yes' if c['contradiction_detected'] else 'no'} | {sizes['1']} / {sizes['2']} / {sizes['3']} |")
    gv = cons["runs"]["gold_view"]
    lines += ["", f"Stop rule: gold_view (gold-backed ceiling) has {gv['conflicting']} conflicting answers, {gv['conflicting_unsat']} of them UNSAT, and "
              f"contradiction detected: {'yes' if gv['contradiction_detected'] else 'no'} (it must be 0, 0 and no).", "",
              "#### Calibration (relations and bindings)", "",
              "Brier is summed over the options (range 0 to 2). Calibration is copied from each cell's score_decisions, not rescored; "
              "groups and readings have no probabilities here.", "",
              "| Run (calibration) | Relation Brier | Relation ECE | Binding Brier | Binding ECE |", "|---|---|---|---|---|"]
    for rid, c in doc["calibration"].items():
        rel, bind = c["decide_relation"], c["decide_binding"]
        lines.append(f"| {_label(rid, doc)} | {_fmt(rel['brier'])} | {_fmt(rel['ece'])} | {_fmt(bind['brier'])} | {_fmt(bind['ece'])} |")
    lines += _binding_lines(doc)
    nul, observed = audit["null_inverted_rate"], audit["inverted"] / audit["items"]
    judged = audit["judged"]
    judging = "pending (no verdicts file yet)." if judged is None else _judging_text(doc, judged)
    lines += ["", "#### Item audit (candidates for review)", "",
              "These flags are candidates for review only. They are not label errors: a flag says the items look hard or "
              "inconsistent with the models, and a reviewer decides. `all_wrong`: all 15 models are wrong. "
              "`weak-over-strong (inverted)`: within each (op, gold class, stratum) cell each model's correctness is centred on its "
              "cell mean; the weak half's mean residual is above the strong half's and the residuals' point-biserial correlation with "
              "the op's own MCC is below 0. The halves rank the models by each op's MCC: top 7 strong, bottom 7 weak, rank 8 in "
              "neither.", "",
              f"{audit['flagged']} of {audit['items']} items are flagged: {audit['all_wrong']} all wrong, "
              f"{audit['inverted']} weak-over-strong (inverted), of which {audit['inverted_not_all_wrong']} are not all wrong. The list "
              "is in [flagged.jsonl](../docs/audits/v0.1.1-item-audit/flagged.jsonl).", "",
              "The inverted rule is pre-registered and is not changed here. An all-wrong item tends to satisfy it mechanically, because "
              f"each cell mean includes the item itself: {audit['all_wrong_and_inverted']} of the {audit['all_wrong']} all-wrong items also "
              "carry the inverted flag. So the overlap of the two flags is not evidence, and the null comparison below inherits it.", "",
              "| Op | Items | Flagged | All wrong | Weak-over-strong (inverted) | Inverted, not all wrong |", "|---|---|---|---|---|---|"]
    for op, c in audit["by_op"].items():
        lines.append(f"| {op} | {c['items']} | {c['flagged']} | {c['all_wrong']} | {c['inverted']} | {c['inverted_not_all_wrong']} |")
    for op in OP_OF.values():
        half = audit["halves"][op]
        lines.append(f"\n{op} halves: strong {', '.join(half['strong'])}; rank 8 {half['rank_8']}; weak {', '.join(half['weak'])}.")
    lines += ["", f"Null rate: under {method['null_inverted_rate']['draws']} random 7-vs-7 splits of the 15 models (seed "
              f"{method['null_inverted_rate']['seed']}), the share of items flagged weak-over-strong (inverted) is mean "
              f"{nul['mean']:.4f} (95th percentile {nul['p95']:.4f}), against an observed {observed:.4f} over the same "
              f"{audit['items']} items.", "",
              "Judging: " + judging, ""]
    lines += _robustness_lines(doc, names) + _cost_lines(doc) + [END]
    return "\n".join(lines)


def splice(text: str, block: str) -> str:
    """The README with its analysis section replaced, or inserted at the end of 'Which differences are resolved'."""
    start, end = text.find(START), text.find(END)
    if start >= 0 or end >= 0:
        check(0 <= start < end, "the README's analysis markers are not in order")
        return text[:start] + block + text[end + len(END):]
    heading = text.find("\n## Which differences are resolved")
    check(heading >= 0, "results/README.md has no 'Which differences are resolved' section")
    nxt = text.find("\n## ", heading + 1)
    check(nxt >= 0, "results/README.md has no section after 'Which differences are resolved'")
    return text[:nxt + 1] + block + "\n\n" + text[nxt + 1:]


def _same(path: Path, data: bytes) -> bool:
    if not path.is_file() or path.read_bytes() != data:
        print(f"release_analysis: {_rel(path)} differs from the rebuild", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ANALYSIS)
    ap.add_argument("--flagged", type=Path, default=FLAGGED)
    ap.add_argument("--readme", type=Path)
    ap.add_argument("--repeats", type=Path, default=REPEATS)
    ap.add_argument("--check", action="store_true", help="compare the rebuild with the committed files; exit 1 on a difference")
    a = ap.parse_args(argv)
    started = time.time()
    try:
        doc, flagged = build(a.repeats)
        out = dump(doc)
        readme = None
        if a.readme is not None:
            readme = splice(a.readme.read_bytes().decode("utf-8"), markdown(doc)).encode("utf-8")
    except (CheckError, OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"release_analysis: {exc}", file=sys.stderr)
        return 1
    if a.check:
        ok = _same(a.out, out) & _same(a.flagged, flagged)
        if readme is not None:
            ok = ok & _same(a.readme, readme)
        print(f"release_analysis: check {'passed' if ok else 'FAILED'} in {time.time() - started:.1f} s", file=sys.stderr)
        return 0 if ok else 1
    for path, data in ((a.out, out), (a.flagged, flagged), (a.readme, readme)):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            print(f"release_analysis: wrote {_rel(path)}", file=sys.stderr)
    print(f"release_analysis: done in {time.time() - started:.1f} s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

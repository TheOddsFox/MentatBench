"""Recomputes the Mentat-Hard test results (docs/results/mentat-hard-20261030.json) from the run directories.

usage, from the repo root:
  python docs/results/mentat-hard-results/build_results.py [--runs DIR] [--baselines DIR] [--hf DIR] [--fixture DIR]
         [--prereg JSON] [--ci JSON] [--out JSON]

MEASUREMENT ONLY (reads <fixture>/gold; product code never does). Each run's submission.build/decisions.jsonl is scored
with decision_scoring.load and decision_scoring.score (fixture version 3 headline). The result must equal the run's own
score_decisions.json, and Q_N comes from score.json. The relation depth is joined from gold/suite_labels.jsonl. The
intervals are read from the league_ci output (--ci), whose points must equal the recomputed ones. Any mismatch stops the
script with exit 1. Runs without decisions (empty, oracle) get Q_N only.

The runs are not in git: the defaults resolve only where runs/ exists (the main checkout). Pass the paths otherwise.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "python"))

from mentat_bench import corrections, decision_scoring, ir  # noqa: E402

TOL = 1e-9  # the recomputed decision score against the run's own score_decisions.json
CI_TOL = 1e-5  # league_ci rounds its values to 6 decimals
DATE = "2026-10-30"
RUN_PREFIX = "20261030-test"
REFERENCE = "d3_27b-q8_0"
LAYOUT = "relation-group-headline/1"
CODE_COMMIT = "0bff47f"
DEPTH_BUCKETS = ("0", "1", "2", "3", "4+", "NONE")
BASELINES = ("heuristic", "gold_view", "lexical", "embedding", "empty", "oracle")  # runs/20261030-test-baselines
RULES = "heuristic"  # the prereg's "rules" baseline
FAMILY = ("d3:2b-q8_0", "d3:4b-q8_0", "d3:9b-q8_0", "d3:27b-q8_0")


class CheckError(Exception):
    """A value that does not match its source: the script stops."""


def check(ok: bool, msg: str) -> None:
    if not ok:
        raise CheckError(msg)


def jload(path: Path) -> dict:
    check(path.is_file(), f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def same(a, b) -> bool:
    """Equal trees: floats within TOL, None only against None."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) <= TOL
    return a == b


def rnd(x):
    if isinstance(x, dict):
        return {k: rnd(v) for k, v in x.items()}
    if isinstance(x, list):
        return [rnd(v) for v in x]
    return round(x, 6) if isinstance(x, float) else x


def label_depths(fixture: Path) -> dict[str, int | None]:
    """The inference depth of every relation label (None for NONE, which has no depth)."""
    depths = {}
    for row in ir.read_jsonl(fixture / "gold" / "suite_labels.jsonl"):
        if row["op"] == "decide_relation":
            depths[row["qid"]] = row.get("depth")
    return depths


def depth_table(items: list[dict], depths: dict[str, int | None]) -> dict:
    """Relation accuracy by gold depth. Missing answers count as wrong, as in decision_scoring."""
    table = {b: {"n": 0, "correct": 0} for b in DEPTH_BUCKETS}
    for it in items:
        if it["op"] != "decide_relation":
            continue
        depth = depths[it["qid"]]
        if it["truth"] == "NONE":
            check(depth is None, f"{it['qid']}: a NONE label with depth {depth!r}")
            bucket = "NONE"
        else:
            check(depth in (0, 1, 2, 3, 4), f"{it['qid']}: a named relation with depth {depth!r}")
            bucket = "4+" if depth >= 4 else str(depth)
        table[bucket]["n"] += 1
        table[bucket]["correct"] += int(it["truth"] == it["pred"])
    for row in table.values():
        row["accuracy"] = row["correct"] / row["n"] if row["n"] else None
    check(sum(row["n"] for row in table.values()) == sum(it["op"] == "decide_relation" for it in items),
          "depth buckets do not cover every relation row")
    return table


def repair_fields(build: dict) -> dict:
    """The build's repair drops. They change the repaired base (Q_N), not the decisions the headline scores."""
    gd, gc = build.get("groups_dropped"), build.get("groups_committed")
    if gc:
        check(abs(build.get("groups_dropped_pct") - 100 * gd / gc) <= 0.005, f"groups_dropped_pct differs from {gd}/{gc}")
    return {"groups_dropped": gd, "groups_committed": gc, "groups_dropped_pct": build.get("groups_dropped_pct"),
            "relations_dropped": build.get("relations_dropped")}


def abstention_fields(cell: Path, items: list[dict], scored: dict, stats: dict) -> dict:
    """A model cell's relation abstentions for length, from its decisions. A relation abstention is a decision whose only key
    is "relation" (the child writes NONE with no conf or probs). Its count must equal the child's abstained_overlength count,
    and the NONE confusion must equal the correct NONE count. Group abstentions are the child's count only: an empty group list
    with null probs is not an unambiguous marker (clef-flash has such rows and no abstentions), so they are not counted here."""
    rows, _ = decision_scoring.read_decisions(cell / "submission.build" / "decisions.jsonl")
    by_qid = {r["qid"]: r for r in rows if r.get("qid") is not None}
    rel_abst = {q for q, r in by_qid.items() if r.get("op") == "decide_relation" and set(r["decision"]) == {"relation"}}
    grp_empty = {q for q, r in by_qid.items() if r.get("op") == "decide_group" and r["decision"].get("groups") == []}
    grp_null_probs = {q for q in grp_empty if by_qid[q].get("probs") is None}
    check(len(rel_abst) == stats["ops"]["decide_relation"]["abstained_overlength"],
          f"{cell.name}: {len(rel_abst)} relation abstentions in the decisions, child stats say "
          f"{stats['ops']['decide_relation']['abstained_overlength']}")
    rel_items = [it for it in items if it["op"] == "decide_relation"]
    none_answers = [it for it in rel_items if it["pred"] == "NONE"]
    correct_none = [it for it in none_answers if it["truth"] == "NONE"]
    check(len(correct_none) == scored["ops"]["decide_relation"]["confusion"]["NONE"]["NONE"],
          f"{cell.name}: correct NONE count differs from the confusion matrix")
    abstained_none = [it for it in none_answers if it["qid"] in rel_abst]
    abstained_correct = [it for it in correct_none if it["qid"] in rel_abst]
    return {"relation_abstained": len(rel_abst), "group_abstained_child_stats": stats["ops"]["decide_group"]["abstained_overlength"],
            "group_empty_lists": len(grp_empty), "group_empty_null_probs": len(grp_null_probs),
            "relation_none_answers": len(none_answers),
            "relation_none_abstained": len(abstained_none), "relation_none_not_abstained": len(none_answers) - len(abstained_none),
            "correct_none": len(correct_none), "correct_none_abstained": len(abstained_correct),
            "correct_none_not_abstained": len(correct_none) - len(abstained_correct)}


def cell_record(cell: Path, fixture: Path, fixture_hash: str, version: int, depths: dict) -> dict:
    """Everything the summary needs from one cell: the recomputed decision score (checked against score_decisions.json),
    Q_N and the build and provenance files."""
    score = jload(cell / "score.json")
    build = jload(cell / "build_summary.json")
    cell_doc = jload(cell / "cell.json")
    check(cell_doc["fixture_stamp"]["content_hash"] == fixture_hash, f"{cell.name}: fixture hash differs")
    check(cell_doc["runtime"]["repo"]["commit"].startswith(CODE_COMMIT), f"{cell.name}: code commit differs")
    check(cell_doc["runtime"]["repo"]["dirty"] is False, f"{cell.name}: built from a dirty tree")
    check(cell_doc.get("base_repaired") == build.get("base_repaired"), f"{cell.name}: base_repaired differs between files")
    repaired = build.get("base_repaired")
    rec = {"cell": cell.name, "Q_N": score["Q_N"], "base_repaired": repaired,
           "strict_Q_N": None if repaired is None else (0.0 if repaired else score["Q_N"]),
           "build_wall_s": build.get("wall_seconds"), "build_step_s": cell_doc.get("step_seconds", {}).get("build"),
           "code_commit": cell_doc["runtime"]["repo"]["commit"]}
    items = scored = None
    if (cell / "score_decisions.json").is_file():
        items, counts = decision_scoring.load(fixture / "gold" / "suite_labels.jsonl", fixture / "candidates.jsonl",
                                              cell / "submission.build" / "decisions.jsonl")
        scored = decision_scoring.score(items, counts, version)
        check(same(scored, jload(cell / "score_decisions.json")),
              f"{cell.name}: the recomputed decision score differs from score_decisions.json")
        ops = scored["ops"]
        rel = ops["decide_relation"]["by_split"]
        rec.update({
            "headline": scored["headline"], "counts": counts, "depth": depth_table(items, depths),
            "coverage": {op: ops[op]["coverage"] for op in ops}, "seen_relation_mcc": rel["seen"]["mcc"],
            "held_out_relation_mcc": rel["held_out"]["mcc"],
        })
    policy_path = cell / "submission" / "build_policy.json"
    rec["policy"] = jload(policy_path) if policy_path.is_file() else None
    stats_path = cell.parent / f"{cell.name}.meta" / "child_stats.json"
    rec["child_stats"] = jload(stats_path) if stats_path.is_file() else None
    rec["repair"] = repair_fields(build)
    rec["abstention"] = (abstention_fields(cell, items, scored, rec["child_stats"])
                         if rec["child_stats"] is not None and items is not None else None)
    return rec


def run_row(name: str, rec: dict, ci: dict, prereg_model: dict | None) -> dict:
    """One output row, a model or a baseline. Every point is checked against the league_ci output of the same run."""
    in_ci = name in ci["runs"]  # empty and oracle are not in the league_ci run: points only, no interval
    metrics = ci["runs"][name]["metrics"] if in_ci else {}
    values = {"Q_N": rec["Q_N"]}
    if "headline" in rec:
        h = rec["headline"]
        values.update({"relation_group_mcc": h["relation_group_mcc"], "relations": h["parts"]["relation_mcc"],
                       "groups": h["parts"]["group_mcc"], "bindings": h["secondary"]["binding_mcc"],
                       "readings": h["diagnostics"]["reading_mcc"], "held_out_relations": rec["held_out_relation_mcc"]})
    intervals = {}
    for key, value in values.items():
        if not in_ci:
            intervals[key] = {"point": value, "ci95": None}
            continue
        point = metrics[key]["point"]
        check((value is None) == (point is None) and (value is None or abs(value - point) <= CI_TOL),
              f"{name}: league_ci point for {key} is {point}, recomputed {value}")
        intervals[key] = {"point": value, "ci95": None if point is None else [metrics[key]["lo"], metrics[key]["hi"]]}
    gaps = None
    if in_ci and name != ci["reference"]:
        gaps = {label: {"point": ci["gaps"][name][key]["point"], "ci95": [ci["gaps"][name][key]["lo"], ci["gaps"][name][key]["hi"]]}
                for key, label in (("relation_group_mcc", "headline"), ("relations", "relation_mcc"), ("groups", "group_mcc"),
                                   ("bindings", "binding_mcc"), ("Q_N", "Q_N")) if key in ci["gaps"][name]}
    out = {"run": name, "gap_to_reference": gaps, "base_repaired": rec["base_repaired"],
           "Q_N": {"repaired": intervals["Q_N"], "strict": rec["strict_Q_N"]},
           "build": {"wall_s": rec["build_wall_s"],
                     "wall_min": None if rec["build_wall_s"] is None else round(rec["build_wall_s"] / 60, 1),
                     "step_build_s": rec["build_step_s"]}}
    if "headline" in rec:
        out.update({"headline": {"metric": rec["headline"]["metric"], **intervals["relation_group_mcc"]},
                    "parts": {"relation_mcc": intervals["relations"], "group_mcc": intervals["groups"]},
                    "binding_mcc": intervals["bindings"],
                    "diagnostics": {"reading_mcc": intervals["readings"], "held_out_relation_mcc": intervals["held_out_relations"],
                                    "seen_relation_mcc": {"point": rec["seen_relation_mcc"], "ci95": None}},
                    "depth": rec["depth"], "coverage": rec["coverage"], "counts": rec["counts"]})
    if rec["child_stats"] is not None:
        out["child_stats"] = rec["child_stats"]["ops"]
    out["repair"] = rec["repair"]  # repaired base only: Q_N, not the headline
    if rec["abstention"] is not None:
        out["abstention"] = rec["abstention"]
    if prereg_model is not None:  # a pre-registered model: its build must be the registered one
        model = rec["policy"]["model"]
        check(model["revision"] == prereg_model["digest"], f"{name}: model digest differs from the prereg")
        check(float(rec["policy"]["relation_floor"]) == float(prereg_model["relation_floor"]), f"{name}: floor differs from prereg")
        out.update({"model": model["id"], "child_version": model["child_version"], "child_prompt_sha256": model["prompt_sha256"],
                    "model_digest": model["revision"], "relation_floor": prereg_model["relation_floor"],
                    "dev": {"headline": prereg_model["dev_headline"], "relation_mcc": prereg_model["dev_mcc_ops"]["relations"],
                            "group_mcc": prereg_model["dev_mcc_ops"]["groups"], "binding_mcc": prereg_model["dev_mcc_ops"]["bindings"],
                            "reading_mcc": prereg_model["dev_mcc_ops"]["readings"], "build_s": prereg_model["dev_build_s"],
                            "abstained_overlength": prereg_model["dev_abstained_overlength"],
                            # unrounded test headline minus the dev value as stored in the prereg
                            "change_to_test": rec["headline"]["relation_group_mcc"] - prereg_model["dev_headline"]}})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=Path, default=ROOT / "runs" / RUN_PREFIX)
    ap.add_argument("--baselines", type=Path, default=ROOT / "runs" / f"{RUN_PREFIX}-baselines")
    ap.add_argument("--hf", type=Path, default=ROOT / "runs" / f"{RUN_PREFIX}-hf" / "hf")
    ap.add_argument("--fixture", type=Path, default=ROOT / "fixtures" / "mentat_hard_test")
    ap.add_argument("--prereg", type=Path, default=ROOT / "release" / "v0.1.0" / "record" / "prereg.json")
    ap.add_argument("--ci", type=Path, default=ROOT / "docs" / "results" / "mentat-hard-20261030-ci.json")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "results" / "mentat-hard-20261030.json")
    a = ap.parse_args(argv)
    try:
        return build(a)
    except (CheckError, OSError, ValueError, KeyError) as exc:
        print(f"build_results: {exc!r}", file=sys.stderr)
        return 1


def build(a: argparse.Namespace) -> int:
    fixture = a.fixture
    fixture_hash = corrections.content_hash(corrections.content_hashes(fixture))
    fixture_doc = jload(fixture / "fixture.json")
    version = decision_scoring.fixture_version(fixture)
    check(version == 3, f"{fixture} is version {version}, not 3")
    prereg = jload(a.prereg)
    ci = jload(a.ci)
    check(ci["stamp"]["content_hash"] == fixture_hash and ci["layout"] == LAYOUT and ci["B"] == 2000 and ci["seed"] == 0
          and ci["reference"] == REFERENCE, "the CI file is not the pre-registered league_ci output")
    depths = label_depths(fixture)
    check(len(depths) == 6000, f"{len(depths)} relation labels, not 6000")
    child_prompt = prereg["child"].split("prompt_sha256 ")[1].split(",")[0]

    models, commits, prompts = {}, set(), set()
    for model_id, prereg_model in prereg["models"].items():
        name = model_id.replace(":", "_")
        rec = cell_record(a.runs / name, fixture, fixture_hash, version, depths)
        check(rec["policy"]["model"]["id"] == model_id, f"{name}: model id differs")
        check(rec["policy"]["model"]["prompt_sha256"] == child_prompt, f"{name}: child prompt differs from the prereg")
        commits.add(rec["code_commit"])
        prompts.add(rec["policy"]["model"]["prompt_sha256"])
        models[name] = run_row(name, rec, ci, prereg_model)
    check(len(models) == 15, "expected 15 models")
    return finish(a, fixture, fixture_doc, fixture_hash, version, prereg, ci, depths, models, commits, prompts)


def finish(a, fixture, fixture_doc, fixture_hash, version, prereg, ci, depths, models, commits, prompts) -> int:
    baselines = {}
    for name in BASELINES:
        rec = cell_record(a.baselines / name, fixture, fixture_hash, version, depths)
        commits.add(rec["code_commit"])
        baselines[name] = run_row(name, rec, ci, None)
    if RULES in baselines:
        baselines[RULES]["dev"] = {"headline": prereg["rules_dev_headline"]}

    if (a.hf / "score.json").is_file():
        check(a.hf.name in ci["runs"], "the hf cell is complete but not in the CI file: rerun league_ci with it")
        rec = cell_record(a.hf, fixture, fixture_hash, version, depths)
        hf = {"status": "complete", "run": a.hf.name, **run_row(a.hf.name, rec, ci, None),
              "build_flags": jload(a.hf / "cell.json")["build_flags"]}  # not the pre-registered command (no floor, other timeouts)
    else:
        hf = {"status": "incomplete", "run": a.hf.name,
              "todo": "TODO: the hf pipeline cell (Qwen2.5-1.5B-Instruct) has no score.json yet; rerun build_results.py once it completes"}

    check(len(commits) == 1, f"cells were built at several commits: {sorted(commits)}")
    check(len(prompts) == 1, f"child prompt hashes differ: {sorted(prompts)}")

    allrows = {**models, **baselines, **({"hf": hf} if "headline" in hf else {})}
    ranking = sorted(models, key=lambda n: -models[n]["headline"]["point"])
    names = [n for n in allrows if n != "gold_view" and allrows[n].get("headline") and allrows[n]["headline"]["ci95"]]
    overlaps = []  # headline intervals that overlap (a weak test: the paired gaps are the test of a difference)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            lo_x, hi_x = allrows[x]["headline"]["ci95"]
            lo_y, hi_y = allrows[y]["headline"]["ci95"]
            if lo_x <= hi_y and lo_y <= hi_x:
                overlaps.append([x, y])
    depth_n = {b: models[REFERENCE]["depth"][b]["n"] for b in DEPTH_BUCKETS}
    for name, r in {**models, **baselines, "hf": hf}.items():
        if "depth" in r:
            check({b: r["depth"][b]["n"] for b in DEPTH_BUCKETS} == depth_n, f"{name}: depth bucket sizes differ")

    out = {
        "provenance": {
            "date": DATE,
            "code_commit": CODE_COMMIT,
            "fixture": {"name": fixture_doc["name"], "version": fixture_doc["version"], "content_hash": fixture_hash},
            "child": {"id": "systemone-choice", "child_version": "7", "prompt_sha256": next(iter(prompts))},
            "prereg": "release/v0.1.0/record/prereg.json",
            "ci_file": a.ci.name,
            "ci_layout": LAYOUT,
            "B": ci["B"],
            "seed": ci["seed"],
            "reference": REFERENCE,
            "headline": "relation_group_mcc: the mean of relation and group MCC, on raw (unfloored) decisions",
            "runs": f"runs/{RUN_PREFIX} (not committed)",
            "script": "docs/results/mentat-hard-results/build_results.py",
        },
        "models": models,
        "ranking": ranking,
        "baselines": baselines,
        "hf": hf,
        "headline_interval_overlaps": overlaps,
        "family_curve": [{"model": m, "test": models[m.replace(":", "_")]["headline"],
                          "dev_headline": models[m.replace(":", "_")]["dev"]["headline"]} for m in FAMILY],
        "depth_buckets": list(DEPTH_BUCKETS),
        "depth_n": depth_n,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(rnd(out), indent=1, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {a.out}: {len(models)} models, {len(baselines)} baselines, hf {hf['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

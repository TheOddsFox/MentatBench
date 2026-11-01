"""Builds the v0.1.0 results bundle of the Mentat-Hard test set: results/v0.1.0/ (leaderboard, predictions, cells) and results/index.json.

usage, from the repo root:
  python results/v0.1.0/build_bundle.py --runs DIR --baselines DIR --hf DIR [--fixture DIR] [--out DIR]
    --runs       runs/20261030-test           (the 15 model cells)
    --baselines  runs/20261030-test-baselines (heuristic, lexical, embedding, empty, gold_view, oracle)
    --hf         runs/20261030-test-hf/hf     (the hf pipeline cell)

MEASUREMENT ONLY, like docs/results/mentat-hard-results/build_results.py, which this script imports and runs first. Every
number comes from the run directories (through build_results, which uses the decision scorer), from decision_scoring (the labels
and scores of the predictions), or from the committed JSONs. The test gold is read from fixtures/mentat_hard_test, which is public.
The private split is never read: its numbers come only from docs/results/mentat-hard-private-20261030{,-ci}.json.

It never writes to the run directories. It writes only under --out (default results/): the regenerated results are built in
a temporary directory that is removed afterwards. Every check runs before anything is written; any failed check prints the reason
and exits 1 with nothing written. The output is deterministic (byte-identical on rerun).
"""

import argparse
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from mentat_bench import corrections, decision_scoring, ir  # noqa: E402
from mentat_bench.adapters.base import READINGS  # noqa: E402

RUN_PREFIX = "20261030-test"
RUN_DIR_FORMER = "runs/20261030-v31-test"  # the run directories before the 2026-11-01 rename (raw cell files)
RESULTS_DIR = ROOT / "docs" / "results"
MAIN = RESULTS_DIR / "mentat-hard-20261030.json"
CI = RESULTS_DIR / "mentat-hard-20261030-ci.json"
PRIVATE = RESULTS_DIR / "mentat-hard-private-20261030.json"
PRIVATE_CI = RESULTS_DIR / "mentat-hard-private-20261030-ci.json"
RECORD = ROOT / "release" / "v0.1.0" / "record"
PREREG = RECORD / "prereg.json"
BUILD_RESULTS = RESULTS_DIR / "mentat-hard-results" / "build_results.py"
MANIFEST = RECORD / "release_manifest.json"
GATES = RECORD / "check_gates.md"
RENAMES = ROOT / "docs" / "history" / "RENAMES.json"
OLLAMA = ROOT / "python" / "mentat_bench" / "children" / "ollama_models.json"
HF_MODELS = ROOT / "python" / "mentat_bench" / "children" / "models.json"
TEST_FIXTURE = ROOT / "fixtures" / "mentat_hard_test"

VERSION = "v0.1.0"
RELEASE_DATE = "2026-10-31"
SCHEMA = "mentat-bench-leaderboard/1"
INDEX_SCHEMA = "mentat-bench-results-index/1"
PROB_DP = 4  # predictions carry probabilities rounded to this many decimals
# Brier and ECE are rederived from the rounded probabilities. Rounding moves a few items across ECE bin edges: the largest
# differences seen over the 20 decision cells are 6e-6 (Brier) and 2.6e-3 (ECE). Everything else is compared exactly.
PROB_TOL = {"brier": 1e-4, "ece": 5e-3}
PRIVATE_CI_TOL = 1e-5  # the private results carry 6-decimal values of the league_ci output
HF_ID = "hf"
BASELINE_IDS = ("heuristic", "lexical", "embedding", "empty", "gold_view", "oracle")  # the order of the leaderboard
KIND = {"heuristic": "baseline", "lexical": "baseline", "embedding": "baseline", "empty": "baseline",
        "gold_view": "ceiling", "oracle": "ceiling", HF_ID: "pipeline_baseline"}
DISPLAY = {
    "d3_27b-q8_0": "D3 27B", "d3_9b-q8_0": "D3 9B", "pplx-decider_27b": "Pplx Decider 27B", "kev_9b-bf16": "Kev 9B",
    "d3_4b-q8_0": "D3 4B", "clef_27b-q8_0": "Clef 27B", "nimble-v3_9b-q8_0": "Nimble-v3 9B", "nimble_9b-bf16": "Nimble 9B",
    "clef-flash_9b": "Clef-Flash 9B", "tev1_4b-q8_0": "TEV1 4B", "d1_3b-q8_0": "D1 3B", "d3_2b-q8_0": "D3 2B",
    "tev1_0.8b-q8_0": "TEV1 0.8B", "d1-omni_600m-q8_0": "D1-Omni 600M", "laya_latest": "Laya 0.4B",
    "heuristic": "Rules (heuristic child)", "lexical": "Lexical", "embedding": "Embedding", "empty": "Empty",
    "gold_view": "gold_view (perfect curated answers)", "oracle": "Oracle", HF_ID: "hf pipeline (Qwen2.5-1.5B-Instruct)",
}
LICENSE_CANON = {"apache-2.0": "Apache-2.0", "cc-by-nc-4.0": "CC-BY-NC-4.0", "lfm-open-1.0": "LFM-Open-1.0"}
GATE_STATUS = {1: "MET", 2: "MET", 3: "NOT APPLICABLE", 4: "NOT APPLICABLE", 5: "REMOVED", 6: "MET (attested)", 7: "MET",
               8: "MET (attested)", 9: "MET", 10: "MET", 11: "MET (attested)"}
ABSOLUTE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|(?<![\w.])/(?:c|home|tmp|var|Users)/")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.\w+")


class CheckError(Exception):
    """A value that does not match its source: the script stops and writes nothing."""


def check(ok: bool, msg: str) -> None:
    if not ok:
        raise CheckError(msg)


def jload(path: Path):
    check(path.is_file(), f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def close(a, b, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def load_build_results():
    """docs/results/mentat-hard-results/build_results.py, imported unchanged (its hyphenated folder is not a package)."""
    spec = importlib.util.spec_from_file_location("build_results", BUILD_RESULTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model_ids() -> dict[str, str]:
    """run directory name -> Ollama tag, for the 15 pre-registered models (the tag with ':' as '_' is the directory name)."""
    return {key.replace(":", "_"): key for key in jload(PREREG)["models"]}


# ---------------------------------------------------------------- measurement checks

def regenerate_main(br, args, fixture: Path) -> None:
    """Rebuilds the results from the runs. They must equal the committed JSON (build_results.same, floats within 1e-9)."""
    with tempfile.TemporaryDirectory() as tmp:
        ns = argparse.Namespace(runs=args.runs, baselines=args.baselines, hf=args.hf, fixture=fixture, prereg=PREREG, ci=CI,
                                out=Path(tmp) / "main.json")
        with redirect_stdout(io.StringIO()):
            code = br.build(ns)
        check(code == 0, "build_results did not finish")
        regenerated = json.loads((Path(tmp) / "main.json").read_text(encoding="utf-8"))
    check(br.same(regenerated, jload(MAIN)), "the regenerated results differ from docs/results/mentat-hard-20261030.json")


def check_private(main: dict) -> dict:
    """The private aggregates against the CI file of the private split and against the public results (public_test)."""
    private, ci = jload(PRIVATE), jload(PRIVATE_CI)
    check(ci["stamp"]["content_hash"] == private["split"]["content_hash"], "private CI and private results name different splits")
    public = {**main["models"], **main["baselines"]}
    for name, run in private["runs"].items():
        check(name in ci["runs"], f"{name}: not in the private CI file")
        metrics = ci["runs"][name]["metrics"]
        check(close(run["headline"]["point"], metrics["relation_group_mcc"]["point"], PRIVATE_CI_TOL),
              f"{name}: private headline differs from the private CI file")
        check(close(run["relation"]["point"], metrics["relations"]["point"], PRIVATE_CI_TOL),
              f"{name}: private relation differs from the private CI file")
        check(close(run["group"]["point"], metrics["groups"]["point"], PRIVATE_CI_TOL),
              f"{name}: private group differs from the private CI file")
        check(close(run["public_test"]["headline"], public[name]["headline"]["point"], PRIVATE_CI_TOL),
              f"{name}: the private file's public_test headline differs from the public results")
    return private


def check_ollama(prereg: dict) -> dict:
    """Each model's Ollama tag, digest and license from children/ollama_models.json, and its digest equal to the prereg's."""
    ollama = jload(OLLAMA)
    for key, model in prereg["models"].items():
        check(key in ollama, f"{key}: not in children/ollama_models.json")
        check(ollama[key]["digest"] == model["digest"], f"{key}: digest differs from the prereg")
        check(ollama[key]["license"] == model["license"], f"{key}: license differs from the prereg")
    return ollama


def check_manifest(fixture: Path, fixture_hash: str, main: dict) -> dict:
    """The fixture files against the release manifest's pinned hashes, and the content hash against the committed provenance."""
    check(main["provenance"]["fixture"]["content_hash"] == fixture_hash, "fixture content hash differs from the committed results")
    manifest = jload(MANIFEST)
    pinned = manifest["fixtures"]["mentat_hard_test"]["files"]
    files = {}
    for path in sorted(p for p in fixture.rglob("*") if p.is_file()):
        rel = path.relative_to(fixture).as_posix()
        data = path.read_bytes()
        files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    for rel, sha in pinned.items():
        check(rel in files and files[rel]["sha256"] == sha, f"{rel}: differs from the release manifest")
    return files


def fixture_counts(fixture: Path) -> dict:
    """Item counts of the test fixture from its labels (the same numbers the results document quotes)."""
    labels = list(ir.read_jsonl(fixture / "gold" / "suite_labels.jsonl"))
    rel = [r for r in labels if r["op"] == "decide_relation"]
    grp = [r for r in labels if r["op"] == "decide_group"]
    readings = [r for r in grp if "readings" in r]
    counts = {"relation_questions": len(rel), "relation_none": sum(r["label"] == "NONE" for r in rel),
              "relation_held_out": sum(bool(r["held_out"]) for r in rel), "group_questions": len(grp),
              "group_questions_non_reading": len(grp) - len(readings), "reading_questions": len(readings),
              "binding_questions": sum(r["op"] == "decide_binding" for r in labels)}
    counts["scored_items"] = (counts["relation_questions"] + counts["group_questions_non_reading"]
                              + 2 * counts["reading_questions"] + counts["binding_questions"])
    expected = {"relation_questions": 6000, "relation_none": 1500, "relation_held_out": 2332, "group_questions": 1184,
                "reading_questions": 184, "binding_questions": 3348, "scored_items": 10716}
    for key, value in expected.items():
        check(counts[key] == value, f"fixture {key} is {counts[key]}, the results document says {value}")
    return counts


# ---------------------------------------------------------------- cells and predictions

def scrub_str(s: str) -> str:
    """A repo-relative form of a local path: the runs/ tail of a main-checkout path, else the file name; backslashes as slashes.
    The run directories were renamed on 2026-11-01: a path a run recorded under the former name is given the new one."""
    t = s.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", t) or t.startswith("/"):
        out = "runs/" + t.split("/runs/", 1)[1] if "/runs/" in t else t.rsplit("/", 1)[-1]
        return out.replace(RUN_DIR_FORMER, f"runs/{RUN_PREFIX}")
    s = s.replace(RUN_DIR_FORMER, f"runs/{RUN_PREFIX}")
    return s.replace("\\", "/") if "\\" in s and " " not in s else s


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return scrub_str(value) if isinstance(value, str) else value


def cell_doc(cid: str, cdir: Path, kind: str, predictions: str | None) -> dict:
    """The per-cell summary: score, score_decisions, build summary, child stats, the cell's build record and its wall time."""
    cell = jload(cdir / "cell.json")
    build = jload(cdir / "build_summary.json")
    meta = cdir.parent / f"{cdir.name}.meta" / "child_stats.json"
    doc = {
        "id": cid, "kind": kind, "run_dir": f"runs/{cdir.parent.name}/{cdir.name}",
        "score": jload(cdir / "score.json"),
        "score_decisions": jload(cdir / "score_decisions.json") if (cdir / "score_decisions.json").is_file() else None,
        "build_summary": build, "child_stats": jload(meta) if meta.is_file() else None, "cell": cell,
        "wall": {"build_wall_s": build.get("wall_seconds"),
                 "build_wall_min": None if build.get("wall_seconds") is None else round(build["wall_seconds"] / 60, 1),
                 "step_build_s": cell.get("step_seconds", {}).get("build")},
        "predictions": predictions,
    }
    return scrub(doc)


def prediction_rows(cdir: Path, fixture: Path, cands: dict, depths: dict) -> list[dict]:
    """One row per scored item (a reading question has a base and an alt row), labelled by decision_scoring.load."""
    decisions = cdir / "submission.build" / "decisions.jsonl"
    items, _ = decision_scoring.load(fixture / "gold" / "suite_labels.jsonl", fixture / "candidates.jsonl", decisions)
    rows, _ = decision_scoring.read_decisions(decisions)
    abstained = {r["qid"] for r in rows if r.get("qid") is not None and r.get("op") == "decide_relation"
                 and isinstance(r.get("decision"), dict) and set(r["decision"]) == {"relation"}}
    out, reading_index = [], Counter()
    for it in items:
        qid = it["qid"]
        cand = cands[qid]
        row = {"qid": qid, "op": it["op"], "stratum": it["stratum"], "held_out": it["held_out"], "gold": it["truth"],
               "pred": it["pred"], "correct": it["truth"] == it["pred"]}
        if it["option"] != it["truth"]:
            row["option"] = it["option"]  # a group class (at_least_k:3, a terms class) is labelled by its kind as well
        if it["probs"] is not None:
            row["probs"] = {k: round(p, PROB_DP) for k, p in it["probs"].items()}
        if it["op"] == "decide_relation":
            row.update({"a": cand["a"], "b": cand["b"], "depth": depths[qid], "abstained": qid in abstained})
        elif it["op"] == "decide_binding":
            row["id"] = cand["id"]
        else:
            row["members"] = cand["members"]
            if it["op"] == "decide_reading":
                row["reading"] = READINGS[reading_index[qid]]
                reading_index[qid] += 1
        if it["chain"] is not None:
            row["chain"] = it["chain"]
        if it["order"] is not None:
            row["order"] = it["order"]
        out.append(row)
    check(all(v == 2 for qid, v in reading_index.items() if qid), "a reading question without its two items")
    out.sort(key=lambda r: (r["qid"], READINGS.index(r["reading"]) if "reading" in r else 0))
    return out


def encode_predictions(rows: list[dict]) -> bytes:
    text = "".join(json.dumps(r, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n" for r in rows)
    return gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0)


def rescore(blob: bytes, version: int) -> dict:
    """The scores of a predictions file, rebuilt from its rows alone (decision_scoring.score, the same function as the run)."""
    items = []
    for line in gzip.decompress(blob).decode("utf-8").splitlines():
        r = json.loads(line)
        items.append({"qid": r["qid"], "op": r["op"], "stratum": r["stratum"], "held_out": r["held_out"], "truth": r["gold"],
                      "option": r.get("option", r["gold"]), "pred": r["pred"], "probs": r.get("probs"),
                      "chain": r.get("chain"), "order": r.get("order")})
    return decision_scoring.score(items, {}, version)


def same_scored(a, b, parent: str | None = None, br=None) -> bool:
    """Equal score trees. Brier and ECE values use PROB_TOL (the probabilities are rounded); ECE bins are not compared."""
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        for k in a:
            if (parent is None and k == "counts") or k == "bins":
                continue
            if k == "value" and parent in PROB_TOL:
                if not close(a[k], b[k], PROB_TOL[parent]):
                    return False
                continue
            if not same_scored(a[k], b[k], k, br):
                return False
        return True
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_scored(x, y, parent, br) for x, y in zip(a, b))
    return br.same(a, b)


def build_cells(args, br, fixture: Path, version: int, depths: dict, cands: dict) -> tuple[dict, dict]:
    """(cell documents, predictions bytes) for the 22 cells. A decision cell must rescore to its own file."""
    docs, preds = {}, {}
    models = model_ids()
    layout = [(name, args.runs / name, "system_one_model") for name in models]
    layout += [(name, args.baselines / name, KIND[name]) for name in BASELINE_IDS]
    layout += [(HF_ID, args.hf, KIND[HF_ID])]
    for cid, cdir, kind in layout:
        check(cdir.is_dir(), f"{cid}: missing cell directory {cdir}")
        # a cell has scored decisions when it has score_decisions.json (as build_results decides). empty and oracle write
        # decisions with no qid, which the scorer does not count, so they have none.
        is_scored = (cdir / "score_decisions.json").is_file()
        check(is_scored == (cid not in ("empty", "oracle")), f"{cid}: scored decisions present={is_scored}, unexpected")
        predictions = None
        if is_scored:
            rows = prediction_rows(cdir, fixture, cands, depths)
            check(len(rows) == 10716, f"{cid}: {len(rows)} prediction rows, the fixture has 10716 scored items")
            blob = encode_predictions(rows)
            rescored = rescore(blob, version)
            committed = jload(cdir / "score_decisions.json")
            check(same_scored(rescored, committed, None, br), f"{cid}: the predictions do not rescore to score_decisions.json")
            if cid in models:
                stats = jload(cdir.parent / f"{cdir.name}.meta" / "child_stats.json")
                n_abst = sum(r["abstained"] for r in rows if r["op"] == "decide_relation")
                check(n_abst == stats["ops"]["decide_relation"]["abstained_overlength"],
                      f"{cid}: {n_abst} relation abstentions in the predictions, child stats differ")
            predictions = f"results/{VERSION}/predictions/{cid}.jsonl.gz"
            preds[cid] = blob
        docs[cid] = cell_doc(cid, cdir, kind, predictions)
        if cid in models:
            docs[cid]["decisions_empty_group"] = empty_group_lists(cdir)
    return docs, preds


# ---------------------------------------------------------------- leaderboard

def metric(block):
    """{point, ci95} from a results block, or None when the block is absent (empty and oracle have no interval)."""
    if block is None:
        return None
    return {"point": block["point"], "ci95": block.get("ci95")}


def entry_metrics(row: dict) -> dict:
    diag = row.get("diagnostics", {})
    parts = row.get("parts", {})
    return {
        "headline": metric(row.get("headline")),
        "relation": metric(parts.get("relation_mcc")),
        "group": metric(parts.get("group_mcc")),
        "binding": metric(row.get("binding_mcc")),
        "reading": metric(diag.get("reading_mcc")),
        "held_out_relation": metric(diag.get("held_out_relation_mcc")),
        "seen_relation": metric(diag.get("seen_relation_mcc")),
        "q_n_repaired": metric(row["Q_N"]["repaired"]),
        "q_n_strict": {"point": row["Q_N"]["strict"], "ci95": None},
    }


def gap_block(gap: dict | None):
    if gap is None:
        return None
    return {"headline": metric(gap.get("headline")), "relation": metric(gap.get("relation_mcc")),
            "group": metric(gap.get("group_mcc")), "binding": metric(gap.get("binding_mcc")),
            "q_n_repaired": metric(gap.get("Q_N"))}


def tag_facts(tag: str | None) -> dict:
    """Parameters and quantisation from an Ollama tag ('d3:27b-q8_0' -> 27B, q8_0). Null when the tag does not state them."""
    if tag is None:
        return {"params": None, "params_source": None, "quant": None}
    name = tag.split(":", 1)[1]
    size = re.match(r"^(\d+(?:\.\d+)?)([bm])", name)
    quant = re.search(r"-(q\d+_\w+|bf16|fp16)$", name)
    params = f"{size.group(1)}{size.group(2).upper()}" if size else None
    return {"params": params, "params_source": "tag" if params else None, "quant": quant.group(1) if quant else None}


def model_entry(name: str, tag: str, main_row: dict, rank: int, ollama: dict, private: dict, ids: dict,
                prereg_model: dict) -> dict:
    art = ollama[tag]
    entry = {"id": name, "display_name": DISPLAY[name], "kind": "system_one_model", "family": tag.split(":", 1)[0],
             "rank": rank, "ollama_tag": tag, "hf_model": None, "hf_revision": None, "digest": art["digest"],
             "size_bytes": art["size"], "license": canon_license(art["license"]), **tag_facts(tag)}
    entry["child"] = {"id": "systemone-choice", "version": main_row["child_version"],
                      "prompt_sha256": main_row["child_prompt_sha256"]}
    raw_floor = float(main_row["relation_floor"])
    disabled = raw_floor == sys.float_info.max  # no floor reaches the precision: the model names no relation under it
    check(prereg_model["names_relations"] == (not disabled), f"{name}: names_relations differs from the floor")
    entry["relation_floor"] = None if disabled else raw_floor
    entry["relation_floor_disabled"] = disabled
    entry["names_relations_under_floor"] = prereg_model["names_relations"]
    entry["dev"] = {"headline": main_row["dev"]["headline"], "relation": main_row["dev"]["relation_mcc"],
                    "group": main_row["dev"]["group_mcc"], "binding": main_row["dev"]["binding_mcc"],
                    "reading": main_row["dev"]["reading_mcc"],
                    "headline_change_test_minus_dev": main_row["dev"]["change_to_test"]}
    return finish_entry(name, main_row, entry, private, ids)


def finish_entry(name: str, main_row: dict, entry: dict, private: dict, ids: dict) -> dict:
    entry.update({"display_name": DISPLAY[name], "kind": entry.get("kind", KIND.get(name)),
                  "run_dir": run_dir(name),
                  "cell": f"results/{VERSION}/cells/{name}.json",
                  "predictions": f"results/{VERSION}/predictions/{name}.jsonl.gz" if name in ids["predictions"] else None,
                  "runcard": f"release/{VERSION}/runcards/{name}.json" if name in ids["runcards"] else None,
                  "metrics": entry_metrics(main_row),
                  "gap_to_reference": gap_block(main_row.get("gap_to_reference")),
                  "depth_accuracy": main_row.get("depth"), "coverage": main_row.get("coverage"),
                  "counts": main_row.get("counts"), "abstention": main_row.get("abstention"),
                  "repair": main_row.get("repair"),
                  "runtime": {"build_wall_min": main_row["build"]["wall_min"], "build_wall_s": main_row["build"]["wall_s"],
                              "private_build_wall_min": private.get("wall_minutes") if private else None},
                  "private": private_block(private),
                  "notes": [], "flags": []})  # filled by apply_notes
    if "dev" not in entry:
        entry["dev"] = {"headline": main_row["dev"]["headline"]} if "dev" in main_row else None
    entry.setdefault("rank", None)
    entry.setdefault("params", None)
    return entry


def run_dir(name: str) -> str:
    if name == HF_ID:
        return f"runs/{RUN_PREFIX}-hf/hf"
    if name in BASELINE_IDS:
        return f"runs/{RUN_PREFIX}-baselines/{name}"
    return f"runs/{RUN_PREFIX}/{name}"


def private_block(run: dict | None) -> dict | None:
    if run is None:
        return None
    if "headline" not in run:
        return None
    return {"status": run["status"], "headline": metric(run["headline"]), "relation": metric(run["relation"]),
            "group": metric(run["group"]), "binding": metric(run.get("binding_secondary")),
            "public_test_headline": run.get("public_test", {}).get("headline"),
            "headline_private_minus_public": run.get("headline_private_minus_public"),
            "gap_to_reference": metric(run.get("gap_to_reference"))}


def baseline_entry(name: str, main_row: dict, private: dict, ids: dict) -> dict:
    entry = {"id": name, "display_name": DISPLAY[name], "kind": KIND[name], "family": None, "rank": None,
             "ollama_tag": None, "hf_model": None, "hf_revision": None, "digest": None, "size_bytes": None, "license": None,
             "params": None, "params_source": None, "quant": None, "child": None, "relation_floor": None}
    if name == HF_ID:
        pin = jload(HF_MODELS)["models"]["Qwen/Qwen2.5-1.5B-Instruct"]
        entry.update({"hf_model": "Qwen/Qwen2.5-1.5B-Instruct", "hf_revision": pin["revision"], "size_bytes": pin["bytes"],
                      "license": canon_license(pin["license"]), "build_flags": main_row.get("build_flags")})
    entry["dev"] = {"headline": main_row["dev"]["headline"]} if "dev" in main_row else None
    entry["relation_floor_disabled"] = None
    entry["names_relations_under_floor"] = None
    return finish_entry(name, main_row, entry, private, ids)


def build_leaderboard(main: dict, ci: dict, private: dict, prereg: dict, ollama: dict, gates: list, fixture_files: dict,
                      counts: dict, fixture_doc: dict, ids: dict) -> dict:
    models_main = main["models"]
    ranking = main["ranking"]
    model_rank = {name: i + 1 for i, name in enumerate(ranking)}
    tags = model_ids()
    models = [model_entry(name, tags[name], models_main[name], model_rank[name], ollama, private["runs"].get(name), ids,
                          prereg["models"][tags[name]]) for name in ranking]
    baselines = [baseline_entry(name, main["baselines"][name] if name != HF_ID else main["hf"],
                                private["runs"].get(name) or private["ceilings"].get(name), ids)
                 for name in BASELINE_IDS + (HF_ID,)]
    entries = {e["id"]: e for e in models + baselines}
    reference = main["provenance"]["reference"]
    rules = entries["heuristic"]["metrics"]["headline"]["ci95"]
    above = [e["id"] for e in models if e["metrics"]["headline"]["ci95"][0] > rules[1]]
    overlap = [e["id"] for e in models if not (e["metrics"]["headline"]["ci95"][1] < rules[0]
                                              or e["metrics"]["headline"]["ci95"][0] > rules[1])]
    resolved = []
    for e in models + baselines:
        if e["gap_to_reference"] is None:
            continue
        for stat in ("headline", "relation", "group", "binding"):
            gap = e["gap_to_reference"][stat]
            if gap is None:
                continue
            lo, hi = gap["ci95"]
            excludes = not (lo <= 0 <= hi)
            resolved.append({"run": e["id"], "statistic": stat, "gap_reference_minus_run": gap, "excludes_zero": excludes,
                             "higher": None if not excludes else ("reference" if gap["point"] > 0 else "run")})
    private_moves = {name: run["headline_private_minus_public"] for name, run in private["runs"].items()}
    worst = max(private_moves, key=lambda n: abs(private_moves[n]))
    order = private["order"]
    fixture_block = {"name": fixture_doc["name"], "version": fixture_doc["version"], "registry_version": "3",
                     "content_hash": main["provenance"]["fixture"]["content_hash"], "leagues": fixture_doc["leagues"],
                     "markets": fixture_doc["markets"], "outcomes": fixture_doc["outcomes"], "queries": fixture_doc["queries"],
                     "item_counts": counts, "files": fixture_files}
    return {
        "schema": SCHEMA,
        "release": VERSION,
        "date": RELEASE_DATE,
        "results_date": main["provenance"]["date"],
        "benchmark": {
            "name": "Mentat-Hard",
            "measures": "How accurately a System One model (one pass of typed decisions: relations between propositions, groups "
                        "of members, bindings, readings) understands the logical relationships in league rule worlds.",
            "split": "mentat_hard_test (public, Mentat-Hard test set, revision 3.1)",
            "headline": {"metric": "relation_group_mcc", "definition": "mean of the relation MCC and the group MCC, on raw "
                         "(unfloored) decisions", "decided_on": "dev (owner decision 2026-10-30)"},
            "secondary": "binding_mcc",
            "diagnostics": ["reading_mcc", "held_out_relation_mcc", "seen_relation_mcc", "q_n_repaired", "q_n_strict"],
            "reference": reference,
        },
        "glossary": {
            "headline": "relation_group_mcc: mean of relation and group MCC (multiclass MCC, Gorodkin R_K) on raw decisions",
            "relation": "relation MCC over the 6,000 relation questions (EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, NONE)",
            "group": "group MCC over the 1,000 non-reading group questions (the group class: its kind, with k where it applies)",
            "binding": "binding MCC over the 3,348 binding questions (bound or unresolved); secondary",
            "reading": "reading MCC over the 368 reading items (184 reading questions, two readings each); diagnostic",
            "held_out_relation": "relation MCC on the 2,332 held-out relations; diagnostic",
            "seen_relation": "relation MCC on the seen relations; point only (not bootstrapped); diagnostic",
            "q_n_repaired": "Q_N of the repaired base (the end-to-end exam score); diagnostic, not the ranking",
            "q_n_strict": "Q_N with an unrepaired base scored 0; diagnostic",
            "gap_to_reference": "reference minus run, paired within each league-bootstrap draw",
            "ci95": "95% percentile interval of the league bootstrap (B 2000, seed 0, 18 leagues); null where no interval exists",
            "rank": "position by the headline point estimate among the 15 models; ranks are not statistically resolved",
        },
        "units": {"mcc": "dimensionless, -1 to 1", "q_n": "points on a 0 to 100 scale", "build_wall_min": "minutes",
                  "build_wall_s": "seconds", "size_bytes": "bytes",
                  "relation_floor": "the relation floor in the prereg's units; null when relation_floor_disabled is true",
                  "dev_headline": "headline units, -1 to 1", "ci95": "[lower, upper] in the metric's units",
                  "rank": "1 is the highest headline point among the models"},
        "provenance": {
            "code_commit": main["provenance"]["code_commit"],
            "code_commit_note": "the commit the test cells ran at (clean tree); the prereg names 9ad5f2c, which adds only the "
                                "prereg files",
            "release_pinned_commit": jload(MANIFEST)["repo"]["commit"],
            "release_manifest": {"path": f"release/{VERSION}/record/release_manifest.json",
                                 "sha256": json_sha(MANIFEST)},
            "fixture": fixture_block,
            "private_split": {"name": private["split"]["name"], "content_hash": private["split"]["content_hash"],
                              "generator_version": private["split"]["generator_version"], "sealed": private["split"]["sealed"],
                              "outcomes": private["split"]["outcomes"], "candidates": private["split"]["candidates"],
                              "queries": private["split"]["queries"], "item_counts": private["item_counts_private"]},
            "child": {"id": "systemone-choice", "version": "7",
                      "prompt_sha256": main["provenance"]["child"]["prompt_sha256"]},
            "ci": {"method": "league bootstrap: B draws of the 18 leagues with replacement (random.Random('league_ci:0')), the "
                             "same draws for every run; 95% percentile intervals (numpy, linear); paired gaps, reference minus run",
                   "file": "docs/results/mentat-hard-20261030-ci.json", "layout": main["provenance"]["ci_layout"],
                   "B": ci["B"], "seed": ci["seed"], "leagues": ci["leagues"], "level": 0.95, "reference": ci["reference"],
                   "reference_chosen_on": "dev", "private_file": "docs/results/mentat-hard-private-20261030-ci.json"},
            "prereg": "release/v0.1.0/record/prereg.json",
            "results_md": "docs/results/mentat-hard-20261030.md",
            "results_json": "docs/results/mentat-hard-20261030.json",
            "private_results_md": "docs/results/mentat-hard-private-20261030.md",
            "private_results_json": "docs/results/mentat-hard-private-20261030.json",
            "results_builder": "docs/results/mentat-hard-results/build_results.py",
            "bundle_builder": f"results/{VERSION}/build_bundle.py",
            "runs": f"runs/{RUN_PREFIX}, runs/{RUN_PREFIX}-baselines, runs/{RUN_PREFIX}-hf (not committed)",
            "ollama_version": next(iter(ollama.values()))["ollama_version"],
        },
        "ranking": ranking,
        "depth_buckets": main["depth_buckets"],
        "depth_n": main["depth_n"],
        "models": models,
        "baselines": baselines,
        "resolved_comparisons": resolved,
        "resolved_comparisons_note": "Only gaps to the reference are paired. An interval that excludes 0 is resolved; an interval "
                                     "that includes 0 is not. Overlapping intervals among non-reference runs are not a test of a "
                                     "tie, and the paired comparison among them is not computed.",
        "unpaired_headline_vs_rules": {"rules_ci95": rules, "above_rules_interval": above,
                                       "overlap_rules_interval": overlap},
        "headline_interval_overlaps": main["headline_interval_overlaps"],
        "d3_size_curve": [size_point(m, entries, tags) for m in main["family_curve"]],
        "private_checks": {"headline_metric": "relation_group_mcc", "floors_match_prereg_and_public": private["checks"]
                           ["floors_match_prereg_and_public"], "score_decisions_match_ci": private["score_decisions_match_ci"],
                           "spearman_rho_public_vs_private": order["spearman_rho"], "pairs_compared": order["pairs"],
                           "pairs_disjoint_in_both": order["pairs_disjoint_in_both"],
                           "order_flips_among_disjoint_in_both": order["order_flips_among_disjoint_in_both"],
                           "max_abs_headline_change": {"value": private_moves[worst], "run": worst},
                           "runs_compared": sorted(private_moves)},
        "release_gates": {"source": f"release/{VERSION}/record/check_gates.md", "built_at_commit": jload(MANIFEST)["repo"]["commit"],
                          "recorded_at": "defed6a, 2026-10-31",
                          "gate_7_scope": "MET at the pinned commit only; UNKNOWN at any later commit (docs/remaining-work.md section 2.3)",
                          "rows": gates},
    }


def size_point(m: dict, entries: dict, tags: dict) -> dict:
    """One point of the D3 size curve (family_curve in the results JSON, keyed by Ollama tag)."""
    e = entries[next(name for name, tag in tags.items() if tag == m["model"])]
    return {"id": e["id"], "display_name": e["display_name"],
            "headline": {"point": m["test"]["point"], "ci95": m["test"]["ci95"]},
            "relation": e["metrics"]["relation"]["point"], "group": e["metrics"]["group"]["point"],
            "dev_headline": m["dev_headline"]}


def json_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def renames() -> dict[str, str]:
    """old path -> new path of every file moved out of the earlier layout (docs/history/RENAMES.json)."""
    doc = jload(RENAMES)
    check(doc["schema"] == "mentat-bench-renames/1", "docs/history/RENAMES.json has another schema")
    return {old: entry["path"] for old, entry in doc["renames"].items()}


def relabel(text: str, moved: dict[str, str]) -> str:
    """A frozen gate text with its old paths replaced by the new ones (longest first) and the one phrase fixed."""
    for old in sorted(moved, key=len, reverse=True):
        text = text.replace(old, moved[old])
    return text.replace("spec v0.2", "spec revision of 2026-10-27")


def gate_rows() -> list[dict]:
    """The gate table of the frozen check_gates.md, with its paths mapped to the moved files. The file itself is not changed."""
    moved, rows = renames(), []
    for line in GATES.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or set(line) <= {"|", "-", " "} or line.startswith("| gate"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append({"gate": len(rows) + 1, "name": cells[0], "status": cells[1], "evidence": relabel(cells[2], moved),
                     "note": relabel(cells[3], moved)})
    check(len(rows) == 11, f"{len(rows)} gate rows, expected 11")
    for row in rows:
        check(row["status"] == GATE_STATUS[row["gate"]],
              f"gate {row['gate']} reads {row['status']}, remaining-work says {GATE_STATUS[row['gate']]}")
    return rows


def canon_license(raw: str | None) -> str | None:
    """A licence name in the casing the licence documents use ('apache-2.0' -> 'Apache-2.0'); None stays None."""
    return None if raw is None else LICENSE_CANON.get(raw.lower(), raw)


def empty_group_lists(cdir: Path) -> dict:
    """Group decisions with an empty group list, and how many of them have null probabilities (the child's abstention marker)."""
    rows, _ = decision_scoring.read_decisions(cdir / "submission.build" / "decisions.jsonl")
    empty = [r for r in rows if r.get("op") == "decide_group" and isinstance(r.get("decision"), dict)
             and r["decision"].get("groups") == []]
    return {"empty_lists": len(empty), "null_probs": sum(r.get("probs") is None for r in empty)}


def rows_of(blob: bytes) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(blob).decode("utf-8").splitlines()]


def apply_notes(lb: dict, main: dict, prereg: dict, rows: dict, counts: dict, docs: dict) -> None:
    """Per-entry notes and machine-readable flags. Each number comes from the leaderboard, the results JSON, the predictions
    rows or the cell's child stats. Each qualitative claim is asserted, so a changed source fails the build instead of leaving
    a stale note."""
    E = {e["id"]: e for e in lb["models"] + lb["baselines"]}
    models = [e["id"] for e in lb["models"]]
    for e in E.values():
        e["notes"], e["flags"] = [], []

    def note(name, text):
        E[name]["notes"].append(text)

    def flag(name, value):
        if value not in E[name]["flags"]:
            E[name]["flags"].append(value)

    def met(name, key):
        return E[name]["metrics"][key]

    def ci3(block):
        return f"{block['point']:.3f} [{block['ci95'][0]:.3f}, {block['ci95'][1]:.3f}]"

    def overlaps(a, b):
        return a[0] <= b[1] and b[0] <= a[1]

    def unresolved(cid):  # binding predictions 'unresolved' on test: (count, correct)
        hits = [r for r in rows[cid] if r["op"] == "decide_binding" and r["pred"] == "unresolved"]
        return len(hits), sum(r["correct"] for r in hits)

    def named(cid):  # relation predictions that name a relation (raw decisions, not floored)
        return Counter(r["pred"] for r in rows[cid] if r["op"] == "decide_relation" and r["pred"] not in ("NONE", "__missing__"))

    ref = main["provenance"]["reference"]
    check(max(models, key=lambda n: E[n]["dev"]["headline"]) == ref, "the reference is not the best dev headline")
    note(ref, f"Reference: every other run's gap is reference minus run, paired within each bootstrap draw. Chosen on dev as the "
              f"best dev headline ({E[ref]['dev']['headline']:.4f}).")
    flag(ref, "reference")

    never = [n for n in models if unresolved(n)[0] == 0]
    check(set(never) == {"d3_27b-q8_0", "d3_9b-q8_0", "d3_4b-q8_0", "pplx-decider_27b"}, f"never-flags set is {never}")
    for n in never:
        flag(n, "never_flags_ambiguous")
        note(n, "Never flags an ambiguous proposition on test (no unresolved predictions), so its binding MCC is 0 by convention "
                "(degenerate interval).")

    d9 = "d3_9b-q8_0"
    dev_bind, test_bind = main["models"][d9]["dev"]["binding_mcc"], met(d9, "binding")["point"]
    check(test_bind == 0.0 and dev_bind > 0.2, "d3_9b binding is not a drop from dev to test")
    flag(d9, "binding_fell_dev_to_test")
    note(d9, f"Binding MCC fell from {dev_bind:.3f} on dev to {test_bind:.3f} on test. It flags no binding question as ambiguous "
             f"on test. The cause is observed but not identified.")

    by_group = sorted(models, key=lambda n: -met(n, "group")["point"])
    check(by_group[1] == "kev_9b-bf16", "Kev 9B is not second on group MCC")
    nv3 = "nimble-v3_9b-q8_0"
    check(overlaps(met("kev_9b-bf16", "group")["ci95"], met(nv3, "group")["ci95"]), "Kev and Nimble-v3 group intervals are disjoint")
    note("kev_9b-bf16", f"Group MCC {met('kev_9b-bf16', 'group')['point']:.3f} is second among the models on test. Its interval "
                        f"overlaps Nimble-v3 9B's ({met(nv3, 'group')['point']:.3f}), so the second place is not resolved.")

    c27 = "clef_27b-q8_0"
    check(max(models, key=lambda n: met(n, "binding")["point"]) == c27, "Clef 27B does not have the highest binding point")
    wall = E[c27]["runtime"]["build_wall_min"]
    step = round(main["models"][c27]["build"]["step_build_s"] / 60, 1)
    check(wall <= 80, "Clef 27B is over the 80-minute target")
    note(c27, f"Highest binding point on test ({ci3(met(c27, 'binding'))}).")
    note(c27, f"Build wall time {wall} min: at or under the 80-minute target (its step timer reads {step} min).")

    n9 = "nimble_9b-bf16"
    count, correct = unresolved(n9)
    note(n9, f"Binding {ci3(met(n9, 'binding'))} comes from {count} flags, {correct} of them correct.")

    lic = E[nv3]["license"]
    check(lic is not None and "NC" in lic, "Nimble-v3 licence is not non-commercial")
    note(nv3, f"License {lic} (non-commercial terms, as recorded in children/ollama_models.json).")
    flag(nv3, "license_non_commercial")

    rules = met("heuristic", "headline")["ci95"]
    c_flash = "clef-flash_9b"
    check(met(c_flash, "headline")["ci95"][0] > rules[1], "Clef-Flash 9B is not above the rules interval")
    note(c_flash, f"Headline interval lies entirely above the rules baseline's interval ({rules[0]:.3f} to {rules[1]:.3f}; "
                  f"unpaired comparison).")
    tev4 = "tev1_4b-q8_0"
    check(overlaps(met(tev4, "headline")["ci95"], rules), "TEV1 4B does not overlap the rules interval")
    note(tev4, f"Headline interval overlaps the rules baseline's interval ({rules[0]:.3f} to {rules[1]:.3f}; unpaired comparison).")

    t8 = "tev1_0.8b-q8_0"
    check(main["models"][t8]["base_repaired"] is False, "TEV1 0.8B base is repaired")
    check(sum(main["models"][n]["base_repaired"] is False for n in models) == 1, "more than one unrepaired base")
    check(abs(met(t8, "q_n_repaired")["point"] - met(t8, "q_n_strict")["point"]) < 1e-9, "TEV1 0.8B strict Q_N differs")
    check(E[t8]["relation_floor_disabled"], "TEV1 0.8B has a floor")
    check(abs(met(t8, "relation")["point"]) < 0.02, "TEV1 0.8B relation MCC is not near zero")
    named8 = named(t8)
    flag(t8, "base_not_repaired")
    flag(t8, "names_no_relations_under_floor")
    note(t8, f"Flag ‡: its base was not repaired, so its strict Q_N equals its repaired Q_N ({met(t8, 'q_n_repaired')['point']:.2f}). "
             f"It is the only build whose base was not repaired (the pre-registration expected strict Q_N 0).")
    note(t8, f"Under its relation floor (the largest float) it names no relations. Its raw decisions name "
             f"{sum(named8.values())} relations (EXCLUDES {named8.get('EXCLUDES', 0)}, EQUIV {named8.get('EQUIV', 0)}), and its "
             f"relation MCC is near zero ({met(t8, 'relation')['point']:.3f}).")
    check(met(t8, "headline")["ci95"][0] <= 0 <= met(t8, "headline")["ci95"][1], "TEV1 0.8B headline excludes 0")
    note(t8, f"Headline interval includes 0 ({ci3(met(t8, 'headline'))}).")

    d1 = "d1-omni_600m-q8_0"
    check(E[d1]["relation_floor_disabled"], "D1-Omni 600M has a floor")
    check(met(d1, "headline")["ci95"][0] <= 0 <= met(d1, "headline")["ci95"][1], "D1-Omni headline excludes 0")
    check(met(d1, "binding")["ci95"][0] > 0, "D1-Omni binding interval includes 0")
    flag(d1, "names_no_relations_under_floor")
    f_count, f_correct = unresolved(d1)
    note(d1, f"Under its relation floor (the largest float) it names no relations. Its raw decisions name "
             f"{sum(named(d1).values()):,} relations.")
    note(d1, f"Headline interval includes 0 ({ci3(met(d1, 'headline'))}).")
    note(d1, f"Binding MCC {ci3(met(d1, 'binding'))} has an interval above 0, but it comes from {f_count:,} flags with a precision "
             f"of {f_correct / f_count:.2f}.")

    d13 = "d1_3b-q8_0"
    held, seen = met(d13, "held_out_relation")["point"], met(d13, "seen_relation")["point"]
    check(held > seen, "D1 3B held-out is not above seen")
    note(d13, f"Its held-out relation MCC ({held:.3f}) is slightly above its seen relation MCC ({seen:.3f}). The difference has no "
              f"interval, so it is descriptive.")

    la = "laya_latest"
    ab = E[la]["abstention"]
    cs = docs[la]["child_stats"]["ops"]
    rel_abs, grp_abs = cs["decide_relation"]["abstained_overlength"], cs["decide_group"]["abstained_overlength"]
    check(rel_abs == ab["relation_abstained"], "Laya relation abstentions differ from child stats")
    empties = docs[la]["decisions_empty_group"]
    null_none = empties["null_probs"]
    check(null_none == grp_abs == ab["group_empty_null_probs"],
          f"Laya: {null_none} empty group lists with null probs, child abstained {grp_abs}")
    check(empties["empty_lists"] == ab["group_empty_lists"] and empties["empty_lists"] - null_none == 1,
          "Laya: the empty group lists do not split into 1,032 with null probs and one with probs")
    n_rel, n_grp = counts["relation_questions"], counts["group_questions"]
    flag(la, "abstains_for_length")
    flag(la, "abstentions_scored_as_none")
    note(la, f"Flag †: scored as pre-registered. An abstention is a NONE with no confidence. On test its child abstained for length "
             f"on {rel_abs:,} of {n_rel:,} relation questions and on {grp_abs:,} of {n_grp:,} group questions. "
             f"{ab['correct_none_abstained']:,} of its {ab['correct_none']:,} correct NONE decisions are abstentions "
             f"({ab['correct_none_not_abstained']} are not).")
    note(la, f"Its {null_none:,} group abstentions are empty group lists with null probabilities, and decision_scoring reads each "
             f"empty list as the group class 'none'. The one other empty group list (with probabilities) is also 'none'. So its "
             f"group MCC ({met(la, 'group')['point']:.3f}) scores abstentions as answers and is not a judgment.")
    note(la, f"Its relation MCC ({met(la, 'relation')['point']:.3f}) is computed with the abstentions as NONE answers. Its context "
             f"is 512 tokens (pre-registration).")
    check(met(la, "headline")["ci95"][1] < 0, "Laya headline interval is not below 0")
    note(la, f"Headline interval is entirely below 0 on test (upper bound {met(la, 'headline')['ci95'][1]:.3f}).")
    for n in models:  # every model's empty group lists with null probs cover its child's group abstentions
        child = docs[n]["child_stats"]["ops"]["decide_group"]["abstained_overlength"]
        check(docs[n]["decisions_empty_group"]["null_probs"] >= child,
              f"{n}: fewer empty group lists with null probs than child abstentions")

    d2 = "d3_2b-q8_0"
    q2, q27 = met(d2, "q_n_repaired"), met(ref, "q_n_repaired")
    check(q2["point"] > q27["point"] and overlaps(q2["ci95"], q27["ci95"]), "D3 2B Q_N is not above D3 27B with overlap")
    note(d2, f"Its repaired Q_N point ({q2['point']:.2f}) is above D3 27B's ({q27['point']:.2f}), and the intervals overlap. Q_N "
             f"is a diagnostic and does not rank models.")

    note("heuristic", f"The pre-registered rules baseline (heuristic child). Its dev headline is {E['heuristic']['dev']['headline']:.4f} "
                      f"(the prereg's rules_dev_headline).")
    check(abs(prereg["rules_dev_headline"] - E["heuristic"]["dev"]["headline"]) < 1e-9, "rules dev headline differs from the prereg")

    for n in ("lexical", "embedding"):
        check(E[n]["coverage"]["decide_group"] == 0.0 and E[n]["coverage"]["decide_reading"] == 0.0,
              f"{n} answers group or reading questions")
        flag(n, "no_group_or_reading_decisions")
        answered = sum(r["pred"] != "__missing__" for r in rows[n])
        note(n, f"No group or reading decisions: its {counts['group_questions_non_reading']:,} group questions and "
                f"{counts['reading_questions']:,} reading questions are unanswered ({answered:,} answered decisions: relations "
                f"and bindings).")

    for n in ("empty", "oracle"):
        flag(n, "q_n_only")
        note(n, f"No decisions: Q_N only ({met(n, 'q_n_repaired')['point']:.2f}, no interval; not in the league_ci run).")

    gv = E["gold_view"]["gap_to_reference"]["headline"]["point"]
    check(gv < 0, "gold_view is not above the reference")
    note("gold_view", f"Ceiling (perfect curated answers), not a System One model. It is above the reference, so its paired gap to "
                      f"D3 27B is {gv:.3f} (negative).")

    hf_flags = main["hf"]["build_flags"]
    check("--relation-floor" not in hf_flags and "--batch-size" not in hf_flags, "hf build flags changed")
    lt, ht = hf_flags[hf_flags.index("--local-timeout") + 1], hf_flags[hf_flags.index("--hello-timeout") + 1]
    acc = E["hf"]["depth_accuracy"]["NONE"]["accuracy"]
    check(acc > 0.95, "hf does not answer NONE for nearly every relation")
    flag("hf", "nonstandard_build_flags")
    flag("hf", "answers_none_for_nearly_every_relation")
    note("hf", f"Pre-registered hf pipeline baseline (release gate 10), not a System One child. Its build flags differ from the "
               f"pre-registered command: no relation floor, no batch size, --local-timeout {lt} and --hello-timeout {ht}.")
    note("hf", "Its cell finished after the first league_ci run, so it was added to that bootstrap run. Every other run's value is "
               "unchanged.")
    note("hf", f"It answers NONE for nearly every relation ({acc:.3f} on NONE rows), so its named-relation accuracy is near zero.")

    for n in models:  # flags that follow from the results rather than from a note
        if E[n]["relation_floor_disabled"]:
            flag(n, "names_no_relations_under_floor")
        if main["models"][n]["base_repaired"] is False:
            flag(n, "base_not_repaired")
        if docs[n]["child_stats"]["ops"]["decide_relation"]["abstained_overlength"] > 0:
            flag(n, "abstains_for_length")


def csv_bytes(lb: dict) -> bytes:
    columns = ["rank", "id", "display_name", "kind", "family", "ollama_tag", "digest", "params", "quant", "license",
               "size_bytes",
               "headline_point", "headline_lo", "headline_hi", "relation_point", "relation_lo", "relation_hi",
               "group_point", "group_lo", "group_hi", "binding_point", "binding_lo", "binding_hi",
               "reading_point", "reading_lo", "reading_hi", "held_out_relation_point", "held_out_relation_lo",
               "held_out_relation_hi", "seen_relation_point", "q_n_repaired_point", "q_n_repaired_lo", "q_n_repaired_hi",
               "q_n_strict", "gap_headline_point", "gap_headline_lo", "gap_headline_hi", "dev_headline", "relation_floor",
               "private_headline_point", "private_headline_lo", "private_headline_hi", "build_wall_min"]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for e in lb["models"] + lb["baselines"]:
        m, g, p = e["metrics"], e["gap_to_reference"] or {}, e["private"] or {}
        values = {"rank": e["rank"], "id": e["id"], "display_name": e["display_name"], "kind": e["kind"],
                  "family": e["family"], "ollama_tag": e["ollama_tag"], "digest": e["digest"], "params": e["params"],
                  "quant": e["quant"], "license": e["license"],
                  "size_bytes": e["size_bytes"], "build_wall_min": e["runtime"]["build_wall_min"],
                  "q_n_strict": m["q_n_strict"]["point"], "dev_headline": (e["dev"] or {}).get("headline"),
                  "relation_floor": e["relation_floor"]}
        for key in ("headline", "relation", "group", "binding", "reading", "held_out_relation", "seen_relation",
                    "q_n_repaired"):
            name = {"headline": "headline", "relation": "relation", "group": "group", "binding": "binding",
                    "reading": "reading", "held_out_relation": "held_out_relation", "seen_relation": "seen_relation",
                    "q_n_repaired": "q_n_repaired"}[key]
            block = m[key]
            values[f"{name}_point"] = None if block is None else block["point"]
            if key != "seen_relation":
                values[f"{name}_lo"] = None if block is None or block["ci95"] is None else block["ci95"][0]
                values[f"{name}_hi"] = None if block is None or block["ci95"] is None else block["ci95"][1]
        gh = g.get("headline")
        values.update({"gap_headline_point": None if gh is None else gh["point"],
                       "gap_headline_lo": None if gh is None or gh["ci95"] is None else gh["ci95"][0],
                       "gap_headline_hi": None if gh is None or gh["ci95"] is None else gh["ci95"][1]})
        ph = p.get("headline")
        values.update({"private_headline_point": None if ph is None else ph["point"],
                       "private_headline_lo": None if ph is None or ph["ci95"] is None else ph["ci95"][0],
                       "private_headline_hi": None if ph is None or ph["ci95"] is None else ph["ci95"][1]})
        writer.writerow(["" if values.get(c) is None else values.get(c) for c in columns])
    return buf.getvalue().encode("utf-8")


def index_doc() -> dict:
    """results/index.json: the latest release (v0.1.0, with the roles its readers need) and the two development rounds."""
    record = f"release/{VERSION}/record"
    return {
        "schema_version": INDEX_SCHEMA,
        "latest": VERSION,
        "versions": [
            {"version": VERSION, "status": "latest", "superseded_by": None, "benchmark": "Mentat-Hard",
             "fixture": "mentat_hard_test", "fixture_version": "3.1", "headline": "relation_group_mcc",
             "date": RELEASE_DATE, "results_date": "2026-10-30", "builder": f"results/{VERSION}/build_bundle.py",
             "leaderboard_json": f"results/{VERSION}/leaderboard.json", "leaderboard_csv": f"results/{VERSION}/leaderboard.csv",
             "cells_dir": f"results/{VERSION}/cells", "predictions_dir": f"results/{VERSION}/predictions",
             "runcards_dir": f"release/{VERSION}/runcards", "readme": "results/README.md",
             "results_md": "docs/results/mentat-hard-20261030.md", "results_json": "docs/results/mentat-hard-20261030.json",
             "private_split": "mentat_hard_private", "private_results_md": "docs/results/mentat-hard-private-20261030.md",
             "private_results_json": "docs/results/mentat-hard-private-20261030.json",
             "prereg": f"{record}/prereg.md", "release_manifest": f"{record}/release_manifest.json",
             "release_gate_output": f"{record}/check_gates.md", "attestations": f"{record}/attestations.json"},
            {"version": "round-2", "kind": "development_round", "status": "superseded", "superseded_by": VERSION,
             "benchmark": "Mentat-Hard", "fixture": "mentat_hard_test", "fixture_version": "2",
             "headline": "relation_mcc (headline changed post-hoc 2026-10-29)", "date": "2026-10-29", "results_date": "2026-10-29",
             "private_split": None, "readme": None, "leaderboard_json": None, "leaderboard_csv": None,
             "results_md": "docs/history/round-2/report.md", "results_json": "docs/history/round-2/results.json"},
            {"version": "round-1", "kind": "development_round", "status": "superseded", "superseded_by": "round-2",
             "benchmark": "Mentat-Hard", "fixture": "mentat_hard_test", "fixture_version": "1",
             "headline": "four-part MCC mean (the v1 headline)", "date": "2026-10-28", "results_date": "2026-10-28",
             "private_split": None, "readme": None, "leaderboard_json": None, "leaderboard_csv": None,
             "results_md": "docs/history/round-1/report.md", "results_json": "docs/history/round-1/results.json"},
        ],
    }


# ---------------------------------------------------------------- main

def build(args) -> dict:
    """Every check and every output, in memory. Returns {relative path: bytes}."""
    br = load_build_results()
    fixture = args.fixture
    version = decision_scoring.fixture_version(fixture)
    check(version == 3, f"{fixture} is fixture version {version}, not 3")
    fixture_hash = corrections.content_hash(corrections.content_hashes(fixture))
    regenerate_main(br, args, fixture)
    main, ci = jload(MAIN), jload(CI)
    prereg = jload(PREREG)
    check(ci["stamp"]["content_hash"] == fixture_hash, "the CI file is for another fixture")
    private = check_private(main)
    ollama = check_ollama(prereg)
    fixture_files = check_manifest(fixture, fixture_hash, main)
    counts = fixture_counts(fixture)
    depths = br.label_depths(fixture)
    cands = {}
    for row in ir.read_jsonl(fixture / "candidates.jsonl"):
        check(row["qid"] not in cands, f"duplicate candidate {row['qid']}")
        cands[row["qid"]] = row
    docs, preds = build_cells(args, br, fixture, version, depths, cands)
    gates = gate_rows()
    fixture_doc = jload(fixture / "fixture.json")
    lb_ids = {"predictions": set(preds), "runcards": {p.stem for p in (ROOT / "release" / VERSION / "runcards").glob("*.json")}}
    lb = build_leaderboard(main, ci, private, prereg, ollama, gates, fixture_files, counts, fixture_doc, lb_ids)
    rows = {cid: rows_of(blob) for cid, blob in preds.items()}
    apply_notes(lb, main, prereg, rows, counts, docs)
    out = {f"{VERSION}/leaderboard.json": dump_json(lb), f"{VERSION}/leaderboard.csv": csv_bytes(lb)}
    for cid, blob in preds.items():
        out[f"{VERSION}/predictions/{cid}.jsonl.gz"] = blob
    for cid, doc in docs.items():
        out[f"{VERSION}/cells/{cid}.json"] = dump_json(doc)
    for path, data in out.items():
        text = gzip.decompress(data).decode("utf-8") if path.endswith(".gz") else data.decode("utf-8")
        check(not ABSOLUTE.search(text), f"{path}: an absolute or personal path")
        check(not EMAIL.search(text), f"{path}: an email address")
    print(f"checked: {len(preds)} prediction files, {len(docs)} cells, {len(lb['models'])} models, "
          f"{len(lb['baselines'])} baselines; fixture {version} {fixture_hash[:12]}", file=sys.stderr)
    return out


def dump_json(obj) -> bytes:
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def write(out_dir: Path, files: dict[str, bytes]) -> None:
    """Writes the files, and removes the predictions and cells files this build no longer produces."""
    for rel, data in files.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for sub, suffix in ((f"{VERSION}/predictions", ".jsonl.gz"), (f"{VERSION}/cells", ".json")):
        folder = out_dir / sub
        if folder.is_dir():
            for stale in folder.glob(f"*{suffix}"):
                if f"{sub}/{stale.name}" not in files:
                    stale.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=Path, default=ROOT / "runs" / RUN_PREFIX)
    ap.add_argument("--baselines", type=Path, default=ROOT / "runs" / f"{RUN_PREFIX}-baselines")
    ap.add_argument("--hf", type=Path, default=ROOT / "runs" / f"{RUN_PREFIX}-hf" / "hf")
    ap.add_argument("--fixture", type=Path, default=TEST_FIXTURE)
    ap.add_argument("--out", type=Path, default=ROOT / "results")
    a = ap.parse_args(argv)
    try:
        files = build(a)
    except (CheckError, OSError, ValueError, KeyError) as exc:
        print(f"build_bundle: {exc!r}", file=sys.stderr)
        return 1
    write(a.out, files)
    print(f"wrote {len(files)} files under {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

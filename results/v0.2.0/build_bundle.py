"""Builds the v0.2.0 results: a new measurement of the Mentat-Hard test set on Ollama 0.40.13. The 15 pre-registered models, the two
Kev models added after pre-registration (kev_4b-q8_0, kev_0.8b-q8_0), and the baselines.

usage, from the repository root:
  python results/v0.2.0/build_bundle.py collect --r1 DIR --baselines DIR --kev-dev DIR [--r2 DIR] [--only ID ...] [--out DIR]
  python results/v0.2.0/build_bundle.py build [--check]

collect reads the run directories and writes, under --out (default results/v0.2.0): cells/<id>.json, predictions/<id>.jsonl.gz,
repeats/runs.json and repeats/predictions/<id>.r2.jsonl.gz (--r2), baselines.json (--baselines), record/added-models.json and
record/kev-pins.json, record/run-commits.json (each cell's repo commit and dirty flag). The run cards go to release/v0.2.0/runcards, or to --out/runcards when --out is given.
  --r1        the headline run: one folder per model id (runcell output, plus <id>.meta/child_stats.json, <id>.reproduce/)
  --baselines one folder per baseline id (heuristic, lexical, embedding, empty, gold_view, oracle, hf)
  --kev-dev   the Kev dev runs: <id>/ (runcell output) and <id>.floor.json (the relation floor of the dev build)
  --r2        the repeat run: one folder per model id (predictions only are kept)
Every check runs on every cell before anything is written. A failed check prints its reason and exits 1 with nothing written.
--only restricts the cells (a partial collection, for checks on the runs that exist).

build [--check] reads committed files only: results/v0.2.0/cells, predictions and baselines.json (a carried baseline's files are
the v0.1.0 ones), the fixture, the v0.1.0 leaderboard and results/index.json. It recomputes every interval with
tools/eval/league_ci.py's primitives (B = 2000, seed 0) and every rank interval (B = 20000, release_analysis's rank function), and
writes results/v0.2.0/leaderboard.json, leaderboard.csv and results/index.json. With --check it compares the bytes with the
committed files and exits 1 on any difference. The analysis (results/v0.2.0/analysis.json) is written by
tools/eval/release_analysis_v020.py, which pins the leaderboard by sha256.

The per-cell builders are the v0.1.0 ones, imported unchanged from results/v0.1.0/build_bundle.py. The analysis helpers (cubes,
rank intervals) are release_analysis's. The repeat-run checks are this file's own (collect_repeats).
"""

import argparse
import copy
import csv
import gzip
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools" / "eval"))

from mentat_bench import corrections, decision_scoring, ir, runcard  # noqa: E402
import league_ci as LC  # noqa: E402
import release_analysis as RA  # noqa: E402


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V010_BUILDER = ROOT / "results" / "v0.1.0" / "build_bundle.py"
RESULTS = ROOT / "results"
V020 = RESULTS / "v0.2.0"
RELEASE, BASED_ON = "v0.2.0", "v0.1.0"
RELEASE_DATE = "2026-11-02"  # the day the last runs finished (PST); set by the owner
SCHEMA = "mentat-bench-leaderboard/1"
REPEAT_SCHEMA = "mentat-bench-repeats/1"
BASELINES_SCHEMA = "mentat-bench-v020-baselines/1"
OLLAMA_VERSION = "0.40.13"
ENGINE_SHA256 = "38fc6fcc4b862634fbcc6e6de0ec654aaef1d0960202c76988f46fe28c0ce094"  # the engine the r1, r2 and dev runs used
REFERENCE = "d3_27b-q8_0"  # pre-registered; chosen on dev
# Commits a run may record: 29ce00d (the dirty tree before the reproduce tool was tracked; True = dirty allowed) and f1652ad, the
# reproduce-tool branch head (clean; its tree is the same as eaa9233's, the main commit of PR #5). The 29ce00d -> f1652ad diff touches
# only README.md, tests/test_reproduce.py and tools/reproduce.py. A run at any other commit is refused: extend this only on purpose.
ACCEPTED_COMMITS = {"29ce00d72f81f779b1e5a472287ba30f73ea8eac": True, "f1652ad60bf701da86bbedfe94319b5b21c7923b": False}
ADDED = {"kev_4b-q8_0": "kev:4b-q8_0", "kev_0.8b-q8_0": "kev:0.8b-q8_0"}  # id -> Ollama tag, added after pre-registration
ADDED_DISPLAY = {"kev_4b-q8_0": "Kev 4B", "kev_0.8b-q8_0": "Kev 0.8B"}
B_CI, SEED, B_RANK = 2000, 0, 20000  # the league_ci intervals (and the first of the rank draws); the rank intervals
ROUND = 6
ABSOLUTE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|(?<![\w.])/(?:c|home|tmp|var|Users)/")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
PERSONAL = (Path.home().name,)  # the user's name, read from this machine: it is not written into the code
REL, GRP, BIND, READ = "decide_relation", "decide_group", "decide_binding", "decide_reading"
FLOOR_FLAG = "--relation-floor"
REPEAT_PROTOCOL = ("owner re-measurement on the same machine: r2 is the run-to-run repeat of the r1 headline run; predictions only, "
                   "no cells")

bundle = _module("build_bundle_v010", V010_BUILDER)  # the per-cell builders, unchanged
CheckError, check, jload = bundle.CheckError, bundle.check, bundle.jload
TEST_FIXTURE, PREREG, OLLAMA = bundle.TEST_FIXTURE, bundle.PREREG, bundle.OLLAMA
TAG_OF = {key.replace(":", "_"): key for key in jload(PREREG)["models"]}  # id -> the prereg's Ollama tag
MODELS = tuple(TAG_OF) + tuple(ADDED)  # the 15 pre-registered models, then the two added Kev models
BASELINES = bundle.BASELINE_IDS + (bundle.HF_ID,)
DISPLAY = {**bundle.DISPLAY, **ADDED_DISPLAY}
_BR = None


def br():
    """docs/results/mentat-hard-results/build_results.py, imported unchanged (its depth, repair and abstention helpers)."""
    global _BR
    if _BR is None:
        _BR = bundle.load_build_results()
    return _BR


def tag_of(mid: str) -> str:
    return TAG_OF.get(mid) or ADDED[mid]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_hash() -> str:
    return corrections.content_hash(corrections.content_hashes(TEST_FIXTURE))


def flag_map(flags: list[str]) -> dict:
    """The build flags as a flag -> value map (a flag with no value maps to None), with paths written with forward slashes."""
    out, i = {}, 0
    while i < len(flags):
        word = flags[i].replace("\\", "/")
        if word.startswith("--") and i + 1 < len(flags) and not flags[i + 1].startswith("--"):
            out[word] = flags[i + 1].replace("\\", "/")
            i += 2
        else:
            out[word] = None
            i += 1
    return out


def floor_of(flags: list[str]) -> float:
    check(flags.count(FLOOR_FLAG) == 1, f"the build flags name {FLOOR_FLAG} {flags.count(FLOOR_FLAG)} times")
    return float(flags[flags.index(FLOOR_FLAG) + 1])


def without_floor(flags: list[str]) -> dict:
    return {k: v for k, v in flag_map(flags).items() if k != FLOOR_FLAG}


def cell_checks(cdir: Path, name: str, system: bool) -> dict:
    """The checks every collected cell passes before its data is used: the files, completeness, the fixture, the commit, the engine,
    the Ollama version. A system_one cell also needs its build policy, decisions and child stats. Returns the cell, its build
    summary, the build policy's model block and the child stats (None for a baseline without them)."""
    need = ["cell.json", "score.json", "build_summary.json"]
    if system:
        need += ["score_decisions.json", "submission/build_policy.json", "submission.build/decisions.jsonl"]
    for rel in need:
        check((cdir / rel).is_file(), f"{name}: missing {rel}")
    stats = cdir.parent / f"{cdir.name}.meta" / "child_stats.json"
    check(stats.is_file() or not system, f"{name}: missing {cdir.name}.meta/child_stats.json")
    cell = jload(cdir / "cell.json")
    check(cell.get("status") == "complete" and not cell.get("failed_step"), f"{name}: the cell is not complete")
    check(cell["fixture_stamp"]["content_hash"] == fixture_hash(), f"{name}: the fixture is not the test fixture")
    repo = cell["runtime"]["repo"]
    check(repo["commit"] in ACCEPTED_COMMITS and repo["dirty"] is ACCEPTED_COMMITS[repo["commit"]],
          f"{name}: the repo is at {repo['commit']} (dirty {repo['dirty']}); accepted: {sorted(ACCEPTED_COMMITS)}")
    check(cell["runtime"]["engine"]["sha256"] == ENGINE_SHA256, f"{name}: the engine is not the one the runs used")
    policy, model = None, None
    if (cdir / "submission" / "build_policy.json").is_file():
        policy = jload(cdir / "submission" / "build_policy.json")
        model = policy["model"]
        check(model["ollama_version"] == OLLAMA_VERSION, f"{name}: Ollama {model['ollama_version']}, not {OLLAMA_VERSION}")
    return {"cell": cell, "build": jload(cdir / "build_summary.json"), "model": model, "policy": policy,
            "stats": jload(stats) if stats.is_file() else None}


def scored_items(cdir: Path, version: int) -> tuple[dict, list[dict], dict]:
    """(score, items, counts) of a decision cell: the score is decision_scoring's, and must equal its score_decisions.json."""
    items, counts = decision_scoring.load(TEST_FIXTURE / "gold" / "suite_labels.jsonl", TEST_FIXTURE / "candidates.jsonl",
                                          cdir / "submission.build" / "decisions.jsonl")
    scored = decision_scoring.score(items, counts, version)
    check(br().same(scored, jload(cdir / "score_decisions.json")), f"{cdir.name}: the recomputed score differs from score_decisions")
    return scored, items, counts


def prediction_blob(cdir: Path, name: str, version: int, cands: dict, depths: dict) -> tuple[list[dict], bytes]:
    """The prediction rows of a decision cell and their encoding; the rows must rescore to the cell's score_decisions.json."""
    rows = bundle.prediction_rows(cdir, TEST_FIXTURE, cands, depths)
    check(len(rows) == 10716, f"{name}: {len(rows)} prediction rows, the fixture has 10716 scored items")
    blob = bundle.encode_predictions(rows)
    check(bundle.same_scored(bundle.rescore(blob, version), jload(cdir / "score_decisions.json"), None, br()),
          f"{name}: the predictions do not rescore to score_decisions.json")
    return rows, blob


def scrubbed(data: bytes) -> None:
    """Refuses an output that holds an absolute path, an e-mail address or a personal word (text or gzip)."""
    text = gzip.decompress(data).decode("utf-8") if data[:2] == b"\x1f\x8b" else data.decode("utf-8")
    check(not ABSOLUTE.search(text), "an absolute or personal path in an output")
    check(not EMAIL.search(text), "an e-mail address in an output")
    for word in PERSONAL:
        check(word not in text, f"the personal word {word!r} in an output")


def cell_document(mid: str, cdir: Path, kind: str, predictions: str | None, parts: dict, score: dict, items: list[dict],
                  counts: dict, depths: dict) -> dict:
    """The v0.1.0 cell document, plus the blocks build needs (no run directory is read in build): the model block, the counts,
    depth, coverage, repair and abstention; and the floor of a system_one model."""
    doc = bundle.cell_doc(mid, cdir, kind, predictions)
    doc["model"] = parts["model"]
    doc["counts"] = counts
    doc["depth"] = br().depth_table(items, depths)
    doc["coverage"] = {op: score["ops"][op]["coverage"] for op in score["ops"]}
    doc["repair"] = br().repair_fields(parts["build"])
    doc["abstention"] = br().abstention_fields(cdir, items, score, parts["stats"]) if kind == "system_one_model" else None
    return doc


def kev_floor(kev_dev: Path, mid: str) -> dict:
    """The dev build's relation floor for a Kev model, from its floor file: the floor itself, or the largest float when the model
    names no relation (the harness argument then is sys.float_info.max, as for the pre-registered models)."""
    path = kev_dev / f"{mid}.floor.json"
    check(path.is_file(), f"{mid}: no dev floor file {path}")
    doc = jload(path)
    check(isinstance(doc.get("names_relations"), bool), f"{mid}: the floor file has no names_relations")
    value = float(doc["floor"]) if doc["names_relations"] else sys.float_info.max
    # harness_args is the argument the dev build used: a string ("--relation-floor X") or, in older files, a list; it must name the floor
    args = doc.get("harness_args")
    words = args.split() if isinstance(args, str) else list(args or [])
    check(bool(words) and (words[-1] == repr(value) or float(words[-1]) == value),
          f"{mid}: harness_args {args!r} does not name the floor {value!r}")
    return {"value": value, "names_relations": doc["names_relations"]}


def kev_dev(kev_dev_dir: Path, mid: str) -> dict:
    """The dev headline and parts of a Kev model, from its dev run's score_decisions.json (the dev fixture's scores)."""
    cdir = kev_dev_dir / mid
    check((cdir / "score_decisions.json").is_file(), f"{mid}: no dev run at {cdir}")
    score = jload(cdir / "score_decisions.json")
    ops = score["ops"]
    return {"headline": score["headline"]["relation_group_mcc"], "relation": ops[REL]["mcc"], "group": ops[GRP]["mcc"],
            "binding": ops[BIND]["mcc"], "reading": ops[READ]["mcc"] if READ in ops else None}


def run_flips(cells_v010_rows: list[dict], rows: list[dict]) -> dict:
    """Decision flips between two prediction row lists, overall and per op, over the items both sides have."""
    def key(r):
        return (r["qid"], r["op"], r.get("reading"))
    before = {key(r): r["pred"] for r in cells_v010_rows}
    after = {key(r): r["pred"] for r in rows}
    per_op = {}
    for k in before.keys() & after.keys():
        per_op[k[1]] = per_op.get(k[1], 0) + int(before[k] != after[k])
    return {"flips": sum(per_op.values()), "per_op": per_op, "items": len(before.keys() & after.keys())}


def v010_rows(mid: str) -> list[dict]:
    blob = (RESULTS / "v0.1.0" / "predictions" / f"{mid}.jsonl.gz").read_bytes()
    return [json.loads(line) for line in gzip.decompress(blob).decode("utf-8").splitlines()]


def collect_models(args, version: int, cands: dict, depths: dict, ids: list[str], outputs: dict) -> dict:
    """The system_one cells of the r1 run, checked against the pre-registration (or, for the Kev models, their dev floor files)
    and against the v0.1.0 cells (build flags, profile, top_k, batch size). Returns {id: cell document}."""
    prereg = jload(PREREG)
    pre_prompt = prereg["child"].split("prompt_sha256 ")[1].split(",")[0]
    pins = jload(OLLAMA)
    docs = {}
    candidates_sha = sha256((TEST_FIXTURE / "candidates.jsonl").read_bytes())
    for mid in (m for m in ids if m in MODELS):
        cdir, name, tag = args.r1 / mid, f"{mid} (r1)", tag_of(mid)
        parts = cell_checks(cdir, name, system=True)
        cell, model = parts["cell"], parts["model"]
        check(model["revision"] == pins[tag]["digest"], f"{name}: the model digest is not the {tag} pin")
        check(model["prompt_sha256"] == pre_prompt and "option_order" not in model, f"{name}: the prompt is not the pre-registered one")
        if mid in TAG_OF:
            pre = prereg["models"][tag]
            check(model["revision"] == pre["digest"], f"{name}: the model digest differs from the pre-registration")
            floor = {"value": float(pre["relation_floor"]), "names_relations": bool(pre["names_relations"])}
        else:
            floor = kev_floor(args.kev_dev, mid)
        check(floor_of(cell["build_flags"]) == floor["value"], f"{name}: the relation floor differs from its pre-registered or dev value")
        check(parts["policy"]["candidates_sha256"] == candidates_sha, f"{name}: the candidates differ from the fixture's")
        ref = jload(RESULTS / "v0.1.0" / "cells" / f"{mid if mid in TAG_OF else REFERENCE}.json")["cell"]
        check(without_floor(cell["build_flags"]) == without_floor(ref["build_flags"]), f"{name}: the build flags differ from v0.1.0's")
        check(cell["runtime"].get("profile_sha256") == ref["runtime"]["profile_sha256"], f"{name}: the profile differs from v0.1.0's")
        check(cell["top_k"] == ref["top_k"] and cell["batch_size"] == ref["batch_size"], f"{name}: top_k or batch size differs")
        score, items, counts = scored_items(cdir, version)
        rows, blob = prediction_blob(cdir, name, version, cands, depths)
        abstained = sum(r["abstained"] for r in rows if r["op"] == REL)
        check(abstained == parts["stats"]["ops"]["decide_relation"]["abstained_overlength"], f"{name}: abstentions differ from the stats")
        pred = f"results/{RELEASE}/predictions/{mid}.jsonl.gz"
        doc = cell_document(mid, cdir, "system_one_model", pred, parts, score, items, counts, depths)
        doc["floor"] = floor
        doc["dev"] = kev_dev(args.kev_dev, mid) if mid in ADDED else None
        if mid in TAG_OF:  # the flips against the v0.1.0 cell, which the reproduce report of this run also counts
            before = v010_rows(mid)
            doc["version_shift"] = {**run_flips(before, rows), "headline_delta": score["headline"]["relation_group_mcc"]
                                    - jload(RESULTS / "v0.1.0" / "cells" / f"{mid}.json")["score_decisions"]["headline"]["relation_group_mcc"]}
            report = args.r1 / f"{mid}.reproduce" / "report.json"
            if report.is_file():
                rep = jload(report)
                check(rep["decisions"]["flips"] == doc["version_shift"]["flips"],
                      f"{name}: {doc['version_shift']['flips']} flips against v0.1.0; its report says {rep['decisions']['flips']}")
                check(abs(rep["metrics"]["headline"]["delta"] - doc["version_shift"]["headline_delta"]) <= 1e-9,
                      f"{name}: the headline change differs from its report")
        card = (json.dumps(runcard.runcard(cdir), indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        text = bundle.dump_json(doc)
        for data in (text, blob, card):
            scrubbed(data)
        outputs[f"cells/{mid}.json"] = text
        outputs[f"predictions/{mid}.jsonl.gz"] = blob
        outputs[f"runcards/{mid}.json"] = card
        docs[mid] = doc
    return docs


def collect_repeats(args, version: int, cands: dict, depths: dict, ids: list[str], outputs: dict) -> None:
    """The r2 repeat runs: predictions only, each checked against the r1 run of the same model (the same command, the same engine)
    and rescored. A model without an r2 folder is listed in left_out."""
    records, left_out = [], []
    for mid in (m for m in ids if m in MODELS):
        cdir = args.r2 / mid
        if not (cdir / "cell.json").is_file():
            left_out.append({"id": mid, "tag": "r2", "reason": "no cell in the r2 folder"})
            continue
        name = f"{mid} (r2)"
        parts = cell_checks(cdir, name, system=True)
        r1 = jload(args.r1 / mid / "cell.json")
        cell = parts["cell"]
        check(without_floor(cell["build_flags"]) == without_floor(r1["build_flags"]), f"{name}: the build flags differ from r1's")
        check(cell["profile"].replace("\\", "/") == r1["profile"].replace("\\", "/")
              and cell["runtime"]["profile_sha256"] == r1["runtime"]["profile_sha256"], f"{name}: the profile differs from r1's")
        check(floor_of(cell["build_flags"]) == floor_of(r1["build_flags"]), f"{name}: the relation floor differs from r1's")
        rows, blob = prediction_blob(cdir, name, version, cands, depths)
        decisions = sha256((cdir / "submission.build" / "decisions.jsonl").read_bytes())
        published = sha256((args.r1 / mid / "submission.build" / "decisions.jsonl").read_bytes())
        # the answers: r1's rows against r2's (labels only; the probabilities are not compared here)
        r1_rows, _ = prediction_blob(args.r1 / mid, f"{mid} (r1)", version, cands, depths)
        r1_label = {(r["qid"], r["op"], r.get("reading")): r["pred"] for r in r1_rows}
        label_flips = sum(r1_label[(r["qid"], r["op"], r.get("reading"))] != r["pred"] for r in rows)
        outputs[f"repeats/predictions/{mid}.r2.jsonl.gz"] = blob
        records.append({
            "id": mid, "tag": "r2", "backend": "cuda", "option_seed": None, "protocol": REPEAT_PROTOCOL,
            "status": cell["status"], "rc": 0, "ollama_version": OLLAMA_VERSION, "model_digest": parts["model"]["revision"],
            "prompt_sha256": parts["model"]["prompt_sha256"], "code_commit": cell["runtime"]["repo"]["commit"],
            "engine_sha256": ENGINE_SHA256, "decisions_sha256": decisions, "published_decisions_sha256": published,
            "identical_to_published_decisions": decisions == published,
            "identical_labels": label_flips == 0, "label_flips": label_flips,
            "headline_relation_group_mcc": bundle.rescore(blob, version)["headline"]["relation_group_mcc"],
            "relation_abstentions": sum(r["abstained"] for r in rows if r["op"] == REL),
            "predictions": f"results/{RELEASE}/repeats/predictions/{mid}.r2.jsonl.gz",
            "predictions_sha256": sha256(blob),
        })
    doc = {"schema": REPEAT_SCHEMA, "protocol": REPEAT_PROTOCOL, "published_run": "r1", "runs": records, "left_out": left_out}
    outputs["repeats/runs.json"] = bundle.dump_json(doc)


def without_usage(score: dict) -> dict:
    return {k: v for k, v in score.items() if k != "usage"}


def collect_baselines(args, version: int, cands: dict, depths: dict, ids: list[str], outputs: dict) -> None:
    """The baseline cells. A cell whose predictions (or, for the two with none, whose score.json) equal the v0.1.0 cell's is carried
    from v0.1.0, and its bytes stay there. Any other is published here as a new run."""
    status = {}
    for mid in (b for b in ids if b in BASELINES):
        cdir = args.baselines / mid
        if not (cdir / "cell.json").is_file():
            continue
        old = jload(RESULTS / "v0.1.0" / "cells" / f"{mid}.json")
        decisions = (cdir / "score_decisions.json").is_file()
        if decisions:
            rows, blob = prediction_blob(cdir, mid, version, cands, depths)
            same = (RESULTS / "v0.1.0" / "predictions" / f"{mid}.jsonl.gz").read_bytes() == blob
        else:
            # no predictions: the score must equal v0.1.0's once usage (timings, memory, call counts) is removed; the seal digest
            # hashes the submission directory with its timings, so it is not compared
            same = without_usage(jload(cdir / "score.json")) == without_usage(old["score"])
        if same:
            status[mid] = {"status": "carried", "from": BASED_ON}
            continue
        parts = cell_checks(cdir, mid, system=False)
        if decisions:
            score, items, counts = scored_items(cdir, version)
            doc = cell_document(mid, cdir, KIND[mid], f"results/{RELEASE}/predictions/{mid}.jsonl.gz", parts, score, items, counts, depths)
            outputs[f"predictions/{mid}.jsonl.gz"] = blob
        else:
            doc = bundle.cell_doc(mid, cdir, KIND[mid], None)
        outputs[f"cells/{mid}.json"] = bundle.dump_json(doc)
        status[mid] = {"status": "rerun", "from": None}
    outputs["baselines.json"] = bundle.dump_json({"schema": BASELINES_SCHEMA, "release": RELEASE, "based_on": BASED_ON,
                                                  "baselines": status})


KIND = bundle.KIND


def collect(args) -> int:
    version = decision_scoring.fixture_version(TEST_FIXTURE)
    check(version == 3, f"the fixture is version {version}, not 3")
    cands = {row["qid"]: row for row in ir.read_jsonl(TEST_FIXTURE / "candidates.jsonl")}
    depths = br().label_depths(TEST_FIXTURE)
    ids = list(args.only) if args.only else list(MODELS) + list(BASELINES)
    for mid in ids:
        check(mid in MODELS or mid in BASELINES, f"{mid}: not a model or baseline of v0.2.0")
    outputs: dict[str, bytes] = {}
    docs = collect_models(args, version, cands, depths, ids, outputs)
    if args.r2 is not None:
        collect_repeats(args, version, cands, depths, ids, outputs)
    if args.baselines is not None:
        collect_baselines(args, version, cands, depths, ids, outputs)
    pins = jload(OLLAMA)
    outputs["record/run-commits.json"] = bundle.dump_json({mid: {"commit": doc["cell"]["runtime"]["repo"]["commit"],
                                                                  "dirty": doc["cell"]["runtime"]["repo"]["dirty"]}
                                                           for mid, doc in docs.items()})
    outputs["record/run-commits.json"] = bundle.dump_json({mid: {"commit": doc["cell"]["runtime"]["repo"]["commit"],
                                                                  "dirty": doc["cell"]["runtime"]["repo"]["dirty"]}
                                                           for mid, doc in docs.items()})
    outputs["record/added-models.json"] = added_record(docs)
    outputs["record/kev-pins.json"] = bundle.dump_json({tag_of(mid): pins[tag_of(mid)] for mid in ADDED})
    for data in outputs.values():
        scrubbed(data)  # every file collect writes, the repeat and baseline files included
    out = args.out
    release_dir = ROOT / "release" / RELEASE if out == V020 else out  # the record and the run cards live with the release
    written = 0
    for name, data in outputs.items():
        if name.startswith("runcards/"):
            dest = release_dir / "runcards" / name.split("/", 1)[1]
        elif name.startswith("record/"):
            dest = release_dir / name
        else:
            dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written += 1
    print(f"collect: wrote {written} files ({len(docs)} system_one cells) under {out} and {release_dir}", file=sys.stderr)
    return 0


def added_record(docs: dict) -> bytes:
    """The two added models: their pins, their dev floors and dev headlines, and the flag that says they were added later."""
    pins = jload(OLLAMA)
    rows = []
    for mid in ADDED:
        if mid not in docs:
            continue
        tag = tag_of(mid)
        rows.append({"id": mid, "ollama_tag": tag, "family": "kev", "digest": pins[tag]["digest"], "license": pins[tag]["license"],
                     "ollama_version": pins[tag]["ollama_version"], "size_bytes": pins[tag]["size"],
                     "added_after_preregistration": True, "relation_floor": docs[mid]["floor"]["value"],
                     "names_relations": docs[mid]["floor"]["names_relations"], "dev_headline": docs[mid]["dev"]["headline"]})
    return bundle.dump_json(rows)


# ---------------------------------------------------------------- build (committed files only)

def rounded(x):
    return None if x is None else round(float(x), ROUND)


def run_draws(rows, score: dict, mid: str, fx: dict, W: np.ndarray, ones: np.ndarray) -> dict:
    """{metric: (point, draws over W)} of one run: the league_ci components from the prediction rows (the cubes and the rank
    helpers of the release analysis), the held-out and seen relation MCCs, and Q_N from score.json's per-query utilities. A run
    without rows (empty, oracle) has Q_N only."""
    out = {}
    if rows is not None:
        def mcc(cube):
            return LC._mcc(cube, ones)[0], RA._mcc_draws(cube, W)
        out["relations"] = mcc(RA._op_cube(rows, REL, fx))
        out["groups"] = mcc(RA._op_cube(rows, GRP, fx))
        out["bindings"] = mcc(RA._op_cube(rows, BIND, fx))
        if any(r["op"] == READ for r in rows):
            out["readings"] = mcc(RA._op_cube(rows, READ, fx))
        out["held_out_relations"] = mcc(RA._op_cube([r for r in rows if r["op"] == REL and r["held_out"]], REL, fx))
        out["seen_relations"] = mcc(RA._op_cube([r for r in rows if r["op"] == REL and not r["held_out"]], REL, fx))
        (rp, rd), (gp, gd) = out["relations"], out["groups"]
        out["headline"] = ((rp + gp) / 2, (rd + gd) / 2)  # NaN propagates: null when a part is
    fam_sum, fam_n = LC._family_sums(Path(mid), score, fx)
    run = {"fam_sum": fam_sum, "fam_n": fam_n}
    out["q_n"] = (LC._q_n(run, ones)[0], LC._q_n(run, W))
    return out


def load_run(mid: str, status: dict) -> dict:
    """The cell document and prediction rows (None when the run has none) of a model or baseline, from the release or, when the
    baseline is carried, from the v0.1.0 files."""
    if status.get(mid) == "carried":
        base = RESULTS / BASED_ON
        cell = jload(base / "cells" / f"{mid}.json")
        pred = base / "predictions" / f"{mid}.jsonl.gz"
    else:
        cell = jload(V020 / "cells" / f"{mid}.json")
        pred = V020 / "predictions" / f"{mid}.jsonl.gz"
    rows = None
    if pred.is_file():
        rows = [json.loads(line) for line in gzip.decompress(pred.read_bytes()).decode("utf-8").splitlines()]
    return {"cell": cell, "rows": rows}


def baseline_status() -> dict:
    """baselines.json: each baseline's status, carried (from v0.1.0) or rerun (published here)."""
    path = V020 / "baselines.json"
    check(path.is_file(), "results/v0.2.0/baselines.json is missing")
    doc = jload(path)
    check(doc["schema"] == BASELINES_SCHEMA, "baselines.json has another schema")
    check(sorted(doc["baselines"]) == sorted(BASELINES), f"baselines.json names {sorted(doc['baselines'])}, not the 7 baselines")
    return {mid: entry["status"] for mid, entry in doc["baselines"].items()}


def statistic(point, draws, ci: bool = True) -> dict:
    """{point, ci95} of one metric from its point and its draws over W (the first B_CI draws give the interval)."""
    if point is None or np.isnan(point):
        return {"point": None, "ci95": None}
    if not ci or draws is None:
        return {"point": rounded(point), "ci95": None}
    lo, hi = LC._interval(draws[:B_CI])
    return {"point": rounded(point), "ci95": None if lo is None else [rounded(lo), rounded(hi)]}


def gap_statistic(ref: dict, run: dict, name: str) -> dict | None:
    """Reference minus run, paired within each draw of the first B_CI draws: {point, ci95}, or None when either side lacks it."""
    if name not in ref or name not in run:
        return None
    (rp, rd), (mp, md) = ref[name], run[name]
    if rp is None or mp is None or np.isnan(rp) or np.isnan(mp):
        return {"point": None, "ci95": None}
    diff = rd[:B_CI] - md[:B_CI]
    lo, hi = LC._interval(diff)
    return {"point": rounded(rp - mp), "ci95": None if lo is None else [rounded(lo), rounded(hi)]}


METRIC_SOURCE = {"headline": "headline", "relation": "relations", "group": "groups", "binding": "bindings",
                 "reading": "readings", "held_out_relation": "held_out_relations", "seen_relation": "seen_relations",
                 "q_n_repaired": "q_n"}
GAP_STATS = ("headline", "relation", "group", "binding", "q_n_repaired")


def metrics_of(draws: dict, strict: float | None) -> dict:
    out = {}
    for metric, source in METRIC_SOURCE.items():
        if source not in draws:
            out[metric] = None
            continue
        point, values = draws[source]
        out[metric] = statistic(point, values, ci=metric != "seen_relation")  # seen is a point (not bootstrapped), as in v0.1.0
    out["q_n_strict"] = {"point": rounded(strict), "ci95": None}
    return out


def flags_of(mid: str, cell: dict, rows, metrics: dict, floor_disabled, abstention, coverage: dict) -> list[str]:
    """Data-derived flags only (the rules read the cell and the predictions), plus the provenance flag of an added model."""
    flags = []
    if mid == REFERENCE:
        flags.append("reference")
    if mid in ADDED:
        flags.append("not_preregistered")
    if rows is None:
        flags.append("q_n_only")
    if mid in MODELS:
        if cell["cell"].get("base_repaired") is False:
            flags.append("base_not_repaired")
        if floor_disabled:
            flags.append("names_no_relations_under_floor")
        if abstention and abstention.get("relation_abstained", 0) > 0:
            flags.append("abstains_for_length")
        if rows is not None and not any(r["op"] == BIND and r["pred"] == "unresolved" for r in rows):
            flags.append("never_flags_ambiguous")
    if coverage and coverage.get(GRP) == 0 and coverage.get(READ) == 0 and rows is not None:
        flags.append("no_group_or_reading_decisions")
    group_answers = {r["pred"] for r in rows or () if r["op"] == GRP and r["pred"] != "__missing__"}
    if len(group_answers) == 1:
        # one class for every group decision: the group MCC is 0 by construction, so the headline is the relation MCC over 2
        flags.append("constant_group_output")
    if mid == "hf" and FLOOR_FLAG not in cell["cell"]["build_flags"]:
        flags.append("nonstandard_build_flags")
    return flags


def build_outputs() -> dict[str, bytes]:
    """The v0.2.0 leaderboard, its CSV and the results index, in memory, from the committed files only."""
    fx = LC.read_fixture(TEST_FIXTURE)
    L = len(fx["leagues"])
    check(L == 18, f"the fixture has {L} leagues, the release is for 18")
    ones = np.ones((1, L), dtype=np.int64)
    W = LC.draws(B_RANK, SEED, L)
    check(np.array_equal(W[:B_CI], LC.draws(B_CI, SEED, L)), "the first 2000 rank draws are not the league_ci draws")
    version = decision_scoring.fixture_version(TEST_FIXTURE)
    status = baseline_status()
    prior_lb = jload(RESULTS / BASED_ON / "leaderboard.json")
    prior = {e["id"]: e for e in prior_lb["models"] + prior_lb["baselines"]}
    pins = jload(OLLAMA)
    runs, draws, points, commits = {}, {}, {}, set()
    for mid in MODELS + BASELINES:
        run = load_run(mid, status)
        cell, rows = run["cell"], run["rows"]
        if rows is not None:
            blob = bundle.encode_predictions(rows)
            check(bundle.same_scored(bundle.rescore(blob, version), cell["score_decisions"], None, br()),
                  f"{mid}: the committed predictions do not rescore to the cell's score_decisions")
        if status.get(mid) != "carried" and mid in MODELS:
            commits.add((cell["cell"]["runtime"]["repo"]["commit"], cell["cell"]["runtime"]["repo"]["dirty"]))
        draws[mid] = run_draws(rows, cell["score"], mid, fx, W, ones)
        points[mid] = {k: v[0] for k, v in draws[mid].items()}
        runs[mid] = run
    headline = {m: draws[m]["headline"][1] for m in MODELS}
    V = np.vstack([headline[m] for m in MODELS])
    P = [points[m]["headline"] for m in MODELS]
    ranks = RA._ranks(list(MODELS), V, P)
    ranking = sorted(MODELS, key=lambda m: -points[m]["headline"])
    # the point rank as the analysis has it: 1 + the models with a strictly higher point (tied models share a rank)
    point_rank = {m: 1 + int(sum(points[j]["headline"] > points[m]["headline"] for j in MODELS)) for m in MODELS}
    check(all(ranks[m]["rank"] == point_rank[m] for m in MODELS), "the rank intervals' points differ from the point ranks")
    entries = {}
    for mid in MODELS + BASELINES:
        cell, rows = runs[mid]["cell"], runs[mid]["rows"]
        strict = None if cell["cell"].get("base_repaired") is None else (
            0.0 if cell["cell"]["base_repaired"] else points[mid]["q_n"])
        metrics = metrics_of(draws[mid], strict)
        entries[mid] = (metrics, rows, cell, strict)
    rows_info = {}
    for mid in MODELS + BASELINES:
        metrics, rows, cell, _ = entries[mid]
        ref = REFERENCE
        gap = None
        if mid != ref:
            gap = {stat: gap_statistic(draws[ref], draws[mid], METRIC_SOURCE[stat]) for stat in GAP_STATS}
        rows_info[mid] = gap
    models = [model_entry(mid, entries[mid], rows_info[mid], ranks.get(mid), point_rank.get(mid), pins, prior, status)
              for mid in ranking]
    baselines = [baseline_entry(mid, entries[mid], rows_info[mid], pins, prior, status) for mid in BASELINES]
    everything = models + baselines
    lb = leaderboard(models, baselines, ranking, prior_lb, commits, L, everything)
    lb_bytes = bundle.dump_json(lb)
    csv_text = leaderboard_csv(lb)
    index_bytes = index_doc()
    out = {"leaderboard.json": lb_bytes, "leaderboard.csv": csv_text, "index.json": index_bytes}
    for name, data in out.items():
        scrubbed(data)
    print(f"build: {len(models)} models, {len(baselines)} baselines; fixture {version}; checked {len(rows_info)} runs",
          file=sys.stderr)
    return out


def model_entry(mid: str, parts: tuple, gap: dict, rank_iv: dict | None, rank: int, pins: dict, prior: dict, status: dict) -> dict:
    metrics, rows, cell, strict = parts
    tag = tag_of(mid)
    art = pins[tag]
    floor = cell["floor"]
    disabled = floor["value"] == sys.float_info.max
    if mid in ADDED:
        dev = dict(cell["dev"])
    else:
        dev = dict(prior[mid]["dev"])  # the dev block is carried from v0.1.0 (the dev runs are not repeated)
    # the change is test minus dev only where both are 0.40.13 (the Kev models' dev runs). The 15 carried dev blocks are 0.40.11
    # dev results, so their change is null rather than a difference across versions
    if mid in ADDED and dev is not None and metrics["headline"]["point"] is not None and dev.get("headline") is not None:
        dev["headline_change_test_minus_dev"] = rounded(metrics["headline"]["point"] - dev["headline"])
    elif dev is not None:
        dev["headline_change_test_minus_dev"] = None
    wall = cell["build_summary"].get("wall_seconds")
    entry = {"id": mid, "display_name": DISPLAY[mid], "kind": "system_one_model", "family": tag.split(":", 1)[0], "rank": rank,
             "ollama_tag": tag, "hf_model": None, "hf_revision": None, "digest": art["digest"], "size_bytes": art["size"],
             "license": bundle.canon_license(art["license"]), **bundle.tag_facts(tag)}
    entry["child"] = {"id": "systemone-choice", "version": cell["model"]["child_version"],
                      "prompt_sha256": cell["model"]["prompt_sha256"]}
    entry["relation_floor"] = None if disabled else floor["value"]
    entry["relation_floor_disabled"] = disabled
    entry["names_relations_under_floor"] = floor["names_relations"]
    entry["dev"] = dev
    entry.update({"cell": f"results/{RELEASE}/cells/{mid}.json", "predictions": f"results/{RELEASE}/predictions/{mid}.jsonl.gz",
                  "runcard": f"release/{RELEASE}/runcards/{mid}.json"})
    entry["metrics"] = metrics
    entry["gap_to_reference"] = gap
    entry["rank_interval"] = None if rank_iv is None else {"lo": rank_iv["ci95"][0], "hi": rank_iv["ci95"][1],
                                                            "median": rank_iv["median"]}
    entry["depth_accuracy"] = cell["depth"]
    entry["coverage"] = cell["coverage"]
    entry["counts"] = cell["counts"]
    entry["abstention"] = cell["abstention"]
    entry["repair"] = cell["repair"]
    entry["runtime"] = {"build_wall_min": None if wall is None else round(wall / 60, 1), "build_wall_s": wall,
                        "private_build_wall_min": None}
    entry["private"] = None
    entry["flags"] = flags_of(mid, cell, rows, metrics, disabled, cell["abstention"], cell["coverage"])
    entry["notes"] = []
    return entry


def carry_problems(row: dict, old: dict) -> list[str]:
    """Where a carried baseline's row differs from v0.1.0's row. Two fields may differ: the gap (against this release's reference),
    and private, which must be null: v0.2.0 publishes no private-split results (v0.1.0 keeps them)."""
    a = {k: v for k, v in row.items() if k not in ("gap_to_reference", "private")}
    b = {k: v for k, v in old.items() if k not in ("gap_to_reference", "private")}
    problems = [k for k in sorted(set(a) | set(b)) if k not in a or k not in b or a[k] != b[k]]
    if row.get("private") is not None:
        problems.append("private")
    return problems


def carried_metric_problems(metrics: dict, old_metrics: dict, has_rows: bool) -> list[str]:
    """The metrics recomputed from the carried files must be v0.1.0's: every point, and every interval where the run has rows. A run
    without rows has Q_N only, and v0.1.0 gives it no interval."""
    problems = []
    for name in sorted(set(metrics) | set(old_metrics)):
        got, want = metrics.get(name) or {}, old_metrics.get(name) or {}  # v0.1.0 writes null where a metric has no value
        if got.get("point") != want.get("point") or (has_rows and got.get("ci95") != want.get("ci95")):
            problems.append(name)
    return problems


def baseline_entry(mid: str, parts: tuple, gap: dict | None, pins: dict, prior: dict, status: dict) -> dict:
    metrics, rows, cell, strict = parts
    old = prior[mid]
    if status.get(mid) == "carried":
        # v0.1.0's row, field for field, except the gap and private (see carry_problems); the recomputed metrics must agree with it
        entry = copy.deepcopy(old)
        entry["gap_to_reference"] = gap if old["gap_to_reference"] is not None else None  # no gap where v0.1.0 has none
        entry["private"] = None
        problems = carry_problems(entry, old) + carried_metric_problems(metrics, old["metrics"], rows is not None)
        check(not problems, f"{mid}: the carried row differs from v0.1.0 in {problems}")
        return entry
    entry = {"id": mid, "display_name": DISPLAY[mid], "kind": KIND[mid], "family": None, "rank": None, "ollama_tag": None,
             "hf_model": None, "hf_revision": None, "digest": None, "size_bytes": None, "license": None, "params": None,
             "params_source": None, "quant": None, "child": None, "relation_floor": None, "relation_floor_disabled": None,
             "names_relations_under_floor": None, "rank_interval": None}
    if mid == bundle.HF_ID:
        pin = jload(bundle.HF_MODELS)["models"]["Qwen/Qwen2.5-1.5B-Instruct"]
        entry.update({"hf_model": "Qwen/Qwen2.5-1.5B-Instruct", "hf_revision": pin["revision"], "size_bytes": pin["bytes"],
                      "license": bundle.canon_license(pin["license"])})
    entry["dev"] = dict(cell["dev"]) if cell.get("dev") else None
    entry["cell"] = f"results/{RELEASE}/cells/{mid}.json"
    entry["predictions"] = None if rows is None else f"results/{RELEASE}/predictions/{mid}.jsonl.gz"
    entry["runcard"] = None
    entry["metrics"] = metrics
    entry["gap_to_reference"] = gap
    entry["depth_accuracy"] = cell.get("depth")
    entry["coverage"] = cell.get("coverage")
    entry["counts"] = cell.get("counts")
    entry["abstention"] = None
    entry["repair"] = cell.get("repair")
    wall = cell["build_summary"].get("wall_seconds")
    entry["runtime"] = {"build_wall_min": None if wall is None else round(wall / 60, 1), "build_wall_s": wall,
                        "private_build_wall_min": None}
    entry["private"] = None
    entry["flags"] = flags_of(mid, cell, rows, metrics, None, None, entry["coverage"] or {})
    entry["notes"] = []
    return entry


def resolved_rows(entries: list[dict]) -> list[dict]:
    out = []
    for e in entries:
        if e["gap_to_reference"] is None:
            continue
        for stat in ("headline", "relation", "group", "binding"):
            gap = e["gap_to_reference"].get(stat)
            if gap is None or gap["ci95"] is None:
                continue
            lo, hi = gap["ci95"]
            excludes = not (lo <= 0 <= hi)
            out.append({"run": e["id"], "statistic": stat, "gap_reference_minus_run": gap, "excludes_zero": excludes,
                        "higher": None if not excludes else ("reference" if gap["point"] > 0 else "run")})
    return out


def leaderboard(models, baselines, ranking, prior_lb, commits, L, everything) -> dict:
    return {
        "schema": SCHEMA,
        "release": RELEASE,
        "date": RELEASE_DATE,
        "benchmark": {
            "name": "Mentat-Hard",
            "measures": "How accurately a System One model (one pass of typed decisions: relations between propositions, groups "
                        "of members, bindings, readings) understands the logical relationships in league rule worlds.",
            "split": "mentat_hard_test (public, Mentat-Hard test set, revision 3.1)",
            "headline": {"metric": "relation_group_mcc", "definition": "mean of the relation MCC and the group MCC, on raw "
                         "(unfloored) decisions", "decided_on": "dev (owner decision 2026-10-30)"},
            "secondary": "binding_mcc",
            "diagnostics": ["reading_mcc", "held_out_relation_mcc", "seen_relation_mcc", "q_n_repaired", "q_n_strict"],
            "reference": REFERENCE,
        },
        "provenance": {
            "ollama_version": OLLAMA_VERSION,
            "engine_sha256": ENGINE_SHA256,
            "code_commits": [{"commit": c, "dirty": d} for c, d in sorted(commits)],
            "ci": {"method": "league bootstrap: B draws of the 18 leagues with replacement (random.Random('league_ci:0')), the "
                             "same draws for every run; 95% percentile intervals (numpy, linear); paired gaps, reference minus run; "
                             "rank intervals from B = 20000 of the same draws",
                   "B": B_CI, "seed": SEED, "leagues": L, "level": 0.95, "layout": "relation-group-headline/1",
                   "reference": REFERENCE, "rank_B": B_RANK},
            "fixture": {"name": "mentat_hard_test", "version": "3.1", "registry_version": "3",
                        "content_hash": fixture_hash(), "item_counts": prior_lb["provenance"]["fixture"]["item_counts"]},
            "analysis": {"path": f"results/{RELEASE}/analysis.json"},
            "prereg": f"release/{BASED_ON}/record/prereg.json",
            "record_from": BASED_ON,  # the release whose frozen records this release reuses (a version, as the site reads it)
            "record": f"release/{BASED_ON}/record",
            "added_models": f"release/{RELEASE}/record/added-models.json",
            "kev_pins": f"release/{RELEASE}/record/kev-pins.json",
            "repeats": f"results/{RELEASE}/repeats/runs.json",
            "baselines": f"results/{RELEASE}/baselines.json",
            "bundle_builder": f"results/{RELEASE}/build_bundle.py",
        },
        "ranking": ranking,
        "depth_buckets": prior_lb["depth_buckets"],
        "depth_n": prior_lb["depth_n"],
        "models": models,
        "baselines": baselines,
        "resolved_comparisons": resolved_rows(everything),
        "resolved_comparisons_note": "Only gaps to the reference are paired. An interval that excludes 0 is resolved; an interval "
                                     "that includes 0 is not. Overlapping intervals among non-reference runs are not a test of a "
                                     "tie, and the paired comparison among them is not computed (the analysis computes it).",
    }


CSV_COLUMNS = ["rank", "id", "display_name", "kind", "family", "ollama_tag", "digest", "params", "quant", "license", "size_bytes",
               "headline_point", "headline_lo", "headline_hi", "relation_point", "relation_lo", "relation_hi",
               "group_point", "group_lo", "group_hi", "binding_point", "binding_lo", "binding_hi",
               "reading_point", "reading_lo", "reading_hi", "held_out_relation_point", "held_out_relation_lo",
               "held_out_relation_hi", "seen_relation_point", "q_n_repaired_point", "q_n_repaired_lo", "q_n_repaired_hi",
               "q_n_strict", "gap_headline_point", "gap_headline_lo", "gap_headline_hi", "dev_headline", "relation_floor",
               "rank_lo", "rank_hi", "build_wall_min"]


def leaderboard_csv(lb: dict) -> bytes:
    """The leaderboard as one row per entry (models, then baselines), the same numbers as the JSON."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for e in lb["models"] + lb["baselines"]:
        m, g = e["metrics"], e["gap_to_reference"] or {}
        values = {"rank": e["rank"], "id": e["id"], "display_name": e["display_name"], "kind": e["kind"], "family": e["family"],
                  "ollama_tag": e["ollama_tag"], "digest": e["digest"], "params": e["params"], "quant": e["quant"],
                  "license": e["license"], "size_bytes": e["size_bytes"], "build_wall_min": e["runtime"]["build_wall_min"],
                  "q_n_strict": m["q_n_strict"]["point"], "dev_headline": (e["dev"] or {}).get("headline"),
                  "relation_floor": e["relation_floor"]}
        for name in ("headline", "relation", "group", "binding", "reading", "held_out_relation", "seen_relation", "q_n_repaired"):
            block = m[name]
            values[f"{name}_point"] = None if block is None else block["point"]
            if name != "seen_relation":
                values[f"{name}_lo"] = None if block is None or block["ci95"] is None else block["ci95"][0]
                values[f"{name}_hi"] = None if block is None or block["ci95"] is None else block["ci95"][1]
        gh = g.get("headline")
        values.update({"gap_headline_point": None if gh is None else gh["point"],
                       "gap_headline_lo": None if gh is None or gh["ci95"] is None else gh["ci95"][0],
                       "gap_headline_hi": None if gh is None or gh["ci95"] is None else gh["ci95"][1]})
        ri = e.get("rank_interval")
        values.update({"rank_lo": None if ri is None else ri["lo"], "rank_hi": None if ri is None else ri["hi"]})
        writer.writerow(["" if values.get(c) is None else values.get(c) for c in CSV_COLUMNS])
    return buf.getvalue().encode("utf-8")


def index_doc() -> bytes:
    """results/index.json with v0.2.0 as the latest release. v0.1.1 is superseded by it (the same index, built on the v0.1.1
    entry, which is kept as it is except its status); the v0.2.0 entry names its files by role."""
    index = jload(RESULTS / "index.json")
    versions = [v for v in index["versions"] if v["version"] != RELEASE]
    v011 = next(v for v in versions if v["version"] == "v0.1.1")
    check(v011["status"] in ("latest", "superseded"), "the index does not hold v0.1.1 as a release")
    record = f"release/{BASED_ON}/record"
    superseded = {**v011, "status": "superseded", "superseded_by": RELEASE,
                  "note": "superseded by v0.2.0: a new measurement on Ollama 0.40.13 (v0.1.1 measured on 0.40.11)"}
    latest = {
        "version": RELEASE, "status": "latest", "superseded_by": None, "benchmark": "Mentat-Hard", "fixture": "mentat_hard_test",
        "fixture_version": "3.1", "headline": "relation_group_mcc", "date": RELEASE_DATE, "record_from": BASED_ON,
        "ollama_version": OLLAMA_VERSION, "builder": f"results/{RELEASE}/build_bundle.py",
        "leaderboard_json": f"results/{RELEASE}/leaderboard.json", "leaderboard_csv": f"results/{RELEASE}/leaderboard.csv",
        "analysis_json": f"results/{RELEASE}/analysis.json", "cells_dir": f"results/{RELEASE}/cells",
        "predictions_dir": f"results/{RELEASE}/predictions", "runcards_dir": f"release/{RELEASE}/runcards",
        "readme": f"results/{RELEASE}/README.md", "repeats": f"results/{RELEASE}/repeats/runs.json",
        "deviations": f"release/{RELEASE}/record/README.md", "private_split": None,
        "prereg": f"{record}/prereg.md", "release_manifest": f"{record}/release_manifest.json",
        "release_gate_output": f"{record}/check_gates.md", "attestations": f"{record}/attestations.json",
    }
    kept = [superseded if v["version"] == "v0.1.1" else v for v in versions]
    return bundle.dump_json({**index, "latest": RELEASE, "versions": [latest] + kept})


def build(check_only: bool) -> int:
    outputs = build_outputs()
    targets = {"leaderboard.json": V020 / "leaderboard.json", "leaderboard.csv": V020 / "leaderboard.csv",
               "index.json": RESULTS / "index.json"}
    if check_only:
        stale = [name for name, data in outputs.items() if not targets[name].is_file() or targets[name].read_bytes() != data]
        if stale:
            print(f"build --check: differs from the committed files: {stale}", file=sys.stderr)
            return 1
        print("build --check: the committed files are what build writes", file=sys.stderr)
        return 0
    for name, data in outputs.items():
        targets[name].write_bytes(data)
    print(f"wrote {', '.join(outputs)}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect", help="check the run directories and write the release's cells, predictions and repeats")
    c.add_argument("--r1", type=Path, required=True, help="the headline run folder (one per model id)")
    c.add_argument("--r2", type=Path, help="the repeat run folder (one per model id)")
    c.add_argument("--baselines", type=Path, help="the baseline folder (one per baseline id)")
    c.add_argument("--kev-dev", type=Path, required=True, help="the Kev dev runs: <id>/ and <id>.floor.json")
    c.add_argument("--only", nargs="+", help="collect only these ids (a partial collection)")
    c.add_argument("--out", type=Path, default=V020, help="where to write (default results/v0.2.0)")
    b = sub.add_parser("build", help="write the v0.2.0 leaderboard, CSV and index from committed files")
    b.add_argument("--check", action="store_true", help="compare the bytes with the committed files and exit 1 on a difference")
    a = ap.parse_args(argv)
    try:
        if a.command == "collect":
            return collect(a)
        return build(a.check)
    except (CheckError, OSError, ValueError, KeyError, IndexError, TypeError, StopIteration) as exc:
        print(f"build_bundle v0.2.0: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

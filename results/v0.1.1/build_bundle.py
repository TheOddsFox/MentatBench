"""Builds the v0.1.1 results: the repeat runs (collect-repeats) and the analysis release (build).

usage, from the repository root:
  python results/v0.1.1/build_bundle.py collect-repeats [--runs-root DIR]
  python results/v0.1.1/build_bundle.py build [--check]

collect-repeats (the private machine, or any checkout with the local run directories; they are not committed) reads the run
directories and writes results/v0.1.1/repeats/: predictions/<id>.<tag>.jsonl.gz, cells/<id>.<tag>.json and runs.json. The tags are
r1 (the published run), r2 and r3 (CUDA repeats), shuffle (the option-shuffle pass, MB_S1_OPTION_SEED) and vulkan_r2 and vulkan_r3
(a backend diagnostic, on d3_9b-q8_0 only). Every run is checked before anything is written. shuffle covers all 15 system_one
models; a shuffle run that is missing, incomplete or has a non-zero exit code is left out and listed on stderr. The diagnostic is
labelled as such: the leaderboard headline stays the single pre-registered run.

build [--check] reads committed files only (results/v0.1.0/, results/v0.1.1/analysis.json, results/index.json) and writes
results/v0.1.1/leaderboard.json, results/v0.1.1/leaderboard.csv and results/index.json. With --check it compares the bytes with the
committed files and exits 1 on any difference.

The per-run cell and prediction builders are the v0.1.0 ones, imported unchanged from results/v0.1.0/build_bundle.py.
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V010_BUILDER = ROOT / "results" / "v0.1.0" / "build_bundle.py"
V011 = ROOT / "results" / "v0.1.1"
RESULTS = ROOT / "results"
RELEASE = "v0.1.1"
BASED_ON = "v0.1.0"
RELEASE_DATE = "2026-11-01"
PUBLISHED_RUN = "20261030-test"  # the run directory of the pre-registered matrix (r1)
REPEAT_MODELS = ("d3_9b-q8_0", "kev_9b-bf16", "d3_4b-q8_0")
VULKAN_MODELS = ("d3_9b-q8_0",)
SHUFFLE_SEED = 20261101
PROTOCOL = "diagnostic only; the leaderboard headline remains the single pre-registered run"
# tag, run directory under runs/, cell directory pattern, backend, option seed
SOURCES = (
    ("r1", "20261030-test", "{id}", "cuda", None),
    ("r2", "20261101-cuda-repeat", "{id}.r2", "cuda", None),
    ("r3", "20261101-cuda-repeat", "{id}.r3", "cuda", None),
    ("shuffle", "20261101-cuda-shuffle", "{id}.shuffle", "cuda", SHUFFLE_SEED),
    # these ran on Ollama's Vulkan backend by mistake (runs/20261101-repeat/README.txt): a backend-sensitivity diagnostic
    ("vulkan_r2", "20261101-repeat", "{id}.r2", "vulkan", None),
    ("vulkan_r3", "20261101-repeat", "{id}.r3", "vulkan", None),
)
SKIP = {"vulkan_r2": VULKAN_MODELS, "vulkan_r3": VULKAN_MODELS}  # tag -> the only models it covers


def load_v010():
    """results/v0.1.0/build_bundle.py, unchanged, as a module."""
    spec = importlib.util.spec_from_file_location("build_bundle_v010", V010_BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


ENGINE_SHA256 = "2df1bdfbb802612e1a93857c2fb8ee5cfc17fb164c54c55034cb8edc4ed7937c"  # the mb-engine of the published run and every repeat
KNOWN_REPEAT_TAGS = ("r2", "r3", "shuffle")  # the tag parts of the repeat folders (the published run's folder has none)


def tag_part(model: str, name: str) -> str | None:
    """The tag of a run folder name: None for the published run (the name is the model id), else the text after '<id>.'. The id
    may contain dots (tev1_0.8b-q8_0), so the id is taken off the front rather than split at the first dot."""
    if name == model:
        return None
    if not name.startswith(model + "."):
        raise ValueError(f"run folder {name!r} does not start with the model id {model!r}")
    return name[len(model) + 1:]


def last_rc(log: Path, key: str, tagpart: str | None):
    """The exit code of the last attempt of a model (and tag) in a progress log: None when the log has no such attempt, or when a
    later attempt started and did not finish (so an earlier attempt's rc=0 does not count). The lines read are
    '== <key> <tag> start ...' and '== <key> <tag> done ... rc=<n>'; for the published run the tag is absent, so the repeat tags
    are skipped."""
    if not log.is_file():
        return None
    rc = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        words = line.split()
        if len(words) < 2 or words[0] != "==" or words[1] != key:
            continue
        rest = words[2:]
        if tagpart is None:
            if rest and rest[0] in KNOWN_REPEAT_TAGS:
                continue
        else:
            if not rest or rest[0] != tagpart:
                continue
            rest = rest[1:]
        if "start" in rest:
            rc = None  # a new attempt: until it finishes, the last exit code is unknown
        if "done" in rest:
            codes = [w for w in rest if w.startswith("rc=")]
            rc = int(codes[0][3:]) if codes else None
    return rc


def without_floor(flags: list[str]) -> list[str]:
    """The build flags with the relation floor option and its value removed (the floor is checked on its own). A path is compared
    with its separators normalised: the same file recorded with backslashes (Windows) is the same command."""
    out, skip = [], False
    for word in flags:
        if skip:
            skip = False
        elif word == "--relation-floor":
            skip = True
        else:
            out.append(word.replace("\\", "/"))
    return out


def command_mismatches(cell: dict, published: dict, floor: float) -> list[str]:
    """What of a repeat's recorded command differs from the published run's: the engine, the build flags (the floor value aside),
    the profile, top_k and batch size, and the relation floor against the pre-registered one. An empty list is a match."""
    out = []
    if cell["runtime"]["engine"]["sha256"] != ENGINE_SHA256:
        out.append("engine sha256 is not the published run's")
    if without_floor(cell["build_flags"]) != without_floor(published["build_flags"]):
        out.append("build flags differ from the published run's (floor aside)")
    if (cell["profile"].replace("\\", "/") != published["profile"].replace("\\", "/")
            or cell["runtime"].get("profile_sha256") != published["runtime"].get("profile_sha256")):
        out.append("profile differs from the published run's")
    for field in ("top_k", "batch_size"):
        if cell.get(field) != published.get(field):
            out.append(f"{field} {cell.get(field)!r} differs from the published run's {published.get(field)!r}")
    floors = [cell["build_flags"][i + 1] for i, word in enumerate(cell["build_flags"][:-1]) if word == "--relation-floor"]
    if len(floors) != 1 or float(floors[0]) != float(floor):
        out.append(f"relation floor {floors} differs from the pre-registered {floor!r}")
    return out


def run_problem(cdir: Path, rc) -> str | None:
    """Why a repeat run is left out (its folder or one of its records is missing, its cell is not complete, or its last attempt did
    not finish with exit code 0), or None when it can be read."""
    if not cdir.is_dir():
        return "no run directory"
    for rel in ("cell.json", "build_summary.json", "submission/build_policy.json", "submission.build/decisions.jsonl"):
        if not (cdir / rel).is_file():
            return f"missing {rel}"
    if not (cdir.parent / f"{cdir.name}.meta" / "child_stats.json").is_file():
        return f"missing {cdir.name}.meta/child_stats.json"
    cell = json.loads((cdir / "cell.json").read_text(encoding="utf-8"))
    if cell.get("status") != "complete" or cell.get("failed_step"):
        return f"cell not complete (status {cell.get('status')!r})"
    if rc is None:
        return "no exit code for the last attempt in progress.log (it did not finish, or the log has no such attempt)"
    if rc != 0:
        return f"exit code {rc} (progress.log)"
    return None


def collect_repeats(runs_root: Path) -> None:
    """Checks and writes the repeat runs (see the module docstring). A run that cannot be read (its folder or a record missing, its
    cell not complete, or its last attempt not finished with exit code 0) is left out: it is listed in runs.json's left_out with its
    reason. Every run that is read is checked strictly, and any failed check stops the collection with nothing written: the engine,
    the recorded command against the published run's (the relation floor against the pre-registered one), the rescored headline,
    and the predictions. The published run (r1) has no copy here: its records point at results/v0.1.0."""
    bundle = load_v010()
    br = bundle.load_build_results()
    fixture = bundle.TEST_FIXTURE
    version = bundle.decision_scoring.fixture_version(fixture)
    prereg = bundle.jload(bundle.PREREG)
    ollama = bundle.jload(bundle.OLLAMA)
    pre_prompt = prereg["child"].split("prompt_sha256 ")[1].split(",")[0]
    tag_of = {key.replace(":", "_"): key for key in prereg["models"]}  # id -> the prereg's model key (Ollama tag)
    floor_of = {model: float(prereg["models"][key]["relation_floor"]) for model, key in tag_of.items()}
    leaderboard = bundle.jload(RESULTS / "v0.1.0" / "leaderboard.json")
    system_one = [e["id"] for e in leaderboard["models"] if e["kind"] == "system_one_model"]
    bundle.check(sorted(system_one) == sorted(tag_of), "the leaderboard's 15 models and the prereg's model keys differ")
    depths = br.label_depths(fixture)
    cands = {row["qid"]: row for row in bundle.ir.read_jsonl(fixture / "candidates.jsonl")}
    outputs, records, published, left_out = {}, [], {}, []
    for model in system_one:
        key = tag_of[model]
        published_cell = bundle.jload(RESULTS / "v0.1.0" / "cells" / f"{model}.json")["cell"]
        r1_decisions = runs_root / "20261030-test" / model / "submission.build" / "decisions.jsonl"
        if r1_decisions.is_file():
            published[model] = sha256(r1_decisions.read_bytes())
        for tag, run_dir, pattern, backend, seed in SOURCES:
            allowed = system_one if tag == "shuffle" else SKIP.get(tag, REPEAT_MODELS)
            if model not in allowed:
                continue
            name = pattern.format(id=model)
            cdir = runs_root / run_dir / name
            rc = last_rc(runs_root / run_dir / "progress.log", key, tag_part(model, name))
            problem = run_problem(cdir, rc)
            if problem is None and model not in published:
                problem = "the published run's decisions are missing (runs/20261030-test)"
            if problem is not None:
                bundle.check(tag != "r1", f"{model}: the published run is left out ({problem}); nothing is written")
                left_out.append({"id": model, "tag": tag, "run_dir": f"runs/{run_dir}/{name}", "reason": problem})
                continue
            cell = bundle.jload(cdir / "cell.json")
            policy = bundle.jload(cdir / "submission" / "build_policy.json")["model"]
            build = bundle.jload(cdir / "build_summary.json")
            stats = bundle.jload(cdir.parent / f"{cdir.name}.meta" / "child_stats.json")
            bundle.check(cell["runtime"]["engine"]["sha256"] == ENGINE_SHA256, f"{model} {tag}: the engine is not the published run's")
            mismatches = command_mismatches(cell, published_cell, floor_of[model])
            bundle.check(not mismatches, f"{model} {tag}: the recorded command differs from the published run: {'; '.join(mismatches)}")
            bundle.check(policy["revision"] == ollama[key]["digest"], f"{model} {tag}: model digest differs from the pin")
            if seed is None:
                bundle.check(policy["prompt_sha256"] == pre_prompt and "option_order" not in policy,
                             f"{model} {tag}: prompt is not the pre-registered child's")
            else:
                bundle.check(policy.get("option_order") == {"scheme": "crc32-shuffle-seeded-v1", "seed": seed},
                             f"{model} {tag}: option order is not the seed {seed}")
                bundle.check(policy["prompt_sha256"] != pre_prompt, f"{model} {tag}: shuffled prompt equals the pre-registered one")
            rows = bundle.prediction_rows(cdir, fixture, cands, depths)
            bundle.check(len(rows) == 10716, f"{model} {tag}: {len(rows)} prediction rows")
            blob = bundle.encode_predictions(rows)
            bundle.check(bundle.same_scored(bundle.rescore(blob, version), bundle.jload(cdir / "score_decisions.json"), None, br),
                         f"{model} {tag}: the predictions do not rescore to score_decisions.json")
            abstained = sum(r["abstained"] for r in rows if r["op"] == "decide_relation")
            bundle.check(abstained == stats["ops"]["decide_relation"]["abstained_overlength"],
                         f"{model} {tag}: relation abstentions differ from the child stats")
            decisions_sha = sha256((cdir / "submission.build" / "decisions.jsonl").read_bytes())
            if tag == "r1":
                committed = (RESULTS / "v0.1.0" / "predictions" / f"{model}.jsonl.gz").read_bytes()
                bundle.check(blob == committed, f"{model}: the published predictions were not reproduced byte for byte")
                pred_rel = f"results/v0.1.0/predictions/{model}.jsonl.gz"  # no copy: the published run's own files
                cell_rel = f"results/v0.1.0/cells/{model}.json"
            else:
                pred_rel = f"results/v0.1.1/repeats/predictions/{model}.{tag}.jsonl.gz"
                cell_rel = f"results/v0.1.1/repeats/cells/{model}.{tag}.json"
                cell_doc = bundle.cell_doc(f"{model}.{tag}", cdir, "system_one_model", pred_rel)
                cell_text = bundle.dump_json(cell_doc).decode("utf-8")
                for text in (cell_text, gzip.decompress(blob).decode("utf-8")):
                    bundle.check(not bundle.ABSOLUTE.search(text) and not bundle.EMAIL.search(text), f"{model} {tag}: a local path")
                outputs[f"predictions/{model}.{tag}.jsonl.gz"] = blob
                outputs[f"cells/{model}.{tag}.json"] = cell_text.encode("utf-8")
            records.append({
                "id": model, "tag": tag, "backend": backend, "option_seed": seed, "protocol": PROTOCOL,
                "run_dir": f"runs/{run_dir}/{name}", "status": cell["status"], "rc": rc,
                "ollama_version": policy["ollama_version"], "model_digest": policy["revision"],
                "prompt_sha256": policy["prompt_sha256"], "code_commit": cell["runtime"]["repo"]["commit"],
                "engine_sha256": cell["runtime"]["engine"]["sha256"], "decisions_sha256": decisions_sha,
                "wall_seconds_build": build.get("wall_seconds"), "relation_abstentions": abstained,
                "headline_relation_group_mcc": bundle.jload(cdir / "score_decisions.json")["headline"]["relation_group_mcc"],
                "predictions": pred_rel, "cell": cell_rel,
            })
    for record in records:  # the published decisions the repeat's decisions are compared with
        record["published_decisions_sha256"] = published[record["id"]]
        record["identical_to_published_decisions"] = record["decisions_sha256"] == published[record["id"]]
    doc = {"schema": "mentat-bench-repeats/1", "protocol": PROTOCOL, "published_run": PUBLISHED_RUN, "runs": records,
           "left_out": left_out}
    for stale in list((V011 / "repeats" / "predictions").glob("*.r1.jsonl.gz")) + list((V011 / "repeats" / "cells").glob("*.r1.json")):
        stale.unlink()  # the published run's copies are not kept here
    for name, data in outputs.items():
        path = V011 / "repeats" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (V011 / "repeats" / "runs.json").write_bytes(bundle.dump_json(doc))
    for item in left_out:
        print(f"collect-repeats: left out {item['id']} {item['tag']}: {item['reason']} ({item['run_dir']})", file=sys.stderr)
    shuffled = sum(1 for r in records if r["tag"] == "shuffle")
    print(f"wrote {len(records)} repeat runs under {V011 / 'repeats'} ({shuffled} shuffle passes); left out {len(left_out)} runs",
          file=sys.stderr)


# ---------------------------------------------------------------- build (committed files only)

def jload(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_outputs() -> dict[str, bytes]:
    """The three v0.1.1 files, in memory, from committed files: results/v0.1.0/, analysis.json and results/index.json."""
    bundle = load_v010()
    analysis_path = V011 / "analysis.json"
    analysis_bytes = analysis_path.read_bytes()
    analysis = json.loads(analysis_bytes.decode("utf-8"))
    ranks = analysis["rank_intervals"]["headline"]
    lb = jload(RESULTS / "v0.1.0" / "leaderboard.json")
    for entry in lb["models"]:
        interval = ranks[entry["id"]]
        bundle.check(interval["rank"] == entry["rank"], f"{entry['id']}: analysis point rank {interval['rank']} is not {entry['rank']}")
        entry["rank_interval"] = {"lo": interval["ci95"][0], "hi": interval["ci95"][1], "median": interval["median"]}
    lb["release"] = RELEASE
    lb["date"] = RELEASE_DATE
    lb["provenance"]["analysis"] = {"path": "results/v0.1.1/analysis.json", "sha256": sha256(analysis_bytes)}
    lb["provenance"]["based_on_release"] = BASED_ON
    lb["provenance"]["bundle_builder"] = "results/v0.1.1/build_bundle.py"
    resolved = sum(pr["resolved"] for pr in analysis["pairwise"]["headline"])
    lb["resolved_comparisons_note"] = (
        f"{resolved} of 105 headline pairs among the 15 models are resolved after Holm correction (paired league bootstrap, alpha 0.05). "
        "The relation and group pairs, the rank intervals and the weighting schemes are in results/v0.1.1/analysis.json.")
    lb_bytes = bundle.dump_json(lb)

    rows = list(csv.reader(io.StringIO((RESULTS / "v0.1.0" / "leaderboard.csv").read_text(encoding="utf-8"))))
    bounds = {e["id"]: e["rank_interval"] for e in lb["models"]}
    rank_cols = {"rank_lo": "", "rank_hi": ""}
    header = rows[0] + list(rank_cols)
    body = []
    id_column = rows[0].index("id")
    for row in rows[1:]:
        interval = bounds.get(row[id_column])
        body.append(row + ([str(interval["lo"]), str(interval["hi"])] if interval else ["", ""]))
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([header] + body)
    csv_bytes = buf.getvalue().encode("utf-8")

    index = jload(RESULTS / "index.json")
    v010 = next(v for v in index["versions"] if v["version"] == BASED_ON)
    v010["status"] = "superseded"
    v010["superseded_by"] = RELEASE
    v010["note"] = "same measurements; v0.1.1 adds analysis"
    v011 = dict(v010)
    v011.update({
        "version": RELEASE, "status": "latest", "superseded_by": None, "date": RELEASE_DATE, "builder": "results/v0.1.1/build_bundle.py",
        "leaderboard_json": "results/v0.1.1/leaderboard.json", "leaderboard_csv": "results/v0.1.1/leaderboard.csv",
        "analysis_json": "results/v0.1.1/analysis.json", "based_on": BASED_ON,
        "item_audit_flagged": "docs/audits/v0.1.1-item-audit/flagged.jsonl",
        "item_audit_verdicts": "docs/audits/v0.1.1-item-audit/verdicts.json",
    })
    v011.pop("note", None)
    index["latest"] = RELEASE
    index["versions"] = [v011] + [v for v in index["versions"] if v["version"] != RELEASE]
    return {
        "leaderboard.json": lb_bytes,
        "leaderboard.csv": csv_bytes,
        "index.json": bundle.dump_json(index),
    }


def build(check: bool) -> int:
    outputs = build_outputs()
    targets = {"leaderboard.json": V011 / "leaderboard.json", "leaderboard.csv": V011 / "leaderboard.csv",
               "index.json": RESULTS / "index.json"}
    if check:
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
    c = sub.add_parser("collect-repeats", help="check and write the repeat runs from the local run directories")
    c.add_argument("--runs-root", type=Path, default=ROOT / "runs", help="the directory holding the run directories")
    b = sub.add_parser("build", help="write the v0.1.1 leaderboard, CSV and index from committed files")
    b.add_argument("--check", action="store_true", help="compare the bytes with the committed files and exit 1 on a difference")
    a = ap.parse_args(argv)
    try:
        if a.command == "collect-repeats":
            collect_repeats(a.runs_root)
            return 0
        return build(a.check)
    except Exception as exc:  # a failed check (the v0.1.0 builder's CheckError) or a missing file: report it, write nothing more
        print(f"build_bundle v0.1.1: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

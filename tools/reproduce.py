"""Re-runs published cells of the Mentat-Hard test set and compares each run with its published cell.

usage, from the repository root:
  python tools/reproduce.py run <id> [<id> ...] --out-root DIR [--url URL] [--release RELEASE]
  python tools/reproduce.py compare <run_dir> --id <id> [--release RELEASE]

RELEASE is a version in results/index.json (default v0.1.0). A cell is read from the cells directory of the release that holds it:
the release's own cell, or else the cell of the release its record_from entry carries (baseline cells only) or its based_on entry
bases (an analysis release, which may not hold a cell its base holds). That release's cell, predictions and run card are used, and
the report names it as measured_in.

run re-runs each cell with the flags recorded in its cell (fixture_path, adapter, local_arg, profile, build_flags), into a new
<out-root>/<id>, then compares it. A published cell records its child interpreter as a Windows name
(python.exe); the run uses the interpreter that runs this tool. A system_one_model cell runs against the Ollama server at --url
(default http://127.0.0.1:11434) with the model tag of the published run, MB_S1_PARALLEL=2 and a child stats file. Duplicate ids,
a non-empty <out-root>/<id> or <out-root>/<id>.meta, a missing model tag and an unusable child interpreter are refused before any
run starts (exit 2).

compare reads a run directory and writes <run_dir>.reproduce/report.json and predictions.jsonl.gz (the run's predictions, encoded
as the bundle encodes them), then prints one summary line. Verdicts:
  exact             the decompressed predictions are byte-identical to the published ones (the gzip container is reported
                    separately)
  within_tolerance  not identical, and every check of checks_for is within its limit
  differs           a check fails (including any run-defining difference)
  incomplete        the cell, score or build policy is missing or unreadable, the cell is not complete, or the decisions cannot
                    be read
Run-defining facts (build flags, adapter, child arguments, fixture path and files, profile, model digest, prompt sha) must match the
published run: any difference makes the verdict differs and the reason names it. The Ollama version, engine sha, package versions and
GPUs are reported, not checked (the published engine binary was not rebuilt, so the engine sha differs).
compare rewrites <run_dir>.reproduce/ each time it runs; run refuses a non-empty <id>.reproduce as it does <id> and <id>.meta.
Exit code: 0 for exact and within_tolerance, 1 otherwise, 2 for a usage error.

The comparison reuses the publication's own rescoring code (results/v0.1.0/build_bundle.py). The owner chose the limits from the
repeat runs, so this is a consistency check against the published run, not an independent verification.
"""

import argparse
import gzip
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The default is fixed, not the index's "latest": an unflagged run keeps the same comparison target when a release is added.
RELEASE = "v0.1.0"
INDEX = ROOT / "results" / "index.json"
# The per-cell builders of every release: results/v0.2.0/build_bundle.py imports this file unchanged.
BUILDER = ROOT / "results" / "v0.1.0" / "build_bundle.py"
TEST_FIXTURE = ROOT / "fixtures" / "mentat_hard_test"
DEFAULT_URL = "http://127.0.0.1:11434"
SCHEMA = "mentat-bench-reproduce/1"
# Limits, from the committed repeats of kev_9b-bf16 on CUDA (results/v0.1.1/repeats) against the published run: the headline moved
# by 0.000384 at most, the relation by 0.000767, 15 of 10716 decisions (0.14%) changed answer, and one probability moved by 0.0109.
# The q8 models (d3_4b, d3_9b) repeated byte for byte. The owner chose these limits.
METRIC_TOL = 1e-3  # headline, relation and group MCC: absolute difference
READING_TOL = 0.01  # reading MCC over 368 items: two flips moved it by 0.0049 (kev r2), and the per-op cap allows two flips
BINDING_TOL = 0.01  # binding MCC: one flip on a near-zero MCC moves it by about 0.003 (the CUDA repeats had no binding flip)
FLIP_RATE_TOL = 0.005  # share of all decisions that changed answer
PROB_TOL = 0.02  # largest difference of any one probability
PUBLISHED_TOL = 1e-9  # the committed headline against the headline rescored from the published predictions
METRICS = ("headline", "relation", "group", "binding", "reading")
EXACT_OR_TOLERATED = ("exact", "within_tolerance")
# A fact whose difference means the run is not the published run: its verdict is never exact.
RUN_DEFINING = ("adapter", "child_arguments", "build_flags", "fixture_path", "fixture_files", "profile_path", "profile_sha256",
                "model_digest", "prompt_sha256")
PYTHON_CHILD = re.compile(r"python[0-9.]*(\.exe)?")  # the interpreter names the published cells record (python.exe on Windows)


class UsageError(Exception):
    """A bad argument, or a published cell that cannot be re-run or compared: exit 2, nothing is run."""


def load_bundle():
    """results/v0.1.0/build_bundle.py, imported unchanged: its prediction rows, encoding and rescoring are the published ones."""
    spec = importlib.util.spec_from_file_location("build_bundle_v010", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUNDLE = load_bundle()


# ---------------------------------------------------------------- pure comparison (no files)

def _key(row: dict) -> tuple:
    """One scored item: a reading question has two (its base and its alt reading)."""
    return (row["qid"], row["op"], row.get("reading"))


def _same_labels(a: dict, b: dict) -> bool:
    """The same item: the same labels. The answers are not compared here: an answer that differs or is missing is a flip."""
    return (a["gold"], a["stratum"], a["held_out"], a.get("option")) == (b["gold"], b["stratum"], b["held_out"], b.get("option"))


def delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else b - a


def flip_cap(items: int) -> float:
    """The most flips an op may have: the overall rate, but at least two flips, so one flip on a small op is not a failure
    (one flip on the 368 reading items is 0.27%)."""
    return max(FLIP_RATE_TOL, 2 / items)


def _headline(score: dict) -> dict:
    h = score["headline"]
    return {"headline": h["relation_group_mcc"], "relation": h["parts"]["relation_mcc"], "group": h["parts"]["group_mcc"],
            "binding": h["secondary"]["binding_mcc"], "reading": h["diagnostics"]["reading_mcc"]}


def compare_rows(published: list[dict], reproduced: list[dict], version: int | None) -> dict:
    """The comparison of two prediction row lists (the rows of predictions.jsonl.gz), with no file read: the MCCs of each side
    (rescored by decision_scoring), the decision flips overall and per op, the largest probability difference, and whether the
    two item sets carry the same labels. Flips and probabilities are taken over the items both sides have."""
    if not BUNDLE.decision_scoring.relation_group(version):
        raise ValueError("the headline needs a fixture of version 3 or later")
    pub_head = _headline(BUNDLE.rescore(BUNDLE.encode_predictions(published), version))
    rep_head = _headline(BUNDLE.rescore(BUNDLE.encode_predictions(reproduced), version))
    pub = {_key(r): r for r in published}
    rep = {_key(r): r for r in reproduced}
    same_items = (len(pub) == len(published) and len(rep) == len(reproduced) and pub.keys() == rep.keys()
                  and all(_same_labels(pub[k], rep[k]) for k in pub))
    per_op: dict[str, dict] = {}
    max_prob = 0.0
    for key in pub.keys() & rep.keys():
        stat = per_op.setdefault(pub[key]["op"], {"items": 0, "flips": 0})
        stat["items"] += 1
        stat["flips"] += pub[key]["pred"] != rep[key]["pred"]
        pa, pb = pub[key].get("probs"), rep[key].get("probs")
        if pa is not None and pb is not None:  # an answer on one side only is a flip, not a probability difference
            for option in pa.keys() | pb.keys():
                max_prob = max(max_prob, abs(pa.get(option, 0.0) - pb.get(option, 0.0)))
    for stat in per_op.values():
        stat["flip_rate"] = stat["flips"] / stat["items"]
    items = sum(s["items"] for s in per_op.values())
    flips = sum(s["flips"] for s in per_op.values())
    return {
        "same_items": same_items,
        "metrics": {m: {"published": pub_head[m], "reproduced": rep_head[m], "delta": delta(pub_head[m], rep_head[m])}
                    for m in METRICS},
        "decisions": {"items": items, "flips": flips, "flip_rate": flips / items if items else None, "per_op": per_op},
        "max_prob_diff": max_prob,
    }


def checks_for(comparison: dict, published_headline: float | None) -> dict[str, dict]:
    """Every check, by name, as {value, limit, ok}. A missing value fails. published_headline is the committed headline, checked
    against the one rescored from the published predictions."""
    checks = {"same_items": {"value": comparison["same_items"], "limit": True, "ok": comparison["same_items"] is True}}
    for name in METRICS:
        change = comparison["metrics"][name]["delta"]
        limit = {"reading": READING_TOL, "binding": BINDING_TOL}.get(name, METRIC_TOL)
        checks[name] = {"value": change, "limit": limit, "ok": change is not None and abs(change) <= limit}
    rate = comparison["decisions"]["flip_rate"]
    checks["flip_rate"] = {"value": rate, "limit": FLIP_RATE_TOL, "ok": rate is not None and rate <= FLIP_RATE_TOL}
    for op, stat in comparison["decisions"]["per_op"].items():
        limit = flip_cap(stat["items"])
        checks[f"flip_rate:{op}"] = {"value": stat["flip_rate"], "limit": limit, "ok": stat["flip_rate"] <= limit}
    checks["max_prob_diff"] = {"value": comparison["max_prob_diff"], "limit": PROB_TOL,
                               "ok": comparison["max_prob_diff"] <= PROB_TOL}
    rescored = comparison["metrics"]["headline"]["published"]
    gap = None if published_headline is None or rescored is None else abs(published_headline - rescored)
    checks["published_headline"] = {"value": gap, "limit": PUBLISHED_TOL, "ok": gap is not None and gap <= PUBLISHED_TOL}
    return checks


def run_defining_checks(differences: list[str]) -> dict[str, dict]:
    """A failing check for each run-defining fact that differs from the published run (such a run is never exact)."""
    return {f"run_defining:{key}": {"value": "differs", "limit": "same as the published run", "ok": False}
            for key in differences if key in RUN_DEFINING}


def verdict_for(checks: dict[str, dict], bytes_equal: bool) -> tuple[str, list[str]]:
    """(verdict, reasons) of a complete run. A failing check is differs, and the reasons name each one. Otherwise exact when the
    decompressed predictions are identical, else within_tolerance, whose reasons name every check that passed."""
    failed = [name for name, c in checks.items() if not c["ok"]]
    if failed:
        return "differs", [f"{name}: value {checks[name]['value']!r}, limit {checks[name]['limit']!r}" for name in failed]
    if bytes_equal:
        return "exact", []
    return "within_tolerance", ["decompressed predictions differ; every check is within its limit: " + ", ".join(checks)]


def slashes(words) -> list | None:
    """The words with backslashes as forward slashes: runcell records a Windows path as backslashes, the bundle scrubs it."""
    if not isinstance(words, list):
        return None
    return [str(w).replace("\\", "/") for w in words]


def _obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def forward(text) -> str | None:
    """A recorded path with forward slashes (runcell records a Windows path with backslashes)."""
    return None if text is None else str(text).replace("\\", "/")


def facts(cell: dict, policy: dict) -> dict:
    """The facts two runs are compared on, from a cell record and a build policy (a published run's policy is its run card's).
    The child interpreter is not one of them (see interpreter_name); GPU names and drivers are compared, not UUIDs."""
    runtime, model = _obj(cell.get("runtime")), _obj(policy.get("model"))
    gpus = runtime.get("gpus") if isinstance(runtime.get("gpus"), list) else []
    local = cell.get("local_arg")
    return {
        "adapter": cell.get("adapter"), "child_arguments": slashes(local[1:]) if isinstance(local, list) else None,
        "fixture_path": forward(cell.get("fixture_path")), "profile_path": forward(cell.get("profile")),
        "build_flags": slashes(cell.get("build_flags")),
        "ollama_version": model.get("ollama_version"), "model_digest": model.get("revision"),
        "prompt_sha256": model.get("prompt_sha256"), "engine_sha256": _obj(runtime.get("engine")).get("sha256"),
        "profile_sha256": runtime.get("profile_sha256"), "fixture_files": _obj(runtime.get("fixture")).get("files"),
        "gpus": [[_obj(g).get("name"), _obj(g).get("driver")] for g in gpus],
        "cuda": runtime.get("cuda"), "versions": runtime.get("versions"),
    }


def compare_environment(published: dict, reproduced: dict) -> dict:
    """Each fact of the published run against the reproduced run's. A fact whose values differ is listed in differences: the two
    runs are not like for like on it. Package versions are compared only where both sides record the package; a package recorded
    on one side only is listed in one_sided (the published runcards record packages the current runner does not capture)."""
    compared, differences, one_sided = {}, [], {}
    for key in published:
        p, r = published[key], reproduced[key]
        if key == "versions" and isinstance(p, dict) and isinstance(r, dict):
            one_sided[key] = {"published_only": sorted(p.keys() - r.keys()), "reproduced_only": sorted(r.keys() - p.keys())}
            same = all(p[name] == r[name] for name in p.keys() & r.keys())
        else:
            same = p == r
        compared[key] = {"published": p, "reproduced": r}
        if not same:
            differences.append(key)
    return {"compared": compared, "differences": differences, "one_sided": one_sided}


def interpreter_name(local_arg: list | None) -> str | None:
    """The file name of a cell's child interpreter (no directory, so no local path enters a report)."""
    if not local_arg:
        return None
    return local_arg[0].replace("\\", "/").rsplit("/", 1)[-1]


def child_interpreter(recorded: str) -> str:
    """The interpreter a local child runs under: this tool's own when the recorded name is a Python interpreter (the published
    cells record python.exe), else the recorded command."""
    return sys.executable if PYTHON_CHILD.fullmatch(interpreter_name([recorded]).lower()) else recorded


def resolve_interpreter(recorded: str) -> str:
    """The child interpreter a run will use. UsageError when it cannot be found, before anything runs."""
    path = child_interpreter(recorded)
    if shutil.which(path) is None and not Path(path).is_file():
        raise UsageError(f"the child interpreter {recorded!r} cannot be found; put it on PATH or give its full path")
    return path


def runcell_argv(cell: dict, out: Path) -> list[str]:
    """The runcell command of a published cell, from its recorded fixture, adapter, local child, profile and build flags. The
    local argv is passed as --local-arg=VALUE, which takes dash-leading values; build_flags carry the candidates file."""
    local = [child_interpreter(cell["local_arg"][0]), *cell["local_arg"][1:]] if cell["local_arg"] else []
    return [sys.executable, "-m", "mentat_bench.runcell", "--fixture", cell["fixture_path"], "--adapter", cell["adapter"],
            *[f"--local-arg={arg}" for arg in local], "--profile", cell["profile"], "--out", str(out), *cell["build_flags"]]


def run_env(model: str | None, url: str, stats: Path) -> dict:
    """The environment of a run: the inherited one, without any MB_S1_ setting, plus the published run's Ollama settings when the
    cell is a model (model is its tag)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MB_S1_")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "python"), os.environ.get("PYTHONPATH")]))
    if model is not None:
        env.update(MB_S1_MODEL=model, MB_S1_URL=url, MB_S1_PARALLEL="2", MB_S1_STATS=str(stats))
    return env


# ---------------------------------------------------------------- files

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_record(path: Path, label: str, reasons: list[str], run_dir: Path) -> dict | None:
    """A JSON object of a run directory. None, with the reason appended, when it is missing, unreadable or not an object."""
    if not path.is_file():
        reasons.append(f"{label} is missing")
        return None
    try:
        value = read_json(path)
    except (OSError, ValueError) as exc:
        reasons.append(f"{label} is not readable: {scrub(str(exc), run_dir)}")
        return None
    if not isinstance(value, dict):
        reasons.append(f"{label} is not a JSON object")
        return None
    return value


def decode(blob: bytes) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(blob).decode("utf-8").splitlines()]


def load_index() -> dict[str, dict]:
    """The releases of results/index.json, by version."""
    return {entry["version"]: entry for entry in read_json(INDEX)["versions"]}


def own_cell(index: dict, release: str, cell_id: str) -> Path | None:
    """The file of a cell the release holds itself, in its cells_dir (default results/<release>/cells), or None. An analysis release
    whose cells_dir is its base's holds no cells of its own."""
    entry = index[release]
    cells_dir = entry.get("cells_dir", f"results/{release}/cells")
    base = entry.get("based_on")
    if base is not None and index.get(base, {}).get("cells_dir", f"results/{base}/cells") == cells_dir:
        return None
    path = ROOT / cells_dir / f"{cell_id}.json"
    return path if path.is_file() else None


def resolve(index: dict, release: str, cell_id: str) -> tuple[str | None, list[str]]:
    """(the release that holds the cell, or None; the releases followed). The release's own cell comes first. Otherwise record_from
    (carried baselines) or based_on (analysis releases) is followed, one link at a time. A record_from link carries only baseline
    cells: a model cell found through one is not carried."""
    chain, current, through_record_from = [], release, False
    while True:
        if current not in index:
            raise UsageError(f"no release {current!r} in results/index.json")
        chain.append(current)
        path = own_cell(index, current, cell_id)
        if path is not None:
            carried_model = through_record_from and read_json(path).get("kind") == "system_one_model"
            return (None if carried_model else current), chain
        entry = index[current]
        if entry.get("record_from"):
            current, through_record_from = entry["record_from"], True
        elif entry.get("based_on"):
            current = entry["based_on"]
        else:
            return None, chain
        if current in chain:
            return None, chain


def holding_release(release: str, cell_id: str) -> str:
    """The release whose cells hold the cell (see resolve). UsageError when the release is unknown, when the chain holds no such cell,
    or when an analysis release holds a cell that its base also holds (an analysis release changes no prediction)."""
    index = load_index()
    holder, chain = resolve(index, release, cell_id)
    if holder is None:
        raise UsageError(f"no published cell {cell_id!r} in results/{release}/cells, nor in the releases it carries or bases on "
                         f"({' -> '.join(chain)})")
    base = index[release].get("based_on")
    if holder == release and base is not None and resolve(index, base, cell_id)[0] is not None:
        raise UsageError(f"{release} is an analysis release based on {base}, which holds {cell_id!r}; an analysis release may not "
                         "hold a cell that its base holds")
    return holder


def cell_file(release: str, cell_id: str) -> Path:
    """The published cell file, from the release that holds it."""
    return own_cell(load_index(), holding_release(release, cell_id), cell_id)


def runcard(release: str, cell_id: str) -> dict:
    """The run card of a published cell, from the release that holds it."""
    return read_json(ROOT / load_index()[holding_release(release, cell_id)]["runcards_dir"] / f"{cell_id}.json")


def published_doc(cell_id: str, release: str) -> dict:
    doc = read_json(cell_file(release, cell_id))
    if doc["predictions"] is None:
        raise UsageError(f"{cell_id} has no published predictions (it has no decision log), so there is nothing to compare")
    return doc


def model_tag(cell_id: str, doc: dict) -> str | None:
    """The Ollama tag of a system_one_model cell (recorded in its child stats); None for the other cells."""
    if doc["kind"] != "system_one_model":
        return None
    tag = _obj(doc.get("child_stats")).get("model")
    if not tag:
        raise UsageError(f"{cell_id}: the published run records no model tag")
    return tag


def is_fresh(path: Path) -> bool:
    return not path.exists() or (path.is_dir() and not any(path.iterdir()))


def run_rows(run_dir: Path) -> list[dict]:
    """The prediction rows of a run's decisions, built as the bundle builds the published ones."""
    decisions = run_dir / "submission.build" / "decisions.jsonl"
    if decisions.is_file():
        for number, line in enumerate(decisions.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue  # a malformed line is named by decision_scoring
            if not isinstance(value, dict):
                raise ValueError(f"decisions.jsonl line {number} is not a JSON object")
    cands = {row["qid"]: row for row in BUNDLE.ir.read_jsonl(TEST_FIXTURE / "candidates.jsonl")}
    depths = BUNDLE.load_build_results().label_depths(TEST_FIXTURE)
    return BUNDLE.prediction_rows(run_dir, TEST_FIXTURE, cands, depths)


def scrub(text: str, run_dir: Path) -> str:
    """The message with the run directory and the repository root replaced by names (no local path enters a report)."""
    for path, label in ((run_dir, run_dir.name), (ROOT, "<repo>")):
        for form in {str(path), path.as_posix()}:
            text = text.replace(form, label)
    return text


def incomplete_reason(run_dir: Path, cell: dict) -> str | None:
    if cell.get("status") != "complete" or cell.get("incomplete") or cell.get("failed_step"):
        return f"cell not complete (status {cell.get('status')!r}, failed step {cell.get('failed_step')!r})"
    if not (run_dir / "score_decisions.json").is_file():
        return "score_decisions.json is missing (the decisions were not scored)"
    return None


def compare_run(run_dir: Path, cell_id: str, release: str = RELEASE) -> tuple[dict, Path]:
    """Compares a run directory with its published cell, writes <run_dir>.reproduce/ and returns (report, report path)."""
    doc = published_doc(cell_id, release)
    pub_blob = (ROOT / doc["predictions"]).read_bytes()
    card = runcard(release, cell_id)
    holder = holding_release(release, cell_id)
    reasons: list[str] = []
    cell_record = read_record(run_dir / "cell.json", "cell.json", reasons, run_dir)
    policy_record = read_record(run_dir / "submission" / "build_policy.json", "submission/build_policy.json", reasons, run_dir)
    score_record = read_record(run_dir / "score.json", "score.json", reasons, run_dir)
    cell, policy, score = cell_record or {}, policy_record or {}, score_record or {}
    if cell_record is not None and (problem := incomplete_reason(run_dir, cell)):
        reasons.append(problem)
    rows = None
    try:
        rows = run_rows(run_dir)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, BUNDLE.CheckError) as exc:
        reasons.append(f"the run's decisions cannot be read: {scrub(str(exc), run_dir)}")
    environment = compare_environment(facts(doc["cell"], _obj(card.get("build_policy"))), facts(cell, policy))
    environment["child_interpreter"] = {"published": interpreter_name(doc["cell"]["local_arg"]),
                                        "reproduced": interpreter_name(cell.get("local_arg"))}
    report = {
        "schema": SCHEMA, "id": cell_id, "release": release, "measured_in": holder, "run": run_dir.name, "verdict": "incomplete",
        "reasons": reasons, "builder": load_index()[holder].get("builder"), "rescoring_builder": BUILDER.relative_to(ROOT).as_posix(),
        "cell_status": cell.get("status"), "failed_step": cell.get("failed_step"), "predictions_bytes_equal": None,
        "predictions_gzip_equal": None, "same_items": None, "metrics": None, "decisions": None, "max_prob_diff": None,
        "checks": {}, "environment": environment,
        "q_n": {"published": _obj(doc.get("score")).get("Q_N"), "reproduced": score.get("Q_N"), "decisive": False,
                "note": "reported only: the engine binary differs from the published one (see environment)"},
        "tolerance": {"metric_abs": METRIC_TOL, "reading": READING_TOL, "binding": BINDING_TOL, "flip_rate": FLIP_RATE_TOL,
                      "flip_rate_per_op": "max(flip_rate, 2 / items)", "max_prob_diff": PROB_TOL,
                      "published_headline": PUBLISHED_TOL},
    }
    report["q_n"]["delta"] = delta(report["q_n"]["published"], report["q_n"]["reproduced"])
    out = run_dir.parent / f"{run_dir.name}.reproduce"
    out.mkdir(parents=True, exist_ok=True)
    if rows is None:
        (out / "predictions.jsonl.gz").unlink(missing_ok=True)  # an earlier compare's file must not stand for this run
    if rows is not None:
        blob = BUNDLE.encode_predictions(rows)
        (out / "predictions.jsonl.gz").write_bytes(blob)
        comparison = compare_rows(decode(pub_blob), rows, BUNDLE.decision_scoring.fixture_version(TEST_FIXTURE))
        committed = _obj(_obj(doc.get("score_decisions")).get("headline")).get("relation_group_mcc")
        checks = checks_for(comparison, committed) | run_defining_checks(environment["differences"])
        bytes_equal = gzip.decompress(blob) == gzip.decompress(pub_blob)
        report.update(comparison, predictions_bytes_equal=bytes_equal, predictions_gzip_equal=blob == pub_blob, checks=checks)
        if not reasons:
            report["verdict"], report["reasons"] = verdict_for(checks, bytes_equal)
    path = out / "report.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return report, path


def summary_line(report: dict, path: Path) -> str:
    head = f"{report['id']}: {report['verdict']}"
    if report["measured_in"] != report["release"]:
        head += f" (measured in {report['measured_in']})"
    if report["metrics"] is None:
        return f"{head} ({'; '.join(report['reasons'])}) | report {path}"
    h, d = report["metrics"]["headline"], report["decisions"]
    diffs = report["environment"]["differences"]
    text = (f"{head}: headline {h['published']:.4f} -> {h['reproduced']:.4f} (delta {h['delta']:+.4f}), flips {d['flips']} of "
            f"{d['items']} ({d['flip_rate']:.2%}), max prob diff {report['max_prob_diff']:.4f}, environment differs in "
            f"{len(diffs)}: {', '.join(diffs) or 'none'}")
    if report["reasons"]:
        text += f" [{'; '.join(report['reasons'])}]"
    return f"{text} | report {path}"


# ---------------------------------------------------------------- run

def run_cells(ids: list[str], out_root: Path, url: str, release: str) -> int:
    if len(set(ids)) != len(ids):
        raise UsageError(f"a cell id is given more than once: {', '.join(ids)}")
    docs = {cid: published_doc(cid, release) for cid in ids}
    tags = {cid: model_tag(cid, doc) for cid, doc in docs.items()}  # every refusal is checked before the first run starts
    for cid, doc in docs.items():
        if doc["cell"]["local_arg"]:
            resolve_interpreter(doc["cell"]["local_arg"][0])
    if out_root.exists() and not out_root.is_dir():
        raise UsageError(f"{out_root} is not a directory")
    for cid in ids:
        for path in (out_root / cid, out_root / f"{cid}.meta", out_root / f"{cid}.reproduce"):
            if not is_fresh(path):
                raise UsageError(f"{path} exists and is not empty; each cell needs a new run directory")
    out_root.mkdir(parents=True, exist_ok=True)
    ok = True
    for cid in ids:
        doc, run_dir = docs[cid], out_root / cid
        stats = out_root / f"{cid}.meta" / "child_stats.json"
        stats.parent.mkdir(parents=True, exist_ok=True)
        argv = runcell_argv(doc["cell"], run_dir)
        print(f"reproduce: running {cid}: {subprocess.list2cmdline(argv)}", file=sys.stderr)
        code = subprocess.run(argv, cwd=ROOT, env=run_env(tags[cid], url, stats)).returncode
        if not run_dir.is_dir():
            print(f"{cid}: runcell exited {code} before creating its run directory", file=sys.stderr)
            ok = False
            continue
        report, path = compare_run(run_dir, cid, release)
        print(summary_line(report, path))
        ok = ok and code == 0 and report["verdict"] in EXACT_OR_TOLERATED
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="re-run published cells into new run directories, then compare them")
    r.add_argument("ids", nargs="+", metavar="id", help="published cell ids, e.g. lexical d3_27b-q8_0")
    r.add_argument("--out-root", required=True, type=Path, help="directory that receives <id>/ for each cell")
    r.add_argument("--url", default=DEFAULT_URL, help="Ollama server for model cells (default %(default)s)")
    r.add_argument("--release", default=RELEASE, help="a release in results/index.json (default %(default)s)")
    c = sub.add_parser("compare", help="compare an existing run directory with its published cell")
    c.add_argument("run_dir", type=Path, help="a run directory written by runcell")
    c.add_argument("--id", required=True, help="the published cell id")
    c.add_argument("--release", default=RELEASE, help="a release in results/index.json (default %(default)s)")
    a = ap.parse_args(argv)
    try:
        if a.command == "run":
            return run_cells(a.ids, a.out_root.resolve(), a.url, a.release)
        run_dir = a.run_dir.resolve()
        if not run_dir.is_dir():
            raise UsageError(f"{run_dir} is not a directory")
        report, path = compare_run(run_dir, a.id, a.release)
        print(summary_line(report, path))
        return 0 if report["verdict"] in EXACT_OR_TOLERATED else 1
    except UsageError as exc:
        print(f"reproduce: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

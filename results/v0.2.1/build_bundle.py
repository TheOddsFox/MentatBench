"""Builds the v0.2.1 analysis release, based on v0.2.0: the same measurements, no score changed, analysis added.

usage, from the repository root:
  python results/v0.2.1/build_bundle.py build [--check]

build [--check] reads committed files only: results/v0.2.0/ (leaderboard.json, leaderboard.csv, analysis.json, predictions/), the
fixture fixtures/mentat_hard_test, the v0.1.1 item audit's verdicts, the reasoning reference's results.json and results/index.json.
It writes:
  leaderboard.json, leaderboard.csv  every v0.2.0 row unchanged; the release, date and provenance rewritten (based_on_release v0.2.0)
  analysis.json                      v0.2.0's analysis with its release field re-pointed to v0.2.1; nothing recomputed
  profiles.json                      per model: relation and group accuracy, missing answers and the failure examples (see DEFINITIONS)
  reference.json                     the unranked reasoning reference, from its scored results.json
  index.json                         only while v0.2.0 or v0.2.1 is the index's latest release; a later release's builder owns it then
README.md is written by hand.

The reference needs its scored results (three complete instances). Until they exist, build writes the other files, does not write
reference.json, prints "reference results not scored yet" and exits 1. With --check it compares the bytes with the committed files
and exits 1 on any difference (or on the missing reference).
"""

import argparse
import collections
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools" / "eval"))

import league_ci as LC  # noqa: E402  read_fixture: the league of every item, as the v0.2.0 intervals assign it

RESULTS = ROOT / "results"
V021 = RESULTS / "v0.2.1"
V020 = RESULTS / "v0.2.0"
RELEASE, BASED_ON = "v0.2.1", "v0.2.0"
RELEASE_DATE = "2026-11-03"  # set by the owner
FIXTURE = ROOT / "fixtures" / "mentat_hard_test"
VERDICTS = ROOT / "docs" / "audits" / "v0.1.1-item-audit" / "verdicts.json"
REFERENCE_RESULTS = ROOT / "docs" / "audits" / "v0.2.0-reasoning-reference" / "results.json"
REFERENCE_NOTE = "docs/audits/v0.2.0-reasoning-reference/notes.md"
PROFILES_SCHEMA = "mentat-bench-profiles/1"
REFERENCE_SCHEMA = "mentat-bench-reference/1"
SCORED_SCHEMA = "mentat-bench-reasoning-reference/1"  # the scorer's results.json (docs/audits/v0.2.0-reasoning-reference/score.py)
REFERENCE_ID = "haiku-5-5-reasoning"
RELATION_N, GROUP_N, SAMPLE_ITEMS, SAMPLE_RELATIONS, SAMPLE_GROUPS, LEAGUES = 6000, 1000, 500, 300, 200, 18
SET_SIZE_BUCKETS = ["2", "3", "4-8"]
GROUP_KINDS = ["none", "at_most_one", "exactly_one", "at_most_k", "at_least_k", "at_least_k+at_most_k", "ordered_chain"]
SUSPECT = ("suspect_label", "unclear")  # the v0.1.1 audit verdicts whose items the examples leave out
NOTE_SUPERSEDED = "same measurements; v0.2.1 adds analysis, profiles and an unranked reference"
DEFINITIONS = {
    "relation_accuracy": "share of the 6,000 relation items whose predicted relation equals the gold relation",
    "group_accuracy": "share of the 1,000 curated group items whose predicted class equals the gold class exactly (kind, k and "
                      "chain); readings excluded",
    "set_size": "number of member outcomes in a group item",
    "missing": "relation: abstained items, scored as their recorded answer as in the published scores (an abstention is recorded as "
               "NONE); group: items with no answer, counted wrong (group abstentions are not recorded per item)",
    "examples": "the modal-confusion rule: for each op, the model's most frequent wrong (gold, predicted) cell, ties by class name; "
                "within it, the item the most other models answered correctly, ties by lowest qid; abstentions and items the v0.1.1 "
                "audit judged suspect_label or unclear are excluded",
}
CONDITIONS = [
    "The test-taker may reason before it answers. The System One runs are one pass per request with no generated reasoning.",
    "It is a hosted model (Claude Haiku 5.5). The test set, its gold and this sample's key are public, and the model's training "
    "data is not known, so memorisation of these items cannot be ruled out.",
    "Each instance sees about 50 items in one context, about 2.8 per league, and the items of one league share one rule card. "
    "Consistency across items is possible here and not in the System One runs.",
    "Groups are asked all at once: the kind, the bounds and the chain in one reply, in a different answer format from the System "
    "One kind-first flow.",
    "The prompts carry the [label: Yes] and [label: No] tags of each market proposition's side, as the System One prompts do.",
    "The three instances are three sessions of one model with the same prompt and no temperature or seed control. They are not "
    "three models.",
    "The sample is 500 of the 7,000 test items (300 relations and 200 groups), weighted to the full test set. The intervals are "
    "conditional on these 500 items.",
    "The value is the mean of three sessions. Session 3 scored 0.925: on one slice it set aside the reading convention for the "
    "market labels, and that slice holds 23 of its 24 relation errors. The slice was valid under the protocol and is scored as "
    "answered.",
    "The reference is unranked, is not on the leaderboard, and changes no score.",
]
ABSOLUTE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|(?<![\w.])/(?:c|home|tmp|var|Users)/")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.\w+")


class CheckError(Exception):
    pass


class NotScored(CheckError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jload(path: Path):
    return json.loads(path.read_bytes().decode("utf-8"))


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dump_sorted(obj) -> bytes:
    """The release files the v0.1.x and v0.2.0 builders write (keys sorted, as the committed files are)."""
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def dump_ordered(obj) -> bytes:
    """profiles.json and reference.json: the keys in the order the contract shows them."""
    return (json.dumps(obj, indent=1, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def scrubbed(data: bytes) -> None:
    text = data.decode("utf-8")
    check(not ABSOLUTE.search(text) and not EMAIL.search(text), "a local path or an address in a published file")


def has_key(obj, key: str) -> bool:
    if isinstance(obj, dict):
        return key in obj or any(has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key(v, key) for v in obj)
    return False


# ---------------------------------------------------------------- the analysis and the leaderboard (copied from v0.2.0)

def analysis_bytes(v020: bytes) -> bytes:
    """v0.2.0's analysis, its release field re-pointed to v0.2.1. Its based_on (the v0.2.0 leaderboard, the v0.1.0 baselines'
    predictions and the v0.2.0 models' predictions) stays as it is."""
    analysis = json.loads(v020.decode("utf-8"))
    check(analysis["release"] == BASED_ON and analysis["based_on"]["release"] == BASED_ON,
          "the v0.2.0 analysis does not name v0.2.0 as its release and based_on release")
    analysis["release"] = RELEASE
    return dump_sorted(analysis)


def leaderboard_bytes(v020: bytes, analysis_sha: str) -> bytes:
    """v0.2.0's leaderboard with the release, date and provenance of an analysis release (based_on only: no record_from). The
    rows (models, baselines), the ranking and the comparisons are v0.2.0's, byte for byte once serialised."""
    lb = json.loads(v020.decode("utf-8"))
    check(lb["release"] == BASED_ON, "the v0.2.0 leaderboard does not name v0.2.0")
    lb["release"] = RELEASE
    lb["date"] = RELEASE_DATE
    prov = lb["provenance"]
    prov.pop("record_from")
    prov["analysis"] = {"path": f"results/{RELEASE}/analysis.json", "sha256": analysis_sha}
    prov["based_on_release"] = BASED_ON
    prov["bundle_builder"] = f"results/{RELEASE}/build_bundle.py"
    return dump_sorted(lb)


# ---------------------------------------------------------------- profiles.json

def kind(cls: str) -> str:
    """The class without its k values: at_least_k:2+at_most_k:3 -> at_least_k+at_most_k; none stays none."""
    return "+".join(term.split(":")[0] for term in cls.split("+"))


def set_size_bucket(n: int) -> str:
    if n == 2:
        return "2"
    if n == 3:
        return "3"
    check(4 <= n <= 8, f"a group of {n} members is outside the set-size buckets")
    return "4-8"


def missing(row: dict) -> bool:
    """An abstained relation (informational: it is scored as its recorded NONE answer), or a group with no answer (pred __missing__
    or null). Group abstentions are not recorded per item in the predictions."""
    return bool(row.get("abstained")) or row["pred"] in (None, "__missing__")


def is_correct(row: dict) -> bool:
    """Scored as the published scores score it: an abstained relation is its recorded answer (NONE), so it is correct when its
    gold is NONE. A group with no answer has pred None or __missing__, which is never its gold."""
    return row["pred"] == row["gold"]


def stat(correct: int, n: int) -> dict:
    return {"accuracy": round(correct / n, 6) if n else None, "correct": correct, "n": n}


def choose_example(wrong: dict[str, tuple[str, str]], others: dict[str, int]):
    """wrong: qid -> (gold, pred) of a model's eligible wrong answers; others: qid -> how many other models answered it correctly.
    The modal (gold, pred) cell (most items; ties by gold, then pred), then within it the qid with the most others (ties by the
    lowest qid). Returns (cell, cell_count, qid), or None when there is no wrong answer."""
    if not wrong:
        return None
    counts = collections.Counter(wrong.values())
    cell = min(counts, key=lambda c: (-counts[c], c))
    qid = min((q for q, c in wrong.items() if c == cell), key=lambda q: (-others[q], q))
    return cell, counts[cell], qid


def read_predictions(model: str) -> list[dict]:
    path = V020 / "predictions" / f"{model}.jsonl.gz"
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode("utf-8").splitlines() if line.strip()]


def profiles_bytes() -> bytes:
    fx = LC.read_fixture(FIXTURE)
    league = {qid: fx["leagues"][i] for qid, i in fx["item_league"].items()}  # league_ci's assignment, by league id
    cands = {row["qid"]: row for row in read_rows(FIXTURE / "candidates.jsonl")}
    corpus = {row["id"]: row for row in read_rows(FIXTURE / "corpus.jsonl")}
    meta = {row["id"]: row["league"] for row in read_rows(FIXTURE / "gold" / "meta.jsonl")}
    names = {row["id"]: row["name"] for row in jload(FIXTURE / "gold" / "world.json")["leagues"]}
    cards = collections.defaultdict(set)
    for oid, row in corpus.items():
        cards[meta[oid]].add(row["context"])
    check(all(len(cards[lid]) == 1 for lid in fx["leagues"]), "a league has more or fewer than one rule card")
    leagues = {lid: {"name": names[lid], "card": next(iter(cards[lid]))} for lid in fx["leagues"]}

    rel_qids = sorted(q for q, c in cands.items() if c["op"] == "decide_relation")
    grp_qids = sorted(q for q, c in cands.items() if c["op"] == "decide_group" and c.get("source") == "curated")
    check(len(rel_qids) == RELATION_N and len(grp_qids) == GROUP_N, "the candidates do not hold 6,000 relations and 1,000 groups")
    suspect = {v["qid"] for v in jload(VERDICTS)["items"] if v["verdict"] in SUSPECT}

    lb = jload(V020 / "leaderboard.json")
    models = [e["id"] for e in sorted(lb["models"], key=lambda e: e["rank"])]
    check(len(models) == 17, f"the v0.2.0 leaderboard has {len(models)} models, not 17")
    rows = {}  # model -> op -> qid -> row
    for m in models:
        rows[m] = {"decide_relation": {}, "decide_group": {}}
        for row in read_predictions(m):
            if row["op"] in rows[m]:
                rows[m][row["op"]][row["qid"]] = row
        check(set(rows[m]["decide_relation"]) == set(rel_qids) and set(rows[m]["decide_group"]) == set(grp_qids),
              f"{m}: its predictions do not cover the candidates' relations and curated groups")
    gold = {}  # the gold of each item, the same in every model's predictions
    for m in models:
        for op, qids in (("decide_relation", rel_qids), ("decide_group", grp_qids)):
            for q in qids:
                check(gold.setdefault(q, rows[m][op][q]["gold"]) == rows[m][op][q]["gold"], f"{q}: the models disagree on its gold")
    for q in grp_qids:
        check(all(rows[m]["decide_group"][q]["members"] == cands[q]["members"] for m in models), f"{q}: members differ from candidates")

    def outcomes(op: str, q: str) -> list[dict]:
        ids = [cands[q]["a"], cands[q]["b"]] if op == "decide_relation" else cands[q]["members"]
        return [{"text": corpus[i]["text"], "label": corpus[i]["label"]} for i in ids]

    def example(m: str, op: str, qids: list[str]):
        wrong = {q: (gold[q], rows[m][op][q]["pred"]) for q in qids
                 if not missing(rows[m][op][q]) and not is_correct(rows[m][op][q]) and q not in suspect}
        others = {q: sum(is_correct(rows[o][op][q]) for o in models if o != m) for q in wrong}
        pick = choose_example(wrong, others)
        if pick is None:
            return None
        (g, p), count, q = pick
        return {"op": op, "qid": q, "league": league[q], "gold": g, "pred": p, "cell_count": count,
                "others_correct": others[q], "outcomes": outcomes(op, q)}

    out_models = {}
    for m in models:
        rel, grp = rows[m]["decide_relation"], rows[m]["decide_group"]
        rel_correct = sum(is_correct(rel[q]) for q in rel_qids)
        grp_correct = sum(is_correct(grp[q]) for q in grp_qids)
        size = {b: [0, 0] for b in SET_SIZE_BUCKETS}  # bucket -> [correct, n], the order stat() takes
        kinds = {k: [0, 0] for k in GROUP_KINDS}
        for q in grp_qids:
            bucket = set_size_bucket(len(cands[q]["members"]))
            size[bucket][0] += is_correct(grp[q])
            size[bucket][1] += 1
            k = kind(gold[q])
            check(k in kinds, f"{q}: gold kind {k!r} is not in group_kinds")
            kinds[k][0] += is_correct(grp[q])
            kinds[k][1] += 1
        check(sum(v[1] for v in size.values()) == GROUP_N and sum(v[1] for v in kinds.values()) == GROUP_N,
              "the group partitions do not sum to 1,000")
        check(sum(v[0] for v in size.values()) == grp_correct and sum(v[0] for v in kinds.values()) == grp_correct,
              "the group partitions do not sum to the group correct count")
        group = {"accuracy": round(grp_correct / GROUP_N, 6), "correct": grp_correct, "n": GROUP_N,
                 "by_set_size": {b: stat(*size[b]) for b in SET_SIZE_BUCKETS},
                 "by_kind": {k: stat(*kinds[k]) for k in GROUP_KINDS}}
        examples = [e for e in (example(m, "decide_relation", rel_qids), example(m, "decide_group", grp_qids)) if e]
        out_models[m] = {
            "relation": {"accuracy": round(rel_correct / RELATION_N, 6), "correct": rel_correct, "n": RELATION_N},
            "group": group,
            "missing": {"relation": sum(missing(rel[q]) for q in rel_qids), "group": sum(missing(grp[q]) for q in grp_qids)},
            "examples": examples,
        }
    doc = {"schema": PROFILES_SCHEMA, "release": RELEASE, "based_on": BASED_ON, "definitions": DEFINITIONS,
           "set_size_buckets": SET_SIZE_BUCKETS, "group_kinds": GROUP_KINDS, "leagues": leagues, "models": out_models}
    out = dump_ordered(doc)
    scrubbed(out)
    return out


# ---------------------------------------------------------------- reference.json

def interval(block: dict) -> dict:
    return {"point": block["point"], "ci95": [block["lo"], block["hi"]]}


def reference_bytes(results: dict, leaderboard: dict) -> bytes:
    """reference.json from the scorer's results.json. Refuses (NotScored) while the three instances are not all complete."""
    if results.get("schema") != SCORED_SCHEMA:
        raise CheckError(f"the reference results are not {SCORED_SCHEMA!r}")
    three, insts = results.get("three_instances", {}), results.get("instances", {})
    keys = ("1", "2", "3")
    if three.get("status") != "complete" or any(insts.get(k, {}).get("status") != "complete" for k in keys):
        raise NotScored("reference results not scored yet: results.json has no complete three-instance summary (status "
                        f"{three.get('status')!r})")
    design, boot = results["design"], results["bootstrap"]
    check((design["items"], design["relations"], design["groups"], boot["leagues"]) ==
          (SAMPLE_ITEMS, SAMPLE_RELATIONS, SAMPLE_GROUPS, LEAGUES), "the reference sample is not 500 items, 300 and 200, 18 leagues")
    metrics = {name: interval(three["weighted"][name]) for name in ("headline", "relation", "group")}
    for name in metrics:
        mean = sum(insts[k]["weighted"][name]["point"] for k in keys) / 3
        check(abs(metrics[name]["point"] - mean) <= 1e-6, f"{name}: the point is not the mean of the three instances")
    instances = [{"id": k, "headline": insts[k]["weighted"]["headline"]["point"],
                  "relation": insts[k]["weighted"]["relation"]["point"], "group": insts[k]["weighted"]["group"]["point"]}
                 for k in keys]

    def agree(op: str) -> dict:
        a = three["agreement"][op]
        pairs = list(a["pairwise"].values())
        check(len(pairs) == 3, f"{op}: the three instances have {len(pairs)} pairs, not 3")
        check(isinstance(a["fleiss_kappa"], (int, float)) and not isinstance(a["fleiss_kappa"], bool),
              f"{op}: fleiss_kappa is {a['fleiss_kappa']!r}, not a number (every rating in one category): the contract has no null")
        return {"pairwise": round(sum(pairs) / 3, 6), "fleiss_kappa": a["fleiss_kappa"]}

    ids = {e["id"] for e in leaderboard["models"] + leaderboard["baselines"]}
    check(REFERENCE_ID not in ids, "the reference id equals a model or baseline id")
    ranking = leaderboard["ranking"]
    check(set(ranking) <= set(results["models"]), "the reference results do not score every leaderboard model")
    on_sample = [{"id": m, **{name: interval(results["models"][m]["weighted"][name]) for name in ("headline", "relation", "group")}}
                 for m in ranking]
    doc = {"schema": REFERENCE_SCHEMA, "release": RELEASE, "references": [{
        "id": REFERENCE_ID, "display_name": "Claude Haiku 5.5, reasoning allowed", "kind": "reasoning_reference", "ranked": False,
        "model": "claude-haiku-5-5", "note": REFERENCE_NOTE,
        "sample": {"items": SAMPLE_ITEMS, "relations": SAMPLE_RELATIONS, "groups": SAMPLE_GROUPS, "weighted": True,
                   "leagues": LEAGUES, "instances": 3},
        "conditions": CONDITIONS, "metrics": metrics, "instances": instances,
        "agreement": {"relation": agree("decide_relation"), "group": agree("decide_group")}}],
        "models_on_sample": on_sample}
    check(not has_key(doc, "rank"), "reference.json holds a rank")
    out = dump_ordered(doc)
    scrubbed(out)
    return out


# ---------------------------------------------------------------- the results index

def index_doc() -> bytes | None:
    """results/index.json with v0.2.1 as the latest release and v0.2.0 superseded by it. None once a later release is the index's
    latest: that release's builder owns the index from then on."""
    index = jload(RESULTS / "index.json")
    if index["latest"] not in (BASED_ON, RELEASE):
        return None
    versions = [v for v in index["versions"] if v["version"] != RELEASE]
    v020 = next(v for v in versions if v["version"] == BASED_ON)
    superseded = {**v020, "status": "superseded", "superseded_by": RELEASE, "note": NOTE_SUPERSEDED}
    latest = {
        "version": RELEASE, "status": "latest", "superseded_by": None, "benchmark": "Mentat-Hard", "fixture": "mentat_hard_test",
        "fixture_version": "3.1", "headline": "relation_group_mcc", "date": RELEASE_DATE, "based_on": BASED_ON,
        "ollama_version": v020["ollama_version"], "builder": f"results/{RELEASE}/build_bundle.py",
        "leaderboard_json": f"results/{RELEASE}/leaderboard.json", "leaderboard_csv": f"results/{RELEASE}/leaderboard.csv",
        "analysis_json": f"results/{RELEASE}/analysis.json", "profiles_json": f"results/{RELEASE}/profiles.json",
        "reference_json": f"results/{RELEASE}/reference.json", "readme": f"results/{RELEASE}/README.md",
        "cells_dir": v020["cells_dir"], "predictions_dir": v020["predictions_dir"], "repeats": v020["repeats"],
        "runcards_dir": v020["runcards_dir"], "deviations": v020["deviations"], "private_split": None,
        "prereg": v020["prereg"], "release_manifest": v020["release_manifest"],
        "release_gate_output": v020["release_gate_output"], "attestations": v020["attestations"],
    }
    kept = [superseded if v["version"] == BASED_ON else v for v in versions]
    return dump_sorted({**index, "latest": RELEASE, "versions": [latest] + kept})


# ---------------------------------------------------------------- build

def build_outputs() -> dict[str, bytes]:
    """Every file this release writes except reference.json, in memory, from committed files only."""
    analysis = analysis_bytes((V020 / "analysis.json").read_bytes())
    out = {
        "leaderboard.json": leaderboard_bytes((V020 / "leaderboard.json").read_bytes(), sha256(analysis)),
        "leaderboard.csv": (V020 / "leaderboard.csv").read_bytes(),  # no release or provenance in it: v0.2.0's bytes
        "analysis.json": analysis,
        "profiles.json": profiles_bytes(),
    }
    index = index_doc()
    if index is not None:
        out["index.json"] = index
    for data in out.values():
        scrubbed(data)
    return out


def build(check_only: bool) -> int:
    outputs = build_outputs()
    targets = {"leaderboard.json": V021 / "leaderboard.json", "leaderboard.csv": V021 / "leaderboard.csv",
               "analysis.json": V021 / "analysis.json", "profiles.json": V021 / "profiles.json",
               "reference.json": V021 / "reference.json", "index.json": RESULTS / "index.json"}
    refused = None
    try:
        outputs["reference.json"] = reference_bytes(jload(REFERENCE_RESULTS), jload(V020 / "leaderboard.json"))
    except NotScored as exc:
        refused = str(exc)
    if check_only:
        stale = [name for name, data in outputs.items() if not targets[name].is_file() or targets[name].read_bytes() != data]
        if stale:
            print(f"build --check: differs from the committed files: {stale}", file=sys.stderr)
        if refused:
            print(f"build --check: {refused}", file=sys.stderr)
        if stale or refused:
            return 1
        print("build --check: the committed files are what build writes", file=sys.stderr)
        return 0
    for name, data in outputs.items():
        targets[name].write_bytes(data)
    print(f"wrote {', '.join(outputs)}", file=sys.stderr)
    if refused:
        print(f"build: {refused}; reference.json is not written", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write the v0.2.1 files from committed files")
    b.add_argument("--check", action="store_true", help="compare the bytes with the committed files and exit 1 on a difference")
    a = ap.parse_args(argv)
    try:
        return build(a.check)
    except Exception as exc:  # a failed check, a missing or unreadable input: report it, write nothing more
        print(f"build_bundle v0.2.1: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

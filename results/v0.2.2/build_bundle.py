"""Builds the v0.2.2 analysis release, based on v0.2.1: the same measurements, no score changed, the real-question transfer check added.

usage, from the repository root:
  python results/v0.2.2/build_bundle.py build [--check]

build [--check] reads committed files only: results/v0.2.1/ (leaderboard.json, leaderboard.csv, analysis.json, profiles.json,
reference.json), results/v0.2.0/leaderboard.json (the v0.2.0 ranking and headlines), the transfer pilot's results.json and
results/index.json. It writes:
  leaderboard.json, leaderboard.csv  every v0.2.1 row unchanged; the release, date and provenance rewritten (based_on_release v0.2.1)
  analysis.json                      v0.2.1's analysis with its release field re-pointed to v0.2.2; nothing recomputed
  profiles.json, reference.json      v0.2.1's files with only the release field re-pointed
  transfer.json                      the pilot's results.json, rounded, with each model's v0.2.0 headline beside its pilot numbers
  index.json                         only while v0.2.1 or v0.2.2 is the index's latest release; a later release's builder owns it then
README.md is written by hand.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"
V022 = RESULTS / "v0.2.2"
V021 = RESULTS / "v0.2.1"
V020 = RESULTS / "v0.2.0"
RELEASE, BASED_ON = "v0.2.2", "v0.2.1"
RELEASE_DATE = "2026-11-04"  # set by the owner
PILOT = ROOT / "docs" / "audits" / "v0.2.0-transfer-pilot"
PILOT_SCHEMA = "mentat-bench-transfer-results/1"  # the pilot's analyse.py score output
TRANSFER_SCHEMA = "mentat-bench-transfer/1"
NOTE, DEVIATIONS = "docs/audits/v0.2.0-transfer-pilot/notes.md", "docs/audits/v0.2.0-transfer-pilot/deviations.md"
THRESHOLD = 0.6  # the pilot's pre-registered threshold for the reading
STATISTIC = "spearman rho, pilot headline vs v0.2.0 headline"  # analyse.py's primary statistic (PREREG: Spearman's rho of the headlines)
# The site's banned words (its copy guard), checked in the conditions; "fixture" is banned in any prose.
BANNED = ("fixture", "outperform", "smarter", "superior", "beats", "better than", "general reasoning", "general capabilit",
          "public bundle", "test fixture", "v0.3", "measurement release", "internal release")
SENSITIVITY = {"unanimous_only": "Unanimous judges only", "excluding_changed": "Excluding items the reviewer changed",
               "excluding_trivial": "Excluding trivial items"}
ANCHORS = ("heuristic", "lexical")
NOTE_SUPERSEDED = "same measurements; v0.2.2 adds the real-question transfer check (unranked)"
# The index entry's pass-through fields, copied from v0.2.1's entry.
PASS_THROUGH = ("attestations", "benchmark", "cells_dir", "deviations", "fixture", "fixture_version", "headline", "ollama_version",
                "predictions_dir", "prereg", "private_split", "release_gate_output", "release_manifest", "repeats", "runcards_dir")
CONDITIONS = [
    "The labels are not exact gold. Three sessions of Claude Haiku 5.5 judged each item from the question text and the outcome label.",
    "The maintainer review of 90 items was AI-assisted, which makes its human layer weaker than an unassisted review; the maintainer "
    "checked and owns those answers.",
    "The reviewer applied a stricter ambiguity standard than the judges, and about 470 items the reviewer did not see keep the "
    "judges' readings.",
    "Eight answers were revised after an AI consistency check, so those items were not reviewed blind.",
    "The three judge sessions are sessions of one model, not three independent models.",
    "The items are not a representative sample of Polymarket questions. They are 200 items in 89 clusters of related events, from one "
    "snapshot of one venue.",
    "The models and judges saw the question text and the outcome label, not the market rules. The models also saw each event's "
    "title, but only in the binding prompt, as in v0.2.0; the judges did not see it.",
    "The questions are public and recent, and the models' training data is not known, so memorisation of these items is possible.",
    "The relation floors were calibrated on the v0.2.0 Mentat-Hard test set and carried over unchanged.",
    "Each model was run once, as on the leaderboard. Group MCC is low for every model on both sets, so small differences in group "
    "order are mostly noise.",
    "Headline levels are not comparable across the two sets: most models score higher on the pilot, so only the order is compared.",
    "On these 200 items, under these conditions, the order carries over; the check is exploratory and changes no score, rank or "
    "leaderboard row.",
    "The pilot's departures from its pre-registration are logged in deviations.md.",
    "No question text is published; the repository holds market ids and sha256 hashes, and the text is refetched from Polymarket's "
    "public API.",
]
ABSOLUTE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|(?<![\w.])/(?:c|home|tmp|var|Users)/")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
IDS_ONLY = re.compile(r"[A-Za-z0-9_.+/ ,-]+")  # the text outside the conditions: ids, paths, digests and fixed labels, no question marks


class CheckError(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jload(path: Path):
    return json.loads(path.read_bytes().decode("utf-8"))


def dump_sorted(obj) -> bytes:
    """The release files the v0.1.x to v0.2.1 builders write (keys sorted, as the committed files are)."""
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def dump_ordered(obj) -> bytes:
    """profiles.json, reference.json and transfer.json: the keys in the order the contract shows them."""
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


def strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)
    elif isinstance(obj, str):
        yield obj


def repoint(data: bytes, dump) -> bytes:
    """A v0.2.1 file with its release field re-pointed to v0.2.2 and nothing else changed. The file must already be in the form
    the builder writes, so a parse and a dump reproduce its bytes and the output differs from it on the release line alone."""
    doc = json.loads(data.decode("utf-8"))
    check(doc["release"] == BASED_ON, f"the v0.2.1 file does not name {BASED_ON} as its release")
    check(dump(doc) == data, "the v0.2.1 file is not in the builder's form, so re-pointing its release would change more")
    doc["release"] = RELEASE
    return dump(doc)


# ---------------------------------------------------------------- the analysis and the leaderboard (carried from v0.2.1)

def analysis_bytes(v021: bytes) -> bytes:
    """v0.2.1's analysis, its release field re-pointed to v0.2.2. Its based_on (the v0.2.0 leaderboard and predictions) stays: the
    chain runs v0.2.2 -> v0.2.1 -> v0.2.0."""
    check(json.loads(v021.decode("utf-8"))["based_on"]["release"] == "v0.2.0", "the v0.2.1 analysis does not name v0.2.0 as its base")
    return repoint(v021, dump_sorted)


def leaderboard_bytes(v021: bytes, analysis_sha: str) -> bytes:
    """v0.2.1's leaderboard with the release, date and provenance of an analysis release based on v0.2.1. The rows, the ranking and
    the comparisons are unchanged, byte for byte once serialised."""
    lb = json.loads(v021.decode("utf-8"))
    check(lb["release"] == BASED_ON, "the v0.2.1 leaderboard does not name v0.2.1")
    lb["release"] = RELEASE
    lb["date"] = RELEASE_DATE
    prov = lb["provenance"]
    check("record_from" not in prov, "the v0.2.1 leaderboard holds a record_from")
    prov["analysis"] = {"path": f"results/{RELEASE}/analysis.json", "sha256": analysis_sha}
    prov["based_on_release"] = BASED_ON
    prov["bundle_builder"] = f"results/{RELEASE}/build_bundle.py"
    return dump_sorted(lb)


# ---------------------------------------------------------------- transfer.json

def block(b: dict) -> dict:
    """A pilot point and its 95% interval, rounded."""
    return {"point": round(b["point"], 6), "ci95": [round(b["lo"], 6), round(b["hi"], 6)]}


def reading_rule(lo: float, hi: float) -> str:
    """The pre-registered reading of an interval (PREREG, Primary): carries over when its lower bound is at least the threshold, does not
    carry over when its upper bound is below it, and inconclusive otherwise."""
    return "carries over" if lo >= THRESHOLD else "does not carry over" if hi < THRESHOLD else "inconclusive"


def transfer_bytes(pilot: bytes, v020_lb: dict) -> bytes:
    """transfer.json, from the pilot's results.json (the numbers are rounded to 6 decimals). The models are in the v0.2.0
    leaderboard's rank order, each with its v0.2.0 headline. Only the ids, the readings and the statistic come from the pilot's text."""
    res = json.loads(pilot.decode("utf-8"))
    check(res["schema"] == PILOT_SCHEMA, f"the pilot results are not {PILOT_SCHEMA!r}")
    main, sec, ranking = res["main"], res["main"]["secondary"], v020_lb["ranking"]
    v020 = {e["id"]: e["metrics"]["headline"]["point"] for e in v020_lb["models"]}
    check(len(ranking) == 17 and set(ranking) == set(main["models"]) == set(v020), "the pilot does not hold the 17 leaderboard models")
    check(set(res["sensitivity"]) == set(SENSITIVITY), "the pilot's sensitivity analyses are not the three pre-registered ones")
    check(set(main["anchors"]) == set(ANCHORS), "the pilot's anchors are not heuristic and lexical")
    prim = main["primary"]
    check(prim["statistic"] == STATISTIC, "the primary statistic is not the pre-registered one")
    check(prim["reading"] == reading_rule(prim["lo"], prim["hi"]),
          "the primary reading is not the pre-registered rule applied to its interval")
    for k in SENSITIVITY:
        s = res["sensitivity"][k]
        check(s["reading"] == reading_rule(s["lo"], s["hi"]), f"the reading of {k} is not the pre-registered rule applied to its interval")
    items = main["items"]
    doc = {
        "schema": TRANSFER_SCHEMA, "release": RELEASE, "based_on": BASED_ON, "kind": "transfer_check", "ranked": False,
        "title": "Real-question check", "note": NOTE, "deviations": DEVIATIONS,
        "source": {"path": "docs/audits/v0.2.0-transfer-pilot/results.json", "sha256": sha256(pilot)},
        "items": {"relations": items["relation"], "groups": items["group"], "clusters": items["clusters"]},
        "primary": {"statistic": prim["statistic"], **block(prim), "draws": prim["draws"], "reading": prim["reading"],
                    "threshold": THRESHOLD},
        "secondary": {"kendall_tau_b": block(sec["kendall_tau_b"]), "rho_relation": block(sec["rho_relation"]),
                      "rho_group": block(sec["rho_group"]),
                      "resolved_pairs": {"pairs": sec["resolved_pairs"]["pairs"], "kept_order": sec["resolved_pairs"]["kept_order"]}},
        "sensitivity": [{"id": k, "label": label, "relations": res["sensitivity"][k]["items"]["relation"],
                         "groups": res["sensitivity"][k]["items"]["group"], **block(res["sensitivity"][k]),
                         "reading": res["sensitivity"][k]["reading"]} for k, label in SENSITIVITY.items()],
        "models": [{"id": m, "v020_headline": round(v020[m], 6), "headline": block(main["models"][m]["headline"]),
                    "relation_mcc": round(main["models"][m]["relation_mcc"]["point"], 6),
                    "group_mcc": round(main["models"][m]["group_mcc"]["point"], 6),
                    "relation_accuracy": round(main["models"][m]["accuracy"]["decide_relation"], 6),
                    "group_accuracy": round(main["models"][m]["accuracy"]["decide_group"], 6)} for m in ranking],
        "anchors": [{"id": a, "headline": block(main["anchors"][a]["headline"]),
                     "relation_mcc": round(main["anchors"][a]["relation_mcc"]["point"], 6),
                     "group_mcc": round(main["anchors"][a]["group_mcc"]["point"], 6)} for a in ANCHORS],
        "conditions": CONDITIONS,
    }
    check(not has_key(doc, "rank") and doc["ranked"] is False, "transfer.json holds a rank key or is ranked")
    text = " ".join(CONDITIONS).lower()
    check(not any(word in text for word in BANNED), "the conditions hold a banned word, or the word fixture")
    check(all(IDS_ONLY.fullmatch(s) for s in strings({k: v for k, v in doc.items() if k != "conditions"})),
          "free text outside the conditions (only ids, paths, digests and fixed labels are allowed)")
    return dump_ordered(doc)


# ---------------------------------------------------------------- the results index

def index_doc() -> bytes | None:
    """results/index.json with v0.2.2 as the latest release and v0.2.1 superseded by it. None once a later release is the index's
    latest: that release's builder owns the index from then on."""
    index = jload(RESULTS / "index.json")
    if index["latest"] not in (BASED_ON, RELEASE):
        return None
    versions = [v for v in index["versions"] if v["version"] != RELEASE]
    v021 = next(v for v in versions if v["version"] == BASED_ON)
    superseded = {**v021, "status": "superseded", "superseded_by": RELEASE, "note": NOTE_SUPERSEDED}
    latest = {
        **{k: v021[k] for k in PASS_THROUGH}, "version": RELEASE, "status": "latest", "superseded_by": None, "based_on": BASED_ON,
        "date": RELEASE_DATE, "builder": f"results/{RELEASE}/build_bundle.py",
        "leaderboard_json": f"results/{RELEASE}/leaderboard.json", "leaderboard_csv": f"results/{RELEASE}/leaderboard.csv",
        "analysis_json": f"results/{RELEASE}/analysis.json", "profiles_json": f"results/{RELEASE}/profiles.json",
        "reference_json": f"results/{RELEASE}/reference.json", "transfer_json": f"results/{RELEASE}/transfer.json",
        "readme": f"results/{RELEASE}/README.md",
    }
    kept = [superseded if v["version"] == BASED_ON else v for v in versions]
    return dump_sorted({**index, "latest": RELEASE, "versions": [latest] + kept})


# ---------------------------------------------------------------- build

def build_outputs() -> dict[str, bytes]:
    """Every file this release writes, in memory, from committed files only."""
    analysis = analysis_bytes((V021 / "analysis.json").read_bytes())
    out = {
        "leaderboard.json": leaderboard_bytes((V021 / "leaderboard.json").read_bytes(), sha256(analysis)),
        "leaderboard.csv": (V021 / "leaderboard.csv").read_bytes(),  # no release or provenance in it: v0.2.1's bytes
        "analysis.json": analysis,
        "profiles.json": repoint((V021 / "profiles.json").read_bytes(), dump_ordered),
        "reference.json": repoint((V021 / "reference.json").read_bytes(), dump_ordered),
        "transfer.json": transfer_bytes((PILOT / "results.json").read_bytes(), jload(V020 / "leaderboard.json")),
    }
    index = index_doc()
    if index is not None:
        out["index.json"] = index
    for data in out.values():
        scrubbed(data)
    return out


def build(check_only: bool) -> int:
    outputs = build_outputs()
    targets = {name: V022 / name for name in outputs}
    targets["index.json"] = RESULTS / "index.json"
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
    b = sub.add_parser("build", help="write the v0.2.2 files from committed files")
    b.add_argument("--check", action="store_true", help="compare the bytes with the committed files and exit 1 on a difference")
    a = ap.parse_args(argv)
    try:
        return build(a.check)
    except Exception as exc:  # a failed check, a missing or unreadable input: report it, write nothing more
        print(f"build_bundle v0.2.2: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

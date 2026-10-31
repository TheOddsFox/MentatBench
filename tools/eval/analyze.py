"""MEASUREMENT ONLY (reads <fixture>/gold; product code never does). Per-op error analysis of one finalized
toy_native cell.  usage: python tools/eval/analyze.py NAME WORK_DIR FINALIZE_RUN_DIR OUT.json --fixture-dir FIXTURE_DIR
WORK_DIR: the build's work dir (checkpoint.json, decided.jsonl, relations.jsonl, groups.jsonl).
FINALIZE_RUN_DIR: finalize_run.py output for that work dir (units.json: which relation rows / group units admission kept).
Gold relation entailment: gold_oracle.Gold (2-CNF closure of gold/theory.json + gold bindings, all 4 interpretations)."""
import argparse
import json
from array import array
import sys
from collections import Counter, defaultdict
from pathlib import Path

from mentat_bench import ir
from mentat_bench.children.common import binary_label
from mentat_bench.harness import fixed as F

sys.path.insert(0, str(Path(__file__).parent))
from gold_oracle import Gold  # noqa: E402

OPS = ("IMPLIES", "IMPLIED_BY", "EXCLUDES", "EQUIV")
FLIP = {"IMPLIES": "IMPLIED_BY", "IMPLIED_BY": "IMPLIES", "EXCLUDES": "EXCLUDES", "EQUIV": "EQUIV"}

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("name")
ap.add_argument("work", type=Path)
ap.add_argument("frun", type=Path)
ap.add_argument("outp", type=Path)
ap.add_argument("--fixture-dir", type=Path, required=True)
a = ap.parse_args()
name, work, frun, outp, FX = a.name, a.work, a.frun, a.outp, a.fixture_dir
ckpt = json.loads((work / "checkpoint.json").read_text())
curated = (ckpt.get("policy") or {}).get("candidates_sha256") is not None  # build --candidates: no blocking, pairs as asked
corpus = list(ir.read_jsonl(FX / "corpus.jsonl"))
cid = [r["id"] for r in corpus]
cidx = {x: k for k, x in enumerate(cid)}
gold = Gold(FX, cid)
decided = list(F._jsonl_prefix(work / "decided.jsonl", ckpt["decided_bytes"]))
to_c = [cidx[r["id"]] for r in decided]  # decided-row index -> corpus index
rel_rows = list(F._jsonl_prefix(work / "relations.jsonl", ckpt["relations_bytes"]))
units = json.loads((frun / "units.json").read_text())
assert len(units["rel_dropped"]) == len(rel_rows), (len(units["rel_dropped"]), len(rel_rows))
market = [x.rsplit(":", 1)[0] for x in cid]

_ent = {}


def ent(i, j, rel):  # corpus indices
    k = (i, j, rel)
    if k not in _ent:
        _ent[k] = gold.entailed(i, j, rel)
    return _ent[k]


def bucket(c):
    if c is None:
        return "none"
    return "<4" if c < 4 else "4-5" if c < 5 else "5-6" if c < 6 else "6+"


def pr(n, k):
    return {"n": n, "entailed": k, "precision": round(k / n, 4) if n else None}


per = defaultdict(lambda: [0, 0])  # (kind, op) -> [n, entailed]
per_kept = defaultdict(lambda: [0, 0])
by_conf = defaultdict(lambda: [0, 0])
same_mkt = defaultdict(lambda: [0, 0])
wrong = Counter()
named_at = {}  # (corpus i, corpus j) -> relation (oriented i<j), decisions and derived rows
for k, row in enumerate(rel_rows):
    i, j, rel = to_c[row[0]], to_c[row[1]], row[2]
    rep = row[3] if len(row) > 3 else -1
    conf = row[4] if len(row) > 4 else None
    kind = "decision" if rep == -1 else "derived"
    e = ent(i, j, rel)
    if not e:  # a wrong row: gold entails another op on the pair (conflict) or nothing (unsupported)
        conflict = any(ent(i, j, op) for op in OPS if op != rel)
        wrong[(kind, rel, "conflict" if conflict else "unsupported")] += 1
    for d in (per[(kind, rel)], per[(kind, "ALL")]):
        d[0] += 1
        d[1] += e
    if not units["rel_dropped"][k]:
        for d in (per_kept[(kind, rel)], per_kept[(kind, "ALL")]):
            d[0] += 1
            d[1] += e
    if kind == "decision":
        for d in (by_conf[(bucket(conf), rel)], by_conf[(bucket(conf), "ALL")]):
            d[0] += 1
            d[1] += e
    if market[i] == market[j]:
        for d in (same_mkt[(kind, rel)], same_mkt[(kind, "ALL")]):
            d[0] += 1
            d[1] += e
    a, b, r = (i, j, rel) if i < j or curated else (j, i, FLIP[rel])  # a curated pair keeps its asked orientation
    named_at[(a, b)] = r

# recall over the candidate pairs (every pair the relation phase considered: asked, or derived by complement dedup)
recs = [{"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r.get("event_title")} for r in corpus]
if curated:  # the curated pairs as asked (pairs.bin: decided-row indices); the blocking recall check does not apply
    raw_pairs = array("I")
    raw_pairs.frombytes((work / "pairs.bin").read_bytes())
    cands = [(to_c[raw_pairs[k]], to_c[raw_pairs[k + 1]]) for k in range(0, len(raw_pairs), 2)]
else:
    cands = F.candidate_pairs(recs, top_k=24)
    assert len(cands) == ckpt["pairs_considered"], (len(cands), ckpt["pairs_considered"])
rec = defaultdict(lambda: [0, 0, 0])  # strongest gold-entailed op -> [gold positives, named entailed, named anything]
miss_as = Counter()
for i, j in cands:
    a, b = (i, j) if curated else (min(i, j), max(i, j))
    g = [op for op in OPS if ent(a, b, op)]
    if not g:
        continue
    top = "EQUIV" if "EQUIV" in g else g[0]
    key = ("same_market" if market[a] == market[b] else "cross_market", top)
    r = named_at.get((a, b))
    for kk in (key, ("all", top), ("all", "ALL")):
        rec[kk][0] += 1
        rec[kk][1] += r is not None and r in g
        rec[kk][2] += r is not None
    if r is None or r not in g:
        miss_as[(top, r or "NONE/not logged")] += 1

# bindings: binary-label negation vs the label side, and status vs gold
gb = {json.loads(line)["id"]: json.loads(line) for line in open(FX / "gold" / "bindings.jsonl", encoding="utf-8")}
neg = Counter()
for r in decided:
    lab = corpus[cidx[r["id"]]]["label"]
    is_bin, side = binary_label(lab)
    if not is_bin:
        neg["named_outcome", r["status"]] += 1
        continue
    if r["status"] != "bound":
        neg["binary", "unresolved"] += 1
    else:
        neg["binary", "negation_correct" if bool(r["negated"]) == side else "negation_wrong"] += 1
bstat = Counter((r["status"], gb[r["id"]]["status"]) for r in decided)

# groups
groups = list(F._jsonl_prefix(work / "groups.jsonl", ckpt["groups_bytes"]))
gk = Counter((g["kind"], g["source"]) for g in groups)
gu = defaultdict(lambda: [0, 0, 0, 0, 0])  # unit kind -> [units, kept, 2cnf, kept 2cnf, kept 2cnf entailed]
for row, kind, dropped, two, en in units["groups"]:
    d = gu[kind]
    d[0] += 1
    d[1] += not dropped
    d[2] += two
    d[3] += two and not dropped
    d[4] += (two and not dropped and en)
fin = json.loads((frun / "finalize_result.json").read_text()).get("summary") or {}

asked = ckpt["relations_committed"]
dec_named = per[("decision", "ALL")][0]
out = {
    "name": name, "pairs_considered": ckpt["pairs_considered"], "asked": asked,
    "pairs_derived": ckpt.get("pairs_derived"), "relation_rejected": ckpt.get("relation_rejected"),
    "equiv_rejected": ckpt.get("equiv_rejected"),
    "named_decisions": dec_named, "derived_rows": per[("derived", "ALL")][0],
    "none_rate": round(1 - (dec_named + (ckpt.get("relation_rejected") or 0) + (ckpt.get("equiv_rejected") or 0)) / asked, 4),
    "per_op": {f"{k}:{op}": pr(*v) for (k, op), v in sorted(per.items())},
    "per_op_retained": {f"{k}:{op}": pr(*v) for (k, op), v in sorted(per_kept.items())},
    "by_conf": {f"{b}:{op}": pr(*v) for (b, op), v in sorted(by_conf.items())},
    "wrong_rows": {f"{k}:{op}:{w}": n for (k, op, w), n in sorted(wrong.items())},
    "same_market_rows": {f"{k}:{op}": pr(*v) for (k, op), v in sorted(same_mkt.items())},
    "recall": {f"{a}:{op}": {"gold_entailed": v[0], "named_entailed": v[1], "named_any": v[2],
                             "recall": round(v[1] / v[0], 4) if v[0] else None} for (a, op), v in sorted(rec.items())},
    "misses_top": [[f"gold {t} / child {c}", n] for (t, c), n in miss_as.most_common(8)],
    "binary_negation": {f"{a}:{b}": n for (a, b), n in sorted(neg.items())},
    "binding_status_vs_gold": {f"child {a} / gold {b}": n for (a, b), n in sorted(bstat.items())},
    "group_rows": {f"{k}:{s}": n for (k, s), n in sorted(gk.items())},
    "group_units": {k: dict(zip(("units", "kept", "2cnf", "kept_2cnf", "kept_2cnf_entailed"), v)) for k, v in gu.items()},
    "finalize": {k: fin.get(k) for k in ("admit_mode", "relations_dropped", "derived_dropped", "floor_dropped_relations",
                                          "groups_committed", "groups_dropped", "groups_dropped_pct", "base_repaired",
                                          "contradiction_rate", "admit_rejected_relations", "admit_rejected_groups",
                                          "admit_seconds", "base_probe")},
    "gold_ignored_ops": gold.ignored, "wall_seconds_checkpoint": ckpt.get("wall_seconds"),
}
outp.write_text(json.dumps(out, indent=2))
print(json.dumps({k: out[k] for k in ("name", "asked", "named_decisions", "none_rate")}))

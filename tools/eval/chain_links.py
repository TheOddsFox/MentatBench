"""MEASUREMENT ONLY (reads <fixture>/gold; product code never does). Classifies each ordered_chain link (a -> b, after the
direction) of a kept work dir against gold, and whether admission kept it. Prints one JSON line.

usage: python tools/eval/chain_links.py NAME WORK_DIR FINALIZE_RUN_DIR --fixture-dir FIXTURE_DIR
FIXTURE_DIR: a fixtures/<name> directory (corpus.jsonl and gold/). FINALIZE_RUN_DIR: finalize_run.py output for WORK_DIR
(units.json: which group units admission kept)."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from mentat_bench import ir
from mentat_bench.harness import fixed as F

sys.path.insert(0, str(Path(__file__).parent))
from gold_oracle import Gold  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("name")
ap.add_argument("work", type=Path)
ap.add_argument("frun", type=Path)
ap.add_argument("--fixture-dir", type=Path, required=True)
a = ap.parse_args()

ckpt = json.loads((a.work / "checkpoint.json").read_text())
corpus = list(ir.read_jsonl(a.fixture_dir / "corpus.jsonl"))
cid = [r["id"] for r in corpus]
ci = {x: k for k, x in enumerate(cid)}
g = Gold(a.fixture_dir, cid)
dec = list(F._jsonl_prefix(a.work / "decided.jsonl", ckpt["decided_bytes"]))
mkt = [x.rsplit(":", 1)[0] for x in cid]
units_by_row = {}
for row, kind, dropped, two, en in json.loads((a.frun / "units.json").read_text())["groups"]:
    units_by_row.setdefault(row, []).append(dropped)
cnt = Counter()
for n, r in enumerate(F._jsonl_prefix(a.work / "groups.jsonl", ckpt["groups_bytes"])):
    if r["kind"] != "ordered_chain":
        continue
    m = [ci[dec[x]["id"]] for x in r["members"]]
    drops = units_by_row.get(n)
    for link, (x, y) in enumerate(zip(m, m[1:])):
        if r["direction"] == "reverse":
            x, y = y, x
        cls = ("same_market" if mkt[x] == mkt[y] else "gold_implies" if g.entailed(x, y, "IMPLIES")
               else "gold_reverse" if g.entailed(x, y, "IMPLIED_BY") else "gold_excludes" if g.entailed(x, y, "EXCLUDES")
               else "unrelated_in_gold")
        kept = "?" if drops is None or link >= len(drops) else ("dropped" if drops[link] else "kept")
        cnt[f"{cls}:{kept}"] += 1
print(json.dumps({"name": a.name, "links": dict(sorted(cnt.items())), "unit_rows_matched": len(units_by_row)}))

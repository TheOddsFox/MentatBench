"""MEASUREMENT ONLY (reads <fixture>/gold; product code never does). Runs the group decision of the HF child on a seeded
sample of candidate sets of a fixture (group_candidates over s1, s2, s3), and measures the gold-entailed share of the
ordered_chain links it emits. Run it once per checkout (PYTHONPATH=<checkout>/python) to compare the old and new child.
The model comes from MB_HF_* as for the child (e.g. MB_HF_MODEL=Qwen/Qwen2.5-1.5B-Instruct, MB_HF_DEVICE=cuda:0).

usage: python tools/eval/chain_sample.py --fixture-dir FIXTURE_DIR [--n 200] [--seed 0] [--batch 16] [--out JSON]

A link is (a, b) of consecutive members of an emitted chain, after its direction (forward: a before b). It is gold-entailed
when gold implies a -> b (gold_oracle.Gold, every interpretation). A link is reversed when gold implies b -> a and not
a -> b. The correct-direction share is entailed / (entailed + reversed): the direction accuracy among the links gold orders.
A chain is fully entailed when every link is."""
import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from mentat_bench import ir
from mentat_bench.children import hf_child as HF
from mentat_bench.harness.fixed import _context, group_candidates

sys.path.insert(0, str(Path(__file__).parent))
from gold_oracle import Gold  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture-dir", type=Path, required=True)
    ap.add_argument("--n", type=int, default=200, help="candidate sets in the sample (default 200)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--batch", type=int, default=16, help="candidate sets per decide call (the protocol's batch, 16)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    corpus = list(ir.read_jsonl(a.fixture_dir / "corpus.jsonl"))
    cid = [r["id"] for r in corpus]
    recs = [{"id": r["id"], "question": r["text"], "label": r.get("label"), "event_title": r.get("event_title"),
             "context": r.get("context")} for r in corpus]  # _context omits a null card
    candidates = list(group_candidates(recs, ("s1", "s2", "s3"), 64))
    picked = random.Random(a.seed).sample(range(len(candidates)), min(a.n, len(candidates)))
    items = [{"ctx": {"group_id": f"c{k:07d}", "source": candidates[j][0],
                      "members": [_context(recs[i]) for i in candidates[j][1]]}} for k, j in enumerate(picked)]
    child = HF.HFChild.from_env()
    gold = Gold(a.fixture_dir, cid)
    market = [x.rsplit(":", 1)[0] for x in cid]
    ci = {x: k for k, x in enumerate(cid)}
    kinds: Counter = Counter()
    stats: Counter = Counter()
    errors = 0
    started = time.perf_counter()
    for start in range(0, len(items), a.batch):
        results, _ = child.decide("decide_group", items[start:start + a.batch])
        for result in results:
            if not isinstance(result, dict) or "groups" not in result:
                errors += 1
                continue
            for group in result["groups"]:
                kinds[group["kind"]] += 1
                if group["kind"] != "ordered_chain":
                    continue
                members = [ci[m] for m in group["members"]]
                stats["chains"] += 1
                stats["chain_members"] += len(members)
                stats["reverse"] += group.get("direction") == "reverse"
                if group.get("direction") == "reverse":
                    members.reverse()
                entailed = []
                for x, y in zip(members, members[1:]):
                    stats["links"] += 1
                    ok = gold.entailed(x, y, "IMPLIES")
                    stats["entailed_links"] += ok
                    stats["reversed_links"] += (not ok) and gold.entailed(y, x, "IMPLIES")
                    stats["same_market_links"] += market[x] == market[y]
                    entailed.append(ok)
                stats["chains_fully_entailed"] += all(entailed)
    seconds = time.perf_counter() - started
    links = stats["links"]
    oriented = stats["entailed_links"] + stats["reversed_links"]
    out = {"fixture": a.fixture_dir.name, "sample": len(items), "candidate_sets": len(candidates), "seed": a.seed,
           "child": child.describe(), "decision_errors": errors, "kinds": dict(sorted(kinds.items())),
           "chains": stats["chains"], "chain_members_mean": round(stats["chain_members"] / max(1, stats["chains"]), 3),
           "links": links, "entailed_links": stats["entailed_links"],
           "entailed_share": round(stats["entailed_links"] / links, 4) if links else None,
           "reversed_links": stats["reversed_links"],
           "reversed_share": round(stats["reversed_links"] / links, 4) if links else None,
           "correct_direction_share": (round(stats["entailed_links"] / oriented, 4) if oriented else None),
           "chains_fully_entailed": stats["chains_fully_entailed"], "same_market_links": stats["same_market_links"],
           "reverse_chains": stats["reverse"], "decide_seconds": round(seconds, 2),
           "gold_ignored_ops": gold.ignored}
    text = json.dumps(out, indent=2, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

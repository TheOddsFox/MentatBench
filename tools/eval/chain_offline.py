"""MEASUREMENT ONLY (reads <fixture>/gold; product code never does). Offline check of the chain member selection, with no
model: over the candidate sets of group_candidates (s1, s2, s3) of a fixture, how many chains would be gold-entailed IF
they were ordered correctly, before (every member, as the old child offered) and after (common.chain_members).

usage: python tools/eval/chain_offline.py --fixture-dir FIXTURE_DIR [--fixture-dir ...] [--out JSON]

A chain is gold-orderable when its members can be placed in a sequence whose consecutive links are all gold IMPLIES. The
order used here is the gold order (members sorted by how many other members gold implies, most first). For a total
ladder that order is exact; otherwise the test is a lower bound. A set of more than 26 members is not decided by the
order stage (one letter each), so it is counted apart."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from mentat_bench import ir
from mentat_bench.children import hf_child as HF
from mentat_bench.children.common import chain_members
from mentat_bench.harness import fixed as F

sys.path.insert(0, str(Path(__file__).parent))
from gold_oracle import Gold  # noqa: E402

MAX_MEMBERS = 64  # the adapter's group limit (protocol.MAX_MEMBERS)


def chain_stats(gold: Gold, market: list[str], chosen: list[int]) -> dict[str, int]:
    """Links, gold-entailed links and orderability of one chain offered in the gold order."""
    count = {m: sum(gold.entailed(m, j, "IMPLIES") for j in chosen if j != m) for m in chosen}
    order = sorted(chosen, key=lambda m: (-count[m], m))
    links = list(zip(order, order[1:]))
    entailed = [gold.entailed(a, b, "IMPLIES") for a, b in links]
    return {"sets": 1, "links": len(links), "entailed_links": sum(entailed),
            "orderable": int(all(entailed)), "same_market_links": sum(market[a] == market[b] for a, b in links)}


def measure(fixture: Path) -> dict:
    corpus = list(ir.read_jsonl(fixture / "corpus.jsonl"))
    cid = [r["id"] for r in corpus]
    recs = [{"id": r["id"], "question": r["text"], "label": r.get("label"), "event_title": r.get("event_title")}
            for r in corpus]
    gold = Gold(fixture, cid)
    market = [x.rsplit(":", 1)[0] for x in cid]
    total = Counter()
    per = {"old": Counter(), "new": Counter()}
    for _, members in F.group_candidates(recs, ("s1", "s2", "s3"), MAX_MEMBERS):
        total["sets"] += 1
        old = list(members)
        if len(old) >= 2:
            for key, value in chain_stats(gold, market, old).items():
                per["old"][key] += value
        ctxs = [{"id": recs[i]["id"], "question": recs[i]["question"], "label": recs[i]["label"]} for i in members]
        kept = {m["id"] for m in chain_members(ctxs)}
        new = [i for i in members if recs[i]["id"] in kept]
        if len(new) < 2:
            per["new"]["fewer_than_two"] += 1
        elif len(new) > len(HF.ORDER_LETTERS):
            per["new"]["more_than_26"] += 1
        else:
            for key, value in chain_stats(gold, market, new).items():
                per["new"][key] += value
    return {"fixture": fixture.name, "candidate_sets": total["sets"],
            **{f"{name}": dict(counter) for name, counter in per.items()}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture-dir", type=Path, action="append", required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    results = [measure(fx) for fx in a.fixture_dir]
    text = json.dumps(results, indent=2, sort_keys=True)
    if a.out:
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

"""Why group MCC is low: an error analysis of the v0.2.0 group items. EXPLORATORY: not pre-registered; it changes no score,
gold label or flag.

usage: python -B docs/audits/v0.2.0-group-items/analyse.py      (from the repository root; writes results.json beside this
       file, deterministically; reads only committed files)

Inputs: results/v0.2.0/leaderboard.json and predictions/<id>.jsonl.gz (the headline run of each of the 17 models), and the
test fixture's candidates.jsonl and corpus.jsonl. The 1,000 curated group rows are scored; the 184 reading rows are not.

A group class is the canonical terms of its bounds (adapters.base.group_terms): lo..hi true members, optionally an ordered
chain. An answer is "true but weaker" when every bound it states holds in the gold (its lo <= gold lo, its hi >= gold hi, and
it claims a chain only when the gold has one) but it is not the gold class; it is "not entailed" otherwise (some world
allowed by the rules breaks it).
"""
import collections
import gzip
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RES = ROOT / "results/v0.2.0"
FIX = ROOT / "fixtures/mentat_hard_test"


def rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def bounds(cls, n):
    """(chain, lo, hi) of a class string, or None for an invalid or missing answer."""
    chain, lo, hi = False, 0, n
    if cls == "none":
        return chain, lo, hi
    for term in cls.split("+"):
        kind, _, k = term.partition(":")
        if kind == "ordered_chain":
            chain = True
        elif kind == "exactly_one":
            lo, hi = 1, 1
        elif kind == "at_most_one":
            hi = 1
        elif kind == "at_least_k":
            lo = int(k)
        elif kind == "at_most_k":
            hi = int(k)
        else:
            return None
    return chain, lo, hi


def kind(cls):
    """The class without its k values: at_least_k:2+at_most_k:3 -> at_least_k+at_most_k."""
    return "+".join(term.split(":")[0] for term in cls.split("+"))


def outcome(gold, pred, n):
    if pred == gold:
        return "correct"
    g, p = bounds(gold, n), bounds(pred, n)
    if p is None:
        return "invalid"
    if not ((not p[0] or g[0]) and p[1] <= g[1] and p[2] >= g[2]):
        return "not_entailed"
    if pred == "none":
        return "weaker_said_none"
    return "weaker_right_kind" if kind(pred) == kind(gold) else "weaker_other_kind"


def mcc(pairs):
    """Multiclass MCC (Gorodkin) of (truth, pred) pairs."""
    t = collections.Counter(a for a, _ in pairs)
    p = collections.Counter(b for _, b in pairs)
    s, c = len(pairs), sum(a == b for a, b in pairs)
    labels = set(t) | set(p)
    den = math.sqrt(s * s - sum(p[k] ** 2 for k in labels)) * math.sqrt(s * s - sum(t[k] ** 2 for k in labels))
    return (c * s - sum(t[k] * p[k] for k in labels)) / den if den else 0.0


def share(counter, total):
    return {k: round(v / total, 4) for k, v in sorted(counter.items())}


def main():
    lb = json.loads((RES / "leaderboard.json").read_text(encoding="utf-8"))
    models = [m["id"] for m in sorted(lb["models"], key=lambda m: m["rank"])]
    lead = models[0]
    corpus = {r["id"]: r for r in rows(FIX / "corpus.jsonl")}
    members = {r["qid"]: r["members"] for r in rows(FIX / "candidates.jsonl") if r.get("op") == "decide_group"}

    items = {}  # qid -> {"gold", "stratum", "n", "pred": {model: class}}
    for m in models:
        for r in rows(RES / f"predictions/{m}.jsonl.gz"):
            if r["op"] != "decide_group" or r.get("stratum") not in ("real", "decoy"):
                continue
            it = items.setdefault(r["qid"], {"gold": r["gold"], "stratum": r["stratum"], "n": len(r["members"]), "pred": {}})
            assert it["gold"] == r["gold"] and r["members"] == members[r["qid"]]
            it["pred"][m] = r["pred"]
    assert len(items) == 1000 and all(len(it["pred"]) == len(models) for it in items.values())
    out = {"models": models, "items": len(items)}

    # 1. Each model's answers by outcome, and its group MCC as published and with k dropped.
    per_model = {}
    for m in models:
        c = collections.Counter(outcome(it["gold"], it["pred"][m], it["n"]) for it in items.values())
        pairs = [(it["gold"], it["pred"][m]) for it in items.values()]
        published = lb["models"][[x["id"] for x in lb["models"]].index(m)]["metrics"]["group"]["point"]
        per_model[m] = {"outcomes": dict(sorted(c.items())), "mcc": round(mcc(pairs), 6),
                        "mcc_kind_only": round(mcc([(kind(a), kind(b)) for a, b in pairs]), 6)}
        assert abs(per_model[m]["mcc"] - published) < 5e-6, (m, per_model[m]["mcc"], published)
    out["per_model"] = per_model
    pooled = collections.Counter()
    for v in per_model.values():
        pooled.update(v["outcomes"])
    errors = sum(v for k, v in pooled.items() if k != "correct")
    out["pooled_outcomes"] = dict(sorted(pooled.items()))
    out["pooled_errors"] = errors

    # 2. Accuracy by gold kind (decoys are the gold "none" rows) and by member count.
    def acc(qids, who):
        return round(sum(items[q]["pred"][w] == items[q]["gold"] for q in qids for w in who) / (len(qids) * len(who)), 4)

    by_kind = collections.defaultdict(list)
    for q, it in items.items():
        by_kind[kind(it["gold"])].append(q)
    out["by_gold_kind"] = {k: {"items": len(v), "pooled_acc": acc(v, models), "leader_acc": acc(v, [lead])}
                           for k, v in sorted(by_kind.items(), key=lambda kv: -len(kv[1]))}
    by_n = collections.defaultdict(list)
    for q, it in items.items():
        if it["stratum"] == "real":
            by_n[it["n"]].append(q)
    out["real_by_member_count"] = {str(n): {"items": len(v), "pooled_acc": acc(v, models), "leader_acc": acc(v, [lead])}
                                   for n, v in sorted(by_n.items())}

    # 3. Items no model gets right.
    nobody = sorted(q for q, it in items.items() if all(p != it["gold"] for p in it["pred"].values()))
    out["no_model_correct"] = {"items": len(nobody),
                               "by_gold": dict(sorted(collections.Counter(f"{kind(items[q]['gold'])}/{items[q]['stratum']}"
                                                                          for q in nobody).items()))}

    # 4. Items whose gold needs a k or a chain: what the models answer.
    kq = [q for q, it in items.items() if "_k" in it["gold"] or "ordered_chain" in it["gold"]]
    c = collections.Counter()
    for q in kq:
        g = items[q]["gold"]
        for p in items[q]["pred"].values():
            c["correct" if p == g else "none" if p == "none" else "right_kind_wrong_k" if kind(p) == kind(g)
              else "other_kind"] += 1
    out["count_or_chain_items"] = {"items": len(kq), "answers": share(c, len(kq) * len(models))}

    # 5. Decoys (gold none): what the leader claims.
    dq = [q for q, it in items.items() if it["stratum"] == "decoy"]
    out["decoys"] = {"items": len(dq), "leader_answers": dict(collections.Counter(kind(items[q]["pred"][lead])
                                                                                    for q in dq).most_common())}

    # 6. exactly_one items: a card's seed group (the members' teams are exactly the teams of a card line "Group G (...): A, B,
    # C. Exactly one of them is the seed") against derived ones. A member's team is the one card team its text names.
    def seed_group(q):
        card = corpus[members[q][0]]["context"]
        teams = re.search(r"Teams: ([^.\n]*)\.", card).group(1).split(", ")
        named = [[t for t in teams if re.search(rf"\b{re.escape(t)}\b", corpus[i]["text"])] for i in members[q]]
        if any(len(t) != 1 for t in named):
            return False
        groups = re.findall(r"Group \S+ \([^)]*\): ([^.]*)\. Exactly one of them is the seed", card)
        return {t[0] for t in named} in [set(g.split(", ")) for g in groups]

    ex = [q for q, it in items.items() if it["gold"] == "exactly_one"]
    out["exactly_one"] = {name: {"items": len(s), "leader_correct": sum(items[q]["pred"][lead] == "exactly_one" for q in s),
                                 "leader_answers": dict(collections.Counter(items[q]["pred"][lead] for q in s).most_common())}
                          for name, s in (("seed_groups", [q for q in ex if seed_group(q)]),
                                          ("derived", [q for q in ex if not seed_group(q)]))}

    # 7. Group MCC on the decoys with the small (2-3 member) or the large (4-8 member) real items.
    def subset_mcc(m, keep):
        return round(mcc([(it["gold"], it["pred"][m]) for it in items.values() if it["stratum"] == "decoy" or keep(it["n"])]), 6)

    out["mcc_by_set_size"] = {m: {"decoys_and_n_2_3": subset_mcc(m, lambda n: n <= 3),
                                  "decoys_and_n_4_8": subset_mcc(m, lambda n: n >= 4)} for m in models}

    path = Path(__file__).with_name("results.json")
    path.write_text(json.dumps(out, indent=1, sort_keys=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()

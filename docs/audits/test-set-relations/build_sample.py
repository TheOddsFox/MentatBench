"""Blind answerability sample of Mentat-Hard v3.1 (child v6, league rule cards): writes sample.jsonl, key.jsonl and sources.json
into this directory.

usage: python -B build_sample.py --dev <dev fixture dir> --heldout <held-out test-split fixture dir>

The prompt of each item is the wire text of the System One child v6. Each sampled item goes through the real child
(children.protocol.handle, which validates it, then SystemOneChild.decide) to the FakeOllama of tests/test_systemone_child.py
on 127.0.0.1. The state (with the league rule card of each outcome asked about), the instructions and the options of each
request the child sends are rendered as the prompt. No real server is contacted.
Read-only on the repo: bytecode is not written, and only this directory is written.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

WORKTREE = Path(__file__).resolve().parents[3]  # the repository root
OUT = Path(__file__).resolve().parent
SEED = "audit-v3.1:2026-10-30"
CLASSES = ("EQUIV", "IMPLIES", "IMPLIED_BY", "EXCLUDES", "NONE")
PER_CLASS = 6
DEEP_CLASSES = ("IMPLIES", "IMPLIED_BY", "EXCLUDES")
DEEP_PER_CLASS = 10
GROUP_PER_STRATUM = 15
BINDING_PER_STATUS = 15
READING_PER_STRATUM = 30
DEV_FAMILIES = 20  # families 1-20: dev wording; 21-30: held-out wording, on the test split only (fixtures_hard)
DEV_SEED = 20261027
REGISTERED_TEST_SEED = 20261028  # the test split's registered seed: the held-out build must not use it
CANONICAL_KIND_ORDER = ("exactly_one", "ordered_chain", "at_least_k", "at_most_k", "at_most_one")
NEUTRAL = ("Answer all of these together: the kind (one of the options above); the lower bound, the fewest of the {n} outcomes "
           "that must hold (0 to {n}); the upper bound, the most of them that can hold (0 to {n}, where {n} means no limit); "
           "and, if the kind is ordered_chain, the outcome numbers in chain order, strongest first.")

sys.dont_write_bytecode = True
sys.path[:0] = [str(WORKTREE / "python"), str(WORKTREE / "tests")]

from test_systemone_child import DIGEST, TAG, VERSION, FakeOllama  # noqa: E402

from mentat_bench import corrections as CORR  # noqa: E402
from mentat_bench.children import systemone_child as S1  # noqa: E402
from mentat_bench.children.protocol import handle  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:  # LF on Windows too: the prompts stay byte-exact
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def canonical_kind(terms: list) -> str:
    """The suite's canonical label from the terms (base.group_terms order): exactly_one > ordered_chain > at_least_k >
    at_most_k > at_most_one; 'none' for no terms. Checked against gold/suite_labels.jsonl for every curated group."""
    kinds = [kind for kind, _ in terms]
    if not kinds:
        return "none"
    return next(k for k in CANONICAL_KIND_ORDER if k in kinds)


def ctx_of(corpus: dict[str, dict], oid: str) -> dict:
    """The child's outcome context, as harness.fixed._context builds it from a corpus record (question = text; the league
    rule card as context when the record has one)."""
    r = corpus[oid]
    ctx = {"id": r["id"], "question": r["text"], "label": r["label"], "event_title": r.get("event_title")}
    if r.get("context") is not None:
        ctx["context"] = r["context"]
    return ctx


def source(path: Path) -> dict[str, Any]:
    """A fixture directory's identity: its fixture.json fields and its content hash (corrections.content_hash)."""
    fx = json.loads((path / "fixture.json").read_text(encoding="utf-8"))
    return {"path": str(path), "name": fx["name"], "split": fx["split"], "seed": fx["seed"], "version": fx["version"],
            "content_hash": CORR.content_hash(CORR.content_hashes(path))}


def load(path: Path) -> dict[str, Any]:
    """The rows of a fixture the sample uses. family maps an outcome id to its rendering family (gold/meta.jsonl)."""
    return {"corpus": {r["id"]: r for r in read_jsonl(path / "corpus.jsonl")},
            "labels": {r["qid"]: r for r in read_jsonl(path / "gold" / "suite_labels.jsonl")},
            "cands": read_jsonl(path / "candidates.jsonl"),
            "family": {r["id"]: r["family"] for r in read_jsonl(path / "gold" / "meta.jsonl")}}


def render(request: dict, name: str) -> str:
    """The prompt text of one question: the state the child sent (with its rule card), the question's instructions, then
    its options menu."""
    assert list(request["questions"]) == [name], (name, list(request["questions"]))
    q = request["questions"][name]
    menu = "\n".join(f"{key}: {text}" for key, text in q["criteria"].items())
    return f"{request['state']}\n\n{q['instructions']}\nOptions:\n{menu}"


def expect_state(text: str, want: str) -> None:
    assert text == want, "wire state differs from the state the child builds from the context"


def pools(fx: dict[str, Any], build: str) -> dict[str, Any]:
    """The candidate pools of one fixture. Relations: the dev build's rows are all dev wording; the held-out build's rows
    are those with at least one outcome of a held-out family. Groups, bindings and readings come from the dev build only."""
    corpus, labels, family = fx["corpus"], fx["labels"], fx["family"]
    rel_pool: dict[str, list[dict]] = {c: [] for c in CLASSES}
    rel_excluded = 0
    group_pool: dict[str, list[dict]] = {"real": [], "decoy": []}
    bind_pool: dict[str, list[dict]] = {"bound": [], "unresolved": []}
    read_pool: dict[str, list[dict]] = {"reading": [], "reading_decoy": []}
    for row in fx["cands"]:
        lab = labels[row["qid"]]
        if row["op"] == "decide_relation":
            fams = [family[row["a"]], family[row["b"]]]
            if (max(fams) > DEV_FAMILIES) != (build == "heldout"):
                continue  # dev build: dev wording only; held-out build: at least one held-out family
            a, b = ctx_of(corpus, row["a"]), ctx_of(corpus, row["b"])
            if len(S1._relation_question(a, b)["instructions"]) > S1.MAX_INSTRUCTION_CHARS:
                rel_excluded += 1  # the child refuses these as an item error: never sent, so no prompt exists
                continue
            rel_pool[lab["label"]].append({"qid": row["qid"], "a": a, "b": b, "suite": lab["stratum"], "families": fams,
                                           "depth": lab.get("depth")})
        elif build != "dev":
            continue
        elif row["op"] == "decide_binding":
            bind_pool[lab["label"]].append({"qid": row["qid"], "ctx": ctx_of(corpus, row["id"])})
        elif row["op"] == "decide_group" and row["source"] == "curated":
            terms = [list(t) for t in lab["terms"]]
            assert (lab["stratum"] == "real") == bool(terms), row["qid"]  # real: terms non-empty; decoy: terms []
            assert canonical_kind(terms) == lab["label"], row["qid"]
            members = [ctx_of(corpus, m) for m in row["members"]]
            group_pool[lab["stratum"]].append({"qid": row["qid"], "members": members, "terms": terms, "kind": lab["label"]})
        elif row["op"] == "decide_group" and row["source"] == "reading":
            assert len(row["members"]) == 5, row["qid"]
            members = row["members"]
            picks = {}
            for reading in ("base", "alt"):
                oid = lab["readings"][reading]
                picks[reading] = "none" if oid is None else S1.ORDER_LETTERS[members[1:].index(oid)]  # ValueError if absent
            read_pool[lab["stratum"]].append({"qid": row["qid"], "u": ctx_of(corpus, members[0]),
                                              "options": [ctx_of(corpus, m) for m in members[1:]], "gold": picks})
    if build == "dev":
        assert all(max(x["families"]) <= DEV_FAMILIES for c in CLASSES for x in rel_pool[c])
    return {"relation": rel_pool, "relation_excluded": rel_excluded, "group": group_pool, "binding": bind_pool,
            "reading": read_pool}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dev", required=True, type=Path, help="the dev fixture directory (split dev, version 3)")
    ap.add_argument("--heldout", required=True, type=Path, help="the held-out fixture directory (split test, fresh seed)")
    args = ap.parse_args()
    dev_src, held_src = source(args.dev), source(args.heldout)
    assert dev_src["split"] == "dev" and dev_src["seed"] == DEV_SEED and dev_src["version"] == "3.1", dev_src
    assert held_src["split"] == "test" and held_src["seed"] != REGISTERED_TEST_SEED and held_src["version"] == "3.1", held_src

    dev = pools(load(args.dev), "dev")
    held = pools(load(args.heldout), "heldout")
    counts_pool = {"relation_dev": {c: len(v) for c, v in dev["relation"].items()},
                   "relation_heldout": {c: len(v) for c, v in held["relation"].items()},
                   "group": {k: len(v) for k, v in dev["group"].items()},
                   "binding": {k: len(v) for k, v in dev["binding"].items()},
                   "reading": {k: len(v) for k, v in dev["reading"].items()}}
    print("pools", json.dumps(counts_pool), "relation rows excluded (instructions over the limit): dev",
          dev["relation_excluded"], "held-out", held["relation_excluded"])

    # Sampling: one generator, seeded with SEED; then one shuffle across the types.
    rng = random.Random(SEED)
    picked: list[tuple[str, str, str, dict]] = []  # (type, stratum, build, item)
    for cls in CLASSES:
        picked += [("relation", cls, "dev", x) for x in rng.sample(dev["relation"][cls], PER_CLASS)]
    # v3.1: the derived links at depth 3 or more, 10 per entailment class, apart from the rows drawn above.
    taken = {x["qid"] for kind, _, build, x in picked if kind == "relation"}
    for cls in DEEP_CLASSES:
        deep = [x for x in dev["relation"][cls] if isinstance(x["depth"], int) and x["depth"] >= 3 and x["qid"] not in taken]
        picked += [("relation", cls, "deep", x) for x in rng.sample(deep, DEEP_PER_CLASS)]
    for stratum in ("real", "decoy"):
        picked += [("group", stratum, "dev", x) for x in rng.sample(dev["group"][stratum], GROUP_PER_STRATUM)]
    for status in ("bound", "unresolved"):
        picked += [("binding", status, "dev", x) for x in rng.sample(dev["binding"][status], BINDING_PER_STATUS)]
    for stratum in ("reading", "reading_decoy"):
        picked += [("reading", stratum, "dev", x) for x in rng.sample(dev["reading"][stratum], READING_PER_STRATUM)]
    for cls in CLASSES:
        picked += [("relation", cls, "heldout", x) for x in rng.sample(held["relation"][cls], PER_CLASS)]
    rng.shuffle(picked)

    # The real child, over the fake server: one request per question, the requests recorded by the fake.
    fake = FakeOllama()
    child = S1.SystemOneChild(TAG, fake.url, DIGEST, VERSION, parallel=1, timeout=10.0)
    sample: list[dict] = []
    key: list[dict] = []
    try:
        for index, (kind, stratum, build, x) in enumerate(picked, start=1):
            item_id = f"A{index:03d}"
            fake.requests.clear()
            if kind == "relation":
                a, b = x["a"], x["b"]
                reply = handle(child, json.dumps({"op": "decide_relation", "a": a, "b": b}))
                reqs = list(fake.requests)
                assert "error" not in reply and [list(r["questions"]) for r in reqs] == [["relation"]], reqs
                expect_state(reqs[0]["state"], S1._frame(S1._rules([a, b]) + S1._prop("A", a)))
                q = reqs[0]["questions"]["relation"]
                assert q["instructions"] == S1.T["relation_lead"].format(proposition=S1._prop("B", b)) + \
                    S1.PROMPTS["relation"]["instructions"]
                assert list(q["criteria"]) == list(S1.relation_order(a, b)) and set(q["criteria"]) == set(S1.RELATION_CHOICES)
                sample.append({"id": item_id, "type": "relation", "prompt": render(reqs[0], "relation"),
                               "answer_format": '{"relation": "EQUIV" | "IMPLIES" | "IMPLIED_BY" | "EXCLUDES" | "NONE"}'})
                key.append({"id": item_id, "qid": x["qid"], "type": "relation",
                            "part": {"dev": "relations", "deep": "deep_relations"}.get(build, "heldout_relations"),
                            "build": "dev" if build == "deep" else build, "stratum": stratum, "suite_stratum": x["suite"], "families": x["families"],
                            "depth": x["depth"], "gold": stratum})
            elif kind == "binding":
                ctx = x["ctx"]
                reply = handle(child, json.dumps({"op": "decide_binding", "ctx": ctx}))
                reqs = list(fake.requests)
                assert "error" not in reply and [list(r["questions"]) for r in reqs][:1] == [["status"]], reqs
                expect_state(reqs[0]["state"], S1._frame(S1._record(ctx)))
                assert reqs[0]["questions"]["status"]["instructions"] == S1.PROMPTS["status"]["instructions"]
                sample.append({"id": item_id, "type": "binding", "prompt": render(reqs[0], "status"),
                               "answer_format": '{"status": "bound" | "unresolved"}'})
                key.append({"id": item_id, "qid": x["qid"], "type": "binding", "part": "bindings", "build": build,
                            "stratum": stratum, "gold": stratum})
            elif kind == "group":
                members = x["members"]
                n = len(members)
                ctx = {"group_id": x["qid"], "source": "curated", "members": members}
                reply = handle(child, json.dumps({"op": "decide_group", "ctx": ctx}))
                reqs = list(fake.requests)
                assert "error" not in reply and [list(r["questions"]) for r in reqs][:1] == [["kind"]], reqs
                expect_state(reqs[0]["state"], S1._frame(S1._rules(members) + S1.T["group_state"].format(
                    listing=S1._listing(members))))
                assert list(reqs[0]["questions"]["kind"]["criteria"]) == list(S1.GROUP_CHOICES)
                prompt = render(reqs[0], "kind") + "\n\n" + NEUTRAL.format(n=n)
                fmt = ('{"kind": "none" | "exactly_one" | "at_most_one" | "at_least_k" | "at_most_k" | "ordered_chain", '
                       f'"lower": integer 0 to {n}, "upper": integer 0 to {n}, '
                       '"chain": [outcome numbers, strongest first; [] unless the kind is ordered_chain]}')
                sample.append({"id": item_id, "type": "group", "prompt": prompt, "answer_format": fmt})
                key.append({"id": item_id, "qid": x["qid"], "type": "group", "part": "groups", "build": build,
                            "stratum": stratum, "gold": x["terms"], "kind": x["kind"], "n": n})
            else:  # reading: the base and the alt question of one row, both asked with the rule card of U
                u, options = x["u"], x["options"]
                ctx = {"group_id": x["qid"], "source": "reading", "members": [u] + options}
                reply = handle(child, json.dumps({"op": "decide_group", "ctx": ctx}))
                reqs = list(fake.requests)
                assert "error" not in reply and [list(r["questions"]) for r in reqs] == [["base"], ["alt"]], reqs
                letters = S1.ORDER_LETTERS[:len(options)]
                listing = "\n".join(S1.T["letter"].format(letter=lt, question=m["question"], label=m["label"])
                                    for lt, m in zip(letters, options))
                expect_state(reqs[0]["state"], S1._frame(S1._rules([u]) + S1.T["reading_state"].format(
                    proposition=S1._prop("U", u), listing=listing)))
                expect_state(reqs[1]["state"], reqs[0]["state"])
                # the questions are named base and alt; with a rule card their instructions are the base_rules and alt_rules ones
                assert reqs[0]["questions"]["base"]["instructions"] == S1.PROMPTS["base_rules"]["instructions"]
                assert reqs[1]["questions"]["alt"]["instructions"] == S1.PROMPTS["alt_rules"]["instructions"]
                prompt = {"base": render(reqs[0], "base"), "alt": render(reqs[1], "alt")}
                sample.append({"id": item_id, "type": "reading", "prompt": prompt,
                               "answer_format": '{"base": "A" | "B" | "C" | "D" | "none", "alt": "A" | "B" | "C" | "D" | "none"}'})
                key.append({"id": item_id, "qid": x["qid"], "type": "reading",
                            "part": "readings_ambiguous" if stratum == "reading" else "readings_decoy", "build": build,
                            "stratum": stratum, "gold": x["gold"]})
    finally:
        fake.close()

    write_jsonl(OUT / "sample.jsonl", sample)
    write_jsonl(OUT / "key.jsonl", key)
    (OUT / "sources.json").write_text(json.dumps({"sample_seed": SEED, "dev": dev_src, "heldout": held_src}, indent=1,
                                                 sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    tally: dict[str, int] = {}
    for k in key:
        tally[k["part"]] = tally.get(k["part"], 0) + 1
    print("sample rows", len(sample), "key rows", len(key))
    print(json.dumps(tally, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()

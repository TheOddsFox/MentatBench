"""Deterministic generator for the controlled toy10k fixture (python -m mentat_bench.fixtures_gen).

Formal world. The gold theory's admissible models are the world:
- entities k = (season, team) carry an integer score X_k; threshold classes T(k,t) mean X_k >= t, t = 1..8.
  Axiom: T(k,t+1) => T(k,t).
- events (season, group) have four candidate entities. The winner is categorical, so exactly one candidate
  wins: exactly(1, [W(e,c)]). Axiom: W(e,c) => T(c,5).
- per (season, group): atleast(2, [T(k,5)]) and atmost(2, [T(k,6)]).
- dated propositions ("by the mid-season break") are unresolved (missing code no_date). Two exhaustive
  interpretations: "base" (dated <=> T(k,t)) and "alt" (dated <=> not T(k,t)). A dated Yes and its No are
  complementary in every interpretation.

Rendering. Each class is written with positive and negated question templates, so both polarities occur.
Markets are binary (Yes/No) except 12 four-outcome markets. Every record is controlled. Gold pairwise
relations are emitted only between distinct classes; same-class paraphrases are linked by bindings.

Identity and scale. Outcome IDs are unsalted SHA-256 prefixes of the market's semantic key. They are stable
across scales (so matched queries keep their IDs), but the key space is public, so anyone with this generator can
enumerate them. This toy is therefore not a hidden set; a hidden release needs a secret key kept outside the
repository. Row order is the hash order of the markets. Dated propositions are sampled per season, so adding
seasons never changes the content of an earlier season, and the queries (which target seasons 2025 and 2026
only) are identical at any scale.

Layout. Top level holds only public inputs: corpus.jsonl, queries.jsonl, fixture.json. Everything gold-bearing
is under gold/: the submission, answers.jsonl, verdicts.jsonl, query_design.jsonl (tags, premises) and
summary.json.

Pipeline. `generate` writes corpus, gold submission, queries and gold/query_design (premises unset) without the
engine. `finalize` runs mb-engine (validate, eval, check) and measures the material premises of each query by
ablation: a premise is material when dropping it changes the engine answer. It needs the mb-engine binary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import engine, ir

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "fixtures" / "toy10k"
SEED = 20261018
BASE_SEASONS = 4  # toy10k; larger scales add seasons (each ~2.5k outcomes)
DATED_PER_SEASON = 25  # 100 dated propositions at BASE_SEASONS
ADJ = ("Ardent", "Brisk", "Cobalt", "Dusky", "Ember", "Frosted", "Gilded", "Hollow")
NOUN = ("Falcons", "Harbor", "Lynx", "Meridian", "Quarry", "Summit")
TEAMS = tuple(f"{a} {n}" for a in ADJ for n in NOUN)
GROUP_LETTERS = "ABCDEFGHIJKL"
THRESHOLDS = tuple(range(1, 9))
POS_TEMPLATES = (
    "Will {team} score at least {t} points in the {season} season?",
    "Does {team} finish the {season} season with {t} or more points?",
)
NEG_TEMPLATES = ("Will {team} score fewer than {t} points in the {season} season?",)
WIN_TEMPLATE = "Will {team} win the {season} Group {group} tournament?"
NOT_WIN_TEMPLATE = "Will {team} fail to win the {season} Group {group} tournament?"
MULTI_TEMPLATE = "Which team wins the {season} Group {group} tournament?"
DATED_TEMPLATE = "Will {team} score at least {t} points by the {season} mid-season break?"
MULTI_GROUPS = (0, 4, 8)
GOLD_INTERPRETATIONS = ["base", "alt"]
ORDER = ("ENTAILED", "REFUTED", "CONTINGENT", "UNRESOLVED")


def season(si: int) -> str:
    return str(2023 + si)


def thr_class(si: int, i: int, t: int) -> str:
    return f"pts_{season(si)}_t{i:02d}_ge{t}"


def win_class(si: int, i: int) -> str:
    return f"win_{season(si)}_g{GROUP_LETTERS[i // 4]}_t{i:02d}"


def opaque(*key: Any) -> str:
    """16 hex digits: unsalted SHA-256 of a semantic key. Stable across scales; enumerable (see the module docstring)."""
    return hashlib.sha256("|".join(map(str, key)).encode("utf-8")).hexdigest()[:16]


def _clause_relation(n1: bool, n2: bool, sx: bool, sy: bool) -> str | None:
    """Relation of records with meanings cx^n1 and cy^n2 forced by the clause (cx^sx) => (cy^sy).
    OR means the pair is a disjunction (not pairwise-expressible; carried as an `or` constraint)."""
    admissible = {
        (a ^ int(n1), b ^ int(n2))
        for a in (0, 1)
        for b in (0, 1)
        if not ((a ^ int(sx)) == 1 and (b ^ int(sy)) == 0)
    }
    forbidden = {(x, y) for x in (0, 1) for y in (0, 1)} - admissible
    if not forbidden:
        return None
    if forbidden == {(1, 0)}:
        return "IMPLIES"
    if forbidden == {(0, 1)}:
        return "IMPLIED_BY"
    if forbidden == {(1, 1)}:
        return "EXCLUDES"
    if forbidden == {(0, 0)}:
        return "OR"
    if forbidden == {(1, 0), (0, 1)}:
        return "EQUIV"
    raise ValueError(f"clause relation not representable: {sorted(forbidden)}")


def _pair_expr(rel: str, a: str, b: str) -> dict[str, Any]:
    if rel == "IMPLIES":
        return {"op": "implies", "a": ir.var(a), "b": ir.var(b)}
    if rel == "IMPLIED_BY":
        return {"op": "implies", "a": ir.var(b), "b": ir.var(a)}
    if rel == "EXCLUDES":
        return {"op": "atmost", "k": 1, "args": [ir.var(a), ir.var(b)]}
    return {"op": "iff", "a": ir.var(a), "b": ir.var(b)}


def _dated_keys(seed: int, seasons: int) -> set[tuple[int, int, int]]:
    """(season, entity, threshold) of dated propositions, drawn per season from that season's pool only."""
    pool = [(i, t) for i in range(len(TEAMS)) for t in THRESHOLDS]
    keys: set[tuple[int, int, int]] = set()
    for si in range(seasons):
        for i, t in random.Random(f"{seed}:dated:{si}").sample(pool, DATED_PER_SEASON):
            keys.add((si, i, t))
    return keys


def _market(key: tuple, question: str, title: str, kind: str, outcomes: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    return {"mid": opaque(*key), "question": question, "title": title, "kind": kind, "meta": meta, "outcomes": outcomes}


def _binary(key: tuple, question: str, title: str, kind: str, cls: str | None, negated: bool, meta: dict[str, Any]) -> dict[str, Any]:
    if cls is None:
        outcomes = [{"label": "Yes", "cls": None, "neg": False}, {"label": "No", "cls": None, "neg": False}]
    else:
        outcomes = [{"label": "Yes", "cls": cls, "neg": negated}, {"label": "No", "cls": cls, "neg": not negated}]
    return _market(key, question, title, kind, outcomes, meta)


def _markets(seed: int, seasons: int) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    for si in range(seasons):
        s = season(si)
        for i, team in enumerate(TEAMS):
            group = GROUP_LETTERS[i // 4]
            for t in THRESHOLDS:
                cls = thr_class(si, i, t)
                title = f"{s} points race"
                for v, tmpl in enumerate(POS_TEMPLATES):
                    meta = {"variant": f"pos{v}", "si": si, "i": i, "t": t}
                    markets.append(_binary(("thr", si, i, t, f"pos{v}"), tmpl.format(team=team, t=t, season=s), title, "thr", cls, False, meta))
                for v, tmpl in enumerate(NEG_TEMPLATES):
                    meta = {"variant": f"neg{v}", "si": si, "i": i, "t": t}
                    markets.append(_binary(("thr", si, i, t, f"neg{v}"), tmpl.format(team=team, t=t, season=s), title, "thr", cls, True, meta))
            title = f"{s} Group {group} tournament"
            markets.append(_binary(("win", si, i, "pos"), WIN_TEMPLATE.format(team=team, season=s, group=group), title, "win", win_class(si, i), False, {"variant": "pos", "si": si, "i": i, "t": 0}))
            markets.append(_binary(("win", si, i, "neg"), NOT_WIN_TEMPLATE.format(team=team, season=s, group=group), title, "win", win_class(si, i), True, {"variant": "neg", "si": si, "i": i, "t": 0}))
    for si in range(seasons):
        s = season(si)
        for g in MULTI_GROUPS:
            members = [4 * g + c for c in range(4)]
            markets.append(_market(
                ("multi", si, g),
                MULTI_TEMPLATE.format(season=s, group=GROUP_LETTERS[g]),
                f"{s} Group {GROUP_LETTERS[g]} tournament",
                "multi",
                [{"label": TEAMS[i], "cls": win_class(si, i), "neg": False} for i in members],
                {"variant": "multi", "si": si, "i": members[0], "t": 0},
            ))
    for si, i, t in sorted(_dated_keys(seed, seasons)):
        meta = {"variant": "dated", "si": si, "i": i, "t": t}
        markets.append(_binary(("dated", si, i, t), DATED_TEMPLATE.format(team=TEAMS[i], t=t, season=season(si)),
                               f"{season(si)} mid-season standings", "dated", None, False, meta))
    markets.sort(key=lambda m: m["mid"])  # row order: hash order, no gold structure
    if len({m["mid"] for m in markets}) != len(markets):
        raise ValueError("market ID collision")
    return markets


def _records(seed: int, seasons: int = BASE_SEASONS) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for market in _markets(seed, seasons):
        meta = market["meta"]
        for idx, out in enumerate(market["outcomes"], start=1):
            recs.append({
                "id": f"toy:{market['mid']}:{idx}",
                "question": market["question"],
                "label": out["label"],
                "event_title": market["title"],
                "cls": out["cls"],
                "neg": bool(out["neg"]),
                "kind": market["kind"],
                "si": meta["si"],
                "i": meta["i"],
                "t": meta["t"],
                "variant": meta["variant"],
            })
    if len({r["id"] for r in recs}) != len(recs):
        raise ValueError("outcome ID collision")
    return recs


def _gold_constraints(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_cls: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_tag: dict[tuple, dict[str, Any]] = {}
    for r in recs:
        if r["cls"] is not None:
            by_cls[r["cls"]].append(r)
        by_tag[(r["kind"], r["si"], r["i"], r["t"], r["variant"], r["label"])] = r

    constraints: list[dict[str, Any]] = []

    def add(expr: dict[str, Any], interps: list[str] | None = None) -> None:
        constraints.append({"id": f"g{len(constraints):06d}", "expr": expr, "interps": interps})

    def clause(cls_x: str, cls_y: str) -> None:
        for r1 in by_cls[cls_x]:
            for r2 in by_cls[cls_y]:
                rel = _clause_relation(r1["neg"], r2["neg"], False, False)
                if rel == "OR":
                    add({"op": "or", "args": [ir.var(r1["id"]), ir.var(r2["id"])]})
                elif rel is not None:
                    add(_pair_expr(rel, r1["id"], r2["id"]))

    seasons = 1 + max(r["si"] for r in recs)
    for si in range(seasons):
        for i in range(len(TEAMS)):
            for t in range(1, max(THRESHOLDS)):
                clause(thr_class(si, i, t + 1), thr_class(si, i, t))
            clause(win_class(si, i), thr_class(si, i, 5))

    yes = lambda kind, si, i, t, variant: ir.var(by_tag[(kind, si, i, t, variant, "Yes")]["id"])  # noqa: E731
    for si in range(seasons):
        for g in range(len(TEAMS) // 4):
            members = [4 * g + c for c in range(4)]
            add({"op": "exactly", "k": 1, "args": [yes("win", si, i, 0, "pos") for i in members]})
            add({"op": "atleast", "k": 2, "args": [yes("thr", si, i, 5, "pos0") for i in members]})
            add({"op": "atmost", "k": 2, "args": [yes("thr", si, i, 6, "pos0") for i in members]})
    for r in recs:
        if r["kind"] == "dated" and r["label"] == "Yes":
            no = by_tag[("dated", r["si"], r["i"], r["t"], "dated", "No")]
            add({"op": "iff", "a": ir.var(r["id"]), "b": ir.neg(ir.var(no["id"]))})
            base = yes("thr", r["si"], r["i"], r["t"], "pos0")
            alt = ir.var(by_tag[("thr", r["si"], r["i"], r["t"], "pos0", "No")]["id"])
            add({"op": "iff", "a": ir.var(r["id"]), "b": base}, ["base"])
            add({"op": "iff", "a": ir.var(r["id"]), "b": alt}, ["alt"])
    return constraints


def _binding_row(r: dict[str, Any]) -> dict[str, Any]:
    if r["cls"] is None:
        return {"id": r["id"], "status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}
    return {"id": r["id"], "status": "bound", "class": r["cls"], "negated": r["neg"], "missing": []}


def _queries(recs: list[dict[str, Any]], seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(seed)
    by_cls: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in recs:
        if r["cls"] is not None:
            by_cls[r["cls"]].append(r)
    ids_of_entity: dict[tuple[int, int], list[str]] = defaultdict(list)
    for r in recs:
        if r["kind"] in ("thr", "win"):
            ids_of_entity[(r["si"], r["i"])].append(r["id"])

    def lit(cls: str, sign: int) -> dict[str, Any]:
        """Variable for a record whose meaning is cls (sign 0) or not cls (sign 1); random among candidates."""
        options = sorted((r for r in by_cls[cls] if r["neg"] == bool(sign)), key=lambda r: r["id"])
        return ir.var(rng.choice(options)["id"])

    def thr(si: int, i: int, t: int, sign: int = 0) -> dict[str, Any]:
        return lit(thr_class(si, i, t), sign)

    def win(si: int, i: int, sign: int = 0) -> dict[str, Any]:
        return lit(win_class(si, i), sign)

    # Anchors use only base-season dated propositions, so they do not depend on the scale.
    dated_yes = sorted((r for r in recs if r["kind"] == "dated" and r["label"] == "Yes" and r["si"] < BASE_SEASONS),
                       key=lambda r: r["id"])
    dated5 = [r for r in dated_yes if r["t"] == 5]
    if len(dated5) < 2:
        raise ValueError("fixture needs at least two dated threshold-5 propositions")
    da, db = dated5[0], dated5[1]
    dc = next(r for r in dated_yes if r["id"] not in {da["id"], db["id"]})
    dv = lambda r: ir.var(r["id"])  # noqa: E731
    neg = ir.neg
    teams = lambda g: [4 * g + c for c in range(4)]  # noqa: E731

    queries: list[dict[str, Any]] = []
    design: list[dict[str, Any]] = []

    def add(qid: str, family: str, tags: list[str], **body: Any) -> None:
        queries.append({"qid": qid, "family": family, **body})
        design.append({"qid": qid, "family": family, "premises": None, "tags": tags, "anchor": True})

    # Closure: joint hypotheses over the compiled theory (every hypothesis is satisfiable in every interpretation).
    hyp = [thr(3, i, 5, 0) for i in teams(0)] + [thr(3, i, 6, 1) for i in teams(0)]
    add("closure-01", "closure", ["joint", "group_atleast_atmost"], hyp=hyp)
    hyp = [win(3, 0, 0), thr(3, 4, 5, 1), thr(3, 8, 2, 0), thr(3, 9, 4, 1), thr(3, 10, 1, 0), thr(3, 12, 7, 1), thr(3, 13, 2, 0), thr(3, 14, 3, 1)]
    add("closure-02", "closure", ["winner_axiom", "joint", "chain"], hyp=hyp)
    hyp = [win(3, 8, 1), win(3, 9, 1), win(3, 10, 1), thr(3, 11, 8, 1), thr(3, 12, 3, 0), thr(3, 13, 3, 0), thr(3, 14, 3, 0), thr(3, 15, 3, 0)]
    add("closure-03", "closure", ["exactly_one_entailment", "joint"], hyp=hyp)
    add("closure-04", "closure", ["unresolved_asserted", "interpretation_split"], hyp=[dv(da)])
    add("closure-05", "closure", ["unresolved_negated"], hyp=[neg(dv(db)), neg(dv(dc))])
    hyp = [thr(3, 0, 4, 0), thr(3, 1, 2, 0), thr(3, 2, 6, 1), thr(3, 3, 5, 0), win(3, 1, 1), win(3, 2, 1), thr(3, 0, 7, 1), thr(3, 3, 8, 1)]
    universe = sorted(i for e in (0, 1, 2, 3) for i in ids_of_entity[(3, e)])
    add("closure-06", "closure", ["universe_subset", "joint"], hyp=hyp, universe=universe)
    add("closure-07", "closure", ["no_hypothesis"], hyp=[{"op": "true"}])
    hyp = [thr(2, i, 2, 0) for i in (16, 17, 18, 19)] + [thr(3, i, 8, 1) for i in (20, 21, 22, 23)]
    add("closure-08", "closure", ["joint", "chain"], hyp=hyp)
    hyp = [win(3, 24, 0), thr(3, 24, 6, 1), thr(3, 25, 5, 0), thr(3, 26, 4, 0), win(2, 0, 1), thr(3, 27, 2, 1), thr(2, 4, 3, 0), thr(2, 5, 6, 1)]
    add("closure-09", "closure", ["winner_axiom", "mixed_seasons"], hyp=hyp)
    hyp = [thr(3, 28, 3, 1), thr(3, 29, 3, 1), thr(3, 32, 2, 0), thr(3, 33, 6, 1), thr(3, 34, 4, 0)] + [thr(2, i, 2, 0) for i in range(3)]
    add("closure-10", "closure", ["joint", "negated_threshold"], hyp=hyp)

    # Selection: relations against a target expression over bound (resolved) propositions only.
    add("selection-01", "selection", ["nonempty", "equiv"], relation="equiv", target=thr(3, 0, 5, 0))
    add("selection-02", "selection", ["nonempty", "equiv", "winner"], relation="equiv", target=win(3, 4, 0))
    add("selection-03", "selection", ["nonempty", "implies"], relation="implies", target=thr(3, 1, 4, 0))
    add("selection-04", "selection", ["nonempty", "implied_by", "winner_axiom"], relation="implied_by", target=win(3, 5, 0))
    add("selection-05", "selection", ["nonempty", "excludes", "winner"], relation="excludes", target=win(3, 6, 0))
    add("selection-06", "selection", ["nonempty", "implies", "join"], relation="implies",
        target=ir.disj(thr(3, 7, 5, 0), thr(3, 8, 5, 0)))
    add("selection-07", "selection", ["nonempty", "implies", "join", "winner"], relation="implies",
        target=ir.disj(win(3, 8, 0), win(3, 9, 0)))
    add("selection-08", "selection", ["nonempty", "implies", "conjunction_target"], relation="implies",
        target=ir.conj(win(3, 16, 0), thr(3, 16, 5, 0)))
    add("selection-09", "selection", ["nonempty", "excludes"], relation="excludes", target=thr(3, 10, 2, 1))
    add("selection-10", "selection", ["nonempty", "implies", "universe_subset"], relation="implies",
        target=thr(3, 11, 6, 0), universe=sorted(ids_of_entity[(3, 11)]))

    # Feasibility: 4 SAT, 4 UNSAT, 2 UNRESOLVED (the 2:2:1 mixture of the spec, within 10 queries).
    add("feasibility-01", "feasibility", ["sat", "monotone_chain"], assume=[thr(3, 0, 5, 0), thr(3, 0, 3, 0)])
    add("feasibility-02", "feasibility", ["pairwise_unsat", "chain"], assume=[thr(3, 1, 5, 0), thr(3, 1, 4, 1)])
    add("feasibility-03", "feasibility", ["jointly_unsat_pairwise_sat", "exactly_one"], assume=[win(3, i, 1) for i in teams(1)])
    add("feasibility-04", "feasibility", ["jointly_unsat_pairwise_sat", "atleast_group"], assume=[thr(3, i, 5, 1) for i in (8, 9, 10)])
    add("feasibility-05", "feasibility", ["sat", "atmost_group"], assume=[thr(2, 12, 6, 0), thr(2, 13, 6, 0), thr(2, 14, 5, 1)])
    add("feasibility-06", "feasibility", ["unresolved", "missing_no_date"], assume=[dv(da), thr(da["si"], da["i"], 5, 0)])
    add("feasibility-07", "feasibility", ["unresolved", "missing_no_date"], assume=[dv(db), thr(db["si"], db["i"], 5, 1)])
    add("feasibility-08", "feasibility", ["sat", "multi_group"],
        assume=[win(3, 0, 0), win(3, 4, 0), thr(3, 0, 5, 0), thr(3, 1, 3, 0), thr(3, 2, 3, 0), thr(3, 3, 1, 0), win(3, 1, 1), win(3, 5, 1)])
    add("feasibility-09", "feasibility", ["pairwise_unsat", "winner_axiom"], assume=[win(3, 12, 0), thr(3, 12, 5, 1)])
    add("feasibility-10", "feasibility", ["sat", "dated_atom_both_interpretations"], assume=[dv(dc)])

    # Partition and coverage: 4 true, 4 false, 2 UNRESOLVED.
    add("partition-01", "partition", ["true", "exclusive"], claim="exclusive", members=[win(3, i, 0) for i in teams(0)], target=None)
    add("partition-02", "partition", ["false", "implication_not_exclusive"], claim="exclusive",
        members=[thr(3, 0, 5, 0), thr(3, 0, 4, 0)], target=None)
    add("partition-03", "partition", ["false", "gap"], claim="exhaustive", members=[win(3, i, 0) for i in (0, 1, 2)], target=None)
    members = [win(3, i, 0) for i in teams(1)]
    add("partition-04", "partition", ["true", "exhaustive_target"], claim="exhaustive", members=members,
        target=ir.disj(*[win(3, i, 0) for i in teams(1)]))
    members = [thr(3, 1, 5, 0), ir.conj(thr(3, 1, 4, 0), neg(thr(3, 1, 5, 0)))]
    add("partition-05", "partition", ["true", "partition_nontrivial"], claim="partition", members=members, target=thr(3, 1, 4, 0))
    add("partition-06", "partition", ["false", "out_of_target"], claim="partition",
        members=[thr(3, 2, 5, 0), thr(3, 3, 5, 0)], target=thr(3, 2, 4, 0))
    add("partition-07", "partition", ["false", "overlap"], claim="exclusive", members=[win(3, 12, 0), neg(thr(3, 12, 6, 0))], target=None)
    add("partition-08", "partition", ["unresolved", "missing_no_date"], claim="exclusive",
        members=[thr(da["si"], da["i"], 5, 0), dv(da)], target=None)
    members = [win(3, i, 0) for i in teams(0)]
    add("partition-09", "partition", ["true", "partition_exhaustive_target"], claim="partition", members=members,
        target=ir.disj(*[win(3, i, 0) for i in teams(0)]))
    add("partition-10", "partition", ["unresolved", "interpretation_split", "missing_no_date"], claim="exclusive",
        members=[dv(dc), thr(dc["si"], dc["i"], dc["t"], 0)], target=None)

    return queries, design


def generate(out_dir: str | Path, seed: int = SEED, seasons: int = BASE_SEASONS, name: str = "toy10k") -> dict[str, Any]:
    """Pure generation (no engine): corpus, gold submission, queries and gold/query_design (premises unset)."""
    out_dir = Path(out_dir)
    gold_dir = out_dir / "gold"
    gold_dir.mkdir(parents=True, exist_ok=True)
    recs = _records(seed, seasons)
    ir.write_jsonl(out_dir / "corpus.jsonl", (
        {"id": r["id"], "text": r["question"], "label": r["label"], "event_title": r["event_title"], "controlled": True}
        for r in recs
    ))
    rows = [_binding_row(r) for r in recs]
    constraints = _gold_constraints(recs)
    usage = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}
    ir.write_submission(gold_dir, rows, constraints, GOLD_INTERPRETATIONS, usage)
    queries, design = _queries(recs, seed + 1)
    ir.write_jsonl(out_dir / "queries.jsonl", queries)
    ir.write_jsonl(gold_dir / "query_design.jsonl", design)
    summary = {
        "name": name,
        "seasons": seasons,
        "seed": seed,
        "controlled": True,
        "outcomes": len(recs),
        "markets": len({r["id"].rsplit(":", 2)[1] for r in recs}),
        "classes": len({r["cls"] for r in recs if r["cls"]}),
        "unresolved_outcomes": sum(1 for r in recs if r["cls"] is None),
        "gold_constraints": len(constraints),
        "interpretations": GOLD_INTERPRETATIONS,
        "queries": len(queries),
        # Correction: literal alias definitions are exempt from alias_binding_cycle (docs/ir.md; crates/mb-ir/src/validate.rs); the note below that says otherwise is stale.
        "notes": [
            "Outcome IDs are unsalted SHA-256 prefixes of market keys: stable across scales, enumerable from the public generator (not a hidden set). Row order is hash order.",
            "Top level: corpus.jsonl, queries.jsonl, fixture.json. Gold-bearing files are under gold/ (submission, answers.jsonl, verdicts.jsonl, query_design.jsonl, summary.json).",
            "Dated propositions are sampled per season; anchor queries use base-season dated propositions only.",
            "Each dated Yes/No pair is complementary in every interpretation (iff(Yes, not No)).",
            "gold/query_design.jsonl premises = material premises by single-drop ablation (set by finalize, needs mb-engine).",
            "Gold constraints are pair-expanded over records (one constraint per record pair). Class-level refs would be equivalent under the binding semantics, but the current alias definitions (var of the first bound outcome) fail the engine's alias_binding_cycle check, so the expansion is kept until the alias representation is settled.",
            "No evidence scope: queries carry no per-query evidence field (decision D-F).",
        ],
    }
    ir.write_json(out_dir / "fixture.json", summary)
    return summary


# ---------------------------------------------------------------- engine step (needs mb-engine)


def canonical_answer(family: str, ans: dict[str, Any]) -> Any:
    """Comparable content of one answer: decisions, labels and selected IDs (witnesses/cores are not compared)."""
    if ans.get("error") is not None:
        return ["error", ans["error"]]
    if family == "closure":
        return [[lab, sorted(ans["labels"][lab])] for lab in ORDER]
    if family == "selection":
        return sorted(ans["ids"])
    return [ans["decision"], sorted(ans.get("missing") or [])]


_ITEM_KEY = {"closure": "hyp", "feasibility": "assume", "partition": "members"}


def ablation_queries(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One variant per premise: the query with that premise dropped (qid suffixed __drop<j>)."""
    variants: list[dict[str, Any]] = []
    for q in queries:
        key = _ITEM_KEY.get(q["family"])
        if key is None:
            continue
        items = q[key]
        for j in range(len(items)):
            rest = items[:j] + items[j + 1:]
            if not rest and q["family"] == "closure":
                rest = [ir.TRUE]  # an empty hypothesis is written as `true`
            variants.append({**q, "qid": f"{q['qid']}__drop{j}", key: rest})
    return variants


def material_premises(gold_dir: Path, queries: list[dict[str, Any]], answers: dict[str, dict[str, Any]], workdir: Path) -> dict[str, int]:
    """Premises whose removal changes the engine answer, one batched eval for all variants."""
    variants = ablation_queries(queries)
    counts = {q["qid"]: 0 for q in queries}
    if not variants:
        return counts
    qpath, apath = workdir / "ablation_queries.jsonl", workdir / "ablation_answers.jsonl"
    ir.write_jsonl(qpath, variants)
    engine.eval_submission(gold_dir, qpath, apath)
    got = {a["qid"]: a for a in ir.read_jsonl(apath)}
    if len(got) != len(variants):
        raise RuntimeError(f"ablation: engine answered {len(got)} of {len(variants)} variants")
    family = {q["qid"]: q["family"] for q in queries}
    for v in variants:
        base = v["qid"].split("__drop")[0]
        if canonical_answer(family[base], got[v["qid"]]) != canonical_answer(family[base], answers[base]):
            counts[base] += 1
    return counts


def _replace_all(staged: list[tuple[Path, Path]]) -> None:
    """Move each staged temp file onto its target. Targets change only after every stage has succeeded."""
    for tmp, target in staged:
        os.replace(tmp, target)


def _stage_json(target: Path, obj: Any, indent: int | None = 2) -> tuple[Path, Path]:
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=True, allow_nan=False) + "\n")
    return Path(name), target


def _stage_jsonl(target: Path, rows: list[Any]) -> tuple[Path, Path]:
    fd, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(ir.dumps(row) + "\n")
    return Path(name), target


def finalize(out_dir: str | Path) -> dict[str, Any]:
    """Engine step for a generated fixture directory. Everything is computed and staged in temp files first;
    the targets (gold/answers.jsonl, gold/verdicts.jsonl, gold/query_design.jsonl, gold/summary.json) are replaced
    only when every step has succeeded, so a failure leaves the previous files (or none) and no partial output."""
    out_dir = Path(out_dir)
    gold_dir = out_dir / "gold"
    queries_path = out_dir / "queries.jsonl"
    answers_path = gold_dir / "answers.jsonl"
    verdicts_path = gold_dir / "verdicts.jsonl"
    design_path = gold_dir / "query_design.jsonl"
    summary_path = gold_dir / "summary.json"
    staged: list[tuple[Path, Path]] = []
    engine_tmp: list[Path] = []
    try:
        engine.validate(gold_dir)
        answers_tmp, verdicts_tmp = answers_path.with_name(answers_path.name + ".tmp"), verdicts_path.with_name(verdicts_path.name + ".tmp")
        engine_tmp += [answers_tmp, verdicts_tmp]
        engine.eval_submission(gold_dir, queries_path, answers_tmp)
        engine.check(gold_dir, queries_path, answers_tmp, verdicts_tmp)
        queries = list(ir.read_jsonl(queries_path))
        answers = {a["qid"]: a for a in ir.read_jsonl(answers_tmp)}
        verdicts = list(ir.read_jsonl(verdicts_tmp))
        with tempfile.TemporaryDirectory() as tmp:
            counts = material_premises(gold_dir, queries, answers, Path(tmp))

        design = list(ir.read_jsonl(design_path))
        for d in design:
            d["premises"] = counts[d["qid"]]
        premises = {
            "ge8": sum(1 for d in design if d["premises"] >= 8),
            "histogram": {str(k): v for k, v in sorted(Counter(d["premises"] for d in design).items())},
        }
        summary = {"answers": answer_summary(queries, answers), "premises": premises,
                   "verdict_reasons": dict(sorted(Counter(v["reason"] for v in verdicts).items()))}
        staged += [(answers_tmp, answers_path), (verdicts_tmp, verdicts_path)]
        staged.append(_stage_jsonl(design_path, design))
        staged.append(_stage_json(summary_path, summary))
        _replace_all(staged)
    except BaseException:
        for tmp in engine_tmp + [t for t, _ in staged]:
            tmp.unlink(missing_ok=True)
        raise
    return {"answers": len(answers), "verdict_reasons": summary["verdict_reasons"], "premises": premises}


def answer_summary(queries: list[dict[str, Any]], answers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_family: dict[str, Counter] = defaultdict(Counter)
    for q in queries:
        ans = answers[q["qid"]]
        if q["family"] == "closure":
            by_family["closure"]["error" if ans.get("error") else "answered"] += 1
        elif q["family"] == "selection":
            by_family["selection"]["nonempty" if ans.get("ids") else "empty"] += 1
        else:
            by_family[q["family"]][ans.get("decision", "error")] += 1
    return {family: dict(sorted(c.items())) for family, c in sorted(by_family.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.fixtures_gen")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen_p = sub.add_parser("gen", help="pure generation (no engine)")
    gen_p.add_argument("--out", default=str(OUT_DIR))
    gen_p.add_argument("--seed", type=int, default=SEED)
    gen_p.add_argument("--seasons", type=int, default=BASE_SEASONS)
    fin_p = sub.add_parser("finalize", help="gold answers, verdicts and premises via mb-engine")
    fin_p.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args(argv)
    if args.cmd == "gen":
        summary = generate(args.out, args.seed, args.seasons)
        print({k: summary[k] for k in ("outcomes", "markets", "classes", "gold_constraints", "queries")})
        return 0
    print(finalize(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

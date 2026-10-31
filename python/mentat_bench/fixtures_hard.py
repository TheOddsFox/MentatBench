"""Mentat-Hard world generator and its language (docs/mentat_hard.md, data contracts: docs/mentat_hard_contracts.md).

World. A split is a set of independent leagues; each league is one logical component. A league has 4-6 teams with
distinct integer scores in 0..L (L = 5-7), a qualifying set Q (the top k by score, k = 2..3), a cup winner W and
runner-up S (both drawn from the qualifiers), and, under rule 7, two overlapping subgroups with one seed each. Classes:
T(team, t) = score >= t (t = 1..L), W(team), S(team), Q(team), and seed(group, team). The gold world is drawn first;
every rule is then emitted only in a form the gold world satisfies (checked by evaluating every constraint on it):
  1 ladder         T(x,t+1) -> T(x,t)                                         every league
  2 qualify        exactly(k, Q), k = 2 in about 5 of 6 leagues (drawn per league); Q(x) -> T(x,q)  every league
  3 count          atleast(k1, T(.,a)), atmost(k2, T(.,b)); k1, k2 tight or one off the gold count (one off in
                   chain leagues)
  4 outscores      X > Y: T(Y,t) -> T(X,t+1) (t < L)   (one pair; no unit facts, so nothing is forced)
  5 placement      exactly-one W, exactly-one S, atmost(1, W(x), S(x)), finalists qualify W(x) -> Q(x),
                   S(x) -> Q(x), and "if X wins, Y is runner-up" W(X) -> S(Y) (X the gold winner and Y the
                   gold runner-up, or X a gold non-winner); with k = 2 also exactly(1, W(x), S(x), not Q(x)),
                   implied (the qualifiers are the finalists) and stated as an exactly-one set     every league
  6 chain          X1 >= X2 >= ... >= Xc (c = 2-3, at most n - k; each link "at least as many points": T(X(i+1),t) ->
                   T(Xi,t), so the chain implies across c-1 hops without pinning any score level; gold scores are
                   strict), W(x) -> T(x,a) with a drawn from 1..the gold winner's score
  7 overlap        (every league) three subgroups of 3-4 teams, each later one sharing 1-2 teams with the first;
                   exactly-one seed per group; a team in several groups holds at most one seed; a seed has at
                   least s points: seed(g,x) -> T(x,s_g). Groups alternate named in titles / implicit (a
                   paraphrased scope phrase only). world.json has one overlap entry per pair of groups sharing teams
  8 ambiguous      2-3 "by the break" (missing no_date) or "top half" (missing ambiguous_scope) propositions per
                   league (always on), unresolved, with a base and an alt reading (iff with a class per
                   interpretation). Break at final points s: base T(x, s+1) (final points), alt T(x, s) (plus the
                   Shield point). Top half: base Q(x) (a playoff place), alt T(x, M) with one M per league. The
                   readings disagree in the gold world, so they are not interchangeable
Rules 3, 4 and 6 are drawn per league; rules 7 and 8 are always on; league 0 draws all, so even a small split built with
--leagues exercises every rule type. The q, finalist and s_g links (rules 2, 5, 7) tie the scores, the
qualifiers, the cup and the seeds together, so each league is one component. No outcome is forced: a league is
redrawn with the same rule set and k (new gold world and other parameters, up to 100 seeded draws) while its bank
shows any class fixed by the base (TARGET_FIXED = MAX_FIXED = 0); if no draw is free of constants, generation
fails. The estimate is recorded per league in world.json ("fixed_share", always 0 in version 2).

Rule card (v3). Each league has a rule card, rendered by render_card from the rule list alone (world_json without scores,
winner, runner_up, qualifiers and seeds; the renderer refuses those keys). Every corpus row of the league carries it as
"context". The card is one sentence per constraint group of rules 2-7 (docs/mentat_hard.md has the template checklist),
and it returns the lg.cons indices each sentence states.

Language. 30 rendering families. A family is a style index (aligned across kinds: plain, "or more", "more than t-1",
"not fewer than", "fail to", negated, double negative, "at most t-1", ...) and an event-title variant. Families 1-20
use the dev wording lists; families 21-30 use held-out lists that never appear in dev, and the dev split renders
only families 1-20. Each class has one market; a share of classes (--paraphrase-share, default 17%) get a second
market and some (--third-share, default 17%) a third, every market of a class in a different style (paraphrases).
A share of threshold and qualify classes (GLOSS_SHARE) also gets a glossed market: the ambiguous phrase with its reading
stated ("...by the break, counted on final points?", "...top half, meaning a playoff place?"), bound, with meta gloss true.
Market ids are opaque("hard", split, seed, *key); class names are split-salted hashes; team and league names come
from split-specific syllables, so dev and test share no id, class name or team name. A team name may recur in
another league of the same split (a disambiguation distractor).

Exam. About 200 queries (closure 80, feasibility 50, partition 40, selection 30), built with expected answers
proved by construction: joint-only UNSAT sets by cardinality arguments, with every pair witnessed SAT by a sampled
valid world (a bank of worlds that satisfy every constraint); false partition claims witnessed by a bank world.
Premises, targets, UNSAT options and partition members use only classes the bank shows with both values (not fixed by
the base); closure and selection universes are the query league's outcomes. Feasibility SAT and UNSAT sets share one
draw of size (3-9) and of single-category, and UNSAT sets are padded with gold-true literals of other categories (every
pair still witnessed), so size and category count do not predict the answer. Each SAT set is the twin of the UNSAT set
before it: the same multiset of proposition kinds (win, runner-up, qualify, seed, threshold, dated), all true in one
valid world, so the kinds a question names do not predict the answer either. Some SAT and UNSAT sets also carry an
ambiguous literal whose two readings agree on the answer, so an ambiguous outcome does not imply UNRESOLVED. The
builders cycle through answer kinds,
so the file order is shuffled and qids are renumbered per family in file order.
`finalize` runs mb-engine (fixtures_gen.finalize: answers, verdicts, material premises, summary), then checks the
expectations and that no assumption pair of a joint-UNSAT query is UNSAT on its own. `audit` re-solves every query
with audit_brute; any skipped, unverified or mismatched query fails.

Layout: corpus.jsonl, queries.jsonl, fixture.json (public; no gold-derived counts); gold/ holds the submission
(ir.write_submission), meta.jsonl and world.json (docs/mentat_hard_contracts.md; an unresolved outcome's meta class is "u" + its market id), and the
finalized exam (answers, verdicts, query_design with expectations and material premises, summary with the gold counts
under "counts").

gold/world.json keys (exact):
  {"name", "split", "seed", "version", "leagues": [league]}
  league     {"id", "name", "teams": [team], "L", "scores": {team: int}, "winner", "runner_up", "qualifiers": [team],
              "seeds": {subgroup id: team}, "rules": [rule], "subgroups": [subgroup],
              "paraphrase_pairs": [[market id, market id]],   (every pair of markets of one class, first market first)
              "fixed_share"}   (share of the league's classes the bank shows with one value only: an upper estimate of
                                the classes the base theory fixes; at most MAX_FIXED)
  subgroup   {"id", "members": [team], "named": bool, "title", "theme"}   (title "Group <theme>" or the scope phrase)
  rule       {"rule": 1, "type": "ladder"}
             {"rule": 2, "type": "qualify", "k", "min_points"}
             {"rule": 3, "type": "count", "atleast": {"k", "t"}, "atmost": {"k", "t"}}
             {"rule": 4, "type": "outscores", "pairs": [[X, Y]]}                  (X outscores Y)
             {"rule": 5, "type": "placement", "conditionals": [[X, Y]]}           (if X wins, Y is runner-up)
             {"rule": 6, "type": "chain", "teams": [X1, ..., Xn], "a"}
             {"rule": 7, "type": "overlap", "groups": [id, id], "shared": [team], "seed_min_points": {id: int}}
             {"rule": 8, "type": "ambiguous", "props": [{"market", "kind": "break"|"tophalf", "team", "t", "missing",
              "negated" (the wording is negative: the Yes outcome asserts the negation of the base and alt classes),
              "readings": {"base": reading, "alt": reading}}]}
  reading    {"class", "kind", "team", "t", "subgroup"}   (the class the proposition means in that interpretation)

Registration (corrections.register_generated contract; whitelisted with params seed and split): SEED, REQUIRED_FILES
and generate(out, *, name, seed, split, leagues, paraphrase_pct, third_pct, relations, groups) build the complete fixture (gen, finalize,
audit gate, curated suite with its audit) into a new directory.

Commands:
  python -m mentat_bench.fixtures_hard gen --split dev|test [--seed N] [--leagues N] [--paraphrase-share F]
                                           [--third-share F] --out DIR
  python -m mentat_bench.fixtures_hard build --split dev|test [--seed N] [--leagues N] [--name NAME] --out NEW_DIR
  python -m mentat_bench.fixtures_hard finalize --out DIR
  python -m mentat_bench.fixtures_hard audit --out DIR
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
import tempfile
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from . import engine, fixtures_gen, ir

NAME = "mentat_hard"
VERSION = "3.1"
SEEDS = {"dev": 20261027, "test": 20261028}
LEAGUES = 18
DEV_FAMILIES = 20
FAMILIES = 30
VENUE = "mh"
MAX_QUESTION = 200
# Share of classes with a second market, and (a subset of those) with a third, all in different styles. Defaults give
# >= 800 unordered EQUIV outcome pairs per split after constants (the curated suite asks each pair once), at
# <= ~2,800 outcomes; a third market adds 4 same-meaning pairs for 2 outcomes, a second 2 pairs for 2.
PARAPHRASE_SHARE = 0.17
THIRD_SHARE = 0.17
GLOSS_SHARE = 0.3  # threshold and qualify classes with a glossed market (the ambiguous phrase, read out): decoys' u's
OPTIONAL_RULES = (3, 4, 6, 7, 8)
SUBGROUPS = 3  # seeded subgroups per league (rule 7, always on): exactly-one sets for the curated suite
RULE_P = 0.6
BANK_TRIES = 2000
TARGET_FIXED = 0.0  # a league is redrawn while its bank shows any class fixed by the base (estimate from above): no forced outcome
MAX_FIXED = 0.0  # ValueError when the best of LEAGUE_TRIES draws is above this (no silent fallback)
LEAGUE_TRIES = 100
SEED = SEEDS["dev"]  # the registration default (corrections); generate(seed=None) takes SEEDS[split]
# Every file of a complete fixture (corrections REQUIRED_FILES); the curated suite's files join at integration.
REQUIRED_FILES = ("corpus.jsonl", "queries.jsonl", "fixture.json", "gold/bindings.jsonl", "gold/theory.json",
                  "gold/provenance.jsonl", "gold/usage.json", "gold/meta.jsonl", "gold/world.json", "gold/answers.jsonl",
                  "gold/verdicts.jsonl", "gold/query_design.jsonl", "gold/summary.json",
                  "candidates.jsonl", "gold/suite_labels.jsonl")
GOLD_INTERPRETATIONS = ["base", "alt"]
EXAM = {"closure": 80, "feasibility": 50, "partition": 40, "selection": 30}
CATEGORY = {"thr": "score", "win": "placement", "runner": "placement", "qual": "qualify", "seed": "overlap"}
AMBIGUOUS_SHARE = 0.6  # SAT/UNSAT feasibility items that also carry an ambiguous literal whose readings agree
MISSING = {"break": "no_date", "tophalf": "ambiguous_scope"}
VERIFIED = ("verified_exhaustive", "verified_independent_solver")
# audit_brute's DPLL has no clause learning; a few true claims need more than its default 200k conflicts (tens of
# seconds) for some seeds. The budget bounds effort only: a skip still fails the audit.
AUDIT_BUDGET = 5_000_000
AUDIT_QUERY_SECONDS = 600.0
# the curated suite generate() writes (hard_suite.build defaults); small builds (tests) pass smaller sizes: the group
# pool is drawn from the leagues there are, so 1,000 groups on one league is slow
SUITE_RELATIONS, SUITE_GROUPS = 6_000, 1_000  # owner decision 2026-10-30: 6,000 relations (was 10,000)

# Split-specific name syllables: first syllables are disjoint, so no team or league name is shared across splits.
FIRST = {
    "dev": ("Bar", "Cal", "Dor", "Fen", "Gal", "Hal", "Kel", "Lor", "Mar", "Nor", "Pel", "Ros", "Sal", "Tor", "Val"),
    "test": ("Ash", "Bel", "Cor", "Dun", "Eld", "Fal", "Gor", "Hen", "Ith", "Jor", "Kar", "Lun", "Mor", "Orm", "Pry"),
}
TEAM_END = ("by", "dale", "field", "ford", "gate", "ham", "holt", "mere", "moor", "port", "stead", "ton", "wick", "worth")
LEAGUE_END = ("aria", "ova", "ene", "ica", "ura")
THEMES = (
    ("Coastal", ("the coastal sides", "the clubs on the coast", "the seaside teams")),
    ("Northern", ("the northern sides", "the clubs from the north", "the teams up north")),
    ("Founding", ("the founding clubs", "the original member clubs", "the charter sides")),
    ("Riverside", ("the riverside sides", "the clubs along the river", "the teams by the river")),
    ("Upland", ("the upland sides", "the hill clubs", "the highland teams")),
    ("Capital", ("the capital sides", "the clubs from the capital", "the big-city teams")),
)

# Wording. "+" = the question asserts the class, "-" = it asserts its negation (the Yes label is the negated record).
# {n} is the threshold t, {m} is t - 1. Dev lists have 10 styles aligned across kinds, held-out lists 5.
WORDING: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "thr": ((
        "+Will {team} score at least {n} points in the {ln} League?",
        "+Will {team} finish the {ln} League season with {n} or more points?",
        "+Will {team} collect more than {m} points in the {ln} League?",
        "+Will {team} end the {ln} League season with not fewer than {n} points?",
        "-Will {team} fail to reach {n} points in the {ln} League?",
        "-Will {team} finish the {ln} League with fewer than {n} points?",
        "+Will {team} not fail to reach {n} points in the {ln} League?",
        "-Will {team} score at most {m} points in the {ln} League?",
        "+Will {team} pick up {n} points or more in the {ln} League?",
        "+Is it false that {team} ends the {ln} League below {n} points?",
    ), (
        "+Will {team} hit the {n}-point mark in the {ln} League?",
        "-Will {team} come up short of {n} points in the {ln} League?",
        "+Will {team}'s {ln} League tally top {m} points?",
        "+Is it untrue that {team} stays under {n} points in the {ln} League?",
        "-Will {team} be held to {m} points or fewer in the {ln} League?",
    )),
    "win": ((
        "+Will {team} win the {ln} Cup?",
        "+Will {team} lift the {ln} Cup trophy?",
        "+Will {team} be crowned {ln} Cup champions?",
        "+Will {team} take the {ln} Cup title?",
        "-Will {team} fail to win the {ln} Cup?",
        "-Will {team} miss out on the {ln} Cup title?",
        "+Will {team} not fail to win the {ln} Cup?",
        "-Will {team} finish the {ln} Cup without the trophy?",
        "+Will the {ln} Cup be won by {team}?",
        "+Is it false that {team} misses out on the {ln} Cup title?",
    ), (
        "+Will {team} claim {ln} Cup glory?",
        "-Will {team} be denied the {ln} Cup crown?",
        "+Will {team} emerge as {ln} Cup winners?",
        "+Is it untrue that {team} goes without the {ln} Cup crown?",
        "-Will the {ln} Cup crown elude {team}?",
    )),
    "runner": ((
        "+Will {team} finish runner-up in the {ln} Cup?",
        "+Will {team} lose the {ln} Cup final?",
        "+Will {team} end the {ln} Cup in second place?",
        "+Will {team} be the {ln} Cup runner-up?",
        "-Will {team} fail to finish runner-up in the {ln} Cup?",
        "-Will {team} avoid finishing second in the {ln} Cup?",
        "+Will {team} not fail to finish second in the {ln} Cup?",
        "-Will {team} finish somewhere other than second in the {ln} Cup?",
        "+Will {team} take the {ln} Cup silver medal?",
        "+Is it false that {team} misses second place in the {ln} Cup?",
    ), (
        "+Will {team} be the beaten finalists in the {ln} Cup?",
        "-Will {team} miss out on second place in the {ln} Cup?",
        "+Will {team} settle for second in the {ln} Cup?",
        "+Is it untrue that {team} misses the {ln} Cup silver?",
        "-Will {team} finish anywhere but second in the {ln} Cup?",
    )),
    "qual": ((
        "+Will {team} qualify for the {ln} playoffs?",
        "+Will {team} reach the {ln} League playoffs?",
        "+Will {team} earn a {ln} playoff berth?",
        "+Will {team} book a place in the {ln} playoffs?",
        "-Will {team} fail to qualify for the {ln} playoffs?",
        "-Will {team} miss the {ln} playoffs?",
        "+Will {team} not miss the {ln} playoffs?",
        "-Will {team} be left out of the {ln} playoffs?",
        "+Will {team} be among the {ln} playoff qualifiers?",
        "+Is it false that {team} misses the {ln} playoffs?",
    ), (
        "+Will {team} punch a ticket to the {ln} postseason?",
        "-Will {team} sit out the {ln} postseason?",
        "+Will {team} advance to the {ln} postseason?",
        "+Is it untrue that {team} misses the {ln} postseason?",
        "-Will {team} be shut out of the {ln} postseason?",
    )),
    "seed": ((
        "+Will {team} be the top seed {scope} in the {ln} Cup draw?",
        "+Will {team} be seeded first {scope} for the {ln} Cup?",
        "+Will {team} receive the seed {scope} for the {ln} Cup?",
        "+Will {team} hold the seeding {scope} in the {ln} Cup draw?",
        "-Will {team} fail to get the seed {scope} for the {ln} Cup?",
        "-Will {team} go unseeded {scope} in the {ln} Cup draw?",
        "+Will {team} not miss out on the seed {scope} in the {ln} Cup?",
        "-Will {team} be passed over for the seed {scope} in the {ln} Cup?",
        "+Will {team} be handed the seed {scope} for the {ln} Cup?",
        "+Is it false that {team} goes unseeded {scope} in the {ln} Cup?",
    ), (
        "+Will {team} land the protected slot {scope} in the {ln} Cup?",
        "-Will {team} be left unprotected {scope} in the {ln} Cup draw?",
        "+Will {team} get pot-one status {scope} for the {ln} Cup?",
        "+Is it untrue that {team} misses the protected slot {scope} in the {ln} Cup?",
        "-Will {team} be denied pot-one status {scope} for the {ln} Cup?",
    )),
    "break": ((
        "+Will {team} have at least {n} points by the {ln} League mid-season break?",
        "+Will {team} sit on {n} or more points at the {ln} League break?",
        "+Will {team} have more than {m} points by the {ln} League break?",
        "+Will {team} have not fewer than {n} points by the {ln} League break?",
        "-Will {team} fail to reach {n} points by the {ln} League break?",
        "-Will {team} be below {n} points at the {ln} League mid-season break?",
        "+Will {team} not fail to reach {n} points by the {ln} League break?",
        "-Will {team} have at most {m} points at the {ln} League break?",
        "+Will {team} reach {n} points by the {ln} League winter break?",
        "+Is it false that {team} is below {n} points at the {ln} League break?",
    ), (
        "+Will {team} have hit {n} points before the {ln} League pause?",
        "-Will {team} still be short of {n} points at the {ln} League pause?",
        "+Will {team} have topped {m} points before the {ln} League pause?",
        "+Is it untrue that {team} is under {n} points at the {ln} League pause?",
        "-Will {team} be held to {m} points or fewer at the {ln} League pause?",
    )),
    "tophalf": ((
        "+Will {team} finish in the top half of the {ln} League?",
        "+Will {team} end the {ln} League season in the upper half?",
        "+Will {team} place in the top half of the {ln} League table?",
        "+Will {team} be a top-half side in the {ln} League?",
        "-Will {team} fail to finish in the top half of the {ln} League?",
        "-Will {team} finish in the bottom half of the {ln} League?",
        "+Will {team} not fail to make the {ln} League top half?",
        "-Will {team} end up in the lower half of the {ln} League?",
        "+Will {team} land a top-half finish in the {ln} League?",
        "+Is it false that {team} finishes in the {ln} League's bottom half?",
    ), (
        "+Will {team} sit in the {ln} League's top half at the end?",
        "-Will {team} drop into the {ln} League's lower half?",
        "+Will {team} finish above the {ln} League midline?",
        "+Is it untrue that {team} ends below the {ln} League midline?",
        "-Will {team} finish beneath the {ln} League midline?",
    )),
    # Glossed markets (v3): the ambiguous phrase with its reading stated, so the market is bound without the rule card.
    # "gthr" is a threshold market (reading 1: final points), "gqual" a qualification market (reading 1: a playoff place).
    "gthr": ((
        "+Will {team} have at least {n} points by the {ln} League break, counted on final points?",
        "+Will {team} sit on {n} or more points at the {ln} League break, counted on final points?",
        "+Will {team} have more than {m} points by the {ln} League break, counted on final points?",
        "+Will {team} have not fewer than {n} points by the {ln} League break, counted on final points?",
        "-Will {team} fail to reach {n} points by the {ln} League break, counted on final points?",
        "-Will {team} be below {n} points at the {ln} League break, counted on final points?",
        "+Will {team} not fail to reach {n} points by the {ln} League break, counted on final points?",
        "-Will {team} have at most {m} points at the {ln} League break, counted on final points?",
        "+Will {team} reach {n} points by the {ln} League break, counted on final points?",
        "+Is it false that {team} is below {n} points at the {ln} League break, counted on final points?",
    ), (
        "+Will {team} have hit {n} points before the {ln} League pause, counted on final points?",
        "-Will {team} still be short of {n} points at the {ln} League pause, counted on final points?",
        "+Will {team} have topped {m} points before the {ln} League pause, counted on final points?",
        "+Is it untrue that {team} is under {n} points at the {ln} League pause, counted on final points?",
        "-Will {team} be held to {m} points or fewer at the {ln} League pause, counted on final points?",
    )),
    "gqual": ((
        "+Will {team} finish in the top half of the {ln} League, meaning a playoff place?",
        "+Will {team} end the {ln} League season in the upper half, meaning a playoff place?",
        "+Will {team} place in the top half of the {ln} League table, meaning a playoff place?",
        "+Will {team} be a top-half side in the {ln} League, meaning a playoff place?",
        "-Will {team} fail to finish in the top half of the {ln} League, meaning a playoff place?",
        "-Will {team} finish in the bottom half of the {ln} League, meaning no playoff place?",
        "+Will {team} not fail to make the {ln} League top half, meaning a playoff place?",
        "-Will {team} end up in the lower half of the {ln} League, meaning no playoff place?",
        "+Will {team} land a top-half finish in the {ln} League, meaning a playoff place?",
        "+Is it false that {team} finishes in the {ln} League's bottom half, meaning no playoff place?",
    ), (
        "+Will {team} sit in the {ln} League's top half at the end, meaning a playoff place?",
        "-Will {team} drop into the {ln} League's lower half, meaning no playoff place?",
        "+Will {team} finish above the {ln} League midline, meaning a playoff place?",
        "+Is it untrue that {team} ends below the {ln} League midline, meaning a playoff place?",
        "-Will {team} finish beneath the {ln} League midline, meaning no playoff place?",
    )),
}
TITLES: dict[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "thr": (("{ln} League standings", "{ln} League points race"), ("{ln} League scoring ledger", "{ln} League tally watch")),
    "win": (("{ln} Cup winner", "{ln} Cup final"), ("{ln} Cup showpiece", "{ln} Cup decider")),
    "runner": (("{ln} Cup final", "{ln} Cup runner-up"), ("{ln} Cup showpiece", "{ln} Cup decider")),
    "qual": (("{ln} playoff race", "{ln} League playoffs"), ("{ln} postseason picture", "{ln} postseason berths")),
    "seed_named": (("{ln} Cup draw: Group {group}", "Group {group} seeding, {ln} Cup"),
                   ("{ln} Cup pots: Group {group}", "{ln} Cup protected slot, Group {group}")),
    "seed_implicit": (("{ln} Cup draw", "{ln} Cup seeding"), ("{ln} Cup pots", "{ln} Cup protected slots")),
    "break": (("{ln} League mid-season standings", "{ln} League halfway table"),
              ("{ln} League pause snapshot", "{ln} League winter table")),
    "tophalf": (("{ln} League final table", "{ln} League table split"),
                ("{ln} League midline watch", "{ln} League end-of-season split")),
}
TITLES["gthr"], TITLES["gqual"] = TITLES["break"], TITLES["tophalf"]


def opaque(split: str, seed: int, *key: Any) -> str:
    return fixtures_gen.opaque("hard", split, seed, *key)


def family_style(family: int) -> tuple[int, int, bool]:
    """(style index, title index, held out) of a family: 1-20 dev wording, 21-30 held-out wording."""
    if not 1 <= family <= FAMILIES:
        raise ValueError(f"family {family} outside 1..{FAMILIES}")
    if family <= DEV_FAMILIES:
        return (family - 1) % 10, (family - 1) // 10, False
    return (family - 21) % 5, (family - 21) // 5, True


def render(kind: str, family: int, team: str, ln: str, t: int | None = None, scope: str = "",
           group: str = "", named: bool = False) -> tuple[str, str, bool]:
    """(question, event title, negated) of one market in a family's style."""
    style, title_i, held = family_style(family)
    text = WORDING[kind][held][style]
    tkind = ("seed_named" if named else "seed_implicit") if kind == "seed" else kind
    title = TITLES[tkind][held][title_i].format(ln=ln, group=group)
    num = {} if t is None else {"n": t, "m": t - 1}
    question = re.sub(r"\b1 points\b", "1 point", text[1:].format(team=team, ln=ln, scope=scope, **num))
    return question, title, text[0] == "-"


# ---------------------------------------------------------------- expressions

def _ref(name: str) -> dict[str, Any]:
    return {"op": "ref", "name": name}


def _card(op: str, k: int, args: list[dict[str, Any]]) -> dict[str, Any]:
    return {"op": op, "k": k, "args": args}


def _implies(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    return {"op": "implies", "a": a, "b": b}


def _ev(e: dict[str, Any], env: dict[str, bool]) -> bool:
    """Truth of an Expr; env maps class names (ref) and outcome ids (var) to values."""
    op = e["op"]
    if op == "ref":
        return env[e["name"]]
    if op == "var":
        return env[e["id"]]
    if op == "not":
        return not _ev(e["a"], env)
    if op == "implies":
        return not _ev(e["a"], env) or _ev(e["b"], env)
    if op == "iff":
        return _ev(e["a"], env) == _ev(e["b"], env)
    if op == "and":
        return all(_ev(x, env) for x in e["args"])
    if op == "or":
        return any(_ev(x, env) for x in e["args"])
    n = sum(_ev(x, env) for x in e["args"])
    return {"atmost": n <= e["k"], "atleast": n >= e["k"], "exactly": n == e["k"]}[op]


# ---------------------------------------------------------------- world

def name_pools(split: str, seed: int) -> tuple[list[str], list[str]]:
    """(team pool, league names) of a split, in a seeded order."""
    rng = random.Random(f"hard:{split}:{seed}:names")
    teams = sorted(a + b for a in FIRST[split] for b in TEAM_END)
    leagues = sorted(a + b[1:] if a[-1] == b[0] else a + b for a in FIRST[split] for b in LEAGUE_END)
    rng.shuffle(teams)
    rng.shuffle(leagues)
    return teams, leagues


class League:
    """One league: gold world, classes, class-level constraints, ambiguous propositions and the bank of valid worlds."""

    def __init__(self, split: str, seed: int, li: int, name: str, pool: list[str], attempt: int = 0):
        rng = random.Random(f"hard:{split}:{seed}:league:{li}:{attempt}")
        self.split, self.seed, self.li, self.ln = split, seed, li, name
        self.id = f"{split}-L{li + 1:02d}"
        rrng = random.Random(f"hard:{split}:{seed}:rules:{li}")  # the rule set does not depend on the attempt
        rules = list(OPTIONAL_RULES) if li == 0 else [r for r in OPTIONAL_RULES if rrng.random() < RULE_P]
        while len(rules) < 2:
            rules = sorted(set(rules) | {rrng.choice(OPTIONAL_RULES)})
        rules = sorted(set(rules) | {7, 8})  # every league has seeded subgroups (exactly-one sets) and ambiguous propositions
        self.optional = rules
        n = self.n = rng.choice((4, 5, 5, 6))
        L = self.L = rng.choice([x for x in (5, 6, 7) if n * x <= 36])
        self.teams = rng.sample(pool, n)
        self.scores = dict(zip(self.teams, rng.sample(range(L + 1), n)))
        ranked = sorted(self.teams, key=lambda x: -self.scores[x])
        self.k = rrng.choice((2, 2, 2, 2, 2, 3))  # per league, not per draw; k = 2: each team's cup outcome is exactly-one
        qual = ranked[:self.k]
        winner = rng.choice(qual)
        runner = rng.choice([x for x in qual if x != winner])
        self.classes: dict[str, dict[str, Any]] = {}
        self.cls_of: dict[tuple, str] = {}
        self.cons: list[dict[str, Any]] = []
        self.rules: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.props: list[dict[str, Any]] = []
        self.a: int | None = None
        self.chain: list[str] = []
        self.pairs: list[tuple[str, str]] = []  # every X > Y comparison (rules 4 and 6)
        self.conditionals: list[tuple[str, str]] = []
        T, W, S, Q = (lambda x, t: self.C("thr", x, t)), (lambda x: self.C("win", x)), (lambda x: self.C("runner", x)), (lambda x: self.C("qual", x))
        teams = self.teams

        for x in teams:  # rule 1
            for t in range(1, L):
                self.add(1, _implies(T(x, t + 1), T(x, t)))
        self.rules.append({"rule": 1, "type": "ladder"})
        self.add(2, _card("exactly", self.k, [Q(x) for x in teams]))
        # q and s_g are drawn low, so these links pin few scores
        self.q = rng.randint(1, min(2, *(self.scores[x] for x in qual)))  # the k-th of n distinct scores is >= n - k
        for x in teams:
            self.add(2, _implies(Q(x), T(x, self.q)))
        self.rules.append({"rule": 2, "type": "qualify", "k": self.k, "min_points": self.q})
        self.add(5, _card("exactly", 1, [W(x) for x in teams]))
        self.add(5, _card("exactly", 1, [S(x) for x in teams]))
        for x in teams:
            self.add(5, _card("atmost", 1, [W(x), S(x)]))
            self.add(5, _implies(W(x), Q(x)))
            self.add(5, _implies(S(x), Q(x)))
            if self.k == 2:  # implied: the two qualifiers are the two finalists (stated for the suite's group pool)
                self.add(5, _card("exactly", 1, [W(x), S(x), ir.neg(Q(x))]))
        conds = [(winner, runner)] if rng.random() < 0.5 else []  # true conditional, else only vacuous ones
        for x in rng.sample([x for x in teams if x != winner], rng.choice((1, 2)) - len(conds)):
            conds.append((x, rng.choice([y for y in teams if y != x])))
        for x, y in conds:
            self.add(5, _implies(W(x), S(y)))
        self.conditionals = conds
        self.rules.append({"rule": 5, "type": "placement", "conditionals": [list(c) for c in conds]})

        def count(t: int) -> int:
            return sum(self.scores[x] >= t for x in teams)

        # the chain (2-3 members, at most n - k, never fewer than 2: an ordered chain needs two) is drawn before rule 3, whose
        # counts are clamped against its length: the atleast count is met by the teams outside the chain, and the atmost count
        # leaves room for the whole chain (else chain leagues pin scores)
        chain = sorted(rng.sample(teams, max(2, min(n - self.k, rng.choice((2, 2, 3))))),
                       key=lambda x: -self.scores[x]) if 6 in rules else []
        if 3 in rules:
            cand = [t for t in range(1, L + 1) if 1 <= count(t) <= n - 1]
            a, b = rng.choice(cand), rng.choice(cand)
            tight = 0.0 if 6 in rules else 0.4  # tight counts on top of a chain pin almost every score
            k1 = count(a) if count(a) == 1 or rng.random() < tight else count(a) - 1
            k2 = count(b) if count(b) == n - 1 or rng.random() < tight else count(b) + 1
            if 6 in rules:
                k1, k2 = min(k1, n - len(chain)), max(k2, len(chain))
            self.add(3, _card("atleast", k1, [T(x, a) for x in teams]))
            self.add(3, _card("atmost", k2, [T(x, b) for x in teams]))
            self.count = {"atleast": {"k": k1, "t": a}, "atmost": {"k": k2, "t": b}}
            self.rules.append({"rule": 3, "type": "count", **self.count})
        if 4 in rules:
            ordered = [(x, y) for x in teams for y in teams if self.scores[x] > self.scores[y]]
            pairs = rng.sample(ordered, 1)  # each strict pair fixes T(X,1) and not T(Y,L) in every world
            for x, y in pairs:
                self.outscores(4, x, y)
            self.rules.append({"rule": 4, "type": "outscores", "pairs": [list(p) for p in pairs]})
        if 6 in rules:
            # links are "at least as many points" (T(Y,t) -> T(X,t)): strict links inside 0..L would pin most scores
            for x, y in zip(chain, chain[1:]):
                for t in range(1, L + 1):
                    self.add(6, _implies(T(y, t), T(x, t)))
            # a is drawn from 1..the winner's score (the gold winner satisfies W -> T(., a); the exact score would be a gold value)
            self.a = a = rng.randint(1, self.scores[winner])
            for x in teams:
                self.add(6, _implies(W(x), T(x, a)))
            self.chain = chain
            self.rules.append({"rule": 6, "type": "chain", "teams": chain, "a": a})
        seeds: dict[str, str] = {}
        self.seed_min: dict[str, int] = {}
        if 7 in rules:
            members: list[list[str]] = []
            while len(members) < SUBGROUPS:  # every later group shares 1-2 teams with the first; no repeated set
                mem = rng.sample(teams, rng.choice((3, 3, 4)) if n > 4 else 3)
                if (not members or 1 <= len(set(mem) & set(members[0])) <= 2) and set(mem) not in map(set, members):
                    members.append(mem)
            themes = rng.sample(THEMES, SUBGROUPS)
            first_named = rng.random() < 0.5
            for j, (theme, mem) in enumerate(zip(themes, members)):
                gid = f"{self.id}-G{j + 1}"
                named = first_named == (j % 2 == 0)
                self.groups.append({"id": gid, "members": list(mem), "named": named, "theme": theme[0],
                                    "phrases": list(theme[1]),
                                    "title": f"Group {theme[0]}" if named else theme[1][0]})
                self.add(7, _card("exactly", 1, [self.C("seed", x, g=gid) for x in mem]))
            for x in teams:  # a team in several groups holds at most one seed
                held = [g["id"] for g in self.groups if x in g["members"]]
                if len(held) > 1:
                    self.add(7, _card("atmost", 1, [self.C("seed", x, g=gid) for gid in held]))
            for _ in range(50):  # gold seeds: distinct members with points (at most one team has 0)
                pick = {g["id"]: rng.choice([x for x in g["members"] if self.scores[x] >= 1]) for g in self.groups}
                if len(set(pick.values())) == len(pick):
                    seeds.update(pick)
                    break
            else:
                raise ValueError(f"{self.id}: no distinct gold seeds")
            for g in self.groups:
                gid = g["id"]
                self.seed_min[gid] = s = rng.randint(1, min(2, self.scores[seeds[gid]]))
                for x in g["members"]:
                    self.add(7, _implies(self.C("seed", x, g=gid), T(x, s)))
            for g1, g2 in combinations(self.groups, 2):  # one overlap entry per pair of groups that share teams
                shared = [x for x in g1["members"] if x in g2["members"]]
                if shared:
                    self.rules.append({"rule": 7, "type": "overlap", "groups": [g1["id"], g2["id"]], "shared": shared,
                                       "seed_min_points": {g["id"]: self.seed_min[g["id"]] for g in (g1, g2)}})
        self.gold = {"scores": dict(self.scores), "W": winner, "S": runner, "Q": sorted(qual), "seeds": seeds}
        if 8 in rules:  # 2-3 distinct propositions (a repeated (type, team, t) is redrawn)
            # "top half" has one threshold M per league, and the readings disagree in the gold world: either the top-half teams
            # are non-qualifiers with s >= M (M <= the best non-qualifier's score), or qualifiers with s < M
            non_q = [self.scores[x] for x in teams if x not in qual]
            q_low = min(self.scores[x] for x in qual)
            modes = [m for m, ok in (("out", max(non_q) >= 1), ("in", q_low < L)) if ok]
            if not modes:
                raise ValueError(f"{self.id}: no top-half reading disagrees in the gold world")
            if "out" in modes and ("in" not in modes or rng.random() < 0.5):
                M = rng.randint(1, max(non_q))
                tops = [x for x in teams if x not in qual and self.scores[x] >= M]
            else:
                M = rng.randint(q_low + 1, L)
                tops = [x for x in qual if self.scores[x] < M]
            brk = [x for x in teams if 1 <= self.scores[x] < L]  # break: the literal reading is final points s + 1, the alt s
            if not brk:
                raise ValueError(f"{self.id}: no team can carry a break proposition")
            want = rng.randint(2, 3)
            for _ in range(100):
                if len(self.props) == want:
                    break
                if tops and rng.random() < 0.5:
                    x = rng.choice(tops)
                    prop = {"type": "tophalf", "team": x, "t": None, "base": self.cls_of[("qual", x, None, None)],
                            "alt": self.cls_of[("thr", x, M, None)]}
                else:
                    x = rng.choice(brk)
                    s = self.scores[x]
                    prop = {"type": "break", "team": x, "t": s + 1, "base": self.cls_of[("thr", x, s + 1, None)],
                            "alt": self.cls_of[("thr", x, s, None)]}
                if all((p["type"], p["team"], p["t"]) != (prop["type"], x, prop["t"]) for p in self.props):
                    self.props.append({**prop, "j": len(self.props)})
            if len(self.props) < 2:
                raise ValueError(f"{self.id}: fewer than two distinct ambiguous propositions")
        if not self.check(self.gold):
            raise ValueError(f"{self.id}: the gold world violates a rule")
        brng = random.Random(f"hard:{split}:{seed}:bank:{li}:{attempt}")
        bank = {self._key(self.gold): self.gold}
        for i in range(BANK_TRIES):  # alternate fresh samples and random-walk steps from a known valid world
            w = self._sample(brng) if i % 2 else self._step(brng.choice(list(bank.values())), brng)
            if w is not None:
                bank.setdefault(self._key(w), w)
        self.bank = [bank[k] for k in sorted(bank, key=lambda k: (k != self._key(self.gold), k))]  # gold first
        self.bank_env = [self.values(w) for w in self.bank]
        # classes seen with both values: provably not fixed by the base (forced classes make trivial exam items)
        self.free = {c for c in self.classes if len({env[c] for env in self.bank_env}) == 2}

    def C(self, kind: str, team: str, t: int | None = None, g: str | None = None) -> dict[str, Any]:
        """Class ref; the class is created on first use, so every class is in some constraint."""
        key = (kind, team, t, g)
        if key not in self.cls_of:
            name = "c" + opaque(self.split, self.seed, "class", self.li, kind, team, t, g)
            self.cls_of[key] = name
            self.classes[name] = {"kind": kind, "team": team, "t": t, "subgroup": g}
        return _ref(self.cls_of[key])

    def add(self, rule: int, expr: dict[str, Any], interps: list[str] | None = None) -> None:
        self.cons.append({"rule": rule, "expr": expr, "interps": interps})

    def outscores(self, rule: int, x: str, y: str) -> None:
        for t in range(1, self.L):
            self.add(rule, _implies(self.C("thr", y, t), self.C("thr", x, t + 1)))
        self.pairs.append((x, y))

    def values(self, w: dict[str, Any]) -> dict[str, bool]:
        """Class values in world w."""
        out = {}
        for name, c in self.classes.items():
            kind, x = c["kind"], c["team"]
            out[name] = (w["scores"][x] >= c["t"] if kind == "thr" else w["W"] == x if kind == "win" else
                         w["S"] == x if kind == "runner" else x in w["Q"] if kind == "qual" else
                         w["seeds"][c["subgroup"]] == x)
        return out

    def check(self, w: dict[str, Any]) -> bool:
        """Every class-level constraint holds in w."""
        env = self.values(w)
        return all(_ev(c["expr"], env) for c in self.cons)

    def _key(self, w: dict[str, Any]) -> tuple:
        env = self.values(w)
        return tuple(env[c] for c in sorted(env))

    def _sample(self, rng: random.Random) -> dict[str, Any] | None:
        """A random world repaired towards the comparisons; None unless every constraint holds."""
        teams, L = self.teams, self.L
        vals = rng.sample(range(L + 1), self.n) if rng.random() < 0.5 else [rng.randint(0, L) for _ in teams]
        scores = dict(zip(teams, vals))
        if self.chain:
            for x, v in zip(self.chain, sorted((scores[x] for x in self.chain), reverse=True)):
                scores[x] = v
        for x, y in self.pairs:
            if scores[x] <= scores[y]:
                scores[x], scores[y] = scores[y], scores[x]
        free = [x for x in teams if scores[x] >= self.q]
        if len(free) < self.k:
            return None
        q = rng.sample(free, self.k)
        can_win = [x for x in q if self.a is None or scores[x] >= self.a]
        if not can_win:
            return None
        win = rng.choice(can_win)
        runner = dict(self.conditionals).get(win) or rng.choice([x for x in q if x != win])
        seeds: dict[str, str] = {}
        for g in self.groups:
            cand = [x for x in g["members"] if scores[x] >= self.seed_min[g["id"]] and x not in seeds.values()]
            if not cand:
                return None
            seeds[g["id"]] = rng.choice(cand)
        w = {"scores": scores, "W": win, "S": runner, "Q": sorted(q), "seeds": seeds}
        return w if runner != win and self.check(w) else None

    def _step(self, w: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
        """One to three random edits of a valid world (a score +-1, a qualifier swap, the winner, the runner-up or a
        seed); None unless every constraint holds."""
        w = {"scores": dict(w["scores"]), "W": w["W"], "S": w["S"], "Q": list(w["Q"]), "seeds": dict(w["seeds"])}
        for _ in range(rng.randint(1, 3)):
            edit, x = rng.randrange(5), rng.choice(self.teams)
            if edit == 0:
                w["scores"][x] = min(self.L, max(0, w["scores"][x] + rng.choice((-1, 1))))
            elif edit == 1 and x not in w["Q"]:
                w["Q"][rng.randrange(self.k)] = x
            elif edit == 2:
                w["W"] = x
            elif edit == 3:
                w["S"] = x
            elif self.groups:
                g = rng.choice(self.groups)
                w["seeds"][g["id"]] = rng.choice(g["members"])
        w["Q"] = sorted(w["Q"])
        return w if self.check(w) else None

    def world_json(self) -> dict[str, Any]:
        rules = list(self.rules)
        if self.props:
            rules.append({"rule": 8, "type": "ambiguous", "props": [
                {"market": p["market"], "kind": p["type"], "team": p["team"], "t": p["t"], "missing": MISSING[p["type"]],
                 "negated": p["neg"], "readings": {r: dict(self.classes[p[r]], **{"class": p[r]}) for r in ("base", "alt")}}
                for p in self.props]})
        return {"id": self.id, "name": self.ln, "teams": self.teams, "L": self.L, "scores": self.scores,
                "winner": self.gold["W"], "runner_up": self.gold["S"], "qualifiers": self.gold["Q"],
                "seeds": self.gold["seeds"], "rules": sorted(rules, key=lambda r: r["rule"]),
                "subgroups": [{k: g[k] for k in ("id", "members", "named", "title", "theme")} for g in self.groups],
                "paraphrase_pairs": self.paraphrase_pairs, "fixed_share": self.fixed_share}


def _league(split: str, seed: int, li: int, name: str, pool: list[str]) -> League:
    """The first seeded draw of league li whose bank shows no class fixed by the base (TARGET_FIXED = 0: a forced outcome
    would be a constant of the suite); else the least fixed of LEAGUE_TRIES draws, and ValueError when even that one is
    above MAX_FIXED (also 0). The rule set is fixed per league; a redraw changes the gold world and the rule parameters
    only. The estimate is recorded as lg.fixed_share (world.json "fixed_share")."""
    best = None
    for attempt in range(LEAGUE_TRIES):
        try:
            lg = League(split, seed, li, name, pool, attempt)
        except ValueError:  # a draw whose gold seeds cannot be distinct
            continue
        lg.fixed_share = round(1 - len(lg.free) / len(lg.classes), 4)
        if best is None or lg.fixed_share < best.fixed_share:
            best = lg
        if lg.fixed_share <= TARGET_FIXED:
            return lg
    if best is not None and best.fixed_share <= MAX_FIXED:
        return best
    raise ValueError(f"league {li}: no draw of {LEAGUE_TRIES} fixes at most {MAX_FIXED:.0%} of its classes "
                     f"(best {best.fixed_share if best else 1:.1%})")


# ---------------------------------------------------------------- rule card

CARD_HIDDEN = ("scores", "winner", "runner_up", "qualifiers", "seeds")  # gold: the card reads the rule list only
THEME_PHRASES = dict(THEMES)  # theme name -> its three scope phrases (static vocabulary, not gold)
CARD_MAX = 2000  # the corpus `context` cap (docs/mentat_hard_contracts.md)


def _pts(v: int) -> str:
    return f"{v} point" if v == 1 else f"{v} points"


def _teams(v: int) -> str:
    return "team" if v == 1 else "teams"


def card_sentences(world: dict[str, Any]) -> list[tuple[str, str, list[int]]]:
    """The rule card of a league as (tag, sentence, lg.cons indices the sentence states). The input is world_json() without
    CARD_HIDDEN, so the card reads the rules, the teams, the name, L and the static theme phrases only. Indices follow
    League's emission order (rule 1 first, with no sentence). The tags name the template; the tests check each tagged group
    against the constraints it claims. Rule 8 has no group: its readings are sentences without indices."""
    hidden = [k for k in CARD_HIDDEN if k in world]
    if hidden:
        raise ValueError(f"the rule card reads the rules only, not {hidden}")
    ln, L, teams = world["name"], world["L"], world["teams"]
    n = len(teams)
    rules = lambda kind: [r for r in world["rules"] if r["type"] == kind]  # noqa: E731
    out: list[tuple[str, str, list[int]]] = [
        ("header", f"Rules of the {ln} League and {ln} Cup. Teams: {', '.join(teams)}.", [])]
    at = n * (L - 1)  # the ladder (rule 1) is emitted first, with no sentence

    def take(count: int) -> list[int]:  # the next `count` constraints, in emission order
        nonlocal at
        at += count
        return list(range(at - count, at))

    qual = rules("qualify")[0]
    k = qual["k"]
    idx = take(1 + n)  # exactly(k, Q), then Q(x) -> T(x, q)
    out.append(("qualify", f"Exactly {k} teams qualify for the playoffs; each qualifier has at least "
                           f"{_pts(qual['min_points'])}.", idx))
    place = rules("placement")[0]
    per = 4 if k == 2 else 3  # per team: at most one of W and S, W -> Q, S -> Q, and with k = 2 exactly one of W, S, not Q
    idx = take(2 + per * n + len(place["conditionals"]))  # exactly one W, exactly one S, the teams, the conditionals
    out.append(("placement", "The Cup has exactly one winner and one runner-up, two different teams, both playoff qualifiers.",
                idx[:2] + [idx[2 + per * j + m] for j in range(n) for m in range(3)]))
    if k == 2:
        out.append(("finalists", f"So the {k} qualifiers are exactly the Cup winner and the runner-up.",
                    [idx[2 + per * j + 3] for j in range(n)]))
    for c, (x, y) in enumerate(place["conditionals"]):
        out.append((f"conditional:{x}:{y}", f"If {x} wins the Cup, {y} is runner-up.", [idx[2 + per * n + c]]))
    for c in rules("count"):
        a, b = c["atleast"], c["atmost"]
        idx = take(1)
        out.append(("count:atleast", f"At least {a['k']} {_teams(a['k'])} {'has' if a['k'] == 1 else 'have'} "
                                     f"{_pts(a['t'])} or more.", idx))
        idx = take(1)
        out.append(("count:atmost", f"At most {b['k']} {_teams(b['k'])} {'has' if b['k'] == 1 else 'have'} "
                                    f"{_pts(b['t'])} or more.", idx))
    for o in rules("outscores"):
        for x, y in o["pairs"]:
            idx = take(L - 1)
            out.append((f"outscores:{x}:{y}", f"For each t from 1 to {L - 1}: if {y} has at least t points, {x} has at "
                                              f"least t + 1.", idx))
    for c in rules("chain"):
        for x, y in zip(c["teams"], c["teams"][1:]):
            idx = take(L)
            out.append((f"link:{x}:{y}", f"{x} has at least as many points as {y}.", idx))
        idx = take(n)  # W(x) -> T(x, a) for every team
        out.append(("winner-bound", f"The Cup winner has at least {_pts(c['a'])}.", idx))
    groups = world["subgroups"]
    smin = {g: s for r in rules("overlap") for g, s in r["seed_min_points"].items()}
    exactly = take(len(groups))  # one exactly-one per group, emitted first
    multi = [x for x in teams if sum(x in g["members"] for g in groups) > 1]
    at_most = take(len(multi))  # no team is the seed of two groups
    for j, g in enumerate(groups):
        idx = [exactly[j]] + take(len(g["members"]))  # seed(g, x) -> T(x, s) for each member
        phrases = "; ".join(THEME_PHRASES[g["theme"]])
        out.append((f"group:{g['id']}", f"Group {g['theme']} ({phrases}): {', '.join(g['members'])}. Exactly one of them is "
                                        f"the seed, with at least {_pts(smin[g['id']])}.", idx))
    if multi:
        out.append(("group-atmost", "No team is the seed of two groups.", at_most))
    kinds = {p["kind"]: p for r in rules("ambiguous") for p in r["props"]}
    if "break" in kinds:
        out.append(("reading:break", "'By the break' has two readings: (1) final points; (2) final points plus the 1 point "
                                     "every team gets in the Shield round.", []))
    if "tophalf" in kinds:
        out.append(("reading:tophalf", f"'Top half' has two readings: (1) a playoff place; (2) at least "
                                       f"{_pts(kinds['tophalf']['readings']['alt']['t'])}.", []))
    return out


def render_card(world: dict[str, Any]) -> tuple[str, list[int]]:
    """The rule card of a league (the `context` of its outcomes, one sentence per line) and the lg.cons indices its sentences
    state (rules 2-7, each constraint once; see card_sentences)."""
    sents = card_sentences(world)
    return "\n".join(s for _, s, _ in sents), sorted(i for _, _, idx in sents for i in idx)


# ---------------------------------------------------------------- markets

def _families(rng: random.Random, split: str, avoid: set[tuple[int, bool]] = frozenset()) -> int:
    """A family of the split whose (style, held-out) wording differs from every style in `avoid`."""
    top = FAMILIES if split == "test" else DEV_FAMILIES
    while True:
        f = rng.randint(1, top)
        style, _, held = family_style(f)
        if (style, held) not in avoid:
            return f


def _markets(lg: League, share: float = PARAPHRASE_SHARE, third: float = THIRD_SHARE) -> list[dict[str, Any]]:
    """Markets of a league (one per class; a `share` of classes get a second market and a `third` share a third one,
    each in a different style; plus the ambiguous propositions); each market carries its two outcome rows (corpus, binding, meta) and fills lg.recs, lg.paraphrase_pairs and the props."""
    split, seed = lg.split, lg.seed
    groups = {g["id"]: g for g in lg.groups}
    markets: list[dict[str, Any]] = []
    lg.recs = {c: {False: [], True: []} for c in lg.classes}
    lg.paraphrase_pairs = []

    def make(key: tuple, kind: str, fam: int, team: str, t: int | None, g: str | None, rng: random.Random,
             cls: str | None, paraphrase_of: str | None, gloss: bool = False) -> dict[str, Any]:
        mid = opaque(split, seed, "market", lg.li, *key)
        scope, gname, named = "", "", False
        if g is not None:
            grp = groups[g]
            named, gname = grp["named"], grp["theme"]
            scope = f"in Group {gname}" if named else "among " + rng.choice(grp["phrases"])
        question, title, neg = render(("g" + kind) if gloss else kind, fam, team, lg.ln, t, scope, gname, named)
        if len(question) > MAX_QUESTION:
            raise ValueError(f"question over {MAX_QUESTION} characters: {question!r}")
        meta_kind = "dated" if kind in MISSING else kind
        rows = []
        for idx, label in ((1, "Yes"), (2, "No")):
            oid = f"{VENUE}:{mid}:{idx}"
            negated = neg if idx == 1 else not neg
            if cls is None:
                bind = {"id": oid, "status": "unresolved", "class": None, "negated": False, "missing": [MISSING[kind]]}
            else:
                bind = {"id": oid, "status": "bound", "class": cls, "negated": negated, "missing": []}
                lg.recs[cls][negated].append(oid)
            rows.append(({"id": oid, "text": question, "label": label, "event_title": title, "controlled": True}, bind,
                         {"id": oid, "market": mid, "league": lg.id, "kind": meta_kind, "team": team, "t": t,
                          "subgroup": g, "class": cls or "u" + mid, "family": fam, "test_only": fam > DEV_FAMILIES,
                          "paraphrase_of": paraphrase_of, "gloss": gloss}))
        return {"mid": mid, "neg": neg, "rows": rows, "question": question}

    for cls, c in lg.classes.items():
        rng = random.Random(f"hard:{split}:{seed}:class:{cls}")
        key = (c["kind"], c["team"], c["t"], c["subgroup"])
        r = rng.random()
        styles: set[tuple[int, bool]] = set()
        mids: list[str] = []
        for j, tag in enumerate(("p", "x", "y")[:1 + (r < share) + (r < min(third, share))]):
            fam = _families(rng, split, styles)
            styles.add(family_style(fam)[::2])
            m = make(key + (tag,), c["kind"], fam, c["team"], c["t"], c["subgroup"], rng, cls, mids[0] if j else None)
            markets.append(m)
            mids.append(m["mid"])
        grng = random.Random(f"hard:{split}:{seed}:gloss:{cls}")
        if c["kind"] in ("thr", "qual") and grng.random() < GLOSS_SHARE:  # a glossed market: the phrase with its reading
            fam = _families(grng, split, styles)
            styles.add(family_style(fam)[::2])
            m = make(key + ("g",), c["kind"], fam, c["team"], c["t"], None, grng, cls, mids[0], gloss=True)
            markets.append(m)
            mids.append(m["mid"])
        lg.paraphrase_pairs += [[a, b] for i, a in enumerate(mids) for b in mids[i + 1:]]
    for p in lg.props:
        rng = random.Random(f"hard:{split}:{seed}:prop:{lg.li}:{p['j']}")
        m = make(("amb", p["j"]), p["type"], _families(rng, split), p["team"], p["t"], None, rng, None, None)
        markets.append(m)
        yes, no = (r[0]["id"] for r in m["rows"])
        p.update(market=m["mid"], yes=yes, no=no, neg=m["neg"])
        meaning = ir.neg(ir.var(yes)) if m["neg"] else ir.var(yes)  # the proposition as worded positively
        lg.add(8, {"op": "iff", "a": ir.var(yes), "b": ir.neg(ir.var(no))})
        lg.add(8, {"op": "iff", "a": meaning, "b": _ref(p["base"])}, ["base"])
        lg.add(8, {"op": "iff", "a": meaning, "b": _ref(p["alt"])}, ["alt"])
    lg.card, lg.card_covered = render_card({k: v for k, v in lg.world_json().items() if k not in CARD_HIDDEN})
    for m in markets:
        for row in m["rows"]:
            row[0]["context"] = lg.card  # every outcome of a league shares its rule card (corpus field `context`)
    lg.outcomes = sorted(r[0]["id"] for m in markets for r in m["rows"])
    return markets


# ---------------------------------------------------------------- exam

class _Exam:
    """Query builders. A literal is (class, value); `lit` renders it as a var of a record with that meaning."""

    def __init__(self, rng: random.Random):
        self.rng = rng

    def lit(self, lg: League, cls: str, value: bool) -> dict[str, Any]:
        return ir.var(self.rng.choice(lg.recs[cls][not value]))

    def plit(self, p: dict[str, Any], value: bool) -> dict[str, Any]:
        """The ambiguous proposition (as worded positively) has `value`."""
        return ir.var(p["yes"] if value != p["neg"] else p["no"])

    def pool(self, lg: League, w: dict[str, Any]) -> list[tuple[str, bool, str]]:
        """Literals true in world w on classes not fixed by the base, at most one per team and kind:
        (class, value, category)."""
        rng, out = self.rng, []
        for x in lg.teams:
            s = w["scores"][x]
            if s >= 1:
                out.append((lg.cls_of[("thr", x, rng.randint(1, s), None)], True, "score"))
            if s < lg.L:
                out.append((lg.cls_of[("thr", x, rng.randint(s + 1, lg.L), None)], False, "score"))
            out.append((lg.cls_of[("win", x, None, None)], w["W"] == x, "placement"))
            out.append((lg.cls_of[("runner", x, None, None)], w["S"] == x, "placement"))
            out.append((lg.cls_of[("qual", x, None, None)], x in w["Q"], "qualify"))
        for g in lg.groups:
            for x in g["members"]:
                out.append((lg.cls_of[("seed", x, None, g["id"])], w["seeds"][g["id"]] == x, "overlap"))
        return [lit for lit in out if lit[0] in lg.free]

    def pick(self, pool: list[tuple[str, bool, str]], lo: int, hi: int) -> list[tuple[str, bool, str]]:
        """lo..hi literals from the pool (fewer if the pool is smaller), one from each of two categories first."""
        rng = self.rng
        cats = sorted({c for _, _, c in pool})
        first = [rng.choice([lit for lit in pool if lit[2] == c]) for c in rng.sample(cats, min(2, len(cats)))]
        m = rng.randint(min(lo, len(pool)), min(hi, len(pool)))
        picks = first + rng.sample([lit for lit in pool if lit not in first], max(0, m - len(first)))
        rng.shuffle(picks)
        return picks

    def closure_blocks(self, lg: League, w: dict[str, Any]) -> list[tuple[str, bool, str]]:
        """8-16 premises true in world w, made of whole blocks whose premises are each needed for an entailment (a
        dropped premise loses it): not-W for every team but the winner (entails the winner), the same for the
        runner-up, the qualifiers and each group's seed (count arguments), and score pins T(x,s), not T(x,s+1) (with a
        chain they propagate across its links). Only classes the base leaves free; at least two kinds of block."""
        rng, c, free = self.rng, lg.cls_of, lg.free
        units: list[tuple[str, list[tuple[str, bool]]]] = []

        def block(kind: str, target: str, others: list[str]) -> None:
            lits = [(o, False) for o in others if o in free]
            if target in free and len(lits) >= 2:
                units.append((kind, lits))

        teams = lg.teams
        block("placement", c[("win", w["W"], None, None)], [c[("win", x, None, None)] for x in teams if x != w["W"]])
        if w["W"] not in dict(lg.conditionals):  # a conditional on the winner already fixes the runner-up
            block("placement", c[("runner", w["S"], None, None)],
                  [c[("runner", x, None, None)] for x in teams if x not in (w["W"], w["S"])])
        if len(w["Q"]) > 2:  # with k = 2 the finalists already fix the qualifiers
            block("qualify", c[("qual", w["Q"][0], None, None)], [c[("qual", x, None, None)] for x in teams if x not in w["Q"]])
        for g in lg.groups:
            sd = w["seeds"][g["id"]]
            block("overlap", c[("seed", sd, None, g["id"])], [c[("seed", x, None, g["id"])] for x in g["members"] if x != sd])
        for x in teams:
            sc = w["scores"][x]
            pin = [(c[("thr", x, sc, None)], True)] if sc >= 1 else []
            pin += [(c[("thr", x, sc + 1, None)], False)] if sc < lg.L else []
            pin = [lit for lit in pin if lit[0] in free]
            if pin:
                units.append(("score", pin))
        rng.shuffle(units)
        units.sort(key=lambda u: u[0] == "score")  # count blocks first, then pins fill up to the target
        m = rng.randint(8, 16)
        picks: list[tuple[str, bool, str]] = []
        for kind, lits in units:
            if len(picks) + len(lits) <= 16 and (len(picks) < m or len({k for *_, k in picks}) < 2):
                picks += [(cl, v, kind) for cl, v in lits]
        if len({k for *_, k in picks}) < 2:
            return []
        rng.shuffle(picks)
        return picks

    def amb_lit(self, lg: League, lits: list[tuple[str, bool]], joint: bool) -> dict[str, Any] | None:
        """A literal on an ambiguous proposition of lg whose two readings agree on the set's answer, or None. joint: some
        bank world satisfies the whole set under each reading (a SAT set stays SAT in both interpretations). Otherwise
        (a joint-only UNSAT set, which stays UNSAT with any extra literal): under each reading the literal is witnessed
        with every member, so no pair becomes UNSAT or UNRESOLVED."""
        opts = [(p, v) for p in lg.props for v in (True, False)]
        self.rng.shuffle(opts)
        for p, v in opts:
            readings = [(p["base"], v), (p["alt"], v)]
            if joint:
                ok = all(self.witnessed(lg, lits + [r]) for r in readings)
            else:
                ok = all(self.witnessed(lg, [r, other]) for r in readings for other in lits)
            if ok:
                return self.plit(p, v)
        return None

    def twin(self, lg: League, need: Counter, dated: bool) -> tuple[League, list[tuple[str, bool]], Any] | None:
        """(lg, literals, ambiguous literal or None): for each kind, need[kind] free classes of lg, each literal as it is
        in one bank world (so the set is SAT), plus an ambiguous literal whose readings agree when `dated`; None when
        lg lacks the classes or no world gives a usable ambiguous literal."""
        free: dict[str, list[str]] = defaultdict(list)
        for c in sorted(lg.free):
            free[lg.classes[c]["kind"]].append(c)
        if any(len(free[k]) < n for k, n in need.items()) or (dated and not lg.props):
            return None
        for _ in range(6):
            env = self.rng.choice(lg.bank_env)
            lits = [(c, env[c]) for k, n in sorted(need.items()) for c in self.rng.sample(free[k], n)]
            amb = self.amb_lit(lg, lits, joint=True) if dated else None
            if amb is not None or not dated:
                return lg, lits, amb
        return None

    def pad(self, lg: League, lits: list[tuple[str, bool]], n: int) -> list[tuple[str, bool]]:
        """lits plus up to n literals true in the gold world, other categories first, each pair with every literal
        witnessed by a bank world, so the set stays UNSAT only jointly while its size and category count vary."""
        kinds = {lg.classes[c]["kind"] for c, _ in lits}
        cand = [(c, v) for c, v, _ in self.pool(lg, lg.gold) if c not in {cl for cl, _ in lits}]
        self.rng.shuffle(cand)
        cand.sort(key=lambda lit: lg.classes[lit[0]]["kind"] in kinds)
        out = list(lits)
        for lit in cand:
            if len(out) - len(lits) >= n:
                break
            if all(self.witnessed(lg, [lit, other]) for other in out):
                out.append(lit)
        return out

    @staticmethod
    def witnessed(lg: League, lits: list[tuple[str, bool]]) -> bool:
        """Some bank world satisfies every literal (a proof of satisfiability)."""
        return any(all(env[c] == v for c, v in lits) for env in lg.bank_env)

    # -- feasibility: joint-only UNSAT constructions (each proved by a cardinality argument)
    def unsat_options(self, lg: League) -> list[tuple[str, list[tuple[str, bool]]]]:
        rng, teams, c = self.rng, lg.teams, lg.cls_of
        T = lambda x, t: c[("thr", x, t, None)]  # noqa: E731
        W, S, Q = (lambda x: c[("win", x, None, None)]), (lambda x: c[("runner", x, None, None)]), (lambda x: c[("qual", x, None, None)])
        opts = [("qualify_overflow", [(Q(x), True) for x in rng.sample(teams, lg.k + 1)]),
                ("winner_none", [(W(x), False) for x in teams]),
                ("runner_none", [(S(x), False) for x in teams])]
        if lg.n - lg.k + 1 >= 3:
            opts.append(("qualify_underflow", [(Q(x), False) for x in rng.sample(teams, lg.n - lg.k + 1)]))
        for x, y in lg.conditionals:
            z = rng.choice([t for t in teams if t != y])
            opts.append(("conditional_placement", [(W(t), False) for t in teams if t != x] + [(S(z), True)]))
        if lg.a is not None:
            x = rng.choice(teams)
            others = rng.sample([t for t in teams if t != x], lg.k)
            opts.append(("chain_qualify", [(W(x), True)] + [(Q(t), True) for t in others]))
        if 3 in lg.optional:
            k1, a = lg.count["atleast"]["k"], lg.count["atleast"]["t"]
            if lg.n - k1 + 1 >= 3:
                opts.append(("atleast_ladder", [(T(x, rng.randint(1, a)), False) for x in rng.sample(teams, lg.n - k1 + 1)]))
            k2, b = lg.count["atmost"]["k"], lg.count["atmost"]["t"]
            if k2 + 1 >= 3:
                opts.append(("atmost_ladder", [(T(x, rng.randint(b, lg.L)), True) for x in rng.sample(teams, k2 + 1)]))
        if lg.groups:
            g = rng.choice(lg.groups)
            opts.append(("seed_none", [(c[("seed", x, None, g["id"])], False) for x in g["members"]]))
            g1, g2 = rng.choice([(a, b) for a in lg.groups for b in lg.groups
                                 if a is not b and set(a["members"]) & set(b["members"])])
            x = rng.choice([x for x in g1["members"] if x in g2["members"]])
            opts.append(("seed_overlap", [(c[("seed", x, None, g1["id"])], True)]
                         + [(c[("seed", y, None, g2["id"])], False) for y in g2["members"] if y != x]))
        return opts


def _exam(leagues: list[League], split: str, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ex = _Exam(random.Random(f"hard:{split}:{seed}:exam"))
    rng = ex.rng
    scale = len(leagues) / LEAGUES
    counts = {f: max(1, round(n * scale)) for f, n in EXAM.items()}
    queries: list[dict[str, Any]] = []
    design: list[dict[str, Any]] = []

    def add(family: str, lg: League, tags: list[str], expect: Any, **body: Any) -> None:
        qid = f"{family}-{sum(q['family'] == family for q in queries) + 1:03d}"
        queries.append({"qid": qid, "family": family, **body})
        design.append({"qid": qid, "family": family, "premises": None, "tags": tags, "anchor": True,
                       "league": lg.id, "expect": expect})

    def cycle(i: int) -> League:
        return leagues[i % len(leagues)]

    for i in range(counts["closure"]):
        for j in range(4 * len(leagues)):  # another world, then another league, until 8 premises fit
            lg = cycle(i + j // 4)
            picks = ex.closure_blocks(lg, rng.choice(lg.bank))
            if len(picks) >= 8:
                break
        else:
            raise ValueError("no league yields 8 closure premises")
        add("closure", lg, sorted({c for _, _, c in picks}) + ["joint"], None,
            hyp=[ex.lit(lg, c, v) for c, v, _ in picks], universe=lg.outcomes)

    kinds = ("unsat", "sat", "unsat", "sat", "unresolved")
    with_props = [lg for lg in leagues if lg.props]  # never empty: league 0 draws rule 8
    i = 0
    last_unsat: tuple[int, Counter, bool] | None = None  # (league index, kind multiset, dated) of the latest UNSAT set
    for j in range(counts["feasibility"]):
        kind = kinds[j % len(kinds)]
        if kind == "unresolved":
            lg = with_props[j % len(with_props)]
            p = rng.choice(lg.props)
            vb, va = lg.values(lg.gold)[p["base"]], lg.values(lg.gold)[p["alt"]]
            extra = [(c, v) for c, v, _ in ex.pick(ex.pool(lg, lg.gold), 2, 3)]
            assume = [ex.plit(p, vb), ex.lit(lg, p["alt"], va)] + [ex.lit(lg, c, v) for c, v in extra]
            add("feasibility", lg, ["unresolved", "ambiguous_" + p["type"]], {"decision": "UNRESOLVED"}, assume=assume)
            continue
        # SAT and UNSAT sets share one draw of size and of single-category, so neither predicts the answer
        size, single = rng.randint(3, 9), rng.random() < 0.25
        if kind == "sat" and last_unsat is not None:
            # the twin of the latest UNSAT set: the same multiset of proposition kinds (and a dated literal when it has
            # one), all true in one valid world, so the kinds a question names do not predict SAT or UNSAT
            li, need, dated = last_unsat
            last_unsat = None
            twin = next((t for k in range(len(leagues)) for t in [ex.twin(leagues[(li + k) % len(leagues)], need, dated)]
                         if t is not None), None)
            if twin is not None:
                lg, lits, amb = twin
                assume = [ex.lit(lg, c, v) for c, v in lits]
                if amb:
                    assume.insert(rng.randrange(len(assume) + 1), amb)
                cats = sorted({CATEGORY[lg.classes[c]["kind"]] for c, _ in lits})
                add("feasibility", lg, ["sat", "twin"] + cats + (["ambiguous"] if amb else []), {"decision": "SAT"},
                    assume=assume)
                continue
        if kind == "sat":
            lg = cycle(i)
            i += 1
            w = lg.bank[1 + rng.randrange(len(lg.bank) - 1)] if len(lg.bank) > 1 else lg.gold
            pool = ex.pool(lg, w)
            cats = sorted({k for *_, k in pool if sum(lit[2] == k for lit in pool) >= 3})
            if cats and single:
                cat = rng.choice(cats)
                same = [lit for lit in pool if lit[2] == cat]
                picks = rng.sample(same, min(size, len(same)))
            else:
                picks = ex.pick(pool, size, size)
            assume = [ex.lit(lg, c, v) for c, v, _ in picks]
            amb = ex.amb_lit(lg, [(c, v) for c, v, _ in picks], joint=True) if rng.random() < AMBIGUOUS_SHARE else None
            if amb:
                assume.insert(rng.randrange(len(assume) + 1), amb)
            add("feasibility", lg, ["sat"] + sorted({c for _, _, c in picks}) + (["ambiguous"] if amb else []),
                {"decision": "SAT"}, assume=assume)
            continue
        found = None
        for _ in range(len(leagues)):  # a league whose constructions all lack a pair witness is skipped
            lg = cycle(i)
            i += 1
            opts = [(nm, ls) for nm, ls in ex.unsat_options(lg) if all(c in lg.free for c, _ in ls)]
            rng.shuffle(opts)
            opts.sort(key=lambda o: len(o[1]) > size)  # constructions that fit the drawn size first
            ok = [(nm, ls) for nm, ls in opts if all(ex.witnessed(lg, [p, q]) for p, q in combinations(ls, 2))][:4]
            # padding can fall short (no witnessed literal left): keep the padded set closest to the drawn size
            padded = [(nm, ls if single else ex.pad(lg, ls, size - len(ls))) for nm, ls in ok]
            found = min(padded, key=lambda o: abs(len(o[1]) - size), default=None)
            if found:
                break
        if found is None:
            raise ValueError("no league has a joint-only UNSAT construction with witnessed pairs")
        name, lits = found
        rng.shuffle(lits)
        assume = [ex.lit(lg, c, v) for c, v in lits]
        amb = ex.amb_lit(lg, lits, joint=False) if rng.random() < AMBIGUOUS_SHARE else None
        if amb:
            assume.insert(rng.randrange(len(assume) + 1), amb)
        add("feasibility", lg, ["unsat", "joint_only", name] + (["ambiguous"] if amb else []),
            {"decision": "UNSAT", "joint_only": True}, assume=assume)
        last_unsat = (leagues.index(lg), Counter(lg.classes[c]["kind"] for c, _ in lits), amb is not None)

    # the documented 4:4:2 answer mix, with the claim types taken in turn within each answer (offset for false), so
    # true and false are balanced within every claim type and the public claim field does not predict the answer
    claims, pkinds = ("exclusive", "exhaustive", "partition"), ("true", "false", "true", "false", "unresolved")
    seen_kind: Counter[str] = Counter()
    for j in range(counts["partition"]):
        kind = pkinds[j % len(pkinds)]
        claim = claims[(seen_kind[kind] + (kind == "false")) % 3]
        seen_kind[kind] += 1
        for k in range(len(leagues)):  # a league without such a construction on free classes is skipped
            lg = with_props[(j + k) % len(with_props)] if kind == "unresolved" else cycle(j + k)
            try:
                name, members, target = _partition(ex, lg, kind, claim)
                break
            except LookupError:
                continue
        else:
            raise ValueError(f"no league has a {kind} {claim} construction")
        subs = {g["id"]: g["named"] for g in lg.groups}
        used = {lg.classes[m]["subgroup"] for m in (_class_of(lg, e) for e in members) if m}
        tags = [kind, name] + sorted({"named" if subs[g] else "implicit" for g in used if g})
        add("partition", lg, tags, {"decision": "UNRESOLVED" if kind == "unresolved" else kind},
            claim=claim, members=members, target=target)

    skinds = ("equiv", "implies", "implied_by", "equiv", "excludes")
    for j in range(counts["selection"]):
        rel = skinds[j % len(skinds)]
        for k in range(len(leagues)):  # a league without a free target for this relation is skipped
            lg = cycle(j + 3 + k)
            try:
                target, must = _selection(ex, lg, rel)
                break
            except LookupError:
                continue
        else:
            raise ValueError(f"no league has a free {rel} selection target")
        add("selection", lg, [rel, "paraphrase" if any(c in lg.para for c in must) else "single"],
            {"must_include": sorted(oid for c, v in must.items() for oid in lg.recs[c][not v])},
            relation=rel, target=target, universe=lg.outcomes)
    # The builders cycle through answer kinds; the file order is shuffled and qids renumbered in that order, so a
    # query's position and number say nothing about its answer.
    pairs = list(zip(queries, design))
    random.Random(f"hard:{split}:{seed}:order").shuffle(pairs)
    seen: Counter[str] = Counter()
    for q, d in pairs:
        seen[q["family"]] += 1
        q["qid"] = d["qid"] = f"{q['family']}-{seen[q['family']]:03d}"
    return [q for q, _ in pairs], [d for _, d in pairs]


def _partition(ex: _Exam, lg: League, kind: str, claim: str) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """(name, members, target) of a `claim` whose decision is `kind`: proved by construction (true, UNRESOLVED) or
    witnessed by a bank world violating the claim (false). LookupError when the league has no such construction."""
    rng, c, teams = ex.rng, lg.cls_of, lg.teams
    W, S, Q = (lambda x: c[("win", x, None, None)]), (lambda x: c[("runner", x, None, None)]), (lambda x: c[("qual", x, None, None)])
    T = lambda x, t: c[("thr", x, t, None)]  # noqa: E731
    seed = lambda x, g: c[("seed", x, None, g["id"])]  # noqa: E731
    lits = lambda ls: [ex.lit(lg, cl, v) for cl, v in ls]  # noqa: E731
    if kind == "unresolved":  # base reading: the claim holds by the reading itself; alt: the gold world violates it
        if not lg.props:
            raise LookupError(f"{lg.id}: no ambiguous proposition")
        p = rng.choice(lg.props)
        vb, va = lg.values(lg.gold)[p["base"]], lg.values(lg.gold)[p["alt"]]
        if claim == "exclusive":
            return f"ambiguous_{p['type']}", [ex.plit(p, vb), ex.lit(lg, p["alt"], va)], None
        return f"ambiguous_{p['type']}", [ex.plit(p, vb)], ex.lit(lg, p["base"], vb)
    opts: list[tuple[str, str, list[tuple[str, bool]], tuple[str, bool] | None]] = []
    x, m = rng.choice(teams), rng.choice(teams)
    t = rng.randint(1, lg.L - 1)
    if kind == "true":
        opts += [("winner_exclusive", "exclusive", [(W(y), True) for y in teams], None),
                 ("winner_exhaustive", "exhaustive", [(W(y), True) for y in teams], None),
                 ("winner_partition", "partition", [(W(y), True) for y in teams], None),
                 ("runner_exhaustive", "exhaustive", [(S(y), True) for y in teams], None),
                 ("runner_partition", "partition", [(S(y), True) for y in teams], None),
                 ("qualify_exhaustive", "exhaustive", [(Q(y), True) for y in rng.sample(teams, lg.n - lg.k + 1)], None),
                 ("winner_runner_exclusive", "exclusive", [(W(x), True), (S(x), True)], None),
                 ("ladder_exhaustive", "exhaustive", [(T(x, t), True)], (T(x, t + 1), True))]
        if lg.k == 2:  # the qualifiers are the finalists
            opts.append(("finalists_partition", "partition", [(W(x), True), (S(x), True)], (Q(x), True)))
        for y, z in lg.conditionals:
            opts.append(("conditional_exclusive", "exclusive", [(W(y), True), (S(z), False)], None))
        for g in lg.groups:
            for claim_g in ("partition", "exclusive", "exhaustive"):
                opts.append((f"seed_{claim_g}:{g['id']}", claim_g, [(seed(y, g), True) for y in g["members"]], None))
        for g1, g2 in combinations(lg.groups, 2):
            for y in (y for y in g1["members"] if y in g2["members"]):
                opts.append((f"seed_overlap:{g1['id']}:{g2['id']}", "exclusive", [(seed(y, g1), True), (seed(y, g2), True)], None))
        if lg.a is not None:
            opts.append(("chain_exhaustive", "exhaustive", [(Q(x), True)], (W(x), True)))
        for y, z in lg.pairs:
            u = rng.randint(1, lg.L - 1)
            opts.append(("outscores_exclusive", "exclusive", [(T(z, u), True), (T(y, u + 1), False)], None))
        for y, z in zip(lg.chain, lg.chain[1:]):
            u = rng.randint(1, lg.L)
            opts.append(("chain_exclusive", "exclusive", [(T(z, u), True), (T(y, u), False)], None))
    else:
        opts += [("qualify_exclusive", "exclusive", [(Q(y), True) for y in teams], None),
                 ("qualify_partition", "partition", [(Q(y), True) for y in teams], None),
                 ("qualify_gap", "exhaustive", [(Q(y), True) for y in rng.sample(teams, lg.n - lg.k)], None),
                 ("winner_gap", "exhaustive", [(W(y), True) for y in teams if y != m], None),
                 ("winner_partition_gap", "partition", [(W(y), True) for y in teams if y != m], None),
                 ("winner_qualify_exclusive", "exclusive", [(W(x), True), (Q(x), True)], None),
                 ("ladder_reverse_exhaustive", "exhaustive", [(T(x, t + 1), True)], (T(x, t), True)),
                 ("ladder_reverse_partition", "partition", [(T(x, t + 1), True)], (T(x, t), True))]
        for g in lg.groups:
            gap = rng.choice(g["members"])
            for claim_g in ("exhaustive", "partition"):
                opts.append((f"seed_gap_{claim_g}:{g['id']}", claim_g,
                             [(seed(y, g), True) for y in g["members"] if y != gap], None))
        if lg.groups:
            opts.append(("seed_union_exclusive", "exclusive", [(seed(y, g), True) for g in lg.groups for y in g["members"]], None))
    opts = [o for o in opts if o[1] == claim]
    rng.shuffle(opts)
    if rng.random() < 0.6:  # most claims over the named and implicit seed groups when the league has them
        opts.sort(key=lambda o: not any(lg.classes[cl]["subgroup"] for cl, _ in o[2]))
    for name, _, members, target in opts:
        if not all(cl in lg.free for cl, _ in members + ([target] if target else [])):
            continue  # a member the base fixes makes the claim trivial
        if kind == "false" and not any(_violated(env, claim, members, target) for env in lg.bank_env):
            continue
        return name, lits(members), (None if target is None else lits([target])[0])
    raise LookupError(f"{lg.id}: no {kind} {claim} construction")


def _class_of(lg: League, e: dict[str, Any]) -> str | None:
    """Class of a member literal (a var), None for an unresolved record."""
    for cls, r in lg.recs.items():
        if e["id"] in r[False] or e["id"] in r[True]:
            return cls
    return None


def _violated(env: dict[str, bool], claim: str, members: list[tuple[str, bool]], target: tuple[str, bool] | None) -> bool:
    true = sum(env[c] == v for c, v in members)
    t = target is None or env[target[0]] == target[1]
    if claim == "exclusive":
        return true > 1
    if claim == "exhaustive":
        return t and true == 0
    return true > 1 or (t and true == 0) or (not t and true > 0)  # partition: exclusive, exhaustive, members in target


def _selection(ex: _Exam, lg: League, rel: str) -> tuple[dict[str, Any], dict[str, bool]]:
    """(target, classes the answer must contain with their meaning). Targets prefer paraphrased classes; LookupError
    when the league has no class for this relation that the base leaves free."""
    rng, c = ex.rng, lg.cls_of

    def choice(seq: list) -> Any:
        if not seq:
            raise LookupError(rel)
        return rng.choice(seq)

    thr = [k for k in c if k[0] == "thr" and c[k] in lg.free]
    if rel == "equiv":
        cands = sorted(lg.para & lg.free) or sorted(lg.free)
        cls = choice(cands)
        return ex.lit(lg, cls, True), {cls: True}
    if rel == "excludes":  # a free member of an exactly-one set excludes the other members
        sets = [[c[("win", x, None, None)] for x in lg.teams], [c[("runner", x, None, None)] for x in lg.teams]]
        sets += [[c[("seed", x, None, g["id"])] for x in g["members"]] for g in lg.groups]
        cls, group = choice([(cl, s) for s in sets for cl in s if cl in lg.free])
        return ex.lit(lg, cls, True), {other: True for other in group if other != cls}
    para_thr = [k for k in thr if c[k] in lg.para] or thr
    _, x, t, _ = choice(sorted(para_thr))
    if rel == "implies":  # p -> T(x,t): every higher threshold
        return ex.lit(lg, c[("thr", x, t, None)], True), {c[("thr", x, u, None)]: True for u in range(t, lg.L + 1)}
    return ex.lit(lg, c[("thr", x, t, None)], True), {c[("thr", x, u, None)]: True for u in range(1, t + 1)}


# ---------------------------------------------------------------- generate

def gen(out_dir: str | Path, split: str, seed: int | None = None, leagues: int = LEAGUES,
        paraphrase_share: float = PARAPHRASE_SHARE, third_share: float = THIRD_SHARE, name: str | None = None) -> dict[str, Any]:
    """Pure generation (no engine): corpus, gold submission, meta, world, queries and gold/query_design. `name` (default
    mentat_hard_<split>) is the fixture name in fixture.json and world.json."""
    name = name or f"{NAME}_{split}"
    if split not in SEEDS:
        raise ValueError(f"split must be one of {sorted(SEEDS)}")
    if not 0 <= third_share <= paraphrase_share <= 1:
        raise ValueError("need 0 <= third_share <= paraphrase_share <= 1")
    seed = SEEDS[split] if seed is None else seed
    pool, names = name_pools(split, seed)
    if leagues < 1 or leagues > len(names):
        raise ValueError(f"leagues must be in 1..{len(names)}")
    lgs = [_league(split, seed, li, names[li], pool) for li in range(leagues)]
    if len(pool) < 2 * sum(lg.n for lg in lgs):
        raise ValueError("team pool is smaller than twice the teams needed")
    markets = []
    for lg in lgs:
        markets += _markets(lg, paraphrase_share, third_share)
        lg.para = {cls for cls, r in lg.recs.items() if len(r[False]) > 1}
    if len({m["mid"] for m in markets}) != len(markets) or len({m["question"] for m in markets}) != len(markets):
        raise ValueError("market id or question collision")
    rows = sorted((r for m in markets for r in m["rows"]), key=lambda r: r[0]["id"])  # hash order
    flat = [c for lg in lgs for c in lg.cons]
    cons = [{"id": f"g{i:06d}", "expr": c["expr"], "interps": c["interps"]} for i, c in enumerate(flat)]
    out = Path(out_dir)
    gold = out / "gold"
    gold.mkdir(parents=True, exist_ok=True)
    ir.write_jsonl(out / "corpus.jsonl", (r[0] for r in rows))
    usage = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}
    ir.write_submission(gold, (r[1] for r in rows), cons, GOLD_INTERPRETATIONS, usage)
    ir.write_jsonl(gold / "meta.jsonl", (r[2] for r in rows))
    ir.write_json(gold / "world.json", {"name": name, "split": split, "seed": seed, "version": VERSION,
                                        "leagues": [lg.world_json() for lg in lgs]})
    queries, design = _exam(lgs, split, seed)
    ir.write_jsonl(out / "queries.jsonl", queries)
    ir.write_jsonl(gold / "query_design.jsonl", design)
    fams = Counter(r[2]["family"] for r in rows if r[0]["label"] == "Yes")
    summary = {
        "name": name, "split": split, "seed": seed, "version": VERSION,
        "generator": "mentat_bench.fixtures_hard", "controlled": True, "leagues": leagues,
        "outcomes": len(rows), "markets": len(markets), "queries": len(queries),
        "paraphrase_share": paraphrase_share, "third_share": third_share,
    }
    ir.write_json(out / "fixture.json", summary)  # public: no gold-derived counts (finalize adds them to gold/summary.json)
    summary = {**summary, **_gold_counts(out)}
    stats = {
        "constraints_by_rule": dict(sorted(Counter(c["rule"] for lg in lgs for c in lg.cons).items())),
        "paraphrase_pairs": sum(len(lg.paraphrase_pairs) for lg in lgs),
        "glossed_markets": sum(r[2]["gloss"] for r in rows) // 2,  # two outcome rows per market
        # same-meaning outcome pairs of one class across its markets (unordered; the ordered count is twice this)
        "equiv_outcome_pairs": sum(len(r[v]) * (len(r[v]) - 1) // 2 for lg in lgs for r in lg.recs.values() for v in r),
        "families": dict(sorted(fams.items())),
        "test_only_share": round(sum(v for f, v in fams.items() if f > DEV_FAMILIES) / len(markets), 4),
        "exam": dict(Counter(q["family"] for q in queries)),
        "bank_worlds": [len(lg.bank) for lg in lgs],
    }
    return {**summary, "stats": stats}


# ---------------------------------------------------------------- finalize and audit

def finalize(out_dir: str | Path) -> dict[str, Any]:
    """fixtures_gen.finalize (engine answers, verdicts, material premises, summary), then the exam checks: no answer
    error, verdicts ok or family_not_certified, every expectation met, and no assumption pair of a joint-only UNSAT
    query UNSAT on its own. A failed check (RuntimeError) or any other failure rolls back: the finalized files are
    removed and gold/query_design.jsonl gets its generated bytes back, so the directory is as `gen` left it."""
    out = Path(out_dir)
    gold = out / "gold"
    design_bytes = (gold / "query_design.jsonl").read_bytes()
    res = fixtures_gen.finalize(out)  # stages its own files; a failure there leaves nothing behind
    try:
        pairs = _check_exam(out)
        summary = ir.read_json(gold / "summary.json")
        ir.write_json(gold / "summary.json", {**summary, "counts": _gold_counts(out)})
        return {**res, "pair_checks": pairs}
    except BaseException:
        for name in ("answers.jsonl", "verdicts.jsonl", "summary.json"):
            (gold / name).unlink(missing_ok=True)
        (gold / "query_design.jsonl").write_bytes(design_bytes)
        raise


def _gold_counts(out: Path) -> dict[str, Any]:
    """Counts read from the gold submission (kept out of the public fixture.json)."""
    theory = ir.read_json(out / "gold" / "theory.json")
    binds = list(ir.read_jsonl(out / "gold" / "bindings.jsonl"))
    return {"classes": len(theory["aliases"]), "gold_constraints": len(theory["constraints"]),
            "unresolved_outcomes": sum(b["status"] == "unresolved" for b in binds),
            "interpretations": theory["interpretations"]}


def _check_exam(out: Path) -> int:
    """The exam checks of finalize; returns the number of engine pair checks, RuntimeError on a failed check."""
    gold = out / "gold"
    queries = list(ir.read_jsonl(out / "queries.jsonl"))
    answers = {a["qid"]: a for a in ir.read_jsonl(gold / "answers.jsonl")}
    design = {d["qid"]: d for d in ir.read_jsonl(gold / "query_design.jsonl")}
    bad = [q["qid"] for q in queries if answers[q["qid"]].get("error")]
    bad += [v["qid"] for v in ir.read_jsonl(gold / "verdicts.jsonl") if v["reason"] not in ("ok", "family_not_certified")]
    pairs = []
    for q in queries:
        exp, a = design[q["qid"]]["expect"], answers[q["qid"]]
        if exp is None:
            continue
        if "decision" in exp and a.get("decision") != exp["decision"]:
            bad.append(q["qid"])
        if "must_include" in exp and not set(exp["must_include"]) <= set(a.get("ids") or []):
            bad.append(q["qid"])
        if exp.get("joint_only"):
            pairs += [{"qid": f"{q['qid']}__pair{i}_{j}", "family": "feasibility", "assume": [q["assume"][i], q["assume"][j]]}
                      for i, j in combinations(range(len(q["assume"])), 2)]
    with tempfile.TemporaryDirectory() as tmp:
        qp, ap = Path(tmp) / "pairs.jsonl", Path(tmp) / "pairs_answers.jsonl"
        ir.write_jsonl(qp, pairs)
        engine.eval_submission(gold, qp, ap)
        bad += sorted({a["qid"].split("__")[0] for a in ir.read_jsonl(ap) if a.get("decision") != "SAT"})
    if bad:
        raise RuntimeError(f"exam checks failed for {len(bad)} queries, e.g. {sorted(set(bad))[:5]}")
    return len(pairs)


def audit(out_dir: str | Path) -> dict[str, Any]:
    """audit_brute over every query; the base of every component must be decided consistent, one component per league,
    every league touched by a query, and every query verified (no skip, no mismatch)."""
    from . import audit_brute  # numpy

    out = Path(out_dir)
    rep = audit_brute.audit_fixture(out, budget=AUDIT_BUDGET, query_seconds=AUDIT_QUERY_SECONDS)
    fx = ir.read_json(out / "fixture.json")
    touched = {d["league"] for d in ir.read_jsonl(out / "gold" / "query_design.jsonl")}
    failures = [r["qid"] + ":" + r["status"] for r in rep["results"] if r["status"] not in VERIFIED]
    if rep.get("fatal") or rep["base"] != "consistent":
        failures.append(f"base: {rep['base']}")
    if rep["components"] != fx["leagues"]:
        failures.append(f"components {rep['components']} != leagues {fx['leagues']}")
    if len(touched) != fx["leagues"]:
        failures.append(f"queries touch {len(touched)} of {fx['leagues']} leagues")
    return {"components": rep["components"], "component_outcomes_max": rep["component_outcomes_max"],
            "summary": rep["summary"], "by_family": rep["by_family"], "runtime_s": rep["runtime_s"],
            "failures": failures}


def generate(out: str | Path, *, name: str, seed: int | None = None, split: str = "dev", leagues: int = LEAGUES,
             paraphrase_pct: int = round(100 * PARAPHRASE_SHARE), third_pct: int = round(100 * THIRD_SHARE),
             relations: int = SUITE_RELATIONS, groups: int = SUITE_GROUPS) -> dict[str, Any]:
    """The complete fixture (the corrections.register_generated contract): gen, engine finalize, the audit gate, and the
    curated suite (hard_suite build and audit), built in a scratch directory beside `out` and renamed to `out` only when
    every step passed. `out` must not exist. Params
    are integers or strings (percent shares). Any failure removes the scratch directory and raises RuntimeError, which
    corrections reports as a refusal. seed None is the split's default (SEEDS[split])."""
    out = Path(out)
    if out.exists():
        raise FileExistsError(f"{out} exists; generate writes a new directory")
    if split not in SEEDS:
        raise RuntimeError(f"split must be one of {sorted(SEEDS)}")
    seed = SEEDS[split] if seed is None else seed
    for key, value in (("seed", seed), ("leagues", leagues), ("paraphrase_pct", paraphrase_pct), ("third_pct", third_pct),
                       ("relations", relations), ("groups", groups)):
        if type(value) is not int:
            raise RuntimeError(f"{key} must be an integer, got {value!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".mh-build-", dir=out.parent))
    try:
        res = gen(scratch, split, seed, leagues, paraphrase_pct / 100, third_pct / 100, name)
        res["finalize"] = finalize(scratch)
        res["audit"] = audit(scratch)
        if res["audit"]["failures"]:
            raise RuntimeError(f"audit gate: {len(res['audit']['failures'])} failures, e.g. {res['audit']['failures'][:3]}")
        # the curated suite (docs/mentat_hard.md s3): candidates.jsonl and gold/suite_labels.jsonl, then its audit
        from . import hard_suite

        res["suite"] = hard_suite.build(scratch, relations, groups)
        if res["suite"]["uncovered"] or res["suite"]["constants_excluded"]:  # v2: no constants, every rule row asked
            raise RuntimeError(f"suite: {res['suite']['uncovered']} uncovered rows, "
                               f"{res['suite']['constants_excluded']} constant outcomes")
        rep = hard_suite.audit(scratch)
        bad = rep["mismatches"] + rep["depth"]["mismatches"] + rep.get("world", {}).get("disagreements", [])
        if bad:
            raise RuntimeError(f"suite audit: {len(bad)} problems, e.g. {bad[:2]}")
        res["suite_audit"] = {"checked": dict(rep["checked"]), "depth": rep["depth"]["checked"],
                              "world": {k: v for k, v in rep.get("world", {}).items() if k != "disagreements"}}
        scratch.rename(out)
    except ValueError as exc:  # generation refusals (bad split, size, construction) are refusals too
        shutil.rmtree(scratch, ignore_errors=True)
        raise RuntimeError(str(exc)) from exc
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mentat_bench.fixtures_hard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="pure generation (no engine)")
    g.add_argument("--split", choices=sorted(SEEDS), required=True)
    g.add_argument("--seed", type=int)
    g.add_argument("--leagues", type=int, default=LEAGUES)
    g.add_argument("--paraphrase-share", type=float, default=PARAPHRASE_SHARE, help="classes with a second market")
    g.add_argument("--third-share", type=float, default=THIRD_SHARE, help="classes with a third market")
    g.add_argument("--out", required=True)
    for name, text in (("finalize", "engine gold and exam checks"), ("audit", "audit_brute over every query")):
        sub.add_parser(name, help=text).add_argument("--out", required=True)
    b = sub.add_parser("build", help="the complete fixture into a new directory (gen, finalize, audit gate)")
    b.add_argument("--split", choices=sorted(SEEDS), required=True)
    b.add_argument("--seed", type=int)
    b.add_argument("--leagues", type=int, default=LEAGUES)
    b.add_argument("--name")
    b.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    if args.cmd == "gen":
        res = gen(args.out, args.split, args.seed, args.leagues, args.paraphrase_share, args.third_share)
    elif args.cmd == "build":
        res = generate(args.out, name=args.name or f"{NAME}_{args.split}", seed=args.seed, split=args.split,
                       leagues=args.leagues)
    elif args.cmd == "finalize":
        res = finalize(args.out)
    else:
        res = audit(args.out)
    res["wall_s"] = round(time.perf_counter() - t0, 2)
    print(json.dumps(res, indent=1, sort_keys=True))
    return 1 if res.get("failures") else 0


if __name__ == "__main__":
    sys.exit(main())

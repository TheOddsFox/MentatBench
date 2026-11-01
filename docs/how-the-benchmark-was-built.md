# How the Mentat-Hard benchmark was built

This document explains how Mentat-Hard was designed, generated, audited, registered and scored, and why each design changed along the way. It is written for researchers and for readers of the results website. Most sections link to the repository file behind each claim. Numbers come from committed sources, or from the design spec where marked "per the design spec". Open questions are marked as open.

The current release is **v0.1.0**: the development round 3 results on the Mentat-Hard test set, revision 3.1, released 2026-10-31. The results are in [results/README.md](../results/README.md), and the machine-readable leaderboard is [results/v0.1.0/leaderboard.json](../results/v0.1.0/leaderboard.json).

## 1. Purpose and framing

**System One.** Mentat Bench is a benchmark for systems that compile a large corpus of natural-language propositions into a reusable logical representation, then answer queries from it ([spec: SPEC.md](../SPEC.md)). Its System One panel measures a model that makes typed decisions in a single pass, with no generated reasoning. Each question has a fixed menu of typed answers, and the model returns a probability for each option ([local_protocol.md](local_protocol.md), "System One child").

**What "understanding logical relationships" means here.** It is a narrow claim. Inside a generated league whose rules are stated on a short card, the model has to:
- name the logical link between two propositions: EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, or NONE;
- name the constraint that a set of members satisfies (a group question);
- say whether a proposition is bound or unresolved (a binding question);
- pick the option that each reading of an ambiguous phrase implies (a reading question).

The gold answer for each question is computed by the engine and re-checked by an independent brute-force auditor (section 3.4). A score measures agreement with that gold. It does not measure general knowledge or open-ended reasoning.

**Fixed harness.** The spec's section 9 requires a fixed harness: candidate generation, prompts, decision schemas, call graph, solver and retry policy stay fixed, and only the model adapter changes. Mentat-Hard is the main System One panel, specified in [mentat_hard.md](mentat_hard.md) with its data contracts in [mentat_hard_contracts.md](mentat_hard_contracts.md) (spec section 4).

**Scope.** The data is synthetic and single-domain: a fictional sports league. The held-out wording covers phrasing only, not new rule types ([mentat_hard.md](mentat_hard.md), section 8).

## 2. Project history and the decisions behind each change

Dates are the dates the repository records for each event: the decision, the document or the run. PR numbers refer to pull requests of the private development repository, which is not published here; where a PR is cited for a date, its merge date is given. The table lists each phase in order. The "why" column gives the reason recorded in the repository.

| When | What | Why it changed | Sources |
|---|---|---|---|
| 2026-10-18 to 10-21 | Early toy fixtures and model "waves", including the synthetic `toy10k` and an internal fixture built from Polymarket records. Performance work and A/B flags. | These first measurements exposed contradictory model theories and led to repair, cost, and scoring controls. They are part of the project's research history, not the public v0.1.0 results bundle. PRs #1 to #24. |
| 2026-10-18 to 10-27 | The exact-scale track: generated corpora at 10k, 100k and 1M units, aimed at an "Exact 1M" label. | The 1M run took about 31 hours. Its first generator was one replicated 36-unit template, so it was never Exact-1M evidence. On 2026-10-27 the owner scrapped the long runs and removed the code. The 1M run data and an engine copy were then deleted, so gate 5 can no longer be reproduced. | [Historical status](exact_scale.md). PRs #11 and #42. |
| 2026-10-27 | toy10k retired as a System One panel. The fixture stays for tests and the harness. | Mentat-Hard was planned to replace it: smaller (each 27B cell about an hour), harder (8 rule types, 30 wording families, hard distractors), exactly gold (generated world, engine gold, independent audit), and split (dev and test share no ids, names or held-out wording). | [mentat_hard.md](mentat_hard.md) (Purpose), [systemone-dev-notes-20261027](history/notes/systemone-dev-notes-20261027.md), [remaining-work.md](remaining-work.md) section 1.1 |
| 2026-10-27 (signed off), 2026-10-28 (run) | development round 1 (generator "1"). Eight Ollama models, child version 4, 2,806 bindings, 10,000 relations, 1,000 groups. | Headline was the mean of three MCCs (relations, groups, bindings), published 2026-10-28: D3 27B scored 0.248 = (0.565 + 0.179 + 0.000) / 3. Superseded by round 2. The round 1 record is kept as published. | [round-1 report](history/round-1/report.md), [round-1 pre-registration](history/round-1/prereg.md). PRs #57 to #60 (merged 2026-10-27 and 2026-10-28). |
| 2026-10-28 (generated), 2026-10-29 (registered) | round 2 (generator "2"). | Forced outcomes removed: a league is redrawn while any class is fixed by the base, so no asked question has a foregone answer. Chains cut to 2 to 3 members (owner decision, 2026-10-28; every accepted chain ended up with 2). Reading questions and full-cardinality group labels added. | [mentat_hard.md](mentat_hard.md) sections 1 and 9. PRs #61 to #67 and results #68 (merged 2026-10-28 to 2026-10-29). |
| 2026-10-29 | round 2 validity audit, and the headline changed to relation MCC. | Blind answerability judges could not reach most real group constraints or any second reading from the text. Relation MCC was stable under paraphrase, while binding and group scores moved. The audit examined the round 2 items. After the round 2 test results were known, the owner made relation MCC the headline for round 2 (and round 1), with group and reading MCC as diagnostics. This is a disclosed post-hoc change. | [headline-change-disclosure.md](history/round-2/headline-change-disclosure.md) |
| 2026-10-29 (design, per the spec), 2026-10-30 (registered) | round 3 (generator "3"): rule cards. Each league gets a plain-English card (corpus field `context`). Glossed markets, child version 6, reading rows with three same-team options. | The round 2 audit showed that groups and readings depend on rules the text never stated. The card states them. Bindings get no card, as pre-registered. | [mentat_hard.md](mentat_hard.md) section 2 and section 9. PRs #71 (context), #74 (child version 6), #75 (generator version 3). |
| 2026-10-30 | Blind audit of round 3 (child version 6), 7 parts. | Checks that the round 3 questions are answerable from the prompt text. Every part passed for both judges. | [round 3 audit notes](audits/test-set-groups/notes.md). PR #76. |
| 2026-10-30 | revision 3.1: harder relations. | The design spec (section 9) gives the reason: on dev, D3 27B's relation score was far above its group score, and about 1,226 of the 6,000 dev relations were generator edges that the card states almost word for word. revision 3.1 cut NONE to 25%, made two thirds of the NONE pairs rule-vocabulary distractors, drew derived links deepest first, and tagged each relation with its inference depth. | [mentat_hard.md](mentat_hard.md) section 9. PR #79. |
| 2026-10-30 | revision 3.1 audit, round 1 failed; child version 7 fixed the instruction; round 2 passed. | Round 1 (child version 6) failed one part, reading decoys: judge 1 answered 11 of 30, judge 2 answered 29 of 30. The alt-reading instruction was ambiguous for glossed decoys. The owner fixed the instruction (child version 7), discarded the dev cells that had started on child version 6, and re-ran the full audit against the same bar. Round 2 passed all eight parts. | [revision 3.1 audit notes](audits/test-set-relations/notes.md), [round-1 notes](audits/test-set-relations/round1-child-v6/notes.md). PRs #78 (child version 7) and #80 (audit). |
| 2026-10-30 | Headline changed to the mean of relation MCC and group MCC. | Decided on dev, before any test run. Binding MCC stays secondary. | [mentat_hard.md](mentat_hard.md) section 5. PR #77. |
| 2026-10-30 (registration, pre-registration); 2026-10-31 (results merged) | Registration, pre-registration, test matrix, private split. | The matrix, headline rule, reference and floors were frozen before the test runs. The private split was run after the test matrix. | Registration PR #81, pre-registration PR #82, [test results](results/mentat-hard-20261030.md) PR #83, [private results](results/mentat-hard-private-20261030.md) PR #84. |
| 2026-10-31 | Release v0.1.0 and the results bundle. | Pins the fixtures, run cards, baselines, attestations and gate output. | [release.md](release.md) section 8. PRs #85 and #86. |

## 3. The world generator

The generator is [fixtures_hard.py](../python/mentat_bench/fixtures_hard.py). Its constants are in the module header: 18 leagues per split, a 30-family wording set, and a 2,000-character cap on the rule card.

### 3.1 Leagues and seeds

Each split is a set of independent **leagues**. Each league is one logical component, with 4 to 6 teams. Each team has an integer score from 0 to L, where L is 5 to 7. Four classes describe the league: T(team, t) means "score at least t", W is "wins the cup", S is "runner-up", and Q is "qualifies for the playoffs" ([mentat_hard.md](mentat_hard.md) section 1).

Team names come from a split-specific pool, and the dev and test pools are disjoint. Ids are opaque hashes of the split, seed and key, so dev and test share no outcome or class ids. The seeds are dev 20261027 and test 20261028 (`SEEDS` in `fixtures_hard.py`).

### 3.2 Rule types

Every league has rules 1, 2, 5, 7 and 8. Rules 3, 4 and 6 are each drawn per league with probability 0.6 (league 1 gets all three), from a seed per league; if fewer than two optional rules are drawn, one more is added (this happened once on test). Each rule reaches the model only through its pairwise and group consequences.

| # | Rule | What it states | What the model can say about it |
|---|---|---|---|
| 1 | Score ladder | T(x,t+1) implies T(x,t) | IMPLIES, IMPLIED_BY |
| 2 | Exactly-k qualify (k = 2 or 3) | exactly k teams are in Q | at_least_k and at_most_k groups |
| 3 | At-least and at-most counts | at least k1 teams reach a points; at most k2 teams reach b points | group kinds |
| 4 | Comparison "X outscores Y" | cross-team implications | cross-team IMPLIES |
| 5 | Conditional placement "if X wins, Y is runner-up" | exactly one winner and one runner-up, not the same team | IMPLIES, EXCLUDES, exactly_one |
| 6 | Chains X1 >= X2 >= ... | each link is "at least as many points" | multi-hop IMPLIES |
| 7 | Overlapping groups | three seeded subgroups; each later one shares one or two teams with the first, and each has exactly one "seed" | exactly_one, EXCLUDES across the overlap |
| 8 | Base and alt readings | 2 or 3 ambiguous propositions per league ("by the break", "top half") with two admissible readings | unresolved bindings; reading questions |

Rule 8 in round 3: "by the break" at final points s has a base reading T(x, s+1) (the literal one) and an alt reading T(x, s) (which counts the one point every team gets in the Shield round). "Top half" has a base reading (a playoff place) and an alt reading (at least M points, with one M per league). The two readings disagree in the gold world ([mentat_hard.md](mentat_hard.md) section 1).

### 3.3 No forced outcomes

A **forced outcome** is a class that the base theory fixes, so it is true or false in every model and cannot be asked. round 1 had three sources of them, and round 2 removed them. Now a league is redrawn while its bank shows any class fixed by the base. The constants `TARGET_FIXED` and `MAX_FIXED` are both 0, and `LEAGUE_TRIES` is 100. If no draw qualifies, the generator raises an error rather than falling back ([mentat_hard.md](mentat_hard.md) section 1).

### 3.4 Gold is certified by the engine and an independent audit

- The gold is written by `ir.write_submission`. Exam answers and suite labels come from `mb-engine` over all interpretations. The engine is the Rust crate [crates/mb-engine](../crates/mb-engine) and its IR validator is [crates/mb-ir](../crates/mb-ir).
- `audit_brute` ([audit_brute.py](../python/mentat_bench/audit_brute.py)) re-solves every component with its own brute-force and DPLL solver. It shares no code with the engine. A query that exceeds its budget fails the build.
- The suite's depth check uses a separate graph search, `audit_brute.DepthGraph`, and shares no code with the generator's search (section 4.2).
- `generate` refuses a split that has any uncovered constraint or any constant, so the generation gate is a hard stop.

### 3.5 Rule cards

- Each league has a **rule card**, a short plain-English statement of its rules. It is stored in the corpus field `context` on each of the league's rows (capped at 2,000 characters; test cards run 1,113 to 1,427 characters).
- `card_sentences` and `render_card` build the card from the rule list only. The renderer is given the league's world without `scores`, `winner`, `runner_up`, `qualifiers` and `seeds`, and it refuses those keys. The test rebuilds each sentence's constraints from the league's fields and compares them.
- Some card parameters are conditioned on the gold world: the top-half threshold M, the "if X wins" pair, q, the seed minimum, the chain order and the winner bound a. Each is drawn so that the theory is true. These are bounds the theory needs, not gold values read out. The card cannot be produced without them, and the results document discloses this ([mentat_hard.md](mentat_hard.md) section 1).
- The card goes with relation, group and reading questions. It does not go with bindings. This was pre-registered ([prereg](../release/v0.1.0/record/prereg.md)).

### 3.6 Exam queries

About 200 queries per split are finalized by the engine: 80 closure, 50 feasibility, 40 partition and 30 selection ([mentat_hard.md](mentat_hard.md) section 4). They feed only Q_N, a diagnostic (section 8).

### 3.7 Sizes (test split, round 3)

18 leagues; 1,674 markets; 3,348 outcomes; 1,047 classes; 1,727 gold constraints; 92 unresolved outcomes (46 propositions); 851 paraphrase pairs; 197 glossed markets ([mentat_hard.md](mentat_hard.md) section 1, round 3 sizes).

## 4. The curated suite

The suite ([hard_suite.py](../python/mentat_bench/hard_suite.py)) is the set of questions that models answer. Its labels are in `gold/suite_labels.jsonl`, and the public list of questions is in `candidates.jsonl`. Counts in this section that the results README does not give come from the design spec (`mentat_hard.md` section 3), and the data was spot-checked where noted.

### 4.1 Bindings

Every outcome on the split is a binding question: is the proposition bound, or unresolved? The test split has 3,348 of them.

### 4.2 Relations

The test split has 6,000 ordered pairs. They are built in this order ([mentat_hard.md](mentat_hard.md) section 3):

1. Every generator edge and every at-most-one exclusion. On test, all 1,346 were asked.
2. 500 trivial pairs (the same market, labelled EXCLUDES at depth 0) and 800 EQUIV pairs.
3. Derived links that the generator did not state, drawn deepest tier first (depth 4 or more, then 3, then 2).
4. NONE pairs: 1,500, which is 25% of the relations. Per the design spec, two thirds (1,000) are **rule-vocabulary** distractors: same-league pairs that share a team, a threshold, a draw group or a constraint. One third (500) are **profile-matched**, with the word overlap of their two questions matched to the positives. Cross-league NONE pairs are capped and reported: 56 on test (3.7%). A cross-league pair can be recognized from its event title, which names the league.
5. Orientation: IMPLIES and IMPLIED_BY follow the tier balance or the seeded random draw. EQUIV, EXCLUDES and NONE have no direction, so their rows are oriented at random.

The test mix (per the design spec) is EQUIV 800; IMPLIES 1,242 (624 generator edges and 618 derived); IMPLIED_BY 1,196 (578 edges and 618 derived); EXCLUDES 762 real (144 edges and 618 derived) plus 500 trivial; and NONE 1,500.

**Inference depth.** Every entailed pair has a depth: the fewest rule constraints a careful reader must chain to derive it from the card. The search is an exact 0-1 breadth-first search over the class graph (`hard_suite.Depths`). A score-ladder step costs 0. A generator edge that is not a ladder step costs 1. Paraphrases, negations and the readings of an unresolved proposition cost 0. An entailment that no chain of edges reaches (it needs a counting argument) has depth `count`, is reported, and is never drawn. Depth is a gold-side tag. The model never sees it.

Depth 0 contains EQUIV, the trivial same-market pairs and the ladder steps. Depth 1 is the generator edges that are not ladder steps. On test, the asked relations are depth 0: 1,770; depth 1: 876; depth 2: 1,194; depth 3: 654; depth 4 or more: 6; and NONE: 1,500 ([mentat_hard.md](mentat_hard.md) section 3, and the [results README](../results/README.md), depth table). The independent `audit_brute.DepthGraph` recomputes every relation's depth. On test, all 6,000 matched, with 0 mismatches.

### 4.3 Groups

The test split has 1,000 candidate member sets of 2 to 8 members, drawn from three sources ([mentat_hard.md](mentat_hard.md) section 3):
1. every unscoped gold cardinality constraint, as its own set (393 on test);
2. about 30% decoys, labelled `none` (300);
3. subsets and chains, walked along the implications (307).

A group's label is the canonical term of its exact bounds. A bound counts only when it holds in every interpretation. An UNRESOLVED feasibility answer does not count as valid, so a bound is stated only when every interpretation forces it. The class is the canonical class of the answer's bounds, so a wrong k is a wrong class ([decision_scoring.py](../python/mentat_bench/decision_scoring.py)). Recomputed from the committed data, 24 of the 1,000 test group rows (13 real, 11 decoy) contain a member whose binding is unresolved, and 27 of the dev group rows do. The design spec's earlier figure of 30 on test was wrong, and it is corrected in `mentat_hard.md`. Their bounds are the every-interpretation bounds.

### 4.4 Readings

Reading questions are a separate block of 184 on test (92 ambiguous rows and 92 decoy rows). Each row has four options after u: three on the same team as u, and one other-team distractor (checked on all 184 test rows). A reading question is scored as two items, one per reading, which makes 368 items.

- **Ambiguous rows** present the base and alt outcomes of an ambiguous proposition, plus one distractor from another team.
- **Decoy rows** use **glossed markets**. A glossed market states its reading in the question, for example "counted on final points" or "meaning a playoff place". A decoy proposition is one of the two outcomes of a glossed market, and its gold option is the same class in another market.
- **Equalized cues.** Option kinds, same-team counts, number matches, polarity, and option wording families are matched between the two strata. On test, both strata have 79 held-out rows.

**Rule 8 redesign.** In round 2, the phrases "by the break" and "top half" alone predicted which stratum a row belonged to. The owner accepted that cue with disclosure, and it was removed in round 3. In round 3 the phrase regex predicts the stratum at 0.48 on test, which is chance. The gloss now identifies the decoy stratum perfectly, at 1.0 on both splits. That is a designed cue, because a glossed phrase has only one reading. It is disclosed, and the owner has to decide whether it stands ([mentat_hard.md](mentat_hard.md) section 3).

### 4.5 Wording families and held-out wording

- There are 30 rendering families. Each is a coherent style for every proposition kind. Families vary number phrasing ("at least t", "more than t-1", "not fewer than t", "fail to reach t"), double negatives, label polarity, event-title paraphrases, and whether the group is implicit or named ([mentat_hard.md](mentat_hard.md) section 2).
- **Dev uses families 1 to 20. Families 21 to 30 appear only in test.** This is the held-out wording.
- A relation is held out if any of its outcomes has a test-only family. On test, 2,332 of 6,000 relations (38.9%) are held out. The pick target was 30%, and the per-bin quotas rounded up. Dev has no held-out pairs.
- About 17% of classes get a second and a third market in different wordings, which gives EQUIV pairs ([mentat_hard.md](mentat_hard.md) section 1).

### 4.6 Ordering

Relation, group and reading rows come in **league blocks**: leagues in a seeded order, with rows shuffled inside each block. Consecutive requests therefore share a rule card ([hard_suite.py](../python/mentat_bench/hard_suite.py), `_league_blocked`).

### 4.7 Self-checks

- `build` reports uncovered generator edges, uncovered cardinality constraints, uncovered unresolved outcomes, and constants. `generate` refuses a split when any of them is not zero.
- `audit` re-derives the group terms and audits every reading row. `world_check` checks each reading option against `gold/world.json`.
- The final test audit ([mentat_hard.md](mentat_hard.md) section 3, "Audit counts"): `audit_brute` checked 3,348 bindings, 1,184 group and reading rows, and 6,000 relations, with 0 mismatches. The world check checked 5,832 relations, 976 group rows and 184 readings, with 0 disagreements. The depth audit checked all 6,000 relation depths, with 0 mismatches. Every one of the 1,500 NONE rows was confirmed to be entailed under no interpretation.

## 5. Splits, registration and corrections

- **Registered splits.** `mentat_hard_dev` and `mentat_hard_test` are at registry version 3 (test set revision 3.1). [fixtures/VERSIONS.json](../fixtures/VERSIONS.json) holds each version's name, status (active, superseded or retired), content hash, per-file SHA-256 hashes and change log. The content hash covers every regular file in the fixture directory. The hashes for round 3 are on the [results README](../results/README.md), in the test file table.
- **Corrections.** A fix is a new version, never an edit. A registered version is never edited in place, because its content hash is its identity. Regeneration is allowed only through a whitelisted generator, and `fixtures_hard` is on that list. The corrections process is in [corrections.md](corrections.md), and the tool is [corrections.py](../python/mentat_bench/corrections.py). Retired and superseded versions stay listed, with their status.
- **Test gold is public.** The test split's gold is published. Only the private split is sealed.
- **The private split.** `mentat_hard_private` is a third split. The same generator built it (test set revision 3.1), from a seed drawn with a CSPRNG. Its seed is not recorded in the repository. The split and its run directories are not published. It is committed by its content hash only, and the results published are aggregates ([release.md](release.md) section 8; [private results](results/mentat-hard-private-20261030.md)).

## 6. Answerability audits

**Why.** A score is only meaningful if the gold answer can be derived from the text the model sees. The round 2 audit found that real group constraints and second readings were not derivable from the text. The round 3 rule cards were added to fix that, and the audits check the fix.

**Method (all rounds).** Judges are model instances: two independent Haiku 5.5 judges at high effort, not people. Each gets only its instructions (`JUDGE.md`) and a sample. The sample prompts are the exact wire text that the child sends, captured through the real child against a fake server, and a separate script rebuilds them to check. A part passes when both judges score at least 80% (24 of 30). When both judges miss an item, the item is checked for derivability from the prompt alone. A derivable miss counts as difficulty and stays in the score.

**Round by round:**
- **round 2, 2026-10-29, dev, 150 items** ([notes](history/round-2-answerability-audit/), [validity audit](history/round-2/headline-change-disclosure.md)). Bindings were answerable: clear 15 of 15 for both judges, ambiguous 13 and 15. Relations were partly answerable: real links scored 5 of 24 and 17 of 24. Real group sets scored 1 of 15 and 5 of 15. Second readings scored 0 of 30 for both judges. The audit led to the headline change.
- **round 3, child version 6, 2026-10-30, 180 items** (dev, plus a held-out scratch build with a non-registered seed; [notes](audits/test-set-groups/notes.md)). All seven parts passed for both judges: bindings 30 of 30, relations 29 of 30 for each judge, groups 30 of 30, readings base 30 of 30, readings alt 30 of 30, reading decoys 30 of 30, and held-out relations 30 of 30.
- **revision 3.1, 210 items** ([notes](audits/test-set-relations/notes.md)). This adds a deep-relation part (depth 3 or more, 30 items). **Round 1 used child version 6 and failed one part.** Reading decoys scored 11 of 30 for judge 1 and 29 of 30 for judge 2. The cause, recorded in [round-1 notes](audits/test-set-relations/round1-child-v6/notes.md): child version 6's alt instruction was ambiguous for a glossed decoy, one whose gloss states the reading on final points. In 19 of 30 decoy rows, judge 1 answered the alt reading differently from the key. The owner decided to fix the instruction (child version 7), restart the dev cells, and re-run the full audit against the same bar. **Round 2 used child version 7.** Every part passed for both judges: bindings 30 of 30, relations 30 of 30, deep relations 30 of 30, groups (judge 1 29 of 30, judge 2 30 of 30), readings base and alt 30 of 30 each, reading decoys 30 of 30, and held-out relations (judge 1 29 of 30, judge 2 30 of 30). The first instance of judge 2 ran out of context on the full 210-item file, so judge 2 ran as three instances on 70-item slices.

**Limits.** The judges are models. Each part is a sample of 30 items, so the audit shows that the sampled gold answers are derivable from the prompt. It does not show that every question is derivable, and it does not show that a model's answers are right.

## 7. The harness and the protocol

- **The child.** `systemone-choice` is the child ([systemone_child.py](../python/mentat_bench/children/systemone_child.py); [local_protocol.md](local_protocol.md), "System One child"). It sends each question to a local Ollama `/v1/systemone` endpoint. The server computes the per-option probabilities, and the child reads them back. The prompts are zero-shot and uncalibrated.
- **One question per request.** `MAX_QUESTIONS` is 1. Packing questions into one request changed relation decisions on nimble:9b-bf16 (31 of 240 argmaxes), and it gave no speedup.
- **Relation menu.** The five options are EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES and NONE. The order is shuffled per pair with a CRC32 seed, so that no option sits in the same position for every pair.
- **Other question types.** A group question takes requests for the kind, for k, and for each round of an ordered chain. A binding question takes requests for status, negation and, when the status is unresolved, the missing part.
- **Abstentions.** A card can push a state past the server's context limit. Ollama refuses an over-long state with an HTTP 400 error, and the child counts that as an abstention. Card-driven abstentions are recorded when `MB_S1_STATS` is set. Laya's context is 512 tokens, so it abstains for length on most questions (see the [results README](../results/README.md), limit 1).
- **Child versions.** version 4 was used in round 1, version 5 in round 2, version 6 added the rule cards in round 3, and version 7 fixed the alt instruction after the audit (PR #78).
- **Model pins.** [children/ollama_models.json](../python/mentat_bench/children/ollama_models.json) maps each tag to its digest, Ollama version, size and licence. The child refuses an unpinned model unless `MB_S1_ALLOW_UNPINNED` is set. The results record each digest ([leaderboard.json](../results/v0.1.0/leaderboard.json)). The hf baseline is pinned to a Hugging Face revision in [children/models.json](../python/mentat_bench/children/models.json).
- **Outside servers.** [outside_models.md](outside_models.md) describes how another lab would plug in its own server. `systemone_child conform <tag>` runs the checks and exits 0 only when every check passes. Pinning a server that is not Ollama requires a passing conform report.
- **Baselines.**
  - **Rules** is the heuristic child ([heuristic_child.py](../python/mentat_bench/children/heuristic_child.py)). It is hand-written against the generator's public code, and it was never tuned on test. It is pre-registered as the rules baseline.
  - **Lexical** and **embedding** run on CPU ([lexical_child.py](../python/mentat_bench/children/lexical_child.py), [embed_child.py](../python/mentat_bench/children/embed_child.py)). They answer relations and bindings only.
  - **Empty** gives no answers, so it has a Q_N only.
  - **gold_view** answers every curated question from the gold labels ([mock adapter](../python/mentat_bench/adapters/mock.py); [contracts](mentat_hard_contracts.md), "gold_view answers"). It is a diagnostic sanity check, not a model. Its MCC of 1.0 is the ceiling.
  - **Oracle** replays the gold for the exam. It has a Q_N only, which is 100.
  - **hf pipeline** is Qwen2.5-1.5B-Instruct at a pinned revision. It was run after the other cells, with different build flags, and this is disclosed.

## 8. Scoring

- **Scorer.** [decision_scoring.py](../python/mentat_bench/decision_scoring.py) scores each item once. The headline uses raw decisions with no relation floor. A missing answer counts as wrong and lowers coverage. A binding is bound or unresolved. A reading is scored per reading.
- **MCC.** Each operation's MCC is the multiclass Matthews correlation coefficient (Gorodkin's R_K). It is 0 when there are no pairs or when one marginal is constant. The scorer also reports macro-F1, accuracy, per-class precision and recall, the confusion matrix, Brier and ECE, by stratum and by seen or held-out wording.
- **Headline by fixture version.**
  - round 1: the published headline (2026-10-28) was the mean of three MCCs (relations, groups, bindings). The round 1 record keeps it as published.
  - round 2: the four-part mean (relations, groups, bindings, readings) was pre-registered. From 2026-10-29 the headline for round 1 and round 2 is relation MCC, a post-hoc change for round 1 and round 2 ([section 2](#2-project-history-and-the-decisions-behind-each-change)).
  - round 3 and later: the mean of relation MCC and group MCC. The owner decided this on 2026-10-30, on dev ([mentat_hard.md](mentat_hard.md) section 5).
- **Diagnostics, not scores.** Reading MCC has known validity problems, from the round 2 audit. Q_N is a diagnostic. Seen and held-out relation MCC, and accuracy by depth, are descriptive.
- **Floors.** Each model has a relation floor, which is the lowest `--relation-floor` at which the precision of its named relations, on dev suite labels, reaches at least 0.8 ([floor_calibrate.py](../tools/eval/floor_calibrate.py)). A floor affects only the compiled base that Q_N scores, not the headline. A model that never reaches 0.8 gets the largest float, and it names no relations under that floor (TEV1 0.8B and D1-Omni 600M).
- **Q_N.** Q_N is the end-to-end exam score of the model's compiled base, over 200 queries. Every model build except TEV1 0.8B was repaired, which means decisions were dropped until the base was consistent. Strict Q_N gives 0 to a repaired build. TEV1 0.8B's base was not repaired, so its strict Q_N equals its repaired Q_N. The rules and lexical baselines score higher on Q_N than the models do, which is why Q_N is not the headline.
- **Intervals.** [league_ci.py](../tools/eval/league_ci.py) runs a league bootstrap: it resamples the 18 leagues with 2,000 draws, seeded with `random.Random("league_ci:0")`, and takes 95% percentile intervals. Paired gaps are reference minus run, within each draw. The reference is D3 27B, chosen on dev. The CI file reproduces byte for byte from the run list.
- **Known limits of the intervals.** With 18 clusters the intervals somewhat under-cover. The reference choice is not corrected for in the paired gaps. Only gaps to the reference are paired, and the intervals do not measure run-to-run variation.

## 9. Model selection and run infrastructure

- **The 15 models.** Eight models were carried over from earlier rounds. Seven are new in round 3: D3 2B and 4B, D1 3B, D1-Omni 600M, Nimble-v3 9B, and TEV1 0.8B and 4B ([results README](../results/README.md), "Models").
- **Skipped.** `d1:2b` and `d1-omni:2b` need a newer Ollama, an owner decision. `strands-decider:2b` runs only on MLX ([prereg](../release/v0.1.0/record/prereg.md)).
- **Pins.** Model digests and the Ollama version are pinned (section 7). The runs used Ollama 0.40.11.
- **Hardware.** Two NVIDIA RTX A6000 GPUs, with one Ollama server per GPU, CUDA only, `OLLAMA_NUM_PARALLEL=2` and `MB_S1_PARALLEL=2`.
- **Flags.** `--top-k 24`, `--batch-size 128`, a build deadline of 5,400 s (1.5 hours), and each model's dev floor. The cells also set `--local-timeout 600.0` and `--hello-timeout 900.0`, which the pre-registered command does not list, and this is disclosed.
- **Size and time.** Relations were cut from 10,000 to 6,000 to keep the 27B cells at or under 80 minutes ([prereg](../release/v0.1.0/record/prereg.md), "Size"). The build wall times on test for the three 27B models were 74.6, 74.4 and 78.7 minutes ([results README](../results/README.md), runtime). On the private split, the slowest 27B cell took 76.9 minutes ([private results](results/mentat-hard-private-20261030.md)). The baselines ran in 0.7 to 17.5 seconds each.
- **Determinism.** Each model was run once on test, so there is no test repeat. A dev repeat of D3 9B, on the other GPU, gave byte-identical decisions and scores.
- **Commit.** The test cells ran at commit `0bff47f` (a commit of the private development repository, not published here), a clean tree. The pre-registration names `9ad5f2c`. The two commits differ only by the two pre-registration files ([results README](../results/README.md), "Code").

## 10. Pre-registration and what was post-hoc

- The round 3 pre-registration ([prereg.md](../release/v0.1.0/record/prereg.md)) froze the matrix, the headline rule, the reference, the floors and the interval method before any test run.
- The round 3 results document states in its introduction (lines 11–12) that anything not pre-registered is labelled post-hoc, and that no score was changed after the test runs ([round 3 results](results/mentat-hard-20261030.md)). Its "Disclosed limits" section lists the post-hoc changes. They are:
  - revision 3.1, harder relations;
  - the relation plus group headline;
  - child version 7, after the first audit round failed.
- Each change is an owner decision, recorded in its PR. All were decided on dev before the test runs.
- The round 2 headline change of 2026-10-29 was made after the round 2 test results were known. It is disclosed as post-hoc ([round 2 validity audit](history/round-2/headline-change-disclosure.md), section 4).

## 11. Archived release evidence

- The v0.1.0 manifest and gate output are retained in [release/v0.1.0/record/](../release/v0.1.0/record/release_manifest.json) and [check_gates.md](../release/v0.1.0/record/check_gates.md). Their statuses refer to the source and inputs pinned for that measurement run; they are not checks of the current public source tree.
- Gates 1, 2, 7, 9 and 10 were recorded as MET; gates 6, 8 and 11 were owner-attested; gates 3 and 4 were NOT APPLICABLE; gate 5 was REMOVED. The tool did not check certificate rules, strata or blinding in practice.
- **Engine provenance.** The pinned Windows engine binary was a copy of the one the round 3 runs used. Its SHA-256 was not reproduced by a rebuild. The external catalogue and sealed private split used for that release are not included here. See [release.md](release.md) for the limits on historical verification.

## 12. Safeguards against leakage and gaming

- **Leakage tests.** [test_leakage.py](../tests/test_leakage.py) checks, among other things, that a build with a non-oracle adapter never opens a gold file; that corpus rows carry only public fields; that no query or corpus field carries a gold-revealing word; that fixture directories hold only public files; that sealed submissions contain no gold paths; and that a local build sets no proxy environment.
- **Held-out wording.** Families 21 to 30 appear only in test. A relation is held out if it uses one of them (section 4.5).
- **Private split.** It is sealed, its seed is not recorded, and only aggregates are published (section 5).
- **Public-cue checks.** Per the design spec's build report, a public-cue baseline on groups scores 0.74 on dev and 0.77 on test, against a real share of 0.70. The decoys are therefore not fully separated by public cues. This is disclosed ([mentat_hard.md](mentat_hard.md) section 8).
- **Gloss cue.** The gloss on a decoy marks the decoy stratum perfectly, by design (section 4.4).
- **No tuning on test.** The test split is not used for tuning ([mentat_hard.md](mentat_hard.md) section 7). The test gold is public, so this rule is a process guard and not a technical one.

## 13. Known limitations and open questions

From the [results README](../results/README.md) (limits 1 to 23):

- The data is synthetic and single-domain, and the candidates are gold-informed, so blocking is not tested.
- The test gold is public.
- The held-out wording covers phrasing only.
- The rules baseline is hand-written against the generator's public code.
- The judges are models, and each audit part is a sample of 30 items.
- Each model was run once on test. The intervals cover item and league sampling only, and with 18 leagues they somewhat under-cover.
- Only gaps to D3 27B are paired. The order among the other models is not tested.
- D3 9B's binding MCC fell from 0.293 on dev to 0.000 on test. The cause is not identified.
- Laya 0.4B's abstentions are scored as NONE answers, as pre-registered. TEV1 0.8B and D1-Omni 600M are at chance.
- Q_N is a diagnostic, and strict Q_N is 0 for every repaired build except TEV1 0.8B.
- Depth is a gold-side tag, and the depth accuracies are descriptive.
- Card bounds are conditioned on gold, and the glossed-decoy cue is by design. Whether the glossed-decoy cue stands is an owner decision.
- The round 3 engine binary's provenance is limited (section 11).
- The exact-scale results cannot be reproduced, because the run data was deleted ([exact_scale.md](exact_scale.md)).
- Nimble-v3 is licensed CC-BY-NC-4.0, which is non-commercial.

## 14. Reproducing and extending

- **Results.** The results README has a four-step reproduction section ([results/README.md](../results/README.md), "How to reproduce"). It needs the run directories, which are not in git.
- **Fast tests.** `python -m pytest tests/test_results_bundle.py -q` checks the committed results against the sources, and it needs no runs. The hard-suite and generator tests are `tests/test_hard_suite.py` and `tests/test_fixtures_hard.py`. The gold-view acceptance test is `tests/test_hard_gold_view.py`.
- **Regenerate a fixture.** `python -m mentat_bench.fixtures_hard build --split <split> --out <dir>` ([mentat_hard.md](mentat_hard.md), section 9). A regenerated registered version is a new version, through the corrections process ([corrections.md](corrections.md)).
- **Add a model.** Pin it, conform it, and run a cell ([outside_models.md](outside_models.md); [local_protocol.md](local_protocol.md)).

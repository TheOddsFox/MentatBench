# Mentat-Hard: specification

**Status:** registered 2026-10-30: `mentat_hard_dev` and `mentat_hard_test` registry version 3 (test set revision 3.1) are active; round 2 is superseded. round 1 was signed off by the owner on 2026-10-27, including the MCC pilot bar and the canonical group labels. **round 2** (generator version `"2"`, 2026-10-28) changes the world, the curated suite and the headline. **round 3** (generator version `"3"`, 2026-10-29) adds the rule card (corpus field `context`), fixes the disclosed reading cues and makes the league's rules public to the model; sections 1, 2, 3, 5, 7 and 9 describe round 3 where it differs from round 2. Owner decisions are in the plan and in `docs/remaining-work.md`. **2026-10-29:** the headline was the relation MCC; the binding MCC is secondary. **2026-10-30:** for round 3 and later, the headline is the mean of the relation and group MCCs; the reading MCC stays a diagnostic (see section 5). The four-part and three-part means are no longer scores; section 7's pilot bar was set on the four-part headline and is historical. **revision 3.1** (generator version `"3.1"`, 2026-10-30) changes the relation selection only (section 3): NONE falls to 25% of the relations, two thirds of them are rule-vocabulary distractors, the derived links are drawn at inference depth 2 or more first, and each relation label carries a gold-side `depth`. The world, cards, bindings and the group and reading rows are unchanged in content.

**Purpose.** Mentat-Hard is the main System One panel. It measures how accurately, and how fast, a typed one-pass (System One) model makes the semantic decisions a logical map is built from. It also checks whether those decisions compose into correct answers.

Toy10k is retired for this purpose. Next to toy10k, Mentat-Hard is:
- **smaller**: every cell finishes, at about 1 h for 27B models;
- **harder**: 8 rule types, 30 wording families, hard distractors;
- **exactly gold**: generated world, engine gold, independent brute-force audit;
- **split**: a dev split and a test split with no shared ids, names or held-out wordings.

## 1. World
Each split is a set of independent **leagues**. Each league is one logical component and is generated from a seed.

| Element | Per league |
|---|---|
| Teams | 4–6, with names from a split-specific pool (dev and test pools are disjoint) |
| Score | an integer per team, 0..L, with L = 5–7 |
| Classes | T(team, t) = "score ≥ t"; W(team) = wins; S(team) = runner-up; Q(team) = qualifies |

**Rule types.** Each league draws a seeded subset of rules; rules 7 and 8 are always on. Every rule reaches the model only through its pairwise and group consequences; the decision vocabulary is unchanged.

| # | Rule | Encoding (IR, exact) | Consequences the model can state |
|---|---|---|---|
| 1 | Score ladder | T(x,t+1) → T(x,t) | IMPLIES / IMPLIED_BY |
| 2 | Exactly-k qualify, k = 2..3 | exactly(k, Q) | at_least_k and at_most_k groups |
| 3 | At-least / at-most, varied k | atleast(k, T(·,a)), atmost(k, T(·,b)) | at_least_k / at_most_k groups |
| 4 | Comparison "X outscores Y" | T(Y,t) → T(X,t+1) for t < L (round 1 also had T(X,1) and not T(Y,L); round 2 drops them) | cross-team IMPLIES |
| 5 | Conditional placement "if X wins, Y is runner-up" | exactly-one W; exactly-one S; W(x) → ¬S(x); W(X) → S(Y) | IMPLIES, EXCLUDES, exactly_one |
| 6 | Chains | chains X1 ≥ X2 ≥ … ≥ Xc, each link "at least as many points": T(X(i+1),t) → T(Xi,t); and W(x) → T(x,a) (round 1 also had T(x,a) → Q(x)) | multi-hop IMPLIES (exam closure) |
| 7 | Overlapping groups | three seeded subgroups (`SUBGROUPS` = 3), each later one sharing 1–2 teams with the first, each with exactly-one "seed" | exactly_one, EXCLUDES across the overlap |
| 8 | Base/alt interpretations | 2–3 ambiguous propositions per league ("by the break", "top half"), always on, with two admissible readings (round 3: break = final points s+1 (base) or s (alt); top half = a playoff place (base) or at least M points (alt), one M per league) | UNRESOLVED binding; reading questions (section 3) |

**Version 2 changes to the world (no forced outcomes).** A forced outcome is a class that the base theory fixes (true or false in every model), so the suite cannot ask it. round 1 had three sources:
- the rule-4 unit facts T(X,1) and not T(Y,L);
- the chain-top implication T(x,a) → Q(x), which fixed the top thresholds of a chain whenever k was below the chain length;
- rule 3 counts that interact with chains.

round 2 removes the first two and clamps rule 3 against the chain: k1 ≤ n − c and k2 ≥ c, with the chain drawn first. The generator accepts a league only when its bank shows every class with both values (TARGET_FIXED = MAX_FIXED = 0). **Chain length.** With chains of 4–6 members, every draw tried on the chain leagues (40 sampled draws per league, out of the LEAGUE_TRIES = 100 the generator allows) still pinned some class. The pinned classes were chain thresholds and the chain bottom's winner, which follow from rule 7 needing a team with points in each subgroup together with the chain's zero prefix. round 2 therefore draws chains of **2–3** members, capped at n − k so the k qualifiers can sit outside the chain, and never fewer than 2 (an ordered chain needs two members). The owner accepted 2–3-member chains on 2026-10-28, instead of the plan's 4–6. In practice every accepted chain has **2** members (in the accepted worlds): a 3-member draw passed the zero-constant check 0 times in 31 draws on dev, so the redraw never kept one. The floor applies only when n − k = 1 (n = 4, k = 3), which did not occur in the accepted dev or test worlds.

**Rule card (round 3, corpus field `context`).** Each league has a rule card: a short plain-English statement of its rules, delivered to the model through `context` on each of the league's corpus rows. System One child version 6 and later (#74; version 7 rewords the alt reading instruction) shows it with the relation, group and reading questions, not with bindings. `fixtures_hard.card_sentences` and `render_card` render it from the rule list only: the input is the league's `world_json()` without `scores`, `winner`, `runner_up`, `qualifiers` and `seeds`, and the renderer refuses those keys. The card states the rules as constraints: rules 2-7 each as sentence groups, rule 8 as the two readings. Each group's sentence states exactly the constraints of its template, and the test rebuilds each template's constraints from the league's fields (members, k, q, the count's k and t, the outscores range, the chain links, the winner bound's a, the seed points) and compares them with the constraints at the sentence's indices. Rule 1 (the ladder) has no sentence (it is semantic: points at least t).

**What the card discloses (gold-conditioned parameters).** The card names no gold score, winner or qualifier, but some of its parameters are conditioned on the gold world, because the theory needs them to be true there. These are bounds the generator draws, not gold values. Measured on the final builds (dev / test): (1) the top-half threshold M lies in a range set by the gold non-qualifier or qualifier scores (1..the best non-qualifier's score, or the lowest qualifier's score plus one..L, one M per league); (2) the conditional "if X wins, Y is runner-up" equals the gold (winner, runner-up) pair in 12 of 27 conditionals on dev and 11 of 27 on test, and otherwise is a vacuous pair; (3) q is at most the lowest qualifier's score; (4) each seed bound (seed_min, one per group of an overlap rule; 108 values on dev, 104 on test) is at most the gold seed's points, and equals them in 28 of 108 on dev and 29 of 104 on test; (5) the chain is sorted by gold score; (6) the winner bound a is drawn from 1 to the gold winner's score, so the gold winner satisfies it, and equals the winner's score in 1 of 9 rule-6 leagues on dev and 5 of 15 on test. The bounds are part of the theory, not values read from the gold; the card cannot be produced without them.

Length (round 3, final code): dev 1,101 to 1,439 characters (mean 1,288); test 1,113 to 1,427 characters (mean 1,304). The cap is 2,000 characters (`CARD_MAX`, the corpus field's cap in docs/mentat_hard_contracts.md). Each implicit group's sentence lists all three scope phrases of its theme, since its implicit markets use any of them (the test checks every phrase the league's question texts use appears in its card).

Template checklist (the reviewer checks each line; A, B are teams, Q, W, S, T are the classes of section 1):

| Sentence | Constraints it states | Why it is exactly those |
|---|---|---|
| "Rules of the L League and L Cup. Teams: …." | none | header |
| "Exactly k teams qualify for the playoffs; each qualifier has at least q points." | exactly(k, Q); Q(x) → T(x,q) for every x (rule 2) | the two parts of rule 2, one sentence |
| "The Cup has exactly one winner and one runner-up, two different teams, both playoff qualifiers." | exactly(1,W); exactly(1,S); not(W(x) and S(x)) for every x; W(x) → Q(x) and S(x) → Q(x) for every x (rule 5) | "two different teams" is the atmost per team; "both qualifiers" the two implications |
| "So the 2 qualifiers are exactly the Cup winner and the runner-up." (k = 2 only) | exactly(1, W(x), S(x), not Q(x)) for every x (rule 5) | given exactly(2, Q), exactly(1,W), exactly(1,S) and the sentence above, this is "Q = {W, S}", and the per-team constraint holds iff Q(x) ↔ W(x) or S(x) |
| "If X wins the Cup, Y is runner-up." | W(X) → S(Y) (rule 5) | one conditional, one implication |
| "At least k1 teams have a or more points." / "At most k2 teams have b or more points." | atleast(k1, T(·,a)); atmost(k2, T(·,b)) (rule 3) | the two counts, one each |
| "For each t from 1 to L-1: if Y has at least t points, X has at least t+1." | T(Y,t) → T(X,t+1) for t = 1..L-1 (rule 4) | the outscores pairs, one sentence per pair |
| "X has at least as many points as Y." | T(Y,t) → T(X,t) for t = 1..L (rule 6) | with scores in 0..L this is score(X) ≥ score(Y), one sentence per chain link |
| "The Cup winner has at least a points." | W(x) → T(x,a) for every x (rule 6) | one sentence for the chain's winner constraint |
| "Group G (theme description): members. Exactly one of them is the seed, with at least s points." | exactly(1, seed(G,x) for x in G); seed(G,x) → T(x,s) for x in G (rule 7) | one group: its seed set and the seed's points |
| "No team is the seed of two groups." | atmost(1, seed(G,x) over the groups G of x), for each team x in two or more groups (rule 7) | the cross-group exclusion, one sentence |
| "'By the break' has two readings: (1) final points; (2) final points plus the 1 point every team gets in the Shield round." | none (the readings are rule 8's scoped iffs) | the break reading: base T(x,s+1), alt T(x,s) |
| "'Top half' has two readings: (1) a playoff place; (2) at least M points." | none (the readings are rule 8's scoped iffs) | base Q(x), alt T(x,M), M one per league |

The k = 2 sentence is the only one that is an equivalence under the other sentences rather than a literal translation: the
checklist's equivalence is the reviewer's to verify.

**Ambiguous readings (round 3, rule 8).** A "by the break" proposition at a team's final points s asks for at least t = s+1 points
(the literal reading, base): the bound outcome of T(x, s+1). Its alt is T(x, s), which is the reading that counts the 1 point
every team gets in the Shield round. The two readings therefore disagree in the gold world (T(x,s+1) is false, T(x,s) is
true). The "top half" propositions of a league share one threshold M, drawn once per league with a direction: either the
top-half teams are non-qualifiers with s ≥ M (1 ≤ M ≤ the best non-qualifier's score), or they are qualifiers with s < M
(M ≤ L). Base is the playoff place Q(x), alt is T(x, M), so again the readings disagree in the gold world. The attempt loop
of the league (`_league`) redraws a league whose T(x,M) or other classes are constant in the base. The card states both
readings as the numbered readings above.

**Reading rows (round 3 cue fixes).** An ambiguous row has five members: u, the base option and the alt option (both on u's team),
the same-team near miss (the threshold one step from the wording on the side away from the alt: t+1, or t-2 at the top for
break; M+1 or M-1 for top half), and one other-team distractor of the base's kind from the same league. A decoy row has the
same structure: u (a decoy proposition's outcome), the gold option (the same class, another market), the same-team mirror
(thr(team, alt threshold)), the same-team near miss, and one same-league distractor. Both strata have three options on u's
team. Chain walks in the suite are at most four members (`CHAIN_MAX`). The relation and group rows, and the reading rows, come
in league blocks (leagues in a seeded order, rows shuffled within each block), so consecutive requests share a rule card.

**Sizes (generated, round 2).**

| Split | Leagues | Markets | Outcomes | Classes | Gold constraints | Unresolved outcomes (props) | Paraphrase pairs | Engine constants |
|---|---|---|---|---|---|---|---|---|
| dev | 18 | 1,354 | 2,708 | 975 | 1,590 | 94 (47) | 498 | 0 |
| test | 18 | 1,507 | 3,014 | 1,057 | 1,739 | 92 (46) | 606 | 0 |

**Sizes (generated, round 3; the table above is round 2).** Glossed markets add 180 (dev) and 197 (test) markets of threshold and qualify classes.

| Split | Leagues | Markets | Outcomes | Classes | Gold constraints | Unresolved outcomes (props) | Paraphrase pairs | Glossed markets | Engine constants |
|---|---|---|---|---|---|---|---|---|---|
| dev | 18 | 1,569 | 3,138 | 987 | 1,612 | 100 (50) | 772 | 180 | 0 |
| test | 18 | 1,674 | 3,348 | 1,047 | 1,727 | 92 (46) | 851 | 197 | 0 |

- About 17% of classes get a second and a third market in different wordings, which gives EQUIV pairs (paraphrases, such as "more than 4" for "at least 5"). Every market of a class uses a different style.
- Components stay within one league (at most ~70 classes). No `wsum` is used.

**Gold and audit.**
- Gold is written by `ir.write_submission`.
- Exam answers and suite labels come from mb-engine over all interpretations.
- `audit_brute` re-solves every component. A skipped query (over budget) fails the build.

**Ids.** Ids are `opaque("hard", split, seed, *key)`, so dev and test share no outcome or class id.

**Seeds.** Dev 20261027, test 20261028. Both are registered and regenerable through `corrections.register_generated`; test gold is public.

## 2. Language
- **30 rendering families.** Each family is a coherent style for every proposition kind (threshold, win, runner-up, qualify, placement, dated).
- **Dimensions the families vary:**
  - number phrasing: at least t / t or more / more than t−1 / not fewer than t / fail to reach t (negated, with the label flipped);
  - double negatives;
  - label polarity: Yes/No, or "fail to …" with Yes;
  - event-title paraphrases;
  - implicit vs named groups.
- **Held out.** Families 21–30 appear only in the test split (one third). Dev uses families 1–20; test uses all 30.
- **Group visibility.** Half the subgroups are named in event titles. The other half are implicit: the members' questions share only a paraphrased scope ("…finishes first among the coastal sides"), so membership must be inferred.

## 3. Curated decision suite (per split)
- **Format.** `candidates.jsonl` (public) lists every question. `gold/suite_labels.jsonl` holds the exact label of each.
- **Questions.**
  - **Bindings:** every outcome (2,708 dev, 3,014 test). Label: bound/unresolved (plus missing code).
  - **Relations (6,000 ordered pairs, revision 3.1; the owner's budget of 2026-10-30; round 2 used 10,000).** Built in this order, each step capped by the budget left:
    1. every unscoped generator implies edge (as a class-literal outcome pair, oriented 50/50 by the seeded rng) and every two-argument atmost-1 (EXCLUDES). 1,226 dev, 1,346 test; all asked. A score-ladder edge (one team's thresholds one point apart) is depth 0, every other edge depth 1 (see "Inference depth").
    2. 500 trivial (same-market Yes/No; stratum `trivial`, depth 0) and 800 EQUIV (absolute counts, as in round 3). A pair whose depth is `count` is never drawn.
    3. the derived links (closure implications and exclusions the generator did not state), **deepest tier first**: depth 4 or more, then 3, then 2. Depth 1 and 0 are drawn only if the supply runs out, which it does not on either split. Each tier is water-filled over IMPLIES, IMPLIED_BY and EXCLUDES (each class takes at most its slot of the tier), and IMPLIES and IMPLIED_BY share one pool of implication pairs, each taking half of it (the odd pair to a random side), so their counts are equal by construction. The derived budget is what NONE leaves: 1,974 dev, 1,854 test.
    4. NONE, 25% of the budget (1,500 of 6,000; round 3 had 2,324 dev and 2,280 test, 38–39%). Two thirds (1,000) are **rule-vocabulary** distractors and one third (500) **profile-matched**, cross-league capped as in round 3. Both are profiled on the word overlap (Jaccard deciles) of every drawn positive (the edges, EQUIV and the derived links), and their overlap deciles match the positives' (table below). See "NONE design".
    5. Orientation: IMPLIES and IMPLIED_BY follow the tier balance (derived) or the seeded rng (edges). EQUIV, EXCLUDES and NONE have no direction, so their rows are oriented at random: a < b is no cue.

    Final mix, dev (test in brackets):

    | Label | Stratum | Count |
    |---|---|---|
    | EQUIV | real | 800 (800) |
    | IMPLIES | real | 1,220 (1,242): 562 (624) edges, 658 (618) derived |
    | IMPLIED_BY | real | 1,179 (1,196): 521 (578) edges, 658 (618) derived |
    | EXCLUDES | real | 801 (762): 143 (144) edges, 658 (618) derived |
    | EXCLUDES | trivial (same market) | 500 (500) |
    | NONE | hard_distractor | 1,500 (1,500): 1,000 rule-vocabulary, 500 profile-matched |

    The trivial same-market pairs are labelled EXCLUDES, because that is what the engine finds. An earlier draft listed them under NONE.

    Shares of the 6,000 (test in brackets): NONE **25.0%** (25.0%; the round 3 shares were 38.7% and 38.0%), trivial 8.3% (8.3%), EQUIV 13.3% (13.3%), IMPLIES 20.3% (20.7%), IMPLIED_BY 19.7% (19.9%), EXCLUDES real 13.4% (12.7%). The edges are oriented by the rng, so IMPLIES and IMPLIED_BY differ by 41 (dev) and 46 (test) edges; the derived links differ by none. Dev has no held-out pairs; test has 2,332 of 6,000 (38.9%) with a test-only family, above the 30% pick target because the per-bin and per-tier quotas round up. The relation rows change; the bindings, the groups and the reading rows of revision 3.1 are the round 3 rows in content (see section 9).

    **Inference depth (revision 3.1; gold side only).** Every entailed pair (IMPLIES, IMPLIED_BY, EQUIV, EXCLUDES) has a depth: the fewest rule constraints a careful reader must chain to derive it from the card (`hard_suite.Depths`, an exact 0-1 breadth-first search). The graph has one node per class literal (a class, or its negation). Every unscoped constraint of the gold theory is an edge, with its contrapositive: an implication a ⇒ b; each pairwise exclusion of an atmost-1 or exactly-1 constraint (a ⇒ not b); and, for an exactly-1 constraint of two arguments, the at-least-one edges (not x ⇒ y; none exist in the round 3 theories). An edge costs 1, a rule constraint a reader chains, except a score-ladder step T(x,t+1) ⇒ T(x,t) of one team, which costs 0 wherever it comes from, including the generator's rule-1 ladder rows (rule 1 has no card sentence). Paraphrases (one class in two markets), negations and the readings of an unresolved proposition cost 0; an unresolved outcome is its reading's class under each interpretation, and the entailment must hold in both, so its depth is the larger of the two. IMPLIES (a, b) has the least cost from a to b; IMPLIED_BY the least cost from b to a; EXCLUDES the least cost from a to not b; EQUIV the larger of the two directions. A generator edge that is not a ladder step (and not EQUIV) is depth 1; the build refuses any other value. An entailment that no chain of edges reaches (it needs a counting argument over exactly, atleast or atmost with k of at least 2) has depth `count`: it is reported and never drawn. The build refuses a NONE pair that a chain reaches (soundness of the graph). Depth is an integer, `count`, or null for NONE; the report buckets it as 0, 1, 2, 3, 4+ (four or more), count and none.

    **Degraded mode.** Without `gold/meta.jsonl` (the toy fixtures) no ladder step can be recognized, so every edge costs 1; the generator's ladder rows are then depth 1, and the independent audit uses the same rule. Mentat-Hard fixtures always carry meta.

    **Independent depth audit.** `audit_brute.DepthGraph` recomputes every relation row's depth with its own 0-1 breadth-first search over the theory JSON, the bindings and the meta rows; it shares no code with `Depths`. `hard_suite.audit` checks every decide_relation row (sampled or not), and the generation gate refuses a mismatch.

    **Depth of the asked relations (revision 3.1, dev / test).**

    | Depth | Asked, dev | Asked, test | What it is |
    |---|---|---|---|
    | 0 | 1,725 | 1,770 | EQUIV (800), trivial (500), score-ladder edges (425 dev, 470 test) |
    | 1 | 801 | 876 | generator edges that are not ladder steps (all asked) |
    | 2 | 1,242 | 1,194 | derived, two constraint steps |
    | 3 | 690 | 654 | derived, three steps |
    | 4+ | 42 | 6 | derived, four or more steps |
    | none | 1,500 | 1,500 | NONE |

    **Derived draw by tier (revision 3.1, dev / test, per class).** Every depth 4+ and depth 3 pair is drawn while the budget allows (it does on both splits): IMPLIES, IMPLIED_BY and EXCLUDES each draw 14 / 2 at depth 4+ and 230 / 218 at depth 3, then 414 / 398 at depth 2. No tier below 2 is reached.

    **Depth supply (revision 3.1, dev / test).** Derived pool pairs (unordered; one per implication pair and one per exclusion pair; generator edges and trivial pairs left out). Implications are IMPLIES and IMPLIED_BY together.

    | Kind | 0 | 1 | 2 | 3 | 4+ | count (never drawn) |
    |---|---|---|---|---|---|---|
    | implication, dev | 6,485 | 8,772 | 2,434 | 460 | 28 | 1,682 |
    | exclusion, dev | 4,999 | 4,572 | 1,217 | 230 | 14 | 841 |
    | implication, test | 7,196 | 9,516 | 2,260 | 436 | 4 | 1,936 |
    | exclusion, test | 5,535 | 4,980 | 1,130 | 218 | 2 | 968 |

    The depth-0 and depth-1 supply is large because a paraphrase of a ladder edge, or of a one-constraint edge, is depth 0 or 1 as the definition says; none of it is drawn while depth 2 or more is available.

    **NONE design (revision 3.1).** A NONE pair is entailed under no interpretation (the engine's label, NONE; the audit re-derives every NONE row with `audit_brute`, see the audit counts below). The rule-vocabulary pairs (1,000 of 1,500) are same-league pairs, not in one market, that share a team, a threshold (the same t), a draw group (the same subgroup) or a constraint of the theory (both classes in one unscoped constraint, one card sentence's constraint). Same-team adjacent thresholds are ladder-linked, so they are IMPLIES and never NONE. Supply: 43,987 such pairs on dev among 241,336 within-league NONE pairs. The profile-matched third is the round 3 construction with its cross-league cap. **Cross-league cue:** a cross-league NONE pair can be recognized from its event title (the league's name), so the cross-league cap is reported: 54 cross-league pairs on dev (3.6% of the NONE stratum) and 56 on test (3.7%). The first revision 3.1 build had 69 on each split; the count moved when the overlap reference began to include the derived links. The build does not tag the NONE sub-kind (`stratum` stays `hard_distractor`); the build report gives the counts (`none`).

    **Word overlap per label (revision 3.1; Jaccard deciles of the two questions' words; percent of the label's asked rows).** The EXCLUDES row includes the 500 trivial pairs, all in the top decile (same market, same words).

    Dev:

    | label | 0.0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | n |
    |---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
    | EQUIV | 0.0 | 1.9 | 15.0 | 35.0 | 24.2 | 14.2 | 4.9 | 3.4 | 0.8 | 0.6 | 800 |
    | IMPLIES | 4.0 | 29.5 | 38.6 | 16.1 | 6.1 | 3.4 | 0.8 | 0.2 | 1.2 | 0.0 | 1,220 |
    | IMPLIED_BY | 3.6 | 31.5 | 35.3 | 16.5 | 7.7 | 2.5 | 1.1 | 0.5 | 1.3 | 0.0 | 1,179 |
    | EXCLUDES | 3.5 | 22.2 | 20.5 | 8.2 | 4.6 | 1.4 | 1.0 | 0.0 | 0.1 | 38.4 | 1,301 |
    | NONE | 3.5 | 25.9 | 31.9 | 19.5 | 10.5 | 5.1 | 1.9 | 0.9 | 1.0 | 0.0 | 1,500 |

    Test:

    | label | 0.0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | n |
    |---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
    | EQUIV | 0.0 | 0.8 | 20.2 | 36.4 | 20.8 | 14.2 | 4.2 | 2.1 | 1.0 | 0.2 | 800 |
    | IMPLIES | 2.5 | 28.9 | 40.5 | 17.2 | 6.7 | 1.9 | 0.4 | 0.6 | 1.4 | 0.0 | 1,242 |
    | IMPLIED_BY | 2.1 | 31.7 | 36.8 | 18.3 | 6.4 | 3.4 | 0.3 | 0.3 | 0.8 | 0.0 | 1,196 |
    | EXCLUDES | 1.9 | 21.2 | 22.3 | 9.2 | 3.7 | 1.2 | 0.7 | 0.1 | 0.0 | 39.6 | 1,262 |
    | NONE | 2.1 | 25.3 | 34.7 | 21.0 | 9.3 | 4.8 | 1.3 | 0.7 | 0.9 | 0.0 | 1,500 |

    **Rules baseline (heuristic child, relation MCC, dev).** round 3 dev: 0.305; revision 3.1 dev: 0.250. The rules baseline is a hand-written reference, not a model result; the value is deterministic (the two revision 3.1 dev builds are byte-identical).

    **Audit counts (revision 3.1 final builds, `audit` with sample = every row).** dev: `audit_brute` checked 3,138 bindings, 1,200 group and reading rows, and 6,000 relations, with 0 mismatches; the world check (`world_check`) checked 5,805 relations, 973 group rows and 200 readings, with 0 disagreements; the independent depth audit checked all 6,000 relation depths, with 0 mismatches; all 1,500 NONE rows (in the NONE cell `decide_relation|NONE|hard_distractor`) are entailed under no interpretation. test: 3,348 bindings, 1,184 group and reading rows, and 6,000 relations, with 0 mismatches; the world check checked 5,832 relations, 976 group rows and 184 readings, with 0 disagreements; the depth audit checked all 6,000 relation depths, with 0 mismatches; all 1,500 NONE rows are entailed under no interpretation. The audit was run with sample = 10^9 (every row of every cell; `hard_suite.audit`), and its output is kept in the verification scratch directory.

    At most k with k = 1 is named at_most_one in the canonical group labels.
  - **Groups (1,000 candidate member sets, 2–8 members).**
    1. Every unscoped gold cardinality constraint, as its own set, with no cap: dev has 377 such constraints over 375 distinct member sets (two constraints repeat a set already present); test has 393 constraints over 393 distinct sets.
    2. About 30% decoys (terms `[]`, label none): 300 on each split.
    3. Subsets and chains (walks on the implications), water-filled over the remainder: 325 dev, 307 test.

    **Full-cardinality labels.** A group row carries `terms`: the canonical `group_terms(lo, hi, n, chain)` of the exact bounds (`adapters/base.py`). Its `label` and `k` are `terms[0]`. Order: ordered_chain, then exactly_one or at_least_k, then at_most_one or at_most_k. An exactly-2 qualify set is `[at_least_k 2, at_most_k 2]`, and a chain carries `order`, the members strongest first.

    **Bound convention (every interpretation).** The terms are bounds that hold in every interpretation of the gold theory. `lo` is the largest k such that at-least-k is valid, and `hi` the smallest k such that at-most-k is valid, where *valid* means unsatisfiable in every interpretation. A feasibility answer of UNRESOLVED (satisfiable in some interpretations and not in others) therefore counts as not valid, and a bound is stated only when every interpretation forces it. 27 dev and 24 test of the 1,000 curated group rows contain an ambiguous (unresolved) member (recomputed from `candidates.jsonl` and `gold/bindings.jsonl`: test 13 real and 11 decoy); their bounds are the every-interpretation bounds, so they can be weaker than the bounds of any single interpretation.
  - **Reading questions (round 3; dev 100 + 100, test 92 + 92 rows, ambiguous + decoy; round 2 counts are in section 9).** A `decide_group` row with `source: "reading"`, members `[u, o1..o4]`, placed after the group rows with qid prefix `g`, in league blocks. Every row has three options on u's team (two readings or gold and mirror, and a same-team near miss) and one other-team distractor of u's league (of the base's or alt's kind in ambiguous rows).
    - **Ambiguous rows** (u unresolved; one row for each of the Yes and the No outcome of each ambiguous proposition): the options are the base and alt outcomes, the bound outcome of each reading's class with u's polarity, both on u's team. The base is the literal reading: the threshold the proposition states. One other-team outcome, of the base's or alt's kind, is the distractor, so each ambiguous row has three options on u's team and one other-team option (checked on all 184 test reading rows, ambiguous and decoy). Label `readings: {base, alt}`, stratum `reading`.
    - **Decoy propositions** (u bound; stratum `reading_decoy`), one per ambiguous proposition, built as **complement pairs** of **glossed markets** (round 3): the two outcomes of a glossed market m1 of a paraphrase class (its question states the reading, "...counted on final points?" or "...meaning a playoff place?") are the two decoy u's (flipped polarity). Each row's gold option is the same-polarity outcome of another market m2 of that class (the answer for both readings). The same-team mirror is thr(team, alt threshold), the same-team near miss is the threshold `near_threshold` gives for the partner proposition's league, and one distractor of the same league and kind completes the row. Among feasible decoys the least family overspend is taken (up to 40 candidates; the same-team options and the distractor are chosen by family need). A repair pass then swaps options in either stratum (a same-team option for another market of its class, a distractor for another class of the same league) until the option wording families match.
    - **Equalized cues** (dev, exactly; test, see the round 3 note below): option kinds, same-team count, number-match rows, u's complement twins (cross-row), option twins, negated flags (u and options), base and other-team thresholds, option wording families and held-out rows. Decoy u's are glossed by construction (see the wording cue). The one cue not equalized by design is u's own kind: an ambiguous u is an unresolved proposition ("by the break", "top half"), so its wording is the cue, by design, and the binding question tests the same wording cue.
    - **Complement coverage.** Both outcomes of every ambiguous proposition have a reading row. An uncovered outcome (a missing row) counts once in `uncovered`; a proposition with one row is reported as `complement_incomplete` and is already in that count.
    - **Uniqueness.** Each reading's option is the only option equivalent to u under that reading: the other reading's option is not equivalent to u under r. The audit checks this for every ambiguous row (`the other reading's option is equivalent to u under r`); no row fails on either split.
    - Reading rows are shuffled among themselves; no two group or reading rows share a member set.
    - **Wording cue (round 3; replaces the round 2 cue, which the owner accepted with disclosure).** The ambiguous u's carry the generator's phrases ("by the break", "top half"). In round 3 the decoy u's are glossed markets: they carry the same phrases, each with its reading stated ("counted on final points", "meaning a playoff place"). Measured on the final builds: a regex for `break|top half` predicts the reading stratum at 0.52 on dev and 0.48 on test (chance), so the round 2 phrase cue is gone. The gloss wording identifies the decoy stratum at 1.0 on both splits, so a lexical cue of the gloss replaces it. The gloss is a perfect stratum cue by design: a glossed phrase has only one reading, so a glossed u is always a bound decoy and never an unresolved ambiguous proposition. This follows the owner's glossed-market design ("draw decoy reading u's from them"); whether it stands is the owner's decision. On the final builds both splits match their option families, and the test split's held-out rows match (see the statistics below).

    **Reading stratum statistics, round 3** (`reading_stats` on the final builds). Dev: 100 ambiguous and 100 decoy rows; three options on u's team in every row of both strata; option kinds (qual 76, thr 324), number matches (62), u's complement twins (100), option twins (100), held rows (0), negated u (50), negated options (200), base and other-team threshold histograms, and the option wording families are identical in the two strata; decoy shortfall 0, family residual 0. Test: 92 ambiguous and 92 decoy rows; three options on u's team in every row; option kinds (qual 60, thr 308 in both strata), number matches (62), u's complement twins (92), option twins (92), held rows (79 in both strata), negated u (46), negated options (184), and the option wording families are identical; decoy shortfall 0, family residual 0. The earlier draw had a test shortfall of 4 (four propositions without a decoy) and a residual of 32 option slots; those are gone because the gold worlds changed when the winner bound was drawn from 1..the winner's score (the seeded redraw, not a targeted fix). The slow test `test_reading_strata_match_on_both_splits` pins the counts on both splits.

**Reading stratum statistics (round 2, historical)** (`reading_stats`, built on each split; the test compares the strata):

    | Split | Stratum | Rows | Options on u's team | Option kinds (qual / thr) | Number-match rows | u's complement twins | Option twins | Held-out rows | Negated u / options | Kind of u |
    |---|---|---|---|---|---|---|---|---|---|---|
    | dev | ambiguous | 94 | 2 in all 94 | 88 / 288 | 50 | 94 | 94 | 0 | 47 / 188 | dated in all 94 |
    | dev | decoy | 94 | 2 in all 94 | 88 / 288 | 50 | 94 | 94 | 0 | 47 / 188 | qual 44, thr 50 |
    | test | ambiguous | 92 | 2 in all 92 | 80 / 288 | 52 | 92 | 92 | 72 (78%) | 46 / 184 | dated in all 92 |
    | test | decoy | 88 | 2 in all 88 | 80 / 272 | 48 | 88 | 88 | 72 (82%) | 44 / 176 | qual 40, thr 48 |

    Dev has no test-only families, so held-out rows are 0 there. On dev every cue above matches exactly: the threshold histograms (base and other same-team option), the option family histograms, the option kinds and the polarity counts. On test three cues do **not** match exactly, and the build reports them: (1) two of the 46 ambiguous propositions (base thresholds 3 and 4) have no decoy proposition: the search over paraphrase classes, markets and mirrors found none that meets the kind, threshold, test-only and row-status constraints (`decoy_shortfall` 2), so the decoy stratum has 88 rows against 92; (2) 16 option slots in the decoys lack their family after the repair pass, which found no admissible swap for them (`family_residual` 16); (3) the held-out share is 78% against 82%, which follows from (1). These are the cues this build does not equalize on test; the fallback for (1) is to pair with a same-team paraphrase of another threshold, which breaks the threshold match, and we did not take it. A top-half u states no number, so only break propositions can match on number.

- **Disclosure.** Candidate selection is gold-informed, so this suite does not test blocking (which pairs a system chooses to ask).

**Self-checks.** `build` reports `uncovered` (generator edges, cardinality constraints, and unresolved outcomes without a reading row; a proposition with one Yes/No row is counted once, through its missing row) and the constant count; `generate` refuses a split with either non-zero. `audit` re-derives the group terms from the exact bounds and audits **every** reading row (the generation gate; other cells are sampled). For each reading row it checks, with `audit_brute`'s `Checker.satisfiable`, that u has the same truth value as each reading's option **in that reading's interpretation** (an ambiguous row: u ≡ base option under base, u ≡ alt option under alt); that each decoy's gold option is equivalent to u in every interpretation; and that no distractor is equivalent to u in any interpretation. `world_check` checks reading options against `gold/world.json` (class, kind, team, t and polarity), that the literal base is u's threshold, that the alt and the decoy's same-team option are neighbouring thresholds, and the group bounds against the declared rules.

## 4. Exam (end to end)
About 200 queries, finalized by the engine (`fixtures_gen.finalize` pattern), stored with the split:

| Family | Count | Shape |
|---|---|---|
| closure | 80 | 8–16 premises spanning at least 2 rule types; outputs across the league |
| feasibility | 50 | SAT/UNSAT only jointly (no pairwise conflict), plus UNRESOLVED cases |
| partition | 40 | exhaustive/exclusive claims over implicit and named groups |
| selection | 30 | equivalence and implication sets that include paraphrases |

The round 2 exam finalizes with every expectation met on both splits (the same construction as round 1; the forced-outcome removal does not change any expectation).

Scored with the existing `score_run`. Q_N and strict Q_N are diagnostics (section 5), not the headline.

## 5. Run and metrics
**One run per model.** `runcell --candidates <split>/candidates.jsonl --relation-floor <dev floor>`:
- the build asks every curated question, one question per request;
- it logs every raw answer, with per-option probabilities, to `decisions.jsonl`;
- it compiles with the floor, then seals, evaluates and scores.

**Server setup.** Two Ollama servers, one per A6000, with `MB_S1_PARALLEL=2`. The same 1.5 h budget for every model; every model must finish.

**Metrics (scored on raw answers).**
- **Headline:** for round 3 and later fixtures (owner decision 2026-10-30), the **mean of the relation MCC and the group MCC** (`metric` `relation_group_mcc`; `parts` `relation_mcc` and `group_mcc`). The relation MCC is the multiclass MCC of the relation decisions (5 classes); the group MCC is the MCC of the group decisions (the canonical terms), answerable in round 3 after the two-judge audit of 2026-10-30. For round 1 and round 2 fixtures the headline is the relation MCC alone from the owner decision of 2026-10-29 (`metric` `relation_mcc`); the round 1 record of 2026-10-28 published the mean of three MCCs (relations, groups, bindings). **Secondary:** the binding MCC (status), reported with its league-bootstrap interval. **Diagnostics, no score or mean:** the reading MCC (`decide_reading`: each reading's option, or none), which has known validity problems (`docs/history/round-2/headline-change-disclosure.md`). The per-op MCCs are in `ops.<op>.mcc` of `score_decisions.json`. The earlier four-part mean (`headline.mcc_mean`) and the three-part mean (`mcc_mean_3`) are not scores any more: a post-hoc change from the pre-registered four-part mean, justified by that audit.
- **Secondary:**
  - macro-F1;
  - per-op and per-class accuracy;
  - Brier and ECE from the per-option probabilities;
  - seen vs held-out wording;
  - items/s, p50/p95 and wall time.
- **Diagnostics, never a ranking or a headline:** Q_N and strict Q_N of the end-to-end exam, using the floored compile. Every build is repaired, so strict Q_N is 0, and the rules baseline outscores the models on Q_N; both facts are why Q_N is not the headline.

**Baselines.**
- **empty:** builds nothing.
- **rules:** the heuristic child, never tuned on test.
- **oracle:** for the exam, full replay of gold. For the suite, `gold_view` answers every curated question, giving MCC 1.0 as a sanity check. For round 2 and round 3, `gold_view` must also score Q_N = 100 (the acceptance test of stream 6: `tests/test_hard_gold_view.py` on the committed round 3 fixtures).

**Floors.** Per model, set on dev: the lowest `--relation-floor` at which precision (exact, from suite labels) is ≥ 0.8. Raw decisions are scored unfloored.

## 6. Runtime estimate
Request rates are the ones measured on 2026-10-27 with one question per request (`docs/history/notes/systemone-dev-notes-20261027.md`). The relation rate is reduced by 10% for the 5-option menu.

| Model | Bindings (~2.6k requests) | Relations (6k, round 3; was 10k) | Groups (~1.3k requests) | Total |
|---|---|---|---|---|
| clef:27b-q8_0 | 7.6 min | 41 min | 9 min | ~58 min |
| d3:27b-q8_0 | 6.8 min | 31 min | 11 min | ~50 min |
| pplx-decider:27b | 7.2 min | 33 min | 6 min | ~47 min |
| nimble:9b-bf16 | 3.1 min | 16 min | 2.4 min | ~22 min |
| d3:9b-q8_0 / kev:9b | 2–2.5 min | 11–12 min | 2 min | ~16 min |
| laya | 1 min | 2.4 min | 0.4 min | ~4 min |

The table is the round 1 estimate. round 2 adds reading rows to the group column (dev: 1,000 group rows plus 188 reading rows); the round 2 pilot re-measures the column. The two-GPU matrix (8 models) takes about 2.5 h. The relation count is the main knob: 27B models stay at or under about 1 h with 10k relations.

**Generation time (round 2; machine- and load-dependent, not a benchmark).** On the authoring machine the full build (generate, engine finalize and audit, suite, suite audit) took 251 s for dev and 374 s for test, of which the suite's engine queries took 132 s and 146 s; the generator alone takes 5 s (dev) and 12 s (test). The verifier measured 187 s (dev) and 421 s (test) wall time under load. These depend on the machine and its load.

## 7. Pilot bar (dev only; fixed before the pilot)
Pilot models: D3 27B, Laya and the rules baseline. The pilot bar was set on the **4-part headline** (section 5) before the 2026-10-29 owner decision. It is historical: the bars below are kept for the record, and they are not a score or a gate now.
- **Headline MCC (historical, four-part mean):** D3 27B ≤ 0.80; rules ≤ 0.50; D3 27B − Laya ≥ 0.25. These are the owner's accuracy bars (≤ 85%, ≤ 60%, ≥ 20 points) restated on the MCC scale.
- **Runtime:** 27B ≤ 1 h; 9B ≤ 20 min.

If the bar is missed, these knobs are tuned on dev only, then frozen:
- the distractor share and hardness;
- the implicit-group share;
- the paraphrase share;
- the rule mix per league;
- L.

The round 2 pilot checks that the reading questions are not lexically easy (the rules baseline could lift on them), and that the chain change keeps the relation mix (section 9).

The test split is never used for tuning.

**Deviations from the signed-off text (generator, 2026-10-27).**
- *Paraphrase share: 17% of classes with three markets, not 15% with two.* The curated suite asks 800 EQUIV pairs (8% of the round 2 10,000 relation questions; 13% of the round 3 6,000), each unordered pair once. 15% gave about 300 same-meaning outcome pairs per split; a third market adds four such pairs for two outcomes, so triple paraphrases give about 1,000 (dev) and 1,200 (test) pairs on outcomes the base theory leaves free. Both shares are generator parameters.
- *Exactly-one supply.* The suite asks at least 140 exactly_one groups (173 dev, 186 test). Every league has rule 7 with three seeded subgroups (not a drawn rule with two), k = 2 in about 5 of 6 leagues, and with k = 2 the implied exactly(1, W(x), S(x), not Q(x)) per team is stated in the theory.
- *Rule 6 chains are non-strict ("at least as many points").* Strict links (T(Y,t) → T(X,t+1)) inside scores 0..L fix most of a chain's thresholds in every world, so non-strict links keep the multi-hop implications. In round 2 the chains are shorter (section 9).

## 8. Limits (disclosed in results)
- Synthetic, single domain (a fictional sports league).
- The curated suite does not test blocking.
- Gold-informed candidates.
- Held-out wording covers phrasing only, not new rule types.
- The rules baseline is hand-written against the generator's public code, so it is an upper bound on what template-matching achieves.
- Closure and selection queries carry the query league's outcomes as their universe, so `queries.jsonl` exposes each league's outcome set.
- Ids are derived from public keys and the seed (`opaque("hard", split, seed, *key)`), so they are reproducible from the generator; this reveals nothing beyond the gold, which is public for both splits.
- Public-cue baselines for groups (build report, `shared_specific_word_acc`: 0.74 dev, 0.77 test, against a real share of 0.70) sit slightly above the real share, so the decoys are not fully separated by those cues. The reading rows have no public-cue baseline yet; the round 2 pilot checks whether the rules baseline gains on them.

## 9. Versions and owner checkpoints
- **revision 3.1** (generator `"3.1"`, 2026-10-30; owner decision on a harder relation suite): the relation mix of section 3 (NONE 25% of the 6,000 relations, from 38.7%; two thirds rule-vocabulary distractors; derived links drawn deepest tier first, depth 4+, 3, then 2, and every depth-3-or-more pair drawn while the budget allows; score-ladder steps cost 0 in the depth; the gold-side `depth` tag and the independent depth audit). The corpus and the rule cards are byte-identical to round 3 on both splits (the generator is unchanged; only `world.json` and `fixture.json` carry the new version). Bindings, group and reading rows are identical in content to round 3. Binding qids are unchanged; group and reading qids change, because the league-block shuffle consumes a different number of draws once the relation rows change. **Reason:** in the round 3 timing spike on a scratch dev build (run not committed), D3 27B scored a relation MCC of 0.815 (the headline) against 0.24 for groups. About 1,226 of the 6,000 dev relations are generator edges, each stated almost verbatim on the card, and a two-judge audit found every question type answerable from the prompt text. The revision 3.1 mix asks for deeper inference (the depth field lets the scorer report accuracy by depth) rather than obscurity: the distractors share the vocabulary of the rules, and the derived links are the deepest the supply allows (depth 2 or more for all of them on both splits).
- **round 3** (generator `"3"`, 2026-10-29): the rule card (corpus `context`, section 2), the break reading at final points s+1 with alt s (no coin flip), one top-half threshold M per league with readings that disagree in the gold world, glossed markets, reading rows with three same-team options (a near miss, and a decoy's mirror and near miss), same-league distractors, chain walks of at most four members, and league blocks for relation, group and reading rows. Build: `python -m mentat_bench.fixtures_hard build --split S --out DIR`. Registered 2026-10-30 as registry version 3 (its fixture.json says 3.1: the revision 3.1 bullet below); the committed fixtures are that build.
- **round 1** (generator `"1"`) is the signed-off 2026-10-27 world. It regenerates only at commit `7749647`, and its registration stays superseded in `fixtures/VERSIONS.json`.
- **round 2** (generator `"2"`, 2026-10-28) is this document's world, with reading questions, full-cardinality labels, the four-part headline, no forced outcomes and 2–3 chain members. Registered 2026-10-29; superseded by registry version 3 on 2026-10-30.
- **Owner decision (accepted 2026-10-28):** chains of 2–3 members instead of 4–6 (section 1), with every accepted chain at 2 members. The rule-3 clamps the plan asked for are in place as well.
- round 1 and round 2 headlines were not directly comparable: round 2 adds the reading component, and group MCC on full cardinality labels is expected to fall. Since 2026-10-29 the headline was the relation MCC; since 2026-10-30 it is, for round 3 and later, the mean of the relation and group MCCs (the round 1 record of 2026-10-28 published the three-part mean; from 2026-10-29 round 1 and round 2 are scored by relation MCC). The 3-part and 4-part means are historical. Compare round 1 and round 2 relation MCCs only as the validity audit (`docs/history/round-2/headline-change-disclosure.md`) allows.

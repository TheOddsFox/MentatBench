# v0.2.0 group items: why group MCC is low

Written 2026-11-03. **Exploratory: not pre-registered.** This analysis changes no score, gold label or flag, and it proposes
no change to v0.2.0. It asks why the group MCC is low (the reference model scores 0.254 on groups against 0.788 on relations)
and whether the group labels are at fault.

`analyse.py` computes every number below from committed files (the v0.2.0 headline predictions of all 17 models and the test
fixture) and writes [`results.json`](results.json), deterministically. It checks that its group MCC for each model equals the
published `metrics.group.point`. Run it from the repository root with `python -B docs/audits/v0.2.0-group-items/analyse.py`.

## How a group item is scored

A group item is a set of 2 to 8 outcomes. Its gold class is the tightest constraint that holds in every interpretation of the
league's rules: lower and upper bounds on how many members are true, and an ordered chain where each member implies the next
(`group_terms` in `adapters/base.py`). Exactly one holds is `exactly_one`, at most one is `at_most_one`, and other bounds carry
their k (`at_least_k:2+at_most_k:3`). The 300 decoys have no constraint (`none`). The class must match exactly, so an answer
that is true but weaker than the gold counts as wrong.

The System One child asks for the kind first. An answer of `none` ends the question. Otherwise it asks k (for `at_least_k` and
`at_most_k`), the chain's order (for `ordered_chain`), and then each bound the kind leaves open. An `ordered_chain` answer on a
set whose members include both sides of one binary market gets no decision, which the scorer also records as `none`; this
case is not counted separately here.

Below, an answer is **true but weaker** when every bound it states is entailed by the rules but it is not the gold class, and
**not entailed** otherwise (some world allowed by the rules breaks it).

## Findings

**1. None of the 11 flagged group labels is shown to be wrong.** The v0.1.1 item audit left 11 group items as `suspect_label`
([notes](../v0.1.1-item-audit/notes.md), part (b)). Its exploratory bound-based reading (part (d)) already resolved nine, all
with gold `at_most_k`: judge 2 gave the gold bounds under the kind label `none` in all nine, and judge 1 in one (g00596). The
other two are worked by hand from the rule card below, and the gold is correct in both. These derivations are by the analyst,
not an independent adjudication, and this note checks no other group label.

**2. Count and chain items are almost never answered correctly.** 276 of the 1,000 items have a gold with a k or an ordered
chain. Across the 17 models, 1.2% of the answers to them are correct; 39.6% are `none`, 52.4% another kind, and 6.8% the right
kind with a wrong k. 229 items are answered correctly by no model: all real items, 226 of them count or chain items (67
`at_most_k`, 65 `ordered_chain`, 59 `at_least_k`, 35 `at_least_k+at_most_k`) and 3 `at_most_one`.

| Gold kind | Items | Accuracy, all models | Accuracy, D3 27B |
|---|---|---|---|
| none (decoys) | 300 | 0.387 | 0.277 |
| at_most_one | 239 | 0.195 | 0.883 |
| exactly_one | 185 | 0.351 | 0.292 |
| at_most_k | 81 | 0.012 | 0.074 |
| ordered_chain | 79 | 0.011 | 0.165 |
| at_least_k | 63 | 0.004 | 0.032 |
| at_least_k+at_most_k | 53 | 0.021 | 0.057 |

**3. Accuracy falls with set size overall, though not at every step.** Real items only:

| Members | Items | Accuracy, all models | Accuracy, D3 27B |
|---|---|---|---|
| 2 | 253 | 0.161 | 0.751 |
| 3 | 246 | 0.233 | 0.309 |
| 4 | 55 | 0.108 | 0.236 |
| 5 | 56 | 0.066 | 0.071 |
| 6 | 69 | 0.099 | 0.087 |
| 7 | 12 | 0.000 | 0.000 |
| 8 | 9 | 0.000 | 0.000 |

For D3 27B the group MCC is 0.313 on the decoys with the two- and three-member items and 0.042 on the decoys with the four- to
eight-member items (all items: 0.254). Five models score higher on the second subset than the first: Kev 9B (0.179 against
0.155), D1 3B, D3 2B, Kev 0.8B and D1-Omni 600M (`mcc_by_set_size` in `results.json`).

**4. Most errors are in the kind rather than in k.** With k dropped from both gold and answer, the group MCC changes by -0.004
to +0.048 across the 17 models (D3 27B: 0.254 to 0.285; `per_model` in `results.json`). On the count and chain items, 6.8% of
answers have the right kind with a wrong k, against 52.4% with another kind (finding 2).

**5. The kind-first question shapes the errors.** Of the 13,076 wrong answers, 4,405 (34%) are `none` on an item that has a
constraint, which ends the question before any bound is asked. 1,400 of these come from the two models that answer `none` on
every item (Kev 4B and TEV1 0.8B, flagged `constant_group_output`); without them it is 3,005 of 11,676 (26%). Another 692 wrong
answers are true but weaker under another kind or k, and 7,979 are not entailed. D3 27B answers `at_most_one` on 91 of the 185
`exactly_one` items: true, but it misses that one member must hold. That happens both on a card's own seed groups ("Exactly one
of them is the seed"; 21 of 54, with 30 correct) and on derived sets (70 of 131, with 24 correct). The v0.1.1 judges reported
the same difficulty: its instructions did not say which kind to give when a set's count range fits more than one kind
([notes](../v0.1.1-item-audit/notes.md)).

**6. The leader over-claims on decoys.** D3 27B states a constraint on 217 of the 300 decoys, which have none.

**7. Groups still carry signal.** The group intervals are about as wide as the relation intervals (median width 0.049
against 0.045 over the 17 models), the group-only ranking resolves 80 of the 136 pairs (relation-only: 124)
([analysis.json](../../../results/v0.2.0/analysis.json), `weighting`), and it orders the models differently from relations.

## The two remaining suspects, worked by hand

**g00453** (Lunova; gold `at_least_k:1`; both v0.1.1 judges answered at least 2, at most 4). The members are: (a) Jormere has at
least 1 point; (b) Falholt is the Founding seed; (c) Gorby has more than 0 points; (d) Jormere has fewer than 3 points.

- At least one holds: if (a) and (c) are both false, Jormere and Gorby have 0 points, and so does Jorham (Jormere has at least
  as many points as Jorham). Only three teams are left to have a point, against "at least 4 teams have 1 point or more". If (a)
  is false, (d) is true.
- Exactly one can hold: Jormere 3 (Coastal seed, Cup winner), Prymoor 2 (Founding seed, runner-up), Jorham 1, Falholt 1
  (Northern seed), Dunham 0, Gorby 0. Every card rule holds: two qualifiers with at least 2 points each, Falholt does not win,
  four teams with a point, one with 3 or more, Dunham's ladder is vacuous at 0, Jormere at least Jorham, the winner has at
  least 2, the three seeds meet their point bounds and are different teams. Only (a) is true. So at least 2 is not forced,
  and the gold lower bound of 1 stands.
- All four can hold: Jormere 2 (Northern seed, Cup winner), Prymoor 2 (Coastal seed, runner-up), Falholt 1 (Founding seed),
  Gorby 1, Jorham 0, Dunham 0. So there is no upper bound below 4, as the gold says.

**g00551** (Jorova; gold `ordered_chain` on two members; both v0.1.1 judges answered `none`; 11 of the 17 models answer
`exactly_one`). The members are: (p) Pryham has at least 1 point; (q) Ashmoor has fewer than 1 point.

- (q) implies (p): with Ashmoor and Pryham at 0, Jorwick is at 0 too (Pryham has at least as many points as Jorwick), leaving
  Gorham, Karham and Belstead, three teams against "at least 4 teams have 1 point or more". So the chain holds.
- Both can hold: Ashmoor 0, Pryham 1, Jorwick 0, Belstead 5 (Cup winner, Founding seed), Karham 1 (runner-up, Riverside seed),
  Gorham 1 (Coastal seed).
- Neither can hold: Pryham 0, Jorwick 0, Ashmoor 5 (Cup winner, Coastal seed), Belstead 1 (runner-up, Founding seed), Karham 1
  (Riverside seed), Gorham 1.
- So neither `exactly_one` nor `at_most_one` holds, and the gold `ordered_chain` alone is right.

## What this suggests

None of this is a case for changing v0.2.0 scores or weights after the fact. For the next test-set revision, decided before
any results:

1. Ask for the bounds directly ("at least how many must hold", "at most how many can hold") and the chain separately, instead
   of the kind first. This removes the `none` exit and the choice between kind labels that fit the same bounds.
2. Report the five- to eight-member items as a separate hard band (as for relation depth), or cap them: accuracy on them is
   below 0.1 for D3 27B and for all models pooled (finding 3).
3. Report group accuracy by set size and gold kind beside the group MCC.

## Limits

- Model answers come from one run per model (the v0.2.0 headline run). The repeat runs agree closely (see the v0.2.0 analysis,
  `robustness`).
- A card seed group is found by matching the teams named in the members' texts against the card's group lines; a member whose
  text names no card team, or several, makes the item count as derived.
- The hand derivations are the analyst's. They were re-derived by a second model, not checked by a person.

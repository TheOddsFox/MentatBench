# Mentat-Hard v2 test results, 2026-10-29

> **Superseded (2026-10-30).** This is the v2 record. The current results are Mentat-Hard v3 (fixture 3.1):
> `mentat-hard-v3-20261030.md` (test) and `mentat-hard-v3-private-20261030.md` (private split). The v2 numbers are kept as a record.

> **Headline changed (2026-10-29, post-hoc).** The public headline is now relation MCC, and binding MCC is secondary.
> Group and reading MCC, the four-part and three-part means, and Q_N are diagnostics only. The four-part numbers below
> are the pre-registered record. See the addendum at the end and `mentat-hard-v2-validity-audit-20261029.md`. In
> `mentat-hard-v2-20261029-ci.json`, the keys `headline` and `mcc_mean_3` are those legacy means.

This document reports the pre-registered test matrix, run as specified in `mentat-hard-v2-prereg-20261029.md` (PR #67),
apart from the two deviations listed under Setup. The test was run once per model, after the pre-registration was merged.
The numbers are in `mentat-hard-v2-20261029.json` and `mentat-hard-v2-20261029-ci.json`. The run directories are in
`runs/20261029-v2-test/` (not committed). An independent verifier re-derived the results table, the secondary table and the intervals from the run
directories before publication; its corrections are applied in this text. The limits were not re-derived.

## Setup
- **Code:** commit 36cd81e (the pre-registration commit), whose python tree is identical to b8990f1. Child v5
  (`prompt_sha256` 15c9c1ce…).
- **Fixture:** `mentat_hard_test` v2 (1f4613be…), 18 leagues. The questions:
  - 10,000 relation questions;
  - 1,000 group questions;
  - 180 reading questions (360 reading items);
  - 3,014 binding questions (1,507 propositions, two outcomes each);
  - a 200-query exam.
- **Run conditions:** two Ollama servers, one pinned to each RTX A6000 by the run script (the pinning is not recorded in the run directories), the per-model relation floors from dev, and a 1.5 h budget.
  Every model finished, the slowest (Clef 27B) in 59 min.
- **Deviation 1:** the model builds also set `--local-timeout 600.0` and `--hello-timeout 900`. The pre-registered command
  does not list them; the v1 runs used the same values. No model cell had an item error, retry or over-length abstention (child stats), and no decision row in any cell has an error.
- **Confidence intervals:** league bootstrap (`tools/eval/league_ci.py`), 18 leagues, B = 2000, seed 0, 95% percentile
  intervals. The paired gaps are against the reference model, D3 27B.
- **Deviation 2:** a reading question is assigned to the league of its proposition (`members[0]`). That rule is not in the
  pre-registration. Commit 01516f4 added it after the test runs, because the earlier tool refused this fixture: reading
  distractors can come from other leagues (343 of 720 option slots). In all 180 reading questions, `members[0]` is the
  question's own proposition.

## Results (test)

Headline = the mean of the four per-op MCCs, on raw decisions. Q_N is the repaired-base score. Strict Q_N is 0 for all
eight models, because every model build's base was repaired. For rules, gold_view, oracle and empty, strict Q_N equals Q_N.

| Run | Headline MCC | Gap to D3 27B | Relations | Groups | Bindings | Readings | Q_N | Build |
|---|---|---|---|---|---|---|---|---|
| **gold_view** (perfect answers; ceiling) | 1.000 | −0.627 | 1.000 | 1.000 | 1.000 | 1.000 | 100.00 | 1 s |
| **D3 27B** (reference) | **0.373** [0.349, 0.392] | — | 0.704 | 0.199 | 0.000 | 0.589 | 19.3 | 47 min |
| Clef 27B | 0.368 [0.325, 0.407] | 0.005 [−0.030, 0.045] | 0.462 | 0.172 | 0.310 | 0.528 | 14.3 | 59 min |
| Pplx Decider 27B | 0.325 [0.304, 0.341] | 0.048 [0.035, 0.061] | 0.538 | 0.126 | 0.000 | 0.634 | 13.1 | 49 min |
| D3 9B | 0.305 [0.276, 0.327] | 0.068 [0.054, 0.083] | 0.548 | 0.118 | −0.009 | 0.563 | 16.4 | 16 min |
| Clef-Flash 9B | 0.272 [0.245, 0.296] | 0.101 [0.081, 0.121] | 0.419 | 0.194 | 0.084 | 0.390 | 12.4 | 21 min |
| Nimble 9B | 0.240 [0.220, 0.256] | 0.133 [0.117, 0.146] | 0.411 | 0.091 | 0.000 | 0.456 | 11.0 | 22 min |
| Kev 9B | 0.190 [0.168, 0.211] | 0.183 [0.164, 0.201] | 0.399 | 0.032 | −0.018 | 0.347 | 15.4 | 19 min |
| Laya 0.4B | 0.112 [0.093, 0.133] | 0.261 [0.227, 0.287] | 0.057 | 0.041 | 0.020 | 0.330 | 12.4 | 4 min |
| Rules | 0.096 [0.077, 0.116] | 0.277 [0.246, 0.302] | 0.278 | 0.000 | 0.000 | 0.106 | 32.3 | 1 s |
| Oracle | — | — | — | — | — | — | 100.00 | — |
| Empty | — | — | — | — | — | — | 17.0 | — |

The `-ci.json` file has the intervals for every component, `mcc_mean_3`, held-out relations and Q_N, with all paired gaps.

### Secondary scores (relations)

| Run | Macro-F1 | Brier | ECE | MCC, seen wording | MCC, held-out wording | Seen − held-out |
|---|---|---|---|---|---|---|
| D3 27B | 0.764 | 0.313 | 0.023 | 0.720 | 0.672 | 0.048 |
| Clef 27B | 0.523 | 0.515 | 0.094 | 0.483 | 0.420 | 0.062 |
| Pplx Decider 27B | 0.582 | 0.446 | 0.084 | 0.563 | 0.491 | 0.071 |
| D3 9B | 0.588 | 0.431 | 0.012 | 0.586 | 0.479 | 0.107 |
| Clef-Flash 9B | 0.494 | 0.599 | 0.142 | 0.448 | 0.362 | 0.086 |
| Nimble 9B | 0.496 | 0.562 | 0.109 | 0.440 | 0.353 | 0.087 |
| Kev 9B | 0.456 | 0.538 | 0.040 | 0.427 | 0.349 | 0.078 |
| Laya 0.4B | 0.157 | 0.883 | 0.233 | 0.061 | 0.049 | 0.012 |
| Rules | 0.254 | — | — | 0.299 | 0.238 | 0.061 |

Brier and ECE come from the child's per-option probabilities; the rules child gives none. No interval was computed for
the seen − held-out difference.

## Findings
1. **The ceiling is real and far above the best model.**
   - Perfect curated answers now reach Q_N 100 and MCC 1.0. In v1 they reached only Q_N 74.8.
   - The best System One model, D3 27B, reaches a headline of 0.373: a gap of 0.627 MCC [0.608, 0.651] to the ceiling.
2. **D3 27B and Clef 27B are not statistically distinguishable at the top.** Their gap is 0.005, with an interval from
   −0.030 to 0.045. The other seven models are each below D3 27B, with a gap interval that excludes 0.
3. **Each model has a different profile.**
   - **Relations:** D3 27B leads at 0.704. Its relation gap to every other model excludes 0, and it has the lowest
     Brier score (0.313).
   - **Groups:** Clef-Flash 9B is about level with D3 27B (0.194 vs 0.199; gap interval [−0.031, 0.043]).
   - **Bindings:** Clef 27B has the highest score (0.310 [0.154, 0.453]). Clef-Flash 9B (0.084 [0.005, 0.178]) is the only
     other model whose interval excludes 0.
   - **Readings:** Pplx Decider has the highest point score (0.634). Its lead over D3 27B, 0.045, has an interval of
     [0.000, 0.091].
4. **Bindings: three of the eight models never flag an ambiguous proposition.**
   - D3 27B, Pplx Decider and Nimble answer "bound" for every one of the 3,014 binding questions (MCC 0 by convention).
   - Clef 27B catches 14 of the 92 ambiguous outcomes.
   - Laya catches 84 of them, but flags 2,643 binding questions, covering 1,410 of the 1,507 propositions.
5. **Readings: models rarely find the second reading.**
   - On the 92 ambiguous reading questions (one per unresolved outcome), D3 27B repeats its literal answer for the second
     reading on 71, and gets the second reading right once.
   - Among the eight models, Laya has the most second-reading hits (49 of 92); of the other seven, Clef 27B has the most
     (25 of 92). The gold_view ceiling has 92 of 92.
   - Most of the reading score therefore comes from the literal reading and from decoys, where repeating is correct.
     246 of D3 27B's 247 correct reading items are literal-reading or decoy items, and it gets all 88 decoy rows right.
6. **The end-to-end exam still does not rank models.**
   - Every model build dropped at least one unit outside the relation floor, so its base was repaired and strict Q_N is 0
     for all eight. Repaired Q_N is 11–19.
   - The rules baseline beats every model on Q_N (32.3 [28.8, 36.1]; no model interval reaches above 25.8). The empty build (17.0) has a higher point
     Q_N than every model except D3 27B (19.3); the intervals overlap.
   - Now that perfect answers reach 100, the gap is the models' own answers, not missing curated questions. It includes
     answers that the relation floor removes (931 to 9,365 floored relations for the seven models other than D3 27B) and
     answers that the base repair drops (0 to 28 relations and 59 to 353 group rows).
   - The decision MCC is the meaningful score.
7. **Held-out wording lowers relation MCC.**
   - Held-out relation MCC is 0.01 to 0.11 below seen-wording MCC. Five of eight models drop by 0.07 or more: D3 9B
     0.107, Nimble 0.087, Clef-Flash 0.086, Kev 0.078 and Pplx 0.071.
   - D3 27B drops least among the larger models (0.048).
8. **Speed:** Laya is fastest (4 min); the 9B models take 16–22 min and the 27B models 47–59 min. All are within the 1.5 h
   budget.

## Compared with v1
The v1 and v2 headlines are not comparable: v2 adds a fourth component and uses compound group classes on new fixtures.

On the three-part mean (`mcc_mean_3`), D3 27B scores 0.301 (v1: 0.248) and Clef 27B 0.315 (v1: 0.242). In both versions
the two are within noise of each other, but their order flips on this mean: D3 27B is ahead in v1 and Clef 27B in v2.

## Limits
These are the limits disclosed in the pre-registration, with corrections:
- **Gold-informed candidates:** blocking is not tested.
- **Synthetic single domain,** with public test gold.
- **Wording cue:** a phrase regex identifies ambiguous propositions 73–85% of the time. The figure comes from the stream-5
  verification's classifier on scratch builds of these splits (script not committed); it was not re-derived here.
- **Chain lengths:**
  - Reading chains have 2 members.
  - The generator's rule-6 chains have 2 members.
  - The curated ordered-chain group labels, which include chain walks, have 2 to 7 members (32 of 77 have 2). So the
    pre-registration's "chains are 2 members" holds only for the generator's chains.
- **Decoy shortfall:** on test, 2 of 46 ambiguous propositions have no matching decoy. That gives 92 ambiguous rows
  (46 × 2) against 88 decoy rows (44 × 2).
- **Intervals:**
  - With 18 clusters, the bootstrap intervals somewhat under-cover; no coverage simulation was run.
  - The reference was chosen on dev, and the gaps are not corrected for multiple comparisons.
- **Single runs:** one run per model. Runs are deterministic (dev repeats of laya and d3:9b gave byte-identical decisions, relations, groups and decision scores; the
  verifier re-checked the decisions), so the intervals cover
  item sampling only.
- **Repairs:** strict Q_N is 0 for every model build.

## Addendum (2026-10-29): headline changed after the validity audit

This addendum makes a **post-hoc change**: the owner changed the headline after these results were known, so it departs
from the pre-registration. The reason is the validity audit (`mentat-hard-v2-validity-audit-20261029.md`):
- Real group constraints and second readings depend on generator rules that the question text never states. Two blind
  judges could not reach most of their gold answers from the text (real groups 1/15 and 5/15; second readings 0/30 and
  0/30).
- Relations and bindings are answerable from the text.
- Relation MCC is stable under prompt rewording (within 0.01); bindings and groups are not.

The new scoring:
- **Headline:** relation MCC.
- **Secondary:** binding MCC, with its interval.
- **Diagnostics only:** group and reading MCC (known validity problems) and Q_N / strict Q_N. None of them is part of any
  score.
- **No longer scores:** the four-part and three-part means above.

Relation and binding MCC on the v2 test split, with 95% league-bootstrap intervals (`mentat-hard-v2-20261029-ci.json`,
same draws). The gaps are D3 27B minus the run.

| Run | Relation MCC (headline) | Gap to D3 27B | Binding MCC (secondary) | Gap to D3 27B |
|---|---|---|---|---|
| gold_view (perfect answers) | 1.000 | −0.296 [−0.318, −0.272] | 1.000 | −1.000 |
| **D3 27B** (reference) | **0.704** [0.682, 0.728] | — | 0.000 [0.000, 0.000] | — |
| D3 9B | 0.548 [0.530, 0.567] | 0.155 [0.144, 0.167] | −0.009 [−0.014, 0.000] | 0.009 [0.000, 0.014] |
| Pplx Decider 27B | 0.538 [0.518, 0.559] | 0.165 [0.154, 0.178] | 0.000 [0.000, 0.000] | 0.000 |
| Clef 27B | 0.462 [0.440, 0.486] | 0.242 [0.226, 0.259] | 0.310 [0.154, 0.453] | −0.310 [−0.453, −0.154] |
| Clef-Flash 9B | 0.419 [0.397, 0.442] | 0.285 [0.269, 0.302] | 0.084 [0.005, 0.178] | −0.084 [−0.178, −0.005] |
| Nimble 9B | 0.411 [0.391, 0.430] | 0.293 [0.280, 0.307] | 0.000 [0.000, 0.000] | 0.000 |
| Kev 9B | 0.399 [0.380, 0.420] | 0.304 [0.292, 0.318] | −0.018 [−0.040, 0.019] | 0.018 [−0.019, 0.040] |
| Rules | 0.278 [0.263, 0.292] | 0.426 [0.410, 0.446] | 0.000 [0.000, 0.000] | 0.000 |
| Laya 0.4B | 0.057 [0.048, 0.066] | 0.647 [0.624, 0.672] | 0.020 [−0.026, 0.057] | −0.020 [−0.057, 0.026] |

What changes under the new headline:
- **D3 27B leads alone.** Every other model is below it, with a gap interval that excludes 0. Clef 27B, tied with D3 27B
  on the four-part mean, is fourth on relations.
- **The gap to the ceiling is 0.296** on relations.
- **Clef 27B leads on the secondary score:** it is far ahead on bindings (0.310). Clef-Flash 9B is the only other model
  whose binding interval excludes 0.
- **Laya scores below the rules baseline on relations** (0.057 vs 0.278).

Binding MCC is 0 by convention for a model that never flags an ambiguous proposition, so its [0, 0] intervals are
degenerate. Only 92 test items are ambiguous.

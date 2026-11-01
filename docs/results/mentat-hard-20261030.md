# development round 3 test results, 2026-10-30

> Results bundle (website entry point, leaderboard JSON and CSV, per-item predictions): [results/README.md](../../results/README.md).

This document reports the pre-registered round 3 test matrix (`prereg.md`, frozen before any test run).
Each of the 15 models was run once on `mentat_hard_test` (test set revision 3.1), with the CPU baselines and the hf pipeline
baseline. The numbers are in `mentat-hard-20261030.json` (built by `mentat-hard-results/build_results.py`) and the
intervals are in `mentat-hard-20261030-ci.json` (league bootstrap, `tools/eval/league_ci.py`). The run directories are in
`runs/20261030-test/`, `runs/20261030-test-baselines/` and `runs/20261030-test-hf/` (not committed).

Scores follow the pre-registration. Anything not pre-registered is labelled post-hoc. No score was changed after the test
runs. The overlap statements below are descriptive, not tests.

## Setup
- **Code:** the test cells record commit 0bff47f (a commit of the private development repository, not published here), a clean tree. The prereg names 9ad5f2c. 0bff47f adds only the two prereg files (`docs/results/prereg.{md,json}`) on top of it, with no code change (`git diff --stat 9ad5f2c 0bff47f`).
- **Child:** `systemone-choice` version 7, `prompt_sha256` d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44.
  One question per request, rule cards in the relation, group and reading states.
- **Fixture:** `mentat_hard_test` revision 3.1, content hash 7b408459…. 6,000 relation questions (1,500 NONE), 1,184 group questions
  (184 of them reading questions, two readings each), 3,348 binding questions. 10,532 decisions per model cell, heuristic, gold_view and hf, all scored. Lexical and embedding have 9,348 decisions (6,000 relation and 3,348 binding, no group decisions, so their 1,000 group questions are unanswered).
- **Run conditions:** per-model relation floors from dev, `--top-k 24`, `--batch-size 128`, build deadline 5,400 s. Every cell
  has the same build flags apart from the relation floor. They also set `--local-timeout 600.0` and `--hello-timeout 900.0`,
  which the pre-registered command does not list (the round 2 runs did the same). Every cell completed with no item errors, no
  500 retries and no refused instructions (child stats; child stats exist only for the 15 model cells).
- **Headline:** the mean of relation MCC and group MCC on raw (unfloored) decisions, decided on dev. Binding MCC is secondary.
  Reading MCC, held-out relation MCC and Q_N are diagnostics.
- **Confidence intervals:** league bootstrap, 18 leagues, B = 2000, seed 0, 95% percentile intervals. The paired gaps are
  reference minus run, with the reference D3 27B (chosen on dev). The CI file reproduces byte for byte from
  `league_ci.py` over the 20 runs listed under Provenance (the 15 model cells, the four CPU baselines and the hf cell).
- **One run per model.** The dev repeat of d3:9b gave byte-identical decisions (prereg), but there is no repeat on test, so
  the intervals cover item and league sampling only.

## Results (test)

Headline = relation and group MCC (mean). Gap = D3 27B minus the run, paired by bootstrap draw. Bindings are the secondary
score. Dev is the pre-registered dev headline, shown as stored in the prereg JSON (4 decimals).

| Run | Headline: relation + group MCC [95% CI] | Relation MCC [95% CI] | Group MCC [95% CI] | Binding MCC, secondary [95% CI] | Gap to D3 27B [95% CI] | Dev headline (prereg, 4 dp) |
|---|---|---|---|---|---|---|
| **D3 27B** (reference) | 0.519 [0.497, 0.541] | 0.788 [0.766, 0.814] | 0.249 [0.224, 0.273] | 0.000 [0.000, 0.000] | — | 0.5036 |
| D3 9B | 0.275 [0.251, 0.300] | 0.462 [0.438, 0.487] | 0.088 [0.054, 0.121] | 0.000 [0.000, 0.000] | 0.244 [0.221, 0.266] | 0.2938 |
| Pplx Decider 27B | 0.253 [0.239, 0.269] | 0.512 [0.490, 0.536] | −0.007 [−0.029, 0.018] | 0.000 [0.000, 0.000] | 0.266 [0.244, 0.288] | 0.2638 |
| Kev 9B | 0.236 [0.215, 0.258] | 0.325 [0.302, 0.348] | 0.147 [0.121, 0.175] | −0.012 [−0.032, 0.014] | 0.283 [0.265, 0.302] | 0.2533 |
| D3 4B | 0.233 [0.216, 0.251] | 0.393 [0.370, 0.418] | 0.072 [0.049, 0.096] | 0.000 [0.000, 0.000] | 0.286 [0.267, 0.306] | 0.2375 |
| Clef 27B | 0.207 [0.186, 0.231] | 0.393 [0.366, 0.420] | 0.021 [−0.006, 0.049] | 0.213 [0.108, 0.318] | 0.312 [0.288, 0.336] | 0.2044 |
| Nimble-v3 9B | 0.201 [0.189, 0.215] | 0.277 [0.251, 0.304] | 0.126 [0.106, 0.147] | −0.025 [−0.029, −0.021] | 0.317 [0.296, 0.338] | 0.1914 |
| Nimble 9B | 0.178 [0.167, 0.190] | 0.272 [0.258, 0.286] | 0.084 [0.057, 0.109] | 0.145 [0.000, 0.258] | 0.340 [0.318, 0.364] | 0.1813 |
| Clef-Flash 9B | 0.172 [0.163, 0.181] | 0.250 [0.235, 0.266] | 0.093 [0.074, 0.113] | 0.019 [−0.020, 0.092] | 0.347 [0.328, 0.368] | 0.1735 |
| TEV1 4B | 0.144 [0.126, 0.163] | 0.300 [0.272, 0.329] | −0.012 [−0.036, 0.013] | 0.009 [−0.013, 0.031] | 0.374 [0.350, 0.398] | 0.1512 |
| D1 3B | 0.071 [0.058, 0.087] | 0.115 [0.093, 0.139] | 0.027 [0.003, 0.053] | 0.072 [−0.017, 0.171] | 0.447 [0.428, 0.469] | 0.0785 |
| D3 2B | 0.055 [0.042, 0.069] | 0.171 [0.154, 0.188] | −0.060 [−0.082, −0.037] | −0.022 [−0.054, 0.015] | 0.463 [0.447, 0.481] | 0.0820 |
| TEV1 0.8B ‡ | −0.000 [−0.005, 0.005] | −0.000 [−0.009, 0.010] | 0.000 [0.000, 0.000] | −0.027 [−0.075, 0.015] | 0.519 [0.499, 0.540] | 0.0063 |
| D1-Omni 600M | −0.003 [−0.010, 0.004] | 0.015 [0.003, 0.027] | −0.021 [−0.033, −0.007] | 0.045 [0.038, 0.052] | 0.522 [0.500, 0.544] | −0.0006 |
| Laya 0.4B † | −0.020 [−0.032, −0.007] | −0.016 [−0.029, 0.002] | −0.024 [−0.041, −0.006] | −0.036 [−0.086, 0.011] | 0.539 [0.511, 0.564] | −0.0052 |
| *Rules (heuristic child)* | *0.140 [0.121, 0.153]* | *0.251 [0.235, 0.268]* | *0.030 [0.000, 0.053]* | *0.000 [0.000, 0.000]* | *0.378 [0.362, 0.404]* | *0.1252* |
| *Embedding* | *0.007 [−0.002, 0.014]* | *0.014 [−0.004, 0.029]* | *0.000 [0.000, 0.000]* | *0.000 [0.000, 0.000]* | *0.511 [0.489, 0.536]* | — |
| *Lexical* | *0.000 [0.000, 0.000]* | *0.000 [0.000, 0.000]* | *0.000 [0.000, 0.000]* | *0.000 [0.000, 0.000]* | *0.519 [0.497, 0.541]* | — |
| *gold_view* (perfect curated answers; ceiling) | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *−0.481 [−0.503, −0.459]* | — |
| *hf pipeline (Qwen2.5-1.5B-Instruct)* | *0.008 [−0.002, 0.018]* | *0.030 [0.018, 0.043]* | *−0.015 [−0.027, −0.002]* | *−0.018 [−0.021, −0.014]* | *0.511 [0.485, 0.537]* | — |

- The rules, lexical, embedding and gold_view rows are the CPU baselines from `runs/20261030-test-baselines`, run in the
  same league bootstrap. Their paired gaps are against D3 27B. The rules child is the pre-registered "rules" baseline.
- The hf row is the pre-registered hf pipeline baseline (release gate 10). Its cell finished after the first league_ci run, so
  it was added to the bootstrap run; the other runs' values are unchanged. Its build flags differ from the pre-registered
  command: no `--relation-floor` and no `--batch-size`, with `--local-timeout 1800.0` and `--hello-timeout 3600.0`.
- Empty and oracle have Q_N only, because they have no decisions. They are not in the league_ci run, so they have no interval.
- ‡ **TEV1 0.8B.** Its base was not repaired, so its strict Q_N is its repaired Q_N (17.03). The pre-registration expected strict Q_N of 0 for every build.
- † **Laya 0.4B.** Scored as pre-registered: an abstention is a NONE with no conf. On test, Laya's child abstained for length
  on 4,981 of 6,000 relation questions and 1,032 of 1,184 group questions. Most of its correct NONE answers are abstentions:
  1,198 of its 1,202 correct NONE decisions are abstentions, and 4 are not. See Limits, item 1.

## Which differences are resolved

- **D3 27B is above every other model and every non-ceiling baseline.** All 14 other models, and the hf baseline, have gap
  intervals that exclude 0. The smallest gap is to D3 9B, 0.244 [0.221, 0.266]. The rules, embedding, lexical and hf
  baselines are also below it by intervals that exclude 0. Only gold_view, the ceiling, is above it.
- **Relation MCC alone also puts D3 27B first** (0.788, against 0.512 for the next model, Pplx Decider). The group lead is
  smaller: D3 27B 0.249 against Kev 9B 0.147.
- **Nine models have headline intervals entirely above the rules interval** (upper bound 0.153): D3 27B, D3 9B, Pplx, Kev,
  D3 4B, Clef 27B, Nimble-v3, Nimble and Clef-Flash. TEV1 4B (0.126 to 0.163) overlaps the rules interval. Those intervals are
  unpaired. The paired test is only computed against D3 27B.
- **The middle of the table is not ordered by these intervals.** The intervals of D3 9B, Pplx, Kev and D3 4B all overlap one
  another, and Clef 27B overlaps D3 4B, Kev, Nimble-v3 and Nimble. The 27 overlapping pairs are listed in the JSON
  (`headline_interval_overlaps`). Overlap is not evidence of a tie. The paired comparison among non-reference models was
  not computed.
- **Some runs are indistinguishable from zero.** Lexical (0.000), embedding (0.007, interval [−0.002, 0.014]), hf (0.008,
  interval [−0.002, 0.018]), TEV1 0.8B and D1-Omni 600M all have headline intervals that include 0. Laya's interval is
  entirely below 0 (upper bound −0.007).

## Parts and secondary

- **Relations:** D3 27B 0.788 [0.766, 0.814] leads. Pplx is next (0.512), then D3 9B (0.462). The relation gap interval to
  every other model excludes 0.
- **Groups:** D3 27B 0.249 [0.224, 0.273] leads. Kev 9B 0.147 [0.121, 0.175] is second.
- **Bindings (secondary):**
  - Clef 27B has the highest point (0.213 [0.108, 0.318]).
  - Four models never flag an ambiguous proposition on test: D3 27B, D3 9B, D3 4B and Pplx. Their MCC is 0 by convention, so
    their intervals are degenerate. This matches the pre-registration for D3 27B, Pplx and D3 4B. D3 9B scored 0.293 on dev
    and 0.000 on test (see Limits).
  - Nimble 9B's 0.145 [0.000, 0.258] comes from two flags, both correct.
  - D1-Omni 600M's 0.045 [0.038, 0.052] has an interval above 0, but it comes from 3,127 flags with precision about 0.03.

## Diagnostics (not scores)

**Reading MCC** (diagnostic, 184 reading questions, 368 items)

| Run | Reading MCC [95% CI] |
|---|---|
| D3 27B | 0.804 [0.764, 0.847] |
| Pplx Decider 27B | 0.630 [0.569, 0.683] |
| Clef 27B | 0.493 [0.434, 0.549] |
| D3 9B | 0.453 [0.398, 0.511] |
| Nimble 9B | 0.402 [0.340, 0.454] |
| Nimble-v3 9B | 0.377 [0.301, 0.451] |
| Clef-Flash 9B | 0.338 [0.279, 0.395] |
| D3 4B | 0.300 [0.252, 0.352] |
| D1 3B | 0.295 [0.222, 0.370] |
| Kev 9B | 0.289 [0.211, 0.357] |
| D3 2B | 0.273 [0.223, 0.322] |
| TEV1 4B | 0.271 [0.208, 0.327] |
| Rules | −0.016 [−0.104, 0.082] |
| Embedding | 0.000 |
| Lexical | 0.000 |
| TEV1 0.8B | 0.000 |
| D1-Omni 600M | 0.000 |
| Laya 0.4B | 0.000 |
| hf pipeline | 0.000 [0.000, 0.000] |

**Held-out vs seen relation MCC.** Held-out MCC has a 95% interval from the league bootstrap. Seen MCC is a point only:
the bootstrap does not compute it.

| Run | Seen (point) | Held-out [95% CI] | Seen − held-out (point) |
|---|---|---|---|
| D3 27B (reference) | 0.787 | 0.787 [0.760, 0.815] | 0.000 |
| D3 9B | 0.504 | 0.399 [0.373, 0.427] | 0.105 |
| Pplx Decider 27B | 0.514 | 0.506 [0.475, 0.540] | 0.009 |
| Kev 9B | 0.374 | 0.253 [0.222, 0.285] | 0.121 |
| D3 4B | 0.434 | 0.331 [0.298, 0.365] | 0.103 |
| Clef 27B | 0.427 | 0.337 [0.298, 0.380] | 0.091 |
| Nimble-v3 9B | 0.298 | 0.244 [0.210, 0.281] | 0.053 |
| Nimble 9B | 0.288 | 0.246 [0.219, 0.271] | 0.042 |
| Clef-Flash 9B | 0.255 | 0.245 [0.216, 0.271] | 0.010 |
| TEV1 4B | 0.340 | 0.240 [0.211, 0.270] | 0.100 |
| D1 3B | 0.112 | 0.124 [0.105, 0.147] | −0.012 |
| D3 2B | 0.200 | 0.135 [0.111, 0.157] | 0.065 |
| TEV1 0.8B | 0.001 | −0.003 [−0.022, 0.018] | 0.004 |
| D1-Omni 600M | 0.025 | 0.006 [−0.016, 0.024] | 0.020 |
| Laya 0.4B | −0.012 | −0.021 [−0.048, 0.020] | 0.010 |
| hf pipeline (Qwen2.5-1.5B-Instruct) | 0.039 | 0.027 [0.007, 0.044] | 0.012 |

- The held-out split is 2,332 of the 6,000 relations (38.9%), as pre-registered.
- Seven of the 15 models lose 0.05 or more of relation MCC on held-out wording: D3 9B, Kev 9B, D3 4B, TEV1 4B, Clef 27B,
  D3 2B and Nimble-v3. D3 27B loses none. Its held-out and seen scores are the same to three decimals.
- Held-out wording is not a uniform penalty: D1 3B scores slightly higher on the held-out split. The seen−held-out
  differences have no interval, so they are descriptive.

**Q_N (repaired base) and strict Q_N**, both diagnostics

| Run | Q_N (repaired) [95% CI] | Strict Q_N |
|---|---|---|
| D3 2B | 26.34 [21.07, 31.94] | 0.00 |
| D3 27B | 24.98 [19.92, 31.59] | 0.00 |
| Kev 9B | 21.95 [16.67, 27.11] | 0.00 |
| TEV1 4B | 21.52 [15.18, 28.27] | 0.00 |
| D3 9B | 18.82 [14.80, 22.97] | 0.00 |
| Laya 0.4B | 18.51 [14.97, 21.83] | 0.00 |
| D3 4B | 17.55 [12.90, 22.15] | 0.00 |
| Nimble-v3 9B | 17.19 [13.09, 22.25] | 0.00 |
| TEV1 0.8B | 17.03 [14.58, 19.67] | **17.03 (base not repaired)** |
| Clef-Flash 9B | 16.40 [13.20, 19.69] | 0.00 |
| D1 3B | 15.60 [12.13, 19.19] | 0.00 |
| Clef 27B | 15.52 [11.99, 19.20] | 0.00 |
| Pplx Decider 27B | 14.57 [9.84, 20.01] | 0.00 |
| D1-Omni 600M | 14.21 [9.05, 19.50] | 0.00 |
| Nimble 9B | 12.88 [9.98, 15.75] | 0.00 |
| *Empty (no interval)* | *16.88* | *16.88* |
| *Rules* | *32.22 [28.55, 36.13]* | *32.22 (not repaired)* |
| *Lexical* | *32.22 [28.57, 36.11]* | *32.22* |
| *Embedding* | *31.38 [27.96, 35.18]* | *31.38* |
| *gold_view* | *100.00* | *100.00* |
| *Oracle (no interval)* | *100.00* | *100.00* |
| *hf pipeline* | *9.06 [5.69, 12.74]* | *0.00* |

- **Strict Q_N is 0 for 14 of the 15 models.** The exception is TEV1 0.8B. Its base was not repaired, unlike the
  pre-registration's expectation, so its strict Q_N is its repaired Q_N, 17.03, not 0.
- Empty and oracle have Q_N only (no decisions) and no interval, because they are not in the league_ci run.
- **The Q_N ranking is not the decision ranking.** The CPU baselines (lexical 32.2, rules 32.2, embedding 31.4) have a higher
  Q_N than every model. Among the models, D3 2B (26.3) has a higher point than D3 27B (25.0), and the intervals overlap.
  Q_N is a diagnostic, as pre-registered, and the decision MCC is the meaningful score.
- The rules baseline's Q_N gap to D3 27B is −7.24 [−12.98, −1.17] (reference minus run, in the CI file).

## Relation accuracy by gold depth

Pre-registered as reported. Per run, accuracy on the relation rows grouped by the gold depth of their suite label
(`gold/suite_labels.jsonl`). Raw decisions, scored as answered (unfloored), with missing answers counted as wrong. It is not
used for any score.

| Run | 0 | 1 | 2 | 3 | 4+ | NONE | All relations |
|---|---|---|---|---|---|---|---|
| *n* | *1,770* | *876* | *1,194* | *654* | *6* | *1,500* | *6,000* |
| D3 27B | 0.915 | 0.920 | 0.716 | 0.648 | 0.500 | 0.837 | 0.827 |
| D3 9B | 0.679 | 0.377 | 0.349 | 0.310 | 0.000 | 0.787 | 0.555 |
| Pplx Decider 27B | 0.810 | 0.662 | 0.482 | 0.463 | 0.333 | 0.407 | 0.584 |
| Kev 9B | 0.465 | 0.316 | 0.113 | 0.122 | 0.167 | 0.898 | 0.444 |
| D3 4B | 0.563 | 0.379 | 0.327 | 0.301 | 0.333 | 0.719 | 0.500 |
| Clef 27B | 0.754 | 0.489 | 0.437 | 0.434 | 0.167 | 0.247 | 0.490 |
| Nimble-v3 9B | 0.451 | 0.428 | 0.245 | 0.234 | 0.000 | 0.640 | 0.430 |
| Nimble 9B | 0.520 | 0.545 | 0.502 | 0.460 | 0.333 | 0.094 | 0.407 |
| Clef-Flash 9B | 0.503 | 0.499 | 0.486 | 0.477 | 0.333 | 0.026 | 0.377 |
| TEV1 4B | 0.488 | 0.295 | 0.122 | 0.098 | 0.000 | 0.852 | 0.435 |
| D1 3B | 0.245 | 0.330 | 0.208 | 0.213 | 0.000 | 0.480 | 0.305 |
| D3 2B | 0.163 | 0.236 | 0.137 | 0.115 | 0.000 | 0.870 | 0.340 |
| TEV1 0.8B | 0.014 | 0.006 | 0.012 | 0.015 | 0.000 | 0.950 | 0.246 |
| D1-Omni 600M | 0.151 | 0.377 | 0.339 | 0.320 | 0.167 | 0.061 | 0.217 |
| Laya 0.4B† | 0.051 | 0.059 | 0.039 | 0.021 | 0.167 | 0.801 | 0.234 |
| *Rules* | *0.288* | *0.000* | *0.000* | *0.000* | *0.000* | *1.000* | *0.335* |
| *Embedding* | *0.006* | *0.000* | *0.000* | *0.000* | *0.000* | *0.995* | *0.251* |
| *Lexical* | *0.000* | *0.000* | *0.000* | *0.000* | *0.000* | *1.000* | *0.250* |
| *hf pipeline* | *0.021* | *0.013* | *0.001* | *0.000* | *0.000* | *0.987* | *0.255* |
| *gold_view* | *1.000* | *1.000* | *1.000* | *1.000* | *1.000* | *1.000* | *1.000* |

Notes:
- **The buckets differ in class mix, so they are not comparable to MCC.** Depth 0 holds 800 EQUIV, 500 EXCLUDES, 242 IMPLIES
  and 228 IMPLIED_BY. Depth 1 has no EQUIV (382 IMPLIES, 350 IMPLIED_BY, 144 EXCLUDES). Depths 2 and 3 are one-third each of
  IMPLIES, IMPLIED_BY and EXCLUDES. The NONE bucket is the only one with NONE rows. A model that names few relations can score
  high on NONE, and an accuracy is not a chance-corrected score. Use the MCC for comparisons.
- **The 4+ bucket has six rows** (two each of IMPLIES, IMPLIED_BY and EXCLUDES, all depth 4). One row moves its accuracy by
  17 points, so it is not informative on its own.
- † Laya's abstentions for length are NONE decisions, scored as pre-registered. 1,198 of its 1,202 correct NONE
  decisions are abstentions (4 are not), so its NONE column is mostly abstention, not judgement (see Limits, item 1).
- The hf pipeline answers NONE for nearly every relation (0.987 on NONE rows), so its named-relation accuracy is near zero.
- Depth accuracy is a point with no interval.
- For D3 27B accuracy is high on depths 0 and 1 (0.915 and 0.920) and falls to 0.648 at depth 3. Most models fall with depth. Clef-Flash
  is nearly flat.

## Repair drops (repaired base only)

Each build repairs its base theory. The build reports the groups and relations it dropped (`build_summary.json`: `groups_dropped`, `groups_committed`, `groups_dropped_pct`, `relations_dropped`). The percentage is groups dropped over groups committed. These drops change the repaired base that Q_N scores. They do not change any decision, so they are not in the headline or in any other decision score.

| Run | Groups dropped / committed (% of committed) | Relations dropped (of 6,000 asked) |
|---|---|---|
| D3 27B (reference) | 92 / 1,776 (5.18%) | 52 |
| D3 9B | 149 / 1,397 (10.67%) | 30 |
| Pplx Decider 27B | 361 / 1,752 (20.61%) | 79 |
| Kev 9B | 51 / 824 (6.19%) | 9 |
| D3 4B | 106 / 1,420 (7.46%) | 30 |
| Clef 27B | 261 / 1,753 (14.89%) | 74 |
| Nimble-v3 9B | 264 / 1,672 (15.79%) | 2 |
| Nimble 9B | 320 / 1,632 (19.61%) | 9 |
| Clef-Flash 9B | 204 / 1,903 (10.72%) | 8 |
| TEV1 4B | 49 / 1,251 (3.92%) | 2 |
| D1 3B | 188 / 1,573 (11.95%) | 0 |
| D3 2B | 6 / 717 (0.84%) | 0 |
| TEV1 0.8B | 0 / 0 (no group answers; base not repaired) | 0 |
| D1-Omni 600M | 373 / 1,982 (18.82%) | 0 |
| Laya 0.4B | 10 / 156 (6.41%) | 0 |
| hf pipeline (Qwen2.5-1.5B-Instruct) | 228 / 1,000 (22.80%) | 0 |

## Speed (build wall time per cell)

Build wall time = `build_summary.json` `wall_seconds`, as in the round 2 table. The cell's `step_seconds.build` is also in the JSON.

| Run | Build wall time |
|---|---|
| D3 27B | 74.6 min (4,478 s) |
| Pplx Decider 27B | 74.4 min (4,465 s) |
| Clef 27B | 78.7 min (4,721 s; `step_seconds.build` 79.6 min) |
| Kev 9B | 34.9 min |
| Nimble 9B | 27.7 min |
| Nimble-v3 9B | 28.3 min |
| Clef-Flash 9B | 26.4 min |
| D3 9B | 21.9 min |
| TEV1 4B | 17.2 min |
| D3 4B | 15.4 min |
| D1 3B | 8.5 min |
| D3 2B | 6.9 min |
| TEV1 0.8B | 6.6 min |
| D1-Omni 600M | 4.7 min |
| Laya 0.4B | 2.4 min |
| hf (Qwen2.5-1.5B) | 1.5 min |

All three 27B cells are at or under the 80-minute target, and Clef 27B is close to the limit (78.7 min, or 79.6 min by the
step timer). Baselines take 0.7 to 17.5 s each (embedding is the slowest, at 17.5 s).

## D3 family size curve (pre-registered comparison)

| Model | Test headline [95% CI] | Test relation MCC | Test group MCC | Dev headline |
|---|---|---|---|---|
| D3 2B | 0.055 [0.042, 0.069] | 0.171 | −0.060 | 0.082 |
| D3 4B | 0.233 [0.216, 0.251] | 0.393 | 0.072 | 0.237 |
| D3 9B | 0.275 [0.251, 0.300] | 0.462 | 0.088 | 0.294 |
| D3 27B | 0.519 [0.497, 0.541] | 0.788 | 0.249 | 0.504 |

The headline rises at every step. From 2B to 4B it adds 0.18, from 4B to 9B 0.04, and from 9B to 27B 0.24, the largest step,
mostly from relations and groups. Dev gives the same ordering.

## Dev vs test headline (pre-registered dev values)

| Run | Dev headline (prereg JSON) | Test headline | Change, test minus dev (unrounded) |
|---|---|---|---|
| D3 27B (reference) | 0.5036 | 0.5185 | 0.0149 |
| D3 9B | 0.2938 | 0.2748 | −0.0190 |
| Pplx Decider 27B | 0.2638 | 0.2527 | −0.0111 |
| Kev 9B | 0.2533 | 0.2359 | −0.0174 |
| D3 4B | 0.2375 | 0.2327 | −0.0048 |
| Clef 27B | 0.2044 | 0.2068 | 0.0024 |
| Nimble-v3 9B | 0.1914 | 0.2014 | 0.0100 |
| Nimble 9B | 0.1813 | 0.1783 | −0.0030 |
| Clef-Flash 9B | 0.1735 | 0.1717 | −0.0018 |
| TEV1 4B | 0.1512 | 0.1440 | −0.0072 |
| Rules (heuristic child) | 0.1252 | 0.1405 | 0.0153 |
| D1 3B | 0.0785 | 0.0710 | −0.0075 |
| D3 2B | 0.0820 | 0.0552 | −0.0268 |
| TEV1 0.8B | 0.0063 | −0.0001 | −0.0064 |
| D1-Omni 600M | −0.0006 | −0.0032 | −0.0026 |
| Laya 0.4B | −0.0052 | −0.0201 | −0.0149 |

The top model stays the same, and the order of the other models is unchanged except for one adjacent pair: D1 3B moves above D3 2B on test (dev had D3 2B first, by 0.0035). Every change is within 0.03.

## Disclosed limits

These are the limits disclosed in the pre-registration, carried over unchanged:
- **Gold-informed candidates.** Candidate selection uses gold, so blocking is not tested. The data is a synthetic single
  domain, and the test gold is public.
- **Card parameters.** Some card bounds are conditioned on gold (M range, q, seed_min, chain order, winner bound a). Each is a
  bound the theory needs, not a value.
- **Surface cues.** The gloss on a decoy proposition marks the decoy stratum perfectly. Cross-league NONE pairs (about 3.7% of
  NONE) are always NONE.
- **Held-out share on test** is 38.9% of relations (2,332 of 6,000), above the ~30% target.
- **Small context.** Laya's context is 512 tokens. See the new finding below.
- **Bindings.** Bindings get no card. Binding MCC is 0.000 for D3 27B, Pplx and D3 4B, as pre-registered.
- **Post-hoc changes, all decided on dev before any test run:** revision 3.1 (harder relations), the relation + group headline and
  child version 7 (the audit fix). Each is an owner decision recorded in its PR.
- **Judges are models.** The answerability audits use two Haiku 5.5 judges, not people.
- **Intervals.** With 18 clusters, the bootstrap intervals somewhat under-cover. Choosing the reference on dev is not
  corrected for in the paired gaps.
- **End-to-end score.** Strict Q_N is 0 for every repaired build (see the Q_N table for the one exception).

New limits found in these results:
1. **Laya's abstentions are scored as NONE answers (pre-registered scoring, kept; not rescored).** An abstention is a NONE
   with no conf, as the child and the pre-registration define it. On test, Laya's child abstained for length on 4,981 of
   6,000 relation questions (83%) and on 1,032 of 1,184 group questions (`child_stats.json`). A relation abstention is written
   as `{"relation": "NONE"}`, and a group abstention as an empty group list. Laya has 1,033 empty group lists: 1,032 with null probs, the same number as the child's group abstentions, and one with probs (an at_most_one answer the child gave).
   `decisions.jsonl` does not mark either, so the scorer counts them as answers. Laya's relation coverage is therefore 1.0.
   At most 20 of its 5,001 NONE decisions are non-abstained answers, so most of its correct NONE answers are abstentions (1,198 of 1,202; 4 are not). Its relation MCC (−0.016) is computed with the abstentions as NONE. On dev the child abstained on
   5,552 requests (prereg).
2. **TEV1 0.8B's base was not repaired, unlike the pre-registration's expectation.** Its strict Q_N is 17.03 (equal to its
   repaired Q_N), not 0. It is the only build whose base was not repaired.
3. **hf was run after the others, with different build flags.** Its cell finished after the first league_ci run, so it was
   added to the bootstrap run. Its build has no relation floor and uses `--local-timeout 1800.0` and `--hello-timeout 3600.0`.
   Only the hf entries of the CI file are new: every other run and gap value is unchanged from the earlier file. It is a
   baseline, not a System One child, and it names almost no relations (see the depth notes).
4. **Empty and oracle have Q_N only.** They have no decisions and no interval, because they are not in the league_ci run.
5. **D3 9B's binding MCC fell from 0.293 on dev to 0.000 on test. This is observed and unexplained.** On test it flags no
   binding question as ambiguous. Binding questions carry no rule card (prereg), so the card change is not an explanation. The
   cause has not been identified.
6. **Uncertainty.** Only gaps to D3 27B are paired, so overlapping intervals among the other models are not ordered by a paired
   test. Held-out relation MCC has an interval. Seen relation MCC and depth accuracy are points without intervals.
7. **Depth accuracy is descriptive.** The 4+ bucket has six rows, and the buckets differ in class mix (see the depth notes).
8. **Some models name few or no relations.** Under their relation floor (the largest float), TEV1 0.8B and D1-Omni 600M name no relations. Their raw decisions, which the headline uses, do name relations: TEV1 0.8B names 324 (EXCLUDES 209, EQUIV 115) and D1-Omni 600M names 5,714. Their relation MCC is near zero. The hf pipeline names almost none (depth table).

## Provenance

- Code commit 0bff47f (the prereg names 9ad5f2c; 0bff47f adds only the two prereg files, with no code change). Test cells: `runs/20261030-test/` (15 models, one run each). Baselines:
  `runs/20261030-test-baselines/`. hf: `runs/20261030-test-hf/hf/` (complete; included in the league_ci run).
- Fixture `mentat_hard_test` revision 3.1, content hash 7b408459538d7420e808d5d81e71916c57aee78f459a6b8c0905e48e43c8e6ab.
- Child `systemone-choice` version 7, prompt sha256 d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44.
- Intervals: `mentat-hard-20261030-ci.json` (league_ci, `relation-group-headline/1`, B = 2000, seed 0, reference
  `d3_27b-q8_0`, runs: the 15 model cells plus `heuristic`, `gold_view`, `lexical`, `embedding` and `hf`, 20 in all).
  Rerunning the command below reproduces it byte for byte (`<runs>` is the `runs/` folder of the checkout that holds the run directories; `<scratch>` is any temporary folder):

  ```
  PYTHONPATH=python python tools/eval/league_ci.py --fixture fixtures/mentat_hard_test --runs <runs>/20261030-test/clef-flash_9b <runs>/20261030-test/clef_27b-q8_0 <runs>/20261030-test/d1-omni_600m-q8_0 <runs>/20261030-test/d1_3b-q8_0 <runs>/20261030-test/d3_27b-q8_0 <runs>/20261030-test/d3_2b-q8_0 <runs>/20261030-test/d3_4b-q8_0 <runs>/20261030-test/d3_9b-q8_0 <runs>/20261030-test/kev_9b-bf16 <runs>/20261030-test/laya_latest <runs>/20261030-test/nimble-v3_9b-q8_0 <runs>/20261030-test/nimble_9b-bf16 <runs>/20261030-test/pplx-decider_27b <runs>/20261030-test/tev1_0.8b-q8_0 <runs>/20261030-test/tev1_4b-q8_0 <runs>/20261030-test-baselines/heuristic <runs>/20261030-test-baselines/gold_view <runs>/20261030-test-baselines/lexical <runs>/20261030-test-baselines/embedding <runs>/20261030-test-hf/hf --reference <runs>/20261030-test/d3_27b-q8_0 --B 2000 --seed 0 --out docs/results/mentat-hard-20261030-ci.json --md <scratch>/results/regen-ci-hf.md
  ```
- Recompute: `python docs/results/mentat-hard-results/build_results.py` from the repo root. It checks every decision score
  against `score_decisions.json`, every interval point against the CI file, the model digest and floor against the
  pre-registration, and the fixture and child hashes. It writes `mentat-hard-20261030.json`.

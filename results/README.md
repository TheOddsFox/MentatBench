# Mentat-Hard results, v0.1.1 (analysis of the v0.1.0 measurements)

This folder holds the results of the development round 3 test matrix (test set revision 3.1, released as v0.1.0 on 2026-10-31): 15 System One
models, their baselines (the rules child, lexical, embedding, empty, oracle, gold_view as the ceiling, and the hf pipeline baseline), and a replication on the sealed private split. It is the entry point for the
website and for anyone who wants the numbers in machine-readable form. The tables below are checked cell by cell against [v0.1.0/leaderboard.json](v0.1.0/leaderboard.json) by `tests/test_results_bundle.py`. That JSON is built by the bundle builder from the committed results documents and the run directories. The figures in the prose were checked by hand against the same JSON. Nothing here is a new measurement. This is the v0.1.1 analysis release: it analyses the v0.1.0 measurements and changes no score.

Start here:

- One-page summary: [SUMMARY.md](SUMMARY.md)
- How the benchmark was built, and why each design changed: [docs/how-the-benchmark-was-built.md](../docs/how-the-benchmark-was-built.md)
- Human-readable test results: [docs/results/mentat-hard-20261030.md](../docs/results/mentat-hard-20261030.md)
- Private-split results (aggregates only): [docs/results/mentat-hard-private-20261030.md](../docs/results/mentat-hard-private-20261030.md)
- Leaderboard, JSON: [v0.1.0/leaderboard.json](v0.1.0/leaderboard.json). Flat table: [v0.1.0/leaderboard.csv](v0.1.0/leaderboard.csv)
- Per-item test predictions: [v0.1.0/predictions/](v0.1.0/predictions/). Per-cell summaries: [v0.1.0/cells/](v0.1.0/cells/)
- Release gates: [release/v0.1.0/record/check_gates.md](../release/v0.1.0/record/check_gates.md). Release manifest: [release/v0.1.0/record/release_manifest.json](../release/v0.1.0/record/release_manifest.json)
- Versions, latest first: [index.json](index.json). History: [round 2](#history) and [round 1](#history)

## Summary

- **D3 27B** (Ollama `d3:27b-q8_0`) is first on the pre-registered headline, the mean of relation MCC and group MCC:
  0.519 [0.497, 0.541] on the public test split and 0.525 [0.504, 0.546] on the private split. It is the reference model, chosen on dev.
- **Next is D3 9B**, at 0.275 [0.251, 0.300]. Its paired gap to D3 27B is 0.244 [0.221, 0.266], an interval that excludes 0.
- **Several middle pairs are not resolved.** Several headline intervals overlap, and overlap is not evidence of a tie. The Holm-adjusted
  pairwise comparisons among all 15 models are in the v0.1.1 analysis section: 89 of the 105 headline pairs are resolved, and the other 16
  are not (all 105 are in [analysis.json](v0.1.1/analysis.json)). See [Which differences are resolved](#which-differences-are-resolved).
- **Relation MCC alone also puts D3 27B first** (0.788, against 0.512 for Pplx Decider 27B). The group lead is smaller:
  0.249 against 0.147 for Kev 9B.
- **Baselines.** The pre-registered rules baseline (a heuristic child) scores 0.140 [0.121, 0.153]. gold_view, which uses perfect
  curated answers, is the ceiling at 1.000. Lexical scores 0.000 and embedding 0.007.
- **The headline ranking replicates on the private split:** Spearman 0.98 over 16 runs, every model's headline within 0.025 of its public value (largest: Clef-Flash 9B, -0.025), and the top three unchanged. The middle order, the group second place and the binding scores move (see [Private-split replication](#private-split-replication)).
- **Caveats that matter.** Laya 0.4B's abstentions are scored as NONE answers, as pre-registered. TEV1 0.8B and D1-Omni 600M are at
  chance. D3 9B's binding score fell from 0.293 on dev to 0.000 on test and the cause is not known. Each model was run once. See
  [Limits and disclosures](#limits-and-disclosures).

## What the results support

Three tiers. Each claim is backed by the tables in this file: the leaderboard, the analysis section, the private-split section and the limits.

**Well supported**

- D3 27B (rank 1 on the headline) leads this benchmark's relation and group decision tasks, and so the headline, their mean. Relation MCC 0.788, against 0.512 for the next model (Pplx Decider 27B); group MCC 0.249, against 0.147 for the next (Kev 9B). It does not lead binding: its binding MCC is 0.000.
- Its headline difference from every other model is resolved after Holm correction (paired league bootstrap). All 14 of its headline pairs are resolved, each in its favour.
- The owner-run private-split check agrees on the leading model: D3 27B is first on the private split too (0.525, against 0.273 for D3 9B). This is a replication run by the owner, not an independent reproduction.

**Partially supported**

- Generalisation to new wording within the same generator (held-out phrasing families). D3 27B's relation MCC is the same on held-out and seen wording (0.787 and 0.787), but seven of the 15 models lose 0.05 or more on held-out wording.

**Not established**

- General decision-making ability. The benchmark measures typed decisions from one generator, not decision-making in general.
- Building reliable, consistent knowledge bases. Strict Q_N is 0 for 14 of the 15 models (tev1_0.8b-q8_0 is the exception, at 17.0), D3 27B included. Local conflicts: 1,505 of D3 27B's 5,172 claim-bearing answers (29.1%; 95% interval 23.5% to 34.3%) belong to a minimal conflict of at most three answers under either predicate (logical UNSAT, or a forced suite outcome), and its answer set contains a detected contradiction. 83 of them belong to a minimal logically unsatisfiable set of at most three answers.

### What this benchmark does not measure

- One synthetic sports-league generator. Every item comes from it.
- Inference depth. Of the 4,500 named relations, 3,840 are depth 0 to 2, 654 are depth 3, and only six are depth 4 or more, all in one league (test-L10). See [Limits and disclosures](#limits-and-disclosures).
- Held-out wording. The held-out split changes wording, not rule types.
- Repeats. The headline is one run per model. The option-shuffle pass covers 15 models (one seed, 20261101), and the run-to-run, Vulkan and backend repeats are diagnostics, not extra headline runs. The first d3:9b repeat attempt ran on Ollama's Vulkan backend by mistake; the CUDA repeats were run afterwards in a separate folder.
- Test gold is public. Models built or tuned on it would carry a contamination risk that these scores do not measure.
- One shuffle seed, one machine and one backend for the repeats (plus the Vulkan diagnostic). The build costs come from one machine.
- Prompt wording varies only through the held-out phrasing families. No other prompt variants were run.
- Judges. The answerability audits and the item audit use model judges (two Haiku 5.5 judge sets), not people. The item audit judged all 423 all-wrong items, 127 sampled weak-over-strong items and 50 unflagged controls. Pre-registered result: 13 of 550 judged flagged items are suspect labels. This result is fragile: 9 of the 13 are group items from one judge slice, where one judge gave the gold bounds under the kind label `none`, and one judge instance in that slice agreed with gold on only 24 of 100 items; with the discarded first answers for that slice the count is 5. Under an exploratory bound-based reading, 4 candidates remain (g00453, g00551, r03602, r04933). None is a confirmed label error; no score changes. 0 of 50 controls were labelled suspect (exact two-sided 95% interval 0% to 7.1%; one-sided upper bound 5.8%; only 5 controls are groups). Flags and suspect labels are candidates for review, not confirmed label errors. Suspect labels are not adjudicated, and they remain in the scores.
- Consistency. The check is sound but incomplete, and it ignores paraphrase identities between markets, so its counts are lower bounds.
- Private replication. The private split is owner-run, and only its aggregates are published.
- External reproduction. There is none yet.

None of this measures general reasoning.

## What the benchmark measures

Mentat-Hard measures how accurately, and how fast, a **System One** model makes the typed semantic decisions from which a logical map
is built. A System One model answers in one pass, with no generated reasoning. The questions are about **leagues**: small generated
logical worlds, each with teams, scores and rules. Each league has a **rule card**, a short plain-English statement of its rules,
which the model is given with its questions. The model answers one question per request, of four kinds:

- **Relation** (6,000 on test, 1,500 of them NONE; the private split has 6,000 too): how two propositions relate: EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, or NONE.
  Deeper relations need more inference. Each relation label carries its inference depth (0 to 4+).
- **Group** (1,000 non-reading questions on test): which constraint a set of members satisfies: exactly_one, ordered_chain, at_least_k,
  at_most_k, at_most_one, or none.
- **Binding** (3,348 on test): whether a proposition is bound or unresolved (an ambiguity the model should flag).
- **Reading** (184 questions on test, each with two readings, scored as two items): what each reading of an ambiguous proposition picks.

The **headline** is the mean of relation MCC and group MCC, computed on raw decisions (no relation floor). The owner chose it on dev on
2026-10-30, after the dev runs and before any test run. **Binding MCC** is secondary. **Reading MCC**, **held-out relation MCC**, and
**Q_N** are diagnostics. MCC is the multiclass Matthews correlation coefficient (Gorodkin's R_K), so 0 means no better than chance and
1 means perfect. Q_N is the end-to-end exam score of the model's compiled base, and it does not rank models (see
[Q_N and why it is not the ranking](#q_n-and-why-it-is-not-the-ranking)).

The reference for the scoring details is [docs/mentat_hard.md](../docs/mentat_hard.md) and its scorer, `python/mentat_bench/decision_scoring.py`.

## Leaderboard

Test split (`mentat_hard_test`, test set revision 3.1). Intervals are 95% percentile intervals of the league bootstrap (B = 2000, seed 0,
18 leagues). Rank is by the headline point estimate among the 15 models. **Ranks are not statistically resolved**: adjacent ranks
often have overlapping intervals. Gap to D3 27B is the reference minus the run, paired within each bootstrap draw. A gap whose
interval excludes 0 is resolved. Italic rows are baselines, not System One models. Params and quant come from the Ollama tag; a
dash means the tag does not state them. Laya's tag is `latest`, which states no parameter count; the results document calls it 0.4B.
License comes from `python/mentat_bench/children/ollama_models.json` (the hf row from `children/models.json`), spelled as in those files. "not recorded" means the file has none. Build time is the
wall time of the build on the test split.

- ‡ **TEV1 0.8B.** Its base was not repaired, so its strict Q_N equals its repaired Q_N (17.03). The pre-registration expected 0.
- † **Laya 0.4B.** Its child abstained for length on most questions, and an abstention is scored as a NONE answer, as pre-registered.

| Rank | Run id | Model | Params | Quant | Headline: relation + group MCC [95% CI] | Relation MCC [95% CI] | Group MCC [95% CI] | Binding MCC, secondary [95% CI] | Gap to D3 27B [95% CI] | Private headline [95% CI] | Build (min) | License |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `d3_27b-q8_0` | D3 27B | 27B | q8_0 | **0.519 [0.497, 0.541]** | 0.788 [0.766, 0.814] | 0.249 [0.224, 0.273] | 0.000 [0.000, 0.000] | reference | 0.525 [0.504, 0.546] | 74.6 | Apache-2.0 |
| 2 | `d3_9b-q8_0` | D3 9B | 9B | q8_0 | **0.275 [0.251, 0.300]** | 0.462 [0.438, 0.487] | 0.088 [0.054, 0.121] | 0.000 [0.000, 0.000] | 0.244 [0.221, 0.266] | 0.273 [0.257, 0.293] | 21.9 | Apache-2.0 |
| 3 | `pplx-decider_27b` | Pplx Decider 27B | 27B | — | **0.253 [0.239, 0.269]** | 0.512 [0.490, 0.536] | -0.007 [-0.029, 0.018] | 0.000 [0.000, 0.000] | 0.266 [0.244, 0.288] | 0.260 [0.246, 0.277] | 74.4 | Apache-2.0 |
| 4 | `kev_9b-bf16` | Kev 9B | 9B | bf16 | **0.236 [0.215, 0.258]** | 0.325 [0.302, 0.348] | 0.147 [0.121, 0.175] | -0.012 [-0.032, 0.014] | 0.283 [0.265, 0.302] | 0.215 [0.197, 0.233] | 34.9 | Apache-2.0 |
| 5 | `d3_4b-q8_0` | D3 4B | 4B | q8_0 | **0.233 [0.216, 0.251]** | 0.393 [0.370, 0.418] | 0.072 [0.049, 0.096] | 0.000 [0.000, 0.000] | 0.286 [0.267, 0.306] | 0.227 [0.213, 0.241] | 15.4 | Apache-2.0 |
| 6 | `clef_27b-q8_0` | Clef 27B | 27B | q8_0 | **0.207 [0.186, 0.231]** | 0.393 [0.366, 0.420] | 0.021 [-0.006, 0.049] | 0.213 [0.108, 0.318] | 0.312 [0.288, 0.336] | 0.223 [0.204, 0.244] | 78.7 | not recorded |
| 7 | `nimble-v3_9b-q8_0` | Nimble-v3 9B | 9B | q8_0 | **0.201 [0.189, 0.215]** | 0.277 [0.251, 0.304] | 0.126 [0.106, 0.147] | -0.025 [-0.029, -0.021] | 0.317 [0.296, 0.338] | 0.194 [0.174, 0.216] | 28.3 | CC-BY-NC-4.0 |
| 8 | `nimble_9b-bf16` | Nimble 9B | 9B | bf16 | **0.178 [0.167, 0.190]** | 0.272 [0.258, 0.286] | 0.084 [0.057, 0.109] | 0.145 [0.000, 0.258] | 0.340 [0.318, 0.364] | 0.165 [0.153, 0.179] | 27.7 | not recorded |
| 9 | `clef-flash_9b` | Clef-Flash 9B | 9B | — | **0.172 [0.163, 0.181]** | 0.250 [0.235, 0.266] | 0.093 [0.074, 0.113] | 0.019 [-0.020, 0.092] | 0.347 [0.328, 0.368] | 0.147 [0.134, 0.159] | 26.4 | Apache-2.0 |
| 10 | `tev1_4b-q8_0` | TEV1 4B | 4B | q8_0 | **0.144 [0.126, 0.163]** | 0.300 [0.272, 0.329] | -0.012 [-0.036, 0.013] | 0.009 [-0.013, 0.031] | 0.374 [0.350, 0.398] | 0.129 [0.105, 0.153] | 17.2 | Apache-2.0 |
| 11 | `d1_3b-q8_0` | D1 3B | 3B | q8_0 | **0.071 [0.058, 0.087]** | 0.115 [0.093, 0.139] | 0.027 [0.003, 0.053] | 0.072 [-0.017, 0.171] | 0.447 [0.428, 0.469] | 0.059 [0.048, 0.072] | 8.5 | not recorded |
| 12 | `d3_2b-q8_0` | D3 2B | 2B | q8_0 | **0.055 [0.042, 0.069]** | 0.171 [0.154, 0.188] | -0.060 [-0.082, -0.037] | -0.022 [-0.054, 0.015] | 0.463 [0.447, 0.481] | 0.065 [0.043, 0.088] | 6.9 | Apache-2.0 |
| 13 | `tev1_0.8b-q8_0` | TEV1 0.8B ‡ | 0.8B | q8_0 | **-0.000 [-0.005, 0.005]** | -0.000 [-0.009, 0.010] | 0.000 [0.000, 0.000] | -0.027 [-0.075, 0.015] | 0.519 [0.499, 0.540] | 0.003 [-0.001, 0.008] | 6.6 | Apache-2.0 |
| 14 | `d1-omni_600m-q8_0` | D1-Omni 600M | 600M | q8_0 | **-0.003 [-0.010, 0.004]** | 0.015 [0.003, 0.027] | -0.021 [-0.033, -0.007] | 0.045 [0.038, 0.052] | 0.522 [0.500, 0.544] | 0.005 [-0.004, 0.015] | 4.7 | LFM-Open-1.0 |
| 15 | `laya_latest` | Laya 0.4B † | — | — | **-0.020 [-0.032, -0.007]** | -0.016 [-0.029, 0.002] | -0.024 [-0.041, -0.006] | -0.036 [-0.086, 0.011] | 0.539 [0.511, 0.564] | -0.008 [-0.026, 0.011] | 2.4 | Apache-2.0 |
| — | `heuristic` | *Rules (heuristic child)* | — | — | *0.140 [0.121, 0.153]* | *0.251 [0.235, 0.268]* | *0.030 [0.000, 0.053]* | *0.000 [0.000, 0.000]* | *0.378 [0.362, 0.404]* | 0.139 [0.115, 0.159] | 0.0 | — |
| — | `lexical` | *Lexical* | — | — | *0.000 [0.000, 0.000]* | *0.000 [0.000, 0.000]* | *0.000 [0.000, 0.000]* | *0.000 [0.000, 0.000]* | *0.519 [0.497, 0.541]* | — | 0.0 | — |
| — | `embedding` | *Embedding* | — | — | *0.007 [-0.002, 0.014]* | *0.014 [-0.004, 0.029]* | *0.000 [0.000, 0.000]* | *0.000 [0.000, 0.000]* | *0.511 [0.489, 0.536]* | — | 0.3 | — |
| — | `empty` | *Empty* | — | — | *—* | *—* | *—* | *—* | *—* | — | 0.2 | — |
| — | `gold_view` | *gold_view (perfect curated answers)* | — | — | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *-0.481 [-0.503, -0.459]* | 1.000 [1.000, 1.000] | 0.0 | — |
| — | `oracle` | *Oracle* | — | — | *—* | *—* | *—* | *—* | *—* | — | 0.0 | — |
| — | `hf` | *hf pipeline (Qwen2.5-1.5B-Instruct)* | — | — | *0.008 [-0.002, 0.018]* | *0.030 [0.018, 0.043]* | *-0.015 [-0.027, -0.002]* | *-0.018 [-0.021, -0.014]* | *0.511 [0.485, 0.537]* | — | 1.5 | Apache-2.0 |

The machine-readable version of this table, with ranks, notes, depth accuracy, coverage, abstention counts, dev values, private values,
and the file each row came from, is [v0.1.0/leaderboard.json](v0.1.0/leaderboard.json).

## Which differences are resolved

The statements below are the round 3 record's own (the round 3 results document, v0.1.0). Intervals are the league bootstrap; the gaps in that record are paired against D3 27B. The v0.1.1 analysis section below pairs all 105 model pairs with Holm correction.

- **D3 27B is above every other model and every non-ceiling baseline.** All 14 other models, and the hf baseline, have gap intervals
  that exclude 0. The smallest gap is to D3 9B, 0.244 [0.221, 0.266]. The rules, embedding, lexical and hf baselines are also below
  D3 27B by intervals that exclude 0. Only gold_view, the ceiling, is above it.
- **The relation lead is clear.** D3 27B's relation gap intervals to every other model exclude 0.
- **The group lead is smaller.** D3 27B's group MCC is 0.249 [0.224, 0.273], and Kev 9B's is 0.147 [0.121, 0.175]. The group second place
  is not resolved: Nimble-v3 9B is at 0.126 [0.106, 0.147], and its interval overlaps Kev 9B's.
- **Nine models have headline intervals entirely above the rules interval** (rules: 0.121 to 0.153): D3 27B, D3 9B, Pplx Decider 27B,
  Kev 9B, D3 4B, Clef 27B, Nimble-v3 9B, Nimble 9B and Clef-Flash 9B. TEV1 4B (0.126 to 0.163) overlaps the rules interval. These
  comparisons are unpaired. The v0.1.1 analysis pairs the 15 models with each other (105 pairs, Holm-adjusted); it does not pair them with the baselines.
- **The middle of the table is not ordered by these intervals.** The intervals of D3 9B, Pplx Decider 27B, Kev 9B and D3 4B all overlap
  one another, and Clef 27B overlaps D3 4B, Kev 9B, Nimble-v3 9B and Nimble 9B. The results JSON lists 27 overlapping pairs of headline
  intervals (`headline_interval_overlaps` in [leaderboard.json](v0.1.0/leaderboard.json)). Overlap is not evidence of a tie.
- **Some runs are indistinguishable from zero.** Lexical (0.000), embedding (0.007, [-0.002, 0.014]), hf (0.008, [-0.002, 0.018]),
  TEV1 0.8B and D1-Omni 600M all have headline intervals that include 0. Laya's interval lies entirely below 0 (upper bound -0.007).

<!-- analysis:start -->

### Analysis (v0.1.1, same measurements)

Every table in this section is computed by `tools/eval/release_analysis.py` from the committed v0.1.0 predictions, cells and fixture; the numbers are in [v0.1.1/analysis.json](v0.1.1/analysis.json). No score changes.

The leaderboard's intervals are the published league-bootstrap intervals (B = 2000). The rank, pairwise and depth intervals and the p-values in this section are the paired draws of the same resampling at B = 20000 (the first 2000 are the published draws). Uncertainty comes only from resampling the 18 leagues of one fixture: each model has one deterministic run, and the three metric families (headline, relation, group) are corrected separately, not against each other.

#### Rank intervals

Rank 1 is the highest point. The interval is the 2.5% to 97.5% range of the rank over the draws.

| Model | Headline rank (95%) | Relation rank (95%) | Group rank (95%) |
|---|---|---|---|
| d3_27b-q8_0 | 1 [1, 1] (median 1) | 1 [1, 1] (median 1) | 1 [1, 1] (median 1) |
| d3_9b-q8_0 | 2 [2, 3] (median 2) | 3 [3, 3] (median 3) | 5 [3, 7] (median 5) |
| pplx-decider_27b | 3 [2, 4] (median 3) | 2 [2, 2] (median 2) | 11 [9, 14] (median 11) |
| kev_9b-bf16 | 4 [3, 5] (median 4) | 6 [6, 6] (median 6) | 2 [2, 3] (median 2) |
| d3_4b-q8_0 | 5 [4, 5] (median 5) | 4 [4, 5] (median 4) | 7 [5, 7] (median 7) |
| clef_27b-q8_0 | 6 [6, 7] (median 6) | 5 [4, 5] (median 5) | 9 [8, 11] (median 9) |
| nimble-v3_9b-q8_0 | 7 [6, 7] (median 7) | 8 [8, 10] (median 8) | 3 [2, 4] (median 3) |
| nimble_9b-bf16 | 8 [8, 9] (median 8) | 9 [7, 9] (median 9) | 6 [4, 7] (median 6) |
| clef-flash_9b | 9 [8, 9] (median 9) | 10 [9, 10] (median 10) | 4 [4, 6] (median 5) |
| tev1_4b-q8_0 | 10 [10, 10] (median 10) | 7 [7, 8] (median 7) | 12 [9, 14] (median 12) |
| d1_3b-q8_0 | 11 [11, 12] (median 11) | 12 [12, 12] (median 12) | 8 [8, 10] (median 8) |
| d3_2b-q8_0 | 12 [11, 12] (median 12) | 11 [11, 11] (median 11) | 15 [15, 15] (median 15) |
| tev1_0.8b-q8_0 | 13 [13, 14] (median 13) | 14 [13, 15] (median 14) | 10 [9, 12] (median 10) |
| d1-omni_600m-q8_0 | 14 [13, 14] (median 14) | 13 [13, 14] (median 13) | 13 [11, 14] (median 13) |
| laya_latest | 15 [15, 15] (median 15) | 15 [14, 15] (median 15) | 14 [11, 14] (median 13) |

#### Holm-resolved pairs

Differences are higher minus lower. The Holm p-value is adjusted within each family; when no draw lies on the other side it is a floor. Leave-one-league-out: each resolved pair's point difference is recomputed with each of the 18 leagues left out.

**Headline**: 89 of 105 pairs are resolved (Holm, alpha 0.05). Leave-one-league-out: 89 of 89 keep their sign in all 18 recomputations.
- d3_27b-q8_0 over d3_9b-q8_0: higher minus lower 0.244 [0.221, 0.266], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over pplx-decider_27b: higher minus lower 0.266 [0.245, 0.289], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_9b-bf16: higher minus lower 0.283 [0.265, 0.301], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_4b-q8_0: higher minus lower 0.286 [0.267, 0.306], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef_27b-q8_0: higher minus lower 0.312 [0.288, 0.337], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.317 [0.296, 0.339], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble_9b-bf16: higher minus lower 0.340 [0.317, 0.364], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef-flash_9b: higher minus lower 0.347 [0.328, 0.368], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.374 [0.350, 0.399], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.447 [0.428, 0.469], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.463 [0.447, 0.481], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.519 [0.499, 0.540], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.522 [0.500, 0.545], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over laya_latest: higher minus lower 0.539 [0.511, 0.564], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_9b-bf16: higher minus lower 0.039 [0.021, 0.056], p_holm 0.0105 (1 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_4b-q8_0: higher minus lower 0.042 [0.020, 0.063], p_holm 0.0105 (5 of 20,000 draws on the other side)
- d3_9b-q8_0 over clef_27b-q8_0: higher minus lower 0.068 [0.041, 0.094], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.073 [0.052, 0.095], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over nimble_9b-bf16: higher minus lower 0.096 [0.068, 0.124], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over clef-flash_9b: higher minus lower 0.103 [0.077, 0.129], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.131 [0.108, 0.154], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.204 [0.180, 0.227], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.220 [0.200, 0.239], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.275 [0.251, 0.300], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.278 [0.253, 0.305], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over laya_latest: higher minus lower 0.295 [0.269, 0.318], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over clef_27b-q8_0: higher minus lower 0.046 [0.022, 0.070], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over nimble-v3_9b-q8_0: higher minus lower 0.051 [0.030, 0.071], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over nimble_9b-bf16: higher minus lower 0.074 [0.054, 0.095], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over clef-flash_9b: higher minus lower 0.081 [0.065, 0.099], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over tev1_4b-q8_0: higher minus lower 0.109 [0.089, 0.128], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d1_3b-q8_0: higher minus lower 0.182 [0.159, 0.201], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_2b-q8_0: higher minus lower 0.197 [0.181, 0.214], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over tev1_0.8b-q8_0: higher minus lower 0.253 [0.241, 0.266], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d1-omni_600m-q8_0: higher minus lower 0.256 [0.239, 0.274], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over laya_latest: higher minus lower 0.273 [0.251, 0.294], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble-v3_9b-q8_0: higher minus lower 0.034 [0.017, 0.054], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble_9b-bf16: higher minus lower 0.058 [0.033, 0.082], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over clef-flash_9b: higher minus lower 0.064 [0.044, 0.086], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_4b-q8_0: higher minus lower 0.092 [0.069, 0.114], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1_3b-q8_0: higher minus lower 0.165 [0.146, 0.185], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_2b-q8_0: higher minus lower 0.181 [0.162, 0.199], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.236 [0.216, 0.258], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.239 [0.216, 0.264], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over laya_latest: higher minus lower 0.256 [0.233, 0.277], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.031 [0.018, 0.046], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over nimble_9b-bf16: higher minus lower 0.054 [0.033, 0.076], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over clef-flash_9b: higher minus lower 0.061 [0.044, 0.079], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_4b-q8_0: higher minus lower 0.089 [0.068, 0.109], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.162 [0.148, 0.175], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.177 [0.159, 0.196], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.233 [0.216, 0.250], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.236 [0.219, 0.254], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over laya_latest: higher minus lower 0.253 [0.231, 0.273], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over clef-flash_9b: higher minus lower 0.035 [0.017, 0.053], p_holm 0.0105 (4 of 20,000 draws on the other side)
- clef_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.063 [0.035, 0.089], p_holm 0.0105 (2 of 20,000 draws on the other side)
- clef_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.136 [0.110, 0.162], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.152 [0.133, 0.172], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.207 [0.186, 0.230], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.210 [0.187, 0.235], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over laya_latest: higher minus lower 0.227 [0.202, 0.254], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over clef-flash_9b: higher minus lower 0.030 [0.015, 0.045], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.057 [0.038, 0.078], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.130 [0.118, 0.142], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.146 [0.127, 0.164], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.202 [0.189, 0.216], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.205 [0.190, 0.220], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over laya_latest: higher minus lower 0.221 [0.201, 0.240], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1_3b-q8_0: higher minus lower 0.107 [0.089, 0.123], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d3_2b-q8_0: higher minus lower 0.123 [0.103, 0.143], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.178 [0.165, 0.193], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.181 [0.171, 0.192], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over laya_latest: higher minus lower 0.198 [0.179, 0.217], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1_3b-q8_0: higher minus lower 0.101 [0.085, 0.114], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over d3_2b-q8_0: higher minus lower 0.117 [0.101, 0.132], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over tev1_0.8b-q8_0: higher minus lower 0.172 [0.163, 0.181], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1-omni_600m-q8_0: higher minus lower 0.175 [0.165, 0.186], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over laya_latest: higher minus lower 0.192 [0.174, 0.209], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.073 [0.051, 0.094], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.089 [0.073, 0.107], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.144 [0.127, 0.161], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.147 [0.129, 0.166], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over laya_latest: higher minus lower 0.164 [0.146, 0.182], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.071 [0.058, 0.087], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.074 [0.060, 0.091], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over laya_latest: higher minus lower 0.091 [0.069, 0.113], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.055 [0.042, 0.069], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.058 [0.043, 0.075], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over laya_latest: higher minus lower 0.075 [0.057, 0.092], p_holm < 0.0105 (0 of 20,000 draws on the other side)

**Relation**: 97 of 105 pairs are resolved (Holm, alpha 0.05). Leave-one-league-out: 97 of 97 keep their sign in all 18 recomputations.
- d3_27b-q8_0 over d3_9b-q8_0: higher minus lower 0.327 [0.307, 0.348], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over pplx-decider_27b: higher minus lower 0.276 [0.253, 0.301], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_9b-bf16: higher minus lower 0.464 [0.443, 0.487], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_4b-q8_0: higher minus lower 0.395 [0.372, 0.420], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef_27b-q8_0: higher minus lower 0.396 [0.375, 0.416], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.512 [0.486, 0.538], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble_9b-bf16: higher minus lower 0.516 [0.489, 0.546], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef-flash_9b: higher minus lower 0.538 [0.509, 0.569], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.488 [0.463, 0.514], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.673 [0.655, 0.692], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.618 [0.601, 0.636], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.789 [0.769, 0.811], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.773 [0.747, 0.804], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over laya_latest: higher minus lower 0.805 [0.775, 0.831], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_9b-q8_0: higher minus lower 0.050 [0.024, 0.077], p_holm 0.0105 (1 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_9b-bf16: higher minus lower 0.137 [0.118, 0.157], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_4b-q8_0: higher minus lower 0.068 [0.055, 0.082], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over clef_27b-q8_0: higher minus lower 0.069 [0.047, 0.092], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.185 [0.167, 0.203], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over nimble_9b-bf16: higher minus lower 0.189 [0.162, 0.219], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over clef-flash_9b: higher minus lower 0.211 [0.178, 0.243], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.161 [0.147, 0.176], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.346 [0.327, 0.364], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.291 [0.275, 0.307], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.462 [0.438, 0.485], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.446 [0.416, 0.478], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over laya_latest: higher minus lower 0.478 [0.448, 0.500], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over kev_9b-bf16: higher minus lower 0.187 [0.156, 0.217], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_4b-q8_0: higher minus lower 0.119 [0.089, 0.148], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over clef_27b-q8_0: higher minus lower 0.119 [0.095, 0.143], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over nimble-v3_9b-q8_0: higher minus lower 0.235 [0.208, 0.262], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over nimble_9b-bf16: higher minus lower 0.240 [0.217, 0.263], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over clef-flash_9b: higher minus lower 0.261 [0.232, 0.291], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over tev1_4b-q8_0: higher minus lower 0.212 [0.178, 0.244], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d1_3b-q8_0: higher minus lower 0.397 [0.374, 0.418], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_2b-q8_0: higher minus lower 0.341 [0.317, 0.365], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over tev1_0.8b-q8_0: higher minus lower 0.512 [0.492, 0.533], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d1-omni_600m-q8_0: higher minus lower 0.497 [0.472, 0.526], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- pplx-decider_27b over laya_latest: higher minus lower 0.528 [0.496, 0.559], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over kev_9b-bf16: higher minus lower 0.069 [0.051, 0.087], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over kev_9b-bf16: higher minus lower 0.068 [0.045, 0.094], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble-v3_9b-q8_0: higher minus lower 0.048 [0.023, 0.070], p_holm 0.0105 (1 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble_9b-bf16: higher minus lower 0.052 [0.026, 0.080], p_holm 0.0105 (1 of 20,000 draws on the other side)
- kev_9b-bf16 over clef-flash_9b: higher minus lower 0.074 [0.048, 0.102], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_4b-q8_0: higher minus lower 0.025 [0.009, 0.039], p_holm 0.0330 (31 of 20,000 draws on the other side)
- kev_9b-bf16 over d1_3b-q8_0: higher minus lower 0.210 [0.190, 0.230], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_2b-q8_0: higher minus lower 0.154 [0.136, 0.170], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.325 [0.303, 0.349], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.309 [0.280, 0.341], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over laya_latest: higher minus lower 0.341 [0.312, 0.362], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.117 [0.102, 0.131], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over nimble_9b-bf16: higher minus lower 0.121 [0.095, 0.147], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over clef-flash_9b: higher minus lower 0.143 [0.113, 0.172], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_4b-q8_0: higher minus lower 0.093 [0.075, 0.109], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.278 [0.258, 0.299], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.223 [0.207, 0.240], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.393 [0.371, 0.417], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.378 [0.348, 0.409], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over laya_latest: higher minus lower 0.410 [0.379, 0.433], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.116 [0.093, 0.139], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over nimble_9b-bf16: higher minus lower 0.121 [0.096, 0.146], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over clef-flash_9b: higher minus lower 0.142 [0.113, 0.172], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.093 [0.066, 0.119], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.278 [0.252, 0.302], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.222 [0.205, 0.240], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.393 [0.368, 0.419], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.378 [0.349, 0.409], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over laya_latest: higher minus lower 0.409 [0.378, 0.438], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.162 [0.139, 0.185], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.106 [0.088, 0.127], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.277 [0.253, 0.302], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.261 [0.227, 0.297], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over laya_latest: higher minus lower 0.293 [0.261, 0.323], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1_3b-q8_0: higher minus lower 0.157 [0.127, 0.185], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d3_2b-q8_0: higher minus lower 0.102 [0.079, 0.124], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.273 [0.254, 0.289], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.257 [0.242, 0.273], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over laya_latest: higher minus lower 0.289 [0.263, 0.309], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over clef-flash_9b: higher minus lower 0.050 [0.016, 0.086], p_holm 0.0330 (29 of 20,000 draws on the other side)
- clef-flash_9b over d1_3b-q8_0: higher minus lower 0.135 [0.102, 0.166], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over d3_2b-q8_0: higher minus lower 0.080 [0.056, 0.103], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over tev1_0.8b-q8_0: higher minus lower 0.251 [0.232, 0.268], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1-omni_600m-q8_0: higher minus lower 0.235 [0.216, 0.256], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over laya_latest: higher minus lower 0.267 [0.241, 0.290], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.185 [0.163, 0.208], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.130 [0.109, 0.152], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.300 [0.274, 0.329], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.285 [0.250, 0.322], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over laya_latest: higher minus lower 0.316 [0.283, 0.342], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over d1_3b-q8_0: higher minus lower 0.056 [0.038, 0.073], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.115 [0.096, 0.137], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.100 [0.071, 0.132], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over laya_latest: higher minus lower 0.131 [0.101, 0.155], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.171 [0.155, 0.188], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.155 [0.132, 0.179], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over laya_latest: higher minus lower 0.187 [0.163, 0.204], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1-omni_600m-q8_0 over laya_latest: higher minus lower 0.031 [0.010, 0.048], p_holm 0.0360 (39 of 20,000 draws on the other side)

**Group**: 65 of 105 pairs are resolved (Holm, alpha 0.05). Leave-one-league-out: 65 of 65 keep their sign in all 18 recomputations.
- d3_27b-q8_0 over d3_9b-q8_0: higher minus lower 0.160 [0.124, 0.196], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over pplx-decider_27b: higher minus lower 0.255 [0.219, 0.293], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_9b-bf16: higher minus lower 0.101 [0.079, 0.124], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_4b-q8_0: higher minus lower 0.176 [0.152, 0.200], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef_27b-q8_0: higher minus lower 0.228 [0.192, 0.266], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.122 [0.093, 0.155], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble_9b-bf16: higher minus lower 0.164 [0.136, 0.197], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef-flash_9b: higher minus lower 0.156 [0.141, 0.171], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.261 [0.223, 0.299], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.222 [0.183, 0.263], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.309 [0.281, 0.338], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.249 [0.224, 0.273], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.270 [0.246, 0.293], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over laya_latest: higher minus lower 0.272 [0.239, 0.305], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over pplx-decider_27b: higher minus lower 0.095 [0.051, 0.141], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_9b-q8_0: higher minus lower 0.059 [0.024, 0.098], p_holm 0.0344 (7 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.100 [0.052, 0.151], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.148 [0.108, 0.187], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.088 [0.055, 0.123], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.110 [0.074, 0.143], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over laya_latest: higher minus lower 0.112 [0.078, 0.145], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over pplx-decider_27b: higher minus lower 0.154 [0.117, 0.193], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over pplx-decider_27b: higher minus lower 0.079 [0.050, 0.109], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over pplx-decider_27b: higher minus lower 0.133 [0.097, 0.169], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over pplx-decider_27b: higher minus lower 0.091 [0.057, 0.126], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over pplx-decider_27b: higher minus lower 0.100 [0.065, 0.135], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_4b-q8_0: higher minus lower 0.075 [0.044, 0.104], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over clef_27b-q8_0: higher minus lower 0.126 [0.093, 0.164], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over clef-flash_9b: higher minus lower 0.054 [0.029, 0.078], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_4b-q8_0: higher minus lower 0.159 [0.116, 0.201], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1_3b-q8_0: higher minus lower 0.120 [0.079, 0.163], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_2b-q8_0: higher minus lower 0.207 [0.174, 0.241], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.147 [0.121, 0.176], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.169 [0.139, 0.199], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over laya_latest: higher minus lower 0.171 [0.140, 0.203], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d3_4b-q8_0: higher minus lower 0.054 [0.026, 0.081], p_holm 0.0176 (3 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_4b-q8_0: higher minus lower 0.084 [0.047, 0.122], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.132 [0.101, 0.165], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.072 [0.049, 0.096], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.094 [0.073, 0.115], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over laya_latest: higher minus lower 0.096 [0.065, 0.130], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over clef_27b-q8_0: higher minus lower 0.105 [0.070, 0.139], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over clef_27b-q8_0: higher minus lower 0.072 [0.041, 0.105], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.081 [0.048, 0.117], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.138 [0.105, 0.177], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.099 [0.077, 0.123], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.186 [0.153, 0.220], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.126 [0.106, 0.147], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.148 [0.126, 0.169], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over laya_latest: higher minus lower 0.150 [0.121, 0.181], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over tev1_4b-q8_0: higher minus lower 0.096 [0.057, 0.134], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1_3b-q8_0: higher minus lower 0.057 [0.023, 0.094], p_holm 0.0344 (7 of 20,000 draws on the other side)
- nimble_9b-bf16 over d3_2b-q8_0: higher minus lower 0.144 [0.107, 0.180], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.084 [0.057, 0.110], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.106 [0.080, 0.129], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over laya_latest: higher minus lower 0.108 [0.075, 0.140], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over tev1_4b-q8_0: higher minus lower 0.105 [0.075, 0.135], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1_3b-q8_0: higher minus lower 0.066 [0.033, 0.102], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over d3_2b-q8_0: higher minus lower 0.153 [0.131, 0.175], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over tev1_0.8b-q8_0: higher minus lower 0.093 [0.074, 0.112], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1-omni_600m-q8_0: higher minus lower 0.114 [0.095, 0.135], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- clef-flash_9b over laya_latest: higher minus lower 0.117 [0.088, 0.145], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over d3_2b-q8_0: higher minus lower 0.087 [0.052, 0.120], p_holm < 0.0105 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.048 [0.019, 0.077], p_holm 0.0369 (8 of 20,000 draws on the other side)
- tev1_0.8b-q8_0 over d3_2b-q8_0: higher minus lower 0.060 [0.038, 0.082], p_holm < 0.0105 (0 of 20,000 draws on the other side)

#### Weighting of relation and group MCC

The headline weights the relation and group MCC equally (the published scheme, w = 0.5). Each scheme weights the relation MCC by w and the group MCC by 1 - w, with its own rank intervals and its own Holm family of 105 pairs. The published column reproduces the headline ranks and pairs.

| Model | relation_only (1 / 0) | r75 (0.75 / 0.25) | published (0.5 / 0.5) | r25 (0.25 / 0.75) | group_only (0 / 1) |
|---|---|---|---|---|---|
| d3_27b-q8_0 | 1 [1, 1] | 1 [1, 1] | 1 [1, 1] | 1 [1, 1] | 1 [1, 1] |
| d3_9b-q8_0 | 3 [3, 3] | 3 [2, 3] | 2 [2, 3] | 3 [2, 5] | 5 [3, 7] |
| pplx-decider_27b | 2 [2, 2] | 2 [2, 3] | 3 [2, 4] | 8 [6, 9] | 11 [9, 14] |
| kev_9b-bf16 | 6 [6, 6] | 6 [5, 6] | 4 [3, 5] | 2 [2, 3] | 2 [2, 3] |
| d3_4b-q8_0 | 4 [4, 5] | 4 [4, 5] | 5 [4, 5] | 5 [4, 6] | 7 [5, 7] |
| clef_27b-q8_0 | 5 [4, 5] | 5 [4, 6] | 6 [6, 7] | 9 [6, 9] | 9 [8, 11] |
| nimble-v3_9b-q8_0 | 8 [8, 10] | 7 [7, 8] | 7 [6, 7] | 4 [3, 5] | 3 [2, 4] |
| nimble_9b-bf16 | 9 [7, 9] | 8 [7, 9] | 8 [8, 9] | 7 [5, 9] | 6 [4, 7] |
| clef-flash_9b | 10 [9, 10] | 10 [9, 10] | 9 [8, 9] | 6 [6, 8] | 4 [4, 6] |
| tev1_4b-q8_0 | 7 [7, 8] | 9 [7, 10] | 10 [10, 10] | 10 [10, 11] | 12 [9, 14] |
| d1_3b-q8_0 | 12 [12, 12] | 12 [12, 12] | 11 [11, 12] | 11 [10, 11] | 8 [8, 10] |
| d3_2b-q8_0 | 11 [11, 11] | 11 [11, 11] | 12 [11, 12] | 13 [12, 15] | 15 [15, 15] |
| tev1_0.8b-q8_0 | 14 [13, 15] | 14 [13, 14] | 13 [13, 14] | 12 [12, 13] | 10 [9, 12] |
| d1-omni_600m-q8_0 | 13 [13, 14] | 13 [13, 14] | 14 [13, 14] | 14 [13, 15] | 13 [11, 14] |
| laya_latest | 15 [14, 15] | 15 [15, 15] | 15 [15, 15] | 15 [14, 15] | 14 [11, 14] |

Holm-resolved pairs per scheme (of 105): relation_only 97, r75 94, published 89, r25 80, group_only 65.

Robust pairs: 48 are resolved under every scheme with the same winner (weighting.robust_pairs in analysis.json).

Weight-dependent pairs (53): the winner differs across schemes, or the pair is resolved under some schemes only.

- D3 9B vs Pplx Decider 27B, higher by scheme: relation_only Pplx Decider 27B, r75 not resolved, published not resolved, r25 D3 9B, group_only D3 9B.
- D3 9B vs Kev 9B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 not resolved, group_only Kev 9B.
- D3 9B vs D3 4B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 not resolved, group_only not resolved.
- D3 9B vs Clef 27B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 D3 9B, group_only not resolved.
- D3 9B vs Nimble-v3 9B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 not resolved, group_only not resolved.
- D3 9B vs Nimble 9B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 not resolved, group_only not resolved.
- D3 9B vs Clef-Flash 9B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 D3 9B, group_only not resolved.
- D3 9B vs D1 3B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 D3 9B, group_only not resolved.
- Pplx Decider 27B vs Kev 9B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published not resolved, r25 Kev 9B, group_only Kev 9B.
- Pplx Decider 27B vs D3 4B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published not resolved, r25 not resolved, group_only D3 4B.
- Pplx Decider 27B vs Clef 27B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 not resolved, group_only not resolved.
- Pplx Decider 27B vs Nimble-v3 9B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Nimble-v3 9B, group_only Nimble-v3 9B.
- Pplx Decider 27B vs Nimble 9B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 not resolved, group_only Nimble 9B.
- Pplx Decider 27B vs Clef-Flash 9B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 not resolved, group_only Clef-Flash 9B.
- Pplx Decider 27B vs TEV1 4B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs D1 3B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs D3 2B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs TEV1 0.8B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs D1-Omni 600M, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs Laya 0.4B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Kev 9B vs D3 4B, higher by scheme: relation_only D3 4B, r75 D3 4B, published not resolved, r25 Kev 9B, group_only Kev 9B.
- Kev 9B vs Clef 27B, higher by scheme: relation_only Clef 27B, r75 not resolved, published not resolved, r25 Kev 9B, group_only Kev 9B.
- Kev 9B vs Nimble-v3 9B, higher by scheme: relation_only Kev 9B, r75 Kev 9B, published Kev 9B, r25 not resolved, group_only not resolved.
- Kev 9B vs Nimble 9B, higher by scheme: relation_only Kev 9B, r75 Kev 9B, published Kev 9B, r25 Kev 9B, group_only not resolved.
- D3 4B vs Nimble-v3 9B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 not resolved, group_only Nimble-v3 9B.
- D3 4B vs Nimble 9B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 not resolved, group_only not resolved.
- D3 4B vs Clef-Flash 9B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 not resolved, group_only not resolved.
- D3 4B vs D1 3B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 D3 4B, group_only not resolved.
- Clef 27B vs Nimble-v3 9B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published not resolved, r25 Nimble-v3 9B, group_only Nimble-v3 9B.
- Clef 27B vs Nimble 9B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published not resolved, r25 not resolved, group_only not resolved.
- Clef 27B vs Clef-Flash 9B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 not resolved, group_only Clef-Flash 9B.
- Clef 27B vs TEV1 4B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 not resolved, group_only not resolved.
- Clef 27B vs D1 3B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Clef 27B vs TEV1 0.8B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Clef 27B vs D1-Omni 600M, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Clef 27B vs Laya 0.4B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Nimble-v3 9B vs Clef-Flash 9B, higher by scheme: relation_only not resolved, r75 not resolved, published Nimble-v3 9B, r25 Nimble-v3 9B, group_only not resolved.
- Nimble-v3 9B vs TEV1 4B, higher by scheme: relation_only not resolved, r75 not resolved, published Nimble-v3 9B, r25 Nimble-v3 9B, group_only Nimble-v3 9B.
- Nimble 9B vs Clef-Flash 9B, higher by scheme: relation_only not resolved, r75 Nimble 9B, published not resolved, r25 not resolved, group_only not resolved.
- Nimble 9B vs TEV1 4B, higher by scheme: relation_only not resolved, r75 not resolved, published not resolved, r25 Nimble 9B, group_only Nimble 9B.
- Clef-Flash 9B vs TEV1 4B, higher by scheme: relation_only TEV1 4B, r75 not resolved, published not resolved, r25 Clef-Flash 9B, group_only Clef-Flash 9B.
- TEV1 4B vs D1 3B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 not resolved, group_only not resolved.
- TEV1 4B vs D3 2B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 TEV1 4B, group_only not resolved.
- TEV1 4B vs TEV1 0.8B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 TEV1 4B, group_only not resolved.
- TEV1 4B vs D1-Omni 600M, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 TEV1 4B, group_only not resolved.
- TEV1 4B vs Laya 0.4B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 TEV1 4B, group_only not resolved.
- D1 3B vs D3 2B, higher by scheme: relation_only D3 2B, r75 not resolved, published not resolved, r25 D1 3B, group_only D1 3B.
- D1 3B vs TEV1 0.8B, higher by scheme: relation_only D1 3B, r75 D1 3B, published D1 3B, r25 D1 3B, group_only not resolved.
- D1 3B vs Laya 0.4B, higher by scheme: relation_only D1 3B, r75 D1 3B, published D1 3B, r25 D1 3B, group_only not resolved.
- D3 2B vs TEV1 0.8B, higher by scheme: relation_only D3 2B, r75 D3 2B, published D3 2B, r25 not resolved, group_only TEV1 0.8B.
- D3 2B vs D1-Omni 600M, higher by scheme: relation_only D3 2B, r75 D3 2B, published D3 2B, r25 not resolved, group_only not resolved.
- D3 2B vs Laya 0.4B, higher by scheme: relation_only D3 2B, r75 D3 2B, published D3 2B, r25 not resolved, group_only not resolved.
- D1-Omni 600M vs Laya 0.4B, higher by scheme: relation_only D1-Omni 600M, r75 D1-Omni 600M, published not resolved, r25 not resolved, group_only not resolved.

#### Depth (accuracy by gold depth, with intervals)

A `*` marks a degenerate cell (n < 30 or valid draws below 99%); its interval is shown as n/a. The 4+ bucket holds 6 items in 1 league.

| Run (depth, 95% interval) | 0 | 1 | 2 | 3 | 4+ | NONE |
|---|---|---|---|---|---|---|
| d3_27b-q8_0 | 0.915 [0.901, 0.929] | 0.920 [0.909, 0.931] | 0.716 [0.677, 0.761] | 0.648 [0.611, 0.683] | 0.500* (n/a) | 0.837 [0.803, 0.869] |
| d3_9b-q8_0 | 0.679 [0.654, 0.704] | 0.377 [0.344, 0.407] | 0.349 [0.319, 0.381] | 0.310 [0.273, 0.348] | 0.000* (n/a) | 0.787 [0.759, 0.816] |
| pplx-decider_27b | 0.810 [0.789, 0.828] | 0.662 [0.631, 0.692] | 0.482 [0.449, 0.517] | 0.463 [0.423, 0.504] | 0.333* (n/a) | 0.407 [0.366, 0.449] |
| kev_9b-bf16 | 0.465 [0.433, 0.496] | 0.316 [0.287, 0.346] | 0.113 [0.094, 0.133] | 0.122 [0.088, 0.154] | 0.167* (n/a) | 0.898 [0.873, 0.920] |
| d3_4b-q8_0 | 0.563 [0.538, 0.588] | 0.379 [0.355, 0.403] | 0.327 [0.302, 0.357] | 0.301 [0.263, 0.342] | 0.333* (n/a) | 0.719 [0.688, 0.749] |
| clef_27b-q8_0 | 0.754 [0.734, 0.772] | 0.489 [0.450, 0.525] | 0.437 [0.412, 0.463] | 0.434 [0.405, 0.467] | 0.167* (n/a) | 0.247 [0.201, 0.301] |
| nimble-v3_9b-q8_0 | 0.451 [0.421, 0.483] | 0.428 [0.384, 0.474] | 0.245 [0.228, 0.263] | 0.234 [0.195, 0.264] | 0.000* (n/a) | 0.640 [0.602, 0.679] |
| nimble_9b-bf16 | 0.520 [0.502, 0.539] | 0.545 [0.500, 0.591] | 0.502 [0.471, 0.528] | 0.460 [0.403, 0.506] | 0.333* (n/a) | 0.094 [0.074, 0.114] |
| clef-flash_9b | 0.503 [0.480, 0.526] | 0.499 [0.467, 0.532] | 0.486 [0.465, 0.505] | 0.477 [0.434, 0.523] | 0.333* (n/a) | 0.026 [0.015, 0.039] |
| tev1_4b-q8_0 | 0.488 [0.457, 0.516] | 0.295 [0.269, 0.321] | 0.122 [0.103, 0.144] | 0.098 [0.066, 0.135] | 0.000* (n/a) | 0.852 [0.837, 0.867] |
| d1_3b-q8_0 | 0.245 [0.227, 0.266] | 0.330 [0.297, 0.362] | 0.208 [0.188, 0.232] | 0.213 [0.177, 0.248] | 0.000* (n/a) | 0.480 [0.445, 0.515] |
| d3_2b-q8_0 | 0.163 [0.141, 0.186] | 0.236 [0.209, 0.263] | 0.137 [0.113, 0.164] | 0.115 [0.093, 0.139] | 0.000* (n/a) | 0.870 [0.843, 0.895] |
| tev1_0.8b-q8_0 | 0.014 [0.010, 0.018] | 0.006 [0.001, 0.012] | 0.012 [0.005, 0.019] | 0.015 [0.005, 0.024] | 0.000* (n/a) | 0.950 [0.938, 0.962] |
| d1-omni_600m-q8_0 | 0.151 [0.137, 0.166] | 0.377 [0.343, 0.408] | 0.339 [0.316, 0.363] | 0.320 [0.271, 0.359] | 0.167* (n/a) | 0.061 [0.044, 0.079] |
| laya_latest | 0.051 [0.010, 0.106] | 0.059 [0.012, 0.121] | 0.039 [0.002, 0.092] | 0.021 [0.004, 0.053] | 0.167* (n/a) | 0.801 [0.602, 0.959] |
| heuristic (baseline) | 0.288 [0.272, 0.303] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000* (n/a) | 1.000 [1.000, 1.000] |
| lexical (baseline) | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000* (n/a) | 1.000 [1.000, 1.000] |
| embedding (baseline) | 0.006 [0.002, 0.011] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000* (n/a) | 0.995 [0.992, 0.998] |
| gold_view (gold-backed ceiling) | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000* (n/a) | 1.000 [1.000, 1.000] |
| hf (baseline) | 0.021 [0.014, 0.028] | 0.013 [0.007, 0.018] | 0.001 [0.000, 0.003] | 0.000 [0.000, 0.000] | 0.000* (n/a) | 0.987 [0.981, 0.993] |

#### Consistency

An answer is conflicting if it belongs to a minimal conflict of at most 3 answers under either predicate: the main predicate (logical UNSAT, a forced suite outcome, a cardinality or an invalid group) or the UNSAT-only predicate. The conflicting share is that union, so the UNSAT share is part of it in total and in every op. The main-predicate count is shown on its own. The smallest-witness columns count the main-predicate answers by the size of their smallest witness under the main predicate, so they sum to the main-predicate count; an answer only in a minimal UNSAT-only conflict has no main witness. Each share is over claim-bearing answers, with a league-bootstrap 95% interval. Contradiction detected: the detector finds a conflict in the whole answer set (sound, not complete).

| Run (consistency) | Claim-bearing | Conflicting share (95%) | UNSAT share (95%) | Main-predicate conflicts | Contradiction detected | Smallest witness 1 / 2 / 3 (main predicate) |
|---|---|---|---|---|---|---|
| d3_27b-q8_0 | 5172 | 0.291 [0.235, 0.343] | 0.016 [0.010, 0.022] | 1491 | yes | 76 / 589 / 826 |
| d3_9b-q8_0 | 4159 | 0.435 [0.392, 0.474] | 0.085 [0.060, 0.111] | 1741 | yes | 177 / 797 / 767 |
| pplx-decider_27b | 5941 | 0.538 [0.493, 0.577] | 0.150 [0.132, 0.167] | 3012 | yes | 262 / 1283 / 1467 |
| kev_9b-bf16 | 2522 | 0.414 [0.354, 0.467] | 0.108 [0.083, 0.132] | 1004 | yes | 206 / 427 / 371 |
| d3_4b-q8_0 | 4808 | 0.457 [0.419, 0.491] | 0.039 [0.026, 0.054] | 2162 | yes | 75 / 1092 / 995 |
| clef_27b-q8_0 | 6318 | 0.569 [0.519, 0.613] | 0.078 [0.066, 0.090] | 3569 | yes | 70 / 1716 / 1783 |
| nimble-v3_9b-q8_0 | 4991 | 0.589 [0.552, 0.620] | 0.144 [0.117, 0.171] | 2850 | yes | 172 / 1424 / 1254 |
| nimble_9b-bf16 | 6556 | 0.608 [0.567, 0.645] | 0.231 [0.210, 0.255] | 3699 | yes | 382 / 1673 / 1644 |
| clef-flash_9b | 6937 | 0.559 [0.520, 0.594] | 0.057 [0.044, 0.072] | 3851 | yes | 120 / 1982 / 1749 |
| tev1_4b-q8_0 | 3690 | 0.482 [0.454, 0.508] | 0.076 [0.066, 0.086] | 1778 | yes | 136 / 892 / 750 |
| d1_3b-q8_0 | 5172 | 0.609 [0.577, 0.639] | 0.087 [0.075, 0.099] | 3122 | yes | 300 / 1469 / 1353 |
| d3_2b-q8_0 | 1929 | 0.369 [0.321, 0.413] | 0.061 [0.042, 0.079] | 695 | yes | 107 / 313 / 275 |
| tev1_0.8b-q8_0 | 324 | 0.083 [0.045, 0.120] | 0.040 [0.020, 0.064] | 27 | yes | 13 / 10 / 4 |
| d1-omni_600m-q8_0 | 6709 | 0.732 [0.707, 0.753] | 0.516 [0.482, 0.551] | 4303 | yes | 1072 / 1560 / 1671 |
| laya_latest | 1150 | 0.505 [0.376, 0.592] | 0.185 [0.157, 0.210] | 544 | yes | 102 / 214 / 228 |
| heuristic (baseline) | 1 | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0 | no | 0 / 0 / 0 |
| lexical (baseline) | 0 | n/a | n/a | 0 | no | 0 / 0 / 0 |
| embedding (baseline) | 0 | n/a | n/a | 0 | no | 0 / 0 / 0 |
| gold_view (gold-backed ceiling) | 5200 | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0 | no | 0 / 0 / 0 |
| hf (baseline) | 1079 | 0.674 [0.641, 0.704] | 0.475 [0.429, 0.521] | 623 | yes | 562 / 50 / 11 |

Stop rule: gold_view (gold-backed ceiling) has 0 conflicting answers, 0 of them UNSAT, and contradiction detected: no (it must be 0, 0 and no).

#### Calibration (relations and bindings)

Brier is summed over the options (range 0 to 2). Calibration is copied from each cell's score_decisions, not rescored; groups and readings have no probabilities here.

| Run (calibration) | Relation Brier | Relation ECE | Binding Brier | Binding ECE |
|---|---|---|---|---|
| d3_27b-q8_0 | 0.288 | 0.144 | 0.075 | 0.091 |
| d3_9b-q8_0 | 0.573 | 0.051 | 0.105 | 0.148 |
| pplx-decider_27b | 0.537 | 0.061 | 0.098 | 0.147 |
| kev_9b-bf16 | 0.687 | 0.045 | 0.172 | 0.172 |
| d3_4b-q8_0 | 0.652 | 0.043 | 0.088 | 0.113 |
| clef_27b-q8_0 | 0.608 | 0.062 | 0.104 | 0.143 |
| nimble-v3_9b-q8_0 | 0.718 | 0.128 | 0.097 | 0.048 |
| nimble_9b-bf16 | 0.728 | 0.175 | 0.060 | 0.050 |
| clef-flash_9b | 0.721 | 0.051 | 0.127 | 0.162 |
| tev1_4b-q8_0 | 0.807 | 0.267 | 0.701 | 0.298 |
| d1_3b-q8_0 | 0.778 | 0.056 | 0.140 | 0.183 |
| d3_2b-q8_0 | 0.806 | 0.193 | 0.290 | 0.157 |
| tev1_0.8b-q8_0 | 1.120 | 0.478 | 1.029 | 0.687 |
| d1-omni_600m-q8_0 | 0.833 | 0.136 | 1.030 | 0.630 |
| laya_latest | 0.904 | 0.229 | 0.857 | 0.529 |
| heuristic (baseline) | n/a | n/a | n/a | n/a |
| lexical (baseline) | n/a | n/a | n/a | n/a |
| embedding (baseline) | n/a | n/a | n/a | n/a |
| gold_view (gold-backed ceiling) | n/a | n/a | n/a | n/a |
| hf (baseline) | n/a | n/a | n/a | n/a |
#### Binding detection

Does each run's own probability of an unresolved binding separate the unresolved items from the bound ones? AUROC is the Mann-Whitney statistic (a tied pair counts one half); average precision is the step-wise area under the precision-recall curve. Both have league-bootstrap 95% intervals over the same draws. Flagged counts items predicted unresolved (argmax of the two probabilities). MCC is the leaderboard's binding point. Runs without probabilities (the baselines and the gold_view ceiling) have no AUROC.

D3 27B's probabilities rank unresolved bindings above bound ones (AUROC 0.717 [0.670, 0.766]), but its decisions flag 0 (MCC 0.000): the ranking signal is not used at its decision threshold.
The same pattern (AUROC interval above 0.5, no flagged items) holds for d3_9b-q8_0, pplx-decider_27b.

| Run | Items | Unresolved (prevalence) | Flagged | Binding MCC | AUROC (95%) | Average precision (95%) |
|---|---|---|---|---|---|---|
| d3_27b-q8_0 | 3348 | 92 (0.0275) | 0 | 0.000 | 0.717 [0.670, 0.766] | 0.067 [0.048, 0.106] |
| d3_9b-q8_0 | 3348 | 92 (0.0275) | 0 | 0.000 | 0.670 [0.619, 0.723] | 0.076 [0.053, 0.138] |
| pplx-decider_27b | 3348 | 92 (0.0275) | 0 | 0.000 | 0.762 [0.710, 0.815] | 0.088 [0.069, 0.124] |
| kev_9b-bf16 | 3348 | 92 (0.0275) | 116 | -0.012 | 0.486 [0.435, 0.539] | 0.025 [0.022, 0.031] |
| d3_4b-q8_0 | 3348 | 92 (0.0275) | 0 | 0.000 | 0.452 [0.389, 0.510] | 0.028 [0.024, 0.036] |
| clef_27b-q8_0 | 3348 | 92 (0.0275) | 47 | 0.213 | 0.875 [0.845, 0.901] | 0.180 [0.111, 0.282] |
| nimble-v3_9b-q8_0 | 3348 | 92 (0.0275) | 73 | -0.025 | 0.357 [0.304, 0.411] | 0.020 [0.018, 0.022] |
| nimble_9b-bf16 | 3348 | 92 (0.0275) | 2 | 0.145 | 0.707 [0.650, 0.762] | 0.131 [0.062, 0.232] |
| clef-flash_9b | 3348 | 92 (0.0275) | 35 | 0.019 | 0.509 [0.468, 0.557] | 0.036 [0.029, 0.056] |
| tev1_4b-q8_0 | 3348 | 92 (0.0275) | 2280 | 0.009 | 0.448 [0.401, 0.497] | 0.023 [0.021, 0.027] |
| d1_3b-q8_0 | 3348 | 92 (0.0275) | 48 | 0.072 | 0.786 [0.757, 0.818] | 0.091 [0.063, 0.164] |
| d3_2b-q8_0 | 3348 | 92 (0.0275) | 478 | -0.022 | 0.328 [0.249, 0.408] | 0.022 [0.017, 0.040] |
| tev1_0.8b-q8_0 | 3348 | 92 (0.0275) | 3322 | -0.027 | 0.522 [0.461, 0.582] | 0.067 [0.036, 0.127] |
| d1-omni_600m-q8_0 | 3348 | 92 (0.0275) | 3127 | 0.045 | 0.637 [0.589, 0.686] | 0.036 [0.032, 0.043] |
| laya_latest | 3348 | 92 (0.0275) | 2927 | -0.036 | 0.472 [0.417, 0.526] | 0.029 [0.024, 0.038] |
| heuristic (baseline) | 3348 | 92 (0.0275) | 0 | 0.000 | n/a | n/a |
| lexical (baseline) | 3348 | 92 (0.0275) | 0 | 0.000 | n/a | n/a |
| embedding (baseline) | 3348 | 92 (0.0275) | 0 | 0.000 | n/a | n/a |
| gold_view (gold-backed ceiling) | 3348 | 92 (0.0275) | 92 | 1.000 | n/a | n/a |
| hf (baseline) | 3348 | 92 (0.0275) | 37 | -0.018 | n/a | n/a |


#### Item audit (candidates for review)

These flags are candidates for review only. They are not label errors: a flag says the items look hard or inconsistent with the models, and a reviewer decides. `all_wrong`: all 15 models are wrong. `weak-over-strong (inverted)`: within each (op, gold class, stratum) cell each model's correctness is centred on its cell mean; the weak half's mean residual is above the strong half's and the residuals' point-biserial correlation with the op's own MCC is below 0. The halves rank the models by each op's MCC: top 7 strong, bottom 7 weak, rank 8 in neither.

2962 of 7000 items are flagged: 423 all wrong, 2875 weak-over-strong (inverted), of which 2539 are not all wrong. The list is in [flagged.jsonl](../docs/audits/v0.1.1-item-audit/flagged.jsonl).

The inverted rule is pre-registered and is not changed here. An all-wrong item tends to satisfy it mechanically, because each cell mean includes the item itself: 336 of the 423 all-wrong items also carry the inverted flag. So the overlap of the two flags is not evidence, and the null comparison below inherits it.

| Op | Items | Flagged | All wrong | Weak-over-strong (inverted) | Inverted, not all wrong |
|---|---|---|---|---|---|
| decide_relation | 6000 | 2422 | 196 | 2422 | 2226 |
| decide_group | 1000 | 540 | 227 | 453 | 313 |

decide_relation halves: strong d3_27b-q8_0, pplx-decider_27b, d3_9b-q8_0, d3_4b-q8_0, clef_27b-q8_0, kev_9b-bf16, tev1_4b-q8_0; rank 8 nimble-v3_9b-q8_0; weak nimble_9b-bf16, clef-flash_9b, d3_2b-q8_0, d1_3b-q8_0, d1-omni_600m-q8_0, tev1_0.8b-q8_0, laya_latest.

decide_group halves: strong d3_27b-q8_0, kev_9b-bf16, nimble-v3_9b-q8_0, clef-flash_9b, d3_9b-q8_0, nimble_9b-bf16, d3_4b-q8_0; rank 8 d1_3b-q8_0; weak clef_27b-q8_0, tev1_0.8b-q8_0, pplx-decider_27b, tev1_4b-q8_0, d1-omni_600m-q8_0, laya_latest, d3_2b-q8_0.

Null rate: under 1000 random 7-vs-7 splits of the 15 models (seed 20261101), the share of items flagged weak-over-strong (inverted) is mean 0.2284 (95th percentile 0.3210), against an observed 0.4107 over the same 7000 items.

Judging: the item audit used model judges (2 Haiku 5.5 judge sets), not people. 550 of 2962 flagged items were judged (PREREG.md: all 423 all_wrong items; 127 inverted (not all_wrong) items, stratified by op x gold class x stratum; 50 unflagged controls, stratified the same way over the unflagged items): suspect label 13, hard but valid 427, unclear 110. Pre-registered result: 13 of 550 judged flagged items are suspect labels. This result is fragile: 9 of the 13 are group items from one judge slice, where one judge gave the gold bounds under the kind label `none`, and one judge instance in that slice agreed with gold on only 24 of 100 items; with the discarded first answers for that slice the count is 5. Under an exploratory bound-based reading, 4 candidates remain (g00453, g00551, r03602, r04933). None is a confirmed label error; no score changes. 0 of 50 controls were labelled suspect (exact two-sided 95% interval 0% to 7.1%; one-sided upper bound 5.8%; only 5 controls are groups). Flags and suspect labels are candidates for review, not confirmed label errors. Suspect labels are not adjudicated, and they remain in the scores.

#### Robustness: option order, run to run and backend

Deltas are headline changes against the published run. The option-shuffle delta is one pass with the answer options in a seeded random order (one seed, 20261101). Run to run is the largest change of the r2 and r3 repeats. Backend is the Vulkan pass of d3:9b. Flip rate is the share of the 10,716 decision rows whose label differs. The published run is the r1 reference in results/v0.1.0 (its predictions and cell are not copied here).

| Model | Option shuffle delta | Shuffle flip rate | Run to run max abs delta | Run to run max flip rate | Vulkan delta | Vulkan flip rate |
|---|---|---|---|---|---|---|
| d3_27b-q8_0 | +0.0073 | 4.8% | n/a | n/a | n/a | n/a |
| d3_9b-q8_0 | +0.0083 | 6.7% | 0.0000 | 0.0% | +0.0040 | 1.3% |
| pplx-decider_27b | +0.0220 | 12.0% | n/a | n/a | n/a | n/a |
| kev_9b-bf16 | +0.0041 | 11.8% | 0.0004 | 0.1% | n/a | n/a |
| d3_4b-q8_0 | +0.0046 | 10.6% | 0.0000 | 0.0% | n/a | n/a |
| clef_27b-q8_0 | +0.0000 | 0.7% | n/a | n/a | n/a | n/a |
| nimble-v3_9b-q8_0 | +0.0012 | 18.1% | n/a | n/a | n/a | n/a |
| nimble_9b-bf16 | -0.0098 | 17.8% | n/a | n/a | n/a | n/a |
| clef-flash_9b | +0.0000 | 1.0% | n/a | n/a | n/a | n/a |
| tev1_4b-q8_0 | +0.0206 | 22.7% | n/a | n/a | n/a | n/a |
| d1_3b-q8_0 | -0.0350 | 20.3% | n/a | n/a | n/a | n/a |
| d3_2b-q8_0 | +0.0091 | 8.5% | n/a | n/a | n/a | n/a |
| tev1_0.8b-q8_0 | +0.0022 | 6.1% | n/a | n/a | n/a | n/a |
| d1-omni_600m-q8_0 | -0.0073 | 27.9% | n/a | n/a | n/a | n/a |
| laya_latest | +0.0029 | 4.7% | n/a | n/a | n/a | n/a |

One option-shuffle pass per model (15 models) moves the headline by up to ±0.0350 (median 0.0073).
Run to run, for the 3 repeated models (d3_9b-q8_0, kev_9b-bf16, d3_4b-q8_0), the r2 and r3 repeats were bit-identical for 2 (d3_9b-q8_0, d3_4b-q8_0) and differed by at most ±0.0004 for kev_9b-bf16.
Over the 3 models with run-to-run repeats (presentation_matched), the shuffle moves the headline by at most ±0.0083, against a run-to-run change of at most ±0.0004: larger than run noise.
Backend: the Vulkan pass moves the headline by ±0.0040 (1 model).

Disclosure: the first d3:9b repeat attempt ran on Ollama's Vulkan backend by mistake (runs/20261101-repeat); the CUDA repeats r2 and r3 were run afterwards in a separate folder (runs/20261101-cuda-repeat). The Vulkan passes are kept as a backend diagnostic, and the headline is the CUDA run.

- Diagnostic only: the leaderboard headline remains the single pre-registered run; repeats use the pre-registered command on CUDA (Ollama 0.40.11, both A6000s).
- shuffle = one pass with answer options presented in a seeded random order (MB_S1_OPTION_SEED=20261101); same model, weights and prompts otherwise.
- vulkan = the d3:9b repeats run on Ollama's Vulkan backend instead of CUDA (two passes, r2 and r3, which must agree); reported as a backend-sensitivity diagnostic.

#### Cost

Hardware: 2× NVIDIA RTX A6000 (48 GB), Ollama 0.40.11 (CUDA), one server per GPU, OLLAMA_NUM_PARALLEL=2. From the single published run per model; build-phase wall clock, includes harness overhead.

Decisions are the cell's decision count, one per decision item (a reading's base and alt rows count once). Decisions per second is decisions over the build wall seconds; build minutes are the same time over 60.

| Model | Params | Quant | Size (GB) | Build (min) | Decisions | Decisions/s |
|---|---|---|---|---|---|---|
| d3_27b-q8_0 | 27B | q8_0 | 29.53 | 74.6 | 10532 | 2.35 |
| d3_9b-q8_0 | 9B | q8_0 | 10.45 | 21.9 | 10532 | 8.00 |
| pplx-decider_27b | 27B | n/a | 17.48 | 74.4 | 10532 | 2.36 |
| kev_9b-bf16 | 9B | bf16 | 17.92 | 34.9 | 10532 | 5.03 |
| d3_4b-q8_0 | 4B | q8_0 | 5.83 | 15.4 | 10532 | 11.41 |
| clef_27b-q8_0 | 27B | q8_0 | 29.66 | 78.7 | 10532 | 2.23 |
| nimble-v3_9b-q8_0 | 9B | q8_0 | 9.53 | 28.3 | 10532 | 6.20 |
| nimble_9b-bf16 | 9B | bf16 | 17.92 | 27.7 | 10532 | 6.35 |
| clef-flash_9b | 9B | n/a | 10.58 | 26.4 | 10532 | 6.65 |
| tev1_4b-q8_0 | 4B | q8_0 | 4.61 | 17.2 | 10532 | 10.19 |
| d1_3b-q8_0 | 3B | q8_0 | 3.73 | 8.5 | 10532 | 20.63 |
| d3_2b-q8_0 | 2B | q8_0 | 3.22 | 6.9 | 10532 | 25.34 |
| tev1_0.8b-q8_0 | 0.8B | q8_0 | 0.83 | 6.6 | 10532 | 26.80 |
| d1-omni_600m-q8_0 | 600M | q8_0 | 0.85 | 4.7 | 10532 | 37.35 |
| laya_latest | n/a | n/a | 0.84 | 2.4 | 10532 | 72.46 |

<!-- analysis:end -->

## Parts and the secondary binding score

- **Relations:** D3 27B 0.788 [0.766, 0.814] leads. Pplx Decider 27B is next at 0.512, then D3 9B at 0.462.
- **Groups:** D3 27B 0.249 [0.224, 0.273] leads. Kev 9B 0.147 [0.121, 0.175] is second, and Nimble-v3 9B 0.126 [0.106, 0.147] third.
- **Bindings (secondary):**
  - Clef 27B has the highest point, 0.213 [0.108, 0.318].
  - Four models never flag an ambiguous proposition on test: D3 27B, D3 9B, D3 4B and Pplx Decider 27B. Their binding MCC is 0 by
    convention, so their intervals are degenerate.
  - D3 9B scored 0.293 on dev and 0.000 on test. It flags no binding question as ambiguous on test. The cause is not identified.
  - Nimble 9B's 0.145 [0.000, 0.258] comes from two flags, both correct.
  - D1-Omni 600M's 0.045 [0.038, 0.052] has an interval above 0, but it comes from 3,127 flags with a precision of about 0.03.

## Private-split replication

The private split (`mentat_hard_private`, test set revision 3.1, sealed) has 3,228 binding outcomes, 6,000 relation questions and 1,178 group
questions (1,000 group and 178 reading questions). It is built by the same generator as the public test, from a seed that is not
recorded anywhere in the repository. The split and its run directories are not published. Only the aggregates in
[docs/results/mentat-hard-private-20261030.json](../docs/results/mentat-hard-private-20261030.json) and its CI file are, and this
folder reuses only those aggregates. The private results include the same 15 models and the rules baseline, and the gold_view ceiling.
Lexical, embedding and hf were not run on the private split.

| Run | Private headline [95% CI] | Private relation MCC [95% CI] | Private group MCC [95% CI] | Private binding MCC, secondary [95% CI] | Public headline (test) | Private minus public |
|---|---|---|---|---|---|---|
| D3 27B | 0.525 [0.504, 0.546] | 0.777 [0.757, 0.798] | 0.272 [0.237, 0.312] | 0.000 [0.000, 0.000] | 0.519 | +0.006 |
| D3 9B | 0.273 [0.257, 0.293] | 0.441 [0.420, 0.467] | 0.104 [0.084, 0.127] | -0.007 [-0.010, 0.000] | 0.275 | -0.002 |
| Pplx Decider 27B | 0.260 [0.246, 0.277] | 0.497 [0.478, 0.520] | 0.023 [-0.004, 0.050] | 0.000 [0.000, 0.000] | 0.253 | +0.007 |
| Kev 9B | 0.215 [0.197, 0.233] | 0.309 [0.287, 0.335] | 0.121 [0.097, 0.145] | -0.011 [-0.035, 0.033] | 0.236 | -0.021 |
| D3 4B | 0.227 [0.213, 0.241] | 0.376 [0.353, 0.401] | 0.078 [0.051, 0.106] | 0.000 [0.000, 0.000] | 0.233 | -0.006 |
| Clef 27B | 0.223 [0.204, 0.244] | 0.388 [0.364, 0.415] | 0.059 [0.036, 0.081] | 0.094 [0.003, 0.197] | 0.207 | +0.016 |
| Nimble-v3 9B | 0.194 [0.174, 0.216] | 0.255 [0.220, 0.290] | 0.132 [0.107, 0.156] | -0.026 [-0.030, -0.021] | 0.201 | -0.008 |
| Nimble 9B | 0.165 [0.153, 0.179] | 0.278 [0.263, 0.292] | 0.053 [0.032, 0.074] | 0.000 [0.000, 0.000] | 0.178 | -0.013 |
| Clef-Flash 9B | 0.147 [0.134, 0.159] | 0.231 [0.210, 0.251] | 0.062 [0.044, 0.078] | -0.017 [-0.021, -0.014] | 0.172 | -0.025 |
| TEV1 4B | 0.129 [0.105, 0.153] | 0.283 [0.253, 0.316] | -0.024 [-0.056, 0.010] | -0.007 [-0.042, 0.025] | 0.144 | -0.015 |
| D1 3B | 0.059 [0.048, 0.072] | 0.093 [0.074, 0.115] | 0.024 [0.004, 0.045] | 0.072 [-0.017, 0.173] | 0.071 | -0.012 |
| D3 2B | 0.065 [0.043, 0.088] | 0.161 [0.137, 0.188] | -0.031 [-0.064, 0.002] | -0.024 [-0.055, 0.012] | 0.055 | +0.010 |
| TEV1 0.8B | 0.003 [-0.001, 0.008] | 0.006 [-0.001, 0.015] | 0.000 [0.000, 0.000] | 0.017 [0.014, 0.021] | -0.000 | +0.003 |
| D1-Omni 600M | 0.005 [-0.004, 0.015] | 0.011 [-0.001, 0.024] | -0.001 [-0.014, 0.011] | 0.039 [0.024, 0.050] | -0.003 | +0.008 |
| Laya 0.4B | -0.008 [-0.026, 0.011] | 0.015 [-0.009, 0.037] | -0.030 [-0.061, 0.004] | 0.038 [0.005, 0.062] | -0.020 | +0.012 |
| Rules (heuristic child) | 0.139 [0.115, 0.159] | 0.248 [0.226, 0.273] | 0.030 [0.000, 0.054] | 0.000 [0.000, 0.000] | 0.140 | -0.001 |
| gold_view (perfect curated answers) | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | — | — |

- **Headlines move little.** Every model's headline is within 0.025 of its public value. The largest change is Clef-Flash 9B's
  (-0.025). The headline is a mean, so relation and group changes partly offset each other: for D3 27B, relation is -0.011 and group +0.024.
- **The top of the table carries over.** D3 27B is first on the headline in both splits, and its private interval (0.504 to 0.546) lies
  above every other model's (the highest upper bound among them is 0.293). Its gap to every other run excludes 0 in the private
  split too. The smallest gap is to D3 9B, 0.252 [0.229, 0.276] (0.244 publicly).
- **The top three keep their order.** D3 27B, D3 9B and Pplx Decider 27B are the top three in both splits.
- **The middle reorders, within overlapping intervals.** Across the 16 runs compared, the two orders have a Spearman correlation of
  0.98. Of the 120 pairs, 97 have disjoint intervals in both splits, and none of those changes order.
- **Some public statements do not carry over.** Seven models have headline intervals above the rules interval in the private split
  (D3 27B, D3 9B, Pplx Decider 27B, D3 4B, Clef 27B, Kev 9B and Nimble-v3 9B). Nimble 9B, Clef-Flash 9B and TEV1 4B now overlap it. Laya's
  headline interval includes 0 privately ([-0.026, 0.011]).
- **Group second place is not resolved.** Privately, second place is Nimble-v3 9B (0.132 [0.107, 0.156]), with Kev 9B third
  (0.121 [0.097, 0.145]). Publicly it is Kev 9B, then Nimble-v3 9B. The two intervals overlap in each split.
- **Binding is the least stable score.** Nimble 9B moves from 0.145 to 0.000, and Clef 27B from 0.213 to 0.094.

## Diagnostics

These are not scores. They explain the scores.

### Reading MCC

Diagnostic. The reading block has 184 reading questions, 368 items.

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
| TEV1 0.8B | 0.000 [0.000, 0.000] |
| D1-Omni 600M | 0.000 [0.000, 0.000] |
| Laya 0.4B | 0.000 [0.000, 0.000] |
| *Rules (heuristic child)* | *-0.016 [-0.104, 0.082]* |
| *Embedding* | *0.000 [0.000, 0.000]* |
| *Lexical* | *0.000 [0.000, 0.000]* |
| *gold_view (perfect curated answers)* | *1.000 [1.000, 1.000]* |
| *hf pipeline (Qwen2.5-1.5B-Instruct)* | *0.000 [0.000, 0.000]* |

### Held-out and seen relations

The held-out split is 2,332 of the 6,000 relations (38.9%), as pre-registered. The held-out interval comes from the bootstrap; the seen
figure is a point only.

| Run | Seen relation MCC (point) | Held-out relation MCC [95% CI] | Seen minus held-out (point) |
|---|---|---|---|
| D3 27B | 0.787 | 0.787 [0.760, 0.815] | 0.000 |
| D3 9B | 0.504 | 0.399 [0.373, 0.427] | 0.105 |
| Pplx Decider 27B | 0.514 | 0.506 [0.475, 0.540] | 0.009 |
| Kev 9B | 0.374 | 0.253 [0.222, 0.285] | 0.121 |
| D3 4B | 0.434 | 0.331 [0.298, 0.365] | 0.103 |
| Clef 27B | 0.427 | 0.337 [0.298, 0.380] | 0.091 |
| Nimble-v3 9B | 0.298 | 0.244 [0.210, 0.281] | 0.053 |
| Nimble 9B | 0.288 | 0.246 [0.219, 0.271] | 0.042 |
| Clef-Flash 9B | 0.255 | 0.245 [0.216, 0.271] | 0.010 |
| TEV1 4B | 0.340 | 0.240 [0.211, 0.270] | 0.100 |
| D1 3B | 0.112 | 0.124 [0.105, 0.147] | -0.012 |
| D3 2B | 0.200 | 0.135 [0.111, 0.157] | 0.065 |
| TEV1 0.8B | 0.001 | -0.003 [-0.022, 0.018] | 0.004 |
| D1-Omni 600M | 0.025 | 0.006 [-0.016, 0.024] | 0.020 |
| Laya 0.4B | -0.012 | -0.021 [-0.048, 0.020] | 0.010 |
| Rules (heuristic child) | 0.282 | 0.205 [0.184, 0.226] | 0.077 |
| hf pipeline (Qwen2.5-1.5B-Instruct) | 0.039 | 0.027 [0.007, 0.044] | 0.012 |

Seven of the 15 models lose 0.05 or more of relation MCC on held-out wording: D3 9B, Kev 9B, D3 4B, TEV1 4B, Clef 27B, D3 2B and Nimble-v3 9B.
D3 27B loses none. Held-out wording is not a uniform penalty: D1 3B scores slightly higher on the held-out split. The differences have no
interval, so they are descriptive.

### Relation accuracy by gold depth

Accuracy on the relation rows, grouped by the gold inference depth of each label (`gold/suite_labels.jsonl`). Raw decisions, scored as
answered, with missing answers counted as wrong. Descriptive only, and not used for any score. The buckets differ in class mix, so the
accuracies are not comparable to MCC. Depth 0 holds 800 EQUIV, 500 EXCLUDES, 242 IMPLIES and 228 IMPLIED_BY. Depth 1 has no EQUIV. The NONE
column is the only one with NONE rows, and a model that names few relations can score high there. The 4+ bucket has six rows, so one row
moves its accuracy by 17 points.

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
| Laya 0.4B | 0.051 | 0.059 | 0.039 | 0.021 | 0.167 | 0.801 | 0.234 |
| *Rules (heuristic child)* | 0.288 | 0.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.335 |
| *Lexical* | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.250 |
| *Embedding* | 0.006 | 0.000 | 0.000 | 0.000 | 0.000 | 0.995 | 0.251 |
| *hf pipeline (Qwen2.5-1.5B-Instruct)* | 0.021 | 0.013 | 0.001 | 0.000 | 0.000 | 0.987 | 0.255 |
| *gold_view (perfect curated answers)* | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

For D3 27B, accuracy is high at depths 0 and 1 (0.915 and 0.920) and falls to 0.648 at depth 3. Most models fall with depth. Clef-Flash 9B is
nearly flat. The hf pipeline answers NONE for nearly every relation (0.987 on NONE rows), so its named-relation accuracy is near zero.
Laya's NONE column is mostly abstentions, not judgement (see [Limits](#limits-and-disclosures), item 1).

### Q_N and why it is not the ranking

Q_N is the end-to-end exam score: a model's decisions are compiled into a base, and 200 queries are answered from it. Q_N is reported for
the repaired base. Strict Q_N gives 0 to any build whose base had to be repaired, and keeps the Q_N of a build whose base was not repaired.

| Run | Q_N, repaired base [95% CI] | Strict Q_N |
|---|---|---|
| D3 27B | 24.98 [19.92, 31.59] | 0.00 |
| D3 9B | 18.82 [14.80, 22.97] | 0.00 |
| Pplx Decider 27B | 14.57 [9.84, 20.01] | 0.00 |
| Kev 9B | 21.95 [16.67, 27.11] | 0.00 |
| D3 4B | 17.55 [12.90, 22.15] | 0.00 |
| Clef 27B | 15.52 [11.99, 19.20] | 0.00 |
| Nimble-v3 9B | 17.19 [13.09, 22.25] | 0.00 |
| Nimble 9B | 12.88 [9.98, 15.75] | 0.00 |
| Clef-Flash 9B | 16.40 [13.20, 19.69] | 0.00 |
| TEV1 4B | 21.52 [15.18, 28.27] | 0.00 |
| D1 3B | 15.60 [12.13, 19.19] | 0.00 |
| D3 2B | 26.34 [21.07, 31.94] | 0.00 |
| TEV1 0.8B | 17.03 [14.58, 19.67] | 17.03 |
| D1-Omni 600M | 14.21 [9.05, 19.50] | 0.00 |
| Laya 0.4B | 18.51 [14.97, 21.83] | 0.00 |
| *Empty* | *16.88* | *16.88* |
| *Rules (heuristic child)* | *32.22 [28.55, 36.13]* | *32.22* |
| *Lexical* | *32.22 [28.57, 36.11]* | *32.22* |
| *Embedding* | *31.38 [27.96, 35.18]* | *31.38* |
| *gold_view (perfect curated answers)* | *100.00 [100.00, 100.00]* | *100.00* |
| *Oracle* | *100.00* | *100.00* |
| *hf pipeline (Qwen2.5-1.5B-Instruct)* | *9.06 [5.69, 12.74]* | *0.00* |

- **Strict Q_N is 0 for 14 of the 15 models.** The exception is TEV1 0.8B, whose base was not repaired, so its strict Q_N equals its repaired Q_N.
- **The Q_N ranking is not the decision ranking.** The CPU baselines (lexical 32.2, rules 32.2, embedding 31.4) have a higher Q_N than every
  model. Among the models, D3 2B (26.3) has a higher point than D3 27B (25.0), and the intervals overlap.
- The rules baseline's Q_N gap to D3 27B is -7.24 [-12.98, -1.17] (reference minus run).
- Repair drops change the repaired base that Q_N scores. They do not change any decision, so they are not in the headline. The build reports
  the groups and relations it dropped:

| Run | Groups dropped / committed (% of committed) | Relations dropped (of 6,000 asked) |
|---|---|---|
| D3 27B | 92 / 1,776 (5.18%) | 52 |
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
| TEV1 0.8B | 0 / 0 (0.00%) | 0 |
| D1-Omni 600M | 373 / 1,982 (18.82%) | 0 |
| Laya 0.4B | 10 / 156 (6.41%) | 0 |
| hf pipeline (Qwen2.5-1.5B-Instruct) | 228 / 1,000 (22.80%) | 0 |

### D3 family size curve

The pre-registered comparison. The headline rises at every step: from 2B to 4B it adds 0.18, from 4B to 9B 0.04, and from 9B to 27B 0.24,
the largest step, mostly from relations and groups. Dev gives the same ordering.

| Model | Test headline [95% CI] | Test relation MCC | Test group MCC | Dev headline |
|---|---|---|---|---|
| D3 2B | 0.055 [0.042, 0.069] | 0.171 | -0.060 | 0.082 |
| D3 4B | 0.233 [0.216, 0.251] | 0.393 | 0.072 | 0.237 |
| D3 9B | 0.275 [0.251, 0.300] | 0.462 | 0.088 | 0.294 |
| D3 27B | 0.519 [0.497, 0.541] | 0.788 | 0.249 | 0.504 |

### Dev and test

Dev headlines are the pre-registered values, as stored in the prereg (4 decimals). The top model stays the same, and the order of the other
models is unchanged except for one adjacent pair: D1 3B moves above D3 2B on test (dev had D3 2B first, by 0.0035). Every change is within 0.03.

| Run | Dev headline (prereg, 4 dp) | Test headline | Test minus dev (unrounded) |
|---|---|---|---|
| D3 27B | 0.5036 | 0.519 | +0.0149 |
| D3 9B | 0.2938 | 0.275 | -0.0190 |
| Pplx Decider 27B | 0.2638 | 0.253 | -0.0111 |
| Kev 9B | 0.2533 | 0.236 | -0.0174 |
| D3 4B | 0.2375 | 0.233 | -0.0048 |
| Clef 27B | 0.2044 | 0.207 | +0.0024 |
| Nimble-v3 9B | 0.1914 | 0.201 | +0.0100 |
| Nimble 9B | 0.1813 | 0.178 | -0.0030 |
| Clef-Flash 9B | 0.1735 | 0.172 | -0.0018 |
| TEV1 4B | 0.1512 | 0.144 | -0.0072 |
| D1 3B | 0.0785 | 0.071 | -0.0075 |
| D3 2B | 0.0820 | 0.055 | -0.0268 |
| TEV1 0.8B | 0.0063 | -0.000 | -0.0064 |
| D1-Omni 600M | -0.0006 | -0.003 | -0.0026 |
| Laya 0.4B | -0.0052 | -0.020 | -0.0149 |
| Rules (heuristic child) | 0.1252 | 0.140 | +0.0153 |

## Methodology

### Fixture

- **Test:** `mentat_hard_test`, test set revision 3.1 (registry version 3), content hash
  `7b408459538d7420e808d5d81e71916c57aee78f459a6b8c0905e48e43c8e6ab`. It has 18 leagues, 1,674 markets, 3,348 outcomes, and 200 queries.
  Its questions: 6,000 relations (1,500 NONE; 2,332 held-out), 1,184 group questions (1,000 non-reading and 184 reading), and 3,348 bindings.
  Each model cell has 10,716 scored items, since a reading question is two items. Lexical and embedding answer only the relations and bindings
  (9,348 decisions), so their 1,000 group questions are unanswered.
- **Private:** `mentat_hard_private`, test set revision 3.1, sealed, content hash
  `1253bb5dfee2c02a34ded7e659a0158dc8996be9660d7be0ee14ffba19077263`. 3,228 outcomes, 10,406 candidates, 200 queries. Aggregates only.
- **Dev:** `mentat_hard_dev`, test set revision 3.1, content hash `218003c63135ace753a1b2449ee002dc2813c7e99bed8c0956fd5a52187c3964`. It sets the
  relation floors, the headline choice and the reference.
- Test files and their hashes (the same per-file SHA-256 values are pinned by the release manifest, and the builder checks them):

| File (fixtures/mentat_hard_test/) | Bytes | SHA-256 |
|---|---|---|
| `candidates.jsonl` | 982,408 | `30856a412260fa6ee751cd9777947b28988f90eeca0a68ee42089d2f19490c93` |
| `corpus.jsonl` | 5,050,844 | `d83b9f44bd526c680c67ea4546fe6bedd1014c811dc984b0c47767568b5694e5` |
| `fixture.json` | 284 | `5fdacb61c156c9b7c2bad5bff985c5600f5f25d3233bb6d676ddbe3b934424d8` |
| `gold/answers.jsonl` | 385,787 | `5b2f1118b39b15c03e6b6829d88e6d5a1e20a887812ef2a9dd389597ca33d1e4` |
| `gold/bindings.jsonl` | 631,292 | `ad7e616f39d104900b966b621cfd640079a58b609f1195d3aa4af118c2cbaa18` |
| `gold/meta.jsonl` | 774,266 | `5fba201e911cf89340eaff14d6cec82f057c658da763edb82584e6e4ea2a701d` |
| `gold/provenance.jsonl` | 557,442 | `b215fa8f5ee4e898836926655fdf4a4acfd6cbc1bd1ba7f93cbe7ce925aa2090` |
| `gold/query_design.jsonl` | 35,576 | `b339f56ff91ad7353f5176967ec9b64fd5f97633f94cdddfe5907283b20c9b6e` |
| `gold/suite_labels.jsonl` | 1,469,338 | `8fa691d3efec7424a8c12b78cec5a2f67131b4aefc172311a8a8217eafa0e46f` |
| `gold/summary.json` | 800 | `b1238b28d183d994d774f6dea8590dafe4386e8cfb2e8dc6471d0b20f2f73cd0` |
| `gold/theory.json` | 536,876 | `51077ca39805cd4079f930c4fc35e23d3f10382c0f0b920476863726d748f40b` |
| `gold/usage.json` | 55 | `90d35a556ad3005b046494b9b32c2c0cd28932123ca2a5cb38f5f11761250234` |
| `gold/verdicts.jsonl` | 13,230 | `e7b8135f3ae3ec8e03f1e61c0f2e5d08cb1d50ddca1339387ce9d84f3727f376` |
| `gold/world.json` | 162,189 | `146fb16be67eb11b828868d159538490dc2fd1240a69cd017b41ffac1a8795da` |
| `queries.jsonl` | 567,766 | `f69424c23449030cc1c5a6e1c2a1ed085f1153ba0e940070c1c45a89e15e06ec` |

### Rule cards, child and runs

- **Rule cards** are in the relation, group and reading states, and not in the binding state (pre-registered). A card is a plain-English
  statement of the league's rules, rendered from the rule list (`corpus.jsonl` field `context`).
- **Child:** `systemone-choice` version 7, `prompt_sha256` `d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44`. One question per
  request. The child records card-driven abstentions in its stats.
- **Servers:** Ollama 0.40.11, two servers, one per NVIDIA RTX A6000, CUDA only, with `OLLAMA_NUM_PARALLEL=2` and `MB_S1_PARALLEL=2`.
- **Build flags:** `--top-k 24`, `--batch-size 128`, a build deadline of 5,400 s, and the per-model relation floor from dev. The cells also set
  `--local-timeout 600.0` and `--hello-timeout 900.0`, which the pre-registered command does not list. Every model cell completed with no
  item errors, no 500 retries and no refused instructions (child stats).
- **Code:** the test cells ran at commit `0bff47f` (a commit of the private development repository, not published here), a clean tree. The pre-registration names `9ad5f2c`. The two commits differ only by the two
  pre-registration files.
- **Models:** 15. Seven are new in round 3: D3 2B and 4B, D1 3B, D1-Omni 600M, Nimble-v3 9B, and TEV1 0.8B and 4B. Skipped: `d1:2b` and
  `d1-omni:2b`, because they need a newer Ollama (an owner decision), and `strands-decider:2b`, which runs only on MLX.
- **Baselines:** the pre-registered rules baseline (`heuristic`, the heuristic child), `lexical` and `embedding` (CPU), `empty` (no answers,
  Q_N only), `gold_view` (perfect curated answers, the ceiling), `oracle` (Q_N only, 100), and the hf pipeline (Qwen2.5-1.5B-Instruct, release
  gate 10).

### Relation floors

Each model has a relation floor from dev: the lowest `--relation-floor` at which the exact precision of its named relations, on suite labels,
is at least 0.8 (`tools/eval/floor_calibrate.py`). If no floor reaches 0.8, the floor is the largest float, and the model names no relation
under it (TEV1 0.8B and D1-Omni 600M). The floor is applied when the base is built, so it changes the repaired base scored by Q_N. The headline
and the parts score the raw decisions.

### Scoring

`python/mentat_bench/decision_scoring.py` scores each item once. Raw decisions are scored as answered. A missing answer counts as wrong and
lowers coverage. The relation class is the decision's relation. A group class is the canonical class of the answer's bounds, so a wrong k
is a wrong class; the decision's kind is also reported. Binding is bound or unresolved. Reading items are scored per reading. Each op reports MCC, macro-F1, accuracy, per-class precision
and recall, the confusion matrix, Brier and ECE, by stratum and by seen or held-out split. The predictions files in this folder are labelled by
this same code, and the builder rescores them to check the run's own score file.

### Intervals

- **League bootstrap:** 18 leagues are resampled with replacement, B = 2000 draws, from `random.Random("league_ci:0")`. Every run uses the same draws.
  Intervals are the 2.5th and 97.5th percentiles (numpy, linear) of the draws. Gaps are reference minus run, paired within each draw.
- **Scope:** the intervals cover item and league sampling only. Each model ran once, so run-to-run variation is not measured.
- **Reproduction:** the CI file reproduces byte for byte from `tools/eval/league_ci.py` over the 20 runs listed in the results document's Provenance.

### Pre-registration

The matrix, the headline rule and the reference were pre-registered in
[release/v0.1.0/record/prereg.md](../release/v0.1.0/record/prereg.md) before any test run. The post-hoc
changes were all decided on dev before the test runs (see [Limits](#limits-and-disclosures)).

## Limits and disclosures

These are the limits disclosed in the results documents, plus the release record's own.

1. **Laya's abstentions are scored as NONE answers.** Scoring is as pre-registered, and it is not rescored. An abstention is a NONE with no
   confidence. On test, Laya's child abstained for length on 4,981 of 6,000 relation questions (83%) and on 1,032 of 1,184 group questions.
   1,198 of its 1,202 correct NONE decisions are abstentions. Its relation MCC (-0.016) is computed with the abstentions as NONE. The same holds for its group MCC: its 1,032 group abstentions are empty group lists with null probabilities, which `decision_scoring` reads as the group class `none`. So its group MCC scores abstentions as answers and is not a judgment about its group reasoning. Its context
   is 512 tokens, so the pre-registration's own dev run already showed this (5,552 dev requests abstained for length, per the pre-registration).
2. **TEV1 0.8B and D1-Omni 600M are at chance.** The pre-registration says they answer, but at chance. Under their floors they name no relations.
   Their raw decisions do name relations: TEV1 0.8B names 324 (EXCLUDES 209, EQUIV 115), and D1-Omni 600M names 5,714. Their relation MCCs are near
   zero (TEV1 0.8B -0.000 [-0.009, 0.010]; D1-Omni 600M 0.015 [0.003, 0.027]).
3. **D3 9B's binding MCC fell from 0.293 on dev to 0.000 on test.** It is observed, and the cause is not identified. Binding questions carry no
   rule card, so the card change is not an explanation.
4. **One run per model on test.** The dev repeat of D3 9B (`runs/20261030-v31c7-dev` and `runs/20261030-v31c7-dev-rep`, on the other GPU) gave byte-identical decisions and scores, but there is no test repeat. The intervals cover item
   and league sampling only.
5. **Post-hoc changes, all decided on dev before any test run:** revision 3.1 (harder relations), the relation plus group headline, and child version 7
   (the audit fix). Each is an owner decision recorded in its pull request.
6. **Judges are models.** The answerability audits use two Haiku 5.5 judges, not people. The item audit's result is fragile (see the item audit in the analysis section): 9 of its 13 suspect labels come from one judge slice, and 4 remain under an exploratory bound-based reading. Both audits pass every part (bar 0.8). The revision 3.1 audit passed only in round 2: round 1 (child version 6) failed on reading decoys because the alt-reading instruction was ambiguous, and child version 7 fixed it before re-auditing the same items:
   [round 3 audit](../docs/audits/test-set-groups/) and [revision 3.1 audit](../docs/audits/test-set-relations/).
7. **Gate 7 (leakage) is MET only at the pinned commit `defed6a`**, on a clean tree. At any later commit it reads UNKNOWN, because the tool does
   not run the test there.
8. **Nimble-v3 9B is licensed CC-BY-NC-4.0**, which is non-commercial, as recorded in `children/ollama_models.json`.
9. **Skipped models:** `d1:2b` and `d1-omni:2b` need a newer Ollama (an owner decision), and `strands-decider:2b` runs only on MLX.
10. **Gold-informed candidates.** Candidate selection uses gold, so blocking is not tested. The data is a synthetic single domain, and the test gold is public.
11. **Card parameters are partly conditioned on gold** (M range, q, seed_min, chain order, winner bound a). Each is a bound the theory needs, not a value.
12. **Surface cues.** The gloss on a decoy proposition marks the decoy stratum perfectly. Cross-league NONE pairs (about 3.7% of NONE) are always NONE.
13. **Bindings get no card.** Binding MCC is 0.000 for D3 27B, Pplx Decider 27B and D3 4B, as pre-registered.
14. **Held-out share is 38.9%** of relations on test (2,332 of 6,000), above the ~30% target.
15. **Intervals.** With 18 clusters, the bootstrap intervals somewhat under-cover. The reference was chosen on dev, and that choice is not corrected for in the paired gaps.
16. **Paired tests cover the 15 models, not the baselines.** The v0.1.1 analysis tests all 105 pairs of the 15 models in each family, with Holm
    correction: 89 of 105 headline pairs, 97 of 105 relation pairs and 65 of 105 group pairs are resolved. Unresolved pairs are not ordered. In the
    v0.1.0 leaderboard, seen relation MCC and depth accuracy are points without intervals.
17. **Some models name few or no relations.** The hf pipeline names almost none. Its build flags differ from the pre-registered command: it has no
    relation floor and no batch size, uses `--local-timeout 1800.0` and `--hello-timeout 3600.0`, and finished after the first league_ci run.
    It was added to the bootstrap run, and every other run's value is unchanged.
18. **The empty and oracle baselines have Q_N only.** They have no scored decisions and no interval.
19. **Strict Q_N is 0 for every repaired build.** TEV1 0.8B is the one exception (see Q_N).
20. **Private split.** Only aggregates are published. The seed is not recorded. The sealed directory is not in the repository. Gate 2 (the complete
    oracle panel) is MET for the directory the owner gave `check-gates` (maintainer tooling, not included in the public repository), on the owner's machine.
21. **Engine provenance.** `target/release/mb-engine.exe` (sha256 `2df1bdfb...`) is a copy of the binary the round 3 runs used. It is not a rebuild
    at the pinned commit, and the sha was not reproduced by a rebuild. The engine source is the one the round 2 and round 3 runs used.
22. **Local children are not sandboxed.** Every run card is a local adapter at 0 USD, and the price schedule is `none`.
23. **Depth accuracy is descriptive.** The buckets differ in class mix, and the 4+ bucket has six rows.

## Release gate status

Release v0.1.0 is built at `defed6a` on a clean tree. The gate output is [release/v0.1.0/record/check_gates.md](../release/v0.1.0/record/check_gates.md). The
numbering follows [docs/remaining-work.md](../docs/remaining-work.md) section 2.3. Gates 1, 2, 7, 9 and 10 are MET. Gates 6, 8 and 11 are MET
by owner attestation in [release/v0.1.0/record/attestations.json](../release/v0.1.0/record/attestations.json). Gates 3 and 4 are NOT APPLICABLE, and gate 5 is
REMOVED. The tool does not check certificate rules and strata (gate 6), that corrections are applied (gate 11), or blinding in practice
(gate 8).

| Gate | Name | Status (v0.1.0) |
|---|---|---|
| 1 | Immutable corpus manifests, evidence policy, schemas, IR semantics, resource profiles | MET |
| 2 | Complete exact-panel oracle (every corpus record bound or unresolved; an answer for every query) | MET |
| 3 | Declared coverage for every native scope | NOT APPLICABLE |
| 4 | Audited separation of unjudged material (UNJUDGED is not false) | NOT APPLICABLE |
| 5 | At least 1M reference-distinct, actively tested semantic units before using the Exact 1M label | REMOVED |
| 6 | Reference validation of every hidden query and certificate rule (with global-difficulty strata) | MET (attested) |
| 7 | Leakage checks | MET |
| 8 | Blinded annotation and adjudication policy | MET (attested) |
| 9 | Complete cost accounting | MET |
| 10 | Passing baseline runs | MET |
| 11 | Public correction process that versions faulty fixtures | MET (attested) |

## Runtime

Build wall time for each cell on the test split, from `build_summary.json` (`wall_seconds`). The 27B cells are at or under the 80-minute target.
Baselines take 0.7 to 17.5 seconds each (embedding is the slowest).

| Run | Build wall time (min) |
|---|---|
| D3 27B | 74.6 |
| D3 9B | 21.9 |
| Pplx Decider 27B | 74.4 |
| Kev 9B | 34.9 |
| D3 4B | 15.4 |
| Clef 27B | 78.7 |
| Nimble-v3 9B | 28.3 |
| Nimble 9B | 27.7 |
| Clef-Flash 9B | 26.4 |
| TEV1 4B | 17.2 |
| D1 3B | 8.5 |
| D3 2B | 6.9 |
| TEV1 0.8B | 6.6 |
| D1-Omni 600M | 4.7 |
| Laya 0.4B | 2.4 |
| *Rules (heuristic child)* | *0.0* |
| *Lexical* | *0.0* |
| *Embedding* | *0.3* |
| *gold_view (perfect curated answers)* | *0.0* |
| *hf pipeline (Qwen2.5-1.5B-Instruct)* | *1.5* |

## Pinned provenance

- Code commit `0bff47f` (the cells); release commit `defed6a` (the pinned release commit).
- Fixture `mentat_hard_test` revision 3.1 (content hash above); private split `mentat_hard_private` (hash above).
- Child `systemone-choice` version 7, `prompt_sha256` `d6d5ed56…`.
- Model pins: Ollama digests, sizes and licenses are in [children/ollama_models.json](../python/mentat_bench/children/ollama_models.json)
  and in each entry of [leaderboard.json](v0.1.0/leaderboard.json). The digests match the pre-registration.
- hf pin: `Qwen/Qwen2.5-1.5B-Instruct`, revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` (in [children/models.json](../python/mentat_bench/children/models.json)).
- Run cards: [release/v0.1.0/runcards/](../release/v0.1.0/runcards/) (22 cards). Baseline table: [release/v0.1.0/baselines.json](../release/v0.1.0/baselines.json).

## History

Each earlier record is kept as it was published. Its headline differs from this one, so its numbers are not comparable with v0.1.0.

| Record | Date | Headline (as published) | Status | Documents |
|---|---|---|---|---|
| development round 3, release v0.1.0 | 2026-10-30 (results), 2026-10-31 (release) | relation + group MCC mean. D3 27B 0.519 [0.497, 0.541] | **current** | [test](../docs/results/mentat-hard-20261030.md), [private](../docs/results/mentat-hard-private-20261030.md), [prereg](../release/v0.1.0/record/prereg.md) |
| development round 2 | 2026-10-29 | relation MCC, from 2026-10-29 (the headline changed post-hoc). D3 27B 0.704 [0.682, 0.728]; Clef 27B 0.462 [0.440, 0.486]; rules 0.278 [0.263, 0.292] | superseded by round 3 (header of the round 2 results document) | [results](../docs/history/round-2/report.md), [json](../docs/history/round-2/results.json), [ci](../docs/history/round-2/ci.json) |
| development round 1 | 2026-10-28 | three-part MCC mean (relations, groups, bindings). D3 27B 0.248 | superseded by round 2 (`docs/remaining-work.md` section 2.1) | [results](../docs/history/round-1/report.md), [json](../docs/history/round-1/results.json) |

The round 2 validity audit, which led to the headline change, is
[docs/history/round-2/headline-change-disclosure.md](../docs/history/round-2/headline-change-disclosure.md).

## How to reproduce

The runs are not in git. They are the run directories of the round 3 matrix (`runs/20261030-test`, `runs/20261030-test-baselines` and
`runs/20261030-test-hf/hf`). Replace the placeholders below with the paths to those directories. Commands run from the repository root.

```bash
# 1. The public round 3 results, recomputed from the runs (writes to a scratch file, then compare with the committed JSON)
python docs/results/mentat-hard-results/build_results.py --runs <runs/20261030-test> \
    --baselines <runs/20261030-test-baselines> --hf <runs/20261030-test-hf/hf> --out <scratch>/mentat-hard-20261030.json

# 2. The league bootstrap, byte for byte (see docs/results/mentat-hard-20261030.md, Provenance, for the full run list)
PYTHONPATH=python python tools/eval/league_ci.py --fixture fixtures/mentat_hard_test --runs <each run directory> \
    --reference <runs/20261030-test/d3_27b-q8_0> --B 2000 --seed 0 --out <scratch>/ci.json

# 3. This results bundle, from the runs and the committed JSON; it exits 1 if anything disagrees
python results/v0.1.0/build_bundle.py --runs <runs/20261030-test> \
    --baselines <runs/20261030-test-baselines> --hf <runs/20261030-test-hf/hf> --out <scratch>/results

# 4. The bundle's fast tests, which need no runs
python -m pytest tests/test_results_bundle.py
```

Step 3 writes to `--out` (default `results/`). Pointing `--out` at a scratch directory and comparing with `results/` checks determinism.

## Files

Every file in this folder, with its format.

| Path | What it is | Format |
|---|---|---|
| `README.md` | This page: the entry point. | Markdown |
| `index.json` | The versions, the latest one, and the paths of each version's files. | JSON, see [Schemas](#schemas) |
| `v0.1.0/leaderboard.json` | The v0.1.0 leaderboard for the website: metadata, provenance, models, baselines, comparisons, gates. | JSON, see [Schemas](#schemas) |
| `v0.1.0/leaderboard.csv` | One row per entry (15 models, then 7 baselines), the main columns. Generated from the JSON. | CSV, see [Schemas](#schemas) |
| `v0.1.0/predictions/<id>.jsonl.gz` | Per-item test predictions for the 20 cells with scored decisions (15 models, `heuristic`, `lexical`, `embedding`, `gold_view`, `hf`). Gzip, deterministic. | JSON Lines, see [Schemas](#schemas) |
| `v0.1.0/cells/<id>.json` | The per-cell summary for all 22 cells: score, score_decisions, build summary, child stats, cell build record and wall time. | JSON, see [Schemas](#schemas) |
| `v0.1.0/build_bundle.py` | The measurement-only builder that regenerates everything in this folder from the runs and the committed JSON. | Python (stdlib and the existing `mentat_bench` package) |

The test that guards this folder is [tests/test_results_bundle.py](../tests/test_results_bundle.py).

### Schemas

**`index.json`** (`schema_version` `mentat-bench-results-index/1`). `latest` is the newest version. `versions[]` lists the versions, newest
first. Each entry has `version`, `date`, `results_date`, `status` (`latest` or `superseded`), `benchmark`, `fixture`, `fixture_version`,
`headline`, `superseded_by`, and the paths of its files: `readme`, `leaderboard_json`, `leaderboard_csv`, `predictions_dir`, `cells_dir`,
`builder`, `results_md`, `results_json`, and the private and prereg documents where they exist. Paths are repository-relative. Older versions have `null` for `readme`, `leaderboard_json`, `leaderboard_csv`, `predictions_dir`, `cells_dir` and `builder`, because this bundle does not cover them. Their `results_md` and `results_json` point to the committed documents. The two development rounds (`round-2`, `round-1`) also have `kind` = `development_round`; the latest release has no `kind`.

**`v0.1.0/leaderboard.json`** (`schema` `mentat-bench-leaderboard/1`). Sorted keys. Top level:

- `release`, `date`, `results_date`: the public bundle version and dates.
- `benchmark`: the name, what it measures, the split, the headline definition and decision date, the secondary score, the diagnostics, and the
  reference model's id.
- `glossary`: the meaning of each metric name used below, and of `ci95` and `rank`. `units` gives the unit of each quantity (MCC from -1 to 1, Q_N from 0 to 100, minutes, seconds, bytes).
- `provenance`: the code commit (`code_commit`), the release commit, the release manifest path and hash, `fixture` (name, version, registry version,
  content hash, leagues, markets, outcomes, queries, `item_counts`, and `files` with each file's `bytes` and `sha256`), `private_split`, `child`,
  `ci` (method, file, layout, `B`, `seed`, `leagues`, `level`, `reference`), the document paths, and `ollama_version`.
  The `code_commit` values are commits of the private development repository.
- `ranking`: the 15 model ids in rank order. `depth_buckets` and `depth_n`: the depth buckets and their sizes.
- `models[]`: the 15 System One models, in rank order. `baselines[]`: `heuristic`, `lexical`, `embedding`, `empty`, `gold_view`, `oracle`, `hf`.
- `resolved_comparisons[]` and `resolved_comparisons_note`: every paired gap to the reference, one row per run and statistic, with
  `excludes_zero` and `higher` (`reference` or `run`, when the gap excludes 0).
- `unpaired_headline_vs_rules`: the rules interval and the models entirely above it or overlapping it (unpaired).
- `headline_interval_overlaps[]`: the pairs of runs whose headline intervals overlap. Overlap is not a tie.
- `d3_size_curve[]`: the four D3 sizes with headline, relation and group points, and dev headline.
- `private_checks`: the private split's floor and score checks, the Spearman correlation over 120 pairs, and the largest headline change.
- `release_gates`: the source file, the commit, the scope of gate 7, and the 11 gate rows (`gate`, `name`, `status`, `evidence`, `note`).

Each entry of `models[]` and `baselines[]` has these keys:

- `id` (the run directory name, stable), `display_name`, `kind` (`system_one_model`, `baseline`, `ceiling` or `pipeline_baseline`), `family`,
  `rank` (models only), `run_dir` (repository-relative path under `runs/`, not committed), `cell` (the cell summary path), `predictions`
  (`null` when the cell has no scored decisions), `runcard`.
- Model identity: `ollama_tag`, `hf_model`, `hf_revision`, `digest`, `size_bytes`, `license`, `params`, `params_source`, `quant`, `child`
  (`id`, `version`, `prompt_sha256`), `relation_floor` (a float, or `null` when `relation_floor_disabled` is `true`: no floor reaches the precision, so the model names no relation under it), and `names_relations_under_floor` (from the prereg).
- `metrics`: `headline`, `relation`, `group`, `binding`, `reading`, `held_out_relation`, `seen_relation`, `q_n_repaired`, `q_n_strict`. Each is
  `{"point": x, "ci95": [lo, hi] or null}`. A metric the run does not have is `null`. `seen_relation` and `q_n_strict` have no interval.
- `gap_to_reference`: `headline`, `relation`, `group`, `binding` and `q_n_repaired`, each a paired gap (reference minus run). `null` for the reference.
- `dev`: the pre-registered dev values (`headline`, and for models `relation`, `group`, `binding`, `reading` and
  `headline_change_test_minus_dev`).
- `depth_accuracy` (buckets `0`, `1`, `2`, `3`, `4+`, `NONE` with `n`, `correct`, `accuracy`), `coverage` (per op), `counts`, `abstention`,
  `repair` (groups and relations dropped), `runtime` (`build_wall_min`, `build_wall_s`, `private_build_wall_min`).
- `private`: the private split's `status`, `headline`, `relation`, `group`, `binding`, `public_test_headline`, `headline_private_minus_public`
  and `gap_to_reference`; `null` for runs not on the private split.
- `flags[]`: machine-readable flags, each explained by a line of `notes[]`. The ‡ mark is `base_not_repaired` and the † mark is `abstains_for_length`. Other flags: `reference`, `never_flags_ambiguous`, `binding_fell_dev_to_test`, `names_no_relations_under_floor`, `license_non_commercial`, `no_group_or_reading_decisions`, `q_n_only`, `nonstandard_build_flags`, `answers_none_for_nearly_every_relation`. `notes[]`: the plain-text notes, with their numbers read from the results. `hf` also has `build_flags`.

**`v0.1.0/leaderboard.csv`**: a header row, then one row per entry (models in rank order, then baselines). The columns are `rank`, `id`,
`display_name`, `kind`, `family`, `ollama_tag`, `digest`, `params`, `quant`, `license`, `size_bytes`, then `headline_point`, `headline_lo`, `headline_hi`,
`relation_point`, `relation_lo`, `relation_hi`, `group_point`, `group_lo`, `group_hi`, `binding_point`, `binding_lo`, `binding_hi`,
`reading_point`, `reading_lo`, `reading_hi`, `held_out_relation_point`, `held_out_relation_lo`, `held_out_relation_hi`,
`seen_relation_point`, `q_n_repaired_point`, `q_n_repaired_lo`, `q_n_repaired_hi`, `q_n_strict`, `gap_headline_point`, `gap_headline_lo`,
`gap_headline_hi`, `dev_headline`, `relation_floor`, `private_headline_point`, `private_headline_lo`, `private_headline_hi`, and
`build_wall_min`. An empty cell means the value does not exist for that run (for example, empty and oracle have no intervals).

**`v0.1.0/predictions/<id>.jsonl.gz`**: one JSON object per scored item, sorted by `qid` (a reading question's base row comes before its alt
row). Each row has `qid`, `op` (`decide_relation`, `decide_group`, `decide_binding` or `decide_reading`), `stratum`, `held_out`, `gold` (the
label class), `pred` (the predicted class, `__missing__` when the answer is absent or invalid), and `correct` (`gold == pred`). Optional keys:

- `option`: the label's kind, present only when it differs from `gold` (a group class with k, or a group with terms).
- `probs`: the child's probabilities over the options of the op, rounded to 4 decimals, when the answer is scored with probabilities.
- Relation rows: `a` and `b` (the candidate's order; `pred` and `probs` are oriented to it), `depth` (the gold inference depth, `null` for NONE),
  and `abstained` (`true` when the child wrote a bare relation, which is an abstention for length).
- Binding rows: `id` (the proposition).
- Group and reading rows: `members`. Reading rows also have `reading` (`base` or `alt`).
- `chain` (the implied order of an answered `ordered_chain`) and `order` (the gold order of a labelled chain).

Group abstentions are not marked per row. The count is in the cell summary (`child_stats`). Each file rescores to its cell's
`score_decisions.json` with `decision_scoring.score`: the headline, the per-op metrics, and the strata are exact. Brier and ECE values
are recomputed from the rounded probabilities, so they agree to within 1e-4 (Brier) and 5e-3 (ECE). The largest observed differences are
6e-6 and 2.6e-3.

**`v0.1.0/cells/<id>.json`**: `id`, `kind`, `run_dir`, `score` (the cell's `score.json`), `score_decisions` (its `score_decisions.json`; `null` for
empty and oracle), `build_summary`, `child_stats` (model cells only), `cell` (the cell's `cell.json`: adapter, build flags, fixture stamp,
engine and repo facts, step timings), `wall` (`build_wall_s`, `build_wall_min`, `step_build_s`), and `predictions` (the source measurement path, or `null`).
Use the leaderboard or index for public v0.1.0 artifact paths.

The JSON files are UTF-8, with one-space indentation and sorted keys. The builder writes them byte-identically on rerun.

## Changelog

- 2026-10-31: v0.1.0 results bundle (this folder). `v0.1.0/` is the bundle that was there before the relabel (its earlier path is in `docs/history/RENAMES.json`), relabelled and regenerated, not a new addition; the numbers are unchanged. The round-3 results report (`docs/results/mentat-hard-20261030.md`) was relabelled and its JSON regenerated, and the private document's title was edited. The release record (`release/v0.1.0/record/`) is byte-identical to its originals (sha256 in `docs/history/RENAMES.json`).

- 2026-10-31 (regenerated files): the 22 cell files and `leaderboard.json` changed only in paths, labels and provenance fields. The run directories were renamed on 2026-11-01 (their former names carried the earlier run label; see [docs/history/README.md](../docs/history/README.md)), so the cells' `run_dir`, `out_dir`, `answers` and `verdicts` fields, and the leaderboard's baseline run directories, now carry the new names. The `predictions` path of the 20 cells that have predictions pointed at a directory that does not exist in this checkout (the former results folder); it now points at `results/v0.1.0/predictions/`. The leaderboard's document paths, split label and release-gate notes now name the current files and labels, and its provenance field for the earlier release label was removed. The release-gate table gained `release_gates.recorded_at` = "defed6a, 2026-10-31", which marks the gate statuses as recorded at that commit, not re-verified at a later commit. The run-card paths in the leaderboard moved to `release/v0.1.0/runcards/`. No local paths were present in the previous files, so none were removed. The CSV is unchanged. No score changed: every numeric value in the cells and in `leaderboard.json` is identical to the previous bundle, and the 20 predictions files are byte-identical to it.

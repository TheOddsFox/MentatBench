# Mentat-Hard results, v0.2.0 (a new measurement on Ollama 0.40.13)

> The four tables (leaderboard, run to run, version shift, baselines) were written from [leaderboard.json](leaderboard.json),
> [analysis.json](analysis.json) and [baselines.json](baselines.json) by a one-off script that is not in the repository. Each
> cell is checked against those files by `tests/test_results_bundle_v020.py`
> (`test_v020_readme_tables_match_the_committed_json_cell_by_cell`). The analysis section between the markers is written by
> `tools/eval/release_analysis_v020.py --readme` and checked by `--check --readme`. The sentences of the prose are checked by
> `tests/test_release_docs.py`.

This folder holds the v0.2.0 release of the Mentat-Hard test set (fixture revision 3.1). It re-measures the 15 pre-registered
System One models and adds two Kev models (kev_4b-q8_0, kev_0.8b-q8_0) that were added after pre-registration, on Ollama 0.40.13.
The published v0.1.0 measurements used Ollama 0.40.11; the v0.1.0 record is frozen in [../v0.1.0/](../v0.1.0/) and
[release/v0.1.0/record](../../release/v0.1.0/record). The v0.1.x pages in [../README.md](../README.md) stay as they were.

What this is: an owner re-measurement on the same machine. It is not an independent reproduction or verification. The scores are a
score on this benchmark's relation and group decisions, not general capability.

## Leaderboard

| Rank | Run id | Model | Params | Quant | Headline: relation + group MCC [95% CI] | Relation MCC [95% CI] | Group MCC [95% CI] | Binding MCC, secondary [95% CI] | Gap to D3 27B [95% CI] | Rank [95% interval] | Build (min) | License |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `d3_27b-q8_0` | D3 27B | 27B | q8_0 | 0.521 [0.500, 0.545] | 0.788 [0.765, 0.814] | 0.254 [0.230, 0.279] | 0.000 [0.000, 0.000] | reference | 1–1 (median 1) | 70.7 | Apache-2.0 |
| 2 | `d3_9b-q8_0` | D3 9B | 9B | q8_0 | 0.274 [0.253, 0.297] | 0.460 [0.437, 0.484] | 0.089 [0.059, 0.118] | 0.000 [0.000, 0.000] | 0.247 [0.227, 0.268] | 2–3 (median 2) | 21.1 | Apache-2.0 |
| 3 | `pplx-decider_27b` | Pplx Decider 27B | 27B | — | 0.257 [0.242, 0.274] | 0.510 [0.488, 0.536] | 0.004 [-0.018, 0.029] | 0.000 [0.000, 0.000] | 0.264 [0.241, 0.289] | 2–4 (median 3) | 72.9 | Apache-2.0 |
| 4 | `kev_9b-bf16` | Kev 9B | 9B | bf16 | 0.236 [0.214, 0.261] | 0.324 [0.302, 0.348] | 0.148 [0.119, 0.179] | -0.012 [-0.032, 0.014] | 0.285 [0.268, 0.304] | 3–5 (median 4) | 26.3 | Apache-2.0 |
| 5 | `d3_4b-q8_0` | D3 4B | 4B | q8_0 | 0.232 [0.216, 0.249] | 0.393 [0.370, 0.416] | 0.071 [0.050, 0.093] | 0.000 [0.000, 0.000] | 0.289 [0.271, 0.312] | 4–5 (median 5) | 14.6 | Apache-2.0 |
| 6 | `clef_27b-q8_0` | Clef 27B | 27B | q8_0 | 0.208 [0.188, 0.230] | 0.395 [0.370, 0.420] | 0.020 [-0.009, 0.050] | 0.187 [0.087, 0.291] | 0.314 [0.289, 0.339] | 6–7 (median 6) | 76.0 | — |
| 7 | `nimble-v3_9b-q8_0` | Nimble-v3 9B | 9B | q8_0 | 0.200 [0.186, 0.214] | 0.274 [0.246, 0.303] | 0.126 [0.106, 0.145] | -0.025 [-0.030, -0.021] | 0.321 [0.300, 0.343] | 6–7 (median 7) | 27.1 | CC-BY-NC-4.0 |
| 8 | `nimble_9b-bf16` | Nimble 9B | 9B | bf16 | 0.177 [0.166, 0.189] | 0.273 [0.258, 0.287] | 0.082 [0.055, 0.106] | 0.145 [0.000, 0.258] | 0.344 [0.320, 0.369] | 8–9 (median 8) | 29.9 | — |
| 9 | `clef-flash_9b` | Clef-Flash 9B | 9B | — | 0.173 [0.164, 0.182] | 0.251 [0.236, 0.267] | 0.095 [0.077, 0.114] | 0.017 [-0.020, 0.087] | 0.348 [0.328, 0.371] | 8–9 (median 9) | 25.7 | Apache-2.0 |
| 10 | `tev1_4b-q8_0` | TEV1 4B | 4B | q8_0 | 0.142 [0.123, 0.163] | 0.301 [0.273, 0.330] | -0.017 [-0.042, 0.010] | 0.002 [-0.023, 0.027] | 0.379 [0.353, 0.403] | 10–11 (median 10) | 16.5 | Apache-2.0 |
| 11 | `kev_4b-q8_0` | Kev 4B (not pre-registered) | 4B | q8_0 | 0.137 [0.126, 0.148] | 0.274 [0.251, 0.295] | 0.000 [0.000, 0.000] | 0.206 [0.137, 0.269] | 0.384 [0.363, 0.406] | 10–11 (median 11) | 17.9 | Apache-2.0 |
| 12 | `d1_3b-q8_0` | D1 3B | 3B | q8_0 | 0.071 [0.058, 0.087] | 0.115 [0.093, 0.139] | 0.027 [0.003, 0.053] | 0.072 [-0.017, 0.171] | 0.450 [0.429, 0.474] | 12–13 (median 12) | 8.0 | — |
| 13 | `d3_2b-q8_0` | D3 2B | 2B | q8_0 | 0.052 [0.038, 0.068] | 0.168 [0.150, 0.187] | -0.063 [-0.087, -0.037] | -0.031 [-0.063, 0.005] | 0.469 [0.453, 0.486] | 13–14 (median 13) | 6.8 | Apache-2.0 |
| 14 | `kev_0.8b-q8_0` | Kev 0.8B (not pre-registered) | 0.8B | q8_0 | 0.052 [0.035, 0.069] | 0.068 [0.051, 0.087] | 0.035 [0.011, 0.062] | 0.003 [-0.041, 0.044] | 0.470 [0.445, 0.495] | 13–14 (median 14) | 10.5 | Apache-2.0 |
| 15 | `tev1_0.8b-q8_0` | TEV1 0.8B | 0.8B | q8_0 | 0.000 [-0.005, 0.006] | 0.001 [-0.009, 0.011] | 0.000 [0.000, 0.000] | -0.042 [-0.097, 0.014] | 0.521 [0.501, 0.543] | 15–16 (median 15) | 7.3 | Apache-2.0 |
| 16 | `d1-omni_600m-q8_0` | D1-Omni 600M | 600M | q8_0 | -0.003 [-0.010, 0.004] | 0.015 [0.003, 0.027] | -0.021 [-0.033, -0.007] | 0.045 [0.038, 0.052] | 0.524 [0.503, 0.548] | 15–16 (median 16) | 5.5 | LFM-Open-1.0 |
| 17 | `laya_latest` | Laya 0.4B | — | — | -0.020 [-0.032, -0.007] | -0.016 [-0.029, 0.002] | -0.024 [-0.041, -0.006] | -0.036 [-0.086, 0.011] | 0.541 [0.514, 0.567] | 17–17 (median 17) | 3.0 | Apache-2.0 |

## Run to run (r2)

| Model | Decisions byte-identical | Answers identical (share of 10,716) | Changed answers | Headline change vs r1 | Flip rate |
|---|---|---|---|---|---|
| D3 27B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| D3 9B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Pplx Decider 27B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Kev 9B | no | 1.0000 | 0 | +0.0000 | 0.00% |
| D3 4B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Clef 27B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Nimble-v3 9B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Nimble 9B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Clef-Flash 9B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| TEV1 4B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Kev 4B | no | 0.9994 | 6 | +0.0003 | 0.06% |
| D1 3B | no | 0.9898 | 109 | +0.0002 | 1.02% |
| D3 2B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Kev 0.8B | no | 0.9967 | 35 | -0.0002 | 0.33% |
| TEV1 0.8B | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| D1-Omni 600M | yes | 1.0000 | 0 | +0.0000 | 0.00% |
| Laya 0.4B | yes | 1.0000 | 0 | +0.0000 | 0.00% |

17 repeats in all. 13 have a byte-identical decisions file, and 14 have every answer equal to r1's. The largest headline change is 0.0003 (absolute). Probability-only differences (every answer equal, the probabilities differ, so the decisions file is not byte-identical): Kev 9B. Runs with changed answers (changed rows in brackets): Kev 4B (6), D1 3B (109), Kev 0.8B (35).

## Version shift (Ollama 0.40.11 to 0.40.13)

| Model | Headline change (v0.2.0 minus v0.1.0) | Changed answers | Flip rate |
|---|---|---|---|
| D3 27B | +0.0027 | 65 | 0.61% |
| D3 9B | -0.0003 | 88 | 0.82% |
| Pplx Decider 27B | +0.0044 | 279 | 2.60% |
| Kev 9B | +0.0003 | 28 | 0.26% |
| D3 4B | -0.0008 | 125 | 1.17% |
| Clef 27B | +0.0009 | 185 | 1.73% |
| Nimble-v3 9B | -0.0017 | 234 | 2.18% |
| Nimble 9B | -0.0010 | 45 | 0.42% |
| Clef-Flash 9B | +0.0015 | 134 | 1.25% |
| TEV1 4B | -0.0018 | 189 | 1.76% |
| D1 3B | +0.0000 | 1 | 0.01% |
| D3 2B | -0.0027 | 212 | 1.98% |
| TEV1 0.8B | +0.0004 | 47 | 0.44% |
| D1-Omni 600M | +0.0001 | 4 | 0.04% |
| Laya 0.4B | +0.0000 | 0 | 0.00% |

Across the 15 pre-registered models, the headline changes have a median absolute value of 0.0009 and a largest of 0.0044, against a run-to-run maximum of 0.0003. The Kev models have no v0.1.0 run and are not in this table.

## Baselines

| Baseline | Status | Headline [95% CI] or Q_N | Source |
|---|---|---|---|
| Rules (heuristic child) | carried | 0.140 [0.121, 0.153] | v0.1.0 files (exact, see the record) |
| Lexical | carried | 0.000 [0.000, 0.000] | v0.1.0 files (exact, see the record) |
| Embedding | carried | 0.007 [-0.002, 0.014] | v0.1.0 files (exact, see the record) |
| Empty | carried | Q_N 16.88 (no headline) | v0.1.0 files (exact, see the record) |
| gold_view (perfect curated answers) | carried | 1.000 [1.000, 1.000] | v0.1.0 files (exact, see the record) |
| Oracle | carried | Q_N 100.00 (no headline) | v0.1.0 files (exact, see the record) |
| hf pipeline (Qwen2.5-1.5B-Instruct) | carried | 0.008 [-0.002, 0.018] | v0.1.0 files (exact, see the record) |

gold_view is a ceiling (gold-backed), not a System One model. empty and oracle report Q_N only.

## Analysis

<!-- analysis:start -->

### Analysis (v0.2.0, a new measurement on Ollama 0.40.13)

Every table in this section is computed by `tools/eval/release_analysis_v020.py` from the committed v0.2.0 predictions, cells and leaderboard, and the committed v0.1.0 predictions for the version shift. The numbers are in [analysis.json](analysis.json). No score is changed by the analysis.

The leaderboard's intervals are league-bootstrap intervals (B = 2000). The rank, pairwise and depth intervals and the p-values here are the paired draws of the same resampling at B = 20000 (the first 2000 are the leaderboard's draws). Uncertainty comes only from resampling the 18 leagues of one fixture: each model has one deterministic run per measurement, and the three metric families are corrected separately.

#### Rank intervals

Rank 1 is the highest point. The interval is the 2.5% to 97.5% range of the rank over the draws.

| Model | Headline rank (95%) | Relation rank (95%) | Group rank (95%) |
|---|---|---|---|
| d3_27b-q8_0 | 1 [1, 1] (median 1) | 1 [1, 1] (median 1) | 1 [1, 1] (median 1) |
| d3_9b-q8_0 | 2 [2, 3] (median 2) | 3 [3, 3] (median 3) | 5 [4, 7] (median 5) |
| pplx-decider_27b | 3 [2, 4] (median 3) | 2 [2, 2] (median 2) | 11 [9, 14] (median 11) |
| kev_9b-bf16 | 4 [3, 5] (median 4) | 6 [6, 6] (median 6) | 2 [2, 3] (median 2) |
| d3_4b-q8_0 | 5 [4, 5] (median 5) | 5 [4, 5] (median 5) | 7 [5, 7] (median 7) |
| clef_27b-q8_0 | 6 [6, 7] (median 6) | 4 [4, 5] (median 4) | 10 [8, 13] (median 10) |
| nimble-v3_9b-q8_0 | 7 [6, 7] (median 7) | 9 [8, 11] (median 9) | 3 [2, 3] (median 3) |
| nimble_9b-bf16 | 8 [8, 9] (median 8) | 10 [7, 10] (median 9) | 6 [4, 7] (median 6) |
| clef-flash_9b | 9 [8, 9] (median 9) | 11 [10, 11] (median 11) | 4 [4, 6] (median 4) |
| tev1_4b-q8_0 | 10 [10, 11] (median 10) | 7 [7, 8] (median 7) | 14 [11, 16] (median 14) |
| kev_4b-q8_0 | 11 [10, 11] (median 11) | 8 [8, 11] (median 9) | 12 [10, 13] (median 12) |
| d1_3b-q8_0 | 12 [12, 13] (median 12) | 13 [13, 13] (median 13) | 9 [8, 11] (median 9) |
| d3_2b-q8_0 | 13 [13, 14] (median 13) | 12 [12, 12] (median 12) | 17 [17, 17] (median 17) |
| kev_0.8b-q8_0 | 14 [13, 14] (median 14) | 14 [14, 14] (median 14) | 8 [7, 10] (median 8) |
| tev1_0.8b-q8_0 | 15 [15, 16] (median 15) | 16 [15, 17] (median 16) | 12 [10, 13] (median 12) |
| d1-omni_600m-q8_0 | 16 [15, 16] (median 16) | 15 [15, 16] (median 15) | 15 [14, 16] (median 15) |
| laya_latest | 17 [17, 17] (median 17) | 17 [16, 17] (median 17) | 16 [14, 16] (median 15) |

#### Holm-resolved pairs

Differences are higher minus lower. The Holm p-value is adjusted within each family; when no draw lies on the other side it is a floor. Leave-one-league-out: each resolved pair's point difference is recomputed with each of the 18 leagues left out.

**Headline**: 117 of 136 pairs are resolved (Holm, alpha 0.05). Leave-one-league-out: 117 of 117 keep their sign in all 18 recomputations.
- d3_27b-q8_0 over d3_9b-q8_0: higher minus lower 0.247 [0.226, 0.267], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over pplx-decider_27b: higher minus lower 0.264 [0.241, 0.289], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_9b-bf16: higher minus lower 0.285 [0.267, 0.304], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_4b-q8_0: higher minus lower 0.289 [0.269, 0.311], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef_27b-q8_0: higher minus lower 0.314 [0.289, 0.339], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.321 [0.300, 0.343], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble_9b-bf16: higher minus lower 0.344 [0.320, 0.369], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef-flash_9b: higher minus lower 0.348 [0.327, 0.371], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.379 [0.354, 0.403], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_4b-q8_0: higher minus lower 0.384 [0.362, 0.406], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.450 [0.429, 0.473], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.469 [0.454, 0.486], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.470 [0.445, 0.496], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.521 [0.500, 0.544], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.524 [0.502, 0.549], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over laya_latest: higher minus lower 0.541 [0.513, 0.567], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_9b-bf16: higher minus lower 0.038 [0.020, 0.055], p_holm 0.0136 (1 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_4b-q8_0: higher minus lower 0.043 [0.023, 0.061], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over clef_27b-q8_0: higher minus lower 0.067 [0.040, 0.093], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.075 [0.058, 0.092], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over nimble_9b-bf16: higher minus lower 0.097 [0.071, 0.123], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over clef-flash_9b: higher minus lower 0.101 [0.077, 0.126], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.132 [0.110, 0.155], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_4b-q8_0: higher minus lower 0.137 [0.120, 0.156], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.203 [0.181, 0.225], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.222 [0.202, 0.242], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.223 [0.200, 0.247], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.274 [0.252, 0.298], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.277 [0.255, 0.302], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over laya_latest: higher minus lower 0.295 [0.270, 0.316], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over clef_27b-q8_0: higher minus lower 0.049 [0.025, 0.075], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over nimble-v3_9b-q8_0: higher minus lower 0.057 [0.035, 0.079], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over nimble_9b-bf16: higher minus lower 0.080 [0.059, 0.101], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over clef-flash_9b: higher minus lower 0.084 [0.066, 0.104], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over tev1_4b-q8_0: higher minus lower 0.115 [0.095, 0.135], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over kev_4b-q8_0: higher minus lower 0.120 [0.102, 0.138], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d1_3b-q8_0: higher minus lower 0.186 [0.165, 0.206], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_2b-q8_0: higher minus lower 0.205 [0.186, 0.224], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over kev_0.8b-q8_0: higher minus lower 0.206 [0.185, 0.225], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over tev1_0.8b-q8_0: higher minus lower 0.257 [0.243, 0.271], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d1-omni_600m-q8_0: higher minus lower 0.260 [0.243, 0.278], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over laya_latest: higher minus lower 0.277 [0.255, 0.298], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble-v3_9b-q8_0: higher minus lower 0.036 [0.017, 0.057], p_holm 0.0136 (1 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble_9b-bf16: higher minus lower 0.059 [0.034, 0.085], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over clef-flash_9b: higher minus lower 0.063 [0.040, 0.088], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_4b-q8_0: higher minus lower 0.094 [0.069, 0.119], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over kev_4b-q8_0: higher minus lower 0.099 [0.082, 0.117], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1_3b-q8_0: higher minus lower 0.165 [0.146, 0.186], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_2b-q8_0: higher minus lower 0.184 [0.162, 0.205], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over kev_0.8b-q8_0: higher minus lower 0.185 [0.160, 0.211], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.236 [0.214, 0.260], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.239 [0.215, 0.266], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over laya_latest: higher minus lower 0.256 [0.231, 0.278], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.032 [0.017, 0.048], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over nimble_9b-bf16: higher minus lower 0.055 [0.035, 0.074], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over clef-flash_9b: higher minus lower 0.059 [0.042, 0.077], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_4b-q8_0: higher minus lower 0.090 [0.069, 0.109], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over kev_4b-q8_0: higher minus lower 0.095 [0.081, 0.109], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.161 [0.147, 0.174], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.179 [0.161, 0.196], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.180 [0.158, 0.202], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.232 [0.215, 0.248], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.235 [0.219, 0.252], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over laya_latest: higher minus lower 0.252 [0.230, 0.271], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over clef-flash_9b: higher minus lower 0.034 [0.016, 0.053], p_holm 0.0136 (4 of 20,000 draws on the other side)
- clef_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.065 [0.038, 0.091], p_holm 0.0136 (1 of 20,000 draws on the other side)
- clef_27b-q8_0 over kev_4b-q8_0: higher minus lower 0.071 [0.051, 0.093], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.137 [0.110, 0.162], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.155 [0.134, 0.177], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.156 [0.127, 0.188], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.207 [0.187, 0.230], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.211 [0.189, 0.236], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over laya_latest: higher minus lower 0.228 [0.203, 0.255], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over clef-flash_9b: higher minus lower 0.027 [0.012, 0.041], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.058 [0.036, 0.080], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over kev_4b-q8_0: higher minus lower 0.063 [0.049, 0.078], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.129 [0.114, 0.143], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.147 [0.127, 0.167], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.148 [0.129, 0.167], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.200 [0.185, 0.214], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.203 [0.187, 0.219], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over laya_latest: higher minus lower 0.220 [0.199, 0.239], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over kev_4b-q8_0: higher minus lower 0.040 [0.026, 0.056], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1_3b-q8_0: higher minus lower 0.106 [0.089, 0.121], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d3_2b-q8_0: higher minus lower 0.125 [0.106, 0.144], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over kev_0.8b-q8_0: higher minus lower 0.126 [0.103, 0.150], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.177 [0.165, 0.190], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.180 [0.170, 0.191], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over laya_latest: higher minus lower 0.197 [0.177, 0.216], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over kev_4b-q8_0: higher minus lower 0.036 [0.022, 0.052], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1_3b-q8_0: higher minus lower 0.102 [0.085, 0.117], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over d3_2b-q8_0: higher minus lower 0.121 [0.104, 0.137], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over kev_0.8b-q8_0: higher minus lower 0.122 [0.099, 0.144], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over tev1_0.8b-q8_0: higher minus lower 0.173 [0.163, 0.182], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1-omni_600m-q8_0: higher minus lower 0.176 [0.167, 0.186], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over laya_latest: higher minus lower 0.193 [0.175, 0.211], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.071 [0.048, 0.093], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.090 [0.072, 0.109], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.091 [0.065, 0.117], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.142 [0.123, 0.160], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.145 [0.126, 0.165], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over laya_latest: higher minus lower 0.162 [0.142, 0.181], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.066 [0.051, 0.079], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.084 [0.068, 0.100], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.085 [0.065, 0.105], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.137 [0.125, 0.148], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.140 [0.127, 0.153], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over laya_latest: higher minus lower 0.157 [0.141, 0.170], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.071 [0.058, 0.086], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.074 [0.060, 0.091], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over laya_latest: higher minus lower 0.091 [0.069, 0.113], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.052 [0.038, 0.067], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.056 [0.040, 0.072], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over laya_latest: higher minus lower 0.073 [0.052, 0.091], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.051 [0.036, 0.069], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.055 [0.034, 0.075], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over laya_latest: higher minus lower 0.072 [0.048, 0.093], p_holm < 0.0136 (0 of 20,000 draws on the other side)

**Relation**: 124 of 136 pairs are resolved (Holm, alpha 0.05). Leave-one-league-out: 124 of 124 keep their sign in all 18 recomputations.
- d3_27b-q8_0 over d3_9b-q8_0: higher minus lower 0.328 [0.309, 0.348], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over pplx-decider_27b: higher minus lower 0.278 [0.255, 0.302], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_9b-bf16: higher minus lower 0.464 [0.443, 0.488], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_4b-q8_0: higher minus lower 0.396 [0.372, 0.420], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef_27b-q8_0: higher minus lower 0.393 [0.375, 0.412], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.515 [0.486, 0.543], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble_9b-bf16: higher minus lower 0.516 [0.488, 0.546], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef-flash_9b: higher minus lower 0.537 [0.507, 0.569], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.487 [0.462, 0.513], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_4b-q8_0: higher minus lower 0.514 [0.491, 0.540], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.673 [0.656, 0.691], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.621 [0.603, 0.640], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.720 [0.698, 0.743], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.788 [0.767, 0.811], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.773 [0.747, 0.804], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over laya_latest: higher minus lower 0.805 [0.774, 0.831], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_9b-q8_0: higher minus lower 0.050 [0.023, 0.077], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_9b-bf16: higher minus lower 0.136 [0.119, 0.155], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_4b-q8_0: higher minus lower 0.067 [0.055, 0.081], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over clef_27b-q8_0: higher minus lower 0.065 [0.045, 0.086], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.187 [0.168, 0.206], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over nimble_9b-bf16: higher minus lower 0.187 [0.161, 0.215], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over clef-flash_9b: higher minus lower 0.209 [0.177, 0.240], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.159 [0.147, 0.172], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_4b-q8_0: higher minus lower 0.186 [0.170, 0.205], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.345 [0.326, 0.363], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.292 [0.276, 0.309], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.392 [0.370, 0.414], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.460 [0.436, 0.483], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.445 [0.416, 0.475], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over laya_latest: higher minus lower 0.476 [0.447, 0.498], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over kev_9b-bf16: higher minus lower 0.186 [0.154, 0.216], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_4b-q8_0: higher minus lower 0.117 [0.088, 0.147], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over clef_27b-q8_0: higher minus lower 0.115 [0.092, 0.138], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over nimble-v3_9b-q8_0: higher minus lower 0.236 [0.207, 0.265], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over nimble_9b-bf16: higher minus lower 0.237 [0.215, 0.261], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over clef-flash_9b: higher minus lower 0.259 [0.228, 0.290], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over tev1_4b-q8_0: higher minus lower 0.209 [0.177, 0.240], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over kev_4b-q8_0: higher minus lower 0.236 [0.205, 0.267], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d1_3b-q8_0: higher minus lower 0.395 [0.373, 0.416], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_2b-q8_0: higher minus lower 0.342 [0.319, 0.367], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over kev_0.8b-q8_0: higher minus lower 0.442 [0.417, 0.468], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over tev1_0.8b-q8_0: higher minus lower 0.509 [0.489, 0.532], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d1-omni_600m-q8_0: higher minus lower 0.495 [0.469, 0.524], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over laya_latest: higher minus lower 0.526 [0.493, 0.559], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over kev_9b-bf16: higher minus lower 0.068 [0.050, 0.087], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over kev_9b-bf16: higher minus lower 0.071 [0.050, 0.094], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble-v3_9b-q8_0: higher minus lower 0.051 [0.024, 0.075], p_holm 0.0136 (1 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble_9b-bf16: higher minus lower 0.052 [0.026, 0.079], p_holm 0.0136 (1 of 20,000 draws on the other side)
- kev_9b-bf16 over clef-flash_9b: higher minus lower 0.073 [0.046, 0.101], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over kev_4b-q8_0: higher minus lower 0.050 [0.041, 0.061], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1_3b-q8_0: higher minus lower 0.209 [0.189, 0.230], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_2b-q8_0: higher minus lower 0.157 [0.140, 0.173], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over kev_0.8b-q8_0: higher minus lower 0.256 [0.233, 0.280], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.324 [0.302, 0.348], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.309 [0.279, 0.340], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over laya_latest: higher minus lower 0.341 [0.312, 0.361], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.119 [0.102, 0.136], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over nimble_9b-bf16: higher minus lower 0.120 [0.095, 0.146], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over clef-flash_9b: higher minus lower 0.141 [0.113, 0.170], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_4b-q8_0: higher minus lower 0.092 [0.074, 0.107], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over kev_4b-q8_0: higher minus lower 0.119 [0.102, 0.137], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.278 [0.258, 0.298], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.225 [0.209, 0.243], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.325 [0.303, 0.347], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.392 [0.369, 0.415], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.377 [0.348, 0.408], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over laya_latest: higher minus lower 0.409 [0.378, 0.433], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.122 [0.098, 0.146], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over nimble_9b-bf16: higher minus lower 0.122 [0.099, 0.147], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over clef-flash_9b: higher minus lower 0.144 [0.116, 0.171], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.094 [0.069, 0.118], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over kev_4b-q8_0: higher minus lower 0.121 [0.101, 0.145], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.280 [0.257, 0.303], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.227 [0.210, 0.245], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.327 [0.300, 0.355], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.395 [0.371, 0.419], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.380 [0.353, 0.410], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over laya_latest: higher minus lower 0.411 [0.381, 0.438], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.159 [0.136, 0.183], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.106 [0.085, 0.129], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.205 [0.179, 0.234], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.273 [0.247, 0.301], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.258 [0.222, 0.296], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over laya_latest: higher minus lower 0.290 [0.256, 0.320], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1_3b-q8_0: higher minus lower 0.158 [0.128, 0.185], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d3_2b-q8_0: higher minus lower 0.105 [0.081, 0.128], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over kev_0.8b-q8_0: higher minus lower 0.205 [0.177, 0.230], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.272 [0.253, 0.289], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.257 [0.241, 0.274], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over laya_latest: higher minus lower 0.289 [0.263, 0.310], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over clef-flash_9b: higher minus lower 0.050 [0.017, 0.086], p_holm 0.0406 (28 of 20,000 draws on the other side)
- clef-flash_9b over d1_3b-q8_0: higher minus lower 0.136 [0.103, 0.168], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over d3_2b-q8_0: higher minus lower 0.084 [0.060, 0.106], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over kev_0.8b-q8_0: higher minus lower 0.183 [0.154, 0.212], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over tev1_0.8b-q8_0: higher minus lower 0.251 [0.231, 0.269], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1-omni_600m-q8_0: higher minus lower 0.236 [0.217, 0.256], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over laya_latest: higher minus lower 0.268 [0.243, 0.289], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over kev_4b-q8_0: higher minus lower 0.027 [0.013, 0.044], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.186 [0.163, 0.209], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.134 [0.113, 0.155], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.233 [0.206, 0.259], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.301 [0.274, 0.329], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.286 [0.252, 0.323], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_4b-q8_0 over laya_latest: higher minus lower 0.317 [0.283, 0.344], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over d1_3b-q8_0: higher minus lower 0.159 [0.134, 0.184], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.106 [0.088, 0.122], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.206 [0.182, 0.229], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.273 [0.251, 0.296], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.259 [0.230, 0.287], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over laya_latest: higher minus lower 0.290 [0.261, 0.311], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over d1_3b-q8_0: higher minus lower 0.053 [0.035, 0.069], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.047 [0.029, 0.067], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.114 [0.095, 0.137], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.100 [0.070, 0.132], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over laya_latest: higher minus lower 0.131 [0.101, 0.155], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.099 [0.080, 0.119], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.167 [0.151, 0.184], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.152 [0.129, 0.177], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_2b-q8_0 over laya_latest: higher minus lower 0.184 [0.160, 0.202], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.068 [0.052, 0.083], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.053 [0.028, 0.079], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over laya_latest: higher minus lower 0.084 [0.059, 0.103], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1-omni_600m-q8_0 over laya_latest: higher minus lower 0.032 [0.010, 0.049], p_holm 0.0494 (37 of 20,000 draws on the other side)

**Group**: 80 of 136 pairs are resolved (Holm, alpha 0.05). Leave-one-league-out: 80 of 80 keep their sign in all 18 recomputations.
- d3_27b-q8_0 over d3_9b-q8_0: higher minus lower 0.165 [0.135, 0.195], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over pplx-decider_27b: higher minus lower 0.250 [0.214, 0.287], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_9b-bf16: higher minus lower 0.106 [0.084, 0.128], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_4b-q8_0: higher minus lower 0.183 [0.158, 0.208], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef_27b-q8_0: higher minus lower 0.234 [0.196, 0.275], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble-v3_9b-q8_0: higher minus lower 0.128 [0.100, 0.160], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over nimble_9b-bf16: higher minus lower 0.172 [0.143, 0.205], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over clef-flash_9b: higher minus lower 0.159 [0.143, 0.177], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_4b-q8_0: higher minus lower 0.271 [0.233, 0.309], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_4b-q8_0: higher minus lower 0.254 [0.229, 0.280], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1_3b-q8_0: higher minus lower 0.227 [0.187, 0.271], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.317 [0.290, 0.346], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.219 [0.183, 0.256], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.254 [0.229, 0.280], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.276 [0.251, 0.301], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_27b-q8_0 over laya_latest: higher minus lower 0.278 [0.244, 0.310], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over pplx-decider_27b: higher minus lower 0.085 [0.045, 0.126], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_9b-q8_0: higher minus lower 0.059 [0.027, 0.095], p_holm 0.0136 (1 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.105 [0.060, 0.153], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over kev_4b-q8_0: higher minus lower 0.089 [0.060, 0.119], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.151 [0.113, 0.188], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.089 [0.060, 0.119], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.110 [0.080, 0.138], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_9b-q8_0 over laya_latest: higher minus lower 0.113 [0.082, 0.143], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over pplx-decider_27b: higher minus lower 0.144 [0.105, 0.185], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over pplx-decider_27b: higher minus lower 0.067 [0.042, 0.092], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over pplx-decider_27b: higher minus lower 0.122 [0.085, 0.158], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over pplx-decider_27b: higher minus lower 0.078 [0.042, 0.112], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over pplx-decider_27b: higher minus lower 0.091 [0.056, 0.124], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- pplx-decider_27b over d3_2b-q8_0: higher minus lower 0.067 [0.030, 0.103], p_holm 0.0136 (1 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_4b-q8_0: higher minus lower 0.077 [0.044, 0.109], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over clef_27b-q8_0: higher minus lower 0.128 [0.090, 0.170], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over nimble_9b-bf16: higher minus lower 0.066 [0.027, 0.107], p_holm 0.0348 (5 of 20,000 draws on the other side)
- kev_9b-bf16 over clef-flash_9b: higher minus lower 0.053 [0.024, 0.082], p_holm 0.0136 (1 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_4b-q8_0: higher minus lower 0.165 [0.119, 0.210], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over kev_4b-q8_0: higher minus lower 0.148 [0.118, 0.179], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1_3b-q8_0: higher minus lower 0.121 [0.078, 0.166], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d3_2b-q8_0: higher minus lower 0.211 [0.173, 0.249], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over kev_0.8b-q8_0: higher minus lower 0.113 [0.076, 0.154], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.148 [0.118, 0.179], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.170 [0.138, 0.201], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_9b-bf16 over laya_latest: higher minus lower 0.172 [0.138, 0.206], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d3_4b-q8_0: higher minus lower 0.055 [0.026, 0.083], p_holm 0.0180 (2 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_4b-q8_0: higher minus lower 0.088 [0.052, 0.123], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over kev_4b-q8_0: higher minus lower 0.071 [0.049, 0.094], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.134 [0.101, 0.166], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.071 [0.049, 0.094], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.092 [0.074, 0.113], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d3_4b-q8_0 over laya_latest: higher minus lower 0.095 [0.065, 0.127], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over clef_27b-q8_0: higher minus lower 0.106 [0.067, 0.143], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over clef_27b-q8_0: higher minus lower 0.075 [0.041, 0.109], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef_27b-q8_0 over d3_2b-q8_0: higher minus lower 0.083 [0.045, 0.123], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_4b-q8_0: higher minus lower 0.143 [0.106, 0.182], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over kev_4b-q8_0: higher minus lower 0.126 [0.107, 0.145], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1_3b-q8_0: higher minus lower 0.099 [0.074, 0.126], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d3_2b-q8_0: higher minus lower 0.189 [0.155, 0.222], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over kev_0.8b-q8_0: higher minus lower 0.091 [0.062, 0.122], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over tev1_0.8b-q8_0: higher minus lower 0.126 [0.107, 0.145], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.147 [0.125, 0.170], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble-v3_9b-q8_0 over laya_latest: higher minus lower 0.150 [0.120, 0.181], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over tev1_4b-q8_0: higher minus lower 0.099 [0.058, 0.138], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over kev_4b-q8_0: higher minus lower 0.082 [0.055, 0.107], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d3_2b-q8_0: higher minus lower 0.145 [0.110, 0.178], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over tev1_0.8b-q8_0: higher minus lower 0.082 [0.055, 0.107], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over d1-omni_600m-q8_0: higher minus lower 0.103 [0.079, 0.127], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- nimble_9b-bf16 over laya_latest: higher minus lower 0.106 [0.073, 0.138], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over tev1_4b-q8_0: higher minus lower 0.112 [0.080, 0.144], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over kev_4b-q8_0: higher minus lower 0.095 [0.076, 0.114], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1_3b-q8_0: higher minus lower 0.068 [0.034, 0.104], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over d3_2b-q8_0: higher minus lower 0.158 [0.133, 0.182], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over kev_0.8b-q8_0: higher minus lower 0.060 [0.026, 0.095], p_holm 0.0399 (6 of 20,000 draws on the other side)
- clef-flash_9b over tev1_0.8b-q8_0: higher minus lower 0.095 [0.076, 0.114], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over d1-omni_600m-q8_0: higher minus lower 0.116 [0.097, 0.137], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- clef-flash_9b over laya_latest: higher minus lower 0.119 [0.089, 0.147], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_4b-q8_0 over d3_2b-q8_0: higher minus lower 0.063 [0.038, 0.087], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- d1_3b-q8_0 over d3_2b-q8_0: higher minus lower 0.090 [0.054, 0.123], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over d3_2b-q8_0: higher minus lower 0.098 [0.065, 0.129], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- tev1_0.8b-q8_0 over d3_2b-q8_0: higher minus lower 0.063 [0.038, 0.087], p_holm < 0.0136 (0 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over d1-omni_600m-q8_0: higher minus lower 0.056 [0.025, 0.086], p_holm 0.0236 (3 of 20,000 draws on the other side)
- kev_0.8b-q8_0 over laya_latest: higher minus lower 0.059 [0.027, 0.092], p_holm < 0.0136 (0 of 20,000 draws on the other side)

#### Weighting of relation and group MCC

The headline weights the relation and group MCC equally (the published scheme, w = 0.5). Each scheme weights the relation MCC by w and the group MCC by 1 - w, with its own rank intervals and its own Holm family of 136 pairs. The published column reproduces the headline ranks and pairs.

| Model | relation_only (1 / 0) | r75 (0.75 / 0.25) | published (0.5 / 0.5) | r25 (0.25 / 0.75) | group_only (0 / 1) |
|---|---|---|---|---|---|
| d3_27b-q8_0 | 1 [1, 1] | 1 [1, 1] | 1 [1, 1] | 1 [1, 1] | 1 [1, 1] |
| d3_9b-q8_0 | 3 [3, 3] | 3 [2, 3] | 2 [2, 3] | 3 [2, 4] | 5 [4, 7] |
| pplx-decider_27b | 2 [2, 2] | 2 [2, 3] | 3 [2, 4] | 7 [5, 9] | 11 [9, 14] |
| kev_9b-bf16 | 6 [6, 6] | 6 [5, 6] | 4 [3, 5] | 2 [2, 3] | 2 [2, 3] |
| d3_4b-q8_0 | 5 [4, 5] | 4 [4, 5] | 5 [4, 5] | 5 [4, 6] | 7 [5, 7] |
| clef_27b-q8_0 | 4 [4, 5] | 5 [4, 6] | 6 [6, 7] | 9 [6, 9] | 10 [8, 13] |
| nimble-v3_9b-q8_0 | 9 [8, 11] | 7 [7, 9] | 7 [6, 7] | 4 [3, 5] | 3 [2, 3] |
| nimble_9b-bf16 | 10 [7, 10] | 8 [7, 9] | 8 [8, 9] | 8 [6, 9] | 6 [4, 7] |
| clef-flash_9b | 11 [10, 11] | 10 [9, 11] | 9 [8, 9] | 6 [5, 8] | 4 [4, 6] |
| tev1_4b-q8_0 | 7 [7, 8] | 9 [7, 10] | 10 [10, 11] | 11 [10, 13] | 14 [11, 16] |
| kev_4b-q8_0 | 8 [8, 11] | 11 [10, 11] | 11 [10, 11] | 10 [10, 11] | 12 [10, 13] |
| d1_3b-q8_0 | 13 [13, 13] | 13 [13, 13] | 12 [12, 13] | 12 [11, 13] | 9 [8, 11] |
| d3_2b-q8_0 | 12 [12, 12] | 12 [12, 12] | 13 [13, 14] | 15 [14, 17] | 17 [17, 17] |
| kev_0.8b-q8_0 | 14 [14, 14] | 14 [14, 14] | 14 [13, 14] | 13 [11, 13] | 8 [7, 10] |
| tev1_0.8b-q8_0 | 16 [15, 17] | 16 [15, 16] | 15 [15, 16] | 14 [14, 15] | 12 [10, 13] |
| d1-omni_600m-q8_0 | 15 [15, 16] | 15 [15, 16] | 16 [15, 16] | 16 [15, 17] | 15 [14, 16] |
| laya_latest | 17 [16, 17] | 17 [17, 17] | 17 [17, 17] | 17 [15, 17] | 16 [14, 16] |

Holm-resolved pairs per scheme (of 136): relation_only 124, r75 121, published 117, r25 107, group_only 80.

Robust pairs: 58 are resolved under every scheme with the same winner (weighting.robust_pairs in analysis.json).

Weight-dependent pairs (74): the winner differs across schemes, or the pair is resolved under some schemes only.

- D3 9B vs Pplx Decider 27B, higher by scheme: relation_only Pplx Decider 27B, r75 not resolved, published not resolved, r25 D3 9B, group_only D3 9B.
- D3 9B vs Kev 9B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 not resolved, group_only Kev 9B.
- D3 9B vs D3 4B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 not resolved, group_only not resolved.
- D3 9B vs Clef 27B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 D3 9B, group_only not resolved.
- D3 9B vs Nimble-v3 9B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 not resolved, group_only not resolved.
- D3 9B vs Nimble 9B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 D3 9B, group_only not resolved.
- D3 9B vs Clef-Flash 9B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 D3 9B, group_only not resolved.
- D3 9B vs D1 3B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 D3 9B, group_only not resolved.
- D3 9B vs Kev 0.8B, higher by scheme: relation_only D3 9B, r75 D3 9B, published D3 9B, r25 D3 9B, group_only not resolved.
- Pplx Decider 27B vs Kev 9B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published not resolved, r25 Kev 9B, group_only Kev 9B.
- Pplx Decider 27B vs D3 4B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published not resolved, r25 not resolved, group_only D3 4B.
- Pplx Decider 27B vs Clef 27B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 not resolved, group_only not resolved.
- Pplx Decider 27B vs Nimble-v3 9B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 not resolved, group_only Nimble-v3 9B.
- Pplx Decider 27B vs Nimble 9B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 not resolved, group_only Nimble 9B.
- Pplx Decider 27B vs Clef-Flash 9B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 not resolved, group_only Clef-Flash 9B.
- Pplx Decider 27B vs TEV1 4B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs Kev 4B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs D1 3B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs Kev 0.8B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs TEV1 0.8B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs D1-Omni 600M, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Pplx Decider 27B vs Laya 0.4B, higher by scheme: relation_only Pplx Decider 27B, r75 Pplx Decider 27B, published Pplx Decider 27B, r25 Pplx Decider 27B, group_only not resolved.
- Kev 9B vs D3 4B, higher by scheme: relation_only D3 4B, r75 D3 4B, published not resolved, r25 Kev 9B, group_only Kev 9B.
- Kev 9B vs Clef 27B, higher by scheme: relation_only Clef 27B, r75 not resolved, published not resolved, r25 Kev 9B, group_only Kev 9B.
- Kev 9B vs Nimble-v3 9B, higher by scheme: relation_only Kev 9B, r75 Kev 9B, published Kev 9B, r25 not resolved, group_only not resolved.
- Kev 9B vs TEV1 4B, higher by scheme: relation_only not resolved, r75 Kev 9B, published Kev 9B, r25 Kev 9B, group_only Kev 9B.
- D3 4B vs Nimble-v3 9B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 not resolved, group_only Nimble-v3 9B.
- D3 4B vs Nimble 9B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 not resolved, group_only not resolved.
- D3 4B vs Clef-Flash 9B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 not resolved, group_only not resolved.
- D3 4B vs D1 3B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 D3 4B, group_only not resolved.
- D3 4B vs Kev 0.8B, higher by scheme: relation_only D3 4B, r75 D3 4B, published D3 4B, r25 D3 4B, group_only not resolved.
- Clef 27B vs Nimble-v3 9B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published not resolved, r25 Nimble-v3 9B, group_only Nimble-v3 9B.
- Clef 27B vs Nimble 9B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published not resolved, r25 not resolved, group_only not resolved.
- Clef 27B vs Clef-Flash 9B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 not resolved, group_only Clef-Flash 9B.
- Clef 27B vs TEV1 4B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 not resolved, group_only not resolved.
- Clef 27B vs Kev 4B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Clef 27B vs D1 3B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Clef 27B vs Kev 0.8B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Clef 27B vs TEV1 0.8B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Clef 27B vs D1-Omni 600M, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Clef 27B vs Laya 0.4B, higher by scheme: relation_only Clef 27B, r75 Clef 27B, published Clef 27B, r25 Clef 27B, group_only not resolved.
- Nimble-v3 9B vs Nimble 9B, higher by scheme: relation_only not resolved, r75 not resolved, published not resolved, r25 Nimble-v3 9B, group_only not resolved.
- Nimble-v3 9B vs Clef-Flash 9B, higher by scheme: relation_only not resolved, r75 not resolved, published Nimble-v3 9B, r25 Nimble-v3 9B, group_only not resolved.
- Nimble-v3 9B vs TEV1 4B, higher by scheme: relation_only not resolved, r75 not resolved, published Nimble-v3 9B, r25 Nimble-v3 9B, group_only Nimble-v3 9B.
- Nimble-v3 9B vs Kev 4B, higher by scheme: relation_only not resolved, r75 Nimble-v3 9B, published Nimble-v3 9B, r25 Nimble-v3 9B, group_only Nimble-v3 9B.
- Nimble 9B vs TEV1 4B, higher by scheme: relation_only not resolved, r75 not resolved, published not resolved, r25 Nimble 9B, group_only Nimble 9B.
- Nimble 9B vs Kev 4B, higher by scheme: relation_only not resolved, r75 not resolved, published Nimble 9B, r25 Nimble 9B, group_only Nimble 9B.
- Nimble 9B vs D1 3B, higher by scheme: relation_only Nimble 9B, r75 Nimble 9B, published Nimble 9B, r25 Nimble 9B, group_only not resolved.
- Nimble 9B vs Kev 0.8B, higher by scheme: relation_only Nimble 9B, r75 Nimble 9B, published Nimble 9B, r25 Nimble 9B, group_only not resolved.
- Clef-Flash 9B vs TEV1 4B, higher by scheme: relation_only TEV1 4B, r75 not resolved, published not resolved, r25 Clef-Flash 9B, group_only Clef-Flash 9B.
- Clef-Flash 9B vs Kev 4B, higher by scheme: relation_only not resolved, r75 not resolved, published Clef-Flash 9B, r25 Clef-Flash 9B, group_only Clef-Flash 9B.
- TEV1 4B vs Kev 4B, higher by scheme: relation_only TEV1 4B, r75 not resolved, published not resolved, r25 not resolved, group_only not resolved.
- TEV1 4B vs D1 3B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 not resolved, group_only not resolved.
- TEV1 4B vs D3 2B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 TEV1 4B, group_only not resolved.
- TEV1 4B vs Kev 0.8B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 not resolved, group_only not resolved.
- TEV1 4B vs TEV1 0.8B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 TEV1 4B, group_only not resolved.
- TEV1 4B vs D1-Omni 600M, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 TEV1 4B, group_only not resolved.
- TEV1 4B vs Laya 0.4B, higher by scheme: relation_only TEV1 4B, r75 TEV1 4B, published TEV1 4B, r25 TEV1 4B, group_only not resolved.
- Kev 4B vs D1 3B, higher by scheme: relation_only Kev 4B, r75 Kev 4B, published Kev 4B, r25 not resolved, group_only not resolved.
- Kev 4B vs Kev 0.8B, higher by scheme: relation_only Kev 4B, r75 Kev 4B, published Kev 4B, r25 not resolved, group_only not resolved.
- Kev 4B vs TEV1 0.8B, higher by scheme: relation_only Kev 4B, r75 Kev 4B, published Kev 4B, r25 Kev 4B, group_only not resolved.
- Kev 4B vs D1-Omni 600M, higher by scheme: relation_only Kev 4B, r75 Kev 4B, published Kev 4B, r25 Kev 4B, group_only not resolved.
- Kev 4B vs Laya 0.4B, higher by scheme: relation_only Kev 4B, r75 Kev 4B, published Kev 4B, r25 Kev 4B, group_only not resolved.
- D1 3B vs D3 2B, higher by scheme: relation_only D3 2B, r75 not resolved, published not resolved, r25 D1 3B, group_only D1 3B.
- D1 3B vs Kev 0.8B, higher by scheme: relation_only D1 3B, r75 D1 3B, published not resolved, r25 not resolved, group_only not resolved.
- D1 3B vs TEV1 0.8B, higher by scheme: relation_only D1 3B, r75 D1 3B, published D1 3B, r25 D1 3B, group_only not resolved.
- D1 3B vs D1-Omni 600M, higher by scheme: relation_only D1 3B, r75 D1 3B, published D1 3B, r25 D1 3B, group_only not resolved.
- D1 3B vs Laya 0.4B, higher by scheme: relation_only D1 3B, r75 D1 3B, published D1 3B, r25 D1 3B, group_only not resolved.
- D3 2B vs Kev 0.8B, higher by scheme: relation_only D3 2B, r75 D3 2B, published not resolved, r25 Kev 0.8B, group_only Kev 0.8B.
- D3 2B vs TEV1 0.8B, higher by scheme: relation_only D3 2B, r75 D3 2B, published D3 2B, r25 not resolved, group_only TEV1 0.8B.
- D3 2B vs D1-Omni 600M, higher by scheme: relation_only D3 2B, r75 D3 2B, published D3 2B, r25 not resolved, group_only not resolved.
- D3 2B vs Laya 0.4B, higher by scheme: relation_only D3 2B, r75 D3 2B, published D3 2B, r25 not resolved, group_only not resolved.
- Kev 0.8B vs TEV1 0.8B, higher by scheme: relation_only Kev 0.8B, r75 Kev 0.8B, published Kev 0.8B, r25 Kev 0.8B, group_only not resolved.
- D1-Omni 600M vs Laya 0.4B, higher by scheme: relation_only D1-Omni 600M, r75 D1-Omni 600M, published not resolved, r25 not resolved, group_only not resolved.

#### Depth (accuracy by gold depth, with intervals)

A `*` marks a degenerate cell (n < 30 or valid draws below 99%); its interval is shown as n/a. The 4+ bucket holds 6 items in 1 league.

| Run (depth, 95% interval) | 0 | 1 | 2 | 3 | 4+ | NONE |
|---|---|---|---|---|---|---|
| d3_27b-q8_0 | 0.915 [0.901, 0.928] | 0.920 [0.910, 0.930] | 0.714 [0.674, 0.759] | 0.645 [0.606, 0.681] | 0.500* (n/a) | 0.840 [0.807, 0.872] |
| d3_9b-q8_0 | 0.680 [0.654, 0.705] | 0.376 [0.345, 0.406] | 0.349 [0.319, 0.381] | 0.307 [0.269, 0.346] | 0.000* (n/a) | 0.785 [0.758, 0.812] |
| pplx-decider_27b | 0.809 [0.787, 0.829] | 0.658 [0.626, 0.688] | 0.482 [0.448, 0.518] | 0.468 [0.429, 0.509] | 0.333* (n/a) | 0.402 [0.358, 0.446] |
| kev_9b-bf16 | 0.464 [0.431, 0.495] | 0.317 [0.288, 0.347] | 0.114 [0.095, 0.134] | 0.122 [0.088, 0.154] | 0.167* (n/a) | 0.897 [0.872, 0.920] |
| d3_4b-q8_0 | 0.563 [0.537, 0.588] | 0.374 [0.352, 0.397] | 0.331 [0.306, 0.359] | 0.301 [0.263, 0.342] | 0.500* (n/a) | 0.718 [0.688, 0.746] |
| clef_27b-q8_0 | 0.758 [0.738, 0.775] | 0.492 [0.457, 0.525] | 0.443 [0.417, 0.471] | 0.433 [0.402, 0.462] | 0.167* (n/a) | 0.244 [0.201, 0.295] |
| nimble-v3_9b-q8_0 | 0.451 [0.419, 0.483] | 0.427 [0.385, 0.471] | 0.237 [0.217, 0.259] | 0.223 [0.183, 0.256] | 0.000* (n/a) | 0.643 [0.604, 0.681] |
| nimble_9b-bf16 | 0.521 [0.503, 0.541] | 0.545 [0.501, 0.591] | 0.503 [0.472, 0.529] | 0.459 [0.401, 0.505] | 0.333* (n/a) | 0.092 [0.072, 0.111] |
| clef-flash_9b | 0.502 [0.479, 0.526] | 0.505 [0.474, 0.537] | 0.487 [0.467, 0.506] | 0.476 [0.436, 0.516] | 0.333* (n/a) | 0.027 [0.017, 0.037] |
| tev1_4b-q8_0 | 0.489 [0.459, 0.517] | 0.295 [0.268, 0.324] | 0.121 [0.102, 0.144] | 0.101 [0.066, 0.141] | 0.000* (n/a) | 0.853 [0.838, 0.867] |
| kev_4b-q8_0 | 0.333 [0.300, 0.365] | 0.172 [0.148, 0.197] | 0.044 [0.032, 0.059] | 0.014 [0.005, 0.025] | 0.000* (n/a) | 0.971 [0.962, 0.979] |
| d1_3b-q8_0 | 0.245 [0.227, 0.266] | 0.330 [0.297, 0.362] | 0.208 [0.188, 0.232] | 0.213 [0.177, 0.248] | 0.000* (n/a) | 0.480 [0.445, 0.515] |
| d3_2b-q8_0 | 0.162 [0.138, 0.188] | 0.229 [0.203, 0.257] | 0.136 [0.115, 0.162] | 0.113 [0.089, 0.138] | 0.000* (n/a) | 0.869 [0.844, 0.893] |
| kev_0.8b-q8_0 | 0.069 [0.060, 0.078] | 0.219 [0.191, 0.247] | 0.118 [0.094, 0.144] | 0.104 [0.076, 0.131] | 0.167* (n/a) | 0.759 [0.731, 0.789] |
| tev1_0.8b-q8_0 | 0.014 [0.010, 0.018] | 0.006 [0.001, 0.012] | 0.013 [0.006, 0.020] | 0.014 [0.005, 0.021] | 0.000* (n/a) | 0.951 [0.939, 0.961] |
| d1-omni_600m-q8_0 | 0.151 [0.137, 0.166] | 0.377 [0.343, 0.408] | 0.339 [0.316, 0.363] | 0.320 [0.271, 0.359] | 0.167* (n/a) | 0.062 [0.044, 0.080] |
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
| d3_27b-q8_0 | 5163 | 0.293 [0.234, 0.347] | 0.017 [0.011, 0.024] | 1497 | yes | 76 / 587 / 834 |
| d3_9b-q8_0 | 4159 | 0.435 [0.392, 0.474] | 0.084 [0.059, 0.111] | 1748 | yes | 174 / 796 / 778 |
| pplx-decider_27b | 5942 | 0.535 [0.489, 0.574] | 0.147 [0.128, 0.164] | 2993 | yes | 254 / 1296 / 1443 |
| kev_9b-bf16 | 2521 | 0.413 [0.352, 0.466] | 0.106 [0.081, 0.131] | 1001 | yes | 207 / 423 / 371 |
| d3_4b-q8_0 | 4803 | 0.459 [0.423, 0.493] | 0.042 [0.027, 0.058] | 2169 | yes | 75 / 1090 / 1004 |
| clef_27b-q8_0 | 6315 | 0.566 [0.514, 0.610] | 0.075 [0.060, 0.090] | 3546 | yes | 67 / 1712 / 1767 |
| nimble-v3_9b-q8_0 | 4975 | 0.591 [0.557, 0.619] | 0.152 [0.125, 0.179] | 2845 | yes | 178 / 1400 / 1267 |
| nimble_9b-bf16 | 6556 | 0.608 [0.568, 0.644] | 0.230 [0.207, 0.255] | 3701 | yes | 381 / 1668 / 1652 |
| clef-flash_9b | 6937 | 0.558 [0.518, 0.593] | 0.062 [0.048, 0.078] | 3840 | yes | 124 / 1985 / 1731 |
| tev1_4b-q8_0 | 3689 | 0.480 [0.447, 0.510] | 0.076 [0.066, 0.087] | 1768 | yes | 137 / 886 / 745 |
| kev_4b-q8_0 | 1190 | 0.101 [0.071, 0.133] | 0.018 [0.011, 0.026] | 120 | yes | 22 / 51 / 47 |
| d1_3b-q8_0 | 5172 | 0.609 [0.577, 0.639] | 0.087 [0.075, 0.099] | 3122 | yes | 300 / 1469 / 1353 |
| d3_2b-q8_0 | 1925 | 0.375 [0.324, 0.423] | 0.063 [0.043, 0.082] | 704 | yes | 109 / 321 / 274 |
| kev_0.8b-q8_0 | 2373 | 0.399 [0.351, 0.447] | 0.075 [0.049, 0.103] | 926 | yes | 228 / 359 / 339 |
| tev1_0.8b-q8_0 | 324 | 0.074 [0.041, 0.110] | 0.037 [0.018, 0.060] | 24 | yes | 12 / 12 / 0 |
| d1-omni_600m-q8_0 | 6708 | 0.732 [0.707, 0.753] | 0.516 [0.482, 0.551] | 4302 | yes | 1072 / 1559 / 1671 |
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
| d3_9b-q8_0 | 0.573 | 0.051 | 0.105 | 0.149 |
| pplx-decider_27b | 0.536 | 0.060 | 0.098 | 0.147 |
| kev_9b-bf16 | 0.687 | 0.045 | 0.173 | 0.172 |
| d3_4b-q8_0 | 0.652 | 0.045 | 0.088 | 0.113 |
| clef_27b-q8_0 | 0.608 | 0.063 | 0.104 | 0.143 |
| nimble-v3_9b-q8_0 | 0.718 | 0.130 | 0.097 | 0.047 |
| nimble_9b-bf16 | 0.728 | 0.175 | 0.060 | 0.050 |
| clef-flash_9b | 0.721 | 0.050 | 0.128 | 0.162 |
| tev1_4b-q8_0 | 0.806 | 0.264 | 0.701 | 0.299 |
| kev_4b-q8_0 | 0.731 | 0.101 | 0.181 | 0.197 |
| d1_3b-q8_0 | 0.778 | 0.056 | 0.140 | 0.183 |
| d3_2b-q8_0 | 0.806 | 0.194 | 0.300 | 0.150 |
| kev_0.8b-q8_0 | 0.802 | 0.034 | 0.706 | 0.434 |
| tev1_0.8b-q8_0 | 1.119 | 0.476 | 1.030 | 0.686 |
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
| d3_9b-q8_0 | 3348 | 92 (0.0275) | 0 | 0.000 | 0.671 [0.619, 0.724] | 0.077 [0.053, 0.146] |
| pplx-decider_27b | 3348 | 92 (0.0275) | 0 | 0.000 | 0.760 [0.706, 0.815] | 0.084 [0.066, 0.117] |
| kev_9b-bf16 | 3348 | 92 (0.0275) | 116 | -0.012 | 0.486 [0.436, 0.539] | 0.025 [0.022, 0.031] |
| d3_4b-q8_0 | 3348 | 92 (0.0275) | 0 | 0.000 | 0.452 [0.389, 0.511] | 0.028 [0.024, 0.036] |
| clef_27b-q8_0 | 3348 | 92 (0.0275) | 45 | 0.187 | 0.875 [0.845, 0.901] | 0.181 [0.111, 0.282] |
| nimble-v3_9b-q8_0 | 3348 | 92 (0.0275) | 75 | -0.025 | 0.352 [0.300, 0.406] | 0.019 [0.018, 0.022] |
| nimble_9b-bf16 | 3348 | 92 (0.0275) | 2 | 0.145 | 0.708 [0.650, 0.763] | 0.131 [0.062, 0.231] |
| clef-flash_9b | 3348 | 92 (0.0275) | 37 | 0.017 | 0.509 [0.469, 0.557] | 0.036 [0.028, 0.058] |
| tev1_4b-q8_0 | 3348 | 92 (0.0275) | 2277 | 0.002 | 0.450 [0.401, 0.501] | 0.023 [0.021, 0.027] |
| kev_4b-q8_0 | 3348 | 92 (0.0275) | 127 | 0.206 | 0.449 [0.376, 0.514] | 0.223 [0.132, 0.321] |
| d1_3b-q8_0 | 3348 | 92 (0.0275) | 48 | 0.072 | 0.786 [0.757, 0.818] | 0.091 [0.063, 0.164] |
| d3_2b-q8_0 | 3348 | 92 (0.0275) | 512 | -0.031 | 0.328 [0.247, 0.410] | 0.021 [0.017, 0.036] |
| kev_0.8b-q8_0 | 3348 | 92 (0.0275) | 2852 | 0.003 | 0.463 [0.403, 0.519] | 0.025 [0.021, 0.035] |
| tev1_0.8b-q8_0 | 3348 | 92 (0.0275) | 3318 | -0.042 | 0.520 [0.461, 0.578] | 0.071 [0.035, 0.129] |
| d1-omni_600m-q8_0 | 3348 | 92 (0.0275) | 3127 | 0.045 | 0.637 [0.589, 0.686] | 0.036 [0.032, 0.043] |
| laya_latest | 3348 | 92 (0.0275) | 2927 | -0.036 | 0.472 [0.417, 0.526] | 0.029 [0.024, 0.038] |
| heuristic (baseline) | 3348 | 92 (0.0275) | 0 | 0.000 | n/a | n/a |
| lexical (baseline) | 3348 | 92 (0.0275) | 0 | 0.000 | n/a | n/a |
| embedding (baseline) | 3348 | 92 (0.0275) | 0 | 0.000 | n/a | n/a |
| gold_view (gold-backed ceiling) | 3348 | 92 (0.0275) | 92 | 1.000 | n/a | n/a |
| hf (baseline) | 3348 | 92 (0.0275) | 37 | -0.018 | n/a | n/a |

#### Robustness: run to run and version shift

Run to run is the r2 repeat against the r1 headline run (one repeat per model). The version shift is the change from the v0.1.0 run (Ollama 0.40.11) to this run (Ollama 0.40.13) for the 15 pre-registered models; the Kev models have no v0.1.0 run. Flip rate is the share of the 10,716 decision rows whose label differs.

| Model | Run to run max abs delta | Run to run flip rate | Version shift delta | Version shift flip rate |
|---|---|---|---|---|
| d3_27b-q8_0 | 0.0000 | 0.0% | +0.0027 | 0.6% |
| d3_9b-q8_0 | 0.0000 | 0.0% | -0.0003 | 0.8% |
| pplx-decider_27b | 0.0000 | 0.0% | +0.0044 | 2.6% |
| kev_9b-bf16 | 0.0000 | 0.0% | +0.0003 | 0.3% |
| d3_4b-q8_0 | 0.0000 | 0.0% | -0.0008 | 1.2% |
| clef_27b-q8_0 | 0.0000 | 0.0% | +0.0009 | 1.7% |
| nimble-v3_9b-q8_0 | 0.0000 | 0.0% | -0.0017 | 2.2% |
| nimble_9b-bf16 | 0.0000 | 0.0% | -0.0010 | 0.4% |
| clef-flash_9b | 0.0000 | 0.0% | +0.0015 | 1.3% |
| tev1_4b-q8_0 | 0.0000 | 0.0% | -0.0018 | 1.8% |
| kev_4b-q8_0 | 0.0003 | 0.1% | n/a | n/a |
| d1_3b-q8_0 | 0.0002 | 1.0% | +0.0000 | 0.0% |
| d3_2b-q8_0 | 0.0000 | 0.0% | -0.0027 | 2.0% |
| kev_0.8b-q8_0 | 0.0002 | 0.3% | n/a | n/a |
| tev1_0.8b-q8_0 | 0.0000 | 0.0% | +0.0004 | 0.4% |
| d1-omni_600m-q8_0 | 0.0000 | 0.0% | +0.0001 | 0.0% |
| laya_latest | 0.0000 | 0.0% | +0.0000 | 0.0% |

Version shift: 15 models, median absolute headline change 0.0009, largest 0.0044.
Run to run: 17 models with an r2 repeat, largest absolute headline change 0.0003.

- Diagnostic only: the leaderboard headline is the r1 run; r2 is a run-to-run repeat of r1 on the same machine, engine and command (Ollama 0.40.13, both A6000s).
- identical is the share of the 10,716 prediction rows whose answer equals r1's; 1 means every answer is the same. identical_decisions is true only when the decisions file is byte-identical, which also needs the same probabilities. Equal answers with different probabilities are probability-only differences: the answers agree, the probabilities move by a small amount, and the decisions file is not byte-identical. identical_decisions implies identical_labels.
- The r1-versus-r2 differences may come from batching (OLLAMA_NUM_PARALLEL=2). This is a hypothesis; it is not tested.

#### Cost

Hardware: 2× NVIDIA RTX A6000 (48 GB), Ollama 0.40.13 (CUDA), one server per GPU, OLLAMA_NUM_PARALLEL=2. From the single published run per model; build-phase wall clock, includes harness overhead.

Decisions are the cell's decision count, one per decision item (a reading's base and alt rows count once). Decisions per second is decisions over the build wall seconds; build minutes are the same time over 60.

| Model | Params | Quant | Size (GB) | Build (min) | Decisions | Decisions/s |
|---|---|---|---|---|---|---|
| d3_27b-q8_0 | 27B | q8_0 | 29.53 | 70.7 | 10532 | 2.48 |
| d3_9b-q8_0 | 9B | q8_0 | 10.45 | 21.1 | 10532 | 8.31 |
| pplx-decider_27b | 27B | n/a | 17.48 | 72.9 | 10532 | 2.41 |
| kev_9b-bf16 | 9B | bf16 | 17.92 | 26.3 | 10532 | 6.67 |
| d3_4b-q8_0 | 4B | q8_0 | 5.83 | 14.6 | 10532 | 12.02 |
| clef_27b-q8_0 | 27B | q8_0 | 29.66 | 76.0 | 10532 | 2.31 |
| nimble-v3_9b-q8_0 | 9B | q8_0 | 9.53 | 27.1 | 10532 | 6.47 |
| nimble_9b-bf16 | 9B | bf16 | 17.92 | 29.9 | 10532 | 5.88 |
| clef-flash_9b | 9B | n/a | 10.58 | 25.7 | 10532 | 6.84 |
| tev1_4b-q8_0 | 4B | q8_0 | 4.61 | 16.5 | 10532 | 10.62 |
| kev_4b-q8_0 | 4B | q8_0 | 4.48 | 17.9 | 10532 | 9.79 |
| d1_3b-q8_0 | 3B | q8_0 | 3.73 | 8.0 | 10532 | 21.83 |
| d3_2b-q8_0 | 2B | q8_0 | 3.22 | 6.8 | 10532 | 25.90 |
| kev_0.8b-q8_0 | 0.8B | q8_0 | 0.81 | 10.5 | 10532 | 16.64 |
| tev1_0.8b-q8_0 | 0.8B | q8_0 | 0.83 | 7.3 | 10532 | 24.19 |
| d1-omni_600m-q8_0 | 600M | q8_0 | 0.85 | 5.5 | 10532 | 31.95 |
| laya_latest | n/a | n/a | 0.84 | 3.0 | 10532 | 59.18 |

<!-- analysis:end -->

## What changed from v0.1.1

- The measurement is new: Ollama 0.40.13 (v0.1.0 and v0.1.1 used 0.40.11). A re-run on 0.40.13 does not reproduce the v0.1.0 cells
  exactly. The version shift above is that difference, and it is reported, not corrected.
- Two Kev models are added after pre-registration (kev:4b-q8_0 and kev:0.8b-q8_0). They were pinned, conformed, dev-run and
  floor-calibrated with the same rule as the pre-registered models, on Ollama 0.40.13. Their pins and floors are in
  [release/v0.2.0/record](../../release/v0.2.0/record). Their floors are the dev floors of their own dev runs, and their dev headline
  is recorded there; they are flagged `not_preregistered`.
- The relation floors of the 15 pre-registered models are unchanged. They affect only the repaired-base Q_N diagnostic.
- The reference model stays D3 27B (chosen on dev before pre-registration).
- The r1 run is the headline run. It was designated before the r2 repeat, which is a run-to-run repeat only.
- No private split was re-run. The item audit (model judges) was not re-run; its results are v0.1.1's.
- The release gates are not re-run; the gate record is v0.1.0's ([release/v0.1.0/record/check_gates.md](../../release/v0.1.0/record/check_gates.md)).
- The runs used an engine binary (sha256 `38fc6fcc…`) built on the owner's machine from the public engine source (`crates/`,
  `Cargo.toml` and `Cargo.lock` identical to main). Rebuilding from that source gives a binary of the same size that differs in 28
  bytes (sha256 `c382aadd…`), so the binary is not byte-reproducible. Only Q_N and repairs depend on the engine; the headline is
  scored from the model's decisions. See [release/v0.2.0/record/README.md](../../release/v0.2.0/record/README.md).

## Glossary

- Rank: 1 plus the number of models with a strictly higher headline point. Tied models share a rank (the analysis uses the same rule).
  The leaderboard's rank intervals are the 95% range of the per-draw rank over B = 20000 draws.
- Headline: the mean of the relation and group MCC on raw decisions (not floored). Binding MCC is secondary.
- Not pre-registered: a Kev model added after pre-registration (see the record).
- Constant group output (flag `constant_group_output`): every group decision of the model is one class. Kev 4B and TEV1 0.8B
  answer `none` for all 1,000 group decisions, so their group MCC is 0 by construction and their headline is their relation MCC
  divided by two. Their headlines come from the relation MCC alone; the group part carries no information in those two runs.
- Dev change (`headline_change_test_minus_dev`): for the two Kev models it is the test headline minus their own dev-run headline,
  both on 0.40.13. For the 15 pre-registered models it is null: their dev blocks are the 0.40.11 dev results carried from v0.1.0,
  and a change across the two Ollama versions is not reported.
- Identical (run to run): "identical decisions" means the decisions file is byte-identical, which also needs the same
  probabilities. "Identical answers" means every answer equals r1's. A repeat can have identical answers and different
  probabilities: those are probability-only differences, and the decisions file is then not identical. The analysis's `identical`
  field is the share of answers equal to r1's; `identical_decisions` implies `identical_labels`.

## Files

- [leaderboard.json](leaderboard.json), [leaderboard.csv](leaderboard.csv): the leaderboard (built by `build_bundle.py build`).
- [analysis.json](analysis.json): the analysis (built by `tools/eval/release_analysis_v020.py`), pinned to the leaderboard by sha256.
- [cells/](cells), [predictions/](predictions): one cell document and one predictions file per run (the carried baselines point at v0.1.0).
- [repeats/](repeats): the r2 repeat, predictions only.
- [baselines.json](baselines.json): each baseline's status.
- [../../release/v0.2.0/runcards/](../../release/v0.2.0/runcards): one run card per model cell.
- [../../release/v0.2.0/record/README.md](../../release/v0.2.0/record/README.md): the deviations and the record.
- The builder: [build_bundle.py](build_bundle.py).

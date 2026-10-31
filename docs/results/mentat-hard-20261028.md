# Mentat-Hard v1: System One test matrix, 2026-10-28

**Run.** Split `mentat_hard_test` v1 (0907b4f8…), code `main` 7749647, run under the frozen protocol in `mentat-hard-prereg-20261028.md`.

**Evaluation.** Eight Ollama System One models went through the fixed harness with child v4, one question per request. Each cell answered every curated question (2,806 bindings, 10,000 relations, 1,000 groups) and then compiled its answers with its dev-calibrated relation floor for the exam. Numbers are in `mentat-hard-20261028.json`; the run directories are `runs/20261028-mh-test/` (not committed).

## Results

| Model | **MCC** (headline) | Relations / groups / bindings MCC | Relation macro-F1 | Relation Brier / ECE | Q_N (repaired / strict) | Build |
|---|---|---|---|---|---|---|
| d3:27b-q8_0 | **0.248** | 0.565 / 0.179 / 0.000 | 0.637 | 0.483 / 0.105 | 18.5 (0) | 43.6 min |
| clef:27b-q8_0 | **0.242** | 0.364 / 0.148 / 0.215 | 0.448 | 0.590 / 0.040 | 15.1 (0) | 55.5 min |
| d3:9b-q8_0 | **0.212** | 0.451 / 0.140 / 0.046 | 0.504 | 0.556 / 0.092 | 25.0 (0) | 15.1 min |
| nimble:9b-bf16 | **0.211** | 0.322 / 0.072 / 0.238 | 0.432 | 0.655 / 0.181 | 12.0 (0) | 20.8 min |
| clef-flash:9b | **0.198** | 0.328 / 0.182 / 0.085 | 0.429 | 0.647 / 0.092 | 17.1 (0) | 19.3 min |
| pplx-decider:27b | **0.175** | 0.416 / 0.109 / 0.000 | 0.482 | 0.547 / 0.028 | 16.2 (0) | 44.6 min |
| kev:9b-bf16 | **0.113** | 0.331 / −0.012 / 0.019 | 0.397 | 0.624 / 0.055 | 15.1 (0) | 13.9 min |
| laya (421M) | **0.034** | 0.051 / 0.031 / 0.021 | 0.167 | 0.881 / 0.209 | 15.7 (0) | 4.6 min |
| *rules (heuristic)* | 0.089 | 0.254 / 0.012 / 0.000 | 0.228 | — | 30.9 (30.9) | 2 s |
| *empty* | — | — | — | — | 17.1 (17.1) | 8 s |
| *gold_view (perfect answers)* | 1.000 | 1.000 / 1.000 / 1.000 | 1.000 | — | 74.8 (74.8) | 2 s |
| *oracle (full gold theory)* | — | — | — | — | 100.0 | — |

Every cell finished within the 1.5 h budget. No item errors, no over-length abstentions.

## What this shows
- **Separation.**
  - Relations separate the models clearly: MCC runs from 0.05 (Laya) to 0.56 (D3 27B).
  - Seven of the 8 models beat the rules baseline on relations (0.25). Laya does not.
  - The headline range is 0.03–0.25, and no model is near the ceiling.
- **Groups and bindings are weak for every model.**
  - Group MCC is at most 0.18.
  - Chain order is correct in at most 26% of ordered chains (D3 27B).
  - Gold has 52 unresolved bindings: 26 ambiguous scope and 26 unclear date.
  - D3 27B and pplx call every outcome "bound", so their binding MCC is 0.
  - Apart from Laya, no model catches more than 6 of the 52: Clef 27B 6, Clef-Flash and Kev 4 each, Nimble 3, D3 9B 1.
  - The best binding MCCs belong to Nimble (0.238; 3 caught, no false alarms) and Clef 27B (0.215).
  - Laya catches 48 of the 52, but it also calls 2,402 of the 2,754 clear outcomes "unresolved".
- **Size is not decisive.** D3 9B (0.212) beats Perplexity Decider 27B (0.175), at about 3× its speed. It trails Clef 27B (0.242) by 0.03. Intervals for these runs are in the addendum below.
- **Held-out wording barely matters.** Relation MCC on the 31% of pairs that involve test-only wording is within ±0.04 of the seen-wording MCC for every model. Phrasing generalization is not where these models fail.
- **The end-to-end exam does not reward these decisions yet.**
  - Every model's floored compile still contradicts itself, so the base is repaired and strict Q_N is 0.
  - Repaired Q_N ranges from 12.0 to 25.0. Five models score below the empty build (17.1), Clef-Flash is level with it, and D3 27B (18.5) and D3 9B (25.0) are above it. Every model is below rules (30.9).
  - Perfect curated answers (gold_view) reach only 74.8, which bounds what this suite's compile track can reach.
  - The exam is a composition check; the decision MCC is the meaningful score at this level.
- **Calibration.** Relation ECE is lowest for pplx (0.03) and Clef 27B (0.04). D3 27B is at 0.10, Laya 0.21 and Nimble 0.18.

## Comparison with the dev calibration
Test headline MCC stays within 0.07 of dev for every model:

| Model | Dev | Test |
|---|---|---|
| d3:27b | 0.263 | 0.248 |
| clef:27b | 0.309 | 0.242 |
| d3:9b | 0.278 | 0.212 |
| nimble | 0.224 | 0.211 |
| clef-flash | 0.184 | 0.198 |
| pplx | 0.174 | 0.175 |
| kev | 0.121 | 0.113 |
| laya | 0.017 | 0.034 |

The order changes only among close models.

## Limits
- **Benchmark design:**
  - candidate selection is gold-informed, so blocking is not tested;
  - synthetic single domain;
  - test gold is public.
- **Exam residuals:** the feasibility Yes-share is a weak cue (AUC ≈ 0.6), and the 8 partition items with ambiguous outcomes are all UNRESOLVED.
- **Child v4's binding wording** names the generator's ambiguous phrasings as examples, the same for every model.
- **Run design:** one run per model (repeats on dev were byte-identical), and ordered by MCC; the confidence intervals are in the addendum below.
- **Group labels require the exact k**, so a model that gets the right kind with the wrong k scores as wrong.

## Addendum (2026-10-28): confidence intervals

Intervals for the decision MCCs and Q_N of the v1 test runs, from `tools/eval/league_ci.py` (JSON: `docs/results/mentat-hard-20261028-ci.json`). Each cell is the point value [2.5%, 97.5%]. Q_N is the repaired-base value from `score.json`, the same number as the main table's first Q_N figure. Strict Q_N is 0 for all eight System One runs, because each one's base is repaired (`base repaired = yes`); for rules, empty, gold_view and oracle, strict equals repaired. Gap columns are reference minus run, with D3 27B (d3_27b-q8_0) as the reference: the best point headline of the System One models (0.248).

| Run | Headline MCC | Relations MCC | Groups MCC | Bindings MCC | Held-out relations MCC | Q_N (repaired base) | Headline gap (reference d3_27b-q8_0 minus run) | Q_N (repaired base) gap |
|---|---|---|---|---|---|---|---|---|
| clef-flash_9b | 0.198 [0.152, 0.245] | 0.328 [0.300, 0.361] | 0.182 [0.145, 0.215] | 0.085 [-0.013, 0.188] | 0.305 [0.270, 0.343] | 17.13 [12.86, 21.81] | 0.050 [0.010, 0.091] | 1.32 [-4.27, 6.79] |
| clef_27b-q8_0 | 0.242 [0.161, 0.322] | 0.364 [0.336, 0.395] | 0.148 [0.119, 0.177] | 0.215 [-0.008, 0.424] | 0.367 [0.333, 0.404] | 15.12 [12.02, 18.54] | 0.006 [-0.061, 0.074] | 3.33 [-1.52, 8.05] |
| d3_27b-q8_0 (reference) | 0.248 [0.228, 0.275] | 0.565 [0.531, 0.613] | 0.179 [0.145, 0.218] | 0.000 [0.000, 0.000] | 0.575 [0.533, 0.626] | 18.46 [13.92, 23.80] | reference | reference |
| d3_9b-q8_0 | 0.212 [0.179, 0.268] | 0.451 [0.423, 0.488] | 0.140 [0.106, 0.174] | 0.046 [-0.009, 0.175] | 0.426 [0.394, 0.464] | 25.02 [21.41, 28.75] | 0.036 [-0.012, 0.067] | -6.56 [-11.90, -1.97] |
| kev_9b-bf16 | 0.113 [0.091, 0.134] | 0.331 [0.309, 0.358] | -0.012 [-0.035, 0.012] | 0.019 [-0.026, 0.068] | 0.327 [0.298, 0.357] | 15.07 [10.67, 19.81] | 0.135 [0.115, 0.158] | 3.39 [-1.15, 8.40] |
| laya_latest | 0.034 [0.021, 0.047] | 0.051 [0.043, 0.060] | 0.031 [0.005, 0.058] | 0.021 [-0.007, 0.044] | 0.037 [0.024, 0.051] | 15.65 [12.39, 19.47] | 0.213 [0.185, 0.247] | 2.81 [-3.53, 8.46] |
| nimble_9b-bf16 | 0.211 [0.127, 0.265] | 0.322 [0.295, 0.353] | 0.072 [0.047, 0.097] | 0.238 [0.000, 0.385] | 0.309 [0.279, 0.343] | 12.02 [8.85, 15.24] | 0.037 [-0.013, 0.123] | 6.44 [0.38, 13.06] |
| pplx-decider_27b | 0.175 [0.159, 0.197] | 0.416 [0.383, 0.459] | 0.109 [0.079, 0.141] | 0.000 [0.000, 0.000] | 0.419 [0.375, 0.465] | 16.25 [12.22, 20.76] | 0.073 [0.061, 0.085] | 2.21 [-2.02, 6.51] |
| rules | 0.089 [0.075, 0.105] | 0.254 [0.238, 0.276] | 0.012 [-0.025, 0.058] | 0.000 [0.000, 0.000] | 0.239 [0.219, 0.262] | 30.90 [27.06, 34.90] | 0.159 [0.136, 0.181] | -12.44 [-18.62, -6.40] |
| gold_view (diagnostic) | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 1.000 [1.000, 1.000] | 74.77 [69.81, 80.12] | -0.752 [-0.772, -0.725] | -56.31 [-63.07, -49.94] |
| empty (diagnostic) | - | - | - | - | - | 17.11 [14.61, 19.86] | - | 1.34 [-4.48, 6.98] |
| oracle (diagnostic) | - | - | - | - | - | 100.00 [100.00, 100.00] | - | -81.54 [-86.08, -76.20] |

Method:
- 18 leagues, B = 2000 bootstrap draws, seed 0, percentile intervals (numpy, linear; 2.5 and 97.5 percentiles).
- Runs: the twelve cell folders `runs/20261028-mh-test/` clef-flash_9b, clef_27b-q8_0, d3_27b-q8_0, d3_9b-q8_0, kev_9b-bf16, laya_latest, nimble_9b-bf16, pplx-decider_27b, rules, gold_view, empty, oracle (the `.meta` and `.log` entries beside them are not runs). Command: `python tools/eval/league_ci.py --fixture fixtures/mentat_hard_test --runs <the twelve folders, in that order> --reference d3_27b-q8_0 --B 2000 --seed 0 --out docs/results/mentat-hard-20261028-ci.json`.
- A draw samples the 18 leagues with replacement and recomputes each op's MCC, the held-out relation MCC, the headline (the mean of the per-op MCCs, as `decision_scoring.score` defines it) and Q_N from the confusions and query utilities of the sampled leagues. The same draws serve every run, so the gaps are paired.
- An item's league is that of its candidate's first outcome. The 384 cross-league NONE distractors are on side a, and the b-league dependence of those relations is ignored: the clusters are the leagues of side a only.
- No draw in this run lacked an op or a family, so no draw was excluded.
- The point values equal each run's `score_decisions.json` headline, per-op and held-out MCCs, and its `score.json` Q_N, to 1e-9; a mismatch fails the run.
- The runs are deterministic, so the intervals cover item and league sampling only, not run-to-run variation.
- With 18 league clusters, percentile intervals can under-cover.
- The reference is the best point headline on the same data. The "excludes 0" lists below carry no correction for that selection or for the multiplicity of the eleven gaps.
- empty and oracle have no decisions, so they have Q_N only. gold_view and oracle read gold, so neither is the default reference.

Gaps to D3 27B whose interval excludes 0:
- Headline: clef-flash_9b (+0.050 [0.010, 0.091]), kev_9b (+0.135 [0.115, 0.158]), laya (+0.213 [0.185, 0.247]), pplx-decider_27b (+0.073 [0.061, 0.085]), rules (+0.159 [0.136, 0.181]), gold_view (-0.752 [-0.772, -0.725]). The gaps to clef_27b (+0.006), d3_9b (+0.036) and nimble (+0.037) include 0.
- Q_N (repaired base): d3_9b (-6.56 [-11.90, -1.97]), nimble (+6.44 [0.38, 13.06]), rules (-12.44 [-18.62, -6.40]), gold_view (-56.31 [-63.07, -49.94]) and oracle (-81.54 [-86.08, -76.20]). The other Q_N gaps include 0.

Facts behind the table:
- gold_view is the perfect-answer ceiling: every decision interval is [1.000, 1.000] and Q_N is 74.77 [69.81, 80.12].
- oracle reads gold (its build warns that its score is a diagnostic), so its Q_N of 100 is not a model result.
- The bindings MCC of D3 27B is 0.000 because it answers "bound" for every binding (all 2806 items: 2754 gold-bound and 52 gold-unresolved are predicted bound). pplx-decider_27b and rules have the same binding confusion. The MCC is 0 by convention when a prediction is constant, so the [0.000, 0.000] intervals of these three runs are degenerate, not measured uncertainty.
- nimble's binding lower bound of 0.000 comes from that same convention, not from measured uncertainty: in 242 of 2000 draws (12.1%) the draw has no "unresolved" prediction or no "unresolved" truth, so its MCC is 0 by convention. Those draws set the 2.5% lower bound.

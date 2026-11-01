# Mentat-Hard test matrix: pre-registration, 2026-10-28

This document freezes the protocol before any model is run on the **test** split. The numbers are in `mentat-hard-prereg-20261028.json`; the spec is in `docs/mentat_hard.md`.

## Frozen setup
- **Code.** `main` at 9330e05. Its python tree is identical to b37c610, which ran the dev calibration below.
- **Child.** `systemone-choice` v4: one question per request, EQUIV in the relation menu, per-option probabilities. `prompt_sha256` is fa949d8d….
  - The binding wording was fixed on dev after the first pilot (#58).
  - Disclosure: that wording names "by the break" and "top half" as examples of ambiguity. These are the generator's own ambiguous phrasings, and the hint is the same for every model.
- **Servers.** Ollama 0.40.11, two servers (one per RTX A6000, CUDA only), with `OLLAMA_NUM_PARALLEL=2` and `MB_S1_PARALLEL=2`.
- **Fixtures.** `mentat_hard_test` v1 (0907b4f8…), against which the dev calibration was done on `mentat_hard_dev` v1 (7d23bcbd…). Both are registered and regenerate byte for byte.
- **Per cell.**
  - Command: `runcell --fixture fixtures/mentat_hard_test --candidates fixtures/mentat_hard_test/candidates.jsonl --profile profiles/mentat-hard-v1.json --local-protocol 2 --top-k 24 --batch-size 128 --relation-floor <floor below>`.
  - Budget: 1.5 h for every model.
  - One run per model. Dev repeats of laya and d3:9b were byte-identical: theory, bindings, answers and all 13,872 decisions.
- **Baselines on test.**
  - empty;
  - rules (heuristic child, never tuned on test);
  - oracle: full replay, no `--candidates`, for the exam;
  - gold_view with `--candidates`: suite sanity, MCC 1.0 expected, and the compile ceiling for Q_N.
- **Scores.**
  - Headline: the mean of per-op multiclass MCC on raw (unfloored) decisions.
  - Secondary: per-op MCC, macro-F1, Brier/ECE, MCC on held-out vs seen wording, and Q_N and strict Q_N from the floored compile.
  - Speed: items/s and wall time.

## Floors (dev, exact precision ≥ 0.8)

| Model | Dev MCC (rel / grp / bind) | Dev build | Floor |
|---|---|---|---|
| clef:27b-q8_0 | 0.309 (0.403 / 0.145 / 0.380) | 56 min | 1.8172 |
| d3:9b-q8_0 | 0.278 (0.494 / 0.142 / 0.198) | 15 min | 0.7420 |
| d3:27b-q8_0 | 0.263 (0.622 / 0.168 / 0.000) | 43 min | 0.000286 (keeps all) |
| nimble:9b-bf16 | 0.224 (0.355 / 0.075 / 0.243) | 21 min | 4.9706 |
| clef-flash:9b | 0.184 (0.351 / 0.178 / 0.024) | 19 min | 3.2461 |
| pplx-decider:27b | 0.174 (0.460 / 0.063 / 0.000) | 45 min | 1.1762 |
| kev:9b-bf16 | 0.121 (0.354 / 0.005 / 0.003) | 14 min | 1.4077 |
| laya (421M) | 0.017 (0.050 / 0.007 / −0.007) | 5 min | none reaches 0.8: names no relations |

The rules baseline scores 0.086 on dev. The exact floor values are in the JSON and are passed to the harness with `repr` precision.

## Pilot record (dev)
Pilot 1 (child v3): d3:27b 0.230, laya 0.029, rules 0.086. The spread of 0.20 missed the 0.25 bar, mostly because d3:27b called 1,509 clear outcomes "unresolved".

The binding wording was fixed (v4). Pilot 2: d3:27b 0.263, laya 0.017, so the spread is 0.246. The owner accepted this on 2026-10-28.

## Disclosed limits
- Candidate selection is gold-informed, so blocking is not tested.
- Synthetic single domain.
- Test gold is public.
- Feasibility Yes-share is a weak residual cue (AUC ≈ 0.6, not significant).
- The 8 partition items that contain an ambiguous outcome are all UNRESOLVED.
- The end-to-end Q_N ceiling from perfect curated answers is about 78 on dev.

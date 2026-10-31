# System One dev calibration notes, 2026-10-27

These are the dev-split findings from calibrating 8 System One models (Ollama `/v1/systemone`) for a toy10k matrix. The owner stopped that matrix on 2026-10-27 and retired toy10k as a System One panel in favour of Mentat-Hard, so no toy10k model results exist. The findings below carry over: packing, determinism, speeds and dev precision. Numbers are in `systemone-dev-notes-20261027.json`.

## Setup
- **Code.**
  - Code: `main` at 2c9f082, plus the model pins (`children/ollama_models.json`, added with these notes).
  - Child: `systemone-choice` version 2, one question per request, `prompt_sha256` 03f1d855….
  - Server: Ollama 0.40.11. There are two servers, one per RTX A6000. Each is CUDA only (`GGML_VK_VISIBLE_DEVICES=-1`), with `OLLAMA_NUM_PARALLEL=2`, and the child runs with `MB_S1_PARALLEL=2`.
- **Dev fixture: `runs/20261026-s1dev/fixture` (not committed).**
  - It comes from the toy10k generator run at 8 seasons. The corpus is seasons 2027–2030 only (10,232 outcomes) and shares no outcome id with toy10k. `gold/` is the 8-season gold.
  - Corpus sha256 183ef532…, gold theory 1ab982fa….
  - A new-seed fixture was planned, but the generator needs at least 4 seasons to produce queries, so this split is used instead.
- **Dev build, per model.** `build --top-k 24 --batch-size 128 --max-pairs 40000 --no-groups` with the toy-local-v0-long profile. The relation floor depends only on relation decisions, so groups are skipped. About 5k of the 40k pairs are trivial within-market Yes/No pairs.
- **Floor rule (owner).** `tools/eval/floor_calibrate.py` picks the lowest `--relation-floor` at which the named relations reach precision ≥ 0.8 against dev gold. Gold is the 2-CNF oracle, so precision is a lower bound.

## Results
| Model | Dev precision (no floor) | Floor | Kept at floor | Relation q/s (dev build) | Projected toy10k bindings + relations (not run) |
|---|---|---|---|---|---|
| d3:27b-q8_0 | 0.970 | 0.0017 (all) | 16,777 | 5.9 | 13.7 h |
| kev:9b-bf16 | 0.889 | 9.7e-05 (all) | 11,106 | 17.9 | 4.5 h |
| d3:9b-q8_0 | 0.887 | 0.00021 (all) | 14,593 | 16.8 | 4.8 h |
| nimble:9b-bf16 | 0.829 | 0.0049 (all) | 13,442 | 12.0 | 6.7 h |
| clef:27b-q8_0 | 0.814 | 0.00017 (all) | 13,976 | 4.5 | 17.7 h |
| pplx-decider:27b | 0.812 | 0.00037 (all) | 17,407 | 5.7 | 14.2 h |
| clef-flash:9b | 0.710 | 0.752 | 14,885 (of 17,134), at 0.80 | 12.9 | 6.2 h |
| laya (421M) | 0.360 | 4.738 | 3 (of 35,768), effectively none | 96 | 0.9 h |

A floor marked "(all)" is the lowest conf the model emitted, so every named relation is kept. The projection is bindings plus about 280k relations, at dev-build rates. Groups add up to 25% of the deadline, so every model except Laya is expected to stop at the 5 h cap with part of its relations done. These projections motivated a smaller benchmark.

## Checks
- **Packing (first pass, with up to 64 questions per request).**
  - nimble:9b-bf16 changed its relation argmax on 31 of 240 questions depending on whether a question was sent alone or packed. d3:9b-q8_0 changed 0.
  - Packing gave no speedup.
  - The owner switched to one question per request (#53), and the calibration was re-run.
  - Effect of the switch on dev precision: nimble 0.72 → 0.83 and clef:27b 0.78 → 0.81. The others changed by at most 0.02.
- **Determinism.** With one question per request, repeated dev builds produced byte-identical bindings and relations, both for Laya on the same GPU and for d3:9b on the other GPU. Packed runs had differed on about 0.04% of relation decisions. One run per model is enough.
- **Errors.** No item errors and no over-length abstentions occurred in any dev build. Groups were not run.

## Status
The toy10k protocol frozen from these floors was started on 2026-10-27 and stopped after about 1.5 h by owner decision; its partial runs were discarded. The toy10k baselines measured on this code were empty 22.40 and rules 49.43, both strict.

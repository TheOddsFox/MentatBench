# Mentat-Hard v3 test matrix: pre-registration, 2026-10-30

This document freezes the protocol before any model is run on the v3 **test** split or the v3 private split. The numbers are in
`mentat-hard-v3-prereg-20261030.json`; the spec is in `docs/mentat_hard.md`.

## What changed from v2

- **The questions are answerable from the text.** v2's validity audit found that groups and second readings could not be
  answered, because the league rules were never stated. Each league now has a plain-English **rule card**, carried in the
  corpus field `context` (#71, #75). System One child v7 shows the card with relation, group and reading questions, but not
  with bindings (#74, #78).
  - Two blind judges passed every part of the v3 audit (#76).
  - They also passed every part of the v3.1 audit (#80), after round 1 exposed an ambiguous alt-reading instruction, which
    child v7 fixed.
- **Harder relations (v3.1, #79).** NONE drops to 25% and is mostly made of same-league pairs that share rule wording.
  Derived links come from the deepest tier first. Every relation label carries an inference `depth`, which an independent
  audit checks.
- **Size.** 6,000 relations per split, down from 10,000, because the card doubles request cost and there is no prefix
  reuse. The owner chose this to keep 27B cells at or under 80 minutes.
- **Headline (#77, decided on dev).** The mean of relation MCC and group MCC. Binding MCC is secondary. Reading MCC and Q_N
  are diagnostics. v1 and v2 keep the relation-MCC headline, so v2 and v3 numbers are not comparable.
- **Fifteen models.** Seven are new (#72): d3 2b/4b, d1 3b, d1-omni 600m, nimble-v3 9b, tev1 0.8b/4b. d1:2b and
  d1-omni:2b were skipped because they need a newer Ollama (owner decision). strands-decider:2b only runs on MLX.
- **Private split v3.1.** New secret seed, one draw, self-checks clean. Only its content hash is public.

## Frozen setup

- **Code:** `main` at 9ad5f2c (v3.1 registered, #81). The dev cells below ran at f599bfa, which differs only by the
  registration commit.
- **Child:** `systemone-choice` v7, `prompt_sha256` `d6d5ed56…`.
  - One question per request.
  - Rule cards in the relation, group and reading states.
  - The child records card-driven abstentions in `MB_S1_STATS`.
- **Servers:** Ollama 0.40.11, two servers (one per RTX A6000, CUDA only), `OLLAMA_NUM_PARALLEL=2`, `MB_S1_PARALLEL=2`.
- **Fixtures** (all registered or sealed):
  - test: `mentat_hard_test` v3 (fixture version 3.1), `7b408459…`;
  - dev: `mentat_hard_dev` v3, `218003c6…`;
  - private: `mentat_hard_private` v3.1, sealed, `1253bb5d…`.
- **Per cell:**
  - Command: `runcell --fixture <split> --candidates <split>/candidates.jsonl --profile profiles/mentat-hard-v1.json
    --local-protocol 2 --top-k 24 --batch-size 128 --relation-floor <floor below>`.
  - Deadline: 5,400 s. Target: 27B cells ≤ 80 min.
  - One run per model.
- **Baselines on test:**
  - On CPU: empty, lexical, embedding, rules (heuristic child), oracle (full replay, no `--candidates`), and gold_view with
    `--candidates` (perfect curated answers; headline 1.0 and Q_N 100 expected).
  - On GPU, for release gate 10: the hf pipeline (Qwen2.5-1.5B-Instruct).
- **Scores:**
  - Headline: the mean of relation and group MCC on raw (unfloored) decisions.
  - Secondary: binding MCC.
  - Diagnostics: reading MCC, relation MCC by depth and on held-out vs seen wording, Q_N and strict Q_N.
  - Speed: build wall time.
  - CIs and paired gaps to the reference, D3 27B (`league_ci.py`, layout `relation-group-headline/1`, 18 leagues, B = 2000,
    seed 0, 95% percentile intervals).
- **Private split:** all 15 models plus gold_view and the oracle, run after the test matrix with the same floors. The
  private files never leave `mentat-bench-private`.

## Floors and dev scores (dev v3.1, child v7, exact precision ≥ 0.8)

| Model | Dev headline | Rel | Grp | Bind | Read | Dev build | Floor |
|---|---|---|---|---|---|---|---|
| d3:27b-q8_0 (reference) | 0.504 | 0.771 | 0.236 | 0.000 | 0.884 | 74 min | 0.00106 (keeps all) |
| d3:9b-q8_0 | 0.294 | 0.470 | 0.118 | 0.293 | 0.542 | 22 min | 0.7373 |
| pplx-decider:27b | 0.264 | 0.504 | 0.024 | 0.000 | 0.645 | 74 min | 1.3451 |
| kev:9b-bf16 | 0.253 | 0.344 | 0.163 | 0.051 | 0.311 | 35 min | 1.2282 |
| d3:4b-q8_0 | 0.237 | 0.408 | 0.067 | 0.000 | 0.334 | 15 min | 1.3813 |
| clef:27b-q8_0 | 0.204 | 0.402 | 0.007 | 0.138 | 0.525 | 78 min | 1.6063 |
| nimble-v3:9b-q8_0 | 0.191 | 0.272 | 0.111 | 0.066 | 0.388 | 28 min | 4.5717 |
| nimble:9b-bf16 | 0.181 | 0.288 | 0.075 | 0.312 | 0.383 | 27 min | 4.2406 |
| clef-flash:9b | 0.173 | 0.265 | 0.082 | 0.167 | 0.312 | 26 min | 3.0817 |
| tev1:4b-q8_0 | 0.151 | 0.318 | −0.016 | −0.025 | 0.294 | 17 min | 5.3921 |
| d3:2b-q8_0 | 0.082 | 0.195 | −0.030 | −0.021 | 0.371 | 7 min | 4.3787 |
| d1:3b-q8_0 | 0.079 | 0.102 | 0.054 | 0.179 | 0.318 | 8 min | 3.8493 |
| tev1:0.8b-q8_0 | 0.006 | 0.013 | 0.000 | 0.000 | 0.000 | 7 min | none reaches 0.8: names no relations |
| d1-omni:600m-q8_0 | −0.001 | 0.011 | −0.012 | 0.050 | 0.000 | 5 min | none reaches 0.8: names no relations |
| laya:latest | −0.005 | 0.034 | −0.044 | −0.030 | 0.000 | 3 min | 4.4989 (abstains, see limits) |

- **Rules baseline:** 0.125 on the dev headline (relation 0.250, group 0.0).
- **Floors:** the exact values are in the JSON, and the harness receives them at `repr` precision.
- **Determinism:** a dev repeat of d3:9b on the other GPU gave byte-identical decisions and scores.
- **Earlier dev runs:** the v3 spike (`runs/20261030-v3-spike`, child v6, v3 relations) is superseded, as are the v3.1 cells
  run on child v6, which were stopped after about 35 minutes. Neither is used.

## Disclosed limits

- **Gold-informed candidates.** Candidate selection uses gold, so blocking is not tested. The data is a synthetic single
  domain, and the test gold is public.
- **Card parameters.** Some card bounds are conditioned on gold: the M range, the conditional being the gold pair about half
  the time, q, seed_min, chain order and the winner bound a (`docs/mentat_hard.md`). Each is a bound the theory needs, not
  a value.
- **Surface cues.** The gloss on a decoy proposition marks the decoy stratum perfectly. That is by design: a glossed phrase
  has one reading. Cross-league NONE pairs (about 3.7% of NONE) are always NONE. The rules baseline relation MCC fell from
  0.305 (v3) to 0.250 (v3.1).
- **Held-out share on test** is 38.9% of relations, above the ~30% target.
- **Small context.** laya's context is 512 tokens. With the card, 5,552 of its dev requests were refused for length and
  counted as abstentions, so laya effectively cannot take this exam. tev1:0.8b and d1-omni:600m answer, but at chance, and
  name no relations under the floor rule.
- **Bindings.** Binding MCC is 0.000 for d3:27b, pplx and d3:4b: they never flag an ambiguous proposition. Bindings get no
  card, so this is comparable with v2.
- **Post-hoc changes, all on dev before any test run:** v3.1 (harder relations), the relation + group headline and child v7
  (the audit fix). Each is an owner decision, recorded in its PR.
- **Judges are models.** The answerability audits use two Haiku 5.5 judges, not people.
- **Intervals.** With 18 clusters, the bootstrap intervals somewhat under-cover. Choosing the reference on dev is not
  corrected for in the paired gaps.
- **End-to-end score.** Every model build needs a repair, so strict Q_N is expected to be 0. The decision MCCs are the
  meaningful scores.

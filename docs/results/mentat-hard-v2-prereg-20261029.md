# Mentat-Hard v2 test matrix: pre-registration, 2026-10-29

This document freezes the protocol before any model is run on the v2 **test** split. The numbers are in
`mentat-hard-v2-prereg-20261029.json`; the spec is in `docs/mentat_hard.md`.

## What changed from v1
- **Curated questions cover the full theory.** A model answering every curated question perfectly (gold_view) now scores
  Q_N = 100.00 on dev and test, as does the oracle. In v1 the same model scored 74.77. Four changes did this:
  - reading questions for ambiguous propositions;
  - no forced outcomes;
  - every rule edge and counting set asked;
  - full-bound group labels.

  The slow test `tests/test_mentat_hard_v2_gold_view.py` checks it.
- **Headline:** the mean of **four** per-op MCCs (relations, groups, bindings, readings). `mcc_mean_3` (without readings)
  is reported for comparison, but v1 and v2 headlines are not comparable: the fixtures, question mix and group classes all
  differ.
- **Confidence intervals:** a league bootstrap (`tools/eval/league_ci.py`, 18 leagues, B = 2000, seed 0, 95%
  percentile), with paired gaps to the reference model.
- **Reference model:** D3 27B, the best dev headline. It is labelled as the reference next to the perfect-answer ceiling
  (gold_view). There is no extra run.

## Frozen setup
- **Code:** `main` at b8990f1. The dev calibration below ran at this commit.
- **Child:** `systemone-choice` v5, `prompt_sha256` 15c9c1ce….
  - One question per request.
  - Group bound follow-ups.
  - Reading questions (base and alt).
  - The v4 binding wording, which names "by the break" and "top half", is kept. This is disclosed, and the hint is the
    same for every model.
- **Servers:** Ollama 0.40.11, two servers (one per RTX A6000, CUDA only), `OLLAMA_NUM_PARALLEL=2`,
  `MB_S1_PARALLEL=2`.
- **Fixtures:** `mentat_hard_test` v2 (1f4613be…). Dev calibration used `mentat_hard_dev` v2 (09da4937…). Both are
  registered and regenerate byte for byte.
- **Per cell:**
  - Command: `runcell --fixture fixtures/mentat_hard_test --candidates fixtures/mentat_hard_test/candidates.jsonl
    --profile profiles/mentat-hard-v1.json --local-protocol 2 --top-k 24 --batch-size 128 --relation-floor <floor below>`.
  - Budget: 1.5 h for every model.
  - One run per model. The dev repeats of laya and d3:9b were byte-identical: decisions, relations, groups and
    score_decisions.
- **Baselines on test:**
  - empty;
  - rules (heuristic child v3, never tuned on test);
  - oracle (full replay, no `--candidates`);
  - gold_view with `--candidates` (perfect curated answers; MCC 1.0 and Q_N 100 expected).
- **Scores:**
  - Headline: the 4-part MCC on raw (unfloored) decisions.
  - Secondary: `mcc_mean_3`, per-op MCC, macro-F1, relation Brier/ECE, held-out vs seen wording, and Q_N and strict Q_N
    from the floored compile.
  - Speed: build wall time.
  - CIs and paired gaps to D3 27B for the headline, each op, held-out relations and Q_N.

## Floors and dev scores (dev, exact precision ≥ 0.8)

| Model | Dev MCC | Rel | Grp | Bind | Read | Dev build | Floor |
|---|---|---|---|---|---|---|---|
| d3:27b-q8_0 (reference) | 0.383 | 0.697 | 0.202 | 0.000 | 0.635 | 46 min | 0.000471 (keeps all) |
| clef:27b-q8_0 | 0.382 | 0.454 | 0.157 | 0.374 | 0.542 | 58 min | 1.9683 |
| d3:9b-q8_0 | 0.377 | 0.557 | 0.103 | 0.213 | 0.635 | 16 min | 0.8569 |
| nimble:9b-bf16 | 0.335 | 0.394 | 0.122 | 0.287 | 0.536 | 21 min | 4.9401 |
| pplx-decider:27b | 0.327 | 0.521 | 0.139 | 0.000 | 0.649 | 48 min | 1.2975 |
| clef-flash:9b | 0.293 | 0.387 | 0.205 | 0.060 | 0.521 | 20 min | 3.1696 |
| kev:9b-bf16 | 0.209 | 0.404 | 0.017 | 0.013 | 0.402 | 19 min | 1.3902 |
| laya (421M) | 0.090 | 0.056 | 0.005 | −0.016 | 0.316 | 4 min | none reaches 0.8: names no relations |

The rules baseline scores 0.096 on dev (Q_N 33.6, not repaired). The exact floor values are in the JSON, and the harness
receives them at `repr` precision. The dev Q_N of every model build is 10–16 on the repaired base; strict Q_N is 0.

## Pilot record (dev)
The pilot on 2026-10-29 met all three bars:

| Bar | Result | Required |
|---|---|---|
| D3 27B headline | 0.383 | ≤ 0.80 |
| Rules headline | 0.096 | ≤ 0.50 |
| D3 27B − Laya | 0.293 | ≥ 0.25 |

D3 27B took 46 min; the budget is 90 min. The owner accepted the pilot on 2026-10-29.

## Disclosed limits
- **Gold-informed candidates.** Candidate selection uses gold, so blocking is not tested. The data is a synthetic single
  domain, and the test gold is public.
- **Wording cue.** Ambiguous propositions use the generator's ambiguous phrases. A phrase regex finds the reading stratum
  0.73–0.85 of the time. The owner accepted this: spotting ambiguous wording is part of the task.
- **Reading structure and repeats.** The reading-question structure is at chance; an option-only classifier does no
  better. However, every reading row has two options on u's team and two on other teams, so dropping the other-team pair
  is easy. On dev, D3 27B repeats its literal answer for the second reading on 84 of 94 ambiguous rows, so it almost
  never identifies a second reading.
- **Shorter chains.** Chains are 2 members (owner-accepted), because longer chains force outcomes.
- **Test decoys.** On test, 2 of 46 ambiguous propositions have no matching decoy, giving 180 reading rows against 188 on
  dev.
- **End-to-end score.** Every model build needs a repair, so strict Q_N is expected to be 0 and repaired Q_N stays low.
  The decision MCC is the meaningful score.
- **Intervals.** With 18 clusters, the bootstrap intervals somewhat under-cover. Choosing the reference on dev is not
  corrected for in the paired gaps.

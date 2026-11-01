# Mentat-Hard v2 private-split results, 2026-10-29

> **Superseded (2026-10-30).** This is the v2 private record. The current private-split results are Mentat-Hard v3 (fixture 3.1):
> `mentat-hard-v3-private-20261030.md`, with the v3 test results in `mentat-hard-v3-20261030.md`. The v2 numbers are kept as a record.

These are the results of the eight System One models and the baselines on the **private test split**. The split is held
outside the repository, and only its content hash is public: `0f687621845d48e9404f7fd2d3c801988dd55558c35bb6a625850f8a2707a8bd`
(the commitment is in `docs/release.md` §7). The scores are published; the split, its seed and the run directories are not.
The numbers are in `mentat-hard-v2-private-20261029-ci.json`.

## Setup
- **Split:** `mentat_hard_private`, built by the v2 generator (`fixtures_hard` version "2", the same as the public splits)
  from a secret CSPRNG seed, with the test split's settings.
  - It has 18 leagues, 1,416 markets, 2,832 outcomes, 14,002 curated questions and a 200-query exam.
  - Its self-checks are clean: 0 uncovered, 0 forced outcomes.
  - Two seeds were drawn; the first was rejected by the generator's self-audit (solver budget).
- **Protocol:** exactly the pre-registered test protocol (`mentat-hard-v2-prereg-20261029.md`).
  - The same child v5 (prompt 15c9c1ce…), the same per-model relation floors (every floor equals the pre-registered
    string), and the same flags, profile and 1.5 h budget.
  - One run per model. Every build completed: the slowest, Clef 27B, took 59 min of build wall time (60 min for its build step, setup
    included).
  - The runs recorded commits 2b13715, 79305ab and eaf5537 (the run worktree gained doc-only commits during the queue;
    79305ab and eaf5537 are on branch `w9/audit`, and this branch carries patch-identical copies of them). Their code,
    profiles and fixtures are identical (`git diff 2b13715 eaf5537 -- python tools profiles fixtures` is empty).
- **Headline (post-hoc, see `mentat-hard-v2-validity-audit-20261029.md`):** relation MCC, with binding MCC as a secondary
  score. Group and reading MCC and Q_N are diagnostics, not scores.
- **Intervals:** league bootstrap, 18 leagues, B = 2000, seed 0, 95% percentile. The gaps are D3 27B minus the run.
  `tools/eval/league_ci.py --sealed <hash>` accepts the split only at its committed hash.

## Results (private split)

| Run | Relation MCC (headline) | Gap to D3 27B | Binding MCC (secondary) | Public-test relation MCC |
|---|---|---|---|---|
| gold_view (perfect answers) | 1.000 | −0.299 [−0.318, −0.277] | 1.000 | 1.000 |
| **D3 27B** (reference) | **0.701** [0.682, 0.723] | — | 0.000 [0.000, 0.000] | 0.704 |
| D3 9B | 0.551 [0.528, 0.575] | 0.151 [0.136, 0.165] | 0.080 [−0.010, 0.226] | 0.548 |
| Pplx Decider 27B | 0.534 [0.512, 0.562] | 0.168 [0.151, 0.183] | 0.000 [0.000, 0.000] | 0.538 |
| Clef 27B | 0.454 [0.437, 0.473] | 0.247 [0.232, 0.262] | 0.309 [0.141, 0.452] | 0.462 |
| Kev 9B | 0.395 [0.380, 0.413] | 0.307 [0.291, 0.323] | 0.033 [−0.023, 0.103] | 0.399 |
| Clef-Flash 9B | 0.392 [0.367, 0.417] | 0.310 [0.289, 0.331] | 0.022 [−0.026, 0.104] | 0.419 |
| Nimble 9B | 0.389 [0.370, 0.409] | 0.312 [0.296, 0.329] | 0.148 [0.000, 0.241] | 0.411 |
| Rules | 0.280 [0.269, 0.291] | 0.422 [0.403, 0.440] | 0.000 [0.000, 0.000] | 0.278 |
| Laya 0.4B | 0.058 [0.046, 0.069] | 0.644 [0.625, 0.663] | 0.010 [−0.028, 0.044] | 0.057 |

Diagnostics (not scores):
- **Q_N (repaired base):** 11.8–17.6 for the models, rules 34.2, empty 17.6, oracle and gold_view 100. Strict Q_N is 0
  for every model, because every model build was repaired.
- **Group and reading MCC:** in the CI JSON's `runs[*].metrics` and `gaps` (its `roles` key marks them diagnostic).

## Findings
1. **The public results transfer.** Every model's relation MCC on the private split is within 0.03 of its public-test
   value. The largest drop is Clef-Flash 9B (0.419 to 0.392). The order matches, except among the three lower 9B models,
   which are within 0.006 of each other here.
2. **D3 27B leads alone,** with every gap interval excluding 0. The gap to perfect answers is 0.299.
3. **D3 9B and Pplx Decider 27B are second and third.** Their intervals overlap with each other, and both are ahead of
   Clef 27B.
4. **The binding scores move between splits** (D3 9B: −0.009 public, 0.080 private; Nimble: 0.000 to 0.148), as expected
   from a score resting on a few flagged items. Clef 27B is the only model with a stable, clearly positive binding score
   (0.310 public, 0.309 private).

## Limits
- One run per model; the runs are deterministic, so the intervals cover item sampling only. With 18 clusters, the
  intervals somewhat under-cover.
- The split is synthetic and in a single domain, from the same generator as the public splits. It measures transfer
  to new items, not to new domains.
- The relation headline was chosen after the public test results were known (post-hoc). This private run applies it to
  data no one had seen.

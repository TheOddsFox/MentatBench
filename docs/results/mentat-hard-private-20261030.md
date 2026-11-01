# Private-split results (Mentat-Hard test set, revision 3.1), 2026-10-30

> Results bundle (website entry point, leaderboard JSON and CSV): [results/README.md](../../results/README.md).

These are the results of the 15 System One models on the private test split, `mentat_hard_private` (test set revision 3.1), with the
rules baseline and the gold_view ceiling. The split is held outside the repository, and only its content hash is public:
`1253bb5dfee2c02a34ded7e659a0158dc8996be9660d7be0ee14ffba19077263`. The scores are published; the split, its seed and the run
directories are not. The numbers are in `mentat-hard-private-20261030.json`, and the league bootstrap output is in
`mentat-hard-private-20261030-ci.json` (a copy of the `league_ci` output for the sealed split; it holds the sealed name and hash only).

The public test results are in `mentat-hard-20261030.md` and `mentat-hard-20261030.json`.

## Setup

- **Split:** `mentat_hard_private`, built by the revision 3.1 generator (`fixtures_hard` round 3.1) from one seed drawn with a CSPRNG (per the private build log).
  The seed is not recorded anywhere in the repository. The split has 3,228 outcomes, 10,406 candidates and a 200-query exam.
  Each model cell scores 10,406 decisions: 6,000 relation questions, 1,178 group questions (1,000 group and 178 reading questions,
  each reading question having two items, 356 in all) and 3,228 binding outcomes. See Limits, item 3, for how this differs from
  the public test split.
- **Protocol:** the pre-registered round 3 test protocol (`prereg.md`).
  - Code: the 15 model cells record commit 0bff47f, a clean tree (from each cell's repo record). The prereg names 9ad5f2c; as in
    the public test, 0bff47f adds only the two prereg files on top of it. The rules, gold_view and oracle cells record
    caec42d, which differs from 0bff47f only in docs (`python`, `tools`, `profiles` and `fixtures` are unchanged).
  - Child: `systemone-choice` version 7, prompt sha256 `d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44` (the
    pre-registered value). Checked against each private cell's build policy: all 15 record child version 7, this prompt
    hash and their pinned model digest.
  - Relation floors: each of the 15 model cells uses the floor the prereg lists for it. All 15 match the prereg and the public
    test (checked against the cells' build flags).
  - Build flags: the same as the public test cells, including `--local-timeout 600.0` and `--hello-timeout 900.0`, which the
    pre-registered command does not list. Profile `mentat-hard-v1.json`, build deadline 5,400 s.
  - One run per model. All 15 model cells, the rules baseline and gold_view are `complete`, with no incomplete builds and no failed
    steps. Across the 15 model cells, the child reported no item errors, no refused instructions and no 500 retries, and every
    model cell has decision coverage of 1.0.
- **Headline:** the mean of relation MCC and group MCC on raw (unfloored) decisions, as pre-registered. Binding MCC is secondary.
  Reading MCC, held-out relation MCC and Q_N are diagnostics and are not discussed here.
- **Intervals:** league bootstrap, 18 leagues, B = 2000, seed 0, 95% percentile intervals (`league_ci`). The paired gaps are
  reference minus run, with the reference D3 27B (chosen on dev, as pre-registered).

## Results (private split)

Headline = relation and group MCC (mean). Gap = D3 27B minus the run, paired by bootstrap draw. Binding is the secondary score.

| Run | Headline: relation + group MCC [95% CI] | Relation MCC [95% CI] | Group MCC [95% CI] | Binding MCC, secondary [95% CI] | Gap to D3 27B [95% CI] |
|---|---|---|---|---|---|
| **D3 27B** (reference) | 0.525 [0.504, 0.546] | 0.777 [0.757, 0.798] | 0.272 [0.237, 0.312] | 0.000 [0.000, 0.000] | reference |
| D3 9B | 0.273 [0.257, 0.293] | 0.441 [0.420, 0.467] | 0.104 [0.084, 0.127] | -0.007 [-0.010, 0.000] | 0.252 [0.229, 0.276] |
| Pplx Decider 27B | 0.260 [0.246, 0.277] | 0.497 [0.478, 0.520] | 0.023 [-0.004, 0.050] | 0.000 [0.000, 0.000] | 0.265 [0.247, 0.284] |
| D3 4B | 0.227 [0.213, 0.241] | 0.376 [0.353, 0.401] | 0.078 [0.051, 0.106] | 0.000 [0.000, 0.000] | 0.298 [0.274, 0.322] |
| Clef 27B | 0.223 [0.204, 0.244] | 0.388 [0.364, 0.415] | 0.059 [0.036, 0.081] | 0.094 [0.003, 0.197] | 0.301 [0.278, 0.324] |
| Kev 9B | 0.215 [0.197, 0.233] | 0.309 [0.287, 0.335] | 0.121 [0.097, 0.145] | -0.011 [-0.035, 0.033] | 0.310 [0.290, 0.331] |
| Nimble-v3 9B | 0.194 [0.174, 0.216] | 0.255 [0.220, 0.290] | 0.132 [0.107, 0.156] | -0.026 [-0.030, -0.021] | 0.331 [0.307, 0.356] |
| Nimble 9B | 0.165 [0.153, 0.179] | 0.278 [0.263, 0.292] | 0.053 [0.032, 0.074] | 0.000 [0.000, 0.000] | 0.359 [0.332, 0.386] |
| Clef-Flash 9B | 0.147 [0.134, 0.159] | 0.231 [0.210, 0.251] | 0.062 [0.044, 0.078] | -0.017 [-0.021, -0.014] | 0.378 [0.353, 0.405] |
| TEV1 4B | 0.129 [0.105, 0.153] | 0.283 [0.253, 0.316] | -0.024 [-0.056, 0.010] | -0.007 [-0.042, 0.025] | 0.395 [0.369, 0.424] |
| D1 3B | 0.059 [0.048, 0.072] | 0.093 [0.074, 0.115] | 0.024 [0.004, 0.045] | 0.072 [-0.017, 0.173] | 0.466 [0.444, 0.488] |
| D3 2B | 0.065 [0.043, 0.088] | 0.161 [0.137, 0.188] | -0.031 [-0.064, 0.002] | -0.024 [-0.055, 0.012] | 0.460 [0.427, 0.488] |
| TEV1 0.8B | 0.003 [-0.001, 0.008] | 0.006 [-0.001, 0.015] | 0.000 [0.000, 0.000] | 0.017 [0.014, 0.021] | 0.521 [0.501, 0.544] |
| D1-Omni 600M | 0.005 [-0.004, 0.015] | 0.011 [-0.001, 0.024] | -0.001 [-0.014, 0.011] | 0.039 [0.024, 0.050] | 0.520 [0.499, 0.542] |
| Laya 0.4B | -0.008 [-0.026, 0.011] | 0.015 [-0.009, 0.037] | -0.030 [-0.061, 0.004] | 0.038 [0.005, 0.062] | 0.533 [0.507, 0.558] |
| *Rules (heuristic child)* | *0.139 [0.115, 0.159]* | *0.248 [0.226, 0.273]* | *0.030 [0.000, 0.054]* | *0.000 [0.000, 0.000]* | *0.385 [0.364, 0.414]* |
| *gold_view (perfect curated answers; ceiling)* | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *1.000 [1.000, 1.000]* | *−0.475 [−0.496, −0.454]* |

- The Rules row is the pre-registered heuristic child. Lexical, embedding and hf have no private cells in the summary copies, so
  they are not in this table.
- Empty was not run on the private split. The oracle has Q_N only (100.0, from its `score.json`), with no decisions and no interval.

## Private versus public test

Public values are from `mentat-hard-20261030.json`. Differences are private minus public, computed from unrounded values.

| Run | Public test headline [95% CI] | Private headline [95% CI] | Headline difference | Relation difference | Group difference | Binding difference |
|---|---|---|---|---|---|---|
| **D3 27B** (reference) | 0.519 [0.497, 0.541] | 0.525 [0.504, 0.546] | +0.0062 | -0.0114 | +0.0238 | +0.0000 |
| D3 9B | 0.275 [0.251, 0.300] | 0.273 [0.257, 0.293] | -0.0021 | -0.0203 | +0.0160 | -0.0067 |
| Pplx Decider 27B | 0.253 [0.239, 0.269] | 0.260 [0.246, 0.277] | +0.0073 | -0.0145 | +0.0292 | +0.0000 |
| D3 4B | 0.233 [0.216, 0.251] | 0.227 [0.213, 0.241] | -0.0057 | -0.0169 | +0.0054 | +0.0000 |
| Clef 27B | 0.207 [0.186, 0.231] | 0.223 [0.204, 0.244] | +0.0164 | -0.0051 | +0.0380 | -0.1191 |
| Kev 9B | 0.236 [0.215, 0.258] | 0.215 [0.197, 0.233] | -0.0209 | -0.0158 | -0.0260 | +0.0007 |
| Nimble-v3 9B | 0.201 [0.189, 0.215] | 0.194 [0.174, 0.216] | -0.0079 | -0.0218 | +0.0060 | -0.0008 |
| Nimble 9B | 0.178 [0.167, 0.190] | 0.165 [0.153, 0.179] | -0.0129 | +0.0058 | -0.0316 | -0.1454 |
| Clef-Flash 9B | 0.172 [0.163, 0.181] | 0.147 [0.134, 0.159] | -0.0250 | -0.0193 | -0.0307 | -0.0361 |
| TEV1 4B | 0.144 [0.126, 0.163] | 0.129 [0.105, 0.153] | -0.0148 | -0.0171 | -0.0124 | -0.0160 |
| D1 3B | 0.071 [0.058, 0.087] | 0.059 [0.048, 0.072] | -0.0123 | -0.0218 | -0.0028 | +0.0000 |
| D3 2B | 0.055 [0.042, 0.069] | 0.065 [0.043, 0.088] | +0.0098 | -0.0095 | +0.0291 | -0.0022 |
| TEV1 0.8B | -0.000 [-0.005, 0.005] | 0.003 [-0.001, 0.008] | +0.0033 | +0.0066 | +0.0000 | +0.0442 |
| D1-Omni 600M | -0.003 [-0.010, 0.004] | 0.005 [-0.004, 0.015] | +0.0083 | -0.0038 | +0.0203 | -0.0056 |
| Laya 0.4B | -0.020 [-0.032, -0.007] | -0.008 [-0.026, 0.011] | +0.0122 | +0.0309 | -0.0065 | +0.0733 |
| *Rules (heuristic child)* | *0.140 [0.121, 0.153]* | *0.139 [0.115, 0.159]* | *-0.0012* | *-0.0021* | *-0.0003* | *+0.0000* |

- **Headlines move little.** Every model's headline is within 0.025 of its public value (largest: Clef-Flash 9B, -0.025). The
  headline is the mean of relation and group, so relation and group changes partly offset each other (D3 27B: relation -0.011,
  group +0.024).
- **Parts move more than the headline.** Relation differences are within 0.022 for all models except Laya (+0.031). Group
  differences reach 0.032 (Nimble 9B) and are largest for Clef 27B at +0.038, followed by Nimble 9B at -0.032. Group differences
  are larger in size than the relation difference for 8 of the 15 models.
- **Binding moves the most, and it is the least stable score.** Nimble 9B (0.145 to 0.000) and Clef 27B (0.213 to 0.094) drop the most.
  Laya's binding point rises from -0.036 to 0.038. See Binding below.

## Do the order and the resolved gaps carry over?

- **The top of the table does.** D3 27B is first on the headline in both splits, and its interval (0.504 to 0.546) lies above
  every other model's (the highest upper bound among them is 0.293). Its gap to every other model (and to the rules baseline)
  excludes 0 in the private split too. The smallest gap is to D3 9B, 0.252 [0.229, 0.276] (0.244 publicly). Relation MCC also
  puts D3 27B first (0.777, against 0.497 for Pplx Decider 27B).
- **The top three keep their order.** D3 27B, D3 9B and Pplx Decider 27B are the top three in both splits.
- **The middle reorders, within overlapping intervals.** Across the 16 runs compared (15 models and the rules baseline), the two
  orders have a Spearman correlation of 0.98. Of the 120 pairs, 97 have disjoint intervals in both splits, and none of them changes
  order. The other 23 pairs overlap in both splits (13) or are disjoint in only one split (10). Five pairs are disjoint only
  privately: D3 9B against D3 4B and against Kev 9B, Pplx against D3 4B and against Kev 9B, and Clef 27B against Nimble 9B. Five are
  disjoint only publicly: D3 4B against Nimble-v3 9B, Nimble 9B against TEV1 4B and against the rules baseline, Clef-Flash 9B against
  the rules baseline, and TEV1 0.8B against Laya. Overlap is not evidence of a tie.
- **Some swaps are between near-zero runs.** D1 3B and D3 2B swap places (both intervals overlap in both splits), and so do D1-Omni
  600M and TEV1 0.8B. The D3 family curve keeps its order: 2B 0.065, 4B 0.227, 9B 0.273, 27B 0.525.
- **Some public statements do not carry over.** Publicly, nine models had headline intervals entirely above the rules interval. In
  the private split seven do (D3 27B, D3 9B, Pplx Decider 27B, D3 4B, Clef 27B, Kev 9B and Nimble-v3 9B; the rules interval is
  0.115 to 0.159). Nimble 9B and Clef-Flash 9B now overlap it. Laya's headline interval was entirely below 0 publicly (upper bound
  -0.007). Privately it is [-0.026, 0.011], which includes 0.
- **Runs that include 0 still do.** TEV1 0.8B ([-0.001, 0.008]) and D1-Omni 600M ([-0.004, 0.015]) have headline intervals that
  include 0, as they did publicly.
- **Group second place is not resolved.** D3 27B leads groups (0.272 [0.237, 0.312]). Privately, second place is Nimble-v3 9B
  (0.132 [0.107, 0.156]), with Kev 9B third (0.121 [0.097, 0.145]). Publicly, second place is Kev 9B (0.147 [0.121, 0.175]), with
  Nimble-v3 9B third (0.126 [0.106, 0.147]). The two intervals overlap in each split.

## Laya abstentions

Laya's child abstained for length on 3,760 of 6,000 relation questions (62.7%) and on 946 of 1,178 group questions (80.3%)
(`child_stats.json`). Its binding answers had no length abstentions. Publicly it abstained on 4,981 of 6,000 relation questions (83%) and on
1,032 of 1,184 group questions (87%). As pre-registered, an abstention is scored as a NONE with no confidence, so Laya's relation
score counts these abstentions as answers. The split of its correct NONE decisions into abstentions and judgements (1,198 of 1,202
publicly) needs the decisions file, which is not in the summary copies, so it is not computed here.

## Binding (secondary)

- Clef 27B has the highest binding point privately (0.094 [0.003, 0.197]), as it did publicly (0.213 [0.108, 0.318]). Its
  point falls to under half, and the lower bound is close to 0.
- Nimble 9B's public binding (0.145 [0.000, 0.258], from two flags) is 0.000 privately, with a degenerate interval.
- D1-Omni 600M's binding is 0.039 [0.024, 0.050] privately (0.045 [0.038, 0.052] publicly). TEV1 0.8B's binding interval excludes
  0 privately (0.017 [0.014, 0.021]) and includes 0 publicly (-0.027 [-0.075, 0.015]).
- D3 27B, D3 9B (-0.007 [-0.010, 0.000]), D3 4B and Pplx have binding MCC at or near 0, as they did publicly.

## Wall times

Build wall time = `build_summary.json` `wall_seconds`, the same measure as the public table. The 15 model cells ran on two
servers, with at most two cells at a time (12 overlapping pairs in the progress log). The log starts the first cell at 17:41 and
records the last as done at 21:35 (log clock), a span of 3 h 54 min.

| Run | Private build wall time (min) | Public test (min) |
|---|---|---|
| D3 27B | 73.9 | 74.6 |
| Pplx Decider 27B | 74.1 | 74.4 |
| Clef 27B | 76.9 | 78.7 |
| Kev 9B | 33.4 | 34.9 |
| Nimble-v3 9B | 27.9 | 28.3 |
| Nimble 9B | 27.0 | 27.7 |
| Clef-Flash 9B | 26.1 | 26.4 |
| D3 9B | 21.7 | 21.9 |
| TEV1 4B | 16.9 | 17.2 |
| D3 4B | 15.3 | 15.4 |
| D1 3B | 8.3 | 8.5 |
| D3 2B | 6.8 | 6.9 |
| TEV1 0.8B | 6.5 | 6.6 |
| D1-Omni 600M | 4.7 | 4.7 |
| Laya 0.4B | 2.7 | 2.4 |

All three 27B cells finished inside the 5,400 s build deadline (Clef 27B at 76.9 min was the slowest, against the 80-minute target).

## Private commitment

- **Hash:** `1253bb5dfee2c02a34ded7e659a0158dc8996be9660d7be0ee14ffba19077263` (content hash, sealed). The bootstrap was run with `--sealed` at this hash, and the CI file records it.
- **Generator:** `fixtures_hard` round 3.1, the same generator version as the public test split.
- **Seed:** per the private build log, one seed was drawn, from a CSPRNG. It is not recorded in the repository, the results or the CI file.
- **Self-checks:** 0 uncovered (`score.json`, anchor and active families, every cell). Per the private build log, there are also
  0 constants and no audit failures.
- **Size:** 3,228 outcomes, 10,406 candidates, 200 queries.
- **Registry note:** each private cell's fixture stamp carries an error note. The lookup found no registered fixture version with
  this content hash, so the stamp keeps only the content hash, which matches the sealed commitment. I did not check whether the
  public cells carry the same note.

## Limits

1. **One run per model.** The intervals cover item and league sampling only, not run-to-run variation.
2. **Few clusters.** With 18 leagues, the bootstrap intervals somewhat under-cover, as in the public test.
3. **Different split.** The private split is built by the same generator as the public test, but its composition differs: 3,228
   binding outcomes (public 3,348), 1,178 group questions with 178 readings (public 1,184 with 184), and 2,352 held-out relations
   (public 2,332). It measures transfer to new items from the same generator, not to new domains.
4. **Pre-registered choices applied as they were.** The headline and the reference were chosen on dev. This report applies them to
   the private split and does not change any score.
5. **Laya's abstention scoring** is the pre-registered one, and it drives most of Laya's NONE accuracy (see Laya abstentions).
6. **Secondary binding** is fragile across the two splits (see Binding).
7. **Child identity** was checked by the coordinator against each private cell's build policy in the private run directory,
   not from the summary copies: all 15 record child
   version 7, prompt hash `d6d5ed56…` and their pinned model digest.
8. **Commitment facts** (the CSPRNG draw, one seed, 0 constants and no audit failures) are from the coordinator's private build log,
   not from the summary copies, which do not contain the seed or the generator log.
9. **Baselines.** The pre-registration lists the 15 models plus gold_view and the oracle for the private split; the rules
   baseline was added. Lexical, embedding and hf were not run on the private split, so the private table compares the models
   only with the rules baseline and the ceiling.
10. **Diagnostics not analysed.** Reading MCC, held-out relation MCC and Q_N are in the CI file's diagnostics, and this report does
    not discuss them.

## Files

- `docs/results/mentat-hard-private-20261030.json`: the aggregate numbers behind this report (headline, parts, gaps, public
  comparison, order analysis, Laya counts, wall times, checks).
- `docs/results/mentat-hard-private-20261030-ci.json`: the league bootstrap output for the sealed split (sealed name and hash only).
- Test results this report compares against: `docs/results/mentat-hard-20261030.md` and `.json`.

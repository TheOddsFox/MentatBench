# Reasoning reference for v0.2.0: results

Run on 2026-11-03 under [`PREREG.md`](PREREG.md), which was merged before the first call. **Exploratory and unranked.** This
is not a leaderboard entry and changes no score. It is outside the System One scope: the System One models answer in one pass,
and this reference was allowed to reason. Scores, intervals, agreement and per-bucket accuracies are in
[`results.json`](results.json), written by `score.py`. The means of the three sessions by bucket, the mean pairwise agreement,
the gap and the slice 9 answer counts below are computed from it and from `answers/`. `representativeness.json` is unchanged:
it depends only on the models.

## What was run

Three sessions of one model, Claude Haiku 5.5 (`claude-haiku-5-5`), each answered the same 500-item sample of the public test
set: 300 relations and 200 groups, drawn and weighted as in PREREG. Each session answered the ten slices of 50 items in
separate `claude -p` calls with no tools, from an empty folder outside the repository. No temperature or seed was set.

**Protocol outcome.** All 30 calls were valid on the first attempt: no void, no re-run, no tool use, and one turn each
(`raw/ledger.jsonl`). The frozen files matched their pre-registered hashes. The calls used about 1.58 million input tokens
and 1.61 million output tokens in total, most of the output being the model's reasoning. One group answer from session 3 was
not in the answer format; as pre-registered, it is scored as missing (wrong).

## Results

Weighted MCC on the 500-item sample, with league-bootstrap 95% intervals (18 leagues, 2,000 draws). The reference value is
the mean of the three sessions, computed within each draw. The models are scored on the same items and draws.

| | Headline | Relation MCC | Group MCC |
|---|---|---|---|
| **Haiku 5.5, reasoning allowed (mean of 3)** | **0.960** [0.947, 0.972] | 0.955 [0.939, 0.970] | 0.965 [0.946, 0.982] |
| Session 1 | 0.977 | 0.980 | 0.975 |
| Session 2 | 0.977 | 0.979 | 0.975 |
| Session 3 | 0.925 | 0.906 | 0.944 |
| D3 27B (top System One model) | 0.518 [0.469, 0.567] | 0.756 | 0.281 |
| D3 9B | 0.263 [0.222, 0.305] | 0.478 | 0.049 |
| Pplx Decider 27B | 0.263 [0.221, 0.301] | 0.489 | 0.038 |

All 17 models' same-sample values are in `results.json` (`models`). On this sample the gap between the reference and the
top System One model is about 0.44 on the headline, and the two intervals are far apart.

**Agreement between sessions.** Fleiss' kappa is 0.913 on relations and 0.924 on groups. Mean pairwise agreement is 0.931
on relations and 0.937 on groups (two missing answers count as agreement; agreement on items both sessions answered is in
`results.json`).

**By relation depth and group set size** (weighted accuracy, not MCC):

| | Items | Haiku 5.5 (mean of 3) | D3 27B |
|---|---|---|---|
| Relation, depth 0 | 86 | 0.951 | 0.889 |
| Relation, depth 1 | 47 | 0.985 | 0.933 |
| Relation, depth 2 | 60 | 0.967 | 0.683 |
| Relation, depth 3 | 39 | 0.923 | 0.590 |
| Relation, depth 4+ (6 items, one league) | 6 | 0.944 | 0.500 |
| Relation, NONE | 62 | 0.984 | 0.806 |
| Group, 2 members | 69 | 0.971 | 0.679 |
| Group, 3 members | 69 | 0.983 | 0.276 |
| Group, 4–8 members | 62 | 0.955 | 0.117 |

The single-pass model falls off with relation depth and group size; the reference stays above 0.9 in every bucket.

## Session 3, slice 9

Session 3 answered 15 of 38 relations and 7 of 12 groups correctly on slice 9, against near-perfect scores on its other
slices and on the same slice in sessions 1 and 2 (38 of 38 and 12 of 12 each). Its answers there are not shifted by one
item; it answered NONE on 18 of the 38 relations (session 1: 10). Its visible summary for the slice says: "In every item the
labels contradict the rules in at least one case (H405, for example), so I treated them as market labels and reasoned about
which worlds the rules allow." Setting the labels aside departs from the reading convention in `INSTRUCTIONS.md` (a No label
negates the question). The slice was valid under the protocol, so it is scored as answered; PREREG allows a re-run only for
a protocol failure. Session 3 made 23 of its 24 relation errors in this slice.

## What this shows and what it does not

- The items are answerable from the prompt text by a model that may reason: the reference reaches about 0.96 on the
  headline, and its sessions agree closely. This is consistent with the earlier blind audits, whose model judges also
  reached the gold answers.
- On the same items, the 17 System One models, which answer in one pass, score far lower. On average across them, and for
  D3 27B, the gap is largest on deeper relations; the gap on larger groups is clear for D3 27B and holds for 9 of the 17.
  This gives the System One scores a point of comparison on the same items.
- It is not a ranking and not a comparison under equal conditions. The reference may reason, is a hosted model, sees about
  50 items in one context with shared rule cards (so it can check its answers against each other, which a System One model
  answering one question at a time cannot), and answers each group's bounds at once, while the System One child asks one
  question at a time. The `[label: Yes]` / `[label: No]` market tags are in all 500 prompts, as in the System One prompts.
- Two of the 17 models (Kev 4B and Kev 0.8B) were added after the v0.1.0 pre-registration
  (`release/v0.1.0/record/prereg.md`); they are scored here like the others.
- The test set, its gold and this sample's key are public. A hosted model's training data is unknown, so this score cannot
  be separated from possible exposure to these items.
- The intervals are conditional on the 500 sampled items; they do not include the variance of drawing the sample.
- Three sessions of one model show its run-to-run spread on this prompt. They are not independent judgements and say
  nothing about the spread across models.
- A preflight call on two made-up items (not test items) checked the output format before the runs. It is not part of the
  result.

## Files

`raw/` (the 30 raw outputs and the ledger), `answers/` (the extracted answer lines), `results.json` (scores, intervals,
agreement, breakdowns, and the 17 models on the same sample), `PREREG.md`, `INSTRUCTIONS.md`, and the scripts.

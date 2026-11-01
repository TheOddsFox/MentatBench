# v0.1.1 item audit: notes

Run on 2026-11-01 under `PREREG.md` (committed before any sample existed), as amended before judging (one fresh Haiku 5.5
instance per 100-item slice; two independent judge sets). No score, gold label or flag was changed by this audit.

## Result (`verdicts.json`)

| Group | n | suspect_label | hard_valid | unclear |
|---|---|---|---|---|
| all_wrong (every model wrong) | 423 | 12 | 314 | 97 |
| inverted (sample, not all_wrong) | 127 | 1 | 113 | 13 |
| controls (unflagged) | 50 | 0 | 48 | 2 |

- 13 of 550 judged flagged items are `suspect_label` (both judges gave the same non-gold answer).
- 0 of 50 controls were labelled `suspect_label`. The exact two-sided 95% interval for 0 of 50 is 0% to 7.1% (one-sided upper bound 5.8%). Only 5 controls
  are group items, so this interval is mainly a relation estimate.
- 9 of the 13 are group items whose gold is `at_most_k:K` and where both judges answered `none`. Several judges reported that
  `JUDGE.md` does not say which kind to give when a set's count range fits more than one kind. The cause of the 9 is an untested
  hypothesis: a shared misreading of the group-kind taxonomy, or gold errors. They are candidates for review, not confirmed errors.
- Most items that every model gets wrong are `hard_valid`: the judges, reading the prompt alone, reach the gold answer.

## Robustness of the result

The pre-registered result above is unchanged. `exploratory.py` computes the checks below from the committed files and writes
`exploratory.json` (deterministic; it checks that its recomputed verdicts equal `verdicts.json`). Parts (a) to (c) check the
pre-registered result. Part (d) is EXPLORATORY: it is not pre-registered, and it changes no verdict.

**(a) Gold agreement by slice** (answers equal to the gold class, of 100 per slice)

| Slice | Ids | Judge set 1 | Judge set 2 |
|---|---|---|---|
| 1 | A001-A100 | 80 | 96 |
| 2 | A101-A200 | 96 | 95 |
| 3 | A201-A300 | 24 | 87 |
| 4 | A301-A400 | 99 | 100 |
| 5 | A401-A500 | 99 | 100 |
| 6 | A501-A600 | 96 | 85 |

Judge set 1's discarded first answers for slice 3 (deviation 1) agree with gold on 97 of 100.

**(b) The 13 suspect labels.** Eleven are group items and two are relations (r03602 and r04933, both slice 6). Nine group items
(g00037, g00047, g00189, g00211, g00347, g00507, g00596, g00861, g01000) have gold `at_most_k:K` and both judges answered `none`.
All nine are in judge set 1, slice 3. Judge 2 gave the gold bounds 0 to K under the kind label `none` in all nine. Judge 1 did so
in one (g00596); in seven of the other eight its upper bound was n. The other two group suspects are g00453 (gold `at_least_k:1`;
both judges `at_least_k:2`, bounds 2 to 4; slice 6) and g00551 (gold `ordered_chain` on two members; both judges `none`; slice 6).

**(c) Sensitivity to the discarded first answers.** With judge set 1's discarded slice-3 answers in place of the re-run, the
flagged items are 488 hard but valid, 57 unclear and 5 suspect (pre-registered: 427, 110 and 13). The controls are 49, 1 and 0
(pre-registered: 48, 2 and 0). The five suspects are g00211, g00453, g00551, r03602 and r04933. The re-run is the answer set the
pre-registration allows; this is shown only to size the effect of the deviation.

**(d) EXPLORATORY: bound-based reading.** A group answer matches gold when its lower and upper bounds equal the gold class's bounds,
whatever its kind label. This is not applied when the gold or the answer has an `ordered_chain`, because the chain is a constraint
the bounds do not carry. The verdict rule is the pre-registered one, with this match: hard but valid when both judges match;
suspect when both normalised answers are the same, not null, and neither judge matches. Under this reading the flagged items are
439 hard but valid, 107 unclear and 4 suspect; the controls are 48, 2 and 0. Nine of the 13 are resolved, including all nine
`at_most_k` items (judge 2 gives the gold bounds in each; g00596 has both judges). Four remain: g00453, g00551, r03602 and r04933.
g00551 remains because a `none` answer does not carry the chain.

None of the 13 is a confirmed label error, and no score changes. The judges are models, and nothing here is an adjudication.

## Judge isolation and normalisation

- Each judge instance was given only `JUDGE.md` and its slice (100 rows), in its own empty folder. It was told not to open other
  files, and it had no access to the gold, the key, the flags or the verdict rule. All instances reported compliance.
- Per judge set, after `score.py`'s normalisation: 0 of 600 answers were missing or invalid for judge set 1, and 0 of 600 for
  judge set 2. Every id has exactly one answer in each set.
- Sample ordering: PREREG's "ascending key order" is Python tuple order over `(op, gold_class, stratum)`, the cell key that
  `build_sample.py` sorts on.

## Deviations, recorded as they happened

1. Judge set 1, slice 3 (A201–A300): the first instance delegated its items to sub-agents, against the protocol, and said so.
   Its answers are kept unchanged in `deviations/judge1_slice3_delegated_answers.jsonl` and are not used. A fresh instance
   re-judged the slice with an explicit no-delegation rule; `judge1_answers.jsonl` uses the re-run. PREREG allows a re-run only
   for a missing or malformed answer file, so this re-run is outside the pre-registered rule. It was decided after the delegation
   was seen, and it is recorded here as a deviation.
2. After that, every running instance was reminded not to delegate; all other eleven slices reported answering every item
   themselves.
3. Judge set 2, slice 2 used one shell command to write its first nine answer lines (writing only, no computation).
4. Several instances reported lower confidence on group items (bounds for larger sets estimated by hand, kind chosen by a
   stated rule). Independent noise tends to fall on `unclear`; a misreading shared by both judge sets can still produce
   `suspect_label`, which is why the 9 at_most_k suspects are treated as an untested hypothesis.

## Limits

Judges are model judges (Haiku 5.5), not people. No person adjudicated any item in this release. Suspect labels are not
adjudicated and remain in the analysis scores. The item-audit flags are candidates for review, not label errors.

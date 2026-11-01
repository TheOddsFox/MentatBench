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
- 0 of 50 controls were labelled `suspect_label`. The one-sided 95% upper bound for a rate of 0 in 50 is about 7%. Only 5 controls
  are group items, so this bound is mainly a relation estimate.
- 9 of the 13 are group items whose gold is `at_most_k:K` and where both judges answered `none`. Several judges reported that
  `JUDGE.md` does not say which kind to give when a set's count range fits more than one kind. The cause of the 9 is an untested
  hypothesis: a shared misreading of the group-kind taxonomy, or gold errors. They are candidates for review, not confirmed errors.
- Most items that every model gets wrong are `hard_valid`: the judges, reading the prompt alone, reach the gold answer.

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

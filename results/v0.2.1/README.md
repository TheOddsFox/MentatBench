# Mentat-Hard results, v0.2.1 (an analysis release based on v0.2.0)

This folder holds the v0.2.1 analysis release of the Mentat-Hard test set. It is based on [v0.2.0](../v0.2.0/README.md), and it
changes no leaderboard row, score or prediction. Every row, metric, rank and gap is v0.2.0's. The predictions are the ones in
[../v0.2.0/predictions](../v0.2.0/predictions).

An analysis release may add unranked, exploratory material outside the leaderboard. v0.2.1 adds three files:

- [analysis.json](analysis.json): the v0.2.0 analysis, with its release field set to v0.2.1. Nothing in it is recomputed.
- [profiles.json](profiles.json): for each of the 17 leaderboard models, the accuracy on the 6,000 relation items and on the 1,000
  curated group items, the counts by group set size and by group kind, the missing answers, and one failure example per op. The
  examples are chosen by the fixed rule in its `definitions`. They illustrate a model's most frequent confusion and do not measure it.
  An abstained relation is scored as its recorded answer, NONE, as in the published scores, so the relation counts agree with the
  leaderboard's depth buckets for every model. laya_latest abstains on 4,981 of its 6,000 relations: `missing.relation` counts them,
  and 1,407 of its relations are correct. Group abstentions are not recorded per item, so `missing.group` counts only the group items
  with no answer (there are none in the committed predictions).
- [reference.json](reference.json): an unranked reasoning reference. It is Claude Haiku 5.5 with reasoning allowed, scored on a
  500-item sample of the same test items in three sessions. It is not a leaderboard entry and has no rank. On that sample its
  headline is 0.960 [0.947, 0.972], against 0.518 [0.469, 0.567] for D3 27B on the same items; the file also holds all 17 models'
  same-sample scores. It is not a comparison under equal conditions: the reference may reason, and its other conditions, including
  one session's drop on one slice, are listed in the file and in the note. The reference note is
  [docs/audits/v0.2.0-reasoning-reference/notes.md](../../docs/audits/v0.2.0-reasoning-reference/notes.md), and the
  pre-registration is [PREREG.md](../../docs/audits/v0.2.0-reasoning-reference/PREREG.md).

[leaderboard.json](leaderboard.json) and [leaderboard.csv](leaderboard.csv) hold v0.2.0's rows (the CSV is byte-identical to
v0.2.0's). The JSON's release, date and provenance are v0.2.1's: the release is based on v0.2.0, and there is no record_from, because the measurements are v0.2.0's.

## Rebuild and check

From the repository root, `python results/v0.2.1/build_bundle.py build` writes the files, and
`python results/v0.2.1/build_bundle.py build --check` exits 1 on any byte difference from the committed files. Both read committed
files only. README.md is written by hand.

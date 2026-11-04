# Mentat-Hard results, v0.2.2 (an analysis release based on v0.2.1)

This folder holds the v0.2.2 analysis release of the Mentat-Hard test set. It is based on [v0.2.1](../v0.2.1/README.md), which is
based on [v0.2.0](../v0.2.0/README.md), and it changes no leaderboard row, score, rank or prediction. Every row, metric and rank is
v0.2.0's. The predictions are the ones in [../v0.2.0/predictions](../v0.2.0/predictions).

An analysis release may add unranked, exploratory material outside the leaderboard. v0.2.2 adds one file and carries v0.2.1's
other files:

- [transfer.json](transfer.json): the real-question check, from the transfer pilot. The pilot asked whether the v0.2.0 order of the
  17 models carries over to 200 items built from real Polymarket questions. On these 200 items, under these conditions, the order
  carries over: the Spearman correlation between the pilot headlines and the v0.2.0 headlines is 0.821, with a 95% cluster-bootstrap
  interval of [0.772, 0.890], against a pre-registered threshold of 0.6. On these 200 items, the relation order also holds (0.838),
  and the group order holds only weakly (0.311, with an interval of [0.032, 0.464]). Headline levels are not comparable across the
  two sets, so only the order is compared. The file also holds the three sensitivity
  analyses, each model's pilot headline beside its v0.2.0 headline, the two baselines and the limits of the check. The pilot's note,
  with its method and limits, is [docs/audits/v0.2.0-transfer-pilot/notes.md](../../docs/audits/v0.2.0-transfer-pilot/notes.md).
- [analysis.json](analysis.json): v0.2.1's analysis, with its release field set to v0.2.2. Nothing in it is recomputed.
- [profiles.json](profiles.json) and [reference.json](reference.json): v0.2.1's files, with only the release field set to v0.2.2.

[leaderboard.json](leaderboard.json) and [leaderboard.csv](leaderboard.csv) hold v0.2.0's rows. The CSV is byte-identical to
v0.2.0's and v0.2.1's. The JSON's release, date and provenance are v0.2.2's: the release is based on v0.2.1, and there is no
record_from, because the measurements are v0.2.0's.

The transfer check is exploratory. It is not a leaderboard comparison, and it changes no score or rank.

## Rebuild and check

From the repository root, `python results/v0.2.2/build_bundle.py build` writes the files, and
`python results/v0.2.2/build_bundle.py build --check` exits 1 on any byte difference from the committed files. Both read committed
files only. README.md is written by hand.

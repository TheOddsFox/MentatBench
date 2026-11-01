# Mentat-Hard v3.1 answerability sample, notes

v3.1 (owner decision 2026-10-30, "harder through deeper inference") changes only the relation suite of v3:
- NONE drops to 25%, mostly same-league pairs that share rule wording;
- derived links come from the deepest tier first.

The cards, bindings, groups and readings are those of v3, whose audit is `docs/results/mentat-hard-v3-audit/notes.md` (#76).

This audit repeats the v3 design on the v3.1 builds and adds one part for the new deep links. Tools are copied from the v3
audit:
- `build_sample.py`: seed `audit-v3.1:2026-10-30`, version 3.1, and the deep part;
- `verify.py`: checks the deep part's counts and depth;
- `score.py`: adds the deep part to the bar;
- `JUDGE.md`: unchanged.

## Sources

- **Dev:** v3.1 scratch build, seed 20261027, content hash
  `218003c63135ace753a1b2449ee002dc2813c7e99bed8c0956fd5a52187c3964`. It is byte-identical to the implementer's repeat build.
- **Held-out:** v3.1 scratch test-split build, seed 777000001 (not the registered test seed), content hash
  `208c3d36682aae43e3e53901bcb865d1fbbfda723c81a7ba525f33f35bfaee26`.
- `sources.json` records the paths. verify.py refuses a fixture whose hash changed.

## Sample (210 rows)

| part | n | source |
|---|---|---|
| bindings | 30 | 15 bound, 15 unresolved |
| relations | 30 | 6 per gold class, dev wording |
| deep_relations | 30 | derived links at depth ≥ 3, 10 each of IMPLIES, IMPLIED_BY and EXCLUDES; dev; not overlapping the relations part. All 30 drawn are depth 3, since depth 4+ is rare |
| groups | 30 | 15 real, 15 decoy |
| readings | 30 + 30 rows | ambiguous and decoy, each with base and alt questions |
| heldout_relations | 30 | 6 per class, at least one held-out family |

Prompts are the exact child-v6 wire text (state with the card, instructions, options), captured through the real child
against the fake server. verify.py output: all checks pass (fields, prompt rebuild, cards in relation, group and reading
prompts but not bindings, counts, gold round trip in 18 parts).

## Pre-registered bar (written and committed before any judging)

A part passes when **both** judges score at least 80% (24 of 30). The parts are bindings, relations, deep_relations, groups
(full class), readings_base, readings_alt, reading_decoys and heldout_relations. Report only, with no bar: relations per
class, real relations, real groups, and group kind.

Adjudication: when both judges miss an item, record whether the answer is derivable from the prompt alone (a short
derivation from the card and the question). A derivable miss counts as difficulty, not invalidity. Such items stay in the
score.

Judges: two independent Haiku 5.5 judges at high effort, as for v2 and v3. Each gets only `JUDGE.md` and `sample.jsonl`.

## Round 1 result (child v6): FAILED on reading decoys

Scored with `score.py` (output in `score_output.txt` and `summary.json` here). Every part passed except one:

| part | judge 1 | judge 2 | bar |
|---|---|---|---|
| reading_decoys | 11/30 | 29/30 | FAIL |

Cause: in 19 of 30 decoy rows, judge 1 answered base correctly and alt differently. Decoy propositions carry an explicit
gloss (e.g. "at least 3 points by the Torova League break, counted on final points"), which fixes reading (1). The
child v6 alt instruction says only "If U uses no term with two readings, both readings are its plain meaning". U does use
"by the break", so applying reading (2) (final points ≥ n−1) is a defensible reading of the instruction, while the key
expects the gloss to win. This is an instruction defect that models see too, not judge noise (the v3 judges happened to
read it the key's way).

Owner decision (2026-10-30): fix the alt instruction (child v7), re-run the full audit against the same bar, and restart the
dev cells. The v3.1 dev cells started at 09:00 on child v6 were stopped about 35 minutes in and are discarded.

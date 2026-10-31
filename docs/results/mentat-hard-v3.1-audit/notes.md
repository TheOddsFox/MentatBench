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

## Round 2 (child v7), written before judging

Round 1 (child v6) failed on reading decoys; the record and its cause are in `round1-child-v6/`. Child v7 rewords only
`alt_rules`: "If U's wording fixes one meaning (it uses no term with two readings, or it says which reading it means), give the
option for that meaning." The sample was rebuilt with the same seed, so it holds the same 210 items (the same ids and qids)
and only the reading prompts' alt instruction changed (60 prompts). The bar is unchanged. Two new judge instances (the same
model and effort) answer the rebuilt sample blind.

## Round 2 result (child v7): every part passes

| part | n | judge 1 | judge 2 | bar (both ≥ 80%) |
|---|---|---|---|---|
| bindings | 30 | 30 | 30 | PASS |
| relations | 30 | 30 | 30 | PASS |
| deep_relations (depth ≥ 3) | 30 | 30 | 30 | PASS |
| groups (full class) | 30 | 29 | 30 | PASS |
| readings base | 30 | 30 | 30 | PASS |
| readings alt | 30 | 30 | 30 | PASS |
| reading decoys | 30 | 30 | 30 | PASS (round 1: 11 and 29) |
| held-out relations | 30 | 29 | 30 | PASS |

- **Missed by both judges:** none.
- **How judge 2 ran:** the first judge-2 instance ran out of context on the 210-item file, about 140k tokens, and wrote no answers. Judge 2 was therefore run as three independent instances of the same model and effort, on 70-item slices A001–A070, A071–A140 and A141–A210, cut unchanged from `sample.jsonl`. Their answers are concatenated in `judge2_answers.jsonl`.
- **Blindness:** every judge's tool log shows reads of only `JUDGE.md` and its sample or slice, plus writes to its own answer file.
- **Points judges raised** (none made a judge miss the bar):
  - "midline" is held-out wording the card does not define, by design;
  - "topped N points" was read as more than N, which matches the generator (m = t − 1).

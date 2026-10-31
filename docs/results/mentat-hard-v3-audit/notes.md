# Mentat-Hard v3 answerability sample (v3 dev and held-out wording), notes

Purpose: show that the v3 questions (child v6, with the per-league rule card on relation, group and reading questions) are
answerable from the prompt text before any v3 fixture is registered. v2 found groups and second readings unanswerable from
the text (`mentat-hard-v2-validity-audit-20261029.md`); this audit repeats that check on v3.

Source: worktree `w10-audit-v3` at origin/main 6e2ae87 (branch `w10/audit-v3`). Files here: `sample.jsonl` (180 rows),
`key.jsonl` (180 rows, gold), `sources.json` (fixtures and hashes), `build_sample.py` (writes all three), `verify.py`
(self-check), `score.py` (scores judge answers against the bar), `JUDGE.md` (the judge instructions).

## Pre-registered bar (written before any judging)

An audit part **passes when both judges score at least 80% on it** (`score.py`: every judge, at least 24 of 30).
The parts, each of 30 items:

| Part | Items | Scored as |
|---|---|---|
| bindings | 30 (15 bound, 15 unresolved, dev) | status |
| relations | 30 (6 per gold class, dev) | relation |
| groups | 30 (15 real, 15 decoy, dev) | full canonical class (kind plus bounds) |
| readings_base | 30 (the base question of the 30 ambiguous rows, dev) | base option |
| readings_alt | 30 (the alt question of the same 30 rows, dev) | alt option |
| reading_decoys | 30 (the 30 decoy rows, dev) | base and alt both right |
| heldout_relations | 30 (6 per class, held-out build, each with an outcome of family 21-30) | relation |

Report only, with no bar: relations_real (the 24 non-NONE dev relation items; v2-comparable), groups_real (the 15 real
sets, class; v2-comparable), groups_kind (the kind alone, all 30, and by stratum), relations per class.

**Adjudication rule (fixed in advance).** Where both judges miss an item, record whether the answer is derivable from the
prompt text alone, with a short written derivation from the rule card and the question. An item that is derivable but
missed counts as difficulty, not invalidity. Such items are reported and never removed from the score. The derivations are
recorded in `adjudication.jsonl` (one line per id: `{"id", "derivable", "derivation"}`), which `score.py --adjudication`
reads. `score.py` counts both-missed items per part with their adjudication status.

## Sources (hashes and seeds)

| Source | Build | Seed | Content hash (`corrections.content_hash` over all files) |
|---|---|---|---|
| dev | scratch `v3-dev` (fixtures_hard, version "3", split dev) | 20261027 | `b1caaae68d0dab1979decd4b666e4e45d63ef93224b709095e5b12eb820481f0` |
| held-out | `python -m mentat_bench.fixtures_hard build --split test --seed 777000001 --out <scratch>/v3-heldout` (name `mentat_hard_test`, version "3") | 777000001 | `afde592b6e87a6fd3d117ac47913230447d2992cd2c93c517284970ea10b69ff` |

The dev hash matches the owner's record. The held-out seed is not the registered test seed (20261028), so no registered
test fixture is read. The held-out build finished with exit 0 (its audit gate and suite audit passed). Its scratch
directory is not committed; `sources.json` records the paths, and `verify.py` rejects a fixture whose hash has changed.

## Sample

Seed `random.Random("audit-v3:2026-10-30")`. Pools, in file order, then a seeded draw per part, then one shuffle across
parts. Ids A001 to A180 are assigned in the shuffled order.

| Part | Drawn from (pool) |
|---|---|
| relations (30) | dev relation rows, 6 per class; pools EQUIV 800, IMPLIES 915, IMPLIED_BY 934, EXCLUDES 1027, NONE 2324 |
| groups (30) | dev curated groups; real 700, decoy 300 |
| bindings (30) | dev bindings; bound 3038, unresolved 100 |
| readings (30 + 30) | dev reading rows; ambiguous 100, decoy 100 |
| heldout_relations (30) | held-out build relation rows with at least one family 21-30; pools EQUIV 240, IMPLIES 420, IMPLIED_BY 425, EXCLUDES 332, NONE 706 |

Relation rows excluded for instructions over `MAX_INSTRUCTION_CHARS` (the child refuses them): 0 in each build.

Counts in the key (verify, 4b): relations 30 (6 per class), heldout_relations 30 (6 per class, each with a family 21-30
outcome), groups 30 (15 real, 15 decoy), bindings 30 (15 bound, 15 unresolved), readings_ambiguous 30, readings_decoy 30.
Dev relation rows have only families 1-20; the held-out rows have at least one of families 21-30 (21-30 appear in
the held-out relation rows listed by verify).

## Prompt text: how it was produced

- Each sampled item goes through the real child v6: `protocol.handle(child, line)` -> `SystemOneChild.decide`, with
  `parallel=1`, against `FakeOllama` from `tests/test_systemone_child.py` on 127.0.0.1. No real Ollama server is contacted.
- The prompt is rendered from the recorded wire request: `state` + `"\n\n"` + `questions[name].instructions` +
  `"\nOptions:\n"` + one `key: criterion` line per option, in the order the child sent.
- The state now carries the league rule card (`League rules:` followed by the card, once per distinct card). Relation:
  the cards of A and B. Group: the cards of the members. Reading: the card of U. Binding: no card (verify checks this).
- Relation: the request for `relation`. Binding: the request for `status`. Group: the request for `kind`, plus the v2
  neutral line (kind, lower and upper bound, chain order). Reading: the requests `base` and `alt`, in that order. The
  wire names of the reading questions are `base` and `alt`; with a card their instructions are the `base_rules` and
  `alt_rules` ones (reading (1) and (2) of the rules). `build_sample.py` asserts this.
- Each wire state is checked against the state the child builds from the context (`expect_state`).

## Self-check (`verify.py`, run output)

```
1. fields: ok; ids opaque; sample.jsonl has no qid, outcome id, stratum, terms, family, build or gold text
4a. sources: dev b1caaae68d0d seed 20261027 | held-out afde592b6e87 seed 777000001
2. prompts equal the child-v6 rebuild: {'group': 30, 'reading': 60, 'binding': 30, 'relation': 60}
3. rule cards: each asked outcome's card is in its relation, group and reading prompts, and not in binding prompts: {'group': 85, 'reading': 60, 'binding': 30, 'relation': 120} outcomes checked
4b. counts: {'groups': 30, 'readings_decoy': 30, 'bindings': 30, 'readings_ambiguous': 30, 'heldout_relations': 30, 'relations': 30} | held-out families: [2, 6, 8, 10, 11, 12, 13, 15, 16, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
5. gold round trip: every item correct in 17 parts: ['bindings', 'groups', 'groups_kind', 'groups_kind_decoy', 'groups_kind_real', 'groups_real', 'heldout_relations', 'reading_decoys', 'readings_alt', 'readings_base', 'relations', 'relations_EQUIV', 'relations_EXCLUDES', 'relations_IMPLIED_BY', 'relations_IMPLIES', 'relations_NONE', 'relations_real']
```

Exit 0. The held-out family list in 4b is the families of both outcomes of the held-out relation rows; 21-30 each appear.
The scorer CLI was also exercised on scratch answer files (a gold-only file and an empty file, and an adjudication file):
the tables, the pass or fail, and the both-missed counts behaved as specified. Those scratch files are not in the repo.

## Prompt length (characters, the full prompt; reading rows: base and alt each)

| Part | n | min | median | mean | p90 | max |
|---|---|---|---|---|---|---|
| bindings | 30 | 642 | 662 | 665 | 681 | 694 |
| relations | 30 | 1725 | 1884 | 1949 | 2027 | 3064 |
| heldout_relations | 30 | 1777 | 1881 | 1982 | 2008 | 3364 |
| groups | 30 | 1925 | 2214 | 2193 | 2360 | 2390 |
| readings_ambiguous, base | 30 | 1893 | 2088 | 2101 | 2216 | 2243 |
| readings_ambiguous, alt | 30 | 1967 | 2162 | 2175 | 2290 | 2317 |
| readings_decoy, base | 30 | 1897 | 2097 | 2107 | 2247 | 2285 |
| readings_decoy, alt | 30 | 1971 | 2171 | 2181 | 2321 | 2359 |

Longer relation maxima are pairs whose two outcomes are in different leagues (two cards in the state).

## Decisions and deviations

- The v3 dev source is the scratch build, not `fixtures/mentat_hard_dev` (which is the v2 fixture). `build_sample.py` takes
  both sources as arguments; `sources.json` records their paths, seeds and hashes.
- Held-out relations come from a scratch test-split build with a fresh seed. Their rows are test-split generator output,
  not registered fixture items, and the registered test seed is not used.
- Judge instructions (`JUDGE.md`) are the v2 ones, as the validity audit records them: a careful, logically precise
  expert, the text alone, no outside knowledge, and the two reading conventions (No negates; "not fail to reach N" means
  "at least N"). The v2 wording itself is not in the repo, so `JUDGE.md` restates it; the judges' output format is v2's.
- Group prompt: the kind question plus the v2 neutral line, scored on the full canonical class (kind and bounds), as in v2.
- Chain order is collected, not scored.
- The wire names of the reading questions are `base` and `alt` (not `base_rules` and `alt_rules`): the child keeps the
  question name and changes the instructions. The prompts carry the card-bearing instructions.

## Scoring

`python -B score.py --key key.jsonl --answers judge1_answers.jsonl --answers judge2_answers.jsonl --adjudication adjudication.jsonl`
Writes `summary.json` (default, next to the key): per part, each judge's count and accuracy, pass or fail (every judge at
least 80%), the report-only cells, and, with two judges, the both-missed ids and their adjudication. A missing answer is wrong
and is tallied in `missing`.

## Judging (2026-10-30)

Two independent judges, both Haiku 5.5 at high effort, the same model the v2 audit used. Each got only JUDGE.md and
sample.jsonl. Their tool logs show only those two files were read, plus their own answer file being written. No key, notes,
code or fixture was opened. They wrote `judge1_answers.jsonl` and `judge2_answers.jsonl` (180 lines each). Scored with the
command above, with no adjudication file: no item was missed by both judges, so nothing needed adjudicating. The output is
in `score_output.txt` and `summary.json`.

| part | n | judge 1 | judge 2 | bar (both ≥ 80%) |
|---|---|---|---|---|
| bindings | 30 | 30 | 30 | PASS |
| relations | 30 | 29 | 29 | PASS |
| groups (full class) | 30 | 30 | 30 | PASS |
| readings base | 30 | 30 | 30 | PASS |
| readings alt | 30 | 30 | 30 | PASS |
| reading decoys | 30 | 30 | 30 | PASS |
| held-out relations | 30 | 30 | 30 | PASS |

Report-only cells:
- Real relations: 23/24 for each judge, on different items.
- Real groups: 15/15 for each judge (v2 judges: 1/15 and 5/15).
- Group kind: 30/30 for each judge.

The v2 comparison: second readings were 0/30 for both judges in v2 and are 30/30 here.

Judgment calls the judges raised (answered, then reported):
- "bottom half" is not defined on the card; both judges read it as the complement of "top half";
- "midline" is a held-out word the card does not define, by design (held-out wording is never on cards);
- "tally top N points" was read as "more than N";
- one group with two always-true outcomes was answered as at_least_k 2..2.

None of these made a judge miss the bar.

What this shows, and what it doesn't. It shows that a careful general-purpose reader can answer every question type from the
prompt the child sends. It says nothing about how hard the questions are for System One models, which is what the
benchmark measures. Near-perfect judge scores also mean the ceiling is reachable from the text, so model gaps reflect model
skill rather than missing information.

## Not done

- The sample is 30 items per part, on one dev build and one held-out scratch build. It does not cover the registered test
  split, and it does not test the prompt sensitivity of v3.
- The judges are models, not people.

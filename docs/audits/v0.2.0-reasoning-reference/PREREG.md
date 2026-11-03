# Reasoning reference for MentatBench v0.2.0: pre-registration

Written before any reference answer exists and committed before the first call. This is an **exploratory reference, not a ranked
result**. It is not a leaderboard entry, it changes no score, headline, flag or label, and it is outside the System One scope of the
specification (`SPEC.md` line 11: System One models "answer the harness's typed decisions ... in one pass, without generated
reasoning").

**Hashes of the frozen files** (sha256, as recorded in `frozen.json`). `score.py extract`, `score.py run` and `run_slice.py` refuse, before any
CLI call, ledger read or write, when a listed file differs from its recorded hash. The test suite checks that these lines match
`frozen.json` and the files. `frozen.json` is regenerated (`score.py freeze`) only if a frozen file changes before the commit.

- `score.py`: `8fa4e486e07006b01fe3b8e73ae7915e8c9c2fd2b637f7dc1fb7784b8d375c37`
- `run_slice.py`: `644234d57391c67f19728eb9ee4ed67d0d03a04e3175ebf8b8a58b1b091a96e8`
- `INSTRUCTIONS.md`: `44ebb4a53af157eb2ab47403de666148be254f96c550432e6629ddc253a980ba`
- `sample.jsonl`: `0bb3f70b34946769f0fc97bd2d4787d95810ddbae53e8edc139639c76f146c47`
- `key.jsonl`: `a1f0aebdf73cfd26624185fc49a766c25d6a88aa0632694c38f53c836e0e8522`
- `slices.json`: `970379a56cfbc1e9381a76638fa13bb17926bd3376bd42ee2da6535ed1296aa1`

## Purpose

The v0.2.0 numbers are measured under one pass per item with no generated reasoning. This reference asks a different question on a
sample of the same test items: how one hosted model, allowed to reason in prose before it answers, scores on them. It gives a point of
comparison for reading the System One numbers. It does not rank anything.

## Pinned inputs

- Fixture: `fixtures/mentat_hard_test`, split test, seed 20261028, version 3.1, content hash
  `7b408459538d7420e808d5d81e71916c57aee78f459a6b8c0905e48e43c8e6ab`. `slices.json` pins it.
- Not a sealed or private split. The reference is not run on any sealed split.
- Prompt capture: the System One child `python/mentat_bench/children/systemone_child.py`, unseeded (option seed None), with
  `PROMPT_SHA256` `d6d5ed56e65e98e2c9c948c69939257bb4b971325f72da3ef5967ea4b5b4fb44`, the value every v0.2.0 cell records.
- Predictions and leaderboard: `results/v0.2.0/predictions/<id>.jsonl.gz` and `results/v0.2.0/leaderboard.json`. The 17 models are
  all in the v0.2.0 leaderboard. `kev_4b-q8_0` and `kev_0.8b-q8_0` were added after pre-registration (release/v0.1.0/record/prereg.md, which
  lists fifteen models and not these two), and they are scored here like the others. `score.py` checks that each model's full-set relation and group MCC, recomputed from its predictions, equals
  its published point.
- Leagues: the grouping of `tools/eval/league_ci.py` (`read_fixture`). A relation's league is the league of its outcome `a`. A
  group's league is the league of its members, which must all share one. There are 18 test leagues.
- Contamination. The fixture's candidates and labels are in the public repository, and this reference's `sample.jsonl` and
  `key.jsonl` are too. A hosted model's training data is unknown, so its score on these items cannot be separated from possible
  memorisation of the items or their labels. This is a limit on every reading of the reference, not a control.

## Population

The 6000 `decide_relation` items and the 1000 curated `decide_group` items (real 700, decoy 300). The 184 reading groups are not
items. Gold labels are the suite labels of `gold/suite_labels.jsonl`. A relation's depth is its `depth` tag there (NONE for the
1500 hard-distractor items, which have no depth).

The strata, as `docs/mentat_hard.md` section 3 defines them. `trivial` is 500 same-market Yes/No pairs, labelled EXCLUDES, depth 0.
`hard_distractor` is the 1500 NONE pairs: 1000 rule-vocabulary pairs (same-league pairs that are not in one market but share a team,
a threshold, a draw group or a constraint) and 500 profile-matched pairs, all with depth NONE. `real` is everything else: the
EQUIV pairs, the derived implications and exclusions, and the generator's edges.

## Sample design (`build_sample.py`)

One generator, `random.Random("reasoning-reference-v0.2.0")`, is used for the draws in cell order and then for one shuffle.

**Relations: 300.** A cell is stratum x gold label x depth bucket, with buckets 0, 1, 2, 3, 4+ and NONE. The six depth-4+ items (two each
of IMPLIES, IMPLIED_BY and EXCLUDES, all real, all in league `test-L10`) form one pooled cell, and all six are taken. The other 294
seats: each of the 14 other cells gets its floor of 5, or the whole cell when smaller. The remaining 224 seats are shared out in
proportion to each cell's population left after the floor, by largest remainder (integer arithmetic; ties go to the smaller cell
name).

**Groups: 200.** A cell is the gold kind with its k values dropped (the kinds of the terms, joined by `+`; `none` for decoys) x
set-size bucket (2, 3, or 4 to 8 members). There are 17 cells. Each gets its floor of 4, or the whole cell when smaller (one cell,
`at_least_k` with 2 members, has 3 items). The remaining 133 seats are shared by the same largest-remainder rule.

Allocation (population, sample, weight; the weight is population / sample):

| Stratum | Gold | Depth | Population | Sample | Weight |
|---|---|---|---|---|---|
| hard_distractor | NONE | NONE | 1500 | 62 | 24.1935 |
| real | EQUIV | 0 | 800 | 35 | 22.8571 |
| real | IMPLIES | 0 | 242 | 14 | 17.2857 |
| real | IMPLIES | 1 | 382 | 19 | 20.1053 |
| real | IMPLIES | 2 | 398 | 20 | 19.9000 |
| real | IMPLIES | 3 | 218 | 13 | 16.7692 |
| real | IMPLIED_BY | 0 | 228 | 13 | 17.5385 |
| real | IMPLIED_BY | 1 | 350 | 18 | 19.4444 |
| real | IMPLIED_BY | 2 | 398 | 20 | 19.9000 |
| real | IMPLIED_BY | 3 | 218 | 13 | 16.7692 |
| real | EXCLUDES | 1 | 144 | 10 | 14.4000 |
| real | EXCLUDES | 2 | 398 | 20 | 19.9000 |
| real | EXCLUDES | 3 | 218 | 13 | 16.7692 |
| trivial | EXCLUDES | 0 | 500 | 24 | 20.8333 |
| pooled | IMPLIES, IMPLIED_BY, EXCLUDES | 4+ | 6 | 6 | 1.0000 |

| Gold kind | Set size | Population | Sample | Weight |
|---|---|---|---|---|
| at_least_k | 2 | 3 | 3 | 1.0000 |
| at_least_k | 3 | 21 | 6 | 3.5000 |
| at_least_k | 4-8 | 39 | 9 | 4.3333 |
| at_least_k+at_most_k | 4-8 | 53 | 11 | 4.8182 |
| at_most_k | 3 | 35 | 8 | 4.3750 |
| at_most_k | 4-8 | 46 | 10 | 4.6000 |
| at_most_one | 2 | 206 | 33 | 6.2424 |
| at_most_one | 3 | 25 | 7 | 3.5714 |
| at_most_one | 4-8 | 8 | 5 | 1.6000 |
| exactly_one | 3 | 138 | 23 | 6.0000 |
| exactly_one | 4-8 | 47 | 10 | 4.7000 |
| none (decoys) | 2 | 136 | 23 | 5.9130 |
| none (decoys) | 3 | 106 | 18 | 5.8889 |
| none (decoys) | 4-8 | 58 | 12 | 4.8333 |
| ordered_chain | 2 | 44 | 10 | 4.4000 |
| ordered_chain | 3 | 27 | 7 | 3.8571 |
| ordered_chain | 4-8 | 8 | 5 | 1.6000 |

**Weights.** Each item's weight is its cell's population divided by the cell's sample size, so the weights sum to 6000 over the
relations and to 1000 over the groups. The weighted confusion counts each item at its weight. The unweighted confusion counts each
item once.

**Identifiers.** After the shuffle the 500 items are numbered H001 to H500 in shuffled order. `sample.jsonl` holds `id`, `type`,
`prompt`, `answer_format` only. `key.jsonl` holds `id`, `qid`, `op`, `gold` (the class string), `weight`, `cell`, `league`,
`n_members` (null for relations), `stratum` and `bucket` (the depth bucket of a relation, the set-size bucket of a group).

**Prompts.** Each prompt is the child's wire text, captured through the real child (`children.protocol.handle`, then
`SystemOneChild.decide`) against the in-process `FakeOllama` of `tests/test_systemone_child.py`, with the rendering of the v0.1.1 item
audit (`docs/audits/v0.1.1-item-audit/build_sample.py`). A relation prompt is the state with the league rule card of each outcome asked
about, the question's instructions and its options. The build asserts that each state equals the one the child builds from the same
context. No real server is contacted.

A group prompt is the child's kind question (its state, rule cards and options) followed by one neutral sentence that asks for the
kind, the lower bound, the upper bound and, for an ordered chain, the chain order, all in one reply. The System One child asks for the
kind first and then for each bound it leaves open, one request at a time. The reference's group prompt therefore keeps the child's
state and kind options and changes the question's flow and its answer format. It is not identical to the System One group flow.

**Answer formats.** A relation: `{"relation": "EQUIV" | "IMPLIES" | "IMPLIED_BY" | "EXCLUDES" | "NONE"}`. A group:
`{"kind": ..., "lower": integer 0 to n, "upper": integer 0 to n, "chain": [...]}`, where n is the member count.

## Slices (`slices.json`)

The 500 items in shuffled order are cut into consecutive slices. A slice closes at 50 items, or before the item that would take its
prompt characters over the budget of 120,000 (the sum of the prompts' lengths), a limit set before any answer exists. The budget does
not bind: the ten slices hold 50 items each, with 99,064 to 104,135 prompt characters.

| Slice | Items | Relations | Groups | Prompt characters |
|---|---|---|---|---|
| 1 | 50 | 27 | 23 | 103,530 |
| 2 | 50 | 22 | 28 | 104,135 |
| 3 | 50 | 28 | 22 | 102,826 |
| 4 | 50 | 35 | 15 | 100,601 |
| 5 | 50 | 29 | 21 | 103,767 |
| 6 | 50 | 32 | 18 | 101,325 |
| 7 | 50 | 30 | 20 | 101,676 |
| 8 | 50 | 30 | 20 | 101,712 |
| 9 | 50 | 38 | 12 | 99,064 |
| 10 | 50 | 29 | 21 | 101,745 |

Each slice holds about 2.8 items per league (50 over 18 leagues), and the items of one league share its rule card within the slice.

Slice j's ids are `slices.json` `slices[j-1].ids`. `python -B score.py input j` writes the stdin of slice j: `INSTRUCTIONS.md`, a blank
line, then the slice's item lines of `sample.jsonl`, in order.

## Instructions

`INSTRUCTIONS.md` is frozen with this pre-registration. The test-taker reasons in prose if it likes, then ends with exactly one fenced
`jsonl` block with one line per id, written once at the end of the reply. It says to answer from the prompt text alone, to use no
outside knowledge and no tool, and it keeps the reading conventions of the v0.1.1 audit: a label of No negates the question, and "not
fail to reach N" means at least N. It does not mention the gold, the key, the benchmark or any repository.

The prompts are the child's text, which says "for a benchmark" in its first line. They are kept byte for byte, so that they are the
same text as the System One runs.

## Run protocol

There are three instances. An instance is a separate session of the same model (claude-haiku-5-5) given the same prompt for the same
slice. No temperature or seed is set, and the CLI offers no control of either here, so instances differ by sampling only. The three
instances are not independent samples of a population of models; they are three draws of one model's answers to one prompt. For each
instance i in {1, 2, 3} and each slice j in 1 to 10, the runner `run_slice.py` makes one call (30 calls before any re-run):

    python -B run_slice.py i j 1 --workdir <an empty folder outside the repository>

It runs, from that empty folder, `claude -p --safe-mode --model claude-haiku-5-5 --tools "" --strict-mcp-config
--no-session-persistence --output-format json`, with the stdin of `score.py input j` as bytes (not through a shell redirection), and
the environment variable `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. It saves the stdout bytes exactly to
`raw/inst<i>/slice<j>/attempt<k>.json` and the stderr to `attempt<k>.stderr`. Nothing is parsed or edited.

Then `python -B score.py extract i j k` checks the raw output and writes one line to `raw/ledger.jsonl`:
`{"inst", "slice", "attempt", "raw_sha256", "verdict", "reason"}`, where verdict is `valid` or `void`. A valid attempt also writes
`answers/inst<i>/slice<j>.jsonl`, the lines of the fenced block, verbatim.

`python -B score.py preflight <raw file>` checks the shape and fence form of one raw output and writes nothing. It is run on the first
call's output before any slice.

Each call is a separate session. No session is resumed, no context or file is shared between instances or slices, and each call runs
in its own empty folder. An instance sees only the instructions and its slice. It is not told the gold, the key, the flags, the other
instances' answers, or this document. The operator does not edit a raw output, an answer file, a prompt or the ledger. No majority vote
is taken; each instance is scored on its own.

The protocol reads `is_error`, `subtype`, `num_turns`, `result` and, when present, `permission_denials` of the `--output-format json`
result object. That the object has these fields is an assumption that the first call checks with `preflight`. If the first output
does not have them, the extraction rule is amended in writing, and no answer is scored under the old rule.

## Schema errors, void attempts, and the one re-run

**Missing or unreadable raw file.** If the raw file of the attempt does not exist, or cannot be read, `extract` is refused. It writes
no ledger entry and uses no attempt. This is separate from a schema error below and from a void.

**Schema error.** The raw bytes are not UTF-8 JSON (a byte order mark is accepted), the result is not a JSON object, or one of
`is_error` (bool), `subtype` (str), `num_turns` (int), `result` (str) is missing or of another type, or `permission_denials` is
present and is not a list. A schema error raises an error. It writes no ledger entry and does not use up the attempt. The raw file is
kept: the operator moves it to `raw/.../attempt<k>.schema-error-<n>.json` and repeats the call for the same attempt. `run_slice.py`
refuses to overwrite a raw file that has no ledger entry, so this move is the only way to repeat that attempt.

**Void.** An attempt is void when the object is readable and one of these holds:

- an error result: `is_error` is true, or `subtype` is not `success` (this includes a context overflow);
- `num_turns` is not 1 (a second turn, which would mean a retry or a tool use; no tool is allowed);
- `permission_denials` is a non-empty list;
- the result text does not hold exactly one fenced `jsonl` block (CRLF line endings are read as LF first, and the block's lines are
  split on LF only, so a U+2028 or U+2029 inside a JSON string does not split a line);
- the block's lines are not exactly one `{"id", "answer"}` object per id of the slice: a line that is not JSON, a line with other keys,
  an id that is not a string, a repeated id, a missing id, or an extra id.

A void attempt writes no answer file and is recorded in the ledger with its reason.

**The one re-run.** Every void is re-run exactly once, as attempt 2, in a new session, with the same prompt. This is not at the
operator's discretion: `score.py extract` refuses attempt 2 unless attempt 1 is recorded as void, refuses any attempt 3, and refuses
an attempt that already has a ledger entry. `run_slice.py` applies the same checks before it calls the CLI.

**Double void.** If attempt 2 is also void, the slice is closed as `double_void`. Its 50 items score `__missing__` for that instance,
which is wrong on every one of them. The instance is still scored, and both voids are reported in `results.json`.

**Low scores never void.** A wrong answer, an answer whose value is outside its format, and a low score for a slice do not void an
attempt or permit a re-run. An out-of-format value is scored as `__missing__`, which is wrong.

**Raw files and the ledger.** Every attempt is kept in `raw/`, and the ledger records each one with its sha256. `run()` checks the frozen
files and verifies the ledger before it reads anything else: the key, the fixture, the predictions, and any raw output or answer file.
A raw file that is missing, or whose sha256 differs from its ledger entry, refuses the run, and the run stops.

## Scoring (`score.py`)

- **Normalisation.** This follows the v0.1.1 item audit and `decision_scoring`. An answer that is a JSON string is parsed first. A
  relation answer is its label, with whitespace stripped and case kept; the five labels are the only valid ones. A group answer must be
  an object. Its `kind` is lower-cased and is read only to tell `none` and `ordered_chain` apart: kind `none` gives class `none`, and
  an ordered-chain kind adds the chain term. Any other kind must still be one of the six kinds, but the class comes from the bounds:
  `terms_class(group_terms(lower, upper, n, chain))`, the canonical class string the models' predictions use. Bounds must be integers
  in 0 to n (a bool is not), and the canonical terms must be able to hold them (upper at least 1, lower at most upper). The `chain`
  field is collected and is **not scored**. For the record, the fixture's gold `order` lists the chain's member ids
  in the gold chain order; of the 79 gold ordered chains, 29 are in the same order as the group's listing and 50 are a different
  permutation of it, so the gold chain order is not the listing order, and the chain is not scored in any case. An invalid or absent answer is `__missing__`. It counts
  as wrong. `decision_scoring` writes `invalid` for a group whose lower bound is above its upper bound (and for reading picks); the
  reference writes `__missing__` where it would write `invalid`, and both count wrong.
- **Primary: weighted MCC.** The Gorodkin multiclass MCC of `decision_scoring.mcc`, computed on the sample-weighted confusion (each
  item adds its weight), for relations (300 items, weight 6000 in total) and groups (200 items, weight 1000). The headline is the mean
  of the relation MCC and the group MCC, as in the v0.2.0 headline.
- **Secondary: unweighted MCC.** The same formula with each item counted once, for the same two parts and the headline.
- **Intervals.** League bootstrap: B = 2000 draws of the 18 leagues with replacement, from `random.Random("league_ci:0")`
  (`tools/eval/league_ci.py` `draws`), the same draws for every source, so that the comparisons are paired. The 95% interval is the 2.5
  and 97.5 percentiles (numpy, linear) of the draws that have items. Each draw's MCC is `league_ci._mcc` over the league-summed confusion,
  with the weighted confusion as float counts.
  **These intervals are conditional on the 500 sampled items.** They capture the variation from resampling leagues, and they exclude the
  variation from which items were sampled. They are appropriate for paired comparisons between the reference's instances and a model on
  these same items. They are not a confidence interval for a population of items, or for the test set.
- **Models.** The 17 models are scored on the same 500 items, from their headline predictions, with the same weights and draws.
- **Instances.** Each instance is scored once every one of its ten slices is accepted or double void. An instance with no ledger entry is reported
  as `absent`. An instance with some ledger entries but a slice still pending, or some slices not yet run, is `incomplete` and is not
  scored.
  - *Three-instance summary, main*: for each draw, the mean of the three instances' MCCs. The relation and group mains are these means;
    the headline main is the mean of the two. The point is the mean of the three instances' points. The interval comes from the draws.
    Weighted is primary; unweighted is secondary.
  - *Per instance*: the values with their intervals, and the range (the minimum and maximum of the three weighted points).
  - *Agreement*, per op, unweighted, for each pair of instances: the share of items on which the two give the same normalised answer,
    counting two `__missing__` answers as agreement (`pairwise`); the same share over the items both answered, with the count of those
    items (`pairwise_both_answered`, `both_answered_items`). Fleiss' kappa over the three instances, with `__missing__` as a category. A
    kappa is null when every rating falls in one category.
  - *Per-slice correct rates*: for each instance and slice, the unweighted share correct, per op and overall, and the voids and
    double-void state of each slice in `protocol`.
- **By depth and by set size.** For each instance and each model, the weighted accuracy and the item count for the relation depth
  buckets (0, 1, 2, 3, 4+, NONE) and the group set-size buckets (2, 3, 4 to 8). The 4+ bucket is six items from one league (`test-L10`),
  so its accuracy is one league's, and it should be read as such.

**Models-only mode.** While no instance has any ledger entry, `score.py` writes `results.json` with the 17 models alone, and the
three-instance summary marked incomplete.

## Representativeness (run before any answer; descriptive only)

`representativeness.py` sets each model's weighted sample values beside its published full-set values (relation, group and headline),
in `representativeness.json`, from `results.json`. The run gives:

| Metric | Spearman (17 models) | Mean absolute difference | Maximum absolute difference | Full-set value inside the sample 95% interval |
|---|---|---|---|---|
| Relation MCC | 0.998 | 0.012 | 0.042 | 17 of 17 |
| Group MCC | 0.924 | 0.020 | 0.063 | 16 of 17 (nimble-v3_9b-q8_0 outside) |
| Headline | 0.985 | 0.011 | 0.046 | 16 of 17 (tev1_4b-q8_0 outside) |

These counts are descriptive. They describe how the 500 sampled items track the full set for these 17 models. Because the intervals are
conditional on the sampled items, the inside counts are not a test of how representative the sample is. On these items the sample's
values track the full-set values closely for relations and the headline, and less closely for groups, where the 200-item sample is
smaller and its intervals are wider. Two models (kev_4b-q8_0 and tev1_0.8b-q8_0) predict `none` for every group, so their group MCC is 0
at every draw and their group interval is [0, 0]. These numbers are reported as they are.

## Disclosure: the conditions differ from the System One runs

- **Reasoning is allowed.** The reference test-taker may write prose before its answers. The System One child is one pass per request,
  with no generated reasoning. The reference is therefore not the System One condition.
- **A hosted model.** Claude Haiku 5.5, not a local System One model. Its training data is not known to us, so its scores cannot be
  separated from possible memorisation of these items (see Pinned inputs).
- **Cross-item context.** Each instance sees about 50 items in one context, about 2.8 per league, and the items of one league share one
  rule card. The instance can therefore use one item's answer to check another's, across the items of a slice. The System One child
  sends each question in its own request and holds no other item in view. Consistency across items is possible here and not there.
- **Groups are asked all at once.** A group answer gives the kind, the lower and upper bounds and the chain in one reply, with a neutral
  sentence and a different answer format from the child's kind-first flow (see Sample design, Prompts).
- **Market-side tags.** The prompts carry the `[label: Yes]` and `[label: No]` tags of each market proposition's side: all 300 relation
  prompts and all 200 group prompts of the sample carry them. The child builds these tags from the item, so the System One prompts carry
  them too, since they are the same text.
- **Three instances of one model.** The three instances are three sessions with the same prompt and no temperature or seed control.
  Their differences come from sampling alone. They are not three models, and their agreement is not evidence of three independent
  judgements.
- **Model set.** `kev_4b-q8_0` and `kev_0.8b-q8_0` were added after pre-registration (release/v0.1.0/record/prereg.md). They are
  scored here like the others.
  This reference re-scores no v0.2.0 cell.

## Status and scope

The reference is **unranked** and outside the System One scope of `SPEC.md`. It is not added to the leaderboard, to the headline, or to
any release. It is exploratory: its numbers describe one hosted model, with three instances, on one sample under the conditions above.
No change to a System One score or to any target follows from it.

## Outputs

- `sample.jsonl`, `key.jsonl`, `slices.json`: the design, written by `build_sample.py`, deterministically.
- `INSTRUCTIONS.md`: the test-taker brief.
- `run_slice.py`: the capture runner. It writes `raw/` (every attempt, with its stderr).
- `raw/ledger.jsonl`: one line per checked attempt (`inst`, `slice`, `attempt`, `raw_sha256`, `verdict`, `reason`).
- `answers/inst<i>/slice<j>.jsonl`: the accepted answers, written by `score.py extract`.
- `results.json` (schema `mentat-bench-reasoning-reference/1`): the models; the instances with their status, protocol (slice states and
  voids), per-slice correct rates, and by-depth and by-set-size accuracies; and the three-instance summary.
- `representativeness.json` (schema `mentat-bench-reasoning-representativeness/1`).

**Pins.** The committed `results.json` and `representativeness.json` are the models-only run, and a test checks that a fresh models-only
run reproduces their bytes, as the sample files are checked. Once answers are scored, these two files change and the pin is replaced in
the same commit.

Tests: `tests/test_reasoning_reference_sample.py` (a re-run gives the committed bytes, floors and totals, weights, the depth-4 cell,
slices, no leak) and `tests/test_reasoning_reference_score.py` (normalisation, the gold round trip, the weighted MCC against
`decision_scoring.mcc` on the expanded items, a model's own predictions through the extract path reproduce its weighted MCC, schema
errors versus voids, permission denials, the ledger's overwrite, second-void and changed-file rules, double void scoring, the capture
runner with a patched subprocess, the pins, and the instruction wording).

## Decisions made while writing this pre-registration

1. The allocation has floors first, then largest remainder over the population left after the floors. The brief did not say whether the
   floor is a pre-allocation or a rounding step; the pre-allocation is used, so that every cell meets its floor exactly.
2. The depth-4+ relations are pooled across strata and labels (one cell of six, all in league `test-L10`), and their weight is 1.
3. A group's kind is taken from its terms (`at_least_k+at_most_k` for a gold with both bounds), with k dropped, so that the cells are the
   kinds the terms carry and not the canonical label alone.
4. The prompt budget is 120,000 characters per slice. It does not bind (the largest slice is 104,135).
5. An answer whose value is outside its format is scored wrong, not voided. Only a protocol failure voids an attempt. A raw object that
   cannot be read as UTF-8 JSON with the required fields is a schema error. A missing or unreadable raw file is refused. Neither uses an
   attempt.
6. The extraction reads the single fenced `jsonl` block of the result text and keeps its lines verbatim.
7. Per-depth and per-set-size accuracies are weighted, so that they describe the test set, and the item counts are reported beside them.
8. `permission_denials` is optional: when absent it counts as none. A present, non-list value is a schema error.
9. The interval wording and the representativeness counts are descriptive, conditional on the sampled items.

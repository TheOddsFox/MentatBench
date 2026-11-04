# Transfer pilot for MentatBench v0.2.0: pre-registration

Written before any judge call, any pilot label, and any model run on the pilot, and committed before the first call. This is an
**exploratory pilot, not a ranked result**. It is not a leaderboard entry, it changes no score, headline, flag or label, and the
website decision waits for its result. Its question is whether the System One order of the 17 v0.2.0 models carries over to 200
items built from real Polymarket questions.

**Hashes of the frozen files** (sha256, as recorded in `frozen.json`). `score.py input`, `score.py extract`, `score.py preflight`, `run_slice.py`
and `analyse.py` refuse, before any CLI call, ledger read or write, when a listed file differs from its recorded hash. `frozen.json` also
records the repository inputs the rules, the runs and the analysis read, under `inputs`, and checks them the same way. It is written once,
before the first judge call: `score.py freeze` refuses once the ledger, the answers or a raw output exists (the local judge items, written
before freezing, do not count).

Frozen files:

- `JUDGE.md`: `709b7a6ac8df4a4db66f829242dc85eb4254b05a3460c10c4abcb5b1ab6c0978`
- `pool.jsonl`: `ade03aca53e53c4947121e06b223fb41066d3a20d7cd33b215c57f0466d864b9`
- `pool_summary.json`: `763b1e8cf5a3058045cabca7371b8a209f3513c626725897c09ceee6f423b7e0`
- `slices.json`: `048deb3f60418ea794bb9d9aa2e5da4040304fb28cc445929b11ea43f2d5a1c9`
- `snapshot_manifest.json`: `3bdd6e72fc40e5952b57e2a35eba040fe60710a3335bc240874bf1656c02c7ac`
- `fetch.py`: `6e4926d0bd05772c9e4b9e1d78cbb111e2aa712474bac655c3f52459915c4f6f`
- `rehydrate.py`: `a3c5cf69e117884a1f24b8ec4900f45923daf6c5cca8957d338608ed77eb791e`
- `build_pool.py`: `50389b98db8fc9f2552cc85c3d9741b8a03c36dcb54e1785a3cfe0a0b6df4c0f`
- `run_slice.py`: `8c7aaf2709f154e7f79caeffc4269a1fa9bfeaa53c3a66e50e38e384136a0d85`
- `score.py`: `2f6616544f752c1ae5242df1d62ff51fa94c1b2e208a32f0f3cd60260271d858`
- `analyse.py`: `513fd8c4a623e571706caea18de0a77f8924d71340777b4478a39ce0ee937572`

Recorded inputs (repository files, read by the rules, the runs and the analysis):

- `python/mentat_bench/decision_scoring.py`: `0e7f65030355677cdcbc87b59ddea31bc9ece5adc704a19c1bd12aeaf5ba390d`
- `tools/eval/league_ci.py`: `062fd44eccfe227e11b07756501a698cb8a2d1c68b0fe555555afdf105a6f0d1`
- `python/mentat_bench/adapters/base.py`: `a429e9aa172b4a507dbbdf2952169944f05c2b5023248dd389701bc4bee33df1`
- `python/mentat_bench/children/systemone_child.py`: `d81a62888f8b8a114d3f1852e676dfc11b526059eb8d2f36b5aca7ab42cb7869`
- `python/mentat_bench/children/ollama_models.json`: `83bdeed1120f8391d9a103d7216261e59891efb3730fb869a85b5f417dc0e542`
- `tools/reproduce.py`: `13e067f0b51c039991fcf89af590e38d99a876f80c77cb5be266200d1b170a44`
- `profiles/mentat-hard-v1.json`: `d8804e8167219dfe189a399cbffe724331dad2537beb1a50999b74ed05752a02`
- `results/v0.2.0/leaderboard.json`: `686a8c1474c8132287c0a8dd3b78fb98201b235dd7d5ead4a54660d91b95b457`
- `results/v0.2.0/analysis.json`: `d381fec2193ad47c65df9f4f3ed95cebccd6fc205b61cd92f1d3a8b707c3b6f6`
- `results/v0.2.0/baselines.json`: `50bea0c528d791e5120680a21d7161aeb7821324c99ff7217319e67bd7191b96`
- `results/v0.2.0/cells/clef-flash_9b.json`: `08a165a6073ef4b919b5572697f2b812990fc7f42866d37be229fbc1b5d5ffa0`
- `results/v0.2.0/cells/clef_27b-q8_0.json`: `b7f427953d6cfd2343b77fe2c4e4deaaeae30003a66877c74a4cca55e777b70c`
- `results/v0.2.0/cells/d1-omni_600m-q8_0.json`: `abc7f8fc84c403f80de59fdb4d43fce44155b6924dfbea60adac13773426c3d6`
- `results/v0.2.0/cells/d1_3b-q8_0.json`: `3322be992992b26c7749b0571d4ccae8d376f90f9d47fd41a88c808a5c6b7e87`
- `results/v0.2.0/cells/d3_27b-q8_0.json`: `7a6a0783ae5707895a013f62e29ef209d8f83dc3e32400747803ee917446e427`
- `results/v0.2.0/cells/d3_2b-q8_0.json`: `d53946d1e2e7d4d281ed642da0761d37d72f6bd7b59aef55aeec37fa445d4f87`
- `results/v0.2.0/cells/d3_4b-q8_0.json`: `191f0ef3b3fbfbcbad21f8b8819808dbfe9a2d122df6e3ff41f7d1cc2c819e15`
- `results/v0.2.0/cells/d3_9b-q8_0.json`: `2de22ef464c7c92e8a30637e981c695163ca23b8f45e2a6f610dc27674f60efa`
- `results/v0.2.0/cells/kev_0.8b-q8_0.json`: `6980c62647e017393d6c09f95284b4adb82b10f3fe525e7accfbb72df70ecfb4`
- `results/v0.2.0/cells/kev_4b-q8_0.json`: `0f34feaa1e883839f768079cf87b5292181d4bf0225296fbbaaea83fd355edbc`
- `results/v0.2.0/cells/kev_9b-bf16.json`: `dff853f76b04eff6c79e8d0d3c8c5fea79d25e7a2b50d1c791a4a9ee86a8a6a1`
- `results/v0.2.0/cells/laya_latest.json`: `ce5f68c6179ee9b484c033fc0237245ed36b8f515c273b942e171f04d2e37eeb`
- `results/v0.2.0/cells/nimble-v3_9b-q8_0.json`: `6e52fee4a15c626a20d4062b5f7b7592e908c967e66679c97bab830c2cdfd4bf`
- `results/v0.2.0/cells/nimble_9b-bf16.json`: `f2c08af7b9476854d555e21c52e1ecde1b1a4e8c60c7dc99beef75bd26027ca7`
- `results/v0.2.0/cells/pplx-decider_27b.json`: `b1a140cc72878354ba20f1e3963aa5cd7eb90fad5366951eb019160d4d557c35`
- `results/v0.2.0/cells/tev1_0.8b-q8_0.json`: `c9dcd8bae7f17a32c3a2276a970697dfe5c18596c32381371626747949bc2d80`
- `results/v0.2.0/cells/tev1_4b-q8_0.json`: `af9ea1e194a7251b3383a72de45295c09a09c34593df67f3e8fa405cc6cbdcf5`

The 17 cell files are the v0.2.0 cells of the 17 models. The two baselines (`lexical`, `heuristic`) have no cell file: their records are in
`leaderboard.json` and `baselines.json`, both listed above.

## Purpose

The v0.2.0 numbers come from one synthetic sports-league generator. Every item is generated from one template family, so the
leaderboard does not show that its order means anything on real market language. This pilot takes the same 17 models to 200 real
items and asks whether their order on those items matches the order on the v0.2.0 test set. The pilot is one of several checks; by
itself it can only say that the order carries over, does not carry over, or that the result is inconclusive.

## Terms, and what is published: ids, not texts

Polymarket's terms do not grant redistribution of question texts. The decision of 2026-11-03 is therefore to publish **ids, not
texts**.

- **Local only**, in `../mb-runs/transfer-pilot/` (a sibling of the repository), outside the repository: the snapshot (`snapshot/events.jsonl`,
  with question, outcome and event text, and event titles); the pool texts (`pool_texts.jsonl`, which carry each outcome's event title);
  the judge items (`judge/slices/`); the stdin and raw outputs of every judge call (`judge/raw/`, with the stdin saved beside each raw
  output); the rebuilt texts (`rehydrated/`); the review sheets (`review/`); the fixture's `corpus.jsonl`; and every run directory the model
  runs write (`runcell --out` and the `.build` work directories), since they hold the corpus and the decisions' texts.
- **Published** in this folder: the snapshot manifest (the query, the page record, the stop record, the counts, the sha256 of the local
  `events.jsonl`, and one row per event with its id, the sha256 of its series slug and of its category slug, its negRisk flag, its
  creation time, its volume and its market count); `pool.jsonl` (each item's opaque id, its outcome ids, the sha256 of each outcome's
  question, label and event title, the event id, its structural type, its cluster and its span flag); `pool_summary.json` (the counts quoted
  below); `slices.json` (the id lists and the sha256 of each judge item file); `JUDGE.md`; the judge ledger `judge/ledger.jsonl` (for each
  line: the instance, slice and attempt, the raw output's and the stdin's sha256, the verdict, a fixed reason code, and the sha256 of the
  answers of an accepted attempt); the canonical answers `judge/answers/`; `deviations.md` (each torn ledger line removed, with its sha256); `consensus.jsonl` and `consensus_summary.json` (the statuses,
  the labels, the judges' classes, and Fleiss' kappa); `review_labels.jsonl` (each reviewed item's ids, labels and the sha256 of its sheet)
  and `final_labels.jsonl` (ids and labels only); `selection.jsonl` and `selection_report.json`; the published fixture files (`candidates.jsonl`,
  `gold/suite_labels.jsonl`, `gold/meta.jsonl`, `fixture.json`), which hold no corpus, and `fixture_manifest.json` (the sha256 of each
  published file, of the corpus, and of the selection the fixture was built from); `results.json` (the statistics, with the sha256 of
  every input it read); the scripts; and the tests.
- **The repository holds no text.** Only ids, classes, decisions and hashes are copied into it. The analysis refuses a corpus, a review
  sheet or a fixture folder whose path is inside the repository (`analyse.py`, `refuse_repo_path`), and `rehydrate.py` refuses an output
  folder inside it. Run directories are pinned in the model-run section and guarded when the runner is written.
- **Rebuilding.** `rehydrate.py` refetches each pool market by id (`GET /markets/<id>`) and each pool event by id (`GET /events/<id>`),
  checks the sha256 of each question, label and event title against `pool.jsonl`, writes the texts (with their event titles) to a folder
  outside the repository, rebuilds the judge items from the published ids, and checks each rebuilt judge item file against the sha256 in
  `slices.json` and against the local copy. It exits non-zero on any mismatch. The analysis checks the local texts against `pool.jsonl` on
  every read, with the same comparison, and refuses a mismatch.
- **Event titles.** Each outcome's Polymarket event title is in the local corpus as `event_title`, as the v0.2.0 corpus records carry it.
  The System One child shows the title only in its binding prompt (`systemone_child.py` renders "Event: ..." there), so the models see the
  event title in binding prompts only, as in v0.2.0. The relation and group prompts never include it, and the judges never see a title.
- **Attribution.** The questions are Polymarket's. Any write-up names Polymarket as the source and says that the texts are not
  republished.
- **Tests** use synthetic data only. `tests/test_transfer_pilot_pool.py` builds synthetic events, outcomes, titles, ledgers, sheets and
  judge answers with invented strings that appear in no snapshot, in temporary folders.
- **The manifest.** The snapshot has 134,048 markets. Listing every market's hashes made the manifest 45 MB, so the manifest holds one row
  per event. The question, label and event-title hashes of the pool's outcomes are in `pool.jsonl`. The first manifest was rewritten by
  `fetch.py --rebuild-manifest`, which runs without the network: it checks that the local `events.jsonl` has the sha256 the manifest
  recorded, and it keeps the fetch time, pages, stop and fetch counts. The rewrite also replaced each slug with its sha256.

## Source and snapshot (`fetch.py`)

- **Source.** The Polymarket Gamma API, public, with no key: `GET https://gamma-api.polymarket.com/events` with limit 100, the offset,
  order `volume`, ascending `false`, and `start_date_min=2026-05-03T00:00:00Z`. The User-Agent is `mentatbench-pilot/0.1`. At most two
  requests a second. Retries with backoff (1, 2, 4, 8 seconds) on 429, 5xx and network errors.
- **Pagination cap.** Offsets above 2000 are refused: the API answers offset 2001 with HTTP 422, "offset too large, use /events/keyset
  for deeper pagination" (probed 2026-11-03). The pull stops at offset 2000 and returned 2,100 events. Events ranked below the 2,100th
  by volume are not in the snapshot. The keyset endpoint is not used.
- **Window.** The events kept have `createdAt` on or after 2026-05-03 (about six months). All 2,100 returned events meet this, and none
  is a duplicate. Closed and open events both count, because resolution is never used.
- **Fetch time.** `2026-11-04T07:13:23+00:00` (UTC). The pull is frozen at this point. The pilot is fixed to these bytes, and `fetch.py`
  refuses to pull again while the snapshot exists.
- **Snapshot counts.** 2,100 events; 134,048 markets; 419 negRisk events; 2,006 events with a seriesSlug (69 series); 94 without one;
  51 categories (the slug of each event's first tag). Every market has two outcome labels. Sports and esports markets dominate, which is
  why the pool's caps below exist.

## Pool (`build_pool.py`)

The build is deterministic. One seed, `transfer-pilot:v1`, drives every draw, in a fixed order, from one `random.Random`. Two builds give
identical bytes, and the order of the input events does not change the result.

**Propositions.** A proposition is one outcome of one market. Its text is the market's question and the outcome's label. Outcome ids
are `pm:<market_id>:<outcome_index>`. A relation item is an ordered pair of propositions (a, b). A group item is a set of 2 to 8
propositions from distinct markets, listed in a shuffled order.

**Eligibility.** A market gives propositions when its question is present; it is at most 230 characters; its outcomes are a list of two or
more labels; and its question is English and not a placeholder. A placeholder is a market whose question or group title contains the word "placeholder"
(Polymarket's stand-in markets, which read "Placeholder" followed by a number); it is dropped first. The English test is a heuristic: a question is non-English when more than 20% of its letters are
outside ASCII, or when it has more non-English function words than English ones (the word lists are in `build_pool.py`). The placeholder test removed 22 markets, and the English test 1,437. The 230-character cut removed none in this snapshot.

**The child's instruction cap.** The child refuses a relation whose instructions exceed `MAX_INSTRUCTION_CHARS` (300,
`systemone_child.py:101`, checked at line 723). The pool renders each candidate relation with the child's own function
(`systemone_child._relation_question`, the instructions the child builds for the pair in the order the pool gives it), and a candidate
over the cap is **rejected and redrawn** from the same seeded generator. Group states have no character cap in the child; a group's size
is bounded by `MAX_K` and `MAX_CHAIN` (8 and 26), and the pool's groups have at most 8 members. In this snapshot no relation was rejected:
the rendered instructions run from 81 to 169 characters. The count of rejections is in `pool_summary.json`
(`stats["relation rejected: child instructions over 300 characters"]`, absent when zero).

**Caps and dedupe, to a fixed point.** The series cap (at most three events per seriesSlug, by volume), the category cap (no category
of an event's first tag holds more than 25% of the kept events; while one does, the lowest-volume event of the largest category is
dropped) and the duplicate rule (two questions are the same when they are equal after lower-casing and keeping only word characters) are
applied together, in rounds, until a round changes nothing. In each round the series cap and the category cap are applied to the events
still kept, and then the duplicate rule keeps the first market of each question in volume order and drops an event that loses all its
markets. A question is therefore represented by its highest-volume copy among the events that survive the caps, and every cap holds on the
final set. Each event is counted once, under the reason it was dropped. The category cap needs at least four categories among the events
it sees, and refuses otherwise rather than emptying the set.
The result: 232 kept events, 5,662 kept markets, and 51 categories. Sports holds 58 of the 232 kept events, exactly 25%. Events dropped by the
series cap: 1,858; by the category cap: 9; one event lost all its markets to duplicates. Markets of the kept events dropped as duplicates of
a higher-volume kept event's question: 690.

**Items are unique per operation.** A relation pair (as an unordered pair) and a group set (as a set) each appear once among their own
operation. A relation and a group may share outcomes; the duplicate rule above applies to markets, not to items.

**Thresholds and ladders.** A market's threshold is a number its question holds, outside a date, outside a score ("2-1"), and not an
index: a number directly after one of the index words (`player`, `map`, `game`, `round`, `set`, `quarter`, `q`, `week`, `race`, `stage`,
`match`, `leg`, `half`, `period`, `inning`, `day`, `episode`, `season`, `part`, looking at the two words before it), a number with a `q`
directly before it (Q1), a number directly followed by an uppercase period marker `Q`, `H`, `P` or `OT` (1Q, 2H, 3P, 1OT), or an
ordinal (1st, 2nd, 3rd, 4th). The threshold is the single number of the group item title when the question
holds it in that way, and otherwise the number after a threshold word ("above", "reach", "O/U", "over", "under", and similar). A threshold
has a template: the question with that number masked. A **threshold ladder** is two markets of one event with the same template and two
different thresholds. A **date ladder** is two markets of one event whose questions are equal once their dates are masked (a month with an
optional day, or a four-digit year), whose dates differ, and whose masked template has at least three words. A **group ladder** is between
2 and 8 markets of one event that share a template (a threshold template or a date template).

**Clusters.** A union-find joins kept events that share a seriesSlug. Each cluster is numbered `C0001`, `C0002`, … by its smallest event
id. The kept events form 156 clusters. The item-level clusters (each item's cluster, below) take 131 distinct values, and an item's
outcomes may touch more clusters (the span flag below). Tags do not join events. An item's cluster is the **smallest cluster
id among its outcomes** (for a relation, of `a` and `b`; for a group, of all its members). An item **spans** clusters when its outcomes lie
in more than one cluster: 183 items span: 70 random_cross, 83 same_topic and 30 decoy items. The other 377 items lie in one cluster. (A
same_topic item spans when its two events share a category but not a seriesSlug.)

**Structural types and quotas.** Each generator draws random candidates from the kept events. A candidate is kept when it is new (an
unordered pair of outcomes, or a set of members, that has not appeared), meets its type's rule, and passes the child's cap. Each pair's
order is a coin flip, so Yes and No outcomes appear on both sides. Generation stops at the quota, or after 300 times the quota in attempts.

| Op | Type | Quota | Rule |
|---|---|---|---|
| relation | `same_event_alt` | 110 | Two different markets of one event (an event with at least two markets). |
| relation | `threshold_ladder` | 50 | A threshold ladder (above). |
| relation | `date_ladder` | 40 | A date ladder (above). |
| relation | `trivial` | 40 | The two outcomes of one binary market (Yes and No, Over and Under, or two team names). |
| relation | `same_topic` | 90 | Two markets of two different events that share a seriesSlug or a first-tag category. |
| relation | `random_cross` | 70 | Two markets of two different events, at random. |
| group | `event_fields` | 60 | Between 2 and 8 markets of one event, one outcome from each. |
| group | `ladder` | 40 | A group ladder (above). |
| group | `mixed` | 30 | Between 2 and 8 outcomes from two events of one seriesSlug (one cluster), at least one from each. The order is redrawn until the members are not two contiguous event blocks (groups of three or more members): up to 20 shuffles, then an alternating order, so that the two events interleave. |
| group | `decoy` | 30 | Between 2 and 8 outcomes, one from each of several random events, in at least two clusters. |

No quota fell short. The pool has 400 relations and 160 groups, 560 items. The share of outcomes labelled No is 32.8% of the 1,272 distinct
outcomes the items use. The 40 trivial items are 10% of the relations in the pool.

**Ids and the judge order.** After one seeded shuffle of the 560 items, they receive the ids `T0001` to `T0560` in shuffled order. The
published `pool.jsonl` is sorted by id and holds, per item: `id`, `op`, `structural_type`, `cluster`, `span`, the outcome ids (`a`, `b`
for a relation, `members` for a group), and `text_sha256` (for each outcome: the sha256 of its question, of its label and of its event
title, and its event id). The judges see only the id, the type (`relation` or `group`), the prompt and the answer format.

**Judge slices.** The 560 items form 12 slices, as equal as possible (eight of 47 items and four of 46), in the shuffled order.
Relations are 30 to 39 of the items in each slice, groups 7 to 17. `slices.json` publishes each slice's ids and the sha256 of its judge
item file.

## Judge brief and prompts

`JUDGE.md` is the brief, and it is frozen. Its semantics are the catalogue proposition semantics of `SPEC.md` section 2: what holds in
every interpretation consistent with the question text and the outcome label alone. It uses no resolution rules, no descriptions, no
outside facts and no current-world facts. Event membership does not, by itself, make outcomes exclusive or exhaustive. Its reading
conventions follow the v0.1.1 item audit: a label of "No" negates the question, and "Yes" is the question as written.

- **Relation prompt.** Two propositions, A and B, each with its question and its outcome. The answer is one of `EQUIV`, `IMPLIES`,
  `IMPLIED_BY`, `EXCLUDES`, `NONE` or `UNCLEAR`. `IMPLIES` means A implies B and B does not imply A. `EXCLUDES` means A and B cannot both
  hold. `NONE` means neither relation holds and they can both hold. The first of EQUIV, IMPLIES, IMPLIED_BY and EXCLUDES that holds is
  the answer; otherwise the answer is NONE.
- **Group prompt.** The members, numbered 1 to n, each with its question and its outcome. The answer is the kind (`none`,
  `exactly_one`, `at_most_one`, `at_least_k`, `at_most_k`, `ordered_chain`, or `UNCLEAR`), the bounds `lower` and `upper` on the number
  of true members (integers from 0 to n), and the chain (member numbers, strongest first, for `ordered_chain`; otherwise `[]`).
- **Answer format.** As in the reference's `INSTRUCTIONS.md`: a single fenced `jsonl` block at the end of the reply, one line per id,
  and nothing else in the block.
- The brief does not mention a benchmark, a gold, a repository or any other item, and it says that no tool and no delegation are used.

## Judging (`run_slice.py`, `score.py`)

There are three separate sessions of one model (`claude-haiku-5-5`); they are not three independent models. Each slice is answered by
each session once. That is 36 sessions before any re-run. No temperature or seed is set, and the CLI offers no control of either, so
sessions differ by sampling only, and their agreement is not evidence of independent judgements.

**Preflight gate.** Until a preflight passes, only instance 1 may be called, one slice at a time in slice order: instance 1, slice 1 first,
and the next slice only after the previous instance-1 slice is settled (accepted, or double void by the rules below). A double void while
the gate is closed stops the pilot (Stop, below). As in the reference's PREREG, the first raw output is inspected. The operator runs
`python -B score.py preflight` after a call of instance 1. That command checks the raw output of the latest called line of instance 1,
whatever its slice: its error flags, its shape (one result object, one fenced block, the right ids), and its tool use (no permission
denials, one turn). It records a `preflight` line with `pass` or `fail` and the reason. A failed check can be repeated after the next call.
A raw output that a schema error moved aside is checked under the name its `schema_error` line records, so a failing first call is recorded
as `fail`. Once a check passes, the gate is open for every instance and slice, and `score.py` refuses nothing on this account.

For instance i in {1, 2, 3} and slice j in 1 to 12, the operator runs

    python -B run_slice.py i j 1 --workdir <an empty folder outside the repository>

It runs, from that folder, `claude -p --safe-mode --model claude-haiku-5-5 --tools "" --strict-mcp-config --no-session-persistence
--output-format json`, with the stdin of `score.py input j` (JUDGE.md, a blank line, then the slice's item lines) passed as bytes, and
the environment variable `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, with a timeout of 1,800 seconds.

**The call lines.** Before the subprocess starts, `run_slice.py` saves the stdin as `judge/raw/inst<i>/slice<j>/attempt<k>.stdin.txt` and
writes a `calling` line to the ledger (the stdin's sha256). When the subprocess returns, it saves the stdout as
`attempt<k>.json`, the stderr beside it, and then writes a `called` line (the raw output's sha256). A call with no closing line is
**pending**, and no new call is made while one is pending. A call that is interrupted, or that times out, stays pending. A calling line
younger than 1,800 + 120 seconds may be a call still running: `extract` refuses to close it, so an in-flight call cannot be voided.

**Closing.** `score.py extract i j k` closes the pending call of that attempt, with a line whose verdict is one of:

- `valid`: the raw output is a valid result for the slice's ids. The attempt is used. The canonical answers are written to
  `judge/answers/inst<i>/slice<j>.jsonl`, and the line records their sha256.
- `void`: the attempt is used, with a fixed reason code. The codes are `raw_missing` (a call with no saved raw output, or an interrupted
  call), `raw_unrecorded` (a raw output was saved, but its `called` line was not written), `raw_mismatch` (the raw output is not the one the
  called line recorded), `stdin_mismatch` (the saved stdin is not the one sent, or the items changed since the call),
  `schema_error_repeats` (the second schema error on the attempt), `error_result` (an error object, with or without a result field),
  `num_turns:<n>`, `permission_denials:<n>`, `fenced_blocks:<n>`, `line_not_json:<position>`, `line_not_object:<position>`,
  `id_not_string:<position>`, `id_repeated`, and `ids_mismatch:missing=<n>,extra=<n>`.
- `schema_error`: the raw output is not a result the protocol can read (`not_utf8`, `not_json`, `not_object`, `missing:<field>`,
  `type:<field>`). `extract` moves the file aside as `attempt<k>.schema-error-<n>.json`. A schema error does not use up the attempt. The
  **second schema error on an attempt closes that attempt as void, with reason `schema_error_repeats`**, so the next attempt (or the
  double-void rule) applies. The second schema error and its void line are written in one append, synced to disk. An attempt with two
  schema-error lines and no void line (a torn append) counts as closed. An error object is not a schema error: it is a void, whether or
  not it carries a result field.

**Parse order.** A raw output is read as UTF-8 (a BOM is accepted) and must be one JSON object. `is_error` must be present and a boolean.
When it is true, the object is a void (`error_result`), whatever else it holds, and needs no `subtype`. Otherwise `subtype` must be present
and a string, and a subtype other than `success` is a void (`error_result`). Only a success object must then have `num_turns` (an integer),
`result` (a string) and, when present, a list of `permission_denials`.

Every closing line records the raw output's sha256 as it is on disk, so a line written after a mismatch still verifies. A void reason
never contains judge text, and neither does a line of the ledger or of the answers.

**Attempts.** An attempt is **closed** by a void line, or by a torn schema-error pair (two `schema_error` lines of one attempt with no void
line). The next attempt is the lowest attempt number not closed, and no third is made. Two closed attempts make the slice **double void**.
A slice is **settled** when it is accepted or double void. `analyse.py` uses `score.py`'s `settled` and `closed_attempts` for its
completeness check and its double-void count, so the two never disagree. An instance outside 1 to 3, a slice that is not in
`slices.json`, and an input for an unknown slice are refused.

**Canonical answers.** A valid attempt's answers are rewritten from parsed values, never copied. A relation answer is kept only when its
label is one of `EQUIV`, `IMPLIES`, `IMPLIED_BY`, `EXCLUDES`, `NONE` or `UNCLEAR`. A group answer is kept as its kind (one of the six
kinds, or `UNCLEAR`), its integer bounds from 0 to n, and, for an ordered chain, its distinct integer positions from 1 to n. Any other
answer is recorded as `{"invalid": true}`.

**Verification.** `verify_ledger` runs before every call and every extract. It checks each closing line's raw output (under its name, or
the schema-error name it was moved to), the saved stdin (against the line's sha256), and every valid line's answers, which are re-derived
from the raw output and compared with the answers file. Calling, called and preflight lines of a pending call are checked when that call is
closed. `score.py freeze` refuses once the ledger, the answers or a raw output exists.

**Repairs.** `read_ledger` refuses a ledger line that is not JSON and names its line number. Only a torn last line (a line a crash left
half-written) may be removed, by the operator, by hand. Each removal is recorded in `deviations.md` with the removed line's number, the
sha256 of the removed line as written (without its line break), and the reason; the removal is a deviation from this PREREG. No other line
is edited or removed, and the removed text is not kept in the published files.

**Interrupted closes.** A schema error renames the raw output aside before its line is written. If `extract` finds a called line with no
raw output under its name, and the aside file for that schema-error repeat has the called line's sha256, it finishes that close as the
schema error. Otherwise the missing output is closed as `raw_missing`.

**Stop.** The pilot stops when the ledger shows a `double_void_gated` state (an instance-1 slice ends double void while the preflight gate
is closed) or a `late_call` state (a `called` line is written after its attempt was closed, for example when the subprocess returns after
`extract` closed the call as `raw_missing`). A `stop` line with that fixed reason code is written at the close or the refused call that
causes it. From then on `score.py` refuses every call and every extract (`stopped: <code>`). A stop is a protocol failure to be investigated.
The stop line is not edited. Resuming requires a recorded deviation in `deviations.md` that gives the cause and the change; the resumed run
is a new protocol version (a new PREREG, frozen again, with a fresh ledger), and the stopped ledger is kept as it is.

**Catch-all.** Any ledger state the code refuses as unexpected pauses the pilot. The operator records the state and the resolution in
`deviations.md` before any further call. No ledger line is edited by hand, except a torn last line under Repairs.

## Labels (consensus, `analyse.py consensus`)

Each session's canonical answer is normalised by `score.py normalised`. A relation answer is its label. A group answer is `UNCLEAR` when
its kind is `UNCLEAR`, `none` when its kind is `none`, and otherwise `terms_class(group_terms(lower, upper, n, chain))`, the canonical class
of `decision_scoring`, so the bounds decide the class (for example `at_least_k:2`). An invalid answer, and a session with no accepted answer
for an item (a double-void slice), is `__missing__`, which is not a valid answer.

The valid answers of an item decide it, in this order:

- **Three valid answers.** All three equal: unanimous. Two equal: a majority (2-1). All different: review (reason `all_different`). A
  majority on `UNCLEAR` is **dropped**, and is not reviewed (three `UNCLEAR`s, or two `UNCLEAR`s and one other).
- **Two valid answers** (one session gave an invalid answer or was double void). Equal: `two_agree`. Different: review (reason
  `two_disagree`). An agreed `UNCLEAR` is dropped.
- **Fewer than two valid answers:** review (reason `fewer_than_two`).

Fleiss' kappa over the three sessions, for relation and for group labels, with `__missing__` as a category, is written to
`consensus_summary.json` with the status counts. A review item is labelled by the user; a dropped item has no label and is not selected.

## Review (`analyse.py review`)

The review sheet is `../mb-runs/transfer-pilot/review/review.csv`. It is LOCAL, because it holds question text.
Its columns are `id`, `op`, `reason`, `propositions`, `judge1`, `judge2`, `judge3`, `consensus` and `your_label`. The header lines (starting
`#`) give the vocabulary.

- **Recomputed, not trusted.** `analyse.py review` recomputes the rows the first sheet must hold, from `consensus.jsonl` and the seeded spot
  draw: the review items with their reasons, and the spot-check sample. The sample is the labelled items (unanimous, majority and two-agree),
  sorted by id, and `ceil(0.10 × n)` of them drawn with `random.Random("transfer-pilot-review:0")`; each has reason `spot_check`. The sheet's
  ids must equal these exactly, and each row's reason must equal the reason recomputed for it. A missing, extra or reason-changed row stops
  the run. A row for a unanimous, majority or dropped item that is not in the set is refused the same way.
- **Vocabulary, validated strictly.** A relation row takes `EQUIV`, `IMPLIES`, `IMPLIED_BY`, `EXCLUDES`, `NONE` or `UNCLEAR`. A group row
  takes a class in the exact `terms_class` form that the group's size allows (for example `none`, `exactly_one`, `at_most_one`,
  `at_least_k:2`, `at_most_k:2`, `ordered_chain`, or `at_least_k:2+at_most_k:3`), or `UNCLEAR`. `UNCLEAR` drops the item. A blank label, a
  label outside the vocabulary, an unknown id, a duplicate id, a changed header, or an op that does not match the pool stops the run.
- **Recorded consensus.** Before a review or a score reads anything, the consensus is recomputed from the ledger, its raw outputs and the answers
  files, and the run is refused if it differs from `consensus.jsonl`.
- **Changed.** A spot-check item is **changed** when its label differs from its consensus label, including a drop. A review item has no
  consensus label, so it is reviewed, not changed: `excluding_changed` (below) removes only the spot-check items the reviewer changed.
- **Escalation.** If more than two spot-check items are changed, `analyse.py review` writes `review_escalation.csv` with every remaining 2-1
  item (every majority item outside the spot-check sample) and stops. The run goes on only when that sheet holds exactly those items, each
  with reason `escalation`. Selection cannot run before the review is complete, because it reads `final_labels.jsonl`, which the review
  writes.
- **Never overwritten.** `analyse.py consensus` refuses to write a sheet that already has any label, and `analyse.py review` refuses to record
  labels that differ from those already recorded (a re-run must reproduce them). A sheet is written once; a changed
  label is a new decision, recorded in the published output below, and the sheet itself is not edited afterwards.
- **Published labels.** `review_labels.jsonl` has a row for each reviewed item: its id, op, reason, consensus label, final label, whether it
  was changed, and the file name and sha256 of the sheet that holds it. `final_labels.jsonl` has a row for each item: its final label (null
  when dropped) and its source (`reviewed`, `unanimous`, `majority`, `two_agree` or `dropped`). Both hold ids and labels only.

## Selection (`analyse.py select`)

The sample is 120 relations and 80 groups, drawn from the labelled items only. Each cell's draw is a simple random sample without
replacement, with cells in name order and items in id order, from `random.Random("transfer-pilot-select:0")`.

- **Relations.** The **trivial** cell holds the trivial-type items, whatever their label, and takes `min(12, its size)` seats (12 is 10% of
  120). The other relations share the rest: `120 minus the trivial seats drawn`, and that number is the seat total the label cells receive.
  The label cells are **EQUIV**, **IMPLIES|IMPLIED_BY** (both labels together), **EXCLUDES** and **NONE**. The shares are set by the reference's
  rule (`docs/audits/v0.2.0-reasoning-reference/build_sample.py`, `allocate`): each non-empty cell takes `min(its size, floor)`, with floor
  `(120 minus the trivial seats) // (number of non-empty label cells)`; the seats left are shared in proportion to each cell's size left
  after the floor, by largest remainder (integer arithmetic; ties to the smaller cell name). A cell smaller than its share takes all of it.
  If the labelled items cannot fill the seats, the sample is smaller, and the report says so.
- **Groups.** A cell is the kind of the final label with `k` dropped (the kinds of its terms, joined by `+`; `none` for no constraint),
  crossed with the set-size bucket (2, 3, or 4 to 8 members). The 80 seats are shared over the non-empty cells by the same rule, with floor
  `80 // (number of non-empty cells)`.
- **Report.** `selection_report.json` gives each cell's pool size, allocated seats and drawn items, the totals, the trivial share of the
  drawn relations, the drawn count per relation label and per group kind, the classes with fewer than 5 drawn items, and the sha256 of the
  `final_labels.jsonl` it read. **A relation label or a group kind with fewer than 5 drawn items is reported, but gets no per-class
  statement** in the write-up.

## Fixture (`analyse.py fixture`)

The fixture of the selected items has the v0.2.0 contract shapes. `corpus.jsonl` (LOCAL: each outcome's `id`, `text` (the question), `label`,
`event_title` and `controlled: true`, with no `context`) is written only to the local folder. `candidates.jsonl` (`decide_relation` with `a`
and `b`, or `decide_group` with `members` and `source: "curated"`). `gold/suite_labels.jsonl` (the final label as `label`; for a group also
`terms`; `depth` null; `stratum` `trivial`, `decoy` or `real`). `gold/meta.jsonl` (each outcome's `league`, which is its cluster, with its
market and event ids). `fixture.json` (version "3.1", generator `polymarket-gamma-snapshot`, `controlled: false`). The runnable fixture is the
local one; the published folder holds the same files without `corpus.jsonl`, and `analyse.py` refuses to publish a folder that holds a corpus.
`fixture_manifest.json` holds the sha256 of each published file, of the corpus, and of the `selection.jsonl` the fixture was built from.

## Model runs (Phase 3, fixed now)

- **Fixture and run folders.** The runnable fixture, with its corpus, is built under `mb-runs\transfer-pilot\fixture`, and every runcell
  `--out` and `.build` work directory is under `mb-runs\transfer-pilot\runs`. Only ids, classes, decisions (as classes) and hashes are copied
  into the repository. No run directory is an input to the analysis yet: the runner's paths are pinned here, and the runner is guarded when its
  code is written. The analysis refuses a review sheet, a fixture corpus or a fixture folder inside the repository.
- **Settings.** Each model's argv and environment come from `tools/reproduce.py` `runcell_argv(cell, out)` and `run_env(model, url,
  stats)`, for the cell recorded in `results/v0.2.0/cells/<id>.json`: its profile (`profiles/mentat-hard-v1.json`), its adapter and local
  child, and its build flags, with its recorded `--relation-floor`. The pilot inherits each cell's floor. The floor is a v0.2.0 calibration
  on the test fixture, and carrying it over is a choice made here.
- **Pins.** Ollama 0.40.13. Each tag is pinned by its digest in `python/mentat_bench/children/ollama_models.json`.
- **Predictions.** One file, `{model: {item id: class}}`, with a class for each selected item from each of the 17 models and from the two
  baselines (`lexical`, `heuristic`). A class is in the normalised form of `decision_scoring`. The mapping from a model's decisions to this
  file is made at Phase 3 and records `__missing__` for an item with an error or with no decision: a missing decision is a wrong answer for
  that item, and it is written as such, not left out. `analyse.py score` refuses a file that lacks any of the 17 models or the two baselines,
  that lacks any selected item for any of them, or that holds a class which is not a label of the item (or `__missing__`).
- **Baselines.** The two baselines are scale anchors. They are reported in `results.json` with the same draws and intervals as the models,
  and they are not in the correlation.

## Statistics (`analyse.py score`)

- **MCC.** `decision_scoring.mcc` on the (gold, prediction) pairs of each operation, without weights. It returns 0 when there are no pairs
  or when the gold or the predicted marginal is constant; an undefined MCC therefore counts as 0. The headline is the mean of the relation
  MCC and the group MCC, as in v0.2.0.
- **Bootstrap.** Clusters are resampled: `league_ci.draws(2000, 0, L)` (the generator `random.Random("league_ci:0")`), with L the number of
  distinct clusters among the 200 items. Each item's league is its cluster: the smallest cluster id among its outcomes. `league_ci.read_fixture`
  is not used, since it would refuse spanning items. The same draws serve every model and both baselines, so the comparisons are paired.
  In each draw an item counts as many times as its cluster's multiplicity.
- **Spearman's rho.** Average ranks for ties. A vector with no variation (a constant) gives rho = 0, for the point estimate and for each
  draw; a draw in which either rank vector is constant gives rho = 0 for that draw.
- **Kendall's tau-b.** Pairs tied in one vector, or in both, are excluded from the concordant and discordant counts, and each tie adjusts
  the denominator. A zero denominator gives 0.

**Primary.** Spearman's rho between the 17 models' pilot headlines (on the 200 items, each item once) and their v0.2.0 headlines
(`results/v0.2.0/leaderboard.json`, `metrics.headline.point`). The 95% interval is the 2.5 and 97.5 percentiles (numpy, linear) of rho over
the 2,000 draws. The point estimate is rho on the full sample.

**Reading, fixed now, applied as written.**

- *Carries over:* the interval's lower bound is at least 0.6.
- *Does not carry over:* the interval's upper bound is below 0.6.
- *Inconclusive:* otherwise.

**Secondary statistics.**

- Kendall's tau-b between the pilot and v0.2.0 headlines, with its interval.
- Spearman's rho for the relation MCC alone and for the group MCC alone, with their intervals.
- **Resolved pairs.** The v0.2.0 analysis resolves 117 of the 136 headline pairs under Holm (alpha 0.05; `results/v0.2.0/analysis.json`,
  `pairwise.headline` with `resolved: true`). For each, the pilot keeps the order when the pilot headline of the v0.2.0 higher model is
  strictly greater than that of the other model. **A pilot difference of exactly 0 does not keep the order.** The share kept is reported.
  (An earlier design note named 89 pairs. The v0.2.0 analysis gives 117 for the headline; 80 are resolved for groups and 124 for relations.
  The pre-registered count is the headline's 117.)
- Each model's pilot relation MCC, group MCC and headline, each with its interval, beside its plain accuracy on relations and on groups.
- The two baselines (`lexical`, `heuristic`), reported in the same form, as scale anchors.
- Fleiss' kappa over the three sessions, and the counts of unanimous, majority, two-agree, review, dropped and double-void items
  (`consensus_summary.json`).
- **Double void.** The number of double-void sessions, and the items of their slices, are written to `results.json`. A double-void slice
  leaves its items with one session fewer: their consensus is decided on the sessions that answered (two-agree, or review), so no item is
  dropped for that reason alone. The double-void count is reported beside the consensus counts.

**Sensitivity.** The primary statistic is recomputed on three subsets, and each result gives its item count:

- `unanimous_only`: the items whose consensus status is `unanimous`;
- `excluding_changed`: all items except the spot-check items the reviewer changed (review items are not removed, since they have no
  consensus to change);
- `excluding_trivial`: all items except the trivial relations.

A sensitivity result does not replace the primary reading.

**Recorded inputs.** `results.json` records the sha256 of the predictions file, of `final_labels.jsonl`, `selection.jsonl`,
`review_labels.jsonl`, `consensus.jsonl` and, when present, `fixture_manifest.json`. Each of `selection_report.json`, `fixture_manifest.json`
and `consensus_summary.json` records the sha256 of its own inputs.

## Disclosures

- **Labels are model labels with partial human review.** Three sessions of one model labelled every item. The user reviewed the review
  items, a seeded 10% of the rest, and, when the escalation rule applies, the remaining 2-1 items. The labels are not exact gold and can be
  wrong. The review rate and the number of changes are reported.
- **Memorisation is possible.** The questions are public and recent (created since 2026-05-03). The models and the judge sessions may have
  seen them. This cannot be separated from ability.
- **Event titles.** The models see the event title in the binding prompt only, as in v0.2.0. The relation and group prompts never include
  it, and the judges never see it.
- **The judge prompts differ from the child's.** The judges see propositions and labels. The models are run on the fixture's corpus through
  the child's own prompts. The pilot has no `context` (rule card) for the child, since no rule card exists for real markets.
- **The conditions differ from System One's.** Each judge session answers about 47 items in one context, so consistency across items is
  possible in judging. The child works one question at a time.
- **The pool is sports-heavy.** Sports holds exactly 25% of the kept events, and the other kept categories are small. Category shares of
  the selected items are reported.
- **The sample is not representative.** It is stratified by label, structural type and kind, and the MCCs are unweighted. They describe these
  200 items. They are not estimates of the Polymarket population.
- **Clusters are coarse, and some items span them.** Clusters follow the seriesSlug. Random cross-event items, same-topic items that share
  only a category, and decoys can span clusters; their cluster is the smallest id among their outcomes. Mixed, event, ladder and trivial
  items stay in one cluster. The pilot's bootstrap does not use `league_ci.read_fixture`, which would refuse spanning items.
- **Trivial items.** A trivial item is the two outcomes of one binary market: Yes and No, but also Over and Under, and two team names. Their
  labels are reported. They are capped at 12 seats (10% of 120); the pool holds 40 of them.
- **Sessions are not models.** The three sessions are three separate sessions of one model. They are not three independent models.
- **Two models were added after the v0.2.0 pre-registration.** `kev_4b-q8_0` and `kev_0.8b-q8_0` are scored like the others, as in the
  reference.

## Decisions made while writing this pre-registration

1. Ids, not texts: the local folder holds all texts, and the published files hold ids, structure and sha256 hashes. The manifest holds one row
   per event, with its slugs hashed, because listing 134,048 markets was 45 MB. The pool's outcomes carry their own hashes, including the
   event title's.
2. The pagination cap is offset 2000, which gives 2,100 events. The pull ends cleanly at the cap, and the keyset endpoint is not used.
3. The English test is a heuristic with a 20% non-ASCII letter level, because a 5% level dropped short names with one accented letter.
4. A threshold is a number the question holds outside a date, a score and an index. A ladder needs the same template with that number masked.
   Group ladders need a shared template too. Index numbers (players, maps, rounds, Q1, 1Q, ordinals) are not thresholds.
5. Tags do not join clusters. Only seriesSlug does, and the series cap of three keeps the clusters small.
6. The series cap, the category cap and the duplicate rule run in rounds until nothing changes, so the dedupe keeps the highest-volume copy
   that survives the caps, and every cap holds on the final set. The category cap refuses a set with fewer than four categories.
7. Mixed groups are redrawn until they are not two contiguous event blocks, with an alternating fallback.
8. A spanning item's cluster is the smallest cluster id among its outcomes.
9. The consensus, the review and the selection rules are fixed here and implemented in `analyse.py`. The review sheet has two columns beyond
   the first list (`reason` and `consensus`), which the recomputed checks and the changed count need.
10. The selection's shares are set on `120 minus the trivial seats` for relations and on 80 for groups, with floors `// (number of non-empty
    cells)`, so that the floors cannot use all the seats.
11. An earlier design note gave 89 Holm-resolved pairs, which does not match `results/v0.2.0/analysis.json` (117 headline pairs are resolved).
    The pre-registered count is 117.
12. The statistics use `decision_scoring.mcc` on the resampled pairs, not `league_ci._mcc`. Both return 0 for a constant marginal, and the
    draws come from `league_ci.draws`.
13. A missing decision is a wrong answer, written as `__missing__` by the mapping from decisions to predictions, and never silently dropped by
    the analysis. The analysis refuses a predictions file that lacks an item.
14. "Changed" is defined for spot-check items only. A review item has no consensus, so it is reviewed, not changed.
15. The judging has a preflight gate (until it passes, instance 1 only, one slice at a time in order); every call is written as a calling line and then a called line, and every
    mismatch closes an attempt as void with a fixed code, so an interrupted or changed call cannot strand an attempt.
16. Placeholder markets are not questions: they are dropped before any draw, and the count is in `pool_summary.json`.

## Outputs

- `fetch.py`, `snapshot_manifest.json` (published); `mb-runs/transfer-pilot/snapshot/events.jsonl` (local).
- `build_pool.py`, `pool.jsonl`, `pool_summary.json`, `slices.json` (published); `pool_texts.jsonl` and `judge/slices/` (local).
- `rehydrate.py` (verifies the hashes, restores and verifies the event titles, and rebuilds the judge input; refuses an output inside the
  repository).
- `JUDGE.md`, `run_slice.py`, `score.py` (the judge protocol), `judge/ledger.jsonl` and `judge/answers/` (published, written by the calls),
  and `deviations.md` (published; one entry per torn ledger line removed).
- `analyse.py`: `consensus.jsonl`, `consensus_summary.json`, `review_labels.jsonl`, `final_labels.jsonl`, `selection.jsonl`,
  `selection_report.json`, `fixture_manifest.json`, the published fixture files, and `results.json` (published); the review sheets and the
  corpus (local).
- `frozen.json` (`mentat-bench-transfer-frozen/1`): the sha256 of each frozen file and each recorded input.
- `tests/test_transfer_pilot_pool.py`: synthetic tests of the pool, the hash checks, the fetch, the judge protocol (calls, closings, reason
  codes, schema-error repeats, re-derivation, the freeze guard, the inputs, the gate and its slice order, the in-flight grace, the torn-line
  refusal, the parse order, the period-marker index rule), and the analysis, including a full synthetic pipeline from the
  judges' answers to the scores.

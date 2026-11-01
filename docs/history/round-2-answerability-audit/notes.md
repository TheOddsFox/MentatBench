# Mentat-Hard v2 answerability sample (dev split), notes

Source: worktree `w9-audit` at origin/main 2b13715, `fixtures/mentat_hard_dev`. Seed `random.Random("audit:2026-10-29")`.
Files: `sample.jsonl` (150 rows), `key.jsonl` (150 rows), `score.py`, `build_sample.py` (writes both), `verify.py` (self-check).

## Counts
| type | stratum (key) | n | source |
|---|---|---|---|
| relation | EQUIV / IMPLIES / IMPLIED_BY / EXCLUDES / NONE | 6 each (30) | pools 800 / 1337 / 1391 / 1472 / 5000 |
| group | real / decoy | 15 / 15 | pools 700 real (terms non-empty), 300 decoy (terms []) |
| binding | bound / unresolved | 15 / 15 | pools 2614 / 94 |
| reading | reading / reading_decoy | 30 / 30 (60 rows, each with a base and an alt question) | pools 94 / 94 |

Shuffled across types, then ids A001..A150 assigned in that order. Relation rows excluded for instructions over
MAX_INSTRUCTION_CHARS: 0 (the child would refuse them, so none were sampled).

## Prompt text: how it was produced
- Each sampled item was sent through the real child: `protocol.handle(child, line)` -> `SystemOneChild.decide`, with
  `parallel=1`, against `FakeOllama` from `tests/test_systemone_child.py` on 127.0.0.1. No Ollama port was touched.
- The prompt is rendered from the recorded wire request: `state` + `"\n\n"` + `questions[name].instructions` + `"\nOptions:\n"`
  + one `key: criterion` line per option, in the order the child sent. Relation menus therefore keep the child's per-pair shuffle.
- Relation: the request for `relation` (one question). Binding: the request for `status` (the first question of the item).
  Group: the request for `kind` (the first question), plus one neutral instruction appended (see below). Reading: the requests
  for `base` and `alt`, in that order.
- The server renders the JSON menu itself, and that rendering is not visible from here. The prompt above is the deterministic
  text of the same state, instructions and options. It is not a copy of server-side rendering.

## Self-check (`verify.py`, run output)
1. No gold-revealing fields: every sample row has exactly `id, type, prompt, answer_format`. Ids match `A\d{3}`. The text of
   sample.jsonl has no `qid`, `stratum`, `terms`, `gold`, `readings`, outcome id (`mh:`), or qid pattern (`r00001`, `g01001`,
   `b00001`). Prompts still carry the market's own `[label: ...]` text, which is input, not gold.
2. Prompts equal a rebuild from the child's builders (`_relation_question`, `_choice`, `_frame`, `_prop`, `_listing`, the
   templates), which is a separate path from the wire capture. All 150 items match (30 relation, 30 binding, 30 group, 60 reading).
   Build-time asserts also check that each wire state equals the state the child builds from the context.
3. Scorer round trip: the gold, fed back as an answer, scores 100% in every cell (24 cells).
   CLI check: an all-baseline answer file (NONE, bound, none, none) gives the expected counts; the file was deleted.

Run: `python -B verify.py` (exit 0, output above). The repo was not modified: `git status --ignored` is clean after each run.

## Decisions and deviations
- Reading rows: one sample row per row, with `prompt` = `{"base": <text>, "alt": <text>}`, since a reading row is two questions.
- Group prompt = the child's kind question (state, instructions, options) + one neutral instruction (`NEUTRAL` in
  build_sample.py), which asks for kind, lower bound, upper bound, and chain order if a chain. Chain order is collected, not scored.
- Group canonical class: the judge's answer goes to `group_terms(lower, upper, n, kind == ordered_chain)`, then `terms_class`,
  and is compared with `terms_class(gold terms)`. Kind none is class `none` whatever the bounds. Kind-only compares the kind with
  the canonical kind of the gold terms. That kind is derived from the terms (exactly_one > ordered_chain > at_least_k > at_most_k
  > at_most_one), and build_sample.py asserts it equals the suite label for all 1000 curated groups.
- Key extras: relation has `suite_stratum` (hard_distractor / trivial / real), which is the sampling stratum's source; group has
  `kind` and `n`. `n` is needed to compute the canonical class.
- Relation stratum = gold class (the sampling stratum); the scorer also reports relation accuracy by suite stratum.
- Answer format per type is in sample.jsonl. The judge answers as `{"id": "A001", "answer": {...}}` lines in an answers file.

## Scoring
`python -B score.py --key key.jsonl --answers <judge answers jsonl>`. Reports per type and per stratum: relation (per class),
binding, group (class and kind), reading (base and alt per stratum). A missing answer counts as wrong and is tallied.
score.py imports `mentat_bench.adapters.base` from the worktree path `REPO_PYTHON`, so that worktree must exist.

## Not done
- No judge was run. The sample has no answers yet.
- Unknown: whether the server's own menu rendering changes how the child's questions read. The prompts here are not that rendering.

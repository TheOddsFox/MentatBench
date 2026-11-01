# Local child protocol (JSON lines)

The `local` build adapter (`LocalJSONLAdapter`, `python -m mentat_bench build --adapter local --local-arg ...`) starts
a child process and exchanges one JSON object per line over its stdin and stdout. The child is any program that
speaks this protocol. Two versions exist:

- **Version 1** (default, `--local-protocol 1`): `decide_binding` and `decide_relation`. No handshake is sent, so
  version-1 children are unaffected by round 2.
- **Version 2** (opt-in, `--local-protocol 2`): adds the `hello` handshake, the `decide_group` operation and, when the
  child advertises it, the `batch` envelope. Group decisions are made only when the handshake succeeds; batches are
  made only when the handshake advertises `batch`.

## Common rules (versions 1 and 2)

- The adapter writes one request per line (ASCII JSON, `sort_keys`, no NaN or Infinity) with a sequence number
  `"id"`. The child answers with exactly one line per request. A response may echo `"id"`; when it does, it must match.
- The child runs with `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8`. Stderr is inherited for diagnostics.
- `{"error": str, ...}` is an adapter error. The gateway retries it (`max_retries`). Usage fields in an error
  response are still charged. The child stays alive.
- Protocol failures kill the child's process tree and count as adapter errors: a closed stdout, a line that is not a
  JSON object, a mismatched `id`, a second line after a response, or no response within `--local-timeout` seconds.
  The next request restarts the child. More than three restarts in a row make the adapter fatal.
- Usage fields on any response: `tokens_in` and `tokens_out` (non-negative integers, default 0) and `cost_usd`
  (finite, non-negative, default 0). Invalid fields count as 0 and the response is an error. Optional: `gpu_seconds`
  (finite, non-negative: GPU time of the call, reported by GPU children) and `model` (a non-empty string naming the
  model in usage.json; default: the adapter kind `local`).
- CPU and RSS of a local child's process tree are metered as `child_cpu_seconds` and `child_peak_rss_mb` (see ir.md, usage.json).

### Version 1 operations

```text
request   {"op": "decide_binding", "id": N, "ctx": {"id", "question", "label", "event_title", "context"?}}
response  {"status": "bound"|"unresolved", "class": str|null, "negated": bool, "missing": [str..], usage?}

request   {"op": "decide_relation", "id": N, "a": {ctx}, "b": {ctx}}
response  {"relation": "EQUIV"|"IMPLIES"|"IMPLIED_BY"|"EXCLUDES"|"NONE"|"UNRESOLVED", "conf"?: number, usage?}
```

`context` is an optional field of every outcome context (bindings, relations, group members): the league rule card, a string,
from the optional `context` of the corpus row (docs/mentat_hard_contracts.md). It is delivered to every child; the System One
child (version 6 and later, below) shows it in its states. It is sent only when the row has one; a row without it, or with null, sends
the request without the key.
`event_title` is provenance, not context.

- Limits. A card is at most 2,000 characters (`CONTEXT_MAX_CHARS`). A longer card, or one that is not a string, is a
  ValueError naming the row. The check runs while the build streams the corpus, like the question check; there is no pre-pass.
- Cost. A card is repeated in every binding, relation pair and group request that names its outcome, so it costs its length
  once per such request. The cost grows with the number of requests naming the outcome, not with the corpus size.
- Leakage. Card text is subject to the leakage DENY_WORDS scan (tests/test_leakage.py, check (c)).
- Policy. build_policy.json records `context_rows` (the number of rows with a card) only when that number is not zero.
- Resume. A checkpoint that predates the corpus digest is refused when the corpus has cards; a checkpoint that has a digest
  is refused when the corpus differs, cards included.

`conf` is optional and never required. When present it must be a finite number (NaN, infinities, booleans and strings
make the decision malformed, counted as UNRESOLVED). It is the child's own score for its answer. For a named relation,
higher means more confident that the relation is real. For a NONE answer the value is the child's score for the decision
too, and it can be negative or positive: the reference hf child sends the raw margin `score[best named] - score[NONE]`,
in nats, for both (a negative value means NONE beat every named relation; a positive value below its floor means a named
relation lost the floor). The build admits relations in confidence order (docs/ir.md, the admission order) and applies `--relation-floor` to them. A child without `conf`
keeps the old responses and the old logs: its relations are admitted in the study's tie-break order (same market, then
same event title, then question-token Jaccard; docs/ir.md, the admission order), after the relations with a conf.

### Provenance: optional `evidence` on bindings

A `decide_binding` response may carry `"evidence": [REF..]`: provenance REFs of a known shape (`src:<market>:question`,
`src:<market>:outcome_label:<i>`, `src:<market>:event_title`, `src:event:<id>:event_title`, `ax:<NAME>`). The harness
checks each REF's shape (`ValueError` becomes a malformed decision) and writes the outcome's REFs as given: when
`evidence` is present it replaces the derived market-keyed event title, so a gold binding's own REFs are written
verbatim. Absent `evidence` leaves the submission exactly as before. Only the gold-backed adapters (`oracle`,
`gold_view`) may supply it. Any other adapter's reply may carry `evidence`, but the harness strips its gold-only REFs (exactly
`ax:<name>` and `src:event:<id>:event_title`) before the binding is written and counts them in the build summary as
`gold_refs_rejected`; a binding left without evidence keeps its derived REFs. This also applies when a build is resumed from a
checkpoint written before the guard. The harness checks the SHAPE of every other supplied REF. Only gold-backed adapters may
replay a whole theory (`extra_theory`); a build refuses any other adapter that does, or that uses a gold adapter name.
Relations and groups carry no provenance in this protocol, so a gold-view theory cites question and outcome-label REFs but
not axioms. Non-gold adapters receive only the public outcome context (`id`, `question`, `label`, `event_title`, and `context` when the row has one) for bindings,
relations and group members.

Model-built submissions have no way to know gold's event-id or axiom REFs: there is no public vocabulary for them and no
mechanism that supplies axioms to a builder, so a guessed `ax:` or `src:event:` REF is stripped rather than written.
Scoped evaluation remains available through the generic query-evidence contract in [docs/ir.md](ir.md); a model cannot
introduce private axioms by guessing a reference.

## Version 2

### hello

Sent once, right after the child starts, when the adapter is built with `protocol=2`.

```text
request   {"op": "hello", "id": N, "protocol": 2}
response  {"protocol": 2, "name": str, "version": str, "ops": {"decide_binding": {"max_batch": int}, "decide_relation":
           {"max_batch": int}, "decide_group": {"max_members": int, "max_batch": int}, "batch": {"max_calls": int}},
           "model": {"id": str, "revision": str?, ...}?, ...}
```

The hello has its own timeout, `--hello-timeout` (default 900 s), because a child may load its weights before it
answers. A hello that fails leaves groups off, and the reason is recorded as `groups_disabled_reason` in the summary and
in build_policy.json: `hello_timeout` (no answer in time; the child is killed), `hello_error` (an error answer; the child
stays alive), `hello_failed` (any other failure; the child is killed), `no_decide_group` (no decide_group op), `flag`
(`--local-protocol 1` or `--no-groups`), `full_replay` (the oracle replays a full theory, so no group phase runs), or
`adapter_no_groups` (another adapter that makes no group decisions, such as `empty`). `groups_disabled_reason` is null
when groups ran. With `--local-protocol 2` and groups requested, any hello reason is a refusal (exit 3, nothing
written) unless `--allow-groups-off` is given, which builds without groups and prints a warning. The message names what
the user can do for that reason: a longer `--hello-timeout` only helps `hello_timeout`.

Sentence for docs/ir.md (build_policy.json, not edited here): "`groups_disabled_reason` is null when the group phase
ran, otherwise one of `flag`, `full_replay`, `adapter_no_groups`, `hello_timeout`, `hello_error`, `hello_failed` or
`no_decide_group` (see docs/local_protocol.md)." The hello's `model` block (id, revision, dtype, device, ...) is copied into build_policy.json under
`model`, and the usage model of a local child becomes `local:<model id>` (a response's own `model` string takes
precedence).

Groups are enabled only when the response has `protocol` equal to 2 and `ops` is an object that has `decide_group`.
An error response keeps the child alive at round 1 with groups off. Any other failure of the handshake (a malformed
answer, `ops` that is not an object, a missing `decide_group`, a wrong protocol number) kills the child and falls back
to round 1: the next request starts a fresh child. The build then makes no group decisions and runs the pairwise
phase as before. `build --no-groups` also turns groups off.

Batching is enabled when `ops` also has `batch` (an object, `max_calls` optional). Each op's `max_batch` (optional,
at least 1) is the most calls of that op a batch may carry; an op without `max_batch` is always asked one call at a
time. The build's batch size is min(`max_batch`, `--batch-size`, default 64); `max_calls` caps the batch size of
every op as well. A hello without `batch` keeps groups on and makes single calls only.

`decide_group.max_members` (optional, at least 2) is the largest group the child wants. The build sends candidates of
at most min(`--group-max-members`, max_members) outcomes; the value used is recorded in build_policy.json.

### decide_group

```text
request   {"op": "decide_group", "id": N,
           "ctx": {"group_id": "c0000001", "source": "s1"|"s2"|"s3"|"s4"|"file"|"reading",
                   "members": [{"id", "question", "label", "event_title", "context"?}, ...]}}
response  {"groups": [decision, ...], usage?}
```

A group decision may carry the same optional `conf` as a relation, inside its object (`{"kind", "members", ...,
"conf"?: number}`). check_group validates it; it is logged in groups.jsonl when present and used in admission order.
A `conf` at the top level of a `decide_group` response is not read: the adapter returns only `groups`.

The members are a candidate set of two or more outcomes (at most `--group-max-members`, default 64, at most 256, and
the advertised max_members when there is one). Candidate
sources: `s1` the outcomes of one market, `s2` the outcomes of one event, `s3` template and entity families inside an
event, `s4` (opt-in with `--group-sources s1,s2,s3,s4`) bounded agglomeration of candidate pairs, `file` (the group questions of
a curated `--candidates` file) and `reading` (its reading rows; see Curated candidates below).

A decision:

```text
{"kind": "exactly_one" | "at_most_one" | "at_least_k" | "at_most_k" | "ordered_chain",
 "members": [outcome id, ...],        # distinct, at least two, all from ctx.members
 "k": int,                            # at_least_k and at_most_k: 1 <= k <= len(members); omitted or 1 otherwise
 "direction": "forward" | "reverse",  # ordered_chain only; default forward
 "scope": null | {"axis": str, "values": [str, ...], "of": [str, ...]}}
```

Meaning. `exactly_one`: exactly one member holds. `at_most_one`: at most one holds. `at_least_k`: at least k hold.
`at_most_k`: at most k hold. `ordered_chain`: each member implies the next one (`reverse`: each next member implies
the previous one). A scope limits the decision to the interpretations whose `axis` value is in `values`. `of` lists
the values the axis has in this decision; `values` must be a non-empty subset of `of`.

Validation (`adapters.base.check_group`). A decision that fails is rejected alone and counted in `groups_rejected`.
A scope that is malformed or over the caps is also counted in `group_scope_rejected`. Limits: axis names match
`[a-z0-9_]{1,32}`; at most 4 values in `values` and in `of`; at most 3 axes over the whole theory; the cross product
of the axis values at most `max_interpretations` (8 in the default profile). A decision that would exceed these caps
is rejected, never silently unscoped. Two members of one literal under `exactly_one` or `at_most_one` are rejected.
The chain guard does the same for `ordered_chain`: a chain links outcomes of different classes, so two bound members of
one class (a market's Yes and No, say) are rejected. Both kinds of rejection are counted in `groups_rejected`. The chain guard is admission-only: a chain whose members come to
share a class through a later EQUIV merge is not dropped at finalize.
A decision that repeats an accepted group is skipped without a count. A response whose `groups` is not a list is one
group error (`group_errors`), and does not count against the relation phase.

Interpretations. Without scopes the theory has one interpretation, `base`. With scopes it has the cross product of
the axes, named `axis=value|axis=value` in axis-name order (for example `tie=none|window=excl`).

### batch

Sent only when the hello advertised `batch`. A batch carries calls of the three decision ops (one op per batch in
the build).

```text
request   {"op": "batch", "id": N, "calls": [{"op": "decide_binding", "ctx": {...}},
                                              {"op": "decide_relation", "a": {...}, "b": {...}},
                                              {"op": "decide_group", "ctx": {...}}, ...]}
response  {"results": [response fields or {"error": str}, ...], "tokens_in": int?, "tokens_out": int?,
           "cost_usd": float?, "gpu_seconds": float?}
```

- `results` has one entry per call, in order. Each entry has the fields of that op's single response (a decision, a
  relation, or `groups`), or `{"error": str}` for that call alone: only the failed calls are sent again.
- The usage fields are one total for the whole batch. They are one usage record in usage.json with `items` equal to
  the number of calls.
- A batch-level `{"error": str}` fails every call in it (all are retried).
- A `results` list whose length differs from `calls`, or an entry that is not an object, is a protocol error: the
  child is killed and the whole batch fails, as for any protocol failure.
- A batch whose spend or time overshoots a cap is charged, and its decisions are discarded as a whole.

### Example transcript

```text
-> {"id":1,"op":"hello","protocol":2}
<- {"id":1,"name":"example","ops":{"decide_binding":{},"decide_group":{"max_members":64},"decide_relation":{}},"protocol":2,"version":"0.1"}
-> {"ctx":{"group_id":"c0000001","members":[{"event_title":"E","id":"v:m1:1","label":"Yes","question":"Will X1 win?"},{"event_title":"E","id":"v:m2:1","label":"Yes","question":"Will X2 win?"}],"source":"s2"},"id":2,"op":"decide_group"}
<- {"groups":[{"kind":"at_most_one","members":["v:m1:1","v:m2:1"]}],"id":2,"tokens_in":120,"tokens_out":9,"cost_usd":0.0}
```

### Optional `conf` on relation and group responses

Where `conf` sits is given in the operation sections above (decide_relation and decide_group); this section gives its
meaning. It is optional: a response without it is unchanged, and the harness does not require it. For the reference hf child (`hf_child.py`, version 9) a relation's conf is the margin, in nats, of the best named relation over NONE, and a group
decision's conf is the margin of the chosen kind over `none`. A relation's margin is the difference of the two projected logits of the letters, so the normaliser cancels. Relation calibration (a content-free per-letter prior
subtracted from each letter's logit) is off by default: `relation_calibration` in children/models.json is false for every pinned model, and MB_HF_RELATION_CALIBRATION=1 or 0 overrides it (a value other than 0 or 1 is refused). Relation calibration is also off whenever MB_HF_CALIBRATE=0; describe() then reports the source as disabled by MB_HF_CALIBRATE=0. With calibration on, the margin is the difference of the calibrated logits. With calibration off the margin lies on the bf16 grid (with calibration on it is a float difference, so the grid does not apply), and a decision within
two bf16 steps of the floor is rounding-sensitive (a margin of exactly the floor is named by the >= rule, but another batch shape can round each of the two logits one step, moving the margin by up to two steps). The hf child decides a named relation only when its margin is at least the model's relation floor, and answers NONE otherwise, so a NONE can carry a positive conf (the margin of the named relation it did not take). The floor is `relation_floor` in children/models.json (4.0 for every pinned model), or RELATION_FLOOR (4.0) for a model without one; MB_HF_RELATION_FLOOR may only raise it. Every named relation of the hf child has a margin of at least its floor, so the harness `--relation-floor` can only tighten it further.
A NONE representative of a complement pair triggers the fallback asks of `--complement-dedup` (see Cost controls). The
field only carries a child's score; it is not a decision and it is not validated beyond being a finite number.

### Optional `probs` on binding, relation and group responses

A decision may carry the child's probability of each option as `"probs": {option: p}`. It is optional, like `conf`; a
response without it is unchanged. The option keys are fixed per decision (`PROB_OPTIONS` in `adapters/base.py`):

| Response | Where `probs` sits | Option keys |
|---|---|---|
| binding | top level (the status distribution) | `bound`, `unresolved` |
| relation | in the relation object (`{"relation", "conf"?, "probs"?}`) | `EQUIV`, `IMPLIES`, `IMPLIED_BY`, `EXCLUDES`, `NONE` |
| group | top level of the `decide_group` response (the kind distribution) | `none`, `exactly_one`, `at_most_one`, `at_least_k`, `at_most_k`, `ordered_chain` |

A `probs` value is valid when it is an object with exactly those keys, each a finite number in [0, 1], and the values sum
to 1 within 1e-2. `null` means absent. A malformed `probs` is dropped, not an error: the decision is kept (a binding
without its probs, a relation with conf and no probs, a group with `probs` None), and the local adapter prints one line
on stderr and counts it in `probs_dropped`. The probs are diagnostic only: no decision depends on them, and the checks
of `check_binding`, `check_relation_decision` and `check_group` do not read them. `check_probs` is the strict check
(conformance runs it on every answer that carries probs). Each group decision list carries `probs` as an attribute (None when absent), so the
gateway returns it unchanged.

## Checking a child

Each version-2 child should answer the handshake and at least one `decide_group` request. A quick check: run
`build --adapter local --local-protocol 2 --local-arg <child argv>` on a small corpus, and confirm that the summary has
`group_candidates > 0` and that `theory.json` contains `g...` constraints. A child that ignores the handshake shows
`group_candidates: 0`.

## Budgets, policy and failures

- **Group budget.** The group phase has its own share of the build. It stops when `--max-group-calls` candidate calls
  have been made (default 20000), or when the spend of the phase reaches `--group-budget-frac` (default 0.25) of the
  spend ceiling, or when its elapsed time reaches that fraction of the deadline left at its start. Reaching a share
  sets `group_budget_stopped` in the summary and ends the group phase only: the relation phase still runs, and the
  global budget stops (`budget_stopped`) behave as before. Accepted groups from completed calls are kept.
- **Cost controls.** `--batch-size` (default 64) caps the decisions per batch call; `--top-k` (default 64, see README.md) is the
  blocking partner count per market (`python -m mentat_bench.harness.topk_sweep` reports blocking recall by K);
  `--complement-dedup` (default on) asks one representative pair of two binary markets whose outcomes are bound as
  complements. The other three record pairs are derived only when the representative's answer is EQUIV, IMPLIES,
  IMPLIED_BY or EXCLUDES; the derived relation is the one a truth table gives for the flipped outcomes (exact, or the
  tightest named relation when the image has no name). When the representative is UNRESOLVED, an adapter error or NONE
  (no entailment), the three pairs are asked normally (`dedup_fallback_asked`). `--complement-fallback` (default `all`)
  changes that fallback: `flipped` asks only the both-flipped pair (not-a, not-b) and skips the two mixed pairs; `none`
  asks no fallback and skips all three. A skipped pair is not asked, not written to relations.jsonl, and counted in
  `dedup_fallback_skipped`.
  Blocking recall is measured before dedup. Summary keys: `calls` (usage entries), `items` (decisions), `pairs_derived`
  (derived pairs with a named relation, covered without a call), `pairs_derived_none` (derived pairs whose relation is
  NONE, no constraint), `dedup_representatives`, `dedup_named`, `dedup_undecided` (UNRESOLVED or error), `dedup_none`,
  `dedup_fallback_asked` (fallback pairs asked) and `dedup_fallback_skipped` (fallback pairs the policy left unasked).
  At every stop, after the last committed batch: `relations_committed + pairs_derived + pairs_derived_none +
  dedup_fallback_skipped = pairs_considered`. Batches are the `checkpoint_every` windows of pairs.bin, as before; a
  discarded batch (budget, deadline, adapter failure) moves nothing; a cap hit mid-batch keeps the batch's complete units. A representative's three derived
  pairs are counted with it when it is decided. When the window ends inside its block, the derived pairs past the window
  are counted already: `pairs_considered` is the cursor plus those pairs (`pairs_ahead`, persisted; the next batch passes
  them asking nothing). A legacy checkpoint, written before `pairs_ahead` existed, resumes with `pairs_ahead` 0: its cursor is
  exact and the decisions are unchanged, but if the cursor sits inside a complement block, that block's derived pairs past
  the cursor are already in `pairs_derived` while `pairs_considered` does not count them yet. So the identity can be short
  by up to 3 pairs at an early stop, until the next window commits (then it holds). `pairs_considered` equals `candidate_pairs` only when the relation phase covered every candidate:
  no budget or deadline stop, no `--max-pairs` or `--max-calls` cut, no adapter failure. Under `--max-pairs` the cut
  is at the pair limit, so `pairs_considered` can exceed the cap by the derived pairs of a representative at the cut.
  With `--complement-fallback all`, `dedup_fallback_skipped` is 0.
  `--max-calls` is a hard cap (see Caps and partial builds). A batch is cut at a whole unit (a plain pair, or a
  representative with its derived pairs) whose decisions fit the budget left, a representative reserving its fallback
  decisions too; so some decisions can go unused at the cap. A retry (a failed item retried alone, or a failed batch)
  runs only if its items fit in what is left, so it can take a fallback's reserved decision. Then the batch keeps its
  complete units (a plain pair answered; a representative answered and, unless its answer is named, its fallbacks
  answered), commits them, and the phase stops. The paid answers of the cut unit are not committed; they stay in
  usage.json. The identity above holds at the cap too.
  `--pair-order score` (default `index`) asks the candidate market pairs by their blocking score, highest first; ties keep
  the index order. Sibling pairs (the outcomes of one market, which are not blocked) come first in both orders, in index
  order, so a cap below the sibling count cuts siblings. Under `--max-pairs` the pairs cut are the lowest-scoring market
  pairs. A complement unit's four record pairs stay together, representative first. Score order also changes the order of
  the rows in relations.jsonl (so the line numbers of derived rows), the tie-break of admission (log order), and the merge
  order of the opt-in `s4` group source. Memory: score mode holds every market pair and its score in memory, about 140
  bytes per market pair (toy10k: 223,604 market pairs over 5,104 markets, peak RSS 75.6 MB against 44.7 MB in index
  order, same candidate pairs and time). Index order streams the pairs to disk. Use index order beyond about 100k markets
  (about 0.6 GB of score structures there).
  A derived row in relations.jsonl is `[i, j, relation, line]`: `line` is the index of its representative's row. The
  base-check pruning never drops a derived row alone: when a derived row is the culprit, its representative goes with
  all its derived rows (`derived_dropped` counts those; `relations_dropped` counts the representatives and plain
  relations).
- **Base repair and contradictions.** A model whose relations contradict each other or the bindings makes the base
  inconsistent; finalize makes it consistent by admission by default (docs/ir.md, Python CLI section: the relations are
  admitted in confidence order). The post-hoc repair described here is the `--no-admit` path, and the fallback when admission does not
  apply (a scoped theory that is not settled, or 2 x outcomes above `--admit-max-literals`) or is refused (`admit_mode` in the summary names the
  reason). It repairs the base by dropping constraints. This is a courtesy that keeps a theory usable, not a
  quality judgement: the theory is scored after the repair, and the number of drops is the penalty signal. The repair
  works in passes: each pass finds, by one strongly-connected-component computation, a conflict cycle
  x -> ... -> not x -> ... -> x (a shortest path each way inside its component) for up to 256 unsatisfied variables
  (one cycle per variable), repairs the relation with the
  highest index on each cycle (the later of the contradicting relations, a deterministic tie-break; groups only when the
  cycle has no relation), and repeats until the base is consistent. The passes follow the depth of the conflicts, not the
  number of repairs: a real 1.5B base with 8,161 repairs took 170 s (one pass per repair would take hours). The
  repaired units are always on a conflict cycle of the state they were chosen from, so the result is consistent and
  can differ from a one-repair-per-pass sequence. A culprit whose cycle an earlier repair of the same pass has already broken
  is skipped, so a repair is never counted twice in one interpretation. `MB_PRUNE=bisect` selects the old prefix bisection, which also
  yields a consistent base (kept for equivalence tests). Two caps stop an unusable theory (exit 5, with the count and
  rate in the message): `--max-relation-drops` (default 20000) and `--max-relation-drop-frac` (default 0.25 of the
  relations asked), and the same pair for groups: `--max-group-drops` (default 20000 rows) and `--max-group-drop-frac`
  (default 0.9 of the committed group rows). All four caps are recorded in build_policy.json but are not part of the resume
  policy: they do not change the decisions, so `--resume` may change them. Model calls (bindings, blocking, groups, relations)
  stop at 0.95 x `build_deadline_s` (the reserve of 5% is `finalize_reserve_s` in the summary); a call that crosses that point is
  discarded and charged. A stop in blocking, groups or relations is `budget_stopped`, sealed with PARTIAL.json. A stop during
  bindings ends the build with BuildIncomplete (no checkpoint exists yet, so nothing is sealable), as before but 5% earlier.
  A resume keeps counting the elapsed time of the earlier run. This leaves finalize the rest, but does not bound it: admission
  without a refusal may run past the deadline. The stop bounds the calls only for adapters that honour `timeout_s` (the local
  adapter does). The build deadline stops admission only after a unit was refused (BaseCheckIncomplete, the checkpoint kept). A consistent admitted base is sealed even past the deadline,
  with its mb-engine confirmation skipped (`base_probe` `skipped_deadline`), as on the repair path; no partial reason is
  written for it. `--repair-budget-s` bounds only the post-hoc repair, checked between repair passes. The repair runs when admission is refused
  or does not apply (a scoped theory that is not settled, or too many outcomes for `--admit-max-literals`), and in the engine fallback. An exhausted
  budget fails the build (RepairBudgetStopped, exit 3, nothing written, the checkpoint kept). `--no-admit` runs the same
  post-hoc repair, so the budget and the relation floor apply to it too. A repaired base is reported as
  `base_repaired: true` (a relation or group was dropped, or a group lost an interpretation); the summary also has
  `groups_dropped` and `groups_dropped_pct`. Scoring follows spec s9: an inconsistent base theory fails its universe, so
  report.py shows `Q_N (repaired)` (the score of the repaired theory) next to `strict Q_N`, which is 0.0 for a repaired
  base and equal to Q_N otherwise. Both views are reported; the repair is a courtesy that lets the rest of the theory be
  scored. Summary and cell.json carry `relations_asked`, `relations_dropped` and `contradiction_rate`;
  report.py shows them as `dropped%`. The speed columns come from usage.json and build_summary.json: `bind`/`grp`/`rel items/s`
  are the charged items (retried ones included; torn placeholder rows left out) per second of each phase's call seconds,
  `call p50 s`/`call p95 s` the nearest-rank percentile of the per-call seconds, `items/call` the mean items per call, and
  `rel wall s` the relation phase's seconds; the JSON rows also hold each phase's breakdown (`phases`). A usage entry's `op`
  (the decision method it charged) names its phase exactly. When some call has no op (usage from before the op), the
  phases are assigned by time (see `speed()`). A resume's torn placeholder row takes the op of the row before its cut (of the checkpoint's phase when no row precedes
  it; none for a phase without calls).
  No rate or percentile is shown when the mean call takes under 1 ms. While repairing, finalize prints a progress line to stderr every 30 s.
  A build that makes no progress for 900 s (a heartbeat from its decision loops, checkpoints and repair passes resets
  the clock) writes the stack of its threads to `<work>/stall_trace.txt`, repeated while it stalls. A long repair that
  keeps progressing writes nothing; it prints its progress line to stderr every 30 s.
- **Resume.** `build --resume` (or `runcell --resume` with the same run directory) continues a killed or interrupted
  build from its kept work dir `<out>.build`. A build that is interrupted (Ctrl-C, a crash) keeps its checkpoint, and
  the command says so. The checkpoint records the phase (bindings, blocking, groups, groups_done, relations) and the
  cursors (the candidate pairs decided: `pairs_considered` less `pairs_ahead`; the group candidates committed). Resume truncates each log to its checkpointed
  length, restores the compact state from the logs (market units, class ids, the class parity from the accepted
  relation rows, the committed group keys), skips the phases that are complete, and continues from the cursors. The
  decisions are the same as an uninterrupted run's when no overall limit binds on the resume: the theory, bindings and
  provenance are byte-identical (for a deterministic adapter). The paid calls of a discarded batch (a crash's, or a budget stop's) stay in the work dir's usage log (usage_calls.jsonl) and, in the finished build, in usage.json; the usage log keeps them across the resume, and
  are charged against the spend ceiling and `--max-calls`, and their decisions are asked again (see below). Because
  those paid calls count toward the overall spend ceiling, a resume that reaches that ceiling or the call cap earlier than
  an uninterrupted build with the same limits stops earlier, and its theory can then differ. The group share does not
  count them: it measures the spend of the group decisions committed (`group_committed_spend`; the discarded spend is in
  `discarded_spend`), so a resume that stopped on the group share reaches the same group decisions and theory as an
  uninterrupted build. When a time window binds instead (the group phase's share of the deadline, or the build deadline),
  the resume is not equal to an uninterrupted build: the resumed clock counts the seconds of the calls only (the
  checkpoint's wall time and the rows past it), not the time between the last checkpoint and the kill. Spend and elapsed time carry over (the seconds of the calls past the checkpoint are added to the elapsed time, since the checkpoint's wall time predates them; the spend ceiling, the group budget and the deadline count the
  earlier run; the group budget's start, `group_spend0` and `group_t0`, is kept in the checkpoint). The summary says `resumed: true` and
  `prior_calls`. Not carried over: the adapter's consecutive-failure counter (it restarts at 0). A resume must use the
  policy flags the checkpoint was built with (top_k, the group settings, dedup, `complement_fallback`, `pair_order`, batch size, max pairs and calls, the
  adapter, the admission knobs `admit`, `admit_max_literals`, `relation_floor` and `repair_budget_s`, and the model block
  when the child reports one); otherwise it is refused with a message naming the knob. A checkpoint without `admit` in its
  policy is a pre-admission build and resumes as `admit` false: resume it with `--no-admit` (the refusal names it).
  A resume also checks the corpus: the checkpoint holds a sha256 of the outcome records in order (`corpus` in its
  policy; the records are read once more for it, with no model call), and a resume whose records differ is refused
  before anything is decided. A checkpoint without it is accepted and records the digest.
  A kill mid-write can leave an incomplete final row in `usage_calls.jsonl`. The resume cuts an unparseable final row and
  writes a placeholder in its place: `batch_size` items toward `--max-calls` (a batch's row can carry that many decisions;
  a single-call build counts one), cost unknown and counted as 0, marked `"torn": true` (the call was sent). The spend
  ceiling can therefore be exceeded by the torn call's real cost, which is not recorded: `evaluate` cannot see it, since
  the placeholder costs 0. The placeholder is an upper bound on items: a batch smaller than `batch_size` is over-charged,
  which can end the remaining phases as `calls_capped`. The resume records `torn_usage_rows` (the count of placeholders) in usage.json and the summary. A
  final row that parses but lacks its newline gets the newline and keeps its cost. An unparseable row that is not the last
  one is corruption: the resume refuses it, and no log is changed. A torn tail lies past the checkpoint: an unparseable row
  inside the checkpointed rows is damage, not a torn tail, and refuses the resume the same way (ValueError, nothing changed).
  A checkpoint whose required keys are missing, or of another kind, or whose phase is not a build phase, is corrupt: the
  resume and the finalize refuse it with exit 3, and the work dir is kept.
  If the usage log cannot be written (for example, the disk is full), the build fails with the OSError. The failing
  entry's cost is in the in-memory spend only, so it is recorded in neither file. A resume from the last checkpoint asks
  the decisions of the uncommitted batch again and pays them again: rerun the same command with `--resume`.
  A build that stopped before its first checkpoint has nothing to resume. A build that stopped on the spend ceiling or
  the deadline (`budget_stopped`; the summary's `stopped_in` names the phase: blocking, groups or relations) keeps its
  work dir only with `--keep-work`, and its partial submission is written to `--out`. To extend it, remove that partial
  `--out` (builds are write-once) and `--resume` with a profile whose `spend_ceiling_usd` or `build_deadline_s` is larger.
  The stop is cleared only when the new limits leave headroom: the prior spend is below the new ceiling, and the elapsed
  time is below 0.95 of the new deadline. Then the stopped phase runs again from its cursor: a stop in blocking runs
  blocking again (pairs.bin was cut short), groups continue from the committed candidates, relations from the committed
  pair cursor. The extension is recorded in `budget_extended`, a list with one entry `{old, new, stopped_in}` per cleared
  stop (`old` and `new` hold `spend_ceiling_usd` and `build_deadline_s`; a second extension appends), in the summary and
  build_policy.json, and `finalize_reserve_s` follows the new deadline. Without headroom, or for a checkpoint
  without `stopped_in`, the stop stays final: nothing new is decided, the same partial theory is sealed, and no
  `budget_extended` entry is written, even when a limit changed. A decreased `spend_ceiling_usd` or `build_deadline_s` is
  refused as a usage error before any log is touched: the limits may only grow, so a cleared stop never shrinks one.
  Every other profile key must match the checkpoint's profile, or the resume is refused
  naming the key; a checkpoint written before the profile was recorded is accepted. The call that overshot the limit
  and the batch the stop discarded are paid and stay in usage.json, and the resumed build asks those decisions again.
  When the stop was on the group share (`group_budget_stopped`, or a resume that reaches no overall limit), its theory,
  bindings and provenance equal an uninterrupted build's; when an overall limit binds (the spend ceiling, or the call cap
  through the discarded calls), the resume can stop earlier and its theory can differ. Its usage holds the repeated calls
  too. A
  group-phase share stop (`group_budget_stopped`) is final, as before. A crash or Ctrl-C leaves `budget_stopped` false:
  a crash in the group phase resumes groups from its committed candidates, and a crash in the relation phase resumes
  from its pair cursor. Either way the paid calls after the last checkpoint are charged on resume (see Caps). A checkpoint written before `complement_fallback` and `pair_order`
  resumes as `all` and `index`; the build's `build_policy.json` records the values it ran with (the defaults filled in).
- **Extending a budget-stopped build (operator steps).** A stop on the spend ceiling or the deadline leaves a partial
  submission in `--out`, and its work dir `<out>.build` is removed unless the first run keeps it. So:
  1. First run, with the cli: `python -m mentat_bench build --adapter ... --corpus CORPUS.jsonl --out OUT
     --profile P.json --keep-work`. The stop exits 0 with `budget_stopped` and `stopped_in` in the summary; OUT holds the
     partial submission (PARTIAL.json) and OUT.build holds the checkpoint and checkpoints.jsonl.
  2. Move the partial output out of the way: rename OUT to any other name (for example OUT.partial). The cli refuses
     `--resume` while `--out` exists and is not empty, and builds are write-once. Do not delete it unless you no longer need it.
  3. Write P2.json: a copy of P.json with a larger `spend_ceiling_usd` and/or `build_deadline_s`. Change nothing else
     (every other profile key must match the checkpoint's).
  4. Resume with the same adapter and flags as step 1, the new profile and `--resume`: `python -m mentat_bench build ...
     --out OUT --profile P2.json --resume --keep-work`. The stopped phase continues from its cursor. The stop is cleared
     only when the new limits leave headroom (prior spend below the new ceiling, and elapsed time below 0.95 of the new
     deadline); otherwise the build stays partial with the same `budget_stopped`, and you repeat from step 2 with larger limits.
  5. The extended build is labelled: its summary (and the runcell `build_summary.json`) and OUT/build_policy.json carry
     `budget_extended`, a list with the old and new limits of each cleared stop. Drop `--keep-work` on the final run to remove OUT.build on success.
  `runcell` cannot do this: it passes no `--keep-work`, so its budget-stopped work dir (`<run>/submission.build`) is
  removed and there is nothing to resume. Its `--resume` continues only a kept checkpoint, and it also refuses a run
  directory whose `submission/` exists, so the partial `submission/` must be renamed first as in step 2. A group-phase
  share stop (`group_budget_stopped`) is not extended this way.
- **Caps and partial builds.** `--max-pairs N` caps the candidate pairs the relation phase covers (`pairs_capped`; the
  summary also carries `pairs_asked_cap` = N and `pairs_considered`). `--max-calls N` is a hard cap on the decision items
  of the build, bindings included: every charged attempt counts (the gateway's `items_left`, set from N less the items
  already charged), and a retry runs only if it fits, so retries never exceed N. A fresh build makes one binding call per
  outcome, so a N below the outcome count is refused before any call (a usage error, exit 2, nothing written). The group
  and relation phases stop at N (`calls_capped`), and a relation batch the cap cuts keeps its complete units (Cost
  controls). A resume keeps N and charges every paid call in usage.json, including the calls made after its last
  checkpoint (a crash's discarded batch), so the cap and the spend ceiling count them; a torn final row counts as
  `batch_size` items (see Resume). A binding retry leaves the items of
  the bindings not yet asked, so every binding gets its first attempt. The CLI refuses a fresh build with N below its
  outcome count before the adapter starts. A resume refuses a checkpoint whose N is below its binding count (one built
  before the cap was enforced). A `BindingCountMismatch` (the bindings read a different number of outcomes from the one
  `--max-calls` was checked against) is a failed build, exit 3: nothing is sealed, the usage log and work dir are kept, and
  the path and the paid call count are printed. A build that fails in the bindings phase (a budget stop, Ctrl-C, a usage
  write that fails) keeps its work dir when its usage log holds a paid call, prints the path and the count, and has no
  checkpoint to resume. To recover, move `<out>.build` aside (rename it), then rebuild into a new output directory: a fresh
  build refuses a leftover work dir, and `--resume` has nothing to continue. A complete build with any of
  `budget_stopped`, `pairs_capped`, `calls_capped`, `group_budget_stopped` or `deferred_unresolved` is still sealed, and writes PARTIAL.json
  (`{"partial": true, "reasons": [...]}`) beside the submission, hashed into the seal. `evaluate` warns on stderr and
  adds `"partial": [reasons]` to its stdout JSON. `report.py` shows the status: complete, the first partial reason, or
  incomplete (adapter fatal, or a cell whose build failed). `runcell` writes these into cell.json (`status`, `partial`,
  `incomplete`, `build_exit` for a failed build, and `evaluate`, the JSON evaluate printed).
- **Batch health.** A whole batch that fails after the retries is one failed call (one failure in a row; its items are
  failed decisions). An item error inside a successful envelope is one failed decision each.
- **Policy.** build_policy.json (sealed with the submission) records the adapter name, whether groups ran,
  `group_sources`, the effective `group_max_members`, `group_budget_frac`, `max_group_calls`, `max_df`,
  `max_relation_drops`, `max_relation_drop_frac`, `max_group_drops`, `max_group_drop_frac`, `top_k`, `complement_dedup`,
  `batch_size` (the flag when the adapter batches, else 1), `max_pairs`, `max_calls`, `complement_fallback` and `pair_order`, and the admission knobs `admit`,
  `admit_max_literals`, `relation_floor` and `repair_budget_s`. A build with a cleared budget stop adds `budget_extended`
  (a list of `{old, new, stopped_in}` entries, one per cleared stop; `old` and `new` hold `spend_ceiling_usd` and
  `build_deadline_s`); the summary also has `budget_extended` and `stopped_in` when they apply. The profile itself is in
  the submission's profile.json, not here. On the pairwise path the admission knobs and the four drop caps
  (`max_relation_drops`, `max_relation_drop_frac`, `max_group_drops`, `max_group_drop_frac`) are the values applied by the
  finalize that wrote the file, so a resume that changed a cap records the new one (a scoped or over-cap build records
  `admit` true too; the summary's `admit_mode` says whether admission ran); on the full-replay path and with `probe` off
  they are null. The checkpoint's own policy,
  which resume and ceilings read, keeps the build's requested values. The adapter name for the local adapter is `local`. A build that ends with `adapter_fatal` also writes `INCOMPLETE.json` (reason + summary keys) next to it; `seal` refuses such a directory unless `--allow-incomplete` is given.
- **Partial marker.** PARTIAL.json is written only for a complete build (no `adapter_fatal`). Its reasons are the summary
  keys `budget_stopped`, `pairs_capped`, `calls_capped`, `group_budget_stopped`, `deferred_unresolved` that are set.
  `deferred_unresolved` is the count of deferred group parts that the engine localization left unresolved when the round cap
  or the build deadline stopped it and the engine then accepted the base without them (docs/ir.md, Base consistency). Those
  parts are dropped, or downgraded for an exactly_one, and their drops count against `max_group_drops` like localized drops.
- **Failures.** A child failure that exhausts its restarts is an adapter fatal error in any phase. In the group phase
  it sets `groups_fatal` and `adapter_fatal`: the phase, the relation phase and the seal stop, the latest checkpoint is
  kept, and the build exits 3 with nothing sealed.
- **Gold.** `build --gold DIR` is read only by the oracle and gold_view adapters, and only for the recall metrics
  (`blocking_recall`, `group_recall`). `evaluate` warns on stderr when a submission was built by either of them.
- **Curated candidates (`--candidates FILE`).** FILE is a public `candidates.jsonl` (the Curated suite of
  docs/mentat_hard_contracts.md). It is checked in full before the child starts (schema, unique qids, one binding question
  per outcome, a relation's a != b, a group's 2..64 distinct members, a reading group exactly 5, every id a corpus outcome) and refused when it lies
  inside a `gold` directory. Bindings are unchanged (every outcome is decided). Blocking does not run: pairs.bin holds the
  file's relation questions in file order, and each is asked exactly as (a, b) as written. The group candidates are the
  file's group questions only, in file order (group source `file`; a reading row, source `reading`, carries its own
  source and its answer is normalized by `base.reading_picks`: one canonical scoped pair per reading with an option pick,
  see docs/ir.md, Scoped settle), and every one is asked: the group share of the
  budget is 1.0 and `max_group_calls` is at least the number of curated groups (the effective values are in
  build_policy.json; the deadline still applies). Complement dedup is off; `--complement-dedup` with `--candidates` is
  refused, and so is `--candidates` with the oracle (a full replay asks no question). build_policy.json records `candidates_sha256` and `candidates_gold_informed: true` (null and
  false without the flag); `candidates_sha256` is resume policy (a checkpoint from before it resumes as null).
  **Disclosure:** the candidate selection is gold-informed, so a build with `--candidates` does not test blocking (which
  pairs a system chooses to ask); its results say so. `runcell --candidates FILE` passes the flag and, after the score
  step, runs `python -m mentat_bench.decision_scoring` on the decision log against `gold/suite_labels.jsonl` (with
  `--candidates`, and `--fixture` the fixture directory, required: its fixture.json version picks the headline, the relation
  MCC for versions 1 and 2 and the relation and group mean for round 3 and later) into `score_decisions.json` (cell.json `decision_scoring`: `ok`, `failed`, or `not_importable` when the module is absent;
  the cell is incomplete unless it is `ok`). The Mentat-Hard profile is `profiles/mentat-hard-v1.json` (toy-local-v0
  with `build_deadline_s` 5400, 1.5 h).
- **Raw decision log.** Every build (with or without `--candidates`) writes `<out>.build/decisions.jsonl`: one line per
  answered question, in the order asked: `{"qid", "op", "a", "b", "id", "members", "decision", "conf", "probs", "error"}`.
  `a`/`b` are the exact ids sent (relations), `id` the outcome (bindings), `members` the ids sent (groups); `decision` is
  the raw answer (a relation object, `{"relation": name}` for a bare name, NONE and UNRESOLVED included; the binding
  object; `{"groups": [...]}` for a group list); `conf` and `probs` are the answer's top-level ones (a group list's
  `.probs`), else null; `error` is the gateway's error when the item failed after its retries (`decision` is then null).
  `qid` is the file's qid with `--candidates`, else null. Derived complement pairs are not logged (only what was asked;
  complement fallbacks are, with qid null). The log is committed with each batch and checkpointed (`decisions_bytes`); a
  resume cuts it back to the checkpoint like the other logs. A checkpoint from before the log, resumed, logs only what is
  asked from then on: the summary and build_policy.json say `decisions_complete: false`. A non-gold-backed adapter's
  gold-only evidence REFs are stripped from the logged binding, as from decided.jsonl. It is not part of the submission
  (runcell keeps the work dir).

## Reference baseline children
- `lexical_child.py` (hello name `lexical-retrieval`): bindings by normalised text; EQUIV only for the same claim with no polarity-bearing token difference, equal numbers and token-set Jaccard >= 0.9; never IMPLIES/EXCLUDES/UNRESOLVED; `decide_group` returns `[]` (run with `--no-groups`).
- `embed_child.py` (`embed-cosine`): same gate plus a cosine threshold (99.5th percentile of cosines between random unrelated corpus pairs, fixed seed, gold-free), mean-pooled hidden states of the already cached Qwen2.5-0.5B (no download; `MB_HF_DEVICE` default `cpu`); needs `--corpus` for its startup calibration (not metered).
- Both are deliberately weak baselines: they never emit IMPLIES, EXCLUDES or groups. Complement dedup in the harness can derive exclusions from their EQUIV answers.

## System One child

`systemone_child.py` answers the typed decisions of this document with a server's System One endpoint,
`POST {MB_S1_URL}/v1/systemone`: Ollama's, or any server with the same wire format (`docs/outside_models.md`). It is torch-free:
the server makes the probabilities and the child reads them back.
The decisions are the same typed questions as `hf_child` (bindings, relations, groups), not identical prompts: the child's
prompts are zero-shot (no few-shot turns), uncalibrated (no content-free prior) and use the server's own probabilities.
Relation menus are shuffled per pair with the `crc32-shuffle-v1` order of `hf_child`'s `relation_order`, applied to the five
options of round 3 (EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, NONE; `hf_child` has four, without EQUIV), so no option sits at
the same place for every pair. The crc32 seed is `"{a_id}\x1f{b_id}"`. An exact tie between relations goes to the one the
pair's menu lists first. EQUIV is described as "A and B mean the same: each holds exactly when the other does".
`conf` is not comparable with round 2: EQUIV joins the named relations, so the best named margin changes. Relation
floors are recalibrated for each child version, not carried over. Every criteria value is a non-empty string (the order stage's options are "outcome X").

- Rule cards (version 6; version 7 rewords `alt_rules`). An outcome may carry its league's rule card (`context`; absent when the row has none). The state of
  a relation (the cards of A and B, each shown once when they differ), of a group (the cards of its members, on every question
  of it: kind, k, the order rounds and the bounds) and of a reading (the card of U) begins with the cards under `League rules:`,
  before the body, so the card is a prefix a server can reuse across the questions of one league. A binding has no card: the
  card names the ambiguous phrases. A reading with a card asks `base_rules` ("Under reading (1) of the league rules, which option
  means the same as U? Answer none if no option does.") and `alt_rules` ("Under reading (2) of the league rules, which option
  means the same as U? If U's wording fixes one meaning (it uses no term with two readings, or it says which reading it means),
  give the option for that meaning. Answer none if no option does."; version 6 said only "If U uses no term with two readings,
  both readings are its plain meaning", which left a glossed U such as "by the break, counted on final points" open to
  reading (2), as the revision 3.1 answerability audit found),
  under the names `base` and `alt`. A request for outcomes with no card keeps its version-5 bytes. A card is stripped of
  surrounding whitespace, and a blank card is no card. The layout (`League rules:`, the blank line after the cards, and the
  blank line between two cards) is `PROMPTS["text"]` in the child, so `prompt_sha256` covers it. `conform` probes `base_rules`
  and `alt_rules` on a card-bearing reading state, so the card wording is checked on the live server.
- Over-context with a card. A card adds to the state, so a card near the context limit can make a question too long. The child
  relies on the server refusing an over-long state: Ollama does (HTTP 400 "exceeds the available context size", verified on
  Ollama 0.40.11; laya says "longer than the context length"),
  and that refusal is an abstention. A server that truncates the state instead of refusing would drop the card silently, with no
  abstention. The conform `over_context` probe sends a 1 MB state only, so it does not test a card near the limit. The usage
  record has no abstention field, so a cell run that reports abstentions sets `MB_S1_STATS` (the harness's runcell does not set
  it): card-driven abstentions then appear in that file's `abstained_overlength` counts.

- Configuration: `MB_S1_MODEL` (the model tag, required; a bare name means `<name>:latest`, as Ollama resolves it, and the
  pin, the `/api/tags` check and the requests all use the resolved name), `MB_S1_URL` (default `http://127.0.0.1:11434`),
  `MB_S1_PARALLEL` (requests in flight per call, default 4), `MB_S1_TIMEOUT` (seconds per request, default 300),
  `MB_S1_ALLOW_UNPINNED=1` (see Pins) and `MB_S1_PINS` (the pin file, for tests). Every request asks the server to keep the
  model loaded (`keep_alive` -1).
- Server URL. `MB_S1_URL` must be http or https to `localhost`, `127.0.0.1` or `::1` (case-insensitive), with no userinfo,
  no backslash and no trailing dot in the host, and a valid port. There is no override. The child follows no redirects
  (a 3xx is an error reply) and uses no proxy.
- One question per request, for every op (`MAX_QUESTIONS` is 1; the endpoint allows 64). Packing changed relation decisions on
  nimble:9b-bf16: 31 of 240 argmaxes changed (conf up to 6.2) between a question sent alone and the same question packed with
  15 others, while d3:9b-q8_0 changed 0. Packing gave no speedup either (17.7 s packed against 17.0 s single for 240 questions,
  `MB_S1_PARALLEL=2`). Concurrency across requests stays (`MB_S1_PARALLEL`). Bindings: a request for `status`, one for `negated`
  (a label that is not binary, asked when the status is bound: a binary label's negation is read from the label), and one for
  `missing` when the status is unresolved. Relations: one request per pair. Groups: one request for `kind`, one for `k` when
  the kind is at_least_k or at_most_k, and one request per round of an ordered_chain (the strongest of the members not yet
  placed; the last member needs no question). Then, for a set of at most 8 members, one request for each bound the kind
  leaves open: `lower` (the fewest that must hold) after at_most_one, at_most_k and ordered_chain, `upper` (the most that can
  hold, or `no limit`) after at_least_k and ordered_chain. The bounds are the canonical terms of `adapters.base.group_terms`, with the kind's own term emitted first. An ordered_chain on a set where `chain_members` drops a member (both sides of a binary market) asks no chain or bound questions and gets no decision. A reading row (`source` "reading": the proposition, then its
  options: four in Mentat-Hard, at most 26) gets two requests, `base` and `alt`, each a pick among the options or `none`. Results come back in item order,
  and the answers do not depend on the parallelism. `describe()` reports version 7: the `alt_rules` wording above (version 6: rule cards in the states; version 5: group bounds and reading rows; version 4: the binding `status` and `missing` questions ask about the meaning of the proposition, not whether the event happens; round 3 asked whether the question was fully specified). Version 3 added EQUIV to the relation menu, and the answers carry `probs` (round 2
  changed the decisions of round 1: one question per request).
- Refusals and abstentions. A refusal (HTTP 400 or 413) is for the one question of its request. It is an abstention when the
  cause is the input length: a 413, or a 400 whose text says "longer than the context length" or "exceeds the available context
  size" (in any letter case; the child's one matcher, `is_over_context_text`, is used by the abstention and by `conform`).
  A relation abstains as NONE with no conf;
  a group abstains as `{"groups": []}`; an abstention is not a failure, so it does not count toward the harness's
  consecutive-failure abort. A binding whose status is refused is an item error. Any other 400 (its text is not about the
  context) is an item error, for the items of its request. Abstentions are counted per op (`decide_relation`,
  `decide_group`) and printed on stderr at exit, with the count of refused requests (`rejected_400`). Each refusal is also
  printed on stderr. The usage record has no field for them: the harness keeps only its whitelisted usage fields.
- Failures. HTTP 500 is retried once, after a 0.5 s pause. A second 500 is an item error for its
  question. So a server-wide outage gives consecutive item
  errors, which the harness's consecutive-failure guard counts. A connection refused, a timeout, a 3xx, a 404 for the model
  and any other HTTP status fail the whole call (the queued requests of the call are cancelled), and the gateway retries
  the call. A reply that is not JSON, or has no answer for an item, fails that item.
- Stats (`MB_S1_STATS=<path>`, optional). After every decide() call that returns, the child rewrites that JSON file (a
  temporary file, then a rename that rides out a sharing violation, `seal.replace_with_retry`) as
  `{"model": ..., "ops": {...}}`, with cumulative counts for `decide_binding`, `decide_relation` and `decide_group`:
  `units` (items answered by the calls that returned), `requests` (HTTP requests of those calls, retries included),
  `abstained_overlength` (abstentions returned), `item_errors` (item errors returned, including the refused instructions),
  `retries_500` (500 replies that were retried) and `refused_instructions` (relations not sent because their instructions
  were over `MAX_INSTRUCTION_CHARS`). The counts are per decide attempt, and the counts come from the results decide()
  returns: a call that raises changes no count, and a unit the gateway resends counts once per attempt. A failed write is
  reported on stderr and never fails the call. At startup the count continues from an existing file, so a restarted child
  does not reset it; a malformed file, or a parent directory that does not exist, refuses startup. Known limit (LOW 4): the
  tokens of requests completed inside a call that then fails are not charged, because the call's usage is not reported.
  The harness's runcell does not set `MB_S1_STATS`; the caller sets it to `<cell dir>/child_stats.json`. The stderr line at
  exit is unchanged.
- Wire format. The child sends `POST /v1/systemone` with `{"model", "state", "questions", "keep_alive": -1}`, where
  `questions` maps one name to `{"type": "choice", "instructions", "criteria"}` (one question per request). The reply is
  `{"answers": {name: {"type": "choice", "probabilities": {option: p}}}, "usage": {"input_tokens", "output_tokens"}}`, plus
  a top-level `"digest"` for a server whose digest is read from its replies (below), and a `"model"` that must name the
  requested model when present. Each answer's `probabilities` give every criteria option a number in [0, 1], summing to 1
  within 0.01. The over-context refusal is HTTP 413, or HTTP 400 whose `error` text contains `longer than the context
  length` or `exceeds the available context size`, in any letter case (for a body that is not JSON, its first 200 characters).
  The servers differ in wording (laya and Ollama 0.40.11, the two seen), so the child matches both phrases and no other.
  The other refusals and abstentions are
  the ones below.
- Pins. `children/ollama_models.json` maps each tag to `{"digest", "ollama_version", "size", "license"}`, and optionally
  `"server"` (absent means `ollama`, which is every existing entry) and `"conform"` (a non-Ollama entry only: the passing
  conform report of its pin, `{"ok": true, "date", "checks"}`). The digest is the full sha256 hex, bare or with a `sha256:`
  prefix (the prefix is removed before any comparison). It is read from the first source that gives one: the model's entry in
  `/api/tags` (Ollama); else its entry in `/v1/models`; else the top-level `digest` of the reply to one probe question. A source
  that does not list the model, or lists it without a digest, falls through to the next. In `/api/tags` and `/v1/models`, 404,
  405 and 501 mean absent, and a 200 that is not a JSON object is absent too (with a note on stderr); any other error is fatal.
  A server where no source gives a digest is refused, even with `MB_S1_ALLOW_UNPINNED=1`. At startup the child refuses a tag that is not on the server, is not pinned, whose digest or
  `server` differs from its pin, or that is served by a server other than Ollama without a passing `conform` record in its pin,
  unless `MB_S1_ALLOW_UNPINNED=1`. At exit the child reads the digest again. A change, or a server that cannot be asked, prints
  one `systemone_child:` line on stderr and the child exits 1 after its output. The build that ran it then fails:
  `LocalJSONLAdapter.close()` records a nonzero exit (or a child that had to be killed) after its input closed, and
  `cli build` fails on it. `close()` waits up to 30 s for the child to exit (`CLOSE_WAIT_S`), so a reply-kind server's exit
  probe is not killed by mistake. A refused pin or startup prints one `systemone_child:` line, not a traceback. To pin a model the server already has:
  `python -m mentat_bench.children.systemone_child pin <tag> [--license X]`. It writes that entry only, from the server's
  digest and `/api/version` (null when that is absent or not an object), and replaces the pin file atomically (a temporary
  file, then `os.replace`). A server other than Ollama is pinned only when `conform` passes; its checks are kept in the entry.
  Ollama entries are written as before.
- Conformance. `python -m mentat_bench.children.systemone_child conform <tag> [--url URL]` checks a live server without any
  benchmark fixture and prints a JSON report. It exits 1 when a check fails. A bad URL or an unreachable server is a failed
  `server` check in the report, not a traceback. The checks: `pin` (the digest and server kind are the pin's; `pin` itself
  does not run it, since the entry does not exist yet); `probabilities` (one fixed question per name the child sends, built by
  the child's own builders at the largest menus the decision code builds: `k` with 8 options, `lower` with 9, `upper` with 8,
  `order` with 26 letters, readings with four options and none (Mentat-Hard's reading rows have four; the child accepts up to
  26, which conform does not probe); each answer a choice whose probabilities are in [0, 1] and sum to 1 within 0.01. A menu
  refused as over-context is an abstention, as the child would make it: the `probabilities` detail records it as
  `abstains (over context)`, and it passes `probabilities` and `determinism`; any other refusal or error fails it.
  `determinism` (the same question twice gives the same probabilities, within an absolute 1e-6, and the same argmax; its detail
  counts the questions it compared); `over_context` (a 1 MB state is refused as in the wire format above); `usage` (the reply's `usage` has
  non-negative integer `input_tokens` and `output_tokens`); `digest_at_exit` (the digest is unchanged at exit). The menus are
  rebuilt here with the decision code's formulas, since that code builds them inline: a change to its menus must be mirrored
  here. `--url` defaults to `MB_S1_URL`, else `http://127.0.0.1:11434`; the loopback rule of `MB_S1_URL` (under "Server URL"
  above) applies.
- The hello's `model` block carries the digest as `revision` (so build_policy.json and report.py read it as the model
  revision), with `ollama_version` (null for a server without `/api/version`), `child_version` and `prompt_sha256`; the build's checkpoint keeps the block, so a build started on
one child version does not resume on another. `prompt_sha256` is the SHA-256 of the canonical JSON of every prompt, option text, choice
  list, menu-order version and format string.
- Limits: one question per request (`MAX_QUESTIONS` is 1; the endpoint allows 64), 26 members per ordered_chain (one letter each), 8 k values, and
  `decide_group.max_members` 64. Usage: `tokens_in` is the sum of the server's input token counts over the requests of the
  call, `tokens_out` the sum of its output token counts (0 in the probes), and `cost_usd` is 0.

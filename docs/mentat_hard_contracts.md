# Mentat-Hard: data contracts between workstreams

These formats are fixed. Each stream writes or reads exactly these.

## Fixture directory (written by WS1 `fixtures_hard.py`)

| Path | Public? | Contents |
|---|---|---|
| `corpus.jsonl` | public | One outcome per line: `{"id", "text", "label", "event_title", "controlled": true}`, the same shape as toy10k. Optional `"context"`: a string of at most 2,000 characters, the league rule card, delivered to the child (System One child v6 and later shows it with its relation, group and reading questions, not bindings). A row without it (or with null) is read as before, and the request carries no `context` key. Card text is subject to the leakage DENY_WORDS scan. |
| `fixture.json` | public | Name, split, seed, counts, generator version. |
| `queries.jsonl` | public | The exam, about 200 queries, in the existing query schema (closure/feasibility/partition/selection). |
| `gold/` | gold | `bindings.jsonl`, `theory.json`, `provenance.jsonl`, `usage.json` (from `ir.write_submission`); `answers.jsonl`, `verdicts.jsonl`, `query_design.jsonl`, `summary.json` (engine-finalized exam). |
| `gold/meta.jsonl` | gold | One line per outcome (see below). |
| `gold/world.json` | gold | Per league: teams, L, scores, the rules drawn (type and parameters), subgroups (members, named or implicit, title), and the paraphrase pairs. |

**`context` (Mentat-Hard v3, optional).** The rule card of the outcome's league: a short plain-English statement of the league's
rules (`fixtures_hard.render_card`). Every outcome of a league carries the same string. The card is delivered to the
model through `context` on the corpus row: System One child v6 and later shows it with the relation, group and reading questions
(not bindings). A corpus row without the field is a row of an older fixture. The card is rendered from the rule list
only (never from the scores, winner, runner-up, qualifiers or seeds); its remaining gold-conditioned parameters are
disclosed in docs/mentat_hard.md (section 2, rule card). The decision labels and the exam never read it.

Each `gold/meta.jsonl` line is:

```
{"id", "market", "league", "kind": "thr"|"win"|"runner"|"qual"|"seed"|"dated",
 "team", "t": int|null, "subgroup": str|null, "class": str, "family": int (1..30),
 "test_only": bool, "paraphrase_of": market id|null, "gloss": bool}
```

`gloss` (v3) is true for a glossed market: a bound paraphrase of a threshold or qualify class whose question states the
ambiguous phrase's reading ("...by the break, counted on final points?", "...top half, meaning a playoff place?"). The
suite draws the decoy propositions' u from these markets only.

## Curated suite (written by WS3 `hard_suite.py` into the fixture directory)
`candidates.jsonl` is public. Each line is one question, in a fixed order:

```
{"qid": "b00001", "op": "decide_binding", "id": OUTCOME}
{"qid": "r00001", "op": "decide_relation", "a": OUTCOME, "b": OUTCOME}   # oriented: ask exactly (a, b)
{"qid": "g00001", "op": "decide_group", "members": [OUTCOME, ...], "source": "curated"}
```

`gold/suite_labels.jsonl` holds one label per qid:

```
{"qid", "op", "label": ...,
 "k": int|null, "missing": [codes]|null,
 "order": [OUTCOME, ...]|null,
 "held_out": bool,
 "stratum": "real"|"hard_distractor"|"trivial"|"decoy",
 "depth": int|"count"|null}          # decide_relation rows only (v3.1)
```

**depth (v3.1; decide_relation rows only; gold side only, never in candidates.jsonl).** The inference depth of the pair as ordered: the fewest rule constraints a reader must chain to derive the entailment from the card (docs/mentat_hard.md, section 3). An integer: 0 for a score-ladder step (one team's thresholds one point apart, including the generator's rule-1 ladder rows), a paraphrase or a negation; 1 for a generator edge that is not a ladder step; 2 or more for a derived link (drawn deepest first, 4+, 3, then 2). `"count"` for an entailment that no chain of constraints reaches (it needs a counting argument; never drawn). `null` for NONE. A `candidates.jsonl` row never carries `depth`; the scorer reads it from the label only. The independent depth audit (`audit_brute.DepthGraph`) recomputes every row's depth from the theory JSON.

The `label` values are:
- for `decide_binding`: `bound` or `unresolved`;
- for `decide_relation`: `EQUIV`, `IMPLIES`, `IMPLIED_BY`, `EXCLUDES` or `NONE`, for the pair as ordered (a, b), with its `depth`;
- for `decide_group`: `exactly_one`, `ordered_chain`, `at_least_k`, `at_most_k`, `at_most_one` or `none`.

**Canonical group label:** the first entailed kind in this order: exactly_one > ordered_chain > at_least_k (largest k) > at_most_k (smallest k) > at_most_one > none.

**order:** for `ordered_chain` labels only (null on every other line), the members strongest first: each member implies the next in every interpretation. The order is unique: no two members are equivalent (a candidate set whose members would form a chain with an equivalent pair is not asked). The label itself is the kind; `order` lets the scorer report a secondary "chain order correct" metric.

**held_out:** true if any outcome in the question uses a test-only family.

**terms (v2):** a `decide_group` label row may carry `"terms": [[kind, k], ...]`, the canonical constraint terms of its gold
bounds: `group_terms(lo, hi, n, chain)` in `adapters/base.py`. The order is fixed: ordered_chain first, then at_least_k, then
at_most_one or at_most_k; exactly_one alone for lo = hi = 1; `[]` for no constraint. Exactly-k is `[at_least_k k, at_most_k k]`. `group_terms` raises ValueError unless 0 ≤ lo ≤ hi ≤ n and hi ≥ 1.
`label` and `k` stay as terms[0] (v1-compatible). A decoy has `"terms": []` and label `none`. A row without `terms` is a v1 row.

**Reading questions (v2):** after all curated group rows, `candidates.jsonl` has rows with `"source": "reading"` and a qid that
continues the group numbering:

```
{"qid": "g0xxxx", "op": "decide_group", "members": [u, o1, o2, o3, o4], "source": "reading"}
```

`u` is the outcome asked about (an unresolved ambiguous outcome, or a bound decoy); `o1..o4` are its four options, shuffled.
In v3 three of the four are on u's team (the two readings and a same-team near miss, or the gold, the same-team mirror and the
near miss) and the fourth is a distractor of the same league and kind.
The suite label row is:

```
{"qid", "op": "decide_group", "stratum": "reading" | "reading_decoy", "readings": {"base": oid|null, "alt": oid|null},
 "label": "reading", "k": null, "held_out": bool, "missing": null, "order": null}
```

`readings[r]` is the option that `u` maps to under reading `r` (null: none). A decoy has the same option for both readings.

**Answer:** a list of group decisions. For each reading `r` with a pick `c` (not none), two `ordered_chain` decisions, members
`[u, c]` and `[c, u]`, each with `"scope": {"axis": "reading", "values": [r], "of": ["alt", "base"]}`. A none pick emits nothing for
its reading, and `[]` is a valid answer (both none).

**Scoring:** each reading row gives two items, op `decide_reading`, one per reading. The truth is `opt<i>` where `members[i]` is
`readings[r]` (i in 1..4), or `none`. The prediction is `reading_picks(answer, members)` in `adapters/base.py`, the one function the
scorer and the harness share: it returns each reading's pick, an option id, `none` or `invalid`, and the scorer maps it to a class. The
rules: the chains read are the valid two-member `ordered_chain` decisions that contain `u = members[0]`, in their implied order (a
reverse direction reverses it). A chain counts for reading `r` when its scope is None, or when its axis is `reading`, its `of` is
exactly `["alt", "base"]` and `r` is in its values. A pick `c` needs both `[u, c]` and `[c, u]` among the counted chains, and those
two must be the only counted chains. No counted chain gives `none`; any other set gives `invalid` (two options, a half pair, or a stray
chain). So an unscoped pair answers both readings: on an ambiguous row the second reading is wrong, and on a none row it is wrong for
both. Non-chain and malformed decisions are ignored. `__missing__` is only for an unanswered or errored item, or an answer with no
valid decision and a malformed one. Readings carry no probs. The component is `readings`.

## gold_view answers (diagnostic)

`gold_view` (`adapters/mock.py`) answers the curated suite from its labels, so it is a diagnostic and not a product adapter:
- **One decision per term:** a group row with `terms` gets one group decision per `[kind, k]` term, over the candidate's members
  (an `ordered_chain` term over the label's `order`, forward). A row without `terms` gets its one canonical decision (v1).
- **Scoped reading pairs:** a reading question gets, for each reading with a pick `c`, the chains `[u, c]` and `[c, u]` scoped to
  exactly that reading (see Reading questions). A none pick emits nothing.
- **File-order member-tuple key:** a group or reading answer is looked up by `tuple(members)` in candidate-file order, and the
  context must present the members in that order.
- **Exact-duplicate refusal:** two questions with the same members in the same order raise ValueError. The same member set in
  another order is a different key, so both are kept.

## Decision scoring (score_decisions.json)

The report's headline column is **rel+grp MCC (headline)**, the mean of the relation MCC (`ops.decide_relation.mcc`) and the group MCC (`ops.decide_group.mcc`), for v3 and later fixtures; for v1 and v2 it is the relation MCC alone (**rel MCC**). **bind MCC (secondary)** is the binding MCC, and **read MCC** is a diagnostic (`python -m mentat_bench.report`). Owner decisions 2026-10-29 (relation MCC) and 2026-10-30 (relation and group MCC mean for v3 and later).

`decision_scoring.py` gives the MCC of four ops: relations, groups, bindings and readings, each in its `ops.<op>.mcc`. A group row with `terms` is
predicted by the canonical class of its answer's bounds, counting only unscoped decisions over the row's members: lo is the
largest lower bound, hi the smallest upper bound, and chain says whether an ordered_chain is asked; the class is
`group_terms(lo, hi, n, chain)` joined by `+` (`invalid` when lo > hi, `none` with no decisions). A v1 row keeps the first-group rule.

**Malformed siblings:** a decision that fails the group checks is skipped, and the row is scored from its valid decisions. A row
is `__missing__` only when it has no valid decision and at least one malformed one (not a list at all is also `__missing__`).

**Probs:** a group row with `terms` is not scored by probs, because the child's probs are over kinds and do not name a compound
class. Such rows are excluded from the group Brier and ECE, and counted as excluded. A v1 row keeps its probs.

`headline` for a v3 or later fixture is `{"metric": "relation_group_mcc", "relation_group_mcc": mean, "parts": {"relation_mcc": x, "group_mcc": g}, "secondary": {"binding_mcc": y}, "diagnostics": {"reading_mcc": r}}`, where `mean` is the mean of `x` and `g`. For a v1 or v2 fixture it is `{"metric": "relation_mcc", "relation_mcc": x, "secondary": {"binding_mcc": y}, "diagnostics": {"group_mcc": g, "reading_mcc": r}}`, with no mean. An op with no items is null. The v3 group MCC is now answerable (two-judge audit, 2026-10-30). Output written from 2026-10-29 uses this schema. A `score_decisions.json` written before it keeps the old headline on disk (`mcc_mean`, `components`, `ops_counted`, and `mcc_mean_3` for a v2 run); readers take each MCC from `ops.<op>.mcc`, which both layouts carry. The four-part and three-part means are not scores (a post-hoc change from the pre-registered four-part mean, justified by `docs/results/mentat-hard-v2-validity-audit-20261029.md`).

## Raw decision log (written by the WS4a harness into the build work dir)
`decisions.jsonl` has one line per answered question, appended and checkpointed like `relations.jsonl`:

```
{"qid": str|null, "op", "a": OUTCOME|null, "b": OUTCOME|null, "id": OUTCOME|null, "members": [..]|null,
 "decision": {raw adapter decision: relation | status, negated, missing | groups[...]},
 "conf": float|null, "probs": {option: p}|null, "error": str|null}
```

- The harness logs the exact `(a, b)` it asked. If the order differs from `candidates.jsonl`, WS5 orients the label: a swapped order swaps IMPLIES and IMPLIED_BY.
- `qid` is filled when the build ran with `--candidates`, and null otherwise.

## Per-option probabilities (WS4b protocol and child)
A decision may carry optional `"probs": {option: p}`: finite, each value in [0, 1], summing to 1 ± 1e-2. The option keys are:

| Decision | Option keys |
|---|---|
| relation | `EQUIV`, `IMPLIES`, `IMPLIED_BY`, `EXCLUDES`, `NONE` |
| group kind | `none`, `exactly_one`, `at_most_one`, `at_least_k`, `at_most_k`, `ordered_chain` |
| binding status | `bound`, `unresolved` |

The adapter (`local_jev`) passes `probs` through, and the harness logs it. Children that do not send `probs` are unaffected.

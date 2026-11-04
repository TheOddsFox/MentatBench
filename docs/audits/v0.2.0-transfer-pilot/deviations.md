# Deviations from the pre-registration

This file records each removal of a torn last line from `judge/ledger.jsonl`, as PREREG.md (Repairs) allows. Only a torn last line may
be removed, by hand, and each removal is an entry here. An entry gives the removed line's number in the ledger as it was, the sha256 of
the removed line as written (without its line break), the reason, and the time.

No removals so far.

## Other deviations

Recorded on 2026-11-04, after the judging and before any model run.

### D1. The review sheets were simplified
The reviewer did not fill the pre-registered `review.csv` and `review_escalation.csv` directly.
- **Simplified sheets:** the reviewer filled simplified, blind versions. They carried one row per item with the propositions written as
  `[OUTCOME] question`, and no judge answers, consensus or reason.
- **Relation answers:** A->B, B->A, SAME, EXCLUSIVE, NONE or UNCLEAR.
- **Group answers:** the fewest members that must be true, the most that can be true, and whether they form a chain, or UNCLEAR.
- **Mapping back:** the answers were mapped back, by item id, into the `your_label` column of the pre-registered sheets. Relations used a fixed
  table, and groups used `group_terms` and `terms_class` from `python/mentat_bench/adapters/base.py`.
- **Validation:** both sheets were then validated by `analyse.read_sheet` before `analyse.py review` ran.

### D2. Chain definition
The simplified sheet's guide defined a chain as "each outcome implies the next, in the listed order". The protocol (JUDGE.md) defines a
chain in an order the answerer chooses.
- **Affected answers:** four answers were "no chain" only because the chain runs against the listed order: T0092, T0097, T0332 and T0356.
- **Correction:** they were changed to a chain, with their bounds unchanged. The labels became `ordered_chain`.

### D3. Consistency revisions
Four answers were changed to UNCLEAR, which drops the items. The reason is consistency with the reviewer's own rule: an item is UNCLEAR
when the text allows readings that give different answers.
- **T0085 and T0525:** whether a dated "win" and an undated "draw" concern the same match. The reviewer had already ruled this way on T0303.
- **T0156 and T0274:** two relations the reviewer had answered NONE, or answered by a revision argument not applied to similar items.

### D4. Not blind, and AI-assisted
- **The eight revised items are not blind.** All eight revisions (D2, D3) were made after an AI consistency check of the reviewer's answers.
  The check showed the reviewer the analysis behind each revision, and the reviewer approved each one.
- **The review was AI-assisted.** The reviewer reports that the sheets were filled with AI assistance, then checked and owned by the reviewer.
- **Consequence:** the human layer that PREREG.md (Labels) and SPEC.md §7 assume is weaker than an unassisted human review.

### D5. A stricter ambiguity standard
The reviewer applied a stricter ambiguity standard than the judges.
- **First sheet:** 9 of the 54 spot-check items changed, so the escalation sheet of every remaining 2-1 item was reviewed as pre-registered.
  All 9 changes were to UNCLEAR, and none moved an item to another class.
- **Unreviewed items:** the roughly 470 items the reviewer did not see keep the judges' readings. Typical cases are "reach" or "dip to" read
  as thresholds, and undated fixtures read as the dated match.
- **What bounds this:** the pre-registered sensitivity analyses, "unanimous only" and "excluding changed". The note reports them.

### D6. The model runs use runcell's build step only
PREREG ("Model runs") names `runcell_argv` and `run_env`. runcell refuses the pilot fixture: it requires `queries.jsonl` and
`gold/answers.jsonl` (`runcell.py`, `corrections.REQUIRED`), and the pilot has no exam queries. So `run_models.py` runs only the build step.
- **What runs:** `python -m mentat_bench build ...` with the argv runcell itself passes to that step for each cell. Parity was checked
  token for token for all 19 cells against runcell's own build argv.
- **Settings kept from the cell:**
  - its child, its profile and its build flags;
  - the recorded `--relation-floor`;
  - `--no-groups` for `lexical`.
- **What changes:** `--candidates` points at the pilot's candidates, and `--corpus` and `--out` at the pilot's fixture and run folders.
- **Environment:** `tools/reproduce.py` `run_env`, plus `MB_ENGINE`. `MB_S1_*` variables are stripped, and `MB_S1_ALLOW_UNPINNED` is never set.
- **Engine:** the binary is `target/release/mb-engine.exe`, sha256 `38fc6f…`. That is the binary the 17 v0.2.0 model cells record.
  The v0.1.0 baseline cells record another binary (`2df1bd…`).
- **Baselines:** `lexical` and `heuristic` reproduced exactly with the current code in the fresh-clone check of 2026-11-03
  (`docs/reproductions/2026-11-03-fresh-clone.md`).
- **Code since v0.2.0:** between the v0.2.0 model cells' commit (`f1652ad`) and this branch, the harness, cli, adapters, children,
  `decision_scoring`, the engine wrapper and the IR are unchanged. Only pins were added to `ollama_models.json`.
- **Records:** each run records its engine sha256, git commit and the hashes of the code it used.

### D7. Decisions are mapped to classes without `decision_scoring.load`
`decision_scoring.load` refuses the pilot's suite labels: their group labels are full classes, such as `at_least_k:2+at_most_k:3`.
- **The mapping:** `predictions.py` applies decision_scoring's own helpers to each decision. Those are `read_decisions`, the relation
  orientation and answer, and `terms_class(group_terms(...))` for groups. On synthetic decisions it gives the class `load` would give in 33
  of 34 cases.
- **The one difference:** a group whose decided bounds are impossible (lower above upper). v0.2.0 kept the literal `invalid` as a predicted
  class. The pilot maps it to `__missing__`, because `analyse.py score` accepts only an item's labels or `__missing__`. Both are wrong
  answers, so accuracy is unchanged, but the MCC marginal can differ slightly. The note reports the count, from `mapping_summary.json`.
- **No floor:** as in v0.2.0's scoring, no floor is applied to the decisions. The relation floor acts only when the build admits candidates.

### D8. Ollama version
The runs use Ollama 0.40.13, as the 17 v0.2.0 cells record. `ollama_models.json` records 0.40.11 for 15 of its pins, from when they were
pinned. The child checks each model's digest, not the server version, and each run records its tag and digest.

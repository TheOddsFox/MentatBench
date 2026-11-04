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

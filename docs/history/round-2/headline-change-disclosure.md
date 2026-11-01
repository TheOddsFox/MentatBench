# Mentat-Hard v2 validity audit, 2026-10-29

This audit asks whether each Mentat-Hard v2 question type measures what its score claims. It has three checks:

1. **Answerability:** can a careful reader reach the gold answer from the question text alone?
2. **Prompt sensitivity:** do the scores hold when the prompt wording changes?
3. **Outside models:** what would an outside lab need to run its own model?

The audit led to a **post-hoc change of the headline** (section 4). The audit data and scripts are in
`docs/results/mentat-hard-v2-audit/`.

## 1. Answerability (blind judges)

**Sample.** 150 items from the **dev** split (`random.Random("audit:2026-10-29")`, `build_sample.py`):

| Question type | Items |
|---|---|
| Relations | 30 (6 per gold class) |
| Groups | 30 (15 real constraint sets, 15 decoys) |
| Bindings | 30 (15 clear, 15 ambiguous) |
| Reading rows | 60 (30 ambiguous, 30 decoy), each with a literal (base) and a second-reading (alt) question |

**Prompt text.** Each prompt is the exact text System One child v5 sends, captured through the real child against a
local fake server. `verify.py` rebuilds all 150 prompts by a separate path, and they match. The sample file holds no gold,
strata or internal ids; its only ids are opaque (A001 to A150). For groups, one neutral line asks for the kind and both bounds together.

**Judges.** Two independent judges (Claude Haiku 5.5, high effort), each with its own copy of the sample and no access to
the key. They were told to answer as a careful, logically precise expert, from the text alone. The instructions added two
reading conventions: a label of No negates the question, and "not fail to reach N" means "at least N". This is a model
judgment, not a human study.

**Results** (accuracy against gold; `score.py`):

| Type | Stratum | Judge 1 | Judge 2 | Same answer from both judges |
|---|---|---|---|---|
| Bindings | clear | 15/15 | 15/15 | 15/15 |
| Bindings | ambiguous | 13/15 | 15/15 | 13/15 |
| Relations | NONE (hard distractors) | 6/6 | 6/6 | 6/6 |
| Relations | real links (EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES) | 5/24 | 17/24 | 12/24 |
| Groups | decoy sets | 13/15 | 15/15 | 12/15 |
| Groups | real sets | 1/15 | 5/15 | 7/15 |
| Readings | decoy rows (both readings) | 11/30 | 29/30 | 12/30 |
| Readings | ambiguous rows, literal | 0/30 | 14/30 | 7/30 (base and alt together; base alone 16/30) |
| Readings | ambiguous rows, second reading | 0/30 | 0/30 | |

Judge 2's relation accuracy by class: EQUIV 6/6, IMPLIES 5/6, IMPLIED_BY 4/6, EXCLUDES 2/6.

**What the failures show**
- **Bindings are answerable from the wording** (28/30 and 30/30).
- **Most relations are answerable.** EQUIV, IMPLIES and IMPLIED_BY links follow from thresholds and wording. Many EXCLUDES
  links and some cross-category links instead depend on league rules the text never states, for example that a cup
  winner cannot also be its runner-up, or how cup places relate to playoff places.
- **Real group constraints mostly depend on unstated league rules.** Example (A002): "exactly one of: Bardale wins the
  Norura Cup / is left out of the Norura playoffs / is the cup runner-up". This holds in the generated world because of
  the generator's league rules, but no question text says so. Both judges missed it.
- **The second reading is not answerable by meaning.** Neither judge got a single second reading right on ambiguous rows
  (0/30 each).
  - For "by the break" propositions, the generator defines the second reading as the neighbouring threshold (n ± 1),
    which no wording supports. It is reachable only by eliminating the other options.
  - For "top half" propositions, which reading counts as literal (playoff place or a points threshold) is the
    generator's choice. Judge 2 found the literal reading 14/30 times, mostly on "by the break" rows.
  - Decoy rows (clear propositions, where both readings are the same option) are answerable: Judge 2 got 29/30.

**Conclusion.** Bindings and most relations measure understanding of the wording. Real groups and second readings
largely measure whether a model guesses rules the generator never states. Their scores do not support claims about
logical understanding until the generator states those rules in the text, or the gold stops depending on them.

## 2. Prompt sensitivity

Two paraphrased prompt sets were run on the dev split, through a scratch wrapper around child v5 that changes only the
text the model reads:
- **A:** every instruction and criterion is reworded, and the binding examples ("by the break", "top half") are kept.
- **B:** as A, but without examples of ambiguous wording.

| Model | Prompt | Relations | Bindings | Groups | Readings |
|---|---|---|---|---|---|
| D3 27B | v5 (pre-registered) | 0.697 | 0.000 | 0.202 | 0.635 |
| D3 27B | A | 0.705 | 0.000 | 0.260 | 0.635 |
| D3 27B | B | 0.705 | −0.011 | 0.260 | 0.635 |
| D3 9B | v5 (pre-registered) | 0.557 | 0.213 | 0.103 | 0.635 |
| D3 9B | A | 0.557 | −0.005 | 0.179 | 0.612 |
| D3 9B | B | 0.557 | −0.005 | 0.179 | 0.612 |

**Relation MCC is stable** under rewording: within 0.01, even though individual answers change (804 of 10,000 relation
labels differ between v5 and A for D3 9B, and the full decision records differ for all 10,000, yet the MCC moves by
0.0002). **The other components are fragile.**
- **Bindings:** D3 9B's binding MCC drops from 0.213 to about 0. It rested on 6 correct flags out of 94 ambiguous items.
- **Groups:** scores move by up to 0.08.

Removing the ambiguity examples (B vs A) left every D3 9B MCC unchanged (no relation or group decision changed; 4 of
2,708 binding labels flipped) and moved only D3 27B's binding MCC (0.000 to −0.011).

## 3. Outside models

`docs/outside_models.md` sketches how an outside lab would plug in its own model. It covers:
- the JSON-lines child protocol;
- the current dependency on the custom Ollama `/v1/systemone` endpoint;
- a proposed generic child for OpenAI-compatible chat endpoints;
- how results would be labelled;
- how a private-split run is requested. The owner runs it with the repository's own child only.

The generic child is not implemented.

## 4. Decision: post-hoc change of the headline (owner, 2026-10-29)

The pre-registered headline (PR #67) was the mean of four MCCs: relations, groups, bindings and readings. Based on
sections 1 and 2, the owner changed it **after** the test results were known:

- **Relation MCC is the single public headline.** It is answerable from the text and stable under rewording.
- **Binding MCC is a secondary score, reported with its interval.** It is answerable, but sparse: 92 ambiguous items on
  test, and few models flag any of them.
- **Group and reading MCC are diagnostics with known validity problems.** They are excluded from every score until the
  generator is fixed.
- **Q_N and strict Q_N are diagnostics only.**

This is a post-hoc change, disclosed as such. The published v2 results keep the pre-registered four-part numbers; the
addendum to `mentat-hard-v2-20261029.md` re-reports them under the new headline. On the v2 test split, the ranking by
relation MCC differs from the four-part ranking: D3 27B leads clearly (0.704), while Clef 27B (0.462) falls behind D3 9B
(0.548) and Pplx Decider (0.538).

## Limits
- **Model judges.** The judges are two instances of one smaller model, not humans. Disagreement is high on groups and
  readings, which supports the conclusion that those types are not answerable from the text. A careful human might do
  better on relations: Judge 2 got 17/24 real links right, Judge 1 only 5/24.
- **Small sample.** 30 items per type, on dev only.
- **Prompt sensitivity covers two models and two paraphrase sets.**

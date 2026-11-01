# Mentat-Hard results bundle v0.1.1 summary (analysis of the v0.1.0 measurements)

This public bundle reports the development round 3 measurements on the Mentat-Hard test set, revision 3.1, released as v0.1.0.

**What it measures:** how accurately System One decision models (small typed-decision LLMs, run locally through an Ollama `/v1/systemone` endpoint) make the typed decisions a logical map is built from. There is no generated reasoning: each question has a fixed menu of typed answers, one question per request, and the model returns a probability for each option.

Full write-up: [README.md](README.md). Leaderboard (machine-readable): [v0.1.0/leaderboard.json](v0.1.0/leaderboard.json). How the benchmark was built: [../docs/how-the-benchmark-was-built.md](../docs/how-the-benchmark-was-built.md).

## The test

Each question is about a made-up sports league: teams, scores, playoffs, a cup and draw groups. Each league has a rule card, a plain-English statement of its rules, shown with relation, group and reading questions but not with bindings.

| Question type | What the model answers | Test split |
|---|---|---|
| **Relations** | The logical link between two propositions: EQUIV, IMPLIES, IMPLIED_BY, EXCLUDES, or NONE (no link) | 6,000 pairs (1,500 NONE) |
| **Groups** | The constraint a set of 2–8 members satisfies: exactly_one, ordered_chain, at_least_k, at_most_k, at_most_one, or none | 1,000 sets, plus 184 reading questions |
| **Readings** | For a phrase with two meanings ("by the break", "top half"), which option each reading picks | 184 questions, 368 items |
| **Bindings** | Whether a market's proposition is bound, or unresolved (ambiguous) | 3,348 |

What makes it hard:
- **Inference depth.** Each relation carries an inference depth, 0 to 4+: the number of rule steps a reader must chain to derive it from the card. The depth is a gold-side tag the model never sees. Depth 0 holds EQUIV pairs, same-market pairs and score-ladder steps.
- **Close NONE pairs.** NONE is 25% of the relations. Two thirds of the NONE pairs are same-league pairs that share a team, a threshold, a draw group or a rule constraint; the other third is matched to the word overlap of the real links.
- **Held-out wording.** 2,332 of 6,000 relations (38.9%) have at least one proposition worded in one of ten phrasing families (21–30) that appear only in the test split, never in dev.
- **Answerability audit.** The audit checked that the gold answers can be derived from the prompt text. It used two model judges (Haiku 5.5, not people) on a 30-item sample per part. The revision 3.1 audit's first round failed on reading decoys because an instruction was ambiguous. Child version 7 fixed the instruction, and round 2 passed all eight parts for both judges.

## Scoring

- **Headline:** the mean of relation MCC and group MCC, on raw decisions with no relation floor. The owner chose it on dev on 2026-10-30, before any test run. MCC is the multiclass Matthews correlation (Gorodkin's R_K): 0 is chance, 1 is perfect.
- **Secondary:** binding MCC.
- **Diagnostics only:** reading MCC, held-out vs seen relation MCC, accuracy by depth, and Q_N (the end-to-end score of the model's compiled base over 200 queries).
- **Intervals:** 95% percentile intervals from a league bootstrap (18 leagues, 2,000 draws, seed 0). Paired gaps are reference minus run, with D3 27B as the reference, chosen on dev.
- **Pre-registration:** the matrix, headline rule and reference were registered before any test run. Changes made after the dev results were decided before the test runs and are disclosed as post-hoc.
- **Private split:** a sealed split checks that the results replicate. Its directory is not published; only its content hash and aggregate results are.

## Results (test split)

| Model | Headline [95% CI] |
|---|---|
| **D3 27B** | **0.519** [0.497, 0.541] |
| D3 9B | 0.275 |
| Pplx Decider 27B | 0.253 |
| Kev 9B | 0.236 |
| D3 4B | 0.233 |
| Clef 27B | 0.207 |
| Nimble-v3 9B | 0.201 |
| Nimble 9B | 0.178 |
| Clef-Flash 9B | 0.172 |
| TEV1 4B | 0.144 |
| D1 3B | 0.071 |
| D3 2B | 0.055 |
| TEV1 0.8B, D1-Omni 600M, Laya 0.4B | −0.000, −0.003, −0.020 |
| *Rules baseline (heuristic child)* | *0.140 [0.121, 0.153]* |
| *gold_view (perfect curated answers, the ceiling)* | *1.000* |

The lead comes from both parts. On relation MCC, D3 27B scores 0.788 and the next model, Pplx Decider 27B, 0.512. On group MCC, D3 27B scores 0.249 and the next, Kev 9B, 0.147.

What's settled:
- **D3 27B is first.** Every other model, and every baseline except gold_view, sits below it by a paired gap whose interval excludes 0. The smallest gap is to D3 9B, 0.244 [0.221, 0.266]. gold_view is the only run above D3 27B.
- **Nine models clear the rules baseline.** Their headline intervals lie entirely above the rules interval on test. This comparison is unpaired. On the private split the count is seven.

What isn't:
- **The next four.** D3 9B, Pplx Decider 27B, Kev 9B and D3 4B overlap on every pair of headline intervals, so their order isn't settled.
- **Second place on groups.** Kev 9B 0.147 [0.121, 0.175] and Nimble-v3 9B 0.126 [0.106, 0.147] overlap.
- **The rest of the order.** The results JSON lists 27 overlapping pairs of headline intervals. Overlap is not evidence of a tie. This v0.1.0 summary pairs only the gaps to D3 27B; the v0.1.1 analysis adds Holm-adjusted pairwise comparisons among all 15 models (89 of the 105 headline pairs resolved; see [v0.1.1/analysis.json](v0.1.1/analysis.json)).
- **Runs near zero.** Lexical, embedding, the hf baseline, TEV1 0.8B and D1-Omni 600M have intervals that include 0. Laya's lies below 0.

Private split (`mentat_hard_private`, sealed; aggregates only):
- **The headline ranking replicates.** Spearman is 0.98 over 16 runs (15 models and the rules baseline). Every headline is within 0.025 of its public value; the largest move is Clef-Flash 9B's, −0.025. The top three keep their order.
- **Some things move.** The middle order, second place on groups and the binding scores change between splits.

Other findings:
- **Depth:** D3 27B scores 0.915 at depth 0, 0.920 at depth 1, 0.716 at depth 2 and 0.648 at depth 3. Depth 1 is the generator's stated edges other than score-ladder steps (ladder steps are depth 0). The depth 4+ bucket has only six rows. These figures are descriptive, not scores.
- **Held-out wording:** D3 27B's relation MCC is 0.787 on both seen and held-out wording. Seven of the 15 models lose 0.05 or more.
- **Readings:** D3 27B leads, with reading MCC 0.804 (diagnostic).
- **Q_N** does not follow the decision ranking. The rules and lexical baselines score 32.2, above D3 27B's 25.0. Fourteen of the 15 model builds were repaired, so their strict Q_N is 0.

## What the results support

Three tiers. Each claim is backed by the results in [README.md](README.md).

**Well supported**

- D3 27B (rank 1 on the headline) leads this benchmark's relation and group decision tasks, and so the headline, their mean. Relation MCC 0.788, against 0.512 for the next model (Pplx Decider 27B); group MCC 0.249, against 0.147 for the next (Kev 9B). It does not lead binding: its binding MCC is 0.000.
- Its headline difference from every other model is resolved after Holm correction (paired league bootstrap). All 14 of its headline pairs are resolved, each in its favour.
- The owner-run private-split check agrees on the leading model: D3 27B is first on the private split too (0.525, against 0.273 for D3 9B). This is a replication run by the owner, not an independent reproduction.

**Partially supported**

- Generalisation to new wording within the same generator (held-out phrasing families). D3 27B's relation MCC is the same on held-out and seen wording (0.787 and 0.787), but seven of the 15 models lose 0.05 or more on held-out wording.

**Not established**

- General decision-making ability. The benchmark measures typed decisions from one generator, not decision-making in general.
- Building reliable, consistent knowledge bases. Strict Q_N is 0 for 14 of the 15 models (tev1_0.8b-q8_0 is the exception, at 17.0), D3 27B included. Local conflicts: 1,505 of D3 27B's 5,172 claim-bearing answers (29.1%; 95% interval 23.5% to 34.3%) belong to a minimal conflict of at most three answers under either predicate (logical UNSAT, or a forced suite outcome), and its answer set contains a detected contradiction. 83 of them belong to a minimal logically unsatisfiable set of at most three answers.

### What this benchmark does not measure

- One synthetic sports-league generator. Every item comes from it.
- Inference depth. Of the 4,500 named relations, 3,840 are depth 0 to 2, 654 are depth 3, and only six are depth 4 or more, all in one league (test-L10).
- Held-out wording. The held-out split changes wording, not rule types.
- Repeats. The headline is one run per model. The option-shuffle pass covers 15 models (one seed, 20261101), and the run-to-run, Vulkan and backend repeats are diagnostics, not extra headline runs. The first d3:9b repeat attempt ran on Ollama's Vulkan backend by mistake; the CUDA repeats were run afterwards in a separate folder.
- Test gold is public. Models built or tuned on it would carry a contamination risk that these scores do not measure.
- One shuffle seed, one machine and one backend for the repeats (plus the Vulkan diagnostic). The build costs come from one machine.
- Prompt wording varies only through the held-out phrasing families. No other prompt variants were run.
- Judges. The answerability audits and the item audit use model judges (two Haiku 5.5 judge sets), not people. The item audit judged all 423 all-wrong items, 127 sampled weak-over-strong items and 50 unflagged controls. Pre-registered result: 13 of 550 judged flagged items are suspect labels. This result is fragile: 9 of the 13 are group items from one judge slice, where one judge gave the gold bounds under the kind label `none`, and one judge instance in that slice agreed with gold on only 24 of 100 items; with the discarded first answers for that slice the count is 5. Under an exploratory bound-based reading, 4 candidates remain (g00453, g00551, r03602, r04933). None is a confirmed label error; no score changes. 0 of 50 controls were labelled suspect (exact two-sided 95% interval 0% to 7.1%; one-sided upper bound 5.8%; only 5 controls are groups). Flags and suspect labels are candidates for review, not confirmed label errors. Suspect labels are not adjudicated, and they remain in the scores.
- Consistency. The check is sound but incomplete, and it ignores paraphrase identities between markets, so its counts are lower bounds.
- Private replication. The private split is owner-run, and only its aggregates are published.
- External reproduction. There is none yet.

None of this measures general reasoning.

## Limits

- **One run per model.** Each model was run once on test, so the intervals cover item and league sampling only. A dev repeat of D3 9B was byte-identical.
- **Laya's context is too short.** Laya 0.4B has a 512-token context and abstains for length on 83% of relation questions. Its abstentions are scored as NONE, as pre-registered.
- **Two models at chance.** TEV1 0.8B and D1-Omni 600M score at chance.
- **An unexplained binding drop.** D3 9B's binding MCC fell from 0.293 on dev to 0.000 on test, and the cause has not been identified.
- **Item audit.** Its 13 suspect labels are fragile: 9 come from one judge slice, and 4 remain under an exploratory bound-based reading. None is a confirmed error, and no score changes.
- **Scope of the evidence.** The audit judges are models. The data is synthetic and covers a single domain. The test gold is public. Candidate selection uses gold, so blocking is not tested.
- **Licence.** Nimble-v3 is licensed CC-BY-NC-4.0 (non-commercial use only).
- **Post-hoc changes.** Some design changes were made after seeing dev data. All were decided before any test run, and all are disclosed.

## Practical facts

- **Hardware:** two NVIDIA RTX A6000 GPUs, one Ollama server per GPU (Ollama 0.40.11). All runs are local, at zero API cost.
- **Run time:** on test, the three 27B models' builds took 74.4 to 78.7 minutes, against an 80-minute target and a 5,400 s build deadline. The slowest 27B cell on the private split took 76.9 minutes.
- **Release gates (v0.1.0):**
  - MET: gates 1, 2, 7, 9 and 10. Gate 7 is MET at the pinned commit only.
  - MET by owner attestation: gates 6, 8 and 11.
  - Not applicable: gates 3 and 4.
  - Removed: gate 5.

  See [../release/v0.1.0/record/check_gates.md](../release/v0.1.0/record/check_gates.md).

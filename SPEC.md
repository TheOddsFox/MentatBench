**Specification, revision of 27 October 2026 (proposed on that date; the specification the v0.1.0 results were measured under).** MUST denotes a release requirement. Numeric defaults are frozen in the release manifest before any scored run.

**Changelog.** Revision of 2026-10-27 (owner decisions): the focus is System One models under the fixed harness (s1, s5, s9). The Exact Scale panel, the Exact 1M label, its scale requirements and the 2026-10-18 semantic-scale counts are removed (s3, s4, s6, s8, s10); the exact-scale track was measured up to 1M and then retired (docs/exact_scale.md). The Mentat-Hard panel is the main System One panel and the headline (s4, s8). The 2026-10-29 owner decisions make the relation MCC the only headline: the binding MCC is secondary, the group and reading MCCs are diagnostics that enter no score, and the four-part mean is no longer a score (a post-hoc change, justified by the validity audit in docs/history/round-2/headline-change-disclosure.md). The 2026-10-30 owner decision makes the headline, from fixture version 3 on, the mean of the relation MCC and the group MCC, decided on dev before any version 3 test run or pre-registration: version 3 adds rule cards, and a two-judge audit of its groups found them answerable (both judges 15/15 on real groups; docs/audits/test-set-groups/notes.md, merged in #76), so the group MCC is a headline part again. The binding MCC stays secondary, and readings and Q_N stay diagnostics. Versions 1 and 2 keep the relation MCC headline: their published documents are history, and the round-2 groups were invalid. Q_N and strict Q_N are diagnostics (s8). A release under this revision may be Mentat-Hard-only by declaring its scope (`release build --scope mentat-hard`, s10): the native items of s10 then do not apply to that release, and the manifest says so. Everything else is unchanged from the 2026-10-18 spec (`docs/history/specs/spec-2026-10-18.md`, kept as history). The history of earlier revisions and of the development rounds is in [docs/history/README.md](docs/history/README.md).

**Relabel (2026-11-01).** The labels of this file were changed to revision names, and no requirement changed. The text as first issued is `docs/history/specs/spec-2026-10-27.md`; the history is in [docs/history/README.md](docs/history/README.md).

# 1 Purpose and evaluation unit

Mentat Bench measures whether an AI system can compile a corpus of natural-language propositions into a reusable, logically faithful semantic representation. Evaluation uses unseen queries requiring discovery, alignment, composition, and consequences across the corpus.

This revision focuses on **System One models under the fixed harness** (s9): models that answer the harness's typed decisions (bindings, relations, groups) in one pass, without generated reasoning. The benchmark asks whether such a model understands the logical relationships between propositions quickly and accurately.

The evaluation unit is **one cold corpus compilation followed by a hidden workload**. Pair classification is a diagnostic. It is not the headline task or score.

The flagship question is: **After one pass of typed decisions over a corpus, does the resulting semantic model support correct unfamiliar conclusions across the corpus within a declared resource budget?**

A market question introduces a proposition. It does not assert that the proposition is true. The compiled theory contains definitions and justified relationships; query hypotheses supply asserted truths.

# 2 Corpus and meaning policy

The initial source is the Polymarket catalogue contract, `DATA.md` (catalogue snapshot; not distributed). It describes 4,321,358 market records and 8,639,383 outcome records, including 4,319,644 usable markets. Benchmark builds require the four Parquet relations and manifest, pinned by their release and content hashes.

An outcome is referenced by `(venue, market_id, outcome_index)`, with its original asset identity retained. Questions, outcome labels, and event context jointly determine meaning. All outcomes of a selected market remain available, including nonbinary outcomes. Identifiers remain strings; membership joins preserve unlinked markets.

The evidence policy (unchanged from the 2026-10-18 spec) is **catalogue proposition semantics**. The source omits resolution rules, descriptions, per-record timestamps, and prices. The system MUST NOT infer payout equivalence, winning outcomes, statistical independence, or trading availability from these fields. Event membership does not assert exclusivity or exhaustiveness. Missing dates, measurement sources, or boundary conventions remain unresolved when they affect a conclusion. Contractual resolution follows predefined market rules; an enriched contract-semantics edition requires archived rules supplied as evidence. [Polymarket resolution policy](https://help.polymarket.com/en/articles/13364518-how-are-prediction-markets-resolved).

All permitted domain axioms are supplied explicitly. Current-world facts and external browsing are excluded during evaluation. World-space exhaustiveness is relative to the declared theory; catalogue coverage is relative to the immutable snapshot, whose upstream completeness is not claimed.

# 3 What is counted

A Mentat-Hard result MUST report what its panel specification (`docs/mentat_hard.md`) counts: the outcomes and their semantic classes, the curated questions per decision op, and the exam queries. A native result reports the outcome records and the unresolved records of its scopes.

The 2026-10-18 semantic-scale counts (N_sem, N_gold, N_active and the structure statistics) are not required in this revision: their producers belonged to the removed exact-scale track. Where a count of semantic classes is reported, repeated wording, affirmative/negative tokens, and padding cannot supply it, and raw-record counts never substitute for it.

The system need not materialize a relationship for every pair. Compact shared expressions and higher-order constraints are valid representations.

This revision requires no scaling experiments and no Exact 1M label: the corpus-growth and semantic-growth experiments of the 2026-10-18 spec belonged to the removed exact-scale track.

# 4 Evaluation panels

| Panel | Input and gold | Permitted claim |
| - | - | - |
| Mentat-Hard | A small generated corpus with exact gold and a dev/test split | Exact performance over the declared full corpus |
| Native Catalogue | Original Polymarket records; independently audited, fully interpreted query scopes plus sampled audits of emitted claims | Exact performance inside declared scopes; estimated precision for audited open discoveries |

The panels have separate scores. Controlled or altered records MUST be identified as such in release documentation and MUST NOT be presented as original Polymarket records. The native panel establishes fidelity to real source language; the Mentat-Hard panel establishes exact behavior on generated material whose gold is known.

## Mentat-Hard panel

A small generated fixture with exact gold and a dev/test split is the main System One panel. It is specified in `docs/mentat_hard.md`, with its interfaces in `docs/mentat_hard_contracts.md`.

Each exact-scored native query declares both its allowed evidence/axioms and its output universe. Every eligible record must have a reference interpretation or an explicit unresolved status. The engine uses only constraints whose complete expanded source references and provenance fall within that evidence scope; the reference engine obeys the same restriction. Outside evidence cannot silently change an exact-scored answer. A scope may span many events and thousands of records. Scope membership is a coverage boundary, not a shortlist of relevant answers.

Unannotated relationships are **UNJUDGED**, not false. Unrestricted native-corpus recall is not reported without complete gold. Isolation of an inserted controlled domain from native background must be established by construction or adjudication; internal gold alone does not prove isolation.

# 5 Execution protocol

```text
build(corpus_dir, evidence_policy, resource_profile, output_dir)
    -> bindings, theory, provenance, usage

seal(output_dir)
    -> immutable submission digest

evaluate(sealed_submission, hidden_workload)
    -> verified answers, semantic scores, resource report
```

The builder receives the entire corpus, policy, schemas, development examples, and budget. It receives neither hidden queries nor per-query relevant-record lists. All corpus-specific preprocessing starts inside the metered run.

At the deadline, the builder seals its representation. **No model calls, new semantic parsing, web access, or submission edits are allowed after sealing.** A common deterministic query engine evaluates the hidden workload from the submitted representation. Raw text alone is not an executable semantic submission.

The public intermediate representation supports:

- Boolean references, negation, conjunction, disjunction, and implication.

- At-most, at-least, and exactly-k cardinality constraints over outcome indicators.

- Bounded integer-weighted sums of outcome indicators compared with integer constants.

- Shared expressions and aliases, avoiding redundant quadratic edge lists.

- Explicit unresolved interpretations and provenance for every nontrivial binding or constraint.

The only free Boolean variables are registered outcome IDs. Helpers are acyclic expression aliases, not new ungrounded world variables. Reference meanings can therefore be substituted for each source ID when validating an assertion. Implication cycles are permitted and can express equivalence; expression-definition cycles are not.

Ambiguity is represented by a finite or symbolic set of admissible theories over the same IDs, with bounded missing-qualifier codes and an explicit coverage definition. Definitive relations must hold across all admissible interpretations. Unresolved atoms retain their Boolean identity: asserting P entails P, and P with its negation is inconsistent. Missing interpretation blocks a judgment only when it can change that judgment. Exact ambiguous fixtures require an exhaustive interpretation specification; an unbounded informal uncertainty description is insufficient gold.

Unrestricted programs and executable source text are not valid intermediate representations. Numeric and temporal language is compiled into relationships among the observed propositions. Entity, time, jurisdiction, unit, measurement source, and definition scope must remain distinct whenever they change those relationships. Systems may use any internal construction method, but export into the common representation.

Minimum artifacts:

| Artifact | Required contents |
| - | - |
| Bindings | Every eligible outcome ID; expression reference or unresolved status; evidence references |
| Theory | Shared Boolean expressions, weighted/cardinality constraints, and logical relationships |
| Provenance | Source record/field references or supplied-axiom references for semantic assertions |
| Usage | Model versions, all model and embedding calls, retries, token use, resource consumption, and costs |

The benchmark scores logical consequences, not a particular graph topology. Equivalent compact encodings receive equivalent credit. The common engine generates certificates using source outcome IDs, supplied axiom IDs, query-assumption indices, and public operators; private aliases are expanded. System One models do not need to generate prose proofs or arbitrary code.

# 6 Hidden workload

A workload uses **1,000 queries: 250 per family**, unless the panel's specification says otherwise. One hundred queries, 25 per family, are matched anchors, with their evidence problem and output universe held fixed. The other 900 are sampled from the active semantic content. All propositions of an exact-gold corpus are query-eligible.

Active coverage requires a registered obligation for each counted unit: `unit → query → scored output → meaning-changing mutation → verification certificate`. The mutation must change a scored answer. Mere presence, repeated CONTINGENT labels, or a hypothesis directly asserting the same atom do not establish semantic coverage.

Queries are machine-readable expression trees over source outcome IDs and the declared operators. This tests unfamiliar compositions of observed concepts. Interpretation of wholly new natural-language predicates after sealing is outside this revision.

## A Global scenario closure

Given a joint hypothesis H, classify every eligible outcome proposition against the submitted theory K:

| Label | Meaning |
| - | - |
| ENTAILED | Every surviving interpretation of K and H implies the proposition |
| REFUTED | Every surviving interpretation implies its negation |
| CONTINGENT | Every survivor establishes its meaning and permits both truth values |
| UNRESOLVED | The supplied evidence does not sufficiently determine its meaning for this judgment |

Outputs may use compressed ID sets or columnar tables. Logical contingency and missing semantics remain distinct. Each outcome must be accounted for exactly once. ENTAILED/REFUTED take priority when a hypothesis or justified relationship settles a literal even if its source is otherwise unresolved. Otherwise an adequately interpreted open literal is CONTINGENT, and an inadequately interpreted literal is UNRESOLVED.

Closure, selection, and partition hypotheses are satisfiable under every reference interpretation. Submitted closure uses only interpretations consistent with H; if none survive, that query scores zero. Inconsistent base theories are invalid. Intentionally inconsistent hypotheses are evaluated in the feasibility family.

## B Global semantic selection and joins

Return all eligible propositions satisfying equivalence, implication in a specified direction, or exclusion relative to a target expression; joins combine such predicates across source outcomes. Targets may combine several referenced propositions. Correct relationships depend on entity identity, measurement, temporal operator, quantity boundary, polarity, and evidence scope. These are logical queries over the compiled vocabulary, not free-text searches performed after sealing.

No relevant-candidate list is supplied. Evaluation uses the entire declared eligible universe. Missing candidates count as false negatives. Grouped or factorized results are permitted when the evaluator can reconstruct their exact meaning. Counts must be backed by selected IDs or a verifiable set representation.

## C Joint feasibility

Determine whether jointly asserted outcome literals and constraints admit a world. Return SAT if every admissible interpretation admits a world, UNSAT if none does, and UNRESOLVED otherwise. SAT witnesses assign query-participating outcome IDs and declared support; the verifier checks extension to a satisfying gold world in every applicable interpretation. UNSAT cores identify conflicting query assumptions and source/axiom references, verified against gold. Certificates cannot rely solely on the submitted theory.

Fixtures include jointly impossible assignments whose pairs are all feasible. A cardinality-minimum conflict core is not required.

## D Partition and coverage

Determine whether source propositions form an exclusive group, an exhaustive cover, or an exact partition of a declared target region. Return true if the claim holds in every interpretation, false if it fails in every interpretation, and UNRESOLVED otherwise. Supply a valid certificate or counterexamples demonstrating overlap, a gap, or an out-of-target member, with gold-extension checks as in feasibility.

Group members can be provided by the query or selected by a global semantic predicate evaluated against the submitted representation. The target universe and selection semantics are explicit. Trivial target duplicates and unconditional Yes/No covers are excluded when the task calls for a nontrivial partition.

## Required global difficulty

Joint-composition queries combine several materially relevant premises. Those premises must participate in a shared conclusion, feasibility decision, or coverage claim; concatenating independent subqueries does not qualify. Broad aggregation is reported separately. A panel's specification sets its own counts and minimum supports (the 2026-10-18 per-tier minimums belonged to the removed exact-scale track).

The release reports support-size strata, arity, depth, component coupling, and answer-set sizes. Support counts come from reference construction and ablation checks, not padded model explanations. Related evidence is distributed across source events and file locations. Event IDs, row order, and text-template families must not reveal gold component labels.

The reference engine must finish every gold workload within the published verification budget. Uncontrolled solver complexity is not the intended difficulty. No single lexical template family may contribute more than 10% of total scored query weight.

Freeze answer mixtures before model runs. At least 90% of selection queries have nonempty nontrivial answers. Each feasibility/partition family contains 100 positive, 100 negative, and 50 UNRESOLVED cases, or 125/125 when ambiguity is inapplicable. Closure coverage blocks include both entailed and refuted consequences; report their full class distribution and decisive-outcome accuracy. Report constant-answer, all-contingent, and all-unresolved baselines.

# 7 Ground truth and generalization

Gold annotates canonical meanings and justified domain rules, then derives relationships and query answers through the reference engine. It does not enumerate all pairs.

Natural meanings require independent annotation and adjudication. Automated verification checks formal consequences; it cannot establish that a formalization faithfully represents the language. Controlled generators begin with a formal world model and render its propositions using independently reviewed language transformations. Unreviewed model consensus is not gold.

Keep source families, near-duplicates, aliases, transformations, and connected scenario families together across development, calibration, and hidden-test splits. Include held-out linguistic templates and held-out compositions. Rename, paraphrase, reorder, and selectively alter operators to measure invariance and correct sensitivity. The fixed snapshot does not support a historical split inferred from IDs.

Audit the compiled theory as well as its query answers. Exact scopes report representation-invariant soundness: every reference-permitted world must satisfy the submitted theory, after source-ID substitution and alias expansion. With alternatives, compare their unions of permitted worlds. Report counterexamples and source-level error localization. This checks soundness, not completeness; queries test missed consequences and interpretation distinctions.

Native open-discovery audits probability-sample a preregistered, normalized assertion registry for blinded adjudication. Identity bindings, definitions, supplied axioms, tautologies, and duplicate claims cannot inflate its denominator. Report bindings and cross-proposition constraints separately, with weighted precision, the audit frame, and uncertainty. Omitted gold edges cannot automatically invalidate an emitted claim.

The benchmark must report false assertions, missed consequences, unresolved interpretations, and corpus coverage. A consistent but empty theory must score poorly. A correct-looking answer to a small workload does not certify every assertion in the submitted corpus model.

# 8 Scoring

Each query receives utility u in [0,1]:

| Family | Utility |
| - | - |
| Closure | Macro F1 over ENTAILED, REFUTED, CONTINGENT, and UNRESOLVED |
| Selection and joins | Set F1 over required IDs or tuples |
| Feasibility | 1 only for a correct decision with a valid required certificate |
| Partition and coverage | 1 only for a correct decision with a valid required certificate |

For UNRESOLVED decisions, the system must identify a gold-accepted missing qualifier or semantic scope; any required rationale is a bounded reason code/evidence reference. Multiple valid witnesses, cores, or reasons are accepted through verification rather than exact-string matching.

F1 uses TP, FP, and FN with `2TP / (2TP + FP + FN)`. For closure macro F1, classes absent from both gold and prediction are omitted; predicted-only classes score zero. For set tasks, a correctly empty result scores one. An absent, malformed, or timed-out answer scores zero. Gold fixes equivalence/complement groups and record/tuple weights before grading; each semantic class has unit total weight, independent of submitted groupings. The manifest defines how these weights enter each family's counts. Query exactness is also reported at original record identity.

The exact quality score is a diagnostic:

```text
Q_N = 100 * mean over the four families(mean query utility)
```

The headline is **the Mentat-Hard panel's (s4) score at a named resource profile** (docs/mentat_hard.md, section 5). For fixture version 3 and later it is **the mean of the relation MCC and the group MCC**, each the multiclass MCC of its curated decisions (owner decision 2026-10-30). For fixture versions 1 and 2 it is **the relation MCC** alone (owner decision 2026-10-29). It is the only headline. The binding MCC is a secondary score, reported with its league-bootstrap interval. The reading MCC is a diagnostic with known validity problems and enters no score or mean. Q_N, strict Q_N and the four family scores are diagnostics: they are reported beside the headline and are never a ranking or a headline score, because every model build is repaired and so strict Q_N is 0. The report also gives exact-query success, matched-anchor performance, active-workload performance, false-entailment counts, and theory-audit findings. Native scope quality and open-discovery audit precision remain separate from it.

Quality intervals use a preregistered paired resampling procedure over independent construction blocks or corpus seeds; queries sharing support remain dependent. If there are too few independent units, report the fixed-corpus result without a generalization interval. Exhaustive Exact audits report finite-corpus results directly. Native sampled audits use an estimator and interval accounting for selection probabilities, clustering, and finite-population sampling where applicable. Zero observed errors do not establish perfect precision. Audit precision is diagnostic, not a ranking gate.

Coverage and precision are reported together. Emitting nothing or marking everything unresolved cannot earn a high quality score. Confidence calibration on typed semantic decisions is diagnostic; confidence values are not probabilities that market events will happen.

# 9 Model comparisons and budgets

Maintain two separately ranked builder configurations:

1. **Fixed harness:** candidate generation, prompts, decision schemas, call graph, solver, and retry policy are fixed; only the model adapter changes.

2. **Open builder:** entrants may change retrieval, decomposition, models, and compilation algorithms, but export the same intermediate representation and use the same query evaluator.

An open-builder score is a system result. The fixed harness supports attribution to a model under that harness. Jev uses native typed decisions; generative models use equivalent constrained decisions. [TypeSafe interface](https://docs.typesafe.ai/introduction).

The release manifest pins hardware, memory, storage, concurrency, build deadline, evaluator setup budget, query timeout, model price schedule, and spend ceilings. It also caps representation bytes, expression nodes, interpretation alternatives, integer bit lengths, and certificate size. Every comparison identifies that profile. Report results over multiple spend ceilings rather than an arbitrary quality-per-dollar composite.

Charge all corpus-specific work: ingestion, preprocessing, embeddings, candidate discovery, model calls, retries, compilation, and index construction. Generic pretrained weights are permitted and declared; precomputed test-corpus indexes or cached semantic labels are not. Only the metered model gateway may make external requests during compilation. No external inference is permitted after sealing.

Submission loading, validation, normalization, solver setup, query-index initialization, and certificate construction are metered as setup or query work. Reference-gold grading cost is excluded. At a build cap, stop and score the latest valid complete checkpoint sealed within budget; without one, the run scores zero. Invalid artifacts, unauthorized post-seal changes, or inconsistent base theories fail the affected evaluation universe; query timeouts score zero. Submission processing overruns follow fixed timeout policy. Verified evaluator/reference defects invoke the correction process.

Report build/setup spend, build/setup time, CPU/GPU use, peak RAM, stored representation size, query p50/p95, throughput, and timeouts. Compute amortized cost as `build_and_setup_cost / query_count + deterministic_query_cost`, using the measured 1,000-query workload. Projections at other query volumes are explicitly labelled projections. Record both billed prices and usage quantities to support later repricing.

# 10 Baselines and release gates

Required baselines are an empty/unresolved theory, lexical retrieval, embeddings without semantic reasoning, deterministic parsing plus rules, a fixed model-to-theory pipeline, and oracle meanings through the common query engine. Include an oracle-retrieval or supplied-evidence diagnostic to separate candidate loss from semantic mistakes; it does not replace the corpus score.

A release is scoreable only when it includes:

- Immutable corpus manifests, evidence policy, schemas, intermediate-representation semantics, and resource profiles.

- A complete Mentat-Hard oracle; declared coverage for every native scope; audited separation of unjudged material.

- (Removed in this revision: the Exact 1M label and its requirement of 1M reference-distinct, actively tested units. The release tool keeps the gate number and reports it as removed.)

- Reference validation of every hidden query and certificate rule, including the required global-difficulty strata.

- Leakage checks, blinded annotation/adjudication policy, complete cost accounting, and passing baseline runs.

- A public correction process that versions faulty fixtures and applies changes consistently to submitted runs.

A release whose panel is Mentat-Hard only declares that scope (`release build --scope mentat-hard`, maintainer tooling, not included in the public repository). The native items above (declared coverage for every native scope, and audited separation of unjudged material) then do not apply to it, and the release records that they do not.

The static compile-and-query benchmark is part of this revision. Incremental edits, additions, deletions, and retraction of stale consequences belong to a subsequent maintenance track; constructed updates must be distinguished from observed history.

**Release outcome:** a reproducible measure of how faithfully a model and its harness convert a proposition corpus into a semantic system that remains correct under unfamiliar global queries.

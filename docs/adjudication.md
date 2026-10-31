# Native open-discovery adjudication protocol

Normative basis: `mentat-bench-v0.2.md` sections 4, 7 and 8. Implementation: `python/mentat_bench/adjudication.py`
(`python -m mentat_bench.adjudication frame|sample|export|import|estimate`).

Native open-discovery audits check the claims a submitted theory makes beyond the queries. Human annotators judge a
probability sample of those claims. Precision from this audit is a diagnostic, never a ranking gate. A claim that the
gold does not contain is not wrong for that reason: gold omissions cannot invalidate an emitted claim, and the
tooling never reads gold.

## 1. What an annotator judges

Each packet line is one **claim** about real source propositions. A packet line has three fields:

| field | meaning |
| - | - |
| `assertion_id` | opaque id (sha256 of the canonical form, which includes the interpretation scope); use it verbatim |
| `claim` | the claim in plain English; `[Question -> Label]` names one proposition (a question and one of its outcome labels) |
| `sources` | the texts behind the bracketed propositions: `ref` (`src:<market>:question`, `src:<market>:outcome_label:<n>`, `src:event:<id>:event_title`) and `text` |

A claim may begin with **"Under the interpretation ...:"** (or "Under each of these interpretations ..."). Such a claim
is conditional: it is asserted only under the named interpretations of the source meaning, and you judge it under those
interpretations alone. A claim with no prefix holds under every interpretation.

The interpretation labels are rendered in plain English where the pattern is known. `incl_tie_one` reads "in which
interval endpoints are inclusive and ties resolve to one side"; `excl_tie_none` reads "in which interval endpoints are
exclusive and ties resolve to neither side". Other labels, such as `base` or `alt`, appear quoted as they are. The labels
belong to the theory being audited: when a model's submission is audited they come from that submission, and when gold is
audited they come from gold. Packets never include model or system names, and the labels do not identify the system that
produced them.

You do **not** see the model, any gold label, the claim's category, its cluster or any other internal field. Do not
try to identify the model or consult any gold or answer file.

A proposition `[Q -> L]` is true when the market question Q resolves to outcome L. Reason only from the source texts
and from ordinary knowledge of how such questions are worded. You judge the **logic of the claim, not what actually
happened**: a claim can be correct even though the event did not occur, and incorrect even though it did.

## 2. Verdicts

Record exactly one verdict per assertion.

- **correct**: in every possible resolution consistent with the source texts (and the named interpretations, if any),
  the claim holds.
- **incorrect**: at least one such resolution makes the claim false. Give a short counterexample in `rationale` (for
  example, "a price of 81,500 makes the first question resolve Yes and the second No").
- **unjudgeable**: the sources do not settle it: the wording is ambiguous, a needed definition is missing, or the texts
  contradict each other. Say what is missing in `rationale`.

If in doubt between correct and unjudgeable, choose unjudgeable and say why. Do not guess.

## 3. Examples

1. **Threshold monotonicity (correct).** Claim: `if [Bitcoin above 81,600 on Sept 29 -> Yes] holds then [Bitcoin above
   81,400 on Sept 29 -> Yes] holds`. A price above 81,600 is above 81,400, so the implication holds in every case.
2. **Reversed threshold (incorrect).** Claim: `if [above 81,400 -> Yes] holds then [above 81,600 -> Yes] holds`. A
   price of 81,500 makes the first Yes and the second No. Rationale: that counterexample.
3. **Exclusive buckets (incorrect on these sources).** Claim: `exactly one of [tweets less than 40 -> Yes] and [tweets
   40 to 49 -> Yes] holds`. The two ranges are disjoint, but they do not cover every count: a count of 55 makes both No.
   Rationale: "a count of 55 satisfies neither bucket".
4. **Binary complement (correct).** Claim: `exactly one of [Will X win -> Yes] and [Will X win -> No] holds`. The Yes
   and No outcomes of one binary question are complementary.
5. **Conditional claim (judge under the named interpretation).** Claim: `Under the interpretations incl_tie_one: if
   [Up -> Yes] holds then [Above 100 -> Yes] holds`. Judge the implication only under the interpretation that a flat
   outcome resolves to the upper side. Do not judge it under the other interpretations.
6. **Missing definition (unjudgeable).** The claim relies on how a count is measured, and the sources never define it.
   Rationale: "the sources do not define what counts as a tweet".

## 4. Packets, double annotation and adjudication

- One packet per annotator: `packets/<annotator>.jsonl` (or `.csv`, same content). Packets are shuffled.
- Annotator names are 1-64 letters, digits, `-` or `_`. The name `adjudicator` is reserved. Other names are refused,
  so a name cannot write outside `packets/`.
- A fraction (`--overlap`, default 0.2) of assertions goes to two annotators, for agreement checks. Each annotator
  judges every assertion in their own packet independently; do not discuss items with each other.
- An item assigned to two annotators with only one answer returned stays **pending** until the second answer or an
  adjudicator answer arrives. Disagreements are resolved by an adjudicator file (`annotator: "adjudicator"`) with a
  rationale. A pending item blocks estimation.
- Annotation file, one JSON object per line:

```json
{"assertion_id": "<id from the packet>", "verdict": "correct|incorrect|unjudgeable", "rationale": "...", "annotator": "<your id>"}
```

  `rationale` is required and non-empty for `incorrect` and `unjudgeable`; optional for `correct`. The schema is
  written to `adjudication_schema.json` in the export directory.

## 5. Coordinator workflow

Run from the repository root with `PYTHONPATH=python CUDA_VISIBLE_DEVICES=`.

```text
python -m mentat_bench.adjudication frame    --submission <dir with bindings.jsonl theory.json provenance.jsonl> --corpus <corpus.jsonl> --out <frame_dir>
python -m mentat_bench.adjudication sample   --frame <frame_dir>/frame.jsonl --design units --n 200 --seed <int> --out <sample_dir>
python -m mentat_bench.adjudication export   --frame <frame_dir>/frame.jsonl --sample-dir <sample_dir> --annotators a,b,c --overlap 0.2 --seed <int> --out <export_dir>
python -m mentat_bench.adjudication import   --export <export_dir> --annotations a.jsonl b.jsonl c.jsonl --adjudication adj.jsonl --out <resolved_dir>
python -m mentat_bench.adjudication estimate --sample-dir <sample_dir> --resolved <resolved_dir>/resolved.jsonl --out <estimate.json>
```

- `frame` writes `frame.jsonl` (one row per distinct assertion, with source texts, interpretation scope, cluster id and
  tercile) and `frame_summary.json` (counts of every exclusion).
- `frame_summary.json` reports `unresolved_bindings`: outcomes whose status is unresolved make no claim, so they are
  not rows. Their missing codes stay in the submission and the count is reported beside the registry.
- `sample` requires `--design units` or `--design clusters`; the example above uses `units`.
- `export` writes `assignments.jsonl`. This file is for the coordinator only. Do not send it to annotators.
- `estimate` refuses to run while any sampled assertion lacks a final verdict.

## 6. What the frame counts

The **registry** has one row per distinct assertion. Its key is the canonical clause set together with the
interpretation scope (null = every interpretation). Two constraints merge only when both their clauses and their scope
are equal, so a conditional and an unconditional claim with the same clauses stay apart.

These never enter the denominator:

- **identity bindings**: an outcome bound to its own class representative with no negation;
- **definitions**: an unconditional constraint over exactly two outcomes of a market that has exactly two outcomes,
  whose canonical form is "exactly one of the two"; and the binding that makes an outcome the binary complement of the
  other outcome of that same two-outcome market (both bound to the same class with opposite negation). A binding that
  links outcomes of different markets is a claim and stays;
- **supplied-axiom restatements**: an unconditional single-market constraint that cites a supplied axiom and cites no
  source beyond its own market. A market's own sources are its question, its outcome labels, its event title and the
  event titles its bindings cite. A constraint that cites the event title of another market or event is a claim and
  stays in the registry;
- **tautologies**: constraints that are true in every world, whatever their provenance. A constant contradiction stays;
- **duplicates**: a canonical form repeated by several constraints becomes one row listing all of them. Two
  implications that form an equivalence (same scope) are merged into one iff row.

Strata: bindings; pairwise (at most two literals, no cardinality); group (wider clauses or cardinality). Each
constraint stratum has a conditional twin (`pairwise|cond`, `group|cond`) for rows with an interpretation scope.
Clusters are the connected components of outcomes linked by rows; terciles split clusters by size.

## 7. Sampling and estimation

- **Units design** (`--design units`): stratified simple random sampling without replacement over strata
  stratum x cluster-size tercile, with proportional allocation n_h. A stratum receives at least two units when the
  budget allows, so its variance is estimable. Each sampled assertion records `pi = n_h / N_h`.
- **Clusters design** (`--design clusters`): sample clusters SRS within tercile strata, then optionally `--within k`
  rows per sampled cluster. Each assertion records `pi = (m_h / M_h) * (k_c / M_c)`.
- **Estimator**: Hajek ratio of Horvitz-Thompson totals over judged assertions (`w = 1/pi`). Unjudgeable assertions
  are outside the denominator and appear only in the sensitivity bounds.
- **Domains**: combined, bindings, constraints, pairwise, group, conditional; plus each stratum.
- **Variance**: linearisation of the ratio, summed per PSU (assertion for units, cluster for clusters) within strata,
  with finite-population correction `(1 - m_h / M_h)`. A stratum with one sampled PSU that holds judged rows of the
  domain gives an undefined variance, reported as `n/a`.
- **Intervals**: design-based linearised normal interval; exact Clopper-Pearson on the unweighted counts (scipy when
  importable, otherwise a pure-Python incomplete beta); Wilson with the design effect (`n_eff = n / deff`).
- **Unjudgeable sensitivity**: precision with all unjudgeable treated as incorrect and as correct.
- **Zero observed errors** print no precision. The output states the number judged and a one-sided 95%
  Clopper-Pearson upper bound on the error rate, labelled **sample-only**: it treats the judged assertions as a simple
  random sample and is not a design-based bound. Zero observed errors do not establish perfect precision.

## 8. Known limits

- Canonicalisation handles `ref`, `not`, `and`, `or`, `implies`, `iff`, `atmost`, `atleast`, `exactly`. `wsum` and
  CNFs over 512 clauses are hashed as opaque atoms; they are deduplicated but not decomposed. Fixtures contain none.
- CNF cleanup removes tautologies, propagates units and removes subsumed clauses. It does not regroup pairwise
  at-most-one cliques stated as separate constraints into one cardinality row.
- The units design treats sampled assertions as independent PSUs. Use the clusters design when clusters are large
  (toy10k has 48 clusters of about 1,300 rows each).
- Clusters are connected components, so a densely linked fixture can form one large cluster.
- The supplied-axiom rule depends on the bindings' evidence to know which event titles belong to a market. A market
  whose bindings omit its event title is treated as citing a foreign source, so its restatement stays in the registry.

# MentatBench v0.1.1 (analysis release)

MentatBench evaluates whether a model can turn a corpus of propositions into a logically faithful representation and answer new global questions from it. This checkout contains the benchmark software, the Mentat-Hard fixtures, and a public results bundle.

## Results

The published panel is the **Mentat-Hard test set, revision 3.1**. Its headline is the mean of relation MCC and group MCC; Q_N is an end-to-end diagnostic, not the model ranking. See the [results overview](results/README.md), [leaderboard JSON](results/v0.1.0/leaderboard.json), and [leaderboard CSV](results/v0.1.0/leaderboard.csv).

The `v0.1.0` label identifies this public software and results bundle, exported from a private development repository whose commits are not published. The measurements were run on the Mentat-Hard test set, revision 3.1 (development round 3); this publication relabels those results and is not a new measurement.

The `v0.1.1` label identifies the analysis release. It analyses the same `v0.1.0` measurements and changes no score; the analysis is in [results/README.md](results/README.md).

### What the results support

Three tiers. Each claim is backed by the results in [results/README.md](results/README.md).

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

## Build from source

Use Python 3.11 or newer (CI uses Python 3.12) and Rust. CI targets Linux; a macOS smoke check was run by the maintainer (it is not part of CI). Windows has not been validated.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cargo build --locked --release
```

The Rust build creates `target/release/mb-engine`. Wire formats and runtime behavior are documented in [docs/ir.md](docs/ir.md), and local model processes in [docs/local_protocol.md](docs/local_protocol.md).

## Check the harness

Run the CPU gold-backed diagnostic on the dev fixture:

```bash
MB_ENGINE="$PWD/target/release/mb-engine" CUDA_VISIBLE_DEVICES= \
  python -m mentat_bench.runcell --fixture fixtures/mentat_hard_dev \
  --candidates fixtures/mentat_hard_dev/candidates.jsonl --adapter gold_view \
  --profile profiles/mentat-hard-v1.json --out runs/gold-view-dev
```

This diagnostic should produce Q_N 100 and a headline of 1.000. `gold_view` reads gold labels, so this is an evaluator ceiling check, not a model result. The public test split also includes its gold; only aggregate results are published for the private split.

To connect an existing model server, pin it and run `systemone_child conform <tag>`; see [docs/outside_models.md](docs/outside_models.md) and [docs/local_protocol.md](docs/local_protocol.md). Do not tune on the test split.

## Tests

Install `pytest` in the venv if it is not already available:

```bash
python -m pip install pytest
```

With the engine built, run the complete suite in offline mode:

```bash
MB_RUN_SLOW=1 MB_REQUIRE_ENGINE=1 MB_ENGINE="$PWD/target/release/mb-engine" \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -m pytest -q
```

CI on pull requests runs only the Rust tests and the fast suite (no `MB_RUN_SLOW`); run the complete suite above locally before a release.

Real-model checks are opt-in and require cached weights. Optional checks that need model libraries or SciPy skip when those extras are unavailable; the offline flags prevent model downloads.

## License

MentatBench is MIT licensed ([LICENSE](LICENSE)). Vendored dependencies keep their upstream licenses; see [vendor/PATCH.md](vendor/PATCH.md) and the license files under `vendor/`.

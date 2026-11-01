# MentatBench v0.1.0

MentatBench evaluates whether a model can turn a corpus of propositions into a logically faithful representation and answer new global questions from it. This checkout contains the benchmark software, the Mentat-Hard fixtures, and a public results bundle.

## Results

The published panel is the **Mentat-Hard test set, revision 3.1**. Its headline is the mean of relation MCC and group MCC; Q_N is an end-to-end diagnostic, not the model ranking. See the [results overview](results/README.md), [leaderboard JSON](results/v0.1.0/leaderboard.json), and [leaderboard CSV](results/v0.1.0/leaderboard.csv).

The `v0.1.0` label identifies this public software and results bundle, exported from a private development repository whose commits are not published. The measurements were run on the Mentat-Hard test set, revision 3.1 (development round 3); this publication relabels those results and is not a new measurement.

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

Real-model checks are opt-in and require cached weights. Optional checks that need model libraries or SciPy skip when those extras are unavailable; the offline flags prevent model downloads.

## License

MentatBench is MIT licensed ([LICENSE](LICENSE)). Vendored dependencies keep their upstream licenses; see [vendor/PATCH.md](vendor/PATCH.md) and the license files under `vendor/`.

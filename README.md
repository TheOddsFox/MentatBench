# MentatBench v0.2.0 (latest release; v0.1.x record kept)

MentatBench evaluates whether a model can turn a corpus of propositions into a logically faithful representation and answer new global questions from it. This checkout contains the benchmark software, the Mentat-Hard fixtures, and a public results bundle.

## Results

**v0.2.0** (the latest release) re-measures the **Mentat-Hard test set, revision 3.1** on Ollama 0.40.13: the 15 pre-registered models, two Kev models added after pre-registration (`kev_4b-q8_0` and `kev_0.8b-q8_0`, marked not pre-registered), and the baselines. The headline is the mean of relation MCC and group MCC; Q_N is a diagnostic. D3 27B ranks first, with a headline of 0.521 (95% interval 0.500 to 0.545; its rank interval is 1 to 1), and all 16 of its headline pairs are resolved in its favour after Holm correction. 117 of the 136 headline pairs among the 17 models are resolved. This is an owner re-measurement on the same machine, not an independent reproduction. The scores are scores on this benchmark's relation and group decisions, not general capability.

Repeats: a run-to-run repeat of each model's headline run gives identical answers for 14 of 17 models and a byte-identical decisions file for 13; the largest headline change is 0.0003. Moving from Ollama 0.40.11 (v0.1.0) to 0.40.13, the headline of the 15 pre-registered models moves by a median of 0.0009 and at most 0.0044. Full tables and the run-to-run and version-shift details are in [results/v0.2.0/README.md](results/v0.2.0/README.md), with [leaderboard JSON](results/v0.2.0/leaderboard.json) and [analysis JSON](results/v0.2.0/analysis.json).

### v0.1.x results (frozen record)

The published panel is the **Mentat-Hard test set, revision 3.1**. Its headline is the mean of relation MCC and group MCC; Q_N is an end-to-end diagnostic, not the model ranking. See the [results overview](results/README.md), [leaderboard JSON](results/v0.1.0/leaderboard.json), and [leaderboard CSV](results/v0.1.0/leaderboard.csv).

The `v0.1.0` label identifies this public software and results bundle, exported from a private development repository whose commits are not published. The measurements were run on the Mentat-Hard test set, revision 3.1 (development round 3); this publication relabels those results and is not a new measurement.

The `v0.1.1` label identifies the analysis release. It analyses the same `v0.1.0` measurements and changes no score; the analysis is in [results/README.md](results/README.md).

### What the v0.1.x results support

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

Use Python 3.11 or newer (CI uses Python 3.12) and Rust. CI targets Linux; a macOS smoke check was run by the maintainer (it is not part of CI). Windows 11 was checked once by the owner, on the owner's machine, as a fresh clone (Python 3.11.9, Rust 1.99.1). That is not an independent check; the record is in [docs/reproductions/2026-11-03-fresh-clone.md](docs/reproductions/2026-11-03-fresh-clone.md).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cargo build --locked --release
```

On Windows, activate the venv with `.venv\Scripts\Activate.ps1` (PowerShell), `.venv\Scripts\activate.bat` (cmd) or `source .venv/Scripts/activate` (Git Bash). Or skip activation and run `.venv\Scripts\python -m pip install -e .` directly. The Rust build creates `target/release/mb-engine` (`mb-engine.exe` on Windows). Wire formats and runtime behavior are documented in [docs/ir.md](docs/ir.md), and local model processes in [docs/local_protocol.md](docs/local_protocol.md).

## Check the harness

Run the CPU gold-backed diagnostic on the dev fixture:

```bash
MB_ENGINE="$PWD/target/release/mb-engine" CUDA_VISIBLE_DEVICES= \
  python -m mentat_bench.runcell --fixture fixtures/mentat_hard_dev \
  --candidates fixtures/mentat_hard_dev/candidates.jsonl --adapter gold_view \
  --profile profiles/mentat-hard-v1.json --out runs/gold-view-dev
```

In PowerShell:

```powershell
$env:MB_ENGINE = "$PWD\target\release\mb-engine.exe"; $env:CUDA_VISIBLE_DEVICES = ""
python -m mentat_bench.runcell --fixture fixtures\mentat_hard_dev --candidates fixtures\mentat_hard_dev\candidates.jsonl --adapter gold_view --profile profiles\mentat-hard-v1.json --out runs\gold-view-dev
```

This diagnostic should produce Q_N 100 and a headline of 1.000. `gold_view` reads gold labels, so this is an evaluator ceiling check, not a model result. The public test split also includes its gold; only aggregate results are published for the private split.

To connect an existing model server, pin it and run `systemone_child conform <tag>`; see [docs/outside_models.md](docs/outside_models.md) and [docs/local_protocol.md](docs/local_protocol.md). Do not tune on the test split.

## Reproduce a published result

Two offline checks read only committed files:

```bash
python results/v0.1.1/build_bundle.py build --check
python tools/eval/release_analysis.py --check
```

The v0.2.0 release has the same kind of offline checks, on its committed files:

```bash
python results/v0.2.0/build_bundle.py build --check
python tools/eval/release_analysis_v020.py --check
```

`--release` names a release in [`results/index.json`](results/index.json). The release must hold the cell, or carry it (`record_from`, for baseline cells only) or base it (`based_on`). The default is `v0.1.0`. It is fixed rather than `latest`, so an unflagged command keeps its meaning when a release is added. The v0.2.0 model cells are in `results/v0.2.0/cells/`. Its seven baselines are carried from v0.1.0, so `--release v0.2.0` compares them with the v0.1.0 measurement; the report's `measured_in` names the release that holds it. How scores are compared is in [docs/comparability.md](docs/comparability.md).

The first rebuilds the v0.1.1 leaderboard and CSV, and their index while v0.1.1 is the latest release (from v0.2.0 on, `results/index.json` belongs to the v0.2.0 builder, whose `build --check` checks it), and exits 1 on any byte difference (under a second). The second recomputes the analysis and exits 1 on any difference from the committed files. It takes about 12 minutes on the maintainer's machine.

Two quick checks:

- `lexical heuristic` (v0.1.0) needs no model server. Both verdicts are exact; it took 8 seconds on the Windows check.
- `d1-omni_600m-q8_0 laya_latest --release v0.2.0` needs the two Ollama models below. Both verdicts are exact, with 0 flips of 10,716 decisions. Laya abstains on over-length prompts, as its published run does. It took 10 minutes 53 seconds on the Windows check.

Prerequisites for the model cells:

- **Ollama:** version 0.40.13 for the v0.2.0 model cells, and 0.40.11 for the v0.1.x model cells (`ollama --version`). Other versions are reported by the tool, not checked, and can change scores (see the caveats below).
- **Model tags:** pull each tag from the public registry, for example `ollama pull d1-omni:600m-q8_0` and `ollama pull laya:latest`. The tags, digests and sizes are in [`children/ollama_models.json`](python/mentat_bench/children/ollama_models.json).
- **Disk and GPU:** the two models above are 1.7 GB together. `d3:27b-q8_0` (cell `d3_27b-q8_0`) is 29.5 GB and took about 70 minutes, by the owner's estimate. The published runs used two 48 GB NVIDIA RTX A6000 GPUs, one Ollama server per GPU, on CUDA. Other hardware, and CPU only, has not been checked.

To re-run published cells, build the engine and point `MB_ENGINE` at it. Set `MB_ENGINE` in the shell that runs the tool. A model cell also needs its Ollama server running with the published model tag. A POSIX shell (Linux, macOS):

```bash
export MB_ENGINE="$PWD/target/release/mb-engine"
python tools/reproduce.py run lexical heuristic --out-root runs/reproduce
```

On Windows the engine is `mb-engine.exe`, in Git Bash too: `export MB_ENGINE="$PWD/target/release/mb-engine.exe"`. In PowerShell:

```powershell
$env:MB_ENGINE = "$PWD\target\release\mb-engine.exe"
python tools/reproduce.py run lexical heuristic --out-root runs\reproduce
```

If `MB_ENGINE` does not name an existing file, the harness skips engine confirmation with a warning, but the evaluate step needs the engine: the cell ends `incomplete` (failed step `evaluate`), and `run` exits 1.

`run` re-runs each cell with the flags recorded in its published cell (`results/<release>/cells/<id>.json`, or the release that holds it), into a new `<out-root>/<id>`, and then compares the run with the published predictions. Before anything runs, it refuses a duplicate id, a model cell without its model tag, a child interpreter it cannot find, and a directory that is not empty (`<id>`, `<id>.meta` or `<id>.reproduce`). A published cell records its child interpreter as `python.exe`; the run uses the Python that runs the tool instead. `--url` names the Ollama server (default `http://127.0.0.1:11434`). A model whose digest differs from the pinned one is refused by the child, and this tool cannot override that.

To compare a run that already exists, use `compare`:

```bash
python tools/reproduce.py compare runs/reproduce/d3_27b-q8_0 --id d3_27b-q8_0
```

Each comparison writes `<run>.reproduce/report.json` and `<run>.reproduce/predictions.jsonl.gz`, and rewrites them if they exist. It prints one line. The report gives the headline, relation, group, binding and reading MCC (published and reproduced), the decision flips overall and per operation, each check with its value and limit, the largest probability difference, and the environment. The verdicts are:

- **exact**: the decompressed predictions are byte-identical to the published ones, and no run-defining fact differs. The gzip container is compared separately and reported.
- **within tolerance**: not identical, but every check passes. The headline, relation and group MCC are within 0.001 of the published values; the reading and binding MCC within 0.01. At most 0.5% of all decisions changed answer, and no operation has more flips than the larger of 0.5% of its items and two flips. No probability differs by more than 0.02. The published headline matches the one rescored from the published predictions.
- **differs**: a check fails. The report names each failing check. A difference in a run-defining fact always gives differs, unless the run is incomplete: build flags, adapter, child arguments, fixture path and files, profile path and hash, model digest and prompt sha256.
- **incomplete**: the run's cell, score or build policy is missing, unreadable or not complete, or its decisions cannot be read.

The Ollama version, the engine sha256, the package versions and the GPUs are reported, not checked. Package versions are compared only where both runs record the package; one-sided packages are listed separately.

The limits were chosen by the owner from the committed repeats of `kev_9b-bf16` on CUDA (`results/v0.1.1/repeats/`), compared with the published run. Those repeats moved the headline by 0.00038 at most, the relation MCC by 0.00077, and the reading MCC by 0.0049 (two flips out of 368 items). They flipped 0.14% of decisions, and moved one probability by 0.011. The q8 repeats of `d3_4b` and `d3_9b` were byte-identical. The Vulkan diagnostic of `d3_9b` is rejected as `differs`. The binding limit is 0.01 because one flip on a near-zero binding MCC moves it by about 0.003, and the CUDA repeats had no binding flip to measure.

`run` exits 0 only when every verdict is exact or within tolerance. Q_N is reported but does not decide the verdict.

Caveats:

- A re-run needs the same model digests as the published run. The local child refuses a tag whose digest differs from [`children/ollama_models.json`](python/mentat_bench/children/ollama_models.json).
- The Ollama version and the GPU backend can change scores. The v0.1.x runs used Ollama 0.40.11 and the v0.2.0 runs used 0.40.13, both on CUDA. Moving from 0.40.11 to 0.40.13 moved the headline of the 15 pre-registered models by at most 0.0044. A Vulkan run of one v0.1.x model moved its headline by 0.004.
- Q_N may differ. The published engine binary was not rebuilt, so its sha256 differs from a fresh build; the environment section of the report lists it.
- The `hf` and `embedding` baselines need their model weights in the local cache. A cell whose build fails is incomplete, and `run` says so.
- The comparison reuses the publication's own rescoring code (`results/v0.1.0/build_bundle.py`), and the owner chose the limits. It is a consistency check against the published run, not an independent verification. A re-run by the owner is not an independent reproduction either.

## Independent reproduction

An independent reproduction is a check run by someone other than the owner, on a machine, accounts and Ollama install that the owner did not set up or use. A re-run by the owner, on any machine, is not one. Neither is the [fresh-clone check of 2026-11-03](docs/reproductions/2026-11-03-fresh-clone.md), which ran on the owner's machine.

To run a check and report it:

1. Clone the repository into an empty folder, set up the venv and build the engine as under [Build from source](#build-from-source).
2. Install Ollama at the version named under the prerequisites, and pull the model tags.
3. Run a check, for example `python tools/reproduce.py run lexical heuristic --out-root runs/reproduce`, or `python tools/reproduce.py run d1-omni_600m-q8_0 laya_latest --release v0.2.0 --out-root runs/reproduce` for the model cells.
4. Open an issue at [github.com/TheOddsFox/MentatBench/issues](https://github.com/TheOddsFox/MentatBench/issues) with:
   - the printed line of each cell, with its verdict and numbers;
   - `<out-root>/<id>.reproduce/report.json` for each cell (it holds no local paths);
   - your environment: operating system, Python, Rust and Ollama versions, GPU and driver if any, the repository commit (`git rev-parse HEAD`), and the kind of machine and account it ran on.

Report the verdict the tool prints, including `differs` and `incomplete`. Each is a result.

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

In PowerShell:

```powershell
$env:MB_RUN_SLOW = "1"; $env:MB_REQUIRE_ENGINE = "1"; $env:MB_ENGINE = "$PWD\target\release\mb-engine.exe"; $env:HF_HUB_OFFLINE = "1"; $env:TRANSFORMERS_OFFLINE = "1"
python -m pytest -q
```

CI on pull requests runs only the Rust tests and the fast suite (no `MB_RUN_SLOW`); run the complete suite above locally before a release.

Real-model checks are opt-in and require cached weights. Optional checks that need model libraries or SciPy skip when those extras are unavailable; the offline flags prevent model downloads.

## License

MentatBench is MIT licensed ([LICENSE](LICENSE)). Vendored dependencies keep their upstream licenses; see [vendor/PATCH.md](vendor/PATCH.md) and the license files under `vendor/`.

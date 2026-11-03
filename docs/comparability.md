# Comparability

This page says when two scores can be compared: two runs of one model, a run and a published cell, or two releases. The facts it names are recorded in each cell (`results/<release>/cells/<id>.json`), in its run card (`release/<release>/runcards/<id>.json`) and in the fixture registry (`fixtures/VERSIONS.json`).

## Facts that must match

A difference in a fact marked "yes" makes `tools/reproduce.py compare` return `differs`, unless the run is incomplete: the run is not the published run.

| Fact | Where it is recorded | Checked by `tools/reproduce.py` |
|---|---|---|
| Fixture: `mentat_hard_test`, version 3 (called revision 3.1 in the results), content hash `7b408459…` | `fixture_stamp` in the cell; `fixtures/VERSIONS.json` | yes: the fixture path and the hash of each file |
| Fixture file hashes | `runtime.fixture.files`. For version 3 all 15 files match the registry | yes (`fixture_files`) |
| System One child: the child module and its arguments | `local_arg` in the cell | yes (`child_arguments`) |
| System One child version (`7` in all 17 v0.2.0 model cards) | `build_policy.model.child_version` in the run card | no |
| Prompt | `build_policy.model.prompt_sha256` | yes |
| Profile and its sha256 | `profile` (`profiles/mentat-hard-v1.json`); `runtime.profile_sha256` (`d8804e81…`, the committed file) | yes (`profile_path`, `profile_sha256`) |
| Model digest | `build_policy.model.revision`; the pins are in [`children/ollama_models.json`](../python/mentat_bench/children/ollama_models.json) | yes (`model_digest`) |
| Adapter and build flags | `adapter`, `build_flags` | yes |
| Items: the same 10,716 decisions and labels | the predictions file | yes (`same_items`) |
| Scoring code | `runtime.repo.commit` of the run; the rescoring code is [`results/v0.1.0/build_bundle.py`](../results/v0.1.0/build_bundle.py) with [`decision_scoring.py`](../python/mentat_bench/decision_scoring.py) | no: the tool rescores both sides with the publication's code |

## Facts reported, not matched

- **Ollama version** (`build_policy.model.ollama_version`). The v0.2.0 analysis measured the shift from Ollama 0.40.11 (v0.1.0) to 0.40.13 (v0.2.0) with the same model digests. For the 15 pre-registered models the headline moved by a median of 0.000874 and at most 0.004429. Up to 2.6% of the decisions changed answer. Source: `robustness.summary.version_shift` and `robustness.models` in [`results/v0.2.0/analysis.json`](../results/v0.2.0/analysis.json).

  The shift cannot reverse the point gap of any of the 89 resolved headline pairs among the 15 models measured on both Ollama versions. The smallest headline gap among those 89 pairs is 0.026562 (`nimble-v3_9b-q8_0` over `clef-flash_9b`, in `pairwise.headline`). The other 28 resolved headline pairs involve `kev_4b-q8_0` or `kev_0.8b-q8_0`, which have no v0.1.0 run, so they have no version shift and no bound here. That is about six times the largest single shift. Two models moving by 0.004429 in opposite directions change a gap by at most 0.008858, which is less than 0.026562. The version shift is reported for the headline only, so no bound is claimed for the relation or group MCC. The analysis does not rerun the pair tests on the v0.1.0 numbers.

- **GPU backend.** The GPU backend is not recorded in the cells or run cards. Only the GPUs and the CUDA settings are reported (`runtime.gpus`, `runtime.cuda`). The v0.2.0 runs used CUDA on two NVIDIA RTX A6000 GPUs. A Vulkan run of one v0.1.x model (`d3:9b`) moved its headline by ±0.0040 ([`results/README.md`](../results/README.md), backend).
- **Package versions** (`runtime.versions`). Compared where both runs record the package. A package recorded on one side only is listed apart.
- **Engine binary** (`runtime.engine.sha256`). The published engine cannot be rebuilt byte for byte from the source. Only Q_N and repairs depend on the engine; the headline is scored from the decisions ([`release/v0.2.0/record/README.md`](../release/v0.2.0/record/README.md), item 9).
- **GPUs, drivers and CUDA settings** (`runtime.gpus`, `runtime.cuda`). Reported, not decisive.
- **Q_N.** A diagnostic. It is reported and does not decide a verdict.

## Release types

- **New measurement** (`record_from` in [`results/index.json`](../results/index.json)). The models are measured again on a stated setup, and the release record lists each deviation. v0.2.0 measured on Ollama 0.40.13, not the pre-registered 0.40.11 ([`release/v0.2.0/record/README.md`](../release/v0.2.0/record/README.md), item 1). A new measurement may carry baselines from its `record_from` release when their predictions reproduce exactly (empty and oracle have none, so their scores must match). v0.2.0 carries all seven baselines from v0.1.0 (item 11). Its scores are compared with the earlier release only through the facts above.
- **Analysis release** (`based_on`). It analyses the measurements of its base release and changes no score, prediction or leaderboard row. v0.1.1 is one, based on v0.1.0. It may add unranked exploratory material outside the leaderboard, such as per-model accuracy profiles or a reasoning reference.
- **Correction.** A fixture fault is corrected by registering a new fixture version. The saved answers of each run are re-scored under it, and no run is rebuilt for a gold correction ([`docs/corrections.md`](corrections.md), sections 3 and 5). A correction changes the scores it corrects.

## Unranked material

Unranked material, such as per-model accuracy profiles or a reasoning reference, is outside the System One scope. [SPEC.md](../SPEC.md), section 1, scopes the revision to System One models under the fixed harness. Unranked material has no leaderboard row and no rank. Its numbers are not on the leaderboard and are not ranked against it.

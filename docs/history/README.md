# History of MentatBench records

This folder keeps the records of earlier development rounds and superseded spec revisions, byte for byte. Each file was moved
here with `git mv` and its sha256 is in [RENAMES.json](RENAMES.json), which maps every old path to its new one. The current
release is **v0.1.0**; its governing records are in [release/v0.1.0/record/](../../release/v0.1.0/record/README.md).

One row per record. A row may cover several files of one record, and its paths list them. The columns are the round or kind, the
date, what the record is, whether it is frozen (its bytes are unchanged since the original commit), and the path it had before the
move. Paths are relative to the repository root.

| Round or kind | Date | What it is | Frozen | Original path |
|---|---|---|---|---|
| round 1 | 2026-10-28 | Results report (`round-1/report.md`), three-part MCC mean headline | yes | `docs/results/mentat-hard-20261028.md` |
| round 1 | 2026-10-28 | Results JSON (`round-1/results.json`) | yes | `docs/results/mentat-hard-20261028.json` |
| round 1 | 2026-10-28 | League bootstrap CI (`round-1/ci.json`) | yes | `docs/results/mentat-hard-20261028-ci.json` |
| round 1 | 2026-10-27 | Pre-registration (`round-1/prereg.md`, `round-1/prereg.json`) | yes | `docs/results/mentat-hard-prereg-20261028.md`, `docs/results/mentat-hard-prereg-20261028.json` |
| round 2 | 2026-10-29 | Results report (`round-2/report.md`), relation MCC headline (changed post-hoc) | yes | `docs/results/mentat-hard-v2-20261029.md` |
| round 2 | 2026-10-29 | Results JSON (`round-2/results.json`) | yes | `docs/results/mentat-hard-v2-20261029.json` |
| round 2 | 2026-10-29 | League bootstrap CI (`round-2/ci.json`) | yes | `docs/results/mentat-hard-v2-20261029-ci.json` |
| round 2 | 2026-10-29 | Private-split aggregates (`round-2/private.md`, `round-2/private-ci.json`) | yes | `docs/results/mentat-hard-v2-private-20261029.md`, `docs/results/mentat-hard-v2-private-20261029-ci.json` |
| round 2 | 2026-10-29 | Pre-registration (`round-2/prereg.md`, `round-2/prereg.json`) | yes | `docs/results/mentat-hard-v2-prereg-20261029.md`, `docs/results/mentat-hard-v2-prereg-20261029.json` |
| round 2 | 2026-10-29 | Validity audit and the disclosure of the post-hoc headline change (`round-2/headline-change-disclosure.md`) | yes | `docs/results/mentat-hard-v2-validity-audit-20261029.md` |
| round 2 | 2026-10-29 | Answerability audit: sample, key, scripts and judge answers (`round-2-answerability-audit/`) | yes | `docs/results/mentat-hard-v2-audit/` |
| round 2 | 2026-10-29 | Release manifest and gate output (`round-2/release_manifest.json`, `round-2/check_gates.md`) | yes | `release/release_manifest_v0.2.json`, `release/check_gates_v0.2.md` |
| round 2 | 2026-10-29 | Run cards of the 14 round-2 cells (`round-2/runcards/`) | yes | `release/runcards/` |
| round 2 | 2026-10-29 | Baseline table (`round-2/baselines/baselines.json`) | yes | `release/baselines/` |
| spec | 2026-10-18 | Specification, first revision (`specs/spec-2026-10-18.md`) | yes | `mentat-bench-v0.1.md` |
| spec | 2026-10-28 (last change) | Wire-format contract between the Rust engine and the Python harness, pinned by the v0.1.0 manifest. The frozen copy (`specs/ir-2026-10-28.md`) was made by `git mv`; a new current `docs/ir.md` was written at the old path, with its spec citation pointing to `SPEC.md`. The frozen copy keeps the sha256 that the manifest pins. | yes | `docs/ir.md` |
| spec | 2026-10-27 | Specification, second revision, pinned by the v0.1.0 manifest (`specs/spec-2026-10-27.md`); the current copy is `SPEC.md` | yes | `mentat-bench-v0.2.md` |
| notes | 2026-10-27 | System One development notes (`notes/systemone-dev-notes-20261027.md`, `.json`) | yes | `docs/results/systemone-dev-notes-20261027.md`, `docs/results/systemone-dev-notes-20261027.json` |
| round 3 (governs v0.1.0) | 2026-10-30 | Pre-registration of the round-3 test matrix, frozen before any test run (`release/v0.1.0/record/prereg.md`, `prereg.json`) | yes | `docs/results/mentat-hard-v3-prereg-20261030.md`, `docs/results/mentat-hard-v3-prereg-20261030.json` |
| round 3 (governs v0.1.0) | 2026-10-31 | Release manifest, gate output and attestations (`release/v0.1.0/record/`) | yes | `release/release_manifest_v0.3.json`, `release/check_gates_v0.3.md`, `release/v0.3/attestations.json` |
| round 3 | 2026-10-30 | Answerability audit of the round-3 questions (`docs/audits/test-set-groups/`) | yes | `docs/results/mentat-hard-v3-audit/` |
| round 3 | 2026-10-30 | Answerability audit of revision 3.1 of the test set (`docs/audits/test-set-relations/`) | yes | `docs/results/mentat-hard-v3.1-audit/` |
| round 3 | 2026-10-30 | Results report and JSON for the public test split (`docs/results/mentat-hard-20261030.md`, `.json`). The report's labels were changed and the JSON was regenerated; the numbers are unchanged. | no | `docs/results/mentat-hard-v3-20261030.md`, `docs/results/mentat-hard-v3-20261030.json` |
| round 3 | 2026-10-30 | League bootstrap CI for the public test split (`docs/results/mentat-hard-20261030-ci.json`). Renamed only: byte-identical, not regenerated. | yes | `docs/results/mentat-hard-v3-20261030-ci.json` |
| round 3 | 2026-10-30 | Private-split report and JSON (`docs/results/mentat-hard-private-20261030.md`, `.json`). The JSON's title was edited; the numbers are unchanged. | no | `docs/results/mentat-hard-v3-private-20261030.md`, `docs/results/mentat-hard-v3-private-20261030.json` |
| round 3 | 2026-10-30 | Private-split league bootstrap CI (`docs/results/mentat-hard-private-20261030-ci.json`). Renamed only: byte-identical. | yes | `docs/results/mentat-hard-v3-private-20261030-ci.json` |
| round 3 | 2026-10-30 | Script that recomputes the test results (`docs/results/mentat-hard-results/build_results.py`) | no (paths and labels edited) | `docs/results/mentat-hard-v3-results/build_results.py` |
| round 3 | 2026-10-31 | Results bundle: leaderboard, cells, predictions, runcards and baselines (`results/v0.1.0/`, `release/v0.1.0/runcards/`, `release/v0.1.0/baselines.json`) | predictions, runcards and baselines: yes; leaderboard and cells: no (regenerated) | `results/v0.3/`, `release/v0.3/runcards/`, `release/v0.3/baselines.json` |

## Glossary of the old labels

- **Mentat-Hard v1, v2, v3** are the development rounds 1, 2 and 3. Each round generated its own test material and ran its own
  matrix. The names are kept only in the frozen records, where they are the labels the records were published under.
- **v0.2** and **v0.3** were internal labels of rounds 2 and 3. They were never the names of a published release. The published
  release is **v0.1.0**, which contains the round-3 results.
- **v0.1** is the first specification (2026-10-18). **v0.2** in a spec or a gate output is the second specification (2026-10-27).
- **3.1** is the label of revision 3.1 of the Mentat-Hard test set (the `fixture.json` version of that test set). Its registry
  version is 3, and that number is kept in the registry, in file names such as `fixtures/manifests/mentat_hard_test_v3.json`, and in
  the manifest. The revision is not a release.
- **20261030-v31-test**, with its `-baselines`, `-hf` and `.log` siblings, was the name of the round-3 run directories. The directories
  were renamed locally on 2026-11-01 to `20261030-test*`, and the published files use the new names. No frozen record names the
  run directories; if one ever does, its former name is `20261030-v31-test*`.
- **measurement release** and **internal release** were informal names for the round-3 record. They are removed from the current
  files.

**Which records govern v0.1.0.** The round-3 pre-registration in [release/v0.1.0/record/](../../release/v0.1.0/record/README.md)
(`prereg.md`, `prereg.json`) governs the published v0.1.0 results. The round-2 records and the round-1 records do not: they are kept
for history, and their headlines differ from v0.1.0's.

Each superseded record is kept as it was published. Its headline differs from the current one, so its numbers are not comparable
with v0.1.0.

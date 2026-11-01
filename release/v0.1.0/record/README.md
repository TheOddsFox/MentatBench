# Record of v0.1.0: frozen files

The files in this folder govern the published v0.1.0 results. They were moved here from their original paths with `git mv`
and are byte-identical to those originals, so their sha256 values still match the pins in the files themselves. Do not edit
them. The mapping from every old path to its new one is [docs/history/RENAMES.json](../../../docs/history/RENAMES.json), and
the history of the rounds is in [docs/history/README.md](../../../docs/history/README.md).

| File | What it is | Original path |
|---|---|---|
| `prereg.md`, `prereg.json` | The round-3 pre-registration, frozen on 2026-10-30 before any test run. It fixes the matrix, the headline rule, the reference, the relation floors and the interval method. It governs the v0.1.0 results. | `docs/results/mentat-hard-v3-prereg-20261030.md`, `.json` |
| `release_manifest.json` | The release manifest, built at commit `defed6a`. It pins the spec, `docs/ir.md`, the engine, the profiles, the fixture files and manifests, the run cards, the baseline table, the attestations and the registry. | `release/release_manifest_v0.3.json` |
| `check_gates.md` | The release gate output of that manifest. | `release/check_gates_v0.3.md` |
| `attestations.json` | The owner's attestations for gates 6, 8 and 11. | `release/v0.3/attestations.json` |

The release manifest, the gate output and the attestations were not regenerated. The pinned engine binary and the sealed private
split are not available, so the gates could not be run again. Their contents are the originals.

## Old labels inside these files

These files were written under the earlier labels. Read them with this key.

- **v0.3** in a file name or a path is the internal label of the round-3 release. The release is **v0.1.0** now. The paths inside
  the manifest and the attestations still say `release/v0.3/` and `docs/results/mentat-hard-v3-...`. [RENAMES.json](../../../docs/history/RENAMES.json)
  maps each of them to its current file.
- **v3** in `prereg.md`, `release_manifest.json` and `check_gates.md` is the registry version of the fixtures (`fixtures/VERSIONS.json`,
  `fixtures/manifests/mentat_hard_{dev,test}_v3.json`). It is load-bearing, so it is not renamed.
- **"3.1"** is the `fixture.json` label of revision 3.1 of the Mentat-Hard test set (and of its dev set). It is not a release name.
- **"spec v0.2"** in the gate output means the specification of 2026-10-27, which is `docs/history/specs/spec-2026-10-27.md` (and its
  relabelled copy, `SPEC.md`). The results bundle reads the gate text with this phrase changed (see `results/v0.1.0/build_bundle.py`).
- **"Mentat-Hard v3"** in the pre-registration means development round 3.
- **recorded_at** is not a sign that the gates were run again. The results bundle carries `recorded_at: "defed6a, 2026-10-31"`
  next to the gate table so that the table never reads as a re-verification at a later commit.

`python -m mentat_bench.release verify` and `check-gates` (maintainer tooling, not included in the public repository) resolve each pin through [docs/history/RENAMES.json](../../../docs/history/RENAMES.json)
when its path has moved, and accept the file there only if it has the sha256 that RENAMES records. The bundle builder
(`results/v0.1.0/build_bundle.py`) reads the gate table from `check_gates.md` with the old paths mapped.

## Re-checking the gates on this tree

The commands below are maintainer tooling and are not included in the public repository. From the repository root, with `PYTHONPATH=python` and `MB_ENGINE` pointing at the engine:

    python -m mentat_bench.release check-gates --manifest release/v0.1.0/record/release_manifest.json

This reproduces the statuses of `check_gates.md` for gates 3, 4, 5, 6, 8, 9 and 11. The other statuses differ for these reasons,
and no record file is rewritten by the check:

- Gates 1 and 10 read NOT MET unless `target/release/mb-engine.exe` is the pinned binary (sha256 `2df1bd...`), because `verify`
  checks the engine pin. The local binary is not that binary.
- Gate 2 reads UNKNOWN unless the sealed split's directory is given:
  `--sealed-dir mentat_hard_private=<the owner's sealed directory>`. That directory is not in the repository.
- Gate 7 reads UNKNOWN unless the tree is clean and at the pinned commit `defed6a`, because the leakage test runs only there.
- The evidence text names `release/v0.3/attestations.json` for gates 6, 8 and 11, because the manifest pins that path. The file is
  `release/v0.1.0/record/attestations.json`.

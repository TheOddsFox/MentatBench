# Public correction process

Spec s10 requires a public process that versions faulty fixtures and applies a correction consistently to submitted
runs. This document is that process. The code is `python/mentat_bench/corrections.py`, the registry is
`fixtures/VERSIONS.json`, and the tests are `tests/test_corrections.py`.

## 1. Reporting a fault

A fault is a verified defect in a fixture's reference: a gold answer, a gold verdict, a query that is ill-formed or
mis-scoped, or a corpus row whose label contradicts its text. "Verified" means reproduced: the report names the
fixture version, the query ids, and the evidence (an `mb-engine check` or `eval` output, a brute-force counterexample,
or a provenance row that does not support the gold). An engine defect is not a fixture fault: it is reported and fixed
in the engine, and the fixtures it affects are re-scored as in section 5.

## 2. Triage

Each verified fault is one of: `gold` (reference answer, verdict, or gold file such as theory or bindings), `query`
(the query itself, or the order in which queries are generated), `corpus` (a row or its label), or `engine` (evaluator
behaviour). Only `gold`, `query` and `corpus` faults change a fixture. The change log records the class in its reason.

## 3. A fix is a new version, never an edit

A registered version is never edited in place: its content hash is its identity. A fix is written to a new directory
(for example by the generator, or from a reviewed patch), then registered as the next version of the same name. The
committed `fixtures/*` directories are not changed by a correction until its version is registered.

For a generated fixture such as `mentat_hard_dev`, a correction is generated into a new directory and registered as the next
version. The old registered bytes and their hash remain unchanged. A regeneration is never registered over an existing
version's hash: it is a new version, even before release, so earlier runs still identify the bytes they used.

## 4. Registering a version

```
python -m mentat_bench.corrections register --fixture <new dir> --name <name> --version <v> \
    --reason "<what was wrong and what changed>" --affected <qid> [--affected <qid> ...]
```

- The content hash covers EVERY regular file of the fixture directory (relative POSIX paths, sorted, each with its
  SHA-256). A directory that differs from a registered version in any file, adds one, or lacks one is not that version:
  `stamp_for` refuses it and names the nearest registered version and the files that differ.
- The previous `active` version of the name becomes `superseded` with `superseded_by` set to the new version.
- The new entry gets a change log entry `{date, reason, affected_queries}`. Affected queries must exist in the new version.
- The registry refuses a repeated version, and content that is already registered under another version.
- `register` refuses a fixture whose `queries.jsonl` has no rows (`... has no rows`), so a version always has queries to
  score.
- A name holds committed versions or generated versions (section 8), never both: `register` refuses a name that has
  generated versions (the retired exact-scale names among them), and `register --generator` refuses a name that has
  committed ones.
- Every registry entry is validated when the registry is read: an object with a `name`, `version`, `status` (one of
  the four values), `content_hash`, `hashes` (file to SHA-256), `change_log`, `superseded_by`, and a `generator`
  object where present. Any command refuses a malformed registry (`refused: registry is malformed: ...`, naming the
  entry and the field, exit 2) and writes nothing. A missing registry file is refused with "cannot read registry ...: the file
  is missing" by `list`, `register`, `register --generator`, `rescore`, `rescore --rerun-extra`, `report` and `runcell`.
  `regenerate` and `rehash` find no entry in a missing file and refuse with "is not registered". To start a registry, create `{"entries": []}`.

`rehash --fixture <dir> --name <name> --version <v> --reason "<why the hash scope changed>"` recomputes the hashes of an
existing version after the hash scope has changed (for example when a file kind was not covered before). It refuses
any change to a registered file, so it changes the scope only; the change log records it. It is for maintenance of
unreleased versions, not for corrections.

`python -m mentat_bench.corrections list` prints the registry (refused when the file is missing or malformed). Registry status values are `active`, `superseded`,
`withdrawn` and `retired`. The tool sets `active` and `superseded`; `withdrawn` (a version that must not be used) and
`retired` (a version kept as a record whose generator was removed, section 8) are set by a reviewed registry edit that
carries a change log entry and leaves the version's hashes unchanged.

## 5. Applying a correction to submitted runs

A run is never rebuilt for a gold correction. Its saved answers are re-scored:

```
python -m mentat_bench.corrections rescore --run <run dir> --fixture <new dir> --from <old dir> --profile <pinned profile> \
    [--queries-file <name> ...]
```

- `<run dir>` is the output of a cell or of `evaluate`: `answers.jsonl`, `verdicts.jsonl` as scored under the old version,
  and `cell.json`.
- The query files are the ones the run was evaluated on: `--queries-file` names (repeatable), else `cell.json`'s
  `query_files` (plain file names in the fixture directory), else `queries.jsonl` alone. A run with an extra pass lists
  `queries_extra.jsonl` in `query_files`; a run whose `extra/answers.jsonl` exists is re-scored for that file even when
  its `cell.json` (written before the list held the extra file) names only `queries.jsonl`. Files other than
  `queries_extra.jsonl` are scored from `answers.jsonl` together, as before. Names given with `--queries-file` are used
  as they are: `--queries-file queries.jsonl` leaves the run's extra pass (and `score_extra.json`) alone.
- The version the run recorded (`cell.json` `fixture_stamp`) is authoritative: `--from` must hold that version's files,
  or the rescore is refused. Without a recorded stamp, `--from` is checked against the registry alone.
- Both versions must be registered, and the new one must be `active`.
- Corpus and the query files used must be byte-identical in both versions. If one changed, the saved answers do not
  apply: the submission is re-built and re-evaluated, and that is a new run.
- `mb-engine check` runs against the corrected gold, so the certificate verdicts are the corrected ones. No model is run.
- The report `<run dir>/rescore_<name>_v<version>.json` holds both versions (`fixture.from`, `fixture.to`: name, version,
  status, content hash), the query files, the cap `max_certificate_refs` used, the old and new Q_N and family means, the
  delta, every query whose utility changed, and every query whose certificate verdict changed. Its old Q_N equals the
  run's own `score.json` Q_N when the run was scored on the same files.

The run's `score.json`, answers and verdicts are never overwritten, and a report is never replaced: a rescore writes a new
`rescore_<name>_v<version>.json` and refuses when that file already exists, so a run re-scored for several corrections has
one report per version. Its scratch files go to a new `rescore_work_*` directory in the run, removed afterwards; an
existing `rescore_work` directory is never touched. `score_extra.json` is the one file a rescore replaces (below).

A run with an extra pass (`runcell --extra-queries`: `extra/answers.jsonl`) is re-scored for its extra file too. The
extra file is scored under the new extra gold, and the report's `extra` block holds the old and new scores. The run's
`score_extra.json` is replaced by the re-score (its fixture stamp is the new version), and the run's own copy is kept as
`score_extra_<name>_v<old>.json`. If `queries_extra.jsonl` differs between the versions, the extra pass is refused in
the report (`extra.status` = refused, with the reason) and `score_extra.json` is left as it was; the main re-score still
applies. `report` marks an extra Q_N whose stamp is not the registry's active version as `stale`.

The extra pass is re-scored only when `cell.json` records `extra_status` = ok (a cell written before that field existed
counts as ok when its `score_extra.json` is there). Otherwise the extra block is refused with the reason. A missing or
unreadable `score_extra.json` is refused and never overwritten. A query file that is re-scored (`queries_extra.jsonl` included) with
no rows in either version refuses the whole rescore, as an empty main file does. `queries_extra.jsonl` named alone (without
`queries.jsonl`) is refused, because `answers.jsonl` covers only `queries.jsonl`; named together with `queries.jsonl` on
a run with no extra answers, it is dropped from the names the report lists. An engine failure during the extra re-score is
refused in the extra block, `score_extra.json` is left as it was, and the main re-score still completes. The report and
`score_extra.json` are written through a temp file and `os.replace`, so a reader never sees a partial file.

When a fixture correction changes `queries_extra.jsonl`, saved extra answers do not apply to the new rows. The extra pass
is refused in the rescore report (`extra.status` = refused) and its `score_extra.json` stays unchanged. The main query
file can still be re-scored if its corpus and query bytes match. A new run with `runcell --extra-queries` produces an
extra score for the corrected version.

`rescore ... --rerun-extra` (same `--run`, `--fixture`, `--from`, `--profile`; no `--queries-file`) produces it without a new
run. It re-runs only the extra pass: `evaluate` on the run's sealed `submission/` with the new version's
`queries_extra.jsonl`, then `score` against its `gold/answers_extra.jsonl`. The results go to `<run dir>/extra_<name>_v<version>/`
(`answers.jsonl`, `verdicts.jsonl`, `score_extra.json` stamped with the new version). Everything the call writes is inside
that directory: evaluate's temp dir is made in a sub-directory of it, and a failed call removes the directory it created.
The rules:

- The run must have a `cell.json` whose extra pass ended `ok` (`extra_status`; a cell without that field counts as ok when
  its `score_extra.json` is there). A failed or missing extra pass is refused: run the cell again with `--extra-queries`.
- An existing target directory is refused, and `--from` is checked against the recorded stamp. The new version must be
  `active`, and `corpus.jsonl` must be identical in both versions (the submission was built on it).
- `<name>` and `<version>` may use only `A-Z a-z 0-9 . _ -` (also checked by `register` and `rehash`).
- The new version's fixture name must be the name the run recorded in `cell.json` `fixture_stamp`; a cell with no
  `fixture_stamp` is refused, because its fixture name is not recorded.
- `--queries-file` cannot be combined with `--rerun-extra` (usage error, exit 2).
- A `queries_extra.jsonl` of the new version with no rows is refused (as in `rescore`).

The rerun output is named `extra_<name>_v<version>/` for the corrected fixture version.

`report` reads the newest re-run score of an active version in preference to `score_extra.json`: among the
`extra_<name>_v<version>/score_extra.json` files whose stamp is the registry's active version, the most recently written one
gives the extra Q_N. The row's `extra_from` names the file it came from (`extra_<name>_v<version>/score_extra.json`, or
`score_extra.json`). `extra_Q_N_recorded` is always the run's own `score_extra.json` Q_N, which can differ from the re-run's.
A re-run score whose version has since been superseded is not used, and `score_extra.json` is shown
(stale if it is not active either).

## 6. How results record the fixture version

A result is attributable to one fixture version by its stamp, `corrections.stamp_for(<fixture dir>)`:
`{name, version, status, content_hash}`. The run writes it into `cell.json` as `fixture_stamp`; rescore reads it there
and refuses an `--from` directory that holds another version. Rescore reports carry the stamps of both versions.

A fixture with no registered version is recorded as the error-only stamp `{error, content_hash}`: `error` is the reason it
has no version and `content_hash` is its fingerprint (the same hash a registered version has). `runcell --resume` writes
that stamp to `fixture_stamp.json` and refuses a resume unless the recorded and the current `content_hash` are both present
and equal. A cell that has no `fixture_stamp.json` (started before one was recorded) resumes with a warning that the
fixture is not checked. A fixture that cannot be fingerprinted (a required file missing, a link, or an unreadable file) is
refused by `runcell` before any build starts, so every cell records a fingerprint.

`runcell` reads the registry once per cell, before any build: a missing, unreadable or malformed registry is refused (exit 2),
so no cell records an unregistered stamp for a registered fixture. Its required files are `corpus.jsonl`, `queries.jsonl` and
`gold/answers.jsonl` (the refusal names the missing ones); its score step also reads `gold/bindings.jsonl` and
`gold/query_design.jsonl`, which runcell always passes, so a fixture without them fails at score.

A cell whose step fails (seal, build-to-evaluate, score, or the extra steps) has `status` `incomplete`, `incomplete` true and
`failed_step` naming the first step with a non-zero exit code; its exit code is in `steps`. The build's status (complete,
budget_stopped, ...) is kept only when every step succeeds. `report` takes its STATUS from the cell's `status` when
`cell.json` has one, so a failed step shows as incomplete there too.

## 7. Audit and trust boundaries

The public Mentat-Hard dev and test fixtures are synthetic generated data; there is no external catalogue prerequisite
for their corrections. A verified corpus, query, or gold fault is registered as a new fixture version under the same
immutable-hash rules above. Preserve the original fixture bytes, the affected query ids, and the evidence that reproduced
the fault.

A local adapter is an arbitrary process and is not sandboxed. The harness does not pass it the gold directory as an
argument or environment variable, but a child that can read the fixture on disk may still access public gold files. Do
not treat the process boundary as a security boundary.

## 8. Generated versions

A generated version is registered by its manifest: a registry entry with a `generator` object (module, params, code
identity, manifest path) and a per-file hash manifest `fixtures/manifests/<name>_v<version>.json`. Its data is rebuilt on
demand and checked byte for byte. The data MAY also be committed under `fixtures/<name>` when the owner decides so: the
Mentat-Hard splits are (owner decision: the test gold is public, and the matrix runs read the committed directories). A
committed directory is built with the generator's own command (`python -m mentat_bench.fixtures_hard build --split S
--out fixtures/mentat_hard_S`), must stamp to the registered content hash (`stamp_for`; the test suite checks it), and is
never written by `regenerate`, which always rebuilds into a new directory outside `fixtures/` and compares:

```
python -m mentat_bench.corrections register --generator <module> [--seed S] [--param KEY=VALUE ...] --name <name> \
    --version <v> --reason "<why>"
python -m mentat_bench.corrections regenerate <name> --version <v> --out <new dir>
```

- **Whitelist.** Only a module in `GENERATORS` (corrections.py) can register or regenerate; nothing is imported by name from a
  file. The whitelist holds `mentat_bench.fixtures_hard` (params `seed` and `split`; register the test split with
  `--seed 20261028 --param split=test`); a fixture generator is added to it with its parameter contract
  (`GENERATOR_PARAMS`: its keys, `seed` among them; each value an integer or a string, never a boolean). A whitelisted module
  has `SEED` (the default seed), `REQUIRED_FILES` (the files every version holds; `content_hashes` requires them for a
  directory whose `fixture.json` names a generated entry) and `generate(out, *, name, **params)`, which writes the new
  directory `out`. A `RuntimeError` from it is a refusal.
- **Register.** The generator writes into a scratch directory; the manifest is written first and the registry entry last
  (a failed registry write removes the manifest). A registered version's manifest is never replaced; an orphan manifest (no
  entry) is. A name's versions all use one module.
- **Code identity.** `code_sha256` is a SHA-256 over the sources of the modules `GENERATORS` lists for the module. On
  `regenerate` a different identity is a warning: the output is what is checked.
- **Regenerate.** Refused: a manifest or entry whose fields differ, params outside the contract, an output directory that
  exists or lies inside `fixtures/` (a committed version is compared, never overwritten), and a `retired` version. Any file that differs from the manifest refuses and removes
  the output (a removal that fails is reported, and the directory is left in place).

**Mentat-Hard (generated, with committed directories).** `mentat_hard_dev` and `mentat_hard_test` are generated versions whose
directories are also committed under `fixtures/`.
- **v3** (registry version `"3"`, generator fixture version `"3.1"`, registered 2026-10-30) is active for each split: `register --generator
  mentat_bench.fixtures_hard --seed 20261027 --param split=dev --name mentat_hard_dev --version 3 --reason "<why>"` (test: `--seed 20261028
  --param split=test`). Content hashes: dev `218003c63135ace753a1b2449ee002dc2813c7e99bed8c0956fd5a52187c3964`, test
  `7b408459538d7420e808d5d81e71916c57aee78f459a6b8c0905e48e43c8e6ab`. The registry version (3) and the fixture.json version (3.1) differ
  on purpose: the registry numbers the content, and the fixture version is the generator's (the headline reads its major number).
  The committed directories are its build (`python -m mentat_bench.fixtures_hard build --split S --out fixtures/mentat_hard_S`),
  and `regenerate` reproduces both byte for byte.
- **v2** (generator `"2"`, registered 2026-10-29) is `superseded` by v3. Its entry, hashes and manifest
  (`fixtures/manifests/mentat_hard_{dev,test}_v2.json`) stay; its committed bytes are in git history (commit `928df14`, the
  registration). Content hashes: dev `09da49370c1f3830c39bcd0e9f47193766b2446c22a03190b144a26b7fccad55`, test
  `1f4613bec957dd4de9b261b48241dbeeb3faf127a1a7686a6cf938b9a6aefc2e`.
- **v1** (generator `"1"`, registered 2026-10-27) is `superseded` by v2. Its entry, hashes and manifest
  (`fixtures/manifests/mentat_hard_{dev,test}_v1.json`) stay; its committed bytes are in git history.
- **Limit: v1 regenerates only at commit `7749647`**, where the generator is still v1. The whitelist names a module, not a commit,
  and `GENERATOR_PARAMS` is tied to the module, which is now v3. At HEAD `regenerate` refuses v1 (the output differs from its
  manifest), and the refusal is the behaviour, not a bug to work around. To reproduce v1, extract that commit
  (`git archive 7749647 python fixtures | tar -x -C <new dir>`) and run `regenerate` there: dev gives `7d23bcbd...`, test
  `0907b4f8...`.
- **v2 regenerates at commit `5e65729`** (the generator with `VERSION = "2"`). Checked for dev: that commit's
  `python/` tree, built with `python -m mentat_bench.fixtures_hard build --split dev`, gives content hash
  `09da4937...`, the registered v2 hash. The test split was not checked at that commit. At HEAD (v3.1) `regenerate`
  refuses v2, as it refuses v1: the generator now writes v3.1 bytes.

**Retired versions (exact-scale).** The exact-scale fixtures were registered this way (owner decision D3). Their generators
(`fixtures_exact`, `fixtures_d4`) were removed on 2026-10-27 by owner decision, with the rest of the exact-scale track
(docs/exact_scale.md). The five versions are `retired`: each entry got a change-log entry ("generator removed 2026-10-27, owner
decision; not regenerable at this commit"). Their content hashes, file hashes and manifests are unchanged, and are kept as
records. `regenerate` refuses them ("is retired: its generator ... was removed"), `rehash` refuses them, and `register`
refuses a committed version under their names.

| Version | Content hash (sha256) | Files | Bytes |
|---|---|---|---|
| `exact_p1_s24` v1 (p1, 24 seasons, 10,368 classes) | `89cbdc172cafb94206ac2f2495261e21624c747f59a9efb298f266498eda5d96` | 8 | 17,993,153 |
| `exact_p2_s22` v1 (p2, 22 seasons, 10,184 classes, the 10k tier) | `3a82e59b60aa23a562a2497082a59fc3fc74a4b742fc2697a96ce26caa2fefcb` | 10 | 18,714,878 |
| `exact_p2_s210` v1 (p2, 210 seasons, 100,518 classes, the 100k tier) | `35437e92a26c2a871d0783f2a3b1f18e655882d41b141db61a0c6efae503138b` | 10 | 184,389,579 |
| `exact_p2_s2096` v1 (p2, 2,096 seasons, 1,000,383 classes, the 1M tier) | `6a4d2cd28d05ac00b47496e4588c285106f9402454371e4fd011268600547e0d` | 10 | 1,835,404,454 |
| `exact_d4` v1 (the D4 proxy, seed 20261018) | `493884b6b1f38ee15a540e3871b79880d1165a201f742b66686d7854324f39b9` | 11 | 5,836,766 |

The byte counts are those of the regenerated directories, measured before the removal.

# v0.2.0 record: deviations from the pre-registration

> The pre-registration itself is
> [release/v0.1.0/record/prereg.md](../../../v0.1.0/record/prereg.md); nothing in it is changed. This file lists where v0.2.0 differs
> from it, and why.

Release: v0.2.0, a new measurement of the Mentat-Hard test set (fixture revision 3.1, content hash
`7b408459538d7420e808d5d81e71916c57aee78f459a6b8c0905e48e43c8e6ab`, the same test fixture as v0.1.0). Measured on 2026-11-02 (the last run finished that day, Pacific time).

## Deviations

1. **Ollama 0.40.13, not pre-registered.** The pre-registration names Ollama 0.40.11. v0.2.0 measures on 0.40.13 (both A6000 GPUs,
   CUDA). The digests of the 15 models are the pre-registered ones (the pins in `children/ollama_models.json`), so the weights are
   the same; the server is not. A re-run on 0.40.13 does not reproduce the v0.1.0 cells exactly: the decision flips against v0.1.0
   are 65 (D3 27B), 125 (D3 4B), 88 (D3 9B), 47 (TEV1 0.8B) and 28 (Kev 9B, bf16), of 10,716 decisions. The headline changes are
   +0.0027, −0.0008, −0.0003, +0.0004 and +0.0003. The version shift is reported in
   [results/v0.2.0/README.md](../../../../results/v0.2.0/README.md); it is not corrected.
2. **Two Kev models added after pre-registration.** `kev:4b-q8_0` (id `kev_4b-q8_0`, "Kev 4B") and `kev:0.8b-q8_0` (id
   `kev_0.8b-q8_0`, "Kev 0.8B") were added through the documented outside-lab path: pin ([added-models.json](added-models.json),
   [kev-pins.json](kev-pins.json)), conform (the conform report of each: digest, determinism and over-context checks, all passed),
   a dev run, `floor_calibrate --labels` on the dev labels, then the test run. Their floors come from their own dev runs, by the
   same rule as the pre-registered models, on 0.40.13. Their dev headlines are 0.1594 (kev_4b-q8_0) and 0.0422 (kev_0.8b-q8_0);
   their dev floors are 0.4150489932077879 and 1.4908574616241355. They are flagged `not_preregistered` in leaderboard.json and marked "not pre-registered" in the README leaderboard table.
3. **r1 is the headline run, designated before r2.** r1 is the run of each model on 0.40.13 (folder
   `runs/repro-0.40.13/<id>`). r2 (`runs/repeat-0.40.13-r2/<id>`) is a run-to-run repeat of r1 only; it is not a second headline.
   Only r2's predictions are committed (no r2 cells). Provenance of the r2 runs: tev1_4b and d1-omni_600m were run on GPU1's
   Ollama server (port 11436) rather than GPU0's; pplx-decider_27b r2 ran on GPU0 (port 11435). The server configuration is the same
   (CUDA only, `OLLAMA_NUM_PARALLEL=2`). A later GPU0 lane tried tev1_4b and d1-omni again; the tool refused both because their folders
   were not empty, so nothing was overwritten. The
   two Kev test runs ran on GPU1. The first attempt of the Kev test runs failed before any model call (a queue-script argument
   bug) and was re-run; nothing from the failed attempt is used.
   "identical" has two meanings, and the prose uses them as follows. `identical_decisions` is true when the decisions file is
   byte-identical (answers and probabilities). `identical_labels` is true when every answer equals r1's. Equal answers with
   different probabilities are probability-only differences: the answers agree, the probabilities move by a small amount, and the
   decisions file is not byte-identical. In the analysis, `identical` is the share of rows whose answer equals r1's (the field the
   site reads); `identical_decisions` implies `identical_labels`. The analysis does not list the r2 predictions in
   `based_on.predictions`: the site's predictions rule names only the release and record_from predictions. Each r2 record in
   `repeats/runs.json` carries `predictions_sha256`, the hash of the predictions file, and the analysis checks each file against it.
4. **Reference unchanged.** The reference is D3 27B, chosen on dev before pre-registration. It is not re-chosen for v0.2.0.
5. **Relation floors unchanged for the pre-registered models.** Their floors (the prereg's `relation_floor`) are used as they are.
   A floor affects only which relations are named under the floor, and so only the repaired-base Q_N diagnostic; the decision
   scores use raw decisions. The Kev models use their own dev floors.
6. **No private split.** The private split is not re-run (owner-run only, sealed). v0.2.0 publishes no private-split results: no
   row has a private block, and v0.1.0's private results stay in v0.1.0.
7. **Item audit not re-run.** The v0.1.1 item audit (model judges) stands for the same test set. v0.2.0's analysis sets `item_audit`
   to null and points to v0.1.1.
8. **Release gates not re-run.** The gate record is v0.1.0's ([check_gates.md](../../v0.1.0/record/check_gates.md)); the gates
   were checked at the v0.1.0 commit, and gate 7 (the scope of the pinned commit) is UNKNOWN at any later commit, as it was.
9. **Engine binary (Q_N diagnostic).** The runs used an engine binary (sha256
   `38fc6fcc4b862634fbcc6e6de0ec654aaef1d0960202c76988f46fe28c0ce094`) built on the owner's machine from the public engine source
   (`crates/`, `Cargo.toml` and `Cargo.lock` identical to main). Rebuilding from that source gives a binary of the same size that
   differs in 28 bytes (sha256 `c382aaddf046d1d93e1cdcd5f16d4d82af0f1bea011202d6c0cde52ea3319103`), so the binary is not
   byte-reproducible. The v0.1.0 binary (sha256 `2df1bdfbb802612e1a93857c2fb8ee5cfc17fb164c54c55034cb8edc4ed7937c`) is a different
   file. Only Q_N and repairs depend on the engine; the headline is scored from the model's decisions.
10. **Cell commit provenance.** Five headline cells record repo commit `29ce00d` with `dirty: true`: d3_27b-q8_0, d3_4b-q8_0,
    d3_9b-q8_0, kev_9b-bf16 and tev1_0.8b-q8_0. The other twelve cells record `f1652ad`, the reproduce-tool branch head, clean; its
    tree equals main's (`eaa9233`), and the diff `29ce00d..f1652ad` touches only `README.md`, `tests/test_reproduce.py` and
    `tools/reproduce.py`. The owner's account of the dirty state is that the tree held uncommitted drafts of the reproduce tool and
    README, not benchmark code. That is the owner's statement, not a verified fact: a commit diff cannot show uncommitted state,
    and the cells record only `dirty: true`. The collection accepts exactly these two commits. The commit and dirty flag of each cell
    are in [run-commits.json](run-commits.json) (written by `collect`); the run cards repeat them.
11. **Baselines.** Each baseline is carried from v0.1.0 when its predictions are reproduced exactly; empty and oracle have no
    predictions, so they are carried when their `score.json` equals v0.1.0's once `usage` (timings, memory, call counts) is removed.
    The seal digest is not compared: it hashes the submission directory, timings included. The carry rule is in the builder
    (`results/v0.2.0/build_bundle.py`, `collect_baselines`). Status: [baselines.json](../../../../results/v0.2.0/baselines.json).
    All seven baselines are carried from v0.1.0: lexical, heuristic, embedding, hf and gold_view have identical predictions; empty and oracle
    have equal scores once usage is removed.
    empty and oracle were re-run on CPU with the published flags (`--adapter empty|oracle --local-protocol 2 --top-k 24`, no
    `--candidates`) on the engine the runs used (sha256 38fc6fcc…, item 9); their Q_N (16.8769 and 100.0) equal v0.1.0's.
    A carried baseline's row in [leaderboard.json](../../../../results/v0.2.0/leaderboard.json) is v0.1.0's row, field for field
    (flags, notes, build flags, run card and run directory, metrics, depth, coverage, counts, repair, runtime). The two fields
    that differ are these: `gap_to_reference` (against this release's reference, D3 27B, re-measured here; null for empty and
    oracle, as in v0.1.0) and `private` (null, because v0.2.0 publishes no private-split results; v0.1.0 keeps its private blocks).
    The builder checks this: `carry_problems` names any other field that differs, or a private block that is not null, and
    `carried_metric_problems` requires every recomputed metric point, and every interval where the run has rows, to equal
    v0.1.0's. Empty and oracle have no Q_N interval, as in v0.1.0. The v0.2.0 flags do not add a `carried_from_v0.1.0` flag:
    [baselines.json](../../../../results/v0.2.0/baselines.json) records the carry.
12. **Dates.** The release date is 2026-11-02.
13. **Dev blocks.** The dev block of the 15 pre-registered models is carried from v0.1.0 (their dev runs are not repeated): those are
    0.40.11 dev results, and their `headline_change_test_minus_dev` is null, because a change across the two Ollama versions is not
    reported. The two Kev models' dev blocks come from their own 0.40.13 dev runs, and their change is computed.

## What the claims are

- An owner re-measurement on the same machine. It is not an independent reproduction or verification.
- A score on this benchmark's relation and group decisions, not general capability.
- r1 versus r2 differences may come from batching (OLLAMA_NUM_PARALLEL=2). That is a hypothesis; it is not tested here.

## Files in this folder

- [runcards/](../runcards): one run card per model cell (`mentat_bench.runcard`).
- [run-commits.json](run-commits.json): each cell's repo commit and dirty flag.
- [added-models.json](added-models.json): the two added models' pins, dev floors and dev headlines.
- [kev-pins.json](kev-pins.json): the two Kev pins, as in `children/ollama_models.json`.

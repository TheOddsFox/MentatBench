# Run evidence and reporting

This guide covers run cards, lower-ceiling cells, and comparisons supported by the public source checkout. The release manifests and gate reports under `release/` are historical records; they are not regenerated or checked by this checkout.

## Lower-ceiling cells

`runcell` keeps a build's checkpoints in `<run>/submission.build`. The ceilings tool finalizes the latest checkpoint within each requested limit, then seals, evaluates, and scores that cell without asking the model again.

```bash
python -m mentat_bench.ceilings \
  --work runs/model/submission.build --metric gpu_seconds --ceilings 60,300,600 \
  --fixture fixtures/mentat_hard_dev --profile profiles/mentat-hard-v1.json \
  --out runs/model/ceilings
```

The work directory must come from a build made with `--keep-work` (all `runcell` runs keep it). Metrics are `spend_usd`, `gpu_seconds`, `wall_seconds`, and `items`. Each output cell records its checkpoint, committed usage, and any billed work after that checkpoint. A cell below the first checkpoint has no submission and scores zero. Use the profile and fixture from the original run.

## Run cards

```bash
python -m mentat_bench.runcard --cell runs/model
```

The command writes `runcard.json` beside `cell.json`. A card records the machine, available runtime package versions, engine and profile identity, fixture file hashes, source revision and dirty state, build policy, model details, usage, scores, and run status. It reads runtime facts captured by `runcell` when available; otherwise it captures the current machine state. It does not run the engine.

## Comparing runs

```bash
python -m mentat_bench.report --runs runs/model-a runs/model-b
```

The report summarizes the supplied `runcell` directories. It does not combine quality and cost into a single ranking.

## Historical release evidence

The manifests, run cards, and gate reports for internal releases v0.2 and v0.3 remain unchanged under [`release/`](../release/). They are measurement records. The v0.3 evidence concerns Mentat-Hard fixture 3.1 and the results now published in the [v0.1.0 bundle](../results/README.md). The public software and bundle version is separate from the original internal release label.

Those manifests pin inputs that are not present here: the original private source snapshot, a Windows `mb-engine.exe`, the external Parquet catalogue, and a sealed private fixture. The internal release runner was omitted from this source checkout. Consequently this checkout has no active `release build`, `release verify`, or `release check-gates` command, and the archived gate reports are not verification of the current public source tree.

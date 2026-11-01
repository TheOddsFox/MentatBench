# Plugging an outside model into Mentat-Hard

**Status: implemented.** Owner decision (2026-10-29): an outside lab plugs its model in through any server that implements
`/v1/systemone` (Ollama's endpoint, or a third-party server with the same wire format), behind a conformance check and a
digest pin. Only System One decision models are plugged in this way. The chat-logprob child sketched in earlier versions of
this document (`openai_chat_child`) is retired: it does not exist and is not planned.

The fixed harness (spec s9) keeps the prompts, decision schemas, call graph, solver and retry policy, and changes only the
model adapter. An outside lab supplies one thing: a server that answers the System One questions. The engine, the scoring,
the gold and the release tools are not touched.

## 1. What a server must implement

- **`POST /v1/systemone`** in the wire format of `docs/local_protocol.md`, section "System One child": one question per
  request, a `choice` answer with `probabilities` over the question's options (each in [0, 1], summing to 1 within 0.01),
  and `usage` with `input_tokens` and `output_tokens`.
- **Refusals.** An input that does not fit the context is refused with HTTP 413, or with HTTP 400 whose `error` text contains
  one of the phrases `longer than the context length` or `exceeds the available context size` (any letter case; Ollama 0.40.11
  answers the second, laya the first). The child reads any of them as an abstention. A server that
  answers an oversized input instead fails `conform`. Other 400s are item errors, 500 is retried once, and a 404 or a redirect
  fails the call.
- **A digest.** The child needs one digest for the model, from the first of these that the server gives. A source that does
  not list the model, or lists it without a digest, falls through to the next:
  - `/api/tags`, as Ollama does (the entry's `digest`);
  - `/v1/models`, an entry for the model with a `digest` field;
  - a top-level `digest` field in the reply to a `/v1/systemone` request.

  In `/api/tags` and `/v1/models`, 404, 405 and 501 mean absent. The digest may carry a `sha256:` prefix. A server where no
  source gives a digest is refused. Its model cannot be pinned.
- **Loopback.** The child talks only to `MB_S1_URL` on `localhost`, `127.0.0.1` or `::1`, follows no redirects and uses no
  proxy. That rule is unchanged, so the server runs on the machine that runs the child. The child does not reach a server on
  another host.
- **Ollama version** (`/api/version`) is optional. Its absence, or any error from it, is recorded as null.

## 2. Plugging in: pin, conform, run

Run these with `PYTHONPATH=python` from the repository, with `MB_S1_URL` set to the server (default `http://127.0.0.1:11434`).

```
# 1. Pin the model the server serves (writes children/ollama_models.json, or MB_S1_PINS for a local trial)
python -m mentat_bench.children.systemone_child pin <tag> [--license X]

# 2. Conform: a JSON report on stdout; exit 0 only when every check passes
python -m mentat_bench.children.systemone_child conform <tag> [--url http://127.0.0.1:11434]
```

`pin` for a server that is not Ollama runs `conform` first, refuses the pin when a check fails, and keeps the passing report
in the entry's `conform` field. Its `server` field records the server kind (`openai` for `/v1/models`, `reply` for a digest in
the reply). An Ollama entry is written as before, without those fields. The child refuses a non-Ollama pin that has no passing
`conform` record.

`conform` needs no benchmark fixture. It checks the server against the child's own question builders, at the largest menus the
decision code builds. A bad URL or an unreachable server is a failed `server` check in the report:

| Check | What it requires |
| --- | --- |
| `pin` | the digest and server kind are the pin's (not run by `pin`, which runs the other checks before the entry exists) |
| `probabilities` | one fixed question per name the child sends (status, negated, missing, relation, kind, k, order, lower, upper, base, alt), at the largest menus: probabilities in [0, 1], summing to 1 within 0.01. A menu refused as over-context is an abstention, as in production: it is recorded as `abstains (over context)` and passes. Any other refusal or error fails |
| `determinism` | the same question twice gives the same probabilities (absolute difference at most 1e-6, and the same argmax); its detail counts the questions compared |
| `over_context` | a 1 MB state is refused as over-context (413, or 400 with one of the phrases `longer than the context length`, `exceeds the available context size`) |
| `usage` | the reply's `usage` has non-negative integer `input_tokens` and `output_tokens` |
| `digest_at_exit` | the digest read after the checks equals the digest at the start |

Then the fixed harness runs the child. The dev run calibrates the relation floor, and the test run uses it, as for any
System One model:

```
# dev: the floor for this model (lowest --relation-floor with precision >= 0.8 on the dev suite labels)
PYTHONPATH=python MB_S1_MODEL=<tag> MB_S1_URL=<server> \
python -m mentat_bench.runcell --fixture fixtures/mentat_hard_dev --candidates fixtures/mentat_hard_dev/candidates.jsonl \
    --adapter local --local-protocol 2 --local-arg python --local-arg=-m --local-arg=mentat_bench.children.systemone_child \
    --local-timeout 1800 --hello-timeout 3600 --profile profiles/mentat-hard-v1.json --out runs/<date>-<lab>-dev/<cell>

# test: the cell, with the floor from the dev step
PYTHONPATH=python MB_S1_MODEL=<tag> MB_S1_URL=<server> \
python -m mentat_bench.runcell --fixture fixtures/mentat_hard_test --candidates fixtures/mentat_hard_test/candidates.jsonl \
    --adapter local --local-protocol 2 --local-arg python --local-arg=-m --local-arg=mentat_bench.children.systemone_child \
    --local-timeout 1800 --hello-timeout 3600 --relation-floor <floor from dev> --profile profiles/mentat-hard-v1.json \
    --out runs/<date>-<lab>/<cell>

python -m mentat_bench.runcard --cell runs/<date>-<lab>/<cell> --out docs/history/round-2/runcards/<lab>-<cell>.json
python -m mentat_bench.report --runs runs/<date>-<lab>/<cell>
```

The child refuses a model that is not pinned, unless `MB_S1_ALLOW_UNPINNED=1` is set. `MB_S1_PINS` names another pin file: it is
for a lab's local trials only. Each request carries `keep_alive: -1`, so the model stays loaded for the run.

The child checks the digest at startup and again at exit. At exit a change, or a server that cannot be asked, makes the child
exit 1 after its output, and the build that ran it fails: `cli build` treats a child that exits nonzero after its input closes
as a failed build, with the message `build failed: the local child ...`. The adapter waits up to 30 s for the child to exit after
its input closes (`CLOSE_WAIT_S`), so the exit probe of a server with no model list is not killed by mistake; a child still
running after that is killed and fails the build.

## 3. How results are labelled

- **Rankable.** A run through the fixed harness, with the child's prompts and decision logic, the pinned profile
  (`profiles/mentat-hard-v1.json`), the fixture and the engine of the release, against a model whose pin is in the repository's
  own `children/ollama_models.json`, as reviewed in a pull request, with a passing `conform` record (for a server that is not
  Ollama). Such runs are ranked by the headline of their fixture version (spec s8; `docs/mentat_hard.md`, section 5): the relation
  MCC for versions 1 and 2 (owner decision 2026-10-29), and the mean of the relation and group MCC for round 3 and later
  (owner decision 2026-10-30). The binding MCC is secondary; the reading MCC and Q_N are diagnostics, and the group MCC is a
  part of the round 3 headline and a diagnostic in versions 1 and 2.
- **Not rankable.** A run whose model is pinned only in an `MB_S1_PINS` file, or unpinned (`MB_S1_ALLOW_UNPINNED=1`), or whose
  server fails `conform`. Such a run is a diagnostic only and is not ranked.
- **Private split.** The lab does not receive the private fixture, its labels or any per-question result. The owner runs the
  private split with the repository's own child and a loopback URL set by the owner, never by the lab. The lab's argv and
  URLs are never used for a private run.

What the run records about the model is the hello's `model` block: the digest as `revision`, `ollama_version`
(null without `/api/version`), `child_version` and `prompt_sha256`. The conform report and the pin's `server` field are not
yet carried into the run card.

## 4. Limits

- The URL rule is unchanged: loopback only. Serving a lab model from another host needs an owner decision, not a change here.
- `conform` samples one question per stage, at the largest menus, and checks determinism on those. It does not measure drift
  over a full run. Its menus are rebuilt with the decision code's formulas, since that code builds them inline.
- A server that truncates an oversized input instead of refusing it fails `over_context`, and so cannot be pinned for a run.
- The digest is checked at startup and at exit, not on each request. A change during the run is caught at exit.
- A server whose digest is read from its replies is probed once at startup and once at exit, with one extra request each.
- The exit check of the build (`cli build`) is the first real enforcement of a child's exit status. A child that exits nonzero for
  reasons of its own (for example a crash in its interpreter teardown after all its output) now fails the build too.

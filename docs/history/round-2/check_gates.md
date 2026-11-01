| gate | status | evidence | note |
|---|---|---|---|
| Immutable corpus manifests, evidence policy, schemas, IR semantics, resource profiles | MET | release_manifest.json | verifies; pins the spec, docs/ir.md, the engine, profiles, fixture files and fixture manifests, data, run cards, baselines and the repo state; the evidence policy and the schemas are not pinned; sealed ['mentat_hard_private'] pinned by content hash only; declared scope mentat-hard |
| Complete exact-panel oracle (every corpus record bound or unresolved; an answer for every query) | UNKNOWN | fixtures/mentat_hard_dev/gold, fixtures/mentat_hard_test/gold | binding and answer coverage of the pinned fixtures only; the oracle's correctness and the evidence policy are not pinned here; sealed ['mentat_hard_private'] not inspected (hash only) |
| Declared coverage for every native scope | NOT APPLICABLE | fixture.json of each native fixture | scope mentat-hard (declared at build): no fixture is native by content (controlled is not true in any pinned fixture.json), and no sealed split is declared native |
| Audited separation of unjudged material (UNJUDGED is not false) | NOT APPLICABLE | docs/adjudication.md | scope mentat-hard (declared at build): UNJUDGED is a native-scope concept (spec s4), and no native scope is in the release |
| At least 1M reference-distinct, actively tested semantic units before using the Exact 1M label | REMOVED | docs/exact_scale.md | removed by owner decision 2026-10-27: the exact-scale code is gone and the Exact 1M label is not claimed (spec v0.2) |
| Reference validation of every hidden query and certificate rule (with global-difficulty strata) | UNKNOWN | fixtures/mentat_hard_dev/gold/verdicts.jsonl, fixtures/mentat_hard_test/gold/verdicts.jsonl | every query has a verdict; certificate rules and strata are not checked here |
| Leakage checks | UNKNOWN | tests/test_leakage.py | test present; the tool does not run it (run pytest on it) |
| Blinded annotation and adjudication policy | UNKNOWN | python/mentat_bench/adjudication.py | blinded tooling present; blinding in practice is not checked |
| Complete cost accounting | UNKNOWN | runcards in the manifest | usage recorded; charges outside the gateway are not metered here |
| Passing baseline runs | NOT MET | release/baselines/baselines.json and the run cards of its cells | every required baseline passes; the fixed pipeline (hf) is absent |
| Public correction process that versions faulty fixtures | UNKNOWN | docs/corrections.md | document present; that corrections are applied is not checked here |

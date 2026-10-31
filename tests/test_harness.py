import json

import pytest

from mentat_bench import ir
from mentat_bench.adapters.base import usage
from mentat_bench.adapters.mock import EmptyAdapter
from mentat_bench.harness import fixed
from mentat_bench.harness.fixed import ParityUnionFind, candidate_pairs
from mentat_bench.manifest import DEFAULT_PROFILE
from irval import check_expr, check_submission


def _records(n=6):
    return [
        {"id": f"toy:m{i}:1", "question": f"Will alpha team reach {i} points in 2026?", "label": "Yes", "event_title": "e"}
        for i in range(1, n + 1)
    ]


def test_parity_union_find_tracks_negation_through_chains():
    uf = ParityUnionFind()
    uf.union("A", "B", True)  # A == not B
    uf.union("B", "C", False)  # B == C
    root_a, pa = uf.find("A")
    root_c, pc = uf.find("C")
    assert root_a == root_c
    assert pa != pc  # A == not C
    assert uf.find("B") == (root_a, uf.find("B")[1])


def _pair_records(texts):
    return [{"id": f"toy:m{i}:1", "question": t, "label": "Yes", "event_title": None} for i, t in enumerate(texts, 1)]


def test_candidate_pairs_are_deterministic_and_ordered():
    recs = _pair_records(["p1 alpha", "p1 beta", "p2 gamma", "p2 delta", "solo zeta"])
    first = candidate_pairs(recs)
    assert first == candidate_pairs(recs)
    assert first == [(0, 1), (2, 3)]
    assert all(i < j for i, j in first)


class _Scripted:
    """Binding decisions from a dict; relation decisions from a function. Costs are configurable."""

    name = "scripted"

    def __init__(self, bindings, relation, cost_binding=0.0, cost_relation=0.0):
        self.bindings = bindings
        self.relation = relation
        self.cost_binding = cost_binding
        self.cost_relation = cost_relation

    def decide_binding(self, ctx):
        return self.bindings[ctx["id"]], usage("scripted", "local", cost_usd=self.cost_binding)

    def decide_relation(self, a, b):
        return self.relation(a["id"], b["id"]), usage("scripted", "local", cost_usd=self.cost_relation)


def test_union_find_merges_classes_with_parity_into_aliases(tmp_path):
    recs = _records(2)
    bindings = {
        "toy:m1:1": {"status": "bound", "class": "X", "negated": False, "missing": []},
        "toy:m2:1": {"status": "bound", "class": "Y", "negated": True, "missing": []},
    }
    adapter = _Scripted(bindings, lambda a, b: "EQUIV")
    summary = fixed.build(recs, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["budget_stopped"] is False
    rows = {r["id"]: r for r in ir.read_jsonl(tmp_path / "sub" / "bindings.jsonl")}
    assert rows["toy:m1:1"]["class"] == rows["toy:m2:1"]["class"]
    assert rows["toy:m1:1"]["negated"] == rows["toy:m2:1"]["negated"]
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text())
    (alias_expr,) = theory["aliases"].values()
    assert alias_expr["op"] == "not"  # class meaning is the negation of the first outcome
    assert check_submission(tmp_path / "sub") == []


def test_budget_in_bindings_phase_writes_nothing(tmp_path):
    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=0.0)
    with pytest.raises(fixed.BuildIncomplete):
        fixed.build(_records(2), EmptyAdapter(), tmp_path / "sub", profile)
    assert not (tmp_path / "sub").exists() or not any((tmp_path / "sub").iterdir())


def test_budget_in_relations_keeps_completed_checkpoints(tmp_path):
    recs = _pair_records(["p1 x1", "p1 x2", "p2 x3", "p2 x4", "p3 x5", "p3 x6"])
    bindings = {r["id"]: {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]} for r in recs}
    adapter = _Scripted(bindings, lambda a, b: "EXCLUDES", cost_relation=1.0)
    profile = dict(DEFAULT_PROFILE, spend_ceiling_usd=1.5)  # call 1 ends at 1.0; call 2 overshoots to 2.0
    summary = fixed.build(recs, adapter, tmp_path / "sub", profile, checkpoint_every=1)
    assert summary["candidate_pairs"] == 3
    assert summary["budget_stopped"] is True
    assert summary["relations_committed"] == 1  # the overshooting call is discarded; its batch is not committed
    assert summary["calls"] == 6 + 2 and summary["spend_usd"] == pytest.approx(2.0)  # its spend is still recorded
    assert check_submission(tmp_path / "sub") == []


def test_malformed_decisions_become_unresolved(tmp_path):
    recs = _records(2)
    bindings = {"toy:m1:1": {"status": "bound", "class": None}, "toy:m2:1": {"status": "unresolved", "class": None, "missing": []}}
    adapter = _Scripted(bindings, lambda a, b: "MAYBE")
    fixed.build(recs, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    rows = list(ir.read_jsonl(tmp_path / "sub" / "bindings.jsonl"))
    assert all(r["status"] == "unresolved" and r["missing"] == ["malformed_decision"] for r in rows)
    assert check_submission(tmp_path / "sub") == []


def test_empty_adapter_build_is_wellformed(tmp_path):
    fixed.build(_records(5), EmptyAdapter(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text())
    assert theory["constraints"] == [] and theory["interpretations"] == ["base"]
    assert check_submission(tmp_path / "sub") == []
    assert all(r["status"] == "unresolved" for r in ir.read_jsonl(tmp_path / "sub" / "bindings.jsonl"))


def test_expr_checker_accepts_ir_ops_and_rejects_bad_shapes():
    good = [
        {"op": "true"},
        ir.var("toy:m1:1"),
        {"op": "and", "args": [ir.var("toy:m1:1"), {"op": "false"}]},
        {"op": "implies", "a": ir.var("toy:m1:1"), "b": ir.var("toy:m2:1")},
        {"op": "atleast", "k": 2, "args": [ir.var("toy:m1:1")]},
        {"op": "wsum", "cmp": "le", "k": 3, "terms": [{"w": 2, "e": ir.var("toy:m1:1")}]},
        {"op": "ref", "name": "A"},
    ]
    for e in good:
        assert check_expr(e, refs={"A"}) == [], e
    bad = [
        {"op": "xor", "args": []},
        {"op": "atmost", "k": "2", "args": []},
        {"op": "wsum", "cmp": "lt", "k": 1, "terms": []},
        {"op": "ref", "name": "Missing"},
        {"op": "var"},
    ]
    for e in bad:
        assert check_expr(e, refs={"A"}), e


def test_build_refuses_nonempty_output_before_any_model_call(tmp_path):
    out = tmp_path / "sub"
    fixed.build(_records(2), EmptyAdapter(), out, dict(DEFAULT_PROFILE))
    with pytest.raises(FileExistsError):
        fixed.build(_records(2), EmptyAdapter(), out, dict(DEFAULT_PROFILE))

    calls = []

    class Counting(EmptyAdapter):
        def decide_binding(self, ctx):
            calls.append(ctx)
            return super().decide_binding(ctx)

    with pytest.raises(FileExistsError):
        fixed.build(_records(2), Counting(), out, dict(DEFAULT_PROFILE))
    assert calls == []


def test_parity_contradicting_equiv_is_rejected_and_emits_no_iff(tmp_path):
    recs = _pair_records(["p1 a", "p1 b"])
    bindings = {
        "toy:m1:1": {"status": "bound", "class": "X", "negated": False, "missing": []},
        "toy:m2:1": {"status": "bound", "class": "X", "negated": True, "missing": []},  # X and not-X
    }
    adapter = _Scripted(bindings, lambda a, b: "EQUIV")
    summary = fixed.build(recs, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["candidate_pairs"] == 1
    assert summary["equiv_rejected"] == 1
    assert summary["constraints"] == 0
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text())
    assert theory["constraints"] == []
    assert check_submission(tmp_path / "sub") == []


def test_union_find_reports_contradictions():
    uf = ParityUnionFind()
    assert uf.union("A", "A", True) is False  # same class, opposite negation
    assert uf.union("A", "B", False) is True
    assert uf.union("B", "A", True) is False  # contradicts the recorded parity


def test_full_replay_adapter_skips_relation_decisions(tmp_path):
    from mentat_bench.adapters.base import usage as _usage

    recs = _records(2)
    constraint = {"id": "g000001", "expr": {"op": "atmost", "k": 1, "args": [ir.var("toy:m1:1"), ir.var("toy:m2:1")]}, "interps": None}

    class Replay:
        name = "gold_replay"
        supplies_gold_refs = True  # full replay is for gold-backed adapters only: this fake stands in for the oracle
        relation_calls = 0

        def decide_binding(self, ctx):
            return {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]}, _usage("replay")

        def decide_relation(self, a, b):
            Replay.relation_calls += 1
            return "EXCLUDES", _usage("replay")

        def extra_theory(self):
            return {"interpretations": ["base"], "constraints": [dict(constraint)]}

    summary = fixed.build(recs, Replay(), tmp_path / "sub", dict(DEFAULT_PROFILE))
    assert summary["mode"] == "full_replay" and summary["candidate_pairs"] == 0
    assert Replay.relation_calls == 0
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text())
    assert [c["id"] for c in theory["constraints"]] == ["g000001"]
    assert check_submission(tmp_path / "sub") == []


def test_jsonl_and_json_readers_accept_utf8_bom(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_bytes(b"\xef\xbb\xbf" + b'{"a":1}\n')
    assert list(ir.read_jsonl(p)) == [{"a": 1}]
    q = tmp_path / "x.json"
    q.write_bytes(b'\xef\xbb\xbf{"b":2}')
    assert ir.read_json(q) == {"b": 2}


def test_candidate_pairs_skip_tokens_over_the_df_cap():
    # "common" is in every record: with max_df=3 it must not link anything, "uniq" tokens still do.
    texts = [f"common pair{i // 2} filler{i}" for i in range(10)]
    recs = _pair_records(texts)
    capped = candidate_pairs(recs, max_df=3, max_df_frac=1.0)
    assert capped == [(i, i + 1) for i in range(0, 10, 2)]
    assert len(candidate_pairs(recs, max_df=100, max_df_frac=1.0)) > len(capped)


def test_blocking_stops_at_the_build_deadline_and_keeps_bindings(tmp_path):
    now = [0.0]
    recs = _pair_records([f"p{i // 2} x{i}" for i in range(2000)])
    bindings = {r["id"]: {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]} for r in recs}
    adapter = _Scripted(bindings, lambda a, b: "EXCLUDES")
    import mentat_bench.harness.fixed as fx

    orig_gateway, orig_iter = fx.Gateway, fx.iter_candidate_pairs

    def blocking_after_the_deadline(*a, **k):
        now[0] = 100.0  # the deadline passes between the bindings phase and blocking
        return orig_iter(*a, **k)

    fx.Gateway = lambda *a, **k: orig_gateway(*a, clock=lambda: now[0], **k)
    fx.iter_candidate_pairs = blocking_after_the_deadline
    try:
        summary = fixed.build(recs, adapter, tmp_path / "sub", dict(DEFAULT_PROFILE, build_deadline_s=50.0))
    finally:
        fx.Gateway, fx.iter_candidate_pairs = orig_gateway, orig_iter
    assert summary["budget_stopped"] is True
    assert summary["relations_committed"] == 0
    assert summary["outcomes"] == 2000
    assert check_submission(tmp_path / "sub") == []


def test_model_calls_stop_at_the_finalize_reserve_and_admission_still_seals(tmp_path, monkeypatch):
    # Four relations over two complement markets (p: A, not A; q: B, not B) say A -> B, A -> not B, not A -> B and
    # not A -> not B: no consistent base, so admission refuses one. Each relation call takes 5 s of the fake clock; the
    # build deadline is 100 s, so the model calls stop at 95 s (the reserve is 5 s) and finalize still has that time.
    import mentat_bench.harness.fixed as fx

    now = [0.0]
    orig_gateway = fx.Gateway
    monkeypatch.setattr(fx, "Gateway", lambda *a, **k: orig_gateway(*a, clock=lambda: now[0], **k))
    chain = {("toy:p:1", "toy:q:1"): "IMPLIES", ("toy:p:1", "toy:q:2"): "IMPLIES",
             ("toy:p:2", "toy:q:1"): "IMPLIES", ("toy:p:2", "toy:q:2"): "IMPLIES"}

    class _Timed(_Scripted):
        def decide_relation(self, a, b):
            now[0] += 5.0
            return super().decide_relation(a, b)

    chain_recs = [{"id": f"toy:{m}:{k}", "question": f"quasar chain outcome {m} {k}", "label": "Yes", "event_title": None}
                  for m in ("p", "q") for k in (1, 2)]
    fillers = [{"id": f"toy:f{k}:1", "question": f"zeta{k // 2} filler", "label": "Yes", "event_title": None} for k in range(20)]
    bindings = {r["id"]: {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]} for r in fillers}
    for rec, (cls, neg) in zip(chain_recs, [("A", False), ("A", True), ("B", False), ("B", True)]):
        bindings[rec["id"]] = {"status": "bound", "class": cls, "negated": neg, "missing": []}
    adapter = _Timed(bindings, lambda a, b: chain.get((a, b), "NONE"))
    sub = tmp_path / "sub"
    summary = fixed.build(chain_recs + fillers, adapter, sub, dict(DEFAULT_PROFILE, build_deadline_s=100.0),
                          checkpoint_every=1, complement_dedup=False)
    assert summary["budget_stopped"] is True
    assert summary["finalize_reserve_s"] == 5.0
    assert summary["wall_seconds"] == pytest.approx(95.0)  # the model calls stopped at the reserve point
    assert summary["relations_committed"] == 19  # the calls that started before 95 s
    assert summary["admit_mode"] == "admit" and summary["admit_rejected_relations"] >= 1  # admission had to drop one
    assert json.loads((sub / "PARTIAL.json").read_text())["reasons"] == ["budget_stopped"]
    assert check_submission(sub) == []


def test_finalize_records_the_reserve_of_the_build_not_of_the_finalizing_profile(tmp_path):
    fixed.build(_records(), EmptyAdapter(), tmp_path / "sub", dict(DEFAULT_PROFILE, build_deadline_s=3600.0), keep_work=True)
    work = tmp_path / "sub.build"
    larger = dict(DEFAULT_PROFILE, build_deadline_s=7200.0)
    assert fixed.finalize_checkpoint(work, tmp_path / "late", larger)["finalize_reserve_s"] == 180.0  # the build's 5% of 3600 s
    ckpt = json.loads((work / "checkpoint.json").read_text())
    del ckpt["finalize_reserve_s"]  # a checkpoint written before the reserve was recorded takes the profile's
    (work / "checkpoint.json").write_text(json.dumps(ckpt), encoding="utf-8")
    assert fixed.finalize_checkpoint(work, tmp_path / "older", larger)["finalize_reserve_s"] == 360.0


def test_killed_build_can_be_finalized_from_its_latest_checkpoint(tmp_path):
    recs = _pair_records([f"p{i // 2} x{i}" for i in range(8)])
    bindings = {r["id"]: {"status": "unresolved", "class": None, "negated": False, "missing": ["no_date"]} for r in recs}
    seen = []

    def relation(a, b):
        seen.append((a, b))
        if len(seen) == 3:
            raise KeyboardInterrupt  # hard kill, not a budget stop: nothing runs after this
        return "EXCLUDES"

    adapter = _Scripted(bindings, relation)
    out = tmp_path / "sub"
    with pytest.raises(KeyboardInterrupt):
        fixed.build(recs, adapter, out, dict(DEFAULT_PROFILE), checkpoint_every=1)
    work = tmp_path / "sub.build"
    assert work.is_dir() and not out.exists()
    with pytest.raises(FileExistsError):  # a leftover build is never silently overwritten
        fixed.build(recs, adapter, out, dict(DEFAULT_PROFILE))
    summary = fixed.finalize_checkpoint(work, tmp_path / "recovered", dict(DEFAULT_PROFILE))
    assert summary["relations_committed"] == 2 and summary["constraints"] == 2
    assert check_submission(tmp_path / "recovered") == []

"""build --candidates (a curated candidates.jsonl, docs/mentat_hard_contracts.md) and the raw decision log decisions.jsonl:
the file is validated in full and refused under gold/; its relation questions are asked exactly in file order and
orientation, its group questions are the only groups, complement dedup is off (refused at the CLI); the policy records
the file's sha256 and a resume must match it; every build logs each asked question with its raw answer, checkpointed and
truncated on resume like relations.jsonl."""

import json
import sys
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentat_bench import cli, runcell
from mentat_bench.adapters.base import AdapterError, usage
from mentat_bench.harness import fixed
from mentat_bench.manifest import DEFAULT_PROFILE

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_resume import Det, _records  # noqa: E402
from test_runcell import PROFILE, _with_gold  # noqa: E402
from helpers_pa_engine import make_fake_engine  # noqa: E402

PROBS = {"EQUIV": 0.1, "IMPLIES": 0.6, "IMPLIED_BY": 0.1, "EXCLUDES": 0.1, "NONE": 0.1}


class Groups(list):
    """A group decision list carrying per-option probabilities (as the System One child's v3 sends)."""
    probs = {"none": 0.2, "exactly_one": 0.8}


class Rec:
    """Records what it is asked. Relations answer IMPLIES with conf and probs, except NONE for v:m2:* pairs and an
    error for (v:m3:1, v:m4:1); bindings carry probs; groups answer a Groups list."""

    name = "rec"
    supports_groups = True

    def __init__(self, kill_after=None):
        self.relations, self.groups, self.kill_after = [], [], kill_after

    def decide_binding(self, ctx):
        k = int(ctx["id"].split(":")[1][1:])
        return ({"status": "bound", "class": f"c{k}", "negated": ctx["id"].endswith(":2"), "missing": [],
                 "probs": {"bound": 0.9, "unresolved": 0.1}}, usage(self.name))

    def decide_relation(self, a, b):
        self.relations.append((a["id"], b["id"]))
        if self.kill_after is not None and len(self.relations) > self.kill_after:
            raise KeyboardInterrupt("killed")
        if (a["id"], b["id"]) == ("v:m3:1", "v:m4:1"):
            raise AdapterError("boom")
        if a["id"].startswith("v:m2:"):
            return "NONE", usage(self.name)
        return {"relation": "IMPLIES", "conf": 0.7, "probs": PROBS}, usage(self.name)

    def decide_group(self, ctx):
        self.groups.append(ctx)
        ids = [m["id"] for m in ctx["members"]]
        return Groups([{"kind": "exactly_one", "members": ids}]), usage(self.name)


# (a, b) as asked: some against the record order, one repeated
PAIRS = [("v:m5:1", "v:m0:1"), ("v:m0:2", "v:m6:1"), ("v:m2:1", "v:m1:1"), ("v:m3:1", "v:m4:1"), ("v:m6:2", "v:m1:2"),
         ("v:m5:1", "v:m0:1"), ("v:m4:2", "v:m3:2")]
GROUPS = [["v:m4:1", "v:m4:2"], ["v:m1:2", "v:m0:1", "v:m5:1"]]


def _questions(pairs=PAIRS, groups=GROUPS):
    rows = [{"qid": f"b{n:05d}", "op": "decide_binding", "id": rec["id"]} for n, rec in enumerate(_records())]
    rows += [{"qid": f"r{n:05d}", "op": "decide_relation", "a": a, "b": b} for n, (a, b) in enumerate(pairs)]
    rows += [{"qid": f"g{n:05d}", "op": "decide_group", "members": m, "source": "curated"} for n, m in enumerate(groups)]
    return rows


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _log(work):
    return [json.loads(line) for line in (work / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]


def _build(tmp_path, adapter, name="sub", candidates=None, **kwargs):
    kwargs.setdefault("max_relation_drop_frac", 1.0)
    return fixed.build(_records(), adapter, tmp_path / name, dict(DEFAULT_PROFILE), keep_work=True,
                       candidates=candidates, **kwargs)


# ---- validation --------------------------------------------------------------------------------------------------------

IDS = {rec["id"] for rec in _records()}


@pytest.mark.parametrize("bad, match", [
    ({"qid": "x", "op": "decide_thing"}, "op must be one of"),
    ({"qid": "x", "op": "decide_relation", "a": "v:m0:1", "b": "v:m1:1", "extra": 1}, "exactly the keys"),
    ({"qid": "b00000", "op": "decide_relation", "a": "v:m0:1", "b": "v:m1:1"}, "unique"),
    ({"qid": "", "op": "decide_relation", "a": "v:m0:1", "b": "v:m1:1"}, "non-empty"),
    ({"qid": "x", "op": "decide_relation", "a": "v:m0:1", "b": "v:m0:1"}, "a != b"),
    ({"qid": "x", "op": "decide_relation", "a": "v:m0:1", "b": 3}, "strings"),
    ({"qid": "x", "op": "decide_relation", "a": "v:m0:1", "b": "v:zz:1"}, "not a corpus outcome"),
    ({"qid": "x", "op": "decide_binding", "id": "v:m0:1"}, "second binding question"),
    ({"qid": "x", "op": "decide_group", "members": ["v:m0:1"], "source": "curated"}, "distinct members"),
    ({"qid": "x", "op": "decide_group", "members": ["v:m0:1", "v:m0:1"], "source": "curated"}, "distinct members"),
    ({"qid": "x", "op": "decide_group", "members": ["v:m0:1", "v:m1:1"], "source": "s1"}, "source 'curated'"),
    ({"qid": "x", "op": "decide_group", "members": [f"v:m{k}:1" for k in range(4)], "source": "reading"}, "exactly 5 members"),
    ({"qid": "x", "op": "decide_group", "members": [f"v:m{k}:1" for k in range(fixed.GROUP_MAX_MEMBERS + 1)],
      "source": "curated"}, "distinct members"),
])
def test_a_bad_question_is_refused(tmp_path, bad, match):
    path = _write(tmp_path / "candidates.jsonl", [*_questions(), bad])
    with pytest.raises(ValueError, match=match):
        fixed.load_candidates(path, IDS)


def test_a_valid_file_loads_in_order_with_its_sha256(tmp_path):
    import hashlib
    path = _write(tmp_path / "candidates.jsonl", _questions())
    questions, sha = fixed.load_candidates(path, IDS)
    assert questions == _questions() and sha == hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "bad.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not JSON"):
        fixed.load_candidates(tmp_path / "bad.jsonl")


def test_a_candidates_file_under_gold_is_refused(tmp_path):
    path = _write(tmp_path / "fixture" / "gold" / "candidates.jsonl", _questions())
    with pytest.raises(ValueError, match="gold directory"):
        fixed.load_candidates(path)
    with pytest.raises(ValueError, match="gold directory"):
        _build(tmp_path, Rec(), candidates=path)
    assert not (tmp_path / "sub.build").exists()  # refused before anything was written


def test_a_build_refuses_an_unknown_outcome_before_any_call(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions(pairs=[("v:m0:1", "v:zz:1")]))
    adapter = Rec()
    with pytest.raises(ValueError, match="not a corpus outcome"):
        fixed.build(iter(_records()), adapter, tmp_path / "sub", dict(DEFAULT_PROFILE), candidates=path)  # a stream
    assert not (tmp_path / "sub.build").exists() and adapter.relations == []


class _Replay(Rec):
    def extra_theory(self):
        return {"constraints": []}


def test_a_full_replay_adapter_refuses_candidates(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions())
    with pytest.raises(ValueError, match="full-replay"):
        _build(tmp_path, _Replay(), candidates=path)
    assert not (tmp_path / "sub.build").exists()


def test_every_curated_group_is_asked_whatever_the_group_budget_share(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions())
    adapter = Rec()
    summary = _build(tmp_path, adapter, candidates=path, group_budget_frac=1e-9, max_group_calls=1)
    assert len(adapter.groups) == len(GROUPS) and summary["group_budget_stopped"] is False
    policy = json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["group_budget_frac"] == 1.0 and policy["max_group_calls"] == len(GROUPS)


class _Evidence(Rec):
    def decide_binding(self, ctx):
        decision, use = super().decide_binding(ctx)
        evidence = ["ax:hidden", f"src:outcome:{ctx['id']}"] if ctx["id"].endswith(":1") else "ax:malformed"
        return {**decision, "evidence": evidence}, use


def test_the_logged_binding_keeps_no_gold_only_ref(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions())
    _build(tmp_path, _Evidence(), candidates=path)
    text = (tmp_path / "sub.build" / "decisions.jsonl").read_text(encoding="utf-8")
    assert "ax:" not in text
    first = _log(tmp_path / "sub.build")[0]
    assert first["decision"]["evidence"] == ["src:outcome:v:m0:1"]


# ---- the build with candidates -----------------------------------------------------------------------------------------

def test_pairs_are_asked_in_file_order_and_orientation_and_groups_only_from_the_file(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions())
    adapter = Rec()
    summary = _build(tmp_path, adapter, candidates=path, checkpoint_every=3)  # complement_dedup defaults on: forced off
    retries = [("v:m3:1", "v:m4:1")] * 2  # the failing pair is asked three times in all
    assert adapter.relations == PAIRS[:4] + retries + PAIRS[4:]
    assert [[m["id"] for m in ctx["members"]] for ctx in adapter.groups] == GROUPS
    assert {ctx["source"] for ctx in adapter.groups} == {"file"}
    work = tmp_path / "sub.build"
    raw = array("I")
    raw.frombytes((work / "pairs.bin").read_bytes())
    ids = [rec["id"] for rec in _records()]
    assert [(ids[raw[k]], ids[raw[k + 1]]) for k in range(0, len(raw), 2)] == PAIRS
    assert summary["candidate_pairs"] == len(PAIRS) and summary["dedup_representatives"] == 0
    policy = json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["candidates_sha256"] == fixed.load_candidates(path)[1] and policy["candidates_gold_informed"] is True
    assert policy["complement_dedup"] is False and policy["group_sources"] == ["file"]


def test_the_decision_log_holds_every_question_as_asked_with_its_raw_answer(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions())
    _build(tmp_path, Rec(), candidates=path, checkpoint_every=3)
    rows = _log(tmp_path / "sub.build")
    assert all(list(row) == ["qid", "op", "a", "b", "id", "members", "decision", "conf", "probs", "error"] for row in rows)
    bindings = [row for row in rows if row["op"] == "decide_binding"]
    assert [(row["qid"], row["id"]) for row in bindings] == [(f"b{n:05d}", rec["id"]) for n, rec in enumerate(_records())]
    assert bindings[0]["decision"]["status"] == "bound" and bindings[0]["probs"] == {"bound": 0.9, "unresolved": 0.1}
    relations = [row for row in rows if row["op"] == "decide_relation"]
    assert [(row["qid"], row["a"], row["b"]) for row in relations] == [(f"r{n:05d}", a, b) for n, (a, b) in enumerate(PAIRS)]
    first, none, failed = relations[0], relations[2], relations[3]
    assert first["decision"] == {"relation": "IMPLIES", "conf": 0.7, "probs": PROBS}
    assert (first["conf"], first["probs"], first["error"]) == (0.7, PROBS, None)
    assert none["decision"] == {"relation": "NONE"} and none["conf"] is None and none["probs"] is None
    assert failed["decision"] is None and "boom" in failed["error"] and failed["error"].startswith("AdapterError")
    groups = [row for row in rows if row["op"] == "decide_group"]
    assert [(row["qid"], row["members"]) for row in groups] == [("g00000", GROUPS[0]), ("g00001", GROUPS[1])]
    assert groups[1]["decision"] == {"groups": [{"kind": "exactly_one", "members": GROUPS[1]}]}
    assert groups[1]["probs"] == Groups.probs


def test_a_default_build_logs_every_asked_question_with_null_qids(tmp_path):
    summary = _build(tmp_path, Det())
    rows = _log(tmp_path / "sub.build")
    assert all(row["qid"] is None for row in rows)
    assert sum(row["op"] == "decide_binding" for row in rows) == summary["outcomes"]
    assert sum(row["op"] == "decide_relation" for row in rows) == summary["relations_committed"]  # derived rows: not logged
    assert sum(row["op"] == "decide_group" for row in rows) == summary["group_candidates"]
    policy = json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["candidates_sha256"] is None and policy["candidates_gold_informed"] is False


def test_a_resume_needs_the_same_candidates_file(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions())
    with pytest.raises(KeyboardInterrupt):
        _build(tmp_path, Rec(kill_after=4), candidates=path, checkpoint_every=2)
    other = _write(tmp_path / "other.jsonl", _questions(pairs=PAIRS[:3]))
    with pytest.raises(ValueError, match="candidates_sha256"):
        _build(tmp_path, Rec(), candidates=other, checkpoint_every=2, resume=True)
    with pytest.raises(ValueError, match="cannot resume"):  # without the file (its group source differs first)
        _build(tmp_path, Rec(), checkpoint_every=2, resume=True)


def test_a_resume_mid_relations_truncates_the_log_and_ends_like_the_uninterrupted_build(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions())
    _build(tmp_path, Rec(), name="full", candidates=path, checkpoint_every=2)
    with pytest.raises(KeyboardInterrupt):
        _build(tmp_path, Rec(kill_after=5), name="killed", candidates=path, checkpoint_every=2)
    work = tmp_path / "killed.build"
    ckpt = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    assert ckpt["phase"] == "relations" and 0 < ckpt["pairs_considered"] < ckpt["pairs_total"]
    assert ckpt["decisions_bytes"] == (work / "decisions.jsonl").stat().st_size
    with open(work / "decisions.jsonl", "ab") as fh:
        fh.write(b'{"qid": "r00004", "op": "decide_rel')  # a torn tail past the checkpoint
    _build(tmp_path, Rec(), name="killed", candidates=path, checkpoint_every=2, resume=True)
    assert (work / "decisions.jsonl").read_bytes() == (tmp_path / "full.build" / "decisions.jsonl").read_bytes()
    for name in ("theory.json", "bindings.jsonl", "provenance.jsonl"):
        assert (tmp_path / "killed" / name).read_bytes() == (tmp_path / "full" / name).read_bytes()


def test_a_legacy_checkpoint_without_the_log_still_resumes(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        _build(tmp_path, Det(kill_after=6), checkpoint_every=4)
    work = tmp_path / "sub.build"
    ckpt = json.loads((work / "checkpoint.json").read_text(encoding="utf-8"))
    for key in ("decisions_bytes",):
        ckpt.pop(key)
    for key in ("candidates_sha256", "candidates_gold_informed"):
        ckpt["policy"].pop(key)
    (work / "checkpoint.json").write_text(json.dumps(ckpt), encoding="utf-8")
    (work / "decisions.jsonl").unlink()
    summary = _build(tmp_path, Det(), checkpoint_every=4, resume=True)
    assert all(row["op"] == "decide_relation" for row in _log(work))  # only what the resume asked
    assert summary["decisions_complete"] is False  # scorers know the log is partial
    assert json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))["decisions_complete"] is False


def test_a_build_with_its_whole_log_does_not_flag_it(tmp_path):
    summary = _build(tmp_path, Det())
    assert "decisions_complete" not in summary
    assert "decisions_complete" not in json.loads((tmp_path / "sub" / "build_policy.json").read_text(encoding="utf-8"))


# ---- reading questions (source "reading") ------------------------------------------------------------------------------

# u -> the pick of each reading (None: the reading emits nothing), as the reading child answers it
READINGS = {"v:m0:1": {"base": "v:m1:1", "alt": "v:m2:1"}, "v:m2:2": {"base": None, "alt": "v:m0:1"}}
READING_ROWS = [  # members [u, o1..o4]; the alt readings of the two rows contradict each other (c0 = c2 and c2 = not c0)
    {"qid": "g00000", "op": "decide_group", "members": ["v:m0:1", "v:m1:1", "v:m2:1", "v:m3:1", "v:m4:1"], "source": "reading"},
    {"qid": "g00001", "op": "decide_group", "members": ["v:m2:2", "v:m0:1", "v:m1:1", "v:m3:1", "v:m4:1"], "source": "reading"},
]


class Reading(Rec):
    """Answers a reading row with two ordered chains per reading that picks an option: [u, c] and [c, u], scoped to that
    reading. Curated groups are answered as Rec answers them. `picks` is the answer table (u -> reading -> option)."""

    picks = READINGS

    def decide_group(self, ctx):
        if ctx["source"] != "reading":
            return super().decide_group(ctx)
        self.groups.append(ctx)
        u = ctx["members"][0]["id"]
        out = []
        for reading, pick in self.picks[u].items():
            if pick is None:
                continue
            scope = {"axis": "reading", "values": [reading], "of": ["alt", "base"]}
            out += [{"kind": "ordered_chain", "members": [u, pick], "scope": scope},
                    {"kind": "ordered_chain", "members": [pick, u], "scope": scope}]
        return out, usage(self.name)


def _satisfiable_per_interpretation(out):
    """Each interpretation of a written submission is satisfiable: the bindings and the 2-CNF constraints that hold in it
    (implications, and the at most one of two outcomes)."""
    theory = json.loads((out / "theory.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (out / "bindings.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    names = theory["interpretations"]
    index = {row["id"]: i for i, row in enumerate(rows)}
    units = []
    for con in theory["constraints"]:
        expr = con["expr"]
        if expr["op"] == "implies":
            a, b = index[expr["a"]["id"]], index[expr["b"]["id"]]
            clause = (2 * a + 1, 2 * b)
        else:  # the at most one of two outcomes: not a or not b
            assert expr["op"] == "atmost" and expr["k"] == 1 and len(expr["args"]) == 2
            a, b = (index[arg["id"]] for arg in expr["args"])
            clause = (2 * a + 1, 2 * b + 1)
        mask = -1 if con["interps"] is None else sum(1 << names.index(name) for name in con["interps"])
        units.append(fixed._Unit(con["id"], None, mask, [clause], False, "ordered_chain"))
    check = fixed._BaseCheck(SimpleNamespace(ids=list(index), rows=lambda: rows), units, len(names))
    return {name: not check.unsat(len(units), set(), i) for i, name in enumerate(names)}


def test_reading_rows_compile_scoped_chains_and_settle_the_conflicting_reading(tmp_path):
    path = _write(tmp_path / "candidates.jsonl", _questions(pairs=[], groups=[]) + READING_ROWS)
    adapter = Reading()
    summary = _build(tmp_path, adapter, candidates=path)
    assert [ctx["source"] for ctx in adapter.groups] == ["reading", "reading"]
    assert summary["admit_mode"] == "admit" and summary["group_candidates"] == 2
    theory = json.loads((tmp_path / "sub" / "theory.json").read_text(encoding="utf-8"))
    assert theory["interpretations"] == ["reading=alt", "reading=base"]  # both readings are declared
    constraints = {c["id"]: c for c in theory["constraints"]}
    # the first row's pairs (g0000000 base, g0000002 and g0000003 alt) and the second row's base-less alt pair hold; the
    # second row's alt pair (g0000004, g0000005) contradicts the first row's alt pair, so the whole pair loses the alt bit
    assert sorted(constraints) == ["g0000000.0", "g0000001.0", "g0000002.0", "g0000003.0"]
    assert constraints["g0000000.0"]["interps"] == ["reading=base"] and constraints["g0000000.0"]["expr"]["a"]["id"] == "v:m0:1"
    assert all(constraints[cid]["interps"] == ["reading=alt"] for cid in ("g0000002.0", "g0000003.0"))
    assert summary["groups_dropped"] == 2 and summary["base_repaired"] is True
    assert summary["settle_dropped"] == 2 and summary["settle_cleared_bits"] == 0
    assert _satisfiable_per_interpretation(tmp_path / "sub") == {"reading=alt": True, "reading=base": True}


# ---- CLI and runcell ---------------------------------------------------------------------------------------------------

def _corpus(tmp_path):
    path = tmp_path / "corpus.jsonl"
    path.write_text("".join(json.dumps({"id": r["id"], "text": r["question"], "label": r["label"],
                                        "event_title": r["event_title"]}) + "\n" for r in _records()), encoding="utf-8")
    return path


def test_the_cli_refuses_complement_dedup_unknown_ids_and_gold_paths_before_the_adapter(tmp_path, capsys):
    corpus = _corpus(tmp_path)
    good = _write(tmp_path / "candidates.jsonl", _questions())
    base = ["build", "--adapter", "empty", "--corpus", str(corpus), "--out", str(tmp_path / "out")]
    assert cli.main([*base, "--candidates", str(good), "--complement-dedup"]) == cli.EXIT_USAGE
    assert "--complement-dedup does not apply" in capsys.readouterr().err
    unknown = _write(tmp_path / "unknown.jsonl", _questions(pairs=[("v:m0:1", "v:zz:1")]))
    assert cli.main([*base, "--candidates", str(unknown)]) == cli.EXIT_USAGE
    assert "not a corpus outcome" in capsys.readouterr().err
    assert cli.main(["build", "--adapter", "oracle", "--gold", str(tmp_path), "--corpus", str(corpus), "--out",
                     str(tmp_path / "out"), "--candidates", str(good)]) == cli.EXIT_USAGE
    assert "full replay" in capsys.readouterr().err
    gold = _write(tmp_path / "gold" / "candidates.jsonl", _questions())
    assert cli.main([*base, "--candidates", str(gold)]) == cli.EXIT_USAGE
    assert not (tmp_path / "out.build").exists() and not (tmp_path / "out").exists()
    assert cli.main([*base, "--candidates", str(good), "--no-complement-dedup"]) == 0
    capsys.readouterr()
    policy = json.loads((tmp_path / "out" / "build_policy.json").read_text(encoding="utf-8"))
    assert policy["candidates_gold_informed"] is True and policy["complement_dedup"] is False


@pytest.mark.parametrize("importable", [False, True])
def test_runcell_passes_candidates_and_scores_the_decision_log(tmp_path, monkeypatch, importable):
    fixture = _with_gold(_fixture_with_records(tmp_path), extra=False)
    candidates = _write(fixture / "candidates.jsonl", _questions())
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    calls = []
    real_run = runcell.subprocess.run

    def run(argv, **kwargs):
        if runcell.DECISION_SCORING not in argv:
            return real_run(argv, **kwargs)
        calls.append(argv)
        return runcell.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(runcell.subprocess, "run", run)
    # a module that is not there, or one that is (the scorer lands from another stream; its run is faked here)
    monkeypatch.setattr(runcell, "DECISION_SCORING", "mentat_bench.manifest" if importable else "mentat_bench.no_such_module")
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, [], candidates=candidates) == 0
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["candidates"] == str(candidates) and "--candidates" in cell["build_flags"]
    assert (out / "submission.build" / "decisions.jsonl").is_file()
    if not importable:
        assert cell["decision_scoring"] == "not_importable" and calls == [] and "score_decisions" not in cell["steps"]
        assert cell["status"] == "incomplete" and cell["incomplete"] is True  # the suite was not scored
    else:
        assert cell["decision_scoring"] == "ok" and cell["steps"]["score_decisions"] == 0
        argv = calls[0]
        assert argv[argv.index("--decisions") + 1] == str(out / "submission.build" / "decisions.jsonl")
        assert argv[argv.index("--labels") + 1] == str(fixture / "gold" / "suite_labels.jsonl")
        assert argv[argv.index("--candidates") + 1] == str(candidates)
        assert argv[argv.index("--out") + 1] == str(out / "score_decisions.json")
        assert cell["status"] == "complete" and cell["incomplete"] is False


def test_a_failed_decision_scoring_makes_the_cell_incomplete(tmp_path, monkeypatch):
    fixture = _with_gold(_fixture_with_records(tmp_path), extra=False)
    candidates = _write(fixture / "candidates.jsonl", _questions())
    monkeypatch.setenv("MB_ENGINE", str(make_fake_engine(tmp_path / "bin")))
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    real_run = runcell.subprocess.run
    monkeypatch.setattr(runcell, "DECISION_SCORING", "mentat_bench.manifest")
    monkeypatch.setattr(runcell.subprocess, "run", lambda argv, **kw: runcell.subprocess.CompletedProcess(argv, 1)
                        if runcell.DECISION_SCORING in argv else real_run(argv, **kw))
    out = tmp_path / "cell"
    assert runcell.run_cell(fixture, "empty", [], out, PROFILE, [], candidates=candidates) == 1
    cell = json.loads((out / "cell.json").read_text(encoding="utf-8"))
    assert cell["decision_scoring"] == "failed" and cell["status"] == "incomplete"
    assert cell["failed_step"] == "score_decisions"


def test_every_profile_validates_and_mentat_hard_v1_has_its_deadline():
    from mentat_bench.manifest import load_profile
    profiles = Path(__file__).resolve().parents[1] / "profiles"
    for path in sorted(profiles.glob("*.json")):
        load_profile(path)
    hard = load_profile(profiles / "mentat-hard-v1.json")
    toy = load_profile(profiles / "toy-local-v0.json")
    assert hard["build_deadline_s"] == 5400.0 and {**hard, "build_deadline_s": toy["build_deadline_s"]} == toy


def _fixture_with_records(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "gold").mkdir(parents=True)
    _corpus(tmp_path).replace(fixture / "corpus.jsonl")
    (fixture / "queries.jsonl").write_text(json.dumps({"family": "closure", "qid": "c1", "hyp": []}) + "\n", encoding="utf-8")
    return fixture

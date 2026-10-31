"""The certificate cap in `evaluate` (F4): the pinned profile's max_certificate_refs reaches `mb-engine check` by flag,
a gold profile that disagrees is refused (exit 6), and an unlimited pinned profile is reported as certificate_cap null.

The engine is stubbed: these tests pin what evaluate passes and refuses, not what the engine does with it.
"""

import json
from pathlib import Path

import pytest

from mentat_bench import cli, engine, ir
from mentat_bench.manifest import DEFAULT_PROFILE
from mentat_bench.seal import seal

EMPTY_USAGE = {"calls": [], "wall_seconds": 0.0, "peak_rss_mb": 0.0}


def _setup(tmp_path: Path, pinned_cap, gold_profile):
    """A sealed submission whose profile.json is the pinned profile; `gold_profile` is None (no file), or the dict
    written to gold/profile.json."""
    pinned = dict(DEFAULT_PROFILE)
    if pinned_cap is not None:
        pinned["max_certificate_refs"] = pinned_cap
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "bindings.jsonl").write_text("", encoding="utf-8")
    ir.write_json(sub / "profile.json", pinned)
    ir.write_json(sub / "usage.json", EMPTY_USAGE)
    digest = seal(sub)["digest"]
    pinned_path = tmp_path / "pinned.json"
    ir.write_json(pinned_path, pinned)
    gold = tmp_path / "gold"
    gold.mkdir()
    if gold_profile is not None:
        ir.write_json(gold / "profile.json", gold_profile)
    queries = tmp_path / "q.jsonl"
    ir.write_jsonl(queries, [{"qid": "a", "family": "closure"}])
    return ["evaluate", "--submission", str(sub), "--expected-digest", digest, "--profile", str(pinned_path),
            "--queries", str(queries), "--gold", str(gold), "--out", str(tmp_path / "run")]


@pytest.fixture()
def stub_engine(monkeypatch):
    seen = {}

    def fake_eval(submission, queries, answers, timeout_s=None):
        Path(answers).write_text("", encoding="utf-8")

    def fake_check(gold, queries, answers, verdicts, timeout_s=None, max_certificate_refs=None):
        seen["max_certificate_refs"] = max_certificate_refs
        Path(verdicts).write_text("", encoding="utf-8")

    monkeypatch.setattr(engine, "validate", lambda *a, **k: None)
    monkeypatch.setattr(engine, "engine_budget_s", lambda *a, **k: 10.0)
    monkeypatch.setattr(engine, "eval_submission", fake_eval)
    monkeypatch.setattr(engine, "check", fake_check)
    return seen


def _profile(**extra):
    return dict(DEFAULT_PROFILE, **extra)


def test_pinned_cap_reaches_check_and_is_reported(tmp_path, stub_engine, capsys):
    argv = _setup(tmp_path, 5, None)
    assert cli.main(argv) == 0
    assert stub_engine["max_certificate_refs"] == 5
    assert json.loads(capsys.readouterr().out)["certificate_cap"] == 5


def test_unlimited_pinned_profile_is_reported_as_null(tmp_path, stub_engine, capsys):
    argv = _setup(tmp_path, None, None)
    assert cli.main(argv) == 0
    assert stub_engine["max_certificate_refs"] is None
    assert json.loads(capsys.readouterr().out)["certificate_cap"] is None


def test_gold_profile_with_the_same_cap_is_accepted(tmp_path, stub_engine):
    assert cli.main(_setup(tmp_path, 5, _profile(max_certificate_refs=5))) == 0
    assert stub_engine["max_certificate_refs"] == 5


def test_gold_profile_without_the_key_takes_the_pinned_cap(tmp_path, stub_engine):
    assert cli.main(_setup(tmp_path, 5, _profile())) == 0
    assert stub_engine["max_certificate_refs"] == 5


def test_a_gold_profile_with_a_different_cap_is_refused(tmp_path, stub_engine, capsys):
    assert cli.main(_setup(tmp_path, 5, _profile(max_certificate_refs=6))) == cli.EXIT_VALIDATE
    assert "differs" in capsys.readouterr().err
    assert "max_certificate_refs" not in stub_engine


def test_a_gold_cap_against_an_unlimited_pinned_profile_is_refused(tmp_path, stub_engine):
    assert cli.main(_setup(tmp_path, None, _profile(max_certificate_refs=6))) == cli.EXIT_VALIDATE
    assert "max_certificate_refs" not in stub_engine


def test_an_invalid_gold_cap_is_refused_as_an_invalid_profile(tmp_path, stub_engine):
    assert cli.main(_setup(tmp_path, 5, _profile(max_certificate_refs=0))) == cli.EXIT_VALIDATE


def test_engine_check_passes_the_flag_only_when_a_cap_is_given(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(engine, "_run", lambda args, budget: calls.append(list(args)))
    monkeypatch.setattr(engine, "_expect_lines", lambda *a: None)
    engine.check("g", "q", "a", "v", timeout_s=1.0, max_certificate_refs=7)
    engine.check("g", "q", "a", "v", timeout_s=1.0)
    assert calls[0][-2:] == ["--max-certificate-refs", "7"]
    assert calls[1] == ["check", "g", "q", "a", "v"]

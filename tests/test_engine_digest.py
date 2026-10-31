"""mb-engine digest line: `eval` reports the SHA-256 of the files it read and wrote, equal to hashlib over the raw bytes
(BOM included), and EngineError carries the engine's error codes.

The engine-backed tests need the mb-engine binary (MB_ENGINE or target/release) and skip otherwise.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from mentat_bench import engine

REPO = Path(__file__).resolve().parents[1]
GOLD = REPO / "fixtures" / "toy10k" / "gold"
QUERIES = REPO / "fixtures" / "toy10k" / "queries.jsonl"
SUBMISSION_FILES = ("theory.json", "bindings.jsonl", "provenance.jsonl")
needs_engine = pytest.mark.skipif(not engine.engine_available(), reason="mb-engine not built: set MB_ENGINE")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _eval(gold: Path, out_dir: Path) -> tuple[Path, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    answers = out_dir / "answers.jsonl"
    return answers, engine.eval_submission(gold, QUERIES, answers)


def _assert_digests(digest: dict, gold: Path, answers: Path) -> None:
    assert set(digest) == {"mb_engine_digests", "version"}
    assert digest["version"]
    assert digest["mb_engine_digests"] == {
        "theory.json": _sha(gold / "theory.json"),
        "bindings.jsonl": _sha(gold / "bindings.jsonl"),
        "provenance.jsonl": _sha(gold / "provenance.jsonl"),
        "queries": _sha(QUERIES),
        "answers": _sha(answers),
    }


@needs_engine
def test_digest_line_matches_hashlib_on_toy10k_gold(tmp_path):
    answers, digest = _eval(GOLD, tmp_path)
    _assert_digests(digest, GOLD, answers)


@needs_engine
def test_digest_line_hashes_the_bom_bytes_and_the_answers_stay_identical(tmp_path):
    bom_gold = tmp_path / "gold"
    shutil.copytree(GOLD, bom_gold)
    for name in SUBMISSION_FILES:
        path = bom_gold / name
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    answers, digest = _eval(bom_gold, tmp_path / "run")
    _assert_digests(digest, bom_gold, answers)
    assert _sha(bom_gold / "theory.json") != _sha(GOLD / "theory.json")
    assert answers.read_bytes() == (GOLD / "answers.jsonl").read_bytes()


@needs_engine
def test_reevaluating_toy10k_gold_reproduces_committed_answers_byte_for_byte(tmp_path):
    answers, _ = _eval(GOLD, tmp_path)
    assert answers.read_bytes() == (GOLD / "answers.jsonl").read_bytes()


@needs_engine
def test_inconsistent_base_raises_with_its_error_code(tmp_path):
    bad = tmp_path / "bad"
    shutil.copytree(GOLD, bad)
    theory = json.loads((bad / "theory.json").read_text(encoding="utf-8"))
    ref = json.loads((bad / "provenance.jsonl").read_text(encoding="utf-8").splitlines()[0])["ref"]
    theory["constraints"].append({"id": "XA_FALSE", "expr": {"op": "false"}, "interps": None, "provenance": [ref]})
    (bad / "theory.json").write_text(json.dumps(theory), encoding="utf-8")
    with pytest.raises(engine.EngineError) as info:
        engine.eval_submission(bad, QUERIES, tmp_path / "answers.jsonl")
    assert info.value.codes == ["inconsistent_base"]
    assert info.value.returncode == 1


@needs_engine
def test_profile_json_digest_is_listed_when_the_profile_is_used(tmp_path):
    gold = tmp_path / "gold"
    shutil.copytree(GOLD, gold)
    (gold / "profile.json").write_text("{}", encoding="utf-8")
    answers, digest = _eval(gold, tmp_path / "run")
    assert digest["mb_engine_digests"]["profile.json"] == _sha(gold / "profile.json")
    assert len(digest["mb_engine_digests"]) == 6


@needs_engine
def test_validate_error_codes_come_from_its_stdout(tmp_path):
    bad = tmp_path / "bad"
    shutil.copytree(GOLD, bad)
    theory = json.loads((bad / "theory.json").read_text(encoding="utf-8"))
    ref = json.loads((bad / "provenance.jsonl").read_text(encoding="utf-8").splitlines()[0])["ref"]
    theory["constraints"].append({"id": "XA_UNKNOWN", "expr": {"op": "ref", "name": "NO_SUCH_ALIAS"}, "interps": None,
                                  "provenance": [ref]})
    (bad / "theory.json").write_text(json.dumps(theory), encoding="utf-8")
    with pytest.raises(engine.EngineError) as info:
        engine.validate(bad)
    assert "unknown_alias" in info.value.codes
    assert info.value.returncode == 1


def test_error_codes_and_digest_line_parse_leniently():
    assert engine._error_codes('stage\tload\t0.1s\n[{"code": "a", "message": "x"}, {"code": "b"}]\n') == ["a", "b"]
    assert engine._error_codes("mb-engine: something went wrong") == []
    assert engine._error_codes("") == []
    assert engine._digest_line("") is None
    # U+2028 inside an echoed message is not a line break for the engine's output.
    assert engine._error_codes('[{"code": "a", "message": "x y"}]\n') == ["a"]


def _digest(theory_hex: str = "a" * 64) -> dict:
    keys = ("theory.json", "bindings.jsonl", "provenance.jsonl", "queries", "answers")
    return {"mb_engine_digests": {k: theory_hex for k in keys}, "version": "0.1.0"}


def test_digest_line_must_carry_five_lowercase_sha256_values_and_the_last_one_counts():
    good, other = _digest("a" * 64), _digest("b" * 64)
    assert engine._digest_line(json.dumps(good) + "\n") == good
    # The last line carrying mb_engine_digests is used.
    assert engine._digest_line(json.dumps(good) + "\n" + json.dumps(other) + "\n") == other
    assert engine._digest_line(json.dumps(other) + "\n" + json.dumps(good) + "\n" + "junk\n") == good
    # A malformed last line gives None even when an earlier line is good.
    assert engine._digest_line(json.dumps(good) + "\n" + json.dumps(_digest("A" * 64)) + "\n") is None
    assert engine._digest_line(json.dumps(_digest("a" * 63))) is None
    missing = _digest()
    del missing["mb_engine_digests"]["answers"]
    assert engine._digest_line(json.dumps(missing)) is None
    assert engine._digest_line('{"mb_engine_digests": 5, "version": "0.1.0"}') is None

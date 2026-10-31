"""Golden outputs of origin/main 6e2ae87 for a version 2 input (tests/golden/v2): the decision scores of two runs, and league_ci's
JSON and Markdown over them, must be byte-identical with this code. The golden files were written by the origin/main code from the
helpers of tests/test_league_ci.py (a three-league fixture with compound groups and reading questions; "wrong" gives four wrong
answers). Line endings are compared as LF: the text files are LF in the repository, and a Windows write may leave CRLF."""

import json
import sys
from pathlib import Path

from mentat_bench import decision_scoring as DS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "eval"))
import league_ci as LC  # noqa: E402

GOLD = Path(__file__).resolve().parent / "golden" / "v2"
STAMP = {"name": "golden_v2", "version": "2", "status": "active", "content_hash": "ab" * 32}  # the golden runs' fixture_stamp.json


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def test_the_version_2_decision_scores_are_byte_identical_to_origin_main(tmp_path):
    fixture = GOLD / "fixture"
    for run in (GOLD / "run", GOLD / "wrong"):
        out = tmp_path / f"{run.name}.json"
        assert DS.main(["--decisions", str(run / "submission.build" / "decisions.jsonl"),
                        "--labels", str(fixture / "gold" / "suite_labels.jsonl"),
                        "--candidates", str(fixture / "candidates.jsonl"), "--fixture", str(fixture), "--out", str(out)]) == 0
        assert _lf(out.read_bytes()) == _lf((run / "score_decisions.json").read_bytes())


def test_the_version_2_league_ci_json_and_markdown_are_byte_identical_to_origin_main():
    doc = LC.run_ci(GOLD / "fixture", [GOLD / "run", GOLD / "wrong"], STAMP, B=50, seed=0)
    assert doc["layout"] == "relation-headline/1" and "relation_group_mcc" not in doc["runs"]["wrong"]["metrics"]
    assert (json.dumps(doc, indent=1, sort_keys=True, allow_nan=False) + "\n").encode("utf-8") == _lf(
        (GOLD / "league_ci.json").read_bytes())
    assert LC.markdown(doc).encode("utf-8") == _lf((GOLD / "league_ci.md").read_bytes())

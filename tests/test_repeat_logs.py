"""The progress-log formats the repeat collector reads, pinned with verbatim lines copied from the real logs of the four run folders
(20261030-test, 20261101-cuda-repeat, 20261101-repeat, 20261101-cuda-shuffle). Each test writes the lines to a temporary log in the
order they appear in the real file, then asks last_rc for the exit code the collector would use. The real folders are read only
when they are present (the private machine); the verbatim tests always run."""

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_bundle_v011_logs", ROOT / "results" / "v0.1.1" / "build_bundle.py")
BB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BB)

# the private run folders, if this machine has them: MB_PRIVATE_RUNS (unset: the real-folder test is skipped)
REAL_RUNS = Path(os.environ["MB_PRIVATE_RUNS"]) if os.environ.get("MB_PRIVATE_RUNS") else None

# 20261101-cuda-shuffle/progress.log, lines 1-10 and 14-20 (the first attempt failed with rc=127 for d3:9b, kev and d3:4b; the second
# attempt finished d3:9b and d3:4b with rc=0 and kev with rc=0 at 04:17:42)
CUDA_SHUFFLE = [
    "== attempt 03:49:51",
    "== d3:9b-q8_0 shuffle gpu0 start 03:50:01 commit=c8616c5",
    "== d3:9b-q8_0 shuffle 03:50:01 done rc=127",
    "== kev:9b-bf16 shuffle gpu1 start 03:50:01 commit=c8616c5",
    "== kev:9b-bf16 shuffle 03:50:01 done rc=127",
    "== d3:4b-q8_0 shuffle gpu0 start 03:50:01 commit=c8616c5",
    "== d3:4b-q8_0 shuffle 03:50:01 done rc=127",
    "== all done 03:50:01",
    "== attempt 03:50:44",
    "== d3:9b-q8_0 shuffle gpu0 start 03:50:54 commit=c8616c5",
    "== kev:9b-bf16 shuffle gpu1 start 03:50:54 commit=c8616c5",
    "== d3:9b-q8_0 shuffle 04:12:40 done rc=0",
    "== d3:4b-q8_0 shuffle gpu0 start 04:12:40 commit=c8616c5",
    "== kev:9b-bf16 shuffle 04:17:42 done rc=0",
    "== d3:4b-q8_0 shuffle 04:29:11 done rc=0",
    "== all done 04:29:11",
    "== attempt shuffle-all 05:14:46",
    "== clef:27b-q8_0 shuffle gpu0 start 05:14:56 commit=c8616c5",
    "== d3:27b-q8_0 shuffle gpu1 start 05:14:56 commit=c8616c5",
    "== d3:27b-q8_0 shuffle 06:31:12 done rc=0",
    "== clef:27b-q8_0 shuffle 06:34:57 done rc=0",
]

# 20261030-test/progress.log, lines 1-3: the published run has no tag word; it starts with 'test' and the floor
PUBLISHED = [
    "== d3:27b-q8_0 test floor=--relation-floor 0.0010576248168946423 13:44:32 start commit=0bff47f",
    "== d3:27b-q8_0 15:01:00 done rc=0",
]

# 20261101-cuda-repeat/progress.log, lines 1-7 and 12-15 (the repeats run on two lanes; the lines interleave)
CUDA_REPEAT = [
    "== attempt 02:16:19",
    "== d3:9b-q8_0 r2 gpu0 start 02:16:29 commit=eef88eb",
    "== d3:9b-q8_0 r3 gpu1 start 02:16:29 commit=eef88eb",
    "== d3:9b-q8_0 r2 02:38:42 done rc=0",
    "== d3:9b-q8_0 r3 02:38:42 done rc=0",
    "== kev:9b-bf16 r2 gpu0 start 02:38:42 commit=eef88eb",
    "== kev:9b-bf16 r3 gpu1 start 02:38:42 commit=eef88eb",
    "== kev:9b-bf16 r3 03:05:12 done rc=0",
    "== d3:4b-q8_0 r3 gpu1 start 03:05:12 commit=eef88eb",
    "== kev:9b-bf16 r2 03:14:18 done rc=0",
    "== d3:4b-q8_0 r2 gpu0 start 03:14:18 commit=eef88eb",
    "== d3:4b-q8_0 r3 03:21:53 done rc=0",
    "== d3:4b-q8_0 r2 03:30:45 done rc=0",
    "== kev:9b-bf16 shuffle gpu1 start 03:49:21 commit=eef88eb",
    "== kev:9b-bf16 shuffle 03:49:24 done rc=3",
    "== all done (both lanes finished by 03:30:45; script stopped manually after a wait-on-servers bug) 03:49:27",
]

# 20261101-repeat/progress.log (the Vulkan folder): the d3:4b r2 attempt that ran second finished with rc=3, after an rc=0 attempt
VULKAN_TAIL = [
    "== d3:4b-q8_0 r2 gpu0 start 01:43:59 commit=eef88eb",
    "== d3:4b-q8_0 r2 01:43:59 done rc=0",
    "== attempt 01:44:55",
    "== d3:9b-q8_0 r2 gpu0 start 01:45:00 commit=eef88eb",
    "== d3:9b-q8_0 r2 02:14:13 done rc=0",
    "== kev:9b-bf16 r2 gpu0 start 02:14:13 commit=eef88eb",
    "== kev:9b-bf16 r2 02:15:32 done rc=127",
    "== d3:4b-q8_0 r2 gpu0 start 02:15:32 commit=eef88eb",
    "== d3:4b-q8_0 r2 02:15:36 done rc=3",
]


def _log(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "progress.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_shuffle_rc_is_the_last_attempts_exit_code(tmp_path):
    """Each model's rc=127 from the first attempt is superseded by its later attempt; the later attempt's rc is what counts."""
    log = _log(tmp_path, CUDA_SHUFFLE)
    assert BB.last_rc(log, "d3:9b-q8_0", BB.tag_part("d3_9b-q8_0", "d3_9b-q8_0.shuffle")) == 0
    assert BB.last_rc(log, "kev:9b-bf16", BB.tag_part("kev_9b-bf16", "kev_9b-bf16.shuffle")) == 0
    assert BB.last_rc(log, "d3:4b-q8_0", BB.tag_part("d3_4b-q8_0", "d3_4b-q8_0.shuffle")) == 0
    assert BB.last_rc(log, "clef:27b-q8_0", "shuffle") == 0  # one attempt, started in the 'shuffle-all' attempt
    assert BB.last_rc(log, "d3:27b-q8_0", "shuffle") == 0
    assert BB.last_rc(log, "tev1:4b-q8_0", "shuffle") is None  # not in this excerpt: no such attempt


def test_published_run_line_with_the_test_word_and_floor(tmp_path):
    """The published run's start line has the word 'test' and the floor in place of a tag; its done line has no tag."""
    log = _log(tmp_path, PUBLISHED)
    assert BB.last_rc(log, "d3:27b-q8_0", None) == 0


def test_repeat_tags_are_read_and_the_other_tag_is_not(tmp_path):
    log = _log(tmp_path, CUDA_REPEAT)
    assert BB.last_rc(log, "d3:9b-q8_0", "r2") == 0
    assert BB.last_rc(log, "d3:9b-q8_0", "r3") == 0
    assert BB.last_rc(log, "kev:9b-bf16", "r2") == 0
    assert BB.last_rc(log, "kev:9b-bf16", "r3") == 0
    assert BB.last_rc(log, "d3:4b-q8_0", "r2") == 0
    assert BB.last_rc(log, "d3:4b-q8_0", "r3") == 0
    # the aborted shuffle in this folder is not the shuffle the collector reads (that is 20261101-cuda-shuffle)
    assert BB.last_rc(log, "kev:9b-bf16", "shuffle") == 3


def test_the_published_run_does_not_read_the_repeat_lines(tmp_path):
    """With no tag, a model's published-run exit code ignores the repeat lines of the same log."""
    log = _log(tmp_path, CUDA_REPEAT)
    assert BB.last_rc(log, "d3:9b-q8_0", None) is None  # the published run is not in this folder


def test_a_last_attempt_that_failed_is_not_hidden_by_an_earlier_success(tmp_path):
    """d3:4b r2 finished rc=0 once and then rc=3 in the Vulkan folder: the last attempt's code (3) is the one that counts."""
    log = _log(tmp_path, VULKAN_TAIL)
    assert BB.last_rc(log, "d3:4b-q8_0", "r2") == 3
    assert BB.last_rc(log, "kev:9b-bf16", "r2") == 127
    assert BB.last_rc(log, "d3:9b-q8_0", "r2") == 0


def test_a_later_attempt_without_a_done_line_leaves_no_exit_code(tmp_path):
    lines = CUDA_SHUFFLE + ["== kev:9b-bf16 shuffle gpu1 start 06:00:00 commit=c8616c5"]
    log = _log(tmp_path, lines)
    assert BB.last_rc(log, "kev:9b-bf16", "shuffle") is None


def test_the_attempt_and_summary_lines_name_no_model(tmp_path):
    """'== attempt ...', '== all done ...' and the server lines are not model lines: they never set an exit code."""
    log = _log(tmp_path, ["== attempt 01:00:00", "{\"server\": \"ollama\", \"version\": \"0.40.11\"}"])
    assert BB.last_rc(log, "attempt", None) is None  # the word after '==' is 'attempt', so no model key matches


@pytest.mark.skipif(REAL_RUNS is None or not REAL_RUNS.is_dir(), reason="MB_PRIVATE_RUNS is not set to the private run folders")
def test_the_real_shuffle_folder_has_fifteen_complete_runs():
    """The 15 shuffle runs are all complete with exit code 0 in the real folder (read only)."""
    prereg = BB.jload(ROOT / "release" / "v0.1.0" / "record" / "prereg.json")
    keys = {k.replace(":", "_"): k for k in prereg["models"]}
    run_dir = REAL_RUNS / "20261101-cuda-shuffle"
    for model, key in keys.items():
        name = f"{model}.shuffle"
        rc = BB.last_rc(run_dir / "progress.log", key, "shuffle")
        assert BB.run_problem(run_dir / name, rc) is None, (model, rc)

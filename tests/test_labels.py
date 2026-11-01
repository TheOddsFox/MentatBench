"""The v0.1.0 relabel: no internal round or measurement label (v0.2, v0.3, Mentat-Hard v2/v3, v3.1, "measurement release",
"internal release") in the paths or text of the repo's files, outside the frozen record. The frozen record keeps its labels as
written: docs/history/, release/v0.1.0/record/ and docs/audits/ are listed with sha256 in docs/history/RENAMES.json, and
the fixture and fixture-manifest files are pinned by hash. The scan covers tracked files and untracked files that git does
not ignore, so a new file is checked before it is committed. A second test checks the run-directory label (v31) that the
2026-11-01 run-directory rename removed; the one remaining match is the builder's former-name literal, allowed below."""

import fnmatch
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIS_FILE = "tests/test_labels.py"
LABEL = re.compile(r"(?i)v0\.[23]\b|Mentat-Hard v\d|mentat[-_ ]hard[-_ ]v[23]|\bv3\.1\b|measurement[ _-]release|internal release")
RUN_LABEL = re.compile(r"(?i)\bv31\b|-v31-")  # the run-directory label that the 2026-11-01 rename removed

# Frozen or hash-pinned trees, and the fixture manifests and golden files that are compared byte for byte.
EXEMPT_PREFIXES = ("docs/history/", "release/v0.1.0/record/", "docs/audits/", "fixtures/manifests/", "tests/golden/")
EXEMPT_PATHS = {"fixtures/VERSIONS.json", THIS_FILE}
# Files this public tree omits: the catalogue tooling, the native and scoped toy fixtures, the release module, the exact-scale
# results, and the tests of those modules (every tests/ file that imports one of them). An entry belongs here only while the
# public tree lacks that file.
PRIVATE_ONLY = (
    "python/mentat_bench/corpus.py", "python/mentat_bench/datacheck.py", "python/mentat_bench/fixtures_native*.py",
    "python/mentat_bench/fixtures_scoped.py", "python/mentat_bench/release.py",
    "fixtures/toy_native*", "docs/results/exact-*",
    "tests/test_pb_corpus.py", "tests/test_duckdb_bounds.py", "tests/test_datacheck.py",
    "tests/test_data_gating.py", "tests/test_fixtures_native.py", "tests/test_fixtures_native_v2.py",
    "tests/test_fixtures_scoped.py", "tests/test_release.py",
)
# (path, anchor, literal): in the one line of that file that contains the anchor, the literal is cut before the scan. The
# two entries are both in the v0.1.0 builder: the gate-phrase mapping (gate_rows maps "spec v0.2" to the 2026-10-27 spec),
# and the scrub rule's former run-directory name (RUN_DIR_FORMER, which rewrites raw cell files from the old name). Any
# other line of that file, or any other file, that contains a literal is flagged.
ALLOWED_LITERALS = {
    ("results/v0.1.0/build_bundle.py", 'text.replace("spec v0.2", "spec revision of 2026-10-27")', "spec v0.2"),
    ("results/v0.1.0/build_bundle.py", 'RUN_DIR_FORMER = "runs/20261030-v31-test"', "runs/20261030-v31-test"),
}
BINARY_SUFFIXES = {".gz", ".parquet", ".png", ".jpg", ".jpeg", ".pdf", ".zip", ".duckdb", ".pyc", ".whl", ".sqlite", ".ico"}


def candidate_files() -> list[str]:
    """Tracked files and untracked files that git does not ignore (git's own view of the repo's files)."""
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                         capture_output=True, check=True).stdout
    return sorted({p for p in out.decode("utf-8").split("\0") if p})


def is_exempt(path: str) -> bool:
    return (path.startswith(EXEMPT_PREFIXES) or path in EXEMPT_PATHS
            or any(fnmatch.fnmatchcase(path, pattern) for pattern in PRIVATE_ONLY))


def cut_allowed(path: str, line: str) -> str:
    for allowed_path, anchor, literal in ALLOWED_LITERALS:
        if path == allowed_path and anchor in line:
            line = line.replace(literal, "", 1)
    return line


def hits(path: str, pattern: re.Pattern) -> list[str]:
    """'path:line: match' for each match of the pattern in the path, or in the text of a text file."""
    found = []
    m = pattern.search(path)
    if m:
        found.append(f"{path}:0: path contains {m.group(0)!r}")
    full = ROOT / path
    if not full.is_file() or full.suffix in BINARY_SUFFIXES:  # a file deleted from the working tree is git status's to report
        return found
    data = full.read_bytes()
    if b"\0" in data:
        return found
    for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
        m = pattern.search(cut_allowed(path, line))
        if m:
            found.append(f"{path}:{number}: {m.group(0)!r}")
    return found


def scan(pattern: re.Pattern) -> tuple[list[str], list[str]]:
    """(files scanned, matches) over every candidate file that is not exempt."""
    scanned, found = [], []
    for path in candidate_files():
        if is_exempt(path):
            continue
        scanned.append(path)
        found += hits(path, pattern)
    return scanned, found


def test_no_round_or_measurement_labels_outside_the_frozen_record():
    scanned, found = scan(LABEL)
    assert "README.md" in scanned and "results/README.md" in scanned  # the scan reached the public text
    assert not found, "labels left outside the frozen record (path:line: match):\n" + "\n".join(found)


def test_no_internal_run_labels():
    _, found = scan(RUN_LABEL)
    assert not found, "run-directory labels left (path:line: match):\n" + "\n".join(found)


def test_allowed_literals_are_each_on_exactly_one_line():
    for path, anchor, literal in ALLOWED_LITERALS:
        lines = [line for line in (ROOT / path).read_text(encoding="utf-8").splitlines() if anchor in line]
        assert len(lines) == 1, (path, anchor, len(lines))
        assert literal in lines[0], (path, literal)


def test_pattern_catches_each_label_form_and_not_the_current_names():
    for label in ("v0.2", "v0.3", "Mentat-Hard v2", "mentat_hard_v3", "mentat-hard v3", "fixture v3.1",
                  "measurement release", "measurement_release", "measurement-release", "internal release"):
        assert LABEL.search(label), label
    for current in ("v0.1.0", "v0.1.1", "v0.10", "mentat_hard_dev", "Mentat-Hard test set, revision 3.1", "revision 3.1"):
        assert not LABEL.search(current), current
    for run in ("v31", "x-v31-y", "runs/x-v31-test/hf"):  # generic samples: the real run prefix is what the rename changes
        assert RUN_LABEL.search(run), run
    for current in ("runs/x-test", "v0.1.0", "v3.10"):
        assert not RUN_LABEL.search(current), current

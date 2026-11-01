"""The history and the frozen record. Every rename in docs/history/RENAMES.json has its old path gone and its new path present,
with the recorded bytes. Every sha256 pin in the v0.1.0 release manifest resolves through RENAMES to a file with that hash.
The pre-registration's fixture, prompt and model hashes still hold. No file in a frozen directory is unlisted, so a frozen
file cannot change silently. Reads committed files only: never runs/ and never the sealed split."""

import hashlib
import json
import re
from pathlib import Path

from mentat_bench import corrections
from mentat_bench.children import systemone_child

ROOT = Path(__file__).resolve().parents[1]
RENAMES_PATH = ROOT / "docs" / "history" / "RENAMES.json"
MANIFEST_PATH = ROOT / "release" / "v0.1.0" / "record" / "release_manifest.json"
PREREG_PATH = ROOT / "release" / "v0.1.0" / "record" / "prereg.json"
OLLAMA_PATH = ROOT / "python" / "mentat_bench" / "children" / "ollama_models.json"
SHA256_HEX = re.compile(r"[0-9a-f]{64}")

# The public export tree has no release.py. The export edits these two files, so their pins hold in the private repo only.
PUBLIC_TREE = not (ROOT / "python" / "mentat_bench" / "release.py").exists()
EXPORT_EDITED = {"docs/ir.md", "fixtures/VERSIONS.json"}
# Pins outside the repo: the external data tables, and the engine binary that the build puts under target/.
NOT_IN_REPO = ("external:", "target/")
# Manifest keys that map a path to its pin (not a {path, sha256} object); they are read by name below.
PATH_KEYED = {"fixture_manifests", "profiles", "fixtures"}
# An old path that the tree reuses for a new current file (docs/ir.md, commit 811bef3): the frozen bytes moved to the
# RENAMES target, and the old path now holds the rewritten document. Its bytes must differ from the frozen sha.
REUSED_OLD_PATHS = {"docs/ir.md"}
# Frozen directories, and the files in them that stay editable (the index of the history and the record's readme).
FROZEN_DIRS = ("docs/history", "docs/audits", "release/v0.1.0/record")
UNFROZEN = {"docs/history/README.md", "docs/history/RENAMES.json", "release/v0.1.0/record/README.md"}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def renames() -> dict:
    return load(RENAMES_PATH)["renames"]


def resolve(path: str, table: dict) -> str:
    return table[path]["path"] if path in table else path


def manifest_pins(manifest: dict) -> list[tuple[str, str, str]]:
    """(where, path, sha256) for every file pin in the manifest: every {path|file, sha256} object, plus the path-keyed
    fixture manifests, profiles and fixture files."""
    pins: list[tuple[str, str, str]] = []

    def walk(node, where):
        if isinstance(node, dict):
            path = node.get("path", node.get("file"))
            if isinstance(node.get("sha256"), str) and isinstance(path, str):
                pins.append((where, path, node["sha256"]))
            for key, child in node.items():
                walk(child, f"{where}/{key}")
        elif isinstance(node, list):
            for i, child in enumerate(node):
                walk(child, f"{where}[{i}]")

    walk({k: v for k, v in manifest.items() if k not in PATH_KEYED}, "")
    for path, spec in manifest["fixture_manifests"].items():
        pins.append((f"fixture_manifests/{path}", path, spec["sha256"]))
    for path, spec in manifest["profiles"].items():
        pins.append((f"profiles/{path}", path, spec["sha256"]))
    for name, spec in manifest["fixtures"].items():
        for rel, sha in spec["files"].items():
            pins.append((f"fixtures/{name}/{rel}", f"{spec['path']}/{rel}", sha))
    return pins


def checked_pins(manifest: dict) -> list[tuple[str, str, str]]:
    kept = []
    for where, path, sha in manifest_pins(manifest):
        if path.startswith(NOT_IN_REPO) or (PUBLIC_TREE and path in EXPORT_EDITED):
            continue
        kept.append((where, path, sha))
    return kept


def test_renames_old_paths_are_gone_and_new_paths_hold_the_recorded_bytes():
    table = renames()
    problems = []
    for old, entry in table.items():
        new = entry["path"]
        if old in REUSED_OLD_PATHS:
            if not (ROOT / old).is_file() or sha256_of(ROOT / old) == entry.get("sha256"):
                problems.append(f"{old}: reused path must hold the rewritten file, not the frozen bytes")
        elif (ROOT / old).exists():
            problems.append(f"{old}: old path still exists")
        if not (ROOT / new).is_file():
            problems.append(f"{old} -> {new}: new path is missing")
        elif "sha256" in entry and sha256_of(ROOT / new) != entry["sha256"]:
            problems.append(f"{old} -> {new}: bytes differ from the recorded sha256")
    assert not problems, "\n".join(problems)
    assert REUSED_OLD_PATHS <= table.keys()


def test_manifest_sha256_pins_resolve_through_renames():
    manifest, table = load(MANIFEST_PATH), renames()
    pins = checked_pins(manifest)
    assert len(pins) >= 50, f"the walk found only {len(pins)} pins"  # 58 on the public tree, 60 in the private repo
    problems = []
    for where, path, sha in pins:
        target = resolve(path, table)
        if not (ROOT / target).is_file():
            problems.append(f"{where}: {path} -> {target} is missing")
        elif sha256_of(ROOT / target) != sha:
            problems.append(f"{where}: {target} differs from the pinned sha256")
    assert not problems, "\n".join(problems)


def test_runcards_record_the_pinned_profile():
    manifest = load(MANIFEST_PATH)
    profile = "profiles/mentat-hard-v1.json"
    pinned = manifest["profiles"][profile]["sha256"]
    assert sha256_of(ROOT / profile) == pinned
    for run in manifest["runcards"]:
        assert run["profile_sha256"] == pinned, run["path"]


def test_prereg_fixture_hashes_are_the_content_hashes_of_the_fixtures():
    prereg = load(PREREG_PATH)
    for name in ("mentat_hard_dev", "mentat_hard_test"):
        recorded = SHA256_HEX.findall(prereg["fixtures"][name.removeprefix("mentat_hard_")])
        assert len(recorded) == 1, name
        actual = corrections.content_hash(corrections.content_hashes(ROOT / "fixtures" / name))
        assert actual == recorded[0], name


def test_prereg_prompt_sha_is_the_child_prompt_sha():
    recorded = re.search(r"prompt_sha256 ([0-9a-f]{64})", load(PREREG_PATH)["child"])
    assert recorded and recorded.group(1) == systemone_child.PROMPT_SHA256


def test_prereg_model_digests_are_the_ollama_pins():
    models, pins = load(PREREG_PATH)["models"], load(OLLAMA_PATH)
    assert models
    for tag, spec in models.items():
        assert tag in pins, tag
        assert spec["digest"] == pins[tag]["digest"], tag


def test_frozen_directories_hold_only_renamed_files():
    listed = {entry["path"] for entry in renames().values() if "sha256" in entry}
    unlisted = []
    for directory in FROZEN_DIRS:
        for path in sorted((ROOT / directory).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel not in UNFROZEN and rel not in listed:
                unlisted.append(rel)
    assert not unlisted, "frozen files not listed in RENAMES.json with a sha256:\n" + "\n".join(unlisted)

"""The public checkout carries only the released synthetic fixtures, with every file pinned by VERSIONS.json."""

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"
RETAINED = {"toy10k", "mentat_hard_dev", "mentat_hard_test"}


def test_retained_fixture_inventory_and_hashes():
    actual_dirs = {path.name for path in FIXTURES.iterdir() if path.is_dir() and path.name != "manifests"}
    assert actual_dirs == RETAINED

    registry = json.loads((FIXTURES / "VERSIONS.json").read_text(encoding="utf-8"))
    active = {entry["name"]: entry for entry in registry["entries"] if entry["status"] == "active"}
    assert set(active) == RETAINED

    for name in sorted(RETAINED):
        entry = active[name]
        root = FIXTURES / name
        hashes = {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()
        }
        assert hashes == entry["hashes"], f"{name} file inventory or content hash differs from VERSIONS.json"
        digest = hashlib.sha256("".join(f"{rel} {hashes[rel]}\n" for rel in sorted(hashes)).encode()).hexdigest()
        assert digest == entry["content_hash"], f"{name} content hash differs from VERSIONS.json"

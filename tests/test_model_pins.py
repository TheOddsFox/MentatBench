"""The hf child's pins (children/models.json): canonical, and every entry carries the files needed to load. The pin tool
(tools/pin_model.py) refuses what it must (license, incomplete files, a download that differs from the Hub) and writes
nothing when it refuses or fails."""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
PINS = REPO / "python" / "mentat_bench" / "children" / "models.json"
REV = "b" * 40


def _pin_tool():
    spec = importlib.util.spec_from_file_location("pin_model", REPO / "tools" / "pin_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(doc) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def test_models_json_is_canonical() -> None:
    text = PINS.read_text(encoding="utf-8")
    assert text == _canonical(json.loads(text))


def test_every_pin_has_its_fields_and_the_files_to_load() -> None:
    tool = _pin_tool()
    data = json.loads(PINS.read_text(encoding="utf-8"))
    assert set(data) == {"models", "note"}
    assert data["models"]
    for model_id, entry in data["models"].items():
        assert "/" in model_id, model_id
        optional = {"relation_floor", "relation_calibration"}  # the hf child's defaults (else RELATION_FLOOR, off)
        assert set(entry) - optional == {"bytes", "download_seconds", "files", "license", "revision"}, model_id
        if "relation_floor" in entry:
            assert 0 <= entry["relation_floor"] <= 6, model_id
        if "relation_calibration" in entry:
            assert isinstance(entry["relation_calibration"], bool), model_id
        assert re.fullmatch(r"[0-9a-f]{40}", entry["revision"]), model_id
        assert entry["license"], model_id
        assert entry["files"] and entry["bytes"] == sum(f["bytes"] for f in entry["files"]), model_id
        for f in entry["files"]:
            assert set(f) == {"bytes", "path", "sha256"}, (model_id, f)
            assert f["bytes"] >= 0 and re.fullmatch(r"[0-9a-f]{64}", f["sha256"]), (model_id, f)
        assert tool.missing_files([f["path"] for f in entry["files"]]) == [], model_id


def test_missing_files_names_each_gap() -> None:
    tool = _pin_tool()
    complete = ["config.json", "tokenizer.json", "model-00001-of-00002.safetensors",
                "model-00002-of-00002.safetensors", "model.safetensors.index.json"]
    assert tool.missing_files(complete) == []
    assert tool.missing_files(["config.json", "tokenizer.json", "model.safetensors"]) == []
    assert "config.json" in tool.missing_files(complete[1:])[0]
    assert any("tokenizer" in p for p in tool.missing_files([n for n in complete if n != "tokenizer.json"]))
    assert any("index" in p for p in tool.missing_files([n for n in complete if "index" not in n]))
    assert any("numbered [2]" in p for p in tool.missing_files([n for n in complete if n != complete[3]]))


def test_pin_tool_refuses_an_unknown_license_unless_allowed() -> None:
    tool = _pin_tool()
    assert tool.check_license(["license:apache-2.0", "region:us"], False) == "apache-2.0"
    with pytest.raises(SystemExit, match="allow-license"):
        tool.check_license(["license:some-custom-license"], False)
    with pytest.raises(SystemExit):
        tool.check_license(None, False)
    assert tool.check_license(None, True) == "unknown"
    with pytest.raises(SystemExit, match="more than one"):
        tool.check_license(["license:mit", "license:apache-2.0"], True)


class FakeHub:
    """Stands in for huggingface_hub: serves `files` as the Hub metadata and `payload` as what a download writes."""

    def __init__(self, tmp_path: Path, files: dict[str, bytes], tags=("license:apache-2.0",), model_id="Org/Model"):
        self.tmp, self.tags, self.model_id = tmp_path, list(tags), model_id
        self.files = files
        self.payload = dict(files)
        self.downloads: list = []
        self.fail_download = False

    def model_info(self, model_id, revision=None, files_metadata=False):
        siblings = [SimpleNamespace(rfilename=name, size=len(data),
                                    lfs=(SimpleNamespace(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
                                         if name.endswith(".safetensors") else None))
                    for name, data in self.files.items()]
        return SimpleNamespace(id=self.model_id, sha=REV, tags=self.tags, siblings=siblings)

    def snapshot_download(self, model_id, revision=None, allow_patterns=None):
        self.downloads.append(list(allow_patterns))
        if self.fail_download:
            raise RuntimeError("network went away")
        root = self.tmp / "snapshot"
        root.mkdir(exist_ok=True)
        for name in allow_patterns:
            (root / name).write_bytes(self.payload[name])
        return str(root)


COMPLETE = {"config.json": b"{}", "tokenizer.json": b"tok", "model.safetensors": b"weights-bytes"}


@pytest.fixture
def hub(monkeypatch, tmp_path):
    tool = _pin_tool()
    pins = tmp_path / "models.json"
    other = {"bytes": 1, "download_seconds": 1.0, "files": [], "license": "mit", "revision": "c" * 40}
    pins.write_text(_canonical({"models": {"Other/Kept": other, "Org/Model": other}, "note": "n"}), encoding="utf-8")
    fake = FakeHub(tmp_path, COMPLETE)
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(
        HfApi=lambda: fake, snapshot_download=fake.snapshot_download))
    monkeypatch.setattr(tool, "PINS", pins)
    fake.pins, fake.tool = pins, tool
    return fake


def test_pin_replaces_its_entry_and_keeps_the_others(hub) -> None:
    assert hub.tool.main(["Org/Model"]) == 0
    doc = json.loads(hub.pins.read_text(encoding="utf-8"))
    assert doc["models"]["Other/Kept"]["revision"] == "c" * 40
    assert doc["models"]["Org/Model"]["revision"] == REV
    assert doc["models"]["Org/Model"]["license"] == "apache-2.0"
    assert [f["path"] for f in doc["models"]["Org/Model"]["files"]] == sorted(COMPLETE)
    assert hub.pins.read_text(encoding="utf-8") == _canonical(doc)


def test_entry_is_keyed_by_the_canonical_repo_id(hub) -> None:
    hub.model_id = "Org/Canonical"
    assert hub.tool.main(["org/canonical"]) == 0
    assert "Org/Canonical" in json.loads(hub.pins.read_text(encoding="utf-8"))["models"]


def test_license_is_refused_before_any_download(hub) -> None:
    hub.tags = ["license:cc-by-nc-4.0"]
    with pytest.raises(SystemExit, match="allow-license"):
        hub.tool.main(["Org/Model"])
    assert hub.downloads == []


def test_two_license_tags_are_refused_even_when_allowed(hub) -> None:
    hub.tags = ["license:mit", "license:apache-2.0"]
    with pytest.raises(SystemExit, match="more than one"):
        hub.tool.main(["Org/Model", "--allow-license"])
    assert hub.downloads == []


def test_incomplete_shards_are_refused_before_any_download(hub) -> None:
    hub.files = {"config.json": b"{}", "tokenizer.json": b"t", "model-00001-of-00002.safetensors": b"a",
                 "model.safetensors.index.json": b"{}"}
    with pytest.raises(SystemExit, match="numbered"):
        hub.tool.main(["Org/Model"])
    assert hub.downloads == []


def test_a_download_that_differs_from_the_hub_is_refused_and_nothing_is_written(hub) -> None:
    before = hub.pins.read_bytes()
    hub.payload["model.safetensors"] = b"weights-BYTES"  # same size, different content: the LFS sha256 differs
    with pytest.raises(SystemExit, match="sha256"):
        hub.tool.main(["Org/Model"])
    assert hub.pins.read_bytes() == before
    hub.payload["model.safetensors"] = b"x"  # a size that differs from the Hub's
    with pytest.raises(SystemExit, match="bytes"):
        hub.tool.main(["Org/Model"])
    assert hub.pins.read_bytes() == before


def test_a_failed_download_writes_nothing(hub) -> None:
    before = hub.pins.read_bytes()
    hub.fail_download = True
    with pytest.raises(RuntimeError):
        hub.tool.main(["Org/Model"])
    assert hub.pins.read_bytes() == before
    assert not hub.pins.with_name(hub.pins.name + ".tmp").exists()


def test_repin_keeps_the_optional_per_model_keys(tmp_path, monkeypatch) -> None:
    """A re-pin replaces the pinned files and revision, and keeps the hf child's optional per-model keys."""
    tool = _pin_tool()
    snap = tmp_path / "snap"
    snap.mkdir()
    files = {"config.json": b"{}", "tokenizer.json": b"{}", "model.safetensors": b"w" * 16}
    for name, data in files.items():
        (snap / name).write_bytes(data)
    pins = tmp_path / "models.json"
    old = {"bytes": 1, "download_seconds": 1.0, "files": [], "license": "apache-2.0", "relation_calibration": True,
           "relation_floor": 3.5, "revision": "a" * 40}
    pins.write_text(json.dumps({"models": {"x/model": old}, "note": "test"}, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    monkeypatch.setattr(tool, "PINS", pins)
    new_revision = "b" * 40
    siblings = [SimpleNamespace(rfilename=name, size=len(data), lfs=None) for name, data in files.items()]

    class FakeHfApi:
        def model_info(self, model_id, revision=None, files_metadata=False):
            return SimpleNamespace(id=model_id, sha=new_revision, tags=["license:apache-2.0"], siblings=siblings)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(
        HfApi=FakeHfApi,
        snapshot_download=lambda model_id, revision=None, allow_patterns=None: str(snap),
    ))
    assert tool.main(["x/model", "--revision", new_revision]) == 0
    entry = json.loads(pins.read_text(encoding="utf-8"))["models"]["x/model"]
    assert entry["revision"] == new_revision
    assert entry["relation_calibration"] is True and entry["relation_floor"] == 3.5
    assert {f["path"] for f in entry["files"]} == set(files)

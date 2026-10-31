#!/usr/bin/env python
"""Pin a Hugging Face model for the hf child: download its top-level safetensors, config, tokenizer and index files into
the default cache, check them against the Hub metadata, hash them, and write its entry into
python/mentat_bench/children/models.json.

    python tools/pin_model.py MODEL_ID [--revision SHA] [--allow-license]

Without --revision the current main revision is pinned. The license comes from the repo's license:* tags: they must name
exactly one license, which must be in KNOWN_LICENSES unless --allow-license is given. Both are checked, and the files
must be complete, before anything is downloaded. Other entries are re-serialized in canonical form (same content);
MODEL_ID's entry, keyed by the canonical repo id, is added or replaced. Nothing is written if any check fails. The
child itself only loads pins from the local cache.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import time
from pathlib import Path

PINS = Path(__file__).resolve().parents[1] / "python" / "mentat_bench" / "children" / "models.json"
ALLOW = ["*.safetensors", "*.json", "*.txt", "*.md", "LICENSE*"]
KNOWN_LICENSES = ("apache-2.0", "mit", "bsd-3-clause")
TOKENIZER_FILES = ("tokenizer.json", "tokenizer.model", "vocab.json")
SHARD = re.compile(r"^(.+)-(\d+)-of-(\d+)\.safetensors$")
OPTIONAL_KEYS = ("relation_calibration", "relation_floor")  # the hf child's per-model defaults, kept across a re-pin


def wanted(name: str) -> bool:
    """Top-level files only: no pattern matches across a directory separator."""
    return "/" not in name and any(fnmatch.fnmatch(name, pattern) for pattern in ALLOW)


def check_license(tags: list[str] | None, allow_license: bool) -> str:
    """The one license:* tag of the repo. Several distinct licenses are refused; an unknown or missing one is refused
    unless allowed."""
    names = sorted({tag.split(":", 1)[1] for tag in tags or [] if tag.startswith("license:")})
    if len(names) > 1:
        raise SystemExit(f"the license tags name more than one license: {names}")
    license_id = names[0] if names else "unknown"
    if license_id not in KNOWN_LICENSES and not allow_license:
        raise SystemExit(f"license {license_id!r} is not one of {KNOWN_LICENSES}; pass --allow-license to pin it anyway")
    return license_id


def missing_files(names: list[str]) -> list[str]:
    """What a pin needs to load: config.json, a tokenizer file, the safetensors weights, and for sharded weights the
    index and every shard number up to the total. Returns the problems, empty when complete."""
    problems = []
    if "config.json" not in names:
        problems.append("config.json")
    if not any(name in names for name in TOKENIZER_FILES):
        problems.append("a tokenizer file (" + " or ".join(TOKENIZER_FILES) + ")")
    weights = [name for name in names if name.endswith(".safetensors")]
    if not weights:
        problems.append("safetensors weights")
    shards: dict[tuple[str, str], set[int]] = {}
    for name in weights:
        match = SHARD.match(name)
        if match:
            shards.setdefault((match.group(1), match.group(3)), set()).add(int(match.group(2)))
    if shards and not any(name.endswith(".safetensors.index.json") for name in names):
        problems.append("the safetensors index")
    for (prefix, total), numbers in sorted(shards.items()):
        missing = sorted(set(range(1, int(total) + 1)) - numbers)
        if missing:
            problems.append(f"shards {prefix}-<n>-of-{total} numbered {missing}")
    return problems


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, doc: dict) -> None:
    """Temp file in the same directory, fsynced, then os.replace: a reader never sees a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model_id")
    parser.add_argument("--revision", help="commit sha to pin (default: the current main revision)")
    parser.add_argument("--allow-license", action="store_true", help="pin a model whose license is not in KNOWN_LICENSES")
    args = parser.parse_args(argv)

    from huggingface_hub import HfApi, snapshot_download  # after argparse, so --help works without the package

    info = HfApi().model_info(args.model_id, revision=args.revision, files_metadata=True)
    model_id, revision = info.id, info.sha
    license_id = check_license(info.tags, args.allow_license)
    siblings = {s.rfilename: s for s in info.siblings}
    names = sorted(name for name in siblings if wanted(name))
    print(f"not pinned (outside the allow patterns): {', '.join(sorted(set(siblings) - set(names))) or 'none'}")
    problems = missing_files(names)
    if problems:
        raise SystemExit(f"refusing to pin {model_id}: missing {'; '.join(problems)}")

    t0 = time.time()
    snapshot = Path(snapshot_download(model_id, revision=revision, allow_patterns=names))
    seconds = round(time.time() - t0, 1)
    files, mismatches = [], []
    for name in names:
        path, sibling = snapshot / name, siblings[name]
        size, digest = path.stat().st_size, sha256_of(path)
        if size != sibling.size:
            mismatches.append(f"{name}: {size} bytes, the Hub says {sibling.size}")
        if sibling.lfs is not None and digest != sibling.lfs.sha256:
            mismatches.append(f"{name}: sha256 differs from the Hub's LFS sha256")
        files.append({"path": name, "bytes": size, "sha256": digest})
    if mismatches:
        raise SystemExit(f"refusing to pin {model_id}, nothing written: {'; '.join(mismatches)}")
    entry = {
        "bytes": sum(f["bytes"] for f in files),
        "download_seconds": seconds,
        "files": files,
        "license": license_id,
        "revision": revision,
    }

    models = json.loads(PINS.read_text(encoding="utf-8"))
    old = models["models"].get(model_id, {})
    previous = old.get("revision")
    entry.update({key: old[key] for key in OPTIONAL_KEYS if key in old})
    models["models"][model_id] = entry
    write_json_atomic(PINS, models)
    action = "replaced" if previous else "added"
    print(f"{action} {model_id} at {revision}: {len(files)} files, {entry['bytes']} bytes, "
          f"{seconds} s, license {license_id}" + (f" (was {previous})" if previous else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

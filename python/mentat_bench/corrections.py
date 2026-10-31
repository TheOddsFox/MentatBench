"""Versioned fixtures and the public correction process (docs/corrections.md).

fixtures/VERSIONS.json is the registry: one entry per fixture version with its name, version, content hashes, status
(active | superseded | withdrawn | retired), superseded_by and a change log (date, reason, affected queries). A version's content
hash covers EVERY regular file of its fixture directory (relative POSIX paths, sorted). A directory that differs from
its registered set in any file, adds one or lacks one is not that version: stamp_for refuses it. A retired entry is a
version kept as a record whose generator was removed (the exact-scale versions, 2026-10-27): it cannot be regenerated.

A generated version is registered by its manifest: a whitelisted generator (GENERATORS) writes it into a scratch directory,
its per-file hashes go to fixtures/manifests/<name>_v<version>.json, and regenerate rebuilds it (always into a new directory
outside fixtures/) and compares every byte with that manifest. Its data may also be committed under fixtures/<name> when
the owner decides so (Mentat-Hard: the test gold is public and the matrix runs read the committed directories); a
committed directory must stamp to the registered content hash. The whitelist holds mentat_bench.fixtures_hard (params seed
and split); the exact-scale generators were removed.

Results record the version they were scored on as `stamp_for(fixture)`: {name, version, status, content_hash}. The run
card and cell.json write it (runcell); rescore reads the recorded stamp instead of trusting --from.

Commands (python -m mentat_bench.corrections):
  list                                   print the registry
  register --fixture DIR --name N --version V --reason TEXT [--affected QID ...]
                                         add a version; the previous active version of N is superseded
  register --generator MODULE [--seed S] [--param KEY=VALUE ...] --name N --version V --reason TEXT
                                         add a generated version: the generator writes into a scratch directory, the
                                         per-file hashes go to fixtures/manifests/<name>_v<version>.json, and the entry
                                         records the generator. register writes no fixture data; the data may be
                                         committed separately when the owner decides so (Mentat-Hard)
  regenerate NAME --version V --out DIR  rebuild a generated version into a new DIR with the whitelisted generator, and
                                         compare every file's SHA-256 with its manifest (any difference removes DIR);
                                         a retired version is refused (its generator is gone)
  rehash --fixture DIR --name N --version V --reason TEXT
                                         recompute the hashes of an existing version (the change log records it);
                                         only the hash scope may change, the content must still be that version
  rescore --run DIR --fixture NEW --from OLD --profile PROFILE --rerun-extra
                                         re-run the extra pass instead: evaluate the run's sealed submission with NEW's
                                         queries_extra.jsonl and score it against NEW's extra gold, into a new
                                         DIR/extra_<name>_v<version>/ (only files in that directory are written)
  rescore --run DIR --fixture NEW --from OLD --profile PROFILE [--queries-file NAME ...]
                                         re-score a run's saved answers against the corrected gold without re-running
                                         any model: mb-engine check against NEW's gold gives new verdicts; the score under
                                         OLD and NEW and the per-query changes go to DIR/rescore_<name>_v<version>.json.
                                         The query files are those of cell.json (query_files; default queries.jsonl),
                                         or the --queries-file names. Refused unless corpus and those queries are
                                         identical in both versions. A run with an extra pass (extra/answers.jsonl)
                                         re-scores queries_extra.jsonl into score_extra.json (the run's copy is kept
                                         as score_extra_<name>_v<old>.json), or is refused for that file alone. A query file
                                         re-scored (queries_extra.jsonl included) with no rows in either version refuses
                                         the whole rescore.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from . import engine, ir, scoring
from .manifest import ProfileError, load_profile
from .seal import SealError, _is_link, replace_with_retry, verify_seal

REGISTRY = Path(__file__).resolve().parents[2] / "fixtures" / "VERSIONS.json"
STATUSES = ("active", "superseded", "withdrawn", "retired")
QUERY_FILES = ("queries.jsonl", "queries_extra.jsonl")  # the query files a fixture may hold (affected-query checks)
GOLD_ANSWER_FILES = ("answers.jsonl", "answers_extra.jsonl")
REQUIRED = ("corpus.jsonl", "queries.jsonl", "gold/answers.jsonl")  # every fixture directory not named by a generated entry
# The generator modules a generated version may name, with the modules whose source is its code identity (the modules the
# generator runs), and each module's parameter contract (its keys, "seed" among them). The exact-scale generators were
# removed on 2026-10-27; Mentat-Hard (fixtures_hard: generator, engine gold, audit, curated suite) is the one listed. A
# whitelisted module has SEED (the default seed), REQUIRED_FILES (the files every version holds) and
# generate(out, *, name, **params), which writes the new directory `out` with a fixture.json naming `name`.
GENERATORS: dict[str, tuple[str, ...]] = {
    "mentat_bench.fixtures_hard": ("fixtures_hard", "fixtures_gen", "hard_suite", "audit_brute", "engine", "ir"),
}
GENERATOR_PARAMS: dict[str, tuple[str, ...]] = {"mentat_bench.fixtures_hard": ("seed", "split")}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")  # a content hash or a file hash: a lowercase SHA-256 hex digest
ENTRY_FIELDS = {"name": (str,), "version": (str,), "status": (str,), "content_hash": (str,), "hashes": (dict,),
                "change_log": (list,), "superseded_by": (str, type(None))}  # every registry entry has these, typed
CODE_DIR = Path(__file__).resolve().parent  # where the GENERATORS sources are read from
EXTRA_QUERIES = "queries_extra.jsonl"  # the second query file of a fixture (runcell --extra-queries)
EXTRA_ANSWERS = "answers_extra.jsonl"  # its gold answers
EXTRA_SCORE = "score_extra.json"  # a run's score of EXTRA_QUERIES (its answers are in extra/)
NAME_OR_VERSION = re.compile(r"[A-Za-z0-9._-]+")  # a name and a version make the directory extra_<name>_v<version>


class CorrectionError(ValueError):
    """A refused registry or rescore operation (the message says why)."""


def _check_name_and_version(name: str, version: str) -> None:
    if not (NAME_OR_VERSION.fullmatch(name) and NAME_OR_VERSION.fullmatch(version)):
        raise CorrectionError(f"a name and a version use only A-Z a-z 0-9 . _ -, got {name!r} version {version!r}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _registered_entry(fixture: Path, registry: Path | list[dict[str, Any]]) -> dict[str, Any] | None:
    """The generated registry entry that the directory's fixture.json names, or None."""
    name = (_json_dict(fixture / "fixture.json") or {}).get("name")
    if not isinstance(name, str):
        return None
    for entry in _entries(registry):
        if entry["name"] == name and "generator" in entry:
            return entry
    return None


def _required_files(fixture: Path, registry: Path | list[dict[str, Any]] = REGISTRY) -> tuple[str, ...]:
    """The files a fixture directory must hold. A directory whose fixture.json names a generated entry holds its generator's
    REQUIRED_FILES; when that generator is not whitelisted (a retired version's is gone), the files the entry registered.
    Any other directory holds REQUIRED."""
    entry = _registered_entry(fixture, registry)
    if entry is None:
        return REQUIRED
    gen = entry["generator"]
    if gen["module"] not in GENERATORS:
        return tuple(entry["hashes"])
    _checked_params(gen["params"], gen["module"])
    return tuple(importlib.import_module(gen["module"]).REQUIRED_FILES)


def content_hashes(fixture: str | Path, registry: Path | list[dict[str, Any]] = REGISTRY) -> dict[str, str]:
    """SHA-256 of every regular file below the fixture directory, keyed by POSIX relative path. Symbolic links and
    junctions are refused (they could point outside the version); the required files must be present (_required_files).
    `registry` is a registry path or the entries list of a snapshot (read_registry)."""
    fixture = Path(fixture)
    if not fixture.is_dir():
        raise CorrectionError(f"{fixture} is not a directory")
    hashes: dict[str, str] = {}

    def unreadable(err: OSError) -> None:
        raise CorrectionError(f"cannot read {err.filename}: {err.strerror or err}") from err

    for root, dirs, names in os.walk(fixture, onerror=unreadable):
        for d in dirs:
            if _is_link(Path(root) / d):
                raise CorrectionError(f"{Path(root) / d} is a link; a fixture version holds regular files only")
        for name in names:
            path = Path(root) / name
            if _is_link(path) or not path.is_file():
                raise CorrectionError(f"{path} is not a regular file")
            hashes[path.relative_to(fixture).as_posix()] = _sha256(path)
    missing = [rel for rel in _required_files(fixture, registry) if rel not in hashes]
    if missing:
        raise CorrectionError(f"{fixture} has no {missing}")
    return dict(sorted(hashes.items()))


def content_hash(hashes: dict[str, str]) -> str:
    return hashlib.sha256("".join(f"{rel} {hashes[rel]}\n" for rel in sorted(hashes)).encode("utf-8")).hexdigest()


def _check_entry(index: int, entry: Any) -> None:
    """Refuses a registry entry that is not an object, lacks a typed field, or has a bad status, generator or hash map."""
    if not isinstance(entry, dict):
        raise CorrectionError(f"registry is malformed: entry {index} is not an object")
    label = f"entry {index}" + (f" ({entry['name']!r})" if isinstance(entry.get("name"), str) else "")
    for field, types in ENTRY_FIELDS.items():
        if field not in entry or not isinstance(entry[field], types):
            raise CorrectionError(f"registry is malformed: {label} field {field!r} is missing or has the wrong type")
    if entry["status"] not in STATUSES:
        raise CorrectionError(f"registry is malformed: {label} field 'status' is {entry['status']!r}, not one of {STATUSES}")
    if not all(isinstance(v, str) and SHA256_HEX.fullmatch(v) for v in entry["hashes"].values()):
        raise CorrectionError(f"registry is malformed: {label} field 'hashes' must map each file to a lowercase SHA-256 hex digest")
    if not SHA256_HEX.fullmatch(entry["content_hash"]):
        raise CorrectionError(f"registry is malformed: {label} field 'content_hash' must be a lowercase SHA-256 hex digest")
    if "generator" in entry:
        gen = entry["generator"]
        if not (isinstance(gen, dict) and isinstance(gen.get("params"), dict)
                and all(isinstance(gen.get(k), str) for k in ("module", "code_sha256", "manifest"))):
            raise CorrectionError(f"registry is malformed: {label} field 'generator' must be an object with string module, "
                                  "code_sha256 and manifest, and an object params")


def _load(registry: Path, must_exist: bool = False) -> dict[str, Any]:
    """The registry document, every entry validated (_check_entry). A missing file is an empty registry, unless must_exist
    (the commands that list, register or report on the registry: a missing file is refused, as list_entries refuses it)."""
    if not registry.is_file():
        if must_exist:
            raise CorrectionError(f"cannot read registry {registry}: the file is missing")
        return {"entries": []}
    try:
        doc = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorrectionError(f"cannot read registry {registry}: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise CorrectionError(f"registry is malformed: {registry} must be an object with an entries list")
    for index, entry in enumerate(doc["entries"]):
        _check_entry(index, entry)
    return doc


def _entries(registry: Path | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries of a registry path (read and validated), or the snapshot list that read_registry returned."""
    return registry if isinstance(registry, list) else _load(Path(registry))["entries"]


def _save(registry: Path, doc: dict[str, Any]) -> None:
    tmp = registry.with_name(registry.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, registry)


def _difference(hashes: dict[str, str], entry: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """(changed, extra, missing) files of a directory against a registered version."""
    known = entry["hashes"]
    changed = sorted(rel for rel in hashes.keys() & known.keys() if hashes[rel] != known[rel])
    return changed, sorted(hashes.keys() - known.keys()), sorted(known.keys() - hashes.keys())


def stamp_for(fixture: str | Path, registry: Path | list[dict[str, Any]] = REGISTRY,
              hashes: dict[str, str] | None = None) -> dict[str, Any]:
    """The stamp of a fixture directory, {name, version, status, content_hash}, for the registered version whose files
    it holds exactly. CorrectionError otherwise, naming the nearest registered version and the files that differ.
    `hashes` is content_hashes(fixture) when the caller already has it (it is not computed again). `registry` is a path
    or the entries list of a snapshot (read_registry)."""
    hashes = content_hashes(fixture, registry) if hashes is None else hashes
    ch = content_hash(hashes)
    entries = _entries(registry)
    for entry in entries:
        if entry.get("content_hash") == ch:
            return {"name": entry["name"], "version": entry["version"], "status": entry["status"], "content_hash": ch}
    if not entries:
        raise CorrectionError(f"{fixture} is not a registered fixture version: the registry is empty")
    nearest = min(entries, key=lambda e: sum(map(len, _difference(hashes, e))))
    changed, extra, missing = _difference(hashes, nearest)
    raise CorrectionError(
        f"{fixture} is not a registered fixture version; nearest is {nearest['name']} v{nearest['version']}: "
        f"changed {changed}, extra {extra}, missing {missing}"
    )


def list_entries(registry: Path = REGISTRY) -> list[dict[str, Any]]:
    return list(_load(Path(registry))["entries"])


def read_registry(registry: Path = REGISTRY) -> list[dict[str, Any]]:
    """The entries of a registry file that must exist (runcell's snapshot, read once per cell, and the report and the list
    command). A missing, unreadable or malformed registry is a CorrectionError."""
    return list(_load(Path(registry), must_exist=True)["entries"])


def _query_ids(fixture: Path) -> set[str]:
    return {q["qid"] for f in QUERY_FILES if (fixture / f).is_file() for q in ir.read_jsonl(fixture / f)}


def _refuse_other_kind(entries: list[dict[str, Any]], name: str, generated: bool) -> None:
    """A name holds committed versions or generated versions, never both: refuses `name` when it has an entry of the other kind."""
    if any(e["name"] == name and ("generator" in e) != generated for e in entries):
        kind, other = ("generated", "committed") if generated else ("committed", "generated")
        raise CorrectionError(f"{name} is a {other} fixture name: a {kind} version cannot share it")


def register(fixture: str | Path, name: str, version: str, reason: str, affected: Iterable[str] = (),
             registry: Path = REGISTRY, date: str | None = None, generator: dict[str, Any] | None = None) -> dict[str, Any]:
    """Adds a version of `name` for `fixture`. Refuses a repeated version, identical content already registered, affected
    queries that the fixture does not contain, a fixture whose queries.jsonl has no rows, and a name that holds versions of
    the other kind (generated or committed). The previous active version of `name` is superseded. `generator`
    (register_generated) is stored in the entry as it is."""
    fixture, registry = Path(fixture), Path(registry)
    if not name or not version or not reason.strip():
        raise CorrectionError("register needs a name, a version and a reason")
    _check_name_and_version(name, version)
    hashes = content_hashes(fixture, registry)
    ch = content_hash(hashes)
    if not any(True for _ in ir.read_jsonl(fixture / "queries.jsonl")):
        raise CorrectionError(f"{fixture}/queries.jsonl has no rows: a version is registered with its queries")
    affected = sorted(set(affected))
    unknown = [q for q in affected if q not in _query_ids(fixture)]
    if unknown:
        raise CorrectionError(f"affected queries not in {fixture}: {unknown}")
    doc = _load(registry, must_exist=True)
    entries = doc["entries"]
    _refuse_other_kind(entries, name, generated=generator is not None)
    if any(e["name"] == name and e["version"] == version for e in entries):
        raise CorrectionError(f"{name} version {version} is already registered")
    if any(e["content_hash"] == ch for e in entries):
        raise CorrectionError("this content is already registered under another version")
    for e in entries:
        if e["name"] == name and e["status"] == "active":
            e["status"], e["superseded_by"] = "superseded", version
    entry = {
        "name": name, "version": version, "status": "active", "superseded_by": None,
        "content_hash": ch, "hashes": hashes,
        "change_log": [{"date": date or datetime.date.today().isoformat(), "reason": reason.strip(), "affected_queries": affected}],
    }
    if generator is not None:
        entry["generator"] = generator
    entries.append(entry)
    _save(registry, doc)
    return entry


def _generator_code(module: Any) -> tuple[str, ...]:
    """The code modules of a whitelisted generator. Any other module is refused: nothing is imported by name from a file."""
    if not isinstance(module, str) or module not in GENERATORS:
        raise CorrectionError(f"generator module {module!r} is not whitelisted (one of {sorted(GENERATORS)})")
    return GENERATORS[module]


def _code_sha256(module: str) -> str:
    """Code identity of a generator: SHA-256 over `<module> <sha256 of its source file>` lines, sorted (the content_hash
    format). The source bytes are read as stored (.gitattributes keeps Python sources at LF)."""
    lines = "".join(f"{mod} {_sha256(CODE_DIR / (mod + '.py'))}\n" for mod in sorted(_generator_code(module)))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def _checked_params(params: Any, module: str) -> dict[str, Any]:
    """The generator parameters of a generated version: exactly the keys of the module's contract (GENERATOR_PARAMS), an
    integer seed, and each value an integer or a string. Booleans are not integers."""
    keys = GENERATOR_PARAMS.get(module)
    if (keys is None or not isinstance(params, dict) or set(params) != set(keys) or type(params.get("seed")) is not int
            or any(type(v) not in (int, str) for v in params.values())):
        raise CorrectionError(f"generator params of {module} must be {list(keys or ())} (seed an integer; each value an "
                              f"integer or a string), got {params!r}")
    return params


def _run_generator(module: str, out: Path, params: dict[str, Any], name: str) -> None:
    """Runs the whitelisted generator of `module` into the new directory `out`; its RuntimeError is a refusal."""
    gen = importlib.import_module(module)
    try:
        gen.generate(out, name=name, **params)
    except RuntimeError as exc:
        raise CorrectionError(f"{module} failed: {exc}") from exc


def _manifest_rel(name: str, version: str) -> str:
    """The manifest path, relative to the registry's directory (fixtures/)."""
    return f"manifests/{name}_v{version}.json"


def register_generated(module: str, params: dict[str, Any] | None, name: str, version: str, reason: str,
                       registry: Path = REGISTRY, date: str | None = None) -> dict[str, Any]:
    """Registers a generated version without committing its data. The whitelisted generator writes the fixture into a
    scratch directory, which is registered with its generator (module, params, code identity, manifest path). `params`
    without a seed takes the module's SEED. The per-file hashes go to manifests/<name>_v<version>.json beside the registry.
    The scratch directory is removed. Refused: a name that holds committed versions, a module other than the name's
    generated versions, and a version that is registered (its manifest is never replaced). A manifest with no registry entry
    (a crash leftover) is replaced."""
    registry = Path(registry)
    from . import cli  # not at import time (see _run_cli)
    _check_name_and_version(name, version)
    _generator_code(module)
    gen = importlib.import_module(module)
    params = _checked_params({"seed": gen.SEED, **(params or {})}, module)
    entries = _load(registry, must_exist=True)["entries"]
    _refuse_other_kind(entries, name, generated=True)
    if any(e["name"] == name and e["version"] == version for e in entries):
        raise CorrectionError(f"{name} version {version} is registered: its manifest is never replaced")
    for other in (e["generator"] for e in entries if e["name"] == name):
        if other["module"] != module:
            raise CorrectionError(f"{name} has generated versions of {other['module']}: a version of {name} must use it")
    manifest_path = registry.parent / _manifest_rel(name, version)
    generator = {"module": module, "params": params, "code_sha256": _code_sha256(module), "manifest": _manifest_rel(name, version)}
    tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    scratch = Path(tempfile.mkdtemp(prefix="corrections_generate_"))
    target = scratch / "fixture"  # the generator creates this directory
    try:
        _run_generator(module, target, params, name)
        hashes = content_hashes(target, registry)
        manifest = {"name": name, "version": version, "generator": {k: generator[k] for k in ("module", "params", "code_sha256")},
                    "content_hash": content_hash(hashes), "files": hashes}
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(tmp, manifest)
        os.replace(tmp, manifest_path)  # the manifest first: the registry entry is written last
        try:
            entry = register(target, name, version, reason, registry=registry, date=date, generator=generator)
        except BaseException:
            manifest_path.unlink(missing_ok=True)  # no manifest without its registry entry
            raise
    finally:
        tmp.unlink(missing_ok=True)
        cli._remove_tree(scratch)
    return entry


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorrectionError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise CorrectionError(f"manifest {path} must be an object")
    return doc


def regenerate(name: str, version: str, out: str | Path, registry: Path = REGISTRY) -> dict[str, Any]:
    """Rebuilds the generated version (name, version) into the new directory `out`: the whitelisted generator with the
    params the registry records, checked against the manifest. Every file's SHA-256 must equal the manifest's; any
    difference refuses and removes `out`. A generator source whose code identity differs from the recorded one is a
    warning in the result, not a refusal: the output is what is checked. A retired version is refused: its generator was
    removed."""
    out, registry = Path(out), Path(registry)
    _check_name_and_version(name, version)
    entry = next((e for e in _load(registry)["entries"] if e["name"] == name and e["version"] == version), None)
    if entry is None:
        raise CorrectionError(f"{name} version {version} is not registered")
    gen = entry.get("generator")
    if not isinstance(gen, dict):
        raise CorrectionError(f"{name} version {version} has no generator: only a generated version can be regenerated")
    if entry["status"] == "retired":
        raise CorrectionError(f"{name} version {version} is retired: its generator {gen['module']} was removed, so it cannot "
                              "be regenerated at this commit (its hashes and manifest are kept as a record)")
    module = gen["module"]
    _generator_code(module)
    params = _checked_params(gen["params"], module)
    if gen.get("manifest") != _manifest_rel(name, version):
        raise CorrectionError(f"{name} version {version} names manifest {gen.get('manifest')!r}, expected {_manifest_rel(name, version)!r}")
    code = _code_sha256(module)  # provenance only: the output, not the source, is what the check compares
    recorded = gen.get("code_sha256")
    warnings = []
    if code != recorded:
        warnings.append(f"the code of {module} differs from the code {name} v{version} was registered with (code sha256 "
                        f"{code}, registered {recorded}); the output is checked against the manifest all the same")
    manifest = _read_manifest(registry.parent / gen["manifest"])
    expected = {"name": name, "version": version, "generator": {"module": module, "params": params, "code_sha256": recorded},
                "content_hash": content_hash(entry["hashes"]), "files": entry["hashes"]}
    if manifest != expected or entry["content_hash"] != expected["content_hash"]:
        raise CorrectionError(f"the manifest {gen['manifest']} does not match the registry entry of {name} v{version}")
    fixtures_dir = REGISTRY.parent.resolve()  # the repository's fixtures/: a regenerated version is never written there
    if out.resolve() == fixtures_dir or fixtures_dir in out.resolve().parents:
        raise CorrectionError(f"{out} is inside {fixtures_dir}: a regenerated version is not written there "
                              "(a committed version is compared, never overwritten); give a directory outside it")
    if out.exists():
        raise CorrectionError(f"{out} exists: a regenerated version is written to a new directory and never replaces one")
    try:
        _run_generator(module, out, params, name)
        hashes = content_hashes(out, registry)
    except BaseException as exc:
        if _discard(out):
            raise
        raise CorrectionError(f"regenerating {name} v{version} failed ({exc}), and {out} could not be removed") from exc
    changed, extra, missing = _difference(hashes, {"hashes": manifest["files"]})
    if changed or extra or missing or content_hash(hashes) != manifest["content_hash"]:
        note = "the output is removed" if _discard(out) else f"{out} could not be removed and is left in place"
        raise CorrectionError(f"regenerated {name} v{version} differs from its manifest: changed {changed}, extra {extra}, "
                              f"missing {missing}; {note}")
    return {"dir": str(out), "stamp": stamp_for(out, registry, hashes), "warnings": warnings}


def _discard(out: Path) -> bool:
    """Removes a directory that a regenerate call created. True when it is gone; False when it survived."""
    from . import cli  # not at import time (see _run_cli)
    if out.exists():
        cli._remove_tree(out)
    return not out.exists()


def rehash(fixture: str | Path, name: str, version: str, reason: str, registry: Path = REGISTRY,
           date: str | None = None) -> dict[str, Any]:
    """Recomputes the hashes of the registered version (name, version) from `fixture`, which must hold that version's
    content (only the hash scope may have changed, not the files). The change log records it. Refused when the content
    would equal another version."""
    fixture, registry = Path(fixture), Path(registry)
    if not reason.strip():
        raise CorrectionError("rehash needs a reason")
    _check_name_and_version(name, version)
    doc = _load(registry)
    entry = next((e for e in doc["entries"] if e["name"] == name and e["version"] == version), None)
    if entry is None:
        raise CorrectionError(f"{name} version {version} is not registered")
    if "generator" in entry:
        raise CorrectionError(f"{name} version {version} is generated: its hashes are its manifest's, and rehash does not apply")
    hashes = content_hashes(fixture, registry)
    ch = content_hash(hashes)
    if any(e["content_hash"] == ch and e is not entry for e in doc["entries"]):
        raise CorrectionError("this content is already registered under another version")
    changed, extra, missing = _difference(hashes, entry)
    if changed or missing:
        raise CorrectionError(f"{fixture} is not {name} v{version}: changed {changed}, missing {missing}; "
                              "rehash changes the hash scope only, not the content")
    entry["hashes"], entry["content_hash"] = hashes, ch
    entry["change_log"].append({"date": date or datetime.date.today().isoformat(), "reason": reason.strip(), "affected_queries": []})
    _save(registry, doc)
    return entry


def _concat(parts: list[Path], out: Path) -> Path:
    """Writes the existing `parts` into `out`, one after another; a part without a final newline gets one, so the
    last line of a part and the first of the next stay two lines."""
    with open(out, "wb") as dst:
        for part in parts:
            if part.is_file():
                data = part.read_bytes()
                if data and not data.endswith(b"\n"):
                    data += b"\n"
                dst.write(data)
    return out


def _score(queries: Path, answers: Path, gold_answers: Path, verdicts: Path | None) -> dict[str, Any]:
    rows = list(ir.read_jsonl(queries))
    cert = scoring.read_verdicts(verdicts) if verdicts is not None and verdicts.is_file() else {}
    return scoring.score_run(rows, ir.JsonlIndex(answers), ir.JsonlIndex(gold_answers), cert)


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    return {"Q_N": report["Q_N"], "exact_query_success": report["exact_query_success"],
            "families": {f: v["mean_utility"] for f, v in report["families"].items()}}


def _cell(run: Path) -> dict[str, Any]:
    path = run / "cell.json"
    if not path.is_file():
        return {}
    try:
        cell = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorrectionError(f"cannot read {path}: {exc}") from exc
    return cell if isinstance(cell, dict) else {}


def _check_recorded(run: Path, stamp_old: dict[str, Any]) -> None:
    """Refuses a --from version other than the one the run's cell.json recorded (no record: nothing to check)."""
    recorded = _cell(run).get("fixture_stamp")
    if recorded is not None and (not isinstance(recorded, dict) or recorded.get("content_hash") != stamp_old["content_hash"]):
        raise CorrectionError(f"--from holds {stamp_old['name']} v{stamp_old['version']}, which is not the version the run "
                              f"recorded ({recorded})")


def _query_names(run: Path, old: Path, queries_files: list[str] | None) -> list[str]:
    """The query files the run was evaluated on: --queries-file names, else cell.json's query_files, else queries.jsonl."""
    names = list(queries_files) if queries_files else (_cell(run).get("query_files") or ["queries.jsonl"])
    if not isinstance(names, list) or not all(
            isinstance(n, str) and n and "/" not in n and "\\" not in n and n not in (".", "..") for n in names):
        raise CorrectionError(f"query files must be plain file names in the fixture directory, got {names!r}")
    for name in names:
        if not (old / name).is_file():
            raise CorrectionError(f"query file {name} is not in {old}")
    return names


def rescore(run: str | Path, new: str | Path, old: str | Path, profile: str | Path, registry: Path = REGISTRY,
            queries_files: list[str] | None = None) -> dict[str, Any]:
    """Re-scores the saved answers of `run` (DIR/answers.jsonl; DIR/verdicts.jsonl as scored under `old`) against the
    gold of `new`, the corrected version. The version the run recorded (cell.json fixture_stamp) must be the one `old`
    holds. Writes and returns the delta report."""
    run, new, old = Path(run), Path(new), Path(old)
    answers = run / "answers.jsonl"
    if not answers.is_file():
        raise CorrectionError(f"{run} has no answers.jsonl to re-score")
    entries = read_registry(registry)  # a missing or malformed registry is refused here, as register refuses it
    hashes_old, hashes_new = content_hashes(old, entries), content_hashes(new, entries)  # each directory is hashed once
    stamp_old, stamp_new = stamp_for(old, entries, hashes_old), stamp_for(new, entries, hashes_new)
    for stamp in (stamp_old, stamp_new):
        _check_name_and_version(stamp["name"], stamp["version"])  # the names build the file names below
    _check_recorded(run, stamp_old)
    if stamp_new["status"] != "active":
        raise CorrectionError(f"{new} is {stamp_new['status']}: re-score onto an active version")
    report_path = run / f"rescore_{stamp_new['name']}_v{stamp_new['version']}.json"
    if report_path.exists():
        raise CorrectionError(f"{report_path} exists: a rescore writes a new file and never replaces one")
    has_extra = (run / "extra" / "answers.jsonl").is_file()
    names = [n for n in _query_names(run, old, queries_files) if n != EXTRA_QUERIES or has_extra]  # no extra answers: none scored
    if has_extra and not queries_files and EXTRA_QUERIES not in names:  # named files are used as given (no extra unless named)
        names.append(EXTRA_QUERIES)  # a cell.json written before query_files listed the extra file
    main = [n for n in names if n != EXTRA_QUERIES]  # answers.jsonl covers these; the extra file has its own pass
    if not main:
        raise CorrectionError(f"{EXTRA_QUERIES} cannot be re-scored alone: answers.jsonl covers queries.jsonl, "
                              "so name queries.jsonl with it")
    for folder in (old, new):  # an empty query file or a missing gold file would score nothing, silently
        if not (folder / "gold" / "answers.jsonl").is_file():
            raise CorrectionError(f"{folder} has no gold/answers.jsonl: the version has no gold answers to re-score against")
        for name in names:  # every file re-scored, queries_extra.jsonl included (names holds it only when its pass is re-scored)
            path = folder / name
            if name == EXTRA_QUERIES and not path.is_file():
                continue  # the new version lacks the extra file: _rescore_extra refuses the extra pass alone (main still applies)
            if not path.is_file() or not any(True for _ in ir.read_jsonl(path)):
                raise CorrectionError(f"{path} is missing or has no rows: there are no queries to re-score")
    for rel in ["corpus.jsonl", *main]:
        if hashes_new.get(rel) != hashes_old.get(rel):
            raise CorrectionError(f"{rel} differs between the versions: the saved answers do not apply; re-run the submission")
    try:
        cap = load_profile(profile).get("max_certificate_refs")
    except ProfileError as exc:
        raise CorrectionError(f"invalid profile: {exc}") from exc

    work = Path(tempfile.mkdtemp(dir=run, prefix="rescore_work_"))  # a new scratch dir: an existing one is never touched
    try:
        report_old, report_new, cert_old, cert_new = _score_pass(
            work, "", old, new, main, answers, run / "verdicts.jsonl", GOLD_ANSWER_FILES, cap)
        extra = _rescore_extra(run, work, old, new, stamp_old, stamp_new, hashes_old, hashes_new, cap) \
            if EXTRA_QUERIES in names else None
    finally:
        from . import cli  # not at import time (see _run_cli)
        cli._remove_tree(work)  # retries after a gc: a scratch file may still be open on Windows
    report = {
        "run": str(run),
        "fixture": {"from": stamp_old, "to": stamp_new},
        "queries": names,  # every query file the run used; the main block covers the non-extra ones
        "max_certificate_refs": cap,
        "old": _summary(report_old),
        "new": _summary(report_new),
        "delta": _delta(report_old, report_new),
        **_changes(report_old, report_new, cert_old, cert_new),
        "extra": extra,  # None when the run has no extra pass or the query files name not the extra file
    }
    _write_json_atomic(report_path, report)
    return report


def rerun_extra(run: str | Path, new: str | Path, old: str | Path, profile: str | Path,
                registry: Path = REGISTRY) -> dict[str, Any]:
    """Re-runs the extra pass of `run` for the version `new`: evaluate on the run's sealed submission with new's
    queries_extra.jsonl, then score against new's gold/answers_extra.jsonl. The results go to a new directory,
    run/extra_<name>_v<version>/ (answers.jsonl, verdicts.jsonl, score_extra.json stamped with new). Everything the call
    writes, evaluate's temp dir included, is inside that directory, and an existing directory is refused. The run must have
    a cell.json whose extra pass finished ok, and `old` must be the version it recorded. The submission was built on old's
    corpus, so corpus.jsonl must be identical in both versions."""
    run, new, old = Path(run), Path(new), Path(old)
    sub = run / "submission"
    entries = read_registry(registry)  # a missing or malformed registry is refused here, as register refuses it
    hashes_old, hashes_new = content_hashes(old, entries), content_hashes(new, entries)  # each directory is hashed once
    stamp_old, stamp_new = stamp_for(old, entries, hashes_old), stamp_for(new, entries, hashes_new)
    _check_recorded(run, stamp_old)
    if not (run / "cell.json").is_file():
        raise CorrectionError(f"{run} has no cell.json: only the extra pass of a cell can be re-run")
    if _extra_status(run) != "ok":
        raise CorrectionError(f"the run's extra pass did not finish ok (extra_status {_extra_status(run)!r}): "
                              "re-run the cell with --extra-queries instead")
    if stamp_new["status"] != "active":
        raise CorrectionError(f"{new} is {stamp_new['status']}: re-run onto an active version")
    recorded = _cell(run).get("fixture_stamp")  # _check_recorded: a dict or None
    if recorded is None:
        raise CorrectionError(f"{run}/cell.json has no fixture_stamp, so the fixture name of the run is not recorded and "
                              f"{new} cannot be checked against it")
    if stamp_new["name"] != recorded.get("name"):
        raise CorrectionError(f"{new} is {stamp_new['name']}, but the run recorded fixture {recorded.get('name')!r}: "
                              "re-run onto a version of that fixture")
    _check_name_and_version(stamp_new["name"], stamp_new["version"])
    target = run / f"extra_{stamp_new['name']}_v{stamp_new['version']}"
    if target.exists():
        raise CorrectionError(f"{target} exists: a re-run writes a new directory and never replaces one")
    for rel in ("corpus.jsonl", EXTRA_QUERIES, f"gold/{EXTRA_ANSWERS}", "gold/bindings.jsonl"):
        if not (new / rel).is_file():
            raise CorrectionError(f"{new} has no {rel}")
    if not any(True for _ in ir.read_jsonl(new / EXTRA_QUERIES)):  # as rescore: an empty query file would score nothing
        raise CorrectionError(f"{new / EXTRA_QUERIES} has no rows: there are no queries to re-run")
    if hashes_new["corpus.jsonl"] != hashes_old["corpus.jsonl"]:
        raise CorrectionError("corpus.jsonl differs between the versions: the submission was built on the old corpus; "
                              "re-run the submission")
    try:
        digest = verify_seal(sub, action="re-run the extra pass")["digest"]
    except SealError as exc:
        raise CorrectionError(f"{sub} is not an intact sealed submission: {exc}") from exc
    target.mkdir()
    scratch = target / "evaluate"  # evaluate's --out; its temp dir is made beside it, so it is inside target too
    try:  # the same evaluate and score commands that runcell runs for its extra pass
        _run_cli(["evaluate", "--submission", str(sub), "--expected-digest", digest, "--profile", str(profile),
                  "--queries", str(new / EXTRA_QUERIES), "--gold", str(new / "gold"), "--out", str(scratch)], "evaluate")
        for name in ("answers.jsonl", "verdicts.jsonl"):
            os.replace(scratch / name, target / name)
        scratch.rmdir()
        scored = json.loads(_run_cli([
            "score", "--queries", str(new / EXTRA_QUERIES), "--gold-answers", str(new / "gold" / EXTRA_ANSWERS),
            "--answers", str(target / "answers.jsonl"), "--verdicts", str(target / "verdicts.jsonl"),
            "--gold-bindings", str(new / "gold" / "bindings.jsonl"),
        ], "score"))
        scored["fixture_stamp"] = stamp_new
        _write_json_atomic(target / EXTRA_SCORE, scored)
    except BaseException:
        from . import cli  # not at import time (see _run_cli)
        cli._remove_tree(target)  # the directory this call created, and nothing else
        raise
    return {"run": str(run), "dir": str(target), "fixture": {"from": stamp_old, "to": stamp_new},
            "queries": [EXTRA_QUERIES], "new": _summary(scored)}


def _extra_status(run: Path) -> str:
    """The run's extra pass status from cell.json (ok, failed, not_run). A cell written before extra_status existed
    counts as ok when its score_extra.json is there."""
    status = _cell(run).get("extra_status")
    if status is None:
        status = "ok" if (run / EXTRA_SCORE).is_file() else "missing"
    return status


def _run_cli(argv: list[str], step: str) -> str:
    """Runs one cli command and returns its stdout; a non-zero exit is refused (cli has printed the reason to stderr)."""
    from . import cli  # not at import time: cli reaches report, which reads this module's REGISTRY
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    if code != 0:
        raise CorrectionError(f"the extra pass's {step} failed with exit {code}; see the message above")
    return buf.getvalue()


def _score_pass(work: Path, tag: str, old: Path, new: Path, names: list[str], answers: Path, verdicts_old: Path,
                gold_files: tuple[str, ...], cap: int | None) -> tuple[dict, dict, dict, dict]:
    """Scores the run's answers on the query files `names` under the old gold and under the new gold (verdicts from
    mb-engine check against the new gold). Returns (report_old, report_new, cert_old, cert_new)."""
    queries = _concat([old / name for name in names], work / f"{tag}queries.jsonl")
    gold_old = _concat([old / "gold" / f for f in gold_files], work / f"{tag}gold_answers_old.jsonl")
    gold_new = _concat([new / "gold" / f for f in gold_files], work / f"{tag}gold_answers_new.jsonl")
    verdicts_new = work / f"{tag}verdicts_new.jsonl"
    engine.check(new / "gold", queries, answers, verdicts_new, max_certificate_refs=cap)
    report_old = _score(queries, answers, gold_old, verdicts_old)
    report_new = _score(queries, answers, gold_new, verdicts_new)
    cert_old = scoring.read_verdicts(verdicts_old) if verdicts_old.is_file() else {}
    cert_new = scoring.read_verdicts(verdicts_new)
    return report_old, report_new, cert_old, cert_new


def _changes(report_old: dict, report_new: dict, cert_old: dict, cert_new: dict) -> dict[str, Any]:
    """Queries whose utility changed and certificate verdicts that changed between the old and the new score."""
    return {
        "changed_queries": [
            {"qid": qid, "old": report_old["per_query"].get(qid), "new": utility}
            for qid, utility in sorted(report_new["per_query"].items()) if report_old["per_query"].get(qid) != utility
        ],
        "verdicts_changed": sorted(q for q in set(cert_old) | set(cert_new) if cert_old.get(q) != cert_new.get(q)),
    }


def _delta(report_old: dict, report_new: dict) -> dict[str, float]:
    return {"Q_N": report_new["Q_N"] - report_old["Q_N"],
            "exact_query_success": report_new["exact_query_success"] - report_old["exact_query_success"]}


def _json_dict(path: Path) -> dict[str, Any] | None:
    """The object in a JSON file, or None when the file is absent or not an object."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _write_json_atomic(path: Path, doc: dict[str, Any]) -> None:
    """Writes a JSON object through a temp file, fsynced, then os.replace: a reader never sees a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    replace_with_retry(tmp, path)


def _rescore_extra(run: Path, work: Path, old: Path, new: Path, stamp_old: dict, stamp_new: dict,
                   hashes_old: dict, hashes_new: dict, cap: int | None) -> dict[str, Any] | None:
    """The run's extra pass (runcell --extra-queries: extra/answers.jsonl, scored into score_extra.json), re-scored
    like the main file. It is refused, not raised, when queries_extra.jsonl differs between the versions (the saved
    extra answers do not apply), so the main re-score is still written and score_extra.json stays as it was. A
    re-scored score_extra.json replaces the run's copy; the run's own copy is kept as score_extra_<name>_v<old>.json."""
    answers = run / "extra" / "answers.jsonl"
    if not answers.is_file():
        return None

    def refused(reason: str) -> dict[str, Any]:
        return {"status": "refused", "queries": [EXTRA_QUERIES], "reason": reason}

    if EXTRA_QUERIES not in hashes_old or hashes_old[EXTRA_QUERIES] != hashes_new.get(EXTRA_QUERIES):
        return refused(f"{EXTRA_QUERIES} differs between the versions: the saved extra answers do not apply; "
                       "re-run the extra pass")
    status = _extra_status(run)
    if status != "ok":
        return refused(f"the run's extra pass did not finish ok (extra_status {status!r}); re-run the extra pass")
    prior = _json_dict(run / EXTRA_SCORE)  # None when missing or unreadable: such a file is never overwritten
    if prior is None:
        return refused(f"{EXTRA_SCORE} is missing or not a readable JSON object; it is left as it was")
    recorded = prior.get("fixture_stamp")
    if recorded is not None and (not isinstance(recorded, dict) or recorded.get("content_hash") != stamp_old["content_hash"]):
        return refused(f"--from holds {stamp_old['name']} v{stamp_old['version']}, not the version {EXTRA_SCORE} "
                       f"recorded ({recorded})")
    try:
        report_old, report_new, cert_old, cert_new = _score_pass(
            work, "extra_", old, new, [EXTRA_QUERIES], answers, run / "extra" / "verdicts.jsonl", (EXTRA_ANSWERS,), cap)
    except (CorrectionError, engine.EngineError, OSError, ValueError) as exc:  # the main file is still re-scored
        return refused(f"the extra re-score failed, score_extra.json is left as it was: {exc}")
    keep = run / f"score_extra_{stamp_old['name']}_v{stamp_old['version']}.json"
    if not keep.exists():
        shutil.copy2(run / EXTRA_SCORE, keep)
    report_new["fixture_stamp"] = stamp_new
    _write_json_atomic(run / EXTRA_SCORE, report_new)
    return {"status": "rescored", "queries": [EXTRA_QUERIES], "old": _summary(report_old), "new": _summary(report_new),
            "delta": _delta(report_old, report_new), **_changes(report_old, report_new, cert_old, cert_new)}


def _cli_params(pairs: list[str]) -> dict[str, Any]:
    """--param KEY=VALUE pairs: a value that is a decimal integer is an int, any other a string."""
    params: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise CorrectionError(f"--param takes KEY=VALUE, got {pair!r}")
        params[key] = int(value) if re.fullmatch(r"-?[0-9]+", value) else value
    return params


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mentat_bench.corrections", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    reg = argparse.ArgumentParser(add_help=False)
    reg.add_argument("--registry", default=str(REGISTRY))
    sub.add_parser("list", parents=[reg], help="print the registry")
    r = sub.add_parser("register", parents=[reg], help="add a fixture version")
    r.add_argument("--fixture", help="the fixture directory (or --generator)")
    r.add_argument("--generator", help="a whitelisted generator module: the version is registered by its manifest; its "
                   "data may be committed separately when the owner decides so (Mentat-Hard)")
    r.add_argument("--seed", type=int, help="generator seed (with --generator; default: the generator's SEED)")
    r.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="another generator parameter (with --generator; an integer value when it is one; repeatable)")
    r.add_argument("--name", required=True)
    r.add_argument("--version", required=True)
    r.add_argument("--reason", required=True)
    r.add_argument("--affected", action="append", default=[], help="affected query id (repeatable)")
    g = sub.add_parser("regenerate", parents=[reg], help="rebuild a generated version and check it against its manifest")
    g.add_argument("name")
    g.add_argument("--version", required=True)
    g.add_argument("--out", required=True, help="a new directory (removed again if the check fails)")
    h = sub.add_parser("rehash", parents=[reg], help="recompute the hashes of a registered version")
    h.add_argument("--fixture", required=True)
    h.add_argument("--name", required=True)
    h.add_argument("--version", required=True)
    h.add_argument("--reason", required=True)
    rs = sub.add_parser("rescore", parents=[reg], help="re-score a run's saved answers against a corrected fixture")
    rs.add_argument("--run", required=True)
    rs.add_argument("--fixture", required=True, help="the corrected version (its gold is used)")
    rs.add_argument("--from", dest="old", required=True, help="the version the run was scored on (checked against the record)")
    rs.add_argument("--profile", required=True, help="the pinned profile of the run (max_certificate_refs)")
    rs.add_argument("--queries-file", action="append", dest="queries_files", default=None,
                    help="query file of the fixture the run used (repeatable; default: cell.json query_files)")
    rs.add_argument("--rerun-extra", action="store_true",
                    help="re-run only the extra pass on the run's submission (queries_extra.jsonl of --fixture) into "
                         "DIR/extra_<name>_v<version>/; cannot be combined with --queries-file")
    args = parser.parse_args(argv)
    if args.command == "rescore" and args.rerun_extra and args.queries_files:
        print("refused: --queries-file cannot be combined with --rerun-extra (only the extra file is re-run)", file=sys.stderr)
        return 2
    try:
        if args.command == "list":
            for e in read_registry(Path(args.registry)):
                print(f"{e['name']} v{e['version']} {e['status']} {e['content_hash'][:12]} superseded_by={e['superseded_by']}")
        elif args.command == "register" and args.generator is not None:
            if args.fixture is not None or args.affected:
                raise CorrectionError("a generated version takes --generator (and its parameters), and no --fixture or --affected")
            params = _cli_params(args.param)
            if args.seed is not None:
                params["seed"] = args.seed
            entry = register_generated(args.generator, params, args.name, args.version, args.reason, Path(args.registry))
            print(f"registered {entry['name']} v{entry['version']} {entry['content_hash']} (generated; manifest {entry['generator']['manifest']})")
        elif args.command == "register":
            if args.fixture is None or args.param or args.seed is not None:
                raise CorrectionError("register needs --fixture DIR, or --generator MODULE with its parameters")
            entry = register(args.fixture, args.name, args.version, args.reason, args.affected, Path(args.registry))
            print(f"registered {entry['name']} v{entry['version']} {entry['content_hash']}")
        elif args.command == "regenerate":
            result = regenerate(args.name, args.version, args.out, Path(args.registry))
            for warning in result["warnings"]:
                print(f"warning: {warning}", file=sys.stderr)
            print(f"regenerated {args.name} v{args.version} into {result['dir']} {result['stamp']['content_hash']}")
        elif args.command == "rehash":
            entry = rehash(args.fixture, args.name, args.version, args.reason, Path(args.registry))
            print(f"rehashed {entry['name']} v{entry['version']} {entry['content_hash']}")
        elif args.command == "rescore" and args.rerun_extra:
            report = rerun_extra(args.run, args.fixture, args.old, args.profile, Path(args.registry))
            print(json.dumps({"dir": report["dir"], "Q_N": report["new"]["Q_N"],
                              "exact_query_success": report["new"]["exact_query_success"]}, sort_keys=True))
        else:
            report = rescore(args.run, args.fixture, args.old, args.profile, Path(args.registry), args.queries_files)
            print(json.dumps({"delta": report["delta"], "changed_queries": len(report["changed_queries"]),
                              "verdicts_changed": len(report["verdicts_changed"]),
                              "extra": (report["extra"] or {}).get("status")}, sort_keys=True))
    except (CorrectionError, OSError, ValueError, engine.EngineError) as exc:  # a malformed registry is a CorrectionError
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Seal a submission directory: per-file SHA-256 plus a digest over the sorted file listing.

Files are listed recursively by POSIX path relative to the submission (seal.json excluded at the top level, but a
seal.json that is a symbolic link or junction is refused). Symbolic links, Windows junctions and any other reparse point
are refused, at any depth and for the submission directory itself. Sealing is write-once: seal.json is published with
os.link, which never overwrites, so a second seal (for example after an edit) is refused. Sealed files are marked
read-only. verify_seal detects any later change.

Ordering. A seal (1) takes the ledger lock, creates build_id.json if absent (sealing first makes sure the submission
holds a random id, sealed like any other file), then marks every listed file read-only and takes the listing (names,
sizes, times, modes); (2) hashes the files OUTSIDE the lock, read-only; (3) takes the lock again and re-checks seal.json,
the listing and build_id.json; (4) appends the entry `{build_id, submission, digest, sealed_at_utc}` to the ledger,
fsynced, and only then (5) publishes seal.json. So every seal.json has its ledger entry. A seal that fails (other than a
lock timeout) undoes its own work while nothing seals the directory: it removes the build_id.json it created and restores
the write access it removed.

Kill-window recovery. A hard kill (SIGKILL, power loss) between (4) and (5) leaves an entry without seal.json. The next
seal of the directory publishes seal.json with the SAME digest, but only when the directory still hashes to the
ledgered digest under the lock; a directory changed since is refused. A seal.json that is present but malformed or
zero-length (torn) is replaced under the same condition, and refused otherwise, naming the file to delete. Deleting a
seal.json whose directory is unchanged, and sealing again, therefore succeeds with the same digest.

Ledger. The ledger is `seal_ledger.jsonl` in the submission's parent directory, or the one central file named by the
MB_SEAL_LEDGER environment variable (resolved to an absolute path when it is relative; it must name the same file across
seals). The lock file is the ledger's name with `.oslock`, `seal_ledger.oslock` by default; a ledger whose name is its
lock's name (`.oslock`, or the `.lock` of builds before the OS lock) is refused. A failed or short append truncates the
ledger back to its size before the append, and an append starts on a new line. Under the lock, before each append, a
final line with no newline that is not a complete entry is cut (an append killed before its fsync; its seal.json cannot
exist, since seal.json is published after the append). A directory is
refused when the ledger has an entry with its build_id OR with its path (compared case-insensitively on Windows), so
renaming the directory does not allow a second seal, and a directory is never sealed under a second digest.
What the ledger protects: it is one local file that must stay intact. A partial or malformed ledger line makes every
seal on that ledger refuse (it fails closed, for every directory on that ledger). What it does NOT protect: deleting
ledger lines together with rewriting seal.json and the files (the ledger is local, so it is copied out for external
audit); moving a sealed directory under another parent with the default per-parent ledger (MB_SEAL_LEDGER, one central
file, closes that gap). Deleting build_id.json as well makes the directory a new build to every ledger. The default
evaluate does not consult the ledger; `verify_ledger` (evaluate --ledger) checks a sealed digest against the ledger
entry of the submission's build_id.

The lock. An OS lock on the lock file (msvcrt.locking on Windows, fcntl.flock on POSIX), taken without waiting and retried
until LOCK_WAIT_S passes (LockTimeout). The lock file is created on first use and never removed, and the lock belongs to
the open handle: the OS releases it when the handle closes or the process dies, so a killed seal never leaves it held.
Hashing and verification happen outside the lock. Only a local volume gets the lock's guarantee: network filesystems may
not enforce flock or byte-range locks, and seal cannot detect that. Builds before the OS lock locked a file named `.lock`
with an owner record and removed it as stale; the OS lock file has another name, so they never touch it, but they do not
wait for it either. Mixing the two on one ledger is not excluded: concurrent seals may both pass their checks and append,
and a later seal then refuses the directory. Do not run the two builds at once.

Limits. The listing compares names, sizes, mtime_ns, ctime_ns and modes on every platform, not contents. While a mark
is set an ordinary write fails, so an edit needs the read-only mark (a mode change) to be made. Windows st_ctime is the
creation time: it catches a file replaced whole, not a write, so an edit that clears the mark, keeps the size, restores
the mtime and sets the mark again before the lock is not seen there. seal.json is published with os.link from the parent,
so seal refuses a submission on another volume than its parent and probes for hard links before it writes anything.
A torn seal.json is possible only from a file written before this ordering, or from disk damage; it is never accepted
unless the ledger matches. Undocumented window: after the ledger append and before seal.json is published the listing is
not checked again; a writer that gets past the mark in that window leaves a seal.json whose digest no longer matches the
files, which verify_seal reports as modified. The POSIX branches (flock, the directory fsync, the mount check) are
exercised only through mocks; they have not been run on POSIX.

Incomplete builds. A build that stopped with an adapter failure writes `INCOMPLETE.json` (a reason) into its
directory. seal refuses such a directory unless allow_incomplete is given; the marker is then sealed with the rest,
so evaluate can see it in the sealed files and warn that the submission is a partial theory.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import time
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SEAL_FILE = "seal.json"
BUILD_ID_FILE = "build_id.json"
INCOMPLETE_FILE = "INCOMPLETE.json"
LEDGER_FILE = "seal_ledger.jsonl"
LOCK_SUFFIX = ".oslock"  # the OS-lock file is the ledger's name with this suffix; a new name, so builds with the old .lock never touch it
LOCK_FILE = "seal_ledger" + LOCK_SUFFIX  # the lock of the default ledger (see _ledger_lock)
LEDGER_ENV = "MB_SEAL_LEDGER"
LOCK_WAIT_S = 20.0
REPLACE_TRIES = 8  # os.replace and unlink attempts for sealed and checkpoint files (about 2 s of backoff in all)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX32 = re.compile(r"[0-9a-f]{32}")


class SealError(RuntimeError):
    pass


class LockTimeout(SealError):
    """The ledger lock was not taken within LOCK_WAIT_S (another seal runs, or the lock is held open elsewhere)."""


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-standard JSON constant {token}")


def _is_link(path: Path) -> bool:
    """Symbolic link or reparse point (Windows junction, mount point, other reparse data). Does not follow."""
    st = os.lstat(path)
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _refuse_link_root(path: Path, action: str) -> None:
    if os.path.lexists(path) and _is_link(path):
        raise SealError(f"{path} is a symbolic link or junction; refusing to {action} through it")


def _same_path(recorded: object, path: Path) -> bool:
    """Path equality as the platform sees it: case-insensitive on Windows (os.path.normcase on both sides)."""
    return isinstance(recorded, str) and os.path.normcase(recorded) == os.path.normcase(str(path))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(files: dict[str, str]) -> str:
    listing = "".join(f"{name} {files[name]}\n" for name in sorted(files))
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def _require_dir(submission: Path) -> None:
    if not submission.is_dir():
        raise SealError(f"{submission} is not a directory")


def _listing(submission: Path, action: str = "seal") -> dict[str, tuple[int, int, int, int]]:
    """(size, mtime_ns, ctime_ns, mode) of every regular file below `submission`, keyed by POSIX relative path. Refuses
    links and reparse points, and any directory it cannot read (no silent skips). seal.json is excluded, but only after
    the check that it is not a link. Sealing takes this listing after marking the files read-only, before hashing, and
    again under the ledger lock."""
    _require_dir(submission)

    def unreadable(err: OSError) -> None:
        raise SealError(f"cannot read {err.filename}: {err.strerror}") from err

    files: dict[str, tuple[int, int, int, int]] = {}
    for root, dirs, names in os.walk(submission, onerror=unreadable):
        for d in dirs:
            path = Path(root) / d
            if _is_link(path):  # checked before os.walk descends into it
                raise SealError(f"{path.relative_to(submission).as_posix()} is a symbolic link or junction; refusing to {action}")
        for name in sorted(names):
            path = Path(root) / name
            rel = path.relative_to(submission).as_posix()
            if rel == SEAL_FILE:
                if _is_link(path):
                    raise SealError(f"{rel} is a symbolic link or junction; refusing to {action}")
                continue
            if _is_link(path) or not path.is_file():
                raise SealError(f"{rel} is not a regular file; refusing to {action}")
            st = path.stat()
            files[rel] = (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode)
    return files


def _make_read_only(submission: Path, listing: dict[str, tuple[int, int, int, int]], originals: dict[str, int]) -> None:
    """Marks every listed file read-only before it is hashed. The modes of the files that were writable go into
    `originals` as each is changed, so a failed seal can restore them."""
    for rel, (_size, _mtime, _ctime, mode) in listing.items():
        if mode & stat.S_IWRITE:
            try:
                os.chmod(submission / rel, stat.S_IREAD)
            except OSError as exc:
                raise SealError(f"cannot mark {rel} read-only: {exc}") from exc
            originals[rel] = stat.S_IMODE(mode)


def _restore_modes(submission: Path, originals: dict[str, int]) -> None:
    """Puts back the modes _make_read_only changed. A file that cannot be reached is a warning: the others still go back."""
    for rel, mode in originals.items():
        try:
            os.chmod(submission / rel, mode)
        except OSError as exc:
            warnings.warn(f"could not restore the write access of {submission / rel}: {exc}", stacklevel=2)


def _current_files(submission: Path, action: str = "seal") -> dict[str, str]:
    """SHA-256 of every regular file below `submission`, keyed by POSIX relative path (see _listing)."""
    return {rel: _sha256(submission / rel) for rel in _listing(submission, action)}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=_reject_constant)
    except (OSError, ValueError) as exc:
        raise SealError(f"malformed {path.name}: {exc}") from exc


def _read_record(path: Path) -> dict[str, Any]:
    """seal.json as {digest, files}; SealError when it is not exactly that shape."""
    record = _load_json(path)
    if not isinstance(record, dict) or set(record) != {"digest", "files"}:
        raise SealError('malformed seal.json: expected exactly the keys "digest" and "files"')
    digest, files = record["digest"], record["files"]
    if not isinstance(digest, str) or not _HEX64.fullmatch(digest) or not isinstance(files, dict):
        raise SealError("malformed seal.json: digest must be a SHA-256 hex string and files an object")
    for name, value in files.items():
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            raise SealError(f"malformed seal.json: {name!r} has no SHA-256 hex digest")
        parts = name.split("/")
        if name == SEAL_FILE or not name or name.startswith("/") or "\\" in name or any(p in ("", ".", "..") for p in parts):
            raise SealError(f"malformed seal.json: {name!r} is not a plain relative path")
    return record


def _read_build_id(submission: Path) -> str | None:
    path = submission / BUILD_ID_FILE
    if not os.path.lexists(path):
        return None
    record = _load_json(path)
    if not isinstance(record, dict) or set(record) != {"build_id"} or not isinstance(record["build_id"], str) \
            or not _HEX32.fullmatch(record["build_id"]):
        raise SealError(f"malformed {BUILD_ID_FILE}: expected {{\"build_id\": <32 hex digits>}}")
    return record["build_id"]


def replace_with_retry(src: str | Path, dst: str | Path) -> None:
    """os.replace that rides out a transient sharing violation: on Windows another process holding the target open
    (WinError 5 or 32) surfaces as PermissionError. Retries with backoff, then raises the last error."""
    delay = 0.05
    for attempt in range(1, REPLACE_TRIES + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def incomplete_reason(submission: str | Path) -> str | None:
    """The reason in the INCOMPLETE.json marker of `submission`; None when there is no marker."""
    path = Path(submission) / INCOMPLETE_FILE
    if not os.path.lexists(path):
        return None
    try:
        marker = _load_json(path)
    except SealError:
        return "unreadable marker"
    return str(marker.get("reason", "unspecified")) if isinstance(marker, dict) else "malformed marker"


def _write_exclusive(path: Path, text: str) -> None:
    with open(path, "x", encoding="utf-8", newline="\n") as fh:  # O_EXCL: never overwrites
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())  # durable before the caller names the file (its ledger entry, or seal.json)


def _ledger_entries(ledger: Path) -> list[tuple[str, dict[str, Any]]]:
    """(raw line, entry) for every ledger line. A malformed line, or a ledger that is not UTF-8 text, is a SealError: the
    ledger fails closed. (A torn final line is cut by _drop_torn_tail, under the lock, before an append.)"""
    if not ledger.is_file():
        return []
    try:
        text = ledger.read_text(encoding="utf-8-sig")
    except (OSError, ValueError) as exc:  # ValueError covers UnicodeDecodeError
        raise SealError(f"seal ledger {ledger} cannot be read ({exc}); refusing to seal") from exc
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line, parse_constant=_reject_constant)
        except ValueError as exc:
            raise SealError(f"{ledger} line {number} is malformed ({exc}); refusing to seal") from exc
        if not isinstance(entry, dict):
            raise SealError(f"{ledger} line {number} is not an object; refusing to seal")
        entries.append((line, entry))
    return entries


def _drop_torn_tail(ledger: Path) -> None:
    """Under the ledger lock, before an entry is appended: cuts a final line that has no newline and is not a complete
    entry. Such a line is what an append left when it was killed or failed before its fsync; its seal.json cannot exist,
    because seal.json is published after the append. A complete final line is kept (see _ledger_append)."""
    try:
        data = ledger.read_bytes()
    except FileNotFoundError:
        return
    if not data or data.endswith(b"\n"):
        return
    cut = data.rfind(b"\n") + 1
    try:
        entry = json.loads(data[cut:].decode("utf-8-sig"), parse_constant=_reject_constant)
    except ValueError:
        entry = None
    if isinstance(entry, dict):
        return
    try:
        fd = os.open(ledger, os.O_WRONLY)
        try:
            os.ftruncate(fd, cut)
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise SealError(f"cannot cut the unfinished last line of {ledger}: {exc}") from exc


def _ledger_append(ledger: Path, line: str) -> None:
    """Appends one entry, fsynced. A final line without its newline gets one first, so the entry starts a line. The size
    before the append is recorded: a failed or short write truncates the ledger back to it, so no partial line is left (a
    truncate that fails too leaves one, and the ledger then fails closed)."""
    size = ledger.stat().st_size if ledger.exists() else 0
    lead = b""
    if size:
        with open(ledger, "rb") as fh:
            fh.seek(size - 1)
            lead = b"" if fh.read(1) == b"\n" else b"\n"
    data = memoryview(lead + (line + "\n").encode("utf-8"))
    fd = os.open(ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        while data:
            written = os.write(fd, data)
            if written <= 0:
                raise OSError("short write to the seal ledger")
            data = data[written:]
        os.fsync(fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.ftruncate(fd, size)
            os.fsync(fd)
        raise
    finally:
        os.close(fd)
    _fsync_dir(ledger.parent)  # the ledger's name is durable too (it may have just been created)


def _unlink_if_present(path: Path) -> None:
    """os.unlink that rides out a sharing violation (Windows: the file is open elsewhere) with replace_with_retry's
    backoff. A missing file is fine; a file still held after the last try raises PermissionError."""
    delay = 0.05
    for attempt in range(1, REPLACE_TRIES + 1):
        try:
            os.unlink(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == REPLACE_TRIES:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def _fsync_dir(directory: Path) -> None:
    """fsync of a directory, so a new or removed file name survives a power loss (POSIX). Windows cannot open a
    directory for it, so the call is skipped there."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _discard_file(path: Path, why: str) -> None:
    """Removes a file this seal created, after a failure (sharing violations are retried), and makes the removal durable.
    A file that cannot be removed is a SealError naming it."""
    try:
        _unlink_if_present(path)
        _fsync_dir(path.parent)
    except OSError as exc:
        raise SealError(f"cannot remove {path} after {why}: {exc}; delete it, then seal again") from exc


def _try_lock(fd: int) -> bool:
    """One non-blocking try at the exclusive lock on byte 0 of the lock file: a msvcrt byte-range lock on Windows
    (LK_NBLCK, which fails at once while held; LK_LOCK would retry on its own), flock on POSIX. The lock belongs to the
    open handle: closing the handle releases it, and so does the death of the process. False while another handle holds
    it."""
    if os.name == "nt":
        import msvcrt  # Windows only

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno == errno.EACCES:  # the byte is locked by another handle
                return False
            raise
        return True
    import fcntl  # POSIX only

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:  # held by another open file description
        return False
    return True


@contextmanager
def _ledger_lock(ledger: Path) -> Iterator[None]:
    """Exclusive lock for the ledger, the build_id and the seal.json steps: the OS lock on the lock file beside the ledger
    (the ledger's name with .oslock; seal_ledger.oslock by default), see _try_lock. The file is created when absent and never
    removed. A lock not taken within LOCK_WAIT_S is LockTimeout."""
    lock = ledger.with_suffix(LOCK_SUFFIX)
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        raise SealError(f"cannot create {lock}: {exc}") from exc
    try:
        give_up = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                if _try_lock(fd):
                    break
            except OSError as exc:
                raise SealError(f"cannot lock {lock}: {exc}") from exc
            if time.monotonic() >= give_up:
                raise LockTimeout(f"{lock} is held by another seal and was not released within {LOCK_WAIT_S:g} s")
            time.sleep(0.05)
        yield
    finally:
        os.close(fd)  # releases the lock


def _probe_hard_links(directory: Path) -> None:
    """Refuses early a volume without hard links: seal.json is published with os.link (which never overwrites) and the
    lock relies on it, so sealing there could not be exclusive. Probed with two names in `directory`, removed again."""
    token = secrets.token_hex(8)
    first, second = directory / f".mentat-probe-{token}.a", directory / f".mentat-probe-{token}.b"
    try:
        _write_exclusive(first, "")
        os.link(first, second)
    except OSError as exc:
        raise SealError(f"{directory} does not support hard links ({exc}); the seal lock and seal.json need them, "
                        "so seal on a volume that has them") from exc
    finally:
        with contextlib.suppress(OSError):
            os.unlink(first)
        with contextlib.suppress(OSError):
            os.unlink(second)


def _create_build_id_file(submission: Path) -> str:
    """Creates build_id.json with a fresh id, exclusively. A failed or interrupted write removes the partly written file
    this call made."""
    build_id = secrets.token_hex(16)
    path = submission / BUILD_ID_FILE
    try:
        _write_exclusive(path, json.dumps({"build_id": build_id}, sort_keys=True) + "\n")
        _fsync_dir(submission)
    except FileExistsError as exc:  # made by someone else: not ours to remove
        raise SealError(f"{BUILD_ID_FILE} appeared in {submission} while it was being sealed; seal it again") from exc
    except BaseException as exc:
        _discard_file(path, "a failed build_id write")
        if isinstance(exc, OSError):
            raise SealError(f"cannot write {BUILD_ID_FILE} in {submission}: {exc}") from exc
        raise
    return build_id


def ensure_build_id(submission: str | Path) -> str:
    """The submission's build_id, writing a fresh build_id.json when there is none (called at build time)."""
    submission = Path(submission)
    _require_dir(submission)
    existing = _read_build_id(submission)
    if existing is not None:
        return existing
    return _create_build_id_file(submission)


def ledger_path(submission: str | Path) -> Path:
    """The ledger `submission` is sealed in: the file named by MB_SEAL_LEDGER when set (resolved to an absolute path),
    else seal_ledger.jsonl beside the submission. A ledger that is its own lock file (its name with .oslock, or with the
    .lock of builds before the OS lock, is the same path, compared case-insensitively on Windows) is refused."""
    central = os.environ.get(LEDGER_ENV)
    path = Path(os.path.abspath(central)) if central else Path(os.path.abspath(Path(submission).parent / LEDGER_FILE))
    if os.path.normcase(str(path)) in {os.path.normcase(str(path.with_suffix(LOCK_SUFFIX))),
                                       os.path.normcase(str(path.with_suffix(".lock")))}:
        raise SealError(f"the seal ledger {path} is its own lock file's name; name the ledger another way")
    return path


def verify_ledger(submission: str | Path, ledger: str | Path, digest: str) -> None:
    """SealError unless `ledger` holds an entry with the build_id of `submission` recorded with this sealed `digest`.
    The check evaluate --ledger makes; it does not look at paths, so a moved sealed directory still verifies."""
    build_id = _read_build_id(Path(submission))
    if build_id is None:
        raise SealError(f"{submission} has no {BUILD_ID_FILE}; it cannot be matched to a ledger entry")
    entries = [entry for _line, entry in _ledger_entries(Path(ledger)) if entry.get("build_id") == build_id]
    if not entries:
        raise SealError(f"seal ledger {ledger} has no entry for build {build_id}")
    if not any(entry.get("digest") == digest for entry in entries):
        raise SealError(f"seal ledger {ledger} records a different digest for build {build_id}")


def _entry_line(build_id: str, digest: str, submission: Path) -> str:
    return json.dumps({"build_id": build_id, "digest": digest,
                       "sealed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "submission": str(submission)}, sort_keys=True, allow_nan=False)


def _entries_naming(ledger: Path, build_id: str | None, submission: Path) -> list[dict[str, Any]]:
    """The ledger entries for this build_id or this path. The caller holds the ledger lock."""
    return [entry for _line, entry in _ledger_entries(ledger)
            if entry.get("build_id") == build_id or _same_path(entry.get("submission"), submission)]


def _seal_file_state(submission: Path) -> str:
    """'absent', 'valid' (a readable seal record) or 'torn' (present but not one). A seal.json that is a symbolic link or
    junction is refused."""
    path = submission / SEAL_FILE
    if not os.path.lexists(path):
        return "absent"
    if _is_link(path):
        raise SealError(f"{path} is a symbolic link or junction; refusing to seal through it")
    try:
        _read_record(path)
    except SealError:
        return "torn"
    return "valid"


def _publish_seal_json(submission: Path, record: dict[str, Any]) -> None:
    """Publishes seal.json whole and never over another file. It is written and fsynced in a temp file beside the
    submission (outside its listing), then hard-linked to seal.json (os.link does not overwrite); the temp file is removed
    in every case. A failure after the link removes the seal.json this call made. A failure of the write keeps the ledger
    entry, and the next seal of the directory publishes seal.json again with the same digest."""
    seal_path = submission / SEAL_FILE
    tmp = submission.parent / f".{SEAL_FILE}.{secrets.token_hex(8)}.tmp"
    text = json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n"
    linked = False
    try:
        _write_exclusive(tmp, text)
        try:
            os.link(tmp, seal_path)
        except FileExistsError as exc:  # another sealer's file: never removed here
            raise SealError(f"{submission} is already sealed; sealing is write-once, build a new submission") from exc
        linked = True
        _fsync_dir(submission)
    except BaseException as exc:
        if linked:
            _discard_file(seal_path, "a failed seal.json publish")
        if isinstance(exc, OSError) and not isinstance(exc, SealError):
            raise SealError(f"cannot write seal.json in {submission}: {exc}; its ledger entry stays, and the next seal of "
                            "this directory publishes seal.json with the same digest") from exc
        raise
    finally:
        with contextlib.suppress(OSError):
            _unlink_if_present(tmp)


def _undo_failed_seal(ledger: Path, submission: Path, build_id: str | None, created_id: bool,
                      originals: dict[str, int]) -> None:
    """Undoes a failed seal's own work, under the ledger lock, and only when nothing seals the directory (no valid
    seal.json, no ledger entry for its build_id or path): restores the modes _make_read_only changed and removes the
    build_id.json this seal created. A directory that is sealed, or in the kill window, keeps its marks. Best effort: a
    warning says what was left when the lock or a removal fails."""
    try:
        with _ledger_lock(ledger):
            state = _seal_file_state(submission)
            if state == "valid" or _entries_naming(ledger, build_id, submission):
                return
            _restore_modes(submission, originals)
            if created_id:  # no valid seal.json and no entry name it: nothing depends on this build_id
                _discard_file(submission / BUILD_ID_FILE, "a failed seal")
    except (SealError, OSError) as exc:
        warnings.warn(f"the failed seal of {submission} left its work in place: {exc}", stacklevel=3)


def seal(submission: str | Path, allow_incomplete: bool = False) -> dict[str, Any]:
    """Seal `submission` (ordering in the module docstring). Returns the seal record {digest, files}. A directory whose
    build_id or path is in the ledger is refused, except the kill window: a ledger entry for this build_id and path,
    with no (or a torn) seal.json, is finished with the SAME digest when the directory still hashes to it. An incomplete
    build (INCOMPLETE.json) is refused unless allow_incomplete, which seals the marker with the rest."""
    raw = Path(submission)
    _refuse_link_root(raw, "seal")
    submission = raw.resolve()
    _require_dir(submission)
    if _seal_file_state(submission) == "valid":
        raise SealError(f"{submission} is already sealed; sealing is write-once, build a new submission")
    reason = incomplete_reason(submission)
    if reason is not None and not allow_incomplete:
        raise SealError(f"{submission} is an incomplete build ({reason}); refusing to seal it. "
                        "Pass --allow-incomplete to seal it anyway (evaluate will warn that it is partial)")
    ledger = ledger_path(submission)
    if os.stat(submission).st_dev != os.stat(submission.parent).st_dev:  # before any write: seal.json is linked from the parent
        raise SealError(f"{submission} is on another volume than its parent; seal.json is published from the parent by a hard "
                        "link, so seal a directory on the same volume as its parent")
    _probe_hard_links(submission.parent)  # before any write: a volume without hard links is refused early
    build_id, created_id, originals = None, False, {}
    try:
        with _ledger_lock(ledger):
            build_id = _read_build_id(submission)
            if build_id is None:
                build_id, created_id = _create_build_id_file(submission), True
            _make_read_only(submission, _listing(submission), originals)  # before hashing: an edit now needs a mode change
            listing = _listing(submission)  # taken after the marks, so the marks are part of what is compared below
        # Hashing is outside the lock (it can take minutes), on read-only files; under the lock the listing, seal.json,
        # build_id.json and the ledger are checked again before anything is written.
        files = {rel: _sha256(submission / rel) for rel in listing}
        digest = _digest(files)
        with _ledger_lock(ledger):
            state = _seal_file_state(submission)
            if state == "valid":
                raise SealError(f"{submission} is already sealed; sealing is write-once, build a new submission")
            if _listing(submission) != listing:
                raise SealError(f"{submission} changed while it was being sealed; seal it again")
            if _read_build_id(submission) != build_id:
                raise SealError(f"{BUILD_ID_FILE} in {submission} changed while it was being sealed; seal it again")
            _drop_torn_tail(ledger)  # an unfinished last line from a killed append; never a seal.json's entry
            entries = _entries_naming(ledger, build_id, submission)
            if entries:  # the kill window (or a torn seal.json): only the ledgered digest of this build and path
                if len(entries) != 1 or entries[0].get("build_id") != build_id or not _same_path(entries[0].get("submission"), submission):
                    raise SealError(f"{submission} already has a seal ledger entry; sealing is write-once, build a new submission")
                if entries[0].get("digest") != digest:
                    raise SealError(f"{submission} changed since it was ledgered; it is not sealed again")
            else:
                if state == "torn":
                    raise SealError(f"{submission / SEAL_FILE} is malformed and has no ledger entry for this build; "
                                    "delete it to seal the directory again")
                try:  # ledger first: every seal.json has its entry
                    _ledger_append(ledger, _entry_line(build_id, digest, submission))
                except OSError as exc:
                    raise SealError(f"cannot record the seal in {ledger}: {exc}") from exc
            if state == "torn":  # replaced only with the ledgered digest, checked above
                try:
                    _unlink_if_present(submission / SEAL_FILE)
                except OSError as exc:
                    raise SealError(f"cannot remove the malformed {submission / SEAL_FILE}: {exc}; delete it, then seal "
                                    "again") from exc
            _publish_seal_json(submission, {"digest": digest, "files": files})
    except BaseException as exc:
        if (created_id or originals) and not isinstance(exc, LockTimeout):  # a lock not taken needs no second wait
            _undo_failed_seal(ledger, submission, build_id, created_id, originals)
        raise
    try:  # the directory is sealed already: a failed mark is reported, not a failed seal
        os.chmod(submission / SEAL_FILE, stat.S_IREAD)
    except OSError as exc:
        warnings.warn(f"could not mark {submission / SEAL_FILE} read-only: {exc}", stacklevel=2)
    return {"digest": digest, "files": files}


def verify_seal(submission: str | Path, expected_digest: str | None = None, action: str = "verify") -> dict[str, Any]:
    """Raise SealError on any post-seal edit: modified, added or removed files (at any depth), a link or reparse
    point (seal.json included), a forged or malformed seal.json, a missing directory, or (when given) a digest other
    than expected_digest (compared case-insensitively after stripping whitespace)."""
    raw = Path(submission)
    _refuse_link_root(raw, action)
    submission = raw
    _require_dir(submission)
    path = submission / SEAL_FILE
    if not os.path.lexists(path):
        raise SealError(f"{submission} is not sealed")
    if _is_link(path):
        raise SealError(f"{path} is a symbolic link or junction; refusing to {action} through it")
    expected = None
    if expected_digest is not None:
        expected = expected_digest.strip().lower()
        if not _HEX64.fullmatch(expected):
            raise SealError("expected digest must be a SHA-256 hex string")
    record = _read_record(path)
    claimed: dict[str, str] = record["files"]
    current = _current_files(submission, action)
    problems = []
    for name in sorted(set(claimed) | set(current)):
        if name not in current:
            problems.append(f"removed: {name}")
        elif name not in claimed:
            problems.append(f"added: {name}")
        elif claimed[name] != current[name]:
            problems.append(f"modified: {name}")
    if _digest(claimed) != record["digest"]:
        problems.append("digest does not match file listing")
    if expected is not None and record["digest"] != expected:
        problems.append(f"digest {record['digest']} does not match expected {expected}")
    if problems:
        raise SealError("; ".join(problems))
    return record

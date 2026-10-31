"""Seal lock and listing. The ledger lock is an OS lock on a lock file that is never removed: two processes contend (real
subprocesses), a killed holder releases it, concurrent seals of one directory serialize, and a listing change between
hashing and the lock is refused, including a same-size, same-mtime edit that flips the read-only mark. A failed seal
undoes its marks. The POSIX branch (fcntl.flock) is exercised through mocks only: this suite runs on Windows and the
POSIX branch was not run on POSIX."""

import errno
import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from mentat_bench import seal as seal_mod
from mentat_bench.seal import BUILD_ID_FILE, SEAL_FILE, LockTimeout, SealError, seal, verify_seal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers_seal import fake_clock, is_seal_temp, scoped_os as _scoped_os  # noqa: E402

PACKAGE_ROOT = str(Path(__file__).resolve().parents[1] / "python")

# A child that holds the ledger lock until a line arrives on its stdin (or until it is killed).
_HOLD = (
    "import sys\n"
    "from pathlib import Path\n"
    "from mentat_bench import seal as s\n"
    "with s._ledger_lock(Path(sys.argv[1])):\n"
    "    print('locked', flush=True)\n"
    "    sys.stdin.readline()\n"
)

# A child that waits for a line on stdin (the start), then seals the directory it is given.
_RACE = (
    "import sys\n"
    "from mentat_bench import seal as s\n"
    "real = s._try_lock\n"
    "reported = []\n"
    "def reporting(fd):\n"
    "    got = real(fd)\n"
    "    if not got and not reported:\n"
    "        reported.append(1)\n"
    "        print('waiting', flush=True)\n"
    "    return got\n"
    "s._try_lock = reporting\n"
    "try:\n"
    "    s.seal(sys.argv[1])\n"
    "    print('sealed', flush=True)\n"
    "except s.SealError as exc:\n"
    "    print('refused ' + str(exc).replace(chr(10), ' '), flush=True)\n"
)


def _submission(tmp_path: Path) -> Path:
    d = tmp_path / "sub"
    d.mkdir()
    (d / "bindings.jsonl").write_text('{"id":"toy:m1:1"}\n', encoding="utf-8")
    (d / "theory.json").write_text('{"interpretations":["base"]}\n', encoding="utf-8")
    return d


def _central(tmp_path: Path) -> Path:
    return tmp_path / "central" / "ledger.jsonl"  # the ledger every test seals to (see the autouse fixture)


def _child_env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": PACKAGE_ROOT}


def _hook_second_lock(monkeypatch, tamper) -> None:
    """Runs `tamper` just before the second ledger lock of a seal (the lock taken after hashing), then takes it."""
    real_lock, entries = seal_mod._ledger_lock, []

    @contextmanager
    def hooked(ledger):
        entries.append(1)
        if len(entries) == 2:
            tamper()
        with real_lock(ledger):
            yield

    monkeypatch.setattr(seal_mod, "_ledger_lock", hooked)


@pytest.fixture(autouse=True)
def _no_ledger_leak(monkeypatch, tmp_path):
    monkeypatch.setenv("MB_SEAL_LEDGER", str(_central(tmp_path)))
    _central(tmp_path).parent.mkdir()


@pytest.fixture
def holder(tmp_path):
    """A real child process holding the ledger lock. Released by a line on its stdin; killed at the end if still alive."""
    proc = subprocess.Popen([sys.executable, "-c", _HOLD, str(_central(tmp_path))], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=_child_env())
    try:
        assert proc.stdout.readline().strip() == "locked"
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()  # by handle: only the child this fixture started
        proc.wait(timeout=30)
        proc.stdin.close()
        proc.stdout.close()


def test_the_lock_is_a_file_that_is_never_removed_and_one_handle_holds_it(tmp_path, monkeypatch):
    ledger = _central(tmp_path)
    lock = ledger.with_suffix(seal_mod.LOCK_SUFFIX)
    monkeypatch.setattr(seal_mod, "LOCK_WAIT_S", 0.2)
    with seal_mod._ledger_lock(ledger):
        assert lock.is_file()
        with pytest.raises(LockTimeout, match="held by another seal"):
            with seal_mod._ledger_lock(ledger):  # a second handle in this process is refused too
                pass
    assert lock.is_file()  # released, not removed
    assert not ledger.with_suffix(".lock").exists()  # the name used before the OS lock is never created
    with seal_mod._ledger_lock(ledger):
        pass


def test_two_processes_contend_and_the_waiter_gets_the_lock_on_release(tmp_path, holder, monkeypatch):
    ledger = _central(tmp_path)
    monkeypatch.setattr(seal_mod, "LOCK_WAIT_S", 0.5)
    with pytest.raises(LockTimeout):
        with seal_mod._ledger_lock(ledger):
            pass  # the child has it
    holder.stdin.write("\n")
    holder.stdin.flush()
    assert holder.wait(timeout=30) == 0
    with seal_mod._ledger_lock(ledger):  # free now, taken at once
        pass


def test_a_killed_holder_releases_the_lock(tmp_path, holder, monkeypatch):
    monkeypatch.setattr(seal_mod, "LOCK_WAIT_S", 2.0)
    holder.kill()  # no cleanup runs in the child; the OS must release the lock
    holder.wait(timeout=30)
    with seal_mod._ledger_lock(_central(tmp_path)):
        pass


def test_a_seal_refused_by_a_lock_held_in_another_process_leaves_nothing_behind(tmp_path, holder, monkeypatch):
    d = _submission(tmp_path)
    monkeypatch.setattr(seal_mod, "LOCK_WAIT_S", 0.5)
    with pytest.raises(LockTimeout):
        seal(d)
    assert not (d / SEAL_FILE).exists() and not (d / BUILD_ID_FILE).exists()
    assert os.access(d / "theory.json", os.W_OK)  # the marks come after the lock, so none were set


def test_concurrent_seals_of_one_directory_serialize(tmp_path):
    """Three processes seal one directory and all three are queued on the ledger lock before it is released: exactly one
    seal is written, the others are refused as already sealed, and the ledger holds one entry."""
    d = _submission(tmp_path)
    holder = seal_mod._ledger_lock(_central(tmp_path))
    holder.__enter__()  # taken before the children start, so none of them can seal first
    procs = []
    try:
        procs = [subprocess.Popen([sys.executable, "-c", _RACE, str(d)], stdout=subprocess.PIPE, text=True,
                                  env=_child_env()) for _ in range(3)]
        for proc in procs:
            assert proc.stdout.readline().strip() == "waiting"
    finally:
        holder.__exit__(None, None, None)  # all three contend now
    try:
        outs = [proc.communicate(timeout=120)[0].strip() for proc in procs]
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
    assert sum(out == "sealed" for out in outs) == 1, outs
    for out in outs:
        assert out == "sealed" or "already sealed" in out, outs
    assert len(_central(tmp_path).read_text(encoding="utf-8").splitlines()) == 1
    verify_seal(d)


def test_a_change_between_hashing_and_the_lock_refuses_the_seal(tmp_path, monkeypatch):
    d = _submission(tmp_path)

    def tamper():
        os.chmod(d / "bindings.jsonl", stat.S_IREAD | stat.S_IWRITE)
        (d / "bindings.jsonl").write_text('{"id":"toy:m1:1","changed":true}\n', encoding="utf-8")

    _hook_second_lock(monkeypatch, tamper)
    with pytest.raises(SealError, match="changed while it was being sealed"):
        seal(d)
    assert not (d / SEAL_FILE).exists() and not (d / BUILD_ID_FILE).exists()  # nothing burned
    assert os.access(d / "theory.json", os.W_OK)  # the marks were undone


def test_a_same_size_same_mtime_edit_that_flips_the_read_only_mark_is_refused(tmp_path, monkeypatch):
    """Between hashing and the lock an edit keeps the size and the mtime, and clears the read-only mark (a mode change).
    The listing compares the mode, so the seal is refused."""
    d = _submission(tmp_path)
    target = d / "bindings.jsonl"
    before = os.stat(target)
    new_text = '{"id":"toy:m1:9"}\n'
    assert len(new_text) == len('{"id":"toy:m1:1"}\n')

    def tamper():
        os.chmod(target, stat.S_IREAD | stat.S_IWRITE)  # the mode flips: writable now
        target.write_text(new_text, encoding="utf-8")
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))  # same mtime as before

    _hook_second_lock(monkeypatch, tamper)
    with pytest.raises(SealError, match="changed while it was being sealed"):
        seal(d)
    assert os.stat(target).st_mtime_ns == before.st_mtime_ns and target.read_text(encoding="utf-8") == new_text
    assert not (d / SEAL_FILE).exists()


def test_the_files_are_read_only_when_they_are_hashed(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    real_sha256, writable_while_hashed = seal_mod._sha256, []

    def checked(path):
        writable_while_hashed.append(os.access(path, os.W_OK))
        return real_sha256(path)

    monkeypatch.setattr(seal_mod, "_sha256", checked)
    seal(d)
    assert len(writable_while_hashed) == 3 and not any(writable_while_hashed)  # bindings, theory, build_id


def test_a_failed_seal_restores_the_write_access_it_removed(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    real_sha256 = seal_mod._sha256

    def unreadable_theory(path):
        if Path(path).name == "theory.json":
            raise OSError("disk read error")
        return real_sha256(path)

    monkeypatch.setattr(seal_mod, "_sha256", unreadable_theory)
    with pytest.raises(OSError, match="disk read error"):
        seal(d)
    assert all(os.access(d / name, os.W_OK) for name in ("bindings.jsonl", "theory.json"))
    assert not (d / BUILD_ID_FILE).exists() and not (d / SEAL_FILE).exists()
    assert not _central(tmp_path).exists()  # no entry: the directory is sealable again
    monkeypatch.setattr(seal_mod, "_sha256", real_sha256)
    seal(d)
    verify_seal(d)


def test_a_seal_that_failed_after_its_ledger_entry_keeps_its_marks(tmp_path, monkeypatch):
    """The ledger entry stands, so the directory is in the kill window: it stays read-only until the next seal."""
    d = _submission(tmp_path)
    real_write = seal_mod._write_exclusive

    def disk_full(path, text):
        if is_seal_temp(path):
            raise OSError("disk full")
        return real_write(path, text)

    monkeypatch.setattr(seal_mod, "_write_exclusive", disk_full)
    with pytest.raises(SealError, match="cannot write seal.json"):
        seal(d)
    assert not os.access(d / "theory.json", os.W_OK)


def test_a_failed_seal_whose_undo_cannot_take_the_lock_warns_and_says_what_was_left(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    held = []

    def fail_while_holding_the_lock(path):
        # the seal's undo must take the lock, and this process holds it: the undo gives up and warns
        if not held:
            held.append(seal_mod._ledger_lock(_central(tmp_path)))
            held[0].__enter__()
        raise OSError("disk read error")

    monkeypatch.setattr(seal_mod, "_sha256", fail_while_holding_the_lock)
    monkeypatch.setattr(seal_mod, "LOCK_WAIT_S", 0.2)
    try:
        with pytest.warns(UserWarning, match="left its work in place"):
            with pytest.raises(OSError, match="disk read error"):
                seal(d)
    finally:
        held[0].__exit__(None, None, None)
    assert not os.access(d / "theory.json", os.W_OK)  # not restored: the undo could not take the lock


def test_a_lock_file_that_cannot_be_opened_is_a_seal_error(tmp_path, monkeypatch):
    real_open = os.open

    def denied(path, flags, *args):
        if str(path).endswith(seal_mod.LOCK_SUFFIX):
            raise PermissionError(errno.EACCES, "access denied")
        return real_open(path, flags, *args)

    _scoped_os(monkeypatch, open=denied)
    with pytest.raises(SealError, match="cannot create"):
        seal(_submission(tmp_path))


def test_a_lock_that_cannot_be_locked_for_another_reason_is_a_seal_error(tmp_path, monkeypatch):
    def bad_handle(fd):
        raise OSError(errno.EBADF, "bad handle")

    monkeypatch.setattr(seal_mod, "_try_lock", bad_handle)
    with pytest.raises(SealError, match="cannot lock"):
        seal(_submission(tmp_path))


def test_the_listing_holds_size_times_and_mode(tmp_path):
    d = _submission(tmp_path)
    st = os.stat(d / "theory.json")
    assert seal_mod._listing(d)["theory.json"] == (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode)


def test_a_submission_on_another_volume_than_its_parent_is_refused_before_anything_is_written(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    real_stat = os.stat

    def other_volume(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        return SimpleNamespace(st_dev=st.st_dev + 1) if Path(path).name == "sub" else st

    _scoped_os(monkeypatch, stat=other_volume)
    with pytest.raises(SealError, match="another volume"):
        seal(d)
    assert not _central(tmp_path).exists() and not _central(tmp_path).with_suffix(seal_mod.LOCK_SUFFIX).exists()
    assert not (d / BUILD_ID_FILE).exists()


def test_an_exdev_on_the_seal_json_link_is_a_seal_error_and_the_entry_stays(tmp_path, monkeypatch):
    """The mount check passed, but the link of seal.json fails with EXDEV (a mount appeared meanwhile): the entry was
    appended first, so it stays, as after any failed publish; the error names the publish."""
    d = _submission(tmp_path)
    real_link = os.link

    def cross_device_for_seal_json(src, dst, *args, **kwargs):
        if Path(dst).name == SEAL_FILE:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_link(src, dst, *args, **kwargs)

    _scoped_os(monkeypatch, link=cross_device_for_seal_json)
    with pytest.raises(SealError, match="cannot write seal.json"):
        seal(d)
    assert not (d / SEAL_FILE).exists()
    assert len(_central(tmp_path).read_text(encoding="utf-8").splitlines()) == 1


def test_a_torn_unfinished_last_line_is_cut_before_the_next_entry(tmp_path):
    seal(_submission(tmp_path))
    ledger = _central(tmp_path)
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write('{"build_id": "ab')  # an append killed before its newline and fsync
    d2 = tmp_path / "second"
    d2.mkdir()
    (d2 / "x.jsonl").write_text("{}\n", encoding="utf-8")
    seal(d2)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(json.loads(line) for line in lines)
    verify_seal(d2)


def test_a_complete_last_line_without_its_newline_is_kept_and_the_next_entry_starts_its_own_line(tmp_path):
    seal(_submission(tmp_path))
    ledger = _central(tmp_path)
    ledger.write_text(ledger.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")  # complete, newline lost
    d2 = tmp_path / "second"
    d2.mkdir()
    (d2 / "x.jsonl").write_text("{}\n", encoding="utf-8")
    seal(d2)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(isinstance(json.loads(line), dict) for line in lines)


def test_a_ledger_that_is_not_utf8_text_refuses_the_seal_with_a_seal_error(tmp_path):
    _central(tmp_path).write_bytes(b"\xff\xfe not a ledger\n")
    d = _submission(tmp_path)
    with pytest.warns(UserWarning, match="left its work in place"):  # the undo reads the ledger too: it fails closed
        with pytest.raises(SealError, match="cannot be read"):
            seal(d)
    assert not (d / SEAL_FILE).exists()


def test_a_failed_mark_of_a_published_seal_json_is_a_warning_not_a_failed_seal(tmp_path, monkeypatch):
    d = _submission(tmp_path)
    real_chmod = os.chmod

    def stuck_seal_json(path, mode, *args, **kwargs):
        if Path(path).name == SEAL_FILE:
            raise PermissionError("the file is held open")
        return real_chmod(path, mode, *args, **kwargs)

    _scoped_os(monkeypatch, chmod=stuck_seal_json)
    with pytest.warns(UserWarning, match="could not mark"):
        record = seal(d)
    assert (d / SEAL_FILE).is_file() and record["files"]
    verify_seal(d)


def test_a_torn_seal_json_without_an_entry_is_refused_and_the_build_id_made_for_it_is_removed(tmp_path):
    d = _submission(tmp_path)
    (d / SEAL_FILE).write_bytes(b"")  # torn, no entry for this build
    with pytest.raises(SealError, match="malformed"):
        seal(d)
    assert not (d / BUILD_ID_FILE).exists()
    assert os.access(d / "theory.json", os.W_OK)  # the marks are undone too


def test_a_lock_timeout_at_the_second_lock_keeps_the_build_id_and_the_marks(tmp_path, monkeypatch):
    d = _submission(tmp_path)

    def busy():
        raise LockTimeout("held by another seal")

    _hook_second_lock(monkeypatch, busy)
    with pytest.raises(LockTimeout):
        seal(d)
    assert (d / BUILD_ID_FILE).is_file()  # not undone: the undo needs the lock too
    assert not os.access(d / "theory.json", os.W_OK)
    assert not (d / SEAL_FILE).exists()


# ---- Windows lock branch on a mocked msvcrt (the POSIX suite run reaches it this way) -------------------------------

def _msvcrt_refusing(monkeypatch, error_number):
    def locking(fd, mode, nbytes):
        assert (mode, nbytes) == (2, 1)  # LK_NBLCK, one byte: our own wait loop, not LK_LOCK's ten retries
        raise OSError(error_number, "locked")

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, locking=locking))
    _scoped_os(monkeypatch, name="nt")


def test_the_windows_branch_treats_a_locked_byte_as_busy_and_waits(tmp_path, monkeypatch):
    clock = fake_clock(monkeypatch)
    _msvcrt_refusing(monkeypatch, errno.EACCES)
    monkeypatch.setattr(seal_mod, "LOCK_WAIT_S", 1.0)
    with pytest.raises(LockTimeout):
        with seal_mod._ledger_lock(tmp_path / "ledger.jsonl"):
            pass
    assert len(clock.sleeps) >= 10  # counted on the fake clock: a busy byte is retried, not refused at once


def test_the_windows_branch_refuses_another_lock_error_at_once(tmp_path, monkeypatch):
    clock = fake_clock(monkeypatch)
    _msvcrt_refusing(monkeypatch, errno.EBADF)
    with pytest.raises(SealError, match="cannot lock"):
        with seal_mod._ledger_lock(tmp_path / "ledger.jsonl"):
            pass
    assert clock.sleeps == []


# ---- POSIX-only branches on a mocked os and fcntl layer (this suite runs on Windows; the branches are not reached) --

def test_try_lock_on_the_posix_branch_asks_flock_without_blocking(monkeypatch):
    calls = []

    def flock(fd, how):
        calls.append((fd, how))
        if len(calls) == 1:
            raise BlockingIOError(errno.EAGAIN, "held")

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=flock))
    _scoped_os(monkeypatch, name="posix")
    assert seal_mod._try_lock(7) is False
    assert seal_mod._try_lock(7) is True
    assert calls == [(7, 2 | 4), (7, 2 | 4)]


def test_a_flock_held_elsewhere_is_a_lock_timeout_on_the_posix_branch(tmp_path, monkeypatch):
    def flock(fd, how):
        raise BlockingIOError(errno.EAGAIN, "held")

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=flock))
    clock = fake_clock(monkeypatch)
    _scoped_os(monkeypatch, name="posix")
    monkeypatch.setattr(seal_mod, "LOCK_WAIT_S", 1.0)
    with pytest.raises(LockTimeout):
        with seal_mod._ledger_lock(tmp_path / "ledger.jsonl"):
            pass
    assert len(clock.sleeps) >= 10


def test_fsync_dir_on_the_posix_branch_opens_fsyncs_and_closes_the_directory(tmp_path, monkeypatch):
    """POSIX only: the directory is opened read-only, fsynced and closed, so the new seal.json name is durable. The
    mocked os layer runs the POSIX path on Windows too."""
    calls = []
    _scoped_os(monkeypatch, name="posix")
    _scoped_os(monkeypatch, open=lambda path, flags: calls.append(("open", path, flags)) or 77)
    _scoped_os(monkeypatch, fsync=lambda fd: calls.append(("fsync", fd)))
    _scoped_os(monkeypatch, close=lambda fd: calls.append(("close", fd)))
    seal_mod._fsync_dir(tmp_path)
    assert calls == [("open", tmp_path, os.O_RDONLY), ("fsync", 77), ("close", 77)]


def test_fsync_dir_is_skipped_on_the_nt_branch(tmp_path, monkeypatch):
    calls = []
    _scoped_os(monkeypatch, name="nt")
    _scoped_os(monkeypatch, open=lambda path, flags: calls.append(path) or 77)
    seal_mod._fsync_dir(tmp_path)
    assert calls == []


@pytest.mark.parametrize("st_mode, attributes, linked", [
    (stat.S_IFLNK | 0o777, 0, True),  # POSIX symbolic link
    (stat.S_IFREG | 0o644, 0, False),  # regular file
    (stat.S_IFDIR | 0o755, stat.FILE_ATTRIBUTE_REPARSE_POINT, True),  # Windows junction (reparse point)
])
def test_is_link_on_a_mocked_stat(tmp_path, monkeypatch, st_mode, attributes, linked):
    _scoped_os(monkeypatch, lstat=lambda path: SimpleNamespace(st_mode=st_mode, st_file_attributes=attributes))
    assert seal_mod._is_link(tmp_path / "entry") is linked

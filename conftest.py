# Last modified: 2026-11-10 | Change: New. Every test runs on a temp data folder and test-only lock names | Full history: git log
"""Test isolation for every test_keeper_*.py file (review item T1, 2026-11-10).

A test run once overwrote the LIVE data/watch_heartbeat.json and appended fake `hwnd 500` lines to the
LIVE data/created_windows.jsonl, because modules default to the real data folder. Three layers stop that:

1. REDIRECT: before each test, every keeper_* / test_keeper_* module attribute and function default that
   points into the real data folder is pointed at the same name inside a per-test temp folder.
2. GUARD: for the whole run, open / os.open / mkdir / rename / replace / remove of anything under the real
   data folder is refused (PermissionError) and recorded. The test that did it fails, and so does the run.
   Only THIS process is watched, so the live recorder writing its heartbeat can never cause a failure.
3. LOCKS: Windows lock names get a test-only prefix, so a test that makes "restore" refuse never waits on
   the live recorder's capture mark, and no test can ever hold a real Session Keeper lock.
Reads of the real data folder stay allowed.
"""

import builtins
import importlib
import inspect
import io
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REAL_DATA = HERE / "data"
REAL_TEXT = os.path.normcase(str(REAL_DATA))
VIOLATIONS = []                       # (where, path, what) for every refused write, read by the checks below
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
TEST_LOCK = f"SessionKeeperTest-{os.getpid()}"


# WHAT IT DOES: True when a path-like is the real data folder or anything inside it.
# DEPENDS ON: The real path as text; resolves the given path (relative paths are against the current folder).
# AFFECTS: Pure. File descriptors and anything that is not a path are never "real".
def is_real(target):
    if isinstance(target, int) or not isinstance(target, (str, bytes, os.PathLike)):
        return False
    try:
        text = os.fsdecode(os.fspath(target))
        text = os.path.normcase(os.path.abspath(text))
    except (TypeError, ValueError):
        return False
    return text == REAL_TEXT or text.startswith(REAL_TEXT + os.sep)


# WHAT IT DOES: Records a refused write and raises PermissionError so nothing reaches the disk.
# AFFECTS: VIOLATIONS; the current test then fails in keeper_isolation's teardown.
def refuse(target, what):
    VIOLATIONS.append((os.environ.get("PYTEST_CURRENT_TEST", "outside a test"), str(target), what))
    raise PermissionError(f"test tried to {what} the REAL data folder: {target}")


# WHAT IT DOES: Wraps one file-system function so a write under the real data folder is refused.
# DEPENDS ON: pick(args, kwargs) returning the paths that call would write.
# AFFECTS: The wrapper carries keeper_write_guard = True so tests can confirm it is installed.
def guarded(original, what, pick):
    def wrapper(*args, **kwargs):
        for target in pick(args, kwargs):
            if is_real(target):
                refuse(target, what)
        return original(*args, **kwargs)
    wrapper.keeper_write_guard = True
    wrapper.__wrapped__ = original
    return wrapper


# WHAT IT DOES: open() / io.open(): the file, only when the mode writes, appends, creates or updates.
def open_targets(args, kwargs):
    mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
    file = args[0] if args else kwargs.get("file")
    return [file] if any(flag in str(mode) for flag in "wax+") else []


# WHAT IT DOES: os.open(): the path, only when the flags write or create.
def os_open_targets(args, kwargs):
    flags = args[1] if len(args) > 1 else kwargs.get("flags", 0)
    return [args[0] if args else kwargs.get("path")] if flags & WRITE_FLAGS else []


# WHAT IT DOES: Installs the write guard for the whole run (pytest calls this once, before collection).
# DEPENDS ON: Nothing else in the repo, so it is in place before any keeper module is imported.
# AFFECTS: builtins.open, io.open, os.open, os.mkdir, os.rename, os.replace, os.remove, os.unlink, os.rmdir.
# Path.open / write_text / mkdir / rename / replace / unlink all go through these.
# DANGER ZONE HIGH: Never let the guard skip a write / breaks: the live heartbeat and created-window log /
# depends on: pathlib calling io.open and os.* at call time (true on Python 3.12).
def pytest_configure(config):
    first = lambda args, kwargs: [args[0] if args else None]
    both = lambda args, kwargs: list(args[:2])
    builtins.open = io.open = guarded(io.open, "write", open_targets)
    os.open = guarded(os.open, "open for writing", os_open_targets)
    for name, pick in (("mkdir", first), ("remove", first), ("unlink", first), ("rmdir", first),
                       ("rename", both), ("replace", both)):
        setattr(os, name, guarded(getattr(os, name), name, pick))
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    for path in sorted(HERE.glob("keeper_*.py")):   # import now, so redirection finds every module
        try:
            importlib.import_module(path.stem)
        except Exception:
            pass                                      # a module that cannot import fails in its own tests


# WHAT IT DOES: The same path inside the temp data folder, or None when the value is not a real data path.
def moved(value, fake):
    if not isinstance(value, Path) or not is_real(value):
        return None
    relative = os.path.relpath(os.path.abspath(value), REAL_DATA)
    return fake if relative == "." else fake / relative


# WHAT IT DOES: Points one function's real-data defaults at the temp folder (restored after the test).
def redirect_defaults(function, fake, monkeypatch):
    function = inspect.unwrap(function) if callable(function) else function
    defaults = getattr(function, "__defaults__", None)
    if defaults and any(moved(value, fake) for value in defaults):
        monkeypatch.setattr(function, "__defaults__", tuple(moved(value, fake) or value for value in defaults))
    keywords = getattr(function, "__kwdefaults__", None)
    if keywords and any(moved(value, fake) for value in keywords.values()):
        monkeypatch.setattr(function, "__kwdefaults__",
                            {key: moved(value, fake) or value for key, value in keywords.items()})


# WHAT IT DOES: Before every test: temp data folder, redirected paths and defaults, test-only lock names.
# After: fails the test if it tried to write the real data folder (even if the code swallowed the error).
# DEPENDS ON: pytest's tmp_path and monkeypatch, which undo every change after the test.
# AFFECTS: Module attributes for the duration of one test only.
@pytest.fixture(autouse=True)
def keeper_isolation(tmp_path, monkeypatch):
    fake = tmp_path / "keeper_data"
    fake.mkdir()
    import keeper_locks, keeper_single
    real_lock, real_screen = keeper_locks.lock_name, keeper_single.screen_lock_name
    test_lock = lambda operation: f"Local\\{TEST_LOCK}-{operation}"
    test_screen = lambda: f"Local\\{TEST_LOCK}-recover-screen"
    for name, module in list(sys.modules.items()):
        if not name.startswith(("keeper_", "test_keeper_")) or module is None:
            continue
        for attribute, value in list(vars(module).items()):
            if (target := moved(value, fake)) is not None:
                monkeypatch.setattr(module, attribute, target)
            elif value is real_lock:
                monkeypatch.setattr(module, attribute, test_lock)
            elif value is real_screen:
                monkeypatch.setattr(module, attribute, test_screen)
            elif inspect.isfunction(value) and value.__module__ == name:
                redirect_defaults(value, fake, monkeypatch)
            elif inspect.isclass(value) and value.__module__ == name:
                for member in vars(value).values():
                    redirect_defaults(getattr(member, "__func__", member), fake, monkeypatch)
    before = len(VIOLATIONS)
    yield fake
    new = VIOLATIONS[before:]
    if new:
        pytest.fail(f"test tried to write the REAL data folder: {new}", pytrace=False)


# WHAT IT DOES: Run-level backstop: any refused write anywhere (even outside a test) fails the whole run.
# AFFECTS: The exit status and one printed line per refused write.
def pytest_sessionfinish(session, exitstatus):
    if VIOLATIONS:
        for where, path, what in VIOLATIONS:
            print(f"\nREAL DATA WRITE REFUSED: {where}: {what} {path}")
        session.exitstatus = 1

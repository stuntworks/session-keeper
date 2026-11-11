# Last modified: 2026-11-10 | Change: capture_mark + wait_out_capture: a restore waits for a running capture instead of failing | Full history: git log
"""Windows automatically releases these locks if Session Keeper exits or crashes."""

import ctypes
from ctypes import wintypes
from contextlib import ExitStack, contextmanager
import hashlib
import time
from pathlib import Path


# WHAT IT DOES: The per-user mutex name for one operation ("watch", "restore").
# DEPENDS ON: The user's home directory identity.
# AFFECTS: Name only. exclusive_operation and operation_running must use the same one.
def lock_name(operation):
    user_key = hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
    return f"Local\\SessionKeeper-{user_key}-{operation}"


# WHAT IT DOES: True when some process holds this operation's lock (e.g. the recorder for "watch").
# DEPENDS ON: OpenMutexW, which only opens an existing mutex: it never creates or takes it.
# AFFECTS: Nothing. Error 2 (no such mutex) means nobody holds it; any other failure counts as running,
# so a check that cannot tell never starts a second recorder.
def operation_running(operation, name=None):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenMutexW(0x00100000, False, name or lock_name(operation))   # SYNCHRONIZE
    if handle:
        kernel.CloseHandle(handle)
        return True
    return ctypes.get_last_error() != 2


# WHAT IT DOES: Allows one Session Keeper operation of the named type per user at a time.
# DEPENDS ON: Windows named mutexes and the user's home directory identity.
# AFFECTS: Prevents two recovery screens from launching the same conversations together.
@contextmanager
def exclusive_operation(operation):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, True, lock_name(operation))
    error = ctypes.get_last_error()
    if not handle:
        raise OSError(error, "Cannot acquire Session Keeper lock")
    if error == 183:
        kernel.CloseHandle(handle)
        raise RuntimeError(f"Session Keeper {operation} is already running")
    try:
        yield
    finally:
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


# WHAT IT DOES: Marks "a background capture is using the <operation> lock" for as long as the block runs.
# DEPENDS ON: A named mutex that exists only while some process holds a handle to it (never owned/waited on).
# AFFECTS: Nothing is locked; wait_out_capture only reads whether the mark exists.
@contextmanager
def capture_mark(operation):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, lock_name(f"{operation}-capture"))
    try:
        yield
    finally:
        if handle:
            kernel.CloseHandle(handle)


# WHAT IT DOES: Takes the <operation> lock like exclusive_operation, but when a background CAPTURE holds it,
# polls for up to `wait` seconds instead of failing at once. Another restore holding it still fails at once.
# DEPENDS ON: lock (exclusive_operation, injectable so callers' test patches keep working) and the capture
# mark set by keeper_app.record_once.
# AFFECTS: A restore started while the recorder scans waits a few seconds instead of "Recovery paused".
# DANGER ZONE HIGH: Never wait on another restore / breaks: two restores launching the same chat / depends on:
# only record_once setting the capture mark.
@contextmanager
def wait_out_capture(operation, lock=None, wait=60, capturing=None, clock=time.monotonic, sleep=time.sleep):
    lock = lock or exclusive_operation
    capturing = capturing or (lambda: operation_running(f"{operation}-capture"))
    deadline = clock() + wait
    with ExitStack() as stack:
        while True:
            try:
                stack.enter_context(lock(operation))
                break
            except RuntimeError:
                if clock() >= deadline or not capturing():
                    raise
                sleep(0.5)
        yield

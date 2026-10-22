# Last modified: 2026-10-03 | Change: Prevent duplicate watchers and simultaneous restores | Full history: git log
"""Windows automatically releases these locks if Session Keeper exits or crashes."""

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import hashlib
from pathlib import Path


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
    user_key = hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
    handle = kernel.CreateMutexW(None, True, f"Local\\SessionKeeper-{user_key}-{operation}")
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

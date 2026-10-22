# Last modified: 2026-10-21 | Change: One recovery screen at a time; a second launch brings it to the front | Full history: git log
"""Single instance for `keeper_app.py recover`. A Windows named mutex says whether a recovery screen
is running (Windows frees it if that process crashes); a small file in data\\ says which window to raise.
Separate from the "watch" and "restore" locks in keeper_locks, which are not touched."""

import ctypes
import hashlib
import json
import os
import time
from ctypes import wintypes
from pathlib import Path

from keeper_core import DATA

INFO = DATA / "recover_window.json"
WAIT_SECONDS = 15          # how long a second launch waits for the first one's window to appear
WAIT_OBJECT_0, WAIT_ABANDONED, STILL_ACTIVE = 0, 0x80, 259
SW_RESTORE, SW_SHOW = 9, 5

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
user = ctypes.WinDLL("user32", use_last_error=True)
kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel.CreateMutexW.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
user.IsWindow.argtypes = [wintypes.HWND]
user.IsIconic.argtypes = [wintypes.HWND]
user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user.SetForegroundWindow.argtypes = [wintypes.HWND]
user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_held = []  # keeps the owned mutex handle open for the life of this process


# WHAT IT DOES: The per-user mutex name for the recovery screen.
# DEPENDS ON: The same home-directory key keeper_locks uses.
# AFFECTS: Different name from the watch and restore locks, so those are unaffected.
def screen_lock_name():
    user_key = hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
    return f"Local\\SessionKeeper-{user_key}-recover-screen"


# WHAT IT DOES: Takes the named mutex without waiting. Returns its handle, or None if a live process owns it.
# DEPENDS ON: Windows: a crashed owner leaves the mutex "abandoned", which counts as free.
# AFFECTS: The handle must stay open (or be released) by the caller.
def try_own(name):
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), "Cannot create Session Keeper screen lock")
    if kernel.WaitForSingleObject(handle, 0) in (WAIT_OBJECT_0, WAIT_ABANDONED):
        return handle
    kernel.CloseHandle(handle)
    return None


# WHAT IT DOES: Gives the mutex back (tests only; the app simply keeps it until exit).
def release(handle):
    kernel.ReleaseMutex(handle)
    kernel.CloseHandle(handle)


# WHAT IT DOES: Decides which window to raise from the saved record, or None if the record is stale.
# DEPENDS ON: pid_alive(pid) -> bool and window_pid(hwnd) -> owning pid or None (injected; pure logic).
# AFFECTS: A record left by a crashed or replaced instance is ignored, never trusted.
def choose_target(info, pid_alive, window_pid):
    if not isinstance(info, dict):
        return None
    pid, hwnd = info.get("pid"), info.get("hwnd")
    if type(pid) is not int or type(hwnd) is not int or pid <= 0 or hwnd <= 0:
        return None
    if not pid_alive(pid) or window_pid(hwnd) != pid:
        return None
    return hwnd


# WHAT IT DOES: True if a process with this pid is still running.
def pid_alive(pid):
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = wintypes.DWORD()
    try:
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


# WHAT IT DOES: The pid that owns a window, or None if the window no longer exists.
def window_pid(hwnd):
    if not user.IsWindow(hwnd):
        return None
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value or None


# WHAT IT DOES: Reads the running screen's {pid, hwnd} record; damaged or missing reads as None.
def read_info(path=INFO):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# WHAT IT DOES: Restores a minimised window and brings it to the front.
# DEPENDS ON: This process being the one the user just launched (Windows lets it set the foreground).
# AFFECTS: Only that window's show state and z-order.
def bring_to_front(hwnd):
    user.ShowWindow(hwnd, SW_RESTORE if user.IsIconic(hwnd) else SW_SHOW)
    user.SetForegroundWindow(hwnd)


# WHAT IT DOES: Call before building any window. True = this launch is the recovery screen; False = an
# existing screen was raised and this launch should exit.
# DEPENDS ON: try_own, the record written by publish_window.
# AFFECTS: If the other instance never shows a window within WAIT_SECONDS, opens anyway (never blocks forever).
def claim_recover_screen(name=None, wait=WAIT_SECONDS, info=INFO):
    name = name or screen_lock_name()
    deadline = time.monotonic() + wait
    while True:
        handle = try_own(name)
        if handle:
            _held.append(handle)
            return True
        target = choose_target(read_info(info), pid_alive, window_pid)
        if target:
            bring_to_front(target)
            return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.25)


# WHAT IT DOES: Records which window a second launch should raise (the loading window, then the screen).
# DEPENDS ON: A mapped Tk window; wm_frame() is the outer Windows handle.
# AFFECTS: Writes data\recover_window.json only (never a snapshot file). Failures are ignored.
def publish_window(widget, path=INFO):
    try:
        widget.update_idletasks()
        record = {"pid": os.getpid(), "hwnd": int(widget.wm_frame(), 16)}
        temporary = Path(path).with_suffix(".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)
    except (OSError, ValueError):
        pass

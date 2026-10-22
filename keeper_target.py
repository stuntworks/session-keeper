# Last modified: 2026-10-19 | Change: Add a tab into an exact already-open terminal window and verify it landed | Full history: git log
"""Targets one existing Windows Terminal window from outside, then proves the tab landed there.

HOW A WINDOW IS TARGETED
- A window Session Keeper created has a name (SessionKeeper-<uuid>), so `wt -w <name> new-tab` reaches it exactly.
- Any other window has no name we can read from outside, and Windows Terminal's numeric window IDs are not
  exposed. For those we bring that exact HWND to the foreground, confirm it is the foreground window, then run
  `wt -w 0 new-tab`. `-w 0` means "the most recently used Terminal window", which Windows Terminal updates when
  a window is activated.

KNOWN RACE (why every add is verified and never retried)
- Between the focus and the launch, the user (or another program) can activate a different Terminal window, and
  Windows can refuse a focus change from a background process. Either way the new tab could land elsewhere.
- So after each launch the read-only tab probe is compared before and after: the target window must have exactly
  one more tab, no other window may change, and no window may appear. If that fails, we stop adding tabs to open
  windows for the rest of the run and report it. Nothing is closed or moved to "fix" it.
"""

import ctypes
import time
from ctypes import wintypes

FOCUS_SETTLE_SECONDS = 0.5
SW_RESTORE = 9


# WHAT IT DOES: Signals that a tab may have opened in the wrong place; the run stops adding to open windows.
# DEPENDS ON: Raised only after a launch whose landing could not be proved.
# AFFECTS: The caller reports it and does not retry.
class TargetMissed(RuntimeError):
    pass


# WHAT IT DOES: Brings one exact window handle to the foreground and confirms Windows accepted it.
# DEPENDS ON: user32 focus rules; Windows may refuse when this process did not receive the last input.
# AFFECTS: Changes which window is in front. Never moves, resizes or closes anything.
def bring_to_front(hwnd):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.GetForegroundWindow.restype = wintypes.HWND
    handle = wintypes.HWND(hwnd)
    if not user.IsWindow(handle):
        return False
    if user.IsIconic(handle):
        user.ShowWindow(handle, SW_RESTORE)
    user.SetForegroundWindow(handle)
    time.sleep(FOCUS_SETTLE_SECONDS)
    return (user.GetForegroundWindow() or 0) == hwnd


# WHAT IT DOES: Counts tabs per window handle from one read-only probe result.
# DEPENDS ON: read_windows output.
# AFFECTS: Landing verification only.
def tab_counts(windows):
    return {window.get("hwnd"): len(window.get("tabs") or []) for window in windows}


# WHAT IT DOES: Proves a launch added exactly one tab to the target and changed nothing else.
# DEPENDS ON: Probe results from just before and just after the launch.
# AFFECTS: A False result stops further adds to open windows.
def landed_in(before, after, hwnd):
    first, second = tab_counts(before), tab_counts(after)
    if set(first) != set(second) or hwnd not in first:
        return False
    return second[hwnd] == first[hwnd] + 1 and all(second[h] == first[h] for h in first if h != hwnd)


# WHAT IT DOES: Opens one saved conversation as a new tab in an exact open window, then verifies it.
# DEPENDS ON: Caller-supplied launch, session wait and window probe functions (patched in tests).
# AFFECTS: Starts one conversation; raises TargetMissed if its landing cannot be proved.
# DANGER ZONE HIGH: Focus then `wt -w 0` / breaks: tab lands in another window / depends on: no focus change in between.
def add_tab(hwnd, row, window_name, launch, wait_for_session, read_windows):
    before = read_windows()
    if hwnd not in tab_counts(before):
        raise RuntimeError("its window closed before the tab could be added")
    if not window_name and not bring_to_front(hwnd):
        raise RuntimeError("could not bring its window to the front, so nothing was opened")
    launch(window_name or "0", row)
    verified = wait_for_session(row["session_id"])
    after = read_windows()
    if not landed_in(before, after, hwnd):
        raise TargetMissed(f"{row['title']} opened, but not in its own window. Stopped adding tabs to open "
                           "windows; nothing was closed. Check your terminals.")
    return verified

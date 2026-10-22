# Last modified: 2026-10-19 | Change: Classify windows on saved spots and close only user-approved empty ones | Full history: git log
"""Decides whether a terminal window on a saved spot is LIVE, EMPTY or UNKNOWN, by process tree, never by title alone.

EMPTY means every tab in the window is a bare shell (cmd, PowerShell) with no child program. Tab titles are only used
to pair tabs with shell processes, and a pairing counts only when the number of tabs with that title equals the number
of tab processes with that console title and every one of those processes is bare. Anything else is UNKNOWN.
"""

import ctypes
import time
from ctypes import wintypes

from keeper_core import strip_leading_glyphs

SHELLS = {"cmd.exe", "powershell.exe", "pwsh.exe"}
HOSTS = {"windowsterminal.exe", "openconsole.exe"}
IGNORED_CHILDREN = {"conhost.exe", "openconsole.exe"}
WM_CLOSE = 0x0010
CLOSE_WAIT_SECONDS = 6
LIVE, EMPTY, UNKNOWN = "live", "empty", "unknown"


# WHAT IT DOES: Lists every process that runs directly inside a Windows Terminal tab, with its console title.
# DEPENDS ON: psutil process tree and the console-title probe.
# AFFECTS: Returns None when anything is unreadable, so no window can be called empty on partial evidence.
def read_shells():
    import psutil
    from keeper_codex import console_title
    shells = []
    try:
        for host in psutil.process_iter(["pid", "name"]):
            if (host.info.get("name") or "").lower() != "windowsterminal.exe":
                continue
            for process in host.children(recursive=True):
                name = process.name().lower()
                if name in HOSTS or name == "conhost.exe" or process.parent().name().lower() not in HOSTS:
                    continue
                children = [child for child in process.children() if child.name().lower() not in IGNORED_CHILDREN]
                title = console_title(process.pid)
                if title is None:
                    return None
                shells.append({"pid": process.pid, "name": name, "title": title,
                               "bare": name in SHELLS and not children})
    except (psutil.Error, OSError):
        return None
    return shells


# WHAT IT DOES: Normalises a tab or console title for pairing.
# DEPENDS ON: strip_leading_glyphs.
# AFFECTS: Pairing only.
def title_key(title):
    return strip_leading_glyphs(title or "").casefold()


# WHAT IT DOES: Classifies one live window as LIVE, EMPTY or UNKNOWN.
# DEPENDS ON: The window's matched tabs, every live window's tab titles, and read_shells output.
# AFFECTS: Only EMPTY windows may ever be offered for closing.
# DANGER ZONE HIGH: Fails closed / breaks: closing a window with work in it / depends on: exact count pairing.
def classify_window(group, all_groups, shells):
    tabs = group.get("tabs") or []
    if any(tab.get("outcome") == "MATCHED" and tab.get("sessionId") for tab in tabs):
        return LIVE
    if not tabs or shells is None:
        return UNKNOWN
    tab_titles = [title_key(tab.get("strippedTitle") or tab.get("rawTitle")) for other in all_groups
                  for tab in other.get("tabs") or []]
    for tab in tabs:
        key = title_key(tab.get("strippedTitle") or tab.get("rawTitle"))
        holders = [shell for shell in shells if title_key(shell["title"]) == key]
        if not key or not holders or len(holders) != tab_titles.count(key):
            return UNKNOWN
        if not all(shell["bare"] for shell in holders):
            return UNKNOWN
    return EMPTY


# WHAT IT DOES: Marks each occupant of each blocked spot with its state.
# DEPENDS ON: plan_restore's blocked list, live tab identities and shell list.
# AFFECTS: The confirm dialog and Preview text; a window not found in the identity scan is UNKNOWN.
def classify_blocked(blocked, groups, shells):
    by_hwnd = {group.get("hwnd"): group for group in groups}
    for spot in blocked:
        for occupant in spot["occupants"]:
            group = by_hwnd.get(occupant.get("hwnd"))
            occupant["state"] = classify_window(group, groups, shells) if group else UNKNOWN
    return blocked


# WHAT IT DOES: Asks one exact window to close, the same way clicking its X would.
# DEPENDS ON: user32 PostMessageW. Never kills a process.
# AFFECTS: Windows Terminal may show its own "close all tabs?" prompt; then the window stays open.
def close_window(hwnd):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    return bool(user.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0))


# WHAT IT DOES: Waits until a window handle no longer exists.
# DEPENDS ON: user32 IsWindow.
# AFFECTS: A window that does not close in time is reported and its spot is skipped.
def wait_closed(hwnd, timeout=CLOSE_WAIT_SECONDS):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindow.argtypes = [wintypes.HWND]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not user.IsWindow(wintypes.HWND(hwnd)):
            return True
        time.sleep(0.25)
    return not user.IsWindow(wintypes.HWND(hwnd))


# WHAT IT DOES: Closes approved EMPTY windows on one spot, then proves they are gone and no live chat was lost.
# DEPENDS ON: Fresh probes passed in by the caller: read_windows, read_groups(windows), read_shells, live_ids.
# AFFECTS: Returns (True, None) when the spot is free; (False, reason) to skip it; raises if a live chat vanished.
# DANGER ZONE HIGH: Only exact approved HWNDs, re-verified EMPTY right now / breaks: user's work / depends on: fresh scan.
def close_empty_spot(hwnds, approved, read_windows, read_groups, read_shells_now, live_ids):
    if not hwnds or any(hwnd not in approved for hwnd in hwnds):
        return False, "a window on this spot was not approved for closing"
    windows = read_windows()
    groups = read_groups(windows)
    shells = read_shells_now()
    by_hwnd = {group.get("hwnd"): group for group in groups}
    for hwnd in hwnds:
        group = by_hwnd.get(hwnd)
        if not group or classify_window(group, groups, shells) != EMPTY:
            return False, "a window on this spot no longer looks empty, so it was left alone"
    before = live_ids()
    open_after = []
    for hwnd in hwnds:
        close_window(hwnd)
        if not wait_closed(hwnd):
            open_after.append(hwnd)
    lost = before - live_ids()
    if lost:
        raise RuntimeError("A running chat disappeared while closing empty terminals. Recovery stopped.")
    remaining = {window.get("hwnd") for window in read_windows()}
    if open_after or remaining & set(hwnds):
        return False, "an empty terminal did not close (Windows Terminal may be asking you to confirm)"
    return True, None

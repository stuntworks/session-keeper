# Last modified: 2026-11-10 | Change: Review fixes: session-ID proof for several new windows, like-for-like baseline, 10-minute pending, still-starting skip | Full history: git log
"""Finding the terminal window a restore just launched, and adopting it if it shows up too late.

Right after a crash 30 chats may be resuming at once and Windows Terminal can take far longer than 15 s to
show a new window. The wait is now WINDOW_WAIT_SECONDS, polled with a cheap handle list (no PowerShell) and
the full window probe only once a new handle exists. If the window still does not appear, the launch is
written to data\\pending_windows.jsonl; when that window turns up later (end of this run, or the next
Restart) it is adopted ONLY when it is provably the one this restore launched (see adopt_late_windows).
A pending launch counts for PENDING_MINUTES only: within that time a Restart never launches its chats again
(still_starting), and after it the entry is ignored entirely (no adoption, no probe).
Windows Terminal's own window name (the -w SessionKeeper-<uuid>) cannot be read back from a window by
any API found (not the HWND title, not UI Automation), so the proof is the exact session ID plus the time bound.
"""

import ctypes
import json
import subprocess
import time
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keeper_core import DATA, strip_leading_glyphs
from keeper_created import group_key, record_created_window

WINDOW_WAIT_SECONDS = 90      # bounded: a loaded machine after a crash, not forever
POLL_SECONDS = 0.5            # cheap handle check
PROBE_SECONDS = 1.5           # after a full probe (a PowerShell run) wait a little longer
TERMINAL_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"
PENDING_LOG = DATA / "pending_windows.jsonl"
PENDING_MINUTES = 10          # a launch older than this is no longer "still starting" and is never adopted
clock = time.monotonic        # tests swap these two for a fake clock
pause = time.sleep


# WHAT IT DOES: Raised when the launched window did not appear within the bounded wait.
# AFFECTS: open_group records the launch as pending so a late window can be adopted.
class WindowNotSeen(RuntimeError):
    pass


# WHAT IT DOES: The handles of every top-level Windows Terminal window, or None when they cannot be listed.
# DEPENDS ON: user32 EnumWindows / GetClassNameW. Read-only, microseconds; no PowerShell.
# AFFECTS: Only decides when the slower UI Automation probe is worth running.
def terminal_handles():
    try:
        user = ctypes.WinDLL("user32", use_last_error=True)
        found = set()
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        # WHAT IT DOES: Keeps one window handle when its class is Windows Terminal's.
        # DEPENDS ON: GetClassNameW. AFFECTS: The local found set only.
        def collect(handle, _):
            name = ctypes.create_unicode_buffer(64)
            if user.GetClassNameW(handle, name, 64) and name.value == TERMINAL_CLASS:
                found.add(int(handle or 0))
            return True
        if not user.EnumWindows(callback_type(collect), 0):
            return None
        return found
    except (AttributeError, OSError):
        return None


# WHAT IT DOES: Waits (bounded) for the new Windows Terminal window this launch opened.
# DEPENDS ON: read (keeper_core.read_windows), terminal_handles, clock/pause; baseline = terminal_handles()
# taken just before the launch (same lister, so hidden windows of that class never look new); groups(windows)
# (live tab identities) and session_id, the chat just launched.
# AFFECTS: Returns the window dict. ONE new window: it must carry the expected title (as before). SEVERAL new
# windows: one is accepted only when the identity scan proves it holds session_id; a title alone never
# decides (Claude retitles its window, and the owner may open one by hand). Raises WindowNotSeen after the
# wait; RuntimeError when several new windows stayed unproven (that group stops, as before).
# DANGER ZONE HIGH: Never return a window not proven new and ours / breaks: moving, maximising and recording
# a stranger's terminal / depends on: previous_handles and baseline read before the launch.
def find_new_window(previous_handles, expected_title, read, timeout_seconds=None, handles=None, baseline=None,
                    session_id=None, groups=None):
    timeout_seconds = WINDOW_WAIT_SECONDS if timeout_seconds is None else timeout_seconds
    list_handles = handles or terminal_handles
    base = set(previous_handles if baseline is None else baseline)
    expected = expected_title.casefold()
    deadline = clock() + timeout_seconds
    crowded = False
    while True:
        current = list_handles()
        probed = False
        if current is None or set(current) - base:
            probed = True
            try:
                windows = read()
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
                windows = []                     # a window still starting can upset the probe; try again
            new = [w for w in windows if w.get("hwnd") not in previous_handles]
            crowded = len(new) > 1
            if len(new) == 1 and expected in strip_leading_glyphs(new[0].get("title") or "").casefold():
                return new[0]
            if crowded:
                if not (groups and session_id):
                    raise RuntimeError(CROWDED)
                proven = proven_windows(new, groups, session_id)
                if len(proven) == 1:
                    return proven[0]
        if clock() >= deadline:
            if crowded:
                raise RuntimeError(CROWDED)
            raise WindowNotSeen(f"its new terminal window has not appeared after {int(timeout_seconds)} seconds; "
                                "it is probably still starting. Session Keeper moves it to its spot when it appears. "
                                f"To fix: nothing now. If it is still missing in {PENDING_MINUTES} minutes, press "
                                "Restart selected again")
        pause(PROBE_SECONDS if probed else POLL_SECONDS)


CROWDED = ("another terminal window opened at the same moment, so Session Keeper did not move either one. "
           "To fix: close the extra new window, then press Restart selected again")


# WHAT IT DOES: The new windows that the live identity scan shows holding session_id.
# DEPENDS ON: groups(windows) returning live windows with MATCHED tabs. A failed scan proves nothing.
# AFFECTS: Read-only.
def proven_windows(new, groups, session_id):
    try:
        live = groups([dict(w) for w in new])
    except Exception:
        return []
    holders = {g.get("hwnd") for g in live or []
               if any(t.get("outcome") == "MATCHED" and t.get("sessionId") == session_id for t in g.get("tabs") or [])}
    return [w for w in new if w.get("hwnd") in holders]


# WHAT IT DOES: Writes one launch whose window did not appear in time, so a late window can be adopted.
# DEPENDS ON: The saved window (its tab IDs), the first tab's ID and title, the target spot, the handles
# that existed before the launch, and this boot's id.
# AFFECTS: Appends to data\pending_windows.jsonl only. Returns True when written, False when it could not be.
def record_pending(window_name, snapshot, window, window_index, session_id, title, rect, known_hwnds, boot_id,
                   maximize=False, path: Path = None):
    entry = {"window_name": window_name, "group": group_key(snapshot, window_index), "boot_id": boot_id,
             "session_id": session_id, "title": title, "rect": rect, "maximize": bool(maximize),
             "group_ids": [t.get("sessionId") for t in window.get("tabs") or [] if t.get("sessionId")],
             "known_hwnds": sorted(h for h in known_hwnds if isinstance(h, int)),
             "at": datetime.now(timezone.utc).isoformat()}
    try:
        with open(path or PENDING_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        return False
    return True


# WHAT IT DOES: Notes that a pending launch's window has been adopted, so it is never adopted twice.
# DEPENDS ON: The data folder being writable. AFFECTS: Appends one line to data\pending_windows.jsonl.
def mark_adopted(window_name, hwnd, boot_id, path: Path = None):
    with open(path or PENDING_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"adopted": window_name, "hwnd": hwnd, "boot_id": boot_id}) + "\n")


# WHAT IT DOES: The pending launches of this boot, made in the last PENDING_MINUTES, not adopted yet.
# Unreadable lines, and entries without a readable time, are ignored.
# DEPENDS ON: data\pending_windows.jsonl (missing = nothing pending); now (default: the clock).
# AFFECTS: Read-only. A launch from an earlier boot is never trusted, since handles get reused.
def load_pending(boot_id, path: Path = None, now=None) -> list[dict]:
    log = path or PENDING_LOG
    oldest = (now or datetime.now(timezone.utc)) - timedelta(minutes=PENDING_MINUTES)
    if not log.exists():
        return []
    pending, adopted = [], set()
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("boot_id") != boot_id:
            continue
        if entry.get("adopted"):
            adopted.add(entry["adopted"])
        elif entry.get("window_name") and entry.get("session_id") and isinstance(entry.get("rect"), dict) \
                and launched_at(entry) is not None and launched_at(entry) >= oldest:
            pending.append(entry)
    return [entry for entry in pending if entry["window_name"] not in adopted]


# WHAT IT DOES: The time a pending entry was written, or None when it is missing or unreadable.
# DEPENDS ON: The entry's "at" field. AFFECTS: Pure.
def launched_at(entry):
    try:
        moment = datetime.fromisoformat(str(entry.get("at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


# WHAT IT DOES: Splits off the selected rows whose saved window has a launch still starting (pending, recent):
# none of that window's chats is launched again, so a slow window never becomes a second copy of a chat.
# DEPENDS ON: load_pending, keeper_core.recovery_rows, group_key.
# AFFECTS: Returns (keys to restore now, one plain line per held-back tab). Pure apart from reading the log.
def still_starting(snapshot, selected_keys, boot_id):
    from keeper_core import recovery_rows
    waiting = {entry["group"]: entry for entry in load_pending(boot_id)}
    if not waiting:
        return set(selected_keys), []
    keep, lines = set(), []
    for row in recovery_rows(snapshot):
        if row["key"] not in selected_keys:
            continue
        entry = waiting.get(group_key(snapshot, row["window_index"]))
        if entry is None:
            keep.add(row["key"])
        else:
            lines.append(f"Not opened again: '{row['title']}'. Its window ('{entry['title']}') is still starting. "
                         f"To fix: nothing now; press Restart selected in a few minutes to fill that window.")
    keep |= set(selected_keys) - {row["key"] for row in recovery_rows(snapshot)}
    return keep, lines


# WHAT IT DOES: Adopts each late window that is provably the one a pending launch opened: records it as a
# Session Keeper window (so the next Restart fills it by name) and moves it to its saved spot.
# DEPENDS ON: read_windows and read_groups(windows) (live tab identities); place(hwnd, rect, known, maximize=).
# AFFECTS: owned (this boot's created-window records) gains each adopted window; warnings get one line each.
# Returns the adopted handles. Costs nothing (no probe) when nothing is pending.
# DANGER ZONE HIGH: Proof = handle absent before that launch, not already a Session Keeper window, EVERY tab
# matched to an exact session ID, the launched chat among them and no chat from outside its saved group,
# and exactly one such window / breaks: moving or filling a stranger's terminal / depends on: identity scan.
def adopt_late_windows(owned, boot_id, read_windows, read_groups, place, warnings):
    pending = load_pending(boot_id)
    if not pending:
        return []
    groups = read_groups(read_windows())
    adopted = []
    for entry in pending:
        taken = {o.get("hwnd") for o in owned}
        candidates = []
        for group in groups:
            hwnd, tabs = group.get("hwnd"), group.get("tabs") or []
            if not isinstance(hwnd, int) or hwnd in entry["known_hwnds"] or hwnd in taken or not tabs:
                continue
            ids = [t.get("sessionId") for t in tabs if t.get("outcome") == "MATCHED" and t.get("sessionId")]
            if len(ids) != len(tabs) or entry["session_id"] not in ids or not set(ids) <= set(entry["group_ids"]):
                continue
            candidates.append(hwnd)
        if len(candidates) != 1:
            continue
        hwnd = candidates[0]
        record_created_window(entry["window_name"], hwnd, entry["group"], boot_id, rect=entry["rect"])
        mark_adopted(entry["window_name"], hwnd, boot_id)
        owned.append({"window_name": entry["window_name"], "hwnd": hwnd, "group": entry["group"],
                      "boot_id": boot_id})
        adopted.append(hwnd)
        try:
            placed = place(hwnd, entry["rect"], set(entry["known_hwnds"]), maximize=entry.get("maximize", False))
            warnings.append(f"The window for '{entry['title']}' opened late; it was moved to its saved spot. "
                            "Nothing to fix.")
            if placed is False:
                warnings.append(not_maximised(entry["title"]))
        except Exception as error:
            warnings.append(f"The window for '{entry['title']}' opened late and could not be moved to its spot "
                            f"({error}). Nothing is lost: drag it where you want.")
    return adopted


# WHAT IT DOES: The plain line for a window placed at its spot that Windows did not maximise.
# DEPENDS ON: Nothing. AFFECTS: Text only.
def not_maximised(title):
    return (f"'{title}' opened at its saved spot but did not maximise. To fix: double-click its title bar "
            "if you want it full screen.")

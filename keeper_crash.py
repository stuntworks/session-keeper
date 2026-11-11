# Last modified: 2026-11-10 | Change: Review fixes: terminal chats only, recent-crash preselect, layout age label, drop wording, two crashes, offline drives, saved died list | Full history: git log
"""After Windows Terminal crashes, three things matter for recovery (2026-11-10 crash at 00:08:42):

1. WHEN it crashed. Read from the Windows Application event log (event 1000, WindowsTerminal.exe, this boot).
   If the log cannot be read, a sharp fall in matched chats between two neighbouring saved layouts stands in.
2. WHICH saved layout was the last one before it, so the screen can offer "Before the crash" even after a
   few tabs were reopened by hand and "Latest" became a tiny post-crash layout.
3. WHICH chats died in it but are not in the layout shown: every Claude Code CLI flushes its transcript
   as it dies, so transcripts last written in the burst right after the crash are the dead chats. Codex
   rollouts are NOT used: nothing shows Codex writing its rollout when it is killed, so its last write can
   be long before the crash and the burst rule would miss or misplace it.

Read-only everywhere: ~/.claude and the saved layout files are only read. The extra "no saved place"
window lives only in the copy of the layout the screen shows; it is never written to a snapshot file.
"""

import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from keeper_chooser import captured
from keeper_heartbeat import DROP, MIN_DROP_TABS

BURST_BEFORE = timedelta(seconds=10)     # a transcript written just before the logged crash moment
BURST_AFTER = timedelta(minutes=2)       # every CLI flushed within ~90 s of the 2026-11-10 crash
EVENT_TIMEOUT = 20                       # seconds; the event-log read normally takes ~2 s
CRASH_TITLE = "Open at the crash, no saved place"
DROP_TITLE = "Open before many tabs closed, no saved place"   # no event log: never call it a crash
RECENT_CRASH = timedelta(hours=6)        # "Before the crash" is preselected only this long after the crash
FRESH = timedelta(minutes=10)            # a layout saved longer than this before the crash is called old
DROP_GAP = timedelta(minutes=10)         # wider gap between two layouts: when they died is unknown, no search
FOLDER_DEADLINE = 3.0                    # seconds for ALL folder checks; a dead network drive hangs much longer
HEAD_BYTES = 16384                       # same head peek_more reads
ENTRYPOINT = re.compile(rb'"entrypoint"\s*:\s*"([^"]*)"')
DONE = "SESSION-KEEPER-DONE"

# PowerShell: the current boot time, then every WindowsTerminal.exe "Application Error" (event 1000)
# since then, one UTC time per line, then DONE. "No events found" is a normal, empty answer.
EVENT_SCRIPT = (
    "$ErrorActionPreference='Stop'; "
    "$boot=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; "
    "try { $found=@(Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error'; "
    "Id=1000; StartTime=$boot}) } "
    "catch { if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $found=@() } else { throw } }; "
    "$found | Where-Object { [string]$_.Properties[0].Value -ieq 'WindowsTerminal.exe' } | "
    "ForEach-Object { $_.TimeCreated.ToUniversalTime().ToString('o') }; "
    f"'{DONE}'")


# WHAT IT DOES: Times (local, timezone-aware, oldest first) of every Windows Terminal crash since boot.
# DEPENDS ON: keeper_core.run_hidden (no window, no pipes) and Windows PowerShell's Get-WinEvent.
# AFFECTS: Read-only. Returns [] when the log says no crash, None when the log could not be read
# (then callers fall back to the sharp-drop rule). Never raises. Call it OFF the Tk thread (~2 s).
def read_crash_times(run=None, timeout=EVENT_TIMEOUT):
    try:
        if run is None:
            from keeper_core import run_hidden as run
        result = run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", EVENT_SCRIPT],
                     timeout=timeout)
        lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        if result.returncode or DONE not in lines:
            return None
        times = [datetime.fromisoformat(line.replace("Z", "+00:00")).astimezone()
                 for line in lines if line != DONE]
        return sorted(time for time in times if time.tzinfo is not None)
    except Exception:
        return None


# WHAT IT DOES: The heartbeat's "sharp drop" idea for matched chats: at least MIN_DROP_TABS lost AND at least
# DROP (half) of them gone. Same thresholds as keeper_heartbeat, not a copy of them.
# AFFECTS: Pure.
def sharp_fall(before, after):
    return before - after >= MIN_DROP_TABS and after <= before * (1 - DROP)


# WHAT IT DOES: Matched-chat count of one saved layout (0 when unknown).
def matched(snapshot):
    value = (snapshot.get("counts") or {}).get("matched", 0) if isinstance(snapshot, dict) else 0
    return value if isinstance(value, int) else 0


# WHAT IT DOES: History index of the newest normal layout captured strictly BEFORE `moment`, else None.
# DEPENDS ON: history newest first (read_history / fast_history order).
# AFFECTS: Pure.
def before_index(history, moment):
    for index, (_, snapshot) in enumerate(history):
        when = captured(snapshot)
        if not snapshot.get("legacy") and when is not None and when < moment:
            return index
    return None


# WHAT IT DOES: Fallback when the event log is unreadable: the newest neighbouring pair of layouts on the
# newest layout's boot where matched chats fell sharply. Returns (older_index, newer_index) or None.
# DEPENDS ON: history newest first; only the layouts the screen loaded (the newest ~200 plus checkpoints).
# AFFECTS: Pure. Many tabs closed on purpose look the same, so the screen calls it "many tabs closed".
def newest_drop(history):
    boot = history[0][1].get("boot_id") if history else None
    normal = [index for index, (_, snapshot) in enumerate(history)
              if not snapshot.get("legacy") and snapshot.get("boot_id") == boot and captured(snapshot)]
    for newer, older in zip(normal, normal[1:]):
        if sharp_fall(matched(history[older][1]), matched(history[newer][1])):
            return older, newer
    return None


# WHAT IT DOES: Claude Code chats whose transcript was last written between start and end (the crash
# burst). Live and already-shown chats are filtered later, by shown_layout, against the live set then.
# DEPENDS ON: keeper_sessions.list_transcripts (file stat only) and peek_more (first/last 16 KB of a file).
# AFFECTS: Read-only. Returns [{"session_id", "title", "cwd", "stopped", "entrypoint"}], oldest stop first.
# cwd is None when the transcript does not name its folder: never guessed. Only terminal chats are kept:
# entrypoint "cli". Desktop app and SDK chats ("claude-desktop", "sdk-cli", "sdk-py") are dropped, because
# nothing else would stop them being reopened as a second copy in a terminal. Old transcripts with no
# entrypoint field (before 2026-10-12) are kept with entrypoint None: shown, never restorable.
# DANGER ZONE HIGH: Never keep a non-"cli" entrypoint / breaks: a Desktop chat opened twice / depends on:
# the field being in the transcript's first 16 KB (it is on the first line Claude Code writes).
def burst_sessions(start, end, claude_root=None, lister=None, peek=None):
    if lister is None or peek is None:
        from keeper_sessions import list_transcripts, peek_more
        lister, peek = lister or list_transcripts, peek or peek_more
    start, end = local_naive(start), local_naive(end)
    found = []
    for entry in lister(claude_root=claude_root) if claude_root else lister():
        if entry.get("kind") != "claude" or not start <= entry["modified"] <= end:
            continue
        entrypoint = entry_point(entry.get("path"))
        if entrypoint not in ("cli", None):
            continue
        try:
            title, cwd, _, _ = peek(entry, {})
        except (OSError, ValueError, TypeError):
            title, cwd = None, None
        found.append({"session_id": entry["session_id"], "title": title or f"Claude {entry['session_id'][:8]}",
                      "cwd": cwd if isinstance(cwd, str) and cwd.strip() else None, "stopped": entry["modified"],
                      "entrypoint": entrypoint})
    return sorted(found, key=lambda item: item["stopped"])


# WHAT IT DOES: The "entrypoint" a Claude transcript's head names ("cli" = a terminal chat), else None.
# DEPENDS ON: The first HEAD_BYTES of the file. An unreadable file gives "unreadable" (never kept).
# AFFECTS: Read-only.
def entry_point(path):
    try:
        with open(path, "rb") as handle:
            found = ENTRYPOINT.search(handle.read(HEAD_BYTES))
    except (OSError, TypeError, ValueError):
        return "unreadable"
    return found.group(1).decode("utf-8", "replace") if found else None


# WHAT IT DOES: A timezone-aware time as a naive local time (list_transcripts' "modified" is naive local).
def local_naive(moment):
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


# WHAT IT DOES: Every saved project folder (in the loaded layouts and the dead chats) that cannot be used
# now. Returns (not_usable, offline): offline is the part whose drive is not there or did not answer in time
# ("drive not connected"); the rest of not_usable are folders that are gone from a drive that is there.
# DEPENDS ON: is_dir on each folder and each drive root, all at once on daemon threads, with ONE overall
# deadline: a dead network drive can hang one check for a minute, so a check still running is "offline".
# AFFECTS: Read-only. Tabs in these folders are shown but cannot be restored (keeper_core.recovery_rows).
def gone_folders(history, died=(), is_dir=os.path.isdir, deadline=FOLDER_DEADLINE):
    folders = {tab.get("cwd") for _, snapshot in history for window in snapshot.get("windows") or []
               for tab in window.get("tabs") or [] if isinstance(tab, dict)}
    folders |= {item.get("cwd") for item in died}
    folders = {folder for folder in folders if isinstance(folder, str) and folder.strip()}
    answers = checked(folders | {drive_root(folder) for folder in folders}, is_dir, deadline)
    root_there = lambda folder: not drive_root(folder) or answers.get(drive_root(folder)) is True
    offline = {folder for folder in folders
               if folder not in answers or (answers[folder] is False and not root_there(folder))}
    gone = {folder for folder in folders if answers.get(folder) is False and folder not in offline}
    return gone | offline, offline


# WHAT IT DOES: "C:\\" for "C:\\work\\x", "\\\\nas\\share\\" for a network path ("" when there is none).
def drive_root(folder):
    drive = os.path.splitdrive(folder)[0]
    return drive + "\\" if drive else ""


# WHAT IT DOES: is_dir for every path at once; {path: True/False} for the answers in by the deadline.
# AFFECTS: Starts one daemon thread per path; a hung one is left behind and never blocks the app's exit.
def checked(paths, is_dir, deadline):
    answers, threads = {}, []

    def check(path):
        try:
            answers[path] = bool(is_dir(path))
        except Exception:
            answers[path] = False
    for path in paths:
        if path:
            threads.append(threading.Thread(target=check, args=(path,), daemon=True))
            threads[-1].start()
    end = time.monotonic() + deadline
    for thread in threads:
        thread.join(max(0.0, end - time.monotonic()))
    return dict(answers)


# WHAT IT DOES: Everything the recovery screen needs about a crash, gathered off the Tk thread.
# DEPENDS ON: crash_times from read_crash_times ([] = none, None = log unreadable -> drop fallback),
# history newest first, live session ids. burst and is_dir are injectable for tests.
# AFFECTS: Read-only, except that remember (keeper_crash_record.died_for) may save the died list once.
# Returns {"crash": None or {time, source, index, last, label_time, layout_time, fresh, preselect}, "died", "gone",
# "offline", "monitors"}.
# Several crashes this boot: every one in the last RECENT_CRASH counts; the layout before the one that lost
# the most matched chats is offered, and the chats that died in any of them are listed.
# preselect: the screen opens on "Before the crash" only while the crash is recent, no real restore has
# finished since it (restored_at, keeper_crash_record), and a chat from it is still not open.
# fresh: the layout is from this boot and saved at most FRESH before the crash, else the label says "old".
def crash_facts(history, live, crash_times, burst=burst_sessions, is_dir=os.path.isdir, monitors=None,
                now=None, restored_at=None, remember=None, boot=None, deadline=FOLDER_DEADLINE):
    now = (now or datetime.now(timezone.utc)).astimezone()
    remember = remember or (lambda moment, source, compute: compute())
    crash, died = None, []
    if crash_times:
        recent = [moment for moment in crash_times if now - moment <= RECENT_CRASH] or crash_times[-1:]
        indexes = {moment: before_index(history, moment) for moment in recent}
        size = lambda moment: -1 if indexes[moment] is None else matched(history[indexes[moment]][1])
        moment = max(recent, key=lambda moment: (size(moment), moment))
        crash = {"time": moment, "source": "event", "index": indexes[moment], "last": max(recent)}
        for when in recent:
            died += remember(when, "event", lambda when=when: burst(when - BURST_BEFORE, when + BURST_AFTER))
        died = list({item["session_id"]: item for item in died}.values())
    elif crash_times is None and (pair := newest_drop(history)):
        older, newer = (captured(history[index][1]) for index in pair)
        crash = {"time": newer, "source": "drop", "index": pair[0], "last": newer}
        if newer - older <= DROP_GAP:          # wider: nobody knows when they stopped, so say nothing
            died = remember(newer, "drop", lambda: burst(older, newer + BURST_AFTER))
    gone, offline = gone_folders(history, died, is_dir, deadline)
    if crash is not None:
        rows, index = [], crash["index"]
        layout = captured(history[index][1]) if index is not None else None
        boot = boot if boot is not None else history[0][1].get("boot_id")
        crash.update(layout_time=layout, label_time=layout or crash["time"],
                     fresh=layout is not None and history[index][1].get("boot_id") == boot
                     and crash["time"] - layout <= FRESH)
        if index is not None:
            from keeper_core import recovery_rows
            rows = recovery_rows(mark_gone(history[index][1], gone, offline))
        waiting = {row["session_id"] for row in rows} | {
            item["session_id"] for item in died
            if item["cwd"] and item["cwd"] not in gone and item.get("entrypoint") == "cli"}
        restored = restored_at is not None and restored_at > crash["last"]     # after the newest crash
        crash["preselect"] = (index is not None and now - crash["time"] <= RECENT_CRASH and not restored
                              and bool(waiting - set(live)))
    return {"crash": crash, "died": died, "gone": gone, "offline": offline, "monitors": monitors}


# WHAT IT DOES: History index the screen opens on: the "Before the crash" layout when preselected, else 0.
# AFFECTS: Pure. Only a layout is preselected; nothing is ever launched by this.
def start_index(history, extras):
    crash = (extras or {}).get("crash")
    if crash and crash.get("preselect") and crash.get("index") is not None and crash["index"] < len(history):
        return crash["index"]
    return 0


# WHAT IT DOES: Copy of a layout whose tabs in a missing folder carry "folder_missing" (so they are shown
# but recovery_rows leaves them out), plus "drive_offline" when the folder's drive is not connected.
# The given layout is never changed.
# AFFECTS: Pure. Returns the same object when nothing needs marking.
def mark_gone(snapshot, gone, offline=()):
    if not gone or not any(tab.get("cwd") in gone for window in snapshot.get("windows") or []
                           for tab in window.get("tabs") or []):
        return snapshot
    windows = [dict(window, tabs=[gone_tab(tab, offline) if tab.get("cwd") in gone else tab
                                  for tab in window.get("tabs") or []])
               for window in snapshot.get("windows") or []]
    return dict(snapshot, windows=windows)


# WHAT IT DOES: A tab copy marked not restorable because of its folder (and why: gone, or drive offline).
def gone_tab(tab, offline=()):
    return dict(tab, folder_missing=True, **({"drive_offline": True} if tab.get("cwd") in offline else {}))


# WHAT IT DOES: The layout the screen shows: the saved one, with missing folders marked and, after a crash,
# ONE extra window holding the chats that died in it and are neither open now nor in this layout.
# DEPENDS ON: extras from crash_facts (None: the saved layout exactly); live session ids at load time.
# AFFECTS: Returns a NEW dict; the saved snapshot and its file are never changed. The extra window gets a
# real spot on the primary monitor (keeper_home.minimised_rect, the existing fallback) so plan_restore and
# restore_selected treat it like any other window, with all their duplicate-live checks.
# DANGER ZONE HIGH: Never write this result to a snapshot file / breaks: history is the evidence of what was
# really on screen / depends on: callers keeping it in memory only.
def shown_layout(snapshot, extras, live):
    if not extras:
        return snapshot
    gone, offline = extras.get("gone") or set(), extras.get("offline") or set()
    shown = mark_gone(snapshot, gone, offline)
    crash = extras.get("crash")
    present = {tab.get("sessionId") for window in shown.get("windows") or [] for tab in window.get("tabs") or []}
    extra = [item for item in extras.get("died") or []
             if item["session_id"] not in set(live) and item["session_id"] not in present]
    if not crash or not extra:
        return shown
    # origin_unknown: an old transcript that does not say it was a terminal chat. Shown, never restorable.
    tabs = [{"rawTitle": item["title"], "strippedTitle": item["title"], "outcome": "MATCHED", "kind": "claude",
             "sessionId": item["session_id"], "cwd": item["cwd"], "method": "crash-transcript",
             "role": "interactive", "entrypoint": item.get("entrypoint"),
             **({} if item.get("entrypoint") == "cli" else {"origin_unknown": True})}
            for item in extra]
    tabs = [gone_tab(tab, offline) if tab["cwd"] in gone else tab for tab in tabs]
    drop = crash.get("source") == "drop"
    title = DROP_TITLE if drop else CRASH_TITLE
    window = dict(crash_rect(shown.get("monitors") or extras.get("monitors")), tabs=tabs, windowTitle=title,
                  crash_group=True)
    windows = list(shown.get("windows") or [])
    if windows and all(type(item.get("z")) is int for item in windows):
        window["z"] = min(item["z"] for item in windows) - 1       # drawn and reopened in front
    when = clock_text(crash["time"])
    note = (f"{len(extra)} chat(s) that stopped when many tabs closed ({when}) are not in this layout. "
            if drop else f"{len(extra)} chat(s) open at the crash ({when}) are not in this layout. ")
    note += f"They are in the window marked '{title}'. Codex chats are not checked."
    return dict(shown, windows=windows + [window], note=" ".join(part for part in (shown.get("note"), note) if part))


# WHAT IT DOES: A real, visible spot for the extra window: the existing minimised-window fallback (primary
# monitor, 60 px in, at most 1600 x 900), or a plain 1600 x 900 at 60,60 when no monitor is known.
# AFFECTS: Pure. At restore time keeper_display.keep_visible moves it onto a monitor that exists then.
def crash_rect(monitors):
    from keeper_home import minimised_rect
    return minimised_rect(monitors) or {"x": 60, "y": 60, "w": 1600, "h": 900}


# WHAT IT DOES: "12:08 AM" for today, "Nov 9, 12:08 AM" otherwise.
def clock_text(moment, now=None):
    moment = moment.astimezone()
    now = (now or datetime.now(timezone.utc)).astimezone()
    day = "" if moment.date() == now.date() else f"{moment:%b} {moment.day}, "
    return day + moment.strftime("%I:%M %p").lstrip("0")


# WHAT IT DOES: Chooser label for the crash entry, with the LAYOUT's own date, time and age:
# "Before the crash - saved Nov 10, 6:58 AM - 5 hours ago". A layout from another boot or saved more than
# FRESH before the crash says so: "Last layout before the crash - 12 days old (Oct 29, 2:44 PM)".
# A drop found without the event log is called what it is: "Before many tabs closed - saved ...".
# DEPENDS ON: keeper_recorder_health.saved_when / amount (the header's wording).
# AFFECTS: Text only.
def crash_label(crash, now=None):
    from keeper_recorder_health import amount, saved_when
    now = (now or datetime.now(timezone.utc)).astimezone()
    layout = crash["label_time"].astimezone()
    when = saved_when({"captured_at": layout.isoformat()}, now)
    if crash["source"] != "event":
        return f"Before many tabs closed - saved {when}"
    if crash.get("fresh", True):
        return f"Before the crash - saved {when}"
    return f"Last layout before the crash - {amount((now - layout).total_seconds())} old ({when.split(' - ')[0]})"


# WHAT IT DOES: Snapshot files the prune plan must keep: the last layout before each crash time, and the
# older layout of every sharp drop between neighbouring layouts of one boot. Returns {path: reason}.
# DEPENDS ON: items as keeper_prune.scan returns them ((path, snapshot-or-None)); crash_times aware datetimes.
# AFFECTS: Pure. keeper_prune keeps these for its longest tier (30 days) and says why in its report.
def prune_protect(items, crash_times=()):
    valid = []
    for path, snapshot in items:
        if isinstance(snapshot, dict) and not snapshot.get("legacy"):
            when = captured(snapshot)
            if when is not None:
                valid.append((when, str(path), path, snapshot))
    valid.sort(key=lambda row: (row[0], row[1]))
    reasons = {}
    for moment in crash_times or ():
        older = [row for row in valid if row[0] < moment]
        if older:
            reasons[older[-1][2]] = f"last layout before the Windows Terminal crash at {clock_text(moment)}"
    for (when, _, path, snapshot), (_, _, _, newer) in zip(valid, valid[1:]):
        if snapshot.get("boot_id") == newer.get("boot_id") and sharp_fall(matched(snapshot), matched(newer)):
            reasons.setdefault(path, f"last layout before many tabs closed ({clock_text(when)})")
    return reasons

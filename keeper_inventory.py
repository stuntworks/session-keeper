# Last modified: 2026-11-10 | Change: Maximised flag kept in the snapshot; one unreadable process no longer ends the whole capture | Full history: git log
"""Live process identity, separate from a window's screen location."""

import json
from pathlib import Path
import psutil
from keeper_codex import active_codex_sessions, console_title
from keeper_core import strip_leading_glyphs
from keeper_roles import ParentSnapshot, process_role
from keeper_claude_identity import open_claude_record


# WHAT IT DOES: Reads a Claude session record only if it belongs to this process lifetime.
# DEPENDS ON: Claude's PID record, process creation time, and session UUID.
# AFFECTS: Rejects stale records after Windows reuses a process number.
def claude_record(process, directory=None):
    path = (directory or Path.home() / ".claude" / "sessions") / f"{process.pid}.json"
    try:
        if path.stat().st_mtime < process.create_time():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("pid") == process.pid and record.get("sessionId"):
            return record
    except (OSError, ValueError):
        pass
    return None


# WHAT IT DOES: The process's working folder, or None when Windows will not say.
def process_folder(process):
    try:
        return process.cwd()
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


# WHAT IT DOES: Adds one "could not read" line for a process to `skipped` (when the caller passed a list).
# DEPENDS ON: Nothing.
# AFFECTS: The list only; the recorder logs it (keeper_heartbeat), at most once per change.
def note_skip(skipped, program, pid, what, error):
    if skipped is not None:
        detail = ((str(error) or type(error).__name__).splitlines() or [""])[0][:120]
        skipped.append(f"{program} {pid}: {what} ({type(error).__name__}: {detail})")


# WHAT IT DOES: Runs one read of one process; any failure except "process gone" gives `fallback` instead.
# DEPENDS ON: read() touching one process only.
# AFFECTS: The failure is noted in skipped. psutil.NoSuchProcess still propagates (the process is skipped).
def guarded(read, fallback, skipped, program, pid, what):
    try:
        return read()
    except psutil.NoSuchProcess:
        raise
    except Exception as error:
        note_skip(skipped, program, pid, what, error)
        return fallback


# WHAT IT DOES: Inspects one claude.exe. Returns its session row, or None when it is left out.
# DEPENDS ON: The same checks live_inventory always made; unreadable open files or console title only
# make the row unidentified (uncertain), exactly like a Claude that has no transcript open yet.
# AFFECTS: Nothing.
def inspect_claude(process, snapshot, read_titles, skipped):
    role = process_role(process, "claude", snapshot)
    if role == "service":
        return None
    record = claude_record(process)
    method = "pid-record"
    if not record and role == "interactive":
        record = guarded(lambda: open_claude_record(process), None, skipped, "Claude", process.pid,
                         "could not read its open files")
        method = "open-main-transcript" if record else "unknown"
    title = guarded(lambda: console_title(process.pid), None, skipped, "Claude", process.pid,
                    "could not read its tab title") if read_titles else None
    # Console-less review workers do not occupy a terminal, but known IDs still prevent duplicates.
    if read_titles and not record and not title and role != "worker":
        return None
    session = {"kind": "claude", "pid": process.pid,
               "sessionId": record.get("sessionId") if record else None,
               "cwd": record.get("cwd") if record else None,
               "title": title, "method": method, "role": role,
               "uncertain": not bool(record)}
    if not record:
        # Launch evidence for keeper_restore.provably_not_selected; never used to name a chat.
        session.update({"command": process.info.get("cmdline") or [], "processCwd": process_folder(process)})
    return session


# WHAT IT DOES: Collects live Claude and Codex CLI sessions, including unresolved processes.
# DEPENDS ON: psutil, Claude PID records, open Codex rollout files, keeper_roles.ParentSnapshot.
# skipped: optional list that receives one line per process that could not be fully read.
# AFFECTS: Duplicate prevention must stop if an active CLI has an unknown identity.
# DANGER ZONE HIGH: One process must never end the whole scan / breaks: the recorder (~30 hours of lost
# captures to "SystemExtendedHandleInformation buffer too big", 2026-10) / depends on: an unreadable
# Claude becoming an UNIDENTIFIED row when titles are not read (restore checks), so a restore still
# stops on it instead of opening that chat twice. With titles (capture) it is left out, as an untitled
# unidentified Claude always was.
def live_inventory(read_titles=True, skipped=None):
    sessions = []
    snapshot = ParentSnapshot()  # read lazily, once, at the first claude.exe
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        if (process.info.get("name") or "").lower() != "claude.exe":
            continue
        try:
            session = inspect_claude(process, snapshot, read_titles, skipped)
        except psutil.NoSuchProcess:
            continue
        except Exception as error:
            note_skip(skipped, "Claude", process.pid, "could not be inspected", error)
            session = None if read_titles else {
                "kind": "claude", "pid": process.pid, "sessionId": None, "cwd": None, "title": None,
                "method": "unreadable", "role": "unknown", "uncertain": True,
                "command": process.info.get("cmdline") or [], "processCwd": None}
        if session:
            sessions.append(session)
    for session in active_codex_sessions(read_titles=read_titles, skipped=skipped):
        session["kind"] = "codex"
        session["uncertain"] = not bool(session.get("sessionId"))
        sessions.append(session)
    return sessions


# WHAT IT DOES: Links each visible tab to exactly one console with exactly one known ID.
# DEPENDS ON: UI Automation titles and live process console titles.
# AFFECTS: Duplicate titles remain unknown instead of swapping conversations. A probe's "maximized" flag is
# carried into the snapshot when present (absent from older probes, and then left out).
def match_inventory(windows, sessions):
    title_counts = {}
    by_title = {}
    for window in windows:
        for raw_title in window.get("tabs", []):
            title = strip_leading_glyphs(raw_title).casefold()
            title_counts[title] = title_counts.get(title, 0) + 1
    for session in sessions:
        if session.get("role", "interactive") == "interactive" and session.get("title"):
            by_title.setdefault(session["title"].casefold(), []).append(session)
    report = []
    used = set()
    counts = {"tabs_total": 0, "matched": 0, "ambiguous": 0, "none": 0}
    for window in windows:
        tabs = []
        for raw_title in window.get("tabs", []):
            title = strip_leading_glyphs(raw_title)
            candidates = by_title.get(title.casefold(), [])
            tab = {"rawTitle": raw_title, "strippedTitle": title, "outcome": "NONE", "kind": "unknown"}
            if len(candidates) == 1 and title_counts[title.casefold()] == 1 and candidates[0].get("sessionId"):
                session = candidates[0]
                tab.update({key: session.get(key) for key in ("sessionId", "kind", "cwd", "method", "role")})
                tab["outcome"] = "MATCHED"
                used.add(session["sessionId"])
            elif len(candidates) > 1 or title_counts[title.casefold()] > 1:
                tab["outcome"] = "AMBIGUOUS"
            counts[tab["outcome"].lower() if tab["outcome"] != "MATCHED" else "matched"] += 1
            counts["tabs_total"] += 1
            tabs.append(tab)
        report.append({**{key: window.get(key) for key in ("hwnd", "x", "y", "w", "h")},
                       **({"maximized": window["maximized"]} if "maximized" in window else {}),
                       "windowTitle": window.get("title"), "tabs": tabs})
    return report, [session for session in sessions if session.get("sessionId") not in used], counts

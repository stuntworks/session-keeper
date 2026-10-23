# Last modified: 2026-10-22 | Change: Unidentified Claude rows carry launch command and folder for the restore proof | Full history: git log
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


# WHAT IT DOES: Collects live Claude and Codex CLI sessions, including unresolved processes.
# DEPENDS ON: psutil, Claude PID records, open Codex rollout files, keeper_roles.ParentSnapshot.
# AFFECTS: Duplicate prevention must stop if an active CLI has an unknown identity.
def live_inventory(read_titles=True):
    sessions = []
    snapshot = ParentSnapshot()  # read lazily, once, at the first claude.exe
    for process in psutil.process_iter(["pid", "name", "cmdline"]):
        if (process.info.get("name") or "").lower() != "claude.exe":
            continue
        try:
            role = process_role(process, "claude", snapshot)
            if role == "service":
                continue
            record = claude_record(process)
            method = "pid-record"
            if not record and role == "interactive":
                record = open_claude_record(process)
                method = "open-main-transcript" if record else "unknown"
            title = console_title(process.pid) if read_titles else None
            # Console-less review workers do not occupy a terminal, but known IDs still prevent duplicates.
            if read_titles and not record and not title and role != "worker":
                continue
            sessions.append({"kind": "claude", "pid": process.pid,
                "sessionId": record.get("sessionId") if record else None,
                "cwd": record.get("cwd") if record else None,
                "title": title, "method": method, "role": role,
                "uncertain": not bool(record)})
            if not record:
                # Launch evidence for keeper_restore.provably_not_selected; never used to name a chat.
                sessions[-1].update({"command": process.info.get("cmdline") or [], "processCwd": process_folder(process)})
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as error:
            raise RuntimeError(f"Cannot inspect Claude process {process.pid}") from error
    for session in active_codex_sessions(read_titles=read_titles):
        session["kind"] = "codex"
        session["uncertain"] = not bool(session.get("sessionId"))
        sessions.append(session)
    return sessions


# WHAT IT DOES: Links each visible tab to exactly one console with exactly one known ID.
# DEPENDS ON: UI Automation titles and live process console titles.
# AFFECTS: Duplicate titles remain unknown instead of swapping conversations.
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
                       "windowTitle": window.get("title"), "tabs": tabs})
    return report, [session for session in sessions if session.get("sessionId") not in used], counts

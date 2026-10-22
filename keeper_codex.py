# Last modified: 2026-10-21 | Change: Read command line and folder only for codex.exe, never start times of every process | Full history: git log
"""Read-only Codex CLI inventory for Session Keeper."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import psutil

from keeper_core import strip_leading_glyphs
from keeper_roles import command_role

CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
TITLE_PROBE = Path(__file__).with_name("probe_console_title.py")
RESUME_ID = re.compile(r"\bresume\s+([0-9a-fA-F-]{36})\b")
ROLLOUT_NAME = re.compile(r"rollout-(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d)-([0-9a-fA-F-]{36})\.jsonl$")


# WHAT IT DOES: Reads one CLI process's current console title in a separate process.
# DEPENDS ON: probe_console_title.py and Windows AttachConsole.
# AFFECTS: Tab-to-process matching fails closed when the title cannot be read.
def console_title(process_id):
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    result = subprocess.run([str(executable), str(TITLE_PROBE), str(process_id)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=environment, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    title = result.stdout.strip()
    if result.returncode or title.startswith(("AttachConsole failed", "GetConsoleTitle failed")):
        return None
    return strip_leading_glyphs(title)


# WHAT IT DOES: Confirms a rollout file's own session ID matches its filename.
# DEPENDS ON: Codex's first JSONL record containing session_meta.
# AFFECTS: Rejects a misnamed or damaged candidate before assigning it to a tab.
def verified_rollout_id(path, expected_id):
    try:
        with path.open("r", encoding="utf-8") as file:
            first = json.loads(file.readline())
        payload = first.get("payload", {})
        # DANGER ZONE FRAGILE: Exclude simultaneous subagent rollouts / breaks: CLI identity / depends on: source field.
        if first.get("type") == "session_meta" and payload.get("id") == expected_id and payload.get("source") == "cli":
            return expected_id
    except (OSError, json.JSONDecodeError):
        pass
    return None


# WHAT IT DOES: Reads active Codex CLI processes and the IDs that can be proved.
# DEPENDS ON: psutil, process command lines, console titles, and rollout files.
# AFFECTS: Recovery snapshots and duplicate detection use these IDs.
def active_codex_sessions(read_titles=True):
    sessions = []
    # Only the name is read for every process. The old list also read every process's start time,
    # which nothing used and which took ~8 s on protected system processes.
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if (process.info["name"] or "").lower() != "codex.exe":
                continue
            # Same rules as process_iter: unreadable -> None, vanished -> NoSuchProcess (skipped below).
            process.info.update(process.as_dict(["cmdline", "cwd"]))
            command = process.info["cmdline"] or []
            role = command_role("codex", command)
            if role == "service":
                continue
            if role == "worker":
                sessions.append({"pid": process.pid, "title": "Codex background worker",
                                 "sessionId": None, "cwd": process.info.get("cwd"),
                                 "method": "headless-command", "role": role})
                continue
            title = console_title(process.pid) if read_titles else None
            # DANGER ZONE HIGH: A CLI can switch chats / breaks: correct resume ID / depends on: currently open main rollout.
            verified = set()
            for opened in process.open_files():
                path = Path(opened.path)
                match = ROLLOUT_NAME.fullmatch(path.name)
                if match:
                    candidate = verified_rollout_id(path, match.group(2))
                    if candidate:
                        verified.add(candidate)
            session_id = next(iter(verified)) if len(verified) == 1 else None
            method = "open-main-rollout" if session_id else "unknown"
            if read_titles and not title and not verified:
                continue
            sessions.append({"pid": process.pid, "title": title,
                             "sessionId": session_id, "cwd": process.info.get("cwd"),
                             "method": method, "role": "interactive"})
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError, ValueError) as error:
            raise RuntimeError(f"Cannot inspect Codex process {process.pid}") from error
    return sessions

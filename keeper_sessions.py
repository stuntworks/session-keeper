# Last modified: 2026-10-22 | Change: Sort by chooser (last active, started, project) | Full history: git log
"""Pure, read-only listing for the All sessions tab. No Tk.

Finding the list touches file metadata only (path, size, modified time). peek() then reads at most
PEEK_BYTES from the start and end of one file for its name and folder. Full facts (keeper_agentinfo) are
measured later, only for rows that are shown or clicked. Never writes to ~/.claude or ~/.codex."""

import json
import re
from datetime import datetime
from pathlib import Path

from keeper_agentinfo import (CLAUDE_PROJECTS, CODEX_INDEX, SESSION_ID, TIMESTAMP, parse_time, plural, size_words,
                              span_words, when)
from keeper_codex import CODEX_SESSIONS

PEEK_BYTES = 16384
CWD_FIELD = re.compile(rb'"cwd":("(?:[^"\\]|\\.)*")')
TITLE_PREFIXES = ((b'{"type":"custom-title"', "customTitle"), (b'{"type":"agent-name"', "agentName"),
                  (b'{"type":"ai-title"', "aiTitle"))
READING = "Reading…"
SORTS = (("active", "Last active"), ("started", "Started"), ("project", "Project (folder)"))


# WHAT IT DOES: Every Claude Code (projects/*/<id>.jsonl) and Codex (sessions/**/rollout-*-<id>.jsonl)
# transcript, newest first. Same places keeper_agentinfo.find_transcript looks.
# DEPENDS ON: Directory listing and stat only; no file is opened.
# AFFECTS: Returns [{"kind", "session_id", "path", "size", "modified", "created", "folder", "title", "cwd"}]
# (plus "started" / "last_active" once peek_more has read them).
def list_transcripts(claude_root=None, codex_root=None):
    found = []
    claude = Path(claude_root or CLAUDE_PROJECTS)
    if claude.is_dir():
        for path in claude.glob("*/*.jsonl"):
            if SESSION_ID.fullmatch(path.stem):
                add(found, "claude", path.stem, path, path.parent.name)
    codex = Path(codex_root or CODEX_SESSIONS)
    if codex.is_dir():
        for path in codex.glob("**/rollout-*.jsonl"):
            ids = SESSION_ID.findall(path.name)
            if ids:
                add(found, "codex", ids[-1], path, "")
    return sort_entries(found, "active")


def add(found, kind, session_id, path, folder):
    try:
        stat = path.stat()
    except OSError:
        return
    found.append({"kind": kind, "session_id": session_id, "path": str(path), "size": stat.st_size,
                  "modified": datetime.fromtimestamp(stat.st_mtime),
                  "created": datetime.fromtimestamp(stat.st_ctime), "folder": folder, "title": None, "cwd": None})


# WHAT IT DOES: Codex's saved thread names, newest per session id.
# DEPENDS ON: ~/.codex/session_index.jsonl (small); a missing or damaged file gives {}.
# AFFECTS: Read-only.
def codex_titles(index_path=None):
    names = {}
    try:
        with open(index_path or CODEX_INDEX, "rb") as handle:
            for raw in handle:
                try:
                    item = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(item, dict) and item.get("id") and item.get("thread_name"):
                    names[str(item["id"])] = str(item["thread_name"])
    except OSError:
        return {}
    return names


# WHAT IT DOES: Cheap name and folder for one listed transcript: reads only its first and last PEEK_BYTES.
# DEPENDS ON: Claude title lines (custom-title > agent-name > ai-title, the later one wins) and any "cwd" field;
# Codex names come from codex_titles, its folder from the session_meta line at the top.
# AFFECTS: Read-only. Returns (title or None, cwd or None).
def peek(entry, titles):
    return peek_more(entry, titles)[:2]


# WHAT IT DOES: peek plus the transcript's own first and last timestamps, from the same two reads.
# DEPENDS ON: Whole JSON lines only (same rule as keeper_agentinfo.stream), so it agrees with the "Last active"
# the full facts show. A last line longer than PEEK_BYTES gives None and the caller falls back to file times.
# AFFECTS: Read-only. Returns (title or None, cwd or None, started or None, last_active or None).
def peek_more(entry, titles):
    try:
        with open(entry["path"], "rb") as handle:
            head = handle.read(PEEK_BYTES)
            size = entry.get("size") or 0
            tail = b""
            if size > PEEK_BYTES:
                handle.seek(max(size - PEEK_BYTES, PEEK_BYTES))
                tail = handle.read(PEEK_BYTES)
    except OSError:
        return None, None, None, None
    cwd = None
    found = CWD_FIELD.search(head)
    if found:
        try:
            cwd = json.loads(found.group(1))
        except ValueError:
            cwd = None
    head_lines, tail_lines = head.split(b"\n"), tail.split(b"\n")
    started = edge_time(head_lines[:-1] if tail else head_lines, False)
    last = edge_time(tail_lines[1:] if tail else head_lines, True)
    if entry["kind"] == "codex":
        return titles.get(entry["session_id"]), cwd, started, last
    return claude_title(head, tail), cwd, started, last


# WHAT IT DOES: The first (or, newest=True, last) timestamp among whole JSON object lines.
def edge_time(lines, newest):
    for raw in (reversed(lines) if newest else lines):
        raw = raw.rstrip(b"\r")
        if raw.startswith(b"{") and raw.endswith(b"}"):
            found = TIMESTAMP.search(raw)
            moment = parse_time(found.group(1)) if found else None
            if moment:
                return moment.replace(tzinfo=None)
    return None


def claude_title(head, tail):
    best = {}
    for chunk, whole_first in ((head, True), (tail, False)):
        lines = chunk.split(b"\n")
        lines = lines[:-1] if whole_first else lines[1:]      # drop the line cut at the chunk edge
        for raw in lines:
            for prefix, field in TITLE_PREFIXES:
                if raw.startswith(prefix):
                    try:
                        value = json.loads(raw).get(field)
                    except (ValueError, AttributeError):
                        value = None
                    if value:
                        best[field] = str(value)
    return best.get("customTitle") or best.get("agentName") or best.get("aiTitle")


# WHAT IT DOES: The three moments a row can be ordered by. The real transcript time when known, else file times.
def active_time(entry):
    return entry.get("last_active") or entry["modified"]


def started_time(entry):
    return entry.get("started") or entry.get("created") or entry["modified"]


def folder_key(entry):
    return str(entry.get("cwd") or entry.get("folder") or "").casefold()


# WHAT IT DOES: Orders rows: "active" newest last-active first; "started" newest first-timestamp first;
# "project" folders A-Z (no folder last), newest-active first inside each folder.
# AFFECTS: Returns a new list.
def sort_entries(entries, mode):
    if mode == "started":
        return sorted(entries, key=started_time, reverse=True)
    newest = sorted(entries, key=active_time, reverse=True)
    if mode == "project":
        return sorted(newest, key=lambda entry: (not folder_key(entry), folder_key(entry)))
    return newest


# WHAT IT DOES: The search box and the All / Claude / Codex filter. Every typed word must appear in the
# name, folder, session id or a date form ("2026-10-21", "Oct 21", "October 21", "Tuesday").
# AFFECTS: Returns a new list in the same order.
def filter_entries(entries, text, kind):
    words = text.casefold().split()
    shown = []
    for entry in entries:
        if kind in ("claude", "codex") and entry["kind"] != kind:
            continue
        moment = active_time(entry)
        haystack = " ".join(str(part) for part in (
            entry.get("title") or "", entry.get("cwd") or "", entry.get("folder") or "", entry["session_id"],
            f"{moment:%Y-%m-%d %H:%M}", f"{moment:%b} {moment.day}", f"{moment:%B} {moment.day}",
            f"{moment:%A}")).casefold()
        if all(word in haystack for word in words):
            shown.append(entry)
    return shown


# WHAT IT DOES: The plain-word work summary: "Changed files 142 times (37 files) · ran 380 commands", or
# "talk only" for a session that never changed a file or ran a command.
def work_words(facts):
    changes, files, commands = facts.get("file_changes", 0), facts.get("files_changed", 0), facts.get("commands", 0)
    parts = [f"Changed files {plural(changes, 'time')} ({plural(files, 'file')})"] if changes else []
    parts += [f"ran {plural(commands, 'command')}"] if commands else []
    if not parts:
        return "talk only"
    text = " · ".join(parts)
    return text[0].upper() + text[1:]


# WHAT IT DOES: The words for one row. Before its facts arrive it shows what the listing knows.
# DEPENDS ON: A listing entry and its agent_facts (None while still reading).
# AFFECTS: Returns a dict of strings only.
def row_text(entry, facts):
    title = (facts or {}).get("title") or entry.get("title") or f"Untitled session {entry['session_id'][:8]}"
    text = {"title": title, "folder": entry.get("cwd") or entry.get("folder") or "Folder unknown",
            "last_active": when(active_time(entry)), "started": "", "active_for": "",
            "size": size_words(entry.get("size") or 0), "work": READING}
    if facts is None:
        return text
    if not facts.get("available"):
        text["work"] = facts.get("reason") or "Not available"
        return text
    if facts.get("last_active"):
        text["last_active"] = when(facts["last_active"])
    text["started"] = when(facts.get("started"))
    text["active_for"] = span_words(facts.get("active_span"))
    text["size"] = f"{plural(facts.get('messages', 0), 'message')} · {size_words(facts.get('transcript_bytes', 0))}"
    text["work"] = work_words(facts)
    return text

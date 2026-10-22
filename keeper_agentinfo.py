# Last modified: 2026-10-21 | Change: New read-only transcript facts (last message, times, size, file-change and command counts) | Full history: git log
"""Pure, read-only facts about one saved Claude Code / Codex session, measured from its transcript.

Nothing here guesses or labels. Every number is a count of entries actually present in the transcript
file, so the owner can tell an engineer tab (many file changes) from an advisor tab (talk only) himself.
No Tk. Never writes to ~/.claude or ~/.codex.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from keeper_codex import CODEX_SESSIONS

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_INDEX = Path.home() / ".codex" / "session_index.jsonl"
SESSION_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
TIMESTAMP = re.compile(rb'"timestamp":"([^"]+)"')
CODEX_PAYLOAD = re.compile(rb'"type":"response_item","payload":\{"type":"([a-z_]+)"')
FRACTION = re.compile(r"(\.\d{6})\d+")
PATCH_HEADER = re.compile(r"^\*\*\* (?:Update File|Add File|Delete File|Move to): (.+)$")
JS_ESCAPE = re.compile(r"\\(.)", re.S)
LAST_TEXT_LIMIT = 400

CLAUDE_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
CLAUDE_SHELL_TOOLS = {"Bash", "PowerShell"}
CLAUDE_TITLE_KEYS = {b'{"type":"custom-title"': "customTitle", b'{"type":"agent-name"': "agentName",
                     b'{"type":"ai-title"': "aiTitle"}
CODEX_SHELL_TOOLS = {"shell", "shell_command", "exec_command", "local_shell", "container.exec"}
CODEX_CODE_MODE_COMMANDS = ("tools.exec_command(", "tools.shell_command(", "tools.shell(")
NOISE_PREFIXES = ("<", "[Request interrupted", "# AGENTS.md")

_cache = {}


# WHAT IT DOES: Forgets every cached result (tests use it; the app never needs to).
# DEPENDS ON: nothing.
# AFFECTS: The next agent_facts call re-reads its transcript.
def clear_cache():
    _cache.clear()


# WHAT IT DOES: Finds the transcript file for one recovery row, or None.
# DEPENDS ON: Claude's projects/<encoded cwd>/<id>.jsonl layout and Codex's sessions/YYYY/MM/DD/rollout-*-<id>.jsonl.
# AFFECTS: Read-only directory listing. Falls back to a glob when the folder encoding does not match.
def find_transcript(kind, session_id, cwd, claude_root=None, codex_root=None):
    if kind == "claude":
        root = Path(claude_root or CLAUDE_PROJECTS)
        if cwd:
            direct = root / re.sub(r"[^A-Za-z0-9]", "-", str(cwd)) / f"{session_id}.jsonl"
            if direct.is_file():
                return direct
        return next((path for path in root.glob(f"*/{session_id}.jsonl") if path.is_file()), None)
    root = Path(codex_root or CODEX_SESSIONS)
    for pattern in (f"*/*/*/rollout-*-{session_id}.jsonl", f"**/rollout-*-{session_id}.jsonl"):
        found = next((path for path in root.glob(pattern) if path.is_file()), None)
        if found:
            return found
    return None


# WHAT IT DOES: Measures one saved session from its transcript. Returns {"available": False, "reason"} or
# {"available": True, path, title, last_text, last_text_time, started, last_active, active_span (seconds),
#  user_prompts, assistant_replies, messages, transcript_bytes, file_changes, files_changed, commands,
#  skipped_lines}. Times are local-timezone datetimes.
# DEPENDS ON: row["session_id"], row["kind"] ("claude"/"codex"), row["cwd"]; the transcript on disk.
# AFFECTS: Read-only. Cached by (path, size, mtime) so an unchanged file is never read twice.
def agent_facts(row, claude_root=None, codex_root=None, codex_index=None):
    kind, session_id = row.get("kind"), str(row.get("session_id") or "")
    if kind not in ("claude", "codex"):
        return {"available": False, "reason": "Not a Claude or Codex session"}
    if not SESSION_ID.fullmatch(session_id):
        return {"available": False, "reason": "No exact session ID saved for this tab"}
    path = find_transcript(kind, session_id, row.get("cwd"), claude_root, codex_root)
    if path is None:
        return {"available": False, "reason": "Transcript not found on this computer"}
    try:
        stat = path.stat()
    except OSError as error:
        return {"available": False, "reason": f"Transcript can't be opened ({error.strerror or error})"}
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key in _cache:
        return _cache[key]
    try:
        scan = scan_claude(path) if kind == "claude" else scan_codex(path)
    except OSError as error:
        return {"available": False, "reason": f"Transcript can't be read ({error.strerror or error})"}
    if kind == "codex":
        scan["title"] = codex_thread_name(session_id, codex_index)
    facts = finish(scan, path, stat.st_size)
    _cache[key] = facts
    return facts


# WHAT IT DOES: Turns raw scan counters into the public facts dict.
# DEPENDS ON: the counters produced by scan_claude / scan_codex.
# AFFECTS: Returns a new dict only.
def finish(scan, path, size):
    started, last_active = scan["started"], scan["last_active"]
    span = (last_active - started).total_seconds() if started and last_active else None
    return {"available": True, "path": str(path), "title": scan.get("title"),
            "last_text": trim_text(final_paragraph(scan["last_text"])) if scan["last_text"] else None,
            "last_text_time": scan["last_text_time"], "started": started, "last_active": last_active,
            "active_span": span, "user_prompts": scan["user_prompts"],
            "assistant_replies": len(scan["replies"]),
            "messages": scan["user_prompts"] + len(scan["replies"]), "transcript_bytes": size,
            "file_changes": scan["file_changes"], "files_changed": len(scan["files"]),
            "commands": scan["commands"], "skipped_lines": scan["skipped"]}


def new_scan():
    return {"started": None, "last_active": None, "last_text": None, "last_text_time": None,
            "user_prompts": 0, "replies": set(), "file_changes": 0, "files": set(), "commands": 0,
            "skipped": 0, "title": None}


# WHAT IT DOES: Converts an ISO timestamp from a transcript to local time, or None.
# DEPENDS ON: "...Z" or offset timestamps, any number of fraction digits.
# AFFECTS: Nothing.
def parse_time(text):
    if isinstance(text, bytes):
        text = text.decode("ascii", "replace")
    try:
        return datetime.fromisoformat(FRACTION.sub(r"\1", text).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return None


# WHAT IT DOES: Streams the file line by line, keeping only first/last timestamps and lines worth parsing.
# DEPENDS ON: JSONL, one object per line. Nested strings are JSON-escaped, so a raw byte match on
# '"type":"x"' only ever hits real structure, never quoted message text.
# AFFECTS: Yields (raw line, parsed or None). Damaged lines are counted in scan["skipped"] and dropped.
def stream(path, scan, wanted):
    with open(path, "rb", buffering=1 << 20) as handle:
        for raw in handle:
            if len(raw) < 3 and not raw.strip():
                continue
            if not raw.startswith(b"{") or not raw.rstrip(b"\r\n").endswith(b"}"):
                scan["skipped"] += 1
                continue
            found = TIMESTAMP.search(raw)
            if found:
                moment = parse_time(found.group(1))
                if moment:
                    scan["started"] = scan["started"] or moment
                    scan["last_active"] = moment
            if not wanted(raw):
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                scan["skipped"] += 1
                continue
            if isinstance(entry, dict):
                yield raw, entry


def claude_wanted(raw):
    if b'"type":"assistant"' in raw:
        return b'"type":"tool_use"' in raw or b'"type":"text"' in raw
    if b'"type":"user"' in raw:
        return b'"tool_use_id"' not in raw
    return raw.startswith((b'{"type":"custom-title"', b'{"type":"agent-name"', b'{"type":"ai-title"'))


# WHAT IT DOES: Measures a Claude Code transcript (~/.claude/projects/*/<id>.jsonl).
# DEPENDS ON: entry types user / assistant / custom-title / agent-name / ai-title; one content block per line.
# AFFECTS: Read-only.
def scan_claude(path):
    scan, titles, tool_ids = new_scan(), {}, set()
    for raw, entry in stream(path, scan, claude_wanted):
        kind = entry.get("type")
        if kind == "assistant":
            claude_assistant(entry, scan, tool_ids)
        elif kind == "user":
            if is_claude_prompt(entry):
                scan["user_prompts"] += 1
        else:
            for prefix, field in CLAUDE_TITLE_KEYS.items():
                if raw.startswith(prefix) and entry.get(field):
                    titles[field] = str(entry[field])
    scan["title"] = titles.get("customTitle") or titles.get("agentName") or titles.get("aiTitle")
    return scan


def claude_assistant(entry, scan, tool_ids):
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    blocks = message.get("content") if isinstance(message.get("content"), list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text") or "").strip()
            # DANGER ZONE FRAGILE: API error / synthetic lines are not replies / breaks: last message / depends on: flags.
            if text and not entry.get("isApiErrorMessage") and message.get("model") != "<synthetic>":
                scan["replies"].add(message.get("id") or entry.get("uuid"))
                scan["last_text"], scan["last_text_time"] = text, parse_time(entry.get("timestamp"))
        elif block.get("type") == "tool_use":
            tool_id = block.get("id")
            if tool_id in tool_ids:
                continue
            tool_ids.add(tool_id)
            name = block.get("name")
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            if name in CLAUDE_EDIT_TOOLS:
                scan["file_changes"] += 1
                target = tool_input.get("file_path") or tool_input.get("notebook_path")
                if target:
                    scan["files"].add(os.path.normcase(str(target).strip()))
            elif name in CLAUDE_SHELL_TOOLS:
                scan["commands"] += 1


def is_claude_prompt(entry):
    if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain") or "toolUseResult" in entry:
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
            return False
        content = " ".join(str(block.get("text") or "") for block in content
                           if isinstance(block, dict) and block.get("type") == "text")
    return is_human_text(content)


def is_human_text(text):
    text = str(text or "").strip()
    return bool(text) and not text.startswith(NOISE_PREFIXES)


def codex_wanted(raw):
    found = CODEX_PAYLOAD.search(raw, 0, 400)
    return bool(found) and found.group(1) in (b"message", b"custom_tool_call", b"function_call", b"local_shell_call")


# WHAT IT DOES: Measures a Codex CLI rollout (~/.codex/sessions/**/rollout-*-<id>.jsonl).
# DEPENDS ON: response_item payloads: message (role user/assistant), custom_tool_call (apply_patch, exec),
# function_call (shell_command), local_shell_call.
# AFFECTS: Read-only.
def scan_codex(path):
    scan = new_scan()
    for _raw, entry in stream(path, scan, codex_wanted):
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        kind = payload.get("type")
        if kind == "message":
            codex_message(entry, payload, scan)
        else:
            codex_tool(payload, scan)
    return scan


def codex_message(entry, payload, scan):
    blocks = payload.get("content") if isinstance(payload.get("content"), list) else []
    text = "\n".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict)).strip()
    if payload.get("role") == "user" and is_human_text(text):
        scan["user_prompts"] += 1
    elif payload.get("role") == "assistant" and text:
        scan["replies"].add(payload.get("id") or entry.get("timestamp"))
        scan["last_text"], scan["last_text_time"] = text, parse_time(entry.get("timestamp"))


def codex_tool(payload, scan):
    name = str(payload.get("name") or "")
    body = payload.get("input") if payload.get("type") == "custom_tool_call" else payload.get("arguments")
    body = body if isinstance(body, str) else json.dumps(body or payload.get("action") or "")
    patches = body.count("*** Begin Patch")
    # DANGER ZONE FRAGILE: Code-mode "exec" scripts call tools inside JavaScript / breaks: work counts /
    # depends on: tools.apply_patch / tools.exec_command spellings in the script text.
    scan["file_changes"] += max(patches, 1 if name == "apply_patch" else 0)
    scan["files"].update(patch_files(body))
    if payload.get("type") == "local_shell_call" or name in CODEX_SHELL_TOOLS:
        scan["commands"] += 1
    elif name == "exec":
        scan["commands"] += sum(body.count(call) for call in CODEX_CODE_MODE_COMMANDS)


# WHAT IT DOES: Lists the file paths named in every apply_patch block inside a tool call body.
# DEPENDS ON: "*** Begin Patch" / "*** Update|Add|Delete File: <path>" headers, raw or JS-string-escaped.
# AFFECTS: Nothing.
def patch_files(body):
    files, start = set(), body.find("*** Begin Patch")
    while start != -1:
        end = body.find("*** End Patch", start)
        segment = body[start:end if end != -1 else len(body)]
        if segment[len("*** Begin Patch"):].startswith("\\n"):
            segment = JS_ESCAPE.sub(lambda m: {"n": "\n", "t": "\t", "r": "\r"}.get(m.group(1), m.group(1)), segment)
        for line in segment.splitlines():
            header = PATCH_HEADER.match(line.strip())
            if header:
                files.add(os.path.normcase(header.group(1).strip()))
        start = body.find("*** Begin Patch", start + 1)
    return files


# WHAT IT DOES: Returns the newest thread name Codex saved for this session, or None.
# DEPENDS ON: ~/.codex/session_index.jsonl lines {"id", "thread_name", "updated_at"} (small file).
# AFFECTS: Read-only.
def codex_thread_name(session_id, index_path=None):
    name = None
    try:
        with open(index_path or CODEX_INDEX, "rb") as handle:
            for raw in handle:
                if session_id.encode() not in raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if entry.get("id") == session_id and entry.get("thread_name"):
                    name = str(entry["thread_name"])
    except OSError:
        return None
    return name


def final_paragraph(text):
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return " ".join(paragraphs[-1].split()) if paragraphs else ""


def trim_text(text, limit=LAST_TEXT_LIMIT):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,;:-") + "…"


def plural(count, word):
    return f"{count:,} {word}" + ("" if count == 1 else "s")


def when(moment):
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "Unknown"


# WHAT IT DOES: Writes a time span as plain words: "under 1 min", "50 min", "2 h 5 min", "3 days 4 h".
# DEPENDS ON: seconds (float or None).
# AFFECTS: Nothing.
def span_words(seconds):
    if seconds is None:
        return "Unknown"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "under 1 min"
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min"
    days, hours = divmod(hours, 24)
    return f"{plural(days, 'day')} {hours} h"


def size_words(size):
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} KB"
    megabytes = size / (1024 * 1024)
    return f"{megabytes:.1f} MB" if megabytes < 10 else f"{round(megabytes):,} MB"


# WHAT IT DOES: Turns agent_facts output into (label, value) rows for the details drawer. Plain counts, no labels
# like "engineer" or "advisor".
# DEPENDS ON: the dict from agent_facts.
# AFFECTS: Returns a list only.
def format_facts(facts):
    if not facts.get("available"):
        return [("TRANSCRIPT", facts.get("reason") or "Not available")]
    rows = [("NAME", facts["title"])] if facts.get("title") else []
    rows.append(("LAST MESSAGE", facts.get("last_text") or "No reply text in the transcript"))
    if facts.get("last_text_time"):
        rows.append(("WHEN", when(facts["last_text_time"])))
    rows += [("STARTED", when(facts.get("started"))), ("LAST ACTIVE", when(facts.get("last_active"))),
             ("ACTIVE FOR", span_words(facts.get("active_span"))),
             ("SIZE", f"{plural(facts.get('messages', 0), 'message')} · {size_words(facts.get('transcript_bytes', 0))}")]
    changes, files, commands = facts.get("file_changes", 0), facts.get("files_changed", 0), facts.get("commands", 0)
    edited = f"Changed files {plural(changes, 'time')} ({plural(files, 'file')})" if changes else "Changed no files"
    if commands:
        rows.append(("WORK", f"{edited} · ran {plural(commands, 'command')}"))
    else:
        rows.append(("WORK", f"{edited} · talk only" if not changes else edited))
    if facts.get("skipped_lines"):
        rows.append(("SKIPPED", f"{plural(facts['skipped_lines'], 'damaged line')} not read"))
    return rows

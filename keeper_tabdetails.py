# Last modified: 2026-10-22 | Change: Always show the command for an exact id; Open it for me / Open a second copy | Full history: git log
"""Pure text/state: describes one tab of the Saved recovery map. No Tk, never launches anything.
Owner rule 2026-10-22: never hide the information; the user decides how to use it."""

from keeper_copycmd import build_resume_text
from keeper_core import SESSION_ID
from keeper_restore import resume_program

OPEN_NOTE = ("This chat is already open in another window. Two copies of the same chat can overwrite each other's "
             "history. Open a second copy only if you mean to.")
FOLDER_NOTE = ("Its saved project folder is gone or was never recorded, so this is the bare command. "
               "To use it: open a terminal in the right folder and paste it.")
UNKNOWN_NOTE = ("No exact session found for this tab, so there is no command. To find it: use the All "
                "sessions view, or run `claude -r` in its folder and pick it from the list.")


# WHAT IT DOES: Describes one tab: name, Claude/Codex, folder, window number, status and the exact command.
# DEPENDS ON: snapshot, recovery rows by key, live session IDs, keeper_copycmd.build_resume_text.
# AFFECTS: Returns a dict only. Any tab with an exact id gets a command (bare when the folder is unusable);
# can_open only when the full command works; second_copy only for a tab that is already running.
def tab_details(snapshot, key, rows, live):
    window_index, tab_index = (int(part) for part in key.split(":"))
    tab = snapshot["windows"][window_index]["tabs"][tab_index]
    row = rows.get(key)
    info = {"key": key, "window": window_index + 1,
            "name": (row or {}).get("title") or tab.get("strippedTitle") or tab.get("rawTitle") or "Unnamed tab",
            "program": "", "folder": tab.get("cwd") or "", "status": "Can't be identified",
            "command": "", "can_copy": False, "can_open": False, "second_copy": False, "note": UNKNOWN_NOTE}
    exact = (row or tab).get("session_id") or tab.get("sessionId") or ""
    kind = (row or {}).get("kind") or tab.get("kind") or "claude"
    if (not row and (tab.get("outcome") != "MATCHED" or tab.get("role") in ("worker", "service", "unknown"))) \
            or not SESSION_ID.fullmatch(exact) or kind not in ("claude", "codex"):
        return info
    row = row or {"key": key, "session_id": exact, "kind": kind, "title": info["name"], "cwd": tab.get("cwd")}
    info["program"] = "Codex" if kind == "codex" else "Claude"
    info["folder"] = row.get("cwd") or ""
    is_live = exact in live
    info["status"] = "Already open" if is_live else "Missing"
    info["can_copy"] = True
    try:
        info["command"] = build_resume_text([row])
        full = True
    except (ValueError, RuntimeError):
        info["command"] = " ".join(resume_program(row))
        full = False
    notes = [OPEN_NOTE] if is_live else []
    if not full:
        notes.append(FOLDER_NOTE)
    info["note"] = " ".join(notes)
    info["can_open"] = full and not is_live and key in rows
    info["second_copy"] = full and is_live and key in rows
    info["row"] = rows.get(key)
    return info

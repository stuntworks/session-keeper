# Last modified: 2026-10-21 | Change: Pure builder for the tab details panel (status, folder, exact command) | Full history: git log
"""Pure text/state: describes one tab of the Saved recovery map. No Tk, never launches anything."""

from keeper_copycmd import build_resume_text

OPEN_NOTE = "This one is already running; opening it again would make a duplicate."
FOLDER_NOTE = "The project folder for this tab was not recorded, so it is not restored and there is no command to copy."
UNKNOWN_NOTE = "No exact session found, so there is no command to copy."


# WHAT IT DOES: Describes one tab: name, Claude/Codex, folder, window number, status and the exact command.
# DEPENDS ON: snapshot, recovery rows by key (identified tabs only), live session IDs, keeper_copycmd.build_resume_text.
# AFFECTS: Returns a dict only. The command comes from the same builder as the Copy command button;
# unidentified tabs (not in rows) and unquotable folders never get a command.
def tab_details(snapshot, key, rows, live):
    window_index, tab_index = (int(part) for part in key.split(":"))
    tab = snapshot["windows"][window_index]["tabs"][tab_index]
    row = rows.get(key)
    info = {"key": key, "window": window_index + 1,
            "name": (row or {}).get("title") or tab.get("strippedTitle") or tab.get("rawTitle") or "Unnamed tab",
            "program": "", "folder": tab.get("cwd") or "", "status": "Can't be identified",
            "command": "", "can_copy": False, "note": UNKNOWN_NOTE}
    if not row:
        if tab.get("outcome") == "MATCHED" and not (tab.get("cwd") or "").strip():
            info["note"] = FOLDER_NOTE
        return info
    info["program"] = "Codex" if row["kind"] == "codex" else "Claude"
    info["folder"] = row["cwd"]
    is_live = row["session_id"] in live
    info["status"] = "Already open" if is_live else "Missing"
    info["note"] = OPEN_NOTE if is_live else ""
    try:
        info["command"] = build_resume_text([row])
        info["can_copy"] = True
    except (ValueError, RuntimeError) as error:
        info["note"] = (info["note"] + " " if info["note"] else "") + f"Can't build a command: {error}"
    return info

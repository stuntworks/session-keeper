# Last modified: 2026-10-20 | Change: Build paste-ready resume commands for the Copy command button | Full history: git log
"""Pure text builder: turns selected recovery rows into commands the user can paste into a terminal."""

from keeper_core import SESSION_ID
from keeper_restore import resume_program


# WHAT IT DOES: Builds one paste-ready block per row: cd to the saved folder, then the exact resume command.
# DEPENDS ON: recovery_rows-style dicts (session_id, kind, title, cwd) and keeper_restore.resume_program.
# AFFECTS: Returns text only. Refuses any row without an exact session ID, or a folder that cannot be quoted safely.
def build_resume_text(rows):
    if not rows:
        raise ValueError("No tabs selected")
    blocks = []
    for row in rows:
        if not SESSION_ID.fullmatch(row.get("session_id") or ""):
            raise ValueError("Tab has no exact session ID")
        cwd = row["cwd"]
        if any(character in cwd for character in ('"', "\n", "\r")):
            raise ValueError("Saved project folder cannot be quoted safely")
        lines = [f'cd "{cwd}"', " ".join(resume_program(row))]
        if len(rows) > 1:
            title = " ".join((row.get("title") or "Recovered session").split())
            lines.insert(0, f"# {title}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

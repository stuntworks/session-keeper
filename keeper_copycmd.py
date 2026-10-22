# Last modified: 2026-10-22 | Change: One PowerShell-safe line per tab, refuses missing folders | Full history: git log
"""Pure text builder: turns selected recovery rows into commands the user can paste into a terminal."""

from pathlib import Path

from keeper_core import SESSION_ID
from keeper_restore import resume_program


# WHAT IT DOES: Builds one paste-ready block per row: one PowerShell line that moves to the saved folder and resumes only if that worked.
# DEPENDS ON: recovery_rows-style dicts (session_id, kind, title, cwd) and keeper_restore.resume_program.
# AFFECTS: Returns text only. Refuses any row without an exact session ID, a folder with a line break, or a folder missing on this PC.
def build_resume_text(rows):
    if not rows:
        raise ValueError("No tabs selected")
    blocks = []
    for row in rows:
        if not SESSION_ID.fullmatch(row.get("session_id") or ""):
            raise ValueError("Tab has no exact session ID")
        cwd = row["cwd"]
        if "\n" in cwd or "\r" in cwd:
            raise ValueError("Saved project folder cannot be quoted safely")
        if not Path(cwd).is_dir():
            raise ValueError(f"Saved project folder no longer exists: {cwd}")
        literal = "'" + cwd.replace("'", "''") + "'"
        lines = [f"if (Set-Location -LiteralPath {literal} -PassThru) {{ {' '.join(resume_program(row))} }}"]
        if len(rows) > 1:
            title = " ".join((row.get("title") or "Recovered session").split())
            lines.insert(0, f"# {title}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

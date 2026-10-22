# Last modified: 2026-10-07 | Change: Durable record of windows Session Keeper created | Full history: git log
"""Append-only record of terminal windows Session Keeper itself opened during recovery."""

import json
from pathlib import Path

from keeper_core import DATA

CREATED_LOG = DATA / "created_windows.jsonl"


# WHAT IT DOES: Names the saved window group a snapshot row belongs to.
# DEPENDS ON: The snapshot capture time and the saved window index.
# AFFECTS: A created window is only reused for the same saved group.
def group_key(snapshot: dict, window_index: int) -> str:
    return f"{snapshot.get('captured_at', 'unknown')}#{window_index}"


# WHAT IT DOES: Appends one line recording a window Session Keeper just created.
# DEPENDS ON: The data folder being writable. Existing lines are never rewritten or deleted.
# AFFECTS: A later recovery can finish a partly restored window instead of skipping it.
def record_created_window(window_name, hwnd, group, boot_id, path: Path = None):
    entry = {"window_name": window_name, "hwnd": hwnd, "group": group, "boot_id": boot_id}
    target = path or CREATED_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


# WHAT IT DOES: Reads the windows created during this Windows boot. Unreadable lines are ignored.
# DEPENDS ON: The record file; a missing file means none were created.
# AFFECTS: A handle from an earlier boot is never trusted, since handles get reused.
def load_created_windows(boot_id, path: Path = None) -> list[dict]:
    log = path or CREATED_LOG
    if not log.exists():
        return []
    found = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("boot_id") == boot_id and entry.get("window_name"):
            found.append(entry)
    return found

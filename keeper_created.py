# Last modified: 2026-10-27 | Change: Each record also logs the target spot and any converted-from spot | Full history: git log
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
# rect is the spot it is placed at (physical pixels); converted_from is the saved spot of an older snapshot.
# DEPENDS ON: The data folder being writable. Existing lines are never rewritten or deleted.
# AFFECTS: A later recovery can finish a partly restored window instead of skipping it.
def record_created_window(window_name, hwnd, group, boot_id, path: Path = None, rect=None, converted_from=None):
    entry = {"window_name": window_name, "hwnd": hwnd, "group": group, "boot_id": boot_id}
    if rect is not None:
        entry["rect"] = rect
    if converted_from is not None:
        entry["converted_from"] = converted_from
    with open(path or CREATED_LOG, "a", encoding="utf-8") as handle:
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

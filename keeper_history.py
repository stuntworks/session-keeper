# Last modified: 2026-10-21 | Change: Split one-file reading into load_snapshot for the fast start | Full history: git log
"""Read saved layouts without replacing or deleting recovery evidence."""

import json
from datetime import datetime
from pathlib import Path
from keeper_core import DATA


# WHAT IT DOES: Reads valid saved layouts newest first, ignoring interrupted writes.
# DEPENDS ON: Snapshot files with a version, time, and windows list.
# AFFECTS: Recovery selection includes older layouts after a later partial snapshot.
def read_history(data_dir=DATA):
    history = []
    for path in Path(data_dir).glob("snapshot_*.json"):
        snapshot = load_snapshot(path)
        if snapshot is not None:
            history.append((path, snapshot))
    return sorted(history, key=lambda item: item[1]["captured_at"], reverse=True)


# WHAT IT DOES: Reads one saved layout, or returns None for an interrupted or unknown-format file.
# DEPENDS ON: Snapshot files with a version, time, and windows list.
# AFFECTS: Nothing; the file is only read. Shared by read_history and keeper_fast_history.
def load_snapshot(path):
    try:
        snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
            return None
        if not isinstance(snapshot.get("windows"), list):
            return None
        datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))
        return snapshot
    except (OSError, ValueError, KeyError, TypeError):
        return None


# WHAT IT DOES: Returns a startup recovery only if the latest layout predates this boot.
# DEPENDS ON: Saved layout history and current Windows boot ID.
# AFFECTS: Restarting the watcher on the same boot does not resurrect an older boot's layout.
def startup_recovery(current_boot, data_dir=DATA):
    history = read_history(data_dir)
    if not history:
        return None
    path, snapshot = history[0]
    if snapshot.get("boot_id") != current_boot and not snapshot.get("legacy"):
        return path, snapshot
    return None

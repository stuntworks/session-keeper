# Last modified: 2026-11-10 | Change: Login offer prefers the previous boot's layout over a much smaller one captured while the login window was open | Full history: git log
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
# A newest layout saved while the login recovery window was open (e.g. 2 chats opened by hand before
# restoring) with fewer than half the matched chats of the boot before it gives way to that boot's last one.
def startup_recovery(current_boot, data_dir=DATA):
    from keeper_fast_history import newest_snapshot  # login must not parse every snapshot file
    newest = newest_snapshot(data_dir)
    if newest is None:
        return None
    path, snapshot = newest
    if snapshot.get("boot_id") != current_boot and not snapshot.get("legacy"):
        return fuller_previous_boot(newest, current_boot, data_dir) or (path, snapshot)
    return None


# WHAT IT DOES: The previous boot's last layout when the newest was captured with the login window open
# and is much smaller; otherwise None.
# DEPENDS ON: keeper_fast_history.fast_history (only read in this rare case), the "login_window_open" mark
# set by keeper_heartbeat, counts.matched.
# AFFECTS: Nothing; returns (path, snapshot) or None.
def fuller_previous_boot(newest, current_boot, data_dir):
    snapshot = newest[1]
    if not snapshot.get("login_window_open"):
        return None
    from keeper_fast_history import fast_history
    matched = lambda item: (item[1].get("counts") or {}).get("matched", 0)
    earlier = [item for item in fast_history(data_dir)
               if item[1].get("boot_id") not in (snapshot.get("boot_id"), current_boot)
               and not item[1].get("legacy")]
    if not earlier:
        return None
    previous = max(earlier, key=lambda item: item[1].get("captured_at", ""))
    return previous if matched(newest) * 2 < matched(previous) else None

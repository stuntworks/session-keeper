# Last modified: 2026-11-10 | Change: "Before the crash" entry (keeper_crash), placed first so it wins over Latest when they are the same layout | Full history: git log
"""Turns thousands of saved snapshots into a short list of meaningful moments. Read-only (thinning lives in keeper_prune)."""

from datetime import datetime, timedelta

CHECKPOINTS = ((timedelta(minutes=15), "15 min ago"), (timedelta(hours=1), "1 hour ago"),
               (timedelta(hours=3), "3 hours ago"))
PAST_DAYS = 5


# WHAT IT DOES: Reads a snapshot's capture time as a local, timezone-aware datetime.
# DEPENDS ON: ISO capture times written by save_snapshot.
# AFFECTS: Unparseable times are left out of time-based entries.
def captured(snapshot):
    try:
        return datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00")).astimezone()
    except (KeyError, TypeError, ValueError):
        return None


# WHAT IT DOES: Formats a clock time the way the chooser shows it, e.g. "9:05 AM".
# DEPENDS ON: A datetime.
# AFFECTS: Labels only.
def clock(moment):
    return moment.strftime("%I:%M %p").lstrip("0")


# WHAT IT DOES: Builds the chooser entries, newest first: Latest, Before last reboot, recent checkpoints, days.
# DEPENDS ON: History sorted newest first, as read_history returns it. crash: keeper_crash.crash_facts' "crash"
# (None: no crash entry, exactly as before).
# AFFECTS: Returns (label, history_index) pairs; each saved snapshot appears at most once. The crash entry is
# added first, so when the newest layout IS the one before the crash it is labelled "Before the crash".
def chooser_entries(history, now=None, crash=None):
    if not history:
        return []
    now = (now or datetime.now()).astimezone()
    entries, used = [], set()

    def add(label, index):
        if index is not None and index not in used:
            used.add(index)
            entries.append((label, index))

    normal = [(index, captured(snap)) for index, (_, snap) in enumerate(history) if not snap.get("legacy")]
    normal = [(index, moment) for index, moment in normal if moment]
    if crash and crash.get("index") is not None and 0 <= crash["index"] < len(history):
        from keeper_crash import crash_label
        add(crash_label(crash, now), crash["index"])
    first_time = captured(history[0][1])
    # An old newest layout must look old: "Latest (Oct 29, 2:44 PM)", not "Latest (2:44 PM)".
    day = "" if not first_time or first_time.date() == now.date() else f"{first_time:%b} {first_time.day}, "
    add("Latest" + (f" ({day}{clock(first_time)})" if first_time else ""), 0)
    newest_boot = history[0][1].get("boot_id")
    before = next((index for index, _ in normal if history[index][1].get("boot_id") != newest_boot), None)
    if before is not None:
        moment = captured(history[before][1])
        add(f"Before last reboot ({moment:%b %d}, {clock(moment)})", before)
    for age, label in CHECKPOINTS:
        found = next((index for index, moment in normal if moment <= now - age), None)
        if found is not None:
            add(f"{label} ({clock(captured(history[found][1]))})", found)
    today = [(index, moment) for index, moment in normal if moment.date() == now.date()]
    if today:
        index, moment = today[-1]
        add(f"Today {clock(moment)} (first today)", index)
    for back in range(1, PAST_DAYS + 1):
        day = (now - timedelta(days=back)).date()
        found = next(((index, moment) for index, moment in normal if moment.date() == day), None)
        if found:
            name = "Yesterday" if back == 1 else f"{found[1]:%a %b %d}"
            add(f"{name} {clock(found[1])} (last that day)", found[0])
    for index, (_, snap) in enumerate(history):
        if snap.get("legacy"):
            add("Old prototype capture", index)
            break
    return entries

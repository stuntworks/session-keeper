# Last modified: 2026-11-10 | Change: Every crash this boot, the saved died list, the last finished restore and the boot id go to crash_facts | Full history: git log
"""Runs on the loading window's worker thread (keeper_loading.load_then_show). Read-only."""

from keeper_chooser import captured
from keeper_core import DATA
from keeper_crash import crash_facts, newest_drop, read_crash_times
from keeper_crash_record import died_for, last_restore_done
from keeper_fast_history import fast_history
from keeper_restore import live_session_ids


# WHAT IT DOES: Reads the saved layouts the screen offers (fast_history, not all ~10,000 files), the
# conversations already running (a 4-14 s process scan that used to freeze the screen on the Tk thread), and
# the crash facts: the newest Windows Terminal crash this boot, the layout before it and the chats that died.
# DEPENDS ON: keeper_fast_history, keeper_restore.live_session_ids, keeper_crash (event log read ~2 s),
# keeper_crash_record (the saved died list and the last finished restore), keeper_core.read_boot_id.
# AFFECTS: Returns (history, live, extras) for RecoveryWindow so nothing is read twice. The only write is
# keeper_crash_record.died_for saving a crash's died list the first time any process works it out.
def load_recovery_start():
    crashes = read_crash_times()                 # None = event log unreadable: crash_facts uses drops instead
    history = crash_history(crashes)
    if not history:
        return history, set(), None
    live = live_session_ids()
    return history, live, crash_facts(history, live, crashes, monitors=read_monitors_soft(),
                                      restored_at=last_restore_done(), remember=died_for, boot=read_boot_soft())


# WHAT IT DOES: The layouts the screen offers, plus the last one before each crash this boot. Without the event
# log, the newest sharp drop among them is looked at again with every layout between its two ends loaded,
# so the "before" side is the last big layout, not just the nearest one the fast read happened to keep.
# DEPENDS ON: keeper_fast_history.fast_history (read-only), keeper_crash.newest_drop.
# AFFECTS: Read-only. Returns history newest first.
def crash_history(crashes, data_dir=DATA, now=None):
    history = fast_history(data_dir, now, before=crashes or [])
    if crashes is None and (pair := newest_drop(history)):
        older, newer = (captured(history[index][1]) for index in pair)
        history = fast_history(data_dir, now, spans=[(older, newer)])
    return history


# WHAT IT DOES: This PC's monitors for placing the "no saved place" window when a layout has none saved.
# AFFECTS: Read-only; None when they cannot be read.
def read_monitors_soft():
    try:
        from keeper_display import read_monitors
        return read_monitors()
    except Exception:
        return None


# WHAT IT DOES: This boot's id (keeper_core.read_boot_id), or None when it cannot be read (then crash_facts
# uses the newest layout's boot).
# AFFECTS: Read-only.
def read_boot_soft():
    try:
        from keeper_core import read_boot_id
        return read_boot_id()
    except Exception:
        return None

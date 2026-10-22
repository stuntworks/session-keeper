# Last modified: 2026-10-21 | Change: Everything slow the recover screen needs, gathered off the Tk thread | Full history: git log
"""Runs on the loading window's worker thread (keeper_loading.load_then_show). Read-only."""

from keeper_fast_history import fast_history
from keeper_restore import live_session_ids


# WHAT IT DOES: Reads the saved layouts the screen offers (fast_history, not all ~10,000 files) and the
# conversations already running (a 4-14 s process scan that used to freeze the screen on the Tk thread).
# DEPENDS ON: keeper_fast_history and keeper_restore.live_session_ids.
# AFFECTS: Nothing is written; the result is handed to RecoveryWindow so neither is read twice.
def load_recovery_start():
    history = fast_history()
    return history, (live_session_ids() if history else set())

# Last modified: 2026-10-18 | Change: Wait for native restore before filling missing chats | Full history: WORK backup
"""Read-only startup observation; never launches or changes Windows settings."""
import json
import time
from keeper_live_view import read_live_chats

# WHAT IT DOES: Summarizes observed tabs and rejects incomplete identity evidence.
# DEPENDS ON: The existing live-chat inventory, including unplaced processes.
# AFFECTS: Unknown tabs cannot be mistaken for absent conversations.
def observation(report):
    windows = report['windows']
    tabs = [tab for window in windows for tab in window.get('tabs', [])]
    uncertain = any(tab.get('outcome') != 'MATCHED' or not tab.get('sessionId') for tab in tabs)
    uncertain = uncertain or any(row.get('role') not in ('worker', 'service') for row in report['unplaced'])
    fingerprint = json.dumps(windows, sort_keys=True)
    return fingerprint, bool(windows and tabs) and not uncertain, {tab['sessionId'] for tab in tabs if tab.get('sessionId')}

# WHAT IT DOES: Waits for visible, identified terminals to remain stable before automatic recovery.
# DEPENDS ON: Read-only scans, a cancellation event, and monotonic time.
# AFFECTS: No-window, unknown, failed, or changing observations pause recovery instead of triggering it.
# DANGER ZONE HIGH: Stability is observation, not a Windows completion signal; restore still rechecks every launch.
def wait_for_native_restore(cancel, reader=read_live_chats, clock=time.monotonic):
    started = clock()
    previous = None
    stable_since = started
    while clock() - started < 120:
        if cancel.is_set():
            return None
        report = reader()
        fingerprint, ready, live = observation(report)
        now = clock()
        if fingerprint != previous or not ready:
            stable_since = now
        previous = fingerprint
        if now - started >= 30 and now - stable_since >= 10 and ready:
            return live
        if cancel.wait(5):
            return None
    raise RuntimeError('Windows Terminal startup is not confirmed stable and identified. Automatic recovery is paused; inspect On screen now and use Preview.')

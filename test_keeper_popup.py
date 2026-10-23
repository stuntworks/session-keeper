# Last modified: 2026-10-22 | Change: Free Tk objects on the main thread at module end | Full history: git log
"""The watcher must stay silent while the user works; closing a tab is not a crash."""

import json
import tempfile
import tkinter as tk
from contextlib import nullcontext
import unittest
from pathlib import Path
from unittest.mock import patch

import keeper_app
from tk_test_support import free_tk_garbage

FIRST_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SECOND_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# WHAT IT DOES: Builds a snapshot holding the given matched Claude sessions in one window.
# DEPENDS ON: The version-one snapshot shape read by recovery_rows.
# AFFECTS: Provides before and after layouts for the watcher comparison.
def snapshot(session_ids):
    return {"version": 1, "captured_at": "2026-10-03T10:00:00+00:00", "boot_id": "boot-1",
            "windows": [{"hwnd": 1, "x": 0, "y": 0, "w": 1200, "h": 800, "windowTitle": "term",
                         "tabs": [{"rawTitle": "chat", "strippedTitle": "chat", "outcome": "MATCHED",
                                   "kind": "claude", "sessionId": session_id, "cwd": "D:\\"}
                                  for session_id in session_ids]}],
            "unmatched_sessions": [], "counts": {"matched": len(session_ids)}}


# WHAT IT DOES: Exercises a watcher cycle in which a recorded session is no longer present.
# DEPENDS ON: A real Tk root kept withdrawn and a stubbed recovery screen.
# AFFECTS: Catches any return of the mid-session popup the user reported on 2026-10-03.
class WatcherSilenceTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    # WHAT IT DOES: Closes one recorded tab and runs the watcher's completion step.
    # DEPENDS ON: snapshot_finished, which decides whether to show the recovery screen.
    # AFFECTS: the user closing a conversation must not raise the Session Keeper window.
    def test_closing_a_tab_does_not_show_the_recovery_window(self):
        with tempfile.TemporaryDirectory() as folder:
            before = Path(folder) / "before.json"
            after = Path(folder) / "after.json"
            before.write_text(json.dumps(snapshot([FIRST_ID, SECOND_ID])), encoding="utf-8")
            after.write_text(json.dumps(snapshot([FIRST_ID])), encoding="utf-8")
            with patch.object(keeper_app, "latest_recovery_snapshot",
                              return_value=(before, json.loads(before.read_text(encoding="utf-8")))),                     patch.object(keeper_app, "RecoveryWindow") as screen:
                keeper_app.snapshot_finished(self.root, after)
            self.assertFalse(screen.called, "watcher built a recovery screen for a closed tab")
            self.assertEqual(self.root.state(), "withdrawn")

    # WHAT IT DOES: Runs the completion step when a capture recorded nothing at all.
    # DEPENDS ON: snapshot_finished tolerating a missing snapshot path.
    # AFFECTS: A failed capture must not read as every conversation crashing.
    def test_empty_capture_does_not_show_the_recovery_window(self):
        with tempfile.TemporaryDirectory() as folder:
            before = Path(folder) / "before.json"
            before.write_text(json.dumps(snapshot([FIRST_ID])), encoding="utf-8")
            with patch.object(keeper_app, "latest_recovery_snapshot",
                              return_value=(before, json.loads(before.read_text(encoding="utf-8")))),                     patch.object(keeper_app, "RecoveryWindow") as screen:
                keeper_app.snapshot_finished(self.root, None)
            self.assertFalse(screen.called, "watcher built a recovery screen after an empty capture")
            self.assertEqual(self.root.state(), "withdrawn")


# WHAT IT DOES: Starts the background watcher with an older boot's layout available.
# DEPENDS ON: A patched Tk root, stubbed recovery screen, and no event loop.
# AFFECTS: Logging in must not throw the Session Keeper window at the user uninvited.
class StartupSilenceTests(unittest.TestCase):
    def test_watch_mode_startup_does_not_show_the_recovery_window(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(keeper_app.sys, "argv", ["keeper_app.py", "watch"]),                 patch.object(keeper_app, "auto_restore_enabled", return_value=False),                 patch.object(keeper_app.tk, "Tk", return_value=root),                 patch.object(root, "mainloop"),                 patch.object(keeper_app, "exclusive_operation", lambda name: nullcontext()),                 patch.object(keeper_app, "read_boot_id", return_value="boot-2"),                 patch.object(keeper_app, "watch"),                 patch.object(keeper_app, "RecoveryWindow") as screen:
            keeper_app.main()
        self.assertFalse(screen.called, "login raised a recovery screen nobody asked for")
        self.assertEqual(root.state(), "withdrawn")

# WHAT IT DOES: Frees this module's Tk objects on the main thread before other tests start threads.
# DEPENDS ON: tk_test_support.free_tk_garbage.
# AFFECTS: Prevents the random Tcl_AsyncDelete abort in later tests.
def tearDownModule():
    free_tk_garbage()


if __name__ == "__main__":
    unittest.main()

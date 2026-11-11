# Last modified: 2026-11-10 | Change: New. The recorder keeps recording through unreadable processes, open windows, log and login errors | Full history: git log
"""Fakes, temporary folders and injected clocks only. No real process is inspected, started or stopped,
and nothing is written to the real data folder."""

import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psutil

import keeper_app
import keeper_codex
import keeper_heartbeat
import keeper_inventory
import keeper_restore
from keeper_heartbeat import HEARTBEAT_FILE, WATCH_LOG, Recorder

CLAUDE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BUFFER_ERROR = "SystemExtendedHandleInformation buffer too big"


# WHAT IT DOES: A pretend claude.exe/codex.exe; any method named in `fails` raises that error.
# DEPENDS ON: Nothing real.
# AFFECTS: Test only.
def fake_process(pid, name="claude.exe", fails=None, files=()):
    fails = fails or {}

    def method(attribute, value):
        def call(*args):
            if attribute in fails:
                raise fails[attribute]
            return value
        return call

    process = SimpleNamespace(pid=pid, info={"pid": pid, "name": name, "cmdline": [name], "cwd": "C:\\work"})
    process.open_files = method("open_files", [SimpleNamespace(path=path) for path in files])
    process.create_time = method("create_time", 1.0)
    process.cwd = method("cwd", "C:\\work")
    process.as_dict = lambda attrs: {attr: process.info.get(attr) for attr in attrs}
    return process


# WHAT IT DOES: A temporary folder removed after the test.
def temporary_folder(test):
    folder = Path(tempfile.mkdtemp(prefix="keeper_rh_"))
    test.addCleanup(shutil.rmtree, folder, ignore_errors=True)
    return folder


# WHAT IT DOES: Item A. One process that cannot be inspected affects only itself; the scan finishes.
# DEPENDS ON: keeper_inventory.live_inventory and keeper_codex.active_codex_sessions with faked psutil.
# AFFECTS: Guards the ~30 hours of captures lost to "SystemExtendedHandleInformation buffer too big".
class UnreadableProcessTests(unittest.TestCase):
    def scan(self, processes, read_titles=False, title="Work"):
        skipped = []
        records = {101: {"pid": 101, "sessionId": CLAUDE_ID, "cwd": "C:\\work"}}
        with patch.object(keeper_inventory.psutil, "process_iter", return_value=processes), \
                patch.object(keeper_inventory, "process_role", return_value="interactive"), \
                patch.object(keeper_inventory, "claude_record",
                             side_effect=lambda process: (process.create_time(), records.get(process.pid))[1]), \
                patch.object(keeper_inventory, "console_title", return_value=title), \
                patch.object(keeper_inventory, "active_codex_sessions", return_value=[]):
            return keeper_inventory.live_inventory(read_titles=read_titles, skipped=skipped), skipped

    def test_open_files_error_makes_that_claude_unidentified_and_the_scan_goes_on(self):
        broken = fake_process(100, fails={"open_files": RuntimeError(BUFFER_ERROR)})
        sessions, skipped = self.scan([broken, fake_process(101)])
        self.assertEqual([session["pid"] for session in sessions], [100, 101])
        self.assertTrue(sessions[0]["uncertain"])
        self.assertEqual(sessions[1]["sessionId"], CLAUDE_ID)
        self.assertEqual(len(skipped), 1)
        self.assertIn("100", skipped[0])
        self.assertIn(BUFFER_ERROR, skipped[0])

    def test_access_denied_claude_is_left_out_of_a_capture_but_kept_for_restore_checks(self):
        elevated = fake_process(100, fails={"create_time": psutil.AccessDenied(100)})
        captured, skipped = self.scan([elevated, fake_process(101)], read_titles=True)
        self.assertEqual([session["pid"] for session in captured], [101])
        self.assertIn("Claude 100", skipped[0])
        checked, _ = self.scan([elevated, fake_process(101)], read_titles=False)
        self.assertEqual([session["pid"] for session in checked], [100, 101])
        self.assertTrue(checked[0]["uncertain"])
        self.assertEqual(checked[0]["role"], "unknown")

    def test_title_probe_timeout_only_loses_that_title(self):
        def title(pid):
            if pid == 100:
                raise subprocess.TimeoutExpired(["probe"], 10)
            return "Work"
        skipped = []
        with patch.object(keeper_inventory.psutil, "process_iter", return_value=[fake_process(100), fake_process(101)]), \
                patch.object(keeper_inventory, "process_role", return_value="interactive"), \
                patch.object(keeper_inventory, "claude_record",
                             side_effect=lambda process: {"pid": process.pid, "sessionId": CLAUDE_ID}), \
                patch.object(keeper_inventory, "console_title", side_effect=title), \
                patch.object(keeper_inventory, "active_codex_sessions", return_value=[]):
            sessions = keeper_inventory.live_inventory(read_titles=True, skipped=skipped)
        self.assertEqual([(session["pid"], session["title"]) for session in sessions], [(100, None), (101, "Work")])
        self.assertIn("tab title", skipped[0])

    def test_unreadable_codex_does_not_end_the_codex_scan(self):
        processes = [fake_process(200, "codex.exe", fails={"open_files": RuntimeError(BUFFER_ERROR)}),
                     fake_process(201, "codex.exe", fails={"open_files": psutil.AccessDenied(201)})]
        skipped = []
        with patch.object(keeper_codex.psutil, "process_iter", return_value=processes), \
                patch.object(keeper_codex, "console_title", return_value="Codex work"):
            sessions = keeper_codex.active_codex_sessions(read_titles=False, skipped=skipped)
        self.assertEqual([(session["pid"], session["sessionId"]) for session in sessions], [(200, None), (201, None)])
        self.assertEqual(len(skipped), 2)

    def test_restore_still_stops_on_an_unreadable_claude(self):
        elevated = fake_process(100, fails={"create_time": psutil.AccessDenied(100)})
        with patch.object(keeper_inventory.psutil, "process_iter", return_value=[elevated]), \
                patch.object(keeper_inventory, "process_role", return_value="interactive"), \
                patch.object(keeper_inventory, "claude_record", side_effect=lambda process: process.create_time()), \
                patch.object(keeper_inventory, "active_codex_sessions", return_value=[]), \
                patch.object(keeper_codex, "console_title", return_value=None), \
                patch.object(keeper_restore, "STRICT_WAIT_SECONDS", 0):
            with self.assertRaises(keeper_restore.UnidentifiedSessions):
                keeper_restore.live_session_ids(strict=True)


# WHAT IT DOES: Items A and C on the log side: one line when something changes, never one per capture.
# DEPENDS ON: keeper_heartbeat.Recorder writing into a temporary folder.
# AFFECTS: Keeps watcher_errors.log readable.
class ChangeLogTests(unittest.TestCase):
    def setUp(self):
        self.folder = temporary_folder(self)
        self.recorder = Recorder(self.folder, wall=lambda: 1_800_000_000.0, pid=7)

    def lines(self):
        path = self.folder / WATCH_LOG
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def test_skipped_processes_are_logged_once_per_change(self):
        for unused in range(3):
            self.recorder.processes_skipped(["Claude 100: could not read its open files"])
        self.assertEqual(len(self.lines()), 1)
        self.assertIn("Claude 100", self.lines()[0])
        self.recorder.processes_skipped([])
        self.recorder.processes_skipped([])
        self.assertEqual(len(self.lines()), 2)
        self.assertIn("read again", self.lines()[1])

    def test_nothing_matched_is_logged_once_and_its_end_once(self):
        for unused in range(4):
            self.assertFalse(self.recorder.review({"matched": 0, "tabs_total": 35}))
        self.assertEqual(len(self.lines()), 1)
        self.assertIn("none of the 35", self.lines()[0])
        self.assertTrue(self.recorder.review({"matched": 33, "tabs_total": 35}))
        self.assertEqual(len(self.lines()), 2)
        self.assertIn("saved again", self.lines()[1])

    def test_no_terminal_tabs_is_not_a_problem(self):
        self.assertFalse(self.recorder.review({"matched": 0, "tabs_total": 0}))
        self.assertEqual(self.lines(), [])

    def test_sharp_drop_is_saved_and_logged_once(self):
        self.assertTrue(self.recorder.review({"matched": 33, "tabs_total": 35}))
        self.assertTrue(self.recorder.review({"matched": 3, "tabs_total": 35}))
        self.assertTrue(self.recorder.review({"matched": 3, "tabs_total": 35}))
        self.assertEqual(len(self.lines()), 1)
        self.assertIn("only 3 of 35", self.lines()[0])
        self.assertIn("33 of 35", self.lines()[0])

    def test_closing_a_window_is_not_a_drop(self):
        self.recorder.review({"matched": 33, "tabs_total": 35})
        self.recorder.review({"matched": 13, "tabs_total": 15})
        self.assertEqual(self.lines(), [])

    def test_snapshot_once_logs_through_the_running_recorder(self):
        def capture(skipped):
            skipped.append("Claude 100: could not read its open files")
            return {"counts": {"matched": 0, "tabs_total": 35}}
        with patch.object(keeper_heartbeat, "ACTIVE", self.recorder), \
                patch.object(keeper_app, "exclusive_operation", lambda name: nullcontext()), \
                patch.object(keeper_app, "capture_snapshot", side_effect=capture), \
                patch.object(keeper_app, "save_snapshot") as save:
            self.assertIsNone(keeper_app.snapshot_once())
        save.assert_not_called()
        self.assertEqual(len(self.lines()), 2)


# WHAT IT DOES: Items B, D and the heartbeat half of F, on the watch loop itself.
# DEPENDS ON: keeper_app.watch / snapshot_finished / snapshot_failed with a fake root.
# AFFECTS: Guards the chain of captures that must never stop.
class WatchLoopTests(unittest.TestCase):
    def setUp(self):
        self.folder = temporary_folder(self)

    def test_records_while_the_login_recovery_window_is_open(self):
        for state in ("normal", "iconic", "withdrawn"):
            root = MagicMock()
            root.state.return_value = state
            with patch.object(keeper_app, "run_background") as background:
                keeper_app.watch(root)
            background.assert_called_once()
            self.assertIs(background.call_args.args[1], keeper_app.record_once, state)

    def test_closing_the_login_window_hides_it_and_recording_goes_on(self):
        screen = SimpleNamespace(busy=False, watch_mode=True, root=MagicMock(), cancel_countdown=lambda: None)
        keeper_app.RecoveryWindow.skip(screen)
        screen.root.withdraw.assert_called_once()
        screen.root.destroy.assert_not_called()
        with patch.object(keeper_app, "run_background") as background:
            keeper_app.watch(screen.root)
        background.assert_called_once()

    def test_next_capture_is_scheduled_even_when_the_log_cannot_be_written(self):
        blocked = self.folder / "not_a_folder"
        blocked.write_text("a file where the data folder should be", encoding="utf-8")
        root = MagicMock()
        with patch.object(keeper_app, "DATA", blocked):
            keeper_app.snapshot_failed(root, RuntimeError("probe failed"))
        root.after.assert_called_once()
        self.assertEqual(root.after.call_args.args[0], keeper_app.INTERVAL_MS)

    def test_failure_is_logged_and_scheduled(self):
        root = MagicMock()
        with patch.object(keeper_app, "DATA", self.folder):
            keeper_app.snapshot_failed(root, RuntimeError("probe failed"))
        self.assertIn("probe failed", (self.folder / WATCH_LOG).read_text(encoding="utf-8"))
        root.after.assert_called_once()

    def test_heartbeat_after_save_and_after_failure(self):
        times = iter([1_799_999_000.0, 1_800_000_000.0, 1_800_000_120.0])   # recorder start, save, failure
        recorder = Recorder(self.folder, wall=lambda: next(times), pid=42)
        root = MagicMock()
        with patch.object(keeper_heartbeat, "ACTIVE", recorder), patch.object(keeper_app, "DATA", self.folder):
            keeper_app.snapshot_finished(root, Path("snapshot_x.json"))
            first = json.loads((self.folder / HEARTBEAT_FILE).read_text(encoding="utf-8"))
            keeper_app.snapshot_failed(root, RuntimeError("probe failed"))
        second = json.loads((self.folder / HEARTBEAT_FILE).read_text(encoding="utf-8"))
        self.assertEqual(first["pid"], 42)
        self.assertEqual(first["last_saved"], first["last_attempt"])
        self.assertIsNone(first["last_error"])
        self.assertEqual(second["last_saved"], first["last_saved"])
        self.assertNotEqual(second["last_attempt"], first["last_attempt"])
        self.assertEqual(second["last_error"], "probe failed")
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), [HEARTBEAT_FILE, WATCH_LOG])

    def test_heartbeat_is_silent_outside_the_watch_process(self):
        with patch.object(keeper_heartbeat, "ACTIVE", None):
            keeper_heartbeat.capture_saved(Path("x.json"))
        self.assertEqual(list(self.folder.iterdir()), [])


# WHAT IT DOES: Item E. Nothing the login recovery screen does may end the recorder.
# DEPENDS ON: keeper_app.main in watch mode with a fake root, fake lock and a temporary data folder.
# AFFECTS: Guards the whole-boot outage when startup raised.
class StartupSurvivalTests(unittest.TestCase):
    def start_watch(self, **failures):
        folder = temporary_folder(self)
        root = MagicMock()
        patches = {"auto_restore_enabled": MagicMock(return_value=True),
                   "read_boot_id": MagicMock(return_value="boot-2"),
                   "startup_recovery": MagicMock(return_value=(Path("saved.json"), {"windows": []})),
                   "RecoveryWindow": MagicMock()}
        for name, error in failures.items():
            patches[name] = MagicMock(side_effect=error)
        with patch.object(keeper_app.sys, "argv", ["keeper_app.py", "watch"]), \
                patch.object(keeper_app.tk, "Tk", return_value=root), \
                patch.object(keeper_app, "make_dpi_aware"), patch.object(keeper_app, "install_ui_guard"), \
                patch.object(keeper_app, "exclusive_operation", lambda name: nullcontext()), \
                patch.object(keeper_app, "DATA", folder), \
                patch.multiple(keeper_app, **patches):
            keeper_app.main()
        return root, folder

    def assert_still_recording(self, root, folder, text):
        root.mainloop.assert_called_once()
        self.assertTrue(any(call.args[0] == 1000 for call in root.after.call_args_list), "first capture not scheduled")
        self.assertIn(text, (folder / WATCH_LOG).read_text(encoding="utf-8"))
        self.assertIsNone(keeper_heartbeat.ACTIVE, "recorder state must not leak out of main()")

    def test_boot_id_failure_still_records(self):
        root, folder = self.start_watch(read_boot_id=RuntimeError("Cannot determine Windows boot time"))
        self.assert_still_recording(root, folder, "Cannot determine Windows boot time")

    def test_startup_recovery_failure_still_records(self):
        root, folder = self.start_watch(startup_recovery=OSError("disk busy"))
        self.assert_still_recording(root, folder, "disk busy")

    def test_recovery_window_failure_hides_it_and_still_records(self):
        root, folder = self.start_watch(RecoveryWindow=RuntimeError("Cannot inspect Claude process 5"))
        self.assert_still_recording(root, folder, "Cannot inspect Claude process 5")
        root.withdraw.assert_called()


if __name__ == "__main__":
    unittest.main()

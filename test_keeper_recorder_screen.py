# Last modified: 2026-11-10 | Change: New. Recovery screen warns when the recorder is off or stale, shows dates, reloads on relaunch | Full history: git log
"""Hidden Tk roots, fakes, temporary folders and throwaway mutex names only. The real recorder is never
checked-and-started, no process is launched, and nothing is written to the real data folder."""

import json
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import keeper_app
import keeper_reload
import keeper_single
import keeper_watchdog
from keeper_chooser import chooser_entries
from keeper_heartbeat import HEARTBEAT_FILE
from keeper_locks import operation_running
from keeper_recorder_health import check_recorder, recorder_warning, saved_when, show_recorder_health
from keeper_reload import ReloadWatch
from keeper_theme import STOP
from tk_test_support import free_tk_garbage, keep_drawer_on_main_thread, no_workers_left, run_inline

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 11, 10, 9, 0).astimezone()
OLD = (NOW - timedelta(days=12, hours=1)).isoformat()
SESSION = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def tearDownModule():
    free_tk_garbage()


# WHAT IT DOES: A one-window saved layout captured at `captured`.
def layout(captured, cwd="C:\\work"):
    return {"version": 1, "captured_at": captured, "boot_id": "boot-1", "monitors": [],
            "windows": [{"x": 0, "y": 0, "w": 1200, "h": 800, "tabs": [
                {"outcome": "MATCHED", "sessionId": SESSION, "strippedTitle": "chat", "cwd": cwd}]}],
            "unmatched_sessions": [], "counts": {"matched": 1, "tabs_total": 1}}


# WHAT IT DOES: Item F, the words. Short, plain, and always says how old the newest layout is.
# DEPENDS ON: keeper_recorder_health with a fixed clock.
# AFFECTS: What the owner reads at the top of the recovery screen.
class WarningTextTests(unittest.TestCase):
    def test_recorder_off_and_started_again(self):
        text = recorder_warning(False, True, None, OLD, NOW)
        self.assertEqual(text, "Recorder was off. It is on again now. Newest layout is 12 days old.")

    def test_recorder_off_and_could_not_start(self):
        self.assertTrue(recorder_warning(False, False, None, OLD, NOW).startswith(
            "Recorder is not running. Newest layout is 12 days old."))

    def test_recorder_stuck(self):
        beat = {"last_attempt": (NOW - timedelta(hours=3)).isoformat()}
        self.assertEqual(recorder_warning(True, False, beat, OLD, NOW),
                         "Recorder is stuck. Last try was 3 hours ago. Newest layout is 12 days old.")

    def test_recorder_running_but_not_saving(self):
        beat = {"last_attempt": (NOW - timedelta(minutes=1)).isoformat(),
                "note": "none of the 35 terminal tabs matched a chat",
                "problem_since": (NOW - timedelta(hours=2)).isoformat()}   # R2: only a real problem streak warns
        text = recorder_warning(True, False, beat, (NOW - timedelta(hours=3)).isoformat(), NOW)
        self.assertTrue(text.startswith("Recorder is on but has not saved for 3 hours."))
        self.assertIn("none of the 35", text)

    def test_healthy_recorder_says_nothing(self):
        beat = {"last_attempt": (NOW - timedelta(minutes=1)).isoformat()}
        self.assertEqual(recorder_warning(True, False, beat, (NOW - timedelta(minutes=2)).isoformat(), NOW), "")

    def test_saved_when_shows_date_and_age(self):
        captured = datetime(2026, 10, 29, 14, 44).astimezone()
        text = saved_when({"captured_at": captured.isoformat()}, now=captured + timedelta(days=12, minutes=5))
        self.assertEqual(text, "Oct 29, 2:44 PM - 12 days ago")

    def test_chooser_latest_shows_the_date_when_not_today(self):
        captured = datetime(2026, 10, 29, 14, 44).astimezone()
        history = [(Path("a.json"), {"captured_at": captured.isoformat(), "boot_id": "b"})]
        self.assertEqual(chooser_entries(history, captured + timedelta(days=12))[0][0], "Latest (Oct 29, 2:44 PM)")
        self.assertEqual(chooser_entries(history, captured + timedelta(hours=1))[0][0], "Latest (2:44 PM)")


# WHAT IT DOES: Item F, the checks. Start the recorder only when it is really not running.
# DEPENDS ON: Injected is_running and start; a temporary heartbeat folder; a throwaway mutex name.
# AFFECTS: Nothing real.
class RecorderCheckTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="keeper_rs_"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.started = []

    def check(self, running):
        return check_recorder(self.folder, OLD, is_running=running, start=lambda: self.started.append(1), now=NOW)

    def test_not_running_starts_it_once_and_says_so(self):
        self.assertIn("on again now", self.check(lambda name: False))
        self.assertEqual(self.started, [1])

    def test_running_is_left_alone(self):
        (self.folder / HEARTBEAT_FILE).write_text(json.dumps({"last_attempt": NOW.isoformat()}), encoding="utf-8")
        # R2: an old-format heartbeat (no pid / started_at) cannot be tied to the live recorder: stay silent
        self.assertEqual(self.check(lambda name: True), "")
        self.assertEqual(self.started, [])

    def test_a_check_that_cannot_tell_never_starts_a_second_recorder(self):
        def broken(name):
            raise OSError("no")
        self.check(broken)
        self.assertEqual(self.started, [])

    def test_lock_check_sees_a_held_lock_without_taking_it(self):
        name = f"Local\\SessionKeeperTest-{uuid.uuid4().hex}"
        self.assertFalse(operation_running("watch", name=name))
        handle = keeper_single.try_own(name)
        try:
            self.assertTrue(operation_running("watch", name=name))
        finally:
            keeper_single.release(handle)
        self.assertFalse(operation_running("watch", name=name))

    def test_recorder_is_started_hidden_without_handover(self):
        with patch.object(keeper_watchdog.subprocess, "Popen") as popen:
            keeper_watchdog.launch_replacement(handover=False)
        command = popen.call_args.args[0]
        self.assertEqual(command[1:], [str(keeper_watchdog.APP), "watch", "--no-login-window"])
        self.assertTrue(popen.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)


# WHAT IT DOES: Items F and G on the real recovery screen, on a hidden Tk root with every read faked.
# DEPENDS ON: keeper_app.RecoveryWindow.
# AFFECTS: Test only.
class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(no_workers_left, self)
        self.addCleanup(self.root.destroy)
        keep_drawer_on_main_thread(self)
        self.history = [(Path("old.json"), layout(OLD))]
        for target, value in [("fast_history", self.history), ("live_session_ids", set()),
                              ("auto_restore_enabled", False), ("terminal_restore_enabled", False),
                              ("window_geometry", "1400x860")]:
            patch.object(keeper_app, target, return_value=value).start()
        patch.object(keeper_app, "LiveChatsPanel").start()
        patch.object(keeper_app, "run_background", run_inline).start()
        self.addCleanup(patch.stopall)
        self.view = keeper_app.RecoveryWindow(self.root, *self.history[0], history=self.history, live=set())

    def test_summary_shows_the_layout_date_and_age(self):
        text = self.view.header.sub_label.cget("text")
        moment = datetime.fromisoformat(OLD)
        self.assertIn(f"Saved {moment:%b} {moment.day}, ", text)
        self.assertIn(" days ago", text)

    def test_warning_line_is_red_and_shown(self):
        folder = Path(tempfile.mkdtemp(prefix="keeper_rs_"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        show_recorder_health(self.view, folder, is_running=lambda name: False, start=lambda: None)
        line = self.view.header.warning_label
        self.assertTrue(line.cget("text").startswith("Recorder was off. It is on again now. Newest layout is"))
        self.assertEqual(str(line.cget("foreground")).lower(), STOP.lower())
        self.assertTrue(line.winfo_manager())
        show_recorder_health(self.view, folder, is_running=lambda name: True, start=lambda: None,
                             now=datetime.now().astimezone() - timedelta(days=12))
        self.assertFalse(line.winfo_manager())

    def test_reload_swaps_in_the_newest_history(self):
        newer = (Path("new.json"), layout(datetime.now().astimezone().isoformat()))
        keeper_reload.reload_history(self.view, [newer] + self.history, set())
        self.assertEqual(self.view.path, Path("new.json"))
        self.assertEqual(len(self.view.history), 2)
        self.assertTrue(self.view.entries[0][0].startswith("Latest"))
        self.assertIn("just now", self.view.header.sub_label.cget("text"))


# WHAT IT DOES: Item G. A second launch leaves a note; the open screen reloads only when it is idle.
# DEPENDS ON: keeper_single.claim_recover_screen with a throwaway mutex; ReloadWatch with a fake screen.
# AFFECTS: Nothing real.
class ReloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="keeper_rl_"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.note = self.folder / "recover_reload.json"

    def test_second_launch_leaves_a_reload_note(self):
        name = f"SessionKeeperTest-{uuid.uuid4().hex}"
        code = (f"import sys, time; sys.path.insert(0, {str(HERE)!r}); import keeper_single as k; "
                f"h = k.try_own({name!r}); print('owned', flush=True); time.sleep(3); k.release(h)")
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.addCleanup(child.stdout.close)
        self.assertEqual(child.stdout.readline().strip(), "owned")
        info = self.folder / "recover_window.json"
        info.write_text(json.dumps({"pid": child.pid, "hwnd": 4242}), encoding="utf-8")
        with patch.object(keeper_single, "window_pid", return_value=child.pid), \
                patch.object(keeper_single, "bring_to_front"):
            self.assertFalse(keeper_single.claim_recover_screen(name, wait=2, info=info))
        self.assertTrue(self.note.exists())
        child.wait(timeout=20)

    def fake_view(self):
        root = MagicMock()
        root.winfo_exists.return_value = True
        return SimpleNamespace(root=root, busy=False, countdown_job=None, native_wait=None, pick_token=None,
                               countdown_text=MagicMock())

    def test_new_note_reloads_once_and_waits_while_busy(self):
        view = self.fake_view()
        loaded = []
        history = [(Path("new.json"), layout(NOW.isoformat()))]
        watch = ReloadWatch(view, self.note, load=lambda: (history, set()),
                            run=lambda root, action, ok, err: ok(action()))
        with patch.object(keeper_reload, "reload_history", side_effect=lambda v, h, live: loaded.append(h)):
            watch.poll()
            self.assertEqual(loaded, [])                     # no note yet
            keeper_single.request_reload(self.note)
            view.busy = True
            watch.poll()
            self.assertEqual(loaded, [])                     # a restore is running: wait
            view.busy = False
            watch.poll()
            watch.poll()
            self.assertEqual(loaded, [history])              # once, not on every poll

    def test_note_left_before_the_screen_opened_is_not_reloaded(self):
        keeper_single.request_reload(self.note)
        view = self.fake_view()
        run = MagicMock()
        ReloadWatch(view, self.note, load=lambda: ([], set()), run=run).poll()
        run.assert_not_called()

    def test_open_recovery_wires_the_warning_and_the_reload(self):
        root = MagicMock()
        with patch.object(keeper_app, "RecoveryWindow") as screen, \
                patch.object(keeper_app, "show_recorder_health") as health, \
                patch.object(keeper_app, "ReloadWatch") as reload_watch, \
                patch.object(keeper_app, "publish_window"):
            keeper_app.open_recovery(root, [(Path("a.json"), layout(OLD))], set())
        health.assert_called_once_with(screen.return_value, keeper_app.DATA)
        reload_watch.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()

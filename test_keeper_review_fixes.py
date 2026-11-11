# Last modified: 2026-11-10 | Change: record_once test patches after_capture (it runs the hourly prune now) | Full history: git log
"""Fakes, injected clocks, temporary folders and throwaway mutex names only. No real recorder, recovery
screen or Windows Terminal is started, and nothing is written to the real data folder."""

import json
import shutil
import tempfile
import unittest
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import keeper_app
import keeper_heartbeat
import keeper_locks
import keeper_watchdog
from keeper_heartbeat import HEARTBEAT_FILE, Recorder, read_heartbeat
from keeper_history import startup_recovery
from keeper_recorder_health import (RECHECK_MS, STUCK_START, check_recorder, live_heartbeat,
                                    recorder_warning, show_recorder_health)

NOW = datetime(2026, 11, 10, 9, 0).astimezone()
BUSY = "Session Keeper restore is already running"


def temporary_folder(test):
    folder = Path(tempfile.mkdtemp(prefix="keeper_rv_"))
    test.addCleanup(shutil.rmtree, folder, ignore_errors=True)
    return folder


# WHAT IT DOES: A clock the test moves by hand; sleep() moves it too.
class FakeClock:
    def __init__(self, now=1000.0):
        self.now, self.sleeps = now, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


# WHAT IT DOES: A lock stand-in that is busy for the first `busy` tries.
def busy_lock(busy, tries):
    class Lock:
        def __enter__(self):
            tries.append(1)
            if len(tries) <= busy:
                raise RuntimeError(BUSY)

        def __exit__(self, *exc):
            return False

    return lambda name: Lock()


# WHAT IT DOES: R1. A restore waits (bounded) for a background capture; never for another restore.
# DEPENDS ON: keeper_locks.wait_out_capture / capture_mark with fakes or throwaway mutex names.
# AFFECTS: Nothing real.
class RestoreWaitsForCaptureTests(unittest.TestCase):
    def test_restore_waits_for_a_capture_then_runs(self):
        clock, tries, ran = FakeClock(), [], []
        with keeper_locks.wait_out_capture("restore", lock=busy_lock(3, tries), capturing=lambda: True,
                                           clock=clock, sleep=clock.sleep):
            ran.append(1)
        self.assertEqual((len(tries), ran), (4, [1]))

    def test_restore_fails_at_once_when_another_restore_holds_the_lock(self):
        tries = []
        with self.assertRaises(RuntimeError):
            with keeper_locks.wait_out_capture("restore", lock=busy_lock(99, tries), capturing=lambda: False,
                                               sleep=lambda s: self.fail("must not wait for a restore")):
                pass
        self.assertEqual(len(tries), 1)

    def test_wait_is_bounded(self):
        clock = FakeClock()
        with self.assertRaises(RuntimeError):
            with keeper_locks.wait_out_capture("restore", lock=busy_lock(10 ** 6, []), capturing=lambda: True,
                                               wait=60, clock=clock, sleep=clock.sleep):
                pass
        self.assertLessEqual(clock.now - 1000.0, 61)

    def test_capture_mark_is_seen_only_while_held(self):
        prefix = f"Local\\SessionKeeperTest-{uuid.uuid4().hex}"
        with patch.object(keeper_locks, "lock_name", lambda operation: f"{prefix}-{operation}"):
            self.assertFalse(keeper_locks.operation_running("restore-capture"))
            with keeper_locks.capture_mark("restore"):
                self.assertTrue(keeper_locks.operation_running("restore-capture"))
            self.assertFalse(keeper_locks.operation_running("restore-capture"))

    def test_record_once_holds_the_capture_mark(self):
        prefix = f"Local\\SessionKeeperTest-{uuid.uuid4().hex}"
        seen = []
        with patch.object(keeper_locks, "lock_name", lambda operation: f"{prefix}-{operation}"), \
                patch.object(keeper_app, "snapshot_once",
                             lambda: seen.append(keeper_locks.operation_running("restore-capture"))), \
                patch.object(keeper_app, "after_capture"):
            keeper_app.record_once()
        self.assertEqual(seen, [True])

    def test_restore_and_its_check_use_the_waiting_lock(self):
        import keeper_restore
        calls = []

        def refuse(name, lock=None):
            calls.append((name, lock))
            raise RuntimeError("stop here")

        with patch.object(keeper_restore, "wait_out_capture", refuse):
            for run in (lambda: keeper_restore.assess_restore({"windows": []}, [], None),
                        lambda: keeper_restore.restore_selected({"windows": []}, [], True)):
                try:
                    run()
                except RuntimeError:
                    pass
        self.assertEqual([name for name, _ in calls], ["restore", "restore"])
        self.assertTrue(all(lock is keeper_restore.exclusive_operation for _, lock in calls))


# WHAT IT DOES: A stand-in for the watch-mode Tk root.
class FakeRoot:
    def __init__(self, state="normal"):
        self.timers, self._state = [], state

    def after(self, ms, callback):
        self.timers.append(ms)

    def state(self):
        return self._state


# WHAT IT DOES: A stand-in for the login recovery window (keeper_app.RecoveryWindow is patched to this class).
class FakeLoginView:
    def __init__(self, countdown=None, busy=False):
        self.watch_mode = True
        self.busy, self.countdown_job, self.native_wait, self.pick_token = busy, countdown, None, None


# WHAT IT DOES: R1 + R5 at the watch loop. Skip (and tick) while the login view is busy; capture otherwise.
# DEPENDS ON: keeper_app.watch with run_background, the watchdog and heartbeat hooks patched.
# AFFECTS: Nothing real.
class WatchLoopTests(unittest.TestCase):
    def run_watch(self, root):
        self.captures, self.ticks, self.marks = [], [], []
        with patch.object(keeper_app, "RecoveryWindow", FakeLoginView), \
                patch.object(keeper_app, "run_background", lambda *args: self.captures.append(1)), \
                patch.object(keeper_watchdog, "tick", lambda: self.ticks.append(1)), \
                patch.object(keeper_watchdog, "capture_started"), \
                patch.object(keeper_heartbeat, "login_window", lambda is_open: self.marks.append(is_open)):
            keeper_app.watch(root)

    def test_counting_down_login_view_skips_the_capture_but_ticks(self):
        root = FakeRoot()
        root._keeper_window = FakeLoginView(countdown="after#1")
        self.run_watch(root)
        self.assertEqual((self.captures, self.ticks, root.timers), ([], [1], [keeper_app.SKIP_MS]))

    def test_restoring_login_view_skips_the_capture(self):
        root = FakeRoot()
        root._keeper_window = FakeLoginView(busy=True)
        self.run_watch(root)
        self.assertEqual(self.captures, [])

    def test_skipping_is_capped_so_recording_never_stops_for_good(self):
        root = FakeRoot()
        root._keeper_window = FakeLoginView(countdown="after#1")
        root._keeper_skips = keeper_app.MAX_SKIPS
        self.run_watch(root)
        self.assertEqual((self.captures, self.ticks), ([1], [1]))
        self.assertEqual(root._keeper_skips, 0)

    def test_idle_open_login_window_still_records_and_marks_the_layout(self):
        root = FakeRoot("iconic")
        root._keeper_window = FakeLoginView()
        self.run_watch(root)
        self.assertEqual((self.captures, self.ticks, self.marks), ([1], [1], [True]))

    def test_no_login_window_records_unmarked(self):
        root = FakeRoot("withdrawn")
        root._keeper_window = FakeLoginView()
        self.run_watch(root)
        self.assertEqual((self.captures, self.marks), ([1], [False]))


# WHAT IT DOES: R2. Heartbeat at start; only the live recorder's heartbeat counts; no false alarms.
# DEPENDS ON: keeper_heartbeat.Recorder and keeper_recorder_health with injected process lookups.
# AFFECTS: A temporary folder only.
class HonestWarningTests(unittest.TestCase):
    def setUp(self):
        self.folder = temporary_folder(self)

    def write_beat(self, **fields):
        (self.folder / HEARTBEAT_FILE).write_text(json.dumps(fields), encoding="utf-8")

    def test_recorder_writes_a_heartbeat_when_it_starts(self):
        recorder = Recorder(self.folder, wall=lambda: NOW.timestamp(), pid=4242)
        recorder.begin()
        beat = read_heartbeat(self.folder)
        self.assertEqual((beat["pid"], beat["started_at"], beat["last_attempt"]), (4242, NOW.isoformat(), None))

    def test_previous_boot_heartbeat_does_not_say_stuck(self):
        self.write_beat(pid=999, started_at=(NOW - timedelta(days=1)).isoformat(),
                        last_attempt=(NOW - timedelta(hours=9)).isoformat())
        text = check_recorder(self.folder, (NOW - timedelta(hours=9)).isoformat(), is_running=lambda n: True,
                              start=lambda: None, now=NOW, started_of=lambda pid: None)   # pid 999 is gone
        self.assertEqual(text, "")

    def test_reused_pid_is_not_the_recorder(self):
        began = NOW - timedelta(days=1)
        beat = {"pid": 7, "started_at": began.isoformat()}
        self.assertIsNone(live_heartbeat(beat, lambda pid: (NOW - timedelta(minutes=5)).timestamp()))
        self.assertEqual(live_heartbeat(beat, lambda pid: began.timestamp() - 3), beat)
        self.assertIsNone(live_heartbeat({"last_attempt": NOW.isoformat()}, lambda pid: 0.0))   # old format

    def test_nothing_matched_yet_after_reboot_is_not_a_warning(self):
        beat = {"last_attempt": (NOW - timedelta(minutes=1)).isoformat(), "note": None, "problem_since": None}
        self.assertEqual(recorder_warning(True, False, beat, (NOW - timedelta(days=2)).isoformat(), NOW), "")

    def test_short_problem_streak_is_not_a_warning_yet(self):
        beat = {"last_attempt": (NOW - timedelta(minutes=1)).isoformat(),
                "note": "none of the 35 terminal tabs matched a chat",
                "problem_since": (NOW - timedelta(minutes=10)).isoformat()}
        self.assertEqual(recorder_warning(True, False, beat, (NOW - timedelta(days=2)).isoformat(), NOW), "")

    def test_long_problem_streak_is_reported(self):
        beat = {"last_attempt": (NOW - timedelta(minutes=1)).isoformat(),
                "note": "none of the 35 terminal tabs matched a chat",
                "problem_since": (NOW - timedelta(hours=2)).isoformat()}
        text = recorder_warning(True, False, beat, (NOW - timedelta(hours=3)).isoformat(), NOW)
        self.assertTrue(text.startswith("Recorder is on but has not saved for 3 hours."))

    def test_waiting_for_a_restore_is_not_a_problem(self):
        recorder = Recorder(self.folder, wall=lambda: NOW.timestamp(), pid=1)
        recorder.begin()
        recorder.attempt_finished(error=RuntimeError(BUSY))
        beat = read_heartbeat(self.folder)
        self.assertEqual((beat["problem_since"], beat["last_error"]), (None, None))

    def test_problem_streak_keeps_its_start(self):
        clock = FakeClock(NOW.timestamp())
        recorder = Recorder(self.folder, wall=clock, pid=1)
        recorder.begin()
        recorder.review({"matched": 0, "tabs_total": 35})
        recorder.attempt_finished()
        first = read_heartbeat(self.folder)["problem_since"]
        clock.now += 600
        recorder.review({"matched": 0, "tabs_total": 35})
        recorder.attempt_finished()
        self.assertEqual(read_heartbeat(self.folder)["problem_since"], first)
        recorder.review({"matched": 3, "tabs_total": 35})
        recorder.attempt_finished(path=Path("saved.json"))
        self.assertIsNone(read_heartbeat(self.folder)["problem_since"])

    def test_stuck_is_checked_again_before_it_is_shown(self):
        view = MagicMock()
        view.history = [(Path("a.json"), {"captured_at": (NOW - timedelta(hours=9)).isoformat()})]
        self.write_beat(pid=5, started_at=(NOW - timedelta(days=1)).isoformat(),
                        last_attempt=(NOW - timedelta(hours=9)).isoformat())   # e.g. just woke from sleep
        checks = dict(is_running=lambda n: True, start=lambda: None, now=NOW,
                      started_of=lambda pid: (NOW - timedelta(days=1, minutes=1)).timestamp())
        show_recorder_health(view, self.folder, **checks)
        view.header.set_warning.assert_called_once_with("")
        ms, recheck = view.root.after.call_args.args
        self.assertEqual(ms, RECHECK_MS)
        recheck()                                            # still no new try three minutes later: say so
        self.assertTrue(view.header.set_warning.call_args.args[0].startswith(STUCK_START))

    def test_missing_recorder_is_never_silent(self):
        self.write_beat(pid=5, started_at=NOW.isoformat())
        text = check_recorder(self.folder, None, is_running=lambda n: False, start=lambda: None, now=NOW,
                              started_of=lambda pid: None)
        self.assertIn("Recorder was off", text)


# WHAT IT DOES: R3. Only the real login launch (plain `watch`) may show the login recovery window.
# DEPENDS ON: keeper_watchdog.launch_replacement with Popen patched; keeper_app.main with fakes.
# AFFECTS: Nothing real; no process is started.
class LoginWindowOnlyAtLoginTests(unittest.TestCase):
    def test_every_replacement_recorder_says_no_login_window(self):
        for handover in (True, False):
            with patch.object(keeper_watchdog.subprocess, "Popen") as popen:
                keeper_watchdog.launch_replacement(handover=handover)
            self.assertIn("--no-login-window", popen.call_args.args[0])

    def start_watch(self, *flags):
        recovery = MagicMock(return_value=(Path("saved.json"), {"windows": []}))
        with patch.object(keeper_app.sys, "argv", ["keeper_app.py", "watch", *flags]), \
                patch.object(keeper_app.tk, "Tk", return_value=MagicMock()), \
                patch.object(keeper_app, "make_dpi_aware"), patch.object(keeper_app, "install_ui_guard"), \
                patch.object(keeper_app, "exclusive_operation", lambda name: nullcontext()), \
                patch.object(keeper_app, "DATA", temporary_folder(self)), \
                patch.object(keeper_watchdog, "HANDOVER_WAIT", 0), \
                patch.multiple(keeper_app, auto_restore_enabled=MagicMock(return_value=True),
                               read_boot_id=MagicMock(return_value="boot-2"), startup_recovery=recovery,
                               RecoveryWindow=MagicMock()):
            keeper_app.main()
        return recovery

    def test_restarted_recorder_shows_no_login_window(self):
        self.start_watch("--no-login-window", "--handover").assert_not_called()
        self.start_watch("--no-login-window").assert_not_called()

    def test_real_login_launch_still_offers_recovery(self):
        self.start_watch().assert_called_once()


# WHAT IT DOES: R4. A small layout saved while the login window was open does not hide the good one.
# DEPENDS ON: keeper_history.startup_recovery on a temporary folder of snapshot files.
# AFFECTS: A temporary folder only.
class LoginWindowLayoutTests(unittest.TestCase):
    def setUp(self):
        self.folder = temporary_folder(self)

    def save(self, name, boot, captured, matched, marked=False):
        stamp = datetime.fromisoformat(captured).strftime("%Y%m%dT%H%M%S000000Z")
        snapshot = {"version": 1, "boot_id": boot, "captured_at": captured, "windows": [],
                    "counts": {"matched": matched, "tabs_total": matched}}
        if marked:
            snapshot["login_window_open"] = True
        (self.folder / f"snapshot_{stamp}_{name}.json").write_text(json.dumps(snapshot), encoding="utf-8")

    def test_small_login_window_layout_gives_way_to_the_previous_boot(self):
        self.save("crash", "boot-1", "2026-11-09T22:00:00+00:00", 30)
        self.save("login", "boot-2", "2026-11-10T08:00:00+00:00", 2, marked=True)
        self.assertEqual(startup_recovery("boot-3", self.folder)[1]["boot_id"], "boot-1")

    def test_unmarked_newest_layout_still_wins(self):
        self.save("crash", "boot-1", "2026-11-09T22:00:00+00:00", 30)
        self.save("work", "boot-2", "2026-11-10T08:00:00+00:00", 2)
        self.assertEqual(startup_recovery("boot-3", self.folder)[1]["boot_id"], "boot-2")

    def test_marked_layout_that_is_not_much_smaller_still_wins(self):
        self.save("crash", "boot-1", "2026-11-09T22:00:00+00:00", 30)
        self.save("login", "boot-2", "2026-11-10T08:00:00+00:00", 20, marked=True)
        self.assertEqual(startup_recovery("boot-3", self.folder)[1]["boot_id"], "boot-2")

    def test_capture_is_marked_while_the_login_window_is_open(self):
        snapshot = {"counts": {"matched": 2, "tabs_total": 2}}
        recorder = Recorder(self.folder, pid=1)
        with patch.object(keeper_heartbeat, "ACTIVE", recorder):
            keeper_heartbeat.login_window(True)
            self.assertTrue(keeper_heartbeat.review_capture(snapshot, []))
        self.assertTrue(snapshot["login_window_open"])


if __name__ == "__main__":
    unittest.main()

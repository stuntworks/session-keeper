# Last modified: 2026-10-21 | Change: Tests for one recovery screen at a time | Full history: git log
"""Fake window records and a throwaway mutex name only. Nothing is focused, launched or closed."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from unittest.mock import patch

import keeper_single
from keeper_single import choose_target, claim_recover_screen, pid_alive, try_own

HERE = Path(__file__).resolve().parent


class ChooseTargetTests(unittest.TestCase):
    def test_live_owner_with_its_own_window_is_chosen(self):
        self.assertEqual(choose_target({"pid": 40, "hwnd": 900}, lambda pid: True, lambda hwnd: 40), 900)

    def test_dead_owner_is_stale(self):
        self.assertIsNone(choose_target({"pid": 40, "hwnd": 900}, lambda pid: False, lambda hwnd: 40))

    def test_window_now_belongs_to_another_program(self):
        self.assertIsNone(choose_target({"pid": 40, "hwnd": 900}, lambda pid: True, lambda hwnd: 77))

    def test_closed_window_is_stale(self):
        self.assertIsNone(choose_target({"pid": 40, "hwnd": 900}, lambda pid: True, lambda hwnd: None))

    def test_missing_or_damaged_record(self):
        for info in (None, [], {}, {"pid": "40", "hwnd": 900}, {"pid": 40}, {"pid": 0, "hwnd": 900},
                     {"pid": True, "hwnd": 900}):
            self.assertIsNone(choose_target(info, lambda pid: True, lambda hwnd: 40), info)


# WHAT IT DOES: A child Python process takes the lock; this process must see it as taken, then free.
# DEPENDS ON: Windows named mutexes; a unique test-only name so a real Session Keeper is never touched.
# AFFECTS: The child exits on its own; nothing is killed.
class MutexTests(unittest.TestCase):
    def hold(self, name, seconds, release):
        code = (f"import sys, time; sys.path.insert(0, {str(HERE)!r}); import keeper_single as k; "
                f"h = k.try_own({name!r}); print('owned' if h else 'busy', flush=True); time.sleep({seconds}); "
                + ("k.release(h)" if release else "import os; os._exit(0)"))
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "owned")
        return child

    def test_second_claim_fails_while_first_runs_then_succeeds(self):
        name = f"SessionKeeperTest-{uuid.uuid4().hex}"
        child = self.hold(name, 2, release=True)
        self.assertIsNone(try_own(name))
        child.wait(timeout=20)
        child.stdout.close()
        self.assertIsNotNone(try_own(name))

    def test_crashed_owner_does_not_block(self):
        name = f"SessionKeeperTest-{uuid.uuid4().hex}"
        child = self.hold(name, 1, release=False)
        child.wait(timeout=20)
        child.stdout.close()
        time.sleep(0.2)
        self.assertIsNotNone(try_own(name))

    def test_second_launch_raises_the_published_window_and_stops(self):
        name = f"SessionKeeperTest-{uuid.uuid4().hex}"
        child = self.hold(name, 3, release=True)
        with tempfile.TemporaryDirectory() as folder:
            info = Path(folder) / "recover_window.json"
            info.write_text(json.dumps({"pid": child.pid, "hwnd": 4242}), encoding="utf-8")
            with patch.object(keeper_single, "window_pid", return_value=child.pid),                     patch.object(keeper_single, "bring_to_front") as raised:
                self.assertFalse(claim_recover_screen(name, wait=2, info=info))
            raised.assert_called_once_with(4242)
        child.wait(timeout=20)
        child.stdout.close()

    def test_owner_without_a_window_does_not_block_forever(self):
        name = f"SessionKeeperTest-{uuid.uuid4().hex}"
        child = self.hold(name, 4, release=True)
        with tempfile.TemporaryDirectory() as folder:
            started = time.monotonic()
            self.assertTrue(claim_recover_screen(name, wait=1, info=Path(folder) / "none.json"))
            self.assertLess(time.monotonic() - started, 3)
        child.wait(timeout=20)
        child.stdout.close()

    def test_pid_alive_reads_real_processes(self):
        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(0x7FFFFFF0))


# WHAT IT DOES: The record written for a hidden Tk root names a real window owned by this process.
# DEPENDS ON: A withdrawn Tk root (never shown) and a temporary record path.
# AFFECTS: Nothing on screen.
class PublishTests(unittest.TestCase):
    def test_published_window_is_ours(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with tempfile.TemporaryDirectory() as folder:
            info = Path(folder) / "recover_window.json"
            keeper_single.publish_window(root, info)
            record = keeper_single.read_info(info)
            self.assertEqual(record["pid"], os.getpid())
            self.assertEqual(choose_target(record, pid_alive, keeper_single.window_pid), record["hwnd"])


# WHAT IT DOES: The recover entry point stops before building any window when another screen is open.
# DEPENDS ON: Patched argv, lock and Tk; nothing real is opened.
# AFFECTS: Guards against a double-click opening a second recovery screen.
class AppHookTests(unittest.TestCase):
    def test_second_recover_launch_builds_no_window(self):
        import keeper_app
        with patch.object(keeper_app.sys, "argv", ["keeper_app.py", "recover"]),                 patch.object(keeper_app, "claim_recover_screen", return_value=False),                 patch.object(keeper_app.tk, "Tk") as tk_root:
            keeper_app.main()
        tk_root.assert_not_called()

    def test_watch_mode_never_takes_the_screen_lock(self):
        import keeper_app
        with patch.object(keeper_app.sys, "argv", ["keeper_app.py", "watch"]),                 patch.object(keeper_app, "claim_recover_screen") as claim,                 patch.object(keeper_app.tk, "Tk"), patch.object(keeper_app, "make_dpi_aware"),                 patch.object(keeper_app, "auto_restore_enabled", return_value=False),                 patch.object(keeper_app, "exclusive_operation"):
            keeper_app.main()
        claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()

# Last modified: 2026-11-03 | Change: New. Tests for the UI error and freeze logs | Full history: git log
"""UI guard: Tk callback errors and window stalls leave evidence in the data folder's logs."""

import shutil
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path

from unittest.mock import MagicMock

from keeper_uiguard import ERRORS_LOG, FREEZE_LOG, UiGuard, cap_log, install
from tk_test_support import free_tk_garbage


def tearDownModule():
    free_tk_garbage()


class UiGuardTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="keeper_ug_"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.destroy_root)

    def destroy_root(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass                                            # a test already closed it

    def guard(self, **kw):
        guard = UiGuard(self.root, self.dir, **kw)
        self.addCleanup(guard.stop)
        return guard

    def test_callback_exception_lands_in_error_log(self):
        self.guard(crash_dumps=False)

        def boom():
            raise ValueError("hover exploded")
        self.root.after(10, boom)
        self.root.after(200, self.root.quit)
        self.root.mainloop()
        text = (self.dir / ERRORS_LOG).read_text(encoding="utf-8")
        self.assertIn("ValueError: hover exploded", text)
        self.assertIn("Traceback", text)

    def test_stall_longer_than_three_seconds_writes_freeze_entry(self):
        self.guard(crash_dumps=False)
        self.root.after(300, lambda: time.sleep(3.5))      # the Tk thread is stuck
        self.root.after(5200, self.root.quit)
        self.root.mainloop()
        text = (self.dir / FREEZE_LOG).read_text(encoding="utf-8")
        self.assertIn("window not answering", text)
        self.assertIn("Thread", text)
        self.assertIn("test_keeper_uiguard.py", text, "the stuck Tk thread's own stack must be in the dump")
        self.assertEqual(text.count("window not answering"), 1, "one stall, one entry")

    def test_healthy_window_writes_no_freeze_entry(self):
        self.guard(crash_dumps=False)
        self.root.after(1500, self.root.quit)
        self.root.mainloop()
        self.assertFalse((self.dir / FREEZE_LOG).exists())

    def test_destroyed_window_is_never_reported_frozen(self):
        guard = self.guard(crash_dumps=False)
        self.root.update()
        self.root.destroy()
        self.assertTrue(guard.stopped.is_set())
        time.sleep(3.8)                                     # no beats any more, but nobody is watching
        self.assertFalse((self.dir / FREEZE_LOG).exists())
        guard.thread.join(2)
        self.assertFalse(guard.thread.is_alive())

    def test_stopping_with_nothing_logged_leaves_no_empty_file(self):
        guard = self.guard()                                # crash dumps on: opens the freeze log
        guard.stop()
        self.assertFalse((self.dir / FREEZE_LOG).exists())

    def test_install_ignores_a_stand_in_root(self):
        self.assertIsNone(install(MagicMock(), self.dir))
        self.assertFalse(self.dir.joinpath(FREEZE_LOG).exists())

    def test_dumps_are_at_most_once_per_30_seconds(self):
        guard = self.guard(crash_dumps=False)
        guard.last_beat = 100.0
        self.assertTrue(guard.due(104.0))
        guard.last_dump = 104.0
        self.assertFalse(guard.due(120.0))
        self.assertTrue(guard.due(134.5))

    def test_log_is_cut_to_newest_half_when_over_cap(self):
        path = self.dir / "x.log"
        lines = [f"line {n:05d}\n" for n in range(1000)]
        path.write_text("".join(lines), encoding="utf-8")
        size = path.stat().st_size
        cap_log(path, limit=size // 2)
        kept = path.read_text(encoding="utf-8")
        self.assertLessEqual(len(kept.encode()), size // 4 + 20)
        self.assertTrue(kept.endswith("line 00999\n"), "newest lines survive")
        self.assertTrue(kept.startswith("line "), "cut on a line boundary")
        self.assertNotIn("line 00000", kept)

    def test_small_log_is_left_alone(self):
        path = self.dir / "y.log"
        path.write_text("hello\n", encoding="utf-8")
        cap_log(path, limit=1000)
        self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")

    def test_error_log_cap_applies_when_writing(self):
        guard = self.guard(crash_dumps=False, limit=2000)
        for _ in range(60):
            guard.append(guard.errors, "x" * 100 + "\n")
        self.assertLess((self.dir / ERRORS_LOG).stat().st_size, 2200)


if __name__ == "__main__":
    unittest.main()

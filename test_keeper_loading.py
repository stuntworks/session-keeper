# Last modified: 2026-10-21 | Change: Tests for the loading-window hand-off | Full history: git log
"""Pure-logic tests: result goes to the main thread, errors reach the window."""

import threading
import time
import unittest

from keeper_loading import load_then_show


class FakeRoot:
    def __init__(self):
        self.jobs = []

    def after(self, ms, fn):
        self.jobs.append(fn)

    def pump(self, seconds=3):
        end = time.time() + seconds
        while self.jobs and time.time() < end:
            self.jobs.pop(0)()
            time.sleep(0.01)


class FakeWindow:
    def __init__(self):
        self.events = []

    def show_error(self, text):
        self.events.append(("error", text))

    def close(self):
        self.events.append(("close",))


class LoadThenShowTests(unittest.TestCase):
    def test_loader_runs_off_main_thread_and_callback_on_main(self):
        root, window, seen = FakeRoot(), FakeWindow(), {}
        main = threading.get_ident()

        def loader():
            seen["loader"] = threading.get_ident()
            return 42

        load_then_show(root, window, loader, lambda r: seen.update(ready=(r, threading.get_ident())))
        root.pump()
        self.assertNotEqual(seen["loader"], main)
        self.assertEqual(seen["ready"], (42, main))
        self.assertEqual(window.events, [("close",)])

    def test_loader_error_shown_and_window_kept(self):
        root, window = FakeRoot(), FakeWindow()
        called = []

        def loader():
            raise ValueError("bad snapshot")

        load_then_show(root, window, loader, called.append)
        root.pump()
        self.assertEqual(called, [])
        self.assertEqual(len(window.events), 1)
        self.assertEqual(window.events[0][0], "error")
        self.assertIn("bad snapshot", window.events[0][1])

    def test_screen_build_error_shown_not_closed(self):
        root, window = FakeRoot(), FakeWindow()

        def ready(result):
            raise RuntimeError("cannot build screen")

        load_then_show(root, window, lambda: 1, ready)
        root.pump()
        self.assertEqual([e[0] for e in window.events], ["error"])
        self.assertIn("cannot build screen", window.events[0][1])


if __name__ == "__main__":
    unittest.main()

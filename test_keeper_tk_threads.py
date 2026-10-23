# Last modified: 2026-10-22 | Change: Regression test for Tk objects freed on a worker thread (Tcl_AsyncDelete crash) | Full history: git log
"""Proves the crash the suite used to hit at random, and that freeing Tk garbage on the main thread stops it.

Each case runs in its own Python process, because the failure aborts the whole interpreter."""

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

from tk_test_support import running_workers

HERE = Path(__file__).resolve().parent

# A destroyed root still tied up in a reference cycle (like RecoveryWindow <-> root), then a
# worker thread that happens to run the garbage collector.
SCENARIO = textwrap.dedent("""
    import gc, sys, threading, tkinter as tk
    sys.path.insert(0, {here!r})
    from tk_test_support import free_tk_garbage

    class Screen:
        def __init__(self, root):
            self.root = root
            root._screen = self
            self.choice = tk.BooleanVar(root, value=True)

    def make_and_close_root():
        root = tk.Tk()
        root.withdraw()
        Screen(root)
        root.destroy()

    make_and_close_root()
    if {free_first}:
        free_tk_garbage()
    worker = threading.Thread(target=gc.collect)
    worker.start()
    worker.join()
    print("clean exit")
""")


# WHAT IT DOES: Runs SCENARIO in a fresh Python process and returns (exit code, all output).
# DEPENDS ON: tk_test_support.free_tk_garbage and a working Tk install.
# AFFECTS: Test only; starts and ends one child process.
def run_scenario(free_first):
    code = SCENARIO.format(here=str(HERE), free_first=free_first)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    return done.returncode, done.stdout + done.stderr


# WHAT IT DOES: Guards the rule that Tk objects are only ever freed on the main thread.
# DEPENDS ON: tk_test_support.
# AFFECTS: Test run only.
class TkFreedOnMainThreadTests(unittest.TestCase):
    def test_worker_collecting_a_closed_root_crashes_the_process(self):
        returncode, output = run_scenario(free_first=False)
        self.assertNotEqual(returncode, 0, output)
        self.assertIn("Tcl_AsyncDelete", output)

    def test_freeing_tk_garbage_on_the_main_thread_first_prevents_it(self):
        returncode, output = run_scenario(free_first=True)
        self.assertEqual(returncode, 0, output)
        self.assertIn("clean exit", output)
        self.assertNotIn("main thread is not in main loop", output)

    def test_no_background_worker_is_running_between_tests(self):
        self.assertEqual(running_workers(), [])


if __name__ == "__main__":
    unittest.main()

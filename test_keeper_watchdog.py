# Last modified: 2026-11-10 | Change: R5: a Tk thread that stops ticking between captures also fires; sleep does not | Full history: git log
"""The background recorder must never stop silently: a stuck capture is logged and the recorder restarts."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import keeper_core
from keeper_watchdog import RESTART_FILE, WATCH_LOG, CaptureWatchdog, watch_lock


# WHAT IT DOES: A clock the test moves by hand.
# DEPENDS ON: Nothing.
# AFFECTS: Lets a 10-minute hang be tested in microseconds.
class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


# WHAT IT DOES: Drives CaptureWatchdog.check with fake clocks, a fake restart and a fake exit.
# DEPENDS ON: A temporary data folder (never the real data folder).
# AFFECTS: Nothing outside the temporary folder.
class CaptureWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="keeper_wd_"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.clock = FakeClock()
        self.wall = FakeClock(1_800_000_000.0)
        self.restarts = []
        self.exits = []

    # Tests that jump the clock far between looks turn sleep detection off unless they test it.
    def watchdog(self, **kw):
        kw.setdefault("sleep_gap", 10 ** 9)
        return CaptureWatchdog(self.dir, limit=600, clock=self.clock, wall=self.wall,
                               restart=lambda: self.restarts.append(1),
                               hard_exit=lambda: self.exits.append(1), **kw)

    def log_text(self):
        path = self.dir / WATCH_LOG
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_stuck_capture_logs_stacks_and_restarts_once(self):
        dog = self.watchdog()
        dog.capture_started()
        self.clock.now += 601
        self.assertTrue(dog.check())
        self.assertFalse(dog.check())                      # a second check must not restart again
        self.clock.now += 601
        self.assertFalse(dog.check())
        self.assertEqual(self.restarts, [1])
        self.assertEqual(self.exits, [1])
        text = self.log_text()
        self.assertIn("capture stuck", text)
        self.assertIn("thread 0x", text)                   # faulthandler's all-thread stack dump
        self.assertIn("in check", text)
        self.assertIn("restarting", text)
        self.assertEqual(len(json.loads((self.dir / RESTART_FILE).read_text())), 1)

    def test_capture_that_finishes_in_time_triggers_nothing(self):
        dog = self.watchdog()
        dog.capture_started()
        self.clock.now += 30
        self.assertFalse(dog.check())
        dog.capture_done()
        self.clock.now += 120                              # the next tick comes on time
        dog.tick()
        self.assertFalse(dog.check())
        self.assertEqual((self.restarts, self.exits, self.log_text()), ([], [], ""))

    def test_idle_while_the_loop_ticks_triggers_nothing(self):
        dog = self.watchdog(idle_limit=720)
        for _ in range(100):                               # the loop keeps ticking (skipped ticks count too)
            self.clock.now += 120
            dog.tick()
            self.assertFalse(dog.check())
        self.assertEqual((self.restarts, self.exits, self.log_text()), ([], [], ""))

    def test_loop_that_stops_ticking_between_captures_restarts(self):
        dog = self.watchdog(idle_limit=720)
        dog.capture_started()
        dog.capture_done()                                 # last capture finished; then the Tk thread hangs
        self.clock.now += 700
        self.assertFalse(dog.check())
        self.clock.now += 30
        self.assertTrue(dog.check())
        self.assertEqual((self.restarts, self.exits), ([1], [1]))
        self.assertIn("no capture started (recorder loop hung)", self.log_text())

    def test_sleep_gap_is_not_a_hang(self):
        dog = self.watchdog(idle_limit=720, sleep_gap=120)
        self.clock.now += 30
        self.assertFalse(dog.check())
        self.clock.now += 8 * 3600                         # the PC slept overnight: no checks ran
        self.assertFalse(dog.check())
        self.clock.now += 30
        self.assertFalse(dog.check())
        self.assertEqual((self.restarts, self.exits, self.log_text()), ([], [], ""))

    def test_hook_reaches_the_active_watchdog(self):
        import keeper_watchdog
        dog = self.watchdog(idle_limit=720)
        self.clock.now += 700
        with unittest.mock.patch.object(keeper_watchdog, "ACTIVE", dog):
            keeper_watchdog.tick()
        self.clock.now += 100
        self.assertFalse(dog.check())

    def test_restart_cap_holds(self):
        recent = [self.wall.now - 60 * minutes for minutes in (5, 20, 50)]
        (self.dir / RESTART_FILE).write_text(json.dumps(recent))
        dog = self.watchdog(max_restarts=3)
        dog.capture_started()
        self.clock.now += 601
        self.assertFalse(dog.check())
        self.assertEqual((self.restarts, self.exits), ([], []))
        self.assertIn("not restarting", self.log_text())
        self.assertEqual(len(json.loads((self.dir / RESTART_FILE).read_text())), 3)

    def test_old_restarts_do_not_count_against_the_cap(self):
        old = [self.wall.now - 3700 - minute for minute in range(5)]
        (self.dir / RESTART_FILE).write_text(json.dumps(old))
        dog = self.watchdog(max_restarts=3)
        dog.capture_started()
        self.clock.now += 601
        self.assertTrue(dog.check())
        self.assertEqual(self.restarts, [1])
        self.assertEqual(len(json.loads((self.dir / RESTART_FILE).read_text())), 1)

    def test_capped_watchdog_logs_again_only_after_another_limit(self):
        recent = [self.wall.now - 60 for _ in range(3)]
        (self.dir / RESTART_FILE).write_text(json.dumps(recent))
        dog = self.watchdog(max_restarts=3)
        dog.capture_started()
        self.clock.now += 601
        dog.check()
        first = self.log_text().count("not restarting")
        self.clock.now += 30
        dog.check()
        self.assertEqual(self.log_text().count("not restarting"), first)
        self.clock.now += 600
        self.wall.now += 3700                              # the hour has passed: a restart is allowed again
        self.assertTrue(dog.check())
        self.assertEqual(self.restarts, [1])


# WHAT IT DOES: The replacement recorder waits for the stuck one to let go of the watch lock.
# DEPENDS ON: A fake lock that is busy for the first few tries.
# AFFECTS: Nothing; no real mutex is taken.
class WatchLockTests(unittest.TestCase):
    def fake_lock(self, busy_tries):
        tries = []

        class Lock:
            def __enter__(self):
                tries.append(1)
                if len(tries) <= busy_tries:
                    raise RuntimeError("Session Keeper watch is already running")

            def __exit__(self, *exc):
                return False

        return tries, lambda name: Lock()

    def test_waits_for_the_old_recorder_to_exit(self):
        clock = FakeClock()
        tries, lock = self.fake_lock(busy_tries=3)
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock.now += seconds

        with watch_lock(30, lock=lock, clock=clock, sleep=sleep):
            pass
        self.assertEqual(len(tries), 4)

    def test_gives_up_after_the_wait(self):
        clock = FakeClock()
        tries, lock = self.fake_lock(busy_tries=10_000)

        def sleep(seconds):
            clock.now += seconds

        with self.assertRaises(RuntimeError):
            with watch_lock(5, lock=lock, clock=clock, sleep=sleep):
                pass

    def test_no_wait_fails_at_once_like_before(self):
        tries, lock = self.fake_lock(busy_tries=1)
        with self.assertRaises(RuntimeError):
            with watch_lock(0, lock=lock, sleep=lambda s: self.fail("must not sleep")):
                pass
        self.assertEqual(len(tries), 1)


# The child ignores the timeout and starts a grandchild that holds stdout/stderr for 20 s. With
# subprocess.run(capture_output=True) the post-timeout communicate() waits for that grandchild.
CHILD = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'],"
    " stdout=sys.stdout, stderr=sys.stderr)\n"
    "print('started', flush=True)\n"
    "time.sleep(20)\n"
)


# WHAT IT DOES: run_hidden must return or raise soon after its timeout, even when a grandchild holds the output.
# DEPENDS ON: A real short Python child (no PowerShell, no 45 s wait).
# AFFECTS: Leaves one sleeping grandchild that exits by itself within 20 s.
class BoundedRunTests(unittest.TestCase):
    def test_timeout_is_bounded_when_a_grandchild_holds_the_output(self):
        began = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            keeper_core.run_hidden([sys.executable, "-c", CHILD], timeout=2)
        self.assertLess(time.monotonic() - began, 10)

    def test_normal_run_returns_text_output(self):
        result = keeper_core.run_hidden([sys.executable, "-c", "import sys; print('hi'); print('err', file=sys.stderr)"],
                                        timeout=30)
        self.assertIsInstance(result, subprocess.CompletedProcess)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "hi\n", "err\n"))


if __name__ == "__main__":
    unittest.main()

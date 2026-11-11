# Last modified: 2026-11-10 | Change: Also fires when the Tk thread stops scheduling captures; replacements never show the login window | Full history: git log
"""Watchdog for `keeper_app.py watch`: a capture that never returns must not stop recording forever.

The next capture is only scheduled when the previous one reports back, so one hung call used to stop
the recorder silently while the process stayed alive. When a capture has been running longer than
STUCK_LIMIT seconds this writes every thread's stack to watcher_errors.log (evidence of WHERE it hung),
starts a fresh recorder and hard-exits this one. A hung thread cannot be killed; a new process can.
Restarts are capped (MAX_RESTARTS per RESTART_WINDOW) so a fault that hangs every capture cannot
turn into a restart storm.
"""

import ctypes
import faulthandler
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import tkinter as tk

from keeper_uiguard import cap_log, stamp

WATCH_LOG = "watcher_errors.log"
RESTART_FILE = "watch_restarts.json"
STUCK_LIMIT = 600            # seconds; a normal capture takes seconds and PowerShell gives up after 45
CHECK_EVERY = 30             # seconds between looks
MAX_RESTARTS = 3
RESTART_WINDOW = 3600        # seconds
INTERVAL = 120              # seconds between captures (keeper_app.INTERVAL_MS)
IDLE_LIMIT = INTERVAL + STUCK_LIMIT   # no capture STARTED (or deliberately skipped) for this long = Tk thread hung
SLEEP_GAP = CHECK_EVERY * 4  # a gap this long between two looks means the PC slept: start counting again
HANDOVER_WAIT = 60           # seconds the replacement waits for the stuck recorder to let go of the lock
APP = Path(__file__).with_name("keeper_app.py")

ACTIVE = None                # the running watchdog in a watch process; None everywhere else


# WHAT IT DOES: The windowless interpreter beside the current one, so the replacement never shows a console.
# DEPENDS ON: sys.executable.
# AFFECTS: Returns a path only.
def windowless_python():
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


# WHAT IT DOES: Starts a fresh `keeper_app.py watch --handover`, hidden and detached from this process.
# DEPENDS ON: keeper_app.py beside this file; --handover makes it wait for this process's watch lock.
# handover=False (the recovery screen starting a recorder that is not running) takes the lock at once.
# AFFECTS: One new background process. --no-login-window: only the real login launch (Startup shortcut,
# plain `watch`) may show the login recovery window, never a restarted or screen-started recorder.
def launch_replacement(handover=True):
    subprocess.Popen([windowless_python(), str(APP), "watch", "--no-login-window",
                      *(["--handover"] if handover else [])],
                     cwd=str(APP.parent),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)


# WHAT IT DOES: Ends this process at once, hung threads included. Windows releases its named mutexes.
# DEPENDS ON: kernel32.TerminateProcess; os._exit if that is unavailable.
# AFFECTS: Kills the stuck recorder. Never returns.
# DANGER ZONE HIGH: Only the watchdog may call this / breaks: the recorder / depends on: a replacement
# having been launched first.
def hard_exit(code=3):
    try:
        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.TerminateProcess(kernel.GetCurrentProcess(), code)
    finally:
        os._exit(code)


# WHAT IT DOES: Takes the watch lock, retrying for up to wait_seconds while an old recorder is still exiting.
# DEPENDS ON: keeper_locks.exclusive_operation (or a stand-in for tests).
# AFFECTS: wait_seconds=0 behaves exactly like exclusive_operation("watch"): busy means RuntimeError at once.
@contextmanager
def watch_lock(wait_seconds, lock=None, clock=time.monotonic, sleep=time.sleep):
    if lock is None:
        from keeper_locks import exclusive_operation as lock
    deadline = clock() + wait_seconds
    with ExitStack() as stack:
        while True:
            try:
                stack.enter_context(lock("watch"))
                break
            except RuntimeError:
                if clock() >= deadline:
                    raise
                sleep(0.5)
        yield


# WHAT IT DOES: Notices a capture that has run too long; logs all stacks, then restarts the recorder.
# DEPENDS ON: capture_started / capture_done called from the Tk thread; check() called from its own thread.
# AFFECTS: watcher_errors.log, watch_restarts.json, and (through restart/hard_exit) the process itself.
# Never touches Tk. It also fires when the Tk thread stops ticking (no capture started or deliberately
# skipped for idle_limit seconds): a hang BETWEEN captures used to go unnoticed.
# DANGER ZONE FRAGILE: The PC sleeping looks like a hang / breaks: a needless restart after every sleep /
# depends on: check() running every CHECK_EVERY seconds, so a much longer gap between looks means sleep.
class CaptureWatchdog:
    def __init__(self, data_dir, limit=STUCK_LIMIT, max_restarts=MAX_RESTARTS, window=RESTART_WINDOW,
                 clock=time.monotonic, wall=time.time, restart=launch_replacement, hard_exit=hard_exit,
                 idle_limit=IDLE_LIMIT, sleep_gap=SLEEP_GAP):
        self.data_dir = Path(data_dir)
        self.limit, self.max_restarts, self.window = limit, max_restarts, window
        self.idle_limit, self.sleep_gap = idle_limit, sleep_gap
        self.clock, self.wall, self.restart, self.hard_exit = clock, wall, restart, hard_exit
        self.guard = threading.Lock()
        self.started_at = None
        self.ticked_at = clock()        # the watch loop's last tick (a capture start or a deliberate skip)
        self.looked_at = None
        self.fired = False

    # WHAT IT DOES: The watch loop is alive (called on every tick, including ticks it deliberately skips).
    def tick(self):
        with self.guard:
            self.ticked_at = self.clock()

    def capture_started(self):
        with self.guard:
            self.started_at = self.ticked_at = self.clock()

    def capture_done(self):
        with self.guard:
            self.started_at = None

    # WHAT IT DOES: One look. True only when it has just launched a replacement and asked this process to exit.
    def check(self):
        with self.guard:
            now = self.clock()
            slept = self.looked_at is not None and now - self.looked_at > self.sleep_gap
            self.looked_at = now
            if slept:                            # the PC was asleep: nothing hung, count from now
                self.ticked_at = now
                if self.started_at is not None:
                    self.started_at = now
            if self.fired:
                return False
            if self.started_at is not None:
                stuck, what = now - self.started_at, "capture stuck"
                if stuck <= self.limit:
                    return False
            else:
                stuck, what = now - self.ticked_at, "no capture started (recorder loop hung)"
                if stuck <= self.idle_limit:
                    return False
        count = self.claim_restart()
        self.log(stuck, count, what)
        if count is None:
            self.rearm()                         # capped: look again after another full limit
            return False
        self.fired = True
        try:
            self.restart()
        except Exception as error:               # no replacement: staying alive beats leaving nothing running
            self.append(f"{stamp()} could not start a replacement recorder: {error}\n")
            self.fired = False
            self.rearm()
            return False
        self.hard_exit()
        return True

    def rearm(self):
        with self.guard:
            self.ticked_at = self.clock()
            if self.started_at is not None:
                self.started_at = self.clock()

    # WHAT IT DOES: Records a restart if fewer than max_restarts happened in the last window.
    # AFFECTS: watch_restarts.json. Returns this restart's number, or None when the cap is reached.
    def claim_restart(self):
        path = self.data_dir / RESTART_FILE
        now = self.wall()
        try:
            times = [float(value) for value in json.loads(path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            times = []
        times = [value for value in times if now - value < self.window]
        if len(times) >= self.max_restarts:
            return None
        times.append(now)
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(times), encoding="utf-8")
        except OSError:
            pass
        return len(times)

    # WHAT IT DOES: Appends the stuck-capture line, every thread's stack, and what happens next.
    def log(self, stuck, count, what="capture stuck"):
        if count is None:
            outcome = (f"not restarting: {self.max_restarts} restarts in the last {self.window // 60} min already;"
                       f" looking again in {self.limit // 60} min")
        else:
            outcome = f"restarting the recorder (restart {count} of {self.max_restarts} allowed per hour)"
        path = self.data_dir / WATCH_LOG
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            cap_log(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"\n=== {stamp()} {what} for {stuck:.0f}s; all thread stacks ===\n")
                handle.flush()
                faulthandler.dump_traceback(file=handle, all_threads=True)
                handle.write(f"{stamp()} {outcome}\n")
        except (OSError, ValueError):
            pass

    def append(self, text):
        try:
            with open(self.data_dir / WATCH_LOG, "a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass


# WHAT IT DOES: Starts the watchdog thread for this watch process.
# DEPENDS ON: Called once from keeper_app's watch branch, after the watch lock is held.
# AFFECTS: One daemon thread; sets ACTIVE so capture_started/capture_done reach it. A stand-in root
# (tests) starts nothing, so a test process can never be restarted or exited by it.
def start(root, data_dir):
    global ACTIVE
    if not isinstance(root, tk.Misc):
        return None
    ACTIVE = CaptureWatchdog(data_dir)

    def loop():
        while True:
            time.sleep(CHECK_EVERY)
            try:
                ACTIVE.check()
            except Exception:                    # the watchdog itself must never die
                pass

    threading.Thread(target=loop, name="keeper-capture-watchdog", daemon=True).start()
    return ACTIVE


# WHAT IT DOES: Tk thread hooks around one capture. No-ops outside a watch process.
def tick():
    if ACTIVE is not None:
        ACTIVE.tick()


def capture_started():
    if ACTIVE is not None:
        ACTIVE.capture_started()


def capture_done():
    if ACTIVE is not None:
        ACTIVE.capture_done()

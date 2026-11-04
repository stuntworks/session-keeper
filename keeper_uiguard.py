# Last modified: 2026-11-03 | Change: New. Logs UI errors and freezes to data so a silent pythonw crash leaves evidence | Full history: git log
"""Safety net for the windows: the app runs under pythonw, so every error and freeze used to vanish.

Writes two small logs next to the snapshots (the data folder):
  ui_errors.log  every exception raised inside a Tk callback, with its traceback.
  ui_freeze.log  a timestamped stack of every thread when the window stopped answering for
                 more than 3 seconds (at most once per 30 seconds), plus hard-crash dumps.
Each log is cut to its newest half when it passes about 1 MB.
"""

import faulthandler
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path

ERRORS_LOG = "ui_errors.log"
FREEZE_LOG = "ui_freeze.log"
MAX_BYTES = 1_000_000
BEAT_MS = 250
STALL_SECONDS = 3.0
DUMP_EVERY = 30.0


# WHAT IT DOES: Keeps a log under about `limit` bytes by dropping its oldest half (whole lines only).
# DEPENDS ON: Nothing. A missing or unreadable file is left alone.
# AFFECTS: Rewrites the file in place (other open handles on it keep working).
def cap_log(path, limit=MAX_BYTES):
    try:
        path = Path(path)
        if path.stat().st_size <= limit:
            return
        with open(path, "r+b") as handle:
            handle.seek(-(limit // 2), 2)
            tail = handle.read()
            cut = tail.find(b"\n")
            tail = tail[cut + 1:] if cut >= 0 else tail
            handle.seek(0)
            handle.write(tail)
            handle.truncate()
    except OSError:
        pass


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# WHAT IT DOES: Watches one Tk root: logs callback exceptions and any stall of its main loop.
# DEPENDS ON: A Tk root running its main loop on the thread that created this object.
# AFFECTS: Appends to the two logs in data_dir. The watchdog thread NEVER touches Tk: it only reads a
# timestamp the Tk thread writes every 250 ms.
class UiGuard:
    def __init__(self, root, data_dir, stall=STALL_SECONDS, every=DUMP_EVERY, limit=MAX_BYTES, crash_dumps=True):
        self.root, self.stall, self.every, self.limit = root, stall, every, limit
        self.errors, self.freeze = Path(data_dir) / ERRORS_LOG, Path(data_dir) / FREEZE_LOG
        self.last_beat = time.monotonic()
        self.last_dump = None
        self.stopped = threading.Event()
        self.crash_file = None
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        cap_log(self.errors, limit)
        cap_log(self.freeze, limit)
        root.report_callback_exception = self.on_exception
        root.bind("<Destroy>", self.on_destroy, add="+")
        if crash_dumps:
            try:
                self.crash_file = open(self.freeze, "a", encoding="utf-8")
                faulthandler.enable(file=self.crash_file, all_threads=True)
            except (OSError, RuntimeError, ValueError):
                self.crash_file = None
        self.beat()
        self.thread = threading.Thread(target=self.watch, name="keeper-ui-watchdog", daemon=True)
        self.thread.start()

    # WHAT IT DOES: Tk callback exception: appends the traceback to ui_errors.log.
    def on_exception(self, kind, value, trace):
        text = "".join(traceback.format_exception(kind, value, trace))
        self.append(self.errors, f"=== {stamp()} exception in a Tk callback ===\n{text}\n")
        if sys.__stderr__ is not None:
            try:
                sys.__stderr__.write(text)
            except (OSError, ValueError):
                pass

    # WHAT IT DOES: The root window is closing: stop watching, so a closed window is never reported as frozen.
    def on_destroy(self, event):
        if event.widget is self.root:
            self.stop()

    # WHAT IT DOES: Runs on the Tk thread every 250 ms; the proof that the window is still answering.
    def beat(self):
        if self.stopped.is_set():
            return
        self.last_beat = time.monotonic()
        try:
            self.root.after(BEAT_MS, self.beat)
        except Exception:                        # the root was destroyed: stop quietly
            self.stopped.set()

    # WHAT IT DOES: Watchdog thread: when no beat for `stall` seconds, writes every thread's stack.
    # AFFECTS: ui_freeze.log only. Pure Python and file access; never calls Tk.
    def watch(self):
        while not self.stopped.wait(0.5):
            if self.due(time.monotonic()):
                self.dump()

    def due(self, now):
        if now - self.last_beat <= self.stall:
            return False
        return self.last_dump is None or now - self.last_dump >= self.every

    def dump(self):
        self.last_dump = time.monotonic()
        silent = self.last_dump - self.last_beat
        cap_log(self.freeze, self.limit)
        try:
            with open(self.freeze, "a", encoding="utf-8") as handle:
                handle.write(f"\n=== {stamp()} window not answering for {silent:.1f}s; all thread stacks ===\n")
                handle.flush()
                faulthandler.dump_traceback(file=handle, all_threads=True)
        except OSError:
            pass

    def append(self, path, text):
        try:
            cap_log(path, self.limit)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass

    # WHAT IT DOES: Stops the watchdog and heartbeat and releases the crash-dump file (tests, shutdown).
    def stop(self):
        self.stopped.set()
        if self.crash_file is not None:
            try:
                faulthandler.disable()
                self.crash_file.close()
                if self.freeze.stat().st_size == 0:      # nothing was ever logged: leave no empty file behind
                    self.freeze.unlink()
            except (OSError, ValueError):
                pass
            self.crash_file = None


# WHAT IT DOES: Turns the safety net on for this root. Never raises: a broken log folder must not stop the app.
# DEPENDS ON: Called once after tk.Tk() on the Tk thread.
# AFFECTS: root.report_callback_exception, one daemon thread, the two logs.
def install(root, data_dir):
    if not isinstance(root, tk.Misc):             # a stand-in root (tests) has nothing to watch
        return None
    try:
        return UiGuard(root, data_dir)
    except Exception:
        return None

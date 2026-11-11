# Last modified: 2026-11-10 | Change: A sharp matched drop is remembered (drop_seen) so the crash record is saved soon | Full history: git log
"""What the background recorder (`keeper_app.py watch`) tells the rest of the app about itself.

After every capture attempt it writes data\\watch_heartbeat.json (when it last tried, when it last saved,
the last error), so the recovery screen can say when recording has stopped. It also writes ONE log line
to watcher_errors.log when something about the captures CHANGES (processes it could not read, nothing
matched, matching suddenly collapsed), never the same line every two minutes.
Read side: keeper_recorder_health.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from keeper_uiguard import cap_log, stamp

HEARTBEAT_FILE = "watch_heartbeat.json"
WATCH_LOG = "watcher_errors.log"
DROP = 0.5                  # matched share falling by this much (e.g. 33/35 -> 3/35) is logged
MIN_DROP_TABS = 3           # ...and only when at least this many matched tabs were lost

ACTIVE = None               # the Recorder of a running watch process; None everywhere else (tests, recover)
BUSY_TEXT = "is already running"   # keeper_locks' refusal while a restore holds the lock: waiting, not broken


# WHAT IT DOES: Appends one stamped line to the recorder's log. Never raises.
# DEPENDS ON: A data folder that may or may not exist or be writable.
# AFFECTS: watcher_errors.log (kept small by cap_log).
# DANGER ZONE HIGH: Must never raise / breaks: the capture chain (a failed log write used to end it) /
# depends on: callers not wrapping it themselves.
def log_line(data_dir, text):
    try:
        path = Path(data_dir) / WATCH_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        cap_log(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp()} {text}\n")
    except Exception:
        pass


# WHAT IT DOES: Reads the heartbeat file; missing or damaged reads as None.
# DEPENDS ON: The file written by Recorder.attempt_finished.
# AFFECTS: Nothing.
def read_heartbeat(data_dir):
    try:
        data = json.loads((Path(data_dir) / HEARTBEAT_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# WHAT IT DOES: Writes the heartbeat atomically (temp file, then replace). Never raises.
# DEPENDS ON: A writable data folder.
# AFFECTS: data\watch_heartbeat.json only.
def write_heartbeat(data_dir, record):
    try:
        path = Path(data_dir) / HEARTBEAT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        pass


# WHAT IT DOES: Remembers the last capture's results so only CHANGES are logged, and writes the heartbeat.
# DEPENDS ON: review() on the capture worker thread, attempt_finished() on the Tk thread afterwards. Only one
# capture is in flight at a time, so the two never overlap.
# AFFECTS: watcher_errors.log and watch_heartbeat.json in data_dir.
class Recorder:
    def __init__(self, data_dir, wall=time.time, pid=None):
        self.data_dir = Path(data_dir)
        self.wall = wall
        self.pid = pid if pid is not None else os.getpid()
        self.previous = None        # (matched, total) of the last capture that saw any tab
        self.skipping = False       # True while captures are dropped because nothing matched
        self.unreadable = ""        # the last "could not read" text that was logged
        self.note = None            # why the last capture was not saved, for the heartbeat
        self.started_at = self.stamp()
        self.login_window = False   # set on the Tk thread before each capture (keeper_app.watch)
        self.drop_seen = None       # wall time of a sharp matched drop; keeper_crash_record.after_capture

    def stamp(self):
        return datetime.fromtimestamp(self.wall()).astimezone().isoformat()

    # WHAT IT DOES: The first heartbeat, written when the recorder starts, so the recovery screen never
    # judges this recorder by a file left by the previous boot's recorder or by one from before a sleep.
    # AFFECTS: watch_heartbeat.json; last_saved carries over (it is about the saved layouts, not the process).
    def begin(self):
        old = read_heartbeat(self.data_dir) or {}
        write_heartbeat(self.data_dir, {
            "pid": self.pid, "started_at": self.started_at, "last_attempt": None,
            "last_saved": old.get("last_saved"), "last_error": None, "note": None, "problem_since": None})

    def log(self, text):
        log_line(self.data_dir, text)

    # WHAT IT DOES: Logs the processes this capture could not read, only when the list changed.
    # AFFECTS: One line naming every skipped process; one line when they can all be read again.
    def processes_skipped(self, skipped):
        text = "; ".join(dict.fromkeys(skipped))
        if text == self.unreadable:
            return
        if text:
            self.log(f"capture: could not read {len(skipped)} process(es); each was left out or marked "
                     f"unidentified and the capture went on: {text}")
        else:
            self.log("capture: every Claude/Codex process can be read again")
        self.unreadable = text

    # WHAT IT DOES: Decides whether a capture is saved (only when a tab matched, as before) and logs the
    # moment captures start being dropped, start again, or matching collapses compared with the last one.
    # AFFECTS: Returns True to save. Never blocks a save.
    def review(self, counts):
        matched, total = counts.get("matched", 0), counts.get("tabs_total", 0)
        self.note = None
        if not matched:
            if total:
                self.note = f"none of the {total} terminal tabs matched a chat"
                if not self.skipping:
                    self.log(f"capture not saved: {self.note} (logged once until it changes)")
            self.skipping = bool(total)
            return False
        if self.skipping:
            self.log(f"captures saved again: {matched} of {total} tabs matched")
            self.skipping = False
        if self.previous and total:
            was_matched, was_total = self.previous
            if was_total and was_matched - matched >= MIN_DROP_TABS and \
                    was_matched / was_total - matched / total >= DROP:
                self.log(f"capture saved, but only {matched} of {total} tabs matched (last time "
                         f"{was_matched} of {was_total})")
        # Many chats gone at once (keeper_crash.sharp_fall's rule, counts not shares: after a crash the few
        # reopened tabs all match): look for a Windows Terminal crash now, not in an hour.
        if self.previous and self.previous[0] - matched >= MIN_DROP_TABS and matched <= self.previous[0] * (1 - DROP):
            self.drop_seen = self.drop_seen or self.wall()
        self.previous = (matched, total)
        return True

    # WHAT IT DOES: Writes the heartbeat after one capture attempt (saved, skipped or failed).
    # AFFECTS: watch_heartbeat.json. The last-saved time carries over from the file when nothing was saved.
    # problem_since: when the current run of real problems began (tabs existed but none matched, or a
    # capture failed). A capture refused because a restore held the lock is waiting, not a problem.
    def attempt_finished(self, path=None, error=None):
        now = self.stamp()
        old = read_heartbeat(self.data_dir) or {}
        text = None if error is None else (str(error) or type(error).__name__)[:300]
        busy = isinstance(error, RuntimeError) and BUSY_TEXT in str(error)
        note = None if path or error is not None else self.note
        problem = not path and not busy and (error is not None or bool(note))
        same = old.get("pid") == self.pid and old.get("started_at") == self.started_at
        since = (old.get("problem_since") if same else None) or now
        write_heartbeat(self.data_dir, {
            "pid": self.pid, "started_at": self.started_at, "last_attempt": now,
            "last_saved": now if path else old.get("last_saved"),
            "last_error": None if busy else text, "note": note,
            "problem_since": since if problem else None})


# WHAT IT DOES: Turns the heartbeat and change-logging on for this watch process.
# DEPENDS ON: Called from keeper_app's watch branch only; stop() is called when its main loop ends.
# AFFECTS: ACTIVE. Tests that run main() get it switched off again by stop().
def start(data_dir):
    global ACTIVE
    ACTIVE = Recorder(data_dir)
    ACTIVE.begin()
    return ACTIVE


def stop():
    global ACTIVE
    ACTIVE = None


# WHAT IT DOES: snapshot_once's save decision: logs unreadable processes and match trouble when recording.
# DEPENDS ON: ACTIVE (None outside the watch process: then it is the old rule, save when anything matched).
# AFFECTS: Log lines only; returns True to save.
def review_capture(snapshot, skipped):
    if ACTIVE is None:
        return bool(snapshot["counts"]["matched"])
    ACTIVE.processes_skipped(skipped)
    if ACTIVE.login_window:          # keeper_history.startup_recovery may prefer the previous boot's layout
        snapshot["login_window_open"] = True
    return ACTIVE.review(snapshot["counts"])


# WHAT IT DOES: Tk-thread hook before each capture: is the login recovery window open right now?
# AFFECTS: Whether the next saved layout is marked "login_window_open". No-op outside a watch process.
def login_window(is_open):
    if ACTIVE is not None:
        ACTIVE.login_window = bool(is_open)


# WHAT IT DOES: Tk-thread hooks after each capture. No-ops outside a watch process. Never raise.
def capture_saved(path):
    if ACTIVE is not None:
        ACTIVE.attempt_finished(path=path)


def capture_failed(data_dir, error):
    log_line(data_dir, str(error))
    if ACTIVE is not None:
        ACTIVE.attempt_finished(error=error)

# Last modified: 2026-11-10 | Change: New. Saved died-in-crash lists and the "restore finished" mark | Full history: git log
"""Two small files of Session Keeper's own in the data folder (review items S2 and S8, 2026-11-10).

crash_record_<UTC crash time>_<event|drop>.json: the chats that died in one crash, worked out ONCE from
the transcripts' last-write times. Those times change as soon as a chat is resumed, so a list worked out
later is wrong; the saved one is not. Written by the recorder soon after it sees a sharp drop (or on its
hourly crash read), or by the recovery screen, whichever sees the crash first. Records older than 30 days
are dropped by the normal prune. Snapshot files are never touched here.

restore_done.json: when the recovery screen last finished a real (not preview) restore. Once a restore has
finished after a crash, the screen stops opening on "Before the crash".
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keeper_core import DATA
from keeper_crash import BURST_AFTER, BURST_BEFORE, burst_sessions

RECORD = "crash_record_{stamp}_{source}.json"
RECORD_GLOB = "crash_record_*.json"
RESTORE_DONE = "restore_done.json"
KEEP = timedelta(days=30)                # the prune's longest tier


# WHAT IT DOES: Where the record of one crash lives.
def record_path(data_dir, moment, source):
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path(data_dir) / RECORD.format(stamp=stamp, source=source)


# WHAT IT DOES: Writes one crash record (atomic: temp file then replace). Never raises.
# DEPENDS ON: died items as burst_sessions returns them ("stopped" a naive local datetime or None).
# AFFECTS: One small file in data_dir.
def save_record(data_dir, moment, source, died, now=None):
    try:
        path = record_path(data_dir, moment, source)
        path.parent.mkdir(parents=True, exist_ok=True)
        sessions = [{"session_id": item["session_id"], "title": item.get("title"), "cwd": item.get("cwd"),
                     "entrypoint": item.get("entrypoint"),
                     "stopped": item["stopped"].isoformat() if isinstance(item.get("stopped"), datetime) else None}
                    for item in died]
        body = {"version": 1, "time": moment.astimezone().isoformat(), "source": source,
                "saved_at": (now or datetime.now(timezone.utc)).astimezone().isoformat(), "sessions": sessions}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(body, indent=1), encoding="utf-8")
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError, KeyError):
        pass


# WHAT IT DOES: The saved died list of one crash, or None when there is no readable record.
# AFFECTS: Read-only. Items come back in burst_sessions' shape.
def load_record(data_dir, moment, source):
    try:
        body = json.loads(record_path(data_dir, moment, source).read_text(encoding="utf-8"))
        return [{"session_id": item["session_id"], "title": item.get("title") or f"Claude {item['session_id'][:8]}",
                 "cwd": item.get("cwd") or None, "entrypoint": item.get("entrypoint"),
                 "stopped": datetime.fromisoformat(item["stopped"]) if item.get("stopped") else None}
                for item in body["sessions"]]
    except (OSError, ValueError, TypeError, KeyError):
        return None


# WHAT IT DOES: The died list of one crash: the saved record when there is one, else compute() — saved
# only once the burst is over (moment + BURST_AFTER), so a chat still flushing is not left out for good.
# DEPENDS ON: compute, a zero-argument burst search. keeper_crash.crash_facts calls this as `remember`.
# AFFECTS: May write one record.
def died_for(moment, source, compute, data_dir=DATA, now=None):
    saved = load_record(data_dir, moment, source)
    if saved is not None:
        return saved
    died = compute()
    now = (now or datetime.now(timezone.utc)).astimezone()
    if now >= moment + BURST_AFTER:
        save_record(data_dir, moment, source, died, now)
    return died


# WHAT IT DOES: Saves a record for every crash time that has none yet and whose burst is over.
# Returns True while some crash is still too new to save.
# AFFECTS: Records in data_dir. Reads transcript file times only.
def remember_crashes(times, data_dir=DATA, now=None, burst=burst_sessions):
    now = (now or datetime.now(timezone.utc)).astimezone()
    waiting = False
    for moment in times or ():
        if now < moment + BURST_AFTER:
            waiting = True
        elif load_record(data_dir, moment, "event") is None:
            save_record(data_dir, moment, "event", burst(moment - BURST_BEFORE, moment + BURST_AFTER), now)
    return waiting


# WHAT IT DOES: Notes that a real restore just finished. Never raises.
# DEPENDS ON: Called by keeper_restore.restore_selected after a non-preview restore.
# AFFECTS: restore_done.json in data_dir.
def note_restore_done(data_dir=None, now=None):
    try:
        folder = Path(data_dir or DATA)
        folder.mkdir(parents=True, exist_ok=True)
        moment = (now or datetime.now(timezone.utc)).astimezone().isoformat()
        (folder / RESTORE_DONE).write_text(json.dumps({"at": moment}), encoding="utf-8")
    except OSError:
        pass


# WHAT IT DOES: When the last real restore finished (aware datetime), or None.
# AFFECTS: Read-only.
def last_restore_done(data_dir=None):
    try:
        body = json.loads((Path(data_dir or DATA) / RESTORE_DONE).read_text(encoding="utf-8"))
        return datetime.fromisoformat(body["at"]).astimezone()
    except (OSError, ValueError, TypeError, KeyError):
        return None


# WHAT IT DOES: Deletes crash records whose crash is older than KEEP. Returns how many went.
# DEPENDS ON: The exact record name pattern; nothing else in data_dir is ever looked at.
# AFFECTS: crash_record_*.json only. Called by keeper_prune.run_prune on apply.
# DANGER ZONE HIGH: Never widen the glob / breaks: snapshot files are the evidence / depends on: RECORD_GLOB.
def drop_old_records(data_dir, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone()
    dropped = 0
    for path in Path(data_dir).glob(RECORD_GLOB):
        try:
            stamp = path.name.split("_")[2]
            moment = datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
        except (IndexError, ValueError):
            continue
        if now - moment > KEEP:
            try:
                path.unlink()
                dropped += 1
            except OSError:
                pass
    return dropped

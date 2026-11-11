# Last modified: 2026-11-10 | Change: Report lists unreadable/truncated snapshots and leftover partial_*.tmp; manual --apply deletes those over a day old | Full history: git log
"""Thins old snapshots in the data folder. The ONLY Session Keeper code that deletes snapshot files.

Keep plan (owner approved 2026-10-22, "snapshot keep plan"):
  - every snapshot from the last 2 hours
  - the newest snapshot in each clock hour for the last 7 days
  - the newest snapshot of each day for the last 30 days
  - the last snapshot before every reboot (each boot's newest), forever
  - the newest valid snapshot, whatever its age
  - (2026-11-10) the last snapshot before each Windows Terminal crash and before each sharp drop in matched
    chats (keeper_crash.prune_protect), for the longest tier (30 days); the report lists each with its reason
  - files that fail to parse, legacy captures, and any name that is not exactly
    snapshot_<UTC stamp>_<8 hex>.json are never deleted; nothing else in the folder is touched
Everything else is deleted, and only by run_prune(apply=True).
(2026-11-10) Every report lists the unreadable snapshot files it saw, each marked "truncated" (a write cut off
part way, from before snapshots were written to a temp name first) or "unreadable". Only the manual
`prune --apply` (never the hourly automatic prune) deletes, and only TRUNCATED files older than a day.
Leftover partial_snapshot_*.tmp files (a save killed before its rename) are listed too, and the same manual
--apply deletes those older than a day. No other file in the folder is touched.
On apply it also drops crash records (keeper_crash_record, crash_record_*.json) older than 30 days.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keeper_core import DATA
from keeper_crash import burst_sessions, prune_protect
from keeper_crash_record import drop_old_records, remember_crashes
from keeper_history import load_snapshot
from keeper_locks import exclusive_operation

NAME = re.compile(r"snapshot_\d{8}T\d{12}Z_[0-9a-f]{8}\.json")   # exactly what save_snapshot writes
PARTIAL = re.compile(r"partial_snapshot_\d{8}T\d{12}Z_[0-9a-f]{8}\.tmp")   # save_snapshot's temp name
EVERYTHING = timedelta(hours=2)
HOURLY = timedelta(days=7)
DAILY = timedelta(days=30)
TRUNCATED_AGE = timedelta(days=1)    # a truncated file younger than this may still be being written
DROP_WAIT = 600             # seconds: after a sharp drop, keep looking for its crash this long
EVERY_SECONDS = 3600          # the recorder prunes at most once per hour
RESTORE_LOCK = "restore"
SAMPLE = 10
MAX_GAP = timedelta(days=7)           # automatic prune skips if the two newest snapshots are further apart
MAX_FUTURE = timedelta(minutes=10)    # automatic prune skips if any snapshot is later than now by more
MAX_AUTO_DELETE = 500                 # one automatic run deletes at most this many (normal hourly growth ~30)
_last_run = [None]            # monotonic time of the last recorder prune in this process


# WHAT IT DOES: Reads the save time from a snapshot file name, or None.
# DEPENDS ON: NAME pattern.
# AFFECTS: Second opinion for the "last 2 hours" rule.
def name_time(name):
    try:
        return datetime.strptime(name.split("_")[1], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
    except (IndexError, ValueError):
        return None


# WHAT IT DOES: Splits (path, snapshot-or-None) pairs into (keep, delete) path lists by the keep plan.
# DEPENDS ON: `now` (any timezone-aware datetime); snapshots as load_snapshot returns them (None = unreadable).
# AFFECTS: Pure: reads nothing, deletes nothing. Every input path lands in exactly one list.
# protect: paths kept anyway when captured within DAILY (the last layout before a crash or a sharp drop).
# DANGER ZONE CRITICAL: decides which recovery snapshots are destroyed / breaks: crash recovery if a
# needed snapshot lands in delete / depends on: anything uncertain (unreadable, odd name, no boot id,
# no timezone, legacy) staying in keep.
def plan_prune(items, now, protect=()):
    now = now.astimezone()
    keep, candidates = [], []
    for path, snapshot in items:
        name = Path(path).name
        when = None
        if NAME.fullmatch(name) and isinstance(snapshot, dict) and not snapshot.get("legacy") \
                and isinstance(snapshot.get("boot_id"), str):
            try:
                when = datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError, AttributeError):
                when = None
        if when is None or when.tzinfo is None or name_time(name) is None:
            keep.append(path)
        else:
            candidates.append((when.astimezone(), name_time(name), name, snapshot["boot_id"], path))
    if not candidates:
        return keep, []
    candidates.sort(key=lambda row: (row[0], row[2]))
    kept = {len(candidates) - 1, max(range(len(candidates)), key=lambda i: (candidates[i][1], candidates[i][2]))}
    hours, days, boots = {}, {}, {}
    for index, (when, stamp, _, boot, _) in enumerate(candidates):
        if when >= now - EVERYTHING or stamp >= now - EVERYTHING:
            kept.add(index)
        if when >= now - HOURLY:
            hours[(when.date(), when.hour, when.utcoffset())] = index   # sorted: the last one wins = newest
        if when >= now - DAILY:
            days[when.date()] = index
            if path_of(candidates[index]) in protect:
                kept.add(index)
        boots[boot] = index
        if index + 1 < len(candidates) and candidates[index + 1][3] != boot:
            kept.add(index)   # last snapshot before a boot change, even if boots ever interleave
    kept.update(hours.values(), days.values(), boots.values())
    delete = []
    for index, row in enumerate(candidates):
        (keep if index in kept else delete).append(row[4])
    return keep, delete


def path_of(row):
    return row[4]


# WHAT IT DOES: Lists (path, snapshot-or-None) for every snapshot_*.json file in the folder.
# DEPENDS ON: The data folder; load_snapshot (a full parse; ~2 s for 13,000 files).
# AFFECTS: Read-only. Files with other names are listed unparsed (None) and are therefore kept.
def scan(data_dir):
    items = []
    for path in Path(data_dir).glob("snapshot_*.json"):
        items.append((path, load_snapshot(path) if NAME.fullmatch(path.name) else None))
    return items


# WHAT IT DOES: "truncated" when a snapshot file is empty or its JSON stops part way (cut off at the end),
# otherwise "unreadable" (bad text, valid JSON that is not a snapshot, or a file that cannot be read).
# DEPENDS ON: The file. AFFECTS: Read-only.
# DANGER ZONE HIGH: Only "truncated" may ever be deleted / breaks: a damaged but meaningful file lost / depends
# on: the error being AT the end of the text (or an unterminated string, which by definition runs to the end).
def damage_kind(path):
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "unreadable"
    if not text.strip():
        return "truncated"
    try:
        json.loads(text)
    except json.JSONDecodeError as error:
        if error.msg.startswith("Unterminated string") or error.pos >= len(text.rstrip()):
            return "truncated"
    return "unreadable"


# WHAT IT DOES: The truncated files (from damaged, a list of (name, kind)) older than TRUNCATED_AGE by both
# their name stamp and their file time, as paths.
# DEPENDS ON: name_time and the files' modified times. AFFECTS: Read-only.
def old_truncated(data_dir, damaged, now):
    return [Path(data_dir) / name for name, kind in damaged
            if kind == "truncated" and over_a_day(Path(data_dir) / name, name, now)]


# WHAT IT DOES: The leftover partial_snapshot_*.tmp names in the folder (exact save_snapshot temp names only).
# DEPENDS ON: The data folder. AFFECTS: Read-only.
def partial_names(data_dir):
    return sorted(path.name for path in Path(data_dir).glob("partial_*.tmp") if PARTIAL.fullmatch(path.name))


# WHAT IT DOES: True when a file is older than TRUNCATED_AGE by both the stamp in its snapshot name and its
# file time. DEPENDS ON: name_time; the file. AFFECTS: Read-only. An unreadable time is never "old".
def over_a_day(path, snapshot_name, now):
    stamp = name_time(snapshot_name)
    try:
        written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return False
    return stamp is not None and stamp < now - TRUNCATED_AGE and written < now - TRUNCATED_AGE


# WHAT IT DOES: Deletes the given snapshot files, refusing anything outside data_dir or with another name.
# pattern: NAME (snapshots) by default; PARTIAL only for save_snapshot's leftover temp files.
# DEPENDS ON: The data folder.
# AFFECTS: Removes files. Returns a list of error strings; never raises for one bad file.
# DANGER ZONE CRITICAL: the only unlink in Session Keeper / breaks: recovery history / depends on:
# the folder and name checks below.
def delete_files(data_dir, paths, pattern=NAME):
    folder, errors = Path(data_dir).resolve(), []
    for path in paths:
        path = Path(path)
        try:
            if path.parent.resolve() != folder or not pattern.fullmatch(path.name) or path.is_symlink() \
                    or not path.is_file():
                errors.append(f"refused {path}")
                continue
            path.unlink()
        except OSError as error:
            errors.append(f"{path.name}: {error}")
    return errors


# WHAT IT DOES: Returns a plain-English reason the PC clock looks wrong, or None when it looks sane.
# DEPENDS ON: snapshots as scan returns them (unreadable ones are ignored); `now` timezone-aware.
# AFFECTS: Pure.
# DANGER ZONE CRITICAL: the only defence against a clock jump making the keep plan delete everything /
# breaks: crash recovery history / depends on: MAX_GAP and MAX_FUTURE.
def clock_problem(items, now):
    times = []
    for _, snapshot in items:
        try:
            when = datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if when.tzinfo is not None:
            times.append(when)
    times.sort()
    if times and times[-1] > now + MAX_FUTURE:
        return f"newest snapshot {times[-1].isoformat()} is in the future (clock moved backwards?)"
    if len(times) >= 2 and times[-1] - times[-2] > MAX_GAP:
        return f"gap of {times[-1] - times[-2]} between the two newest snapshots (clock jump or long absence?)"
    return None


# WHAT IT DOES: Plans the prune for a folder and, only when apply=True, deletes the planned files.
# DEPENDS ON: scan, plan_prune, delete_files. Callers that apply must hold the restore lock.
# AFFECTS: apply=False is read-only. Returns counts, oldest kept / newest deleted names, a sample.
# automatic=True (the recorder) skips entirely when clock_problem finds something (report["skipped"]
# holds the reason) and deletes at most MAX_AUTO_DELETE files, oldest first. The manual command passes
# automatic=False: with a clock problem it reports report["warning"] and, on apply, deletes NOTHING
# (report["blocked"]) unless force=True. crash_times: Windows Terminal crash times (keeper_crash.read_crash_times);
# the layouts before them and before sharp drops are kept 30 days and listed in report["protected"].
def run_prune(data_dir=DATA, now=None, apply=False, automatic=False, force=False, crash_times=()):
    now = (now or datetime.now()).astimezone()
    items = scan(data_dir)
    reasons = prune_protect(items, crash_times)
    problem = clock_problem(items, now)
    if automatic and problem:
        return {"apply": apply, "total": len(items), "keep": len(items), "delete": 0,
                "untouched_unreadable_or_other": 0, "oldest_kept": None, "newest_deleted": None, "sample": [],
                "deleted": 0, "errors": [], "skipped": problem}
    keep, delete = plan_prune(items, now, set(reasons))
    if automatic and len(delete) > MAX_AUTO_DELETE:
        delete = sorted(delete, key=lambda p: Path(p).name)
        keep = list(keep) + delete[MAX_AUTO_DELETE:]
        delete = delete[:MAX_AUTO_DELETE]
    unreadable = sum(1 for path, snapshot in items if snapshot is None)
    damaged = sorted((Path(path).name, damage_kind(path)) for path, snapshot in items
                     if snapshot is None and NAME.fullmatch(Path(path).name))
    kept_set = set(keep)
    kept_named = sorted(Path(p).name for p in keep if NAME.fullmatch(Path(p).name))
    gone = sorted(Path(p).name for p in delete)
    report = {"apply": apply, "total": len(items), "keep": len(keep), "delete": len(delete),
              "untouched_unreadable_or_other": unreadable, "oldest_kept": kept_named[0] if kept_named else None,
              "newest_deleted": gone[-1] if gone else None,
              "sample": gone[::max(1, len(gone) // SAMPLE)][:SAMPLE], "deleted": 0, "errors": [],
              "protected": sorted((Path(p).name, why) for p, why in reasons.items() if p in kept_set),
              "damaged": damaged, "truncated_deleted": [], "partials": partial_names(data_dir),
              "partials_deleted": []}
    if problem:
        report["warning"] = problem
    blocked = bool(problem and apply and not force)
    if blocked:
        report["blocked"] = True
    if apply and delete and not blocked:
        report["errors"] = delete_files(data_dir, delete)
        report["deleted"] = sum(1 for path in delete if not Path(path).exists())
    if apply and not blocked:
        report["old_crash_records"] = drop_old_records(data_dir, now)
    if apply and not automatic and not blocked:      # the manual --apply only, never the hourly prune
        cut = old_truncated(data_dir, damaged, now)
        report["errors"] += delete_files(data_dir, cut)
        report["truncated_deleted"] = sorted(path.name for path in cut if not path.exists())
        stale = [Path(data_dir) / name for name in report["partials"]
                 if over_a_day(Path(data_dir) / name, name[len("partial_"):], now)]
        report["errors"] += delete_files(data_dir, stale, PARTIAL)
        report["partials_deleted"] = sorted(path.name for path in stale if not path.exists())
    return report


# WHAT IT DOES: Turns a run_prune report into the lines the command prints.
# ISOLATED
def report_text(report):
    lines = []
    if report.get("warning"):
        lines.append(f"WARNING: PC clock looks wrong: {report['warning']}")
    if report.get("blocked"):
        lines.append("NOTHING DELETED because of the clock warning. To delete anyway, run: prune --apply --force")
    lines += [("APPLIED" if report["apply"] and not report.get("blocked") else "NOT APPLIED" if report.get("blocked") else "DRY RUN (nothing deleted; add --apply to delete)"),
             f"snapshot files: {report['total']}", f"keep: {report['keep']}", f"delete: {report['delete']}",
             f"unreadable or other names (never deleted): {report['untouched_unreadable_or_other']}",
             f"oldest kept: {report['oldest_kept']}", f"newest deleted: {report['newest_deleted']}"]
    protected = report.get("protected") or []
    lines.append(f"last layouts before a crash or a sharp drop (kept at least 30 days): {len(protected)}")
    lines += [f"  {name}: {why}" for name, why in protected[:SAMPLE]]
    damaged = report.get("damaged") or []
    lines.append(f"unreadable or truncated snapshot files: {len(damaged)} (truncated ones over a day old are deleted "
                 "by prune --apply)")
    lines += [f"  {name}: {kind}" for name, kind in damaged]
    if report.get("truncated_deleted"):
        lines.append(f"truncated files deleted: {len(report['truncated_deleted'])}")
    partials = report.get("partials") or []
    lines.append(f"leftover partial snapshot files (a save cut off before it finished): {len(partials)} "
                 "(those over a day old are deleted by prune --apply)")
    lines += [f"  {name}" for name in partials]
    if report.get("partials_deleted"):
        lines.append(f"partial files deleted: {len(report['partials_deleted'])}")
    if report["apply"]:
        lines.append(f"deleted: {report['deleted']}")
    lines += [f"errors: {len(report['errors'])}"] + [f"  {error}" for error in report["errors"][:SAMPLE]]
    lines += ["sample of files to delete:"] + [f"  {name}" for name in report["sample"]]
    return "\n".join(lines)


# WHAT IT DOES: Appends one line to the recorder's error log; never raises.
# DEPENDS ON: Writable data folder.
# AFFECTS: watcher_errors.log only.
def log_error(data_dir, message):
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        with (Path(data_dir) / "watcher_errors.log").open("a", encoding="utf-8") as file:
            file.write(f"{datetime.now().isoformat()} prune: {message}\n")
    except OSError:
        pass


# WHAT IT DOES: Forgets the last recorder prune time (tests; a new process starts fresh anyway).
# ISOLATED
def reset_schedule():
    _last_run[0] = None


# WHAT IT DOES: Called by the recorder after each successful snapshot: prunes if an hour has passed
# since the last prune, and only when no restore is running. Never raises.
# DEPENDS ON: The restore lock (skipped, and retried after the next snapshot, while it is held).
# AFFECTS: Deletes old snapshot files at most once per hour; errors go to watcher_errors.log.
# read_crashes: zero-argument crash-time reader (keeper_crash.read_crash_times), called BEFORE the lock is
# taken so a slow event log never holds up a restore; None or a failed read protects drop layouts only.
def prune_if_due(data_dir=DATA, clock=time.monotonic, lock_name=RESTORE_LOCK, read_crashes=None):
    started = clock()
    if _last_run[0] is not None and started - _last_run[0] < EVERY_SECONDS:
        return None
    locked = [False]
    try:
        crash_times = (read_crashes() if read_crashes else None) or ()
        with exclusive_operation(lock_name):
            locked[0] = True
            _last_run[0] = started
            report = run_prune(data_dir, apply=True, automatic=True, crash_times=crash_times)
    except Exception as error:   # the recorder must keep recording whatever happens here
        if locked[0] or not isinstance(error, RuntimeError):
            log_error(data_dir, error)
        return None   # lock busy: a restore is running; try again after the next snapshot
    if report.get("skipped"):
        log_error(data_dir, f"automatic prune skipped: {report['skipped']}")
    for message in report["errors"][:SAMPLE]:
        log_error(data_dir, message)
    return report


# WHAT IT DOES: The recorder's step after each saved layout: the hourly prune as before and, when the
# heartbeat saw matched chats fall sharply, ONE crash-log read right away (not an hour later) to save the
# crash record while the transcripts still show when each chat died.
# DEPENDS ON: recorder (keeper_heartbeat.ACTIVE) and its drop_seen; read_crashes (keeper_crash
# .read_crash_times, ~2 s, read at most once here); prune (prune_if_due); keeper_crash_record.
# AFFECTS: Crash records in data_dir (never a snapshot); whatever prune_if_due does. Never raises.
def after_capture(data_dir=DATA, read_crashes=None, prune=None, recorder=None, now=None, burst=None):
    if read_crashes is None:
        from keeper_crash import read_crash_times as read_crashes
    prune = prune or prune_if_due
    burst = burst or burst_sessions
    if recorder is None:
        import keeper_heartbeat
        recorder = keeper_heartbeat.ACTIVE
    read = []

    def crashes():
        if not read:
            read.append(read_crashes())
        return read[0]
    try:
        seen = getattr(recorder, "drop_seen", None)
        if seen is not None:
            moment = (now or datetime.now(timezone.utc)).astimezone()
            waiting = remember_crashes(crashes(), data_dir, moment, burst)
            if not waiting or moment.timestamp() - seen > DROP_WAIT:
                recorder.drop_seen = None
        prune(data_dir, read_crashes=crashes)
        if read:                                # the hourly prune read the crash log too
            remember_crashes(read[0], data_dir, now, burst)
    except Exception:
        pass

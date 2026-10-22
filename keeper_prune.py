# Last modified: 2026-10-22 | Change: Snapshot keep plan: thin old recovery snapshots, dry run by default | Full history: git log
"""Thins old snapshots in the data folder. The ONLY Session Keeper code that deletes snapshot files.

Keep plan (owner approved 2026-10-22, "snapshot keep plan"):
  - every snapshot from the last 2 hours
  - the newest snapshot in each clock hour for the last 7 days
  - the newest snapshot of each day for the last 30 days
  - the last snapshot before every reboot (each boot's newest), forever
  - the newest valid snapshot, whatever its age
  - files that fail to parse, legacy captures, and any name that is not exactly
    snapshot_<UTC stamp>_<8 hex>.json are never deleted; nothing else in the folder is touched
Everything else is deleted, and only by run_prune(apply=True).
"""

import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keeper_core import DATA
from keeper_history import load_snapshot
from keeper_locks import exclusive_operation

NAME = re.compile(r"snapshot_\d{8}T\d{12}Z_[0-9a-f]{8}\.json")   # exactly what save_snapshot writes
EVERYTHING = timedelta(hours=2)
HOURLY = timedelta(days=7)
DAILY = timedelta(days=30)
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
# DANGER ZONE CRITICAL: decides which recovery snapshots are destroyed / breaks: crash recovery if a
# needed snapshot lands in delete / depends on: anything uncertain (unreadable, odd name, no boot id,
# no timezone, legacy) staying in keep.
def plan_prune(items, now):
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
        boots[boot] = index
        if index + 1 < len(candidates) and candidates[index + 1][3] != boot:
            kept.add(index)   # last snapshot before a boot change, even if boots ever interleave
    kept.update(hours.values(), days.values(), boots.values())
    delete = []
    for index, row in enumerate(candidates):
        (keep if index in kept else delete).append(row[4])
    return keep, delete


# WHAT IT DOES: Lists (path, snapshot-or-None) for every snapshot_*.json file in the folder.
# DEPENDS ON: The data folder; load_snapshot (a full parse; ~2 s for 13,000 files).
# AFFECTS: Read-only. Files with other names are listed unparsed (None) and are therefore kept.
def scan(data_dir):
    items = []
    for path in Path(data_dir).glob("snapshot_*.json"):
        items.append((path, load_snapshot(path) if NAME.fullmatch(path.name) else None))
    return items


# WHAT IT DOES: Deletes the given snapshot files, refusing anything outside data_dir or with another name.
# DEPENDS ON: The data folder.
# AFFECTS: Removes files. Returns a list of error strings; never raises for one bad file.
# DANGER ZONE CRITICAL: the only unlink in Session Keeper / breaks: recovery history / depends on:
# the folder and name checks below.
def delete_files(data_dir, paths):
    folder, errors = Path(data_dir).resolve(), []
    for path in paths:
        path = Path(path)
        try:
            if path.parent.resolve() != folder or not NAME.fullmatch(path.name) or path.is_symlink() \
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
# (report["blocked"]) unless force=True.
def run_prune(data_dir=DATA, now=None, apply=False, automatic=False, force=False):
    now = (now or datetime.now()).astimezone()
    items = scan(data_dir)
    problem = clock_problem(items, now)
    if automatic and problem:
        return {"apply": apply, "total": len(items), "keep": len(items), "delete": 0,
                "untouched_unreadable_or_other": 0, "oldest_kept": None, "newest_deleted": None, "sample": [],
                "deleted": 0, "errors": [], "skipped": problem}
    keep, delete = plan_prune(items, now)
    if automatic and len(delete) > MAX_AUTO_DELETE:
        delete = sorted(delete, key=lambda p: Path(p).name)
        keep = list(keep) + delete[MAX_AUTO_DELETE:]
        delete = delete[:MAX_AUTO_DELETE]
    unreadable = sum(1 for path, snapshot in items if snapshot is None)
    kept_named = sorted(Path(p).name for p in keep if NAME.fullmatch(Path(p).name))
    gone = sorted(Path(p).name for p in delete)
    report = {"apply": apply, "total": len(items), "keep": len(keep), "delete": len(delete),
              "untouched_unreadable_or_other": unreadable, "oldest_kept": kept_named[0] if kept_named else None,
              "newest_deleted": gone[-1] if gone else None,
              "sample": gone[::max(1, len(gone) // SAMPLE)][:SAMPLE], "deleted": 0, "errors": []}
    if problem:
        report["warning"] = problem
    blocked = bool(problem and apply and not force)
    if blocked:
        report["blocked"] = True
    if apply and delete and not blocked:
        report["errors"] = delete_files(data_dir, delete)
        report["deleted"] = sum(1 for path in delete if not Path(path).exists())
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
def prune_if_due(data_dir=DATA, clock=time.monotonic, lock_name=RESTORE_LOCK):
    started = clock()
    if _last_run[0] is not None and started - _last_run[0] < EVERY_SECONDS:
        return None
    locked = [False]
    try:
        with exclusive_operation(lock_name):
            locked[0] = True
            _last_run[0] = started
            report = run_prune(data_dir, apply=True, automatic=True)
    except Exception as error:   # the recorder must keep recording whatever happens here
        if locked[0] or not isinstance(error, RuntimeError):
            log_error(data_dir, error)
        return None   # lock busy: a restore is running; try again after the next snapshot
    if report.get("skipped"):
        log_error(data_dir, f"automatic prune skipped: {report['skipped']}")
    for message in report["errors"][:SAMPLE]:
        log_error(data_dir, message)
    return report

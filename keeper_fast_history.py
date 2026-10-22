# Last modified: 2026-10-21 | Change: Fast recovery start: parse only the snapshots the screen can show | Full history: git log
"""Find the few saved layouts the recovery screen offers without parsing all ~10,000 files. Read-only.

The chooser (keeper_chooser.chooser_entries) offers: Latest, Before last reboot, 15 min / 1 hour /
3 hours ago, the first snapshot today, the last snapshot of each of the 5 previous days, and the old
prototype capture. Each choice is "the newest (or oldest) valid snapshot that passes a test", so any
subset of the full history that still contains those exact winners gives the chooser the same answers.
This module builds that subset: the newest RECENT files plus a targeted probe for every choice.
File names carry the save time (a few seconds after capture, and in the same order, because
snapshot_once holds the restore lock across capture and save), which is what makes probing possible.
"""

from bisect import bisect_left, bisect_right
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from keeper_chooser import CHECKPOINTS, PAST_DAYS
from keeper_core import DATA
from keeper_history import load_snapshot

RECENT = 200
SLACK = timedelta(minutes=10)      # file stamp minus capture time; measured at most ~19 s on real data
LATE = timedelta(minutes=10)       # the chooser runs a moment after this load; cover that delay


# WHAT IT DOES: Reads the save time out of a snapshot file name, or None for other names.
# DEPENDS ON: save_snapshot's "snapshot_%Y%m%dT%H%M%S%fZ_xxxxxxxx.json" pattern.
# AFFECTS: Names that do not match (the legacy prototype) are always parsed in full.
def name_time(path):
    try:
        return datetime.strptime(path.name.split("_")[1], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc)
    except (IndexError, ValueError):
        return None


# WHAT IT DOES: Reads a snapshot's capture time, timezone-aware.
# DEPENDS ON: A snapshot that load_snapshot accepted.
# AFFECTS: Nothing.
def moment(snapshot):
    return datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))


# WHAT IT DOES: Parses files on demand (each at most once) and remembers the ones the screen needs.
# DEPENDS ON: Files sorted oldest first by name time.
# AFFECTS: Read-only; the files are never written, renamed or deleted.
class Probe:
    def __init__(self, files):
        self.paths = [path for path, _ in files]
        self.times = [stamp for _, stamp in files]
        self.cache, self.keep = {}, set()

    def load(self, index):
        if index not in self.cache:
            self.cache[index] = load_snapshot(self.paths[index])
        return self.cache[index]

    # WHAT IT DOES: True for a usable, non-prototype snapshot (the chooser's "normal" list).
    def normal(self, index):
        snapshot = self.load(index)
        return snapshot is not None and not snapshot.get("legacy")

    # WHAT IT DOES: Keeps the newest `count` valid files.
    def newest(self, count):
        found = 0
        for index in range(len(self.paths) - 1, -1, -1):
            if found >= count:
                return
            if self.load(index) is not None:
                self.keep.add(index)
                found += 1

    # WHAT IT DOES: Keeps every normal snapshot captured at or before `start`, going back in time,
    # through the first one captured at or before `stop` (stop <= start).
    def back_from(self, start, stop):
        index = bisect_right(self.times, start + SLACK) - 1
        while index >= 0:
            if self.normal(index):
                when = moment(self.load(index))
                if when <= start:
                    self.keep.add(index)
                    if when <= stop:
                        return
            index -= 1

    # WHAT IT DOES: Keeps the first normal snapshot captured on local date `day`.
    def first_on(self, day):
        begins = datetime.combine(day, time.min).astimezone()
        for index in range(bisect_left(self.times, begins - SLACK), len(self.paths)):
            if self.normal(index):
                local = moment(self.load(index)).astimezone().date()
                if local == day:
                    self.keep.add(index)
                    return
                if local > day:
                    return

    # WHAT IT DOES: Nearest normal snapshot at or below `index` (walking back in time), as (index, snapshot).
    def normal_at_or_below(self, index):
        while index >= 0:
            if self.normal(index):
                return index, self.load(index)
            index -= 1
        return None

    # WHAT IT DOES: Keeps the newest normal snapshot whose boot differs from `boot`, by halving the file list.
    # DEPENDS ON: Every snapshot of the newest boot being newer than every other snapshot (time only
    # moves forward across a reboot; verified on the real data folder 2026-10-21).
    # AFFECTS: Avoids parsing the ~9,000 files of a long-running boot one by one.
    def before_boot(self, boot):
        low, high = 0, len(self.paths)
        while low < high:
            middle = (low + high) // 2
            found = self.normal_at_or_below(middle)
            if found is not None and found[1].get("boot_id") == boot:
                high = middle
            else:
                low = middle + 1
        found = self.normal_at_or_below(low - 1)
        if found is not None:
            self.keep.add(found[0])


# WHAT IT DOES: Returns the saved layouts the recovery screen needs, newest first, like read_history.
# DEPENDS ON: The snapshot folder and the current time (the chooser may run up to LATE afterwards).
# AFFECTS: Read-only. Choices made in keeper_chooser pick the same snapshot as from the full history.
def fast_history(data_dir=DATA, now=None):
    now = (now or datetime.now()).astimezone()
    stamped, others = [], []
    for path in Path(data_dir).glob("snapshot_*.json"):
        stamp = name_time(path)
        (others if stamp is None else stamped).append((path, stamp))
    stamped.sort(key=lambda item: (item[1], item[0].name))
    probe = Probe(stamped)
    probe.newest(RECENT)
    history = [(path, snapshot) for path in (path for path, _ in others)
               if (snapshot := load_snapshot(path)) is not None]
    history += [(probe.paths[index], probe.load(index)) for index in probe.keep]
    if not history:
        return []
    newest = max(history, key=lambda item: item[1]["captured_at"])[1]
    probe.before_boot(newest.get("boot_id"))
    late = now + LATE
    for age, _ in CHECKPOINTS:
        probe.back_from(late - age, now - age)
    for day in {now.date(), late.date()}:
        probe.first_on(day)
        for back in range(1, PAST_DAYS + 1):
            ends = datetime.combine(day - timedelta(days=back - 1), time.min).astimezone()
            probe.back_from(ends - timedelta(microseconds=1), ends - timedelta(microseconds=1))
    history = [item for item in history if name_time(item[0]) is None]
    history += [(probe.paths[index], probe.load(index)) for index in probe.keep]
    return sorted(history, key=lambda item: item[1]["captured_at"], reverse=True)

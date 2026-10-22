# Last modified: 2026-10-21 | Change: Tests that the fast start picks the same chooser snapshots as a full read | Full history: git log
"""Fake snapshots in a temporary folder only. Compares fast_history with the full read_history."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import keeper_fast_history
from keeper_chooser import chooser_entries
from keeper_fast_history import fast_history
from keeper_history import read_history

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)


# WHAT IT DOES: Writes one fake snapshot the way save_snapshot names it (file stamp a few seconds after capture).
# DEPENDS ON: A temporary folder.
# AFFECTS: Test data only.
def write(folder, captured, boot, number, body=None):
    stamp = (captured + timedelta(seconds=7)).strftime("%Y%m%dT%H%M%S%fZ")
    path = Path(folder) / f"snapshot_{stamp}_{number:08x}.json"
    data = {"version": 1, "captured_at": captured.isoformat(), "boot_id": boot, "windows": []}
    path.write_text(body if body is not None else json.dumps(data), encoding="utf-8")
    return path


# WHAT IT DOES: Builds days of history: sparse old days, one reboot far back, a dense recent run, junk files.
# DEPENDS ON: write().
# AFFECTS: Test data only.
def build(folder, reboot_minutes_back=900, recent_minutes=1400, gap_days=()):
    number = 0
    for day in range(1, 8):
        if day in gap_days:
            continue
        for hour in (2, 9, 17, 23):
            number += 1
            write(folder, NOW - timedelta(days=day) + timedelta(hours=hour - 15), "boot-old", number)
    for minutes in range(recent_minutes, 0, -2):
        number += 1
        boot = "boot-new" if minutes < reboot_minutes_back else "boot-mid"
        write(folder, NOW - timedelta(minutes=minutes), boot, number)
    legacy = {"version": 1, "captured_at": "2026-09-26T04:56:30+00:00", "boot_id": "legacy-prototype",
              "windows": [], "legacy": True}
    (Path(folder) / "snapshot_legacy_prototype.json").write_text(json.dumps(legacy), encoding="utf-8")
    return number


# WHAT IT DOES: Turns chooser entries into (label, file name) so two histories can be compared.
# DEPENDS ON: chooser_entries.
# AFFECTS: Nothing.
def picks(history, now):
    return [(label, history[index][0].name) for label, index in chooser_entries(history, now)]


class FastHistoryTests(unittest.TestCase):
    # WHAT IT DOES: Asserts every chooser option names the same snapshot for several screen-open times.
    def assert_same(self, folder, now=NOW):
        full = read_history(folder)
        fast = fast_history(folder, now)
        for later in (timedelta(0), timedelta(seconds=40), timedelta(minutes=5)):
            self.assertEqual(picks(fast, now + later), picks(full, now + later))
        return full, fast

    def test_same_choices_with_reboot_far_back(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder)
            full, fast = self.assert_same(folder)
            self.assertLess(len(fast), len(full) / 2)
            self.assertEqual(fast[0][0], full[0][0])

    def test_same_choices_with_recent_reboot_and_missing_days(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, reboot_minutes_back=30, gap_days=(2, 4))
            self.assert_same(folder)

    def test_same_choices_when_recording_stopped_hours_ago(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, recent_minutes=300)
            self.assert_same(folder, NOW + timedelta(hours=7))

    def test_broken_files_at_probe_points_are_skipped_like_read_history(self):
        with tempfile.TemporaryDirectory() as folder:
            number = build(folder)
            for minutes in (15, 60, 180, 899, 901):
                write(folder, NOW - timedelta(minutes=minutes, seconds=-1), "boot-new", number + minutes, "{broken")
            write(folder, NOW - timedelta(seconds=30), "boot-new", number + 5000, "{broken")
            write(folder, NOW - timedelta(days=1, hours=-8), "boot-old", number + 6000,
                  json.dumps({"version": 2, "captured_at": "x"}))
            self.assert_same(folder)

    def test_parses_only_a_few_hundred_files(self):
        with tempfile.TemporaryDirectory() as folder:
            build(folder, recent_minutes=4000, reboot_minutes_back=3900)
            real = keeper_fast_history.load_snapshot
            with patch.object(keeper_fast_history, "load_snapshot", side_effect=real) as counted:
                fast_history(folder, NOW)
            self.assertLess(counted.call_count, 330)

    def test_empty_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(fast_history(folder, NOW), [])


if __name__ == "__main__":
    unittest.main()

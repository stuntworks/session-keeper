# Last modified: 2026-10-22 | Change: Tests for the snapshot keep plan (thinning old recovery snapshots) | Full history: git log
"""Fake snapshots in temporary folders only. Never touches the real data folder."""

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import keeper_app
import keeper_prune
from keeper_chooser import chooser_entries
from keeper_fast_history import fast_history
from keeper_history import read_history
from keeper_locks import exclusive_operation
from keeper_prune import plan_prune, prune_if_due, run_prune

NOW = datetime(2026, 10, 19, 15, 0).astimezone()   # local time, like the chooser
COUNTER = [0]


# WHAT IT DOES: Builds a (path, snapshot) pair named the way save_snapshot names files.
# ISOLATED
def item(captured, boot="boot-now", folder="data", legacy=False):
    COUNTER[0] += 1
    stamp = (captured + timedelta(seconds=7)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = Path(folder) / f"snapshot_{stamp}_{COUNTER[0]:08x}.json"
    data = {"version": 1, "captured_at": captured.isoformat(), "boot_id": boot, "windows": []}
    if legacy:
        data["legacy"] = True
    return path, data


# WHAT IT DOES: Writes an item to disk and returns its path.
# ISOLATED
def write(folder, captured, boot="boot-now", body=None):
    path, data = item(captured, boot, folder)
    path.write_text(body if body is not None else json.dumps(data), encoding="utf-8")
    return path


# WHAT IT DOES: Runs the planner and returns the deleted file names as a set.
# ISOLATED
def deleted(items, now=NOW):
    keep, delete = plan_prune(items, now)
    assert not set(keep) & set(delete), "a path is in both keep and delete"
    assert len(keep) + len(delete) == len(items)
    return {Path(p).name for p in delete}


class PlanTests(unittest.TestCase):
    def test_every_snapshot_of_last_two_hours_kept_boundary_inclusive(self):
        items = [item(NOW - timedelta(minutes=m)) for m in range(0, 121, 2)]
        self.assertEqual(deleted(items), set())

    def test_just_over_two_hours_thinned_to_newest_in_hour(self):
        newest_in_hour = item(NOW - timedelta(hours=2, minutes=1))      # 12:59
        older = item(NOW - timedelta(hours=2, minutes=30))              # 12:30, same clock hour
        latest = item(NOW)
        self.assertEqual(deleted([newest_in_hour, older, latest]), {older[0].name})

    def test_hour_bucketing_keeps_newest_of_each_clock_hour(self):
        hour_a = [item(NOW.replace(hour=10, minute=m)) for m in (5, 20, 59)]
        hour_b = [item(NOW.replace(hour=11, minute=m)) for m in (0, 1)]
        gone = deleted(hour_a + hour_b + [item(NOW)])
        self.assertEqual(gone, {hour_a[0][0].name, hour_a[1][0].name, hour_b[0][0].name})

    def test_seven_day_boundary(self):
        inside = [item(NOW - timedelta(days=7) + timedelta(minutes=m)) for m in (1, 2)]   # same hour, inside
        outside = [item(NOW - timedelta(days=7, minutes=m)) for m in (1, 2)]              # outside 7 d
        gone = deleted(inside + outside + [item(NOW)])
        # inside: newest of its hour kept, other deleted; outside: only daily rule, and the day's newest
        # snapshot is the inside one (same day), so both outside files go.
        self.assertEqual(gone, {inside[0][0].name, outside[0][0].name, outside[1][0].name})

    def test_day_bucketing_between_seven_and_thirty_days(self):
        day = (NOW - timedelta(days=12)).replace(hour=1)
        same_day = [item(day + timedelta(hours=h)) for h in (0, 5, 20)]
        gone = deleted(same_day + [item(NOW)])
        self.assertEqual(gone, {same_day[0][0].name, same_day[1][0].name})

    def test_thirty_day_boundary(self):
        inside = item(NOW - timedelta(days=30) + timedelta(minutes=1))
        outside = item(NOW - timedelta(days=30, minutes=1))
        gone = deleted([inside, outside, item(NOW)])
        self.assertEqual(gone, {outside[0].name})

    def test_last_snapshot_before_each_reboot_kept_forever(self):
        year_ago = NOW - timedelta(days=365)
        boot_a = [item(year_ago + timedelta(minutes=m), "boot-a") for m in (0, 2, 4)]
        boot_b = [item(NOW - timedelta(days=200) + timedelta(minutes=m), "boot-b") for m in (0, 2)]
        gone = deleted(boot_a + boot_b + [item(NOW, "boot-c")])
        self.assertEqual(gone, {boot_a[0][0].name, boot_a[1][0].name, boot_b[0][0].name})

    def test_newest_valid_kept_whatever_its_age(self):
        old = [item(NOW - timedelta(days=400, minutes=m)) for m in (0, 2, 4)]
        gone = deleted(old)
        self.assertEqual(gone, {old[1][0].name, old[2][0].name})

    def test_unparseable_files_never_deleted(self):
        broken = (item(NOW - timedelta(days=90))[0], None)
        self.assertEqual(deleted([broken, item(NOW)]), set())

    def test_non_snapshot_names_and_legacy_never_deleted(self):
        old = NOW - timedelta(days=90)
        _, data = item(old)
        others = [(Path("data/snapshot_legacy_prototype.json"), data),
                  (Path("data/snapshot_20260101T000000000000Z_abc.json"), data),
                  (Path("data/other.json"), data),
                  item(old, legacy=True)]
        self.assertEqual(deleted(others + [item(NOW)]), set())

    def test_snapshot_without_boot_or_from_future_kept(self):
        _, data = item(NOW - timedelta(days=90))
        no_boot = (item(NOW)[0].with_name("snapshot_20260701T000000000000Z_0000beef.json"),
                   {k: v for k, v in data.items() if k != "boot_id"})
        future = item(NOW + timedelta(days=3))
        self.assertEqual(deleted([no_boot, future, item(NOW)]), set())


class RunnerTests(unittest.TestCase):
    # WHAT IT DOES: Fills a folder with 40 days of dense history plus junk; returns the junk paths.
    def fill(self, folder):
        folder = Path(folder)
        for minutes in range(0, 40 * 24 * 60, 60):
            write(folder, NOW - timedelta(minutes=minutes), "boot-old" if minutes > 20 * 24 * 60 else "boot-now")
        for minutes in range(0, 180, 2):
            write(folder, NOW - timedelta(minutes=minutes, seconds=30))
        junk = [folder / "watcher_errors.log", folder / "created_windows.jsonl", folder / "notes.json",
                write(folder, NOW - timedelta(days=60), body="{broken"),
                folder / "snapshot_legacy_prototype.json"]
        for path in junk:
            if not path.exists():
                path.write_text("{}", encoding="utf-8")
        return junk

    def test_dry_run_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            self.fill(folder)
            before = sorted(Path(folder).iterdir())
            report = run_prune(folder, NOW, apply=False)
            self.assertEqual(sorted(Path(folder).iterdir()), before)
            self.assertGreater(report["delete"], 0)
            self.assertEqual(report["deleted"], 0)

    def test_apply_deletes_exactly_the_plan_and_leaves_other_files(self):
        with tempfile.TemporaryDirectory() as folder:
            junk = self.fill(folder)
            plan = run_prune(folder, NOW, apply=False)
            report = run_prune(folder, NOW, apply=True)
            self.assertEqual(report["deleted"], plan["delete"])
            for path in junk:
                self.assertTrue(path.exists(), path.name)
            self.assertEqual(run_prune(folder, NOW, apply=False)["delete"], 0)

    def test_refuses_to_delete_outside_folder_or_odd_names(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            outside = write(elsewhere, NOW - timedelta(days=50))
            odd = Path(folder) / "notes.json"
            odd.write_text("{}", encoding="utf-8")
            errors = keeper_prune.delete_files(folder, [outside, odd])
            self.assertTrue(outside.exists() and odd.exists())
            self.assertEqual(len(errors), 2)

    def test_chooser_still_works_on_thinned_history(self):
        with tempfile.TemporaryDirectory() as folder:
            for minutes in range(0, 9 * 24 * 60, 2):
                boot = "boot-now" if minutes < 26 * 60 else "boot-old"
                write(folder, NOW - timedelta(minutes=minutes), boot)
            full = read_history(folder)
            before = {label.split(" (")[0]: full[i][0].name for label, i in chooser_entries(full, NOW)}
            run_prune(folder, NOW, apply=True)
            thin = read_history(folder)
            self.assertLess(len(thin), len(full) / 10)
            after = {label.split(" (")[0]: thin[i][0].name for label, i in chooser_entries(thin, NOW)}
            fast = fast_history(folder, NOW)
            self.assertEqual([(l, fast[i][0].name) for l, i in chooser_entries(fast, NOW)],
                             [(l, thin[i][0].name) for l, i in chooser_entries(thin, NOW)])
            # Exact same snapshot: Latest, Before last reboot, 15 min, 1 hour, and every previous day.
            exact = [label for label in before if label != "3 hours ago" and not label.startswith("Today")]
            self.assertTrue(any(label.startswith("Yesterday") for label in exact))
            for label in exact:
                self.assertEqual(after.get(label), before[label], label)
            # 3 hours ago and first-today still offered, now at the nearest kept hourly snapshot.
            self.assertIn("3 hours ago", after)
            self.assertTrue(any(label.startswith("Today") for label in after))
            self.assertEqual(len(after), len(before))


class RecorderTests(unittest.TestCase):
    def setUp(self):
        keeper_prune.reset_schedule()

    def test_runs_at_most_once_per_hour(self):
        clock = [1000.0]
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(keeper_prune, "run_prune", return_value={"deleted": 0, "errors": []}) as run:
            for at in (1000.0, 1000.0 + 1800, 1000.0 + 3599):
                clock[0] = at
                prune_if_due(folder, clock=lambda: clock[0], lock_name="unittest-prune")
            self.assertEqual(run.call_count, 1)
            clock[0] = 1000.0 + 3600
            prune_if_due(folder, clock=lambda: clock[0], lock_name="unittest-prune")
            self.assertEqual(run.call_count, 2)
            self.assertTrue(run.call_args.kwargs.get("apply"))

    def test_no_prune_while_restore_lock_held(self):
        with tempfile.TemporaryDirectory() as folder:
            old = [write(folder, NOW - timedelta(days=50, minutes=m)) for m in (0, 2, 4)]
            write(folder, datetime.now().astimezone() - timedelta(minutes=1))
            write(folder, datetime.now().astimezone())
            with exclusive_operation("unittest-prune"):
                prune_if_due(folder, clock=lambda: 5.0, lock_name="unittest-prune")
            self.assertTrue(all(path.exists() for path in old))
            prune_if_due(folder, clock=lambda: 6.0, lock_name="unittest-prune")   # lock free: not gated by the skip
            self.assertFalse(any(path.exists() for path in old))

    def test_default_lock_is_the_restore_lock(self):
        names = []

        @contextmanager
        def fake(name):
            names.append(name)
            raise RuntimeError(f"Session Keeper {name} is already running")
            yield

        with tempfile.TemporaryDirectory() as folder, patch.object(keeper_prune, "exclusive_operation", fake), \
                patch.object(keeper_prune, "run_prune") as run:
            prune_if_due(folder, clock=lambda: 1.0)
        self.assertEqual(names, ["restore"])
        run.assert_not_called()

    def test_errors_are_logged_never_raised(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(keeper_prune, "run_prune", side_effect=ValueError("boom")):
            prune_if_due(folder, clock=lambda: 1.0, lock_name="unittest-prune")
            self.assertIn("boom", (Path(folder) / "watcher_errors.log").read_text(encoding="utf-8"))

    def test_recorder_prunes_only_after_successful_snapshot(self):
        with patch.object(keeper_app, "snapshot_once", return_value=None), \
                patch.object(keeper_app, "prune_if_due") as due:
            self.assertIsNone(keeper_app.record_once())
            due.assert_not_called()
        with patch.object(keeper_app, "snapshot_once", return_value=Path("x.json")), \
                patch.object(keeper_app, "prune_if_due") as due:
            self.assertEqual(keeper_app.record_once(), Path("x.json"))
            due.assert_called_once()

    def test_watch_uses_record_once(self):
        root = MagicMock()
        root.state.return_value = "withdrawn"
        with patch.object(keeper_app, "run_background") as background:
            keeper_app.watch(root)
        self.assertIs(background.call_args.args[1], keeper_app.record_once)


class ClockGuardTests(unittest.TestCase):
    def setUp(self):
        keeper_prune.reset_schedule()

    def auto(self, folder):
        prune_if_due(folder, clock=lambda: 1.0, lock_name="unittest-prune")

    def old_and_recent(self, folder, newest_age):
        now = datetime.now().astimezone()
        old = [write(folder, now - timedelta(days=50, minutes=m)) for m in (0, 2, 4)]
        write(folder, now - newest_age - timedelta(minutes=1))
        write(folder, now - newest_age)
        return old

    def test_forward_jump_skips_and_logs_one_line(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime.now().astimezone()
            old = [write(folder, now - timedelta(days=370, minutes=m)) for m in (0, 2, 4)]
            write(folder, now)
            self.auto(folder)
            self.assertTrue(all(p.exists() for p in old))
            log = (Path(folder) / "watcher_errors.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(log), 1)
            self.assertIn("skipped", log[0])

    def test_backward_clock_skips(self):
        with tempfile.TemporaryDirectory() as folder:
            old = self.old_and_recent(folder, timedelta(minutes=-11))
            self.auto(folder)
            self.assertTrue(all(p.exists() for p in old))

    def test_small_future_skew_still_prunes(self):
        with tempfile.TemporaryDirectory() as folder:
            old = self.old_and_recent(folder, timedelta(minutes=-5))
            self.auto(folder)
            self.assertFalse(any(p.exists() for p in old))

    def test_normal_case_prunes(self):
        with tempfile.TemporaryDirectory() as folder:
            old = self.old_and_recent(folder, timedelta(minutes=1))
            self.auto(folder)
            self.assertFalse(any(p.exists() for p in old))
            self.assertFalse((Path(folder) / "watcher_errors.log").exists())

    def test_cap_respected(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime.now().astimezone()
            base = now - timedelta(days=100)
            old = [write(folder, base + timedelta(seconds=i)) for i in range(520 + 0)]
            write(folder, now - timedelta(minutes=1))
            write(folder, now)
            self.auto(folder)
            self.assertEqual(sum(1 for p in old if not p.exists()), keeper_prune.MAX_AUTO_DELETE)
            keeper_prune.reset_schedule()
            self.auto(folder)   # next hourly run finishes the rest
            self.assertEqual(sum(1 for p in old if p.exists()), 0)

    def test_manual_apply_blocked_when_clock_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime.now().astimezone()
            old = [write(folder, now - timedelta(days=370, minutes=m)) for m in (0, 2, 4)]
            write(folder, now)
            report = run_prune(folder, apply=True)
            self.assertTrue(all(p.exists() for p in old))
            self.assertEqual(report["deleted"], 0)
            self.assertIn("warning", report)
            text = keeper_prune.report_text(report)
            self.assertIn("WARNING", text)
            self.assertIn("--force", text)
            self.assertLess(text.index("WARNING"), text.index("snapshot files"))

    def test_manual_apply_force_deletes_and_warns(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime.now().astimezone()
            old = [write(folder, now - timedelta(days=370, minutes=m)) for m in (0, 2, 4)]
            write(folder, now)
            report = run_prune(folder, apply=True, force=True)
            self.assertFalse(any(p.exists() for p in old[1:]))
            self.assertIn("WARNING", keeper_prune.report_text(report))

    def test_dry_run_shows_warning_first(self):
        with tempfile.TemporaryDirectory() as folder:
            now = datetime.now().astimezone()
            for m in (0, 2, 4):
                write(folder, now - timedelta(days=370, minutes=m))
            write(folder, now)
            text = keeper_prune.report_text(run_prune(folder))
            self.assertTrue(text.startswith("WARNING"))


class CliTests(unittest.TestCase):
    def run_main(self, *args):
        with patch.object(keeper_app.sys, "argv", ["keeper_app.py", "prune", *args]), \
                patch.object(keeper_app, "run_prune_command") as command, \
                patch.object(keeper_app.tk, "Tk") as tk_root:
            keeper_app.main()
        tk_root.assert_not_called()
        return command

    def test_prune_is_dry_run_by_default(self):
        self.assertEqual(self.run_main().call_args.kwargs, {"apply": False, "force": False})

    def test_prune_apply(self):
        self.assertEqual(self.run_main("--apply").call_args.kwargs, {"apply": True, "force": False})

    def test_prune_apply_force(self):
        self.assertEqual(self.run_main("--apply", "--force").call_args.kwargs, {"apply": True, "force": True})

    def test_report_text_dry_run(self):
        with tempfile.TemporaryDirectory() as folder:
            write(folder, NOW - timedelta(days=50))
            write(folder, NOW - timedelta(days=50, minutes=2))
            write(folder, NOW)
            text = keeper_prune.report_text(run_prune(folder, NOW, apply=False))
        self.assertIn("DRY RUN", text)
        self.assertIn("delete: 2", text)


if __name__ == "__main__":
    unittest.main()

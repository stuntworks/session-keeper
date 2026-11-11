# Last modified: 2026-11-10 | Change: New. Independent-review fixes V1-V8 for the restore batch 3 | Full history: git log
"""Fakes only: no terminal is opened, no claude is launched, no window is moved, no clock is waited on."""

import json
import os
import subprocess
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import keeper_newwindow
import keeper_prune
import keeper_restore
import keeper_target
from keeper_crash_record import last_restore_done
from keeper_created import load_created_windows
from keeper_restore import restore_selected
from test_keeper_restore_batch3 import FakeClock, ID1, ID2, ID3, ID4, LOCK, fake_world, layout

SPOT = {"x": 10, "y": 20, "w": 900, "h": 700}


# WHAT IT DOES: Writes a pending launch for ID1 (group 0 of `snapshot`) `age` ago, as a timed-out restore would.
def pending(snapshot, age, known=(11,)):
    keeper_newwindow.record_pending("SessionKeeper-late", snapshot, snapshot["windows"][0], 0, ID1, "First",
                                    dict(SPOT), set(known), "B")
    log = keeper_newwindow.PENDING_LOG
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    entries[-1]["at"] = (datetime.now(timezone.utc) - age).isoformat()
    log.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def two_tab_layout():
    return layout((SPOT, [(ID1, "First"), (ID2, "Second")]))


# WHAT IT DOES: V1, a chat whose window is still starting is never launched a second time.
class StillStartingTests(unittest.TestCase):
    def test_second_restart_within_10_minutes_does_not_relaunch(self):
        snapshot = two_tab_layout()
        pending(snapshot, timedelta(minutes=3))
        world = fake_world(self, [])
        with patch("keeper_restore.find_new_window", return_value={"hwnd": 500}) as find:
            restored, warnings = restore_selected(snapshot, {"0:0", "0:1"}, lock_name=LOCK)
        world["launch_tab"].assert_not_called()          # neither the chat nor its group's other tab
        find.assert_not_called()
        self.assertEqual(restored, [])
        self.assertTrue(any("'First'" in w and "still starting" in w for w in warnings), warnings)

    def test_after_10_minutes_it_may_open_again(self):
        snapshot = two_tab_layout()
        pending(snapshot, timedelta(minutes=30))
        world = fake_world(self, [])
        with patch("keeper_restore.find_new_window", return_value={"hwnd": 500}):
            restore_selected(snapshot, {"0:0", "0:1"}, lock_name=LOCK)
        self.assertEqual(world["launch_tab"].call_count, 2)

    def test_timeout_message_does_not_say_press_restart_now(self):
        clock = FakeClock()
        with patch.object(keeper_newwindow, "clock", clock), patch.object(keeper_newwindow, "pause", clock.sleep):
            with self.assertRaises(keeper_newwindow.WindowNotSeen) as caught:
                keeper_newwindow.find_new_window(set(), "First", lambda: [], handles=lambda: set(), baseline=set())
        text = str(caught.exception)
        self.assertIn("still starting", text)
        self.assertNotIn("press Restart selected again;", text)


# WHAT IT DOES: V2, a window the owner opened by hand during the wait is never recorded, moved or maximised.
class HandOpenedWindowTests(unittest.TestCase):
    def owner_and_ours(self, ours_holds_chat):
        owner = {"hwnd": 800, "x": 0, "y": 0, "w": 800, "h": 600, "title": "First", "tabs": ["First"]}
        ours = {"hwnd": 801, "x": 0, "y": 0, "w": 800, "h": 600, "title": "Claude retitled it", "tabs": ["x"]}
        state = {"launched": False}
        groups = [{"hwnd": 801, "tabs": [{"outcome": "MATCHED", "sessionId": ID1}]}] if ours_holds_chat else []
        world = fake_world(self, lambda: [owner, ours] if state["launched"] else [], groups=groups)
        world["launch_tab"].side_effect = lambda name, row: state.update(launched=True)
        snapshot = layout((dict(SPOT, maximized=True), [(ID1, "First"), (ID2, "Second")]))
        with patch.object(keeper_newwindow, "terminal_handles",
                          side_effect=lambda: {800, 801} if state["launched"] else set()):
            restored, warnings = restore_selected(snapshot, {"0:0", "0:1"}, lock_name=LOCK)
        moved = [call[0][0] for call in world["place_new_window"].call_args_list]
        recorded = [o["hwnd"] for o in load_created_windows("B")]
        self.assertNotIn(800, moved)
        self.assertNotIn(800, recorded)
        return moved, recorded, restored

    def test_owner_window_with_matching_title_never_used_without_proof(self):
        moved, recorded, restored = self.owner_and_ours(ours_holds_chat=False)
        self.assertEqual((moved, recorded, restored), ([], [], []))

    def test_proven_window_is_used_even_when_retitled(self):
        moved, recorded, restored = self.owner_and_ours(ours_holds_chat=True)
        self.assertEqual((moved, recorded), ([801], [801]))


# WHAT IT DOES: V3, an old pending entry adopts nothing and costs no probe.
class OldPendingTests(unittest.TestCase):
    def test_two_hour_old_pending_adopts_nothing(self):
        snapshot = two_tab_layout()
        pending(snapshot, timedelta(hours=2))
        reads = []
        adopted = keeper_newwindow.adopt_late_windows(
            [], "B", lambda: reads.append(1) or [{"hwnd": 900}],
            lambda w: [{"hwnd": 900, "tabs": [{"outcome": "MATCHED", "sessionId": ID1}]}],
            place=lambda *a, **k: self.fail("moved a window from a stale pending launch"), warnings=[])
        self.assertEqual((adopted, reads), ([], []))
        self.assertEqual(keeper_newwindow.load_pending("B"), [])


# WHAT IT DOES: V4, an error after a tab was launched stops further adds; one before launch costs only that tab.
class AddLandingTests(unittest.TestCase):
    def run_adds(self, add_tab):
        snapshot = layout((SPOT, [(ID1, "Live"), (ID2, "A"), (ID3, "B"), (ID4, "C")]))
        live_window = dict(SPOT, hwnd=77, title="Live", tabs=["Live"])
        group = {"hwnd": 77, "tabs": [{"outcome": "MATCHED", "sessionId": ID1}]}
        fake_world(self, [live_window], live={ID1}, groups=[group])
        with patch.object(keeper_target, "add_tab", side_effect=add_tab) as add:
            restored, warnings = restore_selected(snapshot, {"0:1", "0:2", "0:3"}, lock_name=LOCK)
        return restored, warnings, add.call_count

    def test_probe_error_after_launch_stops_further_adds(self):
        def add_tab(hwnd, row, name, launch, wait, read):
            launch(name or "0", row)
            if row["title"] == "A":
                raise subprocess.TimeoutExpired("powershell", 45)     # the landing check's probe timed out
            return True
        restored, warnings, calls = self.run_adds(add_tab)
        self.assertEqual((restored, calls), ([], 1))
        self.assertTrue(any("'B'" in w and "Not added" in w for w in warnings), warnings)

    def test_error_before_launch_costs_one_tab(self):
        def add_tab(hwnd, row, name, launch, wait, read):
            if row["title"] == "A":
                raise RuntimeError("could not bring its window to the front, so nothing was opened")
            launch(name or "0", row)
            return True
        restored, warnings, calls = self.run_adds(add_tab)
        self.assertEqual((restored, calls), (["B", "C"], 3))


# WHAT IT DOES: V5, a window placed right but not maximised is a warning; the group carries on.
class MaximiseFailureTests(unittest.TestCase):
    def test_maximise_failure_group_completes_with_warning(self):
        world = fake_world(self, [])
        world["place_new_window"].return_value = False
        snapshot = layout((dict(SPOT, maximized=True), [(ID1, "First"), (ID2, "Second")]))
        with patch("keeper_restore.find_new_window", return_value={"hwnd": 500}):
            restored, warnings = restore_selected(snapshot, {"0:0", "0:1"}, lock_name=LOCK)
        self.assertEqual(restored, ["First", "Second"])
        self.assertTrue(any("maximise" in w for w in warnings), warnings)

    def test_place_script_exit_3_is_not_an_error(self):
        rect = dict(SPOT)
        with patch("keeper_restore.subprocess.run") as run:
            run.return_value.returncode = 3
            self.assertIs(keeper_restore.place_new_window(9, rect, set(), maximize=True), False)
            run.return_value.returncode = 0
            self.assertIs(keeper_restore.place_new_window(9, rect, set(), maximize=True), True)
            run.return_value.returncode = 1
            run.return_value.stderr = "Window landed too far"
            with self.assertRaises(RuntimeError):
                keeper_restore.place_new_window(9, rect, set(), maximize=True)


# WHAT IT DOES: V6, a hidden terminal-class window does not make the full probe run every cycle.
class BaselineTests(unittest.TestCase):
    def test_hidden_window_in_baseline_does_not_trigger_probe(self):
        clock, reads = FakeClock(), []
        with patch.object(keeper_newwindow, "clock", clock), patch.object(keeper_newwindow, "pause", clock.sleep):
            with self.assertRaises(keeper_newwindow.WindowNotSeen):
                keeper_newwindow.find_new_window({1, 2}, "First", lambda: reads.append(1) or [],
                                                 handles=lambda: {1, 2, 99}, baseline={1, 2, 99})
        self.assertEqual(reads, [])

    def test_open_group_passes_a_baseline_from_the_same_lister(self):
        fake_world(self, [])
        with patch.object(keeper_newwindow, "terminal_handles", return_value={1, 99}), \
             patch("keeper_restore.find_new_window", return_value={"hwnd": 500}) as find:
            restore_selected(two_tab_layout(), {"0:0"}, lock_name=LOCK)
        self.assertEqual(find.call_args.kwargs.get("baseline"), {1, 99})


# WHAT IT DOES: V7, a paused restore (nothing opened) does not clear the "Before the crash" preselect.
class RestoreDoneTests(unittest.TestCase):
    def test_paused_restore_does_not_write_restore_done(self):
        world = fake_world(self, [])
        world["read_windows"].side_effect = RuntimeError("A terminal window could not be inspected.")
        restored, warnings = restore_selected(two_tab_layout(), {"0:0"}, lock_name=LOCK)
        self.assertEqual(restored, [])
        self.assertIsNone(last_restore_done())

    def test_real_restore_writes_restore_done(self):
        fake_world(self, [])
        with patch("keeper_restore.find_new_window", return_value={"hwnd": 500}):
            restored, _ = restore_selected(two_tab_layout(), {"0:0"}, lock_name=LOCK)
        self.assertEqual(restored, ["First"])
        self.assertIsNotNone(last_restore_done())


# WHAT IT DOES: V8, leftover partial_*.tmp files are listed, and only the manual --apply deletes old ones.
class PartialFileTests(unittest.TestCase):
    def test_partial_files_listed_and_old_ones_deleted_on_apply(self):
        folder = Path(tempfile.mkdtemp(prefix="keeper_review_"))
        self.addCleanup(shutil.rmtree, folder, True)
        now = datetime.now(timezone.utc)

        def write(when):
            path = folder / f"partial_snapshot_{when.strftime('%Y%m%dT%H%M%S%fZ')}_{os.urandom(4).hex()}.tmp"
            path.write_text('{"version": 1', encoding="utf-8")
            os.utime(path, (when.timestamp(), when.timestamp()))
            return path
        old, new = write(now - timedelta(days=2)), write(now - timedelta(hours=1))
        other = folder / "partial_other.tmp"
        other.write_text("x", encoding="utf-8")
        os.utime(other, (0, 0))
        report = keeper_prune.run_prune(folder, now=now)
        self.assertEqual(sorted(report["partials"]), sorted([old.name, new.name]))
        self.assertIn(old.name, keeper_prune.report_text(report))
        keeper_prune.run_prune(folder, now=now, apply=True, automatic=True)
        self.assertTrue(old.exists())
        report = keeper_prune.run_prune(folder, now=now, apply=True)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists() and other.exists())
        self.assertEqual(report["partials_deleted"], [old.name])


if __name__ == "__main__":
    unittest.main()

# Last modified: 2026-10-07 | Change: Tests for strict-ID race and partly restored windows | Full history: git log
"""Fakes only: no terminal is opened, no claude is launched."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import keeper_restore
from keeper_core import plan_restore
from keeper_created import group_key, load_created_windows, record_created_window
from keeper_restore import live_session_ids, restore_selected

ID1 = "11111111-1111-1111-1111-111111111111"
ID2 = "22222222-2222-2222-2222-222222222222"


# WHAT IT DOES: Builds a two-tab saved window for the fix tests.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def make_snapshot():
    return {"captured_at": "T1", "windows": [{"x": 10, "y": 20, "w": 900, "h": 700, "tabs": [
        {"outcome": "MATCHED", "sessionId": ID1, "strippedTitle": "First", "cwd": "C:\\Users\\example"},
        {"outcome": "MATCHED", "sessionId": ID2, "strippedTitle": "Second", "cwd": "C:\\Users\\example"}]}]}


# WHAT IT DOES: Bug 1, the strict check must give a just-launched process time to register.
# DEPENDS ON: Fake inventory.
# AFFECTS: Without it only one tab of a window opens.
class StrictWaitTests(unittest.TestCase):
    def test_waits_until_uncertain_clears(self):
        answers = [[{"uncertain": True}], [{"uncertain": True}], [{"sessionId": ID1}]]
        with patch("keeper_inventory.live_inventory", side_effect=answers), \
             patch.object(keeper_restore, "STRICT_POLL_SECONDS", 0.01):
            self.assertEqual(live_session_ids(strict=True), {ID1})

    def test_still_uncertain_after_timeout_fails_closed(self):
        with patch("keeper_inventory.live_inventory", return_value=[{"uncertain": True}]), \
             patch.object(keeper_restore, "STRICT_WAIT_SECONDS", 0.05), \
             patch.object(keeper_restore, "STRICT_POLL_SECONDS", 0.01):
            with self.assertRaises(RuntimeError):
                live_session_ids(strict=True)

    # WHAT IT DOES: A strict failure for tab 2 stops the group with a warning, no exception.
    def test_strict_failure_midgroup_is_a_warning(self):
        calls = {"n": 0}

        def fake_ids(strict=False):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise RuntimeError("An open CLI session could not be identified.")
            return set()
        with patch("keeper_restore.live_session_ids", side_effect=fake_ids), \
             patch("keeper_restore.read_windows", return_value=[]), \
             patch("keeper_restore.launch_tab") as launch, \
             patch("keeper_restore.find_new_window", return_value={"hwnd": 500}), \
             patch("keeper_restore.place_new_window"), \
             patch("keeper_restore.wait_for_session", return_value=True), \
             patch("keeper_restore.load_created_windows", return_value=[]), \
             patch("keeper_restore.record_created_window"), \
             patch("keeper_restore.read_boot_id", return_value="B"), \
             patch("keeper_restore.time.sleep"):
            restored, warnings = restore_selected(make_snapshot(), {"0:0", "0:1"})
        self.assertEqual(restored, ["First"])
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(any("could not be identified" in w for w in warnings))


# WHAT IT DOES: Bug 2, finish a window Session Keeper itself left half restored.
# DEPENDS ON: plan_restore and perform_restore with fakes.
# AFFECTS: Without it tabs 2-4 of a half-restored window are stranded.
class PartialWindowTests(unittest.TestCase):
    owned = [{"window_name": "SessionKeeper-abc", "hwnd": 77, "group": "T1#0"}]

    def occupant(self, hwnd=77, tabs=("First",)):
        return {"hwnd": hwnd, "x": 10, "y": 20, "w": 900, "h": 700, "title": "First", "tabs": list(tabs)}

    def test_own_window_same_group_is_reused(self):
        plans, warnings = plan_restore(make_snapshot(), {"0:1"}, {ID1}, [self.occupant()],
                                       owned_windows=self.owned)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["window_name"], "SessionKeeper-abc")
        self.assertEqual([r["title"] for r in plans[0]["tabs"]], ["Second"])
        self.assertFalse(any("occupied" in w for w in warnings))

    def test_unknown_window_still_skipped(self):
        plans, warnings = plan_restore(make_snapshot(), {"0:1"}, {ID1}, [self.occupant(hwnd=99)],
                                       owned_windows=self.owned)
        self.assertEqual(plans, [])
        self.assertTrue(any("occupied" in w for w in warnings))

    def test_other_group_or_foreign_tab_still_skipped(self):
        other = [{"window_name": "SessionKeeper-abc", "hwnd": 77, "group": "T1#5"}]
        plans, _ = plan_restore(make_snapshot(), {"0:1"}, {ID1}, [self.occupant()], owned_windows=other)
        self.assertEqual(plans, [])
        plans, warnings = plan_restore(make_snapshot(), {"0:1"}, {ID1},
                                       [self.occupant(tabs=("First", "Unrelated shell"))],
                                       owned_windows=self.owned)
        self.assertEqual(plans, [])
        self.assertTrue(any("occupied" in w for w in warnings))

    def test_two_windows_in_the_spot_still_skipped(self):
        plans, _ = plan_restore(make_snapshot(), {"0:1"}, {ID1},
                                [self.occupant(), self.occupant(hwnd=99, tabs=("x",))],
                                owned_windows=self.owned)
        self.assertEqual(plans, [])

    def test_restore_adds_missing_tab_into_existing_window(self):
        with patch("keeper_restore.live_session_ids", return_value={ID1}), \
             patch("keeper_restore.read_windows", return_value=[self.occupant()]), \
             patch("keeper_restore.load_created_windows", return_value=self.owned), \
             patch("keeper_restore.read_boot_id", return_value="B"), \
             patch("keeper_restore.launch_tab") as launch, \
             patch("keeper_restore.find_new_window") as find, \
             patch("keeper_restore.place_new_window") as place, \
             patch("keeper_restore.wait_for_session", return_value=True), \
             patch("keeper_restore.time.sleep"):
            restored, warnings = restore_selected(make_snapshot(), {"0:1"})
        self.assertEqual(restored, ["Second"])
        self.assertEqual(launch.call_args.args[0], "SessionKeeper-abc")
        find.assert_not_called()
        place.assert_not_called()

    def test_new_window_is_recorded(self):
        with patch("keeper_restore.live_session_ids", return_value=set()), \
             patch("keeper_restore.read_windows", return_value=[]), \
             patch("keeper_restore.load_created_windows", return_value=[]), \
             patch("keeper_restore.read_boot_id", return_value="B"), \
             patch("keeper_restore.record_created_window") as record, \
             patch("keeper_restore.launch_tab"), \
             patch("keeper_restore.find_new_window", return_value={"hwnd": 500}), \
             patch("keeper_restore.place_new_window"), \
             patch("keeper_restore.wait_for_session", return_value=True), \
             patch("keeper_restore.time.sleep"):
            restore_selected(make_snapshot(), {"0:0", "0:1"})
        name, hwnd, group, boot = record.call_args.args
        self.assertTrue(name.startswith("SessionKeeper-"))
        self.assertEqual((hwnd, group, boot), (500, "T1#0", "B"))

    def test_record_file_is_append_only_and_boot_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "created.jsonl"
            record_created_window("SessionKeeper-a", 1, group_key(make_snapshot(), 0), "B1", path)
            record_created_window("SessionKeeper-b", 2, "T1#0", "B2", path)
            self.assertEqual([e["window_name"] for e in load_created_windows("B1", path)], ["SessionKeeper-a"])
            self.assertEqual(len(path.read_text().splitlines()), 2)
            self.assertEqual(load_created_windows("B1", Path(folder) / "none.jsonl"), [])


if __name__ == "__main__":
    unittest.main()

# Last modified: 2026-10-22 | Change: Fix-it restore test takes a throwaway lock | Full history: git log
"""Owner rule 2026-10-22: no bare "paused" or "skipped". Each stop names the tab (and monitor where a
window is involved) and says what to do, plus the go-ahead button where one exists."""

import unittest
from unittest.mock import patch

import keeper_restore
import keeper_target
from keeper_core import monitor_label, plan_restore

ID1 = "11111111-1111-1111-1111-111111111111"
ID2 = "22222222-2222-2222-2222-222222222222"
MONITORS = [{"x": 0, "y": 0, "w": 1920, "h": 1080, "primary": True}, {"x": 1920, "y": 0, "w": 1920, "h": 1080}]


def saved(rect=None):
    rect = rect or {"x": 2000, "y": 20, "w": 900, "h": 700}
    return {"captured_at": "T1", "windows": [{**rect, "tabs": [
        {"outcome": "MATCHED", "sessionId": ID1, "strippedTitle": "First", "cwd": "C:\\Work"},
        {"outcome": "MATCHED", "sessionId": ID2, "strippedTitle": "Second", "cwd": "C:\\Work"}]}]}


def has_fix(line):
    return "To fix" in line or "Nothing to fix" in line


class MonitorTests(unittest.TestCase):
    def test_monitor_label(self):
        self.assertEqual(monitor_label({"x": 2000, "y": 20, "w": 900, "h": 700}, MONITORS), "monitor 2")
        self.assertEqual(monitor_label({"x": 10, "y": 20, "w": 900, "h": 700}, MONITORS), "monitor 1")
        self.assertEqual(monitor_label({"x": 10, "y": 20, "w": 900, "h": 700}, None), "")


class PlannerMessageTests(unittest.TestCase):
    def test_on_top_line_names_the_covering_window_and_monitor(self):
        covering = [{"hwnd": 9, "x": 2000, "y": 20, "w": 900, "h": 700, "tabs": ["PowerShell"]}]
        plans, warnings = plan_restore(saved(), {"0:0"}, set(), covering, MONITORS)
        self.assertIn("PowerShell", plans[0]["covering"])
        self.assertIn("monitor 2", plans[0]["covering"])

    def test_missing_position_says_how_to_fix(self):
        plans, warnings = plan_restore(saved({"x": None, "y": 0, "w": 0, "h": 0}), {"0:0"}, set(), [], MONITORS)
        self.assertEqual(plans, [])
        self.assertTrue(has_fix(warnings[0]) and "First" in warnings[0] and "Copy command" in warnings[0])

    def test_already_open_says_nothing_to_fix(self):
        plans, warnings = plan_restore(saved(), {"0:0"}, {ID1}, [], MONITORS)
        self.assertTrue("already open" in warnings[0] and has_fix(warnings[0]))

    def test_split_home_says_how_to_fix(self):
        groups = [{"hwnd": 1, "x": 0, "y": 0, "w": 900, "h": 700, "tabs": [{"sessionId": ID1, "outcome": "MATCHED"}]},
                  {"hwnd": 2, "x": 0, "y": 0, "w": 900, "h": 700, "tabs": [{"sessionId": ID1, "outcome": "MATCHED"}]}]
        with patch("keeper_home.home_window", return_value=__import__("keeper_home").SPLIT):
            plans, warnings = plan_restore(saved(), {"0:1"}, {ID1}, [], MONITORS, live_groups=groups)
        self.assertEqual(plans, [])
        self.assertTrue(any(has_fix(w) and "Second" in w for w in warnings))


class RestoreMessageTests(unittest.TestCase):
    def test_stop_line_for_plain_error_says_how_to_fix(self):
        line = keeper_restore.stop_line("First", RuntimeError("Terminal probe failed"))
        self.assertTrue("First" in line and has_fix(line) and "Restart selected" in line)

    def test_stop_line_for_unidentified_keeps_its_own_fix(self):
        error = keeper_restore.UnidentifiedSessions([{"kind": "claude", "title": "Crypto", "processCwd": "C:\\X"}])
        line = keeper_restore.stop_line("First", error)
        self.assertIn("Crypto", line)
        self.assertIn("type a message", line)

    def test_monitor_change_says_how_to_fix(self):
        reads = iter([MONITORS, MONITORS[:1]])
        with patch("keeper_restore.read_monitors", side_effect=lambda: next(reads)), \
             patch("keeper_restore.live_session_ids", return_value=set()), \
             patch("keeper_restore.read_windows", return_value=[]), \
             patch("keeper_restore.load_created_windows", return_value=[]), \
             patch("keeper_restore.read_boot_id", return_value="B"):
            restored, warnings = keeper_restore.restore_selected(saved(), {"0:0"}, lock_name="unittest-restore")
        self.assertTrue(any("monitor" in w.lower() and has_fix(w) for w in warnings))

    def test_unverified_tab_says_how_to_fix(self):
        line = keeper_restore.unverified_line("First")
        self.assertTrue("First" in line and has_fix(line))

    def test_missing_folder_error_names_the_folder(self):
        row = {"session_id": ID1, "cwd": "C:\\Definitely\\Missing\\Folder", "title": "First", "kind": "claude"}
        with self.assertRaises(ValueError) as caught:
            keeper_restore.launch_tab("w", row)
        self.assertIn("C:\\Definitely\\Missing\\Folder", str(caught.exception))
        self.assertIn("To fix", str(caught.exception))

    def test_target_missed_says_how_to_fix(self):
        with patch.object(keeper_target, "tab_counts", return_value={5: 1}), \
             patch.object(keeper_target, "landed_in", return_value=False):
            with self.assertRaises(keeper_target.TargetMissed) as caught:
                keeper_target.add_tab(5, {"title": "First", "session_id": ID1}, "named", lambda *a: None,
                                      lambda sid: True, lambda: [])
        self.assertIn("To fix", str(caught.exception))


class AppMessageTests(unittest.TestCase):
    def test_plain_failure_panel_says_how_to_fix(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        import keeper_app
        drawer = MagicMock()
        screen = SimpleNamespace(busy=True, countdown_text=MagicMock(), map=SimpleNamespace(drawer=drawer),
                                 selection_changed=MagicMock(), begin_operation=MagicMock(),
                                 command_entries=MagicMock(return_value=[]))
        keeper_app.RecoveryWindow.operation_failed(screen, RuntimeError("Terminal probe failed"))
        notes = drawer.show_message.call_args.args[2]
        self.assertTrue(any(has_fix(note) for note in notes))


if __name__ == "__main__":
    unittest.main()

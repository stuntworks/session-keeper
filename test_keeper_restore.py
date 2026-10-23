# Last modified: 2026-10-22 | Change: Restore tests take a throwaway lock, not the live recorder's | Full history: git log
"""Restore flow tests use fakes so user terminals are never touched."""

import unittest
from unittest.mock import patch

from keeper_restore import clean_environment, restore_selected


# WHAT IT DOES: Checks the restore workflow using one disposable saved layout.
# DEPENDS ON: unittest mocks and keeper_restore.
# AFFECTS: Catches accidental launches or moves of existing windows.
class RestoreTests(unittest.TestCase):
    # WHAT IT DOES: Creates a two-tab saved terminal for fake restore tests.
    # ISOLATED
    def make_snapshot(self):
        return {"windows": [{"x": 10, "y": 20, "w": 900, "h": 700, "tabs": [
            {"outcome": "MATCHED", "sessionId": "11111111-1111-1111-1111-111111111111",
             "strippedTitle": "First", "cwd": "C:\\Users\\example"},
            {"outcome": "MATCHED", "sessionId": "22222222-2222-2222-2222-222222222222",
             "strippedTitle": "Second", "cwd": "C:\\Users\\example"},
        ]}]}

    # WHAT IT DOES: Ensures inherited Claude child markers do not reach resumed sessions.
    # DEPENDS ON: Environment filter.
    # AFFECTS: Transcript recording in recovered sessions.
    def test_clean_environment(self):
        with patch.dict("os.environ", {"CLAUDE_CODE_CHILD_SESSION": "1", "CODEX_THREAD_ID": "old", "OTHER": "yes"}):
            result = clean_environment()
        self.assertNotIn("CLAUDE_CODE_CHILD_SESSION", result)
        self.assertNotIn("CODEX_THREAD_ID", result)
        self.assertEqual(result["OTHER"], "yes")

    # WHAT IT DOES: Proves a dry run reports tabs but never launches or moves windows.
    # DEPENDS ON: Fake empty screen and process inventory.
    # AFFECTS: Preview button must be safe.
    def test_dry_run_never_launches(self):
        with patch("keeper_restore.live_session_ids", return_value=set()), \
             patch("keeper_restore.read_windows", return_value=[]), \
             patch("keeper_restore.launch_tab") as launch:
            restored, warnings = restore_selected(self.make_snapshot(), {"0:0", "0:1"}, True,
                                                 lock_name="unittest-restore")
        self.assertEqual(restored, ["First", "Second"])
        self.assertEqual(warnings, [])
        launch.assert_not_called()

    # WHAT IT DOES: Confirms both tabs use one new window and only its new handle is moved.
    # DEPENDS ON: Fake launch, window detection, and positioner.
    # AFFECTS: Grouping and placement after a crash.
    def test_group_uses_one_new_window(self):
        with patch("keeper_restore.live_session_ids", return_value=set()), \
             patch("keeper_restore.read_windows", return_value=[]), \
             patch("keeper_restore.launch_tab") as launch, \
             patch("keeper_restore.find_new_window", return_value={"hwnd": 500}), \
             patch("keeper_restore.place_new_window") as place, \
             patch("keeper_restore.wait_for_session", return_value=True), \
             patch("keeper_restore.time.sleep"):
            restored, warnings = restore_selected(self.make_snapshot(), {"0:0", "0:1"},
                                                 lock_name="unittest-restore")
        self.assertEqual(restored, ["First", "Second"])
        self.assertEqual(warnings, [])
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(launch.call_args_list[0].args[0], launch.call_args_list[1].args[0])
        self.assertEqual(place.call_args.args[0], 500)


if __name__ == "__main__":
    unittest.main()

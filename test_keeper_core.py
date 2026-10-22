# Last modified: 2026-10-03 | Change: Test recovery decisions before live restore | Full history: git log
"""Focused tests for Session Keeper's snapshot selection and safety gates."""

import tempfile
import unittest
from pathlib import Path

from keeper_core import recovery_rows, save_snapshot, plan_restore


# WHAT IT DOES: Proves snapshot history and recovery decisions with disposable data.
# DEPENDS ON: keeper_core and Python's unittest library.
# AFFECTS: These tests guard against duplicate or misplaced recovery windows.
class KeeperCoreTests(unittest.TestCase):
    # WHAT IT DOES: Builds a small saved layout with one known tab and one unknown tab.
    # ISOLATED
    def make_snapshot(self):
        return {"version": 1, "boot_id": "old-boot", "windows": [
            {"x": 100, "y": 100, "w": 900, "h": 700, "tabs": [
                {"outcome": "MATCHED", "sessionId": "11111111-1111-1111-1111-111111111111",
                 "strippedTitle": "ProjectA", "cwd": "C:\\Users\\example"},
                {"outcome": "NONE", "strippedTitle": "Unknown Codex"},
            ]},
        ]}

    # WHAT IT DOES: Proves a new snapshot never overwrites an older one.
    # DEPENDS ON: Snapshot saving.
    # AFFECTS: An overwrite would erase the pre-crash layout recovery needs.
    def test_snapshots_are_immutable(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            old_path = save_snapshot(self.make_snapshot(), directory)
            new_path = save_snapshot(self.make_snapshot(), directory)
            self.assertNotEqual(old_path, new_path)
            self.assertTrue(old_path.exists())

    # WHAT IT DOES: Keeps unknown tabs visible in the snapshot but ineligible for automatic restore.
    # DEPENDS ON: Recovery row creation.
    # AFFECTS: A guessed session ID would reopen the wrong conversation.
    def test_unknown_tab_is_not_restored(self):
        self.assertEqual(len(recovery_rows(self.make_snapshot())), 1)

    # WHAT IT DOES: Skips a session that is already live.
    # DEPENDS ON: Restore planner.
    # AFFECTS: Prevents duplicate Claude conversations.
    def test_live_session_is_skipped(self):
        snapshot = self.make_snapshot()
        plans, warnings = plan_restore(snapshot, {"0:0"},
            {"11111111-1111-1111-1111-111111111111"}, [])
        self.assertEqual(plans, [])
        self.assertIn("already open", warnings[0])

    # WHAT IT DOES: Skips a saved position when a terminal already covers it.
    # DEPENDS ON: Current window geometry.
    # AFFECTS: Prevents a newly opened window hiding one already on screen.
    def test_occupied_position_is_skipped(self):
        plans, warnings = plan_restore(self.make_snapshot(), {"0:0"}, set(),
            [{"x": 200, "y": 200, "w": 800, "h": 600}])
        self.assertEqual(plans, [])
        self.assertIn("occupied", warnings[0])


if __name__ == "__main__":
    unittest.main()

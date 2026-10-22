# Last modified: 2026-10-03 | Change: Check monitor fallback and the proposed single-setting edit | Full history: git log
"""Tests use small in-memory examples; Windows settings are not written."""

import unittest
from configure_terminal import proposed_settings
from keeper_display import keep_visible


# WHAT IT DOES: Verifies the last setup choices cannot silently alter unrelated settings or hide windows.
# DEPENDS ON: Pure settings transformation and rectangle logic.
# AFFECTS: Protects startup configuration and disconnected-monitor recovery.
class SetupTests(unittest.TestCase):
    # WHAT IT DOES: Preserves every byte except the one approved setting value.
    # ISOLATED
    def test_only_restore_value_changes(self):
        source = b'{\r\n "firstWindowPreference": "persistedWindowLayout",\r\n "profiles": [1,2]\r\n}'
        self.assertEqual(proposed_settings(source), source.replace(b"persistedWindowLayout", b"defaultProfile"))

    # WHAT IT DOES: Refuses a file containing two possible properties instead of guessing which to edit.
    # ISOLATED
    def test_duplicate_setting_refused(self):
        with self.assertRaises(RuntimeError):
            proposed_settings(b'{"firstWindowPreference":"persistedWindowLayout","firstWindowPreference":"defaultProfile"}')

    # WHAT IT DOES: A bad or duplicate-key settings file never raises out of the login check; it counts as "might be on".
    # ISOLATED
    def test_bad_file_never_raises_and_counts_as_possibly_on(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import configure_terminal
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "settings.json"
            bad.write_bytes(b'{"firstWindowPreference":"defaultProfile","firstWindowPreference":"defaultProfile"}')
            with patch.object(configure_terminal, "settings_files", return_value=[("x", bad)]),                     patch.object(configure_terminal, "BAD_FILE_LOG", Path(folder) / "watcher_errors.log"):
                self.assertTrue(configure_terminal.terminal_restore_enabled())
                self.assertIn("duplicate", (Path(folder) / "watcher_errors.log").read_text(encoding="utf-8"))

    # WHAT IT DOES: The CLI reports a bad file in its own entry and still handles the others.
    # ISOLATED
    def test_cli_message_for_bad_file(self):
        import tempfile
        from pathlib import Path
        import configure_terminal
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "bad.json"
            bad.write_bytes(b'{"firstWindowPreference":"persistedWindowLayout","firstWindowPreference":"x"}')
            good = Path(folder) / "good.json"
            good.write_bytes(b'{"firstWindowPreference": "persistedWindowLayout"}')
            reports, code = configure_terminal.run(False, [("bad", bad), ("good", good)], Path(folder) / "b")
            self.assertEqual(code, 1)
            self.assertIn("error", reports[0])
            self.assertNotIn("error", reports[1])
            self.assertIn("bad.json", configure_terminal.describe(reports[0]))

    # WHAT IT DOES: Preserves a saved window that is visible on the second monitor.
    # ISOLATED
    def test_visible_position_kept(self):
        rectangle = {"x": 4000, "y": 50, "w": 800, "h": 600}
        monitors = [{"x": 3840, "y": 0, "w": 3840, "h": 2088}]
        self.assertEqual(keep_visible(rectangle, monitors), rectangle)

    # WHAT IT DOES: Brings an off-screen window to the remaining monitor and fits its work area.
    # ISOLATED
    def test_missing_monitor_falls_back(self):
        rectangle = {"x": 9000, "y": 0, "w": 3000, "h": 2000}
        monitors = [{"x": 0, "y": 0, "w": 1920, "h": 1040, "primary": True}]
        self.assertEqual(keep_visible(rectangle, monitors), {"x": 0, "y": 0, "w": 1920, "h": 1040})


if __name__ == "__main__":
    unittest.main()

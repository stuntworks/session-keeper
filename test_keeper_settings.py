# Last modified: 2026-10-03 | Change: Test opt-in restore setting history | Full history: git log
"""Disposable settings tests leave live preferences untouched."""

import tempfile
import unittest
from pathlib import Path

from keeper_settings import auto_restore_enabled, record_auto_restore


# WHAT IT DOES: Checks that auto restore defaults off and honors the latest choice.
# DEPENDS ON: Temporary event file and keeper_settings.
# AFFECTS: Startup behavior should never silently opt itself in.
class SettingsTests(unittest.TestCase):
    # WHAT IT DOES: Appends two choices and reads the latest one.
    # DEPENDS ON: Temporary directory.
    # AFFECTS: Enables explicit opt-in and a later explicit opt-out.
    def test_append_only_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.jsonl"
            self.assertFalse(auto_restore_enabled(path))
            record_auto_restore(True, path)
            self.assertTrue(auto_restore_enabled(path))
            record_auto_restore(False, path)
            self.assertFalse(auto_restore_enabled(path))
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()

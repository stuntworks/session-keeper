# Last modified: 2026-11-10 | Change: New. Tests can never write the real data folder or take the real locks | Full history: git log
"""Review item T1 (2026-11-10): a test run overwrote the LIVE data/watch_heartbeat.json and appended fake
`hwnd 500` lines to the LIVE data/created_windows.jsonl. conftest.py now redirects every data path to a
per-test temp folder and blocks any write under the real one. These tests prove the guard is in place
WITHOUT writing anything real: the write probe is only tried after the guard is confirmed installed."""

import builtins
import hashlib
import os
import unittest
from pathlib import Path

import keeper_core
import keeper_created
import keeper_locks
import keeper_prune
import keeper_settings
import keeper_single

HERE = Path(__file__).resolve().parent
REAL_DATA = str(HERE / "data")      # text, not a Path: conftest redirects Path values, never text


# WHAT IT DOES: True when a path is the real data folder or inside it.
# AFFECTS: Pure.
def is_real(path):
    real = os.path.normcase(str(REAL_DATA))
    path = os.path.normcase(str(Path(path).resolve()))
    return path == real or path.startswith(real + os.sep)


# WHAT IT DOES: Every data path a module could write by default points into a temp folder, not the real one.
# DEPENDS ON: conftest.py's autouse fixture.
# AFFECTS: Nothing; reads module attributes and function defaults only.
class IsolationTests(unittest.TestCase):
    def test_module_data_paths_are_redirected(self):
        for value in (keeper_core.DATA, keeper_created.CREATED_LOG, keeper_settings.SETTINGS_EVENTS,
                      keeper_single.INFO):
            self.assertFalse(is_real(value), value)

    def test_default_arguments_are_redirected(self):
        for function in (keeper_core.save_snapshot, keeper_prune.run_prune, keeper_prune.prune_if_due):
            for value in function.__defaults__ or ():
                if isinstance(value, Path):
                    self.assertFalse(is_real(value), (function.__name__, value))

    def test_writes_under_the_real_data_folder_are_refused(self):
        guard = getattr(builtins.open, "keeper_write_guard", False)
        self.assertTrue(guard, "conftest write guard is not installed")
        probe = Path(REAL_DATA) / "isolation_probe_never_created.tmp"
        with self.assertRaises(PermissionError):
            with open(probe, "a", encoding="utf-8"):
                pass
        with self.assertRaises(PermissionError):
            probe.write_text("x", encoding="utf-8")
        self.assertFalse(probe.exists())
        import conftest
        del conftest.VIOLATIONS[-2:]    # the two refusals above were on purpose; nothing reached disk

    def test_lock_names_are_not_the_real_ones(self):
        user_key = hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
        for operation in ("watch", "restore", "restore-capture", "watch-capture"):
            self.assertNotEqual(keeper_locks.lock_name(operation), f"Local\\SessionKeeper-{user_key}-{operation}")
        self.assertNotEqual(keeper_single.screen_lock_name(), f"Local\\SessionKeeper-{user_key}-recover-screen")


if __name__ == "__main__":
    unittest.main()

# Last modified: 2026-10-22 | Change: Release blockers: never guess the folder, all Terminal settings locations, fast login read | Full history: git log
"""Fake data in temporary folders only. Nothing is launched and no real snapshot is touched."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import configure_terminal
import keeper_history
from keeper_codex import active_codex_sessions
from keeper_copycmd import build_resume_text
from keeper_core import recovery_rows
from keeper_restore import launch_tab
from keeper_tabdetails import tab_details

ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
GOOD = "persistedWindowLayout"


def snapshot(cwd):
    tab = {"outcome": "MATCHED", "sessionId": ID, "strippedTitle": "T", "kind": "claude"}
    if cwd is not None:
        tab["cwd"] = cwd
    return {"version": 1, "captured_at": "2026-10-20T00:00:00+00:00", "windows": [{"tabs": [tab]}]}


class NeverGuessTheFolder(unittest.TestCase):
    def test_tab_without_folder_is_not_a_restorable_row(self):
        for missing in (None, ""):
            self.assertEqual(recovery_rows(snapshot(missing)), [])

    def test_tab_with_folder_still_a_row(self):
        self.assertEqual(recovery_rows(snapshot("C:/x"))[0]["cwd"], "C:/x")

    def test_tab_details_for_folderless_tab_cannot_copy(self):
        snap = snapshot(None)
        info = tab_details(snap, "0:0", {row["key"]: row for row in recovery_rows(snap)}, set())
        self.assertFalse(info["can_copy"])
        self.assertEqual(info["command"], "")
        self.assertIn("folder", info["note"].lower())

    def test_copy_command_refuses_missing_folder(self):
        for missing in (None, ""):
            with self.assertRaisesRegex(ValueError, "folder"):
                build_resume_text([{"session_id": ID, "kind": "claude", "title": "T", "cwd": missing}])

    def test_launch_tab_refuses_missing_folder(self):
        for missing in (None, ""):
            with patch("keeper_restore.subprocess.Popen") as popen:
                with self.assertRaises(ValueError):
                    launch_tab("w", {"session_id": ID, "kind": "claude", "title": "T", "cwd": missing})
                popen.assert_not_called()

    def test_codex_process_without_folder_reports_unknown_not_home(self):
        process = SimpleNamespace(pid=9, info={"name": "codex.exe", "cmdline": ["codex.exe"], "cwd": None},
                                  open_files=lambda: [])
        process.as_dict = lambda attrs: {name: process.info.get(name) for name in attrs}
        with patch("keeper_codex.psutil.process_iter", return_value=[process]), \
             patch("keeper_codex.console_title", return_value="Work"):
            self.assertIsNone(active_codex_sessions()[0]["cwd"])


class TerminalSettingsLocations(unittest.TestCase):
    def make(self, base, relative, value=GOOD):
        path = Path(base) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"firstWindowPreference": "%s"}' % value, encoding="utf-8")
        return path

    def test_finds_store_preview_and_unpackaged(self):
        with tempfile.TemporaryDirectory() as base:
            self.make(base, "Packages/Microsoft.WindowsTerminalPreview_8wekyb3d8bbwe/LocalState/settings.json")
            self.make(base, "Microsoft/Windows Terminal/settings.json")
            found = [path for _, path in configure_terminal.settings_files(base)]
            self.assertEqual(len(found), 2)

    def test_none_found_exit_code_nonzero_no_traceback(self):
        with tempfile.TemporaryDirectory() as base:
            self.assertEqual(configure_terminal.settings_files(base), [])
            self.assertEqual(configure_terminal.run(True, [])[1], 1)

    def test_apply_backs_up_and_changes_every_file(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as backups:
            first = self.make(base, "Packages/Microsoft.WindowsTerminal_8wekyb3d8bbwe/LocalState/settings.json")
            second = self.make(base, "Microsoft/Windows Terminal/settings.json")
            reports, code = configure_terminal.run(True, configure_terminal.settings_files(base), Path(backups))
            self.assertEqual(code, 0)
            self.assertEqual(len(reports), 2)
            for path in (first, second):
                self.assertIn("defaultProfile", path.read_text(encoding="utf-8"))
            self.assertEqual(len(list(Path(backups).glob("settings_before_*.json"))), 2)

    def test_preview_changes_nothing(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as backups:
            path = self.make(base, "Microsoft/Windows Terminal/settings.json")
            reports, code = configure_terminal.run(False, configure_terminal.settings_files(base), Path(backups))
            self.assertEqual(code, 0)
            self.assertIn(GOOD, path.read_text(encoding="utf-8"))
            self.assertEqual(list(Path(backups).iterdir()), [])


class LoginDoesNotReadEverything(unittest.TestCase):
    def test_startup_recovery_reads_few_files(self):
        with tempfile.TemporaryDirectory() as folder:
            for number in range(500):
                stamp = f"20260101T{number // 60:02d}{number % 60:02d}00000000Z"
                captured = f"2026-01-01T{number // 60:02d}:{number % 60:02d}:00+00:00"
                (Path(folder) / f"snapshot_{stamp}_{number:08x}.json").write_text(json.dumps(
                    {"version": 1, "boot_id": "old", "captured_at": captured, "windows": []}), encoding="utf-8")
            real = keeper_history.load_snapshot
            with patch("keeper_history.load_snapshot", side_effect=real) as spy, \
                 patch("keeper_fast_history.load_snapshot", side_effect=real) as spy2:
                result = keeper_history.startup_recovery("new", folder)
            self.assertEqual(result[1]["captured_at"], "2026-01-01T08:19:00+00:00")
            self.assertLessEqual(spy.call_count + spy2.call_count, 2)


if __name__ == "__main__":
    unittest.main()

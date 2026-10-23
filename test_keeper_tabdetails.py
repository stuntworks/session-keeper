# Last modified: 2026-10-22 | Change: Drawer facts read inline in tests; Tk freed on the main thread | Full history: git log
"""Fake data only. Hidden Tk roots: nothing is launched, focused or closed."""

import os
import shutil
import tempfile
import tkinter as tk
import unittest

from keeper_copycmd import build_resume_text
from keeper_core import recovery_rows
from keeper_picker import SnapshotMap
from keeper_tabdetails import tab_details
from tk_test_support import free_tk_garbage, keep_drawer_on_main_thread, no_workers_left

IDS = [str(n) * 8 + "-" + str(n) * 4 + "-" + str(n) * 4 + "-" + str(n) * 4 + "-" + str(n) * 12 for n in range(1, 6)]

REAL = tempfile.mkdtemp(prefix="keeper_td_")


def tearDownModule():
    shutil.rmtree(REAL, ignore_errors=True)
    free_tk_garbage()


def snap():
    def tab(i, title, kind="claude", cwd=REAL):
        return {"outcome": "MATCHED", "sessionId": IDS[i], "strippedTitle": title, "kind": kind, "cwd": cwd}
    return {"version": 1, "monitors": [], "windows": [
        {"x": 0, "y": 0, "w": 800, "h": 600, "tabs": [tab(0, "red"), tab(1, "blue", "codex")]},
        {"x": 0, "y": 0, "w": 800, "h": 600, "tabs": [{"outcome": "NONE", "strippedTitle": "mystery"}]}]}


class TabDetailsTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = snap()
        self.rows = {row["key"]: row for row in recovery_rows(self.snapshot)}
        self.live = {IDS[0]}

    def details(self, key):
        return tab_details(self.snapshot, key, self.rows, self.live)

    def test_missing_claude_tab_has_exact_command(self):
        self.live = set()
        info = self.details("0:0")
        self.assertEqual(info["status"], "Missing")
        self.assertEqual(info["name"], "red")
        self.assertEqual(info["program"], "Claude")
        self.assertEqual(info["window"], 1)
        self.assertEqual(info["folder"], REAL)
        self.assertEqual(info["command"], build_resume_text([self.rows["0:0"]]))
        self.assertIn(f"claude -r {IDS[0]}", info["command"])
        self.assertTrue(info["can_copy"])
        self.assertEqual(info["note"], "")

    def test_codex_tab_command(self):
        info = self.details("0:1")
        self.assertEqual(info["program"], "Codex")
        self.assertIn(f"codex resume {IDS[1]}", info["command"])

    def test_open_tab_shows_command_with_duplicate_note(self):
        info = self.details("0:0")
        self.assertEqual(info["status"], "Already open")
        self.assertTrue(info["can_copy"])
        # Owner 2026-10-22: never hide it; offer an explicit second copy with a plain warning.
        self.assertIn("Two copies of the same chat can overwrite each other's history", info["note"])
        self.assertFalse(info["can_open"])
        self.assertTrue(info["second_copy"])
        self.assertEqual(info["command"], build_resume_text([self.rows["0:0"]]))

    def test_unidentified_tab_never_gets_command(self):
        info = self.details("1:0")
        self.assertEqual(info["status"], "Can't be identified")
        self.assertEqual(info["command"], "")
        self.assertFalse(info["can_copy"])
        self.assertIn("No exact session found", info["note"])
        self.assertEqual(info["window"], 2)
        self.assertEqual(info["name"], "mystery")

    # Owner 2026-10-22: the folder is gone, but the exact id still gives the bare command to copy.
    def test_missing_folder_still_shows_bare_command(self):
        self.live = set()
        self.rows["0:0"]["cwd"] = os.path.join(REAL, "gone")
        info = self.details("0:0")
        self.assertEqual(info["command"], f"claude -r {IDS[0]}")
        self.assertTrue(info["can_copy"])
        self.assertFalse(info["can_open"])
        self.assertIn("folder", info["note"].lower())

    def test_missing_tab_can_be_opened(self):
        self.live = set()
        info = self.details("0:0")
        self.assertTrue(info["can_open"])
        self.assertFalse(info["second_copy"])


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(no_workers_left, self)
        keep_drawer_on_main_thread(self)
        self.snapshot = snap()
        rows = {row["key"]: row for row in recovery_rows(self.snapshot)}
        options = {key: tk.BooleanVar(master=self.root, value=False) for key in rows}
        self.map = SnapshotMap(self.root)
        self.map.pack(fill="both", expand=True)
        self.map.show(self.snapshot, rows, {IDS[0]}, options)
        self.rows = rows

    def tearDown(self):
        self.root.destroy()

    def test_click_missing_tab_fills_panel_and_still_toggles(self):
        self.map.select_tab("0:1")
        self.assertEqual(self.map.tab_state("0:1"), "selected")
        self.assertIn(f"codex resume {IDS[1]}", self.map.command_box.get("1.0", "end"))
        self.assertEqual(self.map.copy_btn["state"], "normal")
        self.assertEqual(self.map.drawer.restart_btn.cget("text"), "Open it for me")

    def test_click_open_tab_shows_command_not_selectable(self):
        self.map.select_tab("0:0")
        self.assertEqual(self.map.tab_state("0:0"), "live")
        self.assertIn(f"claude -r {IDS[0]}", self.map.command_box.get("1.0", "end"))
        self.assertIn("Two copies", self.map.note_label.cget("text"))
        self.assertEqual(self.map.drawer.restart_btn.cget("text"), "Open a second copy")
        self.assertEqual(str(self.map.drawer.restart_btn["state"]), "normal")

    def test_click_unknown_tab_no_copy(self):
        self.map.select_tab("1:0")
        self.assertEqual(self.map.command_box.get("1.0", "end").strip(), "")
        self.assertEqual(self.map.copy_btn["state"], "disabled")

    def test_copy_puts_exact_text_on_clipboard(self):
        self.map.select_tab("0:1")
        self.map.copy_detail()
        self.assertEqual(self.root.clipboard_get(), build_resume_text([self.rows["0:1"]]))
        self.assertEqual(self.map.copy_status.cget("text"), "Copied")


if __name__ == "__main__":
    unittest.main()

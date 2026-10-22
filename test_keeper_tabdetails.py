# Last modified: 2026-10-21 | Change: Tests for the tab details text/state builder and the click-to-fill panel | Full history: git log
"""Fake data only. Hidden Tk roots: nothing is launched, focused or closed."""

import tkinter as tk
import unittest

from keeper_copycmd import build_resume_text
from keeper_core import recovery_rows
from keeper_picker import SnapshotMap
from keeper_tabdetails import tab_details

IDS = [str(n) * 8 + "-" + str(n) * 4 + "-" + str(n) * 4 + "-" + str(n) * 4 + "-" + str(n) * 12 for n in range(1, 6)]


def snap():
    def tab(i, title, kind="claude", cwd="C:/Users/example/ProjectA"):
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
        self.assertEqual(info["folder"], "C:/Users/example/ProjectA")
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
        self.assertIn("duplicate", info["note"])
        self.assertEqual(info["command"], build_resume_text([self.rows["0:0"]]))

    def test_unidentified_tab_never_gets_command(self):
        info = self.details("1:0")
        self.assertEqual(info["status"], "Can't be identified")
        self.assertEqual(info["command"], "")
        self.assertFalse(info["can_copy"])
        self.assertIn("No exact session found", info["note"])
        self.assertEqual(info["window"], 2)
        self.assertEqual(info["name"], "mystery")

    def test_unbuildable_command_is_not_copyable(self):
        self.live = set()
        self.rows["0:0"]["cwd"] = 'C:/bad"folder'
        info = self.details("0:0")
        self.assertEqual(info["command"], "")
        self.assertFalse(info["can_copy"])


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
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

    def test_click_open_tab_shows_command_not_selectable(self):
        self.map.select_tab("0:0")
        self.assertEqual(self.map.tab_state("0:0"), "live")
        self.assertIn(f"claude -r {IDS[0]}", self.map.command_box.get("1.0", "end"))
        self.assertIn("duplicate", self.map.note_label.cget("text"))

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

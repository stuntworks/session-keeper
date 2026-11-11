# Last modified: 2026-10-22 | Change: Restart command expectation is two plain lines | Full history: git log
"""Hidden Tk roots and fakes only: no transcript is read, nothing is launched."""

import os
import shutil
import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import keeper_allsessions
from keeper_sessions import row_text
from keeper_theme import Theme
from tk_test_support import free_tk_garbage

BASE = datetime(2026, 10, 21, 12, 0)

REAL = tempfile.mkdtemp(prefix="keeper_as_")


def tearDownModule():
    shutil.rmtree(REAL, ignore_errors=True)
    free_tk_garbage()


def project_dir(number):
    path = os.path.join(REAL, f"project{number}")
    os.makedirs(path, exist_ok=True)
    return path


def ident(number):
    return f"{number:08x}-1111-4222-8333-{number:012x}"


def fake_entries(count=6):
    rows = []
    for number in range(count):
        rows.append({"kind": "codex" if number % 3 == 2 else "claude", "session_id": ident(number),
                     "path": f"C:\\fake\\{number}.jsonl", "size": 4096 * (number + 1),
                     "modified": BASE - timedelta(hours=number), "folder": "C--fake",
                     "title": f"Session {number}" if number != 4 else None, "cwd": project_dir(number)})
    return rows


def fake_facts(row):
    number = int(row["session_id"][-12:], 16)
    return {"available": True, "path": row["path"], "title": None, "last_text": "Done.", "last_text_time": BASE,
            "started": BASE - timedelta(days=1), "last_active": BASE - timedelta(hours=number), "active_span": 3600,
            "user_prompts": 2, "assistant_replies": 3, "messages": 5, "transcript_bytes": 4096,
            "file_changes": 3 if number == 0 else 0, "files_changed": 2 if number == 0 else 0,
            "commands": 4 if number == 0 else 0, "skipped_lines": 0,
            "recent_replies": [(BASE, "Patched the parser."), (BASE - timedelta(minutes=5), "Reading the logs.")],
            "recent_files": ["C:\\Work\\project0\\parser.py", "C:\\Work\\project0\\notes.md"]}


class AllSessionsTests(unittest.TestCase):
    def open(self, entries, live=(), facts=fake_facts, peeker=None):
        self.requested = []

        def counted(row):
            self.requested.append(row["session_id"])
            return facts(row)
        root = tk.Tk()
        root.withdraw()
        root.geometry("2000x1100")
        self.addCleanup(root.destroy)
        view = keeper_allsessions.AllSessionsView(root, Theme(root, 2400), lister=lambda: [dict(e) for e in entries],
                                                  facts=counted, live=lambda: set(live), titles=dict,
                                                  peeker=peeker or (lambda entry, titles: (None, None)), sync=True)
        view.pack(fill="both", expand=True)
        view.list.size = (1900, 900)
        view.start()
        return view

    def test_rows_newest_first_and_counted(self):
        view = self.open(list(reversed(fake_entries())))
        self.assertEqual([entry["session_id"] for entry in view.shown], [ident(n) for n in range(6)])
        self.assertIn("6 sessions", view.count_label.cget("text"))

    def test_search_and_kind_filter(self):
        view = self.open(fake_entries())
        view.search_var.set("project3")
        self.assertEqual([entry["session_id"] for entry in view.shown], [ident(3)])
        view.search_var.set("")
        view.set_kind("codex")
        self.assertEqual([entry["kind"] for entry in view.shown], ["codex", "codex"])
        view.set_kind("all")
        self.assertEqual(len(view.shown), 6)

    def test_facts_are_read_only_for_visible_rows(self):
        view = self.open(fake_entries(300))
        self.assertTrue(self.requested)
        self.assertLess(len(set(self.requested)), 40, "never measure every transcript up front")
        self.assertIn(ident(0), self.requested)

    def test_row_shows_facts_and_running_now(self):
        view = self.open(fake_entries(), live={ident(1)})
        texts = " ".join(view.list.canvas.itemcget(item, "text") for item in view.list.canvas.find_all()
                         if view.list.canvas.type(item) == "text")
        self.assertIn("Running now", texts)
        self.assertIn("Changed files 3 times (2 files) · ran 4 commands", texts)
        self.assertIn("talk only", texts)

    def test_click_opens_review_with_work_and_command(self):
        view = self.open(fake_entries())
        view.open_review(view.shown[0])
        review = view.review
        self.assertEqual(review.winfo_manager(), "pack")
        body = review.body.get("1.0", "end")
        self.assertIn("Patched the parser.", body)
        self.assertLess(body.index("Patched the parser."), body.index("Reading the logs."))
        self.assertIn("C:\\Work\\project0\\parser.py", body)
        self.assertEqual(review.soon_label.cget("text"), "Ask about this session: coming soon")
        command = review.command_box.get("1.0", "end").strip()
        self.assertEqual(command, "cd -LiteralPath '" + project_dir(0) + "'" + chr(10) + "claude -r " + ident(0))
        review.copy()
        self.assertEqual(view.clipboard_get(), command)

    def test_running_session_has_no_restart_and_says_so(self):
        view = self.open(fake_entries(), live={ident(0)})
        view.open_review(view.shown[0])
        self.assertIn("running now", view.review.note_label.cget("text").lower())
        names = [str(getattr(widget, "text", "")) for widget in view.review.winfo_children()]
        self.assertFalse(any("Restart" in name for name in names))

    def test_session_without_folder_has_no_command(self):
        entries = fake_entries()
        entries[0]["cwd"] = None
        view = self.open(entries)
        view.open_review(view.shown[0])
        self.assertEqual(view.review.command_box.get("1.0", "end").strip(), "")
        self.assertEqual(view.review.copy_btn.cget("state"), "disabled")

    def test_session_with_missing_folder_explains_and_disables_copy(self):
        entries = fake_entries()
        entries[0]["cwd"] = os.path.join(REAL, "no_such_folder")
        view = self.open(entries)
        view.open_review(view.shown[0])
        self.assertEqual(view.review.command_box.get("1.0", "end").strip(), "")
        self.assertEqual(view.review.copy_btn.cget("state"), "disabled")
        self.assertIn("no longer exists", view.review.note_label.cget("text"))


# WHAT IT DOES: The "Sort by" chooser: Last active (default), Started, Project.
class SortTests(AllSessionsTests):
    def times(self, table):
        return lambda entry, titles: (None, entry["cwd"], *table.get(entry["session_id"], (None, None)))

    def ids(self, view):
        return [entry["session_id"] for entry in view.shown]

    def test_default_is_last_active_from_the_transcript_and_matches_the_rows(self):
        # file times say 0 is newest; the transcripts say 3 talked most recently
        table = {ident(n): (BASE - timedelta(days=9), BASE - timedelta(hours=10 - n if n != 3 else -5))
                 for n in range(6)}
        view = self.open(fake_entries(), peeker=self.times(table))
        self.assertEqual(view.sort, "active")
        self.assertEqual(self.ids(view)[0], ident(3))
        shown = [row_text(entry, None)["last_active"] for entry in view.shown]
        self.assertEqual(shown, sorted(shown, reverse=True), "order must match the Last active on the rows")

    def test_started_sort(self):
        table = {ident(n): (BASE - timedelta(days=n % 3 * 5 + 1), BASE) for n in range(6)}
        table[ident(5)] = (BASE + timedelta(days=2), BASE)
        view = self.open(fake_entries(), peeker=self.times(table))
        view.set_sort("started")
        self.assertEqual(view.sort, "started")
        self.assertEqual(self.ids(view)[0], ident(5))
        starts = [table[i][0] for i in self.ids(view)]
        self.assertEqual(starts, sorted(starts, reverse=True))

    def test_project_sort_groups_folders(self):
        entries = fake_entries()
        for number, entry in enumerate(entries):
            entry["cwd"] = ["C:\\Work\\Zed", "C:\\Work\\alpha"][number % 2]
        view = self.open(entries)
        view.set_sort("project")
        folders = [entry["cwd"] for entry in view.shown]
        self.assertEqual(folders, ["C:\\Work\\alpha"] * 3 + ["C:\\Work\\Zed"] * 3)
        self.assertEqual(self.ids(view)[:3], [ident(1), ident(3), ident(5)])

    def test_search_and_filter_work_with_any_sort(self):
        for sort in ("active", "started", "project"):
            view = self.open(fake_entries())
            view.set_sort(sort)
            view.search_var.set("project3")
            self.assertEqual(self.ids(view), [ident(3)])
            view.search_var.set("")
            view.set_kind("codex")
            self.assertEqual([entry["kind"] for entry in view.shown], ["codex", "codex"])

    def test_chooser_buttons_show_the_choice_and_choice_is_per_view(self):
        view = self.open(fake_entries())
        self.assertEqual([view.sort_buttons[key].cget("text") for key in ("active", "started", "project")],
                         ["Last active", "Started", "Project (folder)"])
        view.set_sort("project")
        self.assertEqual(view.sort_buttons["project"].kind, "seg_on")
        self.assertEqual(view.sort_buttons["active"].kind, "seg")
        self.assertEqual(self.open(fake_entries()).sort, "active")

    def test_background_pass_reorders_only_when_it_finishes_and_keeps_the_top_row(self):
        view = self.open(fake_entries(300))
        before = self.ids(view)
        view.list.top = 40 * view.list.row_height + 30
        anchor = view.shown[40]["path"]
        reverse = {entry["path"]: (None, entry["cwd"], None, BASE - timedelta(hours=n))
                   for n, entry in enumerate(reversed(view.entries))}
        view.handle(("peek", {path: value for path, value in list(reverse.items())[:100]}))
        self.assertEqual(self.ids(view), before, "a chunk must not reorder the list")
        view.handle(("peek", dict(reverse), True))
        self.assertNotEqual(self.ids(view), before)
        self.assertEqual(view.shown[view.list.top // view.list.row_height]["path"], anchor)
        self.assertEqual(view.list.top % view.list.row_height, 30)


# WHAT IT DOES: The third top tab exists, opens the browser lazily and keeps restore buttons off.
class TabTests(unittest.TestCase):
    def test_all_sessions_tab(self):
        import keeper_app
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        snapshot = {"captured_at": "2026-10-21T10:00:00+00:00", "windows": []}
        with patch.object(keeper_app, "live_session_ids", return_value=set()), \
             patch.object(keeper_app, "LiveChatsPanel"), \
             patch.object(keeper_app, "auto_restore_enabled", return_value=False),              patch.object(keeper_app, "terminal_restore_enabled", return_value=False),              patch.object(keeper_app, "fast_history", return_value=[(Path("x.json"), snapshot)]), \
             patch.object(keeper_app, "window_geometry", return_value="1400x860"), \
             patch.object(keeper_allsessions, "list_transcripts", return_value=fake_entries()) as lister, \
             patch.object(keeper_allsessions, "agent_facts", side_effect=fake_facts), \
             patch.object(keeper_allsessions, "codex_titles", return_value={}), \
             patch.object(keeper_allsessions, "peek_more", return_value=(None, None, None, None)), \
             patch.object(keeper_allsessions, "live_session_ids", return_value=set()):
            view = keeper_app.RecoveryWindow(root, Path("x.json"), snapshot)
            self.assertEqual(view.header.all_btn.cget("text"), "All sessions")
            self.assertIsNone(view.all_view)
            lister.assert_not_called()
            view.show_view("all")
            self.assertIsNotNone(view.all_view)
            self.assertEqual(view.bar.restart_btn.cget("state"), "disabled")
            view.show_view("saved")
            self.assertFalse(view.all_tab.winfo_manager())


if __name__ == "__main__":
    unittest.main()

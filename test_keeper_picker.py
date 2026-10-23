# Last modified: 2026-10-22 | Change: Copy command expectation is two plain lines | Full history: git log
"""Hidden Tk roots and fakes only: nothing is launched, focused or closed."""

import os
import shutil
import tempfile
import tkinter as tk
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import keeper_app
from keeper_ask import BESIDE, CLOSE, OccupiedDialog, plan_choices
from keeper_chooser import chooser_entries
from keeper_core import plan_restore
from keeper_map_layout import nudge_apart, shares_column, split_windows
from tk_test_support import free_tk_garbage, keep_drawer_on_main_thread, no_workers_left

IDS = [str(n) * 8 + "-" + str(n) * 4 + "-" + str(n) * 4 + "-" + str(n) * 4 + "-" + str(n) * 12 for n in range(1, 8)]
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

REAL = tempfile.mkdtemp(prefix="keeper_pk_")


def tearDownModule():
    shutil.rmtree(REAL, ignore_errors=True)
    free_tk_garbage()


# WHAT IT DOES: Builds a two-monitor layout: window 1 fully open, window 2 with a missing "green" tab,
# window 3 minimised, plus one unidentified tab.
# DEPENDS ON: Nothing.
# AFFECTS: Test data only.
def layout(captured="2026-10-19T11:50:00+00:00", boot="boot-2"):
    def tab(i, title):
        return {"outcome": "MATCHED", "sessionId": IDS[i], "strippedTitle": title, "cwd": REAL}
    return {"version": 1, "captured_at": captured, "boot_id": boot,
            "monitors": [{"x": 0, "y": 0, "w": 3840, "h": 2088, "primary": True},
                         {"x": 3840, "y": 0, "w": 3840, "h": 2088, "primary": False}],
            "windows": [
                {"x": 100, "y": 100, "w": 1800, "h": 900, "tabs": [tab(0, "red"), tab(1, "blue")]},
                {"x": 4000, "y": 100, "w": 1800, "h": 900, "tabs": [tab(2, "yellow"), tab(3, "green"),
                                                                     {"outcome": "NONE", "strippedTitle": "mystery"}]},
                {"x": -32000, "y": -32000, "w": 237, "h": 39, "tabs": [tab(4, "purple")]}],
            "unmatched_sessions": [], "counts": {"tabs_total": 6}}


LIVE = {IDS[0], IDS[1], IDS[2]}


# WHAT IT DOES: Short chooser built from history moments, never thousands of entries.
# DEPENDS ON: chooser_entries with a fixed clock.
# AFFECTS: Guards the snapshot list the user sees.
class ChooserTests(unittest.TestCase):
    def history(self):
        items = []
        for minutes in range(0, 60 * 30, 2):
            moment = NOW - timedelta(minutes=minutes)
            boot = "boot-2" if minutes < 120 else "boot-1"
            items.append((Path(f"s{minutes}.json"), layout(moment.isoformat(), boot)))
        items.append((Path("legacy.json"), {**layout("2026-09-26T00:00:00+00:00"), "legacy": True}))
        return items

    def test_entries_are_short_and_named(self):
        entries = chooser_entries(self.history(), NOW)
        labels = [label for label, _ in entries]
        self.assertLess(len(entries), 15)
        self.assertTrue(labels[0].startswith("Latest"))
        self.assertTrue(any(label.startswith("Before last reboot") for label in labels))
        self.assertTrue(any(label.startswith("15 min ago") for label in labels))
        self.assertTrue(any(label.startswith("1 hour ago") for label in labels))
        self.assertTrue(any(label.startswith("Today") for label in labels))
        self.assertTrue(any(label.startswith("Yesterday") for label in labels))
        self.assertEqual(labels[-1], "Old prototype capture")

    def test_checkpoints_point_at_the_right_snapshots(self):
        history = self.history()
        entries = dict(chooser_entries(history, NOW))
        found = {label.split(" (")[0]: index for label, index in entries.items()}
        self.assertEqual(found["Latest"], 0)
        self.assertEqual(history[found["Before last reboot"]][1]["boot_id"], "boot-1")
        self.assertEqual(found["Before last reboot"], 60)
        self.assertEqual(found["15 min ago"], 8)
        self.assertEqual(len(set(entries.values())), len(entries))

    def test_empty_history(self):
        self.assertEqual(chooser_entries([], NOW), [])


# WHAT IT DOES: Overlapping boxes are moved apart without changing x or column order.
# DEPENDS ON: nudge_apart.
# AFFECTS: Every tab stays readable on the map.
class NudgeTests(unittest.TestCase):
    def boxes(self):
        return {0: {"x": 10, "y": 10, "w": 300, "h": 200}, 1: {"x": 20, "y": 30, "w": 300, "h": 200},
                2: {"x": 200, "y": 15, "w": 300, "h": 150}, 3: {"x": 900, "y": 10, "w": 300, "h": 200},
                4: {"x": 15, "y": 400, "w": 300, "h": 100}}

    def test_no_overlap_and_order_kept(self):
        before = self.boxes()
        after = nudge_apart(before)
        for a in after:
            self.assertEqual(after[a]["x"], before[a]["x"])
            for b in after:
                if a >= b:
                    continue
                first, second = after[a], after[b]
                overlap = (first["x"] < second["x"] + second["w"] and second["x"] < first["x"] + first["w"]
                           and first["y"] < second["y"] + second["h"] and second["y"] < first["y"] + first["h"])
                self.assertFalse(overlap, f"boxes {a} and {b} overlap")
                if shares_column(before[a], before[b]):
                    self.assertEqual(before[a]["y"] < before[b]["y"], first["y"] < second["y"])
        self.assertEqual(after[3]["y"], 10)


# WHAT IT DOES: Minimised windows go to the strip and restore to a visible spot.
# DEPENDS ON: split_windows and plan_restore.
# AFFECTS: Nothing is drawn or reopened off-screen.
class MinimisedTests(unittest.TestCase):
    def test_minimised_goes_to_strip(self):
        placed, strip = split_windows(layout()["windows"])
        self.assertEqual((placed, strip), ([0, 1], [2]))

    def test_minimised_restores_on_the_main_monitor(self):
        monitors = layout()["monitors"]
        plans, warnings = plan_restore(layout(), {"2:0"}, set(), [], monitors)
        rect = plans[0]["rect"]
        self.assertGreaterEqual(rect["x"], 0)
        self.assertGreaterEqual(rect["w"], 200)
        self.assertTrue(any("minimised" in w for w in warnings))


# WHAT IT DOES: Opens the real recovery screen on a hidden root with every system read faked.
# DEPENDS ON: keeper_app.RecoveryWindow.
# AFFECTS: Test only.
class PickerWindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(no_workers_left, self)
        self.addCleanup(self.root.destroy)
        keep_drawer_on_main_thread(self)
        self.history = [(Path("new.json"), layout()), (Path("old.json"), layout("2026-10-18T09:00:00+00:00", "boot-1"))]
        for target, value in [("fast_history", self.history), ("live_session_ids", LIVE),
                              ("auto_restore_enabled", False), ("terminal_restore_enabled", False),
                              ("window_geometry", "1400x860")]:
            patcher = patch.object(keeper_app, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in ("LiveChatsPanel",):
            patcher = patch.object(keeper_app, target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.restore = patch.object(keeper_app, "restore_selected", return_value=([], [])).start()
        self.assess = patch.object(keeper_app, "assess_restore", return_value=[]).start()
        # Never open a real terminal from a test ("Open a second copy" launches directly).
        self.second = patch.object(keeper_app, "launch_second_copy").start()
        self.addCleanup(patch.stopall)
        sync = patch.object(keeper_app, "run_background", side_effect=lambda root, action, ok, err: ok(action()))
        sync.start()
        self.view = keeper_app.RecoveryWindow(self.root, *self.history[0])
        self.view.map.size = (1600, 800)
        self.view.map.draw()

    def test_clicking_tabs_toggles_only_missing_ones(self):
        view = self.view
        self.assertEqual(view.selected(), set())
        view.map.toggle("1:1")
        self.assertEqual(view.selected(), {"1:1"})
        self.assertEqual(view.map.tab_state("1:1"), "selected")
        view.map.toggle("1:1")
        self.assertEqual(view.selected(), set())
        view.map.toggle("0:0")
        view.map.toggle("1:2")
        self.assertEqual(view.selected(), set())
        self.assertEqual(view.map.tab_state("0:0"), "live")
        self.assertEqual(view.map.tab_state("1:2"), "unknown")
        self.assertTrue(view.map.tab_names("1:2")[0].startswith("? "))
        self.assertIn("not identified", view.map.tab_names("1:2")[1])

    def test_matched_tab_without_folder_says_folder_not_recorded(self):
        view = self.view
        view.snapshot["windows"][1]["tabs"][2] = {"outcome": "MATCHED", "sessionId": "zzz", "strippedTitle": "nofolder"}
        label, hover = view.map.tab_names("1:2")
        self.assertTrue(label.startswith("? "))
        self.assertIn("folder not recorded", hover)
        self.assertNotIn("not identified", hover)

    def test_title_bar_selects_then_unselects_that_window(self):
        self.view.map.toggle_window(1)
        self.assertEqual(self.view.selected(), {"1:1"})
        self.view.map.toggle_window(1)
        self.assertEqual(self.view.selected(), set())

    def test_select_all_covers_every_window(self):
        self.view.check_all()
        self.assertEqual(self.view.selected(), {"1:1", "2:0"})

    def test_every_tab_is_drawn_and_boxes_do_not_overlap(self):
        boxes = self.view.map.tab_boxes
        self.assertEqual(set(boxes), {"0:0", "0:1", "1:0", "1:1", "1:2"})
        self.assertTrue(all(y >= 0 for _, y, _, _ in boxes.values()))
        self.assertEqual(set(self.view.map.bar_boxes), {2}, "the minimised window is a taskbar button")
        self.assertFalse(self.view.map.canvas.find_withtag("tab:2:0"))

    def test_minimised_button_sits_on_the_primary_monitor_taskbar(self):
        x, y, w, h = self.view.map.bar_boxes[2]
        self.assertLess(x + w, self.view.map.size[0] / 2, "primary monitor (x=0) is drawn on the left")

    def test_taskbar_button_click_selects_missing_tabs_and_opens_details(self):
        view = self.view
        view.map.click_button(2)
        self.assertEqual(view.selected(), {"2:0"})
        self.assertEqual(view.map.detail_key, "2:0")
        self.assertEqual(view.map.drawer.winfo_manager(), "pack")
        view.map.click_button(2)
        self.assertEqual(view.selected(), set())

    # A window with many long-named tabs: most do not fit its row on the map.
    def crowded_view(self):
        snapshot = layout()
        names = ["Website redesign", "Context setup notes", "Davinci Resolve scripting",
                 "Marketing plan", "Crawl4ai GitHub review", "Docs cleanup",
                 "Release checklist", "Asset pipeline", "Data scanner", "Call log"]
        snapshot["windows"][1]["tabs"] = [
            {"outcome": "MATCHED", "sessionId": f"{n:08d}-0000-4000-8000-{n:012d}", "strippedTitle": name,
             "cwd": REAL} for n, name in enumerate(names)]
        view = keeper_app.RecoveryWindow(self.root, Path("crowded.json"), snapshot)
        view.map.size = (1600, 800)
        view.map.draw()
        return view

    def test_overflow_list_holds_exactly_the_tabs_not_drawn(self):
        view = self.crowded_view()
        keys = view.map.window_keys(1)
        drawn = [key for key in keys if key in view.map.tab_boxes]
        self.assertTrue(drawn)
        self.assertEqual(view.map.overflow[1], [key for key in keys if key not in drawn])
        self.assertTrue(view.map.overflow[1], "this window must overflow")
        view.map.open_menu(1)
        self.assertEqual(list(view.map.menu_rows), view.map.overflow[1])
        self.assertTrue(view.map.canvas.find_withtag("menu"))

    def test_overflow_row_click_works_like_a_tab_click(self):
        view = self.crowded_view()
        key = view.map.overflow[1][-1]
        self.assertEqual(view.map.tab_state(key), "missing")
        view.map.open_menu(1)
        view.map.click_row(key)
        self.assertEqual(view.selected(), {key})
        self.assertEqual(view.map.detail_key, key)
        self.assertEqual(view.map.drawer.winfo_manager(), "pack")
        self.assertIn(key, view.map.menu_rows, "the list stays open so tabs can be picked one by one")
        view.map.click_row(key)
        self.assertEqual(view.selected(), set())

    def test_overflow_menu_closes_on_escape_and_outside_click(self):
        view = self.crowded_view()
        view.map.open_menu(1)
        view.map.close_menu()
        self.assertFalse(view.map.canvas.find_withtag("menu"))
        view.map.open_menu(1)
        view.map.outside_click(type("Event", (), {"x": 1, "y": 1})())
        self.assertFalse(view.map.canvas.find_withtag("menu"))

    def test_selected_tab_in_overflow_is_counted(self):
        view = self.crowded_view()
        key = view.map.overflow[1][-1]
        view.map.toggle(key)
        canvas = view.map.canvas
        texts = [canvas.itemcget(item, "text") for item in canvas.find_withtag("more-title:1")
                 if canvas.type(item) == "text"]
        self.assertIn("1 selected", " ".join(texts))

    def test_hidden_window_is_not_drawn_but_gets_a_button(self):
        snapshot = layout()
        snapshot["windows"][0].update(x=1960, y=31, w=1876, h=936, z=1)
        snapshot["windows"][1].update(x=1957, y=33, w=1876, h=936, z=0)
        snapshot["windows"][2]["z"] = 2
        view = keeper_app.RecoveryWindow(self.root, Path("twin.json"), snapshot)
        view.map.size = (1600, 800)
        view.map.draw()
        self.assertEqual(set(view.map.bar_boxes), {0, 2})
        self.assertFalse(view.map.canvas.find_withtag("title:0"))
        self.assertTrue(view.map.canvas.find_withtag("title:1"))
        self.assertEqual(set(view.map.tab_boxes), {"1:0", "1:1", "1:2"})

    def test_opens_on_saved_recovery(self):
        self.assertEqual(self.view.current_view, "saved")
        self.assertTrue(self.view.recovery_tab.winfo_manager())
        self.assertFalse(self.view.now_tab.winfo_manager())

    def test_choosing_a_snapshot_stays_on_saved_recovery(self):
        view = self.view
        view.choose_layout(1)
        self.assertEqual(view.current_view, "saved")
        self.assertEqual(view.path, Path("old.json"))
        self.assertIs(self.root._keeper_window, view)
        view.show_view("now")
        view.choose_layout(0)
        self.assertEqual(view.current_view, "saved")
        self.assertEqual(view.path, Path("new.json"))

    def test_scrubber_stops_load_their_snapshot(self):
        view = self.view
        stops = view.stops
        self.assertEqual(sorted(stop["index"] for stop in stops), [0, 1])
        self.assertEqual(stops[-1]["index"], 0)
        view.pick_stop(0)
        self.assertEqual(view.path, Path("old.json"))
        self.assertEqual(view.current_view, "saved")
        self.assertEqual(view.strip.current, 0)

    def test_picking_a_stop_scans_off_the_tk_thread(self):
        view = self.view
        jobs = []
        with patch.object(keeper_app, "run_background", side_effect=lambda root, action, ok, err: jobs.append((action, ok))),                 patch.object(keeper_app, "live_session_ids", return_value=LIVE) as scan:
            view.pick_stop(0)
            scan.assert_not_called()
            self.assertEqual(view.path, Path("new.json"))
            self.assertEqual(len(jobs), 1)
            action, ok = jobs[0]
            self.assertIs(action, scan)
            ok(action())
        self.assertEqual(view.path, Path("old.json"))

    def test_stale_scan_result_is_ignored(self):
        view = self.view
        jobs = []
        with patch.object(keeper_app, "run_background", side_effect=lambda root, action, ok, err: jobs.append(ok)):
            view.choose_layout(1)
            view.choose_layout(0)
        jobs[0](LIVE)
        self.assertEqual(view.path, Path("new.json"))
        jobs[1](LIVE)
        self.assertEqual(view.path, Path("new.json"))

    def test_window_title_strip_click_selects_its_missing_tabs(self):
        canvas = self.view.map.canvas
        self.assertTrue(canvas.find_withtag("title:1"))
        self.assertTrue(canvas.tag_bind("title:1", "<Button-1>"))
        self.view.map.toggle_window(0)
        self.assertEqual(self.view.selected(), set())
        self.view.map.toggle_window(1)
        self.assertEqual(self.view.selected(), {"1:1"})

    def test_select_all_then_clear_all(self):
        view = self.view
        view.select_or_clear()
        self.assertEqual(view.selected(), {"1:1", "2:0"})
        self.assertEqual(view.bar.select_btn.cget("text"), "Clear all")
        view.select_or_clear()
        self.assertEqual(view.selected(), set())
        self.assertEqual(view.bar.select_btn.cget("text"), "Select all")

    def test_restart_this_tab_only_for_a_missing_matched_tab(self):
        view = self.view
        view.restart_one("0:0")
        view.restart_one("1:2")
        self.assess.assert_not_called()
        self.restore.assert_not_called()
        view.map.toggle("2:0")
        view.restart_one("1:1")
        self.assess.assert_called_once()
        self.assertEqual(self.restore.call_args.args[1], {"1:1"})
        self.assertEqual(view.selected(), {"2:0"})

    def test_drawer_open_button_per_tab_state(self):
        view = self.view
        view.map.select_tab("0:0")
        self.assertEqual(view.map.drawer.restart_btn["state"], "normal")
        self.assertEqual(view.map.drawer.restart_btn.cget("text"), "Open a second copy")
        view.map.select_tab("1:2")
        self.assertEqual(view.map.drawer.restart_btn["state"], "disabled")
        view.map.select_tab("1:1")
        self.assertEqual(view.map.drawer.restart_btn["state"], "normal")

    # WHAT IT DOES: "Open a second copy" on a running tab launches only on that explicit click, never via restore.
    def test_second_copy_only_on_explicit_click(self):
        view = self.view
        with patch.object(keeper_app, "launch_second_copy") as launch:
            view.map.select_tab("0:0")
            view.map.drawer.restart_one()
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0]["key"], "0:0")
        self.restore.assert_not_called()

    # WHAT IT DOES: A stopped restore lists the exact command for each tab that did not open, with Copy and Open.
    def test_stopped_restore_shows_commands_for_unopened_tabs(self):
        view = self.view
        view.map.toggle("1:1")
        self.restore.return_value = ([], ["Not opened: 'green': something. To fix: try again."])
        with patch.object(view.map.drawer, "show_message") as shown:
            view.restart()
        commands = shown.call_args.kwargs["commands"]
        self.assertEqual([entry["key"] for entry in commands], ["1:1"])
        self.assertIn("claude -r ", commands[0]["text"])
        self.assertTrue(commands[0]["can_open"])


        view = self.view
        self.assertEqual(view.map.drawer.winfo_manager(), "", "empty drawer must not take map width")
        view.map.select_tab("0:0")
        self.assertEqual(view.map.drawer.winfo_manager(), "pack")
        view.map.close_detail()
        self.assertEqual(view.map.drawer.winfo_manager(), "")

    def test_nothing_picked_offers_restore_all_with_a_note(self):
        view = self.view
        view.selection_changed()
        self.assertTrue(view.bar.restart_btn.cget("text").startswith("Restore all"))
        self.assertEqual(view.bar.restart_btn["state"], "normal")
        self.assertIn("press Restore all", view.copy_status.cget("text"))
        view.map.toggle("1:1")
        self.assertEqual(view.bar.restart_btn.cget("text"), "Restart 1 selected")
        self.assertEqual(view.copy_status.cget("text"), "")

    def test_how_to_use_guide_shows_on_saved_recovery_only(self):
        view = self.view
        view.show_view("saved")
        self.assertEqual(view.header.guide.winfo_manager(), "pack")
        view.show_view("now")
        self.assertEqual(view.header.guide.winfo_manager(), "")
        view.show_view("saved")
        self.assertEqual(view.header.guide.winfo_manager(), "pack")

    def test_nothing_missing_says_why_buttons_are_off(self):
        view = self.view
        with patch.object(view.map, "tab_state", return_value="live"):
            view.selection_changed()
        self.assertEqual(view.bar.select_btn["state"], "disabled")
        self.assertIn("already open", view.copy_status.cget("text"))

    def test_bar_buttons_disabled_on_screen_now(self):
        view = self.view
        view.map.toggle("1:1")
        self.assertEqual(view.bar.restart_btn["state"], "normal")
        self.assertEqual(view.bar.restart_btn.cget("text"), "Restart 1 selected")
        view.show_view("now")
        self.assertEqual(view.bar.restart_btn["state"], "disabled")
        self.assertEqual(str(view.copy_button["state"]), "disabled")
        view.show_view("saved")
        self.assertEqual(view.bar.restart_btn["state"], "normal")

    def test_restart_with_covered_spot_asks_first(self):
        blocked = [{"window_index": 1, "title": "green", "rect": {}, "occupants": [{"hwnd": 9, "state": "empty"}]}]
        self.assess.return_value = blocked
        with patch.object(keeper_app, "OccupiedDialog") as dialog:
            self.view.map.toggle("1:1")
            self.view.restart()
            self.restore.assert_not_called()
            on_done = dialog.call_args.args[2]
            on_done(None)
            self.restore.assert_not_called()
            self.assertFalse(self.view.busy)
            self.view.restart()
            dialog.call_args.args[2](({1: "close_empty"}, {9}))
        self.restore.assert_called_once()
        self.assertEqual(self.restore.call_args.args[3:5], ({1: "close_empty"}, {9}))

    def test_countdown_never_asks_and_never_closes(self):
        self.view.map.toggle("1:1")
        self.view.countdown(0)
        self.assess.assert_not_called()
        self.assertIsNone(self.restore.call_args.args[3])

    def test_copy_command_button_follows_selection(self):
        view = self.view
        self.assertEqual(str(view.copy_button["state"]), "disabled")
        view.map.toggle("1:1")
        self.assertEqual(str(view.copy_button["state"]), "normal")
        view.map.toggle("1:1")
        self.assertEqual(str(view.copy_button["state"]), "disabled")

    def test_copy_command_puts_exact_text_on_clipboard(self):
        view = self.view
        view.map.toggle("1:1")
        view.copy_command()
        self.assertEqual(self.root.clipboard_get(),
                         "cd -LiteralPath '" + REAL + "'" + chr(10) + "claude -r " + IDS[3])
        self.assertIn("Copied command for green", view.copy_status.cget("text"))
        self.assertEqual(view.copy_button.cget("text"), "Copied")
        view.check_all()
        view.copy_command()
        text = self.root.clipboard_get()
        self.assertEqual(text.count("claude -r "), 2)
        self.assertNotIn(IDS[0], text)
        self.assertNotIn("mystery", text)
        self.assertIn("# green", text)
        self.assertIn("for 2 tabs", view.copy_status.cget("text"))

    def test_copy_command_does_nothing_without_selection(self):
        self.root.clipboard_clear()
        self.root.clipboard_append("keep")
        self.view.copy_command()
        self.assertEqual(self.root.clipboard_get(), "keep")


# WHAT IT DOES: The confirm dialog turns buttons into exact choices, with Cancel focused.
# DEPENDS ON: keeper_ask.
# AFFECTS: Nothing closes or stacks unless chosen here.
class AskTests(unittest.TestCase):
    blocked = [{"window_index": 0, "title": "a", "occupants": [{"hwnd": 9, "state": "empty", "tabs": ["PowerShell"]}]},
               {"window_index": 1, "title": "b", "occupants": [{"hwnd": 10, "state": "live", "tabs": ["chat"]}]}]

    def test_plan_choices(self):
        self.assertEqual(plan_choices(self.blocked, CLOSE, set()), ({0: "close_empty"}, {9}))
        self.assertEqual(plan_choices(self.blocked, BESIDE, {1}), ({0: "on_top", 1: "on_top"}, set()))
        self.assertEqual(plan_choices(self.blocked, CLOSE, {0}), ({0: "close_empty"}, {9}))

    def test_cancel_has_focus_and_returns_none(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        results = []
        dialog = OccupiedDialog(root, self.blocked, results.append)
        dialog.withdraw()
        self.assertIs(dialog.default_button, dialog.cancel_button)
        dialog.cancel()
        self.assertEqual(results, [None])


if __name__ == "__main__":
    unittest.main()

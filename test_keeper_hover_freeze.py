# Last modified: 2026-11-03 | Change: New. Hover popup must not loop Enter/Leave forever | Full history: git log
"""Hover freeze guard: pointing at a tab or taskbar button must raise ONE popup and then go quiet.

Why: the recovery screen froze ("Not Responding") after a hover. The popup was drawn where Tk
re-picked it as the item under the pointer, so Leave deleted it, Enter redrew it, forever.
"""

import time
import tkinter as tk
import unittest
from unittest.mock import patch

import keeper_app
from test_keeper_picker import LIVE, layout, REAL  # noqa: F401  (REAL keeps the shared temp dir alive)
from tk_test_support import free_tk_garbage, keep_drawer_on_main_thread, no_workers_left
from pathlib import Path


def tearDownModule():
    free_tk_garbage()


# WHAT IT DOES: Builds the real recovery window (fake snapshot, nothing launched) and counts popup items.
# DEPENDS ON: keeper_app.RecoveryWindow, patched the same way as test_keeper_picker.PickerWindowTests.
# AFFECTS: Test data only.
class HoverFreezeTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        # Mapped (Tk only re-picks the item under the pointer on a mapped window) but invisible and far
        # off screen, so no real mouse can touch it and nothing shows on the desktop.
        self.root.attributes("-alpha", 0)
        self.root.geometry("1600x900+12000+12000")
        self.addCleanup(no_workers_left, self)
        self.addCleanup(self.root.destroy)
        keep_drawer_on_main_thread(self)
        history = [(Path("new.json"), layout()), (Path("old.json"), layout("2026-10-18T09:00:00+00:00", "boot-1"))]
        for target, value in [("fast_history", history), ("live_session_ids", LIVE),
                              ("auto_restore_enabled", False), ("terminal_restore_enabled", False),
                              ("window_geometry", "1400x860")]:
            patch.object(keeper_app, target, return_value=value).start()
        patch.object(keeper_app, "LiveChatsPanel").start()
        patch.object(keeper_app, "restore_selected", return_value=([], [])).start()
        patch.object(keeper_app, "assess_restore", return_value=[]).start()
        patch.object(keeper_app, "launch_second_copy").start()
        self.addCleanup(patch.stopall)
        patch.object(keeper_app, "run_background", side_effect=lambda root, action, ok, err: ok(action())).start()
        self.view = keeper_app.RecoveryWindow(self.root, *history[0])
        self.map = self.view.map
        self.map.size = (1600, 800)
        self.map.draw()
        self.canvas = self.map.canvas
        self.root.update()
        # Let deferred work (wallpapers, monitor names) finish its one redraw before counting popups.
        self.pump(0.6)
        self.tip_items = 0
        self.tip_deletes = 0
        self.watch_tips()

    # Counts popup items created and popup deletions, whatever the code calls them.
    def watch_tips(self):
        canvas = self.canvas
        for name in ("create_text", "create_rectangle"):
            real = getattr(canvas, name)

            def counted(*args, _real=real, **kw):
                if "tip" in (kw.get("tags") or ()):
                    self.tip_items += 1
                return _real(*args, **kw)
            setattr(canvas, name, counted)
        real_delete = canvas.delete

        def counted_delete(*args):
            if args == ("tip",) and canvas.find_withtag("tip"):
                self.tip_deletes += 1
            return real_delete(*args)
        canvas.delete = counted_delete
        # Circuit breaker: a real loop never leaves update(), so after 60 popup calls the popup is
        # switched off and the test fails on the count instead of hanging the whole run.
        owner, name = (self.map.tips, "show") if hasattr(self.map, "tips") else (self.map, "show_tip")
        real_tip = getattr(owner, name)
        self.tip_calls = 0

        def breaker(*args, **kw):
            self.tip_calls += 1
            if self.tip_calls > 60:
                return None
            return real_tip(*args, **kw)
        setattr(owner, name, breaker)

    def pump(self, seconds=0.4):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()

    # Like a person: arrive on the target's own item first, then move on to the spot being tested.
    def hover(self, x, y, arrive=None):
        if arrive:
            self.canvas.event_generate("<Motion>", x=int(arrive[0]), y=int(arrive[1]), warp=False)
            self.pump(0.05)
        self.tip_items = self.tip_deletes = self.tip_calls = 0
        self.canvas.event_generate("<Motion>", x=int(x), y=int(y), warp=False)
        self.pump()
        settled = (self.tip_items, self.tip_deletes)
        self.pump(0.3)
        return settled, (self.tip_items, self.tip_deletes)

    def assert_calm(self, label, x, y, arrive=None):
        before, after = self.hover(x, y, arrive)
        # One popup = 2 canvas items (text + back). Nothing more may happen once settled.
        self.assertLess(self.tip_calls, 60, f"{label}: popup redrawn in an endless loop (UI freeze)")
        self.assertLessEqual(after[0], 2, f"{label}: popup redrawn in a loop {after}")
        self.assertLessEqual(after[1], 1, f"{label}: popup deleted again and again {after}")
        self.assertEqual(before, after, f"{label}: events still firing after settling")

    def test_hover_inside_tab_is_calm(self):
        x, y, w, h = self.map.tab_boxes["1:1"]
        for fx, fy in [(0.5, 0.5), (0.2, 0.5), (0.8, 0.5), (0.5, 0.95), (0.5, 1.0), (0.5, 0.05)]:
            with self.subTest(fx=fx, fy=fy):
                self.canvas.event_generate("<Leave>")
                self.canvas.delete("tip")
                self.assert_calm(f"tab {fx},{fy}", x + w * fx, y + h * fy)

    def test_hover_just_below_a_plain_tab_is_calm(self):
        # The popup's top edge sits a couple of pixels under the tab: this is the spot that froze the app.
        x, y, w, h = self.map.tab_boxes["1:1"]
        for dy in range(int(h) - 3, int(h) + 9):
            with self.subTest(dy=dy):
                self.canvas.delete("tip")
                self.assert_calm(f"below tab +{dy}", x + w / 2, y + dy, (x + w / 2, y + h / 2))

    def test_hover_bottom_edge_of_inspected_tab_is_calm(self):
        self.map.select_tab("1:0")          # opens the drawer: the tab gets the "inspected" outline
        self.map.draw()
        self.root.update()
        x, y, w, h = self.map.tab_boxes["1:0"]
        for dy in range(int(h) - 3, int(h) + 7):
            with self.subTest(dy=dy):
                self.canvas.delete("tip")
                self.assert_calm(f"inspected bottom +{dy}", x + w / 2, y + dy, (x + w / 2, y + h / 2))

    def test_hover_taskbar_button_is_calm(self):
        self.assertTrue(self.map.bar_boxes, "fixture must draw a taskbar button")
        for index, (x, y, w, h) in self.map.bar_boxes.items():
            for fx, fy in [(0.5, 0.5), (0.5, 0.05), (0.5, 0.95)]:
                with self.subTest(index=index, fx=fx, fy=fy):
                    self.canvas.delete("tip")
                    self.assert_calm("taskbar", x + w * fx, y + h * fy)

    def test_moving_inside_one_tab_draws_popup_once(self):
        x, y, w, h = self.map.tab_boxes["1:1"]
        self.tip_items = self.tip_deletes = 0
        for step in range(12):
            self.canvas.event_generate("<Motion>", x=int(x + 10 + step * 5), y=int(y + h / 2), warp=False)
            self.pump(0.03)
        self.pump(0.3)
        self.assertLessEqual(self.tip_items, 2, f"popup redrawn while moving inside one tab: {self.tip_items}")


if __name__ == "__main__":
    unittest.main()

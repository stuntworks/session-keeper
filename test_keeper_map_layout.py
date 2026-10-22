# Last modified: 2026-10-22 | Change: Exact map: windows at their real spot, uniform scale, one tab row | Full history: git log
"""Display geometry only: real restore positions are never touched by these functions."""

import tkinter as tk
import unittest

from keeper_map_layout import exact_layout
from keeper_terminal_view import Painter
from keeper_theme import Theme

M1 = {"x": 0, "y": 0, "w": 3840, "h": 2088, "primary": True}
M2 = {"x": 3840, "y": 0, "w": 3840, "h": 2088, "primary": False}


# WHAT IT DOES: Every window is drawn at its real x/y/w/h, one uniform scale, nothing widened or moved.
# DEPENDS ON: exact_layout.
# AFFECTS: The map looks like the owner's real monitors.
class ExactLayoutTests(unittest.TestCase):
    def layout(self, windows, monitors=(M2, M1), area=(3000, 1100)):
        return exact_layout(list(monitors), windows, list(range(len(windows))), *area,
                            gap=40, side=16, top=16, bar=60, chin=40)

    def test_monitors_keep_their_real_shape_and_order(self):
        screens, boxes, home, scale = self.layout([])
        right, left = screens[0]["screen"], screens[1]["screen"]     # saved order: M2 first, M1 second
        for screen in (left, right):
            self.assertAlmostEqual(screen["w"] / screen["h"], 3840 / 2088, places=6)
            self.assertAlmostEqual(screen["w"], 3840 * scale, places=6)
        self.assertGreater(right["x"], left["x"] + left["w"])

    def test_windows_sit_exactly_where_they_were(self):
        windows = [{"x": 1960, "y": 31, "w": 1876, "h": 936}, {"x": 5846, "y": 1011, "w": 1792, "h": 936}]
        screens, boxes, home, scale = self.layout(windows)
        left, right = screens[1]["screen"], screens[0]["screen"]
        self.assertEqual(home, {0: 1, 1: 0})
        self.assertAlmostEqual(boxes[0]["x"], left["x"] + 1960 * scale)
        self.assertAlmostEqual(boxes[0]["y"], left["y"] + 31 * scale)
        self.assertAlmostEqual(boxes[0]["w"], 1876 * scale)
        self.assertAlmostEqual(boxes[0]["h"], 936 * scale)
        self.assertAlmostEqual(boxes[1]["x"], right["x"] + (5846 - 3840) * scale)
        self.assertAlmostEqual(boxes[1]["y"], right["y"] + 1011 * scale)

    def test_overlapping_windows_stay_overlapping(self):
        windows = [{"x": 1960, "y": 31, "w": 1876, "h": 936}, {"x": 1957, "y": 33, "w": 1876, "h": 936}]
        screens, boxes, home, scale = self.layout(windows)
        self.assertAlmostEqual(boxes[0]["x"] - boxes[1]["x"], 3 * scale)

    def test_part_off_screen_window_is_cut_at_the_screen_edge(self):
        screens, boxes, home, scale = self.layout([{"x": -278, "y": 355, "w": 1694, "h": 936}])
        left = screens[1]["screen"]
        self.assertAlmostEqual(boxes[0]["x"], left["x"])
        self.assertAlmostEqual(boxes[0]["w"], (1694 - 278) * scale)

    def test_everything_fits_the_area_with_no_scrolling(self):
        screens, boxes, home, scale = self.layout([])
        for screen in screens:
            bezel = screen["bezel"]
            self.assertGreaterEqual(bezel["x"], 0)
            self.assertLessEqual(bezel["x"] + bezel["w"], 3000 + 0.01)
            self.assertLessEqual(bezel["y"] + bezel["h"], 1100 + 0.01)
            self.assertAlmostEqual(screen["bar"]["y"], screen["screen"]["y"] + screen["screen"]["h"])
            self.assertAlmostEqual(screen["bar"]["w"], screen["screen"]["w"])


# WHAT IT DOES: Tabs sit in ONE row like the real terminal, shortened to fit the window's real width.
# DEPENDS ON: Painter.row_widths on a real (hidden) Tk canvas.
class TabRowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.painter = Painter(tk.Canvas(self.root), Theme(self.root, 3610))
        self.infos = [{"label": name, "state": "missing", "kind": "claude"} for name in
                      ("Website redesign", "Context.dev setup notes", "Davinci Resolve scripting",
                       "Marketing plan", "V")]

    def tearDown(self):
        self.root.destroy()

    def test_natural_widths_when_they_fit(self):
        natural = [self.painter.tab_width(info) for info in self.infos]
        widths, more = self.painter.row_widths(self.infos, sum(natural) + 1000)
        self.assertEqual((widths, more), (natural, 0))

    def test_squeezed_into_one_row(self):
        room = sum(self.painter.floor_width(info) for info in self.infos) + self.painter.px(6) * 4 + 300
        widths, more, compact = self.painter.row_layout(self.infos, room)
        self.assertEqual(more, 0)
        self.assertTrue(compact, "tight space switches to compact tabs")
        self.assertLessEqual(sum(widths) + self.painter.px(6) * (len(widths) - 1), room + 0.01)
        self.assertEqual(widths[4], self.painter.tab_width(self.infos[4], compact=True),
                         "a short tab keeps its full name")

    def test_too_many_tabs_show_a_more_count(self):
        widths, more = self.painter.row_widths(self.infos * 6, 900)
        self.assertGreater(more, 0)
        self.assertEqual(len(widths) + more, 30)

    def test_compact_tabs_fit_more_than_normal_ones(self):
        infos = self.infos * 2
        room = sum(self.painter.floor_width(info) for info in infos[:4]) + self.painter.px(6) * 3
        normal, _ = self.painter.fit_row(infos, room, compact=False)
        compact, _ = self.painter.fit_row(infos, room, compact=True)
        self.assertGreater(len(compact), len(normal))

    def test_compact_tabs_show_at_least_six_letters(self):
        info = self.infos[0]
        name_font = self.painter.fonts()[0]
        floor = self.painter.floor_width(info, compact=True)
        self.assertGreaterEqual(floor - self.painter.lead(info, compact=True),
                                name_font.measure(info["label"][:6] + "…"))


if __name__ == "__main__":
    unittest.main()

# Last modified: 2026-10-22 | Change: Z-order capture, front/hidden detection, taskbar buttons, restore order | Full history: git log
"""Pure logic: which saved window was in front, which ones were hidden, where their taskbar buttons go,
and the order a restore opens windows in (back first, so the front window ends up in front again)."""

import unittest

from keeper_zorder import (add_z, back_to_front, hidden_windows, stack_order, taskbar_buttons, z_ranks)

M1 = {"x": 0, "y": 0, "w": 3840, "h": 2088, "primary": True}
M2 = {"x": 3840, "y": 0, "w": 3840, "h": 2088, "primary": False}


def win(x, y, w=1876, h=936, z=None):
    window = {"x": x, "y": y, "w": w, "h": h, "tabs": []}
    if z is not None:
        window["z"] = z
    return window


# WHAT IT DOES: The live z-order walk (top window first) gives each terminal its rank.
class ZRankTests(unittest.TestCase):
    def test_walk_ranks_only_the_terminals_top_first(self):
        chain = {None: 50, 50: 11, 11: 60, 60: 22, 22: 33, 33: 0}
        ranks = z_ranks({22, 11, 33}, lambda: chain[None], lambda hwnd: chain.get(hwnd, 0))
        self.assertEqual(ranks, {11: 0, 22: 1, 33: 2})

    def test_walk_stops_on_a_loop(self):
        ranks = z_ranks({5}, lambda: 1, lambda hwnd: 1)
        self.assertEqual(ranks, {})

    def test_add_z_writes_ranks_and_leaves_unknown_windows_alone(self):
        windows = [{"hwnd": 22}, {"hwnd": 11}, {"hwnd": 99}, {}]
        add_z(windows, {11: 0, 22: 1})
        self.assertEqual([window.get("z") for window in windows], [1, 0, None, None])


# WHAT IT DOES: Front-to-back order: recorded z when every window has it, else the saved list order.
class StackOrderTests(unittest.TestCase):
    def test_recorded_z_wins(self):
        self.assertEqual(stack_order([win(0, 0, z=2), win(0, 0, z=0), win(0, 0, z=1)]), [1, 2, 0])

    def test_old_snapshot_without_z_uses_list_order(self):
        self.assertEqual(stack_order([win(0, 0), win(0, 0), win(0, 0)]), [0, 1, 2])

    def test_partial_z_falls_back_to_list_order(self):
        self.assertEqual(stack_order([win(0, 0, z=2), win(0, 0), win(0, 0, z=0)]), [0, 1, 2])


# WHAT IT DOES: A window is hidden when windows in front of it cover at least 80% of it.
class HiddenTests(unittest.TestCase):
    def test_window_behind_a_near_twin_is_hidden(self):
        windows = [win(1960, 31, z=0), win(1957, 33, z=1)]
        self.assertEqual(hidden_windows(windows, [0, 1]), {1})

    def test_half_covered_window_stays_visible(self):
        windows = [win(0, 0, 1000, 1000, z=0), win(500, 0, 1000, 1000, z=1)]
        self.assertEqual(hidden_windows(windows, [0, 1]), set())

    def test_two_front_windows_together_hide_one(self):
        windows = [win(0, 0, 500, 1000, z=0), win(450, 0, 500, 1000, z=1), win(0, 0, 1000, 1000, z=2)]
        self.assertEqual(hidden_windows(windows, [0, 1, 2]), {2})

    def test_only_drawable_windows_cover(self):
        windows = [win(0, 0, z=0), win(0, 0, z=1)]
        self.assertEqual(hidden_windows(windows, [1]), set())


# WHAT IT DOES: Hidden windows get a button on their own monitor's taskbar; minimised ones on the primary.
class TaskbarTests(unittest.TestCase):
    def test_hidden_on_its_monitor_and_minimised_on_primary(self):
        monitors = [M2, M1]
        windows = [win(5000, 100, z=0), win(5010, 110, z=1), win(-32000, -32000, 160, 28, z=2)]
        buttons = taskbar_buttons(monitors, windows, hidden={1}, minimised=[2])
        self.assertEqual(buttons, {0: [1], 1: [2]})

    def test_no_monitors_puts_everything_on_one_bar(self):
        buttons = taskbar_buttons([], [win(0, 0), win(-32000, -32000)], hidden={0}, minimised=[1])
        self.assertEqual(buttons, {0: [0, 1]})


# WHAT IT DOES: Restore opens back windows first, so the saved front window ends up in front again.
class RestoreOrderTests(unittest.TestCase):
    def plans(self, *indexes):
        return [{"tabs": [{"window_index": index, "key": f"{index}:0"}], "rect": {}} for index in indexes]

    def test_back_window_opens_first(self):
        snapshot = {"windows": [win(0, 0, z=1), win(0, 0, z=0), win(0, 0, z=2)]}
        ordered = back_to_front(self.plans(0, 1, 2), snapshot)
        self.assertEqual([plan["tabs"][0]["window_index"] for plan in ordered], [2, 0, 1])

    def test_old_snapshot_uses_list_order_back_first(self):
        snapshot = {"windows": [win(0, 0), win(0, 0), win(0, 0)]}
        ordered = back_to_front(self.plans(0, 1, 2), snapshot)
        self.assertEqual([plan["tabs"][0]["window_index"] for plan in ordered], [2, 1, 0])

    def test_only_order_changes_never_the_plans(self):
        snapshot = {"windows": [win(0, 0, z=1), win(0, 0, z=0)]}
        plans = self.plans(0, 1)
        ordered = back_to_front(plans, snapshot)
        self.assertEqual(sorted(map(id, ordered)), sorted(map(id, plans)))


if __name__ == "__main__":
    unittest.main()


# WHAT IT DOES: A snapshot capture records each terminal's live z rank; a failed read leaves z out.
class CaptureTests(unittest.TestCase):
    def run_capture(self, ranks):
        from unittest.mock import patch
        import keeper_core
        windows = [{"hwnd": 7, "x": 0, "y": 0, "w": 900, "h": 600, "tabs": ["a"]},
                   {"hwnd": 8, "x": 10, "y": 10, "w": 900, "h": 600, "tabs": ["b"]}]
        with patch.object(keeper_core, "read_windows", return_value=windows), \
             patch.object(keeper_core, "read_boot_id", return_value="boot"), \
             patch("keeper_inventory.live_inventory", return_value=[]), \
             patch("keeper_display.read_monitors", return_value=[]), \
             patch("keeper_zorder.read_z_ranks", side_effect=ranks):
            return keeper_core.capture_snapshot()

    def test_capture_records_z(self):
        snapshot = self.run_capture(lambda hwnds: {8: 0, 7: 1})
        self.assertEqual([window.get("z") for window in snapshot["windows"]], [1, 0])

    def test_capture_survives_a_failed_z_read(self):
        def broken(hwnds):
            raise OSError("no desktop")
        snapshot = self.run_capture(broken)
        self.assertEqual([window.get("z") for window in snapshot["windows"]], [None, None])


# WHAT IT DOES: perform_restore opens the saved back window first and the front window last,
# and opens exactly the same tabs as before. Fakes only: nothing is launched.
class PerformRestoreOrderTests(unittest.TestCase):
    IDS = ["11111111-1111-1111-1111-11111111111" + str(n) for n in range(3)]

    def snapshot(self, with_z):
        windows = []
        for number, (x, title) in enumerate(((10, "Front"), (2000, "Back"), (4000, "Middle"))):
            window = {"x": x, "y": 20, "w": 900, "h": 700, "tabs": [
                {"outcome": "MATCHED", "sessionId": self.IDS[number], "strippedTitle": title,
                 "cwd": r"C:\Users\example"}]}
            if with_z:
                window["z"] = {"Front": 0, "Middle": 1, "Back": 2}[title]
            windows.append(window)
        return {"captured_at": "T1", "windows": windows}

    def launched(self, snapshot):
        from unittest.mock import patch
        import keeper_restore
        handles = iter(range(500, 600))
        with patch("keeper_restore.live_session_ids", return_value=set()), \
             patch("keeper_restore.read_windows", return_value=[]), \
             patch("keeper_restore.read_monitors", return_value=[dict(M1, name="A"), dict(M2, name="B")]), \
             patch("keeper_restore.load_created_windows", return_value=[]), \
             patch("keeper_restore.read_boot_id", return_value="B"), \
             patch("keeper_restore.record_created_window"), \
             patch("keeper_restore.launch_tab") as launch, \
             patch("keeper_restore.find_new_window", side_effect=lambda *a, **k: {"hwnd": next(handles)}), \
             patch("keeper_restore.place_new_window"), \
             patch("keeper_restore.wait_for_session", return_value=True), \
             patch("keeper_restore.time.sleep"):
            restored, warnings = keeper_restore.perform_restore(snapshot, {"0:0", "1:0", "2:0"})
        return [call.args[1]["title"] for call in launch.call_args_list], restored

    def test_back_window_opens_first_front_last(self):
        order, restored = self.launched(self.snapshot(with_z=True))
        self.assertEqual(order, ["Back", "Middle", "Front"])

    def test_same_tabs_open_either_way(self):
        with_z, _ = self.launched(self.snapshot(with_z=True))
        without_z, _ = self.launched(self.snapshot(with_z=False))
        self.assertEqual(sorted(with_z), sorted(without_z))
        self.assertEqual(sorted(with_z), ["Back", "Front", "Middle"])

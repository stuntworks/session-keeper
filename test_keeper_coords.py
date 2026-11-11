# Last modified: 2026-10-27 | Change: Saved window spots and monitors share one pixel space | Full history: git log
"""Window rectangles must be in the same physical pixels as the monitors and MoveWindow.
Snapshots from before 2026-10-27 hold system-DPI-aware rectangles; these tests pin their conversion."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from keeper_core import plan_restore

ID1 = "11111111-1111-4111-8111-111111111111"

# The saved (physical work area) monitors on the development PC, as every snapshot so far stored them.
SAVED = [{"name": "\\\\.\\DISPLAY1", "x": 0, "y": 0, "w": 3840, "h": 2088, "primary": True},
         {"name": "\\\\.\\DISPLAY2", "x": 3840, "y": 0, "w": 3840, "h": 2088, "primary": False},
         {"name": "\\\\.\\DISPLAY3", "x": -1920, "y": 0, "w": 1920, "h": 1032, "primary": False}]


# WHAT IT DOES: The live monitors as the new read_monitors reports them: physical work area, its DPI,
# and the same work area in system-DPI-aware pixels (measured live 2026-10-27).
def live_monitors():
    system = [(0, 0, 3840, 2088, 144), (5760, 0, 3840, 2088, 144), (-2880, 0, 2880, 1548, 96)]
    found = []
    for monitor, (x, y, w, h, dpi) in zip(SAVED, system):
        found.append(dict(monitor, dpi=dpi, system_dpi=144, system={"x": x, "y": y, "w": w, "h": h}))
    return found


# WHAT IT DOES: One saved window with one restorable Claude tab at the given rectangle.
def legacy_snapshot(x, y, w, h, monitors=SAVED):
    tab = {"outcome": "MATCHED", "sessionId": ID1, "cwd": "D:\\AI", "strippedTitle": "Tab"}
    return {"version": 1, "captured_at": "T1", "monitors": [dict(m) for m in monitors],
            "windows": [{"hwnd": 1, "x": x, "y": y, "w": w, "h": h, "tabs": [tab]}]}


# WHAT IT DOES: The rectangle a restore would place the window at.
def planned(snapshot, monitors):
    from keeper_coords import physical_snapshot
    converted, _ = physical_snapshot(snapshot, monitors)
    plans, _ = plan_restore(converted, {"0:0"}, set(), [], monitors)
    return plans[0]["rect"]


class LegacyConversion(unittest.TestCase):
    # WHAT IT DOES: The 2026-10-27 bug: a window on the right monitor came back at 0,0.
    def test_right_monitor_window_restores_on_right_monitor(self):
        self.assertEqual(planned(legacy_snapshot(7761, 922, 1750, 1160), live_monitors()),
                         {"x": 5841, "y": 922, "w": 1750, "h": 1160})

    # WHAT IT DOES: A window on the 96-dpi left monitor is moved and shrunk to its physical size.
    def test_left_96dpi_monitor_window_is_scaled(self):
        self.assertEqual(planned(legacy_snapshot(-2487, 110, 1796, 1152), live_monitors()),
                         {"x": -1658, "y": 73, "w": 1197, "h": 768})

    # WHAT IT DOES: Primary-monitor windows were exact before and stay exact.
    def test_primary_window_is_unchanged(self):
        self.assertEqual(planned(legacy_snapshot(100, 50, 1800, 1000), live_monitors()),
                         {"x": 100, "y": 50, "w": 1800, "h": 1000})

    # WHAT IT DOES: A new snapshot is already physical and is never converted a second time.
    def test_physical_snapshot_is_not_converted_again(self):
        from keeper_coords import physical_snapshot
        snapshot = dict(legacy_snapshot(5841, 922, 1750, 1160), coords="physical")
        converted, warnings = physical_snapshot(snapshot, live_monitors())
        self.assertIs(converted, snapshot)
        self.assertEqual(warnings, [])

    # WHAT IT DOES: The converted window keeps where it came from, and the source snapshot is untouched.
    def test_converted_window_remembers_its_saved_rect(self):
        from keeper_coords import physical_snapshot
        snapshot = legacy_snapshot(7761, 922, 1750, 1160)
        converted, _ = physical_snapshot(snapshot, live_monitors())
        self.assertEqual(converted["coords"], "physical")
        self.assertEqual(converted["windows"][0]["converted_from"], {"x": 7761, "y": 922, "w": 1750, "h": 1160})
        self.assertEqual(snapshot["windows"][0]["x"], 7761)
        self.assertNotIn("coords", snapshot)

    # WHAT IT DOES: Monitors changed since the save: nothing is guessed, a warning says so.
    def test_changed_monitors_keep_old_behaviour_with_warning(self):
        from keeper_coords import physical_snapshot
        snapshot = legacy_snapshot(7761, 922, 1750, 1160)
        converted, warnings = physical_snapshot(snapshot, live_monitors()[:2])
        self.assertIs(converted, snapshot)
        self.assertEqual(len(warnings), 1)
        self.assertIn("older Session Keeper", warnings[0])

    # WHAT IT DOES: Without live DPI information (old Windows) nothing is guessed either.
    def test_missing_dpi_information_is_not_guessed(self):
        from keeper_coords import physical_snapshot
        snapshot = legacy_snapshot(7761, 922, 1750, 1160)
        converted, warnings = physical_snapshot(snapshot, [dict(m) for m in SAVED])
        self.assertIs(converted, snapshot)
        self.assertEqual(len(warnings), 1)

    # WHAT IT DOES: Minimised windows (parked at -32000) are left for the minimised fallback.
    def test_minimised_window_is_left_alone(self):
        from keeper_coords import physical_snapshot
        converted, _ = physical_snapshot(legacy_snapshot(-32000, -32000, 160, 28), live_monitors())
        self.assertEqual(converted["windows"][0]["x"], -32000)
        self.assertNotIn("converted_from", converted["windows"][0])


class RestoreUsesConversion(unittest.TestCase):
    # WHAT IT DOES: The real restore path places the window at the converted spot and logs both rectangles.
    def test_restore_places_and_logs_converted_rect(self):
        import keeper_restore
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "created.jsonl"
            from keeper_created import record_created_window

            def record(*args, **kwargs):
                record_created_window(*args, path=log, **kwargs)
            with patch("keeper_restore.read_monitors", return_value=live_monitors()), \
                 patch("keeper_restore.live_session_ids", return_value=set()), \
                 patch("keeper_restore.read_windows", return_value=[]), \
                 patch("keeper_restore.load_created_windows", return_value=[]), \
                 patch("keeper_restore.read_boot_id", return_value="B"), \
                 patch("keeper_restore.record_created_window", side_effect=record), \
                 patch("keeper_restore.launch_tab"), \
                 patch("keeper_restore.find_new_window", return_value={"hwnd": 500, "title": "Tab"}), \
                 patch("keeper_restore.place_new_window") as place, \
                 patch("keeper_restore.wait_for_session", return_value=True), \
                 patch("keeper_restore.time.sleep"):
                keeper_restore.restore_selected(legacy_snapshot(7761, 922, 1750, 1160), {"0:0"},
                                                lock_name="unittest-restore")
            entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(place.call_args.args[1], {"x": 5841, "y": 922, "w": 1750, "h": 1160})
        self.assertEqual(entry["rect"], {"x": 5841, "y": 922, "w": 1750, "h": 1160})
        self.assertEqual(entry["converted_from"], {"x": 7761, "y": 922, "w": 1750, "h": 1160})


class MapUsesConversion(unittest.TestCase):
    # WHAT IT DOES: The map draws a legacy window on its own monitor, not hanging off the right edge.
    def test_map_snapshot_is_converted(self):
        from keeper_coords import map_snapshot
        shown = map_snapshot(legacy_snapshot(7761, 922, 1750, 1160), lambda: live_monitors())
        self.assertEqual(shown["windows"][0]["x"], 5841)

    # WHAT IT DOES: If the monitors cannot be read the map shows the snapshot as it was saved.
    def test_map_falls_back_when_monitors_unreadable(self):
        from keeper_coords import map_snapshot
        snapshot = legacy_snapshot(7761, 922, 1750, 1160)

        def broken():
            raise RuntimeError("Cannot read attached monitors")
        self.assertIs(map_snapshot(snapshot, broken), snapshot)


class ProbeMarker(unittest.TestCase):
    # WHAT IT DOES: Fakes the probe: writes the given JSON to the -OutFile path and records the arguments.
    def fake_probe(self, data, calls):
        from subprocess import CompletedProcess

        def run(arguments):
            calls.append(arguments)
            Path(arguments[arguments.index("-OutFile") + 1]).write_text(json.dumps(data), encoding="utf-8")
            return CompletedProcess(arguments, 0, "", "")
        return run

    # WHAT IT DOES: read_windows asks for physical pixels and accepts the marked answer.
    def test_probe_is_asked_for_physical_pixels(self):
        from keeper_core import read_windows
        calls = []
        data = {"windows": [{"hwnd": 1, "x": 0, "y": 0, "w": 900, "h": 600}], "errors": [], "coords": "physical"}
        with patch("keeper_core.run_powershell", side_effect=self.fake_probe(data, calls)):
            self.assertEqual(len(read_windows()), 1)
        self.assertIn("-Physical", calls[0])

    # WHAT IT DOES: An unmarked (system-DPI) answer is refused rather than mixed with physical monitors.
    def test_unmarked_probe_output_is_refused(self):
        from keeper_core import read_windows
        data = {"windows": [{"hwnd": 1, "x": 0, "y": 0, "w": 900, "h": 600}], "errors": []}
        with patch("keeper_core.run_powershell", side_effect=self.fake_probe(data, [])):
            with self.assertRaises(RuntimeError):
                read_windows()


@unittest.skipUnless(sys.platform == "win32", "needs live Windows Terminal windows")
class LiveCapture(unittest.TestCase):
    # WHAT IT DOES: Every live terminal window's centre lies on a live monitor: windows and monitors share a space.
    # DEPENDS ON: Read-only probes; at least one visible Windows Terminal window (else skipped).
    def test_live_windows_sit_on_live_monitors(self):
        from keeper_core import read_windows
        from keeper_display import read_monitors
        from keeper_home import is_minimised
        windows = [w for w in read_windows() if not is_minimised(w)]
        if not windows:
            self.skipTest("no visible Windows Terminal window")
        monitors = read_monitors()
        for window in windows:
            cx, cy = window["x"] + window["w"] / 2, window["y"] + window["h"] / 2
            inside = any(m["x"] <= cx < m["x"] + m["w"] and m["y"] <= cy < m["y"] + m["h"] for m in monitors)
            self.assertTrue(inside, f"window {window['x']},{window['y']} {window['w']}x{window['h']} is on no monitor")


if __name__ == "__main__":
    unittest.main()

# Last modified: 2026-11-10 | Change: fake_world fakes the new-window clock; two-window case needs proof. Restore batch 3: slow windows, one-hiccup stops, atomic snapshots, maximised, missing monitor | Full history: git log
"""Fakes only: no terminal is opened, no claude is launched, no window is moved, no clock is waited on."""

import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import keeper_core
import keeper_newwindow
import keeper_prune
import keeper_restore
from keeper_core import plan_restore, save_snapshot
from keeper_inventory import match_inventory
from keeper_restore import UnidentifiedSessions, restore_selected

ID1 = "11111111-1111-1111-1111-111111111111"
ID2 = "22222222-2222-2222-2222-222222222222"
ID3 = "33333333-3333-3333-3333-333333333333"
ID4 = "44444444-4444-4444-4444-444444444444"
ID5 = "55555555-5555-5555-5555-555555555555"
ID6 = "66666666-6666-6666-6666-666666666666"
MONITOR = {"name": "M1", "x": 0, "y": 0, "w": 1920, "h": 1040, "primary": True}
LOCK = "unittest-restore"


# WHAT IT DOES: One saved tab row as capture writes it.
def tab(session_id, title):
    return {"outcome": "MATCHED", "sessionId": session_id, "strippedTitle": title, "cwd": os.getcwd()}


# WHAT IT DOES: A saved layout with the given windows; each window is (rect dict, [(id, title), ...]).
def layout(*windows):
    return {"captured_at": "T1", "coords": "physical",
            "windows": [dict(rect, tabs=[tab(i, t) for i, t in tabs]) for rect, tabs in windows]}


# WHAT IT DOES: A fake monotonic clock that only moves when the code sleeps.
class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


# WHAT IT DOES: Patches every outside effect of a restore; returns the started patchers' mocks by name.
# DEPENDS ON: keeper_restore names. live is a set (or callable) of live IDs.
def fake_world(test, windows, live=frozenset(), groups=(), monitors=(MONITOR,)):
    found = {}
    targets = {"live_session_ids": {"side_effect": live if callable(live) else (lambda strict=False: set(live))},
               "read_windows": {"side_effect": windows if callable(windows) else (lambda: list(windows))},
               "read_live_groups": {"side_effect": groups if callable(groups) else (lambda w: list(groups))},
               "read_monitors": {"side_effect": monitors if callable(monitors) else (lambda: list(monitors))},
               "read_boot_id": {"return_value": "B"},
               "launch_tab": {}, "place_new_window": {},
               "wait_for_session": {"return_value": True}}
    for name, kwargs in targets.items():
        patcher = patch(f"keeper_restore.{name}", **kwargs)
        found[name] = patcher.start()
        test.addCleanup(patcher.stop)
    clock = FakeClock()          # no test may really wait: the new-window wait runs on a fake clock
    for patcher in (patch("keeper_restore.time.sleep"), patch.object(keeper_newwindow, "clock", clock),
                    patch.object(keeper_newwindow, "pause", clock.sleep)):
        patcher.start()
        test.addCleanup(patcher.stop)
    return found


# ---------------------------------------------------------------- item 1: slow window, late window adoption
class SlowWindowTests(unittest.TestCase):
    # WHAT IT DOES: A window that shows up 40 s after launch is still found, the group completes and is placed.
    def test_window_at_40_seconds_completes_and_is_placed(self):
        clock = FakeClock()
        late = {"hwnd": 500, "x": 0, "y": 0, "w": 800, "h": 600, "title": "First", "tabs": ["First"]}
        world = fake_world(self, lambda: [late] if clock.now >= 40 else [])
        with patch.object(keeper_newwindow, "clock", clock), patch.object(keeper_newwindow, "pause", clock.sleep), \
             patch.object(keeper_newwindow, "terminal_handles",
                          side_effect=lambda: {500} if clock.now >= 40 else set()):
            restored, warnings = restore_selected(
                layout(({"x": 10, "y": 20, "w": 900, "h": 700}, [(ID1, "First"), (ID2, "Second")])),
                {"0:0", "0:1"}, lock_name=LOCK)
        self.assertEqual(restored, ["First", "Second"])
        self.assertEqual(world["place_new_window"].call_args[0][0], 500)
        self.assertGreaterEqual(clock.now, 40)

    # WHAT IT DOES: A window that never appears stops only its group (with a warning) after a bounded wait;
    # the next group still restores, and the launch is remembered so a late window can be adopted.
    def test_never_appears_stops_group_and_next_group_restores(self):
        clock = FakeClock()
        second = {"hwnd": 600, "x": 1000, "y": 20, "w": 800, "h": 600, "title": "Third", "tabs": ["Third"]}
        state = {"group": 1}

        def launch(name, row):
            if row["session_id"] == ID3:
                state["group"] = 2
        world = fake_world(self, lambda: [second] if state["group"] == 2 else [])
        world["launch_tab"].side_effect = launch
        with patch.object(keeper_newwindow, "clock", clock), patch.object(keeper_newwindow, "pause", clock.sleep), \
             patch.object(keeper_newwindow, "terminal_handles",
                          side_effect=lambda: {600} if state["group"] == 2 else set()):
            restored, warnings = restore_selected(
                layout(({"x": 10, "y": 20, "w": 900, "h": 700}, [(ID1, "First"), (ID2, "Second")]),
                       ({"x": 1000, "y": 20, "w": 800, "h": 600}, [(ID3, "Third")])),
                {"0:0", "0:1", "1:0"}, lock_name=LOCK)
        self.assertEqual(restored, ["Third"])
        self.assertTrue(any("'First'" in w and "has not appeared" in w for w in warnings), warnings)
        self.assertLessEqual(clock.now, keeper_newwindow.WINDOW_WAIT_SECONDS + 5)
        self.assertGreaterEqual(clock.now, 60)
        pending = keeper_newwindow.load_pending("B")
        self.assertEqual([p["session_id"] for p in pending], [ID1])

    # WHAT IT DOES: The second Restart adopts the late window (placed + recorded) and refills it by name.
    def test_late_window_adopted_on_second_restart(self):
        snapshot = layout(({"x": 10, "y": 20, "w": 900, "h": 700}, [(ID1, "First"), (ID2, "Second")]))
        keeper_newwindow.record_pending("SessionKeeper-late", snapshot, snapshot["windows"][0], 0, ID1, "First",
                                        {"x": 10, "y": 20, "w": 900, "h": 700}, {11, 12}, "B")
        late = {"hwnd": 900, "x": 300, "y": 300, "w": 800, "h": 600, "title": "First", "tabs": ["First"]}
        screen = {"placed": False}

        def windows():
            spot = {"x": 10, "y": 20, "w": 900, "h": 700} if screen["placed"] else {}
            return [{"hwnd": 11, "x": 3000, "y": 0, "w": 500, "h": 500, "title": "x", "tabs": ["x"]},
                    dict(late, **spot)]

        def place(handle, rect, previous, maximize=False):
            screen["placed"] = True
        group = {"hwnd": 900, "tabs": [{"outcome": "MATCHED", "sessionId": ID1, "strippedTitle": "First"}]}
        world = fake_world(self, windows, live={ID1}, groups=[group])
        world["place_new_window"].side_effect = place
        with patch("keeper_restore.find_new_window") as find:
            restored, warnings = restore_selected(snapshot, {"0:0", "0:1"}, lock_name=LOCK)
        self.assertEqual(world["place_new_window"].call_args_list[0][0][0], 900)
        names = [call[0][0] for call in world["launch_tab"].call_args_list]
        self.assertEqual(names, ["SessionKeeper-late"])          # Second went INTO the late window
        find.assert_not_called()                                 # no new window was opened
        self.assertEqual(restored, ["Second"])
        from keeper_created import load_created_windows
        self.assertEqual([(o["window_name"], o["hwnd"]) for o in load_created_windows("B")],
                         [("SessionKeeper-late", 900)])
        self.assertEqual(keeper_newwindow.load_pending("B"), [])

    # WHAT IT DOES: A stranger's new window with the same title (its tab is not our chat) is never adopted.
    def test_stranger_window_with_similar_title_never_adopted(self):
        snapshot = layout(({"x": 10, "y": 20, "w": 900, "h": 700}, [(ID1, "First"), (ID2, "Second")]))
        keeper_newwindow.record_pending("SessionKeeper-late", snapshot, snapshot["windows"][0], 0, ID1, "First",
                                        {"x": 10, "y": 20, "w": 900, "h": 700}, {11}, "B")
        stranger = {"hwnd": 901, "x": 300, "y": 300, "w": 800, "h": 600, "title": "First", "tabs": ["First"]}
        for tabs in ([{"outcome": "NONE", "strippedTitle": "First"}],
                     [{"outcome": "MATCHED", "sessionId": ID5, "strippedTitle": "First"}],
                     [{"outcome": "MATCHED", "sessionId": ID1, "strippedTitle": "First"},
                      {"outcome": "NONE", "strippedTitle": "First (copy)"}]):
            owned, warnings = [], []
            adopted = keeper_newwindow.adopt_late_windows(
                owned, "B", lambda: [stranger], lambda w: [{"hwnd": 901, "tabs": tabs}],
                place=lambda *a, **k: self.fail("a stranger's window was moved"), warnings=warnings)
            self.assertEqual(adopted, [])
            self.assertEqual(owned, [])
        # a window that existed before the launch is never ours, even holding the chat
        adopted = keeper_newwindow.adopt_late_windows(
            [], "B", lambda: [dict(stranger, hwnd=11)],
            lambda w: [{"hwnd": 11, "tabs": [{"outcome": "MATCHED", "sessionId": ID1}]}],
            place=lambda *a, **k: self.fail("a pre-existing window was moved"), warnings=[])
        self.assertEqual(adopted, [])

    # WHAT IT DOES: A late window from an earlier group (other title) does not stop the next group's window.
    def test_other_late_window_does_not_block_next_group(self):
        clock = FakeClock()
        reads = [[{"hwnd": 700, "title": "Older group", "tabs": ["Older group"]},
                  {"hwnd": 701, "title": "Third", "tabs": ["Third"]}]]
        with patch.object(keeper_newwindow, "clock", clock), patch.object(keeper_newwindow, "pause", clock.sleep), \
             patch.object(keeper_newwindow, "terminal_handles", return_value={700, 701}):
            found = keeper_newwindow.find_new_window(
                set(), "Third", lambda: reads[0], session_id=ID3,
                groups=lambda w: [{"hwnd": 701, "tabs": [{"outcome": "MATCHED", "sessionId": ID3}]}])
        self.assertEqual(found["hwnd"], 701)      # two new windows: accepted only with the session-ID proof


# ---------------------------------------------------------------- item 2: one hiccup costs only its unit
class OneHiccupTests(unittest.TestCase):
    # WHAT IT DOES: A monitor flicker during group 2 of 3 skips only group 2; groups 1 and 3 restore.
    def test_monitor_flicker_on_group_two(self):
        other = dict(MONITOR, h=900)
        handles = iter([500, 600, 700])
        world = fake_world(self, [])
        snapshot = layout(({"x": 10, "y": 20, "w": 600, "h": 500}, [(ID1, "One")]),
                          ({"x": 700, "y": 20, "w": 600, "h": 500}, [(ID3, "Two")]),
                          ({"x": 10, "y": 530, "w": 600, "h": 500}, [(ID5, "Three")]))
        calls = {"n": 0}

        def flicker():          # start + group 1 fine, group 2 flickers on every retry, group 3 fine
            calls["n"] += 1
            return [other] if 3 <= calls["n"] <= 3 + keeper_restore.MONITOR_RETRIES else [MONITOR]
        world["read_monitors"].side_effect = flicker
        with patch("keeper_restore.find_new_window", side_effect=lambda *a, **k: {"hwnd": next(handles)}):
            restored, warnings = restore_selected(snapshot, {"0:0", "1:0", "2:0"}, lock_name=LOCK)
        self.assertEqual(sorted(restored), ["One", "Three"])
        self.assertTrue(any("'Two'" in w and "monitor" in w for w in warnings), warnings)

    # WHAT IT DOES: An error while adding one tab to an open window costs only that tab.
    def test_add_error_costs_only_that_tab(self):
        import keeper_target
        snapshot = layout(({"x": 10, "y": 20, "w": 900, "h": 700},
                           [(ID1, "Live"), (ID2, "A"), (ID3, "B"), (ID4, "C")]))
        live_window = {"hwnd": 77, "x": 10, "y": 20, "w": 900, "h": 700, "title": "Live", "tabs": ["Live"]}
        group = {"hwnd": 77, "tabs": [{"outcome": "MATCHED", "sessionId": ID1}]}
        fake_world(self, [live_window], live={ID1}, groups=[group])
        with patch.object(keeper_target, "add_tab", side_effect=[True, OSError("probe hiccup"), True]):
            restored, warnings = restore_selected(snapshot, {"0:1", "0:2", "0:3"}, lock_name=LOCK)
        self.assertEqual(restored, ["A", "C"])
        self.assertTrue(any("'B'" in w and "probe hiccup" in w for w in warnings), warnings)

    # WHAT IT DOES: A failed identity check still stops that window's adds, as before.
    def test_identity_failure_still_stops_window(self):
        import keeper_target
        snapshot = layout(({"x": 10, "y": 20, "w": 900, "h": 700},
                           [(ID1, "Live"), (ID2, "A"), (ID3, "B"), (ID4, "C")]))
        live_window = {"hwnd": 77, "x": 10, "y": 20, "w": 900, "h": 700, "title": "Live", "tabs": ["Live"]}
        group = {"hwnd": 77, "tabs": [{"outcome": "MATCHED", "sessionId": ID1}]}
        calls = {"n": 0}

        def ids(strict=False):
            calls["n"] += 1
            if calls["n"] >= 4:            # plan, fresh plan, tab A fine; tab B: an unidentified CLI
                raise UnidentifiedSessions([{"kind": "claude", "title": "?"}])
            return {ID1}
        fake_world(self, [live_window], live=ids, groups=[group])
        with patch.object(keeper_target, "add_tab", return_value=True) as add:
            restored, warnings = restore_selected(snapshot, {"0:1", "0:2", "0:3"}, lock_name=LOCK)
        self.assertEqual(restored, ["A"])
        self.assertEqual(add.call_count, 1)

    # WHAT IT DOES: A probe failure while planning one group skips that group only.
    def test_probe_timeout_on_one_group_skips_only_it(self):
        import subprocess
        calls = {"n": 0}
        handles = iter([500, 600])

        def windows():
            calls["n"] += 1
            if calls["n"] == 2:            # start fine; group 1's fresh plan read times out
                raise subprocess.TimeoutExpired("powershell", 45)
            return []
        fake_world(self, windows)
        snapshot = layout(({"x": 10, "y": 20, "w": 600, "h": 500}, [(ID1, "One")]),
                          ({"x": 700, "y": 20, "w": 600, "h": 500}, [(ID3, "Two")]))
        with patch("keeper_restore.find_new_window", side_effect=lambda *a, **k: {"hwnd": next(handles)}):
            restored, warnings = restore_selected(snapshot, {"0:0", "1:0"}, lock_name=LOCK)
        self.assertEqual(len(restored), 1)
        self.assertEqual(len([w for w in warnings if w.startswith("Not opened")]), 1)


# ---------------------------------------------------------------- item 3: atomic snapshot writes
class AtomicSnapshotTests(unittest.TestCase):
    # WHAT IT DOES: A kill mid-write (simulated by an exception halfway through) leaves no snapshot_*.json.
    def test_killed_mid_write_leaves_no_snapshot_file(self):
        folder = Path(self.id_folder())

        def half(data, file, **kwargs):
            file.write('{"version": 1, "windows": [')
            file.flush()
            raise KeyboardInterrupt("killed")
        with patch("keeper_core.json.dump", side_effect=half):
            with self.assertRaises(KeyboardInterrupt):
                save_snapshot({"version": 1}, folder)
        self.assertEqual(list(folder.glob("snapshot_*.json")), [])

    # WHAT IT DOES: A normal save lands under the final name and parses; an existing name is never replaced.
    def test_save_is_complete_and_never_replaces(self):
        folder = Path(self.id_folder())
        path = save_snapshot({"version": 1, "windows": []}, folder)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"version": 1, "windows": []})
        self.assertEqual([p.name for p in folder.iterdir()], [path.name])
        stamp = path.name.split("_")[1]
        with patch("keeper_core.uuid.uuid4") as fake_uuid, patch("keeper_core.datetime") as fake_time:
            fake_uuid.return_value.hex = path.name.split("_")[2][:8] + "0" * 24
            fake_time.now.return_value.strftime.return_value = stamp
            with self.assertRaises(FileExistsError):
                save_snapshot({"version": 1, "windows": ["other"]}, folder)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"version": 1, "windows": []})
        self.assertEqual([p.name for p in folder.iterdir()], [path.name])

    # WHAT IT DOES: Prune's dry run lists truncated files; --apply deletes only truncated ones older than a day.
    def test_prune_lists_truncated_and_apply_deletes_only_old_ones(self):
        folder = Path(self.id_folder())
        now = datetime.now(timezone.utc)

        def write(when, text):
            name = f"snapshot_{when.strftime('%Y%m%dT%H%M%S%fZ')}_{os.urandom(4).hex()}.json"
            path = folder / name
            path.write_text(text, encoding="utf-8")
            os.utime(path, (when.timestamp(), when.timestamp()))
            return path
        good = write(now - timedelta(minutes=5), json.dumps(
            {"version": 1, "captured_at": now.isoformat(), "boot_id": "B", "windows": []}))
        old_cut = write(now - timedelta(days=2), '{"version": 1, "windows": [{"x": 1')
        new_cut = write(now - timedelta(hours=2), '{"version": 1, "win')
        odd = write(now - timedelta(days=3), '["valid json, not a snapshot"]')
        report = keeper_prune.run_prune(folder, now=now)
        self.assertEqual(sorted(report["damaged"]), sorted([(old_cut.name, "truncated"),
                                                            (new_cut.name, "truncated"),
                                                            (odd.name, "unreadable")]))
        self.assertIn(old_cut.name, keeper_prune.report_text(report))
        self.assertTrue(old_cut.exists())
        report = keeper_prune.run_prune(folder, now=now, apply=True)
        self.assertFalse(old_cut.exists())
        self.assertTrue(new_cut.exists() and odd.exists() and good.exists())
        self.assertEqual(report["truncated_deleted"], [old_cut.name])

    # WHAT IT DOES: The hourly automatic prune never deletes a truncated file (only the manual --apply does).
    def test_automatic_prune_leaves_truncated_files(self):
        folder = Path(self.id_folder())
        when = datetime.now(timezone.utc) - timedelta(days=2)
        path = folder / f"snapshot_{when.strftime('%Y%m%dT%H%M%S%fZ')}_abcdef01.json"
        path.write_text('{"version": 1', encoding="utf-8")
        os.utime(path, (when.timestamp(), when.timestamp()))
        keeper_prune.run_prune(folder, apply=True, automatic=True)
        self.assertTrue(path.exists())

    # WHAT IT DOES: A fresh temp folder per test.
    def id_folder(self):
        import tempfile
        folder = tempfile.mkdtemp(prefix="keeper_batch3_")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        return folder


# ---------------------------------------------------------------- item 4: maximised windows
class MaximisedTests(unittest.TestCase):
    # WHAT IT DOES: The probe's maximised flag reaches the snapshot; windows without it stay as before.
    def test_capture_carries_maximized(self):
        report, _, _ = match_inventory([{"hwnd": 1, "x": -8, "y": -8, "w": 1936, "h": 1056, "title": "A",
                                         "tabs": ["A"], "maximized": True},
                                        {"hwnd": 2, "x": 0, "y": 0, "w": 800, "h": 600, "title": "B",
                                         "tabs": ["B"]}], [])
        self.assertIs(report[0]["maximized"], True)
        self.assertNotIn("maximized", report[1])

    # WHAT IT DOES: A maximised window is placed at its monitor's work area, then maximised.
    def test_maximised_placed_on_its_monitor_then_maximised(self):
        world = fake_world(self, [], monitors=[MONITOR, dict(MONITOR, name="M2", x=1920, primary=False)])
        snapshot = layout(({"x": 1912, "y": -8, "w": 1936, "h": 1056, "maximized": True}, [(ID1, "Big")]))
        with patch("keeper_restore.find_new_window", return_value={"hwnd": 500}):
            restored, warnings = restore_selected(snapshot, {"0:0"}, lock_name=LOCK)
        handle, rect = world["place_new_window"].call_args[0][:2]
        self.assertEqual(rect, {"x": 1920, "y": 0, "w": 1920, "h": 1040})
        self.assertIs(world["place_new_window"].call_args.kwargs.get("maximize"), True)

    # WHAT IT DOES: A snapshot without the field restores exactly as before: same rect, no maximise switch.
    def test_old_snapshot_without_field_unchanged(self):
        world = fake_world(self, [])
        snapshot = layout(({"x": 10, "y": 20, "w": 900, "h": 700}, [(ID1, "Old")]))
        with patch("keeper_restore.find_new_window", return_value={"hwnd": 500}):
            restore_selected(snapshot, {"0:0"}, lock_name=LOCK)
        self.assertEqual(world["place_new_window"].call_args[0][1], {"x": 10, "y": 20, "w": 900, "h": 700})
        self.assertFalse(world["place_new_window"].call_args.kwargs.get("maximize", False))

    # WHAT IT DOES: place_new_window adds -Maximize only when asked; the old argument list is otherwise unchanged.
    def test_place_window_switch(self):
        rect = {"x": 1, "y": 2, "w": 300, "h": 200}
        with patch("keeper_restore.subprocess.run") as run:
            run.return_value.returncode = 0
            keeper_restore.place_new_window(9, rect, set())
            plain = run.call_args[0][0]
            keeper_restore.place_new_window(9, rect, set(), maximize=True)
            maxed = run.call_args[0][0]
        self.assertNotIn("-Maximize", plain)
        self.assertEqual(maxed, plain + ["-Maximize"])


# ---------------------------------------------------------------- item 5: missing monitor cascade
class MissingMonitorTests(unittest.TestCase):
    # WHAT IT DOES: Three windows from a monitor that is gone land at three different visible spots.
    def test_three_windows_cascade(self):
        handles = iter([500, 600, 700])
        world = fake_world(self, [])
        snapshot = layout(({"x": 4000, "y": 0, "w": 1200, "h": 800}, [(ID1, "A")]),
                          ({"x": 4100, "y": 50, "w": 1200, "h": 800}, [(ID3, "B")]),
                          ({"x": 4200, "y": 100, "w": 1200, "h": 800}, [(ID5, "C")]))
        with patch("keeper_restore.find_new_window", side_effect=lambda *a, **k: {"hwnd": next(handles)}):
            restored, warnings = restore_selected(snapshot, {"0:0", "1:0", "2:0"}, lock_name=LOCK)
        rects = [call[0][1] for call in world["place_new_window"].call_args_list]
        self.assertEqual(len(rects), 3)
        self.assertEqual(len({(r["x"], r["y"]) for r in rects}), 3)
        for r in rects:
            self.assertTrue(r["x"] >= 0 and r["y"] >= 0 and r["x"] + r["w"] <= 1920 and r["y"] + r["h"] <= 1040, r)
            self.assertTrue(r["w"] >= 200 and r["h"] >= 150)

    # WHAT IT DOES: The first fallback window keeps exactly today's spot (primary origin, clipped size).
    def test_first_fallback_unchanged(self):
        warnings = []
        plans, _ = plan_restore(layout(({"x": 4000, "y": 0, "w": 1200, "h": 800}, [(ID1, "A")])),
                                {"0:0"}, set(), [], [MONITOR])
        self.assertEqual(plans[0]["rect"], {"x": 0, "y": 0, "w": 1200, "h": 800})

    # WHAT IT DOES: Two minimised windows also open at different spots.
    def test_minimised_windows_cascade(self):
        plans, _ = plan_restore(layout(({"x": -32000, "y": -32000, "w": 160, "h": 28}, [(ID1, "A")]),
                                       ({"x": -32000, "y": -32000, "w": 160, "h": 28}, [(ID3, "B")])),
                                {"0:0", "1:0"}, set(), [], [MONITOR])
        self.assertNotEqual((plans[0]["rect"]["x"], plans[0]["rect"]["y"]),
                            (plans[1]["rect"]["x"], plans[1]["rect"]["y"]))


if __name__ == "__main__":
    unittest.main()

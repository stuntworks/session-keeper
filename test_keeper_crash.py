# Last modified: 2026-11-10 | Change: Labels show the layout's own time and age; fake transcripts say entrypoint cli; facts use the fixed clock | Full history: git log
"""Fake snapshots and fake transcripts in temporary folders only, a fake event-log reader, a fixed clock.
Never touches the real data folder, ~/.claude, ~/.codex, the event log or any terminal."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import keeper_app
import keeper_fast_history
import keeper_reload
from keeper_start import crash_history
from keeper_chooser import captured, chooser_entries
from keeper_core import plan_restore, recovery_rows
from keeper_crash import (CRASH_TITLE, burst_sessions, crash_facts, read_crash_times, shown_layout,
                          start_index)
from keeper_fast_history import fast_history
from keeper_home import minimised_rect
from keeper_prune import plan_prune, report_text, run_prune, scan
from keeper_sessions import list_transcripts

NOW = datetime(2026, 11, 10, 12, 0).astimezone()          # local, like the chooser
CRASH = datetime(2026, 11, 10, 7, 0).astimezone()
MONITORS = [{"x": 0, "y": 0, "w": 2560, "h": 1400, "primary": True}]


# WHAT IT DOES: A fixed session id per (prefix, number), in the exact UUID shape the app requires.
def sid(prefix, number):
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{prefix}-{number}"))


# WHAT IT DOES: One saved layout with `count` matched Claude tabs in one window (ids prefix-0..count-1).
def layout(when, count, folder, boot="boot-now", prefix="chat"):
    tabs = [{"rawTitle": f"{prefix} {n}", "strippedTitle": f"{prefix} {n}", "outcome": "MATCHED",
             "kind": "claude", "sessionId": sid(prefix, n), "cwd": str(folder), "role": "interactive"}
            for n in range(count)]
    return {"version": 1, "coords": "physical", "captured_at": when.astimezone(timezone.utc).isoformat(),
            "boot_id": boot, "monitors": MONITORS,
            "windows": [{"x": 100, "y": 100, "w": 1200, "h": 800, "z": 0, "tabs": tabs}],
            "counts": {"tabs_total": count, "matched": count}}


# WHAT IT DOES: Writes a layout the way save_snapshot names it (file stamp a few seconds after capture).
def write(folder, snapshot, number):
    when = datetime.fromisoformat(snapshot["captured_at"])
    stamp = (when + timedelta(seconds=7)).strftime("%Y%m%dT%H%M%S%fZ")
    path = Path(folder) / f"snapshot_{stamp}_{number:08x}.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


# WHAT IT DOES: A run of fake facts with no crash, no transcripts and every folder present.
def facts(history, live, crashes, **extra):
    return crash_facts(history, live, crashes, burst=extra.pop("burst", lambda start, end: []),
                       is_dir=extra.pop("is_dir", lambda folder: True), now=extra.pop("now", NOW), **extra)


class CrashTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="keeper_crash_"))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.project = self.folder / "project"
        self.project.mkdir()
        self.data = self.folder / "data"
        self.data.mkdir()

    # WHAT IT DOES: 06:00-06:59 every 2 min with 20 chats, a crash at 07:00, one 2-chat layout AT 07:00 and
    # then 2-chat layouts every 2 min from 07:30 to 12:00 (the owner reopened two tabs by hand).
    def build(self, big=20, small=2):
        number = 0
        for minute in range(60, 0, -2):
            number += 1
            write(self.data, layout(CRASH - timedelta(minutes=minute) + timedelta(seconds=30), big, self.project),
                  number)
        write(self.data, layout(CRASH, small, self.project), number + 1)
        moment = CRASH + timedelta(minutes=30)
        while moment <= NOW:
            number += 2
            write(self.data, layout(moment, small, self.project), number)
            moment += timedelta(minutes=2)

    # WHAT IT DOES: The screen's history, with only the newest 20 files read in full (forces the crash probe).
    def history(self, before=()):
        with patch.object(keeper_fast_history, "RECENT", 20):
            return fast_history(self.data, NOW, before=before)


class BeforeTheCrashTests(CrashTestCase):
    def test_event_crash_picks_the_last_layout_strictly_before_it(self):
        self.build()
        history = self.history(before=[CRASH])
        found = facts(history, set(), [CRASH])
        crash = found["crash"]
        self.assertEqual(crash["source"], "event")
        chosen = history[crash["index"]][1]
        self.assertEqual(captured(chosen), (CRASH - timedelta(minutes=2) + timedelta(seconds=30)).astimezone())
        self.assertEqual(chosen["counts"]["matched"], 20)
        self.assertEqual(captured(history[0][1]), NOW)             # newer, small layouts exist
        label, index = chooser_entries(history, NOW, crash=crash)[0]
        self.assertEqual((label, index), ("Before the crash - saved Nov 10, 6:58 AM - 5 hours ago", crash["index"]))
        self.assertTrue(any(label.startswith("Latest") for label, _ in chooser_entries(history, NOW, crash)))
        self.assertTrue(crash["preselect"])
        self.assertEqual(start_index(history, found), crash["index"])

    def test_not_preselected_once_everything_from_before_the_crash_is_open(self):
        self.build()
        history = self.history(before=[CRASH])
        live = {sid("chat", n) for n in range(20)}
        found = facts(history, live, [CRASH])
        self.assertFalse(found["crash"]["preselect"])
        self.assertEqual(start_index(history, found), 0)
        self.assertEqual(chooser_entries(history, NOW, found["crash"])[0][0],
                         "Before the crash - saved Nov 10, 6:58 AM - 5 hours ago")

    def test_no_crash_means_no_entry_and_latest_unchanged(self):
        self.build()
        history = self.history()
        found = facts(history, set(), [])                         # log read fine, no crash: drops are ignored
        self.assertIsNone(found["crash"])
        self.assertEqual(chooser_entries(history, NOW, found["crash"]), chooser_entries(history, NOW))
        self.assertTrue(chooser_entries(history, NOW)[0][0].startswith("Latest"))
        self.assertEqual(start_index(history, found), 0)
        self.assertIs(shown_layout(history[0][1], found, set()), history[0][1])

    def test_event_log_unavailable_falls_back_to_the_sharp_drop(self):
        self.build()
        with patch.object(keeper_fast_history, "RECENT", 20):
            history = crash_history(None, self.data, NOW)         # refines the drop with the layouts between
        found = facts(history, set(), None)
        crash = found["crash"]
        self.assertEqual(crash["source"], "drop")
        self.assertEqual(history[crash["index"]][1]["counts"]["matched"], 20)
        self.assertEqual(captured(history[crash["index"]][1]),
                         (CRASH - timedelta(minutes=2) + timedelta(seconds=30)).astimezone())
        self.assertTrue(chooser_entries(history, NOW, crash)[0][0].startswith("Before many tabs closed - saved "))

    def test_small_change_is_not_a_drop(self):
        self.build(big=4, small=3)
        self.assertIsNone(facts(self.history(), set(), None)["crash"])

    def test_event_reader_fails_soft(self):
        ok = lambda out, code=0: (lambda command, timeout: subprocess.CompletedProcess(command, code, out, ""))
        times = read_crash_times(run=ok("2026-11-10T05:08:42.4243743Z\nSESSION-KEEPER-DONE\n"))
        self.assertEqual(times, [datetime(2026, 11, 10, 5, 8, 42, 424374, tzinfo=timezone.utc).astimezone()])
        self.assertEqual(read_crash_times(run=ok("SESSION-KEEPER-DONE\n")), [])
        self.assertIsNone(read_crash_times(run=ok("", 1)))
        self.assertIsNone(read_crash_times(run=ok("2026-11-10T05:08:42Z\n")))         # cut off: not trusted

        def slow(command, timeout):
            raise subprocess.TimeoutExpired(command, timeout)
        self.assertIsNone(read_crash_times(run=slow))


class PruneTests(CrashTestCase):
    # WHAT IT DOES: 06:29 is the last layout before a 06:30 crash; 06:31-06:59 are newer in the same clock hour.
    def build_hour(self, after):
        before = write(self.data, layout(datetime(2026, 11, 10, 6, 29).astimezone(), 20, self.project), 1)
        for number, minute in enumerate(range(31, 60, 2), start=2):
            write(self.data, layout(datetime(2026, 11, 10, 6, minute).astimezone(), after, self.project), number)
        return before

    def test_prune_keeps_the_layout_before_a_crash(self):
        before = self.build_hour(after=20)                          # no drop: only the event can save it
        crash = datetime(2026, 11, 10, 6, 30).astimezone()
        unprotected = plan_prune(scan(self.data), NOW)[1]
        self.assertIn(before.name, [Path(p).name for p in unprotected])   # the hourly rule alone deletes it
        report = run_prune(self.data, NOW, apply=False, crash_times=[crash])
        self.assertEqual([name for name, _ in report["protected"]], [before.name])
        self.assertIn("crash", report["protected"][0][1])
        self.assertEqual(report["delete"], len(unprotected) - 1)
        self.assertTrue(before.exists())
        self.assertEqual(len(list(self.data.glob("*.json"))), 16)  # dry run: nothing deleted
        self.assertIn("last layouts before a crash or a sharp drop", report_text(report))

    def test_prune_keeps_the_layout_before_a_sharp_drop(self):
        before = self.build_hour(after=2)
        report = run_prune(self.data, NOW, apply=False)
        self.assertEqual([name for name, _ in report["protected"]], [before.name])
        self.assertIn("many tabs closed", report["protected"][0][1])

    def test_protection_ends_after_the_longest_tier(self):
        before = self.build_hour(after=20)
        crash = datetime(2026, 11, 10, 6, 30).astimezone()
        report = run_prune(self.data, NOW + timedelta(days=31), apply=False, crash_times=[crash])
        self.assertNotIn(before.name, [name for name, _ in report["protected"]])


class DiedInTheCrashTests(CrashTestCase):
    # WHAT IT DOES: A fake ~/.claude/projects transcript, last written at `when`.
    def transcript(self, session_id, when, cwd=True):
        folder = self.folder / "claude" / "C--project"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session_id}.jsonl"
        lines = [{"type": "user", "timestamp": "2026-11-10T04:00:00Z", "entrypoint": "cli",
                  **({"cwd": str(self.project)} if cwd else {})},
                 {"type": "custom-title", "customTitle": f"title {session_id[:4]}"}]
        path.write_text("\n".join(json.dumps(line, separators=(",", ":")) for line in lines) + "\n",
                        encoding="utf-8")
        os.utime(path, (when.timestamp(), when.timestamp()))
        return path

    def burst(self, start, end):
        lister = lambda claude_root: list_transcripts(claude_root, self.folder / "no-codex")
        return burst_sessions(start, end, claude_root=self.folder / "claude", lister=lister)

    def test_burst_list_excludes_live_and_shown_sessions(self):
        in_layout, live, dead, no_folder, earlier = (sid("t", n) for n in range(5))
        for name in (in_layout, live, dead):
            self.transcript(name, CRASH + timedelta(seconds=40))
        self.transcript(no_folder, CRASH + timedelta(seconds=80), cwd=False)
        self.transcript(earlier, CRASH - timedelta(minutes=5))
        snapshot = layout(CRASH - timedelta(minutes=1), 1, self.project)
        snapshot["windows"][0]["tabs"][0]["sessionId"] = in_layout
        history = [(Path("x.json"), snapshot)]
        found = crash_facts(history, {live}, [CRASH], burst=self.burst, monitors=MONITORS)
        self.assertEqual({item["session_id"] for item in found["died"]}, {in_layout, live, dead, no_folder})
        shown = shown_layout(snapshot, found, {live})
        extra = shown["windows"][-1]
        self.assertTrue(extra["crash_group"])
        self.assertEqual(extra["windowTitle"], CRASH_TITLE)
        self.assertEqual([tab["sessionId"] for tab in extra["tabs"]], [dead, no_folder])
        self.assertEqual(extra["tabs"][0]["strippedTitle"], f"title {dead[:4]}")
        self.assertIn("2 chat(s) open at the crash", shown["note"])
        self.assertIn("Codex chats are not checked", shown["note"])
        rows = {row["session_id"]: row for row in recovery_rows(shown)}
        self.assertIn(dead, rows)
        self.assertNotIn(no_folder, rows)                           # shown, but no folder is ever guessed
        self.assertIsNone(extra["tabs"][1]["cwd"])
        self.assertEqual(len(snapshot["windows"]), 1)               # the saved layout itself is untouched

    def test_synthetic_group_restores_through_plan_restore(self):
        dead = [sid("d", n) for n in range(3)]
        extras = {"crash": {"time": CRASH}, "gone": set(), "monitors": None,
                  "died": [{"session_id": name, "title": name[:4], "cwd": str(self.project), "stopped": None,
                            "entrypoint": "cli"} for name in dead]}
        snapshot = layout(CRASH - timedelta(minutes=1), 2, self.project)
        shown = shown_layout(snapshot, extras, set())
        rows = [row for row in recovery_rows(shown) if row["session_id"] in dead]
        self.assertEqual(len(rows), 3)
        self.assertEqual(shown["windows"][-1]["z"], -1)
        plans, warnings = plan_restore(shown, {row["key"] for row in rows}, set(), [], MONITORS)
        self.assertEqual(warnings, [])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["rect"], minimised_rect(MONITORS))
        self.assertEqual([row["session_id"] for row in plans[0]["tabs"]], dead)
        plans, warnings = plan_restore(shown, {row["key"] for row in rows}, {dead[0]}, [], MONITORS)
        self.assertEqual([row["session_id"] for row in plans[0]["tabs"]], dead[1:])
        self.assertTrue(any("Not opened twice" in line for line in warnings))

    def test_missing_folder_is_shown_but_not_restorable(self):
        gone = self.folder / "deleted-project"
        snapshot = layout(CRASH - timedelta(minutes=1), 2, self.project)
        snapshot["windows"][0]["tabs"][1]["cwd"] = str(gone)
        found = crash_facts([(Path("x.json"), snapshot)], set(), [], burst=lambda s, e: [])
        self.assertEqual(found["gone"], {str(gone)})
        shown = shown_layout(snapshot, found, set())
        self.assertEqual(len(shown["windows"][0]["tabs"]), 2)        # still visible
        self.assertTrue(shown["windows"][0]["tabs"][1]["folder_missing"])
        self.assertEqual([row["key"] for row in recovery_rows(shown)], ["0:0"])
        self.assertNotIn("folder_missing", snapshot["windows"][0]["tabs"][1])
        self.assertEqual(len(recovery_rows(snapshot)), 2)            # the saved layout is unchanged


class EvidenceTests(CrashTestCase):
    def test_saved_snapshot_files_are_never_modified(self):
        self.build()
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.data.iterdir()}
        history = self.history(before=[CRASH])
        extras = {"crash": None, "gone": {str(self.project)}, "monitors": None, "died": []}
        found = facts(history, set(), [CRASH], burst=lambda s, e: [{"session_id": sid("z", 1), "title": "z",
                                                                     "cwd": str(self.project), "stopped": None,
                                                                     "entrypoint": "cli"}])
        for _, snapshot in history:
            shown = shown_layout(snapshot, found, set())
            shown_layout(snapshot, extras, set())
            plan_restore(shown, {row["key"] for row in recovery_rows(shown)}, set(), [], MONITORS)
        run_prune(self.data, NOW, apply=False, crash_times=[CRASH])
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.data.iterdir()}
        self.assertEqual(before, after)


class ScreenWiringTests(unittest.TestCase):
    def test_open_recovery_opens_on_the_preselected_layout(self):
        history = [(Path("new.json"), {"captured_at": NOW.isoformat()}),
                   (Path("before.json"), {"captured_at": CRASH.isoformat()})]
        extras = {"crash": {"index": 1, "preselect": True}}
        with patch.object(keeper_app, "RecoveryWindow") as screen, \
                patch.object(keeper_app, "show_recorder_health"), \
                patch.object(keeper_app, "ReloadWatch"), patch.object(keeper_app, "publish_window"):
            keeper_app.open_recovery(MagicMock(), history, set(), extras)
        self.assertEqual(screen.call_args.args[1], Path("before.json"))
        self.assertIs(screen.call_args.kwargs["extras"], extras)

    def test_reload_passes_fresh_crash_facts(self):
        root = MagicMock()
        root.winfo_exists.return_value = True
        view = SimpleNamespace(root=root, busy=False, countdown_job=None, native_wait=None, pick_token=None,
                               countdown_text=MagicMock())
        extras = {"crash": None}
        watch = keeper_reload.ReloadWatch(view, Path(tempfile.gettempdir()) / f"no-note-{uuid.uuid4().hex}",
                                          load=None)
        seen = []
        with patch.object(keeper_reload, "reload_history", side_effect=lambda *args: seen.append(args)):
            watch.loaded(([(Path("a.json"), {})], set(), extras))
        self.assertIs(seen[0][3], extras)


if __name__ == "__main__":
    unittest.main()

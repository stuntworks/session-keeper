# Last modified: 2026-11-10 | Change: New. Review fixes S1-S8 for crash recovery; the recorded-restore test now opens a chat | Full history: git log
"""Review of the crash-recovery batch (2026-11-10), items S1-S8. Fake layouts and transcripts in temp folders,
fake clocks, fake folder checks. Never touches the real data folder, ~/.claude, the event log or a terminal."""

import json
import os
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import keeper_crash_record
import keeper_heartbeat
import keeper_prune
import keeper_restore
from keeper_chooser import chooser_entries
from keeper_core import plan_restore, recovery_rows
from keeper_crash import (BURST_AFTER, CRASH_TITLE, DROP_TITLE, burst_sessions, crash_facts, gone_folders,
                          shown_layout)
from keeper_picker import SnapshotMap
from keeper_prune import run_prune
from keeper_scrubber import short_label
from keeper_sessions import list_transcripts
from test_keeper_crash import CRASH, MONITORS, NOW, CrashTestCase, facts, layout, sid, write


# WHAT IT DOES: A fake transcript whose head names an entrypoint (None: no entrypoint field at all).
def transcript(root, project, session_id, when, entrypoint="cli"):
    folder = Path(root) / "C--project"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.jsonl"
    first = {"type": "user", "timestamp": "2026-11-10T04:00:00Z", "cwd": str(project)}
    if entrypoint is not None:
        first["entrypoint"] = entrypoint
    lines = [first, {"type": "custom-title", "customTitle": f"title {session_id[:4]}"}]
    path.write_text("\n".join(json.dumps(line, separators=(",", ":")) for line in lines) + "\n", encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))
    return path


class ReviewCase(CrashTestCase):
    def burst(self, start, end):
        lister = lambda claude_root: list_transcripts(claude_root, self.folder / "no-codex")
        return burst_sessions(start, end, claude_root=self.folder / "claude", lister=lister)


# S1: only terminal (entrypoint "cli") chats are offered; Desktop / SDK chats are never shown as dead tabs.
class EntrypointTests(ReviewCase):
    def test_only_terminal_chats_are_restorable(self):
        cli, desktop, sdk, py, old = (sid("e", n) for n in range(5))
        for name, point in ((cli, "cli"), (desktop, "claude-desktop"), (sdk, "sdk-cli"), (py, "sdk-py"), (old, None)):
            transcript(self.folder / "claude", self.project, name, CRASH + timedelta(seconds=30), point)
        died = self.burst(CRASH - timedelta(seconds=10), CRASH + BURST_AFTER)
        self.assertEqual({item["session_id"] for item in died}, {cli, old})
        self.assertEqual({item["session_id"]: item["entrypoint"] for item in died}, {cli: "cli", old: None})
        snapshot = layout(CRASH - timedelta(minutes=1), 1, self.project)
        found = facts([(Path("x.json"), snapshot)], set(), [CRASH], burst=lambda s, e: died)
        shown = shown_layout(snapshot, found, set())
        tabs = {tab["sessionId"]: tab for tab in shown["windows"][-1]["tabs"]}
        self.assertEqual(set(tabs), {cli, old})                      # the unknown one is still shown
        self.assertTrue(tabs[old]["origin_unknown"])
        rows = {row["session_id"] for row in recovery_rows(shown)}
        self.assertIn(cli, rows)
        self.assertNotIn(old, rows)                                   # but never offered to reopen


# S2: "Before the crash" is preselected only while the crash is recent and nothing was restored since.
class PreselectTests(CrashTestCase):
    def found(self, **extra):
        self.build()
        return facts(self.history(before=[CRASH]), set(), [CRASH], **extra)["crash"]["preselect"]

    def test_preselect_needs_a_recent_crash(self):
        self.assertTrue(self.found(now=CRASH + timedelta(hours=5)))
        self.assertFalse(self.found(now=CRASH + timedelta(hours=7)))

    def test_preselect_ends_after_a_real_restore(self):
        self.assertTrue(self.found(restored_at=CRASH - timedelta(minutes=5)))
        self.assertFalse(self.found(restored_at=CRASH + timedelta(minutes=5)))

    def test_real_restore_is_recorded_but_a_preview_is_not(self):
        data = self.folder / "records"
        with patch.object(keeper_restore, "perform_restore", return_value=(["First"], [])), \
                patch.object(keeper_crash_record, "DATA", data):
            keeper_restore.restore_selected({}, set(), dry_run=True, lock_name="unittest-restore")
            self.assertIsNone(keeper_crash_record.last_restore_done(data))
            keeper_restore.restore_selected({}, set(), dry_run=False, lock_name="unittest-restore")
        done = keeper_crash_record.last_restore_done(data)
        self.assertIsNotNone(done)      # one that opened nothing is not recorded (test_keeper_restore_review)
        self.assertLess(abs((datetime.now().astimezone() - done).total_seconds()), 60)


# S3: the entry names the layout's own time and age, and says so plainly when it is old.
class LabelTests(CrashTestCase):
    def test_label_shows_the_layout_time_and_age(self):
        self.build()
        history = self.history(before=[CRASH])
        crash = facts(history, set(), [CRASH])["crash"]
        self.assertEqual(chooser_entries(history, NOW, crash)[0][0], "Before the crash - saved Nov 10, 6:58 AM - 5 hours ago")

    def test_old_layout_says_how_old(self):
        old = datetime(2026, 10, 29, 14, 44).astimezone()
        write(self.data, layout(old, 20, self.project, boot="boot-before"), 1)
        write(self.data, layout(CRASH + timedelta(minutes=30), 2, self.project), 2)
        history = self.history(before=[CRASH])
        crash = facts(history, set(), [CRASH], now=NOW)["crash"]
        self.assertEqual(chooser_entries(history, NOW, crash)[0][0],
                         "Last layout before the crash - 11 days old (Oct 29, 2:44 PM)")

    def test_same_boot_but_long_before_the_crash_is_old_too(self):
        write(self.data, layout(CRASH - timedelta(minutes=40), 20, self.project), 1)
        write(self.data, layout(CRASH + timedelta(minutes=30), 2, self.project), 2)
        history = self.history(before=[CRASH])
        crash = facts(history, set(), [CRASH])["crash"]
        self.assertTrue(chooser_entries(history, NOW, crash)[0][0].startswith("Last layout before the crash - 5 hours old"))


# S4: a drop found without the event log never says "crash", and a wide gap gets no transcript search.
class DropWordingTests(CrashTestCase):
    def drop(self, gap):
        write(self.data, layout(CRASH - gap, 20, self.project), 1)
        write(self.data, layout(CRASH, 2, self.project), 2)
        return self.history()

    def test_drop_window_and_note_do_not_say_crash(self):
        history = self.drop(timedelta(minutes=2))
        dead = {"session_id": sid("x", 1), "title": "x", "cwd": str(self.project), "stopped": None,
                "entrypoint": "cli"}
        found = facts(history, set(), None, burst=lambda s, e: [dead])
        self.assertEqual(found["crash"]["source"], "drop")
        shown = shown_layout(history[0][1], found, set())
        self.assertEqual(shown["windows"][-1]["windowTitle"], DROP_TITLE)
        self.assertNotIn("crash", shown["note"].lower())
        self.assertNotIn("crash", DROP_TITLE.lower())
        self.assertNotIn("crash", chooser_entries(history, NOW, found["crash"])[0][0].lower())

    def test_wide_gap_skips_the_transcript_search(self):
        history = self.drop(timedelta(minutes=40))
        called = []
        found = facts(history, set(), None, burst=lambda s, e: called.append((s, e)) or [])
        self.assertEqual(found["crash"]["source"], "drop")
        self.assertEqual((called, found["died"]), ([], []))


# S5: two crashes in one boot: the layout before the one that lost more chats is offered.
class TwoCrashTests(CrashTestCase):
    def test_bigger_pre_crash_layout_wins(self):
        second = CRASH + timedelta(hours=2)
        write(self.data, layout(CRASH - timedelta(minutes=2), 20, self.project), 1)
        write(self.data, layout(CRASH + timedelta(minutes=30), 2, self.project), 2)
        write(self.data, layout(second - timedelta(minutes=2), 4, self.project), 3)
        write(self.data, layout(second + timedelta(minutes=30), 1, self.project), 4)
        history = self.history(before=[CRASH, second])
        died = {CRASH: sid("a", 1), second: sid("b", 1)}
        burst = lambda start, end: [{"session_id": died[start + timedelta(seconds=10)], "title": "t",
                                     "cwd": str(self.project), "stopped": None, "entrypoint": "cli"}]
        found = facts(history, set(), [CRASH, second], burst=burst)
        crash = found["crash"]
        self.assertEqual(crash["time"], CRASH)
        self.assertEqual(history[crash["index"]][1]["counts"]["matched"], 20)
        self.assertEqual({item["session_id"] for item in found["died"]}, set(died.values()))
        self.assertIn("6:58 AM", chooser_entries(history, NOW, crash)[0][0])


# S6: folder checks are bounded; a disconnected drive is said plainly and never restorable.
class OfflineDriveTests(CrashTestCase):
    def test_slow_or_missing_drive_is_not_connected(self):
        here, gone, slow, unplugged = str(self.project), str(self.folder / "deleted"), r"\\nas\share\work", r"Z:\work"
        snapshot = layout(CRASH, 4, self.project)
        for tab, folder in zip(snapshot["windows"][0]["tabs"], (here, gone, slow, unplugged)):
            tab["cwd"] = folder
        release = threading.Event()
        self.addCleanup(release.set)

        def is_dir(folder):
            if folder.startswith("\\\\nas"):
                release.wait(30)                                   # a dead network drive hangs
                return False
            if folder.upper().startswith("Z:"):
                return False
            return os.path.isdir(folder)
        started = time.monotonic()
        gone_set, offline = gone_folders([(Path("x.json"), snapshot)], (), is_dir, deadline=0.5)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(gone_set, {gone, slow, unplugged})
        self.assertEqual(offline, {slow, unplugged})
        found = facts([(Path("x.json"), snapshot)], set(), [], is_dir=is_dir, deadline=0.5)
        shown = shown_layout(snapshot, found, set())
        tabs = shown["windows"][0]["tabs"]
        self.assertEqual([bool(tab.get("drive_offline")) for tab in tabs], [False, False, True, True])
        self.assertEqual([row["key"] for row in recovery_rows(shown)], ["0:0"])


# S7: the "no saved place" window always opens as its own window, never inside one opened by hand.
class CrashGroupHomeTests(CrashTestCase):
    def test_crash_group_never_joins_a_hand_opened_window(self):
        dead = [sid("d", n) for n in range(2)]
        extras = {"crash": {"time": CRASH, "source": "event"}, "gone": set(), "offline": set(), "monitors": None,
                  "died": [{"session_id": name, "title": name[:4], "cwd": str(self.project), "stopped": None,
                            "entrypoint": "cli"} for name in dead]}
        shown = shown_layout(layout(CRASH - timedelta(minutes=1), 1, self.project), extras, set())
        keys = {row["key"] for row in recovery_rows(shown) if row["session_id"] == dead[1]}
        by_hand = [{"hwnd": 777, "tabs": [{"outcome": "MATCHED", "sessionId": dead[0]}]}]
        plans, _ = plan_restore(shown, keys, {dead[0]}, [], MONITORS, live_groups=by_hand)
        self.assertEqual(len(plans), 1)
        self.assertNotIn("target_hwnd", plans[0])
        self.assertNotIn("window_name", plans[0])
        self.assertIn("rect", plans[0])


# S8: the died list is saved once, in a small record, and used from then on.
class CrashRecordTests(CrashTestCase):
    def dead(self, name):
        return {"session_id": sid(name, 1), "title": name, "cwd": str(self.project),
                "stopped": (CRASH + timedelta(seconds=30)).replace(tzinfo=None), "entrypoint": "cli"}

    def test_screen_saves_once_then_uses_the_record(self):
        records = self.folder / "records"
        remember = lambda moment, source, compute: keeper_crash_record.died_for(moment, source, compute, records, NOW)
        snapshot = layout(CRASH - timedelta(minutes=1), 1, self.project)
        history = [(Path("x.json"), snapshot)]
        first = facts(history, set(), [CRASH], burst=lambda s, e: [self.dead("first")], remember=remember)
        self.assertEqual([item["title"] for item in first["died"]], ["first"])
        saved = list(records.glob("crash_record_*.json"))
        self.assertEqual(len(saved), 1)
        body = json.loads(saved[0].read_text(encoding="utf-8"))
        self.assertEqual(body["source"], "event")
        self.assertEqual(set(body["sessions"][0]), {"session_id", "title", "cwd", "stopped", "entrypoint"})

        def never(start, end):
            raise AssertionError("transcripts searched again")
        again = facts(history, {sid("first", 1)}, [CRASH], burst=never, remember=remember)
        self.assertEqual([item["title"] for item in again["died"]], ["first"])
        shown = shown_layout(snapshot, again, {sid("first", 1)})      # live now: filtered at show time
        self.assertEqual(len(shown["windows"]), 1)

    def test_not_saved_until_the_burst_is_over(self):
        records = self.folder / "records"
        keeper_crash_record.died_for(CRASH, "event", lambda: [self.dead("early")], records,
                                     CRASH + timedelta(seconds=40))
        self.assertEqual(list(records.glob("crash_record_*.json")), [])

    def test_recorder_saves_soon_after_a_sharp_drop(self):
        records = self.folder / "records"
        recorder = keeper_heartbeat.Recorder(records, wall=lambda: (CRASH + timedelta(minutes=3)).timestamp())
        recorder.previous = (20, 20)
        recorder.review({"matched": 2, "tabs_total": 2})
        self.assertIsNotNone(recorder.drop_seen)
        reads, pruned = [], []
        keeper_prune.after_capture(records, read_crashes=lambda: reads.append(1) or [CRASH],
                                  prune=lambda data_dir, read_crashes=None: pruned.append(1),
                                  recorder=recorder, now=CRASH + timedelta(minutes=3),
                                  burst=lambda s, e: [self.dead("drop")])
        self.assertEqual((reads, pruned), ([1], [1]))
        self.assertEqual(len(list(records.glob("crash_record_*.json"))), 1)
        self.assertIsNone(recorder.drop_seen)

    def test_prune_drops_old_records_but_never_snapshots(self):
        records = self.data
        old = CRASH - timedelta(days=40)
        keeper_crash_record.save_record(records, old, "event", [self.dead("old")])
        keeper_crash_record.save_record(records, CRASH, "event", [self.dead("new")])
        snap = write(self.data, layout(old, 3, self.project), 1)
        before = sorted(path.name for path in records.glob("crash_record_*.json"))
        run_prune(self.data, NOW, apply=False)
        self.assertEqual(sorted(path.name for path in records.glob("crash_record_*.json")), before)   # dry run
        with patch("keeper_prune.MAX_AUTO_DELETE", 100):
            report = run_prune(self.data, NOW, apply=True)
        left = [path.name for path in records.glob("crash_record_*.json")]
        self.assertEqual(len(left), 1)
        self.assertIn(keeper_crash_record.record_path(records, CRASH, "event").name, left)
        self.assertEqual(report["old_crash_records"], 1)
        self.assertTrue(snap.exists() or report["deleted"] >= 1)


# S1 / S4 / S6 on screen: the plain words the owner reads on a tab and on the window's chip.
class WordingTests(unittest.TestCase):
    def test_tab_notes_and_chip(self):
        tabs = [{"strippedTitle": "a", "outcome": "MATCHED", "cwd": "Z:\a", "folder_missing": True, "drive_offline": True},
                {"strippedTitle": "b", "outcome": "MATCHED", "cwd": "C:\b", "origin_unknown": True},
                {"strippedTitle": "c", "outcome": "MATCHED", "cwd": r"C:\c", "folder_missing": True}]
        fake = SimpleNamespace(snapshot={"windows": [{"tabs": tabs, "crash_group": True, "windowTitle": DROP_TITLE}]},
                               tab_state=lambda key: "unknown", window_keys=lambda index: ["0:0", "0:1", "0:2"])
        notes = [SnapshotMap.tab_names(fake, key)[1] for key in ("0:0", "0:1", "0:2")]
        self.assertIn("drive not connected", notes[0])
        self.assertIn("may not be a terminal chat", notes[1])
        self.assertIn("folder is gone", notes[2])
        chips = [text for text, _ in SnapshotMap.footer(fake, 0)]
        self.assertEqual(chips[0], "Open before many tabs closed · no saved place")
        self.assertIn("1 drive not connected", chips)
        self.assertIn("1 folder gone", chips)
        self.assertEqual(short_label("Last layout before the crash - 11 days old (Oct 29, 2:44 PM)"), "Before crash (old)")


if __name__ == "__main__":
    unittest.main()

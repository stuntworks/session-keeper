# Last modified: 2026-10-21 | Change: Fake Codex processes answer as_dict like psutil | Full history: git log
"""Recovery regression tests for failures found during completion."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keeper_codex import active_codex_sessions
from keeper_history import read_history, startup_recovery
from keeper_inventory import claude_record, match_inventory
from keeper_locks import exclusive_operation

FIRST_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SECOND_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# WHAT IT DOES: Exercises the exact identity and history cases that could reopen the wrong chat.
# DEPENDS ON: Temporary files, fake process records, and Windows mutexes.
# AFFECTS: Guards recovery across chat switches, repeated titles, and watcher restarts.
class RecoveryTests(unittest.TestCase):
    # WHAT IT DOES: Makes a test-only rollout with the supplied source type.
    # DEPENDS ON: Temporary directory.
    # AFFECTS: Provides a main or subagent record to the open-file matcher.
    def rollout(self, folder, session_id, source):
        path = Path(folder) / f"rollout-2026-10-03T01-00-00-{session_id}.jsonl"
        path.write_text(json.dumps({"type": "session_meta", "payload":
            {"id": session_id, "source": source}}), encoding="utf-8")
        return path

    # WHAT IT DOES: Uses the open main conversation even when launch arguments name an older conversation.
    # DEPENDS ON: Fake Codex process with an old resume ID and new open rollout.
    # AFFECTS: Switching chats in a CLI does not resume the original chat after a crash.
    def test_open_rollout_wins_over_old_resume_argument(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.rollout(folder, SECOND_ID, "cli")
            process = SimpleNamespace(pid=12, info={"name": "codex.exe",
                "cmdline": ["codex.exe", "resume", FIRST_ID], "cwd": folder},
                open_files=lambda: [SimpleNamespace(path=str(path))])
            process.as_dict = lambda attrs: {name: process.info.get(name) for name in attrs}
            with patch("keeper_codex.psutil.process_iter", return_value=[process]), \
                 patch("keeper_codex.console_title", return_value="Work"):
                result = active_codex_sessions()
            self.assertEqual(result[0]["sessionId"], SECOND_ID)
            self.assertEqual(result[0]["method"], "open-main-rollout")

    # WHAT IT DOES: Refuses to choose between two open main conversation files.
    # DEPENDS ON: Fake Codex process holding two CLI rollouts.
    # AFFECTS: Ambiguous identity is visible rather than silently guessed.
    def test_multiple_main_rollouts_are_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [self.rollout(folder, item, "cli") for item in (FIRST_ID, SECOND_ID)]
            process = SimpleNamespace(pid=12, info={"name": "codex.exe", "cmdline": ["codex.exe"]},
                open_files=lambda: [SimpleNamespace(path=str(path)) for path in paths])
            process.as_dict = lambda attrs: {name: process.info.get(name) for name in attrs}
            with patch("keeper_codex.psutil.process_iter", return_value=[process]), \
                 patch("keeper_codex.console_title", return_value="Work"):
                self.assertIsNone(active_codex_sessions()[0]["sessionId"])

    # WHAT IT DOES: Rejects a PID record created before the current process lifetime.
    # DEPENDS ON: A fake process start after the record's modification time.
    # AFFECTS: Windows PID reuse cannot attach an old Claude ID to a new process.
    def test_stale_pid_record_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "12.json"
            path.write_text(json.dumps({"pid": 12, "sessionId": FIRST_ID}), encoding="utf-8")
            process = SimpleNamespace(pid=12, create_time=lambda: path.stat().st_mtime + 10)
            self.assertIsNone(claude_record(process, Path(folder)))

    # WHAT IT DOES: Keeps identically titled tabs unassigned even with only one matching process.
    # DEPENDS ON: Two visible tabs and one known console title.
    # AFFECTS: Prevents a false unique match across duplicate tab labels.
    def test_duplicate_tabs_are_not_assigned_twice(self):
        windows = [{"tabs": ["Work", "Work"]}]
        sessions = [{"title": "Work", "sessionId": FIRST_ID, "kind": "claude"}]
        report, unmatched, counts = match_inventory(windows, sessions)
        self.assertEqual(counts["matched"], 0)
        self.assertEqual(counts["ambiguous"], 2)
        self.assertEqual(len(unmatched), 1)

    # WHAT IT DOES: Keeps both layouts in history while avoiding stale recovery on same-boot watcher restart.
    # DEPENDS ON: Two temporary snapshots from different boots.
    # AFFECTS: The manual history selector still reaches older larger layouts.
    def test_current_boot_does_not_auto_restore_an_older_boot(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for name, boot, stamp in (("old", "old", "2026-10-02T00:00:00+00:00"),
                                      ("new", "current", "2026-10-03T00:00:00+00:00")):
                (directory / f"snapshot_{name}.json").write_text(json.dumps({"version": 1,
                    "boot_id": boot, "captured_at": stamp, "windows": []}), encoding="utf-8")
            self.assertEqual(len(read_history(directory)), 2)
            self.assertIsNone(startup_recovery("current", directory))
            self.assertEqual(startup_recovery("next", directory)[1]["boot_id"], "current")

    # WHAT IT DOES: Blocks a second operation until the first has released its Windows lock.
    # DEPENDS ON: A unique test mutex name.
    # AFFECTS: Separate UI windows cannot start duplicate restores concurrently.
    def test_operation_lock_releases(self):
        with exclusive_operation("unittest"):
            with self.assertRaises(RuntimeError):
                with exclusive_operation("unittest"):
                    self.fail("Second operation should be excluded")
        with exclusive_operation("unittest"):
            pass


if __name__ == "__main__":
    unittest.main()

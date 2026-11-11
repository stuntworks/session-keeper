# Last modified: 2026-10-22 | Change: Tests for the All sessions listing (discovery, peek, sort, filter, row text) | Full history: git log
"""Fake folders only: every transcript is a temp file written by the test; real ~/.claude and ~/.codex are never read."""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

import keeper_sessions as sessions

CLAUDE_A = "11111111-2222-3333-4444-555555555555"
CLAUDE_B = "22222222-2222-3333-4444-555555555555"
CODEX_A = "019aaaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"


def lines(*entries):
    return "\n".join(json.dumps(entry, separators=(",", ":")) for entry in entries) + "\n"   # compact, as on disk


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="keeper_sessions_test_"))
        self.claude = self.dir / "projects"
        self.codex = self.dir / "sessions"
        self.index = self.dir / "session_index.jsonl"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, path, text, age_minutes=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        moment = time.time() - age_minutes * 60
        os.utime(path, (moment, moment))
        return path

    def claude_file(self, session_id, text, folder="C--Users-example-ProjectA", age=0):
        return self.write(self.claude / folder / f"{session_id}.jsonl", text, age)

    def codex_file(self, session_id, text, age=0):
        return self.write(self.codex / "2026" / "01" / "02" / f"rollout-2026-01-02T10-00-00-{session_id}.jsonl",
                          text, age)

    def listing(self):
        return sessions.list_transcripts(self.claude, self.codex)


# WHAT IT DOES: Lists transcripts from file metadata only, newest first.
class DiscoveryTests(Fixture):
    def test_lists_claude_and_codex_newest_first(self):
        self.claude_file(CLAUDE_A, "{}\n", age=30)
        self.claude_file(CLAUDE_B, "{}\n", folder="D--Other", age=5)
        self.codex_file(CODEX_A, "{}\n", age=10)
        found = self.listing()
        self.assertEqual([(entry["kind"], entry["session_id"]) for entry in found],
                         [("claude", CLAUDE_B), ("codex", CODEX_A), ("claude", CLAUDE_A)])
        self.assertEqual(found[0]["folder"], "D--Other")
        self.assertEqual(found[0]["size"], 3)
        self.assertIsInstance(found[0]["modified"], datetime)

    def test_ignores_subagents_and_odd_names(self):
        self.claude_file(CLAUDE_A, "{}\n")
        self.write(self.claude / "C--x" / CLAUDE_A / "subagents" / "agent-1.jsonl", "{}\n")
        self.write(self.claude / "C--x" / "notes.jsonl", "{}\n")
        self.write(self.codex / "2026" / "rollout-without-id.jsonl", "{}\n")
        self.assertEqual([entry["session_id"] for entry in self.listing()], [CLAUDE_A])

    def test_missing_folders_give_an_empty_list(self):
        self.assertEqual(sessions.list_transcripts(self.dir / "none", self.dir / "nope"), [])


# WHAT IT DOES: A cheap look at the start and end of a file for its name and folder; never the whole file.
class PeekTests(Fixture):
    def test_claude_title_and_cwd(self):
        body = lines({"type": "custom-title", "customTitle": "Old name", "sessionId": CLAUDE_A},
                     {"type": "user", "cwd": "C:\\Users\\example\\ProjectA", "message": {"content": "hi"}})
        filler = lines(*({"type": "assistant", "n": n, "pad": "x" * 200} for n in range(400)))
        tail = lines({"type": "custom-title", "customTitle": "New name", "sessionId": CLAUDE_A})
        path = self.claude_file(CLAUDE_A, body + filler + tail)
        entry = self.listing()[0]
        self.assertGreater(path.stat().st_size, 3 * sessions.PEEK_BYTES)
        self.assertEqual(sessions.peek(entry, {}), ("New name", "C:\\Users\\example\\ProjectA"))

    def test_claude_without_title(self):
        self.claude_file(CLAUDE_A, lines({"type": "user", "cwd": "D:\\Work", "message": {"content": "hi"}}))
        self.assertEqual(sessions.peek(self.listing()[0], {}), (None, "D:\\Work"))

    def test_codex_title_from_index_and_cwd_from_meta(self):
        self.codex_file(CODEX_A, lines({"type": "session_meta", "payload": {"id": CODEX_A, "cwd": "D:\\Proj"}}))
        self.index.write_bytes(lines({"id": CODEX_A, "thread_name": "first"}, {"id": "other", "thread_name": "x"},
                                    {"id": CODEX_A, "thread_name": "Codex helper"}).encode("utf-8"))
        titles = sessions.codex_titles(self.index)
        self.assertEqual(sessions.peek(self.listing()[0], titles), ("Codex helper", "D:\\Proj"))

    def test_missing_index_is_empty(self):
        self.assertEqual(sessions.codex_titles(self.dir / "absent.jsonl"), {})


def entry(kind="claude", title=None, cwd=None, folder="C--Users-example", modified=datetime(2026, 10, 21, 9, 5)):
    return {"kind": kind, "session_id": CLAUDE_A, "title": title, "cwd": cwd, "folder": folder,
            "modified": modified, "size": 2048, "path": "x"}


# WHAT IT DOES: The search box and the All / Claude / Codex filter.
class FilterTests(unittest.TestCase):
    def test_kind_filter(self):
        rows = [entry("claude"), entry("codex")]
        self.assertEqual(len(sessions.filter_entries(rows, "", "all")), 2)
        self.assertEqual([row["kind"] for row in sessions.filter_entries(rows, "", "codex")], ["codex"])

    def test_search_name_folder_and_date(self):
        rows = [entry(title="VozSay website"), entry(cwd="D:\\3 SOFTWARE\\Session-Keeper"),
                entry(modified=datetime(2026, 9, 3, 8, 0))]
        self.assertEqual(sessions.filter_entries(rows, "vozsay", "all"), [rows[0]])
        self.assertEqual(sessions.filter_entries(rows, "session-keeper", "all"), [rows[1]])
        self.assertEqual(sessions.filter_entries(rows, "2026-09-03", "all"), [rows[2]])
        self.assertEqual(sessions.filter_entries(rows, "sep 3", "all"), [rows[2]])
        self.assertEqual(sessions.filter_entries(rows, "vozsay website", "all"), [rows[0]])
        self.assertEqual(sessions.filter_entries(rows, "nothing like this", "all"), [])


# WHAT IT DOES: The words shown on one row, before and after its full facts arrive.
class RowTextTests(unittest.TestCase):
    def test_before_facts(self):
        text = sessions.row_text(entry(title=None, cwd=None), None)
        self.assertEqual(text["title"], "Untitled session 11111111")
        self.assertEqual(text["folder"], "C--Users-example")
        self.assertEqual(text["last_active"], "2026-10-21 09:05")
        self.assertEqual(text["size"], "2 KB")
        self.assertEqual(text["work"], "Reading…")

    def test_with_facts(self):
        facts = {"available": True, "title": "Fixer", "started": datetime(2026, 10, 20, 8, 0),
                 "last_active": datetime(2026, 10, 21, 9, 30), "active_span": 3600 * 25 + 120, "messages": 12,
                 "transcript_bytes": 2048, "file_changes": 142, "files_changed": 37, "commands": 380}
        text = sessions.row_text(entry(cwd="D:\\Work"), facts)
        self.assertEqual(text["title"], "Fixer")
        self.assertEqual(text["folder"], "D:\\Work")
        self.assertEqual(text["last_active"], "2026-10-21 09:30")
        self.assertEqual(text["started"], "2026-10-20 08:00")
        self.assertEqual(text["active_for"], "1 day 1 h")
        self.assertEqual(text["size"], "12 messages · 2 KB")
        self.assertEqual(text["work"], "Changed files 142 times (37 files) · ran 380 commands")

    def test_talk_only(self):
        facts = {"available": True, "messages": 2, "transcript_bytes": 10, "file_changes": 0, "files_changed": 0,
                 "commands": 0}
        self.assertEqual(sessions.row_text(entry(), facts)["work"], "talk only")

    def test_unreadable(self):
        text = sessions.row_text(entry(), {"available": False, "reason": "Transcript can't be read"})
        self.assertEqual(text["work"], "Transcript can't be read")


def stamped(minute, **extra):
    return {"type": "user", "timestamp": f"2026-10-20T10:{minute:02d}:00.000Z", **extra}


# WHAT IT DOES: The transcript's own first and last timestamps, read from the head and tail only.
class TimeTests(Fixture):
    def test_big_file_first_and_last_whole_line(self):
        body = lines({"type": "custom-title", "customTitle": "T"}, stamped(1))
        filler = lines(*({"type": "assistant", "pad": "x" * 200, "timestamp": "2026-10-20T10:30:00.000Z"}
                         for _ in range(400)))
        tail = lines(stamped(55)) + '{"type":"user","timestamp":"2026-10-20T11:59:00.000Z","cut'   # half-written line
        self.claude_file(CLAUDE_A, body + filler + tail)
        _title, _cwd, started, last = sessions.peek_more(self.listing()[0], {})
        self.assertEqual(started, datetime.fromisoformat("2026-10-20T10:01:00+00:00").astimezone().replace(tzinfo=None))
        self.assertEqual(last, datetime.fromisoformat("2026-10-20T10:55:00+00:00").astimezone().replace(tzinfo=None))

    def test_small_file_and_no_timestamps(self):
        self.claude_file(CLAUDE_A, lines(stamped(2), stamped(9)))
        self.claude_file(CLAUDE_B, lines({"type": "custom-title", "customTitle": "x"}))
        found = {row["session_id"]: sessions.peek_more(row, {}) for row in self.listing()}
        self.assertLess(found[CLAUDE_A][2], found[CLAUDE_A][3])
        self.assertEqual(found[CLAUDE_B][2:], (None, None))
        self.assertEqual(sessions.peek(self.listing()[0], {}), found[self.listing()[0]["session_id"]][:2])

    def test_listing_has_creation_time(self):
        self.claude_file(CLAUDE_A, "{}\n")
        self.assertIsInstance(self.listing()[0]["created"], datetime)


def row(name, active=None, started=None, cwd=None, modified=datetime(2026, 10, 1), created=datetime(2026, 9, 1)):
    return {"kind": "claude", "session_id": name, "title": name, "cwd": cwd, "folder": "", "modified": modified,
            "created": created, "last_active": active, "started": started, "size": 1, "path": name}


# WHAT IT DOES: The three orders: last active, started, project.
class SortTests(unittest.TestCase):
    def names(self, rows, mode):
        return [item["session_id"] for item in sessions.sort_entries(rows, mode)]

    def test_last_active_uses_transcript_time_not_file_time(self):
        rows = [row("old-file-new-talk", active=datetime(2026, 10, 9), modified=datetime(2026, 9, 1)),
                row("new-file-old-talk", active=datetime(2026, 10, 2), modified=datetime(2026, 10, 9)),
                row("unknown", modified=datetime(2026, 10, 5))]
        self.assertEqual(self.names(rows, "active"), ["old-file-new-talk", "unknown", "new-file-old-talk"])

    def test_started_falls_back_to_creation_time(self):
        rows = [row("a", started=datetime(2026, 10, 1)), row("b", started=datetime(2026, 10, 7)),
                row("c", created=datetime(2026, 10, 4))]
        self.assertEqual(self.names(rows, "started"), ["b", "c", "a"])

    def test_project_groups_folders_a_to_z_newest_first_inside(self):
        rows = [row("z1", active=datetime(2026, 10, 3), cwd=r"D:\Zeta"), row("a1", active=datetime(2026, 10, 1), cwd=r"D:\alpha"),
                row("z2", active=datetime(2026, 10, 8), cwd=r"D:\Zeta"), row("a2", active=datetime(2026, 10, 5), cwd=r"D:\Alpha"),
                row("none", active=datetime(2026, 10, 9))]
        self.assertEqual(self.names(rows, "project"), ["a2", "a1", "z2", "z1", "none"])

    def test_search_date_uses_last_active(self):
        rows = [row("x", active=datetime(2026, 8, 3, 8, 0), modified=datetime(2026, 10, 1))]
        self.assertEqual(sessions.filter_entries(rows, "2026-08-03", "all"), rows)
        self.assertEqual(sessions.filter_entries(rows, "2026-10-01", "all"), [])
        self.assertEqual(sessions.row_text(rows[0], None)["last_active"], "2026-08-03 08:00")


if __name__ == "__main__":
    unittest.main()

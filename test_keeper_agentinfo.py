# Last modified: 2026-10-21 | Change: Tests for transcript facts (last message, times, size, work counts) | Full history: git log
"""Fake data only. Every transcript here is a small temp file written by the test; real ~/.claude and ~/.codex are never read."""

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

import keeper_agentinfo as info

CLAUDE_ID = "11111111-2222-3333-4444-555555555555"
CODEX_ID = "019aaaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
CWD = "C:\\Users\\example\\ProjectA"


def ts(minute, day=1):
    return f"2026-01-{day:02d}T10:{minute:02d}:00.000Z"


def local(minute, day=1):
    return datetime(2026, 1, day, 10, minute, tzinfo=timezone.utc).astimezone()


def c_user(text, minute, **extra):
    entry = {"parentUuid": None, "isSidechain": False, "type": "user",
             "message": {"role": "user", "content": text}, "uuid": f"u{minute}", "timestamp": ts(minute),
             "cwd": CWD, "sessionId": CLAUDE_ID}
    entry.update(extra)
    return entry


def c_result(minute):
    return {"parentUuid": "x", "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": [{"tool_use_id": "t", "type": "tool_result", "content": "ok"}]},
            "uuid": f"r{minute}", "timestamp": ts(minute), "toolUseResult": {"stdout": "ok"}, "sessionId": CLAUDE_ID}


def c_asst(msg_id, block, minute, **extra):
    entry = {"parentUuid": "x", "isSidechain": False,
             "message": {"model": "claude-fake", "id": msg_id, "type": "message", "role": "assistant",
                         "content": [block]},
             "type": "assistant", "uuid": f"a{msg_id}{minute}", "timestamp": ts(minute), "sessionId": CLAUDE_ID}
    entry.update(extra)
    return entry


def c_text(msg_id, text, minute, **extra):
    return c_asst(msg_id, {"type": "text", "text": text}, minute, **extra)


def c_tool(msg_id, tool_id, name, tool_input, minute):
    return c_asst(msg_id, {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input,
                           "caller": {"type": "direct"}}, minute)


def claude_lines():
    return [
        {"type": "last-prompt", "leafUuid": "x", "sessionId": CLAUDE_ID},
        c_user("please build the thing", 0),
        c_user("<command-name>/rename</command-name>", 1),
        c_user("hook context", 1, isMeta=True),
        c_user("summary of earlier", 1, isCompactSummary=True),
        c_text("msg_1", "First paragraph.\n\nOn it.", 2),
        c_tool("msg_1", "toolu_1", "Edit", {"file_path": "C:\\fake\\a.py", "old_string": "x", "new_string": "y"}, 3),
        c_result(3),
        c_tool("msg_1", "toolu_2", "Write", {"file_path": "C:\\fake\\b.py", "content": "z"}, 4),
        c_tool("msg_1", "toolu_3", "Edit", {"file_path": "C:\\fake\\a.py", "old_string": "y", "new_string": "w"}, 4),
        c_tool("msg_1", "toolu_4", "Bash", {"command": "echo hi"}, 5),
        c_tool("msg_1", "toolu_5", "PowerShell", {"command": "dir"}, 5),
        c_tool("msg_1", "toolu_6", "Read", {"file_path": "C:\\fake\\c.py"}, 5),
        c_tool("msg_1", "toolu_7", "MultiEdit", {"file_path": "C:\\fake\\c.py", "edits": []}, 6),
        c_tool("msg_1", "toolu_8", "NotebookEdit", {"notebook_path": "C:\\fake\\n.ipynb"}, 6),
        {"type": "custom-title", "customTitle": "fake-engineer", "sessionId": CLAUDE_ID},
        c_user("thanks, finish up", 7),
        c_text("msg_2", "Done with step one.\n\nThe build passed and the " + "word " * 150 + "end.", 8),
        c_text("msg_3", "No response requested.", 8, isApiErrorMessage=True),
        c_tool("msg_2", "toolu_9", "Bash", {"command": "git status"}, 9),
        c_result(9),
        {"type": "system", "subtype": "stop_hook_summary", "timestamp": ts(10), "sessionId": CLAUDE_ID},
    ]


def x_entry(kind, payload, minute, day=1):
    return {"timestamp": ts(minute, day), "ordinal": minute, "type": kind, "payload": payload}


def x_msg(role, text, minute, phase=None, day=1):
    payload = {"type": "message", "id": f"m{minute}", "role": role,
               "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}
    if phase:
        payload["phase"] = phase
    return x_entry("response_item", payload, minute, day)


def x_call(kind, name, body, minute):
    key = "input" if kind == "custom_tool_call" else "arguments"
    return x_entry("response_item", {"type": kind, "id": f"c{minute}", "status": "completed",
                                     "call_id": f"call{minute}", "name": name, key: body}, minute)


PATCH = "*** Begin Patch\n*** Update File: C:\\fake\\a.py\n@@\n-x\n+y\n*** Add File: C:\\fake\\b.py\n+z\n*** End Patch\n"
JS_PATCH = ('const r = await tools.apply_patch("*** Begin Patch\\n*** Update File: C:\\\\fake\\\\c.py\\n@@\\n-x\\n+y'
            '\\n*** End Patch\\n");\nawait tools.exec_command({cmd: "git diff"});\nawait tools.exec_command({cmd: "ls"});')


def codex_lines():
    return [
        x_entry("session_meta", {"id": CODEX_ID, "cwd": CWD, "source": "cli", "timestamp": ts(0)}, 0),
        x_msg("developer", "<permissions>rules</permissions>", 0),
        x_msg("user", "<environment_context>fake</environment_context>", 0),
        x_msg("user", "# AGENTS.md instructions for C:\\fake", 0),
        x_msg("user", "fix the fake bug", 1),
        x_msg("assistant", "Looking now.", 2, phase="commentary"),
        x_call("custom_tool_call", "apply_patch", PATCH, 3),
        x_entry("response_item", {"type": "custom_tool_call_output", "call_id": "call3", "output": "ok"}, 3),
        x_call("function_call", "shell_command", json.dumps({"command": "git status", "workdir": CWD}), 4),
        x_call("custom_tool_call", "exec", JS_PATCH, 5),
        x_call("function_call", "update_plan", json.dumps({"plan": []}), 5),
        x_msg("assistant", "Patched it.\n\nAll fake tests pass now.", 6, phase="final_answer"),
        x_call("function_call", "shell_command", json.dumps({"command": "git log"}), 7),
        x_entry("event_msg", {"type": "token_count", "info": None}, 8),
    ]


class Fixture(unittest.TestCase):
    def setUp(self):
        info.clear_cache()
        self.dir = Path(tempfile.mkdtemp())
        self.claude_root = self.dir / "claude_projects"
        self.codex_root = self.dir / "codex_sessions"
        self.codex_index = self.dir / "session_index.jsonl"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, path, entries, extra_raw=()):
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(entry, separators=(",", ":"), ensure_ascii=False) for entry in entries]
        for position, raw in extra_raw:
            lines.insert(position, raw)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def claude_file(self, entries, folder="C--Users-example-ProjectA", **kw):
        return self.write(self.claude_root / folder / f"{CLAUDE_ID}.jsonl", entries, **kw)

    def codex_file(self, entries, **kw):
        return self.write(self.codex_root / "2026" / "01" / "01" / f"rollout-2026-01-01T10-00-00-{CODEX_ID}.jsonl",
                          entries, **kw)

    def facts(self, kind="claude", session_id=None, cwd=CWD):
        row = {"session_id": session_id or (CLAUDE_ID if kind == "claude" else CODEX_ID), "kind": kind, "cwd": cwd}
        return info.agent_facts(row, claude_root=self.claude_root, codex_root=self.codex_root,
                                codex_index=self.codex_index)


class ClaudeFactsTests(Fixture):
    def test_counts_work_messages_and_times(self):
        path = self.claude_file(claude_lines())
        facts = self.facts()
        self.assertTrue(facts["available"])
        self.assertEqual(facts["path"], str(path))
        self.assertEqual(facts["file_changes"], 5)  # 2 Edit + Write + MultiEdit + NotebookEdit; Read is not a change
        self.assertEqual(facts["files_changed"], 4)  # a.py, b.py, c.py, n.ipynb
        self.assertEqual(facts["commands"], 3)  # 2 Bash + 1 PowerShell
        self.assertEqual(facts["user_prompts"], 2)  # command echo, meta and compact summary are not prompts
        self.assertEqual(facts["assistant_replies"], 2)  # msg_1, msg_2; the API error line does not count
        self.assertEqual(facts["messages"], 4)
        self.assertEqual(facts["started"], local(0))
        self.assertEqual(facts["last_active"], local(10))
        self.assertEqual(facts["active_span"], 600)
        self.assertEqual(facts["transcript_bytes"], path.stat().st_size)
        self.assertEqual(facts["title"], "fake-engineer")
        self.assertEqual(facts["skipped_lines"], 0)

    def test_last_text_is_final_paragraph_trimmed_at_word(self):
        self.claude_file(claude_lines())
        facts = self.facts()
        self.assertTrue(facts["last_text"].startswith("The build passed and the word"))
        self.assertLessEqual(len(facts["last_text"]), 401)
        self.assertTrue(facts["last_text"].endswith("…"))
        self.assertFalse(facts["last_text"][:-1].endswith(" "))
        self.assertTrue(facts["last_text"][:-1].endswith("word"))
        self.assertEqual(facts["last_text_time"], local(8))

    def test_tool_only_final_turn_still_finds_earlier_text(self):
        entries = claude_lines()[:6] + [c_tool("msg_9", "toolu_x", "Bash", {"command": "ls"}, 30), c_result(31)]
        self.claude_file(entries)
        facts = self.facts()
        self.assertEqual(facts["last_text"], "On it.")
        self.assertEqual(facts["last_text_time"], local(2))
        self.assertEqual(facts["last_active"], local(31))

    def test_last_text_survives_long_tool_tail(self):
        filler = [c_result(20 + n % 30) for n in range(400)]
        self.claude_file(claude_lines()[:6] + filler)
        self.assertEqual(self.facts()["last_text"], "On it.")

    def test_corrupt_line_is_skipped_and_counted(self):
        self.claude_file(claude_lines(), extra_raw=[(3, '{"type":"assistant","message":{broken'), (5, "not json")])
        facts = self.facts()
        self.assertTrue(facts["available"])
        self.assertEqual(facts["skipped_lines"], 2)
        self.assertEqual(facts["file_changes"], 5)

    def test_wrong_folder_encoding_falls_back_to_glob(self):
        path = self.claude_file(claude_lines(), folder="some-other-folder")
        facts = self.facts(cwd="D:\\not\\the\\folder")
        self.assertTrue(facts["available"])
        self.assertEqual(facts["path"], str(path))

    def test_title_falls_back_to_agent_name_then_ai_title(self):
        entries = [e for e in claude_lines() if e.get("type") != "custom-title"]
        entries.append({"type": "ai-title", "aiTitle": "fake ai title", "sessionId": CLAUDE_ID})
        self.claude_file(entries)
        self.assertEqual(self.facts()["title"], "fake ai title")
        info.clear_cache()
        entries.append({"type": "agent-name", "agentName": "fake-agent", "sessionId": CLAUDE_ID})
        self.claude_file(entries)
        self.assertEqual(self.facts()["title"], "fake-agent")

    def test_talk_only_session(self):
        self.claude_file([c_user("hello", 0), c_text("msg_1", "Hi there.", 1)])
        facts = self.facts()
        self.assertEqual((facts["file_changes"], facts["files_changed"], facts["commands"]), (0, 0, 0))
        self.assertIsNone(facts["title"])

    def test_cache_hits_until_file_changes(self):
        path = self.claude_file(claude_lines())
        first = self.facts()
        self.assertIs(self.facts(), first)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(c_tool("msg_5", "toolu_z", "Write", {"file_path": "C:\\fake\\z.py"}, 40),
                                    separators=(",", ":")) + "\n")
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(self.facts()["file_changes"], 6)

    def test_never_writes_transcript(self):
        path = self.claude_file(claude_lines())
        before = (path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes())
        self.facts()
        self.assertEqual((path.stat().st_size, path.stat().st_mtime_ns, path.read_bytes()), before)


class CodexFactsTests(Fixture):
    def test_counts_patches_commands_and_messages(self):
        path = self.codex_file(codex_lines())
        facts = self.facts("codex")
        self.assertTrue(facts["available"])
        self.assertEqual(facts["path"], str(path))
        self.assertEqual(facts["file_changes"], 2)  # one apply_patch call + one patch inside exec
        self.assertEqual(facts["files_changed"], 3)  # a.py, b.py, c.py
        self.assertEqual(facts["commands"], 4)  # 2 shell_command + 2 exec_command inside exec
        self.assertEqual(facts["user_prompts"], 1)  # environment/AGENTS.md injections are not prompts
        self.assertEqual(facts["assistant_replies"], 2)
        self.assertEqual(facts["started"], local(0))
        self.assertEqual(facts["last_active"], local(8))
        self.assertEqual(facts["last_text"], "All fake tests pass now.")
        self.assertEqual(facts["last_text_time"], local(6))

    def test_title_from_codex_session_index(self):
        self.codex_file(codex_lines())
        self.codex_index.write_text(
            json.dumps({"id": CODEX_ID, "thread_name": "old name", "updated_at": ts(0)}) + "\n"
            + json.dumps({"id": "019fffff-0000-7000-8000-000000000000", "thread_name": "other", "updated_at": ts(1)})
            + "\n" + json.dumps({"id": CODEX_ID, "thread_name": "fake codex name", "updated_at": ts(2)}) + "\n",
            encoding="utf-8")
        self.assertEqual(self.facts("codex")["title"], "fake codex name")

    def test_tool_only_final_turn_and_corrupt_line(self):
        entries = codex_lines()[:6] + [x_call("function_call", "shell_command", "{}", 20)]
        self.codex_file(entries, extra_raw=[(2, "{garbage")])
        facts = self.facts("codex")
        self.assertEqual(facts["last_text"], "Looking now.")
        self.assertEqual(facts["skipped_lines"], 1)
        self.assertEqual(facts["commands"], 1)


class MissingTests(Fixture):
    def test_missing_claude_file(self):
        facts = self.facts()
        self.assertFalse(facts["available"])
        self.assertIn("reason", facts)

    def test_missing_codex_file(self):
        facts = self.facts("codex")
        self.assertFalse(facts["available"])
        self.assertTrue(facts["reason"])

    def test_bad_row(self):
        self.assertFalse(info.agent_facts({"kind": "claude"})["available"])
        self.assertFalse(self.facts(kind="mystery")["available"])
        self.assertFalse(self.facts(session_id="../../etc")["available"])


class FormatTests(unittest.TestCase):
    def base(self, **over):
        facts = {"available": True, "title": None, "last_text": "All good.", "last_text_time": local(8),
                 "started": local(0), "last_active": local(10, day=4),
                 "active_span": 3 * 86400 + 4 * 3600 + 120, "messages": 412, "user_prompts": 200,
                 "assistant_replies": 212, "transcript_bytes": 38 * 1024 * 1024,
                 "file_changes": 142, "files_changed": 37, "commands": 380, "skipped_lines": 0}
        facts.update(over)
        return dict(info.format_facts(facts))

    def test_engineer_lines(self):
        rows = self.base()
        self.assertEqual(rows["LAST MESSAGE"], "All good.")
        self.assertEqual(rows["WHEN"], local(8).strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(rows["STARTED"], local(0).strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(rows["ACTIVE FOR"], "3 days 4 h")
        self.assertEqual(rows["SIZE"], "412 messages · 38 MB")
        self.assertEqual(rows["WORK"], "Changed files 142 times (37 files) · ran 380 commands")
        self.assertNotIn("NAME", rows)
        self.assertNotIn("SKIPPED", rows)

    def test_talk_only_and_small_values(self):
        rows = self.base(file_changes=0, files_changed=0, commands=0, active_span=50 * 60, messages=1,
                         transcript_bytes=2048, title="fake", skipped_lines=2)
        self.assertEqual(rows["WORK"], "Changed no files · talk only")
        self.assertEqual(rows["ACTIVE FOR"], "50 min")
        self.assertEqual(rows["SIZE"], "1 message · 2 KB")
        self.assertEqual(rows["NAME"], "fake")
        self.assertEqual(rows["SKIPPED"], "2 damaged lines not read")

    def test_mixed_work_and_spans(self):
        self.assertEqual(self.base(file_changes=1, files_changed=1, commands=1)["WORK"],
                         "Changed files 1 time (1 file) · ran 1 command")
        self.assertEqual(self.base(file_changes=0, files_changed=0, commands=5)["WORK"],
                         "Changed no files · ran 5 commands")
        self.assertEqual(self.base(active_span=86400 + 30)["ACTIVE FOR"], "1 day 0 h")
        self.assertEqual(self.base(active_span=2 * 3600 + 5 * 60)["ACTIVE FOR"], "2 h 5 min")
        self.assertEqual(self.base(active_span=20)["ACTIVE FOR"], "under 1 min")

    def test_no_text_and_unavailable(self):
        rows = self.base(last_text=None, last_text_time=None)
        self.assertEqual(rows["LAST MESSAGE"], "No reply text in the transcript")
        self.assertNotIn("WHEN", rows)
        self.assertEqual(info.format_facts({"available": False, "reason": "Transcript not found"}),
                         [("TRANSCRIPT", "Transcript not found")])


if __name__ == "__main__":
    unittest.main()

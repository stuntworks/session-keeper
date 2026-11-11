# Last modified: 2026-10-22 | Change: Tests for "What it worked on": recent replies and changed files | Full history: git log
"""Fake data only: small temp transcripts written by the tests; real ~/.claude and ~/.codex are never read."""

import unittest

import keeper_agentinfo as info
from test_keeper_agentinfo import (CLAUDE_ID, CODEX_ID, Fixture, c_text, c_tool, c_user, codex_lines, local,
                                   x_call, x_msg)


# WHAT IT DOES: The review panel's last replies (final paragraph, newest first, with times) and changed files.
# DEPENDS ON: keeper_agentinfo.agent_facts on fake transcripts.
# AFFECTS: Guards the "What it worked on" section.
class ClaudeWorkTests(Fixture):
    def test_last_five_replies_newest_first_final_paragraph(self):
        lines = [c_user("go", 0)]
        for number in range(1, 8):
            lines.append(c_text(f"m{number}", f"Thinking out loud.\n\nReply number {number}.", number))
        self.claude_file(lines)
        replies = self.facts()["recent_replies"]
        self.assertEqual([text for _, text in replies],
                         [f"Reply number {number}." for number in (7, 6, 5, 4, 3)])
        self.assertEqual(replies[0][0], local(7))

    def test_one_message_split_over_lines_is_one_reply(self):
        self.claude_file([c_user("go", 0), c_text("m1", "First part.", 1), c_text("m1", "Second part.", 2)])
        self.assertEqual([text for _, text in self.facts()["recent_replies"]], ["Second part."])

    def test_changed_files_most_recent_first_without_repeats(self):
        lines = [c_user("go", 0),
                 c_tool("m1", "t1", "Edit", {"file_path": "C:\\Work\\A.py"}, 1),
                 c_tool("m2", "t2", "Write", {"file_path": "C:\\Work\\B.py"}, 2),
                 c_tool("m3", "t3", "Bash", {"command": "ls"}, 3),
                 c_tool("m4", "t4", "Edit", {"file_path": "C:\\Work\\A.py"}, 4)]
        self.claude_file(lines)
        self.assertEqual(self.facts()["recent_files"], ["C:\\Work\\A.py", "C:\\Work\\B.py"])

    def test_changed_files_are_capped(self):
        lines = [c_user("go", 0)] + [c_tool(f"m{n}", f"t{n}", "Write", {"file_path": f"C:\\Work\\f{n}.py"}, n % 60)
                                     for n in range(1, info.RECENT_FILES_LIMIT + 10)]
        self.claude_file(lines)
        files = self.facts()["recent_files"]
        self.assertEqual(len(files), info.RECENT_FILES_LIMIT)
        self.assertEqual(files[0], f"C:\\Work\\f{info.RECENT_FILES_LIMIT + 9}.py")

    def test_talk_only_session_has_no_files(self):
        self.claude_file([c_user("go", 0), c_text("m1", "Just advice.", 1)])
        self.assertEqual(self.facts()["recent_files"], [])

    def test_known_path_skips_the_search(self):
        path = self.claude_file([c_user("go", 0), c_text("m1", "Done.", 1)], folder="odd-folder")
        facts = info.agent_facts({"kind": "claude", "session_id": CLAUDE_ID, "cwd": "D:\\elsewhere", "path": path},
                                 claude_root=self.dir / "nowhere")
        self.assertTrue(facts["available"])
        self.assertEqual(facts["recent_replies"][0][1], "Done.")


class CodexWorkTests(Fixture):
    def test_codex_replies_and_patched_files(self):
        lines = codex_lines() + [x_msg("assistant", "Wrapped up.", 9),
                                 x_call("custom_tool_call", "apply_patch",
                                        "*** Begin Patch\n*** Update File: C:\\fake\\b.py\n*** End Patch\n", 10)]
        self.codex_file(lines)
        facts = self.facts(kind="codex", session_id=CODEX_ID)
        self.assertEqual([text for _, text in facts["recent_replies"]][:3],
                         ["Wrapped up.", "All fake tests pass now.", "Looking now."])
        self.assertEqual(facts["recent_files"][0].lower(), "c:\\fake\\b.py")
        self.assertEqual({path.lower() for path in facts["recent_files"]},
                         {"c:\\fake\\a.py", "c:\\fake\\b.py", "c:\\fake\\c.py"})


if __name__ == "__main__":
    unittest.main()

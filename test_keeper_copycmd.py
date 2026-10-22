# Last modified: 2026-10-20 | Change: Tests for the Copy command text builder | Full history: git log
"""Fake snapshot rows only: no real session IDs, nothing is launched."""

import unittest

from keeper_copycmd import build_resume_text
from keeper_restore import resume_program

ID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
BS = chr(92)
VD = "C:" + BS + "Users" + BS + "example" + BS + "ProjectB"
OT = "C:" + BS + "Users" + BS + "example" + BS + "Other"


def row(session_id=ID_A, kind="claude", title="ProjectB", cwd=VD, key="0:0"):
    return {"key": key, "session_id": session_id, "kind": kind, "title": title, "cwd": cwd}


# WHAT IT DOES: Guards the exact text the Copy command button puts on the clipboard.
# DEPENDS ON: keeper_copycmd.build_resume_text and keeper_restore.resume_program.
# AFFECTS: Test run only.
class CopyCommandTests(unittest.TestCase):
    def test_single_claude_tab_is_bare_two_lines(self):
        self.assertEqual(build_resume_text([row()]),
                         'cd "' + VD + '"\nclaude -r ' + ID_A)

    def test_codex_uses_the_restorer_command(self):
        text = build_resume_text([row(ID_B, "codex", "Other", OT)])
        self.assertEqual(text.splitlines()[1], " ".join(resume_program(row(ID_B, "codex"))))
        self.assertEqual(text.splitlines()[1], "codex resume " + ID_B)

    def test_multiple_tabs_have_comment_and_blank_line(self):
        text = build_resume_text([row(), row(ID_B, "codex", "Other", OT, "0:1")])
        self.assertEqual(text, '# ProjectB\ncd "' + VD + '"\nclaude -r ' + ID_A +
                         '\n\n# Other\ncd "' + OT + '"\ncodex resume ' + ID_B)

    def test_bad_session_id_refused(self):
        with self.assertRaises(ValueError):
            build_resume_text([row(session_id="not-an-id")])
        with self.assertRaises(ValueError):
            build_resume_text([row(session_id="")])

    def test_cwd_with_quote_or_newline_refused(self):
        for bad in (VD + '"', VD + chr(10)):
            with self.assertRaises(ValueError):
                build_resume_text([row(cwd=bad)])

    def test_unknown_kind_and_empty_refused(self):
        with self.assertRaises(RuntimeError):
            build_resume_text([row(kind="other")])
        with self.assertRaises(ValueError):
            build_resume_text([])

    def test_title_newline_cannot_inject_a_command(self):
        text = build_resume_text([row(title="x\nrm -rf /"), row(ID_B, key="0:1")])
        self.assertEqual(text.splitlines()[0], "# x rm -rf /")


if __name__ == "__main__":
    unittest.main()

# Last modified: 2026-10-19 | Change: Verify exact-process transcript identity fallback | Full history: git log
"""Synthetic transcripts only; never reads user messages."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from keeper_claude_identity import open_claude_record

FIRST='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
SECOND='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'

# WHAT IT DOES: Ensures only one proved open main transcript may supply a missing ID.
# DEPENDS ON: Temporary files and a fake process open-file list.
# AFFECTS: No title guesses, subagent restores, or arbitrary history searches.
class IdentityFallbackTests(unittest.TestCase):
    # WHAT IT DOES: Writes a synthetic main or subagent transcript.
    # DEPENDS ON: Temporary project folder.
    # AFFECTS: Fixture identity and path must both agree.
    def transcript(self,root,sid,sidechain=False,nested=False):
        folder=root/'project'
        if nested:folder=folder/'subagents'
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/(sid+'.jsonl')
        path.write_text(json.dumps({'sessionId':sid,'isSidechain':sidechain,'cwd':'test-project'})+'\n',encoding='utf-8')
        return path

    # WHAT IT DOES: Accepts a single verified main transcript actually held by the process.
    # DEPENDS ON: Fake open-file inventory.
    # AFFECTS: Missing PID records can be recovered without consulting filenames by title.
    def test_one_open_main(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=self.transcript(root,FIRST)
            process=SimpleNamespace(open_files=lambda:[SimpleNamespace(path=str(path))])
            self.assertEqual(open_claude_record(process,root)['sessionId'],FIRST)
            self.assertIsNone(open_claude_record(SimpleNamespace(open_files=lambda:[]),root))

    # WHAT IT DOES: Refuses subagent paths, sidechain metadata, and two possible main transcripts.
    # DEPENDS ON: Synthetic transcript variants.
    # AFFECTS: Workers or ambiguous history never become a recovery target.
    def test_ambiguous_and_subagent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            a=self.transcript(root,FIRST);b=self.transcript(root,SECOND)
            proc=lambda paths:SimpleNamespace(open_files=lambda:[SimpleNamespace(path=str(p)) for p in paths])
            self.assertIsNone(open_claude_record(proc([a,b]),root))
            b=self.transcript(root,SECOND,sidechain=True)
            self.assertIsNone(open_claude_record(proc([b]),root))
            nested=self.transcript(root,SECOND,nested=True)
            self.assertIsNone(open_claude_record(proc([nested]),root))

    # WHAT IT DOES: Rejects mismatched metadata and malformed records without guessing.
    # DEPENDS ON: Disposable corrupt transcript.
    # AFFECTS: A damaged file remains unknown.
    def test_bad_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=self.transcript(root,FIRST)
            path.write_text('[]\n'+json.dumps({'sessionId':SECOND,'isSidechain':False}),encoding='utf-8')
            process=SimpleNamespace(open_files=lambda:[SimpleNamespace(path=str(path))])
            self.assertIsNone(open_claude_record(process,root))

if __name__=='__main__':unittest.main()

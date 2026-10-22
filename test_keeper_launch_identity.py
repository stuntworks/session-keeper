# Last modified: 2026-10-19 | Change: Guard restored colors and worker separation | Full history: git log
"""Focused tests never launch or close real terminals."""
import unittest
from unittest.mock import patch
from keeper_restore import clean_environment, live_session_ids
from keeper_inventory import match_inventory
from keeper_core import recovery_rows

# WHAT IT DOES: Checks appearance inheritance and recovery eligibility.
# DEPENDS ON: Mock environments and inventories.
# AFFECTS: Workers must never become selectable terminal conversations.
class LaunchIdentityTests(unittest.TestCase):
    # WHAT IT DOES: Removes noninteractive appearance flags without changing the parent.
    # DEPENDS ON: A disposable environment.
    # AFFECTS: Restored CLI color support.
    def test_colors(self):
        import os
        with patch.dict(os.environ, {'NO_COLOR':'1','TERM':'dumb','COLORTERM':'','PATH':'keep'}, clear=True):
            child=clean_environment()
            self.assertNotIn('NO_COLOR',child)
            self.assertNotIn('TERM',child)
            self.assertNotIn('COLORTERM',child)
            self.assertEqual(child['PATH'],'keep')
            self.assertEqual(os.environ['NO_COLOR'],'1')

    # WHAT IT DOES: Preserves useful color capabilities rather than forcing a theme.
    # DEPENDS ON: A disposable environment.
    # AFFECTS: User terminal capabilities stay intact.
    def test_real_color_capabilities(self):
        with patch.dict('os.environ', {'TERM':'xterm-256color','COLORTERM':'truecolor'},clear=True):
            self.assertEqual(clean_environment(),{'TERM':'xterm-256color','COLORTERM':'truecolor'})

    # WHAT IT DOES: Rejects a worker even if its console title matches a visible tab.
    # DEPENDS ON: Synthetic snapshot input.
    # AFFECTS: Background work cannot reopen as a user conversation.
    def test_worker_not_matched(self):
        worker={'title':'Work','sessionId':'a'*36,'role':'worker','kind':'claude'}
        windows,unmatched,counts=match_inventory([{'tabs':['Work']}],[worker])
        self.assertEqual(counts['matched'],0)
        self.assertEqual(unmatched,[worker])

    # WHAT IT DOES: Keeps known worker IDs for deduplication without blocking on unknown workers.
    # DEPENDS ON: Synthetic process inventory.
    # AFFECTS: Background tasks do not prevent terminal recovery.
    def test_worker_does_not_block(self):
        rows=[{'role':'worker','uncertain':True,'sessionId':None},
              {'role':'worker','uncertain':False,'sessionId':'known-worker'}]
        with patch('keeper_inventory.live_inventory',return_value=rows), patch('keeper_restore.STRICT_WAIT_SECONDS',0):
            self.assertEqual(live_session_ids(strict=True),{'known-worker'})

    # WHAT IT DOES: Continues to refuse an unidentified interactive process.
    # DEPENDS ON: Synthetic process inventory.
    # AFFECTS: Duplicate protection is not weakened for user terminals.
    def test_unknown_interactive_blocks(self):
        with patch('keeper_inventory.live_inventory',return_value=[{'role':'interactive','uncertain':True,'sessionId':None}]), patch('keeper_restore.STRICT_WAIT_SECONDS',0):
            with self.assertRaises(RuntimeError): live_session_ids(strict=True)

    # WHAT IT DOES: Refuses saved worker rows even if a previous snapshot marked them matched.
    # DEPENDS ON: Synthetic saved snapshot.
    # AFFECTS: Recovery cannot launch worker records.
    def test_saved_worker_not_restored(self):
        snapshot={'windows':[{'tabs':[{'outcome':'MATCHED','sessionId':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','role':'worker'}]}]}
        self.assertEqual(recovery_rows(snapshot),[])


    # WHAT IT DOES: Checks explicit worker commands without treating prompt words as commands.
    # DEPENDS ON: The launch classifier.
    # AFFECTS: Interactive prompts containing words like review stay interactive.
    def test_command_roles(self):
        from keeper_roles import command_role
        cases=[('claude',['claude.exe','-p','do work'],'worker'),
               ('claude',['claude.exe','--print=hello'],'worker'),
               ('claude',['claude.exe','--','-p'],'interactive'),
               ('codex',['codex.exe','exec','do work'],'worker'),
               ('codex',['codex.exe','review'],'worker'),
               ('codex',['codex.exe','resume','review'],'interactive'),
               ('codex',['codex.exe','exec-server'],'service'),
               ('codex',['codex.exe','app-server'],'service'),
               ('codex',['codex.exe','-c','setting=value','app-server'],'service'),
               ('codex',['codex.exe','-c','setting=review','resume','abc'],'interactive')]
        for provider,command,expected in cases:
            with self.subTest(command=command):self.assertEqual(command_role(provider,command),expected)
        self.assertEqual(command_role('claude',['claude.exe'],r'C:\Program Files\WindowsApps\Claude_1\app\claude.exe'),'service')

    # WHAT IT DOES: Uses parent process evidence to separate a delegated child from a terminal.
    # DEPENDS ON: Fake process ancestry, with no real process changes.
    # AFFECTS: Child workers and inaccessible ancestry cannot become recoverable tabs.
    def test_parent_roles(self):
        from types import SimpleNamespace
        import psutil
        from keeper_roles import process_role
        parent=SimpleNamespace(name=lambda:'claude.exe',cmdline=lambda:['claude.exe'],exe=lambda:r'C:\tools\claude.exe')
        process=SimpleNamespace(info={'cmdline':['claude.exe']},parents=lambda:[parent])
        self.assertEqual(process_role(process,'claude'),'worker')
        process.parents=lambda:[]
        self.assertEqual(process_role(process,'claude'),'interactive')
        with patch.object(process,'parents',side_effect=psutil.AccessDenied(1)):
            self.assertEqual(process_role(process,'claude'),'unknown')

    # WHAT IT DOES: Proves the actual launch function passes the cleaned environment to wt.
    # DEPENDS ON: A mocked Popen, never a real launch.
    # AFFECTS: Testing just the filter must not miss a disconnected launch path.
    def test_launch_uses_color_cleanup(self):
        from pathlib import Path
        from keeper_restore import launch_tab
        row={'session_id':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','title':'Test','cwd':str(Path.home()),'kind':'claude'}
        with patch.dict('os.environ',{'NO_COLOR':'1','TERM':'dumb'}), patch('keeper_restore.subprocess.Popen') as launch:
            launch_tab('test-window',row)
        self.assertNotIn('NO_COLOR',launch.call_args.kwargs['env'])
        self.assertNotIn('TERM',launch.call_args.kwargs['env'])

if __name__=='__main__': unittest.main()

# Last modified: 2026-10-22 | Change: Full restore opens back windows first | Full history: git log
"""Mocks only: no real terminal launches or Windows settings writes."""
import unittest
from unittest.mock import patch,MagicMock
from contextlib import nullcontext,ExitStack
from pathlib import Path
import keeper_app
from keeper_core import plan_restore

# WHAT IT DOES: Builds two saved windows with the same overlap seen during recovery.
# ISOLATED
def layout():
    return {'captured_at':'saved','windows':[
      {'x':0,'y':0,'w':1000,'h':700,'tabs':[{'outcome':'MATCHED','sessionId':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','strippedTitle':'First','cwd':'C:/proj'}]},
      {'x':850,'y':0,'w':1000,'h':700,'tabs':[{'outcome':'MATCHED','sessionId':'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','strippedTitle':'Second','cwd':'C:/proj'}]}]}

# WHAT IT DOES: Proves login respects consent and still skips foreign windows.
# DEPENDS ON: Fake Tk, history, and window records.
# AFFECTS: Automatic recovery cannot run on every recorder restart or overwrite occupied layouts.
class StartupIntegrationTests(unittest.TestCase):
    # WHAT IT DOES: Runs the actual login entry point with disposable service dependencies.
    # DEPENDS ON: Mock state only.
    # AFFECTS: A saved checkbox must reach the startup recovery screen.
    def startup(self,enabled,recovery):
        root=MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(keeper_app.sys,'argv',['keeper_app.py','watch']))
            stack.enter_context(patch.object(keeper_app.tk,'Tk',return_value=root))
            stack.enter_context(patch.object(keeper_app,'exclusive_operation',side_effect=lambda name:nullcontext()))
            stack.enter_context(patch.object(keeper_app,'auto_restore_enabled',return_value=enabled))
            stack.enter_context(patch.object(keeper_app,'read_boot_id',return_value='new'))
            stack.enter_context(patch.object(keeper_app,'startup_recovery',return_value=recovery,create=True))
            screen=stack.enter_context(patch.object(keeper_app,'RecoveryWindow'))
            keeper_app.main()
            return screen.call_args,root

    # WHAT IT DOES: Opens the countdown screen only for opted-in prior-boot recovery.
    # DEPENDS ON: Fake prior-boot snapshot.
    # AFFECTS: The login shortcut actually uses the stored checkbox.
    def test_enabled_previous_boot_opens_recovery(self):
        pair=(Path('saved.json'),layout())
        call,root=self.startup(True,pair)
        self.assertIsNotNone(call)
        self.assertEqual(call.args[1:],pair)
        self.assertTrue(call.kwargs['watch_mode'])
        root.deiconify.assert_called_once()

    # WHAT IT DOES: Keeps startup silent when disabled or there is no previous-boot recovery.
    # DEPENDS ON: Fake stored preference and history.
    # AFFECTS: Closing tabs or restarting the watcher cannot resurrect work.
    def test_no_unrequested_recovery(self):
        for enabled,pair in [(False,(Path('saved.json'),layout())),(True,None)]:
            call,root=self.startup(enabled,pair)
            self.assertIsNone(call)
            root.deiconify.assert_not_called()

    # WHAT IT DOES: Allows overlap with a verified neighboring group restored from the same snapshot.
    # DEPENDS ON: Exact created-window receipt and saved tab title.
    # AFFECTS: A saved overlap cannot strand a complete conversation.
    def test_saved_neighbor_overlap_allowed(self):
        current=[{'hwnd':7,'x':0,'y':0,'w':1000,'h':700,'tabs':['First']}]
        owned=[{'hwnd':7,'group':'saved#0','window_name':'SessionKeeper-first'}]
        plans,warnings=plan_restore(layout(),{'1:0'},set(),current,owned_windows=owned)
        self.assertEqual(len(plans),1)
        self.assertNotIn('window_name',plans[0])

    # WHAT IT DOES: Still rejects an overlapping window without matching ownership evidence.
    # DEPENDS ON: A foreign tab or an old snapshot's receipt.
    # AFFECTS: Other user work remains untouched.
    def test_foreign_overlap_blocked(self):
        for title,group in [('Unrelated','saved#0'),('First','old#0')]:
            plans,_=plan_restore(layout(),{'1:0'},set(),[{'hwnd':7,'x':0,'y':0,'w':1000,'h':700,'tabs':[title]}],owned_windows=[{'hwnd':7,'group':group,'window_name':'SessionKeeper-first'}])
            self.assertEqual(plans,[])

    # WHAT IT DOES: Refills a target group despite its known recovered neighbor overlapping.
    # DEPENDS ON: Two verified receipts from the same layout.
    # AFFECTS: Missing tabs join the correct window instead of being skipped.
    def test_refill_with_known_neighbor(self):
        current=[{'hwnd':7,'x':0,'y':0,'w':1000,'h':700,'tabs':['First']}, {'hwnd':8,'x':850,'y':0,'w':1000,'h':700,'tabs':['Second']}]
        owned=[{'hwnd':7,'group':'saved#0','window_name':'SessionKeeper-first'},{'hwnd':8,'group':'saved#1','window_name':'SessionKeeper-second'}]
        plans,_=plan_restore(layout(),{'1:0'},set(),current,owned_windows=owned)
        self.assertEqual(plans[0]['window_name'],'SessionKeeper-second')


    # WHAT IT DOES: Proves STOP cancels a Terminal startup wait or recovery countdown.
    # DEPENDS ON: A hidden Tk root and mocked read-only system state.
    # AFFECTS: Countdown never launches a terminal during testing.
    def test_countdown_and_stop_controls(self):
        import tkinter as tk
        for competing in (False,True):
            root=tk.Tk();root.withdraw()
            try:
                with patch.object(keeper_app,'fast_history',return_value=[]), patch.object(keeper_app,'live_session_ids',return_value=set()), patch.object(keeper_app,'auto_restore_enabled',return_value=True), patch.object(keeper_app,'terminal_restore_enabled',return_value=competing), patch.object(keeper_app,'LiveChatsPanel'), patch.object(keeper_app,'restore_selected') as restore:
                    view=keeper_app.RecoveryWindow(root,Path('saved.json'),layout(),watch_mode=True)
                    if competing:
                        self.assertIsNone(view.countdown_job)
                        self.assertIn('Waiting for Windows Terminal',view.countdown_text.cget('text'))
                        wait_token=view.native_wait
                        self.assertIsNotNone(wait_token)
                    else:
                        self.assertIsNotNone(view.countdown_job)
                        self.assertIn('10',view.countdown_text.cget('text'))
                    view.skip()
                    if competing:
                        self.assertTrue(wait_token.is_set())
                    self.assertIsNone(view.countdown_job)
                    self.assertEqual(root.state(),'withdrawn')
                    restore.assert_not_called()
            finally:root.destroy()

    # WHAT IT DOES: Runs a complete two-window restore with overlapping saved positions.
    # DEPENDS ON: Fake launch and OS windows, actual restore and planner code.
    # AFFECTS: Fresh receipts must be reused during the same restore operation.
    def test_full_restore_overlapping_groups(self):
        from keeper_restore import restore_selected
        current=[];live=set()
        def launch(name,row):
            current.append({'hwnd':len(current)+1,'tabs':[row['title']],**{key:row['window'][key] for key in ('x','y','w','h')}})
            live.add(row['session_id'])
        with patch('keeper_restore.live_session_ids',side_effect=lambda **kw:set(live)), patch('keeper_restore.read_windows',side_effect=lambda:list(current)), patch('keeper_restore.load_created_windows',return_value=[]), patch('keeper_restore.read_boot_id',return_value='boot'), patch('keeper_restore.read_monitors',return_value=[{'x':0,'y':0,'w':4000,'h':2000}]), patch('keeper_restore.launch_tab',side_effect=launch), patch('keeper_restore.find_new_window',side_effect=lambda *args:current[-1]), patch('keeper_restore.record_created_window'), patch('keeper_restore.place_new_window'), patch('keeper_restore.wait_for_session',return_value=True):
            restored,warnings=restore_selected(layout(),{'0:0','1:0'})
        # Same two tabs; the order is back window first (keeper_zorder.back_to_front). This snapshot has no
        # "z", so list order counts as front-to-back and the last window ('Second') opens first.
        self.assertEqual(sorted(restored),['First','Second'])
        self.assertEqual(restored,['Second','First'])
        self.assertEqual(warnings,[])

if __name__=='__main__':unittest.main()

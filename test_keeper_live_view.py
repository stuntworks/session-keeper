# Last modified: 2026-10-19 | Change: Verify live chat display without changing terminals | Full history: git log
"""The real Tk view is tested while withdrawn; inventories are synthetic."""
import tkinter as tk
import unittest
from unittest.mock import patch
from keeper_live_view import LiveChatsPanel,read_live_chats

# WHAT IT DOES: Checks visible chats, workers, and failed scans stay distinct.
# DEPENDS ON: A hidden Tk window and synthetic inventory.
# AFFECTS: The user must see unknown tabs and stale data rather than false success.
class LiveViewTests(unittest.TestCase):
    # WHAT IT DOES: Opens only a hidden test view; cancels its automatic scan.
    # DEPENDS ON: Tk and the panel's timer.
    # AFFECTS: Tests never inspect or change real terminal sessions.
    def setUp(self):
        self.root=tk.Tk();self.root.withdraw()
        self.panel=LiveChatsPanel(self.root)
        self.panel.after_cancel(self.panel.timer);self.panel.timer=None
        self.addCleanup(self.root.destroy)

    # WHAT IT DOES: Supplies one known tab, one unknown tab, and a separate worker.
    # ISOLATED
    def example(self):
        return {'windows':[{'x':10,'y':20,'tabs':[{'strippedTitle':'ProjectB','outcome':'MATCHED','sessionId':'abc'}, {'strippedTitle':'Other tab','outcome':'NONE'}]}],
                'counts':{'tabs_total':2,'matched':1},'checked_at':'now',
                'unplaced':[{'title':'Build worker','role':'worker','sessionId':'worker-id'}]}

    # WHAT IT DOES: Keeps both visible tabs and lists workers in their own group.
    # DEPENDS ON: The actual Treeview renderer.
    # AFFECTS: Background work cannot inflate the visible tab count.
    def test_all_tabs_and_separate_workers(self):
        self.panel.show_result(self.example())
        groups=self.panel.tree.get_children()
        self.assertEqual(len(groups),2)
        self.assertEqual(len(self.panel.tree.get_children(groups[0])),2)
        self.assertIn('Background workers',self.panel.tree.item(groups[1],'text'))
        self.assertIn('2 tabs on screen',self.panel.status.cget('text'))
        self.assertIn('1 unknown',self.panel.status.cget('text'))

    # WHAT IT DOES: Preserves prior rows and marks them stale when a refresh fails.
    # DEPENDS ON: The actual error callback.
    # AFFECTS: Scan failure must not look like an empty desktop.
    def test_scan_error_is_visible(self):
        self.panel.show_result(self.example())
        before=self.panel.tree.get_children()
        self.panel.show_error(RuntimeError('probe failed'))
        self.assertEqual(self.panel.tree.get_children(),before)
        self.assertIn('out of date',self.panel.status.cget('text'))

    # WHAT IT DOES: Reads current UI state and propagates failures instead of inventing rows.
    # DEPENDS ON: Mocked operating-system readers.
    # AFFECTS: Live list never silently substitutes the recovery snapshot.
    def test_reads_live_state(self):
        with patch('keeper_live_view.read_windows',return_value=[{'tabs':['Current']}]), patch('keeper_live_view.live_inventory',return_value=[]):
            result=read_live_chats()
        self.assertEqual(result['windows'][0]['tabs'][0]['strippedTitle'],'Current')
        with patch('keeper_live_view.read_windows',side_effect=RuntimeError('unreadable')):
            with self.assertRaises(RuntimeError):read_live_chats()

if __name__=='__main__':unittest.main()

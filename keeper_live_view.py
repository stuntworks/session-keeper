# Last modified: 2026-10-21 | Change: Dark, readable On screen now list | Full history: git log
"""Read-only live chat panel. The existing watcher continues saving recovery history."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from keeper_background import run_background
from keeper_core import read_windows
from keeper_inventory import live_inventory, match_inventory
from keeper_theme import DIM, ROOM, FlatButton, Theme, label

# WHAT IT DOES: Reads visible tabs and verified processes without writing recovery history.
# DEPENDS ON: Windows UI Automation and the process inventory.
# AFFECTS: A failed scan is an error, never an empty successful chat list.
def read_live_chats():
    windows=read_windows()
    sessions=live_inventory()
    groups,unplaced,counts=match_inventory(windows,sessions)
    return {'windows':groups,'unplaced':unplaced,'counts':counts,
            'checked_at':datetime.now().astimezone().strftime('%I:%M:%S %p')}

# WHAT IT DOES: Displays all observed tab names, identities, and separate worker status.
# DEPENDS ON: Tk's main thread and a background read-only inventory scan.
# AFFECTS: Never opens, closes, renames or moves terminal sessions.
class LiveChatsPanel(tk.Frame):
    # WHAT IT DOES: Builds a refreshable list and starts the first scan after the UI opens.
    # DEPENDS ON: A parent notebook tab.
    # AFFECTS: The recovery screen stays responsive while process identities are checked.
    def __init__(self,parent,theme=None):
        super().__init__(parent,background=ROOM)
        self.pack(fill='both',expand=True)
        theme=theme or Theme(self.winfo_toplevel())
        theme.style_ttk()
        px=theme.px
        self.loading=False
        self.closed=False
        self.timer=None
        self.bind('<Destroy>',self.on_destroy,add='+')
        top=tk.Frame(self,background=ROOM)
        top.pack(fill='x',pady=(0,px(16)))
        self.refresh_button=FlatButton(top,theme,'Refresh now',self.refresh,width=180,height=56)
        self.refresh_button.pack(side='right')
        self.status=label(top,theme,'Checking current terminal chats...',size=20,anchor='w',justify='left',wraplength=px(1500))
        self.status.pack(side='left',fill='x')
        area=tk.Frame(self,background=ROOM)
        area.pack(fill='both',expand=True)
        self.tree=ttk.Treeview(area,columns=('status','id'),show='tree headings',style='Keeper.Treeview')
        self.tree.heading('#0',text='Window / tab name')
        self.tree.heading('status',text='Identification')
        self.tree.heading('id',text='Conversation ID')
        self.tree.column('#0',width=px(900))
        self.tree.column('status',width=px(360))
        self.tree.column('id',width=px(620))
        scroll=ttk.Scrollbar(area,orient='vertical',command=self.tree.yview,style='Keeper.Vertical.TScrollbar')
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        self.tree.pack(fill='both',expand=True)
        label(self,theme,'Tab names are shown as Windows reports them. Unknown tabs stay listed. Background workers are not restored.',size=19,colour=DIM,anchor='w').pack(fill='x',pady=(px(12),0))
        self.timer=self.after(100,self.refresh)

    # WHAT IT DOES: Stops automatic refresh when this panel is replaced or closed.
    # DEPENDS ON: Tk destruction events and the refresh timer.
    # AFFECTS: Switching saved layouts cannot update a destroyed panel.
    def on_destroy(self,event):
        if event.widget is not self:return
        self.closed=True
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer=None

    # WHAT IT DOES: Scans asynchronously; manual refresh replaces a pending timer.
    # DEPENDS ON: Existing background helper and the live inventory reader.
    # AFFECTS: At most one scan runs for this panel.
    def refresh(self):
        if self.closed or self.loading:return
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer=None
        self.loading=True
        self.refresh_button.configure(state='disabled')
        self.status.configure(text='Checking current terminal chats...')
        run_background(self.winfo_toplevel(),read_live_chats,self.show_result,self.show_error)

    # WHAT IT DOES: Renders a completed scan, including tabs whose identity is unknown.
    # DEPENDS ON: A successfully read current screen, never the selected historical snapshot.
    # AFFECTS: Counts describe visible tabs, not total agent processes.
    def show_result(self,result):
        if self.closed:return
        children=self.tree.get_children()
        if children:self.tree.delete(*children)
        for number,window in enumerate(result['windows'],1):
            group=self.tree.insert('', 'end',text=f"Terminal {number} ({window.get('x')}, {window.get('y')})",open=True)
            for tab in window['tabs']:
                status='Identified' if tab['outcome']=='MATCHED' else 'Unknown - not restorable'
                self.tree.insert(group,'end',text=tab.get('strippedTitle') or tab.get('rawTitle') or 'Unnamed tab',values=(status,tab.get('sessionId') or ''))
        workers=[s for s in result['unplaced'] if s.get('role')=='worker']
        other=[s for s in result['unplaced'] if s.get('role')!='worker']
        for title,sessions in [('Background workers - not restored',workers),('Processes not linked to a visible tab',other)]:
            if sessions:
                group=self.tree.insert('','end',text=title,open=True)
                for session in sessions:
                    self.tree.insert(group,'end',text=session.get('title') or f"{session.get('kind')} PID {session.get('pid')}",values=(session.get('role','unknown'),session.get('sessionId') or ''))
        counts=result['counts']
        self.status.configure(text=f"{counts['tabs_total']} tabs on screen | {counts['matched']} identified | {counts['tabs_total']-counts['matched']} unknown | {len(workers)} background workers. Checked {result['checked_at']}.")
        self.finish_refresh()

    # WHAT IT DOES: Keeps the previous rows but clearly marks a failed refresh as stale.
    # DEPENDS ON: An inventory exception.
    # AFFECTS: A scan failure cannot falsely report that all terminals closed.
    def show_error(self,error):
        if self.closed:return
        self.status.configure(text=f'Could not refresh - this list may be out of date: {error}')
        self.finish_refresh()

    # WHAT IT DOES: Enables manual refresh and schedules another read in one minute.
    # DEPENDS ON: A still-live Tk panel.
    # AFFECTS: Tracking stays visible while this window is open.
    def finish_refresh(self):
        self.loading=False
        self.refresh_button.configure(state='normal')
        self.timer=self.after(60000,self.refresh)

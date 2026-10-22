# Last modified: 2026-10-21 | Change: Walk parents from one parent-id snapshot per scan instead of one per step | Full history: git log
"""Classify launch evidence, never project names or window titles."""
from pathlib import PureWindowsPath
import time
import psutil

# WHAT IT DOES: Classifies explicit headless commands and desktop services.
# DEPENDS ON: Executable path and argument tokens supplied by the operating system.
# AFFECTS: Only interactive candidates may be matched to recovery tabs.
def command_role(provider, command, executable=''):
    path=PureWindowsPath(executable)
    if provider=='claude' and path.parent.name.lower()=='app' and path.parent.parent.name.lower().startswith('claude_') and path.parent.parent.parent.name.lower()=='windowsapps':
        return 'service'
    arguments=list(command[1:])
    if provider=='codex':
        # Global config options can precede the actual service or worker command.
        while arguments:
            option=arguments[0]
            if option in ('-c','--config','--enable','--disable'):
                if len(arguments)<2: return 'unknown'
                arguments=arguments[2:]
            elif option.startswith(('--config=','--enable=','--disable=')):
                arguments=arguments[1:]
            else:
                break
        first=arguments[0] if arguments else '' 
        if first in ('app-server','exec-server','sandbox'): return 'service'
        if first in ('exec','e','review'): return 'worker'
    else:
        for argument in arguments:
            if argument=='--': break
            if argument in ('-p','--print') or argument.startswith('--print='):
                return 'worker'
    return 'interactive'

# WHAT IT DOES: Reads every process's parent id once (one ~40 ms Windows table read) for a whole scan.
# DEPENDS ON: psutil's own Windows parent table (the same one Process.ppid() reads on every call).
# AFFECTS: Only where psutil gets a parent id from; every other check in the parent walk is unchanged.
class ParentSnapshot:
    def __init__(self, read_map=None, clock=time.time):
        self.read_map=read_map or getattr(getattr(psutil,'_psplatform',None),'ppid_map',None)
        self.clock, self.taken_at, self.parents, self.loaded = clock, None, None, False

    def load(self):
        if not self.loaded:
            self.loaded=True
            try:
                if self.read_map:
                    self.taken_at=self.clock()
                    self.parents=dict(self.read_map())
            except (OSError, psutil.Error, TypeError, ValueError):
                self.parents=None
        return self.parents

    # DANGER ZONE HIGH: A wrong parent id could hide a live worker / breaks: duplicate prevention / depends on: start before snapshot.
    # Only a process that started before the table was read can be looked up in it; its verified
    # ancestors started even earlier and are still alive, so their rows are theirs. Anything newer
    # (1 s margin for clock steps) gets None and walks the original slow way.
    def usable_for(self, process):
        parents=self.load()
        if parents is None: return None
        try:
            started=process.create_time()
        except psutil.Error:
            return None
        return parents if started < self.taken_at-1.0 else None

# WHAT IT DOES: Same walk as psutil's Process.parents(), with each parent id pre-filled from the snapshot.
# DEPENDS ON: psutil.Process.ppid() returning its cached _ppid on Windows (guarded by a test).
# AFFECTS: psutil still re-reads start times, rejects reused pids and raises for vanished processes.
def snapshot_parents(process, parents):
    found=[]
    current=process
    while True:
        known=parents.get(current.pid)
        if known and hasattr(current,'_ppid') and current._ppid is None:
            current._ppid=known
        current=current.parent()
        if current is None: return found
        found.append(current)

# WHAT IT DOES: Also recognizes CLI children launched beneath another CLI process.
# DEPENDS ON: Process ancestry; inaccessible ancestry remains unknown. Optional ParentSnapshot for speed.
# AFFECTS: A delegated child cannot be restored as the user's terminal.
def process_role(process, provider, snapshot=None):
    command=process.info.get('cmdline') or []
    executable=command[0] if command else ''
    role=command_role(provider,command,executable)
    if role!='interactive': return role
    try:
        parents=snapshot.usable_for(process) if snapshot is not None else None
        ancestry=snapshot_parents(process,parents) if parents is not None else process.parents()
        for parent in ancestry:
            name=parent.name().lower()
            if name not in ('claude.exe','codex.exe'): continue
            parent_command=parent.cmdline()
            parent_provider=name[:-4]
            if command_role(parent_provider,parent_command,parent.exe())!='service':
                return 'worker'
    except psutil.NoSuchProcess:
        return 'unknown'
    except psutil.AccessDenied:
        return 'unknown'
    return role

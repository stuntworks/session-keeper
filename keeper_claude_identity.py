# Last modified: 2026-10-19 | Change: Recover Claude identity from an open main transcript | Full history: git log
"""Read-only fallback; never searches for a conversation by title or recency."""
import json
import re
from pathlib import Path

SESSION_NAME=re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')

# WHAT IT DOES: Verifies that an open transcript is a main conversation in Claude's project store.
# DEPENDS ON: A direct project transcript and its own session metadata.
# AFFECTS: Subagent files, misnamed files, and unreadable records remain unknown.
def main_transcript_record(path,projects_root):
    path=Path(path).resolve()
    if path.parent.parent!=Path(projects_root).resolve() or path.suffix!='.jsonl' or not SESSION_NAME.fullmatch(path.stem):
        return None
    try:
        with path.open('r',encoding='utf-8') as file:
            budget=2*1024*1024
            for unused in range(64):
                line=file.readline(budget+1)
                budget-=len(line)
                if not line or budget<0:break
                try: record=json.loads(line)
                except ValueError:continue
                if not isinstance(record,dict):continue
                if record.get('sessionId')==path.stem and record.get('isSidechain') is False:
                    return {'sessionId':path.stem,'cwd':record.get('cwd')}
    except (OSError,UnicodeError):
        return None
    return None

# WHAT IT DOES: Uses only main transcript files held open by this exact live process.
# DEPENDS ON: OS open-file inventory and verified transcript metadata.
# AFFECTS: Multiple main conversations remain ambiguous; old resume arguments are not used.
def open_claude_record(process,projects_root=None):
    root=projects_root or Path.home()/'.claude'/'projects'
    found={}
    for opened in process.open_files():
        record=main_transcript_record(opened.path,root)
        if record:found[record['sessionId']]=record
    return next(iter(found.values())) if len(found)==1 else None

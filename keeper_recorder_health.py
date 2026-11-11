# Last modified: 2026-11-10 | Change: No false alarms after reboot or sleep: trust only the live recorder's heartbeat, re-check before saying stuck, not-saving needs a real problem streak | Full history: git log
"""Is the background recorder alive and saving? Read by the recovery screen (recover mode only).

On 2026-10-29 the recorder stopped saving for 12 days and nothing on screen said so. This reads the
"watch" lock (without taking it), the recorder's heartbeat file and the newest saved layout's date, and
turns them into one short warning line. If the recorder is not running it is started, hidden.
A warning that cries wolf gets ignored, so when the evidence is unclear it stays silent, except that a
missing recorder is always reported.
"""

from datetime import datetime

from keeper_heartbeat import read_heartbeat

STUCK_AFTER = 600           # seconds since the recorder last tried a capture (it tries every 2 minutes)
STALE_AFTER = 1800          # seconds since the newest saved layout, and since the problem streak began
START_SLACK = 120           # seconds between the recorder process starting and it writing its first heartbeat
RECHECK_MS = 180_000        # "stuck" is only shown if it is still true this much later (the PC may just have woken)
STUCK_START = "Recorder is stuck."


# WHAT IT DOES: A plain amount of time: "5 minutes", "3 hours", "12 days".
# DEPENDS ON: Nothing.
# AFFECTS: Text only.
def amount(seconds):
    seconds = max(0, int(seconds))
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return "less than a minute"


# WHAT IT DOES: "12 days ago", "3 hours ago", "just now".
def ago(seconds):
    return "just now" if seconds < 60 else f"{amount(seconds)} ago"


# WHAT IT DOES: Reads an ISO time as a local, timezone-aware datetime; None if missing or unreadable.
def parse_time(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return None


# WHAT IT DOES: The layout's date, time and age for the header: "Oct 29, 2:44 PM - 12 days ago".
# DEPENDS ON: The snapshot's captured_at.
# AFFECTS: Text only; an unreadable time is shown as it is, without an age.
def saved_when(snapshot, now=None):
    moment = parse_time(snapshot.get("captured_at"))
    if moment is None:
        return str(snapshot.get("captured_at", "unknown time"))
    now = (now or datetime.now()).astimezone()
    text = f"{moment:%b %d}, {moment:%I:%M %p}".replace(", 0", ", ").replace(" 0", " ", 1)
    return f"{text} - {ago((now - moment).total_seconds())}"


# WHAT IT DOES: The warning line, or "" when the recorder is fine.
# DEPENDS ON: running (the watch lock is held), started (this screen just started it), the heartbeat dict or
# None, the newest saved layout's captured_at, and now.
# AFFECTS: Text only. Short sentences, for someone who finds reading hard.
def recorder_warning(running, started, heartbeat, newest_at, now):
    newest = parse_time(newest_at)
    old = f" Newest layout is {amount((now - newest).total_seconds())} old." if newest else ""
    if not running:
        if started:
            return f"Recorder was off. It is on again now.{old}"
        return f"Recorder is not running.{old} To fix: close Session Keeper and open it again."
    heartbeat = heartbeat or {}
    attempt = parse_time(heartbeat.get("last_attempt")) or parse_time(heartbeat.get("started_at"))
    if attempt is not None and (now - attempt).total_seconds() > STUCK_AFTER:
        return f"{STUCK_START} Last try was {ago((now - attempt).total_seconds())}.{old}"
    since = parse_time(heartbeat.get("problem_since"))     # set only when tabs existed but none matched, or a failure
    if since is not None and (now - since).total_seconds() > STALE_AFTER and \
            newest is not None and (now - newest).total_seconds() > STALE_AFTER:
        reason = heartbeat.get("last_error") or heartbeat.get("note")
        why = f" Last problem: {reason[:90]}." if isinstance(reason, str) and reason else ""
        return f"Recorder is on but has not saved for {amount((now - newest).total_seconds())}.{why}"
    return ""


# WHAT IT DOES: When a process with this pid started (epoch seconds), or None if there is no such process.
# DEPENDS ON: psutil.
# AFFECTS: Nothing.
def process_started(pid):
    try:
        import psutil
        return psutil.Process(int(pid)).create_time()
    except Exception:
        return None


# WHAT IT DOES: The heartbeat only if it was written by the recorder running now; otherwise None.
# DEPENDS ON: started_of(pid) (process_started, injectable).
# AFFECTS: Nothing. Rejects a file left by the previous boot's recorder, an old-format file without pid or
# started_at, and a pid that Windows has since given to a different, newer process.
def live_heartbeat(heartbeat, started_of=process_started):
    if not isinstance(heartbeat, dict):
        return None
    began = parse_time(heartbeat.get("started_at"))
    if began is None or heartbeat.get("pid") is None:
        return None
    created = started_of(heartbeat["pid"])
    if created is None or created > began.timestamp() + START_SLACK:
        return None
    return heartbeat


# WHAT IT DOES: Checks the recorder, starts it when it is not running, and returns the warning line.
# DEPENDS ON: is_running("watch") (keeper_locks.operation_running: looks, never takes the lock), start()
# (keeper_watchdog.launch_replacement without --handover), the heartbeat in data_dir. All injectable.
# AFFECTS: At most one new hidden recorder process. Never raises.
# DANGER ZONE HIGH: Only the recover-mode entry point may call this / breaks: tests or a login screen
# could start a real recorder / depends on: keeper_app.open_recovery being its only caller.
def check_recorder(data_dir, newest_at, is_running=None, start=None, now=None, started_of=process_started):
    if is_running is None:
        from keeper_locks import operation_running as is_running
    if start is None:
        from keeper_watchdog import launch_replacement
        start = lambda: launch_replacement(handover=False)
    now = (now or datetime.now()).astimezone()
    try:
        running = is_running("watch")
    except Exception:
        running = True                       # cannot tell: never start a second recorder
    started = False
    if not running:
        try:
            start()
            started = True
        except Exception:
            started = False
    heartbeat = live_heartbeat(read_heartbeat(data_dir), started_of) if running else None
    return recorder_warning(running, started, heartbeat, newest_at, now)


# WHAT IT DOES: Shows the recorder warning in the recovery screen's header (red line), or hides it.
# DEPENDS ON: A RecoveryWindow with history loaded and header.set_warning; view.root.after for the re-check.
# AFFECTS: The header only, plus whatever check_recorder starts.
# DANGER ZONE FRAGILE: "Stuck" right after the PC wakes is usually just a recorder that has not ticked yet /
# breaks: a red alarm after every sleep / depends on: the recorder trying again within RECHECK_MS of waking.
def show_recorder_health(view, data_dir, confirmed=False, **checks):
    newest_at = view.history[0][1].get("captured_at") if view.history else None
    text = check_recorder(data_dir, newest_at, **checks)
    if text.startswith(STUCK_START) and not confirmed:
        view.header.set_warning("")
        try:
            view.root.after(RECHECK_MS, lambda: show_recorder_health(view, data_dir, confirmed=True, **checks))
        except Exception:                    # the screen is closing: nothing to show
            pass
        return
    view.header.set_warning(text)

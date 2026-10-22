# Session Keeper

Crash and reboot recovery for Claude Code and Codex CLI sessions on Windows Terminal.

Built using [vozsay.com](https://vozsay.com). Talk to your coding agents instead of typing to them.

If you run many AI coding sessions at once, a crash or reboot loses all of them: which conversation was in which tab, which tabs shared a window, and where each window sat on screen. Session Keeper records that layout in the background. After a crash, you pick which sessions to bring back, and it reopens each one in its own window, tab order and screen position, resuming its own conversation.

## What you need

- Windows 10 or 11 with **Windows Terminal**
- **Python 3.12** with Tk (the standard python.org installer includes it)
- **Claude Code** and/or **Codex CLI**, run as tabs in Windows Terminal

## Install (once)

1. Download or clone this repository into a folder you will keep, for example `C:\Tools\session-keeper`. The shortcuts point at this folder, so do not move it afterwards.
2. Open PowerShell in that folder and run:

   ```powershell
   pip install psutil
   powershell -ExecutionPolicy Bypass -File install_startup.ps1          # preview: shows what it will create
   powershell -ExecutionPolicy Bypass -File install_startup.ps1 -Apply   # creates the shortcuts
   ```

   `-Apply` creates three shortcuts. It refuses to overwrite any that already exist.

   | Shortcut | Where | What it does |
   |---|---|---|
   | Session Keeper | Your **Startup** folder | Runs the background recorder at every login |
   | Session Keeper | Your **Desktop** | Opens the recovery screen |
   | Session Keeper | Your **Start menu** | Opens the recovery screen |

3. Turn off Windows Terminal's own restore. It competes with Session Keeper and can open duplicate windows:

   ```powershell
   python configure_terminal.py          # preview the one setting it changes
   python configure_terminal.py --apply  # apply it (backs up your Terminal settings first)
   ```

   It finds Windows Terminal (Store), Preview, Canary and unpackaged installs, and changes each settings file it finds.

4. Log out and back in, or start the recorder now without waiting:

   ```powershell
   pythonw keeper_app.py watch
   ```

## Day to day

You do nothing. The recorder runs silently and saves a snapshot about every two minutes: every Windows Terminal window, its tabs, and the exact Claude or Codex conversation in each tab. Closing a tab is normal use, not a crash, and it never pops anything up while you work. Old snapshots are thinned once an hour: it keeps everything from the last 2 hours, one per hour for 7 days, one per day for 30 days, and always the newest one and the last one before every reboot.

## After a crash or reboot

1. Double-click **Session Keeper** on your desktop. The recovery screen opens large on the monitor your mouse is on.
2. **Saved recovery** draws your monitors side by side with each saved terminal window as a box at its real spot. Each box shows its tabs as buttons. Windows that were minimised are listed in a strip underneath.
3. Tab colours: grey = already open (nothing opens twice), white with a red dot = missing, green = selected, **?** = could not be identified and is never restored. Hover a tab for its full name.
4. Click the missing tabs you want. Click a window's title bar to select all its missing tabs, or press **Select all** (formerly "Check all available") for every window.
5. Press **Preview** to see exactly what would happen, or **Restart selected** (formerly "Restart checked") to do it.
6. A missing tab whose window is still open is added back into that same window. Otherwise each conversation reopens grouped into its original window at its original spot.
7. **Copy command** (green when tabs are selected) copies one PowerShell line per tab. It moves to the saved folder and resumes the chat (`claude -r <session id>` or `codex resume <id>`) only if that folder exists. Paste them into PowerShell if the automatic restore fails.
8. **Saved layout** picks an earlier moment: Latest, Before last reboot, 15 min ago, 1 hour ago, earlier today, or previous days.

**Optional: automatic recovery.** On the recovery screen, tick **Restore saved chats at next Windows login**. After the next reboot, the recovery screen opens by itself at login and restores everything after a ten-second countdown. Press **Skip / STOP** to cancel. It is off by default and only triggers after a real reboot, never when the recorder simply restarts.

## Safety rules it follows

- Never opens a conversation that is already running.
- Never stacks a window on top of an occupied spot unless you click **Open on top anyway** (or **Restore beside them**) for that spot.
- Never guesses. A tab it cannot tie to one exact conversation is shown but not restored.
- Never closes a window on its own. If empty leftover terminals (bare prompts, no program running) sit on a saved spot, it asks once: **Close empty and restore**, **Restore beside them**, or **Cancel** (the default). A terminal with a running chat, or one it cannot check, is never offered for closing. The automatic login restore never closes anything.

## Commands

```powershell
python keeper_app.py recover    # open the recovery screen
python keeper_app.py snapshot   # take one snapshot now
python keeper_app.py watch      # run the background recorder
python -m unittest discover -v -p "test_keeper_*.py"
```

Snapshots and settings are stored in the `data` folder next to the scripts. They contain your session IDs, folder paths and tab titles, so keep that folder private and never commit it.

## Privacy

Session Keeper runs on your PC only. It has no account, no telemetry, and never uploads your snapshots. Claude Code and Codex CLI themselves use their online services as usual once a conversation is resumed.

Session Keeper is an independent project. It is not made or endorsed by Anthropic or OpenAI.

## Limits

- Claude and Codex **command-line** tabs only. The Codex desktop app is not covered.
- Split panes and which tab was active are not restored.
- If a monitor is disconnected, its windows move to the primary monitor, with a warning.
- Restoring reopens the conversation. It does not send a prompt or continue an interrupted task.
- A conversation where nothing was ever typed cannot be resumed.
- Conversations reopen in normal mode. Start options the original session used (for example permission flags) are not carried over.
- If Claude Code asks whether you trust a folder after a restore, answer it in that tab.
- PowerShell and Windows Terminal profiles only. Git Bash and WSL tabs are not covered, and the **Copy command** lines are for PowerShell.
- Session Keeper reads the session files Claude Code and Codex CLI save on your PC. Their format is not a public interface, so an update to either tool can break matching until Session Keeper is updated.
- Tested with automated tests and live layout captures. A full recovery after a real reboot has not yet been verified end to end. Reports welcome.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

If you redistribute Session Keeper or build on it, keep the NOTICE file with your copy, as the license requires.

## About

Session Keeper is made by StuntWorks. We build the tools we use every day and share the ones that might help others. We hope you find it useful.

- [VozSay](https://vozsay.com): offline voice typing for any Windows app, including your terminal. Session Keeper was built with it. Free 14-day trial.
- [CorridorKey StuntWorks](https://github.com/stuntworks/CorridorKey-StuntWorks): our edition of CorridorKey for DaVinci Resolve, After Effects and Premiere. It keys the green screen and also clears the garbage around it, so an imperfect green screen is done in a click or two. Updates coming soon.

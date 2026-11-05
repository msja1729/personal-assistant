# Personal Assistant — voice, senses and a pen for Claude Code

Talk to Claude on your computer. Say **"Jarvis"** (or press a hotkey), ask
anything, and hear the answer while it appears in a small card. Claude can
also **look at your screen** and **write and draw** to explain things, on a
translucent glass board or right over whatever you are looking at.

Works on **Windows 11** and **macOS 14 (Sonoma) or later**.

## What you need

- **Claude Code**, installed and signed in with your Claude account
  ([install guide](https://claude.com/claude-code)). Run `claude` once in a
  terminal and sign in before installing this app.
- **Python 3.11 or newer** ([python.org](https://www.python.org/downloads/)).
  On Windows, tick *"Add python.exe to PATH"* during setup.
- A microphone and speakers.

The app never calls Anthropic's API directly and needs no API key. It talks
to Claude only through Claude Code, using your existing Claude plan.

## Install

1. Download this repository (**Code → Download ZIP**) and unzip it, or `git clone` it.
2. Open a terminal in the folder and run:

   ```
   python install.py          # Windows
   python3 install.py         # macOS
   ```

The installer:

1. checks Claude Code is there;
2. copies the app to its own folder and installs its libraries in a private
   Python environment (nothing is added to your system Python);
3. writes the default settings and downloads the speech and wake-word models
   (about 200 MB);
4. adds the **senses** server (screen + pen tools) to Claude Code;
5. on macOS, asks for **Microphone**, **Screen Recording** and
   **Accessibility** access and tells you if any were denied;
6. optionally walks you through connecting **mail, Teams, calendar and
   health** — see [Connectors](#connectors);
7. adds a Start-menu entry (Windows), optionally starts the app at login,
   and launches it.

You can delete the downloaded folder afterwards.

## Use it

| Do this | What happens |
|---|---|
| Say **"Jarvis"**, or press **Ctrl+Shift+Space** | It starts listening (the card says *Listening*) |
| Ask your question, then stop talking | It notices the silence and sends your words to Claude |
| Wait a moment | The answer appears in the card and is read aloud |
| Press the hotkey while it is talking | It stops and listens to you again |
| Ask a follow-up ("and what about…") | Claude remembers the conversation |

Try:

- *"What's on my screen?"*
- *"Explain the formula for the sum of 1 to n on the board."*
- *"Circle the error in this code."*
- *"What's on my calendar tomorrow?"* (needs the calendar connector)

The wake word uses openWakeWord's *"hey Jarvis"* model, so both "Jarvis" and
"Hey Jarvis" work.

## Dashboard

Click the tray icon (Windows) or menu-bar icon (macOS) to open it. You can
turn each feature on or off: **wake word, speaking replies, notifications,
screen access, drawing**. You can also change the hotkey, the speech model
and how long a silence ends your question. Changes take effect on your next
question, without a restart.

All settings are kept in `config.yaml` in the app folder:

| | Folder |
|---|---|
| Windows | `%USERPROFILE%\.personal-assistant` |
| macOS | `~/Library/Application Support/PersonalAssistant` |

## Privacy and safety

- Your voice is turned into text **on your computer**; audio is never uploaded.
- Replies are spoken by your computer's own voice.
- Claude only sees your screen when it calls the screenshot tool, and only if
  **Screen access** is on. Each call takes a single screenshot; nothing is
  recorded continuously.
- Feature switches are enforced by the app's code, not left to Claude: with
  Screen access off, the screenshot tool refuses before any capture happens.
- Claude may read through connectors without asking, but anything that
  **sends, creates or deletes** (an email, an event…) needs your approval
  and is refused in voice mode.

## Connectors

Mail, Teams, calendar and health data come from **Claude's own connectors**,
which are linked to your Claude account. The installer opens
[claude.ai/settings/connectors](https://claude.ai/settings/connectors); you
click **Connect** and sign in, and it checks that Claude Code can see the
connector. Available:

- Microsoft 365 (Outlook mail, Teams, calendar)
- Gmail
- Google Calendar
- Health: search the connector directory for the app your phone or watch
  syncs to. That's Apple Health on iPhone or Health Connect / Google Fit on
  Android; Samsung Health, Fitbit, Garmin and others are also listed when
  available.

You can add more later from the dashboard (**Connectors…**).

## Uninstall

Windows: Start menu → **Uninstall Personal Assistant**. Or, on either system:

```
python  "%USERPROFILE%\.personal-assistant\app\uninstall.py"                  # Windows
python3 ~/Library/Application\ Support/PersonalAssistant/app/uninstall.py     # macOS
```

This removes everything the installer added, and nothing else:

- the app folder (app, private Python environment, settings, logs, models);
- the senses server in Claude Code;
- the Start-menu and login entries;
- Claude Code's history for the app's own conversations;
- on macOS, the permissions you granted.

Anything you had set up in Claude Code before is left alone. Connectors live
in your Claude account, so the uninstaller asks whether you want to
disconnect the ones you added during install. It can be run again safely.

## Troubleshooting

- **Nothing happens when I say "Jarvis"**: check the wake word is on in the
  dashboard and the microphone works. The hotkey always works.
- **"Claude Code is not installed"**: install it, run `claude` once to sign in,
  then run the installer again.
- **macOS: hotkey or screen capture does not work**: open System Settings →
  Privacy & Security and allow Accessibility and Screen Recording for Python,
  then restart the app.
- Logs are in the `logs` folder inside the app folder.

## For developers

```
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt     # macOS: .venv/bin/pip
python -m pytest                                      # macOS: .venv/bin/python -m pytest
python src/main.py --dashboard                        # run from source
```

Components (see [SPEC.md](SPEC.md)):

| File | Responsibility |
|---|---|
| `src/trigger.py` | Wake word (openWakeWord) and global hotkey |
| `src/capture.py` | Record until silence; local speech-to-text (faster-whisper) |
| `src/agent_bridge.py` | Claude Code (`claude -p --output-format stream-json`), one resumable session |
| `src/output.py` | Notification card; sentence-by-sentence seamless speech |
| `src/senses_server.py` | MCP server (stdio): `capture_screen` and the `board_*` pen tools |
| `src/overlay.py` | Glass board / screen overlay with handwriting-style animation |
| `src/dashboard.py` | Settings window |
| `src/platform_support.py` | Everything Windows- or macOS-specific |
| `src/ipc.py` | Authenticated local channel from the MCP server to the app |
| `install.py`, `uninstall.py` | Install and clean removal, driven by `install_manifest.json` |

Branches: **`Dev`** is where development happens; **`main`** holds public
releases. CI runs the tests on Windows and macOS.

## License

MIT. See [LICENSE](LICENSE).

# SPEC.md — Personal Assistant (Voice + Senses for Claude Code)

## Goal
A background desktop app for Windows and macOS that lets the user talk to Claude Code by voice, hear and see its replies, and gives Claude extra senses (screen view) and a pen (draw on a glass board or on the screen) to explain things.

## Scope (v1)
1. Trigger: the wake word "Jarvis" or a global hotkey starts listening.
2. Voice in: user speech is converted to text and sent to Claude Code.
3. Reply out: Claude's reply is shown as a notification and spoken aloud.
4. Senses: a local MCP server, registered into Claude Code by the first-run setup screen, exposes screen capture (single screenshot).
5. Pen: Claude can open a translucent "glass blackboard" or draw directly over the current screen to explain (text, LaTeX math, arrows, shapes, highlights).
6. Dashboard: settings to toggle every feature on/off at runtime.
7. Install and setup: the app is downloaded from GitHub Releases as a normal installer (Windows `Setup.exe`, macOS `.dmg`) with Python bundled. On first launch a setup screen checks Claude Code, adds the senses, requests macOS permissions and guides connectors; when setup is done, the dashboard opens. Guided connectors: the setup screen walks the user through adding existing mail, Teams, calendar, and health connectors to Claude Code. Health covers all major sources — Apple Health, Google Health (Health Connect / Google Fit), and others (e.g. Samsung Health, Fitbit, Garmin) — for users on both Android and Apple (iPhone/Mac) devices.
8. Uninstall: an "Uninstall" button in the dashboard (and, on Windows, *Settings → Apps*) cleans up everything the app added — app files, `config.yaml`, logs, models, and every entry it added to Claude Code's settings (the senses MCP server and any connectors added during setup). Anything the user had set up in Claude Code before installing is left untouched.

## Out of Scope (v1)
- Continuous screen capture / screen video
- Reminders and scheduling
- Writing our own mail/Teams/calendar integrations (the setup screen only guides setup of existing connectors)
- Linux, mobile, multi-user, cloud sync

## Constraints
- Target OS: Windows 11 and macOS 14 (Sonoma) or later. Every feature and acceptance criterion must hold on both.
- Python 3.11, type hints required. One codebase; OS-specific code is isolated behind a single platform module.
- Claude is reached only through Claude Code (headless, `claude -p` with `--output-format stream-json`) or the Claude Agent SDK. No direct Anthropic API calls. No controlling the interactive Claude CLI TUI.
- Speech-to-text runs locally (faster-whisper). Voice activity detection decides end of speech.
- Wake word: "Jarvis", detected with openWakeWord. Hotkey: configurable global shortcut.
- Text-to-speech: local OS TTS engine (SAPI on Windows, the macOS system voice via `say` on macOS). Each sentence is rendered to memory and played through one continuous audio stream.
- Spoken replies must sound continuous and natural, like one person talking: no audible gaps, clicks, restarts, or cut-off words between sentences or streamed chunks. The next sentence is prepared while the current one is playing, so playback never waits on it.
- UI (dashboard + overlay): PySide6. Overlay window is transparent, always-on-top, and click-through.
- Senses and pen are exposed only as MCP tools from our own MCP server (stdio).
- Every feature is controlled from `config.yaml`; the dashboard edits this file.
- Permissions are enforced in code by the MCP server and app, never left to the model.
- On macOS, the setup screen requests Microphone, Screen Recording, and Accessibility permissions and shows any that are denied.
- Distribution: installers are built by GitHub Actions with PyInstaller (Windows `Setup.exe`, macOS `.dmg`) and published on GitHub Releases. Users need Claude Code, not Python.

## Components

| Component | Responsibility |
|---|---|
| Trigger | Listen for wake word or hotkey; start a capture |
| Capture + STT | Record until silence, return transcript text |
| Agent Bridge | Send transcript to Claude Code, stream reply events, keep one resumable session |
| Output | Show reply as notification; speak reply sentence by sentence as one continuous, seamless stream (next sentence queued ahead, no gaps or cuts) |
| Senses MCP Server | Expose screen and pen tools; check toggles on every call |
| Overlay | Render pen tool commands with a handwriting-style animation |
| Dashboard | Show status; toggle features; changes apply without restart |
| Platform | All Windows/macOS-specific calls (hotkey, TTS, notifications, permissions, DPI) |
| Setup Screen | First-run window: check Claude Code; register MCP server; request OS permissions; guide connector setup; write default config; record everything it added in a manifest; then open the dashboard |
| Uninstall | Dashboard button (and Windows uninstaller hook): read the manifest; remove every Claude Code entry setup added (senses MCP server, connectors); delete app files, config, logs, and models; on macOS, reset the app's Microphone, Screen Recording, and Accessibility permissions |
| Packaging | GitHub Actions builds the Windows installer and macOS `.dmg` and attaches them to a GitHub Release |

## Inputs and Outputs

**Agent Bridge**
- In: transcript `str`
- Out: stream of events `{type: "text" | "tool_use" | "done" | "error", content: str}`

**MCP tools (Senses MCP Server)**

| Tool | Input | Output |
|---|---|---|
| `capture_screen` | `monitor: int = 0` | PNG image + `{width, height}` in pixels |
| `board_open` | `mode: "glass" \| "screen"` | `{ok: bool}` |
| `board_write_text` | `text, x, y, size?` | `{ok, id}` |
| `board_write_latex` | `latex, x, y, size?` | `{ok, id}` |
| `board_draw_arrow` | `x1, y1, x2, y2, label?` | `{ok, id}` |
| `board_draw_shape` | `shape: "box" \| "circle" \| "line", x, y, w, h, label?` | `{ok, id}` |
| `board_highlight` | `x, y, w, h` | `{ok, id}` |
| `board_clear` | — | `{ok}` |
| `board_close` | — | `{ok}` |

- All coordinates `x, y, w, h` are normalized floats in `[0, 1]` relative to the target monitor.
- A tool whose feature is disabled returns `{ok: false, error: "<feature> disabled by user"}` and performs no action.

**Setup screen connector step**
- In: user selects zero or more of: Microsoft 365 (mail, Teams, calendar), Gmail, Google Calendar, Health (Apple Health, Google Health Connect / Google Fit, others).
- Out: each selected connector is added to Claude Code at user scope; the user can skip any or all. Each one added is written to the manifest.

**Uninstall**
- In: the manifest (list of Claude Code entries and file paths setup created).
- Out: every listed entry and path is removed; entries not in the manifest are never touched. Running it twice is safe (already-removed items are skipped).

**config.yaml keys**
`wake_word_enabled`, `wake_word` (default `"jarvis"`), `hotkey`, `speak_aloud`, `show_notifications`, `screen_access`, `drawing`, `stt_model`, `silence_timeout_ms`

## Acceptance Criteria
All criteria are checked on both Windows 11 and macOS 14+.
- The Windows installer and macOS `.dmg` from GitHub Releases install and launch the app on a machine without Python.
- On first launch the setup screen appears; after it finishes, the dashboard opens, and the setup screen does not appear again on later launches.
- After setup, `claude mcp list` shows the senses server, and Claude Code can call `capture_screen`.
- After the guided connector step, `claude mcp list` shows every connector the user selected and none they skipped.
- After uninstall, `claude mcp list` no longer shows the senses server or any connector setup added, while entries the user had before install are still present; no app files, config, logs, or models remain on disk.
- On macOS, the setup screen shows the status (granted/denied) of Microphone, Screen Recording, and Accessibility.
- Pressing the hotkey starts recording within 300 ms (log timestamp).
- Saying "Jarvis" starts recording in ≥ 9 of 10 trials in a quiet room.
- Recording stops automatically after `silence_timeout_ms` of silence.
- A spoken question produces a notification containing Claude's reply text.
- With `speak_aloud: true`, the first spoken audio starts within 6 s of end of speech for a one-line question.
- A multi-sentence reply is spoken without audible cuts: the silence between consecutive sentences is no longer than a natural pause (≤ 300 ms), and no word is clipped or repeated.
- With `speak_aloud: false`, no audio is played. With `show_notifications: false`, no notification appears.
- A follow-up question ("what did I just ask?") is answered correctly, proving session continuity.
- With `screen_access: false`, `capture_screen` returns the disabled error and no image is captured.
- With `drawing: false`, every `board_*` tool returns the disabled error and no overlay appears.
- Toggling any setting in the dashboard takes effect on the next request without restarting the app.
- `board_open("glass")` shows a translucent board; `board_open("screen")` shows a fully transparent overlay. In both modes mouse clicks pass through to apps beneath.
- `board_write_latex(r"\frac{n(n+1)}{2}", 0.5, 0.5)` renders the fraction centred within ±2% of screen width/height of the given point, on a standard display and on a scaled display (Windows 150%, macOS Retina).
- `pytest` passes on both OSes for: config load/save, toggle enforcement on every MCP tool, coordinate conversion (normalized → pixels with DPI scaling), Agent Bridge event parsing, setup connector selection, and uninstall cleanup (removes only manifest entries; safe to run twice).

## Output Files
```
config.yaml
src/main.py
src/trigger.py
src/capture.py
src/agent_bridge.py
src/output.py
src/senses_server.py
src/overlay.py
src/dashboard.py
src/config.py
src/platform_support.py
src/setup_screen.py
src/uninstall.py
packaging/                  (PyInstaller spec, Windows installer script, macOS dmg script)
.github/workflows/          (CI tests; release builds)
tests/test_config.py
tests/test_toggles.py
tests/test_coords.py
tests/test_agent_bridge.py
tests/test_setup.py
tests/test_uninstall.py
README.md
```
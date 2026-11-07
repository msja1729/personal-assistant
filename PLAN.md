# PLAN.md: Personal Assistant

This plan builds the app described in [SPEC.md](SPEC.md) one component at a time. Each component is finished and tested before the next one starts. Each component also gets its own sub-plan in `plans/`, written just before work on it begins.

## How we work

**Branches**
- `feature/<component>`: the work for one component.
- `Dev`: finished and tested components only.
- `main`: public releases only.

**Per component**
1. Write its sub-plan (`plans/NN-name.md`), and get it approved.
2. Create the branch `feature/<component>` from `Dev`.
3. Build it, with tests.
4. Open a pull request into `Dev`. CI runs the tests on Windows and macOS.
5. Go through the component's manual checklist, then merge.

A component is done when:
- its sub-plan's acceptance checks pass;
- `pytest` passes on Windows and macOS in CI;
- its manual checklist has been ticked by you.

## Build order

| # | Component (from SPEC) | What you can do when it's finished | Sub-plan |
|---|---|---|---|
| 0 | **Project setup** | Repo layout, `config.py`, `platform_support.py` skeleton, pytest, ruff, CI. Nothing visible yet | `plans/00-setup.md` |
| 1 | **Overlay (pen)** ← *we start here* | Run a demo script and watch text, LaTeX, arrows, shapes and highlights being handwritten on a glass board or over the screen | `plans/01-overlay.md` |
| 2 | **Senses MCP server** | Claude Code itself can take a screenshot and draw on your screen (typed requests in a Claude Code chat) | `plans/02-senses.md` |
| 3 | **Agent Bridge** | The app sends a typed question to Claude Code and streams the reply back, remembering the conversation | `plans/03-agent-bridge.md` |
| 4 | **Output** | Replies appear in the glass notification card and are spoken smoothly (≤ 300 ms between sentences) | `plans/04-output.md` |
| 5 | **Trigger + Capture/STT** | Say "Jarvis" or press the hotkey, ask out loud, and get a spoken answer: the full voice loop | `plans/05-voice.md` |
| 6 | **Dashboard** | Turn every feature on or off from a settings window, without restarting | `plans/06-dashboard.md` |
| 7 | **Setup screen + Uninstall** | The first time the app opens, a setup screen checks Claude Code, adds the senses, asks for macOS permissions and guides connectors, then opens the dashboard. An "Uninstall" button removes everything | `plans/07-setup.md` |
| 8 | **Installers + Release** | GitHub Actions builds `Setup.exe` (Windows) and `.dmg` (macOS); download links on the GitHub Releases page; beta, then public `v1.0.0` | `plans/08-release.md` |

`config.py` holds every setting from SPEC from step 0 onwards. `platform_support.py` starts nearly empty, and each component adds only the OS-specific code it needs.

## Why this order

1. **Overlay first.** It is the most visual part and can be tested on its own, with no Claude, microphone or installer.
2. **Senses next.** It plugs the overlay into Claude, which shows the core idea working early.
3. **Agent Bridge and Output.** These make the app a usable typed assistant before any voice work.
4. **Voice after that.** It is the hardest to test, so it comes once everything behind it already works.
5. **Dashboard and setup screen last.** They wrap the finished parts, and the installers and release come after them.

## What the user experiences (after step 8)

1. Download the installer from the GitHub Releases page.
2. Install it: on Windows, run `Setup.exe`; on macOS, drag the app into Applications. Python is bundled, so nothing else is needed except Claude Code.
3. **First launch opens the Setup screen:**
   1. Check that Claude Code is installed and signed in, with a link to install it if not.
   2. Add the senses (screen + pen) to Claude Code.
   3. On macOS, ask for Microphone, Screen Recording and Accessibility.
   4. Connect mail, Teams, calendar and health (optional).
4. When setup is done, the Dashboard opens. From then on the app runs in the background (tray or menu bar).
5. To remove it, use **Uninstall** in the dashboard, or on Windows also *Settings → Apps*. Either way, the senses entry and all app files are cleaned up.

## Milestones

| Milestone | After component | Tag |
|---|---|---|
| Claude can draw | 2 | `v0.1.0` (pre-release, on Dev) |
| Typed assistant | 4 | `v0.2.0` (pre-release) |
| Voice assistant | 5 | `v0.3.0` (pre-release) |
| Feature complete | 7 | — (run from source) |
| Installers built | 8 | `v1.0.0-beta.1` (beta testers download from Releases) |
| Public release | 8 | `v1.0.0` (merged to `main`, repo public) |

## Lessons from the first build (keep in mind)

- **Windows app folder:** use `~/.personal-assistant`, not `AppData`. Microsoft Store Python secretly redirects AppData writes, so Claude Code can't find the files.
- **MCP library:** pin `mcp<2`. Version 2 renamed its server API.
- **Seamless speech:** OS voices add about 900 ms of silence after each sentence. Render each sentence to memory, trim the silence, and play everything through one audio stream.
- **Hotkey:** pynput's built-in hotkey matcher misses combinations with Space. Use our own matcher with virtual-key codes.
- **LaTeX:** render on a transparent background (matplotlib `savefig(transparent=True)`).
- **Uninstall:** stop running `senses_server.py` processes first. Open Claude Code sessions keep it running.
- **Claude-account connectors:** our app cannot connect or remove them. It can only guide the user to claude.ai and check the result.

# Step 0: Project setup

**Component:** none yet. This step builds the base every component sits on.
**Branch:** `feature/setup` → pull request into `Dev`

## Goal
A clean, tested project skeleton that checks itself on Windows and macOS on every push.

## What gets built

| File | What it is |
|---|---|
| `pyproject.toml` | Project name, version, Python 3.11, libraries, and the settings for pytest, ruff and mypy |
| `src/__init__.py` | App name and version |
| `src/config.py` | Where the app folder is; the `Config` settings with every key from SPEC and its default; safe load and save of `config.yaml` |
| `src/platform_support.py` | `IS_WINDOWS` / `IS_MAC`, and a check that refuses to run on other systems. Filled in by later steps |
| `config.yaml` | The default settings, matching `Config` |
| `tests/conftest.py` | Gives every test its own temporary app folder, so tests never touch your real settings |
| `tests/test_config.py` | Settings tests |
| `tests/test_platform.py` | Platform-module tests |
| `.github/workflows/ci.yml` | On every push and pull request: ruff, mypy and pytest on Windows and macOS |
| `.gitignore`, `README.md`, `CHANGELOG.md` | Basics |

## Decisions
- **App folder:**
  - Windows: `~/.personal-assistant`. Not under `AppData`, because Microsoft Store Python secretly redirects writes there.
  - macOS: `~/Library/Application Support/PersonalAssistant`.
  - The `PA_HOME` environment variable overrides it; tests use this.
- **Bad values:** a broken or partly wrong `config.yaml` never crashes the app. Bad values fall back to their defaults.
- **Atomic saves:** saving writes a temporary file and then swaps it in, so another process never reads a half-written file.
- **Version:** starts at `0.0.1`.

## Acceptance checks
1. `pytest` passes locally.
2. `ruff check .` and `mypy src` report no problems.
3. The CI run on the pull request is green on Windows and macOS.
4. Tests prove:
   - saving and then loading settings gives back the same settings;
   - a missing file gives the defaults;
   - a broken file gives the defaults;
   - wrong values fall back to their defaults;
   - the defaults in `config.yaml` equal the defaults in code;
   - the app folder is outside `AppData` on Windows;
   - `PA_HOME` overrides the app folder.

## Your manual checklist
- [ ] The folder layout looks right to you.
- [ ] On GitHub, the pull request shows two green ticks (Windows and macOS).

## Not in this step
Anything visible: windows, drawing, Claude, voice. Those start in step 1.

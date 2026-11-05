"""Installer for Personal Assistant (Windows 11 / macOS 14+).

Run with Python 3.11 or newer:   python install.py

Steps:
 1. Check Claude Code is installed.
 2. Copy the app into its own folder and create a private Python environment.
 3. Write the default config.yaml and download the speech and wake-word models.
 4. Register the senses MCP server into Claude Code (user scope).
 5. macOS: request Microphone, Screen Recording and Accessibility and report the result.
 6. Guide the user through connecting mail, Teams, calendar and health connectors.
 7. Add a Start-menu / login entry and start the app.

Everything added is written to install_manifest.json, which uninstall.py uses
to remove exactly what was added — and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from src import APP_NAME, __version__, config  # noqa: E402
from src import claude_settings as cs  # noqa: E402
from src import platform_support as ps  # noqa: E402
from src.agent_bridge import find_claude  # noqa: E402

COPY_ITEMS = ("src", "uninstall.py", "requirements.txt", "README.md", "config.yaml", "LICENSE")


# ---------------------------------------------------------------- manifest


class Manifest:
    """Record of everything the installer added. Saved after every step."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "version": 1,
            "app_version": __version__,
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "app_home": str(path.parent),
            "mcp_servers": [],
            "connectors": [],
            "paths": [],
            "mac_bundle_id": None,
        }
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                for key in ("mcp_servers", "connectors", "paths"):
                    self.data[key] = old.get(key, [])
                self.data["mac_bundle_id"] = old.get("mac_bundle_id")
            except (OSError, json.JSONDecodeError):
                pass

    def add_path(self, path: Path) -> None:
        value = str(path)
        if value not in self.data["paths"]:
            self.data["paths"].append(value)
        self.save()

    def add_server(self, name: str, scope: str = "user") -> None:
        entry = {"name": name, "scope": scope}
        if entry not in self.data["mcp_servers"]:
            self.data["mcp_servers"].append(entry)
        self.save()

    def add_connector(self, name: str) -> None:
        entry = {"name": name, "kind": "claude_ai"}
        if entry not in self.data["connectors"]:
            self.data["connectors"].append(entry)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


# ----------------------------------------------------------------- helpers


def say(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, text: str) -> None:
    say(f"\n[{n}/7] {text}")


def ask(question: str, default: str, assume_yes: bool) -> str:
    if assume_yes or not sys.stdin or not sys.stdin.isatty():
        return default
    try:
        answer = input(f"{question} ").strip()
    except EOFError:
        return default
    return answer or default


def yes(question: str, default: bool, assume_yes: bool) -> bool:
    answer = ask(f"{question} [{'Y/n' if default else 'y/N'}]", "y" if default else "n", assume_yes)
    return answer.lower().startswith("y")


def venv_python(home: Path, windowed: bool = False) -> Path:
    if ps.IS_WINDOWS:
        return home / "venv" / "Scripts" / ("pythonw.exe" if windowed else "python.exe")
    return home / "venv" / "bin" / "python3"


def run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


# ------------------------------------------------------------------- steps


def preflight() -> str:
    if sys.version_info < (3, 11):
        sys.exit(f"Python 3.11 or newer is needed (this is {sys.version.split()[0]}).")
    if not (ps.IS_WINDOWS or ps.IS_MAC):
        sys.exit("Personal Assistant supports Windows 11 and macOS 14 or later.")
    claude = find_claude()
    if claude is None:
        sys.exit(
            "Claude Code was not found. Install it first (https://claude.com/claude-code),\n"
            "sign in once by running `claude`, then run this installer again."
        )
    version = cs.default_runner(claude)(["--version"])
    say(f"  Claude Code: {(version.stdout or '').strip() or claude}")
    return claude


def copy_app(home: Path, manifest: Manifest) -> Path:
    app_dir = home / "app"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True)
    for name in COPY_ITEMS:
        src = HERE / name
        if src.is_dir():
            shutil.copytree(src, app_dir / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif src.exists():
            shutil.copy2(src, app_dir / name)
    say(f"  App copied to {app_dir}")
    return app_dir


def make_venv(home: Path, app_dir: Path) -> None:
    py = venv_python(home)
    if not py.exists():
        say("  Creating a private Python environment...")
        run([sys.executable, "-m", "venv", str(home / "venv")])
    say("  Installing libraries (this can take a few minutes the first time)...")
    run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"])
    run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(app_dir / "requirements.txt")])


def register_senses(run_claude: cs.Runner, home: Path, app_dir: Path, manifest: Manifest) -> bool:
    servers = cs.list_servers(run_claude)
    existing = servers.get(cs.SENSES_NAME)
    if existing is not None:
        if not cs.is_our_server(existing, home):
            say(f"  ! An MCP server named '{cs.SENSES_NAME}' already exists and is not ours; leaving it alone.")
            return False
        cs.remove_server(run_claude, cs.SENSES_NAME)
    result = cs.add_senses_server(run_claude, venv_python(home), app_dir / "src" / "senses_server.py")
    if result.returncode != 0:
        say(f"  ! Could not register the senses server: {(result.stderr or result.stdout).strip()}")
        return False
    manifest.add_server(cs.SENSES_NAME)
    ok = cs.SENSES_NAME in cs.list_servers(run_claude)
    say("  Senses server registered in Claude Code." if ok else "  ! Senses server not visible in `claude mcp list`.")
    return ok


def mac_permissions(manifest: Manifest, home: Path) -> None:
    if not ps.IS_MAC:
        say("  Not needed on Windows (Windows asks for the microphone the first time it is used).")
        return
    say("  macOS will now ask for Microphone, Screen Recording and Accessibility access.")
    code = (
        "import json; from src import platform_support as p; "
        "print(json.dumps({'status': p.request_mac_permissions(), 'bundle': p.mac_bundle_id()}))"
    )
    result = subprocess.run(
        [str(venv_python(home)), "-c", code], cwd=home / "app", capture_output=True, text=True, timeout=400
    )
    try:
        info = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        say(f"  ! Could not check permissions: {result.stderr.strip()[-300:]}")
        return
    manifest.data["mac_bundle_id"] = info["bundle"]
    manifest.save()
    for name in ps.MAC_PERMISSIONS:
        state = info["status"].get(name, "unknown")
        mark = "granted" if state == "granted" else f"DENIED ({state}) — enable it in System Settings > Privacy & Security"
        say(f"  {name:<17} {mark}")


def connectors_step(run_claude: cs.Runner, manifest: Manifest, choice: str | None, assume_yes: bool) -> None:
    say("  Claude can use your mail, Teams, calendar and health data through Claude's own connectors.")
    say("  They are connected to your Claude account, so Claude Code picks them up automatically.\n")
    for i, c in enumerate(cs.CONNECTORS, 1):
        say(f"    {i}. {c.label}")
    while True:
        answer = choice if choice is not None else ask(
            "\n  Which ones do you want to connect? (e.g. 1,3 or all; Enter to skip)", "", assume_yes
        )
        try:
            selected = cs.select_connectors(answer)
            break
        except ValueError as exc:
            if choice is not None:
                sys.exit(f"--connectors: {exc}")
            say(f"  {exc}")
    if not selected:
        say("  Skipped. You can connect them later from the dashboard (Connectors...).")
        return
    for c in selected:
        before = cs.list_servers(run_claude)
        already = [n for n in before if c.matches(n)]
        if already:
            say(f"  ✓ {c.label}: already connected ({', '.join(already)}); left as it is.")
            continue
        say(f"\n  → {c.label}")
        say(f"    Opening {cs.CONNECTORS_PAGE} — click Connect for it and sign in.")
        if c.hint:
            say(f"    {c.hint}")
        ps.open_url(cs.CONNECTORS_PAGE)
        added: list[str] = []
        for _attempt in range(3):
            ask("    Press Enter when you have connected it (or to skip)...", "", assume_yes)
            added = cs.newly_connected(c, before, cs.list_servers(run_claude))
            if added or assume_yes:
                break
            say("    Not visible in Claude Code yet. It can take a moment; try again.")
        if added:
            for name in added:
                manifest.add_connector(name)
            say(f"    ✓ Connected: {', '.join(added)}")
        else:
            say("    Skipped (not connected).")


def shortcuts_step(home: Path, app_dir: Path, manifest: Manifest, autostart: bool) -> None:
    main_script = app_dir / "src" / "main.py"
    if ps.IS_WINDOWS:
        menu = ps.start_menu_path()
        assert menu is not None
        ps.create_windows_shortcut(menu, venv_python(home, windowed=True), f'"{main_script}" --dashboard', app_dir, APP_NAME)
        manifest.add_path(menu)
        uninstall_link = menu.with_name(f"Uninstall {APP_NAME}.lnk")
        base_python = Path(getattr(sys, "_base_executable", sys.executable))
        ps.create_windows_shortcut(uninstall_link, base_python, f'"{app_dir / "uninstall.py"}"', Path.home(), f"Uninstall {APP_NAME}")
        manifest.add_path(uninstall_link)
        say(f"  Start-menu entries added: '{APP_NAME}' and 'Uninstall {APP_NAME}'.")
        if autostart:
            link = ps.autostart_path()
            ps.create_windows_shortcut(link, venv_python(home, windowed=True), f'"{main_script}"', app_dir, APP_NAME)
            manifest.add_path(link)
            say("  Starts automatically when you sign in.")
    elif autostart:
        plist = ps.autostart_path()
        ps.write_mac_launch_agent(plist, venv_python(home), main_script, home / "logs" / "launchd.log")
        manifest.add_path(plist)
        say("  Starts automatically when you log in.")


def launch(home: Path, app_dir: Path) -> None:
    main_script = app_dir / "src" / "main.py"
    flags = 0
    if ps.IS_WINDOWS:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(
        [str(venv_python(home, windowed=True)), str(main_script), "--dashboard"],
        cwd=app_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=not ps.IS_WINDOWS,
    )


# -------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Install {APP_NAME}")
    parser.add_argument("--yes", action="store_true", help="accept defaults without asking")
    parser.add_argument("--connectors", help="connectors to set up, e.g. 'gmail,gcal', 'all' or 'none'")
    parser.add_argument("--no-autostart", action="store_true", help="do not start at login")
    parser.add_argument("--no-launch", action="store_true", help="do not start the app at the end")
    parser.add_argument("--skip-models", action="store_true", help="do not download models now")
    parser.add_argument("--stage2", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    home = config.app_home()
    if not args.stage2:
        # Stage 1 runs on the user's own Python with the standard library only.
        say(f"{APP_NAME} {__version__} installer")
        say("=" * 40)
        step(1, "Checking Claude Code")
        preflight()
        home.mkdir(parents=True, exist_ok=True)
        manifest = Manifest(config.manifest_path())
        manifest.add_path(home)  # the whole app folder: app, venv, config, logs, models
        step(2, "Installing the app")
        if stop_running_app():
            say("  Stopped the running app.")
        app_dir = copy_app(home, manifest)
        make_venv(home, app_dir)
        # Stage 2 continues inside the new environment, where all libraries exist.
        forwarded = [a for a in (argv if argv is not None else sys.argv[1:])]
        return subprocess.call([str(venv_python(home)), str(Path(__file__).resolve()), "--stage2", *forwarded])

    claude = find_claude()
    assert claude is not None
    run_claude = cs.default_runner(claude)
    manifest = Manifest(config.manifest_path())
    app_dir = home / "app"

    step(3, "Settings and models")
    config.ensure_default()
    say(f"  Settings: {config.config_path()}")
    if not args.skip_models:
        run([str(venv_python(home)), str(app_dir / "src" / "main.py"), "--download-models"], cwd=app_dir)

    step(4, "Adding the senses (screen + pen) to Claude Code")
    register_senses(run_claude, home, app_dir, manifest)
    # Claude Code keeps session history for our workspace here; remove it on uninstall.
    manifest.data["claude_workspace"] = str(config.workspace_dir())
    manifest.save()

    step(5, "Permissions")
    mac_permissions(manifest, home)

    step(6, "Connectors (optional)")
    connectors_step(run_claude, manifest, args.connectors, args.yes)

    step(7, "Finishing")
    autostart = not args.no_autostart and yes("  Start Personal Assistant automatically when you log in?", True, args.yes)
    shortcuts_step(home, app_dir, manifest, autostart)
    if not args.no_launch:
        launch(home, app_dir)
        say("  Personal Assistant is running (look for its icon in the system tray / menu bar).")

    cfg = config.load()
    say("\nDone! Say \"" + cfg.wake_word.capitalize() + f"\" or press {cfg.hotkey} and ask anything.")
    say(f"To remove everything later, run: python \"{app_dir / 'uninstall.py'}\"")
    return 0


def stop_running_app() -> bool:
    """Stop a running copy so its files can be replaced."""
    try:
        from src import ipc

        if ipc.app_is_running():
            ipc.send("quit")
            time.sleep(1.5)
            return True
    except Exception:
        pass
    return False


if __name__ == "__main__":
    os.chdir(Path.home())
    sys.exit(main())

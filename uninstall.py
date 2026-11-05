"""Uninstaller for Personal Assistant.

Run:   python uninstall.py        (add --yes to skip questions)

Reads install_manifest.json and removes exactly what the installer added:
the senses MCP server in Claude Code, the start-menu / login entries, Claude
Code's session history for the app's workspace, and the app folder (app,
private Python environment, config.yaml, logs, models). On macOS it also
resets the Microphone, Screen Recording and Accessibility permissions.
Claude Code entries that are not in the manifest are never touched. Running
it twice is safe: anything already gone is skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from src import APP_NAME, config  # noqa: E402
from src import claude_settings as cs  # noqa: E402
from src import platform_support as ps  # noqa: E402


@dataclass(frozen=True)
class Action:
    kind: str  # "remove_server" | "connector" | "reset_permissions" | "delete"
    target: str
    scope: str = "user"


def load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def plan_uninstall(manifest: dict[str, Any], present_servers: dict[str, str]) -> list[Action]:
    """Work out what to remove. Only manifest entries are ever included."""
    actions: list[Action] = []
    app_home = Path(manifest.get("app_home") or config.app_home())
    for entry in manifest.get("mcp_servers", []):
        name = entry.get("name")
        target = present_servers.get(name)
        # Remove it only if it still exists and still points at our app folder.
        if name and target is not None and cs.is_our_server(target, app_home):
            actions.append(Action("remove_server", name, entry.get("scope", "user")))
    for entry in manifest.get("connectors", []):
        name = entry.get("name")
        if name and name in present_servers:
            actions.append(Action("connector", name))
    if manifest.get("mac_bundle_id"):
        actions.append(Action("reset_permissions", str(manifest["mac_bundle_id"])))
    workspace = manifest.get("claude_workspace")
    extra_paths = [str(p) for p in cs.claude_project_dirs(Path(workspace))] if workspace else []
    paths = [p for p in manifest.get("paths", []) if p != str(app_home)] + extra_paths
    for p in paths:
        if os.path.lexists(p):
            actions.append(Action("delete", p))
    if app_home.exists():
        actions.append(Action("delete", str(app_home)))  # last: it holds the manifest
    return actions


def _on_rm_error(func: Callable[..., Any], path: str, _exc: Any) -> None:
    # Clear read-only flags (git/pip files on Windows) and retry once.
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _in_appdata(path: Path) -> bool:
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base and str(path).lower().startswith(base.lower()):
            return True
    return False


def delete_path(path: Path) -> bool:
    if ps.IS_WINDOWS and _in_appdata(path):
        # Microsoft Store Python only "hides" AppData files from itself;
        # let PowerShell (an ordinary process) really delete them.
        literal = str(path).replace("'", "''")
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Remove-Item -LiteralPath '{literal}' -Recurse -Force"],
            capture_output=True,
            creationflags=ps.NO_WINDOW,
        )
        check = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Test-Path -LiteralPath '{literal}'"],
            capture_output=True, text=True, creationflags=ps.NO_WINDOW,
        )
        return check.stdout.strip().lower() == "false"
    if path.is_dir() and not path.is_symlink():
        for _ in range(3):
            shutil.rmtree(path, onerror=_on_rm_error)
            if not path.exists():
                return True
            time.sleep(1)  # files may still be closing after the app stopped
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return not os.path.lexists(path)


def execute(
    actions: list[Action],
    run_claude: cs.Runner | None,
    disconnect_connectors: bool,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Carry out the plan; return a list of problems (empty when everything worked)."""
    problems: list[str] = []
    connectors = [a.target for a in actions if a.kind == "connector"]
    for action in actions:
        if action.kind == "remove_server":
            if run_claude is None:
                problems.append(f"Claude Code not found; could not remove MCP server '{action.target}'")
                continue
            result = cs.remove_server(run_claude, action.target, action.scope)
            if result.returncode == 0:
                log(f"  Removed '{action.target}' from Claude Code.")
            else:
                problems.append(f"could not remove '{action.target}': {(result.stderr or result.stdout).strip()}")
        elif action.kind == "reset_permissions" and ps.IS_MAC:
            done = ps.reset_mac_permissions(action.target)
            log(f"  Reset macOS permissions: {', '.join(done) or 'none'}.")
        elif action.kind == "delete":
            if ps.IS_MAC and action.target.endswith(".plist"):
                subprocess.run(["launchctl", "unload", action.target], capture_output=True)
            if delete_path(Path(action.target)):
                log(f"  Deleted {action.target}")
            else:
                problems.append(f"could not delete {action.target} (is the app still running?)")
    if connectors:
        log("\n  These connectors were connected during install and live in your Claude account:")
        for name in connectors:
            log(f"    - {name}")
        if disconnect_connectors:
            log(f"  Opening {cs.CONNECTORS_PAGE} — click Disconnect on each one.")
            ps.open_url(cs.CONNECTORS_PAGE)
        else:
            log(f"  They were kept. To remove them, disconnect them at {cs.CONNECTORS_PAGE}.")
    return problems


def stop_app() -> None:
    try:
        from src import ipc

        if ipc.app_is_running():
            ipc.send("quit")
            print("  Stopped the running app.")
            time.sleep(2)
    except Exception:
        pass


def guess_manifest(home: Path) -> dict[str, Any] | None:
    """If the manifest is missing but the app folder exists, remove what is clearly ours."""
    if not home.exists():
        return None
    paths = [str(ps.autostart_path())]
    menu = ps.start_menu_path()
    if menu is not None:
        paths += [str(menu), str(menu.with_name(f"Uninstall {APP_NAME}.lnk"))]
    return {
        "app_home": str(home),
        "mcp_servers": [{"name": cs.SENSES_NAME, "scope": "user"}],
        "connectors": [],
        "paths": paths,
        "claude_workspace": str(home / "workspace"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Uninstall {APP_NAME}")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--disconnect-connectors", action="store_true", help="also open Claude's page to disconnect them")
    args = parser.parse_args(argv)

    home = config.app_home()
    # Never run from inside the folder we are about to delete.
    if Path(sys.prefix).resolve().is_relative_to(home.resolve()):
        base = getattr(sys, "_base_executable", None)
        if base and Path(base).exists() and not Path(base).resolve().is_relative_to(home.resolve()):
            return subprocess.call([base, str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])], cwd=Path.home())
    os.chdir(Path.home())

    manifest = load_manifest(config.manifest_path()) or guess_manifest(home)
    if manifest is None:
        print(f"{APP_NAME} is not installed (nothing to remove).")
        return 0

    print(f"Uninstall {APP_NAME}")
    print(f"This removes the app, its settings, logs and models ({home}),")
    print("the senses server from Claude Code, and the start-menu / login entries.")
    if not args.yes and sys.stdin and sys.stdin.isatty():
        if not input("Continue? [y/N] ").strip().lower().startswith("y"):
            print("Cancelled.")
            return 1

    stop_app()
    claude = None
    try:
        from src.agent_bridge import find_claude

        claude = find_claude()
    except Exception:
        pass
    run_claude = cs.default_runner(claude) if claude else None
    present = cs.list_servers(run_claude) if run_claude else {}

    disconnect = args.disconnect_connectors
    if manifest.get("connectors") and not args.yes and not disconnect and sys.stdin and sys.stdin.isatty():
        disconnect = input(
            "Also disconnect the connectors added during install from your Claude account? [y/N] "
        ).strip().lower().startswith("y")

    actions = plan_uninstall(manifest, present)
    problems = execute(actions, run_claude, disconnect)

    if run_claude:
        left = [a.target for a in actions if a.kind == "remove_server" and a.target in cs.list_servers(run_claude)]
        problems += [f"'{n}' is still listed by `claude mcp list`" for n in left]
    if problems:
        print("\nFinished with problems:")
        for p in problems:
            print(f"  ! {p}")
        print("Close the app and run the uninstaller again; it skips what is already gone.")
        return 2
    print(f"\n{APP_NAME} has been removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

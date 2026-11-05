"""Reading and changing Claude Code's MCP settings (used by install and uninstall).

All changes go through the ``claude mcp`` command, never by editing Claude's
files directly.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from src.platform_support import NO_WINDOW

SENSES_NAME = "senses"
CONNECTORS_PAGE = "https://claude.ai/settings/connectors"


@dataclass(frozen=True)
class Connector:
    key: str
    label: str
    claude_name: str | None  # exact name as `claude mcp list` shows it, if fixed
    match: tuple[str, ...] = ()  # otherwise: words that identify it in the name
    hint: str = ""

    def matches(self, name: str) -> bool:
        if self.claude_name is not None:
            return name.lower() == self.claude_name.lower()
        lowered = name.lower()
        return lowered.startswith("claude.ai ") and any(word in lowered for word in self.match)


CONNECTORS: tuple[Connector, ...] = (
    Connector("m365", "Microsoft 365 (Outlook mail, Teams, calendar)", "claude.ai Microsoft 365"),
    Connector("gmail", "Gmail", "claude.ai Gmail"),
    Connector("gcal", "Google Calendar", "claude.ai Google Calendar"),
    Connector(
        "health",
        "Health (Apple Health, Google Health Connect / Google Fit, Samsung Health, Fitbit, Garmin, others)",
        None,
        match=("health", "fit", "garmin", "oura", "whoop", "withings", "strava", "polar"),
        hint="In the connector directory, search for the app your phone or watch syncs to "
        "(Apple Health on iPhone, Health Connect / Google Fit on Android, or Samsung Health, "
        "Fitbit, Garmin and so on) and connect it.",
    ),
)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]

_STATUS = re.compile(r"^\W*\s*(connected|failed|needs|disabled|pending|connecting|error)|^[^\w\s]", re.I)


def default_runner(claude: str) -> Runner:
    def run(args: Sequence[str]) -> subprocess.CompletedProcess:
        cmd = [claude, *args]
        if claude.lower().endswith(".cmd"):
            cmd = ["cmd", "/c", *cmd]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=NO_WINDOW,
        )

    return run


def parse_mcp_list(output: str) -> dict[str, str]:
    """Parse ``claude mcp list`` output into {server name: command or URL}."""
    servers: dict[str, str] = {}
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith(("checking", "no mcp servers")):
            continue
        # Windows paths contain "C:\", so split on the first ": " only.
        if ": " not in line:
            continue
        name, rest = line.split(": ", 1)
        target = rest
        if " - " in rest:
            head, tail = rest.rsplit(" - ", 1)
            if _STATUS.search(tail):  # drop the trailing health status
                target = head
        servers[name.strip()] = target.strip()
    return servers


def list_servers(run: Runner) -> dict[str, str]:
    result = run(["mcp", "list"])
    return parse_mcp_list(result.stdout or "")


def add_senses_server(run: Runner, python: Path, script: Path) -> subprocess.CompletedProcess:
    return run(["mcp", "add", "--scope", "user", SENSES_NAME, "--", str(python), str(script)])


def remove_server(run: Runner, name: str, scope: str = "user") -> subprocess.CompletedProcess:
    return run(["mcp", "remove", name, "--scope", scope])


def is_our_server(target: str, app_home: Path) -> bool:
    """True if a server's command points into our app folder."""
    return str(app_home).lower().replace("\\", "/") in target.lower().replace("\\", "/")


def select_connectors(answer: str, catalog: Sequence[Connector] = CONNECTORS) -> list[Connector]:
    """Turn the user's answer ("1,3", "gmail health", "all", "" = skip) into connectors."""
    answer = answer.strip().lower()
    if answer in ("", "none", "skip", "n", "no", "0"):
        return []
    if answer in ("all", "a", "*"):
        return list(catalog)
    chosen: list[Connector] = []
    for token in re.split(r"[\s,;]+", answer):
        if not token:
            continue
        if token.isdigit():
            index = int(token) - 1
            if not 0 <= index < len(catalog):
                raise ValueError(f"there is no option {token}")
            item = catalog[index]
        else:
            found = [c for c in catalog if c.key == token]
            if not found:
                raise ValueError(f"unknown connector {token!r}")
            item = found[0]
        if item not in chosen:
            chosen.append(item)
    return chosen


def newly_connected(connector: Connector, before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Server names for ``connector`` that appeared between two ``claude mcp list`` snapshots."""
    return [name for name in after if name not in before and connector.matches(name)]


def claude_project_slug(path: Path | str) -> str:
    """Folder name Claude Code uses under ~/.claude/projects for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def claude_project_dirs(workspace: Path, claude_home: Path | None = None) -> list[Path]:
    """Claude Code's session folders for our workspace (case-insensitive match)."""
    projects = (claude_home or Path.home() / ".claude") / "projects"
    if not projects.is_dir():
        return []
    slug = claude_project_slug(workspace).lower()
    return [p for p in projects.iterdir() if p.is_dir() and p.name.lower() == slug]

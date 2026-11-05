import json
import subprocess
from pathlib import Path

import pytest

import install
from src import claude_settings as cs

MCP_LIST = """Checking MCP server health…

claude.ai Claude Docs: https://api.anthropic.com/v1/pages/mcp - ⊘ Disabled for this project (re-enable via /mcp)
claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected
claude.ai Microsoft 365: https://microsoft365.mcp.claude.com/mcp - ! Needs authentication
senses: C:\\Users\\me\\AppData\\Local\\PersonalAssistant\\venv\\Scripts\\python.exe C:\\Users\\me\\AppData\\Local\\PersonalAssistant\\app\\src\\senses_server.py - ✔ Connected
github: npx -y @modelcontextprotocol/server-github - ✗ Failed to connect
"""


def test_parse_mcp_list():
    servers = cs.parse_mcp_list(MCP_LIST)
    assert set(servers) == {"claude.ai Claude Docs", "claude.ai Gmail", "claude.ai Microsoft 365", "senses", "github"}
    assert servers["claude.ai Claude Docs"] == "https://api.anthropic.com/v1/pages/mcp"
    assert servers["claude.ai Gmail"] == "https://gmailmcp.googleapis.com/mcp/v1"
    assert servers["senses"].endswith("senses_server.py")
    assert servers["github"] == "npx -y @modelcontextprotocol/server-github"


def test_parse_empty_list():
    assert cs.parse_mcp_list("No MCP servers configured. Use `claude mcp add` to add a server.") == {}


@pytest.mark.parametrize(
    "answer, keys",
    [
        ("", []),
        ("skip", []),
        ("all", ["m365", "gmail", "gcal", "health"]),
        ("1,3", ["m365", "gcal"]),
        ("2 2 4", ["gmail", "health"]),
        ("gmail, health", ["gmail", "health"]),
    ],
)
def test_select_connectors(answer, keys):
    assert [c.key for c in cs.select_connectors(answer)] == keys


@pytest.mark.parametrize("answer", ["9", "spotify", "0,1"])
def test_select_connectors_rejects_unknown(answer):
    with pytest.raises(ValueError):
        cs.select_connectors(answer)


def test_connector_detection_only_new_entries():
    gmail = cs.CONNECTORS[1]
    health = cs.CONNECTORS[3]
    before = {"claude.ai Gmail": "x"}
    after = {"claude.ai Gmail": "x", "claude.ai Google Calendar": "y", "claude.ai Fitbit": "z"}
    assert cs.newly_connected(gmail, before, after) == []  # was already there: not ours
    assert cs.newly_connected(health, before, after) == ["claude.ai Fitbit"]
    assert cs.newly_connected(cs.CONNECTORS[2], before, after) == ["claude.ai Google Calendar"]


def test_health_connector_needs_claude_ai_prefix():
    health = cs.CONNECTORS[3]
    assert health.matches("claude.ai Apple Health")
    assert not health.matches("my-local-health-server")


class FakeClaude:
    """Stands in for the `claude` command."""

    def __init__(self, servers=None):
        self.servers = dict(servers or {})
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        out, code = "", 0
        if args[:2] == ["mcp", "list"]:
            out = "\n".join(f"{n}: {t} - ✔ Connected" for n, t in self.servers.items())
        elif args[:2] == ["mcp", "add"]:
            name = args[4]
            self.servers[name] = " ".join(args[args.index("--") + 1:])
        elif args[:2] == ["mcp", "remove"]:
            code = 0 if self.servers.pop(args[2], None) is not None else 1
        return subprocess.CompletedProcess(args, code, out, "")


def test_register_senses_records_manifest(app_home):
    fake = FakeClaude({"github": "npx server-github"})
    manifest = install.Manifest(app_home / "install_manifest.json")
    assert install.register_senses(fake, app_home, app_home / "app", manifest)
    assert "senses" in fake.servers
    assert str(app_home) in fake.servers["senses"]
    assert ["mcp", "add", "--scope", "user", "senses"] == fake.calls[1][:5]
    saved = json.loads((app_home / "install_manifest.json").read_text())
    assert saved["mcp_servers"] == [{"name": "senses", "scope": "user"}]


def test_register_senses_leaves_foreign_server(app_home):
    fake = FakeClaude({"senses": "/somewhere/else/server.py"})
    manifest = install.Manifest(app_home / "install_manifest.json")
    assert not install.register_senses(fake, app_home, app_home / "app", manifest)
    assert fake.servers["senses"] == "/somewhere/else/server.py"
    assert manifest.data["mcp_servers"] == []


def test_reinstall_replaces_our_own_server(app_home):
    fake = FakeClaude({"senses": f"{app_home}/venv/python old.py"})
    manifest = install.Manifest(app_home / "install_manifest.json")
    assert install.register_senses(fake, app_home, app_home / "app", manifest)
    assert "senses_server.py" in fake.servers["senses"]


def test_connector_step_records_only_selected_and_connected(app_home, monkeypatch):
    fake = FakeClaude({"claude.ai Gmail": "https://gmail"})
    manifest = install.Manifest(app_home / "install_manifest.json")
    monkeypatch.setattr(install.ps, "open_url", lambda url: fake.servers.setdefault("claude.ai Google Calendar", "https://cal"))
    install.connectors_step(fake, manifest, "gmail,gcal", assume_yes=True)
    # Gmail was connected before install, so it is not ours to remove later.
    assert manifest.data["connectors"] == [{"name": "claude.ai Google Calendar", "kind": "claude_ai"}]


def test_connector_step_skip(app_home):
    fake = FakeClaude()
    manifest = install.Manifest(app_home / "install_manifest.json")
    install.connectors_step(fake, manifest, "", assume_yes=True)
    assert manifest.data["connectors"] == []


def test_manifest_keeps_earlier_entries(app_home):
    path = app_home / "install_manifest.json"
    m = install.Manifest(path)
    m.add_server("senses")
    m.add_path(Path("/x/y"))
    m2 = install.Manifest(path)
    assert m2.data["mcp_servers"] == [{"name": "senses", "scope": "user"}]
    assert m2.data["paths"] == [str(Path("/x/y"))]


def test_claude_project_slug():
    assert cs.claude_project_slug(r"d:\Academics\AIDS\Personal Assistant") == "d--Academics-AIDS-Personal-Assistant"

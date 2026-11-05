import json

import uninstall
from src import claude_settings as cs
from tests.test_installer import FakeClaude


def make_install(app_home, tmp_path):
    """A fake finished install: app folder, a shortcut, a Claude session folder."""
    (app_home / "app" / "src").mkdir(parents=True)
    (app_home / "config.yaml").write_text("drawing: true\n")
    (app_home / "models").mkdir()
    (app_home / "logs").mkdir()
    shortcut = tmp_path / "Startup" / "Personal Assistant.lnk"
    shortcut.parent.mkdir()
    shortcut.write_text("lnk")
    manifest = {
        "version": 1,
        "app_home": str(app_home),
        "mcp_servers": [{"name": "senses", "scope": "user"}],
        "connectors": [{"name": "claude.ai Google Calendar", "kind": "claude_ai"}],
        "paths": [str(app_home), str(shortcut)],
        "mac_bundle_id": None,
    }
    (app_home / "install_manifest.json").write_text(json.dumps(manifest))
    servers = {
        "senses": f"{app_home}/venv/python {app_home}/app/src/senses_server.py",
        "github": "npx server-github",  # the user's own, added before install
        "claude.ai Gmail": "https://gmail",  # the user's own connector
        "claude.ai Google Calendar": "https://cal",  # connected during install
    }
    return manifest, shortcut, FakeClaude(servers)


def test_plan_contains_only_manifest_entries(app_home, tmp_path):
    manifest, shortcut, fake = make_install(app_home, tmp_path)
    actions = uninstall.plan_uninstall(manifest, fake.servers)
    targets = {(a.kind, a.target) for a in actions}
    assert ("remove_server", "senses") in targets
    assert ("connector", "claude.ai Google Calendar") in targets
    assert ("delete", str(shortcut)) in targets
    assert all(t not in ("github", "claude.ai Gmail") for _, t in targets)
    assert actions[-1] == uninstall.Action("delete", str(app_home))  # app folder goes last


def test_server_repointed_elsewhere_is_left_alone(app_home, tmp_path):
    manifest, _, fake = make_install(app_home, tmp_path)
    fake.servers["senses"] = "/opt/other/senses.py"
    actions = uninstall.plan_uninstall(manifest, fake.servers)
    assert not any(a.kind == "remove_server" for a in actions)


def test_execute_cleans_everything(app_home, tmp_path):
    manifest, shortcut, fake = make_install(app_home, tmp_path)
    problems = uninstall.execute(uninstall.plan_uninstall(manifest, fake.servers), fake, False, log=lambda s: None)
    assert problems == []
    assert "senses" not in fake.servers
    assert {"github", "claude.ai Gmail"} <= set(fake.servers)  # untouched
    assert not app_home.exists()
    assert not shortcut.exists()


def test_running_twice_is_safe(app_home, tmp_path):
    manifest, _, fake = make_install(app_home, tmp_path)
    uninstall.execute(uninstall.plan_uninstall(manifest, fake.servers), fake, False, log=lambda s: None)
    calls_before = len(fake.calls)
    # Second run with the same (saved) manifest: nothing left to do.
    second = uninstall.plan_uninstall(manifest, cs.parse_mcp_list(fake(["mcp", "list"]).stdout))
    assert [a for a in second if a.kind != "connector"] == []
    assert uninstall.execute(second, fake, False, log=lambda s: None) == []
    assert len(fake.calls) == calls_before + 1  # only the list call above


def test_main_without_install_is_a_no_op(app_home, capsys):
    import shutil

    shutil.rmtree(app_home)
    assert uninstall.main(["--yes"]) == 0
    assert "not installed" in capsys.readouterr().out


def test_claude_session_folder_removed(app_home, tmp_path, monkeypatch):
    manifest, _, fake = make_install(app_home, tmp_path)
    workspace = app_home / "workspace"
    fake_home = tmp_path / "userhome"
    projects = fake_home / ".claude" / "projects"
    session_dir = projects / cs.claude_project_slug(workspace)
    session_dir.mkdir(parents=True)
    other = projects / "d--my-real-project"
    other.mkdir()
    monkeypatch.setattr(uninstall.cs.Path, "home", classmethod(lambda cls: fake_home))
    manifest["claude_workspace"] = str(workspace)
    uninstall.execute(uninstall.plan_uninstall(manifest, fake.servers), fake, False, log=lambda s: None)
    assert not session_dir.exists()
    assert other.exists()

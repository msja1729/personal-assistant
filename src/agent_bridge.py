"""Agent Bridge: sends a transcript to Claude Code and streams the reply back.

Claude is reached only through headless Claude Code::

    claude -p <transcript> --output-format stream-json --verbose
           --include-partial-messages --resume <session>

One session is kept and resumed for every request (and across app restarts),
so follow-up questions have context.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from src import config, platform_support

log = logging.getLogger(__name__)

EventType = Literal["text", "tool_use", "done", "error"]

SENSES_SERVER = "senses"

SYSTEM_PROMPT = """\
You are the user's voice assistant on their computer. The user spoke to you and \
your reply is read aloud and shown in a small notification.
- Answer briefly and conversationally, in plain sentences that sound natural when \
spoken. No markdown, tables, bullet symbols or code blocks unless asked.
- You can see the screen with the capture_screen tool and draw to explain with the \
board_* tools (glass board or directly over the screen). Use them when seeing or \
showing would help. All board coordinates are fractions 0..1 of the screen: x left \
to right, y top to bottom; text and LaTeX are centred on (x, y).
- When drawing, still give a short spoken explanation.
- Never send, delete or change anything (email, events, files) unless the user \
clearly asked for exactly that.
"""

# Connector tools whose names start with one of these only read data, so they
# may run without a confirmation prompt.
READ_ONLY_PREFIXES = ("search", "get", "list", "read", "fetch", "find", "query", "lookup", "download")


@dataclass(frozen=True)
class Event:
    type: EventType
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "content": self.content}


class StreamParser:
    """Turns Claude Code ``stream-json`` lines into bridge events.

    Text arrives as partial deltas (``stream_event`` / ``text_delta``) for low
    latency. Full ``assistant`` messages repeat that text, so their text is
    only used when no deltas were seen for that message.
    """

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.tools: list[str] = []
        self._streamed_text = False
        self.finished = False

    def feed(self, line: str) -> list[Event]:
        line = line.strip()
        if not line:
            return []
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            log.debug("non-JSON line from claude: %s", line[:200])
            return []
        if not isinstance(obj, dict):
            return []
        kind = obj.get("type")
        if obj.get("session_id"):
            self.session_id = str(obj["session_id"])

        if kind == "system" and obj.get("subtype") == "init":
            tools = obj.get("tools")
            if isinstance(tools, list):
                self.tools = [str(t) for t in tools]
            return []

        if kind == "stream_event":
            event = obj.get("event") or {}
            etype = event.get("type")
            if etype == "message_start":
                self._streamed_text = False
            elif etype == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    self._streamed_text = True
                    return [Event("text", str(delta["text"]))]
            return []

        if kind == "assistant":
            out: list[Event] = []
            message = obj.get("message") or {}
            blocks = message.get("content") or []
            text_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            if not self._streamed_text:
                for b in text_blocks:
                    if b.get("text"):
                        out.append(Event("text", str(b["text"])))
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    out.append(Event("tool_use", str(b.get("name", ""))))
            self._streamed_text = False
            return out

        if kind == "result":
            self.finished = True
            if obj.get("is_error") or obj.get("subtype") not in (None, "success"):
                message = obj.get("result") or obj.get("error") or obj.get("subtype") or "Claude Code error"
                return [Event("error", str(message))]
            return [Event("done", str(obj.get("result") or ""))]

        return []


def find_claude() -> str | None:
    """Locate the Claude Code executable."""
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / ("claude.exe" if platform_support.IS_WINDOWS else "claude"),
        home / ".claude" / "local" / ("claude.exe" if platform_support.IS_WINDOWS else "claude"),
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
    ]
    if platform_support.IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm" / "claude.cmd")
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def allowed_tools(known_tools: list[str]) -> list[str]:
    """Tools that run without asking: our senses server, web search, and read-only connector tools."""
    allowed = [f"mcp__{SENSES_SERVER}", "WebSearch", "WebFetch"]
    for tool in known_tools:
        if not tool.startswith("mcp__") or tool.startswith(f"mcp__{SENSES_SERVER}__"):
            continue
        action = tool.rsplit("__", 1)[-1].lower()
        if action.startswith(READ_ONLY_PREFIXES):
            allowed.append(tool)
    return allowed


class AgentBridge:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        state = self._load_state()
        self.session_id: str | None = state.get("session_id")
        self._known_tools: list[str] = list(state.get("tools", []))

    # ---------------------------------------------------------------- state
    def _load_state(self) -> dict:
        try:
            data = json.loads(config.state_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        path = config.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session_id": self.session_id, "tools": self._known_tools}), encoding="utf-8")

    def new_session(self) -> None:
        self.session_id = None
        self._save_state()

    # ---------------------------------------------------------------- command
    def build_command(self, claude: str, transcript: str) -> list[str]:
        cmd = [
            claude,
            "-p",
            transcript,
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--append-system-prompt",
            SYSTEM_PROMPT,
            "--allowedTools",
            ",".join(allowed_tools(self._known_tools)),
        ]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        return cmd

    def ask(self, transcript: str) -> Iterator[Event]:
        """Send ``transcript`` and yield reply events as they arrive, until done or error."""
        claude = find_claude()
        if claude is None:
            yield Event("error", "Claude Code is not installed or not on PATH.")
            return
        yield from self._run(claude, transcript, retry_fresh=True)

    def _run(self, claude: str, transcript: str, retry_fresh: bool) -> Iterator[Event]:
        workdir = config.workspace_dir()
        workdir.mkdir(parents=True, exist_ok=True)
        cmd = self.build_command(claude, transcript)
        if claude.lower().endswith(".cmd"):
            cmd = ["cmd", "/c", *cmd]
        log.info("asking Claude (session %s)", self.session_id or "new")
        parser = StreamParser()
        produced = False
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=platform_support.NO_WINDOW,
            )
        except OSError as exc:
            yield Event("error", f"Could not start Claude Code: {exc}")
            return
        proc = self._proc
        assert proc.stdout is not None
        for line in proc.stdout:
            for event in parser.feed(line):
                produced = produced or event.type in ("text", "tool_use")
                if event.type == "error" and not produced and retry_fresh and self.session_id:
                    # The saved session may be gone; start a fresh one once.
                    log.warning("resume failed (%s); starting a new session", event.content)
                    proc.kill()
                    self.session_id = None
                    yield from self._run(claude, transcript, retry_fresh=False)
                    return
                yield event
        proc.wait()
        stderr = proc.stderr.read() if proc.stderr else ""
        self._proc = None
        if parser.session_id:
            self.session_id = parser.session_id
        if parser.tools:
            self._known_tools = parser.tools
        self._save_state()
        if not parser.finished:
            if retry_fresh and self.session_id and not produced and "session" in stderr.lower():
                self.session_id = None
                yield from self._run(claude, transcript, retry_fresh=False)
                return
            message = stderr.strip().splitlines()[-1] if stderr.strip() else f"Claude Code exited ({proc.returncode})"
            yield Event("error", message)

    def cancel(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()


if __name__ == "__main__":  # manual check: python -m src.agent_bridge "question"
    logging.basicConfig(level=logging.INFO)
    bridge = AgentBridge()
    for ev in bridge.ask(" ".join(sys.argv[1:]) or "Say hello in one sentence."):
        print(ev.as_dict())

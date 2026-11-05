"""Local command channel between the running app and the senses MCP server.

Claude Code starts the senses server as its own process, but the overlay that
draws lives in the app. Pen commands travel over an authenticated named pipe
(Windows) or unix socket (macOS) using ``multiprocessing.connection``. The
shared secret is a random key stored in the app home folder, so only
processes of this user can send commands.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path
from typing import Any, Callable

from src import config, platform_support

log = logging.getLogger(__name__)

Handler = Callable[[str, dict[str, Any]], dict[str, Any]]


def _key(create: bool) -> bytes | None:
    path = config.ipc_key_path()
    try:
        return path.read_bytes()
    except OSError:
        if not create:
            return None
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


class AppNotRunning(RuntimeError):
    pass


def send(cmd: str, args: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    """Send one command to the running app and return its reply."""
    key = _key(create=False)
    if key is None:
        raise AppNotRunning("Personal Assistant app is not running")
    address = platform_support.ipc_address(config.app_home())
    try:
        conn = Client(address, family=platform_support.ipc_family(), authkey=key)
    except (OSError, EOFError) as exc:
        raise AppNotRunning("Personal Assistant app is not running") from exc
    with conn:
        conn.send({"cmd": cmd, "args": args or {}})
        if not conn.poll(timeout):
            return {"ok": False, "error": "app did not answer in time"}
        reply = conn.recv()
    return reply if isinstance(reply, dict) else {"ok": False, "error": "bad reply"}


class Server:
    """Accepts commands on a background thread and passes them to ``handler``."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        address = platform_support.ipc_address(config.app_home())
        if not platform_support.IS_WINDOWS:
            Path(address).unlink(missing_ok=True)
        self._listener = Listener(address, family=platform_support.ipc_family(), authkey=_key(create=True))
        self._thread = threading.Thread(target=self._serve, name="ipc", daemon=True)
        self._thread.start()

    def close(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass

    def _serve(self) -> None:
        while self._listener is not None:
            try:
                conn = self._listener.accept()
            except (OSError, EOFError):
                if self._listener is None:
                    return
                continue
            except Exception:  # authentication failure etc.
                log.warning("rejected an IPC connection", exc_info=True)
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: Connection) -> None:
        with conn:
            try:
                msg = conn.recv()
                cmd = str(msg.get("cmd", ""))
                args = msg.get("args") or {}
                reply = self._handler(cmd, args)
            except Exception as exc:  # never crash the app on a bad command
                log.exception("IPC command failed")
                reply = {"ok": False, "error": str(exc)}
            try:
                conn.send(reply)
            except OSError:
                pass


def app_is_running() -> bool:
    try:
        return bool(send("ping", timeout=2).get("ok"))
    except AppNotRunning:
        return False

"""Personal Assistant — background app entry point.

Flow: Trigger (wake word / hotkey) -> Capture + STT -> Agent Bridge (Claude
Code) -> Output (notification card + seamless speech). The overlay draws pen
commands that arrive from the senses MCP server over the local IPC channel.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):  # started as a script (shortcut / launch agent)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QFileSystemWatcher, QObject, QRectF, Qt, QTimer, Signal, Slot  # noqa: E402
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon  # noqa: E402

from src import APP_NAME, __version__, config, ipc  # noqa: E402
from src.agent_bridge import AgentBridge  # noqa: E402
from src.capture import Transcriber, record_until_silence  # noqa: E402
from src.dashboard import Dashboard  # noqa: E402
from src.output import NotificationCard, SpeechStream  # noqa: E402
from src.overlay import Overlay  # noqa: E402
from src.platform_support import Speaker  # noqa: E402
from src.trigger import Trigger, ensure_wake_models  # noqa: E402

log = logging.getLogger("assistant")

BOARD_COMMANDS = {
    "board_open": lambda o, a: o.open(a["mode"]),
    "board_write_text": lambda o, a: o.write_text(a["text"], a["x"], a["y"], a.get("size")),
    "board_write_latex": lambda o, a: o.write_latex(a["latex"], a["x"], a["y"], a.get("size")),
    "board_draw_arrow": lambda o, a: o.draw_arrow(a["x1"], a["y1"], a["x2"], a["y2"], a.get("label")),
    "board_draw_shape": lambda o, a: o.draw_shape(a["shape"], a["x"], a["y"], a["w"], a["h"], a.get("label")),
    "board_highlight": lambda o, a: o.highlight(a["x"], a["y"], a["w"], a["h"]),
    "board_clear": lambda o, a: o.clear(),
    "board_close": lambda o, a: o.close(),
}


def setup_logging() -> None:
    folder = config.logs_dir()
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / "app.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("httpx", "httpx2", "httpcore", "huggingface_hub", "urllib3", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler())


def make_icon(color: QColor = QColor(229, 214, 189)) -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(28, 26, 24))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(2, 2, 60, 60))
    p.setBrush(color)
    for i, h in enumerate((16, 30, 22)):
        p.drawRoundedRect(QRectF(16 + i * 12, 32 - h / 2, 8, h), 4, 4)
    p.end()
    return QIcon(pix)


class GuiInvoker(QObject):
    """Runs a function on the GUI thread and returns its result to the caller's thread."""

    _call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, job: tuple[Callable[[], Any], Future]) -> None:
        fn, future = job
        try:
            future.set_result(fn())
        except Exception as exc:
            future.set_exception(exc)

    def call(self, fn: Callable[[], Any], timeout: float = 10.0) -> Any:
        future: Future = Future()
        self._call.emit((fn, future))
        return future.result(timeout)


class UiSignals(QObject):
    status = Signal(str, str)
    card_start = Signal(str)
    card_status = Signal(str)
    card_append = Signal(str)
    card_message = Signal(str, str)
    card_linger = Signal()
    card_hide = Signal()


class Assistant:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.cfg = config.ensure_default()
        self.speaker = Speaker()
        self.transcriber = Transcriber()
        self.bridge = AgentBridge()
        self.overlay = Overlay()
        self.card = NotificationCard()
        self.invoker = GuiInvoker()
        self.ui = UiSignals()
        self.dashboard = Dashboard(on_change=lambda cfg: self.apply_config())
        self.trigger = Trigger(self.on_trigger)
        self._busy = threading.Lock()
        self._cancel = threading.Event()

        self.ui.status.connect(lambda s, d: self.dashboard.status.set_status(s, d))
        self.ui.card_start.connect(self.card.start)
        self.ui.card_status.connect(self.card.set_status)
        self.ui.card_append.connect(self.card.append)
        self.ui.card_message.connect(self.card.show_message)
        self.ui.card_linger.connect(self.card.linger)
        self.ui.card_hide.connect(self.card.fade_out)
        self.dashboard.listen_requested.connect(lambda: self.on_trigger("dashboard"))
        self.dashboard.new_conversation_requested.connect(self.bridge.new_session)
        self.dashboard.close_board_requested.connect(self.overlay.close)

        self.tray = self._make_tray()
        self.ipc = ipc.Server(self.handle_ipc)
        self.ipc.start()

        # Re-read config.yaml whenever it changes on disk (dashboard or a manual edit).
        self.watcher = QFileSystemWatcher([str(config.config_path().parent)])
        self._reload_timer = QTimer(singleShot=True, interval=200)
        self._reload_timer.timeout.connect(self.apply_config)
        self.watcher.directoryChanged.connect(lambda _: self._reload_timer.start())

        self.apply_config()
        threading.Thread(target=self._warm_up, daemon=True).start()

    # ------------------------------------------------------------------ setup
    def _make_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(make_icon(), self.app)
        tray.setToolTip(f"{APP_NAME} — say \"{self.cfg.wake_word}\" or press {self.cfg.hotkey}")
        menu = QMenu()
        actions = [
            ("Open dashboard", self.show_dashboard),
            ("Listen now", lambda: self.on_trigger("tray")),
            ("Close board", self.overlay.close),
            ("New conversation", self.bridge.new_session),
            (None, None),
            (f"Quit {APP_NAME}", self.quit),
        ]
        for text, fn in actions:
            if text is None:
                menu.addSeparator()
                continue
            action = QAction(text, menu)
            action.triggered.connect(fn)
            menu.addAction(action)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self.show_dashboard() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        tray.show()
        self._tray_menu = menu
        return tray

    def _warm_up(self) -> None:
        try:
            self.transcriber.preload(self.cfg.stt_model)
        except Exception:
            log.exception("speech model failed to load")

    def apply_config(self) -> None:
        self.cfg = config.load()
        self.trigger.apply(self.cfg)
        self.dashboard.reload()
        if not self.cfg.drawing and self.overlay.is_open():
            self.overlay.close()
        if not self.cfg.speak_aloud:
            self.speaker.stop()
        if not self.cfg.show_notifications:
            self.card.hide()
        self.tray.setToolTip(f"{APP_NAME} — say \"{self.cfg.wake_word}\" or press {self.cfg.hotkey}")

    def show_dashboard(self) -> None:
        self.dashboard.reload()
        self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def quit(self) -> None:
        log.info("quitting")
        self._cancel.set()
        self.bridge.cancel()
        self.speaker.stop()
        self.trigger.shutdown()
        self.ipc.close()
        self.tray.hide()
        self.app.quit()

    # -------------------------------------------------------------------- IPC
    def handle_ipc(self, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
        if cmd == "ping":
            return {"ok": True, "version": __version__}
        if cmd == "quit":
            self.invoker.call(lambda: QTimer.singleShot(300, self.quit))
            return {"ok": True}
        if cmd == "ask":  # typed question through the same pipeline (testing, accessibility)
            text = str(args.get("text", "")).strip()
            if not text:
                return {"ok": False, "error": "text is empty"}
            self.on_trigger("ipc", transcript=text)
            return {"ok": True}
        if cmd == "status":
            return {"ok": True, "busy": self._busy.locked(), "speaking": self.speaker.is_speaking(),
                    "reply": self.card.text()}
        if cmd == "show_dashboard":
            self.invoker.call(self.show_dashboard)
            return {"ok": True}
        if cmd in BOARD_COMMANDS:
            if not config.load().drawing:  # enforced here too, not only in the MCP server
                return {"ok": False, "error": "drawing disabled by user"}
            try:
                return self.invoker.call(lambda: BOARD_COMMANDS[cmd](self.overlay, args))
            except (KeyError, ValueError, TypeError) as exc:
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"unknown command {cmd!r}"}

    # --------------------------------------------------------------- pipeline
    def on_trigger(self, source: str, transcript: str | None = None) -> None:
        """Called from the hotkey/wake-word threads or the UI."""
        if self._busy.locked():
            # Barge-in: a new trigger interrupts the current reply.
            self._cancel.set()
            self.bridge.cancel()
            self.speaker.stop()
        threading.Thread(target=self._conversation, args=(source, transcript), daemon=True).start()

    def _conversation(self, source: str, transcript: str | None = None) -> None:
        if not self._busy.acquire(timeout=5):
            return
        self._cancel.clear()
        cfg = config.load()  # every request uses the latest settings
        notify = cfg.show_notifications
        try:
            self.trigger.pause()
            if transcript is None:
                self.ui.status.emit("listening", "")
                if notify:
                    self.ui.card_start.emit("Listening")
                try:
                    audio = record_until_silence(cfg.silence_timeout_ms, cancel=self._cancel)
                except Exception as exc:
                    log.exception("microphone error")
                    self._fail(notify, cfg, f"I can't use the microphone: {exc}")
                    return
                if self._cancel.is_set():
                    return
                if notify:
                    self.ui.card_status.emit("Transcribing")
                transcript = self.transcriber.transcribe(audio, cfg.stt_model)
            log.info("request (%s): %r", source, transcript)
            if not transcript:
                self.ui.status.emit("idle", "")
                if notify:
                    self.ui.card_hide.emit()
                return
            self.ui.status.emit("thinking", transcript[:40])
            if notify:
                self.ui.card_start.emit("Thinking")
            speech = SpeechStream(self.speaker, cfg.speak_aloud)
            got_text = False
            for event in self.bridge.ask(transcript):
                if self._cancel.is_set():
                    return
                if event.type == "text":
                    if not got_text and notify:
                        self.ui.card_status.emit("Jarvis")
                    got_text = True
                    speech.feed(event.content)
                    if notify:
                        self.ui.card_append.emit(event.content)
                elif event.type == "tool_use":
                    self.ui.status.emit("thinking", event.content.rsplit("__", 1)[-1])
                    speech.feed("\n\n")  # a tool call ends the current sentence
                    if notify:
                        self.ui.card_append.emit("\n")
                elif event.type == "error":
                    log.error("Claude error: %s", event.content)
                    self._fail(notify, cfg, f"Something went wrong: {event.content}")
                    return
                elif event.type == "done" and not got_text and event.content:
                    speech.feed(event.content)
                    if notify:
                        self.ui.card_append.emit(event.content)
            if self._cancel.is_set():
                return
            speech.flush()
            if cfg.speak_aloud:
                self.ui.status.emit("speaking", "")
                while not self.speaker.wait_until_done(0.1):
                    if self._cancel.is_set():
                        return
            if notify:
                self.ui.card_linger.emit()
            self.ui.status.emit("idle", "")
        except Exception as exc:  # keep the app alive whatever happens
            log.exception("conversation failed")
            self._fail(notify, cfg, f"Something went wrong: {exc}")
        finally:
            self.trigger.resume()
            self._busy.release()

    def _fail(self, notify: bool, cfg: config.Config, message: str) -> None:
        self.ui.status.emit("error", message[:60])
        if notify:
            self.ui.card_message.emit("Error", message)
            self.ui.card_linger.emit()
        if cfg.speak_aloud:
            self.speaker.say(message.split(":")[0] + ".")


def download_models() -> None:
    """Fetch the speech and wake-word models (used by the installer)."""
    cfg = config.ensure_default()
    print("Downloading wake word model…", flush=True)
    ensure_wake_models()
    print(f"Downloading speech model ({cfg.stt_model})…", flush=True)
    Transcriber().preload(cfg.stt_model)
    print("Models ready.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal-assistant")
    parser.add_argument("--dashboard", action="store_true", help="open the dashboard on start")
    parser.add_argument("--download-models", action="store_true", help="download models and exit")
    args = parser.parse_args(argv)
    config.keep_caches_in_app_home()

    if args.download_models:
        download_models()
        return 0

    setup_logging()
    if ipc.app_is_running():
        ipc.send("show_dashboard")
        log.info("already running; opened the dashboard")
        return 0

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_icon())
    log.info("%s %s starting", APP_NAME, __version__)
    assistant = Assistant(app)
    if args.dashboard or not config.state_path().exists():
        assistant.show_dashboard()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

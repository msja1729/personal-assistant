"""Trigger: the wake word ("Jarvis") or the global hotkey starts a capture."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from src import config, platform_support

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, the frame size openWakeWord expects

# Spoken wake word -> openWakeWord pre-trained model name.
WAKE_WORD_MODELS = {
    "jarvis": "hey_jarvis",
    "hey jarvis": "hey_jarvis",
    "alexa": "alexa",
    "hey mycroft": "hey_mycroft",
}

TriggerCallback = Callable[[str], None]


def wake_model_name(wake_word: str) -> str:
    """Model name (or path to a custom .onnx model) for a configured wake word."""
    word = wake_word.strip().lower()
    if word.endswith(".onnx"):
        return wake_word
    if word not in WAKE_WORD_MODELS:
        raise ValueError(f"no wake word model for {wake_word!r}; use one of {sorted(WAKE_WORD_MODELS)}")
    return WAKE_WORD_MODELS[word]


def ensure_wake_models(models: Path | None = None) -> Path:
    """Download openWakeWord's models into our models folder (once)."""
    target = (models or config.models_dir()) / "openwakeword"
    target.mkdir(parents=True, exist_ok=True)
    needed = ["melspectrogram.onnx", "embedding_model.onnx", "hey_jarvis_v0.1.onnx"]
    if not all((target / n).exists() for n in needed):
        from openwakeword import utils  # type: ignore[import-not-found]

        utils.download_models(model_names=["hey_jarvis"], target_directory=str(target))
    return target


class WakeWordListener:
    """Listens to the microphone and fires when the wake word is heard."""

    THRESHOLD = 0.5
    COOLDOWN_S = 2.0

    def __init__(self, wake_word: str, on_wake: Callable[[], None]) -> None:
        self.wake_word = wake_word
        self._on_wake = on_wake
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wakeword", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def pause(self) -> None:
        """Ignore the microphone (while the app records or speaks)."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def _run(self) -> None:
        try:
            import sounddevice as sd
            from openwakeword.model import Model  # type: ignore[import-not-found]

            folder = ensure_wake_models()
            name = wake_model_name(self.wake_word)
            model_path = name if name.endswith(".onnx") else str(next(folder.glob(f"{name}_v*.onnx")))
            model = Model(
                wakeword_models=[model_path],
                inference_framework="onnx",
                melspec_model_path=str(folder / "melspectrogram.onnx"),
                embedding_model_path=str(folder / "embedding_model.onnx"),
            )
        except Exception:
            log.exception("wake word unavailable")
            return
        log.info("wake word listener ready (%s)", self.wake_word)
        last_fire = 0.0
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME) as stream:
                while not self._stop.is_set():
                    frame, _ = stream.read(FRAME)
                    if self._paused.is_set():
                        continue
                    scores = model.predict(np.squeeze(frame))
                    if max(scores.values(), default=0.0) >= self.THRESHOLD:
                        now = time.monotonic()
                        model.reset()
                        if now - last_fire > self.COOLDOWN_S:
                            last_fire = now
                            log.info("trigger: wake word at %.3f", time.time())
                            self._on_wake()
        except Exception:
            log.exception("wake word listener stopped")


class Trigger:
    """Owns the wake-word listener and the hotkey; reconfigures them live."""

    def __init__(self, on_trigger: TriggerCallback) -> None:
        self._on_trigger = on_trigger
        self._wake: WakeWordListener | None = None
        self._hotkey: platform_support.GlobalHotkey | None = None
        self._applied: tuple[bool, str, str] | None = None

    def apply(self, cfg: config.Config) -> None:
        """Start, stop or change the wake word and hotkey to match ``cfg``."""
        wanted = (cfg.wake_word_enabled, cfg.wake_word, cfg.hotkey)
        if wanted == self._applied:
            return
        old = self._applied
        self._applied = wanted

        if old is None or old[2] != cfg.hotkey:
            if self._hotkey is not None:
                self._hotkey.stop()
            self._hotkey = platform_support.GlobalHotkey(cfg.hotkey, self._fire_hotkey)
            try:
                self._hotkey.start()
            except Exception:
                log.exception("could not register hotkey %s", cfg.hotkey)
                self._hotkey = None

        if old is None or old[:2] != wanted[:2]:
            if self._wake is not None:
                self._wake.stop()
                self._wake = None
            if cfg.wake_word_enabled:
                self._wake = WakeWordListener(cfg.wake_word, self._fire_wake)
                self._wake.start()

    def pause(self) -> None:
        if self._wake is not None:
            self._wake.pause()

    def resume(self) -> None:
        if self._wake is not None:
            self._wake.resume()

    def shutdown(self) -> None:
        if self._hotkey is not None:
            self._hotkey.stop()
        if self._wake is not None:
            self._wake.stop()

    def _fire_hotkey(self) -> None:
        log.info("trigger: hotkey at %.3f", time.time())
        self._on_trigger("hotkey")

    def _fire_wake(self) -> None:
        self._on_trigger("wake_word")

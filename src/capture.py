"""Capture + STT: record until the user stops talking, then transcribe locally."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from src import config

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME = SAMPLE_RATE * FRAME_MS // 1000


@dataclass
class EnergyVAD:
    """Voice activity detection on frame loudness with an adaptive noise floor.

    The first frames calibrate the room's background level; a frame counts
    as speech when it is clearly louder than that floor. Recording ends after
    ``silence_timeout_ms`` of non-speech once speech has started.
    """

    silence_timeout_ms: int
    margin_db: float = 9.0
    min_speech_db: float = -50.0
    calibration_frames: int = 6
    noise_db: float = -60.0
    _frames: int = 0
    heard_speech: bool = False
    _silence_ms: int = 0
    _speech_ms: int = field(default=0)

    @staticmethod
    def level_db(frame: np.ndarray) -> float:
        samples = frame.astype(np.float32)
        if np.issubdtype(frame.dtype, np.integer):
            samples = samples / 32768.0
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        return 20.0 * np.log10(max(rms, 1e-9))

    def is_speech(self, frame: np.ndarray) -> bool:
        db = self.level_db(frame)
        self._frames += 1
        if self._frames <= self.calibration_frames:
            # Calibrate quickly toward the ambient level.
            self.noise_db = db if self._frames == 1 else 0.5 * self.noise_db + 0.5 * db
            return False
        speech = db > max(self.noise_db + self.margin_db, self.min_speech_db)
        if not speech:
            # Track slow changes in background noise.
            self.noise_db = 0.95 * self.noise_db + 0.05 * db
        return speech

    def update(self, frame: np.ndarray, frame_ms: int = FRAME_MS) -> bool:
        """Feed one frame; return True when the utterance is finished."""
        if self.is_speech(frame):
            self._speech_ms += frame_ms
            if self._speech_ms >= 90:  # ignore clicks shorter than ~3 frames
                self.heard_speech = True
            self._silence_ms = 0
        else:
            if not self.heard_speech:
                self._speech_ms = 0
            self._silence_ms += frame_ms
        return self.heard_speech and self._silence_ms >= self.silence_timeout_ms


def record_until_silence(
    silence_timeout_ms: int,
    max_seconds: float = 30.0,
    no_speech_timeout_s: float = 6.0,
    cancel: threading.Event | None = None,
) -> np.ndarray:
    """Record from the default microphone until silence; return float32 mono 16 kHz audio."""
    import sounddevice as sd

    vad = EnergyVAD(silence_timeout_ms=silence_timeout_ms)
    chunks: list[np.ndarray] = []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=FRAME) as stream:
        started = time.monotonic()
        log.info("recording started at %.3f", time.time())
        while True:
            frame, _ = stream.read(FRAME)
            frame = frame[:, 0].copy()
            chunks.append(frame)
            if vad.update(frame):
                break
            elapsed = time.monotonic() - started
            if cancel is not None and cancel.is_set():
                return np.zeros(0, dtype=np.float32)
            if elapsed >= max_seconds:
                break
            if not vad.heard_speech and elapsed >= no_speech_timeout_s:
                log.info("no speech heard")
                return np.zeros(0, dtype=np.float32)
    log.info("recording stopped at %.3f (%.1f s)", time.time(), time.monotonic() - started)
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


class Transcriber:
    """Local speech-to-text with faster-whisper. The model loads once and is reused."""

    def __init__(self) -> None:
        self._model = None
        self._model_name = ""
        self._lock = threading.Lock()

    def _get(self, model_name: str):  # noqa: ANN202
        with self._lock:
            if self._model is None or self._model_name != model_name:
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]

                root = config.models_dir() / "whisper"
                root.mkdir(parents=True, exist_ok=True)
                log.info("loading speech model %s", model_name)
                self._model = WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(root))
                self._model_name = model_name
            return self._model

    def preload(self, model_name: str) -> None:
        self._get(model_name)

    def transcribe(self, audio: np.ndarray, model_name: str) -> str:
        if audio.size < SAMPLE_RATE // 4:
            return ""
        model = self._get(model_name)
        language = "en" if model_name.endswith(".en") else None
        segments, _ = model.transcribe(audio, beam_size=1, language=language, vad_filter=False)
        text = " ".join(s.text.strip() for s in segments).strip()
        log.info("transcript: %r", text)
        return text

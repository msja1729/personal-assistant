"""Everything that differs between Windows and macOS lives here.

- Text-to-speech (SAPI on Windows, the system voice on macOS), played seamlessly
- Global hotkey (pynput on Windows, a Quartz event tap on macOS)
- macOS privacy permissions (check, request, reset)
- Start-at-login entries and Start-menu shortcut
- IPC address used between the app and the senses MCP server
"""

from __future__ import annotations

import getpass
import logging
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path
from typing import Callable

try:
    import numpy as np
except ImportError:  # the installer's first stage runs before libraries exist
    np = None  # type: ignore[assignment]

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

log = logging.getLogger(__name__)

# Hide console windows for helper processes on Windows.
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def open_url(url: str) -> None:
    webbrowser.open(url)


def ipc_address(home: Path) -> str:
    """Address of the app's local command channel (named pipe / unix socket)."""
    if IS_WINDOWS:
        user = re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser())
        return rf"\\.\pipe\PersonalAssistant-{user}"
    return str(home / "ipc.sock")


def ipc_family() -> str:
    return "AF_PIPE" if IS_WINDOWS else "AF_UNIX"


# ====================================================================== TTS


class Speaker:
    """Speaks sentences as one continuous stream with short, natural pauses.

    OS voices leave ~0.9 s of silence after every utterance, which sounds
    like the speech is being cut up. Instead, each sentence is synthesized to
    memory by the OS engine (SAPI on Windows, the system voice on macOS) on a
    background thread while the previous sentence is still playing. Its
    leading/trailing silence is trimmed (with short fades, so no clicks), a
    fixed natural pause is added, and everything plays through one audio
    stream that stays open while speaking. Playback never waits for the next
    sentence and the pause between sentences stays under 300 ms.
    """

    RATE = 22050
    PAUSE_S = 0.17  # pause added between sentences
    MARGIN_S = 0.03  # silence kept at each trimmed edge
    FADE_S = 0.006
    IDLE_CLOSE_S = 2.0  # close the audio device after this much idle time

    def __init__(self, sink: Callable[[np.ndarray], None] | None = None) -> None:
        """``sink`` replaces the sound card (used to measure output in tests)."""
        self._texts: "queue.Queue[tuple[int, str]]" = queue.Queue()
        self._chunks: "deque[np.ndarray]" = deque()
        self._lock = threading.Lock()
        self._generation = 0
        self._pending = 0  # sentences accepted but not yet fully played
        self._idle = threading.Event()
        self._idle.set()
        self._sink = sink
        self._stream = None
        self._last_audio = 0.0
        self._ready = threading.Event()
        self.available = False
        threading.Thread(target=self._synth_loop, name="tts-synth", daemon=True).start()
        if sink is None:
            threading.Thread(target=self._device_loop, name="tts-device", daemon=True).start()
        self._ready.wait(10)

    # -- public API (any thread)
    def say(self, sentence: str) -> None:
        sentence = sentence.strip()
        if not sentence:
            return
        with self._lock:
            self._pending += 1
            self._idle.clear()
            generation = self._generation
        self._texts.put((generation, sentence))

    def stop(self) -> None:
        """Stop speaking now and drop everything queued."""
        with self._lock:
            self._generation += 1
            self._chunks.clear()
            self._pending = 0
            self._current = None
            self._idle.set()

    def is_speaking(self) -> bool:
        return not self._idle.is_set()

    def wait_until_done(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    # -- synthesis
    def _synth_loop(self) -> None:
        try:
            engine = _WindowsSynth(self.RATE) if IS_WINDOWS else _MacSynth(self.RATE) if IS_MAC else None
        except Exception:  # pragma: no cover - depends on OS components
            log.exception("text-to-speech engine unavailable")
            engine = None
        self.available = engine is not None
        self._ready.set()
        while True:
            generation, text = self._texts.get()
            if generation != self._generation:
                continue  # stopped while queued
            pcm = None
            if engine is not None:
                try:
                    pcm = engine.synthesize(text)
                except Exception:
                    log.exception("speech synthesis failed")
            with self._lock:
                if generation != self._generation:
                    continue
                if pcm is None or pcm.size == 0:
                    self._finish_one()
                    continue
                self._chunks.append(prepare_chunk(pcm, self.RATE, self.MARGIN_S, self.FADE_S, self.PAUSE_S))
            if self._sink is not None:
                self._drain_to_sink()

    def _finish_one(self) -> None:
        self._pending = max(0, self._pending - 1)
        if self._pending == 0 and not self._chunks:
            self._idle.set()

    def _drain_to_sink(self) -> None:
        while True:
            with self._lock:
                if not self._chunks:
                    return
                chunk = self._chunks.popleft()
            assert self._sink is not None
            self._sink(chunk)
            with self._lock:
                self._finish_one()

    # -- playback (sound card)
    _current: np.ndarray | None = None
    _pos = 0

    def _callback(self, outdata, frames, _time, _status) -> None:  # noqa: ANN001
        out = outdata[:, 0]
        filled = 0
        with self._lock:
            while filled < frames:
                if self._current is None:
                    if not self._chunks:
                        break
                    self._current = self._chunks.popleft()
                    self._pos = 0
                take = min(frames - filled, len(self._current) - self._pos)
                out[filled : filled + take] = self._current[self._pos : self._pos + take]
                filled += take
                self._pos += take
                if self._pos >= len(self._current):
                    self._current = None
                    self._finish_one()
            if filled:
                self._last_audio = time.monotonic()
        out[filled:] = 0

    def _device_loop(self) -> None:
        import sounddevice as sd

        while True:
            time.sleep(0.02)
            with self._lock:
                has_audio = bool(self._chunks) or self._current is not None
            if has_audio and self._stream is None:
                try:
                    self._stream = sd.OutputStream(
                        samplerate=self.RATE, channels=1, dtype="int16", callback=self._callback, latency="low"
                    )
                    self._stream.start()
                    self._last_audio = time.monotonic()
                except Exception:
                    log.exception("could not open the speakers")
                    self.stop()
                    self._stream = None
            elif self._stream is not None and not has_audio:
                if time.monotonic() - self._last_audio > self.IDLE_CLOSE_S:
                    stream, self._stream = self._stream, None
                    stream.stop()
                    stream.close()


def prepare_chunk(pcm: np.ndarray, rate: int, margin_s: float, fade_s: float, pause_s: float) -> np.ndarray:
    """Trim silence at both ends, fade the edges and add the inter-sentence pause."""
    pcm = pcm.astype(np.int16, copy=False)
    loud = np.flatnonzero(np.abs(pcm.astype(np.int32)) > 400)
    if loud.size == 0:
        return np.zeros(0, dtype=np.int16)
    margin = int(rate * margin_s)
    start = max(0, loud[0] - margin)
    end = min(len(pcm), loud[-1] + margin)
    voice = pcm[start:end].astype(np.float32)
    fade = min(int(rate * fade_s), len(voice) // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        voice[:fade] *= ramp
        voice[-fade:] *= ramp[::-1]
    pause = np.zeros(int(rate * pause_s), dtype=np.int16)
    return np.concatenate([voice.astype(np.int16), pause])


class _WindowsSynth:
    """SAPI rendering one sentence to 16-bit mono PCM in memory."""

    SVSF_IS_NOT_XML = 16
    FORMATS = {22050: 22, 16000: 18, 44100: 34}  # SpeechAudioFormatType, 16-bit mono

    def __init__(self, rate: int) -> None:
        import pythoncom  # type: ignore[import-not-found]
        import win32com.client  # type: ignore[import-not-found]

        pythoncom.CoInitialize()
        self._client = win32com.client
        self._voice = win32com.client.Dispatch("SAPI.SpVoice")
        self._format = self.FORMATS[rate]

    def synthesize(self, text: str) -> np.ndarray:
        stream = self._client.Dispatch("SAPI.SpMemoryStream")
        fmt = self._client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = self._format
        stream.Format = fmt
        self._voice.AudioOutputStream = stream
        self._voice.Speak(text, self.SVSF_IS_NOT_XML)
        return np.frombuffer(bytes(stream.GetData()), dtype=np.int16)


class _MacSynth:  # pragma: no cover - macOS only
    """The macOS system voice (``say``) rendering one sentence to 16-bit mono PCM."""

    def __init__(self, rate: int) -> None:
        self._rate = rate
        subprocess.run(["say", "--version"], capture_output=True)

    def synthesize(self, text: str) -> np.ndarray:
        import tempfile
        import wave

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.wav"
            subprocess.run(
                ["say", "--file-format=WAVE", f"--data-format=LEI16@{self._rate}", "-o", str(path), "--", text],
                check=True,
                capture_output=True,
            )
            with wave.open(str(path)) as w:
                return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


# =================================================================== hotkey


def parse_hotkey(spec: str) -> tuple[frozenset[str], str]:
    """Split ``"<ctrl>+<shift>+<space>"`` into ({"ctrl","shift"}, "space")."""
    parts = [p.strip().strip("<>").lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods = {"ctrl", "shift", "alt", "cmd"}
    aliases = {"control": "ctrl", "option": "alt", "command": "cmd", "win": "cmd", "super": "cmd"}
    parts = [aliases.get(p, p) for p in parts]
    modifiers = frozenset(p for p in parts[:-1])
    key = parts[-1]
    if key in mods or not modifiers <= mods:
        raise ValueError(f"hotkey must be modifiers plus one key: {spec!r}")
    return modifiers, key


class GlobalHotkey:
    """Calls ``callback`` whenever the configured key combination is pressed."""

    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        self.spec = spec
        self._callback = callback
        self._impl: object | None = None

    def start(self) -> None:
        parse_hotkey(self.spec)  # validate early
        if IS_MAC:
            self._impl = _MacHotkey(self.spec, self._callback)
        else:
            self._impl = _PynputHotkey(self.spec, self._callback)
        log.info("hotkey active: %s", self.spec)

    def stop(self) -> None:
        impl, self._impl = self._impl, None
        if impl is not None:
            impl.stop()  # type: ignore[attr-defined]


_MODIFIER_NAMES = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
}


def key_name(key: object) -> str:
    """Normalized name of a pynput key: "ctrl", "space", "f5", "j", "7"…"""
    name = getattr(key, "name", None)
    if name:  # a special Key
        return _MODIFIER_NAMES.get(name, name)
    vk = getattr(key, "vk", None)
    # With Ctrl held, letters arrive as control characters; the virtual-key code is reliable.
    if isinstance(vk, int) and (0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39):
        return chr(vk).lower()
    char = getattr(key, "char", None)
    return char.lower() if char else ""


class HotkeyMatcher:
    """Decides when a modifiers+key combination is pressed (no repeats while held)."""

    def __init__(self, spec: str) -> None:
        self.mods, self.key = parse_hotkey(spec)
        self._down: set[str] = set()
        self._fired = False

    def press(self, name: str) -> bool:
        if name in _MODIFIER_NAMES.values():
            self._down.add(name)
            return False
        if name == self.key and self._down == set(self.mods) and not self._fired:
            self._fired = True
            return True
        return False

    def release(self, name: str) -> None:
        self._down.discard(name)
        if name == self.key or name in _MODIFIER_NAMES.values():
            self._fired = False


class _PynputHotkey:
    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        from pynput import keyboard  # type: ignore[import-not-found]

        matcher = HotkeyMatcher(spec)

        def on_press(key: object) -> None:
            if matcher.press(key_name(key)):
                callback()

        def on_release(key: object) -> None:
            matcher.release(key_name(key))

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()


_MAC_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11,
    "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21,
    "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35,
    "l": 37, "j": 38, "k": 40, "n": 45, "m": 46, "space": 49, "enter": 36, "tab": 48,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}


class _MacHotkey:  # pragma: no cover - macOS only
    """Global hotkey via a listen-only Quartz event tap on the main run loop.

    Runs on the main thread's CFRunLoop (which Qt drives), avoiding the
    background-thread input-source crash pynput hits on recent macOS.
    """

    def __init__(self, spec: str, callback: Callable[[], None]) -> None:
        import Quartz  # type: ignore[import-not-found]

        mods, key = parse_hotkey(spec)
        if key not in _MAC_KEYCODES:
            raise ValueError(f"unsupported key on macOS: {key}")
        self._Q = Quartz
        self._keycode = _MAC_KEYCODES[key]
        flag = {
            "ctrl": Quartz.kCGEventFlagMaskControl,
            "shift": Quartz.kCGEventFlagMaskShift,
            "alt": Quartz.kCGEventFlagMaskAlternate,
            "cmd": Quartz.kCGEventFlagMaskCommand,
        }
        self._want = 0
        for m in mods:
            self._want |= flag[m]
        self._all = sum(flag.values())
        self._callback = callback

        def handler(proxy, etype, event, refcon):  # noqa: ANN001
            if etype == Quartz.kCGEventKeyDown:
                code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                flags = Quartz.CGEventGetFlags(event) & self._all
                repeat = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
                if code == self._keycode and flags == self._want and not repeat:
                    self._callback()
            elif etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self._tap, True)
            return event

        self._handler = handler
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
            handler,
            None,
        )
        if not self._tap:
            raise PermissionError("Accessibility permission is needed for the global hotkey")
        self._source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), self._source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)

    def stop(self) -> None:
        Q = self._Q
        Q.CGEventTapEnable(self._tap, False)
        Q.CFRunLoopRemoveSource(Q.CFRunLoopGetMain(), self._source, Q.kCFRunLoopCommonModes)


# ============================================================ permissions


MAC_PERMISSIONS = ("Microphone", "Screen Recording", "Accessibility")


def mac_permission_status() -> dict[str, str]:  # pragma: no cover - macOS only
    """Return {"Microphone": "granted"|"denied"|"not asked", ...}."""
    status: dict[str, str] = {}
    try:
        import AVFoundation  # type: ignore[import-not-found]

        mic = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
        status["Microphone"] = {3: "granted", 0: "not asked"}.get(int(mic), "denied")
    except Exception:
        status["Microphone"] = "unknown"
    try:
        import Quartz  # type: ignore[import-not-found]

        status["Screen Recording"] = "granted" if Quartz.CGPreflightScreenCaptureAccess() else "denied"
    except Exception:
        status["Screen Recording"] = "unknown"
    try:
        import ApplicationServices  # type: ignore[import-not-found]

        status["Accessibility"] = "granted" if ApplicationServices.AXIsProcessTrusted() else "denied"
    except Exception:
        status["Accessibility"] = "unknown"
    return status


def request_mac_permissions() -> dict[str, str]:  # pragma: no cover - macOS only
    """Show the macOS permission prompts, wait for answers, return the final status."""
    import AVFoundation  # type: ignore[import-not-found]
    import ApplicationServices  # type: ignore[import-not-found]
    import Quartz  # type: ignore[import-not-found]

    done = threading.Event()
    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVFoundation.AVMediaTypeAudio, lambda granted: done.set()
    )
    done.wait(120)
    if not Quartz.CGPreflightScreenCaptureAccess():
        Quartz.CGRequestScreenCaptureAccess()
    ApplicationServices.AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
    deadline = time.time() + 120
    while time.time() < deadline and mac_permission_status().get("Accessibility") != "granted":
        time.sleep(1)
    return mac_permission_status()


def mac_bundle_id() -> str:  # pragma: no cover - macOS only
    """Bundle ID macOS attributes our permissions to (the Python app bundle)."""
    try:
        from Foundation import NSBundle  # type: ignore[import-not-found]

        bid = NSBundle.mainBundle().bundleIdentifier()
        if bid:
            return str(bid)
    except Exception:
        pass
    return "org.python.python"


def reset_mac_permissions(bundle_id: str) -> list[str]:  # pragma: no cover - macOS only
    """Reset Microphone, Screen Recording and Accessibility for ``bundle_id``."""
    done = []
    for service in ("Microphone", "ScreenCapture", "Accessibility"):
        result = subprocess.run(["tccutil", "reset", service, bundle_id], capture_output=True, text=True)
        if result.returncode == 0:
            done.append(service)
    return done


# =============================================================== autostart

MAC_LAUNCH_AGENT_LABEL = "com.personalassistant.app"


def autostart_path() -> Path:
    if IS_WINDOWS:
        import os

        appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Personal Assistant.lnk"
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LAUNCH_AGENT_LABEL}.plist"


def start_menu_path() -> Path | None:
    if IS_WINDOWS:
        import os

        appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Personal Assistant.lnk"
    return None


def create_windows_shortcut(link: Path, target: Path, arguments: str, workdir: Path, description: str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)

    def q(value: object) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(" + q(link) + ");"
        f"$s.TargetPath={q(target)};$s.Arguments={q(arguments)};"
        f"$s.WorkingDirectory={q(workdir)};$s.Description={q(description)};$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        capture_output=True,
        creationflags=NO_WINDOW,
    )


def write_mac_launch_agent(path: Path, python: Path, main_script: Path, log_file: Path) -> None:
    import plistlib

    path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": MAC_LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(python), str(main_script)],
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    }
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)

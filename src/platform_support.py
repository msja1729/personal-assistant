"""Everything that differs between Windows and macOS lives in this module.

Step 0 only identifies the operating system. Later components add their
OS-specific code here (overlay click-through, hotkey, speech, permissions).
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


class UnsupportedPlatformError(RuntimeError):
    """Raised when the app is started on an operating system it does not support."""


def check_supported(platform: str | None = None) -> None:
    """Raise UnsupportedPlatformError unless running on Windows or macOS."""
    platform = platform or sys.platform
    if platform not in ("win32", "darwin"):
        raise UnsupportedPlatformError(
            f"Personal Assistant supports Windows 11 and macOS 14 or later (this is {platform})."
        )

"""Detect lock, the UAC desktop, and resume-from-sleep.

The reader is injected. Tests pass a fake. On Windows the real reader is
``win32_input_desktop``, which is not called from tests.
"""

from __future__ import annotations

import sys
import time
from typing import Callable

Reader = Callable[[], str]


class SessionMonitor:
    def __init__(self, reader: Reader | None = None, wall: Callable[[], float] | None = None, mono: Callable[[], float] | None = None):
        self._reader = reader
        self._wall = wall or time.time
        self._mono = mono or time.monotonic
        self._last_wall = self._wall()
        self._last_mono = self._mono()
        self._secure = False

    def poll(self) -> str | None:
        """Return lock, uac, unlock, resume, or None. A reader error fails open."""
        wall = self._wall()
        mono = self._mono()
        wall_delta = wall - self._last_wall
        mono_delta = mono - self._last_mono
        self._last_wall = wall
        self._last_mono = mono
        resumed = wall_delta > 5 and (wall_delta - mono_delta) > 2

        if self._reader is not None:
            try:
                state = self._reader()
            except Exception:
                state = "open"
            secure = state in ("locked", "lock", "uac", "sleep")
            if secure and not self._secure:
                self._secure = True
                return "uac" if state == "uac" else "lock"
            if not secure and self._secure:
                self._secure = False
                return "unlock"
        if resumed:
            return "resume"
        return None


def win32_input_desktop() -> str:
    """``open`` when the input desktop can be opened, else ``locked``.

    OpenInputDesktop fails on the lock screen and on the UAC secure desktop.
    Both mean the hook should come down. This function is Windows-only.
    """
    if sys.platform != "win32":
        return "open"
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)
    desktop_readobjects = 0x0001
    handle = user32.OpenInputDesktop(0, False, desktop_readobjects)
    if not handle:
        return "locked"
    user32.CloseDesktop(handle)
    return "open"

"""Notice when Windows has silently removed the keyboard hook.

Windows drops a low-level hook that is too slow to answer, and it does not
tell the program. The pynput thread keeps running, so the normal watchdog
still sees a live listener. This check compares three things on the
watchdog thread (never the hook thread):

* GetLastInputInfo: the system saw new input.
* GetAsyncKeyState: the set of keyboard keys held down changed.
* The hook's own event counter: did the hook see anything?

When keys changed and the system saw input but the hook saw nothing, that
is a miss. Two misses within a few seconds mean the hook is gone. A key that
merely looks stuck while the mouse moves never counts, because the set of
held keys does not change.
"""

from __future__ import annotations

import sys


class SilentHookDetector:
    def __init__(self, *, misses_needed: int = 2, window: float = 5.0, cooldown: float = 10.0):
        self.misses_needed = misses_needed
        self.window = window
        self.cooldown = cooldown
        self.reset()
        self._cooldown_until = float("-inf")

    def reset(self) -> None:
        self._prev = None
        self._misses: list[float] = []

    def sample(self, now: float, hook_events: int, last_input: int, keys_down: frozenset) -> bool:
        prev = self._prev
        self._prev = (hook_events, last_input, frozenset(keys_down))
        if prev is None:
            return False
        prev_events, prev_input, prev_keys = prev
        miss = keys_down != prev_keys and last_input != prev_input and hook_events == prev_events
        if now < self._cooldown_until:
            return False
        if not miss:
            return False
        self._misses = [moment for moment in self._misses if now - moment <= self.window]
        self._misses.append(now)
        if len(self._misses) < self.misses_needed:
            return False
        self._misses = []
        self._cooldown_until = now + self.cooldown
        return True


# Mouse buttons are 0x01, 0x02, 0x04, 0x05, 0x06. Everything from Backspace up is keyboard.
_KEYBOARD_VKS = tuple(range(0x08, 0xFF))


class WindowsInputProbe:
    """Read-only Win32 probes. They never send input."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._info = LASTINPUTINFO()
        self._info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        self._ctypes = ctypes

    def last_input(self) -> int:
        if not self._user32.GetLastInputInfo(self._ctypes.byref(self._info)):
            raise OSError("GetLastInputInfo failed")
        return int(self._info.dwTime)

    def keys_down(self) -> frozenset:
        state = self._user32.GetAsyncKeyState
        return frozenset(vk for vk in _KEYBOARD_VKS if state(vk) & 0x8000)


def windows_input_probe():
    if sys.platform != "win32":
        return None
    try:
        return WindowsInputProbe()
    except Exception:
        return None

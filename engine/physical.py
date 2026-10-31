"""Keys the user is actually holding.

Injected events must not be recorded here. GetAsyncKeyState cannot make this
decision: it is also true for keys Keyware injected, and off Windows the
platform probe reports every key as down. This set is updated only from
physical hook events. A later OS probe may remove a key that is already up
(Win+L, a missed key-up). It never adds one.
"""

from __future__ import annotations

from typing import Callable

from engine.detector import elapsed_ms
from engine.keys import MODIFIERS, canonicalize

# Skip a key that went down a moment ago. GetAsyncKeyState can lag the hook.
RECONCILE_GRACE_MS = 50


class PhysicalKeys:
    def __init__(self) -> None:
        self._down: dict[str, float] = {}

    def observe(self, key: str, pressed: bool, now: float) -> None:
        name = canonicalize(key)
        if name is None:
            return
        if pressed:
            self._down.setdefault(name, now)
            return
        self._down.pop(name, None)

    def holding(self, key: str) -> bool:
        name = canonicalize(key)
        return name is not None and name in self._down

    def reconcile(self, is_down: Callable[[str], bool], now: float) -> None:
        """Drop keys the OS says are up. Keys younger than the grace period stay."""
        for name, at in list(self._down.items()):
            if elapsed_ms(at, now) < RECONCILE_GRACE_MS:
                continue
            try:
                down = bool(is_down(name))
            except Exception:
                continue
            if not down:
                self._down.pop(name, None)


class GuardedInjector:
    """Do not inject a modifier the user is still holding.

    A key-up for a physically held Ctrl, Win, Alt, or Shift is what made
    Pulse drop its chord. Press is skipped for the same keys so a macro
    cannot add a second down that nothing is allowed to release.
    """

    def __init__(self, inner, physical: PhysicalKeys):
        self._inner = inner
        self._physical = physical

    def _blocked(self, key: str) -> bool:
        return key in MODIFIERS and self._physical.holding(key)

    def press(self, key: str) -> None:
        if self._blocked(key):
            return
        self._inner.press(key)

    def release(self, key: str) -> None:
        if self._blocked(key):
            return
        self._inner.release(key)

    def type_text(self, text: str) -> None:
        self._inner.type_text(text)

    def click(self, button: str) -> None:
        self._inner.click(button)

    def mouse_down(self, button: str) -> None:
        down = getattr(self._inner, "mouse_down", None)
        if down is None:
            self._inner.click(button)
        else:
            down(button)

    def mouse_up(self, button: str) -> None:
        up = getattr(self._inner, "mouse_up", None)
        if up is not None:
            up(button)

    def mask(self) -> None:
        mask = getattr(self._inner, "mask", None)
        if mask is not None:
            mask()

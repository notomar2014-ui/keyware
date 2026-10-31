"""Fold the Copilot key's three-key burst into one key called "copilot".

What the hardware sends (Microsoft's spec for Windows 11 Copilot keys, the
same on Dell, HP, Lenovo, ASUS and Surface keyboards):

    press:    Left Win down  (VK 0x5B, scan 0x5B, extended)
              Left Shift down (VK 0xA0, scan 0x2A)
              F23 down        (VK 0x86, scan 0x6E)
    release:  F23 up, Left Shift up, Left Win up

The three downs arrive about a millisecond apart, so a WH_KEYBOARD_LL hook
sees three ordinary key events. Windows itself treats Win+Shift+F23 as the
"open Copilot / Search / your chosen app" hotkey.

How Keyware handles it, only while a binding uses the Copilot key (or the
trigger recorder is listening), so an unbound key keeps its Windows default:

- Win and Shift are never held back. Real Win and Win+Shift shortcuts are
  not delayed at all (no timer, no buffering).
- When F23 goes down while Win and Shift went down within ``WINDOW_MS`` and
  no other key came in between, that is the Copilot key. F23 is swallowed, so
  the Windows hotkey never fires (no Copilot or Search window). The caller
  then injects Shift up, a mask key and Win up, so Windows does not see Win or
  Shift as still held and the Start menu does not open.
- The physical Win, Shift and F23 releases (and any auto-repeat of them) are
  swallowed afterwards in whatever order they arrive.
- F23 on its own (a macropad set to F23) is still F23.

Pure state machine: no threads, no OS calls. The service feeds it events and
acts on the decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Win and Shift must both have gone down this recently before F23.
WINDOW_MS = 300
# Absorbed keys we have not heard from for this long are forgotten (fail open).
STALE_MS = 30_000

PASS = "pass"  # handle the event as usual
SWALLOW = "swallow"  # swallow it, nothing else
DOWN = "copilot_down"  # swallow; release Win/Shift in the OS; feed ("copilot", True)
REPEAT = "copilot_repeat"  # swallow; feed ("copilot", True) as auto-repeat
UP = "copilot_up"  # swallow; feed ("copilot", False)


@dataclass
class CopilotFolder:
    enabled: bool = False
    win_at: float | None = None
    shift_at: float | None = None
    other_at: float | None = None
    active: bool = False
    absorbed: set[str] = field(default_factory=set)
    last_event: float = 0.0
    detected: int = 0

    def reset(self) -> None:
        self.win_at = None
        self.shift_at = None
        self.other_at = None
        self.active = False
        self.absorbed.clear()

    def busy(self) -> bool:
        """True while a Copilot press is in progress and must be finished."""
        return self.active or bool(self.absorbed)

    def feed(self, key: str, pressed: bool, now: float) -> str:
        self.last_event = now
        if key in ("win", "shift"):
            return self._modifier(key, pressed, now)
        if key == "f23":
            return self._f23(pressed, now)
        if pressed:
            self.other_at = now
        return PASS

    def tick(self, now: float) -> bool:
        """Forget a press whose key-ups never came. True when something was dropped."""
        if not self.busy():
            return False
        if (now - self.last_event) * 1000.0 < STALE_MS:
            return False
        self.reset()
        return True

    # ------------------------------------------------------------------
    def _modifier(self, key: str, pressed: bool, now: float) -> str:
        attr = "win_at" if key == "win" else "shift_at"
        if pressed:
            if key in self.absorbed:
                return SWALLOW  # auto-repeat of the Copilot key's own Win or Shift
            if getattr(self, attr) is None:
                setattr(self, attr, now)
            return PASS
        setattr(self, attr, None)
        if key in self.absorbed:
            self.absorbed.discard(key)
            return SWALLOW
        return PASS

    def _f23(self, pressed: bool, now: float) -> str:
        if pressed:
            if self.active:
                return REPEAT
            if self.enabled and self._burst(now):
                self.active = True
                self.absorbed = {"win", "shift", "f23"}
                self.win_at = None
                self.shift_at = None
                self.detected += 1
                return DOWN
            self.other_at = now
            return PASS
        if self.active:
            self.active = False
            self.absorbed.discard("f23")
            return UP
        if "f23" in self.absorbed:
            self.absorbed.discard("f23")
            return SWALLOW
        return PASS

    def _burst(self, now: float) -> bool:
        if self.win_at is None or self.shift_at is None:
            return False
        first = min(self.win_at, self.shift_at)
        if (now - first) * 1000.0 > WINDOW_MS:
            return False
        if self.other_at is not None and self.other_at >= first:
            return False
        return True

"""Hotkey recorder for the trigger and key fields.

Fed key events (from the low-level hook, or from Tk when there is no hook),
it reports what is held right now and, when the keys are released, the
recorded trigger. It never injects anything.

Rules:
- Modifiers plus one key -> chord (Ctrl + Win + Shift + R).
- One key alone -> tap. Held for HOLD_MS or longer -> hold, with its length.
- Esc alone cancels. Backspace alone clears. With a modifier both record.
- Leader mode: pressing the leader key first is skipped; the next key is recorded.
- single=True records one key and ignores modifiers (leader key field).
- Gives up after ``timeout`` seconds so the keyboard is never held hostage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.keys import MODIFIERS, format_key, format_keys, ordered_keys

HOLD_MS = 500
MIN_HOLD_MS = 300
MAX_HOLD_MS = 5000
DRAIN_SECONDS = 1.5


@dataclass
class RecordEvent:
    kind: str  # live | done | cancel | clear
    text: str = ""
    keys: tuple[str, ...] = ()
    mode: str = ""
    hold_ms: int = 0


@dataclass
class KeyRecorder:
    leader_key: str | None = None
    single: bool = False
    timeout: float = 15.0
    started: float | None = None
    down: dict = field(default_factory=dict)
    main: str | None = None
    main_at: float = 0.0
    main_mods: tuple[str, ...] = ()
    mods_seen: set = field(default_factory=set)
    after_leader: bool = False
    result_sent: bool = False
    finished: bool = False
    _last_hold_tenth: int = -1
    result_at: float | None = None

    # ------------------------------------------------------------------
    def feed(self, key: str, pressed: bool, now: float) -> list[RecordEvent]:
        if self.finished:
            return []
        if self.started is None:
            self.started = now
        if pressed:
            return self._press(key, now)
        return self._release(key, now)

    def tick(self, now: float) -> list[RecordEvent]:
        if self.finished:
            return []
        if self.started is None:
            self.started = now
        if now - self.started >= self.timeout:
            self.finished = True
            if self.result_sent:
                return []
            self.result_sent = True
            return [RecordEvent(kind="cancel", text="Recording timed out.")]
        if self.result_sent:
            # Waiting for the last key-ups. A lost key-up must not keep
            # swallowing the keyboard.
            if self.result_at is None:
                self.result_at = now
            elif now - self.result_at >= DRAIN_SECONDS:
                self.finished = True
            return []
        if self.main and not self.main_mods and not self.single and not self.result_sent and self.main in self.down:
            held_ms = (now - self.main_at) * 1000
            if held_ms >= HOLD_MS:
                tenth = int(held_ms // 100)
                if tenth != self._last_hold_tenth:
                    self._last_hold_tenth = tenth
                    return [RecordEvent(kind="live", text=f"Hold {format_key(self.main)} · {_hold_ms(held_ms)} ms")]
        return []

    # ------------------------------------------------------------------
    def _held_mods(self) -> tuple[str, ...]:
        return tuple(name for name in ordered_keys(list(self.down)) if name in MODIFIERS)

    def _press(self, key: str, now: float) -> list[RecordEvent]:
        if key in self.down:  # auto-repeat
            return []
        if self.result_sent:
            self.down[key] = now
            return []
        mods = self._held_mods()
        if self.main is None and not mods and not self.down:
            if key == "esc":
                return self._end("cancel", "Recording cancelled.")
            if key == "backspace":
                return self._end("clear", "Cleared.")
        if key == "esc" and {"ctrl", "alt", "shift"} <= set(mods):  # panic chord
            return self._end("cancel", "Recording cancelled.")
        self.down[key] = now
        if key in MODIFIERS:
            self.mods_seen.add(key)
            if self.main is None:
                shown = "" if self.single else format_keys(list(self._held_mods())) + " + …"
                return [RecordEvent(kind="live", text=shown or "…")]
            return []
        if self.main is None:
            self.main = key
            self.main_at = now
            self.main_mods = () if self.single else mods
            self._last_hold_tenth = -1
            return [RecordEvent(kind="live", text=format_keys(list(self.main_mods) + [key]))]
        return []

    def _release(self, key: str, now: float) -> list[RecordEvent]:
        self.down.pop(key, None)
        out: list[RecordEvent] = []
        if not self.result_sent:
            if self.main is not None and key == self.main:
                out.extend(self._finish_main(now))
            elif self.main is None and key in MODIFIERS and not self.down and self.mods_seen and not self.single:
                keys = tuple(ordered_keys(list(self.mods_seen)))
                out.append(self._result(keys, "chord"))
            elif self.main is None and not self.down and not self.single:
                out.append(RecordEvent(kind="live", text="Press keys…"))
        if self.result_sent and not self.down:
            self.finished = True
        return out

    def _finish_main(self, now: float) -> list[RecordEvent]:
        key = self.main
        held_ms = (now - self.main_at) * 1000
        if self.leader_key and key == self.leader_key and not self.main_mods and not self.after_leader:
            self.after_leader = True
            self.main = None
            self.mods_seen.clear()
            return [RecordEvent(kind="live", text=f"{format_key(key)} → …")]
        if self.single:
            return [self._result((key,), "tap")]
        keys = tuple(ordered_keys(list(self.main_mods) + [key]))
        if self.main_mods:
            return [self._result(keys, "chord")]
        if self.after_leader:
            return [self._result(keys, "leader")]
        if held_ms >= HOLD_MS:
            return [self._result(keys, "hold", _hold_ms(held_ms))]
        return [self._result(keys, "tap")]

    def _result(self, keys, mode: str, hold_ms: int = 0) -> RecordEvent:
        self.result_sent = True
        if not self.down:
            self.finished = True
        return RecordEvent(kind="done", text=format_keys(list(keys)), keys=tuple(keys), mode=mode, hold_ms=hold_ms)

    def _end(self, kind: str, text: str) -> list[RecordEvent]:
        self.result_sent = True
        self.finished = not self.down
        return [RecordEvent(kind=kind, text=text)]


def _hold_ms(held_ms: float) -> int:
    rounded = int(round(held_ms / 100.0)) * 100
    return max(MIN_HOLD_MS, min(MAX_HOLD_MS, rounded))

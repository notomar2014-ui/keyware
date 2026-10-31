"""Pure input state machine.

No threads, no OS hooks, no audio devices. Callers feed key events and a
clock. The result says whether that one event must be swallowed and which
actions, injections, or cues should run afterwards on another thread.

Replays that must stay in order are `inject_down` / `inject_up` effects.
A full tap of a key that is already released is a single `inject` effect.
The service plays those effects on one queue so a later key cannot overtake
an earlier one. Bare tap, hold, and tap-dance bindings apply only when no
modifier is held, so Ctrl+P still types.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable

from engine import PANIC_KEYS
from engine.keys import MODIFIERS, canonicalize, format_key, format_keys
from engine.models import DEFAULT_PAUSE_HOTKEY, Action, Binding, Config, Profile, trigger_text

# A key we still think is down after this long, with no repeat, is dropped.
STALE_HELD_MS = 30_000


def elapsed_ms(start: float, now: float) -> int:
    """Milliseconds between two perf_counter readings, rounded to the nearest ms.

    Rounding absorbs binary float error so a hold of exactly 800 ms is not
    reported as 799.999 ms.
    """
    return int(round((now - start) * 1000.0))


@dataclass
class Effect:
    kind: str
    message: str = ""
    binding_id: str = ""
    action: Action | None = None
    hz: int = 0
    key: str = ""
    keys: tuple[str, ...] = ()


def _clicks_while_held(binding: Binding) -> bool:
    action = binding.action
    return action is not None and action.type == "auto_click" and action.click_mode == "hold"


@dataclass
class HandleResult:
    suppress: bool
    effects: list[Effect] = field(default_factory=list)


# A swallowed key with no event (not even auto-repeat) for this long is
# treated as released when the OS also says it is up.
SWALLOWED_STALE_MS = 2000


@dataclass
class _Down:
    at: float
    suppressed: bool
    consumed: bool
    profile_id: str
    fired_ids: set[str] = field(default_factory=set)
    replayed: bool = False


@dataclass
class _Dance:
    key: str
    count: int
    deadline: float


class InputDetector:
    def __init__(self, config: Config):
        self.config = config
        self.enabled = False
        self.capture = False
        self.global_profile: Profile | None = None
        self.active: Profile | None = None
        self.active_id = ""
        self.paused = False
        self.down_keys: set[str] = set()
        self.modifiers: set[str] = set()
        self.held: dict[str, _Down] = {}
        self.seen: dict[str, float] = {}
        self.replayed: set[str] = set()
        self.swallow_release: set[str] = set()
        self.dance: _Dance | None = None
        self.leader_until: float | None = None
        self.right_alt = False
        self._chords: dict[tuple[frozenset[str], str], Binding] = {}
        self._by_key: dict[str, list[Binding]] = {}
        self._leaders: dict[str, Binding] = {}
        # Trigger key -> binding id of a "while held" auto-click it started.
        self.autoclick_keys: dict[str, str] = {}
        self.active_binding_ids: frozenset[str] = frozenset()

    def set_enabled(self, enabled: bool) -> list[Effect]:
        enabled = bool(enabled)
        if not enabled:
            if not self.enabled and not self.paused:
                return []
            self.paused = False
            effects = self.release_replayed()
            self._clear_transient()
            self.enabled = False
            return effects
        if enabled == self.enabled and not self.paused:
            return []
        self.enabled = True
        self.paused = False
        return []

    def set_paused(self, paused: bool) -> list[Effect]:
        """Stop remaps without forgetting which physical keys are down.

        The hook stays installed so the pause chord can resume.
        """
        paused = bool(paused)
        if paused == self.paused:
            return []
        self.paused = paused
        if not paused:
            return []
        effects = self.release_replayed()
        self.held.clear()
        self.autoclick_keys.clear()
        self.dance = None
        self.leader_until = None
        self.capture = False
        return effects

    def snapshot_state(self) -> dict:
        return {
            "enabled": self.enabled,
            "paused": self.paused,
            "capture": self.capture,
            "active_id": self.active_id,
            "down_keys": set(self.down_keys),
            "modifiers": set(self.modifiers),
            "held": copy.deepcopy(self.held),
            "seen": dict(self.seen),
            "replayed": set(self.replayed),
            "swallow_release": set(self.swallow_release),
            "dance": copy.deepcopy(self.dance),
            "leader_until": self.leader_until,
            "right_alt": self.right_alt,
        }

    def restore_state(self, state: dict) -> None:
        self.enabled = state["enabled"]
        self.paused = state["paused"]
        self.capture = state["capture"]
        self.active_id = state["active_id"]
        self.down_keys = set(state["down_keys"])
        self.modifiers = set(state["modifiers"])
        self.held = state["held"]
        self.seen = dict(state["seen"])
        self.replayed = set(state["replayed"])
        self.swallow_release = set(state["swallow_release"])
        self.dance = state["dance"]
        self.leader_until = state["leader_until"]
        self.right_alt = state["right_alt"]

    def observe_passthrough(self, key: str, pressed: bool, now: float) -> list[Effect]:
        """Track a key while an elevated window has the foreground.

        Nothing is swallowed. Replayed keys are released so a modifier
        Keyware injected cannot stay down across the elevated window.
        """
        name = canonicalize(key)
        if name is None:
            return []
        effects: list[Effect] = []
        if pressed:
            self.down_keys.add(name)
            self.seen[name] = now
            if name in MODIFIERS:
                self.modifiers.add(name)
            return effects
        self.down_keys.discard(name)
        self.modifiers.discard(name)
        self.seen.pop(name, None)
        self.held.pop(name, None)
        self.swallow_release.discard(name)
        if name in self.replayed:
            self.replayed.discard(name)
            effects.append(Effect(kind="inject_up", key=name))
        return effects

    def set_profiles(self, global_profile: Profile, active: Profile) -> list[Effect]:
        first = self.active is None
        changed = active.id != self.active_id
        self.global_profile = global_profile
        self.active = active
        self.active_id = active.id
        self._reindex()
        if not changed or first:
            return []
        effects = self._flush_held_characters()
        for down in self.held.values():
            down.consumed = True
        self.dance = None
        self.leader_until = None
        effects.append(Effect(kind="log", message=f"Profile → {active.name}"))
        return effects

    def begin_capture(self) -> None:
        self.capture = True

    def cancel_capture(self) -> None:
        self.capture = False

    def panic(self) -> list[Effect]:
        effects = self.release_replayed()
        self.enabled = False
        self._clear_transient()
        effects.append(Effect(kind="panic", message="Panic switch pressed. Hooks released."))
        return effects

    def disarm(self) -> None:
        """Drop replay bookkeeping after dry run so a later key-up is not injected."""
        for down in self.held.values():
            down.consumed = True
            down.suppressed = False
            down.replayed = False
            down.fired_ids.clear()
        self.replayed.clear()
        self.swallow_release.clear()
        self.dance = None
        self.leader_until = None

    def suspend_tracking(self) -> None:
        """Forget in-progress keys when the secure desktop takes over.

        Replayed keys are released by the caller first. Physical keys come
        back as new events after the hook is installed again.
        """
        self._clear_transient()

    def release_replayed(self) -> list[Effect]:
        """Key-ups for anything we injected and have not released yet."""
        effects = [Effect(kind="inject_up", key=key) for key in sorted(self.replayed)]
        self.replayed.clear()
        return effects

    def handle(
        self,
        key: str,
        pressed: bool,
        now: float,
        *,
        output_busy: bool = False,
        altgr: bool = False,
    ) -> HandleResult:
        name = canonicalize(key)
        if name is None:
            return HandleResult(False, [])
        if name == "alt":
            if pressed and altgr:
                self.right_alt = True
            elif (not pressed) and altgr:
                self.right_alt = False
        already_down = name in self.down_keys
        if pressed:
            result = self._maybe_mask(name, self._press(name, now))
        else:
            result = self._release(name, now)
        if self.paused:
            return result
        if output_busy and not result.suppress and not (pressed and already_down):
            adopted = self._adopt(name, pressed, now)
            if adopted is not None:
                adopted.effects = list(result.effects) + adopted.effects
                return adopted
        return result

    def tick(self, now: float) -> list[Effect]:
        effects: list[Effect] = []
        if not self.enabled or self.paused:
            return effects
        for key, down in self.held.items():
            if down.consumed and not down.fired_ids:
                continue
            if down.profile_id != self.active_id:
                down.consumed = True
                continue
            holds = self._holds(key)
            for binding in holds:
                if binding.id in down.fired_ids:
                    continue
                if elapsed_ms(down.at, now) < binding.hold_ms:
                    continue
                down.fired_ids.add(binding.id)
                down.consumed = True
                tier = len(down.fired_ids)
                effects.extend(self._fire(binding, tier=tier, key=key))
        if self.dance is not None and elapsed_ms(self.dance.deadline, now) >= 0:
            effects.extend(self._commit_dance())
        if self.leader_until is not None and elapsed_ms(self.leader_until, now) >= 0:
            self.leader_until = None
            effects.append(Effect(kind="log", message="Leader window timed out."))
        effects.extend(self._expire_stuck(now))
        return effects

    def reconcile(self, is_down: Callable[[str], bool], now: float) -> list[Effect]:
        """Drop keys the OS says are up. Used after Win+L and other lost key-ups."""
        if not self.enabled:
            return []
        effects: list[Effect] = []
        for key in list(self.down_keys):
            down = self.held.get(key)
            if down is not None and elapsed_ms(down.at, now) < 50:
                continue
            if down is not None and down.suppressed:
                # Keyware swallowed this key-down, so the OS key state never saw
                # it and GetAsyncKeyState reports "up" while the user still holds
                # it. Forget it only when no event (auto-repeat) came for a while.
                last = self.seen.get(key, down.at)
                if elapsed_ms(last, now) < SWALLOWED_STALE_MS:
                    continue
            try:
                physically_down = bool(is_down(key))
            except Exception:
                continue
            if physically_down:
                continue
            effects.extend(self._release(key, now).effects)
        return effects

    def _press(self, key: str, now: float) -> HandleResult:
        if not self.enabled:
            return HandleResult(False, [])

        if key in self.down_keys:
            self.seen[key] = now
            if self.paused:
                return HandleResult(False, [])
            down = self.held.get(key)
            return HandleResult(bool(down and down.suppressed), [])

        self.down_keys.add(key)
        self.seen[key] = now
        if key in MODIFIERS:
            self.modifiers.add(key)

        if self._pause_hit(key):
            suppress = key not in MODIFIERS
            if suppress:
                self.held[key] = _Down(now, True, True, self.active_id)
                self.swallow_release.add(key)
            return HandleResult(suppress, [Effect(kind="pause_toggle")])

        if self.paused:
            return HandleResult(False, [])

        if self._panic_satisfied(key):
            effects = self.panic()
            if key == "esc":
                return HandleResult(True, effects)
            return HandleResult(False, effects)

        if self.capture:
            return self._capture_press(key, now)

        pending: list[Effect] = []
        if self.dance is not None and self.dance.key != key:
            pending.extend(self._commit_dance())
        # A modifier must stay available for panic and for real shortcuts.
        if key not in MODIFIERS:
            pending.extend(self._flush_rollover(key, now))

        if self._leader_window_open(now) and key != self.config.leader_key and key not in MODIFIERS:
            self.leader_until = None
            binding = self._leaders.get(key)
            if binding is not None:
                self.held[key] = _Down(now, True, True, self.active_id)
                return HandleResult(True, pending + self._fire(binding, key=key))
            return self._finish(
                key,
                now,
                HandleResult(False, pending + [Effect(kind="log", message="Leader window closed.")]),
            )

        if self.config.leader_key and key == self.config.leader_key:
            self.held[key] = _Down(now, True, True, self.active_id)
            return HandleResult(True, pending)

        if self._pulse_blocked(key):
            return self._finish(key, now, HandleResult(False, pending))

        chord = None if key in MODIFIERS else self._chords.get((self._match_modifiers(), key))
        if chord is not None:
            self.held[key] = _Down(now, True, True, self.active_id)
            return HandleResult(True, pending + self._fire(chord, key=key))

        if key not in MODIFIERS and not self._match_modifiers() and self._by_key.get(key):
            if any(effect.kind == "inject_down" for effect in pending):
                self.held[key] = _Down(now, True, True, self.active_id, replayed=True)
                self.replayed.add(key)
                return HandleResult(True, pending + [Effect(kind="inject_down", key=key)])
            held_click = next(
                (b for b in self._by_key[key] if b.mode == "tap" and _clicks_while_held(b)),
                None,
            )
            if held_click is not None:
                # "While held" auto-click starts on key-down, not on release.
                self.held[key] = _Down(now, True, True, self.active_id)
                return HandleResult(True, pending + self._fire(held_click, key=key))
            self.held[key] = _Down(now, True, False, self.active_id)
            return HandleResult(True, pending)

        return self._finish(key, now, HandleResult(False, pending))

    def _release(self, key: str, now: float) -> HandleResult:
        clicking = self.autoclick_keys.pop(key, None)
        result = self._release_key(key, now)
        if clicking is not None:
            result.effects.insert(0, Effect(kind="autoclick_stop", binding_id=clicking, key=key))
        return result

    def _release_key(self, key: str, now: float) -> HandleResult:
        self.down_keys.discard(key)
        self.modifiers.discard(key)
        self.seen.pop(key, None)
        if key in self.swallow_release:
            self.swallow_release.discard(key)
            self.held.pop(key, None)
            if key in self.replayed:
                self.replayed.discard(key)
            return HandleResult(True, [])
        down = self.held.pop(key, None)
        if self.paused:
            return HandleResult(False, [])
        if key in self.replayed:
            self.replayed.discard(key)
            if down is not None:
                down.replayed = False
            return HandleResult(True, [Effect(kind="inject_up", key=key)])
        if down is None or not self.enabled or not down.suppressed:
            return HandleResult(False, [])

        if self.config.leader_key and key == self.config.leader_key:
            duration_ms = elapsed_ms(down.at, now)
            effects: list[Effect] = []
            if duration_ms < self.config.tap_max_ms:
                self.leader_until = now + self.config.leader_window_ms / 1000
                effects.append(Effect(
                    kind="log",
                    message=f"Leader window open ({self.config.leader_window_ms} ms).",
                ))
            else:
                effects.append(Effect(kind="log", message="Leader key held. Window not opened."))
            return HandleResult(True, effects)

        if down.consumed:
            if self.dance is not None and self.dance.key == key:
                return HandleResult(True, self._commit_dance())
            return HandleResult(True, [])

        duration_ms = elapsed_ms(down.at, now)
        has_dance = self._dance_max(key) >= 2
        has_tap = any(binding.mode == "tap" for binding in self._by_key.get(key, []))
        has_hold = bool(self._holds(key))

        if has_dance and duration_ms < self.config.tap_max_ms:
            return HandleResult(True, self._note_tap(key, now))

        effects: list[Effect] = []
        if self.dance is not None and self.dance.key == key:
            effects.extend(self._commit_dance())

        if has_dance and duration_ms >= self.config.tap_max_ms and not down.fired_ids:
            if not any(effect.kind == "inject" and effect.key == key for effect in effects):
                effects.append(Effect(kind="inject", key=key))
            return HandleResult(True, effects)

        if duration_ms < self.config.tap_max_ms and has_tap and not has_dance:
            binding = next(binding for binding in self._by_key[key] if binding.mode == "tap")
            effects.extend(self._fire(binding, key=key))
            return HandleResult(True, effects)

        if has_hold and not has_tap and not has_dance and not down.fired_ids:
            effects.append(Effect(kind="inject", key=key))
            return HandleResult(True, effects)

        return HandleResult(True, effects)

    def _capture_press(self, key: str, now: float) -> HandleResult:
        if key == "esc":
            self.capture = False
            self.held[key] = _Down(now, True, True, self.active_id)
            return HandleResult(True, [Effect(kind="capture_cancel", message="Capture cancelled.")])
        if key in MODIFIERS:
            return HandleResult(False, [])
        self.capture = False
        keys = tuple(name for name in ("ctrl", "win", "alt", "shift") if name in self.modifiers) + (key,)
        self.held[key] = _Down(now, True, True, self.active_id)
        return HandleResult(True, [Effect(kind="capture", keys=keys, message=format_keys(keys))])

    def _note_tap(self, key: str, now: float) -> list[Effect]:
        if self.dance is None or self.dance.key != key:
            self.dance = _Dance(key=key, count=0, deadline=now)
        self.dance.count += 1
        self.dance.deadline = now + self.config.tap_dance_window_ms / 1000.0
        if self.dance.count >= self._dance_max(key):
            return self._commit_dance()
        return [Effect(kind="log", message=f"Tap {self.dance.count} on {format_key(key)}.")]

    def _commit_dance(self) -> list[Effect]:
        dance = self.dance
        self.dance = None
        if dance is None:
            return []
        count = dance.count
        binding = self._binding_for_taps(dance.key, count)
        if binding is not None:
            effects = self._fire(binding, key=dance.key)
            effects.append(Effect(kind="log", message=f"Tap-dance ×{count} on {format_key(dance.key)}."))
            return effects
        if count == 1:
            return [Effect(kind="inject", key=dance.key)]
        return [Effect(kind="log", message=f"No action for {count} taps on {format_key(dance.key)}.")]

    def _fire(self, binding: Binding, tier: int | None = None, key: str = "") -> list[Effect]:
        if key and _clicks_while_held(binding):
            self.autoclick_keys[key] = binding.id
        effects = [
            Effect(
                kind="fire",
                binding_id=binding.id,
                action=binding.action,
                message=self._fire_message(binding),
                key=key,
            ),
            Effect(kind="log", message=self._fire_message(binding)),
        ]
        if binding.mode == "hold" and self.config.audio_enabled:
            hz = self.config.audio_tier1_hz if tier == 1 else self.config.audio_tier2_hz
            effects.append(Effect(kind="audio", hz=hz))
        return effects

    def _fire_message(self, binding: Binding) -> str:
        return f"Fired {trigger_text(binding, self.config.leader_key)} → {binding.label or trigger_text(binding, self.config.leader_key)}"

    def _finish(self, key: str, now: float, result: HandleResult) -> HandleResult:
        """Keep a passthrough key behind any replay already decided in this event."""
        if result.suppress or key in MODIFIERS:
            return result
        if not any(effect.kind in ("inject", "inject_down") for effect in result.effects):
            return result
        self.held[key] = _Down(now, True, True, self.active_id, replayed=True)
        self.replayed.add(key)
        if key in MODIFIERS:
            self.modifiers.add(key)
        result.effects.append(Effect(kind="inject_down", key=key))
        result.suppress = True
        return result

    def _maybe_mask(self, key: str, result: HandleResult) -> HandleResult:
        """A swallowed key with Win or Alt down must not open the Start menu or menu bar."""
        if not result.suppress or key in MODIFIERS:
            return result
        if any(effect.kind == "panic" for effect in result.effects):
            return result
        if "win" not in self.modifiers and "alt" not in self.modifiers:
            return result
        if any(effect.kind == "mask" for effect in result.effects):
            return result
        result.effects.append(Effect(kind="mask"))
        return result

    def _adopt(self, key: str, pressed: bool, now: float) -> HandleResult | None:
        """While a replay is still queued, do not let a physical key jump ahead of it."""
        if pressed:
            if key in self.replayed:
                return HandleResult(True, [])
            self.held[key] = _Down(now, True, True, self.active_id, replayed=True)
            self.replayed.add(key)
            self.down_keys.add(key)
            self.seen[key] = now
            if key in MODIFIERS:
                self.modifiers.add(key)
            return HandleResult(True, [Effect(kind="inject_down", key=key)])
        if key in MODIFIERS:
            return HandleResult(True, [Effect(kind="inject_up", key=key)])
        return None

    def _flush_rollover(self, new_key: str, now: float) -> list[Effect]:
        window = int(getattr(self.config, "rollover_ms", 0) or 0)
        if window <= 0:
            return []
        effects: list[Effect] = []
        for key, down in list(self.held.items()):
            if key == new_key or not self._can_flush_as_character(key, down):
                continue
            if elapsed_ms(down.at, now) > window:
                continue
            effects.append(Effect(kind="inject_down", key=key))
            down.replayed = True
            down.consumed = True
            self.replayed.add(key)
        return effects

    def _flush_held_characters(self) -> list[Effect]:
        effects: list[Effect] = []
        for key, down in list(self.held.items()):
            if not self._can_flush_as_character(key, down):
                continue
            effects.append(Effect(kind="inject_down", key=key))
            down.replayed = True
            down.consumed = True
            self.replayed.add(key)
        if self.dance is not None:
            effects.extend(self._commit_dance())
        return effects

    def _can_flush_as_character(self, key: str, down: _Down) -> bool:
        if not down.suppressed or down.consumed or down.replayed or down.fired_ids:
            return False
        bindings = self._by_key.get(key, [])
        has_tap = any(binding.mode == "tap" for binding in bindings)
        has_dance = any(binding.mode == "tap_dance" for binding in bindings)
        has_hold = any(binding.mode == "hold" for binding in bindings)
        if has_tap and not has_hold and not has_dance:
            return False
        return has_hold or has_dance

    def _expire_stuck(self, now: float) -> list[Effect]:
        effects: list[Effect] = []
        for key in list(self.down_keys):
            seen = self.seen.get(key)
            if seen is None or elapsed_ms(seen, now) < STALE_HELD_MS:
                continue
            self.down_keys.discard(key)
            self.modifiers.discard(key)
            self.seen.pop(key, None)
            self.held.pop(key, None)
            if self.dance is not None and self.dance.key == key:
                self.dance = None
            if key in self.replayed:
                self.replayed.discard(key)
                effects.append(Effect(kind="inject_up", key=key))
            effects.append(Effect(kind="log", message="Released a key that stayed down too long."))
        return effects

    def _binding_for_taps(self, key: str, count: int) -> Binding | None:
        for binding in self._by_key.get(key, []):
            if count == 1 and binding.mode == "tap":
                return binding
            if binding.mode == "tap_dance" and binding.taps == count:
                return binding
        return None

    def _dance_max(self, key: str) -> int:
        highest = 0
        for binding in self._by_key.get(key, []):
            if binding.mode == "tap":
                highest = max(highest, 1)
            elif binding.mode == "tap_dance":
                highest = max(highest, binding.taps)
        return highest

    def _holds(self, key: str) -> list[Binding]:
        holds = [binding for binding in self._by_key.get(key, []) if binding.mode == "hold"]
        return sorted(holds, key=lambda binding: binding.hold_ms)

    def _leader_window_open(self, now: float) -> bool:
        return self.leader_until is not None and elapsed_ms(now, self.leader_until) > 0

    def _panic_satisfied(self, key: str) -> bool:
        if not PANIC_KEYS <= (self.modifiers | self.down_keys | {key}):
            return False
        return "esc" in self.down_keys or key == "esc"

    def _match_modifiers(self) -> frozenset[str]:
        """Modifiers that count for chords. AltGr's injected-looking Ctrl does not."""
        mods = set(self.modifiers)
        if self.right_alt and "ctrl" in mods:
            mods.discard("ctrl")
        return frozenset(mods)

    def _pause_hit(self, key: str) -> bool:
        hotkey = list(getattr(self.config, "pause_hotkey", None) or DEFAULT_PAUSE_HOTKEY)
        wanted: list[str] = []
        for name in hotkey:
            canonical = canonicalize(name)
            if canonical is None:
                return False
            wanted.append(canonical)
        if not wanted:
            return False
        current = set(self.modifiers)
        current.add(key)
        return frozenset(wanted) == frozenset(current)

    def _pulse_blocked(self, key: str) -> bool:
        """Exact Pulse combos pass through. A longer chord is not a prefix match."""
        if not getattr(self.config, "respect_pulse", True):
            return False
        current = set(self.modifiers)
        current.add(key)
        for combo in getattr(self.config, "pulse_hotkeys", []) or []:
            wanted: set[str] = set()
            valid = True
            for name in combo:
                canonical = canonicalize(str(name))
                if canonical is None:
                    valid = False
                    break
                wanted.add(canonical)
            if valid and wanted and current == wanted:
                return True
        return False

    def _clear_transient(self) -> None:
        self.down_keys.clear()
        self.modifiers.clear()
        self.held.clear()
        self.seen.clear()
        self.replayed.clear()
        self.swallow_release.clear()
        self.dance = None
        self.leader_until = None
        self.capture = False
        self.right_alt = False
        self.autoclick_keys.clear()

    def _reindex(self) -> None:
        bindings: list[Binding] = []
        global_profile = self.global_profile
        active = self.active
        if global_profile is not None:
            bindings.extend(binding for binding in global_profile.bindings if binding.enabled)
        if active is not None and (global_profile is None or active.id != global_profile.id):
            overlay = [binding for binding in active.bindings if binding.enabled]
            overlay_sigs = {binding.signature() for binding in overlay}
            bindings = [binding for binding in bindings if binding.signature() not in overlay_sigs]
            bindings.extend(overlay)

        chords: dict[tuple[frozenset[str], str], Binding] = {}
        by_key: dict[str, list[Binding]] = {}
        leaders: dict[str, Binding] = {}
        for binding in bindings:
            if binding.mode == "chord":
                mods = frozenset(name for name in binding.keys if name in MODIFIERS)
                triggers = [name for name in binding.keys if name not in MODIFIERS]
                if len(triggers) == 1:
                    chords[(mods, triggers[0])] = binding
            elif binding.mode == "leader":
                if binding.keys:
                    leaders[binding.keys[0]] = binding
            elif binding.keys and binding.keys[0] not in MODIFIERS:
                by_key.setdefault(binding.keys[0], []).append(binding)
        self._chords = chords
        self._by_key = by_key
        self._leaders = leaders
        self.active_binding_ids = frozenset(binding.id for binding in bindings)

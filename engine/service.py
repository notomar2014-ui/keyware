"""Facade the UI and the tests both drive.

The hook thread only calls handle_input. That method updates the detector
under a short lock and queues any work. Macros, audio, file saves, and
key replay never run inside the hook callback.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from engine.actions import ActionExecutor, Opener, Injector
from engine.autoclick import AutoClicker, ClickJob
from engine.audio_cue import AudioCue
from engine.config_store import ConfigStore
from engine.context_tracker import ContextTracker
from engine.detector import Effect, InputDetector
from engine.hook_engine import HookUnavailable
from engine.models import Binding, Profile, new_id, validate_binding, validate_leader_key
from engine.physical import GuardedInjector, PhysicalKeys
from engine.profiles import match_profile, process_basename
from engine.session import SessionMonitor
from engine.startup import StartupManager
from engine.watchdog import decide_hook
from engine.hook_health import SilentHookDetector
from engine import HOLD_TICK_SECONDS


@dataclass
class UiMessage:
    kind: str
    text: str = ""
    keys: tuple[str, ...] = ()
    mode: str = ""
    hold_ms: int = 0


class _NullHook:
    def start(self) -> None:
        raise HookUnavailable("No keyboard hook is attached.")

    def stop(self) -> None:
        return None

    def is_running(self) -> bool:
        return False


class KeywareService:
    def __init__(
        self,
        store: ConfigStore,
        opener: Opener,
        injector: Injector,
        audio: AudioCue,
        startup: StartupManager,
        context: ContextTracker | None = None,
        hook=None,
        clock: Callable[[], float] | None = None,
        rng=None,
        synchronous: bool = False,
        key_down: Callable[[str], bool] | None = None,
        session: SessionMonitor | None = None,
        elevated_reader: Callable[[], bool] | None = None,
        window_probe: Callable[[], int] | None = None,
        input_probe=None,
    ):
        import random

        # Foreground window handle. Typed output goes only to the window that
        # was in front when the binding fired.
        self._window_probe = window_probe
        # Read-only GetLastInputInfo / GetAsyncKeyState probe for a silently removed hook.
        self._input_probe = input_probe
        self._hook_health = SilentHookDetector()
        self._recorder = None
        self._record_hook_temp = False
        self._record_stop_pending = False

        self.store = store
        self.config, self.warnings = store.load()
        self.opener = opener
        self.physical = PhysicalKeys()
        self._raw_injector = injector
        self.injector = GuardedInjector(injector, self.physical)
        self.audio = audio
        self.audio.enabled = self.config.audio_enabled
        self.startup = startup
        self.context = context
        self.hook = hook if hook is not None else _NullHook()
        self.synchronous = synchronous
        self._clock = clock or time.perf_counter
        self._session = session
        self._elevated_reader = elevated_reader
        self.executor = ActionExecutor(
            opener,
            self.injector,
            time.sleep if not synchronous else _zero_sleep,
            rng or random.SystemRandom(),
            jitter_min_ms=self.config.jitter_min_ms,
            jitter_max_ms=self.config.jitter_max_ms,
        )
        if synchronous:
            self.executor.sleeper = _zero_sleep
        # Run command: the window sets this to a Yes/No dialog. Without it, commands never run.
        self.command_confirmer: Callable[[str], bool] | None = None
        self.executor.confirm = self._confirm_command
        if synchronous:
            # Tests pump the clicker by hand against the fake clock.
            self.clicker = AutoClicker(
                self.injector,
                guard=self._clicker_allowed,
                log=self._log,
                clock=self._clock,
                sleep=_clock_sleeper(self._clock),
                threaded=False,
            )
        else:
            self.clicker = AutoClicker(self.injector, guard=self._clicker_allowed, log=self._log)
        self._lock = threading.Lock()
        self._replay_lock = threading.Lock()
        self._replay_inflight = 0
        self._key_down = key_down
        self._ui: queue.Queue[UiMessage] = queue.Queue()
        self._actions: queue.Queue = queue.Queue()
        self._replay_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._workers = False
        self.hook_status = "off"
        self.unavailable_reason = ""
        self.foreground_name = ""
        self.active_profile_id = "global"
        self.paused = False
        self._elevated = False
        self._suspended = False
        self._hook_failures = 0
        self._hook_altgr = False
        # Copilot key: Win + Shift + F23 folded into one key (engine/copilot.py).
        from engine.copilot import CopilotFolder

        self._copilot = CopilotFolder()
        # Caps Lock light probe (GetKeyState). Tests replace it.
        self._caps_probe = _caps_lock_on
        self._caps_bound_before = False
        self._config_mtime = self._config_mtime_value()
        self._elevation_at = -1.0
        self._detector = InputDetector(self.config)
        self._detector.set_profiles(self.config.global_profile(), self.config.global_profile())
        self._bind_hook()
        for warning in self.warnings:
            self._log(warning)

    def attach_hook(self, hook) -> None:
        self.hook = hook
        self._bind_hook()

    def _bind_hook(self) -> None:
        if hasattr(self.hook, "flags_for_callback"):
            self.hook.flags_for_callback = self._note_hook_event

    def _note_hook_event(self, event) -> None:
        self._hook_altgr = bool(getattr(event, "altgr", False))

    def start(self) -> None:
        # Keyware does not write an auto-start entry.
        initial = ""
        if self.context is not None:
            initial = self.context.read()
        self.set_foreground(initial)
        if not self.synchronous:
            self._start_workers()
        message = self._try_start_hook() if self.config.enabled else None
        if message:
            self._log(message)
        from engine import __version__

        state = "ON" if self.hook_status == "on" else "OFF"
        self._log(f"Keyware {__version__} started. {state}.")

    def shutdown(self) -> None:
        self._stop.set()
        self.clicker.close()
        self.executor.cancel()
        with self._lock:
            effects = self._detector.set_enabled(False)
            self._copilot.reset()
        self._dispatch(effects, from_hook=False)
        try:
            self.hook.stop()
        except Exception:
            pass
        self.hook_status = "off"
        self.paused = False
        self._suspended = False
        if self.context is not None:
            self.context.stop()
        if self._workers:
            self._actions.put(None)
            self._replay_queue.put(None)

    def wants_mouse(self, button: str) -> bool:
        """Mouse-hook filter: Middle/X1/X2 always (cheap, unbound ones pass through);
        Left/Right only when some enabled binding uses them."""
        from engine.keys import MOUSE_ONLY_WHEN_BOUND as MOUSE_NEEDS_MODIFIER

        extra_button = button.startswith("mouse_x") and button not in ("mouse_x1", "mouse_x2")
        if button not in MOUSE_NEEDS_MODIFIER and not extra_button:
            return True
        if self.recording:
            return False
        try:
            return any(b.enabled and button in b.keys
                       for p in self.config.profiles for b in p.bindings)
        except Exception:
            return False

    def hook_event(self, key: str, pressed: bool) -> bool:
        """Hook-thread entry. Never runs macros or disk writes on this thread."""
        altgr = self._hook_altgr
        self._hook_altgr = False
        return self.handle_input(key, pressed, from_hook=True, altgr=altgr)

    def handle_input(
        self,
        key: str,
        pressed: bool,
        now: float | None = None,
        *,
        from_hook: bool = False,
        altgr: bool = False,
    ) -> bool:
        """Return True when this one event must be swallowed."""
        moment = self._clock() if now is None else now
        folded = self._fold_copilot(key, pressed, moment, from_hook=from_hook)
        if folded is not None:
            return folded
        return self._handle_key(key, pressed, moment, from_hook=from_hook, altgr=altgr)

    def _copilot_wanted(self) -> bool:
        if self._recorder is not None:
            return True
        if self._elevated or self.config.dry_run or self.hook_status != "on":
            return False
        detector = self._detector
        return detector.copilot_bound and detector.enabled and not detector.paused

    def _fold_copilot(self, key: str, pressed: bool, moment: float, *, from_hook: bool) -> bool | None:
        """None: handle the key as usual. True/False: the Copilot key decided."""
        from engine import copilot
        from engine.keys import canonicalize

        name = canonicalize(key)
        if name is None:
            return None
        folder = self._copilot
        folder.enabled = self._copilot_wanted()
        try:
            decision = folder.feed(name, pressed, moment)
        except Exception:
            folder.reset()
            return None
        if decision == copilot.PASS:
            return None
        if decision == copilot.SWALLOW:
            self.physical.observe(name, pressed, moment)
            return True
        if decision == copilot.DOWN:
            parts = ("win", "shift")
            self.physical.forget(parts)
            recorder = self._recorder
            if recorder is not None:
                # The recorder swallowed Win and Shift, so Windows never saw them.
                recorder.forget(parts)
            else:
                with self._lock:
                    self._detector.forget(parts)
                # Windows saw Win and Shift go down. Let them go now, with a mask
                # key so the Start menu does not open. F23 is swallowed, so the
                # Copilot / Search hotkey never fires.
                self._replay("up", "shift")
                self._replay("mask", "")
                self._replay("up", "win")
            self._handle_key("copilot", True, moment, from_hook=from_hook)
            return True
        # copilot_repeat / copilot_up
        self._handle_key("copilot", decision == copilot.REPEAT, moment, from_hook=from_hook)
        return True

    def _handle_key(self, key: str, pressed: bool, moment: float, *, from_hook: bool, altgr: bool = False) -> bool:
        self.physical.observe(key, pressed, moment)
        if self._recorder is not None:
            # Recording a trigger: every key is swallowed and no binding runs.
            self._feed_recorder(key, pressed, moment, from_hook=True)
            return True
        if self._elevated:
            return self._passthrough(key, pressed, moment, from_hook=from_hook)
        acquired = self._lock.acquire(timeout=0.02)
        if not acquired:
            self._log("Skipped a key to keep the keyboard responsive.")
            return False
        try:
            busy = False if self.synchronous else self._output_busy()
            snap = self._detector.snapshot_state() if self.config.dry_run else None
            result = self._detector.handle(key, pressed, moment, output_busy=busy, altgr=altgr)
            if snap is not None:
                # The down was not swallowed, so do not swallow its matching key-up.
                self._detector.swallow_release.clear()
                if any(effect.kind == "panic" for effect in result.effects):
                    self._detector.restore_state(snap)
                    self._detector.config = self.config
        except Exception:
            self._log("Input engine error. Passed the key through.")
            return False
        finally:
            self._lock.release()
        effects = result.effects
        suppress = result.suppress
        if self.config.dry_run:
            effects, logs, pause = _split_dry(effects)
            for message in logs:
                self._log(f"Dry run: {message}")
            if pause:
                self.toggle_pause()
            suppress = False
        try:
            self._dispatch(effects, from_hook=from_hook)
        except Exception:
            self._log("Could not dispatch an input event.")
        return suppress

    def tick(self, now: float | None = None) -> None:
        if self._recorder is not None:
            self._tick_recorder(self._clock() if now is None else now)
        elif self._record_stop_pending:
            self._end_recording()
        self.maybe_reload()
        self._poll_elevation()
        with self._lock:
            moment = self._clock() if now is None else now
            effects = self._detector.tick(moment)
            if self._copilot.tick(moment):
                effects.append(Effect(kind="log", message="Copilot key: a key-up never came. Released it."))
            effects.extend(self._caps_check())
            if self._key_down is not None and self.hook_status == "on" and self._detector.enabled:
                try:
                    effects.extend(self._detector.reconcile(self._key_down, moment))
                except Exception:
                    effects.append(Effect(kind="log", message="Could not read key state."))
                try:
                    self.physical.reconcile(self._key_down, moment)
                except Exception:
                    effects.append(Effect(kind="log", message="Could not read key state."))
        if self.config.dry_run:
            effects, logs, pause = _split_dry(effects)
            for message in logs:
                self._log(f"Dry run: {message}")
            if pause:
                self.toggle_pause()
        self._dispatch(effects, from_hook=False)

    def _caps_check(self) -> list[Effect]:
        """When Caps Lock becomes a bound key, make sure its light is not stuck on.

        Only on the change from unbound to bound, so Shift + Caps Lock (which
        still toggles real Caps Lock) is never undone.
        """
        detector = self._detector
        bound = bool(
            detector.caps_bound and detector.enabled and not detector.paused
            and self.hook_status == "on" and not self.config.dry_run
        )
        before, self._caps_bound_before = self._caps_bound_before, bound
        if not bound or before:
            return []
        try:
            lit = bool(self._caps_probe())
        except Exception:
            return []
        if not lit:
            return []
        return [
            Effect(kind="inject", key="caps_lock"),
            Effect(kind="log", message="Caps Lock is bound, so Keyware turned the Caps Lock light off. Shift + Caps Lock still toggles it."),
        ]

    def set_foreground(self, name: str) -> None:
        with self._lock:
            self.foreground_name = process_basename(name)
            active = match_profile(self.config.profiles, self.foreground_name)
            self.active_profile_id = active.id
            effects = self._detector.set_profiles(self.config.global_profile(), active)
        self._dispatch(effects, from_hook=False)
        self._check_clicker()

    def poll(self) -> list[UiMessage]:
        items: list[UiMessage] = []
        while True:
            try:
                items.append(self._ui.get_nowait())
            except queue.Empty:
                break
        return items

    def snapshot(self) -> dict:
        with self._lock:
            active = self.config.profile_by_id(self.active_profile_id)
            return {
                "enabled": self.hook_status == "on",
                "desired": self.config.enabled,
                "hook": self.hook_status,
                "process": self.foreground_name,
                "profile_id": self.active_profile_id,
                "profile_name": active.name if active else "Global",
                "theme": self.config.theme,
                "audio": self.config.audio_enabled,
                "start_with_windows": bool(self.config.start_with_windows),
                "leader_key": self.config.leader_key,
                "unavailable": self.unavailable_reason,
                "config_path": str(self.store.path),
                "paused": self.paused,
                "dry_run": self.config.dry_run,
            }

    def set_enabled(self, enabled: bool) -> str | None:
        if enabled:
            self.paused = False
            self._suspended = False
            self._hook_failures = 0
            self.executor.reset()
            message = self._try_start_hook()
            if message:
                return message
            with self._lock:
                self.config.enabled = True
            self._save()
            self._log("Active. Listening for chords, holds, and macros.")
            return None
        self._stop_hook(clear_master=True, log="Off. The keyboard hook is released.")
        return None

    def toggle_pause(self) -> None:
        """Pause remaps and leave the hook installed so the hotkey can resume."""
        if self.hook_status != "on":
            self._log("Turn Active on before pausing.")
            return
        if self.paused:
            self.paused = False
            self.executor.reset()
            with self._lock:
                self._detector.set_paused(False)
            self._log("Resumed.")
            self._put(UiMessage(kind="pause", text="Resumed."))
            return
        self.paused = True
        self.clicker.stop_all("paused")
        self.executor.cancel()
        self._drain_actions()
        with self._lock:
            effects = self._detector.set_paused(True)
        self._dispatch(effects, from_hook=False)
        self._log("Paused. The hook stays installed. Ctrl + Alt + F12 resumes.")
        self._put(UiMessage(kind="pause", text="Paused. The hook stays installed."))

    def set_dry_run(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled:
            self.clicker.stop_all("dry run")
        with self._lock:
            self.config.dry_run = enabled
            if not enabled:
                self._detector.disarm()
        self._save()
        if enabled:
            self._log("Dry run on. Keys are logged and not remapped.")
        else:
            self._log("Dry run off.")

    def reload_config(self) -> str | None:
        """Reload the config file. An invalid file keeps the previous config."""
        try:
            config, warnings = self.store.read_validated()
        except Exception as exc:
            self._config_mtime = self._config_mtime_value()
            self._log(f"Config reload skipped: {exc}")
            return str(exc)
        self._config_mtime = self._config_mtime_value()
        if config.to_dict() == self.config.to_dict():
            return None
        self._adopt_config(config, warnings)
        self._log("Config reloaded.")
        self._put(UiMessage(kind="reload", text="Config reloaded."))
        return None

    def maybe_reload(self) -> None:
        mtime = self._config_mtime_value()
        if mtime is None or mtime == self._config_mtime:
            return
        self.reload_config()

    def begin_capture(self) -> None:
        with self._lock:
            if self.hook_status != "on":
                raise ValueError("Turn Keyware on to capture a key, or type the chord instead.")
            self._detector.begin_capture()

    # --- hotkey recorder -------------------------------------------------
    @property
    def recording(self) -> bool:
        return self._recorder is not None

    def begin_recording(self, *, leader: bool = False, single: bool = False) -> str:
        """Start recording keys for a field. Returns "hook" or "tk".

        "hook": keys come from the keyboard hook and are swallowed (Win, Alt
        and Ctrl + Win combos included, no Start menu, no Pulse, no bindings).
        Works when Keyware is OFF too: the hook runs just for the recording.
        "tk": no hook here; the window feeds keys with ``feed_recording``.
        """
        from engine.recorder import KeyRecorder

        self.cancel_recording()
        recorder = KeyRecorder(leader_key=self.config.leader_key if leader else None, single=single)
        recorder.tick(self._clock())
        if not self.synchronous and not self._workers:
            self._start_workers()
        source = "hook"
        if self._record_stop_pending and self._record_hook_temp:
            self._record_stop_pending = False  # the temporary hook is still up: reuse it
        elif self.hook_status != "on":
            try:
                self.hook.start()
                self._record_hook_temp = True
            except Exception:
                source = "tk"
        with self._lock:
            self._recorder = recorder
            self._record_source = source
            self._record_window = self._fire_window()
        self._put(UiMessage(kind="record_live", text="Press keys… Esc cancels, Backspace clears."))
        return source

    def feed_recording(self, key: str, pressed: bool, now: float | None = None) -> None:
        """Tk fallback path. Ignored while the hook is doing the recording."""
        if self._recorder is None or getattr(self, "_record_source", "") != "tk":
            return
        self._feed_recorder(key, pressed, self._clock() if now is None else now)

    def cancel_recording(self) -> None:
        if self._recorder is None:
            return
        self._end_recording()
        self._put(UiMessage(kind="record_cancel", text="Recording cancelled."))

    def _feed_recorder(self, key: str, pressed: bool, now: float, *, from_hook: bool = False) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        from engine.keys import canonicalize

        name = canonicalize(key)
        if name is None:
            return
        self._emit_record(recorder.feed(name, pressed, now))
        if recorder.finished:
            # Never stop the hook from inside its own callback; tick does it.
            self._end_recording(defer_stop=from_hook)

    def _tick_recorder(self, now: float) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        window = getattr(self, "_record_window", None)
        if window is not None and self._fire_window() not in (None, window) and not recorder.down:
            # Another window came to the front: stop swallowing keys right away.
            self._end_recording()
            self._put(UiMessage(kind="record_cancel", text="Recording stopped: Keyware is no longer in front."))
            return
        self._emit_record(recorder.tick(now))
        if recorder.finished:
            self._end_recording()

    def _emit_record(self, events) -> None:
        for event in events:
            self._put(UiMessage(kind=f"record_{event.kind}", text=event.text, keys=event.keys, mode=event.mode, hold_ms=event.hold_ms))

    def _end_recording(self, *, defer_stop: bool = False) -> None:
        with self._lock:
            self._recorder = None
        if defer_stop:
            self._record_stop_pending = True
            return
        self._record_stop_pending = False
        if self._record_hook_temp:
            self._record_hook_temp = False
            if self.hook_status != "on":
                try:
                    self.hook.stop()
                except Exception:
                    pass

    def cancel_capture(self) -> None:
        with self._lock:
            self._detector.cancel_capture()

    def run_action(self, action) -> None:
        if getattr(action, "type", "") == "auto_click":
            self._log("Auto-click runs from its trigger key, not the Run button.")
            return
        self._start_action(action, from_hook=False)

    def profiles(self) -> list[Profile]:
        return list(self.config.profiles)

    def bindings(self, profile_id: str) -> list[Binding]:
        profile = self.config.profile_by_id(profile_id)
        if profile is None:
            return []
        return list(profile.bindings)

    def _check_conflicts(self, profile, binding: Binding, replace: bool) -> None:
        """One trigger, one action. Raise ConflictError, or drop the clashes when replacing."""
        from engine.conflicts import ConflictError, find_conflicts

        conflicts = find_conflicts(binding, profile.bindings, self.config.leader_key)
        if not conflicts:
            return
        if not replace:
            raise ConflictError(conflicts)
        gone = {item.binding.id for item in conflicts}
        profile.bindings = [item for item in profile.bindings if item.id not in gone]

    def _override_notes(self, profile, binding: Binding) -> list[str]:
        """App profiles may override Global on purpose. Say so, never block."""
        from engine.conflicts import find_overrides

        glob = self.config.global_profile()
        if profile is glob or profile.id == glob.id:
            return []
        return find_overrides(binding, glob.bindings, self.config.leader_key, profile.name)

    def add_binding(self, profile_id: str, binding: Binding, *, replace: bool = False) -> list[str]:
        validate_binding(binding, self.config)
        with self._lock:
            profile = self._require(profile_id)
            self._check_conflicts(profile, binding, replace)
            if not binding.id:
                binding.id = new_id("bind")
            profile.bindings.append(binding)
            notes = self._override_notes(profile, binding)
        self._save_and_sync()
        self._log(f"Saved binding in {profile.name}.")
        for note in notes:
            self._log(note)
        self._arm_after_edit(binding.enabled)
        return notes

    def update_binding(self, profile_id: str, binding: Binding, *, replace: bool = False) -> list[str]:
        validate_binding(binding, self.config)
        with self._lock:
            profile = self._require(profile_id)
            if not any(item.id == binding.id for item in profile.bindings):
                raise ValueError("That binding is no longer in the profile.")
            self._check_conflicts(profile, binding, replace)
            index = next(i for i, item in enumerate(profile.bindings) if item.id == binding.id)
            profile.bindings[index] = binding
            notes = self._override_notes(profile, binding)
        self._save_and_sync()
        self._log(f"Updated binding in {profile.name}.")
        for note in notes:
            self._log(note)
        self._arm_after_edit(binding.enabled)
        return notes

    def delete_binding(self, profile_id: str, binding_id: str) -> tuple[Binding, int] | None:
        """Remove a binding. Returns (binding, index) so the UI can undo it."""
        removed: tuple[Binding, int] | None = None
        with self._lock:
            profile = self._require(profile_id)
            for index, item in enumerate(profile.bindings):
                if item.id == binding_id:
                    removed = (item, index)
                    break
            profile.bindings = [item for item in profile.bindings if item.id != binding_id]
        if self.clicker.running_binding == binding_id:
            self.clicker.stop(binding_id, "binding deleted")
        self._save_and_sync()
        if removed is not None:
            self._log(f"Deleted binding in {profile.name}.")
        return removed

    def restore_binding(self, profile_id: str, binding: Binding, index: int) -> list[str]:
        """Undo a delete. A binding that now clashes comes back switched off."""
        from engine.conflicts import find_conflicts

        notes: list[str] = []
        with self._lock:
            profile = self._require(profile_id)
            if any(item.id == binding.id for item in profile.bindings):
                return notes
            if binding.enabled and find_conflicts(binding, profile.bindings, self.config.leader_key):
                binding.enabled = False
                notes.append("Restored switched off because another binding now uses the same trigger.")
            position = max(0, min(int(index), len(profile.bindings)))
            profile.bindings.insert(position, binding)
        self._save_and_sync()
        self._log(f"Restored binding in {profile.name}.")
        for note in notes:
            self._log(note)
        return notes

    def set_binding_enabled(self, profile_id: str, binding_id: str, enabled: bool) -> None:
        with self._lock:
            profile = self._require(profile_id)
            for item in profile.bindings:
                if item.id == binding_id:
                    if enabled and not item.enabled:
                        from engine.conflicts import ConflictError, find_conflicts
                        from dataclasses import replace as _copy

                        clash = find_conflicts(_copy(item, enabled=True), profile.bindings, self.config.leader_key)
                        if clash:
                            raise ConflictError(clash)
                    item.enabled = bool(enabled)
                    break
            else:
                raise ValueError("That binding is no longer in the profile.")
        self._save_and_sync()
        self._arm_after_edit(bool(enabled))

    def _arm_after_edit(self, binding_enabled: bool) -> None:
        """A binding the user just saved or switched on must work right away.

        If Keyware is OFF (the master switch), turn it ON. A pause, a locked
        session, or a hook Windows refused is left as it is.
        """
        if not binding_enabled or self.hook_status != "off":
            return
        message = self.set_enabled(True)
        if message:
            self._log(message)
        else:
            self._log("Keyware was OFF. Turned it ON so the binding works now.")

    def status_summary(self) -> dict:
        """Plain words for the header: is anything going to fire right now?"""
        snap = self.snapshot()
        with self._lock:
            profile = self.config.profile_by_id(self.active_profile_id) or self.config.global_profile()
            names = [profile] if profile.is_global else [self.config.global_profile(), profile]
            armed = 0
            leader_missing = False
            for item in names:
                for binding in item.bindings:
                    if not binding.enabled:
                        continue
                    if binding.mode == "leader" and not self.config.leader_key:
                        leader_missing = True
                        continue
                    armed += 1
        if snap["paused"]:
            headline = "PAUSED"
            detail = "Ctrl + Alt + F12 resumes. Keys pass through."
        elif snap["hook"] == "on":
            headline = "ON  ·  DRY RUN" if snap["dry_run"] else "ON"
            plural = "binding" if armed == 1 else "bindings"
            detail = f"{armed} {plural} armed in {snap['profile_name']}"
            if snap["dry_run"]:
                detail += ". Dry run: actions are only logged."
            elif self._elevated:
                detail += ". The app in front runs as administrator, so Windows blocks remaps there."
            elif leader_missing:
                detail += ". Set a leader key to use Leader bindings."
        elif snap["hook"] == "suspended":
            headline = "WAITING"
            detail = "Locked or secure desktop. Keyware comes back on its own."
        elif snap["hook"] == "unavailable":
            headline = "UNAVAILABLE"
            detail = snap["unavailable"] or "The keyboard hook cannot run here."
        else:
            headline = "OFF"
            detail = "Bindings will not fire. Press TURN ON."
        return {"headline": headline, "detail": detail, "on": snap["hook"] == "on"}

    def add_profile(self, name: str, process_names: list[str]) -> Profile:
        title = name.strip()
        if not title:
            raise ValueError("Name the profile.")
        processes = _clean_processes(process_names)
        if not processes:
            raise ValueError("Add a process name such as notepad.exe.")
        profile = Profile(id=new_id("profile"), name=title, process_names=processes, enabled=True, bindings=[])
        with self._lock:
            self.config.profiles.append(profile)
        self._save_and_sync()
        return profile

    def update_profile(self, profile_id: str, name: str, process_names: list[str], enabled: bool) -> None:
        title = name.strip()
        if not title:
            raise ValueError("Name the profile.")
        with self._lock:
            profile = self._require(profile_id)
            profile.name = title
            profile.enabled = True if profile.is_global else bool(enabled)
            if profile.is_global:
                profile.process_names = []
            else:
                processes = _clean_processes(process_names)
                if not processes:
                    raise ValueError("Add a process name such as notepad.exe.")
                profile.process_names = processes
        self._save_and_sync()

    def delete_profile(self, profile_id: str) -> None:
        if profile_id == "global":
            raise ValueError("The Global profile stays.")
        with self._lock:
            self.config.profiles = [profile for profile in self.config.profiles if profile.id != profile_id]
            if not any(profile.is_global for profile in self.config.profiles):
                raise ValueError("The Global profile stays.")
        self._save_and_sync()

    def set_theme(self, theme: str) -> None:
        if theme not in ("light", "dark"):
            raise ValueError("Theme must be light or dark.")
        with self._lock:
            self.config.theme = theme
        self._save()

    def set_audio_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.config.audio_enabled = bool(enabled)
            self.audio.enabled = bool(enabled)
        self._save()

    def ensure_install_id(self) -> str:
        """A random UUID for the Text mode proxy's daily limit. Made once, then kept in config."""
        import uuid

        with self._lock:
            if self.config.install_id:
                return self.config.install_id
            self.config.install_id = str(uuid.uuid4())
            value = self.config.install_id
        self._save()
        return value

    def set_ai_settings(self, *, use_own_key: bool | None = None, provider: str | None = None,
                        key_protected: str | None = None) -> None:
        """Text mode settings. key_protected is the DPAPI blob ("" removes the key), never the key."""
        with self._lock:
            if provider is not None:
                if provider not in ("gemini", "groq"):
                    raise ValueError("Pick Gemini or Groq.")
                self.config.ai_provider = provider
            if key_protected is not None:
                self.config.ai_key_protected = key_protected
            if use_own_key is not None:
                self.config.ai_use_own_key = bool(use_own_key)
        self._save()

    def mark_tray_hint_shown(self) -> bool:
        """True the first time only: the "still running in the tray" notice is shown once."""
        with self._lock:
            if self.config.tray_hint_shown:
                return False
            self.config.tray_hint_shown = True
        self._save()
        return True

    def set_leader_key(self, key: str | None) -> None:
        cleaned = validate_leader_key(key, self.config)
        with self._lock:
            self.config.leader_key = cleaned
            self._detector.config = self.config
        self._save()
        if cleaned:
            self._log(f"Leader key set to {cleaned}.")
            if len(cleaned) == 1 or cleaned in ("space", "enter", "tab", "backspace"):
                self._log(
                    f"Heads up: while Keyware is ON, every {cleaned.upper()} you type is taken as the leader. "
                    "F13 or F1 is a safer choice."
                )
        else:
            self._log("Leader key cleared.")

    def set_start_with_windows(self, enabled: bool) -> bool:
        """Adds or removes the HKCU Run value (Keyware --background). Returns what Windows now has."""
        try:
            self.startup.set_enabled(bool(enabled))
        except OSError as exc:
            raise ValueError(f"Could not change Windows startup: {exc}") from exc
        on = bool(self.startup.is_enabled())
        with self._lock:
            self.config.start_with_windows = on
            self.config.startup_default_applied = True
        self._save()
        self._log("Keyware starts with Windows, in the tray." if on else "Keyware won't start with Windows.")
        return on

    def apply_startup_default(self) -> bool:
        """Start with Windows is ON by default: set once for a new install (or one from before the setting).

        After that the user's choice wins. If it is on and the exe moved, the Run value is pointed at it again.
        Returns True when the Run value was written.
        """
        if not self.config.startup_default_applied:
            try:
                return self.set_start_with_windows(True)
            except ValueError as exc:
                self._log(str(exc))
                return False
        try:
            current = self.startup.current()
        except OSError:
            return False
        if current is not None and current != self.startup.command:
            try:
                self.startup.set_enabled(True)
            except OSError:
                return False
            return True
        with self._lock:
            self.config.start_with_windows = current is not None
        return False

    def note_integrity(self, elevated: bool) -> None:
        """While the foreground window is elevated, fail open and release replays."""
        elevated = bool(elevated)
        if elevated == self._elevated:
            return
        self._elevated = elevated
        if not elevated:
            self._log("Foreground window is no longer elevated. Remaps resumed.")
            return
        self._log("Foreground window is elevated. Remaps pass through so modifiers cannot stick.")
        self.clicker.stop_all("elevated window")
        with self._lock:
            effects = self._detector.release_replayed()
        self._dispatch(effects, from_hook=False)

    def apply_session(self, event: str | None) -> str:
        """Apply one watchdog decision. Used by the timer thread and by tests."""
        try:
            running = bool(self.hook.is_running())
        except Exception:
            running = False
        should_run = bool(self.config.enabled) and self.hook_status != "unavailable"
        action = decide_hook(
            should_run,
            running,
            event,
            self._hook_failures,
            suspended=self._suspended,
        )
        if action == "none":
            return action
        if action == "stop":
            self._suspend_for_session()
            return action
        if action == "pause":
            self._hook_failures = 0
            self._stop_hook(clear_master=True, log="The hook failed to reinstall. Keyware turned off.")
            self._put(UiMessage(kind="panic", text="The hook failed to reinstall. Keyware turned off."))
            return action
        if running:
            try:
                self.hook.stop()
            except Exception:
                pass
        message = self._try_start_hook(persist_failure=False)
        if message:
            self._hook_failures += 1
            self._log(message)
            if self._hook_failures >= 3:
                self._stop_hook(clear_master=True, log="The hook failed to reinstall. Keyware turned off.")
                self._put(UiMessage(kind="panic", text="The hook failed to reinstall. Keyware turned off."))
                return "pause"
            return "reinstall"
        self._hook_failures = 0
        self._suspended = False
        self._log("Hook reinstalled.")
        return "reinstall"

    def check_hook_health(self, now: float | None = None) -> str:
        """Reinstall a hook that Windows removed without telling us.

        Runs on the watchdog thread. Returns ``reinstall`` or ``none``.
        """
        health = self._hook_health
        probe = self._input_probe
        events = getattr(self.hook, "events_seen", None)
        if probe is None or events is None or self.hook_status != "on" or self._elevated or self._suspended:
            health.reset()
            return "none"
        try:
            last_input = int(probe.last_input())
            keys = frozenset(probe.keys_down())
        except Exception:
            health.reset()
            return "none"
        moment = time.monotonic() if now is None else now
        if not health.sample(moment, int(events), last_input, keys):
            return "none"
        self._log("The keyboard hook went quiet (Windows can remove a slow hook). Reinstalling it.")
        self.clicker.stop_all("hook lost")
        with self._lock:
            effects = self._detector.release_replayed()
        self._dispatch(effects, from_hook=False)
        try:
            self.hook.stop()
        except Exception:
            pass
        message = self._try_start_hook(persist_failure=False)
        health.reset()
        if message:
            self._hook_failures += 1
            self._log(message)
            return "reinstall"
        self._log("Hook reinstalled.")
        return "reinstall"

    def _try_start_hook(self, *, persist_failure: bool = True) -> str | None:
        try:
            self.hook.start()
        except HookUnavailable as exc:
            self.hook_status = "unavailable"
            self.unavailable_reason = str(exc)
            with self._lock:
                self._detector.set_enabled(False)
            return str(exc)
        except Exception as exc:
            self.hook_status = "off"
            with self._lock:
                if persist_failure:
                    self.config.enabled = False
                self._detector.set_enabled(False)
            if persist_failure:
                self._save()
            return f"Hook failed ({exc}). Keyware stayed off."
        self.hook_status = "on"
        self.unavailable_reason = ""
        self._suspended = False
        with self._lock:
            if self.paused:
                self._detector.enabled = True
                self._detector.paused = True
            else:
                self._detector.set_enabled(True)
        return None

    def _stop_hook(self, *, clear_master: bool, log: str | None = None) -> None:
        self.clicker.stop_all("Keyware off")
        self.executor.cancel()
        self._drain_actions()
        self.paused = False
        self._suspended = False
        with self._lock:
            if clear_master:
                self.config.enabled = False
            effects = self._detector.set_enabled(False)
            self._copilot.reset()
        self._dispatch(effects, from_hook=False)
        try:
            self.hook.stop()
        except Exception:
            pass
        self.hook_status = "off"
        if clear_master:
            self._save()
        if log:
            self._log(log)

    def _suspend_for_session(self) -> None:
        self.clicker.stop_all("session changed")
        self._suspended = True
        with self._lock:
            effects = self._detector.release_replayed()
            self._detector.suspend_tracking()
            self._copilot.reset()
        self._dispatch(effects, from_hook=False)
        try:
            self.hook.stop()
        except Exception:
            pass
        self.hook_status = "suspended"
        self._log("Input session changed. The hook was removed and will be reinstalled.")

    def _on_panic(self, from_hook: bool) -> None:
        self.clicker.stop_all("panic")
        self.executor.cancel()
        self._drain_actions()
        try:
            self.hook.stop()
        except Exception:
            pass
        self.hook_status = "off"

        def persist() -> None:
            with self._lock:
                self.config.enabled = False
                self._detector.set_enabled(False)
            try:
                self.store.save(self.config)
            except Exception as exc:
                self._put(UiMessage(kind="log", text=f"Could not save config: {exc}"))

        if from_hook:
            threading.Thread(target=persist, name="keyware-panic-save", daemon=True).start()
        else:
            persist()
        self._put(UiMessage(kind="panic", text="Panic switch pressed. Hooks released. Keyware is paused."))

    def _dispatch(self, effects: list[Effect], *, from_hook: bool) -> None:
        for effect in effects:
            if effect.kind == "panic":
                self._on_panic(from_hook)
            elif effect.kind == "fire" and effect.action is not None:
                if effect.action.type == "auto_click":
                    self._fire_autoclick(effect)
                else:
                    self._start_action(effect.action, from_hook=from_hook)
            elif effect.kind == "autoclick_stop":
                self.clicker.stop(effect.binding_id, "key released")
            elif effect.kind == "inject" and effect.key:
                self._replay("tap", effect.key)
            elif effect.kind == "inject_down" and effect.key:
                self._replay("down", effect.key)
            elif effect.kind == "inject_up" and effect.key:
                self._replay("up", effect.key)
            elif effect.kind == "mask":
                self._replay("mask", "")
            elif effect.kind == "pause_toggle":
                self.toggle_pause()
            elif effect.kind == "audio":
                if from_hook and not self.synchronous:
                    if not self._workers:
                        self._start_workers()
                    self._actions.put(("audio", effect.hz))
                else:
                    try:
                        self.audio.play(effect.hz)
                    except Exception:
                        pass
            elif effect.kind == "log" and effect.message:
                self._log(effect.message)
            elif effect.kind == "capture":
                self._put(UiMessage(kind="capture", text=effect.message, keys=effect.keys))
            elif effect.kind == "capture_cancel":
                self._put(UiMessage(kind="capture_cancel", text=effect.message))

    def _fire_autoclick(self, effect: Effect) -> None:
        if self.paused or self.hook_status != "on" or self._elevated:
            return
        action = effect.action
        if action.click_mode == "toggle":
            self.clicker.toggle(effect.binding_id, effect.key or None, action)
        else:
            self.clicker.start(effect.binding_id, effect.key or None, action)

    def _clicker_allowed(self, job: ClickJob) -> bool:
        """Checked before every click. Any "no" stops the auto-clicker."""
        if self.hook_status != "on" or self.paused or self._elevated or self._suspended:
            return False
        detector = self._detector
        if not detector.enabled or detector.paused:
            return False
        if job.binding_id not in detector.active_binding_ids:
            return False
        if job.while_held:
            return job.key is not None and detector.autoclick_keys.get(job.key) == job.binding_id
        return True

    def _check_clicker(self) -> None:
        running = self.clicker.running_binding
        if running is not None and running not in self._detector.active_binding_ids:
            self.clicker.stop(running, "profile no longer active")

    def _start_action(self, action, *, from_hook: bool) -> None:
        if self.synchronous and not from_hook:
            try:
                self.executor.run(action)
            except Exception as exc:
                self._log(f"Action failed: {exc}")
            return
        if not self._workers:
            self._start_workers()
        self._actions.put(("run", action, self._fire_window()))

    def _replay(self, op: str, key: str) -> None:
        """Play key replays on one worker so they cannot pass each other."""
        if self.synchronous:
            self._apply_replay(op, key)
            return
        with self._replay_lock:
            self._replay_inflight += 1
        if not self._workers:
            self._start_workers()
        self._replay_queue.put((op, key))

    def _apply_replay(self, op: str, key: str) -> None:
        try:
            if op == "down":
                self.injector.press(key)
            elif op == "up":
                self.injector.release(key)
            elif op == "tap":
                self.injector.press(key)
                self.injector.release(key)
            elif op == "mask":
                mask = getattr(self.injector, "mask", None)
                if mask is not None:
                    mask()
        except Exception as exc:
            self._log(f"Could not replay input: {exc}")

    def _output_busy(self) -> bool:
        with self._replay_lock:
            return self._replay_inflight > 0

    def _start_workers(self) -> None:
        if self._workers:
            return
        self._workers = True
        threading.Thread(target=self._action_loop, name="keyware-actions", daemon=True).start()
        threading.Thread(target=self._replay_loop, name="keyware-replay", daemon=True).start()
        threading.Thread(target=self._tick_loop, name="keyware-tick", daemon=True).start()
        if self.context is not None:
            self.context.start(self.set_foreground)
        threading.Thread(target=self._watch_hook, name="keyware-watch", daemon=True).start()

    def _action_loop(self) -> None:
        while True:
            action = self._actions.get()
            if action is None or self._stop.is_set():
                return
            if isinstance(action, tuple) and action and action[0] == "audio":
                try:
                    self.audio.play(action[1])
                except Exception:
                    pass
                continue
            hwnd = None
            if isinstance(action, tuple) and action and action[0] == "run":
                _tag, action, hwnd = action
            if self.executor.cancelled:
                continue
            if not self._wait_modifiers_up(action):
                self._log("Skipped a typing action: release Ctrl, Alt, Shift, and Win first.")
                continue
            if not self._same_window(action, hwnd):
                self._log("Skipped a typing action: another window came to the front before Keyware could type.")
                continue
            if action.type in self._TYPING_ACTIONS and hwnd is not None:
                self.executor.guard = lambda action=action, hwnd=hwnd: self._same_window(action, hwnd)
            else:
                self.executor.guard = None
            try:
                self.executor.run(action)
            except Exception as exc:
                self._log(f"Action failed: {exc}")
            finally:
                self.executor.guard = None

    _TYPING_ACTIONS = frozenset({"type_text", "key_combo", "macro", "media", "screenshot"})

    def _fire_window(self):
        if self._window_probe is None:
            return None
        try:
            return int(self._window_probe() or 0) or None
        except Exception:
            return None

    def _confirm_command(self, command: str) -> bool:
        """True when this exact command was confirmed before, or the user confirms it now (once)."""
        from engine.models import command_fingerprint

        fingerprint = command_fingerprint(command)
        with self._lock:
            if fingerprint in self.config.approved_commands:
                return True
        asker = self.command_confirmer
        if asker is None:
            self._log("Run command skipped: confirm it once from the Keyware window.")
            return False
        try:
            approved = bool(asker(command))
        except Exception:
            approved = False
        if not approved:
            self._log("Run command not confirmed. Nothing ran.")
            return False
        with self._lock:
            if fingerprint not in self.config.approved_commands:
                self.config.approved_commands.append(fingerprint)
        try:
            self._save()
        except Exception as exc:
            self._log(f"Could not save the confirmation: {exc}")
        return True

    def _same_window(self, action, hwnd) -> bool:
        if hwnd is None or getattr(action, "type", "") not in self._TYPING_ACTIONS:
            return True
        current = self._fire_window()
        return current is None or current == hwnd

    def _wait_modifiers_up(self, action, *, sleep=None, timeout: float = 3.0, clock=None) -> bool:
        """Typed output must not mix with the chord's own modifiers.

        Ctrl + Win + Shift + L typing "console.log();" while Ctrl is still held
        sends Ctrl+C, Ctrl+O, ... instead. Wait (off the hook thread) until the
        user lets go of every modifier. Give up after ``timeout`` seconds.
        """
        if getattr(action, "type", "") not in self._TYPING_ACTIONS:
            return True
        sleep = sleep or time.sleep
        clock = clock or time.monotonic
        deadline = clock() + timeout
        while any(self.physical.holding(name) for name in ("ctrl", "alt", "shift", "win")):
            if clock() >= deadline:
                return False
            sleep(0.01)
        return True

    def _replay_loop(self) -> None:
        while True:
            item = self._replay_queue.get()
            if item is None:
                return
            op, key = item
            try:
                self._apply_replay(op, key)
            finally:
                with self._replay_lock:
                    self._replay_inflight = max(0, self._replay_inflight - 1)

    def _tick_loop(self) -> None:
        while not self._stop.wait(HOLD_TICK_SECONDS):
            try:
                self.tick()
            except Exception:
                self._log("Hold timer error. In-progress holds were left unchanged.")

    def _watch_hook(self) -> None:
        while not self._stop.wait(0.25):
            try:
                event = self._session.poll() if self._session is not None else None
                self.apply_session(event)
                self.check_hook_health()
            except Exception:
                self._log("Hook watchdog error. The keyboard was left as it was.")

    def _passthrough(self, key: str, pressed: bool, moment: float, *, from_hook: bool) -> bool:
        acquired = self._lock.acquire(timeout=0.02)
        if not acquired:
            return False
        try:
            effects = self._detector.observe_passthrough(key, pressed, moment)
        except Exception:
            self._log("Input engine error. Passed the key through.")
            return False
        finally:
            self._lock.release()
        try:
            self._dispatch(effects, from_hook=from_hook)
        except Exception:
            self._log("Could not dispatch an input event.")
        return False

    def _poll_elevation(self) -> None:
        if self._elevated_reader is None:
            return
        moment = self._clock()
        if moment - self._elevation_at < 0.5:
            return
        self._elevation_at = moment
        try:
            elevated = bool(self._elevated_reader())
        except Exception:
            return
        self.note_integrity(elevated)

    def _adopt_config(self, config, warnings: list[str]) -> None:
        with self._lock:
            was_enabled = bool(self.config.enabled)
            self.config = config
            self.audio.enabled = config.audio_enabled
            self.executor.jitter_min_ms = config.jitter_min_ms
            self.executor.jitter_max_ms = config.jitter_max_ms
            self._detector.config = config
            if not config.dry_run:
                self._detector.disarm()
            active = match_profile(self.config.profiles, self.foreground_name)
            self.active_profile_id = active.id
            effects = self._detector.set_profiles(self.config.global_profile(), active)
        self._dispatch(effects, from_hook=False)
        self._check_clicker()
        for warning in warnings:
            self._log(warning)
        if config.enabled and self.hook_status != "on":
            message = self._try_start_hook()
            if message:
                self._log(message)
        elif not config.enabled and (was_enabled or self.hook_status == "on"):
            self._stop_hook(clear_master=False, log="Active was turned off from the config file.")

    def _config_mtime_value(self) -> tuple[int, int] | None:
        try:
            stat = self.store.path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _save_and_sync(self) -> None:
        self._save()
        with self._lock:
            self._detector.config = self.config
            active = match_profile(self.config.profiles, self.foreground_name)
            self.active_profile_id = active.id
            effects = self._detector.set_profiles(self.config.global_profile(), active)
        self._dispatch(effects, from_hook=False)
        self._check_clicker()

    def _save(self) -> None:
        self.store.save(self.config)
        self._config_mtime = self._config_mtime_value()

    def _require(self, profile_id: str) -> Profile:
        profile = self.config.profile_by_id(profile_id)
        if profile is None:
            raise ValueError("That profile no longer exists.")
        return profile

    def _drain_actions(self) -> None:
        while True:
            try:
                self._actions.get_nowait()
            except queue.Empty:
                break

    def _log(self, text: str) -> None:
        self._put(UiMessage(kind="log", text=text))
        self._file_log(text)

    def _file_log(self, text: str) -> None:
        """Mirror the log to keyware.log next to config.json. Never raises."""
        try:
            path = self.store.path.parent / "keyware.log"
            if path.exists() and path.stat().st_size > 512_000:
                path.replace(path.with_suffix(".log.1"))
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp}  {text}\n")
        except Exception:
            pass

    def _put(self, message: UiMessage) -> None:
        if self._ui.qsize() > 400:
            try:
                self._ui.get_nowait()
            except queue.Empty:
                pass
        self._ui.put(message)


def _zero_sleep(_seconds: float) -> None:
    return None


def _clock_sleeper(clock):
    """Sleep for tests: advance a fake clock (``advance(ms)``) instead of waiting."""
    advance = getattr(clock, "advance", None)
    if advance is None:
        return _zero_sleep
    return lambda seconds: advance(seconds * 1000.0)


def _caps_lock_on() -> bool:
    """True when the Caps Lock toggle (the light) is on. False off Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
    except Exception:
        return False


def _split_dry(effects: list[Effect]) -> tuple[list[Effect], list[str], bool]:
    """Keep logs. Turn fires into dry-run lines. Drop anything that would inject or launch."""
    kept: list[Effect] = []
    logs: list[str] = []
    pause = False
    for effect in effects:
        if effect.kind == "fire":
            if effect.message:
                logs.append(effect.message)
        elif effect.kind == "pause_toggle":
            pause = True
        elif effect.kind == "panic":
            if effect.message:
                logs.append(effect.message)
        elif effect.kind in ("inject", "inject_down", "inject_up", "mask", "audio"):
            continue
        else:
            kept.append(effect)
    return kept, logs, pause


def _clean_processes(names: list[str]) -> list[str]:
    cleaned: list[str] = []
    for name in names:
        text = process_basename(name).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def build_service(
    store: ConfigStore,
    *,
    opener: Opener,
    injector: Injector,
    audio: AudioCue,
    startup: StartupManager | None = None,
    hook=None,
    clock: Callable[[], float] | None = None,
    rng=None,
    synchronous: bool = True,
    foreground: Callable[[], str] | None = None,
    key_down: Callable[[str], bool] | None = None,
    session: SessionMonitor | None = None,
    elevated_reader: Callable[[], bool] | None = None,
) -> KeywareService:
    context = ContextTracker(foreground or (lambda: ""), interval=0.5) if foreground is not None else None
    return KeywareService(
        store=store,
        opener=opener,
        injector=injector,
        audio=audio,
        startup=startup or StartupManager(),
        context=context,
        hook=hook,
        clock=clock,
        rng=rng,
        synchronous=synchronous,
        key_down=key_down,
        session=session,
        elevated_reader=elevated_reader,
    )

"""Windows hook hardening, Pulse coexistence, and config reload.

Every OS reader is a fake. These tests never install a hook, call SendInput,
open a process, or take the real single-instance mutex.
"""

from __future__ import annotations

import json
import os

import pytest

from engine.diag import run_diag
from engine.hook_engine import HookEngine
from engine.instance import InstanceLock
from engine.integrity import probe_pid
from engine.keystate import key_down_from
from engine.models import Action, Binding
from engine.sendinput import (
    KEYEVENTF_EXTENDEDKEY,
    KEYWARE_EXTRA,
    build_keyboard_event,
    build_mouse_click,
    build_unicode_events,
    classify_hook_event,
)
from engine.session import SessionMonitor
from engine.watchdog import decide_hook
from tests.fakes import FakeHook, service


class _Suppress(Exception):
    pass


class _Listener:
    def __init__(self):
        self.suppressed = 0
        self.running = True

    def suppress_event(self):
        self.suppressed += 1
        raise _Suppress()

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def is_running(self):
        return self.running


class _Data:
    def __init__(self, vk, flags=0, extra=0):
        self.vkCode = vk
        self.flags = flags
        self.dwExtraInfo = extra


def test_classify_ignores_our_tag_and_passes_foreign_injected_keys():
    own = classify_hook_event(0x0100, 0x41, 0, KEYWARE_EXTRA, already_down=False)
    assert own.kind == "ignore_own"
    foreign = classify_hook_event(0x0100, 0x41, 0x10, 0, already_down=False)
    assert foreign.kind == "pass_injected"
    other_magic = classify_hook_event(0x0100, 0x41, 0x02, 0x1111, already_down=False)
    assert other_magic.kind == "pass_injected"
    physical = classify_hook_event(0x0100, 0x41, 0, 0, already_down=False)
    assert physical.kind == "key"
    assert physical.name == "a"
    assert physical.pressed is True
    assert physical.repeat is False


def test_classify_marks_repeat_altgr_and_extended_right_alt():
    repeat = classify_hook_event(0x0100, 0x41, 0, 0, already_down=True)
    assert repeat.repeat is True
    right_alt = classify_hook_event(0x0104, 0xA5, 0, 0, already_down=False)
    assert right_alt.altgr is True
    assert right_alt.name == "alt"
    extended_alt = classify_hook_event(0x0104, 0x12, 0x01, 0, already_down=False)
    assert extended_alt.altgr is True
    left_alt = classify_hook_event(0x0104, 0xA4, 0, 0, already_down=False)
    assert left_alt.altgr is False


def test_sendinput_events_carry_the_magic_and_extended_flag():
    down = build_keyboard_event(0x25)
    assert down.extra == KEYWARE_EXTRA == 0x4B575245
    assert down.flags & KEYEVENTF_EXTENDEDKEY
    letter = build_keyboard_event(0x41)
    assert letter.extra == KEYWARE_EXTRA
    assert letter.flags & KEYEVENTF_EXTENDEDKEY == 0
    right_ctrl = build_keyboard_event(0xA3, key_up=True)
    assert right_ctrl.flags & KEYEVENTF_EXTENDEDKEY
    assert right_ctrl.flags & 0x0002
    text = build_unicode_events("A")
    assert text and all(event.extra == KEYWARE_EXTRA for event in text)
    click = build_mouse_click("right")
    assert all(event.extra == KEYWARE_EXTRA for event in click)


def test_hook_filter_drops_our_events_and_foreign_injected_events():
    seen = []

    def on_event(name, pressed):
        seen.append((name, pressed))
        return True

    engine = HookEngine(on_event)
    notes = []
    engine.flags_for_callback = lambda event: notes.append((event.repeat, event.altgr))
    engine._listener = _Listener()

    assert engine._filter(0x0100, _Data(0x41, extra=KEYWARE_EXTRA)) is False
    assert engine._filter(0x0100, _Data(0x41, flags=0x10, extra=99)) is False
    assert seen == []
    assert notes == []

    with pytest.raises(_Suppress):
        engine._filter(0x0100, _Data(0x41))
    assert seen == [("a", True)]
    with pytest.raises(_Suppress):
        engine._filter(0x0100, _Data(0x41))
    assert notes[-1][0] is True


def test_watchdog_reinstalls_after_lock_and_stops_after_three_failures():
    assert decide_hook(True, True, None, 0) == "none"
    assert decide_hook(True, False, None, 0) == "reinstall"
    assert decide_hook(True, False, None, 3) == "pause"
    assert decide_hook(True, True, "lock", 0) == "stop"
    assert decide_hook(True, True, "uac", 0) == "stop"
    assert decide_hook(True, False, "sleep", 0) == "stop"
    assert decide_hook(True, False, None, 0, suspended=True) == "none"
    assert decide_hook(True, False, "unlock", 0, suspended=True) == "reinstall"
    assert decide_hook(True, True, "resume", 0) == "reinstall"
    assert decide_hook(False, False, "resume", 0) == "none"


def test_session_monitor_uses_the_injected_reader_and_clocks():
    wall = {"t": 100.0}
    mono = {"t": 50.0}
    state = {"value": "open"}
    monitor = SessionMonitor(lambda: state["value"], wall=lambda: wall["t"], mono=lambda: mono["t"])
    assert monitor.poll() is None
    state["value"] = "locked"
    assert monitor.poll() == "lock"
    state["value"] = "uac"
    assert monitor.poll() is None
    state["value"] = "open"
    assert monitor.poll() == "unlock"
    wall["t"] = 112.0
    mono["t"] = 51.0
    assert monitor.poll() == "resume"
    assert SessionMonitor(lambda: (_ for _ in ()).throw(RuntimeError("desktop")), wall=lambda: 0, mono=lambda: 0).poll() is None


def test_key_state_reader_is_injected():
    def get_async(vk: int) -> int:
        if vk in (0xA3, 0x11):
            return 0x8000
        return 0

    assert key_down_from(get_async, "ctrl") is True
    assert key_down_from(get_async, "a") is False
    assert key_down_from(lambda _vk: 0, "shift") is False


def test_probe_pid_treats_access_denied_as_elevated_and_errors_as_not():
    assert probe_pid(40, lambda _pid: "denied") is True
    assert probe_pid(40, lambda _pid: "ok") is False
    assert probe_pid(40, lambda _pid: "missing") is False
    assert probe_pid(0, lambda _pid: "denied") is False

    def boom(_pid: int) -> str:
        raise OSError("open failed")

    assert probe_pid(40, boom) is False


def test_second_instance_uses_the_injected_lock():
    state = {"held": False}

    def acquire():
        if state["held"]:
            return False
        state["held"] = True
        return "token"

    released = []
    first = InstanceLock(acquire=acquire, release=lambda token: released.append(token))
    second = InstanceLock(acquire=acquire, release=lambda token: released.append(token))
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert released == ["token"]


def test_main_refuses_a_second_instance(monkeypatch, capsys):
    monkeypatch.setattr(InstanceLock, "acquire", lambda _self: False)
    from main import main

    assert main([]) == 2
    assert "already running" in capsys.readouterr().out


def test_diag_installs_and_removes_a_fake_hook():
    hooks = []

    def factory():
        hook = FakeHook()
        hooks.append(hook)
        return hook

    assert run_diag(factory) == 0
    assert hooks[0].starts == 1
    assert hooks[0].stops == 1
    assert hooks[0].is_running() is False


def test_diag_without_a_windows_hook_leaves_nothing_running(monkeypatch):
    monkeypatch.setattr("engine.hook_engine.is_windows", lambda: False)

    def forbid_listener(*_args, **_kwargs):
        raise AssertionError("diag must not install a listener")

    monkeypatch.setattr("engine.hook_engine._pynput_listener", forbid_listener)
    assert run_diag() == 0


def test_hook_reinstalls_after_lock_and_pauses_after_three_failures(tmp_path):
    created, _opener, _injector, _audio, hook = service(tmp_path)
    created.set_enabled(True)
    assert created.apply_session("lock") == "stop"
    assert hook.is_running() is False
    assert created.config.enabled is True
    assert created.apply_session(None) == "none"
    assert created.apply_session("unlock") == "reinstall"
    assert hook.is_running() is True

    def boom():
        hook.starts += 1
        raise RuntimeError("hook died")

    hook.running = False
    hook.start = boom
    assert created.apply_session(None) == "reinstall"
    assert created.apply_session(None) == "reinstall"
    assert created.apply_session(None) == "pause"
    assert created.config.enabled is False
    assert hook.is_running() is False


def test_pulse_hotkeys_pass_through_and_youtube_still_fires(tmp_path):
    created, opener, _injector, _audio, _hook = service(tmp_path)
    created.add_binding(
        "global",
        Binding(
            id="bind-pulse-space",
            enabled=True,
            mode="chord",
            keys=["ctrl", "win", "space"],
            action=Action(type="open_url", url="https://example.com/pulse"),
        ),
    )
    created.set_enabled(True)
    assert created.handle_input("ctrl", True, 0.0) is False
    assert created.handle_input("win", True, 0.0) is False
    assert created.handle_input("space", True, 0.0) is False
    assert opener.urls == []
    for key in ("space", "win", "ctrl"):
        created.handle_input(key, False, 0.1)
    created.config.respect_pulse = False
    created.handle_input("ctrl", True, 0.2)
    created.handle_input("win", True, 0.2)
    assert created.handle_input("space", True, 0.2) is True
    assert opener.urls == ["https://example.com/pulse"]
    for key in ("space", "win", "ctrl"):
        created.handle_input(key, False, 0.3)
    created.config.respect_pulse = True
    for key in ("ctrl", "win", "shift"):
        assert created.handle_input(key, True, 0.4) is False
    assert created.handle_input("r", True, 0.4) is True
    assert opener.urls[-1] == "https://www.youtube.com"


def test_altgr_does_not_fire_a_ctrl_alt_chord(tmp_path):
    created, opener, _injector, _audio, _hook = service(tmp_path)
    created.add_binding(
        "global",
        Binding(
            id="bind-altgr",
            enabled=True,
            mode="chord",
            keys=["ctrl", "alt", "e"],
            action=Action(type="open_url", url="https://example.com/altgr"),
        ),
    )
    created.set_enabled(True)
    created.handle_input("ctrl", True, 0.0)
    created.handle_input("alt", True, 0.0, altgr=True)
    assert created.handle_input("e", True, 0.0, altgr=False) is False
    assert opener.urls == []


def test_elevated_window_passes_keys_and_releases_replayed_keys(tmp_path):
    created, opener, injector, _audio, _hook = service(tmp_path)
    created.set_enabled(True)
    created._detector.replayed.add("p")
    created.note_integrity(True)
    assert ("release", "p") in injector.events
    assert created.handle_input("ctrl", True, 0.0) is False
    assert created.handle_input("win", True, 0.0) is False
    assert created.handle_input("shift", True, 0.0) is False
    assert created.handle_input("r", True, 0.0) is False
    assert opener.urls == []
    created.handle_input("r", False, 0.1)
    created.note_integrity(False)
    assert created.handle_input("r", True, 0.2) is True
    assert opener.urls == ["https://www.youtube.com"]


def test_invalid_config_reload_keeps_the_previous_config(tmp_path):
    created, opener, _injector, _audio, _hook = service(tmp_path)
    created.set_enabled(True)
    path = created.store.path
    original = path.read_text(encoding="utf-8")
    path.write_text("{", encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    created.tick(1.0)
    assert path.read_text(encoding="utf-8") == "{"
    assert created.config.enabled is True
    assert any(item.id == "bind-youtube-chord" for item in created.config.global_profile().bindings)
    assert any("reload skipped" in message.text.lower() for message in created.poll())

    data = json.loads(original)
    for binding in data["profiles"][0]["bindings"]:
        if binding["id"] == "bind-youtube-chord":
            binding["enabled"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    created.tick(2.0)
    for key in ("ctrl", "win", "shift", "r"):
        created.handle_input(key, True, 2.0)
    assert opener.urls == []


def test_dry_run_logs_and_does_not_open_or_swallow(tmp_path):
    created, opener, injector, _audio, _hook = service(tmp_path)
    created.set_enabled(True)
    created.set_dry_run(True)
    for key in ("ctrl", "win", "shift"):
        assert created.handle_input(key, True, 0.0) is False
    assert created.handle_input("r", True, 0.0) is False
    assert opener.urls == []
    assert injector.events == []
    assert any(message.text.startswith("Dry run:") for message in created.poll())


def test_dry_run_panic_does_not_drop_the_hook(tmp_path):
    created, opener, _injector, _audio, hook = service(tmp_path)
    created.set_enabled(True)
    created.set_dry_run(True)
    for key in ("ctrl", "alt", "shift", "esc"):
        assert created.handle_input(key, True, 0.0) is False
    assert hook.is_running() is True
    assert created.config.enabled is True
    assert created._detector.enabled is True
    for key in ("esc", "shift", "alt", "ctrl"):
        created.handle_input(key, False, 0.1)
    for key in ("ctrl", "win", "shift"):
        created.handle_input(key, True, 0.2)
    assert created.handle_input("r", True, 0.2) is False
    assert opener.urls == []
    assert any("panic" in message.text.lower() for message in created.poll())


def test_pause_hotkey_leaves_the_hook_installed(tmp_path):
    created, opener, _injector, _audio, hook = service(tmp_path)
    created.set_enabled(True)
    created.set_binding_enabled("global", "bind-youtube-hold-p", True)
    assert created.handle_input("ctrl", True, 0.0) is False
    assert created.handle_input("alt", True, 0.0) is False
    assert created.handle_input("f12", True, 0.0) is True
    assert created.paused is True
    assert hook.is_running() is True
    assert created.snapshot()["paused"] is True
    created.handle_input("ctrl", False, 0.05)
    created.handle_input("alt", False, 0.05)
    created.handle_input("p", True, 0.1)
    created.tick(3.0)
    assert opener.urls == []
    created.handle_input("p", False, 3.05)
    created.handle_input("f12", False, 3.1)
    created.handle_input("ctrl", True, 3.15)
    created.handle_input("alt", True, 3.15)
    assert created.handle_input("f12", True, 3.2) is True
    assert created.paused is False
    assert hook.is_running() is True


def test_shutdown_releases_replayed_keys_but_not_a_held_modifier(tmp_path):
    created, _opener, injector, _audio, hook = service(tmp_path)
    created.set_enabled(True)
    created.physical.observe("shift", True, 0.0)
    created._detector.replayed.update({"a", "shift"})
    created.shutdown()
    assert ("release", "a") in injector.events
    assert ("release", "shift") not in injector.events
    assert hook.is_running() is False


def test_macro_does_not_release_a_modifier_the_user_is_holding(tmp_path):
    created, _opener, injector, _audio, _hook = service(tmp_path)
    created.physical.observe("shift", True, 0.0)
    created.run_action(Action(type="key_combo", keys=["shift", "1"]))
    assert ("press", "1") in injector.events
    assert ("press", "shift") not in injector.events
    assert ("release", "shift") not in injector.events


def test_reconcile_drops_a_stuck_key_the_fake_reader_says_is_up(tmp_path):
    created, *_rest = service(tmp_path)
    created._key_down = lambda name: name != "ctrl"
    created.set_enabled(True)
    created.handle_input("ctrl", True, 0.0)
    assert created.physical.holding("ctrl") is True
    created.tick(1.0)
    assert created.physical.holding("ctrl") is False
    assert "ctrl" not in created._detector.down_keys


def test_main_diag_skips_the_instance_lock(monkeypatch):
    def boom(self):
        raise AssertionError("diag must not take the instance lock")

    monkeypatch.setattr(InstanceLock, "acquire", boom)
    monkeypatch.setattr("engine.hook_engine.is_windows", lambda: False)

    def forbid_listener(*_args, **_kwargs):
        raise AssertionError("diag must not install a listener")

    monkeypatch.setattr("engine.hook_engine._pynput_listener", forbid_listener)
    from main import main

    assert main(["--diag"]) == 0

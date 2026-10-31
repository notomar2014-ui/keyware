"""Service wiring: profiles, persistence, panic, and the default YouTube chord."""

from __future__ import annotations

import json
import random

import pytest

from engine.config_store import ConfigStore
from engine.models import Action, Binding, validate_binding
from engine.startup import MemoryRegistry, StartupManager

from tests.fakes import service


def _svc(tmp_path):
    return service(tmp_path)


def test_youtube_chord_and_passthrough(tmp_path):
    created, opener, injector, _audio, hook = _svc(tmp_path)
    assert created.set_enabled(True) is None
    assert hook.starts == 1
    for key in ("ctrl", "win", "shift"):
        assert created.handle_input(key, True, 0.0) is False
    assert created.handle_input("r", True, 0.0) is True
    assert opener.urls == ["https://www.youtube.com"]
    assert created.handle_input("z", True, 0.1) is False
    assert created.handle_input("z", False, 0.12) is False
    assert injector.events == [("mask",)]


def test_hold_p_replays_a_short_press_and_opens_on_the_tier(tmp_path):
    created, opener, injector, audio, _hook = _svc(tmp_path)
    created.set_binding_enabled("global", "bind-youtube-hold-p", True)
    created.set_enabled(True)
    assert created.handle_input("p", True, 0.0) is True
    assert created.handle_input("p", False, 0.1) is True
    assert ("press", "p") in injector.events
    assert opener.urls == []

    injector.events.clear()
    created.handle_input("p", True, 1.0)
    created.tick(3.0)
    assert opener.urls == ["https://www.youtube.com"]
    assert 600 in audio.played or 850 in audio.played
    created.handle_input("p", False, 3.1)
    assert ("press", "p") not in injector.events


def test_gaming_profile_merges_global_chords(tmp_path):
    created, opener, injector, _audio, _hook = _svc(tmp_path)
    created.set_enabled(True)
    created.set_foreground(r"C:\games\javaw.exe")
    snapshot = created.snapshot()
    assert snapshot["profile_name"] == "Gaming"
    assert snapshot["process"] == "javaw.exe"

    created.handle_input("q", True, 0.0)
    created.tick(0.8)
    assert ("press", "shift") in injector.events
    assert ("press", "1") in injector.events

    for key in ("ctrl", "win", "shift"):
        created.handle_input(key, True, 1.0)
    created.handle_input("r", True, 1.0)
    assert opener.urls == ["https://www.youtube.com"]


def test_gaming_tier_two_macro_order(tmp_path):
    created, _opener, injector, audio, _hook = _svc(tmp_path)
    created.set_enabled(True)
    created.set_foreground("javaw.exe")
    created.handle_input("q", True, 0.0)
    created.tick(0.8)
    injector.events.clear()
    audio.played.clear()
    created.tick(2.0)
    kinds = [event[0] for event in injector.events]
    assert "click" in kinds
    click_at = kinds.index("click")
    assert injector.events[click_at] == ("click", "right")
    assert ("press", "2") in injector.events
    assert audio.played == [850]


def test_unmatched_window_uses_global(tmp_path):
    created, _opener, _injector, _audio, _hook = _svc(tmp_path)
    created.set_enabled(True)
    created.set_foreground("notepad.exe")
    assert created.snapshot()["profile_name"] == "Global"
    assert created.handle_input("q", True, 0.0) is False


def test_panic_releases_the_hook_and_ignores_later_keys(tmp_path):
    created, opener, _injector, _audio, hook = _svc(tmp_path)
    created.set_enabled(True)
    for key in ("ctrl", "alt", "shift"):
        created.handle_input(key, True, 0.0)
    assert created.handle_input("esc", True, 0.0) is True
    assert hook.stops >= 1
    assert hook.is_running() is False
    assert created.config.enabled is False
    assert created.snapshot()["hook"] == "off"
    assert created.handle_input("r", True, 1.0) is False
    messages = [message.text for message in created.poll()]
    assert any("Panic" in text for text in messages)
    created.handle_input("ctrl", True, 1.0)
    created.handle_input("win", True, 1.0)
    created.handle_input("shift", True, 1.0)
    created.handle_input("r", True, 1.0)
    assert opener.urls == []


def test_pause_toggle_persists_and_stops_swallowing(tmp_path):
    created, opener, _injector, _audio, hook = _svc(tmp_path)
    created.set_enabled(True)
    created.set_enabled(False)
    assert hook.stops == 1
    assert created.config.enabled is False
    reloaded = ConfigStore(created.store.path).load()[0]
    assert reloaded.enabled is False
    for key in ("ctrl", "win", "shift", "r"):
        assert created.handle_input(key, True, 0.0) is False
    assert opener.urls == []


def test_binding_edits_persist_and_take_effect(tmp_path):
    created, opener, _injector, _audio, _hook = _svc(tmp_path)
    created.set_enabled(True)
    created.add_binding(
        "global",
        Binding(
            id="bind-note",
            enabled=True,
            mode="tap",
            keys=["n"],
            label="Notepad",
            action=Action(type="open_app", app="notepad.exe"),
        ),
    )
    created.handle_input("n", True, 0.0)
    created.handle_input("n", False, 0.05)
    assert opener.apps == ["notepad.exe"]

    created.set_binding_enabled("global", "bind-note", False)
    opener.apps.clear()
    created.handle_input("n", True, 1.0)
    created.handle_input("n", False, 1.05)
    assert opener.apps == []

    raw = json.loads(created.store.path.read_text(encoding="utf-8"))
    assert raw["profiles"][0]["bindings"][-1]["enabled"] is False
    with pytest.raises(ValueError):
        created.add_binding(
            "global",
            Binding(
                id="bind-panic",
                enabled=True,
                mode="chord",
                keys=["ctrl", "alt", "shift", "esc"],
                action=Action(type="open_url", url="https://example.com"),
            ),
        )


def test_profile_crud_and_duplicate_trigger(tmp_path):
    created, _opener, _injector, _audio, _hook = _svc(tmp_path)
    profile = created.add_profile("Notepad", ["notepad.exe", "notepad.exe"])
    assert profile.process_names == ["notepad.exe"]
    created.update_profile(profile.id, "Notes", ["notepad.exe"], True)
    created.set_foreground("notepad.exe")
    assert created.snapshot()["profile_name"] == "Notes"
    with pytest.raises(ValueError):
        created.delete_profile("global")
    created.delete_profile(profile.id)
    created.set_foreground("notepad.exe")
    assert created.snapshot()["profile_name"] == "Global"

    with pytest.raises(ValueError):
        created.add_binding(
            "global",
            Binding(
                id="dup",
                enabled=True,
                mode="chord",
                keys=["ctrl", "win", "shift", "r"],
                action=Action(type="open_url", url="https://example.com"),
            ),
        )


def test_code_profile_types_text(tmp_path):
    created, _opener, injector, _audio, _hook = _svc(tmp_path)
    created.set_enabled(True)
    created.set_foreground("Code.exe")
    for key in ("ctrl", "win", "shift"):
        created.handle_input(key, True, 0.0)
    created.handle_input("l", True, 0.0)
    assert injector.events == [("text", "console.log();"), ("mask",)]


def test_audio_setting_and_leader_key(tmp_path):
    created, opener, _injector, audio, _hook = _svc(tmp_path)
    created.set_binding_enabled("global", "bind-youtube-hold-p", True)
    created.set_audio_enabled(False)
    created.set_enabled(True)
    created.handle_input("p", True, 0.0)
    created.tick(2.0)
    assert opener.urls == ["https://www.youtube.com"]
    assert audio.played == []
    created.set_leader_key("f13")
    assert created.config.leader_key == "f13"
    with pytest.raises(ValueError):
        created.set_leader_key("p")
    with pytest.raises(ValueError):
        created.set_leader_key("esc")


def test_leader_sample_opens_youtube_after_the_key_is_set(tmp_path):
    created, opener, _injector, _audio, _hook = _svc(tmp_path)
    created.set_leader_key("f13")
    created.set_enabled(True)
    created.handle_input("f13", True, 0.0)
    created.handle_input("f13", False, 0.05)
    created.handle_input("y", True, 0.1)
    assert opener.urls == ["https://www.youtube.com"]


def test_start_with_windows_writes_and_removes_the_run_value(tmp_path):
    created, _opener, _injector, _audio, _hook = _svc(tmp_path)
    assert created.config.start_with_windows is False and created.startup.is_enabled() is False
    assert created.set_start_with_windows(True) is True
    assert created.startup.is_enabled() is True and created.config.start_with_windows is True
    assert created.set_start_with_windows(False) is False
    assert created.startup.is_enabled() is False and created.config.start_with_windows is False


def test_corrupt_config_is_backed_up(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    store = ConfigStore(path)
    config, warnings = store.load()
    assert path.with_suffix(".json.bak").exists()
    assert any("unreadable" in warning for warning in warnings)
    assert config.global_profile().id == "global"
    assert any(binding.id == "bind-youtube-chord" for binding in config.global_profile().bindings)


def test_config_roundtrip_preserves_gaming_macro(tmp_path):
    created, *_rest = _svc(tmp_path)
    again = ConfigStore(created.store.path).load()[0]
    gaming = again.profile_by_id("gaming")
    macro = next(binding for binding in gaming.bindings if binding.mode == "hold" and binding.hold_ms == 2000)
    assert [step.op for step in macro.action.steps] == ["key", "delay", "mouse", "delay", "key"]
    assert macro.action.steps[1].ms == 50
    assert macro.action.steps[2].button == "right"
    assert macro.action.steps[3].ms == 20


def test_lock_timeout_fails_open(tmp_path):
    created, *_rest = _svc(tmp_path)
    created.set_enabled(True)
    created._lock.acquire()
    try:
        assert created.handle_input("r", True, 0.0) is False
    finally:
        created._lock.release()


def test_capture_message_reaches_the_ui_queue(tmp_path):
    created, *_rest = _svc(tmp_path)
    created.set_enabled(True)
    created.begin_capture()
    created.handle_input("ctrl", True, 0.0)
    created.handle_input("k", True, 0.0)
    messages = created.poll()
    captured = [message for message in messages if message.kind == "capture"]
    assert captured[0].keys == ("ctrl", "k")
    with pytest.raises(ValueError):
        created.set_enabled(False)
        created.begin_capture()


def test_theme_persists(tmp_path):
    created, *_rest = _svc(tmp_path)
    created.set_theme("light")
    assert ConfigStore(created.store.path).load()[0].theme == "light"


def test_startup_manager_memory_roundtrip():
    registry = MemoryRegistry()
    manager = StartupManager(registry=registry, command="keyware")
    assert manager.is_enabled() is False
    manager.set_enabled(True)
    assert registry.values["Keyware"] == "keyware"
    manager.set_enabled(False)
    assert "Keyware" not in registry.values


def test_validate_binding_rejects_shell_and_empty_macro(tmp_path):
    created, *_rest = _svc(tmp_path)
    config = created.config
    with pytest.raises(ValueError):
        validate_binding(
            Binding(
                id="x",
                enabled=True,
                mode="tap",
                keys=["a"],
                action=Action(type="macro", steps=[]),
            ),
            config,
        )


def test_run_action_is_explicit(tmp_path):
    created, opener, _injector, _audio, _hook = _svc(tmp_path)
    created.run_action(Action(type="open_url", url="https://www.youtube.com"))
    assert opener.urls == ["https://www.youtube.com"]


def test_jitter_helper_stays_inside_5_to_15(tmp_path):
    created, _opener, injector, _audio, _hook = _svc(tmp_path)
    created.executor.rng = random.Random(4)
    pauses: list[float] = []
    created.executor.sleeper = pauses.append
    created.run_action(Action(type="key_combo", keys=["a", "b"]))
    assert injector.events[0] == ("press", "a")
    assert pauses
    assert all(0.005 <= pause <= 0.015 for pause in pauses)

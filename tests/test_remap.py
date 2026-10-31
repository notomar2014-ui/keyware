"""Ordering, masking, stale keys, and config cleanup.

Every event here is synthetic. Nothing is opened, typed, or played.
"""

from __future__ import annotations

import json

from engine.config_store import ConfigStore
from engine.detector import STALE_HELD_MS
from engine.keystate import MASK_VK
from engine.models import Config, default_config, sanitize_config
from engine.startup import launch_command

from tests.fakes import service
from tests.test_detector import binding, detector


def test_mask_virtual_key_is_unassigned():
    assert MASK_VK == 0xE8


def test_default_hold_p_is_off_so_typing_is_not_delayed():
    sample = default_config()
    hold = next(item for item in sample.global_profile().bindings if item.id == "bind-youtube-hold-p")
    assert hold.enabled is False


def test_fresh_config_does_not_swallow_p(tmp_path):
    created, opener, injector, _audio, _hook = service(tmp_path)
    created.set_enabled(True)
    assert created.handle_input("p", True, 0.0) is False
    assert created.handle_input("a", True, 0.04) is False
    assert opener.urls == []
    assert injector.events == []


def test_ctrl_p_is_typed_even_when_hold_p_is_on(tmp_path):
    created, opener, _injector, _audio, _hook = service(tmp_path)
    created.set_binding_enabled("global", "bind-youtube-hold-p", True)
    created.set_enabled(True)
    assert created.handle_input("ctrl", True, 0.0) is False
    assert created.handle_input("p", True, 0.01) is False
    created.tick(3.0)
    assert opener.urls == []


def test_service_panic_releases_replayed_keys(tmp_path):
    created, _opener, injector, _audio, hook = service(tmp_path)
    created.set_binding_enabled("global", "bind-youtube-hold-p", True)
    created.set_enabled(True)
    created.handle_input("p", True, 0.0)
    created.handle_input("a", True, 0.04)
    for key in ("ctrl", "alt", "shift"):
        created.handle_input(key, True, 0.05)
    assert created.handle_input("esc", True, 0.05) is True
    assert ("release", "p") in injector.events
    assert ("release", "a") in injector.events
    assert hook.is_running() is False


def test_rollover_types_the_held_key_before_the_next_one(tmp_path):
    created, opener, injector, _audio, _hook = service(tmp_path)
    created.set_binding_enabled("global", "bind-youtube-hold-p", True)
    created.set_enabled(True)
    assert created.handle_input("p", True, 0.0) is True
    assert created.handle_input("a", True, 0.04) is True
    assert injector.events[:2] == [("press", "p"), ("press", "a")]
    assert opener.urls == []
    created.tick(3.0)
    assert opener.urls == []


def test_rollover_zero_does_not_flush_early():
    engine, clock = detector(binding("hold", ["p"], id="hold", hold_ms=2000))
    engine.config.rollover_ms = 0
    engine.handle("p", True, clock.now)
    pressed = engine.handle("a", True, clock.advance(40))
    assert pressed.suppress is False
    assert [effect for effect in pressed.effects if effect.kind.startswith("inject")] == []
    fired = [effect.binding_id for effect in engine.tick(clock.advance(2000)) if effect.kind == "fire"]
    assert fired == ["hold"]


def test_long_press_on_a_tap_dance_key_still_types():
    engine, clock = detector(binding("tap_dance", ["f"], taps=2))
    engine.handle("f", True, clock.now)
    released = engine.handle("f", False, clock.advance(400))
    assert [effect.key for effect in released.effects if effect.kind == "inject"] == ["f"]
    assert [effect for effect in released.effects if effect.kind == "fire"] == []


def test_panic_releases_keys_that_were_replayed_down():
    engine, clock = detector(binding("hold", ["p"], hold_ms=2000))
    engine.handle("p", True, clock.now)
    engine.handle("a", True, clock.advance(30))
    for key in ("ctrl", "alt", "shift"):
        engine.handle(key, True, clock.now)
    panic = engine.handle("esc", True, clock.now)
    ups = [effect.key for effect in panic.effects if effect.kind == "inject_up"]
    assert ups == ["a", "p"]
    assert any(effect.kind == "panic" for effect in panic.effects)
    assert engine.replayed == set()


def test_shift_release_waits_while_output_is_busy():
    engine, clock = detector(binding("hold", ["p"], hold_ms=2000))
    engine.handle("shift", True, clock.now)
    released = engine.handle("shift", False, clock.advance(20), output_busy=True)
    assert released.suppress is True
    assert [(effect.kind, effect.key) for effect in released.effects] == [("inject_up", "shift")]


def test_reconcile_forgets_a_modifier_the_os_has_released():
    engine, clock = detector(binding("chord", ["ctrl", "r"]))
    engine.handle("ctrl", True, clock.now)
    engine.handle("p", True, clock.advance(10))
    effects = engine.reconcile(lambda name: name != "ctrl", clock.advance(80))
    assert "ctrl" not in engine.modifiers
    assert "ctrl" not in engine.down_keys
    assert effects == [] or all(effect.kind != "fire" for effect in effects)


def test_stuck_key_expires_without_naming_it_in_the_log():
    engine, clock = detector(binding("hold", ["p"], hold_ms=2000))
    engine.handle("p", True, clock.now)
    effects = engine.tick(clock.advance(STALE_HELD_MS))
    assert "p" not in engine.down_keys
    assert any(effect.message == "Released a key that stayed down too long." for effect in effects)
    assert all("p" not in effect.message for effect in effects)


def test_key_repeat_keeps_a_long_hold_from_expiring():
    engine, clock = detector(binding("hold", ["p"], id="hold", hold_ms=800))
    engine.handle("p", True, clock.now)
    engine.handle("p", True, clock.advance(STALE_HELD_MS - 1000))
    effects = engine.tick(clock.advance(2000))
    assert "p" in engine.down_keys
    assert [effect.binding_id for effect in effects if effect.kind == "fire"] == ["hold"]


def test_win_chord_asks_for_a_mask_and_a_plain_chord_does_not():
    engine, clock = detector(binding("chord", ["ctrl", "win", "shift", "r"], id="yt"))
    for key in ("ctrl", "win", "shift"):
        engine.handle(key, True, clock.now)
    pressed = engine.handle("r", True, clock.now)
    assert any(effect.kind == "mask" for effect in pressed.effects)

    plain, clock = detector(binding("chord", ["ctrl", "k"]))
    plain.handle("ctrl", True, clock.now)
    pressed = plain.handle("k", True, clock.now)
    assert [effect.kind for effect in pressed.effects if effect.kind == "mask"] == []


def test_sanitize_rewrites_keys_and_disables_invalid_bindings():
    config = Config.from_dict(
        {
            "leader_key": "F13",
            "rollover_ms": 0,
            "profiles": [
                {
                    "id": "global",
                    "name": "Global",
                    "enabled": True,
                    "process_names": [],
                    "bindings": [
                        {
                            "id": "bad",
                            "enabled": True,
                            "mode": "tap",
                            "keys": ["not-a-key"],
                            "label": "Broken",
                            "action": {"type": "open_url", "url": "https://example.com"},
                        },
                        {
                            "id": "ctrl-r",
                            "enabled": True,
                            "mode": "chord",
                            "keys": ["Ctrl", "R"],
                            "label": "Open",
                            "action": {"type": "open_url", "url": "https://example.com"},
                        },
                        {
                            "id": "lead",
                            "enabled": True,
                            "mode": "leader",
                            "keys": ["Y"],
                            "label": "Leader",
                            "action": {"type": "open_url", "url": "https://example.com"},
                        },
                    ],
                }
            ],
        }
    )
    warnings = sanitize_config(config)
    assert config.leader_key == "f13"
    assert config.rollover_ms == 0
    broken = next(item for item in config.global_profile().bindings if item.id == "bad")
    assert broken.enabled is False
    assert broken.keys == ["not-a-key"]
    chord = next(item for item in config.global_profile().bindings if item.id == "ctrl-r")
    assert chord.enabled is True
    assert chord.keys == ["ctrl", "r"]
    leader = next(item for item in config.global_profile().bindings if item.id == "lead")
    assert leader.enabled is True
    assert leader.keys == ["y"]
    assert any("Broken" in warning for warning in warnings)
    assert len(config.global_profile().bindings) == 3


def test_store_saves_a_sanitized_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "profiles": [
                    {
                        "id": "global",
                        "name": "Global",
                        "enabled": True,
                        "process_names": [],
                        "bindings": [
                            {
                                "id": "bad",
                                "enabled": True,
                                "mode": "tap",
                                "keys": ["nope"],
                                "action": {"type": "open_url", "url": "https://example.com"},
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _config, warnings = ConfigStore(path).load()
    assert any("unknown" in warning for warning in warnings)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["profiles"][0]["bindings"][0]["enabled"] is False
    assert saved["rollover_ms"] == 120


def test_launch_command_starts_in_the_background():
    assert launch_command().rstrip().endswith("--background")


def test_main_forwards_the_minimized_flag(monkeypatch):
    import main as entry
    from engine.instance import InstanceLock

    # Stand-in for the session mutex, on Windows and on Linux.
    monkeypatch.setattr(InstanceLock, "acquire", lambda _self: True)
    monkeypatch.setattr(InstanceLock, "release", lambda _self: None)
    monkeypatch.setattr(entry, "_install_excepthook", lambda: None)
    seen: dict[str, bool] = {}

    def fake_run(minimized: bool = False, background: bool = False) -> None:
        seen["minimized"] = minimized
        seen["background"] = background

    import ui.app as app_module

    monkeypatch.setattr(app_module, "run", fake_run)
    assert entry.main(["--minimized"]) == 0
    assert seen["minimized"] is True and seen["background"] is False
    assert entry.main(["--background"]) == 0
    assert seen["minimized"] is True and seen["background"] is True
    assert entry.main([]) == 0
    assert seen["minimized"] is False and seen["background"] is False

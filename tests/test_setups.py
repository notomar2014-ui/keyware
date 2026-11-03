"""Setups inside a profile (hold 1-9) and the on/off shortcut. Synthetic events only."""

from __future__ import annotations

import pytest

from engine.conflicts import find_conflicts
from engine.detector import InputDetector
from engine.models import (
    Action, Binding, Config, Profile, parse_setup_text, sanitize_config, setup_text,
)

from tests.fakes import service


def _b(bid, keys, setup=0, mode="chord"):
    return Binding(id=bid, enabled=True, mode=mode, keys=list(keys), setup=setup,
                   action=Action(type="open_url", url=f"https://example.com/{bid}"))


def make(hold_seconds=8, active=1):
    game = Profile(
        id="game", name="Game", process_names=["javaw.exe"], enabled=True,
        setups={1: "Elytra", 2: "Spear"}, active_setup=active,
        bindings=[
            _b("always", ["ctrl", "g"]),
            _b("elytra", ["ctrl", "e"], setup=1),
            _b("spear", ["ctrl", "e"], setup=2),
        ],
    )
    other = Profile(id="other", name="Other", process_names=["x.exe"], enabled=True, setups={1: "A", 2: "B"})
    config = Config(enabled=True, setup_hold_seconds=hold_seconds,
                    profiles=[Profile(id="global", name="Global", process_names=[], enabled=True), game, other])
    det = InputDetector(config)
    det.set_enabled(True)
    det.set_profiles(config.global_profile(), game)
    return det, config, game


def fires(det, key, now):
    """Press-release a chord with ctrl; return the fired binding ids."""
    out = det.handle("ctrl", True, now).effects + det.handle(key, True, now).effects
    det.handle(key, False, now + 0.01)
    det.handle("ctrl", False, now + 0.01)
    return [e.binding_id for e in out if e.kind == "fire"]


def setup_effects(effects):
    return [e for e in effects if e.kind == "setup"]


def test_only_active_setup_and_always_bindings_are_live():
    det, _config, _game = make()
    assert fires(det, "e", 0.0) == ["elytra"]
    assert fires(det, "g", 0.1) == ["always"]


def test_hold_8s_switches_and_number_key_passes_through():
    det, _config, game = make()
    down = det.handle("2", True, 0.0)
    assert down.suppress is False  # never blocked
    assert det.tick(7.9) == []
    effects = det.tick(8.0)
    assert [e.message for e in setup_effects(effects)] == ["Setup 2: Spear"]
    assert game.active_setup == 2
    assert det.handle("2", False, 8.5).suppress is False
    assert fires(det, "e", 9.0) == ["spear"]


def test_tap_and_short_hold_do_nothing():
    det, _config, game = make()
    assert det.handle("2", True, 0.0).suppress is False
    assert det.handle("2", False, 0.1).suppress is False
    det.handle("2", True, 1.0)
    det.handle("2", False, 7.0)
    assert setup_effects(det.tick(20.0)) == []
    assert game.active_setup == 1


def test_key_repeat_keeps_original_start_and_fires_once():
    det, _config, game = make()
    det.handle("2", True, 0.0)
    for i in range(1, 100):
        assert det.handle("2", True, i * 0.1).suppress is False  # auto-repeat
    effects = det.tick(10.0)
    assert len(setup_effects(effects)) == 1
    assert setup_effects(det.tick(20.0)) == []  # still held: once per press
    assert game.active_setup == 2


def test_missing_setup_is_ignored():
    det, _config, game = make()
    det.handle("5", True, 0.0)
    assert setup_effects(det.tick(9.0)) == []
    assert game.active_setup == 1


def test_profile_switch_mid_hold_cancels():
    det, config, game = make()
    det.handle("2", True, 0.0)
    det.set_profiles(config.global_profile(), config.profile_by_id("other"))
    assert setup_effects(det.tick(9.0)) == []
    assert game.active_setup == 1
    assert config.profile_by_id("other").active_setup == 0


def test_pause_mid_hold_cancels_and_paused_press_is_not_timed():
    det, _config, game = make()
    det.handle("2", True, 0.0)
    det.set_paused(True)
    det.set_paused(False)
    assert setup_effects(det.tick(9.0)) == []
    det.handle("2", False, 9.1)
    det.set_paused(True)
    det.handle("2", True, 10.0)
    det.set_paused(False)
    assert setup_effects(det.tick(30.0)) == []
    assert game.active_setup == 1


def test_modifier_held_and_numpad_do_not_start_a_hold():
    det, _config, game = make()
    det.handle("shift", True, 0.0)
    det.handle("2", True, 0.0)
    assert setup_effects(det.tick(9.0)) == []
    det.handle("2", False, 9.0)
    det.handle("shift", False, 9.0)
    det.handle("numpad2", True, 10.0)
    assert setup_effects(det.tick(20.0)) == []
    assert game.active_setup == 1


def test_configurable_hold_and_zero_turns_off():
    det, _config, game = make(hold_seconds=2)
    det.handle("2", True, 0.0)
    assert setup_effects(det.tick(2.0))
    assert game.active_setup == 2
    det, _config, game = make(hold_seconds=0)
    det.handle("2", True, 0.0)
    assert setup_effects(det.tick(60.0)) == []


def test_round_trip_and_sanitize():
    _det, config, _game = make()
    again = Config.from_dict(config.to_dict())
    game = again.profile_by_id("game")
    assert game.setups == {1: "Elytra", 2: "Spear"} and game.active_setup == 1
    assert [b.setup for b in game.bindings] == [0, 1, 2]
    assert again.setup_hold_seconds == 8
    assert Config.from_dict({}).setup_hold_seconds == 8
    game.active_setup = 7
    sanitize_config(again)
    assert game.active_setup == 0


def test_setup_text_parsing():
    assert parse_setup_text("1 Elytra, 2: Spear") == {1: "Elytra", 2: "Spear"}
    assert parse_setup_text("") == {}
    assert setup_text({2: "Spear", 1: "Elytra"}) == "1 Elytra, 2 Spear"
    with pytest.raises(ValueError):
        parse_setup_text("10 Nope")
    with pytest.raises(ValueError):
        parse_setup_text("1 a, 1 b")


def test_same_trigger_in_different_setups_is_not_a_conflict():
    _det, _config, game = make()
    elytra, spear = game.bindings[1], game.bindings[2]
    assert find_conflicts(spear, [elytra]) == []
    assert find_conflicts(game.bindings[0], [_b("x", ["ctrl", "g"], setup=1)])


# --- service: persistence, toast, toggle ---------------------------------------


def _svc(tmp_path):
    created, opener, _inj, _audio, hook = service(tmp_path)
    created.set_enabled(True)
    profile = created.add_profile("Game", ["mygame.exe"])
    created.set_profile_setups(profile.id, {1: "Elytra", 2: "Spear"})
    created.set_foreground("mygame.exe")
    created.poll()
    return created, profile, opener, hook


def test_service_switch_persists_and_toasts(tmp_path):
    created, profile, _opener, _hook = _svc(tmp_path)
    assert created.config.profile_by_id(profile.id).active_setup == 1
    assert created.handle_input("2", True, 0.0) is False
    created.tick(8.0)
    created.handle_input("2", False, 8.1)
    toasts = [m.text for m in created.poll() if m.kind == "toast"]
    assert toasts == ["Setup 2: Spear"]
    saved = created.store.load()
    saved = saved[0] if isinstance(saved, tuple) else saved
    assert saved.profile_by_id(profile.id).active_setup == 2


def test_binding_setup_must_exist(tmp_path):
    created, profile, _opener, _hook = _svc(tmp_path)
    with pytest.raises(ValueError):
        created.add_binding(profile.id, _b("bad", ["ctrl", "k"], setup=4))
    created.add_binding(profile.id, _b("ok", ["ctrl", "k"], setup=2))
    created.set_binding_setup(profile.id, "ok", 1)
    assert next(b for b in created.bindings(profile.id) if b.id == "ok").setup == 1


def _chord(created, keys, now):
    result = False
    for key in keys:
        result = created.handle_input(key, True, now)
    for key in reversed(keys):
        created.handle_input(key, False, now + 0.05)
    return result


def test_toggle_shortcut_turns_off_and_on_with_toasts(tmp_path):
    created, _profile, _opener, hook = _svc(tmp_path)
    assert _chord(created, ["ctrl", "alt", "f12"], 0.0) is True
    assert created.paused is True and hook.is_running()
    assert _chord(created, ["ctrl", "alt", "f12"], 1.0) is True
    assert created.paused is False
    toasts = [m.text for m in created.poll() if m.kind == "toast"]
    assert toasts == ["Keyware off", "Keyware on"]


def test_toggle_shortcut_is_configurable(tmp_path):
    created, _profile, _opener, _hook = _svc(tmp_path)
    created.set_pause_hotkey(["ctrl", "shift", "f11"])
    assert created.config.pause_hotkey == ["ctrl", "shift", "f11"]
    assert created.toggle_hotkey_text() == "Ctrl + Shift + F11"
    assert _chord(created, ["ctrl", "alt", "f12"], 0.0) is False
    assert created.paused is False
    assert _chord(created, ["ctrl", "shift", "f11"], 1.0) is True
    assert created.paused is True
    for bad in (["f11"], ["ctrl"], []):
        with pytest.raises(ValueError):
            created.set_pause_hotkey(bad)


def test_paused_number_hold_does_not_switch(tmp_path):
    created, profile, _opener, _hook = _svc(tmp_path)
    _chord(created, ["ctrl", "alt", "f12"], 0.0)
    created.handle_input("2", True, 1.0)
    created.tick(20.0)
    assert created.config.profile_by_id(profile.id).active_setup == 1

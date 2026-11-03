"""v1.3.3: Gaming rename + migration, auto-click, undo delete, donate nudge, config edge cases.

Everything is mocked: the injector records, the clock is fake, nothing is sent to Windows.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from assist.ai_binder import AIBinderError, parse_reply
from engine import __version__
from engine.autoclick import AutoClicker
from engine.config_store import ConfigStore, migrate_gaming_profile
from engine.detector import InputDetector
from engine.models import Action, Binding, Config, Profile, default_config, sanitize_config, validate_binding
from engine import nudge

from tests.fakes import Clock, RecordingInjector, service


def clicks(injector, button="left"):
    return sum(1 for event in injector.events if event == ("mouse_down", button))


def autoclick(mode="tap", keys=("r",), *, button="left", cps=10, click_mode="hold", max_seconds=0, ident="ac"):
    return Binding(id=ident, enabled=True, mode=mode, keys=list(keys), hold_ms=800, label="Auto",
                   action=Action(type="auto_click", button=button, cps=cps, click_mode=click_mode,
                                 max_seconds=max_seconds))


def armed(tmp_path, binding, profile="global"):
    created, opener, injector, audio, hook = service(tmp_path)
    created.add_binding(profile, binding)
    created.set_enabled(True)
    injector.events.clear()
    return created, injector, hook


# ---- version + rename ----------------------------------------------------

def test_version_is_134():
    assert __version__ == "1.3.7"


def test_default_config_has_gaming_not_minecraft():
    config = default_config()
    names = [p.name for p in config.profiles]
    assert "Gaming" in names and "Minecraft" not in names
    gaming = config.profile_by_id("gaming")
    assert gaming.process_names == ["javaw.exe", "minecraft.exe"]
    assert "v133_gaming" in config.migrations
    assert config.donate_url == ""


def _old_config(tmp_path):
    raw = default_config().to_dict()
    raw.pop("migrations")
    raw.pop("donate_url")
    for profile in raw["profiles"]:
        if profile["id"] == "gaming":
            profile["id"], profile["name"] = "minecraft", "Minecraft"
            profile["process_names"].append("robloxplayerbeta.exe")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path, raw


def test_migration_renames_once_keeps_bindings_and_backs_up(tmp_path):
    path, raw = _old_config(tmp_path)
    before = next(p for p in raw["profiles"] if p["id"] == "minecraft")
    store = ConfigStore(path, backup_dir=tmp_path / "bk")
    config, warnings = store.load()
    gaming = config.profile_by_id("gaming")
    assert gaming is not None and gaming.name == "Gaming"
    assert [b.id for b in gaming.bindings] == [b["id"] for b in before["bindings"]]
    assert gaming.process_names == before["process_names"]
    assert "v133_gaming" in config.migrations
    backups = list((tmp_path / "bk").glob("config_before_v133_*.json"))
    assert len(backups) == 1
    assert any("Minecraft" in p["name"] for p in json.loads(backups[0].read_text())["profiles"])
    assert any("Renamed the Minecraft profile" in w for w in warnings)
    saved = json.loads(path.read_text())
    assert "v133_gaming" in saved["migrations"]

    # A profile the user names "Minecraft" later is left alone; no second backup.
    config.profiles.append(Profile(id="mc2", name="Minecraft", process_names=["x.exe"], enabled=True, bindings=[]))
    store.save(config)
    again, _ = store.load()
    assert again.profile_by_id("mc2").name == "Minecraft"
    assert len(list((tmp_path / "bk").glob("*.json"))) == 1


def test_migration_skipped_when_backup_fails(tmp_path, monkeypatch):
    path, _raw = _old_config(tmp_path)
    store = ConfigStore(path, backup_dir=tmp_path / "bk")

    def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(store, "backup_before_migration", broken)
    config, warnings = store.load()
    assert config.profile_by_id("minecraft").name == "Minecraft"
    assert "v133_gaming" not in config.migrations
    assert any("Could not back up" in w for w in warnings)


def test_migration_does_not_clobber_existing_gaming_profile():
    raw = {"profiles": [
        {"id": "minecraft", "name": "Minecraft", "process_names": ["javaw.exe"], "bindings": []},
        {"id": "gaming", "name": "Gaming", "process_names": ["game.exe"], "bindings": []},
    ]}
    assert migrate_gaming_profile(raw) is True
    assert raw["profiles"][0] == {"id": "minecraft", "name": "Minecraft", "process_names": ["javaw.exe"], "bindings": []}
    assert raw["migrations"] == ["v133_gaming"]
    assert migrate_gaming_profile(raw) is False


def test_backup_never_overwrites(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    store = ConfigStore(path, backup_dir=tmp_path / "bk")
    first = store.backup_before_migration()
    second = store.backup_before_migration()
    assert first != second and first.exists() and second.exists()


# ---- config edge cases ------------------------------------------------------

@pytest.mark.parametrize("content", ["", "   ", "{", "[]", "null", "{\"profiles\": 5}"])
def test_empty_or_corrupt_config_recovers(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    config, warnings = ConfigStore(path, backup_dir=tmp_path / "bk").load()
    assert config.global_profile() is not None
    json.loads(path.read_text())  # rewritten as valid JSON


def test_missing_keys_get_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"profiles": [{"id": "global", "name": "Global", "bindings": [
        {"id": "x", "mode": "tap", "keys": ["f2"], "action": {"type": "auto_click"}}]}]}), encoding="utf-8")
    config, _ = ConfigStore(path, backup_dir=tmp_path / "bk").load()
    action = config.global_profile().bindings[0].action
    assert (action.button, action.cps, action.click_mode, action.max_seconds) == ("left", 10, "hold", 0)
    assert config.donate_url == ""


def test_bad_autoclick_values_disable_not_crash(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"profiles": [{"id": "global", "name": "Global", "bindings": [
        {"id": "x", "enabled": True, "mode": "tap", "keys": ["f2"],
         "action": {"type": "auto_click", "cps": "lots", "button": "thumb"}}]}]}), encoding="utf-8")
    config, _ = ConfigStore(path, backup_dir=tmp_path / "bk").load()
    assert config.global_profile().bindings[0].enabled is False


def test_donate_url_must_be_web():
    config = Config(donate_url="javascript:alert(1)")
    warnings = sanitize_config(config)
    assert config.donate_url == "" and warnings
    config = Config(donate_url="https://ko-fi.com/example")
    sanitize_config(config)
    assert config.donate_url == "https://ko-fi.com/example"


# ---- auto-click model -------------------------------------------------------

def test_autoclick_roundtrip_and_validation():
    binding = autoclick(cps=25, click_mode="toggle", max_seconds=30, button="right")
    again = Binding.from_dict(binding.to_dict())
    assert again.action == binding.action
    validate_binding(binding, Config())
    with pytest.raises(ValueError):
        validate_binding(autoclick(cps=0), Config())
    with pytest.raises(ValueError):
        validate_binding(autoclick(cps=51), Config())
    with pytest.raises(ValueError):
        validate_binding(autoclick(max_seconds=4000), Config())
    with pytest.raises(ValueError, match="While held"):
        validate_binding(autoclick(mode="tap_dance", keys=("f8",)), Config())
    dance = autoclick(mode="tap_dance", keys=("f8",), click_mode="toggle")
    dance.taps = 2
    validate_binding(dance, Config())


# ---- detector ---------------------------------------------------------------

def test_detector_fires_held_autoclick_on_key_down_and_stops_on_release():
    config = Config(profiles=[Profile(id="global", name="Global", process_names=[], enabled=True,
                                      bindings=[autoclick()])])
    engine = InputDetector(config)
    engine.set_profiles(config.global_profile(), config.global_profile())
    engine.set_enabled(True)
    down = engine.handle("r", True, 0.0)
    assert down.suppress
    fire = [e for e in down.effects if e.kind == "fire"]
    assert fire and fire[0].key == "r" and engine.autoclick_keys == {"r": "ac"}
    assert engine.handle("r", True, 0.03).effects == []  # auto-repeat does not re-fire
    up = engine.handle("r", False, 0.5)
    assert up.suppress and up.effects[0].kind == "autoclick_stop"
    assert engine.autoclick_keys == {}


# ---- service + clicker --------------------------------------------------------

def test_hold_r_clicks_at_rate_and_stops_on_release(tmp_path):
    created, injector, _hook = armed(tmp_path, autoclick(cps=10))
    assert created.handle_input("r", True, 0.0) is True
    assert created.clicker.running_binding == "ac"
    created.clicker.pump(1.0)
    assert clicks(injector) in (10, 11)
    assert injector.events.count(("mouse_up", "left")) == clicks(injector)
    created.handle_input("r", False, 1.0)
    assert created.clicker.running_binding is None
    before = clicks(injector)
    created.clicker.pump(1.0)
    assert clicks(injector) == before


@pytest.mark.parametrize("cps", [1, 15, 50])
def test_rate_is_accurate(tmp_path, cps):
    created, injector, _hook = armed(tmp_path, autoclick(cps=cps))
    created.handle_input("r", True, 0.0)
    created.clicker.pump(2.0)
    assert abs(clicks(injector) - 2 * cps) <= 1


def test_toggle_starts_and_stops(tmp_path):
    created, injector, _hook = armed(tmp_path, autoclick(keys=("f6",), click_mode="toggle", button="right", cps=20))
    created.handle_input("f6", True, 0.0)
    created.handle_input("f6", False, 0.05)
    assert created.clicker.running_binding == "ac"
    created.clicker.pump(0.5)
    assert clicks(injector, "right") >= 9
    created.handle_input("f6", True, 0.6)
    created.handle_input("f6", False, 0.65)
    assert created.clicker.running_binding is None


@pytest.mark.parametrize("stopper", ["pause", "panic", "off", "profile", "elevated", "shutdown", "delete", "lost"])
def test_every_stop_path(tmp_path, stopper):
    created, injector, hook = armed(tmp_path, autoclick(keys=("f6",), click_mode="toggle"), profile="gaming")
    created.set_foreground("javaw.exe")
    created.handle_input("f6", True, 0.0)
    created.handle_input("f6", False, 0.05)
    assert created.clicker.running_binding == "ac"
    if stopper == "pause":
        created.toggle_pause()
    elif stopper == "panic":
        for key in ("ctrl", "alt", "shift"):
            created.handle_input(key, True, 0.1)
        created.handle_input("esc", True, 0.1)
    elif stopper == "off":
        created.set_enabled(False)
    elif stopper == "profile":
        created.set_foreground("notepad.exe")
    elif stopper == "elevated":
        created.note_integrity(True)
    elif stopper == "shutdown":
        created.shutdown()
    elif stopper == "delete":
        created.delete_binding("gaming", "ac")
    elif stopper == "lost":
        created.apply_session("lock")
    assert created.clicker.running_binding is None
    before = clicks(injector)
    created.clicker.pump(1.0)
    assert clicks(injector) == before


def test_guard_stops_if_profile_changes_mid_click(tmp_path):
    created, injector, _hook = armed(tmp_path, autoclick(keys=("f6",), click_mode="toggle"), profile="gaming")
    created.set_foreground("javaw.exe")
    created.handle_input("f6", True, 0.0)
    created.handle_input("f6", False, 0.05)
    created._detector.active_binding_ids = frozenset()  # as if the profile vanished under it
    created.clicker.pump(0.5)
    assert created.clicker.running_binding is None and clicks(injector) == 0


def test_safety_stop(tmp_path):
    created, injector, _hook = armed(tmp_path, autoclick(keys=("f6",), click_mode="toggle", cps=10, max_seconds=2))
    created.handle_input("f6", True, 0.0)
    created.handle_input("f6", False, 0.05)
    created.clicker.pump(5.0)
    assert created.clicker.running_binding is None
    assert 19 <= clicks(injector) <= 21


def test_sendinput_failure_stops_and_releases(tmp_path):
    created, injector, _hook = armed(tmp_path, autoclick(keys=("f6",), click_mode="toggle"))

    def refuse(_button):
        raise OSError("SendInput sent 0 of 1")

    injector.mouse_up = refuse
    created.handle_input("f6", True, 0.0)
    created.handle_input("f6", False, 0.05)
    created.clicker.pump(1.0)
    assert created.clicker.running_binding is None
    assert any("refused" in m.text for m in created.poll())


def test_run_button_and_dry_run_never_click(tmp_path):
    created, injector, _hook = armed(tmp_path, autoclick(keys=("f6",), click_mode="toggle"))
    created.run_action(autoclick().action)
    assert created.clicker.running_binding is None
    created.set_dry_run(True)
    created.handle_input("f6", True, 0.0)
    created.handle_input("f6", False, 0.05)
    assert created.clicker.running_binding is None and injector.events == []


def test_threaded_clicker_real_time_stop_is_fast():
    injector = RecordingInjector()
    clicker = AutoClicker(injector)
    clicker.start("b", None, Action(type="auto_click", cps=50, click_mode="toggle"))
    time.sleep(0.3)
    clicker.stop_all("test")
    stopped = clicks(injector)
    time.sleep(0.1)
    clicker.close()
    assert clicks(injector) <= stopped + 1
    assert 5 <= stopped <= 20
    assert injector.events[-1][0] == "mouse_up"


# ---- delete / undo -------------------------------------------------------------

def test_delete_then_restore_in_place(tmp_path):
    created, *_ = service(tmp_path)
    ids = [b.id for b in created.bindings("global")]
    removed = created.delete_binding("global", ids[1])
    assert removed is not None and removed[1] == 1
    assert ids[1] not in [b.id for b in created.bindings("global")]
    created.restore_binding("global", *removed)
    assert [b.id for b in created.bindings("global")] == ids
    stored = json.loads((tmp_path / "config.json").read_text())
    assert [b["id"] for b in stored["profiles"][0]["bindings"]] == ids
    created.restore_binding("global", *removed)  # double undo is harmless
    assert [b.id for b in created.bindings("global")] == ids


def test_restore_comes_back_off_if_trigger_taken(tmp_path):
    created, *_ = service(tmp_path)
    removed = created.delete_binding("global", "bind-youtube-chord")
    created.add_binding("global", Binding(id="new", enabled=True, mode="chord", keys=["ctrl", "win", "shift", "r"],
                                          action=Action(type="open_url", url="https://example.com")))
    notes = created.restore_binding("global", *removed)
    assert notes and next(b for b in created.bindings("global") if b.id == "bind-youtube-chord").enabled is False


def test_delete_missing_binding_returns_none(tmp_path):
    created, *_ = service(tmp_path)
    assert created.delete_binding("global", "nope") is None


# ---- nudge ----------------------------------------------------------------------

def test_nudge_on_even_launches_only(tmp_path):
    path = tmp_path / "state.json"
    url = "https://example.com/donate"
    seen = []
    for _ in range(7):
        state = nudge.record_launch(path, now=1000.0)
        seen.append(nudge.should_nudge(state, url))
    assert seen == [False, True, False, True, False, True, False]


def test_nudge_hidden_without_link_and_after_dont_ask(tmp_path):
    path = tmp_path / "state.json"
    nudge.record_launch(path)
    state = nudge.record_launch(path)
    assert nudge.should_nudge(state, "") is False
    assert nudge.should_nudge(state, "https://example.com/donate") is True
    nudge.turn_off(path, state)
    assert nudge.should_nudge(nudge.load_state(path), "https://example.com/donate") is False


def test_nudge_state_survives_garbage(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json", encoding="utf-8")
    assert nudge.record_launch(path).launches == 1


# ---- AI binder ------------------------------------------------------------------

def _reply(action, trigger=None, profile=None):
    return json.dumps({"ok": True, "label": "Auto", "profile": profile or {"scope": "global"},
                       "trigger": trigger or {"mode": "tap", "keys": ["r"]}, "action": action, "notes": ""})


def test_ai_parses_while_held_autoclick():
    proposal = parse_reply(_reply({"type": "auto_click", "button": "left", "cps": 15, "click_mode": "hold"},
                                  profile={"scope": "app", "name": "Gaming", "process_names": ["javaw.exe"]}))
    action = proposal.binding.action
    assert (action.type, action.button, action.cps, action.click_mode) == ("auto_click", "left", 15, "hold")
    assert proposal.profile_name == "Gaming"
    assert ("Action", "Auto-click") in proposal.summary_lines()


def test_ai_parses_toggle_and_clamps_speed():
    proposal = parse_reply(_reply({"type": "auto_click", "button": "right", "cps": 99, "mode": "toggle"},
                                  trigger={"mode": "tap", "keys": ["f6"]}))
    assert proposal.binding.action.cps == 50 and proposal.binding.action.click_mode == "toggle"
    assert any("limited" in note for note in proposal.notes)


def test_ai_hold_trigger_without_delay_becomes_key_down():
    proposal = parse_reply(_reply({"type": "auto_click", "cps": 12}, trigger={"mode": "hold", "keys": ["r"]}))
    assert proposal.binding.mode == "tap"


def test_ai_rejects_bad_autoclick():
    with pytest.raises(AIBinderError):
        parse_reply(_reply({"type": "auto_click", "button": "thumb"}))
    with pytest.raises(AIBinderError):
        parse_reply(_reply({"type": "auto_click", "click_mode": "forever"}))
    with pytest.raises(AIBinderError):
        parse_reply(_reply({"type": "auto_click", "click_mode": "hold"}, trigger={"mode": "double_tap", "keys": ["f8"]}))

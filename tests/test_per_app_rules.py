import json

import pytest

from assist.ai_binder import parse_reply
from engine.detector import Effect
from engine.models import Action, Binding, Config, validate_binding
from engine.profiles import app_rule_mode, apply_app_rules


def _binding(**kw):
    return Binding(id="b1", enabled=True, mode="chord", keys=["ctrl", "shift", "s"],
                   action=Action(type="open_url", url="https://example.com"), **kw)


def test_round_trip_and_list_form():
    b = _binding(app_rules={"cursor.exe": "app", "code.exe": "both"})
    again = Binding.from_dict(json.loads(json.dumps(b.to_dict())))
    assert again.app_rules == {"cursor.exe": "app", "code.exe": "both"}
    raw = b.to_dict()
    raw["app_rules"] = [{"app": "C:\\Apps\\Cursor.EXE", "mode": "App"}, {"app": "chrome", "mode": "both"}]
    assert Binding.from_dict(raw).app_rules == {"cursor.exe": "app", "chrome.exe": "both"}


def test_old_config_loads_unchanged():
    raw = _binding().to_dict()
    assert "app_rules" not in raw
    b = Binding.from_dict(raw)
    assert b.app_rules == {} and b.to_dict() == raw


def test_validate_modes():
    b = _binding(app_rules={"cursor.exe": "sometimes"})
    with pytest.raises(ValueError):
        validate_binding(b, Config())
    ok = _binding(app_rules={"Cursor.exe": "app"})
    validate_binding(ok, Config())
    assert ok.app_rules == {"cursor.exe": "app"}


def test_mode_lookup():
    rules = {"cursor.exe": "app"}
    assert app_rule_mode(rules, "C:\\x\\CURSOR.EXE") == "app"
    assert app_rule_mode(rules, "cursor") == "app"
    assert app_rule_mode(rules, "") == "keyware"
    assert app_rule_mode(rules, "chrome.exe") == "keyware"
    assert app_rule_mode({}, "cursor.exe") == "keyware"


def _effects():
    return [Effect(kind="fire", binding_id="b1", action=Action(type="none"), message="Fired X"),
            Effect(kind="log", message="Fired X")]


@pytest.mark.parametrize("mode,fires,suppress", [("keyware", True, True), ("app", False, False), ("both", True, False)])
def test_dispatch_decision(mode, fires, suppress):
    effects, sup, passed = apply_app_rules(_effects(), True, lambda _id: mode)
    assert any(e.kind == "fire" for e in effects) is fires
    assert sup is suppress
    assert passed is (mode != "keyware")
    if mode == "app":
        assert effects == []


def test_ai_reply_with_app_rules():
    reply = json.dumps({"ok": True, "label": "Ctrl+Shift+S normal in Cursor", "profile": {"scope": "global"},
                        "trigger": {"mode": "chord", "keys": ["ctrl", "shift", "s"]}, "action": {"type": "none"},
                        "app_rules": {"Cursor.exe": "app"}})
    assert parse_reply(reply).binding.app_rules == {"cursor.exe": "app"}
    bad = reply.replace('"app"}', '"maybe"}')
    with pytest.raises(Exception):
        parse_reply(bad)


def _rules_only(keys=("ctrl", "shift", "s")):
    return Binding(id="new", enabled=True, mode="chord", keys=list(keys),
                   action=Action(type="none"), app_rules={"cursor.exe": "app"})


def test_rules_only_proposal_merges_into_existing():
    from assist.ai_binder import Proposal, merge_rules_proposal
    old = _binding(app_rules={"code.exe": "both", "cursor.exe": "keyware"})
    merged = merge_rules_proposal(_rules_only(), [old])
    assert merged.id == "b1"
    assert merged.app_rules == {"code.exe": "both", "cursor.exe": "app"}
    p = Proposal(binding=merged, label="", merged_into=merged.id)
    assert ("Change", "Adds rule to existing Ctrl+Shift+S") in p.summary_lines() or any(
        "Adds rule to existing" in v for _, v in p.summary_lines())


def test_merge_keeps_existing_action():
    from assist.ai_binder import merge_rules_proposal
    merged = merge_rules_proposal(_rules_only(), [_binding()])
    assert merged.action.type == "open_url" and merged.action.url == "https://example.com"


def test_no_matching_binding_creates_as_before():
    from assist.ai_binder import merge_rules_proposal
    assert merge_rules_proposal(_rules_only(("ctrl", "shift", "x")), [_binding()]) is None
    real = Binding(id="n", enabled=True, mode="chord", keys=["ctrl", "shift", "s"],
                   action=Action(type="open_url", url="https://x.com"), app_rules={"cursor.exe": "app"})
    assert merge_rules_proposal(real, [_binding()]) is None


def _svc(tmp_path):
    from tests.fakes import service

    created, opener, injector, audio, _hook = service(tmp_path)
    created.set_enabled(True)
    created.set_foreground("cursor.exe")
    return created, opener, injector, audio


def test_app_rule_chord_passes_the_key_down_and_up(tmp_path):
    """The app must see the whole chord. Swallowing only the key-up leaves the key stuck."""
    created, opener, _injector, _audio = _svc(tmp_path)
    created.add_binding("global", _binding(app_rules={"cursor.exe": "app"}))
    assert created.handle_input("ctrl", True, 0.0) is False
    assert created.handle_input("shift", True, 0.0) is False
    assert created.handle_input("s", True, 0.0) is False
    assert created.handle_input("s", True, 0.05) is False  # auto-repeat
    assert created.handle_input("s", False, 0.1) is False
    assert opener.urls == []
    created.handle_input("shift", False, 0.1)
    created.handle_input("ctrl", False, 0.1)

    created.set_foreground("notepad.exe")
    assert created.handle_input("ctrl", True, 1.0) is False
    assert created.handle_input("shift", True, 1.0) is False
    assert created.handle_input("s", True, 1.0) is True
    assert created.handle_input("s", False, 1.1) is True
    assert opener.urls == ["https://example.com"]


def test_both_rule_runs_the_action_and_passes_the_chord(tmp_path):
    created, opener, _injector, _audio = _svc(tmp_path)
    created.add_binding("global", _binding(app_rules={"cursor.exe": "both"}))
    assert created.handle_input("ctrl", True, 0.0) is False
    assert created.handle_input("shift", True, 0.0) is False
    assert created.handle_input("s", True, 0.0) is False
    assert created.handle_input("s", False, 0.1) is False
    assert opener.urls == ["https://example.com"]


def test_app_rule_hold_passes_the_key_and_does_not_type_it_twice(tmp_path):
    created, opener, injector, audio = _svc(tmp_path)
    created.add_binding("global", Binding(
        id="h", enabled=True, mode="hold", keys=["f8"], hold_ms=300,
        action=Action(type="open_url", url="https://example.com/hold"),
        app_rules={"cursor.exe": "app"},
    ))
    assert created.handle_input("f8", True, 0.0) is False
    assert created.handle_input("f8", False, 0.1) is False
    assert injector.events == []
    assert created.handle_input("f8", True, 1.0) is False
    created.tick(1.3)
    assert created.handle_input("f8", False, 1.4) is False
    assert opener.urls == []
    assert audio.played == []

    created.set_foreground("notepad.exe")
    assert created.handle_input("f8", True, 2.0) is True
    assert created.handle_input("f8", False, 2.1) is True
    assert ("press", "f8") in injector.events
    created.handle_input("f8", True, 3.0)
    created.tick(3.3)
    assert opener.urls == ["https://example.com/hold"]


def test_both_rule_hold_runs_during_the_hold(tmp_path):
    created, opener, injector, _audio = _svc(tmp_path)
    created.add_binding("global", Binding(
        id="h", enabled=True, mode="hold", keys=["f8"], hold_ms=300,
        action=Action(type="open_url", url="https://example.com/hold"),
        app_rules={"cursor.exe": "both"},
    ))
    assert created.handle_input("f8", True, 0.0) is False
    created.tick(0.3)
    assert opener.urls == ["https://example.com/hold"]
    assert created.handle_input("f8", False, 0.4) is False
    assert injector.events == []


def test_app_rule_drops_the_hold_beep_and_both_keeps_it():
    beep = [Effect(kind="fire", binding_id="b1", action=Action(type="none"), message="Fired X"),
            Effect(kind="log", message="Fired X"), Effect(kind="audio", hz=880)]
    dropped, sup, passed = apply_app_rules(beep, True, lambda _id: "app")
    assert dropped == [] and sup is False and passed is True
    kept, _sup, passed = apply_app_rules(beep, True, lambda _id: "both")
    assert [e.kind for e in kept] == ["fire", "log", "audio"] and passed is True


def test_tap_app_rule_passes_the_key_without_firing(tmp_path):
    created, opener, injector, _audio = _svc(tmp_path)
    created.add_binding("global", Binding(
        id="t", enabled=True, mode="tap", keys=["f9"],
        action=Action(type="open_url", url="https://example.com/tap"),
        app_rules={"cursor.exe": "app"},
    ))
    assert created.handle_input("f9", True, 0.0) is False
    assert created.handle_input("f9", False, 0.05) is False
    assert opener.urls == []
    assert injector.events == []

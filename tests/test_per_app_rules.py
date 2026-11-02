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

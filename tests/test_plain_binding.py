"""Plain Text-mode sentences stay global, and the broken calculator action opens calc.exe."""

import json

from assist.ai_binder import apply_plain_scope, parse_reply
from engine.actions import ActionExecutor
from engine.models import Action, Binding, Config
from tests.fakes import RecordingInjector, RecordingOpener


def test_open_calculator_loads_as_calc_and_runs():
    action = Action.from_dict({"type": "open_calculator"})
    assert action.type == "open_app" and action.app == "calc.exe"
    saved = Binding.from_dict({
        "id": "bind-1", "enabled": True, "mode": "tap", "keys": ["f7"],
        "action": {"type": "open_calculator"},
    })
    assert saved.action.type == "open_app" and saved.action.app == "calc.exe"
    other = Binding.from_dict({
        "id": "bind-2", "enabled": True, "mode": "tap", "keys": ["f2"],
        "label": "Keep me", "action": {"type": "open_url", "url": "https://example.com"},
    })
    assert other.label == "Keep me" and other.action.type == "open_url"
    opener = RecordingOpener()
    ActionExecutor(opener, RecordingInjector(), lambda _s: None, __import__("random").Random(1)).run(
        Action(type="open_calculator")
    )
    assert opener.apps == ["calc.exe"]


def test_calculator_sentence_is_not_an_app_profile():
    raw = {
        "ok": True, "label": "Calculator",
        "profile": {"scope": "app", "name": "Calculator", "process_names": ["calculator.exe"]},
        "trigger": {"mode": "hold", "keys": ["f7"], "hold_ms": 2440},
        "action": {"type": "open_calculator"},
        "app_rules": {"calculator.exe": "keyware"},
    }
    proposal = parse_reply(json.dumps(raw))
    assert proposal.binding.action.type == "open_app"
    assert proposal.binding.action.app == "calc.exe"
    apply_plain_scope("F7 opens Calculator", proposal)
    assert proposal.profile_name == ""
    assert proposal.process_names == []
    assert proposal.binding.app_rules == {}
    assert dict(proposal.summary_lines())["Where"] == "Every window"


def test_named_app_and_exception_are_kept():
    chrome = parse_reply(json.dumps({
        "ok": True, "label": "Notion",
        "profile": {"scope": "app", "name": "Chrome", "process_names": ["chrome.exe"]},
        "trigger": {"mode": "chord", "keys": ["ctrl", "k"]},
        "action": {"type": "open_url", "url": "https://www.notion.so"},
    }))
    apply_plain_scope("Ctrl+K opens Notion in Chrome", chrome)
    assert chrome.profile_name == "Chrome"
    assert chrome.process_names == ["chrome.exe"]
    assert "Only while Chrome is in front" in dict(chrome.summary_lines())["Where"]

    rules = parse_reply(json.dumps({
        "ok": True, "label": "Save",
        "profile": {"scope": "global"},
        "trigger": {"mode": "chord", "keys": ["ctrl", "shift", "s"]},
        "action": {"type": "key_combo", "keys": ["ctrl", "k", "s"]},
        "app_rules": {"cursor.exe": "app"},
    }))
    apply_plain_scope("Ctrl+Shift+S saves, but let it work normally in Cursor", rules)
    assert rules.binding.app_rules == {"cursor.exe": "app"}
    assert rules.profile_name == ""


def test_other_profiles_in_a_config_are_not_rewritten():
    config = Config.from_dict({
        "profiles": [
            {"id": "global", "name": "Global", "process_names": [], "bindings": [
                {"id": "b1", "mode": "tap", "keys": ["f7"], "action": {"type": "open_calculator"}},
            ]},
            {"id": "code", "name": "Code", "process_names": ["code.exe"], "bindings": [
                {"id": "b2", "mode": "tap", "keys": ["f9"], "label": "Mine",
                 "action": {"type": "type_text", "text": "hello"}},
            ]},
        ]
    })
    code = config.profile_by_id("code")
    assert code.name == "Code" and code.bindings[0].label == "Mine"
    assert code.bindings[0].action.type == "type_text"
    assert config.global_profile().bindings[0].action.app == "calc.exe"

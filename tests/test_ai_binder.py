"""Describe-it binder with the model mocked. No network, keys, apps, or keystrokes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assist.ai_binder import (
    DEFAULT_PROVIDERS,
    SYSTEM_PROMPT,
    AIBinderError,
    DescribeBinder,
    Provider,
    _HTTPFailure,
    load_keys,
    parse_reply,
)
from engine.models import ACTION_TYPES, default_config
from tests.fakes import Clock

FAKE_KEYS = {"GEMINI_API_KEY": "AIzaFAKEFAKEFAKEFAKE123", "GROQ_API_KEY": "gsk_FAKEFAKEFAKEFAKE123"}


def reply(obj) -> dict:
    content = obj if isinstance(obj, str) else json.dumps(obj)
    return {"choices": [{"message": {"content": content}}]}


def good(**override):
    data = {
        "ok": True,
        "label": "YouTube: minecraft",
        "profile": {"scope": "global"},
        "trigger": {"mode": "tap", "keys": ["f2"]},
        "action": {"type": "open_url", "url": "https://www.youtube.com/results?search_query=minecraft"},
        "notes": "",
    }
    data.update(override)
    return data


class Transport:
    """Scripted answers per call. Each item is a dict reply or an exception."""

    def __init__(self, *answers, clock: Clock | None = None, cost_ms: float = 0):
        self.answers = list(answers)
        self.calls: list[dict] = []
        self.clock = clock
        self.cost_ms = cost_ms

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        if self.clock is not None:
            self.clock.advance(self.cost_ms)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def binder(transport, **kwargs) -> DescribeBinder:
    return DescribeBinder(transport=transport, keys=dict(FAKE_KEYS), **kwargs)


# --- parsing every trigger and action ------------------------------------------


def test_tap_open_url_fills_a_valid_binding():
    proposal = parse_reply(json.dumps(good()), default_config())
    b = proposal.binding
    assert (b.mode, b.keys, b.action.type) == ("tap", ["f2"], "open_url")
    assert b.action.url.endswith("search_query=minecraft")
    assert proposal.profile_name == ""
    assert ("Trigger", "Tap F2") in proposal.summary_lines()


def test_chord_macro_with_text_and_enter():
    data = good(
        trigger={"mode": "chord", "keys": ["Control", "Q"]},
        action={"type": "macro", "steps": [{"op": "text", "text": "you@example.com"}, {"op": "key", "key": "Return"}]},
        notes="Replace you@example.com with your real email.",
    )
    proposal = parse_reply(json.dumps(data))
    b = proposal.binding
    assert b.mode == "chord" and b.keys == ["ctrl", "q"]
    assert [s.op for s in b.action.steps] == ["text", "key"] and b.action.steps[1].key == "enter"
    assert proposal.notes == ["Replace you@example.com with your real email."]
    steps = dict(proposal.summary_lines())["Steps"]
    assert "Enter" in steps and "you@example.com" in steps


def test_hold_is_clamped_and_app_profile_is_kept():
    data = good(
        profile={"scope": "app", "name": "Gaming", "process_names": ["javaw.exe", "bad name", "x.bat"]},
        trigger={"mode": "hold", "keys": ["r"], "hold_ms": 99999},
        action={"type": "macro", "steps": [{"op": "key", "key": "3"}, {"op": "delay", "ms": 50}, {"op": "mouse", "button": "right"}]},
    )
    proposal = parse_reply(json.dumps(data))
    assert proposal.binding.mode == "hold" and proposal.binding.hold_ms == 5000
    assert proposal.profile_name == "Gaming" and proposal.process_names == ["javaw.exe"]


@pytest.mark.parametrize("mode,taps", [("double_tap", 2), ("triple_tap", 3), ("Double-Tap", 2)])
def test_tap_dance_modes(mode, taps):
    data = good(trigger={"mode": mode, "keys": ["f8"]}, action={"type": "key_combo", "keys": ["ctrl", "shift", "t"]})
    b = parse_reply(json.dumps(data)).binding
    assert b.mode == "tap_dance" and b.taps == taps and b.action.keys == ["ctrl", "shift", "t"]


def test_leader_without_a_leader_key_adds_a_note():
    data = good(trigger={"mode": "leader", "keys": ["g"]})
    proposal = parse_reply(json.dumps(data), default_config())
    assert proposal.binding.mode == "leader"
    assert any("leader key" in note for note in proposal.notes)


def test_open_app_and_type_text():
    app = parse_reply(json.dumps(good(action={"type": "open_app", "app": "notepad.exe"}))).binding
    assert app.action.app == "notepad.exe"
    text = parse_reply(json.dumps(good(action={"type": "type_text", "text": "console.log();"}))).binding
    assert text.action.text == "console.log();"


def test_markdown_fences_are_tolerated():
    proposal = parse_reply("```json\n" + json.dumps(good()) + "\n```")
    assert proposal.binding.keys == ["f2"]


# --- rejecting unsafe or unknown output ----------------------------------------


@pytest.mark.parametrize(
    "action,needle",
    [
        ({"type": "format_disk", "cmd": "dir"}, "unknown action"),
        ({"type": "run_command", "command": "shutdown /s /t 0"}, "will not run"),
        ({"type": "run_command", "command": "del /q C:\\temp\\*"}, "will not run"),
        ({"type": "open_url", "url": "file:///C:/Windows"}, "http"),
        ({"type": "open_url", "url": "javascript:alert(1)"}, "http"),
        ({"type": "open_app", "app": "cmd.exe"}, "will not launch"),
        ({"type": "open_app", "app": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"}, "will not launch"),
        ({"type": "open_app", "app": "C:\\tools\\clean.ps1"}, "will not launch"),
        ({"type": "open_app", "app": "notepad.exe /p secret.txt"}, "arguments"),
        ({"type": "type_text", "text": "powershell -c Remove-Item C:\\temp"}, "system command"),
        ({"type": "type_text", "text": "rm -rf /"}, "system command"),
        ({"type": "key_combo", "keys": ["ctrl", "alt", "delete"]}, "not allowed"),
        ({"type": "macro", "steps": [{"op": "shell", "text": "x"}]}, "unknown macro step"),
        ({"type": "macro", "steps": [{"op": "combo", "keys": ["win", "r"]}, {"op": "text", "text": "notepad"}]}, "Run box"),
    ],
)
def test_unsafe_or_unknown_actions_are_rejected(action, needle):
    with pytest.raises(AIBinderError) as caught:
        parse_reply(json.dumps(good(action=action)))
    assert needle.lower() in str(caught.value).lower()
    assert caught.value.final


@pytest.mark.parametrize(
    "override,needle",
    [
        ({"trigger": {"mode": "chord", "keys": ["ctrl", "alt", "shift", "esc"]}}, "panic"),
        ({"trigger": {"mode": "tap", "keys": ["mouse4"]}}, "unknown key"),
        ({"trigger": {"mode": "swipe", "keys": ["f2"]}}, "unknown trigger mode"),
        ({"trigger": {"mode": "tap", "keys": ["ctrl"]}}, "non-modifier"),
        ({"run": "now"}, "unknown field"),
    ],
)
def test_bad_triggers_and_fields_are_rejected(override, needle):
    with pytest.raises(AIBinderError) as caught:
        parse_reply(json.dumps(good(**override)))
    assert needle.lower() in str(caught.value).lower()


def test_model_refusal_is_shown_as_is():
    with pytest.raises(AIBinderError) as caught:
        parse_reply(json.dumps({"ok": False, "error": "Keyware will not run shell commands."}))
    assert str(caught.value) == "Keyware will not run shell commands." and caught.value.final


def test_invalid_json_is_rejected():
    with pytest.raises(AIBinderError, match="not valid JSON"):
        parse_reply("sure! F2 opens youtube")


# --- the agent: request shape, fallbacks, timeouts, keys ---------------------------


def test_request_uses_one_system_prompt_json_mode_and_context():
    transport = Transport(reply(good()))
    proposal = binder(transport).describe("F2 opens YouTube and searches minecraft", default_config(), "Global")
    call = transport.calls[0]
    assert call["url"].startswith("https://generativelanguage.googleapis.com/")
    body = call["body"]
    assert body["model"] == DEFAULT_PROVIDERS[0].model == "gemini-3.5-flash-lite"
    assert body["response_format"] == {"type": "json_object"} and body["temperature"] == 0
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user"]  # Gemini keeps only the last system message
    assert body["messages"][0]["content"] == SYSTEM_PROMPT
    assert "Request: F2 opens YouTube" in body["messages"][1]["content"]
    assert "leader key is not set" in body["messages"][1]["content"]
    assert call["headers"]["Authorization"] == "Bearer " + FAKE_KEYS["GEMINI_API_KEY"]
    assert call["headers"]["User-Agent"].startswith("Keyware/")
    assert call["timeout"] <= 6.0
    assert proposal.model == "gemini-3.5-flash-lite"


def test_system_prompt_teaches_the_whole_schema():
    for word in ACTION_TYPES + ("chord", "hold", "double_tap", "triple_tap", "leader", "delay", "mouse", "combo",
                                "process_names", "scope", "conflict", "ok\": false"):
        assert word.lower() in SYSTEM_PROMPT.lower(), word
    assert SYSTEM_PROMPT.count("User:") >= 8  # worked examples


def test_falls_back_on_timeout_rate_limit_and_bad_json():
    transport = Transport(TimeoutError("timed out"), _HTTPFailure(429, True), reply("not json"), reply(good()))
    proposal = binder(transport, total_budget=60).describe("F2 opens YouTube")
    assert [c["body"]["model"] for c in transport.calls] == [p.model for p in DEFAULT_PROVIDERS]
    assert transport.calls[2]["url"].startswith("https://api.groq.com/")
    assert transport.calls[2]["headers"]["Authorization"] == "Bearer " + FAKE_KEYS["GROQ_API_KEY"]
    assert proposal.model == "openai/gpt-oss-20b"


def test_a_refusal_is_not_retried_on_another_model():
    transport = Transport(reply({"ok": False, "error": "Keyware will not run shell commands."}), reply(good()))
    with pytest.raises(AIBinderError, match="will not run shell"):
        binder(transport).describe("F9 opens powershell and deletes temp")
    assert len(transport.calls) == 1


def test_total_budget_stops_after_about_six_seconds_per_try():
    clock = Clock()
    transport = Transport(*[TimeoutError("timed out")] * 4, clock=clock, cost_ms=6000)
    with pytest.raises(AIBinderError, match="Timed out"):
        binder(transport, clock=clock, total_budget=14).describe("F2 opens YouTube")
    assert len(transport.calls) == 3
    assert transport.calls[-1]["timeout"] == pytest.approx(2.0)


def test_only_providers_with_keys_are_tried_and_missing_keys_are_reported():
    transport = Transport(reply(good()))
    proposal = DescribeBinder(transport=transport, keys={"GROQ_API_KEY": "gsk_x"}).describe("F2 opens YouTube")
    assert proposal.model == "openai/gpt-oss-120b"
    with pytest.raises(AIBinderError, match="No GEMINI_API_KEY or GROQ_API_KEY"):
        DescribeBinder(transport=Transport(), keys={}).describe("F2 opens YouTube")


def test_errors_never_contain_a_key():
    leak = OSError("connection reset for " + FAKE_KEYS["GEMINI_API_KEY"])
    transport = Transport(leak, leak, OSError(FAKE_KEYS["GROQ_API_KEY"]), OSError("x"))
    with pytest.raises(AIBinderError) as caught:
        binder(transport, total_budget=60).describe("F2 opens YouTube")
    text = str(caught.value)
    assert "AIza" not in text and "gsk_" not in text
    assert text == "Could not reach the AI service."


def test_empty_and_overlong_descriptions_do_not_call_the_model():
    transport = Transport()
    with pytest.raises(AIBinderError, match="Describe"):
        binder(transport).describe("   ")
    with pytest.raises(AIBinderError, match="under"):
        binder(transport).describe("x" * 700)
    assert transport.calls == []


def test_keys_only_from_env_behind_the_dev_flag(tmp_path):
    # v1.3.4: the shipped app never reads a .env file or env keys; a developer opts in with KEYWARE_DEV_KEYS=1.
    assert load_keys(env={"GEMINI_API_KEY": "from-env"}) == {}
    keys = load_keys(env={"KEYWARE_DEV_KEYS": "1", "GEMINI_API_KEY": "from-env", "OTHER": "1"})
    assert keys == {"GEMINI_API_KEY": "from-env"}


def test_hook_engine_never_imports_the_network_helper():
    for path in Path("engine").glob("*.py"):
        assert "assist" not in path.read_text(encoding="utf-8"), path


def test_free_providers_only():
    assert [p.model for p in DEFAULT_PROVIDERS] == [
        "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "openai/gpt-oss-120b", "openai/gpt-oss-20b",
    ]
    assert all(isinstance(p, Provider) for p in DEFAULT_PROVIDERS)

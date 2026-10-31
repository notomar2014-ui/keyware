"""Hook fail-open behavior and repository safety checks."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from engine.hook_engine import HookEngine, HookUnavailable
from engine.keys import format_keys, parse_key_expression, vk_to_name
from engine.profiles import match_profile, normalize_process
from engine.models import default_config


class _Suppress(Exception):
    pass


class _Listener:
    def __init__(self):
        self.suppressed = 0
        self.running = True
        self._thread = None

    def suppress_event(self):
        self.suppressed += 1
        raise _Suppress()

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def wait(self):
        return None

    def is_running(self):
        return self.running


def test_hook_does_not_start_off_windows(monkeypatch):
    """The host may be Windows. This test still forces the off-Windows path."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("engine.hook_engine.is_windows", lambda: False)
    created = []

    def factory(_event_filter):
        created.append(True)
        raise AssertionError("listener factory must not run off Windows")

    engine = HookEngine(lambda *_args: False, listener_factory=factory)
    with pytest.raises(HookUnavailable):
        engine.start()
    assert created == []


def test_filter_suppresses_only_the_keys_the_engine_marks():
    seen = []

    def on_event(name, pressed):
        seen.append((name, pressed))
        return name == "r" and pressed

    engine = HookEngine(on_event)
    listener = _Listener()
    engine._listener = listener

    class Data:
        def __init__(self, vk, flags=0):
            self.vkCode = vk
            self.flags = flags

    with pytest.raises(_Suppress):
        engine._filter(0x0100, Data(0x52))
    assert seen == [("r", True)]
    assert listener.suppressed == 1

    assert engine._filter(0x0100, Data(0x41)) is False
    assert listener.suppressed == 1
    assert engine._filter(0x0101, Data(0x52)) is False


def test_injected_and_unknown_keys_and_callback_errors_fail_open():
    def on_event(_name, _pressed):
        raise RuntimeError("detector failed")

    engine = HookEngine(on_event)
    listener = _Listener()
    engine._listener = listener

    class Data:
        def __init__(self, vk, flags=0):
            self.vkCode = vk
            self.flags = flags

    assert engine._filter(0x0100, Data(0x41, flags=0x10)) is False
    assert listener.suppressed == 0
    assert engine._filter(0x0100, Data(0x41)) is False
    assert listener.suppressed == 0
    assert engine._filter(0x0100, Data(0xFF)) is False
    assert engine._filter(0x0200, Data(0x41)) is False


def test_listener_is_created_with_selective_suppression_only():
    source = Path("engine/hook_engine.py").read_text(encoding="utf-8")
    assert "suppress=False" in source
    assert "suppress=True" not in source


def test_engine_sources_do_not_open_a_shell_or_the_network():
    root = Path("engine")
    banned = ("shell=True", "urllib.request", "http.client", "requests", "socket.socket", "webbrowser")
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{path} contains {token}"


def test_virtual_key_and_expression_roundtrip():
    assert vk_to_name(0x52) == "r"
    assert vk_to_name(0x11) == "ctrl"
    assert vk_to_name(0x5B) == "win"
    assert vk_to_name(0x1B) == "esc"
    assert parse_key_expression("Ctrl + Win + Shift + R") == ["ctrl", "win", "shift", "r"]
    assert format_keys(["shift", "r", "ctrl", "win"]) == "Ctrl + Win + Shift + R"
    assert parse_key_expression("F13") == ["f13"]
    assert parse_key_expression("numpad 1") == ["numpad1"]


def test_process_matching_is_case_insensitive():
    config = default_config()
    assert match_profile(config.profiles, r"C:\Games\JAVAW.EXE").id == "gaming"
    assert match_profile(config.profiles, "chrome.exe").id == "browser"
    assert match_profile(config.profiles, "CODE.EXE").id == "code"
    assert match_profile(config.profiles, "notepad.exe").id == "global"
    assert match_profile(config.profiles, "").id == "global"
    assert normalize_process("javaw") == "javaw"

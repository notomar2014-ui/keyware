"""Follow-ups for 1.4.2: cached matching, saved frame-host rules, and shell names."""

from __future__ import annotations

import json
import os
import time

import pytest

from assist.ai_binder import PROMPT_VERSION, SYSTEM_PROMPT, AIBinderError, parse_reply
from engine.appidentity import (
    _identity_keys_cached,
    app_names_match,
    default_search_dirs,
    is_execution_alias,
    is_shell_name,
)
from engine.applaunch import _known_for_stem
from engine.context_tracker import (
    clear_foreground_cache,
    foreground_was_frame_host,
    resolve_foreground,
)
from engine.models import Action, Binding
from engine.profiles import app_rule_mode, match_profile, same_process
from engine.script import compile_script, execute_script
from tests.fakes import RecordingInjector, RecordingOpener, service
from tests.test_ai_binder import good
from tests.test_v141 import _Rng, _ctx


CORE = "Windows.UI.Core.CoreWindow"


def test_name_matching_is_cached_and_stays_cheap():
    _identity_keys_cached.cache_clear()
    _known_for_stem.cache_clear()
    assert app_names_match("calc.exe", "CalculatorApp.exe")
    assert _identity_keys_cached.cache_info().misses >= 1
    identity_hits = _identity_keys_cached.cache_info().hits
    known_hits = _known_for_stem.cache_info().hits
    # A miss on both alias groups still has to ask the known-app index.
    assert app_names_match("notepad.exe", "chrome.exe") is False
    rules = {f"tool{i}.exe": "app" for i in range(49)}
    rules["calc.exe"] = "both"
    started = time.perf_counter()
    for _ in range(30):
        assert app_rule_mode(rules, "CalculatorApp.exe") == "both"
        assert app_rule_mode(rules, "notepad.exe") == "keyware"
    elapsed = time.perf_counter() - started
    assert _identity_keys_cached.cache_info().hits > identity_hits
    assert _known_for_stem.cache_info().hits > known_hits
    # Rebuilding every alias set was about 8 ms per 50-rule pass (~0.5 s here).
    assert elapsed < 0.25, elapsed


def test_frame_host_children_are_read_once_per_window():
    clear_foreground_cache()
    calls = {"n": 0}

    def children(_hwnd):
        calls["n"] += 1
        return [(CORE, r"C:\pkg\CalculatorApp.exe")]

    assert resolve_foreground(10, "ApplicationFrameHost.exe", children) == "CalculatorApp.exe"
    assert foreground_was_frame_host() is True
    assert resolve_foreground(10, "ApplicationFrameHost.exe", children) == "CalculatorApp.exe"
    assert calls["n"] == 1
    assert resolve_foreground(11, "notepad.exe", children) == "notepad.exe"
    assert foreground_was_frame_host() is False
    assert calls["n"] == 1
    clear_foreground_cache()
    assert foreground_was_frame_host() is False


def test_saved_frame_host_rule_matches_only_the_hosted_app():
    assert same_process("ApplicationFrameHost.exe", "CalculatorApp.exe", hosted_in_frame=True)
    assert same_process("ApplicationFrameHost.exe", "CalculatorApp.exe", hosted_in_frame=False) is False
    assert same_process("calc.exe", "ApplicationFrameHost.exe", hosted_in_frame=True) is False
    assert same_process("ApplicationFrameHost.exe", "notepad.exe", hosted_in_frame=False) is False
    assert app_rule_mode({"ApplicationFrameHost.exe": "app"}, "CalculatorApp.exe", hosted_in_frame=True) == "app"
    assert app_rule_mode({"ApplicationFrameHost.exe": "app"}, "CalculatorApp.exe") == "keyware"
    assert app_rule_mode({"calc.exe": "app"}, "ApplicationFrameHost.exe", hosted_in_frame=True) == "keyware"
    assert app_names_match("calc.exe", "CalculatorApp.exe")


def test_saved_frame_host_profile_and_rule_follow_the_hosted_window(tmp_path):
    created, opener, *_rest = service(tmp_path)
    created.add_profile("Store apps", ["ApplicationFrameHost.exe"])
    created.set_foreground("CalculatorApp.exe", hosted_in_frame=True)
    assert created.snapshot()["profile_name"] == "Store apps"
    created.set_foreground("CalculatorApp.exe", hosted_in_frame=False)
    assert created.snapshot()["profile_name"] == "Global"
    created.set_foreground("notepad.exe", hosted_in_frame=True)
    assert created.snapshot()["profile_name"] == "Store apps"
    created.set_foreground("notepad.exe")
    assert created.snapshot()["profile_name"] == "Global"

    created.add_profile("Calculator", ["calc.exe"])
    created.set_foreground("CalculatorApp.exe")
    assert created.snapshot()["profile_name"] == "Calculator"

    created.set_enabled(True)
    created.add_binding("global", Binding(
        id="open", enabled=True, mode="tap", keys=["f8"],
        action=Action(type="open_url", url="https://example.com/hosted"),
        app_rules={"ApplicationFrameHost.exe": "app"},
    ))

    class Fresh:
        def __init__(self):
            self.hosted_in_frame = True

        def read(self):
            return "CalculatorApp.exe"

    created.context = Fresh()
    created.handle_input("f8", True, 0.0)
    created.handle_input("f8", False, 0.05)
    assert opener.urls == []
    created.context.hosted_in_frame = False
    created.handle_input("f8", True, 1.0)
    created.handle_input("f8", False, 1.05)
    assert opener.urls == ["https://example.com/hosted"]


@pytest.mark.parametrize("line", [
    "app wt.exe",
    "app wt",
    "app terminal",
    "app WindowsTerminal",
    "app WindowsTerminal.exe",
    'app "Windows Terminal"',
    "app wsl",
    "app ubuntu",
    "app debian",
    "app kali",
    "app winget",
    "app pwsh",
    "app pwsh.exe",
    'app "PowerShell 7"',
])
def test_script_refuses_shell_aliases(line):
    assert is_shell_name(line.split(" ", 1)[1])
    with pytest.raises(ValueError, match="shell"):
        compile_script(line)


def test_script_still_opens_calculator_and_matches_terminal():
    compile_script("app calc.exe")
    compile_script("app notepad.exe")
    from engine.actions import ActionExecutor

    opener, injector = RecordingOpener(), RecordingInjector()
    executor = ActionExecutor(opener, injector, lambda _s: None, _Rng())
    execute_script(
        'if app is wt.exe\n  type "yes"\nelse\n  type "no"\nend',
        executor=executor, action=Action(type="none"),
        ctx=_ctx(app="WindowsTerminal.exe"), variables={},
    )
    assert ("text", "yes") in injector.events


@pytest.mark.parametrize("app", [
    "wt.exe", "terminal", "WindowsTerminal.exe", "Windows Terminal",
    "wsl", "ubuntu", "debian", "kali", "winget", "pwsh", "PowerShell 7",
])
def test_text_mode_refuses_shell_aliases(app):
    with pytest.raises(AIBinderError, match="will not launch"):
        parse_reply(json.dumps(good(action={"type": "open_app", "app": app})))
    with pytest.raises(AIBinderError, match="will not launch"):
        parse_reply(json.dumps(good(action={"type": "macro", "steps": [{"op": "app", "target": app}]})))


def test_text_mode_prompt_does_not_offer_windows_terminal():
    assert PROMPT_VERSION == "2026.11.26-2"
    assert "wt.exe (Windows Terminal)" not in SYSTEM_PROMPT
    assert "Shells are refused" in SYSTEM_PROMPT
    assert "Photos, Paint, Settings and Xbox" in SYSTEM_PROMPT
    opened = parse_reply(json.dumps(good(action={"type": "open_app", "app": "calc.exe"})))
    assert opened.binding.action.app == "calc.exe"


def test_execution_alias_is_the_user_windowsapps_folder_only(monkeypatch):
    assert is_execution_alias(r"C:\Users\A\AppData\Local\Microsoft\WindowsApps\calc.exe")
    assert is_execution_alias(r"C:/Users/A/AppData/Local/Microsoft/WindowsApps/calc.exe")
    assert is_execution_alias(r"C:\Program Files\WindowsApps\CalculatorApp.exe") is False
    monkeypatch.setenv("LOCALAPPDATA", r"D:\Redirected\Local")
    assert is_execution_alias(r"D:\Redirected\Local\Microsoft\WindowsApps\calc.exe")
    assert is_execution_alias(r"D:\Redirected\Local\Microsoft\WindowsApps") is False


def test_search_dirs_drop_relative_dot_entries():
    env = {
        "WINDIR": r"C:\Windows",
        "LOCALAPPDATA": r"C:\Users\A\AppData\Local",
        "USERPROFILE": r"C:\Users\A",
        # A drive letter cannot sit in PATH here: this suite also runs on Linux,
        # where os.pathsep is ":" and would split "C:\Tools" in two.
        "PATH": os.pathsep.join([".", "..", "./", "Tools"]),
    }
    dirs = [item.replace("/", "\\").rstrip("\\").lower() for item in default_search_dirs(env)]
    assert "." not in dirs
    assert ".." not in dirs
    assert "tools" in dirs
    assert dirs[0].endswith("system32")


def test_match_profile_keeps_global_when_the_frame_flag_is_absent():
    from engine.models import Profile

    profiles = [
        Profile(id="global", name="Global", process_names=[], enabled=True),
        Profile(id="frame", name="Frame", process_names=["ApplicationFrameHost.exe"], enabled=True),
    ]
    assert match_profile(profiles, "CalculatorApp.exe").name == "Global"
    assert match_profile(profiles, "CalculatorApp.exe", hosted_in_frame=True).name == "Frame"

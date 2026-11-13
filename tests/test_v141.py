"""Keyware 1.4.1: optional scripts, and the hook-path speedups around them."""

from __future__ import annotations

import json
import os
import time
from collections import Counter

import pytest

from assist.ai_binder import AIBinderError, parse_reply
from engine import __version__
from engine.actions import ActionExecutor
from engine.conflicts import ConflictError, find_conflicts
from engine.detector import InputDetector
from engine.models import Action, Binding, Config, default_config, validate_binding
from engine.script import (
    SCRIPT_MAX_REPEAT,
    SCRIPT_MAX_STEPS,
    SCRIPT_MAX_WAIT_TOTAL_MS,
    ScriptContext,
    compile_script,
    execute_script,
)
from tests.fakes import RecordingInjector, RecordingOpener, service


def test_version_and_package_version():
    from store.validate_msix import _version4

    assert __version__ == "1.4.1"
    assert _version4() == "1.4.1.0"


def test_script_is_omitted_until_a_binding_has_one():
    plain = Binding(id="b", enabled=True, mode="tap", keys=["f9"], action=Action(type="none"))
    assert "script" not in plain.to_dict()
    assert "scripting" not in default_config().to_dict()
    scripted = Binding(id="b", enabled=True, mode="tap", keys=["f9"], action=Action(type="none"), script="do\n")
    validate_binding(scripted, Config())
    assert scripted.script == "do"
    again = Binding.from_dict(json.loads(json.dumps(scripted.to_dict())))
    assert again.script == "do"
    turned = default_config()
    turned.scripting = True
    assert Config.from_dict(turned.to_dict()).scripting is True


@pytest.mark.parametrize("source", [
    "import os",
    "eval('1')",
    "run notepad",
    "app cmd.exe",
    "app C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
    "path C:\\tools\\wipe.bat",
    "url javascript:alert(1)",
    "repeat 9\n  do\nend",
    "if app is chrome.exe\n  do",
    "while true\n  do\nend",
    "wait 5000",
    "type " + ("x" * 501),
])
def test_unsafe_or_unbounded_scripts_are_rejected(source):
    if source.startswith("type "):
        source = 'type "' + ("x" * 501) + '"'
    with pytest.raises(ValueError):
        compile_script(source)


def test_a_normal_script_compiles_and_branches():
    source = """
    # only when Chrome is in front
    if app is chrome.exe
      type "hi"
    else
      do
    end
    if not mod ctrl
      click left
    end
    """
    program = compile_script(source)
    assert program[0][0] == "if"
    assert any(op[0] == "do" for op in program)
    opener, injector = RecordingOpener(), RecordingInjector()
    executor = ActionExecutor(opener, injector, lambda _s: None, _Rng())
    action = Action(type="open_url", url="https://example.com/manual")
    execute_script(source, executor=executor, action=action, ctx=_ctx(app="notepad.exe"), variables={})
    assert opener.urls == ["https://example.com/manual"]
    assert ("click", "left") in injector.events
    assert ("text", "hi") not in injector.events
    opener.urls.clear()
    injector.events.clear()
    execute_script(source, executor=executor, action=action, ctx=_ctx(app="chrome.exe", mods={"ctrl"}), variables={})
    assert ("text", "hi") in injector.events
    assert opener.urls == []
    assert ("click", "left") not in injector.events


def test_repeat_and_variables_stay_inside_the_process():
    source = "set n = n + 1\nif n == 2\n  do\nelse\n  stop\nend"
    opener, injector = RecordingOpener(), RecordingInjector()
    executor = ActionExecutor(opener, injector, lambda _s: None, _Rng())
    variables: dict[str, int] = {}
    action = Action(type="open_url", url="https://example.com/second")
    ctx = _ctx()
    execute_script(source, executor=executor, action=action, ctx=ctx, variables=variables)
    assert opener.urls == [] and variables["n"] == 1
    execute_script(source, executor=executor, action=action, ctx=ctx, variables=variables)
    assert opener.urls == ["https://example.com/second"] and variables["n"] == 2


def test_repeat_cannot_blow_the_step_budget():
    source = "\n".join(["repeat 8", "repeat 8", "repeat 8", "set n = n + 1", "end", "end", "end"])
    compile_script(source)  # nested repeats are legal; the budget stops them
    opener, injector = RecordingOpener(), RecordingInjector()
    executor = ActionExecutor(opener, injector, lambda _s: None, _Rng())
    variables: dict[str, int] = {}
    with pytest.raises(Exception, match=str(SCRIPT_MAX_STEPS)):
        execute_script(source, executor=executor, action=Action(type="none"), ctx=_ctx(), variables=variables)
    assert variables.get("n", 0) < 8 ** 3


def test_wait_budget_is_two_seconds_even_when_each_wait_is_legal():
    source = "repeat 8\n  wait 500\nend"
    assert SCRIPT_MAX_REPEAT == 8
    opener, injector = RecordingOpener(), RecordingInjector()
    executor = ActionExecutor(opener, injector, lambda _s: None, _Rng())
    with pytest.raises(Exception, match=str(SCRIPT_MAX_WAIT_TOTAL_MS)):
        execute_script(source, executor=executor, action=Action(type="none"), ctx=_ctx(), variables={})


def test_cancel_releases_a_modifier_the_script_pressed():
    opener, injector = RecordingOpener(), RecordingInjector()
    executor = ActionExecutor(opener, injector, lambda _s: executor.cancel(), _Rng())
    executor.jitter_min_ms = 0
    executor.jitter_max_ms = 0
    execute_script("combo ctrl a", executor=executor, action=Action(type="none"), ctx=_ctx(), variables={})
    pressed = [event[1] for event in injector.events if event[0] == "press"]
    released = [event[1] for event in injector.events if event[0] == "release"]
    assert pressed and Counter(pressed) == Counter(released)


def _arm(tmp_path, script: str, *, mode="tap", keys=None, action=None, rules=None, hold_ms=800):
    created, opener, injector, _audio, _hook = service(tmp_path)
    binding = Binding(
        id="scripted",
        enabled=True,
        mode=mode,
        keys=keys or ["f9"],
        hold_ms=hold_ms,
        action=action or Action(type="open_url", url="https://example.com/manual"),
        script=script,
        app_rules=rules or {},
        label="Scripted",
    )
    created.add_binding("global", binding)
    created.set_enabled(True)
    return created, opener, injector


def _tap(created, key="f9", at=0.0):
    created.handle_input(key, True, at)
    created.handle_input(key, False, at + 0.05)


def test_service_runs_the_script_instead_of_the_action(tmp_path):
    created, opener, injector = _arm(tmp_path, 'type "hi"')
    _tap(created)
    assert ("text", "hi") in injector.events
    assert opener.urls == []


def test_service_script_follows_the_foreground_app_and_profile(tmp_path):
    created, opener, injector = _arm(
        tmp_path,
        "if app is notepad.exe\n  type \"in app\"\nelse\n  do\nend\nif profile is gaming\n  click right\nend",
    )
    _tap(created)
    assert opener.urls == ["https://example.com/manual"]
    assert ("text", "in app") not in injector.events
    opener.urls.clear()
    created.set_foreground("notepad.exe")
    _tap(created, at=1.0)
    assert ("text", "in app") in injector.events
    assert opener.urls == []
    other = tmp_path / "profile-case"
    other.mkdir()
    created, opener, injector = _arm(other, "if profile is gaming\n  do\nend")
    created.set_foreground("javaw.exe")
    _tap(created)
    assert opener.urls == ["https://example.com/manual"]


def test_chord_script_sees_the_modifier_that_was_held(tmp_path):
    created, opener, _injector = _arm(
        tmp_path,
        "if mod ctrl\n  do\nend",
        mode="chord",
        keys=["ctrl", "f9"],
    )
    created.handle_input("ctrl", True, 0.0)
    created.handle_input("f9", True, 0.01)
    assert opener.urls == ["https://example.com/manual"]


def test_dry_run_logs_the_script_and_does_not_run_it(tmp_path):
    created, opener, injector = _arm(tmp_path, 'type "hi"')
    created.set_dry_run(True)
    _tap(created, at=1.0)
    assert opener.urls == []
    assert ("text", "hi") not in injector.events
    assert any("script" in message.text.lower() for message in created.poll())


def test_app_rule_still_skips_the_script_and_both_still_runs_it(tmp_path):
    created, opener, injector = _arm(tmp_path, "do", rules={"notepad.exe": "app"})
    created.set_foreground("notepad.exe")
    _tap(created)
    assert opener.urls == []
    other = tmp_path / "both-case"
    other.mkdir()
    created, opener, injector = _arm(other, 'type "both"', rules={"notepad.exe": "both"})
    created.set_foreground("notepad.exe")
    _tap(created)
    assert ("text", "both") in injector.events


def test_pause_stops_the_rest_of_the_script(tmp_path):
    created, _opener, injector = _arm(tmp_path, "key a\nwait 40\nkey b")

    def sleeper(seconds):
        if seconds >= 0.03:
            created.toggle_pause()

    created.executor.sleeper = sleeper
    _tap(created)
    assert ("press", "a") in injector.events
    assert ("press", "b") not in injector.events
    assert created.paused is True
    pressed = [event[1] for event in injector.events if event[0] == "press"]
    released = [event[1] for event in injector.events if event[0] == "release"]
    assert Counter(k for k in pressed if k == "a") == Counter(k for k in released if k == "a")


def test_hook_thread_queues_the_script_instead_of_running_it(tmp_path):
    created, opener, _injector = _arm(tmp_path, "do")
    seen = []

    def spy(effect, *, from_hook):
        seen.append((from_hook, list(opener.urls), effect.script))

    created._start_script = spy
    created.handle_input("f9", True, 0.0, from_hook=True)
    created.handle_input("f9", False, 0.05, from_hook=True)
    assert seen == [(True, [], "do")]
    assert opener.urls == []


def test_a_script_does_not_create_a_second_trigger(tmp_path):
    created, *_ = _arm(tmp_path, "do")
    other = Binding(id="other", enabled=True, mode="tap", keys=["f9"], action=Action(type="none"), script="stop")
    with pytest.raises(ConflictError):
        created.add_binding("global", other)
    assert find_conflicts(other, created.bindings("global"))


def test_a_bad_script_in_the_file_is_turned_off_not_run(tmp_path):
    created, *_ = service(tmp_path)
    raw = json.loads(created.store.path.read_text(encoding="utf-8"))
    raw["profiles"][0]["bindings"].append({
        "id": "bad-script",
        "enabled": True,
        "mode": "tap",
        "keys": ["f8"],
        "label": "Bad",
        "action": {"type": "open_url", "url": "https://example.com"},
        "script": {"not": "text"},
    })
    created.store.path.write_text(json.dumps(raw), encoding="utf-8")
    from engine.config_store import ConfigStore

    loaded, warnings = ConfigStore(created.store.path).load()
    bad = next(item for item in loaded.global_profile().bindings if item.id == "bad-script")
    assert bad.enabled is False
    assert any("Bad" in warning for warning in warnings)


def test_text_mode_cannot_smuggle_a_script_action():
    payload = {
        "ok": True,
        "label": "Script me",
        "profile": {"scope": "global"},
        "trigger": {"mode": "tap", "keys": ["f9"]},
        "action": {"type": "script", "text": "import os"},
    }
    with pytest.raises(AIBinderError, match="unknown action"):
        parse_reply(json.dumps(payload))


def test_config_is_not_statted_on_every_hold_tick(tmp_path):
    created, *_ = service(tmp_path)
    calls = {"n": 0}
    original = created.maybe_reload

    def wrapped():
        calls["n"] += 1
        return original()

    created.maybe_reload = wrapped
    created.tick(0.0)
    created.tick(0.02)
    created.tick(0.6)
    assert calls["n"] == 2


def test_hook_log_lines_are_not_written_on_the_caller_when_async(tmp_path):
    created, *_ = service(tmp_path)
    created.synchronous = False
    created._log("queued from the hook")
    log = tmp_path / "keyware.log"
    assert not log.exists() or "queued from the hook" not in log.read_text(encoding="utf-8")
    created._flush_logs()
    assert "queued from the hook" in log.read_text(encoding="utf-8")


def test_script_compile_and_plain_keys_stay_cheap():
    started = time.perf_counter()
    for index in range(200):
        compile_script(f"set n = {index}\nif n > 0\n  stop\nend")
    assert time.perf_counter() - started < 1.5
    opener, injector = RecordingOpener(), RecordingInjector()
    executor = ActionExecutor(opener, injector, lambda _s: None, _Rng())
    program_source = "set n = n + 1\nif n < 5\n  stop\nend"
    ctx = _ctx()
    variables: dict[str, int] = {}
    started = time.perf_counter()
    for _ in range(300):
        execute_script(program_source, executor=executor, action=Action(type="none"), ctx=ctx, variables=variables)
    assert time.perf_counter() - started < 1.5
    detector = InputDetector(default_config())
    detector.set_enabled(True)
    profile = default_config().global_profile()
    detector.set_profiles(profile, profile)
    started = time.perf_counter()
    for index in range(2000):
        detector.handle("z", True, index * 0.01)
        detector.handle("z", False, index * 0.01 + 0.001)
    assert time.perf_counter() - started < 1.5


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")
def test_script_box_stays_hidden_until_scripting_is_on(tmp_path):
    from ui.app import KeywareApp

    created, *_ = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    try:
        app.update()
        assert not app.script_box.winfo_ismapped()
        app._set_entry(app.trigger, "f9")
        app.mode.set("Tap")
        app._on_mode("Tap")
        app.action.set("Open URL")
        app._show_action_fields()
        app._set_entry(app.url, "https://example.com/plain")
        app._save_binding()
        saved = next(item for item in created.bindings("global") if item.keys == ["f9"])
        assert saved.script == "" and saved.action.url == "https://example.com/plain"

        app._open_settings()
        app.script_switch.select()
        app._on_scripting()
        app._close_settings()
        app.update()
        assert app.script_box.winfo_ismapped()
        app._set_entry(app.trigger, "f8")
        app.mode.set("Tap")
        app._on_mode("Tap")
        app.action.set("Open URL")
        app._show_action_fields()
        app._set_entry(app.url, "https://example.com/scripted")
        app.script_text.insert("1.0", "do")
        app._check_script()
        assert "Looks good" in app.script_note.cget("text")
        app._save_binding()
        scripted = next(item for item in created.bindings("global") if item.keys == ["f8"])
        assert scripted.script == "do"

        app.script_switch.deselect()
        app._on_scripting()
        app.update()
        assert not app.script_box.winfo_ismapped()
        app._edit_binding(scripted)
        app.update()
        assert app.script_box.winfo_ismapped()
        assert app._script_source() == "do"
        app._set_binder_mode("Text")
        app.update()
        assert not app.script_box.winfo_ismapped()
    finally:
        app.destroy()


class _Rng:
    def randint(self, low, high):
        return low


def _ctx(app="", profile="Global", setup=0, mods=()):
    return ScriptContext(app=app, profile=profile, setup=setup, mods=frozenset(mods))

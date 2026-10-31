"""Regressions for the Oct 31 2026 'it does not work at all' report."""

from __future__ import annotations

import json

from engine.sendinput import (
    KEYWARE_EXTRA,
    KEYWARE_TEST_EXTRA,
    TaggedInjector,
    build_keyboard_event,
    classify_hook_event,
)
from tests.fakes import service


def test_test_tagged_injection_is_treated_like_a_physical_key():
    event = classify_hook_event(0x0100, 0x41, 0x10, KEYWARE_TEST_EXTRA, already_down=False)
    assert event.kind == "key" and event.name == "a" and event.pressed
    assert KEYWARE_TEST_EXTRA != KEYWARE_EXTRA
    # Untagged injection from other programs (Pulse) still passes untouched.
    assert classify_hook_event(0x0100, 0x41, 0x10, 0, already_down=False).kind == "pass_injected"


def test_injected_keys_carry_a_hardware_scan_code():
    sent = []
    injector = TaggedInjector(send=sent.extend, scan_for=lambda vk: {0x50: 0x19}.get(vk, 0))
    injector.press("p")
    injector.release("p")
    assert [event.scan for event in sent] == [0x19, 0x19]
    assert all(event.extra == KEYWARE_EXTRA for event in sent)
    assert build_keyboard_event(0x50, scan=0x19).scan == 0x19


def test_saving_a_binding_while_off_turns_keyware_on(tmp_path):
    from engine.models import Action, Binding

    created, opener, _injector, _audio, hook = service(tmp_path)
    assert created.snapshot()["hook"] == "off"
    binding = Binding(id="", enabled=True, mode="chord", keys=["ctrl", "alt", "k"], label="K",
                      action=Action(type="open_url", url="https://example.com"))
    created.add_binding("global", binding)
    assert created.snapshot()["hook"] == "on"
    assert hook.starts == 1
    assert json.loads(created.store.path.read_text())["enabled"] is True
    for key in ("ctrl", "alt"):
        created.handle_input(key, True, 0.0)
    assert created.handle_input("k", True, 0.0) is True
    assert opener.urls == ["https://example.com"]


def test_enabling_a_binding_while_off_turns_keyware_on(tmp_path):
    created, _opener, _injector, _audio, hook = service(tmp_path)
    created.set_binding_enabled("global", "bind-youtube-hold-p", True)
    assert created.snapshot()["hook"] == "on"


def test_status_text_explains_off_state(tmp_path):
    created, *_ = service(tmp_path)
    assert "OFF" in created.status_summary()["headline"]
    created.set_enabled(True)
    summary = created.status_summary()
    assert summary["headline"].startswith("ON")
    assert "binding" in summary["detail"]


def test_log_lines_are_mirrored_to_a_file(tmp_path):
    created, *_ = service(tmp_path)
    created.set_enabled(True)
    log = tmp_path / "keyware.log"
    assert log.exists()
    assert "Active" in log.read_text(encoding="utf-8")


def test_logo_assets_and_tray_icons():
    from ui.tray import ASSETS, make_icon

    for name in ("keyware.svg", "keyware.ico", "keyware_256.png", "keyware_64.png", "keyware_32.png", "keyware_16.png"):
        assert (ASSETS / name).exists(), name
    on = make_icon(64)
    off = make_icon(64, off=True)
    paused = make_icon(64, paused=True)
    assert on.size == off.size == paused.size == (64, 64)
    assert on.tobytes() != off.tobytes() != paused.tobytes()
    assert make_icon(16).size == (16, 16)


def test_typing_key_as_leader_warns(tmp_path):
    created, *_ = service(tmp_path)
    created.set_leader_key("v")
    lines = [m.text for m in created.poll()]
    assert any("every V" in line for line in lines)
    created.set_leader_key("f13")
    assert not any("every" in m.text for m in created.poll())


def test_typing_actions_wait_until_chord_modifiers_are_released(tmp_path):
    from engine.models import Action

    created, *_ = service(tmp_path)
    created.physical.observe("ctrl", True, 0.0)
    calls = {"n": 0}

    def sleep(_s):
        calls["n"] += 1
        if calls["n"] == 3:
            created.physical.observe("ctrl", False, 0.1)

    text = Action(type="type_text", text="hi")
    assert created._wait_modifiers_up(text, sleep=sleep, timeout=1.0) is True
    assert calls["n"] == 3
    # URL actions never wait.
    created.physical.observe("ctrl", True, 0.2)
    assert created._wait_modifiers_up(Action(type="open_url", url="https://e.com"), sleep=sleep, timeout=1.0) is True
    # A modifier held past the timeout skips the action instead of sending Ctrl+letters.
    ticks = iter(i * 0.5 for i in range(100))
    assert created._wait_modifiers_up(text, sleep=lambda _s: None, timeout=1.0, clock=lambda: next(ticks)) is False


def test_reconcile_keeps_a_held_key_keyware_swallowed():
    """A swallowed key-down never reaches the OS key state.

    GetAsyncKeyState then says the key is up while the user still holds it.
    Reconcile must not forget it, or no hold ever fires in the real app.
    """
    from engine.models import Action
    from tests.test_detector import binding, detector, fires

    engine, clock = detector(binding("hold", ["p"], Action(type="type_text", text="[HOLD]"), hold_ms=600))
    down = engine.handle("p", True, clock.now)
    assert down.suppress is True
    engine.reconcile(lambda _name: False, clock.advance(200))
    assert "p" in engine.down_keys
    assert fires(engine.tick(clock.advance(450)))


def test_reconcile_still_drops_a_swallowed_key_that_went_silent():
    from tests.test_detector import binding, detector

    engine, clock = detector(binding("hold", ["p"], hold_ms=5000))
    engine.handle("p", True, clock.now)
    engine.reconcile(lambda _name: False, clock.advance(2500))
    assert "p" not in engine.down_keys


def test_typing_action_is_skipped_when_another_window_took_focus(tmp_path):
    from engine.models import Action

    created, *_ = service(tmp_path)
    windows = iter([101, 202])
    created._window_probe = lambda: next(windows)
    hwnd = created._fire_window()
    assert hwnd == 101
    text = Action(type="type_text", text="hi")
    assert created._same_window(text, hwnd) is False  # focus moved to 202
    created._window_probe = lambda: 101
    assert created._same_window(text, 101) is True
    # Opening a URL does not type into a window, so it never waits on focus.
    created._window_probe = lambda: 999
    assert created._same_window(Action(type="open_url", url="https://e.com"), 101) is True
    # No probe (tests, non-Windows): never skip.
    created._window_probe = None
    assert created._fire_window() is None
    assert created._same_window(text, None) is True


def test_main_sets_an_app_user_model_id_for_the_taskbar_icon():
    import main

    calls = []

    class Shell:
        def SetCurrentProcessExplicitAppUserModelID(self, value):
            calls.append(value)
            return 0

    assert main.set_app_user_model_id(Shell()) is True
    assert calls == ["Keyware.Keyware"]


def test_typing_stops_mid_text_when_another_window_comes_to_the_front():
    from engine.actions import ActionExecutor
    from engine.models import Action

    typed = []
    front = {"same": True}

    class Inj:
        def type_text(self, text):
            typed.append(text)
            if len(typed) == 3:
                front["same"] = False

        def press(self, key):
            pass

        def release(self, key):
            pass

    ex = ActionExecutor(opener=None, injector=Inj(), sleeper=lambda s: None, rng=None)
    ex.guard = lambda: front["same"]
    ex.run(Action(type="type_text", text="[CHORD]"))
    assert "".join(typed) == "[CH"


def test_startup_is_written_to_the_file_log(tmp_path):
    from engine import __version__
    from tests.fakes import service

    created, *_ = service(tmp_path)
    created.config.enabled = True
    created.start()
    text = (tmp_path / "keyware.log").read_text(encoding="utf-8")
    assert f"Keyware {__version__} started" in text and "ON" in text


def test_status_dot_tooltip_text():
    from ui.tooltip import dot_tooltip

    assert dot_tooltip(True, False) == "Keyware is ON - click to turn off"
    assert dot_tooltip(False, False) == "Keyware is OFF - click to turn on"
    assert dot_tooltip(True, True) == "Keyware is PAUSED - click to turn off"

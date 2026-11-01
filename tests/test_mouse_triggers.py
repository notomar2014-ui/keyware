"""Mouse button triggers: headless, no real hooks."""
from types import SimpleNamespace

import pytest

from engine import hook_engine
from engine.hook_engine import CombinedHook, HookEngine, MouseHookEngine, classify_mouse_event
from engine.keys import canonicalize, format_key
from engine.models import _require_keys, _require_mouse_modifier
from engine.sendinput import KEYWARE_EXTRA, TaggedInjector, build_mouse_button


class FakeListener:
    def __init__(self, f):
        self.filter, self.running, self.suppressed = f, False, 0

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def suppress_event(self):
        self.suppressed += 1


def ev(data=0, extra=0):
    return SimpleNamespace(mouseData=data, dwExtraInfo=extra)


def make(monkeypatch, decide=lambda k, p: True, wants=None):
    monkeypatch.setattr(hook_engine, "is_windows", lambda: True)
    seen = []
    eng = MouseHookEngine(lambda k, p: seen.append((k, p)) or decide(k, p), wants=wants,
                          listener_factory=FakeListener)
    eng.start()
    return eng, seen


def test_classify():
    assert classify_mouse_event(0x207, 0, 0) == ("mouse_middle", True)
    assert classify_mouse_event(0x20C, 2 << 16, 0) == ("mouse_x2", False)
    assert classify_mouse_event(0x20B, 1 << 16, 0) == ("mouse_x1", True)
    assert classify_mouse_event(0x200, 0, 0) is None  # move
    assert classify_mouse_event(0x20A, 0, 0) is None  # wheel
    assert classify_mouse_event(0x207, 0, KEYWARE_EXTRA) is None  # our replay


def test_middle_suppressed_when_bound(monkeypatch):
    eng, seen = make(monkeypatch)
    eng._filter(0x207, ev()); eng._filter(0x208, ev())
    assert seen == [("mouse_middle", True), ("mouse_middle", False)]
    assert eng._listener.suppressed == 2


def test_plain_left_never_forwarded(monkeypatch):
    eng, seen = make(monkeypatch, wants=lambda n: n not in ("mouse_left", "mouse_right"))
    for m in (0x201, 0x202, 0x204, 0x205):
        assert eng._filter(m, ev()) is False
    assert seen == [] and eng._listener.suppressed == 0


def test_orphan_up_ignored_and_errors_fail_open(monkeypatch):
    eng, seen = make(monkeypatch, decide=lambda k, p: 1 / 0)
    assert eng._filter(0x208, ev()) is False and seen == []
    assert eng._filter(0x207, ev()) is False and eng._listener.suppressed == 0


def test_combined_hook_mouse_failure_keeps_keyboard(monkeypatch):
    monkeypatch.setattr(hook_engine, "is_windows", lambda: True)
    kb = HookEngine(lambda k, p: False, listener_factory=FakeListener)

    class Boom(FakeListener):
        def start(self):
            raise OSError("no hook")

    hook = CombinedHook(kb, MouseHookEngine(lambda k, p: False, listener_factory=Boom))
    hook.start()
    assert hook.is_running() and not hook.mouse.is_running()
    hook.flags_for_callback = print
    assert kb.flags_for_callback is print
    hook.stop()
    assert not hook.is_running()


def test_keys_and_validation():
    assert canonicalize("middle click") == "mouse_middle"
    assert canonicalize("mouse_x1") == "mouse_x1"
    assert format_key("mouse_x2") == "Mouse Forward (X2)"
    assert _require_keys(["ctrl", "mouse_left"]) == ["ctrl", "mouse_left"]
    with pytest.raises(ValueError):
        _require_mouse_modifier(["mouse_left"])
    _require_mouse_modifier(["mouse_middle"])
    _require_mouse_modifier(["alt", "mouse_right"])
    with pytest.raises(ValueError):
        _require_keys(["mouse_middle"], sending=True)


def test_replay_x_button():
    sent = []
    inj = TaggedInjector(send=sent.extend, scan_for=lambda vk: 0)
    inj.press("mouse_x1"); inj.release("mouse_x1")
    assert [(e.mouse_flags, e.mouse_data) for e in sent] == [(0x80, 1), (0x100, 1)]
    assert build_mouse_button("middle", up=False).mouse_flags


def test_service_mouse_bindings(tmp_path):
    from engine.models import Action, Binding
    from tests.fakes import service

    created, opener, injector, _a, _h = service(tmp_path)
    created.set_enabled(True)
    assert created.wants_mouse("mouse_middle") and not created.wants_mouse("mouse_left")
    created.add_binding("global", Binding(id="m", enabled=True, mode="tap", keys=["mouse_middle"],
                                          action=Action(type="open_app", app="notepad.exe")))
    created.add_binding("global", Binding(id="l", enabled=True, mode="chord", keys=["ctrl", "mouse_left"],
                                          action=Action(type="open_app", app="calc.exe")))
    assert created.wants_mouse("mouse_left") and not created.wants_mouse("mouse_right")
    created.handle_input("mouse_middle", True, 0.0)
    created.handle_input("mouse_middle", False, 0.05)
    created.handle_input("ctrl", True, 1.0)
    created.handle_input("mouse_left", True, 1.01)
    created.handle_input("mouse_left", False, 1.05)
    created.handle_input("ctrl", False, 1.1)
    assert opener.apps == ["notepad.exe", "calc.exe"]
    # Unbound X1 passes through untouched.
    assert created.handle_input("mouse_x1", True, 2.0) is False
    assert created.handle_input("mouse_x1", False, 2.05) is False
    with pytest.raises(ValueError):
        created.add_binding("global", Binding(id="bad", enabled=True, mode="tap", keys=["mouse_right"],
                                              action=Action(type="open_app", app="x.exe")))

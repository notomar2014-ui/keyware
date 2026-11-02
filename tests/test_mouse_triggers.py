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


# --- any-button: wheel, tilt, extra buttons, every VK, unknown VK / scan code ---
from engine.hook_engine import classify_wheel
from engine.keys import is_allowed, name_to_vk, vk_to_name
from engine.sendinput import classify_hook_event


def test_wheel_and_tilt_classify():
    assert classify_wheel(0x20A, 120 << 16) == "mouse_wheel_up"
    assert classify_wheel(0x20A, (-120 & 0xFFFF) << 16) == "mouse_wheel_down"
    assert classify_wheel(0x20E, 120 << 16) == "mouse_wheel_right"
    assert classify_wheel(0x20E, (-120 & 0xFFFF) << 16) == "mouse_wheel_left"
    assert classify_wheel(0x200, 120 << 16) is None


def test_extra_mouse_buttons():
    assert classify_mouse_event(0x20B, 3 << 16, 0) == ("mouse_x3", True)
    assert classify_mouse_event(0x20B, 0, 0) is None
    assert canonicalize("mouse_x7") == "mouse_x7" and format_key("mouse_x3") == "Mouse Button 6"


def test_wheel_hook_only_when_bound(monkeypatch):
    eng, seen = make(monkeypatch, wants=lambda n: n == "mouse_wheel_left")
    eng._filter(0x20A, ev(120 << 16))
    assert seen == []
    eng._filter(0x20E, ev((-120 & 0xFFFF) << 16))
    assert seen == [("mouse_wheel_left", True), ("mouse_wheel_left", False)]
    assert eng._listener.suppressed == 1
    eng._filter(0x20E, ev((-120 & 0xFFFF) << 16, KEYWARE_EXTRA))  # own replay ignored
    assert len(seen) == 2


def test_wheel_needs_modifier_tilt_does_not():
    with pytest.raises(ValueError):
        _require_mouse_modifier(["mouse_wheel_up"])
    _require_mouse_modifier(["ctrl", "mouse_wheel_down"])
    _require_mouse_modifier(["mouse_wheel_left"])
    assert canonicalize("scroll up") == "mouse_wheel_up" and canonicalize("tilt right") == "mouse_wheel_right"


@pytest.mark.parametrize("name,vk", [("f13", 0x7C), ("f24", 0x87), ("browser_back", 0xA6),
                                     ("launch_mail", 0xB4), ("launch_app2", 0xB7), ("media_play_pause", 0xB3),
                                     ("num_lock", 0x90), ("pause", 0x13), ("sleep", 0x5F)])
def test_every_vk_named(name, vk):
    assert vk_to_name(vk) == name and name_to_vk(name) == vk and canonicalize(name) == name


def test_every_vk_has_a_name():
    for vk in range(1, 0xFF):
        if vk in (1, 2, 4, 5, 6):
            assert vk_to_name(vk) is None
        else:
            assert is_allowed(vk_to_name(vk)), hex(vk)


def test_unknown_vk_and_scan_code():
    assert vk_to_name(0xE8) == "vk_e8" and name_to_vk("vk_e8") == 0xE8
    assert canonicalize("vk_0xE8") == "vk_e8" and canonicalize("vk_7c") == "f13"
    assert format_key("vk_e8") == "Key 0xE8"
    assert _require_keys(["vk_e8"], sending=True) == ["vk_e8"]
    ev_ = classify_hook_event(0x100, 0xFF, 0x01, 0, already_down=False, scan=0x6E)
    assert ev_.kind == "key" and ev_.name == "sc_e06e"
    assert classify_hook_event(0x100, 0xFF, 0, 0, already_down=False).kind == "ignore"
    with pytest.raises(ValueError):
        _require_keys(["sc_e06e"], sending=True)
    assert _require_keys(["sc_e06e"]) == ["sc_e06e"]
    assert canonicalize("copilot") == "copilot"


def test_replay_wheel_and_scan():
    sent = []
    inj = TaggedInjector(send=sent.extend, scan_for=lambda vk: 0)
    inj.press("mouse_wheel_down"); inj.release("mouse_wheel_down")
    inj.press("sc_e06e"); inj.release("vk_e8")
    assert (sent[0].mouse_flags, sent[0].mouse_data) == (0x0800, (-120) & 0xFFFFFFFF)
    assert sent[1].scan == 0x6E and sent[1].flags & 0x0008 and sent[2].vk == 0xE8
    inj.press("mouse_x3")  # cannot be synthesised: no-op
    assert len(sent) == 3


def test_service_wheel_and_extra_only_when_bound(tmp_path):
    from engine.models import Action, Binding
    from tests.fakes import service

    created, opener, *_ = service(tmp_path)
    created.set_enabled(True)
    assert not created.wants_mouse("mouse_wheel_up") and not created.wants_mouse("mouse_x3")
    created.add_binding("global", Binding(id="w", enabled=True, mode="chord", keys=["ctrl", "mouse_wheel_up"],
                                          action=Action(type="open_app", app="notepad.exe")))
    assert created.wants_mouse("mouse_wheel_up") and not created.wants_mouse("mouse_wheel_down")

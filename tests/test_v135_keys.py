"""v1.3.5: Copilot key, Caps Lock remapping, tap / hold for any key, macropad F13-F24.

Everything here runs on fake hook events against the in-memory service.
"""

from __future__ import annotations

import pytest

from engine import copilot
from engine.copilot import CopilotFolder
from engine.models import Action, Binding, Config, validate_binding, trigger_text
from engine.recorder import KeyRecorder
from tests.fakes import service


def url(link="https://example.com"):
    return Action(type="open_url", url=link)


def combo(*keys):
    return Action(type="key_combo", keys=list(keys))


def _svc(tmp_path, *bindings, caps_on=False):
    created, opener, injector, audio, hook = service(tmp_path)
    created._caps_probe = lambda: caps_on
    created.set_enabled(True)
    for binding in bindings:
        created.add_binding("global", binding)
    created.poll()
    injector.events.clear()
    return created, opener, injector


def press_copilot(svc, t=1.0, *, gap=0.001):
    """What the hardware sends for one Copilot key press."""
    return [
        svc.handle_input("win", True, t),
        svc.handle_input("shift", True, t + gap),
        svc.handle_input("f23", True, t + 2 * gap),
    ]


def release_copilot(svc, t=1.1, order=("f23", "shift", "win"), gap=0.001):
    return [svc.handle_input(key, False, t + i * gap) for i, key in enumerate(order)]


COPILOT_URL = Binding(id="cp", enabled=True, mode="tap", keys=["copilot"], label="Copilot", action=url())


# --- the folder on its own --------------------------------------------------

def test_folder_detects_the_burst_and_swallows_the_releases_in_any_order():
    for order in (("f23", "shift", "win"), ("win", "shift", "f23"), ("shift", "f23", "win")):
        folder = CopilotFolder(enabled=True)
        assert folder.feed("win", True, 0.0) == copilot.PASS  # never held back
        assert folder.feed("shift", True, 0.001) == copilot.PASS
        assert folder.feed("f23", True, 0.002) == copilot.DOWN
        decisions = [folder.feed(key, False, 0.1 + i / 1000) for i, key in enumerate(order)]
        assert decisions.count(copilot.UP) == 1
        assert decisions.count(copilot.SWALLOW) == 2
        assert not folder.busy()


def test_folder_ignores_f23_alone_and_win_shift_other_keys():
    folder = CopilotFolder(enabled=True)
    assert folder.feed("f23", True, 0.0) == copilot.PASS  # macropad F23
    assert folder.feed("f23", False, 0.05) == copilot.PASS
    assert folder.feed("win", True, 1.0) == copilot.PASS
    assert folder.feed("shift", True, 1.01) == copilot.PASS
    assert folder.feed("s", True, 1.02) == copilot.PASS  # Win+Shift+S
    assert folder.feed("f23", True, 1.03) == copilot.PASS  # another key came in between
    folder.reset()
    assert folder.feed("win", True, 2.0) == copilot.PASS
    assert folder.feed("shift", True, 2.01) == copilot.PASS
    assert folder.feed("f23", True, 2.6) == copilot.PASS  # far too slow for hardware


def test_folder_off_when_nothing_uses_the_key():
    folder = CopilotFolder(enabled=False)
    folder.feed("win", True, 0.0)
    folder.feed("shift", True, 0.001)
    assert folder.feed("f23", True, 0.002) == copilot.PASS


def test_folder_forgets_a_press_whose_key_ups_never_came():
    folder = CopilotFolder(enabled=True)
    folder.feed("win", True, 0.0)
    folder.feed("shift", True, 0.001)
    folder.feed("f23", True, 0.002)
    assert folder.tick(10.0) is False
    assert folder.tick(40.0) is True
    assert not folder.busy()


# --- through the service ----------------------------------------------------

def test_copilot_key_fires_its_binding_and_never_leaves_win_or_shift_down(tmp_path):
    svc, opener, injector = _svc(tmp_path, COPILOT_URL)
    assert press_copilot(svc) == [False, False, True]  # Win, Shift pass; F23 swallowed
    assert injector.events[:3] == [("release", "shift"), ("mask",), ("release", "win")]
    assert release_copilot(svc) == [True, True, True]
    assert opener.urls == ["https://example.com"]
    presses = [e for e in injector.events if e[0] == "press"]
    assert presses == []
    assert not svc.physical.holding("win") and not svc.physical.holding("shift")
    assert "win" not in svc._detector.down_keys and "shift" not in svc._detector.down_keys


@pytest.mark.parametrize("order", [("f23", "shift", "win"), ("win", "shift", "f23"), ("shift", "win", "f23")])
def test_copilot_any_release_order(tmp_path, order):
    svc, opener, _inj = _svc(tmp_path, COPILOT_URL)
    press_copilot(svc)
    assert release_copilot(svc, order=order) == [True, True, True]
    assert opener.urls == ["https://example.com"]
    assert not svc._copilot.busy()
    assert svc.handle_input("a", True, 2.0) is False


def test_copilot_rapid_taps_fire_each_time(tmp_path):
    svc, opener, _inj = _svc(tmp_path, COPILOT_URL)
    t = 1.0
    for _ in range(5):
        press_copilot(svc, t)
        release_copilot(svc, t + 0.03)
        t += 0.08
    assert len(opener.urls) == 5


def test_copilot_held_with_auto_repeat_fires_once(tmp_path):
    svc, opener, _inj = _svc(tmp_path, COPILOT_URL)
    press_copilot(svc)
    for i in range(4):  # auto-repeat of F23 (and of Win / Shift on some keyboards)
        assert svc.handle_input("f23", True, 1.03 + i * 0.03) is True
        assert svc.handle_input("win", True, 1.031 + i * 0.03) is True
    release_copilot(svc, 1.2)
    assert len(opener.urls) == 1


def test_copilot_as_a_held_modifier_with_tap_hold(tmp_path):
    binding = Binding(id="cph", enabled=True, mode="hold", keys=["copilot"], hold_ms=200,
                      action=combo("ctrl"), hold_press=True, tap_action=url("https://copilot.microsoft.com"))
    svc, opener, injector = _svc(tmp_path, binding)
    press_copilot(svc)
    svc.tick(1.3)
    assert ("press", "ctrl") in injector.events
    release_copilot(svc, 1.5)
    assert injector.events[-1] == ("release", "ctrl")
    assert opener.urls == []
    press_copilot(svc, 3.0)
    release_copilot(svc, 3.05)
    assert opener.urls == ["https://copilot.microsoft.com"]


def test_unbound_copilot_key_keeps_the_windows_default(tmp_path):
    svc, opener, injector = _svc(tmp_path)
    assert press_copilot(svc) == [False, False, False]
    assert release_copilot(svc) == [False, False, False]
    assert injector.events == []


def test_win_shift_shortcuts_are_untouched_while_copilot_is_bound(tmp_path):
    binding = Binding(id="snip", enabled=True, mode="chord", keys=["win", "shift", "s"], action=url("https://snip"))
    svc, opener, injector = _svc(tmp_path, COPILOT_URL, binding)
    assert svc.handle_input("win", True, 1.0) is False
    assert svc.handle_input("shift", True, 1.01) is False
    assert svc.handle_input("s", True, 1.02) is True
    assert opener.urls == ["https://snip"]
    for key in ("s", "shift", "win"):
        svc.handle_input(key, False, 1.1)
    assert ("release", "win") not in injector.events
    # Win+Shift+Left arrow passes straight through.
    assert svc.handle_input("win", True, 2.0) is False
    assert svc.handle_input("shift", True, 2.01) is False
    assert svc.handle_input("left", True, 2.02) is False


def test_plain_f23_still_works_while_copilot_is_bound(tmp_path):
    pad = Binding(id="pad", enabled=True, mode="tap", keys=["f23"], action=url("https://pad"))
    svc, opener, _inj = _svc(tmp_path, COPILOT_URL, pad)
    svc.handle_input("f23", True, 1.0)
    svc.handle_input("f23", False, 1.05)
    assert opener.urls == ["https://pad"]


def test_copilot_passes_when_keyware_is_paused_or_dry_run(tmp_path):
    svc, opener, injector = _svc(tmp_path, COPILOT_URL)
    svc.config.dry_run = True
    assert press_copilot(svc) == [False, False, False]
    release_copilot(svc)
    svc.config.dry_run = False
    svc._copilot.reset()
    svc.set_enabled(False)
    assert press_copilot(svc, 3.0) == [False, False, False]
    assert opener.urls == []


def test_recorder_records_the_copilot_key_as_one_key(tmp_path):
    svc, _opener, injector = _svc(tmp_path)
    assert svc.begin_recording() == "hook"
    assert press_copilot(svc) == [True, True, True]
    assert release_copilot(svc) == [True, True, True]
    msgs = [m for m in svc.poll() if m.kind.startswith("record")]
    done = msgs[-1]
    assert done.kind == "record_done" and done.keys == ("copilot",) and done.mode == "tap"
    assert "Copilot key" in done.text
    assert injector.events == []  # nothing reached Windows, nothing to release


def test_copilot_key_validates_and_cannot_be_sent():
    config = Config()
    b = Binding(id="c", enabled=True, mode="tap", keys=["copilot"], action=url())
    validate_binding(b, config)
    assert "Copilot key" in trigger_text(b, None)
    # A recorded Win+Shift+F23 chord is the Copilot key too.
    chord = Binding(id="c2", enabled=True, mode="chord", keys=["win", "shift", "f23"], action=url())
    validate_binding(chord, config)
    assert chord.keys == ["copilot"] and chord.mode == "tap"
    with pytest.raises(ValueError):
        validate_binding(Binding(id="c3", enabled=True, mode="tap", keys=["f1"], action=combo("copilot")), config)


# --- Caps Lock --------------------------------------------------------------

def test_caps_lock_bound_never_toggles_and_shift_caps_still_does(tmp_path):
    caps = Binding(id="caps", enabled=True, mode="tap", keys=["caps_lock"], action=combo("esc"))
    svc, _opener, injector = _svc(tmp_path, caps)
    assert svc.handle_input("caps_lock", True, 1.0) is True
    assert svc.handle_input("caps_lock", False, 1.05) is True
    assert ("press", "esc") in injector.events and ("press", "caps_lock") not in injector.events
    injector.events.clear()
    assert svc.handle_input("shift", True, 2.0) is False
    assert svc.handle_input("caps_lock", True, 2.01) is False  # real Caps Lock toggle
    assert svc.handle_input("caps_lock", False, 2.05) is False
    svc.handle_input("shift", False, 2.1)
    assert injector.events == []


def test_caps_lock_can_be_turned_off(tmp_path):
    off = Binding(id="off", enabled=True, mode="tap", keys=["caps_lock"], action=Action(type="none"))
    validate_binding(off, Config())
    svc, opener, injector = _svc(tmp_path, off)
    assert svc.handle_input("caps_lock", True, 1.0) is True
    assert svc.handle_input("caps_lock", False, 1.05) is True
    assert injector.events == [] and opener.urls == []


def test_binding_caps_lock_turns_a_lit_caps_lock_off_once(tmp_path):
    caps = Binding(id="caps", enabled=True, mode="tap", keys=["caps_lock"], action=combo("esc"))
    svc, _opener, injector = _svc(tmp_path, caps_on=True)
    svc.tick(0.5)
    assert injector.events == []
    svc.add_binding("global", caps)
    svc.tick(1.0)
    assert injector.events == [("press", "caps_lock"), ("release", "caps_lock")]
    svc.tick(1.5)  # not again: Shift + Caps Lock may have turned it on on purpose
    assert injector.events == [("press", "caps_lock"), ("release", "caps_lock")]
    assert any("Caps Lock light off" in m.text for m in svc.poll() if m.text)


def test_caps_tap_esc_hold_ctrl(tmp_path):
    caps = Binding(id="caps", enabled=True, mode="hold", keys=["caps_lock"], hold_ms=200,
                   action=combo("ctrl"), hold_press=True, tap_action=combo("esc"))
    validate_binding(caps, Config())
    svc, _opener, injector = _svc(tmp_path, caps)
    # tap
    assert svc.handle_input("caps_lock", True, 1.0) is True
    assert svc.handle_input("caps_lock", False, 1.08) is True
    assert injector.events == [("press", "esc"), ("release", "esc")]
    injector.events.clear()
    # hold past the threshold
    svc.handle_input("caps_lock", True, 2.0)
    svc.tick(2.25)
    assert injector.events == [("press", "ctrl")]
    assert svc.handle_input("c", True, 2.3) is False  # Ctrl+C from the OS's point of view
    svc.handle_input("c", False, 2.35)
    svc.handle_input("caps_lock", False, 2.5)
    assert injector.events[-1] == ("release", "ctrl")
    injector.events.clear()
    # fast roll: Caps then C before 200 ms is still Ctrl+C
    svc.handle_input("caps_lock", True, 3.0)
    assert svc.handle_input("c", True, 3.05) is True  # replayed after Ctrl
    assert injector.events == [("press", "ctrl"), ("press", "c")]
    svc.handle_input("c", False, 3.08)
    svc.handle_input("caps_lock", False, 3.1)
    assert injector.events[-1] == ("release", "ctrl")
    assert "ctrl" not in svc._detector.replayed


def test_held_caps_ctrl_is_released_on_pause_and_off(tmp_path):
    caps = Binding(id="caps", enabled=True, mode="hold", keys=["caps_lock"], hold_ms=200,
                   action=combo("ctrl"), hold_press=True, tap_action=combo("esc"))
    svc, _opener, injector = _svc(tmp_path, caps)
    svc.handle_input("caps_lock", True, 1.0)
    svc.tick(1.3)
    assert ("press", "ctrl") in injector.events
    svc.set_enabled(False)
    assert ("release", "ctrl") in injector.events
    injector.events.clear()
    svc.handle_input("caps_lock", False, 1.5)
    assert injector.events == []


# --- tap / hold on any key --------------------------------------------------

def test_tap_hold_on_a_letter_key(tmp_path):
    j = Binding(id="j", enabled=True, mode="hold", keys=["f8"], hold_ms=250,
                action=url("https://hold"), tap_action=url("https://tap"))
    svc, opener, injector = _svc(tmp_path, j)
    svc.handle_input("f8", True, 1.0)
    svc.handle_input("f8", False, 1.1)
    assert opener.urls == ["https://tap"]
    svc.handle_input("f8", True, 2.0)
    svc.tick(2.3)
    assert opener.urls == ["https://tap", "https://hold"]
    for i in range(5):  # auto-repeat does not fire again
        svc.handle_input("f8", True, 2.3 + i * 0.03)
    svc.tick(2.6)
    svc.handle_input("f8", False, 2.7)
    assert opener.urls == ["https://tap", "https://hold"]
    assert injector.events == []


def test_tap_hold_without_tap_action_types_the_key_on_a_tap(tmp_path):
    b = Binding(id="sp", enabled=True, mode="hold", keys=["f9"], hold_ms=200,
                action=combo("shift"), hold_press=True)
    svc, _opener, injector = _svc(tmp_path, b)
    svc.handle_input("f9", True, 1.0)
    svc.handle_input("f9", False, 1.05)
    assert injector.events == [("press", "f9"), ("release", "f9")]


def test_another_key_during_a_one_shot_hold_makes_it_a_tap(tmp_path):
    j = Binding(id="j", enabled=True, mode="hold", keys=["f8"], hold_ms=250,
                action=url("https://hold"), tap_action=url("https://tap"))
    svc, opener, _inj = _svc(tmp_path, j)
    svc.handle_input("f8", True, 1.0)
    svc.handle_input("a", True, 1.05)
    svc.tick(1.5)
    svc.handle_input("a", False, 1.55)
    svc.handle_input("f8", False, 1.6)
    assert opener.urls == ["https://tap"]


def test_keys_without_a_hold_action_are_never_delayed(tmp_path):
    j = Binding(id="j", enabled=True, mode="hold", keys=["f8"], hold_ms=250,
                action=url("https://hold"), tap_action=url("https://tap"))
    tap = Binding(id="t", enabled=True, mode="tap", keys=["f7"], action=url("https://f7"))
    svc, opener, injector = _svc(tmp_path, j, tap)
    for i, key in enumerate("hello"):
        assert svc.handle_input(key, True, 1.0 + i / 100) is False
        assert svc.handle_input(key, False, 1.005 + i / 100) is False
    assert injector.events == []
    svc.handle_input("f7", True, 2.0)
    svc.handle_input("f7", False, 2.02)
    assert opener.urls == ["https://f7"]  # a tap binding fires on the press, same as before


def test_tap_hold_round_trips_and_validates():
    config = Config()
    b = Binding(id="b", enabled=True, mode="hold", keys=["caps_lock"], hold_ms=200,
                action=combo("ctrl"), hold_press=True, tap_action=combo("esc"))
    validate_binding(b, config)
    again = Binding.from_dict(b.to_dict())
    assert again.tap_action.keys == ["esc"] and again.hold_press and again.hold_ms == 200
    assert "Tap / hold" in trigger_text(again, None)
    plain = Binding(id="p", enabled=True, mode="hold", keys=["p"], hold_ms=200, action=url())
    with pytest.raises(ValueError):
        validate_binding(plain, config)  # plain long-press still needs 300 ms or more
    bad = Binding(id="x", enabled=True, mode="hold", keys=["f8"], hold_ms=200,
                  action=url(), hold_press=True)
    with pytest.raises(ValueError):
        validate_binding(bad, config)  # "keep held" only makes sense for keys
    tapped = Binding(id="y", enabled=True, mode="tap", keys=["f8"], action=url(), tap_action=url())
    validate_binding(tapped, config)
    assert tapped.tap_action is None  # only hold bindings keep a tap side


def test_existing_two_tier_holds_still_work(tmp_path):
    svc, opener, injector, _audio, _hook = service(tmp_path)
    svc.set_binding_enabled("global", "bind-youtube-hold-p", True)
    svc.set_enabled(True)
    assert svc.handle_input("p", True, 0.0) is True
    assert svc.handle_input("p", False, 0.1) is True
    assert ("press", "p") in injector.events


# --- macropads --------------------------------------------------------------

@pytest.mark.parametrize("key", [f"f{n}" for n in range(13, 25)])
def test_recorder_captures_macropad_f13_to_f24(key):
    rec = KeyRecorder()
    msgs = rec.feed(key, True, 0.0) + rec.feed(key, False, 0.05)
    [result] = [m for m in msgs if m.kind == "done"]
    assert result.keys == (key,) and result.mode == "tap"


def test_macropad_ctrl_alt_f13_records_as_a_chord(tmp_path):
    svc, _opener, _inj = _svc(tmp_path)
    svc.begin_recording()
    for key, pressed, t in [("ctrl", True, 1.0), ("alt", True, 1.001), ("f13", True, 1.002),
                            ("f13", False, 1.05), ("alt", False, 1.051), ("ctrl", False, 1.052)]:
        assert svc.handle_input(key, pressed, t) is True
    done = [m for m in svc.poll() if m.kind == "record_done"][-1]
    assert done.keys == ("ctrl", "alt", "f13") and done.mode == "chord"


# --- AI Text mode -----------------------------------------------------------

def _ai(reply):
    import json as _json

    from assist.ai_binder import parse_batch

    return parse_batch(_json.dumps(reply))


def test_ai_caps_tap_esc_hold_ctrl():
    [p] = _ai({"ok": True, "label": "Caps", "profile": {"scope": "global"},
               "trigger": {"mode": "hold", "keys": ["caps lock"], "hold_ms": 200, "keep_held": True},
               "action": {"type": "key_combo", "keys": ["ctrl"]},
               "tap_action": {"type": "key_combo", "keys": ["esc"]}})
    b = p.binding
    assert b.keys == ["caps_lock"] and b.mode == "hold" and b.hold_ms == 200
    assert b.hold_press and b.tap_action.keys == ["esc"]
    lines = dict(p.summary_lines())
    assert lines["Tap does"] == "Key or shortcut" or "Tap does" in lines


def test_ai_turn_off_caps_and_copilot_to_cursor():
    [off] = _ai({"ok": True, "trigger": {"mode": "tap", "keys": ["caps_lock"]}, "action": {"type": "none"}})
    assert off.binding.action.type == "none"
    [cp] = _ai({"ok": True, "trigger": {"mode": "tap", "keys": ["Copilot key"]},
                "action": {"type": "open_app", "app": "cursor.exe"}})
    assert cp.binding.keys == ["copilot"]
    [chord] = _ai({"ok": True, "trigger": {"mode": "chord", "keys": ["win", "shift", "f23"]},
                   "action": {"type": "open_app", "app": "cursor.exe"}})
    assert chord.binding.keys == ["copilot"] and chord.binding.mode == "tap"


def test_ai_never_sends_the_copilot_key():
    from assist.ai_binder import AIBinderError

    with pytest.raises(AIBinderError):
        _ai({"ok": True, "trigger": {"mode": "tap", "keys": ["f1"]}, "action": {"type": "key_combo", "keys": ["copilot"]}})


def test_ai_tap_hold_one_shot_and_prompt_knows_the_new_words():
    from assist.ai_binder import SYSTEM_PROMPT

    [p] = _ai({"ok": True, "trigger": {"mode": "hold", "keys": ["f9"], "hold_ms": 400},
               "action": {"type": "open_app", "app": "spotify.exe"},
               "tap_action": {"type": "media", "media": "media_play_pause"}})
    assert p.binding.tap_action.type == "media" and not p.binding.hold_press
    for word in ("copilot", "caps_lock", "tap_action", "keep_held", "cursor.exe", "obs64.exe",
                 "photoshop.exe", "figma.exe", "blender.exe", "slack.exe", "winword.exe", "excel.exe",
                 "adobe premiere pro.exe", "f13-f24"):
        assert word in SYSTEM_PROMPT


def test_version_flag_prints_and_writes_the_version(tmp_path, capsys):
    import main
    from engine import __version__

    out = tmp_path / "v.txt"
    assert main.main(["--version", "--out", str(out)]) == 0
    assert __version__ == "1.3.7"
    assert out.read_text(encoding="utf-8").strip() == "1.3.7"
    assert "1.3.7" in capsys.readouterr().out

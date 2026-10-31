"""Hotkey recorder: the trigger field records keys directly."""

from __future__ import annotations

from engine.recorder import KeyRecorder
from tests.fakes import service


def feed(rec, events):
    out = []
    t = 0.0
    for key, pressed, dt in events:
        t += dt
        out.extend(rec.feed(key, pressed, t))
    return out


def done(messages):
    return [m for m in messages if m.kind == "done"]


def test_chord_with_four_keys_fills_on_release_and_shows_live_modifiers():
    rec = KeyRecorder()
    msgs = feed(rec, [("ctrl", True, 0), ("win", True, .05), ("shift", True, .05), ("r", True, .05),
                      ("r", False, .05), ("shift", False, .02), ("win", False, .02), ("ctrl", False, .02)])
    live = [m.text for m in msgs if m.kind == "live"]
    assert live[0] == "Ctrl + …"
    assert "Ctrl + Win + Shift + R" in live
    [result] = done(msgs)
    assert result.keys == ("ctrl", "win", "shift", "r") and result.mode == "chord"
    assert rec.finished  # every key is up, nothing left to swallow


def test_single_key_tap_and_function_key():
    rec = KeyRecorder()
    [result] = done(feed(rec, [("f1", True, 0), ("f1", False, .08)]))
    assert result.keys == ("f1",) and result.mode == "tap"


def test_long_press_records_a_hold_with_its_length():
    rec = KeyRecorder()
    msgs = feed(rec, [("p", True, 0)])
    msgs += rec.tick(1.0)
    assert any(m.kind == "live" and "Hold P" in m.text for m in msgs)
    [result] = done(feed(rec, [("p", True, 1.0), ("p", False, 1.04)]))  # repeat then release at ~2.04 s
    assert result.mode == "hold" and result.keys == ("p",) and result.hold_ms == 2000


def test_esc_cancels_and_backspace_clears_but_with_a_modifier_they_record():
    rec = KeyRecorder()
    [m] = [x for x in feed(rec, [("esc", True, 0)]) if x.kind != "live"]
    assert m.kind == "cancel"
    rec = KeyRecorder()
    [m] = [x for x in feed(rec, [("backspace", True, 0)]) if x.kind != "live"]
    assert m.kind == "clear"
    rec = KeyRecorder()
    [result] = done(feed(rec, [("ctrl", True, 0), ("backspace", True, .05), ("backspace", False, .05), ("ctrl", False, .05)]))
    assert result.keys == ("ctrl", "backspace")


def test_leader_mode_skips_the_leader_key_and_records_the_next_one():
    rec = KeyRecorder(leader_key="f1")
    msgs = feed(rec, [("f1", True, 0), ("f1", False, .05), ("y", True, .2), ("y", False, .05)])
    [result] = done(msgs)
    assert result.keys == ("y",) and result.mode == "leader"


def test_single_key_target_ignores_modifiers():
    rec = KeyRecorder(single=True)
    [result] = done(feed(rec, [("shift", True, 0), ("f13", True, .05), ("f13", False, .05), ("shift", False, .05)]))
    assert result.keys == ("f13",)


def test_recording_times_out_instead_of_holding_the_keyboard():
    rec = KeyRecorder(timeout=10)
    rec.tick(0)
    assert rec.tick(5) == [] and not rec.finished
    assert [m.kind for m in rec.tick(10.5)] == ["cancel"]
    assert rec.finished


# --- service integration -----------------------------------------------------


def test_service_records_through_the_hook_and_suspends_bindings(tmp_path):
    created, opener, _inj, _audio, hook = service(tmp_path)
    created.set_enabled(True)
    from engine.models import Action, Binding

    created.add_binding("global", Binding(id="x", enabled=True, mode="chord", keys=["ctrl", "f9"],
                                          action=Action(type="open_url", url="https://example.com")))
    created.poll()
    assert created.begin_recording() == "hook"
    assert created.handle_input("ctrl", True, 1.0) is True  # swallowed while recording
    assert created.handle_input("f9", True, 1.05) is True
    assert created.handle_input("f9", False, 1.1) is True
    assert created.handle_input("ctrl", False, 1.15) is True
    assert opener.urls == []  # the binding did not fire
    msgs = [m for m in created.poll() if m.kind.startswith("record")]
    assert msgs[-1].kind == "record_done" and msgs[-1].keys == ("ctrl", "f9")
    assert created.recording is False
    assert created.handle_input("a", True, 2.0) is False  # normal again


def test_service_starts_the_hook_just_for_recording_when_off_and_stops_it_after(tmp_path):
    created, _o, _i, _a, hook = service(tmp_path)
    assert created.hook_status == "off"
    assert created.begin_recording() == "hook"
    assert hook.running and created.hook_status == "off" and created.config.enabled is False
    created.handle_input("f1", True, 1.0)
    created.handle_input("f1", False, 1.05)
    created.tick(1.1)  # the hook is stopped off its own thread
    assert hook.running is False
    assert created.config.enabled is False


def test_service_cancel_recording_releases_the_keyboard(tmp_path):
    created, _o, _i, _a, hook = service(tmp_path)
    created.begin_recording()
    created.cancel_recording()
    assert created.recording is False and hook.running is False
    assert created.handle_input("a", True, 1.0) is False


def test_tk_fallback_when_the_hook_cannot_start(tmp_path):
    from engine.hook_engine import HookUnavailable

    created, _o, _i, _a, hook = service(tmp_path)

    def boom():
        raise HookUnavailable("no hook here")

    hook.start = boom
    assert created.begin_recording() == "tk"
    created.feed_recording("shift", True, 1.0)
    created.feed_recording("x", True, 1.02)
    created.feed_recording("x", False, 1.05)
    created.feed_recording("shift", False, 1.06)
    msgs = [m for m in created.poll() if m.kind == "record_done"]
    assert msgs and msgs[0].keys == ("shift", "x")


def test_recording_stops_when_another_window_comes_to_the_front(tmp_path):
    created, _o, _i, _a, hook = service(tmp_path)
    created._window_probe = lambda: 11
    created.begin_recording()
    created._window_probe = lambda: 22
    created.tick(1.0)
    assert created.recording is False and hook.running is False
    assert any(m.kind == "record_cancel" for m in created.poll())


def test_a_lost_key_up_after_the_result_does_not_keep_swallowing_keys():
    rec = KeyRecorder()
    feed(rec, [("ctrl", True, 0), ("x", True, .05), ("x", False, .05)])  # ctrl key-up never arrives
    assert not rec.finished
    rec.tick(0.2)
    rec.tick(2.0)
    assert rec.finished

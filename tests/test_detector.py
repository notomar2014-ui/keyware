"""Input detector. Every case feeds synthetic events. Nothing is injected for real."""

from __future__ import annotations

from engine.detector import InputDetector
from engine.models import Action, Binding, Config, MacroStep, Profile

from tests.fakes import Clock


def binding(mode, keys, action=None, **kwargs) -> Binding:
    return Binding(
        id=kwargs.pop("id", f"b-{mode}-{'-'.join(keys)}-{kwargs.get('hold_ms', kwargs.get('taps', ''))}"),
        enabled=kwargs.pop("enabled", True),
        mode=mode,
        keys=list(keys),
        action=action or Action(type="open_url", url="https://example.com"),
        **kwargs,
    )


def detector(*bindings, gaming=(), leader_key=None, audio=True) -> tuple[InputDetector, Clock]:
    config = Config(
        enabled=True,
        audio_enabled=audio,
        leader_key=leader_key,
        profiles=[
            Profile(id="global", name="Global", process_names=[], enabled=True, bindings=list(bindings)),
            Profile(
                id="gaming",
                name="Gaming",
                process_names=["javaw.exe"],
                enabled=True,
                bindings=list(gaming),
            ),
        ],
    )
    engine = InputDetector(config)
    engine.set_enabled(True)
    engine.set_profiles(config.global_profile(), config.global_profile())
    return engine, Clock()


def fires(effects):
    if hasattr(effects, "effects"):
        effects = effects.effects
    return [effect for effect in effects if effect.kind == "fire"]


def test_unbound_key_is_not_swallowed():
    engine, clock = detector()
    down = engine.handle("z", True, clock.now)
    up = engine.handle("z", False, clock.advance(40))
    assert down.suppress is False
    assert up.suppress is False
    assert fires(down.effects) == []
    assert fires(up.effects) == []


def test_other_keys_pass_while_modifiers_are_held():
    engine, clock = detector(binding("chord", ["ctrl", "win", "shift", "r"]))
    for key in ("ctrl", "win", "shift"):
        assert engine.handle(key, True, clock.now).suppress is False
    assert engine.handle("a", True, clock.now).suppress is False
    assert engine.handle("a", False, clock.now).suppress is False


def test_chord_fires_only_when_every_modifier_is_down():
    engine, clock = detector(binding("chord", ["ctrl", "win", "shift", "r"], id="yt"))
    for key in ("ctrl", "win", "shift"):
        assert engine.handle(key, True, clock.now).suppress is False
    pressed = engine.handle("r", True, clock.now)
    assert pressed.suppress is True
    assert [effect.binding_id for effect in fires(pressed.effects)] == ["yt"]
    released = engine.handle("r", False, clock.advance(30))
    assert released.suppress is True
    assert fires(released.effects) == []


def test_chord_missing_a_modifier_types_the_key():
    engine, clock = detector(binding("chord", ["ctrl", "win", "shift", "r"]))
    engine.handle("ctrl", True, clock.now)
    engine.handle("win", True, clock.now)
    pressed = engine.handle("r", True, clock.now)
    assert pressed.suppress is False
    assert fires(pressed.effects) == []


def test_extra_modifier_does_not_match_a_shorter_chord():
    engine, clock = detector(binding("chord", ["ctrl", "r"]))
    engine.handle("ctrl", True, clock.now)
    engine.handle("shift", True, clock.now)
    assert engine.handle("r", True, clock.now).suppress is False


def test_tap_fires_under_300ms_and_not_at_300():
    engine, clock = detector(binding("tap", ["f"], id="tap"))
    engine.handle("f", True, clock.now)
    early = engine.handle("f", False, clock.advance(299))
    assert [effect.binding_id for effect in fires(early.effects)] == ["tap"]

    engine.handle("f", True, clock.now)
    late = engine.handle("f", False, clock.advance(300))
    assert fires(late.effects) == []


def test_hold_tiers_fire_once_each_and_release_resets():
    engine, clock = detector(
        binding("hold", ["q"], id="t1", hold_ms=800),
        binding("hold", ["q"], id="t2", hold_ms=2000),
    )
    engine.handle("q", True, clock.now)
    assert fires(engine.tick(clock.advance(799))) == []
    first = fires(engine.tick(clock.advance(1)))
    assert [effect.binding_id for effect in first] == ["t1"]
    assert fires(engine.tick(clock.advance(100))) == []
    second = fires(engine.tick(clock.advance(1100)))
    assert [effect.binding_id for effect in second] == ["t2"]

    engine.handle("q", False, clock.now)
    engine.handle("q", True, clock.now)
    again = fires(engine.tick(clock.advance(800)))
    assert [effect.binding_id for effect in again] == ["t1"]


def test_hold_tiers_play_600_then_850():
    engine, clock = detector(
        binding("hold", ["q"], id="t1", hold_ms=800),
        binding("hold", ["q"], id="t2", hold_ms=2000),
    )
    engine.handle("q", True, 0)
    effects = engine.tick(2.0)
    tones = [effect.hz for effect in effects if effect.kind == "audio"]
    assert tones == [600, 850]


def test_audio_can_be_disabled():
    engine, _clock = detector(binding("hold", ["q"], hold_ms=800), audio=False)
    engine.handle("q", True, 0)
    assert [effect for effect in engine.tick(1) if effect.kind == "audio"] == []


def test_hold_only_key_is_replayed_when_released_early_and_not_after_firing():
    engine, clock = detector(binding("hold", ["p"], hold_ms=2000))
    engine.handle("p", True, clock.now)
    early = engine.handle("p", False, clock.advance(120))
    assert early.suppress is True
    assert [effect.key for effect in early.effects if effect.kind == "inject"] == ["p"]

    engine.handle("p", True, clock.now)
    engine.tick(clock.advance(2000))
    late = engine.handle("p", False, clock.now)
    assert [effect for effect in late.effects if effect.kind == "inject"] == []


def test_key_repeat_does_not_restart_the_hold():
    engine, clock = detector(binding("hold", ["p"], id="hold", hold_ms=800))
    engine.handle("p", True, clock.now)
    repeat = engine.handle("p", True, clock.advance(700))
    assert repeat.suppress is True
    assert fires(repeat.effects) == []
    assert [effect.binding_id for effect in fires(engine.tick(clock.advance(100)))] == ["hold"]


def test_two_keys_are_independent():
    engine, clock = detector(
        binding("hold", ["a"], id="a", hold_ms=800),
        binding("hold", ["b"], id="b", hold_ms=800),
    )
    engine.handle("a", True, clock.now)
    engine.handle("b", True, clock.advance(400))
    fired = [effect.binding_id for effect in fires(engine.tick(clock.advance(400)))]
    assert fired == ["a"]
    fired = [effect.binding_id for effect in fires(engine.tick(clock.advance(400)))]
    assert fired == ["b"]


def test_tap_dance_single_double_and_triple():
    def tap(engine, clock, gap_ms):
        engine.handle("f", True, clock.now if gap_ms == 0 else clock.advance(gap_ms))
        return engine.handle("f", False, clock.advance(40))

    single, clock = detector(
        binding("tap", ["f"], id="one"),
        binding("tap_dance", ["f"], id="two", taps=2),
    )
    assert fires(tap(single, clock, 0)) == []
    assert [effect.binding_id for effect in fires(single.tick(clock.advance(250)))] == ["one"]
    tap(single, clock, 0)
    assert [effect.binding_id for effect in fires(tap(single, clock, 60))] == ["two"]

    triple, clock = detector(binding("tap_dance", ["f"], id="three", taps=3))
    tap(triple, clock, 0)
    tap(triple, clock, 50)
    assert [effect.binding_id for effect in fires(tap(triple, clock, 50))] == ["three"]


def test_tap_dance_timeout_and_unmapped_count():
    engine, clock = detector(binding("tap_dance", ["f"], id="three", taps=3))
    engine.handle("f", True, clock.now)
    engine.handle("f", False, clock.advance(40))
    engine.handle("f", True, clock.advance(40))
    engine.handle("f", False, clock.advance(40))
    effects = engine.tick(clock.advance(250))
    assert fires(effects) == []
    assert any("No action for 2 taps" in effect.message for effect in effects)

    engine.handle("f", True, clock.now)
    released = engine.handle("f", False, clock.advance(30))
    assert fires(released) == []
    committed = engine.tick(clock.advance(250))
    assert [effect.key for effect in committed if effect.kind == "inject"] == ["f"]


def test_different_key_commits_a_pending_tap():
    engine, clock = detector(binding("tap_dance", ["f"], taps=2))
    engine.handle("f", True, clock.now)
    engine.handle("f", False, clock.advance(40))
    pressed = engine.handle("z", True, clock.advance(40))
    # z is replayed after f so the typed f cannot land after z.
    assert pressed.suppress is True
    assert [effect.key for effect in pressed.effects if effect.kind == "inject"] == ["f"]
    assert [effect.key for effect in pressed.effects if effect.kind == "inject_down"] == ["z"]


def test_leader_window_and_timeout():
    engine, clock = detector(binding("leader", ["y"], id="yt"), leader_key="f13")
    engine.handle("f13", True, clock.now)
    opened = engine.handle("f13", False, clock.advance(80))
    assert opened.suppress is True
    assert any("Leader window open" in effect.message for effect in opened.effects)
    fired = engine.handle("y", True, clock.advance(200))
    assert fired.suppress is True
    assert [effect.binding_id for effect in fires(fired.effects)] == ["yt"]

    engine.handle("f13", True, clock.now)
    engine.handle("f13", False, clock.advance(40))
    missed = engine.handle("z", True, clock.advance(1500))
    assert missed.suppress is False
    assert fires(missed.effects) == []


def test_leader_ignores_a_long_hold_and_unbound_follow_up_passes():
    engine, clock = detector(binding("leader", ["y"]), leader_key="caps_lock")
    engine.handle("caps_lock", True, clock.now)
    held = engine.handle("caps_lock", False, clock.advance(400))
    assert any("not opened" in effect.message for effect in held.effects)
    assert engine.handle("y", True, clock.now).suppress is False

    engine.handle("caps_lock", True, clock.now)
    engine.handle("caps_lock", False, clock.advance(40))
    follow = engine.handle("z", True, clock.advance(20))
    assert follow.suppress is False
    assert any("closed" in effect.message for effect in follow.effects)


def test_panic_clears_state_and_later_keys_pass():
    engine, clock = detector(binding("hold", ["p"], hold_ms=800))
    engine.handle("p", True, clock.now)
    for key in ("ctrl", "alt", "shift"):
        assert engine.handle(key, True, clock.now).suppress is False
    panic = engine.handle("esc", True, clock.now)
    assert panic.suppress is True
    assert any(effect.kind == "panic" for effect in panic.effects)
    assert engine.enabled is False
    assert engine.tick(clock.advance(5000)) == []
    assert engine.handle("p", True, clock.now).suppress is False


def test_panic_also_completes_if_escape_is_already_down():
    engine, clock = detector()
    engine.handle("esc", True, clock.now)
    engine.handle("ctrl", True, clock.now)
    engine.handle("alt", True, clock.now)
    panic = engine.handle("shift", True, clock.now)
    assert any(effect.kind == "panic" for effect in panic.effects)
    assert panic.suppress is False


def test_disabled_detector_never_suppresses():
    engine, clock = detector(binding("tap", ["a"]))
    engine.set_enabled(False)
    assert engine.handle("a", True, clock.now).suppress is False
    assert engine.handle("a", False, clock.now).suppress is False


def test_disabled_binding_is_ignored():
    engine, clock = detector(binding("tap", ["a"], enabled=False))
    assert engine.handle("a", True, clock.now).suppress is False


def test_app_profile_overrides_global_and_falls_back():
    shared = binding("chord", ["ctrl", "r"], Action(type="open_url", url="https://global.example"), id="global")
    override = binding("chord", ["ctrl", "r"], Action(type="open_url", url="https://mine.example"), id="mine")
    q = binding("hold", ["q"], id="q", hold_ms=800)
    engine, clock = detector(shared, gaming=(override, q))
    config = engine.config

    engine.handle("ctrl", True, clock.now)
    pressed = engine.handle("r", True, clock.now)
    assert [effect.binding_id for effect in fires(pressed.effects)] == ["global"]
    engine.handle("r", False, clock.now)
    engine.handle("ctrl", False, clock.now)

    effects = engine.set_profiles(config.global_profile(), config.profile_by_id("gaming"))
    assert any("Gaming" in effect.message for effect in effects)
    engine.handle("ctrl", True, clock.now)
    pressed = engine.handle("r", True, clock.now)
    assert [effect.action.url for effect in fires(pressed.effects)] == ["https://mine.example"]
    engine.handle("r", False, clock.now)
    engine.handle("ctrl", False, clock.now)

    engine.handle("q", True, clock.now)
    assert [effect.binding_id for effect in fires(engine.tick(clock.advance(800)))] == ["q"]

    engine.set_profiles(config.global_profile(), config.global_profile())
    engine.handle("q", False, clock.now)
    assert engine.handle("q", True, clock.now).suppress is False


def test_leaving_a_profile_cancels_an_in_progress_hold():
    q = binding("hold", ["q"], id="q", hold_ms=800)
    engine, clock = detector(gaming=(q,))
    config = engine.config
    engine.set_profiles(config.global_profile(), config.profile_by_id("gaming"))
    engine.handle("q", True, clock.now)
    engine.set_profiles(config.global_profile(), config.global_profile())
    assert fires(engine.tick(clock.advance(800))) == []


def test_capture_records_a_chord_and_escape_cancels():
    engine, clock = detector()
    engine.begin_capture()
    for key in ("ctrl", "win", "shift"):
        assert engine.handle(key, True, clock.now).suppress is False
    captured = engine.handle("r", True, clock.now)
    assert captured.suppress is True
    assert captured.effects[0].kind == "capture"
    assert captured.effects[0].keys == ("ctrl", "win", "shift", "r")
    assert engine.capture is False

    engine.begin_capture()
    cancelled = engine.handle("esc", True, clock.now)
    assert cancelled.effects[0].kind == "capture_cancel"
    assert engine.capture is False


def test_unknown_key_is_ignored():
    engine, clock = detector(binding("tap", ["a"]))
    assert engine.handle("not-a-key", True, clock.now).suppress is False


def test_macro_binding_is_only_selected_not_run_by_the_detector():
    action = Action(
        type="macro",
        steps=[
            MacroStep(op="key", key="1"),
            MacroStep(op="delay", ms=50),
            MacroStep(op="mouse", button="right"),
        ],
    )
    engine, clock = detector(binding("tap", ["v"], action, id="macro"))
    engine.handle("v", True, clock.now)
    released = engine.handle("v", False, clock.advance(40))
    assert fires(released.effects)[0].action.steps[1].ms == 50

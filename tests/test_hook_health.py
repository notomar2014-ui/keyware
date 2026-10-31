"""Silent hook removal: Windows can drop a slow low-level hook without telling us."""

from engine.hook_health import SilentHookDetector
from tests.fakes import service


def feed(det, samples):
    fired = []
    for now, events, last_input, keys in samples:
        fired.append(det.sample(now, events, last_input, frozenset(keys)))
    return fired


def test_hook_that_sees_events_is_healthy():
    det = SilentHookDetector()
    samples = [(t * 0.25, t, 1000 + t, {"a"} if t % 2 else set()) for t in range(40)]
    assert not any(feed(det, samples))


def test_two_missed_key_changes_mean_the_hook_is_gone():
    det = SilentHookDetector()
    fired = feed(det, [(0.0, 5, 100, set()), (0.25, 5, 120, {65}), (0.5, 5, 140, set())])
    assert fired == [False, False, True]


def test_one_miss_is_not_enough():
    det = SilentHookDetector()
    assert not any(feed(det, [(0.0, 5, 100, set()), (0.25, 5, 120, {65}), (0.5, 6, 140, {65})]))


def test_misses_far_apart_do_not_add_up():
    det = SilentHookDetector(window=5.0)
    fired = feed(
        det,
        [(0.0, 5, 100, set()), (0.25, 5, 120, {65}), (0.5, 6, 121, {65}), (9.0, 6, 300, set())],
    )
    assert fired == [False, False, False, False]


def test_a_key_reported_stuck_with_mouse_movement_never_fires():
    det = SilentHookDetector()
    samples = [(t * 0.25, 5, 100 + t * 10, {16}) for t in range(60)]
    assert not any(feed(det, samples))


def test_no_new_input_no_miss():
    det = SilentHookDetector()
    assert not any(feed(det, [(0.0, 5, 100, set()), (0.25, 5, 100, {65}), (0.5, 5, 100, set())]))


def test_cooldown_after_a_reinstall():
    det = SilentHookDetector(cooldown=10.0)
    seq = [(0.0, 5, 100, set()), (0.25, 5, 120, {65}), (0.5, 5, 140, set())]
    assert feed(det, seq)[-1] is True
    later = [(0.75, 5, 160, {65}), (1.0, 5, 180, set()), (1.25, 5, 200, {65})]
    assert not any(feed(det, later))


class Probe:
    def __init__(self):
        self.tick = 100
        self.keys: frozenset = frozenset()

    def last_input(self):
        return self.tick

    def keys_down(self):
        return self.keys


def make(tmp_path):
    created, _o, _i, _a, hook = service(tmp_path)
    hook.events_seen = 0
    probe = Probe()
    created._input_probe = probe
    created.set_enabled(True)
    return created, hook, probe


def quiet_typing(created, probe, clock_start=0.0):
    results = []
    for step, keys in enumerate([set(), {65}, set(), {66}]):
        probe.tick += 20
        probe.keys = frozenset(keys)
        results.append(created.check_hook_health(clock_start + step * 0.25))
    return results


def test_service_reinstalls_a_silently_removed_hook_and_logs_it(tmp_path):
    created, hook, probe = make(tmp_path)
    starts, stops = hook.starts, hook.stops
    assert "reinstall" in quiet_typing(created, probe)
    assert hook.starts == starts + 1 and hook.stops == stops + 1
    assert created.hook_status == "on"
    log = (tmp_path / "keyware.log").read_text(encoding="utf-8")
    assert "went quiet" in log


def test_service_leaves_a_working_hook_alone(tmp_path):
    created, hook, probe = make(tmp_path)
    starts = hook.starts
    for step in range(12):
        hook.events_seen += 1
        probe.tick += 20
        probe.keys = frozenset({65} if step % 2 else ())
        assert created.check_hook_health(step * 0.25) == "none"
    assert hook.starts == starts


def test_no_check_while_off_or_elevated(tmp_path):
    created, hook, probe = make(tmp_path)
    created._elevated = True
    assert set(quiet_typing(created, probe)) == {"none"}
    created._elevated = False
    created.set_enabled(False)
    assert set(quiet_typing(created, probe, 10.0)) == {"none"}


def test_hooks_without_a_counter_are_skipped(tmp_path):
    created, hook, probe = make(tmp_path)
    del hook.events_seen
    assert set(quiet_typing(created, probe)) == {"none"}


def test_hook_engine_counts_every_event():
    from engine.hook_engine import HookEngine

    engine = HookEngine(lambda _name, _pressed: False)

    class Data:
        vkCode = 65
        flags = 0
        dwExtraInfo = 0

    engine._filter(0x0100, Data())
    engine._filter(0x0101, Data())
    assert engine.events_seen == 2

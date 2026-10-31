"""One trigger, one action per profile."""

from __future__ import annotations

from engine.conflicts import dedupe_profile, find_conflicts
from engine.models import Action, Binding


def b(mode, keys, ident, **kw):
    return Binding(id=ident, enabled=kw.pop("enabled", True), mode=mode, keys=list(keys),
                   action=Action(type="type_text", text=ident or "x"), label=kw.pop("label", ""), **kw)


def ids(conflicts):
    return [c.binding.id for c in conflicts]


def test_identical_chords_conflict_even_with_different_actions():
    existing = [b("chord", ["ctrl", "shift", "r"], "a")]
    found = find_conflicts(b("chord", ["shift", "ctrl", "r"], "new"), existing)
    assert ids(found) == ["a"]
    assert "Ctrl + Shift + R" in found[0].reason


def test_different_chords_do_not_conflict():
    existing = [b("chord", ["ctrl", "shift", "r"], "a"), b("chord", ["ctrl", "r"], "b")]
    assert find_conflicts(b("chord", ["ctrl", "alt", "r"], "new"), existing) == []


def test_one_bare_key_cannot_be_a_tap_a_hold_and_a_double_tap():
    existing = [b("hold", ["p"], "hold", hold_ms=800), b("tap", ["q"], "q")]
    assert ids(find_conflicts(b("tap", ["p"], "new"), existing)) == ["hold"]
    assert ids(find_conflicts(b("tap_dance", ["p"], "new", taps=2), existing)) == ["hold"]
    assert ids(find_conflicts(b("hold", ["p"], "new", hold_ms=800), existing)) == ["hold"]


def test_hold_tiers_on_one_key_are_allowed_but_not_mixed_with_a_tap():
    # Hold Q 800 ms and Hold Q 2000 ms are two triggers (the Gaming sample uses this).
    existing = [b("hold", ["q"], "short", hold_ms=800), b("hold", ["q"], "long", hold_ms=2000)]
    assert find_conflicts(b("hold", ["q"], "new", hold_ms=1200), existing) == []
    assert ids(find_conflicts(b("tap", ["q"], "new"), existing)) == ["short", "long"]


def test_a_hold_on_r_does_not_clash_with_a_chord_ending_in_r():
    existing = [b("chord", ["ctrl", "win", "shift", "r"], "chord")]
    assert find_conflicts(b("hold", ["r"], "new"), existing) == []


def test_two_leader_shortcuts_on_the_same_follow_key_conflict():
    existing = [b("leader", ["y"], "a"), b("leader", ["z"], "b")]
    assert ids(find_conflicts(b("leader", ["y"], "new"), existing)) == ["a"]
    # A leader follow-up Y and a bare tap on Y are different triggers.
    assert find_conflicts(b("tap", ["y"], "new"), existing) == []


def test_editing_a_binding_does_not_conflict_with_itself_and_disabled_ones_are_ignored():
    existing = [b("chord", ["ctrl", "f20"], "a"), b("chord", ["ctrl", "f21"], "off", enabled=False)]
    assert find_conflicts(b("chord", ["ctrl", "f20"], "a"), existing) == []
    assert find_conflicts(b("chord", ["ctrl", "f21"], "new"), existing) == []
    # Saving a disabled binding is allowed; enabling it is checked again.
    assert find_conflicts(b("chord", ["ctrl", "f20"], "new", enabled=False), existing) == []


def test_conflict_reason_names_the_other_binding_by_label():
    existing = [b("chord", ["ctrl", "f20"], "a", label="Open YouTube")]
    found = find_conflicts(b("chord", ["ctrl", "f20"], "new"), existing)
    assert "Open YouTube" in found[0].reason


def test_dedupe_disables_later_duplicates_and_keeps_the_first():
    first = b("chord", ["ctrl", "f20"], "first")
    dup = b("chord", ["ctrl", "f20"], "dup")
    hold = b("hold", ["p"], "hold")
    tap = b("tap", ["p"], "tap")
    other = b("chord", ["ctrl", "f21"], "other")
    messages = dedupe_profile([first, dup, hold, tap, other])
    assert [x.enabled for x in (first, dup, hold, tap, other)] == [True, False, True, False, True]
    assert len(messages) == 2 and all("Disabled" in m for m in messages)


def test_dedupe_leaves_a_clean_profile_alone():
    items = [b("chord", ["ctrl", "f20"], "a"), b("hold", ["p"], "b"), b("leader", ["y"], "c")]
    assert dedupe_profile(items) == []
    assert all(x.enabled for x in items)


# --- service and config integration -----------------------------------------
import json

import pytest

from engine.conflicts import ConflictError
from tests.fakes import service


def _global(created):
    return created.config.global_profile().id


def test_service_blocks_a_conflicting_binding_and_can_replace_it(tmp_path):
    created, *_ = service(tmp_path)
    gid = _global(created)
    created.add_binding(gid, b("chord", ["ctrl", "f20"], "", label="First"))
    with pytest.raises(ConflictError) as info:
        created.add_binding(gid, b("chord", ["ctrl", "f20"], "", label="Second"))
    assert "First" in str(info.value)
    assert [x.label for x in created.bindings(gid) if x.keys == ["ctrl", "f20"]] == ["First"]
    created.add_binding(gid, b("chord", ["ctrl", "f20"], "", label="Second"), replace=True)
    assert [x.label for x in created.bindings(gid) if x.keys == ["ctrl", "f20"]] == ["Second"]


def test_editing_into_a_conflict_is_blocked_and_enabling_a_clash_is_blocked(tmp_path):
    created, *_ = service(tmp_path)
    gid = _global(created)
    created.add_binding(gid, b("hold", ["f13"], "", label="Hold", hold_ms=800))
    created.add_binding(gid, b("tap", ["f14"], "", label="Tap"))
    tap = next(x for x in created.bindings(gid) if x.label == "Tap")
    edited = b("tap", ["f13"], tap.id, label="Tap")
    with pytest.raises(ConflictError):
        created.update_binding(gid, edited)
    created.add_binding(gid, b("tap_dance", ["f13"], "", label="Dance", taps=2, enabled=False))
    dance = next(x for x in created.bindings(gid) if x.label == "Dance")
    with pytest.raises(ConflictError):
        created.set_binding_enabled(gid, dance.id, True)
    assert next(x for x in created.bindings(gid) if x.label == "Dance").enabled is False


def test_duplicates_in_the_config_file_are_disabled_on_load_and_logged(tmp_path):
    created, *_ = service(tmp_path)
    gid = _global(created)
    created.add_binding(gid, b("chord", ["ctrl", "f20"], "", label="Keep"))
    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    glob = next(p for p in raw["profiles"] if p["id"] == gid)
    dup = dict(next(x for x in glob["bindings"] if x["label"] == "Keep"))
    dup.update(id="bind-dup", label="Dup")
    glob["bindings"].append(dup)
    (tmp_path / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    again, *_ = service(tmp_path)
    labels = {x.label: x.enabled for x in again.bindings(gid)}
    assert labels["Keep"] is True and labels["Dup"] is False
    assert any("Disabled" in line and "Dup" in line for line in (tmp_path / "keyware.log").read_text(encoding="utf-8").splitlines())
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert next(x for p in saved["profiles"] for x in p["bindings"] if x["id"] == "bind-dup")["enabled"] is False


# --- App profiles overriding Global: allowed, but say so (non-blocking) ---

from engine.conflicts import find_overrides  # noqa: E402


def test_find_overrides_names_the_global_binding():
    globals_ = [b("chord", ["ctrl", "f20"], "g1", label="Paste name"), b("tap", ["f21"], "g2")]
    notes = find_overrides(b("chord", ["f20", "ctrl"], "a1"), globals_, None, "Gaming")
    assert len(notes) == 1
    assert "Paste name" in notes[0] and "Global" in notes[0] and "Gaming" in notes[0]
    assert find_overrides(b("chord", ["ctrl", "f22"], "a1"), globals_, None, "Gaming") == []


def test_service_saves_an_override_and_returns_a_note(tmp_path):
    created, *_ = service(tmp_path)
    created.add_binding(_global(created), b("chord", ["ctrl", "f20"], "", label="Everywhere"))
    app = created.add_profile("Game", ["game.exe"])
    notes = created.add_binding(app.id, b("chord", ["ctrl", "f20"], "", label="In game"))
    assert notes and "Everywhere" in notes[0]
    assert [x.label for x in created.bindings(app.id)] == ["In game"]
    assert "Everywhere" in (tmp_path / "keyware.log").read_text(encoding="utf-8")
    assert created.add_binding(_global(created), b("chord", ["ctrl", "f23"], "", label="G")) == []
    edited = created.bindings(app.id)[0]
    assert created.update_binding(app.id, edited)

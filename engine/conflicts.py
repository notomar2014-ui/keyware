"""One trigger, one action per profile.

Two enabled bindings in the same profile conflict when the same key press
would have to pick between them:
- chords with the same key set (Ctrl + Shift + R twice),
- leader shortcuts with the same follow-up key,
- bare-key triggers (tap, hold, double/triple-tap) on the same key.
  Holds of different lengths on one key are tiers and stay allowed.

A chord always has a modifier, so a hold on R and Ctrl + R are different
triggers. App profiles may still override Global on purpose; this check is
per profile.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.keys import format_key, format_keys, ordered_keys
from engine.models import Binding, trigger_text

BARE_MODES = ("tap", "hold", "tap_dance")


@dataclass
class Conflict:
    binding: Binding
    reason: str


class ConflictError(ValueError):
    def __init__(self, conflicts: list[Conflict]):
        self.conflicts = conflicts
        super().__init__(conflicts[0].reason if conflicts else "Trigger conflict.")


def trigger_slot(binding: Binding) -> tuple:
    if binding.mode == "chord":
        return ("chord", tuple(sorted(ordered_keys(binding.keys))))
    if binding.mode == "leader":
        return ("leader", binding.keys[0] if binding.keys else "")
    if binding.mode in BARE_MODES:
        return ("key", binding.keys[0] if binding.keys else "")
    return (binding.mode, tuple(sorted(binding.keys)))


def _hold_tiers(a: Binding, b: Binding) -> bool:
    """Holds on one key with different lengths are separate tiers, not a clash."""
    if a.mode != "hold" or b.mode != "hold" or int(a.hold_ms) == int(b.hold_ms):
        return False
    # A tap / hold binding already decides what every press of the key does.
    return not (a.is_tap_hold() or b.is_tap_hold())


def _name(binding: Binding, leader_key: str | None) -> str:
    text = trigger_text(binding, leader_key)
    label = binding.label or ""
    if not label:
        from engine.models import action_type_label

        label = action_type_label(binding.action.type)
    return f"“{label}” ({text})"


def _what(binding: Binding, leader_key: str | None) -> str:
    if binding.mode == "chord":
        return format_keys(binding.keys)
    if binding.mode == "leader":
        lead = format_key(leader_key) if leader_key else "Leader"
        return f"{lead} → {format_key(binding.keys[0])}"
    return format_key(binding.keys[0])


def find_conflicts(candidate: Binding, existing: list[Binding], leader_key: str | None = None) -> list[Conflict]:
    if not candidate.enabled:
        return []
    slot = trigger_slot(candidate)
    found: list[Conflict] = []
    for item in existing:
        if item.id == candidate.id or not item.enabled:
            continue
        a, b = int(getattr(candidate, "setup", 0) or 0), int(getattr(item, "setup", 0) or 0)
        if a and b and a != b:
            continue  # different setups are never active together
        if trigger_slot(item) == slot and not _hold_tiers(candidate, item):
            reason = f"{_what(candidate, leader_key)} is already used by {_name(item, leader_key)}. One trigger can only do one thing."
            found.append(Conflict(binding=item, reason=reason))
    return found


def dedupe_profile(bindings: list[Binding], leader_key: str | None = None, profile_name: str = "") -> list[str]:
    """Disable later bindings that clash with an earlier one. Returns log lines."""
    kept: list[Binding] = []
    messages: list[str] = []
    where = f" in {profile_name}" if profile_name else ""
    for item in bindings:
        if not item.enabled:
            continue
        clash = find_conflicts(item, kept, leader_key)
        if clash:
            item.enabled = False
            messages.append(
                f"Disabled {_name(item, leader_key)}{where}: same trigger as {_name(clash[0].binding, leader_key)}."
            )
            continue
        kept.append(item)
    return messages


def find_overrides(
    candidate: Binding, global_bindings: list[Binding], leader_key: str | None = None, profile_name: str = ""
) -> list[str]:
    """Global bindings that an app-profile binding replaces while that app is in front.

    This is allowed on purpose. The notes are information only, never a block.
    """
    if not candidate.enabled:
        return []
    where = profile_name or "this app"
    notes = []
    for item in find_conflicts(candidate, global_bindings, leader_key):
        notes.append(
            f"Note: while {where} is in front, this replaces Global {_name(item.binding, leader_key)}."
        )
    return notes

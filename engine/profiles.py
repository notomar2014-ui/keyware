"""Match the foreground process to a profile. Global is the fallback."""

from __future__ import annotations

from engine.models import Profile


def process_basename(name: str) -> str:
    """File name from a Windows or POSIX path. os.path.basename is not enough on Linux."""
    text = str(name or "").strip().replace("\\", "/")
    return text.rsplit("/", 1)[-1]


def normalize_process(name: str) -> str:
    text = process_basename(name).strip().lower()
    if text.endswith(".exe"):
        text = text[: -len(".exe")]
    return text


def same_process(expected: str, actual: str) -> bool:
    left = normalize_process(expected)
    right = normalize_process(actual)
    return bool(left) and left == right


def match_profile(profiles: list[Profile], process_name: str) -> Profile:
    """Return the first enabled app profile whose process list matches.

    Profiles earlier in the list win when more than one matches. Anything else,
    including an empty foreground name, uses Global.
    """
    global_profile = next((profile for profile in profiles if profile.is_global), None)
    if global_profile is None:
        raise ValueError("Config is missing the Global profile.")
    actual = normalize_process(process_name)
    if not actual:
        return global_profile
    for profile in profiles:
        if profile.is_global or not profile.enabled:
            continue
        if any(same_process(candidate, actual) for candidate in profile.process_names):
            return profile
    return global_profile

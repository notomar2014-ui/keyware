"""Canonical key names, aliases, and Windows virtual-key translation.

The detector and the tests speak only in these names. Platform hooks convert
to and from them at the edge so the engine never imports pynput or pywin32.
"""

from __future__ import annotations

MODIFIERS = frozenset({"ctrl", "alt", "shift", "win"})

# Preferred injection VK when several hardware keys share one name.
_LETTERS = {chr(code): 0x41 + (code - ord("a")) for code in range(ord("a"), ord("z") + 1)}
_DIGITS = {str(n): 0x30 + n for n in range(10)}

NAME_TO_VK: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "shift": 0xA0,
    "ctrl": 0xA2,
    "alt": 0xA4,
    "caps_lock": 0x14,
    "esc": 0x1B,
    "space": 0x20,
    "page_up": 0x21,
    "page_down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "print_screen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "win": 0x5B,
    "menu": 0x5D,
    **_DIGITS,
    **_LETTERS,
    "numpad0": 0x60,
    "numpad1": 0x61,
    "numpad2": 0x62,
    "numpad3": 0x63,
    "numpad4": 0x64,
    "numpad5": 0x65,
    "numpad6": 0x66,
    "numpad7": 0x67,
    "numpad8": 0x68,
    "numpad9": 0x69,
    "numpad_multiply": 0x6A,
    "numpad_add": 0x6B,
    "numpad_subtract": 0x6D,
    "numpad_decimal": 0x6E,
    "numpad_divide": 0x6F,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "f13": 0x7C,
    "f14": 0x7D,
    "f15": 0x7E,
    "f16": 0x7F,
    "f17": 0x80,
    "f18": 0x81,
    "f19": 0x82,
    "f20": 0x83,
    "f21": 0x84,
    "f22": 0x85,
    "f23": 0x86,
    "f24": 0x87,
    "semicolon": 0xBA,
    "equals": 0xBB,
    "comma": 0xBC,
    "minus": 0xBD,
    "period": 0xBE,
    "slash": 0xBF,
    "backtick": 0xC0,
    "bracket_left": 0xDB,
    "backslash": 0xDC,
    "bracket_right": 0xDD,
    "quote": 0xDE,
    # Media and volume keys (v1.3.3). They act on the whole system, never on the default device choice.
    "volume_mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
    "media_next": 0xB0,
    "media_prev": 0xB1,
    "media_stop": 0xB2,
    "media_play_pause": 0xB3,
}

# Hardware VKs that collapse to the same canonical name.
_VK_ALIASES = {
    0x10: "shift",
    0x11: "ctrl",
    0x12: "alt",
    0xA0: "shift",
    0xA1: "shift",
    0xA2: "ctrl",
    0xA3: "ctrl",
    0xA4: "alt",
    0xA5: "alt",
    0x5B: "win",
    0x5C: "win",
    0x5D: "menu",
}

VK_TO_NAME: dict[int, str] = {vk: name for name, vk in NAME_TO_VK.items()}
VK_TO_NAME.update(_VK_ALIASES)

# Trigger-only keys. They have no single virtual-key code, so Keyware can
# record and bind them but never sends them.
# "copilot": the Copilot key on Windows 11 keyboards. The hardware sends
# Left Win + Left Shift + F23 (scan code 0x6E) in about a millisecond and
# releases F23, Left Shift, Left Win. engine/copilot.py folds that into one key.
TRIGGER_ONLY_KEYS = frozenset({"copilot"})
COPILOT_PARTS = ("win", "shift", "f23")

ALLOWED_KEYS = frozenset(NAME_TO_VK) | TRIGGER_ONLY_KEYS
SENDABLE_KEYS = frozenset(NAME_TO_VK)

_PRETTY = {
    "copilot": "Copilot key",
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win",
    "esc": "Esc",
    "caps_lock": "Caps Lock",
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "backspace": "Backspace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "page_up": "Page Up",
    "page_down": "Page Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "print_screen": "Print Screen",
    "menu": "Menu",
    "semicolon": ";",
    "equals": "=",
    "comma": ",",
    "minus": "-",
    "period": ".",
    "slash": "/",
    "backtick": "`",
    "bracket_left": "[",
    "backslash": "\\",
    "bracket_right": "]",
    "quote": "'",
    "numpad_add": "Numpad +",
    "numpad_subtract": "Numpad -",
    "numpad_multiply": "Numpad *",
    "numpad_divide": "Numpad /",
    "numpad_decimal": "Numpad .",
    "volume_mute": "Mute",
    "volume_down": "Volume Down",
    "volume_up": "Volume Up",
    "media_next": "Next Track",
    "media_prev": "Previous Track",
    "media_stop": "Stop Media",
    "media_play_pause": "Play/Pause",
}

_ALIASES = {
    "copilot": "copilot",
    "copilot key": "copilot",
    "co-pilot": "copilot",
    "co-pilot key": "copilot",
    "copilot button": "copilot",
    "win shift f23": "copilot",
    "mute": "volume_mute",
    "vol up": "volume_up",
    "vol down": "volume_down",
    "play pause": "media_play_pause",
    "play/pause": "media_play_pause",
    "playpause": "media_play_pause",
    "next track": "media_next",
    "previous track": "media_prev",
    "prev track": "media_prev",
    "control": "ctrl",
    "ctl": "ctrl",
    "windows": "win",
    "window": "win",
    "cmd": "win",
    "super": "win",
    "meta": "win",
    "option": "alt",
    "escape": "esc",
    "return": "enter",
    "ret": "enter",
    "spacebar": "space",
    "caps": "caps_lock",
    "capslock": "caps_lock",
    "caps lock": "caps_lock",
    "pgup": "page_up",
    "pageup": "page_up",
    "page up": "page_up",
    "pgdn": "page_down",
    "pagedown": "page_down",
    "page down": "page_down",
    "del": "delete",
    "ins": "insert",
    "plus": "equals",
    "equal": "equals",
    "=": "equals",
    "-": "minus",
    ",": "comma",
    ".": "period",
    "/": "slash",
    ";": "semicolon",
    "'": "quote",
    "[": "bracket_left",
    "]": "bracket_right",
    "\\": "backslash",
    "`": "backtick",
    "printscreen": "print_screen",
    "print screen": "print_screen",
    "prtsc": "print_screen",
    "apps": "menu",
    "back": "backspace",
    **{f"numpad {index}": f"numpad{index}" for index in range(10)},
    **{f"num {index}": f"numpad{index}" for index in range(10)},
}


def canonicalize(name: str | None) -> str | None:
    """Return the canonical key name, or None if it is not a known key."""
    if name is None:
        return None
    text = str(name).strip().lower().replace("_", " ")
    text = " ".join(text.split())
    if text in _ALIASES:
        return _ALIASES[text]
    compact = text.replace(" ", "_")
    if compact in _ALIASES:
        return _ALIASES[compact]
    if compact in ALLOWED_KEYS:
        return compact
    if text in ALLOWED_KEYS:
        return text
    if len(text) == 1 and text in ALLOWED_KEYS:
        return text
    pretty_inverse = {value.lower(): key for key, value in _PRETTY.items()}
    if text in pretty_inverse:
        return pretty_inverse[text]
    return None


def vk_to_name(vk_code: int) -> str | None:
    return VK_TO_NAME.get(int(vk_code))


def format_key(name: str) -> str:
    if name in _PRETTY:
        return _PRETTY[name]
    if name.startswith("numpad") and name[6:].isdigit():
        return f"Numpad {name[6:]}"
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    if len(name) == 1:
        return name.upper()
    return name.replace("_", " ").title()


def ordered_keys(keys: list[str] | tuple[str, ...]) -> list[str]:
    present = list(keys)
    mods = [name for name in ("ctrl", "win", "alt", "shift") if name in present]
    rest = [name for name in present if name not in MODIFIERS]
    return mods + rest


def format_keys(keys: list[str] | tuple[str, ...]) -> str:
    return " + ".join(format_key(name) for name in ordered_keys(keys))


def parse_key_expression(expression: str) -> list[str]:
    """Parse 'Ctrl + Win + Shift + R' or 'shift+1' into canonical keys."""
    raw = expression.strip()
    if not raw:
        raise ValueError("Enter a key or chord.")
    parts = [part.strip() for part in raw.split("+")]
    parts = [part for part in parts if part]
    if not parts:
        raise ValueError("Enter a key or chord.")
    keys: list[str] = []
    for part in parts:
        name = canonicalize(part)
        if name is None:
            raise ValueError(f"Unknown key '{part}'.")
        if name not in keys:
            keys.append(name)
    return ordered_keys(keys)


def non_modifiers(keys: list[str] | tuple[str, ...]) -> list[str]:
    return [name for name in keys if name not in MODIFIERS]


def modifiers_in(keys: list[str] | tuple[str, ...]) -> list[str]:
    return [name for name in keys if name in MODIFIERS]

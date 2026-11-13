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

# Every other virtual-key code Windows defines (winuser.h), so any key can be a trigger.
NAME_TO_VK.update({
    "cancel": 0x03, "clear": 0x0C, "pause": 0x13, "kana": 0x15, "ime_on": 0x16, "junja": 0x17,
    "ime_final": 0x18, "kanji": 0x19, "ime_off": 0x1A, "convert": 0x1C, "nonconvert": 0x1D,
    "accept": 0x1E, "modechange": 0x1F, "select": 0x29, "print": 0x2A, "execute": 0x2B,
    "help": 0x2F, "sleep": 0x5F, "numpad_separator": 0x6C, "num_lock": 0x90, "scroll_lock": 0x91,
    "oem_fj_jisho": 0x92, "oem_fj_masshou": 0x93, "oem_fj_touroku": 0x94, "oem_fj_loya": 0x95,
    "oem_fj_roya": 0x96,
    "browser_back": 0xA6, "browser_forward": 0xA7, "browser_refresh": 0xA8, "browser_stop": 0xA9,
    "browser_search": 0xAA, "browser_favorites": 0xAB, "browser_home": 0xAC,
    "launch_mail": 0xB4, "launch_media_select": 0xB5, "launch_app1": 0xB6, "launch_app2": 0xB7,
    "oem_8": 0xDF, "oem_ax": 0xE1, "oem_102": 0xE2, "ico_help": 0xE3, "ico_00": 0xE4,
    "processkey": 0xE5, "ico_clear": 0xE6, "packet": 0xE7, "oem_reset": 0xE9, "oem_jump": 0xEA,
    "oem_pa1": 0xEB, "oem_pa2": 0xEC, "oem_pa3": 0xED, "oem_wsctrl": 0xEE, "oem_cusel": 0xEF,
    "oem_attn": 0xF0, "oem_finish": 0xF1, "oem_copy": 0xF2, "oem_auto": 0xF3, "oem_enlw": 0xF4,
    "oem_backtab": 0xF5, "attn": 0xF6, "crsel": 0xF7, "exsel": 0xF8, "ereof": 0xF9, "play": 0xFA,
    "zoom": 0xFB, "pa1": 0xFD, "oem_clear": 0xFE,
})
# Mouse-button VKs are handled by the mouse hook, never as keys.
_MOUSE_VKS = frozenset({0x01, 0x02, 0x04, 0x05, 0x06})

VK_TO_NAME: dict[int, str] = {vk: name for name, vk in NAME_TO_VK.items()}
VK_TO_NAME.update(_VK_ALIASES)

# Trigger-only keys. They have no single virtual-key code, so Keyware can
# record and bind them but never sends them.
# "copilot": the Copilot key on Windows 11 keyboards. The hardware sends
# Left Win + Left Shift + F23 (scan code 0x6E) in about a millisecond and
# releases F23, Left Shift, Left Win. engine/copilot.py folds that into one key.
# Mouse buttons are trigger-only too. Left/Right must be bound with a modifier
# (models.validate_binding enforces that) so plain clicks are never swallowed.
# Wheel up/down also need a modifier so normal scrolling keeps working.
MOUSE_WHEEL = frozenset({"mouse_wheel_up", "mouse_wheel_down", "mouse_wheel_left", "mouse_wheel_right"})
MOUSE_TRIGGERS = frozenset({"mouse_left", "mouse_right", "mouse_middle", "mouse_x1", "mouse_x2"}) | MOUSE_WHEEL
MOUSE_NEEDS_MODIFIER = frozenset({"mouse_left", "mouse_right", "mouse_wheel_up", "mouse_wheel_down"})
# Only forwarded by the mouse hook when a binding uses them (no replay of passed-through scrolls).
MOUSE_ONLY_WHEN_BOUND = MOUSE_NEEDS_MODIFIER | MOUSE_WHEEL
TRIGGER_ONLY_KEYS = frozenset({"copilot"}) | MOUSE_TRIGGERS
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
    "scroll_lock": "Scroll Lock",
    "pause": "Pause/Break",
    "num_lock": "Num Lock",
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
    "prt sc": "print_screen",
    "prtscn": "print_screen",
    "prt scn": "print_screen",
    "snapshot": "print_screen",
    "scroll lock": "scroll_lock",
    "scrolllock": "scroll_lock",
    "pause/break": "pause",
    "pause break": "pause",
    "break": "pause",
    "pause key": "pause",
    "apps": "menu",
    "back": "backspace",
    **{f"numpad {index}": f"numpad{index}" for index in range(10)},
    **{f"num {index}": f"numpad{index}" for index in range(10)},
}


_ALIASES.update({
    "middle click": "mouse_middle", "middle mouse": "mouse_middle", "mouse 3": "mouse_middle",
    "mouse 4": "mouse_x1", "mouse back": "mouse_x1", "xbutton1": "mouse_x1", "x1": "mouse_x1",
    "mouse 5": "mouse_x2", "mouse forward": "mouse_x2", "xbutton2": "mouse_x2", "x2": "mouse_x2",
    "left click": "mouse_left", "right click": "mouse_right",
    "wheel up": "mouse_wheel_up", "scroll up": "mouse_wheel_up",
    "wheel down": "mouse_wheel_down", "scroll down": "mouse_wheel_down",
    "wheel left": "mouse_wheel_left", "tilt left": "mouse_wheel_left",
    "wheel right": "mouse_wheel_right", "tilt right": "mouse_wheel_right",
})


import re as _re

# Generic names for keys Keyware has no name for, captured by "press the key":
#   vk_XX   any virtual-key code (hex), sendable;
#   sc_XXX  a raw scan code (hex, E0-extended adds 0xE000) when the VK is 0/0xFF, trigger-only;
#   mouse_xN extra mouse buttons beyond X1/X2 the low-level hook reports.
_GENERIC_RE = _re.compile(r"^(?:vk_([0-9a-f]{1,2})|sc_([0-9a-f]{1,4})|mouse_x([0-9]{1,2}))$")


def generic_key(text: str) -> str | None:
    """Canonical generic name (vk_xx / sc_xxx / mouse_xN) or None."""
    m = _GENERIC_RE.match(text.replace("0x", ""))
    if not m:
        return None
    if m.group(1) is not None:
        vk = int(m.group(1), 16)
        if vk in (0, 0xFF) or vk in _MOUSE_VKS:
            return None
        return VK_TO_NAME.get(vk) or f"vk_{vk:02x}"
    if m.group(2) is not None:
        sc = int(m.group(2), 16)
        return f"sc_{sc:03x}" if sc else None
    n = int(m.group(3))
    return f"mouse_x{n}" if 1 <= n <= 16 else None


def is_trigger_only(name: str) -> bool:
    return name in TRIGGER_ONLY_KEYS or name.startswith("sc_") or name.startswith("mouse_")


def is_allowed(name: str) -> bool:
    return name in ALLOWED_KEYS or generic_key(name) == name


def name_to_vk(name: str) -> int | None:
    vk = NAME_TO_VK.get(name)
    if vk is None and name.startswith("vk_") and generic_key(name) == name:
        vk = int(name[3:], 16)
    return vk


def scan_name(scan: int, extended: bool) -> str | None:
    scan = int(scan) & 0xFFFF
    if not scan:
        return None
    return f"sc_{(scan | 0xE000) if extended and scan < 0x100 else scan:03x}"


def canonicalize(name: str | None) -> str | None:
    """Return the canonical key name, or None if it is not a known key."""
    if name is None:
        return None
    text = str(name).strip().lower().replace("_", " ")
    text = " ".join(text.split())
    generic = generic_key(text.replace(" ", "_"))
    if generic:
        return generic
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


_MOUSE_PRETTY = {
    "mouse_left": "Left Click", "mouse_right": "Right Click", "mouse_middle": "Middle Click",
    "mouse_x1": "Mouse Back (X1)", "mouse_x2": "Mouse Forward (X2)",
    "mouse_wheel_up": "Wheel Up", "mouse_wheel_down": "Wheel Down",
    "mouse_wheel_left": "Wheel Tilt Left", "mouse_wheel_right": "Wheel Tilt Right",
}


def vk_to_name(vk_code: int) -> str | None:
    vk = int(vk_code)
    name = VK_TO_NAME.get(vk)
    if name is None and 0 < vk < 0xFF and vk not in _MOUSE_VKS:
        name = f"vk_{vk:02x}"  # unknown/new key: still usable as a trigger
    return name


def format_key(name: str) -> str:
    if name in _MOUSE_PRETTY:
        return _MOUSE_PRETTY[name]
    if name.startswith("mouse_x") and name[7:].isdigit():
        return f"Mouse Button {int(name[7:]) + 3}"
    if name.startswith("vk_"):
        return f"Key 0x{name[3:].upper()}"
    if name.startswith("sc_"):
        return f"Scan 0x{name[3:].upper()}"
    if name in _PRETTY:
        return _PRETTY[name]
    if name.startswith("numpad") and name[6:].isdigit():
        return f"Numpad {name[6:]}"
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    if len(name) == 1:
        return name.upper()
    return name.replace("_", " ").title()


# Keys the editor offers in a picker. Recording still accepts every other key.
PICKER_KEYS = (
    "tab", "scroll_lock", "page_up", "page_down", "home", "end",
    "delete", "insert", "print_screen", "pause",
)


def insert_key(expression: str, key: str) -> str:
    """Add one canonical key to a combo the user is editing."""
    name = canonicalize(key)
    if name is None:
        raise ValueError(f"Unknown key '{key}'.")
    current = (expression or "").strip()
    keys = parse_key_expression(current) if current else []
    if name not in keys:
        keys.append(name)
    return format_keys(keys)


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

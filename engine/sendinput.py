"""Tagged SendInput events and low-level hook classification.

Keyware stamps every keyboard and mouse event it injects with one fixed
``dwExtraInfo`` value:

    KEYWARE_EXTRA = 0x4B575245

That is the ASCII bytes of ``KWRE`` in a 32-bit word. On 64-bit Windows the
value is zero-extended into the pointer-sized ``dwExtraInfo`` field.

The low-level hook ignores an event when its extra info equals
``KEYWARE_EXTRA``, even if the injected flag was stripped. Injected events
that carry any other extra info, including 0, belong to another program.
Those are passed through untouched: they are not remapped and they are not
answered with a key-up. Pulse and any other hook then still see the keys
their own injector produced.

``build_*`` functions are pure. ``send_inputs`` is the only function that
calls SendInput, and only on Windows.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from engine.keys import vk_to_name

# ASCII "KWRE". Documented contract with anything else that reads dwExtraInfo.
KEYWARE_EXTRA = 0x4B575245
# ASCII "KWTE". Injected input with this tag is handled as if it were typed on
# the keyboard. Only Keyware's own end-to-end test driver (tools/e2e_drive.py)
# sends it, so a real test can exercise the hook without a person at the keys.
KEYWARE_TEST_EXTRA = 0x4B575445

LLKHF_EXTENDED = 0x01
LLKHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040

_PRESS_MESSAGES = frozenset({WM_KEYDOWN, WM_SYSKEYDOWN})
_RELEASE_MESSAGES = frozenset({WM_KEYUP, WM_SYSKEYUP})

# Keys whose hardware scan is extended. Right-hand modifiers are in this set.
EXTENDED_VKS = frozenset(
    {
        0x21,  # page up
        0x22,  # page down
        0x23,  # end
        0x24,  # home
        0x25,  # left
        0x26,  # up
        0x27,  # right
        0x28,  # down
        0x2C,  # print screen
        0x2D,  # insert
        0x2E,  # delete
        0x5C,  # right windows
        0x5D,  # menu
        0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3,  # volume and media keys
        0x6F,  # numpad divide
        0xA3,  # right ctrl
        0xA5,  # right alt
    }
)

_MOUSE_DOWN_UP = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


@dataclass(frozen=True)
class HookEvent:
    """One classified WH_KEYBOARD_LL event. ``kind`` is the only decision."""

    kind: str
    name: str = ""
    pressed: bool = False
    repeat: bool = False
    altgr: bool = False
    extended: bool = False
    vk: int = 0


@dataclass(frozen=True)
class InputEvent:
    """One SendInput record. ``extra`` is dwExtraInfo."""

    kind: str
    vk: int = 0
    scan: int = 0
    flags: int = 0
    extra: int = KEYWARE_EXTRA
    mouse_flags: int = 0
    mouse_data: int = 0


def classify_hook_event(msg: int, vk: int, flags: int, extra: int, *, already_down: bool, scan: int = 0) -> HookEvent:
    """Decide what the hook callback is allowed to do with one event.

    ``ignore_own`` and ``pass_injected`` and ``ignore`` must not be remapped.
    ``key`` is a physical key the detector may see.
    """
    flags = int(flags or 0)
    extra = int(extra or 0) & 0xFFFFFFFF
    vk = int(vk)
    extended = bool(flags & LLKHF_EXTENDED)
    if extra == KEYWARE_EXTRA:
        return HookEvent(kind="ignore_own", vk=vk, extended=extended)
    if flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED) and extra != KEYWARE_TEST_EXTRA:
        return HookEvent(kind="pass_injected", vk=vk, extended=extended)
    if msg in _PRESS_MESSAGES:
        pressed = True
    elif msg in _RELEASE_MESSAGES:
        pressed = False
    else:
        return HookEvent(kind="ignore", vk=vk, extended=extended)
    name = vk_to_name(vk) or ""
    if not name and scan:
        from engine.keys import scan_name

        name = scan_name(scan, extended) or ""  # VK 0/0xFF: brand-new key, use its scan code
    if not name:
        return HookEvent(kind="ignore", vk=vk, extended=extended)
    altgr = name == "alt" and (vk == 0xA5 or (vk == 0x12 and extended))
    return HookEvent(
        kind="key",
        name=name,
        pressed=pressed,
        repeat=bool(pressed and already_down),
        altgr=altgr,
        extended=extended,
        vk=vk,
    )


# Hardware scan, extended flag, and whether SendInput must honor the scan.
# Tab's scan is ignored unless KEYEVENTF_SCANCODE is set, so Ctrl+Tab never
# arrives while a low-level hook is installed. Navigation keys and Print
# Screen are E0-prefixed. Pause is not: Windows builds its E1 sequence from
# VK_PAUSE alone, and scan 0x45 would be Num Lock.
_SEND_SCAN = {
    0x09: (0x0F, False, True),   # tab
    0x13: (0x00, False, False),  # pause / break
    0x21: (0x49, True, True),    # page up
    0x22: (0x51, True, True),    # page down
    0x23: (0x4F, True, True),    # end
    0x24: (0x47, True, True),    # home
    0x2C: (0x37, True, True),    # print screen
    0x2D: (0x52, True, True),    # insert
    0x2E: (0x53, True, True),    # delete
    0x91: (0x46, False, True),   # scroll lock
}


def build_keyboard_event(
    vk: int,
    *,
    key_up: bool = False,
    extended: bool | None = None,
    extra: int = KEYWARE_EXTRA,
    scan: int = 0,
    use_scan: bool = False,
) -> InputEvent:
    """A virtual-key event tagged with ``extra``. Extended is chosen from the VK.

    ``use_scan`` sets KEYEVENTF_SCANCODE so Windows uses ``scan`` instead of
    inventing one. Leave it off for letters: their scan depends on the layout.
    """
    flags = KEYEVENTF_KEYUP if key_up else 0
    if extended is None:
        extended = int(vk) in EXTENDED_VKS
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if use_scan and scan:
        flags |= KEYEVENTF_SCANCODE
    return InputEvent(kind="key", vk=int(vk) & 0xFFFF, scan=int(scan) & 0xFFFF, flags=flags, extra=int(extra) & 0xFFFFFFFF)


def build_unicode_events(text: str, *, extra: int = KEYWARE_EXTRA) -> list[InputEvent]:
    """UTF-16 code units, each as a tagged key-down and key-up."""
    events: list[InputEvent] = []
    raw = text.encode("utf-16-le")
    for index in range(0, len(raw), 2):
        unit = int.from_bytes(raw[index : index + 2], "little")
        events.append(InputEvent(kind="unicode", scan=unit, flags=KEYEVENTF_UNICODE, extra=int(extra) & 0xFFFFFFFF))
        events.append(
            InputEvent(
                kind="unicode",
                scan=unit,
                flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                extra=int(extra) & 0xFFFFFFFF,
            )
        )
    return events


def build_mouse_click(button: str, *, extra: int = KEYWARE_EXTRA) -> list[InputEvent]:
    pair = _MOUSE_DOWN_UP.get(button)
    if pair is None:
        raise ValueError(f"Unknown mouse button '{button}'.")
    down, up = pair
    tag = int(extra) & 0xFFFFFFFF
    return [
        InputEvent(kind="mouse", extra=tag, mouse_flags=down),
        InputEvent(kind="mouse", extra=tag, mouse_flags=up),
    ]


_XBUTTONS = {"x1": 1, "x2": 2}
# Wheel notches: (MOUSEEVENTF_WHEEL / HWHEEL, delta).
_WHEELS = {"wheel_up": (0x0800, 120), "wheel_down": (0x0800, -120),
           "wheel_right": (0x1000, 120), "wheel_left": (0x1000, -120)}


def _replay_special(key: str, *, up: bool) -> list[InputEvent] | None:
    """SendInput records replaying a passed-through mouse/scan-code trigger, or None for a VK key."""
    if key.startswith("mouse_"):
        button = key[6:]
        if button in _WHEELS:  # a notch is one event: send it on the press only
            flag, delta = _WHEELS[button]
            return [] if up else [InputEvent(kind="mouse", extra=KEYWARE_EXTRA, mouse_flags=flag,
                                             mouse_data=delta & 0xFFFFFFFF)]
        if button.startswith("x") and button[1:].isdigit() and button not in _XBUTTONS:
            return []  # buttons past X2 cannot be synthesised by SendInput
        return [build_mouse_button(button, up=up)]
    if key.startswith("sc_"):
        sc = int(key[3:], 16)
        flags = 0x0008 | (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if sc > 0xFF else 0)
        return [InputEvent(kind="key", vk=0, scan=sc & 0xFF, flags=flags, extra=KEYWARE_EXTRA)]
    return None


def build_mouse_button(button: str, *, up: bool, extra: int = KEYWARE_EXTRA) -> InputEvent:
    if button in _XBUTTONS:
        # MOUSEEVENTF_XDOWN / XUP with XBUTTON1/2 in mouseData.
        return InputEvent(kind="mouse", extra=int(extra) & 0xFFFFFFFF,
                          mouse_flags=0x0100 if up else 0x0080, mouse_data=_XBUTTONS[button])
    pair = _MOUSE_DOWN_UP.get(button)
    if pair is None:
        raise ValueError(f"Unknown mouse button '{button}'.")
    return InputEvent(kind="mouse", extra=int(extra) & 0xFFFFFFFF, mouse_flags=pair[1] if up else pair[0])


def send_inputs(events: list[InputEvent]) -> None:
    """Send tagged events. Raises on a short write. Never called by tests."""
    if sys.platform != "win32":
        raise OSError("SendInput is only available on Windows.")
    if not events:
        return
    _send_win32(events)


def _send_win32(events: list[InputEvent]) -> None:
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        )

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        )

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = (
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        )

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT))

        _anonymous_ = ("u",)
        _fields_ = (("type", wintypes.DWORD), ("u", _U))

    records = (INPUT * len(events))()
    for index, event in enumerate(events):
        if event.kind == "mouse":
            records[index].type = INPUT_MOUSE
            records[index].mi = MOUSEINPUT(0, 0, int(event.mouse_data), int(event.mouse_flags), 0, int(event.extra))
        elif event.kind == "unicode":
            records[index].type = INPUT_KEYBOARD
            records[index].ki = KEYBDINPUT(0, int(event.scan), int(event.flags), 0, int(event.extra))
        else:
            records[index].type = INPUT_KEYBOARD
            records[index].ki = KEYBDINPUT(int(event.vk), int(event.scan), int(event.flags), 0, int(event.extra))

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    sent = int(user32.SendInput(len(events), records, ctypes.sizeof(INPUT)))
    if sent != len(events):
        error = ctypes.get_last_error()
        raise OSError(f"SendInput delivered {sent} of {len(events)} events (error {error}).")


def windows_scan_code(vk: int) -> int:
    """MapVirtualKeyW(vk, MAPVK_VK_TO_VSC). 0 off Windows or on error."""
    if sys.platform != "win32":
        return 0
    import ctypes

    try:
        return int(ctypes.windll.user32.MapVirtualKeyW(int(vk), 0)) & 0xFF
    except Exception:
        return 0


class TaggedInjector:
    """Keyboard and mouse injection that always sets ``KEYWARE_EXTRA``."""

    def __init__(self, send=None, scan_for=None):
        self._send = send or send_inputs
        # Games (BlueStacks, DirectInput, raw input) read the hardware scan
        # code. A VK-only event with scan 0 is ignored by many of them.
        self._scan_for = scan_for or windows_scan_code

    def _scan(self, vk: int) -> int:
        try:
            return int(self._scan_for(vk) or 0)
        except Exception:
            return 0

    def press(self, key: str) -> None:
        special = _replay_special(key, up=False)
        if special is not None:
            self._send(special)
            return
        self._send([self._key_event(key, key_up=False)])

    def release(self, key: str) -> None:
        special = _replay_special(key, up=True)
        if special is not None:
            self._send(special)
            return
        self._send([self._key_event(key, key_up=True)])

    def _key_event(self, key: str, *, key_up: bool) -> InputEvent:
        from engine.keys import name_to_vk

        vk = name_to_vk(key)
        if vk is None:
            raise ValueError(f"Unknown key '{key}'.")
        spec = _SEND_SCAN.get(vk)
        if spec is not None:
            scan, extended, use_scan = spec
            return build_keyboard_event(vk, key_up=key_up, scan=scan, extended=extended, use_scan=use_scan)
        return build_keyboard_event(vk, key_up=key_up, scan=self._scan(vk))

    def type_text(self, text: str) -> None:
        if text:
            self._send(build_unicode_events(text))

    def click(self, button: str) -> None:
        self._send(build_mouse_click(button))

    def mouse_down(self, button: str) -> None:
        self._send([build_mouse_button(button, up=False)])

    def mouse_up(self, button: str) -> None:
        self._send([build_mouse_button(button, up=True)])

    def mask(self) -> None:
        from engine.keystate import MASK_VK

        self._send(
            [
                build_keyboard_event(MASK_VK, key_up=False, extended=False),
                build_keyboard_event(MASK_VK, key_up=True, extended=False),
            ]
        )

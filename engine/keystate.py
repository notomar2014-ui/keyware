"""Physical key state on Windows, used to forget modifiers stuck after Win+L.

GetAsyncKeyState is read from the timer thread, never from the hook callback.
Off Windows the probe reports every key as down so a reconcile pass is a no-op.
"""

from __future__ import annotations

import sys

from engine.keys import NAME_TO_VK

# Unassigned VK. A press of it while Win or Alt is down stops the Start menu
# and the menu bar from treating the modifier as a lone tap. Same value
# PowerToys Keyboard Manager uses for this.
MASK_VK = 0xE8

_SIDES: dict[str, tuple[int, ...]] = {
    "shift": (0x10, 0xA0, 0xA1),
    "ctrl": (0x11, 0xA2, 0xA3),
    "alt": (0x12, 0xA4, 0xA5),
    "win": (0x5B, 0x5C),
}


def key_down_from(get_async, name: str) -> bool:
    """True when ``get_async(vk)`` has the high bit set for any side of ``name``.

    ``get_async`` is injected so tests never call GetAsyncKeyState. A key with
    no virtual-key mapping is reported down, which makes a reconcile a no-op.
    """
    vks = _SIDES.get(name)
    if name == "copilot":
        vks = (0x86,)  # F23 is the part of the Copilot key that stays down
    if not vks:
        from engine.keys import name_to_vk

        vk = name_to_vk(name)
        vks = (vk,) if vk is not None else ()
    if not vks:
        return True
    for vk in vks:
        if int(get_async(int(vk))) & 0x8000:
            return True
    return False


def windows_key_down(name: str) -> bool:
    """True when any physical key for this canonical name is down.

    Off Windows every key is reported down so a reconcile pass is a no-op.
    GetAsyncKeyState is also true for injected keys, so this probe is only
    used to forget a key the OS says is already up. It is never called from
    the hook callback.
    """
    if sys.platform != "win32":
        return True
    import ctypes

    user32 = ctypes.windll.user32
    get_state = user32.GetAsyncKeyState
    get_state.restype = ctypes.c_short
    return key_down_from(get_state, name)

"""System tray icon. Optional: the window still runs if a tray backend is missing."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


ASSETS = Path(__file__).resolve().with_name("assets")


def _base_icon(size: int):
    from PIL import Image

    source = ASSETS / ("keyware_16.png" if size <= 24 else "keyware_32.png" if size <= 40 else "keyware_64.png" if size <= 64 else "keyware_256.png")
    try:
        image = Image.open(source).convert("RGBA")
    except Exception:
        image = Image.new("RGBA", (size, size), (12, 12, 13, 255))
    if image.size != (size, size):
        image = image.resize((size, size), Image.LANCZOS)
    return image


def make_icon(size: int = 64, paused: bool = False, off: bool = False):
    """The Keyware keyboard logo. OFF is dimmed with a red dot, paused gets pause bars."""
    from PIL import ImageDraw, ImageEnhance

    image = _base_icon(size)
    draw = ImageDraw.Draw(image)
    dot = max(4, size // 4)
    if paused:
        bar_w = max(2, size // 10)
        gap = max(2, size // 12)
        top, bottom = size * 0.30, size * 0.70
        left = (size - (bar_w * 2 + gap)) / 2
        draw.rounded_rectangle((size * 0.18, size * 0.18, size * 0.82, size * 0.82), radius=size // 6, fill=(10, 10, 11, 220))
        draw.rectangle((left, top, left + bar_w, bottom), fill=(255, 255, 255, 255))
        draw.rectangle((left + bar_w + gap, top, left + bar_w * 2 + gap, bottom), fill=(255, 255, 255, 255))
        return image
    if off:
        alpha = image.getchannel("A")
        image = ImageEnhance.Brightness(image.convert("RGB")).enhance(0.45).convert("RGBA")
        image.putalpha(alpha)
        draw = ImageDraw.Draw(image)
        draw.ellipse((size - dot - 1, size - dot - 1, size - 1, size - 1), fill=(239, 68, 68, 255))
        return image
    draw.ellipse((size - dot - 1, size - dot - 1, size - 1, size - 1), fill=(34, 197, 94, 255))
    return image


def start_tray(
    on_show: Callable[[], None],
    on_pause: Callable[[], None],
    on_reload: Callable[[], None],
    on_open_config: Callable[[], None],
    on_quit: Callable[[], None],
    is_paused: Callable[[], bool],
    on_power: Callable[[], None] | None = None,
    is_on: Callable[[], bool] | None = None,
):
    try:
        import pystray
    except Exception:
        return None
    icon = pystray.Icon(
        "keyware",
        make_icon(off=not (is_on and is_on())),
        "Keyware",
        menu=pystray.Menu(
            pystray.MenuItem("Show Keyware", lambda *_args: on_show(), default=True),
            pystray.MenuItem(
                lambda *_args: "Turn OFF" if (is_on and is_on()) else "Turn ON",
                lambda *_args: on_power and on_power(),
            ),
            pystray.MenuItem("Pause", lambda *_args: on_pause(), checked=lambda *_args: is_paused()),
            pystray.MenuItem("Reload config", lambda *_args: on_reload()),
            pystray.MenuItem("Open config", lambda *_args: on_open_config()),
            pystray.MenuItem("Quit", lambda *_args: on_quit()),
        ),
    )
    threading.Thread(target=icon.run, name="keyware-tray", daemon=True).start()
    return icon

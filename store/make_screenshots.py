"""Frame real Keyware window captures as Microsoft Store screenshots.

1920x1080 is the Store set. 1080x1080 is the square set.
  python3 store/capture_ui.py /tmp/kw-shots/raw
  python3 store/make_screenshots.py /tmp/kw-shots/raw site/assets/store
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
SQ = 1080
BG = (9, 9, 11)
INK = (245, 245, 246)
MUTED = (139, 156, 255)
FONT = "/usr/share/fonts/truetype/macos/Inter-Bold.ttf"
FONT_SMALL = "/usr/share/fonts/truetype/macos/Inter-SemiBold.ttf"

# Captions are the Store listing lines. Each source is a real Keyware 1.4.1 window.
# Website frames (07+) come from store/capture_site.py photographs of keyware.site.
SITE_SHOTS = [
    ("hero.png", "07-site-home.png", "Make your keys your own"),
    ("features.png", "08-site-features.png", "Every key can do more"),
    ("gallery.png", "09-site-gallery.png", "A look at the app"),
    ("guide.png", "10-site-guide.png", "How to use Keyware"),
    ("whats-new.png", "11-site-whats-new.png", "What's new in 1.4.1"),
]
SHOTS = [
    ("01-text.png", "01-plain-english.png", "Remap any key in plain English"),
    ("02-profiles.png", "02-per-app.png", "Per-app shortcuts"),
    ("03-macro.png", "03-macros.png", "Macros & hotkeys"),
    ("04-script.png", "04-scripting.png", "Optional Advanced scripting (1.4.1)"),
    ("05-safety.png", "05-safety.png", "Built-in update check / pause & panic stop"),
    ("06-home.png", "06-free.png", "x64 + ARM64, free & open source"),
]


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def fit_title(draw: ImageDraw.ImageDraw, text: str, width: int, start: int, floor: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > floor and draw.textlength(text, font=font(FONT, size)) > width:
        size -= 2
    return font(FONT, size)


def rounded(im: Image.Image, radius: int) -> Image.Image:
    out = im.convert("RGBA")
    mask = Image.new("L", out.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, out.width - 1, out.height - 1), radius, fill=255)
    out.putalpha(mask)
    return out


def shadow(base: Image.Image, box: tuple[int, int, int, int], radius: int) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0, y0 + 18, x1, y1 + 28), radius + 8, fill=(0, 0, 0, 170))
    layer = layer.filter(ImageFilter.GaussianBlur(22))
    base.alpha_composite(layer)


def place_app(canvas: Image.Image, app: Image.Image, top: int, side: int, bottom: int) -> None:
    avail_w = canvas.width - side * 2
    avail_h = canvas.height - top - bottom
    scale = min(avail_w / app.width, avail_h / app.height)
    resized = app.resize((round(app.width * scale), round(app.height * scale)), Image.LANCZOS)
    framed = rounded(resized, 22)
    x = (canvas.width - framed.width) // 2
    y = top + (avail_h - framed.height) // 2
    shadow(canvas, (x, y, x + framed.width, y + framed.height), 22)
    canvas.alpha_composite(framed, (x, y))


def wide(app: Image.Image, title: str) -> Image.Image:
    canvas = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(canvas)
    eyebrow = font(FONT_SMALL, 22)
    draw.text((72, 48), "KEYWARE", font=eyebrow, fill=MUTED)
    title_font = fit_title(draw, title, W - 144, 64, 36)
    draw.text((72, 84), title, font=title_font, fill=INK)
    place_app(canvas, app, 176, 72, 48)
    return canvas.convert("RGB")


def square(app: Image.Image, title: str) -> Image.Image:
    canvas = Image.new("RGBA", (SQ, SQ), BG + (255,))
    draw = ImageDraw.Draw(canvas)
    draw.text((48, 36), "KEYWARE", font=font(FONT_SMALL, 18), fill=MUTED)
    title_font = fit_title(draw, title, SQ - 96, 42, 26)
    draw.text((48, 64), title, font=title_font, fill=INK)
    place_app(canvas, app, 140, 36, 36)
    return canvas.convert("RGB")


def _frame(src: Path, out: Path, shots) -> None:
    wide_dir = out / "1920x1080"
    square_dir = out / "1080x1080"
    wide_dir.mkdir(parents=True, exist_ok=True)
    square_dir.mkdir(parents=True, exist_ok=True)
    for source, name, title in shots:
        app = Image.open(src / source).convert("RGB")
        wide(app, title).save(wide_dir / name, optimize=True)
        square(app, title).save(square_dir / name, optimize=True)
        print(name, title)


def frame_site(src_dir: str, out_dir: str) -> None:
    """Frame live keyware.site captures. App shots stay numbered 01-06."""
    _frame(Path(src_dir), Path(out_dir), SITE_SHOTS)


def main(src_dir: str, out_dir: str) -> None:
    _frame(Path(src_dir), Path(out_dir), SHOTS)


if __name__ == "__main__":
    main(*sys.argv[1:3])

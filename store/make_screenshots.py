"""Compose Microsoft Store screenshots (1920x1080 PNG) from Keyware v1.3.4 UI captures.

Store rule: PNG, at least 1366x768, up to 10 per language. Run:  python store/make_screenshots.py SRC_DIR OUT_DIR
Only v1.3.4 captures are used: older ones show a Windows user folder in the log and the donation button.
The one v1.3.4 capture that still shows the "Support Keyware" button gets it painted over, matching the
current build where donation buttons are hidden.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
BG = (9, 9, 11)
FONT = "/usr/share/fonts/truetype/sand-box/google/Inter/Inter-VariableFont_opsz,wght.ttf"

SHOTS = [
    ("ui_v134_text.png", "01_text_mode.png", "Describe it, get a binding",
     "Type what you want in plain words. Keyware drafts the binding, you review it and save."),
    ("ui_v134_gear.png", "02_bindings.png", "Chords, holds and leader keys",
     "Bind key combos, timed holds or a leader key to open sites, type text, launch apps and more."),
    ("ui_v134_profiles.png", "03_profiles.png", "Profiles per app",
     "Gaming, Browser, Code: each profile turns on only in the apps you choose."),
    ("ui_v134_settings.png", "04_settings.png", "Start with Windows, your way",
     "Dry run, audio ticks, a pause hotkey (Ctrl+Alt+F12) and an optional own AI key."),
    ("ui_v134_feedback.png", "05_feedback.png", "Free and open source",
     "No accounts, no ads, no tracking. Send ideas or bug reports right from Settings."),
]


def mask_support_button(im: Image.Image) -> Image.Image:
    """Paint over the pink 'Support Keyware' button (bbox found by its pink text/heart)."""
    px = im.load()
    pink = [(x, y) for y in range(im.height) for x in range(im.width)
            if px[x, y][0] > 200 and px[x, y][1] < 140 and px[x, y][2] > 120]
    if not pink:
        return im
    x0, y0 = min(p[0] for p in pink), min(p[1] for p in pink)
    x1, y1 = max(p[0] for p in pink), max(p[1] for p in pink)
    # The button is a rounded rect about 22 px left/right and 12 px above/below its pink label.
    ImageDraw.Draw(im).rectangle([x0 - 30, y0 - 17, min(x1 + 24, im.width - 1), y1 + 14], fill=BG)
    return im


def font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_axes([14 if size < 30 else 32, weight])
    except Exception:
        pass
    return f


def wrap(draw, text, f, width):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=f) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def compose(src: Path, title: str, body: str) -> Image.Image:
    app = mask_support_button(Image.open(src).convert("RGB"))
    scale = min(1300 / app.width, 960 / app.height)
    app = app.resize((round(app.width * scale), round(app.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), BG)
    ax, ay = W - app.width - 60, (H - app.height) // 2
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle([ax - 2, ay - 2, ax + app.width + 1, ay + app.height + 1], 10, outline=(48, 48, 56), width=2)
    canvas.paste(app, (ax, ay))
    text_w = ax - 60 - 70
    ft, fb = font(52, 700), font(28, 400)
    y = H // 2 - 140
    for line in wrap(d, title, ft, text_w):
        d.text((60, y), line, font=ft, fill=(245, 245, 247))
        y += 64
    y += 24
    for line in wrap(d, body, fb, text_w):
        d.text((60, y), line, font=fb, fill=(170, 170, 180))
        y += 40
    return canvas


def main(src_dir: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for src, name, title, body in SHOTS:
        img = compose(Path(src_dir) / src, title, body)
        assert img.size[0] >= 1366 and img.size[1] >= 768
        img.save(out / name, optimize=True)
        print(name, img.size)


if __name__ == "__main__":
    main(*sys.argv[1:3])

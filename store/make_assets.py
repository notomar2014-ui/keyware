"""Generate the MSIX visual assets from Keyware's own icon (ui/assets/keyware.svg).

Run from the repo root:  python store/make_assets.py
Needs: pip install cairosvg pillow   (cairosvg needs the Cairo library; on Windows use the
committed PNGs in store/Assets, which are what the MSIX build packs.)
"""

from __future__ import annotations

import io
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "ui" / "assets" / "keyware.svg"
ICON_SMALL = ROOT / "ui" / "assets" / "keyware_small.svg"  # simpler drawing that stays crisp at 16-48 px
OUT = Path(__file__).resolve().parent / "Assets"
LISTING = Path(__file__).resolve().parent / "StoreListingImages"


def icon(size: int) -> Image.Image:
    source = ICON_SMALL if size <= 48 and ICON_SMALL.exists() else ICON
    png = cairosvg.svg2png(url=str(source), output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def square(size: int) -> Image.Image:
    return icon(size)


def padded(width: int, height: int, fill: float) -> Image.Image:
    """Icon centred on a transparent canvas, ``fill`` = icon height / canvas height."""
    side = max(1, round(min(width, height) * fill))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(icon(side), ((width - side) // 2, (height - side) // 2))
    return canvas


def save(image: Image.Image, name: str, folder: Path = OUT) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    image.save(folder / name, optimize=True)


def main() -> None:
    scales = {100: 1.0, 125: 1.25, 150: 1.5, 200: 2.0, 400: 4.0}
    for scale, factor in scales.items():
        save(square(round(44 * factor)), f"Square44x44Logo.scale-{scale}.png")
        save(padded(round(150 * factor), round(150 * factor), 0.66), f"Square150x150Logo.scale-{scale}.png")
        save(padded(round(310 * factor), round(150 * factor), 0.66), f"Wide310x150Logo.scale-{scale}.png")
        save(padded(round(71 * factor), round(71 * factor), 0.70), f"SmallTile.scale-{scale}.png")
        save(padded(round(310 * factor), round(310 * factor), 0.60), f"LargeTile.scale-{scale}.png")
        save(square(round(50 * factor)), f"StoreLogo.scale-{scale}.png")
        save(padded(round(620 * factor), round(300 * factor), 0.66), f"SplashScreen.scale-{scale}.png")
    for size in (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256):
        save(square(size), f"Square44x44Logo.targetsize-{size}.png")
        save(square(size), f"Square44x44Logo.targetsize-{size}_altform-unplated.png")
        save(square(size), f"Square44x44Logo.targetsize-{size}_altform-lightunplated.png")
    # Partner Center Store listing logos (uploaded by hand, not packed into the MSIX).
    for size in (300, 1080, 2160):
        save(square(size), f"StoreLogo_{size}x{size}.png", LISTING)
    print(f"Wrote {len(list(OUT.glob('*.png')))} package assets to {OUT}")


if __name__ == "__main__":
    main()

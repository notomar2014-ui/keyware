"""Keyware logo: dark rounded square, faceted 3D monochrome keyboard. Original drawing."""
import io, sys
import cairosvg
from PIL import Image

def lerp(a, b, t): return (a[0] + (b[0]-a[0])*t, a[1] + (b[1]-a[1])*t)

def make_svg(simple=False):
    # Keyboard top face as a trapezoid seen from the front and above.
    if simple:
        BL, BR, FR, FL = (46, 72), (210, 72), (238, 166), (18, 166)
        depth = 30
    else:
        BL, BR, FR, FL = (56, 80), (200, 80), (230, 160), (26, 160)
        depth = 22
    def at(u, v):  # u across 0..1, v back(0)->front(1)
        return lerp(lerp(BL, BR, u), lerp(FL, FR, u), v)
    def quad(u0, v0, u1, v1):
        return [at(u0, v0), at(u1, v0), at(u1, v1), at(u0, v1)]
    pts = lambda ps: " ".join(f"{x:.2f},{y:.2f}" for x, y in ps)
    out = []
    out.append('<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">')
    out.append('''<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#232326"/><stop offset="1" stop-color="#0b0b0c"/></linearGradient>
<linearGradient id="top" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8e8e95"/><stop offset="1" stop-color="#55555b"/></linearGradient>
<linearGradient id="front" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#3b3b40"/><stop offset="1" stop-color="#26262a"/></linearGradient>
<linearGradient id="left" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6b6b72"/><stop offset="1" stop-color="#46464c"/></linearGradient>
<linearGradient id="cap" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#c9c9cf"/></linearGradient>
<linearGradient id="capdim" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e6e6ea"/><stop offset="1" stop-color="#a7a7ae"/></linearGradient>
<linearGradient id="hl" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ffffff" stop-opacity="0"/><stop offset=".5" stop-color="#ffffff" stop-opacity=".35"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
</defs>''')
    out.append('<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#bg)"/>')
    if not simple: out.append('<rect x="8.75" y="8.75" width="238.5" height="238.5" rx="55.25" fill="none" stroke="#ffffff" stroke-opacity=".08" stroke-width="1.5"/>')
    # soft shadow
    out.append(f'<ellipse cx="128" cy="{FL[1]+depth+12}" rx="104" ry="10" fill="#000" opacity=".45"/>')
    # body facets: front, left side, right side, top
    out.append(f'<polygon points="{pts([FL, FR, (FR[0], FR[1]+depth), (FL[0], FL[1]+depth)])}" fill="url(#front)"/>')
    out.append(f'<polygon points="{pts([BL, FL, (FL[0], FL[1]+depth)])}" fill="url(#left)" opacity=".9"/>')
    out.append(f'<polygon points="{pts([BR, FR, (FR[0], FR[1]+depth)])}" fill="#2d2d32"/>')
    out.append(f'<polygon points="{pts([BL, BR, FR, FL])}" fill="url(#top)"/>')
    out.append(f'<polyline points="{pts([FL, FR])}" stroke="#d9d9de" stroke-opacity=".55" stroke-width="1.6" fill="none"/>')
    out.append(f'<polyline points="{pts([BL, BR])}" stroke="#ffffff" stroke-opacity=".35" stroke-width="1.2" fill="none"/>')
    # keys
    if simple:
        rows = [[1]*4, [1, 2.4, 1]]
        gap_u, gap_v, mu, mv = 0.07, 0.12, 0.07, 0.12
    else:
        rows = [[1]*10, [1.4]+[1]*8+[1.6], [1.8]+[1]*7+[2.2], [1.3, 1, 5.2, 1, 1.3]]
        gap_u, gap_v, mu, mv = 0.014, 0.05, 0.05, 0.08
    nrows = len(rows)
    row_h = (1 - 2*mv - gap_v*(nrows-1)) / nrows
    for r, widths in enumerate(rows):
        v0 = mv + r*(row_h + gap_v)
        v1 = v0 + row_h
        total = sum(widths)
        usable = 1 - 2*mu - gap_u*(len(widths)-1)
        u = mu
        for w in widths:
            du = usable * w / total
            u0, u1 = u, u + du
            u = u1 + gap_u
            thick = 0.38 * row_h  # visible front lip of the key
            cap_top = quad(u0, v0, u1, v1 - thick)
            lip = [at(u0, v1 - thick), at(u1, v1 - thick), at(u1, v1), at(u0, v1)]
            out.append(f'<polygon points="{pts(lip)}" fill="#7a7a81"/>')
            fill = "url(#cap)" if (r + int(u0*20)) % 3 else "url(#capdim)"
            out.append(f'<polygon points="{pts(cap_top)}" fill="{fill}"/>')
            if not simple:
                a, b = cap_top[0], cap_top[1]
                out.append(f'<polyline points="{pts([a, b])}" stroke="#ffffff" stroke-width=".9" fill="none" opacity=".9"/>')
    # glossy facet streak across the top face
    out.append(f'<polygon points="{pts([at(0.0,0.0), at(0.38,0.0), at(0.18,1.0), at(-0.02,1.0)])}" fill="url(#hl)" opacity=".35"/>')
    out.append('</svg>')
    return "\n".join(out)

def render(svg, size):
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def render_small(svg, size):
    """Title-bar sizes: trim the empty margin so the keyboard fills the square."""
    big = render(svg, size * 8)
    # The dark tile behind the keyboard is invisible at 16 px; trim to the keyboard itself.
    bright = big.convert("L").point(lambda v: 255 if v > 70 else 0)
    box = bright.getbbox() or big.getbbox()
    art = big.crop(box)
    room = size - 1
    scale = room / max(art.size)
    art = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.alpha_composite(art, ((size - art.width) // 2, (size - art.height) // 2))
    return out

if __name__ == "__main__":
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    full, simple = make_svg(False), make_svg(True)
    open(f"{outdir}/keyware.svg", "w").write(full)
    open(f"{outdir}/keyware_small.svg", "w").write(simple)
    imgs = {}
    # Each size is rendered natively (no downscaling), so the title bar
    # (16/20/24 px at 100-150 % scaling) and taskbar stay crisp.
    ico_sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    for size in ico_sizes:
        imgs[size] = render(full, size) if size >= 40 else render_small(simple, size)
    for size in (256, 128, 64, 32, 16):
        imgs[size].save(f"{outdir}/keyware_{size}.png")
    imgs[256].save(f"{outdir}/keyware.ico", sizes=[(s, s) for s in ico_sizes],
                   append_images=[imgs[s] for s in ico_sizes if s != 256])
    # preview: large + each size, on dark and light
    W, H = 900, 380
    prev = Image.new("RGBA", (W, H), (245, 245, 247, 255))
    dark = Image.new("RGBA", (W//2, H), (30, 30, 32, 255)); prev.paste(dark, (W//2, 0))
    prev.alpha_composite(imgs[256], (40, 60))
    x = 330
    for size in (64, 32, 16):
        prev.alpha_composite(imgs[size], (x, 160 - size//2)); x += size + 24
    big2 = imgs[256].resize((180, 180), Image.LANCZOS)
    prev.alpha_composite(big2, (W//2 + 30, 100))
    x = W//2 + 240
    for size in (64, 32, 16):
        prev.alpha_composite(imgs[size], (x, 190 - size//2)); x += size + 24
    prev.convert("RGB").save(f"{outdir}/logo_preview.png")
    print("ok")

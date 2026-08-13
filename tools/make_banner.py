"""Compose the README banner: brand logo, tagline and a rendered part.

Regenerate it after changing the logo assets or the showcase renders:

    python tools/make_banner.py --render docs/showcase-keychain-m.png \
        --out docs/banner.png

Montserrat is the brand typeface. The script looks for it in the usual font
folders and falls back to a system sans if it is missing, so the banner still
builds on a machine that does not have it installed.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent

WIDTH, HEIGHT = 1600, 520
BG_TOP = (22, 22, 31)
BG_BOTTOM = (10, 10, 16)
BRAND_PURPLE = (121, 26, 217)
TEXT = (214, 214, 224)
MUTED = (128, 128, 145)

FONT_DIRS = [
    Path.home() / "Library/Fonts",
    Path("/Library/Fonts"),
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    Path.home() / "Downloads",
]
SYSTEM_FALLBACKS = [
    Path("/System/Library/Fonts/HelveticaNeue.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]


def find_font(weight: str) -> Path | None:
    """Montserrat-<weight>.ttf from any font folder, else a system sans."""
    for directory in FONT_DIRS:
        if not directory.is_dir():
            continue
        for candidate in directory.rglob(f"Montserrat-{weight}.ttf"):
            return candidate
    for fallback in SYSTEM_FALLBACKS:
        if fallback.exists():
            print(f"[banner] Montserrat-{weight} not found, using {fallback.name}")
            return fallback
    return None


def load_font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = find_font(weight)
    if path is None:
        return ImageFont.load_default(size)
    return ImageFont.truetype(str(path), size)


def rasterize_svg(svg: Path, width: int) -> Image.Image:
    """SVG to RGBA at a given width. rsvg-convert if present, else cairosvg."""
    tmp = Path(tempfile.mkstemp(suffix=".png")[1])
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-w", str(width), "-o", str(tmp), str(svg)], check=True)
    else:
        try:
            import cairosvg  # type: ignore
        except ImportError:
            sys.exit("ERROR: need rsvg-convert on PATH or the cairosvg package to "
                     "rasterize the logo.")
        cairosvg.svg2png(url=str(svg), write_to=str(tmp), output_width=width)
    img = Image.open(tmp).convert("RGBA")
    tmp.unlink(missing_ok=True)
    return img


def vertical_gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    w, h = size
    base = Image.new("RGB", (1, h))
    px = base.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return base.resize((w, h), Image.BILINEAR).convert("RGBA")


def purple_halo(size: tuple[int, int], center: tuple[int, int], radius: int) -> Image.Image:
    """A soft brand-colored glow, so the right half is not a flat dark slab."""
    halo = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(halo)
    steps = 42
    for i in range(steps, 0, -1):
        r = radius * i / steps
        alpha = int(52 * (1 - i / steps) ** 2)
        draw.ellipse([center[0] - r, center[1] - r, center[0] + r, center[1] + r],
                     fill=BRAND_PURPLE + (alpha,))
    return halo


def drop_shadow(part: Image.Image, blur: int = 26, offset: int = 16) -> Image.Image:
    from PIL import ImageFilter
    shadow = Image.new("RGBA", (part.width + blur * 3, part.height + blur * 3), (0, 0, 0, 0))
    mask = part.split()[3].point(lambda a: min(150, a))
    black = Image.new("RGBA", part.size, (0, 0, 0, 255))
    shadow.paste(black, (blur + offset // 2, blur + offset), mask)
    return shadow.filter(ImageFilter.GaussianBlur(blur))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", type=Path, default=ROOT / "assets/logo-full.svg")
    ap.add_argument("--render", type=Path, default=ROOT / "docs/showcase-keychain-m.png")
    ap.add_argument("--out", type=Path, default=ROOT / "docs/banner.png")
    args = ap.parse_args()

    canvas = vertical_gradient((WIDTH, HEIGHT), BG_TOP, BG_BOTTOM)
    canvas.alpha_composite(purple_halo((WIDTH, HEIGHT), (1210, 250), 430))

    # Right side: the rendered part, with a soft shadow under it.
    if args.render.exists():
        part = Image.open(args.render).convert("RGBA")
        target_h = 430
        part = part.resize((round(part.width * target_h / part.height), target_h),
                           Image.LANCZOS)
        pos = (WIDTH - part.width - 60, (HEIGHT - part.height) // 2)
        shadow = drop_shadow(part)
        canvas.alpha_composite(shadow, (pos[0] - 78, pos[1] - 78))
        canvas.alpha_composite(part, pos)

    # Left side: logo, tagline, credit line. The logo SVG carries generous empty
    # margins, so crop to the inked area first — otherwise the wordmark lands on
    # top of the tagline and the whole left column drifts off center.
    logo = rasterize_svg(args.logo, 620)
    box = logo.split()[3].getbbox()
    if box:
        logo = logo.crop(box)

    left = 90
    logo_top = 118
    canvas.alpha_composite(logo, (left, logo_top))

    draw = ImageDraw.Draw(canvas)
    tagline = load_font("Medium", 32)
    small = load_font("Regular", 21)

    y = logo_top + logo.height + 38
    draw.text((left, y), "The logo on any part you print.", font=tagline, fill=TEXT)
    draw.text((left, y + 44), "STL or 3MF in, multicolor GLB out.", font=tagline, fill=TEXT)

    draw.line([(left + 2, HEIGHT - 112), (left + 42, HEIGHT - 112)],
              fill=BRAND_PURPLE, width=3)
    draw.text((left, HEIGHT - 94), "in collaboration with MechatronicStore",
              font=small, fill=MUTED)
    draw.text((left, HEIGHT - 62), "mechatronicstore.cl", font=small, fill=MUTED)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(args.out, quality=95)
    print(f"[banner] {args.out} ({args.out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

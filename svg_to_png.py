"""Convert SVG -> high-res RGBA PNG.

Prefers `rsvg-convert` (librsvg) when available: handles SVG2, gradients,
filters, masks correctly. Falls back to `svglib` (pure Python) if not.

Renders the SVG into a square canvas (default 2048x2048) preserving aspect.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

from color_utils import hex_to_rgb as _hex_to_rgb  # re-exported for tests/callers


def has_rsvg() -> bool:
    return shutil.which("rsvg-convert") is not None


def _quantize_to_palette(im: Image.Image, palette_hex: list[str]) -> Image.Image:
    """Snap every pixel to the nearest color from a FIXED palette of pure hex
    values. Median-cut/k-means would average anti-aliased pixels and produce
    darkened cluster centroids; this avoids that.

    Result: image contains ONLY the exact hex values in ``palette_hex``, with
    crisp anti-alias-free boundaries.
    """
    import numpy as np
    # int32 is required: int16² overflows when channel deltas exceed ~181
    # (181² = 32761 ≈ INT16_MAX), producing negative wrap-around distances
    # that make argmin pick a wrong palette entry. (Black → yellow bug, May 2026.)
    rgb = np.asarray(im.convert("RGB"), dtype=np.int32)  # (H, W, 3)
    pal = np.array([_hex_to_rgb(h) for h in palette_hex], dtype=np.int32)  # (N, 3)
    diffs = rgb[:, :, None, :] - pal[None, None, :, :]
    dists = (diffs * diffs).sum(axis=3)
    idx = dists.argmin(axis=2)
    out = pal[idx].astype(np.uint8)
    return Image.fromarray(out, mode="RGB").convert("RGBA")


def render_with_rsvg(svg_path: Path, size: int, bg_hex: str) -> Image.Image:
    """Render via rsvg-convert (better SVG2 support, handles gradients)."""
    result = subprocess.run(
        ["rsvg-convert", "-w", str(size), "-a", "--background-color", bg_hex, str(svg_path)],
        check=True, capture_output=True,
    )
    return Image.open(io.BytesIO(result.stdout)).convert("RGBA")


def render_with_svglib(svg_path: Path, size: int) -> Image.Image:
    """Render via svglib + reportlab (pure Python fallback). No gradient support."""
    drawing = svg2rlg(str(svg_path))
    if drawing is None:
        raise SystemExit(f"svglib failed to parse {svg_path}")
    src_w, src_h = float(drawing.width) or 1.0, float(drawing.height) or 1.0
    scale = size / max(src_w, src_h)
    drawing.width = src_w * scale
    drawing.height = src_h * scale
    drawing.scale(scale, scale)
    KEY = 0xFF00FF
    buf = io.BytesIO()
    renderPM.drawToFile(drawing, buf, fmt="PNG", bg=KEY)
    buf.seek(0)
    return Image.open(buf).convert("RGBA")


def render(svg_path: Path, png_path: Path, size: int = 2048, bg_hex: str | None = None,
           palette_hex: list[str] | None = None, strict: bool = False) -> None:
    # Pick the best renderer available.
    bg_hex_safe = bg_hex if bg_hex else "#FF00FF"  # magenta chroma-key fallback for svglib
    if has_rsvg():
        rendered = render_with_rsvg(svg_path, size, bg_hex_safe)
        used = "rsvg-convert"
    else:
        rendered = render_with_svglib(svg_path, size)
        used = "svglib"
    print(f"[svg_to_png] renderer={used}  rendered={rendered.size}")

    # Auto-crop to the bbox of non-base-color content (removes empty SVG padding).
    if bg_hex:
        h = bg_hex.lstrip("#")
        bg = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)
    else:
        bg = (0, 0, 0, 0)

    # For svglib (uses magenta chroma key), replace magenta with bg first.
    if not has_rsvg():
        px_in = rendered.load()
        for y in range(rendered.height):
            for x in range(rendered.width):
                r, g, b, _a = px_in[x, y]
                if r > 240 and b > 240 and g < 30:
                    px_in[x, y] = bg

    # Auto-crop: use PIL's native getbbox() with a difference image instead of
    # sampling every Nth pixel (the strided loop could skip 1px-thin strokes
    # entirely on large images).
    # We work in RGB because Image.getbbox() on an RGBA difference treats
    # alpha==0 as "off" even when RGB differs — the result was None for any
    # opaque image with an opaque bg, missing the whole logo.
    from PIL import ImageChops
    rendered_rgb = rendered.convert("RGB")
    bg_layer = Image.new("RGB", rendered.size, bg[:3])
    diff = ImageChops.difference(rendered_rgb, bg_layer)
    diff_bbox = diff.getbbox()
    if diff_bbox is not None:
        # 4px padding so anti-aliased edges aren't clipped flush.
        pad = 4
        l = max(0, diff_bbox[0] - pad)
        t = max(0, diff_bbox[1] - pad)
        r = min(rendered.width, diff_bbox[2] + pad)
        b = min(rendered.height, diff_bbox[3] + pad)
        rendered = rendered.crop((l, t, r, b))
        print(f"[svg_to_png] auto-cropped to {rendered.size} (content bbox)")

    # Paste centered onto a square canvas of the requested size, padded with bg.
    canvas = Image.new("RGBA", (size, size), bg)
    scale = min(size / rendered.width, size / rendered.height)
    new_w = int(rendered.width * scale)
    new_h = int(rendered.height * scale)
    rendered = rendered.resize((new_w, new_h), Image.LANCZOS)
    ox = (size - new_w) // 2
    oy = (size - new_h) // 2
    canvas.paste(rendered, (ox, oy), rendered if rendered.mode == "RGBA" else None)

    # Palette quantization to a FIXED set of pure hex values. Eliminates
    # anti-alias halos that Bambu Studio's Texture-to-Color algorithm otherwise
    # detects as extra filament slots (we want 3-4 clean colors, not 11).
    if bg_hex and palette_hex:
        canvas = _quantize_to_palette(canvas, palette_hex=[bg_hex, *palette_hex])
        print(f"[svg_to_png] palette-quantized to {1 + len(palette_hex)} pure colors: "
              f"{[bg_hex, *palette_hex]}")

    canvas.save(png_path, "PNG")

    # Safeguard: when bg_hex is set, the entire border MUST be that color so
    # mesh faces with UV outside [0,1] (CLAMP/EXTEND) sample the base color.
    if bg_hex:
        px = canvas.load()
        expected = bg
        bad = 0
        for i in range(0, size, max(1, size // 40)):
            for sample in (px[i, 0], px[i, size - 1], px[0, i], px[size - 1, i]):
                if sample != expected:
                    bad += 1
        if bad:
            msg = (f"{bad} edge pixels are NOT base color — non-target faces "
                   f"may sample wrong color in the GLB")
            if strict:
                raise RuntimeError(msg)
            print(f"WARNING: {msg}", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("svg", type=Path)
    p.add_argument("png", type=Path)
    p.add_argument("--size", type=int, default=2048)
    p.add_argument("--bg", default=None, help="Hex bg color (e.g. #1a1a1a). Omit for transparent.")
    p.add_argument("--palette", default=None,
                   help="Comma-separated hex list for palette quantization "
                        "(e.g. '#791ad9,#ffe546,#d6334a'). Logo colors only — bg is added automatically.")
    p.add_argument("--strict", action="store_true",
                   help="Abort with non-zero exit if any edge pixel is not the "
                        "background color (otherwise just prints a warning).")
    args = p.parse_args()
    palette = [c.strip() for c in args.palette.split(",")] if args.palette else None
    render(args.svg, args.png, args.size, args.bg, palette, strict=args.strict)
    print(f"wrote {args.png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

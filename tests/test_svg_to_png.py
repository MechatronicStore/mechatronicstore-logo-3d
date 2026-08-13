"""Tests for svg_to_png module — pure functions only (no rendering)."""
from svg_to_png import _hex_to_rgb, _quantize_to_palette


# ----- C1: _hex_to_rgb supports 3-char hex shorthand -----

def test_hex_to_rgb_6_char():
    assert _hex_to_rgb("#1a1a1a") == (26, 26, 26)


def test_hex_to_rgb_no_hash():
    assert _hex_to_rgb("ffe546") == (255, 229, 70)


def test_hex_to_rgb_3_char_shorthand():
    """#FFF must expand to #FFFFFF — current code fails with ValueError."""
    assert _hex_to_rgb("#fff") == (255, 255, 255)


def test_hex_to_rgb_3_char_mixed():
    assert _hex_to_rgb("#abc") == (0xaa, 0xbb, 0xcc)


# ----- C7: regression — int32 quantization (no int16 overflow) -----

def test_quantize_snaps_pure_pixels_to_palette():
    """Regression for the int16 overflow bug (May 2026). With int16, a black
    pixel was wrongly snapped to yellow because (diffs * diffs).sum() wrapped
    to negative values when any channel delta exceeded 181."""
    from PIL import Image
    import numpy as np

    palette = ["#1a1a1a", "#791ad9", "#ffe546", "#d6334a"]
    # Pure black pixel — distance 0 to first palette entry, > 30000 to the rest
    img = Image.new("RGB", (1, 1), (26, 26, 26)).convert("RGBA")
    out = _quantize_to_palette(img, palette)
    px = out.convert("RGB").getpixel((0, 0))
    assert px == (26, 26, 26), f"black should snap to #1a1a1a, got {px}"


def test_quantize_snaps_purple_correctly():
    from PIL import Image

    palette = ["#1a1a1a", "#791ad9", "#ffe546"]
    img = Image.new("RGB", (1, 1), (121, 26, 217)).convert("RGBA")
    out = _quantize_to_palette(img, palette)
    px = out.convert("RGB").getpixel((0, 0))
    assert px == (121, 26, 217)


def test_quantize_handles_full_image_no_overflow():
    """A whole image of one color must snap entirely to one palette entry."""
    from PIL import Image
    from collections import Counter

    palette = ["#1a1a1a", "#791ad9", "#ffe546", "#d6334a"]
    img = Image.new("RGB", (50, 50), (26, 26, 26)).convert("RGBA")
    out = _quantize_to_palette(img, palette)
    colors = Counter(out.convert("RGB").getdata())
    assert len(colors) == 1
    assert (26, 26, 26) in colors

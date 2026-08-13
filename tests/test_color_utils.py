"""Tests for color_utils module — pure color math, no Blender."""
import pytest

from color_utils import hex_to_rgba_linear


# ----- B4: hex_to_rgba validates input and supports shorthand -----

def test_hex_to_rgba_linear_black():
    r, g, b, a = hex_to_rgba_linear("#000000")
    assert (r, g, b, a) == (0.0, 0.0, 0.0, 1.0)


def test_hex_to_rgba_linear_white():
    r, g, b, a = hex_to_rgba_linear("#ffffff")
    assert (r, g, b, a) == pytest.approx((1.0, 1.0, 1.0, 1.0), abs=1e-6)


def test_hex_to_rgba_linear_purple_srgb_curve():
    # #791ad9 in sRGB is (0.475, 0.102, 0.851); linearized must be smaller for
    # each channel below 1.0 (srgb → linear is non-linear, compresses mids).
    r, g, b, _ = hex_to_rgba_linear("#791ad9")
    assert 0.18 < r < 0.20
    assert 0.005 < g < 0.015
    assert 0.68 < b < 0.71


def test_hex_to_rgba_linear_no_hash():
    r, g, b, a = hex_to_rgba_linear("ffe546")
    assert a == 1.0
    assert 0.9 < r <= 1.0


def test_hex_to_rgba_linear_3_char_shorthand():
    """#fff must expand to #ffffff."""
    assert hex_to_rgba_linear("#fff") == pytest.approx((1.0, 1.0, 1.0, 1.0), abs=1e-6)


def test_hex_to_rgba_linear_invalid_raises():
    """Garbage input must raise ValueError, not crash with cryptic int() error."""
    with pytest.raises(ValueError, match="hex color"):
        hex_to_rgba_linear("not-a-hex")


def test_hex_to_rgba_linear_named_color_raises():
    """Named colors like 'red' must raise ValueError (no silent fallback)."""
    with pytest.raises(ValueError, match="hex color"):
        hex_to_rgba_linear("red")


def test_hex_to_rgba_linear_4_char_raises():
    """Hex codes with non-standard length must raise."""
    with pytest.raises(ValueError, match="hex color"):
        hex_to_rgba_linear("#fffe")

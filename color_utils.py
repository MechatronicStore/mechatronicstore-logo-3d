"""Pure color math used by the texturizer pipeline.

Kept Blender-free so the functions can be unit-tested with vanilla pytest
(apply_logo.py imports bpy at module top, which only resolves inside Blender).
"""
from __future__ import annotations


def _expand_hex(hex_str: str) -> str:
    """Normalize a hex color string to a 6-digit lowercase form.

    Accepts ``#RGB`` shorthand (expands each digit) and the full ``#RRGGBB``
    form, with or without the leading ``#``. Raises ``ValueError`` for any
    other length so callers fail loudly instead of silently mis-parsing.
    """
    h = hex_str.lstrip("#").lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError(f"hex color must be 3 or 6 hex digits, got '{hex_str}'")
    return h


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Parse a hex color into a (r, g, b) tuple of 0..255 ints."""
    h = _expand_hex(hex_str)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _srgb_to_linear(c: float) -> float:
    """Apply the sRGB inverse companding curve. Blender shaders work in linear
    light, so any color picked in a UI/CSS context must be linearized before
    feeding it into a Principled BSDF baseColor."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_rgba_linear(hex_str: str) -> tuple[float, float, float, float]:
    """Parse a hex color and return linear-light (r, g, b, 1.0).

    Used by Blender material setup. Alpha is always 1.0 — we don't model
    transparent filaments in this pipeline.
    """
    r, g, b = hex_to_rgb(hex_str)
    return (
        _srgb_to_linear(r / 255.0),
        _srgb_to_linear(g / 255.0),
        _srgb_to_linear(b / 255.0),
        1.0,
    )

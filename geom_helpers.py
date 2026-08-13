"""Blender-free helpers extracted from apply_logo_geom for unit testing.

Anything that operates on bpy objects stays in apply_logo_geom.py; functions
that work on plain Python values live here so they can be exercised without
spinning up Blender.
"""
from __future__ import annotations


# Convention: each mesh name pattern maps to one AMS filament slot (1-based,
# matches what Bambu Studio displays). The writer of model_settings.config
# uses this mapping to pre-assign filaments so the user doesn't have to
# right-click each part after import.
#
# logo_circuits stays unbound (None) — the orchestrator passes the chosen
# slot at runtime since --circuit-color decides which filament it goes to.
DEFAULT_FILAMENT_SLOTS: dict[str, int | None] = {
    "coaster_base": 1,
    "logo_black":   1,   # if a black group survives, it merges with the base
    "logo_purple":  2,
    "logo_yellow":  3,
    "logo_red":     4,
    "logo_circuits": None,
}


def slot_for_mesh(mesh_name: str, circuit_color_slot: int = 3) -> int:
    """Return the AMS slot (1..N) for a given mesh name. Unknown names fall
    back to slot 1 (the base filament). logo_circuits resolves via the
    runtime circuit-color choice."""
    if mesh_name == "logo_circuits":
        return circuit_color_slot
    return DEFAULT_FILAMENT_SLOTS.get(mesh_name, 1) or 1


# Logical color groups (linear-sRGB → group name). Each SVG material is
# clustered into one of these by nearest centroid.
COLOR_GROUPS: dict[str, tuple[float, float, float]] = {
    "purple": (0.19, 0.01, 0.69),   # mechatronic
    "yellow": (1.00, 0.74, 0.04),   # STORE.CL
    "red":    (0.41, 0.00, 0.00),   # cursor accent
    "black":  (0.02, 0.01, 0.01),   # outlines / detail
}


def classify_material_color(rgb: tuple[float, float, float]) -> str:
    """Snap an arbitrary linear-sRGB color to the nearest logical group name."""
    best, best_dist = None, float("inf")
    for name, ref in COLOR_GROUPS.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if d < best_dist:
            best, best_dist = name, d
    return best  # type: ignore[return-value]


def pick_largest_index(areas: list[float]) -> int | None:
    """Return the index of the largest non-zero area, or ``None`` if the list
    is empty or contains only zeros.

    Used by the "keep only the cursor's main arrow, drop motion lines" cleanup
    in apply_logo_geom. Returning ``None`` instead of an arbitrary index makes
    the caller fall back to keeping everything — safer than silently deleting
    legitimate curves that happen to have zero-area control-point bbox.
    """
    if not areas:
        return None
    largest = max(areas)
    if largest <= 0:
        return None
    return areas.index(largest)

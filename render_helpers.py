"""Blender-free helpers for render_glb_previews.

Lives outside render_previews.py so the math can be unit-tested without
spinning up Blender (which the rest of that module needs for bpy.ops).
"""
from __future__ import annotations

BBox = tuple[tuple[float, float, float], tuple[float, float, float]]


def detect_thin_axis(bbox: BBox, flatness_ratio: float = 5.0) -> int | None:
    """Return 0/1/2 (X/Y/Z) if one axis is much thinner than the other two.

    Returns ``None`` when no single axis dominates by ``flatness_ratio`` — the
    model isn't a flat plate and the caller should keep its default
    orientation.

    Use case: previews of a glTF that was exported with ``yup=False`` will
    arrive Y-flat in Blender (because Blender's glTF importer applies the
    Y-up→Z-up rotation regardless). Detecting which axis is thin lets the
    renderer compensate before framing the camera.
    """
    (mn, mx) = bbox
    ranges = [mx[i] - mn[i] for i in range(3)]
    # "Flat" requires that the thinnest axis is also flatness_ratio× smaller
    # than the *second*-thinnest. A tall cylinder (X=Y=thin, Z=tall) has
    # second-thinnest == thinnest, so this returns None — correct, because
    # rotating it would just spin a tall model on its side.
    sorted_ranges = sorted(ranges)
    thin, second_thin = sorted_ranges[0], sorted_ranges[1]
    if thin == 0 or thin * flatness_ratio > second_thin:
        return None
    return ranges.index(thin)


def union_bbox(boxes: list[BBox]) -> BBox:
    """Return the axis-aligned bounding box that contains every input box.

    Each input is ``((min_x, min_y, min_z), (max_x, max_y, max_z))``.

    Raises ``ValueError`` if the list is empty — caller must supply at least
    one bbox so we never invent an arbitrary zero-volume default.
    """
    if not boxes:
        raise ValueError("union_bbox needs at least one box")
    min_x = min(b[0][0] for b in boxes)
    min_y = min(b[0][1] for b in boxes)
    min_z = min(b[0][2] for b in boxes)
    max_x = max(b[1][0] for b in boxes)
    max_y = max(b[1][1] for b in boxes)
    max_z = max(b[1][2] for b in boxes)
    return (min_x, min_y, min_z), (max_x, max_y, max_z)

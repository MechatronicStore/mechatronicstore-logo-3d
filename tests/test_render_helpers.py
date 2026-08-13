"""Tests for render_helpers — Blender-free geometry math used by previews."""
import pytest

from render_helpers import union_bbox, detect_thin_axis


def test_union_of_one_box_is_itself():
    box = ((0.0, 0.0, 0.0), (1.0, 2.0, 3.0))
    assert union_bbox([box]) == box


def test_union_of_two_disjoint_boxes():
    a = ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    b = ((5.0, 5.0, 5.0), (10.0, 10.0, 10.0))
    assert union_bbox([a, b]) == ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))


def test_union_of_nested_boxes():
    outer = ((-10.0, -10.0, -10.0), (10.0, 10.0, 10.0))
    inner = ((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0))
    # Order shouldn't matter
    assert union_bbox([inner, outer]) == outer
    assert union_bbox([outer, inner]) == outer


def test_union_of_overlapping_boxes():
    a = ((0.0, 0.0, 0.0), (5.0, 5.0, 5.0))
    b = ((3.0, 3.0, 3.0), (8.0, 8.0, 8.0))
    assert union_bbox([a, b]) == ((0.0, 0.0, 0.0), (8.0, 8.0, 8.0))


def test_union_regression_coaster_with_logo_on_top():
    """The bug that produced a one-line preview: coaster bbox is 100x100x4,
    logo is small (70x20x0.4) sitting ON TOP at z=4. Using only the first
    mesh's bbox gave a tiny vertical extent — union must produce the full
    100x100x4.4 envelope."""
    coaster = ((-50.0, -50.0, 0.0), (50.0, 50.0, 4.0))
    logo    = ((-35.0, -10.0, 4.0), (35.0, 10.0, 4.4))
    circuits = ((-48.0, -48.0, 4.0), (48.0, 48.0, 4.4))
    out = union_bbox([coaster, logo, circuits])
    assert out == ((-50.0, -50.0, 0.0), (50.0, 50.0, 4.4))


def test_union_empty_list_raises():
    with pytest.raises(ValueError, match="at least one"):
        union_bbox([])


# ----- E5: detect_thin_axis identifies the "up" axis of a flat model -----

def test_detect_thin_axis_canonical_z_up_coaster():
    """100x100x4 coaster lying flat in Z-up: thin axis is Z."""
    bbox = ((-50.0, -50.0, 0.0), (50.0, 50.0, 4.0))
    assert detect_thin_axis(bbox) == 2  # Z


def test_detect_thin_axis_y_up_after_gltf_roundtrip():
    """Same coaster after Blender's glTF Y-up rotation: thin axis is Y."""
    bbox = ((-50.0, -4.0, -50.0), (50.0, 0.0, 50.0))
    assert detect_thin_axis(bbox) == 1  # Y


def test_detect_thin_axis_tall_cylinder_keeps_z_thinnest_none():
    """A tall cylinder (10x10x100) has Z largest, X & Y both smallest — no
    single 'thin' axis dominates by 5×, so return None to signal 'no flat
    model detected' and the caller should keep Z-up convention."""
    bbox = ((-5.0, -5.0, 0.0), (5.0, 5.0, 100.0))
    assert detect_thin_axis(bbox) is None


def test_detect_thin_axis_requires_significant_flatness():
    """A nearly-cubic model (100x100x90) has Z smallest but not by much — must
    NOT be treated as flat; return None."""
    bbox = ((-50.0, -50.0, 0.0), (50.0, 50.0, 90.0))
    assert detect_thin_axis(bbox) is None


def test_detect_thin_axis_x_thin():
    """A wallplate-shaped model (4x100x100) — thin axis is X."""
    bbox = ((-2.0, -50.0, -50.0), (2.0, 50.0, 50.0))
    assert detect_thin_axis(bbox) == 0

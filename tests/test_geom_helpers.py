"""Tests for geom_helpers — Blender-free geometry helpers used by apply_logo_geom."""
import pytest

from geom_helpers import (
    classify_material_color,
    pick_largest_index,
    slot_for_mesh,
    COLOR_GROUPS,
    DEFAULT_FILAMENT_SLOTS,
)


# ----- A3: pick_largest_index handles zero-area lists safely -----

def test_pick_largest_index_basic():
    assert pick_largest_index([1.0, 3.0, 2.0]) == 1


def test_pick_largest_index_single():
    assert pick_largest_index([5.0]) == 0


def test_pick_largest_index_all_zero_returns_none():
    """When every curve has area 0 we can't trust the 'largest' choice — the
    cursor cleanup must not silently keep an arbitrary curve and discard the
    others."""
    assert pick_largest_index([0.0, 0.0, 0.0]) is None


def test_pick_largest_index_empty_returns_none():
    assert pick_largest_index([]) is None


def test_pick_largest_index_one_nonzero():
    """If one entry is real and the rest zero, that entry wins."""
    assert pick_largest_index([0.0, 0.0, 1.0]) == 2


# ----- A4 sanity: classify_material_color is consistent -----

def test_classify_purple():
    assert classify_material_color((0.19, 0.01, 0.69)) == "purple"


def test_classify_yellow():
    assert classify_material_color((1.0, 0.74, 0.04)) == "yellow"


def test_classify_red():
    assert classify_material_color((0.41, 0.0, 0.0)) == "red"


def test_classify_black():
    assert classify_material_color((0.02, 0.01, 0.01)) == "black"


def test_color_groups_has_expected_keys():
    """Regression: the four canonical groups must exist with stable names."""
    assert set(COLOR_GROUPS.keys()) == {"purple", "yellow", "red", "black"}


# ----- slot_for_mesh: AMS filament assignment by mesh name -----

def test_slot_coaster_base_is_one():
    assert slot_for_mesh("coaster_base") == 1


def test_slot_logo_purple_is_two():
    assert slot_for_mesh("logo_purple") == 2


def test_slot_logo_yellow_is_three():
    assert slot_for_mesh("logo_yellow") == 3


def test_slot_logo_red_is_four():
    assert slot_for_mesh("logo_red") == 4


def test_slot_circuits_resolves_via_runtime_color():
    """logo_circuits has no fixed slot — it follows --circuit-color."""
    assert slot_for_mesh("logo_circuits", circuit_color_slot=3) == 3
    assert slot_for_mesh("logo_circuits", circuit_color_slot=2) == 2


def test_slot_unknown_mesh_falls_back_to_one():
    """Unmapped names default to slot 1 instead of crashing."""
    assert slot_for_mesh("some_random_mesh") == 1


def test_default_slots_keys_match_pipeline_meshes():
    """Regression: the four meshes apply_logo_geom always emits MUST have an entry."""
    pipeline_meshes = {"coaster_base", "logo_purple", "logo_yellow", "logo_red"}
    assert pipeline_meshes <= set(DEFAULT_FILAMENT_SLOTS.keys())

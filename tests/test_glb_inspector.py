"""Tests for glb_inspector — parses a GLB file's JSON chunk without Blender."""
import json
import struct
from pathlib import Path

import pytest

from glb_inspector import inspect_glb, validate_report


def _make_minimal_glb(tmp_path: Path, num_meshes: int = 1) -> Path:
    """Hand-craft a GLB file with N mesh entries in its JSON chunk."""
    gltf = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": list(range(num_meshes))}],
        "nodes": [{"mesh": i} for i in range(num_meshes)],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}
                   for _ in range(num_meshes)],
        "materials": [{"name": f"mat_{i}"} for i in range(num_meshes)],
        "accessors": [{"componentType": 5126, "count": 3, "type": "VEC3"}],
        "bufferViews": [{"buffer": 0, "byteLength": 36}],
        "buffers": [{"byteLength": 36}],
    }
    json_chunk = json.dumps(gltf).encode("utf-8")
    # Pad to 4-byte boundary
    while len(json_chunk) % 4:
        json_chunk += b" "
    bin_chunk = b"\x00" * 36
    while len(bin_chunk) % 4:
        bin_chunk += b"\x00"

    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    out = tmp_path / "minimal.glb"
    with out.open("wb") as f:
        f.write(b"glTF")
        f.write(struct.pack("<I", 2))           # version
        f.write(struct.pack("<I", total))       # length
        f.write(struct.pack("<I", len(json_chunk)))
        f.write(b"JSON")
        f.write(json_chunk)
        f.write(struct.pack("<I", len(bin_chunk)))
        f.write(b"BIN\x00")
        f.write(bin_chunk)
    return out


def test_inspect_minimal_glb(tmp_path: Path):
    glb = _make_minimal_glb(tmp_path, num_meshes=3)
    report = inspect_glb(glb)
    assert report["mesh_count"] == 3
    assert report["material_count"] == 3
    assert report["file_size_bytes"] > 0


def test_inspect_rejects_non_glb(tmp_path: Path):
    bad = tmp_path / "not.glb"
    bad.write_bytes(b"hello world")
    with pytest.raises(ValueError, match="not a GLB"):
        inspect_glb(bad)


def test_validate_passes_minimal(tmp_path: Path):
    report = inspect_glb(_make_minimal_glb(tmp_path, num_meshes=1))
    errors = validate_report(report)
    assert errors == []


def test_validate_flags_missing_meshes(tmp_path: Path):
    """Expect at least 1 mesh — a GLB with zero meshes is almost certainly broken."""
    report = inspect_glb(_make_minimal_glb(tmp_path, num_meshes=1))
    report["mesh_count"] = 0
    errors = validate_report(report)
    assert any("mesh" in e.lower() for e in errors)


def test_validate_flags_mesh_count_mismatch(tmp_path: Path):
    report = inspect_glb(_make_minimal_glb(tmp_path, num_meshes=3))
    errors = validate_report(report, expected_meshes=5)
    assert any("expected 5" in e for e in errors)


def test_validate_warns_on_zero_materials(tmp_path: Path):
    """Materials missing usually means the importer will paint everything grey."""
    report = inspect_glb(_make_minimal_glb(tmp_path, num_meshes=1))
    report["material_count"] = 0
    errors = validate_report(report)
    assert any("material" in e.lower() for e in errors)

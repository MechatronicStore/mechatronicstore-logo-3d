"""Tests for the .3mf reader. Every fixture is built in-test, no binary blobs."""
from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from mesh_input import (
    IDENTITY,
    MeshInputError,
    apply_transform,
    compose,
    list_pieces,
    parse_transform,
    read_3mf,
    to_stl,
    write_binary_stl,
)

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

# A unit tetrahedron: 4 vertices, 4 triangles. Enough to exercise the reader.
VERTS = [(0, 0, 0), (10, 0, 0), (0, 10, 0), (0, 0, 10)]
TRIS = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]


def _mesh_xml() -> str:
    v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in VERTS)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in TRIS)
    return f"<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh>"


def _write_3mf(path: Path, parts: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        for name, body in parts.items():
            zf.writestr(name, body)
    return path


def simple_3mf(tmp_path: Path, unit: str = "millimeter", transform: str | None = None,
               items: int = 1) -> Path:
    item_attr = f' transform="{transform}"' if transform else ""
    build = "".join(f'<item objectid="1"{item_attr}/>' for _ in range(items))
    model = (f'<?xml version="1.0"?><model unit="{unit}" xmlns="{CORE}">'
             f'<resources><object id="1" type="model" name="Tetra">{_mesh_xml()}</object>'
             f'</resources><build>{build}</build></model>')
    return _write_3mf(tmp_path / "simple.3mf", {"3D/3dmodel.model": model})


def production_3mf(tmp_path: Path) -> Path:
    """Objects living in 3D/Objects/*.model, referenced via p:path — the shape
    Bambu Studio and MakerWorld downloads actually use."""
    child = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}">'
             f'<resources><object id="7" type="model" name="Child">{_mesh_xml()}</object>'
             f'</resources><build/></model>')
    root = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}" xmlns:p="{PROD}">'
            f'<resources><object id="1" type="model"><components>'
            f'<component objectid="7" p:path="/3D/Objects/object_7.model" '
            f'transform="1 0 0 0 1 0 0 0 1 100 0 0"/>'
            f'</components></object></resources>'
            f'<build><item objectid="1"/></build></model>')
    return _write_3mf(tmp_path / "prod.3mf", {
        "3D/3dmodel.model": root,
        "3D/Objects/object_7.model": child,
    })


# ------------------------------------------------------------------ transforms

def test_parse_transform_identity_when_absent():
    assert parse_transform(None) == IDENTITY


def test_parse_transform_rejects_wrong_length():
    with pytest.raises(MeshInputError):
        parse_transform("1 0 0 0 1 0")


def test_parse_transform_rejects_non_numeric():
    with pytest.raises(MeshInputError):
        parse_transform("1 0 0 0 1 0 0 0 1 0 0 nope")


def test_apply_transform_translates():
    t = parse_transform("1 0 0 0 1 0 0 0 1 5 6 7")
    assert apply_transform(t, (1.0, 2.0, 3.0)) == (6.0, 8.0, 10.0)


def test_compose_applies_inner_first():
    scale2 = parse_transform("2 0 0 0 2 0 0 0 2 0 0 0")
    shift = parse_transform("1 0 0 0 1 0 0 0 1 10 0 0")
    # inner=scale, outer=shift → scale then shift
    both = compose(shift, scale2)
    assert apply_transform(both, (1.0, 0.0, 0.0)) == (12.0, 0.0, 0.0)
    # the other order must differ, otherwise we composed wrong
    other = compose(scale2, shift)
    assert apply_transform(other, (1.0, 0.0, 0.0)) == (22.0, 0.0, 0.0)


# ----------------------------------------------------------------- 3mf reading

def test_reads_single_object(tmp_path):
    pieces = read_3mf(simple_3mf(tmp_path))
    assert len(pieces) == 1
    assert pieces[0].name == "Tetra"
    assert pieces[0].tri_count == 4
    assert pieces[0].bbox_mm() == (10.0, 10.0, 10.0)


def test_unit_conversion_to_mm(tmp_path):
    piece = read_3mf(simple_3mf(tmp_path, unit="centimeter"))[0]
    assert piece.bbox_mm() == (100.0, 100.0, 100.0)


def test_unknown_unit_is_rejected(tmp_path):
    with pytest.raises(MeshInputError, match="unit"):
        read_3mf(simple_3mf(tmp_path, unit="furlong"))


def test_build_item_transform_is_applied(tmp_path):
    piece = read_3mf(simple_3mf(tmp_path, transform="2 0 0 0 2 0 0 0 2 0 0 0"))[0]
    assert piece.bbox_mm() == (20.0, 20.0, 20.0)


def test_one_piece_per_build_item(tmp_path):
    pieces = read_3mf(simple_3mf(tmp_path, items=3))
    assert [p.index for p in pieces] == [1, 2, 3]


def test_production_extension_external_objects(tmp_path):
    piece = read_3mf(production_3mf(tmp_path))[0]
    assert piece.tri_count == 4
    xs = [v[0] for tri in piece.triangles for v in tri]
    assert min(xs) == 100.0  # the component's translation was applied


def test_missing_part_is_reported(tmp_path):
    root = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}" xmlns:p="{PROD}">'
            f'<resources><object id="1" type="model"><components>'
            f'<component objectid="7" p:path="/3D/Objects/gone.model"/>'
            f'</components></object></resources>'
            f'<build><item objectid="1"/></build></model>')
    path = _write_3mf(tmp_path / "broken.3mf", {"3D/3dmodel.model": root})
    with pytest.raises(MeshInputError, match="not in the archive"):
        read_3mf(path)


def test_triangle_index_out_of_range_is_reported(tmp_path):
    model = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}">'
             f'<resources><object id="1" type="model"><mesh>'
             f'<vertices><vertex x="0" y="0" z="0"/></vertices>'
             f'<triangles><triangle v1="0" v2="1" v3="2"/></triangles>'
             f'</mesh></object></resources>'
             f'<build><item objectid="1"/></build></model>')
    path = _write_3mf(tmp_path / "bad_index.3mf", {"3D/3dmodel.model": model})
    with pytest.raises(MeshInputError, match="out of range"):
        read_3mf(path)


def test_empty_build_is_reported(tmp_path):
    model = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}">'
             f'<resources><object id="1" type="model">{_mesh_xml()}</object></resources>'
             f'<build/></model>')
    path = _write_3mf(tmp_path / "empty.3mf", {"3D/3dmodel.model": model})
    with pytest.raises(MeshInputError, match="empty <build>"):
        read_3mf(path)


def test_not_a_zip_is_reported(tmp_path):
    fake = tmp_path / "fake.3mf"
    fake.write_text("this is not a zip")
    with pytest.raises(MeshInputError, match="not a zip"):
        read_3mf(fake)


def test_zip_without_root_model_is_reported(tmp_path):
    path = _write_3mf(tmp_path / "nomodel.3mf", {"3D/other.xml": "<x/>"})
    with pytest.raises(MeshInputError, match="no 3D/3dmodel.model"):
        read_3mf(path)


def test_bambu_names_win_over_object_names(tmp_path):
    model = (f'<?xml version="1.0"?><model unit="millimeter" xmlns="{CORE}">'
             f'<resources><object id="1" type="model" name="generic">{_mesh_xml()}</object>'
             f'</resources><build><item objectid="1"/></build></model>')
    config = '<?xml version="1.0"?><config><object id="1">' \
             '<metadata key="name" value="Dragon Egg"/></object></config>'
    path = _write_3mf(tmp_path / "named.3mf", {
        "3D/3dmodel.model": model,
        "Metadata/model_settings.config": config,
    })
    assert read_3mf(path)[0].name == "Dragon Egg"


# --------------------------------------------------------------- public helpers

def test_list_pieces_passes_stl_through(tmp_path):
    stl = write_binary_stl([((0, 0, 0), (1, 0, 0), (0, 1, 0))], tmp_path / "one.stl")
    pieces = list_pieces(stl)
    assert len(pieces) == 1 and pieces[0].name == "one"


def test_list_pieces_rejects_other_formats(tmp_path):
    obj = tmp_path / "thing.obj"
    obj.write_text("v 0 0 0")
    with pytest.raises(MeshInputError, match="unsupported"):
        list_pieces(obj)


def test_to_stl_needs_a_choice_when_ambiguous(tmp_path):
    path = simple_3mf(tmp_path, items=2)
    with pytest.raises(MeshInputError, match="--piece"):
        to_stl(path, tmp_path)


def test_to_stl_rejects_out_of_range_piece(tmp_path):
    path = simple_3mf(tmp_path, items=2)
    with pytest.raises(MeshInputError, match="out of range"):
        to_stl(path, tmp_path, select=5)


def test_to_stl_writes_readable_binary_stl(tmp_path):
    path = simple_3mf(tmp_path)
    stl, label = to_stl(path, tmp_path)
    assert label == "Tetra"
    data = stl.read_bytes()
    count = struct.unpack("<I", data[80:84])[0]
    assert count == 4
    assert len(data) == 84 + 50 * count


def test_to_stl_merge_keeps_every_piece(tmp_path):
    path = simple_3mf(tmp_path, items=3)
    stl, label = to_stl(path, tmp_path, merge=True)
    count = struct.unpack("<I", stl.read_bytes()[80:84])[0]
    assert count == 12
    assert "3 pieces" in label


def test_to_stl_leaves_stl_untouched(tmp_path):
    stl = write_binary_stl([((0, 0, 0), (1, 0, 0), (0, 1, 0))], tmp_path / "keep.stl")
    before = stl.read_bytes()
    out, _ = to_stl(stl, tmp_path)
    assert out == stl and out.read_bytes() == before


def test_binary_stl_normals_are_unit_length(tmp_path):
    stl = write_binary_stl([((0, 0, 0), (2, 0, 0), (0, 2, 0))], tmp_path / "n.stl")
    nx, ny, nz = struct.unpack("<3f", stl.read_bytes()[84:96])
    assert pytest.approx((nx * nx + ny * ny + nz * nz), abs=1e-6) == 1.0
    assert pytest.approx(nz, abs=1e-6) == 1.0

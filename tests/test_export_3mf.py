"""Tests for the minimal 3MF writer."""
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from export_3mf import write_3mf, MeshPart


NS_CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def _make_triangle_part(name="part", color=(1.0, 0.0, 0.0, 1.0)) -> MeshPart:
    return MeshPart(
        name=name,
        color_rgba=color,
        vertices=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        triangles=[(0, 1, 2)],
    )


def test_write_3mf_creates_valid_zip(tmp_path: Path):
    out = tmp_path / "x.3mf"
    write_3mf(out, [_make_triangle_part()])
    assert zipfile.is_zipfile(out)


def test_write_3mf_contains_required_parts(tmp_path: Path):
    out = tmp_path / "x.3mf"
    write_3mf(out, [_make_triangle_part()])
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    assert "[Content_Types].xml" in names
    assert "_rels/.rels" in names
    assert "3D/3dmodel.model" in names


def test_write_3mf_emits_objects_bundle(tmp_path: Path):
    """The Bambu-native flavor bundles all meshes into ONE 3D/Objects/object_1.model
    file with N <object> entries (local ids 1..N). The composite in
    3dmodel.model references each via objectid="K". model_settings.config
    uses those same K values as <part id="K">."""
    out = tmp_path / "x.3mf"
    parts = [
        _make_triangle_part("logo_purple", (0.5, 0.1, 0.8, 1.0)),
        _make_triangle_part("logo_yellow", (1.0, 0.9, 0.3, 1.0)),
    ]
    write_3mf(out, parts)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        bundle = z.read("3D/Objects/object_1.model").decode()
        root_xml = z.read("3D/3dmodel.model").decode()
    assert "3D/Objects/object_1.model" in names
    assert "3D/Objects/object_2.model" not in names
    # Root has no inline meshes — meshes are all in the bundle
    assert "<mesh>" not in root_xml
    # Bundle has 2 <object> entries (one per part)
    bundle_root = ET.fromstring(bundle)
    assert len(bundle_root.findall(f".//{NS_CORE}object")) == 2


def test_write_3mf_declares_filament_colors_in_project_settings(tmp_path: Path):
    """Display colours moved from <basematerials> to project_settings.config —
    Bambu reads them from there for the filament panel."""
    out = tmp_path / "x.3mf"
    parts = [
        _make_triangle_part("p1", (0.5, 0.1, 0.8, 1.0)),
        _make_triangle_part("p2", (1.0, 0.9, 0.3, 1.0)),
        _make_triangle_part("p3", (0.8, 0.2, 0.3, 1.0)),
    ]
    write_3mf(out, parts, filament_slots=[1, 2, 3])
    with zipfile.ZipFile(out) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    assert cfg["filament_colour"][0] == "#801ACC"  # 0.5/0.1/0.8 rounded
    assert cfg["filament_colour"][1] == "#FFE64C"  # 1.0/0.9/0.3
    assert cfg["filament_colour"][2] == "#CC334C"  # 0.8/0.2/0.3


def test_write_3mf_single_item_pointing_at_composite(tmp_path: Path):
    """Final structure: 1 <item> in <build> pointing at the composite object
    that wires together all the per-mesh objects via <components>. The
    per-mesh subtype (normal/modifier/etc.) is declared inside
    model_settings.config — not on the build item."""
    out = tmp_path / "x.3mf"
    parts = [_make_triangle_part(f"p{i}") for i in range(4)]
    write_3mf(out, parts)
    with zipfile.ZipFile(out) as z:
        model_xml = z.read("3D/3dmodel.model")
    root = ET.fromstring(model_xml)
    items = root.findall(f".//{NS_CORE}build/{NS_CORE}item")
    assert len(items) == 1, "build should reference only the composite"
    composites = [o for o in root.findall(f".//{NS_CORE}object")
                  if o.find(f"{NS_CORE}components") is not None]
    assert len(composites) == 1


def test_write_3mf_rejects_empty_parts(tmp_path: Path):
    out = tmp_path / "x.3mf"
    with pytest.raises(ValueError, match="at least one"):
        write_3mf(out, [])


# ----- Bambu-native flavor: extruder/filament pre-assignment via
# Metadata/model_settings.config -----

def test_write_3mf_includes_bambu_model_settings_config(tmp_path: Path):
    """For Bambu to skip the scale dialog and apply filaments automatically,
    the archive must include Metadata/model_settings.config."""
    out = tmp_path / "x.3mf"
    write_3mf(out, [
        _make_triangle_part("coaster_base"),
        _make_triangle_part("logo_purple"),
    ], filament_slots=[1, 2])
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    assert "Metadata/model_settings.config" in names


def test_write_3mf_model_settings_has_part_per_mesh(tmp_path: Path):
    """The composite object inside model_settings.config carries one <part>
    per mesh, each with its own extruder. Subtype defaults to normal_part,
    but logos passed with subtype='modifier_part' should round-trip."""
    out = tmp_path / "x.3mf"
    from export_3mf import MeshPart, SUBTYPE_MODIFIER
    parts = [
        MeshPart("coaster_base", (0.1, 0.1, 0.1, 1.0),
                 [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)]),
        MeshPart("logo_purple", (0.5, 0.1, 0.8, 1.0),
                 [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)],
                 subtype=SUBTYPE_MODIFIER),
    ]
    write_3mf(out, parts, filament_slots=[1, 2])
    with zipfile.ZipFile(out) as z:
        cfg = z.read("Metadata/model_settings.config").decode()
    root = ET.fromstring(cfg)
    parts_by_name = {}
    for p in root.findall(".//part"):
        name = next((m.get("value") for m in p.findall("./metadata")
                     if m.get("key") == "name"), None)
        extruder = next((m.get("value") for m in p.findall("./metadata")
                         if m.get("key") == "extruder"), None)
        if name and extruder:
            parts_by_name[name] = (p.get("subtype"), int(extruder))
    assert parts_by_name == {
        "coaster_base": ("normal_part", 1),
        "logo_purple": ("modifier_part", 2),
    }


def test_write_3mf_model_xml_declares_bambustudio_namespace(tmp_path: Path):
    """The <model> root must carry a BambuStudio:3mfVersion metadata so the
    importer takes the BBS branch instead of treating us as 'external 3MF'."""
    out = tmp_path / "x.3mf"
    write_3mf(out, [_make_triangle_part()], filament_slots=[1])
    with zipfile.ZipFile(out) as z:
        model_xml = z.read("3D/3dmodel.model").decode()
    assert "BambuStudio:3mfVersion" in model_xml


def test_write_3mf_content_types_declares_config(tmp_path: Path):
    """[Content_Types].xml must declare the .config extension or Bambu won't
    parse Metadata/*.config files."""
    out = tmp_path / "x.3mf"
    write_3mf(out, [_make_triangle_part()], filament_slots=[1])
    with zipfile.ZipFile(out) as z:
        ct = z.read("[Content_Types].xml").decode()
    assert 'Extension="config"' in ct


def test_write_3mf_slot_count_must_match_parts(tmp_path: Path):
    """If the caller passes filament_slots, the length must equal len(parts)."""
    out = tmp_path / "x.3mf"
    with pytest.raises(ValueError, match="filament_slots"):
        write_3mf(out, [_make_triangle_part("a"), _make_triangle_part("b")],
                  filament_slots=[1])


def test_write_3mf_default_slots_all_one_when_omitted(tmp_path: Path):
    """Without filament_slots, every part defaults to extruder=1 (slot 1)."""
    out = tmp_path / "x.3mf"
    write_3mf(out, [_make_triangle_part(f"p{i}") for i in range(3)])
    with zipfile.ZipFile(out) as z:
        cfg = z.read("Metadata/model_settings.config").decode()
    root = ET.fromstring(cfg)
    # Now extruders live on <part>, not on <object>.
    extruders = []
    for p in root.findall(".//part"):
        for md in p.findall("./metadata"):
            if md.get("key") == "extruder":
                extruders.append(int(md.get("value")))
    assert extruders == [1, 1, 1]


# ----- Bambu Studio "is from Bambu Lab" gates -----

def test_write_3mf_declares_application_bambu_prefix(tmp_path: Path):
    """The Application metadata MUST start with 'BambuStudio-' for the
    importer to set m_is_bbl_3mf=true (bbs_3mf.cpp:4079). Without this the
    'load geometry data and color data only' dialog fires."""
    out = tmp_path / "x.3mf"
    write_3mf(out, [_make_triangle_part()], filament_slots=[1])
    with zipfile.ZipFile(out) as z:
        model_xml = z.read("3D/3dmodel.model").decode()
    assert 'name="Application"' in model_xml
    assert "BambuStudio-" in model_xml


def test_write_3mf_includes_project_settings_config(tmp_path: Path):
    """Even with the Application tag, Plater.cpp checks that
    Metadata/project_settings.config exists and parses as JSON. Without it
    config_loaded ends up empty and the dialog fires anyway."""
    out = tmp_path / "x.3mf"
    write_3mf(out, [_make_triangle_part()], filament_slots=[1])
    with zipfile.ZipFile(out) as z:
        assert "Metadata/project_settings.config" in z.namelist()
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    # Three required keys validated before the rest is accepted.
    assert cfg["version"]
    assert cfg["name"]
    assert cfg["from"]


def test_write_3mf_project_filaments_match_max_slot(tmp_path: Path):
    """filament_colour must have at least one entry per AMS slot referenced.
    If we assign extruder=4 to a part, the colour array needs ≥4 entries."""
    out = tmp_path / "x.3mf"
    parts = [
        _make_triangle_part("a", (0.1, 0.1, 0.1, 1.0)),
        _make_triangle_part("b", (0.5, 0.1, 0.9, 1.0)),
        _make_triangle_part("c", (1.0, 0.9, 0.3, 1.0)),
        _make_triangle_part("d", (0.8, 0.2, 0.3, 1.0)),
    ]
    write_3mf(out, parts, filament_slots=[1, 2, 3, 4])
    with zipfile.ZipFile(out) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    assert len(cfg["filament_colour"]) == 4
    assert len(cfg["filament_settings_id"]) == 4
    assert len(cfg["filament_type"]) == 4


def test_write_3mf_project_filaments_get_part_colors(tmp_path: Path):
    """The colour at slot i in filament_colour should reflect the display
    color of the part assigned to slot i (better UX in Bambu's filament panel)."""
    out = tmp_path / "x.3mf"
    parts = [
        _make_triangle_part("base", (0.1, 0.1, 0.1, 1.0)),     # slot 1, black
        _make_triangle_part("purple", (0.475, 0.102, 0.851, 1.0)),  # slot 2
    ]
    write_3mf(out, parts, filament_slots=[1, 2])
    with zipfile.ZipFile(out) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    # Slot 1 should be the dark colour, slot 2 the purple
    assert cfg["filament_colour"][0] == "#1A1A1A"
    assert cfg["filament_colour"][1] == "#791AD9"

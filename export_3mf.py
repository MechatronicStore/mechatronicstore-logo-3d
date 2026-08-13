"""3MF writer for Bambu Studio with multi-filament + modifier_part support.

Structure mirrors what Bambu Studio / OrcaSlicer themselves produce
(reverse-engineered from auto_pa_line_single.3mf in OrcaSlicer resources):

    project.3mf
    ├── [Content_Types].xml
    ├── _rels/.rels
    ├── 3D/
    │   ├── 3dmodel.model            ← root, references external object_N.model files
    │   ├── _rels/
    │   │   └── 3dmodel.model.rels   ← declares each object_N.model as a part
    │   └── Objects/
    │       ├── object_1.model       ← coaster_base mesh
    │       ├── object_2.model       ← logo_purple mesh
    │       ├── object_3.model       ← logo_yellow mesh
    │       └── object_4.model       ← logo_red mesh
    └── Metadata/
        ├── model_settings.config    ← composite object id + part subtype/extruder
        └── project_settings.config  ← filament_colour/type/settings arrays

Key things the spec-minimal version got wrong (and this one gets right):

  1. **Meshes live in their own files**. The root 3dmodel.model only carries
     a composite <object> with <components p:path="/3D/Objects/object_N.model">
     references. Bambu can't bind per-volume extruder settings to inline
     meshes — the production namespace + external files are required.

  2. **UUIDs everywhere**. Every <object>, <component>, and <build><item>
     gets a uuid4. Bambu uses these for internal volume tracking; without
     them the per-part extruder mapping doesn't take effect.

  3. **Part ids are LOCAL (1..N) inside the object**, not the global
     object_id. The composite object lists its meshes as <part id="1">,
     <part id="2">, etc — the index into the parent's <components> list.

  4. **3D/_rels/3dmodel.model.rels** declares each external object_N.model
     as a relationship. Without it Bambu can resolve the p:path strings
     but doesn't load the meshes.
"""
from __future__ import annotations

import datetime
import json
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
NS_BAMBU = "http://schemas.bambulab.com/package/2021"

BAMBU_APPLICATION_TAG = "BambuStudio-02.00.02.01"

SUBTYPE_NORMAL = "normal_part"
SUBTYPE_MODIFIER = "modifier_part"


@dataclass(frozen=True)
class MeshPart:
    """One filament-coloured chunk of geometry.

    The vertices are expected in WORLD coordinates (the writer will
    re-center each mesh on its bbox center and push the translation into
    the component/part transforms — that's what Bambu reference files do,
    and the slicer's filament-per-part binding depends on it).
    """
    name: str
    color_rgba: tuple[float, float, float, float]
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]
    subtype: str = SUBTYPE_NORMAL

    def center(self) -> tuple[float, float, float]:
        if not self.vertices:
            return (0.0, 0.0, 0.0)
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        zs = [v[2] for v in self.vertices]
        return ((min(xs) + max(xs)) / 2,
                (min(ys) + max(ys)) / 2,
                (min(zs) + max(zs)) / 2)


def _color_hex(rgba: tuple[float, float, float, float]) -> str:
    r, g, b, a = rgba
    return (
        f"#{int(round(r * 255)):02X}"
        f"{int(round(g * 255)):02X}"
        f"{int(round(b * 255)):02X}"
        f"{int(round(a * 255)):02X}"
    )


def _color_hex_rgb(rgba: tuple[float, float, float, float]) -> str:
    """Like _color_hex but without alpha (project_settings uses 6-char form)."""
    r, g, b, _ = rgba
    return (
        f"#{int(round(r * 255)):02X}"
        f"{int(round(g * 255)):02X}"
        f"{int(round(b * 255)):02X}"
    )


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _build_objects_bundle_xml(parts: list[MeshPart],
                              object_uuids: list[str],
                              centers: list[tuple[float, float, float]]) -> bytes:
    """Emit 3D/Objects/object_1.model — a SINGLE file containing N <object>
    entries (local ids 1..N), one per mesh. The composite in 3dmodel.model
    references each via <component p:path="/3D/Objects/object_1.model"
    objectid="K"/>, and model_settings.config uses those same K values as
    <part id="K"/>.

    Putting every mesh in one bundle file (vs N separate files) is what the
    Orca/Bambu reference 3MFs do and what makes the part→volume binding in
    model_settings.config actually take effect.
    """
    model = ET.Element(
        "model",
        {
            "unit": "millimeter",
            "xml:lang": "en-US",
            "xmlns": NS_CORE,
            "xmlns:BambuStudio": NS_BAMBU,
            "xmlns:p": NS_PROD,
            "requiredextensions": "p",
        },
    )
    meta = ET.SubElement(model, "metadata", {"name": "BambuStudio:3mfVersion"})
    meta.text = "1"
    resources = ET.SubElement(model, "resources")
    for i, (part, obj_uuid, center) in enumerate(zip(parts, object_uuids, centers)):
        obj = ET.SubElement(resources, "object", {
            "id": str(i + 1),
            "p:UUID": obj_uuid,
            "type": "model",
        })
        mesh = ET.SubElement(obj, "mesh")
        verts = ET.SubElement(mesh, "vertices")
        # Re-center: store the mesh around the origin and let the
        # <component>/<part> transforms carry the world position. This
        # matches how Bambu itself saves files and is what the slicer's
        # filament-per-part binding expects.
        cx, cy, cz = center
        for x, y, z in part.vertices:
            ET.SubElement(verts, "vertex",
                          {"x": f"{x - cx:.6f}",
                           "y": f"{y - cy:.6f}",
                           "z": f"{z - cz:.6f}"})
        tris = ET.SubElement(mesh, "triangles")
        for v1, v2, v3 in part.triangles:
            ET.SubElement(tris, "triangle",
                          {"v1": str(v1), "v2": str(v2), "v3": str(v3)})
    return ET.tostring(model, encoding="UTF-8", xml_declaration=True)


def _build_root_model_xml(parts: list[MeshPart], composite_id: int,
                          composite_uuid: str,
                          component_uuids: list[str],
                          item_uuid: str,
                          build_uuid: str,
                          plate_offset: tuple[float, float, float],
                          centers: list[tuple[float, float, float]]) -> bytes:
    """Emit 3D/3dmodel.model — the composite object that wires together every
    /3D/Objects/object_N.model via p:path components, plus the <build><item>
    that positions the model on the plate."""
    model = ET.Element(
        "model",
        {
            "unit": "millimeter",
            "xml:lang": "en-US",
            "xmlns": NS_CORE,
            "xmlns:BambuStudio": NS_BAMBU,
            "xmlns:p": NS_PROD,
            "requiredextensions": "p",
        },
    )
    today = datetime.date.today().isoformat()
    for name, value in [
        ("Application", BAMBU_APPLICATION_TAG),
        ("BambuStudio:3mfVersion", "1"),
        ("CreationDate", today),
        ("ModificationDate", today),
    ]:
        m = ET.SubElement(model, "metadata", {"name": name})
        m.text = value
    resources = ET.SubElement(model, "resources")
    composite = ET.SubElement(resources, "object", {
        "id": str(composite_id),
        "p:UUID": composite_uuid,
        "type": "model",
    })
    components = ET.SubElement(composite, "components")
    # Each component picks one of the meshes from the single bundle via
    # objectid (matches the local <object id> inside the bundle, and is the
    # <part id> in model_settings.config). The 4x3 row-major transform
    # column 3 holds the translation that brings the origin-centered mesh
    # back to its world position.
    for i, _ in enumerate(parts):
        cx, cy, cz = centers[i]
        component_transform = (
            f"1 0 0 0 1 0 0 0 1 {cx:.6f} {cy:.6f} {cz:.6f}"
        )
        ET.SubElement(components, "component", {
            "p:path": "/3D/Objects/object_1.model",
            "objectid": str(i + 1),
            "p:UUID": component_uuids[i],
            "transform": component_transform,
        })
    build = ET.SubElement(model, "build", {"p:UUID": build_uuid})
    dx, dy, dz = plate_offset
    item_transform = f"1 0 0 0 1 0 0 0 1 {dx} {dy} {dz}"
    ET.SubElement(build, "item", {
        "objectid": str(composite_id),
        "p:UUID": item_uuid,
        "transform": item_transform,
        "printable": "1",
    })
    return ET.tostring(model, encoding="UTF-8", xml_declaration=True)


def _build_model_rels_xml() -> bytes:
    """3D/_rels/3dmodel.model.rels — declares the single bundle file as a
    3dmodel relationship. Without this Bambu can resolve the p:path strings
    but doesn't actually load the external meshes."""
    rels = ET.Element("Relationships", {
        "xmlns": "http://schemas.openxmlformats.org/package/2006/relationships",
    })
    ET.SubElement(rels, "Relationship", {
        "Target": "/3D/Objects/object_1.model",
        "Id": "rel-1",
        "Type": "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel",
    })
    return ET.tostring(rels, encoding="UTF-8", xml_declaration=True)


def _build_model_settings_config(parts: list[MeshPart],
                                  filament_slots: list[int],
                                  composite_id: int,
                                  centers: list[tuple[float, float, float]]) -> bytes:
    """Composite object with N <part> entries (ids 1..N matching component
    objectids in the composite). Each part carries its subtype, extruder,
    full 4x4 matrix with the world-space translation, and mesh_stat
    metadata. Plus a <plate> instance and <assemble_item> to satisfy what
    Bambu writes when it saves natively.
    """
    root = ET.Element("config")
    obj_el = ET.SubElement(root, "object", {"id": str(composite_id)})
    base_name = parts[0].name if parts else "model"
    total_faces = sum(len(p.triangles) for p in parts)
    ET.SubElement(obj_el, "metadata", {"key": "name", "value": base_name})
    ET.SubElement(obj_el, "metadata",
                  {"key": "extruder", "value": str(filament_slots[0] if filament_slots else 1)})
    ET.SubElement(obj_el, "metadata", {"face_count": str(total_faces)})
    for i, (part, slot, center) in enumerate(zip(parts, filament_slots, centers)):
        cx, cy, cz = center
        # 4x4 row-major matrix with the world translation in column 3.
        # Bambu uses this to size the per-part bbox.
        matrix_str = (
            f"1 0 0 {cx:.6f} "
            f"0 1 0 {cy:.6f} "
            f"0 0 1 {cz:.6f} "
            f"0 0 0 1"
        )
        part_el = ET.SubElement(obj_el, "part", {
            "id": str(i + 1),
            "subtype": part.subtype,
        })
        ET.SubElement(part_el, "metadata",
                      {"key": "name", "value": part.name})
        ET.SubElement(part_el, "metadata",
                      {"key": "matrix", "value": matrix_str})
        ET.SubElement(part_el, "metadata",
                      {"key": "extruder", "value": str(slot)})
        ET.SubElement(part_el, "mesh_stat", {
            "face_count": str(len(part.triangles)),
            "edges_fixed": "0",
            "degenerate_facets": "0",
            "facets_removed": "0",
            "facets_reversed": "0",
            "backwards_edges": "0",
        })

    # <plate> with a <model_instance> linking the composite to plate 1.
    plate_el = ET.SubElement(root, "plate")
    for key, value in [
        ("plater_id", "1"),
        ("plater_name", ""),
        ("locked", "false"),
        ("filament_map_mode", "Auto For Flush"),
        ("thumbnail_file", "Metadata/plate_1.png"),
    ]:
        ET.SubElement(plate_el, "metadata", {"key": key, "value": value})
    instance = ET.SubElement(plate_el, "model_instance")
    for key, value in [
        ("object_id", str(composite_id)),
        ("instance_id", "0"),
        ("identify_id", "1"),
    ]:
        ET.SubElement(instance, "metadata", {"key": key, "value": value})

    # <assemble> registers the composite as a printable assembly.
    assemble_el = ET.SubElement(root, "assemble")
    ET.SubElement(assemble_el, "assemble_item", {
        "object_id": str(composite_id),
        "instance_id": "0",
        "transform": "1 0 0 0 1 0 0 0 1 0 0 0",
        "offset": "0 0 0",
    })
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


_TEMPLATE_PATH = Path(__file__).parent / "project_settings_template.json"


def _build_project_settings_json(parts: list[MeshPart],
                                  filament_slots: list[int]) -> bytes:
    """Load the full Bambu Studio template and override the per-filament
    arrays for our N slots. The slicer needs all ~558 keys present (with
    valid types and matching array lengths), so we cannot synthesize the
    file from scratch — we mutate a known-good baseline.
    """
    with _TEMPLATE_PATH.open() as f:
        cfg = json.load(f)
    n_slots = max(filament_slots) if filament_slots else 1
    colours = ["#808080"] * n_slots
    for part, slot in zip(parts, filament_slots):
        idx = slot - 1
        if 0 <= idx < n_slots:
            colours[idx] = _color_hex_rgb(part.color_rgba)
    cfg["filament_colour"] = colours
    # Keep the template's filament_settings_id / filament_type for slot 1,
    # then duplicate the slot-1 entries up to n_slots so the arrays align
    # with the colour list.
    cfg["filament_type"] = ["PLA"] * n_slots
    base_settings_id = cfg.get("filament_settings_id", ["Generic PLA"])[0]
    cfg["filament_settings_id"] = [base_settings_id] * n_slots
    # Other per-filament arrays must also match n_slots — pad with first value.
    for key, default in [
        ("filament_density", "1.24"),
        ("filament_diameter", "1.75"),
        ("filament_flow_ratio", "1"),
        ("filament_minimal_purge_on_wipe_tower", "15"),
        ("filament_max_volumetric_speed", "12"),
        ("filament_cost", "20"),
        ("activate_air_filtration", "0"),
    ]:
        if key in cfg and isinstance(cfg[key], list):
            value = cfg[key][0] if cfg[key] else default
            cfg[key] = [value] * n_slots
    return json.dumps(cfg, indent=4).encode("utf-8")


_CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="png" ContentType="image/png"/>
 <Default Extension="config" ContentType="application/vnd.bambulab-package.3dmodel-config+xml"/>
</Types>
"""

# Minimum slice_info that Bambu accepts as "yes this came from us"
_SLICE_INFO = b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <header>
    <header_item key="X-BBL-Client-Type" value="slicer"/>
    <header_item key="X-BBL-Client-Version" value="02.07.00.55"/>
  </header>
</config>
"""

# Empty filament_sequence — Bambu fills it in on slice
_FILAMENT_SEQUENCE = b'{"plate_1":{"nozzle_sequence":[],"optimal_assignment":[],"sequence":[]}}'


_RELS = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def write_3mf(out_path: Path | str, parts: list[MeshPart],
              filament_slots: list[int] | None = None,
              plate_offset: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
    """Write a Bambu-native 3MF that imports as ONE multi-part model with
    per-part filament pre-assignment.

    Args:
        out_path: destination .3mf path.
        parts: ordered list of MeshPart. The FIRST part is the printable
            "normal_part" (must reach Z=0); the rest are usually modifier_parts
            (override filament in their region).
        filament_slots: parallel list of 1-based AMS slots, one per part.
            Defaults to all slot 1.
        plate_offset: translation applied via <build><item> transform.
            Lets the caller position the model on the plate without
            modifying the mesh vertices.
    """
    if not parts:
        raise ValueError("write_3mf needs at least one MeshPart")
    if filament_slots is None:
        filament_slots = [1] * len(parts)
    elif len(filament_slots) != len(parts):
        raise ValueError(
            f"filament_slots length ({len(filament_slots)}) must match "
            f"parts length ({len(parts)})"
        )

    composite_id = len(parts) + 2  # parts use ids 2..N+1 conceptually; composite at N+2
    composite_uuid = _new_uuid()
    component_uuids = [_new_uuid() for _ in parts]
    item_uuid = _new_uuid()
    build_uuid = _new_uuid()
    object_uuids = [_new_uuid() for _ in parts]

    # Each part's world-space center becomes the translation of its
    # <component>/<part>. The mesh itself ends up origin-centered in the
    # bundle so Bambu's slicer can bind extruder=K to the right volume.
    centers = [p.center() for p in parts]

    root_xml = _build_root_model_xml(parts, composite_id, composite_uuid,
                                     component_uuids, item_uuid, build_uuid,
                                     plate_offset, centers)
    bundle_xml = _build_objects_bundle_xml(parts, object_uuids, centers)
    model_rels_xml = _build_model_rels_xml()
    settings_xml = _build_model_settings_config(parts, filament_slots,
                                                composite_id, centers)
    project_json = _build_project_settings_json(parts, filament_slots)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("3D/3dmodel.model", root_xml)
        z.writestr("3D/_rels/3dmodel.model.rels", model_rels_xml)
        z.writestr("3D/Objects/object_1.model", bundle_xml)
        z.writestr("Metadata/model_settings.config", settings_xml)
        z.writestr("Metadata/project_settings.config", project_json)
        z.writestr("Metadata/slice_info.config", _SLICE_INFO)
        z.writestr("Metadata/filament_sequence.json", _FILAMENT_SEQUENCE)

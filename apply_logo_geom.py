"""Geometry-based logo on STL: extrude each SVG color as a real 3D mesh.

Approach (replaces the texture+UV pipeline that produced blurry-banded prints):
  1. Import the STL coaster.
  2. Import the SVG as curves (one curve per path, grouped by fill color).
  3. Cluster the 8+ SVG materials into a small set of "logical" colors
     (purple "mechatronic", yellow "STORE.CL", red cursor accent, black).
  4. Convert curves to meshes, solidify (extrude) each color group on Z.
  5. Center + scale the logo on the coaster top face.
  6. Export GLB with one mesh-per-color (Bambu Studio prints each mesh with
     a separate AMS filament — clean letters, no per-triangle clustering).

Usage (called by logo3d.py):
  blender --background --python apply_logo_geom.py -- \
      --stl coaster.stl \
      --svg logo.svg \
      --out coaster.glb \
      --logo-size-mm 70 \
      --logo-height-mm 0.4
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geom_helpers import (  # noqa: E402
    COLOR_GROUPS,
    classify_material_color,
    pick_largest_index,
    slot_for_mesh,
)
from export_3mf import MeshPart, SUBTYPE_MODIFIER, SUBTYPE_NORMAL, write_3mf  # noqa: E402

# Color name → AMS slot. logo_circuits gets a runtime-decided slot based on
# the --circuit-color flag below.
CIRCUIT_COLOR_TO_SLOT = {"purple": 2, "yellow": 3, "red": 4}


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--stl", required=True, type=Path)
    p.add_argument("--svg", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--logo-size-mm", type=float, default=70.0,
                   help="Logo width on the coaster top face")
    p.add_argument("--logo-height-mm", type=float, default=0.4,
                   help="Extrusion height of each logo color above the coaster top")
    p.add_argument("--offset-x-mm", type=float, default=0.0,
                   help="Move the logo along X from the center of the top face")
    p.add_argument("--offset-y-mm", type=float, default=0.0,
                   help="Move the logo along Y from the center of the top face")
    p.add_argument("--circuits", action="store_true",
                   help="Add a procedural PCB-style decoration around the logo "
                        "(outer ring + vias + radial traces).")
    p.add_argument("--circuit-color", default="yellow",
                   choices=["yellow", "purple", "red"],
                   help="Filament group for the circuit decoration (default: yellow)")
    p.add_argument("--also-3mf", action="store_true",
                   help="Also write a sibling .3mf file (Bambu Studio's native "
                        "multicolor format) alongside the GLB.")
    p.add_argument("--engrave", action="store_true",
                   help="Sink the logo INTO the top face (negative relief) "
                        "instead of extruding it upward. Useful for keychains "
                        "and badges where you want the letters flush with the "
                        "surface. Bambu prints the colored letters embedded in "
                        "the base layer.")
    p.add_argument("--also-stls", action="store_true",
                   help="Also export each mesh as its own STL file next to "
                        "the GLB (coaster_base.stl, logo_purple.stl, etc.). "
                        "Friendly format for CAD viewers (Fusion 360, FreeCAD, "
                        "OnShape) and clean import + Assemble in Bambu Studio.")
    return p.parse_args(argv)


# COLOR_GROUPS + classify_material_color live in geom_helpers (Blender-free).

# Final display colors (sRGB hex) per group — what Bambu/viewers show.
# These are baked into the material baseColor so the user sees the right thing.
GROUP_DISPLAY = {
    "purple": (0.475, 0.102, 0.851, 1.0),   # #791ad9
    "yellow": (1.000, 0.898, 0.275, 1.0),   # #ffe546
    "red":    (0.839, 0.200, 0.290, 1.0),   # #d6334a
    "black":  (0.102, 0.102, 0.102, 1.0),   # #1a1a1a
}


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_stl(path: Path) -> bpy.types.Object:
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=str(path))
    else:
        bpy.ops.import_mesh.stl(filepath=str(path))
    obj = bpy.context.selected_objects[0]
    obj.name = "coaster_base"
    return obj


def import_svg_grouped(svg_path: Path) -> dict[str, list[str]]:
    """Import the SVG and return {group_name: [curve_object_names]}."""
    before = set(o.name for o in bpy.data.objects)
    bpy.ops.import_curve.svg(filepath=str(svg_path))
    new_objects = [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]
    print(f"[geom] SVG produced {len(new_objects)} curves")

    groups: dict[str, list[str]] = {}
    for obj in new_objects:
        if obj.type != "CURVE":
            continue
        # SVG import puts the fill color in material_slots[0].material.diffuse_color
        if not obj.material_slots or not obj.material_slots[0].material:
            continue
        c = obj.material_slots[0].material.diffuse_color
        group = classify_material_color((c[0], c[1], c[2]))
        groups.setdefault(group, []).append(obj.name)
    for g, names in groups.items():
        print(f"[geom]   group '{g}': {len(names)} curves")
    return groups


def _curve_bbox_area(obj: bpy.types.Object) -> float:
    """Approx footprint area of a 2D curve via its splines' control points."""
    xs, ys = [], []
    for spline in obj.data.splines:
        for bp in spline.bezier_points:
            xs.append(bp.co.x); ys.append(bp.co.y)
        for pt in spline.points:
            xs.append(pt.co.x); ys.append(pt.co.y)
    if not xs:
        return 0.0
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def filter_keep_largest(curve_names: list[str]) -> list[str]:
    """For the cursor's red group: the main arrow is one big curve and the
    "click motion lines" are 3-4 tiny curves around it. Keep only the largest;
    the small ones won't be visible at print scale anyway.

    Safety: if every curve reports area 0 (e.g. SVG import quirk that leaves
    control points at the origin), we keep ALL curves instead of arbitrarily
    deleting the others. Losing the cursor entirely is worse than printing a
    few extra strokes.
    """
    if len(curve_names) <= 1:
        return curve_names
    names = [n for n in curve_names if n in bpy.data.objects]
    areas = [_curve_bbox_area(bpy.data.objects[n]) for n in names]
    idx = pick_largest_index(areas)
    if idx is None:
        print(f"[geom] cursor cleanup: all curves have zero bbox area, keeping all {len(names)}")
        return names
    keep = names[idx]
    for n in names:
        if n == keep:
            continue
        obj = bpy.data.objects.get(n)
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)
    print(f"[geom] cursor cleanup: kept '{keep}', removed {len(names) - 1} motion-line curves")
    return [keep]


def _give_stroke_curves_thickness(curve_names: list[str],
                                    stroke_thickness: float = 0.002) -> None:
    """For SVG paths imported as strokes (open curves with no fill), the
    bezier control points lie on the centerline so the bbox is 1-dimensional
    and conversion to mesh produces degenerate geometry. Setting
    bevel_depth on those curves makes Blender mesh them as a swept tube of
    the given radius, which after scaling becomes a visible solid trace.

    Threshold for "this curve is a stroke": bbox area <= 1e-9 (curves whose
    control points share an X or Y coordinate). Other curves (proper filled
    paths) are left untouched.
    """
    for name in curve_names:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "CURVE":
            continue
        area = _curve_bbox_area(obj)
        if area <= 1e-9:
            obj.data.bevel_depth = stroke_thickness
            obj.data.fill_mode = "FULL"


def curves_to_mesh(curve_names: list[str], target_name: str) -> bpy.types.Object | None:
    """Join curves, convert to mesh. Returns the resulting mesh object."""
    if not curve_names:
        return None
    bpy.ops.object.select_all(action="DESELECT")
    first = None
    for n in curve_names:
        obj = bpy.data.objects.get(n)
        if obj is None:
            continue
        obj.select_set(True)
        if first is None:
            first = obj
            bpy.context.view_layer.objects.active = obj
    if first is None:
        return None
    # Bevel/extrude on the curve itself produces clean planar mesh after conversion.
    bpy.ops.object.join()
    joined = bpy.context.active_object
    joined.name = target_name + "_curve"
    # Convert curve → mesh (filled). The SVG import gives 2D Bézier curves with
    # closed loops — conversion produces filled n-gons that we then triangulate.
    bpy.ops.object.convert(target="MESH")
    mesh_obj = bpy.context.active_object
    mesh_obj.name = target_name
    return mesh_obj


def extrude_mesh(obj: bpy.types.Object, height_mm: float) -> None:
    """Extrude the planar mesh up by height_mm in +Z. Uses bmesh for control."""
    if not obj or obj.type != "MESH":
        return
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    # Ensure consistent face normals
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    # Extrude all faces
    ret = bmesh.ops.extrude_face_region(bm, geom=bm.faces[:])
    verts_translate = [v for v in ret["geom"] if isinstance(v, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=verts_translate, vec=(0.0, 0.0, height_mm))
    bm.to_mesh(me)
    bm.free()
    me.update()


def report_support(logo_objects: list[bpy.types.Object], coaster: bpy.types.Object,
                   top_z: float, tol_mm: float = 0.05) -> float:
    """Warn when the logo would hang in mid-air, and return the covered fraction.

    Parts downloaded from model sites are often hollow or frame-shaped, so the
    XY center of the bounding box is not necessarily on top of any material.
    A raised logo placed there prints as a floating island. We shoot a ray
    straight down under a sample of the logo's own vertices and check whether
    the part's surface is really up at top_z.
    """
    inv = coaster.matrix_world.inverted()
    samples = []
    for obj in logo_objects:
        wm = obj.matrix_world
        verts = list(obj.data.vertices)
        step = max(1, len(verts) // 120)
        samples += [wm @ verts[i].co for i in range(0, len(verts), step)]
    if not samples:
        return 1.0

    hits = 0
    misses = 0          # nothing at all below: the logo hangs over open air
    lower = 0           # material, but further down than the logo sits
    worst_gap = 0.0
    for point in samples:
        origin = inv @ Vector((point.x, point.y, top_z + 5.0))
        hit, loc, _, _ = coaster.ray_cast(origin, Vector((0.0, 0.0, -1.0)))
        if not hit:
            misses += 1
            continue
        surface_z = (coaster.matrix_world @ loc).z
        gap = top_z - surface_z
        if gap <= tol_mm:
            hits += 1
        else:
            lower += 1
            worst_gap = max(worst_gap, gap)

    covered = hits / len(samples)
    if covered >= 0.99:
        print(f"[geom] support check: logo fully lands on material ({covered:.0%})")
        return covered

    print(f"[geom] WARNING support check: only {covered:.0%} of the logo sits on the "
          f"top surface.")
    if misses:
        print(f"[geom]   {misses / len(samples):.0%} of it hangs over open air — this part "
              f"is hollow, frame-shaped, or the logo runs past the edge.")
    if lower:
        print(f"[geom]   {lower / len(samples):.0%} of it floats above a lower surface "
              f"(up to {worst_gap:.1f} mm of empty space underneath).")
    print("[geom]   It would print as a floating island. Move it with --offset-x-mm / "
          "--offset-y-mm, or shrink it with --logo-size-mm.")
    return covered


def fit_logo_on_top(logo_objects: list[bpy.types.Object], coaster: bpy.types.Object,
                    logo_size_mm: float, logo_height_mm: float,
                    engrave: bool = False,
                    offset_x_mm: float = 0.0, offset_y_mm: float = 0.0) -> None:
    """Center the union of logo objects over the coaster top and scale to fit
    a logo_size_mm-wide footprint.

    Z placement:
      - ``engrave=False`` (default): logo sits ON TOP of the coaster, in
        z ∈ [top_z, top_z + logo_height_mm] — protruding relief.
      - ``engrave=True``: logo is SUNK INTO the top face, in
        z ∈ [top_z - logo_height_mm, top_z] — flush negative relief.
        Bambu prints the colored letters embedded inside the last
        ``logo_height_mm`` mm of the base.
    """
    if not logo_objects:
        return
    # Coaster top Z (world)
    coaster_wm = coaster.matrix_world
    top_z = max((coaster_wm @ v.co).z for v in coaster.data.vertices)
    # Compute current bbox of all logo geometry combined
    xs, ys = [], []
    for obj in logo_objects:
        wm = obj.matrix_world
        for v in obj.data.vertices:
            cv = wm @ v.co
            xs.append(cv.x); ys.append(cv.y)
    if not xs:
        return
    cur_w = max(xs) - min(xs)
    cur_h = max(ys) - min(ys)
    cur_cx = (min(xs) + max(xs)) / 2
    cur_cy = (min(ys) + max(ys)) / 2
    # Pick the larger dimension to drive the scale. If both axes are zero
    # the logo collapsed to a point and we'd produce an infinitely large
    # mesh — abort instead of dividing by 1e-6 and writing a broken file.
    ref = max(cur_w, cur_h)
    if ref < 1e-6:
        raise RuntimeError("Logo geometry has zero extent; check the SVG paths")
    scale = logo_size_mm / ref
    print(f"[geom] logo bbox before: {cur_w:.2f} x {cur_h:.2f}, scaling x{scale:.3f}")

    # Coaster XY center (world)
    cxs = [(coaster_wm @ v.co).x for v in coaster.data.vertices]
    cys = [(coaster_wm @ v.co).y for v in coaster.data.vertices]
    target_cx = (min(cxs) + max(cxs)) / 2 + offset_x_mm
    target_cy = (min(cys) + max(cys)) / 2 + offset_y_mm
    if offset_x_mm or offset_y_mm:
        print(f"[geom] logo offset: x{offset_x_mm:+.1f} y{offset_y_mm:+.1f} mm")

    # Apply (scale + translate) to each logo object via direct mesh transform.
    # We move first so the geometry centers on origin, then scale, then translate.
    # For engrave mode we sink the logo so its TOP face sits flush with the
    # coaster's top — the extruded body extends downward into the base.
    z_offset = top_z - logo_height_mm if engrave else top_z
    for obj in logo_objects:
        for v in obj.data.vertices:
            v.co.x = (v.co.x - cur_cx) * scale + target_cx
            v.co.y = (v.co.y - cur_cy) * scale + target_cy
            v.co.z = z_offset + v.co.z
        obj.data.update()

    report_support(logo_objects, coaster, top_z)


def assign_group_material(obj: bpy.types.Object, group: str) -> None:
    """Replace the SVG's auto-generated material with a clean group material
    using the canonical display color. Always refreshes the color values so a
    cached material from a previous run can't carry a stale baseColor into
    the new export.
    """
    if obj is None:
        return
    mat_name = f"mecha_{group}"
    rgba = GROUP_DISPLAY[group]
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(name=mat_name)
    if not mat.use_nodes:
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.7
    mat.diffuse_color = rgba
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def assign_coaster_material(obj: bpy.types.Object) -> None:
    assign_group_material(obj, "black")
    obj.data.materials[0].name = "mecha_coaster"


def make_circuit_decoration(coaster: bpy.types.Object, logo_size_mm: float,
                             logo_height_mm: float, color_group: str) -> list[bpy.types.Object]:
    """Generate a procedural PCB-style decoration on the coaster top:
      - one thin outer ring near the bezel
      - 8 small circular "vias" arranged on a wider ring
      - 8 short radial traces connecting vias toward the ring

    Returns the list of created mesh objects (already extruded, materials
    assigned). The pattern occupies the donut between the logo and the
    coaster edge — it won't overlap with the lettering.
    """
    import math
    coaster_wm = coaster.matrix_world
    cxs = [(coaster_wm @ v.co).x for v in coaster.data.vertices]
    cys = [(coaster_wm @ v.co).y for v in coaster.data.vertices]
    zs  = [(coaster_wm @ v.co).z for v in coaster.data.vertices]
    cx = (min(cxs) + max(cxs)) / 2
    cy = (min(cys) + max(cys)) / 2
    radius = (max(cxs) - min(cxs)) / 2          # coaster outer radius
    top_z  = max(zs)
    # Layout: place everything in the band between (logo_radius + margin) and
    # the coaster bezel, both 0.4mm extruded above the top face.
    logo_r = logo_size_mm / 2.0
    band_inner = logo_r + 3.0                   # leave 3mm gap to the lettering
    band_outer = radius - 3.0                   # leave 3mm gap to the rim
    ring_r     = (band_inner + band_outer) / 2  # main decorative ring radius
    via_r      = 1.2                            # via pad radius (2.4mm diameter)
    trace_w    = 0.8                            # radial trace width
    trace_len  = (band_outer - band_inner) * 0.7

    objects: list[bpy.types.Object] = []

    # --- Outer thin ring (built as cylinder-minus-cylinder so it sits flat) ---
    # The previous version used primitive_torus_add and tried to clamp Z onto
    # [0, logo_height_mm] vertex by vertex, but that warped the bottom half
    # of the torus into a flat disc with curved top. A boolean difference
    # between two coaxial cylinders gives a clean planar ring.
    ring_width = 1.0
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=128, radius=band_outer - 0.5, depth=logo_height_mm,
        location=(cx, cy, top_z + logo_height_mm / 2),
    )
    ring_outer = bpy.context.active_object
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=128, radius=band_outer - 0.5 - ring_width, depth=logo_height_mm * 2,
        location=(cx, cy, top_z + logo_height_mm / 2),
    )
    ring_inner = bpy.context.active_object
    bool_mod = ring_outer.modifiers.new(name="ring_cut", type="BOOLEAN")
    bool_mod.operation = "DIFFERENCE"
    bool_mod.object = ring_inner
    bpy.context.view_layer.objects.active = ring_outer
    bpy.ops.object.modifier_apply(modifier="ring_cut")
    bpy.data.objects.remove(ring_inner, do_unlink=True)
    ring_outer.name = "circuit_ring"
    objects.append(ring_outer)

    # --- Vias on the inner band ---
    n_vias = 8
    for i in range(n_vias):
        angle = 2 * math.pi * i / n_vias + math.pi / n_vias  # offset half-step
        x = cx + ring_r * math.cos(angle)
        y = cy + ring_r * math.sin(angle)
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=24, radius=via_r, depth=logo_height_mm,
            location=(x, y, top_z + logo_height_mm / 2),
        )
        via = bpy.context.active_object
        via.name = f"circuit_via_{i}"
        objects.append(via)

    # --- Radial traces (thin rectangles from each via toward the ring) ---
    for i in range(n_vias):
        angle = 2 * math.pi * i / n_vias + math.pi / n_vias
        # trace center halfway between via and outer ring
        mid_r = ring_r + trace_len / 2 + 0.5
        x = cx + mid_r * math.cos(angle)
        y = cy + mid_r * math.sin(angle)
        bpy.ops.mesh.primitive_cube_add(
            size=1.0,
            location=(x, y, top_z + logo_height_mm / 2),
        )
        trace = bpy.context.active_object
        trace.name = f"circuit_trace_{i}"
        # Scale: length along radial direction, width perpendicular, thin in Z
        trace.scale = (trace_len, trace_w, logo_height_mm)
        # Rotate to point along radial direction
        trace.rotation_euler = (0, 0, angle)
        objects.append(trace)

    # Apply transforms now so each mesh has its absolute geometry, then merge
    # all decoration into a single mesh object with one material.
    bpy.ops.object.select_all(action="DESELECT")
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.object.join()
    merged = bpy.context.active_object
    merged.name = "logo_circuits"
    assign_group_material(merged, color_group)
    print(f"[geom] circuits: ring + {n_vias} vias + {n_vias} traces "
          f"(verts={len(merged.data.vertices)} polys={len(merged.data.polygons)})")
    return [merged]


def _export_per_mesh_stls(out_dir: Path) -> list[Path]:
    """Export every mesh in the scene as its own STL file inside ``out_dir``.
    File names match the object names (coaster_base.stl, logo_purple.stl, …).

    Designed for the "open in CAD + Assemble in Bambu Studio" workflow:
      1. Open each STL in Fusion / FreeCAD / OnShape to inspect or edit.
      2. In Bambu: drag-drop all 4 STLs to the plate → Cmd+A select all →
         right-click → "Assemble". You get one multi-part model. Then change
         filament per part (3 clicks) → Slice.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        stl_path = out_dir / f"{obj.name}.stl"
        if hasattr(bpy.ops.wm, "stl_export"):
            bpy.ops.wm.stl_export(filepath=str(stl_path),
                                   export_selected_objects=True)
        else:
            bpy.ops.export_mesh.stl(filepath=str(stl_path), use_selection=True)
        exported.append(stl_path)
    return exported


def _extract_mesh_parts(modifier_meshes: set[str] | None = None) -> list[MeshPart]:
    """Walk every mesh object in the scene and return a MeshPart per object,
    using its first material's baseColor (or diffuse_color fallback) as the
    color.

    ``modifier_meshes``: names of objects to flag with the 'modifier_part'
    subtype. Anything not in the set defaults to 'normal_part'. Used by
    the engrave / protrude logic: in engrave mode the coaster_base is the
    only normal_part, the logos are modifier_part (override filament in
    their region, not standalone printable geometry).
    """
    modifier_meshes = modifier_meshes or set()
    parts: list[MeshPart] = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        me = obj.data
        wm = obj.matrix_world
        verts = [tuple((wm @ v.co)[:]) for v in me.vertices]
        tris: list[tuple[int, int, int]] = []
        for poly in me.polygons:
            if len(poly.vertices) == 3:
                tris.append(tuple(poly.vertices))
            else:
                vs = list(poly.vertices)
                for i in range(1, len(vs) - 1):
                    tris.append((vs[0], vs[i], vs[i + 1]))
        rgba: tuple[float, float, float, float] = (0.5, 0.5, 0.5, 1.0)
        if obj.material_slots and obj.material_slots[0].material:
            mat = obj.material_slots[0].material
            bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.use_nodes else None
            if bsdf:
                c = bsdf.inputs["Base Color"].default_value
                rgba = (c[0], c[1], c[2], c[3])
            else:
                c = mat.diffuse_color
                rgba = (c[0], c[1], c[2], c[3])
        subtype = SUBTYPE_MODIFIER if obj.name in modifier_meshes else SUBTYPE_NORMAL
        parts.append(MeshPart(name=obj.name, color_rgba=rgba,
                              vertices=verts, triangles=tris, subtype=subtype))
    # Stable order: coaster_base first (it owns the printable footprint),
    # then everyone else. Bambu needs the printable normal_part first so it
    # knows which mesh defines the build plate footprint.
    parts.sort(key=lambda p: (0 if p.subtype == SUBTYPE_NORMAL else 1, p.name))
    return parts


def _center_on_plate(meshes: list, plate_center=(128.0, 128.0)) -> None:
    """Translate every mesh so the union bbox center sits at ``plate_center``
    (X, Y) on the plate. Bambu's printable area starts at (0, 0); our
    pipeline produces models centered on the origin which means half the
    model sits in negative coords and falls outside the plate on import.

    (128, 128) is the center of the P1S 256×256 bed; H2D's 350×320 bed
    has plenty of margin to the right and slight margin above. Either
    printer accepts this position with the model fully inside the plate.
    """
    from mathutils import Vector
    xs, ys = [], []
    for m in meshes:
        wm = m.matrix_world
        for v in m.data.vertices:
            cv = wm @ v.co
            xs.append(cv.x); ys.append(cv.y)
    if not xs:
        return
    cur_cx = (min(xs) + max(xs)) / 2
    cur_cy = (min(ys) + max(ys)) / 2
    dx = plate_center[0] - cur_cx
    dy = plate_center[1] - cur_cy
    for m in meshes:
        for v in m.data.vertices:
            v.co.x += dx
            v.co.y += dy
        m.data.update()


def export_glb(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove any leftover non-mesh objects (residual SVG curves the convert
    # step did not absorb). They block transform_apply with "Cannot apply to
    # a 2D curve" errors.
    for obj in list(bpy.data.objects):
        if obj.type != "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
    # Translate every mesh so the model lands centered on the Bambu plate
    # (P1S/H2D both accept (128, 128) as a safe inside-the-printable-area spot).
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    _center_on_plate(meshes)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.export_scene.gltf(
        filepath=str(out_path),
        export_format="GLB",
        export_image_format="AUTO",
        export_materials="EXPORT",
        # yup=False so Bambu Studio receives Z-up directly (Bambu treats glTF
        # as Z-up regardless of the spec). Earlier yup=True made the coaster
        # stand on its edge.
        export_yup=False,
        use_selection=False,
        export_apply=True,
    )


def main() -> int:
    args = parse_args()
    reset_scene()

    # 1. Coaster base
    coaster = import_stl(args.stl)
    assign_coaster_material(coaster)

    # 2. Logo curves grouped by color
    groups = import_svg_grouped(args.svg)
    if not groups:
        sys.exit("ERROR: no SVG curves imported")

    # 3. Per-group: join → convert to mesh → extrude. Keep the resulting
    # mesh objects for later positioning.
    # Skip the 'black' group — it's auto-generated by some SVGs as outline
    # and would conflict with the dark coaster base.
    logo_meshes: list[bpy.types.Object] = []
    for group_name, curve_names in groups.items():
        if group_name == "black":
            # Delete black-group curves (visually merge with coaster)
            for n in curve_names:
                obj = bpy.data.objects.get(n)
                if obj:
                    bpy.data.objects.remove(obj, do_unlink=True)
            continue
        # For the cursor's red group: drop the small "click motion" decorative
        # curves; keep only the largest path (the arrow itself).
        if group_name == "red":
            curve_names = filter_keep_largest(curve_names)
        # Any curve whose control points form a degenerate (1-D) bbox is a
        # stroke imported by Blender's SVG addon. Give it bevel thickness so
        # it meshes as a swept tube instead of a zero-volume strip.
        _give_stroke_curves_thickness(curve_names)
        mesh_obj = curves_to_mesh(curve_names, f"logo_{group_name}")
        if mesh_obj:
            # Reject degenerate meshes (e.g. SVG strokes whose control points
            # share a coordinate axis — Blender meshes them as zero-width
            # strips). Threshold 0.1 mm² of footprint area before extrusion.
            verts = mesh_obj.data.vertices
            if verts:
                xs = [v.co.x for v in verts]
                ys = [v.co.y for v in verts]
                footprint = (max(xs) - min(xs)) * (max(ys) - min(ys))
            else:
                footprint = 0.0
            if footprint < 1e-7:
                print(f"[geom] dropping degenerate mesh 'logo_{group_name}' "
                      f"(footprint={footprint:.2e} ≈ 0). SVG paths in this "
                      f"group are strokes without fill — convert them to "
                      f"filled paths in Inkscape to include this color.")
                bpy.data.objects.remove(mesh_obj, do_unlink=True)
                continue
            extrude_mesh(mesh_obj, args.logo_height_mm)
            assign_group_material(mesh_obj, group_name)
            logo_meshes.append(mesh_obj)
            print(f"[geom] mesh '{mesh_obj.name}': "
                  f"verts={len(mesh_obj.data.vertices)} polys={len(mesh_obj.data.polygons)}")

    # Hard fail if the SVG only produced 'black' curves (filtered out) or
    # otherwise yielded no usable logo geometry. Otherwise we'd silently
    # write a bare coaster.glb and the user would notice only at print time.
    if not logo_meshes:
        sys.exit("ERROR: SVG produced no non-black curves to extrude — "
                 "check that the source has paths in the recognized colors "
                 f"(centroids: {list(COLOR_GROUPS.keys())}).")

    # 4. Fit + center on coaster top
    fit_logo_on_top(logo_meshes, coaster, args.logo_size_mm, args.logo_height_mm,
                    engrave=args.engrave,
                    offset_x_mm=args.offset_x_mm, offset_y_mm=args.offset_y_mm)

    # 4b. Engrave mode requires a boolean cut on the coaster — the letters
    # physically overlap the base, and without subtraction the slicer's
    # mesh-priority rule paints the base color over them and the engrave is
    # invisible. We subtract each logo mesh from the coaster_base so the
    # base ends up with cavity-shaped holes that the (independent) logo
    # meshes fill in their own colors.
    if args.engrave:
        for logo in logo_meshes:
            bool_mod = coaster.modifiers.new(name=f"engrave_{logo.name}",
                                              type="BOOLEAN")
            bool_mod.operation = "DIFFERENCE"
            bool_mod.object = logo
            bpy.context.view_layer.objects.active = coaster
            bpy.ops.object.modifier_apply(modifier=bool_mod.name)
        print(f"[geom] engrave: subtracted {len(logo_meshes)} logo meshes "
              f"from coaster_base (coaster polys now {len(coaster.data.polygons)})")

    # 4b. Optional PCB-style decoration around the logo
    if args.circuits:
        circuit_meshes = make_circuit_decoration(coaster, args.logo_size_mm,
                                                  args.logo_height_mm, args.circuit_color)
        logo_meshes.extend(circuit_meshes)

    # 5. Export
    export_glb(args.out)
    print(f"[geom] wrote {args.out} ({os.path.getsize(args.out)} bytes)")
    print(f"[geom] objects in scene: {[o.name for o in bpy.data.objects if o.type == 'MESH']}")

    # 5b. Optional 3MF sidecar (Bambu Studio's native multicolor format)
    if args.also_3mf:
        mf_path = args.out.with_suffix(".3mf")
        # Every logo mesh is a "modifier_part" — it overrides the coaster's
        # filament in its volume without being treated as standalone printable
        # geometry. Bambu requires this for both protrude and engrave modes,
        # because in both cases the logo meshes don't touch Z=0 on their own.
        modifier_names = {o.name for o in bpy.data.objects
                          if o.type == "MESH" and o.name != "coaster_base"}
        parts = _extract_mesh_parts(modifier_meshes=modifier_names)
        circuit_slot = CIRCUIT_COLOR_TO_SLOT.get(args.circuit_color, 3)
        slots = [slot_for_mesh(p.name, circuit_color_slot=circuit_slot)
                 for p in parts]
        write_3mf(mf_path, parts, filament_slots=slots)
        slot_summary = ", ".join(f"{p.name}→{s}({p.subtype.split('_')[0]})"
                                  for p, s in zip(parts, slots))
        print(f"[geom] wrote {mf_path} ({os.path.getsize(mf_path)} bytes, "
              f"{len(parts)} parts; AMS: {slot_summary})")

    # 5c. Optional per-mesh STL export (CAD-friendly + Bambu Assemble workflow)
    if args.also_stls:
        stls_dir = args.out.parent / "stls"
        exported = _export_per_mesh_stls(stls_dir)
        print(f"[geom] wrote {len(exported)} STLs into {stls_dir}/")
        for p in exported:
            print(f"        {p.name}  ({p.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

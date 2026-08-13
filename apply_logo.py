"""Blender headless pipeline: STL + logo PNG -> textured GLB.

Usage:
  /Applications/Blender.app/Contents/MacOS/Blender --background --python apply_logo.py -- \
      --stl examples/coaster_simple.stl \
      --logo assets/logo-full.png \
      --out  output/coaster_logo.glb \
      [--logo-size-mm 60] [--base-color "#1a1a1a"]

Approach:
  1. Import the STL.
  2. Find the bounding box; the "top face" of a flat-ish coaster is everything
     with normals pointing close to +Z within a tolerance band near Z_max.
  3. Build a UV map by orthographic planar projection from +Z, then re-scale
     and re-center those UVs so the logo PNG (which occupies UV space [0,1])
     maps to a `logo_size_mm` square at the centre of the top face. UVs outside
     that square fall outside [0,1] and are clipped (image node CLIP extension).
  4. Material: Principled BSDF, base color = dark grey. Image texture (logo
     PNG, alpha) drives Mix between dark grey and the logo color, so the logo
     wins on opaque pixels and the rest of the coaster stays grey.
  5. Export GLB with textures embedded (GLB binary container does this by
     default; we also force `export_image_format='AUTO'`).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import bpy  # type: ignore
import bmesh  # type: ignore
from mathutils import Vector  # type: ignore

# color_utils is Blender-free; import via the script's own folder so this works
# when Blender invokes apply_logo.py with cwd != repo root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from color_utils import hex_to_rgba_linear  # noqa: E402


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    else:
        argv = []
    p = argparse.ArgumentParser()
    p.add_argument("--stl", required=True, type=Path)
    p.add_argument("--logo", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--logo-size-mm", type=float, default=60.0)
    p.add_argument("--base-color", default="#1a1a1a")
    p.add_argument("--face", default="top",
                   choices=["top", "bottom", "front", "back", "right", "left"],
                   help="Which face of the mesh to project the logo onto.")
    p.add_argument("--face-angle-deg", type=float, default=25.0,
                   help="Faces within this angle of the face normal count as target.")
    p.add_argument("--face-depth-mm", type=float, default=0.5,
                   help="Only faces within this distance (mm) of the bbox extreme "
                        "in the face direction count. Prevents internal cavity "
                        "faces from getting the logo. Previously 5mm which made "
                        "every face on a <=5mm-tall coaster qualify (every "
                        "lateral face received the UVs and Bambu painted the "
                        "logo on the sides). 0.5mm keeps the filter strict "
                        "without rejecting legitimate slight wobble.")
    p.add_argument("--subdivide-target-tris", type=int, default=8000,
                   help="Subdivide target-face edges until the matching region has "
                        "at least N triangles. Bambu Texture-to-Color samples ONE color "
                        "per triangle, so coarse top faces (e.g. 124 tris) produce blocky "
                        "logos. Default 8000 → ~1mm² per tri on a 70mm logo. Set 0 to skip.")
    return p.parse_args(argv)


# Face name -> normal vector (world space) and the two in-plane axes (U, V).
# UV axis ordering chosen so the logo reads upright when printed (front-facing).
FACE_DEFS = {
    #          normal     U axis    V axis
    "top":    ((0, 0, 1),  (1, 0, 0), (0, 1, 0)),    # look down: U=X, V=Y
    "bottom": ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),   # look up: U=-X (mirror), V=Y
    "front":  ((0, -1, 0), (1, 0, 0), (0, 0, 1)),    # look at -Y face: U=X, V=Z
    "back":   ((0, 1, 0),  (-1, 0, 0), (0, 0, 1)),   # look at +Y face: U=-X, V=Z
    "right":  ((1, 0, 0),  (0, -1, 0), (0, 0, 1)),   # look at +X face: U=-Y, V=Z
    "left":   ((-1, 0, 0), (0, 1, 0), (0, 0, 1)),    # look at -X face: U=Y, V=Z
}


def hex_to_rgba(h: str) -> tuple[float, float, float, float]:
    # Thin wrapper kept for backward compat with the call sites below.
    return hex_to_rgba_linear(h)


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_stl(path: Path) -> bpy.types.Object:
    # Blender 4+ uses bpy.ops.wm.stl_import.
    if hasattr(bpy.ops.wm, "stl_import"):
        bpy.ops.wm.stl_import(filepath=str(path))
    else:
        bpy.ops.import_mesh.stl(filepath=str(path))
    obj = bpy.context.selected_objects[0]
    obj.name = "coaster"
    return obj


def _target_face_mask(bm: bmesh.types.BMesh, wm, normal: Vector,
                      cos_thresh: float, n_threshold: float) -> list[bool]:
    """Return a per-face bool list: True if face is a target (normal+extreme)."""
    mask = []
    for bm_face in bm.faces:
        n_world = (wm.to_3x3() @ bm_face.normal).normalized()
        is_normal_ok = n_world.dot(normal) >= cos_thresh
        if is_normal_ok:
            center_local = sum((v.co for v in bm_face.verts), Vector()) / len(bm_face.verts)
            center_world = wm @ center_local
            is_extreme = center_world.dot(normal) >= n_threshold
        else:
            is_extreme = False
        mask.append(is_normal_ok and is_extreme)
    return mask


def _subdivide_target_region(bm: bmesh.types.BMesh, wm, normal: Vector,
                             cos_thresh: float, n_threshold: float,
                             target_tris: int) -> int:
    """Recursively 4-way subdivide edges that belong to target faces, until the
    target region has at least ``target_tris`` triangles.

    Subdivision multiplies each face by ~4× per pass. We loop because the mask
    must be recomputed after each pass (new faces inherit the normal but aren't
    in the original mask).

    Returns the number of passes performed.
    """
    if target_tris <= 0:
        return 0
    passes = 0
    while True:
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        mask = _target_face_mask(bm, wm, normal, cos_thresh, n_threshold)
        n_target = sum(mask)
        if n_target >= target_tris or passes >= 6:
            break
        # Collect every edge whose adjacent faces include at least one target face.
        target_edges = set()
        for face_idx, is_target in enumerate(mask):
            if not is_target:
                continue
            for edge in bm.faces[face_idx].edges:
                target_edges.add(edge)
        if not target_edges:
            break
        bmesh.ops.subdivide_edges(bm, edges=list(target_edges), cuts=1,
                                   use_grid_fill=True)
        passes += 1
        print(f"[apply_logo] subdivide pass {passes}: target faces {n_target} → {sum(_target_face_mask(bm, wm, normal, cos_thresh, n_threshold))}")
    return passes


def build_uvs(obj: bpy.types.Object, logo_size_mm: float, face_angle_deg: float,
              face: str, face_depth_mm: float, subdivide_target_tris: int = 0) -> None:
    """Planar orthographic projection onto a chosen face of the mesh.

    A face is considered a target if BOTH:
      1. Its normal is within ``face_angle_deg`` of the chosen face normal.
      2. Its center is within ``face_depth_mm`` of the bbox extreme along
         that normal direction (filters out internal cavity faces).

    Other faces get UV (0.001, 0.001) — a tiny corner inside [0,1] where the PNG
    has solid base color. We CANNOT use (10,10) and rely on the clamp sampler
    because Bambu Studio's Texture-to-Color algorithm ignores the sampler and
    treats out-of-range UVs as random/wrap, producing color noise on lateral
    faces ("manchas moradas" bug from May 2026).

    Args:
        face: one of FACE_DEFS keys (top|bottom|front|back|right|left)
        face_depth_mm: max distance from bbox extreme in normal direction
    """
    if face not in FACE_DEFS:
        raise ValueError(f"Unknown face '{face}'. Valid: {list(FACE_DEFS)}")

    normal_raw, u_axis_raw, v_axis_raw = FACE_DEFS[face]
    normal = Vector(normal_raw)
    u_axis = Vector(u_axis_raw)
    v_axis = Vector(v_axis_raw)

    mesh = obj.data

    wm = obj.matrix_world
    verts_world = [wm @ v.co for v in mesh.vertices]
    us = [v.dot(u_axis) for v in verts_world]
    vs = [v.dot(v_axis) for v in verts_world]
    ns = [v.dot(normal) for v in verts_world]
    cu = (min(us) + max(us)) / 2.0
    cv = (min(vs) + max(vs)) / 2.0
    n_extreme = max(ns)
    n_threshold = n_extreme - face_depth_mm

    half = logo_size_mm / 2.0
    cos_thresh = math.cos(math.radians(face_angle_deg))

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()

    # Pass 1: densify target region so per-triangle color sampling can resolve
    # the logo's fine features.
    if subdivide_target_tris > 0:
        _subdivide_target_region(bm, wm, normal, cos_thresh, n_threshold,
                                 subdivide_target_tris)
        bm.faces.ensure_lookup_table()

    # Pass 2: split edges between matching and non-matching faces so the UVs on
    # each side stay independent. Without this, the GLB exporter unifies verts
    # shared by both regions and the non-matching side inherits the matching UV
    # — making lateral/bottom faces sample the logo colors instead of the bg
    # (May 2026 bug: coaster laterals showed logo bands in Bambu).
    mask = _target_face_mask(bm, wm, normal, cos_thresh, n_threshold)
    matching_faces = {bm.faces[i] for i, m in enumerate(mask) if m}
    border_edges = []
    for edge in bm.edges:
        adj = list(edge.link_faces)
        if len(adj) == 2 and (adj[0] in matching_faces) != (adj[1] in matching_faces):
            border_edges.append(edge)
    if border_edges:
        bmesh.ops.split_edges(bm, edges=border_edges)
        bm.faces.ensure_lookup_table()
        print(f"[apply_logo] split {len(border_edges)} border edges (matching↔non-matching)")

    # Recompute UV layer on the densified+split mesh.
    uv_layer_bm = bm.loops.layers.uv.verify()

    n_normal_ok = 0
    n_match = 0
    for bm_face in bm.faces:
        n_world = (wm.to_3x3() @ bm_face.normal).normalized()
        is_normal_ok = n_world.dot(normal) >= cos_thresh

        if is_normal_ok:
            n_normal_ok += 1
            center_local = sum((v.co for v in bm_face.verts), Vector()) / len(bm_face.verts)
            center_world = wm @ center_local
            is_extreme = center_world.dot(normal) >= n_threshold
        else:
            is_extreme = False

        if is_normal_ok and is_extreme:
            n_match += 1
            for loop in bm_face.loops:
                co_w = wm @ loop.vert.co
                u = (co_w.dot(u_axis) - (cu - half)) / logo_size_mm
                v = (co_w.dot(v_axis) - (cv - half)) / logo_size_mm
                loop[uv_layer_bm].uv = (u, v)
        else:
            for loop in bm_face.loops:
                loop[uv_layer_bm].uv = (0.001, 0.001)
    print(f"[apply_logo] face='{face}' normal_ok: {n_normal_ok}  "
          f"matching (normal+extreme within {face_depth_mm}mm): {n_match} / {len(bm.faces)}")
    # Write the bmesh (possibly subdivided + UV'd) back to the object's mesh.
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def make_material(name: str, logo_png: Path, base_color: tuple[float, float, float, float]) -> bpy.types.Material:
    """Minimal glTF-friendly material: ImageTexture.Color -> BSDF.BaseColor.

    The PNG already has the dark grey base color baked into its background
    (via `svg_to_png.py --bg`). Sampler is left at the default REPEAT, but UVs
    for non-up-facing faces are pushed to (10,10) so they sample the (grey)
    edge pixel after CLAMP/REPEAT both wrap to grey. We also set
    extension='EXTEND' (clamp) explicitly to be safe.
    """
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 0.7
    # Set BaseColor default to the requested base color too, in case the
    # gltf exporter ever drops the texture link.
    bsdf.inputs["Base Color"].default_value = base_color

    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.location = (-200, 0)
    img = bpy.data.images.load(str(logo_png.resolve()))
    img.alpha_mode = "STRAIGHT"
    tex.image = img
    tex.extension = "EXTEND"
    # Closest (nearest-neighbor) is mandatory for palette-quantized PNGs: any
    # smoother filter blends adjacent pixels back into the intermediate colors
    # we worked hard to eliminate, and Bambu Studio's color detector then
    # claims 8+ filaments instead of the 4 we want.
    tex.interpolation = "Closest"

    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    return mat


def export_glb(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Apply any accumulated object transforms into vertex data so the GLB
    # contains the absolute world geometry. This avoids Bambu Studio importing
    # the mesh in an unexpected orientation when the object has a non-identity
    # rotation (May 2026 bug: coaster appearing standing on edge in Bambu).
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.export_scene.gltf(
        filepath=str(out_path),
        export_format="GLB",
        export_image_format="AUTO",
        export_materials="EXPORT",
        export_yup=False,
        use_selection=False,
        export_apply=True,
    )


def main() -> int:
    args = parse_args()

    reset_scene()
    obj = import_stl(args.stl)

    # Make sure we have only this object selected.
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    build_uvs(obj, args.logo_size_mm, args.face_angle_deg, args.face, args.face_depth_mm,
              subdivide_target_tris=args.subdivide_target_tris)

    mat = make_material("coaster_mat", args.logo, hex_to_rgba(args.base_color))
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    # Diagnostics.
    mesh = obj.data
    print(f"[apply_logo] verts={len(mesh.vertices)} polys={len(mesh.polygons)}")
    print(f"[apply_logo] bbox z: {min(v.co.z for v in mesh.vertices):.3f} .. {max(v.co.z for v in mesh.vertices):.3f}")

    export_glb(args.out)
    print(f"[apply_logo] wrote {args.out} ({os.path.getsize(args.out)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

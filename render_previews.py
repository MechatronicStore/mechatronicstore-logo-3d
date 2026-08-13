"""Render N previews of a GLB from different angles using Blender headless.

Used to visually verify that a textured GLB looks correct without opening a GUI.

Usage:
    /Applications/Blender.app/Contents/MacOS/Blender --background --python render_previews.py -- \
        --glb output/coaster_logo.glb \
        --outdir output/previews \
        [--size 800] [--samples 32]

Renders: top.png, front.png, right.png, iso.png
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import bpy  # type: ignore
from mathutils import Vector  # type: ignore

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render_helpers import union_bbox, detect_thin_axis  # noqa: E402


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--glb", required=True, type=Path)
    p.add_argument("--outdir", required=True, type=Path)
    p.add_argument("--size", type=int, default=800)
    p.add_argument("--samples", type=int, default=32)
    return p.parse_args(argv)


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup_eevee(size: int, samples: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in {
        e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    } else "BLENDER_EEVEE"
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.film_transparent = False
    if hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = samples
    scene.render.image_settings.file_format = "PNG"


def import_glb(path: Path):
    bpy.ops.import_scene.gltf(filepath=str(path))
    meshes = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    _reorient_to_z_up(meshes)
    return meshes


def _reorient_to_z_up(meshes: list) -> None:
    """If the GLB was exported with yup=False, Blender's glTF importer still
    applies its standard Y-up→Z-up rotation, leaving the model lying on its
    side (Y becomes the thin axis instead of Z). Detect that case and rotate
    the meshes back so the camera's 'top' view actually looks down at the top.

    Rotates mesh data directly (not via object transform) — Blender 5.x
    doesn't propagate ``rotation_euler`` changes to ``matrix_world`` until the
    depsgraph re-evaluates, so a later ``bound_box`` query would still see the
    old orientation if we relied on ``transform_apply``.

    Only rotates when the union bbox shows a clear flat axis. Tall/cubic
    models keep their orientation.
    """
    import math
    import bmesh
    from mathutils import Matrix
    if not meshes:
        return
    boxes = [_world_bbox(m) for m in meshes]
    (mn, mx) = union_bbox(boxes)
    thin = detect_thin_axis((mn, mx))
    if thin is None or thin == 2:
        return  # already Z-up (or not flat)
    if thin == 1:
        # Y is thin → rotate -90° around X so +Y maps to +Z. (Rotation(+90°,X)
        # sends +Y to -Z which would flip the model upside-down — the top
        # face ends up facing away from the camera.)
        rot = Matrix.Rotation(-math.pi / 2, 3, "X")
        label = "X -90°"
    else:
        # X is thin → rotate +90° around Y so +X maps to +Z.
        rot = Matrix.Rotation(math.pi / 2, 3, "Y")
        label = "Y +90°"
    for m in meshes:
        bm = bmesh.new()
        bm.from_mesh(m.data)
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=rot)
        bm.to_mesh(m.data)
        bm.free()
        m.data.update()
    print(f"[preview] reoriented ({label}) so thin axis becomes +Z")


def _world_bbox(obj) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Compute a single mesh's axis-aligned bbox in world coordinates."""
    wm = obj.matrix_world
    coords = [wm @ Vector(c) for c in obj.bound_box]
    return (
        (min(c.x for c in coords), min(c.y for c in coords), min(c.z for c in coords)),
        (max(c.x for c in coords), max(c.y for c in coords), max(c.z for c in coords)),
    )


def fit_camera_to_meshes(cam, meshes: list, view_axis: str, padding: float = 1.4) -> None:
    """Position camera so the union of all meshes fills the frame.

    Earlier we only looked at ``meshes[0]``; for multi-object GLBs (coaster +
    logo + circuits) that left the camera framing a tiny first mesh, producing
    one-line previews. We now compute the union bbox across every mesh.

    view_axis: 'top' (+Z), 'front' (+Y), 'right' (+X), 'iso' (1,1,1)
    """
    boxes = [_world_bbox(m) for m in meshes]
    (mn_x, mn_y, mn_z), (mx_x, mx_y, mx_z) = union_bbox(boxes)
    min_v = Vector((mn_x, mn_y, mn_z))
    max_v = Vector((mx_x, mx_y, mx_z))
    center = (min_v + max_v) / 2
    size = max(max_v - min_v) * padding

    if view_axis == "top":
        cam.location = center + Vector((0, 0, size * 2))
        cam.rotation_euler = (0, 0, 0)
    elif view_axis == "front":
        cam.location = center + Vector((0, -size * 2, 0))
        cam.rotation_euler = (math.radians(90), 0, 0)
    elif view_axis == "right":
        cam.location = center + Vector((size * 2, 0, 0))
        cam.rotation_euler = (math.radians(90), 0, math.radians(90))
    elif view_axis == "iso":
        d = size * 1.8
        cam.location = center + Vector((d, -d, d))
        cam.rotation_euler = (math.radians(55), 0, math.radians(45))

    cam.data.type = "ORTHO"
    cam.data.ortho_scale = size


def add_lights() -> None:
    # Key light from top
    bpy.ops.object.light_add(type="AREA", location=(0, 0, 200))
    key = bpy.context.object
    key.data.energy = 2000
    key.data.size = 200
    # Fill from front-side
    bpy.ops.object.light_add(type="AREA", location=(100, -100, 100))
    fill = bpy.context.object
    fill.data.energy = 800
    fill.data.size = 200
    # Ambient via world. `read_factory_settings(use_empty=True)` may leave
    # bpy.data.worlds empty, so probe with len() instead of truthiness on a
    # collection that doesn't reliably support __bool__.
    world = bpy.data.worlds.new("World") if len(bpy.data.worlds) == 0 else bpy.data.worlds[0]
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.4, 0.4, 0.4, 1.0)
        bg.inputs[1].default_value = 0.5


def render_view(cam, meshes: list, view_axis: str, out: Path) -> None:
    fit_camera_to_meshes(cam, meshes, view_axis)
    bpy.context.scene.camera = cam
    bpy.context.scene.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    print(f"[preview] {out}")


def main() -> int:
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    reset_scene()
    meshes = import_glb(args.glb)
    if not meshes:
        sys.exit("No mesh objects in GLB")

    bpy.context.view_layer.objects.active = meshes[0]

    setup_eevee(args.size, args.samples)
    add_lights()

    bpy.ops.object.camera_add()
    cam = bpy.context.object

    for view in ("top", "front", "right", "iso"):
        render_view(cam, meshes, view, args.outdir / f"{view}.png")

    return 0


if __name__ == "__main__":
    sys.exit(main())

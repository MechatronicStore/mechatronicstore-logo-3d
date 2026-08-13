"""Render a presentation shot of a GLB: studio light, matte PLA look, alpha background.

render_previews.py exists to *check* a result (flat light, four fixed views,
gray background). This one exists to *show* it: a single three quarter view with
a soft key light, a rim light and a transparent background, so the PNG sits well
on both the light and the dark theme of a README.

Usage:
    blender --background --python tools/render_showcase.py -- \
        --glb part_logo.glb --out docs/example.png [--size 1000] [--samples 96]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import bpy          # type: ignore
from mathutils import Vector   # type: ignore

ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent
sys.path.insert(0, str(ROOT))
from render_helpers import union_bbox, detect_thin_axis   # noqa: E402


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--glb", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--size", type=int, default=1000)
    p.add_argument("--samples", type=int, default=96)
    p.add_argument("--elevation", type=float, default=38.0,
                   help="Camera height in degrees above the plate")
    p.add_argument("--azimuth", type=float, default=35.0,
                   help="Camera rotation in degrees around the part")
    return p.parse_args(argv)


def world_bbox(obj):
    """Bounds from the actual vertices.

    obj.bound_box is a cache that still reports the pre-rotation extents right
    after we transform mesh data with bmesh, which framed the part off-center.
    Reading the vertices costs a little more and is always current.
    """
    wm = obj.matrix_world
    coords = [wm @ v.co for v in obj.data.vertices]
    if not coords:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    return ((min(c.x for c in coords), min(c.y for c in coords), min(c.z for c in coords)),
            (max(c.x for c in coords), max(c.y for c in coords), max(c.z for c in coords)))


def reorient_to_z_up(meshes: list) -> None:
    """The glTF importer can leave the part lying on its side. Stand it up."""
    import bmesh
    from mathutils import Matrix

    thin = detect_thin_axis(union_bbox([world_bbox(m) for m in meshes]))
    if thin is None or thin == 2:
        return
    rot = (Matrix.Rotation(-math.pi / 2, 3, "X") if thin == 1
           else Matrix.Rotation(math.pi / 2, 3, "Y"))
    for m in meshes:
        bm = bmesh.new()
        bm.from_mesh(m.data)
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=rot)
        bm.to_mesh(m.data)
        bm.free()
        m.data.update()


def matte_plastic(meshes: list) -> None:
    """Filament is matte, not glossy. Keep each mesh's color, kill the shine."""
    for m in meshes:
        for slot in m.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes:
                continue
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if not bsdf:
                continue
            for name, value in (("Roughness", 0.62), ("Metallic", 0.0),
                                ("Specular IOR Level", 0.35), ("Sheen Weight", 0.05)):
                if name in bsdf.inputs:
                    bsdf.inputs[name].default_value = value


def setup_scene(size: int, samples: int) -> None:
    scene = bpy.context.scene
    engines = {e.identifier for e in
               bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines \
        else "BLENDER_EEVEE"
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.film_transparent = True          # blends into any README theme
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    if hasattr(scene.eevee, "taa_render_samples"):
        scene.eevee.taa_render_samples = samples
    if hasattr(scene.eevee, "use_raytracing"):
        scene.eevee.use_raytracing = True         # contact shadows on the logo edges
    # Standard, not AgX: the filmic transform desaturates the brand purple and
    # turns the brand yellow olive. Fidelity beats cinematic here.
    scene.view_settings.view_transform = "Standard"

    world = bpy.data.worlds.new("World") if len(bpy.data.worlds) == 0 else bpy.data.worlds[0]
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.05, 0.05, 0.06, 1.0)
        bg.inputs[1].default_value = 1.2


def studio_lights(center: Vector, size: float) -> None:
    """Key light high and to the left, fill low and right, rim from behind.

    Energy scales with size squared because a part exported in millimetres is
    read by Blender as metres: a 100 mm coaster becomes a 100 m disc, and the
    lights sit ~200 m away. Watts that look right at desk scale render nearly
    black at that distance, which turned the brand purple muddy and the brand
    yellow olive. The 6x factor is what brings a mm-scale part to a normal
    exposure at these distances: 60x blows out to white, 0.6x renders muddy.
    """
    for loc, energy, radius in (
        ((-size * 1.2, -size * 1.1, size * 2.0), 9.0, 1.4),   # key
        ((size * 1.6, -size * 0.8, size * 0.8), 3.5, 1.8),    # fill
        ((size * 0.2, size * 1.8, size * 1.2), 4.0, 1.2),     # rim
    ):
        bpy.ops.object.light_add(type="AREA", location=center + Vector(loc))
        light = bpy.context.object
        light.data.energy = energy * size * size * 6
        light.data.size = size * radius
        direction = center - light.location
        light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def place_camera(center: Vector, radius: float, elevation: float, azimuth: float):
    """Long lens, pulled back far enough that the bounding sphere fits the frame.

    Deriving the distance from the lens beats projecting the corners and then
    zooming: no second pass, and nothing to drift off-center.
    """
    bpy.ops.object.camera_add()
    cam = bpy.context.object
    cam.data.lens = 85                       # long lens keeps the part undistorted
    cam.data.sensor_fit = "AUTO"
    half_fov = math.atan((cam.data.sensor_width / 2) / cam.data.lens)
    distance = (radius * 1.02) / math.sin(half_fov)
    el, az = math.radians(elevation), math.radians(azimuth)
    cam.location = center + Vector((
        distance * math.cos(el) * math.sin(az),
        -distance * math.cos(el) * math.cos(az),
        distance * math.sin(el),
    ))
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam
    return cam


def main() -> int:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.glb))
    meshes = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    if not meshes:
        sys.exit("no mesh objects in the GLB")

    reorient_to_z_up(meshes)
    matte_plastic(meshes)

    (mn, mx) = union_bbox([world_bbox(m) for m in meshes])
    center = (Vector(mn) + Vector(mx)) / 2
    diagonal = Vector(mx) - Vector(mn)
    size = max(diagonal)
    radius = diagonal.length / 2

    setup_scene(args.size, args.samples)
    studio_lights(center, size)
    place_camera(center, radius, args.elevation, args.azimuth)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    bpy.context.scene.render.filepath = str(args.out)
    bpy.ops.render.render(write_still=True)
    print(f"[showcase] {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

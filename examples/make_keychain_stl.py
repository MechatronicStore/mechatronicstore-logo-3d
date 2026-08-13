"""Generate a keychain disc STL: rounded rectangle plate with a hole.

Run in Blender headless:
    /Applications/Blender.app/Contents/MacOS/Blender --background --python \
        examples/make_keychain_stl.py
"""
import bpy
import bmesh
from pathlib import Path

# Keychain dimensions (mm)
WIDTH = 50.0
HEIGHT = 35.0
THICKNESS = 3.0
CORNER_RADIUS = 5.0
HOLE_DIAMETER = 5.0
# Corner hole: 5mm from top edge AND 5mm from left edge → 2.5mm wall.
HOLE_OFFSET_FROM_TOP = 5.0
HOLE_OFFSET_FROM_LEFT = 5.0

OUTPUT = Path(__file__).parent / "keychain.stl"


bpy.ops.wm.read_factory_settings(use_empty=True)

# 1. Base plate (cube scaled to width × height × thickness, centered on origin)
bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, THICKNESS / 2))
plate = bpy.context.active_object
plate.scale = (WIDTH, HEIGHT, THICKNESS)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
plate.name = "keychain"

# 2. Bevel corners (subdivide + bevel modifier baked)
bevel = plate.modifiers.new(name="bevel", type="BEVEL")
bevel.width = CORNER_RADIUS
bevel.segments = 8
bevel.limit_method = "ANGLE"
bevel.angle_limit = 0.523599  # 30°
bpy.context.view_layer.objects.active = plate
bpy.ops.object.modifier_apply(modifier="bevel")

# 3. Hole: cylinder boolean-cut at the top-LEFT corner of the keychain
hole_x = -WIDTH / 2 + HOLE_OFFSET_FROM_LEFT
hole_y = HEIGHT / 2 - HOLE_OFFSET_FROM_TOP
bpy.ops.mesh.primitive_cylinder_add(
    vertices=32,
    radius=HOLE_DIAMETER / 2,
    depth=THICKNESS * 2,
    location=(hole_x, hole_y, THICKNESS / 2),
)
hole = bpy.context.active_object

bool_mod = plate.modifiers.new(name="hole", type="BOOLEAN")
bool_mod.operation = "DIFFERENCE"
bool_mod.object = hole
bpy.context.view_layer.objects.active = plate
bpy.ops.object.modifier_apply(modifier="hole")
bpy.data.objects.remove(hole, do_unlink=True)

# 4. Export STL
bpy.ops.object.select_all(action="DESELECT")
plate.select_set(True)
bpy.context.view_layer.objects.active = plate
if hasattr(bpy.ops.wm, "stl_export"):
    bpy.ops.wm.stl_export(filepath=str(OUTPUT), export_selected_objects=True)
else:
    bpy.ops.export_mesh.stl(filepath=str(OUTPUT), use_selection=True)

print(f"[keychain] wrote {OUTPUT}")
print(f"[keychain] verts={len(plate.data.vertices)} polys={len(plate.data.polygons)}")

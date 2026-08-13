"""Test configuration for logo3d.

Tests that don't need Blender run with vanilla pytest. Tests that need bpy
get invoked via `blender --background --python` from a runner script
(see tests/blender_runner.py) and shouldn't be picked up by collection here.
"""
import sys
from pathlib import Path

# Project root on sys.path so `from svg_to_png import ...` works without
# turning everything into a package.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

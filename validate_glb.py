"""CLI wrapper around glb_inspector — smoke-test a GLB without Blender.

Usage:
    uv run python validate_glb.py output/coaster.glb
    uv run python validate_glb.py output/coaster.glb --expect-meshes 5

Exits 0 if the report passes validation, 1 otherwise. Designed for CI / a
post-export hook in scripts that drive the pipeline.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from glb_inspector import inspect_glb, validate_report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("glb", type=Path, help="Path to the GLB file to inspect")
    p.add_argument("--expect-meshes", type=int, default=None,
                   help="Fail if mesh_count != this value")
    args = p.parse_args()

    if not args.glb.exists():
        sys.exit(f"ERROR: {args.glb} does not exist")

    report = inspect_glb(args.glb)
    print(f"file size:       {report['file_size_bytes']:>10} bytes")
    print(f"meshes:          {report['mesh_count']:>10}")
    print(f"materials:       {report['material_count']:>10}")
    print(f"nodes:           {report['node_count']:>10}")
    print(f"accessors:       {report['accessor_count']:>10}")

    errors = validate_report(report, expected_meshes=args.expect_meshes)
    if errors:
        print("\nVALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("\n✅ GLB looks well-formed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

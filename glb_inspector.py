"""Inspect a GLB file without spinning up Blender.

GLB layout (binary glTF 2.0):
  magic(4)="glTF" | version(4) | length(4) | <chunk>+

  chunk = chunkLength(4) | chunkType(4) | chunkData

  chunkType is "JSON" for the metadata chunk (always first) and "BIN\0" for
  the optional binary buffer chunk.

The JSON chunk gives us mesh/material/accessor counts cheap enough to use in
post-export smoke tests.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path


def inspect_glb(path: Path | str) -> dict:
    """Read the JSON chunk of a GLB and return a small report dict.

    Raises ``ValueError`` for files that don't start with the glTF magic.
    """
    path = Path(path)
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"{path} is not a GLB file")
    json_chunk_len = struct.unpack("<I", data[12:16])[0]
    json_bytes = data[20:20 + json_chunk_len]
    gltf = json.loads(json_bytes)
    return {
        "file_size_bytes": len(data),
        "mesh_count": len(gltf.get("meshes", [])),
        "material_count": len(gltf.get("materials", [])),
        "node_count": len(gltf.get("nodes", [])),
        "accessor_count": len(gltf.get("accessors", [])),
    }


def validate_report(report: dict, expected_meshes: int | None = None) -> list[str]:
    """Return a list of human-readable problems with ``report``.

    Empty list = the export passed all the smoke checks. Callers can decide
    whether to exit non-zero (CI) or just print warnings.
    """
    errors: list[str] = []
    if report["mesh_count"] == 0:
        errors.append("GLB has zero meshes — nothing will print")
    if report["material_count"] == 0:
        errors.append("GLB has zero materials — every mesh will render as default grey")
    if expected_meshes is not None and report["mesh_count"] != expected_meshes:
        errors.append(
            f"expected {expected_meshes} meshes, found {report['mesh_count']}"
        )
    return errors

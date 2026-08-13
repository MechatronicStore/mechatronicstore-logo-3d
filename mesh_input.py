"""Mesh input layer: accept .stl or .3mf and hand a plain binary STL downstream.

Why this exists: most models people download (MakerWorld, Printables, Thingiverse)
ship as .3mf, and .3mf is a zip of XML — not something Blender's STL importer or
the logo pipeline can read directly. This module flattens a .3mf into triangles
and writes a binary STL, so the rest of the pipeline stays STL-only.

Supported .3mf features:
  * core spec (2015/02) — <object><mesh>, <build><item transform=...>
  * production extension (2015/06) — objects split into 3D/Objects/*.model and
    referenced with p:path (this is what Bambu Studio / MakerWorld exports use)
  * <components> — objects assembled from other objects, recursively
  * unit conversion (micron | millimeter | centimeter | inch | foot | meter)
  * Bambu's Metadata/model_settings.config for human-readable piece names

Only geometry is read. Paint/color data, slicing settings, thumbnails and any
other metadata inside the file are ignored, never copied to the output.

Usage as a library:
    pieces = list_pieces(Path("model.3mf"))
    stl_path, label = to_stl(Path("model.3mf"), select=2, workdir=tmp)

Usage from the shell:
    python mesh_input.py model.3mf --list
    python mesh_input.py model.3mf --select 2 --out piece.stl
"""
from __future__ import annotations

import argparse
import struct
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

CORE_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PROD_NS = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"

# 3MF unit name → millimetres
UNIT_TO_MM = {
    "micron": 0.001,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}

ROOT_MODEL = "3D/3dmodel.model"

# A .3mf with more triangles than this is refused rather than silently eating
# all available RAM. 20M triangles is far beyond any printable part.
MAX_TRIANGLES = 20_000_000


class MeshInputError(Exception):
    """Raised for anything malformed, unsupported or absurdly large."""


@dataclass
class Piece:
    """One build item: an object placed on the plate, with its own transform."""

    index: int          # 1-based, the number the CLI's --select expects
    object_id: str
    name: str
    triangles: list[tuple[tuple[float, float, float], ...]] = field(default_factory=list)

    @property
    def tri_count(self) -> int:
        return len(self.triangles)

    def bbox_mm(self) -> tuple[float, float, float]:
        if not self.triangles:
            return (0.0, 0.0, 0.0)
        xs, ys, zs = [], [], []
        for tri in self.triangles:
            for v in tri:
                xs.append(v[0]); ys.append(v[1]); zs.append(v[2])
        return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


# ---------------------------------------------------------------- transforms

IDENTITY = (1.0, 0.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 0.0, 1.0,
            0.0, 0.0, 0.0)


def parse_transform(raw: str | None) -> tuple[float, ...]:
    """3MF transforms are 12 numbers: the 3x3 rotation/scale then the offset."""
    if not raw:
        return IDENTITY
    parts = raw.replace(",", " ").split()
    if len(parts) != 12:
        raise MeshInputError(f"transform must have 12 numbers, got {len(parts)}: {raw!r}")
    try:
        return tuple(float(p) for p in parts)
    except ValueError as exc:
        raise MeshInputError(f"transform has a non-numeric value: {raw!r}") from exc


def compose(outer: tuple[float, ...], inner: tuple[float, ...]) -> tuple[float, ...]:
    """Return the transform equal to applying `inner` first, then `outer`."""
    def cell(row: int, col: int) -> float:
        return sum(inner[row * 3 + k] * outer[k * 3 + col] for k in range(3))

    rot = [cell(r, c) for r in range(3) for c in range(3)]
    off = [
        sum(inner[9 + k] * outer[k * 3 + c] for k in range(3)) + outer[9 + c]
        for c in range(3)
    ]
    return tuple(rot + off)


def apply_transform(t: tuple[float, ...], v: tuple[float, float, float]
                    ) -> tuple[float, float, float]:
    x, y, z = v
    return (
        x * t[0] + y * t[3] + z * t[6] + t[9],
        x * t[1] + y * t[4] + z * t[7] + t[10],
        x * t[2] + y * t[5] + z * t[8] + t[11],
    )


# ------------------------------------------------------------------ 3mf read

def _normalize(part: str) -> str:
    """Zip entry names have no leading slash; p:path attributes usually do."""
    return part.lstrip("/")


class _Package:
    """Lazy reader over the zip, caching each parsed .model part."""

    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf
        self._parsed: dict[str, ET.Element] = {}
        self.names = set(zf.namelist())

    def model(self, part: str) -> ET.Element:
        part = _normalize(part)
        if part not in self._parsed:
            if part not in self.names:
                raise MeshInputError(f".3mf references a part that is not in the archive: {part}")
            try:
                self._parsed[part] = ET.fromstring(self.zf.read(part))
            except ET.ParseError as exc:
                raise MeshInputError(f"{part} is not valid XML: {exc}") from exc
        return self._parsed[part]

    def objects(self, part: str) -> dict[str, ET.Element]:
        root = self.model(part)
        res = root.find(f"{CORE_NS}resources")
        if res is None:
            return {}
        return {o.get("id"): o for o in res.findall(f"{CORE_NS}object") if o.get("id")}

    def unit_scale(self, part: str) -> float:
        unit = (self.model(part).get("unit") or "millimeter").lower()
        if unit not in UNIT_TO_MM:
            raise MeshInputError(f"unknown 3MF unit {unit!r}")
        return UNIT_TO_MM[unit]


def _bambu_names(pkg: _Package) -> dict[str, str]:
    """Bambu Studio stores the piece names users see in a side-car config."""
    part = "Metadata/model_settings.config"
    if part not in pkg.names:
        return {}
    try:
        root = ET.fromstring(pkg.zf.read(part))
    except ET.ParseError:
        return {}
    names: dict[str, str] = {}
    for obj in root.iter("object"):
        oid = obj.get("id")
        if not oid:
            continue
        for md in obj.findall("metadata"):
            if md.get("key") == "name" and md.get("value"):
                names[oid] = md.get("value")
                break
    return names


def _collect(pkg: _Package, part: str, object_id: str, transform: tuple[float, ...],
             scale: float, out: list, budget: list[int], depth: int = 0) -> None:
    """Walk one object, appending world-space triangles to `out`.

    `budget` is a single-item list acting as a shared triangle counter so a
    pathological file can't blow up memory. `depth` guards against a
    components cycle (a malformed file referencing itself).
    """
    if depth > 32:
        raise MeshInputError("component nesting deeper than 32 levels — cyclic .3mf?")

    objects = pkg.objects(part)
    obj = objects.get(object_id)
    if obj is None:
        raise MeshInputError(f"object id {object_id} not found in {part}")

    mesh = obj.find(f"{CORE_NS}mesh")
    if mesh is not None:
        verts_el = mesh.find(f"{CORE_NS}vertices")
        tris_el = mesh.find(f"{CORE_NS}triangles")
        if verts_el is not None and tris_el is not None:
            verts = [
                apply_transform(transform, (float(v.get("x")) * scale,
                                            float(v.get("y")) * scale,
                                            float(v.get("z")) * scale))
                for v in verts_el.findall(f"{CORE_NS}vertex")
            ]
            n = len(verts)
            for tri in tris_el.findall(f"{CORE_NS}triangle"):
                budget[0] += 1
                if budget[0] > MAX_TRIANGLES:
                    raise MeshInputError(
                        f"model exceeds {MAX_TRIANGLES:,} triangles — refusing to load")
                try:
                    i1, i2, i3 = int(tri.get("v1")), int(tri.get("v2")), int(tri.get("v3"))
                except (TypeError, ValueError) as exc:
                    raise MeshInputError(f"triangle with a bad vertex index in {part}") from exc
                if not (0 <= i1 < n and 0 <= i2 < n and 0 <= i3 < n):
                    raise MeshInputError(f"triangle references a vertex out of range in {part}")
                out.append((verts[i1], verts[i2], verts[i3]))

    comps = obj.find(f"{CORE_NS}components")
    if comps is not None:
        for comp in comps.findall(f"{CORE_NS}component"):
            cid = comp.get("objectid")
            if not cid:
                continue
            cpath = comp.get(f"{PROD_NS}path") or part
            child_scale = pkg.unit_scale(_normalize(cpath)) if _normalize(cpath) != part else scale
            _collect(pkg, _normalize(cpath), cid,
                     compose(transform, parse_transform(comp.get("transform"))),
                     child_scale, out, budget, depth + 1)


def read_3mf(path: Path) -> list[Piece]:
    """Return one Piece per <build><item>, triangles already in world mm."""
    if not zipfile.is_zipfile(path):
        raise MeshInputError(f"{path.name} is not a zip archive — is it really a .3mf?")

    with zipfile.ZipFile(path) as zf:
        pkg = _Package(zf)
        if ROOT_MODEL not in pkg.names:
            raise MeshInputError(f"{path.name} has no {ROOT_MODEL} — not a valid .3mf")

        root = pkg.model(ROOT_MODEL)
        build = root.find(f"{CORE_NS}build")
        items = build.findall(f"{CORE_NS}item") if build is not None else []
        if not items:
            raise MeshInputError(f"{path.name} has an empty <build> — nothing to place")

        names = _bambu_names(pkg)
        root_scale = pkg.unit_scale(ROOT_MODEL)
        budget = [0]
        pieces: list[Piece] = []

        for i, item in enumerate(items, start=1):
            oid = item.get("objectid")
            if not oid:
                continue
            part = _normalize(item.get(f"{PROD_NS}path") or ROOT_MODEL)
            scale = pkg.unit_scale(part) if part != ROOT_MODEL else root_scale
            tris: list = []
            _collect(pkg, part, oid, parse_transform(item.get("transform")),
                     scale, tris, budget)
            if not tris:
                continue  # placeholder / empty object, nothing to print
            obj_name = names.get(oid) or _object_name(pkg, part, oid) or f"object {oid}"
            pieces.append(Piece(index=len(pieces) + 1, object_id=oid,
                                name=obj_name, triangles=tris))

    if not pieces:
        raise MeshInputError("no triangles found in this .3mf")
    return pieces


def _object_name(pkg: _Package, part: str, oid: str) -> str | None:
    obj = pkg.objects(part).get(oid)
    if obj is None:
        return None
    if obj.get("name"):
        return obj.get("name")
    for md in obj.findall(f"{CORE_NS}metadata"):
        if md.get("name", "").endswith("name") and md.text:
            return md.text.strip()
    return None


# ------------------------------------------------------------------ stl write

def write_binary_stl(triangles: list, out: Path, header: str = "logo3d") -> Path:
    """Write triangles as a binary STL, with per-facet normals recomputed."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        fh.write(header.encode("ascii", "replace")[:80].ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(triangles)))
        for a, b, c in triangles:
            u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            nx = u[1] * v[2] - u[2] * v[1]
            ny = u[2] * v[0] - u[0] * v[2]
            nz = u[0] * v[1] - u[1] * v[0]
            length = (nx * nx + ny * ny + nz * nz) ** 0.5
            if length:
                nx, ny, nz = nx / length, ny / length, nz / length
            fh.write(struct.pack("<12fH", nx, ny, nz,
                                 *a, *b, *c, 0))
    return out


# --------------------------------------------------------------- public API

def list_pieces(path: Path) -> list[Piece]:
    """Pieces inside a model. An .stl is always a single unnamed piece."""
    suffix = path.suffix.lower()
    if suffix == ".stl":
        return [Piece(index=1, object_id="-", name=path.stem)]
    if suffix == ".3mf":
        return read_3mf(path)
    raise MeshInputError(f"unsupported input {suffix or path.name!r} — use .stl or .3mf")


def to_stl(path: Path, workdir: Path, select: int | None = None,
           merge: bool = False) -> tuple[Path, str]:
    """Return (stl_path, label). For .stl inputs the file is used as-is.

    select is 1-based over the pieces reported by list_pieces(). merge=True
    fuses every piece into one mesh, keeping their relative placement.
    """
    if path.suffix.lower() == ".stl":
        return path, path.stem

    pieces = read_3mf(path)

    if merge:
        tris = [t for p in pieces for t in p.triangles]
        out = write_binary_stl(tris, workdir / f"{path.stem}_merged.stl")
        return out, f"{len(pieces)} pieces merged"

    if select is None:
        if len(pieces) > 1:
            listing = "\n".join(_describe(p) for p in pieces)
            raise MeshInputError(
                f"{path.name} holds {len(pieces)} pieces — pick one with --piece N "
                f"(or --merge-pieces to fuse them all):\n{listing}")
        select = 1

    if not 1 <= select <= len(pieces):
        raise MeshInputError(f"--piece {select} is out of range: this file has "
                             f"{len(pieces)} piece(s)")

    piece = pieces[select - 1]
    out = write_binary_stl(piece.triangles, workdir / f"{path.stem}_p{select}.stl")
    return out, piece.name


def _describe(p: Piece) -> str:
    w, d, h = p.bbox_mm()
    return f"  {p.index}. {p.name}  ({w:.0f} x {d:.0f} x {h:.0f} mm, {p.tri_count:,} tris)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect a .3mf or convert a piece of it to .stl")
    ap.add_argument("model", type=Path, help="input .3mf (or .stl, which is passed through)")
    ap.add_argument("--list", action="store_true", help="list the pieces and exit")
    ap.add_argument("--select", type=int, default=None, help="1-based piece to extract")
    ap.add_argument("--merge", action="store_true", help="fuse every piece into one mesh")
    ap.add_argument("--out", type=Path, default=None, help="output .stl path")
    args = ap.parse_args()

    try:
        if args.list:
            for p in list_pieces(args.model):
                print(_describe(p))
            return 0
        out_dir = (args.out.parent if args.out else Path.cwd())
        stl, label = to_stl(args.model, out_dir, select=args.select, merge=args.merge)
        if args.out and stl != args.out:
            stl.replace(args.out)
            stl = args.out
        print(f"{stl}  ({label})")
    except MeshInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

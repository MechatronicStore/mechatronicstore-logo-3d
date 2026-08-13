"""logo3d — put the MechatronicStore logo on any 3D-printable part.

Takes a mesh (.stl, or .3mf as downloaded from MakerWorld / Printables) plus a
logo (.svg) and writes a multicolor .glb — one mesh per logo color, which is
what Bambu Studio needs to assign a filament per color.

Two backends:

  --mode geom (default)  Each SVG color becomes its own extruded mesh. Sharp
      letters, one filament per mesh. This is the one you want.

  --mode texture         Single mesh with a UV-mapped texture, relying on Bambu
      Studio's Texture-to-Color detector. Keeps the original geometry untouched,
      but per-triangle color sampling makes small logos look blocky.

Quick start:
    python logo3d.py --model examples/coaster_simple.stl --logo m --preview
    python logo3d.py --model "MakerWorld part.3mf" --list-pieces
    python logo3d.py --model "MakerWorld part.3mf" --piece 2 --logo full

Requires Blender 5.x (or 4.x) on PATH, or $BLENDER_PATH pointing at it.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mesh_input import MeshInputError, list_pieces, to_stl, _describe

HERE = Path(__file__).parent
ASSETS = HERE / "assets"

# Friendly names for the bundled logos.
LOGO_ALIASES = {
    "m": ASSETS / "logo-m.svg",             # the m on its own — small parts
    "full": ASSETS / "logo-full.svg",       # full wordmark — flat, wide surfaces
}


def resolve_blender() -> str:
    """Locate Blender: $BLENDER_PATH, then PATH, then the macOS default."""
    env = os.environ.get("BLENDER_PATH")
    if env and Path(env).exists():
        return env
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    mac_default = "/Applications/Blender.app/Contents/MacOS/Blender"
    if Path(mac_default).exists():
        return mac_default
    sys.exit("ERROR: Blender not found. Install it, put it on PATH, or set BLENDER_PATH.")


def resolve_logo(value: str) -> Path:
    """Accept 'm', 'full', or a path to any SVG."""
    if value in LOGO_ALIASES:
        path = LOGO_ALIASES[value]
        if not path.exists():
            sys.exit(f"ERROR: bundled logo missing: {path}")
        return path
    path = Path(value).expanduser()
    if not path.exists():
        sys.exit(f"ERROR: logo not found: {path}\n"
                 f"       Use --logo m, --logo full, or a path to an .svg file.")
    if path.suffix.lower() != ".svg":
        sys.exit(f"ERROR: the logo must be an .svg (got {path.suffix or 'no extension'}). "
                 f"Raster images (.png/.jpg) only work in --mode texture via --logo-png.")
    return path


def run_geom(args, blender: str, stl: Path) -> None:
    cmd = [blender, "--background", "--python", str(HERE / "apply_logo_geom.py"), "--",
           "--stl", str(stl),
           "--svg", str(args.logo_svg),
           "--out", str(args.out),
           "--logo-size-mm", str(args.logo_size_mm),
           "--logo-height-mm", str(args.logo_height_mm),
           "--offset-x-mm", str(args.offset_x_mm),
           "--offset-y-mm", str(args.offset_y_mm)]
    if args.circuits:
        cmd += ["--circuits", "--circuit-color", args.circuit_color]
    if args.also_3mf:
        cmd.append("--also-3mf")
    if args.engrave:
        cmd.append("--engrave")
    if args.also_stls:
        cmd.append("--also-stls")
    subprocess.run(cmd, check=True)


def run_texture(args, blender: str, stl: Path, workdir: Path) -> None:
    png = args.out.with_suffix(".png") if args.keep_png else workdir / "logo.png"

    print(f"[1/2] SVG -> PNG  size={args.png_size}px  bg={args.base_color}")
    cmd = [sys.executable, str(HERE / "svg_to_png.py"), str(args.logo_svg), str(png),
           "--size", str(args.png_size), "--bg", args.base_color]
    if args.palette:
        cmd += ["--palette", args.palette]
    subprocess.run(cmd, check=True)

    print(f"[2/2] Blender -> GLB  face={args.face} angle={args.face_angle_deg} "
          f"logo={args.logo_size_mm}mm")
    subprocess.run(
        [blender, "--background", "--python", str(HERE / "apply_logo.py"), "--",
         "--stl", str(stl),
         "--logo", str(png),
         "--out", str(args.out),
         "--logo-size-mm", str(args.logo_size_mm),
         "--base-color", args.base_color,
         "--face", args.face,
         "--face-angle-deg", str(args.face_angle_deg),
         "--face-depth-mm", str(args.face_depth_mm),
         "--subdivide-target-tris", str(args.subdivide_target_tris)],
        check=True,
    )


def render_previews(blender: str, glb: Path) -> Path:
    """Render top/front/right/iso PNGs next to the GLB. Look at them before printing."""
    outdir = glb.parent / f"{glb.stem}_previews"
    outdir.mkdir(parents=True, exist_ok=True)
    subprocess.run([blender, "--background", "--python", str(HERE / "render_previews.py"), "--",
                    "--glb", str(glb), "--outdir", str(outdir)], check=True)
    return outdir


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", "--stl", dest="model", required=True, type=Path,
                   metavar="FILE", help="Input mesh: .stl or .3mf (MakerWorld exports work)")
    p.add_argument("--logo", default="m", metavar="M|FULL|FILE.SVG",
                   help="Bundled 'm' (default), bundled 'full', or a path to your own .svg")
    p.add_argument("--out", type=Path, default=None,
                   help="Output .glb (default: <model name>_logo.glb next to the input)")
    p.add_argument("--mode", default="geom", choices=["geom", "texture"],
                   help="geom = extruded meshes (default, recommended); texture = UV map")
    p.add_argument("--logo-size-mm", type=float, default=None,
                   help="Logo width in mm (default: 45%% of the part's shortest top edge)")
    p.add_argument("--preview", action="store_true",
                   help="Render 4 preview PNGs of the result — do this before printing")

    piece = p.add_argument_group("3mf pieces")
    piece.add_argument("--list-pieces", action="store_true",
                       help="List the pieces inside a .3mf and exit")
    piece.add_argument("--piece", type=int, default=None, metavar="N",
                       help="Which piece of the .3mf to use (1-based, see --list-pieces)")
    piece.add_argument("--merge-pieces", action="store_true",
                       help="Fuse every piece of the .3mf into one mesh")

    geom = p.add_argument_group("geom mode")
    geom.add_argument("--logo-height-mm", type=float, default=0.6,
                      help="How far the logo sticks out (or sinks in). Default 0.6")
    geom.add_argument("--offset-x-mm", type=float, default=0.0,
                      help="Shift the logo along X, in mm, from the center of the top face")
    geom.add_argument("--offset-y-mm", type=float, default=0.0,
                      help="Shift the logo along Y, in mm, from the center of the top face")
    geom.add_argument("--engrave", action="store_true",
                      help="Sink the logo into the surface instead of raising it")
    geom.add_argument("--circuits", action="store_true",
                      help="Add a procedural PCB-trace decoration around the logo")
    geom.add_argument("--circuit-color", default="yellow",
                      choices=["yellow", "purple", "red"], help="Filament group for --circuits")
    geom.add_argument("--also-3mf", action="store_true",
                      help="Also write a .3mf next to the .glb")
    geom.add_argument("--also-stls", action="store_true",
                      help="Also write one .stl per color, for slicers without GLB support")

    tex = p.add_argument_group("texture mode")
    tex.add_argument("--base-color", default="#1a1a1a", help="Color of everything but the logo")
    tex.add_argument("--face", default="top",
                     choices=["top", "bottom", "front", "back", "right", "left"],
                     help="Which face to project onto (default: top)")
    tex.add_argument("--face-angle-deg", type=float, default=25.0,
                     help="Faces within N degrees of the target normal count")
    tex.add_argument("--face-depth-mm", type=float, default=0.5,
                     help="Only faces within N mm of the bbox extreme count")
    tex.add_argument("--png-size", type=int, default=2048, help="Intermediate PNG resolution")
    tex.add_argument("--palette", default=None,
                     help="Comma-separated hex colors to pin the PNG palette to")
    tex.add_argument("--subdivide-target-tris", type=int, default=8000,
                     help="Subdivide the target face until it has N triangles")
    tex.add_argument("--keep-png", action="store_true", help="Keep the intermediate PNG")
    return p


def default_logo_size_mm(stl: Path) -> float:
    """45% of the shortest horizontal edge — a size that fits without guessing."""
    import struct

    try:
        with stl.open("rb") as fh:
            fh.seek(80)
            count = struct.unpack("<I", fh.read(4))[0]
            xs, ys = [], []
            step = max(1, count // 4000)  # sample big meshes instead of reading it all
            for i in range(count):
                data = fh.read(50)
                if len(data) < 50:
                    break
                if i % step:
                    continue
                vals = struct.unpack("<12fH", data)[3:12]
                xs += [vals[0], vals[3], vals[6]]
                ys += [vals[1], vals[4], vals[7]]
        if not xs:
            return 40.0
        shortest = min(max(xs) - min(xs), max(ys) - min(ys))
        return round(max(8.0, shortest * 0.45), 1)
    except Exception:
        return 40.0


def main() -> int:
    args = build_parser().parse_args()

    if not args.model.exists():
        sys.exit(f"ERROR: model not found: {args.model}")

    try:
        if args.list_pieces:
            pieces = list_pieces(args.model)
            print(f"{args.model.name} — {len(pieces)} piece(s):")
            for piece in pieces:
                print(_describe(piece))
            print("\nPick one with --piece N, or fuse them all with --merge-pieces.")
            return 0

        blender = resolve_blender()
        args.logo_svg = resolve_logo(args.logo)
        if args.out is None:
            args.out = args.model.with_name(f"{args.model.stem}_logo.glb")
        args.out.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="logo3d_") as tmp:
            workdir = Path(tmp)
            stl, label = to_stl(args.model, workdir,
                               select=args.piece, merge=args.merge_pieces)
            if args.model.suffix.lower() == ".3mf":
                print(f"[logo3d] 3mf piece: {label}")

            if args.logo_size_mm is None:
                args.logo_size_mm = default_logo_size_mm(stl)
                print(f"[logo3d] logo size not given, using {args.logo_size_mm} mm")

            print(f"[logo3d] mode={args.mode} logo={args.logo_svg.name} "
                  f"size={args.logo_size_mm}mm"
                  f"{' engraved' if args.engrave else ''}")

            if args.mode == "geom":
                run_geom(args, blender, stl)
            else:
                run_texture(args, blender, stl, workdir)

        if not args.out.exists():
            sys.exit("ERROR: Blender finished but no .glb was written — see the log above.")

        print(f"\nOK  {args.out}  ({args.out.stat().st_size // 1024} KB)")

        if args.preview:
            outdir = render_previews(blender, args.out)
            print(f"    previews: {outdir}  <- open these and check the logo before printing")

        print("\nHow to open it in Bambu Studio (this part matters):")
        print("  1. Launch Bambu Studio first, with no file open.")
        print("  2. Drag the .glb onto the build plate. Double-clicking the file does NOT work.")
        print("  3. In texture mode, accept 'Convert texture to color painting'.")
        print("  4. Assign a filament to each color, then slice.")
        return 0

    except MeshInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: {Path(exc.cmd[0]).name} exited with {exc.returncode} — see the log above.",
              file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Batch-process images: resize, convert format, set quality, strip EXIF.

Walks a folder (optionally recursive), writes results to an output folder
mirroring the original structure, and never touches source files. Resizing
preserves aspect ratio: "--resize 1280" caps the longest side, "--resize
1280x720" fits inside that box (images are never upscaled).

Without --format every image keeps its own format (a JPEG stays a real JPEG,
so --quality applies). EXIF orientation is baked into the pixels and the
Orientation tag is reset to 1, so photos never end up sideways -- neither
with --strip-exif nor when EXIF is kept. Colour profiles (ICC) are kept.
Transparent images converted to JPEG are flattened onto white, not black.
If two sources would produce the same output name (logo.png and logo.jpg
-> logo.webp), both keep their original extension in the name instead
(logo.png.webp, logo.jpg.webp) so nothing is silently overwritten.

Usage:
    python 05_image_batch.py ./photos --out ./photos_web --resize 1600 --format webp
    python 05_image_batch.py ./scans --out ./small --resize 1280x720 --quality 80 --recursive
    python 05_image_batch.py ./export --out ./clean --strip-exif

Dependencies: Pillow
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif"}
FORMAT_MAP = {"webp": ("WEBP", ".webp"), "jpeg": ("JPEG", ".jpg"), "png": ("PNG", ".png")}
# Pillow format names we can write back when the source format is kept.
KEEPABLE = {"JPEG", "PNG", "WEBP", "BMP", "TIFF", "GIF"}
EXIF_FORMATS = {"JPEG", "WEBP", "PNG", "TIFF"}
ORIENTATION_TAG = 0x0112


def parse_resize(spec: str) -> tuple[int, int]:
    """'1280' -> (1280, 1280) box; '1280x720' -> (1280, 720) box."""
    try:
        if "x" in spec.lower():
            w, _, h = spec.lower().partition("x")
            box = int(w), int(h)
        else:
            side = int(spec)
            box = side, side
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{spec}' is not a size; use 1600 (longest side) or 1280x720 (box)") from None
    if min(box) < 1:
        raise argparse.ArgumentTypeError("sizes must be positive")
    return box


def quality_arg(value: str) -> int:
    try:
        q = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a number") from None
    if not 1 <= q <= 100:
        raise argparse.ArgumentTypeError("quality must be between 1 and 100")
    return q


def target_for(src: Path, src_format: str | None, wanted: str | None) -> tuple[str, str]:
    """Return (Pillow format, file suffix) for the output of `src`."""
    if wanted:
        return FORMAT_MAP[wanted]
    if src_format in KEEPABLE:
        return src_format, src.suffix
    return "PNG", ".png"  # exotic source format: fall back to lossless PNG


def plan_outputs(files: list[Path], src_root: Path, out_root: Path,
                 wanted: str | None) -> dict[Path, Path]:
    """Map every source to an output path, disambiguating name collisions."""
    planned: dict[Path, Path] = {}
    for src in files:
        suffix = FORMAT_MAP[wanted][1] if wanted else src.suffix
        planned[src] = (out_root / src.relative_to(src_root)).with_suffix(suffix)
    groups: dict[str, list[Path]] = defaultdict(list)
    for src, dest in planned.items():
        groups[str(dest).lower()].append(src)
    for sources in groups.values():
        if len(sources) > 1:
            for src in sources:
                dest = planned[src]
                planned[src] = dest.with_name(src.name + dest.suffix)
    return planned


def flatten_alpha(img: Image.Image) -> Image.Image:
    """Composite transparent images onto white (JPEG has no alpha channel)."""
    if img.mode == "P":
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA", "PA"):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if img.mode not in ("RGB", "L", "CMYK"):
        return img.convert("RGB")
    return img


def process_one(src: Path, dest: Path, args) -> bool:
    try:
        with Image.open(src) as original:
            src_format = original.format          # exif_transpose() drops .format
            icc = original.info.get("icc_profile")
            had_orientation = ORIENTATION_TAG in original.getexif()
            img = ImageOps.exif_transpose(original)   # rotates the pixels
            exif = img.getexif()
            if had_orientation:
                exif[ORIENTATION_TAG] = 1             # pixels are upright now

            fmt, _suffix = target_for(src, src_format, args.format)

            deep = img.mode in ("I", "F") or img.mode.startswith("I;16")
            if deep and fmt not in ("PNG", "TIFF"):
                # 16-bit scans/masks -> 8-bit by scaling, not by clipping to white
                img = img.convert("I").point(lambda v: v * (1 / 256)).convert("L")

            if args.resize:
                mode = img.mode
                if mode.startswith("I;16"):
                    img = img.convert("I")      # LANCZOS cannot resample I;16 directly
                img.thumbnail(args.resize, Image.Resampling.LANCZOS)
                if img.mode != mode:
                    img = img.convert(mode)

            if fmt == "JPEG":
                img = flatten_alpha(img)
            elif fmt == "WEBP" and img.mode not in ("RGB", "RGBA", "L", "LA"):
                img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")

            save_kwargs: dict = {}
            if fmt in ("JPEG", "WEBP"):
                save_kwargs["quality"] = args.quality
            if fmt in ("JPEG", "PNG"):
                save_kwargs["optimize"] = True
            if icc and fmt in ("JPEG", "WEBP", "PNG", "TIFF"):
                save_kwargs["icc_profile"] = icc
            if not args.strip_exif and len(exif) and fmt in EXIF_FORMATS:
                save_kwargs["exif"] = exif.tobytes()

            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, fmt, **save_kwargs)

        before, after = src.stat().st_size, dest.stat().st_size
        print(f"  {src.name} -> {dest.name}  [{fmt}]  ({before // 1024} KB -> {after // 1024} KB)")
        return True
    except Exception as exc:  # noqa: BLE001 - report and keep batch running
        print(f"  FAILED {src.name}: {exc}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch resize/convert images into a separate output folder."
    )
    parser.add_argument("folder", help="input folder with images")
    parser.add_argument("--out", required=True, help="output folder (created if missing)")
    parser.add_argument("--resize", type=parse_resize,
                        help="max size: '1600' longest side, or '1280x720' box")
    parser.add_argument("--format", choices=sorted(FORMAT_MAP),
                        help="convert to this format (default: keep each file's format)")
    parser.add_argument("--quality", type=quality_arg, default=85,
                        help="JPEG/WebP quality 1-100 (default: 85)")
    parser.add_argument("--strip-exif", action="store_true",
                        help="drop all EXIF metadata (orientation is baked in first)")
    parser.add_argument("--recursive", action="store_true", help="include subfolders")
    args = parser.parse_args()

    src_root = Path(args.folder).expanduser().resolve()
    out_root = Path(args.out).expanduser().resolve()
    if not src_root.is_dir():
        sys.exit(f"Not a folder: {src_root}")
    if out_root == src_root or out_root.is_relative_to(src_root):
        sys.exit("Output folder must be outside the input folder.")

    files = sorted(p for p in (src_root.rglob("*") if args.recursive else src_root.iterdir())
                   if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    if not files:
        print("No images found.")
        return

    outputs = plan_outputs(files, src_root, out_root, args.format)
    renamed = [src for src in files
               if outputs[src].stem != src.stem]
    if renamed:
        print(f"Note: {len(renamed)} file(s) share an output name; "
              "their original extension is kept in the new name.")

    ok = 0
    for src in files:
        if process_one(src, outputs[src], args):
            ok += 1
    print(f"\nProcessed {ok}/{len(files)} images -> {out_root}")
    if ok != len(files):
        sys.exit(1)


if __name__ == "__main__":
    main()

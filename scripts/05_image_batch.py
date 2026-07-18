#!/usr/bin/env python3
"""Batch-process images: resize, convert format, set quality, strip EXIF.

Walks a folder (optionally recursive), writes results to an output folder
mirroring the original structure, and never touches source files. Resizing
preserves aspect ratio: "--resize 1280" caps the longest side, "--resize
1280x720" fits inside that box. EXIF orientation is baked in before any
metadata is stripped so photos never end up sideways.

Usage:
    python 05_image_batch.py ./photos --out ./photos_web --resize 1600 --format webp
    python 05_image_batch.py ./scans --out ./small --resize 1280x720 --quality 80 --recursive
    python 05_image_batch.py ./export --out ./clean --strip-exif

Dependencies: Pillow
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif"}
FORMAT_MAP = {"webp": ("WEBP", ".webp"), "jpeg": ("JPEG", ".jpg"), "png": ("PNG", ".png")}


def parse_resize(spec: str) -> tuple[int, int]:
    """'1280' -> (1280, 1280) box; '1280x720' -> (1280, 720) box."""
    if "x" in spec.lower():
        w, _, h = spec.lower().partition("x")
        return int(w), int(h)
    side = int(spec)
    return side, side


def process_one(src: Path, dest: Path, args) -> bool:
    try:
        with Image.open(src) as img:
            exif_bytes = img.info.get("exif")
            img = ImageOps.exif_transpose(img)

            if args.resize:
                img.thumbnail(parse_resize(args.resize), Image.LANCZOS)

            fmt, suffix = (FORMAT_MAP[args.format] if args.format
                           else (img.format or "PNG", src.suffix))
            dest = dest.with_suffix(suffix)

            if fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")

            save_kwargs: dict = {}
            if fmt in ("JPEG", "WEBP"):
                save_kwargs["quality"] = args.quality
            if exif_bytes and not args.strip_exif and fmt in ("JPEG", "WEBP"):
                save_kwargs["exif"] = exif_bytes

            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, fmt, **save_kwargs)

        before, after = src.stat().st_size, dest.stat().st_size
        print(f"  {src.name} -> {dest.name}  ({before // 1024} KB -> {after // 1024} KB)")
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
    parser.add_argument("--resize", help="max size: '1600' longest side, or '1280x720' box")
    parser.add_argument("--format", choices=sorted(FORMAT_MAP), help="convert to this format")
    parser.add_argument("--quality", type=int, default=85,
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

    ok = 0
    for src in files:
        rel = src.relative_to(src_root)
        if process_one(src, out_root / rel, args):
            ok += 1
    print(f"\nProcessed {ok}/{len(files)} images -> {out_root}")


if __name__ == "__main__":
    main()

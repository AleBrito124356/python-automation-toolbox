#!/usr/bin/env python3
"""Zip a folder into timestamped backups with rotation and a manifest.

Creates <name>_YYYYmmdd-HHMMSS.zip in the destination, embeds a JSON manifest
(file list, sizes, per-file mtimes) inside the archive, and keeps only the
last N backups of that same folder -- older ones are rotated out. Exclude
patterns use fnmatch globs and match both path segments and full relative
paths, so "node_modules" or "*.log" both work.

Usage:
    python 04_folder_backup.py C:/Projects/norden D:/Backups
    python 04_folder_backup.py C:/Projects/norden D:/Backups --keep 8 --exclude node_modules .venv "*.log"
    python 04_folder_backup.py ~/Documents ~/Backups --keep 12 --exclude "*.tmp"

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def is_excluded(rel: Path, patterns: list[str]) -> bool:
    rel_posix = rel.as_posix()
    for pat in patterns:
        if fnmatch(rel_posix, pat):
            return True
        if any(fnmatch(part, pat) for part in rel.parts):
            return True
    return False


def create_backup(src: Path, dest: Path, patterns: list[str]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = dest / f"{src.name}_{stamp}.zip"
    manifest: dict = {
        "source": str(src),
        "created": datetime.now().isoformat(timespec="seconds"),
        "excludes": patterns,
        "files": [],
    }
    total = 0
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(src)
            if is_excluded(rel, patterns):
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                print(f"  unreadable, skipped: {rel} ({exc})", file=sys.stderr)
                continue
            zf.write(path, arcname=f"{src.name}/{rel.as_posix()}")
            manifest["files"].append({
                "path": rel.as_posix(),
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
            total += stat.st_size
            count += 1
        manifest["file_count"] = count
        manifest["total_bytes"] = total
        zf.writestr(f"{src.name}/_backup_manifest.json", json.dumps(manifest, indent=2))
    print(f"Backed up {count} files ({human(total)}) -> {zip_path.name} "
          f"({human(zip_path.stat().st_size)} compressed)")
    return zip_path


def rotate(src_name: str, dest: Path, keep: int) -> None:
    backups = sorted(dest.glob(f"{src_name}_*.zip"))
    while len(backups) > keep:
        oldest = backups.pop(0)
        oldest.unlink()
        print(f"Rotated out old backup: {oldest.name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zip backup of a folder with keep-last-N rotation and manifest."
    )
    parser.add_argument("source", help="folder to back up")
    parser.add_argument("dest", help="destination folder for the .zip files")
    parser.add_argument("--keep", type=int, default=5,
                        help="how many backups of this folder to keep (default: 5)")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="fnmatch patterns to skip, e.g. node_modules .venv *.log")
    args = parser.parse_args()

    src = Path(args.source).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"Source is not a folder: {src}")
    if src == dest or dest.is_relative_to(src):
        sys.exit("Destination must be outside the source folder.")
    dest.mkdir(parents=True, exist_ok=True)

    create_backup(src, dest, args.exclude)
    rotate(src.name, dest, max(args.keep, 1))

    remaining = sorted(dest.glob(f"{src.name}_*.zip"))
    print(f"{len(remaining)} backup(s) on disk for '{src.name}'.")


if __name__ == "__main__":
    main()

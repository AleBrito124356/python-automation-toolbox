#!/usr/bin/env python3
"""Archive stale files out of your Downloads folder into dated categories.

Files older than N days (by modified time) move to
Downloads/Archive/YYYY-MM/Category/. Nothing is ever deleted, the Archive
folder itself is never re-processed, and the default is a dry-run preview.
Applied runs write a JSON moves log inside the Archive folder.

Usage:
    python 10_downloads_cleaner.py
    python 10_downloads_cleaner.py --days 60 --apply
    python 10_downloads_cleaner.py C:/Users/me/Desktop --days 14 --apply

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ARCHIVE_NAME = "Archive"

CATEGORY_RULES: dict[str, set[str]] = {
    "Installers": {".exe", ".msi", ".dmg", ".pkg", ".appimage"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".iso"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".svg", ".bmp"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md"},
    "Video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Audio": {".mp3", ".wav", ".m4a", ".flac", ".ogg"},
}


def category_for(path: Path) -> str:
    ext = path.suffix.lower()
    for name, exts in CATEGORY_RULES.items():
        if ext in exts:
            return name
    return "Other"


def unique_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move files older than N days into a dated, categorized Archive folder."
    )
    parser.add_argument("folder", nargs="?", default=str(Path.home() / "Downloads"),
                        help="folder to clean (default: ~/Downloads)")
    parser.add_argument("--days", type=int, default=30,
                        help="minimum age in days to archive (default: 30)")
    parser.add_argument("--apply", action="store_true",
                        help="actually move files (default is a dry-run preview)")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")

    cutoff = time.time() - args.days * 86400
    archive_root = folder / ARCHIVE_NAME

    plan: list[tuple[Path, Path]] = []
    for item in sorted(folder.iterdir()):
        if item.is_dir() or item.name.startswith("."):
            continue
        try:
            mtime = item.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        month = datetime.fromtimestamp(mtime).strftime("%Y-%m")
        dest = archive_root / month / category_for(item) / item.name
        plan.append((item, dest))

    if not plan:
        print(f"Nothing older than {args.days} days in {folder}.")
        return

    total = 0
    for src, dest in plan:
        total += src.stat().st_size
        age_days = int((time.time() - src.stat().st_mtime) / 86400)
        print(f"  {src.name}  ({age_days}d old)  ->  {dest.relative_to(folder)}")
    print(f"\n{len(plan)} file(s), {total / (1024 * 1024):,.1f} MB total.")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to move files.")
        return

    log: list[dict[str, str]] = []
    for src, dest in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        final = unique_path(dest)
        src.rename(final)
        log.append({"from": str(src), "to": str(final)})

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = archive_root / f"cleaner_moves_{stamp}.json"
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"Archived {len(log)} file(s). Moves log: {log_path}")


if __name__ == "__main__":
    main()

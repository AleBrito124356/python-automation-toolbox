#!/usr/bin/env python3
"""Archive stale files out of your Downloads folder into dated categories.

Files older than N days (by modified time) move to
Downloads/Archive/YYYY-MM/Category/. Nothing is ever deleted, the Archive
folder itself is never re-processed, and the default is a dry-run preview.
Applied runs write a JSON moves log inside the Archive folder, and
--undo LOG moves every file back and removes the folders it emptied.

Never touched: downloads still in progress (.crdownload, .part, .partial,
.download, .opdownload, .tmp), folder metadata such as desktop.ini and
Thumbs.db (moving desktop.ini breaks the Downloads folder's name and icon),
Windows system files and dotfiles. A file that is locked by another
program is reported and skipped; the rest of the run continues.

Usage:
    python 10_downloads_cleaner.py
    python 10_downloads_cleaner.py --days 60 --apply
    python 10_downloads_cleaner.py --undo C:/Users/me/Downloads/Archive/cleaner_moves_20260718-090000.json

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import stat
import sys
import time
from datetime import datetime
from pathlib import Path

ARCHIVE_NAME = "Archive"
LOG_PREFIX = "cleaner_moves_"

CATEGORY_RULES: dict[str, set[str]] = {
    "Installers": {".exe", ".msi", ".dmg", ".pkg", ".appimage"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".iso"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".svg", ".bmp"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv", ".md"},
    "Video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Audio": {".mp3", ".wav", ".m4a", ".flac", ".ogg"},
}

PARTIAL_SUFFIXES = {".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp"}
PROTECTED_NAMES = {"desktop.ini", "thumbs.db", "ehthumbs.db", ".ds_store", "icon\r"}


def category_for(path: Path) -> str:
    ext = path.suffix.lower()
    for name, exts in CATEGORY_RULES.items():
        if ext in exts:
            return name
    return "Other"


def skip_reason(item: Path) -> str | None:
    """Why this file must never be archived, or None if it is fair game."""
    name = item.name.lower()
    if name in PROTECTED_NAMES:
        return "folder metadata"
    if item.name.startswith("."):
        return "hidden"
    if item.suffix.lower() in PARTIAL_SUFFIXES:
        return "download in progress"
    attrs = getattr(item.stat(), "st_file_attributes", 0)
    if attrs & getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4):
        return "system file"
    return None


def unique_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def plan_moves(folder: Path, days: float, now: float | None = None) -> list[tuple[Path, Path]]:
    cutoff = (now if now is not None else time.time()) - days * 86400
    archive_root = folder / ARCHIVE_NAME
    plan: list[tuple[Path, Path]] = []
    for item in sorted(folder.iterdir()):
        try:
            if item.is_dir() or skip_reason(item):
                continue
            mtime = item.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        month = datetime.fromtimestamp(mtime).strftime("%Y-%m")
        plan.append((item, archive_root / month / category_for(item) / item.name))
    return plan


def unique_log_path(archive_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = archive_root / f"{LOG_PREFIX}{stamp}.json"
    n = 1
    while path.exists():
        path = archive_root / f"{LOG_PREFIX}{stamp}_{n}.json"
        n += 1
    return path


def apply_moves(plan: list[tuple[Path, Path]], archive_root: Path) -> tuple[Path | None, int]:
    """Move files, logging each success. Returns (log path, failures)."""
    log: list[dict[str, str]] = []
    failures = 0
    log_path: Path | None = None
    try:
        for src, dest in plan:
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                final = unique_path(dest)
                src.rename(final)
            except OSError as exc:
                failures += 1
                print(f"  skipped {src.name}: {exc.strerror or exc}", file=sys.stderr)
                continue
            log.append({"from": str(src), "to": str(final)})
    finally:
        if log:  # written even if something unexpected interrupts the loop
            archive_root.mkdir(parents=True, exist_ok=True)
            log_path = unique_log_path(archive_root)
            log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    return log_path, failures


def prune_empty_dirs(start: Path, stop: Path) -> int:
    """Remove empty folders from `start` upwards, never `stop` itself. Returns count."""
    removed = 0
    current = start.resolve()
    stop = stop.resolve()
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            break  # not empty: other archived files still live here
        removed += 1
        current = current.parent
    return removed


def undo(log_file: Path) -> int:
    if not log_file.is_file():
        sys.exit(f"Moves log not found: {log_file}")
    try:
        entries = json.loads(log_file.read_text(encoding="utf-8"))
        pairs = [(Path(e["to"]), Path(e["from"])) for e in entries]
    except (ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Not a cleaner moves log: {log_file} ({exc})")
    archive_root = log_file.parent
    restored = 0
    for current, original in reversed(pairs):
        if not current.exists():
            print(f"  skip (no longer in the archive): {current}")
            continue
        if original.exists():
            print(f"  skip (a file already exists at the original path): {original}")
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            current.rename(original)
        except OSError as exc:
            print(f"  failed {current.name}: {exc.strerror or exc}", file=sys.stderr)
            continue
        restored += 1
        prune_empty_dirs(current.parent, archive_root)
    print(f"Restored {restored}/{len(pairs)} file(s) to {archive_root.parent}.")
    return restored


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Move files older than N days into a dated, categorized Archive folder."
    )
    parser.add_argument("folder", nargs="?", default=str(Path.home() / "Downloads"),
                        help="folder to clean (default: ~/Downloads)")
    parser.add_argument("--days", type=float, default=30,
                        help="minimum age in days to archive (default: 30)")
    parser.add_argument("--apply", action="store_true",
                        help="actually move files (default is a dry-run preview)")
    parser.add_argument("--undo", metavar="LOG",
                        help="move everything from a cleaner_moves_*.json log back")
    args = parser.parse_args()

    if args.undo:
        undo(Path(args.undo).expanduser())
        return

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")

    archive_root = folder / ARCHIVE_NAME
    plan = plan_moves(folder, args.days)
    days_label = f"{args.days:g}"
    if not plan:
        print(f"Nothing older than {days_label} days in {folder}.")
        return

    total = 0
    now = time.time()
    for src, dest in plan:
        info = src.stat()
        total += info.st_size
        age_days = int((now - info.st_mtime) / 86400)
        print(f"  {src.name}  ({age_days}d old)  ->  {dest.relative_to(folder)}")
    print(f"\n{len(plan)} file(s), {total / (1024 * 1024):,.1f} MB total.")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to move files.")
        return

    log_path, failures = apply_moves(plan, archive_root)
    moved = len(plan) - failures
    if log_path:
        print(f"Archived {moved} file(s). Moves log: {log_path}")
        print(f"To reverse: python {Path(__file__).name} --undo \"{log_path}\"")
    if failures:
        print(f"{failures} file(s) could not be moved (in use?) and were left in place.")
        sys.exit(1)


if __name__ == "__main__":
    main()

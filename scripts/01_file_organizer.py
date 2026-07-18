#!/usr/bin/env python3
"""Sort a messy folder into tidy subfolders by file type, date, or both.

Files are grouped into category folders (Images, Documents, Audio, ...),
date folders (2026-07), or nested type/date folders. Dry-run by default:
nothing moves until you pass --apply. Every applied run writes a JSON undo
log inside the organized folder, so the whole operation is reversible.

Usage:
    python 01_file_organizer.py C:/Users/me/Downloads
    python 01_file_organizer.py C:/Users/me/Downloads --by type-date --apply
    python 01_file_organizer.py --undo C:/Users/me/Downloads/undo_20260718-104500.json

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

CATEGORIES: dict[str, set[str]] = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".svg", ".heic", ".ico"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".rtf", ".odt", ".csv", ".epub"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus"},
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv", ".m4v"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"},
    "Installers": {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".yaml", ".yml", ".sh", ".ps1", ".sql", ".ipynb"},
}


def category_for(path: Path) -> str:
    ext = path.suffix.lower()
    for name, exts in CATEGORIES.items():
        if ext in exts:
            return name
    return "Other"


def unique_path(dest: Path) -> Path:
    """Return dest, or dest with ' (1)', ' (2)', ... appended until it is free."""
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def plan_moves(folder: Path, mode: str) -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    for item in sorted(folder.iterdir()):
        if item.is_dir() or item.name.startswith("undo_") or item.name.startswith("."):
            continue
        parts: list[str] = []
        if "type" in mode:
            parts.append(category_for(item))
        if "date" in mode:
            stamp = datetime.fromtimestamp(item.stat().st_mtime)
            parts.append(stamp.strftime("%Y-%m"))
        dest = folder.joinpath(*parts) / item.name
        if dest == item:
            continue
        moves.append((item, dest))
    return moves


def apply_moves(moves: list[tuple[Path, Path]], folder: Path) -> Path:
    log: list[dict[str, str]] = []
    for src, dest in moves:
        dest.parent.mkdir(parents=True, exist_ok=True)
        final = unique_path(dest)
        src.rename(final)
        log.append({"from": str(src), "to": str(final)})
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = folder / f"undo_{stamp}.json"
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    return log_path


def undo(log_file: Path) -> None:
    if not log_file.is_file():
        sys.exit(f"Undo log not found: {log_file}")
    entries = json.loads(log_file.read_text(encoding="utf-8"))
    restored = 0
    for entry in reversed(entries):
        src = Path(entry["to"])
        dest = Path(entry["from"])
        if not src.exists():
            print(f"  skip (missing): {src}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(unique_path(dest))
        restored += 1
    print(f"Restored {restored}/{len(entries)} files.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Organize a folder into type and/or date subfolders. Dry-run by default."
    )
    parser.add_argument("folder", nargs="?", help="folder to organize")
    parser.add_argument("--by", choices=["type", "date", "type-date"], default="type",
                        help="grouping strategy (default: type)")
    parser.add_argument("--apply", action="store_true",
                        help="actually move files (default is a dry-run preview)")
    parser.add_argument("--undo", metavar="LOG",
                        help="reverse a previous run using its undo_*.json log")
    args = parser.parse_args()

    if args.undo:
        undo(Path(args.undo).expanduser())
        return
    if not args.folder:
        parser.error("provide a folder to organize, or --undo LOG")

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")

    moves = plan_moves(folder, args.by)
    if not moves:
        print("Nothing to organize.")
        return

    for src, dest in moves:
        print(f"  {src.name}  ->  {dest.relative_to(folder)}")
    print(f"\n{len(moves)} file(s) to move.")

    if args.apply:
        log_path = apply_moves(moves, folder)
        print(f"Done. Undo log: {log_path}")
        print(f"To reverse: python {Path(__file__).name} --undo \"{log_path}\"")
    else:
        print("Dry-run only. Re-run with --apply to move files.")


if __name__ == "__main__":
    main()

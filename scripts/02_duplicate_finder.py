#!/usr/bin/env python3
"""Find duplicate files by content and quarantine the copies. Never deletes.

Two-stage detection keeps large trees fast: files are bucketed by size first,
and only same-size files get hashed (BLAKE2b, chunked reads). Duplicates are
MOVED into a timestamped quarantine folder with a JSON manifest, so you can
review before deleting anything by hand -- or restore with the manifest.

Usage:
    python 02_duplicate_finder.py C:/Users/me/Pictures
    python 02_duplicate_finder.py C:/Users/me/Pictures --auto-keep-newest
    python 02_duplicate_finder.py D:/Archive --min-size 1048576 --auto-keep-newest

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CHUNK = 1024 * 1024  # 1 MiB read chunks


def hash_file(path: Path) -> str:
    h = hashlib.blake2b(digest_size=32)
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def collect_groups(root: Path, min_size: int) -> list[list[Path]]:
    """Return groups of files with identical content (2+ files each)."""
    by_size: dict[int, list[Path]] = defaultdict(list)
    scanned = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith("_duplicates_") for part in path.parts):
            continue  # never re-scan a previous quarantine folder
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < min_size:
            continue
        by_size[size].append(path)
        scanned += 1

    print(f"Scanned {scanned} files, hashing {sum(len(v) for v in by_size.values() if len(v) > 1)} size-collision candidates...")

    by_hash: dict[str, list[Path]] = defaultdict(list)
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for path in paths:
            try:
                by_hash[hash_file(path)].append(path)
            except OSError as exc:
                print(f"  unreadable, skipped: {path} ({exc})", file=sys.stderr)

    return [sorted(g, key=lambda p: p.stat().st_mtime, reverse=True)
            for g in by_hash.values() if len(g) > 1]


def choose_keeper(group: list[Path], auto: bool) -> int:
    """Return the index of the file to keep. Group is sorted newest first."""
    if auto:
        return 0
    print()
    for i, path in enumerate(group, start=1):
        mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  [{i}] {path}  ({human(path.stat().st_size)}, modified {mtime})")
    while True:
        answer = input(f"Keep which? [1-{len(group)}, Enter=1, s=skip group] ").strip().lower()
        if answer in ("", "1"):
            return 0
        if answer == "s":
            return -1
        if answer.isdigit() and 1 <= int(answer) <= len(group):
            return int(answer) - 1
        print("  Invalid choice.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find duplicate files and move copies to quarantine (never deletes)."
    )
    parser.add_argument("folder", help="root folder to scan recursively")
    parser.add_argument("--auto-keep-newest", action="store_true",
                        help="keep the newest copy of each group without prompting")
    parser.add_argument("--min-size", type=int, default=1,
                        help="ignore files smaller than N bytes (default: 1)")
    args = parser.parse_args()

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    groups = collect_groups(root, args.min_size)
    if not groups:
        print("No duplicates found.")
        return

    wasted = sum(g[0].stat().st_size * (len(g) - 1) for g in groups)
    print(f"Found {len(groups)} duplicate group(s), {human(wasted)} reclaimable.\n")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    quarantine = root / f"_duplicates_{stamp}"
    manifest: list[dict] = []
    moved = 0

    for group in groups:
        keeper_idx = choose_keeper(group, args.auto_keep_newest)
        if keeper_idx == -1:
            continue
        keeper = group[keeper_idx]
        for i, path in enumerate(group):
            if i == keeper_idx:
                continue
            quarantine.mkdir(parents=True, exist_ok=True)
            target = quarantine / path.name
            n = 1
            while target.exists():
                target = quarantine / f"{path.stem} ({n}){path.suffix}"
                n += 1
            path.rename(target)
            moved += 1
            manifest.append({"kept": str(keeper), "original": str(path), "quarantine": str(target)})
            print(f"  quarantined: {path.name}  (kept {keeper})")

    if manifest:
        manifest_path = quarantine / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"\nMoved {moved} duplicate(s) to {quarantine}")
        print(f"Manifest: {manifest_path}")
        print("Review the folder, then delete it yourself when you are sure.")
    else:
        print("\nNothing quarantined.")


if __name__ == "__main__":
    main()

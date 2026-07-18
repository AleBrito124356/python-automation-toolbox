#!/usr/bin/env python3
"""Report the largest folders and files under a path. Read-only.

Walks the tree once, aggregates per-directory sizes bottom-up, and prints
two ranked tables: biggest directories and biggest individual files, each
with a share-of-total bar. Permission errors are counted and skipped, never
fatal.

Usage:
    python 09_disk_report.py C:/Users/me
    python 09_disk_report.py D:/ --top 25
    python 09_disk_report.py . --top 10 --min-mb 50

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} TB"


def bar(fraction: float, width: int = 20) -> str:
    filled = round(fraction * width)
    return "#" * filled + "." * (width - filled)


def scan(root: Path) -> tuple[dict[str, int], list[tuple[int, str]], int]:
    """Return (dir_sizes, file_sizes, error_count)."""
    dir_sizes: dict[str, int] = {}
    files: list[tuple[int, str]] = []
    errors = 0

    def on_error(_exc: OSError) -> None:
        nonlocal errors
        errors += 1

    for dirpath, _dirnames, filenames in os.walk(root, topdown=False, onerror=on_error):
        local = 0
        for name in filenames:
            fpath = os.path.join(dirpath, name)
            try:
                size = os.lstat(fpath).st_size
            except OSError:
                errors += 1
                continue
            local += size
            files.append((size, fpath))
        # bottom-up: children are already in dir_sizes when we reach the parent
        subtotal = local + sum(
            dir_sizes.get(os.path.join(dirpath, d), 0) for d in _dirnames
        )
        dir_sizes[dirpath] = subtotal

    return dir_sizes, files, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Largest directories and files report.")
    parser.add_argument("path", nargs="?", default=".", help="root path (default: current dir)")
    parser.add_argument("--top", type=int, default=15, help="rows per table (default: 15)")
    parser.add_argument("--min-mb", type=float, default=0,
                        help="hide entries smaller than N megabytes")
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    print(f"Scanning {root} ...")
    dir_sizes, files, errors = scan(root)
    total = dir_sizes.get(str(root), 0)
    if total == 0:
        print("Empty tree (or everything was unreadable).")
        return

    min_bytes = int(args.min_mb * 1024 * 1024)

    print(f"\nTotal: {human(total)} in {len(files)} files"
          + (f"  ({errors} unreadable entries skipped)" if errors else ""))

    print(f"\n== Top {args.top} directories ==")
    ranked_dirs = sorted(
        ((s, p) for p, s in dir_sizes.items() if p != str(root) and s >= min_bytes),
        reverse=True,
    )
    for size, path in ranked_dirs[: args.top]:
        rel = os.path.relpath(path, root)
        print(f"  {human(size):>12}  [{bar(size / total)}]  {rel}")

    print(f"\n== Top {args.top} files ==")
    ranked_files = sorted((f for f in files if f[0] >= min_bytes), reverse=True)
    for size, path in ranked_files[: args.top]:
        rel = os.path.relpath(path, root)
        print(f"  {human(size):>12}  [{bar(size / total)}]  {rel}")


if __name__ == "__main__":
    main()

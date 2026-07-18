#!/usr/bin/env python3
"""Bulk-rename files with regex find/replace, sequence numbers, and undo.

Shows a preview table (old name -> new name) and refuses to run into name
collisions. The replacement string supports regex backreferences (\\1, \\g<1>)
plus a {n} token that becomes a zero-padded sequence number. Dry-run by
default; applied runs write a JSON undo log.

Usage:
    python 03_bulk_rename.py ./photos --find "IMG-(\\d+)" --replace "vacation-\\1"
    python 03_bulk_rename.py ./photos --find "^.*$" --replace "trip_{n}" --start 1 --pad 3 --apply
    python 03_bulk_rename.py --undo ./photos/rename_undo_20260718-110000.json

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


def build_new_name(name: str, pattern: re.Pattern, replace: str, seq: int, pad: int) -> str | None:
    """Return the new file name, or None if the pattern does not match."""
    stem, ext = Path(name).stem, Path(name).suffix
    if not pattern.search(stem):
        return None
    new_stem = pattern.sub(replace, stem)
    new_stem = new_stem.replace("{n}", str(seq).zfill(pad))
    new_name = new_stem + ext
    return None if new_name == name else new_name


def preview_table(rows: list[tuple[str, str]]) -> None:
    width = max(len(old) for old, _ in rows)
    print(f"\n  {'OLD'.ljust(width)}   NEW")
    print(f"  {'-' * width}   {'-' * width}")
    for old, new in rows:
        print(f"  {old.ljust(width)}   {new}")


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
        if dest.exists():
            print(f"  skip (target exists): {dest}")
            continue
        src.rename(dest)
        restored += 1
    print(f"Restored {restored}/{len(entries)} names.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regex bulk rename with preview, sequence numbering and undo."
    )
    parser.add_argument("folder", nargs="?", help="folder containing the files")
    parser.add_argument("--find", help="regex applied to the file stem (extension untouched)")
    parser.add_argument("--replace", default="",
                        help="replacement; supports \\1 backrefs and {n} sequence token")
    parser.add_argument("--start", type=int, default=1, help="first sequence number (default: 1)")
    parser.add_argument("--pad", type=int, default=3, help="zero-padding width for {n} (default: 3)")
    parser.add_argument("--ext", help="only touch files with this extension, e.g. .jpg")
    parser.add_argument("--apply", action="store_true", help="actually rename (default is preview)")
    parser.add_argument("--undo", metavar="LOG", help="reverse a previous run from its JSON log")
    args = parser.parse_args()

    if args.undo:
        undo(Path(args.undo).expanduser())
        return
    if not args.folder or not args.find:
        parser.error("provide FOLDER and --find REGEX, or --undo LOG")

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"Not a folder: {folder}")

    try:
        pattern = re.compile(args.find)
    except re.error as exc:
        sys.exit(f"Bad regex: {exc}")

    plan: list[tuple[Path, Path]] = []
    seq = args.start
    for item in sorted(folder.iterdir()):
        if not item.is_file():
            continue
        if args.ext and item.suffix.lower() != args.ext.lower():
            continue
        new_name = build_new_name(item.name, pattern, args.replace, seq, args.pad)
        if new_name is None:
            continue
        plan.append((item, item.with_name(new_name)))
        seq += 1

    if not plan:
        print("No files match.")
        return

    # Collision safety: no two targets may collide, and no target may already exist.
    targets = [dest for _, dest in plan]
    sources = {src for src, _ in plan}
    duplicates = {t.name for t in targets if targets.count(t) > 1}
    existing = {t.name for t in targets if t.exists() and t not in sources}
    if duplicates or existing:
        for name in sorted(duplicates):
            print(f"COLLISION: two or more files would become '{name}'", file=sys.stderr)
        for name in sorted(existing):
            print(f"COLLISION: '{name}' already exists in the folder", file=sys.stderr)
        sys.exit("Aborted. Adjust --replace (add {n}?) and try again.")

    preview_table([(src.name, dest.name) for src, dest in plan])
    print(f"\n{len(plan)} file(s) to rename.")

    if not args.apply:
        print("Preview only. Re-run with --apply to rename.")
        return

    log: list[dict[str, str]] = []
    for src, dest in plan:
        src.rename(dest)
        log.append({"from": str(src), "to": str(dest)})
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = folder / f"rename_undo_{stamp}.json"
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"Done. Undo log: {log_path}")


if __name__ == "__main__":
    main()

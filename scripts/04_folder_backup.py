#!/usr/bin/env python3
"""Zip a folder into timestamped backups with rotation, a manifest and --verify.

Creates <name>_YYYYmmdd-HHMMSS.zip in the destination, embeds a JSON manifest
(file list, sizes, per-file mtimes) inside the archive, and keeps only the
last N backups of that same folder -- older ones are rotated out. Rotation
only ever touches archives whose name is exactly <name>_<timestamp>.zip, so
backups of other folders that share a prefix (proj vs proj_old) are safe,
and the archive that was just written is never deleted. Two runs in the
same second get distinct names instead of overwriting each other.

--verify re-reads an archive, checks every member's CRC and compares the
contents against the embedded manifest (missing, extra or resized files).
It exits non-zero on any mismatch, so it can gate a scheduled job.

Exclude patterns use fnmatch globs and match both path segments and full
relative paths, so "node_modules" or "*.log" both work.

Usage:
    python 04_folder_backup.py C:/Projects/norden D:/Backups
    python 04_folder_backup.py C:/Projects/norden D:/Backups --keep 8 --exclude node_modules .venv "*.log"
    python 04_folder_backup.py --verify D:/Backups/norden_20260718-200000.zip

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import zlib
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

MANIFEST_NAME = "_backup_manifest.json"


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


def backup_pattern(src_name: str) -> re.Pattern:
    """Exact name pattern for this folder's backups: <name>_<stamp>[_<n>].zip."""
    return re.compile(rf"^{re.escape(src_name)}_(\d{{8}}-\d{{6}})(?:_(\d+))?\.zip$")


def list_backups(src_name: str, dest: Path) -> list[Path]:
    """This folder's backups only, oldest first (by embedded timestamp, then suffix)."""
    pattern = backup_pattern(src_name)
    found: list[tuple[str, int, Path]] = []
    for path in dest.iterdir():
        match = pattern.match(path.name)
        if match and path.is_file():
            found.append((match.group(1), int(match.group(2) or 0), path))
    return [path for _stamp, _n, path in sorted(found)]


def next_backup_path(src_name: str, dest: Path) -> Path:
    """A fresh name that also sorts after every existing backup from the same second."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    pattern = backup_pattern(src_name)
    used = [int(m.group(2) or 0) for p in dest.iterdir()
            if (m := pattern.match(p.name)) and m.group(1) == stamp]
    n = max(used) + 1 if used else 0
    while True:
        candidate = dest / (f"{src_name}_{stamp}.zip" if n == 0 else f"{src_name}_{stamp}_{n}.zip")
        if not candidate.exists() and not candidate.with_name(candidate.name + ".partial").exists():
            return candidate
        n += 1


def create_backup(src: Path, dest: Path, patterns: list[str]) -> Path:
    zip_path = next_backup_path(src.name, dest)
    partial = zip_path.with_name(zip_path.name + ".partial")
    manifest: dict = {
        "source": str(src),
        "created": datetime.now().isoformat(timespec="seconds"),
        "excludes": patterns,
        "files": [],
    }
    total = 0
    count = 0
    try:
        with zipfile.ZipFile(partial, "x", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(src.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(src)
                if is_excluded(rel, patterns):
                    continue
                if rel.as_posix() == MANIFEST_NAME:
                    print(f"  skipped {rel}: name is reserved for the backup manifest",
                          file=sys.stderr)
                    continue
                try:
                    stat = path.stat()
                    zf.write(path, arcname=f"{src.name}/{rel.as_posix()}")
                except OSError as exc:
                    print(f"  unreadable, skipped: {rel} ({exc})", file=sys.stderr)
                    continue
                written = zf.infolist()[-1]
                manifest["files"].append({
                    "path": rel.as_posix(),
                    "size": written.file_size,   # what is really in the zip
                    "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                })
                total += written.file_size
                count += 1
            manifest["file_count"] = count
            manifest["total_bytes"] = total
            zf.writestr(f"{src.name}/{MANIFEST_NAME}", json.dumps(manifest, indent=2))
        # Only a complete archive gets the real name, so rotation never counts
        # (or keeps) a half-written backup from an interrupted run.
        partial.rename(zip_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    print(f"Backed up {count} files ({human(total)}) -> {zip_path.name} "
          f"({human(zip_path.stat().st_size)} compressed)")
    return zip_path


def rotate(src_name: str, dest: Path, keep: int, protect: Path | None = None) -> list[Path]:
    """Delete this folder's oldest backups beyond `keep`. Never deletes `protect`."""
    backups = list_backups(src_name, dest)
    removed: list[Path] = []
    excess = len(backups) - keep
    for path in backups:
        if excess <= 0:
            break
        if protect is not None and path.resolve() == protect.resolve():
            continue
        path.unlink()
        removed.append(path)
        excess -= 1
        print(f"Rotated out old backup: {path.name}")
    return removed


def verify_zip(zip_path: Path) -> list[str]:
    """Return a list of problems (empty list means the backup is sound)."""
    problems: list[str] = []
    if not zip_path.is_file():
        return [f"not found: {zip_path}"]
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        return [f"not a readable zip archive ({exc})"]

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        # 1. Integrity: decompress every member; CRC and stream errors surface here.
        for info in infos:
            try:
                with zf.open(info) as fh:
                    while fh.read(1024 * 1024):
                        pass
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as exc:
                problems.append(f"corrupt member: {info.filename} ({exc})")

        # 2. Completeness: compare against the embedded manifest.
        manifests = [i for i in infos
                     if i.filename.count("/") == 1 and i.filename.endswith("/" + MANIFEST_NAME)]
        if not manifests:
            problems.append(f"no {MANIFEST_NAME} inside; not a backup made by this script")
            return problems
        top = manifests[0].filename.split("/", 1)[0]
        try:
            manifest = json.loads(zf.read(manifests[0]).decode("utf-8"))
        except (ValueError, zipfile.BadZipFile, zlib.error, OSError) as exc:
            problems.append(f"manifest unreadable ({exc})")
            return problems

        in_zip = {i.filename[len(top) + 1:]: i.file_size for i in infos
                  if i.filename.startswith(top + "/") and i is not manifests[0]}
        expected = {f["path"]: f["size"] for f in manifest.get("files", [])}
        for rel in sorted(expected.keys() - in_zip.keys()):
            problems.append(f"missing from archive: {rel}")
        for rel in sorted(in_zip.keys() - expected.keys()):
            problems.append(f"not in manifest: {rel}")
        for rel in sorted(expected.keys() & in_zip.keys()):
            if expected[rel] != in_zip[rel]:
                problems.append(f"size mismatch: {rel} (manifest {expected[rel]} B, "
                                f"archive {in_zip[rel]} B)")
        declared = manifest.get("file_count")
        if declared is not None and declared != len(expected):
            problems.append(f"manifest says {declared} files but lists {len(expected)}")
    return problems


def run_verify(paths: list[str]) -> int:
    failed = 0
    for name in paths:
        zip_path = Path(name).expanduser()
        problems = verify_zip(zip_path)
        if problems:
            failed += 1
            print(f"FAILED  {zip_path}")
            for problem in problems:
                print(f"  - {problem}")
        else:
            with zipfile.ZipFile(zip_path) as zf:
                members = sum(1 for i in zf.infolist() if not i.is_dir()) - 1
            print(f"OK      {zip_path}  ({members} files, CRC and manifest match)")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zip backup of a folder with keep-last-N rotation, manifest and verify."
    )
    parser.add_argument("source", nargs="?", help="folder to back up")
    parser.add_argument("dest", nargs="?", help="destination folder for the .zip files")
    parser.add_argument("--keep", type=int, default=5,
                        help="how many backups of this folder to keep (default: 5)")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="fnmatch patterns to skip, e.g. node_modules .venv *.log")
    parser.add_argument("--verify", nargs="+", metavar="ZIP",
                        help="check existing backup(s): CRC of every file + manifest; "
                             "exit 1 on any problem")
    parser.add_argument("--verify-after", action="store_true",
                        help="verify the new archive before rotating old ones out")
    args = parser.parse_args()

    if args.verify:
        sys.exit(run_verify(args.verify))
    if not args.source or not args.dest:
        parser.error("provide SOURCE and DEST, or --verify ZIP")

    src = Path(args.source).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()
    if not src.is_dir():
        sys.exit(f"Source is not a folder: {src}")
    if src == dest or dest.is_relative_to(src):
        sys.exit("Destination must be outside the source folder.")
    dest.mkdir(parents=True, exist_ok=True)

    created = create_backup(src, dest, args.exclude)
    if args.verify_after and run_verify([str(created)]):
        sys.exit("New backup failed verification; old backups were NOT rotated.")
    rotate(src.name, dest, max(args.keep, 1), protect=created)

    remaining = list_backups(src.name, dest)
    print(f"{len(remaining)} backup(s) on disk for '{src.name}'.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Find duplicate files by content and quarantine the copies. Never deletes.

Two-stage detection keeps large trees fast: files are bucketed by size first,
and only same-size files get hashed (BLAKE2b, chunked reads). Duplicates are
MOVED into a timestamped quarantine folder that mirrors their original
location, next to a JSON manifest, so you can review before deleting
anything by hand -- or put everything back with --undo MANIFEST.

Which copy is kept is decided by a policy (--keep newest|oldest|shortest-path)
or, when you run it in a terminal without --keep, by asking you per group.
--dry-run only reports: groups, the copy each policy would keep and the
space you would reclaim. Nothing moves. --json prints the same report as
JSON for scripts. Hard links to the same file are not duplicates (moving
one frees nothing), symlinks are ignored, and version-control folders
(.git, .hg, .svn) are never scanned, so a repository is never corrupted.

Usage:
    python 02_duplicate_finder.py C:/Users/me/Pictures --dry-run
    python 02_duplicate_finder.py C:/Users/me/Pictures --keep newest
    python 02_duplicate_finder.py --undo C:/Users/me/Pictures/_duplicates_20260718-101500/manifest.json

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

CHUNK = 1024 * 1024  # 1 MiB read chunks
QUARANTINE_PREFIX = "_duplicates_"
ALWAYS_EXCLUDED = (".git", ".hg", ".svn")
POLICIES = ("newest", "oldest", "shortest-path")


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


def log(message: str, quiet: bool = False) -> None:
    """Progress/info lines. With --json they go to stderr so stdout stays pure JSON."""
    print(message, file=sys.stderr if quiet else sys.stdout)


def is_excluded(rel: Path, patterns: list[str]) -> bool:
    return any(fnmatch(part, pat) for part in rel.parts for pat in patterns)


def collect_groups(root: Path, min_size: int, excludes: list[str] | None = None,
                   quiet: bool = False) -> list[list[Path]]:
    """Return groups of files with identical content (2+ distinct files each)."""
    patterns = list(ALWAYS_EXCLUDED) + list(excludes or [])
    by_size: dict[int, list[Path]] = defaultdict(list)
    seen_inodes: set[tuple[int, int]] = set()
    scanned = hardlinks = 0
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0].startswith(QUARANTINE_PREFIX):
            continue  # never re-scan a previous quarantine folder
        if is_excluded(rel, patterns):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
            info = path.stat()
        except OSError:
            continue
        if info.st_size < min_size:
            continue
        if info.st_ino:
            inode = (info.st_dev, info.st_ino)
            if inode in seen_inodes:
                hardlinks += 1
                continue  # another name for a file we already have
            seen_inodes.add(inode)
        by_size[info.st_size].append(path)
        scanned += 1

    candidates = sum(len(v) for v in by_size.values() if len(v) > 1)
    log(f"Scanned {scanned} files, hashing {candidates} size-collision candidates..."
        + (f" ({hardlinks} hard link(s) ignored)" if hardlinks else ""), quiet)

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


def pick_by_policy(group: list[Path], policy: str) -> int:
    """Index of the copy to keep under `policy`."""
    if policy == "newest":
        key = lambda i: (-group[i].stat().st_mtime, len(str(group[i])), str(group[i]))  # noqa: E731
    elif policy == "oldest":
        key = lambda i: (group[i].stat().st_mtime, len(str(group[i])), str(group[i]))  # noqa: E731
    elif policy == "shortest-path":
        key = lambda i: (len(str(group[i])), str(group[i]))  # noqa: E731
    else:
        raise ValueError(f"unknown policy {policy!r}")
    return min(range(len(group)), key=key)


def stdin_is_interactive() -> bool:
    """True only for a real console. On Windows, NUL claims to be a TTY, so ask the console."""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            import msvcrt
            handle = msvcrt.get_osfhandle(sys.stdin.fileno())
            mode = ctypes.c_uint32()
            return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
        except (OSError, ValueError, AttributeError):
            return False
    return True


NO_TTY_MESSAGE = ("No terminal to ask which copy to keep. Choose a policy with "
                  "--keep newest|oldest|shortest-path, or just report with --dry-run.")


def ask_keeper(group: list[Path], default: int) -> int:
    """Interactive choice. Returns the index to keep, or -1 to skip the group."""
    print()
    for i, path in enumerate(group, start=1):
        mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        marker = "*" if i - 1 == default else " "
        print(f" {marker}[{i}] {path}  ({human(path.stat().st_size)}, modified {mtime})")
    while True:
        try:
            answer = input(f"Keep which? [1-{len(group)}, Enter={default + 1}, s=skip group] ")
        except EOFError:
            print()
            sys.exit(NO_TTY_MESSAGE)
        answer = answer.strip().lower()
        if answer == "":
            return default
        if answer == "s":
            return -1
        if answer.isdigit() and 1 <= int(answer) <= len(group):
            return int(answer) - 1
        print("  Invalid choice.")


def choose_keeper(group: list[Path], auto: bool) -> int:
    """Backwards-compatible helper: auto keeps the newest copy, else ask."""
    newest = pick_by_policy(group, "newest")
    return newest if auto else ask_keeper(group, newest)


def quarantine_target(quarantine: Path, root: Path, path: Path) -> Path:
    """Mirror the original relative location inside the quarantine folder."""
    target = quarantine / path.relative_to(root)
    n = 1
    while target.exists():
        target = target.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return target


def unique_quarantine(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = root / f"{QUARANTINE_PREFIX}{stamp}"
    n = 1
    while folder.exists():
        folder = root / f"{QUARANTINE_PREFIX}{stamp}_{n}"
        n += 1
    return folder


def undo(manifest_path: Path) -> int:
    if not manifest_path.is_file():
        sys.exit(f"Manifest not found: {manifest_path}")
    try:
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
        moves = [(Path(e["quarantine"]), Path(e["original"])) for e in entries]
    except (ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Not a duplicate-finder manifest: {manifest_path} ({exc})")

    restored = 0
    remaining: list[dict] = []
    for entry, (quarantined, original) in zip(entries, moves):
        if not quarantined.exists():
            print(f"  skip (not in quarantine any more): {quarantined}")
            continue
        if original.exists():
            print(f"  conflict, left in quarantine: {original} already exists")
            remaining.append(entry)
            continue
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            quarantined.rename(original)
        except OSError as exc:
            print(f"  failed {quarantined.name}: {exc.strerror or exc}", file=sys.stderr)
            remaining.append(entry)
            continue
        restored += 1

    quarantine = manifest_path.parent
    if remaining:
        manifest_path.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
        print(f"Restored {restored}/{len(entries)}. {len(remaining)} file(s) stay in "
              f"{quarantine} (manifest updated).")
    else:
        manifest_path.unlink()
        for folder in sorted((p for p in quarantine.rglob("*") if p.is_dir()),
                             key=lambda p: len(p.parts), reverse=True):
            try:
                folder.rmdir()
            except OSError:
                pass
        try:
            quarantine.rmdir()
            print(f"Restored {restored}/{len(entries)} file(s); removed {quarantine.name}.")
        except OSError:
            print(f"Restored {restored}/{len(entries)} file(s); {quarantine} still has "
                  "other files in it, so it was kept.")
    return restored


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find duplicate files and move copies to quarantine (never deletes)."
    )
    parser.add_argument("folder", nargs="?", help="root folder to scan recursively")
    parser.add_argument("--keep", choices=POLICIES,
                        help="keep this copy of every group without prompting")
    parser.add_argument("--auto-keep-newest", action="store_true",
                        help="same as --keep newest (kept for older scripts)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report groups and reclaimable space; move nothing")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON on stdout (progress goes to stderr)")
    parser.add_argument("--min-size", type=int, default=1,
                        help="ignore files smaller than N bytes (default: 1)")
    parser.add_argument("--exclude", nargs="*", default=[], metavar="PATTERN",
                        help="skip folders/files matching these globs, e.g. node_modules '*.tmp'")
    parser.add_argument("--undo", metavar="MANIFEST",
                        help="move everything listed in a quarantine manifest.json back")
    args = parser.parse_args()

    if args.undo:
        undo(Path(args.undo).expanduser())
        return
    if not args.folder:
        parser.error("provide a FOLDER to scan, or --undo MANIFEST")

    policy = args.keep or ("newest" if args.auto_keep_newest else None)
    interactive = policy is None and not args.dry_run
    if interactive and (args.json or not stdin_is_interactive()):
        sys.exit(NO_TTY_MESSAGE)

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    quiet = args.json
    groups = collect_groups(root, args.min_size, args.exclude, quiet=quiet)
    wasted = sum(g[0].stat().st_size * (len(g) - 1) for g in groups)
    report: dict = {
        "root": str(root), "policy": policy or "interactive", "dry_run": args.dry_run,
        "reclaimable_bytes": wasted, "groups": [], "moved": 0,
        "quarantine": None, "manifest": None,
    }
    if not groups:
        log("No duplicates found.", quiet)
        if args.json:
            print(json.dumps(report, indent=2))
        return
    log(f"Found {len(groups)} duplicate group(s), {human(wasted)} reclaimable.", quiet)

    quarantine = unique_quarantine(root)
    manifest: list[dict] = []
    try:
        for number, group in enumerate(groups, start=1):
            default = pick_by_policy(group, policy or "newest")
            keeper_idx = ask_keeper(group, default) if interactive else default
            size = group[0].stat().st_size
            report["groups"].append({
                "size": size,
                "keep": str(group[keeper_idx]) if keeper_idx >= 0 else None,
                "duplicates": [str(p) for i, p in enumerate(group) if i != keeper_idx],
            })
            if args.dry_run:
                log(f"\nGroup {number}: {len(group)} copies x {human(size)}", quiet)
                for i, path in enumerate(group):
                    mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                    log(f"  {'keep' if i == keeper_idx else 'move'}  {path}  (modified {mtime})", quiet)
                continue
            if keeper_idx == -1:
                continue
            keeper = group[keeper_idx]
            for i, path in enumerate(group):
                if i == keeper_idx:
                    continue
                target = quarantine_target(quarantine, root, path)
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    path.rename(target)
                except OSError as exc:
                    print(f"  could not move {path}: {exc.strerror or exc}", file=sys.stderr)
                    continue
                manifest.append({"kept": str(keeper), "original": str(path),
                                 "quarantine": str(target), "size": size})
                log(f"  quarantined: {path.relative_to(root)}  (kept {keeper.relative_to(root)})", quiet)
    finally:
        # Written even when the run stops early (Ctrl+C, an error, closed stdin),
        # so every move that happened can be undone.
        if manifest:
            (quarantine / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                                      encoding="utf-8")

    report["moved"] = len(manifest)
    if args.dry_run:
        log(f"\nDry-run: nothing moved. {human(wasted)} could be reclaimed.", quiet)
    elif manifest:
        manifest_path = quarantine / "manifest.json"
        report["quarantine"] = str(quarantine)
        report["manifest"] = str(manifest_path)
        log(f"\nMoved {len(manifest)} duplicate(s) to {quarantine}", quiet)
        log(f"Manifest: {manifest_path}", quiet)
        log("Review the folder, then delete it yourself when you are sure.", quiet)
        log(f"Changed your mind? python {Path(__file__).name} --undo \"{manifest_path}\"", quiet)
    else:
        log("\nNothing quarantined.", quiet)
    if args.json:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Bulk-rename files with regex find/replace, sequence numbers, and undo.

Shows a preview table (old name -> new name) and refuses to run into name
collisions. The replacement string supports regex backreferences (\\1, \\g<1>)
plus a {n} token that becomes a zero-padded sequence number. Dry-run by
default.

Applied runs are transactional: every file is first moved to a unique
temporary name and only then to its target, so chains and swaps (a -> b while
b -> c) work on every OS and nothing is ever overwritten. If any rename fails,
the files already touched are put back. The JSON undo log is written BEFORE
the first rename, so even a killed process leaves a log that --undo can use.

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
import secrets
import sys
from datetime import datetime
from pathlib import Path

LOG_PREFIX = "rename_undo_"
TMP_PREFIX = ".renametmp-"


class RenameError(Exception):
    """A rename step failed; `restored` tells whether the rollback succeeded."""

    def __init__(self, cause: OSError, restored: bool, leftovers: list[str]):
        super().__init__(str(cause))
        self.cause = cause
        self.restored = restored
        self.leftovers = leftovers


def build_new_name(name: str, pattern: re.Pattern, replace: str, seq: int, pad: int) -> str | None:
    """Return the new file name, or None if the pattern does not match.

    Raises ValueError for a replacement that cannot produce a valid file name.
    """
    stem, ext = Path(name).stem, Path(name).suffix
    if not pattern.search(stem):
        return None
    try:
        new_stem = pattern.sub(replace, stem)
    except (re.error, IndexError) as exc:
        raise ValueError(f"bad --replace template: {exc}") from None
    new_stem = new_stem.replace("{n}", str(seq).zfill(pad))
    if not new_stem.strip():
        raise ValueError(f"'{name}' would get an empty name")
    if "/" in new_stem or "\\" in new_stem:
        raise ValueError(f"'{name}' -> '{new_stem}': a file name cannot contain / or \\")
    new_name = new_stem + ext
    return None if new_name == name else new_name


def preview_table(rows: list[tuple[str, str]]) -> None:
    width = max(len(old) for old, _ in rows)
    print(f"\n  {'OLD'.ljust(width)}   NEW")
    print(f"  {'-' * width}   {'-' * width}")
    for old, new in rows:
        print(f"  {old.ljust(width)}   {new}")


def stage(pairs: list[tuple[Path, Path]]) -> list[dict[str, str]]:
    """Attach a unique temporary name to every (source, target) pair."""
    token = secrets.token_hex(4)
    return [{"from": str(src), "to": str(dest),
             "tmp": str(src.with_name(f"{TMP_PREFIX}{token}-{i}"))}
            for i, (src, dest) in enumerate(pairs)]


def two_phase_rename(moves: list[tuple[Path, Path, Path]]) -> None:
    """Rename (src, tmp, dest) triples: every src -> tmp, then every tmp -> dest.

    On any OSError the completed steps are reversed and RenameError is raised.
    """
    staged: list[tuple[Path, Path, Path]] = []
    finished: list[tuple[Path, Path, Path]] = []
    try:
        for src, tmp, dest in moves:
            src.rename(tmp)
            staged.append((src, tmp, dest))
        for src, tmp, dest in staged:
            if dest.exists():  # POSIX rename would overwrite silently
                raise FileExistsError(17, "target already exists", str(dest))
            tmp.rename(dest)
            finished.append((src, tmp, dest))
    except OSError as exc:
        leftovers: list[str] = []
        for src, tmp, dest in reversed(finished):
            try:
                dest.rename(tmp)
            except OSError:
                leftovers.append(str(dest))
        for src, tmp, dest in reversed(staged):
            try:
                if tmp.exists():
                    tmp.rename(src)
            except OSError:
                leftovers.append(str(tmp))
        raise RenameError(exc, not leftovers, leftovers) from exc


def write_log(path: Path, entries: list[dict[str, str]], status: str) -> None:
    payload = {"version": 2, "status": status, "entries": entries}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_log(log_file: Path) -> tuple[list[dict[str, str]], dict | None]:
    data = json.loads(log_file.read_text(encoding="utf-8"))
    if isinstance(data, list):  # v1 logs: a bare list of {"from", "to"}
        return data, None
    return data["entries"], data


def undo(log_file: Path) -> int:
    if not log_file.is_file():
        sys.exit(f"Undo log not found: {log_file}")
    try:
        entries, meta = read_log(log_file)
    except (ValueError, KeyError) as exc:
        sys.exit(f"Not a rename undo log: {log_file} ({exc})")

    moves: list[tuple[Path, Path]] = []
    for entry in entries:
        original = Path(entry["from"])
        current = Path(entry["to"])
        tmp = Path(entry["tmp"]) if entry.get("tmp") else None
        if not current.exists() and tmp is not None and tmp.exists():
            current = tmp  # the run was interrupted between the two phases
        if not current.exists():
            if not original.exists():
                print(f"  skip (missing): {current}")
            continue
        moves.append((current, original))

    currents = {cur for cur, _ in moves}
    safe: list[tuple[Path, Path]] = []
    for cur, original in moves:
        if original.exists() and original not in currents:
            print(f"  skip (target exists): {original}")
            continue
        safe.append((cur, original))

    if safe:
        try:
            two_phase_rename([(Path(e["from"]), Path(e["tmp"]), Path(e["to"]))
                              for e in stage(safe)])
        except RenameError as err:
            sys.exit(f"Undo failed ({err.cause}); "
                     + ("nothing was changed." if err.restored
                        else f"check these paths by hand: {', '.join(err.leftovers)}"))
    if meta is not None:
        write_log(log_file, entries, "undone")
    print(f"Restored {len(safe)}/{len(entries)} names.")
    return len(safe)


def unique_log_path(folder: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"{LOG_PREFIX}{stamp}.json"
    n = 1
    while path.exists():
        path = folder / f"{LOG_PREFIX}{stamp}_{n}.json"
        n += 1
    return path


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
        if item.name.startswith((LOG_PREFIX, TMP_PREFIX)):
            continue  # never rename our own undo logs or leftovers
        if args.ext and item.suffix.lower() != args.ext.lower():
            continue
        try:
            new_name = build_new_name(item.name, pattern, args.replace, seq, args.pad)
        except ValueError as exc:
            sys.exit(f"Aborted: {exc}")
        if new_name is None:
            continue
        plan.append((item, item.with_name(new_name)))
        seq += 1

    if not plan:
        print("No files match.")
        return

    # Collision safety: no two targets may collide, and no target may already
    # exist unless it is itself one of the files being renamed away.
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

    entries = stage(plan)
    log_path = unique_log_path(folder)
    write_log(log_path, entries, "in-progress")   # before touching anything
    try:
        two_phase_rename([(Path(e["from"]), Path(e["tmp"]), Path(e["to"])) for e in entries])
    except RenameError as err:
        if err.restored:
            log_path.unlink(missing_ok=True)
            sys.exit(f"Rename failed ({err.cause}). Every file was put back; nothing changed.")
        write_log(log_path, entries, "failed")
        sys.exit(f"Rename failed ({err.cause}) and some files could not be put back.\n"
                 f"Recover with: python {Path(__file__).name} --undo \"{log_path}\"")
    write_log(log_path, entries, "done")
    print(f"Done. Undo log: {log_path}")
    print(f"To reverse: python {Path(__file__).name} --undo \"{log_path}\"")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Log everything you copy into a JSONL history file. Ctrl+C for a summary.

Polls the clipboard, appends each new entry as one JSON line with a
timestamp, and dedupes: consecutive repeats are always skipped, and with
the default settings anything you already copied this session is skipped
too. Handy when filling forms, doing research, or collecting snippets --
your clipboard finally has a memory.

Privacy note: the history file is plain text on YOUR disk and nothing
leaves the machine. Do not run it while copying passwords.

Usage:
    python 16_clipboard_watcher.py
    python 16_clipboard_watcher.py --out research_snippets.jsonl --interval 0.5
    python 16_clipboard_watcher.py --allow-repeats --max-chars 2000

Dependencies: pyperclip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import pyperclip
except ImportError:
    sys.exit("pyperclip is required: pip install pyperclip")


def digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Clipboard history logger (JSONL).")
    parser.add_argument("--out", default="clipboard_history.jsonl",
                        help="history file (default: clipboard_history.jsonl)")
    parser.add_argument("--interval", type=float, default=0.8,
                        help="poll interval in seconds (default: 0.8)")
    parser.add_argument("--max-chars", type=int, default=10000,
                        help="truncate stored entries to N chars (default: 10000)")
    parser.add_argument("--allow-repeats", action="store_true",
                        help="log an entry again even if it was copied earlier this session")
    args = parser.parse_args()

    out = Path(args.out).expanduser()
    seen: set[str] = set()
    last = ""
    captured = 0
    skipped = 0
    started = time.time()

    try:
        last = pyperclip.paste() or ""  # ignore whatever was copied before start
    except pyperclip.PyperclipException as exc:
        sys.exit(f"Clipboard unavailable: {exc}")
    if last:
        seen.add(digest(last))

    print(f"Watching clipboard every {args.interval}s -> {out}")
    print("Copy things. Ctrl+C to stop and see the summary.\n")

    try:
        with out.open("a", encoding="utf-8") as fh:
            while True:
                time.sleep(args.interval)
                try:
                    current = pyperclip.paste() or ""
                except pyperclip.PyperclipException:
                    continue  # clipboard busy (common on Windows), retry next tick
                if not current.strip() or current == last:
                    continue
                last = current
                key = digest(current)
                if not args.allow_repeats and key in seen:
                    skipped += 1
                    continue
                seen.add(key)
                entry = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "chars": len(current),
                    "text": current[: args.max_chars],
                    "truncated": len(current) > args.max_chars,
                }
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()
                captured += 1
                preview = current.strip().replace("\n", " ")[:60]
                print(f"  [{captured}] {len(current)} chars: {preview}")
    except KeyboardInterrupt:
        pass

    minutes = (time.time() - started) / 60
    print("\n== Session summary ==")
    print(f"  Duration:  {minutes:.1f} min")
    print(f"  Captured:  {captured} entries")
    print(f"  Skipped:   {skipped} duplicates")
    print(f"  History:   {out.resolve()}")


if __name__ == "__main__":
    main()

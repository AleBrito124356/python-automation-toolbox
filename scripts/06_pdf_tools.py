#!/usr/bin/env python3
"""Merge, split, rotate PDFs and extract page ranges from the command line.

Four subcommands built on pypdf. Page specs are 1-based and accept ranges
and lists: "1-3,7,10-12". Nothing is modified in place -- every command
writes a new file or a new folder of files.

Usage:
    python 06_pdf_tools.py merge report_a.pdf report_b.pdf -o combined.pdf
    python 06_pdf_tools.py split big.pdf -o pages/
    python 06_pdf_tools.py rotate scan.pdf --angle 90 --pages 2-4 -o fixed.pdf
    python 06_pdf_tools.py extract manual.pdf --pages 10-25 -o chapter2.pdf

Dependencies: pypdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    sys.exit("pypdf is required: pip install pypdf")


def parse_pages(spec: str, total: int) -> list[int]:
    """'1-3,7' -> [0, 1, 2, 6] (0-based, validated against total)."""
    indices: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            start_s, _, end_s = chunk.partition("-")
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(chunk)
        if start < 1 or end > total or start > end:
            sys.exit(f"Page range '{chunk}' is out of bounds (document has {total} pages).")
        indices.extend(range(start - 1, end))
    return indices


def cmd_merge(args) -> None:
    writer = PdfWriter()
    for name in args.inputs:
        path = Path(name)
        if not path.is_file():
            sys.exit(f"Not found: {path}")
        reader = PdfReader(path)
        writer.append(reader)
        print(f"  + {path.name} ({len(reader.pages)} pages)")
    out = Path(args.output)
    with out.open("wb") as fh:
        writer.write(fh)
    print(f"Merged {len(args.inputs)} files -> {out}")


def cmd_split(args) -> None:
    src = Path(args.input)
    reader = PdfReader(src)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    pad = len(str(len(reader.pages)))
    for i, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        out = out_dir / f"{src.stem}_p{str(i).zfill(pad)}.pdf"
        with out.open("wb") as fh:
            writer.write(fh)
    print(f"Split {src.name} into {len(reader.pages)} single-page PDFs -> {out_dir}")


def cmd_rotate(args) -> None:
    src = Path(args.input)
    reader = PdfReader(src)
    total = len(reader.pages)
    targets = set(parse_pages(args.pages, total)) if args.pages else set(range(total))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i in targets:
            page.rotate(args.angle)
        writer.add_page(page)
    out = Path(args.output)
    with out.open("wb") as fh:
        writer.write(fh)
    print(f"Rotated {len(targets)} page(s) by {args.angle} deg -> {out}")


def cmd_extract(args) -> None:
    src = Path(args.input)
    reader = PdfReader(src)
    indices = parse_pages(args.pages, len(reader.pages))
    writer = PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
    out = Path(args.output)
    with out.open("wb") as fh:
        writer.write(fh)
    print(f"Extracted {len(indices)} page(s) from {src.name} -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF merge/split/rotate/extract toolbox.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_merge = sub.add_parser("merge", help="concatenate PDFs in the given order")
    p_merge.add_argument("inputs", nargs="+", help="input PDFs, in order")
    p_merge.add_argument("-o", "--output", required=True, help="output PDF")
    p_merge.set_defaults(func=cmd_merge)

    p_split = sub.add_parser("split", help="one PDF per page")
    p_split.add_argument("input", help="input PDF")
    p_split.add_argument("-o", "--output", required=True, help="output folder")
    p_split.set_defaults(func=cmd_split)

    p_rotate = sub.add_parser("rotate", help="rotate all pages or a page range")
    p_rotate.add_argument("input", help="input PDF")
    p_rotate.add_argument("--angle", type=int, choices=[90, 180, 270], required=True,
                          help="clockwise rotation")
    p_rotate.add_argument("--pages", help="1-based spec like 1-3,7 (default: all pages)")
    p_rotate.add_argument("-o", "--output", required=True, help="output PDF")
    p_rotate.set_defaults(func=cmd_rotate)

    p_extract = sub.add_parser("extract", help="pull a page range into a new PDF")
    p_extract.add_argument("input", help="input PDF")
    p_extract.add_argument("--pages", required=True, help="1-based spec like 10-25 or 1,3,5")
    p_extract.add_argument("-o", "--output", required=True, help="output PDF")
    p_extract.set_defaults(func=cmd_extract)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

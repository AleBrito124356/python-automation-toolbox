#!/usr/bin/env python3
"""Merge, split, rotate PDFs and extract page ranges from the command line.

Four subcommands built on pypdf. Page specs are 1-based and accept ranges,
lists and open-ended ranges: "1-3,7,10-12", "5-" (5 to the end), "-3"
(first three). A malformed spec gets a one-line explanation, not a
traceback. Nothing is modified in place -- every command writes a new file
or a new folder of files, and an output path equal to an input is refused.
Password-protected PDFs can be opened with --password.

Usage:
    python 06_pdf_tools.py merge report_a.pdf report_b.pdf -o combined.pdf
    python 06_pdf_tools.py split big.pdf -o pages/
    python 06_pdf_tools.py rotate scan.pdf --angle 90 --pages 2-4 -o fixed.pdf
    python 06_pdf_tools.py extract manual.pdf --pages 10-25 -o chapter2.pdf
    python 06_pdf_tools.py extract manual.pdf --pages 26- -o rest.pdf

Dependencies: pypdf
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import DependencyError, PdfReadError
except ImportError:
    sys.exit("pypdf is required: pip install pypdf")


class ToolError(Exception):
    """A user-facing problem: printed as one line, exit code 1."""


def parse_pages(spec: str, total: int) -> list[int]:
    """'1-3,7' -> [0, 1, 2, 6] (0-based, validated against total).

    Also accepts open ranges: '5-' is page 5 to the end, '-3' is pages 1-3.
    Raises ToolError with a readable message for anything else.
    """
    indices: list[int] = []
    for raw in spec.split(","):
        chunk = raw.strip()
        if not chunk:
            raise ToolError(f"Page spec '{spec}' has an empty item (check the commas).")
        match = re.fullmatch(r"(\d*)\s*-\s*(\d*)", chunk)
        if match and any(match.groups()):
            start_s, end_s = match.groups()
            start = int(start_s) if start_s else 1
            end = int(end_s) if end_s else total
        elif chunk.isdigit():
            start = end = int(chunk)
        else:
            raise ToolError(f"'{chunk}' is not a page or range. Examples: 3  1-4  7-  -2  1-3,9")
        if start < 1 or end > total or start > end:
            raise ToolError(f"Page range '{chunk}' is out of bounds (document has {total} pages).")
        indices.extend(range(start - 1, end))
    return indices


def open_pdf(name: str, password: str | None) -> PdfReader:
    path = Path(name)
    if not path.is_file():
        raise ToolError(f"Not found: {path}")
    try:
        reader = PdfReader(path)
    except (PdfReadError, ValueError, OSError) as exc:
        raise ToolError(f"{path.name} is not a readable PDF ({exc}).") from None
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt(password or "")
        except DependencyError:
            raise ToolError(f"{path.name} uses AES encryption: pip install cryptography") from None
        if not unlocked:
            hint = "wrong --password" if password else "pass --password"
            raise ToolError(f"{path.name} is password-protected: {hint}.")
    return reader


def check_output(out: Path, inputs: list[str]) -> Path:
    """Refuse to overwrite an input while it is still being read."""
    target = out.resolve()
    for name in inputs:
        if Path(name).resolve() == target:
            raise ToolError(f"Output {out} is also an input; choose a different -o path.")
    return out


def cmd_merge(args) -> None:
    out = check_output(Path(args.output), args.inputs)
    writer = PdfWriter()
    for name in args.inputs:
        reader = open_pdf(name, args.password)
        writer.append(reader)
        print(f"  + {Path(name).name} ({len(reader.pages)} pages)")
    with out.open("wb") as fh:
        writer.write(fh)
    print(f"Merged {len(args.inputs)} files -> {out}")


def cmd_split(args) -> None:
    src = Path(args.input)
    reader = open_pdf(args.input, args.password)
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
    out = check_output(Path(args.output), [args.input])
    reader = open_pdf(args.input, args.password)
    total = len(reader.pages)
    targets = set(parse_pages(args.pages, total)) if args.pages else set(range(total))
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i in targets:
            page.rotate(args.angle)
        writer.add_page(page)
    with out.open("wb") as fh:
        writer.write(fh)
    print(f"Rotated {len(targets)} page(s) by {args.angle} deg -> {out}")


def cmd_extract(args) -> None:
    src = Path(args.input)
    out = check_output(Path(args.output), [args.input])
    reader = open_pdf(args.input, args.password)
    indices = parse_pages(args.pages, len(reader.pages))
    writer = PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
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

    for p in (p_merge, p_split, p_rotate, p_extract):
        p.add_argument("--password", help="password for encrypted input PDFs")

    args = parser.parse_args()
    try:
        args.func(args)
    except ToolError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()

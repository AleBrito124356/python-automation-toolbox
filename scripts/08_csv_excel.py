#!/usr/bin/env python3
"""Convert between CSV and Excel (.xlsx) with delimiter sniffing.

Direction is inferred from the file extensions. CSV to xlsx sniffs the
delimiter (comma, semicolon, tab, pipe) and converts numeric-looking cells
to real numbers so Excel treats them as such -- values with leading zeros
(phone numbers, ZIP codes) stay as text. Xlsx to CSV writes UTF-8 with BOM
so Excel reopens accented characters correctly.

Usage:
    python 08_csv_excel.py ventas.csv ventas.xlsx
    python 08_csv_excel.py report.xlsx report.csv --sheet "Q3 Data"
    python 08_csv_excel.py report.xlsx --list-sheets

Dependencies: openpyxl
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl is required: pip install openpyxl")


def coerce(value: str):
    """Turn numeric-looking strings into int/float; keep leading-zero strings."""
    s = value.strip()
    if s == "":
        return None
    if len(s) > 1 and s[0] == "0" and not s.startswith("0."):
        return value  # ZIP codes, phone numbers, IDs
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return value


def sniff_delimiter(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def csv_to_xlsx(src: Path, dest: Path) -> None:
    delimiter = sniff_delimiter(src)
    shown = {"\t": "TAB"}.get(delimiter, delimiter)
    print(f"Detected delimiter: '{shown}'")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = src.stem[:31]  # Excel's sheet-name limit
    rows = 0
    with src.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.reader(fh, delimiter=delimiter):
            ws.append([coerce(cell) for cell in row])
            rows += 1
    ws.freeze_panes = "A2"
    wb.save(dest)
    print(f"Wrote {rows} rows -> {dest}")


def xlsx_to_csv(src: Path, dest: Path, sheet: str | None, delimiter: str) -> None:
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    if sheet is None:
        ws = wb.active
    elif sheet in wb.sheetnames:
        ws = wb[sheet]
    elif sheet.isdigit() and int(sheet) < len(wb.sheetnames):
        ws = wb[wb.sheetnames[int(sheet)]]
    else:
        sys.exit(f"Sheet '{sheet}' not found. Available: {', '.join(wb.sheetnames)}")

    rows = 0
    with dest.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=delimiter)
        for row in ws.iter_rows(values_only=True):
            writer.writerow(["" if cell is None else cell for cell in row])
            rows += 1
    wb.close()
    print(f"Wrote {rows} rows from sheet '{ws.title}' -> {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(description="CSV <-> xlsx converter with sniffing.")
    parser.add_argument("input", help="source file (.csv or .xlsx)")
    parser.add_argument("output", nargs="?", help="destination file (.xlsx or .csv)")
    parser.add_argument("--sheet", help="xlsx->csv: sheet name or 0-based index (default: active)")
    parser.add_argument("--delimiter", default=",",
                        help="xlsx->csv: output delimiter (default: ',')")
    parser.add_argument("--list-sheets", action="store_true", help="print sheet names and exit")
    args = parser.parse_args()

    src = Path(args.input).expanduser()
    if not src.is_file():
        sys.exit(f"Not found: {src}")

    if args.list_sheets:
        if src.suffix.lower() != ".xlsx":
            sys.exit("--list-sheets only applies to .xlsx files")
        wb = openpyxl.load_workbook(src, read_only=True)
        for i, name in enumerate(wb.sheetnames):
            print(f"  [{i}] {name}")
        return

    if not args.output:
        sys.exit("Provide an output file (or use --list-sheets).")
    dest = Path(args.output).expanduser()

    pair = (src.suffix.lower(), dest.suffix.lower())
    if pair == (".csv", ".xlsx"):
        csv_to_xlsx(src, dest)
    elif pair == (".xlsx", ".csv"):
        xlsx_to_csv(src, dest, args.sheet, args.delimiter)
    else:
        sys.exit("Expected csv->xlsx or xlsx->csv (check the file extensions).")


if __name__ == "__main__":
    main()

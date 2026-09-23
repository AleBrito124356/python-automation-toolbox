#!/usr/bin/env python3
"""Generate QR codes for URLs, free text, or contact cards (vCard 3.0).

Three subcommands. URL mode normalizes bare domains to https. vCard mode
builds an RFC 2426 VCARD block (commas, semicolons and backslashes in names
and companies are escaped, so "Acme, Inc." stays one company) so phones
offer "Add contact" on scan. A name written "Family, Given" is split on the
comma; otherwise the last word is the family name.

Every mode saves the PNG first and can then print a terminal preview; if
the console cannot show block characters the preview falls back to '#'.

Usage:
    python 13_qr_generator.py url alejandrobrito.dev -o site.png
    python 13_qr_generator.py text "Ticket #4821 - Gate B" --ascii
    python 13_qr_generator.py vcard --name "Alejandro Brito" --phone "+507 6000-0000" --email "hi@example.com" --org "Freelance"

Dependencies: qrcode[pil]
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

try:
    import qrcode
    from qrcode.exceptions import DataOverflowError
except ImportError:
    sys.exit("qrcode is required: pip install qrcode[pil]")


URI_SCHEMES = ("mailto:", "tel:", "sms:", "smsto:", "geo:")


def normalize_url(link: str) -> str:
    """Add https:// to bare domains; leave real URLs and mailto:/tel:/... alone."""
    link = link.strip()
    if "://" in link or link.lower().startswith(URI_SCHEMES):
        return link
    return "https://" + link


def vcard_escape(value: str) -> str:
    """RFC 2426 text escaping: backslash, comma, semicolon and newlines."""
    return (value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def single_line(value: str) -> str:
    return " ".join(value.split())


def split_name(name: str) -> tuple[str, str]:
    """Return (family, given). 'Brito, Alejandro' or 'Alejandro Brito'."""
    name = name.strip()
    if "," in name:
        family, _, given = name.partition(",")
        return family.strip(), given.strip()
    parts = name.split()
    if len(parts) > 1:
        return parts[-1], " ".join(parts[:-1])
    return "", name


def build_vcard(name: str, phone: str | None, email: str | None,
                org: str | None, url: str | None) -> str:
    family, given = split_name(name)
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{vcard_escape(family)};{vcard_escape(given)};;;",
        f"FN:{vcard_escape(name.strip())}",
    ]
    if org:
        lines.append(f"ORG:{vcard_escape(org.strip())}")
    if phone:
        lines.append(f"TEL;TYPE=CELL:{single_line(phone)}")
    if email:
        lines.append(f"EMAIL;TYPE=INTERNET:{single_line(email)}")
    if url:
        lines.append(f"URL:{single_line(url)}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)


def terminal_preview(qr: "qrcode.QRCode", encoding: str | None) -> str:
    """Block-character preview, or a plain '#' version if `encoding` can't show blocks."""
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    text = buf.getvalue()
    try:
        text.encode(encoding or "ascii")
        return text
    except (UnicodeEncodeError, LookupError):
        return "".join("".join("  " if dark else "##" for dark in row) + "\n"
                       for row in qr.get_matrix())


def render(payload: str, out: Path, ascii_preview: bool) -> int:
    """Save the QR PNG, then optionally preview it. Returns the module count."""
    qr = qrcode.QRCode(border=2)
    qr.add_data(payload)
    try:
        qr.make(fit=True)
    except (DataOverflowError, ValueError):
        sys.exit(f"Too much data for one QR code ({len(payload.encode('utf-8'))} bytes).")
    qr.make_image(fill_color="black", back_color="white").save(out)
    if ascii_preview:
        print(terminal_preview(qr, sys.stdout.encoding), end="")
    modules = qr.modules_count
    print(f"Saved: {out.resolve()}  ({modules}x{modules} modules)")
    return modules


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="QR generator: url, text, or vcard.")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_url = sub.add_parser("url", help="QR that opens a link")
    p_url.add_argument("link", help="URL (https:// is added if missing)")
    p_url.add_argument("-o", "--output", default="qr_url.png")
    p_url.add_argument("--ascii", action="store_true", help="also print a terminal preview")

    p_text = sub.add_parser("text", help="QR containing arbitrary text")
    p_text.add_argument("content", help="text to encode")
    p_text.add_argument("-o", "--output", default="qr_text.png")
    p_text.add_argument("--ascii", action="store_true", help="also print a terminal preview")

    p_vcard = sub.add_parser("vcard", help="QR contact card")
    p_vcard.add_argument("--name", required=True, help="full name, or 'Family, Given'")
    p_vcard.add_argument("--phone", help="phone number with country code")
    p_vcard.add_argument("--email", help="email address")
    p_vcard.add_argument("--org", help="company or title")
    p_vcard.add_argument("--url", help="website")
    p_vcard.add_argument("-o", "--output", default="qr_vcard.png")
    p_vcard.add_argument("--ascii", action="store_true", help="also print a terminal preview")

    args = parser.parse_args()

    if args.mode == "url":
        payload = normalize_url(args.link)
    elif args.mode == "text":
        payload = args.content
    else:
        payload = build_vcard(args.name, args.phone, args.email, args.org, args.url)

    render(payload, Path(args.output), args.ascii)


if __name__ == "__main__":
    main()

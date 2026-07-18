#!/usr/bin/env python3
"""Generate QR codes for URLs, free text, or contact cards (vCard 3.0).

Three subcommands. URL mode normalizes bare domains to https. vCard mode
builds a spec-compliant VCARD block so phones offer "Add contact" on scan.
Every mode saves a PNG and can print an ASCII preview in the terminal.

Usage:
    python 13_qr_generator.py url alejandrobrito.dev -o site.png
    python 13_qr_generator.py text "Ticket #4821 - Gate B" --ascii
    python 13_qr_generator.py vcard --name "Alejandro Brito" --phone "+507 6000-0000" --email "hi@example.com" --org "Freelance"

Dependencies: qrcode[pil]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import qrcode
except ImportError:
    sys.exit("qrcode is required: pip install qrcode[pil]")


def build_vcard(name: str, phone: str | None, email: str | None,
                org: str | None, url: str | None) -> str:
    parts = name.strip().split()
    family = parts[-1] if len(parts) > 1 else ""
    given = " ".join(parts[:-1]) if len(parts) > 1 else name
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"N:{family};{given};;;",
        f"FN:{name}",
    ]
    if org:
        lines.append(f"ORG:{org}")
    if phone:
        lines.append(f"TEL;TYPE=CELL:{phone}")
    if email:
        lines.append(f"EMAIL:{email}")
    if url:
        lines.append(f"URL:{url}")
    lines.append("END:VCARD")
    return "\n".join(lines)


def render(payload: str, out: Path, ascii_preview: bool) -> None:
    qr = qrcode.QRCode(border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    if ascii_preview:
        qr.print_ascii(invert=True)
    qr.make_image(fill_color="black", back_color="white").save(out)
    modules = qr.version * 4 + 17
    print(f"Saved: {out.resolve()}  ({modules}x{modules} modules)")


def main() -> None:
    parser = argparse.ArgumentParser(description="QR generator: url, text, or vcard.")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_url = sub.add_parser("url", help="QR that opens a link")
    p_url.add_argument("link", help="URL (https:// is added if missing)")
    p_url.add_argument("-o", "--output", default="qr_url.png")
    p_url.add_argument("--ascii", action="store_true", help="also print ASCII preview")

    p_text = sub.add_parser("text", help="QR containing arbitrary text")
    p_text.add_argument("content", help="text to encode")
    p_text.add_argument("-o", "--output", default="qr_text.png")
    p_text.add_argument("--ascii", action="store_true", help="also print ASCII preview")

    p_vcard = sub.add_parser("vcard", help="QR contact card")
    p_vcard.add_argument("--name", required=True, help="full name")
    p_vcard.add_argument("--phone", help="phone number with country code")
    p_vcard.add_argument("--email", help="email address")
    p_vcard.add_argument("--org", help="company or title")
    p_vcard.add_argument("--url", help="website")
    p_vcard.add_argument("-o", "--output", default="qr_vcard.png")
    p_vcard.add_argument("--ascii", action="store_true", help="also print ASCII preview")

    args = parser.parse_args()

    if args.mode == "url":
        link = args.link
        if not link.startswith(("http://", "https://")):
            link = "https://" + link
        payload = link
    elif args.mode == "text":
        payload = args.content
    else:
        payload = build_vcard(args.name, args.phone, args.email, args.org, args.url)

    render(payload, Path(args.output), args.ascii)


if __name__ == "__main__":
    main()

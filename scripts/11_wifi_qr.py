#!/usr/bin/env python3
"""Generate a scan-to-join Wi-Fi QR code: PNG file + terminal preview.

Builds the standard WIFI: payload (the same format Android and iOS cameras
understand), escaping the special characters backslash, semicolon, comma,
colon and quotes. If you omit --password the script prompts for it without
echoing, so the password never lands in your shell history.

The PNG is written first; the terminal preview comes after and never
crashes the run: when the console cannot show block characters (redirected
output, Task Scheduler, old code pages) it falls back to a '#' preview.

Usage:
    python 11_wifi_qr.py --ssid "CasaBrito" --security WPA
    python 11_wifi_qr.py --ssid "CasaBrito" --password "hunter2-not-really" -o wifi.png
    python 11_wifi_qr.py --ssid "Guest Network" --security nopass --hidden --no-preview

Dependencies: qrcode[pil]
"""

from __future__ import annotations

import argparse
import getpass
import io
import re
import sys
from pathlib import Path

try:
    import qrcode
except ImportError:
    sys.exit("qrcode is required: pip install qrcode[pil]")


def escape(value: str) -> str:
    """Escape \\ ; , : \" as required by the WIFI: payload spec."""
    return re.sub(r'([\\;,:"])', r"\\\1", value)


def build_payload(ssid: str, password: str, security: str, hidden: bool) -> str:
    parts = [f"T:{security}", f"S:{escape(ssid)}"]
    if security != "nopass":
        parts.append(f"P:{escape(password)}")
    if hidden:
        parts.append("H:true")
    return "WIFI:" + ";".join(parts) + ";;"


def terminal_preview(qr: "qrcode.QRCode", encoding: str | None) -> str:
    """Block-character preview, or a plain '#' version if `encoding` can't show blocks."""
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    text = buf.getvalue()
    try:
        text.encode(encoding or "ascii")
        return text
    except (UnicodeEncodeError, LookupError):
        # Same orientation as invert=True: light modules are drawn, dark ones blank.
        return "".join("".join("  " if dark else "##" for dark in row) + "\n"
                       for row in qr.get_matrix())


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # odd SSIDs must not crash the run
    parser = argparse.ArgumentParser(description="Wi-Fi QR code generator (PNG + preview).")
    parser.add_argument("--ssid", required=True, help="network name")
    parser.add_argument("--password", help="network password (omit to be prompted securely)")
    parser.add_argument("--security", choices=["WPA", "WEP", "nopass"], default="WPA",
                        help="security type (default: WPA, which also covers WPA2/WPA3)")
    parser.add_argument("--hidden", action="store_true", help="network does not broadcast SSID")
    parser.add_argument("-o", "--output", default="wifi_qr.png", help="PNG path (default: wifi_qr.png)")
    parser.add_argument("--no-preview", action="store_true", help="skip the terminal preview")
    args = parser.parse_args()

    password = args.password or ""
    if args.security != "nopass" and not password:
        password = getpass.getpass(f"Password for '{args.ssid}': ")
        if not password:
            sys.exit("Empty password. Use --security nopass for open networks.")

    payload = build_payload(args.ssid, password, args.security, args.hidden)

    qr = qrcode.QRCode(border=2)
    qr.add_data(payload)
    qr.make(fit=True)

    out = Path(args.output)
    qr.make_image(fill_color="black", back_color="white").save(out)

    print(f"\nNetwork: {args.ssid}  ({args.security}"
          + (", hidden" if args.hidden else "") + ")\n")
    if not args.no_preview:
        print(terminal_preview(qr, sys.stdout.encoding), end="")
    print(f"\nSaved: {out.resolve()}")
    print("Print it, frame it, tape it to the fridge. Guests scan, they join.")


if __name__ == "__main__":
    main()

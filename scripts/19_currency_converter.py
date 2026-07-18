#!/usr/bin/env python3
"""Convert currencies with ECB reference rates. Free API, offline fallback.

Uses frankfurter.app (no key, backed by European Central Bank reference
rates). Every successful fetch is cached under ~/.cache/currency-converter,
so the converter still works on a plane -- it just tells you how old the
cached rates are.

Usage:
    python 19_currency_converter.py 100 USD EUR
    python 19_currency_converter.py 2500 PAB USD
    python 19_currency_converter.py --list

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

API_BASE = "https://api.frankfurter.app"
CACHE_DIR = Path.home() / ".cache" / "currency-converter"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "currency-converter/1.0"})
    with urllib.request.urlopen(request, timeout=15) as resp:
        return json.load(resp)


def cached_fetch(url: str, cache_name: str) -> tuple[dict, bool]:
    """Return (data, from_cache). Falls back to disk cache when offline."""
    cache_file = CACHE_DIR / cache_name
    try:
        data = fetch_json(url)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(
            {"fetched_at": datetime.now().isoformat(timespec="seconds"), "data": data}
        ), encoding="utf-8")
        return data, False
    except (urllib.error.URLError, TimeoutError):
        if cache_file.is_file():
            wrapper = json.loads(cache_file.read_text(encoding="utf-8"))
            fetched = wrapper.get("fetched_at", "unknown time")
            print(f"(offline - using rates cached {fetched})", file=sys.stderr)
            return wrapper["data"], True
        sys.exit("No network and no cached rates yet. Connect once to prime the cache.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Currency conversion via frankfurter.app with offline cache."
    )
    parser.add_argument("amount", nargs="?", type=float, help="amount to convert")
    parser.add_argument("src", nargs="?", help="source currency code, e.g. USD")
    parser.add_argument("dst", nargs="?", help="target currency code, e.g. EUR")
    parser.add_argument("--list", action="store_true", help="list supported currencies")
    args = parser.parse_args()

    if args.list:
        currencies, _ = cached_fetch(f"{API_BASE}/currencies", "currencies.json")
        for code, name in sorted(currencies.items()):
            print(f"  {code}  {name}")
        print(f"\n{len(currencies)} currencies (ECB reference set - PAB is pegged 1:1 to USD).")
        return

    if args.amount is None or not args.src or not args.dst:
        parser.error("usage: AMOUNT SRC DST (e.g. 100 USD EUR), or --list")

    src, dst = args.src.upper(), args.dst.upper()

    # Panama's balboa is pegged 1:1 to USD and not in the ECB set; alias it.
    pab_note = ""
    if "PAB" in (src, dst):
        pab_note = "  (PAB pegged 1:1 to USD)"
        src = "USD" if src == "PAB" else src
        dst = "USD" if dst == "PAB" else dst

    if src == dst:
        print(f"{args.amount:,.2f} {args.src.upper()} = {args.amount:,.2f} {args.dst.upper()}{pab_note}")
        return

    data, from_cache = cached_fetch(f"{API_BASE}/latest?from={src}", f"rates_{src}.json")
    rates = data.get("rates", {})
    if dst not in rates:
        sys.exit(f"Unknown target currency '{dst}'. See supported codes with --list.")

    rate = rates[dst]
    result = args.amount * rate
    stale = " [cached]" if from_cache else ""
    print(f"{args.amount:,.2f} {args.src.upper()} = {result:,.2f} {args.dst.upper()}{pab_note}")
    print(f"Rate: 1 {src} = {rate:.4f} {dst}  (ECB date {data.get('date', '?')}){stale}")


if __name__ == "__main__":
    main()

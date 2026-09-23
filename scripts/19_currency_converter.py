#!/usr/bin/env python3
"""Convert currencies with ECB reference rates. Free API, offline fallback.

Uses frankfurter.app (no key, backed by European Central Bank reference
rates). Every successful fetch is cached under ~/.cache/currency-converter,
so the converter still works on a plane -- it just tells you how old the
cached rates are. Offline, any cached table can answer any pair it covers:
rates cached for USD also convert EUR -> GBP (cross rate via USD).

An unknown currency code is reported as such (the service answers HTTP 4xx);
only real network trouble (no connection, timeout, HTTP 5xx, a captive
portal returning HTML) falls back to the cache.

Usage:
    python 19_currency_converter.py 100 USD EUR
    python 19_currency_converter.py 2500 PAB USD
    python 19_currency_converter.py --list

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

API_BASE = "https://api.frankfurter.app"
CACHE_DIR = Path.home() / ".cache" / "currency-converter"
CODE_RE = re.compile(r"[A-Z]{3}")


class Rejected(Exception):
    """The service answered, but refused the request (HTTP 4xx)."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "currency-converter/1.0"})
    with urllib.request.urlopen(request, timeout=15) as resp:
        return json.load(resp)


def cached_fetch(url: str, cache_name: str) -> tuple[dict | None, str | None]:
    """Return (data, None) on success, or (None, reason) when the network failed.

    Raises Rejected for HTTP 4xx: that is an answer, not an outage.
    """
    try:
        data = fetch_json(url)
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            raise Rejected(exc.code) from None
        return None, f"service error HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(getattr(exc, "reason", exc))
    except ValueError:
        return None, "the response was not JSON (captive portal or proxy?)"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / cache_name).write_text(json.dumps(
            {"fetched_at": datetime.now().isoformat(timespec="seconds"), "data": data}
        ), encoding="utf-8")
    except OSError as exc:
        print(f"(could not update the rate cache: {exc})", file=sys.stderr)
    return data, None


def read_cache(cache_name: str) -> tuple[dict, str] | None:
    path = CACHE_DIR / cache_name
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        return wrapper["data"], wrapper.get("fetched_at", "unknown time")
    except (OSError, ValueError, KeyError):
        return None


def offline_rate(src: str, dst: str) -> dict | None:
    """Best cached rate for src->dst from ANY cached base (newest cache wins)."""
    best: dict | None = None
    if not CACHE_DIR.is_dir():
        return None
    for path in CACHE_DIR.glob("rates_*.json"):
        cached = read_cache(path.name)
        if cached is None:
            continue
        data, fetched_at = cached
        base = data.get("base") or path.stem.removeprefix("rates_")
        rates = {k: float(v) for k, v in (data.get("rates") or {}).items()}
        rates[base] = 1.0
        if src not in rates or dst not in rates or rates[src] == 0:
            continue
        candidate = {"rate": rates[dst] / rates[src], "date": data.get("date", "?"),
                     "fetched_at": fetched_at, "base": base}
        key = (fetched_at, base == src)
        if best is None or key > (best["fetched_at"], best["base"] == src):
            best = candidate
    return best


def get_rate(src: str, dst: str) -> dict:
    """Return {rate, date, fetched_at|None, base, offline_reason|None}."""
    try:
        data, problem = cached_fetch(f"{API_BASE}/latest?from={src}", f"rates_{src}.json")
    except Rejected as exc:
        sys.exit(f"The rates service does not know '{src}' ({exc}). "
                 "See supported codes with --list.")
    if data is not None:
        rates = data.get("rates", {})
        if dst not in rates:
            sys.exit(f"Unknown target currency '{dst}'. See supported codes with --list.")
        return {"rate": float(rates[dst]), "date": data.get("date", "?"),
                "fetched_at": None, "base": src, "offline_reason": None}

    cached = offline_rate(src, dst)
    if cached is None:
        sys.exit(f"No network ({problem}) and no cached rates that cover {src} -> {dst} yet.\n"
                 "Connect once to prime the cache.")
    cached["offline_reason"] = problem
    return cached


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
        try:
            currencies, problem = cached_fetch(f"{API_BASE}/currencies", "currencies.json")
        except Rejected as exc:
            sys.exit(f"The rates service refused the request ({exc}).")
        if currencies is None:
            cached = read_cache("currencies.json")
            if cached is None:
                sys.exit(f"No network ({problem}) and no cached currency list yet.")
            currencies, fetched_at = cached
            print(f"(offline - currency list cached {fetched_at})", file=sys.stderr)
        for code, name in sorted(currencies.items()):
            print(f"  {code}  {name}")
        print(f"\n{len(currencies)} currencies (ECB reference set - PAB is pegged 1:1 to USD).")
        return

    if args.amount is None or not args.src or not args.dst:
        parser.error("usage: AMOUNT SRC DST (e.g. 100 USD EUR), or --list")

    src, dst = args.src.upper(), args.dst.upper()
    for code in (src, dst):
        if not CODE_RE.fullmatch(code):
            sys.exit(f"'{code}' is not a currency code (three letters, e.g. USD, EUR, MXN).")

    # Panama's balboa is pegged 1:1 to USD and not in the ECB set; alias it.
    pab_note = ""
    if "PAB" in (src, dst):
        pab_note = "  (PAB pegged 1:1 to USD)"
        src = "USD" if src == "PAB" else src
        dst = "USD" if dst == "PAB" else dst

    if src == dst:
        print(f"{args.amount:,.2f} {args.src.upper()} = {args.amount:,.2f} {args.dst.upper()}{pab_note}")
        return

    info = get_rate(src, dst)
    result = args.amount * info["rate"]
    print(f"{args.amount:,.2f} {args.src.upper()} = {result:,.2f} {args.dst.upper()}{pab_note}")
    line = f"Rate: 1 {src} = {info['rate']:.4f} {dst}  (ECB date {info['date']})"
    if info["offline_reason"]:
        via = f", cross rate via {info['base']}" if info["base"] not in (src, dst) else ""
        line += f" [cached {info['fetched_at']}{via}]"
        print(f"(offline: {info['offline_reason']} - using cached rates)", file=sys.stderr)
    print(line)


if __name__ == "__main__":
    main()

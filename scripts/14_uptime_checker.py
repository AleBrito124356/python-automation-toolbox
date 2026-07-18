#!/usr/bin/env python3
"""Check a list of websites concurrently and print an uptime status table.

Sites come from a small YAML file (see examples/sites.example.yaml). All
checks run in parallel with async httpx, so 50 sites take about as long as
the slowest one. Optional webhook alert: set UPTIME_WEBHOOK_URL in the
environment (or webhook_url in the YAML) and failures are POSTed as JSON --
works with Slack and Discord incoming webhooks. --loop turns it into a
lightweight monitor.

Usage:
    python 14_uptime_checker.py sites.yaml
    python 14_uptime_checker.py sites.yaml --loop 300
    python 14_uptime_checker.py sites.yaml --timeout 5

Dependencies: httpx, PyYAML
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import httpx
    import yaml
except ImportError:
    sys.exit("Dependencies required: pip install httpx PyYAML")


def load_config(path: Path) -> dict:
    if not path.is_file():
        sys.exit(f"Config not found: {path}\n"
                 "Copy examples/sites.example.yaml and edit it.")
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sites = config.get("sites") or []
    if not sites:
        sys.exit("Config has no 'sites' list.")
    for site in sites:
        if "url" not in site:
            sys.exit(f"Site entry missing 'url': {site}")
        site.setdefault("name", site["url"])
        site.setdefault("expect_status", 200)
    return config


async def check_site(client: httpx.AsyncClient, site: dict) -> dict:
    start = time.perf_counter()
    try:
        resp = await client.get(site["url"])
        latency_ms = (time.perf_counter() - start) * 1000
        ok = resp.status_code == site["expect_status"]
        return {"site": site, "ok": ok, "status": resp.status_code, "latency_ms": latency_ms,
                "error": None if ok else f"expected {site['expect_status']}"}
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return {"site": site, "ok": False, "status": None, "latency_ms": latency_ms,
                "error": type(exc).__name__}


async def run_checks(config: dict, timeout: float) -> list[dict]:
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True,
        headers={"User-Agent": "uptime-checker/1.0"},
    ) as client:
        return await asyncio.gather(*(check_site(client, s) for s in config["sites"]))


def print_table(results: list[dict]) -> None:
    name_w = max(len(r["site"]["name"]) for r in results)
    print(f"\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {'SITE'.ljust(name_w)}  {'STATUS':>6}  {'LATENCY':>9}  RESULT")
    for r in sorted(results, key=lambda r: r["ok"]):
        status = str(r["status"]) if r["status"] is not None else "--"
        verdict = "UP" if r["ok"] else f"DOWN ({r['error']})"
        print(f"  {r['site']['name'].ljust(name_w)}  {status:>6}  "
              f"{r['latency_ms']:>7.0f}ms  {verdict}")


async def send_webhook(url: str, failures: list[dict]) -> None:
    lines = [f"{r['site']['name']}: {r['error']} ({r['site']['url']})" for r in failures]
    payload = {
        "text": "Uptime alert - " + "; ".join(lines),      # Slack format
        "content": "Uptime alert - " + "; ".join(lines),   # Discord format
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
        print(f"  Webhook alert sent ({len(failures)} failure(s)).")
    except httpx.HTTPError as exc:
        print(f"  Webhook failed: {type(exc).__name__}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Concurrent website uptime checks from YAML.")
    parser.add_argument("config", help="YAML file with the sites list")
    parser.add_argument("--timeout", type=float, default=10, help="per-request timeout seconds")
    parser.add_argument("--loop", type=int, metavar="SECONDS",
                        help="keep checking every N seconds until Ctrl+C")
    args = parser.parse_args()

    config = load_config(Path(args.config).expanduser())
    webhook = os.environ.get("UPTIME_WEBHOOK_URL") or config.get("webhook_url") or ""

    try:
        while True:
            results = asyncio.run(run_checks(config, args.timeout))
            print_table(results)
            failures = [r for r in results if not r["ok"]]
            if failures and webhook:
                asyncio.run(send_webhook(webhook, failures))
            if args.loop is None:
                sys.exit(1 if failures else 0)
            time.sleep(args.loop)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

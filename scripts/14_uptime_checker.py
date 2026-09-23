#!/usr/bin/env python3
"""Check a list of websites concurrently: status, content and latency, with alerts.

Sites come from a small YAML file (see examples/sites.example.yaml). All
checks run in parallel with async httpx, so 50 sites take about as long as
the slowest one. Each site is UP, DOWN (connection error, unexpected status,
or `expect_text` missing from the body) or SLOW (correct answer, but slower
than its `max_latency_ms`).

Alerts fire on state CHANGES only, remembered in a small JSON state file
(default: <config>.state.json next to the config, or --state PATH), so it
behaves the same from cron as in --loop mode: an outage produces one DOWN
alert, and the recovery one RECOVERED alert with the outage duration.
--alert-every MINUTES repeats the alert while a site stays DOWN/SLOW.
Webhook: set UPTIME_WEBHOOK_URL (or webhook_url in the YAML); the JSON
POST works with Slack and Discord incoming webhooks. A webhook that answers
non-2xx counts as failed (retried once) and the alert is retried on the
next run instead of being lost.

Exit codes: 0 all sites UP, 1 any site DOWN or SLOW, 2 config error.

Usage:
    python 14_uptime_checker.py sites.yaml
    python 14_uptime_checker.py sites.yaml --loop 300 --alert-every 60
    python 14_uptime_checker.py sites.yaml --json --state /var/tmp/uptime.json

Dependencies: httpx, PyYAML
"""

from __future__ import annotations

import argparse
import asyncio
import json
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

UP, DOWN, SLOW = "UP", "DOWN", "SLOW"
USER_AGENT = "uptime-checker/2.0"


class ConfigError(Exception):
    """Invalid config: reported on stderr, exit code 2."""


# --------------------------------------------------------------------------- config

def load_config(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"Config not found: {path}\nCopy examples/sites.example.yaml and edit it.")
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config is not valid YAML: {exc}") from None
    if not isinstance(config, dict):
        raise ConfigError("Config must be a mapping with a 'sites' list.")
    sites = config.get("sites") or []
    if not isinstance(sites, list) or not sites:
        raise ConfigError("Config has no 'sites' list.")
    names: set[str] = set()
    for site in sites:
        if not isinstance(site, dict) or "url" not in site:
            raise ConfigError(f"Site entry missing 'url': {site}")
        site.setdefault("name", site["url"])
        site["name"] = str(site["name"])
        if site["name"] in names:
            raise ConfigError(f"Two sites are named '{site['name']}'; names must be unique.")
        names.add(site["name"])
        expect = site.setdefault("expect_status", 200)
        codes = expect if isinstance(expect, list) else [expect]
        if not codes or not all(isinstance(c, int) and 100 <= c <= 599 for c in codes):
            raise ConfigError(f"{site['name']}: expect_status must be an HTTP code or a list of codes.")
        site["expect_status"] = codes
        if "expect_text" in site and not isinstance(site["expect_text"], str):
            raise ConfigError(f"{site['name']}: expect_text must be a string.")
        limit = site.get("max_latency_ms")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, (int, float))
                                  or limit <= 0):
            raise ConfigError(f"{site['name']}: max_latency_ms must be a positive number.")
    return config


# --------------------------------------------------------------------------- checks

async def check_site(client: httpx.AsyncClient, site: dict) -> dict:
    start = time.perf_counter()
    result = {"name": site["name"], "url": site["url"], "state": DOWN,
              "status": None, "latency_ms": None, "error": None}
    try:
        resp = await client.get(site["url"])
    except httpx.HTTPError as exc:
        result["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        result["error"] = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        return result
    latency = (time.perf_counter() - start) * 1000
    result["status"] = resp.status_code
    result["latency_ms"] = round(latency, 1)
    expected = site["expect_status"]
    if resp.status_code not in expected:
        want = expected[0] if len(expected) == 1 else "/".join(map(str, expected))
        result["error"] = f"HTTP {resp.status_code}, expected {want}"
    elif site.get("expect_text") and site["expect_text"] not in resp.text:
        result["error"] = f"expected text {site['expect_text']!r} not found in the page"
    elif site.get("max_latency_ms") and latency > site["max_latency_ms"]:
        result["state"] = SLOW
        result["error"] = f"{latency:.0f}ms > {site['max_latency_ms']}ms limit"
    else:
        result["state"] = UP
    return result


async def run_checks(config: dict, timeout: float) -> list[dict]:
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT},
    ) as client:
        return list(await asyncio.gather(*(check_site(client, s) for s in config["sites"])))


# --------------------------------------------------------------------------- state

def default_state_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.stem + ".state.json")


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def human_duration(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def evaluate(results: list[dict], state: dict, now: datetime,
             alert_every_min: float | None = None) -> tuple[list[dict], dict]:
    """Compare results with the remembered state.

    Returns (events, new_state). An event is due when a site's state differs
    from the last state that was successfully announced ("notified"), or when
    --alert-every says a still-failing site should be announced again.
    """
    now_iso = now.isoformat()
    new_state: dict = {}
    events: list[dict] = []
    for r in results:
        prev = state.get(r["name"]) or {}
        prev_state = prev.get("state", UP)
        notified = prev.get("notified", UP)
        since = prev["since"] if prev_state == r["state"] and prev.get("since") else now_iso
        if r["state"] != UP:
            problem_since = (prev.get("problem_since") if prev_state != UP else None) or now_iso
        elif notified != UP:
            problem_since = prev.get("problem_since")  # kept until the recovery is announced
        else:
            problem_since = None
        record = {"state": r["state"], "since": since, "problem_since": problem_since,
                  "notified": notified, "last_alert": prev.get("last_alert"),
                  "last_checked": now_iso, "last_error": r["error"]}

        event = None
        if r["state"] != notified:
            if r["state"] == UP:
                duration = ((datetime.fromisoformat(since)
                             - datetime.fromisoformat(problem_since)).total_seconds()
                            if problem_since else None)
                event = {"kind": "RECOVERED", "was": notified, "duration_s": duration}
            else:
                event = {"kind": r["state"]}
        elif r["state"] != UP and alert_every_min and record["last_alert"]:
            elapsed = (now - datetime.fromisoformat(record["last_alert"])).total_seconds()
            if elapsed >= alert_every_min * 60:
                duration = (now - datetime.fromisoformat(problem_since)).total_seconds()
                event = {"kind": f"STILL {r['state']}", "duration_s": duration}
        if event:
            event.update({"name": r["name"], "url": r["url"], "error": r["error"],
                          "state": r["state"]})
            events.append(event)
        new_state[r["name"]] = record
    return events, new_state


def mark_notified(state: dict, events: list[dict], now: datetime) -> None:
    for event in events:
        record = state[event["name"]]
        record["notified"] = event["state"]
        record["last_alert"] = now.isoformat()
        if event["state"] == UP:
            record["problem_since"] = None


def describe(event: dict) -> str:
    kind = event["kind"]
    if kind == "RECOVERED":
        took = f" after {human_duration(event['duration_s'])}" if event.get("duration_s") else ""
        was = f" (was {event['was']})" if event.get("was") == SLOW else ""
        return f"RECOVERED {event['name']}{took}{was} ({event['url']})"
    if kind.startswith("STILL"):
        return (f"{kind} {event['name']} for {human_duration(event['duration_s'])}: "
                f"{event['error']} ({event['url']})")
    return f"{kind} {event['name']}: {event['error']} ({event['url']})"


# --------------------------------------------------------------------------- webhook

def send_webhook(url: str, events: list[dict], retries: int = 1,
                 retry_delay: float = 1.0) -> tuple[bool, str]:
    """POST the events; returns (delivered, detail). Non-2xx counts as a failure."""
    lines = [describe(e) for e in events]
    text = "Uptime alert - " + "; ".join(lines)
    payload = {"text": text, "content": text}   # Slack reads "text", Discord "content"
    detail = ""
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=10, headers={"User-Agent": USER_AGENT}) as client:
                resp = client.post(url, json=payload)
            if 200 <= resp.status_code < 300:
                return True, f"HTTP {resp.status_code}"
            detail = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            detail = type(exc).__name__
        if attempt < retries:
            time.sleep(retry_delay)
    return False, detail


# --------------------------------------------------------------------------- output

def print_table(results: list[dict], events: list[dict], now: datetime) -> None:
    changed = {e["name"] for e in events}
    name_w = max(len(r["name"]) for r in results)
    print(f"\n{now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {'SITE'.ljust(name_w)}  {'STATUS':>6}  {'LATENCY':>9}  RESULT")
    order = {DOWN: 0, SLOW: 1, UP: 2}
    for r in sorted(results, key=lambda r: order[r["state"]]):
        status = str(r["status"]) if r["status"] is not None else "--"
        latency = f"{r['latency_ms']:>7.0f}ms" if r["latency_ms"] is not None else f"{'--':>9}"
        verdict = r["state"] if r["state"] == UP else f"{r['state']} ({r['error']})"
        marker = "  <- changed" if r["name"] in changed else ""
        print(f"  {r['name'].ljust(name_w)}  {status:>6}  {latency}  {verdict}{marker}")


def run_once(config: dict, args, state_path: Path, webhook: str) -> int:
    now = datetime.now().astimezone().replace(microsecond=0)
    results = asyncio.run(run_checks(config, args.timeout))
    events, state = evaluate(results, load_state(state_path), now, args.alert_every)

    webhook_info = None
    if events and webhook:
        delivered, detail = send_webhook(webhook, events, retry_delay=args.retry_delay)
        webhook_info = {"delivered": delivered, "detail": detail, "events": len(events)}
        if delivered:
            mark_notified(state, events, now)
    elif events:
        mark_notified(state, events, now)   # no webhook: the console is the notification
    try:
        save_state(state_path, state)
    except OSError as exc:
        print(f"Warning: could not save state to {state_path}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps({"checked_at": now.isoformat(), "results": results,
                          "events": events, "webhook": webhook_info}))
    else:
        print_table(results, events, now)
        for event in events:
            print(f"  ! {describe(event)}")
        if webhook_info:
            if webhook_info["delivered"]:
                print(f"  Webhook alert sent ({len(events)} event(s), {webhook_info['detail']}).")
            else:
                print(f"  Webhook failed: {webhook_info['detail']} "
                      "(the alert will be retried on the next run)")
    return 0 if all(r["state"] == UP for r in results) else 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # loop output must reach log files
    parser = argparse.ArgumentParser(description="Concurrent website uptime checks from YAML.")
    parser.add_argument("config", help="YAML file with the sites list")
    parser.add_argument("--timeout", type=float, default=10, help="per-request timeout seconds")
    parser.add_argument("--loop", type=float, metavar="SECONDS",
                        help="keep checking every N seconds until Ctrl+C")
    parser.add_argument("--state", metavar="PATH",
                        help="state file (default: <config>.state.json next to the config)")
    parser.add_argument("--alert-every", type=float, metavar="MINUTES",
                        help="repeat the alert while a site stays DOWN/SLOW (default: once)")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object per check cycle instead of a table")
    parser.add_argument("--retry-delay", type=float, default=1.0, help=argparse.SUPPRESS)
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(2)
    state_path = Path(args.state).expanduser() if args.state else default_state_path(config_path)
    webhook = os.environ.get("UPTIME_WEBHOOK_URL") or config.get("webhook_url") or ""

    try:
        while True:
            code = run_once(config, args, state_path, webhook)
            if args.loop is None:
                sys.exit(code)
            time.sleep(args.loop)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

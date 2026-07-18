#!/usr/bin/env python3
"""Audit what launches with Windows: Run registry keys + Startup folders.

Strictly read-only -- it changes nothing, it just shows you every program
configured to auto-start, where that configuration lives, and what each
location means. Use it to spot the updater daemons and vendor helpers that
accumulate over the years. Remove entries yourself via Task Manager >
Startup apps, or regedit if you know what you are doing.

Usage:
    python 20_startup_auditor.py
    python 20_startup_auditor.py --json
    python 20_startup_auditor.py --json > startup_report.json

Dependencies: stdlib only (Windows)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if sys.platform != "win32":
    sys.exit("This script audits Windows startup locations and only runs on Windows.")

import winreg  # noqa: E402  (guarded import: only available on Windows)

REGISTRY_LOCATIONS = [
    (winreg.HKEY_CURRENT_USER,
     r"Software\Microsoft\Windows\CurrentVersion\Run",
     "HKCU Run",
     "Starts every time YOUR user logs in. Most user-installed apps register here."),
    (winreg.HKEY_CURRENT_USER,
     r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
     "HKCU RunOnce",
     "Runs once at your next login, then Windows deletes the entry. Usually installer leftovers."),
    (winreg.HKEY_LOCAL_MACHINE,
     r"Software\Microsoft\Windows\CurrentVersion\Run",
     "HKLM Run",
     "Starts for EVERY user on this PC. Needs admin rights to modify. Drivers and system tools live here."),
    (winreg.HKEY_LOCAL_MACHINE,
     r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
     "HKLM RunOnce",
     "Machine-wide run-once entries, commonly used to finish installs after a reboot."),
    (winreg.HKEY_LOCAL_MACHINE,
     r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
     "HKLM Run 32-bit",
     "Same as HKLM Run but for 32-bit programs on 64-bit Windows."),
]


def read_registry(hive: int, subkey: str) -> list[dict]:
    entries: list[dict] = []
    try:
        with winreg.OpenKey(hive, subkey) as key:
            i = 0
            while True:
                try:
                    name, value, _type = winreg.EnumValue(key, i)
                except OSError:
                    break
                entries.append({"name": name, "command": str(value)})
                i += 1
    except FileNotFoundError:
        pass
    except PermissionError:
        entries.append({"name": "(access denied)", "command": "run from an elevated prompt to read this key"})
    return entries


def startup_folders() -> list[tuple[str, str, Path]]:
    folders = []
    appdata = os.environ.get("APPDATA")
    programdata = os.environ.get("ProgramData")
    if appdata:
        folders.append((
            "User Startup folder",
            "Shortcuts here launch at YOUR login. Open with: shell:startup",
            Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup",
        ))
    if programdata:
        folders.append((
            "Common Startup folder",
            "Shortcuts here launch for ALL users. Open with: shell:common startup",
            Path(programdata) / "Microsoft/Windows/Start Menu/Programs/StartUp",
        ))
    return folders


def collect() -> list[dict]:
    report: list[dict] = []
    for hive, subkey, label, explanation in REGISTRY_LOCATIONS:
        report.append({
            "location": label,
            "path": subkey,
            "explanation": explanation,
            "entries": read_registry(hive, subkey),
        })
    for label, explanation, folder in startup_folders():
        entries = []
        if folder.is_dir():
            entries = [{"name": item.name, "command": str(item)}
                       for item in sorted(folder.iterdir())
                       if item.name.lower() != "desktop.ini"]
        report.append({
            "location": label,
            "path": str(folder),
            "explanation": explanation,
            "entries": entries,
        })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only report of Windows startup entries.")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args()

    report = collect()

    if args.json:
        print(json.dumps(report, indent=2))
        return

    total = sum(len(loc["entries"]) for loc in report)
    print(f"\nWindows startup audit - {total} entries across {len(report)} locations")
    print("Read-only report. To disable entries: Task Manager > Startup apps.\n")

    for loc in report:
        print(f"== {loc['location']} ==")
        print(f"   Where: {loc['path']}")
        print(f"   What:  {loc['explanation']}")
        if loc["entries"]:
            for entry in loc["entries"]:
                print(f"     - {entry['name']}")
                print(f"       {entry['command']}")
        else:
            print("     (empty)")
        print()


if __name__ == "__main__":
    main()

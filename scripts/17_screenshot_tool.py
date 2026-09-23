#!/usr/bin/env python3
"""Take screenshots from the command line: full screen, region, or timelapse.

Built on mss, which is fast enough to run in a loop. Auto-names files with a
timestamp, supports multi-monitor setups, and --every N --count M turns it
into a timelapse recorder (great paired with 07_video_to_gif afterwards).

Usage:
    python 17_screenshot_tool.py
    python 17_screenshot_tool.py --region 100 100 1280 720 -o shots/
    python 17_screenshot_tool.py --every 5 --count 120 -o timelapse/

Dependencies: mss
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import mss
    import mss.tools
except ImportError:
    sys.exit("mss is required: pip install mss")


def open_mss():
    """mss >= 10 exposes mss.MSS (mss.mss is deprecated); older releases only mss.mss."""
    factory = getattr(mss, "MSS", None) or mss.mss
    return factory()


def unique_name(out_dir: Path, prefix: str = "shot") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{prefix}_{stamp}.png"
    n = 1
    while path.exists():
        path = out_dir / f"{prefix}_{stamp}_{n}.png"
        n += 1
    return path


def grab(sct: "mss.base.MSSBase", monitor: dict, out_dir: Path) -> Path:
    img = sct.grab(monitor)
    path = unique_name(out_dir)
    mss.tools.to_png(img.rgb, img.size, output=str(path))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Screenshots: full, region, or timelapse.")
    parser.add_argument("-o", "--out", default=".", help="output folder (default: current dir)")
    parser.add_argument("--monitor", type=int, default=1,
                        help="monitor number, 0 = all combined (default: 1)")
    parser.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                        help="capture a region instead of a monitor")
    parser.add_argument("--every", type=float, metavar="SECONDS",
                        help="timelapse mode: capture every N seconds")
    parser.add_argument("--count", type=int, default=0,
                        help="timelapse mode: stop after N shots, 0 = until Ctrl+C")
    parser.add_argument("--list", action="store_true", help="list monitors and exit")
    args = parser.parse_args()

    out_dir = Path(args.out).expanduser()
    if args.region and (args.region[2] <= 0 or args.region[3] <= 0):
        parser.error("--region width and height must be positive")
    if args.every is not None and args.every <= 0:
        parser.error("--every must be a positive number of seconds")

    with open_mss() as sct:
        if args.list:
            for i, mon in enumerate(sct.monitors):
                label = "all monitors combined" if i == 0 else f"monitor {i}"
                print(f"  [{i}] {label}: {mon['width']}x{mon['height']} at "
                      f"({mon['left']}, {mon['top']})")
            return

        out_dir.mkdir(parents=True, exist_ok=True)
        if args.region:
            x, y, w, h = args.region
            monitor = {"left": x, "top": y, "width": w, "height": h}
        else:
            if args.monitor >= len(sct.monitors):
                sys.exit(f"No monitor {args.monitor}. Use --list to see what exists.")
            monitor = sct.monitors[args.monitor]

        if not args.every:
            path = grab(sct, monitor, out_dir)
            print(f"Saved: {path.resolve()}")
            return

        taken = 0
        print(f"Timelapse: every {args.every}s"
              + (f", {args.count} shots total" if args.count else ", Ctrl+C to stop"))
        try:
            while True:
                path = grab(sct, monitor, out_dir)
                taken += 1
                print(f"  [{taken}] {path.name}")
                if args.count and taken >= args.count:
                    break
                time.sleep(args.every)
        except KeyboardInterrupt:
            pass
        print(f"\nCaptured {taken} frame(s) -> {out_dir.resolve()}")
        print("Tip: stitch them with ffmpeg, or make a GIF with 07_video_to_gif.py.")


if __name__ == "__main__":
    main()

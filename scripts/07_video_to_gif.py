#!/usr/bin/env python3
"""Convert a video clip to a high-quality GIF using ffmpeg's two-pass palette.

Wraps the palettegen/paletteuse dance so GIFs come out with clean colors
instead of the dithered mess a naive one-pass conversion produces. Checks
that ffmpeg is installed and prints per-OS install instructions if not.

Usage:
    python 07_video_to_gif.py demo.mp4
    python 07_video_to_gif.py demo.mp4 -o demo.gif --fps 12 --width 480
    python 07_video_to_gif.py screen.mkv --start 00:00:05 --duration 8 --width 640

Dependencies: stdlib only (requires the ffmpeg binary on PATH)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

INSTALL_HELP = """ffmpeg was not found on PATH. Install it first:
  Windows:  winget install Gyan.FFmpeg   (or: choco install ffmpeg)
  macOS:    brew install ffmpeg
  Linux:    sudo apt install ffmpeg      (or your distro's equivalent)
Then re-open the terminal so PATH updates take effect."""


def run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        sys.exit(f"ffmpeg failed (exit {result.returncode}):\n{tail}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Video to GIF with two-pass palette for crisp colors."
    )
    parser.add_argument("input", help="input video (mp4, mkv, mov, webm, ...)")
    parser.add_argument("-o", "--output", help="output GIF (default: <input>.gif)")
    parser.add_argument("--fps", type=int, default=15, help="GIF frame rate (default: 15)")
    parser.add_argument("--width", type=int, default=480,
                        help="output width in px, height auto (default: 480)")
    parser.add_argument("--start", help="start position, e.g. 00:00:05 or 5.5")
    parser.add_argument("--duration", help="clip length in seconds, e.g. 8")
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit(INSTALL_HELP)

    src = Path(args.input).expanduser()
    if not src.is_file():
        sys.exit(f"Not found: {src}")
    out = Path(args.output) if args.output else src.with_suffix(".gif")

    seek: list[str] = []
    if args.start:
        seek += ["-ss", args.start]
    if args.duration:
        seek += ["-t", args.duration]

    filters = f"fps={args.fps},scale={args.width}:-1:flags=lanczos"

    with tempfile.TemporaryDirectory() as tmp:
        palette = str(Path(tmp) / "palette.png")

        print("Pass 1/2: generating color palette...")
        run_ffmpeg(["ffmpeg", "-y", *seek, "-i", str(src),
                    "-vf", f"{filters},palettegen=stats_mode=diff", palette])

        print("Pass 2/2: rendering GIF...")
        run_ffmpeg(["ffmpeg", "-y", *seek, "-i", str(src), "-i", palette,
                    "-lavfi", f"{filters} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4",
                    str(out)])

    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"Done: {out} ({size_mb:.1f} MB, {args.fps} fps, {args.width}px wide)")
    if size_mb > 10:
        print("Tip: GIFs over ~10 MB embed poorly. Try --fps 10, a smaller --width, or --duration.")


if __name__ == "__main__":
    main()

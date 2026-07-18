#!/usr/bin/env python3
"""Turn text into natural-sounding speech (MP3) with Microsoft Edge voices.

Uses edge-tts, which needs no API key. Over 300 neural voices across dozens
of languages -- list them with --list-voices, filter by locale prefix. Reads
text from the command line or a file, and supports rate/pitch adjustments.

Usage:
    python 15_text_to_speech.py "Hello from Panama" -o hello.mp3
    python 15_text_to_speech.py --file articulo.txt --voice es-MX-DaliaNeural -o articulo.mp3
    python 15_text_to_speech.py --list-voices --lang es

Dependencies: edge-tts
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    import edge_tts
except ImportError:
    sys.exit("edge-tts is required: pip install edge-tts")

DEFAULT_VOICE = "en-US-AriaNeural"


async def list_voices(lang: str | None) -> None:
    voices = await edge_tts.list_voices()
    if lang:
        voices = [v for v in voices if v["ShortName"].lower().startswith(lang.lower())]
    if not voices:
        print(f"No voices match locale prefix '{lang}'.")
        return
    voices.sort(key=lambda v: v["ShortName"])
    width = max(len(v["ShortName"]) for v in voices)
    for v in voices:
        print(f"  {v['ShortName'].ljust(width)}  {v['Gender']:<7} {v['Locale']}")
    print(f"\n{len(voices)} voice(s). Try: es-MX-DaliaNeural, es-ES-AlvaroNeural, en-US-GuyNeural")


async def synthesize(text: str, voice: str, out: Path, rate: str | None, pitch: str | None) -> None:
    kwargs: dict[str, str] = {}
    if rate:
        kwargs["rate"] = rate
    if pitch:
        kwargs["pitch"] = pitch
    communicate = edge_tts.Communicate(text, voice, **kwargs)
    await communicate.save(str(out))


def main() -> None:
    parser = argparse.ArgumentParser(description="Text to speech via edge-tts (no API key).")
    parser.add_argument("text", nargs="?", help="text to speak")
    parser.add_argument("--file", help="read the text from this UTF-8 file instead")
    parser.add_argument("--voice", default=DEFAULT_VOICE,
                        help=f"voice ShortName (default: {DEFAULT_VOICE})")
    parser.add_argument("-o", "--output", default="speech.mp3", help="output MP3 path")
    parser.add_argument("--rate", help="speed adjustment, e.g. +20%% or -10%%")
    parser.add_argument("--pitch", help="pitch adjustment, e.g. +5Hz or -20Hz")
    parser.add_argument("--list-voices", action="store_true", help="print available voices")
    parser.add_argument("--lang", help="with --list-voices: locale prefix filter, e.g. es or en-GB")
    args = parser.parse_args()

    if args.list_voices:
        asyncio.run(list_voices(args.lang))
        return

    if args.file:
        source = Path(args.file).expanduser()
        if not source.is_file():
            sys.exit(f"Not found: {source}")
        text = source.read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        parser.error("provide TEXT, --file PATH, or --list-voices")

    text = text.strip()
    if not text:
        sys.exit("Nothing to say: the input text is empty.")

    out = Path(args.output)
    print(f"Synthesizing {len(text)} characters with {args.voice} ...")
    asyncio.run(synthesize(text, args.voice, out, args.rate, args.pitch))
    print(f"Saved: {out.resolve()} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

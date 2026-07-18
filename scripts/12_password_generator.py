#!/usr/bin/env python3
"""Generate strong passwords with the secrets module, plus an entropy readout.

Two modes: fully random (guarantees at least one character from each enabled
class) and pronounceable (consonant-vowel syllables plus digits -- easier to
read over the phone, weaker per character, and the entropy display tells you
exactly how much weaker). All randomness comes from secrets, never random.

Usage:
    python 12_password_generator.py
    python 12_password_generator.py --length 24 --count 5
    python 12_password_generator.py --pronounceable --syllables 5

Dependencies: stdlib only
"""

from __future__ import annotations

import argparse
import math
import secrets
import string

CONSONANTS = "bcdfghjklmnpqrstvwz"
VOWELS = "aeiou"


def strength_label(bits: float) -> str:
    if bits >= 90:
        return "excellent"
    if bits >= 70:
        return "strong"
    if bits >= 50:
        return "reasonable"
    return "weak - increase length"


def secure_shuffle(chars: list[str]) -> None:
    """Fisher-Yates driven by secrets.randbelow."""
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]


def random_password(length: int, digits: bool, symbols: bool, upper: bool) -> tuple[str, float]:
    classes = [string.ascii_lowercase]
    if upper:
        classes.append(string.ascii_uppercase)
    if digits:
        classes.append(string.digits)
    if symbols:
        classes.append("!@#$%^&*-_=+?")

    pool = "".join(classes)
    if length < len(classes):
        raise SystemExit(f"--length must be at least {len(classes)} for the enabled classes")

    # One guaranteed pick per class, remainder from the full pool, then shuffle.
    chars = [secrets.choice(c) for c in classes]
    chars += [secrets.choice(pool) for _ in range(length - len(classes))]
    secure_shuffle(chars)

    entropy = length * math.log2(len(pool))  # slight overestimate given guarantees
    return "".join(chars), entropy


def pronounceable_password(syllables: int, digits: int) -> tuple[str, float]:
    parts = [secrets.choice(CONSONANTS) + secrets.choice(VOWELS) for _ in range(syllables)]
    # Capitalize one syllable at a secret position for a bit of shape.
    cap = secrets.randbelow(syllables)
    parts[cap] = parts[cap].capitalize()
    word = "".join(parts)
    tail = "".join(secrets.choice(string.digits) for _ in range(digits))
    password = f"{word}-{tail}" if tail else word

    entropy = (
        syllables * math.log2(len(CONSONANTS) * len(VOWELS))
        + math.log2(syllables)          # capital position
        + digits * math.log2(10)
    )
    return password, entropy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Password generator (secrets-based) with entropy display."
    )
    parser.add_argument("--length", type=int, default=20,
                        help="length for random mode (default: 20)")
    parser.add_argument("--count", type=int, default=1, help="how many to generate")
    parser.add_argument("--no-digits", action="store_true", help="exclude digits")
    parser.add_argument("--no-symbols", action="store_true", help="exclude symbols")
    parser.add_argument("--no-upper", action="store_true", help="exclude uppercase")
    parser.add_argument("--pronounceable", action="store_true",
                        help="readable consonant-vowel syllables plus a digit tail")
    parser.add_argument("--syllables", type=int, default=6,
                        help="pronounceable mode: syllable count (default: 6)")
    parser.add_argument("--tail-digits", type=int, default=3,
                        help="pronounceable mode: digits appended (default: 3)")
    args = parser.parse_args()

    for _ in range(max(args.count, 1)):
        if args.pronounceable:
            pwd, bits = pronounceable_password(max(args.syllables, 2), max(args.tail_digits, 0))
        else:
            pwd, bits = random_password(
                args.length,
                digits=not args.no_digits,
                symbols=not args.no_symbols,
                upper=not args.no_upper,
            )
        print(f"{pwd}    [{bits:.0f} bits - {strength_label(bits)}]")

    if args.pronounceable:
        print("\nNote: pronounceable passwords trade entropy for memorability."
              "\nAdd syllables to compensate: each one adds ~6.6 bits.")


if __name__ == "__main__":
    main()

"""Display helpers and Jinja filters.

Everything on the wire is base units or lamports; everything here turns that
into something readable. Kept in Python (rather than in the templates) so it is
testable - see `tests/test_web.py`.
"""

from __future__ import annotations

import time
from typing import Any

LAMPORTS_PER_SOL = 1_000_000_000

_UNITS = [
    (86_400 * 365, "y"),
    (86_400 * 30, "mo"),
    (86_400 * 7, "w"),
    (86_400, "d"),
    (3_600, "h"),
    (60, "m"),
    (1, "s"),
]


def duration(seconds: Any) -> str:
    try:
        total = abs(int(seconds))
    except (TypeError, ValueError):
        return "-"
    if total == 0:
        return "0s"
    parts: List[str] = []
    left = total
    for size, label in _UNITS:
        if left >= size:
            count, left = divmod(left, size)
            parts.append(f"{count}{label}")
        if len(parts) == 2:
            break
    return " ".join(parts)


def ago(timestamp: Any) -> str:
    try:
        delta = int(time.time()) - int(timestamp)
    except (TypeError, ValueError):
        return "-"
    if delta < 5:
        return "just now"
    return f"{duration(delta)} ago"


def sol(lamports: Any, digits: int = 4) -> str:
    try:
        value = int(lamports) / LAMPORTS_PER_SOL
    except (TypeError, ValueError):
        return "-"
    if value and abs(value) < 10 ** -digits:
        return f"<0.{'0' * (digits - 1)}1 SOL"
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".") + " SOL"


def tokens(base_units: Any, decimals: int = 6) -> str:
    try:
        value = int(base_units) / (10**decimals)
    except (TypeError, ValueError):
        return "-"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:,.2f}K"
    return f"{value:,.2f}"


def bps(value: Any) -> str:
    try:
        return f"{int(value) / 100:g}%"
    except (TypeError, ValueError):
        return "-"


def pct(bps_value: Any) -> str:
    """A CSS width for a 0-10000 bps progress bar."""
    try:
        clamped = max(0, min(10_000, int(bps_value)))
    except (TypeError, ValueError):
        clamped = 0
    return f"{clamped / 100:.2f}%"


def short(key: Any, lead: int = 4, tail: int = 4) -> str:
    text = str(key or "")
    if not text:
        return "-"
    if len(text) <= lead + tail + 1:
        return text
    return f"{text[:lead]}…{text[-tail:]}"


def price(lamports_per_token: Any) -> str:
    try:
        value = int(lamports_per_token)
    except (TypeError, ValueError):
        return "-"
    if value >= LAMPORTS_PER_SOL // 1_000:
        return f"{value / LAMPORTS_PER_SOL:.6f} SOL"
    return f"{value:,} lamports"


FILTERS = {
    "duration": duration,
    "ago": ago,
    "sol": sol,
    "tokens": tokens,
    "bps": bps,
    "pct": pct,
    "short": short,
    "price": price,
}

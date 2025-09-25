from __future__ import annotations

import re

DURATION_PATTERN = re.compile(r"(?P<value>\d+)(?P<unit>[smhd]?)", re.IGNORECASE)


def parse_duration(text: str) -> int:
    text = text.strip()
    if not text:
        raise ValueError("Empty duration")
    if text.isdigit():
        value = int(text)
        if value <= 0:
            raise ValueError("Duration must be positive")
        return value
    total = 0
    for match in DURATION_PATTERN.finditer(text):
        value = int(match.group("value"))
        unit = match.group("unit").lower()
        if unit == "" or unit == "s":
            total += value
        elif unit == "m":
            total += value * 60
        elif unit == "h":
            total += value * 3600
        elif unit == "d":
            total += value * 86400
        else:  # pragma: no cover
            raise ValueError(f"Unknown duration unit: {unit}")
    if total <= 0:
        raise ValueError("Duration must be positive")
    return total

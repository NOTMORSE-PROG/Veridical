"""Visible source positions for printed report evidence."""

import re


def display_anchor(anchor: str) -> str:
    match = re.fullmatch(r"(?:¶|Â¶|paragraph\s+)(\d+)", anchor.strip(), flags=re.I)
    return f"Extracted paragraph {int(match[1]) + 1}" if match else anchor

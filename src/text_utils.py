"""
Utility helpers for normalising team and market names.
"""

from __future__ import annotations

import unicodedata


def short_team_code(value: str) -> str:
    """Return an abbreviated team code (e.g., 'Manchester United' -> 'MU')."""
    if not value:
        return "?"

    normalized = normalize_text(value)
    if not normalized:
        cleaned = value.upper().replace(" ", "")
        return cleaned[:3] if cleaned else "?"

    words = [word for word in normalized.split(" ") if word and word != "and"]
    if not words:
        cleaned = normalized.replace(" ", "")
        return cleaned[:3].upper() if cleaned else "?"

    initials = "".join(word[0] for word in words)
    if len(initials) >= 2:
        return initials.upper()[:4]

    cleaned = "".join(words)
    return cleaned[:3].upper() if cleaned else value[:3].upper()


def normalize_text(value: str) -> str:
    """
    Normalise a string for fuzzy equality checks.

    - Lower-case
    - Strip diacritics
    - Remove non-alphanumeric characters (retaining spaces)
    - Collapse multiple spaces
    """
    if value is None:
        return ""

    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in ascii_text.lower())
    return " ".join(cleaned.split())

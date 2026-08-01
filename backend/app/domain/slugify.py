"""Slug generation for user-created categories.

Slugs are derived from the display name and never shown in the UI: they are an
internal key that the AI categorizer is validated against
(:mod:`app.ai.categorizer`) and that stored ``CategoryRule`` rows resolve
through, which is also why they are immutable once created.
"""

from __future__ import annotations

import re
import unicodedata

MAX_SLUG_LENGTH = 60
FALLBACK_SLUG = "category"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """``"Home Groceries!"`` -> ``"home-groceries"``.

    Accent-folds via NFKD so "Café" and "Cafe" produce the same slug rather
    than one containing a character the AI prompt would have to round-trip.
    """
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    slug = _NON_ALNUM.sub("-", ascii_only.lower()).strip("-")
    slug = slug[:MAX_SLUG_LENGTH].strip("-")
    return slug or FALLBACK_SLUG


def uniquify(base: str, taken: set[str]) -> str:
    """``base``, then ``base-2``, ``base-3``, … until one is free.

    ``taken`` must include every *system* slug as well as the user's own, so a
    custom category can never shadow a seed slug the rules engine depends on.
    Suffixing (rather than rejecting with a 409) means "Groceries" is always
    accepted, which is the better UX for a name the user chose deliberately.
    """
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"

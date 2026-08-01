"""Text normalization for merchant matching.

Bank descriptions are noisy ("TIM HORTONS #4821 TORONTO ON", "SQ *BLUE BOTTLE").
We reduce them to a stable uppercase key so the same merchant matches across
transactions regardless of store number, city, or POS prefix.
"""

from __future__ import annotations

import re

# Common payment-processor / POS prefixes to strip.
_PREFIXES = ("SQ *", "TST*", "TST* ", "PP*", "PAYPAL *", "POS ", "DEBIT ", "VISA ")
_NON_ALNUM = re.compile(r"[^A-Z0-9 ]+")
_MULTISPACE = re.compile(r"\s+")
# Trailing store-number / reference noise.
_TRAILING_NUMS = re.compile(r"\b\d{2,}\b")


def normalize_merchant(text: str | None) -> str:
    if not text:
        return ""
    value = text.upper().strip()
    for prefix in _PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = value.replace("*", " ")
    value = _NON_ALNUM.sub(" ", value)
    value = _TRAILING_NUMS.sub(" ", value)
    value = _MULTISPACE.sub(" ", value).strip()
    return value

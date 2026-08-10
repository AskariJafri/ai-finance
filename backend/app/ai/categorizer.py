"""AI categorization fallback.

Used only when deterministic rules can't categorize a transaction. Wraps an
``LLMProvider``, constrains it to the known category slugs, and defensively
parses the JSON response. Any failure (LLM down, malformed output, unknown
slug) degrades to ``(None, 0.0)`` so the sync pipeline never breaks because of
the AI layer.
"""

from __future__ import annotations

import json
import re

from app.ai.llm.base import LLMProvider
from app.ai.prompts import CATEGORIZE_SYSTEM, build_categorize_prompt
from app.core.logging import get_logger

logger = get_logger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class AICategorizer:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def categorize(
        self,
        *,
        description: str,
        merchant_name: str | None,
        category_slugs: list[str],
        category_names: list[tuple[str, str]] | None = None,
    ) -> tuple[str | None, float]:
        prompt = build_categorize_prompt(
            description=description,
            merchant_name=merchant_name,
            category_slugs=category_slugs,
            category_names=category_names,
        )
        try:
            raw = await self.llm.complete(
                prompt, system=CATEGORIZE_SYSTEM, temperature=0.0, json_object=True
            )
        except Exception as exc:  # LLMUnavailable or anything unexpected
            logger.warning("AI categorization unavailable: %s", exc)
            return None, 0.0

        slug, confidence = self._parse(raw)
        if slug is None or slug not in category_slugs:
            return None, 0.0
        return slug, confidence

    @staticmethod
    def _parse(raw: str) -> tuple[str | None, float]:
        match = _JSON_RE.search(raw or "")
        if not match:
            return None, 0.0
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None, 0.0
        slug = data.get("category")
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return (str(slug) if slug else None), confidence

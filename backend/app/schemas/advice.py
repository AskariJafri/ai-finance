from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AdviceOut(BaseModel):
    month: str
    content: str | None = None
    source: Literal["ai", "fallback", "none"]
    model: str | None = None
    generated_at: str | None = None
    disclaimer: str

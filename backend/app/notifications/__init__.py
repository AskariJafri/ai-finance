"""Notifications module (Phase 7).

Interface placeholder so services can depend on a stable notification port now;
concrete channels (push, email, in-app) land with the notifications phase.
"""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    async def send(self, *, user_id: str, title: str, body: str) -> None: ...

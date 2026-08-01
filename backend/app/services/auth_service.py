"""Authentication use-cases: register, login, refresh."""

from __future__ import annotations

import uuid

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models import User
from app.repositories.users import UserRepository
from app.schemas.auth import TokenPair
from app.services.errors import (
    EmailAlreadyRegistered,
    InvalidCredentials,
    InvalidToken,
)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(
        self, *, email: str, password: str, full_name: str | None
    ) -> tuple[User, TokenPair]:
        if await self.users.get_by_email(email):
            raise EmailAlreadyRegistered("An account with this email already exists.")
        user = await self.users.create(
            email=email, password_hash=hash_password(password), full_name=full_name
        )
        await self.session.commit()
        return user, self._issue_tokens(user)

    async def login(self, *, email: str, password: str) -> tuple[User, TokenPair]:
        user = await self.users.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentials("Incorrect email or password.")
        if not user.is_active:
            raise InvalidCredentials("Account is disabled.")
        return user, self._issue_tokens(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.InvalidTokenError as exc:
            raise InvalidToken("Invalid or expired refresh token.") from exc
        user = await self.users.get_by_id(uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            raise InvalidToken("User no longer exists.")
        # Rotation: a fresh access + refresh pair is issued on every refresh.
        return self._issue_tokens(user)

    async def get_current_user(self, access_token: str) -> User:
        try:
            payload = decode_token(access_token, expected_type="access")
        except jwt.InvalidTokenError as exc:
            raise InvalidToken("Invalid or expired access token.") from exc
        user = await self.users.get_by_id(uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            raise InvalidToken("User no longer exists.")
        return user

    @staticmethod
    def _issue_tokens(user: User) -> TokenPair:
        subject = str(user.id)
        return TokenPair(
            access_token=create_access_token(subject),
            refresh_token=create_refresh_token(subject),
        )

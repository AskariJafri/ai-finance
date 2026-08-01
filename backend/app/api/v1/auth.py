from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AuthServiceDep, CurrentUser
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, auth: AuthServiceDep) -> TokenPair:
    _, tokens = await auth.register(
        email=body.email, password=body.password, full_name=body.full_name
    )
    return tokens


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, auth: AuthServiceDep) -> TokenPair:
    _, tokens = await auth.login(email=body.email, password=body.password)
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, auth: AuthServiceDep) -> TokenPair:
    return await auth.refresh(body.refresh_token)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)

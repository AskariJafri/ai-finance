from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, SessionDep
from app.repositories.accounts import AccountRepository
from app.schemas.common import AccountOut

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
async def list_accounts(user: CurrentUser, session: SessionDep) -> list[AccountOut]:
    repo = AccountRepository(session)
    accounts = await repo.list_for_user(user.id)
    return [AccountOut.from_model(a) for a in accounts]

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, SettingsServiceDep
from app.schemas.settings import BudgetSettings, PlaidCredentialIn, PlaidCredentialStatus

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/plaid", response_model=PlaidCredentialStatus)
async def get_plaid_status(user: CurrentUser, service: SettingsServiceDep) -> PlaidCredentialStatus:
    return await service.get_plaid_status(user.id)


@router.put("/plaid", response_model=PlaidCredentialStatus)
async def set_plaid_credentials(
    body: PlaidCredentialIn, user: CurrentUser, service: SettingsServiceDep
) -> PlaidCredentialStatus:
    return await service.set_plaid(
        user.id, client_id=body.client_id, secret=body.secret, plaid_env=body.plaid_env
    )


@router.delete("/plaid", response_model=PlaidCredentialStatus)
async def delete_plaid_credentials(
    user: CurrentUser, service: SettingsServiceDep
) -> PlaidCredentialStatus:
    return await service.delete_plaid(user.id)


@router.get("/budget", response_model=BudgetSettings)
async def get_budget_settings(user: CurrentUser, service: SettingsServiceDep) -> BudgetSettings:
    return await service.get_budget(user.id)


@router.put("/budget", response_model=BudgetSettings)
async def set_budget_settings(
    body: BudgetSettings, user: CurrentUser, service: SettingsServiceDep
) -> BudgetSettings:
    return await service.set_budget(user.id, body)

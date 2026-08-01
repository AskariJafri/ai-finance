from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import CategoryGroup

_HEX_COLOR = r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$"


class CreateCategoryRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # TRANSFER is rejected in the service layer (not here) so the error message
    # can explain *why*: a user-created transfer category would silently rewire
    # reported income/spending and the is_transfer derivation, with no product
    # upside — the transfer override on a transaction is the right lever for that.
    group: CategoryGroup
    color: str | None = Field(default=None, pattern=_HEX_COLOR)
    # Required for EXPENSE, forced None for INCOME — enforced in the service,
    # where the group is already being checked.
    budget_bucket: str | None = None


class UpdateCategoryDefinitionRequest(BaseModel):
    """Edits a category's own definition (name/color/bucket).

    Deliberately NOT named ``UpdateCategoryRequest`` — that name is already
    ``schemas.common.UpdateCategoryRequest``, which recategorizes a
    *transaction*. ``slug`` and ``group`` aren't here because both are
    immutable: slug is what the AI categorizer and stored ``CategoryRule`` rows
    key on, and group determines whether a category's spend counts as income
    or expense at all. ``extra="forbid"`` turns an attempt to send either into
    a 422 rather than silently ignoring it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, pattern=_HEX_COLOR)
    budget_bucket: str | None = None

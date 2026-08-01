"""Regression coverage for the personal_finance_category extraction boundary.

The bug this guards against: on plaid-python 42.x, ``PersonalFinanceCategory``
is a ``ModelNormal``, not a dict subclass, so ``isinstance(pfc, dict)`` is
always False. The old extractor gated on exactly that, so every PFC value was
silently dropped against real bank data — and the existing test suite missed
it because ``test_categorization.py`` injects plain strings *downstream* of
this adapter, never exercising the adapter boundary itself.
"""

from __future__ import annotations

from app.integrations.plaid.real_client import extract_pfc


class _ModelLikePFC:
    """Stands in for plaid-python's ModelNormal: not a dict, has to_dict()."""

    def __init__(self, primary: str, detailed: str) -> None:
        self.primary = primary
        self.detailed = detailed

    def to_dict(self) -> dict:
        return {"primary": self.primary, "detailed": self.detailed}


class _ModelLikePFCNoToDict:
    """A model-like object without to_dict — exercises the getattr path."""

    def __init__(self, primary: str, detailed: str) -> None:
        self.primary = primary
        self.detailed = detailed


class _ModelLikePFCMissingDetailed:
    """ModelNormal raises AttributeError for an unset optional field — not None."""

    def __init__(self, primary: str) -> None:
        self.primary = primary

    def to_dict(self) -> dict:
        return {"primary": self.primary}

    @property
    def detailed(self):
        raise AttributeError("detailed")


def _raw(pfc) -> dict:
    return {"personal_finance_category": pfc}


def test_pfc_from_model_like_object_with_to_dict():
    raw = _raw(_ModelLikePFC("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"))
    assert extract_pfc(raw) == ("LOAN_PAYMENTS", "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")


def test_pfc_from_model_like_object_without_to_dict():
    raw = _raw(_ModelLikePFCNoToDict("TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER"))
    assert extract_pfc(raw) == ("TRANSFER_IN", "TRANSFER_IN_ACCOUNT_TRANSFER")


def test_pfc_from_plain_dict():
    raw = _raw({"primary": "FOOD_AND_DRINK", "detailed": "FOOD_AND_DRINK_COFFEE"})
    assert extract_pfc(raw) == ("FOOD_AND_DRINK", "FOOD_AND_DRINK_COFFEE")


def test_pfc_absent_returns_none_none():
    assert extract_pfc({}) == (None, None)


def test_pfc_none_value_returns_none_none():
    assert extract_pfc({"personal_finance_category": None}) == (None, None)


def test_pfc_unset_optional_attribute_returns_none():
    # to_dict() itself omits "detailed" here (mirrors a real ModelNormal, whose
    # to_dict() only includes attributes that were actually set).
    raw = _raw(_ModelLikePFCMissingDetailed("INCOME"))
    assert extract_pfc(raw) == ("INCOME", None)


def test_pfc_raw_not_dict_like_returns_none_none():
    # ``raw`` itself must support .get() (a plain Transaction model or dict
    # does); anything else degrades to (None, None) rather than raising.
    assert extract_pfc(object()) == (None, None)

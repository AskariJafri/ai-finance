"""Seed categories and seed merchant->category rules.

These are inserted once at startup (idempotently) so a fresh install has a
sensible default taxonomy and the rule-based categorizer works before the AI
is ever consulted. User-created categories and correction rules layer on top.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import CategoryGroup


@dataclass(frozen=True)
class SeedCategory:
    slug: str
    name: str
    group: CategoryGroup
    icon: str
    color: str
    # 50/30/20 bucket, for EXPENSE seeds only. None for income/transfer.
    budget_bucket: str | None = None


# Offered to the mobile colour picker when creating a category. The API accepts
# any valid hex — restricting it server-side would be pointless friction.
CUSTOM_CATEGORY_PALETTE: tuple[str, ...] = (
    "#EF4444",
    "#F97316",
    "#F59E0B",
    "#84CC16",
    "#16A34A",
    "#14B8A6",
    "#0EA5E9",
    "#6366F1",
    "#8B5CF6",
    "#EC4899",
    "#64748B",
    "#0F766E",
)


# The one income category that represents *pay from an employer*, as opposed to
# every other way money can arrive. Budget targets key off this: a tax refund or
# an e-transfer from a friend is real money, but it is not something to size a
# monthly budget against. Anything else — including uncategorized inflows —
# counts as other income, which fails safe: an unrecognised deposit can't
# silently inflate a 50/30/20 target.
SALARY_SLUG = "salary"

SEED_CATEGORIES: tuple[SeedCategory, ...] = (
    SeedCategory(SALARY_SLUG, "Salary", CategoryGroup.INCOME, "cash", "#16A34A"),
    SeedCategory("income-other", "Other Income", CategoryGroup.INCOME, "plus", "#22C55E"),
    SeedCategory("groceries", "Groceries", CategoryGroup.EXPENSE, "cart", "#F59E0B", "needs"),
    SeedCategory("restaurants", "Restaurants", CategoryGroup.EXPENSE, "food", "#EF4444", "wants"),
    SeedCategory("coffee", "Coffee", CategoryGroup.EXPENSE, "coffee", "#B45309", "wants"),
    SeedCategory(
        "food-delivery", "Food Delivery", CategoryGroup.EXPENSE, "moped", "#DC2626", "wants"
    ),
    SeedCategory("shopping", "Shopping", CategoryGroup.EXPENSE, "bag", "#8B5CF6", "wants"),
    SeedCategory("transport", "Transport", CategoryGroup.EXPENSE, "car", "#0EA5E9", "needs"),
    SeedCategory("travel", "Travel", CategoryGroup.EXPENSE, "plane", "#06B6D4", "wants"),
    SeedCategory(
        "entertainment", "Entertainment", CategoryGroup.EXPENSE, "movie", "#EC4899", "wants"
    ),
    SeedCategory(
        "subscriptions", "Subscriptions", CategoryGroup.EXPENSE, "repeat", "#6366F1", "wants"
    ),
    SeedCategory("utilities", "Utilities", CategoryGroup.EXPENSE, "bolt", "#64748B", "needs"),
    SeedCategory("rent", "Rent & Housing", CategoryGroup.EXPENSE, "home", "#0F766E", "needs"),
    SeedCategory("health", "Health", CategoryGroup.EXPENSE, "heart", "#F43F5E", "needs"),
    SeedCategory("transfer", "Transfer", CategoryGroup.TRANSFER, "swap", "#94A3B8"),
    # Card payments get their own category rather than sharing "transfer", so the
    # user can see *why* a row left their totals — this is what Monarch and YNAB
    # both do. Same TRANSFER group, so it is excluded from income and spending.
    SeedCategory(
        "credit-card-payment", "Credit Card Payment", CategoryGroup.TRANSFER, "card", "#94A3B8"
    ),
    SeedCategory(
        "uncategorized", "Uncategorized", CategoryGroup.EXPENSE, "help", "#9CA3AF", "wants"
    ),
)

# Normalized merchant/keyword -> category slug. Keys are already normalized the
# same way ``normalize_merchant`` produces (uppercase, no punctuation/numbers).
SEED_RULES: dict[str, str] = {
    # Coffee
    "STARBUCKS": "coffee",
    "TIM HORTONS": "coffee",
    "BLUE BOTTLE": "coffee",
    "SECOND CUP": "coffee",
    # Restaurants
    "MCDONALDS": "restaurants",
    "CHIPOTLE": "restaurants",
    "SUBWAY": "restaurants",
    "KFC": "restaurants",
    # Food delivery
    "UBER EATS": "food-delivery",
    "DOORDASH": "food-delivery",
    "SKIP THE DISHES": "food-delivery",
    "GRUBHUB": "food-delivery",
    # Groceries
    "COSTCO": "groceries",
    "WALMART": "groceries",
    "WHOLE FOODS": "groceries",
    "LOBLAWS": "groceries",
    "SAFEWAY": "groceries",
    "TRADER JOES": "groceries",
    # Shopping
    "AMAZON": "shopping",
    "AMZN": "shopping",
    "BEST BUY": "shopping",
    "TARGET": "shopping",
    # Transport
    "UBER": "transport",
    "LYFT": "transport",
    "SHELL": "transport",
    "PETRO CANADA": "transport",
    "CHEVRON": "transport",
    # Subscriptions / entertainment
    "NETFLIX": "subscriptions",
    "SPOTIFY": "subscriptions",
    "DISNEY PLUS": "subscriptions",
    "YOUTUBE PREMIUM": "subscriptions",
    "APPLE COM BILL": "subscriptions",
    "AUDIBLE": "subscriptions",
    # Utilities
    "HYDRO": "utilities",
    "ROGERS": "utilities",
    "BELL": "utilities",
    "AT T": "utilities",
    "COMCAST": "utilities",
    # Income
    "PAYROLL": "salary",
    "DIRECT DEPOSIT": "salary",
    "PAYCHEK": "salary",
    # --- Transfers & credit-card payments — NOT income and NOT spending. ---
    #
    # These move money between the user's own accounts (or pay a card balance);
    # counting either side would distort income/spending. Keys are matched as
    # substrings of the normalized (uppercased, de-numbered) description, so
    # "PAYMENT TO CHASE CARD ENDING 1234" normalizes to
    # "PAYMENT TO CHASE CARD ENDING" and matches "PAYMENT TO CHASE CARD".
    #
    # Text matching is the LAST signal for transfer-ness, behind Plaid's
    # personal_finance_category and transfer-pair matching. So these patterns are
    # deliberately narrow: a false positive silently erases real spending, which
    # is a far worse failure than a miss. Broad keys like "PAYMENT",
    # "ONLINE PAYMENT", "BILL PAYMENT", "AUTOPAY" or "PREAUTHORIZED PAYMENT" were
    # removed for exactly this reason — they also match "ONLINE PAYMENT HYDRO",
    # "PAYMENT TO LANDLORD" and insurance autopay, erasing rent and utilities.
    # Card payments made under those descriptions are caught by PFC + pairing.
    # Card-specific:
    "PAYMENT THANK YOU": "credit-card-payment",
    "PAYMENT THANKYOU": "credit-card-payment",
    "CREDIT CARD PAYMENT": "credit-card-payment",
    "CARD PAYMENT": "credit-card-payment",
    "CC PAYMENT": "credit-card-payment",
    "CARD PMT": "credit-card-payment",
    "CREDIT CRD": "credit-card-payment",
    "CARDMEMBER SERV": "credit-card-payment",
    "PAYMENT TO CHASE CARD": "credit-card-payment",
    # Genuine account-to-account movement:
    "E TRANSFER": "transfer",
    "ETRANSFER": "transfer",
    "INTERAC": "transfer",
    "FUNDS TRANSFER": "transfer",
    "TRANSFER TO": "transfer",
    "TRANSFER FROM": "transfer",
}

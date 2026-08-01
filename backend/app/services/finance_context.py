"""Builds the factual context block the chat assistant answers from.

Same guardrail as the dashboard and the advice service: **every number here is
computed in SQL (or from real market data) before the model sees it.** The LLM
is only ever asked to explain figures it was handed, never to produce them.

The block is deliberately compact — a local 3B model has a small context
window, so this is a snapshot (totals, top categories, positions) rather than a
transaction dump. Each section is independently guarded: a portfolio lookup
that fails because market data is down must not take the whole answer with it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.reports import ReportsService
from app.core.logging import get_logger
from app.integrations.marketdata.provider import MarketDataProvider
from app.repositories.user_settings import UserSettingsRepository
from app.repositories.watchlist import WatchlistRepository
from app.services.dashboard_service import DashboardService
from app.services.invest_service import InvestService

logger = get_logger(__name__)

# Positions beyond this are summarized as "and N more" — a long tail of tiny
# holdings crowds out the rest of the prompt without changing any answer.
_MAX_POSITIONS = 8
_MAX_CATEGORIES = 6


@dataclass
class FinanceContext:
    """Rendered context plus the labels of the sections that made it in."""

    text: str
    sections: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.sections


def _money(v: float, currency: str = "CAD") -> str:
    return f"${v:,.2f} {currency}"


def _pct(v: float) -> str:
    return f"{v:.0%}"


class FinanceContextBuilder:
    def __init__(self, session: AsyncSession, market_data: MarketDataProvider) -> None:
        self.session = session
        self.dashboard = DashboardService(session)
        self.reports = ReportsService(session)
        self.invest = InvestService(session, market_data)
        self.user_settings = UserSettingsRepository(session)
        self.watchlist = WatchlistRepository(session)

    async def build(self, user_id: uuid.UUID) -> FinanceContext:
        lines: list[str] = []
        sections: list[str] = []

        async def add(label: str, builder) -> bool:
            try:
                block = await builder(user_id)
            except Exception as exc:  # one bad section must not sink the answer
                logger.warning("Chat context section %r failed: %s", label, exc)
                return False
            if not block:
                return False
            lines.append(block)
            sections.append(label)
            return True

        # The budget block is only meaningful once there are transactions to
        # split — otherwise it would claim "budget data" for an empty account.
        if await add("cashflow", self._cashflow):
            await add("budget", self._budget)
        await add("portfolio", self._portfolio)
        await add("watchlist", self._watchlist)

        return FinanceContext(text="\n\n".join(lines), sections=sections)

    async def _cashflow(self, user_id: uuid.UUID) -> str | None:
        s = await self.dashboard.summary(user_id)
        if not s.recent_transactions and s.total_balance == 0:
            return None

        lines = [
            f"CASH AND SPENDING (last 30 days, {s.period_start} to {s.period_end}):",
            f"- Net worth: {_money(s.total_balance)} ({_money(s.cash_balance)} cash minus"
            f" {_money(s.credit_balance)} owed)",
            f"- Money in: {_money(s.period_income)}",
            f"- Money out: {_money(s.period_spending)}",
            f"- Net: {_money(s.net_cash_flow)} (savings rate {_pct(s.savings_rate)})",
        ]
        if s.spend_by_category:
            lines.append("- Top spending categories:")
            lines += [
                f"  * {c.category}: {_money(c.amount)}"
                for c in s.spend_by_category[:_MAX_CATEGORIES]
            ]
        if s.uncategorized_count:
            lines.append(
                f"- {s.uncategorized_count} transactions are uncategorized, so the "
                "category split is approximate."
            )
        return "\n".join(lines)

    async def _budget(self, user_id: uuid.UUID) -> str | None:
        month = date.today().strftime("%Y-%m")
        row = await self.user_settings.get_for_user(user_id)
        override = row.monthly_income_override_cents if row is not None else None
        rule = await self.reports.budget_rule(user_id, month, override)
        if rule.income <= 0:
            return (
                f"50/30/20 BUDGET ({month}): no income detected this month, so the "
                "split cannot be computed. The user can set their monthly income "
                "manually in budget settings."
            )
        return "\n".join(
            [
                f"50/30/20 BUDGET ({month}, income {_money(rule.income)} "
                f"from {rule.income_source}):",
                f"- Needs: {_money(rule.needs.amount)} = {_pct(rule.needs.share)} "
                "of income (target 50%)",
                f"- Wants: {_money(rule.wants.amount)} = {_pct(rule.wants.share)} "
                "of income (target 30%)",
                f"- Left to save: {_money(rule.savings.amount)} = "
                f"{_pct(rule.savings.share)} of income (target 20%)",
            ]
        )

    async def _portfolio(self, user_id: uuid.UUID) -> str | None:
        p = await self.invest.portfolio(user_id)
        if not p.connected or p.account is None:
            return None

        lines = [
            f"INVESTMENT PORTFOLIO (as of {p.as_of}, values in {p.currency}):",
            f"- Total value: {_money(p.account.equity)} "
            f"({_money(p.account.invested)} invested, {_money(p.account.cash)} cash)",
        ]
        if p.positions:
            # Holdings arrive sorted by symbol; the prompt wants them by size so
            # the truncation below drops the tail that matters least.
            ranked = sorted(p.positions, key=lambda pos: pos["market_value"], reverse=True)
            lines.append("- Positions (largest first):")
            for pos in ranked[:_MAX_POSITIONS]:
                lines.append(
                    f"  * {pos['symbol']}: {pos['qty']:g} units, "
                    f"value {_money(pos['market_value'])}, "
                    f"{_pct(pos['weight'])} of the portfolio, "
                    f"unrealized {_money(pos['unrealized_pl'])} "
                    f"({pos['unrealized_pl_pct']:+.1%})"
                )
            extra = len(ranked) - _MAX_POSITIONS
            if extra > 0:
                smallest = sum(pos["market_value"] for pos in ranked[_MAX_POSITIONS:])
                lines.append(
                    f"  * ...and {extra} smaller positions worth {_money(smallest)} in total"
                )
        if p.stale_symbols:
            lines.append(
                f"- No current price for: {', '.join(p.stale_symbols)} "
                "(excluded from the totals above)."
            )

        # Risk is a separate, heavier computation (needs price history) — a
        # failure here still leaves the holdings above in the prompt.
        try:
            health = await self.invest.health_figures(user_id)
        except Exception as exc:
            logger.warning("Chat context risk figures failed: %s", exc)
            return "\n".join(lines)

        risk = health.get("risk") or {}
        bits = [
            f"{k}={risk[k]}"
            for k in ("ann_return", "ann_vol", "sharpe", "max_drawdown", "beta")
            if risk.get(k) is not None
        ]
        if bits:
            lines.append(f"- Risk (1 year): {', '.join(bits)}")
        if health.get("risk_bucket"):
            lines.append(f"- Overall risk profile: {health['risk_bucket']}")
        drifts = health.get("drifted_positions") or []
        if drifts:
            lines.append(
                "- Drifted more than 5% from target weight: "
                + ", ".join(
                    f"{d['symbol']} ({_pct(d['weight'])} vs target {_pct(d['target'])})"
                    for d in drifts
                )
            )
        elif health.get("has_targets"):
            lines.append("- All positions are within 5% of their target weights.")
        return "\n".join(lines)

    async def _watchlist(self, user_id: uuid.UUID) -> str | None:
        items = await self.watchlist.list_for_user(user_id)
        if not items:
            return None
        return "WATCHLIST (tracked, not held): " + ", ".join(i.symbol for i in items)

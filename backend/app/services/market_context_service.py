"""The research layer: company news and the daily macro read.

This service **never raises and never fails a caller.** Every provider failure
becomes a staleness flag, the same way an unavailable quote becomes
``PortfolioOut.stale_symbols`` rather than an exception. A plan that would have
worked yesterday must still work when a news vendor is down.

Cost model — the whole point of the design:

* The macro brief is fetched **once per day for the entire application** and
  shared by every user, surface and ticker. It is not summarized by an LLM;
  the provider's own synthesis rides along inside the same search credit and
  the regime read is a keyword tally, so the layer adds **zero** model calls.
* Company news is fetched only for symbols the caller has already narrowed to.
  Callers pass finalists, never a whole universe. Funds are answered locally.
* Both are cached per day, including the "provider had nothing" answer, so a
  second run on the same day costs nothing at all.

Deliberate non-goal: news does not influence ranking, and a bad flag on a
finalist does not promote the next-ranked name. Either would turn a bounded K
into an unbounded fetch loop, which is exactly the cost this design avoids.
"""

from __future__ import annotations

import asyncio
from datetime import date as date_type
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.decision_context import (
    COVERAGE_COVERED,
    COVERAGE_ERROR,
    COVERAGE_FUND,
    GUARDRAIL_MIN_SEVERITY,
    EventFlag,
    MacroBrief,
    MarketContext,
    NewsHeadline,
    SymbolNews,
    classify,
    is_fund,
    polarity_of,
    read_regime,
    sanitize,
    severity_of,
    summarize_articles,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.news.provider import NewsError, NewsProvider
from app.integrations.websearch.provider import SearchResponse, WebSearchError, WebSearchProvider
from app.repositories.research import MacroBriefRepository, NewsSnapshotRepository
from app.schemas.invest import ResearchOut

logger = get_logger(__name__)

# Finnhub's free tier allows 60 requests a minute. Kept well under so a wide
# plan never looks like a scraper, matching MAX_CONCURRENT_FETCHES in the
# market data service.
MAX_CONCURRENT_NEWS_FETCHES = 4

MACRO_TOPIC = "markets"
MACRO_QUERY = (
    "stock market today: S&P 500, Federal Reserve policy, inflation, "
    "and the major market-moving world events"
)


class MarketContextService:
    def __init__(
        self,
        session: AsyncSession,
        news: NewsProvider | None = None,
        search: WebSearchProvider | None = None,
    ) -> None:
        self.session = session
        self.news = news
        self.search = search
        self.snapshots = NewsSnapshotRepository(session)
        self.briefs = MacroBriefRepository(session)

    # --- public ---
    async def context(self, symbols: list[str], *, macro: bool = True) -> MarketContext:
        """Everything the research layer can say about these symbols today."""
        today = datetime.now().date()
        if not settings.research_enabled:
            return MarketContext(as_of=today, stale=True, stale_reason="research disabled")

        brief = await self.macro() if macro else None
        by_symbol, stale_symbols = await self.for_symbols(symbols)
        reason = None
        if stale_symbols:
            reason = f"news unavailable for {', '.join(stale_symbols)}"
        elif macro and brief is None:
            reason = "macro brief unavailable"
        return MarketContext(
            as_of=today,
            macro=brief,
            symbols=by_symbol,
            stale=bool(stale_symbols) or (macro and brief is None),
            stale_symbols=tuple(stale_symbols),
            stale_reason=reason,
        )

    async def macro(self) -> MacroBrief | None:
        """The shared daily read. One search for the whole application."""
        if not settings.research_enabled or self.search is None:
            return None
        today = datetime.now().date()

        cached = await self.briefs.get(today, MACRO_TOPIC)
        if cached is not None:
            return _brief_from_row(cached)

        try:
            response = await self.search.search(
                MACRO_QUERY,
                topic="news",
                days=2,
                max_results=settings.macro_brief_max_results,
                depth="basic",
                include_answer=True,
            )
        except WebSearchError as exc:
            logger.warning("Macro brief unavailable", extra={"extra": {"error": str(exc)}})
            return None

        brief = _brief_from_response(response, as_of=today)
        await self.briefs.upsert(
            as_of=today,
            topic=MACRO_TOPIC,
            regime=brief.regime,
            summary=brief.summary,
            payload={
                "tally": list(brief.tally),
                "headlines": [
                    {
                        "published_on": h.published_on.isoformat(),
                        "source": h.source,
                        "title": h.title,
                        "url": h.url,
                    }
                    for h in brief.headlines
                ],
            },
        )
        await self.session.flush()
        return brief

    async def for_symbols(self, symbols: list[str]) -> tuple[dict[str, SymbolNews], list[str]]:
        """Per-symbol news for an *already narrowed* list.

        Returns ``(by_symbol, stale_symbols)``. Callers must pass finalists —
        passing a whole universe here is what the cost design exists to avoid.
        """
        today = datetime.now().date()
        wanted = sorted({s.upper() for s in symbols if s})
        if not wanted or not settings.research_enabled:
            return {}, []

        out: dict[str, SymbolNews] = {}
        to_fetch: list[str] = []

        # Funds never reach the provider — a company-news endpoint has nothing
        # to say about an index ETF, and asking spends a metered call.
        for symbol in wanted:
            if is_fund(symbol):
                out[symbol] = SymbolNews(symbol=symbol, as_of=today, coverage=COVERAGE_FUND)
            else:
                to_fetch.append(symbol)

        if to_fetch:
            cached = await self.snapshots.get_many(to_fetch, today)
            for symbol, row in cached.items():
                out[symbol] = _news_from_row(row, as_of=today)
            to_fetch = [s for s in to_fetch if s not in cached]

        if not to_fetch or self.news is None:
            for symbol in to_fetch:
                out[symbol] = SymbolNews(symbol=symbol, as_of=today, coverage=COVERAGE_ERROR)
            return out, list(to_fetch)

        fetched = await self._fetch(to_fetch, today)

        # Writes are sequential: an AsyncSession is not safe for concurrent
        # use, so only the network legs above ran in parallel.
        stale: list[str] = []
        wrote = False
        for symbol, articles, error in fetched:
            if error is not None:
                out[symbol] = SymbolNews(symbol=symbol, as_of=today, coverage=COVERAGE_ERROR)
                stale.append(symbol)
                continue
            news = summarize_articles(
                symbol, articles, as_of=today, max_headlines=settings.news_max_articles
            )
            out[symbol] = news
            await self._store(news, today)
            wrote = True

        if wrote:
            await self.session.flush()
            await self.snapshots.prune(today - timedelta(days=settings.news_retention_days))
        return out, stale

    async def guardrail_flags(self, symbols: list[str]) -> dict[str, EventFlag]:
        """Fresh, material negative events, for the auto-trader.

        Only ``covered`` symbols can produce a flag, and only severity >= 2.
        A bare analyst downgrade is an opinion, not an event.
        """
        if not symbols or not settings.research_enabled:
            return {}
        today = datetime.now().date()
        by_symbol, _stale = await self.for_symbols(symbols)
        out: dict[str, EventFlag] = {}
        for symbol, news in by_symbol.items():
            worst = news.worst_negative(as_of=today, within_days=settings.news_event_fresh_days)
            if worst is not None and worst.severity >= GUARDRAIL_MIN_SEVERITY:
                out[symbol] = worst
        return out

    # --- internals ---
    async def _fetch(
        self, symbols: list[str], today: date_type
    ) -> list[tuple[str, list, str | None]]:
        limiter = asyncio.Semaphore(MAX_CONCURRENT_NEWS_FETCHES)
        start = today - timedelta(days=settings.news_lookback_days)

        async def one(symbol: str) -> tuple[str, list, str | None]:
            async with limiter:
                try:
                    articles = await self.news.company_news(
                        symbol, start, today, limit=settings.news_max_articles
                    )
                    return symbol, articles, None
                except NewsError as exc:
                    logger.warning(
                        "News unavailable", extra={"extra": {"symbol": symbol, "error": str(exc)}}
                    )
                    return symbol, [], str(exc)

        return list(await asyncio.gather(*(one(s) for s in symbols)))

    async def _store(self, news: SymbolNews, today: date_type) -> None:
        """Persist a snapshot. ``error`` coverage is never stored — a transient
        failure must not lock in a day of silence for a symbol."""
        if news.coverage == COVERAGE_ERROR:
            return
        worst = max((f.severity for f in news.flags if f.polarity < 0), default=0)
        await self.snapshots.upsert(
            symbol=news.symbol,
            as_of=today,
            coverage=news.coverage,
            article_count=len(news.headlines),
            flags=",".join(sorted({f.key for f in news.flags}))[:200],
            worst_severity=worst,
            payload={
                "headlines": [
                    {
                        "published_on": h.published_on.isoformat(),
                        "source": h.source,
                        "title": h.title,
                        "url": h.url,
                        "flags": list(h.flags),
                    }
                    for h in news.headlines
                ]
            },
        )


# --- rehydration ------------------------------------------------------------


def _headlines_from_payload(payload: dict, fallback: date_type) -> tuple[NewsHeadline, ...]:
    out: list[NewsHeadline] = []
    for row in (payload or {}).get("headlines", []):
        if not isinstance(row, dict):
            continue
        try:
            published = date_type.fromisoformat(str(row.get("published_on")))
        except (TypeError, ValueError):
            published = fallback
        out.append(
            NewsHeadline(
                published_on=published,
                source=str(row.get("source") or "unknown"),
                title=str(row.get("title") or ""),
                url=str(row.get("url") or ""),
                flags=tuple(row.get("flags") or ()),
            )
        )
    return tuple(out)


def _news_from_row(row, *, as_of: date_type) -> SymbolNews:
    headlines = _headlines_from_payload(row.payload or {}, row.as_of)
    flags = tuple(
        EventFlag(
            key=key,
            polarity=polarity_of(key),
            severity=severity_of(key),
            headline=h.title,
            source=h.source,
            published_on=h.published_on,
        )
        for h in headlines
        for key in h.flags
    )
    return SymbolNews(
        symbol=row.symbol,
        as_of=as_of,
        coverage=row.coverage,
        headlines=headlines,
        flags=flags,
    )


def _brief_from_row(row) -> MacroBrief:
    payload = row.payload or {}
    tally = payload.get("tally") or [0, 0, 0]
    return MacroBrief(
        as_of=row.as_of,
        regime=row.regime,
        tally=(int(tally[0]), int(tally[1]), int(tally[2])),
        summary=row.summary or "",
        headlines=_headlines_from_payload(payload, row.as_of),
    )


def _brief_from_response(response: SearchResponse, *, as_of: date_type) -> MacroBrief:
    headlines: list[NewsHeadline] = []
    for result in response.results:
        title = sanitize(result.title, limit=180)
        if not title:
            continue
        published = result.published_at.date() if result.published_at else as_of
        headlines.append(
            NewsHeadline(
                published_on=published,
                source=sanitize(_host(result.url), limit=40) or "web",
                title=title,
                url=result.url,
                flags=classify(title, sanitize(result.content, limit=240)),
            )
        )
    regime, tally = read_regime([h.title for h in headlines])
    return MacroBrief(
        as_of=as_of,
        regime=regime,
        tally=tally,
        summary=sanitize(response.answer or "", limit=600),
        headlines=tuple(headlines),
    )


def research_out(market: MarketContext | None) -> ResearchOut | None:
    """Render a context for the API. ``None`` in, ``None`` out — a plan that ran
    without research says so by omission rather than by an empty object."""
    if market is None:
        return None
    macro = market.macro
    return ResearchOut(
        as_of=market.as_of.isoformat(),
        macro_regime=macro.regime if macro else None,
        macro_summary=(macro.summary or None) if macro else None,
        macro_headlines=[h.title for h in macro.headlines] if macro else [],
        symbols_with_news=sum(
            1 for n in (market.symbols or {}).values() if n.coverage == COVERAGE_COVERED
        ),
        stale=market.stale,
        stale_reason=market.stale_reason,
    )


def _host(url: str) -> str:
    if "://" not in url:
        return url
    return url.split("://", 1)[1].split("/", 1)[0]

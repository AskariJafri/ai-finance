"""Vendor response shapes, tested against the mapping helpers directly.

The mock clients hand back domain objects and exercise none of the parsing, so
without this file a vendor could change its envelope and every test would stay
green while production silently produced zero articles — which downstream
reads as "no news", which reads as "nothing is wrong". That is the same
failure mode ``test_snaptrade_mapping.py`` was written to prevent.

Payloads below are trimmed real responses with identifiers scrubbed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.integrations.news.finnhub_client import _articles, _to_article
from app.integrations.news.provider import NewsError
from app.integrations.websearch.tavily_client import _parse_published, _to_result

# --- Finnhub /company-news --------------------------------------------------

COMPANY_NEWS_BODY = [
    {
        "category": "company news",
        "datetime": 1755950400,
        "headline": "Apple cuts full-year revenue guidance",
        "id": 7712345,
        "image": "https://example.test/img.png",
        "related": "AAPL",
        "source": "Reuters",
        "summary": "The company lowered its outlook, citing softer demand.",
        "url": "https://example.test/aapl-guidance",
    },
    {
        "category": "company news",
        "datetime": 1755864000,
        "headline": "Analyst raises Apple price target",
        "id": 7712300,
        "related": "AAPL",
        "source": "Barron's",
        "summary": "Services growth cited.",
        "url": "https://example.test/aapl-target",
    },
]


def test_a_normal_company_news_array_maps_cleanly():
    rows = _articles(COMPANY_NEWS_BODY)
    assert len(rows) == 2
    article = _to_article(rows[0])
    assert article is not None
    assert article.headline == "Apple cuts full-year revenue guidance"
    assert article.source == "Reuters"
    assert article.published_at.tzinfo is not None, "timestamps must be tz-aware"
    assert article.published_at.year == 2025 or article.published_at.year == 2026


def test_an_error_object_raises_rather_than_parsing_as_zero_articles():
    """Finnhub answers some rejections with a 200 and an object body.

    Treating that object as an empty sequence would report "no news" for a
    symbol we never actually asked about — exactly the confusion the coverage
    enum exists to prevent, arriving one layer too early to be caught.
    """
    with pytest.raises(NewsError, match="rate limit"):
        _articles({"error": "API limit reached. rate limit exceeded"})


def test_an_unexpected_scalar_body_raises():
    with pytest.raises(NewsError):
        _articles("not json we understand")


def test_a_zero_datetime_row_is_dropped_not_dated_to_the_epoch():
    """Undated wire copy cannot be aged, and freshness is what the guardrail
    turns on — a 1970 article would never look fresh, but it would still
    occupy a headline slot."""
    assert _to_article({"headline": "Undated wire copy", "datetime": 0}) is None


def test_a_row_without_a_headline_is_dropped():
    assert _to_article({"datetime": 1755950400, "summary": "orphaned summary"}) is None


def test_missing_summary_and_url_are_tolerated():
    article = _to_article({"headline": "Bare headline", "datetime": 1755950400})
    assert article is not None
    assert article.summary == ""
    assert article.url == ""
    assert article.source == "unknown"


def test_a_nonsense_timestamp_is_dropped_rather_than_raising():
    assert _to_article({"headline": "Bad stamp", "datetime": 10**20}) is None


def test_non_dict_rows_are_filtered_out():
    assert _articles([{"headline": "ok", "datetime": 1}, "junk", None]) == [
        {"headline": "ok", "datetime": 1}
    ]


# --- Tavily /search ---------------------------------------------------------

SEARCH_BODY = {
    "query": "stock market today",
    "answer": "Equities fell as inflation came in hotter than expected.",
    "results": [
        {
            "title": "Stocks slump as inflation reaccelerates",
            "url": "https://example.test/macro-1",
            "content": "Major indices closed lower.",
            "score": 0.94,
            "published_date": "2026-08-22T18:04:00Z",
        },
        {
            "title": "Fed signals a longer hold",
            "url": "https://example.test/macro-2",
            "content": "Officials struck a hawkish tone.",
            "score": 0.88,
            "published_date": None,
        },
    ],
}


def test_a_normal_search_result_maps_cleanly():
    result = _to_result(SEARCH_BODY["results"][0])
    assert result is not None
    assert result.title == "Stocks slump as inflation reaccelerates"
    assert result.score == pytest.approx(0.94)
    assert result.published_at == datetime(2026, 8, 22, 18, 4, tzinfo=UTC)


def test_a_null_published_date_is_tolerated():
    result = _to_result(SEARCH_BODY["results"][1])
    assert result is not None and result.published_at is None


def test_a_result_without_a_title_is_dropped():
    assert _to_result({"url": "https://example.test/x", "content": "orphan"}) is None


@pytest.mark.parametrize(
    "raw",
    ["", "   ", None, 12345, "not-a-date", "2026-13-45T00:00:00Z"],
)
def test_unparseable_published_dates_become_none_rather_than_raising(raw):
    assert _parse_published(raw) is None


def test_a_naive_timestamp_is_assumed_utc():
    parsed = _parse_published("2026-08-22T18:04:00")
    assert parsed is not None and parsed.tzinfo is not None


def test_a_missing_score_is_none_not_zero():
    """Zero would sort as 'least relevant' rather than 'unknown'."""
    result = _to_result({"title": "No score", "url": "u", "content": "c"})
    assert result is not None and result.score is None

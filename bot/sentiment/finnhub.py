"""
Finnhub News API client (fallback provider).

Fetches recent news articles for a given symbol using the Finnhub
company news endpoint.  Requires FINNHUB_API_KEY environment variable.

Rate limit: 60 requests/minute on the free tier.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import requests

from bot.utils.logger import get_logger

log = get_logger("sentiment")

_BASE_URL = "https://finnhub.io/api/v1/company-news"
_TIMEOUT = 10  # seconds


def fetch_news(symbol: str, days: int = 1, limit: int = 20) -> list[dict]:
    """
    Fetch recent news articles for *symbol* from Finnhub.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. "AAPL").
    days:
        Look back this many days for articles.
    limit:
        Maximum number of articles to return.

    Returns
    -------
    list[dict]
        Each dict has keys: ``title``, ``summary``, ``created_at``, ``source``.
        Returns an empty list on error or missing credentials.
    """
    api_key = os.getenv("FINNHUB_API_KEY", "")

    if not api_key:
        log.debug("Finnhub API key not configured — skipping news fetch")
        return []

    today = date.today()
    from_date = today - timedelta(days=days)

    try:
        resp = requests.get(
            _BASE_URL,
            params={
                "symbol": symbol.upper(),
                "from": from_date.isoformat(),
                "to": today.isoformat(),
                "token": api_key,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log.warning("Finnhub news fetch failed", symbol=symbol, error=str(exc))
        return []

    if not isinstance(data, list):
        return []

    articles = []
    for item in data[:limit]:
        articles.append({
            "title": item.get("headline", ""),
            "summary": item.get("summary", ""),
            "created_at": item.get("datetime", ""),
            "source": item.get("source", "finnhub"),
        })

    log.debug("Finnhub news fetched", symbol=symbol, count=len(articles))
    return articles

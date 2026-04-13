"""
Alpaca News API client.

Fetches recent news articles for a given symbol using the Alpaca
News API v2.  Requires ALPACA_API_KEY and ALPACA_API_SECRET
environment variables.

Rate limit: 200 requests/minute on the free tier.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

import requests

from bot.utils.logger import get_logger

log = get_logger("sentiment")

_BASE_URL = "https://data.alpaca.markets/v1beta1/news"
_TIMEOUT = 10  # seconds


def fetch_news(symbol: str, hours: int = 24, limit: int = 20) -> list[dict]:
    """
    Fetch recent news articles for *symbol* from Alpaca.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. "AAPL").
    hours:
        Look back this many hours for articles.
    limit:
        Maximum number of articles to return.

    Returns
    -------
    list[dict]
        Each dict has keys: ``title``, ``summary``, ``created_at``, ``source``.
        Returns an empty list on error or missing credentials.
    """
    api_key = os.getenv("ALPACA_API_KEY", "")
    api_secret = os.getenv("ALPACA_API_SECRET", "")

    if not api_key or not api_secret:
        log.debug("Alpaca credentials not configured — skipping news fetch")
        return []

    start = (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).isoformat()

    try:
        resp = requests.get(
            _BASE_URL,
            params={
                "symbols": symbol.upper(),
                "start": start,
                "limit": limit,
                "sort": "DESC",
            },
            headers={
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        log.warning("Alpaca news fetch failed", symbol=symbol, error=str(exc))
        return []

    articles = []
    for item in data.get("news", []):
        articles.append({
            "title": item.get("headline", ""),
            "summary": item.get("summary", ""),
            "created_at": item.get("created_at", ""),
            "source": item.get("source", "alpaca"),
        })

    log.debug("Alpaca news fetched", symbol=symbol, count=len(articles))
    return articles

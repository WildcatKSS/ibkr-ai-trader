"""
News & sentiment analysis module.

Fetches recent news articles for a symbol and computes a sentiment score
from -1.0 (very bearish) to +1.0 (very bullish).

Providers:
    - Alpaca News API (primary)
    - Finnhub News API (fallback)

Usage::

    from bot.sentiment import get_sentiment

    score = get_sentiment("AAPL")
    # Returns float in [-1.0, 1.0] or 0.0 if no data
"""

from bot.sentiment.scorer import get_sentiment

__all__ = ["get_sentiment"]

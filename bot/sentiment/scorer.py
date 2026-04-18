"""
Sentiment scoring from news articles.

Uses simple keyword-based sentiment analysis on article titles and
summaries.  Articles are weighted by recency (newer = higher weight).

The score is aggregated to a float in [-1.0, 1.0]:
    -1.0 = very bearish
     0.0 = neutral / no data
    +1.0 = very bullish
"""

from __future__ import annotations

from datetime import datetime, timezone

from bot.utils.logger import get_logger

log = get_logger("sentiment")

# Keyword lists for simple sentiment classification.
# Each keyword gets a weight.  Matching is case-insensitive on the
# concatenation of title + summary.

_BULLISH_KEYWORDS: list[tuple[str, float]] = [
    ("beat", 0.3),
    ("beats", 0.3),
    ("exceeded", 0.3),
    ("surpass", 0.3),
    ("upgrade", 0.4),
    ("upgraded", 0.4),
    ("bullish", 0.5),
    ("rally", 0.3),
    ("surge", 0.4),
    ("soar", 0.4),
    ("record high", 0.5),
    ("all-time high", 0.5),
    ("strong earnings", 0.4),
    ("revenue growth", 0.3),
    ("outperform", 0.3),
    ("buy rating", 0.4),
    ("positive", 0.2),
    ("momentum", 0.2),
    ("breakout", 0.3),
    ("profit", 0.2),
    ("growth", 0.2),
    ("optimism", 0.3),
    ("gain", 0.2),
    ("rise", 0.2),
    ("up ", 0.1),
]

_BEARISH_KEYWORDS: list[tuple[str, float]] = [
    ("miss", 0.3),
    ("misses", 0.3),
    ("downgrade", 0.4),
    ("downgraded", 0.4),
    ("bearish", 0.5),
    ("plunge", 0.4),
    ("crash", 0.5),
    ("sell-off", 0.4),
    ("selloff", 0.4),
    ("decline", 0.3),
    ("warning", 0.3),
    ("weak earnings", 0.4),
    ("revenue miss", 0.4),
    ("underperform", 0.3),
    ("sell rating", 0.4),
    ("negative", 0.2),
    ("loss", 0.2),
    ("cut", 0.2),
    ("drop", 0.2),
    ("fall", 0.2),
    ("down ", 0.1),
    ("layoff", 0.3),
    ("lawsuit", 0.2),
    ("investigation", 0.2),
    ("recall", 0.3),
    ("bankruptcy", 0.5),
    ("default", 0.4),
]


def score_article(title: str, summary: str) -> float:
    """
    Score a single article.

    Returns a float in [-1.0, 1.0].
    """
    text = f"{title} {summary}".lower()

    bull_score = 0.0
    bear_score = 0.0

    for keyword, weight in _BULLISH_KEYWORDS:
        if keyword in text:
            bull_score += weight

    for keyword, weight in _BEARISH_KEYWORDS:
        if keyword in text:
            bear_score += weight

    total = bull_score + bear_score
    if total == 0:
        return 0.0

    # Net score normalized to [-1, 1]
    raw = (bull_score - bear_score) / total
    return max(-1.0, min(1.0, raw))


def score_articles(articles: list[dict]) -> float:
    """
    Compute a weighted sentiment score from a list of articles.

    Recent articles get higher weight.  Each article dict should have
    keys ``title``, ``summary``, and optionally ``created_at``.

    Returns a float in [-1.0, 1.0] or 0.0 if no articles.
    """
    if not articles:
        return 0.0

    now = datetime.now(tz=timezone.utc)
    weighted_sum = 0.0
    total_weight = 0.0

    for article in articles:
        title = article.get("title", "")
        summary = article.get("summary", "")

        # Recency weight: articles from the last hour get weight 1.0,
        # 6 hours ago gets 0.5, 24 hours ago gets ~0.2.
        recency_weight = _recency_weight(article.get("created_at"), now)

        article_score = score_article(title, summary)
        weighted_sum += article_score * recency_weight
        total_weight += recency_weight

    if total_weight == 0:
        return 0.0

    result = weighted_sum / total_weight
    return max(-1.0, min(1.0, result))


def _recency_weight(created_at: str | int | None, now: datetime) -> float:
    """Compute a decay weight based on article age.  Returns 0.0–1.0."""
    if not created_at:
        return 0.0  # unknown age → discard (fail-closed)

    try:
        if isinstance(created_at, (int, float)):
            # Unix timestamp (Finnhub)
            ts = datetime.fromtimestamp(created_at, tz=timezone.utc)
        else:
            # ISO string (Alpaca)
            ts = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))

        hours_ago = max(0, (now - ts).total_seconds() / 3600)
        # Exponential decay: half-life of ~6 hours
        weight = 2 ** (-hours_ago / 6)
        return max(0.1, min(1.0, weight))
    except (ValueError, TypeError, OSError):
        return 0.0  # unparseable date → discard (fail-closed)


def get_sentiment(symbol: str) -> float:
    """
    Get the current sentiment score for *symbol*.

    Tries Alpaca first, falls back to Finnhub.

    Returns a float in [-1.0, 1.0] or 0.0 if no data is available.
    """
    from bot.sentiment.alpaca import fetch_news as alpaca_fetch
    from bot.sentiment.finnhub import fetch_news as finnhub_fetch

    articles = alpaca_fetch(symbol)

    if not articles:
        articles = finnhub_fetch(symbol)

    if not articles:
        log.debug("No news articles found", symbol=symbol)
        return 0.0

    score = score_articles(articles)
    log.info(
        "Sentiment score computed",
        symbol=symbol,
        score=round(score, 3),
        articles=len(articles),
    )
    return score

"""
Tests for bot/sentiment/ — scorer, alpaca client, finnhub client.

All HTTP calls are mocked — no real API requests are made.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from bot.sentiment.scorer import (
    score_article,
    score_articles,
    get_sentiment,
    _recency_weight,
)


# ---------------------------------------------------------------------------
# score_article
# ---------------------------------------------------------------------------


class TestScoreArticle:
    def test_bullish_article(self):
        score = score_article("AAPL beats earnings", "Revenue growth exceeded expectations")
        assert score > 0

    def test_bearish_article(self):
        score = score_article("Stock crash after downgrade", "Analysts issue sell rating")
        assert score < 0

    def test_neutral_article(self):
        score = score_article("Company announces new product", "Details pending")
        assert score == 0.0

    def test_mixed_signals(self):
        score = score_article("Revenue growth but earnings miss", "Mixed results")
        # Should still return a valid score in [-1, 1]
        assert -1.0 <= score <= 1.0

    def test_empty_strings(self):
        assert score_article("", "") == 0.0

    def test_score_bounded(self):
        # Even with many keywords, score stays in [-1, 1]
        heavy_bull = "beat surge rally upgrade bullish record high outperform buy rating"
        score = score_article(heavy_bull, heavy_bull)
        assert -1.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# score_articles
# ---------------------------------------------------------------------------


class TestScoreArticles:
    def test_empty_list(self):
        assert score_articles([]) == 0.0

    def test_single_bullish_article(self):
        articles = [{"title": "Stock surges on earnings beat", "summary": "Strong growth"}]
        score = score_articles(articles)
        assert score > 0

    def test_single_bearish_article(self):
        articles = [{"title": "Stock crashes after downgrade", "summary": "Sell rating"}]
        score = score_articles(articles)
        assert score < 0

    def test_recency_weighting(self):
        """Recent articles should have more influence."""
        now = datetime.now(tz=timezone.utc)
        recent = {
            "title": "Stock surges",
            "summary": "bullish rally",
            "created_at": now.isoformat(),
        }
        old = {
            "title": "Stock crashes",
            "summary": "bearish selloff",
            "created_at": (now - timedelta(hours=48)).isoformat(),
        }
        # Recent bullish + old bearish → should lean bullish
        score = score_articles([recent, old])
        assert score > 0

    def test_score_bounded(self):
        articles = [
            {"title": "beat surge rally", "summary": "bullish"},
            {"title": "crash plunge decline", "summary": "bearish"},
        ]
        score = score_articles(articles)
        assert -1.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# _recency_weight
# ---------------------------------------------------------------------------


class TestRecencyWeight:
    def test_recent_article_high_weight(self):
        now = datetime.now(tz=timezone.utc)
        w = _recency_weight(now.isoformat(), now)
        assert w > 0.9

    def test_old_article_low_weight(self):
        now = datetime.now(tz=timezone.utc)
        old = (now - timedelta(hours=48)).isoformat()
        w = _recency_weight(old, now)
        assert w < 0.3

    def test_unix_timestamp(self):
        now = datetime.now(tz=timezone.utc)
        w = _recency_weight(int(now.timestamp()), now)
        assert w > 0.9

    def test_none_returns_default(self):
        now = datetime.now(tz=timezone.utc)
        assert _recency_weight(None, now) == 0.5

    def test_invalid_string_returns_default(self):
        now = datetime.now(tz=timezone.utc)
        assert _recency_weight("not-a-date", now) == 0.5

    def test_weight_bounded(self):
        now = datetime.now(tz=timezone.utc)
        w = _recency_weight(now.isoformat(), now)
        assert 0.1 <= w <= 1.0


# ---------------------------------------------------------------------------
# get_sentiment (integration with mocked API clients)
# ---------------------------------------------------------------------------


class TestGetSentiment:
    def test_returns_float(self):
        articles = [{"title": "Stock surges", "summary": "bullish rally"}]
        with (
            patch("bot.sentiment.alpaca.fetch_news", return_value=articles),
            patch("bot.sentiment.finnhub.fetch_news", return_value=[]),
        ):
            score = get_sentiment("AAPL")
        assert isinstance(score, float)
        assert -1.0 <= score <= 1.0

    def test_falls_back_to_finnhub(self):
        """When Alpaca returns nothing, Finnhub should be tried."""
        finnhub_articles = [{"title": "Earnings beat", "summary": "growth"}]
        with (
            patch("bot.sentiment.alpaca.fetch_news", return_value=[]),
            patch("bot.sentiment.finnhub.fetch_news", return_value=finnhub_articles),
        ):
            score = get_sentiment("AAPL")
        assert score > 0

    def test_no_data_returns_zero(self):
        with (
            patch("bot.sentiment.alpaca.fetch_news", return_value=[]),
            patch("bot.sentiment.finnhub.fetch_news", return_value=[]),
        ):
            score = get_sentiment("AAPL")
        assert score == 0.0

    def test_alpaca_preferred_over_finnhub(self):
        """If Alpaca has data, Finnhub should NOT be called."""
        alpaca_articles = [{"title": "Stock crashes", "summary": "bearish"}]
        finnhub_mock = MagicMock(return_value=[{"title": "Stock surges", "summary": "bullish"}])
        with (
            patch("bot.sentiment.alpaca.fetch_news", return_value=alpaca_articles),
            patch("bot.sentiment.finnhub.fetch_news", finnhub_mock),
        ):
            score = get_sentiment("AAPL")
        finnhub_mock.assert_not_called()
        assert score < 0


# ---------------------------------------------------------------------------
# Alpaca client
# ---------------------------------------------------------------------------


class TestAlpacaClient:
    def test_fetch_news_no_credentials(self):
        from bot.sentiment.alpaca import fetch_news

        with patch.dict("os.environ", {"ALPACA_API_KEY": "", "ALPACA_API_SECRET": ""}):
            result = fetch_news("AAPL")
        assert result == []

    def test_fetch_news_success(self):
        from bot.sentiment.alpaca import fetch_news

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "news": [
                {"headline": "Test headline", "summary": "Test summary",
                 "created_at": "2024-01-01T12:00:00Z", "source": "test"},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch.dict("os.environ", {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"}),
            patch("bot.sentiment.alpaca.requests.get", return_value=mock_response),
        ):
            result = fetch_news("AAPL")
        assert len(result) == 1
        assert result[0]["title"] == "Test headline"

    def test_fetch_news_http_error(self):
        from bot.sentiment.alpaca import fetch_news
        import requests

        with (
            patch.dict("os.environ", {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"}),
            patch("bot.sentiment.alpaca.requests.get",
                  side_effect=requests.RequestException("timeout")),
        ):
            result = fetch_news("AAPL")
        assert result == []


# ---------------------------------------------------------------------------
# Finnhub client
# ---------------------------------------------------------------------------


class TestFinnhubClient:
    def test_fetch_news_no_key(self):
        from bot.sentiment.finnhub import fetch_news

        with patch.dict("os.environ", {"FINNHUB_API_KEY": ""}):
            result = fetch_news("AAPL")
        assert result == []

    def test_fetch_news_success(self):
        from bot.sentiment.finnhub import fetch_news

        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"headline": "Finnhub headline", "summary": "Finnhub summary",
             "datetime": 1704110400, "source": "finnhub"},
        ]
        mock_response.raise_for_status = MagicMock()

        with (
            patch.dict("os.environ", {"FINNHUB_API_KEY": "key"}),
            patch("bot.sentiment.finnhub.requests.get", return_value=mock_response),
        ):
            result = fetch_news("AAPL")
        assert len(result) == 1
        assert result[0]["title"] == "Finnhub headline"

    def test_fetch_news_http_error(self):
        from bot.sentiment.finnhub import fetch_news
        import requests

        with (
            patch.dict("os.environ", {"FINNHUB_API_KEY": "key"}),
            patch("bot.sentiment.finnhub.requests.get",
                  side_effect=requests.RequestException("timeout")),
        ):
            result = fetch_news("AAPL")
        assert result == []

    def test_fetch_news_invalid_response(self):
        from bot.sentiment.finnhub import fetch_news

        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "not a list"}
        mock_response.raise_for_status = MagicMock()

        with (
            patch.dict("os.environ", {"FINNHUB_API_KEY": "key"}),
            patch("bot.sentiment.finnhub.requests.get", return_value=mock_response),
        ):
            result = fetch_news("AAPL")
        assert result == []

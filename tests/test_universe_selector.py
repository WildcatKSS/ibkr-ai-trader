"""
Tests for bot/universe/selector.py

The Anthropic client is always mocked — no real API calls are made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from bot.universe.criteria import CriteriaResult
from bot.universe.selector import Selection, _build_prompt, _fallback, _parse_response, select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(symbol: str, score: float, passes: bool = True) -> CriteriaResult:
    return CriteriaResult(
        symbol=symbol,
        score=score,
        passes_all=passes,
        price_above_ema9=passes,
        price_above_sma50=passes,
        price_above_sma200=passes,
        ema9_rising=passes,
        sma50_rising=passes,
        higher_highs_lows=passes,
        volume_confirms=passes,
        strong_candles=passes,
        small_wicks=passes,
        pullback_above_ema9=passes,
        near_resistance=False,
        has_momentum=False,
        last_price=150.0,
        avg_volume=1_000_000.0,
    )


def _make_client(response_text: str) -> MagicMock:
    """Return a mock anthropic client that returns *response_text*."""
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client.messages.create.return_value = msg
    return client


CANDIDATES = [
    _make_result("NVDA", 90.0),
    _make_result("AAPL", 80.0),
    _make_result("MSFT", 70.0),
]


# ---------------------------------------------------------------------------
# TestSelect — autonomous mode
# ---------------------------------------------------------------------------


class TestSelectAutonomous:
    def test_returns_selection_object(self):
        resp = json.dumps({"selected": ["NVDA"], "reasoning": "Best setup.",
                           "analysis": {"NVDA": "Top pick."}})
        sel = select(CANDIDATES, n=10, mode="autonomous", client=_make_client(resp))
        assert isinstance(sel, Selection)

    def test_selected_contains_one_symbol_in_autonomous(self):
        resp = json.dumps({"selected": ["NVDA"], "reasoning": "Best setup.",
                           "analysis": {}})
        sel = select(CANDIDATES, n=10, mode="autonomous", client=_make_client(resp))
        assert sel.selected == ["NVDA"]

    def test_reasoning_populated(self):
        resp = json.dumps({"selected": ["NVDA"], "reasoning": "Strong breakout.",
                           "analysis": {}})
        sel = select(CANDIDATES, n=10, mode="autonomous", client=_make_client(resp))
        assert "breakout" in sel.reasoning.lower()

    def test_mode_stored_in_result(self):
        resp = json.dumps({"selected": ["AAPL"], "reasoning": "OK.", "analysis": {}})
        sel = select(CANDIDATES, n=10, mode="autonomous", client=_make_client(resp))
        assert sel.mode == "autonomous"

    def test_all_candidates_stored(self):
        resp = json.dumps({"selected": ["NVDA"], "reasoning": ".", "analysis": {}})
        sel = select(CANDIDATES, n=10, mode="autonomous", client=_make_client(resp))
        assert len(sel.all_candidates) == len(CANDIDATES)

    def test_claude_called_once(self):
        resp = json.dumps({"selected": ["NVDA"], "reasoning": ".", "analysis": {}})
        client = _make_client(resp)
        select(CANDIDATES, n=10, mode="autonomous", client=client)
        client.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# TestSelect — approval mode
# ---------------------------------------------------------------------------


class TestSelectApproval:
    def test_approval_mode_returns_multiple_symbols(self):
        resp = json.dumps({
            "selected": ["NVDA", "AAPL", "MSFT"],
            "reasoning": "All good setups.",
            "analysis": {"NVDA": "Top.", "AAPL": "Good.", "MSFT": "OK."},
        })
        sel = select(CANDIDATES, n=10, mode="approval", client=_make_client(resp))
        assert len(sel.selected) == 3

    def test_analysis_dict_populated_in_approval(self):
        resp = json.dumps({
            "selected": ["NVDA", "AAPL"],
            "reasoning": "Review these.",
            "analysis": {"NVDA": "Best.", "AAPL": "Second."},
        })
        sel = select(CANDIDATES, n=10, mode="approval", client=_make_client(resp))
        assert "NVDA" in sel.analysis


# ---------------------------------------------------------------------------
# TestFallback
# ---------------------------------------------------------------------------


class TestFallback:
    def test_autonomous_fallback_returns_top_1(self):
        sel = _fallback(CANDIDATES, "autonomous")
        assert sel.selected == ["NVDA"]

    def test_approval_fallback_returns_all(self):
        sel = _fallback(CANDIDATES, "approval")
        assert set(sel.selected) == {"NVDA", "AAPL", "MSFT"}

    def test_fallback_on_api_error(self):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("timeout")
        sel = select(CANDIDATES, n=10, mode="autonomous", client=client)
        assert sel.selected == ["NVDA"]  # top score

    def test_fallback_on_bad_json(self):
        client = _make_client("not valid json {{{")
        sel = select(CANDIDATES, n=10, mode="autonomous", client=client)
        assert sel.selected == ["NVDA"]

    def test_no_client_returns_fallback(self):
        import unittest.mock as mock
        with mock.patch("bot.universe.selector._make_client", return_value=None):
            sel = select(CANDIDATES, n=10, mode="autonomous", client=None)
        assert isinstance(sel, Selection)

    def test_empty_candidates_returns_empty(self):
        sel = select([], n=10, mode="autonomous", client=MagicMock())
        assert sel.selected == []


# ---------------------------------------------------------------------------
# TestParseResponse
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_valid_json_parsed(self):
        raw = json.dumps({"selected": ["NVDA"], "reasoning": "Great.", "analysis": {}})
        sel = _parse_response(raw, CANDIDATES, "autonomous")
        assert sel.selected == ["NVDA"]

    def test_symbol_not_in_candidates_filtered_out(self):
        raw = json.dumps({"selected": ["UNKNOWN"], "reasoning": ".",
                          "analysis": {}})
        sel = _parse_response(raw, CANDIDATES, "autonomous")
        # UNKNOWN is not in candidates → fallback takes over
        assert sel.selected == ["NVDA"]

    def test_uppercase_normalisation(self):
        raw = json.dumps({"selected": ["nvda"], "reasoning": ".", "analysis": {}})
        sel = _parse_response(raw, CANDIDATES, "autonomous")
        assert "NVDA" in sel.selected

    def test_invalid_json_returns_fallback(self):
        sel = _parse_response("INVALID", CANDIDATES, "autonomous")
        assert sel.selected == ["NVDA"]


# ---------------------------------------------------------------------------
# TestBuildPrompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_contains_symbol_names(self):
        prompt = _build_prompt(CANDIDATES, "autonomous")
        for c in CANDIDATES:
            assert c.symbol in prompt

    def test_autonomous_prompt_mentions_single(self):
        prompt = _build_prompt(CANDIDATES, "autonomous")
        assert "SINGLE" in prompt.upper() or "single" in prompt

    def test_approval_prompt_mentions_rank(self):
        prompt = _build_prompt(CANDIDATES, "approval")
        assert "rank" in prompt.lower() or "Rank" in prompt

    def test_prompt_includes_json_instruction(self):
        prompt = _build_prompt(CANDIDATES, "autonomous")
        assert "JSON" in prompt or "json" in prompt

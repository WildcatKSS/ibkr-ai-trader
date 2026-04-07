"""
Claude-powered universe selector.

Takes the ranked list produced by ``scanner.scan`` and asks Claude to make
the final selection.  Claude is called **once per daily universe scan** — never
inside a tight loop or per-tick.

Modes
-----
autonomous  (``UNIVERSE_APPROVAL_MODE = "autonomous"``)
    Claude picks the single highest-rated symbol.  The engine proceeds to
    trade that symbol automatically without human intervention.

approval    (``UNIVERSE_APPROVAL_MODE = "approval"``)
    Claude ranks all candidates with per-symbol analysis.  The full ranked
    list is stored so the user can confirm their choice via the web dashboard
    before trading begins.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from bot.universe.criteria import CriteriaResult
from bot.utils.logger import get_logger

log = get_logger("universe")

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class Selection:
    """Output of ``select()``."""

    mode: str                              # "autonomous" or "approval"
    selected: list[str]                    # chosen symbol(s)
    reasoning: str                         # Claude's explanation
    analysis: dict[str, str] = field(default_factory=dict)  # per-symbol notes
    all_candidates: list[CriteriaResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select(
    candidates: list[CriteriaResult],
    n: int = 10,
    mode: str = "autonomous",
    client=None,  # anthropic.Anthropic — injected for testability
) -> Selection:
    """
    Use Claude to make the final selection from *candidates*.

    Parameters
    ----------
    candidates:
        Ranked list from ``scanner.scan`` (highest score first).
    n:
        Maximum number of symbols to include in the watchlist
        (``UNIVERSE_MAX_SYMBOLS``).
    mode:
        ``"autonomous"`` — return the single best symbol.
        ``"approval"``   — return the top *n* symbols for human review.
    client:
        ``anthropic.Anthropic()`` instance.  If ``None``, one is created using
        ``ANTHROPIC_API_KEY`` from the environment.

    Returns
    -------
    Selection
        Falls back to score-based ordering (no Claude call) if the API is
        unavailable or returns unparseable output.
    """
    if not candidates:
        log.warning("No candidates passed to selector — returning empty selection")
        return Selection(mode=mode, selected=[], reasoning="No candidates available.",
                         all_candidates=[])

    top = candidates[:n]

    if client is None:
        client = _make_client()

    if client is None:
        log.warning("ANTHROPIC_API_KEY not set — falling back to score-based selection")
        return _fallback(top, mode)

    prompt = _build_prompt(top, mode)

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text
        return _parse_response(raw, top, mode)
    except Exception as exc:  # noqa: BLE001
        log.error("Claude API call failed in universe selector — using fallback",
                  error=str(exc))
        return _fallback(top, mode)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _make_client():
    """Return an ``anthropic.Anthropic`` client or ``None`` if key is absent."""
    try:
        import anthropic  # local import — keeps module importable without the package
    except ImportError:
        return None
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    return anthropic.Anthropic(api_key=key)


def _build_prompt(candidates: list[CriteriaResult], mode: str) -> str:
    """Build the Claude prompt for the given mode."""
    rows = []
    for rank, c in enumerate(candidates, 1):
        core = sum([
            c.price_above_ema9, c.price_above_sma50, c.price_above_sma200,
            c.ema9_rising, c.sma50_rising, c.higher_highs_lows, c.volume_confirms,
        ])
        bonus = sum([
            c.strong_candles, c.small_wicks, c.pullback_above_ema9,
            c.near_resistance, c.has_momentum,
        ])
        flags = []
        if c.near_resistance:
            flags.append("near-resistance")
        if c.has_momentum:
            flags.append("momentum/gap")
        if c.pullback_above_ema9:
            flags.append("pullback-above-EMA9")
        rows.append(
            f"{rank}. {c.symbol:6s}  score={c.score:5.1f}  "
            f"core={core}/7  bonus={bonus}/5  "
            f"price=${c.last_price:.2f}  "
            f"{'  '.join(flags) or 'no bonus flags'}"
        )

    table = "\n".join(rows)
    passes = [c.symbol for c in candidates if c.passes_all]
    passes_str = ", ".join(passes) if passes else "none"

    if mode == "autonomous":
        instruction = (
            "Select the SINGLE best symbol for today's intraday trading session. "
            "Prioritise symbols that pass all core criteria, are near resistance "
            "(breakout candidate), or show strong momentum. "
            'Return JSON with keys "selected" (array with one symbol), '
            '"reasoning" (2–3 sentences), and "analysis" (object mapping each '
            "symbol to a one-sentence note)."
        )
    else:
        instruction = (
            f"Rank all {len(candidates)} candidates for a human trader to review. "
            "Return JSON with keys \"selected\" (array of ALL symbols in your "
            'recommended order, best first), "reasoning" (2–3 sentences about '
            'the overall market picture), and "analysis" (object mapping each '
            "symbol to a one-sentence note)."
        )

    return f"""You are a technical analysis assistant for an intraday equity trading bot.

Today's universe scan produced {len(candidates)} candidate(s).
Symbols that pass ALL core bullish criteria: {passes_str}

Ranked candidates (daily timeframe):
{table}

Core criteria (all 7 required for a fully bullish setup):
  price > EMA9, price > SMA50, price > SMA200,
  EMA9 rising, SMA50 rising, higher-highs/higher-lows structure, volume confirms

Bonus signals that improve ranking:
  strong candles (large bodies), small upper wicks (little rejection),
  pullbacks held above EMA9, near resistance (breakout imminent), gap/momentum

Task: {instruction}

Respond with valid JSON only — no markdown, no prose outside the JSON object.
"""


def _parse_response(raw: str, candidates: list[CriteriaResult], mode: str) -> Selection:
    """Parse Claude's JSON response into a ``Selection``."""
    try:
        data = json.loads(raw.strip())
        selected = [str(s).upper() for s in data.get("selected", [])]
        reasoning = str(data.get("reasoning", "")).strip()
        analysis = {str(k).upper(): str(v) for k, v in data.get("analysis", {}).items()}

        # Validate: selected symbols must be in candidates
        valid_symbols = {c.symbol for c in candidates}
        selected = [s for s in selected if s in valid_symbols]

        if not selected:
            raise ValueError("Claude returned no valid symbols")

        log.info(
            "Claude universe selection complete",
            mode=mode,
            selected=selected,
        )
        return Selection(
            mode=mode,
            selected=selected,
            reasoning=reasoning,
            analysis=analysis,
            all_candidates=candidates,
        )
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        log.warning("Failed to parse Claude response — using fallback",
                    error=str(exc), raw=raw[:200])
        return _fallback(candidates, mode)


def _fallback(candidates: list[CriteriaResult], mode: str) -> Selection:
    """Score-based fallback when Claude is unavailable or returns bad output."""
    if mode == "autonomous":
        selected = [candidates[0].symbol] if candidates else []
        reasoning = "Score-based selection (Claude unavailable)."
    else:
        selected = [c.symbol for c in candidates]
        reasoning = "Score-based ranking (Claude unavailable)."

    return Selection(
        mode=mode,
        selected=selected,
        reasoning=reasoning,
        all_candidates=candidates,
    )

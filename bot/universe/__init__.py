"""
bot/universe — daily stock/ETF universe scanner and Claude-powered selector.

Public API
----------
- ``criteria.score_candidate``   — score one symbol against all bullish criteria
- ``scanner.scan``               — scan a pool of symbols and return ranked list
- ``selector.select``            — Claude-powered final selection from ranked list
"""

from bot.universe.criteria import CriteriaConfig, CriteriaResult, score_candidate
from bot.universe.scanner import ScanConfig, scan, load_scan_config
from bot.universe.selector import Selection, select

__all__ = [
    "CriteriaConfig",
    "CriteriaResult",
    "score_candidate",
    "ScanConfig",
    "scan",
    "load_scan_config",
    "Selection",
    "select",
]

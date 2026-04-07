"""
TradingEngine — main loop for the IBKR AI intraday trading bot.

Loop cadence (approximate):
  - Every 60 s: check calendar, config, and market state.
  - Once per trading day (pre-market or first tick): run the universe scan.
  - On market open: run the signal pipeline for each symbol in the watchlist.
  - 15 min before EOD_CLOSE_MINUTES: initiate position close routine.
  - On market close / shutdown signal: stop cleanly.

Threading model:
  - This module runs in the main thread only.
  - No time.sleep() calls — uses threading.Event.wait() so that SIGTERM
    wakes the loop immediately instead of waiting out a full sleep interval.
"""

from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from typing import Any

from bot.utils.logger import get_logger

log = get_logger("ibkr")

# Set by request_shutdown() from the signal handler in __main__.py
_shutdown_event = threading.Event()


def request_shutdown() -> None:
    """Signal the engine to stop after the current tick."""
    _shutdown_event.set()


class TradingEngine:
    """
    Main trading loop.

    Parameters
    ----------
    trading_mode:
        One of "paper", "live", or "dryrun".  Validated by __main__ before
        TradingEngine is instantiated.
    tick_interval:
        Seconds between loop ticks (default 60).  Shorter values are useful
        in tests; never set to 0 in production.
    data_provider:
        Implementation of ``bot.universe.scanner.DataProvider`` used to fetch
        daily OHLCV bars for the universe scan.  If ``None``, the universe scan
        is skipped (dryrun mode or when the IBKR connection is not yet wired).
    """

    TICK_INTERVAL = 60  # seconds

    def __init__(
        self,
        trading_mode: str,
        tick_interval: int = TICK_INTERVAL,
        data_provider: Any | None = None,
    ) -> None:
        self._trading_mode = trading_mode
        self._tick_interval = tick_interval
        self._data_provider = data_provider
        self._last_scan_date: date | None = None
        self._watchlist: list[str] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block until a shutdown signal is received."""
        log.info(
            "Trading engine started",
            trading_mode=self._trading_mode,
            tick_interval=self._tick_interval,
        )

        while not _shutdown_event.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                # Log and continue — a single bad tick must not kill the bot.
                log.error("Unhandled exception in trading tick", error=str(exc))

            # Wait for the next tick or an early wakeup from a shutdown signal.
            _shutdown_event.wait(timeout=self._tick_interval)

        log.info("Trading engine stopping")
        self._on_shutdown()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """
        One iteration of the trading loop.

        All operations within a tick must be non-blocking and complete well
        within tick_interval seconds.
        """
        from bot.utils.calendar import is_market_open, is_trading_day, minutes_until_close
        from bot.utils.config import get

        now = datetime.now(tz=timezone.utc)
        today = now.date()

        if not is_trading_day():
            log.info("Not a trading day — skipping tick", date=str(today))
            return

        # Run universe scan once per trading day.
        # Triggers on the first tick of each trading day regardless of whether
        # the market is already open (e.g. bot restarted mid-session).
        if self._last_scan_date != today:
            self._scan_universe()
            self._last_scan_date = today

        # Re-read EOD threshold each tick so web UI changes take effect quickly.
        try:
            eod_minutes = int(get("EOD_CLOSE_MINUTES"))
        except (ValueError, TypeError) as exc:
            log.warning(
                "EOD_CLOSE_MINUTES unreadable — falling back to 15 min",
                error=str(exc),
            )
            eod_minutes = 15

        if not is_market_open():
            log.info("Market closed — skipping tick", timestamp=now.isoformat())
            return

        mins_left = minutes_until_close()

        if mins_left <= eod_minutes:
            log.info(
                "EOD close window — triggering position close",
                minutes_until_close=mins_left,
                eod_threshold=eod_minutes,
            )
            self._eod_close()
            return

        log.info(
            "Market open — running signal scan",
            trading_mode=self._trading_mode,
            minutes_until_close=mins_left,
            watchlist=self._watchlist,
        )
        self._run_signals()

    def _scan_universe(self) -> None:
        """
        Run the daily universe scan to build today's watchlist.

        In dryrun mode the scan is skipped and the watchlist stays empty.
        When no DataProvider is available (IBKR not yet connected) a warning
        is logged and the scan is skipped.
        """
        from bot.universe.scanner import get_pool, load_scan_config, scan
        from bot.universe.selector import select
        from bot.utils.config import get

        if self._trading_mode == "dryrun":
            log.info("Dryrun mode — universe scan skipped, watchlist stays empty")
            self._watchlist = []
            return

        if self._data_provider is None:
            log.warning(
                "No DataProvider configured — universe scan skipped. "
                "Wire an IBKR DataProvider to TradingEngine to enable scanning."
            )
            self._watchlist = []
            return

        symbols = get_pool()
        log.info("Starting universe scan", pool_size=len(symbols))

        try:
            config = load_scan_config()
            candidates = scan(symbols, self._data_provider, config)
        except Exception as exc:  # noqa: BLE001
            log.error("Universe scan failed", error=str(exc))
            self._watchlist = []
            return

        n = config.n_results
        mode = get("UNIVERSE_APPROVAL_MODE", "autonomous")

        try:
            selection = select(candidates, n=n, mode=mode)
        except Exception as exc:  # noqa: BLE001
            log.error("Universe selector failed", error=str(exc))
            self._watchlist = [c.symbol for c in candidates[:n]]
            return

        self._watchlist = selection.selected

        log.info(
            "Universe scan complete",
            mode=mode,
            watchlist=self._watchlist,
            candidates_scored=len(candidates),
            reasoning=selection.reasoning[:120] if selection.reasoning else "",
        )

    def _run_signals(self) -> None:
        """
        Placeholder for the signal pipeline.

        Full implementation lives in bot/signals/ and bot/orders/.
        This stub logs intent without placing any orders, satisfying the
        TRADING_MODE=dryrun contract.
        """
        if self._trading_mode == "dryrun":
            log.info("Dryrun mode — signal scan skipped, no orders sent")
            return

        if not self._watchlist:
            log.info("Watchlist is empty — no signals to run")
            return

        # TODO: Implement once bot/signals/generator.py and bot/orders/ are in place.
        log.info(
            "Signal scan stub — not yet implemented",
            trading_mode=self._trading_mode,
            watchlist=self._watchlist,
        )

    def _eod_close(self) -> None:
        """
        Placeholder for the EOD position-close routine.

        Full implementation lives in bot/orders/eod_close.py.
        """
        if self._trading_mode == "dryrun":
            log.info("Dryrun mode — EOD close skipped, no orders sent")
            return

        # TODO: delegate to bot.orders.eod_close once implemented.
        log.info("EOD close stub — not yet implemented", trading_mode=self._trading_mode)

    def _on_shutdown(self) -> None:
        """
        Clean-up hook called once after the loop exits.

        Must be safe to call even if startup was incomplete.
        """
        if self._trading_mode != "dryrun":
            # TODO: cancel open orders and close positions via IBKR API.
            log.info("Shutdown close stub — not yet implemented")
        log.info("Engine shutdown complete")

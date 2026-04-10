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
        from bot.utils.config import get

        if self._trading_mode == "dryrun":
            # In dryrun, use a static watchlist from config instead of the
            # full universe scan.  This allows testing the signal pipeline
            # end-to-end without needing to run the scanner.
            raw = get("DRYRUN_WATCHLIST", default="")
            symbols = [s.strip() for s in raw.split(",") if s.strip()]
            if symbols and self._data_provider is not None:
                self._watchlist = symbols
                log.info(
                    "Dryrun mode — using configured watchlist",
                    watchlist=symbols,
                )
            else:
                log.info(
                    "Dryrun mode — no watchlist configured or no data provider",
                )
                self._watchlist = []
            return

        from bot.universe.scanner import get_pool, load_scan_config, scan
        from bot.universe.selector import select

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
        Run the full signal pipeline for every symbol in the watchlist.

        For each symbol:
          1. Fetch 5-min intraday bars via the data provider.
          2. Generate a signal (indicators → LightGBM → 15-min filter → Claude).
          3. Check risk rules and compute position size.
          4. Execute the order (or log in dryrun).
          5. Send an alert on fill.
        """
        from bot.alerts.notifier import notify
        from bot.orders.executor import execute
        from bot.risk.manager import check
        from bot.signals.generator import generate
        from bot.utils.config import get

        if not self._watchlist:
            log.info("Watchlist is empty — no signals to run")
            return

        try:
            ml_min_prob = get("ML_MIN_PROBABILITY", cast=float, default=0.55)
        except Exception:
            ml_min_prob = 0.55

        # In dryrun mode we still run the pipeline so the logs are useful,
        # but no real orders are sent.
        portfolio_value = self._get_portfolio_value()

        for symbol in self._watchlist:
            try:
                self._run_signal_for_symbol(
                    symbol,
                    ml_min_prob=ml_min_prob,
                    portfolio_value=portfolio_value,
                    generate=generate,
                    check=check,
                    execute=execute,
                    notify=notify,
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Unhandled exception in signal pipeline",
                    symbol=symbol,
                    error=str(exc),
                )

    def _has_open_position(self, symbol: str) -> bool:
        """
        Return True if there is already a pending/open/filled trade for
        *symbol* today.  Prevents duplicate entries for the same symbol
        within a single trading session.
        """
        from datetime import date, datetime, timezone

        from sqlalchemy import select

        from db.models import Trade
        from db.session import get_session

        today = date.today()
        day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        try:
            with get_session() as session:
                row = session.execute(
                    select(Trade.id)
                    .where(
                        Trade.symbol == symbol,
                        Trade.status.in_(["pending", "open", "filled", "dryrun"]),
                        Trade.created_at >= day_start,
                    )
                    .limit(1)
                ).scalar_one_or_none()
            return row is not None
        except Exception as exc:
            log.warning(
                "Cannot check open positions — allowing signal",
                symbol=symbol,
                error=str(exc),
            )
            return False  # fail open

    def _run_signal_for_symbol(
        self,
        symbol: str,
        *,
        ml_min_prob: float,
        portfolio_value: float,
        generate,
        check,
        execute,
        notify,
    ) -> None:
        """Run the pipeline for a single symbol."""
        # Guard: skip if we already entered a position today for this symbol.
        if self._has_open_position(symbol):
            log.debug(
                "Skipping signal — position already exists today",
                symbol=symbol,
            )
            return

        # Fetch intraday bars
        if self._data_provider is None:
            log.warning("No data provider — cannot fetch bars", symbol=symbol)
            return

        bars = self._data_provider.fetch_intraday_bars(symbol, n_bars=200)
        if bars is None or len(bars) == 0:
            log.warning("No intraday bars returned", symbol=symbol)
            return

        # Generate signal
        signal = generate(symbol, bars, ml_min_probability=ml_min_prob)

        if signal.action == "no_trade":
            log.debug("No signal", symbol=symbol, reason=signal.explanation)
            return

        log.info(
            "Signal generated",
            symbol=symbol,
            action=signal.action,
            entry=signal.entry_price,
            target=signal.target_price,
            stop=signal.stop_price,
            confidence=round(signal.confidence, 3),
            ml_label=signal.ml_label,
            ml_prob=round(signal.ml_probability, 3),
            confirmed_15min=signal.confirmed_15min,
        )

        # Risk check
        decision = check(signal, portfolio_value, trading_mode=self._trading_mode)

        if not decision.approved:
            log.info(
                "Signal rejected by risk manager",
                symbol=symbol,
                reason=decision.reason,
            )
            return

        # Execute
        result = execute(
            signal,
            decision,
            trading_mode=self._trading_mode,
            broker=getattr(self._data_provider, "broker", None),
        )

        if result.success:
            notify("trade_opened", {
                "symbol": symbol,
                "action": signal.action,
                "shares": decision.shares,
                "fill_price": result.fill_price,
                "target_price": signal.target_price,
                "stop_price": signal.stop_price,
                "trading_mode": self._trading_mode,
                "explanation": signal.explanation,
            })

    def _eod_close(self) -> None:
        """Close all open positions before market close."""
        from bot.alerts.notifier import notify
        from bot.orders.eod_close import close_all_positions

        broker = getattr(self._data_provider, "broker", None) if self._data_provider else None

        results = close_all_positions(broker, trading_mode=self._trading_mode)

        for r in results:
            if r["success"]:
                notify("trade_closed", {
                    "symbol": r["symbol"],
                    "exit_price": r.get("fill_price"),
                    "pnl": None,
                    "trading_mode": self._trading_mode,
                })

    def _get_portfolio_value(self) -> float:
        """Return current portfolio NAV; falls back to 100 000 on error."""
        try:
            broker = getattr(self._data_provider, "broker", None)
            if broker and hasattr(broker, "get_portfolio_value"):
                return float(broker.get_portfolio_value())
        except Exception as exc:
            log.warning("Cannot get portfolio value", error=str(exc))
        return 100_000.0

    def _on_shutdown(self) -> None:
        """
        Clean-up hook called once after the loop exits.

        Must be safe to call even if startup was incomplete.
        """
        if self._trading_mode != "dryrun":
            log.info("Shutdown: closing any remaining open positions")
            self._eod_close()
        log.info("Engine shutdown complete")

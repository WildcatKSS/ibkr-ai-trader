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
    broker_factory:
        Callable that returns a new ``IBKRConnection`` (or ``None``).  Used by
        the hot-reload logic to create or tear down broker connections when
        ``TRADING_MODE`` is changed via the web UI at runtime.
    """

    TICK_INTERVAL = 60  # seconds

    def __init__(
        self,
        trading_mode: str,
        tick_interval: int = TICK_INTERVAL,
        data_provider: Any | None = None,
        broker_factory: Any | None = None,
    ) -> None:
        self._trading_mode = trading_mode
        if tick_interval < 0:
            raise ValueError(f"tick_interval must be >= 0, got {tick_interval}")
        self._tick_interval = tick_interval
        self._data_provider = data_provider
        self._broker_factory = broker_factory
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
        from bot.utils.calendar import is_market_open, is_trading_day, market_open, minutes_until_close
        from bot.utils.config import get

        # ── Hot-reload: detect TRADING_MODE changes from web UI ──────────
        try:
            current_mode = get("TRADING_MODE", default="dryrun")
            if current_mode in ("paper", "live", "dryrun") and current_mode != self._trading_mode:
                self._handle_mode_change(current_mode)
        except Exception as exc:  # noqa: BLE001
            log.warning("Cannot read TRADING_MODE — keeping current", error=str(exc))

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

        # Wait for the opening-bell volatility to subside.
        try:
            buffer_min = int(get("MARKET_OPEN_BUFFER_MINUTES", default="5"))
        except (ValueError, TypeError):
            buffer_min = 5
        if buffer_min > 0:
            open_time = market_open()
            minutes_since_open = (now - open_time).total_seconds() / 60
            if minutes_since_open < buffer_min:
                log.info(
                    "Market open buffer — skipping tick",
                    minutes_since_open=round(minutes_since_open, 1),
                    buffer=buffer_min,
                )
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

        # In approval mode the watchlist stays empty until the user decides.
        # Check for an approved row each tick so the moment the user clicks
        # "Approve" the engine picks the symbol up without needing a restart.
        if get("UNIVERSE_APPROVAL_MODE", "autonomous") == "approval" and not self._watchlist:
            approved = self._load_approved_selection()
            if approved:
                self._watchlist = [approved]
                log.info("Approved symbol loaded", symbol=approved)
            else:
                log.info("Approval mode — awaiting user selection, skipping tick")
                return

        log.info(
            "Market open — running signal scan",
            trading_mode=self._trading_mode,
            minutes_until_close=mins_left,
            watchlist=self._watchlist,
        )
        self._run_signals()

    def _handle_mode_change(self, new_mode: str) -> None:
        """
        React to a ``TRADING_MODE`` change made via the web UI.

        Transitions:
        - paper/live → dryrun: close open positions, disconnect broker.
        - dryrun → paper/live: connect broker (requires IBKR running).
        - paper ↔ live: close positions, reconnect broker (port may differ).

        After any transition the watchlist is cleared so a fresh universe
        scan runs on the next tick.
        """
        old_mode = self._trading_mode
        log.info(
            "Trading mode changed via web UI",
            old_mode=old_mode,
            new_mode=new_mode,
        )

        # Close positions if leaving a live-trading mode.
        if old_mode in ("paper", "live"):
            log.info("Closing open positions before mode switch")
            self._eod_close()

        # Tear down the existing broker connection.
        if self._data_provider is not None and hasattr(self._data_provider, "disconnect"):
            try:
                self._data_provider.disconnect()
            except Exception as exc:  # noqa: BLE001
                log.warning("Error disconnecting broker", error=str(exc))
            self._data_provider = None

        # Establish a new connection if the new mode needs one.
        if new_mode in ("paper", "live"):
            if self._broker_factory is not None:
                try:
                    broker = self._broker_factory()
                    broker.connect()
                    self._data_provider = broker
                    log.info("Broker connected for new mode", mode=new_mode)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "Cannot connect broker for new mode — "
                        "falling back to dryrun",
                        mode=new_mode,
                        error=str(exc),
                    )
                    new_mode = "dryrun"
                    self._data_provider = None
            else:
                log.error(
                    "No broker factory — cannot switch to paper/live, "
                    "staying in dryrun",
                )
                new_mode = "dryrun"
        elif new_mode == "dryrun" and self._broker_factory is not None:
            # Optionally reconnect for data-only in dryrun.
            try:
                broker = self._broker_factory()
                broker.connect()
                self._data_provider = broker
                log.info("Dryrun: broker connected for data only")
            except Exception:  # noqa: BLE001
                log.info("Dryrun: running without IBKR data")
                self._data_provider = None

        self._trading_mode = new_mode
        self._watchlist = []
        self._last_scan_date = None  # force a fresh universe scan

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

        if mode == "approval":
            # Persist the full ranked list so the user can review and approve
            # a single symbol from the web UI.  Trading is gated on their
            # decision — _run_signals checks _load_approved_selection().
            self._persist_selection(selection, candidates, status="pending_approval")
            self._watchlist = []
            log.info(
                "Universe scan complete — awaiting approval",
                mode=mode,
                candidates_scored=len(candidates),
                top_candidate=candidates[0].symbol if candidates else None,
            )
            return

        # autonomous mode: trade the first symbol immediately.
        self._watchlist = selection.selected
        self._persist_selection(selection, candidates, status="autonomous")

        log.info(
            "Universe scan complete",
            mode=mode,
            watchlist=self._watchlist,
            candidates_scored=len(candidates),
            reasoning=selection.reasoning[:120] if selection.reasoning else "",
        )

    def _persist_selection(
        self,
        selection: Any,
        candidates: list,
        *,
        status: str,
    ) -> None:
        """Write (or overwrite) today's UniverseSelection row."""
        from datetime import date

        from sqlalchemy import select as sa_select

        from db.models import UniverseSelection
        from db.session import get_session

        today = date.today()
        serialised = [
            {
                "symbol": c.symbol,
                "score": float(c.score),
                "passes_all_core": bool(c.passes_all),
                "near_resistance": bool(c.near_resistance),
                "has_momentum": bool(c.has_momentum),
                "pullback_above_ema9": bool(c.pullback_above_ema9),
                "analysis": selection.analysis.get(c.symbol, ""),
            }
            for c in candidates
        ]
        selected = (
            selection.selected[0]
            if status == "autonomous" and selection.selected
            else None
        )
        now = datetime.now(tz=timezone.utc)

        try:
            with get_session() as session:
                row = session.scalars(
                    sa_select(UniverseSelection)
                    .where(UniverseSelection.scan_date == today)
                    .limit(1)
                ).first()
                if row is None:
                    row = UniverseSelection(
                        scan_date=today,
                        candidates=serialised,
                        selected_symbol=selected,
                        status=status,
                        reasoning=selection.reasoning,
                        created_at=now,
                    )
                    session.add(row)
                elif row.status in ("pending_approval", "autonomous"):
                    # Only overwrite rows that haven't been decided by a user.
                    row.candidates = serialised
                    row.selected_symbol = selected
                    row.status = status
                    row.reasoning = selection.reasoning
                    row.created_at = now
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to persist universe selection", error=str(exc))

    def _load_approved_selection(self) -> str | None:
        """
        Return the approved symbol for today, or None.

        When the engine runs in approval mode and no row is approved yet,
        this returns None and the signal scan is skipped for this tick.
        """
        from datetime import date

        from sqlalchemy import select as sa_select

        from db.models import UniverseSelection
        from db.session import get_session

        today = date.today()
        try:
            with get_session() as session:
                row = session.scalars(
                    sa_select(UniverseSelection)
                    .where(UniverseSelection.scan_date == today)
                    .limit(1)
                ).first()
            if row is None:
                return None
            if row.status == "approved" and row.selected_symbol:
                return row.selected_symbol
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("Cannot read universe selection", error=str(exc))
            return None

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

        # Gap filter: skip symbols with excessive overnight gaps.
        try:
            gap_max = float(get("GAP_FILTER_MAX_PCT", default="3.0"))
        except (ValueError, TypeError):
            gap_max = 3.0
        if gap_max > 0 and hasattr(bars.index, "date") and len(bars) >= 2:
            dates = bars.index.date
            today = dates[-1]
            prev_mask = dates < today
            today_mask = dates == today
            if prev_mask.any() and today_mask.any():
                prev_close = float(bars.loc[prev_mask, "close"].iloc[-1])
                today_open = float(bars.loc[today_mask, "open"].iloc[0])
                if prev_close > 0:
                    gap_pct = abs(today_open - prev_close) / prev_close * 100
                    if gap_pct > gap_max:
                        log.info(
                            "Gap filter — skipping symbol",
                            symbol=symbol,
                            gap_pct=round(gap_pct, 2),
                            max_pct=gap_max,
                        )
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
        self._send_daily_summary()
        log.info("Engine shutdown complete")

    def _send_daily_summary(self) -> None:
        """Send a daily P&L summary alert at shutdown."""
        try:
            from datetime import date, datetime, timezone

            from sqlalchemy import func, select

            from bot.alerts.notifier import notify
            from db.models import Trade
            from db.session import get_session

            today = date.today()
            day_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)

            with get_session() as session:
                rows = session.execute(
                    select(Trade.pnl)
                    .where(Trade.status == "closed", Trade.closed_at >= day_start)
                ).all()

            if not rows:
                return  # no trades today — nothing to summarise

            pnls = [r[0] for r in rows if r[0] is not None]
            if not pnls:
                return

            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            notify("daily_summary", {
                "trade_count": len(pnls),
                "wins": len(wins),
                "losses": len(losses),
                "total_pnl": round(sum(pnls), 2),
                "trading_mode": self._trading_mode,
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to send daily summary", error=str(exc))

"""
Entry point for the trading bot process.

Invoked by systemd as:
    python -m bot.core

Responsibilities:
  1. Install SIGTERM/SIGINT handlers for graceful shutdown.
  2. Validate TRADING_MODE before doing anything else.
  3. Run the main loop via TradingEngine.run().
  4. Drain the async logger on exit.
"""

import os
import signal
import sys

from dotenv import load_dotenv

from bot.utils.logger import get_logger, shutdown as logger_shutdown

# Idempotent — systemd already sets env vars via EnvironmentFile, but
# load_dotenv() is needed for development outside systemd.
load_dotenv()

log = get_logger("ibkr")


def _handle_signal(signum, frame):
    log.info("Shutdown signal received", signal=signum)
    from bot.core.engine import request_shutdown
    from bot.orders.executor import poll_interrupt
    request_shutdown()
    poll_interrupt.set()  # wake up any blocked fill-poll loop immediately


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    from bot.utils.config import ConfigError, get

    try:
        # Default to "dryrun" when the key is absent (e.g. before first seed).
        # A ConfigError here means the database itself is unreachable — abort.
        trading_mode = get("TRADING_MODE", default="dryrun")
    except ConfigError as exc:
        log.error("Cannot connect to database — aborting", error=str(exc))
        logger_shutdown()
        sys.exit(1)

    if trading_mode not in ("paper", "live", "dryrun"):
        log.error(
            "Invalid TRADING_MODE — must be paper, live, or dryrun",
            trading_mode=trading_mode,
        )
        logger_shutdown()
        sys.exit(1)

    log.info("Bot starting", trading_mode=trading_mode)

    # ── Create IBKR connection ───────────────────────────────────────────
    from bot.core.broker import IBKRConnection

    broker = None

    if trading_mode in ("paper", "live"):
        # Paper/live: IBKR connection is mandatory.
        port = int(os.getenv("IBKR_PORT", "7497"))
        broker = IBKRConnection(port=port)
        try:
            broker.connect()
        except Exception as exc:
            log.error(
                "Cannot connect to IBKR — aborting",
                error=str(exc),
                port=port,
            )
            logger_shutdown()
            sys.exit(1)
    elif trading_mode == "dryrun":
        # Dryrun: IBKR connection is optional (for data only, no orders).
        port_str = os.getenv("IBKR_PORT", "")
        if port_str:
            port = int(port_str)
            broker = IBKRConnection(port=port)
            try:
                broker.connect()
                log.info("Dryrun: IBKR connected for data only", port=port)
            except Exception as exc:
                log.warning(
                    "Dryrun: IBKR not available — running without data",
                    error=str(exc),
                )
                broker = None

    # ── Broker factory for hot-reload mode switching ────────────────────
    ibkr_port = int(os.getenv("IBKR_PORT", "7497")) if os.getenv("IBKR_PORT") else None

    def _broker_factory() -> IBKRConnection | None:
        """Create a new IBKRConnection.  Used by the engine when the user
        switches TRADING_MODE via the web UI at runtime."""
        if ibkr_port is None:
            return None
        return IBKRConnection(port=ibkr_port)

    # ── Run engine ───────────────────────────────────────────────────────
    from bot.core.engine import TradingEngine

    engine = TradingEngine(
        trading_mode=trading_mode,
        data_provider=broker,
        broker_factory=_broker_factory if ibkr_port is not None else None,
    )
    try:
        engine.run()
    finally:
        # Disconnect whatever broker the engine is currently using.
        current_provider = engine._data_provider
        if current_provider is not None and hasattr(current_provider, "disconnect"):
            try:
                current_provider.disconnect()
            except Exception:
                pass
        log.info("Bot stopped")
        logger_shutdown()


if __name__ == "__main__":
    main()

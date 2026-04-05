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

import signal
import sys

from bot.utils.logger import get_logger, shutdown as logger_shutdown

log = get_logger("ibkr")


def _handle_signal(signum, frame):
    log.info("Shutdown signal received", signal=signum)
    # The engine checks _shutdown_requested on each tick.
    from bot.core.engine import request_shutdown
    request_shutdown()


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    from bot.utils.config import ConfigError, get

    try:
        trading_mode = get("TRADING_MODE")
    except ConfigError as exc:
        log.error("Cannot read TRADING_MODE from database — aborting", error=str(exc))
        sys.exit(1)

    if trading_mode not in ("paper", "live", "dryrun"):
        log.error(
            "Invalid TRADING_MODE — must be paper, live, or dryrun",
            trading_mode=trading_mode,
        )
        sys.exit(1)

    log.info("Bot starting", trading_mode=trading_mode)

    from bot.core.engine import TradingEngine

    engine = TradingEngine(trading_mode=trading_mode)
    try:
        engine.run()
    finally:
        log.info("Bot stopped")
        logger_shutdown()


if __name__ == "__main__":
    main()

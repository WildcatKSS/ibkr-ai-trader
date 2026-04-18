"""
Tests for bot/core/engine.py and bot/core/__main__.py.

Rules:
  - No real IBKR connection.
  - No real Claude API calls.
  - No .env values — all config mocked.
"""

from __future__ import annotations

import threading
from datetime import date
from unittest.mock import ANY, MagicMock, call, patch

import pytest

import bot.core.engine as engine_module
from bot.core.engine import TradingEngine, request_shutdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(trading_mode: str = "dryrun", tick_interval: int = 0) -> TradingEngine:
    """Create an engine with a zero tick interval for instant loop turnaround."""
    return TradingEngine(trading_mode=trading_mode, tick_interval=tick_interval)


def _reset_shutdown():
    """Clear the module-level shutdown event between tests."""
    engine_module._shutdown_event.clear()


@pytest.fixture(autouse=True)
def reset_shutdown_event():
    _reset_shutdown()
    yield
    _reset_shutdown()


# ---------------------------------------------------------------------------
# request_shutdown
# ---------------------------------------------------------------------------


class TestRequestShutdown:
    def test_sets_event(self):
        assert not engine_module._shutdown_event.is_set()
        request_shutdown()
        assert engine_module._shutdown_event.is_set()


# ---------------------------------------------------------------------------
# TradingEngine.__init__
# ---------------------------------------------------------------------------


class TestInit:
    def test_stores_trading_mode(self):
        e = _make_engine("paper")
        assert e._trading_mode == "paper"

    def test_stores_tick_interval(self):
        e = TradingEngine(trading_mode="dryrun", tick_interval=30)
        assert e._tick_interval == 30

    def test_default_tick_interval(self):
        e = TradingEngine(trading_mode="dryrun")
        assert e._tick_interval == TradingEngine.TICK_INTERVAL


# ---------------------------------------------------------------------------
# TradingEngine.run — loop control
# ---------------------------------------------------------------------------


class TestRun:
    def test_exits_when_shutdown_set(self):
        """Engine must exit run() once shutdown is requested."""
        e = _make_engine(tick_interval=0)
        # Request shutdown before starting — loop should exit immediately.
        request_shutdown()
        e.run()  # must return, not hang

    def test_tick_called_before_shutdown(self):
        e = _make_engine(tick_interval=0)
        tick_calls = []

        original_tick = e._tick

        def capturing_tick():
            tick_calls.append(1)
            request_shutdown()  # shut down after first tick

        e._tick = capturing_tick
        e.run()
        assert len(tick_calls) == 1

    def test_exception_in_tick_does_not_kill_loop(self):
        """A single bad tick must not crash the engine."""
        e = _make_engine(tick_interval=0)
        call_count = [0]

        def bad_tick():
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated tick failure")
            request_shutdown()

        e._tick = bad_tick
        e.run()  # must not raise
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# TradingEngine._tick — calendar / market checks
# ---------------------------------------------------------------------------


class TestTick:
    def _call_tick(self, trading_day=True, market_open=True, mins_left=120, eod_minutes="15"):
        e = _make_engine()
        with (
            patch("bot.utils.calendar.is_trading_day", return_value=trading_day),
            patch("bot.utils.calendar.is_market_open", return_value=market_open),
            patch("bot.utils.calendar.minutes_until_close", return_value=mins_left),
            patch("bot.utils.config.get", return_value=eod_minutes),
            patch.object(e, "_scan_universe"),
            patch.object(e, "_run_signals") as mock_signals,
            patch.object(e, "_eod_close") as mock_eod,
        ):
            e._tick()
        return mock_signals, mock_eod

    def test_skips_on_non_trading_day(self):
        mock_signals, mock_eod = self._call_tick(trading_day=False)
        mock_signals.assert_not_called()
        mock_eod.assert_not_called()

    def test_skips_when_market_closed(self):
        mock_signals, mock_eod = self._call_tick(market_open=False)
        mock_signals.assert_not_called()
        mock_eod.assert_not_called()

    def test_calls_eod_close_within_threshold(self):
        mock_signals, mock_eod = self._call_tick(mins_left=10, eod_minutes="15")
        mock_eod.assert_called_once()
        mock_signals.assert_not_called()

    def test_calls_signals_outside_eod_threshold(self):
        mock_signals, mock_eod = self._call_tick(mins_left=60, eod_minutes="15")
        mock_signals.assert_called_once()
        mock_eod.assert_not_called()

    def test_at_exact_eod_threshold_triggers_close(self):
        # mins_left == eod_minutes → should close
        mock_signals, mock_eod = self._call_tick(mins_left=15, eod_minutes="15")
        mock_eod.assert_called_once()

    def test_bad_eod_config_falls_back_to_15(self):
        # If EOD_CLOSE_MINUTES is not an int, default to 15.
        e = _make_engine()
        with (
            patch("bot.utils.calendar.is_trading_day", return_value=True),
            patch("bot.utils.calendar.is_market_open", return_value=True),
            patch("bot.utils.calendar.minutes_until_close", return_value=10),
            patch("bot.utils.config.get", side_effect=ValueError("bad")),
            patch.object(e, "_scan_universe"),
            patch.object(e, "_run_signals") as mock_signals,
            patch.object(e, "_eod_close") as mock_eod,
        ):
            e._tick()
        mock_eod.assert_called_once()  # 10 < 15 default → EOD close


# ---------------------------------------------------------------------------
# TradingEngine._run_signals
# ---------------------------------------------------------------------------


class TestRunSignals:
    def test_dryrun_skips_orders(self):
        e = _make_engine("dryrun")
        e._run_signals()  # must not raise, must not place orders

    def test_paper_logs_stub(self):
        e = _make_engine("paper")
        e._run_signals()  # stub — must not raise

    def test_live_logs_stub(self):
        e = _make_engine("live")
        e._run_signals()  # stub — must not raise


# ---------------------------------------------------------------------------
# TradingEngine._scan_universe — dryrun watchlist
# ---------------------------------------------------------------------------


class TestScanUniverseDryrun:
    def test_dryrun_uses_configured_watchlist(self):
        """In dryrun mode with data_provider and DRYRUN_WATCHLIST, the
        engine should populate the watchlist from config."""
        mock_provider = MagicMock()
        e = TradingEngine(trading_mode="dryrun", tick_interval=0, data_provider=mock_provider)
        with patch("bot.utils.config.get", return_value="AAPL,MSFT,NVDA"):
            e._scan_universe()
        assert e._watchlist == ["AAPL", "MSFT", "NVDA"]

    def test_dryrun_empty_watchlist(self):
        """Empty DRYRUN_WATCHLIST → empty watchlist."""
        mock_provider = MagicMock()
        e = TradingEngine(trading_mode="dryrun", tick_interval=0, data_provider=mock_provider)
        with patch("bot.utils.config.get", return_value=""):
            e._scan_universe()
        assert e._watchlist == []

    def test_dryrun_no_data_provider(self):
        """Without a data provider, watchlist stays empty even if setting is set."""
        e = TradingEngine(trading_mode="dryrun", tick_interval=0, data_provider=None)
        with patch("bot.utils.config.get", return_value="AAPL,MSFT"):
            e._scan_universe()
        assert e._watchlist == []

    def test_dryrun_watchlist_trims_whitespace(self):
        """Handles spaces in the comma-separated list."""
        mock_provider = MagicMock()
        e = TradingEngine(trading_mode="dryrun", tick_interval=0, data_provider=mock_provider)
        with patch("bot.utils.config.get", return_value=" SPY , AAPL , "):
            e._scan_universe()
        assert e._watchlist == ["SPY", "AAPL"]


# ---------------------------------------------------------------------------
# TradingEngine._handle_mode_change — hot-reload via web UI
# ---------------------------------------------------------------------------


class TestHandleModeChange:
    def test_dryrun_to_paper_connects_broker(self):
        """Switching from dryrun to paper should connect a new broker."""
        mock_broker = MagicMock()
        factory = MagicMock(return_value=mock_broker)
        e = TradingEngine(
            trading_mode="dryrun", tick_interval=0, broker_factory=factory,
        )
        e._handle_mode_change("paper")

        assert e._trading_mode == "paper"
        factory.assert_called_once()
        mock_broker.connect.assert_called_once()
        assert e._data_provider is mock_broker
        assert e._watchlist == []
        assert e._last_scan_date is None

    def test_paper_to_dryrun_closes_positions_and_disconnects(self):
        """Switching from paper to dryrun should close positions and disconnect."""
        mock_broker = MagicMock()
        mock_broker.disconnect = MagicMock()
        e = TradingEngine(
            trading_mode="paper", tick_interval=0, data_provider=mock_broker,
        )
        with patch.object(e, "_eod_close") as mock_eod:
            e._handle_mode_change("dryrun")

        mock_eod.assert_called_once()
        mock_broker.disconnect.assert_called_once()
        assert e._trading_mode == "dryrun"

    def test_paper_to_live_reconnects(self):
        """Switching between paper and live should reconnect the broker."""
        old_broker = MagicMock()
        new_broker = MagicMock()
        factory = MagicMock(return_value=new_broker)
        e = TradingEngine(
            trading_mode="paper", tick_interval=0,
            data_provider=old_broker, broker_factory=factory,
        )
        with patch.object(e, "_eod_close"):
            e._handle_mode_change("live")

        old_broker.disconnect.assert_called_once()
        factory.assert_called_once()
        new_broker.connect.assert_called_once()
        assert e._trading_mode == "live"
        assert e._data_provider is new_broker

    def test_broker_connect_failure_falls_back_to_dryrun(self):
        """If broker connection fails during switch, fall back to dryrun."""
        mock_broker = MagicMock()
        mock_broker.connect.side_effect = ConnectionError("refused")
        factory = MagicMock(return_value=mock_broker)
        e = TradingEngine(
            trading_mode="dryrun", tick_interval=0, broker_factory=factory,
        )
        e._handle_mode_change("paper")

        assert e._trading_mode == "dryrun"  # fell back
        assert e._data_provider is None

    def test_no_broker_factory_stays_dryrun(self):
        """Without a broker factory, cannot switch to paper/live."""
        e = TradingEngine(trading_mode="dryrun", tick_interval=0)
        e._handle_mode_change("paper")

        assert e._trading_mode == "dryrun"  # couldn't switch
        assert e._data_provider is None

    def test_mode_change_resets_watchlist(self):
        """After mode change, watchlist and scan date are reset."""
        mock_broker = MagicMock()
        factory = MagicMock(return_value=mock_broker)
        e = TradingEngine(
            trading_mode="dryrun", tick_interval=0, broker_factory=factory,
        )
        e._watchlist = ["AAPL", "MSFT"]
        e._last_scan_date = date.today()

        e._handle_mode_change("paper")
        assert e._watchlist == []
        assert e._last_scan_date is None

    def test_tick_detects_mode_change(self):
        """_tick should detect when TRADING_MODE differs and call _handle_mode_change."""
        e = TradingEngine(trading_mode="dryrun", tick_interval=0)
        with (
            patch("bot.utils.config.get", return_value="paper"),
            patch("bot.utils.calendar.is_trading_day", return_value=False),
            patch.object(e, "_handle_mode_change") as mock_handle,
            patch.object(e, "_scan_universe"),
        ):
            e._tick()
        mock_handle.assert_called_once_with("paper")

    def test_tick_same_mode_no_change(self):
        """_tick should not call _handle_mode_change when mode hasn't changed."""
        e = TradingEngine(trading_mode="dryrun", tick_interval=0)
        with (
            patch("bot.utils.config.get", return_value="dryrun"),
            patch("bot.utils.calendar.is_trading_day", return_value=False),
            patch.object(e, "_handle_mode_change") as mock_handle,
            patch.object(e, "_scan_universe"),
        ):
            e._tick()
        mock_handle.assert_not_called()


# ---------------------------------------------------------------------------
# TradingEngine._eod_close
# ---------------------------------------------------------------------------


class TestEodClose:
    def test_dryrun_skips_orders(self):
        e = _make_engine("dryrun")
        e._eod_close()  # must not raise

    def test_paper_logs_stub(self):
        e = _make_engine("paper")
        e._eod_close()  # stub — must not raise


# ---------------------------------------------------------------------------
# __main__ — startup validation
# ---------------------------------------------------------------------------


class TestMain:
    def test_invalid_trading_mode_exits(self):
        with (
            patch("bot.utils.config.get", return_value="invalid"),
            pytest.raises(SystemExit) as exc_info,
        ):
            from bot.core.__main__ import main
            main()
        assert exc_info.value.code == 1

    def test_config_error_exits(self):
        from bot.utils.config import ConfigError

        with (
            patch("bot.utils.config.get", side_effect=ConfigError("no db")),
            pytest.raises(SystemExit) as exc_info,
        ):
            from bot.core.__main__ import main
            main()
        assert exc_info.value.code == 1

    def test_valid_mode_starts_engine(self):
        request_shutdown()  # pre-set so run() exits immediately
        mock_engine = MagicMock()
        with (
            patch("bot.utils.config.get", return_value="dryrun"),
            patch("bot.core.engine.TradingEngine", return_value=mock_engine),
            patch("bot.utils.logger.shutdown"),
            patch.dict("os.environ", {"IBKR_PORT": ""}, clear=False),
        ):
            from bot.core.__main__ import main
            main()  # must not raise or hang
        mock_engine.run.assert_called_once()

    def test_missing_trading_mode_defaults_to_dryrun(self):
        """When TRADING_MODE is absent from DB, default to dryrun (not abort)."""
        from bot.utils.config import ConfigError

        request_shutdown()
        mock_engine = MagicMock()

        def get_side_effect(key, *, default=None, cast=str):
            if key == "TRADING_MODE":
                return default  # simulates missing key with default="dryrun"
            raise ConfigError("unexpected")

        with (
            patch("bot.utils.config.get", side_effect=get_side_effect),
            patch("bot.core.engine.TradingEngine", return_value=mock_engine),
            patch("bot.utils.logger.shutdown"),
            patch.dict("os.environ", {"IBKR_PORT": ""}, clear=False),
        ):
            from bot.core.__main__ import main
            main()
        mock_engine.run.assert_called_once()

    def test_dryrun_with_ibkr_port_connects(self):
        """Dryrun mode with IBKR_PORT set attempts broker connection."""
        request_shutdown()
        mock_engine = MagicMock()
        mock_broker = MagicMock()

        with (
            patch("bot.utils.config.get", return_value="dryrun"),
            patch("bot.core.engine.TradingEngine", return_value=mock_engine) as MockTE,
            patch("bot.utils.logger.shutdown"),
            patch("bot.core.broker.IBKRConnection", return_value=mock_broker),
            patch.dict("os.environ", {"IBKR_PORT": "7497"}, clear=False),
        ):
            from bot.core.__main__ import main
            main()
        mock_broker.connect.assert_called_once()
        # Verify engine received the broker as data_provider and a factory.
        call_kwargs = MockTE.call_args.kwargs
        assert call_kwargs["trading_mode"] == "dryrun"
        assert call_kwargs["data_provider"] is mock_broker
        assert call_kwargs["broker_factory"] is not None

    def test_dryrun_without_ibkr_port_no_broker(self):
        """Dryrun mode without IBKR_PORT skips broker creation."""
        request_shutdown()
        mock_engine = MagicMock()

        with (
            patch("bot.utils.config.get", return_value="dryrun"),
            patch("bot.core.engine.TradingEngine", return_value=mock_engine) as MockTE,
            patch("bot.utils.logger.shutdown"),
            patch.dict("os.environ", {"IBKR_PORT": ""}, clear=False),
        ):
            from bot.core.__main__ import main
            main()
        call_kwargs = MockTE.call_args.kwargs
        assert call_kwargs["trading_mode"] == "dryrun"
        assert call_kwargs["data_provider"] is None
        assert call_kwargs["broker_factory"] is None

    def test_paper_mode_requires_ibkr(self):
        """Paper mode requires IBKR connection; failure aborts."""
        mock_broker = MagicMock()
        mock_broker.connect.side_effect = ConnectionError("refused")

        with (
            patch("bot.utils.config.get", return_value="paper"),
            patch("bot.core.broker.IBKRConnection", return_value=mock_broker),
            patch("bot.utils.logger.shutdown"),
            patch.dict("os.environ", {"IBKR_PORT": "7497"}, clear=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            from bot.core.__main__ import main
            main()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# TradingEngine._has_open_position
# ---------------------------------------------------------------------------


class TestHasOpenPosition:
    def test_no_trade_returns_false(self):
        e = _make_engine()
        with patch("db.session.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_session.__enter__ = lambda s: mock_session
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value.scalar_one_or_none.return_value = None
            mock_gs.return_value = mock_session
            assert e._has_open_position("AAPL") is False

    def test_existing_trade_returns_true(self):
        e = _make_engine()
        with patch("db.session.get_session") as mock_gs:
            mock_session = MagicMock()
            mock_session.__enter__ = lambda s: mock_session
            mock_session.__exit__ = MagicMock(return_value=False)
            mock_session.execute.return_value.scalar_one_or_none.return_value = 42
            mock_gs.return_value = mock_session
            assert e._has_open_position("AAPL") is True

    def test_db_error_returns_true(self):
        """On DB error, fail closed (block signal to prevent double entries)."""
        e = _make_engine()
        with patch("db.session.get_session", side_effect=Exception("db down")):
            assert e._has_open_position("AAPL") is True


class TestGetPortfolioValue:
    def test_returns_none_when_no_broker(self):
        e = _make_engine()
        assert e._get_portfolio_value() is None

    def test_returns_none_on_broker_error(self):
        mock_provider = MagicMock()
        mock_provider.broker.get_portfolio_value.side_effect = RuntimeError("API down")
        e = TradingEngine(trading_mode="dryrun", tick_interval=0, data_provider=mock_provider)
        assert e._get_portfolio_value() is None

    def test_returns_value_on_success(self):
        mock_provider = MagicMock()
        mock_provider.broker.get_portfolio_value.return_value = 50000.0
        e = TradingEngine(trading_mode="dryrun", tick_interval=0, data_provider=mock_provider)
        assert e._get_portfolio_value() == 50000.0

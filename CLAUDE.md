# IBKR AI Trader — Claude Code Instructions

This file gives Claude Code persistent context about this project.
Read it fully before making any changes to the codebase.

-----

## Project Overview

Autonomous intraday trading bot for Interactive Brokers.
Stack: Python · FastAPI · MariaDB · Nginx · Ubuntu Server.
AI layer: LightGBM (signals) + Claude API (reasoning/explainability).
All positions are intraday only — no overnight exposure ever.

-----

## Commands

```bash
# Activate virtualenv (always required before running anything)
source /opt/ibkr-trader/venv/bin/activate

# Start services
systemctl start ibkr-bot ibkr-web

# Stop services
systemctl stop ibkr-bot ibkr-web

# View live logs
journalctl -u ibkr-bot -f
journalctl -u ibkr-web -f

# Run database migrations
alembic upgrade head

# Seed default configuration
python db/seed.py

# Run tests
pytest tests/

# Update from GitHub (SSH default; HTTPS: GITHUB_TOKEN=ghp_xxx sudo bash deploy/update.sh)
sudo bash deploy/update.sh

# Uninstall the bot from the server (interactive, asks per component)
sudo bash deploy/uninstall.sh

# Run backtesting engine
python -m bot.backtesting.engine --instrument AAPL --start 2024-01-01 --end 2024-12-31 --timeframe 5min

# Retrain LightGBM model manually
python -m bot.ml.trainer --retrain

# Roll back to a previous model version
python -m bot.ml.versioning --rollback <version>
```

-----

## Repository Structure

```
bot/core/        Trading loop (engine.py), SIGTERM handler (__main__.py)
bot/universe/    Daily scanner (scanner.py), criteria scoring (criteria.py), Claude selector (selector.py)
bot/signals/     Technical indicators (indicators.py), 15-min filter + Claude signal (generator.py)
bot/ml/          Feature engineering (features.py), LightGBM singleton (model.py),
                 version manifest (versioning.py), training pipeline (trainer.py), models/
bot/risk/        Circuit breaker + position sizing (manager.py)
bot/orders/      IBKRBroker protocol + fill monitoring (executor.py), EOD close (eod_close.py)
bot/backtesting/ Historical simulation engine — not yet implemented
bot/alerts/      Email + webhook notifications (notifier.py)
bot/utils/       Logger (logger.py), NYSE calendar (calendar.py), config loader (config.py)

web/api/         FastAPI backend: main.py, auth.py — routes not yet fully implemented
web/frontend/    Browser-based management dashboard — not yet implemented

db/              SQLAlchemy models: LogEntry, Setting, Trade (models.py)
                 Alembic migrations (migrations/), seed data (seed.py), session (session.py)
deploy/          Server scripts: setup.sh, update.sh, uninstall.sh
                 systemd/: ibkr-bot.service, ibkr-web.service
logs/            Rotating log files per category
```

-----

## Architecture Rules — NEVER violate these

- **No overnight positions** — every code path that opens a position must be reachable by `eod_close.py`. Never bypass the EOD routine.
- **TRADING_MODE must be checked before every order** — always validate `TRADING_MODE` (`paper` / `live` / `dryrun`) before sending anything to IBKR. In `dryrun` mode, log the intended order but send nothing.
- **Never read secrets from anywhere except `.env`** — API keys and passwords are in `.env` only. Never hardcode, never read from DB, never log them.
- **All configuration comes from MariaDB** — operational settings are stored in DB and managed via the web interface. Never read `settings.yaml` (it does not exist). Use `bot/utils/config.py` to load settings.
- **Claude API is for reasoning only** — never use Claude API for real-time tick processing or inside tight loops. Claude is called once per signal (after LightGBM + 15-min filter) and once per universe scan.
- **LightGBM runs locally** — signal generation must not depend on any external API call. The model file is loaded from `bot/ml/models/`.

-----

## Environment Variables

`setup.sh` generates `/opt/ibkr-trader/.env` automatically. These variables must be present. Never add secrets anywhere else.

| Variable            | Set by   | Description                                                             |
|---------------------|----------|-------------------------------------------------------------------------|
| `ANTHROPIC_API_KEY` | You      | Anthropic API key                                                       |
| `IBKR_PORT`         | You      | TWS paper: 7497 · IB GW paper: 4002 · TWS live: 7496 · IB GW live: 4001 |
| `ALPACA_API_KEY`    | You      | Alpaca News API key                                                     |
| `ALPACA_API_SECRET` | You      | Alpaca News API secret                                                  |
| `FINNHUB_API_KEY`   | You      | Finnhub API key (fallback news provider)                                |
| `SMTP_PASSWORD`     | You      | SMTP password or app password for email alerts                          |
| `WEB_PASSWORD`      | You      | Admin password for the web dashboard login (JWT-authenticated)          |
| `DB_PASSWORD`       | setup.sh | Generated automatically                                                 |
| `SECRET_KEY`        | setup.sh | Session signing key; generated automatically                            |
| `DOMAIN`            | setup.sh | Domain name entered during setup for Certbot                            |

`TRADING_MODE` and `EOD_CLOSE_MINUTES` are operational settings — configure them via the web interface under **Settings → Trading**, not in `.env`.

All other operational settings (risk parameters, position sizing, universe selection, etc.) are configured via the web interface and stored in MariaDB — never in `.env`.

-----

## Daily Universe Scan — exact order

Runs **once per trading day** (first engine tick of the day, before or at market open).

1. `bot/universe/scanner.py` — fetch daily OHLCV bars for each symbol in `UNIVERSE_POOL`
2. `bot/universe/criteria.py` — score each symbol (0–100) against bullish criteria
3. `bot/universe/selector.py` — Claude API call to make final selection

Result:
- **autonomous mode** (`UNIVERSE_APPROVAL_MODE=autonomous`): top-1 symbol is traded automatically.
- **approval mode** (`UNIVERSE_APPROVAL_MODE=approval`): ranked watchlist stored; user picks via web dashboard before trading starts.

### Universe criteria (daily timeframe)

**Moving averages**
- 9 EMA — short-term momentum (`UNIVERSE_EMA9_PERIOD`)
- 50 SMA — trend confirmation (`UNIVERSE_SMA50_PERIOD`)
- 200 SMA — macro trend filter (`UNIVERSE_SMA200_PERIOD`)

**Core bullish criteria — ALL must pass (scores 75 pts total)**

| Criterion | Setting | Points |
|---|---|---|
| Price > 9 EMA | — | 10 |
| Price > 50 SMA | — | 10 |
| Price > 200 SMA | — | 10 |
| 9 EMA rising | — | 10 |
| 50 SMA rising | — | 10 |
| Higher highs + higher lows | `UNIVERSE_HH_HL_LOOKBACK` | 15 |
| Volume above average & rising on green candles | `UNIVERSE_VOLUME_MA_PERIOD` | 10 |

**Bonus criteria — improve ranking (35 pts total)**

| Criterion | Setting | Points |
|---|---|---|
| Strong bullish candles (large bodies) | `UNIVERSE_BODY_RATIO_MIN` | 5 |
| Small upper wicks (little rejection) | `UNIVERSE_WICK_RATIO_MAX` | 5 |
| Pullbacks hold above 9 EMA | — | 5 |
| Near resistance (breakout imminent) | `UNIVERSE_NEAR_RESISTANCE_PCT` | 10 |
| Momentum / gap-up | `UNIVERSE_MOMENTUM_GAP_PCT`, `UNIVERSE_MOMENTUM_5D_RETURN_PCT` | 10 |

A symbol needs score > 0 to appear in the watchlist; `passes_all=True` requires all 7 core criteria.
The watchlist size is configured via `UNIVERSE_MAX_SYMBOLS` (default 10).

Never skip or reorder the universe scan steps.

-----

## Signal Pipeline — exact order

Runs intraday **for each symbol in the watchlist** during market hours.

1. `bot/signals/indicators.py` — calculate ta indicators on 5-min candles
2. `bot/ml/model.py` — LightGBM prediction (long / short / no trade)
3. `bot/signals/generator.py` — 15-min confirmation filter (both timeframes must agree)
4. Claude API call — context, sentiment, final decision, entry/target/stop, explanation
5. `bot/risk/manager.py` — risk check and position sizing
6. `bot/orders/executor.py` — place order via IBKR API

Never skip or reorder these steps.

-----

## ML Module — `bot/ml/`

### Files

| File | Purpose |
|---|---|
| `bot/ml/features.py` | `build(df)` → 24-column feature DataFrame from an enriched OHLCV frame |
| `bot/ml/model.py` | Thread-safe singleton; `predict(features)` → `Prediction(label, probability)` |
| `bot/ml/versioning.py` | Version manifest (`version.json`), `register_version()`, `rollback()` |
| `bot/ml/trainer.py` | `train(df)` → trains LightGBM, saves `.lgbm` file, registers version |
| `bot/ml/models/` | Model files (`.lgbm`); never committed to git |

### Labels

For each bar *t*, the forward return over `ML_FORWARD_BARS` (default 6 = 30 min) is computed:

    forward_return = log(close[t + forward_bars] / close[t])

| Class | Value | Condition |
|---|---|---|
| `no_trade` | 0 | default |
| `long` | 1 | forward_return ≥ ML_LONG_THRESHOLD_PCT / 100 |
| `short` | 2 | forward_return ≤ −ML_SHORT_THRESHOLD_PCT / 100 |

### 24 Feature Names

`rsi`, `stoch_k`, `stoch_d`, `macd_hist`, `adx`, `adx_pos`, `adx_neg`,
`ema_cross`, `bb_pct`, `bb_width`, `bb_squeeze`, `atr_pct`, `mfi`,
`obv_slope`, `volume_ratio`, `close_vs_ema9`, `close_vs_ema21`,
`close_vs_vwap`, `body_ratio`, `upper_wick_ratio`, `lower_wick_ratio`,
`return_1bar`, `return_3bar`, `return_6bar`

### Operational settings (configured via web interface, stored in DB)

| Setting | Default | Description |
|---|---|---|
| `ML_FORWARD_BARS` | 6 | Bars ahead for forward-return label |
| `ML_LONG_THRESHOLD_PCT` | 0.3 | Min return % for long label |
| `ML_SHORT_THRESHOLD_PCT` | 0.3 | Min drop % for short label |
| `ML_MIN_PROBABILITY` | 0.55 | Min predicted probability to act on signal |

### Rules

- `bot/ml/` never calls the Claude API — that belongs in `bot/signals/generator.py`
- The model singleton is loaded lazily on first `predict()` call; call `reload_model()` after retraining
- `predict()` returns `("no_trade", 0.0)` when no model is loaded or features contain NaN
- Retrain via CLI: `python -m bot.ml.trainer --retrain --data path/to/data.csv`
- Roll back via CLI: `python -m bot.ml.versioning --rollback <version>`

-----

## Logging Rules

Every module uses `bot/utils/logger.py` — never use `print()` or the standard `logging` module directly.

### Architecture

The logger uses a two-layer write strategy to keep the trading loop fast:

1. **Disk (synchronous, primary)** — every log entry is written immediately to a rotating file on disk via a `RotatingFileHandler`. This is always the first write and never blocks on DB availability.
2. **MariaDB (asynchronous, secondary)** — a background daemon thread drains an in-process `queue.Queue` and flushes records to MariaDB. If the DB is unavailable or the queue is full, the record is silently skipped from DB (it is already on disk). The trading loop is never blocked waiting for a DB write.

```
log.info(...)
    │
    ├─► RotatingFileHandler  →  logs/<category>.log    (synchronous, immediate)
    ├─► RotatingFileHandler  →  logs/errors.log        (ERROR+ only, synchronous)
    └─► _AsyncDbHandler      →  queue → worker thread → MariaDB  (non-blocking)
```

Never call `session.add()` or any DB operation directly inside a log handler — use the async queue.

### Usage

```python
from bot.utils.logger import get_logger

log = get_logger("trading")
log.info("Order placed", order_id=4821, symbol="AAPL", qty=10, price=174.50)
log.error("Fill timeout", order_id=4821, elapsed_sec=62)
```

Keyword arguments are stored as structured fields in MariaDB and appended as `key=value` pairs to the disk log line.

### Shutdown

Call `bot.utils.logger.shutdown()` once during application shutdown to drain the async queue before the process exits.

### Category table

Each module logs to its own category:

| Module             | Category    |
|--------------------|-------------|
| `bot/universe/`    | `universe`  |
| `bot/signals/`     | `signals`   |
| `bot/ml/`          | `ml`        |
| `bot/risk/`        | `risk`      |
| `bot/orders/`      | `trading`   |
| `bot/alerts/`      | `trading`   |
| `bot/core/`        | `ibkr`      |
| `web/api/`         | `web`       |
| Claude API calls   | `claude`    |
| Sentiment analysis | `sentiment` |

All ERROR and CRITICAL entries are also written to `logs/errors.log` automatically by the logger.

-----

## Database Rules

- Use SQLAlchemy ORM — never raw SQL strings
- All schema changes go through Alembic migrations in `db/migrations/`
- Never modify migration files after they have been applied
- The `db/seed.py` script sets default values for all operational settings — update it when adding new settings

-----

## Testing Rules

- Write a test for every new function in `tests/`
- Tests must never connect to real IBKR or call the real Claude API — use mocks
- Tests must never depend on `.env` values — use test fixtures
- Run `pytest tests/` before committing

-----

## Git Rules

- Branch naming: `feature/`, `fix/`, `refactor/` prefixes
- Never commit `.env` or any file containing secrets
- Never commit model files (`*.lgbm`) — they are generated locally
- Commit messages in English, present tense: "Add fill monitor timeout logic"

-----

## What Claude Should Not Do

- Do not modify `db/migrations/` files that already exist
- Do not add `print()` statements — use the logger
- Do not add any `time.sleep()` calls in the trading loop — use async patterns
- Do not change `TRADING_MODE` logic without updating all three paths: paper, live, dryrun
- Do not make Claude API calls from inside `bot/ml/` — that module is ML only
- Do not add dependencies without adding them to `requirements.txt`
- Technical indicators use the `ta` library (replaces the abandoned `pandas-ta`) — ensure it is in `requirements.txt` and imported only via `bot/signals/indicators.py` (intraday) and `bot/universe/criteria.py` (daily)
- The NYSE trading calendar uses the `exchange_calendars` library (the maintained fork of the abandoned `trading_calendars`) — ensure it is in `requirements.txt` and imported via `bot/utils/calendar.py` only
- Do not add universe criteria logic outside `bot/universe/criteria.py` — all scoring weights and thresholds live there and are configurable via DB settings
- Do not call Claude API inside `bot/universe/scanner.py` or `bot/universe/criteria.py` — the Claude call belongs exclusively in `bot/universe/selector.py`
- The `DataProvider` protocol (`bot/universe/scanner.py`) must be injected — never instantiate an IBKR connection directly inside the universe package
- The `IBKRBroker` protocol (`bot/orders/executor.py`) must be injected — never import `ib_insync` directly inside `bot/orders/`
- Do not add circuit breaker or position sizing logic outside `bot/risk/manager.py`
- Do not add signal pipeline logic outside `bot/signals/generator.py` — the 15-min confirmation filter and Claude call both live there
- `bot/orders/eod_close.py` must always be reachable from every code path that opens a position — this is the no-overnight-positions guarantee

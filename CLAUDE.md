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
bot/core/        Trading loop, IBKR connection, watchdog & reconnect logic, dry run mode
bot/universe/    Daily stock/ETF scanner and Claude-powered selector
bot/signals/     LightGBM → 15-min confirmation → Claude pipeline
bot/ml/          LightGBM model, trainer, feature engineering, versioning, A/B test
bot/risk/        Position sizing, gap filter, circuit breaker
bot/orders/      Order executor, fill monitor, EOD close routine
bot/backtesting/ Historical simulation engine
bot/alerts/      Email and webhook notifications
bot/utils/       Logger, NYSE calendar, config loader

web/api/         FastAPI backend and all route handlers
web/frontend/    Browser-based management dashboard

db/              SQLAlchemy models, Alembic migrations, seed data
deploy/          Nginx config, systemd services, setup.sh
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
| `DB_PASSWORD`       | setup.sh | Generated automatically                                                 |
| `SECRET_KEY`        | setup.sh | Session signing key; generated automatically                            |
| `DOMAIN`            | setup.sh | Domain name entered during setup for Certbot                            |

`TRADING_MODE` and `EOD_CLOSE_MINUTES` are operational settings — configure them via the web interface under **Settings → Trading**, not in `.env`.

All other operational settings (risk parameters, position sizing, universe selection, etc.) are configured via the web interface and stored in MariaDB — never in `.env`.

-----

## Signal Pipeline — exact order

1. `bot/signals/indicators.py` — calculate pandas-ta indicators on 5-min candles
1. `bot/ml/model.py` — LightGBM prediction (long / short / no trade)
1. `bot/signals/generator.py` — 15-min confirmation filter (both timeframes must agree)
1. Claude API call — context, sentiment, final decision, entry/target/stop, explanation
1. `bot/risk/manager.py` — risk check and position sizing
1. `bot/orders/executor.py` — place order via IBKR API

Never skip or reorder these steps.

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
- The NYSE trading calendar uses the `trading_calendars` library — ensure it is in `requirements.txt` and imported via `bot/utils/calendar.py` only

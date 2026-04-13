# IBKR AI Trader

An open-source intraday trading bot for Interactive Brokers, powered by Claude AI. Focused exclusively on **stocks and ETFs** — positions are opened and closed within the same trading day, with no overnight exposure.

**Contributions are welcome** — see [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

-----

## Features

### Core trading

- **Autonomous trading** — The bot places, manages, and closes trades independently without human intervention
- **Intraday only** — All positions are closed before market close; no overnight risk
- **Long and short** — Supports both long and short intraday positions; short selling requires an IBKR margin account
- **Paper & live trading** — Switch seamlessly between simulation and live execution
- **Dry run mode** — Full signal and order generation without submitting orders to IBKR; no paper account required
- **Market calendar aware** — The bot automatically skips NYSE holidays and early-close days using a built-in trading calendar

### Intelligence

- **Universe selection** — Claude autonomously scans and selects the most promising stocks & ETFs for intraday trading, with configurable autonomous or human-approval mode
- **AI-powered trading signals** — LightGBM generates intraday signals on 5-min candles, confirmed on 15-min timeframe, with Claude providing reasoning and context
- **News & sentiment analysis** — Real-time news from Alpaca and Finnhub is scored for market sentiment and fed into the signal pipeline
- **Explainability** — Every automated action, including universe selection, is explained in plain language by Claude and logged

### Risk & safety

- **Risk management** — Programmatic risk checks before every order: daily loss limit, consecutive loss limit, and position size caps
- **Position sizing model** — Configurable capital allocation per trade using fixed percentage, fixed amount, or Kelly Criterion with hard per-instrument capital limits
- **Circuit breaker** — Automatically halts trading when drawdown or loss thresholds are exceeded
- **Gap protection** — Instruments with extreme expected opening gaps are flagged or excluded during universe selection
- **Order fill monitoring** — Unfilled limit orders are automatically cancelled or converted to market orders after a configurable timeout

### Model & backtesting

- **Backtesting** — Test strategies against historical market data before going live, with Sharpe ratio, max drawdown, win rate, and equity curve
- **Model version control** — Every LightGBM retrain is versioned and stored; roll back to any previous model via CLI
- **A/B model testing** — Run a new model version in paper trading alongside the live model before promoting it to production *(not yet implemented)*

### Monitoring & interface

- **Performance dashboard** — P&L charts, Sharpe ratio, win rate, max drawdown, and more via the React web interface
- **Cost dashboard** — Real-time overview of Claude API costs, IBKR commissions, and net P&L after all costs *(not yet implemented)*
- **Comprehensive logging** — DEBUG-level logs across all categories, written to disk and MariaDB
- **Alerting** — Real-time notifications via email on critical events
- **Webhook support** — Push events to any external system via configurable HTTP webhooks
- **Trade export** — Download full trade history as CSV or Excel from the web interface *(not yet implemented)*
- **Configuration audit trail** — Every change made via the web interface is logged with timestamp and user *(not yet implemented)*
- **Web dashboard** — React management interface with login, dashboard, trade history, performance charts, settings, and backtesting UI
- **Web API** — 12 REST API endpoints for status, settings, trades, performance, portfolio, logs, and backtesting
- **Security** — HTTPS via Let’s Encrypt, JWT authentication with rate limiting

-----

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Ubuntu Server                         │
│                                                              │
│   Browser ──► Nginx ──► React (static) + FastAPI (API)       │
│                                              │               │
│                          ┌───────────────────┤               │
│                          ▼                   ▼               │
│                     MariaDB            Python Bot Core       │
│                   (trades, logs,             │               │
│                    config)          ┌────────┼────────┐      │
│                                     ▼        ▼        ▼      │
│                              IBKR API   Claude API   News    │
│                            (TWS/IB GW) (universe,   APIs     │
│                                         signals)   (Alpaca,  │
│                                                    Finnhub)  │
└──────────────────────────────────────────────────────────────┘
```

-----

## Tech Stack

|Layer               |Technology                                              |
|--------------------|--------------------------------------------------------|
|OS                  |Ubuntu Server 22.04 LTS                                 |
|Web server          |Nginx (reverse proxy)                                   |
|Backend API         |FastAPI (Python)                                        |
|Database            |MariaDB + SQLAlchemy + Alembic (incl. all configuration)|
|Trading API         |IBKR TWS API via `ib_insync`                            |
|AI                  |Anthropic Claude API                                    |
|News & sentiment    |Alpaca News API (primary) / Finnhub (fallback)          |
|Signal model        |LightGBM (5-min candles, locally hosted)                |
|Technical indicators|ta                                                      |
|Historical data     |IBKR Historical Data API (backtesting)                  |
|Frontend            |React 18 + TypeScript + Tailwind CSS + Vite             |
|Charts              |Recharts (equity curves, P&L)                           |
|Process mgmt        |systemd                                                 |

-----

## Repository Structure

```
ibkr-ai-trader/
├── bot/
│   ├── core/
│   │   ├── engine.py               # Main trading loop + universe scan + signal dispatch
│   │   ├── broker.py               # IBKR connection via ib_insync (data + orders)
│   │   └── __main__.py             # Process entry point (SIGTERM, TRADING_MODE)
│   ├── universe/
│   │   ├── scanner.py              # Daily OHLCV scan + DataProvider protocol
│   │   ├── criteria.py             # Bullish criteria scoring (75 core + 35 bonus pts)
│   │   └── selector.py             # Claude-powered final instrument selection
│   ├── signals/
│   │   ├── indicators.py           # Technical indicators via ta (5-min candles)
│   │   └── generator.py            # 15-min confirmation filter + Claude signal
│   ├── ml/
│   │   ├── features.py             # 24-feature engineering from enriched OHLCV
│   │   ├── model.py                # Thread-safe LightGBM singleton (predict / reload)
│   │   ├── versioning.py           # Version manifest, register, rollback CLI
│   │   ├── trainer.py              # Training pipeline + forward-return labelling + CLI
│   │   └── models/                 # .lgbm model files (gitignored, .gitkeep present)
│   ├── risk/
│   │   └── manager.py              # Circuit breaker + position sizing (fixed/Kelly)
│   ├── orders/
│   │   ├── executor.py             # IBKRBroker protocol, order execution, fill monitoring
│   │   └── eod_close.py            # End-of-day close all positions
│   ├── alerts/
│   │   └── notifier.py             # Email (SMTP/TLS) + HTTP webhook alerts
│   ├── backtesting/
│   │   ├── engine.py               # Historical bar replay with simulated execution
│   │   ├── metrics.py              # Sharpe, drawdown, win rate, profit factor
│   │   └── results.py              # Result dataclass and JSON serialisation
│   ├── sentiment/
│   │   ├── alpaca.py               # Alpaca News API v2 client (primary)
│   │   ├── finnhub.py              # Finnhub company news client (fallback)
│   │   └── scorer.py               # Keyword sentiment scoring with recency weighting
│   └── utils/
│       ├── __init__.py
│       ├── logger.py               # Disk-first, async MariaDB flush logger
│       ├── calendar.py             # NYSE trading calendar & market hours validation
│       └── config.py
├── web/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app (12 endpoints: health, status, settings, logs, trades, performance, portfolio, backtesting)
│   │   └── auth.py                 # JWT authentication + rate limiting
│   └── frontend/
│       └── src/
│           ├── App.tsx             # Root router (HashRouter with auth guard)
│           ├── api.ts              # Typed API client with token management
│           └── components/         # Login, Dashboard, TradeHistory, Performance, Settings, Backtesting, Layout
├── db/
│   ├── models.py                   # SQLAlchemy ORM models (MariaDB)
│   ├── migrations/                 # Alembic database migrations
│   └── seed.py                     # Initial data / default config
├── deploy/
│   ├── systemd/
│   │   ├── ibkr-bot.service        # Systemd service: trading bot
│   │   └── ibkr-web.service        # Systemd service: web API
│   ├── setup.sh                    # Ubuntu server setup (idempotent, 15 steps)
│   ├── update.sh                   # Update from GitHub (SSH or HTTPS token)
│   └── uninstall.sh                # Interactive full removal script
├── logs/
│   ├── trading.log                 # Order execution & trade lifecycle
│   ├── universe.log                # Universe scan, scoring & selection results
│   ├── signals.log                 # LightGBM predictions, 15-min filter, Claude decisions
│   ├── sentiment.log               # News & sentiment analysis
│   ├── risk.log                    # Risk checks & circuit breaker events
│   ├── ibkr.log                    # Raw IBKR API communication
│   ├── ml.log                      # LightGBM model version, feature values, predictions, confidence
│   ├── claude.log                  # Claude API requests & responses
│   ├── web.log                     # FastAPI request/response log
│   └── errors.log                  # All ERROR and CRITICAL entries (all categories)
├── tests/
│   └── ...
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── main.py
```

-----

## Requirements

- Ubuntu Server 22.04 LTS
- Python 3.11+
- MariaDB 10.11+
- Nginx
- [Interactive Brokers TWS](https://www.interactivebrokers.com/en/trading/tws.php) or [IB Gateway](https://www.interactivebrokers.com/en/trading/ibgateway-stable.php) running locally
- An [Anthropic API key](https://console.anthropic.com/)
- An IBKR account (paper or live)

-----

## Security

### HTTPS with Let’s Encrypt

During setup, `setup.sh` asks whether to use **HTTPS** (recommended for production) or **HTTP** (for local/development use).

**HTTPS mode** (production): Certbot provisions a Let’s Encrypt certificate and configures Nginx automatically. HTTP traffic on port 80 is permanently redirected to HTTPS — no content is served over plain HTTP.

```
Nginx port 80  →  301 redirect to https://your-domain.com
Nginx port 443 →  HTTPS → FastAPI backend
```

Certificate renewal is handled automatically via a systemd timer installed by Certbot. No manual renewal is needed.

**HTTP mode** (local/development): Nginx proxies directly on port 80 with no TLS. Do not use this mode in production or for live trading.

### API Rate Limiting

The login endpoint enforces rate limiting to protect against brute-force attacks. Limits are applied per IP address:

|Endpoint           |Limit               |
|-------------------|---------------------|
|`POST /api/auth/login` |5 failed attempts / 60 seconds |

Exceeding the limit returns HTTP 429 Too Many Requests.

### Authentication

The web API uses JWT Bearer tokens with a **24-hour expiry**. Password comparison uses `hmac.compare_digest()` for constant-time comparison (prevents timing attacks). All protected endpoints require a valid JWT token in the `Authorization` header.

### Two-Factor Authentication (2FA)

TOTP-based 2FA support is planned but **not yet implemented**. The `pyotp` dependency is included in `requirements.txt` for future use.

-----

## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/WildcatKSS/ibkr-ai-trader.git
cd ibkr-ai-trader
```

### 2. Run the server setup script

```bash
chmod +x deploy/setup.sh
sudo bash deploy/setup.sh
```

The script performs the following steps automatically — and skips anything already installed:

|Step|What happens                                                                                      |
|----|--------------------------------------------------------------------------------------------------|
|1   |System update — `apt upgrade` on first install, `apt dist-upgrade` on re-runs                    |
|2   |System packages: curl, git, build-essential, libssl-dev, etc.                                     |
|3   |Python 3.11 via deadsnakes PPA                                                                    |
|4   |MariaDB 10.11 — installed, secured, database and user created                                     |
|5   |Nginx — installed and configured as reverse proxy (HTTP or HTTPS depending on your choice)        |
|5a  |Certbot & Let’s Encrypt — HTTPS mode only: certificate provisioned, HTTP→HTTPS redirect configured|
|6   |Node.js 20+ for frontend tooling                                                                   |
|7   |System user `trader` and application directories under `/opt/ibkr-trader`                         |
|8   |Python virtual environment and all packages from `requirements.txt`                               |
|9   |`.env` file generated with random secrets pre-filled                                              |
|10  |Nginx site config enabled, default site disabled                                                  |
|11  |Systemd services `ibkr-bot` and `ibkr-web` registered and enabled                                |
|12  |Log rotation configured (90-day retention)                                                        |
|13  |UFW firewall — SSH and HTTP always open; HTTPS also opened in HTTPS mode                          |
|14  |Fail2ban — brute-force protection on SSH and Nginx                                                |
|15  |Daily MariaDB backup cron job (30-day retention)                                                  |

At the end, the script prints the generated database password and the web interface URL.

### 3. Fill in your API keys

`setup.sh` generates `/opt/ibkr-trader/.env` automatically with database credentials and secrets pre-filled. The only values you need to add manually are your external API keys and passwords:

```env
ANTHROPIC_API_KEY=your_anthropic_api_key
IBKR_PORT=7497              # TWS paper: 7497 | IB Gateway paper: 4002 | TWS live: 7496 | IB GW live: 4001
ALPACA_API_KEY=your_alpaca_api_key
ALPACA_API_SECRET=your_alpaca_api_secret
FINNHUB_API_KEY=your_finnhub_api_key
SMTP_PASSWORD=your_smtp_password
WEB_PASSWORD=your_dashboard_password
```

All operational settings (trading mode, risk parameters, universe selection, position sizing, etc.) are configured via the web interface after first login. The `.env` file is only for secrets and connection details.

### 4. Run database migrations

```bash
cd /opt/ibkr-trader
venv/bin/alembic upgrade head
venv/bin/python db/seed.py
```

### 5. Start the services

```bash
systemctl start ibkr-bot ibkr-web
```

### 6. Start TWS or IB Gateway

Enable API access before starting the bot:
`Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients`

The web interface is now available at `https://your-domain.com` (HTTPS mode) or `http://your-domain.com` (HTTP mode).

-----

## Updating

To deploy a new version of the bot on an existing Ubuntu server, use the included update script:

```bash
sudo bash /opt/ibkr-trader/deploy/update.sh
```

The script fetches the latest code directly from GitHub — no local git clone required:

|Step|What happens                                                                           |
|----|---------------------------------------------------------------------------------------|
|1   |`ibkr-bot` and `ibkr-web` are stopped                                                 |
|2   |Code is pulled from GitHub (`wildcatkss/ibkr-ai-trader`, branch `main`)               |
|3   |Files are synced to `/opt/ibkr-trader` (`.env`, `venv`, `logs` untouched)             |
|4   |New or updated Python dependencies are installed                                       |
|5   |Pending database migrations are applied via Alembic                                    |
|6   |Nginx is reloaded                                                                      |
|7   |`ibkr-bot` and `ibkr-web` are restarted                                               |

**Authentication:**
- Public repo (default): SSH key of the root user, no configuration needed.
- Private repo: `GITHUB_TOKEN=ghp_xxx sudo bash deploy/update.sh`

**Safety:** if any step fails, the services are automatically restarted with the previous version — the server is never left in a stopped state.

> ⚠ Do not run this during an active trading session. The bot is stopped for the duration of the update.

-----

## Uninstalling

To remove IBKR AI Trader from the server:

```bash
sudo bash /opt/ibkr-trader/deploy/uninstall.sh
```

The script asks for confirmation before touching anything and lets you choose per component:

- Always removed: services, app directory, system user, cron job, fail2ban and logrotate config
- On request: database backup, MariaDB database/user, Nginx config, Let's Encrypt certificate, UFW rules

Python 3.11 and Node.js are never removed (shared system packages).

-----

## Intraday Trading

This bot is designed exclusively for **intraday trading of US stocks and ETFs**. Every position opened during the session is automatically closed before market close — there is no overnight exposure.

### Trading Day Lifecycle

```
09:15 ET  Universe scan        Scanner scores candidates; Claude selects instruments
09:30 ET  Market open          Bot begins generating intraday signals (LightGBM + Claude)
09:35+    Active trading       Signals → risk check → order execution (configurable buffer)
15:45 ET  EOD close routine    All remaining open positions are closed (configurable offset)
16:00 ET  Market close         Bot enters idle state until the next trading day
```

### Intraday Signal Inputs

The signal pipeline uses two layers:

**LightGBM model (primary signal — 24 features on 5-min candles):**

- Momentum: RSI, Stochastic %K/%D, MACD histogram
- Trend: ADX (+DI/−DI), EMA 9/21 cross
- Volatility: Bollinger %B, band width, squeeze detection, ATR %
- Volume: Money Flow Index, OBV slope, volume ratio
- Price-relative: close vs. EMA9, close vs. EMA21, close vs. VWAP
- Candle structure: body ratio, upper/lower wick ratios
- Short-term returns: 1-bar, 3-bar, 6-bar log returns

**15-min confirmation filter:**

- EMA cross and MACD histogram direction on the 15-min timeframe must agree with the 5-min signal

**Sentiment score:**

- Aggregated news sentiment from Alpaca / Finnhub (-1.0 bearish to +1.0 bullish)

**Claude (context & final decision):**

- Evaluates the ML signal, 15-min confirmation, and sentiment score
- Decides whether to act on the signal, and sets entry/target/stop prices
- Provides a plain-language explanation for every decision

### End-of-Day Close Routine

`EOD_CLOSE_MINUTES` before market close (default: 15 minutes), all remaining positions are closed at market price and a daily summary is sent by email.

-----

## How Claude Is Used

Claude is called at two specific points in the pipeline: **universe selection** (once per trading day) and **signal confirmation** (once per actionable signal). All other steps — indicator calculation, ML prediction, risk management, order execution, position closing — are fully programmatic and run without Claude.

Every Claude call produces a plain-language explanation that is stored in MariaDB and visible in the web interface.

-----

### 1 · Universe Selection

At the start of each trading day, the **scanner** fetches daily OHLCV bars for all symbols in the configurable `UNIVERSE_POOL` via IBKR. The **criteria scorer** evaluates each symbol against 12 bullish criteria (7 core + 5 bonus, scoring 0-100): price vs. moving averages, trend structure, volume, candle quality, and breakout proximity.

The scored and ranked list is then passed to **Claude**, which makes the final selection. Claude considers the criteria scores, recent price action, and market context to pick the best instruments for the session.

**Explained in the web interface:** which instruments were selected, why each one scored high, which were rejected and why. In approval mode, the full reasoning is shown before you confirm or reject each suggestion.

-----

### 2 · Signal Generation

Signals are generated by a **LightGBM model** running locally on the server. It evaluates a 24-feature vector built from `ta` indicators (RSI, Stochastic, MACD, ADX, Bollinger Bands, ATR, MFI, OBV, EMA cross, VWAP deviation, candle structure, short-term returns) and outputs a directional prediction: long, short, or no trade. A **15-minute candle confirmation** check then filters out signals where the EMA cross and MACD direction on the 15-min timeframe disagree with the 5-min signal.

The **sentiment module** fetches recent news from Alpaca/Finnhub and computes a score (-1.0 to +1.0). This score, together with the ML prediction and 15-min confirmation, is passed to **Claude** for the final decision. Claude evaluates market context, decides whether to act, and sets the entry price, target, and stop-loss.

**Explained in the web interface:** the LightGBM prediction, the 15-min confirmation result, sentiment score, Claude’s assessment, and the full reasoning behind the final decision.

-----

### 3 · Order Execution

When a signal is confirmed, the **risk manager** computes the position size (fixed %, fixed amount, or Kelly Criterion) and validates it against the circuit breaker and hard caps. The **executor** places a limit order at the entry price via the IBKR API. If the limit order is not filled within the configurable timeout, it is cancelled and retried as a market order.

All trades — including dryrun — are persisted to the `trades` table with full provenance: ML label, probability, 15-min confirmation status, and Claude's explanation.

-----

### 4 · Position Exit

Open positions are exited in one of three ways:

- **Target hit** — price reaches the take-profit level set by Claude at entry
- **Stop hit** — price reaches the stop-loss level set by Claude at entry
- **EOD close** — all remaining positions are closed at market price before market close (configurable via `EOD_CLOSE_MINUTES`)

All exit events, fill prices, and P&L are logged in `logs/trading.log` and persisted to the `trades` table.

-----

### 5 · Sentiment Analysis

Before Claude makes its final trading decision, the bot fetches recent news articles for the instrument from the **Alpaca News API** (primary) and **Finnhub** (fallback). Each article is scored using keyword-based sentiment analysis weighted by recency (newer articles have more influence, with a half-life of ~6 hours). The aggregated sentiment score (-1.0 bearish to +1.0 bullish) is included in the Claude prompt as additional context.

-----

### 6 · Risk Management

Before every order is placed, the **risk manager** (`bot/risk/manager.py`) performs a programmatic check:

1. **Circuit breaker** — has the daily loss limit or consecutive loss limit been hit?
2. **Position sizing** — compute share count from the configured method (fixed %, fixed amount, or half-Kelly)
3. **Hard cap** — ensure the position does not exceed `POSITION_MAX_PCT` of portfolio value

If any check fails, the order is blocked and the reason is logged. Risk management does not call Claude — it is entirely rule-based.

-----

### 7 · Circuit Breaker

The circuit breaker is checked by the risk manager before every order. It trips when:

- **Daily loss** exceeds `CIRCUIT_BREAKER_DAILY_LOSS_PCT` of portfolio value
- **Consecutive losing trades** reach `CIRCUIT_BREAKER_CONSECUTIVE_LOSSES`

When tripped, all subsequent orders are blocked for the rest of the session. An email/webhook alert is sent via the notifier. The circuit breaker is fully programmatic — Claude is not involved.

-----

### 8 · End-of-Day Close Routine

`EOD_CLOSE_MINUTES` before market close (default: 15 minutes), the engine triggers the EOD close routine. All remaining open positions are closed at market price via IBKR to ensure zero overnight exposure. The routine runs programmatically — Claude is not called. An alert is sent if any position fails to close.

-----

### 9 · Daily Summary

After the EOD close, the notifier sends a daily summary via email and/or webhook containing: trade count, wins, losses, and total P&L for the session.

-----

## Market Calendar

The bot uses a built-in NYSE trading calendar to determine whether the market is open before starting any trading activity. It automatically handles:

- **Public holidays** — e.g. Christmas, Thanksgiving, Independence Day
- **Early-close days** — e.g. the Friday after Thanksgiving (market closes at 13:00 ET)
- **Weekend detection** — no trading on Saturdays or Sundays

The calendar is provided by the `exchange_calendars` Python library (the maintained fork of the abandoned `trading_calendars`), which is kept up to date with official NYSE schedules. If the bot is started on a non-trading day, it logs the fact and enters idle state until the next market open.

-----

## Connection & Reliability

### Reconnect Logic

If the connection to IBKR (TWS or IB Gateway) is lost during a trading session, the bot does not crash. The `IBKRConnection._ensure_connected()` method attempts automatic reconnection with exponential backoff (2s → 4s) up to 3 attempts. If reconnection fails, a `ConnectionError` is raised and the current tick is skipped — the bot retries on the next tick (60 seconds later).

### Health Check Endpoint

The web API exposes a `/health` endpoint (no authentication required) for liveness probes:

```json
{
  "status": "ok",
  "timestamp": "2025-03-31T14:22:01+00:00"
}
```

A separate authenticated endpoint `GET /api/status` returns bot runtime status:

```json
{
  "trading_mode": "paper",
  "market_open": true,
  "trading_day": true,
  "timestamp": "2025-03-31T14:22:01+00:00"
}
```

-----

## Order Fill Monitoring

When the bot places a limit order, it actively monitors whether the order gets filled. If a limit order remains unfilled after a configurable timeout, the bot takes one of two actions depending on context:

- **Convert to market order** — if the signal is still valid and time permits
- **Cancel the order** — if the signal has expired or market conditions have changed

This prevents stale limit orders from sitting open and filling at unexpected times. Timeout behaviour is configurable via the web interface under **Settings → Orders**.

All fill events, timeouts, and cancellations are logged in `logs/trading.log` and visible in the web interface trade log.

-----

## Circuit Breaker

The circuit breaker protects your capital by automatically blocking all new orders when configurable thresholds are breached:

- **Daily loss limit** (`CIRCUIT_BREAKER_DAILY_LOSS_PCT`) — blocks trading if losses exceed a set percentage of portfolio value in one day
- **Consecutive losses** (`CIRCUIT_BREAKER_CONSECUTIVE_LOSSES`) — blocks trading after N consecutive losing trades in a row

When the circuit breaker trips, a notification is sent (email and/or webhook) and no new orders are placed for the rest of the session. The EOD close routine still runs to close any remaining open positions.

-----

## Alerting

The bot sends real-time notifications via email and/or HTTP webhook for the following events:

|Event               |Type             |Content                                                |
|--------------------|-----------------|-------------------------------------------------------|
|`trade_opened`      |Info             |Symbol, action, shares, fill price, target, stop       |
|`trade_closed`      |Info             |Symbol, exit price, P&L                                |
|`circuit_breaker`   |Critical         |Reason the circuit breaker tripped                     |
|`daily_summary`     |Info             |Trade count, wins, losses, total P&L                   |
|`error`             |Error            |Unhandled error details and context                    |

-----

## Backtesting

The backtesting engine (`bot/backtesting/`) replays historical OHLCV data through the existing signal pipeline (indicators, LightGBM, 15-min confirmation) with simulated order execution. Claude API calls are skipped to keep backtests fast and reproducible.

### How it works

1. Pre-compute technical indicators and ML features on the full dataset
2. For each bar after warmup (60 bars), get a LightGBM prediction
3. Check 15-min confirmation (EMA cross + MACD direction)
4. If confirmed, open a simulated position with ATR-based stop/target
5. Monitor stop-loss and take-profit on each subsequent bar
6. Close any remaining position at the end of the dataset

### Metrics

The engine computes: total return, total P&L, trade count, win rate, average win/loss, profit factor, max drawdown, Sharpe ratio, largest win/loss.

### Usage

**Via CLI:**
```bash
python -m bot.backtesting.engine --symbol AAPL --data historical.csv --capital 100000
```

**Via API:**
```bash
curl -X POST http://localhost:8000/api/backtesting/run \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"symbol": "AAPL", "initial_capital": 100000, "position_size_pct": 2.0}'
```

**Via web interface:** the Backtesting page lets you configure parameters, run a backtest, and view the equity curve chart and trade log.

-----

## Universe Selection

The bot automatically selects the best instruments for intraday trading each day, removing the need to maintain a manual watchlist.

### How It Works

1. **Scan** — Before market open, the scanner fetches daily OHLCV bars for all symbols in `UNIVERSE_POOL` (configurable list of ~50 US stocks and ETFs) via IBKR.
1. **Score** — The criteria scorer evaluates each symbol against 12 bullish criteria (7 core + 5 bonus, scoring 0-100): price above moving averages, trend structure, higher highs/lows, volume patterns, candle quality, and breakout proximity.
1. **Select** — Claude reviews the scored list and makes the final selection, considering criteria scores, recent price action, and market context.
1. **Trade** — Intraday signals are generated and orders placed. All positions are automatically closed before market close.

### Selection Modes

|Mode        |Behaviour                                                                                             |
|------------|------------------------------------------------------------------------------------------------------|
|`autonomous`|Claude’s selections are applied immediately — the bot starts trading without human input              |
|`approval`  |Selections appear in the web interface; the operator approves, rejects, or edits before trading begins|

Switch between modes at any time via the web interface under **Settings → Universe Selection**.

### Approval Workflow (approval mode)

In approval mode, each Claude suggestion in the web interface shows:

- **Instrument** — ticker, full name, sector, exchange
- **Score** — Claude’s ranking score (0–100)
- **Reasoning** — plain-language explanation of why Claude selected it
- **Key metrics** — average volume, volatility, recent price action, news summary
- **Action** — Approve / Reject / Modify max position size

Approved instruments enter the active trading universe immediately. Rejected ones are excluded until the next scan cycle.

-----

## Web Dashboard

The React management interface (Vite + TypeScript + Tailwind CSS) provides:

- **Login** — JWT authentication with sessionStorage token
- **Dashboard** — Trading mode, market status, daily P&L, trade count, open positions
- **Trade History** — Paginated trade log with symbol and status filters
- **Performance** — Period filters (1d/7d/30d/all), metrics cards, cumulative P&L chart (Recharts)
- **Settings** — Inline editing of all operational settings
- **Backtesting** — Parameter form, equity curve chart, trade log table

The frontend is built to `web/frontend/static/` and served by Nginx. The API proxy routes `/api/*` to the FastAPI backend on port 8000.

-----

## Logging

All bot activity is logged at **DEBUG level** by default. Every entry is written **synchronously to disk first** (so the trading loop is never blocked), and then flushed to **MariaDB asynchronously** via a background queue for full traceability and searchability via the web interface.

### Log Categories

|Category   |File                |What is logged                                                                             |
|-----------|--------------------|-------------------------------------------------------------------------------------------|
|`universe` |`logs/universe.log` |Scan results, instrument scores, selection reasoning, rejected instruments                 |
|`trading`  |`logs/trading.log`  |Order placement, fills, fill timeouts, cancellations, trade lifecycle                      |
|`signals`  |`logs/signals.log`  |Claude prompts, raw responses, generated signals, confidence scores                        |
|`sentiment`|`logs/sentiment.log`|News sources, headlines, Claude sentiment scores, summaries                                |
|`risk`     |`logs/risk.log`     |Position size checks, risk rejections, circuit breaker events                              |
|`ibkr`     |`logs/ibkr.log`     |Raw IBKR API messages, connection state, account updates                                   |
|`ml`       |`logs/ml.log`       |LightGBM model version, feature values per signal, prediction confidence, retraining events|
|`claude`   |`logs/claude.log`   |Full Claude API request/response payloads, token usage, latency                            |
|`web`      |`logs/web.log`      |All HTTP requests, response codes, user actions in the interface                           |
|`errors`   |`logs/errors.log`   |All ERROR and CRITICAL entries across every category                                       |

Every log entry includes: **timestamp**, **log level**, **category**, **module**, **function**, **line number**, and the full **message** — making it straightforward to pinpoint exactly where a bug originated.

### Log Format

```
2025-03-31 14:22:01.483 | DEBUG | trading | executor.place_order:87 | Placing BUY order: AAPL x10 @ LIMIT 174.50 | order_id=4821
2025-03-31 14:22:01.491 | DEBUG | ibkr    | connection.on_order_status:212 | Order 4821 status: Submitted
2025-03-31 14:22:03.102 | DEBUG | ibkr    | connection.on_exec_details:238 | Order 4821 filled: 10 @ 174.48
2025-03-31 14:22:03.110 | INFO  | trading | executor.on_fill:94 | Trade opened: AAPL LONG x10 avg 174.48
2025-03-31 14:22:03.115 | DEBUG | claude  | generator.explain:61 | Claude explanation: "Bought AAPL on momentum signal..."
```

### Querying Logs via API

The `GET /api/logs` endpoint supports filtering by category and level:

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/api/logs?category=trading&level=ERROR&limit=50"
```

A web-based log viewer with full-text search and time range filters is planned as part of the frontend.

### Log Retention

Disk logs are rotated automatically by logrotate (90-day retention, configured by `setup.sh`). MariaDB log entries are retained indefinitely; a purge mechanism is planned.

-----

## Configuration Reference

Configuration is split into two categories: secrets stored in `.env`, and all operational settings managed via the web interface.

### `.env` — Secrets only (set once during setup)

After `setup.sh` runs, fill in only your external API keys and passwords. Everything else is generated automatically or configured via the web interface.

|Variable           |Set by  |Description                                                            |
|-------------------|--------|-----------------------------------------------------------------------|
|`ANTHROPIC_API_KEY`|You     |Your Anthropic API key                                                 |
|`IBKR_PORT`        |You     |TWS paper: 7497 · IB GW paper: 4002 · TWS live: 7496 · IB GW live: 4001|
|`ALPACA_API_KEY`   |You     |Alpaca News API key                                                    |
|`ALPACA_API_SECRET`|You     |Alpaca News API secret                                                 |
|`FINNHUB_API_KEY`  |You     |Finnhub API key (fallback news provider)                               |
|`SMTP_PASSWORD`    |You     |SMTP password or app password for email alerts                         |
|`WEB_PASSWORD`     |You     |Admin password for the web dashboard login (JWT-authenticated)         |
|`DB_PASSWORD`      |setup.sh|Generated automatically                                                |
|`SECRET_KEY`       |setup.sh|Session signing key; generated automatically                           |
|`DOMAIN`           |setup.sh|Your domain name; entered during setup for Certbot                     |

-----

### Web Interface — All operational settings

Every setting below is managed via **Settings** in the web interface. Changes take effect immediately and are recorded in the audit trail. Default values are seeded on first run via `db/seed.py`.

#### Trading

|Setting (DB key)                |Default   |Description                                      |
|--------------------------------|----------|-------------------------------------------------|
|`TRADING_MODE`                  |`dryrun`  |`paper` · `live` · `dryrun`                      |
|`EOD_CLOSE_MINUTES`             |`15`      |Close all positions N minutes before market close|
|`MARKET_OPEN_BUFFER_MINUTES`    |`5`       |Wait N minutes after open before new positions   |

#### Position Sizing

|Setting (DB key)          |Default      |Description                                |
|--------------------------|-------------|-------------------------------------------|
|`POSITION_SIZING_METHOD`  |`fixed_pct`  |`kelly` · `fixed_pct` · `fixed_amount`     |
|`POSITION_SIZE_PCT`       |`2.0`        |% of portfolio per trade (when fixed_pct)  |
|`POSITION_SIZE_AMOUNT`    |`5000.0`     |Dollar amount per trade (when fixed_amount)|
|`POSITION_MAX_PCT`        |`5.0`        |Hard cap: max % in a single position       |

#### Universe Selection

|Setting (DB key)              |Default      |Description                            |
|------------------------------|-------------|---------------------------------------|
|`UNIVERSE_APPROVAL_MODE`      |`autonomous` |`autonomous` · `approval`              |
|`UNIVERSE_MAX_SYMBOLS`        |`10`         |Watchlist size from daily scan         |
|`UNIVERSE_MIN_AVG_VOLUME`     |`500000`     |Minimum 20-day average daily volume    |
|`UNIVERSE_MIN_PRICE`          |`5.0`        |Minimum share price (USD)              |
|`UNIVERSE_MAX_PRICE`          |`500.0`      |Maximum share price (USD)              |

#### Signal & Model

|Setting (DB key)          |Default |Description                                     |
|--------------------------|--------|-------------------------------------------------|
|`ML_FORWARD_BARS`         |`6`     |Bars ahead for forward-return label (6 = 30 min) |
|`ML_LONG_THRESHOLD_PCT`   |`0.3`   |Min return % for long label                       |
|`ML_SHORT_THRESHOLD_PCT`  |`0.3`   |Min drop % for short label                        |
|`ML_MIN_PROBABILITY`      |`0.55`  |Min predicted probability to act on signal        |

#### Risk & Circuit Breaker

|Setting (DB key)                     |Default|Description                           |
|-------------------------------------|-------|--------------------------------------|
|`CIRCUIT_BREAKER_DAILY_LOSS_PCT`     |`3.0`  |Halt trading on daily loss %          |
|`CIRCUIT_BREAKER_CONSECUTIVE_LOSSES` |`5`    |Halt after N consecutive losses       |
|`STOP_LOSS_PCT`                      |`1.0`  |Default stop-loss % of entry price    |
|`TAKE_PROFIT_PCT`                    |`2.0`  |Default take-profit % of entry price  |

#### Orders

|Setting (DB key)             |Default |Description                              |
|-----------------------------|--------|-----------------------------------------|
|`ORDER_FILL_TIMEOUT_SECONDS` |`60`    |Cancel or convert after this many seconds|

#### Alerting & Webhooks

|Setting (DB key)           |Default         |Description                         |
|---------------------------|----------------|------------------------------------|
|`ALERTS_EMAIL_ENABLED`     |`false`         |Enable email alerts                 |
|`ALERTS_EMAIL_FROM`        |—               |Sender address                      |
|`ALERTS_EMAIL_TO`          |—               |Recipient address                   |
|`ALERTS_SMTP_HOST`         |`smtp.gmail.com`|SMTP server                         |
|`ALERTS_SMTP_PORT`         |`587`           |SMTP port                           |
|`ALERTS_WEBHOOKS_ENABLED`  |`false`         |Enable HTTP webhook notifications   |
|`ALERTS_WEBHOOK_URL`       |—               |HTTP endpoint for trade events      |

All 42 default settings are defined in `db/seed.py`. Run `python db/seed.py` to insert missing defaults after adding new settings.

-----

## Data Sources

### News & Sentiment

The bot uses the **Alpaca News API v2** as the primary source for recent news articles (last 24 hours) and the **Finnhub company news API** as a fallback. Articles are scored using keyword-based sentiment analysis with recency weighting. The aggregated score (-1.0 to +1.0) is included in the Claude prompt for the final trading decision. API keys for both services are configured in `.env`.

### Historical Data

The IBKR broker integration (`bot/core/broker.py`) fetches daily and intraday OHLCV bars via IBKR’s Historical Data API. Rate limiting (0.5s pause between requests) is built in to respect IBKR’s pacing limits (~60 requests per 10 minutes).

-----

## Position Sizing

Every trade is sized according to a configurable model. The bot never allocates more capital than the rules allow, regardless of Claude’s confidence in a signal.

### Sizing Methods

|Method        |How it works                                                           |
|--------------|-----------------------------------------------------------------------|
|`kelly`       |Kelly Criterion based on historical win rate and average win/loss ratio|
|`fixed_pct`   |Fixed percentage of current account value per trade                    |
|`fixed_amount`|Fixed dollar amount per trade                                          |

All position sizing settings are configurable via the web interface under **Settings → Position Sizing**.

The hard caps apply regardless of the sizing method chosen. If Kelly produces a size that exceeds `max_pct_per_instrument`, it is automatically reduced to the cap.

-----

## Gap Protection

Instruments with a pre-market gap exceeding `GAP_FILTER_MAX_PCT` (default 3%) are flagged during the signal pipeline. The gap filter setting is configurable via the web API. Flagged instruments are logged in `logs/universe.log`.

-----

## Model Version Control

Every time the LightGBM model is retrained, the new version is saved with a timestamp and performance metrics in `bot/ml/models/version.json`. Previous versions are never overwritten — they remain available for rollback.

### Rollback

Rolling back to a previous version takes effect immediately:

```bash
cd /opt/ibkr-trader
source venv/bin/activate
python -m bot.ml.versioning --rollback v20240324_120000
```

### List versions

```bash
python -m bot.ml.versioning --list
python -m bot.ml.versioning --current
```

### A/B Model Testing *(not yet implemented)*

> Shadow model testing (running a new model in parallel alongside the live model) is planned but not yet built.

-----

## Dry Run Mode

In dry run mode the full signal pipeline executes — LightGBM predictions, 15-min confirmation, Claude reasoning, risk checks — but no orders are sent to IBKR. All decisions are logged exactly as in live or paper mode.

Dry run is the default mode after installation. It uses a configurable watchlist (`DRYRUN_WATCHLIST` setting, default: `SPY,AAPL,MSFT,NVDA,TSLA`) instead of running the full universe scan. IBKR connection is optional in dryrun — when not connected, the bot logs that no data provider is available.

Dry run is useful for:

- Testing the signal pipeline without an IBKR paper account
- Verifying configuration changes before switching to paper or live
- Demonstrating the bot’s decision-making without capital at risk

Switch modes via the web dashboard under **Settings**, or via the API:

```bash
curl -X PUT http://localhost:8000/api/settings/TRADING_MODE \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d ‘{"value": "paper"}’
```

The engine detects mode changes on the next tick (within 60 seconds) and handles broker connection/disconnection automatically — no service restart required.

-----

## Trade Export *(not yet implemented)*

> Trade export (CSV/Excel) from the web interface is planned but not yet built. Trade data is stored in the `trades` table and can be queried via SQL.

-----

## Webhook Support

In addition to email, the bot can send event notifications to any external system via HTTP webhooks. Enable webhooks by setting `ALERTS_WEBHOOKS_ENABLED` to `true` and configuring `ALERTS_WEBHOOK_URL` via the API.

Each webhook payload is a JSON POST with the event type, timestamp, and relevant data. Supported events: `trade_opened`, `trade_closed`, `circuit_breaker`, `daily_summary`, `error`.

-----

## Configuration Audit Trail *(not yet implemented)*

> An audit trail for configuration changes is planned but not yet built. Currently, setting updates are logged via the standard logger (`web` category).

-----

## Cost Dashboard *(not yet implemented)*

> A cost dashboard tracking Claude API usage, IBKR commissions, and net P&L is planned but not yet built.

-----

## Disclaimer

This project is for **educational purposes only**. Trading financial instruments involves significant risk of loss. The authors are not responsible for any financial losses incurred through the use of this software. Always test thoroughly on a paper trading account before going live.

-----

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on setting up a development environment, the branching workflow, code style requirements, and how to report security vulnerabilities.

-----

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.

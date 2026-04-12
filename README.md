# IBKR AI Trader

An open-source intraday trading bot for Interactive Brokers, powered by Claude AI. Focused exclusively on **stocks and ETFs** — positions are opened and closed within the same trading day, with no overnight exposure.

> ⚠️ **Work in progress** — The core trading pipeline is complete and tested: IBKR broker integration, universe selection, signal generation (LightGBM + 15-min filter + Claude), risk management (circuit breaker + position sizing), order execution (fill monitoring + market-order fallback), EOD close routine, and email/webhook alerting — with a **255-test suite**. The web frontend, backtesting engine, and news/sentiment module are not yet implemented. See the [Development Status](#-development-status) section for a full overview. **Contributions are welcome** — see [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

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
- **News & sentiment analysis** — Real-time news is processed by Claude to gauge market sentiment *(not yet implemented)*
- **Explainability** — Every automated action, including universe selection, is explained in plain language by Claude and logged

### Risk & safety

- **Risk management** — Claude advises on position sizing, stop-losses, and portfolio exposure
- **Position sizing model** — Configurable capital allocation per trade using fixed percentage, fixed amount, or Kelly Criterion with hard per-instrument capital limits
- **Circuit breaker** — Automatically halts trading when drawdown or loss thresholds are exceeded
- **Gap protection** — Instruments with extreme expected opening gaps are flagged or excluded during universe selection
- **Order fill monitoring** — Unfilled limit orders are automatically cancelled or converted to market orders after a configurable timeout

### Model & backtesting

- **Backtesting** — Test strategies against historical market data before going live *(not yet implemented)*
- **Model version control** — Every LightGBM retrain is versioned and stored; roll back to any previous model via CLI
- **A/B model testing** — Run a new model version in paper trading alongside the live model before promoting it to production *(not yet implemented)*

### Monitoring & interface

- **Performance dashboard** — P&L charts, Sharpe ratio, win rate, max drawdown, and more *(not yet implemented)*
- **Cost dashboard** — Real-time overview of Claude API costs, IBKR commissions, and net P&L after all costs *(not yet implemented)*
- **Comprehensive logging** — DEBUG-level logs across all categories, written to disk and MariaDB
- **Alerting** — Real-time notifications via email on critical events
- **Webhook support** — Push events to any external system via configurable HTTP webhooks
- **Trade export** — Download full trade history as CSV or Excel from the web interface *(not yet implemented)*
- **Configuration audit trail** — Every change made via the web interface is logged with timestamp and user *(not yet implemented)*
- **Web API** — Settings and configuration managed via REST API; web frontend not yet built
- **Security** — HTTPS via Let’s Encrypt, JWT authentication with rate limiting

-----

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Ubuntu Server                         │
│                                                              │
│   Browser ──► Nginx (reverse proxy) ──► FastAPI (backend)    │
│                                              │               │
│                          ┌───────────────────┤               │
│                          ▼                   ▼               │
│                     MariaDB            Python Bot Core       │
│                   (trades, logs,             │               │
│                    config, users)    ┌───────┴────────┐      │
│                                      ▼                ▼      │
│                               IBKR API          Claude API   │
│                             (TWS / IB GW)    (universe,      │
│                                               signals, risk, │
│                                               sentiment,     │
│                                               execution)     │
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
│   ├── backtesting/                # Historical simulation engine (not yet implemented)
│   ├── sentiment/                  # News & sentiment analysis (not yet implemented)
│   └── utils/
│       ├── __init__.py
│       ├── logger.py               # Disk-first, async MariaDB flush logger
│       ├── calendar.py             # NYSE trading calendar & market hours validation
│       └── config.py
├── web/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app (health, status, settings, logs endpoints)
│   │   └── auth.py                 # JWT authentication + rate limiting
│   └── frontend/                   # React dashboard (not yet implemented)
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
09:15 ET  Universe scan        Claude scores and selects instruments for the day
09:30 ET  Market open          Bot begins monitoring and generating intraday signals
09:30–15:45  Active trading    Claude enters and exits positions throughout the session
15:45 ET  EOD close routine    All remaining open positions are closed (configurable offset)
16:00 ET  Market close         Bot enters idle state until the next trading day
```

### Intraday Signal Inputs

The signal pipeline uses two layers:

**LightGBM model (primary signal — 5-min candles):**

- VWAP deviation — distance of price from the daily VWAP
- Volume ratio — current volume vs. average volume at this time of day
- RSI (14 periods on 5-min bars)
- MACD histogram value
- ATR as volatility measure
- Gap percentage vs. previous close
- Time of day — first hour and last 30 minutes behave differently
- Sector momentum — is the sector outperforming or underperforming today

**15-min confirmation filter:**

- Trend direction on 15-min candles must agree with the 5-min signal before a trade is executed

**Claude (context & final decision):**

- Real-time news headlines and sentiment scores
- Broader market conditions
- Portfolio context and risk state

### End-of-Day Close Routine

`EOD_CLOSE_MINUTES` before market close (default: 15 minutes), all open orders are cancelled, remaining positions are closed at market price, and a daily summary is sent by email. See [step 9 in How Claude Is Used](#9--end-of-day-close-routine) for full detail.

-----

## How Claude Is Used

Every automated action taken by the bot is driven by Claude. Nothing happens silently — each decision is reasoned, logged, and explained in plain language in the web interface.

Every action listed below produces a plain-language explanation that is stored in MariaDB and visible in the web interface. The goal is full auditability: at any point you can open the log viewer and understand exactly what the bot did, why it did it, and what data it was looking at at that moment.

-----

### 1 · Universe Selection

At the start of each trading day, Claude scans all tradeable US stocks and ETFs available via IBKR. For each instrument it evaluates pre-market volume, gap percentage, average true range (ATR), recent price action, sector momentum, and news activity. Claude assigns a score from 0 to 100 and selects the top instruments for that session.

**Explained in the web interface:** which instruments were selected, why each one scored high, which were rejected and why, and what the key metrics were at the time of selection. In approval mode, the full reasoning is shown before you confirm or reject each suggestion.

-----

### 2 · Signal Generation

Signals are generated by a **LightGBM model** running locally on the server. Every 5 minutes it evaluates a feature vector built from ta indicators (VWAP deviation, volume ratio, RSI, MACD histogram, ATR, gap %, sector momentum, time of day) and outputs a directional prediction: long, short, or no trade. A **15-minute candle confirmation** check then filters out signals that run counter to the broader intraday trend — only signals where both timeframes agree are passed forward.

Once a signal clears both checks, it is handed to **Claude**, which evaluates market context, adds sentiment data, and decides whether to act on it. Claude also determines the entry price, price target, and stop-loss level.

**Explained in the web interface:** the LightGBM feature values that produced the signal, the 15-min confirmation result, Claude’s contextual assessment, and the full reasoning behind the final decision.

-----

### 3 · Order Execution

When a signal is confirmed, Claude decides the exact order type (market, limit, or stop-limit), the entry timing, and the initial position size. The bot then places the order via the IBKR API without human intervention.

**Explained in the web interface:** why a specific order type was chosen, what entry timing logic was applied, and what Claude expected to happen after entry.

-----

### 4 · Position Management

After a position is opened, Claude monitors it in real time. It decides whether to move a stop-loss to break-even, scale out of a partial position, hold through temporary pullbacks, or exit early if conditions deteriorate.

**Explained in the web interface:** every adjustment to an open position is logged with Claude’s reasoning — why the stop was moved, why a partial exit was taken, or why Claude chose to hold.

-----

### 5 · Trade Exit

Claude determines when to close a position based on price target reached, stop-loss hit, signal reversal, or deteriorating momentum. It chooses the appropriate exit order type and size.

**Explained in the web interface:** the reason for the exit (target hit, stop, reversal, or manual override), the final P&L, and a post-trade evaluation from Claude on whether the trade played out as expected.

-----

### 6 · Sentiment Analysis *(not yet implemented)*

> This module is planned but not yet built. When implemented, news headlines and articles for all instruments in the active universe will be passed to Claude for sentiment scoring. See the [Development Status](#-development-status) section.

-----

### 7 · Risk Management

Before every order is placed, Claude performs a risk check on the current portfolio state. It evaluates open exposure, correlation between positions, available buying power, and whether the new trade fits within the configured risk parameters (max positions, max daily loss, max drawdown).

**Explained in the web interface:** the outcome of each risk check — approved or blocked — with a full breakdown of the factors Claude weighed and the current portfolio risk metrics at that moment.

-----

### 8 · Circuit Breaker

If the daily loss limit or maximum drawdown threshold is breached, Claude triggers the circuit breaker. It cancels all open orders, closes all remaining positions, and halts trading for the rest of the session.

**Explained in the web interface:** the exact threshold that was breached, the portfolio state at the moment of the trigger, which orders were cancelled, which positions were closed and at what prices, and what the final session P&L was.

-----

### 9 · End-of-Day Close Routine

Fifteen minutes before market close (configurable), Claude initiates the EOD close routine regardless of position P&L. All remaining open positions are closed at market price via IBKR to ensure zero overnight exposure.

**Explained in the web interface:** which positions were closed as part of EOD, the prices achieved, the final session P&L summary, and whether any positions required special handling.

-----

### 10 · Daily Summary

After the EOD close, Claude generates a plain-language summary of the full trading day: instruments traded, total P&L, win rate, best and worst trade, risk events, and observations about market conditions. This summary is sent via email and is available in the web interface.

**Explained in the web interface:** the complete daily summary including all trades, Claude’s overall assessment of the session, and what to monitor the following day.

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

The circuit breaker protects your capital by automatically halting all trading activity when configurable thresholds are breached:

- **Daily loss limit** — stops trading if losses exceed a set percentage of account value in one day
- **Max drawdown** — halts the bot if the portfolio drops beyond a defined peak-to-trough threshold
- **Manual override** — the web interface allows you to manually pause or resume trading at any time

When the circuit breaker trips, all open orders are cancelled, a notification is sent, and the bot enters a safe idle state until manually reset.

-----

## Alerting

The bot sends real-time email notifications for critical events:

|Event                    |Severity|
|-------------------------|--------|
|Trade opened / closed    |Info    |
|Circuit breaker triggered|Critical|
|IBKR connection lost     |Critical|
|Claude API error         |Warning |
|Reconnect attempt failed |Warning |
|Daily P&L summary        |Info    |
|Backtest completed       |Info    |
|Model retrained          |Info    |
|Model rollback           |Warning |
|Model version promoted   |Info    |
|Configuration changed    |Info    |

-----

## Backtesting *(not yet implemented)*

> The backtesting engine is planned but not yet built. When implemented, it will reuse the existing signal pipeline (indicators, LightGBM, risk manager) to replay historical data and calculate performance metrics (Sharpe ratio, max drawdown, win rate). See the [Development Status](#-development-status) section.

-----

## Universe Selection

Claude autonomously decides which stocks and ETFs are most suitable for intraday trading that day, removing the need to maintain a manual watchlist.

### How It Works

1. **Scan** — At market open, the scanner queries IBKR for all tradeable US stocks and ETFs and pulls intraday-relevant metrics: pre-market volume, gap %, volatility, sector momentum, and average true range (ATR).
1. **Score** — Claude evaluates each instrument and assigns an intraday score based on: opening gap, pre-market volume spike, news catalysts, ATR, and sector trend.
1. **Select** — The top-ranked instruments are added to the active intraday universe for that session.
1. **Trade** — Intraday signals are generated and orders placed. All positions are automatically closed 15 minutes before market close.

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

## Performance Dashboard *(not yet implemented)*

> The web frontend and performance dashboard are planned but not yet built. When implemented, the dashboard will show P&L charts, win rate, Sharpe ratio, max drawdown, trade log, and open positions. See the [Development Status](#-development-status) section.

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

All 56 default settings are defined in `db/seed.py`. Run `python db/seed.py` to insert missing defaults after adding new settings.

-----

## Data Sources

### News & Sentiment *(not yet implemented)*

> When implemented, the bot will use the **Alpaca News API** as the primary source for real-time news headlines and the **Finnhub API** as a configurable fallback. Claude will process each news item and return a sentiment score that feeds into signal generation. API keys for both services are configured in `.env`. See the [Development Status](#-development-status) section.

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

Switch modes via the API:

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

## Development Status

This project is in active development. The core trading pipeline is complete; IBKR broker integration, web frontend, backtesting, and news/sentiment are not yet implemented.

|Component                                                                    |Status     |
|-----------------------------------------------------------------------------|-----------|
|`deploy/setup.sh` — full Ubuntu server setup (15 steps, idempotent)         |✅ Complete|
|`deploy/update.sh` — update from GitHub (SSH + HTTPS token support)         |✅ Complete|
|`deploy/uninstall.sh` — interactive removal script                          |✅ Complete|
|`deploy/systemd/` — systemd service units                                   |✅ Complete|
|`db/` — SQLAlchemy models (LogEntry, Setting, Trade), migrations, seed      |✅ Complete|
|`bot/utils/logger.py` — disk-first async logging                            |✅ Complete|
|`bot/utils/config.py` — MariaDB settings loader with TTL cache              |✅ Complete|
|`bot/utils/calendar.py` — NYSE trading calendar                             |✅ Complete|
|`bot/core/engine.py` — trading loop with hot-reload mode switching          |✅ Complete|
|`bot/core/broker.py` — IBKR connection via `ib_insync` (data + orders)      |✅ Complete|
|`bot/universe/` — scanner + criteria scoring + Claude selector              |✅ Complete|
|`bot/signals/indicators.py` — technical indicators via ta (5-min candles)   |✅ Complete|
|`bot/signals/generator.py` — 15-min filter + Claude signal pipeline         |✅ Complete|
|`bot/ml/` — LightGBM features, model, versioning, trainer                  |✅ Complete|
|`bot/risk/manager.py` — circuit breaker + position sizing (fixed/Kelly)     |✅ Complete|
|`bot/orders/executor.py` — IBKRBroker protocol + fill monitoring + fallback |✅ Complete|
|`bot/orders/eod_close.py` — EOD close all positions + P&L calculation       |✅ Complete|
|`bot/alerts/notifier.py` — email (SMTP/TLS) + HTTP webhook alerts           |✅ Complete|
|`web/api/auth.py` — JWT authentication with rate limiting                   |✅ Complete|
|`web/api/main.py` — 6 API endpoints (health, status, settings, logs, auth)  |✅ Complete|
|HTTPS / Let’s Encrypt — provisioned by setup.sh                             |✅ Complete|
|`tests/` — 255 tests (mocked IBKR, mocked Claude API, SQLite fixtures)      |✅ Complete|
|`web/api/` — trade history, performance, portfolio API endpoints            |🔲 To do   |
|`bot/backtesting/` — historical simulation engine                           |🔲 To do   |
|`bot/sentiment/` — news & sentiment analysis (Alpaca + Finnhub)             |🔲 To do   |
|`web/frontend/` — React management dashboard                               |🔲 To do   |
|`web/api/routes/audit.py` — configuration audit trail                       |🔲 To do   |
|`web/api/routes/costs.py` — Claude API + commission cost dashboard          |🔲 To do   |
|`web/api/routes/export.py` — trade export (CSV / Excel)                     |🔲 To do   |
|`bot/ml/ab_test.py` — A/B shadow model testing                             |🔲 To do   |
|2FA (TOTP) for web interface                                                 |🔲 To do   |
|Slippage & commission simulation in backtesting                              |🔲 To do   |

-----

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on setting up a development environment, the branching workflow, code style requirements, and how to report security vulnerabilities.

-----

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.

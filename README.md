# IBKR AI Trader

An open-source intraday trading bot for Interactive Brokers, powered by Claude AI. Focused exclusively on **stocks and ETFs** — positions are opened and closed within the same trading day, with no overnight exposure.

> ⚠️ **Work in progress** — This repository is in active development. The documentation, architecture, core logging module (`bot/utils/logger.py`), and server setup script (`deploy/setup.sh`) are complete. All remaining source code components are not yet implemented. See the [Development Status](#️-development-status) section for a full overview of what is done and what remains to be built.

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
- **News & sentiment analysis** — Real-time news is processed by Claude to gauge market sentiment
- **Explainability** — Every automated action, including universe selection, is explained in plain language by Claude and visible in the web interface

### Risk & safety

- **Risk management** — Claude advises on position sizing, stop-losses, and portfolio exposure
- **Position sizing model** — Configurable capital allocation per trade using fixed percentage, fixed amount, or Kelly Criterion with hard per-instrument capital limits
- **Circuit breaker** — Automatically halts trading when drawdown or loss thresholds are exceeded
- **Gap protection** — Instruments with extreme expected opening gaps are flagged or excluded during universe selection
- **Order fill monitoring** — Unfilled limit orders are automatically cancelled or converted to market orders after a configurable timeout

### Model & backtesting

- **Backtesting** — Test strategies against historical market data before going live
- **Model version control** — Every LightGBM retrain is versioned and stored; roll back to any previous model via the web interface
- **A/B model testing** — Run a new model version in paper trading alongside the live model before promoting it to production

### Monitoring & interface

- **Performance dashboard** — P&L charts, Sharpe ratio, win rate, max drawdown, and more
- **Cost dashboard** — Real-time overview of Claude API costs, IBKR commissions, and net P&L after all costs
- **Comprehensive logging** — DEBUG-level logs across all categories, written to disk and MariaDB
- **Alerting** — Real-time notifications via email on critical events
- **Webhook support** — Push events to any external system via configurable HTTP webhooks
- **Trade export** — Download full trade history as CSV or Excel from the web interface
- **Configuration audit trail** — Every change made via the web interface is logged with timestamp and user
- **Full web interface** — All settings and configuration managed via the browser; no SSH or config files needed after initial setup
- **Security** — HTTPS via Let’s Encrypt, login with session timeout, and optional two-factor authentication (2FA)

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
|Trading API         |IBKR official API (`ibapi`)                             |
|AI                  |Anthropic Claude API                                    |
|News & sentiment    |Alpaca News API (primary) / Finnhub (fallback)          |
|Signal model        |LightGBM (5-min candles, locally hosted)                |
|Technical indicators|pandas-ta                                               |
|Historical data     |IBKR Historical Data API (backtesting)                  |
|Process mgmt        |systemd                                                 |

-----

## Repository Structure

```
ibkr-ai-trader/
├── bot/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── trader.py               # Main trading loop
│   │   ├── connection.py           # IBKR API connection handler
│   │   ├── dry_run.py              # Dry run mode: full pipeline without order submission
│   │   └── watchdog.py             # Reconnect logic & process health monitoring
│   ├── universe/
│   │   ├── __init__.py
│   │   ├── scanner.py              # Scans IBKR universe (stocks & ETFs)
│   │   ├── selector.py             # Claude-powered instrument selection
│   │   └── approval.py             # Human-approval workflow (pending / approve / reject)
│   ├── signals/
│   │   ├── __init__.py
│   │   ├── generator.py            # Signal pipeline: LightGBM → 15-min confirmation → Claude
│   │   ├── sentiment.py            # News & sentiment analysis via Claude
│   │   └── indicators.py           # Technical indicator calculation via pandas-ta
│   ├── ml/
│   │   ├── __init__.py
│   │   ├── model.py                # LightGBM model wrapper (predict / load / save)
│   │   ├── trainer.py              # Model training & periodic retraining pipeline
│   │   ├── features.py             # Feature engineering (VWAP dev, volume ratio, RSI, etc.)
│   │   ├── versioning.py           # Model version registry, tagging & rollback
│   │   ├── ab_test.py              # A/B testing: paper vs live model comparison
│   │   └── models/                 # Saved & versioned model files (.lgbm)
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── manager.py              # Risk management & capital exposure enforcement
│   │   ├── position_sizer.py       # Kelly Criterion / fixed % / fixed amount position sizing
│   │   ├── gap_filter.py           # Earnings & extreme gap detection for universe filtering
│   │   └── circuit_breaker.py      # Drawdown & loss threshold enforcement
│   ├── orders/
│   │   ├── __init__.py
│   │   ├── executor.py             # Autonomous order execution (entry/adjust/exit)
│   │   ├── fill_monitor.py         # Order fill monitoring & timeout/cancel logic
│   │   └── eod_close.py            # End-of-day position close routine
│   ├── backtesting/
│   │   ├── __init__.py
│   │   ├── engine.py               # Backtesting engine (historical simulation)
│   │   ├── data_loader.py          # Load historical OHLCV data
│   │   └── report.py               # Backtest results & metrics
│   ├── alerts/
│   │   ├── __init__.py
│   │   ├── notifier.py             # Alert dispatcher
│   │   ├── email.py                # Email notifications
│   │   └── webhook.py              # HTTP webhook dispatcher for external integrations
│   └── utils/
│       ├── __init__.py
│       ├── logger.py               # Central logging — disk-first, async MariaDB flush
│       ├── calendar.py             # NYSE trading calendar & market hours validation
│       └── config.py
├── web/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app entrypoint
│   │   ├── routes/
│   │   │   ├── trades.py           # Trade history & open positions
│   │   │   ├── signals.py          # AI signal feed
│   │   │   ├── performance.py      # P&L, Sharpe ratio, drawdown metrics
│   │   │   ├── backtests.py        # Run & view backtest results
│   │   │   ├── health.py           # Health check endpoint (/health)
│   │   │   ├── logs.py             # Log viewer: filter, search, export
│   │   │   ├── config.py           # Bot configuration via UI
│   │   │   ├── audit.py            # Configuration change audit trail
│   │   │   ├── costs.py            # Claude API & IBKR commission cost dashboard
│   │   │   ├── export.py           # Trade history export (CSV / Excel)
│   │   │   └── auth.py             # Login / user management
│   │   └── models.py               # Pydantic request/response models
│   └── frontend/
│       ├── index.html              # Dashboard
│       ├── static/
│       └── templates/
├── db/
│   ├── models.py                   # SQLAlchemy ORM models (MariaDB)
│   ├── migrations/                 # Alembic database migrations
│   └── seed.py                     # Initial data / default config
├── deploy/
│   ├── nginx/
│   │   └── ibkr-trader.conf        # Nginx reverse proxy config
│   ├── systemd/
│   │   ├── ibkr-bot.service        # Systemd service: trading bot
│   │   └── ibkr-web.service        # Systemd service: web API
│   └── setup.sh                    # Ubuntu server setup script
├── config/
│   └── instruments.yaml            # Fallback/manual stock & ETF watchlist (used in manual mode)
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

The setup script installs Certbot and configures Nginx for **HTTPS only** from the start. HTTP (port 80) exists solely to redirect all traffic to HTTPS — no content is ever served over plain HTTP.

During setup you will be prompted for your domain name. Certbot then provisions a Let’s Encrypt certificate and configures Nginx automatically:

```
Nginx port 80  →  301 redirect to https://your-domain.com
Nginx port 443 →  HTTPS → FastAPI backend
```

Certificate renewal is handled automatically via a systemd timer installed by Certbot. No manual renewal is needed.

### API Rate Limiting

The FastAPI backend enforces rate limiting on all endpoints to protect against brute-force attacks on the login screen and excessive automated requests. Limits are applied per IP address:

|Endpoint           |Limit                |
|-------------------|---------------------|
|`POST /auth/login` |10 requests / minute |
|`GET /health`      |60 requests / minute |
|All other endpoints|120 requests / minute|

Exceeding the limit returns HTTP 429. Limits are configurable via the web interface under **Settings → Web & Security**.

### Two-Factor Authentication (2FA)

The web interface supports optional TOTP-based 2FA (compatible with Google Authenticator and similar apps). Enable it per user account in the web interface under Settings → Security. It is strongly recommended to enable 2FA when running in live trading mode.

### Session Timeout

Web sessions expire after **30 minutes** of inactivity. This is intentionally short given that the bot can place live trades. The timeout is configurable via the web interface under **Settings → Web & Security**.

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

|Step|What happens                                                                         |
|----|-------------------------------------------------------------------------------------|
|1   |System update (`apt update && apt upgrade`)                                          |
|2   |System packages: curl, git, build-essential, libssl-dev, etc.                        |
|3   |Python 3.11 via deadsnakes PPA                                                       |
|4   |MariaDB 10.11 — installed, secured, database and user created                        |
|5   |Nginx — installed and configured as HTTPS-only reverse proxy with WebSocket support  |
|5a  |Certbot & Let’s Encrypt — SSL certificate provisioned, HTTP→HTTPS redirect configured|
|6   |Node.js 20 for frontend tooling                                                      |
|7   |System user `trader` and application directories under `/opt/ibkr-trader`            |
|8   |Python virtual environment and all packages from `requirements.txt`                  |
|9   |`.env` file generated with random secrets pre-filled                                 |
|10  |Nginx site config enabled, default site disabled                                     |
|11  |Systemd services `ibkr-bot` and `ibkr-web` registered and enabled                    |
|12  |Log rotation configured (90-day retention)                                           |
|13  |UFW firewall — only SSH and HTTP/HTTPS allowed, everything else blocked              |
|14  |Fail2ban — brute-force protection on SSH and Nginx                                   |
|15  |Daily MariaDB backup cron job (30-day retention)                                     |

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

The web interface is now available at `https://your-domain.com`.

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

Signals are generated by a **LightGBM model** running locally on the server. Every 5 minutes it evaluates a feature vector built from pandas-ta indicators (VWAP deviation, volume ratio, RSI, MACD histogram, ATR, gap %, sector momentum, time of day) and outputs a directional prediction: long, short, or no trade. A **15-minute candle confirmation** check then filters out signals that run counter to the broader intraday trend — only signals where both timeframes agree are passed forward.

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

### 6 · Sentiment Analysis

Throughout the session, news headlines and articles for all instruments in the active universe are passed to Claude. Claude returns a sentiment score (positive / neutral / negative) and a short summary for each item. Sentiment feeds directly into signal generation and position management decisions.

**Explained in the web interface:** which news items were processed, the sentiment score assigned, and how sentiment influenced any trading decisions made around that time.

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

The calendar is provided by the `trading_calendars` Python library, which is kept up to date with official NYSE schedules. If the bot is started on a non-trading day, it logs the fact and enters idle state until the next market open.

-----

## Connection & Reliability

### Reconnect Logic

If the connection to IBKR (TWS or IB Gateway) is lost during a trading session, the bot does not crash — it enters a safe waiting state, cancels any pending orders it can no longer monitor, and attempts to reconnect automatically. Reconnect attempts follow an exponential backoff strategy (5s → 10s → 30s → 60s) up to a configurable maximum. If reconnection fails within the session, the circuit breaker is triggered and an email alert is sent.

Reconnect behaviour is configurable via the web interface under **Settings → Risk & Circuit Breaker**.

### Health Check Endpoint

The web API exposes a `/health` endpoint that returns the current status of all critical subsystems:

```json
{
  "status": "ok",
  "ibkr_connected": true,
  "claude_api_reachable": true,
  "db_connected": true,
  "bot_running": true,
  "trading_mode": "paper",
  "open_positions": 2,
  "last_signal_at": "2025-03-31T14:22:01Z"
}
```

This endpoint can be polled by an external monitoring tool (e.g. UptimeRobot, Grafana, or a simple cron job) to alert you if any subsystem goes down.

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

## Backtesting

Test your strategies on historical data before risking real capital:

```bash
cd /opt/ibkr-trader
source venv/bin/activate
python -m bot.backtesting.engine --instrument AAPL --start 2024-01-01 --end 2024-12-31 --timeframe 5min
```

Results are stored in MariaDB and viewable in the web interface, including P&L curve, trade log, Sharpe ratio, max drawdown, and win rate.

The backtesting engine simulates realistic trading conditions by accounting for IBKR commissions and slippage. These values are configurable via the web interface under **Settings → Backtesting**:

|Setting             |Default |Description                          |
|--------------------|--------|-------------------------------------|
|Commission per share|`$0.005`|IBKR Tiered rate, min $1.00 per order|
|Slippage ticks      |`1`     |Assumed ticks of slippage on fills   |

Backtest data also serves as the training dataset for the LightGBM model. The model is retrained periodically (default: weekly) to stay current with changing market conditions. Retraining can also be triggered manually from the web interface.

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

## Performance Dashboard

The web interface includes a live performance dashboard with:

- **P&L chart** — cumulative profit/loss over time (daily, weekly, monthly)
- **Win rate** — percentage of profitable trades
- **Sharpe ratio** — risk-adjusted return
- **Max drawdown** — largest peak-to-trough decline
- **Trade log** — full history with Claude’s reasoning per trade
- **Open positions** — live view of current holdings and unrealized P&L

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

### Searching Logs via Web Interface

The log viewer in the web interface supports:

- **Filter by category** — e.g. show only `trading` or `claude` logs
- **Filter by level** — DEBUG / INFO / WARNING / ERROR / CRITICAL
- **Full-text search** — search across all log messages
- **Time range filter** — narrow down to a specific window
- **Export** — download filtered results as CSV or plain text

### Log Retention

Logs older than the configured retention period (default: 90 days) are automatically purged from both disk and MariaDB. Retention is configurable via the web interface under **Settings → Logging**.

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

|Setting                   |Default   |Description                                      |
|--------------------------|----------|-------------------------------------------------|
|Trading mode              |`paper`   |`paper` · `live` · `dryrun`                      |
|Allow short selling       |`false`   |Requires IBKR margin account                     |
|Market open               |`09:30 ET`|NYSE/NASDAQ open time                            |
|Market close              |`16:00 ET`|Market close time                                |
|EOD close offset          |`15 min`  |Close all positions N minutes before market close|
|Max simultaneous positions|`5`       |Maximum open positions at any time               |

#### Position Sizing

|Setting                   |Default    |Description                           |
|--------------------------|-----------|--------------------------------------|
|Sizing method             |`fixed_pct`|`kelly` · `fixed_pct` · `fixed_amount`|
|Fixed percentage          |`2.0%`     |% of account per trade                |
|Fixed amount              |`$1,000`   |Dollar amount per trade               |
|Max capital per instrument|`10%`      |Hard cap per instrument               |
|Max total exposure        |`50%`      |Hard cap on total deployed capital    |

#### Universe Selection

|Setting                  |Default   |Description                               |
|-------------------------|----------|------------------------------------------|
|Selection mode           |`approval`|`autonomous` · `approval`                 |
|Max instruments          |`20`      |Maximum instruments in active universe    |
|Min average daily volume |`500,000` |Minimum volume filter                     |
|Max pre-market gap       |`5%`      |Exclude instruments gapping more than this|
|Exclude earnings day     |`true`    |Skip instruments reporting earnings today |
|Exclude next-day earnings|`false`   |Skip instruments reporting after close    |

#### Signal & Model

|Setting                     |Default   |Description                            |
|----------------------------|----------|---------------------------------------|
|LightGBM retraining interval|`weekly`  |`daily` · `weekly` · `monthly`         |
|Training lookback           |`6 months`|Historical data window for retraining  |
|Min training samples        |`5,000`   |Minimum samples required to retrain    |
|A/B test days               |`5`       |Trading days to run parallel paper test|
|Auto-promote model          |`false`   |Always require manual confirmation     |

#### Risk & Circuit Breaker

|Setting               |Default|Description                               |
|----------------------|-------|------------------------------------------|
|Max daily loss        |`5%`   |Halt trading if daily loss exceeds this   |
|Max drawdown          |`15%`  |Halt trading if drawdown exceeds this     |
|Reconnect max attempts|`10`   |Max IBKR reconnect attempts before halting|
|Reconnect alert after |`3`    |Send alert after this many failed attempts|

#### Orders

|Setting            |Default |Description                              |
|-------------------|--------|-----------------------------------------|
|Limit order timeout|`60 sec`|Cancel or convert after this many seconds|
|Timeout action     |`cancel`|`cancel` · `convert_to_market`           |

#### Backtesting

|Setting             |Default |Description                          |
|--------------------|--------|-------------------------------------|
|Commission per share|`$0.005`|IBKR Tiered rate, min $1.00 per order|
|Slippage ticks      |`1`     |Assumed ticks of slippage on fills   |

#### Logging

|Setting        |Default  |Description                           |
|---------------|---------|--------------------------------------|
|Log level      |`DEBUG`  |`DEBUG` · `INFO` · `WARNING` · `ERROR`|
|Log retention  |`90 days`|Auto-purge logs older than this       |
|Log to database|`true`   |Write logs to MariaDB                 |
|Log to file    |`true`   |Write logs to disk                    |

#### Alerting & Webhooks

|Setting            |Default         |Description                         |
|-------------------|----------------|------------------------------------|
|Alert email address|—               |Recipient for email notifications   |
|SMTP host          |`smtp.gmail.com`|SMTP server                         |
|SMTP port          |`587`           |SMTP port                           |
|SMTP username      |—               |SMTP login                          |
|Webhook enabled    |`false`         |Enable HTTP webhook notifications   |
|Webhook endpoints  |—               |URL, events, and secret per endpoint|

#### Web & Security

|Setting                     |Default  |Description                         |
|----------------------------|---------|------------------------------------|
|Session timeout             |`30 min` |Web session expiry after inactivity |
|Rate limit — login          |`10/min` |Max login attempts per IP per minute|
|Rate limit — other endpoints|`120/min`|General API rate limit per IP       |
|News provider               |`alpaca` |`alpaca` · `finnhub`                |

-----

## Data Sources

### News & Sentiment

The bot uses the **Alpaca News API** as the primary source for real-time news headlines and articles. **Finnhub** serves as a configurable fallback. Both providers are filtered to instruments in the active trading universe. Claude processes each news item and returns a sentiment score and plain-language summary that feeds into signal generation.

To use Alpaca News, a free Alpaca Markets account is sufficient — no funded brokerage account is required. Sign up at [alpaca.markets](https://alpaca.markets).

### Historical Data for Backtesting

Backtesting uses **IBKR’s Historical Data API**, which provides intraday OHLCV data (1-minute and 5-minute bars) for US stocks and ETFs. This data is pulled on demand during a backtest run and cached locally in MariaDB to avoid repeated API calls for the same date range.

IBKR imposes rate limits on historical data requests. The backtesting engine respects these limits automatically using a built-in request throttler.

The same historical data pipeline is used to retrain the LightGBM model. By default the model is retrained weekly using the most recent 6 months of 5-minute bar data. The retrain schedule is configurable via the web interface under **Settings → Signal & Model**.

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

Stocks with scheduled earnings announcements or analyst events during the session are flagged during universe selection. Instruments with a pre-market gap exceeding a configurable threshold are also filtered out, as extreme gaps reduce signal reliability and increase slippage risk.

These thresholds are configurable via the web interface under **Settings → Universe Selection**.

Flagged instruments are logged in `logs/universe.log` with the reason for exclusion and visible in the web interface universe scan results.

-----

## Model Version Control

Every time the LightGBM model is retrained, the new version is saved with a timestamp and performance metrics. Previous versions are never overwritten — they remain available for comparison and rollback.

### Version Registry

The web interface shows a table of all model versions:

|Version|Trained on|Sharpe (backtest)|Win rate|Status  |
|-------|----------|-----------------|--------|--------|
|v12    |2025-03-24|1.42             |58.3%   |**Live**|
|v11    |2025-03-17|1.38             |57.1%   |Archived|
|v10    |2025-03-10|1.21             |55.8%   |Archived|

### Rollback

Rolling back to a previous version takes effect immediately for the next trading session:

```bash
cd /opt/ibkr-trader
source venv/bin/activate
python -m bot.ml.versioning --rollback v11
```

Or via the web interface under Models → Version History → Activate.

-----

## A/B Model Testing

Before promoting a newly trained model to live trading, it can be run in parallel in paper trading mode while the current live model continues to trade with real orders. Both models generate signals independently — only the live model’s signals result in actual orders.

After a configurable number of trading days, the web interface shows a side-by-side performance comparison. You decide when and whether to promote the new model.

These settings are configurable via the web interface under **Settings → Signal & Model**.

-----

## Audit Trail

Every configuration change made via the web interface is recorded with the timestamp, the user who made the change, the field that was changed, and the old and new values.

Example audit log entry:

```
2025-03-31 09:14:22 | user: admin | MAX_DAILY_LOSS_PCT: 5.0 → 3.0
2025-03-31 09:15:01 | user: admin | UNIVERSE_MODE: approval → autonomous
2025-03-31 11:02:44 | user: admin | TRADING_MODE: paper → live
```

The audit trail is stored in MariaDB and searchable in the web interface under Settings → Audit Log. It cannot be deleted via the web interface.

-----

## Cost Dashboard

The web interface includes a real-time cost dashboard tracking all operational expenses:

|Cost type       |Source        |Tracked                                  |
|----------------|--------------|-----------------------------------------|
|IBKR commissions|Per trade fill|Per trade, daily, monthly                |
|Claude API usage|Per API call  |Token count, cost, daily total           |
|News API calls  |Per request   |Daily call count, within free tier or not|

Net P&L (gross P&L minus all costs) is shown on the performance dashboard alongside gross P&L so you always see what you actually earned.

-----

## Dry Run Mode

In dry run mode the full signal pipeline executes — universe selection, LightGBM predictions, 15-min confirmation, Claude reasoning, risk checks — but no orders are sent to IBKR. All decisions are logged and visible in the web interface exactly as in live or paper mode.

Dry run is useful for:

- Testing new code changes without needing an IBKR paper account
- Verifying that a configuration change produces the expected behaviour
- Demonstrating the bot’s decision-making without any capital at risk

Enable via the web interface under **Settings → Trading** by switching Trading Mode to `dryrun`.

-----

## Trade Export

The full trade history can be exported directly from the web interface under Performance → Export. Available formats:

|Format       |Contents                                                                        |
|-------------|--------------------------------------------------------------------------------|
|CSV          |All fields: timestamp, instrument, direction, entry, exit, P&L, Claude reasoning|
|Excel (.xlsx)|Same as CSV with pre-formatted columns and a summary sheet                      |

Exports can be filtered by date range, instrument, or trading mode (paper / live / dryrun) before downloading.

-----

## Webhook Support

In addition to email, the bot can send event notifications to any external system via HTTP webhooks. This allows integration with tools like Slack, Discord, custom dashboards, or any service that accepts POST requests.

Webhook endpoints are configured via the web interface under **Settings → Alerting & Webhooks**. Add one or more endpoints, select which events each should receive, and optionally set a secret for request verification.

Each webhook payload is a JSON object containing the event type, timestamp, and relevant data. A `X-Webhook-Secret` header is included for verification on the receiving end.

-----

## Position Sizing

Every trade is sized using one of three configurable strategies:

|Strategy      |Description                                                      |
|--------------|-----------------------------------------------------------------|
|`fixed_pct`   |A fixed percentage of total account value per trade (e.g. 2%)    |
|`fixed_amount`|A fixed dollar amount per trade (e.g. $1,000)                    |
|`kelly`       |Kelly Criterion based on model confidence and historical win rate|

All position sizing settings are configurable via the web interface under **Settings → Position Sizing**.
The `max_position_pct` cap is enforced as a hard limit regardless of which sizing strategy is active — Claude cannot override it.

-----

## Gap Protection

Instruments with extreme expected opening gaps are a risk during universe selection. A 15% gap up on an earnings surprise can make a stock untradeable for intraday purposes. The gap protection filter runs as part of universe scanning:

- Instruments with an absolute pre-market gap above a configurable threshold are automatically excluded from the active universe for that session
- Instruments approaching earnings dates (within a configurable window) are flagged in the approval workflow and excluded in autonomous mode
- All exclusions are logged in `logs/universe.log` with the reason

```yaml
universe:
  max_gap_pct: 8.0              # Exclude instruments with gap > 8%
  exclude_earnings_within_days: 2   # Exclude if earnings within 2 days
```

-----

## Model Version Control

Every time the LightGBM model is retrained, the new model is saved with a version number, timestamp, and performance metrics (accuracy, F1 score, backtest Sharpe ratio on the validation set). Previous versions are retained indefinitely.

### Rollback

If a newly deployed model underperforms, you can roll back to any previous version via the web interface under Models → Version History. The rollback takes effect at the start of the next trading session.

### A/B Testing

Before promoting a new model to production, run it in **shadow mode**: the new model generates predictions alongside the live model, but its signals are not acted upon. Performance is tracked in parallel for a configurable number of sessions. If the shadow model outperforms, promote it to live via the web interface.

```yaml
ml:
  ab_test_sessions: 5           # Run shadow model for 5 sessions before promoting
  promotion_threshold_sharpe: 0.1  # Shadow model must beat live by this margin
```

-----

## Configuration Audit Trail

Every change made via the web interface is recorded in the audit log:

- **Who** — the logged-in user
- **When** — timestamp with timezone
- **What** — the exact setting that was changed, old value and new value
- **Where** — the section of the configuration (risk, universe, intraday, etc.)

The audit log is stored in MariaDB and visible in the web interface under Settings → Audit Trail. It is read-only and cannot be edited or deleted via the interface.

-----

## Cost Dashboard

The web interface includes a real-time cost dashboard that tracks all operational expenses:

|Cost type       |Source           |Tracked as                                     |
|----------------|-----------------|-----------------------------------------------|
|Claude API      |Anthropic billing|Cost per call, tokens used, daily/monthly total|
|IBKR commissions|Trade fills      |Per trade, daily total, monthly total          |
|News API        |Alpaca / Finnhub |Requests used vs. plan limit                   |

The dashboard shows **gross P&L**, **total costs**, and **net P&L** side by side so you always know your actual return after expenses.

-----

## Dry Run Mode

Dry run mode executes the full trading pipeline — universe selection, signal generation, risk checks, order sizing — but does not submit any orders to IBKR. This is useful for:

- Testing new code without needing a paper trading account
- Validating configuration changes before applying them live
- Demonstrating the bot’s behaviour without financial exposure

Enable dry run mode via `.env` or the web interface:

```env
TRADING_MODE=dryrun    # paper | live | dryrun
```

In dry run mode, all actions are logged and explained exactly as they would be in live mode. The only difference is that the final IBKR order submission step is skipped.

-----

## Trade Export

Export your full trade history from the web interface under Performance → Export:

- **CSV** — compatible with Excel, Google Sheets, and any data analysis tool
- **Excel (.xlsx)** — formatted with summary statistics on a separate sheet

Each exported row includes: date, instrument, direction, entry price, exit price, quantity, gross P&L, commission, net P&L, hold time, signal source, and Claude’s reasoning summary.

-----

## Webhook Support

In addition to email, the bot can push events to any external system via HTTP webhooks. Configure webhook endpoints via the web interface under **Settings → Alerting & Webhooks**:

```yaml
alerts:
  webhooks:
    - url: https://your-system.com/ibkr-events
      events: [trade_opened, trade_closed, circuit_breaker, connection_lost]
      secret: your_webhook_secret    # HMAC-SHA256 signature for verification
```

Each webhook payload is a JSON object containing the event type, timestamp, and relevant data. The `secret` field enables the receiving system to verify that the request genuinely originated from the bot.

-----

## Disclaimer

This project is for **educational purposes only**. Trading financial instruments involves significant risk of loss. The authors are not responsible for any financial losses incurred through the use of this software. Always test thoroughly on a paper trading account before going live.

-----

## Development Status

This project is currently in the **documentation and architecture phase**. The table below tracks which components still need to be built:

|Component                                                           |Status    |
|--------------------------------------------------------------------|----------|
|README & architecture                                               |✅ Complete|
|`CLAUDE.md` — Claude Code instructions                              |✅ Complete|
|`bot/utils/logger.py` — disk-first async logging                    |✅ Complete|
|`deploy/setup.sh` — server setup script                             |✅ Complete|
|`requirements.txt`                                                  |🔲 To do   |
|`.gitignore`                                                        |🔲 To do   |
|`bot/core/` — trading loop & IBKR connection                        |🔲 To do   |
|`bot/universe/` — Claude-powered stock scanner                      |🔲 To do   |
|`bot/signals/` — signal pipeline (LightGBM → 15-min filter → Claude)|🔲 To do   |
|`bot/ml/` — LightGBM model, trainer, feature engineering            |🔲 To do   |
|`bot/risk/` — risk manager & circuit breaker                        |🔲 To do   |
|`bot/orders/` — order executor & EOD close                          |🔲 To do   |
|`bot/alerts/` — email notifications                                 |🔲 To do   |
|`bot/core/watchdog.py` — reconnect logic & health monitoring        |🔲 To do   |
|`bot/backtesting/` — backtesting engine                             |🔲 To do   |
|`web/api/` — FastAPI backend                                        |🔲 To do   |
|`web/frontend/` — management dashboard                              |🔲 To do   |
|`db/` — MariaDB models & migrations                                 |🔲 To do   |
|`deploy/nginx/` — Nginx config                                      |🔲 To do   |
|`deploy/systemd/` — systemd services                                |🔲 To do   |
|NYSE trading calendar                                               |🔲 To do   |
|Order fill monitoring & timeout logic                               |🔲 To do   |
|Slippage & commission simulation in backtesting                     |🔲 To do   |
|API rate limiting                                                   |🔲 To do   |
|`bot/risk/position_sizer.py` — position sizing model                |🔲 To do   |
|`bot/risk/gap_filter.py` — earnings & gap protection                |🔲 To do   |
|`bot/ml/versioning.py` — model version control & rollback           |🔲 To do   |
|`bot/ml/ab_test.py` — A/B model testing                             |🔲 To do   |
|`bot/alerts/webhook.py` — HTTP webhook dispatcher                   |🔲 To do   |
|`web/api/routes/audit.py` — configuration audit trail               |🔲 To do   |
|`web/api/routes/costs.py` — cost dashboard                          |🔲 To do   |
|`web/api/routes/export.py` — trade export (CSV/Excel)               |🔲 To do   |
|Dry run mode                                                        |🔲 To do   |
|HTTPS / Let’s Encrypt — direct from setup.sh                        |✅ Complete|
|2FA implementation                                                  |🔲 To do   |
|Position sizing model (fixed %, Kelly)                              |🔲 To do   |
|Per-instrument & per-sector capital caps                            |🔲 To do   |
|Gap protection filter                                               |🔲 To do   |
|LightGBM model version control & rollback                           |🔲 To do   |
|A/B model shadow testing                                            |🔲 To do   |
|Configuration audit trail                                           |🔲 To do   |
|Cost dashboard (Claude API + commissions)                           |🔲 To do   |
|Trade export (CSV / Excel)                                          |🔲 To do   |
|Webhook support                                                     |🔲 To do   |

-----

## Contributing

Contributions are welcome once the initial codebase is in place. For now, feel free to open an issue to discuss ideas, report design flaws, or suggest improvements to the architecture. See <CONTRIBUTING.md> for guidelines once available.

-----

## License

MIT License — see <LICENSE> for details.

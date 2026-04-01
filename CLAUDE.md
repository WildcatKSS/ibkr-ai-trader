# CLAUDE.md — ibkr-ai-trader

This file provides guidance for AI assistants (Claude and others) working on this repository. Read this before making any changes.

---

## Project Overview

**ibkr-ai-trader** is an AI-powered trading system that integrates with Interactive Brokers (IBKR) via the TWS API or IB Gateway. The goal is to automate trading decisions using AI/ML models while managing risk through programmatic controls.

> **Status**: Repository initialized. No source files committed yet. This document establishes the intended conventions and structure for the project.

---

## Repository Structure (Intended)

```
ibkr-ai-trader/
├── CLAUDE.md                  # This file
├── README.md                  # Human-facing project overview
├── .env.example               # Template for required environment variables
├── .gitignore                 # Must exclude .env, secrets, __pycache__, etc.
├── pyproject.toml             # Python project config (dependencies, tools)
├── requirements.txt           # Pinned runtime dependencies
├── requirements-dev.txt       # Dev/test dependencies
├── docker-compose.yml         # Local dev stack (IB Gateway, app, etc.)
├── Dockerfile                 # App container definition
│
├── src/
│   └── ibkr_ai_trader/
│       ├── __init__.py
│       ├── main.py            # Application entry point
│       ├── config.py          # Config loading from env vars
│       ├── broker/            # IBKR connection and order management
│       │   ├── __init__.py
│       │   ├── client.py      # IB API client wrapper
│       │   ├── orders.py      # Order placement and management
│       │   └── market_data.py # Real-time and historical data feeds
│       ├── strategy/          # Trading strategy implementations
│       │   ├── __init__.py
│       │   └── base.py        # Abstract strategy interface
│       ├── ai/                # AI/ML model integrations
│       │   ├── __init__.py
│       │   └── signal.py      # Signal generation from AI models
│       ├── risk/              # Risk management and position sizing
│       │   ├── __init__.py
│       │   └── manager.py     # Risk controls and limits
│       └── utils/             # Shared utilities
│           ├── __init__.py
│           └── logging.py     # Structured logging setup
│
├── tests/
│   ├── conftest.py            # Shared fixtures
│   ├── unit/                  # Unit tests (no external dependencies)
│   └── integration/           # Tests that require IB Gateway or mocks
│
├── scripts/                   # One-off utility scripts (not imported)
└── docs/                      # Extended documentation
```

---

## Technology Stack

- **Language**: Python 3.11+
- **IBKR API**: `ibapi` (Interactive Brokers official Python client) or `ib_insync`
- **AI/LLM**: Anthropic Claude API (via `anthropic` SDK) for trade signal analysis
- **Testing**: `pytest` with `pytest-asyncio` for async code
- **Linting**: `ruff` for linting and formatting
- **Type checking**: `mypy` in strict mode
- **Dependency management**: `pyproject.toml` + `pip` or `uv`
- **Containerization**: Docker + Docker Compose

---

## Development Workflow

### Setup

```bash
# Clone and enter repo
git clone <repo-url> && cd ibkr-ai-trader

# Create virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Copy and configure environment
cp .env.example .env
# Edit .env with your IBKR credentials and API keys
```

### Running Locally

```bash
# Start IB Gateway via Docker (paper trading port 4002)
docker-compose up -d ib-gateway

# Run the trader
python -m ibkr_ai_trader.main
```

### Tests

```bash
# Run all tests
pytest

# Run only unit tests (no IB connection needed)
pytest tests/unit/

# Run with coverage
pytest --cov=src/ibkr_ai_trader --cov-report=term-missing

# Run type checks
mypy src/

# Run linter
ruff check src/ tests/
ruff format src/ tests/
```

---

## Environment Variables

All secrets and configuration must come from environment variables. Never hardcode credentials.

| Variable | Required | Description |
|---|---|---|
| `IBKR_HOST` | Yes | TWS/Gateway host (default: `127.0.0.1`) |
| `IBKR_PORT` | Yes | TWS/Gateway port (live: `7496`, paper: `7497`, gateway: `4001`/`4002`) |
| `IBKR_CLIENT_ID` | Yes | Unique client ID for this connection |
| `ANTHROPIC_API_KEY` | Yes | Claude API key for AI signal generation |
| `TRADING_MODE` | Yes | `paper` or `live` — must be explicit |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING` (default: `INFO`) |
| `MAX_POSITION_SIZE` | No | Maximum position size in USD (risk guard) |
| `MAX_DAILY_LOSS` | No | Daily loss limit in USD before halting |

---

## Key Conventions

### Safety First — Trading-Specific Rules

1. **TRADING_MODE guard**: Every order placement must check `config.trading_mode`. Live trading must require explicit opt-in. Paper trading is the default.
2. **Risk manager is not optional**: No order may bypass `risk.manager.check()`. This is a hard architectural requirement.
3. **No market orders without confirmation logic**: Use limit orders by default to avoid slippage on automation.
4. **Idempotent order IDs**: Always generate deterministic order IDs to prevent duplicate fills on reconnect.
5. **Halt on breach**: If `MAX_DAILY_LOSS` is hit, the system must stop placing new orders for the remainder of the trading session.

### Code Style

- Follow PEP 8; enforced via `ruff`
- Use type annotations on all public functions and class attributes
- Prefer `dataclasses` or `pydantic` models over raw dicts for structured data
- Async code uses `asyncio`; avoid mixing sync/async carelessly
- Log with structured key=value pairs, not f-string concatenation

### Error Handling

- Never silently swallow exceptions in trading-critical paths
- IBKR API errors must be logged with the full error code and message
- Network disconnects must trigger reconnect logic, not crash the process
- All unhandled exceptions must halt trading (fail-safe, not fail-open)

### Testing

- Unit tests must not make real network calls — mock the IBKR client
- Integration tests are tagged `@pytest.mark.integration` and are skipped by default in CI
- Every strategy and risk rule must have unit test coverage
- Tests for order logic must verify both the happy path and rejection cases

### Commits and Branches

- Branch naming: `feature/<description>`, `fix/<description>`, `chore/<description>`
- Commit messages: imperative mood, present tense (e.g., `Add RSI strategy`, `Fix reconnect loop`)
- Do not commit `.env` files, secrets, or large data files
- Keep PRs focused; one logical change per PR

---

## External Integrations

### Interactive Brokers TWS / IB Gateway

- Use IB Gateway (not full TWS) for automated/headless deployments
- Paper trading account is at port `4002` (Gateway) or `7497` (TWS)
- Live trading account is at port `4001` (Gateway) or `7496` (TWS)
- The API requires TWS/Gateway to be running and logged in
- Client IDs must be unique per connection; conflicts cause silent failures

### Anthropic Claude API

- Used for interpreting market context, news sentiment, or strategy signals
- Always handle API rate limits and errors gracefully
- Do not send PII or actual account details to the Claude API
- Prompt templates should live in `src/ibkr_ai_trader/ai/prompts/`

---

## What AI Assistants Should NOT Do

- **Do not hardcode** API keys, account IDs, or credentials anywhere
- **Do not remove** risk manager checks or safety guards — ever
- **Do not change** `TRADING_MODE` defaults from `paper` to `live`
- **Do not add** untested order-placement code paths
- **Do not commit** changes that break existing tests
- **Do not introduce** dependencies without adding them to `pyproject.toml`
- **Do not use** `os.system()` or shell injection patterns; use `subprocess` with argument lists if shell execution is needed

---

## Adding New Features

When implementing a new trading strategy:
1. Create a new file under `src/ibkr_ai_trader/strategy/`
2. Subclass the abstract `BaseStrategy` from `strategy/base.py`
3. Implement `generate_signals()` and `on_bar()` methods
4. Add unit tests in `tests/unit/strategy/`
5. Register the strategy in `config.py` or a strategy registry

When integrating a new AI model or signal source:
1. Add the integration under `src/ibkr_ai_trader/ai/`
2. Define a typed interface for the signal output
3. Ensure the signal feeds into the existing risk-checked order flow
4. Document the signal schema in the module docstring

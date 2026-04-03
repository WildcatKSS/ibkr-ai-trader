# Contributing to IBKR AI Trader

Thank you for your interest in contributing. This document explains how to get involved, what to work on, and how to submit changes.

-----

## Before you start

This project is in active development — most source code components are not yet implemented. Check the [Development Status](README.md#️-development-status) table in the README before picking something up, so you don't duplicate work already in progress.

If you want to build something substantial (a new module, a change to the signal pipeline, a new risk model), **open an issue first** to discuss the approach. This avoids wasted effort if the direction doesn't fit the architecture.

-----

## What you can contribute

### Good first contributions
- Bug reports and bug fixes in existing files (`deploy/setup.sh`, `deploy/update.sh`, `bot/utils/logger.py`)
- Improvements to documentation (README, CLAUDE.md, inline comments)
- Missing test coverage for existing modules
- Security findings — see [Reporting a vulnerability](#reporting-a-vulnerability)

### Larger contributions
- Implementing modules listed as **🔲 To do** in the Development Status table
- Adding new features that align with the architecture described in `CLAUDE.md`
- Improvements to the backtesting engine or ML pipeline

-----

## Development setup

```bash
# 1. Fork the repository and clone your fork
git clone https://github.com/<your-username>/ibkr-ai-trader.git
cd ibkr-ai-trader

# 2. Create a virtual environment
python3.11 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy the environment file and fill in your keys
cp .env.example .env
# Edit .env and add your API keys

# 5. Run the tests
pytest tests/
```

You do not need a live IBKR account or a real Claude API key to run tests — all external calls are mocked.

-----

## Workflow

1. **Create a branch** from `main` using the correct prefix:
   - `feature/` — new functionality
   - `fix/` — bug fixes
   - `refactor/` — restructuring without behaviour change

   ```bash
   git checkout -b fix/eod-close-timeout
   ```

2. **Write tests** for every new function in `tests/`. Tests must never connect to real IBKR or call the real Claude API — use mocks.

3. **Run the tests** before committing:
   ```bash
   pytest tests/
   ```

4. **Follow the architecture rules** in `CLAUDE.md`. The most important ones:
   - No overnight positions — every code path that opens a position must be reachable by `eod_close.py`
   - Always validate `TRADING_MODE` before sending anything to IBKR
   - Never read secrets from anywhere except `.env`
   - Never use `print()` — use `bot/utils/logger.py`
   - Never call the Claude API from inside tight loops or `bot/ml/`

5. **Write a clear commit message** in English, present tense:
   ```
   Add fill monitor timeout logic
   Fix EOD close race condition on market holidays
   ```

6. **Open a pull request** against `main` with a description of what the change does and why.

-----

## Code style

- Python 3.11
- Follow existing conventions in the file you are editing
- No `print()` statements — use `get_logger()` from `bot/utils/logger.py`
- No `time.sleep()` in the trading loop — use async patterns
- No hardcoded secrets — all secrets come from `.env`
- Do not add dependencies without adding them to `requirements.txt`

-----

## Reporting a vulnerability

Do not open a public issue for security vulnerabilities. Instead, use [GitHub's private vulnerability reporting](https://github.com/WildcatKSS/ibkr-ai-trader/security/advisories/new).

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- A suggested fix if you have one

-----

## License

By contributing, you agree that your contributions will be licensed under the [GNU General Public License v3.0](LICENSE).

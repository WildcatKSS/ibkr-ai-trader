"""
Alembic migration environment.

Builds the database URL from environment variables so that credentials are
never stored in alembic.ini or committed to the repository.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Load .env so that DB_* variables are available when running migrations
# from the command line outside of the running application.
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# Alembic Config object — gives access to values in alembic.ini.
config = context.config

# Set up Python logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Build the database URL from environment variables and inject it.
_host = os.getenv("DB_HOST", "localhost")
_port = os.getenv("DB_PORT", "3306")
_name = os.getenv("DB_NAME", "ibkr_trader")
_user = os.getenv("DB_USER", "ibkr_trader")
_password = os.getenv("DB_PASSWORD", "")
config.set_main_option(
    "sqlalchemy.url",
    f"mysql+pymysql://{_user}:{_password}@{_host}:{_port}/{_name}?charset=utf8mb4",
)

# Import all models so Alembic can detect schema changes for autogenerate.
from db.models import Base  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live database connection (generates SQL only)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

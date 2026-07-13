"""Alembic environment — async engine, metadata from the app (CLAUDE.md §5).

Schema changes happen **only** through migrations (Golden Rule 5). ``target_metadata`` comes
from ``app.models`` (core + every enabled module), so autogenerate sees the whole schema.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db import Base

# Import side-effect: populates Base.metadata with all tables.
import app.models  # noqa: F401,E402

config = context.config

# NB: the URL is *not* pushed through ``set_main_option`` — Alembic's ConfigParser applies
# ``%`` interpolation, so a password containing a literal ``%`` (or other interpolation
# syntax) raises ``ValueError: invalid interpolation syntax``. We hand it straight to the
# engine below as a plain dict value, exactly like the app's own engine does.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Plain dict, not ``config.get_section`` — see the note by ``config`` above. The value is
    # a raw string here, so ConfigParser interpolation never touches the password.
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = settings.database_url
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

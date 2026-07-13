"""Alembic environment.

The URL comes from app settings, and is handed straight to the engine -- deliberately
never through `config.set_main_option`. Alembic's config is a ConfigParser, which treats
`%` as interpolation syntax, so a percent-encoded password (`%40` for `@`, which Supabase
passwords routinely need) makes it raise "invalid interpolation syntax" before it ever
reaches the database.

Building the engine from app.store.db also means migrations inherit the pooler handling
there: Supabase's transaction pooler on :6543 needs prepared statements off and no client
pool, and getting that wrong produces confusing failures rather than a clean error.
"""

from __future__ import annotations

from alembic import context

from app.config import settings
from app.store.db import engine
from app.store.tables import metadata

target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

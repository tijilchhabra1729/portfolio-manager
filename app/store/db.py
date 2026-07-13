"""Engine construction.

The only difference between local development and Supabase is DATABASE_URL. Same
Postgres, same dialect, same migrations -- so tests run offline against a container and
can never touch real holdings.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from app.config import settings
from app.store.tables import metadata

_engine: Engine | None = None

# Supabase's connection pooler (Supavisor) runs two modes on two ports, and they want
# opposite things from SQLAlchemy:
#
#   6543  transaction mode -- you get a different backend connection per transaction.
#         Server-side prepared statements therefore break (psycopg3 uses them by
#         default), and holding our own pool on top of theirs double-pools. So: no
#         client pool, no prepared statements.
#   5432  session mode -- a connection is yours for its lifetime. Normal pooling, and
#         prepared statements are fine. This is what a long-lived container wants.
TRANSACTION_POOLER_PORT = ":6543"


def _engine_options(url: str) -> dict[str, Any]:
    if TRANSACTION_POOLER_PORT in url:
        return {
            "poolclass": NullPool,
            "connect_args": {"prepare_threshold": None},
        }
    return {"pool_size": 5, "max_overflow": 5}


def engine() -> Engine:
    global _engine
    if _engine is None:
        url = settings().database_url
        _engine = create_engine(
            url,
            pool_pre_ping=True,  # the pooler drops idle connections
            future=True,
            **_engine_options(url),
        )
    return _engine


@contextmanager
def connect() -> Iterator[Connection]:
    """A transaction. Commits on clean exit, rolls back on any exception -- which is what
    makes 'validate the whole file, then commit, or write nothing' true."""
    with engine().begin() as connection:
        yield connection


def create_all() -> None:
    metadata.create_all(engine())


def drop_all() -> None:
    metadata.drop_all(engine())

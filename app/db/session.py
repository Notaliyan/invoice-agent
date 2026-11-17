"""Engine and session plumbing.

SQLite by default, so the project runs with no database to install. Point
DATABASE_URL at Postgres (`postgresql+psycopg://user:pass@host/db`) and the
same models and queries apply.

Tables are created on first use. There are no migrations yet: changing a model
means recreating the database or adding Alembic.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base

_engines: dict[str, Engine] = {}


def _create(url: str) -> Engine:
    parsed = make_url(url)
    kwargs: dict = {}
    if parsed.get_backend_name() == "sqlite":
        # FastAPI runs sync handlers on a thread pool.
        kwargs["connect_args"] = {"check_same_thread": False}
        if parsed.database in (None, "", ":memory:"):
            # An in-memory database exists per connection; share the one.
            kwargs["poolclass"] = StaticPool
        else:
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, **kwargs)

    if parsed.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_connection, _record) -> None:
            # SQLite ignores foreign keys unless asked, per connection.
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def get_engine(url: str) -> Engine:
    """One engine per database URL, with its tables created."""
    if url not in _engines:
        _engines[url] = _create(url)
    return _engines[url]


def dispose_engine(url: str) -> None:
    engine = _engines.pop(url, None)
    if engine is not None:
        engine.dispose()


@contextmanager
def session_scope(url: str) -> Iterator[Session]:
    """A transaction: commits on success, rolls back on any exception."""
    session = Session(get_engine(url), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

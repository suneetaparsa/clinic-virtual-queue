"""Database engine and session factory (SQLite for local use, PostgreSQL in production)."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

# One process owns the queue. Every state change (web, text, staff, scheduler)
# runs under this lock so two writers never interleave. A multi-instance
# deployment replaces this with a PostgreSQL advisory lock (see README).
queue_lock = threading.RLock()


@contextmanager
def session_scope() -> Iterator[Session]:
    with queue_lock:
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(engine)

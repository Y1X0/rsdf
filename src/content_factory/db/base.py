"""SQLAlchemy engine/session setup.

Design note: models deliberately avoid Postgres-only column features (native
ENUM types, ARRAY columns) — enums are stored as plain strings with a
`native_enum=False` SQLAlchemy Enum (VARCHAR + CHECK constraint) and list-like
fields use JSON. This keeps the exact same schema/migrations working against
both Postgres (production/dev, per ARCHITECTURE.md) and SQLite (test suite),
so tests run in-memory with zero external services while production still
gets a real relational database. Adding a native Postgres-only optimization
later (e.g. JSONB, pgvector) is an additive migration, not a rewrite.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from content_factory.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(settings.database_url, connect_args=connect_args, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

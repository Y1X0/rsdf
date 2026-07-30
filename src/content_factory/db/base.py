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

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from content_factory.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(
        settings.database_url,
        connect_args=connect_args,
        future=True,
        # Production Hardening Sprint H1: cheap, high-value fix — without
        # this, a connection that went stale while idle in the pool (DB
        # restart, managed-Postgres failover, a load balancer's idle
        # timeout) surfaces as a mid-request error instead of being
        # transparently recycled. A no-op extra round-trip on SQLite/in
        # normal operation, real protection the moment a connection
        # actually goes bad.
        pool_pre_ping=True,
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

# NOTE: the request-scoped session dependency lives in api/deps.py::get_db,
# not here — that one has the commit-on-success/rollback-on-error semantics
# every route actually needs. A duplicate, commit-less version used to live
# in this module too (dead code, never imported anywhere) and was removed
# during the Production Hardening Sprint specifically because its identical
# name made it a plausible, silent footgun for a future import mistake.

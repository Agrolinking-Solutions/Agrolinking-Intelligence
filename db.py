"""
Shared Postgres/TimescaleDB connection layer for api.py.

Unlike scripts/migration/migrate_to_postgres.py (which exits if no DB is
configured, because it has no other job to do), this module must NEVER
raise on import or make the API unusable when Postgres isn't configured or
isn't reachable — every endpoint that uses it is expected to catch a
connection/query failure and fall back to the existing file-based logic.
Postgres here is a performance/feature upgrade layered on top of a system
that already works without it, not a hard dependency.

Same connection precedence as migrate_to_postgres.py: discrete PGHOST/
PGPORT/PGUSER/PGPASSWORD/PGDATABASE/PGSSLMODE (no URL-encoding pitfalls)
take priority over TSDB_URL as a single connection string.

Usage:
    from db import db_available, get_conn

    if db_available():
        try:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT ...")
                ...
        except Exception:
            logger.warning("Postgres query failed, falling back to file read")
            # fall through to the existing file-based code path
"""

import os
import threading

try:
    import psycopg2
    from psycopg2 import pool as pg_pool
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

DB_URL = os.environ.get("TSDB_URL")
PG_DISCRETE_VARS_SET = (
    os.environ.get("PGHOST") is not None and os.environ.get("PGPASSWORD") is not None
)
CONFIGURED = PSYCOPG2_AVAILABLE and (bool(DB_URL) or PG_DISCRETE_VARS_SET)

_pool = None
_pool_lock = threading.Lock()
_pool_init_failed = False


def db_available() -> bool:
    """
    Cheap, no-network check: is there anything configured that would even
    let us try to connect? Does NOT verify the DB is actually reachable —
    callers must still handle connection/query failures themselves, since
    a briefly-unreachable DB shouldn't be distinguished from "not
    configured" by callers (both mean "use the file fallback").
    """
    return CONFIGURED and not _pool_init_failed


def _get_pool():
    global _pool, _pool_init_failed
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        try:
            if PG_DISCRETE_VARS_SET:
                _pool = pg_pool.SimpleConnectionPool(minconn=1, maxconn=5)
            else:
                _pool = pg_pool.SimpleConnectionPool(minconn=1, maxconn=5, dsn=DB_URL)
        except Exception:
            # Pool creation itself failed (bad credentials, unreachable host
            # at startup, etc.) — mark unavailable so db_available() short-
            # circuits future calls instead of retrying a broken pool on
            # every request.
            _pool_init_failed = True
            raise
        return _pool


class _PooledConnection:
    """Context manager: returns the connection to the pool on exit instead
    of closing it, and rolls back on error so a failed query never leaves
    a broken transaction sitting in the pool for the next borrower."""

    def __enter__(self):
        self._pool = _get_pool()
        self._conn = self._pool.getconn()
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self._pool.putconn(self._conn)
        return False


def get_conn():
    """Context manager yielding a pooled connection. Raises if not
    configured or if the pool can't be created — callers must wrap in
    try/except and fall back, per the module docstring."""
    if not CONFIGURED:
        raise RuntimeError("No database configured (set TSDB_URL or PGHOST/PGPASSWORD/...)")
    return _PooledConnection()

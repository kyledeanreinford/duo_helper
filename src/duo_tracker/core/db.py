"""SQLAlchemy engine helpers.

Raw SQL via `text()` rather than the ORM — the schema is one table and
the queries are explicit. For one-shot statements use `engine.begin()`.
Same pattern as monarch_helper's core/db.py.
"""

from sqlalchemy import create_engine

from duo_tracker.core.config import get_settings


def get_engine():
    return create_engine(get_settings().db_url, pool_pre_ping=True, future=True)

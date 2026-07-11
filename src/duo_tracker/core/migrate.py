"""Idempotent schema migrations, monarch_helper-style.

Convention: STATEMENTS is append-only. Add new statements at the bottom
with a dated comment; never edit existing ones. Everything must be safe
to re-run (IF NOT EXISTS / guarded DO blocks) — `migrate` runs on every
deploy.
"""

import logging

from sqlalchemy import text

from duo_tracker.core.db import get_engine

log = logging.getLogger(__name__)

STATEMENTS: list[str] = [
    # 2026-07-11 initial schema. Typed columns are a convenience projection;
    # raw_response is the source of truth (the course structure has changed
    # shape repeatedly — keep the full payload so metrics can be re-derived).
    """
    CREATE TABLE IF NOT EXISTS duolingo_daily_snapshot (
        id                      bigserial PRIMARY KEY,
        person                  text NOT NULL,
        snapshot_date           date NOT NULL,
        course_id               text NOT NULL,
        current_section         int,
        current_unit            int,
        units_completed         int,
        lessons_completed_today int,
        xp_today                int,
        streak_days             int,
        raw_response            jsonb,
        created_at              timestamptz NOT NULL DEFAULT now(),
        updated_at              timestamptz NOT NULL DEFAULT now(),
        UNIQUE (person, snapshot_date, course_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS duo_snapshot_person_date_idx
        ON duolingo_daily_snapshot (person, snapshot_date DESC)
    """,
]


def run_migrations() -> int:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
    log.info("migrations complete (%d statements)", len(STATEMENTS))
    return 0

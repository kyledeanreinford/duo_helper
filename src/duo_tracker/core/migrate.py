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
    # 2026-07-11 human-query view: everything except raw_response, which is
    # a multi-MB payload per row and floods any interactive SELECT *.
    """
    CREATE OR REPLACE VIEW snapshot_slim AS
        SELECT id, person, snapshot_date, course_id, current_section,
               current_unit, units_completed, lessons_completed_today,
               xp_today, streak_days,
               pg_column_size(raw_response) AS raw_bytes,
               created_at, updated_at
        FROM duolingo_daily_snapshot
    """,
    # 2026-07-13 course-scoped running totals (currentCourse.xp and summed
    # path finishedSessions). xp_summaries is account-wide — a chess day
    # polluted the lesson counts — so daily metrics are now deltas of these.
    """
    ALTER TABLE duolingo_daily_snapshot
        ADD COLUMN IF NOT EXISTS course_xp_total int,
        ADD COLUMN IF NOT EXISTS course_sessions_total int
    """,
]


def run_migrations() -> int:
    engine = get_engine()
    with engine.begin() as conn:
        for stmt in STATEMENTS:
            conn.execute(text(stmt))
    log.info("migrations complete (%d statements)", len(STATEMENTS))
    return 0

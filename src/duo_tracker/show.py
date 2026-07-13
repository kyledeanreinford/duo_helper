"""`duo-tracker show` — sanity-check query of recent snapshot rows."""

from sqlalchemy import text

from duo_tracker.core.db import get_engine


def run(person: str | None = None, days: int = 14) -> int:
    from rich.console import Console
    from rich.table import Table

    # xp_today is still collected (useful for debugging/reconstruction)
    # but not displayed — Kyle considers XP gamification, not progress.
    sql = """
        SELECT person, snapshot_date, course_id, current_section, current_unit,
               units_completed, lessons_completed_today, streak_days
        FROM duolingo_daily_snapshot
        WHERE snapshot_date >= CURRENT_DATE - :days
          AND (CAST(:person AS text) IS NULL OR person = CAST(:person AS text))
        ORDER BY person, snapshot_date DESC
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(sql), {"days": days, "person": person.lower() if person else None}
        ).fetchall()

    table = Table(title=f"duolingo_daily_snapshot (last {days} days)")
    for col in ["person", "date", "course", "section", "unit", "units done",
                "lessons today", "streak"]:
        table.add_column(col, no_wrap=col in ("date", "course"))
    for r in rows:
        table.add_row(*["" if v is None else str(v) for v in r])
    Console().print(table)
    if not rows:
        print("no rows — run `duo-tracker snapshot` first")
    return 0

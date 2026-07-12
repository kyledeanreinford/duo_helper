"""`duo-tracker web` — the pace log, one server-rendered page, no JS.

Shows, per person: the last 7 days, 7- and 30-day rolling lesson averages
(calendar windows — days without activity count as zero), and an estimate
of reaching the end of Section 4 (end of A2, unit 131) at each pace.

The A2 estimate is (remaining units × ASSUMED_LESSONS_PER_UNIT) divided by
the rolling lessons/day. The per-unit number is Kyle's working assumption
(24); as units complete on-record, the page also shows *observed*
lessons-per-unit derived from snapshot history, so the assumption can be
replaced by actuals once there's a unit or two of data. xp_summaries'
numSessions counts stories and radio sessions too — same caveat for both
the paces and the observations, stated on the page.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from duo_tracker.core.config import get_settings
from duo_tracker.core.db import get_engine
from duo_tracker.snapshot import local_today

log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI()

# End of A2 = everything through Section 4 (11 + 30 + 30 + 60 units).
A2_UNIT_TARGET = 131

# Kyle's working assumption until enough units complete on-record to use
# observed actuals (the path payload's own totalSessions implies ~23/unit
# through A2, so this is consistent). Revisit once observed data exists.
ASSUMED_LESSONS_PER_UNIT = 24


@dataclass(frozen=True)
class DayRow:
    day: date
    lessons: int | None   # None = no data for that day (vs a real 0)
    xp: int | None
    streak: int | None
    position: str | None  # "S2 · U9" on days with a full snapshot


@dataclass(frozen=True)
class PersonStats:
    person: str
    today: date
    streak: int | None
    position: str | None
    units_completed: int | None
    avg7: float
    avg30: float
    remaining_a2_lessons: int | None
    eta7: date | None
    eta30: date | None
    week: list[DayRow]
    observations: list["UnitObservation"]
    observed_per_unit: float | None


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    engine = get_engine()
    today = local_today(get_settings().timezone)
    with engine.connect() as conn:
        persons = [
            r[0] for r in conn.execute(text(
                "SELECT DISTINCT person FROM duolingo_daily_snapshot ORDER BY person"
            ))
        ]
        stats = [person_stats(conn, p, today) for p in persons]
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"stats": stats, "today": today},
    )


def person_stats(conn, person: str, today: date) -> PersonStats:
    rows = conn.execute(text("""
        SELECT snapshot_date, lessons_completed_today, xp_today, streak_days,
               current_section, current_unit, units_completed
        FROM duolingo_daily_snapshot
        WHERE person = :person AND snapshot_date > :floor
        ORDER BY snapshot_date DESC
    """), {"person": person, "floor": today - timedelta(days=31)}).fetchall()
    by_day = {r[0]: r for r in rows}

    def lessons_on(d: date) -> int:
        row = by_day.get(d)
        return (row[1] or 0) if row else 0

    avg7 = sum(lessons_on(today - timedelta(days=i)) for i in range(7)) / 7
    avg30 = sum(lessons_on(today - timedelta(days=i)) for i in range(30)) / 30

    week: list[DayRow] = []
    for i in range(7):
        d = today - timedelta(days=i)
        row = by_day.get(d)
        position = None
        if row and row[4] is not None and row[5] is not None:
            position = f"S{row[4]} · U{row[5]}"
        week.append(DayRow(
            day=d,
            lessons=row[1] if row else None,
            xp=row[2] if row else None,
            streak=row[3] if row else None,
            position=position,
        ))

    latest = rows[0] if rows else None
    remaining = remaining_a2_lessons(latest[6] if latest else None)

    obs_rows = conn.execute(text("""
        SELECT snapshot_date, lessons_completed_today, units_completed
        FROM duolingo_daily_snapshot
        WHERE person = :person
        ORDER BY snapshot_date ASC
    """), {"person": person}).fetchall()
    observations = observe_lessons_per_unit([tuple(r) for r in obs_rows])
    total_gained = sum(o.units_gained for o in observations)
    observed_per_unit = (
        sum(o.lessons_spent for o in observations) / total_gained if total_gained else None
    )
    return PersonStats(
        person=person,
        today=today,
        streak=latest[3] if latest else None,
        position=f"Section {latest[4]}, Unit {latest[5]}" if latest and latest[4] else None,
        units_completed=latest[6] if latest else None,
        avg7=avg7,
        avg30=avg30,
        remaining_a2_lessons=remaining,
        eta7=eta(today, remaining, avg7),
        eta30=eta(today, remaining, avg30),
        week=week,
        observations=observations[-6:],
        observed_per_unit=observed_per_unit,
    )


def eta(today: date, remaining: int | None, rate: float) -> date | None:
    if remaining is None or rate <= 0:
        return None
    return today + timedelta(days=round(remaining / rate))


def remaining_a2_lessons(units_completed: int | None) -> int | None:
    """Remaining lessons to end of A2 at the assumed lessons-per-unit rate."""
    if units_completed is None:
        return None
    return max(A2_UNIT_TARGET - units_completed, 0) * ASSUMED_LESSONS_PER_UNIT


@dataclass(frozen=True)
class UnitObservation:
    day: date          # the snapshot day the unit count ticked up
    units_gained: int
    lessons_spent: int  # lessons since the previous unit boundary
    per_unit: float


def observe_lessons_per_unit(rows: list) -> list[UnitObservation]:
    """Observed lessons-per-unit from snapshot history.

    rows: (snapshot_date, lessons, units_completed) ascending by date, only
    days where units_completed is known. Whenever units_completed increases,
    the lessons accumulated since the last increase are attributed to the
    units gained. Includes stories/radio sessions, same caveat as the paces.
    """
    observations: list[UnitObservation] = []
    prev_units: int | None = None
    lessons_acc = 0
    for day, lessons, units in rows:
        if units is None:
            continue
        if prev_units is None:
            prev_units = units
            continue
        lessons_acc += lessons or 0
        if units > prev_units:
            gained = units - prev_units
            observations.append(UnitObservation(
                day=day, units_gained=gained, lessons_spent=lessons_acc,
                per_unit=lessons_acc / gained,
            ))
            lessons_acc = 0
            prev_units = units
    return observations


def serve(host: str = "0.0.0.0", port: int = 8000) -> int:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0

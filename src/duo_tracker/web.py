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

import json
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
    anchor: date  # latest snapshot date — every window/table ends here, so a
                  # mid-day page view never shows an empty "today" as a zero
    streak: int | None
    position: str | None
    units_completed: int | None
    avg7: float
    avg30: float
    delta7: float          # change in 7-day pace vs one week earlier
    delta30: float         # change in 30-day pace vs one week earlier
    remaining_a2_lessons: int | None
    eta7: date | None
    eta30: date | None
    eta_shift_days: int | None  # ETA movement vs a week ago; negative = sooner
    week: list[DayRow]
    units: list["UnitSessions"]   # recent completed + in-progress units
    observed_per_unit: float | None


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/lisboa.apkg")
def anki_deck():
    """Always-current Anki deck, downloadable straight into AnkiMobile."""
    import tempfile

    from fastapi.responses import FileResponse

    from duo_tracker.anki_export import build_deck

    out = tempfile.NamedTemporaryFile(suffix=".apkg", delete=False)
    out.close()
    build_deck(out.name)
    return FileResponse(out.name, filename="lisboa.apkg",
                        media_type="application/octet-stream")


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
    """), {"person": person, "floor": today - timedelta(days=39)}).fetchall()
    course_raw = conn.execute(text("""
        SELECT raw_response->'user'->'currentCourse'
        FROM duolingo_daily_snapshot
        WHERE person = :person
          AND raw_response->'user'->'currentCourse' ? 'pathSectioned'
        ORDER BY snapshot_date DESC
        LIMIT 1
    """), {"person": person}).scalar()
    return compute_stats(person, [tuple(r) for r in rows], course_raw, today)


def compute_stats(person: str, rows: list, course_raw, today: date) -> PersonStats:
    """rows: snapshot tuples newest-first (see person_stats query)."""
    by_day = {r[0]: r for r in rows}
    # Anchor on the latest snapshot, not the calendar day: the nightly row
    # for "today" only exists after 23:59, and counting a not-yet-snapshotted
    # day as zero would drag every pace down all day long.
    anchor = rows[0][0] if rows else today

    def lessons_on(d: date) -> int:
        row = by_day.get(d)
        return (row[1] or 0) if row else 0

    def pace(window: int, end: date) -> float:
        return sum(lessons_on(end - timedelta(days=i)) for i in range(window)) / window

    week_ago = anchor - timedelta(days=7)
    avg7 = pace(7, anchor)
    avg30 = pace(30, anchor)
    delta7 = avg7 - pace(7, week_ago)
    delta30 = avg30 - pace(30, week_ago)

    week: list[DayRow] = []
    for i in range(7):
        d = anchor - timedelta(days=i)
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

    units: list[UnitSessions] = []
    if course_raw is not None:
        try:
            from duo_tracker.duo.models import CurrentCourse
            course = CurrentCourse.model_validate(
                course_raw if isinstance(course_raw, dict) else json.loads(course_raw))
            units = units_from_path(course)
        except Exception:
            log.exception("could not derive per-unit sessions from stored payload")
    # Ground-through units only: a tested-out unit (well under half its
    # sessions done) says nothing about how long a unit takes. Note that
    # normally-completed units finish at total-1 (one optional session per
    # unit), so the threshold must sit well below that.
    grind = [u for u in units if u.completed and u.total_sessions
             and u.sessions_done >= u.total_sessions * 0.5]
    observed_per_unit = (
        sum(u.sessions_done for u in grind) / len(grind) if grind else None
    )
    return PersonStats(
        person=person,
        anchor=anchor,
        streak=latest[3] if latest else None,
        position=f"Section {latest[4]}, Unit {latest[5]}" if latest and latest[4] else None,
        units_completed=latest[6] if latest else None,
        avg7=avg7,
        avg30=avg30,
        delta7=delta7,
        delta30=delta30,
        remaining_a2_lessons=remaining,
        eta7=eta(anchor, remaining, avg7),
        eta30=eta(anchor, remaining, avg30),
        eta_shift_days=eta_shift(anchor, remaining, avg7, avg7 - delta7),
        week=week,
        units=units[-8:],
        observed_per_unit=observed_per_unit,
    )


def eta(today: date, remaining: int | None, rate: float) -> date | None:
    if remaining is None or rate <= 0:
        return None
    return today + timedelta(days=round(remaining / rate))


def eta_shift(today: date, remaining: int | None, rate_now: float, rate_prev: float) -> int | None:
    """How the 7-day-pace ETA moved vs a week ago, in days (negative = sooner)."""
    now, prev = eta(today, remaining, rate_now), eta(today, remaining, rate_prev)
    if now is None or prev is None:
        return None
    return (now - prev).days


def remaining_a2_lessons(units_completed: int | None) -> int | None:
    """Remaining lessons to end of A2 at the assumed lessons-per-unit rate."""
    if units_completed is None:
        return None
    return max(A2_UNIT_TARGET - units_completed, 0) * ASSUMED_LESSONS_PER_UNIT


@dataclass(frozen=True)
class UnitSessions:
    label: str           # "S2 · U9" — matches the Duolingo UI
    objective: str | None
    sessions_done: int
    total_sessions: int
    completed: bool      # False = the currently-active unit, in progress


def units_from_path(course) -> list[UnitSessions]:
    """Per-unit session counts straight from the course path.

    finishedSessions is per-level and course-scoped, so this can't be
    polluted by other courses (chess...) or by how lessons fall across
    days. A completed unit with sessions_done well under its total was
    skipped / tested out of, not ground through — visible, not corrupting.
    """
    out: list[UnitSessions] = []
    sections = [s for s in (course.pathSectioned or []) if s.type in (None, "learning")]
    for s_idx, section in enumerate(sections):
        for u_idx, unit in enumerate(section.units or []):
            levels = unit.levels or []
            states = [lv.state for lv in levels if lv.state is not None]
            completed = bool(states) and all(st == "passed" for st in states)
            started = any(st in ("passed", "active") for st in states)
            if not started:
                continue
            out.append(UnitSessions(
                label=f"S{(section.index + 1) if section.index is not None else s_idx + 1}"
                      f" · U{u_idx + 1}",
                objective=unit.teachingObjective,
                sessions_done=sum(lv.finishedSessions or 0 for lv in levels),
                total_sessions=sum(lv.totalSessions or 0 for lv in levels),
                completed=completed,
            ))
    return out


def serve(host: str = "0.0.0.0", port: int = 8000) -> int:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0

"""`duo-tracker snapshot` — the daily job.

Fetch → build raw blob → reduce (best-effort) → upsert. The raw payload
is persisted even when reduction fails; a failure for one person never
stops the others, but any failure makes the exit code non-zero so a k8s
CronJob shows red.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

from duo_tracker.alerting import send_alert
from duo_tracker.core.config import DuoAccount, get_settings
from duo_tracker.core.db import get_engine
from duo_tracker.duo.auth import decode_user_id
from duo_tracker.duo.client import DuoClient
from duo_tracker.duo.models import UserPayload, XpSummariesPayload
from duo_tracker.duo.reduce import PathPosition, reduce_path
from duo_tracker.duo.trim import trim_user_payload

log = logging.getLogger(__name__)

UPSERT = """
INSERT INTO duolingo_daily_snapshot (
    person, snapshot_date, course_id, current_section, current_unit,
    units_completed, lessons_completed_today, xp_today, streak_days,
    course_xp_total, course_sessions_total, raw_response
) VALUES (
    :person, :snapshot_date, :course_id, :current_section, :current_unit,
    :units_completed, :lessons_completed_today, :xp_today, :streak_days,
    :course_xp_total, :course_sessions_total,
    CAST(:raw_response AS jsonb)
)
ON CONFLICT (person, snapshot_date, course_id) DO UPDATE SET
    current_section = EXCLUDED.current_section,
    current_unit = EXCLUDED.current_unit,
    units_completed = EXCLUDED.units_completed,
    lessons_completed_today = EXCLUDED.lessons_completed_today,
    xp_today = EXCLUDED.xp_today,
    streak_days = EXCLUDED.streak_days,
    course_xp_total = EXCLUDED.course_xp_total,
    course_sessions_total = EXCLUDED.course_sessions_total,
    raw_response = EXCLUDED.raw_response,
    updated_at = now()
"""

PREV_TOTALS = """
SELECT course_xp_total, course_sessions_total, raw_response->'user' AS user_payload
FROM duolingo_daily_snapshot
WHERE person = :person AND course_id = :course_id AND snapshot_date < :snapshot_date
ORDER BY snapshot_date DESC
LIMIT 1
"""


def run(person: str | None = None, date_str: str | None = None) -> int:
    settings = get_settings()
    try:
        accounts = settings.accounts()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    if person:
        accounts = [a for a in accounts if a.person == person.lower()]
        if not accounts:
            log.error("No configured account named %r (DUO_PEOPLE=%s)", person, settings.duo_people)
            return 1

    snapshot_date = (
        date.fromisoformat(date_str) if date_str else local_today(settings.timezone)
    )
    engine = get_engine()
    failures: list[tuple[str, str]] = []
    for account in accounts:
        try:
            snapshot_one(account, snapshot_date, engine)
        except Exception as exc:
            # Loud per-person failure; silent gaps in the data defeat the purpose.
            log.exception("SNAPSHOT FAILED for %s", account.person)
            failures.append((account.person, str(exc)))
    if failures:
        log.error("%d/%d snapshots failed", len(failures), len(accounts))
        send_alert(
            settings.alertmanager_url,
            summary=f"duo-tracker: snapshot failed for {', '.join(p for p, _ in failures)}",
            # The exception text carries the fix (e.g. the re-harvest-JWT
            # instructions from DuoAuthError) straight into Slack.
            description="\n".join(f"{p}: {msg}" for p, msg in failures),
        )
    return 1 if failures else 0


def local_today(tz_name: str) -> date:
    return datetime.now(tz=ZoneInfo(tz_name)).date()


def snapshot_one(account: DuoAccount, snapshot_date: date, engine) -> None:
    user_id = decode_user_id(account.jwt, account.person)
    with DuoClient(account.jwt, account.person) as client:
        user_raw = client.get_user(user_id)
        xp_raw = client.get_xp_summaries(user_id, snapshot_date - timedelta(days=7))

    raw_response = {
        # Trimmed to the path skeleton + streak/course identity (~100x
        # smaller than the raw response); xp_summaries kept whole (tiny).
        "user": trim_user_payload(user_raw),
        "xp_summaries": xp_raw,
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    # Reduction is best-effort: any failure still persists the raw payload.
    course_id = None
    position = PathPosition(None, None, None)
    streak_days = None
    xp_today = None
    lessons_today = None
    xp_total = None
    sessions_total = None
    try:
        user = UserPayload.model_validate(user_raw)
        course = user.currentCourse
        if course:
            course_id = course.id
            if course.learningLanguage and course.learningLanguage != "pt":
                log.warning(
                    "%s's active course is %s (not pt) — snapshotting it anyway",
                    account.person, course.learningLanguage,
                )
        position = reduce_path(course)
        streak_days = _streak_days(user)
        xp_total, sessions_total = course_totals(course)
    except Exception:
        log.exception("reduction failed for %s — persisting raw payload with NULL metrics", account.person)

    if course_id is None:
        # Course id is part of the unique key; fall back to the spec convention.
        course_id = "pt-en"

    # Daily metrics are deltas of the course-scoped running totals, NOT
    # xp_summaries — that endpoint is account-wide, and a day of chess
    # lessons polluted the Portuguese pace (2026-07-12). First-ever
    # snapshot has no baseline and falls back to the account-wide numbers.
    prev = _prev_totals(engine, account.person, course_id, snapshot_date)
    if prev is not None and xp_total is not None:
        xp_today, lessons_today = _delta_metrics(
            account.person, xp_total, sessions_total, prev)
    else:
        try:
            xp_today, lessons_today = _today_metrics(xp_raw, snapshot_date)
            log.info(
                "%s: no prior course totals — falling back to account-wide "
                "xp_summaries for %s", account.person, snapshot_date,
            )
        except Exception:
            log.exception("xp_summaries fallback failed for %s", account.person)

    with engine.begin() as conn:
        conn.execute(text(UPSERT), {
            "person": account.person,
            "snapshot_date": snapshot_date,
            "course_id": course_id,
            "current_section": position.current_section,
            "current_unit": position.current_unit,
            "units_completed": position.units_completed,
            "lessons_completed_today": lessons_today,
            "xp_today": xp_today,
            "streak_days": streak_days,
            "course_xp_total": xp_total,
            "course_sessions_total": sessions_total,
            "raw_response": json.dumps(raw_response),
        })
    log.info(
        "%s %s: section %s unit %s, %s units done, %s lessons / %s xp today, streak %s",
        account.person, snapshot_date, position.current_section, position.current_unit,
        position.units_completed, lessons_today, xp_today, streak_days,
    )


def course_totals(course) -> tuple[int | None, int | None]:
    """(course XP total, path sessions total) — both Portuguese-only.

    Sessions = summed finishedSessions across learning sections (daily
    refresh excluded, consistent with reduce_path). Counts path lessons,
    stories, and radio *in the path*; excludes freeform practice — which
    also makes the pace unit match the A2-remaining estimate's unit.
    """
    if course is None:
        return None, None
    sessions = None
    if course.pathSectioned:
        sessions = sum(
            level.finishedSessions or 0
            for section in course.pathSectioned
            if section.type in (None, "learning")
            for unit in (section.units or [])
            for level in (unit.levels or [])
        )
    return course.xp, sessions


def _prev_totals(engine, person: str, course_id: str, snapshot_date: date) -> tuple[int, int] | None:
    """Most recent prior (xp_total, sessions_total). Rows written before the
    totals columns existed still carry the payload — recompute from it."""
    with engine.connect() as conn:
        row = conn.execute(text(PREV_TOTALS), {
            "person": person, "course_id": course_id, "snapshot_date": snapshot_date,
        }).fetchone()
    if row is None:
        return None
    if row[0] is not None and row[1] is not None:
        return row[0], row[1]
    if row[2]:
        try:
            payload = row[2] if isinstance(row[2], dict) else json.loads(row[2])
            xp, sessions = course_totals(UserPayload.model_validate(payload).currentCourse)
            if xp is not None and sessions is not None:
                return xp, sessions
        except Exception:
            log.exception("could not derive prior course totals from stored payload")
    return None


def _delta_metrics(person: str, xp_total: int, sessions_total: int | None,
                   prev: tuple[int, int]) -> tuple[int, int | None]:
    prev_xp, prev_sessions = prev
    xp_today = xp_total - prev_xp
    lessons_today = sessions_total - prev_sessions if sessions_total is not None else None
    if xp_today < 0 or (lessons_today is not None and lessons_today < 0):
        # Totals went backwards — course reset or Duolingo reshuffle.
        log.warning(
            "%s: course totals decreased (xp %s, sessions %s) — clamping to 0",
            person, xp_today, lessons_today,
        )
        xp_today = max(xp_today, 0)
        lessons_today = max(lessons_today or 0, 0)
    return xp_today, lessons_today


def _streak_days(user: UserPayload) -> int | None:
    if user.streakData and user.streakData.currentStreak:
        length = user.streakData.currentStreak.length
        if length is not None:
            return length
    return user.streak


def _today_metrics(xp_raw: dict, snapshot_date: date) -> tuple[int, int]:
    """(xp_today, lessons_today) from the summary entry for snapshot_date.

    Entry `date` is midnight UTC of the summary day (confirmed live
    2026-07-11) — convert with UTC, not local time, or every entry lands
    a day early. No entry for the date = no activity yet = zeros.
    """
    payload = XpSummariesPayload.model_validate(xp_raw)
    for entry in payload.summaries or []:
        if entry.date is None:
            continue
        entry_day = datetime.fromtimestamp(entry.date, tz=timezone.utc).date()
        if entry_day == snapshot_date:
            return entry.gainedXp or 0, entry.numSessions or 0
    return 0, 0

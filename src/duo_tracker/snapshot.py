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
    units_completed, lessons_completed_today, xp_today, streak_days, raw_response
) VALUES (
    :person, :snapshot_date, :course_id, :current_section, :current_unit,
    :units_completed, :lessons_completed_today, :xp_today, :streak_days,
    CAST(:raw_response AS jsonb)
)
ON CONFLICT (person, snapshot_date, course_id) DO UPDATE SET
    current_section = EXCLUDED.current_section,
    current_unit = EXCLUDED.current_unit,
    units_completed = EXCLUDED.units_completed,
    lessons_completed_today = EXCLUDED.lessons_completed_today,
    xp_today = EXCLUDED.xp_today,
    streak_days = EXCLUDED.streak_days,
    raw_response = EXCLUDED.raw_response,
    updated_at = now()
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
    failures = 0
    for account in accounts:
        try:
            snapshot_one(account, snapshot_date, engine)
        except Exception:
            # Loud per-person failure; silent gaps in the data defeat the purpose.
            log.exception("SNAPSHOT FAILED for %s", account.person)
            failures += 1
    if failures:
        log.error("%d/%d snapshots failed", failures, len(accounts))
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
        xp_today, lessons_today = _today_metrics(xp_raw, snapshot_date)
    except Exception:
        log.exception("reduction failed for %s — persisting raw payload with NULL metrics", account.person)

    if course_id is None:
        # Course id is part of the unique key; fall back to the spec convention.
        course_id = "pt-en"

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
            "raw_response": json.dumps(raw_response),
        })
    log.info(
        "%s %s: section %s unit %s, %s units done, %s lessons / %s xp today, streak %s",
        account.person, snapshot_date, position.current_section, position.current_unit,
        position.units_completed, lessons_today, xp_today, streak_days,
    )


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

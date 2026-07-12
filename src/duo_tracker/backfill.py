"""`duo-tracker backfill` — fill past days' XP/lesson metrics from xp_summaries.

Only xp_today / lessons_completed_today / streak_days are recoverable
historically (xp_summaries is account-wide but reaches back years); path
position only exists as of the fetch, so those columns stay NULL and rows
are marked backfilled in raw_response. streak_days is only reconstructed
inside the current streak window (streakData gives its start date), where
"streak on day d" is exact arithmetic.

Existing rows are left alone: real daily snapshots win over backfill.
"""

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import text

from duo_tracker.core.config import get_settings
from duo_tracker.core.db import get_engine
from duo_tracker.duo.auth import decode_user_id
from duo_tracker.duo.client import DuoClient
from duo_tracker.duo.models import UserPayload, XpSummariesPayload, XpSummary
from duo_tracker.snapshot import local_today

log = logging.getLogger(__name__)

INSERT = """
INSERT INTO duolingo_daily_snapshot (
    person, snapshot_date, course_id, lessons_completed_today, xp_today,
    streak_days, raw_response
) VALUES (
    :person, :snapshot_date, :course_id, :lessons_completed_today, :xp_today,
    :streak_days, CAST(:raw_response AS jsonb)
)
ON CONFLICT (person, snapshot_date, course_id) DO NOTHING
"""


def run(since_str: str, person: str | None = None) -> int:
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

    since = date.fromisoformat(since_str)
    engine = get_engine()
    failures = 0
    for account in accounts:
        try:
            _backfill_one(account, since, engine)
        except Exception:
            log.exception("BACKFILL FAILED for %s", account.person)
            failures += 1
    return 1 if failures else 0


def _backfill_one(account, since: date, engine) -> None:
    user_id = decode_user_id(account.jwt, account.person)
    with DuoClient(account.jwt, account.person) as client:
        user_raw = client.get_user(user_id)
        streak_start, _ = _current_streak_window(user_raw)
        # Streak reconstruction needs every day of the current streak, not
        # just the requested window — a short --since otherwise sees one
        # extended day and reports streak 1 (bit us on 2026-07-12).
        fetch_from = min(since, streak_start) if streak_start else since
        xp_raw = client.get_xp_summaries(user_id, fetch_from)

    user = UserPayload.model_validate(user_raw)
    course_id = (user.currentCourse.id if user.currentCourse else None) or "pt-en"

    entries = [
        (datetime.fromtimestamp(e.date, tz=timezone.utc).date(), e)
        for e in (XpSummariesPayload.model_validate(xp_raw).summaries or [])
        if e.date is not None
    ]
    entries.sort(key=lambda pair: pair[0])

    streaks = reconstruct_streaks(entries, streak_start)

    today = local_today(get_settings().timezone)
    inserted = skipped = 0
    with engine.begin() as conn:
        for day, entry in entries:
            if day < since or day >= today:  # today belongs to `snapshot`
                continue
            streak_days = streaks.get(day)
            result = conn.execute(text(INSERT), {
                "person": account.person,
                "snapshot_date": day,
                "course_id": course_id,
                "lessons_completed_today": entry.numSessions or 0,
                "xp_today": entry.gainedXp or 0,
                "streak_days": streak_days,
                "raw_response": json.dumps({
                    "backfilled": True,
                    "xp_summaries_entry": entry.model_dump(),
                }),
            })
            inserted += result.rowcount
            skipped += 1 - result.rowcount
    log.info(
        "%s: backfilled %d days since %s (%d already had rows)",
        account.person, inserted, since, skipped,
    )


def reconstruct_streaks(
    entries: list[tuple[date, "XpSummary"]], streak_start: date | None
) -> dict[date, int]:
    """Streak count per day, walking forward from the current streak's start.

    Frozen days keep the running value without extending it — plain date
    arithmetic overcounts them (seen live: a 27-day window with length 25
    because of two streak freezes). `entries` must be sorted ascending.
    """
    streaks: dict[date, int] = {}
    running = 0
    for day, entry in entries:
        if streak_start and day >= streak_start:
            if entry.streakExtended:
                running += 1
            streaks[day] = running
    return streaks


def _current_streak_window(user_raw: dict) -> tuple[date | None, int]:
    current = ((user_raw.get("streakData") or {}).get("currentStreak") or {})
    start = current.get("startDate")
    length = current.get("length") or 0
    try:
        return (date.fromisoformat(start) if start else None), length
    except ValueError:
        return None, length

"""`duo-tracker probe` — the live-verification command.

Hits both reverse-engineered endpoints for each configured person, dumps
the raw JSON to data/, and prints a structural summary. Run this (and
eyeball the dumps) BEFORE trusting models.py/reduce.py — the endpoint
shapes are not official and have already shifted several times.
"""

import json
import logging
from datetime import date, datetime, timedelta

from duo_tracker.core.config import get_settings
from duo_tracker.duo.auth import decode_claims
from duo_tracker.duo.client import DuoApiError, DuoAuthError, DuoClient

log = logging.getLogger(__name__)


def run(person: str | None = None) -> int:
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

    settings.probe_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for account in accounts:
        try:
            _probe_one(account, settings.probe_dir)
        except (DuoAuthError, DuoApiError, ValueError) as exc:
            log.error("probe failed for %s: %s", account.person, exc)
            failures += 1
    return 1 if failures else 0


def _probe_one(account, out_dir) -> None:
    claims = decode_claims(account.jwt, account.person)
    user_id = int(claims["sub"])
    exp = claims.get("exp")
    exp_str = datetime.fromtimestamp(exp).isoformat() if exp else "none (no exp claim)"
    print(f"\n=== {account.person} ===")
    print(f"user_id (sub): {user_id}   jwt exp: {exp_str}   claims: {sorted(claims)}")
    if exp and datetime.fromtimestamp(exp) < datetime.now():
        log.warning("JWT for %s is EXPIRED per its exp claim — expect a 401", account.person)

    today = date.today().isoformat()
    with DuoClient(account.jwt, account.person) as client:
        # The fields filter is the flakiest part of this API surface: retry
        # without it if the filtered call comes back empty or missing the course.
        user = client.get_user(user_id)
        if not user or "currentCourse" not in user:
            log.warning("filtered user call missing currentCourse; retrying without fields param")
            user = client.get_user(user_id, fields=None)
        _dump(out_dir / f"{account.person}_user_{today}.json", user)
        _summarize_user(user)

        xp = client.get_xp_summaries(user_id, date.today() - timedelta(days=7))
        _dump(out_dir / f"{account.person}_xp_summaries_{today}.json", xp)
        _summarize_xp(xp)


def _dump(path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {path}")


def _summarize_user(user: dict) -> None:
    print(f"user top-level keys: {sorted(user)}")
    print(f"streak: {user.get('streak')}   totalXp: {user.get('totalXp')}")
    streak_data = user.get("streakData") or {}
    current = (streak_data.get("currentStreak") or {}) if isinstance(streak_data, dict) else {}
    print(f"streakData.currentStreak: {current}")

    course = user.get("currentCourse")
    if not isinstance(course, dict):
        print("!! no currentCourse object — reduction has nothing to work with")
        return
    print(
        f"currentCourse: id={course.get('id')} "
        f"learning={course.get('learningLanguage')} from={course.get('fromLanguage')} "
        f"keys={sorted(course)}"
    )
    path = course.get("pathSectioned")
    if not isinstance(path, list):
        print("!! currentCourse.pathSectioned missing or not a list")
        return
    print(f"pathSectioned: {len(path)} sections")
    for i, section in enumerate(path):
        units = section.get("units") or []
        print(
            f"  section[{i}]: keys={sorted(section)} "
            f"index={section.get('index')} units={len(units)}"
        )
        if i == 0 and units:
            unit = units[0]
            levels = unit.get("levels") or []
            states = [lv.get("state") for lv in levels if isinstance(lv, dict)]
            print(f"    unit[0]: keys={sorted(unit)} unitIndex={unit.get('unitIndex')}")
            print(f"    unit[0] level states: {states}")
            if levels:
                print(f"    level[0] keys: {sorted(levels[0])}")


def _summarize_xp(xp: dict) -> None:
    summaries = xp.get("summaries") if isinstance(xp, dict) else None
    if summaries is None:
        print(f"xp_summaries top-level keys: {sorted(xp) if isinstance(xp, dict) else type(xp)}")
        return
    print(f"xp_summaries: {len(summaries)} entries; last 3:")
    for entry in summaries[:3]:
        ts = entry.get("date")
        day = datetime.fromtimestamp(ts).date().isoformat() if isinstance(ts, (int, float)) else ts
        print(
            f"  {day}: gainedXp={entry.get('gainedXp')} numSessions={entry.get('numSessions')} "
            f"streakExtended={entry.get('streakExtended')} keys={sorted(entry)}"
        )

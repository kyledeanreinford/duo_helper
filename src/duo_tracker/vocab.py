"""`duo-tracker vocab` — sync learned vocabulary into duolingo_vocab.

The practice-hub learned-lexemes endpoint returns the words Duolingo
considers learned (text, translations, audio URL). It computes them from
the progressed skills we send, built from the UNTRIMMED user payload's
pathLevelClientData (the stored payload drops that, so this command does
its own fetch). first_seen/last_seen are ours: the endpoint has no
timestamps, so word-learn dates accrue from running this daily.
"""

import logging
from datetime import date

from sqlalchemy import text

from duo_tracker.core.config import get_settings
from duo_tracker.core.db import get_engine
from duo_tracker.duo.auth import decode_user_id
from duo_tracker.duo.client import DuoClient
from duo_tracker.snapshot import local_today

log = logging.getLogger(__name__)

UPSERT = """
INSERT INTO duolingo_vocab (person, course_id, lexeme, translations, audio_url,
                            first_seen, last_seen)
VALUES (:person, :course_id, :lexeme, CAST(:translations AS jsonb), :audio_url,
        :seen, :seen)
ON CONFLICT (person, course_id, lexeme) DO UPDATE SET
    translations = EXCLUDED.translations,
    audio_url = EXCLUDED.audio_url,
    last_seen = EXCLUDED.last_seen
"""


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

    engine = get_engine()
    today = local_today(settings.timezone)
    failures = 0
    for account in accounts:
        try:
            sync_one(account, engine, today)
        except Exception:
            log.exception("VOCAB SYNC FAILED for %s", account.person)
            failures += 1
    return 1 if failures else 0


def progressed_skills(course_raw: dict) -> list[dict]:
    """Aggregate per-skill progress from the untrimmed course payload —
    the learned-lexemes endpoint derives the vocab list from this."""
    skills: dict[str, dict] = {}
    for section in course_raw.get("pathSectioned") or []:
        for unit in section.get("units") or []:
            for level in unit.get("levels") or []:
                finished = level.get("finishedSessions") or 0
                if not finished:
                    continue
                client_data = level.get("pathLevelClientData") or {}
                ids = ([client_data.get("skillId")] if client_data.get("skillId")
                       else (client_data.get("skillIds") or []))
                for skill_id in ids:
                    entry = skills.setdefault(
                        skill_id, {"finishedLevels": 0, "finishedSessions": 0})
                    entry["finishedLevels"] += 1
                    entry["finishedSessions"] += finished
    return [
        {"skillId": {"id": skill_id}, **counts}
        for skill_id, counts in skills.items()
    ]


def sync_one(account, engine, today: date) -> None:
    import json

    user_id = decode_user_id(account.jwt, account.person)
    with DuoClient(account.jwt, account.person) as client:
        user = client.get_user(user_id)
        course = user.get("currentCourse") or {}
        learning = course.get("learningLanguage")
        from_lang = course.get("fromLanguage")
        course_id = course.get("id") or "pt-en"
        if not learning or not from_lang:
            log.warning("%s: no current course — skipping vocab sync", account.person)
            return
        skills = progressed_skills(course)

        lexemes: list[dict] = []
        start = 0
        while True:
            page = client.get_learned_lexemes(
                user_id, learning, from_lang, skills, start_index=start)
            lexemes.extend(page.get("learnedLexemes") or [])
            nxt = (page.get("pagination") or {}).get("nextStartIndex")
            if nxt is None:
                break
            start = nxt

    inserted = 0
    with engine.begin() as conn:
        for lx in lexemes:
            word = lx.get("text")
            if not word:
                continue
            result = conn.execute(text(UPSERT), {
                "person": account.person,
                "course_id": course_id,
                "lexeme": word,
                "translations": json.dumps(lx.get("translations") or []),
                "audio_url": lx.get("audioURL"),
                "seen": today,
            })
            # xmax=0 marks a fresh insert vs an update in PG's RETURNING-less
            # upsert; cheaper to just count via first_seen after the fact.
            inserted += result.rowcount
        new_today = conn.execute(text("""
            SELECT count(*) FROM duolingo_vocab
            WHERE person = :person AND course_id = :course_id AND first_seen = :seen
        """), {"person": account.person, "course_id": course_id, "seen": today}).scalar()
    log.info("%s: %d lexemes synced, %d first seen today", account.person, len(lexemes), new_today)


def show(person: str | None = None, limit: int = 30) -> int:
    from rich.console import Console
    from rich.table import Table

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT person, lexeme, translations->>0, first_seen
            FROM duolingo_vocab
            WHERE (CAST(:person AS text) IS NULL OR person = CAST(:person AS text))
            ORDER BY first_seen DESC, lexeme
            LIMIT :limit
        """), {"person": person.lower() if person else None, "limit": limit}).fetchall()
        total = conn.execute(text("""
            SELECT count(*) FROM duolingo_vocab
            WHERE (CAST(:person AS text) IS NULL OR person = CAST(:person AS text))
        """), {"person": person.lower() if person else None}).scalar()

    table = Table(title=f"vocabulary ({total} words; {limit} most recent)")
    for col in ["person", "word", "translation", "first seen"]:
        table.add_column(col)
    for r in rows:
        table.add_row(*["" if v is None else str(v) for v in r])
    Console().print(table)
    return 0

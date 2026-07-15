"""European-Portuguese layer: classify learned words and seed the slang deck.

Duolingo teaches Brazilian Portuguese; Kyle is going to Lisbon. Claude
classifies each learned lexeme as a *lexical swap* (everyday Lisbon usage
prefers a different word — ônibus→autocarro) or not. Scope is deliberately
narrow: pronunciation/register differences don't count (Kyle's call).

Both commands are no-ops without ANTHROPIC_API_KEY so the nightly cron can
include them before the key is configured in the duo secret.
"""

import json
import logging
from datetime import date

from sqlalchemy import text

from duo_tracker.core.config import get_settings
from duo_tracker.core.db import get_engine
from duo_tracker.snapshot import local_today

log = logging.getLogger(__name__)

BATCH_SIZE = 40

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "word": {"type": "string"},
                    "differs": {"type": "boolean"},
                    "ep_word": {"type": ["string", "null"]},
                },
                "required": ["word", "differs", "ep_word"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["words"],
    "additionalProperties": False,
}

CLASSIFY_SYSTEM = """\
You are a Portuguese dialectology expert. You will receive vocabulary a
learner knows from Duolingo's Brazilian Portuguese course. The learner is
traveling to Lisbon.

For each word, decide whether everyday European Portuguese (Lisbon) commonly
uses a DIFFERENT WORD for the same meaning — a lexical swap like
ônibus→autocarro, trem→comboio, café da manhã→pequeno-almoço, celular→telemóvel.

Mark differs=true ONLY for genuine lexical swaps in everyday usage. Do NOT
mark words that merely differ in pronunciation, spelling reform details,
register, or frequency — if a Lisboeta would naturally use the same word,
differs=false. When differs=true, ep_word is the European Portuguese word;
otherwise ep_word is null. Return every input word exactly as given."""

SLANG_SYSTEM = """\
You are a Portuguese dialectology expert preparing flashcards for a
Duolingo Brazilian-Portuguese learner (currently ~A1, working toward A2)
visiting Lisbon. Produce the European Portuguese slang, colloquialisms, and
everyday Europeanisms that Duolingo will never teach but that they will hear
or need constantly in Lisbon: greetings/fillers (pá, fixe, giro, bué,
está-se bem), café and restaurant culture (bica, imperial, galão,
se faz favor, a conta), transit, shopping, courtesy formulas, and common
exclamations. Prefer high-frequency, current usage; avoid dated or vulgar
slang except the genuinely unavoidable. Keep notes short and practical."""

SLANG_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "translation": {"type": "string"},
                    "note": {"type": "string"},
                    "category": {"type": "string"},
                },
                "required": ["term", "translation", "note", "category"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["entries"],
    "additionalProperties": False,
}


def _client():
    import anthropic

    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


def parse_classifications(payload: dict, expected: set[str]) -> dict[str, tuple[bool, str | None]]:
    """{word: (differs, ep_word)} for words we actually asked about."""
    out: dict[str, tuple[bool, str | None]] = {}
    for item in payload.get("words", []):
        word = item.get("word")
        if word in expected:
            differs = bool(item.get("differs"))
            ep_word = item.get("ep_word") if differs else None
            out[word] = (differs, ep_word or None)
    return out


def classify(person: str | None = None) -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not set — skipping EP classification")
        return 0
    engine = get_engine()
    today = local_today(settings.timezone)

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT lexeme, translations->>0
            FROM duolingo_vocab
            WHERE ep_differs IS NULL
              AND (CAST(:person AS text) IS NULL OR person = CAST(:person AS text))
            ORDER BY lexeme
        """), {"person": person.lower() if person else None}).fetchall()
    if not rows:
        log.info("no unclassified vocabulary")
        return 0
    log.info("classifying %d words for EP differences (%s)", len(rows), settings.anthropic_model)

    client = _client()
    classified = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        expected = {r[0] for r in batch}
        word_list = "\n".join(f"- {r[0]} ({r[1] or 'no gloss'})" for r in batch)
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=16000,
            system=CLASSIFY_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
            messages=[{"role": "user", "content": word_list}],
        )
        if response.stop_reason == "refusal":
            log.warning("classification batch refused — skipping %d words", len(batch))
            continue
        payload = json.loads(next(b.text for b in response.content if b.type == "text"))
        results = parse_classifications(payload, expected)
        with engine.begin() as conn:
            for word, (differs, ep_word) in results.items():
                conn.execute(text("""
                    UPDATE duolingo_vocab
                    SET ep_differs = :differs, ep_variant = :ep_word, classified_at = :today
                    WHERE lexeme = :word
                """), {"differs": differs, "ep_word": ep_word, "today": today, "word": word})
        classified += len(results)
        missing = expected - set(results)
        if missing:
            log.warning("batch response missing %d words (stay unclassified): %s",
                        len(missing), sorted(missing)[:5])
    swaps = _count_swaps(engine)
    log.info("classified %d words; %d lexical swaps known so far", classified, swaps)
    return 0


def _count_swaps(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT count(DISTINCT lexeme) FROM duolingo_vocab WHERE ep_differs")
        ).scalar()


def seed_slang(force: bool = False) -> int:
    settings = get_settings()
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY not set — skipping slang seed")
        return 0
    engine = get_engine()
    with engine.connect() as conn:
        existing = conn.execute(text("SELECT count(*) FROM lisbon_slang")).scalar()
    if existing and not force:
        log.info("lisbon_slang already has %d entries (use --force to regenerate)", existing)
        return 0

    client = _client()
    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=SLANG_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": SLANG_SCHEMA}},
        messages=[{"role": "user", "content":
                   "Produce 60-80 flashcard entries per the instructions."}],
    )
    if response.stop_reason == "refusal":
        log.error("slang generation refused")
        return 1
    payload = json.loads(next(b.text for b in response.content if b.type == "text"))
    entries = payload.get("entries", [])
    today = date.today()
    with engine.begin() as conn:
        if force:
            conn.execute(text("DELETE FROM lisbon_slang"))
        for e in entries:
            if not e.get("term"):
                continue
            conn.execute(text("""
                INSERT INTO lisbon_slang (term, translation, note, category, added)
                VALUES (:term, :translation, :note, :category, :added)
                ON CONFLICT (term) DO UPDATE SET
                    translation = EXCLUDED.translation,
                    note = EXCLUDED.note,
                    category = EXCLUDED.category
            """), {**{k: e.get(k) for k in ("term", "translation", "note", "category")},
                   "added": today})
    log.info("seeded %d Lisbon slang entries", len(entries))
    return 0

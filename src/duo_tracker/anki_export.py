"""Build the Lisbon Anki deck (.apkg) from the vocab + slang tables.

Two subdecks:
- "Português de Lisboa::EP variants" — words Kyle knows in BP whose Lisbon
  form differs (front: BP word + gloss, back: the EP word).
- "Português de Lisboa::Lisbon slang" — Europeanisms Duolingo never teaches.

Note GUIDs are derived from the lexeme/term only, so re-importing a newer
export updates existing cards instead of duplicating them.
"""

import logging

from sqlalchemy import text

from duo_tracker.core.db import get_engine

log = logging.getLogger(__name__)

# Fixed genanki ids — changing them would orphan existing cards on re-import.
DECK_EP_ID = 1720260714
DECK_SLANG_ID = 1720260715
MODEL_ID = 1720260716

_CARD_CSS = """
.card { font-family: Georgia, serif; font-size: 26px; text-align: center;
        color: #16355c; background-color: #f6f1e5; }
.gloss { font-size: 16px; color: #5e7190; }
.note  { font-size: 16px; color: #5e7190; margin-top: 12px; }
.tag   { font-size: 13px; color: #b3541e; text-transform: uppercase;
         letter-spacing: 2px; margin-top: 16px; }
"""


def build_deck(out_path: str) -> int:
    import genanki

    model = genanki.Model(
        MODEL_ID, "Lisboa card",
        fields=[{"name": "Front"}, {"name": "FrontGloss"},
                {"name": "Back"}, {"name": "Note"}, {"name": "Tag"}],
        templates=[{
            "name": "Card",
            "qfmt": "{{Front}}<div class='gloss'>{{FrontGloss}}</div>"
                    "<div class='tag'>{{Tag}}</div>",
            "afmt": "{{FrontSide}}<hr id='answer'>{{Back}}"
                    "<div class='note'>{{Note}}</div>",
        }],
        css=_CARD_CSS,
    )

    class LisboaNote(genanki.Note):
        @property
        def guid(self):
            # Stable per word so re-imports update, never duplicate.
            return genanki.guid_for(self.fields[4], self.fields[0])

    engine = get_engine()
    with engine.connect() as conn:
        variants = conn.execute(text("""
            SELECT DISTINCT lexeme, translations->>0, ep_variant
            FROM duolingo_vocab
            WHERE ep_differs AND ep_variant IS NOT NULL
            ORDER BY lexeme
        """)).fetchall()
        slang = conn.execute(text("""
            SELECT term, translation, note, category FROM lisbon_slang ORDER BY category, term
        """)).fetchall()

    ep_deck = genanki.Deck(DECK_EP_ID, "Português de Lisboa::EP variants")
    for lexeme, gloss, ep_word in variants:
        ep_deck.add_note(LisboaNote(model=model, fields=[
            lexeme, f"({gloss}) — how do they say it in Lisbon?",
            ep_word, "", "ep-variant",
        ]))

    slang_deck = genanki.Deck(DECK_SLANG_ID, "Português de Lisboa::Lisbon slang")
    for term, translation, note, category in slang:
        slang_deck.add_note(LisboaNote(model=model, fields=[
            term, category or "", translation or "", note or "", "slang",
        ]))

    genanki.Package([ep_deck, slang_deck]).write_to_file(out_path)
    log.info("wrote %s: %d EP-variant cards, %d slang cards",
             out_path, len(variants), len(slang))
    if not variants and not slang:
        log.warning("deck is empty — run `duo-tracker vocab`, `ep-classify`, and `slang-seed` first")
    return 0

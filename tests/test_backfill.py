from datetime import date

from duo_tracker.backfill import reconstruct_streaks
from duo_tracker.duo.models import XpSummary


def entry(extended: bool, frozen: bool = False) -> XpSummary:
    return XpSummary.model_validate({"streakExtended": extended, "frozen": frozen})


def test_frozen_days_hold_the_streak():
    # Live pattern from 2026-06: two freeze days early in a streak.
    days = [
        (date(2026, 6, 15), entry(True)),
        (date(2026, 6, 16), entry(True)),
        (date(2026, 6, 17), entry(False, frozen=True)),
        (date(2026, 6, 18), entry(False, frozen=True)),
        (date(2026, 6, 19), entry(True)),
    ]
    streaks = reconstruct_streaks(days, streak_start=date(2026, 6, 15))
    assert streaks == {
        date(2026, 6, 15): 1,
        date(2026, 6, 16): 2,
        date(2026, 6, 17): 2,
        date(2026, 6, 18): 2,
        date(2026, 6, 19): 3,
    }


def test_days_before_streak_start_get_no_value():
    days = [
        (date(2026, 6, 10), entry(True)),   # a previous, broken streak
        (date(2026, 6, 15), entry(True)),
    ]
    streaks = reconstruct_streaks(days, streak_start=date(2026, 6, 15))
    assert date(2026, 6, 10) not in streaks
    assert streaks[date(2026, 6, 15)] == 1


def test_no_streak_start_means_no_values():
    assert reconstruct_streaks([(date(2026, 6, 15), entry(True))], None) == {}

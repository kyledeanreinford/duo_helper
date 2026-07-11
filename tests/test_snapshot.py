import json
from datetime import date
from pathlib import Path

from duo_tracker.snapshot import _streak_days, _today_metrics
from duo_tracker.duo.models import UserPayload

FIXTURES = Path(__file__).parent / "fixtures"


def load_xp() -> dict:
    return json.loads((FIXTURES / "xp_summaries.json").read_text())


def test_today_metrics_uses_utc_dates():
    # The newest entry's epoch is 2026-07-11T00:00:00Z, which is 2026-07-10
    # in America/Chicago — the UTC conversion must win.
    xp, lessons = _today_metrics(load_xp(), date(2026, 7, 11))
    assert (xp, lessons) == (648, 10)


def test_today_metrics_other_day():
    xp, lessons = _today_metrics(load_xp(), date(2026, 7, 9))
    assert (xp, lessons) == (164, 4)


def test_today_metrics_no_entry_means_zeros():
    xp, lessons = _today_metrics(load_xp(), date(2030, 1, 1))
    assert (xp, lessons) == (0, 0)


def test_today_metrics_garbage_payload():
    assert _today_metrics({}, date(2026, 7, 11)) == (0, 0)
    assert _today_metrics({"summaries": [{"weird": True}]}, date(2026, 7, 11)) == (0, 0)


def test_streak_prefers_streak_data():
    user = UserPayload.model_validate({
        "streak": 3,
        "streakData": {"currentStreak": {"length": 25}},
    })
    assert _streak_days(user) == 25


def test_streak_falls_back_to_top_level():
    assert _streak_days(UserPayload.model_validate({"streak": 3})) == 3
    assert _streak_days(UserPayload.model_validate({})) is None

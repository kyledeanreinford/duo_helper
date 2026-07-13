import json
from pathlib import Path

from duo_tracker.duo.models import CurrentCourse, UserPayload
from duo_tracker.snapshot import _delta_metrics, course_totals

FIXTURES = Path(__file__).parent / "fixtures"


def test_totals_from_real_fixture():
    user = UserPayload.model_validate(json.loads((FIXTURES / "user_payload.json").read_text()))
    xp, sessions = course_totals(user.currentCourse)
    assert xp == 16035  # currentCourse.xp at capture time
    # Fixture keeps units for the first three sections; all of section 1's
    # 11 units were passed, plus 8+ units into section 2 — a real number.
    assert sessions and sessions > 100


def test_totals_exclude_daily_refresh():
    course = CurrentCourse.model_validate({
        "xp": 500,
        "pathSectioned": [
            {"type": "learning", "units": [{"levels": [{"finishedSessions": 3}]}]},
            {"type": "daily_refresh", "units": [{"levels": [{"finishedSessions": 9}]}]},
        ],
    })
    assert course_totals(course) == (500, 3)


def test_totals_missing_path():
    assert course_totals(None) == (None, None)
    assert course_totals(CurrentCourse(xp=42)) == (42, None)


def test_delta_metrics_chess_immune():
    # Chess XP moves account-wide numbers but not the course totals.
    assert _delta_metrics("kyle", xp_total=17066, sessions_total=210, prev=(16035, 193)) == (1031, 17)


def test_delta_metrics_clamps_resets():
    assert _delta_metrics("kyle", xp_total=100, sessions_total=5, prev=(16035, 193)) == (0, 0)

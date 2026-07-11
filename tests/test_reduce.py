import json
from pathlib import Path

from duo_tracker.duo.models import CurrentCourse, UserPayload
from duo_tracker.duo.reduce import reduce_path

FIXTURES = Path(__file__).parent / "fixtures"


def load_user() -> UserPayload:
    return UserPayload.model_validate(json.loads((FIXTURES / "user_payload.json").read_text()))


def test_real_payload_position():
    # Live state at capture time (2026-07-11): intro section done (11 units),
    # 8 of 30 done in the A1 section -> Section 2, Unit 9, 19 units total.
    pos = reduce_path(load_user().currentCourse)
    assert pos.current_section == 2
    assert pos.current_unit == 9
    assert pos.units_completed == 19


def test_daily_refresh_section_excluded():
    user = load_user()
    for section in user.currentCourse.pathSectioned:
        if section.type == "daily_refresh":
            section.completedUnits = 1  # pretend the daily refresh was done
    pos = reduce_path(user.currentCourse)
    assert pos.units_completed == 19  # unchanged


def test_completed_units_fallback_to_level_states():
    # If completedUnits disappears from the payload, derive from level states.
    user = load_user()
    for section in user.currentCourse.pathSectioned:
        section.completedUnits = None
    pos = reduce_path(user.currentCourse)
    # Sections 3+ had their units trimmed from the fixture, so only the
    # early sections contribute: 11 (intro) + 8 (A1) = 19 still.
    assert pos.units_completed == 19
    assert pos.current_section == 2
    assert pos.current_unit == 9


def test_active_section_id_missing_falls_back_to_first_incomplete():
    user = load_user()
    user.currentCourse.activePathSectionId = None
    pos = reduce_path(user.currentCourse)
    assert pos.current_section == 2
    assert pos.current_unit == 9


def test_missing_path():
    assert reduce_path(None).current_section is None
    assert reduce_path(CurrentCourse()).units_completed is None
    assert reduce_path(CurrentCourse(pathSectioned=[])).current_unit is None


def test_all_sections_complete_reports_last_unit():
    user = load_user()
    for section in user.currentCourse.pathSectioned:
        section.completedUnits = section.totalUnits
    user.currentCourse.activePathSectionId = None
    pos = reduce_path(user.currentCourse)
    assert pos.current_section == 8  # last learning section (index 7 + 1)
    assert pos.current_unit == 200   # capped at totalUnits


def test_tolerates_units_without_levels():
    course = CurrentCourse.model_validate({
        "pathSectioned": [
            {"index": 0, "type": "learning", "units": [{"unitIndex": 0}, {"unitIndex": 1}]},
        ]
    })
    pos = reduce_path(course)
    assert pos.units_completed == 0
    assert pos.current_section == 1
    assert pos.current_unit == 1

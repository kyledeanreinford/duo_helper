import json
from datetime import date
from pathlib import Path

from duo_tracker.duo.models import CurrentCourse
from duo_tracker.web import (
    ASSUMED_LESSONS_PER_UNIT,
    compute_stats,
    eta_shift,
    remaining_a2_lessons,
    units_from_path,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_remaining_a2_lessons_uses_assumption():
    assert remaining_a2_lessons(19) == (131 - 19) * ASSUMED_LESSONS_PER_UNIT
    assert remaining_a2_lessons(None) is None
    assert remaining_a2_lessons(131) == 0
    assert remaining_a2_lessons(200) == 0  # past the target never goes negative


def test_units_from_real_fixture():
    course = CurrentCourse.model_validate(
        json.loads((FIXTURES / "user_payload.json").read_text())["currentCourse"])
    units = units_from_path(course)
    # 19 completed + the active unit at capture time (S2 · U9).
    assert sum(u.completed for u in units) == 19
    active = [u for u in units if not u.completed]
    assert len(active) == 1 and active[0].label == "S2 · U9"
    assert active[0].sessions_done < active[0].total_sessions
    # Ordinals are per-section, matching the Duolingo UI.
    assert units[0].label == "S1 · U1"
    assert units[11].label == "S2 · U1"
    # Every completed unit's counts are real session sums.
    assert all(u.sessions_done > 0 for u in units if u.completed)


def test_units_skipped_unit_visible():
    course = CurrentCourse.model_validate({
        "pathSectioned": [{
            "type": "learning", "index": 0,
            "units": [
                {"levels": [{"state": "passed", "finishedSessions": 4, "totalSessions": 4}]},
                # tested out: passed but barely any sessions done
                {"levels": [{"state": "passed", "finishedSessions": 1, "totalSessions": 8}]},
            ],
        }],
    })
    units = units_from_path(course)
    assert [u.sessions_done for u in units] == [4, 1]
    assert all(u.completed for u in units)


def test_units_untouched_units_excluded():
    course = CurrentCourse.model_validate({
        "pathSectioned": [{
            "type": "learning",
            "units": [
                {"levels": [{"state": "passed", "finishedSessions": 2, "totalSessions": 2}]},
                {"levels": [{"state": "locked", "finishedSessions": 0, "totalSessions": 5}]},
            ],
        }],
    })
    assert len(units_from_path(course)) == 1


def test_eta_shift():
    today = date(2026, 7, 11)
    # pace improved 4 -> 5: 1000 lessons goes from 250 to 200 days -> 50 sooner
    assert eta_shift(today, 1000, 5.0, 4.0) == -50
    assert eta_shift(today, 1000, 4.0, 5.0) == 50
    assert eta_shift(today, 1000, 5.0, 0.0) is None   # no prior pace -> no shift
    assert eta_shift(today, None, 5.0, 4.0) is None


def test_compute_stats_anchors_on_latest_snapshot():
    # Latest snapshot is yesterday; page viewed mid-day today.
    rows = [  # newest first: (date, lessons, xp, streak, section, unit, units_completed)
        (date(2026, 7, 10), 7, 350, 24, 2, 9, 19),
        (date(2026, 7, 9), 7, 350, 23, 2, 9, 19),
        (date(2026, 7, 8), 7, 350, 22, 2, 8, 18),
        (date(2026, 7, 7), 7, 350, 21, 2, 8, 18),
        (date(2026, 7, 6), 7, 350, 20, 2, 8, 18),
        (date(2026, 7, 5), 7, 350, 19, 2, 8, 18),
        (date(2026, 7, 4), 7, 350, 18, 2, 8, 18),
    ]
    s = compute_stats("kyle", rows, None, today=date(2026, 7, 11))
    assert s.anchor == date(2026, 7, 10)
    # The 7-day window ends at the anchor: all seven days have 7 lessons.
    assert s.avg7 == 7.0
    # The week table starts at the anchor, not the empty today.
    assert s.week[0].day == date(2026, 7, 10)
    assert s.week[0].lessons == 7
    assert s.units == []


def test_compute_stats_observed_average_ignores_skips():
    course_raw = {
        "pathSectioned": [{
            "type": "learning",
            "units": [
                {"levels": [{"state": "passed", "finishedSessions": 24, "totalSessions": 24}]},
                {"levels": [{"state": "passed", "finishedSessions": 2, "totalSessions": 20}]},
                {"levels": [{"state": "passed", "finishedSessions": 26, "totalSessions": 26}]},
            ],
        }],
    }
    s = compute_stats("kyle", [(date(2026, 7, 10), 7, 350, 24, 1, 3, 2)], course_raw,
                      today=date(2026, 7, 11))
    assert s.observed_per_unit == 25.0  # (24 + 26) / 2; the skip doesn't drag it down


def test_compute_stats_empty_history():
    s = compute_stats("kyle", [], None, today=date(2026, 7, 11))
    assert s.anchor == date(2026, 7, 11)
    assert s.avg7 == 0.0
    assert s.eta7 is None

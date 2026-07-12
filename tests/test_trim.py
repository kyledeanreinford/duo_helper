import json
from pathlib import Path

from duo_tracker.duo.models import UserPayload
from duo_tracker.duo.reduce import reduce_path
from duo_tracker.duo.trim import trim_user_payload

FIXTURES = Path(__file__).parent / "fixtures"


def test_trim_preserves_reduction():
    raw = json.loads((FIXTURES / "user_payload.json").read_text())
    trimmed = trim_user_payload(raw)
    before = reduce_path(UserPayload.model_validate(raw).currentCourse)
    after = reduce_path(UserPayload.model_validate(trimmed).currentCourse)
    assert before == after


def test_trim_keeps_streak_and_course_identity():
    raw = json.loads((FIXTURES / "user_payload.json").read_text())
    trimmed = trim_user_payload(raw)
    assert trimmed["streak"] == raw["streak"]
    assert trimmed["streakData"]["currentStreak"]["length"] == 25
    assert trimmed["currentCourse"]["id"] == "DUOLINGO_PT_EN"
    assert trimmed["currentCourse"]["activePathSectionId"] == raw["currentCourse"]["activePathSectionId"]


def test_trim_drops_level_bloat_keeps_sessions():
    raw = {
        "streak": 1,
        "currentCourse": {
            "id": "DUOLINGO_PT_EN",
            "pathSectioned": [{
                "index": 0, "type": "learning", "completedUnits": 0, "totalUnits": 1,
                "units": [{
                    "unitIndex": 0,
                    "levels": [{
                        "state": "active", "finishedSessions": 2, "totalSessions": 4,
                        "pathLevelClientData": {"huge": "blob"},
                        "pathLevelSessionMetadata": {"more": "bloat"},
                        "debugName": "x",
                    }],
                }],
            }],
        },
    }
    level = trim_user_payload(raw)["currentCourse"]["pathSectioned"][0]["units"][0]["levels"][0]
    assert level == {"state": "active", "finishedSessions": 2, "totalSessions": 4}


def test_trim_tolerates_garbage():
    assert trim_user_payload({}) == {}
    assert trim_user_payload({"currentCourse": None}) == {}
    assert trim_user_payload({"currentCourse": {"pathSectioned": [None, 3]}}) == {
        "currentCourse": {"pathSectioned": [{}, {}]}
    }

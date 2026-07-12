"""Trim the Duolingo user payload before persisting.

The raw response is ~14 MB/night, almost all of it per-level client
metadata (pathLevelClientData, session metadata, debug names) that no
metric will ever need. What we keep is the part that history questions
get asked of: streak data, course identity, and the full path skeleton —
every section/unit/level with its state and session counts — so
lessons-per-unit and position can still be re-derived for any past day.

`duo-tracker probe` still dumps the untrimmed payload to data/ for
debugging; only the database copy is trimmed.
"""

_LEVEL_KEYS = ("state", "finishedSessions", "totalSessions", "type", "subtype")
_UNIT_KEYS = ("unitIndex", "teachingObjective", "isUnlocked", "learningUnitType")
_SECTION_KEYS = ("id", "index", "type", "cefr", "title", "completedUnits", "totalUnits")
_COURSE_KEYS = ("id", "learningLanguage", "fromLanguage", "activePathSectionId",
                "title", "xp", "crowns")
_USER_KEYS = ("streak", "streakData", "totalXp", "courses")


def trim_user_payload(user: dict) -> dict:
    out = {k: user.get(k) for k in _USER_KEYS if k in user}
    course = user.get("currentCourse")
    if isinstance(course, dict):
        trimmed = {k: course.get(k) for k in _COURSE_KEYS if k in course}
        path = course.get("pathSectioned")
        if isinstance(path, list):
            trimmed["pathSectioned"] = [_trim_section(s) for s in path]
        out["currentCourse"] = trimmed
    return out


def _trim_section(section: dict) -> dict:
    if not isinstance(section, dict):
        return {}
    out = {k: section.get(k) for k in _SECTION_KEYS if k in section}
    units = section.get("units")
    if isinstance(units, list):
        out["units"] = [_trim_unit(u) for u in units]
    return out


def _trim_unit(unit: dict) -> dict:
    if not isinstance(unit, dict):
        return {}
    out = {k: unit.get(k) for k in _UNIT_KEYS if k in unit}
    levels = unit.get("levels")
    if isinstance(levels, list):
        out["levels"] = [
            {k: lv.get(k) for k in _LEVEL_KEYS if isinstance(lv, dict) and k in lv}
            for lv in levels
        ]
    return out

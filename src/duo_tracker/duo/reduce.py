"""Reduce currentCourse.pathSectioned to the snapshot's position metrics.

All values are 1-based to match what the Duolingo UI shows ("Section 2,
Unit 9"). Must never raise on weird data — missing pieces become None and
the snapshot persists with the raw payload intact.
"""

import logging
from dataclasses import dataclass

from duo_tracker.duo.models import CurrentCourse, PathSection, PathUnit

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PathPosition:
    current_section: int | None  # 1-based
    current_unit: int | None     # 1-based, within the section
    units_completed: int | None  # across all learning sections


def reduce_path(course: CurrentCourse | None) -> PathPosition:
    if course is None or not course.pathSectioned:
        log.warning("no currentCourse.pathSectioned — position metrics will be NULL")
        return PathPosition(None, None, None)

    # daily_refresh (and any future non-learning section types) would pollute
    # the counts; tolerate a missing type field by treating it as learning.
    sections = [s for s in course.pathSectioned if s.type in (None, "learning")]
    if not sections:
        return PathPosition(None, None, None)

    units_completed = sum(_completed_units(s) for s in sections)

    active = _active_section(sections, course.activePathSectionId)
    if active is None:
        # Course finished: report the last section/unit.
        active = sections[-1]
    section_ordinal = (active.index + 1) if active.index is not None else (
        sections.index(active) + 1
    )
    unit_ordinal = min(_completed_units(active) + 1, _total_units(active) or 10**9)
    return PathPosition(section_ordinal, unit_ordinal, units_completed)


def _active_section(sections: list[PathSection], active_id: str | None) -> PathSection | None:
    if active_id:
        for s in sections:
            if s.id == active_id:
                return s
    for s in sections:
        if _completed_units(s) < (_total_units(s) or 0):
            return s
    return None


def _completed_units(section: PathSection) -> int:
    if section.completedUnits is not None:
        return section.completedUnits
    # Fallback if completedUnits disappears: derive from level states.
    return sum(1 for u in (section.units or []) if _unit_is_completed(u))


def _total_units(section: PathSection) -> int | None:
    if section.totalUnits is not None:
        return section.totalUnits
    return len(section.units) if section.units else None


def _unit_is_completed(unit: PathUnit) -> bool:
    states = [lv.state for lv in (unit.levels or []) if lv.state is not None]
    return bool(states) and all(s == "passed" for s in states)

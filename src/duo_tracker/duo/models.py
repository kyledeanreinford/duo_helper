"""Tolerant pydantic models for the slices of the Duolingo payload we read.

Shapes confirmed against a live probe on 2026-07-11 (see data/ dumps and
tests/fixtures/). Everything is Optional with extra="allow" — the payload
has shifted repeatedly and the raw JSON is persisted anyway; these models
only need to not blow up.
"""

from pydantic import BaseModel, ConfigDict


class _Tolerant(BaseModel):
    model_config = ConfigDict(extra="allow")


class PathLevel(_Tolerant):
    # Observed states: passed | active | locked | unit_test
    state: str | None = None
    finishedSessions: int | None = None
    totalSessions: int | None = None


class PathUnit(_Tolerant):
    # NOTE: unitIndex is GLOBAL across sections (section 2 starts at 11);
    # "unit N within a section" is list position, not this field.
    unitIndex: int | None = None
    teachingObjective: str | None = None
    levels: list[PathLevel] | None = None


class PathSection(_Tolerant):
    id: str | None = None
    index: int | None = None
    # learning | daily_refresh (the latter excluded from unit counts)
    type: str | None = None
    completedUnits: int | None = None
    totalUnits: int | None = None
    units: list[PathUnit] | None = None


class CurrentCourse(_Tolerant):
    id: str | None = None  # e.g. "DUOLINGO_PT_EN"
    learningLanguage: str | None = None
    fromLanguage: str | None = None
    activePathSectionId: str | None = None
    pathSectioned: list[PathSection] | None = None


class CurrentStreak(_Tolerant):
    length: int | None = None


class StreakData(_Tolerant):
    currentStreak: CurrentStreak | None = None


class UserPayload(_Tolerant):
    streak: int | None = None
    streakData: StreakData | None = None
    totalXp: int | None = None
    currentCourse: CurrentCourse | None = None


class XpSummary(_Tolerant):
    date: int | None = None  # midnight UTC of the summary day, epoch seconds
    gainedXp: int | None = None
    numSessions: int | None = None
    streakExtended: bool | None = None


class XpSummariesPayload(_Tolerant):
    summaries: list[XpSummary] | None = None

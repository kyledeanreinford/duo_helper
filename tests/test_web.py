from datetime import date

from duo_tracker.web import (
    ASSUMED_LESSONS_PER_UNIT,
    observe_lessons_per_unit,
    remaining_a2_lessons,
)


def test_remaining_a2_lessons_uses_assumption():
    assert remaining_a2_lessons(19) == (131 - 19) * ASSUMED_LESSONS_PER_UNIT
    assert remaining_a2_lessons(None) is None
    assert remaining_a2_lessons(131) == 0
    assert remaining_a2_lessons(200) == 0  # past the target never goes negative


def test_observe_lessons_per_unit():
    rows = [
        (date(2026, 7, 11), 10, 19),  # baseline day — no observation
        (date(2026, 7, 12), 12, 19),  # working on unit 20
        (date(2026, 7, 13), 14, 20),  # unit 20 done: 12 + 14 = 26 lessons
        (date(2026, 7, 14), 8, 20),
        (date(2026, 7, 15), 30, 22),  # two units at once: 8 + 30 = 38 -> 19.0 each
    ]
    obs = observe_lessons_per_unit(rows)
    assert len(obs) == 2
    assert (obs[0].day, obs[0].units_gained, obs[0].lessons_spent, obs[0].per_unit) == (
        date(2026, 7, 13), 1, 26, 26.0,
    )
    assert (obs[1].units_gained, obs[1].lessons_spent, obs[1].per_unit) == (2, 38, 19.0)


def test_observe_skips_unknown_unit_days():
    # Backfilled rows have units_completed NULL — they must not break the walk.
    rows = [
        (date(2026, 7, 10), 9, None),
        (date(2026, 7, 11), 10, 19),
        (date(2026, 7, 12), None, None),
        (date(2026, 7, 13), 24, 20),
    ]
    obs = observe_lessons_per_unit(rows)
    assert len(obs) == 1
    assert obs[0].lessons_spent == 24


def test_no_observations_from_flat_history():
    rows = [(date(2026, 7, 11), 10, 19), (date(2026, 7, 12), 5, 19)]
    assert observe_lessons_per_unit(rows) == []


def test_eta_shift():
    from duo_tracker.web import eta_shift

    today = date(2026, 7, 11)
    # pace improved 4 -> 5: 1000 lessons goes from 250 to 200 days -> 50 sooner
    assert eta_shift(today, 1000, 5.0, 4.0) == -50
    assert eta_shift(today, 1000, 4.0, 5.0) == 50
    assert eta_shift(today, 1000, 5.0, 0.0) is None   # no prior pace -> no shift
    assert eta_shift(today, None, 5.0, 4.0) is None


def test_compute_stats_anchors_on_latest_snapshot():
    from duo_tracker.web import compute_stats

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
    s = compute_stats("kyle", rows, [], today=date(2026, 7, 11))
    assert s.anchor == date(2026, 7, 10)
    # The 7-day window ends at the anchor: all seven days have 7 lessons.
    assert s.avg7 == 7.0
    # The week table starts at the anchor, not the empty today.
    assert s.week[0].day == date(2026, 7, 10)
    assert s.week[0].lessons == 7


def test_compute_stats_empty_history():
    from duo_tracker.web import compute_stats

    s = compute_stats("kyle", [], [], today=date(2026, 7, 11))
    assert s.anchor == date(2026, 7, 11)
    assert s.avg7 == 0.0
    assert s.eta7 is None

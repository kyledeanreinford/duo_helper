from duo_tracker.vocab import progressed_skills


def test_progressed_skills_aggregates_by_skill():
    course = {
        "pathSectioned": [{
            "units": [{
                "levels": [
                    {"finishedSessions": 3, "pathLevelClientData": {"skillId": "abc"}},
                    {"finishedSessions": 2, "pathLevelClientData": {"skillId": "abc"}},
                    {"finishedSessions": 0, "pathLevelClientData": {"skillId": "zzz"}},  # untouched
                    {"finishedSessions": 1, "pathLevelClientData": {"skillIds": ["def", "ghi"]}},
                    {"finishedSessions": 4},  # no client data (unit test level)
                ],
            }],
        }],
    }
    skills = {s["skillId"]["id"]: s for s in progressed_skills(course)}
    assert set(skills) == {"abc", "def", "ghi"}
    assert skills["abc"] == {"skillId": {"id": "abc"}, "finishedLevels": 2, "finishedSessions": 5}
    assert skills["def"]["finishedSessions"] == 1


def test_progressed_skills_empty_course():
    assert progressed_skills({}) == []
    assert progressed_skills({"pathSectioned": []}) == []

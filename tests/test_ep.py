from duo_tracker.ep import parse_classifications


def test_parse_classifications_filters_and_normalizes():
    payload = {"words": [
        {"word": "ônibus", "differs": True, "ep_word": "autocarro"},
        {"word": "mulher", "differs": False, "ep_word": None},
        # differs=False with a spurious ep_word -> ep_word dropped
        {"word": "juntos", "differs": False, "ep_word": "juntos"},
        # hallucinated word not in the batch -> ignored
        {"word": "trem", "differs": True, "ep_word": "comboio"},
        # differs=True but empty ep_word -> stored as None
        {"word": "empresa", "differs": True, "ep_word": ""},
    ]}
    expected = {"ônibus", "mulher", "juntos", "empresa"}
    out = parse_classifications(payload, expected)
    assert out == {
        "ônibus": (True, "autocarro"),
        "mulher": (False, None),
        "juntos": (False, None),
        "empresa": (True, None),
    }


def test_parse_classifications_garbage():
    assert parse_classifications({}, {"a"}) == {}
    assert parse_classifications({"words": [{"weird": 1}]}, {"a"}) == {}

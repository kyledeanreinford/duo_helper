from duo_tracker.alerting import send_alert


def test_disabled_when_no_url():
    send_alert(None, "s", "d")  # must be a no-op, no HTTP attempted


def test_unreachable_alertmanager_never_raises():
    send_alert("http://127.0.0.1:1", "s", "d")  # connection refused -> logged, swallowed

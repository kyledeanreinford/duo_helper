"""Fail-open alert posting to the cluster's Alertmanager.

Alertmanager (monitoring namespace) holds the one Slack webhook and does
grouping/throttling; apps just POST alerts to its v2 API over the cluster
network. Nothing here may ever raise — a Slack/alertmanager outage must
not turn a partially-working snapshot run into a crashed one.
"""

import logging

import httpx

log = logging.getLogger(__name__)


def send_alert(base_url: str | None, summary: str, description: str,
               labels: dict[str, str] | None = None) -> None:
    if not base_url:
        return
    alert = {
        "labels": {"alertname": "DuoTrackerSnapshotFailed", "app": "duo-tracker",
                   "severity": "warning", **(labels or {})},
        "annotations": {"summary": summary, "description": description},
    }
    try:
        resp = httpx.post(f"{base_url.rstrip('/')}/api/v2/alerts",
                          json=[alert], timeout=5.0)
        if resp.status_code >= 300:
            log.warning("alertmanager returned HTTP %s: %s",
                        resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("could not reach alertmanager (%s) — alert not delivered", exc)

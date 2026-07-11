"""Thin httpx client for the reverse-engineered Duolingo endpoints.

Everything here is unofficial and unconfirmed until `duo-tracker probe`
has been run against a live account — treat the endpoint paths and the
`fields` filter as the most likely things to break.

Methods return raw dicts; typed parsing happens in duo/models.py above
this layer so the full payload is always available to persist even when
parsing fails.
"""

from datetime import date

import httpx

BASE = "https://www.duolingo.com/2017-06-30"

# Duolingo 403s non-browser user agents; send a real one.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

USER_FIELDS = "streak,streakData,totalXp,courses,currentCourse"


class DuoAuthError(RuntimeError):
    """The JWT was rejected (401/403). Message tells the operator how to fix it."""


class DuoApiError(RuntimeError):
    """Any other non-2xx response."""


class DuoClient:
    def __init__(self, jwt: str, person: str):
        self.person = person
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {jwt}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            # Belt and braces: some deployments check the cookie, some the header.
            cookies={"jwt_token": jwt},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DuoClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _get(self, url: str, params: dict | None = None) -> dict:
        resp = self._client.get(url, params=params)
        if resp.status_code in (401, 403):
            raise DuoAuthError(
                f"Duolingo rejected the JWT for '{self.person}' (HTTP {resp.status_code}). "
                f"Re-harvest it: log into duolingo.com as {self.person} in a browser, copy "
                f"the 'jwt_token' cookie value, and update DUO_JWT_{self.person.upper()} "
                f"in .env (or the k8s secret)."
            )
        if resp.status_code >= 300:
            # Bot walls come back as HTML — the body snippet makes that obvious.
            raise DuoApiError(
                f"GET {url} -> HTTP {resp.status_code} for '{self.person}': {resp.text[:300]}"
            )
        return resp.json()

    def get_user(self, user_id: int, fields: str | None = USER_FIELDS) -> dict:
        params = {"fields": fields} if fields else None
        return self._get(f"{BASE}/users/{user_id}", params=params)

    def get_xp_summaries(self, user_id: int, start_date: date) -> dict:
        return self._get(
            f"{BASE}/users/{user_id}/xp_summaries",
            params={"startDate": start_date.isoformat()},
        )

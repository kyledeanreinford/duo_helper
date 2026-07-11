"""Decode the payload of a harvested Duolingo JWT.

We never verify the signature — we're the client, not the server; we just
need the `sub` claim (the numeric Duolingo user id) and `exp` for a
friendly expiry warning. Stdlib only, no pyjwt.
"""

import base64
import json


def decode_claims(jwt: str, person: str = "?") -> dict:
    parts = jwt.strip().split(".")
    if len(parts) != 3:
        raise ValueError(
            f"DUO_JWT_{person.upper()} does not look like a JWT "
            f"(expected 3 dot-separated segments, got {len(parts)})"
        )
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception as exc:
        raise ValueError(f"DUO_JWT_{person.upper()} payload is not decodable: {exc}") from exc


def decode_user_id(jwt: str, person: str = "?") -> int:
    claims = decode_claims(jwt, person)
    sub = claims.get("sub")
    if sub is None:
        raise ValueError(f"DUO_JWT_{person.upper()} has no 'sub' claim; claims: {list(claims)}")
    return int(sub)

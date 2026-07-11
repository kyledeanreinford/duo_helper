import base64
import json

import pytest

from duo_tracker.duo.auth import decode_claims, decode_user_id


def make_jwt(payload: dict) -> str:
    def seg(obj: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()
        return raw.rstrip("=")  # real JWTs strip base64 padding

    return f"{seg({'alg': 'HS256'})}.{seg(payload)}.signature"


def test_decode_user_id():
    assert decode_user_id(make_jwt({"sub": 123456789})) == 123456789


def test_decode_user_id_string_sub():
    assert decode_user_id(make_jwt({"sub": "42"})) == 42


def test_decode_claims_handles_stripped_padding():
    # A payload whose base64 length forces 1..3 padding chars.
    for filler in ("a", "ab", "abc", "abcd"):
        claims = decode_claims(make_jwt({"sub": 1, "x": filler}))
        assert claims["x"] == filler


def test_not_a_jwt():
    with pytest.raises(ValueError, match="does not look like a JWT"):
        decode_claims("garbage", person="kyle")


def test_missing_sub():
    with pytest.raises(ValueError, match="no 'sub' claim"):
        decode_user_id(make_jwt({"exp": 1}))

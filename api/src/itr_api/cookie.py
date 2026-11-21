"""Signing the game cookie (contract 5). The gateway checks it; this is the only signer.

sig = base64url without padding of HMAC-SHA256(key, ASCII "v1.<game_id>.<exp>.<kid>").
Must reproduce the test vector in docs/contracts/game-path.md (tests/test_cookie.py).
"""

import base64
import hashlib
import hmac

COOKIE_NAME = "__Secure-itr_game"
LIFETIME_S = 2700  # 45 min, the server's lifetime


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key: bytes, game_id: str, exp: int, kid: str) -> str:
    signed = f"v1.{game_id}.{exp}.{kid}".encode("ascii")
    sig = b64url(hmac.new(key, signed, hashlib.sha256).digest())
    return f"v1.{game_id}.{exp}.{kid}.{sig}"


def set_cookie(value: str, game_id: str, max_age: int) -> str:
    """The Set-Cookie header: per-game path, never readable by website JavaScript."""
    return (
        f"{COOKIE_NAME}={value}; Path=/s/{game_id}/; Max-Age={max_age}; "
        "Secure; HttpOnly; SameSite=Strict"
    )

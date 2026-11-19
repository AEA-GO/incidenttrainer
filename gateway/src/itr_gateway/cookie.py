"""The game cookie (contract 5): `v1.<game_id>.<exp>.<kid>.<sig>`.

sig = base64url without padding of HMAC-SHA256(key, ASCII "v1.<game_id>.<exp>.<kid>").
The API signs (contract 3); gateway-auth only checks. `sign` lives here too, for tests
and for the laptop script that makes a game.
"""

import base64
import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass

from itr_gateway.errors import BAD_COOKIE, EXPIRED, gateway_error

COOKIE_NAME = "__Secure-itr_game"
COOKIE_NAME_INSECURE = "itr_game"  # laptops only (ITR_COOKIE_INSECURE=1)
LIFETIME_S = 2700  # 45 min, the server's lifetime

GAME_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
KID = re.compile(r"^k[0-9]{1,6}$")
EXP = re.compile(r"^[0-9]{1,12}$")
SIG = re.compile(r"^[A-Za-z0-9_-]{43}$")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def signature(key: bytes, game_id: str, exp: int, kid: str) -> str:
    signed = f"v1.{game_id}.{exp}.{kid}".encode("ascii")
    return b64url(hmac.new(key, signed, hashlib.sha256).digest())


def sign(key: bytes, game_id: str, exp: int, kid: str) -> str:
    """The cookie value for a game (what the API puts in Set-Cookie)."""
    return f"v1.{game_id}.{exp}.{kid}.{signature(key, game_id, exp, kid)}"


@dataclass(frozen=True)
class GameCookie:
    game_id: str
    exp: int
    kid: str


def check(
    value: str | None, key_for: Callable[[str], bytes | None], now: float
) -> GameCookie:
    """Steps 2-4 of "Checking a cookie": format, key and signature, expiry.

    `key_for(kid)` returns the signing key or None. Raises GatewayError 401
    bad_cookie / expired. The caller compares the game id.
    """
    if not value:
        raise gateway_error(BAD_COOKIE)
    parts = value.split(".")
    if len(parts) != 5 or parts[0] != "v1":
        raise gateway_error(BAD_COOKIE)
    _, game_id, exp_text, kid, sig = parts
    if not (
        GAME_ID.match(game_id)
        and EXP.match(exp_text)
        and KID.match(kid)
        and SIG.match(sig)
    ):
        raise gateway_error(BAD_COOKIE)
    key = key_for(kid)
    if key is None:
        raise gateway_error(BAD_COOKIE)
    expected = signature(key, game_id, int(exp_text), kid)
    if not hmac.compare_digest(expected.encode("ascii"), sig.encode("ascii")):
        raise gateway_error(BAD_COOKIE)
    if int(exp_text) <= now:
        raise gateway_error(EXPIRED)
    return GameCookie(game_id=game_id, exp=int(exp_text), kid=kid)


def from_header(cookie_header: str | None, name: str) -> str | None:
    """The value of one cookie from a raw Cookie header (first match wins)."""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value.strip()
    return None

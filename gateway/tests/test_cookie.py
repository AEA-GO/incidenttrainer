import pytest
from conftest import EXP, GAME_ID, KEY, NOW
from itr_gateway import cookie
from itr_gateway.errors import GatewayError

VECTOR_SIG = "-tGnC2KML72exmObhTrxlmLb5IImJIHwoBSa4GRP5v4"
VECTOR_COOKIE = f"v1.{GAME_ID}.{EXP}.k1.{VECTOR_SIG}"


def keys(kid: str) -> bytes | None:
    return {"k1": KEY}.get(kid)


def test_contract_test_vector() -> None:
    assert cookie.b64url_decode("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8") == KEY
    assert cookie.signature(KEY, GAME_ID, EXP, "k1") == VECTOR_SIG
    assert cookie.sign(KEY, GAME_ID, EXP, "k1") == VECTOR_COOKIE


def test_a_good_cookie_passes() -> None:
    checked = cookie.check(VECTOR_COOKIE, keys, NOW)
    assert (checked.game_id, checked.exp, checked.kid) == (GAME_ID, EXP, "k1")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "v1.only.four.parts",
        f"v2.{GAME_ID}.{EXP}.k1.{VECTOR_SIG}",  # unknown version
        f"v1.{GAME_ID}.{EXP}.k9.{VECTOR_SIG}",  # unknown key
        f"v1.{GAME_ID}.{EXP}.k1.{VECTOR_SIG[:-1]}A",  # changed signature
        f"v1.{GAME_ID}.{EXP + 1}.k1.{VECTOR_SIG}",  # changed expiry
        f"v1.{GAME_ID[:-1]}!.{EXP}.k1.{VECTOR_SIG}",  # bad game id
        f"v1.{GAME_ID}.-5.k1.{VECTOR_SIG}",  # bad expiry
        f"v1.{GAME_ID}.{EXP}.key1.{VECTOR_SIG}",  # bad key id
        f"v1.{GAME_ID}.{EXP}.k1.{VECTOR_SIG}.extra",
    ],
)
def test_bad_cookies_are_refused(value) -> None:
    with pytest.raises(GatewayError) as caught:
        cookie.check(value, keys, NOW)
    assert (caught.value.status, caught.value.code) == (401, "bad_cookie")


def test_an_old_cookie_is_expired() -> None:
    for now in (EXP, EXP + 1):
        with pytest.raises(GatewayError) as caught:
            cookie.check(VECTOR_COOKIE, keys, now)
        assert (caught.value.status, caught.value.code) == (401, "expired")


def test_a_forged_cookie_is_bad_not_expired() -> None:
    forged = cookie.sign(b"x" * 32, GAME_ID, EXP - 10_000, "k1")  # old AND forged
    with pytest.raises(GatewayError) as caught:
        cookie.check(forged, keys, NOW + 10_000)
    assert caught.value.code == "bad_cookie"


def test_the_cookie_is_found_among_others() -> None:
    header = f"theme=dark; __Secure-itr_game={VECTOR_COOKIE}; itr_game=other"
    assert cookie.from_header(header, cookie.COOKIE_NAME) == VECTOR_COOKIE
    assert cookie.from_header(header, cookie.COOKIE_NAME_INSECURE) == "other"
    assert cookie.from_header("a=1", cookie.COOKIE_NAME) is None
    assert cookie.from_header(None, cookie.COOKIE_NAME) is None

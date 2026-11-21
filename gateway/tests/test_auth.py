import json

import pytest
from conftest import GAME_ID, KEY_B64, NOW, ORIGIN, OTHER_ID
from itr_gateway.cookie import b64url, sign

API = f"/s/{GAME_ID}/api/v1/"
GRAFANA = f"/s/{GAME_ID}/grafana/"
TERM = f"/s/{GAME_ID}/term/"


def refused(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code
    assert response.headers["cache-control"] == "no-store"


def forwarded(response, port: int, ip: str = "10.20.3.17") -> None:
    assert response.status_code == 204, response.headers
    assert response.headers["x-itr-upstream"] == ip
    assert response.headers["x-itr-port"] == str(port)
    assert response.headers["x-itr-game-id"] == GAME_ID


# ----- step 1: only our paths -------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/s/",
        f"/s/{GAME_ID}/",
        f"/s/{GAME_ID}/control/v1/report",  # 8081 is never reachable
        f"/s/{GAME_ID}/api/v2/state",
        f"/s/{GAME_ID}/apiv1/state",
        f"/s/{GAME_ID[:-1]}/api/v1/state",  # 21 characters
        f"/s/{GAME_ID}x/api/v1/state",
    ],
)
def test_other_paths_are_not_found(world, path) -> None:
    world.put()
    refused(world.browser(path), 404, "not_found")


# ----- steps 2-6: the cookie and the game ------------------------------------------
def test_no_cookie(world) -> None:
    world.put()
    refused(world.browser(API + "state", cookie=None), 401, "bad_cookie")


def test_the_laptop_cookie_name_is_not_accepted_in_production(world) -> None:
    world.put()
    value = sign(bytes(range(32)), GAME_ID, 1796755500, "k1")
    refused(world.browser(API + "state", cookie=f"itr_game={value}"), 401, "bad_cookie")


def test_expired_cookie(world, clock) -> None:
    world.put()
    clock.now = 1796755500
    refused(world.browser(API + "state"), 401, "expired")


def test_cookie_for_another_game(world) -> None:
    world.put()
    world.put(OTHER_ID)
    refused(
        world.browser(API + "state", cookie=world.cookie(OTHER_ID)), 403, "wrong_game"
    )


def test_unknown_game(world) -> None:
    refused(world.browser(API + "state"), 404, "not_found")


# ----- step 7: routing by state ----------------------------------------------------
@pytest.mark.parametrize("state", ["ready", "paged", "investigating", "watching"])
def test_a_game_in_play_reaches_agent_and_grafana(world, state) -> None:
    world.put(state=state)
    forwarded(world.auth(API + "state"), 8080)
    forwarded(world.auth(API + "actions", method="POST"), 8080)
    forwarded(world.auth(API + "feed?after=12"), 8080)
    forwarded(world.auth(GRAFANA + "d/shop-overview?kiosk"), 3000)
    refused(world.browser(TERM), 404, "no_terminal")


def test_the_terminal_needs_a_terminal_lab_and_our_origin(world) -> None:
    world.put(terminal=True)
    forwarded(world.auth(TERM), 7681)
    forwarded(world.auth(TERM + "token"), 7681)
    forwarded(world.auth(TERM + "ws", origin=ORIGIN), 7681)
    refused(world.browser(TERM + "ws"), 403, "bad_origin")
    refused(
        world.browser(TERM + "ws", origin="https://evil.example"), 403, "bad_origin"
    )


def test_booting_game_answers_the_boot_screen_itself(world, clock) -> None:
    world.put(state="booting", private_ip=None)
    clock.now = NOW  # 200 s after created_at
    first = world.auth(API + "state")
    assert first.status_code == 403
    assert first.headers["x-itr-status"] == "200"
    answer = world.browser(API + "state")
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {
        "state": "booting",
        "boot": {
            "server_started": True,
            "agent_ready": False,
            "seconds_since_start": 200,
            "components": {},
        },
    }


def test_booting_game_refuses_everything_else(world) -> None:
    world.put(state="booting")
    for path, method in (
        (API + "state", "POST"),
        (API + "scene", "GET"),
        (GRAFANA, "GET"),
        (TERM, "GET"),
    ):
        answer = world.browser(path, method=method)
        refused(answer, 503, "booting")
        assert answer.headers["retry-after"] == "2"
        assert answer.json()["error"]["retry_after_s"] == 2


def test_ended_game_with_its_server_still_running(world) -> None:
    world.put(state="ended", terminal=True)
    forwarded(world.auth(API + "state"), 8080)
    forwarded(world.auth(GRAFANA), 3000)
    refused(world.browser(TERM), 410, "ended")


@pytest.mark.parametrize(
    "gone",
    [
        {"server_terminated_at": "2026-12-08T18:30:00Z"},
        {"expires_at_server": NOW - 1},
        {"private_ip": None},
    ],
)
def test_ended_game_without_a_server(world, gone) -> None:
    world.put(state="ended", **gone)
    for path in (API + "state", GRAFANA, TERM):
        refused(world.browser(path), 410, "ended")


@pytest.mark.parametrize("ip", ["10.20.33.7", "10.20.63.254"])
def test_forwards_to_game_servers_in_zones_b_and_c(world, ip) -> None:
    world.put(private_ip=ip)
    forwarded(world.auth(API + "state"), 8080, ip=ip)


@pytest.mark.parametrize(
    "ip", ["10.20.16.5", "10.20.64.1", "127.0.0.1", "169.254.169.254", "not-an-ip"]
)
def test_never_forwards_outside_the_sandbox_subnet(world, ip) -> None:
    world.put(private_ip=ip)
    refused(world.browser(API + "state"), 502, "server_unreachable")


# ----- heartbeat, keys, cache -------------------------------------------------------
def test_heartbeat_at_most_every_30_seconds(world, clock) -> None:
    world.put()
    world.auth(API + "state")
    assert world.store.last_seen[GAME_ID] == int(NOW)
    clock.now = NOW + 29
    world.auth(API + "state")
    assert world.store.last_seen[GAME_ID] == int(NOW)
    clock.now = NOW + 30
    world.auth(API + "state")
    assert world.store.last_seen[GAME_ID] == int(NOW + 30)


def test_refused_requests_are_not_a_heartbeat(world) -> None:
    world.put()
    world.auth(API + "state", cookie=None)  # no cookie
    world.auth(TERM)  # good cookie, but this lab has no terminal
    assert GAME_ID not in world.store.last_seen


def test_a_new_key_works_after_rotation(world, clock) -> None:
    world.put()
    world.store.key("k1")  # keys loaded
    new_key = bytes(range(1, 33))
    new_b64 = b64url(new_key)
    world.store.keys_json = json.dumps(
        {
            "current": "k2",
            "keys": {"k1": {"secret": KEY_B64}, "k2": {"secret": new_b64}},
        }
    )
    clock.now = NOW + 30  # an unknown key id makes it look again (at most every 30 s)
    forwarded(
        world.auth(API + "state", cookie=world.cookie(kid="k2", key=new_key)), 8080
    )
    # ...so a stream of made-up key ids doesn't hammer the store
    world.store.keys_json = json.dumps({"keys": {}})
    clock.now = NOW + 40
    refused(
        world.browser(API + "state", cookie=world.cookie(kid="k7", key=new_key)),
        401,
        "bad_cookie",
    )
    forwarded(world.auth(API + "state"), 8080)  # k1 still cached


def test_a_changed_games_file_is_seen_at_once(world) -> None:
    world.put(state="booting")
    assert world.browser(API + "state").json()["state"] == "booting"
    world.put(state="ready")
    forwarded(world.auth(API + "state"), 8080)


# ----- /deny itself -----------------------------------------------------------------
def test_deny_without_a_known_code_is_an_internal_error(world) -> None:
    answer = world.client.get("/deny", headers={"X-Itr-Code": "made_up"})
    refused(answer, 500, "internal")


def test_healthz(world) -> None:
    assert world.client.get("/healthz").json() == {"status": "ok"}


# ----- laptop only: /dev-login ------------------------------------------------------
def test_dev_login_is_off_unless_asked_for(world) -> None:
    world.put()
    assert world.client.get(f"/dev-login?game={GAME_ID}").status_code == 404


def test_dev_login_sets_a_working_cookie(tmp_path, clock) -> None:
    from conftest import KEYS
    from fastapi.testclient import TestClient
    from itr_gateway.app import Settings, build_app
    from itr_gateway.store import FileStore

    games = tmp_path / "games.json"
    games.write_text(
        json.dumps(
            {"games": {GAME_ID: {"state": "investigating", "private_ip": "127.0.0.1"}}}
        )
    )
    store = FileStore(path=games, keys_json=json.dumps(KEYS), clock=clock)
    settings = Settings(
        sandbox_cidrs=["127.0.0.0/8"], insecure_cookie=True, dev_login=True
    )
    client = TestClient(build_app(store, settings, clock=clock), follow_redirects=False)

    answer = client.get(f"/dev-login?game={GAME_ID}")
    assert answer.status_code == 302
    assert answer.headers["location"] == f"/s/{GAME_ID}/grafana/d/shop-overview"
    set_cookie = answer.headers["set-cookie"]
    assert set_cookie.startswith("itr_game=v1.")
    for part in (f"Path=/s/{GAME_ID}/", "HttpOnly", "SameSite=strict", "Max-Age=2700"):
        assert part in set_cookie
    value = set_cookie.split(";", 1)[0]
    check = client.get(
        "/auth",
        headers={"X-Original-URI": f"/s/{GAME_ID}/api/v1/state", "Cookie": value},
    )
    assert check.status_code == 204
    assert client.get("/dev-login?game=AAAAAAAAAAAAAAAAAAAAAA").status_code == 404

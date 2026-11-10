from conftest import CLAIM, GAME_ID


def test_state_before_claim_is_not_ready(player):
    response = player.get("/v1/state", headers={"X-Itr-Game-Id": GAME_ID})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_ready"


def test_state_needs_the_game_header(player):
    response = player.get("/v1/state")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_state_for_another_game_is_refused(player, agent):
    agent.claim(CLAIM["game_id"], CLAIM["token"], CLAIM["public_base_url"])
    response = player.get("/v1/state", headers={"X-Itr-Game-Id": "A" * 22})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "wrong_game"


def test_player_port_has_no_control_paths(player):
    assert player.post("/control/v1/claim", json=CLAIM).status_code == 404

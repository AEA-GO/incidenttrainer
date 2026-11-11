from conftest import CLAIM, GAME_ID, TOKEN


def test_ready_before_claim(control):
    body = control.get("/control/v1/ready").json()
    assert body["booted"] is True
    assert body["claimed"] is False
    assert body["runbook_loaded"] is False
    assert body["world"] == {"name": "dev-shop", "version": "0.1.0"}


def test_claim_once(control):
    assert control.post("/control/v1/claim", json=CLAIM).status_code == 204
    assert control.get("/control/v1/ready").json()["claimed"] is True

    again = control.post("/control/v1/claim", json=CLAIM)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_claimed"


def test_claim_refused_while_booting(control, agent):
    agent.booted = False
    response = control.post("/control/v1/claim", json=CLAIM)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_booted"


def test_claim_rejects_bad_body(control):
    for bad in (
        {**CLAIM, "game_id": "too-short"},
        {**CLAIM, "token": "short"},
        {**CLAIM, "public_base_url": "http://example.com"},
        {**CLAIM, "extra": 1},
    ):
        response = control.post("/control/v1/claim", json=bad)
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "bad_request"


def test_claim_allows_localhost_for_laptops(control):
    body = {**CLAIM, "public_base_url": "http://localhost:8000"}
    assert control.post("/control/v1/claim", json=body).status_code == 204


def test_report_needs_the_token(control):
    control.post("/control/v1/claim", json=CLAIM)

    assert control.get("/control/v1/report").status_code == 401
    wrong = control.get(
        "/control/v1/report", headers={"Authorization": "Bearer " + "x" * 43}
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "unauthorized"

    ok = control.get("/control/v1/report", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200
    assert ok.json()["game_id"] == GAME_ID
    assert ok.json()["events"] == []


def test_report_refused_before_claim(control):
    response = control.get(
        "/control/v1/report", headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 401


def test_no_runbook_read_endpoint(control):
    # Contract 2: there is deliberately no way to read the runbook back.
    assert control.get("/control/v1/runbook").status_code in (404, 405)

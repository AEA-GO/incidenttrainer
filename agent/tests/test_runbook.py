import copy

from conftest import AUTH, CLAIM, GAME_ID


def claimed(control):
    assert control.post("/control/v1/claim", json=CLAIM).status_code == 204


def test_load_runbook_runs_the_fault(control, push, runner, world):
    claimed(control)
    response = control.put("/control/v1/runbook", json=push, headers=AUTH)
    assert response.status_code == 202
    assert response.json() == {"state": "ready"}

    # The fault really changed the config and recreated the service.
    assert "SEARCH_V2=true" in world.config_file("catalog").read_text()
    argv, cwd = runner.calls[0]
    assert argv == ["docker", "compose", "-p", "dev-shop", "up", "-d", "catalog"]
    assert cwd == world.compose_dir

    assert control.get("/control/v1/ready").json()["runbook_loaded"] is True


def test_report_shows_fault_started(control, push):
    claimed(control)
    control.put("/control/v1/runbook", json=push, headers=AUTH)

    report = control.get("/control/v1/report", headers=AUTH).json()
    assert report["game_id"] == GAME_ID
    assert report["state"] == "ready"
    assert report["last_seq"] == 1
    event = report["events"][0]
    assert event["seq"] == 1
    assert event["by"] == "agent"
    assert event["type"] == "fault_started"
    assert event["t"] is None  # no game time before the page
    assert event["at"].endswith("Z")

    later = control.get("/control/v1/report?after_seq=1", headers=AUTH).json()
    assert later["events"] == []


def test_runbook_only_once(control, push):
    claimed(control)
    control.put("/control/v1/runbook", json=push, headers=AUTH)
    again = control.put("/control/v1/runbook", json=push, headers=AUTH)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_loaded"


def test_runbook_needs_claim_then_token(control, push):
    before = control.put("/control/v1/runbook", json=push, headers=AUTH)
    assert before.status_code == 409
    assert before.json()["error"]["code"] == "not_claimed"

    claimed(control)
    no_token = control.put("/control/v1/runbook", json=push)
    assert no_token.status_code == 401


def test_runbook_for_another_game_is_refused(control, push):
    claimed(control)
    other = {**push, "game_id": "B" * 22}
    response = control.put("/control/v1/runbook", json=other, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_runbook"


def test_runbook_for_another_world_is_refused(control, push, runner):
    claimed(control)
    bad = copy.deepcopy(push)
    bad["runbook"]["world"] = "teapot-basic"
    bad["runbook"]["fault"][0]["target"] = "orders"
    response = control.put("/control/v1/runbook", json=bad, headers=AUTH)
    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "teapot-basic" in message and "orders" in message
    assert runner.calls == []  # nothing ran


def test_unsupported_fault_verb_is_refused(control, push, runner):
    claimed(control)
    bad = copy.deepcopy(push)
    bad["runbook"]["fault"][0] = {
        "verb": "cpu_load",
        "target": "catalog",
        "params": {"percent": 50, "duration_s": 60},
    }
    response = control.put("/control/v1/runbook", json=bad, headers=AUTH)
    assert response.status_code == 422
    assert "cpu_load" in response.json()["error"]["message"]
    assert runner.calls == []


def test_failed_fault_leaves_the_game_not_ready(control, push, runner):
    claimed(control)
    runner.exit_code = 1
    response = control.put("/control/v1/runbook", json=push, headers=AUTH)
    assert response.status_code == 500
    assert control.get("/control/v1/ready").json()["runbook_loaded"] is False
    assert control.get("/control/v1/report", headers=AUTH).json()["events"] == []

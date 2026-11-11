"""Buttons: POST /v1/actions (step 5)."""

import copy
import dataclasses

import pytest
from conftest import AUTH, CLAIM, PLAYER
from itr_agent.verbs import VerbError, Verbs


def press(player, action_id, key="key-0001", **given):
    return player.post(
        "/v1/actions",
        json={"action_id": action_id, "input": given},
        headers={**PLAYER, "Idempotency-Key": key},
    )


def report(control):
    return control.get("/control/v1/report", headers=AUTH).json()["events"]


def test_toggle_flag_fixes_the_config(started, player, world, runner):
    assert "SEARCH_V2=true" in world.config_file("catalog").read_text()

    response = press(player, "catalog-flags", flag="SEARCH_V2", value="false")
    assert response.status_code == 200
    result = response.json()
    assert result["label"] == "Toggle flag"
    assert result["command"] == (
        "sed -i 's/^SEARCH_V2=.*/SEARCH_V2=false/' config/catalog.env"
        " && docker compose up -d catalog"
    )
    assert result["exit_code"] == 0
    assert "Recreated" in result["output"]
    assert result["t"] is None  # pressed before the page: no game time yet
    assert "SEARCH_V2=false" in world.config_file("catalog").read_text()
    assert runner.calls[-1][0][-3:] == ["up", "-d", "catalog"]


def test_action_is_recorded_with_the_hidden_risky_flag(started, player, control):
    response = press(player, "restart-storefront")
    assert "risky" not in response.json()

    action = next(e for e in report(control) if e["type"] == "action")
    assert action["data"]["action_id"] == "restart-storefront"
    assert action["data"]["verb"] == "restart"
    assert action["data"]["command"] == "docker compose restart storefront"
    assert action["data"]["risky"] is True


def test_result_appears_in_the_feed_and_recent_changes(started, agent, player, clock):
    agent.prometheus.fire("CheckoutErrorRateHigh", 30)
    agent.tick()
    clock.advance(95)
    press(player, "catalog-flags", flag="SEARCH_V2", value="false")

    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["feed"]["cursor"] == 1
    (item,) = state["feed"]["items"]
    assert item["kind"] == "action_result"
    assert item["t"] == 95
    assert item["result"]["action_id"] == "catalog-flags"
    assert state["changes"][0] == {
        "t": 95,
        "service": "catalog",
        "text": "flag SEARCH_V2 -> false",
        "by": "you",
    }
    later = player.get("/v1/state?feed_after=1", headers=PLAYER).json()
    assert later["feed"] == {"cursor": 1, "items": []}


def test_read_only_buttons_are_not_changes(started, player):
    response = press(player, "show-catalog-config")
    assert response.json()["command"] == "cat config/catalog.env"
    assert "SEARCH_V2=true" in response.json()["output"]
    state = player.get("/v1/state", headers=PLAYER).json()
    assert all(change["by"] != "you" for change in state["changes"])


def test_same_key_runs_once(started, player, runner):
    first = press(player, "restart-catalog", key="click-123")
    second = press(player, "restart-catalog", key="click-123")
    assert first.json() == second.json()
    restarts = [argv for argv, _ in runner.calls if "restart" in argv]
    assert len(restarts) == 1


def test_idempotency_key_is_required(started, player):
    response = player.post(
        "/v1/actions", json={"action_id": "restart-catalog"}, headers=PLAYER
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("action_id", "given", "status", "code"),
    [
        ("no-such-button", {}, 404, "unknown_action"),
        ("catalog-flags", {"flag": "OTHER", "value": "false"}, 422, "bad_input"),
        ("catalog-flags", {"flag": "SEARCH_V2"}, 422, "bad_input"),
        ("restart-catalog", {"extra": "1"}, 422, "bad_input"),
    ],
)
def test_bad_presses(started, player, runner, action_id, given, status, code):
    response = press(player, action_id, **given)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert len(runner.calls) == 1  # only the fault ran


def test_one_action_at_a_time(started, agent, player):
    agent._action_lock.acquire()
    try:
        response = press(player, "restart-catalog")
    finally:
        agent._action_lock.release()
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "busy"
    assert response.headers["Retry-After"] == "2"


def test_at_most_30_actions_a_minute(started, player, clock):
    for i in range(30):
        assert (
            press(player, "show-catalog-config", key=f"key-{i:04d}").status_code == 200
        )
    blocked = press(player, "show-catalog-config", key="key-9999")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "too_fast"
    clock.advance(61)
    assert press(player, "show-catalog-config", key="key-8888").status_code == 200


def test_actions_before_the_runbook(control, player):
    control.post("/control/v1/claim", json=CLAIM)
    assert press(player, "restart-catalog").status_code == 409


def test_runbook_with_impossible_buttons_is_refused(control, push):
    control.post("/control/v1/claim", json=CLAIM)
    bad = copy.deepcopy(push)
    bad["runbook"]["actions"] += [
        {"id": "roll", "verb": "rollback", "target": "catalog"},
        {"id": "grow", "verb": "scale", "target": "catalog", "input": {}},
        {"id": "dance", "verb": "dance", "target": "catalog"},
    ]
    response = control.put("/control/v1/runbook", json=bad, headers=AUTH)
    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "no earlier version" in message
    assert "can't be scaled" in message
    assert "dance" in message


# ----- verbs that the dev lab doesn't use ------------------------------------


def test_rollback_goes_one_version_back(world, runner, tmp_path):
    (tmp_path / ".env").write_text("CATALOG_VERSION=2.1.0\n")
    service = dataclasses.replace(
        world.services["catalog"], versions=("2.0.0", "2.1.0")
    )
    rich = dataclasses.replace(world, services={**world.services, "catalog": service})
    result = Verbs(rich, runner).rollback("catalog")
    assert result.output.startswith("catalog: 2.1.0 -> 2.0.0")
    assert "CATALOG_VERSION=2.0.0" in (tmp_path / ".env").read_text()
    with pytest.raises(VerbError):
        Verbs(rich, runner).rollback("catalog")  # already on the oldest


def test_scale_needs_a_scalable_service(world, runner):
    with pytest.raises(VerbError):
        Verbs(world, runner).scale("storefront", "2")
    service = dataclasses.replace(world.services["storefront"], scalable=True)
    rich = dataclasses.replace(
        world, services={**world.services, "storefront": service}
    )
    result = Verbs(rich, runner).scale("storefront", "2")
    assert result.command == "docker compose up -d --scale storefront=2 storefront"
    with pytest.raises(VerbError):
        Verbs(rich, runner).scale("storefront", "9")


def test_clear_logs_truncates_and_removes_rotated(world, runner, tmp_path):
    logs = tmp_path / "logs" / "catalog"
    logs.mkdir(parents=True)
    (logs / "app.log").write_bytes(b"x" * 2_000_000)
    (logs / "app.log.1").write_bytes(b"x" * 1_000_000)
    rich = dataclasses.replace(world, logs_dir=tmp_path / "logs")
    result = Verbs(rich, runner).clear_logs("catalog")
    assert (logs / "app.log").stat().st_size == 0
    assert not (logs / "app.log.1").exists()
    assert "truncated app.log (2.0 MB)" in result.output
    assert "removed app.log.1 (1.0 MB)" in result.output


def test_show_config_masks_secrets(world, runner):
    world.config_file("catalog").write_text("DB_PASSWORD=hunter2\nSEARCH_V2=true\n")
    output = Verbs(world, runner).show_config("catalog").output
    assert "hunter2" not in output
    assert "DB_PASSWORD=***" in output
    assert "SEARCH_V2=true" in output

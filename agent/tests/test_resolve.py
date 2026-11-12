"""Resolve, the watch, reopen and every way a game ends (steps 6 and 7)."""

import copy

import pytest
from conftest import AUTH, CLAIM, PLAYER

FIXED = {"storefront": 0.2, "catalog": 0.0}
BROKEN = {"storefront": 31.0, "catalog": 30.0}


def resolve(player):
    return player.post("/v1/resolve", headers=PLAYER)


def diary(control) -> list[dict]:
    return control.get("/control/v1/report", headers=AUTH).json()["events"]


def types(control) -> list[str]:
    return [e["type"] for e in diary(control)]


def run_watch(agent, clock, seconds: int, step: int = 10) -> None:
    for _ in range(seconds // step + 1):
        clock.advance(step)
        agent.tick()


# ----- Resolve ---------------------------------------------------------------------


def test_resolve_needs_acknowledge_first(started, agent, prometheus, player):
    prometheus.fire("CheckoutErrorRateHigh", 31.0)
    agent.tick()
    response = resolve(player)
    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Acknowledge the page first."


def test_failed_resolve_names_the_symptom_in_plain_words(
    investigating, player, control
):
    response = resolve(player)
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "failed"
    assert body["attempt"] == 1
    assert body["penalty"] == 2
    assert body["failing"] == [
        {"message": "Checkout error rate is 31% over the last minute (needs under 1%)."}
    ]
    assert "check_id" not in str(body)  # ids stay hidden from the player

    event = next(e for e in diary(control) if e["type"] == "resolve_failed")
    assert event["data"]["failing"][0]["check_id"] == "checkout-healthy"
    assert event["data"]["penalty"] == 2

    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["state"] == "investigating"
    assert state["resolve"] == {"attempts": 1, "wrong": 1, "watch_ends_in_s": None}
    notice = state["feed"]["items"][-1]
    assert notice["kind"] == "notice"
    assert notice["level"] == "error"
    assert notice["text"].startswith("Not resolved yet: Checkout error rate is 31%")


def test_health_check_failure_is_named_too(investigating, prometheus, http, player):
    prometheus.error_rates = FIXED
    http.status = 502
    body = resolve(player).json()
    assert body["failing"] == [{"message": "The storefront health check is failing."}]
    assert http.urls[0] == "http://127.0.0.1:8001/health"


def test_penalty_stops_at_six(investigating, player, clock):
    penalties = []
    for _ in range(4):
        penalties.append(resolve(player).json()["penalty"])
        clock.advance(5)
    assert penalties == [2, 2, 2, 0]


def test_resolve_too_fast(investigating, player, clock):
    resolve(player)
    clock.advance(2)
    response = resolve(player)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_fast"


def test_passed_resolve_starts_the_watch(investigating, prometheus, player, control):
    prometheus.error_rates = FIXED
    body = resolve(player).json()
    assert body == {"result": "passed", "attempt": 1, "watch_seconds": 240}

    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["state"] == "watching"
    assert state["resolve"]["watch_ends_in_s"] == 240
    assert state["feed"]["items"][-1]["text"] == (
        "Checks pass. Watching for 4:00 to make sure the fix holds."
    )
    assert "resolve_passed" in types(control)

    again = resolve(player)
    assert again.status_code == 409
    assert again.json()["error"]["message"] == "Already resolved: the watch is running."


# ----- the watch ----------------------------------------------------------------------


def test_watch_holds_and_the_game_ends_resolved(
    investigating, agent, prometheus, player, control, clock
):
    prometheus.error_rates = FIXED
    resolve(player)
    run_watch(agent, clock, 240)

    assert agent.state == "ended"
    assert agent.end_reason == "resolved"
    assert types(control)[-2:] == ["watch_passed", "ended"]
    ended = diary(control)[-1]["data"]
    assert ended == {
        "reason": "resolved",
        "final_health": agent.health,
        "severity": "SEV2",
        "reopened": False,
    }
    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["state"] == "ended"
    assert state["end_reason"] == "resolved"
    assert (
        state["feed"]["items"][-1]["text"]
        == "Resolved. The fix held for the whole watch."
    )


def test_fix_that_does_not_hold_reopens_at_sev1(
    investigating, agent, prometheus, player, control, clock
):
    prometheus.error_rates = FIXED
    resolve(player)
    clock.advance(30)
    agent.tick()
    assert agent.state == "watching"

    prometheus.error_rates = {"storefront": 12.0}  # it's back
    clock.advance(10)
    agent.tick()

    assert agent.state == "investigating"
    assert agent.severity == "SEV1"
    assert agent.reopened is True
    reopened = next(e for e in diary(control) if e["type"] == "reopened")
    assert reopened["data"]["severity"] == "SEV1"
    assert reopened["data"]["failing"][0]["check_id"] == "checkout-healthy"
    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["severity"] == "SEV1"
    assert state["feed"]["items"][-1]["text"].startswith("The fix didn't hold.")

    # Fix it for real this time; the second watch holds.
    prometheus.error_rates = FIXED
    clock.advance(5)
    assert resolve(player).json()["result"] == "passed"
    run_watch(agent, clock, 240)
    ended = diary(control)[-1]["data"]
    assert ended["reason"] == "resolved"
    assert ended["severity"] == "SEV1"
    assert ended["reopened"] is True


# ----- other endings -------------------------------------------------------------------


def test_give_up(investigating, agent, player, control, clock):
    clock.advance(100)
    response = player.post("/v1/give-up", json={"confirm": True}, headers=PLAYER)
    assert response.json() == {"state": "ended", "end_reason": "gave_up"}
    assert types(control)[-2:] == ["gave_up", "ended"]
    assert diary(control)[-1]["data"]["reason"] == "gave_up"


def test_give_up_needs_confirm(investigating, player):
    response = player.post("/v1/give-up", json={"confirm": False}, headers=PLAYER)
    assert response.status_code == 400


def test_nothing_changes_after_the_end(investigating, agent, player, clock):
    player.post("/v1/give-up", json={"confirm": True}, headers=PLAYER)
    t_at_end = agent.game_time()
    clock.advance(500)
    agent.tick()
    assert agent.game_time() == t_at_end  # the clock stopped

    for path, body in (
        ("/v1/resolve", None),
        ("/v1/give-up", {"confirm": True}),
        ("/v1/ack", None),
    ):
        response = player.post(path, json=body, headers=PLAYER)
        assert response.status_code == 409
        assert response.json()["error"]["code"] in ("game_over", "wrong_state")
    assert player.post("/v1/ack", headers=PLAYER).json()["error"]["code"] == "game_over"
    pressed = player.post(
        "/v1/actions",
        json={"action_id": "restart-catalog"},
        headers={**PLAYER, "Idempotency-Key": "late-click-1"},
    )
    assert pressed.json()["error"]["code"] == "game_over"


def test_time_limit_ends_the_game_unresolved(investigating, agent, control, clock):
    clock.advance(2400)
    agent.tick()
    assert agent.end_reason == "unresolved"
    limit = next(e for e in diary(control) if e["type"] == "time_limit")
    assert limit["data"] == {"watch_cut_short": False}
    assert limit["t"] == 2400


def test_time_limit_during_the_watch(
    investigating, agent, prometheus, player, control, clock
):
    prometheus.error_rates = FIXED
    clock.advance(2300)
    resolve(player)
    clock.advance(100)
    agent.tick()
    limit = next(e for e in diary(control) if e["type"] == "time_limit")
    assert limit["data"] == {"watch_cut_short": True}
    assert agent.end_reason == "unresolved"


def test_gateway_ends_an_abandoned_game(investigating, agent, control):
    assert (
        control.post("/control/v1/end", json={"reason": "abandoned"}).status_code == 401
    )
    response = control.post(
        "/control/v1/end", json={"reason": "abandoned"}, headers=AUTH
    )
    assert response.json() == {"state": "ended", "end_reason": "abandoned"}
    again = control.post("/control/v1/end", json={"reason": "abandoned"}, headers=AUTH)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_ended"
    report = control.get("/control/v1/report", headers=AUTH).json()
    assert report["state"] == "ended"
    assert report["end_reason"] == "abandoned"


def test_damage_checks_run_once_at_the_end(control, push, agent, prometheus, player):
    damaged = copy.deepcopy(push)
    damaged["runbook"]["checks"]["damage"] = [
        {
            "id": "orders-kept",
            "verb": "metric",
            "params": {"query": "shop_orders_rows", "op": "ge", "value": 1},
            "message": "Orders placed before the incident were lost.",
        }
    ]
    control.post("/control/v1/claim", json=CLAIM)
    control.put("/control/v1/runbook", json=damaged, headers=AUTH)
    prometheus.fire("CheckoutErrorRateHigh", 31.0)
    agent.tick()
    player.post("/v1/give-up", json={"confirm": True}, headers=PLAYER)

    events = diary(control)
    assert [e["type"] for e in events][-3:] == ["gave_up", "damage_found", "ended"]
    assert events[-2]["data"] == {
        "check_id": "orders-kept",
        "message": "Orders placed before the incident were lost.",
    }


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            lambda rb: rb["checks"]["success"].append(
                {"id": "x", "verb": "magic", "params": {}, "message": "nope nope"}
            ),
            "verb 'magic'",
        ),
        (
            lambda rb: rb["checks"]["watch"].update(checks=["nope"]),
            "unknown checks: nope",
        ),
        (lambda rb: rb["checks"].update(success=[]), "no success checks"),
    ],
)
def test_runbook_with_broken_checks_is_refused(control, push, change, expected):
    bad = copy.deepcopy(push)
    change(bad["runbook"])
    control.post("/control/v1/claim", json=CLAIM)
    response = control.put("/control/v1/runbook", json=bad, headers=AUTH)
    assert response.status_code == 422
    assert expected in response.json()["error"]["message"]

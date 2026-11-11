"""What the player sees: /v1/scene and /v1/state (step 4)."""

import json

from conftest import CLAIM, PLAYER
from itr_agent.views import scene_clock


def test_scene_before_the_runbook_is_not_ready(control, player):
    control.post("/control/v1/claim", json=CLAIM)
    response = player.get("/v1/scene", headers=PLAYER)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_ready"


def test_scene(started, player):
    scene = player.get("/v1/scene", headers=PLAYER).json()
    assert scene["lab"] == {
        "id": "dev-lab",
        "version": 1,
        "title": "Tuesday afternoon errors",
        "level": "intern",
    }
    assert scene["terminal"] is False
    assert scene["scene"] == {"starts_at": "14:20", "weekday": "Tuesday"}
    assert {s["name"] for s in scene["services"]} == {"storefront", "catalog"}
    storefront = next(s for s in scene["services"] if s["name"] == "storefront")
    assert storefront["calls"] == ["catalog"]
    assert scene["dashboards"][0]["path"] == "grafana/d/shop-overview?kiosk&theme=dark"
    assert scene["hints_total"] == 2
    labels = {a["id"]: a["label"] for a in scene["actions"]}
    assert labels["restart-catalog"] == "Restart"
    assert labels["catalog-flags"] == "Toggle flag"


def test_nothing_hidden_reaches_the_player(started, agent, player, prometheus):
    prometheus.fire("CheckoutErrorRateHigh", 30)
    agent.tick()
    seen = json.dumps(player.get("/v1/scene", headers=PLAYER).json())
    seen += json.dumps(player.get("/v1/state", headers=PLAYER).json())
    for hidden in ("risky", "tone", "checks_passing", "checkout-healthy", "fault"):
        assert hidden not in seen, hidden
    assert "SEARCH_V2=true" not in seen


def test_state_before_the_page(started, agent, player):
    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["state"] == "ready"
    assert state["clock"] == {"t": None, "time_left_s": None, "scene_time": None}
    assert state["page"]["fired"] is False
    assert state["health"]["value"] == 100


def test_state_during_the_incident(started, agent, player, prometheus, clock):
    prometheus.error_rates = {"storefront": 31.2, "catalog": 0.4}
    prometheus.fire("CheckoutErrorRateHigh", 31.2)
    agent.tick()
    clock.advance(75)
    agent.tick()

    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["state"] == "paged"
    assert state["clock"] == {"t": 75, "time_left_s": 2325, "scene_time": "14:21"}
    assert state["page"] == {
        "fired": True,
        "title": "Checkout error rate 31%",
        "acknowledged": False,
        "acknowledged_t": None,
    }
    services = {s["name"]: s for s in state["services"]}
    assert services["storefront"]["status"] == "degraded"
    assert services["storefront"]["label"] == "ERRORS"
    assert services["storefront"]["detail"] == "errors 31%"
    assert services["catalog"]["status"] == "ok"
    assert state["alerts"][0]["name"] == "CheckoutErrorRateHigh"
    # Recent changes: newest first.
    assert [c["ago_s"] for c in state["changes"]] == [1500, 86400]


def test_service_down_and_no_data(started, agent, player, prometheus):
    prometheus.fire("ServiceDown", 0, service="catalog")
    prometheus.error_rates = {"storefront": 80.0}
    agent.tick()
    services = {
        s["name"]: s for s in player.get("/v1/state", headers=PLAYER).json()["services"]
    }
    assert services["catalog"]["status"] == "down"
    assert services["catalog"]["label"] == "DOWN"
    assert services["storefront"]["label"] == "FAILING"  # >= 50 %


def test_scene_clock_wraps_midnight():
    assert scene_clock("14:20", 0) == "14:20"
    assert scene_clock("14:20", 125) == "14:22"
    assert scene_clock("23:59", 120) == "00:01"

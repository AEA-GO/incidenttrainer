"""The page, the game clock, Acknowledge and the health bar (step 4)."""

from conftest import AUTH, PLAYER


def events(control) -> list[dict]:
    return control.get("/control/v1/report", headers=AUTH).json()["events"]


def test_no_page_while_the_alert_is_quiet(started, agent, clock):
    clock.advance(30)
    agent.tick()
    assert agent.state == "ready"
    assert agent.game_time() is None


def test_alert_firing_pages_and_starts_the_clock(
    started, agent, control, prometheus, clock
):
    prometheus.fire("CheckoutErrorRateHigh", 33.4)
    clock.advance(25)
    agent.tick()

    assert agent.state == "paged"
    assert agent.page_title == "Checkout error rate 33%"
    assert agent.game_time() == 0
    clock.advance(42)
    assert agent.game_time() == 42

    paged = next(e for e in events(control) if e["type"] == "paged")
    assert paged["t"] == 0
    assert paged["data"] == {
        "source": "alert",
        "title": "Checkout error rate 33%",
        "severity": "SEV2",
    }


def test_pending_alert_does_not_page(started, agent, prometheus, clock):
    prometheus.fire("CheckoutErrorRateHigh", 12.0, state="pending")
    clock.advance(20)
    agent.tick()
    assert agent.state == "ready"


def test_other_alerts_do_not_page(started, agent, prometheus, clock):
    prometheus.fire("ServiceDown", 0, service="catalog")
    clock.advance(20)
    agent.tick()
    assert agent.state == "ready"


def test_fallback_pages_when_the_alert_is_late(
    started, agent, control, prometheus, clock
):
    # The dev lab's fallback is 90 s after the fault.
    prometheus.fire("CheckoutErrorRateHigh", 21.7, state="pending")
    clock.advance(89)
    agent.tick()
    assert agent.state == "ready"

    clock.advance(1)
    agent.tick()
    assert agent.state == "paged"
    assert agent.page_title == "Checkout error rate 22%"  # value from the pending alert
    assert next(e for e in events(control) if e["type"] == "paged")["data"][
        "source"
    ] == ("fallback")


def test_fallback_without_any_value(started, agent, clock):
    clock.advance(90)
    agent.tick()
    assert agent.page_title == "Checkout error rate ?%"


def test_page_happens_once(started, agent, control, prometheus, clock):
    prometheus.fire("CheckoutErrorRateHigh", 30)
    agent.tick()
    agent.tick()
    assert len([e for e in events(control) if e["type"] == "paged"]) == 1


def test_acknowledge(started, agent, player, control, prometheus, clock):
    early = player.post("/v1/ack", headers=PLAYER)
    assert early.status_code == 409
    assert early.json()["error"]["message"] == "There is no page to acknowledge yet."

    prometheus.fire("CheckoutErrorRateHigh", 30)
    agent.tick()
    clock.advance(41)
    response = player.post("/v1/ack", headers=PLAYER)
    assert response.status_code == 200
    assert response.json() == {"t": 41}
    assert agent.state == "investigating"

    again = player.post("/v1/ack", headers=PLAYER)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "wrong_state"

    acked = [e for e in events(control) if e["type"] == "acknowledged"]
    assert len(acked) == 1
    assert acked[0]["t"] == 41


def test_health_counts_failed_checkouts_from_the_page(
    started, agent, prometheus, clock
):
    prometheus.failed = 100.0  # failures before the page don't count
    prometheus.fire("CheckoutErrorRateHigh", 30)
    agent.tick()
    assert agent.health == 100

    prometheus.failed = 160.0  # 60 failed since the page, budget 600 -> 90%
    clock.advance(10)
    agent.tick()
    assert agent.failed_checkouts == 60
    assert agent.health == 90

    prometheus.failed = 2000.0  # never below 0
    agent.tick()
    assert agent.health == 0


def test_health_events_every_30_seconds(started, agent, control, prometheus, clock):
    prometheus.fire("CheckoutErrorRateHigh", 30)
    for _ in range(7):  # t = 0, 10, ..., 60
        agent.tick()
        clock.advance(10)
    health = [e for e in events(control) if e["type"] == "health"]
    assert [e["t"] for e in health] == [0, 30, 60]


def test_prometheus_down_keeps_last_health(started, agent, prometheus, clock):
    prometheus.fire("CheckoutErrorRateHigh", 30)
    agent.tick()
    prometheus.failed = 60.0
    agent.tick()
    assert agent.health == 90
    prometheus.failed = None  # no answer this time
    agent.tick()
    assert agent.health == 90


def test_tick_before_the_runbook_does_nothing(agent):
    agent.tick()
    assert agent.state == "booting"

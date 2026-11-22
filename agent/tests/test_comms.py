"""Status updates and hints (step 9)."""

import pytest
from conftest import AUTH, PLAYER
from itr_agent.comms import promise_t
from itr_agent.errors import AgentError

UPDATE = {
    "affected": "About a third of checkouts fail",
    "doing": "Looking at catalog",
    "next_update_at": None,
    "page_state": "investigating",
}


def post(player, **changes):
    return player.post("/v1/status-updates", json=UPDATE | changes, headers=PLAYER)


def diary(control) -> list[dict]:
    return control.get("/control/v1/report", headers=AUTH).json()["events"]


# ----- the in-world clock ------------------------------------------------------------
def test_promise_time_in_the_scene_clock():
    assert promise_t("14:20", 0, "14:35") == 900
    assert promise_t("14:20", 125, "14:35") == 900  # still 14:35 = t 900
    assert promise_t("23:50", 0, "00:20") == 1800  # across midnight


@pytest.mark.parametrize("hhmm", ["14:20", "14:10", "15:21"])
def test_promise_must_be_later_and_within_an_hour(hhmm):
    with pytest.raises(AgentError) as refused:
        promise_t("14:20", 0, hhmm)
    assert refused.value.code == "bad_input"


# ----- status updates ------------------------------------------------------------------
def test_status_update_needs_acknowledge(started, agent, prometheus, player):
    prometheus.fire("CheckoutErrorRateHigh", 31.0)
    agent.tick()
    response = post(player)
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "wrong_state"
    )


def test_status_update_writes_event_feed_and_status_page(
    investigating, player, control, clock
):
    clock.advance(60)
    response = post(player, next_update_at="14:35", page_state="identified")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fields_filled"] == 4 and body["next_update_t"] == 900

    event = next(e for e in diary(control) if e["type"] == "status_update")
    assert event["data"]["checks_passing"] is False  # the shop is still broken
    assert event["data"]["page_state"] == "identified"

    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["status_page"] == {
        "state": "identified",
        "last_update_t": body["t"],
        "next_update_at": "14:35",
    }
    item = [i for i in state["feed"]["items"] if i["kind"] == "status_update"][-1]
    assert item["status_update"]["affected"] == UPDATE["affected"]
    assert "checks_passing" not in str(state)  # hidden from the player


def test_fields_filled_counts_what_was_given(investigating, player):
    response = post(player, affected="  ", doing="", next_update_at=None)
    assert response.json()["fields_filled"] == 1  # only the page state


def test_one_update_per_10_seconds(investigating, player, clock):
    assert post(player).status_code == 200
    again = post(player)
    assert again.status_code == 429 and again.json()["error"]["code"] == "too_fast"
    clock.advance(10)
    assert post(player).status_code == 200


def test_bad_page_state_and_time_are_refused(investigating, player):
    assert post(player, page_state="fixed").status_code == 400  # not a page state
    assert post(player, next_update_at="25:00").status_code == 400  # not a time
    assert post(player, next_update_at="13:00").json()["error"]["code"] == "bad_input"


# ----- hints ----------------------------------------------------------------------------
def test_hints_in_order_with_their_wait(investigating, player, control, clock):
    state = player.get("/v1/state", headers=PLAYER).json()
    assert state["hints"] == {"used": 0, "total": 2, "next_available_in_s": 0}

    first = player.post("/v1/hints", headers=PLAYER)
    assert first.status_code == 200
    assert first.json() == {
        "n": 1,
        "text": "Which service started failing first?",
        "hints_left": 1,
    }
    second = player.post("/v1/hints", headers=PLAYER)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "hint_not_yet"

    clock.advance(300)
    assert player.post("/v1/hints", headers=PLAYER).json()["n"] == 2
    none_left = player.post("/v1/hints", headers=PLAYER)
    assert none_left.json()["error"]["code"] == "no_hints_left"

    used = [e for e in diary(control) if e["type"] == "hint_used"]
    assert [e["data"]["hint_id"] for e in used] == ["h1", "h2"]
    feed = player.get("/v1/state", headers=PLAYER).json()["feed"]["items"]
    assert [i["n"] for i in feed if i["kind"] == "hint"] == [1, 2]


def test_hints_need_acknowledge(started, agent, prometheus, player):
    prometheus.fire("CheckoutErrorRateHigh", 31.0)
    agent.tick()
    assert player.post("/v1/hints", headers=PLAYER).status_code == 409

"""Chat with Dana and Sam (step 8)."""

import copy
import json

import pytest
from conftest import AUTH, CLAIM, PLAYER

FIXED = {"storefront": 0.2, "catalog": 0.0}


def items(player, after: int = 0) -> list[dict]:
    state = player.get(f"/v1/state?feed_after={after}", headers=PLAYER).json()
    return state["feed"]["items"]


def chat(player) -> list[dict]:
    return [i for i in items(player) if i["kind"] in ("chat_message", "chat_reply")]


def said(player) -> list[str]:
    """Message ids in the order they appeared (replies as 'you')."""
    return [
        i["message_id"] if i["kind"] == "chat_message" else "you" for i in chat(player)
    ]


def diary(control, type_: str) -> list[dict]:
    events = control.get("/control/v1/report", headers=AUTH).json()["events"]
    return [e for e in events if e["type"] == type_]


def reply(player, message_id: str, option_id: str):
    return player.post(
        "/v1/chat/replies",
        json={"message_id": message_id, "option_id": option_id},
        headers=PLAYER,
    )


def press(player, action_id: str, key: str, **given: str):
    return player.post(
        "/v1/actions",
        json={"action_id": action_id, "input": given},
        headers={**PLAYER, "Idempotency-Key": key},
    )


def later(agent, clock, seconds: int) -> None:
    clock.advance(seconds)
    agent.tick()


def start_with_chat(control, push, agent, prometheus, player, chat_spec) -> None:
    """Start the dev lab with another chat, page and acknowledge."""
    custom = copy.deepcopy(push)
    custom["runbook"]["chat"] = chat_spec
    control.post("/control/v1/claim", json=CLAIM)
    response = control.put("/control/v1/runbook", json=custom, headers=AUTH)
    assert response.status_code == 202, response.json()
    prometheus.fire("CheckoutErrorRateHigh", 31.0)
    agent.tick()
    player.post("/v1/ack", headers=PLAYER)


CHASE = {
    "overdue": {"from": "dana", "text": "You said {promised}. Any news?"},
    "silence": {"from": "sam", "after_s": 600, "text": "Anything new?"},
}


# ----- messages arrive -------------------------------------------------------------


def test_messages_arrive_at_their_time(investigating, agent, player, control, clock):
    later(agent, clock, 15)
    assert chat(player) == []

    later(agent, clock, 5)  # dana-1: 20 s after Acknowledge (at t=0)
    assert said(player) == ["dana-1"]

    later(agent, clock, 39)
    assert said(player) == ["dana-1"]
    later(agent, clock, 1)  # sam-1: at t=60
    message = chat(player)[-1]
    assert message["t"] == 60
    assert message["from"] == "sam"
    assert message["text"].startswith("Getting tickets")
    assert [r["id"] for r in message["replies"]] == ["a", "b", "c"]
    assert set(message["replies"][0]) == {"id", "text"}  # no tone

    event = diary(control, "chat_message")[-1]
    assert event["t"] == 60
    assert event["data"] == {
        "message_id": "sam-1",
        "from": "sam",
        "text": "Getting tickets: 'payment failed' at checkout. How bad is it?",
        "kind": "scripted",
    }


def test_nobody_writes_before_the_page(started, agent, player, clock):
    later(agent, clock, 80)  # fallback page is at 90 s
    assert agent.state == "ready"
    assert chat(player) == []


def test_late_ticks_send_in_order(investigating, agent, player, clock):
    later(agent, clock, 100)  # both were due; dana-1 (t=20) before sam-1 (t=60)
    assert said(player) == ["dana-1", "sam-1"]


# ----- the player replies ---------------------------------------------------------


def test_reply_is_saved_with_its_tone_and_the_reaction_follows(
    investigating, agent, player, control, clock
):
    later(agent, clock, 60)
    response = reply(player, "sam-1", "b")
    assert response.status_code == 204
    assert response.content == b""

    (event,) = diary(control, "chat_reply")
    assert event["t"] == 60
    assert event["data"] == {
        "message_id": "sam-1",
        "option_id": "b",
        "text": "Should be fixed in 5 minutes.",
        "tone": "overconfident",
    }
    you, reaction = chat(player)[-2:]
    assert you["kind"] == "chat_reply"
    assert you["from"] == "you"
    assert you["text"] == "Should be fixed in 5 minutes."
    assert "tone" not in you
    assert reaction["message_id"] == "sam-1-r"
    assert reaction["from"] == "sam"
    assert reaction["text"] == "Great, I'll say 5 minutes."
    assert diary(control, "chat_message")[-1]["data"]["kind"] == "reaction"

    # Reloading the page: the answered message has no reply buttons any more.
    original = next(i for i in chat(player) if i.get("message_id") == "sam-1")
    assert "replies" not in original


def test_a_message_is_answered_once(investigating, agent, player, clock):
    later(agent, clock, 60)
    assert reply(player, "sam-1", "a").status_code == 204
    again = reply(player, "sam-1", "c")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_answered"


@pytest.mark.parametrize(
    ("message_id", "option_id", "status", "code"),
    [
        ("sam-restart", "a", 404, "unknown_message"),  # not sent yet
        ("dana-watch", "a", 404, "unknown_message"),  # not sent yet
        ("nope", "a", 404, "unknown_message"),
        ("sam-1", "z", 404, "unknown_message"),  # no such option
        ("sam-1", "A", 400, "bad_request"),  # option ids are one small letter
    ],
)
def test_bad_replies(
    investigating, agent, player, clock, message_id, option_id, status, code
):
    later(agent, clock, 60)
    response = reply(player, message_id, option_id)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_messages_without_replies_cannot_be_answered(
    investigating, agent, player, clock
):
    press(player, "restart-catalog", "restart-key-1")
    later(agent, clock, 30)
    assert "sam-restart" in said(player)
    assert reply(player, "sam-restart", "a").status_code == 404


def test_tones_never_reach_the_player(investigating, agent, player, clock):
    later(agent, clock, 60)
    reply(player, "sam-1", "a")
    seen = json.dumps(player.get("/v1/state", headers=PLAYER).json())
    for hidden in ("tone", "honest", "overconfident", "deflecting"):
        assert hidden not in seen, hidden


# ----- triggers ---------------------------------------------------------------------


def test_action_trigger_waits_its_delay_and_matches_the_verb(
    investigating, agent, player, clock
):
    press(player, "catalog-flags", "flag-key-1", flag="SEARCH_V2", value="false")
    later(agent, clock, 40)
    assert "sam-restart" not in said(player)  # set_flag isn't a restart

    press(player, "restart-catalog", "restart-key-1")  # at t=40
    later(agent, clock, 29)
    assert "sam-restart" not in said(player)
    later(agent, clock, 1)
    assert said(player).count("sam-restart") == 1

    press(player, "restart-catalog", "restart-key-2")
    later(agent, clock, 60)
    assert said(player).count("sam-restart") == 1  # each message once


def test_resolve_passed_and_reopened_triggers(
    investigating, agent, prometheus, player, clock
):
    prometheus.error_rates = FIXED
    clock.advance(70)
    assert player.post("/v1/resolve", headers=PLAYER).json()["result"] == "passed"
    later(agent, clock, 5)
    assert said(player)[-1] == "dana-watch"
    assert reply(player, "dana-watch", "a").status_code == 204  # during the watch

    prometheus.error_rates = {"storefront": 12.0}
    later(agent, clock, 5)  # the watch check fails: reopened
    assert agent.severity == "SEV1"
    later(agent, clock, 9)
    assert said(player)[-1] != "sam-reopen"
    later(agent, clock, 1)
    assert said(player)[-1] == "sam-reopen"


def test_resolve_failed_trigger_and_the_time_placeholder(
    control, push, agent, prometheus, player, clock
):
    spec = {
        "messages": [
            {
                "id": "dana-x",
                "from": "dana",
                "when": {"trigger": "resolve_failed", "delay_s": 0},
                "text": "Sam still sees errors at {t}.",
            }
        ],
        "chase": CHASE,
    }
    start_with_chat(control, push, agent, prometheus, player, spec)
    prometheus.error_rates = {"storefront": 31.0}
    clock.advance(75)
    assert player.post("/v1/resolve", headers=PLAYER).json()["result"] == "failed"
    agent.tick()  # delay 0: sent by the next loop round
    assert chat(player)[-1]["text"] == "Sam still sees errors at 1:15."


def test_status_update_triggers(control, push, agent, prometheus, player, clock):
    spec = {
        "messages": [
            {
                "id": "dana-first",
                "from": "dana",
                "when": {"trigger": "first_status_update"},
                "text": "Thanks for the update.",
            },
            {
                "id": "sam-hint",
                "from": "sam",
                "when": {"trigger": "hint_used", "delay_s": 5},
                "text": "Found anything?",
            },
        ],
        "chase": CHASE,
    }
    start_with_chat(control, push, agent, prometheus, player, spec)
    with agent._lock:  # step 9's status updates will call this
        agent.chat.status_posted(30, None)
        agent.chat.status_posted(40, None)
        agent.chat.trigger("hint_used", 40)
    clock.advance(45)
    agent.tick()
    assert said(player) == ["dana-first", "sam-hint"]


# ----- chasing -----------------------------------------------------------------------


def test_silence_is_chased_again_and_again(
    investigating, agent, player, control, clock
):
    later(agent, clock, 599)
    assert not [m for m in said(player) if m.startswith("chase")]
    later(agent, clock, 1)
    chase = chat(player)[-1]
    assert (chase["message_id"], chase["from"], chase["t"]) == ("chase-1", "sam", 600)
    assert chase["text"] == "Customers keep asking. Anything I can tell them?"
    assert diary(control, "chat_message")[-1]["data"]["kind"] == "chase"

    later(agent, clock, 599)
    assert chat(player)[-1]["message_id"] == "chase-1"
    later(agent, clock, 1)
    assert chat(player)[-1]["message_id"] == "chase-2"


def test_a_missed_promise_is_chased_once(investigating, agent, player, control, clock):
    with agent._lock:  # a status update at t=100 promising the next by t=400
        agent.chat.status_posted(100, 400)
    later(agent, clock, 459)
    assert diary(control, "update_overdue") == []
    later(agent, clock, 1)  # one minute of grace, like scoring

    (overdue,) = diary(control, "update_overdue")
    assert overdue["t"] == 460
    assert overdue["data"] == {"due_t": 400}
    chase = chat(player)[-1]
    assert chase["from"] == "dana"
    assert chase["text"] == "You said 14:26. Any news?"  # 14:20 + 400 s, in-world

    later(agent, clock, 60)
    assert len(diary(control, "update_overdue")) == 1
    # Silence counts from the last chase (460), not from the update (100).
    later(agent, clock, 539)
    assert chat(player)[-1]["message_id"] == "chase-1"
    later(agent, clock, 1)
    assert chat(player)[-1]["message_id"] == "chase-2"


def test_a_kept_promise_is_not_chased(investigating, agent, control, clock):
    with agent._lock:
        agent.chat.status_posted(100, 400)
    later(agent, clock, 420)
    with agent._lock:
        agent.chat.status_posted(420, 900)  # on time: the new promise replaces it
    later(agent, clock, 50)
    assert diary(control, "update_overdue") == []


# ----- the end ------------------------------------------------------------------------


def test_chat_stops_at_the_end(investigating, agent, player, clock):
    later(agent, clock, 60)
    player.post("/v1/give-up", json={"confirm": True}, headers=PLAYER)
    before = said(player)
    later(agent, clock, 900)
    assert said(player) == before  # no chase, nothing pending
    response = reply(player, "sam-1", "a")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "game_over"


# ----- runbook checks ----------------------------------------------------------------


def message(**changes) -> dict:
    base = {"id": "m1", "from": "sam", "when": {"at": 10}, "text": "hello there"}
    return {**base, **changes}


HONEST = {"id": "a", "tone": "honest", "text": "On it."}
VAGUE = {"id": "b", "tone": "deflecting", "text": "Later."}


@pytest.mark.parametrize(
    ("messages", "chase", "expected"),
    [
        ([message(when={"trigger": "lunch"})], CHASE, "unknown trigger 'lunch'"),
        ([message(), message()], CHASE, "appears twice"),
        ([message(id="chase-1")], CHASE, "reserved"),
        ([message(id="sam-1-r")], CHASE, "reserved"),
        ([message(**{"from": "boss"})], CHASE, "unknown sender 'boss'"),
        ([message(replies=[HONEST])], CHASE, "needs 2-3 replies"),
        (
            [message(replies=[VAGUE, {**VAGUE, "id": "c"}])],
            CHASE,
            "at least one reply must be honest",
        ),
        (
            [message(replies=[HONEST, {**VAGUE, "tone": "rude"}])],
            CHASE,
            "unknown tone 'rude'",
        ),
        (
            [message(when={"trigger": "action", "match": {"target": "billing"}})],
            CHASE,
            "unknown service 'billing'",
        ),
        (
            [message(when={"trigger": "acknowledged", "match": {"verb": "restart"}})],
            CHASE,
            "only works with the action trigger",
        ),
        ([message()], {"overdue": CHASE["overdue"]}, "chase 'silence'"),
    ],
)
def test_runbook_with_broken_chat_is_refused(control, push, messages, chase, expected):
    bad = copy.deepcopy(push)
    bad["runbook"]["chat"] = {"messages": messages, "chase": chase}
    control.post("/control/v1/claim", json=CLAIM)
    response = control.put("/control/v1/runbook", json=bad, headers=AUTH)
    assert response.status_code == 422
    assert expected in response.json()["error"]["message"]

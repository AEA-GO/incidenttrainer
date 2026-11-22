"""Cleanup (every 5 minutes): each case on moto's DynamoDB with the FakeLauncher."""

from datetime import UTC, datetime, timedelta

import pytest
from itr_api import cleanup, main, sessions
from itr_api.launcher import ServerSpec
from test_sessions import CODE, NOW, env, item, play_good_game, start  # noqa: F401 - env is a fixture


@pytest.fixture
def run(env):  # noqa: F811 - the fixture from test_sessions
    table, launcher, client = env
    deps = main.app.dependency_overrides[sessions.get_deps]()
    alerts: list[str] = []

    def at(offset_s: float) -> dict:
        deps.clock = deps.store.clock = lambda: NOW + offset_s
        return cleanup.run(deps, alerts.append)

    return table, launcher, client, at, alerts


def meta_of(table, game_id):
    return item(table, f"GAME#{game_id}", "META")


def cleanup_events(table, game_id):
    reply = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :ev)",
        ExpressionAttributeValues={":pk": f"GAME#{game_id}", ":ev": "EV#cleanup#"},
    )
    return {e["type"]: e["data"] for e in reply["Items"]}


def test_nothing_to_do(run):
    _, _, _, at, alerts = run
    assert at(0) == {
        "boot_failed": 0,
        "abandoned": 0,
        "finished": 0,
        "expired": 0,
        "orphans": 0,
        "running_fixed": None,
        "errors": 0,
    }
    assert alerts == []


def test_a_game_booting_too_long_fails_and_is_given_back(run):
    table, launcher, client, at, _ = run
    game_id = start(client).json()["game_id"]
    assert at(200)["boot_failed"] == 0  # 180 s + 60 s grace not reached yet
    report = at(260)
    assert report["boot_failed"] == 1 and report["errors"] == 0
    meta = meta_of(table, game_id)
    assert (meta["state"], meta["end_reason"], meta["ended_by"]) == (
        "ended",
        "failed",
        "api",
    )
    assert launcher.list() == []
    assert item(table, f"CODE#{CODE}", "META")["games_used"] == 0
    assert item(table, "COUNTER", "RUNNING")["count"] == 0
    events = cleanup_events(table, game_id)
    assert events["boot_failed"] == {"reason": "timeout"}
    assert events["ended"]["reason"] == "failed"
    assert events["server_terminated"]["reason"] == "boot_failed"
    assert at(600)["boot_failed"] == 0  # once only


def test_an_ended_game_is_finished_after_a_minute(run):
    table, launcher, client, at, _ = run
    game_id = start(client).json()["game_id"]
    play_good_game(table, game_id)
    table.update_item(
        Key={"pk": f"GAME#{game_id}", "sk": "META"},
        UpdateExpression="SET ended_at = :at",
        ExpressionAttributeValues={":at": "2026-12-08T18:55:00.000Z"},  # NOW + 600
    )
    assert at(630)["finished"] == 0  # the browser still has time
    report = at(700)
    assert report["finished"] == 1
    assert launcher.list() == []
    assert meta_of(table, game_id)["grade"] == "S"
    assert cleanup_events(table, game_id)["server_terminated"]["reason"] == "finished"


def test_a_server_past_45_minutes_is_deleted_and_alerted(run):
    table, launcher, client, at, alerts = run
    game_id = start(client).json()["game_id"]
    table.update_item(
        Key={"pk": f"GAME#{game_id}", "sk": "META"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={":s": "investigating"},
    )
    assert at(2700 + 60)["expired"] == 0
    report = at(2700 + 180)
    assert report["expired"] == 1 and report["errors"] == 0
    meta = meta_of(table, game_id)
    assert (meta["state"], meta["end_reason"], meta["ended_by"]) == (
        "ended",
        "abandoned",
        "cleanup",
    )
    assert meta["scored_at"] and launcher.list() == []
    assert (
        item(table, f"CODE#{CODE}", "META")["games_used"] == 1
    )  # a played game counts
    assert item(table, "COUNTER", "RUNNING")["count"] == 0
    assert cleanup_events(table, game_id)["server_terminated"]["reason"] == "expired"
    assert alerts and "past 45 min" in alerts[0]


def test_a_game_nobody_ended_is_abandoned_after_50_minutes(run):
    table, launcher, client, at, alerts = run
    game_id = start(client).json()["game_id"]
    table.update_item(
        Key={"pk": f"GAME#{game_id}", "sk": "META"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={":s": "watching"},
    )
    launcher.servers.clear()  # its server is already gone, but nothing ended the game
    report = at(3060)
    assert report["abandoned"] == 1
    assert meta_of(table, game_id)["end_reason"] == "abandoned"
    assert alerts and "50 min" in alerts[0]


def test_a_server_without_a_game_is_deleted_when_not_brand_new(run):
    _, launcher, _, at, alerts = run
    launcher.start(
        ServerSpec(
            game_id="nobody-knows-this-game",
            world="teapot-basic",
            expires_at=datetime.fromtimestamp(NOW, UTC) + timedelta(minutes=45),
        )
    )
    assert at(60)["orphans"] == 0
    assert at(200)["orphans"] == 1
    assert launcher.list() == [] and "no game uses it" in alerts[0]


def test_the_running_counter_is_corrected(run):
    table, _, client, at, _ = run
    start(client)
    table.put_item(Item={"pk": "COUNTER", "sk": "RUNNING", "count": 4})
    assert at(10)["running_fixed"] == {"from": 4, "to": 1}
    assert item(table, "COUNTER", "RUNNING")["count"] == 1
    assert at(20)["running_fixed"] is None


def test_the_lambda_routes_the_schedulers_event(run, monkeypatch):
    calls = []
    deps = main.app.dependency_overrides[sessions.get_deps]()
    monkeypatch.setattr(sessions, "_deps", deps)  # what get_deps() builds on Lambda
    monkeypatch.setattr(
        cleanup, "run", lambda deps, alert: calls.append(deps) or {"ok": 1}
    )
    assert main.handler({"itr_cleanup": True}, None) == {"ok": 1} and calls

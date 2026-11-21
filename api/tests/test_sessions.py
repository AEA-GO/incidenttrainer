"""Contract 3 game endpoints against moto's DynamoDB and S3, with the FakeLauncher."""

import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal

import boto3
import pytest
from fastapi.testclient import TestClient
from itr_api import codes, main, sessions
from itr_api.cookie import b64url, sign
from itr_api.games import GameStore, kyiv_day, month
from itr_api.labs import DictLabs, LabCard, S3Labs, card_from_json, parse_current
from itr_api.launcher.fake import FakeLauncher
from moto import mock_aws

NOW = 1796755500.0  # 2026-12-08T18:45:00Z (the contract 5 test vector's exp)
CODE = "K7QM4XPA"
KEY = b64url(bytes(range(32)))  # test only
CARD = {
    "id": "example-lab",
    "version": 3,
    "world": "teapot-basic",
    "course": {"id": "example-course", "order": 1},
    "card": {"title": "Tuesday afternoon errors", "level": "intern", "minutes": 15},
    "scoring": {"target_time_s": 300},
}


@pytest.fixture
def env():
    with mock_aws():
        db = boto3.resource("dynamodb", region_name="eu-central-1")
        table = db.create_table(
            TableName="itr-test",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.put_item(
            Item={
                "pk": "CONFIG",
                "sk": "COOKIE_KEYS",
                "current": "k1",
                "keys": {"k1": {"secret": KEY}},
            }
        )
        table.put_item(
            Item={
                "pk": f"CODE#{CODE}",
                "sk": "META",
                "active": True,
                "games_total": 6,
                "games_used": 0,
            }
        )
        launcher = FakeLauncher(now=lambda: datetime.fromtimestamp(NOW, UTC))
        deps = sessions.Deps(
            store=GameStore(table, clock=lambda: NOW),
            labs=DictLabs([card_from_json(CARD)]),
            launcher=launcher,
            clock=lambda: NOW,
        )
        main.app.dependency_overrides[sessions.get_deps] = lambda: deps
        try:
            yield table, launcher, TestClient(main.app)
        finally:
            main.app.dependency_overrides.clear()


def start(client, code="k7qm-4xpa", **body):
    payload = {"lab_id": "example-lab", "nickname": "Andrii"} | body
    return client.post("/api/sessions", json=payload, headers={"X-Invite-Code": code})


def item(table, pk, sk):
    return table.get_item(Key={"pk": pk, "sk": sk}).get("Item")


# ----- pieces ------------------------------------------------------------------------------
def test_cookie_reproduces_the_contract_test_vector():
    assert (
        sign(bytes(range(32)), "q3V9xYk2Lm8Ta1BcD4eF0g", 1796755500, "k1")
        == "v1.q3V9xYk2Lm8Ta1BcD4eF0g.1796755500.k1.-tGnC2KML72exmObhTrxlmLb5IImJIHwoBSa4GRP5v4"
    )


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("K7QM-4XPA", CODE),
        ("k7qm4xpa", CODE),
        (" k7qm 4xpa ", CODE),
        ("K7QM-4XP0", None),
        ("", None),
        (None, None),
    ],
)
def test_codes_are_normalised(typed, stored):
    assert codes.normalise(typed) == stored


def test_new_codes_use_only_the_alphabet():
    code = codes.new_code()
    assert codes.normalise(code) == code and codes.shown(code)[4] == "-"


def test_current_accepts_v_prefix():
    assert parse_current("v3\n") == 3 and parse_current("3") == 3


def test_s3_labs_reads_current_then_card():
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="labs",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        s3.put_object(Bucket="labs", Key="labs/example-lab/current", Body=b"v3")
        s3.put_object(
            Bucket="labs",
            Key="labs/example-lab/v3/card.json",
            Body=json.dumps(CARD).encode(),
        )
        labs = S3Labs("labs", s3=s3)
        card = labs.card("example-lab")
        assert (
            isinstance(card, LabCard) and card.version == 3 and card.terminal is False
        )
        assert labs.card("no-such-lab") is None
        assert labs.card("../etc") is None


# ----- invite ------------------------------------------------------------------------------
def test_invite_counts(env):
    _, _, client = env
    reply = client.get("/api/invite", headers={"X-Invite-Code": "K7QM-4XPA"})
    assert reply.status_code == 200
    assert reply.json() == {"games_total": 6, "games_left": 6, "games_left_today": 3}


@pytest.mark.parametrize(
    "header", [{}, {"X-Invite-Code": "AAAA-AAAA"}, {"X-Invite-Code": "nonsense"}]
)
def test_invite_bad_code(env, header):
    _, _, client = env
    reply = client.get("/api/invite", headers=header)
    assert reply.status_code == 401
    assert reply.json()["error"]["code"] == "bad_code"


def test_invite_disabled_code(env):
    table, _, client = env
    table.update_item(
        Key={"pk": f"CODE#{CODE}", "sk": "META"},
        UpdateExpression="SET active = :f",
        ExpressionAttributeValues={":f": False},
    )
    reply = client.get("/api/invite", headers={"X-Invite-Code": CODE})
    assert reply.status_code == 403 and reply.json()["error"]["code"] == "code_disabled"


# ----- start shift -------------------------------------------------------------------------
def test_start_shift_writes_the_game_and_sets_the_cookie(env):
    table, launcher, client = env
    reply = start(client)
    assert reply.status_code == 201, reply.text
    body = reply.json()
    game_id = body["game_id"]
    assert len(game_id) == 22 and body["state"] == "booting"
    assert body["game_path"] == f"/s/{game_id}/"

    cookie = reply.headers["set-cookie"]
    assert cookie.startswith("__Secure-itr_game=v1.")
    for part in (
        f"Path=/s/{game_id}/",
        "Max-Age=2700",
        "Secure",
        "HttpOnly",
        "SameSite=Strict",
    ):
        assert part in cookie
    value = cookie.split(";")[0].split("=", 1)[1]
    _, gid, exp, kid, sig = value.split(".")
    expected = b64url(
        hmac.new(
            bytes(range(32)), f"v1.{gid}.{exp}.{kid}".encode(), hashlib.sha256
        ).digest()
    )
    assert (gid, int(exp), kid, sig) == (game_id, int(NOW) + 2700, "k1", expected)

    meta = item(table, f"GAME#{game_id}", "META")
    assert (
        meta["state"] == "booting"
        and meta["code"] == CODE
        and meta["nickname"] == "Andrii"
    )
    assert (
        meta["lab_id"] == "example-lab"
        and meta["lab_version"] == 3
        and meta["world"] == "teapot-basic"
    )
    assert meta["scoring"] == {"level": "intern", "target_time_s": 300}
    assert meta["terminal"] is False
    assert meta["instance_id"].startswith("i-fake") and meta["private_ip"].startswith(
        "10.20."
    )
    assert meta["created_at"] == "2026-12-08T18:45:00.000Z"
    active = item(table, "ACTIVE", f"GAME#{game_id}")
    assert active["private_ip"] == meta["private_ip"]
    assert (
        item(table, f"CODE#{CODE}", f"GAME#{meta['created_at']}#{game_id}")["state"]
        == "booting"
    )
    assert item(table, f"CODE#{CODE}", "META")["games_used"] == 1
    assert item(table, f"CODE#{CODE}", f"DAY#{kyiv_day(NOW)}")["count"] == 1
    assert item(table, "COUNTER", "RUNNING")["count"] == 1
    assert item(table, "COUNTER", f"MONTH#{month(NOW)}")["games"] == 1
    events = {
        e["type"]: e
        for e in table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :ev)",
            ExpressionAttributeValues={":pk": f"GAME#{game_id}", ":ev": "EV#api#"},
        )["Items"]
    }
    assert set(events) == {"game_created", "server_started"}
    assert events["game_created"]["data"] == {
        "lab_id": "example-lab",
        "lab_version": "v3",
        "level": "intern",
    }
    assert launcher.started == [game_id]


def test_status_while_booting(env):
    _, _, client = env
    game_id = start(client).json()["game_id"]
    reply = client.get(f"/api/sessions/{game_id}", headers={"X-Invite-Code": CODE})
    assert reply.status_code == 200
    assert reply.json() == {
        "game_id": game_id,
        "lab_id": "example-lab",
        "state": "booting",
        "boot": {
            "server_started": True,
            "agent_ready": False,
            "seconds_since_start": 0,
        },
        "scored": False,
    }


def test_status_needs_the_code_that_started_the_game(env):
    table, _, client = env
    table.put_item(
        Item={"pk": "CODE#AAAAAAAA", "sk": "META", "active": True, "games_total": 6}
    )
    game_id = start(client).json()["game_id"]
    assert (
        client.get(
            f"/api/sessions/{game_id}", headers={"X-Invite-Code": "AAAAAAAA"}
        ).status_code
        == 401
    )
    assert client.get(f"/api/sessions/{game_id}").status_code == 401
    assert (
        client.get(
            "/api/sessions/" + "x" * 22, headers={"X-Invite-Code": CODE}
        ).status_code
        == 404
    )
    assert (
        client.get("/api/sessions/short", headers={"X-Invite-Code": CODE}).status_code
        == 404
    )


def test_daily_limit_then_out_of_games(env):
    table, _, client = env
    for _ in range(3):
        assert start(client).status_code == 201
    reply = start(client)
    assert reply.status_code == 429 and reply.json()["error"]["code"] == "daily_limit"
    assert 0 < int(reply.headers["retry-after"]) <= 86400
    table.update_item(
        Key={"pk": f"CODE#{CODE}", "sk": "META"},
        UpdateExpression="SET games_used = :six",
        ExpressionAttributeValues={":six": 6},
    )
    reply = start(client)
    assert reply.status_code == 429 and reply.json()["error"]["code"] == "out_of_games"


def test_all_servers_busy_writes_nothing(env):
    table, launcher, client = env
    table.put_item(Item={"pk": "COUNTER", "sk": "RUNNING", "count": 5})
    reply = start(client)
    assert reply.status_code == 503
    assert reply.json()["error"] == {
        "code": "all_servers_busy",
        "message": "All game servers are busy. Try again in a few minutes.",
        "retry_after_s": 600,
    }
    assert item(table, f"CODE#{CODE}", "META")["games_used"] == 0
    assert launcher.started == []


def test_budget_exhausted(env):
    table, _, client = env
    table.put_item(Item={"pk": "CONFIG", "sk": "LIMITS", "max_games_month": 0})
    reply = start(client)
    assert (
        reply.status_code == 503 and reply.json()["error"]["code"] == "budget_exhausted"
    )


def test_failed_start_gives_the_game_back(env):
    table, launcher, client = env
    launcher.fail_with = "capacity"
    reply = start(client)
    assert reply.status_code == 503
    assert (
        reply.json()["error"]["code"] == "start_failed"
        and reply.headers["retry-after"] == "30"
    )
    assert "set-cookie" not in reply.headers
    games = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :g)",
        ExpressionAttributeValues={":pk": f"CODE#{CODE}", ":g": "GAME#"},
    )["Items"]
    assert len(games) == 1 and games[0]["end_reason"] == "failed"
    game_id = games[0]["game_id"]
    meta = item(table, f"GAME#{game_id}", "META")
    assert (meta["state"], meta["end_reason"]) == ("ended", "failed")
    assert item(table, "ACTIVE", f"GAME#{game_id}") is None
    assert item(table, f"CODE#{CODE}", "META")["games_used"] == 0
    assert item(table, f"CODE#{CODE}", f"DAY#{kyiv_day(NOW)}")["count"] == 0
    assert item(table, "COUNTER", "RUNNING")["count"] == 0
    assert item(table, "COUNTER", f"MONTH#{month(NOW)}")["games"] == 0
    types = {
        e["type"]
        for e in table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :ev)",
            ExpressionAttributeValues={":pk": f"GAME#{game_id}", ":ev": "EV#api#"},
        )["Items"]
    }
    assert types == {"game_created", "boot_failed", "ended"}
    status = client.get(
        f"/api/sessions/{game_id}", headers={"X-Invite-Code": CODE}
    ).json()
    assert status["state"] == "ended" and status["end_reason"] == "failed"
    assert status["boot"]["agent_ready"] is False
    # and the player can simply try again
    assert start(client).status_code == 201


def test_unknown_lab_and_bad_bodies(env):
    _, launcher, client = env
    assert start(client, lab_id="no-such-lab").status_code == 404
    assert start(client, lab_id="../x").json()["error"]["code"] == "bad_request"
    assert start(client, nickname="A").status_code == 400
    assert start(client, nickname="<script>").status_code == 400
    assert start(client, nickname="  Олена  Ковальчук ").status_code == 201
    reply = client.post(
        "/api/sessions",
        json={"lab_id": "example-lab", "nickname": "Andrii", "extra": 1},
        headers={"X-Invite-Code": CODE},
    )
    assert reply.status_code == 400
    assert start(client, code="").status_code == 401
    assert launcher.started and len(launcher.started) == 1


def test_no_cookie_keys_starts_nothing(env):
    table, launcher, client = env
    table.delete_item(Key={"pk": "CONFIG", "sk": "COOKIE_KEYS"})
    deps = main.app.dependency_overrides[sessions.get_deps]()
    deps.store._keys = None
    reply = start(client)
    assert reply.status_code == 500 and reply.json()["error"]["code"] == "internal"
    assert launcher.started == []
    assert item(table, f"CODE#{CODE}", "META")["games_used"] == 0


def test_nickname_is_tidied(env):
    table, _, client = env
    game_id = start(client, nickname="  Олена   К ").json()["game_id"]
    assert item(table, f"GAME#{game_id}", "META")["nickname"] == "Олена К"


def test_lambda_handler_returns_the_cookie_to_the_http_api(env):
    """Mangum must turn Set-Cookie into the payload v2 `cookies` list (else it's dropped)."""
    event = {
        "version": "2.0",
        "routeKey": "ANY /api/{proxy+}",
        "rawPath": "/api/sessions",
        "rawQueryString": "",
        "headers": {
            "content-type": "application/json",
            "x-invite-code": CODE,
            "host": "x",
        },
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/api/sessions",
                "protocol": "HTTP/1.1",
                "sourceIp": "1.2.3.4",
            },
            "stage": "$default",
            "requestId": "r",
            "routeKey": "ANY /api/{proxy+}",
        },
        "body": json.dumps({"lab_id": "example-lab", "nickname": "Andrii"}),
        "isBase64Encoded": False,
    }
    reply = main.handler(event, None)
    assert reply["statusCode"] == 201
    assert any(c.startswith("__Secure-itr_game=v1.") for c in reply["cookies"])


# ----- finish ------------------------------------------------------------------------------
def play_good_game(table, game_id):
    """Put example 1's agent and gateway events on the game and end it, as the gateway would."""
    from test_score import GOOD

    with table.batch_writer() as batch:
        for event in GOOD:
            if event["by"] == "api":
                continue  # the API wrote its own already
            item = json.loads(json.dumps(event), parse_float=Decimal)
            batch.put_item(
                Item=item
                | {
                    "pk": f"GAME#{game_id}",
                    "sk": f"EV#{event['by']}#{event['seq']:06d}",
                }
            )
    table.update_item(
        Key={"pk": f"GAME#{game_id}", "sk": "META"},
        UpdateExpression="SET #s = :ended, end_reason = :r",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={":ended": "ended", ":r": "resolved"},
    )


def test_finish_before_the_end_says_not_ended(env):
    _, launcher, client = env
    game_id = start(client).json()["game_id"]
    reply = client.post(
        f"/api/sessions/{game_id}/finish", headers={"X-Invite-Code": CODE}
    )
    assert reply.status_code == 409
    assert (
        reply.json()["error"]["code"] == "not_ended"
        and reply.headers["retry-after"] == "3"
    )
    assert launcher.list()  # the server is untouched


def test_finish_deletes_the_server_scores_once_and_repeats_the_result(env):
    table, launcher, client = env
    game_id = start(client).json()["game_id"]
    play_good_game(table, game_id)
    first = client.post(
        f"/api/sessions/{game_id}/finish", headers={"X-Invite-Code": CODE}
    )
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["scored"] and result["score"] == 98 and result["grade"] == "S"
    assert launcher.list() == []  # the server is gone
    meta = item(table, f"GAME#{game_id}", "META")
    assert meta["score"] == 98 and meta["grade"] == "S" and meta["server_terminated_at"]
    code_game = item(table, f"CODE#{CODE}", f"GAME#{meta['created_at']}#{game_id}")
    assert (code_game["score"], code_game["grade"], code_game["state"]) == (
        98,
        "S",
        "ended",
    )
    terminated = item(table, f"GAME#{game_id}", "EV#api#000003")
    assert (
        terminated["type"] == "server_terminated"
        and terminated["data"]["reason"] == "finished"
    )

    again = client.post(
        f"/api/sessions/{game_id}/finish", headers={"X-Invite-Code": CODE}
    )
    assert again.json() == result
    status = client.get(
        f"/api/sessions/{game_id}", headers={"X-Invite-Code": CODE}
    ).json()
    assert status["scored"] is True and status["end_reason"] == "resolved"


def test_finish_needs_the_starting_code(env):
    table, _, client = env
    game_id = start(client).json()["game_id"]
    table.put_item(
        Item={"pk": "CODE#AAAAAAAA", "sk": "META", "active": True, "games_total": 6}
    )
    reply = client.post(
        f"/api/sessions/{game_id}/finish", headers={"X-Invite-Code": "AAAAAAAA"}
    )
    assert reply.status_code == 401


def test_finish_of_a_failed_start_is_not_scored(env):
    table, launcher, client = env
    launcher.fail_with = "capacity"
    start(client)
    game_id = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :g)",
        ExpressionAttributeValues={":pk": f"CODE#{CODE}", ":g": "GAME#"},
    )["Items"][0]["game_id"]
    reply = client.post(
        f"/api/sessions/{game_id}/finish", headers={"X-Invite-Code": CODE}
    )
    assert reply.status_code == 200
    assert reply.json() == {"version": "score-v0.2", "scored": False, "why": "failed"}

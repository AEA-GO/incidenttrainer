"""The session broker against a fake agent, with the laptop store and with DynamoDB (moto)."""

import json

import boto3
import pytest
from conftest import GAME_ID, NOW, Clock
from itr_gateway.broker import (
    IDLE_S,
    REPORT_EVERY_S,
    UNREACHABLE_S,
    AgentRefused,
    Broker,
    BucketRunbooks,
    FolderRunbooks,
    Unreachable,
    push_body,
)
from itr_gateway.store import FileStore, TableStore
from moto import mock_aws

BOOK = {
    "id": "checkout-down-3am",
    "version": 3,
    "limits": {"time_limit_s": 2400},
    "title": "Checkout",
}
TOKEN_SHAPE = 43


class FakeAgent:
    """An agent's control port, in memory (same answers as agent/src/itr_agent/control.py)."""

    def __init__(self) -> None:
        self.booted = False
        self.reachable = True
        self.token: str | None = None
        self.claims: list[dict] = []
        self.pushes: list[dict] = []
        self.ends = 0
        self.state = "booting"
        self.end_reason: str | None = None
        self.severity = "SEV2"
        self.reopened = False
        self.events: list[dict] = []
        self.page_size = 500

    def _up(self) -> None:
        if not self.reachable:
            raise Unreachable("connection refused")

    def add(self, type_: str, data: dict | None = None, t: int | None = 5) -> None:
        seq = len(self.events) + 1
        self.events.append(
            {
                "seq": seq,
                "at": f"2026-12-08T18:00:{seq:02d}.000Z",
                "t": t,
                "by": "agent",
                "type": type_,
                "data": data or {},
            }
        )

    def ready(self, ip: str) -> dict:
        self._up()
        return {
            "booted": self.booted,
            "claimed": self.token is not None,
            "runbook_loaded": bool(self.pushes),
            "components": {"shop": "ok" if self.booted else "starting"},
        }

    def claim(self, ip: str, game_id: str, token: str, public_base_url: str) -> None:
        self._up()
        if self.token is not None:
            raise AgentRefused(409, {"error": {"code": "already_claimed"}})
        self.token = token
        self.claims.append(
            {"game_id": game_id, "token": token, "public_base_url": public_base_url}
        )

    def _auth(self, token: str) -> None:
        if token != self.token:
            raise AgentRefused(401, {"error": {"code": "bad_token"}})

    def push(self, ip: str, token: str, body: dict) -> dict:
        self._up()
        self._auth(token)
        self.pushes.append(body)
        self.state = "ready"
        self.add("fault_started", t=None)
        return {"state": "ready"}

    def report(self, ip: str, token: str, after_seq: int) -> dict:
        self._up()
        self._auth(token)
        new = [e for e in self.events if e["seq"] > after_seq][: self.page_size]
        return {
            "state": self.state,
            "severity": self.severity,
            "reopened": self.reopened,
            "end_reason": self.end_reason,
            "last_seq": len(self.events),
            "events": new,
        }

    def end(self, ip: str, token: str) -> dict:
        self._up()
        self._auth(token)
        self.ends += 1
        self.finish("abandoned")
        return {"state": "ended", "end_reason": "abandoned"}

    def finish(self, reason: str) -> None:
        self.state, self.end_reason = "ended", reason
        self.add(
            "ended",
            {
                "reason": reason,
                "final_health": 81,
                "severity": self.severity,
                "reopened": False,
            },
        )


def booting_game(**extra) -> dict:
    return {
        "state": "booting",
        "private_ip": "10.20.3.17",
        "instance_id": "i-0abc",
        "lab_id": "checkout-down-3am",
        "lab_version": 3,
        "terminal": False,
        "created_at": "2026-12-08T18:00:00.000Z",
        "expires_at_server": int(NOW) + 2700,
        "code": "K7QM4XPA",
    } | extra


class Books:
    def __init__(self) -> None:
        self.asked: list[tuple] = []

    def runbook(self, lab_id: str, lab_version: int) -> dict:
        self.asked.append((lab_id, lab_version))
        return BOOK


# ----- the laptop store (FileStore) ----------------------------------------------------
@pytest.fixture
def laptop(tmp_path):
    clock = Clock()
    path = tmp_path / "games.json"
    path.write_text(json.dumps({"games": {GAME_ID: booting_game()}}))
    store = FileStore(path=path, keys_json="{}", clock=clock)
    agent = FakeAgent()
    broker = Broker(
        store=store,
        runbooks=Books(),
        public_base_url="https://play.example",
        agent=agent,
        clock=clock,
    )
    return broker, store, agent, clock


def events_of(store: FileStore) -> list[dict]:
    file = store.events_file(GAME_ID)
    return (
        [json.loads(line) for line in file.read_text().splitlines()]
        if file.exists()
        else []
    )


def test_waits_while_the_agent_boots_and_shows_its_progress(laptop) -> None:
    broker, store, agent, _ = laptop
    agent.reachable = False
    broker.tick()
    assert store.meta(GAME_ID)["state"] == "booting"
    agent.reachable = True
    broker.tick()
    assert store.meta(GAME_ID)["state"] == "booting"
    assert broker.components(GAME_ID) == {"shop": "starting"}
    assert agent.claims == []


def test_no_server_address_yet_means_wait(laptop) -> None:
    broker, store, agent, _ = laptop
    store._edit(GAME_ID, {"private_ip": None})
    agent.booted = True
    broker.tick()
    assert agent.claims == [] and store.meta(GAME_ID)["state"] == "booting"


def test_boot_claims_pushes_and_marks_ready(laptop) -> None:
    broker, store, agent, _ = laptop
    agent.booted = True
    broker.tick()
    meta = store.meta(GAME_ID)
    assert meta["state"] == "ready" and meta["ready_at"].endswith("Z")
    token = meta["control_token"]
    assert len(token) == TOKEN_SHAPE
    assert agent.claims == [
        {"game_id": GAME_ID, "token": token, "public_base_url": "https://play.example"}
    ]
    assert agent.pushes == [push_body(GAME_ID, BOOK)]
    assert (
        agent.pushes[0]["lab_id"] == "checkout-down-3am"
        and agent.pushes[0]["lab_version"] == 3
    )
    assert [(e["by"], e["seq"], e["type"], e["data"]) for e in events_of(store)] == [
        ("gateway", 1, "agent_ready", {"runbook_version": 3})
    ]


def test_a_boot_cut_short_after_the_claim_finishes_with_the_same_token(laptop) -> None:
    broker, store, agent, _ = laptop
    agent.booted = True
    store.save_token(GAME_ID, "x" * 43)  # this worker saved its token...
    agent.token = "x" * 43  # ...and claimed, then crashed before pushing
    broker.tick()
    assert agent.claims == []
    assert len(agent.pushes) == 1
    assert store.meta(GAME_ID)["state"] == "ready"


def test_playing_copies_events_and_progress_every_10_seconds(laptop) -> None:
    broker, store, agent, clock = laptop
    agent.booted = True
    broker.tick()  # ready
    agent.page_size = 2  # forces several report pages
    agent.state = "investigating"
    for type_ in ("paged", "acknowledged", "health", "action", "health"):
        agent.add(type_, {"value": 90} if type_ == "health" else {})
    broker.tick()
    meta = store.meta(GAME_ID)
    assert meta["state"] == "investigating" and meta["events_synced_seq"] == 6
    assert [e["seq"] for e in events_of(store) if e["by"] == "agent"] == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]
    agent.add("chat_message")
    clock.now += REPORT_EVERY_S - 1
    broker.tick()
    assert store.meta(GAME_ID)["events_synced_seq"] == 6  # not yet
    clock.now += 1
    broker.tick()
    assert store.meta(GAME_ID)["events_synced_seq"] == 7


def test_an_ended_game_is_closed_only_after_its_last_events_are_saved(laptop) -> None:
    broker, store, agent, clock = laptop
    agent.booted = True
    broker.tick()
    agent.state = "watching"
    agent.add("resolve_passed")
    agent.finish("resolved")
    clock.now += REPORT_EVERY_S
    broker.tick()
    meta = store.meta(GAME_ID)
    assert (meta["state"], meta["end_reason"]) == ("ended", "resolved")
    assert meta["events_synced_seq"] == 3
    assert events_of(store)[-1]["type"] == "ended"
    assert store.active_ids() == []


def test_an_idle_player_ends_the_game_once(laptop) -> None:
    broker, store, agent, clock = laptop
    agent.booted = True
    broker.tick()
    agent.state = "investigating"
    clock.now += IDLE_S  # ready_at counts as the last sign of life
    broker.tick()
    assert agent.ends == 1
    own = [e for e in events_of(store) if e["by"] == "gateway"]
    assert [(e["seq"], e["type"]) for e in own] == [
        (1, "agent_ready"),
        (2, "heartbeat_lost"),
    ]
    assert own[1]["data"]["last_seen_at"].endswith("Z")
    broker.tick()  # the next look sees `ended`
    meta = store.meta(GAME_ID)
    assert (meta["state"], meta["end_reason"]) == ("ended", "abandoned")
    assert agent.ends == 1


def test_a_player_who_is_still_there_is_not_idle(laptop) -> None:
    broker, store, agent, clock = laptop
    agent.booted = True
    broker.tick()
    agent.state = "investigating"
    clock.now += IDLE_S - 10
    store.heartbeat(GAME_ID, clock.now)
    clock.now += 20
    broker.tick()
    assert agent.ends == 0


def test_an_agent_silent_for_60_seconds_is_ended_by_the_gateway(laptop) -> None:
    broker, store, agent, clock = laptop
    agent.booted = True
    broker.tick()
    agent.state = "investigating"
    agent.add("health", {"value": 64, "failed_checkouts": 30})
    clock.now += REPORT_EVERY_S
    broker.tick()
    agent.reachable = False
    for _ in range(UNREACHABLE_S // REPORT_EVERY_S):
        clock.now += REPORT_EVERY_S
        broker.tick()
        assert store.meta(GAME_ID)["state"] == "investigating"  # not before 60 s
    clock.now += REPORT_EVERY_S
    broker.tick()
    meta = store.meta(GAME_ID)
    assert (meta["state"], meta["end_reason"]) == ("ended", "abandoned")
    ended = events_of(store)[-1]
    assert (ended["by"], ended["seq"], ended["type"]) == ("gateway", 3, "ended")
    assert ended["data"] == {"reason": "abandoned", "final_health": 64}


def test_the_boot_screen_kicks_the_broker(tmp_path) -> None:
    from conftest import KEY, KEYS
    from fastapi.testclient import TestClient
    from itr_gateway.app import Settings, build_app
    from itr_gateway.cookie import sign

    path = tmp_path / "games.json"
    path.write_text(json.dumps({"games": {GAME_ID: booting_game()}}))
    clock = Clock()
    store = FileStore(path=path, keys_json=json.dumps(KEYS), clock=clock)
    agent = FakeAgent()
    agent.booted = True
    broker = Broker(
        store=store,
        runbooks=Books(),
        public_base_url="https://play.example",
        agent=agent,
        clock=clock,
    )
    client = TestClient(build_app(store, Settings(), clock=clock, broker=broker))
    cookie = f"__Secure-itr_game={sign(KEY, GAME_ID, int(NOW) + 600, 'k1')}"
    answer = client.get(
        "/auth",
        headers={"X-Original-URI": f"/s/{GAME_ID}/api/v1/state", "Cookie": cookie},
    )
    assert answer.headers["x-itr-code"] == "boot_state"
    broker._pool.shutdown(wait=True)
    assert store.meta(GAME_ID)["state"] == "ready"


# ----- DynamoDB (moto) ------------------------------------------------------------------
@pytest.fixture
def table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="eu-central-1")
        table = dynamodb.create_table(
            TableName="itr-main",
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
        game = booting_game()
        table.put_item(Item={"pk": f"GAME#{GAME_ID}", "sk": "META"} | game)
        table.put_item(
            Item={"pk": "ACTIVE", "sk": f"GAME#{GAME_ID}", "state": "booting"}
        )
        table.put_item(Item={"pk": "COUNTER", "sk": "RUNNING", "count": 2})
        table.put_item(
            Item={
                "pk": f"CODE#{game['code']}",
                "sk": f"GAME#{game['created_at']}#{GAME_ID}",
                "state": "booting",
            }
        )
        yield table


def test_a_whole_game_in_dynamodb(table) -> None:
    clock = Clock()
    store = TableStore(table=table, clock=clock)
    agent = FakeAgent()
    agent.booted = True
    broker = Broker(
        store=store,
        runbooks=Books(),
        public_base_url="https://play.example",
        agent=agent,
        clock=clock,
    )

    assert store.active_ids() == [GAME_ID]
    broker.tick()
    assert (
        table.get_item(Key={"pk": "ACTIVE", "sk": f"GAME#{GAME_ID}"})["Item"]["state"]
        == "ready"
    )
    agent.state = "investigating"
    agent.add(
        "health", {"value": 97.5, "failed_checkouts": 3}
    )  # a float: stored as a number
    agent.finish("resolved")
    clock.now += REPORT_EVERY_S
    broker.tick()

    meta = table.get_item(Key={"pk": f"GAME#{GAME_ID}", "sk": "META"})["Item"]
    assert (meta["state"], meta["end_reason"], meta["events_synced_seq"]) == (
        "ended",
        "resolved",
        3,
    )
    assert "Item" not in table.get_item(Key={"pk": "ACTIVE", "sk": f"GAME#{GAME_ID}"})
    assert table.get_item(Key={"pk": "COUNTER", "sk": "RUNNING"})["Item"]["count"] == 1
    code_item = table.get_item(
        Key={"pk": "CODE#K7QM4XPA", "sk": f"GAME#2026-12-08T18:00:00.000Z#{GAME_ID}"}
    )
    assert code_item["Item"]["state"] == "ended"
    events = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :ev)",
        ExpressionAttributeValues={":pk": f"GAME#{GAME_ID}", ":ev": "EV#"},
    )["Items"]
    assert [e["sk"] for e in events] == [
        "EV#agent#000001",
        "EV#agent#000002",
        "EV#agent#000003",
        "EV#gateway#000001",
    ]
    assert all(e["expires_at"] > NOW for e in events)
    assert store.last_health(GAME_ID) == 97
    # ending twice does nothing (the API or cleanup may have ended it first)
    assert store.finish(GAME_ID, "abandoned", "2026-12-08T18:30:00.000Z") is False
    assert table.get_item(Key={"pk": "COUNTER", "sk": "RUNNING"})["Item"]["count"] == 1


def test_only_one_worker_gets_to_claim(table) -> None:
    first, second = TableStore(table=table), TableStore(table=table)
    assert first.save_token(GAME_ID, "a" * 43) == "a" * 43
    assert second.save_token(GAME_ID, "b" * 43) == "a" * 43


def test_ending_still_works_when_the_running_counter_is_already_zero(table) -> None:
    table.put_item(Item={"pk": "COUNTER", "sk": "RUNNING", "count": 0})
    store = TableStore(table=table)
    assert store.finish(GAME_ID, "abandoned", "2026-12-08T18:30:00.000Z") is True
    assert table.get_item(Key={"pk": "COUNTER", "sk": "RUNNING"})["Item"]["count"] == 0
    assert "Item" not in table.get_item(Key={"pk": "ACTIVE", "sk": f"GAME#{GAME_ID}"})


# ----- where runbooks come from -------------------------------------------------------
def test_runbooks_from_the_bucket_and_from_a_laptop_folder(tmp_path) -> None:
    with mock_aws():
        s3 = boto3.client("s3", region_name="eu-central-1")
        s3.create_bucket(
            Bucket="itr-main-labs",
            CreateBucketConfiguration={"LocationConstraint": "eu-central-1"},
        )
        s3.put_object(
            Bucket="itr-main-labs",
            Key="labs/checkout-down-3am/v3/runbook.json",
            Body=json.dumps(BOOK),
        )
        assert (
            BucketRunbooks(bucket="itr-main-labs", s3=s3).runbook(
                "checkout-down-3am", 3
            )
            == BOOK
        )
    (tmp_path / "checkout-down-3am").mkdir()
    (tmp_path / "checkout-down-3am" / "push.json").write_text(
        json.dumps({"game_id": "x", "runbook": BOOK})
    )
    assert FolderRunbooks(folder=tmp_path).runbook("checkout-down-3am", 3) == BOOK

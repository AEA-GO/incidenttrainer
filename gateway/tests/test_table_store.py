"""TableStore against a fake DynamoDB (moto): the same items the API and broker write."""

import boto3
import pytest
from conftest import EXP, GAME_ID, KEY, KEY_B64, NOW, Clock
from itr_gateway.store import TableStore
from moto import mock_aws


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
        table.put_item(
            Item={
                "pk": f"GAME#{GAME_ID}",
                "sk": "META",
                "state": "investigating",
                "private_ip": "10.20.3.17",
                "instance_id": "i-0abc",
                "terminal": True,
                "created_at": "2026-12-08T18:00:00.000Z",
                "expires_at_server": EXP,  # stored as a DynamoDB number (Decimal)
            }
        )
        table.put_item(
            Item={
                "pk": "CONFIG",
                "sk": "COOKIE_KEYS",
                "current": "k1",
                "keys": {"k1": {"secret": KEY_B64}},
            }
        )
        yield table


def test_reads_a_game_and_the_keys(table) -> None:
    store = TableStore(table=table, clock=Clock())
    game = store.game(GAME_ID)
    assert game.state == "investigating"
    assert game.private_ip == "10.20.3.17"
    assert game.terminal is True
    assert game.expires_at_server == EXP
    assert game.created_at == 1796752800.0
    assert store.key("k1") == KEY
    assert store.key("k2") is None
    assert store.game("AAAAAAAAAAAAAAAAAAAAAA") is None


def test_a_game_is_cached_60_seconds(table) -> None:
    clock = Clock()
    store = TableStore(table=table, clock=clock)
    store.game(GAME_ID)
    table.update_item(
        Key={"pk": f"GAME#{GAME_ID}", "sk": "META"},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "state"},
        ExpressionAttributeValues={":s": "watching"},
    )
    clock.now = NOW + 59
    assert store.game(GAME_ID).state == "investigating"
    clock.now = NOW + 60
    assert store.game(GAME_ID).state == "watching"
    store.forget(GAME_ID)


def test_heartbeat_moves_forward_only_and_never_creates_a_game(table) -> None:
    def last_seen() -> int | None:
        item = table.get_item(Key={"pk": f"GAME#{GAME_ID}", "sk": "META"})["Item"]
        return item.get("last_seen_at")

    first, second = (
        TableStore(table=table),
        TableStore(table=table),
    )  # two gateway workers
    first.heartbeat(GAME_ID, NOW)
    assert last_seen() == int(NOW)
    second.heartbeat(
        GAME_ID, NOW + 10
    )  # the other worker, too soon: condition stops it
    assert last_seen() == int(NOW)
    second.heartbeat(GAME_ID, NOW + 40)
    assert last_seen() == int(NOW + 40)
    first.heartbeat("AAAAAAAAAAAAAAAAAAAAAA", NOW)
    assert "Item" not in table.get_item(
        Key={"pk": "GAME#AAAAAAAAAAAAAAAAAAAAAA", "sk": "META"}
    )


def test_missing_keys_refuse_every_cookie_without_crashing(table) -> None:
    table.delete_item(Key={"pk": "CONFIG", "sk": "COOKIE_KEYS"})
    assert TableStore(table=table).key("k1") is None


def test_a_short_key_is_rejected(table) -> None:
    table.put_item(
        Item={"pk": "CONFIG", "sk": "COOKIE_KEYS", "keys": {"k1": {"secret": "AAEC"}}}
    )
    assert TableStore(table=table).key("k1") is None

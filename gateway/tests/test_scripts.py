"""The by-hand scripts in gateway/scripts against fake AWS (moto)."""

import argparse
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from itr_gateway import cookie
from itr_gateway.store import TableStore
from moto import mock_aws

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


keys_script = load("cookie_keys")
hand = load("hand_game")


@pytest.fixture
def aws():
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
        yield table


# ----- cookie_keys.py ------------------------------------------------------------------
def run_keys(table, *argv) -> None:
    keys_script.main(list(argv), tbl=table, now=NOW)


def test_first_key_is_current_and_secrets_are_never_printed(aws, capsys) -> None:
    run_keys(aws, "add")
    item = keys_script.load(aws)
    assert item["current"] == "k1"
    secret = item["keys"]["k1"]["secret"]
    assert len(cookie.b64url_decode(secret)) == 32
    out = capsys.readouterr().out
    assert "k1" in out and "current" in out and secret not in out
    assert TableStore(table=aws).key("k1") == cookie.b64url_decode(
        secret
    )  # the gateway reads it


def test_a_rotation(aws, capsys) -> None:
    run_keys(aws, "add")
    run_keys(aws, "add")
    assert keys_script.load(aws)["current"] == "k1"  # a new key isn't used until `use`
    run_keys(aws, "use", "k2")
    with pytest.raises(SystemExit, match="current key"):
        run_keys(aws, "remove", "k2")
    run_keys(aws, "remove", "k1")
    item = keys_script.load(aws)
    assert item["current"] == "k2" and set(item["keys"]) == {"k2"}
    assert item["keys"]["k2"]["secret"] not in capsys.readouterr().out


def test_two_people_rotating_at_once_cannot_lose_a_key(aws) -> None:
    run_keys(aws, "add")
    stale = keys_script.load(aws)
    run_keys(aws, "add")  # someone else, meanwhile
    new, _ = keys_script.add(stale, NOW)
    with pytest.raises(SystemExit, match="changed the keys"):
        keys_script.save(aws, stale, new)
    assert set(keys_script.load(aws)["keys"]) == {"k1", "k2"}


# ----- hand_game.py ---------------------------------------------------------------------
def test_publish_then_start_a_game_the_gateway_accepts(aws, tmp_path) -> None:
    s3 = boto3.client("s3", region_name="eu-central-1")
    s3.create_bucket(
        Bucket="labs", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
    )
    push = tmp_path / "push.json"
    book = {"id": "checkout-down-3am", "version": 4, "limits": {"time_limit_s": 2400}}
    push.write_text(
        json.dumps(
            {
                "game_id": "x",
                "lab_id": "checkout-down-3am",
                "lab_version": 4,
                "runbook": book,
            }
        )
    )
    assert hand.publish(push, s3=s3, bucket="labs").endswith("(current = v4)")
    stored = s3.get_object(Bucket="labs", Key="labs/checkout-down-3am/v4/runbook.json")[
        "Body"
    ].read()
    assert json.loads(stored) == book

    ec2 = boto3.client("ec2", region_name="eu-central-1")
    vpc = ec2.create_vpc(CidrBlock="10.20.0.0/16")["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.20.0.0/20")["Subnet"][
        "SubnetId"
    ]
    ec2.run_instances(
        ImageId="ami-12c6146b",
        MinCount=1,
        MaxCount=1,
        InstanceType="t4g.medium",
        SubnetId=subnet,
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": "itr-image-test"}],
            }
        ],
    )
    keys_script.main(["add"], tbl=aws, now=NOW)

    args = argparse.Namespace(
        lab="checkout-down-3am",
        version=None,
        instance=None,
        table="itr-main",
        bucket="labs",
    )
    game = hand.start(args, ec2=ec2, tbl=aws, s3=s3)
    meta = aws.get_item(Key={"pk": f"GAME#{game['game_id']}", "sk": "META"})["Item"]
    assert (meta["state"], meta["lab_version"], meta["world"]) == (
        "booting",
        4,
        "teapot-basic",
    )
    assert meta["private_ip"].startswith("10.20.")
    assert "Item" in aws.get_item(Key={"pk": "ACTIVE", "sk": f"GAME#{game['game_id']}"})
    assert len(game["game_id"]) == 22

    # the cookie it prints passes gateway-auth's check for this game
    name, value = game["cookie"].split("=", 1)
    assert name == cookie.COOKIE_NAME
    checked = cookie.check(
        value, TableStore(table=aws).key, now=meta["expires_at_server"] - 60
    )
    assert checked.game_id == game["game_id"]
    assert hand.status(game["game_id"], aws)[0] == "state: booting"


def test_start_needs_a_test_server_and_keys(aws) -> None:
    ec2 = boto3.client("ec2", region_name="eu-central-1")
    args = argparse.Namespace(
        lab="x", version=1, instance=None, table="itr-main", bucket="labs"
    )
    with pytest.raises(SystemExit, match="No running test server"):
        hand.start(args, ec2=ec2, tbl=aws, s3=None)

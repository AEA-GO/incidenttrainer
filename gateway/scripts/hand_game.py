"""A game on AWS made by hand, until the API's "Start shift" exists (gateway step 4c).

    uv run scripts/hand_game.py publish ~/IncTrainer/incidenttrainer-labs/out/checkout-down-3am/push.json
    uv run scripts/hand_game.py start                # on the running test server (images/test-server.sh)
    uv run scripts/hand_game.py status <game_id>

`publish` puts a lab's runbook where the gateway reads it: labs/<lab>/v<version>/runbook.json
in the private lab bucket, plus labs/<lab>/current (labs CI will do this later).
`start` writes the game as the API will (GAME#<id>/META state booting + ACTIVE), signs a game
cookie with the current key and prints what play.py needs. The gateway's broker then boots
the game by itself: claims the agent, pushes the runbook, copies events.
`status` shows the game as the API will read it.
"""

import argparse
import json
import os
import secrets
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from itr_gateway.cookie import COOKIE_NAME, LIFETIME_S, b64url_decode, sign

REGION = os.environ.get("AWS_REGION", "eu-central-1")
PUBLIC_ORIGIN = "https://drer7le45ultl.cloudfront.net"
SANDBOX_PREFIX = "10.20."  # sandbox subnet 10.20.0.0/20
DAY_S = 86400


def aws(service: str):
    import boto3

    return boto3.client(service, region_name=REGION)


def labs_bucket() -> str:
    return f"itr-main-labs-{aws('sts').get_caller_identity()['Account']}"


def iso_ms(seconds: float) -> str:
    stamp = datetime.fromtimestamp(seconds, UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def publish(push_file: Path, s3=None, bucket: str | None = None) -> str:
    push = json.loads(push_file.read_text())
    lab, version = push["lab_id"], int(push["lab_version"])
    s3, bucket = s3 or aws("s3"), bucket or labs_bucket()
    key = f"labs/{lab}/v{version}/runbook.json"
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(push["runbook"]).encode(),
        ContentType="application/json",
    )
    s3.put_object(
        Bucket=bucket,
        Key=f"labs/{lab}/current",
        Body=str(version).encode(),
        ContentType="text/plain",
    )
    return f"s3://{bucket}/{key} (current = v{version})"


def test_server(ec2, instance_id: str | None) -> dict:
    filters = [{"Name": "instance-state-name", "Values": ["running"]}]
    if instance_id:
        reply = ec2.describe_instances(InstanceIds=[instance_id], Filters=filters)
    else:
        reply = ec2.describe_instances(
            Filters=filters + [{"Name": "tag:Name", "Values": ["itr-image-test"]}]
        )
    servers = [i for r in reply["Reservations"] for i in r["Instances"]]
    if not servers:
        sys.exit(
            "No running test server: run images/test-server.sh start in the labs repo first."
        )
    server = servers[0]
    if not server.get("PrivateIpAddress", "").startswith(SANDBOX_PREFIX):
        sys.exit(f"{server['InstanceId']} isn't in the sandbox subnet")
    return server


def start(args, ec2=None, tbl=None, s3=None, now: float | None = None) -> dict:
    ec2, s3 = ec2 or aws("ec2"), s3 or aws("s3")
    if tbl is None:
        import boto3

        tbl = boto3.resource("dynamodb", region_name=REGION).Table(args.table)
    now = now or time.time()
    server = test_server(ec2, args.instance)
    version = args.version
    if version is None:
        body = s3.get_object(
            Bucket=args.bucket or labs_bucket(), Key=f"labs/{args.lab}/current"
        )["Body"].read()
        version = int(body.decode().strip())
    keys = tbl.get_item(
        Key={"pk": "CONFIG", "sk": "COOKIE_KEYS"}, ConsistentRead=True
    ).get("Item")
    if not keys or not keys.get("current"):
        sys.exit("No cookie keys yet: run scripts/cookie_keys.py add first.")

    game_id = secrets.token_urlsafe(16)  # 22 characters, 128 bits (data-model.md)
    launched = server["LaunchTime"].timestamp()
    meta = {
        "pk": f"GAME#{game_id}",
        "sk": "META",
        "code": "HANDGAME",
        "lab_id": args.lab,
        "lab_version": version,
        "world": "teapot-basic",
        "terminal": False,
        "nickname": "hand",
        "state": "booting",
        "instance_id": server["InstanceId"],
        "private_ip": server["PrivateIpAddress"],
        "expires_at_server": int(launched) + LIFETIME_S,
        "created_at": iso_ms(now),
        "expires_at": int(now) + 30 * DAY_S,
    }
    tbl.put_item(Item=meta, ConditionExpression="attribute_not_exists(pk)")
    tbl.put_item(
        Item={
            "pk": "ACTIVE",
            "sk": f"GAME#{game_id}",
            "instance_id": server["InstanceId"],
            "private_ip": server["PrivateIpAddress"],
            "state": "booting",
            "expires_at": int(now) + DAY_S,
        }
    )
    kid = keys["current"]
    secret = b64url_decode(keys["keys"][kid]["secret"])
    cookie = sign(
        secret, game_id, min(int(now) + LIFETIME_S, int(launched) + LIFETIME_S), kid
    )
    return {
        "game_id": game_id,
        "cookie": f"{COOKIE_NAME}={cookie}",
        "server": server["InstanceId"],
        "version": version,
    }


def status(game_id: str, tbl) -> list[str]:
    items = tbl.query(
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": f"GAME#{game_id}"},
    )["Items"]
    meta = next((i for i in items if i["sk"] == "META"), None)
    if meta is None:
        return [f"No game {game_id}."]
    fields = (
        "state",
        "end_reason",
        "severity",
        "reopened",
        "ready_at",
        "ended_at",
        "last_seen_at",
        "events_synced_seq",
    )
    lines = [
        f"{name}: {meta.get(name)}" for name in fields if meta.get(name) is not None
    ]
    events = Counter(
        f"{i.get('by')}:{i.get('type')}" for i in items if i["sk"].startswith("EV#")
    )
    lines.append(
        "events: "
        + (", ".join(f"{k} x{v}" for k, v in sorted(events.items())) or "none yet")
    )
    return lines


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="A hand-made game on AWS (gateway step 4c)."
    )
    parser.add_argument("--table", default=os.environ.get("ITR_TABLE", "itr-main"))
    parser.add_argument(
        "--bucket", default=None, help="lab bucket (default itr-main-labs-<account>)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("publish").add_argument("push_file", type=Path)
    begin = sub.add_parser("start")
    begin.add_argument("--lab", default="checkout-down-3am")
    begin.add_argument(
        "--version", type=int, default=None, help="default: labs/<lab>/current"
    )
    begin.add_argument(
        "--instance", default=None, help="default: the running itr-image-test server"
    )
    sub.add_parser("status").add_argument("game_id")
    args = parser.parse_args(argv)

    if args.command == "publish":
        print("Published", publish(args.push_file, bucket=args.bucket))
    elif args.command == "start":
        game = start(args)
        print(
            f"Game {game['game_id']} (v{game['version']}) on {game['server']}: booting. The gateway boots it."
        )
        print("For play.py (the cookie lives 45 min; keep it to yourself):")
        print(f"  export ITR_GATEWAY={PUBLIC_ORIGIN} ITR_GAME_ID={game['game_id']}")
        print(f"  export ITR_GAME_COOKIE='{game['cookie']}'")
    else:
        import boto3

        tbl = boto3.resource("dynamodb", region_name=REGION).Table(args.table)
        print("\n".join(status(args.game_id, tbl)))


if __name__ == "__main__":
    main()

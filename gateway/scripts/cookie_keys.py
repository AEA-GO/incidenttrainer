"""The game-cookie keys in DynamoDB (CONFIG / COOKIE_KEYS, contract 5). Run by hand.

    uv run scripts/cookie_keys.py show            # key ids, which is current, when made (never secrets)
    uv run scripts/cookie_keys.py add             # a new key; the first one also becomes current
    uv run scripts/cookie_keys.py use k2          # sign new cookies with k2
    uv run scripts/cookie_keys.py remove k1       # stop accepting k1 (never the current key)

Rotation: `add`, wait 5 min (gateway and API cache the keys), `use` the new one, wait
50 min (longer than any cookie lives), `remove` the old one. Rotate when someone with table
access leaves, or yearly. Secrets are 32 random bytes from `secrets`, written only to the
table: never to Terraform state, files or the screen. Every write is conditional on the
item's `rev`, so two people rotating at once can't lose a key.
"""

import argparse
import os
import secrets
import sys
from datetime import UTC, datetime

from itr_gateway.cookie import b64url

KEY = {"pk": "CONFIG", "sk": "COOKIE_KEYS"}


def table(name: str):
    import boto3

    return boto3.resource(
        "dynamodb", region_name=os.environ.get("AWS_REGION", "eu-central-1")
    ).Table(name)


def load(tbl) -> dict:
    return tbl.get_item(Key=KEY, ConsistentRead=True).get("Item") or {
        "current": None,
        "keys": {},
        "rev": 0,
    }


def save(tbl, old: dict, new: dict) -> None:
    from botocore.exceptions import ClientError

    item = KEY | {
        "current": new["current"],
        "keys": new["keys"],
        "rev": int(old.get("rev", 0)) + 1,
    }
    condition = "attribute_not_exists(pk)" if "pk" not in old else "rev = :rev"
    args: dict = {"Item": item, "ConditionExpression": condition}
    if "pk" in old:
        args["ExpressionAttributeValues"] = {":rev": int(old.get("rev", 0))}
    try:
        tbl.put_item(**args)
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            sys.exit("Someone else changed the keys just now: run it again.")
        raise


def show(item: dict) -> list[str]:
    lines = []
    for kid in sorted(item["keys"], key=lambda k: int(k[1:])):
        mark = "  <- current (signs new cookies)" if kid == item["current"] else ""
        lines.append(f"{kid}  made {item['keys'][kid].get('created_at', '?')}{mark}")
    return lines or ["no keys yet: run `add`"]


def add(item: dict, now: datetime) -> tuple[dict, str]:
    numbers = [int(kid[1:]) for kid in item["keys"]]
    kid = f"k{max(numbers, default=0) + 1}"
    keys = dict(item["keys"])
    keys[kid] = {
        "secret": b64url(secrets.token_bytes(32)),
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return {"current": item["current"] or kid, "keys": keys}, kid


def use(item: dict, kid: str) -> dict:
    if kid not in item["keys"]:
        sys.exit(f"No key {kid}. Keys: {', '.join(item['keys']) or 'none'}")
    return {"current": kid, "keys": item["keys"]}


def remove(item: dict, kid: str) -> dict:
    if kid == item["current"]:
        sys.exit(f"{kid} is the current key: `use` another one first.")
    if kid not in item["keys"]:
        sys.exit(f"No key {kid}.")
    keys = {k: v for k, v in item["keys"].items() if k != kid}
    return {"current": item["current"], "keys": keys}


def main(argv: list[str] | None = None, tbl=None, now: datetime | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Game-cookie keys in DynamoDB (contract 5)."
    )
    parser.add_argument("--table", default=os.environ.get("ITR_TABLE", "itr-main"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    sub.add_parser("add")
    sub.add_parser("use").add_argument("kid")
    sub.add_parser("remove").add_argument("kid")
    args = parser.parse_args(argv)
    tbl = tbl or table(args.table)
    now = now or datetime.now(UTC)

    item = load(tbl)
    if args.command == "show":
        print("\n".join(show(item)))
        return
    if args.command == "add":
        new, kid = add(item, now)
        save(tbl, item, new)
        print(
            f"Added {kid}."
            + (
                " It is current."
                if new["current"] == kid
                else f" Wait 5 min, then `use {kid}`."
            )
        )
    elif args.command == "use":
        save(tbl, item, use(item, args.kid))
        print(f"{args.kid} signs new cookies now (the API picks it up within 5 min).")
    else:
        save(tbl, item, remove(item, args.kid))
        print(f"Removed {args.kid}: cookies signed with it stop working within 5 min.")
    print("\n".join(show(load(tbl))))


if __name__ == "__main__":
    main()

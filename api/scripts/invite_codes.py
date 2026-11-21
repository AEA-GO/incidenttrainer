"""Invite codes (data-model.md), by hand until there's an admin page.

    uv run --with "botocore[crt]" python scripts/invite_codes.py add --note "Andrii, testing"
    uv run --with "botocore[crt]" python scripts/invite_codes.py add --count 5 --note "friends test"
    uv run --with "botocore[crt]" python scripts/invite_codes.py list
    uv run --with "botocore[crt]" python scripts/invite_codes.py off K7QM-4XPA

The note says who got the code; it is ours only and never shown to players.
Codes are not secrets in the cryptographic sense, but each one is a person's games:
send them privately, never in a public chat or repo.
"""

import argparse
import os
import sys
from datetime import UTC, datetime

from itr_api import codes

REGION = os.environ.get("AWS_REGION", "eu-central-1")


def table(name: str):
    import boto3

    return boto3.resource("dynamodb", region_name=REGION).Table(name)


def add(tbl, note: str, games: int, count: int = 1) -> list[str]:
    from botocore.exceptions import ClientError

    made = []
    while len(made) < count:
        code = codes.new_code()
        try:
            tbl.put_item(
                Item={
                    "pk": f"CODE#{code}",
                    "sk": "META",
                    "active": True,
                    "games_total": games,
                    "games_used": 0,
                    "note": note,
                    "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                continue  # already taken: draw another
            raise
        made.append(code)
    return made


def listing(tbl) -> list[str]:
    """Every code (a scan: fine for a small table, scripts only)."""
    args: dict = {
        "FilterExpression": "begins_with(pk, :code) AND sk = :meta",
        "ExpressionAttributeValues": {":code": "CODE#", ":meta": "META"},
    }
    rows = []
    while True:
        reply = tbl.scan(**args)
        for item in reply["Items"]:
            code = item["pk"].removeprefix("CODE#")
            state = "on " if item.get("active") else "off"
            used = f"{int(item.get('games_used', 0))}/{int(item.get('games_total', 0))}"
            rows.append(
                f"{codes.shown(code)}  {state}  {used:>5}  {item.get('note', '')}"
            )
        if "LastEvaluatedKey" not in reply:
            return sorted(rows)
        args["ExclusiveStartKey"] = reply["LastEvaluatedKey"]


def switch_off(tbl, typed: str) -> str:
    code = codes.normalise(typed)
    if code is None:
        sys.exit(f"{typed!r} isn't an invite code")
    tbl.update_item(
        Key={"pk": f"CODE#{code}", "sk": "META"},
        UpdateExpression="SET active = :off",
        ConditionExpression="attribute_exists(pk)",
        ExpressionAttributeValues={":off": False},
    )
    return codes.shown(code)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Invite codes (data-model.md).")
    parser.add_argument("--table", default=os.environ.get("ITR_TABLE", "itr-main"))
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("add")
    new.add_argument("--note", required=True, help="who gets it (ours only)")
    new.add_argument("--games", type=int, default=6)
    new.add_argument("--count", type=int, default=1)
    sub.add_parser("list")
    sub.add_parser("off").add_argument("code")
    args = parser.parse_args(argv)

    tbl = table(args.table)
    if args.command == "add":
        for code in add(tbl, args.note, args.games, args.count):
            print(codes.shown(code))
    elif args.command == "list":
        print("\n".join(listing(tbl)) or "No codes yet.")
    else:
        print(f"{switch_off(tbl, args.code)} switched off")


if __name__ == "__main__":
    main()

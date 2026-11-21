"""Games in DynamoDB (data-model.md): the start transaction, the server's address, giving a
failed start back, and reading a game. Shared with the cleanup Lambda later (step 4).
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from itr_api.cookie import LIFETIME_S, b64url_decode
from itr_api.labs import LabCard
from itr_api.launcher import Server

KYIV = ZoneInfo("Europe/Kyiv")  # the per-day limit uses Kyiv dates (data-model.md)
DAY_S = 86400
GAME_TTL_S = 30 * DAY_S
KEYS_CACHE_S = 300  # contract 5: both sides cache the cookie keys for 5 minutes

DEFAULT_LIMITS = {
    "max_running": 5,
    "games_per_code": 6,
    "games_per_day": 3,
    "max_games_month": 400,
    "boot_timeout_s": 180,
    "idle_timeout_s": 300,
}

# The API's own events: each happens at most once per game, so each has a fixed seq
# (like the gateway's). Sorting is by `at` anyway (events.md).
API_SEQ = {
    "game_created": 1,
    "server_started": 2,
    "server_terminated": 3,
    "boot_failed": 4,
    "ended": 5,
}


def iso_ms(seconds: float) -> str:
    stamp = datetime.fromtimestamp(seconds, UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def kyiv_day(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, KYIV).strftime("%Y-%m-%d")


def seconds_to_kyiv_midnight(seconds: float) -> int:
    now = datetime.fromtimestamp(seconds, KYIV)
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(int(midnight.timestamp() - seconds), 1)


def month(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m")


def plain(item: dict | None) -> dict | None:
    """DynamoDB Decimals -> int/float, for JSON and comparisons."""
    if item is None:
        return None
    return json.loads(
        json.dumps(item, default=lambda d: int(d) if d == int(d) else float(d))
    )


class Refused(Exception):
    """The start transaction refused: which = code | day | running | month."""

    def __init__(self, which: str):
        super().__init__(which)
        self.which = which


REFUSED_BY_ITEM = (
    "code",
    "day",
    "running",
    "month",
)  # order of the first 4 transaction items


@dataclass(frozen=True)
class NewGame:
    game_id: str
    code: str
    lab: LabCard
    nickname: str
    now: float

    @property
    def created_at(self) -> str:
        return iso_ms(self.now)

    @property
    def code_game_sk(self) -> str:
        return f"GAME#{self.created_at}#{self.game_id}"


class GameStore:
    def __init__(self, table, clock: Callable[[], float] = time.time):
        self.table = table
        self.client = (
            table.meta.client
        )  # the resource's client: plain Python values work
        self.clock = clock
        self._keys: tuple[float, dict] | None = None

    # ----- reads ------------------------------------------------------------------------
    def _get(self, pk: str, sk: str, consistent: bool = False) -> dict | None:
        reply = self.table.get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=consistent)
        return plain(reply.get("Item"))

    def limits(self) -> dict:
        return DEFAULT_LIMITS | (self._get("CONFIG", "LIMITS") or {})

    def code(self, code: str) -> dict | None:
        return self._get(f"CODE#{code}", "META", consistent=True)

    def games_today(self, code: str, now: float) -> int:
        item = self._get(f"CODE#{code}", f"DAY#{kyiv_day(now)}", consistent=True)
        return int((item or {}).get("count", 0))

    def meta(self, game_id: str) -> dict | None:
        return self._get(f"GAME#{game_id}", "META", consistent=True)

    def cookie_key(self) -> tuple[str, bytes] | None:
        """(kid, secret) of the current key, or None if no keys exist yet."""
        now = self.clock()
        if self._keys is None or now - self._keys[0] > KEYS_CACHE_S:
            self._keys = (now, self._get("CONFIG", "COOKIE_KEYS") or {})
        item = self._keys[1]
        kid = item.get("current")
        entry = (item.get("keys") or {}).get(kid) if kid else None
        if not entry:
            return None
        secret = b64url_decode(str(entry["secret"]))
        if len(secret) < 32:
            raise ValueError(f"cookie key {kid} is shorter than 32 bytes")
        return str(kid), secret

    # ----- start ------------------------------------------------------------------------
    def begin(self, game: NewGame, limits: dict) -> None:
        """The start transaction (data-model.md). Raises Refused; writes nothing then."""
        from botocore.exceptions import ClientError

        name, now = self.table.name, int(game.now)
        lab = game.lab
        # A limit of 0 is a kill switch (no new games); a missing counter would pass it.
        for which, limit in (
            ("day", "games_per_day"),
            ("running", "max_running"),
            ("month", "max_games_month"),
        ):
            if int(limits[limit]) <= 0:
                raise Refused(which)
        items = [
            {
                "Update": {
                    "TableName": name,
                    "Key": {"pk": f"CODE#{game.code}", "sk": "META"},
                    "UpdateExpression": "ADD games_used :one",
                    "ConditionExpression": "attribute_exists(pk) AND active = :true"
                    " AND (attribute_not_exists(games_used) OR games_used < games_total)",
                    "ExpressionAttributeValues": {":one": 1, ":true": True},
                }
            },
            {
                "Update": {
                    "TableName": name,
                    "Key": {
                        "pk": f"CODE#{game.code}",
                        "sk": f"DAY#{kyiv_day(game.now)}",
                    },
                    "UpdateExpression": "ADD #count :one SET expires_at = if_not_exists(expires_at, :ttl)",
                    "ConditionExpression": "attribute_not_exists(#count) OR #count < :max",
                    "ExpressionAttributeNames": {"#count": "count"},
                    "ExpressionAttributeValues": {
                        ":one": 1,
                        ":max": int(limits["games_per_day"]),
                        ":ttl": now + 2 * DAY_S,
                    },
                }
            },
            {
                "Update": {
                    "TableName": name,
                    "Key": {"pk": "COUNTER", "sk": "RUNNING"},
                    "UpdateExpression": "ADD #count :one",
                    "ConditionExpression": "attribute_not_exists(#count) OR #count < :max",
                    "ExpressionAttributeNames": {"#count": "count"},
                    "ExpressionAttributeValues": {
                        ":one": 1,
                        ":max": int(limits["max_running"]),
                    },
                }
            },
            {
                "Update": {
                    "TableName": name,
                    "Key": {"pk": "COUNTER", "sk": f"MONTH#{month(game.now)}"},
                    "UpdateExpression": "ADD games :one",
                    "ConditionExpression": "attribute_not_exists(games) OR games < :max",
                    "ExpressionAttributeValues": {
                        ":one": 1,
                        ":max": int(limits["max_games_month"]),
                    },
                }
            },
            {
                "Put": {
                    "TableName": name,
                    "Item": {
                        "pk": f"GAME#{game.game_id}",
                        "sk": "META",
                        "code": game.code,
                        "lab_id": lab.id,
                        "lab_version": lab.version,
                        "world": lab.world,
                        "terminal": lab.terminal,
                        "scoring": json.loads(
                            json.dumps({"level": lab.level} | lab.scoring),
                            parse_float=Decimal,
                        ),
                        "nickname": game.nickname,
                        "state": "booting",
                        "created_at": game.created_at,
                        "expires_at": now + GAME_TTL_S,
                    },
                    "ConditionExpression": "attribute_not_exists(pk)",
                }
            },
            {
                "Put": {
                    "TableName": name,
                    "Item": {
                        "pk": "ACTIVE",
                        "sk": f"GAME#{game.game_id}",
                        "state": "booting",
                        "expires_at": now + DAY_S,
                    },
                }
            },
            {
                "Put": {
                    "TableName": name,
                    "Item": {
                        "pk": f"CODE#{game.code}",
                        "sk": game.code_game_sk,
                        "game_id": game.game_id,
                        "lab_id": lab.id,
                        "state": "booting",
                        "expires_at": now + GAME_TTL_S,
                    },
                }
            },
        ]
        try:
            self.client.transact_write_items(TransactItems=items)
        except ClientError as error:
            reasons = [
                r.get("Code") for r in error.response.get("CancellationReasons", [])
            ]
            for index, which in enumerate(REFUSED_BY_ITEM):
                if reasons[index : index + 1] == ["ConditionalCheckFailed"]:
                    raise Refused(which) from error
            raise

    def server_started(self, game_id: str, server: Server) -> int:
        """Write the server onto the game (and ACTIVE): from now on the gateway can boot it.

        Returns `expires_at_server` (Unix seconds)."""
        expires = int(server.launched_at.timestamp()) + LIFETIME_S
        self.table.update_item(
            Key={"pk": f"GAME#{game_id}", "sk": "META"},
            UpdateExpression="SET instance_id = :id, private_ip = :ip, expires_at_server = :exp,"
            " instance_type = :type, #zone = :zone",
            ConditionExpression="attribute_exists(pk)",
            ExpressionAttributeNames={"#zone": "zone"},
            ExpressionAttributeValues={
                ":id": server.server_id,
                ":ip": server.address,
                ":exp": expires,
                ":type": server.instance_type,
                ":zone": server.zone,
            },
        )
        self.table.update_item(
            Key={"pk": "ACTIVE", "sk": f"GAME#{game_id}"},
            UpdateExpression="SET instance_id = :id, private_ip = :ip",
            ConditionExpression="attribute_exists(pk)",  # never recreate one the gateway removed
            ExpressionAttributeValues={":id": server.server_id, ":ip": server.address},
        )
        return expires

    # ----- a start that failed: end the game `failed` and give everything back -----------
    def fail(self, game: NewGame, reason: str) -> bool:
        """META ended/failed exactly once; the winner gives back code, day, slot and month."""
        from botocore.exceptions import ClientError

        name, at = self.table.name, iso_ms(self.clock())
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": name,
                            "Key": {"pk": f"GAME#{game.game_id}", "sk": "META"},
                            "UpdateExpression": "SET #state = :ended, end_reason = :failed,"
                            " ended_at = :at, failure = :reason",
                            "ConditionExpression": "attribute_exists(pk) AND #state <> :ended",
                            "ExpressionAttributeNames": {"#state": "state"},
                            "ExpressionAttributeValues": {
                                ":ended": "ended",
                                ":failed": "failed",
                                ":at": at,
                                ":reason": reason[:300],
                            },
                        }
                    },
                    {
                        "Delete": {
                            "TableName": name,
                            "Key": {"pk": "ACTIVE", "sk": f"GAME#{game.game_id}"},
                        }
                    },
                    {
                        "Update": {
                            "TableName": name,
                            "Key": {"pk": f"CODE#{game.code}", "sk": game.code_game_sk},
                            "UpdateExpression": "SET #state = :ended, end_reason = :failed",
                            "ExpressionAttributeNames": {"#state": "state"},
                            "ExpressionAttributeValues": {
                                ":ended": "ended",
                                ":failed": "failed",
                            },
                        }
                    },
                ]
            )
        except ClientError as error:
            reasons = [
                r.get("Code") for r in error.response.get("CancellationReasons", [])
            ]
            if reasons[:1] == ["ConditionalCheckFailed"]:
                return False  # someone else ended it first
            raise
        # Give back one by one: a counter that cleanup already reset must not go below 0.
        for pk, sk, field in (
            (f"CODE#{game.code}", "META", "games_used"),
            (f"CODE#{game.code}", f"DAY#{kyiv_day(game.now)}", "count"),
            ("COUNTER", "RUNNING", "count"),
            ("COUNTER", f"MONTH#{month(game.now)}", "games"),
        ):
            try:
                self.table.update_item(
                    Key={"pk": pk, "sk": sk},
                    UpdateExpression="ADD #f :minus",
                    ConditionExpression="#f > :zero",
                    ExpressionAttributeNames={"#f": field},
                    ExpressionAttributeValues={":minus": -1, ":zero": 0},
                )
            except ClientError as error:
                if (
                    error.response.get("Error", {}).get("Code")
                    != "ConditionalCheckFailedException"
                ):
                    raise
        return True

    # ----- events -----------------------------------------------------------------------
    def event(self, game_id: str, type_: str, data: dict) -> None:
        now = self.clock()
        seq = API_SEQ[type_]
        self.table.put_item(
            Item={
                "pk": f"GAME#{game_id}",
                "sk": f"EV#api#{seq:06d}",
                "seq": seq,
                "at": iso_ms(now),
                "t": None,
                "by": "api",
                "type": type_,
                "data": json.loads(json.dumps(data), parse_float=Decimal),
                "expires_at": int(now) + GAME_TTL_S,
            }
        )

    # ----- finish (contract 3): server gone, score saved -------------------------------
    def events(self, game_id: str) -> list[dict]:
        args: dict = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :ev)",
            "ExpressionAttributeValues": {":pk": f"GAME#{game_id}", ":ev": "EV#"},
            "ConsistentRead": True,
        }
        found = []
        while True:
            reply = self.table.query(**args)
            for item in reply.get("Items", []):
                event = plain(item)
                found.append(
                    {k: event.get(k) for k in ("seq", "at", "t", "by", "type", "data")}
                )
            if "LastEvaluatedKey" not in reply:
                return found
            args["ExclusiveStartKey"] = reply["LastEvaluatedKey"]

    def result(self, game_id: str) -> dict | None:
        item = self._get(f"GAME#{game_id}", "RESULT", consistent=True)
        return json.loads(item["result_json"]) if item else None

    def mark_terminated(self, game_id: str) -> bool:
        """Note that the server is gone (the gateway then answers 410). True only the first time."""
        from botocore.exceptions import ClientError

        try:
            self.table.update_item(
                Key={"pk": f"GAME#{game_id}", "sk": "META"},
                UpdateExpression="SET server_terminated_at = :at",
                ConditionExpression="attribute_exists(pk) AND attribute_not_exists(server_terminated_at)",
                ExpressionAttributeValues={":at": iso_ms(self.clock())},
            )
            return True
        except ClientError as error:
            if (
                error.response.get("Error", {}).get("Code")
                == "ConditionalCheckFailedException"
            ):
                return False
            raise

    def save_result(self, game_id: str, meta: dict, result: dict) -> dict:
        """Write RESULT once; a second finish gets the first result back (idempotent)."""
        from botocore.exceptions import ClientError

        now = self.clock()
        try:
            self.table.put_item(
                Item={
                    "pk": f"GAME#{game_id}",
                    "sk": "RESULT",
                    "result_json": json.dumps(result),  # exact round trip (no Decimals)
                    "expires_at": int(now) + GAME_TTL_S,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            if (
                error.response.get("Error", {}).get("Code")
                != "ConditionalCheckFailedException"
            ):
                raise
            return self.result(game_id) or result
        summary = {
            ":at": iso_ms(now),
            ":score": result.get("score"),
            ":grade": result.get("grade"),
        }
        self.table.update_item(
            Key={"pk": f"GAME#{game_id}", "sk": "META"},
            UpdateExpression="SET scored_at = :at, score = :score, grade = :grade",
            ExpressionAttributeValues=summary,
        )
        if meta.get("code") and meta.get(
            "created_at"
        ):  # the code's list of games (labs page "done")
            self.table.update_item(
                Key={
                    "pk": f"CODE#{meta['code']}",
                    "sk": f"GAME#{meta['created_at']}#{game_id}",
                },
                UpdateExpression="SET score = :score, grade = :grade, #state = :ended",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":score": result.get("score"),
                    ":grade": result.get("grade"),
                    ":ended": "ended",
                },
            )
        return result

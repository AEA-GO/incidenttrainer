"""Where gateway-auth reads games and cookie keys, and writes heartbeats and the broker's work.

Two stores with the same methods:
  TableStore   DynamoDB table `itr-main` (data-model.md), on the gateway server
  FileStore    a JSON file of games + keys from an env variable, on a laptop

Reads are cached (contract 5): a game's META 60 s (a booting game 2 s, so the boot
screen sees `ready` quickly), cookie keys 5 min. An unknown key id refreshes the keys
at most every 30 s, so a new key works soon after rotation.
"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from itr_gateway.cookie import b64url_decode

log = logging.getLogger(__name__)

GAME_TTL_S = 60
BOOTING_TTL_S = 2
KEYS_TTL_S = 300
KEYS_RETRY_S = 30
HEARTBEAT_EVERY_S = 30
EVENT_TTL_S = 30 * 86400  # game data lives 30 days (data-model.md)

PLAYING = ("ready", "paged", "investigating", "watching")
STATES = ("booting", *PLAYING, "ended")


@dataclass(frozen=True)
class Game:
    """The parts of `GAME#<id> / META` the gateway needs."""

    game_id: str
    state: str
    private_ip: str | None = None
    instance_id: str | None = None
    terminal: bool = False
    created_at: float | None = None  # Unix seconds
    expires_at_server: float | None = None  # Unix seconds
    server_terminated: bool = False

    def server_gone(self, now: float) -> bool:
        """No server to forward to any more (never started, deleted, or past its life)."""
        if self.server_terminated or not self.private_ip:
            return True
        return self.expires_at_server is not None and self.expires_at_server <= now


def _seconds(value: Any) -> float | None:
    """Unix seconds from a number or an ISO 8601 string (`...Z`)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return datetime.fromisoformat(
        str(value)
    ).timestamp()  # Python 3.11+ reads a trailing Z


def game_from_item(game_id: str, item: dict) -> Game:
    state = str(item.get("state", ""))
    if state not in STATES:
        raise ValueError(f"game {game_id} has unknown state {state!r}")
    return Game(
        game_id=game_id,
        state=state,
        private_ip=item.get("private_ip") or None,
        instance_id=item.get("instance_id") or None,
        terminal=bool(item.get("terminal", False)),
        created_at=_seconds(item.get("created_at")),
        expires_at_server=_seconds(item.get("expires_at_server")),
        server_terminated=bool(item.get("server_terminated_at")),
    )


def keys_from_item(item: dict) -> dict[str, bytes]:
    """`CONFIG / COOKIE_KEYS` → {kid: secret bytes}. Every listed key is accepted."""
    keys = {}
    for kid, entry in (item.get("keys") or {}).items():
        secret = b64url_decode(str(entry["secret"]))
        if len(secret) < 32:
            raise ValueError(f"cookie key {kid} is shorter than 32 bytes")
        keys[str(kid)] = secret
    return keys


class Store(Protocol):
    def game(self, game_id: str) -> Game | None: ...
    def key(self, kid: str) -> bytes | None: ...
    def heartbeat(self, game_id: str, now: float) -> None: ...


@dataclass
class _Cached:
    value: Any
    until: float


@dataclass
class CachingStore:
    """The caching and throttling both stores share. Subclasses load and write."""

    clock: Callable[[], float] = time.time
    _games: dict[str, _Cached] = field(default_factory=dict)
    _keys: dict[str, bytes] = field(default_factory=dict)
    _keys_until: float = 0.0
    _keys_tried: float = -1e18
    _beats: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- for subclasses
    def load_game(self, game_id: str) -> dict | None:
        raise NotImplementedError

    def load_keys(self) -> dict:
        raise NotImplementedError

    def write_heartbeat(self, game_id: str, now: float) -> None:
        raise NotImplementedError

    # --- used by the app
    def game(self, game_id: str) -> Game | None:
        now = self.clock()
        with self._lock:
            hit = self._games.get(game_id)
            if hit and hit.until > now:
                return hit.value
        item = self.load_game(game_id)
        found = game_from_item(game_id, item) if item else None
        ttl = BOOTING_TTL_S if found is None or found.state == "booting" else GAME_TTL_S
        with self._lock:
            self._games[game_id] = _Cached(found, now + ttl)
        return found

    def forget(self, game_id: str) -> None:
        """Drop a cached game (the broker calls this after it changes the game)."""
        with self._lock:
            self._games.pop(game_id, None)

    def key(self, kid: str) -> bytes | None:
        now = self.clock()
        with self._lock:
            stale = now >= self._keys_until
            unknown = kid not in self._keys and now - self._keys_tried >= KEYS_RETRY_S
        if stale or unknown:
            self._refresh_keys(now)
        with self._lock:
            return self._keys.get(kid)

    def _refresh_keys(self, now: float) -> None:
        with self._lock:
            self._keys_tried = now
        try:
            keys = keys_from_item(self.load_keys())
        except Exception:
            log.exception("could not load the cookie keys; keeping the old ones")
            return
        with self._lock:
            self._keys = keys
            self._keys_until = now + KEYS_TTL_S

    def heartbeat(self, game_id: str, now: float) -> None:
        """Note the player is still there: at most one write per game every 30 s."""
        with self._lock:
            last = self._beats.get(game_id)
            if last is not None and now - last < HEARTBEAT_EVERY_S:
                return
            self._beats[game_id] = now
        try:
            self.write_heartbeat(game_id, now)
        except Exception:
            log.exception(
                "heartbeat write failed", extra={"fields": {"game_id": game_id}}
            )


@dataclass
class TableStore(CachingStore):
    """DynamoDB (the gateway server). `table` is a boto3 Table resource."""

    table: Any = None

    def load_game(self, game_id: str) -> dict | None:
        reply = self.table.get_item(Key={"pk": f"GAME#{game_id}", "sk": "META"})
        return reply.get("Item")

    def load_keys(self) -> dict:
        reply = self.table.get_item(
            Key={"pk": "CONFIG", "sk": "COOKIE_KEYS"}, ConsistentRead=True
        )
        if "Item" not in reply:
            raise LookupError("CONFIG / COOKIE_KEYS is missing")
        return reply["Item"]

    def write_heartbeat(self, game_id: str, now: float) -> None:
        from botocore.exceptions import ClientError

        stamp = int(now)
        try:
            self.table.update_item(
                Key={"pk": f"GAME#{game_id}", "sk": "META"},
                UpdateExpression="SET last_seen_at = :now",
                # Cheap and safe with several gateway workers: only move it forward,
                # and never create an item for a game that doesn't exist.
                ConditionExpression=(
                    "attribute_exists(pk) AND (attribute_not_exists(last_seen_at) OR last_seen_at < :cutoff)"
                ),
                ExpressionAttributeValues={
                    ":now": stamp,
                    ":cutoff": stamp - HEARTBEAT_EVERY_S + 1,
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    # ----- the broker's reads and writes (contract 5, data-model.md) -----------------
    def _meta_key(self, game_id: str) -> dict:
        return {"pk": f"GAME#{game_id}", "sk": "META"}

    def _update(
        self,
        key: dict,
        expression: str,
        values: dict,
        condition: str | None = None,
        names=None,
    ) -> bool:
        """An UpdateItem; False when its condition fails."""
        from botocore.exceptions import ClientError

        args: dict = {
            "Key": key,
            "UpdateExpression": expression,
            "ExpressionAttributeValues": values,
        }
        if condition:
            args["ConditionExpression"] = condition
        if names:
            args["ExpressionAttributeNames"] = names
        try:
            self.table.update_item(**args)
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def active_ids(self) -> list[str]:
        reply = self.table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :game)",
            ExpressionAttributeValues={":pk": "ACTIVE", ":game": "GAME#"},
            ConsistentRead=True,
        )
        return [item["sk"].removeprefix("GAME#") for item in reply.get("Items", [])]

    def meta(self, game_id: str) -> dict | None:
        reply = self.table.get_item(Key=self._meta_key(game_id), ConsistentRead=True)
        return reply.get("Item")

    def save_token(self, game_id: str, token: str) -> str:
        if self._update(
            self._meta_key(game_id),
            "SET control_token = :token",
            {":token": token},
            "attribute_exists(pk) AND attribute_not_exists(control_token)",
        ):
            return token
        return str(
            (self.meta(game_id) or {})["control_token"]
        )  # another worker won: use theirs

    def mark_ready(self, game_id: str, at: str) -> None:
        names = {"#state": "state"}
        self._update(
            self._meta_key(game_id),
            "SET #state = :ready, ready_at = :at",
            {":ready": "ready", ":at": at, ":booting": "booting"},
            "#state = :booting",
            names,
        )
        self._update(
            {"pk": "ACTIVE", "sk": f"GAME#{game_id}"},
            "SET #state = :ready",
            {":ready": "ready"},
            "attribute_exists(pk)",
            names,
        )

    def save_events(self, game_id: str, events: list[dict]) -> None:
        expires = int(self.clock()) + EVENT_TTL_S
        with self.table.batch_writer(overwrite_by_pkeys=["pk", "sk"]) as batch:
            for event in events:
                item = json.loads(
                    json.dumps(event), parse_float=Decimal
                )  # DynamoDB has no floats
                item |= {
                    "pk": f"GAME#{game_id}",
                    "sk": f"EV#{event['by']}#{int(event['seq']):06d}",
                    "expires_at": expires,
                }
                batch.put_item(Item=item)

    def save_progress(
        self,
        game_id: str,
        synced_seq: int,
        state: str | None,
        severity: str | None,
        reopened: bool,
    ) -> None:
        sets = ["events_synced_seq = :seq", "reopened = :reopened"]
        values: dict = {":seq": synced_seq, ":reopened": reopened, ":ended": "ended"}
        if severity:
            sets.append("severity = :severity")
            values[":severity"] = severity
        if state:
            sets.append("#state = :state")
            values[":state"] = state
        self._update(
            self._meta_key(game_id),
            "SET " + ", ".join(sets),
            values,
            "#state <> :ended",
            {"#state": "state"},
        )
        if state:
            self._update(
                {"pk": "ACTIVE", "sk": f"GAME#{game_id}"},
                "SET #state = :state",
                {":state": state},
                "attribute_exists(pk)",
                {"#state": "state"},
            )

    def mark_heartbeat_lost(self, game_id: str, at: str) -> None:
        self._update(
            self._meta_key(game_id), "SET heartbeat_lost_at = :at", {":at": at}
        )

    def finish(self, game_id: str, end_reason: str, at: str) -> bool:
        """META `ended` exactly once; the winner also frees the server slot (data-model.md)."""
        from botocore.exceptions import ClientError

        client = self.table.meta.client
        name = self.table.name
        meta_update = {
            "Update": {
                "TableName": name,
                "Key": self._meta_key(game_id),
                "UpdateExpression": "SET #state = :ended, end_reason = :reason, ended_at = :at",
                "ConditionExpression": "attribute_exists(pk) AND #state <> :ended",
                "ExpressionAttributeNames": {"#state": "state"},
                "ExpressionAttributeValues": {
                    ":ended": "ended",
                    ":reason": end_reason,
                    ":at": at,
                },
            }
        }
        leave = {
            "Delete": {
                "TableName": name,
                "Key": {"pk": "ACTIVE", "sk": f"GAME#{game_id}"},
            }
        }
        running = {
            "Update": {
                "TableName": name,
                "Key": {"pk": "COUNTER", "sk": "RUNNING"},
                "UpdateExpression": "SET #count = #count - :one",
                "ConditionExpression": "#count > :zero",
                "ExpressionAttributeNames": {"#count": "count"},
                "ExpressionAttributeValues": {":one": 1, ":zero": 0},
            }
        }
        won = False
        for items in ([meta_update, leave, running], [meta_update, leave]):
            try:
                client.transact_write_items(TransactItems=items)
                won = True
                break
            except ClientError as error:
                reasons = [
                    r.get("Code") for r in error.response.get("CancellationReasons", [])
                ]
                if reasons and reasons[0] == "ConditionalCheckFailed":
                    break  # someone else ended it first
                if len(items) == 3 and reasons[2:3] == ["ConditionalCheckFailed"]:
                    continue  # the counter is already 0 (cleanup resets it): end without it
                raise
        if not won:
            self.leave_active(game_id)
            return False
        meta = self.meta(game_id) or {}
        if meta.get("code") and meta.get(
            "created_at"
        ):  # the code's list of games (best effort)
            self._update(
                {
                    "pk": f"CODE#{meta['code']}",
                    "sk": f"GAME#{meta['created_at']}#{game_id}",
                },
                "SET #state = :ended, end_reason = :reason",
                {":ended": "ended", ":reason": end_reason},
                "attribute_exists(pk)",
                {"#state": "state"},
            )
        return True

    def leave_active(self, game_id: str) -> None:
        self.table.delete_item(Key={"pk": "ACTIVE", "sk": f"GAME#{game_id}"})

    def last_health(self, game_id: str) -> int | None:
        args: dict = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :agent)",
            "ExpressionAttributeValues": {
                ":pk": f"GAME#{game_id}",
                ":agent": "EV#agent#",
            },
            "ScanIndexForward": False,
        }
        while True:
            reply = self.table.query(**args)
            for item in reply.get("Items", []):
                if item.get("type") == "health":
                    return int(item["data"]["value"])
            if "LastEvaluatedKey" not in reply:
                return None
            args["ExclusiveStartKey"] = reply["LastEvaluatedKey"]


@dataclass
class FileStore(CachingStore):
    """A laptop: games from a JSON file (re-read when it changes), keys from JSON text.

    File: {"games": {"<game_id>": {META attributes}}}. Heartbeats stay in memory.
    """

    path: Path | None = None
    keys_json: str = ""
    last_seen: dict[str, int] = field(default_factory=dict)
    _mtime: float = -1.0
    _data: dict = field(default_factory=dict)

    def _file(self) -> dict:
        if self.path is None or not self.path.exists():
            return {}
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._data = json.loads(self.path.read_text())
            self._mtime = mtime
            self._games.clear()
        return self._data

    def load_game(self, game_id: str) -> dict | None:
        return (self._file().get("games") or {}).get(game_id)

    def game(self, game_id: str) -> Game | None:
        self._file()  # a changed file drops the cache at once
        return super().game(game_id)

    def load_keys(self) -> dict:
        return json.loads(self.keys_json)

    def write_heartbeat(self, game_id: str, now: float) -> None:
        self.last_seen[game_id] = int(now)
        self._edit(game_id, {"last_seen_at": int(now)})

    # ----- the broker on a laptop: the games file is the table -------------------------
    # META changes are written back to the file; events go to <file>.events/<game>.jsonl.
    def _edit(
        self, game_id: str, changes: dict, only_if: Callable[[dict], bool] | None = None
    ) -> bool:
        with self._lock:
            data = self._file()
            game = (data.get("games") or {}).get(game_id)
            if game is None or (only_if and not only_if(game)):
                return False
            game.update(changes)
            if self.path is not None:
                self.path.write_text(json.dumps(data, indent=2) + "\n")
                self._mtime = self.path.stat().st_mtime
            self._games.pop(game_id, None)
        return True

    def active_ids(self) -> list[str]:
        games = self._file().get("games") or {}
        return [
            game_id for game_id, game in games.items() if game.get("state") != "ended"
        ]

    def meta(self, game_id: str) -> dict | None:
        game = (self._file().get("games") or {}).get(game_id)
        return dict(game) if game is not None else None

    def save_token(self, game_id: str, token: str) -> str:
        self._edit(
            game_id,
            {"control_token": token},
            lambda game: not game.get("control_token"),
        )
        return str(self.meta(game_id)["control_token"])  # type: ignore[index]

    def mark_ready(self, game_id: str, at: str) -> None:
        self._edit(
            game_id,
            {"state": "ready", "ready_at": at},
            lambda game: game.get("state") == "booting",
        )

    def events_file(self, game_id: str) -> Path:
        folder = Path(f"{self.path}.events")
        folder.mkdir(exist_ok=True)
        return folder / f"{game_id}.jsonl"

    def save_events(self, game_id: str, events: list[dict]) -> None:
        file = self.events_file(game_id)
        seen = set()
        if file.exists():
            seen = {
                (e["by"], e["seq"])
                for e in map(json.loads, file.read_text().splitlines())
            }
        with file.open("a") as out:
            for event in events:
                if (event["by"], event["seq"]) not in seen:  # same key = written once
                    out.write(json.dumps(event) + "\n")

    def save_progress(
        self,
        game_id: str,
        synced_seq: int,
        state: str | None,
        severity: str | None,
        reopened: bool,
    ) -> None:
        changes: dict = {"events_synced_seq": synced_seq, "reopened": reopened}
        if severity:
            changes["severity"] = severity
        if state:
            changes["state"] = state
        self._edit(game_id, changes, lambda game: game.get("state") != "ended")

    def mark_heartbeat_lost(self, game_id: str, at: str) -> None:
        self._edit(game_id, {"heartbeat_lost_at": at})

    def finish(self, game_id: str, end_reason: str, at: str) -> bool:
        return self._edit(
            game_id,
            {"state": "ended", "end_reason": end_reason, "ended_at": at},
            lambda game: game.get("state") != "ended",
        )

    def leave_active(self, game_id: str) -> None:
        pass  # on a laptop, "active" = every game in the file that hasn't ended

    def last_health(self, game_id: str) -> int | None:
        file = self.events_file(game_id)
        if not file.exists():
            return None
        values = [
            e["data"]["value"]
            for e in map(json.loads, file.read_text().splitlines())
            if e["type"] == "health"
        ]
        return int(values[-1]) if values else None


def store_from_env(env: dict[str, str] | None = None) -> CachingStore:
    """ITR_TABLE → DynamoDB; else ITR_DEV_GAMES (laptop file). ITR_COOKIE_KEYS overrides keys."""
    env = dict(os.environ if env is None else env)
    keys_json = env.get("ITR_COOKIE_KEYS", "")
    if env.get("ITR_TABLE"):
        import boto3

        table = boto3.resource("dynamodb").Table(env["ITR_TABLE"])
        store: CachingStore = TableStore(table=table)
        if keys_json:
            store.load_keys = lambda: json.loads(keys_json)  # type: ignore[method-assign]
        return store
    if not keys_json:
        raise SystemExit(
            "Set ITR_TABLE (DynamoDB) or ITR_COOKIE_KEYS + ITR_DEV_GAMES (laptop)"
        )
    path = Path(env["ITR_DEV_GAMES"]) if env.get("ITR_DEV_GAMES") else None
    return FileStore(path=path, keys_json=keys_json)

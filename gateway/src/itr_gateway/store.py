"""Where gateway-auth reads games and cookie keys, and writes heartbeats.

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

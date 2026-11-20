"""gateway-auth as session broker (contract 5, "gateway-auth as broker").

Every 5 s (and at once when the boot screen polls a booting game) the broker moves each
active game one step on:

  booting      agent ready?  -> save a control token (only one worker wins) -> claim
               -> read the runbook -> push it -> event `agent_ready`, META state `ready`
  ready..      every ~10 s: copy new agent events, copy state/severity/reopened to META
  watching     idle 300 s: event `heartbeat_lost`, ask the agent to end the game
  ended        (agent says so) copy the last events, THEN META `ended`, leave ACTIVE,
               free the server slot
  unreachable  agent silent for 60 s while playing: write `ended` (abandoned) itself

The agent's control port (8081) is reached only from here, never from the browser.
"""

import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger("itr_gateway.broker")

CONTROL_PORT = 8081
TICK_S = 5
REPORT_EVERY_S = 10
IDLE_S = 300
UNREACHABLE_S = 60
PLAYING = ("ready", "paged", "investigating", "watching")

# The gateway's own events: each happens at most once per game, so a fixed seq makes
# writing them twice harmless (same key).
SEQ_AGENT_READY = 1
SEQ_HEARTBEAT_LOST = 2
SEQ_ENDED = 3


def iso(seconds: float) -> str:
    """RFC 3339 UTC with milliseconds, like the agent's events."""
    stamp = datetime.fromtimestamp(seconds, UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


class Unreachable(Exception):
    """The agent didn't answer (connection refused, timeout)."""


class AgentRefused(Exception):
    def __init__(self, status: int, body: dict) -> None:
        super().__init__(f"{status} {body}")
        self.status = status
        self.code = (body.get("error") or {}).get("code", "")


class AgentClient:
    """HTTP to an agent's control port. Standard library only; short timeouts."""

    def __init__(self, timeout_s: float = 3.0) -> None:
        self.timeout_s = timeout_s

    def _call(
        self,
        ip: str,
        method: str,
        path: str,
        body: dict | None = None,
        token: str | None = None,
    ) -> dict:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"http://{ip}:{CONTROL_PORT}{path}", data=data, method=method
        )
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as reply:
                raw = reply.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                answer = json.loads(raw) if raw else {}
            except ValueError:
                answer = {}
            raise AgentRefused(error.code, answer) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            raise Unreachable(str(error)) from None

    def ready(self, ip: str) -> dict:
        return self._call(ip, "GET", "/control/v1/ready")

    def claim(self, ip: str, game_id: str, token: str, public_base_url: str) -> None:
        body = {"game_id": game_id, "token": token, "public_base_url": public_base_url}
        self._call(ip, "POST", "/control/v1/claim", body)

    def push(self, ip: str, token: str, body: dict) -> dict:
        return self._call(ip, "PUT", "/control/v1/runbook", body, token)

    def report(self, ip: str, token: str, after_seq: int) -> dict:
        return self._call(
            ip,
            "GET",
            f"/control/v1/report?after_seq={after_seq}&limit=500",
            token=token,
        )

    def end(self, ip: str, token: str) -> dict:
        return self._call(ip, "POST", "/control/v1/end", {"reason": "abandoned"}, token)


class RunbookSource(Protocol):
    def runbook(self, lab_id: str, lab_version: int) -> dict: ...


@dataclass
class BucketRunbooks:
    """The lab content bucket: labs/<lab>/v<version>/runbook.json (written by labs CI)."""

    bucket: str
    s3: Any = None
    _cache: dict = field(default_factory=dict)

    def runbook(self, lab_id: str, lab_version: int) -> dict:
        key = f"labs/{lab_id}/v{lab_version}/runbook.json"
        if key not in self._cache:  # a version never changes once published
            if self.s3 is None:
                import boto3

                self.s3 = boto3.client("s3")
            reply = self.s3.get_object(Bucket=self.bucket, Key=key)
            self._cache[key] = json.loads(reply["Body"].read())
        return self._cache[key]


@dataclass
class FolderRunbooks:
    """A laptop: the labs repo's out/<lab>/push.json (scripts/make_push.py) holds the runbook."""

    folder: Path

    def runbook(self, lab_id: str, lab_version: int) -> dict:
        push = json.loads((self.folder / lab_id / "push.json").read_text())
        return push["runbook"]


def push_body(game_id: str, book: dict) -> dict:
    """What `PUT /control/v1/runbook` takes: the runbook plus who it's for (contract 2)."""
    return {
        "game_id": game_id,
        "lab_id": book["id"],
        "lab_version": book["version"],
        "limits": book.get("limits") or {"time_limit_s": 2400},
        "runbook": book,
    }


class BrokerStore(Protocol):
    """What the broker reads and writes (TableStore and FileStore have these)."""

    def active_ids(self) -> list[str]: ...
    def meta(self, game_id: str) -> dict | None: ...
    def save_token(self, game_id: str, token: str) -> str: ...
    def mark_ready(self, game_id: str, at: str) -> None: ...
    def save_events(self, game_id: str, events: list[dict]) -> None: ...
    def save_progress(
        self,
        game_id: str,
        synced_seq: int,
        state: str,
        severity: str | None,
        reopened: bool,
    ) -> None: ...
    def mark_heartbeat_lost(self, game_id: str, at: str) -> None: ...
    def finish(self, game_id: str, end_reason: str, at: str) -> bool: ...
    def leave_active(self, game_id: str) -> None: ...
    def last_health(self, game_id: str) -> int | None: ...
    def forget(self, game_id: str) -> None: ...


@dataclass
class _Watch:
    """What the broker remembers about one game between steps (lost on restart: fine)."""

    components: dict = field(default_factory=dict)
    next_report: float = 0.0
    silent_since: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class Broker:
    store: BrokerStore
    runbooks: RunbookSource
    public_base_url: str
    agent: AgentClient = field(default_factory=AgentClient)
    clock: Callable[[], float] = time.time
    _watches: dict[str, _Watch] = field(default_factory=dict)
    _guard: threading.Lock = field(default_factory=threading.Lock)
    _pool: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=4)
    )
    _kicked: set = field(default_factory=set)

    # ----- what the app uses ---------------------------------------------------------
    def components(self, game_id: str) -> dict:
        """The agent's boot components, for the boot screen."""
        watch = self._watches.get(game_id)
        return dict(watch.components) if watch else {}

    def kick(self, game_id: str) -> None:
        """The boot screen polled: take the next step now, without making it wait."""
        with self._guard:
            if game_id in self._kicked:
                return
            self._kicked.add(game_id)

        def run() -> None:
            try:
                self.step(game_id)
            finally:
                with self._guard:
                    self._kicked.discard(game_id)

        self._pool.submit(run)

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.tick()
            stop.wait(TICK_S)

    # ----- the loop --------------------------------------------------------------------
    def tick(self) -> None:
        try:
            game_ids = self.store.active_ids()
        except Exception:
            log.exception("could not list the active games")
            return
        for game_id in game_ids:
            self.step(game_id)

    def step(self, game_id: str) -> None:
        watch = self._watch(game_id)
        if not watch.lock.acquire(blocking=False):
            return  # another thread is on it
        try:
            meta = self.store.meta(game_id)
            if meta is None:
                return
            state = meta.get("state")
            if state == "booting":
                self._boot(game_id, meta, watch)
            elif state in PLAYING:
                self._play(game_id, meta, watch)
            elif state == "ended":
                self.store.leave_active(
                    game_id
                )  # whoever ended it may have missed this
                self._drop(game_id)
        except Exception:
            log.exception("broker step failed", extra={"fields": {"game_id": game_id}})
        finally:
            watch.lock.release()

    # ----- booting ---------------------------------------------------------------------
    def _boot(self, game_id: str, meta: dict, watch: _Watch) -> None:
        ip = meta.get("private_ip")
        if not ip:
            return  # the API hasn't got the server's address yet
        try:
            ready = self.agent.ready(ip)
        except (Unreachable, AgentRefused):
            return  # still booting; the API/cleanup give up after boot_timeout_s
        watch.components = ready.get("components") or {}
        if not ready.get("booted"):
            return
        token = meta.get("control_token") or self.store.save_token(
            game_id, secrets.token_urlsafe(32)
        )
        if not ready.get("claimed"):
            try:
                self.agent.claim(ip, game_id, token, self.public_base_url)
            except AgentRefused as refused:
                if refused.code != "already_claimed":  # another worker got there first
                    raise
        if not ready.get("runbook_loaded"):
            book = self.runbooks.runbook(meta["lab_id"], int(meta["lab_version"]))
            try:
                self.agent.push(ip, token, push_body(game_id, book))
            except AgentRefused as refused:
                if refused.code != "already_loaded":
                    raise
        self.store.save_events(
            game_id,
            [
                self._own_event(
                    SEQ_AGENT_READY,
                    "agent_ready",
                    {"runbook_version": int(meta["lab_version"])},
                )
            ],
        )
        self.store.mark_ready(game_id, iso(self.clock()))
        self.store.forget(game_id)
        log.info("game ready", extra={"fields": {"game_id": game_id}})

    # ----- playing ---------------------------------------------------------------------
    def _play(self, game_id: str, meta: dict, watch: _Watch) -> None:
        now = self.clock()
        if now < watch.next_report:
            return
        watch.next_report = now + REPORT_EVERY_S
        ip, token = meta.get("private_ip"), meta.get("control_token")
        if not ip or not token:
            log.error(
                "playing game without server or token",
                extra={"fields": {"game_id": game_id}},
            )
            return
        try:
            report = self._copy_events(
                game_id, ip, token, int(meta.get("events_synced_seq") or 0)
            )
        except Unreachable:
            watch.silent_since = watch.silent_since or now
            if now - watch.silent_since >= UNREACHABLE_S:
                self._end_without_agent(game_id)
            return
        watch.silent_since = None
        if report["state"] == "ended":
            won = self.store.finish(
                game_id, report.get("end_reason") or "abandoned", iso(now)
            )
            self.store.forget(game_id)
            self._drop(game_id)
            log.info(
                "game ended", extra={"fields": {"game_id": game_id, "won_end": won}}
            )
            return
        if self._idle(meta, now) and not meta.get("heartbeat_lost_at"):
            seen = meta.get("last_seen_at") or meta.get("ready_at")
            self.store.save_events(
                game_id,
                [
                    self._own_event(
                        SEQ_HEARTBEAT_LOST,
                        "heartbeat_lost",
                        {"last_seen_at": _as_iso(seen)},
                    )
                ],
            )
            self.store.mark_heartbeat_lost(game_id, iso(now))
            try:
                self.agent.end(ip, token)
            except AgentRefused as refused:
                if refused.code != "already_ended":
                    raise
            watch.next_report = 0.0  # pick up `ended` on the next tick
            log.info(
                "game idle: asked the agent to end it",
                extra={"fields": {"game_id": game_id}},
            )

    def _copy_events(self, game_id: str, ip: str, token: str, synced: int) -> dict:
        """Copy every new agent event, then record progress. Returns the last report."""
        while True:
            report = self.agent.report(ip, token, synced)
            events = report.get("events") or []
            if events:
                self.store.save_events(game_id, events)
                synced = max(int(event["seq"]) for event in events)
            if synced >= int(report.get("last_seq") or 0):
                break
        state = (
            None if report["state"] == "ended" else report["state"]
        )  # `ended` only via finish()
        self.store.save_progress(
            game_id, synced, state, report.get("severity"), bool(report.get("reopened"))
        )
        return report | {"last_copied": synced}

    def _idle(self, meta: dict, now: float) -> bool:
        seen = _as_seconds(meta.get("last_seen_at")) or _as_seconds(
            meta.get("ready_at")
        )
        return seen is not None and now - seen >= IDLE_S

    def _end_without_agent(self, game_id: str) -> None:
        """The agent has been silent for 60 s: end the game ourselves (abandoned)."""
        health = self.store.last_health(game_id)
        self.store.save_events(
            game_id,
            [
                self._own_event(
                    SEQ_ENDED,
                    "ended",
                    {
                        "reason": "abandoned",
                        "final_health": health if health is not None else 0,
                    },
                )
            ],
        )
        self.store.finish(game_id, "abandoned", iso(self.clock()))
        self.store.forget(game_id)
        self._drop(game_id)
        log.warning(
            "agent unreachable: ended the game", extra={"fields": {"game_id": game_id}}
        )

    # ----- helpers -----------------------------------------------------------------------
    def _own_event(self, seq: int, type_: str, data: dict) -> dict:
        return {
            "seq": seq,
            "at": iso(self.clock()),
            "t": None,
            "by": "gateway",
            "type": type_,
            "data": data,
        }

    def _watch(self, game_id: str) -> _Watch:
        with self._guard:
            return self._watches.setdefault(game_id, _Watch())

    def _drop(self, game_id: str) -> None:
        with self._guard:
            self._watches.pop(game_id, None)


def _as_seconds(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or hasattr(value, "as_integer_ratio"):
        return float(value)
    return datetime.fromisoformat(str(value)).timestamp()


def _as_iso(value: Any) -> str | None:
    seconds = _as_seconds(value)
    return iso(seconds) if seconds is not None else None

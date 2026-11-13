"""Everything the agent remembers about its one game. Lives only in memory.

The runbook (contract 1) is stored here and never written to disk.
States follow docs/contracts/game-states.md; events follow docs/contracts/events.md.

Two things change the game:
- HTTP calls (claim, runbook, ack, buttons, chat replies, resolve, give up), each
  under the lock;
- the background loop calling `tick()` every couple of seconds: it looks at
  Prometheus, pages when the lab's alert fires (or the fallback time passes),
  keeps the health bar up to date, sends chat messages that are due, runs the
  watch and ends the game at the time limit.
Resolve, the watch and the endings live in outcome.py; Dana and Sam in chat.py.
"""

import hmac
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from itr_agent.actions import change_text, check_input, label
from itr_agent.chat import Chat
from itr_agent.checks import Checker, http_status
from itr_agent.errors import AgentError
from itr_agent.events import EventLog
from itr_agent.feed import Feed
from itr_agent.observe import Observation, ServiceStatus, observe
from itr_agent.outcome import OutcomeMixin
from itr_agent.prometheus import Alert, PrometheusLike
from itr_agent.runbook import validate_runbook
from itr_agent.verbs import CommandRunner, VerbError, Verbs
from itr_agent.world import World

GameState = Literal["booting", "ready", "paged", "investigating", "watching", "ended"]
HEALTH_EVENT_EVERY_S = 30
ACTIONS_PER_MINUTE = 30
IDEMPOTENCY_TTL_S = 600
PLAYING = ("ready", "paged", "investigating", "watching")
log = logging.getLogger(__name__)


@dataclass
class Agent(OutcomeMixin):
    world: World
    runner: CommandRunner
    prometheus: PrometheusLike
    clock: Callable[[], float] = time.monotonic
    http: Callable[[str, str], int | None] = http_status
    # Always True for now. A later step replaces this with real checks
    # (Compose up, Prometheus answering).
    booted: bool = True
    components: dict[str, str] = field(default_factory=dict)
    game_id: str | None = None
    public_base_url: str | None = None
    state: GameState = "booting"
    severity: str = "SEV2"
    reopened: bool = False
    end_reason: str | None = None
    time_limit_s: int = 2400
    events: EventLog = field(default_factory=EventLog)
    feed: Feed = field(default_factory=Feed)
    # Changes the player made, newest first (shown in "Recent changes")
    changes: list[dict] = field(default_factory=list)
    # The page
    page_title: str | None = None
    acknowledged_t: int | None = None
    # What the background loop last saw
    alerts: list[Alert] = field(default_factory=list)
    services: list[ServiceStatus] = field(default_factory=list)
    health: int = 100
    failed_checkouts: int = 0
    # Dana and Sam; created with the runbook
    chat: Chat | None = None
    # Resolve
    resolve_attempts: int = 0
    wrong_resolves: int = 0
    _token: str | None = field(default=None, repr=False)
    _runbook: dict | None = field(default=None, repr=False)
    _fault_at: float | None = field(default=None, repr=False)
    _paged_at: float | None = field(default=None, repr=False)
    _failed_baseline: float | None = field(default=None, repr=False)
    _last_health_event_t: int | None = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    # One button at a time; the main lock is never held while a command runs.
    _action_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _recent_actions: deque = field(default_factory=deque, repr=False)
    _idempotent: dict = field(default_factory=dict, repr=False)
    _resolve_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_resolve_at: float | None = field(default=None, repr=False)
    _watch_started_at: float | None = field(default=None, repr=False)
    _next_watch_at: float = field(default=0.0, repr=False)
    _ending: bool = field(default=False, repr=False)
    _ended_at: float | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.verbs = Verbs(self.world, self.runner)
        self.checker = Checker(self.world, self.runner, self.prometheus, self.http)

    # ----- facts -------------------------------------------------------------

    @property
    def claimed(self) -> bool:
        return self._token is not None

    @property
    def runbook_loaded(self) -> bool:
        return self._runbook is not None

    @property
    def runbook(self) -> dict:
        """Only for building the player's view; never sent as a whole."""
        if self._runbook is None:
            raise AgentError(409, "not_ready", "The game hasn't started yet.")
        return self._runbook

    def read(self, view: Callable[["Agent"], dict]) -> dict:
        """Build a player view under the lock, so the loop can't change the game halfway."""
        with self._lock:
            return view(self)

    def game_time(self) -> int | None:
        """Seconds since the page (t). None before the page. Stops at the end."""
        if self._paged_at is None:
            return None
        now = self._ended_at if self._ended_at is not None else self.clock()
        return int(now - self._paged_at)

    # ----- control: claim and runbook ---------------------------------------

    def claim(self, game_id: str, token: str, public_base_url: str) -> None:
        """Bind this server to one game. Accepted once per boot (contract 2, claim)."""
        with self._lock:
            if self.claimed:
                raise AgentError(
                    409, "already_claimed", "This server is already claimed."
                )
            if not self.booted:
                raise AgentError(409, "not_booted", "The server is still booting.")
            self.game_id = game_id
            self.public_base_url = public_base_url
            self._token = token

    def require_claimed(self) -> None:
        if not self.claimed:
            raise AgentError(409, "not_claimed", "Claim the server first.")

    def require_token(self, authorization: str | None) -> None:
        """Check `Authorization: Bearer <token>` in constant time."""
        presented = ""
        if authorization and authorization.startswith("Bearer "):
            presented = authorization.removeprefix("Bearer ")
        expected = self._token or ""
        if not expected or not hmac.compare_digest(
            presented.encode(), expected.encode()
        ):
            raise AgentError(401, "unauthorized", "Missing or wrong control token.")

    def require_game(self, game_id_header: str) -> None:
        """Player calls must carry the game id the gateway checked (X-Itr-Game-Id)."""
        if not self.claimed:
            raise AgentError(409, "not_ready", "The game hasn't started yet.")
        if game_id_header != self.game_id:
            raise AgentError(404, "wrong_game", "This server belongs to another game.")

    def load_runbook(self, game_id: str, time_limit_s: int, runbook: dict) -> None:
        """Keep the runbook in memory, run setup and fault, become `ready`."""
        with self._lock:
            if self._runbook is not None:
                raise AgentError(409, "already_loaded", "A runbook is already loaded.")
            if game_id != self.game_id:
                raise AgentError(
                    422, "invalid_runbook", "game_id doesn't match the claimed game."
                )
            problems = validate_runbook(runbook, self.world)
            if problems:
                raise AgentError(422, "invalid_runbook", "; ".join(problems[:5]))

            for step in runbook.get("setup", []) + runbook["fault"]:
                try:
                    result = self.verbs.run_fault_step(step)
                except VerbError as error:
                    raise AgentError(422, "invalid_runbook", str(error)) from error
                log.info(
                    "fault step %s %s -> exit %s",
                    result.verb,
                    result.target,
                    result.exit_code,
                )
                if not result.ok:
                    raise AgentError(
                        500,
                        "internal",
                        f"fault step {result.verb} {result.target} failed",
                    )

            self._runbook = runbook
            self.chat = Chat(
                runbook["chat"], self.events, self.feed, runbook["scene"]["starts_at"]
            )
            self.time_limit_s = time_limit_s
            self.state = "ready"
            self._fault_at = self.clock()
            self.events.add("fault_started")

    # ----- player: acknowledge ------------------------------------------------

    def acknowledge(self) -> int:
        with self._lock:
            if self.state == "ended" or self._ending:
                raise AgentError(409, "game_over", "The game is over.")
            if self.state != "paged":
                message = (
                    "There is no page to acknowledge yet."
                    if self.state == "ready"
                    else "The page is already acknowledged."
                )
                raise AgentError(409, "wrong_state", message)
            t = self.game_time() or 0
            self.acknowledged_t = t
            self.state = "investigating"
            self.events.add("acknowledged", t=t)
            self.chat.trigger("acknowledged", t)
            return t

    # ----- player: chat ---------------------------------------------------------

    def reply(self, message_id: str, option_id: str) -> None:
        """Pick one of the replies a chat message offers (contract 2, /v1/chat/replies)."""
        with self._lock:
            if self.state == "ended" or self._ending:
                raise AgentError(409, "game_over", "The game is over.")
            self.chat.reply(message_id, option_id, self.game_time())

    # ----- player: buttons -----------------------------------------------------

    def press(self, action_id: str, given: dict[str, str], key: str) -> dict:
        """Run one button (contract 2, POST /v1/actions). Returns the ActionResult."""
        with self._lock:
            if self.state not in PLAYING or self._ending:
                raise AgentError(409, "game_over", "The game is over.")
            now = self.clock()
            self._forget_old_keys(now)
            if key in self._idempotent:
                return self._idempotent[key][1]  # a repeated click: same answer
            action = next(
                (a for a in self.runbook["actions"] if a["id"] == action_id), None
            )
            if action is None:
                raise AgentError(404, "unknown_action", "There is no such button.")
            values = check_input(action, given)
            self._check_rate(now)

        if not self._action_lock.acquire(blocking=False):
            raise AgentError(429, "busy", "Another action is still running.", 2)
        try:
            result = self.verbs.run_button(action["verb"], action["target"], values)
        except VerbError as error:
            raise AgentError(422, "bad_input", str(error)) from error
        finally:
            self._action_lock.release()

        with self._lock:
            t = self.game_time()
            response = {
                "action_id": action_id,
                "label": label(action),
                "command": result.command,
                "exit_code": result.exit_code,
                "output": result.output,
                "truncated": result.truncated,
                "duration_ms": result.duration_ms,
                "t": t,
            }
            self.events.add(
                "action",
                {
                    "action_id": action_id,
                    "verb": action["verb"],
                    "target": action["target"],
                    "params": values,
                    "command": result.command,
                    "exit_code": result.exit_code,
                    "output": result.output,
                    "truncated": result.truncated,
                    "duration_ms": result.duration_ms,
                    "risky": bool(action.get("risky", False)),  # hidden until the end
                },
                t=t,
            )
            self.feed.add("action_result", t, result=response)
            text = change_text(result, values)
            if text:
                self.changes.insert(
                    0, {"t": t, "service": action["target"], "text": text, "by": "you"}
                )
            self._idempotent[key] = (self.clock(), response)
            self.chat.trigger("action", t, verb=action["verb"], target=action["target"])
            log.info("action %s -> exit %s", action_id, result.exit_code)
            return response

    def _check_rate(self, now: float) -> None:
        while self._recent_actions and now - self._recent_actions[0] >= 60:
            self._recent_actions.popleft()
        if len(self._recent_actions) >= ACTIONS_PER_MINUTE:
            wait = int(60 - (now - self._recent_actions[0])) + 1
            raise AgentError(429, "too_fast", "Too many actions. Wait a moment.", wait)
        self._recent_actions.append(now)

    def _forget_old_keys(self, now: float) -> None:
        old = [
            k for k, (at, _) in self._idempotent.items() if now - at > IDEMPOTENCY_TTL_S
        ]
        for k in old:
            del self._idempotent[k]

    # ----- background loop ---------------------------------------------------

    def tick(self) -> None:
        """One look at the world. Safe to call any time; does nothing before the runbook."""
        if not self.runbook_loaded or self.state == "ended" or self._ending:
            return
        seen = observe(self.prometheus, self.world)  # network: outside the lock
        with self._lock:
            if self.state == "ended" or self._ending:
                return
            self.alerts = seen.alerts
            self.services = seen.services
            if self.state == "ready":
                self._maybe_page(seen)
            if self._paged_at is not None:
                self._update_health(seen)
                self.chat.deliver(self.game_time())
            t = self.game_time()
            time_up = t is not None and t >= self.time_limit_s
            watching = self.state == "watching"
            watch_due = watching and self.clock() >= self._next_watch_at
        if time_up:
            self.finish("unresolved", [("time_limit", {"watch_cut_short": watching})])
        elif watch_due:
            self.watch_step()

    def _maybe_page(self, seen: Observation) -> None:
        page = self.runbook["page"]
        mine = [a for a in seen.alerts if a.name == page["alert"]]
        firing = next((a for a in mine if a.state == "firing"), None)
        waited = self.clock() - (self._fault_at or self.clock())
        if firing is not None:
            self._page("alert", firing.value, seen)
        elif waited >= page["fallback_after_s"]:
            pending_value = next((a.value for a in mine if a.value is not None), None)
            self._page("fallback", pending_value, seen)

    def _page(self, source: str, value: float | None, seen: Observation) -> None:
        page = self.runbook["page"]
        shown = "?" if value is None else str(round(value))
        self.page_title = page["title"].replace("{value}", shown)
        self._paged_at = self.clock()
        self._failed_baseline = seen.failed_checkouts_total or 0.0
        self.state = "paged"
        self.events.add(
            "paged",
            {"source": source, "title": self.page_title, "severity": page["severity"]},
            t=0,
        )
        log.info("paged (%s): %s", source, self.page_title)

    def _update_health(self, seen: Observation) -> None:
        if seen.failed_checkouts_total is None:
            return  # no data this time: keep the last value
        budget = self.runbook["health"]["budget_failed_checkouts"]
        failed = max(0.0, seen.failed_checkouts_total - (self._failed_baseline or 0.0))
        self.failed_checkouts = int(failed)
        self.health = max(0, min(100, round(100 - 100 * failed / budget)))
        t = self.game_time() or 0
        last = self._last_health_event_t
        if last is None or t - last >= HEALTH_EVENT_EVERY_S:
            self._last_health_event_t = t
            self.events.add(
                "health",
                {"value": self.health, "failed_checkouts": self.failed_checkouts},
                t=t,
            )

    def run_loop(self, stop: threading.Event, every_s: float = 2.0) -> None:
        """Call tick() until stopped. Errors are logged, never fatal."""
        while not stop.wait(every_s):
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")

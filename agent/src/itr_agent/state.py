"""Everything the agent remembers about its one game. Lives only in memory.

The runbook (contract 1) is stored here and never written to disk.
States follow docs/contracts/game-states.md.
"""

import hmac
import logging
import threading
from dataclasses import dataclass, field
from typing import Literal

from itr_agent.errors import AgentError
from itr_agent.events import EventLog
from itr_agent.runbook import validate_runbook
from itr_agent.verbs import CommandRunner, VerbError, Verbs
from itr_agent.world import World

GameState = Literal["booting", "ready", "paged", "investigating", "watching", "ended"]
log = logging.getLogger(__name__)


@dataclass
class Agent:
    world: World
    runner: CommandRunner
    # Step 1-3: always True. A later step replaces this with real checks
    # (Compose up, Prometheus answering).
    booted: bool = True
    components: dict[str, str] = field(default_factory=dict)
    game_id: str | None = None
    public_base_url: str | None = None
    state: GameState = "booting"
    time_limit_s: int = 2400
    events: EventLog = field(default_factory=EventLog)
    _token: str | None = field(default=None, repr=False)
    _runbook: dict | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.verbs = Verbs(self.world, self.runner)

    @property
    def claimed(self) -> bool:
        return self._token is not None

    @property
    def runbook_loaded(self) -> bool:
        return self._runbook is not None

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
            self.time_limit_s = time_limit_s
            self.state = "ready"
            self.events.add("fault_started")

"""Everything the agent remembers about its one game. Lives only in memory.

The runbook (contract 1) will be stored here in step 3 and is never written to disk.
States follow docs/contracts/game-states.md.
"""

import hmac
from dataclasses import dataclass, field
from typing import Literal

from itr_agent.errors import AgentError

GameState = Literal["booting", "ready", "paged", "investigating", "watching", "ended"]


@dataclass
class Agent:
    world_name: str
    world_version: str
    # Step 1: always True. Step 2 replaces this with real checks (Compose up, Prometheus answering).
    booted: bool = True
    components: dict[str, str] = field(default_factory=dict)
    game_id: str | None = None
    public_base_url: str | None = None
    state: GameState = "booting"
    _token: str | None = field(default=None, repr=False)

    @property
    def claimed(self) -> bool:
        return self._token is not None

    def claim(self, game_id: str, token: str, public_base_url: str) -> None:
        """Bind this server to one game. Accepted once per boot (contract 2, claim)."""
        if self.claimed:
            raise AgentError(409, "already_claimed", "This server is already claimed.")
        if not self.booted:
            raise AgentError(409, "not_booted", "The server is still booting.")
        self.game_id = game_id
        self.public_base_url = public_base_url
        self._token = token

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

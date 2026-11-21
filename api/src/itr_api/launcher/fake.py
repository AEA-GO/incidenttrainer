"""FakeLauncher: game servers in memory, for tests (contract 4). It can be told to fail."""

import itertools
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from itr_api.launcher import ErrorKind, LaunchError, Server, ServerSpec

RETRYABLE = {"capacity": True, "quota": False, "config": False, "unknown": True}


@dataclass
class FakeLauncher:
    fail_with: ErrorKind | None = None  # the next start() raises this kind
    servers: dict[str, Server] = field(default_factory=dict)
    started: list[str] = field(default_factory=list)  # game ids, in order
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def start(self, spec: ServerSpec) -> Server:
        for server in self.servers.values():
            if server.game_id == spec.game_id and server.state != "gone":
                return server  # idempotent per game
        if self.fail_with:
            kind, self.fail_with = self.fail_with, None
            raise LaunchError(kind, f"fake {kind} error", RETRYABLE[kind])
        number = next(self._ids)
        server = Server(
            server_id=f"i-fake{number:05d}",
            game_id=spec.game_id,
            world=spec.world,
            address=f"10.20.0.{10 + number}",
            state="running",
            launched_at=datetime.now(UTC),
            expires_at=spec.expires_at,
            instance_type="t4g.medium",
            zone="eu-central-1a",
        )
        self.servers[server.server_id] = server
        self.started.append(spec.game_id)
        return server

    def stop(self, server_id: str) -> None:
        if server_id in self.servers:
            self.servers[server_id] = replace(
                self.servers[server_id], state="gone", address=None
            )

    def address(self, server_id: str) -> str | None:
        server = self.servers.get(server_id)
        return server.address if server and server.state != "gone" else None

    def list(self) -> list[Server]:
        return [s for s in self.servers.values() if s.state != "gone"]

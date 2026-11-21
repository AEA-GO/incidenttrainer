"""Contract 4: the server launcher. The API and the cleanup Lambda start, find and delete
game servers only through this interface (docs/contracts/launcher.md).

    ITR_LAUNCHER=ec2    Ec2Launcher (AWS): ITR_SANDBOX_SUBNETS, ITR_TEMPLATE_PREFIX
    ITR_LAUNCHER=fake   FakeLauncher (tests, laptops without AWS)
"""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

ServerState = Literal["starting", "running", "stopping", "gone"]
ErrorKind = Literal["capacity", "quota", "config", "unknown"]


@dataclass(frozen=True)
class ServerSpec:
    game_id: str  # 22 chars, also the idempotency key
    world: str  # e.g. "teapot-basic"
    expires_at: datetime  # UTC; start + 45 min


@dataclass(frozen=True)
class Server:
    server_id: str  # "i-0abc..." for EC2
    game_id: str
    world: str
    address: str | None  # private IPv4; None until known
    state: ServerState
    launched_at: datetime
    expires_at: datetime
    instance_type: str | None = (
        None  # which size it got (capacity fallback, decided 21 Nov)
    )
    zone: str | None = None


class LaunchError(Exception):
    def __init__(self, kind: ErrorKind, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


class Launcher(Protocol):
    def start(self, spec: ServerSpec) -> Server: ...
    def stop(self, server_id: str) -> None: ...
    def address(self, server_id: str) -> str | None: ...
    def list(self) -> list[Server]: ...


def from_env(env: dict[str, str] | None = None) -> Launcher:
    env = dict(os.environ if env is None else env)
    kind = env.get("ITR_LAUNCHER", "fake")
    if kind == "ec2":
        from itr_api.launcher.ec2 import Ec2Launcher

        subnets = [
            s.strip()
            for s in env.get("ITR_SANDBOX_SUBNETS", "").split(",")
            if s.strip()
        ]
        if not subnets:
            raise RuntimeError("ITR_SANDBOX_SUBNETS is empty")
        return Ec2Launcher(
            subnets=subnets, template_prefix=env.get("ITR_TEMPLATE_PREFIX", "itr-main-")
        )
    if kind == "fake":
        from itr_api.launcher.fake import FakeLauncher

        return FakeLauncher()
    raise RuntimeError(f"unknown ITR_LAUNCHER {kind!r}")

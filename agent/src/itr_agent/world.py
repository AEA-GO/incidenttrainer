"""The world manifest (contract 1, world.schema.json): what the shop looks like.

Baked into the server image at /opt/itr/world.yaml. Labs CI validates it with the
schema; here we only read the parts the agent uses.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Service:
    name: str
    kind: str
    url: str | None
    calls: tuple[str, ...]
    map: dict[str, int] | None
    versions: tuple[str, ...] = ()
    scalable: bool = False


@dataclass(frozen=True)
class StatusRules:
    """How the service map colours a service (world.yaml `status`)."""

    degraded_error_percent: float
    down_error_percent: float
    window_s: int


@dataclass(frozen=True)
class World:
    name: str
    version: str
    compose_dir: Path
    project: str
    config_dir: str
    versions_file: str
    logs_dir: Path
    prometheus_url: str
    alerts: tuple[str, ...]
    services: dict[str, Service]
    metrics: dict[str, str]
    dashboards: tuple[dict[str, str], ...]
    status: StatusRules

    def config_file(self, service: str) -> Path:
        """The env file a service reads its settings and flags from."""
        return self.compose_dir / self.config_dir / f"{service}.env"

    @property
    def versions_path(self) -> Path:
        """The file with <SERVICE>_VERSION=x.y.z lines (deploy and rollback)."""
        return self.compose_dir / self.versions_file


def load_world(path: Path, compose_dir_override: Path | None = None) -> World:
    data = yaml.safe_load(path.read_text())
    if data.get("format") != "itr-world/v1":
        raise ValueError(f"{path}: not an itr-world/v1 manifest")
    compose = data["compose"]
    services = {
        s["name"]: Service(
            s["name"],
            s["kind"],
            s.get("url"),
            tuple(s.get("calls", [])),
            s.get("map"),
            tuple(s.get("versions", [])),
            bool(s.get("scalable", False)),
        )
        for s in data["services"]
    }
    status = data["status"]
    return World(
        name=data["name"],
        version=data["version"],
        # On a laptop the shop lives in the repo, not in /opt (ITR_COMPOSE_DIR).
        compose_dir=(compose_dir_override or Path(compose["dir"])).resolve(),
        project=compose["project"],
        config_dir=compose["config_dir"],
        versions_file=compose["versions_file"],
        logs_dir=Path(data["logs_dir"]),
        prometheus_url=data["prometheus_url"],
        alerts=tuple(data["alerts"]),
        services=services,
        metrics=dict(data["metrics"]),
        dashboards=tuple(data["grafana"]["dashboards"]),
        status=StatusRules(
            degraded_error_percent=status["degraded_error_percent"],
            down_error_percent=status["down_error_percent"],
            window_s=status["window_s"],
        ),
    )

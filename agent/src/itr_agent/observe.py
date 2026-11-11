"""One look at the shop through Prometheus: alerts, failed checkouts, service colours.

Runs in the background loop, outside the agent's lock (network calls can be slow).
"""

from dataclasses import dataclass, field

from itr_agent.prometheus import Alert, PrometheusLike, fill
from itr_agent.world import World


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    status: str  # ok | degraded | down | unknown
    label: str  # the text shown next to the colour
    detail: str


@dataclass(frozen=True)
class Observation:
    alerts: list[Alert] = field(default_factory=list)
    failed_checkouts_total: float | None = None
    services: list[ServiceStatus] = field(default_factory=list)


def service_status(
    name: str, error_rate: float | None, down: bool, world: World
) -> ServiceStatus:
    rules = world.status
    if down:
        return ServiceStatus(name, "down", "DOWN", "not answering")
    if error_rate is None:
        return ServiceStatus(name, "unknown", "NO DATA", "")
    detail = f"errors {error_rate:.0f}%"
    if error_rate >= rules.down_error_percent:
        return ServiceStatus(name, "down", "FAILING", detail)
    if error_rate >= rules.degraded_error_percent:
        return ServiceStatus(name, "degraded", "ERRORS", detail)
    return ServiceStatus(name, "ok", "OK", detail)


def observe(prometheus: PrometheusLike, world: World) -> Observation:
    alerts = prometheus.alerts()
    down = {
        a.labels.get("service")
        for a in alerts
        if a.state == "firing" and a.name == "ServiceDown"
    }
    services = []
    for service in world.services.values():
        rate = None
        if service.kind == "app":
            query = fill(
                world.metrics["error_rate"], service.name, None, world.status.window_s
            )
            rate = prometheus.scalar(query)
        services.append(service_status(service.name, rate, service.name in down, world))
    failed = prometheus.scalar(world.metrics["failed_checkouts_total"])
    return Observation(alerts=alerts, failed_checkouts_total=failed, services=services)

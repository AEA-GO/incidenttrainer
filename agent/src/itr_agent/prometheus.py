"""Talking to the world's Prometheus (on the server's loopback, e.g. http://127.0.0.1:9090).

Never raises on network problems: an unreachable Prometheus just means "no data",
and the page fallback still works.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    name: str
    state: str  # "pending" or "firing"
    value: float | None
    labels: dict[str, str]
    summary: str


class PrometheusLike(Protocol):
    def alerts(self) -> list[Alert]: ...
    def scalar(self, query: str) -> float | None: ...


def fill(template: str, service: str, route: str | None, window_s: int) -> str:
    """Fill a world metrics template ($service, $route, $window)."""
    return (
        template.replace("$service", service)
        .replace("$route", route or ".*")
        .replace("$window", f"{window_s}s")
    )


def parse_alerts(payload: dict) -> list[Alert]:
    alerts = []
    for item in payload.get("data", {}).get("alerts", []):
        labels = item.get("labels", {})
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            value = None
        alerts.append(
            Alert(
                name=labels.get("alertname", ""),
                state=item.get("state", ""),
                value=value,
                labels=labels,
                summary=item.get("annotations", {}).get("summary", ""),
            )
        )
    return alerts


def parse_scalar(payload: dict) -> float | None:
    result = payload.get("data", {}).get("result", [])
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


class PrometheusClient:
    def __init__(self, base_url: str, timeout_s: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            log.warning("prometheus unreachable: %s", error)
            return None

    def alerts(self) -> list[Alert]:
        payload = self._get("/api/v1/alerts")
        return parse_alerts(payload) if payload else []

    def scalar(self, query: str) -> float | None:
        payload = self._get("/api/v1/query", {"query": query})
        return parse_scalar(payload) if payload else None

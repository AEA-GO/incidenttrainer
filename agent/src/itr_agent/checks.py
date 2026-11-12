"""Checks: is the shop really fixed? (contract 1, "Check verbs").

Used for Resolve (success checks), the watch, and damage checks at the end.
They look at the running system only, never at what the player typed.
A check that can't get data fails: "no data" is never treated as healthy.
"""

import json
import logging
import re
import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

from itr_agent.prometheus import PrometheusLike, fill
from itr_agent.verbs import CommandRunner, env_value
from itr_agent.world import World

CHECK_VERBS = {
    "http_ok",
    "error_rate_below",
    "latency_p95_below",
    "disk_free_percent",
    "config_equals",
    "container_running",
    "no_restarts",
    "metric",
}
HTTP_TIMEOUT_S = 3
COMPARE = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
}
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    message: str  # the lab's message with {value} and {threshold} filled in


def show(value: object) -> str:
    """A number as a player reads it: 18.0 -> 18, 0.43 -> 0.4, None -> ?"""
    if value is None:
        return "?"
    if isinstance(value, float):
        text = f"{value:.1f}"
        return text.removesuffix(".0")
    return str(value)


def http_status(
    url: str, method: str = "GET", timeout_s: float = HTTP_TIMEOUT_S
) -> int | None:
    """The HTTP status of one request, or None if nothing answered."""
    request = urllib.request.Request(
        url, method=method, data=b"" if method == "POST" else None
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def parse_compose_ps(output: str) -> list[dict]:
    """`docker compose ps --format json`: one JSON object per line, or one array."""
    output = output.strip()
    if not output:
        return []
    try:
        data = json.loads(output)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        return [
            json.loads(line) for line in output.splitlines() if line.startswith("{")
        ]


def parse_started_at(text: str) -> datetime:
    """Docker's StartedAt has nanoseconds; Python reads up to microseconds."""
    text = text.strip().replace("Z", "+00:00")
    match = re.match(r"^(.*?\.\d{1,6})\d*(\+00:00)$", text)
    if match:
        text = match.group(1) + match.group(2)
    return datetime.fromisoformat(text)


class Checker:
    def __init__(
        self,
        world: World,
        runner: CommandRunner,
        prometheus: PrometheusLike,
        http: Callable[[str, str], int | None] = http_status,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.world = world
        self.runner = runner
        self.prometheus = prometheus
        self.http = http
        self.now = now

    def run_all(self, checks: list[dict]) -> list[CheckResult]:
        """Run checks in parallel (a slow one doesn't hold up the others)."""
        if not checks:
            return []
        with ThreadPoolExecutor(max_workers=min(8, len(checks))) as pool:
            return list(pool.map(self.run, checks))

    def run(self, check: dict) -> CheckResult:
        try:
            passed, value, threshold = getattr(self, f"_{check['verb']}")(
                check["params"]
            )
        except Exception as error:  # a broken check fails; it never crashes the game
            log.exception("check %s could not run", check.get("id"))
            passed, value, threshold = False, f"error: {error}", None
        message = (
            check["message"]
            .replace("{value}", show(value))
            .replace("{threshold}", show(threshold))
        )
        return CheckResult(check["id"], passed, message)

    # ----- verbs -----------------------------------------------------------------
    # Each returns (passed, value, threshold).

    def _http_ok(self, p: dict) -> tuple[bool, object, object]:
        url = self.world.services[p["service"]].url + p["path"]
        expected = p.get("expect_status", 200)
        statuses = [
            self.http(url, p.get("method", "GET")) for _ in range(p.get("attempts", 5))
        ]
        bad = [s for s in statuses if s != expected]
        return not bad, (bad[0] if bad else expected) or "no answer", expected

    def _error_rate_below(self, p: dict) -> tuple[bool, object, object]:
        query = fill(
            self.world.metrics["error_rate"],
            p["service"],
            p.get("route"),
            p["window_s"],
        )
        rate = self.prometheus.scalar(query)
        return rate is not None and rate < p["max_percent"], rate, p["max_percent"]

    def _latency_p95_below(self, p: dict) -> tuple[bool, object, object]:
        query = fill(
            self.world.metrics["latency_p95"],
            p["service"],
            p.get("route"),
            p["window_s"],
        )
        ms = self.prometheus.scalar(query)
        return ms is not None and ms < p["max_ms"], ms, p["max_ms"]

    def _disk_free_percent(self, p: dict) -> tuple[bool, object, object]:
        usage = shutil.disk_usage(p["mount"])
        free = 100 * usage.free / usage.total
        return free >= p["min_percent"], free, p["min_percent"]

    def _config_equals(self, p: dict) -> tuple[bool, object, object]:
        value = env_value(self.world.config_file(p["service"]), p["key"])
        return value == p["value"], value, p["value"]

    def _containers(self, service: str) -> list[dict]:
        argv = ["docker", "compose", "-p", self.world.project]
        argv += ["ps", "--all", "--format", "json", service]
        done = self.runner.run(argv, self.world.compose_dir, 10)
        return parse_compose_ps(done.output) if done.exit_code == 0 else []

    def _container_running(self, p: dict) -> tuple[bool, object, object]:
        containers = self._containers(p["service"])
        states = [
            f"{c.get('State', '?')}{'/' + c['Health'] if c.get('Health') else ''}"
            for c in containers
        ]
        ok = bool(containers) and all(
            c.get("State") == "running" and c.get("Health", "") in ("", "healthy")
            for c in containers
        )
        return ok, ", ".join(states) or "not found", "running"

    def _no_restarts(self, p: dict) -> tuple[bool, object, object]:
        ids = [c["ID"] for c in self._containers(p["service"]) if c.get("ID")]
        if not ids:
            return False, "not found", p["window_s"]
        argv = ["docker", "inspect", "--format", "{{.State.StartedAt}}", *ids]
        done = self.runner.run(argv, self.world.compose_dir, 10)
        if done.exit_code != 0:
            return False, "unknown", p["window_s"]
        ages = [
            (self.now() - parse_started_at(line)).total_seconds()
            for line in done.output.splitlines()
            if line.strip()
        ]
        youngest = min(ages) if ages else 0.0
        return youngest >= p["window_s"], int(youngest), p["window_s"]

    def _metric(self, p: dict) -> tuple[bool, object, object]:
        value = self.prometheus.scalar(p["query"])
        ok = value is not None and COMPARE[p["op"]](value, p["value"])
        return ok, value, p["value"]

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itr_agent.control import build_control_app
from itr_agent.player import build_player_app
from itr_agent.prometheus import Alert
from itr_agent.state import Agent
from itr_agent.verbs import CommandResult
from itr_agent.world import World, load_world

DEV = Path(__file__).resolve().parents[1] / "dev"
GAME_ID = "q3V9xYk2Lm8Ta1BcD4eF0g"
TOKEN = "t" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CLAIM = {
    "game_id": GAME_ID,
    "token": TOKEN,
    "public_base_url": "https://drer7le45ultl.cloudfront.net",
}


class FakeRunner:
    """Records commands instead of running them. Set `exit_code` to simulate failure."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path]] = []
        self.exit_code = 0

    def run(self, argv: list[str], cwd: Path, timeout_s: int) -> CommandResult:
        self.calls.append((argv, cwd))
        return CommandResult(self.exit_code, f"Container {argv[-1]} Recreated\n", 1200)


class FakeClock:
    """Time that only moves when a test says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePrometheus:
    """Answers like Prometheus would. Tests set `firing`, `error_rates`, `failed`."""

    def __init__(self) -> None:
        self.alert_list: list[Alert] = []
        self.error_rates: dict[str, float | None] = {}
        self.failed: float | None = 0.0

    def fire(
        self, name: str, value: float, state: str = "firing", **labels: str
    ) -> None:
        self.alert_list = [
            Alert(name, state, value, {"alertname": name, **labels}, f"{name} summary")
        ]

    def alerts(self) -> list[Alert]:
        return list(self.alert_list)

    def scalar(self, query: str) -> float | None:
        if "shop_checkouts_total" in query:
            return self.failed
        for service, rate in self.error_rates.items():
            if f'service="{service}"' in query:
                return rate
        return None


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def prometheus() -> FakePrometheus:
    return FakePrometheus()


@pytest.fixture
def world(tmp_path: Path) -> World:
    # Verbs edit config files: give them a copy, never the repo's dev/config.
    shutil.copytree(DEV / "config", tmp_path / "config")
    return load_world(DEV / "world.yaml", compose_dir_override=tmp_path)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def agent(
    world: World, runner: FakeRunner, prometheus: FakePrometheus, clock: FakeClock
) -> Agent:
    return Agent(world=world, runner=runner, prometheus=prometheus, clock=clock)


@pytest.fixture
def control(agent: Agent) -> TestClient:
    return TestClient(build_control_app(agent))


@pytest.fixture
def player(agent: Agent) -> TestClient:
    return TestClient(build_player_app(agent))


@pytest.fixture
def push() -> dict:
    """What the gateway sends to PUT /control/v1/runbook for the dev lab."""
    return json.loads((DEV / "labs" / "dev-lab.push.json").read_text())


@pytest.fixture
def started(control, push) -> None:
    """Claimed, runbook loaded, fault running: state `ready`, waiting for the page."""
    assert control.post("/control/v1/claim", json=CLAIM).status_code == 204
    assert (
        control.put("/control/v1/runbook", json=push, headers=AUTH).status_code == 202
    )


PLAYER = {"X-Itr-Game-Id": GAME_ID}

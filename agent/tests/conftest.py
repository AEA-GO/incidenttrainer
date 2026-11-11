import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itr_agent.control import build_control_app
from itr_agent.player import build_player_app
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


@pytest.fixture
def world(tmp_path: Path) -> World:
    # Verbs edit config files: give them a copy, never the repo's dev/config.
    shutil.copytree(DEV / "config", tmp_path / "config")
    return load_world(DEV / "world.yaml", compose_dir_override=tmp_path)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def agent(world: World, runner: FakeRunner) -> Agent:
    return Agent(world=world, runner=runner)


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

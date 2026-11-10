import pytest
from fastapi.testclient import TestClient
from itr_agent.control import build_control_app
from itr_agent.player import build_player_app
from itr_agent.state import Agent

GAME_ID = "q3V9xYk2Lm8Ta1BcD4eF0g"
TOKEN = "t" * 43
CLAIM = {
    "game_id": GAME_ID,
    "token": TOKEN,
    "public_base_url": "https://drer7le45ultl.cloudfront.net",
}


@pytest.fixture
def agent() -> Agent:
    return Agent(world_name="dev-shop", world_version="0.0.0")


@pytest.fixture
def control(agent: Agent) -> TestClient:
    return TestClient(build_control_app(agent))


@pytest.fixture
def player(agent: Agent) -> TestClient:
    return TestClient(build_player_app(agent))

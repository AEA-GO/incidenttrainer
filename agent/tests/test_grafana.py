"""Grafana per game: at claim, root_url + sub path into the world's .env, recreate, wait."""

from conftest import CLAIM
from fastapi.testclient import TestClient
from itr_agent.control import build_control_app
from itr_agent.grafana import POLLS, root_url
from itr_agent.state import Agent
from itr_agent.verbs import env_value


def make(world, runner, prometheus, clock, http) -> tuple[Agent, TestClient]:
    agent = Agent(
        world=world,
        runner=runner,
        prometheus=prometheus,
        clock=clock,
        http=http,
        grafana_in_background=False,
        sleep=lambda _s: None,
    )
    return agent, TestClient(build_control_app(agent))


def test_root_url():
    assert (
        root_url("https://drer7le45ultl.cloudfront.net", "Zo6nEXJRVS0qCy4VkP7JIQ")
        == "https://drer7le45ultl.cloudfront.net/s/Zo6nEXJRVS0qCy4VkP7JIQ/grafana/"
    )


def test_claim_points_grafana_at_the_game(world, runner, prometheus, clock, http):
    world.versions_path.write_text("ORDERS_VERSION=1.4.1\nGRAFANA_BIND=0.0.0.0\n")
    agent, control = make(world, runner, prometheus, clock, http)
    assert control.post("/control/v1/claim", json=CLAIM).status_code == 204

    url = f"{CLAIM['public_base_url']}/s/{CLAIM['game_id']}/grafana/"
    assert env_value(world.versions_path, "GRAFANA_ROOT_URL") == url
    assert env_value(world.versions_path, "GRAFANA_SUB_PATH") == "true"
    assert env_value(world.versions_path, "ORDERS_VERSION") == "1.4.1"  # rest kept
    assert env_value(world.versions_path, "GRAFANA_BIND") == "0.0.0.0"
    argv, cwd = runner.calls[-1]
    assert argv == ["docker", "compose", "-p", world.project, "up", "-d", "grafana"]
    assert cwd == world.compose_dir
    assert (
        http.urls[-1]
        == f"http://127.0.0.1:3000/s/{CLAIM['game_id']}/grafana/api/health"
    )
    assert agent.components["grafana"] == "ready"
    assert control.get("/control/v1/ready").json()["components"]["grafana"] == "ready"


def test_grafana_that_never_answers_is_reported_not_fatal(
    world, runner, prometheus, clock, http
):
    world.versions_path.write_text("")
    http.status = None
    agent, control = make(world, runner, prometheus, clock, http)
    assert control.post("/control/v1/claim", json=CLAIM).status_code == 204
    assert agent.components["grafana"] == "failed"
    assert len(http.urls) == POLLS
    assert agent.claimed  # the game goes on without dashboards


def test_compose_failure_is_reported(world, runner, prometheus, clock, http):
    world.versions_path.write_text("")
    runner.exit_code = 1
    agent, control = make(world, runner, prometheus, clock, http)
    assert control.post("/control/v1/claim", json=CLAIM).status_code == 204
    assert agent.components["grafana"] == "failed" and http.urls == []

"""The terminal in Junior labs: ttyd is started with the runbook and stopped at the end."""

import copy
import os
import pwd
import shutil
import socket
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import AUTH, CLAIM
from fastapi.testclient import TestClient
from itr_agent.control import build_control_app
from itr_agent.state import Agent
from itr_agent.terminal import (
    MAX_RESTARTS,
    Terminal,
    TerminalSettings,
    spawn,
    ttyd_argv,
)


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.exit_code: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = -15

    def kill(self) -> None:
        self.exit_code = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.exit_code or 0


class Spawner:
    def __init__(self) -> None:
        self.started: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self.fail = False

    def __call__(self, argv: list[str], settings: TerminalSettings) -> FakeProcess:
        if self.fail:
            raise OSError("ttyd: not found")
        self.started.append(argv)
        self.processes.append(FakeProcess())
        return self.processes[-1]


SETTINGS = TerminalSettings(user="oncall", bind="all", cwd=Path("/srv/teapot"))


def make(http, spawner, killed=None, settings=SETTINGS) -> tuple[Terminal, list[str]]:
    reports: list[str] = []
    terminal = Terminal(
        settings,
        http,
        lambda _s: None,
        reports.append,
        start_process=spawner,
        kill_user=(killed.append if killed is not None else lambda _u: None),
    )
    terminal._other_user = lambda _user: True  # there is no oncall user here
    return terminal, reports


def test_ttyd_is_writable_limited_and_bound_safely() -> None:
    argv = ttyd_argv(SETTINGS)
    assert argv[0] == "ttyd" and argv[-2:] == ["bash", "--login"]
    assert "--writable" in argv
    assert argv[argv.index("--max-clients") + 1] == "2"
    assert argv[argv.index("--cwd") + 1] == "/srv/teapot"
    assert "--interface" not in argv  # all interfaces; the security group guards it
    laptop = ttyd_argv(TerminalSettings())
    assert laptop[laptop.index("--interface") + 1] == "lo"


def test_settings_from_the_environment() -> None:
    s = TerminalSettings.from_env({}, Path("/srv/teapot"))
    assert (s.ttyd, s.port, s.user, s.bind, s.cwd) == (
        "ttyd",
        7681,
        None,
        "lo",
        Path("/srv/teapot"),
    )
    s = TerminalSettings.from_env(
        {"ITR_TERMINAL_USER": "oncall", "ITR_TERMINAL_BIND": "all"}, Path("/x")
    )
    assert (s.user, s.bind) == ("oncall", "all")
    with pytest.raises(ValueError, match="lo or all"):
        TerminalSettings.from_env({"ITR_TERMINAL_BIND": "0.0.0.0"}, Path("/x"))


def test_start_waits_until_ttyd_answers(http) -> None:
    spawner = Spawner()
    terminal, reports = make(http, spawner)
    assert terminal.start() is True
    assert reports == ["starting", "ready"]
    assert http.urls[-1] == "http://127.0.0.1:7681/"


def test_a_ttyd_that_dies_or_never_starts_is_reported(http) -> None:
    spawner = Spawner()
    http.status = None
    terminal, reports = make(http, spawner)
    terminal.start_process = lambda argv, s: _dead(spawner, argv)
    assert terminal.start() is False
    assert reports == ["starting", "failed"]

    spawner.fail = True
    terminal, reports = make(http, spawner)
    assert terminal.start() is False
    assert reports == ["starting", "failed"]


def _dead(spawner: Spawner, argv: list[str]) -> FakeProcess:
    process = spawner(argv, SETTINGS)
    process.exit_code = 1
    return process


def test_a_killed_ttyd_comes_back_a_few_times(http) -> None:
    spawner = Spawner()
    terminal, _ = make(http, spawner)
    terminal.start()
    terminal.keep_alive()
    assert len(spawner.started) == 1  # still running: nothing to do
    for _ in range(MAX_RESTARTS + 2):
        spawner.processes[-1].exit_code = 137  # the player ran `sudo pkill ttyd`
        terminal.keep_alive()
    assert len(spawner.started) == 1 + MAX_RESTARTS


def test_stop_closes_everything_once_and_for_good(http) -> None:
    spawner, killed = Spawner(), []
    terminal, reports = make(http, spawner, killed)
    terminal.start()
    terminal.stop()
    terminal.stop()
    assert spawner.processes[0].terminated
    assert killed == ["oncall"]  # background jobs end with the game
    assert reports[-1] == "stopped" and reports.count("stopped") == 1
    assert terminal.start() is False  # never again in this game
    terminal.keep_alive()
    assert len(spawner.started) == 1


def test_a_laptop_never_kills_its_own_user(http) -> None:
    killed: list[str] = []
    terminal = Terminal(
        TerminalSettings(user=_me()),
        http,
        lambda _s: None,
        start_process=Spawner(),
        kill_user=killed.append,
    )
    terminal.start()
    terminal.stop()
    assert killed == []


def _me() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


# ----- in the agent --------------------------------------------------------------


@pytest.fixture
def spawner() -> Spawner:
    return Spawner()


@pytest.fixture
def junior_agent(world, runner, prometheus, clock, http, spawner) -> Agent:
    agent = Agent(
        world=world,
        runner=runner,
        prometheus=prometheus,
        clock=clock,
        http=http,
        grafana_per_game=False,
        sleep=lambda _s: None,
        terminal_settings=SETTINGS,
    )
    agent.terminal.start_process = spawner
    agent.terminal.kill_user = lambda _user: None
    return agent


def junior(push: dict) -> dict:
    push = copy.deepcopy(push)
    push["runbook"]["level"] = "junior"
    return push


def load(agent: Agent, push: dict) -> None:
    agent.claim(CLAIM["game_id"], CLAIM["token"], CLAIM["public_base_url"])
    agent.load_runbook(push["game_id"], 2400, push["runbook"])


def test_a_junior_lab_gets_a_terminal_before_the_fault(
    junior_agent, push, runner, spawner
) -> None:
    started_after: list[int] = []
    real = junior_agent.terminal.start_process

    def spy(argv, settings):
        started_after.append(len(runner.calls))  # fault commands run so far
        return real(argv, settings)

    junior_agent.terminal.start_process = spy
    load(junior_agent, junior(push))
    assert started_after == [0]
    assert len(runner.calls) > 0  # the fault ran after it
    assert junior_agent.components["terminal"] == "ready"


def test_an_intern_lab_has_no_terminal(junior_agent, push, spawner) -> None:
    load(junior_agent, push)
    assert spawner.started == []
    assert "terminal" not in junior_agent.components


def test_the_terminal_closes_when_the_game_ends(junior_agent, push, spawner) -> None:
    load(junior_agent, junior(push))
    junior_agent.finish("gave_up", [("gave_up", {})])
    assert spawner.processes[0].terminated
    assert junior_agent.components["terminal"] == "stopped"


def test_a_failing_fault_closes_the_terminal(junior_agent, push, runner, spawner):
    runner.exit_code = 1
    with pytest.raises(Exception, match="failed"):
        load(junior_agent, junior(push))
    assert spawner.processes[0].terminated


def test_the_loop_restarts_a_killed_terminal(
    junior_agent, push, spawner, prometheus
) -> None:
    load(junior_agent, junior(push))
    spawner.processes[0].exit_code = 137
    junior_agent.tick()
    assert len(spawner.started) == 2


def test_the_runbook_endpoint_reports_the_terminal(
    world, runner, prometheus, clock, http, spawner, push
) -> None:
    agent = Agent(
        world=world,
        runner=runner,
        prometheus=prometheus,
        clock=clock,
        http=http,
        grafana_per_game=False,
        sleep=lambda _s: None,
        terminal_settings=SETTINGS,
    )
    agent.terminal.start_process = spawner
    control = TestClient(build_control_app(agent))
    assert control.post("/control/v1/claim", json=CLAIM).status_code == 204
    assert (
        control.put("/control/v1/runbook", json=junior(push), headers=AUTH).status_code
        == 202
    )
    assert control.get("/control/v1/ready").json()["components"]["terminal"] == "ready"


# ----- the real ttyd (skipped where it isn't installed, e.g. CI) --------------------


@pytest.mark.skipif(shutil.which("ttyd") is None, reason="ttyd isn't installed")
def test_real_ttyd_starts_and_stops(tmp_path) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = TerminalSettings(port=port, cwd=tmp_path)

    def status(url: str, method: str = "GET") -> int | None:
        try:
            with urllib.request.urlopen(url, timeout=1) as answer:
                return answer.status
        except OSError:
            return None

    terminal = Terminal(settings, status, time.sleep, start_process=spawn)
    assert terminal.start() is True
    process = terminal.process
    terminal.stop()
    assert process.poll() is not None
    assert status(f"http://127.0.0.1:{port}/") is None

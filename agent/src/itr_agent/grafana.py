"""Grafana per game (contract 2, claim; contract 5, paths).

Players reach Grafana through the gateway at <public_base_url>/s/<game_id>/grafana/, path
unchanged, so Grafana must know that address: its root_url and serve-from-sub-path. The
world's Compose file reads both from GRAFANA_ROOT_URL and GRAFANA_SUB_PATH in its `.env`.
At claim the agent writes them and recreates the Grafana container, in the background:
the gateway gives a claim 3 seconds, a Grafana restart takes 20-40. The website shows
"Dashboards are starting" until Grafana's health check answers through the gateway.
"""

import logging
from collections.abc import Callable
from urllib.parse import urlsplit

from itr_agent.verbs import CommandRunner, write_env_value
from itr_agent.world import World

log = logging.getLogger(__name__)
COMPOSE_TIMEOUT_S = 120
POLL_S = 2.0
POLLS = 60  # 2 minutes, then the dashboards stay "starting" and it is logged


def root_url(public_base_url: str, game_id: str) -> str:
    return f"{public_base_url.rstrip('/')}/s/{game_id}/grafana/"


class GrafanaForGame:
    def __init__(
        self,
        world: World,
        runner: CommandRunner,
        http: Callable[..., int | None],
        sleep: Callable[[float], None],
        report: Callable[[str], None],
    ) -> None:
        self.world, self.runner, self.http = world, runner, http
        self.sleep, self.report = sleep, report

    def start(self, public_base_url: str, game_id: str) -> bool:
        """Point Grafana at the game's address and wait until it answers there."""
        url = root_url(public_base_url, game_id)
        self.report("starting")
        try:
            write_env_value(self.world.versions_path, "GRAFANA_ROOT_URL", url)
            write_env_value(self.world.versions_path, "GRAFANA_SUB_PATH", "true")
        except OSError as error:
            log.error("grafana: can't write %s: %s", self.world.versions_path, error)
            self.report("failed")
            return False
        argv = ["docker", "compose", "-p", self.world.project, "up", "-d", "grafana"]
        done = self.runner.run(argv, self.world.compose_dir, COMPOSE_TIMEOUT_S)
        if done.exit_code != 0:
            log.error(
                "grafana: compose up failed (%s): %s",
                done.exit_code,
                done.output[-500:],
            )
            self.report("failed")
            return False
        health = (
            f"http://127.0.0.1:{self.world.grafana_port}{urlsplit(url).path}api/health"
        )
        for _ in range(POLLS):
            if self.http(health) == 200:
                log.info("grafana: ready at %s", url)
                self.report("ready")
                return True
            self.sleep(POLL_S)
        log.error("grafana: not healthy at %s after %d s", health, int(POLLS * POLL_S))
        self.report("failed")
        return False

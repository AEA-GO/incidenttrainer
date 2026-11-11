"""Start the agent: two web servers in one process, sharing one Agent in memory.

Settings come from environment variables (systemd sets them on the game server):
  ITR_AGENT_HOST        default 0.0.0.0 (the security group lets only the gateway in)
  ITR_PLAYER_PORT       default 8080
  ITR_CONTROL_PORT      default 8081
  ITR_WORLD_MANIFEST    default /opt/itr/world.yaml
  ITR_COMPOSE_DIR       optional; overrides the manifest's compose.dir (laptops)
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import uvicorn

from itr_agent.control import build_control_app
from itr_agent.player import build_player_app
from itr_agent.state import Agent
from itr_agent.verbs import SubprocessRunner
from itr_agent.world import load_world


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the game id on every line."""

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self.agent = agent

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                "level": record.levelname,
                "logger": record.name,
                "game_id": self.agent.game_id,
                "message": record.getMessage(),
            }
        )


def setup_logging(agent: Agent) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(agent))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


async def serve(agent: Agent, host: str, player_port: int, control_port: int) -> None:
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                build_player_app(agent), host=host, port=player_port, log_config=None
            )
        ),
        uvicorn.Server(
            uvicorn.Config(
                build_control_app(agent), host=host, port=control_port, log_config=None
            )
        ),
    ]
    await asyncio.gather(*(server.serve() for server in servers))


def run() -> None:
    compose_dir = os.environ.get("ITR_COMPOSE_DIR")
    world = load_world(
        Path(os.environ.get("ITR_WORLD_MANIFEST", "/opt/itr/world.yaml")),
        Path(compose_dir) if compose_dir else None,
    )
    agent = Agent(world=world, runner=SubprocessRunner())
    setup_logging(agent)
    logging.getLogger(__name__).info(
        "agent starting: world %s %s, compose dir %s",
        world.name,
        world.version,
        world.compose_dir,
    )
    asyncio.run(
        serve(
            agent,
            host=os.environ.get("ITR_AGENT_HOST", "0.0.0.0"),
            player_port=int(os.environ.get("ITR_PLAYER_PORT", "8080")),
            control_port=int(os.environ.get("ITR_CONTROL_PORT", "8081")),
        )
    )

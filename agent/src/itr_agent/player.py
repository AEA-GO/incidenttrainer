"""Player API, port 8080: reached only through the gateway (contract 2, /v1/*).

The gateway strips /s/<game_id>/api and adds X-Itr-Game-Id from the checked cookie.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Header, Query

from itr_agent.errors import AgentError, install_error_handlers
from itr_agent.state import Agent
from itr_agent.views import scene_view, state_view


def build_player_app(agent: Agent) -> FastAPI:
    app = FastAPI(
        title="itr-agent player", docs_url=None, redoc_url=None, openapi_url=None
    )
    install_error_handlers(app)

    def same_game_started(x_itr_game_id: Annotated[str, Header()]) -> None:
        """The right game (checked first), and its runbook is loaded."""
        agent.require_game(x_itr_game_id)
        if not agent.runbook_loaded:
            raise AgentError(409, "not_ready", "The game hasn't started yet.")

    guards = [Depends(same_game_started)]

    @app.get("/v1/scene", dependencies=guards)
    def scene() -> dict:
        return scene_view(agent)

    @app.get("/v1/state", dependencies=guards)
    def state(feed_after: Annotated[int, Query(ge=0)] = 0) -> dict:
        return state_view(agent, feed_after)

    @app.post("/v1/ack", dependencies=guards)
    def ack() -> dict:
        return {"t": agent.acknowledge()}

    return app

"""Player API, port 8080: reached only through the gateway (contract 2, /v1/*).

The gateway strips /s/<game_id>/api and adds X-Itr-Game-Id from the checked cookie.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, Header

from itr_agent.errors import AgentError, install_error_handlers
from itr_agent.state import Agent


def build_player_app(agent: Agent) -> FastAPI:
    app = FastAPI(
        title="itr-agent player", docs_url=None, redoc_url=None, openapi_url=None
    )
    install_error_handlers(app)

    def same_game(x_itr_game_id: Annotated[str, Header()]) -> None:
        agent.require_game(x_itr_game_id)

    @app.get("/v1/state", dependencies=[Depends(same_game)])
    def state() -> dict:
        # Step 3 loads the runbook; until then there is nothing to show.
        raise AgentError(409, "not_ready", "The game hasn't started yet.")

    return app

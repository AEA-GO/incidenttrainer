"""Player API, port 8080: reached only through the gateway (contract 2, /v1/*).

The gateway strips /s/<game_id>/api and adds X-Itr-Game-Id from the checked cookie.
"""

from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Query
from pydantic import BaseModel, ConfigDict, Field

from itr_agent.errors import AgentError, install_error_handlers
from itr_agent.state import Agent
from itr_agent.views import scene_view, state_view


class GiveUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True]


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(max_length=40)
    input: dict[str, str] = Field(default_factory=dict)


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

    @app.post("/v1/actions", dependencies=guards)
    def actions(
        body: ActionRequest,
        idempotency_key: Annotated[str, Header(pattern=r"^[A-Za-z0-9_-]{8,64}$")],
    ) -> dict:
        """Press a button. Repeating the same Idempotency-Key never runs it twice."""
        return agent.press(body.action_id, body.input, idempotency_key)

    @app.post("/v1/resolve", dependencies=guards)
    def resolve() -> dict:
        """Run the success checks. Failed: what's still wrong. Passed: the watch starts."""
        return agent.resolve()

    @app.post("/v1/give-up", dependencies=guards)
    def give_up(body: GiveUpRequest) -> dict:
        return agent.give_up()

    return app

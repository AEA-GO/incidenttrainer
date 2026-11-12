"""Control API, port 8081: only gateway-auth calls it (contract 2, /control/v1/*).

There is deliberately no endpoint that returns the runbook.
"""

from typing import Annotated, Literal

from fastapi import FastAPI, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from itr_agent import __version__
from itr_agent.errors import install_error_handlers
from itr_agent.state import Agent

GAME_ID = r"^[A-Za-z0-9_-]{22}$"


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str = Field(pattern=GAME_ID)
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    # https in AWS; http://localhost only for the laptop setup.
    public_base_url: str = Field(
        pattern=r"^(https://[a-z0-9.-]+|http://localhost(:[0-9]{2,5})?)$"
    )


class EndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["abandoned"]


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_limit_s: int = Field(ge=300, le=2400)


class RunbookPush(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str = Field(pattern=GAME_ID)
    lab_id: str
    lab_version: int = Field(ge=1)
    limits: Limits
    runbook: dict


def build_control_app(agent: Agent) -> FastAPI:
    app = FastAPI(
        title="itr-agent control", docs_url=None, redoc_url=None, openapi_url=None
    )
    install_error_handlers(app)

    @app.get("/control/v1/ready")
    def ready() -> dict:
        return {
            "booted": agent.booted,
            "claimed": agent.claimed,
            "runbook_loaded": agent.runbook_loaded,
            "agent_version": __version__,
            "world": {"name": agent.world.name, "version": agent.world.version},
            "components": agent.components,
        }

    @app.post("/control/v1/claim", status_code=204)
    def claim(body: ClaimRequest) -> Response:
        # A later step adds: start Grafana with
        # root_url <public_base_url>/s/<game_id>/grafana/ and wait for it.
        agent.claim(body.game_id, body.token, body.public_base_url)
        return Response(status_code=204)

    @app.put("/control/v1/runbook", status_code=202)
    def runbook(
        body: RunbookPush,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict:
        agent.require_claimed()
        agent.require_token(authorization)
        agent.load_runbook(body.game_id, body.limits.time_limit_s, body.runbook)
        return {"state": agent.state}

    @app.get("/control/v1/report")
    def report(
        authorization: Annotated[str | None, Header()] = None,
        after_seq: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 500,
    ) -> dict:
        agent.require_token(authorization)
        return {
            "game_id": agent.game_id,
            "state": agent.state,
            "severity": agent.severity,
            "reopened": agent.reopened,
            "end_reason": agent.end_reason,
            "last_seq": agent.events.last_seq,
            "events": [e.to_dict() for e in agent.events.since(after_seq, limit)],
        }

    @app.post("/control/v1/end")
    def end(
        body: EndRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict:
        """The gateway saw no browser activity for 5 minutes (it wrote heartbeat_lost)."""
        agent.require_token(authorization)
        return agent.abandon()

    return app

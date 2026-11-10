"""Control API, port 8081: only gateway-auth calls it (contract 2, /control/v1/*).

There is deliberately no endpoint that returns the runbook.
"""

from typing import Annotated

from fastapi import FastAPI, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from itr_agent import __version__
from itr_agent.errors import install_error_handlers
from itr_agent.state import Agent


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: str = Field(pattern=r"^[A-Za-z0-9_-]{22}$")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    # https in AWS; http://localhost only for the laptop setup.
    public_base_url: str = Field(
        pattern=r"^(https://[a-z0-9.-]+|http://localhost(:[0-9]{2,5})?)$"
    )


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
            "runbook_loaded": False,  # step 3
            "agent_version": __version__,
            "world": {"name": agent.world_name, "version": agent.world_version},
            "components": agent.components,
        }

    @app.post("/control/v1/claim", status_code=204)
    def claim(body: ClaimRequest) -> Response:
        # Step 4 adds: start Grafana with root_url <public_base_url>/s/<game_id>/grafana/ and wait for it.
        agent.claim(body.game_id, body.token, body.public_base_url)
        return Response(status_code=204)

    @app.get("/control/v1/report")
    def report(
        authorization: Annotated[str | None, Header()] = None,
        after_seq: int = 0,
    ) -> dict:
        agent.require_token(authorization)
        return {
            "game_id": agent.game_id,
            "state": agent.state,
            "severity": "SEV2",
            "reopened": False,
            "last_seq": 0,  # step 3: the real event log
            "events": [],
        }

    return app

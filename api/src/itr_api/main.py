"""IncidentTrainer session API: FastAPI app served by AWS Lambda behind CloudFront /api/*.

Locally: `uv run fastapi dev src/itr_api/main.py` (needs `uv add --dev "fastapi[standard]"`), or the tests.
"""

import os
from pathlib import Path

import boto3
from fastapi import FastAPI
from mangum import Mangum

_version_file = Path(__file__).with_name("VERSION")  # written by CI at build time
VERSION = _version_file.read_text().strip() if _version_file.exists() else "dev"

app = FastAPI(
    title="IncidentTrainer API",
    version=VERSION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _table


@app.get("/api/health")
def health() -> dict:
    """Liveness plus a cheap read from DynamoDB, so the whole path is tested."""
    if not os.environ.get("TABLE_NAME"):
        db = "skipped"
    else:
        try:
            _get_table().get_item(Key={"pk": "HEALTH", "sk": "HEALTH"})
            db = "ok"
        except Exception:  # noqa: BLE001 - health must answer, whatever broke
            db = "error"
    return {
        "status": "degraded" if db == "error" else "ok",
        "version": VERSION,
        "db": db,
    }


def launcher_check() -> dict:
    """Start one real game server and delete it at once, with this function's own role.

    Proves the launcher and its IAM policy end to end (contract 4: "one real launch from
    the main environment"). Reachable only by invoking the Lambda directly with
    {"itr_admin": "launcher_check"} (needs lambda:InvokeFunction, i.e. an admin); HTTP
    requests through API Gateway never look like this. Costs well under a cent.
    """
    import secrets
    from datetime import UTC, datetime, timedelta

    from itr_api.launcher import LaunchError, ServerSpec, from_env

    launcher = from_env()
    spec = ServerSpec(
        game_id=secrets.token_urlsafe(16),
        world="teapot-basic",
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    try:
        server = launcher.start(spec)
    except LaunchError as error:
        return {"ok": False, "kind": error.kind, "error": str(error)[:500]}
    attempts = list(getattr(launcher, "last_attempts", []))
    try:
        again = launcher.start(spec)  # idempotent: must be the same server
    finally:
        launcher.stop(server.server_id)  # never leave the test server running
    return {
        "ok": again.server_id == server.server_id,
        "server_id": server.server_id,
        "instance_type": server.instance_type,
        "zone": server.zone,
        "address": server.address,
        "attempts": attempts,
        "stopped": True,
    }


# Lambda entry point (API Gateway HTTP API, payload v2).
_http = Mangum(app, lifespan="off")


def handler(event, context):
    if isinstance(event, dict) and event.get("itr_admin") == "launcher_check":
        return launcher_check()
    return _http(event, context)

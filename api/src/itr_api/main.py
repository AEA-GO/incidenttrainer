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


# Lambda entry point (API Gateway HTTP API, payload v2).
handler = Mangum(app, lifespan="off")

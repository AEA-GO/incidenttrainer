"""gateway-auth's web app, on 127.0.0.1 only. nginx is its only caller.

GET /auth     nginx `auth_request` for every /s/<game_id>/... request.
              204 + X-Itr-Upstream / X-Itr-Port / X-Itr-Game-Id: forward it.
              403 + X-Itr-Status / X-Itr-Code (/ X-Itr-Retry): refuse it; nginx then
              asks /deny for the body. (auth_request only understands 2xx, 401 and
              403, so every refusal is a 403 that carries the real status.)
GET /deny     the browser's answer for a refusal: the contract's JSON error, or the
              boot progress for `GET .../api/v1/state` while the game boots.
GET /healthz  for the gateway's own checks.
"""

import ipaddress
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from itr_gateway import errors
from itr_gateway.cookie import COOKIE_NAME, COOKIE_NAME_INSECURE
from itr_gateway.errors import GatewayError, error_body
from itr_gateway.route import PATH, BootAnswer, Router
from itr_gateway.store import Store

log = logging.getLogger("itr_gateway")

BOOT_STATE = "boot_state"  # not an error: "answer the boot screen yourself"
KNOWN = {
    kind[1]: kind
    for kind in (
        errors.NOT_FOUND,
        errors.BAD_COOKIE,
        errors.EXPIRED,
        errors.WRONG_GAME,
        errors.BAD_ORIGIN,
        errors.NO_TERMINAL,
        errors.ENDED,
        errors.TOO_FAST,
        errors.UNREACHABLE,
        errors.BOOTING,
    )
}
NO_STORE = {"Cache-Control": "no-store"}


@dataclass
class Settings:
    public_origin: str | None = (
        None  # e.g. https://drer7le45ultl.cloudfront.net (WebSocket Origin check)
    )
    sandbox_cidrs: list[str] = field(default_factory=lambda: ["10.20.0.0/20"])
    insecure_cookie: bool = False  # laptops only: cookie `itr_game` without __Secure-


def build_app(
    store: Store,
    settings: Settings,
    clock: Callable[[], float] = time.time,
    boot_progress: Callable[[str], dict] | None = None,
) -> FastAPI:
    """`boot_progress(game_id)` gives the agent's `components` while booting (the broker, step 3)."""
    router = Router(
        store=store,
        cookie_name=COOKIE_NAME_INSECURE if settings.insecure_cookie else COOKIE_NAME,
        public_origin=settings.public_origin,
        sandbox=[ipaddress.ip_network(cidr) for cidr in settings.sandbox_cidrs],
    )
    app = FastAPI(
        title="itr-gateway-auth", docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/auth")
    def auth(request: Request) -> Response:
        headers = request.headers
        uri = headers.get("x-original-uri", "")
        request_id = headers.get("x-request-id", "")
        now = clock()
        try:
            decision = router.decide(
                uri=uri,
                method=headers.get("x-original-method", "GET").upper(),
                cookie_header=headers.get("cookie"),
                origin=headers.get("origin"),
                now=now,
            )
        except GatewayError as error:
            log.info(
                "refused",
                extra={
                    "fields": {
                        "code": error.code,
                        "uri": _path_only(uri),
                        "request_id": request_id,
                    }
                },
            )
            return _refuse(error.status, error.code, error.retry_after_s)
        store.heartbeat(decision.game.game_id, now)
        if isinstance(decision, BootAnswer):
            return _refuse(200, BOOT_STATE)
        return Response(
            status_code=204,
            headers={
                "X-Itr-Upstream": decision.upstream,
                "X-Itr-Port": str(decision.port),
                "X-Itr-Game-Id": decision.game.game_id,
            },
        )

    @app.get("/deny")
    def deny(request: Request) -> Response:
        headers = request.headers
        code = headers.get("x-itr-code", "")
        if code == BOOT_STATE:
            return _boot_state(headers.get("x-original-uri", ""))
        if code not in KNOWN:
            # nginx sends here only after /auth refused; anything else is our bug
            log.error(
                "deny without a known code", extra={"fields": {"code": code[:40]}}
            )
            return JSONResponse(
                error_body("internal", "Something went wrong."),
                status_code=500,
                headers=NO_STORE,
            )
        status, code, message = KNOWN[code]
        retry = headers.get("x-itr-retry", "")
        retry_after_s = int(retry) if retry.isdigit() else None
        extra = {"Retry-After": str(retry_after_s)} if retry_after_s is not None else {}
        return JSONResponse(
            error_body(code, message, retry_after_s),
            status_code=status,
            headers=NO_STORE | extra,
        )

    def _boot_state(uri: str) -> Response:
        match = PATH.match(uri)
        game = store.game(match.group(1)) if match else None
        if game is None or game.state != "booting":
            # It moved on between /auth and here: the browser simply polls again.
            return JSONResponse(
                error_body("booting", "Your server is still starting.", 1),
                status_code=503,
                headers=NO_STORE | {"Retry-After": "1"},
            )
        seconds = int(clock() - game.created_at) if game.created_at else 0
        boot = {
            "server_started": bool(game.instance_id),
            "agent_ready": False,
            "seconds_since_start": max(seconds, 0),
            "components": boot_progress(game.game_id) if boot_progress else {},
        }
        return JSONResponse({"state": "booting", "boot": boot}, headers=NO_STORE)

    return app


def _refuse(status: int, code: str, retry_after_s: int | None = None) -> Response:
    headers = {"X-Itr-Status": str(status), "X-Itr-Code": code}
    if retry_after_s is not None:
        headers["X-Itr-Retry"] = str(retry_after_s)
    return Response(status_code=403, headers=headers)


def _path_only(uri: str) -> str:
    return uri.split("?", 1)[0][:200]


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line; `extra={"fields": {...}}` adds keys (request_id, code, ...)."""

    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        line |= getattr(record, "fields", {})
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)

"""One error shape for every response: {"error": {"code", "message", "retry_after_s"?}}.

Same shape as the session API (contract 3) and the gateway (contract 5).
`message` is always safe to show to the player.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AgentError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retry_after_s: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after_s = retry_after_s


def error_body(code: str, message: str, retry_after_s: int | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if retry_after_s is not None:
        error["retry_after_s"] = retry_after_s
    return {"error": error}


async def _agent_error(_: Request, exc: AgentError) -> JSONResponse:
    headers = {}
    if exc.retry_after_s is not None:
        headers["Retry-After"] = str(exc.retry_after_s)
    return JSONResponse(
        error_body(exc.code, exc.message, exc.retry_after_s),
        status_code=exc.status,
        headers=headers,
    )


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(part) for part in first.get("loc", []))
    detail = first.get("msg", "invalid request")
    return JSONResponse(
        error_body("bad_request", f"{where}: {detail}" if where else detail),
        status_code=400,
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AgentError, _agent_error)
    app.add_exception_handler(RequestValidationError, _validation_error)

"""The one error shape of contract 3: {"error": {"code", "message", "retry_after_s"?}}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

NO_STORE = {"Cache-Control": "no-store"}

# code -> (HTTP status, message safe to show the player)
KNOWN = {
    "bad_request": (400, "Something in the request isn't right."),
    "bad_code": (401, "That invite code doesn't work."),
    "code_disabled": (403, "This invite code has been switched off."),
    "not_found": (404, "Not found."),
    "out_of_games": (429, "This invite code has no games left."),
    "daily_limit": (429, "That's 3 games today. Come back tomorrow."),
    "all_servers_busy": (503, "All game servers are busy. Try again in a few minutes."),
    "budget_exhausted": (503, "We've used this month's games. Try again next month."),
    "start_failed": (
        503,
        "Your server didn't start. The game wasn't counted; try again.",
    ),
    "not_ended": (409, "The game hasn't ended yet."),
    "not_scored_yet": (409, "The game hasn't been scored yet."),
    "internal": (500, "Something went wrong on our side."),
}


class ApiError(Exception):
    def __init__(
        self, code: str, retry_after_s: int | None = None, message: str | None = None
    ):
        super().__init__(code)
        self.code = code
        self.status, default = KNOWN[code]
        self.message = message or default
        self.retry_after_s = retry_after_s


def error_response(error: ApiError) -> JSONResponse:
    body: dict = {"code": error.code, "message": error.message}
    headers = dict(NO_STORE)
    if error.retry_after_s is not None:
        body["retry_after_s"] = error.retry_after_s
        headers["Retry-After"] = str(error.retry_after_s)
    return JSONResponse({"error": body}, status_code=error.status, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, error: ApiError) -> JSONResponse:
        return error_response(error)

    @app.exception_handler(RequestValidationError)
    async def _invalid(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return error_response(ApiError("bad_request"))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        if error.status_code == 404:
            return error_response(ApiError("not_found"))
        if error.status_code < 500:
            return error_response(
                ApiError("bad_request", message=str(error.detail)[:200])
            )
        return error_response(ApiError("internal"))

"""One error shape for every response: {"error": {"code", "message", "retry_after_s"?}}.

Same shape as the session API (contract 3) and the agent (contract 2).
`message` is always safe to show to the player.
"""


class GatewayError(Exception):
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


# The gateway's errors (contract 5, "Errors from the gateway"): status, code, message.
NOT_FOUND = (404, "not_found", "There is no such game or page.")
BAD_COOKIE = (
    401,
    "bad_cookie",
    "Your game session is missing. Start the game again from the lab page.",
)
EXPIRED = (401, "expired", "This game session has expired.")
WRONG_GAME = (403, "wrong_game", "This tab belongs to another game.")
BAD_ORIGIN = (403, "bad_origin", "The terminal only opens from IncidentTrainer itself.")
NO_TERMINAL = (404, "no_terminal", "This lab has no terminal.")
ENDED = (410, "ended", "This game is over.")
TOO_FAST = (429, "too_fast", "Too many requests. Slow down a little.")
UNREACHABLE = (502, "server_unreachable", "Your game server did not answer.")
BOOTING = (503, "booting", "Your server is still starting.")


def gateway_error(
    kind: tuple[int, str, str], retry_after_s: int | None = None
) -> GatewayError:
    status, code, message = kind
    return GatewayError(status, code, message, retry_after_s)

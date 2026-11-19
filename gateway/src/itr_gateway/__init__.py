"""gateway-auth: checks the game cookie and tells nginx where /s/<game_id>/* goes (contract 5).

Settings (environment variables; systemd sets them on the gateway server):
  ITR_TABLE            DynamoDB table (itr-main). Games, cookie keys, heartbeats.
  ITR_DEV_GAMES        laptop instead of ITR_TABLE: JSON file {"games": {"<id>": {META}}}
  ITR_COOKIE_KEYS      laptop: the CONFIG/COOKIE_KEYS item as JSON (overrides the table's)
  ITR_PUBLIC_ORIGIN    e.g. https://drer7le45ultl.cloudfront.net (terminal WebSocket check)
  ITR_SANDBOX_CIDRS    default 10.20.0.0/20; comma-separated. Never forward anywhere else.
  ITR_COOKIE_INSECURE  1 = laptop cookie `itr_game` (no __Secure- prefix)
  ITR_AUTH_HOST        default 127.0.0.1 (only nginx talks to it)
  ITR_AUTH_PORT        default 9000
"""

import logging
import os
import sys


def settings_from_env(env: dict[str, str]):
    from itr_gateway.app import Settings

    cidrs = [
        part.strip()
        for part in env.get("ITR_SANDBOX_CIDRS", "10.20.0.0/20").split(",")
        if part.strip()
    ]
    return Settings(
        public_origin=env.get("ITR_PUBLIC_ORIGIN") or None,
        sandbox_cidrs=cidrs,
        insecure_cookie=env.get("ITR_COOKIE_INSECURE") == "1",
    )


def setup_logging() -> None:
    from itr_gateway.app import JsonLogFormatter

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def main() -> None:
    import uvicorn

    from itr_gateway.app import build_app
    from itr_gateway.store import store_from_env

    setup_logging()
    env = dict(os.environ)
    app = build_app(store_from_env(env), settings_from_env(env))
    uvicorn.run(
        app,
        host=env.get("ITR_AUTH_HOST", "127.0.0.1"),
        port=int(env.get("ITR_AUTH_PORT", "9000")),
        access_log=False,  # nginx logs every request with its request id
        log_config=None,
    )

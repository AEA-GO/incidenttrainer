"""Contract 3, the game endpoints: invite check, Start shift, game status.

The API never talks to game servers: it writes the game, starts the server through the
launcher (contract 4) and signs the cookie (contract 5); the gateway's broker boots it.
"""

import json
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from itr_api import codes
from itr_api.cookie import LIFETIME_S, set_cookie, sign
from itr_api.errors import NO_STORE, ApiError
from itr_api.games import GameStore, NewGame, Refused, seconds_to_kyiv_midnight
from itr_api.labs import Labs
from itr_api.launcher import Launcher, LaunchError, ServerSpec

router = APIRouter(prefix="/api")

GAME_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
BUSY_RETRY_S = 600  # contract 3: all_servers_busy, "about 10 min"
START_RETRY_S = 30


def log(event: str, **fields) -> None:
    """One JSON line per event in CloudWatch, always with the game id when there is one."""
    print(json.dumps({"event": event} | fields, default=str))


@dataclass
class Deps:
    store: GameStore
    labs: Labs
    launcher: Launcher
    clock: Callable[[], float] = field(default=time.time)


_deps: Deps | None = None


def get_deps() -> Deps:
    """Built once per Lambda container from the environment (tests override this)."""
    global _deps
    if _deps is None:
        import boto3

        from itr_api.labs import S3Labs
        from itr_api.launcher import from_env

        table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
        _deps = Deps(
            store=GameStore(table),
            labs=S3Labs(os.environ["ITR_LABS_BUCKET"]),
            launcher=from_env(),
        )
    return _deps


DepsArg = Annotated[Deps, Depends(get_deps)]
InviteCode = Annotated[str | None, Header()]  # X-Invite-Code (contract 3)


# ----- who is asking ---------------------------------------------------------------------
def require_code(deps: Deps, header: str | None) -> tuple[str, dict]:
    code = codes.normalise(header)
    item = deps.store.code(code) if code else None
    if item is None:
        raise ApiError("bad_code")
    if not item.get("active", False):
        raise ApiError("code_disabled")
    return code, item


def invite_view(deps: Deps, code: str, item: dict) -> dict:
    limits = deps.store.limits()
    total = int(item.get("games_total", limits["games_per_code"]))
    left = max(total - int(item.get("games_used", 0)), 0)
    today = deps.store.games_today(code, deps.clock())
    return {
        "games_total": total,
        "games_left": left,
        "games_left_today": max(min(int(limits["games_per_day"]) - today, left), 0),
    }


def valid_nickname(text: str) -> str:
    name = " ".join(text.split())
    ok = 2 <= len(name) <= 24 and all(
        c.isalpha() or c.isdigit() or c in " _.-" for c in name
    )
    if not ok:
        raise ApiError(
            "bad_request",
            message="Nickname: 2-24 letters, digits, spaces, _ . or -.",
        )
    return name


# ----- endpoints -------------------------------------------------------------------------
@router.get("/invite")
def invite(
    response: Response,
    deps: DepsArg,
    x_invite_code: InviteCode = None,
) -> dict:
    code, item = require_code(deps, x_invite_code)
    response.headers.update(NO_STORE)
    return invite_view(deps, code, item)


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lab_id: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    nickname: str = Field(max_length=100)


@router.post("/sessions", status_code=201)
def start_shift(
    body: StartBody,
    response: Response,
    deps: DepsArg,
    x_invite_code: InviteCode = None,
) -> dict:
    code, _ = require_code(deps, x_invite_code)
    nickname = valid_nickname(body.nickname)
    lab = deps.labs.card(body.lab_id)
    if lab is None:
        raise ApiError("not_found")
    key = deps.store.cookie_key()
    if key is None:
        log("no_cookie_keys")  # run gateway/scripts/cookie_keys.py add
        raise ApiError("internal")
    limits = deps.store.limits()
    now = deps.clock()
    game = NewGame(
        game_id=secrets.token_urlsafe(16),  # 22 characters, 128 bits
        code=code,
        lab=lab,
        nickname=nickname,
        now=now,
    )

    try:
        deps.store.begin(game, limits)
    except Refused as refused:
        log("start_refused", code_hint=code[:2], reason=refused.which)
        if refused.which == "code":
            fresh = deps.store.code(code) or {}
            raise ApiError(
                "code_disabled" if not fresh.get("active") else "out_of_games"
            ) from None
        if refused.which == "day":
            raise ApiError("daily_limit", seconds_to_kyiv_midnight(now)) from None
        if refused.which == "running":
            raise ApiError("all_servers_busy", BUSY_RETRY_S) from None
        raise ApiError("budget_exhausted") from None
    deps.store.event(
        game.game_id,
        "game_created",
        {"lab_id": lab.id, "lab_version": f"v{lab.version}", "level": lab.level},
    )
    log("game_created", game_id=game.game_id, lab_id=lab.id, lab_version=lab.version)

    spec = ServerSpec(
        game_id=game.game_id,
        world=lab.world,
        expires_at=datetime.fromtimestamp(now, UTC) + timedelta(seconds=LIFETIME_S),
    )
    try:
        server = deps.launcher.start(spec)
        if not server.address:
            raise LaunchError("unknown", "the server has no private address", True)
        expires_at_server = deps.store.server_started(game.game_id, server)
    except Exception as error:  # noqa: BLE001 - any failure here must give the game back
        _give_back(deps, game, error)
        raise ApiError("start_failed", START_RETRY_S) from None
    deps.store.event(
        game.game_id, "server_started", {"instance_type": server.instance_type}
    )
    log(
        "server_started",
        game_id=game.game_id,
        server_id=server.server_id,
        instance_type=server.instance_type,
        zone=server.zone,
        attempts=getattr(deps.launcher, "last_attempts", None),
    )

    exp = min(int(now) + LIFETIME_S, expires_at_server)
    kid, secret = key
    response.headers["Set-Cookie"] = set_cookie(
        sign(secret, game.game_id, exp, kid), game.game_id, max(exp - int(now), 0)
    )
    response.headers.update(NO_STORE)
    return {
        "game_id": game.game_id,
        "state": "booting",
        "game_path": f"/s/{game.game_id}/",
        "expires_at": datetime.fromtimestamp(expires_at_server, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


def _give_back(deps: Deps, game: NewGame, error: Exception) -> None:
    """The start failed: delete any server it left, end the game `failed`, give the game back."""
    kind = error.kind if isinstance(error, LaunchError) else "unknown"
    log("start_failed", game_id=game.game_id, kind=kind, error=str(error)[:500])
    try:
        for server in deps.launcher.list():
            if server.game_id == game.game_id:
                deps.launcher.stop(server.server_id)
    except Exception as cleanup_error:  # noqa: BLE001 - the cleanup Lambda catches what's left
        log(
            "start_failed_cleanup", game_id=game.game_id, error=str(cleanup_error)[:300]
        )
    if deps.store.fail(game, f"{kind}: {error}"):
        deps.store.event(game.game_id, "boot_failed", {"reason": "launch_error"})
        deps.store.event(
            game.game_id,
            "ended",
            {
                "reason": "failed",
                "final_health": None,
                "severity": None,
                "reopened": False,
            },
        )


@router.get("/sessions/{game_id}")
def game_status(
    game_id: str,
    response: Response,
    deps: DepsArg,
    x_invite_code: InviteCode = None,
) -> dict:
    if not GAME_ID.match(game_id):
        raise ApiError("not_found")
    code = codes.normalise(x_invite_code)
    if code is None:
        raise ApiError("bad_code")
    meta = deps.store.meta(game_id)
    if meta is None:
        raise ApiError("not_found")
    if meta.get("code") != code:
        raise ApiError("bad_code")  # not the code that started this game
    state = str(meta.get("state"))
    created = meta.get("created_at")
    seconds = 0
    if created:
        started = datetime.fromisoformat(str(created)).timestamp()
        seconds = max(int(deps.clock() - started), 0)
    failed = meta.get("end_reason") == "failed"
    body = {
        "game_id": game_id,
        "lab_id": meta.get("lab_id"),
        "state": state,
        "boot": {
            "server_started": bool(meta.get("instance_id")),
            "agent_ready": state != "booting" and not failed,
            "seconds_since_start": seconds,
        },
        "scored": bool(meta.get("scored_at")),
    }
    if meta.get("end_reason"):
        body["end_reason"] = meta["end_reason"]
    response.headers.update(NO_STORE)
    return body

"""Cleanup: the second safety net, every 5 minutes (EventBridge Scheduler invokes the API
Lambda with {"itr_cleanup": true}). The first net is each server's own 45-minute timer.

Each run, in this order:
1. Active games: a game still booting long after its limit ends `failed` (the game is
   given back); a game nobody ended 50 minutes after it started ends `abandoned`.
2. Game servers: an ended game's server is finished (scored, deleted) once the browser had
   a minute to do it; a server past its 45 minutes is deleted (alert: the timer failed); a
   server with no game, or a second server for one game, is deleted (alert).
3. The running-games counter is set to the number of active games if it drifted.

Every step can run twice safely: ending is conditional, Finish is idempotent.
"""

from collections.abc import Callable
from datetime import datetime

from itr_api.cookie import LIFETIME_S
from itr_api.sessions import Deps, delete_server, finish_game, log

BOOT_GRACE_S = 60  # on top of CONFIG/LIMITS boot_timeout_s (180)
YOUNG_SERVER_S = 120  # a server this new may not be written onto its game yet
EXPIRED_GRACE_S = 120  # its own timer should have deleted it by then
STALE_GAME_S = LIFETIME_S + 300  # 50 minutes: no game lasts that long
FINISH_AFTER_S = 60  # the browser calls Finish itself; give it a minute


def seconds(iso: str | None) -> float | None:
    return datetime.fromisoformat(iso).timestamp() if iso else None


def last_health(deps: Deps, game_id: str) -> int | None:
    values = [
        (e.get("data") or {}).get("value")
        for e in deps.store.events(game_id)
        if e.get("type") == "health"
    ]
    return values[-1] if values else None


def end_abandoned(deps: Deps, game_id: str, meta: dict, why: str) -> bool:
    """Cleanup ends a game itself (events.md: only if the agent can't be reached)."""
    if not deps.store.end_game(
        game_id, meta["code"], meta["created_at"], "abandoned", why
    ):
        return False
    deps.store.event(
        game_id,
        "ended",
        {
            "reason": "abandoned",
            "final_health": last_health(deps, game_id),
            "severity": meta.get("severity"),
            "reopened": bool(meta.get("reopened", False)),
        },
        by="cleanup",
    )
    return True


def run(deps: Deps, alert: Callable[[str], None] = lambda _text: None) -> dict:
    now = deps.clock()
    limits = deps.store.limits()
    report = {
        "boot_failed": 0,
        "abandoned": 0,
        "finished": 0,
        "expired": 0,
        "orphans": 0,
        "running_fixed": None,
        "errors": 0,
    }

    # 1. Active games -----------------------------------------------------------------
    for game_id in deps.store.active_ids():
        try:
            meta = deps.store.meta(game_id)
            if meta is None or meta.get("state") == "ended":
                continue
            age = now - (seconds(meta.get("created_at")) or now)
            if (
                meta.get("state") == "booting"
                and age > int(limits["boot_timeout_s"]) + BOOT_GRACE_S
            ):
                delete_server(deps, game_id, meta, "cleanup", "boot_failed")
                if deps.store.end_game(
                    game_id, meta["code"], meta["created_at"], "failed", "boot timeout"
                ):
                    deps.store.event(
                        game_id, "boot_failed", {"reason": "timeout"}, by="cleanup"
                    )
                    deps.store.event(
                        game_id,
                        "ended",
                        {
                            "reason": "failed",
                            "final_health": None,
                            "severity": None,
                            "reopened": False,
                        },
                        by="cleanup",
                    )
                    report["boot_failed"] += 1
                    log("cleanup_boot_failed", game_id=game_id, age_s=int(age))
            elif age > STALE_GAME_S and end_abandoned(
                deps, game_id, meta, "not ended after 50 min"
            ):
                finish_game(
                    deps, game_id, deps.store.meta(game_id), "cleanup", "expired"
                )
                report["abandoned"] += 1
                alert(
                    f"Cleanup ended game {game_id}: still {meta.get('state')} after 50 min."
                )
        except Exception as error:  # noqa: BLE001 - one bad game must not stop the rest
            report["errors"] += 1
            log("cleanup_error", game_id=game_id, step="games", error=str(error)[:300])

    # 2. Game servers -----------------------------------------------------------------
    for server in deps.launcher.list():
        if server.state not in ("starting", "running"):
            continue
        game_id = server.game_id
        try:
            launched_age = now - server.launched_at.timestamp()
            meta = deps.store.meta(game_id) if game_id else None
            known = meta.get("instance_id") if meta else None
            if meta is None or (known and known != server.server_id):
                if launched_age < YOUNG_SERVER_S:
                    continue  # its start may still be writing it onto the game
                deps.launcher.stop(server.server_id)
                report["orphans"] += 1
                log("cleanup_orphan", game_id=game_id, server_id=server.server_id)
                alert(f"Cleanup deleted server {server.server_id}: no game uses it.")
                continue
            if meta.get("state") == "ended":
                if known is None:  # the start never wrote it (failed start)
                    deps.launcher.stop(server.server_id)
                    report["orphans"] += 1
                    continue
                ended_age = now - (seconds(meta.get("ended_at")) or 0)
                if ended_age >= FINISH_AFTER_S:
                    finish_game(deps, game_id, meta, "cleanup", "finished")
                    report["finished"] += 1
                continue
            if now > server.expires_at.timestamp() + EXPIRED_GRACE_S:
                end_abandoned(deps, game_id, meta, "server past 45 min")
                finish_game(
                    deps, game_id, deps.store.meta(game_id), "cleanup", "expired"
                )
                report["expired"] += 1
                alert(
                    f"Cleanup deleted server {server.server_id} (game {game_id}): "
                    "past 45 min, its own timer didn't stop it."
                )
        except Exception as error:  # noqa: BLE001 - one bad server must not stop the rest
            report["errors"] += 1
            log(
                "cleanup_error", game_id=game_id, step="servers", error=str(error)[:300]
            )

    # 3. The running-games counter ------------------------------------------------------
    seen = deps.store.running()
    actual = len(deps.store.active_ids())
    if seen != actual and deps.store.fix_running(seen, actual):
        report["running_fixed"] = {"from": seen, "to": actual}

    log("cleanup", **report)
    return report

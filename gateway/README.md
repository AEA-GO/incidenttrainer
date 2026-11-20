# gateway

The front door to every game: everything a player does goes through `/s/<game_id>/...`
(contract 5, `docs/contracts/game-path.md`). One small server for all games:

```
CloudFront /s/* ──► nginx :80 ──auth_request──► gateway-auth 127.0.0.1:9000
                       │                          checks the cookie, finds the game (DynamoDB)
                       └──► the game server: agent :8080, Grafana :3000, ttyd :7681 (never :8081)
```

**gateway-auth** (this Python project, `itr-gateway-auth`) answers nginx for every request:
forward it (to which private IP and port) or refuse it (status + code from contract 5).
While a game boots it answers the boot screen itself. Later (step 3) it is also the
session broker: claims the agent, pushes the runbook, copies events, ends idle games.

## Files

| Path | What |
| --- | --- |
| `src/itr_gateway/cookie.py` | the game cookie: check (and sign, for tests and laptops); contract test vector |
| `src/itr_gateway/route.py` | path → cookie → game → state: forward or refuse |
| `src/itr_gateway/store.py` | games, cookie keys and heartbeats: DynamoDB (`TableStore`) or a laptop JSON file (`FileStore`), with the contract's caching |
| `src/itr_gateway/app.py` | `/auth`, `/deny`, `/healthz` for nginx |
| `nginx/itr-gateway.conf` | the site (goes in `/etc/nginx/conf.d/`); `itr-auth.conf` and `itr-proxy-headers.conf` go in `/etc/nginx/` |

## Run the tests

```bash
cd gateway
uv run pytest
```

No AWS needed: DynamoDB is faked with moto. `tests/test_nginx.py` starts the real nginx
config with gateway-auth and fake game-server ports and probes it like a browser; it is
skipped where nginx isn't installed (CI installs it).

## A game's server is "gone" when

`GAME#<id> / META` has `server_terminated_at` (written by whoever deletes the server), or
no `private_ip`, or `expires_at_server` has passed. Then an ended game answers `410 ended`.
(`server_terminated_at` is new: add it to data-model.md in the next contracts PR.)

## Settings

See the top of `src/itr_gateway/__init__.py`. On a laptop: `ITR_DEV_GAMES` (a JSON file of
games) + `ITR_COOKIE_KEYS` instead of `ITR_TABLE`, and `ITR_COOKIE_INSECURE=1`.

## How a refusal reaches the browser

nginx's `auth_request` understands only 2xx, 401 and 403. So gateway-auth refuses with
`403` plus `X-Itr-Status` / `X-Itr-Code` / `X-Itr-Retry`, and nginx's `error_page` asks
`/deny` for the real answer (the contract's JSON error, or the boot progress).

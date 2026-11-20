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

## Play a lab through the gateway on a laptop

The same as `docs/play-a-lab.md` in the labs repo, plus the gateway in front: the player's
calls and Grafana go through nginx + gateway-auth exactly as a browser's will. The game id is
play.py's fixed dev id; the cookie key is the contract's public test key (laptops only).

**Terminal 1, the shop** (labs repo, `worlds/teapot-basic`): Grafana must live under the game's path.
```bash
sudo scripts/data-disk.sh                                   # after every reboot
export GRAFANA_ROOT_URL=http://localhost:8088/s/q3V9xYk2Lm8Ta1BcD4eF0g/grafana/ GRAFANA_SUB_PATH=true
docker compose up -d
scripts/seed-data.sh                                        # before every game
```

**Terminal 2, the agent** (platform repo, `agent/`; restart it before every game):
```bash
W=~/IncTrainer/incidenttrainer-labs/worlds/teapot-basic
ITR_WORLD_MANIFEST=$W/world.yaml ITR_COMPOSE_DIR=$W uv run itr-agent
```

**Terminal 3, gateway-auth** (platform repo, `gateway/`):
```bash
dev/run-auth.sh
```

**Terminal 4, nginx and you** (platform repo, `gateway/`):
```bash
docker compose -f dev/compose.yaml up -d                    # nginx on http://localhost:8088
export ITR_GATEWAY=http://localhost:8088
P="python3 $HOME/IncTrainer/incidenttrainer/agent/dev/play.py"
L=~/IncTrainer/incidenttrainer-labs
(cd $L && uv run --no-project --with pyyaml --with jsonschema scripts/make_push.py labs/checkout-down-3am/lab.yaml)
$P start --push $L/out/checkout-down-3am/push.json
$P state          # ... ack, press, chat, resolve, feed: all through the gateway now
$P browser        # a link: opens the game's Grafana through the gateway in your browser
```
Watch what the gateway does: `docker compose -f dev/compose.yaml logs -f` (nginx, one JSON
line per request) and terminal 3 (gateway-auth, one line per refusal).

Afterwards: `docker compose -f dev/compose.yaml down`; in terminal 1 `unset GRAFANA_ROOT_URL
GRAFANA_SUB_PATH && docker compose up -d grafana` puts Grafana back on http://127.0.0.1:3000.

`dev/games.json` holds the game as gateway-auth sees it (state `investigating`, server
127.0.0.1). Until the broker exists (step 3) that state doesn't follow the game; every
playing state routes the same way, so it doesn't matter yet.

## Settings

See the top of `src/itr_gateway/__init__.py`. On a laptop: `ITR_DEV_GAMES` (a JSON file of
games) + `ITR_COOKIE_KEYS` instead of `ITR_TABLE`, and `ITR_COOKIE_INSECURE=1`.

## How a refusal reaches the browser

nginx's `auth_request` understands only 2xx, 401 and 403. So gateway-auth refuses with
`403` plus `X-Itr-Status` / `X-Itr-Code` / `X-Itr-Retry`, and nginx's `error_page` asks
`/deny` for the real answer (the contract's JSON error, or the boot progress).

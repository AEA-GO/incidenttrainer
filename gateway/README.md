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
While a game boots it answers the boot screen itself. It is also the session broker:
claims the agent, pushes the runbook, copies events, ends idle games (below).

## Files

| Path | What |
| --- | --- |
| `src/itr_gateway/cookie.py` | the game cookie: check (and sign, for tests and laptops); contract test vector |
| `src/itr_gateway/route.py` | path → cookie → game → state: forward or refuse |
| `src/itr_gateway/store.py` | games, cookie keys and heartbeats: DynamoDB (`TableStore`) or a laptop JSON file (`FileStore`), with the contract's caching |
| `src/itr_gateway/app.py` | `/auth`, `/deny`, `/healthz` for nginx |
| `src/itr_gateway/broker.py` | the session broker: boots games, copies events, ends idle games |
| `dev/` | the laptop setup: `new_game.py`, `run-auth.sh`, nginx in `compose.yaml` |
| `scripts/` | by hand on AWS: `cookie_keys.py` (contract 5 keys), `hand_game.py` (publish a runbook, start a game) |
| `image/` | the gateway server's image (Packer), its deploy by root-disk swap; see `image/README.md` |
| `nginx/itr-gateway.conf` | the site (goes in `/etc/nginx/conf.d/`); `itr-auth.conf` and `itr-proxy-headers.conf` go in `/etc/nginx/` |

## Run the tests

```bash
cd gateway
uv run pytest
```

No AWS needed: DynamoDB is faked with moto. `tests/test_nginx.py` starts the real nginx
config with gateway-auth and fake game-server ports and probes it like a browser; it is
skipped where nginx isn't installed (CI installs it).

## Contract notes (for the next contracts PR)

- A game's server is "gone" when `GAME#<id> / META` has `server_terminated_at` (written by
  whoever deletes the server), or no `private_ip`, or `expires_at_server` has passed. Then an
  ended game answers `410 ended`. `server_terminated_at` is new in data-model.md.
- New META fields written by the broker: `heartbeat_lost_at`; `ready_at` and
  `events_synced_seq` were already listed.
- `labs/<lab>/v<version>/runbook.json` in the lab bucket is the runbook exactly as
  `make_push.py` builds it (`id`, `version`, `limits`, ...); the broker adds `game_id`,
  `lab_id`, `lab_version` and `limits` around it for `PUT /control/v1/runbook`.
- The gateway's own events use fixed `seq` numbers: `agent_ready` 1, `heartbeat_lost` 2,
  `ended` 3.

## Play a lab through the gateway on a laptop

The same as `docs/play-a-lab.md` in the labs repo, but the gateway does the gateway's job:
it boots the game (claims the agent, pushes the runbook), copies the game's events and keeps
its state, and every player call and Grafana go through nginx + gateway-auth exactly as a
browser's will. The game id is play.py's fixed dev id; the cookie key is the contract's
public test key (laptops only).

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

**Terminal 3, gateway-auth** (platform repo, `gateway/`): a new game, then the gateway.
```bash
L=~/IncTrainer/incidenttrainer-labs
(cd $L && uv run --no-project --with pyyaml --with jsonschema scripts/make_push.py labs/checkout-down-3am/lab.yaml)
python3 dev/new_game.py                                     # before every game
dev/run-auth.sh                                             # keeps running; new games need no restart
```

**Terminal 4, nginx and you** (platform repo, `gateway/`):
```bash
docker compose -f dev/compose.yaml up -d                    # nginx on http://localhost:8088
export ITR_GATEWAY=http://localhost:8088
P="python3 $HOME/IncTrainer/incidenttrainer/agent/dev/play.py"
$P state          # BOOTING, then READY: the gateway started the game (no `start` needed)
$P browser        # a link: opens the game's Grafana through the gateway in your browser
```
Then play as usual: `ack`, `press`, `chat`, `reply`, `resolve`, `feed`, all through the gateway.

What the gateway wrote, as the API will read it from DynamoDB:
```bash
python3 -m json.tool dev/.games.json                        # META: state, ready_at, ended_at, events_synced_seq...
cat dev/.games.json.events/*.jsonl                          # the game diary (agent + gateway events)
```
Watch it work: terminal 3 logs "game ready" / "game ended"; `docker compose -f dev/compose.yaml
logs -f` shows nginx's one JSON line per request.

Next game: Ctrl+C the agent, `scripts/seed-data.sh` in terminal 1, start the agent again,
`python3 dev/new_game.py` in another terminal (gateway-auth picks the new file up by itself).

Afterwards: `docker compose -f dev/compose.yaml down`; in terminal 1 `unset GRAFANA_ROOT_URL
GRAFANA_SUB_PATH && docker compose up -d grafana` puts Grafana back on http://127.0.0.1:3000.

## A game on AWS by hand (until the API's "Start shift" exists)

The real gateway, a real game server, lab 1, played with `play.py` through CloudFront.
Run from the platform repo's `gateway/` folder, signed in with `aws login`:

```bash
uv run scripts/cookie_keys.py add               # once: the first cookie key (secrets never printed)
L=~/IncTrainer/incidenttrainer-labs
(cd $L && uv run --no-project --with pyyaml --with jsonschema scripts/make_push.py labs/checkout-down-3am/lab.yaml)
uv run scripts/hand_game.py publish $L/out/checkout-down-3am/push.json   # runbook -> lab bucket
(cd $L && images/test-server.sh start)          # a game server from the image (~$0.03, dies after 45 min)
uv run scripts/hand_game.py start               # writes the game, prints 2 export lines: run them
P="python3 $HOME/IncTrainer/incidenttrainer/agent/dev/play.py"
$P state                                        # BOOTING until the server is up (~3 min), then READY
uv run scripts/hand_game.py status $ITR_GAME_ID  # what the gateway copied into DynamoDB
(cd $L && images/test-server.sh stop)           # when done
```

Grafana doesn't open through `/s/<id>/grafana/` on AWS yet: the agent must start Grafana with
the game's address at claim (agent step 10). Everything else is the real path.

## The broker (contract 5)

`src/itr_gateway/broker.py`, running inside gateway-auth every 5 s (and at once when the
boot screen polls a booting game):

| Game | What it does |
| --- | --- |
| `booting` | waits for the agent's `ready` (its `components` feed the boot screen); saves a control token on META (only one worker wins); claims the agent; reads `labs/<lab>/v<version>/runbook.json` from the lab bucket; pushes it; writes `agent_ready` and state `ready` |
| `ready` ... `watching` | every 10 s copies new agent events (`EV#agent#<seq>`) and `state`, `severity`, `reopened`, `events_synced_seq` onto META |
| idle 300 s | writes `heartbeat_lost`, asks the agent to end the game |
| agent says `ended` | copies the last events, **then** META `ended` (once), leaves `ACTIVE`, frees the server slot (`COUNTER/RUNNING`) |
| agent silent 60 s | writes `ended` (abandoned, last health) itself |

The gateway's own events have fixed numbers (`agent_ready` 1, `heartbeat_lost` 2, `ended` 3),
so writing one twice is harmless.

## Settings

See the top of `src/itr_gateway/__init__.py`. On a laptop: `ITR_DEV_GAMES` (a JSON file of
games) + `ITR_COOKIE_KEYS` instead of `ITR_TABLE`, and `ITR_COOKIE_INSECURE=1`.

## How a refusal reaches the browser

nginx's `auth_request` understands only 2xx, 401 and 403. So gateway-auth refuses with
`403` plus `X-Itr-Status` / `X-Itr-Code` / `X-Itr-Retry`, and nginx's `error_page` asks
`/deny` for the real answer (the contract's JSON error, or the boot progress).
